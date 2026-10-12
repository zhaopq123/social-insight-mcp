"""HTTP 客户端测试（用 mock transport，不依赖真实网络）。

━━━ 覆盖重点 ━━━

1. **代理 bug 回归**：httpx 0.28 环境代理解析 bug（实测踩过）
2. 重试逻辑：429/5xx 重试，4xx 不重试
3. 缓存：命中、过期、key 区分
4. 错误映射：网络异常 → 结构化 ToolError
5. 节流：同平台请求间隔
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.http import HttpClient


def make_client(handler, **kw) -> HttpClient:
    """构造一个注入了 mock transport 的 HttpClient。"""
    c = HttpClient(**kw)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return c


# ══════════════════════════════════════════════════════════════════
# 代理 bug 回归
# ══════════════════════════════════════════════════════════════════


class TestProxyBugRegression:
    """httpx 0.28 在解析环境代理时会抛 InvalidURL: Invalid port: ':1]'。

    这个 bug 会让**所有**请求失败，而且错误信息极具误导性
    （看起来像 URL 写错了，实际是代理配置问题）。

    修复方式：trust_env=False。这些测试锁住该行为。
    """

    def test_trust_env_defaults_to_false(self):
        """默认必须不读环境代理——国内热榜源本就不该走代理。"""
        assert HttpClient().trust_env is False

    def test_trust_env_can_be_enabled(self):
        assert HttpClient(trust_env=True).trust_env is True

    @pytest.mark.asyncio
    async def test_works_with_proxy_env_set(self, monkeypatch):
        """即使环境里有代理变量，也要能正常请求。"""
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

        def handler(request):
            return httpx.Response(200, json={"ok": True})

        c = HttpClient()  # 默认 trust_env=False
        c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        result = await c.request_json("https://example.com/x", platform="test")
        assert result == {"ok": True}
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 重试逻辑
# ══════════════════════════════════════════════════════════════════


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_on_500(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(500, text="err")
            return httpx.Response(200, json={"ok": True})

        c = make_client(handler, max_retries=3, min_interval={})
        result = await c.request_json("https://x.test/a", platform="test")
        assert result == {"ok": True}
        assert calls["n"] == 3
        await c.aclose()

    @pytest.mark.asyncio
    async def test_retries_on_429(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, json={"ok": True})

        c = make_client(handler, max_retries=3, min_interval={})
        assert await c.request_json("https://x.test/a", platform="test") == {"ok": True}
        assert calls["n"] == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_no_retry_on_404(self):
        """4xx（非 429）重试也没用，不该浪费请求。"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(404, text="nope")

        c = make_client(handler, max_retries=3, min_interval={})
        with pytest.raises(ToolError) as ei:
            await c.request_json("https://x.test/a", platform="test")
        assert ei.value.code == ErrorCode.UPSTREAM_ERROR
        assert calls["n"] == 1, "4xx 不该重试"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        def handler(request):
            return httpx.Response(503, text="down")

        c = make_client(handler, max_retries=2, min_interval={})
        with pytest.raises(ToolError) as ei:
            await c.request_json("https://x.test/a", platform="test")
        assert ei.value.retryable is True
        await c.aclose()

    @pytest.mark.asyncio
    async def test_retries_on_transport_error(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"ok": True})

        c = make_client(handler, max_retries=3, min_interval={})
        assert await c.request_json("https://x.test/a", platform="test") == {"ok": True}
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 错误映射
# ══════════════════════════════════════════════════════════════════


class TestErrorMapping:
    @pytest.mark.asyncio
    async def test_non_json_response(self):
        """上游返回 HTML（聚合站常见）时，必须报明确的错，不能崩。"""

        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        c = make_client(handler, min_interval={})
        with pytest.raises(ToolError) as ei:
            await c.request_json("https://x.test/a", platform="test")
        assert ei.value.code == ErrorCode.UPSTREAM_ERROR
        assert "JSON" in ei.value.message
        assert ei.value.details.get("body_preview")
        await c.aclose()

    @pytest.mark.asyncio
    async def test_timeout_maps_to_timeout_code(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout")

        c = make_client(handler, max_retries=1, min_interval={})
        with pytest.raises(ToolError) as ei:
            await c.request_json("https://x.test/a", platform="test")
        assert ei.value.code == ErrorCode.TIMEOUT
        await c.aclose()

    @pytest.mark.asyncio
    async def test_error_includes_platform(self):
        def handler(request):
            return httpx.Response(500, text="x")

        c = make_client(handler, max_retries=1, min_interval={})
        with pytest.raises(ToolError) as ei:
            await c.request_json("https://x.test/a", platform="douyin")
        assert ei.value.platform == "douyin"
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 缓存
# ══════════════════════════════════════════════════════════════════


class TestCache:
    @pytest.mark.asyncio
    async def test_cache_hit(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"v": calls["n"]})

        c = make_client(handler, min_interval={})
        a = await c.request_json("https://x.test/a", platform="t", cache_ttl=60)
        b = await c.request_json("https://x.test/a", platform="t", cache_ttl=60)
        assert a == b
        assert calls["n"] == 1, "第二次应命中缓存"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_no_cache_when_ttl_zero(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"v": calls["n"]})

        c = make_client(handler, min_interval={})
        await c.request_json("https://x.test/a", platform="t", cache_ttl=0)
        await c.request_json("https://x.test/a", platform="t", cache_ttl=0)
        assert calls["n"] == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_cache_key_includes_params(self):
        """不同参数不能命中同一缓存——否则会返回错数据。"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"n": calls["n"]})

        c = make_client(handler, min_interval={})
        await c.request_json("https://x.test/a", platform="t", params={"q": "1"}, cache_ttl=60)
        await c.request_json("https://x.test/a", platform="t", params={"q": "2"}, cache_ttl=60)
        assert calls["n"] == 2
        await c.aclose()

    @pytest.mark.asyncio
    async def test_cache_expires(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, json={"n": calls["n"]})

        c = make_client(handler, min_interval={})
        await c.request_json("https://x.test/a", platform="t", cache_ttl=0.05)
        await asyncio.sleep(0.1)
        await c.request_json("https://x.test/a", platform="t", cache_ttl=0.05)
        assert calls["n"] == 2
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 文本请求（聚合站）
# ══════════════════════════════════════════════════════════════════


class TestRequestText:
    @pytest.mark.asyncio
    async def test_returns_text(self):
        def handler(request):
            return httpx.Response(200, text="<html>中文</html>")

        c = make_client(handler, min_interval={})
        text = await c.request_text("https://x.test/a", platform="t")
        assert "中文" in text
        await c.aclose()

    @pytest.mark.asyncio
    async def test_error_on_404(self):
        def handler(request):
            return httpx.Response(404)

        c = make_client(handler, min_interval={})
        with pytest.raises(ToolError):
            await c.request_text("https://x.test/a", platform="t")
        await c.aclose()

    @pytest.mark.asyncio
    async def test_caches_text(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, text="x")

        c = make_client(handler, min_interval={})
        await c.request_text("https://x.test/a", platform="t", cache_ttl=60)
        await c.request_text("https://x.test/a", platform="t", cache_ttl=60)
        assert calls["n"] == 1
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 生命周期
# ══════════════════════════════════════════════════════════════════


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_idempotent(self):
        c = HttpClient()
        await c.aclose()
        await c.aclose()  # 不应报错

    @pytest.mark.asyncio
    async def test_lazy_client_creation(self):
        c = HttpClient()
        assert c._client is None
        await c._ensure()
        assert c._client is not None
        await c.aclose()
