"""用户视角测试：从「使用者能不能做成事」出发，而不是「代码对不对」。

━━━ 与单元测试的区别 ━━━

单元测试验证逻辑正确；这里验证**产品行为**：

  U1. 默认输出要「少而准」——不该把 200 条各站榜单丢给用户
  U2. single 话题必须折叠并有说明——不能跟真热点混在一起
  U3. 排序必须可比——热度值跨源不可比，不能参与排序
  U4. 每条要有 brief——用户不该读长问句标题
  U5. 缺参数要给人能懂的错误——不能是框架原文
  U6. 输出体积可控——不能一次吐 25KB

━━━ 用法 ━━━

这些测试走**真实 service 层**（不含 HTTP），
需要网络——标记为 integration，默认跳过：

    pytest tests/test_ux.py -m integration -v
"""

from __future__ import annotations

import os

import pytest

# ⚠️ **不要**在模块级打 pytestmark = integration。
#    实测踩过：那样会把不需要网络的本地测试也一起 skip——
#    而 TestFriendlyErrors 恰恰是覆盖率最高、最该常跑的部分。
#    只给需要真实网络的类单独打标记（见 TestUserExperience）。
RUN_LIVE = os.environ.get("RUN_LIVE", "") == "1"


@pytest.fixture
def svc():
    if not RUN_LIVE:
        pytest.skip("需要真实网络：设 RUN_LIVE=1 才跑")
    from social_insight.service import SocialInsightService

    return SocialInsightService()


@pytest.mark.integration
@pytest.mark.asyncio
class TestUserExperience:
    """用户视角：拿到结果能不能直接用。

    需要真实网络（抓热榜源）——RUN_LIVE=1 才跑。
    """

    async def test_u1_output_is_small_and_precise(self, svc):
        """U1：默认输出应该聚焦在高质量话题，不是全量榜单。"""
        r = await svc.hot_topics(limit_per_source=50)
        await svc.aclose()

        d = r["data"]
        strong = d["topics"]
        single_count = d["also_on_single"]["count"]

        # 主列表不该被 single 淹没
        assert single_count > len(strong) or single_count == 0, (
            "意料之外的数据形态"
        )
        # 关键：主列表条数应该远小于总数（说明真的筛过了）
        total_raw = r["meta"]["raw_count"]
        assert len(strong) <= total_raw * 0.5, (
            f"主列表 {len(strong)} 条占原始 {total_raw} 条的 "
            f"{len(strong)/total_raw:.0%}——筛选没起作用，用户还要自己挑"
        )

    async def test_u2_single_has_explanation(self, svc):
        """U2：single 话题必须带说明，让用户知道为什么放在次要位置。"""
        r = await svc.hot_topics(limit_per_source=50)
        await svc.aclose()

        blk = r["data"]["also_on_single"]
        if blk["count"] > 0:
            assert blk["note"], "single 区块缺说明——用户不知道为什么被降级"
            assert "单一来源" in blk["note"] or "榜单" in blk["note"]
            # single 项也该有 brief（最小信息集）
            for it in blk["items"][:5]:
                assert it.get("brief"), "single 项缺 brief"

    async def test_u3_sort_is_comparable(self, svc):
        """U3：排序必须按「独立源数 → 排名」，不能按跨源热度值。"""
        r = await svc.hot_topics(limit_per_source=50)
        await svc.aclose()

        items = r["data"]["topics"]
        for a, b in zip(items, items[1:]):
            # 源数必须不递增
            assert a["source_count"] >= b["source_count"], (
                f"排序错乱：{a['source_count']} 后面是 {b['source_count']}"
            )
            # 同源数内按 best_position 升序
            if a["source_count"] == b["source_count"]:
                assert a["best_position"] <= b["best_position"], (
                    "同源数内未按最佳排名升序"
                )
        # max_hot_value 不该出现在主列表（跨源不可比，误导用户）
        assert "max_hot_value" not in items[0], (
            "主列表把跨源不可比的热度值暴露给用户了——会被误导"
        )

    async def test_u4_every_topic_has_brief(self, svc):
        """U4：每条热点都要有 brief，用户不该读长问句。"""
        r = await svc.hot_topics(limit_per_source=50)
        await svc.aclose()

        for t in r["data"]["topics"]:
            assert t.get("brief"), f"「{t['word']}」缺 brief"
            assert t.get("brief_compressed") in (True, False)

    async def test_u6_output_size_bounded(self, svc):
        """U6：输出体积要可控——不能一次吐几十 KB 把上下文撑爆。"""
        import json

        r = await svc.hot_topics(limit_per_source=50)
        await svc.aclose()

        size = len(json.dumps(r, ensure_ascii=False))
        assert size < 60_000, (
            f"输出 {size/1024:.1f} KB，太大——AI 读完上下文就炸了"
        )

    async def test_topic_material_same_quality(self, svc):
        """topic_material 也要满足同样的质量标准。"""
        r = await svc.topic_material(top_n=10, limit_per_source=30)
        await svc.aclose()

        for m in r["data"]["materials"]:
            assert m["brief"], "缺 brief"
            assert m["independent_source_count"] >= 1
        # 排序同样可比
        counts = [m["independent_source_count"] for m in r["data"]["materials"]]
        assert counts == sorted(counts, reverse=True), "topic_material 排序错乱"


class TestStrictSchema:
    """U7：tools/list 必须对严格模型友好（本地测试，常跑）。

    ━━━ 背景（2026-10-13 实测）━━━
    GLM-5.3-Flash / Deepseek-V4.1-Flash / Step-5-Preview 逐字校验
    工具 schema，命中 outputSchema 或 anyOf 直接拒整个请求
    （11129 "The tool definition does not meet the model requirements"）。
    fastmcp 默认会自动生成这两样——所以每次改工具都要防回归。
    """

    def _tools(self):
        import asyncio
        import json as _json

        from social_insight.http_server import build_app
        from social_insight.service import SocialInsightService

        app = build_app(SocialInsightService(store=None))
        return asyncio.run(app.list_tools()), _json

    def test_no_output_schema(self):
        """任何工具都不能带 outputSchema——严格模型见到就拒载。"""
        tools, _ = self._tools()
        offenders = [t.name for t in tools if getattr(t, "output_schema", None) is not None]
        assert not offenders, f"这些工具还带着 outputSchema：{offenders}"

    def test_no_anyof(self):
        """inputSchema 里不能有 anyOf（Optional 参数已被压平）。"""
        tools, json = self._tools()
        for t in tools:
            blob = json.dumps(t.parameters, ensure_ascii=False)
            assert "anyOf" not in blob, f"{t.name} 的 inputSchema 还含 anyOf"

    def test_optional_param_flattened_not_dropped(self):
        """压平后参数还得在——不能为了兼容把参数弄丢了。"""
        tools, _ = self._tools()
        by_name = {t.name: t for t in tools}
        cs = by_name["content_search"].parameters["properties"]
        # 这些原本是 `str | None = None`，压平后必须仍是 string 且仍带 default
        for p in ("sort", "time_range", "cursor"):
            assert p in cs, f"{p} 参数丢了"
            assert cs[p].get("type") == "string", f"{p} 类型不对：{cs[p]}"
        # platform/keyword 不带 required 标记——「必填」由 _need 门
        # 在运行时用中文错误兜底（见 TestFriendlyErrors），不靠 schema 拒人
        assert "required" not in by_name["content_search"].parameters, (
            "content_search 不该在 schema 层标 required——那会弹出 pydantic 原文"
        )

    def test_tool_count_stable(self):
        """工具数量是产品决策（宁缺勿滥），意外增减要被发现。"""
        tools, _ = self._tools()
        assert len(tools) == 12, f"工具数变了：{len(tools)} 个（预期 12）"

    def test_stdio_path_schema_clean(self):
        """stdio 路径（server.py 手写 schema）同样不能有 outputSchema/anyOf。"""
        import json as _json

        from social_insight.server import TOOL_DEFS

        for t in TOOL_DEFS:
            assert "outputSchema" not in t, f"{t['name']} 声明了 outputSchema"
            blob = _json.dumps(t["inputSchema"], ensure_ascii=False)
            assert "anyOf" not in blob, f"{t['name']} 的 inputSchema 含 anyOf"


class TestFriendlyErrors:
    """U5：错误必须人能读懂——不能是框架原文。

    这些是**纯本地**测试（不起服务、不抓网络），
    直接构造 build_app 打协议层。
    """

    def _call(self, tool: str, args: dict) -> dict:
        """经 FastMCP 协议层调一次工具，返回信封。

        ━━━ 为什么起真服务而不是进程内 ASGI ━━━

        试过进程内 `httpx.ASGITransport(app=mcp.http_app())`，失败：
            TypeError: 'FastMCP' object is not callable
            → 要先 `.http_app()`（build_app 返回的是 FastMCP 对象）

        再试又失败：
            RuntimeError: Task group is not initialized
            → FastMCP 的 ASGI app 依赖 lifespan，而这个版本
              （4.1.0）没有导出 LifespanManager，只有 combine_lifespans。

        所以改用**真实 uvicorn + 随机端口**——这也更好：
        测的就是用户实际走的链路（HTTP + streamable），
        不是「测试专用捷径」。
        """
        import contextlib
        import json
        import socket
        import threading

        import uvicorn

        from social_insight.http_server import build_app

        mcp = build_app()
        app = mcp.http_app(path="/mcp")

        # 找一个空闲端口
        with contextlib.closing(socket.socket()) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        config = uvicorn.Config(app, host="127.0.0.1", port=port,
                                log_level="error", lifespan="on")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            # 等它就绪
            for _ in range(100):
                if server.started:
                    break
                import time
                time.sleep(0.1)

            import urllib.request

            sid = [None]

            def post(payload):
                h = {"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"}
                if sid[0]:
                    h["Mcp-Session-Id"] = sid[0]
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/mcp",
                    data=json.dumps(payload).encode(), headers=h, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r:
                    new = r.headers.get("Mcp-Session-Id")
                    if new:
                        sid[0] = new
                    return r.read().decode()

            def parse(body):
                body = body.strip()
                if body.startswith("{"):
                    return json.loads(body)
                for line in body.splitlines():
                    if line.startswith("data:"):
                        return json.loads(line[5:].strip())
                return json.loads(body)

            post({"jsonrpc": "2.0", "method": "initialize", "id": 1,
                  "params": {"protocolVersion": "2025-06-18",
                             "capabilities": {},
                             "clientInfo": {"name": "ux", "version": "1"}}})
            post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            body = post({"jsonrpc": "2.0", "method": "tools/call", "id": 9,
                         "params": {"name": tool, "arguments": args}})
            return parse(body)
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    def test_u5_missing_required_param_is_friendly(self):
        """🔴 核心验收：缺必填参数不能返回 pydantic 原文。

        实测踩过（2026-10-13）：缺 keyword 时返回

            1 validation error for call[content_search]
            keyword  Missing required argument [type=missing_argument...]

        这是框架内部细节，用户（创作者）看到只会懵。
        """
        d = self._call("content_search", {"platform": "douyin"})
        assert "result" in d, f"协议层就报错了：{d}"
        text = d["result"]["content"][0]["text"]
        assert not d["result"].get("isError"), (
            f"仍被标记为错误：{text[:200]}"
        )
        import json
        env = json.loads(text)
        assert env["ok"] is False
        assert env["error"]["code"] == "INVALID_ARGUMENT"
        # 必须是中文、可读、带补救建议
        assert "必填参数" in env["error"]["message"]
        assert env["error"]["reason"]
        assert env["error"]["alternatives"]
        # 绝不能出现框架术语
        for banned in ("validation error", "pydantic", "Missing required argument"):
            assert banned not in text, f"泄露了框架原文：{banned}"

    def test_u5_missing_both_params(self):
        """两个必填都缺时也要友好。"""
        d = self._call("creator_profile", {})
        assert "result" in d
        import json
        text = d["result"]["content"][0]["text"]
        env = json.loads(text)
        assert env["ok"] is False
        assert "platform" in env["error"]["message"]
        for banned in ("validation error", "pydantic"):
            assert banned not in text

    def test_u5_analyze_viral_needs_one_of_two(self):
        """keyword 与 creator 二选一，都要说清楚。"""
        d = self._call("analyze_viral", {"platform": "douyin"})
        assert "result" in d
        import json
        text = d["result"]["content"][0]["text"]
        env = json.loads(text)
        assert env["ok"] is False
        assert "keyword" in env["error"]["message"]
        assert "creator" in env["error"]["message"]
