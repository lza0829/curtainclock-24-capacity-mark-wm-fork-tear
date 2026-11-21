# -*- coding: utf-8 -*-
"""0-1 API smoke against running server on 11100.

超额架构（全局唯一）：整笔拒绝。预估并行度 > 额定并发时开窗整笔失败，
写超额冲突、零 open、水位不加；成功路径必有（预估,额定）快照且无钉扎旗。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


def main() -> None:
    st, dash = call("GET", "/dashboard")
    assert st == 200, dash
    assert dash["overage_mode"] == "reject", dash
    assert dash["overage_windows"] == 0, dash
    print("dashboard", dash)

    # 拒绝臂：预估 80 > S-ETL-02 额定 30，整笔拒绝
    _, before = call("GET", "/dashboard")
    st, over = call("POST", "/leases/open", {"window_code": "W-OVR-01"})
    assert st == 409, over
    _, after = call("GET", "/dashboard")
    assert after["open_leases"] == before["open_leases"], (before, after)
    assert after["conflict_count"] == before["conflict_count"] + 1, (before, after)
    assert after["overage_windows"] == 0 and after["overage_open"] == 0, after
    print("overage rejected", over)

    # 成功臂：并行度 20 <= 额定 30
    st, hit = call("POST", "/leases/open", {"window_code": "W-FACT-01"})
    assert st == 200, hit
    assert hit["parallelism"] == 20 and hit["rated_concurrency"] == 30, hit
    assert hit["overage"] is False, hit
    print("open ok", hit)

    # 改额定与开窗互斥
    st, blocked = call("POST", "/slots/S-ETL-02/capacity", {"capacity": 10})
    assert st == 409, blocked
    print("capacity blocked while open", blocked)

    st, closed = call("POST", "/leases/close", {"lease_no": hit["lease_no"]})
    assert st == 200, closed
    assert closed["open_leases"] == before["open_leases"], closed
    assert closed["overage_open"] == 0, closed
    print("closed", closed)

    # 冲突底座仍生效：同算力槽时段重叠
    st, created = call(
        "POST",
        "/windows",
        {
            "code": "W-DIM-DUP",
            "title": "冲突试窗",
            "subject": "ETL",
            "slot_code": "S-ETL-01",
            "executor_code": "E-BI-01",
            "day": "2026-11-20",
            "start_min": 540,
            "end_min": 630,
            "parallelism": 10,
        },
    )
    assert st == 200, created
    st, open1 = call("POST", "/leases/open", {"window_code": "W-DIM-01"})
    assert st == 200, open1
    st, conflict = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    assert st == 409, conflict
    print("conflict", conflict)

    st, closed = call("POST", "/leases/close", {"lease_no": open1["lease_no"]})
    assert st == 200, closed
    print("closed", closed)
    print("SMOKE OK")


if __name__ == "__main__":
    main()
