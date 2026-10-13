#!/usr/bin/env python3
"""验证 compress_title：用真实热榜标题测，不用理想样例。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.hot.brief import compress_title

# 全部是实测中见过的真实标题
REAL = [
    ("郑钦文中网夺冠，十多个品牌集体发文祝贺，霸王茶姬被流量冲崩小程序，如何看待这波冠军商业「狂欢」？", "长问句尾"),
    ("三天11万星的逆天开源REA，逆向分析一切，软件开发的门槛被打到了零，最该慌的为什么不是大厂？", "另一种问句尾"),
    ("开源 Vue 3 组件库 Morya UI：把组件、文档、AI 工具链一起做进一个包", "正常标题（不该动）"),
    ("开源 Vue 3 组件库 Morya UI：把组件、文档、AI 工具链一起做进一个包", "Burmese duplicate"),
    ("李佳琦回应“疑似有孩子”", "引号包裹"),
    ("2026诺贝尔经济学奖揭晓", "短标题（不该动）"),
    ("电脑卖不动了", "极短（不该动）"),
    ("为什么会这样？", "纯问句——保险丝应拦下"),
    ("为什么坚果炒货卖不动了？", "短问句——工业剥离会过多"),
    ("刚刚！A股三大指数集体上涨", "情绪前缀"),
    ("突发：某地发生地震", "突发前缀"),
    ("如何看？", "极短问句"),
    ("榴莲仅退款事件商家又遭仅退款", "无问号正常标题"),
    ("郑钦文登长城拍中网冠军写真", "正常标题"),
    ("红星照耀中国：新时代的伟大征程", "冒号标题，无问句"),
    ("给猫剃毛后它抑郁了，网友：哈哈哈哈哈", "含网友尾巴"),
]

print("=" * 92)
print("%-46s → %-40s" % ("原文", "压缩后"))
print("=" * 92)
ok, warn = 0, 0
for word, why in REAL:
    brief, did = compress_title(word)
    mark = " " if not did else "✓"
    print("%s %-44s → %-38s" % (mark, word[:44], brief[:38]))
    print("       （%s）" % why)
    if did:
        ok += 1
    else:
        warn += 1

print()
print("=" * 92)
print("统计: 压缩 %d 条 / 保持原样 %d 条" % (ok, warn))
print()

# 风险检查：压缩后是否出现原文没有的字（新增信息）
print("── 安全检查：是否有原文没有的字符 ──")
def chars_removed_only(orig, new):
    """返回新增的字符（应恒为空）"""
    from collections import Counter
    co, cn = Counter(orig), Counter(new)
    added = cn - co
    return "".join(added)

bad = 0
for word, _ in REAL:
    brief, _ = compress_title(word)
    added = chars_removed_only(word, brief)
    if added:
        bad += 1
        print("  ❌ 新增字符 %r: %s → %s" % (added, word[:30], brief[:30]))
if bad == 0:
    print("  ✅ 全部压缩结果均为原文子序列——无新增信息")
else:
    print("  ❌ %d 条出现新增字符（违反铁律）" % bad)
