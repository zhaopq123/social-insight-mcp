#!/usr/bin/env python3
"""定时快照采集。

━━━ 为什么需要独立脚本 ━━━

`trend` 的价值取决于**时间序列的密度**。若只在有人调 `hot_topics`
时才落快照，数据点是稀疏且不规则的——「这个话题在涨吗」就答不准。

这个脚本让快照**规律累积**（推荐每 1-2 小时一次），与调用无关。

━━━ 用法 ━━━

    python -m social_insight.snapshot            # 采一次
    python -m social_insight.snapshot --prune    # 采一次 + 清理 30 天前

调度（Linux cron / Termux）：
    0 */2 * * * cd /opt/social-insight-mcp && \\
      ./venv/bin/python -m social_insight.snapshot >> /tmp/si-snapshot.log 2>&1

Termux 环境注意：proot 里没有 systemd，用 cron 或 socat 类工具，
或直接在 Termux 层用 while+sleep 循环。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from .hot.sources import DEFAULT_SOURCES, fetch_many
from .http import HttpClient
from .store import SnapshotStore


async def run_once(
    *,
    sources: list[str] | None = None,
    limit: int = 50,
    db_path: str = "data/snapshots.db",
    prune_days: int | None = None,
    quiet: bool = False,
) -> dict:
    """采集一次快照。"""
    # ⚠️ 必须区分 None（未传）与 []（显式传空）——同 service.hot_topics。
    # 写成 `sources or DEFAULT_SOURCES` 会让空列表被静默替换成默认源
    # （实测在 service 里踩过，这里又犯了一次——结构性测试现在覆盖全 src/）。
    chosen = list(DEFAULT_SOURCES) if sources is None else list(sources)

    store = SnapshotStore(db_path)
    client = HttpClient()
    result: dict = {
        "ok": False,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": chosen,
    }
    try:
        topics, failures = await fetch_many(
            client, chosen, limit_per_source=limit
        )
        saved = store.save_hot_topics(topics)
        stats = store.stats()
        result.update(
            {
                "ok": len(topics) > 0,
                "fetched": len(topics),
                "saved": saved,
                "failures": failures,
                "stats": stats,
            }
        )
        if prune_days is not None:
            result["pruned"] = store.prune(keep_days=prune_days)

        # 推进 WAL 检查点。
        #
        # ⚠️ 实测（2026-10-12）：WAL 稳定在 4.7 MB 不增长但也不回落——
        # 因为 autocheckpoint 只在**写满 1000 页**时触发，
        # 而采集脚本是「批量写 → 长时间空闲」，空闲期不触发。
        #
        # 采集脚本天然是「周期性」的，正好适合顺手推进检查点。
        # 用 TRUNCATE 让 WAL 归零（每次采集完就清干净）。
        try:
            store.checkpoint(truncate=True)
            result["wal_bytes"] = store.wal_size()
        except Exception as exc:  # noqa: BLE001
            # 检查点失败不该让采集失败——数据已经落盘了
            result["wal_checkpoint_error"] = str(exc)[:100]
    finally:
        await client.aclose()
        store.close()

    if not quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="热点快照采集（用于 trend 的时间序列）")
    ap.add_argument("--sources", nargs="*", help="指定源（默认用默认组合）")
    ap.add_argument("--limit", type=int, default=50, help="每源条数")
    ap.add_argument("--db", default="data/snapshots.db", help="SQLite 路径")
    ap.add_argument("--prune", type=int, metavar="DAYS", help="同时清理 N 天前的数据")
    ap.add_argument("--quiet", action="store_true", help="不输出（供 cron 用）")
    args = ap.parse_args()

    result = asyncio.run(
        run_once(
            sources=args.sources,
            limit=args.limit,
            db_path=args.db,
            prune_days=args.prune,
            quiet=args.quiet,
        )
    )
    # 全部源失败 → 非零退出码，让调度器能发现异常
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
