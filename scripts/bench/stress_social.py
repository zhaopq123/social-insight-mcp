#!/usr/bin/env python3
"""social-insight-mcp 压力测试 · 多会话 × 多并发。

按对上游的打扰程度递增设计场景：
  proto   capabilities   —— 纯 MCP 协议 + 内存态，不碰上游/DB
  local   trend          —— 本地 sqlite 读（WAL）
  hot     hot_topics     —— 真实上游（有 300-600s URL 缓存 + single-flight 保护）
  mixed   四工具混合      —— 模拟真实多 agent 使用形态

每个并发 worker 持有**独立 MCP 会话**（各自 initialize + notifications/initialized），
模拟多客户端/多 agent 同时使用的真实形态 —— 单会话串发测不出会话间干扰。

关键指标：p50/p90/p95/p99/max、RPS、错误率、各工具分位。
用法：./venv/bin/python scripts/bench/stress_social.py --scenario mixed -c 20 -d 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import httpx

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "stress", "version": "1"},
    },
}


class McpSession:
    """一个独立的 MCP 客户端会话（含 initialize 握手）。"""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.sid: str | None = None
        self._id = 0

    async def start(self) -> None:
        r = await self.client.post("/mcp", headers=MCP_HEADERS, json=INIT_PAYLOAD)
        r.raise_for_status()
        self.sid = r.headers.get("mcp-session-id")
        if not self.sid:
            raise RuntimeError(f"initialize 无 session id: {r.text[:200]}")
        await self.client.post(
            "/mcp",
            headers={**MCP_HEADERS, "Mcp-Session-Id": self.sid},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    async def call(self, name: str, arguments: dict) -> None:
        """调用工具。

        ⚠️ ReadError 重试一次：服务端 keep-alive 超时（uvicorn 默认 5s）
        关闭空闲连接后，客户端复用死连接会 read error。这是**客户端侧
        必须处理的正常现象**（真实 MCP 客户端同样会遇到），不是服务故障。
        只对连接级错误重试，且只重一次。
        """
        for attempt in (0, 1):
            try:
                await self._call_once(name, arguments)
                return
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError):
                if attempt == 1:
                    raise
                await asyncio.sleep(0.2)

    async def _call_once(self, name: str, arguments: dict) -> None:
        self._id += 1
        r = await self.client.post(
            "/mcp",
            headers={**MCP_HEADERS, "Mcp-Session-Id": self.sid},
            json={
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        r.raise_for_status()
        if "text/event-stream" in r.headers.get("content-type", ""):
            data = None
            for line in r.text.splitlines():
                if line.startswith("data: "):
                    data = line[6:]
            body = json.loads(data) if data else None
        else:
            body = r.json()
        if body is None:
            raise RuntimeError(f"空响应: {r.text[:200]}")
        if "error" in body:
            raise RuntimeError(f"RPC error: {str(body['error'])[:200]}")
        result = body.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"tool error: {str(result.get('content'))[:200]}")


#: 场景 = [(工具, 参数, 权重)]
SCENARIOS: dict[str, list[tuple[str, dict, int]]] = {
    "proto": [("capabilities", {}, 1)],
    "local": [("trend", {"hours": 24, "limit": 20}, 1)],
    "hot": [("hot_topics", {"limit_per_source": 20, "min_sources": 1}, 1)],
    "mixed": [
        ("capabilities", {}, 40),
        ("trend", {"hours": 24, "limit": 20}, 30),
        ("hot_topics", {"limit_per_source": 20, "min_sources": 1}, 20),
        ("topic_match", {"profile": "tech-blogger", "limit_per_source": 20, "top_n": 5}, 10),
    ],
}


class Stats:
    def __init__(self) -> None:
        self.lat: list[float] = []
        self.by_tool: dict[str, list[float]] = {}
        self.errors: list[str] = []
        self.ok = 0

    def record(self, tool: str, dt: float) -> None:
        self.lat.append(dt)
        self.by_tool.setdefault(tool, []).append(dt)
        self.ok += 1

    def record_error(self, tool: str, msg: str) -> None:
        if len(self.errors) < 20:
            self.errors.append(f"{tool}: {msg[:150]}")


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(p / 100 * len(s)))
    return s[k]


async def worker(client: httpx.AsyncClient, mix, deadline: float, stats: Stats) -> None:
    weights = [w for _, _, w in mix]
    sess = McpSession(client)
    try:
        await sess.start()
    except Exception as exc:  # noqa: BLE001 —— 握手失败不该炸掉整个压测
        stats.record_error("initialize", f"{type(exc).__name__}: {exc}")
        return
    while time.monotonic() < deadline:
        tool, args, _ = random.choices(mix, weights=weights)[0]
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(sess.call(tool, args), timeout=60.0)
            stats.record(tool, time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001
            stats.record_error(tool, f"{type(exc).__name__}: {exc}")
        # 无 sleep：纯压力，测饱和行为


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="mixed", choices=sorted(SCENARIOS))
    ap.add_argument("-c", "--concurrency", type=int, default=20)
    ap.add_argument("-d", "--duration", type=int, default=30)
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    mix = SCENARIOS[args.scenario]
    stats = Stats()
    limits = httpx.Limits(
        max_connections=args.concurrency + 10,
        max_keepalive_connections=args.concurrency + 10,
    )

    async with httpx.AsyncClient(
        base_url=args.base, limits=limits, timeout=httpx.Timeout(60.0)
    ) as client:
        t0 = time.monotonic()
        tasks = [
            asyncio.create_task(worker(client, mix, t0 + args.duration, stats))
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*tasks)

    dur = time.monotonic() - t0
    total = stats.ok + len(stats.errors)
    print(f"\n===== {args.label or args.scenario} | c={args.concurrency} d={args.duration}s =====")
    print(
        f"总请求 {total} | 成功 {stats.ok} | 失败 {len(stats.errors)} | "
        f"RPS {total / dur:.1f} | 会话 {args.concurrency}"
    )
    if stats.lat:
        print(
            f"  延迟 p50={pct(stats.lat, 50) * 1000:.0f}ms "
            f"p90={pct(stats.lat, 90) * 1000:.0f}ms "
            f"p95={pct(stats.lat, 95) * 1000:.0f}ms "
            f"p99={pct(stats.lat, 99) * 1000:.0f}ms "
            f"max={max(stats.lat) * 1000:.0f}ms"
        )
    for tool, xs in sorted(stats.by_tool.items()):
        print(
            f"  {tool:<14} n={len(xs):<5} p50={pct(xs, 50) * 1000:7.0f}ms "
            f"p95={pct(xs, 95) * 1000:7.0f}ms p99={pct(xs, 99) * 1000:7.0f}ms"
        )
    for e in stats.errors[:10]:
        print(f"  ERR {e}")


if __name__ == "__main__":
    asyncio.run(main())
