# -*- coding: utf-8 -*-
"""经真实 HTTP 通道注入四类撕裂夹具，验证重放架构的读时自愈与七面互证。

前置：API 已在 127.0.0.1:11100 运行，使用独立冒烟库 /tmp/cc_smoke/smoke.db。
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request

DB = "/tmp/cc_smoke/smoke.db"
BASE = "http://127.0.0.1:11100/api"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", 0), str(e)


def db_exec(sql: str, params=()):
    conn = sqlite3.connect(DB, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def db_query(sql: str, params=()):
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def assert_eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"  ok {name} = {got}")


# 先合法开一扇新窗，作为 T1 的载体（种子窗可能已被冒烟脚本关到 done）
st, cr = call("POST", "/windows", {
    "code": "W-TEAR-01", "title": "撕裂载体窗", "subject": "ETL",
    "slot_code": "S-ETL-01", "executor_code": "E-PIPE-01",
    "day": "2026-11-22", "start_min": 600, "end_min": 660, "parallelism": 40,
})
assert st == 200, cr
st, r = call("POST", "/leases/open", {"window_code": "W-TEAR-01"})
assert st == 200, r
lease_no = r["lease_no"]
print("fixture lease:", lease_no)

# ---- T1 ok 已超无标 -------------------------------------------------------
print("T1 ok 已超无标：篡改 open 租约快照超额但钉扎旗=0")
db_exec("UPDATE leases SET parallelism=99, overage=0 WHERE lease_no=?", (lease_no,))
db_exec("UPDATE windows SET overage=0 WHERE code='W-DIM-01'")
st, dash = call("GET", "/dashboard")
assert_eq("T1 总览超额在途补标", dash["overage_open"], 1)
assert_eq("T1 总览超额窗补标", dash["overage_windows"], 1)
assert_eq("T1 一致性旗标", dash["watermarks_consistent"], True)
row = db_query("SELECT overage FROM leases WHERE lease_no=?", (lease_no,))[0]
assert_eq("T1 租约行补钉扎旗", row["overage"], 1)
st, leases = call("GET", "/leases")
pin = [x for x in leases if x["lease_no"] == lease_no][0]
assert_eq("T1 租约页标记列", pin["overage"], 1)

# ---- T2 拒开水位偷加 ------------------------------------------------------
print("T2 拒开水位偷加：水位虚报 99/7，再走一次拒开")
db_exec("INSERT INTO watermarks(key,value,updated_at) VALUES('open_leases',99,'x') "
        "ON CONFLICT(key) DO UPDATE SET value=99")
db_exec("INSERT INTO watermarks(key,value,updated_at) VALUES('overage_open',7,'x') "
        "ON CONFLICT(key) DO UPDATE SET value=7")
st, _ = call("POST", "/leases/open", {"window_code": "W-OVR-01"})
assert st == 409
st, dash = call("GET", "/dashboard")
real_open = len(db_query("SELECT 1 FROM leases WHERE status='open'"))
assert_eq("T2 总览在途压回真值", dash["open_leases"], real_open)
assert_eq("T2 总览超额在途压回真值", dash["overage_open"], 1)
wm = {r["key"]: r["value"] for r in db_query("SELECT key,value FROM watermarks")}
assert_eq("T2 水位表 open_leases 压回", wm["open_leases"], real_open)
assert_eq("T2 水位表 overage_open 压回", wm["overage_open"], 1)

# ---- T3 标记在无租约 ------------------------------------------------------
print("T3 标记在无租约：插一条 live+钉扎但无租约的批窗")
db_exec(
    "INSERT INTO windows(code,title,subject,slot_code,executor_code,day,start_min,end_min,"
    "parallelism,overage,status) VALUES('W-GHOST','幽灵窗','BI','S-BI-02','E-BI-02',"
    "'2026-11-21',700,760,10,1,'live')"
)
st, dash = call("GET", "/dashboard")
ghost = db_query("SELECT overage FROM windows WHERE code='W-GHOST'")[0]
assert_eq("T3 幽灵批窗摘旗", ghost["overage"], 0)
st, wins = call("GET", "/windows")
g = [w for w in wins if w["code"] == "W-GHOST"][0]
assert_eq("T3 批窗页无钉扎", g["overage"], 0)

# ---- T4 并发双加：直接查最终收敛 ------------------------------------------
print("T4 并发双加：水位再次虚报，读总览后必须精确等于 open 行数")
db_exec("INSERT INTO watermarks(key,value,updated_at) VALUES('open_leases',12345,'x') "
        "ON CONFLICT(key) DO UPDATE SET value=12345")
st, dash = call("GET", "/dashboard")
real_open = len(db_query("SELECT 1 FROM leases WHERE status='open'"))
assert_eq("T4 总览在途==open 行数", dash["open_leases"], real_open)
st, slots = call("GET", "/slots")
assert_eq("T4 槽位页在途之和==总在途",
          sum(s["open_leases"] for s in slots), real_open)
assert_eq("T4 槽位页超额之和==超额在途",
          sum(s["overage_open"] for s in slots), dash["overage_open"])

# ---- 旧缓存通道不复存在 ----------------------------------------------------
print("T5 旧 capacity_paint 缓存清退")
assert not db_query("SELECT 1 FROM settings WHERE key='capacity_paint'"), "缓存残留"
assert "marked_over" not in dash, "总览不得再出现伪造 marked_over 灯"
print("  ok 总览无伪造超额灯字段")

# 收尾关窗
st, c = call("POST", "/leases/close", {"lease_no": lease_no})
assert st == 200, c
assert_eq("关窗后在途归零", c["open_leases"], 0)
assert_eq("关窗后超额在途归零", c["overage_open"], 0)
print("ALL FIXTURE HEALING CHECKS PASSED")
