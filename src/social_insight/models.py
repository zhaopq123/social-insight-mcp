"""统一数据模型。

平台差异（字段名、分页方式、ID 形态）在适配层收敛到这里，
工具层与调用方只看到这一套结构。

设计要点：
  - 计数类字段一律 `int | None`。None = 平台没给，0 = 平台明确说是 0。
    混用这两者会让「爆款判断」得出错误结论。
  - 保留 `raw`，但默认不返回给调用方（省 token），排障时可开。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HotTopic:
    """热榜条目。"""

    platform: str
    word: str
    position: int
    hot_value: int | None = None
    topic_id: str | None = None
    """平台侧的话题 ID，可用于进一步检索。"""

    url: str = ""
    captured_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


@dataclass
class ContentItem:
    """一条内容（抖音作品 / 小红书笔记）。"""

    platform: str
    work_id: str
    title: str = ""
    description: str = ""
    author_id: str = ""
    author_name: str = ""
    tags: list[str] = field(default_factory=list)
    duration_sec: float | None = None

    # 互动指标
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    collect_count: int | None = None
    view_count: int | None = None

    publish_at: str | None = None
    """ISO 8601。用于算「发布后多久火了」。"""

    url: str = ""
    cover_url: str = ""
    captured_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def engagement(self) -> int | None:
        """互动总量。缺数据时返回 None 而不是 0——否则排序会把未知当成最差。"""
        parts = [self.like_count, self.comment_count, self.share_count, self.collect_count]
        known = [p for p in parts if p is not None]
        return sum(known) if known else None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        d["engagement"] = self.engagement
        if not include_raw:
            d.pop("raw", None)
        return d


@dataclass
class Comment:
    platform: str
    comment_id: str
    content: str
    author_name: str = ""
    like_count: int | None = None
    reply_count: int | None = None
    created_at: str | None = None
    parent_id: str | None = None
    """二级评论时指向一级评论。"""

    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


@dataclass
class Creator:
    platform: str
    creator_id: str
    nickname: str = ""
    signature: str = ""
    """个人简介——做对标分析时是重要信号。"""

    follower_count: int | None = None
    following_count: int | None = None
    work_count: int | None = None
    total_likes: int | None = None
    verified: bool | None = None
    url: str = ""
    avatar_url: str = ""
    captured_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


@dataclass
class Page:
    """分页信封。

    `cursor` 对调用方是不透明字符串——原样回传，永远不要解析它。
    """

    items: list[Any] = field(default_factory=list)
    cursor: str | None = None
    has_more: bool = False
    total: int | None = None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        items = [
            i.to_dict(include_raw=include_raw) if hasattr(i, "to_dict") else i for i in self.items
        ]
        return {
            "items": items,
            "cursor": self.cursor,
            "has_more": self.has_more,
            "total": self.total,
            "count": len(items),
        }
