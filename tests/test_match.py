"""topic_match 测试。

━━━ 用什么标准判断这个工具有没有用 ━━━

不是「能跑通」，而是：**两个真实账号在同一批热点上，
能不能选出不同的、各自合理的选题**。

如果「小赵聊软件」和「小赵无人零售」选出一样的结果，
说明匹配根本没起作用——那这个工具就是摆设。

所以核心测试就是这条：**同类热点必须对两个账号给出不同排序**。
"""

from __future__ import annotations

import pytest

from social_insight.hot.match import (
    DOMAIN_LEXICON,
    PRESET_PROFILES,
    AccountProfile,
    _domain_score,
    _normalize,
    get_preset,
    match_topics,
)
from social_insight.models import HotTopic


def make_topic(word: str, platform: str = "douyin", hot: int | None = None) -> HotTopic:
    return HotTopic(
        platform=platform,
        word=word,
        position=1,
        hot_value=hot,
        captured_at="2026-10-12T00:00:00Z",
    )


# ══════════════════════════════════════════════════════════════════
# 归一化
# ══════════════════════════════════════════════════════════════════


class TestNormalize:
    def test_lowercase(self):
        assert _normalize("AI") == "ai"

    def test_strips_noise(self):
        assert _normalize("软件 开发！") == "软件开发"

    def test_empty(self):
        assert _normalize("") == ""


# ══════════════════════════════════════════════════════════════════
# 领域打分
# ══════════════════════════════════════════════════════════════════


class TestDomainScore:
    def test_software_topic(self):
        assert _domain_score("某AI大模型发布", "software") > 0

    def test_sports_topic(self):
        assert _domain_score("郑钦文退出武网", "sports") > 0

    def test_irrelevant_returns_zero(self):
        """不相关的领域应返回 0，不是低分噪声。"""
        assert _domain_score("郑钦文退出武网", "software") == 0.0
        assert _domain_score("某AI大模型发布", "retail") == 0.0

    def test_unknown_domain(self):
        assert _domain_score("任意文本", "不存在的领域") == 0.0

    def test_score_capped(self):
        """分数上限为 1。"""
        text = " ".join(DOMAIN_LEXICON["software"][:20])
        assert _domain_score(text, "software") <= 1.0

    def test_more_hits_higher_score(self):
        one = _domain_score("软件开发", "software")
        three = _domain_score("软件开发部署运维", "software")
        assert three >= one


# ══════════════════════════════════════════════════════════════════
# 预设账号
# ══════════════════════════════════════════════════════════════════


class TestPresets:
    def test_both_exist(self):
        assert "小赵聊软件" in PRESET_PROFILES
        assert "小赵无人零售" in PRESET_PROFILES

    def test_software_profile(self):
        p = get_preset("小赵聊软件")
        assert "software" in p.domains
        assert "软件" in p.keywords
        assert p.form == "image_text"

    def test_retail_profile(self):
        p = get_preset("小赵无人零售")
        assert "retail" in p.domains
        assert any("咖啡" in k for k in p.keywords)

    def test_unknown_raises(self):
        with pytest.raises(KeyError):
            get_preset("不存在的账号")

    def test_profiles_differ(self):
        """两个预设必须真的不同——否则匹配没有意义。"""
        s = get_preset("小赵聊软件")
        r = get_preset("小赵无人零售")
        assert s.domains != r.domains
        assert not set(s.keywords) & set(r.keywords)


# ══════════════════════════════════════════════════════════════════
# 核心：两个账号必须选出不同的选题
# ══════════════════════════════════════════════════════════════════


#: 混合热点：有软件相关、有零售相关、有完全无关的
MIXED_TOPICS = [
    "某AI大模型开源发布",
    "无人咖啡机进驻写字楼",
    "郑钦文退出武汉网球公开赛",
    "软件开发避坑指南",
    "冰淇淋机点位租金怎么谈",
    "某明星官宣恋情",
    "企业数字化转型提速",
    "便利店选址技巧",
]


class TestMatchDifferentiates:
    """如果两个账号选出一样的结果，说明匹配没起作用。"""

    def test_software_account_prefers_software_topics(self):
        p = get_preset("小赵聊软件")
        matches = match_topics([make_topic(t) for t in MIXED_TOPICS], p, top_n=3)
        words = [m.word for m in matches]
        assert any("AI" in w or "软件" in w or "数字化" in w for w in words), (
            f"软件号没选出软件/AI 类话题: {words}"
        )

    def test_retail_account_prefers_retail_topics(self):
        p = get_preset("小赵无人零售")
        matches = match_topics([make_topic(t) for t in MIXED_TOPICS], p, top_n=3)
        words = [m.word for m in matches]
        assert any("咖啡机" in w or "冰淇淋" in w or "便利店" in w for w in words), (
            f"零售号没选出零售类话题: {words}"
        )

    def test_two_accounts_give_different_top1(self):
        """决定性测试：两个账号的**首选**必须不同。"""
        topics = [make_topic(t) for t in MIXED_TOPICS]
        s_top = match_topics(topics, get_preset("小赵聊软件"), top_n=1)[0]
        r_top = match_topics(topics, get_preset("小赵无人零售"), top_n=1)[0]
        assert s_top.word != r_top.word, (
            f"两个账号选出了同一个首选「{s_top.word}」——匹配未生效"
        )

    def test_irrelevant_topics_ranked_low(self):
        """明星八卦对两个号都应该排很低。"""
        topics = [make_topic(t) for t in MIXED_TOPICS]
        for preset_name in ["小赵聊软件", "小赵无人零售"]:
            matches = match_topics(topics, get_preset(preset_name), top_n=10)
            celeb = next((m for m in matches if "官宣" in m.word), None)
            assert celeb is not None
            assert celeb.level == "weak", f"{preset_name} 给八卦话题打的分过高"


class TestMatchScoring:
    def test_keyword_hit_boosts(self):
        """业务词直接命中应显著提升分数。"""
        p = AccountProfile(keywords=["咖啡机"], domains=["retail"])
        hit = match_topics([make_topic("无人咖啡机新品")], p)[0]
        miss = match_topics([make_topic("足球比赛结果")], p)[0]
        assert hit.score > miss.score

    def test_reasons_explain_why(self):
        """理由必须存在——这工具的價值就在于可解释。"""
        p = get_preset("小赵无人零售")
        m = match_topics([make_topic("无人咖啡机进驻写字楼")], p)[0]
        assert m.reasons
        assert any("咖啡机" in r for r in m.reasons), f"理由没提到命中的词: {m.reasons}"

    def test_hit_keywords_recorded(self):
        p = AccountProfile(keywords=["咖啡机", "点位"], domains=["retail"])
        m = match_topics([make_topic("咖啡机点位选择")], p)[0]
        assert "咖啡机" in m.hit_keywords
        assert "点位" in m.hit_keywords

    def test_score_range(self):
        p = get_preset("小赵聊软件")
        for m in match_topics([make_topic(t) for t in MIXED_TOPICS], p, top_n=10):
            assert 0.0 <= m.score <= 1.0

    def test_sorted_descending(self):
        p = get_preset("小赵聊软件")
        matches = match_topics([make_topic(t) for t in MIXED_TOPICS], p, top_n=10)
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_respected(self):
        p = get_preset("小赵聊软件")
        assert len(match_topics([make_topic(t) for t in MIXED_TOPICS], p, top_n=3)) == 3

    def test_noise_penalty(self):
        """命中无关领域应被扣分（软件号遇到体育热点）。"""
        p = get_preset("小赵聊软件")
        sports = match_topics([make_topic("足球比赛夺冠")], p)[0]
        # 体育领域命中 → noise 高 → 分数应低于中性话题
        assert sports.score < 0.35, f"体育热点对软件号不该得高分: {sports.score}"


class TestFormIsMultiplierNotAdditive:
    """形态契合必须是乘数，不能独立加分。

    实测踩过的坑：只按「适合做图文」独立给 0.15 分时，
    「对对联」「养鸡场」这类完全无关的话题也会进候选池。
    """

    def test_irrelevant_tutorial_gets_zero(self):
        """无关话题即使「适合做图文」也不该得分。"""
        p = AccountProfile(keywords=["咖啡机"], domains=["retail"], form="image_text")
        # 「怎么对」含形态词「怎么」，但完全不相关
        m = match_topics([make_topic("牛顿顿顿吃肉下联怎么对")], p)[0]
        assert m.score == 0.0, f"无关话题不该得分: {m.score}"

    def test_relevant_tutorial_boosted(self):
        """相关话题命中形态词应被放大。"""
        p = AccountProfile(keywords=["咖啡机"], domains=["retail"], form="image_text")
        plain = match_topics([make_topic("咖啡机新品")], p)[0]
        tutorial = match_topics([make_topic("咖啡机选购攻略教程")], p)[0]
        assert tutorial.score > plain.score

    def test_form_alone_never_strong(self):
        """只有形态契合绝不可能是 strong。"""
        p = AccountProfile(keywords=["咖啡机"], domains=["retail"])
        m = match_topics([make_topic("如何快速入睡")], p)[0]
        assert m.level == "weak"


class TestCrossSourceDedup:
    """同一话题在多个源出现时必须去重。"""

    def test_duplicates_removed(self):
        p = get_preset("小赵无人零售")
        topics = [
            make_topic("霸王茶姬店员劝退消费者", platform="weibo"),
            make_topic("霸王茶姬店员劝退消费者", platform="baidu"),
            make_topic("霸王茶姬店员劝退消费者", platform="tophub"),
        ]
        matches = match_topics(topics, p, top_n=10)
        assert len(matches) == 1, f"重复未去除: {len(matches)} 条"

    def test_dedup_is_normalized(self):
        """标点/空白不同的同一话题也应去重。"""
        p = get_preset("小赵无人零售")
        topics = [
            make_topic("无人咖啡机 进驻"),
            make_topic("无人咖啡机进驻"),
        ]
        assert len(match_topics(topics, p, top_n=10)) == 1

    def test_different_topics_kept(self):
        """不同话题不能被误删。"""
        p = get_preset("小赵无人零售")
        topics = [make_topic("咖啡机新品"), make_topic("冰淇淋机新品")]
        assert len(match_topics(topics, p, top_n=10)) == 2


class TestCorroborationPassthrough:
    """「火」与「相关」必须分开呈现。"""

    def test_corroboration_attached(self):
        p = get_preset("小赵聊软件")
        corr = {"某AI大模型开源发布": {"level": "confirmed", "source_count": 4}}
        m = match_topics([make_topic("某AI大模型开源发布")], p, corroboration_of=corr)[0]
        assert m.corroboration == "confirmed"
        assert m.source_count == 4

    def test_hot_but_irrelevant_still_low_score(self):
        """**关键**：很火但不相关，不该因为火就排前面。"""
        p = get_preset("小赵聊软件")
        corr = {"郑钦文退出武汉网球公开赛": {"level": "confirmed", "source_count": 5}}
        m = match_topics([make_topic("郑钦文退出武汉网球公开赛")], p, corroboration_of=corr)[0]
        assert m.corroboration == "confirmed", "应如实反映热度"
        assert m.score < 0.35, "但匹配度必须低——很火不等于相关"


# ══════════════════════════════════════════════════════════════════
# 边界
# ══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_topics(self):
        assert match_topics([], get_preset("小赵聊软件")) == []

    def test_empty_word_skipped(self):
        assert match_topics([make_topic("")], get_preset("小赵聊软件")) == []

    def test_empty_profile(self):
        """空定位不该崩溃，只是匹配度都很低。"""
        p = AccountProfile()
        matches = match_topics([make_topic("某AI大模型")], p)
        assert len(matches) == 1
        assert matches[0].score <= 0.2

    def test_serialization(self):
        import json

        p = get_preset("小赵聊软件")
        m = match_topics([make_topic("软件开发避坑指南")], p)[0]
        json.dumps(m.to_dict(), ensure_ascii=False)

    def test_profile_to_dict(self):
        d = get_preset("小赵聊软件").to_dict()
        assert d["name"] == "小赵聊软件"
        assert "software" in d["domains"]
