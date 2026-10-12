"""匹配结果可靠性评估测试。

━━━ 为什么需要 ━━━

实测发现：软件号的 strong 候选**全部来自掘金单源**（8/8）。

原因不是交叉验证失效，而是**垂直领域题材天生只在单源出现**——
技术热点在掘金（受众是程序员），社会热点在微博/百度（受众是大众），
同一话题不会同时出现在两边。

但调用方看到 `single(1源)` 容易误读为「这话题不够热」。
所以 summary 必须**主动说明**来源集中是正常现象。

这个测试锁住那个诚实说明。
"""

from __future__ import annotations

import pytest

from social_insight.hot.match import AccountProfile, TopicMatch
from social_insight.service import _match_summary


def mk(score: float, level: str, sources: list[str]) -> TopicMatch:
    return TopicMatch(word="话题", score=score, level=level, sources=sources)


class TestReliabilityAssessment:
    def test_empty_result(self):
        s = _match_summary([], "某账号", ["software"])
        assert s["reliability"] == "empty"
        assert "常规选题节奏" in s["advice"]

    def test_weak_day(self):
        """全是 weak —— 必须明说「今天没强相关」，并提示别硬凑。"""
        s = _match_summary(
            [mk(0.1, "weak", ["掘金热榜"])], "某账号", ["software"]
        )
        assert s["reliability"] == "weak-day"
        assert "别硬凑" in s["advice"] or "有用信息" in s["advice"]

    def test_single_source_domain(self):
        """强候选全来自单一源 —— 必须解释这是垂直领域的正常现象。

        这是**最关键的一条**：如果不说明，调用方会误判为「数据不可靠」。
        """
        matches = [mk(0.85, "strong", ["掘金热榜"]) for _ in range(5)]
        s = _match_summary(matches, "小赵聊软件", ["software"])

        assert s["reliability"] == "single-source-domain"
        assert "掘金热榜" in s["advice"]
        # 必须说清「不代表性不热」——这是诚实性的核心
        assert "垂直领域" in s["advice"]
        assert "不代表" in s["advice"]

    def test_mixed_sources(self):
        """来源分散 —— 正常情况。"""
        matches = [
            mk(0.85, "strong", ["掘金热榜"]),
            mk(0.8, "strong", ["微博", "百度"]),
        ]
        s = _match_summary(matches, "某账号", ["software"])
        assert s["reliability"] == "mixed"

    def test_counts_and_distribution(self):
        matches = [
            mk(0.85, "strong", ["掘金热榜"]),
            mk(0.6, "maybe", ["掘金热榜"]),
            mk(0.1, "weak", ["微博"]),
        ]
        s = _match_summary(matches, "某账号", ["software"])
        assert s["total"] == 3
        assert s["strong"] == 1
        assert s["maybe"] == 1
        assert s["weak"] == 1
        assert s["source_distribution"]["掘金热榜"] == 2

    def test_weak_ratio(self):
        matches = [mk(0.85, "strong", ["a"])] + [mk(0.0, "weak", ["b"]) for _ in range(9)]
        s = _match_summary(matches, "某账号", [])
        assert s["weak_ratio"] == 0.9

    def test_includes_profile_and_domains(self):
        s = _match_summary([mk(0.85, "strong", ["x"])], "小赵聊软件", ["software"])
        assert s["profile"] == "小赵聊软件"
        assert s["domains"] == ["software"]

    def test_hint_explains_score_vs_corroboration(self):
        """必须解释 score 与 corroboration 是两件事。"""
        s = _match_summary([mk(0.5, "maybe", ["x"])], "某", [])
        assert "两者分开看" in s["hint"]

    def test_all_levels_have_advice(self):
        """任何情况下都要有 advice —— 不能出现 None。"""
        for matches in [
            [],
            [mk(0.1, "weak", ["a"])],
            [mk(0.85, "strong", ["a"])],
            [mk(0.85, "strong", ["a"]), mk(0.8, "strong", ["b"])],
        ]:
            s = _match_summary(matches, "某", [])
            assert s["advice"], f"缺 advice: {matches}"
