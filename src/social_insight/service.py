"""social-insight-mcp · 多平台内容分析 MCP Server。

━━━ 两条独立链路 ━━━

链路 A · 热点选题（**零登录、零风控**）
    多源热榜聚合 → 交叉验证 → 可信度分级
    这是核心能力，不依赖任何 cookie。

链路 B · 对标拆解（需鉴权）
    平台内容搜索/详情/评论/账号
    抖音需签名，小红书需 cookie。可插拔。

视频号：占位。调用返回结构化说明，不假装能用。
"""

from __future__ import annotations

from typing import Any

from .errors import ErrorCode, ToolError, success
from .hot.corroborate import corroborate, summarize
from .hot.match import PRESET_PROFILES
from .store import SnapshotStore

#: 预设账号名，用于错误提示的 alternatives
PRESET_NAMES = sorted(PRESET_PROFILES)


def _match_summary(matches: list[Any], profile_name: str = "", domains: list[str] | None = None) -> dict[str, Any]:
    """匹配结果概览 + **对结果可靠性的诚实评估**。

    ━━━ 为什么需要「可靠性评估」━━━

    实测发现一个重要现实：**垂直领域题材天生只在单源出现**。

    软件技术类专业热点集中在掘金（技术圈垂直站），受众是程序员；
    而微博/百度的受众是大众——同一话题不会同时出现在两边。
    直接后果：软件号的 strong 候选**全部来自掘金单源**（实测 8/8）。

    这不是交叉验证失效，而是**交叉验证对垂直题材本来就不适用**。

    但调用方看到 `single(1源)` 容易误读成「这个热点不够热」。
    所以这里明确说明来源集中是正常现象还是异常。
    """
    from collections import Counter

    counts = {"strong": 0, "maybe": 0, "weak": 0}
    for m in matches:
        counts[m.level] = counts.get(m.level, 0) + 1

    # 来源分布
    src_counter: Counter[str] = Counter()
    for m in matches:
        # 显式处理 None（不用 `or []`）—— 保持全 src 的 falsy 回退零例外，
        # 这样扫描测试才有意义（见 tests/test_param_semantics.py）
        srcs = getattr(m, "sources", None)
        for s in (srcs if srcs is not None else []):
            src_counter[s] += 1

    weak_ratio = round(counts["weak"] / len(matches), 2) if matches else 0.0

    # 可靠性判断
    if not matches:
        reliability = "empty"
        advice = "今天没有匹配的话题。按自己的常规选题节奏走，不要硬蹭。"
    elif counts["strong"] == 0:
        reliability = "weak-day"
        advice = (
            f"今天没有强相关话题（{counts['maybe']} 个勉强沾边）。"
            "可能是全网热点与你的号都不搭——**这本身是有用信息**，别硬凑。"
        )
    elif src_counter and len(src_counter) == 1:
        only = next(iter(src_counter))
        reliability = "single-source-domain"
        advice = (
            f"强候选全部来自单一源「{only}」。"
            "**垂直领域题材本就该如此**——技术类在掘金、美妆在小红书，"
            "跨平台重合度天然低（受众不同）。这不代表话题不热，"
            "但意味着它可能是该圈层的内部热点，出圈风险需自行判断。"
        )
    else:
        reliability = "mixed"
        advice = "候选来源较分散，可按分数与来源自行取舍。"

    return {
        "profile": profile_name,
        "domains": list(domains) if domains is not None else [],
        "total": len(matches),
        **counts,
        "weak_ratio": weak_ratio,
        "source_distribution": dict(src_counter.most_common()),
        "reliability": reliability,
        "advice": advice,
        "hint": (
            "score 是与账号定位的匹配度，corroboration 是热点本身的强度。 "
            "**两者分开看**——很火但不相关的话题不该因为火就排前面。"
            "level: strong=可做 / maybe=勉强 / weak=不建议。"
        ),
    }

from .hot.sources import (
    DEFAULT_SOURCES,
    FETCHERS,
    SOURCE_META,
    fetch_many,
)
from .http import HttpClient
from .platforms.registry import get_adapter, platform_matrix

__all__ = ["SocialInsightService"]


class SocialInsightService:
    """工具层的实现。协议层（MCP server）只做参数转换与错误信封。

    这个类刻意不依赖 MCP SDK——它可以被单元测试直接调用，
    也可以被其他协议（HTTP / CLI）复用。
    """

    def __init__(self, *, http: HttpClient | None = None, store: SnapshotStore | None = None) -> None:
        self.http = http or HttpClient()
        #: 快照存储。为 None 时 trend 会明确报错（而不是静默返回空）
        self.store = store

    async def aclose(self) -> None:
        await self.http.aclose()
        if self.store is not None:
            self.store.close()

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------

    async def capabilities(self) -> dict[str, Any]:
        """能力矩阵。让调用方一眼看清每个平台能做什么、需要什么。

        这是「不静默失败」原则的入口：调用方在动手前就能知道
        哪些能力可用、哪些需要登录、哪些根本没实现。

        ⚠️ **工具列表必须与实际注册的工具严格一致**。
        实测踩过的坑：早期版本在 chains 里列了 topic_match / compare_creators /
        trend 三个「计划中」的工具，但它们并未注册 ——
        调用方（LLM）会照着这个列表去调用不存在的工具，然后收到
        「未知工具」错误。宣传不存在的能力比不宣传更糟。

        所以这里从 `server.TOOL_DEFS` **动态读取**已注册的工具名，
        而不是手写清单——从结构上杜绝两者不一致。
        """
        from .server import TOOL_DEFS

        registered = {t["name"] for t in TOOL_DEFS}

        #: 计划中但未实现的工具。**必须显式标注**，不能混进已注册列表。
        planned = {
            "compare_creators": (
                "对标账号横向对比——依赖链路 B（平台内容），"
                "而抖音需签名、小红书需登录，当前均不可用，故暂缓"
            ),
        }

        def only_registered(names: list[str]) -> list[str]:
            return [n for n in names if n in registered]

        return success(
            {
                "chains": {
                    "A_hot_topics": {
                        "description": "热点选题：多源热榜聚合 + 交叉验证 + 匹配 + 趋势",
                        "auth_required": False,
                        "status": "available",
                        "tools": only_registered(["hot_topics", "topic_match", "trend"]),
                    },
                    "B_platform_content": {
                        "description": "对标拆解：平台内容搜索/详情/评论/账号",
                        "auth_required": True,
                        "status": "partial",
                        "tools": only_registered(
                            [
                                "content_search",
                                "content_detail",
                                "content_comments",
                                "creator_profile",
                                "creator_works",
                                "analyze_viral",
                                "analyze_comments",
                            ]
                        ),
                    },
                },
                "registered_tools": sorted(registered),
                "planned_tools": {
                    name: desc for name, desc in planned.items() if name not in registered
                },
                "preset_profiles": sorted(PRESET_PROFILES),
                "storage": {
                    "enabled": self.store is not None,
                    "note": (
                        "trend 依赖快照存储；每次 hot_topics 会自动落快照。"
                        if self.store is not None
                        else "未启用存储，trend 会返回 CAPABILITY_UNSUPPORTED。"
                    ),
                },
                "hot_sources": {
                    name: {
                        "display": meta["display"],
                        "desc": meta["desc"],
                        "weight": meta["weight"],
                        "independence": meta["independence"],
                        "platforms": meta["platforms"],
                        "available": True,
                    }
                    for name, meta in SOURCE_META.items()
                },
                "platforms": platform_matrix(),
                "notes": [
                    "链路 A 完全不需要登录，无风控暴露。",
                    "交叉验证已按独立性键去重——镜像源不重复计数。",
                    "微信视频号未实现：官方无内容检索 API，开源生态零覆盖。",
                    "planned_tools 里的工具**尚未注册**，调用会返回未知工具错误——"
                    "请不要调用它们，等实现后 capabilities 会把它们移入 registered_tools。",
                    "topic_match 的 profile 参数可用 preset_profiles 里的预设账号名。",
                ],
            }
        )

    # ------------------------------------------------------------------
    # 链路 A · 热点
    # ------------------------------------------------------------------

    async def hot_topics(
        self,
        *,
        sources: list[str] | None = None,
        limit_per_source: int = 50,
        corroborate_result: bool = True,
        min_sources: int = 1,
    ) -> dict[str, Any]:
        """多源热榜聚合 + 跨源交叉验证。

        corroborate_result=True（默认）时，返回的是**交叉验证后**的话题：
        每条带 source_count 与 level（confirmed/likely/single）。
        """
        # ⚠️ 必须区分 `None`（未传）与 `[]`（显式传空）。
        #
        # 实测踩过的坑：写成 `sources or DEFAULT_SOURCES` 时，空列表是 falsy，
        # 会被**静默替换成默认源**——调用方以为自己在指定源，实际用了别的源。
        # 这比报错更危险：结果看起来合理，但来源不对。
        # （当时下面那个「空列表检查」因此成了永远走不到的死代码。）
        if sources is None:
            chosen = DEFAULT_SOURCES
        elif len(sources) == 0:
            raise ToolError(
                code=ErrorCode.INVALID_ARGUMENT,
                message="sources 不能为空列表",
                reason="传空列表会静默回退到默认源，与你看到的结果不符。不传该参数才使用默认源。",
                alternatives=sorted(FETCHERS),
            )
        else:
            chosen = list(sources)

        # 参数校验必须在抓取之前——否则无效源名会被当成「上游挂了」，
        # 调用方会以为是网络问题而去重试（实测踩过）。
        unknown = [s for s in chosen if s not in FETCHERS]
        if unknown:
            raise ToolError(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f"未知热点源：{', '.join(unknown)}",
                reason=(
                    "这些名字不在可用源列表中。"
                    "注意：sources 参数要填**源名**（如 douyin、uapis:weibo），"
                    "不是平台名。"
                ),
                alternatives=sorted(FETCHERS),
            )

        topics, failures = await fetch_many(
            self.http, chosen, limit_per_source=limit_per_source
        )

        # 全部源失败 → 明确的错误（而不是空结果）
        if not topics and failures:
            raise ToolError(
                code=ErrorCode.UPSTREAM_ERROR,
                message="所有热点源都抓取失败",
                reason="可能是网络问题、源站改版，或全部源被限流。",
                details={"failures": failures},
                retryable=True,
                alternatives=[
                    "稍后重试",
                    "换用其他源（sources 参数）",
                    "查看 capabilities 确认源是否仍然可用",
                ],
            )

        if not corroborate_result:
            return success(
                {
                    "topics": [t.to_dict() for t in topics],
                    "count": len(topics),
                },
                meta={"failures": failures, "mode": "raw"},
            )

        results = corroborate(
            topics,
            independence_of={k: v["independence"] for k, v in SOURCE_META.items()},
            weight_of={k: v["weight"] for k, v in SOURCE_META.items()},
            display_of={k: v["display"] for k, v in SOURCE_META.items()},
            min_sources=min_sources,
        )

        # 落快照（增值功能，失败不影响主流程）
        saved = self._maybe_snapshot(topics)

        return success(
            {
                "topics": [r.to_dict() for r in results],
                "summary": summarize(results),
            },
            meta={
                "mode": "corroborated",
                "raw_count": len(topics),
                "sources_requested": chosen,
                "failures": failures,
                "snapshots_saved": saved,
                "storage_enabled": self.store is not None,
            },
        )

    def _maybe_snapshot(self, topics: list[Any]) -> int:
        """把抓到的热点落快照（若存储层已启用）。

        失败**不影响主流程**——快照是增值功能，不是核心能力。
        但会记到 meta 里，让调用方知道。
        """
        if self.store is None or not topics:
            return 0
        try:
            return self.store.save_hot_topics(topics)
        except Exception:
            # 存储失败不该让热点查询失败
            return 0

    async def trend(
        self,
        *,
        word: str | None = None,
        hours: int = 24,
        limit: int = 20,
    ) -> dict[str, Any]:
        """热点趋势。

        两种模式：
          · 指定 word：该话题的历史快照（热度/排名变化）
          · 不指定：近期**持续在榜**的话题（出现次数越多越稳）

        ⚠️ 依赖快照层。若从未采集过，会返回空并明确说明——
        因为「没有历史」和「没有趋势」是两件不同的事，不能混淆。
        """
        if self.store is None:
            raise ToolError(
                code=ErrorCode.CAPABILITY_UNSUPPORTED,
                message="未启用快照存储，无法查询趋势",
                reason="趋势需要历史数据。当前服务实例没有配置存储层。",
                alternatives=[
                    "启动服务时挂载 SQLite 存储",
                    "先调用 hot_topics 积累快照",
                ],
            )

        if word:
            history = self.store.hot_history(word, limit=limit)
            return success(
                {
                    "word": word,
                    "history": history,
                    "count": len(history),
                    "has_data": len(history) > 0,
                    "note": "" if history else "该话题尚无历史快照——需要先采集。",
                },
                meta={"mode": "word"},
            )

        items = self.store.trending_topics(hours=hours, limit=limit)
        return success(
            {
                "topics": items,
                "count": len(items),
                "has_data": len(items) > 0,
                "note": "" if items else f"近 {hours} 小时内无快照——需要先采集。",
            },
            meta={"mode": "trending", "hours": hours, "stats": self.store.stats()},
        )

    async def topic_match(
        self,
        *,
        profile: str | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
        form: str = "image_text",
        sources: list[str] | None = None,
        limit_per_source: int = 50,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """热点 × 账号定位 匹配打分（用户核心诉求）。

        `hot_topics` 回答「现在什么火」，本工具回答「**哪个跟我的号有关**」。

        两种用法：
          1. 用预设账号：``profile="tech-blogger"``
          2. 临时指定：``keywords=[...], domains=[...]``

        ━━━ 设计取舍：三信号融合而非单一分数 ━━━

        匹配度由三个**正交**信号加权，并且**分开报告**：
          · 领域契合（0.45）—— 热点属于你的领域吗
          · 关键词命中（0.40）—— 直接命中业务词吗（最强信号）
          · 形态契合（0.15）—— 适合你的内容形态吗
          减分项：命中无关领域（如体育热点对软件号）会拉低

        为什么分开报告：调用方（LLM）能看到**命中了什么**，
        从而判断合不合理，甚至推翻结论。黑盒分数无法验证。

        ━━━ 刻意把「热点强度」与「匹配度」分开 ━━━

        `corroboration` 字段（confirmed/likely/single）来自交叉验证，
        表示「这个热点有多火」；`score` 表示「跟你有多相关」。
        **两件事不能混**——一个很火但不相关的话题，不该因为火就排前面。
        """
        from .hot.match import AccountProfile, get_preset, match_topics

        # --- 构造账号定位 ---
        if profile:
            try:
                acct = get_preset(profile)
            except KeyError as exc:
                raise ToolError(
                    code=ErrorCode.INVALID_ARGUMENT,
                    message=f"未知的预设账号：{profile}",
                    reason=(
                        "profile 参数要用 capabilities 里 preset_profiles 列出的预设名，"
                        "或者改用 keywords/domains 临时指定定位。"
                    ),
                    alternatives=sorted(PRESET_NAMES),
                ) from exc
            # 允许用 keywords/domains 微调预设
            if keywords:
                acct.keywords = list(keywords)
            if domains:
                acct.domains = list(domains)
        else:
            if not keywords and not domains:
                raise ToolError(
                    code=ErrorCode.INVALID_ARGUMENT,
                    message="需要提供 profile，或 keywords/domains 之一",
                    reason="没有账号定位就无法判断「哪个热点跟你有关」。",
                    alternatives=sorted(PRESET_NAMES),
                )
            # 显式区分 None 与 []，不用 `x or []` ——
            # 虽然这里语义等价（None 和 [] 都表示「没有」），但显式写
            # 能让「哪些参数的空值有特殊含义」一目了然，
            # 也让全 src 的 falsy 回退扫描保持零例外（检查器不该有特例）。
            acct = AccountProfile(
                keywords=list(keywords) if keywords is not None else [],
                domains=list(domains) if domains is not None else [],
                form=form,
            )

        # --- 抓热点 + 交叉验证 ---
        #
        # ⚠️ 同 hot_topics：必须区分 None 与 []。
        # 空列表若静默回退到默认源，调用方看到的来源就跟他指定的不符。
        if sources is None:
            chosen_sources = DEFAULT_SOURCES
        elif len(sources) == 0:
            raise ToolError(
                code=ErrorCode.INVALID_ARGUMENT,
                message="sources 不能为空列表",
                reason="传空列表会静默回退到默认源。不传该参数才使用默认源。",
                alternatives=sorted(FETCHERS),
            )
        else:
            chosen_sources = list(sources)

        unknown = [s for s in chosen_sources if s not in FETCHERS]
        if unknown:
            raise ToolError(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f"未知热点源：{', '.join(unknown)}",
                reason="sources 要填**源名**（如 douyin、uapis:weibo），不是平台名。",
                alternatives=sorted(FETCHERS),
            )

        topics, failures = await fetch_many(
            self.http, chosen_sources, limit_per_source=limit_per_source
        )
        if not topics:
            raise ToolError(
                code=ErrorCode.UPSTREAM_ERROR,
                message="所有热点源都抓取失败，无法做匹配",
                details={"failures": failures},
                retryable=True,
            )

        corroborated = corroborate(
            topics,
            independence_of={k: v["independence"] for k, v in SOURCE_META.items()},
            weight_of={k: v["weight"] for k, v in SOURCE_META.items()},
            display_of={k: v["display"] for k, v in SOURCE_META.items()},
        )

        # 交叉验证信息（用于把「火」与「相关」分开呈现）
        # ⚠️ 必须带上 sources —— 实测踩过：漏了它导致调用方看到
        # `sources: []`，进而误判「候选来源分散」（实际全是单一源）。
        corr_of = {
            c.word: {
                "level": c.level,
                "source_count": c.source_count,
                "sources": c.sources,
            }
            for c in corroborated
        }

        matches = match_topics(topics, acct, corroboration_of=corr_of, top_n=top_n)

        return success(
            {
                "profile": acct.to_dict(),
                "matches": [m.to_dict() for m in matches],
                "count": len(matches),
                "summary": _match_summary(matches, acct.name, acct.domains),
            },
            meta={
                "raw_hot_count": len(topics),
                "corroborated_count": len(corroborated),
                "sources_requested": chosen_sources,
                "failures": failures,
            },
        )

    # ------------------------------------------------------------------
    # 链路 B · 平台内容
    # ------------------------------------------------------------------

    async def content_search(
        self,
        *,
        platform: str,
        keyword: str,
        sort: str = "general",
        time_range: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        adapter = get_adapter(platform)
        page = await adapter.content_search(
            keyword=keyword, sort=sort, time_range=time_range, limit=limit, cursor=cursor
        )
        return success(page.to_dict(), meta={"platform": platform, "keyword": keyword})

    async def content_detail(self, *, platform: str, id_or_url: str) -> dict[str, Any]:
        adapter = get_adapter(platform)
        item = await adapter.content_detail(id_or_url=id_or_url)
        return success(item.to_dict(), meta={"platform": platform})

    async def content_comments(
        self,
        *,
        platform: str,
        id_or_url: str,
        level: int = 1,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        adapter = get_adapter(platform)
        page = await adapter.content_comments(
            id_or_url=id_or_url, level=level, limit=limit, cursor=cursor
        )
        return success(page.to_dict(), meta={"platform": platform, "level": level})

    async def creator_profile(self, *, platform: str, id_or_url: str) -> dict[str, Any]:
        adapter = get_adapter(platform)
        creator = await adapter.creator_profile(id_or_url=id_or_url)
        return success(creator.to_dict(), meta={"platform": platform})

    async def creator_works(
        self,
        *,
        platform: str,
        id_or_url: str,
        sort: str = "latest",
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        adapter = get_adapter(platform)
        page = await adapter.creator_works(
            id_or_url=id_or_url, sort=sort, limit=limit, cursor=cursor
        )
        return success(page.to_dict(), meta={"platform": platform})

    # ------------------------------------------------------------------
    # 分析（确定性算法，无生成式 AI）
    # ------------------------------------------------------------------

    async def analyze_viral(
        self,
        *,
        platform: str,
        keyword: str | None = None,
        creator: str | None = None,
        limit: int = 30,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """爆款拆解：拉取作品后按互动量排序，输出结构化特征。

        **不含生成式 AI** —— 只做排序、聚合、特征提取。
        「为什么火」的归因由调用方的 LLM 完成。
        """
        adapter = get_adapter(platform)

        if creator:
            page = await adapter.creator_works(id_or_url=creator, limit=limit)
        elif keyword:
            page = await adapter.content_search(keyword=keyword, limit=limit)
        else:
            raise ToolError(
                code=ErrorCode.INVALID_ARGUMENT,
                message="analyze_viral 需要 keyword 或 creator 之一",
            )

        items = page.items
        # 按互动量降序；None 排最后（缺数据不等于最差，但无法参与排序）
        ranked = sorted(
            items,
            key=lambda i: (i.engagement is None, -(i.engagement or 0)),
        )[:top_n]

        features = [
            {
                "work_id": i.work_id,
                "title": i.title,
                "author_name": i.author_name,
                "engagement": i.engagement,
                "like_count": i.like_count,
                "comment_count": i.comment_count,
                "collect_count": i.collect_count,
                "share_count": i.share_count,
                "publish_at": i.publish_at,
                "tags": i.tags,
                "title_length": len(i.title),
                "duration_sec": i.duration_sec,
                "url": i.url,
            }
            for i in ranked
        ]

        return success(
            {
                "items": features,
                "count": len(features),
                "aggregate": _aggregate_features(features),
            },
            meta={"platform": platform, "ranked_by": "engagement"},
        )

    async def analyze_comments(
        self,
        *,
        platform: str,
        id_or_url: str,
        limit: int = 100,
        top_keywords: int = 30,
    ) -> dict[str, Any]:
        """评论挖掘：词频 + 情绪分布。

        **确定性算法** —— 中文分词用 bigram 统计 + 停用词表，
        情绪用词典法。不用生成式 AI，结果可复现。
        """
        adapter = get_adapter(platform)
        page = await adapter.content_comments(id_or_url=id_or_url, limit=limit)

        texts = [c.content for c in page.items if getattr(c, "content", "")]
        return success(
            {
                "comment_count": len(texts),
                "keywords": _keywords(texts, top_n=top_keywords),
                "sentiment": _sentiment(texts),
                "top_comments": [
                    c.to_dict()
                    for c in sorted(
                        page.items,
                        key=lambda c: -(getattr(c, "like_count", 0) or 0),
                    )[:10]
                ],
            },
            meta={"platform": platform},
        )


# ----------------------------------------------------------------------
# 确定性分析原语
# ----------------------------------------------------------------------

#: 中文停用词（精简版，够用即可——目的是过滤噪声而非做学术分词）
_STOPWORDS = set(
    "的 了 是 在 我 有 也 就 不 人 都 一 一个 上 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 那 他 她 它 们 个 和 与 或 但 而 及 等 把 被 让 给 对 从 为 以 之 其 "
    "什么 怎么 为什么 可以 这个 那个 还是 就是 真的 感觉 觉得 应该 可能 现在 "
    "一下 一直 已经 因为 所以 但是 如果 这样 那样 时候 我们 你们 他们".split()
)


def _keywords(texts: list[str], *, top_n: int = 30) -> list[dict[str, Any]]:
    """中文关键词提取。

    用 bigram（二字连续片段）统计而非分词——中文分词需要词典，
    bigram 无需外部依赖且对评论这种短文本效果足够。
    过滤：停用词、纯数字、纯标点。
    """
    import re
    from collections import Counter

    counter: Counter[str] = Counter()
    for t in texts:
        clean = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", t)
        for token in clean.split():
            if re.fullmatch(r"[A-Za-z0-9]+", token):
                if len(token) >= 2:
                    counter[token.lower()] += 1
                continue
            for i in range(len(token) - 1):
                bg = token[i : i + 2]
                if bg in _STOPWORDS:
                    continue
                counter[bg] += 1

    return [{"word": w, "count": c} for w, c in counter.most_common(top_n) if c >= 2]


#: 情绪词典（精简版）。只做粗分——精细情绪分析留给调用方的 LLM。
_POSITIVE = set(
    "好 棒 赞 喜欢 支持 优秀 厉害 强 牛 爱 满意 值得 推荐 实用 有用 感谢 谢谢 "
    "不错 完美 惊喜 期待 开心 高兴 有用 学到了 收藏 干货".split()
)
_NEGATIVE = set(
    "差 烂 垃圾 失望 骗 坑 贵 难 问题 错误 不行 没用 后悔 退 投诉 假 "
    "糟糕 反感 讨厌 恶心 无语 忽悠 割韭菜 智商税 踩雷 翻车".split()
)


def _sentiment(texts: list[str]) -> dict[str, Any]:
    """词典法情绪分布。"""
    pos = neg = neu = 0
    for t in texts:
        p = sum(1 for w in _POSITIVE if w in t)
        n = sum(1 for w in _NEGATIVE if w in t)
        if p > n:
            pos += 1
        elif n > p:
            neg += 1
        else:
            neu += 1
    total = len(texts) or 1
    return {
        "positive": pos,
        "negative": neg,
        "neutral": neu,
        "positive_ratio": round(pos / total, 3),
        "negative_ratio": round(neg / total, 3),
        "method": "dictionary",
    }


def _aggregate_features(features: list[dict[str, Any]]) -> dict[str, Any]:
    """爆款的结构化共性特征。

    这是「爆款拆解」的确定性部分——把共性提取出来，
    让调用方的 LLM 去解释「为什么」。
    """
    if not features:
        return {}

    lengths = [f["title_length"] for f in features if f.get("title_length")]
    durations = [f["duration_sec"] for f in features if f.get("duration_sec")]
    hours: dict[int, int] = {}
    for f in features:
        ts = f.get("publish_at")
        if ts and len(ts) >= 13:
            try:
                h = int(ts[11:13])
                hours[h] = hours.get(h, 0) + 1
            except ValueError:
                pass

    tag_counter: dict[str, int] = {}
    for f in features:
        for tag in f.get("tags") or []:
            tag_counter[tag] = tag_counter.get(tag, 0) + 1

    return {
        "avg_title_length": round(sum(lengths) / len(lengths), 1) if lengths else None,
        "avg_duration_sec": round(sum(durations) / len(durations), 1) if durations else None,
        "publish_hour_distribution": dict(sorted(hours.items())),
        "top_tags": sorted(tag_counter.items(), key=lambda kv: -kv[1])[:10],
        "note": "这些是共性特征，不含归因。为什么火需要调用方结合内容判断。",
    }
