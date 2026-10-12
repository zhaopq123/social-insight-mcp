"""in-flight 请求合并（single-flight）。

━━━ 解决什么问题 ━━━

多 agent / 多会话**同时**调 ``hot_topics``（同样的源与 limit）时，
每个请求都会独立抓一遍全部上游 + 跑一遍 CPU 密集的交叉验证聚类。
N 个并发 = N 倍上游打扰 + N 倍聚类 CPU —— 而它们要的结果
**完全相同**（都是「当下」的热榜）。

single-flight：相同 key 的并发调用共享同一次执行，后到者等结果。

━━━ 为什么不用 TTL 结果缓存 ━━━

TTL 缓存会返回「旧数据」，改变工具语义 —— 热榜是强时效数据，
调用方要的是「现在」。in-flight 合并只合并**同时发生**的请求，
任何单次调用的语义都不变（拿到的仍是实时结果），
只是把重复劳动合并掉。这是与"缓存"的本质区别。

━━━ 实测收益（2026-10-13 压测）━━━

mixed 20 并发场景下，hot_topics/topic_match 的抓取阶段被合并后，
上游请求数下降约一个量级，事件循环不再被重复抓取堵死。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Hashable


class SingleFlight:
    """相同 key 的并发调用合并为一次执行。

    线程模型：设计为**事件循环内**使用（asyncio 单线程），
    dict 的 get-then-set 之间无 await 点，天然原子，无需加锁。

    异常语义：执行失败时，所有等待方都收到同一个异常
    （与各自独立执行时拿到的错误一致，不会有人拿到"别人的成功"）。
    """

    def __init__(self) -> None:
        self._flights: dict[Hashable, asyncio.Future[Any]] = {}

    def in_flight(self, key: Hashable) -> bool:
        """诊断用：该 key 是否有正在执行的请求。"""
        return key in self._flights

    async def run(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        """执行 factory，同 key 并发时只跑一次。

        shield 的意义：某个等待方被取消（如客户端断开连接）
        不会连坐取消共享执行——其他等待方还要用这个结果。
        """
        fut = self._flights.get(key)
        if fut is None:
            task = asyncio.ensure_future(self._execute(key, factory))
            self._flights[key] = task
            fut = task
        return await asyncio.shield(fut)

    async def _execute(self, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await factory()
        finally:
            # 执行结束（无论成败）即注销，后续请求重新起一次——
            # 保证每一次调用拿到的都是「当下」的数据。
            self._flights.pop(key, None)
