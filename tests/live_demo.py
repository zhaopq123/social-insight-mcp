#!/usr/bin/env python3
"""实测演示：把 topic_match 的真实结果摊开看。

不是验证脚本，是**给人看的**——展示这个工具实际能给出什么，
以及它的判断依据（命中词 / 领域 / 交叉验证级别）。

调用的是服务器真实接口（MCP 协议），不是本地模拟。
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://192.168.3.35:8100/mcp"
ACCEPT = "application/json, text/event-stream"


class C:
    def __init__(self) -> None:
        self.sid = None
        self.i = 0

    def post(self, p: dict) -> dict | None:
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
    def parse(b: str) -> dict | None:
        b = b.strip()
        if b.startswith("{"):
            return json.loads(b)
        for line in b.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None

    def call(self, name: str, args: dict | None = None):
        d = self.post({
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
        if not d or "result" not in d:
            return None
        return json.loads(d["result"]["content"][0]["text"])


def bar(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def show_account(c: C, profile: str, top_n: int = 12) -> list[dict]:
    bar(f"【{profile}】选题候选（真实调用）")
    r = c.call("topic_match", {"profile": profile, "top_n": top_n, "limit_per_source": 50})
    if not r or not r.get("ok"):
        print("  调用失败:", str(r)[:200])
        return []

    d = r["data"]
    s = d["summary"]
    print(f"热点池 {r['meta']['raw_hot_count']} 条 → 交叉验证 "
          f"{r['meta']['corroborated_count']} 个 → 匹配后 {s['total']} 个")
    print(f"强相关={s['strong']}  勉强={s['maybe']}  不相关={s['weak']}")
    print()
    print(f"{'分数':>5} {'级别':<7} {'交叉验证':<10} 话题")
    print("-" * 78)
    for m in d["matches"]:
        ce = m.get("corroboration") or "-"
        ce_txt = f"{ce}({m.get('source_count', 0)}源)" if ce != "-" else "-"
        print(f"{m['score']:>5.2f} {m['level']:<7} {ce_txt:<10} {m['word'][:44]}")
        if m["reasons"]:
            print(f"      └ {m['reasons'][0]}")
            for extra in m["reasons"][1:2]:
                print(f"        {extra}")
    return d["matches"]


def main() -> int:
    c = C()
    c.post({
        "jsonrpc": "2.0", "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "live-demo", "version": "1"}},
    })
    print("=" * 78)
    print("topic_match 实测演示 · 直连服务器 192.168.3.35:8100")
    print("=" * 78)

    software = show_account(c, "小赵聊软件")
    retail = show_account(c, "小赵无人零售")

    # ---- 对比分析 ----
    bar("两个账号的对比")
    s_words = {m["word"] for m in software}
    r_words = {m["word"] for m in retail}
    overlap = s_words & r_words
    print(f"软件号候选: {len(s_words)} 个")
    print(f"零售号候选: {len(r_words)} 个")
    print(f"重合: {len(overlap)} 个")
    if overlap:
        for w in list(overlap)[:5]:
            print(f"   · {w[:60]}")

    # ---- 最强组合：既火又相关 ----
    bar("最佳选题（既被多源验证、又匹配账号定位）")
    best = [m for m in software + retail
            if m.get("corroboration") in ("confirmed", "likely") and m["score"] >= 0.3]
    if best:
        for m in sorted(best, key=lambda x: -x["score"]):
            src = f"{m['corroboration']}({m['source_count']}源)"
            print(f"  {m['score']:.2f}  [{src}] {m['word'][:48]}")
            if m["reasons"]:
                print(f"          {m['reasons'][0]}")
    else:
        print("  （今天没有同时满足「多源验证 + 强相关」的话题）")
        print("  这本身是有用的信息：说明今天的全网热点与你的号都不太搭，")
        print("  硬蹭不如按自己的常规选题节奏走。")

    # ---- 交叉验证的强信号 ----
    bar("今日全网强信号（confirmed，≥3 独立源）")
    hot = c.call("hot_topics", {"limit_per_source": 50, "min_sources": 3})
    if hot and hot.get("ok"):
        for t in hot["data"]["topics"][:12]:
            print(f"  [{t['source_count']}源] {t['word'][:52]}")
            print(f"         来源: {', '.join(t['sources'])}")
    else:
        print("  查询失败")

    return 0


if __name__ == "__main__":
    sys.exit(main())
