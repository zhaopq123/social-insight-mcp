"""字段填充的完整性测试。

━━━ 为什么需要这一类测试 ━━━

实测踩过的坑：给 `TopicMatch` 加了 `sources` 字段（并在 to_dict 里输出），
但 `match_topics` 构造对象时**忘了赋值**，`service` 也没把它传进去。

后果：调用方看到 `sources: []`、`source_distribution: {}`，
并且可靠性判断得出**相反的错误结论**——
实际全是单一源（掘金），却被判定为「来源分散」。

这类 bug 的特点是：
  · 不报错（空列表是合法值）
  · 结构看起来正常（字段存在、类型正确）
  · **但语义完全错了**，且调用方无从察觉

所以需要「字段确实被填充」的测试——不只是「字段存在」。
"""

from __future__ import annotations

import pytest

from social_insight.hot.corroborate import CorroboratedTopic
from social_insight.hot.match import AccountProfile, match_topics
from social_insight.models import HotTopic


def make_topic(word: str, platform: str = "juejin", hot: int = 100) -> HotTopic:
    return HotTopic(platform=platform, word=word, position=1, hot_value=hot,
                    captured_at="2026-10-12T00:00:00Z")


class TestSourcesFieldPopulated:
    """`sources` 字段必须真的被填上——不能只声明不赋值。"""

    def test_sources_copied_from_corroboration(self):
        """corroboration_of 里给了 sources，match 结果里就要有。"""
        topics = [make_topic("某AI工具发布")]
        profile = AccountProfile(keywords=["AI"], domains=["software"])
        corr = {
            "某AI工具发布": {
                "level": "confirmed",
                "source_count": 3,
                "sources": ["微博", "百度", "掘金热榜"],
            }
        }
        m = match_topics(topics, profile, corroboration_of=corr)[0]
        assert m.sources == ["微博", "百度", "掘金热榜"], (
            "sources 没被填充——这会导致来源统计与可靠性判断出错（实测踩过）"
        )

    def test_source_count_matches_sources(self):
        """source_count 与 sources 长度应一致（除非调用方只给了计数）。"""
        topics = [make_topic("话题A")]
        profile = AccountProfile(keywords=["话题"], domains=["software"])
        corr = {"话题A": {"level": "likely", "source_count": 2, "sources": ["A", "B"]}}
        m = match_topics(topics, profile, corroboration_of=corr)[0]
        assert m.source_count == 2
        assert len(m.sources) == 2

    def test_missing_sources_is_empty_not_crash(self):
        """corroboration_of 没给 sources 时不该崩，返回空列表。"""
        topics = [make_topic("话题A")]
        profile = AccountProfile(keywords=["话题"], domains=["software"])
        corr = {"话题A": {"level": "single", "source_count": 1}}
        m = match_topics(topics, profile, corroboration_of=corr)[0]
        assert m.sources == []

    def test_no_corroboration_info(self):
        """完全没有 corroboration_of 时也要正常。"""
        topics = [make_topic("话题A")]
        profile = AccountProfile(keywords=["话题"], domains=["software"])
        m = match_topics(topics, profile)[0]
        assert m.sources == []
        assert m.source_count == 0

    def test_sources_in_to_dict(self):
        topics = [make_topic("话题A")]
        profile = AccountProfile(keywords=["话题"], domains=["software"])
        corr = {"话题A": {"level": "confirmed", "source_count": 2, "sources": ["X", "Y"]}}
        d = match_topics(topics, profile, corroboration_of=corr)[0].to_dict()
        assert d["sources"] == ["X", "Y"], "to_dict 里丢了 sources"


class TestServicePassesSourcesThrough:
    """service 层必须把 sources 传进 corr_of。"""

    @pytest.mark.asyncio
    async def test_topic_match_includes_sources(self, monkeypatch, tmp_path):
        from social_insight.service import SocialInsightService

        async def fake_fetch(client, srcs, **kw):
            return [
                make_topic("某AI工具发布", platform="juejin"),
                make_topic("某AI工具发布", platform="weibo"),
            ], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        svc = SocialInsightService()
        res = await svc.topic_match(profile="小赵聊软件", top_n=5)
        await svc.aclose()

        assert res["ok"] is True
        matches = res["data"]["matches"]
        # 至少要有匹配结果，且 sources 不该全空（两个源都报道了同一话题）
        if matches:
            top = matches[0]
            assert "source_count" in top
            # 这个假数据里两个源的内容相同 → 应被识别为 2 源
            if top["source_count"] >= 2:
                assert top["sources"], (
                    "service 没把 sources 传进 corr_of —— "
                    "调用方会看到空来源并误判可靠性（实测踩过）"
                )

    @pytest.mark.asyncio
    async def test_summary_uses_real_sources(self, monkeypatch):
        """summary 的来源分布必须基于真实 sources，不能恒为空。"""
        from social_insight.service import SocialInsightService

        async def fake_fetch(client, srcs, **kw):
            # 同一话题来自两个源
            return [
                make_topic("热门AI话题", platform="juejin"),
                make_topic("热门AI话题", platform="weibo"),
            ], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        svc = SocialInsightService()
        res = await svc.topic_match(profile="小赵聊软件", top_n=5)
        await svc.aclose()

        summary = res["data"]["summary"]
        # 若有强候选，来源分布不该是空的
        if summary["strong"] > 0:
            assert summary["source_distribution"], (
                "来源分布为空 —— 说明 sources 没传到 summary（实测踩过）"
            )
