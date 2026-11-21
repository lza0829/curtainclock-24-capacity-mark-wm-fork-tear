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

同源保证：

| 面 | 来源 |
| --- | --- |
| 开窗裁决 / 批窗列表 / 就绪探测 | `services/ready.assess_admission` 同一函数 |
| 租约快照 | 开窗时从同一份裁决写入 |
| 超额记录 | `conflicts.kind='overage'`，成功路径绝不写 |
| 冲突计数 | 直接 `COUNT(conflicts)`，不另设水位 |
| 在途水位 | 由 open 租约行重算（`open_leases` / `overage_open`） |
| 总览超额窗 | 只认钉扎旗 `windows.overage=1 AND status='live'`，不重算比较，因此恒为 0 |
| 槽位页 | `capacity` 实时读取，与裁决一致 |

**改额定与开窗互斥**：算力槽仍有占用中租约时改额定并发返回 409；须先关窗。
改额定只影响未开窗批窗的后续裁决，已开租约保留开窗快照。**关窗同时回落**
占用与超额在途计数（两者都由租约行重算）。

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
