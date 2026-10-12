"""交叉验证算法测试。

━━━ 这是项目的核心算法，必须有充分覆盖 ━━━

覆盖维度：
  1. 归一化（标点/话题符号/空白/噪声前后缀）
  2. 相似度（bigram Jaccard）
  3. 实体锚点（最长公共子串）
  4. 极性守卫（反义词——实测踩过的坑）
  5. 独立性去重（镜像源不重复计数）
  6. 分组与分级
  7. 边界条件（空输入、单源、全重复）
"""

from __future__ import annotations

import pytest

from social_insight.hot.corroborate import (
    CorroboratedTopic,
    _has_conflicting_polarity,
    _is_same_topic,
    _lcs_len,
    _similarity,
    corroborate,
    normalize,
    summarize,
)


# ══════════════════════════════════════════════════════════════════
# 1. 归一化
# ══════════════════════════════════════════════════════════════════


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("#话题#", "话题"),
            ("【突发】新闻", "新闻"),  # 「突发」是 _TRIM 里的噪声前缀，会被剥离
            ("【】新闻", "新闻"),
            ("a b c", "abc"),
            ("话题！", "话题"),
            ("「引号」", "引号"),
            ("A-B_C", "abc"),
            ("  空白  ", "空白"),
        ],
    )
    def test_strips_noise(self, raw, expected):
        assert normalize(raw) == expected

    def test_lowercases_latin(self):
        assert normalize("ABC") == "abc"

    def test_empty(self):
        assert normalize("") == ""
        assert normalize("###") == ""

    def test_idempotent(self):
        """归一化两次结果相同。"""
        once = normalize("#某话题#")
        assert normalize(once) == once


# ══════════════════════════════════════════════════════════════════
# 2. 相似度
# ══════════════════════════════════════════════════════════════════


class TestSimilarity:
    def test_identical(self):
        assert _similarity("abc", "abc") == 1.0

    def test_disjoint(self):
        assert _similarity("abc", "xyz") == 0.0

    def test_symmetric(self):
        assert _similarity("abcd", "abce") == _similarity("abce", "abcd")

    def test_empty(self):
        assert _similarity("", "abc") == 0.0
        assert _similarity("abc", "") == 0.0

    def test_single_char_substring(self):
        assert _similarity("a", "abc") == 1.0

    def test_partial_overlap(self):
        s = _similarity("郑钦文夺冠", "郑钦文退赛")
        assert 0 < s < 1

    def test_range(self):
        """结果必须在 [0,1]。"""
        for a, b in [("abc", "abd"), ("", "x"), ("中文测试", "中文测验")]:
            assert 0.0 <= _similarity(a, b) <= 1.0


# ══════════════════════════════════════════════════════════════════
# 3. 最长公共子串（实体锚点）
# ══════════════════════════════════════════════════════════════════


class TestLCS:
    def test_identical(self):
        assert _lcs_len("abc", "abc") == 3

    def test_entity_anchor(self):
        """实测案例：两条标题的共同实体是「郑钦文」。"""
        assert _lcs_len("武网官方回应郑钦文退赛", "郑钦文退出武网") == 4

    def test_no_common(self):
        assert _lcs_len("abc", "xyz") == 0

    def test_empty(self):
        assert _lcs_len("", "abc") == 0
        assert _lcs_len("abc", "") == 0

    def test_symmetric(self):
        assert _lcs_len("abcdef", "cdefgh") == _lcs_len("cdefgh", "abcdef")


# ══════════════════════════════════════════════════════════════════
# 4. 极性守卫（实测踩过的坑）
# ══════════════════════════════════════════════════════════════════


class TestPolarityGuard:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("A股三大指数集体上涨", "A股三大指数集体下跌"),
            ("郑钦文夺冠", "郑钦文退赛"),
            ("公司获批", "公司被否决"),
            ("项目成功", "项目失败"),
            ("会议开幕", "会议闭幕"),
            ("服务恢复", "服务暂停"),
        ],
    )
    def test_detects_conflict(self, a, b):
        assert _has_conflicting_polarity(a, b) is True

    @pytest.mark.parametrize(
        "a,b",
        [
            ("武网官方回应郑钦文退赛", "郑钦文退出武网"),
            ("同一个话题", "同一个话题"),
            ("我国物流业景气水平提升", "物流业景气水平提升"),
        ],
    )
    def test_no_false_positive(self, a, b):
        assert _has_conflicting_polarity(a, b) is False

    def test_symmetric(self):
        a, b = "上涨", "下跌"
        assert _has_conflicting_polarity(a, b) == _has_conflicting_polarity(b, a)

    def test_both_polarities_mentioned_is_not_conflict(self):
        """同一条里正反都提到 → 不算冲突（避免误伤）。"""
        assert _has_conflicting_polarity("涨跌互现", "涨跌互现") is False


# ══════════════════════════════════════════════════════════════════
# 5. 同话题判定
# ══════════════════════════════════════════════════════════════════


class TestIsSameTopic:
    @pytest.mark.parametrize(
        "a,b,why",
        [
            ("武网官方回应郑钦文退赛", "郑钦文退出武网", "实测漏判案例"),
            ("#郑钦文首夺中网女单冠军#", "郑钦文首夺中网女单冠军", "话题符号"),
            ("郑钦文 首夺 冠军", "郑钦文首夺冠军", "空格"),
            ("刚刚！某事件", "某事件", "前缀噪声"),
            ("完全相同", "完全相同", "相同"),
        ],
    )
    def test_same(self, a, b, why):
        assert _is_same_topic(normalize(a), normalize(b)) is True, why

    @pytest.mark.parametrize(
        "a,b,why",
        [
            ("郑钦文首夺中网女单冠军", "郑钦文退出武网", "同实体不同事件"),
            ("郑钦文夺冠", "郑钦文退赛", "相反事件"),
            ("A股三大指数集体上涨", "A股三大指数集体下跌", "相反事件"),
            ("物流业景气提升", "外贸进出口增长", "不同事件"),
        ],
    )
    def test_different(self, a, b, why):
        assert _is_same_topic(normalize(a), normalize(b)) is False, why

    def test_polarity_beats_similarity(self):
        """极性守卫必须优先于相似度——即使措辞极像。

        「A股三大指数集体上涨/下跌」的 bigram Jaccard 高达 0.64，
        单看相似度会被误判为同一话题。
        """
        a, b = normalize("A股三大指数集体上涨"), normalize("A股三大指数集体下跌")
        assert _similarity(a, b) >= 0.5  # 确实很像
        assert _is_same_topic(a, b) is False  # 但必须判为不同


# ══════════════════════════════════════════════════════════════════
# 6. 独立性去重（核心：镜像源不能重复计数）
# ══════════════════════════════════════════════════════════════════


class TestSubjectAnchorTrap:
    """共享主语 ≠ 同一事件。

    ━━━ 为什么单开一个测试类 ━━━

    实测踩过的坑（2026-10-12）：

        「霸王茶姬店员劝退消费者」
        「霸王茶姬店员活动日有三倍工资吗」

    共享锚点「霸王茶姬店员」(LCS=6)，jac=0.26 —— 被误判为同一话题。
    但前者讲服务事故、后者讲薪资，是两件事。

    **规律**：实体锚点只能证明「在说同一个对象」，
    不能证明「在说同一件事」。锚点必须搭配足够的措辞重合才成立。
    """

    @pytest.mark.parametrize(
        "a,b,why",
        [
            ("霸王茶姬店员劝退消费者", "霸王茶姬店员活动日有三倍工资吗",
             "共享主语，谓语不同"),
            ("郑钦文登长城拍中网冠军写真", "郑钦文首夺中网女单冠军",
             "同人不同事件"),
            ("霸王茶姬店员劝退消费者", "霸王茶姬就崩了致歉",
             "同品牌不同事件"),
        ],
    )
    def test_shared_subject_not_merged(self, a, b, why):
        assert _is_same_topic(normalize(a), normalize(b)) is False, why

    @pytest.mark.parametrize(
        "a,b,why",
        [
            ("霸王茶姬买一送一赢了订单输了体验", "霸王茶姬买一送一到底是谁在喝啊",
             "同事件不同措辞"),
            ("武网官方回应郑钦文退赛", "郑钦文退出武网", "实测应合并"),
        ],
    )
    def test_same_event_still_merged(self, a, b, why):
        """收紧阈值不能破坏真正的同事件合并。"""
        assert _is_same_topic(normalize(a), normalize(b)) is True, why

    def test_threshold_gap_exists(self):
        """验证两类案例之间确实存在可分间隙。

        误合并案例 jac≈0.26，正确合并案例 jac≈0.32 ——
        阈值必须落在这个区间内。这个测试锁住间隙存在：
        将来若有人改了相似度算法导致间隙消失，会立刻暴露。
        """
        false_case = _similarity(
            normalize("霸王茶姬店员劝退消费者"),
            normalize("霸王茶姬店员活动日有三倍工资吗"),
        )
        true_case = _similarity(
            normalize("武网官方回应郑钦文退赛"), normalize("郑钦文退出武网")
        )
        assert false_case < true_case, (
            f"误合并与正确合并的相似度区间重叠："
            f"{false_case:.2f} vs {true_case:.2f} —— 阈值将无法区分"
        )

        from social_insight.hot.corroborate import ENTITY_SIM_FLOOR

        assert false_case < ENTITY_SIM_FLOOR <= true_case, (
            f"ENTITY_SIM_FLOOR={ENTITY_SIM_FLOOR} 没落在 "
            f"({false_case:.2f}, {true_case:.2f}] 区间内"
        )


class TestIndependence:
    def test_mirror_sources_count_once(self, topic_factory):
        """镜像源（同 independence 键）只算一票。

        实测背景：uapis 的抖音榜与抖音官方榜前 10 条重合 7 条。
        如果把它们当两个独立源，会得出虚假的「confirmed」。
        """
        topics = [
            topic_factory("同一个热点", platform="douyin"),
            topic_factory("同一个热点", platform="uapis:douyin"),  # 镜像
        ]
        results = corroborate(
            topics,
            independence_of={"douyin": "douyin", "uapis:douyin": "douyin"},  # 同键
            weight_of={"douyin": 1.0, "uapis:douyin": 0.9},
            display_of={"douyin": "抖音", "uapis:douyin": "uapis"},
        )
        assert len(results) == 1
        assert results[0].source_count == 1, "镜像源被错误地算成了两个独立源"
        assert results[0].level == "single"

    def test_independent_sources_count_separately(self, topic_factory):
        """独立性键不同 → 各算一票。"""
        topics = [
            topic_factory("同一热点", platform="douyin"),
            topic_factory("同一热点", platform="weibo"),
            topic_factory("同一热点", platform="baidu"),
        ]
        results = corroborate(
            topics,
            independence_of={"douyin": "douyin", "weibo": "weibo", "baidu": "baidu"},
            weight_of={"douyin": 1.0, "weibo": 0.9, "baidu": 0.95},
            display_of={"douyin": "抖音", "weibo": "微博", "baidu": "百度"},
        )
        assert len(results) == 1
        assert results[0].source_count == 3
        assert results[0].level == "confirmed"


# ══════════════════════════════════════════════════════════════════
# 7. 分级与分组
# ══════════════════════════════════════════════════════════════════


class TestLevels:
    @pytest.mark.parametrize("n,expected", [(1, "single"), (2, "likely"), (3, "confirmed"), (5, "confirmed")])
    def test_level_thresholds(self, topic_factory, n, expected):
        topics = [topic_factory("热点", platform=f"s{i}") for i in range(n)]
        results = corroborate(
            topics,
            independence_of={f"s{i}": f"s{i}" for i in range(n)},
            weight_of={f"s{i}": 0.5 for i in range(n)},
            display_of={f"s{i}": f"源{i}" for i in range(n)},
        )
        assert results[0].level == expected

    def test_min_sources_filter(self, topic_factory):
        topics = [
            topic_factory("双源话题", platform="a"),
            topic_factory("双源话题", platform="b"),
            topic_factory("单源话题", platform="a"),
        ]
        indep = {"a": "a", "b": "b"}
        w = {"a": 1.0, "b": 1.0}
        d = {"a": "A", "b": "B"}

        all_results = corroborate(topics, independence_of=indep, weight_of=w, display_of=d)
        assert len(all_results) == 2

        filtered = corroborate(
            topics, independence_of=indep, weight_of=w, display_of=d, min_sources=2
        )
        assert len(filtered) == 1
        assert filtered[0].word == "双源话题"


class TestGrouping:
    def test_merges_variants(self, topic_factory):
        """同一话题的不同措辞应合并，且保留所有变体。"""
        topics = [
            topic_factory("武网官方回应郑钦文退赛", platform="weibo"),
            topic_factory("郑钦文退出武网", platform="douyin"),
        ]
        results = corroborate(
            topics,
            independence_of={"weibo": "weibo", "douyin": "douyin"},
            weight_of={"weibo": 0.9, "douyin": 1.0},
            display_of={"weibo": "微博", "douyin": "抖音"},
        )
        assert len(results) == 1
        assert results[0].source_count == 2
        assert len(results[0].variants) == 2, "变体表述应全部保留"

    def test_representative_takes_highest_weight(self, topic_factory):
        """代表标题取权重最高的源。"""
        topics = [
            topic_factory("低权重源的表述", platform="low"),
            topic_factory("高权重源的表述", platform="high"),
        ]
        results = corroborate(
            topics,
            independence_of={"low": "low", "high": "high"},
            weight_of={"low": 0.1, "high": 0.99},
            display_of={"low": "L", "high": "H"},
        )
        assert results[0].word == "高权重源的表述"

    def test_sorted_by_source_count(self, topic_factory):
        """结果按独立源数量降序——强信号排前面。"""
        topics = [
            topic_factory("单源话题", platform="a"),
            topic_factory("三源话题", platform="a"),
            topic_factory("三源话题", platform="b"),
            topic_factory("三源话题", platform="c"),
        ]
        results = corroborate(
            topics,
            independence_of={"a": "a", "b": "b", "c": "c"},
            weight_of={"a": 1.0, "b": 1.0, "c": 1.0},
            display_of={"a": "A", "b": "B", "c": "C"},
        )
        assert results[0].source_count == 3
        assert results[-1].source_count == 1


# ══════════════════════════════════════════════════════════════════
# 8. 边界条件
# ══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_input(self):
        assert corroborate([], independence_of={}, weight_of={}, display_of={}) == []

    def test_empty_words_skipped(self, topic_factory):
        """空标题必须跳过，不能产生空话题。"""
        topics = [topic_factory(""), topic_factory("###"), topic_factory("真实话题")]
        results = corroborate(
            topics,
            independence_of={"douyin": "douyin"},
            weight_of={"douyin": 1.0},
            display_of={"douyin": "抖音"},
        )
        assert len(results) == 1
        assert results[0].word == "真实话题"

    def test_unknown_source_uses_defaults(self, topic_factory):
        """未登记的源不应崩溃，用默认值处理。"""
        topics = [topic_factory("话题", platform="未知源")]
        results = corroborate(topics, independence_of={}, weight_of={}, display_of={})
        assert len(results) == 1

    def test_many_duplicates(self, topic_factory):
        """大量重复不应产生重复话题。"""
        topics = [topic_factory("同一个", platform=f"s{i}") for i in range(20)]
        results = corroborate(
            topics,
            independence_of={f"s{i}": f"s{i}" for i in range(20)},
            weight_of={f"s{i}": 0.5 for i in range(20)},
            display_of={f"s{i}": f"S{i}" for i in range(20)},
        )
        assert len(results) == 1
        assert results[0].source_count == 20


class TestSummarize:
    def test_counts(self):
        items = [
            CorroboratedTopic(word="a", level="confirmed", source_count=3),
            CorroboratedTopic(word="b", level="likely", source_count=2),
            CorroboratedTopic(word="c", level="single", source_count=1),
            CorroboratedTopic(word="d", level="single", source_count=1),
        ]
        s = summarize(items)
        assert s["total"] == 4
        assert s["confirmed"] == 1
        assert s["likely"] == 1
        assert s["single"] == 2

    def test_empty(self):
        s = summarize([])
        assert s["total"] == 0
        assert s["confirmed"] == 0

    def test_has_hint(self):
        """必须有 hint 解释分级含义——LLM 靠它理解结果。"""
        assert summarize([])["hint"]


class TestSerialization:
    def test_topic_to_dict(self):
        t = CorroboratedTopic(
            word="话题",
            level="confirmed",
            source_count=3,
            sources=["抖音", "微博"],
            variants=["话题", "话题！"],
            best_position=1,
            max_hot_value=100,
        )
        d = t.to_dict()
        assert d["word"] == "话题"
        assert d["level"] == "confirmed"
        assert d["source_count"] == 3
        assert d["variants"] == ["话题", "话题！"]

    def test_json_serializable(self):
        import json

        t = CorroboratedTopic(word="中文", level="likely", source_count=2)
        json.dumps(t.to_dict(), ensure_ascii=False)
