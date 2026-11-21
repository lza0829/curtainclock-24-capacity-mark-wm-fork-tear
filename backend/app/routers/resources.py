from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect, transaction
from app.services.ready import OVERAGE_MODE

router = APIRouter(tags=["resources"])


class CapacityBody(BaseModel):
    # 额定并发即 slots.capacity，是开窗裁决与租约快照的唯一来源。
    capacity: int = Field(ge=1)


@router.get("/slots")
def list_slots():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, capacity, active FROM slots ORDER BY code"
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["rated_concurrency"] = item["capacity"]
            open_row = conn.execute(
                "SELECT COUNT(*) c FROM leases WHERE slot_code=? AND status='open'",
                (r["code"],),
            ).fetchone()
            item["open_leases"] = int(open_row["c"])
            pinned_row = conn.execute(
                "SELECT COUNT(*) c FROM leases "
                "WHERE slot_code=? AND status='open' AND overage=1",
                (r["code"],),
            ).fetchone()
            item["overage_open"] = int(pinned_row["c"])
            item["overage_mode"] = OVERAGE_MODE
            out.append(item)
        return out
    finally:
        conn.close()


@router.post("/slots/{code}/capacity")
def set_capacity(code: str, body: CapacityBody):
    """改额定并发。与开窗互斥：该算力槽仍有占用中租约时拒绝，避免半截成功。"""
    try:
        with transaction() as conn:
            slot = conn.execute(
                "SELECT * FROM slots WHERE code=?", (code,)
            ).fetchone()
            if slot is None:
                raise HTTPException(404, "算力槽不存在")
            busy = conn.execute(
                "SELECT COUNT(*) c FROM leases WHERE slot_code=? AND status='open'",
                (code,),
            ).fetchone()["c"]
            if busy > 0:
                raise HTTPException(
                    409, "该算力槽仍有占用中租约，须先关窗再改额定并发"
                )
            conn.execute(
                "UPDATE slots SET capacity=? WHERE code=?", (body.capacity, code)
            )
            # 改额定只影响未开窗批窗的裁决；已开租约保留开窗快照。
            return {
                "ok": True,
                "code": code,
                "capacity": body.capacity,
                "rated_concurrency": body.capacity,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/executors")
def list_executors():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, subject, active FROM executors ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
