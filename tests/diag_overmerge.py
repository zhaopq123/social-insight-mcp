#!/usr/bin/env python3
"""诊断：为什么「霸王茶姬店员劝退消费者」与「霸王茶姬店员活动日有三倍工资吗」被合并。

━━━ 假设 ━━━

这两条共享「霸王茶姬店员」这个 6 字锚点，且 Jaccard 可能超过阈值。
但它们是**不同话题**（一个讲服务事故，一个讲薪资）。

这暴露了实体锚点通道的另一个边界：锚点够长，但说的不是同一件事。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.hot.corroborate import (
    _is_same_topic,
    _lcs_len,
    _similarity,
    normalize,
)


CASES = [
    ("霸王茶姬店员劝退消费者", "霸王茶姬店员活动日有三倍工资吗", False, "实测误合并"),
    ("霸王茶姬店员劝退消费者", "霸王茶姬就崩了致歉", False, "同品牌不同事件"),
    ("霸王茶姬买一送一赢了订单输了体验", "霸王茶姬买一送一到底是谁在喝啊", True, "同事件不同措辞"),
    ("武网官方回应郑钦文退赛", "郑钦文退出武网", True, "实测应合并"),
    ("郑钦文登长城拍中网冠军写真", "郑钦文首夺中网女单冠军", False, "同人不同事件"),
]


def main() -> None:
    print("=" * 96)
    print("%-42s %-38s %6s %4s %5s %s" % ("A", "B", "jac", "lcs", "应合", "实际"))
    print("=" * 96)
    for a, b, expect, why in CASES:
        na, nb = normalize(a), normalize(b)
        jac = _similarity(na, nb)
        lcs = _lcs_len(na, nb)
        got = _is_same_topic(na, nb)
        mark = "✅" if got == expect else "❌"
        print("%-42s %-38s %6.2f %4d %5s %s %s" % (
            a[:40], b[:36], jac, lcs, expect, got, mark))
        if got != expect:
            print("      ↑ 期望 %s，实际 %s（%s）" % (expect, got, why))
            shorter = min(len(na), len(nb))
            print("        短标题长=%d  LCS=%d  差异=%d  阈值区: %s" % (
                shorter, lcs, shorter - lcs,
                "短标题(≤6)用0.7" if shorter <= 6 else "一般用0.5"))
            print("        LCS 内容: 需人工核对锚点在说什么")
    print("=" * 96)


if __name__ == "__main__":
    main()
