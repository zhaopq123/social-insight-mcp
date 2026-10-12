"""MCP HTTP 传输入口（streamable-http）。

━━━ 为什么需要这个文件 ━━━

``server.py`` 走 stdio —— 那是本地客户端的方式。
部署到服务器后，DSH 需要通过网络访问，必须用 streamable-http。

━━━ 为什么用 fastmcp 而不是裸 mcp SDK ━━━

目标服务器（vivo 192.168.3.35 的 Ubuntu 容器）上已经跑着 mcp-imagegen，
用的是 ``fastmcp 4.1.0`` + ``uvicorn``，**版本组合已在真机验证过**。
复用同一套依赖，避免「在我机器上能跑」的问题。

━━━ 设计：工具定义单一来源 ━━━

工具的参数说明写在 ``server.TOOL_DEFS`` 里（JSON Schema），
这里只做「Python 签名 → dispatch」的映射。
两边共用 ``server._dispatch``，不会出现「stdio 与 http 行为不一致」。

━━━ 部署形态（proot 环境的硬约束）━━━

服务器是 Termux + proot-distro Ubuntu 双层结构：
  · **没有 systemd** —— ``systemctl`` 不存在
  · proot 带 ``--kill-on-exit`` —— **后台脱离型进程活不下来**

所以服务必须**前台运行**，由 Termux 层的守护循环拉起：

    while true; do
      proot-distro login ubuntu -- /opt/social-insight-mcp/start-mcp.sh
      sleep 10
    done
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from .errors import ErrorCode, ToolError
from .service import SocialInsightService
from .store import SnapshotStore

logger = logging.getLogger("social_insight.http")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8100

#: 快照数据库路径。可用环境变量 SOCIAL_INSIGHT_DB 覆盖。
DEFAULT_DB_PATH = "data/snapshots.db"


def default_service() -> SocialInsightService:
    """构造默认服务实例（启用快照存储）。

    ⚠️ 实测踩过的坑：若不启用存储层，trend 工具会永远报
    「未启用快照存储」——因为没有任何地方落快照。
    所以默认就启用，让趋势能力真正可用。

    存储初始化失败**不让服务启动失败**——快照是增值功能，
    核心的热点查询不该被拖垮。降级为「无存储」并打日志。
    """
    import logging
    import os

    db_path = os.environ.get("SOCIAL_INSIGHT_DB", DEFAULT_DB_PATH)
    store = None
    try:
        store = SnapshotStore(db_path)
        logging.getLogger("social_insight.http").info("快照存储已启用: %s", db_path)
    except Exception as exc:
        logging.getLogger("social_insight.http").warning(
            "快照存储启用失败（trend 将不可用）: %s", exc
        )
    return SocialInsightService(store=store)


def build_app(service: SocialInsightService | None = None) -> Any:
    """构造 FastMCP ASGI 应用。"""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "缺少 fastmcp。请安装：pip install fastmcp uvicorn\n"
            f"原始错误：{exc}"
        ) from exc

    svc = service or default_service()
    mcp = FastMCP("social-insight-mcp")

    # ─────────────────── 元信息 ───────────────────
    @mcp.tool()
    async def capabilities() -> dict:
        """查询本服务的完整能力矩阵。

        返回两条链路（热点选题 / 对标拆解）、每个平台支持哪些能力、
        各自需要什么鉴权级别、当前可用状态。

        **建议在任何平台相关操作前先调用一次** —— 它会告诉你哪些能力
        现在能用、哪些需要登录、哪些根本没实现（如微信视频号）。
        """
        return await _safe(svc.capabilities())

    # ─────────────────── 链路 A · 热点 ───────────────────
    @mcp.tool()
    async def hot_topics(
        sources: list[str] | None = None,
        limit_per_source: int = 50,
        min_sources: int = 1,
    ) -> dict:
        """多源热榜聚合 + 跨源交叉验证（核心能力，无需登录）。

        从多个互相独立的平台抓取热榜，按话题聚类，统计每个话题被多少个
        独立源报道，给出可信度分级：
          · confirmed —— ≥3 个独立源同时报道，强信号
          · likely    —— 2 个独立源
          · single    —— 仅 1 个源，可能是该站特有

        **已按独立性去重**：镜像源（如 uapis 的抖音榜 = 抖音官方榜）
        不会重复计数，避免虚假信心。

        sources 参数填**源名**（如 douyin、uapis:weibo、uapis:xiaohongshu），
        不填则用默认组合。调用 capabilities 可看到全部可用源。
        min_sources 设 2 可只看交叉验证过的话题。
        """
        return await _safe(
            svc.hot_topics(
                sources=sources,
                limit_per_source=limit_per_source,
                min_sources=min_sources,
            )
        )

    # ─────────────────── 链路 A · 匹配 ───────────────────
    @mcp.tool()
    async def topic_match(
        profile: str | None = None,
        keywords: list[str] | None = None,
        domains: list[str] | None = None,
        form: str = "image_text",
        sources: list[str] | None = None,
        limit_per_source: int = 50,
        top_n: int = 10,
    ) -> dict:
        """热点 × 账号定位 匹配打分（选题闭环的核心一步）。

        hot_topics 回答「现在什么火」，本工具回答「**哪个跟我的号有关**」。

        两种用法：
          · 用预设账号：profile="小赵聊软件" 或 "小赵无人零售"
          · 临时指定：keywords=[...], domains=[...]

        匹配度由三个正交信号加权，且**分开报告命中证据**：
          · 领域契合 0.45 —— 属于你的领域吗
          · 关键词命中 0.40 —— 直接命中业务词吗（最强）
          · 形态契合 0.15 —— 适合你的内容形态吗
          减分：命中无关领域（如体育热点对软件号）会拉低

        **刻意把「热点强度」与「匹配度」分开**：
        corroboration（confirmed/likely/single）表示热点有多火，
        score 表示跟你有多相关。很火但不相关的话题不该因为火就排前面。
        """
        return await _safe(
            svc.topic_match(
                profile=profile,
                keywords=keywords,
                domains=domains,
                form=form,
                sources=sources,
                limit_per_source=limit_per_source,
                top_n=top_n,
            )
        )

    # ─────────────────── 链路 A · 趋势 ───────────────────
    @mcp.tool()
    async def trend(
        word: str | None = None,
        hours: int = 24,
        limit: int = 20,
    ) -> dict:
        """热点趋势查询。

        两种模式：
          · 指定 word：该话题的历史快照（热度/排名变化）
          · 不指定：近期**持续在榜**的话题（出现次数越多越稳）

        ⚠️ 依赖快照层：每次调用 hot_topics 会自动落快照。
        若从未采集过，会返回空并明确说明——
        「没有历史」和「没有趋势」是两件不同的事。
        """
        return await _safe(svc.trend(word=word, hours=hours, limit=limit))

    # ─────────────────── 链路 B · 平台内容 ───────────────────
    @mcp.tool()
    async def content_search(
        platform: str,
        keyword: str,
        sort: str = "general",
        time_range: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """按关键词搜索平台内容（作品/笔记）。

        platform 可选：douyin（抖音）、xiaohongshu（小红书）、
        channels（微信视频号，未实现）。也接受中文别名。

        注意：抖音搜索需 a_bogus 签名、小红书需登录 cookie，
        两者当前都可能返回结构化错误说明原因与替代路径。
        """
        return await _safe(
            svc.content_search(
                platform=platform,
                keyword=keyword,
                sort=sort,
                time_range=time_range,
                limit=limit,
                cursor=cursor,
            )
        )

    @mcp.tool()
    async def content_detail(platform: str, id_or_url: str) -> dict:
        """获取单条内容详情。可传作品 ID 或完整 URL。

        小红书注意：详情必须携带搜索结果里的 xsec_token，
        所以调用顺序被强制 —— 先 content_search，再 content_detail。
        """
        return await _safe(svc.content_detail(platform=platform, id_or_url=id_or_url))

    @mcp.tool()
    async def content_comments(
        platform: str,
        id_or_url: str,
        level: int = 1,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """获取某条内容的评论列表。用于舆情分析与需求点挖掘。

        level=1 取一级评论，level=2 含二级评论。
        """
        return await _safe(
            svc.content_comments(
                platform=platform,
                id_or_url=id_or_url,
                level=level,
                limit=limit,
                cursor=cursor,
            )
        )

    @mcp.tool()
    async def creator_profile(platform: str, id_or_url: str) -> dict:
        """获取创作者主页信息（昵称、简介、粉丝数、作品数）。做对标分析时用。"""
        return await _safe(svc.creator_profile(platform=platform, id_or_url=id_or_url))

    @mcp.tool()
    async def creator_works(
        platform: str,
        id_or_url: str,
        sort: str = "latest",
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """获取某创作者的作品列表。对标账号拆解的主力工具。"""
        return await _safe(
            svc.creator_works(
                platform=platform,
                id_or_url=id_or_url,
                sort=sort,
                limit=limit,
                cursor=cursor,
            )
        )

    # ─────────────────── 分析（确定性算法）───────────────────
    @mcp.tool()
    async def analyze_viral(
        platform: str,
        keyword: str | None = None,
        creator: str | None = None,
        limit: int = 30,
        top_n: int = 10,
    ) -> dict:
        """爆款拆解：拉取一批作品，按互动量排序，输出结构化共性特征
        （平均标题长度、时长、发布时段分布、高频标签）。

        注意：本工具做的是**确定性统计**，不含生成式 AI。
        它把「共性」提取出来，但「为什么火」的归因需要你结合内容判断。

        keyword 与 creator 二选一。
        """
        return await _safe(
            svc.analyze_viral(
                platform=platform,
                keyword=keyword,
                creator=creator,
                limit=limit,
                top_n=top_n,
            )
        )

    @mcp.tool()
    async def analyze_comments(
        platform: str,
        id_or_url: str,
        limit: int = 100,
        top_keywords: int = 30,
    ) -> dict:
        """评论挖掘：词频统计 + 情绪分布 + 高赞评论。

        确定性算法（bigram 词频 + 情绪词典），结果可复现，不含生成式 AI。
        适合挖掘用户真实需求点、痛点、反对意见。
        """
        return await _safe(
            svc.analyze_comments(
                platform=platform,
                id_or_url=id_or_url,
                limit=limit,
                top_keywords=top_keywords,
            )
        )

    return mcp


async def _safe(coro: Any) -> dict:
    """把 ToolError 转成结构化信封，绝不抛裸异常给调用方。

    与 server.py 的 stdio 路径行为一致 —— 这是刻意的：
    同一个工具不该因为传输方式不同而有不同的错误语义。
    """
    try:
        return await coro
    except ToolError as exc:
        return exc.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("工具执行异常")
        return ToolError(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"内部错误：{type(exc).__name__}",
            reason=str(exc)[:500],
        ).to_dict()


def main() -> None:
    """入口。环境变量：HOST / PORT / LOG_LEVEL。"""
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", DEFAULT_PORT))

    mcp = build_app()

    # FastMCP 4.x：http_app() 返回 streamable-http 的 ASGI 应用
    app = mcp.http_app(path="/mcp")

    logger.info("social-insight-mcp 启动于 http://%s:%d/mcp", host, port)
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
