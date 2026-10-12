"""抖音适配器。

━━━ 实测现状（2026-10-12）━━━

  热榜        ✅ 可用    官方接口，无需登录、无需签名
  搜索        ⚠️ 需签名  裸请求返回 status_code=2483
  详情/评论/主页 ⚠️ 需签名

━━━ 签名策略 ━━━

a_bogus 是字节系 JS 逆向的持续对抗项，平台一改算法就要跟。
**不自研**——自研意味着永远在追版本。

已确认的替代路径（按优先级）：
  1. f2 (Johnserf-Seed) —— Apache-2.0，Python 库，活跃维护
  2. douyin-tiktok-scraper (Evil0ctal) —— MIT，PyPI 包

在签名后端接入前，需要签名的方法会抛出 SIGNATURE_REQUIRED，
并说明原因与替代路径——**不静默返回空**。
"""

from __future__ import annotations

from ..models import HotTopic
from .base import AuthLevel, Capability, CapabilitySpec, PlatformAdapter

_SIGNATURE_REASON = (
    "抖音搜索/详情/评论接口要求 a_bogus 签名。"
    "实测裸请求返回 status_code=2483（签名校验拦截）。"
    "签名是持续对抗项，本项目不自研，等待接入成熟库后端。"
)


class DouyinAdapter(PlatformAdapter):
    platform = "douyin"
    display_name = "抖音"

    specs = {
        Capability.HOT_TOPICS: CapabilitySpec(
            capability=Capability.HOT_TOPICS,
            supported=True,
            auth=AuthLevel.NONE,
            note="官方热榜接口，无需登录。实测可用。",
        ),
        Capability.CONTENT_SEARCH: CapabilitySpec(
            capability=Capability.CONTENT_SEARCH,
            supported=False,
            auth=AuthLevel.SIGNATURE,
            note=_SIGNATURE_REASON,
        ),
        Capability.CONTENT_DETAIL: CapabilitySpec(
            capability=Capability.CONTENT_DETAIL,
            supported=False,
            auth=AuthLevel.SIGNATURE,
            note=_SIGNATURE_REASON,
        ),
        Capability.CONTENT_COMMENTS: CapabilitySpec(
            capability=Capability.CONTENT_COMMENTS,
            supported=False,
            auth=AuthLevel.SIGNATURE,
            note=_SIGNATURE_REASON,
        ),
        Capability.CREATOR_PROFILE: CapabilitySpec(
            capability=Capability.CREATOR_PROFILE,
            supported=False,
            auth=AuthLevel.SIGNATURE,
            note=_SIGNATURE_REASON,
        ),
        Capability.CREATOR_WORKS: CapabilitySpec(
            capability=Capability.CREATOR_WORKS,
            supported=False,
            auth=AuthLevel.SIGNATURE,
            note=_SIGNATURE_REASON,
        ),
    }

    def __init__(self, *, http=None) -> None:
        # 延迟导入避免循环依赖
        if http is None:
            from ..http import HttpClient

            http = HttpClient()
        self.http = http

    def alternatives_for(self, capability: Capability) -> list[str]:
        return [
            "接入 f2 (Johnserf-Seed/f2) 作为签名后端",
            "接入 douyin-tiktok-scraper (Evil0ctal) 作为签名后端",
            "改用 hot_topics：抖音热榜无需签名即可用",
        ]

    async def hot_topics(self, *, limit: int = 50) -> list[HotTopic]:
        """抖音官方热榜。实测无需登录、无需签名。"""
        from ..hot.sources import fetch_douyin

        return await fetch_douyin(self.http, limit=limit)
