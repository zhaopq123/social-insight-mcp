#!/usr/bin/env python3
"""服务器端全工具验收（11 个工具逐一验证）。

━━━ 与 verify_remote_mcp.py 的区别 ━━━

那个是**首轮验收**（9 个工具）；这个是**完善后的全量验收**，
覆盖新增的 trend，以及所有错误路径与边界。

每个工具都必须验证：
  · 成功路径：返回结构符合契约
  · 失败路径：返回**结构化**错误（带 reason/alternatives），不是裸异常
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://192.168.3.35:8100/mcp"
ACCEPT = "application/json, text/event-stream"


class C:
    def __init__(self):
        self.sid = None
        self.i = 0
        self.fails: list[str] = []

    def post(self, p):
        self.i += 1
        p["id"] = self.i
        h = {
            "Content-Type": "application/json",
            "Accept": ACCEPT,
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(
            BASE, data=json.dumps(p).encode(), headers=h, method="POST"
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.sid = sid
            return self.parse(r.read().decode("utf-8"))

    @staticmethod
    def parse(b):
        b = b.strip()
        if b.startswith("{"):
            return json.loads(b)
        for line in b.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None

    def call(self, name, args=None):
        d = self.post({
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
        if not d:
            self.fails.append(f"{name}: 无响应")
            return None
        if "error" in d:
            self.fails.append(f"{name}: JSON-RPC 错误 {d['error']}")
            return None
        content = d.get("result", {}).get("content", [])
        if not content:
            self.fails.append(f"{name}: 空 content")
            return None
        try:
            return json.loads(content[0]["text"])
        except (json.JSONDecodeError, KeyError):
            self.fails.append(f"{name}: content 不是 JSON")
            return None

    def expect_ok(self, name, args, check=None) -> dict | None:
        print(f"\n── {name}({json.dumps(args, ensure_ascii=False)[:60]})")
        r = self.call(name, args)
        if r is None:
            print("   ❌ 无响应")
            return None
        if not r.get("ok"):
            self.fails.append(f"{name}: 期望成功但返回错误 {r.get('error', {}).get('code')}")
            print(f"   ❌ 期望成功，得到 {r.get('error', {}).get('code')}: "
                  f"{str(r.get('error', {}).get('reason'))[:80]}")
            return None
        if check and not check(r):
            self.fails.append(f"{name}: 结构校验失败")
            print("   ❌ 结构校验失败")
            return None
        print(f"   ✅ ok  {str(r.get('data'))[:100]}...")
        return r

    def expect_error(self, name, args, codes: set[str]) -> bool:
        print(f"\n── {name}({json.dumps(args, ensure_ascii=False)[:60]}) → 期望错误")
        r = self.call(name, args)
        if r is None:
            print("   ❌ 无响应")
            return False
        if r.get("ok"):
            self.fails.append(f"{name}: 期望报错但成功了")
            print("   ❌ 期望报错但成功了")
            return False
        e = r["error"]
        ok = e["code"] in codes
        has_reason = bool(e.get("reason"))
        has_alts = bool(e.get("alternatives"))
        mark = "✅" if ok else "❌"
        if not ok:
            self.fails.append(f"{name}: 错误码 {e['code']} 不在期望 {codes}")
        print(f"   {mark} {e['code']}")
        print(f"      reason: {str(e.get('reason'))[:80]}")
        print(f"      alternatives: {str(e.get('alternatives'))[:80]}")
        # 契约要求：错误必须同时带 reason 与 alternatives ——
        # 只给其中一个是偷懒，调用方拿不到完整的补救路径
        if not has_reason:
            self.fails.append(f"{name}: 错误缺少 reason（调用方不知道为什么）")
            print("      ⚠️ 缺 reason")
        if not has_alts:
            self.fails.append(f"{name}: 错误缺少 alternatives（调用方不知道怎么补救）")
            print("      ⚠️ 缺 alternatives")
        return ok


def main() -> int:
    c = C()
    d = c.post({
        "jsonrpc": "2.0", "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "full-verify", "version": "1"}},
    })
    if not d:
        print("❌ 握手失败")
        return 1

    print("=" * 74)
    print("social-insight-mcp · 全工具服务器端验收")
    print("=" * 74)

    # ── 工具清单 ──
    lst = c.post({"jsonrpc": "2.0", "method": "tools/list"})
    tools = [t["name"] for t in lst["result"]["tools"]]
    print(f"\n服务端工具数: {len(tools)}")
    for t in sorted(tools):
        print(f"  - {t}")

    expected = {
        "capabilities", "hot_topics", "topic_match", "trend",
        "content_search", "content_detail", "content_comments",
        "creator_profile", "creator_works", "analyze_viral", "analyze_comments",
    }
    missing = expected - set(tools)
    if missing:
        c.fails.append(f"缺少工具: {missing}")
        print(f"❌ 缺少: {missing}")

    print("\n" + "=" * 74)
    print("链路 A · 热点（无需登录）")
    print("=" * 74)

    c.expect_ok("capabilities", {}, lambda r: (
        "registered_tools" in r["data"] and "storage" in r["data"]
    ))

    c.expect_ok("hot_topics", {"limit_per_source": 30}, lambda r: (
        "topics" in r["data"] and "summary" in r["data"]
    ))

    c.expect_ok("topic_match", {"profile": "小赵聊软件", "top_n": 3,
                               "limit_per_source": 30}, lambda r: (
        "matches" in r["data"] and "profile" in r["data"]
    ))

    # trend：可能第一次没有历史（服务刚重启），但必须**结构正确**
    r = c.expect_ok("trend", {"hours": 24, "limit": 5}, lambda r: "has_data" in r["data"])
    if r:
        d = r["data"]
        print(f"      has_data={d['has_data']}  count={d['count']}")
        if not d["has_data"]:
            assert d.get("note"), "无数据时必须给出说明"
            print(f"      note: {d['note'][:70]}")

    # 指定话题的 trend
    c.expect_ok("trend", {"word": "郑钦文"}, lambda r: "history" in r["data"])

    print("\n" + "=" * 74)
    print("链路 B · 平台内容（需鉴权，应诚实报错）")
    print("=" * 74)

    c.expect_error("content_search", {"platform": "channels", "keyword": "x"},
                   {"PLATFORM_NOT_IMPLEMENTED"})
    c.expect_error("content_search", {"platform": "xiaohongshu", "keyword": "x"},
                   {"CAPABILITY_UNSUPPORTED", "AUTH_REQUIRED"})
    c.expect_error("content_search", {"platform": "douyin", "keyword": "x"},
                   {"CAPABILITY_UNSUPPORTED", "SIGNATURE_REQUIRED"})
    c.expect_error("content_detail", {"platform": "channels", "id_or_url": "x"},
                   {"PLATFORM_NOT_IMPLEMENTED"})
    c.expect_error("content_comments", {"platform": "channels", "id_or_url": "x"},
                   {"PLATFORM_NOT_IMPLEMENTED"})
    c.expect_error("creator_profile", {"platform": "channels", "id_or_url": "x"},
                   {"PLATFORM_NOT_IMPLEMENTED"})
    c.expect_error("creator_works", {"platform": "channels", "id_or_url": "x"},
                   {"PLATFORM_NOT_IMPLEMENTED"})
    c.expect_error("analyze_viral", {"platform": "douyin", "keyword": "x"},
                   {"CAPABILITY_UNSUPPORTED", "SIGNATURE_REQUIRED"})
    c.expect_error("analyze_comments", {"platform": "xiaohongshu", "id_or_url": "x"},
                   {"CAPABILITY_UNSUPPORTED", "AUTH_REQUIRED"})

    print("\n" + "=" * 74)
    print("参数校验（必须报参数错误，不是上游错误）")
    print("=" * 74)

    c.expect_error("hot_topics", {"sources": ["不存在的源"]}, {"INVALID_ARGUMENT"})
    c.expect_error("hot_topics", {"sources": []}, {"INVALID_ARGUMENT"})
    c.expect_error("topic_match", {"top_n": 3}, {"INVALID_ARGUMENT"})
    c.expect_error("topic_match", {"profile": "不存在的号"}, {"INVALID_ARGUMENT"})
    c.expect_error("content_search", {"platform": "不存在的平台", "keyword": "x"},
                   {"INVALID_ARGUMENT"})

    print("\n" + "=" * 74)
    print("平台别名")
    print("=" * 74)
    for alias in ["抖音", "小红书", "视频号", "dy", "xhs"]:
        r = c.call("content_search", {"platform": alias, "keyword": "x"})
        if r and not r.get("ok") and r["error"]["code"] != "INVALID_ARGUMENT":
            print(f"   ✅ {alias} → {r['error']['code']}")
        else:
            c.fails.append(f"别名 {alias} 解析异常")
            print(f"   ❌ {alias} → {str(r)[:80]}")

    print("\n" + "=" * 74)
    if c.fails:
        print(f"❌ 失败 {len(c.fails)} 项：")
        for f in c.fails:
            print(f"   - {f}")
        return 1
    print("✅ 全工具验收通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
