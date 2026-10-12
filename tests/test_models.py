"""数据模型测试。

━━━ 重点：None 与 0 的语义区分 ━━━

这是本项目最容易出**静默错误**的地方：
  · ``None`` = 平台没给这个数据
  · ``0``    = 平台明确说是 0

混用这两者会让「爆款判断」得出错误结论——
把「未知」当成「最差」排序，或者把「无人互动」误判成「数据缺失」。
"""

from __future__ import annotations


class TestEngagement:
    """互动总量计算。"""

    def test_all_known(self, content_factory):
        c = content_factory(like=100, comment=20, collect=30, share=10)
        assert c.engagement == 160

    def test_all_none_returns_none(self, content_factory):
        """全部缺失必须返回 None，不是 0。

        如果返回 0，排序时会被当成「最差」，但实际上我们只是不知道。
        """
        c = content_factory()
        assert c.engagement is None

    def test_partial_known(self, content_factory):
        """部分缺失时，只累加已知的。"""
        c = content_factory(like=100, comment=None, collect=30)
        assert c.engagement == 130

    def test_zero_is_not_none(self, content_factory):
        """明确为 0 要参与计算。"""
        c = content_factory(like=0, comment=0)
        assert c.engagement == 0
        assert c.engagement is not None

    def test_zero_and_none_distinct(self, content_factory):
        """0 与 None 必须区分——这是本项目的核心语义约定。"""
        zeros = content_factory(like=0, comment=0, collect=0, share=0)
        nones = content_factory()
        assert zeros.engagement == 0
        assert nones.engagement is None
        assert zeros.engagement != nones.engagement


class TestSerialization:
    """序列化契约。"""

    def test_to_dict_excludes_raw_by_default(self, content_factory):
        """raw 默认不返回——省 token。"""
        c = content_factory()
        c.raw = {"huge": "payload" * 1000}
        d = c.to_dict()
        assert "raw" not in d

    def test_to_dict_includes_raw_when_asked(self, content_factory):
        c = content_factory()
        c.raw = {"k": "v"}
        d = c.to_dict(include_raw=True)
        assert d["raw"] == {"k": "v"}

    def test_engagement_in_dict(self, content_factory):
        c = content_factory(like=10, comment=5)
        assert c.to_dict()["engagement"] == 15

    def test_all_fields_json_serializable(self, content_factory):
        """必须能直接 json.dumps —— 否则 MCP 返回时会炸。"""
        import json

        c = content_factory(like=1, tags=["a", "b"])
        json.dumps(c.to_dict())

    def test_unicode_preserved(self, content_factory):
        c = content_factory(title="中文标题🎉", tags=["标签"])
        d = c.to_dict()
        assert d["title"] == "中文标题🎉"
        assert d["tags"] == ["标签"]


class TestHotTopic:
    def test_basic(self, topic_factory):
        t = topic_factory("某话题", hot=12345)
        d = t.to_dict()
        assert d["word"] == "某话题"
        assert d["hot_value"] == 12345

    def test_raw_excluded(self, topic_factory):
        t = topic_factory("x")
        t.raw = {"big": "x" * 500}
        assert "raw" not in t.to_dict()


class TestCreator:
    def test_none_followers(self, creator_factory):
        """粉丝数未知时是 None——做对标对比时不能当 0 处理。"""
        c = creator_factory(followers=None)
        assert c.to_dict()["follower_count"] is None

    def test_zero_followers(self, creator_factory):
        c = creator_factory(followers=0)
        assert c.to_dict()["follower_count"] == 0


class TestPage:
    def test_page_serializes_items(self, content_factory):
        from social_insight.models import Page

        p = Page(items=[content_factory(work_id="a"), content_factory(work_id="b")], has_more=True)
        d = p.to_dict()
        assert d["count"] == 2
        assert d["has_more"] is True
        assert d["items"][0]["work_id"] == "a"

    def test_empty_page(self):
        from social_insight.models import Page

        d = Page().to_dict()
        assert d["count"] == 0
        assert d["items"] == []
        assert d["has_more"] is False

    def test_cursor_is_opaque(self, content_factory):
        """cursor 原样回传，不解析。"""
        from social_insight.models import Page

        p = Page(items=[], cursor="eyJvZmZzZXQiOjIwfQ==", has_more=True)
        assert p.to_dict()["cursor"] == "eyJvZmZzZXQiOjIwfQ=="
