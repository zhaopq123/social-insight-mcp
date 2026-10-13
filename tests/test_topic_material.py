"""topic_material 测试：创作素材工具。

━━━ 核心契约 ━━━

1. **不返回原文全文** —— 只给 title + brief（token 黑洞防护）
2. **brief 不编造** —— 确定性压缩，无原生摘要时也不硬造
3. **query 匹配要诚实** —— 匹配不到就报错，不用泛相关内容填充
4. **空 query 与空字符串不同** —— None=不过滤，""=报错
"""

from __future__ import annotations

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.models import HotTopic
from social_insight.service import SocialInsightService


def topic(word: str, platform: str = "douyin", hot: int = 1000) -> HotTopic:
    return HotTopic(
        platform=platform, word=word, position=1, hot_value=hot,
        url=f"https://example.com/{word[:6]}",
        captured_at="2026-10-13T00:00:00Z",
    )


FAKE_TOPICS = [
    topic("开源REA三天11万星，软件开发门槛被打到零", "juejin", 9999),
    topic("郑钦文中网夺冠，霸王茶姬被流量冲崩，如何看待这波狂欢？", "weibo", 5000),
    topic("某AI大模型发布新版本", "zhihu", 3000),
]


@pytest.fixture
def svc():
    return SocialInsightService()


@pytest.fixture
def svc_with_fake(monkeypatch):
    """注入假热点，避免真实网络。"""
    s = SocialInsightService()

    async def fake_fetch(client, srcs, **kw):
        return list(FAKE_TOPICS), []

    monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
    return s


class TestBasicFlow:
    @pytest.mark.asyncio
    async def test_returns_materials(self, svc_with_fake):
        r = await svc_with_fake.topic_material()
        assert r["ok"] is True
        d = r["data"]
        assert d["count"] > 0
        first = d["materials"][0]
        # 必备字段
        for key in ("title", "brief", "source_display", "url",
                    "independent_source_count", "corroboration", "sources"):
            assert key in first, f"缺字段 {key}"

    @pytest.mark.asyncio
    async def test_no_full_text(self, svc_with_fake):
        """━━━ 铁律 1：绝不返回原文全文 ━━━

        字段里不能出现 content/full_text/article_body 之类。
        这是 token 黑洞防护，用户明确要求过。
        """
        r = await svc_with_fake.topic_material()
        blob = str(r["data"]).lower()
        for banned in ("full_text", "article_body", "content_body", "raw_content"):
            assert banned not in blob, f"出现了全文类字段 {banned}"

    @pytest.mark.asyncio
    async def test_brief_present_and_explains(self, svc_with_fake):
        """brief 要能解释话题，不能是空的。"""
        r = await svc_with_fake.topic_material()
        for m in r["data"]["materials"]:
            assert m["brief"], f"{m['title']} 的 brief 为空"
            assert m["brief_compressed"] in (True, False)

    @pytest.mark.asyncio
    async def test_usage_hint_tells_ai_what_to_do(self, svc_with_fake):
        """要让 AI 知道这些是素材不是结论。"""
        r = await svc_with_fake.topic_material()
        hint = r["data"]["usage_hint"]
        assert "素材" in hint
        assert "自行判断" in hint


class TestQueryFilter:
    @pytest.mark.asyncio
    async def test_query_filters(self, svc_with_fake):
        r = await svc_with_fake.topic_material(query="开源")
        assert r["data"]["query"] == "开源"
        assert all("开源" in m["title"] for m in r["data"]["materials"])

    @pytest.mark.asyncio
    async def test_query_case_and_punct_insensitive(self, svc_with_fake):
        """归一化匹配：忽略大小写与标点。"""
        r = await svc_with_fake.topic_material(query="rea")
        assert r["data"]["count"] >= 1, "「rea」应命中「开源REA…」（归一化）"

    @pytest.mark.asyncio
    async def test_query_no_match_errors(self, svc_with_fake):
        """━━━ 铁律 3：匹配不到就报错，不用泛相关内容填充 ━━━"""
        with pytest.raises(ToolError) as ei:
            await svc_with_fake.topic_material(query="不存在的关键词xyz")
        e = ei.value.to_dict()["error"]
        assert e["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert e["alternatives"], "必须给替代建议"

    @pytest.mark.asyncio
    async def test_empty_query_string_errors(self, svc_with_fake):
        """空字符串 ≠ None：传 "" 会静默变成不过滤，必须拦住。"""
        with pytest.raises(ToolError) as ei:
            await svc_with_fake.topic_material(query="   ")
        assert ei.value.code == ErrorCode.INVALID_ARGUMENT

    @pytest.mark.asyncio
    async def test_none_query_means_no_filter(self, svc_with_fake):
        """不传 query = 不过滤（这是合法用法）。"""
        r = await svc_with_fake.topic_material()
        assert r["data"]["query"] is None
        assert r["data"]["count"] == len(FAKE_TOPICS)


class TestBounds:
    @pytest.mark.asyncio
    async def test_huge_limit_rejected(self, svc_with_fake):
        with pytest.raises(ToolError) as ei:
            await svc_with_fake.topic_material(limit_per_source=99999)
        assert ei.value.code == ErrorCode.INVALID_ARGUMENT

    @pytest.mark.asyncio
    async def test_empty_sources_rejected(self, svc_with_fake):
        with pytest.raises(ToolError):
            await svc_with_fake.topic_material(sources=[])

    @pytest.mark.asyncio
    async def test_unknown_source_rejected(self, svc_with_fake):
        with pytest.raises(ToolError):
            await svc_with_fake.topic_material(sources=["不存在的源"])


class TestEmptyUpstream:
    @pytest.mark.asyncio
    async def test_all_sources_fail(self, svc, monkeypatch):
        async def fake_fail(*a, **kw):
            return [], [{"source": "x", "error": "boom"}]

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fail)
        with pytest.raises(ToolError) as ei:
            await svc.topic_material()
        assert ei.value.code == ErrorCode.UPSTREAM_ERROR
        assert ei.value.to_dict()["error"]["retryable"] is True


class TestSnapshotIsSaved:
    """快照必须真的保存——`_maybe_snapshot` 是 async，忘了 await 会静默失效。

    ⚠️ 实测踩过（2026-10-13）：`saved = self._maybe_snapshot(topics)`
    少了 await。Python 只发 RuntimeWarning 不报错，
    结果是**快照静默不保存**，trend 工具查不到任何历史。

    这类 bug 靠肉眼几乎看不出来（返回值是个 coroutine 对象，
    不抛异常也不为空），必须用测试锁住。
    """

    @pytest.mark.asyncio
    async def test_snapshot_actually_saved(self, tmp_path, monkeypatch):
        from social_insight.store import SnapshotStore

        store = SnapshotStore(tmp_path / "t.db")
        s = SocialInsightService(store=store)

        async def fake_fetch(client, srcs, **kw):
            return list(FAKE_TOPICS), []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        r = await s.topic_material()
        stats = store.stats()
        await s.aclose()

        assert r["ok"]
        assert stats["total_snapshots"] > 0, (
            "快照没保存——很可能 _maybe_snapshot 忘了 await"
        )
        assert r["meta"]["snapshots_saved"] > 0, "meta 里的保存数不对"

    @pytest.mark.asyncio
    async def test_snapshots_saved_is_int_not_coroutine(self, tmp_path, monkeypatch):
        """返回值必须是 int（不是 coroutine 对象）。"""
        from social_insight.store import SnapshotStore

        store = SnapshotStore(tmp_path / "t.db")
        s = SocialInsightService(store=store)

        async def fake_fetch(client, srcs, **kw):
            return list(FAKE_TOPICS), []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
        r = await s.topic_material()
        await s.aclose()

        v = r["meta"]["snapshots_saved"]
        assert isinstance(v, int), f"应为 int，实际 {type(v).__name__}（忘 await 的典型症状）"


class TestSorting:
    @pytest.mark.asyncio
    async def test_sorted_by_credibility(self, svc_with_fake):
        """多源 + 高热的应排在前面。"""
        r = await svc_with_fake.topic_material()
        items = r["data"]["materials"]
        for a, b in zip(items, items[1:]):
            assert (
                a["independent_source_count"],
                a["hot_value"],
            ) >= (
                b["independent_source_count"],
                b["hot_value"],
            ), "排序不符合「源数优先，热度次之」"
