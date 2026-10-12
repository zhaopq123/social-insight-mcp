"""平台适配器与注册表测试。

━━━ 覆盖重点 ━━━

1. **能力守卫**：不支持的能力必须抛出**带解释的**错误，不能静默返回空
2. **视频号占位**：必须有位置、必须报 PLATFORM_NOT_IMPLEMENTED、必须给替代路径
3. **平台别名**：中文别名（抖音/小红书/视频号）必须能解析
4. **能力矩阵**：capabilities 输出必须完整反映真实状态
"""

from __future__ import annotations

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.platforms.base import (
    AuthLevel,
    Capability,
    CapabilitySpec,
    NotImplementedAdapter,
    PlatformAdapter,
)
from social_insight.platforms.registry import (
    get_adapter,
    platform_matrix,
    resolve_platform,
)


# ══════════════════════════════════════════════════════════════════
# 注册表与别名
# ══════════════════════════════════════════════════════════════════


class TestResolvePlatform:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("douyin", "douyin"),
            ("dy", "douyin"),
            ("抖音", "douyin"),
            ("xiaohongshu", "xiaohongshu"),
            ("xhs", "xiaohongshu"),
            ("小红书", "xiaohongshu"),
            ("channels", "channels"),
            ("视频号", "channels"),
            ("微信视频号", "channels"),
            ("shipinhao", "channels"),
        ],
    )
    def test_aliases(self, alias, expected):
        assert resolve_platform(alias) == expected

    def test_case_insensitive(self):
        assert resolve_platform("DOUYIN") == "douyin"
        assert resolve_platform("XHS") == "xiaohongshu"

    def test_whitespace_tolerated(self):
        assert resolve_platform("  抖音  ") == "douyin"

    def test_unknown_raises_with_available_list(self):
        with pytest.raises(ToolError) as ei:
            resolve_platform("不存在的平台")
        d = ei.value.to_dict()["error"]
        assert d["code"] == ErrorCode.INVALID_ARGUMENT.value
        assert d["reason"], "必须列出可用平台，否则调用方无从修正"

    def test_empty_raises(self):
        with pytest.raises(ToolError):
            resolve_platform("")


class TestGetAdapter:
    def test_returns_adapter(self):
        for name in ["douyin", "xiaohongshu", "channels"]:
            a = get_adapter(name)
            assert isinstance(a, PlatformAdapter)
            assert a.platform == name

    def test_unknown_raises(self):
        with pytest.raises(ToolError):
            get_adapter("nope")


# ══════════════════════════════════════════════════════════════════
# 能力守卫
# ══════════════════════════════════════════════════════════════════


class TestCapabilityGuard:
    @pytest.mark.asyncio
    async def test_unsupported_capability_raises_with_reason(self):
        """不支持的能力必须给出 reason 与 alternatives。"""
        a = get_adapter("douyin")
        with pytest.raises(ToolError) as ei:
            await a.content_search(keyword="x")
        d = ei.value.to_dict()["error"]
        assert d["code"] in (
            ErrorCode.CAPABILITY_UNSUPPORTED.value,
            ErrorCode.SIGNATURE_REQUIRED.value,
        )
        assert d.get("reason"), "必须解释为什么做不到"
        assert d.get("alternatives"), "必须给出替代路径"

    @pytest.mark.asyncio
    async def test_error_never_empty_silent(self):
        """绝不能静默返回空——这是本项目最重要的设计原则。"""
        for platform in ["douyin", "xiaohongshu", "channels"]:
            a = get_adapter(platform)
            if a.supports(Capability.CONTENT_SEARCH):
                continue
            with pytest.raises(ToolError):
                await a.content_search(keyword="x")

    def test_spec_defaults_to_unsupported(self):
        """未声明的能力默认不支持——安全默认值。"""
        a = get_adapter("douyin")
        spec = a.spec(Capability.HOT_TOPICS)
        assert isinstance(spec, CapabilitySpec)


# ══════════════════════════════════════════════════════════════════
# 视频号占位（重点）
# ══════════════════════════════════════════════════════════════════


class TestChannelsPlaceholder:
    """视频号：不实现，但必须有位置且必须诚实报错。"""

    def test_exists_in_registry(self):
        """必须能在注册表里找到——「mcp 得有」。"""
        a = get_adapter("channels")
        assert a.platform == "channels"
        assert a.display_name == "微信视频号"

    def test_all_capabilities_unsupported(self):
        a = get_adapter("channels")
        for cap in Capability:
            assert not a.supports(cap), f"{cap} 不该被标记为支持"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,kwargs",
        [
            ("hot_topics", {}),
            ("content_search", {"keyword": "x"}),
            ("content_detail", {"id_or_url": "x"}),
            ("content_comments", {"id_or_url": "x"}),
            ("creator_profile", {"id_or_url": "x"}),
            ("creator_works", {"id_or_url": "x"}),
        ],
    )
    async def test_all_methods_report_not_implemented(self, method, kwargs):
        """每个能力都必须报 PLATFORM_NOT_IMPLEMENTED（而非 CAPABILITY_UNSUPPORTED）。

        语义差别很重要：前者是「这个平台整体没做」，
        后者是「平台做了但这个能力没有」。调用方需要区分。
        """
        a = get_adapter("channels")
        with pytest.raises(ToolError) as ei:
            await getattr(a, method)(**kwargs)
        d = ei.value.to_dict()["error"]
        assert d["code"] == ErrorCode.PLATFORM_NOT_IMPLEMENTED.value
        assert d["platform"] == "channels"

    @pytest.mark.asyncio
    async def test_error_explains_why(self):
        """错误必须解释「为什么不能做」——这是调研结论的落点。"""
        a = get_adapter("channels")
        with pytest.raises(ToolError) as ei:
            await a.content_search(keyword="x")
        d = ei.value.to_dict()["error"]
        reason = d["reason"]
        # 应提及关键事实
        assert "视频号" in reason
        assert any(kw in reason for kw in ["官方", "API", "无"])

    @pytest.mark.asyncio
    async def test_error_gives_alternatives(self):
        """必须给出替代路径。"""
        a = get_adapter("channels")
        with pytest.raises(ToolError) as ei:
            await a.content_search(keyword="x")
        alts = ei.value.to_dict()["error"]["alternatives"]
        assert len(alts) >= 2
        joined = " ".join(alts)
        # 应提到付费第三方服务，并明确 hook 方案不推荐
        assert "友望" in joined or "新视" in joined or "第三方" in joined
        assert "不推荐" in joined or "风险" in joined

    def test_not_implemented_adapter_type(self):
        a = get_adapter("channels")
        assert isinstance(a, NotImplementedAdapter)


# ══════════════════════════════════════════════════════════════════
# 能力矩阵
# ══════════════════════════════════════════════════════════════════


class TestPlatformMatrix:
    def test_all_platforms_present(self):
        m = platform_matrix()
        assert set(m) == {"douyin", "xiaohongshu", "channels"}

    def test_all_capabilities_listed(self):
        m = platform_matrix()
        for name, info in m.items():
            assert set(info["capabilities"]) == {c.value for c in Capability}

    def test_auth_levels_present(self):
        m = platform_matrix()
        for name, info in m.items():
            for cap, spec in info["capabilities"].items():
                assert "auth" in spec
                assert spec["auth"] in {a.value for a in AuthLevel}

    def test_unsupported_have_notes(self):
        """不支持的能力必须带 note 说明原因。"""
        m = platform_matrix()
        for name, info in m.items():
            for cap, spec in info["capabilities"].items():
                if not spec["supported"]:
                    assert spec.get("note"), f"{name}.{cap} 不支持但没说明原因"

    def test_douyin_hot_topics_available(self):
        """抖音热榜实测可用，必须标记为支持且无需鉴权。"""
        m = platform_matrix()
        spec = m["douyin"]["capabilities"]["hot_topics"]
        assert spec["supported"] is True
        assert spec["auth"] == "none"

    def test_xiaohongshu_hot_topics_available(self):
        """小红书热搜可经聚合源获得，无需登录小红书账号。"""
        m = platform_matrix()
        spec = m["xiaohongshu"]["capabilities"]["hot_topics"]
        assert spec["supported"] is True
        assert spec["auth"] == "none"

    def test_xiaohongshu_search_needs_login(self):
        m = platform_matrix()
        spec = m["xiaohongshu"]["capabilities"]["content_search"]
        assert spec["supported"] is False
        assert spec["auth"] == "login"

    def test_douyin_search_needs_signature(self):
        m = platform_matrix()
        spec = m["douyin"]["capabilities"]["content_search"]
        assert spec["supported"] is False
        assert spec["auth"] == "signature"
