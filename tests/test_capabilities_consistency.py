"""capabilities 与工具注册表的一致性测试。

━━━ 为什么需要这些测试 ━━━

实测踩过的坑：早期版本 capabilities 里宣传了 `topic_match` /
`compare_creators` / `trend` 三个工具，但它们**并未注册**。

后果：调用方（LLM）照着 capabilities 的列表去调用不存在的工具，
收到「未知工具」错误 —— **宣传不存在的能力比不宣传更糟**，
因为它制造了虚假的可用性预期。

这些测试从结构上锁死「capabilities 说的 = 实际注册的」。
"""

from __future__ import annotations

import pytest

from social_insight.server import TOOL_DEFS
from social_insight.service import SocialInsightService


@pytest.fixture
def svc():
    return SocialInsightService()


class TestCapabilitiesConsistency:
    """capabilities 声明的工具必须全部真实存在。"""

    @pytest.mark.asyncio
    async def test_chain_tools_are_registered(self, svc):
        """chains 里列出的每个工具都必须已注册。"""
        registered = {t["name"] for t in TOOL_DEFS}
        res = await svc.capabilities()
        for chain_name, chain in res["data"]["chains"].items():
            for tool in chain["tools"]:
                assert tool in registered, (
                    f"{chain_name} 宣传了未注册的工具 '{tool}' —— "
                    f"调用方会收到「未知工具」错误"
                )
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_registered_tools_matches_defs(self, svc):
        """registered_tools 字段必须与 TOOL_DEFS 完全一致。"""
        res = await svc.capabilities()
        assert res["data"]["registered_tools"] == sorted(t["name"] for t in TOOL_DEFS)
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_planned_tools_not_in_registered(self, svc):
        """planned_tools 与 registered_tools 必须互斥。"""
        res = await svc.capabilities()
        registered = set(res["data"]["registered_tools"])
        planned = set(res["data"]["planned_tools"])
        overlap = registered & planned
        assert not overlap, f"这些工具同时出现在两个列表里: {overlap}"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_planned_tools_explained(self, svc):
        """计划中的工具必须有说明——不能只给个名字。"""
        res = await svc.capabilities()
        for name, desc in res["data"]["planned_tools"].items():
            assert desc, f"计划工具 {name} 缺少说明"
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_notes_warn_about_planned(self, svc):
        """notes 里必须明确警告不要调用计划中的工具。"""
        res = await svc.capabilities()
        notes = " ".join(res["data"]["notes"])
        assert "planned_tools" in notes or "尚未注册" in notes, (
            "必须警告调用方不要调用未注册的工具"
        )
        await svc.aclose()


class TestToolDefsIntegrity:
    """工具定义本身的结构完整性。"""

    def test_no_duplicate_names(self):
        names = [t["name"] for t in TOOL_DEFS]
        assert len(names) == len(set(names)), "工具名重复"

    def test_all_have_description(self):
        for t in TOOL_DEFS:
            assert t.get("description"), f"{t['name']} 缺 description"
            assert len(t["description"]) > 20, f"{t['name']} 的 description 太短，LLM 无法理解"

    def test_all_have_schema(self):
        for t in TOOL_DEFS:
            schema = t.get("inputSchema")
            assert schema, f"{t['name']} 缺 inputSchema"
            assert schema.get("type") == "object"
            assert "properties" in schema

    def test_required_fields_exist(self):
        for t in TOOL_DEFS:
            schema = t["inputSchema"]
            props = set(schema.get("properties", {}))
            for req in schema.get("required", []):
                assert req in props, f"{t['name']}: required '{req}' 不在 properties 里"

    def test_all_params_described(self):
        """每个参数都必须有 description —— LLM 靠它理解怎么填。"""
        for t in TOOL_DEFS:
            for pname, pspec in t["inputSchema"].get("properties", {}).items():
                assert pspec.get("description"), f"{t['name']}.{pname} 缺 description"
                assert pspec.get("type"), f"{t['name']}.{pname} 缺 type"

    def test_expected_tools_present(self):
        """核心工具必须在场。"""
        names = {t["name"] for t in TOOL_DEFS}
        required = {
            "capabilities",
            "hot_topics",
            "content_search",
            "content_detail",
            "content_comments",
            "creator_profile",
            "creator_works",
            "analyze_viral",
            "analyze_comments",
        }
        missing = required - names
        assert not missing, f"缺少核心工具: {missing}"
