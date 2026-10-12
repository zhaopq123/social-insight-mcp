"""小红书适配器。

━━━ 实测现状（2026-10-12）━━━

  全部能力  ⚠️ 必须登录

实测证据（不是推测）：
  1. 搜索 API 直接返回：
     ``{"code":-101,"success":false,"msg":"无登录信息，或登录信息为空"}``
  2. 搜索页 HTML 虽返回 200，但含「登录后」字样，且**不含 xsec_token**
     —— 只是个空壳，拿不到可用数据

━━━ 为什么没有匿名路径 ━━━

小红书的风控是 login-first：所有数据走 edith.xiaohongshu.com 的签名 API，
而签名的输入之一就是登录后才有的 ``a1`` cookie。
**没有 cookie → 算不出签名 → 连门都进不去。**

两条独立闸门，缺一不可：Cookie（a1 + web_session）与签名头（x-s/x-t/x-s-common）。

━━━ 接入策略：编排，不重造 ━━━

签名逆向 + 登录态维护 + 风控退避是持续对抗。本项目不自研这三件事。

推荐后端：xiaohongshu-mcp (xpzouying)
  - Apache-2.0（可商用，唯一许可证干净的候选）
  - 活跃维护（2026-10-12 仍在推送）
  - 本身就是服务，暴露 MCP + HTTP

本适配器只做**只读白名单包装**。

━━━ 重要约束（来自调研与实测）━━━

  1. ``xsec_token`` 必须与 ``feed_id``/``user_id`` **成对**提供，
     且只能从搜索结果里透传 → **调用顺序被强制**：先搜索，再取详情
  2. 同一账号不能在多处网页端登录（登录 MCP 后登网页会被踢下线）
     → 需要**专用采集账号**
"""

from __future__ import annotations

from ..models import HotTopic
from .base import AuthLevel, Capability, CapabilitySpec, PlatformAdapter

_LOGIN_REASON = (
    "小红书无匿名访问路径。实测搜索 API 返回 code=-101「无登录信息」，"
    "且签名头 x-s/x-t 的计算依赖登录后才有的 a1 cookie —— "
    "没有 cookie 连签名都算不出来。"
)

_LOGIN_HOWTO = (
    "需在服务器上为专用采集账号扫码一次（cookie 持久化）。"
    "注意：同一账号不能多处网页端登录，必须用专用号。"
    "或改接第三方付费数据服务（无需自持 cookie）。"
)


class XiaohongshuAdapter(PlatformAdapter):
    platform = "xiaohongshu"
    display_name = "小红书"

    specs = {
        Capability.HOT_TOPICS: CapabilitySpec(
            capability=Capability.HOT_TOPICS,
            # 热搜榜可以通过 uapis 聚合源拿到——不需要登录小红书本身。
            supported=True,
            auth=AuthLevel.NONE,
            note="经 uapis 聚合源获取热搜榜，无需登录小红书账号。",
        ),
        Capability.CONTENT_SEARCH: CapabilitySpec(
            capability=Capability.CONTENT_SEARCH,
            supported=False,
            auth=AuthLevel.LOGIN,
            note=_LOGIN_REASON + " 需先接入采集后端（建议 xiaohongshu-mcp）。",
        ),
        Capability.CONTENT_DETAIL: CapabilitySpec(
            capability=Capability.CONTENT_DETAIL,
            supported=False,
            auth=AuthLevel.LOGIN,
            note=_LOGIN_REASON
            + " 注意：详情必须携带搜索结果里的 xsec_token，调用顺序被强制。",
        ),
        Capability.CONTENT_COMMENTS: CapabilitySpec(
            capability=Capability.CONTENT_COMMENTS,
            supported=False,
            auth=AuthLevel.LOGIN,
            note=_LOGIN_REASON,
        ),
        Capability.CREATOR_PROFILE: CapabilitySpec(
            capability=Capability.CREATOR_PROFILE,
            supported=False,
            auth=AuthLevel.LOGIN,
            note=_LOGIN_REASON + " 主页同样需要 user_id + xsec_token 成对提供。",
        ),
        Capability.CREATOR_WORKS: CapabilitySpec(
            capability=Capability.CREATOR_WORKS,
            supported=False,
            auth=AuthLevel.LOGIN,
            note=_LOGIN_REASON,
        ),
    }

    def __init__(self, *, backend: object | None = None, http=None) -> None:
        """backend 是采集后端（如 xiaohongshu-mcp 客户端）。

        为 None 时，需要登录的能力会抛出结构化错误说明情况。
        这样「没配后端」和「后端挂了」在调用方看来是两件不同的事。
        """
        self.backend = backend
        if http is None:
            from ..http import HttpClient

            http = HttpClient()
        self.http = http

    def alternatives_for(self, capability: Capability) -> list[str]:
        return [
            "接入 xiaohongshu-mcp (xpzouying) 作为采集后端，Apache-2.0 可商用",
            "改用第三方付费数据服务（如 SocialDataX），无需自持 cookie",
            "用 hot_topics(sources=['uapis:xiaohongshu']) 获取小红书热搜——无需登录",
        ]

    async def hot_topics(self, *, limit: int = 20) -> list[HotTopic]:
        """小红书热搜——经 uapis 聚合源，**无需登录**。"""
        from ..hot.sources import fetch_source

        return await fetch_source(self.http, "uapis:xiaohongshu", limit=limit)
