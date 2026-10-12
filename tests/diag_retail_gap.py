#!/usr/bin/env python3
"""诊断：retail-operator为什么没有强相关候选。

━━━ 三个待验证的假设 ━━━

H1. **词典太窄**：retail 词典只有硬件词（咖啡机/冰淇淋机），
    漏了「门店经营/消费/品牌/餐饮」这些能蹭的泛商业话题。

H2. **profile 关键词太窄**：预设只写了硬件，没写经营维度。

H3. **源配置不对称**：软件号有掘金（技术垂直源），
    零售号**没有对应的垂直源**（零售/餐饮/创业类站点）。

诊断目标：用数据判定是哪个（或哪几个）。
"""

import asyncio
import sys
from collections import Counter

sys.path.insert(0, "src")
from social_insight.hot.match import (
    AccountProfile,
    _domain_score,
    _normalize,
    get_preset,
    match_topics,
)
from social_insight.hot.sources import DEFAULT_SOURCES, fetch_many
from social_insight.http import HttpClient


async def main() -> None:
    client = HttpClient()
    topics, failures = await fetch_many(client, DEFAULT_SOURCES, limit_per_source=50)
    print(f"抓取热点 {len(topics)} 条，失败源 {len(failures)} 个")

    # ══════════════ H1 + H2：词典与关键词 ══════════════
    print("\n" + "=" * 76)
    print("【现状】当前 profile 的匹配结果")
    print("=" * 76)
    p_now = get_preset("retail-operator")
    ms_now = match_topics(topics, p_now, top_n=9999)
    lv_now = Counter(m.level for m in ms_now)
    print(f"关键词: {p_now.keywords}")
    print(f"分数分布: strong={lv_now['strong']} maybe={lv_now['maybe']} weak={lv_now['weak']}")
    print("\n前 10（注意分数都很低）:")
    for m in ms_now[:10]:
        print(f"  {m.score:.3f} [{m.level:6s}] {m.word[:56]}")

    # ══════════════ 扩展：加经营维度 ══════════════
    print("\n" + "=" * 76)
    print("【实验】扩展关键词 + 加入 business 领域")
    print("=" * 76)
    EXTRA_KW = [
        "门店", "消费", "品牌", "连锁", "饮品", "茶饮", "餐饮", "便利店",
        "加盟", "客流", "生意", "经营", "零售业", "货架", "商品", "商家",
        "顾客", "价格", "促销", "卖", "营收",
    ]
    p_ext = AccountProfile(
        name="retail-operator(扩展)",
        keywords=list(p_now.keywords) + EXTRA_KW,
        domains=["retail", "business"],   # ← 加入泛商业领域
        form="image_text",
    )
    ms_ext = match_topics(topics, p_ext, top_n=9999)
    lv_ext = Counter(m.level for m in ms_ext)
    print(f"分数分布: strong={lv_ext['strong']} maybe={lv_ext['maybe']} weak={lv_ext['weak']}")
    print("\n前 15:")
    for m in ms_ext[:15]:
        print(f"  {m.score:.3f} [{m.level:6s}] {m.word[:52]}")
        print(f"         └ {m.reasons[0] if m.reasons else '—'}")

    # ══════════════ 找出被漏掉的潜力话题 ══════════════
    print("\n" + "=" * 76)
    print("【关键】当前分数低、但扩展后变高的（= 词典/关键词漏判）")
    print("=" * 76)
    cur = {m.word: m.score for m in ms_now}
    found = []
    for m in ms_ext:
        before = cur.get(m.word, 0.0)
        if before < 0.25 and m.score >= 0.25:
            found.append((before, m.score, m.word, m.reasons[0] if m.reasons else ""))
    if found:
        for before, after, w, r in sorted(found, key=lambda x: -x[1])[:15]:
            print(f"  {before:.2f} → {after:.2f}  {w[:48]}")
            print(f"                  {r}")
    else:
        print("  （无——说明词典不是瓶颈）")

    # ══════════════ H1 深挖：词典能识别多少商业话题 ══════════════
    print("\n" + "=" * 76)
    print("【H1 验证】今天的热点里，有多少条含「商业/消费」特征词")
    print("=" * 76)
    biz_probe = [
        "门店", "消费", "品牌", "连锁", "饮品", "茶饮", "餐饮", "便利店",
        "加盟", "客流", "生意", "经营", "零售", "货架", "商品", "商家",
        "顾客", "价格", "促销", "营收", "上市", "融资", "投资", "行业",
    ]
    hit = [t for t in topics if any(w in t.word for w in biz_probe)]
    print(f"含商业特征词的热点: {len(hit)} / {len(topics)} 条")
    for t in hit[:20]:
        retail_score = _domain_score(_normalize(t.word), "retail")
        print(f"  retail={retail_score:.2f}  {t.word[:54]}")

    # ══════════════ H3 验证：源覆盖 ══════════════
    print("\n" + "=" * 76)
    print("【H3 验证】现有源的领域倾向")
    print("=" * 76)
    software_kw = sum(1 for t in topics if _domain_score(_normalize(t.word), "software") > 0)
    retail_kw = sum(1 for t in topics if _domain_score(_normalize(t.word), "retail") > 0)
    print(f"  含 software 特征的: {software_kw} 条")
    print(f"  含 retail 特征的:   {retail_kw} 条")
    print()
    print("  源清单及其领域倾向:")
    src_hint = {
        "douyin": "大众热点", "baidu": "大众/社会", "uapis:weibo": "大众/娱乐",
        "uapis:zhihu": "知识/科技", "uapis:xiaohongshu": "生活/消费",
        "bilibili": "年轻/娱乐", "juejin": "技术垂直 ★软件号专用",
        "tophub": "全网聚合",
    }
    for s in DEFAULT_SOURCES:
        print(f"    {s:20s} {src_hint.get(s, '')}")
    print()
    print("  ⚠️ 观察：没有任何源是「零售/餐饮/创业」垂直站——")
    print("     而掘金正是软件号的垂直源。这是**不对称**。")

    await client.aclose()


asyncio.run(main())
