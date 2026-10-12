"""参数语义测试：`None` 与 `[]` 必须区分。

━━━ 这是一个系统性陷阱 ━━━

Python 里 `[] or DEFAULT` 会得到 `DEFAULT` —— 空列表是 falsy。
所以写成 ``sources or DEFAULT_SOURCES`` 时，**调用方显式传空列表
会被静默替换成默认源**。

后果比报错更危险：调用方以为自己在指定源，实际用了完全不同的源，
而结果看起来完全合理（有数据、结构正确）——**无从察觉**。

实测踩过的坑：最初版本正是这么写的，导致
`hot_topics(sources=[])` 返回了默认源的数据，且下面那个
「空列表检查」成了永远走不到的死代码。

这个文件从三层锁死该行为：service 参数处理、fetch_many 兜底、以及
「绝不死灰复燃」的横向检查。
"""

from __future__ import annotations

import inspect

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.hot import sources as sources_mod
from social_insight.hot.sources import DEFAULT_SOURCES, fetch_many
from social_insight.http import HttpClient
from social_insight.service import SocialInsightService


class TestHotTopicsSourcesSemantics:
    """service.hot_topics 的 sources 参数语义。"""

    @pytest.mark.asyncio
    async def test_empty_list_rejected(self):
        """显式传空列表必须报参数错误，不能静默回退。"""
        svc = SocialInsightService()
        with pytest.raises(ToolError) as ei:
            await svc.hot_topics(sources=[])
        d = ei.value.to_dict()["error"]
        assert d["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert d["alternatives"], "必须列出可用源"
        assert "空列表" in d["message"] or "空列表" in (d.get("reason") or "")
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_none_uses_default(self, monkeypatch):
        """不传（None）才使用默认源。"""
        seen: list[list[str]] = []

        async def fake_fetch(client, srcs, **kw):
            seen.append(list(srcs))
            return [], [{"source": "x", "error": "mock"}]  # 有失败 → 触发明确错误

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        svc = SocialInsightService()
        with pytest.raises(ToolError):
            await svc.hot_topics()
        assert seen and seen[0] == list(DEFAULT_SOURCES)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_explicit_list_used_verbatim(self, monkeypatch):
        """显式传的列表必须原样使用，顺序也不能变。"""
        seen: list[list[str]] = []

        async def fake_fetch(client, srcs, **kw):
            seen.append(list(srcs))
            return [], [{"source": "x", "error": "mock"}]

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        svc = SocialInsightService()
        picked = ["uapis:weibo", "douyin"]
        with pytest.raises(ToolError):
            await svc.hot_topics(sources=picked)
        assert seen and seen[0] == picked, "传入的源列表被篡改了"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_all_sources_empty_without_failure(self, monkeypatch):
        """所有源都返回空且**无失败**时，应返回空结果而非报错。

        语义区别：
          · 有失败 → 上游问题，报错（调用方该重试）
          · 无失败但为空 → 源确实没有数据，返回空结果（调用方不该重试）
        """

        async def fake_fetch(client, srcs, **kw):
            return [], []  # 无数据，也无失败

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        svc = SocialInsightService()
        res = await svc.hot_topics()
        assert res["ok"] is True
        assert res["data"]["summary"]["total"] == 0
        await svc.aclose()


class TestFetchManySemantics:
    """fetch_many 的兜底语义。"""

    @pytest.mark.asyncio
    async def test_none_uses_default(self, monkeypatch):
        seen = {}

        async def fake(client, source, *, limit):
            seen["called"] = seen.get("called", 0) + 1
            return []

        monkeypatch.setattr(sources_mod, "fetch_source", fake)
        await fetch_many(HttpClient(), None, limit_per_source=1)
        assert seen.get("called") == len(DEFAULT_SOURCES)

    @pytest.mark.asyncio
    async def test_empty_list_fetches_nothing(self, monkeypatch):
        """空列表 → 一个源都不抓（而不是抓默认源）。"""
        called = {"n": 0}

        async def fake(client, source, *, limit):
            called["n"] += 1
            return []

        monkeypatch.setattr(sources_mod, "fetch_source", fake)
        topics, failures = await fetch_many(HttpClient(), [], limit_per_source=1)
        assert topics == []
        assert failures == []
        assert called["n"] == 0, "空列表不该触发任何抓取"

    @pytest.mark.asyncio
    async def test_explicit_list_only(self, monkeypatch):
        seen = []

        async def fake(client, source, *, limit):
            seen.append(source)
            return []

        monkeypatch.setattr(sources_mod, "fetch_source", fake)
        await fetch_many(HttpClient(), ["douyin"], limit_per_source=1)
        assert seen == ["douyin"]


class TestNoFalsyFallbackRegression:
    """横向检查：源码里不该再有 `x or DEFAULT` 形式的源参数。

    这是个**结构性**检查——只要有人再写回那种形式，这个测试就会红。
    """

    def test_service_has_no_falsy_fallback(self):
        src = inspect.getsource(SocialInsightService)
        bad = [
            line.strip()
            for line in src.splitlines()
            if "sources or DEFAULT" in line
            # 注释里提到这个坑是允许的
            and not line.strip().startswith("#")
        ]
        assert not bad, f"发现 falsy 回退写法（会静默替换空列表）: {bad}"

    def test_sources_module_has_no_falsy_fallback(self):
        src = inspect.getsource(sources_mod)
        bad = [
            line.strip()
            for line in src.splitlines()
            if ("sources or DEFAULT" in line or "sources or  " in line)
            and not line.strip().startswith("#")
        ]
        assert not bad, f"fetch_many 里有 falsy 回退: {bad}"
