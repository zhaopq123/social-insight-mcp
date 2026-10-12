"""链路 A · 热点源适配器。

**这一层完全不需要登录、不需要签名、不携带任何 cookie。**

━━━ 核心设计：独立性分组（independence group） ━━━

交叉验证的前提是**源必须互相独立**。如果 B 只是 A 的镜像，
那「两个源都报道」不构成任何证据——这是自欺欺人。

实测（2026-10-12）：uapis 的 douyin 榜与抖音官方榜前 10 条重合 7 条
→ 它是镜像，**不能**用来给抖音热点做交叉验证。
但它对 weibo/zhihu/xiaohongshu/toutiao 等我们无法直连的平台是**唯一来源**，
这时它就是独立源。

因此每个源都标注 `independence` 键：**同键的源互为镜像，只算一票**。
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from ..errors import ErrorCode, ToolError
from ..http import HttpClient
from ..models import HotTopic

# --------------------------------------------------------------------------
# 源注册表
# --------------------------------------------------------------------------

#: 源名 → 元信息
#:   display      显示名
#:   desc         说明
#:   weight       可信度权重（官方源 > 聚合站）
#:   independence 独立性键——同键互为镜像，交叉验证时只算一票
#:   platforms    该源覆盖的平台（用于按平台筛选）
SOURCE_META: dict[str, dict[str, Any]] = {
    # ── 官方直连源（权威，独立）──
    "douyin": {
        "display": "抖音热榜",
        "desc": "抖音站内实时热搜（官方接口）",
        "weight": 1.0,
        "independence": "douyin",
        "platforms": ["douyin"],
    },
    "baidu": {
        "display": "百度热搜",
        "desc": "百度实时热搜（官方接口），社会热点覆盖广",
        "weight": 0.95,
        "independence": "baidu",
        "platforms": ["baidu"],
    },
    "bilibili": {
        "display": "B站排行",
        "desc": "B站全站排行（官方接口），年轻用户风向",
        "weight": 0.8,
        "independence": "bilibili",
        "platforms": ["bilibili"],
    },
    "juejin": {
        "display": "掘金热榜",
        "desc": "掘金技术热榜（官方接口），软件类账号强相关",
        "weight": 0.85,
        "independence": "juejin",
        "platforms": ["juejin"],
    },
    "tophub": {
        "display": "今日热榜",
        "desc": "tophub.today 全网聚合，覆盖小红书/知乎等",
        "weight": 0.7,
        "independence": "tophub",
        "platforms": ["aggregate"],
    },
    # ── uapis 聚合源：按平台细分，每个平台是独立来源 ──
    # 注意：uapis 的 douyin 是抖音官方镜像，故不单独提供（避免虚假交叉验证）
    "uapis:weibo": {
        "display": "微博热搜",
        "desc": "微博热搜榜（经 uapis 聚合）",
        "weight": 0.9,
        "independence": "weibo",
        "platforms": ["weibo"],
    },
    "uapis:zhihu": {
        "display": "知乎热榜",
        "desc": "知乎热榜（经 uapis 聚合）",
        "weight": 0.85,
        "independence": "zhihu",
        "platforms": ["zhihu"],
    },
    "uapis:xiaohongshu": {
        "display": "小红书热搜",
        "desc": "小红书热搜榜（经 uapis 聚合）——**无需登录**",
        "weight": 0.9,
        "independence": "xiaohongshu",
        "platforms": ["xiaohongshu"],
    },
    "uapis:toutiao": {
        "display": "头条热榜",
        "desc": "今日头条热榜（经 uapis 聚合）",
        "weight": 0.85,
        "independence": "toutiao",
        "platforms": ["toutiao"],
    },
    "uapis:36kr": {
        "display": "36氪热榜",
        "desc": "36氪热榜（经 uapis 聚合），商业科技向",
        "weight": 0.8,
        "independence": "36kr",
        "platforms": ["36kr"],
    },
}

#: 默认源组合——刻意挑选**互相独立**的源，让交叉验证有意义
DEFAULT_SOURCES: list[str] = [
    "douyin",
    "baidu",
    "uapis:weibo",
    "uapis:zhihu",
    "uapis:xiaohongshu",
    "bilibili",
    "juejin",
    "tophub",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_hot_text(text: str) -> int | None:
    """把「1234万」「12.3w」这类热度文本转成整数。"""
    if not text:
        return None
    m = re.match(r"^([\d.]+)\s*([万亿wWkK]?)", text.strip())
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    mult = {"万": 10_000, "w": 10_000, "W": 10_000, "亿": 100_000_000, "k": 1_000, "K": 1_000}.get(unit, 1)
    return int(num * mult)


# --------------------------------------------------------------------------
# 各源实现
# --------------------------------------------------------------------------


async def fetch_douyin(client: HttpClient, *, limit: int) -> list[HotTopic]:
    """抖音官方热榜。实测无需登录、无需签名（2026-10-12，48 条）。"""
    url = (
        "https://www.douyin.com/aweme/v1/web/hot/search/list/"
        "?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1"
    )
    payload = await client.request_json(
        url,
        platform="douyin",
        headers={"Referer": "https://www.douyin.com/", "User-Agent": _USER_AGENT},
        cache_ttl=300.0,
    )
    words = (payload.get("data") or {}).get("word_list") or []
    out: list[HotTopic] = []
    for item in words[:limit]:
        word = item.get("word") or ""
        if not word:
            continue
        sid = item.get("sentence_id")
        out.append(
            HotTopic(
                platform="douyin",
                word=word,
                position=int(item.get("position") or len(out) + 1),
                hot_value=_to_int(item.get("hot_value")),
                topic_id=str(sid) if sid else None,
                url=f"https://www.douyin.com/hot/{sid}" if sid else "",
                captured_at=_now_iso(),
                raw=item,
            )
        )
    return out


async def fetch_baidu(client: HttpClient, *, limit: int) -> list[HotTopic]:
    """百度实时热搜。结构：data.cards[0].content[0].content[]（多嵌两层）。"""
    payload = await client.request_json(
        "https://top.baidu.com/api/board?platform=wise&tab=realtime",
        platform="baidu",
        headers={"User-Agent": _USER_AGENT},
        cache_ttl=300.0,
    )
    cards = (payload.get("data") or {}).get("cards") or []
    out: list[HotTopic] = []
    for card in cards:
        for wrapper in card.get("content") or []:
            for item in wrapper.get("content") or []:
                word = item.get("word") or item.get("query") or ""
                if not word:
                    continue
                out.append(
                    HotTopic(
                        platform="baidu",
                        word=word,
                        position=len(out) + 1,
                        hot_value=_to_int(item.get("hotScore")),
                        url=item.get("url") or item.get("rawUrl") or "",
                        captured_at=_now_iso(),
                        raw=item,
                    )
                )
                if len(out) >= limit:
                    return out
    return out


async def fetch_bilibili(client: HttpClient, *, limit: int) -> list[HotTopic]:
    """B站全站排行。"""
    payload = await client.request_json(
        "https://api.bilibili.com/x/web-interface/ranking/v2",
        platform="bilibili",
        headers={"User-Agent": _USER_AGENT, "Referer": "https://www.bilibili.com/"},
        cache_ttl=600.0,
    )
    items = (payload.get("data") or {}).get("list") or []
    out: list[HotTopic] = []
    for i, item in enumerate(items[:limit], start=1):
        title = item.get("title") or ""
        if not title:
            continue
        stat = item.get("stat") or {}
        bvid = item.get("bvid")
        out.append(
            HotTopic(
                platform="bilibili",
                word=title,
                position=i,
                hot_value=_to_int(stat.get("view")),
                topic_id=str(bvid) if bvid else None,
                url=item.get("short_link_v2") or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""),
                captured_at=_now_iso(),
                raw=item,
            )
        )
    return out


async def fetch_juejin(client: HttpClient, *, limit: int) -> list[HotTopic]:
    """掘金热榜。软件类账号强相关。"""
    payload = await client.request_json(
        "https://api.juejin.cn/content_api/v1/content/article_rank?category_id=1&type=hot",
        platform="juejin",
        headers={"User-Agent": _USER_AGENT},
        cache_ttl=600.0,
    )
    items = payload.get("data") or []
    out: list[HotTopic] = []
    for i, entry in enumerate(items[:limit], start=1):
        content = entry.get("content") or {}
        title = content.get("title") or ""
        if not title:
            continue
        counter = entry.get("content_counter") or {}
        cid = content.get("content_id")
        out.append(
            HotTopic(
                platform="juejin",
                word=title,
                position=i,
                hot_value=_to_int(counter.get("hot_rank") or counter.get("view")),
                topic_id=str(cid) if cid else None,
                url=f"https://juejin.cn/post/{cid}" if cid else "",
                captured_at=_now_iso(),
                raw=entry,
            )
        )
    return out


async def fetch_tophub(client: HttpClient, *, limit: int) -> list[HotTopic]:
    """今日热榜（tophub.today）。

    ━━━ 实测结构（2026-10-12，服务器端验证）━━━

    条目容器的类名是 ``cc-cd-cb-ll``（首页约 3000 处）。
    但**标题不在第一个 <a> 里** —— 它在 ``<span class="t">``：

        <div class="cc-cd-cb-ll">
          <span class="s h">1</span>
          <span class="t">瘦子菌！乐力SIIHO成人高活性冻干益生菌粉 原价¥39.9...</span>
          <span class="e">热销10000件(近2小时)</span>
        </div>

    早先版本按 ``<a>文本</a>`` 解析，结果**返回 0 条**（实测踩过）。

    ━━━ 另一个坑：首页混着电商商品 ━━━

    首页既有新闻热点，也有淘宝/京东的**商品推荐**。
    商品条目的特征是带价格（``¥``）或销量（``热销``）。
    这些不是热点，必须过滤——否则会污染选题池。

    聚合站的价值：覆盖我们没有直连能力的平台（小红书、知乎等）。
    """
    html = await client.request_text(
        "https://tophub.today/",
        platform="tophub",
        headers={"User-Agent": _USER_AGENT},
        cache_ttl=600.0,
    )
    out: list[HotTopic] = []
    seen: set[str] = set()

    for block in re.finditer(r'class="cc-cd-cb-ll"[^>]*>(.*?)</div>', html, re.S):
        body = block.group(1)

        # 标题在 span.t 里；取不到就退回第一个 <a> 的文本
        title = ""
        tm = re.search(r'<span[^>]*class="[^"]*\bt\b[^"]*"[^>]*>(.*?)</span>', body, re.S)
        if tm:
            title = re.sub(r"<[^>]+>", "", tm.group(1)).strip()
        if not title:
            am = re.search(r'<a[^>]+href="(?P<url>[^"]*)"[^>]*>(?P<text>.*?)</a>', body, re.S)
            if am:
                title = re.sub(r"<[^>]+>", "", am.group("text")).strip()

        if not title or title in seen:
            continue
        if _is_commerce_item(title):
            continue

        seen.add(title)

        # 链接：块内第一个 <a href>
        url = ""
        um = re.search(r'<a[^>]+href="(?P<url>[^"]*)"', body)
        if um:
            url = um.group("url")

        # 热度：span.s 里的排名数字，或 span.e 里的描述
        hot = None
        sm = re.search(r'<span[^>]*class="[^"]*\bs\b[^"]*"[^>]*>(.*?)</span>', body, re.S)
        if sm:
            hot = _parse_hot_text(re.sub(r"<[^>]+>", "", sm.group(1)).strip())

        out.append(
            HotTopic(
                platform="tophub",
                word=title,
                position=len(out) + 1,
                hot_value=hot,
                url=url,
                captured_at=_now_iso(),
            )
        )
        if len(out) >= limit:
            break
    return out


#: 电商条目特征——tophub 首页混着淘宝/京东商品推荐，必须剔除
_COMMERCE_MARKERS = ("原价¥", "券后¥", "¥", "热销", "包邮", "拍下", "立减", "优惠券")


def _is_commerce_item(title: str) -> bool:
    """判断是否为电商商品条目（非热点）。"""
    return any(m in title for m in _COMMERCE_MARKERS)


#: uapis 支持的平台类型 → 内部平台名
_UAPIS_TYPES: dict[str, str] = {
    "weibo": "weibo",
    "zhihu": "zhihu",
    "xiaohongshu": "xiaohongshu",
    "toutiao": "toutiao",
    "36kr": "36kr",
}


def _make_uapis_fetcher(uapis_type: str, platform_name: str) -> Callable[..., Awaitable[list[HotTopic]]]:
    """为每个 uapis 平台类型生成一个抓取函数。

    这样每个平台在注册表里都是独立源——独立性键不同，交叉验证时各算一票。
    """

    async def _fetch(client: HttpClient, *, limit: int) -> list[HotTopic]:
        payload = await client.request_json(
            f"https://uapis.cn/api/v1/misc/hotboard?type={uapis_type}",
            platform="uapis",
            headers={"User-Agent": _USER_AGENT},
            cache_ttl=300.0,
        )
        entries = payload.get("list") or payload.get("data") or []
        out: list[HotTopic] = []
        for i, item in enumerate(entries[:limit], start=1):
            if not isinstance(item, dict):
                continue
            word = item.get("title") or item.get("word") or ""
            if not word:
                continue
            out.append(
                HotTopic(
                    platform=platform_name,
                    word=word,
                    position=int(item.get("index") or i),
                    hot_value=_to_int(item.get("hot_value")),
                    url=item.get("url") or "",
                    captured_at=_now_iso(),
                    raw=item,
                )
            )
        return out

    return _fetch


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------

FETCHERS: dict[str, Callable[..., Awaitable[list[HotTopic]]]] = {
    "douyin": fetch_douyin,
    "baidu": fetch_baidu,
    "bilibili": fetch_bilibili,
    "juejin": fetch_juejin,
    "tophub": fetch_tophub,
    **{
        f"uapis:{t}": _make_uapis_fetcher(t, p)
        for t, p in _UAPIS_TYPES.items()
    },
}


def source_display(name: str) -> str:
    return SOURCE_META.get(name, {}).get("display", name)


def source_independence(name: str) -> str:
    return SOURCE_META.get(name, {}).get("independence", name)


async def fetch_source(client: HttpClient, source: str, *, limit: int) -> list[HotTopic]:
    fetcher = FETCHERS.get(source)
    if fetcher is None:
        raise ToolError(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"未知热点源：{source}",
            reason=f"可用源：{', '.join(sorted(FETCHERS))}",
        )
    return await fetcher(client, limit=limit)


async def fetch_many(
    client: HttpClient,
    sources: list[str] | None = None,
    *,
    limit_per_source: int = 50,
) -> tuple[list[HotTopic], list[dict[str, Any]]]:
    """并发抓取多个源。

    返回 (话题列表, 失败源列表)。**失败的源会被明确报告**——
    静默失败会让调用方误判「今天没热点」。
    """
    import asyncio

    #: 只把 None 当作「用默认」——空列表是调用方**显式**说了「不要源」，
    #: 静默替换成默认源会让结果与调用方的指定不符（实测踩过）。
    chosen = list(DEFAULT_SOURCES) if sources is None else list(sources)
    if not chosen:
        return [], []
    results = await asyncio.gather(
        *(fetch_source(client, s, limit=limit_per_source) for s in chosen),
        return_exceptions=True,
    )

    topics: list[HotTopic] = []
    failures: list[dict[str, Any]] = []
    for source, result in zip(chosen, results):
        if isinstance(result, BaseException):
            failures.append(
                {
                    "source": source,
                    "display_name": source_display(source),
                    "error": str(result),
                }
            )
            continue
        topics.extend(result)

    return topics, failures
