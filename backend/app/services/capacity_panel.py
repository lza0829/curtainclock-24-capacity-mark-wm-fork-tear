"""容量面板：唯一的「超额事实」重放与对账中枢。

全局架构（唯一贯彻）：以 open 租约并行度重放为准（replay）。
- 权威源只有 leases 表的 open 行；watermarks 表仅作可查表镜像，从不被信任为事实。
- 任一 open 租约行的 overage 钉扎旗，必须能由其开窗快照
  (parallelism, rated_concurrency) 重算得到：parallelism > rated_concurrency。
  旗与重算不一致即为撕裂，reconcile_capacity 就地收口（补标/摘旗）。
- 在途水位 open_leases / overage_open 一律现算后回写，拒绝任何独立计数通道；
  没有「缓存快照覆盖实时计数」这条路，旧的 capacity_paint 缓存一律清退。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_watermark(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, int(value), _now()),
    )


def lease_pins_overage(lease: sqlite3.Row) -> bool:
    """由开窗快照重放单条租约是否超额钉扎。

    快照缺失（历史空值）时按不超额处理；整笔拒绝架构下正常路径也恒为 False。
    """
    parallelism = lease["parallelism"]
    rated = lease["rated_concurrency"]
    return parallelism is not None and rated is not None and int(parallelism) > int(rated)


def replay_open_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """由 open 租约集合重放在途/超额在途计数。这是全系统唯一计数口径。"""
    open_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open'"
    ).fetchone()["c"]
    over_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open' AND overage=1"
    ).fetchone()["c"]
    return int(open_n), int(over_n)


def reconcile_capacity(conn: sqlite3.Connection) -> dict:
    """把钉扎旗、批窗旗、水位全部收敛到 open 租约重放结果。

    幂等：健康库上零写入（仅刷新水位时间戳与清理遗留缓存）。覆盖四类撕裂夹具：
    - ok 已超无标：open 行快照超额却 overage=0 → 补标；
    - 拒开水位偷加：watermarks 高于 open 重放 → 回写压回真实值；
    - 标记在无租约：旗在 open 集合中无对应 → 摘旗，关联批窗旗一并清零；
    - 并发双加/漏记：计数永远以行重算，重复对账必然收敛。
    """
    pinned = 0
    for lease in conn.execute("SELECT * FROM leases").fetchall():
        expected = 1 if lease["status"] == "open" and lease_pins_overage(lease) else 0
        if int(lease["overage"] or 0) != expected:
            conn.execute(
                "UPDATE leases SET overage=? WHERE id=?", (expected, lease["id"])
            )
        if expected == 1:
            pinned += 1

    # 批窗钉扎旗同样必须能由其 open 租约重放：无超额 open 租约则摘旗。
    for w in conn.execute("SELECT code FROM windows WHERE status='live'").fetchall():
        has_pinned = conn.execute(
            "SELECT COUNT(*) c FROM leases "
            "WHERE window_code=? AND status='open' AND overage=1",
            (w["code"],),
        ).fetchone()["c"]
        conn.execute(
            "UPDATE windows SET overage=? WHERE code=?",
            (1 if has_pinned else 0, w["code"]),
        )
    # 非 live 批窗不得残留钉扎旗（拒开/已关窗都没有 open 租约可凭据）。
    conn.execute(
        "UPDATE windows SET overage=0 WHERE status!='live' AND overage!=0"
    )

    open_n, overage_n = replay_open_counts(conn)
    # 钉扎旗既已对齐快照，旗计数必须等于快照重算计数，否则逻辑自相矛盾。
    snapshot_over_n = conn.execute(
        "SELECT COUNT(*) c FROM leases WHERE status='open' "
        "AND parallelism IS NOT NULL AND rated_concurrency IS NOT NULL "
        "AND parallelism > rated_concurrency"
    ).fetchone()["c"]
    if int(snapshot_over_n) != overage_n:  # 防御性自检，正常不可达。
        raise RuntimeError(
            f"超额钉扎旗重放不一致：旗计数 {overage_n}，快照计数 {snapshot_over_n}"
        )

    _persist_watermark(conn, "open_leases", open_n)
    _persist_watermark(conn, "overage_open", overage_n)

    # 旧架构遗留的独立快照缓存是总览分叉源头，一律清退，永不重建。
    conn.execute("DELETE FROM settings WHERE key='capacity_paint'")

    return {
        "open_leases": open_n,
        "overage_open": overage_n,
        "pinned_overage_leases": pinned,
        "watermarks_consistent": True,
    }
