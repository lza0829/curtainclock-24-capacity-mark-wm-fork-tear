from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect, transaction
from app.services.ready import assess_admission, readiness

router = APIRouter(tags=["windows"])


class SessionIn(BaseModel):
    code: str = Field(min_length=2)
    title: str
    subject: str
    slot_code: str
    executor_code: str
    day: str
    start_min: int
    end_min: int
    # 预估并行度：开窗时与算力槽额定并发比较，是超额裁决的唯一输入。
    parallelism: int = Field(default=1, ge=1)


def _window_payload(conn, row) -> dict:
    item = dict(row)
    item["admission"] = assess_admission(conn, row)
    item["readiness"] = readiness(conn, row)
    return item


@router.get("/windows")
def list_windows():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM windows ORDER BY day, start_min, code"
        ).fetchall()
        return [_window_payload(conn, r) for r in rows]
    finally:
        conn.close()


@router.post("/windows")
def create_session(body: SessionIn):
    if body.end_min <= body.start_min:
        raise HTTPException(400, "结束时刻必须晚于开始时刻")
    try:
        with transaction() as conn:
            slot = conn.execute(
                "SELECT code FROM slots WHERE code=?", (body.slot_code,)
            ).fetchone()
            if slot is None:
                raise HTTPException(400, "算力槽不存在")
            conn.execute(
                "INSERT INTO windows(code, title, subject, slot_code, executor_code, day, start_min, end_min, "
                "parallelism, overage, status) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,'draft')",
                (
                    body.code,
                    body.title,
                    body.subject,
                    body.slot_code,
                    body.executor_code,
                    body.day,
                    body.start_min,
                    body.end_min,
                    body.parallelism,
                ),
            )
            row = conn.execute(
                "SELECT * FROM windows WHERE code=?", (body.code,)
            ).fetchone()
            return _window_payload(conn, row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/windows/{code}/ready")
def session_ready(code: str):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM windows WHERE code=?", (code,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "批窗不存在")
        return {
            **readiness(conn, row),
            "admission": assess_admission(conn, row),
        }
    finally:
        conn.close()
