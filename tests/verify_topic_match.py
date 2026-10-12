#!/usr/bin/env python3
"""topic_match 的 MCP 协议级验证（直连服务器，绕过任何客户端缓存）。

━━━ 为什么需要这个文件 ━━━

DSH 的 MCP 客户端在会话建立时固化工具表，服务端新增工具后
客户端不会自动重新 list —— 所以 ws_mcp_call 会报 unknown tool。

这个文件**直连服务端**验证，确认：
  1. 服务端确实暴露了 topic_match
  2. 通过标准 MCP 协议调用能拿到正确结果

验证通过后，只需重启 DSH host 即可让客户端重新发现。
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
        with urllib.request.urlopen(req, timeout=120) as r:
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

    def call(self, name, args):
        d = self.post(
            {"jsonrpc": "2.0", "method": "tools/call",
             "params": {"name": name, "arguments": args}}
        )
        if not d or "result" not in d:
            return None
        return json.loads(d["result"]["content"][0]["text"])


def show(label: str, res: dict | None) -> bool:
    if not res or not res.get("ok"):
        print(f"  [FAIL] {label}: {str(res)[:200]}")
        return False

    d = res["data"]
    prof = d["profile"]
    print(f"  [{label}] {prof['name']}  领域={prof['domains']}")
    print(f"     热点池 {res['meta']['raw_hot_count']} 条 → 交叉验证 {res['meta']['corroborated_count']} 个")
    print(f"     {d['summary']['total']} 个候选: "
          f"strong={d['summary']['strong']} maybe={d['summary']['maybe']} weak={d['summary']['weak']}")
    for m in d["matches"][:5]:
        print(f"       {m['score']:.2f} [{m['level']:6s}] {m['word'][:38]}")
        print(f"              └ {m['reasons'][0]}")
    return True


def main() -> int:
    c = C()
    c.post({
        "jsonrpc": "2.0", "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "tm-verify", "version": "1"}},
    })
    print("=" * 72)
    print("topic_match · MCP 协议级验证（直连，绕过客户端缓存）")
    print("=" * 72)

    # 1. 确认工具在列表里
    lst = c.post({"jsonrpc": "2.0", "method": "tools/list"})
    tools = [t["name"] for t in lst.get("result", {}).get("tools", [])]
    print(f"\n[1] 服务端工具数: {len(tools)}")
    print(f"    含 topic_match: {'topic_match' in tools}")
    if "topic_match" not in tools:
        print("    ❌ 服务端未暴露该工具")
        return 1

    # 2. 用预设账号调用
    print("\n[2] 预设账号调用")
    ok1 = show("小赵聊软件", c.call("topic_match",
                                    {"profile": "小赵聊软件", "top_n": 5,
                                     "limit_per_source": 40}))
    print()
    ok2 = show("小赵无人零售", c.call("topic_match",
                                     {"profile": "小赵无人零售", "top_n": 5,
                                      "limit_per_source": 40}))

    # 3. 临时定位调用
    print("\n[3] 临时定位调用（不依赖预设）")
    ok3 = show("自定义", c.call("topic_match",
                                {"keywords": ["咖啡机", "无人零售"],
                                 "domains": ["retail"], "top_n": 3,
                                 "limit_per_source": 40}))

    # 4. 错误路径
    print("\n[4] 错误路径")
    bad = c.call("topic_match", {"top_n": 3})
    if bad and not bad.get("ok"):
        e = bad["error"]
        good = e["code"] == "INVALID_ARGUMENT" and e.get("alternatives")
        print(f"  [{'OK' if good else 'FAIL'}] 无定位: {e['code']}")
        print(f"         提示可用预设: {e.get('alternatives')}")
        ok4 = good
    else:
        print(f"  [FAIL] 无定位没报错: {str(bad)[:150]}")
        ok4 = False

    unknown = c.call("topic_match", {"profile": "不存在的号", "top_n": 3})
    if unknown and not unknown.get("ok"):
        e = unknown["error"]
        print(f"  [OK] 未知预设: {e['code']}")
        print(f"         可用: {e.get('alternatives')}")
        ok5 = True
    else:
        print(f"  [FAIL] 未知预设没报错: {str(unknown)[:150]}")
        ok5 = False

    print("\n" + "=" * 72)
    if all([ok1, ok2, ok3, ok4, ok5]):
        print("topic_match 协议级验证全部通过")
        print("→ 服务端已就绪，重启 DSH host 后客户端即可调用")
        return 0
    print("有失败项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
