"""账号定位的领域覆盖测试。

━━━ 实测踩过的坑 ━━━

两个预设账号最初只配了**产品维度**关键词，漏了**经营维度**：

  · retail-operator：只有「咖啡机/冰淇淋机/点位」等硬件词
  · tech-blogger：只有「软件/开发/AI」等技术词

后果（2026-10-12 实测）：零售号在 347 条热点里
**strong=0, maybe=0, weak=318** —— 看起来像「今天没有相关热点」，
实际是**词典与关键词太窄，把能做的题材全滤掉了**。

关键误判案例：「霸王茶姬开启买一送一大促，小程序崩溃，店员劝退消费者」
—— 这是无人零售号**最能做**的题材（讲活动预案、客流冲击、运营事故），
却因为词表里没有「消费/商家/客流」而被判 0.00。

修法：商业类账号的定位必须**同时**包含产品词与经营词，
并给 DOMAIN_LEXICON 补上缺失的 `business` 领域。
"""

from __future__ import annotations

import pytest

from social_insight.hot.match import (
    DOMAIN_LABELS,
    DOMAIN_LEXICON,
    _domain_score,
    _normalize,
    get_preset,
    match_topics,
)
from social_insight.models import HotTopic


def make_topic(word: str) -> HotTopic:
    return HotTopic(platform="weibo", word=word, position=1, captured_at="2026-10-12T00:00:00Z")


#: 曾漏判的真实话题（全部取自 2026-10-12 实测热点）
PREVIOUSLY_MISSED = [
    ("霸王茶姬开启买一送一大促，小程序一度崩溃，店员无奈劝退消费者", "零售号应能识别"),
    ("霸王茶姬店员劝退消费者", "零售号应能识别"),
    ("曝晋妈面馆生意惨淡", "门店经营话题"),
    ("郑钦文中网夺冠，十多个品牌集体发文祝贺", "品牌营销话题"),
]


class TestBusinessDomainExists:
    """`business` 领域必须存在——原本缺失导致经营类话题全军覆没。"""

    def test_domain_in_lexicon(self):
        assert "business" in DOMAIN_LEXICON
        assert len(DOMAIN_LEXICON["business"]) >= 20

    def test_domain_has_label(self):
        assert "business" in DOMAIN_LABELS
        assert DOMAIN_LABELS["business"] == "商业/经营"

    def test_recognizes_business_topics(self):
        for text, _ in PREVIOUSLY_MISSED:
            score = _domain_score(_normalize(text), "business")
            assert score > 0, f"business 领域没识别出: {text[:40]}"


class TestRetailDomainBroadened:
    """retail 领域必须覆盖经营维度，不只是硬件词。"""

    @pytest.mark.parametrize(
        "text",
        [
            "霸王茶姬买一送一大促崩溃",
            "某品牌门店客流爆满",
            "连锁便利店促销活动",
            "茶饮品牌加盟政策",
            "餐饮小店生意惨淡",
            "消费者投诉商家",
        ],
    )
    def test_recognizes_operational_topics(self, text):
        assert _domain_score(_normalize(text), "retail") > 0, (
            f"retail 词表漏了经营维度: {text}"
        )

    def test_still_recognizes_hardware(self):
        """补经营词不能挤掉原有硬件词。"""
        for text in ["无人咖啡机进驻写字楼", "冰淇淋机点位租金", "自动售货机补货"]:
            assert _domain_score(_normalize(text), "retail") > 0


class TestPresetProfilesCoverBothDimensions:
    """预设账号必须同时覆盖产品维度与经营维度。"""

    def test_retail_has_operational_keywords(self):
        p = get_preset("retail-operator")
        ops = ["门店", "客流", "成本", "消费", "商家", "运营"]
        missing = [k for k in ops if k not in p.keywords]
        assert not missing, f"零售号缺经营维度关键词: {missing}"

    def test_retail_has_product_keywords(self):
        p = get_preset("retail-operator")
        for k in ["咖啡机", "冰淇淋机", "点位"]:
            assert k in p.keywords, f"零售号缺产品词: {k}"

    def test_retail_includes_business_domain(self):
        p = get_preset("retail-operator")
        assert "retail" in p.domains
        assert "business" in p.domains, "零售号必须也覆盖 business 领域"

    def test_software_has_business_domain(self):
        """软件号的核心诉求之一是「获客」，本质是商业问题。"""
        p = get_preset("tech-blogger")
        assert "software" in p.domains
        assert "business" in p.domains

    def test_software_has_customer_keywords(self):
        p = get_preset("tech-blogger")
        for k in ["获客", "客户", "订单"]:
            assert k in p.keywords, f"软件号缺经营词: {k}"


class TestPreviouslyMissedTopicsNowMatch:
    """决定性测试：曾经被判 0 分的真实话题，现在必须能匹配上。"""

    @pytest.mark.parametrize("text,why", PREVIOUSLY_MISSED)
    def test_retail_now_matches(self, text, why):
        p = get_preset("retail-operator")
        m = match_topics([make_topic(text)], p)[0]
        assert m.score > 0.15, (
            f"零售号仍匹配不上「{text[:36]}」（{why}）: score={m.score}"
        )

    def test_key_article_scores_high(self):
        """那条最能做的题材必须有较高分。"""
        p = get_preset("retail-operator")
        m = match_topics(
            [make_topic("霸王茶姬开启买一送一大促，小程序一度崩溃，店员无奈劝退消费者，商家的活动组织和预案出现了哪些问题？")],
            p,
        )[0]
        assert m.score >= 0.5, f"关键题材分数偏低: {m.score}"
        assert m.level in ("strong", "maybe")

    def test_reasons_explain_hits(self):
        """理由必须指出命中了什么，方便人工复核。"""
        p = get_preset("retail-operator")
        m = match_topics([make_topic("某品牌门店客流爆满，商家紧急促销")], p)[0]
        assert m.hit_keywords, "应记录命中的业务词"
        assert any("命中" in r for r in m.reasons)


class TestNoRegressionOnIrrelevant:
    """补词典不能导致「什么都相关」。"""

    @pytest.mark.parametrize(
        "text",
        [
            "郑钦文退出武汉网球公开赛",
            "某明星官宣恋情",
            "几度秋意浓最是桂花香",
            "我国物流业景气水平进一步提升",
        ],
    )
    def test_irrelevant_still_low(self, text):
        p = get_preset("retail-operator")
        m = match_topics([make_topic(text)], p)[0]
        assert m.score < 0.3, f"无关话题得分过高: {text[:30]} → {m.score}"
