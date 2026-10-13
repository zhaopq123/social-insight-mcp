"""话题匹配逻辑的单元测试。

这些用例全部来自**实测踩过的坑**，不是凭空编的。
回归测试的价值：以后调阈值时，这些用例会立刻告诉你有没有调坏。
"""

import io
import sys
from pathlib import Path

# 模块级只 reconfigure（就地改编码），**不替换 stdout 对象**。
# 原先这里是 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`，
# 它在导入时换掉全局 stdout，pytest 终端仍指向旧流 ->
# ValueError: I/O operation on closed file，整个会话崩掉（实测 428 个测试全跑不出来）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.hot.corroborate import (
    _is_same_topic,
    _lcs_len,
    _similarity,
    normalize,
)

# --- 应判定为「同一话题」（实测漏判过的案例）---
SAME_CASES = [
    # 案例 1：实测中这两条被分成了两簇，实际是同一事件（郑钦文退赛）
    ("武网官方回应郑钦文退赛", "郑钦文退出武网", "实测漏判：同事件不同措辞"),
    # 案例 2：标点/话题符号差异
    ("#郑钦文首夺中网女单冠军#", "郑钦文首夺中网女单冠军", "话题符号差异"),
    ("郑钦文 首夺 中网 女单冠军", "郑钦文首夺中网女单冠军", "空格差异"),
    # 案例 3：前后缀噪声
    ("刚刚！霸王茶姬就崩了致歉", "霸王茶姬就崩了致歉", "前缀噪声"),
    # 案例 4：完全一致
    ("我国物流业景气水平进一步提升", "我国物流业景气水平进一步提升", "完全相同"),
]

# --- 应判定为「不同话题」（避免过度合并）---
DIFF_CASES = [
    # 关键反例：都含「郑钦文」，但是两件事
    ("郑钦文首夺中网女单冠军", "郑钦文退出武网", "同实体但不同事件——不得合并"),
    ("郑钦文夺冠", "郑钦文退赛", "同实体，相反事件"),
    # 泛泛相似但无关
    ("我国物流业景气水平进一步提升", "我国外贸进出口总值增长", "同领域不同事件"),
    ("A股三大指数集体上涨", "A股三大指数集体下跌", "相反事件"),
]


def main() -> int:
    failures: list[str] = []

    print("=" * 72)
    print("应判定为「同一话题」")
    print("=" * 72)
    for a, b, why in SAME_CASES:
        na, nb = normalize(a), normalize(b)
        got = _is_same_topic(na, nb)
        sim = _similarity(na, nb)
        lcs = _lcs_len(na, nb)
        mark = "PASS" if got else "FAIL"
        if not got:
            failures.append(f"漏判: 「{a}」 vs 「{b}」({why})")
        print(f"  [{mark}] jac={sim:.2f} lcs={lcs}  {why}")
        print(f"         「{a}」 vs 「{b}」")

    print("\n" + "=" * 72)
    print("应判定为「不同话题」")
    print("=" * 72)
    for a, b, why in DIFF_CASES:
        na, nb = normalize(a), normalize(b)
        got = _is_same_topic(na, nb)
        sim = _similarity(na, nb)
        lcs = _lcs_len(na, nb)
        mark = "PASS" if not got else "FAIL"
        if got:
            failures.append(f"误合并: 「{a}」 vs 「{b}」({why})")
        print(f"  [{mark}] jac={sim:.2f} lcs={lcs}  {why}")
        print(f"         「{a}」 vs 「{b}」")

    print("\n" + "=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"全部通过（{len(SAME_CASES)} 同话题 + {len(DIFF_CASES)} 异话题）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
