#!/usr/bin/env python3
"""MCP 协议级端到端验证（从本机连服务器）。

━━━ 验证内容 ━━━

1. initialize 握手
2. tools/list —— 确认工具注册完整
3. tools/call —— 真实调用每个工具，验证返回结构
4. 错误路径 —— 视频号占位、无效参数

这是**最终验收**：不是「代码能跑」，而是「作为 MCP 服务能被客户端正常使用」。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

BASE = "http://192.168.3.35:8100/mcp"

#: MCP 要求客户端声明能接受这两种类型
ACCEPT = "application/json, text/event-stream"


class McpClient:
    """极简 MCP streamable-http 客户端（够用即可，不引依赖）。"""

    def __init__(self, url: str):
        self.url = url
        self.session_id: str | None = None
        self._req_id = 0

    def _post(self, payload: dict) -> tuple[int, dict | None, str]:
        self._req_id += 1
        payload["id"] = self._req_id
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": ACCEPT,
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                body = resp.read().decode("utf-8")
                return resp.status, self._parse(body), body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return e.code, self._parse(body), body

    @staticmethod
    def _parse(body: str) -> dict | None:
        """解析响应。streamable-http 可能返回 SSE 格式（data: {...}）。"""
        body = body.strip()
        if not body:
            return None
        if body.startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return None
        # SSE 格式
        for line in body.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    continue
        return None

    def initialize(self) -> tuple[bool, str]:
        status, data, raw = self._post(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "verify-client", "version": "1.0"},
                },
            }
        )
        if status != 200 or not data:
            return False, f"HTTP {status}: {raw[:200]}"
        result = data.get("result", {})
        info = result.get("serverInfo", {})
        return True, f"{info.get('name')} v{info.get('version')} (protocol {result.get('protocolVersion')})"

    def notify_initialized(self) -> None:
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> tuple[bool, list[dict]]:
        status, data, raw = self._post({"jsonrpc": "2.0", "method": "tools/list"})
        if status != 200 or not data:
            return False, []
        return True, data.get("result", {}).get("tools", [])

    def call_tool(self, name: str, args: dict) -> tuple[bool, Any]:
        status, data, raw = self._post(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
        if status != 200 or not data:
            return False, f"HTTP {status}: {raw[:300]}"
        if "error" in data:
            return False, data["error"]
        content = data.get("result", {}).get("content", [])
        if not content:
            return False, "空 content"
        text = content[0].get("text", "")
        try:
            return True, json.loads(text)
        except json.JSONDecodeError:
            return True, text


def main() -> int:
    failures: list[str] = []
    print("=" * 72)
    print(f"MCP 协议验证 · {BASE}")
    print("=" * 72)

    c = McpClient(BASE)

    # ---- 1. 握手 ----
    ok, info = c.initialize()
    print(f"\n[1] initialize: {'OK' if ok else 'FAIL'}  {info}")
    if not ok:
        print("\n无法完成握手，终止。")
        return 1
    c.notify_initialized()
    print(f"    session: {c.session_id}")

    # ---- 2. 工具列表 ----
    ok, tools = c.list_tools()
    print(f"\n[2] tools/list: {'OK' if ok else 'FAIL'}  共 {len(tools)} 个工具")
    if not ok:
        failures.append("tools/list 失败")
    expected = {
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
    got = {t["name"] for t in tools}
    missing = expected - got
    if missing:
        failures.append(f"缺少工具: {missing}")
    for t in tools:
        print(f"    - {t['name']:20s} {t.get('description', '')[:52]}...")

    # ---- 3. capabilities ----
    print(f"\n[3] tools/call: capabilities")
    ok, res = c.call_tool("capabilities", {})
    if ok and isinstance(res, dict) and res.get("ok"):
        d = res["data"]
        print(f"    OK  链路={list(d['chains'])}  热点源={len(d['hot_sources'])}  平台={list(d['platforms'])}")
        if "channels" not in d["platforms"]:
            failures.append("capabilities 里没有视频号")
    else:
        failures.append(f"capabilities 失败: {str(res)[:120]}")
        print(f"    FAIL {str(res)[:200]}")

    # ---- 4. hot_topics（核心能力）----
    print(f"\n[4] tools/call: hot_topics（核心能力）")
    ok, res = c.call_tool("hot_topics", {"limit_per_source": 30, "min_sources": 1})
    if ok and isinstance(res, dict) and res.get("ok"):
        d = res["data"]
        s = d["summary"]
        print(f"    OK  话题={s['total']}  confirmed={s['confirmed']}  likely={s['likely']}")
        fails = res.get("meta", {}).get("failures", [])
        print(f"    失败源={len(fails)}")
        for t in d["topics"][:5]:
            print(f"      [{t['source_count']}源] {t['word'][:46]}")
            print(f"             来源: {', '.join(t['sources'])}")
        if s["total"] == 0:
            failures.append("hot_topics 返回 0 条")
    else:
        failures.append(f"hot_topics 失败: {str(res)[:120]}")
        print(f"    FAIL {str(res)[:200]}")

    # ---- 5. 视频号占位 ----
    print(f"\n[5] tools/call: 视频号占位（必须诚实报错）")
    ok, res = c.call_tool("content_search", {"platform": "channels", "keyword": "测试"})
    if ok and isinstance(res, dict) and not res.get("ok"):
        e = res["error"]
        good = e["code"] == "PLATFORM_NOT_IMPLEMENTED" and e.get("alternatives")
        print(f"    {'OK' if good else 'FAIL'}  {e['code']}")
        print(f"        reason: {e.get('reason', '')[:70]}")
        print(f"        替代路径 {len(e.get('alternatives', []))} 条")
        if not good:
            failures.append("视频号占位错误结构不完整")
    else:
        failures.append(f"视频号没有报错: {str(res)[:120]}")
        print(f"    FAIL {str(res)[:200]}")

    # ---- 6. 小红书需登录 ----
    print(f"\n[6] tools/call: 小红书（必须说明需要登录）")
    ok, res = c.call_tool("content_search", {"platform": "xiaohongshu", "keyword": "咖啡"})
    if ok and isinstance(res, dict) and not res.get("ok"):
        e = res["error"]
        good = e["code"] in ("CAPABILITY_UNSUPPORTED", "AUTH_REQUIRED") and e.get("reason")
        print(f"    {'OK' if good else 'FAIL'}  {e['code']}")
        print(f"        reason: {e.get('reason', '')[:70]}")
        if not good:
            failures.append("小红书错误缺少说明")
    else:
        failures.append(f"小红书没有报错: {str(res)[:120]}")
        print(f"    FAIL {str(res)[:200]}")

    # ---- 7. 无效参数 ----
    print(f"\n[7] tools/call: 无效源名（必须报参数错误）")
    ok, res = c.call_tool("hot_topics", {"sources": ["不存在的源"]})
    if ok and isinstance(res, dict) and not res.get("ok"):
        e = res["error"]
        good = e["code"] == "INVALID_ARGUMENT" and e.get("alternatives")
        print(f"    {'OK' if good else 'FAIL'}  {e['code']}")
        print(f"        可用源 {len(e.get('alternatives', []))} 个")
        if not good:
            failures.append("无效参数错误结构不完整")
    else:
        failures.append(f"无效参数没有报错: {str(res)[:120]}")
        print(f"    FAIL {str(res)[:200]}")

    # ---- 8. 中文别名 ----
    print(f"\n[8] tools/call: 中文平台别名")
    for alias in ["抖音", "视频号"]:
        ok, res = c.call_tool("content_search", {"platform": alias, "keyword": "x"})
        if ok and isinstance(res, dict) and not res.get("ok"):
            code = res["error"]["code"]
            print(f"    OK  {alias} -> {code}")
        else:
            failures.append(f"别名 {alias} 解析异常")
            print(f"    FAIL {alias} -> {str(res)[:100]}")

    # ---- 汇总 ----
    print("\n" + "=" * 72)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("MCP 协议验证全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
