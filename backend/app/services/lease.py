from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services import capacity_panel
from app.services.ready import (
    assess_admission,
    overage_reason,
    readiness,
    resource_busy,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    # conflict_count 由 conflicts 表直接 COUNT 得出（见 dashboard），不维护独立水位，
    # 避免“记录说一条、水位说另一条”。


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

    # 重放架构（全局唯一）：超额（预估并行度 > 额定并发）先于一切占用判断裁决。
    # 拒开臂不产生任何租约行、不改批窗状态、不动水位；只落一条超额冲突。
    # 拒开后同样跑一次对账：水位若被别处偷加，立刻由 open 集合重算压回。
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
        panel = capacity_panel.reconcile_capacity(conn)
        return {
            "ok": False,
            "kind": "overage",
            "error": reason,
            "conflict_mode": _conflict_mode(conn),
            "parallelism": assessment["parallelism"],
            "rated_concurrency": assessment["rated_concurrency"],
            "overage": True,
            "marked_over": True,
            "overage_mode": assessment["overage_mode"],
            "open_leases": panel["open_leases"],
            "overage_open": panel["overage_open"],
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
        # 普通未就绪拒开同样不得偷加水位：对账保证水位只认 open 集合。
        panel = capacity_panel.reconcile_capacity(conn)
        return {
            "ok": False,
            "kind": "not_ready",
            "error": "；".join(ready["reasons"]),
            "conflict_mode": mode,
            "overage": False,
            "marked_over": False,
            "open_leases": panel["open_leases"],
            "overage_open": panel["overage_open"],
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
    # 钉扎旗由本行开窗快照重放决定，而不是写死 0：标记必须能由 open 集合重算。
    # 整笔拒绝架构下能走到这里必有 parallelism <= rated，故实际恒为 0。
    pinned = 1 if assessment["parallelism"] > assessment["rated_concurrency"] else 0
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
            pinned,
            "open",
            _now(),
        ),
    )
    conn.execute(
        "UPDATE windows SET status='live', overage=? WHERE code=?",
        (pinned, window_code),
    )
    # 开窗后统一对账：水位、钉扎旗、总览全部由 open 租约集合重放收敛。
    panel = capacity_panel.reconcile_capacity(conn)
    return {
        "ok": True,
        "lease_no": lease_no,
        "window_code": window_code,
        "status": "open",
        "parallelism": assessment["parallelism"],
        "rated_concurrency": assessment["rated_concurrency"],
        # 回执超额标记 = 租约行钉扎旗 = 快照重放结果，三处同源，禁止再分叉。
        "overage": bool(pinned),
        "marked_over": bool(pinned),
        "open_leases": panel["open_leases"],
        "overage_open": panel["overage_open"],
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
    # 关窗同时回落占用与超额计数：两者都由租约行重算，钉扎超额关窗后一并归零。
    panel = capacity_panel.reconcile_capacity(conn)
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": panel["open_leases"],
        "overage_open": panel["overage_open"],
    }
