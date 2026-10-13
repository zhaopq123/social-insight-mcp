"""交叉验证实测。

验证三件事：
  1. 多源并发抓取是否稳定
  2. 交叉验证算法是否能正确识别「多源同时报道」的话题
  3. 独立性去重是否生效（镜像源不重复计数）
"""

import asyncio
import sys
from pathlib import Path

# ⚠️ 原本这里是 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`，
# 那会在**模块导入时**替换掉全局 stdout 对象。
# 后果：pytest 的终端对象仍指向旧流，之后任何写操作抛
# `ValueError: I/O operation on closed file`，整个测试会话崩掉。
# （实测：这会让 428 个测试一个都跑不出来，与被测代码无关。）
#
# reconfigure() 是**就地**改编码，不换对象 —— 这才是模块级该做的事。
try:  # stdout 可能被 pytest 换成了无 buffer 的对象
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.hot.corroborate import corroborate, summarize
from social_insight.hot.sources import (
    DEFAULT_SOURCES,
    SOURCE_META,
    fetch_many,
)
from social_insight.http import HttpClient


async def main() -> None:
    client = HttpClient()
    print("=" * 74)
    print(f"交叉验证实测 · {len(DEFAULT_SOURCES)} 个源")
    print("=" * 74)

    topics, failures = await fetch_many(client, DEFAULT_SOURCES, limit_per_source=50)

    print(f"\n抓取：{len(topics)} 条原始话题")
    if failures:
        print(f"失败源 {len(failures)} 个：")
        for f in failures:
            print(f"   - {f['display_name']} ({f['source']}): {f['error'][:80]}")
    else:
        print("失败源：无")

    # 每源条数
    print("\n各源条数：")
    per: dict[str, int] = {}
    for t in topics:
        per[t.platform] = per.get(t.platform, 0) + 1
    for name, cnt in sorted(per.items(), key=lambda kv: -kv[1]):
        meta = SOURCE_META.get(name, {})
        print(f"   {meta.get('display', name):14s} {cnt:3d} 条   [独立性键={meta.get('independence', name)}]")

    # --- 交叉验证 ---
    results = corroborate(
        topics,
        independence_of={k: v["independence"] for k, v in SOURCE_META.items()},
        weight_of={k: v["weight"] for k, v in SOURCE_META.items()},
        display_of={k: v["display"] for k, v in SOURCE_META.items()},
    )

    s = summarize(results)
    print("\n" + "=" * 74)
    print(f"交叉验证结果：{s['total']} 个话题")
    print(f"   confirmed (≥3 独立源): {s['confirmed']}")
    print(f"   likely    (2 独立源) : {s['likely']}")
    print(f"   single    (1 独立源) : {s['single']}")
    print("=" * 74)

    print("\n【强信号 · confirmed】")
    for r in [x for x in results if x.level == "confirmed"][:12]:
        print(f"\n  [{r.source_count} 源] {r.word}")
        print(f"        来源: {', '.join(r.sources)}")
        if len(r.variants) > 1:
            print(f"        各源表述: {' | '.join(r.variants[:3])}")

    print("\n【值得做 · likely】")
    for r in [x for x in results if x.level == "likely"][:10]:
        print(f"  [{r.source_count} 源] {r.word[:56]}")
        print(f"        来源: {', '.join(r.sources)}")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
