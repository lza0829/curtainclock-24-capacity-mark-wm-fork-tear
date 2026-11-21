from __future__ import annotations

from datetime import datetime, timezone

from app.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_if_empty() -> None:
    init_db()
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM slots").fetchone()["c"]
        if n > 0:
            return
        # slots 表 = 算力槽；executors 表 = 执行器（库表名兼容底座，界面/题面用语为数仓皮）
        # capacity 即额定并发：开窗裁决与租约（预估,额定）快照的唯一来源。
        slots = [
            ("S-ETL-01", "ETL 主槽 A", 80),
            ("S-ETL-02", "ETL 主槽 B", 30),
            ("S-BI-01", "报表槽甲", 40),
            ("S-BI-02", "报表槽乙", 40),
        ]
        for code, name, cap in slots:
            conn.execute(
                "INSERT INTO slots(code, name, capacity, active) VALUES(?,?,?,1)",
                (code, name, cap),
            )
        executors = [
            ("E-PIPE-01", "管道执行器甲", "ETL"),
            ("E-PIPE-02", "管道执行器乙", "ETL"),
            ("E-BI-01", "报表执行器甲", "BI"),
            ("E-BI-02", "报表执行器乙", "BI"),
        ]
        for code, name, subject in executors:
            conn.execute(
                "INSERT INTO executors(code, name, subject, active) VALUES(?,?,?,1)",
                (code, name, subject),
            )
        day = "2026-11-20"
        # (code, title, subject, slot, executor, day, start, end, parallelism)
        # W-OVR-01 演示超额整笔拒绝：预估 80 > S-ETL-02 额定 30。
        windows = [
            ("W-DIM-01", "维表刷新窗", "ETL", "S-ETL-01", "E-PIPE-01", day, 9 * 60, 10 * 60 + 30, 40),
            ("W-FACT-01", "事实入湖窗", "ETL", "S-ETL-02", "E-PIPE-02", day, 9 * 60, 10 * 60 + 30, 20),
            ("W-RPT-01", "日报物化窗", "BI", "S-BI-01", "E-BI-01", day, 13 * 60, 14 * 60 + 30, 40),
            ("W-RPT-02", "看板刷新窗", "BI", "S-BI-02", "E-BI-02", day, 13 * 60, 14 * 60 + 30, 40),
            ("W-OVR-01", "超额演示窗", "ETL", "S-ETL-02", "E-PIPE-02", day, 16 * 60, 17 * 60, 80),
        ]
        for row in windows:
            conn.execute(
                "INSERT INTO windows(code, title, subject, slot_code, executor_code, day, start_min, end_min, "
                "parallelism, overage, status) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,'draft')",
                row,
            )
        # 在途水位由 open 租约行重算；冲突计数直接数 conflicts 表。
        # 这里仍预置这两项，便于旧库/新库读同一张 watermarks 表时字段齐全。
        for key in ("open_leases", "overage_open"):
            conn.execute(
                "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?)",
                (key, 0, _now()),
            )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('conflict_mode', 'reject')",
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('fixture_day', ?)",
            (day,),
        )
        conn.commit()
    finally:
        conn.close()
