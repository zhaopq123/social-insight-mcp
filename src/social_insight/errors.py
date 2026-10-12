"""统一错误信封。

设计原则（见 ARCHITECTURE.md §4）：
    绝不静默返回空数据。能力缺失必须显式说明原因与替代路径。

静默失败比报错危险得多——调用方会把「没有数据」误读为「这个平台没内容」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """稳定的错误码。调用方可以据此做分支，不依赖 message 文本。"""

    # --- 能力层 ---
    PLATFORM_NOT_IMPLEMENTED = "PLATFORM_NOT_IMPLEMENTED"
    """该平台整体未实现（如视频号）。"""

    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    """平台已接入，但不支持这个具体能力。"""

    # --- 鉴权层 ---
    AUTH_REQUIRED = "AUTH_REQUIRED"
    """需要登录态，当前没有。"""

    SIGNATURE_REQUIRED = "SIGNATURE_REQUIRED"
    """需要请求签名（如抖音 a_bogus），当前不可用。"""

    AUTH_EXPIRED = "AUTH_EXPIRED"
    """登录态已过期，需要重新登录。"""

    # --- 调用层 ---
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    """上游平台返回了非预期结果。"""

    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class ToolError(Exception):
    """工具层异常 → 结构化错误信封。

    作为 Exception 抛出，由工具装饰器捕获并转成返回给调用方的 JSON。
    这样适配层可以直接 `raise ToolError(...)`，不必层层传错误码。
    """

    code: ErrorCode
    message: str
    platform: str | None = None
    reason: str | None = None
    """为什么做不到——给调用方看的解释，不是给机器看的。"""

    alternatives: list[str] = field(default_factory=list)
    """替代路径。即使当前做不了，也要告诉调用方还能怎么办。"""

    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
            },
        }
        err = payload["error"]
        if self.platform:
            err["platform"] = self.platform
        if self.reason:
            err["reason"] = self.reason
        if self.alternatives:
            err["alternatives"] = list(self.alternatives)
        if self.details:
            err["details"] = self.details
        return payload


# --- 便捷构造器：把重复的解释文本收敛到一处，避免各适配层写得不一样 ---

def platform_not_implemented(platform: str, reason: str, alternatives: list[str]) -> ToolError:
    return ToolError(
        code=ErrorCode.PLATFORM_NOT_IMPLEMENTED,
        message=f"平台 {platform} 尚未实现",
        platform=platform,
        reason=reason,
        alternatives=alternatives,
    )


def auth_required(platform: str, what: str, how: str) -> ToolError:
    return ToolError(
        code=ErrorCode.AUTH_REQUIRED,
        message=f"{platform} 的{what}需要登录态",
        platform=platform,
        reason=f"{platform} 无匿名访问路径，该能力必须携带登录 Cookie。",
        alternatives=[how],
    )


def signature_required(platform: str, what: str, detail: str) -> ToolError:
    return ToolError(
        code=ErrorCode.SIGNATURE_REQUIRED,
        message=f"{platform} 的{what}需要请求签名",
        platform=platform,
        reason=detail,
        alternatives=["依赖成熟第三方库的签名实现（f2 / douyin-tiktok-scraper）"],
    )


def success(data: Any, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """成功信封。与 ToolError.to_dict() 结构对称。"""
    payload: dict[str, Any] = {"ok": True, "data": data}
    if meta:
        payload["meta"] = meta
    return payload
