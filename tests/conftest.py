"""pytest 共享配置与 fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试能 import social_insight
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.models import Comment, ContentItem, Creator, HotTopic


@pytest.fixture
def topic_factory():
    """构造 HotTopic 的工厂。"""

    def _make(word: str, platform: str = "douyin", position: int = 1, hot: int | None = None):
        return HotTopic(
            platform=platform,
            word=word,
            position=position,
            hot_value=hot,
            captured_at="2026-10-12T00:00:00Z",
        )

    return _make


@pytest.fixture
def content_factory():
    """构造 ContentItem 的工厂。"""

    def _make(
        work_id: str = "w1",
        platform: str = "douyin",
        title: str = "标题",
        like: int | None = None,
        comment: int | None = None,
        collect: int | None = None,
        share: int | None = None,
        **kw,
    ):
        return ContentItem(
            platform=platform,
            work_id=work_id,
            title=title,
            like_count=like,
            comment_count=comment,
            collect_count=collect,
            share_count=share,
            **kw,
        )

    return _make


@pytest.fixture
def comment_factory():
    def _make(content: str = "评论", like: int | None = None, cid: str = "c1"):
        return Comment(platform="douyin", comment_id=cid, content=content, like_count=like)

    return _make


@pytest.fixture
def creator_factory():
    def _make(nickname: str = "某人", followers: int | None = None, cid: str = "a1"):
        return Creator(platform="douyin", creator_id=cid, nickname=nickname, follower_count=followers)

    return _make
