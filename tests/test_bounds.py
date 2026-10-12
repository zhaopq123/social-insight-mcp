"""参数边界校验测试。

━━━ 实测发现的资源耗尽漏洞 ━━━

多轮压测（2026-10-12）暴露：

    hot_topics(limit_per_source=99999)

让 10 个源**各抓 99999 条** —— 服务 CPU 打到 49%，
单次响应从 <1s 恶化到 **17.8s**，请求超时。

**参数没有上限 = 资源耗尽漏洞。**
而且这个漏洞不会报错，只会让服务悄悄变慢直到不可用。

所以所有数值参数都必须在**抓取之前**校验边界。
"""

from __future__ import annotations

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.service import BOUNDS, SocialInsightService, _check_bounds


@pytest.fixture
def svc():
    return SocialInsightService()


# ══════════════════════════════════════════════════════════════════
# _check_bounds 单元
# ══════════════════════════════════════════════════════════════════


class TestCheckBounds:
    @pytest.mark.parametrize(
        "name,value,expect_ok",
        [
            ("limit_per_source", 1, True),
            ("limit_per_source", 50, True),
            ("limit_per_source", 200, True),
            ("limit_per_source", 0, False),
            ("limit_per_source", -1, False),
            ("limit_per_source", 201, False),
            ("limit_per_source", 99999, False),   # 实测漏洞的触发值
            ("top_n", 0, True),
            ("top_n", 100, True),
            ("top_n", 101, False),
            ("min_sources", 1, True),
            ("min_sources", 0, False),
            ("min_sources", 11, False),
            ("hours", 1, True),
            ("hours", 720, True),
            ("hours", 0, False),
            ("hours", -5, False),
            ("hours", 10000, False),
        ],
    )
    def test_bounds(self, name, value, expect_ok):
        if expect_ok:
            assert _check_bounds(name, value) == value
        else:
            with pytest.raises(ToolError) as ei:
                _check_bounds(name, value)
            assert ei.value.code == ErrorCode.INVALID_ARGUMENT

    def test_non_integer_rejected(self):
        for bad in ["abc", None, [], {}]:
            with pytest.raises(ToolError) as ei:
                _check_bounds("top_n", bad)
            assert ei.value.code == ErrorCode.INVALID_ARGUMENT

    def test_float_string_accepted(self):
        """字符串数字可以接受（MCP 传输可能给字符串）。"""
        assert _check_bounds("top_n", "5") == 5

    def test_error_explains_why(self):
        """超上限的错误必须说明「为什么要上限」。"""
        with pytest.raises(ToolError) as ei:
            _check_bounds("limit_per_source", 99999)
        d = ei.value.to_dict()["error"]
        assert d["reason"], "必须解释原因"
        assert "资源" in d["reason"] or "上限" in d["reason"]
        assert d["details"]["max"] == 200
        assert d["details"]["received"] == 99999

    def test_all_bounds_have_description(self):
        """每个边界都要有中文说明——错误消息要用。"""
        for name, (lo, hi, desc) in BOUNDS.items():
            assert desc, f"{name} 缺说明"
            assert lo < hi, f"{name} 范围颠倒"


# ══════════════════════════════════════════════════════════════════
# service 层：边界必须在抓取之前拦截
# ══════════════════════════════════════════════════════════════════


class TestBoundsEnforcedBeforeFetch:
    """这些参数越界必须**立即**报错，不能真去抓取。"""

    @pytest.mark.asyncio
    async def test_hot_topics_huge_limit(self, svc, monkeypatch):
        """实测漏洞的触发值——必须被拦住。"""
        called = {"n": 0}

        async def fake_fetch(*a, **kw):
            called["n"] += 1
            return [], []

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        with pytest.raises(ToolError) as ei:
            await svc.hot_topics(limit_per_source=99999)
        assert ei.value.code == ErrorCode.INVALID_ARGUMENT
        assert called["n"] == 0, "越界参数不该触发任何抓取（这是漏洞的本质）"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_hot_topics_zero_limit(self, svc, monkeypatch):
        async def fake_fetch(*a, **kw):
            raise AssertionError("不该抓取")

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
        with pytest.raises(ToolError):
            await svc.hot_topics(limit_per_source=0)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_hot_topics_huge_min_sources(self, svc, monkeypatch):
        async def fake_fetch(*a, **kw):
            raise AssertionError("不该抓取")

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
        with pytest.raises(ToolError):
            await svc.hot_topics(min_sources=99)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_topic_match_huge_top_n(self, svc, monkeypatch):
        async def fake_fetch(*a, **kw):
            raise AssertionError("不该抓取")

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
        with pytest.raises(ToolError):
            await svc.topic_match(profile="tech-blogger", top_n=9999)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_trend_negative_hours(self, svc):
        """trend 的 hours 越界也要拦住（它不抓取，但会查出错误结果）。"""
        with pytest.raises(ToolError) as ei:
            await svc.trend(hours=-5)
        assert ei.value.code == ErrorCode.INVALID_ARGUMENT
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_valid_params_still_work(self, svc, monkeypatch):
        """边界内的正常值不能被误伤。"""

        async def fake_fetch(*a, **kw):
            # 带一个失败源 → 会抛 UPSTREAM_ERROR（而非参数错误）
            return [], [{"source": "x", "error": "mock"}]

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)
        with pytest.raises(ToolError) as ei:
            await svc.hot_topics(limit_per_source=200)
        assert ei.value.code == ErrorCode.UPSTREAM_ERROR, (
            f"合法参数被误判为非法: {ei.value.code}"
        )
        await svc.aclose()


class TestRealisticValues:
    """实际会用的值必须在范围内——边界不能设得太紧。"""

    @pytest.mark.asyncio
    async def test_typical_values_allowed(self, svc, monkeypatch):
        async def fake_fetch(*a, **kw):
            return [], [{"source": "x", "error": "mock"}]

        monkeypatch.setattr("social_insight.service.fetch_many", fake_fetch)

        # 这些是文档里推荐的常用值，都必须合法
        for limit in (20, 30, 50, 100):
            with pytest.raises(ToolError) as ei:
                await svc.hot_topics(limit_per_source=limit)
            assert ei.value.code == ErrorCode.UPSTREAM_ERROR, (
                f"常用值 {limit} 被误判为越界"
            )
        await svc.aclose()
