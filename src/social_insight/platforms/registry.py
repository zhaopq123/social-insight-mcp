"""平台注册表。

**这是「视频号有位置但不实现」的落点。**

`get_adapter()` 对任何已登记的平台都返回一个适配器对象——
包括视频号。区别在于视频号的适配器会在每个能力调用上抛出
带解释的结构化错误，而不是让调用方收到 404 或「未知平台」。

这样做的价值：调用方（LLM）能读到「为什么不能做」以及「还能怎么办」，
而不是面对一个含糊的失败去瞎猜。
"""

from __future__ import annotations

from typing import Any

from ..errors import ErrorCode, ToolError
from .base import Capability, CapabilitySpec, PlatformAdapter
from .channels import build_channels_adapter
from .douyin import DouyinAdapter
from .xiaohongshu import XiaohongshuAdapter

_ADAPTERS: dict[str, PlatformAdapter] = {}


def _register(adapter: PlatformAdapter) -> None:
    _ADAPTERS[adapter.platform] = adapter


def _init() -> None:
    if _ADAPTERS:
        return
    _register(DouyinAdapter())
    _register(XiaohongshuAdapter())
    _register(build_channels_adapter())


#: 平台别名 → 规范名。让调用方可以用「视频号」「wx」这类常见写法。
_ALIASES: dict[str, str] = {
    "douyin": "douyin",
    "dy": "douyin",
    "抖音": "douyin",
    "tiktok": "douyin",
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "rednote": "xiaohongshu",
    "小红书": "xiaohongshu",
    "channels": "channels",
    "wechat_channels": "channels",
    "shipinhao": "channels",
    "视频号": "channels",
    "微信视频号": "channels",
}


def resolve_platform(name: str) -> str:
    key = (name or "").strip().lower()
    resolved = _ALIASES.get(key)
    if resolved is None:
        raise ToolError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"未知平台：{name}",
            reason=(
                f"已登记平台：{', '.join(sorted(set(_ALIASES.values())))}。"
                "也接受中文别名（抖音/小红书/视频号）。"
            ),
            alternatives=sorted({*_ALIASES.values(), "抖音", "小红书", "视频号"}),
        )
    return resolved


def get_adapter(name: str) -> PlatformAdapter:
    _init()
    return _ADAPTERS[resolve_platform(name)]


def list_adapters() -> list[PlatformAdapter]:
    _init()
    return list(_ADAPTERS.values())


def platform_matrix() -> dict[str, Any]:
    """生成能力矩阵——给 capabilities 工具用。

    调用方读这一份就知道：每个平台支持哪些能力、需要什么鉴权级别。
    """
    _init()
    out: dict[str, Any] = {}
    for name, adapter in _ADAPTERS.items():
        caps: dict[str, Any] = {}
        for cap in Capability:
            spec: CapabilitySpec = adapter.spec(cap)
            caps[cap.value] = {
                "supported": spec.supported,
                "auth": spec.auth.value,
                **({"note": spec.note} if spec.note else {}),
            }
        out[name] = {
            "display_name": adapter.display_name,
            "capabilities": caps,
        }
    return out
