# Curtainclock（幕窗钟）

骨架：**批窗定义 → 算力槽/执行器资源依赖就绪 → 开窗租约占用 → 关窗释放与冲突水位互证**

- 前端：http://127.0.0.1:6100 （React + Vite）
- API：http://127.0.0.1:11100

## 超额口径：整笔拒绝（全局唯一）

批窗的**预估并行度**（`windows.parallelism`）与算力槽的**额定并发**
（`slots.capacity`，接口别名 `rated_concurrency`）比较：

- **预估并行度 > 额定并发**：开窗整笔失败（409）。同一事务只落一条
  `kind='overage'` 冲突；**零 open 租约**、批窗保持 `draft`、`open_leases`
  与 `overage_open` 水位不加、批窗与租约都不带超额钉扎旗。
- **预估并行度 ≤ 额定并发**：正常开窗。租约写入开窗瞬间的
  `(parallelism, rated_concurrency)` 快照与 `overage=0`。

同源保证（重放架构，全局唯一贯彻：超额事实听 open 租约集合）：

| 面 | 来源 |
| --- | --- |
| 开窗裁决 / 批窗列表 / 就绪探测 | `services/ready.assess_admission` 同一函数 |
| 租约快照 | 开窗时从同一份裁决写入；钉扎旗由本行快照 `parallelism > rated_concurrency` 重放决定（成功路径恒为 0） |
| 超额记录 | `conflicts.kind='overage'`，成功路径绝不写 |
| 冲突计数 | 直接 `COUNT(conflicts)`，不另设水位 |
| 在途水位 | `capacity_panel.reconcile_capacity` 由 open 租约行重算后回写 `watermarks`（仅镜像），API 永远以租约行为权威 |
| 总览超额窗 | 只认钉扎旗 `windows.overage=1 AND status='live'`；钉扎旗在读前对账中被 open 租约快照重放校正，因此恒为 0 且可机械复核 |
| 槽位页 / 租约页 / 批窗页 | 读前同一对账，标记列、在途数与 open 集合互证 |
| 回执 `marked_over` | 与租约钉扎旗同源：ok 回执恒 `false`；拒开回执 `true` 仅作超额事实宣告，不产生租约、不动水位 |

**读时对账自愈**（`reconcile_capacity`，幂等）：钉扎旗与快照不符则补标/摘旗，
批窗无超额 open 租约则摘旗，水位被偷加则压回 open 集合真值，并清退一切独立
快照缓存（旧 `capacity_paint` 通道已删除，总览带 `watermarks_consistent`
实测一致性旗标）。四类撕裂夹具（ok 已超无标、水位加回执拒、标记在无租约、
并发双加）在任一读入口或下一次开窗/拒开/关窗时必然收敛，禁止超额灯与并行度
水位长期分叉。并发安全由 `BEGIN IMMEDIATE` 串行化事务保证：双开不双加、
同窗重开只成一笔。

**改额定与开窗互斥**：算力槽仍有占用中租约时改额定并发返回 409；须先关窗。
改额定只影响未开窗批窗的后续裁决，已开租约保留开窗快照。**关窗同时回落**
占用与超额在途计数（两者都由租约行重算）。

测试：`PYTHONPATH=backend python backend/tests/test_capacity_replay.py`
（八例覆盖拒开不偷加、ok 回执同源、四类夹具自愈、并发双开、缓存清退、关窗回落）。

## 本地冒烟（推荐）

```powershell
cd D:\Asolo11\claude-workspace\curtainclock\source
# 本机若无 3.11：py -V:Astral/CPython3.11.15 -m venv .venv
py -V:Astral/CPython3.11.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "$PWD\backend"
Start-Process .\.venv\Scripts\uvicorn.exe -ArgumentList 'app.main:app','--host','127.0.0.1','--port','11100' -WorkingDirectory "$PWD\backend"
cd frontend
# 前端需 Node ≥18；不足时用 docker compose 起 web
npm install
npm run dev
```

浏览器打开 http://127.0.0.1:6100 ：开一节课 → 同算力槽冲突 → 关窗释放。
`W-OVR-01` 是超额演示窗（预估 80 > 额定 30），点“开窗占用”会得到整笔拒绝。

API 冒烟：`python _smoke_api.py`（需 API 已在 11100 运行）。

## Docker

```powershell
docker compose up -d --build
```
