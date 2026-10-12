"""热点源解析测试（用真实 HTML/JSON 片段，不依赖网络）。

━━━ 为什么用真实片段而不是构造的假数据 ━━━

这些片段全部**取自服务器实测抓回的真实响应**。
构造的假数据会掩盖真实结构的怪癖——比如 tophub 的标题
根本不在 <a> 里，而在 <span class="t"> 里（这个坑实测踩过）。
"""

from __future__ import annotations

import httpx
import pytest

from social_insight.hot.sources import (
    _is_commerce_item,
    _parse_hot_text,
    fetch_baidu,
    fetch_douyin,
    fetch_tophub,
)
from social_insight.http import HttpClient


def make_client(handler) -> HttpClient:
    c = HttpClient()
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return c


# ══════════════════════════════════════════════════════════════════
# 热度文本解析
# ══════════════════════════════════════════════════════════════════


class TestParseHotText:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1234", 1234),
            ("1.5万", 15000),
            ("12.3w", 123000),
            ("2亿", 200000000),
            ("1k", 1000),
            ("1", 1),
            ("", None),
            ("无", None),
            ("abc", None),
            ("  1234  ", 1234),
        ],
    )
    def test_parsing(self, text, expected):
        assert _parse_hot_text(text) == expected


# ══════════════════════════════════════════════════════════════════
# 电商条目过滤
# ══════════════════════════════════════════════════════════════════


class TestCommerceFilter:
    @pytest.mark.parametrize(
        "title",
        [
            "瘦子菌！乐力SIIHO成人高活性冻干益生菌粉 原价¥39.9 券后¥13.9",
            "某商品 热销10000件",
            "限时立减50元",
            "包邮到家",
        ],
    )
    def test_detects_commerce(self, title):
        assert _is_commerce_item(title) is True

    @pytest.mark.parametrize(
        "title",
        [
            "郑钦文退出武汉网球公开赛",
            "我国物流业景气水平进一步提升",
            "某地开通新地铁线路",
            "人民币对美元汇率中间价",
        ],
    )
    def test_news_not_filtered(self, title):
        assert _is_commerce_item(title) is False


# ══════════════════════════════════════════════════════════════════
# 抖音
# ══════════════════════════════════════════════════════════════════


class TestDouyin:
    @pytest.mark.asyncio
    async def test_parses_real_structure(self):
        """真实结构：data.word_list[].{word, position, hot_value, sentence_id}"""

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "status_code": 0,
                    "data": {
                        "word_list": [
                            {
                                "word": "某热点事件",
                                "position": 1,
                                "hot_value": 12000000,
                                "sentence_id": "7481234567890",
                            },
                            {"word": "第二个热点", "position": 2, "hot_value": 9000000},
                        ]
                    },
                },
            )

        c = make_client(handler)
        topics = await fetch_douyin(c, limit=10)
        assert len(topics) == 2
        assert topics[0].word == "某热点事件"
        assert topics[0].hot_value == 12000000
        assert topics[0].topic_id == "7481234567890"
        assert topics[0].url == "https://www.douyin.com/hot/7481234567890"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_skips_empty_words(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"data": {"word_list": [{"word": ""}, {"word": "有效"}]}},
            )

        c = make_client(handler)
        topics = await fetch_douyin(c, limit=10)
        assert len(topics) == 1
        assert topics[0].word == "有效"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"data": {}})

        c = make_client(handler)
        assert await fetch_douyin(c, limit=10) == []
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# 百度
# ══════════════════════════════════════════════════════════════════


class TestBaidu:
    @pytest.mark.asyncio
    async def test_parses_nested_structure(self):
        """真实结构：data.cards[0].content[0].content[] —— 多嵌两层。

        早先版本少写了一层，导致返回 0 条（实测踩过）。
        """

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "cards": [
                            {
                                "component": "hotList",
                                "content": [
                                    {
                                        "content": [
                                            {
                                                "word": "夯实中国式现代化的健康根基",
                                                "url": "https://m.baidu.com/s?word=x",
                                                "isTop": True,
                                            },
                                            {
                                                "word": "郑钦文退出武网",
                                                "hotTag": "3",
                                            },
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                },
            )

        c = make_client(handler)
        topics = await fetch_baidu(c, limit=10)
        assert len(topics) == 2, "必须能穿透两层嵌套"
        assert topics[0].word == "夯实中国式现代化的健康根基"
        assert topics[0].url
        await c.aclose()

    @pytest.mark.asyncio
    async def test_limit_respected(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "cards": [
                            {"content": [{"content": [{"word": f"话题{i}"} for i in range(20)]}]}
                        ]
                    }
                },
            )

        c = make_client(handler)
        assert len(await fetch_baidu(c, limit=5)) == 5
        await c.aclose()

    @pytest.mark.asyncio
    async def test_empty_cards(self):
        def handler(request):
            return httpx.Response(200, json={"data": {"cards": []}})

        c = make_client(handler)
        assert await fetch_baidu(c, limit=10) == []
        await c.aclose()


# ══════════════════════════════════════════════════════════════════
# tophub（重点：标题在 span.t 而非 <a>）
# ══════════════════════════════════════════════════════════════════

#: 真实 HTML 片段（取自服务器实测响应）
TOPHUB_HTML = """
<html><body>
<div class="cc-cd-cb-ll">
  <span class="s h">1</span>
  <span class="t">郑钦文退出武汉网球公开赛</span>
  <span class="e">热度 1234万</span>
</div>
<div class="cc-cd-cb-ll">
  <span class="s h">2</span>
  <span class="t">瘦子菌！乐力SIIHO成人高活性冻干益生菌粉 原价¥39.9 券后¥13.9</span>
  <span class="e">热销10000件(近2小时)</span>
</div>
<div class="cc-cd-cb-ll">
  <span class="s">3</span>
  <span class="t">我国物流业景气水平进一步提升</span>
</div>
<div class="cc-cd-cb-ll">
  <span class="s">4</span>
  <span class="t">郑钦文退出武汉网球公开赛</span>
</div>
</body></html>
"""


class TestTophub:
    @pytest.mark.asyncio
    async def test_extracts_from_span_t(self):
        """标题在 span.t 里 —— 按 <a> 解析会返回 0 条（实测踩过的坑）。"""

        def handler(request):
            return httpx.Response(200, text=TOPHUB_HTML)

        c = make_client(handler)
        topics = await fetch_tophub(c, limit=10)
        words = [t.word for t in topics]
        assert "郑钦文退出武汉网球公开赛" in words, "必须能从 span.t 取到标题"
        assert "我国物流业景气水平进一步提升" in words
        await c.aclose()

    @pytest.mark.asyncio
    async def test_filters_commerce(self):
        """电商商品必须被过滤——否则污染选题池。"""

        def handler(request):
            return httpx.Response(200, text=TOPHUB_HTML)

        c = make_client(handler)
        topics = await fetch_tophub(c, limit=10)
        for t in topics:
            assert "¥" not in t.word, f"电商条目未被过滤: {t.word}"
            assert "热销" not in t.word
        await c.aclose()

    @pytest.mark.asyncio
    async def test_deduplicates(self):
        """重复标题只保留一次。"""

        def handler(request):
            return httpx.Response(200, text=TOPHUB_HTML)

        c = make_client(handler)
        topics = await fetch_tophub(c, limit=10)
        words = [t.word for t in topics]
        assert len(words) == len(set(words)), "存在重复条目"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_empty_html(self):
        def handler(request):
            return httpx.Response(200, text="<html><body>nothing</body></html>")

        c = make_client(handler)
        assert await fetch_tophub(c, limit=10) == []
        await c.aclose()
