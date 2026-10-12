#!/usr/bin/env python3
"""topic_match 真实账号端到端验证。

决定性验收标准：**两个账号在同一批热点上必须选出不同的选题**。
如果选出一样的，说明匹配没起作用。
"""

import asyncio
import sys

sys.path.insert(0, "src")
from social_insight.hot.match import get_preset, match_topics
from social_insight.hot.sources import DEFAULT_SOURCES, fetch_many
from social_insight.http import HttpClient


async def main() -> int:
    client = HttpClient()
    topics, failures = await fetch_many(client, DEFAULT_SOURCES, limit_per_source=40)
    print(f"抓取 {len(topics)} 条热点，失败源 {len(failures)} 个")
    if failures:
        for f in failures:
            print("   -", f["display_name"], f["error"][:50])
    print()

    results = {}
    for preset_name in ["tech-blogger", "retail-operator"]:
        profile = get_preset(preset_name)
        matches = match_topics(topics, profile, top_n=6)
        results[preset_name] = matches

        print("=" * 70)
        print(f"【{preset_name}】领域={profile.domains}  关键词={profile.keywords[:4]}...")
        print("=" * 70)
        for m in matches:
            print(f"  {m.score:.2f} [{m.level:6s}] {m.word[:40]}")
            print(f"        理由: {m.reasons[0] if m.reasons else '—'}")
            if len(m.reasons) > 1:
                print(f"              {m.reasons[1]}")
        print()

    # ---- 决定性验收 ----
    print("=" * 70)
    s_top = {m.word for m in results["tech-blogger"]}
    r_top = {m.word for m in results["retail-operator"]}
    overlap = s_top & r_top

    print(f"软件号 top6: {len(s_top)} 条")
    print(f"零售号 top6: {len(r_top)} 条")
    print(f"重合: {len(overlap)} 条")
    if overlap:
        print("  重合项:", list(overlap)[:3])

    ok = True
    if len(overlap) >= 4:
        print("\n❌ 重合过多——匹配可能未生效")
        ok = False
    else:
        print("\n✅ 两个账号选出了不同的选题——匹配生效")

    await client.aclose()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
