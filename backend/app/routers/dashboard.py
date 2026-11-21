from __future__ import annotations

from fastapi import APIRouter

from app.db import connect, transaction
from app.services import capacity_panel
from app.services.ready import OVERAGE_MODE

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    # 读前先对账：钉扎旗、批窗旗、水位全部向 open 租约重放结果收敛，
    # 即使撕裂来自开窗路径之外，也不允许超额灯与并行度水位长期分叉。
    with transaction() as conn:
        panel = capacity_panel.reconcile_capacity(conn)

    conn = connect()
    try:
        day_row = conn.execute(
            "SELECT value FROM settings WHERE key='fixture_day'"
        ).fetchone()
        day = day_row["value"] if day_row else None
        # 在途租约与冲突计数都直接由权威表计数，不读任何独立缓存；
        # 与租约页、批窗页、槽位页同源。
        open_lease_n = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open'"
        ).fetchone()["c"]
        conflict_count = conn.execute(
            "SELECT COUNT(*) c FROM conflicts"
        ).fetchone()["c"]
        overage_conflicts = conn.execute(
            "SELECT COUNT(*) c FROM conflicts WHERE kind='overage'"
        ).fetchone()["c"]
        live_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live'"
        ).fetchone()["c"]
        draft_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='draft'"
        ).fetchone()["c"]
        # 总览超额窗只认钉扎旗（windows.overage=1 且占用中）；钉扎旗本身已在
        # 对账中被 open 租约快照重放校正，因此这里的数字可由 open 集合机械复核。
        overage_windows = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live' AND overage=1"
        ).fetchone()["c"]
        overage_open = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open' AND overage=1"
        ).fetchone()["c"]
        # 水位表只是镜像：与权威表计数比对，给出可核的一致性旗标。
        wm_open = conn.execute(
            "SELECT value FROM watermarks WHERE key='open_leases'"
        ).fetchone()
        wm_over = conn.execute(
            "SELECT value FROM watermarks WHERE key='overage_open'"
        ).fetchone()
        watermarks_consistent = (
            wm_open is not None
            and wm_over is not None
            and int(wm_open["value"]) == int(open_lease_n)
            and int(wm_over["value"]) == int(overage_open)
        )
        return {
            "fixture_day": day,
            "open_leases": int(open_lease_n),
            "conflict_count": int(conflict_count),
            "overage_conflicts": int(overage_conflicts),
            "live_sessions": live_n,
            "draft_sessions": draft_n,
            "overage_windows": int(overage_windows),
            "overage_open": int(overage_open),
            "overage_mode": OVERAGE_MODE,
            "watermarks_consistent": watermarks_consistent,
            "pinned_overage_leases": panel["pinned_overage_leases"],
        }
    finally:
        conn.close()
