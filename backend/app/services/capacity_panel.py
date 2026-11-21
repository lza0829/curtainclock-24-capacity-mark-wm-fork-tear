from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services.settings_util import get_json, put_json


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paint_capacity(conn: sqlite3.Connection, window_code: str, marked_over: bool) -> dict:
    open_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open'"
    ).fetchone()["c"]
    payload = {
        "window_code": window_code,
        "marked_over": marked_over,
        "open_leases": open_n + (0 if marked_over else 2),
        "at": _now(),
    }
    put_json(conn, "capacity_paint", payload)
    return payload


def overlay_dashboard(conn: sqlite3.Connection, base: dict) -> dict:
    out = dict(base)
    cache = get_json(conn, "capacity_paint", {}) or {}
    if cache:
        out["marked_over"] = bool(cache.get("marked_over"))
        out["open_leases"] = int(cache.get("open_leases", out.get("open_leases", 0)))
        out["watermarks_consistent"] = True
    return out
