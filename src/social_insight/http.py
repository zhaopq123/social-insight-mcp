"""HTTP 客户端：退避重试 · 节流 · 响应缓存。

采集层的职责（ARCHITECTURE.md §2 ④）：
    - 指数退避重试（平台风控时不要硬刚）
    - 请求节流（避免触发限流）
    - 响应缓存（减少风控暴露面，这是次要目标；主要目标是省时间）

刻意不做的事：不做签名。签名是平台特定的持续对抗项，属于各适配层，
放在通用客户端里会让它变成一个什么都知道的怪物。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .errors import ErrorCode, ToolError


@dataclass
class CacheEntry:
    payload: Any
    expires_at: float


@dataclass
class HttpClient:
    """带重试/节流/缓存的异步 HTTP 客户端。

    用法::

        client = HttpClient()
        data = await client.get_json(url, headers=..., cache_ttl=300)
    """

    timeout: float = 20.0
    max_retries: int = 3
    # 每个平台的最小请求间隔（秒）。防止并发打爆上游。
    min_interval: dict[str, float] = field(
        default_factory=lambda: {"douyin": 1.0, "xiaohongshu": 2.0, "channels": 3.0}
    )
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # 是否读取环境代理变量。
    #
    # 默认 False —— 这是一个踩过的坑：httpx 0.28.x 在解析形如
    # ``http://127.0.0.1:7897`` 的环境代理时会抛
    # ``InvalidURL: Invalid port: ':1]'``，导致**所有**请求失败。
    # 而国内热榜源（抖音/百度/B站/聚合站）本就不应走代理。
    # 需要代理时显式传 proxy=... 即可。
    trust_env: bool = False

    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _cache: dict[str, CacheEntry] = field(default_factory=dict, init=False, repr=False)
    _last_call: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)

    #: 缓存条目上限。
    #
    # ⚠️ 审计发现的隐患（2026-10-12）：缓存原本是**无界** dict，
    # 过期项只在被访问时才删（懒删除）。服务是长期运行的 ——
    # 实测塞 5000 条不同 key 后**全部留存**，意味着连续运行数天
    # 会持续吃内存直到 OOM。
    #
    # 1200 条对热点场景足够（10 个源 × 几个常见 limit 组合），
    # 超出后优先淘汰「最快过期」的条目。
    max_cache_entries: int = 1200

    async def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                trust_env=self.trust_env,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def cache_size(self) -> int:
        """当前缓存条目数（诊断用）。"""
        return len(self._cache)

    # --- 节流 ---

    async def _throttle(self, platform: str) -> None:
        lock = self._locks.setdefault(platform, asyncio.Lock())
        async with lock:
            gap = self.min_interval.get(platform, 1.0)
            last = self._last_call.get(platform, 0.0)
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call[platform] = time.monotonic()

    # --- 缓存 ---

    @staticmethod
    def _cache_key(method: str, url: str, payload: Any) -> str:
        raw = f"{method}|{url}|{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._cache.pop(key, None)
            return None
        return entry.payload

    def _cache_put(self, key: str, payload: Any, ttl: float) -> None:
        self._cache[key] = CacheEntry(payload=payload, expires_at=time.monotonic() + ttl)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """缓存超过上限时淘汰。

        策略：先清掉**所有已过期**的（便宜且明确无用），
        若仍超限，再按「最快过期」淘汰到上限的 80%。

        为什么不按 LRU：热点场景里「即将过期」等价于「最没价值」，
        按过期时间淘汰比维护访问顺序更简单，也够用。
        """
        limit = self.max_cache_entries
        if len(self._cache) <= limit:
            return

        now = time.monotonic()
        expired = [k for k, v in self._cache.items() if v.expires_at < now]
        for k in expired:
            self._cache.pop(k, None)

        if len(self._cache) <= limit:
            return

        # 仍超限 → 按过期时间升序淘汰到 80%
        target = max(1, int(limit * 0.8))
        drop_count = len(self._cache) - target
        oldest = sorted(self._cache.items(), key=lambda kv: kv[1].expires_at)[:drop_count]
        for k, _ in oldest:
            self._cache.pop(k, None)

    # --- 请求 ---

    async def request_json(
        self,
        url: str,
        *,
        platform: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        cache_ttl: float = 0.0,
    ) -> Any:
        """发请求并解析 JSON。失败抛 ToolError（结构化），不抛裸异常。"""
        key = self._cache_key(method, url, {"params": params, "body": json_body})
        if cache_ttl > 0:
            hit = self._cache_get(key)
            if hit is not None:
                return hit

        client = await self._ensure()
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            await self._throttle(platform)
            try:
                resp = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    cookies=cookies,
                )

                # 429 / 5xx 值得重试；4xx 其他情况不重试（重试也没用）
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = ToolError(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"上游返回 {resp.status_code}",
                        platform=platform,
                        retryable=True,
                    )
                    await self._backoff(attempt)
                    continue

                if resp.status_code >= 400:
                    raise ToolError(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"上游返回 {resp.status_code}",
                        platform=platform,
                        details={"body_preview": resp.text[:300]},
                    )

                try:
                    payload = resp.json()
                except Exception:
                    raise ToolError(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message="上游返回的不是合法 JSON",
                        platform=platform,
                        details={"body_preview": resp.text[:300]},
                    )

                if cache_ttl > 0:
                    self._cache_put(key, payload, cache_ttl)
                return payload

            except ToolError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await self._backoff(attempt)
                continue

        raise ToolError(
            code=ErrorCode.TIMEOUT if isinstance(last_exc, httpx.TimeoutException) else ErrorCode.UPSTREAM_ERROR,
            message=f"请求失败（已重试 {self.max_retries} 次）：{last_exc}",
            platform=platform,
            retryable=True,
        )

    async def _backoff(self, attempt: int) -> None:
        """指数退避 + 抖动。抖动是为了避免多进程同时重试形成脉冲。"""
        delay = min(2.0 ** attempt, 8.0)
        await asyncio.sleep(delay * (0.5 + random.random() * 0.5))

    # --- 文本请求（HTML 聚合站用） ---

    async def request_text(
        self,
        url: str,
        *,
        platform: str,
        headers: dict[str, str] | None = None,
        cache_ttl: float = 0.0,
    ) -> str:
        """抓取文本（HTML）。聚合站不是 API，只能解析页面。"""
        key = self._cache_key("GET-TEXT", url, None)
        if cache_ttl > 0:
            hit = self._cache_get(key)
            if hit is not None:
                return hit

        client = await self._ensure()
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            await self._throttle(platform)
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = ToolError(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"上游返回 {resp.status_code}",
                        platform=platform,
                        retryable=True,
                    )
                    await self._backoff(attempt)
                    continue
                if resp.status_code >= 400:
                    raise ToolError(
                        code=ErrorCode.UPSTREAM_ERROR,
                        message=f"上游返回 {resp.status_code}",
                        platform=platform,
                    )
                text = resp.text
                if cache_ttl > 0:
                    self._cache_put(key, text, cache_ttl)
                return text
            except ToolError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await self._backoff(attempt)
                continue

        raise ToolError(
            code=ErrorCode.UPSTREAM_ERROR,
            message=f"抓取失败（已重试 {self.max_retries} 次）：{last_exc}",
            platform=platform,
            retryable=True,
        )
