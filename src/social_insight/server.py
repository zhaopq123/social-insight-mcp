"""MCP 协议层。

━━━ 职责边界 ━━━

这一层只做三件事：
  1. 工具注册与 JSON Schema 声明
  2. 参数透传到 service 层
  3. 把异常转成结构化错误信封

**不含任何业务逻辑**——这样 service 层可以被单元测试直接调用，
也可以被其他协议（HTTP / CLI）复用。

━━━ 工具面设计 ━━━

「一个动词一个工具 + platform 参数」，而不是「一个能力一个工具」。
有实测结论指出：工具数量每增加一个，agent 选对工具的概率都会
可测量地下降。所以宁可让参数复杂一点，也不要堆工具。
"""

from __future__ import annotations

import json
from typing import Any

from .errors import ErrorCode, ToolError
from .hot.sources import SOURCE_META
from .service import SocialInsightService

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    MCP_AVAILABLE = False


def _schema(**props: Any) -> dict[str, Any]:
    """构造 JSON Schema。required 由参数是否带默认值推断。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in props.items():
        if isinstance(spec, tuple):
            # (type, description, required?)
            t, desc = spec[0], spec[1]
            is_req = spec[2] if len(spec) > 2 else False
            properties[name] = {"type": t, "description": desc}
            if is_req:
                required.append(name)
        else:
            properties[name] = spec
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


#: 平台枚举——用于 schema 提示
_PLATFORM_DESC = (
    "平台名。可选：douyin（抖音）、xiaohongshu（小红书）、channels（微信视频号，未实现）。"
    "也接受中文别名：抖音/小红书/视频号。"
)

#: 热点源枚举
_SOURCE_DESC = (
    "热点源列表。可用：" + "、".join(f"{k}" for k in SOURCE_META) + "。"
    "不传则用默认组合（刻意挑选互相独立的源）。"
)


TOOL_DEFS: list[dict[str, Any]] = [
    # ─────────────── 元信息 ───────────────
    {
        "name": "capabilities",
        "description": (
            "查询本服务的完整能力矩阵：两条链路（热点选题 / 对标拆解）、"
            "每个平台支持哪些能力、各自需要什么鉴权级别、当前可用状态。"
            "**建议在任何平台相关操作前先调用一次**——它会告诉你"
            "哪些能力现在能用、哪些需要登录、哪些根本没实现（如微信视频号）。"
        ),
        "inputSchema": _schema(),
    },
    # ─────────────── 链路 A · 热点 ───────────────
    {
        "name": "hot_topics",
        "description": (
            "多源热榜聚合 + **跨源交叉验证**（核心能力，无需登录）。\n\n"
            "从多个互相独立的平台抓取热榜，按话题聚类，统计每个话题被"
            "多少个独立源报道，给出可信度分级：\n"
            "  · confirmed —— ≥3 个独立源同时报道，强信号\n"
            "  · likely    —— 2 个独立源\n"
            "  · single    —— 仅 1 个源，可能是该站特有\n\n"
            "**已按独立性去重**：镜像源（如 uapis 的抖音榜 = 抖音官方榜）"
            "不会重复计数，避免虚假信心。\n\n"
            "这是选题的起点：先看哪些话题是全网在火的，再判断哪个适合你的账号。"
        ),
        "inputSchema": _schema(
            sources=("array", _SOURCE_DESC + " 元素为字符串。"),
            limit_per_source=("integer", "每个源最多取多少条（默认 50，上限 100）。"),
            min_sources=("integer", "只返回被至少这么多独立源报道的话题（默认 1）。设 2 可只看交叉验证过的。"),
        ),
    },
    {
        "name": "topic_match",
        "description": (
            "**热点 × 账号定位 匹配打分**（选题闭环的核心一步）。\n\n"
            "`hot_topics` 回答「现在什么火」，本工具回答"
            "「**哪个跟我的号有关**」。\n\n"
            "两种用法：\n"
            "  · 用预设账号：profile=\"tech-blogger\" 或 \"retail-operator\"\n"
            "  · 临时指定：keywords=[...], domains=[...]\n\n"
            "匹配度由三个正交信号加权，且**分开报告命中证据**：\n"
            "  · 领域契合 0.45 —— 属于你的领域吗\n"
            "  · 关键词命中 0.40 —— 直接命中业务词吗（最强）\n"
            "  · 形态契合 0.15 —— 适合你的内容形态吗\n"
            "  减分：命中无关领域（如体育热点对软件号）会拉低\n\n"
            "**刻意把「热点强度」与「匹配度」分开**：\n"
            "corroboration（confirmed/likely/single）表示热点有多火，\n"
            "score 表示跟你有多相关。很火但不相关的话题不该因为火就排前面。"
        ),
        "inputSchema": _schema(
            profile=("string", "预设账号名：tech-blogger / retail-operator。调用 capabilities 可看全部。"),
            keywords=("array", "业务关键词列表。不传 profile 时必填。元素为字符串。"),
            domains=(
                "array",
                "领域列表。可选值：software（软件/科技）、retail（零售/无人零售）、"
                "sports、entertainment、finance、politics、tech_hardware。",
            ),
            form=("string", "内容形态：image_text（图文，默认）/ video（视频）。"),
            sources=("array", _SOURCE_DESC + " 元素为字符串。"),
            limit_per_source=("integer", "每个源取多少条（默认 50）。"),
            top_n=("integer", "返回前 N 个候选（默认 10）。"),
        ),
    },
    {
        "name": "trend",
        "description": (
            "热点趋势查询。\n\n"
            "两种模式：\n"
            "  · 指定 word：该话题的历史快照（热度/排名变化）\n"
            "  · 不指定：近期**持续在榜**的话题（出现次数越多越稳）\n\n"
            "⚠️ 依赖快照层：每次调用 hot_topics 会自动落快照。"
            "若从未采集过，会返回空并明确说明——"
            "「没有历史」和「没有趋势」是两件不同的事。"
        ),
        "inputSchema": _schema(
            word=("string", "要查趋势的话题。不传则返回近期持续在榜的话题列表。"),
            hours=("integer", "时间窗口（小时，默认 24）。仅对列表模式有效。"),
            limit=("integer", "返回条数（默认 20）。"),
        ),
    },
    # ─────────────── 链路 B · 平台内容 ───────────────
    {
        "name": "content_search",
        "description": (
            "按关键词搜索平台内容（作品/笔记）。\n\n"
            "注意：抖音搜索需 a_bogus 签名、小红书需登录 cookie，"
            "两者当前都可能返回结构化错误说明原因。"
        ),
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            keyword=("string", "搜索关键词。", True),
            sort=("string", "排序：general（综合）/ latest（最新）/ hottest（最热）。"),
            time_range=("string", "时间范围：day / week / month。"),
            limit=("integer", "返回条数（默认 20，上限 50）。"),
            cursor=("string", "分页游标。原样回传上一页返回的值，不要解析它。"),
        ),
    },
    {
        "name": "content_detail",
        "description": (
            "获取单条内容详情。可传作品 ID 或完整 URL。\n\n"
            "小红书注意：详情必须携带搜索结果里的 xsec_token，"
            "所以调用顺序被强制——先 content_search，再 content_detail。"
        ),
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            id_or_url=("string", "作品 ID 或完整分享链接。", True),
        ),
    },
    {
        "name": "content_comments",
        "description": "获取某条内容的评论列表。用于舆情分析与需求点挖掘。",
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            id_or_url=("string", "作品 ID 或完整分享链接。", True),
            level=("integer", "1=一级评论（默认），2=含二级评论。"),
            limit=("integer", "返回条数（默认 20，上限 100）。"),
            cursor=("string", "分页游标。"),
        ),
    },
    {
        "name": "creator_profile",
        "description": "获取创作者主页信息（昵称、简介、粉丝数、作品数）。做对标分析时用。",
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            id_or_url=("string", "创作者 ID 或主页链接。", True),
        ),
    },
    {
        "name": "creator_works",
        "description": "获取某创作者的作品列表。对标账号拆解的主力工具。",
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            id_or_url=("string", "创作者 ID 或主页链接。", True),
            sort=("string", "排序：latest（最新，默认）/ hottest（最热）。"),
            limit=("integer", "返回条数（默认 20，上限 50）。"),
            cursor=("string", "分页游标。"),
        ),
    },
    # ─────────────── 分析（确定性算法）───────────────
    {
        "name": "analyze_viral",
        "description": (
            "**爆款拆解**：拉取一批作品，按互动量排序，输出结构化共性特征"
            "（平均标题长度、时长、发布时段分布、高频标签）。\n\n"
            "注意：本工具做的是**确定性统计**，不含生成式 AI。"
            "它把「共性」提取出来，但「为什么火」的归因需要你（调用方）结合内容判断。"
        ),
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            keyword=("string", "按关键词搜索作品。与 creator 二选一。"),
            creator=("string", "按创作者拉作品。与 keyword 二选一。"),
            limit=("integer", "拉取样本量（默认 30）。"),
            top_n=("integer", "输出前 N 条爆款（默认 10）。"),
        ),
    },
    {
        "name": "analyze_comments",
        "description": (
            "**评论挖掘**：词频统计 + 情绪分布 + 高赞评论。\n\n"
            "确定性算法（bigram 词频 + 情绪词典），结果可复现，不含生成式 AI。"
            "适合挖掘用户真实需求点、痛点、反对意见。"
        ),
        "inputSchema": _schema(
            platform=("string", _PLATFORM_DESC, True),
            id_or_url=("string", "作品 ID 或完整分享链接。", True),
            limit=("integer", "抓取评论数（默认 100）。"),
            top_keywords=("integer", "返回关键词数（默认 30）。"),
        ),
    },
]


def build_server(service: SocialInsightService | None = None) -> Any:
    """构造 MCP Server。"""
    if not MCP_AVAILABLE:
        raise RuntimeError("未安装 mcp SDK。请运行：pip install mcp")

    svc = service or SocialInsightService()
    server = Server("social-insight-mcp")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOL_DEFS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        try:
            result = await _dispatch(svc, name, arguments or {})
        except ToolError as exc:
            # 结构化错误——调用方能读到 code / reason / alternatives
            result = exc.to_dict()
        except Exception as exc:  # 兜底：绝不让调用方收到裸异常
            result = ToolError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"内部错误：{type(exc).__name__}",
                reason=str(exc)[:500],
            ).to_dict()

        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2),
            )
        ]

    return server


async def _dispatch(svc: SocialInsightService, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """工具名 → service 方法。参数透传，不做业务判断。"""
    if name == "capabilities":
        return await svc.capabilities()
    if name == "hot_topics":
        return await svc.hot_topics(
            sources=args.get("sources"),
            limit_per_source=args.get("limit_per_source", 50),
            min_sources=args.get("min_sources", 1),
        )
    if name == "topic_match":
        return await svc.topic_match(
            profile=args.get("profile"),
            keywords=args.get("keywords"),
            domains=args.get("domains"),
            form=args.get("form", "image_text"),
            sources=args.get("sources"),
            limit_per_source=args.get("limit_per_source", 50),
            top_n=args.get("top_n", 10),
        )
    if name == "trend":
        return await svc.trend(
            word=args.get("word"),
            hours=args.get("hours", 24),
            limit=args.get("limit", 20),
        )
    if name == "content_search":
        return await svc.content_search(
            platform=args["platform"],
            keyword=args["keyword"],
            sort=args.get("sort", "general"),
            time_range=args.get("time_range"),
            limit=args.get("limit", 20),
            cursor=args.get("cursor"),
        )
    if name == "content_detail":
        return await svc.content_detail(platform=args["platform"], id_or_url=args["id_or_url"])
    if name == "content_comments":
        return await svc.content_comments(
            platform=args["platform"],
            id_or_url=args["id_or_url"],
            level=args.get("level", 1),
            limit=args.get("limit", 20),
            cursor=args.get("cursor"),
        )
    if name == "creator_profile":
        return await svc.creator_profile(platform=args["platform"], id_or_url=args["id_or_url"])
    if name == "creator_works":
        return await svc.creator_works(
            platform=args["platform"],
            id_or_url=args["id_or_url"],
            sort=args.get("sort", "latest"),
            limit=args.get("limit", 20),
            cursor=args.get("cursor"),
        )
    if name == "analyze_viral":
        return await svc.analyze_viral(
            platform=args["platform"],
            keyword=args.get("keyword"),
            creator=args.get("creator"),
            limit=args.get("limit", 30),
            top_n=args.get("top_n", 10),
        )
    if name == "analyze_comments":
        return await svc.analyze_comments(
            platform=args["platform"],
            id_or_url=args["id_or_url"],
            limit=args.get("limit", 100),
            top_keywords=args.get("top_keywords", 30),
        )

    raise ToolError(
        code=ErrorCode.INVALID_ARGUMENT,
        message=f"未知工具：{name}",
        reason=f"可用工具：{', '.join(t['name'] for t in TOOL_DEFS)}",
    )


async def run_stdio() -> None:
    """以 stdio 传输启动——给本地 MCP 客户端用。"""
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio

    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
