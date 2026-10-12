"""平台适配层接口。

这一层存在的唯一理由：把平台差异收敛掉，让工具层和调用方看到统一世界。

核心机制是 **能力声明（Capability）**。每个适配器如实声明自己能做什么、
需要什么鉴权级别。工具层据此在调用前拦截，返回结构化错误——
而不是让请求打到上游、拿到一个含义不明的空结果。

这直接实现了 ARCHITECTURE.md §4 的「登录态分级与降级」和
§3 的「视频号有位置但不实现」。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum

from ..errors import ToolError, platform_not_implemented
from ..models import ContentItem, Creator, HotTopic, Page


class AuthLevel(str, Enum):
    """访问某个能力所需的最低鉴权级别。"""

    NONE = "none"
    """匿名可访问（如抖音热榜）。"""

    SIGNATURE = "signature"
    """需要请求签名，不需要登录（抖音部分接口）。"""

    LOGIN = "login"
    """需要登录 Cookie（小红书全部能力）。"""


class Capability(str, Enum):
    """跨平台统一的能力名。工具层按这个名字查询。"""

    HOT_TOPICS = "hot_topics"
    CONTENT_SEARCH = "content_search"
    CONTENT_DETAIL = "content_detail"
    CONTENT_COMMENTS = "content_comments"
    CREATOR_PROFILE = "creator_profile"
    CREATOR_WORKS = "creator_works"


#: 各能力的中文名，用于错误消息（避免调用方看到 hot_topics 这种内部名）
CAPABILITY_LABELS: dict[Capability, str] = {
    Capability.HOT_TOPICS: "热榜",
    Capability.CONTENT_SEARCH: "内容搜索",
    Capability.CONTENT_DETAIL: "内容详情",
    Capability.CONTENT_COMMENTS: "评论列表",
    Capability.CREATOR_PROFILE: "创作者主页",
    Capability.CREATOR_WORKS: "创作者作品列表",
}


@dataclass(frozen=True)
class CapabilitySpec:
    """一条能力声明。"""

    capability: Capability
    supported: bool
    auth: AuthLevel = AuthLevel.NONE
    note: str = ""
    """给调用方看的说明，例如「需先搜索拿 xsec_token」。"""


class PlatformAdapter(ABC):
    """平台适配器基类。

    子类必须声明 `platform` 与 `specs`，并按需实现各能力方法。
    未实现的方法继承基类的默认行为——抛出带解释的结构化错误。
    """

    platform: str = ""
    display_name: str = ""
    specs: dict[Capability, CapabilitySpec] = {}

    # --- 能力查询 ---

    def spec(self, capability: Capability) -> CapabilitySpec:
        return self.specs.get(
            capability,
            CapabilitySpec(capability=capability, supported=False, note="未声明该能力"),
        )

    def supports(self, capability: Capability) -> bool:
        return self.spec(capability).supported

    def guard(self, capability: Capability) -> None:
        """调用前守卫。不支持就抛出带解释的错误，绝不静默返回空。"""
        spec = self.spec(capability)
        if spec.supported:
            return

        label = CAPABILITY_LABELS.get(capability, capability.value)
        raise ToolError(
            code=__import__("social_insight.errors", fromlist=["ErrorCode"]).ErrorCode.CAPABILITY_UNSUPPORTED,
            message=f"{self.display_name or self.platform} 不支持{label}",
            platform=self.platform,
            reason=spec.note or "该能力在当前平台不可用。",
            alternatives=self.alternatives_for(capability),
        )

    def alternatives_for(self, capability: Capability) -> list[str]:
        """做不到时给调用方的替代路径。子类可覆盖。"""
        return []

    # --- 能力方法：默认全部「不支持」，子类按需覆盖 ---

    async def hot_topics(self, *, limit: int = 50) -> list[HotTopic]:
        self.guard(Capability.HOT_TOPICS)
        raise NotImplementedError

    async def content_search(
        self,
        *,
        keyword: str,
        sort: str = "general",
        time_range: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page:
        self.guard(Capability.CONTENT_SEARCH)
        raise NotImplementedError

    async def content_detail(self, *, id_or_url: str) -> ContentItem:
        self.guard(Capability.CONTENT_DETAIL)
        raise NotImplementedError

    async def content_comments(
        self,
        *,
        id_or_url: str,
        level: int = 1,
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page:
        self.guard(Capability.CONTENT_COMMENTS)
        raise NotImplementedError

    async def creator_profile(self, *, id_or_url: str) -> Creator:
        self.guard(Capability.CREATOR_PROFILE)
        raise NotImplementedError

    async def creator_works(
        self,
        *,
        id_or_url: str,
        sort: str = "latest",
        limit: int = 20,
        cursor: str | None = None,
    ) -> Page:
        self.guard(Capability.CREATOR_WORKS)
        raise NotImplementedError


class NotImplementedAdapter(PlatformAdapter):
    """占位适配器：平台有位置，但整体不实现。

    用于视频号。它存在的价值是——调用方问起时能得到一个**诚实的、
    带解释的**答复，而不是 404 或者一句「未知平台」。

    将来要接第三方数据源，只需写一个新的 PlatformAdapter 子类替换掉它，
    工具层和调用方一行都不用改。
    """

    def __init__(self, platform: str, display_name: str, reason: str, alternatives: list[str]):
        self.platform = platform
        self.display_name = display_name
        self._reason = reason
        self._alternatives = alternatives
        self.specs = {
            cap: CapabilitySpec(capability=cap, supported=False, note=reason)
            for cap in Capability
        }

    def guard(self, capability: Capability) -> None:
        # 整体未实现 → 用 PLATFORM_NOT_IMPLEMENTED，语义比 CAPABILITY_UNSUPPORTED 更准
        raise platform_not_implemented(self.platform, self._reason, self._alternatives)

    def alternatives_for(self, capability: Capability) -> list[str]:
        return list(self._alternatives)
