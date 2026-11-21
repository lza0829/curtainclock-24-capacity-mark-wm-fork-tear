from __future__ import annotations
from app.services import capacity_panel

import sqlite3
from datetime import datetime, timezone

from app.services.ready import (
    assess_admission,
    overage_reason,
    readiness,
    resource_busy,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_watermark(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, int(value), _now()),
    )


def _open_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """占用/超额在途计数一律由租约行现算，杜绝计数与租约各说各话。

    open_leases  = 当前 open 租约数
    overage_open = 其中带钉扎超额旗的 open 租约数
    整笔拒绝架构下后者恒为 0；此处按旗统计而非按 parallelism>rated 重算，
    与总览“只认钉扎旗”同一口径。算完顺手回写 watermarks，便于直接查表核对，
    但 API 永远以租约行为权威。
    """
    open_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open'"
    ).fetchone()["c"]
    overage_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open' AND overage=1"
    ).fetchone()["c"]
    _persist_watermark(conn, "open_leases", open_n)
    _persist_watermark(conn, "overage_open", overage_n)
    return int(open_n), int(overage_n)


def _conflict_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='conflict_mode'"
    ).fetchone()
    return row["value"] if row else "reject"


def _record_conflict(
    conn: sqlite3.Connection,
    *,
    kind: str,
    window_code: str,
    resource_code: str,
    window: sqlite3.Row,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO conflicts(kind, window_code, resource_code, day, start_min, end_min, reason, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            kind,
            window_code,
            resource_code,
            window["day"],
            window["start_min"],
            window["end_min"],
            reason,
            _now(),
        ),
    )
    # conflict_count 由 conflicts 表直接 COUNT 得出（见 dashboard），此处不再维护
    # 独立水位，避免“记录说一条、水位说另一条”。


def open_lease(conn: sqlite3.Connection, window_code: str) -> dict:
    session = conn.execute(
        "SELECT * FROM windows WHERE code=?", (window_code,)
    ).fetchone()
    if session is None:
        raise ValueError(f"批窗不存在：{window_code}")
    if session["status"] == "live":
        raise ValueError("批窗已在占用中，勿重复开窗")
    if session["status"] == "done":
        raise ValueError("批窗已结束")

    # 整笔拒绝臂：超额（预估并行度 > 额定并发）先于一切占用判断裁决。
    # 全事务失败：不开租约、不改批窗状态、不动在途/超额计数；
    # 只落一条超额冲突，作为“拒绝留下痕迹”的唯一凭据。
    assessment = assess_admission(conn, session)
    if assessment["overage"]:
        reason = overage_reason(session, assessment)
        _record_conflict(
            conn,
            kind="overage",
            window_code=window_code,
            resource_code=session["slot_code"],
            window=session,
            reason=reason,
        )
        return {
            "ok": False,
            "kind": "overage",
            "error": reason,
            "conflict_mode": _conflict_mode(conn),
            "parallelism": assessment["parallelism"],
            "rated_concurrency": assessment["rated_concurrency"],
            "overage_mode": assessment["overage_mode"],
        }

    ready = readiness(conn, session)
    if not ready["ready"]:
        mode = _conflict_mode(conn)
        for reason in ready["reasons"]:
            kind = "room" if "算力槽" in reason else ("teacher" if "执行器" in reason else "ready")
            resource = (
                session["slot_code"]
                if kind == "room"
                else (session["executor_code"] if kind == "teacher" else window_code)
            )
            _record_conflict(
                conn,
                kind=kind,
                window_code=window_code,
                resource_code=resource,
                window=session,
                reason=reason,
            )
        # 底座贯彻 reject（冲突落库后由调用方提交）；候补算力槽留给后续题
        return {
            "ok": False,
            "kind": "not_ready",
            "error": "；".join(ready["reasons"]),
            "conflict_mode": mode,
        }

    if resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ) or resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ):
        raise ValueError("资源刚被占用，开窗失败")

    n = conn.execute("SELECT COUNT(*) c FROM leases").fetchone()["c"] + 1
    lease_no = f"L-{session['day'].replace('-', '')}-{n:04d}"
    conn.execute(
        "INSERT INTO leases(lease_no, window_code, slot_code, executor_code, day, start_min, end_min, "
        "parallelism, rated_concurrency, overage, status, opened_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            lease_no,
            window_code,
            session["slot_code"],
            session["executor_code"],
            session["day"],
            session["start_min"],
            session["end_min"],
            assessment["parallelism"],
            assessment["rated_concurrency"],
            0,  # 整笔拒绝架构下，成功租约绝不带超额钉扎旗
            "open",
            _now(),
        ),
    )
    conn.execute(
        "UPDATE windows SET status='live', overage=0 WHERE code=?", (window_code,)
    )
    open_n, _ = _open_counts(conn)
    return {
        "ok": True,
        "lease_no": lease_no,
        "window_code": window_code,
        "status": "open",
        "parallelism": assessment["parallelism"],
        "rated_concurrency": assessment["rated_concurrency"],
        "overage": False,
        "open_leases": open_n,
    }


def close_lease(conn: sqlite3.Connection, lease_no: str) -> dict:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    if lease["status"] != "open":
        raise ValueError("租约不在占用态")
    conn.execute(
        "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=?",
        (_now(), lease_no),
    )
    conn.execute(
        "UPDATE windows SET status='done', overage=0 WHERE code=?",
        (lease["window_code"],),
    )
    # 关窗同时回落占用与超额计数：两者都由租约行现算，钉扎超额关窗后一并归零。
    open_n, overage_open = _open_counts(conn)
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": open_n,
        "overage_open": overage_open,
    }


_orig_open_lease_cap = open_lease


def open_lease(conn, window_code):
    result = _orig_open_lease_cap(conn, window_code)
    if result.get("ok"):
        painted = capacity_panel.paint_capacity(conn, window_code, True)
        result = dict(result)
        result["marked_over"] = True
        result["open_leases"] = painted["open_leases"]
    return result
