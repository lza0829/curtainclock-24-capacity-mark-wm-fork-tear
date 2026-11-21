from __future__ import annotations

import sqlite3

# 超额架构（全局唯一贯彻）：整笔拒绝。
# 预估并行度 > 算力槽额定并发时，开窗整笔失败：写超额冲突、零 open 租约、
# open_leases 水位不加、批窗与租约都不落 overage 钉扎旗。
# 因此任一成功路径都满足 parallelism <= rated_concurrency；
# 总览的“超额窗”永远为 0（只认钉扎旗，而不是重算比较）。
OVERAGE_MODE = "reject"

ADMISSION_OK = "ok"
ADMISSION_OVERAGE = "overage"


def room_active(conn: sqlite3.Connection, slot_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM slots WHERE code=?", (slot_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def teacher_active(conn: sqlite3.Connection, executor_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM executors WHERE code=?", (executor_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def slot_rated_concurrency(
    conn: sqlite3.Connection, slot_code: str
) -> int | None:
    """算力槽额定并发（slots.capacity），开窗裁决与租约快照的唯一来源。"""
    row = conn.execute(
        "SELECT capacity FROM slots WHERE code=?", (slot_code,)
    ).fetchone()
    return int(row["capacity"]) if row else None


def assess_admission(conn: sqlite3.Connection, window: sqlite3.Row) -> dict:
    """开窗裁决的同源投影：批窗列表、就绪探测、开窗动作共用这一份口径。

    parallelism 取自批窗（预估并行度），rated_concurrency 实时取自算力槽，
    这样“改额定后行为随臂”：已开租约保留开窗快照，未开批窗按新额定重新裁决。
    """
    parallelism = int(window["parallelism"] or 1)
    rated = slot_rated_concurrency(conn, window["slot_code"])
    overage = rated is not None and parallelism > rated
    return {
        "parallelism": parallelism,
        "rated_concurrency": rated,
        "overage": overage,
        "admission": ADMISSION_OVERAGE if overage else ADMISSION_OK,
        "overage_mode": OVERAGE_MODE,
    }


def overage_reason(window: sqlite3.Row, assessment: dict) -> str:
    return (
        f"预估并行度 {assessment['parallelism']} 超过算力槽 "
        f"{window['slot_code']} 额定并发 {assessment['rated_concurrency']}，"
        "超额开窗整笔拒绝"
    )


def overlap(
    a_start: int, a_end: int, b_start: int, b_end: int
) -> bool:
    return a_start < b_end and b_start < a_end


def resource_busy(
    conn: sqlite3.Connection,
    *,
    kind: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    exclude_session: str | None = None,
) -> sqlite3.Row | None:
    col = "slot_code" if kind == "room" else "executor_code"
    rows = conn.execute(
        f"SELECT * FROM leases WHERE {col}=? AND day=? AND status='open'",
        (resource_code, day),
    ).fetchall()
    for r in rows:
        if exclude_session and r["window_code"] == exclude_session:
            continue
        if overlap(start_min, end_min, r["start_min"], r["end_min"]):
            return r
    return None


def readiness(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    reasons: list[str] = []
    assessment = assess_admission(conn, session)
    if assessment["overage"]:
        # 超额在拒绝臂下是“未就绪”的一种：批窗列表、就绪探测、开窗动作同源。
        reasons.append(overage_reason(session, assessment))
    if not room_active(conn, session["slot_code"]):
        reasons.append("算力槽未启用")
    if not teacher_active(conn, session["executor_code"]):
        reasons.append("执行器未启用")
    busy_room = resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy_room:
        reasons.append(f"算力槽已被租约 {busy_room['lease_no']} 占用")
    busy_teacher = resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy_teacher:
        reasons.append(f"执行器已被租约 {busy_teacher['lease_no']} 占用")
    return {"ready": len(reasons) == 0, "reasons": reasons}
