from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "幕窗钟"

# 默认落在仓库 data/ 下；测试或本地隔离运行时可用 CURTAINCLOCK_DB 指向临时库。
DB_PATH = Path(
    os.environ.get(
        "CURTAINCLOCK_DB",
        str(Path(__file__).resolve().parents[2] / "data" / "curtainclock.db"),
    )
)
