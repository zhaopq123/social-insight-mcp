#!/usr/bin/env python3
"""多轮压力与稳定性测试（打真实服务）。

━━━ 与单元测试的区别 ━━━

单元测试验证**逻辑正确**；这个脚本验证**服务在真实负载下的行为**：

  轮次 A · 并发      —— 同时打多个请求，看是否串数据/超时/崩溃
  轮次 B · 一致性    —— 同一请求重复多次，结果必须稳定（确定性算法）
  轮次 C · 边界参数  —— 极端参数经协议层传入，看错误是否仍然结构化
  轮次 D · 持续运行  —— 连续多轮调用，观察快照累积与内存表现

用法::

    SOCIAL_INSIGHT_MCP_URL=http://host:8100/mcp python tests/stress_rounds.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("SOCIAL_INSIGHT_MCP_URL", "http://127.0.0.1:8100/mcp")
ACCEPT = "application/json, text/event-stream"


def call_tool(name: str, args: dict | None = None, *, timeout: int = 180) -> tuple[bool, object, float]:
    """调一次工具。返回 (成功, 结果, 耗时秒)。

    每次调用都建新会话——模拟多个独立客户端。
    """
    sid: str | None = None
    t0 = time.time()

    def post(payload: dict) -> dict | None:
        nonlocal sid
        headers = {
            "Content-Type": "application/json",
            "Accept": ACCEPT,
            "MCP-Protocol-Version": "2025-06-18",
        }
        if sid:
            headers["Mcp-Session-Id"] = sid
        req = urllib.request.Request(
            BASE, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            s = r.headers.get("Mcp-Session-Id")
            if s:
                sid = s
            return _parse(r.read().decode("utf-8"))

    try:
        post({
            "jsonrpc": "2.0", "method": "initialize", "id": 1,
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "stress", "version": "1"}},
        })
        d = post({
            "jsonrpc": "2.0", "method": "tools/call", "id": 2,
            "params": {"name": name, "arguments": args or {}},
        })
        if not d or "result" not in d:
            return False, d, time.time() - t0
        content = d["result"].get("content", [])
        if not content:
            return False, "empty content", time.time() - t0
        return True, json.loads(content[0]["text"]), time.time() - t0
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:100]}", time.time() - t0


def _parse(body: str) -> dict | None:
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return None


def hdr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  ❌ {msg}")


# ══════════════════════════════════════════════════════════════════
# 轮次 A · 并发
# ══════════════════════════════════════════════════════════════════


def round_a() -> None:
    hdr("轮次 A · 并发（同时打 6 个请求，看是否串数据/超时）")

    jobs = [
        ("capabilities", {}),
        ("hot_topics", {"limit_per_source": 20}),
        ("topic_match", {"profile": "tech-blogger", "top_n": 3, "limit_per_source": 20}),
        ("topic_match", {"profile": "retail-operator", "top_n": 3, "limit_per_source": 20}),
        ("trend", {"hours": 24, "limit": 5}),
        ("hot_topics", {"limit_per_source": 15, "min_sources": 2}),
    ]

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(call_tool, n, a) for n, a in jobs]
        results = [(jobs[i][0], f.result()) for i, f in enumerate(futures)]

    ok_count = 0
    for name, (ok, res, dt) in results:
        if ok and isinstance(res, dict) and res.get("ok"):
            ok_count += 1
            print(f"  ✅ {name:18s} {dt:5.1f}s")
        else:
            fail(f"并发调用 {name} 失败: {str(res)[:100]}")
            print(f"  ❌ {name:18s} {dt:5.1f}s  {str(res)[:80]}")

    print(f"\n  成功 {ok_count}/{len(jobs)}")
    if ok_count < len(jobs):
        fail(f"并发有 {len(jobs) - ok_count} 个失败")

    # 数据串号检查：两个不同账号的结果必须不同
    tech = next((r for n, (ok, r, _) in results if n == "topic_match" and ok
                 and isinstance(r, dict) and r.get("data", {}).get("profile", {}).get("domains", [""])[0] == "software"), None)
    retail = next((r for n, (ok, r, _) in results if n == "topic_match" and ok
                   and isinstance(r, dict) and "retail" in r.get("data", {}).get("profile", {}).get("domains", [])), None)
    if tech and retail:
        tw = {m["word"] for m in tech["data"]["matches"]}
        rw = {m["word"] for m in retail["data"]["matches"]}
        if tw == rw:
            fail("并发下两个账号返回了相同结果——可能串了会话数据")
        else:
            print(f"  ✅ 两账号结果不同（tech={len(tw)} 条, retail={len(rw)} 条）")


# ══════════════════════════════════════════════════════════════════
# 轮次 B · 一致性
# ══════════════════════════════════════════════════════════════════


def round_b() -> None:
    hdr("轮次 B · 一致性（同一请求重复 5 次，确定性算法结果应稳定）")

    # 用固定输入——注意热点本身会变，所以对比的是**结构**与**排序性质**
    sigs: list[str] = []
    for i in range(5):
        ok, res, dt = call_tool("topic_match",
                                {"profile": "tech-blogger", "top_n": 5,
                                 "limit_per_source": 20})
        if not ok or not isinstance(res, dict) or not res.get("ok"):
            fail(f"第 {i+1} 次调用失败")
            continue
        d = res["data"]
        matches = d["matches"]
        # 检查排序性质（确定性算法必须保持降序）
        scores = [m["score"] for m in matches]
        if scores != sorted(scores, reverse=True):
            fail(f"第 {i+1} 次结果未按分数降序: {scores}")
        # 检查分数范围不变式
        for m in matches:
            if not (0.0 <= m["score"] <= 1.0):
                fail(f"分数越界: {m['score']}")
        sigs.append(f"{len(matches)}|{scores[0]:.3f}|{d['summary']['reliability']}")
        print(f"  第{i+1}次  {dt:4.1f}s  候选={len(matches)}  最高分={scores[0]:.3f}  "
              f"可靠性={d['summary']['reliability']}")

    if len(set(sigs)) > 1:
        print(f"\n  ⚠️ 5 次结果有差异（热点本身在变，属正常）：{set(sigs)}")
    else:
        print("\n  ✅ 5 次结果结构完全一致")

    # 幂等性：同一时刻的两次调用，归一化后的候选集应高度重合
    ok1, r1, _ = call_tool("topic_match", {"profile": "tech-blogger", "top_n": 10,
                                           "limit_per_source": 50})
    ok2, r2, _ = call_tool("topic_match", {"profile": "tech-blogger", "top_n": 10,
                                           "limit_per_source": 50})
    if ok1 and ok2 and isinstance(r1, dict) and isinstance(r2, dict):
        w1 = [m["word"] for m in r1["data"]["matches"]]
        w2 = [m["word"] for m in r2["data"]["matches"]]
        overlap = len(set(w1) & set(w2))
        print(f"  连续两次调用的候选重合：{overlap}/{max(len(w1), 1)}")
        if overlap < max(1, len(w1) // 2):
            fail(f"两次调用重合度过低（{overlap}）——可能有非确定性缺陷")


# ══════════════════════════════════════════════════════════════════
# 轮次 C · 协议层边界参数
# ══════════════════════════════════════════════════════════════════


def round_c() -> None:
    hdr("轮次 C · 边界参数（经协议层传入极端值，错误必须仍结构化）")

    cases = [
        ("hot_topics", {"limit_per_source": 0}, None, "每源 0 条"),
        ("hot_topics", {"limit_per_source": 99999}, None, "极大 limit"),
        ("hot_topics", {"sources": ["不存在的源"]}, "INVALID_ARGUMENT", "无效源名"),
        ("hot_topics", {"sources": []}, "INVALID_ARGUMENT", "空源列表"),
        ("topic_match", {"top_n": 0}, None, "top_n=0"),
        ("topic_match", {"top_n": 9999}, None, "极大 top_n"),
        ("topic_match", {}, "INVALID_ARGUMENT", "无定位"),
        ("topic_match", {"profile": "不存在的账号"}, "INVALID_ARGUMENT", "未知账号"),
        ("topic_match", {"keywords": [], "domains": []}, "INVALID_ARGUMENT", "空关键词"),
        ("topic_match", {"profile": "tech-blogger", "form": "乱写"}, None, "非法 form"),
        ("trend", {"hours": -1}, None, "负小时"),
        ("trend", {"word": ""}, None, "空话题名"),
        ("trend", {"word": "x" * 5000}, None, "超长话题名"),
        ("content_search", {"platform": "不存在", "keyword": "x"}, "INVALID_ARGUMENT", "未知平台"),
        ("content_search", {"platform": "channels", "keyword": ""}, "PLATFORM_NOT_IMPLEMENTED", "空关键词+视频号"),
    ]

    for name, args, expect_code, desc in cases:
        ok, res, dt = call_tool(name, args)
        if not ok:
            fail(f"[{desc}] 调用失败: {str(res)[:80]}")
            print(f"  ❌ {desc:24s} 协议层失败")
            continue

        if not isinstance(res, dict):
            fail(f"[{desc}] 返回非 JSON")
            continue

        if res.get("ok"):
            # 成功路径
            if expect_code is not None:
                fail(f"[{desc}] 期望错误 {expect_code} 但成功了")
                print(f"  ❌ {desc:24s} 期望错误却成功")
            else:
                print(f"  ✅ {desc:24s} ok  {dt:4.1f}s")
        else:
            e = res.get("error", {})
            code = e.get("code")
            has_reason = bool(e.get("reason"))
            has_alts = bool(e.get("alternatives"))
            if expect_code and code != expect_code:
                fail(f"[{desc}] 错误码 {code} ≠ 期望 {expect_code}")
                print(f"  ❌ {desc:24s} {code}")
            elif not (has_reason or has_alts):
                fail(f"[{desc}] 错误缺少 reason 与 alternatives")
                print(f"  ❌ {desc:24s} {code}（无说明）")
            else:
                print(f"  ✅ {desc:24s} {code}")


# ══════════════════════════════════════════════════════════════════
# 轮次 D · 持续运行
# ══════════════════════════════════════════════════════════════════


def round_d(rounds: int = 8) -> None:
    hdr(f"轮次 D · 持续运行（连续 {rounds} 轮调用，观察延迟与快照累积）")

    latencies: list[float] = []
    errors = 0

    for i in range(rounds):
        ok, res, dt = call_tool("hot_topics", {"limit_per_source": 30})
        latencies.append(dt)
        if ok and isinstance(res, dict) and res.get("ok"):
            saved = res.get("meta", {}).get("snapshots_saved", 0)
            print(f"  第{i+1}轮  {dt:5.1f}s  话题={res['data']['summary']['total']:3d}  "
                  f"落快照={saved:3d}")
        else:
            errors += 1
            fail(f"第 {i+1} 轮失败: {str(res)[:80]}")
            print(f"  第{i+1}轮  {dt:5.1f}s  失败")

    if latencies:
        print(f"\n  延迟: 中位 {statistics.median(latencies):.1f}s  "
              f"最小 {min(latencies):.1f}s  最大 {max(latencies):.1f}s")
        # 延迟不该随时间显著恶化（外推的持久性检查）
        first_half = statistics.mean(latencies[: max(1, len(latencies) // 2)])
        second_half = statistics.mean(latencies[len(latencies) // 2 :])
        if second_half > first_half * 2.5:
            fail(f"延迟显著恶化：前半 {first_half:.1f}s → 后半 {second_half:.1f}s")
        else:
            print(f"  稳定性: 前半 {first_half:.1f}s → 后半 {second_half:.1f}s（正常）")

    if errors:
        fail(f"持续运行有 {errors} 轮失败")

    # 快照层健康
    ok, res, _ = call_tool("trend", {"hours": 24, "limit": 3})
    if ok and isinstance(res, dict) and res.get("ok"):
        d = res["data"]
        print(f"\n  快照层: has_data={d['has_data']}  话题数={d['count']}")
        if not d["has_data"]:
            print("  （尚无快照——刚重启后属正常，多次采集后应有）")
    else:
        fail(f"trend 调用失败: {str(res)[:80]}")


# ══════════════════════════════════════════════════════════════════


def main() -> int:
    print("=" * 78)
    print(f"多轮压力与稳定性测试 · {BASE}")
    print("=" * 78)

    t0 = time.time()
    try:
        round_a()
        round_b()
        round_c()
        round_d()
    except KeyboardInterrupt:
        print("\n中断")
        return 130

    hdr("汇总")
    print(f"总耗时 {time.time() - t0:.0f}s")
    if FAILS:
        print(f"\n❌ 共 {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("\n✅ 全部轮次通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
