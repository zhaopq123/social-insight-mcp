"""链路 A · 交叉验证（cross-source corroboration）。

━━━ 为什么需要交叉验证 ━━━

单个热榜的排名是**该站编辑口味 + 该站用户画像**的产物，不等于「全网热点」。
一个话题在多个**互相独立**的源上同时出现，才是它真的在火的证据。

但这里有个陷阱：**镜像源不算数**。
实测 uapis 的 douyin 榜与抖音官方榜前 10 条重合 7 条——它是镜像。
如果拿它给抖音热点做交叉验证，等于把同一份数据数了两遍，
会得出「两个源都报道了，所以很确定」的**虚假信心**。

因此交叉验证必须先按 `independence` 键去重：
**同一个独立性键下的所有源，合并成一张榜单，只算一票。**

━━━ 算法 ━━━

1. 按独立性键分组，每组内取「最好的一条」（官方源优先，其次排名靠前）
2. 跨组匹配同一话题 —— 用**归一化标题**做匹配
3. 统计每个话题被多少个**独立组**报道
4. 输出可信度分级：

   - `confirmed`   ≥3 个独立源 —— 强信号，可以放心做
   - `likely`      2 个独立源   —— 值得做
   - `single`      1 个源       —— 可能是该站特有，需自行判断
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..models import HotTopic

#: 话题匹配时忽略的噪声字符（标点、空白、话题符号、emoji、控制字符）
#:
#: ⚠️ 实测补充（2026-10-12）：
#:   · emoji 区段：热点标题常带 🎉🔥，不剥离会让同一话题因 emoji 差异被拆开
#:   · 控制字符 \x00-\x1f\x7f：会穿透进数据库，也让匹配失效
#:
#: 这是**唯一的归一化实现**——match 与 store 都从这里导入，
#: 避免三处各写一份导致行为漂移（见 tests/test_robustness.py 的一致性测试）。
_NOISE = re.compile(
    r"[\s#＃【】\[\]（）()《》<>「」“”\"'、,，。.！!？?~～\-—_·|｜:：;；"
    r"\U0001F300-\U0001FAFF"      # emoji 主体
    r"\u2600-\u27BF"              # 杂项符号与装饰符号
    r"\uFE0F\u200D"               # 变体选择符与零宽连接符
    r"\x00-\x1f\x7f"              # 控制字符
    r"]+"
)

#: 常见无意义后缀/前缀，匹配时剥离
_TRIM = re.compile(r"^(?:刚刚|最新|突发)[:：]?|(?:视频|详情|回应|后续)$")


def normalize(word: str) -> str:
    """归一化标题，用于跨源匹配。

    目标：把「#郑钦文首夺中网女单冠军#」「刚刚！郑钦文首夺中网女单冠军」
    视为同一话题。

    ⚠️ **这是全项目唯一的归一化实现**。`match.py` 的 `_normalize`
    与 `store.py` 的 `_normalize` 都转发到这里，三处行为必须完全一致——
    不一致会导致跨模块去重失效（实测踩过：
    corroborate 认为「【突发】新闻」=「新闻」，match 却认为不同）。
    """
    if not word:
        return ""
    s = _NOISE.sub("", word)
    s = _TRIM.sub("", s)
    return s.lower()


def _similarity(a: str, b: str) -> float:
    """字符级相似度（Jaccard on bigrams）。

    不用编辑距离——中文标题里词序调换很常见，bigram 集合更稳。
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a in b or b in a else 0.0
    ga = {a[i : i + 2] for i in range(len(a) - 1)}
    gb = {b[i : i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度。

    中文新闻标题里，**专有名词**（人名/地名/机构名）是同一事件的稳定锚点，
    而措辞会变。实测踩过的坑：

        「武网官方回应郑钦文退赛」  ←→  「郑钦文退出武网」
        同一事件，但 bigram Jaccard 只有 0.33

    它们的共同锚点是「郑钦文」（3 字连续子串）。
    所以用最长公共子串来捕捉实体。
    """
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        cur = [0] * (n + 1)
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


#: 极性对立词对。**任一方向交叉命中即判定为相反事件，不得合并。**
#:
#: 这是实测逼出来的守卫。中文新闻标题里「同实体 + 相反动作」是高频模式，
#: 而它们的措辞相似度可能非常高：
#:
#:     「A股三大指数集体上涨」 vs 「A股三大指数集体下跌」   jac=0.64
#:     「郑钦文夺冠」          vs 「郑钦文退赛」            jac=0.33
#:
#: 仅靠相似度无法区分「同一事件的不同措辞」与「相反事件」，
#: 所以需要这层语义守卫。它只做减法（阻止合并），不做加法。
_ANTONYM_PAIRS: list[tuple[str, str]] = [
    # 行情 / 数字
    ("上涨", "下跌"), ("大涨", "大跌"), ("涨停", "跌停"),
    ("上升", "下降"), ("加息", "降息"), ("升值", "贬值"),
    ("涨价", "降价"), ("增", "减"), ("涨", "跌"),
    # 体育 / 竞赛
    ("夺冠", "退赛"), ("夺冠", "出局"), ("获胜", "落败"),
    ("晋级", "淘汰"), ("晋级", "出局"), ("胜利", "失利"),
    ("赢", "输"),
    # 状态 / 决策
    ("成功", "失败"), ("开通", "关闭"), ("批准", "否决"),
    ("通过", "否决"), ("承认", "否认"), ("支持", "反对"),
    ("开始", "结束"), ("开幕", "闭幕"), ("恢复", "暂停"),
    ("签署", "撕毁"), ("解除", "实施"),
    # 审批 / 表决（实测补充）
    ("获批", "被否决"), ("获批", "被拒"), ("获准", "被驳回"),
    ("准予", "不予"), ("入选", "落选"), ("当选", "落败"),
]


def _has_conflicting_polarity(a: str, b: str) -> bool:
    """检测两个标题是否表达了**相反**的极性。

    判定条件（交叉命中）：a 含 x 不含 y，且 b 含 y 不含 x（反之亦然）。
    要求「不含」是为了避免 A 同时提到正反两面时误伤。

    这个守卫**只会阻止合并**，不会促成合并——所以它对召回是保守的，
    这是刻意的：把相反事件合并比漏掉一次合并更糟。
    """
    for x, y in _ANTONYM_PAIRS:
        a_x, a_y = x in a, y in a
        b_x, b_y = x in b, y in b
        if a_x and not a_y and b_y and not b_x:
            return True
        if a_y and not a_x and b_x and not b_y:
            return True
    return False


def _is_same_topic(norm_a: str, norm_b: str) -> bool:
    """判定两个归一化标题是否指向同一事件。

    四道闸门，顺序重要：

    0. **极性守卫**（先跑）：相反事件直接否决，不看相似度
    1. **措辞相似**：bigram Jaccard ≥ 0.5 → 同一话题
    2. **实体相同 + 措辞相关**：共享足够长的连续子串（专有名词）
       且 Jaccard ≥ ENTITY_SIM_FLOOR → 同一话题
    3. **实体占比守卫**：锚点必须占短标题的足够比例

    ━━━ 闸门 3 的由来（实测踩过的坑）━━━

    只有闸门 1、2 时，「单源话题」与「双源话题」会被误判为同一话题：
    它们共享 3 字子串「源话题」（满足 ENTITY_MIN_LEN），
    Jaccard = 0.5（恰好 ≥ 0.25 的 floor）。

    这个误判很危险——它会把不相关的话题合并，**虚增可信度**，
    让调用方误以为某话题被多个源报道。

    闸门 3 要求：LCS 长度必须占**较短标题**的 60% 以上。
    对「单源话题/双源话题」：LCS=3，短标题长 4，占比 0.75……
    仍然通过。

    所以还需要闸门 4（见下）。
    """
    # 闸门 0：相反事件
    if _has_conflicting_polarity(norm_a, norm_b):
        return False

    sim = _similarity(norm_a, norm_b)
    shorter = norm_a if len(norm_a) <= len(norm_b) else norm_b
    longer = norm_b if len(norm_a) <= len(norm_b) else norm_a

    # 闸门 1：措辞高度相似
    #
    # 阈值对**短标题**要更严。实测踩过的坑：
    #   「单源话题」vs「三源话题」—— 4 字标题差 1 字，Jaccard 恰好 0.50，
    #   但它们是完全不同的主题。
    #   而「武网官方回应郑钦文退赛」vs「郑钦文退出武网」Jaccard 只有 0.33，
    #   却是同一事件。
    #
    # 原因：标题越短，单个字的权重越大，Jaccard 越容易虚高。
    # 所以短标题要求更高的相似度。
    threshold = SIMILARITY_THRESHOLD if len(shorter) >= SHORT_TITLE_LEN else SHORT_SIM_THRESHOLD
    if sim >= threshold:
        return True

    # 闸门 2 + 3：实体锚点
    lcs = _lcs_len(norm_a, norm_b)
    if lcs < ENTITY_MIN_LEN or sim < ENTITY_SIM_FLOOR:
        return False

    # 闸门 4：锚点之外必须有足够差异
    #
    # 「单源话题」vs「双源话题」：LCS=3，差异仅首字 1 个 —— 不同主题。
    # 「武网官方回应郑钦文退赛」vs「郑钦文退出武网」：LCS=4，差异 3 个 —— 同一事件。
    if len(shorter) - lcs < ENTITY_MIN_DIFF:
        return False
    if len(longer) - lcs < ENTITY_MIN_DIFF:
        return False
    return True


#: 判定为「同一话题」的措辞相似度阈值
SIMILARITY_THRESHOLD = 0.5

#: 短标题的措辞相似度阈值（更严）。
#:
#: 实测由来：「单源话题」vs「三源话题」Jaccard=0.50 会被误判为同话题。
#: 短标题里单个字权重太大，Jaccard 天然虚高，必须提高门槛。
SHORT_SIM_THRESHOLD = 0.7

#: 「短标题」的长度界线（含）。
SHORT_TITLE_LEN = 6

#: 实体锚点最小长度（连续公共子串）——3 字足以覆盖中文人名/机构名
ENTITY_MIN_LEN = 3

#: 命中实体锚点后，措辞相似度的下限。
#:
#: ━━━ 从 0.25 提到 0.30 的原因（实测误合并）━━━
#:
#:     「霸王茶姬店员劝退消费者」 vs 「霸王茶姬店员活动日有三倍工资吗」
#:     jac=0.26  LCS=6（「霸王茶姬店员」）→ 被误判为同一话题
#:
#: 两条共享的锚点是**主语**（品牌+身份），但**谓语完全不同**
#: （劝退消费者 vs 工资），说的是两件事。
#:
#: 实测数据：正确合并的案例 jac 在 0.32~0.33，误合并的是 0.26——
#: 存在清晰间隙，0.30 可以把两者分开。
#:
#: 更一般的规律：**共享主语 ≠ 同一事件**。
#: 锚点只能证明「在说同一个对象」，不能证明「在说同一件事」，
#: 所以锚点必须搭配足够的措辞重合才成立。
ENTITY_SIM_FLOOR = 0.30

#: 锚点之外的最小差异字符数。
#:
#: 实测由来：「单源话题」vs「双源话题」共享锚点「源话题」(LCS=3)，
#: 差异只有首字 1 个 —— 但它们是**不同主题**。
#: 而「武网官方回应郑钦文退赛」vs「郑钦文退出武网」差异 3 个字符，
#: 却是同一事件。设 2 可正确区分这两组。
ENTITY_MIN_DIFF = 2


@dataclass
class CorroboratedTopic:
    """一个经过跨源验证的话题。"""

    word: str
    """代表标题——取自权重最高的源。"""

    level: str
    """confirmed / likely / single"""

    source_count: int
    """独立源数量（不是原始条目数）。"""

    sources: list[str] = field(default_factory=list)
    """报道该话题的源名列表（**显示名**，如「微博热搜」）。"""

    #: 源的技术名（如 "uapis:weibo"）——与 sources 一一对应。
    #: 加它是为了让调用方能拿到稳定标识符（显示名可能改，
    # 技术名是契约）。2026-10-13 产品改造时补。
    source_keys: list[str] = field(default_factory=list)

    variants: list[str] = field(default_factory=list)
    """各源的原始表述——差异本身有信息量（措辞角度不同）。"""

    best_position: int | None = None
    max_hot_value: int | None = None
    url: str = ""
    platforms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "level": self.level,
            "source_count": self.source_count,
            "sources": self.sources,
            "source_keys": self.source_keys,
            "variants": self.variants,
            "best_position": self.best_position,
            "max_hot_value": self.max_hot_value,
            "url": self.url,
            "platforms": self.platforms,
        }


def _level_of(source_count: int) -> str:
    if source_count >= 3:
        return "confirmed"
    if source_count == 2:
        return "likely"
    return "single"


def corroborate(
    topics: list[HotTopic],
    *,
    independence_of: dict[str, str],
    weight_of: dict[str, float],
    display_of: dict[str, str],
    min_sources: int = 1,
) -> list[CorroboratedTopic]:
    """跨源交叉验证。

    参数
    ----
    topics          : 所有源抓回的原始话题（带 source 信息）
    independence_of : 源名 → 独立性键
    weight_of       : 源名 → 可信度权重
    display_of      : 源名 → 显示名
    min_sources     : 只返回被至少这么多独立源报道的话题
    """
    # --- 1. 按独立性键分组 ---
    by_group: dict[str, list[HotTopic]] = {}
    for t in topics:
        key = independence_of.get(t.platform, t.platform)
        by_group.setdefault(key, []).append(t)

    # --- 2. 每组内按权重与排名排序，避免镜像源重复计数 ---
    #     注意：这里不丢弃条目，而是把「组」作为验证单位。
    #     同一组内的多条会自然合并到同一个话题上（因为它们本来就是同一件事）。
    ordered_groups = sorted(
        by_group.items(),
        key=lambda kv: -max(weight_of.get(t.platform, 0.5) for t in kv[1]),
    )

    # --- 3. 逐组匹配，累积证据 ---
    clusters: list[dict[str, Any]] = []

    # ── 性能：bigram 倒排索引做候选过滤 ──
    #
    # 原实现对每个话题线性扫**全部**已有簇并跑完整的 `_is_same_topic`
    # （含 LCS，O(n·m)），整体 O(n²·m)。实测 2000 条热点要 236ms。
    #
    # 优化依据（**保证不漏召回**）：
    #   · 闸门 1 是 bigram Jaccard ≥ 阈值(≥0.5) → 必然**共享至少一个 bigram**
    #   · 闸门 2/3 要 LCS ≥ ENTITY_MIN_LEN(≥2) → 连续子串长度 ≥2
    #     必然产生至少一个共享 bigram
    #   · 极性守卫只会**否决**，不会让「不共享 bigram」的两项变成同话题
    #
    # 所以「共享至少一个 bigram」是同话题的**必要条件**。
    # 只在共享 bigram 的簇里做完整判定，结果与全扫完全一致，
    # 但候选规模从 O(簇数) 降到 O(共享 bigram 的簇数)，通常是 1~几个。
    #
    # ⚠️ 顺序敏感：必须保持「按 clusters 原有顺序取第一个匹配」，
    #    否则合并顺序变化会改变哪个簇当代表 —— 那会影响 best_position 等输出。
    #    下面按索引升序遍历候选来保证这一点。
    bigram_index: dict[str, set[int]] = {}

    def _bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return set()
        return {s[i : i + 2] for i in range(len(s) - 1)}

    for group_key, group_topics in ordered_groups:
        # 组内先按「权重高、排名前」排序，让代表标题取到最好的那个
        group_topics = sorted(
            group_topics,
            key=lambda t: (-weight_of.get(t.platform, 0.5), t.position),
        )
        for t in group_topics:
            norm = normalize(t.word)
            if not norm:
                continue

            # 候选 = 共享任一 bigram 的簇（按创建顺序升序）
            grams = _bigrams(norm)
            if grams:
                candidate_ids: set[int] = set()
                for g in grams:
                    candidate_ids |= bigram_index.get(g, set())
            else:
                # 长度 < 2 的退化情况：无法用 bigram 过滤，退回全扫
                candidate_ids = set(range(len(clusters)))

            target = None
            if candidate_ids:
                for ci in sorted(candidate_ids):
                    c = clusters[ci]
                    if _is_same_topic(norm, c["norm"]):
                        target = c
                        break

            if target is None:
                new_idx = len(clusters)
                for g in _bigrams(norm):
                    bigram_index.setdefault(g, set()).add(new_idx)
                clusters.append(
                    {
                        "norm": norm,
                        "word": t.word,
                        "groups": {group_key},
                        "sources": {t.platform},
                        "variants": [t.word],
                        "best_position": t.position,
                        "max_hot_value": t.hot_value,
                        "url": t.url,
                        "platforms": {t.platform},
                        "weight": weight_of.get(t.platform, 0.5),
                    }
                )
                continue

            target["groups"].add(group_key)
            target["sources"].add(t.platform)
            if t.word not in target["variants"]:
                target["variants"].append(t.word)
            target["platforms"].add(t.platform)

            # 代表标题取权重最高的源的表述
            w = weight_of.get(t.platform, 0.5)
            if w > target["weight"]:
                target["word"] = t.word
                target["weight"] = w
                target["url"] = t.url

            if t.position and (target["best_position"] is None or t.position < target["best_position"]):
                target["best_position"] = t.position
            if t.hot_value and (target["max_hot_value"] is None or t.hot_value > target["max_hot_value"]):
                target["max_hot_value"] = t.hot_value

    # --- 4. 组装结果 ---
    out: list[CorroboratedTopic] = []
    for c in clusters:
        n = len(c["groups"])
        if n < min_sources:
            continue
        out.append(
            CorroboratedTopic(
                word=c["word"],
                level=_level_of(n),
                source_count=n,
                # sources 存**显示名**（给人看的），
                # source_keys 存**技术名**（契约标识，如 "uapis:weibo"）。
                # 两者一一对应：显示名可能改，技术名不变。
                sources=sorted(display_of.get(s, s) for s in c["sources"]),
                source_keys=sorted(c["sources"]),
                variants=c["variants"],
                best_position=c["best_position"],
                max_hot_value=c["max_hot_value"],
                url=c["url"],
                platforms=sorted(c["platforms"]),
            )
        )

        # 排序：独立源数量降序 → 权重降序 → 排名升序
        #
        # ⚠️ 刻意不用 max_hot_value 参与排序——跨源热度不可比
        #    （抖音 1200万 vs 今日热榜 4，量纲完全不同）。
        #    「几个独立源在报」+「最好排名」才是可比的信号。
    out.sort(
        key=lambda t: (
            -t.source_count,
            -(weight_of.get(t.platforms[0], 0.5) if t.platforms else 0.5),
            t.best_position if t.best_position is not None else 999,
        )
    )
    return out


def summarize(results: list[CorroboratedTopic]) -> dict[str, Any]:
    """给调用方一个一眼能看懂的可信度概览。"""
    counts = {"confirmed": 0, "likely": 0, "single": 0}
    for r in results:
        counts[r.level] = counts.get(r.level, 0) + 1
    return {
        "total": len(results),
        **counts,
        "hint": (
            "confirmed = ≥3 个独立源同时报道，强信号；"
            "likely = 2 个；single = 仅 1 个源，可能是该站特有话题。"
            "注意：已按独立性去重，镜像源不重复计数。"
        ),
    }
