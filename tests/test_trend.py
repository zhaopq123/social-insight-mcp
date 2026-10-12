"""trend 工具的 service 层测试。

━━━ 核心原则 ━━━

「没有历史」和「没有趋势」是**两件不同的事**，不能混淆。
trend 必须明确告诉调用方「是还没采集」而不是「这个话题没趋势」。
"""

from __future__ import annotations

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.models import HotTopic
from social_insight.service import SocialInsightService
from social_insight.store import SnapshotStore


def make_topic(word: str, platform: str = "douyin", hot: int | None = None) -> HotTopic:
    return HotTopic(
        platform=platform,
        word=word,
        position=1,
        hot_value=hot,
        captured_at="2026-10-12T00:00:00Z",
    )


@pytest.fixture
def svc_with_store(tmp_path):
    store = SnapshotStore(tmp_path / "t.db")
    svc = SocialInsightService(store=store)
    yield svc
    svc.store.close()


@pytest.fixture
def svc_no_store():
    return SocialInsightService(store=None)


class TestNoStorage:
    """未启用存储时必须明确报错，不能静默返回空。"""

    @pytest.mark.asyncio
    async def test_reports_clearly(self, svc_no_store):
        with pytest.raises(ToolError) as ei:
            await svc_no_store.trend()
        d = ei.value.to_dict()["error"]
        assert d["code"] == ErrorCode.CAPABILITY_UNSUPPORTED.value
        assert d["reason"]
        assert d["alternatives"], "必须告诉调用方怎么启用"

    @pytest.mark.asyncio
    async def test_word_mode_also_reports(self, svc_no_store):
        with pytest.raises(ToolError):
            await svc_no_store.trend(word="某话题")


class TestTrendWord:
    @pytest.mark.asyncio
    async def test_has_history(self, svc_with_store):
        svc_with_store.store.save_hot_topics([make_topic("话题A", hot=100)])
        svc_with_store.store.save_hot_topics([make_topic("话题A", hot=200)])

        res = await svc_with_store.trend(word="话题A")
        assert res["ok"] is True
        d = res["data"]
        assert d["has_data"] is True
        assert d["count"] == 2
        assert [h["hot_value"] for h in d["history"]] == [100, 200]

    @pytest.mark.asyncio
    async def test_no_history_is_explicit(self, svc_with_store):
        """没有历史必须明确说「尚无快照」，不能假装「无趋势」。"""
        res = await svc_with_store.trend(word="从未采集的话题")
        d = res["data"]
        assert d["has_data"] is False
        assert d["count"] == 0
        assert "尚无历史快照" in d["note"]


class TestTrendList:
    @pytest.mark.asyncio
    async def test_ranked_by_persistence(self, svc_with_store):
        for _ in range(3):
            svc_with_store.store.save_hot_topics([make_topic("持续话题")])
        svc_with_store.store.save_hot_topics([make_topic("一次话题")])

        res = await svc_with_store.trend()
        d = res["data"]
        assert d["has_data"] is True
        assert d["topics"][0]["word"] == "持续话题"

    @pytest.mark.asyncio
    async def test_empty_is_explicit(self, svc_with_store):
        res = await svc_with_store.trend()
        d = res["data"]
        assert d["has_data"] is False
        assert "无快照" in d["note"]

    @pytest.mark.asyncio
    async def test_meta_has_stats(self, svc_with_store):
        """meta 里带存储概况，便于诊断。"""
        svc_with_store.store.save_hot_topics([make_topic("话题")])
        res = await svc_with_store.trend()
        assert "stats" in res["meta"]


class TestSnapshotIntegration:
    """hot_topics 应自动落快照。"""

    @pytest.mark.asyncio
    async def test_snapshots_saved_in_meta(self, svc_with_store, monkeypatch):
        """验证 hot_topics 会写快照（用假抓取避免依赖网络）。"""

        async def fake_fetch(client, sources=None, **kw):
            return [make_topic("话题A"), make_topic("话题B")], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        res = await svc_with_store.hot_topics()
        assert res["meta"]["snapshots_saved"] == 2
        assert res["meta"]["storage_enabled"] is True

    @pytest.mark.asyncio
    async def test_storage_disabled_flag(self, svc_no_store, monkeypatch):
        async def fake_fetch(client, sources=None, **kw):
            return [make_topic("话题A")], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        res = await svc_no_store.hot_topics()
        assert res["meta"]["storage_enabled"] is False
        assert res["meta"]["snapshots_saved"] == 0

    @pytest.mark.asyncio
    async def test_storage_failure_does_not_break(self, tmp_path, monkeypatch):
        """存储失败不该让热点查询失败——快照是增值功能。"""
        store = SnapshotStore(tmp_path / "t.db")
        svc = SocialInsightService(store=store)

        async def fake_fetch(client, sources=None, **kw):
            return [make_topic("话题A")], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        # 让存储写入必然失败（删表）
        with store._tx() as c:
            c.execute("DROP TABLE hot_snapshots")

        res = await svc.hot_topics()
        assert res["ok"] is True, "存储失败不该影响主流程"
        svc.store.close()
