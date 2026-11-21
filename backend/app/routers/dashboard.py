from __future__ import annotations
from app.services import capacity_panel

from fastapi import APIRouter

from app.db import connect
from app.services.ready import OVERAGE_MODE

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day_row = conn.execute(
            "SELECT value FROM settings WHERE key='fixture_day'"
        ).fetchone()
        day = day_row["value"] if day_row else None
        # 在途租约与冲突计数都直接由权威表计数，不读可能漂移的独立水位；
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
        # 总览超额窗只认钉扎旗（windows.overage=1 且占用中），绝不重算
        # parallelism > capacity。整笔拒绝架构下恒为 0；缺钉扎的成功不体现在此。
        overage_windows = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live' AND overage=1"
        ).fetchone()["c"]
        overage_open = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open' AND overage=1"
        ).fetchone()["c"]
        base = {
            "fixture_day": day,
            "open_leases": int(open_lease_n),
            "conflict_count": int(conflict_count),
            "overage_conflicts": int(overage_conflicts),
            "live_sessions": live_n,
            "draft_sessions": draft_n,
            "overage_windows": overage_windows,
            "overage_open": overage_open,
            "overage_mode": OVERAGE_MODE,
        }
        return capacity_panel.overlay_dashboard(conn, base)
    finally:
        conn.close()
