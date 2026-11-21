# -*- coding: utf-8 -*-
"""超额宣告与并行度证据一致性测试（重放架构）。

四类撕裂夹具 + 并发双开 + 七面互证，全部以临时库隔离运行：
  T1 ok 已超无标   T2 拒开水位偷加   T3 标记在无租约   T4 并发双加/漏标
运行：PYTHONPATH=backend python -m pytest backend/tests/test_capacity_replay.py -q
或纯库直跑：PYTHONPATH=backend python backend/tests/test_capacity_replay.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="cc_test_")
os.environ["CURTAINCLOCK_DB"] = str(Path(_tmp) / "test.db")

from app.db import connect, init_db, transaction  # noqa: E402
from app.routers import dashboard as dashboard_router  # noqa: E402
from app.routers import leases as leases_router  # noqa: E402
from app.routers import resources as resources_router  # noqa: E402
from app.routers import windows as windows_router  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.services import capacity_panel, lease as lease_svc  # noqa: E402


class CapacityReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        seed_if_empty()

    def _fresh_window(self, code: str, slot: str, parallelism: int,
                      start: int = 600, end: int = 660, executor: str = "E-PIPE-01",
                      day: str = "2026-11-21"):
        with transaction() as conn:
            conn.execute(
                "INSERT INTO windows(code,title,subject,slot_code,executor_code,day,"
                "start_min,end_min,parallelism,overage,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,'draft')",
                (code, "试窗", "ETL", slot, executor, day, start, end, parallelism),
            )

    def _open(self, code):
        with transaction() as conn:
            return lease_svc.open_lease(conn, code)

    def _close(self, no):
        with transaction() as conn:
            return lease_svc.close_lease(conn, no)

    def _wm(self, key):
        conn = connect()
        try:
            row = conn.execute(
                "SELECT value FROM watermarks WHERE key=?", (key,)
            ).fetchone()
            return None if row is None else int(row["value"])
        finally:
            conn.close()

    def _assert_all_surfaces_agree(self):
        """七面互证：总览/槽位/租约页/批窗页/水位表/回执口径/冲突计数。"""
        dash = dashboard_router.dashboard()
        slots = {s["code"]: s for s in resources_router.list_slots()}
        leases = leases_router.list_leases()
        wins = {w["code"]: w for w in windows_router.list_windows()}
        conn = connect()
        try:
            open_rows = conn.execute(
                "SELECT * FROM leases WHERE status='open'"
            ).fetchall()
            replay_open = len(open_rows)
            replay_over = sum(
                1 for r in open_rows
                if r["parallelism"] is not None
                and r["rated_concurrency"] is not None
                and r["parallelism"] > r["rated_concurrency"]
            )
            replay_pins = sum(1 for r in open_rows if r["overage"] == 1)
        finally:
            conn.close()

        # 1) 水位表镜像 == open 集合重放
        self.assertEqual(self._wm("open_leases"), replay_open)
        self.assertEqual(self._wm("overage_open"), replay_over)
        # 2) 总览占用条 == 重放，且自带一致性旗标
        self.assertEqual(dash["open_leases"], replay_open)
        self.assertEqual(dash["overage_open"], replay_over)
        self.assertTrue(dash["watermarks_consistent"])
        # 3) 钉扎旗可由快照重放（旗数 == 快照重算数）
        self.assertEqual(replay_pins, replay_over)
        # 4) 总览超额窗：只认 live+钉扎，且每个都能在 open 集合找到凭据
        self.assertEqual(dash["overage_windows"], replay_over)
        # 5) 槽位页逐槽计数之和 == 总在途
        self.assertEqual(sum(s["open_leases"] for s in slots.values()), replay_open)
        self.assertEqual(sum(s["overage_open"] for s in slots.values()), replay_over)
        # 6) 租约页：每条 open 行的旗 == 快照重放
        for r in leases:
            if r["status"] != "open":
                continue
            expect = (
                r["parallelism"] is not None
                and r["rated_concurrency"] is not None
                and r["parallelism"] > r["rated_concurrency"]
            )
            self.assertEqual(bool(r["overage"]), bool(expect), r)
        # 7) 批窗页：live 批窗旗必须有同窗 open 超额租约凭据
        for code, w in wins.items():
            if w["status"] == "live":
                self.assertEqual(
                    w["overage"],
                    1 if any(
                        r["window_code"] == code and r["status"] == "open" and r["overage"] == 1
                        for r in leases
                    ) else 0,
                )
            else:
                self.assertEqual(w["overage"], 0)
        return dash, replay_open, replay_over

    # ---- 基线：拒开臂不偷加、成功臂不伪标 ----------------------------------
    def test_01_reject_does_not_touch_watermark(self):
        before = self._wm("open_leases")
        result = self._open("W-OVR-01")  # 80 > 30
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "overage")
        # 拒开回执自带超额宣告，且与真实在途并存而不矛盾
        self.assertTrue(result["marked_over"])
        self.assertTrue(result["overage"])
        self.assertEqual(result["open_leases"], before)
        self.assertEqual(self._wm("open_leases"), before)
        self.assertEqual(self._wm("overage_open"), 0)
        conn = connect()
        try:
            n = conn.execute(
                "SELECT COUNT(*) c FROM leases WHERE window_code='W-OVR-01'"
            ).fetchone()["c"]
            w = conn.execute(
                "SELECT status, overage FROM windows WHERE code='W-OVR-01'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 0)          # 零 open 租约
        self.assertEqual(w["status"], "draft")
        self.assertEqual(w["overage"], 0)
        self._assert_all_surfaces_agree()

    def test_02_ok_receipt_matches_lease_row(self):
        self._fresh_window("W-OK-01", "S-ETL-01", 40)  # 40 <= 80
        result = self._open("W-OK-01")
        self.assertTrue(result["ok"])
        # ok 回执不得带超额标记（旧猴子补丁曾强塞 marked_over=True）
        self.assertFalse(result["marked_over"])
        self.assertFalse(result["overage"])
        conn = connect()
        try:
            row = conn.execute(
                "SELECT * FROM leases WHERE lease_no=?", (result["lease_no"],)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["overage"], 0)
        self.assertEqual(row["parallelism"], 40)
        self.assertEqual(row["rated_concurrency"], 80)
        self.assertEqual(result["open_leases"], self._wm("open_leases"))
        self._assert_all_surfaces_agree()
        self._close(result["lease_no"])
        self._assert_all_surfaces_agree()

    # ---- T1：ok 已超无标 → 对账补标且七面可见 ------------------------------
    def test_t1_ok_overaged_but_unpinned_gets_reconciled(self):
        self._fresh_window("W-T1", "S-BI-01", 40)  # 40 <= 40 先合法开窗
        result = self._open("W-T1")
        self.assertTrue(result["ok"])
        lease_no = result["lease_no"]
        # 模拟撕裂：额定被改到快照之下后手工制造“快照超额但旗为 0”的 open 行
        with transaction() as conn:
            conn.execute(
                "UPDATE leases SET parallelism=99, overage=0 WHERE lease_no=?",
                (lease_no,),
            )
            conn.execute(
                "UPDATE windows SET overage=0 WHERE code='W-T1'"
            )
        # 任一读入口触发对账即收口
        dash = dashboard_router.dashboard()
        conn = connect()
        try:
            row = conn.execute(
                "SELECT overage FROM leases WHERE lease_no=?", (lease_no,)
            ).fetchone()
            w = conn.execute(
                "SELECT overage FROM windows WHERE code='W-T1'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["overage"], 1, "ok 已超无标必须补标")
        self.assertEqual(w["overage"], 1)
        self.assertEqual(dash["overage_open"], 1)
        self.assertEqual(dash["overage_windows"], 1)
        self._assert_all_surfaces_agree()
        self._close(lease_no)

    # ---- T2：拒开水位偷加 → 对账压回 ---------------------------------------
    def test_t2_stolen_watermark_on_reject_is_rolled_back(self):
        real_open = self._wm("open_leases")
        with transaction() as conn:
            conn.execute(
                "INSERT INTO watermarks(key,value,updated_at) "
                "VALUES('open_leases', 99, '2026-01-01T00:00:00Z') "
                "ON CONFLICT(key) DO UPDATE SET value=99",
            )
            conn.execute(
                "INSERT INTO watermarks(key,value,updated_at) "
                "VALUES('overage_open', 7, '2026-01-01T00:00:00Z') "
                "ON CONFLICT(key) DO UPDATE SET value=7",
            )
        # 再走一次拒开路径，其内部对账必须把偷加的水位压回 open 集合真值
        result = self._open("W-OVR-01")
        self.assertFalse(result["ok"])
        self.assertEqual(result["open_leases"], real_open)
        self.assertEqual(self._wm("open_leases"), real_open)
        self.assertEqual(self._wm("overage_open"), 0)
        self._assert_all_surfaces_agree()

    # ---- T3：标记在无租约 → 摘旗，总览灯灭 ---------------------------------
    def test_t3_pin_without_open_lease_is_stripped(self):
        with transaction() as conn:
            conn.execute(
                "INSERT INTO windows(code,title,subject,slot_code,executor_code,day,"
                "start_min,end_min,parallelism,overage,status) "
                "VALUES('W-T3','试窗','ETL','S-BI-02','E-BI-02','2026-11-21',"
                "700,760,10,1,'live')"
            )
            # 水位也虚报
            conn.execute(
                "INSERT INTO watermarks(key,value,updated_at) "
                "VALUES('overage_open', 5, '2026-01-01T00:00:00Z') "
                "ON CONFLICT(key) DO UPDATE SET value=5",
            )
        dash = dashboard_router.dashboard()
        conn = connect()
        try:
            w = conn.execute(
                "SELECT overage, status FROM windows WHERE code='W-T3'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(w["overage"], 0, "无租约的超额标记必须摘除")
        self.assertEqual(dash["overage_windows"], 0)
        self.assertEqual(self._wm("overage_open"), 0)
        self._assert_all_surfaces_agree()

    # ---- T4：并发双开不得双加漏标（串行化事务 + 行重算）--------------------
    def test_t4_concurrent_open_double_count(self):
        # 两个不同批窗、不同资源，允许同时成功；计数必须精确等于 open 行数
        self._fresh_window("W-T4A", "S-ETL-01", 10, start=800, end=860,
                           executor="E-PIPE-01")
        self._fresh_window("W-T4B", "S-BI-01", 10, start=800, end=860,
                           executor="E-BI-01")
        results: list[dict] = []
        errors: list[Exception] = []

        def worker(code):
            try:
                results.append(self._open(code))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=worker, args=("W-T4A",))
        t2 = threading.Thread(target=worker, args=("W-T4B",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        self.assertEqual(errors, [])
        oks = [r for r in results if r.get("ok")]
        self.assertEqual(len(oks), 2)
        # 两个事务串行提交：回执计数是各自提交快照时刻的准确在途数，
        # 序列必须是 {real-1, real}——不双加（无 >real）、不漏记（最大值==real）。
        conn = connect()
        try:
            real = conn.execute(
                "SELECT COUNT(*) c FROM leases WHERE status='open'"
            ).fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(sorted(r["open_leases"] for r in oks), [real - 1, real])
        for r in oks:
            self.assertFalse(r["marked_over"])
        # 最终水位（所有提交完成后）必须精确等于 open 行数
        self.assertEqual(self._wm("open_leases"), real)
        self._assert_all_surfaces_agree()

        # 同窗并发重开：只允许一个成功，另一个拒，计数不得虚增
        again: list[dict] = []
        errs2: list[Exception] = []

        def reopen(code, sink):
            try:
                sink.append(self._open(code))
            except Exception as e:  # noqa: BLE001
                errs2.append(e)

        ta = threading.Thread(target=reopen, args=("W-T4A", again))
        tb = threading.Thread(target=reopen, args=("W-T4A", again))
        ta.start(); tb.start()
        ta.join(); tb.join()
        self.assertEqual(len(again) + len(errs2), 2)
        self.assertEqual(len([r for r in again if r.get("ok")]), 0)
        self.assertEqual(self._wm("open_leases"), real)
        self._assert_all_surfaces_agree()

    # ---- 旧缓存通道必须被清退，永不重建 -------------------------------------
    def test_t5_legacy_paint_cache_eradicated(self):
        with transaction() as conn:
            from app.services.settings_util import put_json
            put_json(conn, "capacity_paint", {"marked_over": True, "open_leases": 123})
        dashboard_router.dashboard()
        conn = connect()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='capacity_paint'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(row, "capacity_paint 缓存必须被清退且不重建")

    # ---- 关窗回落：占用与超额在途同时归零 ----------------------------------
    def test_t6_close_drains_both_counters(self):
        self._fresh_window("W-T6", "S-ETL-02", 20, start=900, end=960,
                           executor="E-PIPE-02")
        r = self._open("W-T6")
        self.assertTrue(r["ok"])
        closed = self._close(r["lease_no"])
        conn = connect()
        try:
            w = conn.execute(
                "SELECT status, overage FROM windows WHERE code='W-T6'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(w["status"], "done")
        self.assertEqual(w["overage"], 0)
        self.assertEqual(closed["overage_open"], 0)
        self.assertEqual(self._wm("overage_open"), 0)
        self._assert_all_surfaces_agree()


if __name__ == "__main__":
    unittest.main(verbosity=2)
