"""快照采集脚本测试。

采集脚本是 cron 调用的入口，必须：
  · 全部源失败时返回非零退出码（让调度器能发现）
  · 不因为单个源失败就整体失败
  · 支持 prune
"""

from __future__ import annotations

import pytest

from social_insight import snapshot as snap_mod
from social_insight.models import HotTopic
from social_insight.snapshot import run_once


def make_topic(word: str, platform: str = "douyin") -> HotTopic:
    return HotTopic(platform=platform, word=word, position=1, captured_at="2026-10-12T00:00:00Z")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "snap.db")


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_saves_snapshots(self, db_path, monkeypatch):
        async def fake_fetch(client, srcs, **kw):
            return [make_topic("话题A"), make_topic("话题B")], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        r = await run_once(db_path=db_path, quiet=True)
        assert r["ok"] is True
        assert r["saved"] == 2
        assert r["stats"]["total_snapshots"] == 2

    @pytest.mark.asyncio
    async def test_partial_failure_still_saves(self, db_path, monkeypatch):
        """部分源失败仍应成功——不该因为一个源挂了就整体失败。"""

        async def fake_fetch(client, srcs, **kw):
            return [make_topic("话题A")], [{"source": "x", "error": "挂了"}]

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        r = await run_once(db_path=db_path, quiet=True)
        assert r["ok"] is True
        assert r["saved"] == 1
        assert len(r["failures"]) == 1

    @pytest.mark.asyncio
    async def test_total_failure_returns_not_ok(self, db_path, monkeypatch):
        """全部源失败 → ok=False（退出码非零，调度器能发现）。"""

        async def fake_fetch(client, srcs, **kw):
            return [], [{"source": "x", "error": "全挂"}]

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        r = await run_once(db_path=db_path, quiet=True)
        assert r["ok"] is False
        assert r["saved"] == 0

    @pytest.mark.asyncio
    async def test_prune(self, db_path, monkeypatch):
        async def fake_fetch(client, srcs, **kw):
            return [make_topic("话题A")], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        r = await run_once(db_path=db_path, prune_days=30, quiet=True)
        assert "pruned" in r
        assert r["pruned"] == 0  # 新数据不该被删

    @pytest.mark.asyncio
    async def test_accumulates_across_runs(self, db_path, monkeypatch):
        """多次采集应累积——这是趋势数据的基础。"""

        async def fake_fetch(client, srcs, **kw):
            return [make_topic("持续话题")], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        for _ in range(3):
            await run_once(db_path=db_path, quiet=True)

        r = await run_once(db_path=db_path, quiet=True)
        assert r["stats"]["total_snapshots"] == 4
        assert r["stats"]["distinct_topics"] == 1  # 同一话题

    @pytest.mark.asyncio
    async def test_result_shape(self, db_path, monkeypatch):
        async def fake_fetch(client, srcs, **kw):
            return [make_topic("话题")], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        r = await run_once(db_path=db_path, quiet=True)
        for key in ("ok", "at", "sources", "fetched", "saved", "failures", "stats"):
            assert key in r, f"结果缺少 {key}"
