"""链路 A · 热点 × 账号定位 匹配打分。

━━━ 这个工具解决什么 ━━━

`hot_topics` 告诉你「现在什么火」，但**没告诉你哪个跟你的号有关**。
`topic_match` 补上这一环：把热点和账号定位做匹配，输出候选 + 理由。

━━━ 设计取舍：为什么先做词典版，而不是 embedding ━━━

embedding 语义匹配更准，但需要：下载模型（~100MB）、装 onnxruntime、
有 CPU/内存开销。而**词典版零依赖、可解释、立刻可用**。

更重要的是**可解释性**：词典版能说清「为什么匹配」——
命中的是哪个领域、哪个关键词。调用方（LLM）拿到理由后可以
自己判断合不合理，甚至推翻。embedding 版只能给一个分数，
是个黑盒数字，调用方无法验证。

所以：
  - 词典版（本文件）：默认，零依赖，带命中证据
  - embedding 版（可选）：需要时叠加语义相似度

━━━ 三信号融合 ━━━

匹配度不是单一分数，而是三个正交信号的加权：

1. **领域契合**（最高权重）：热点属于哪个领域？你的号在哪个领域？
2. **关键词命中**：热点的词是否直接命中你的业务词
3. **形式契合**：你的号发什么形态？（图文/视频）——热点是否适合该形态

三者分开报告，调用方可以只看其中一项。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..models import HotTopic

# --------------------------------------------------------------------------
# 领域词典
# --------------------------------------------------------------------------

#: 领域 → 特征词。命中越多，该热点越可能属于这个领域。
#:
#: 这些词是从用户两个账号的实际选题里提炼的（见知识库：
#: 抖音「小赵聊软件」= 软件/开发/避坑/获客；
#: 小红书「小赵无人零售」= 无人咖啡机/冰淇淋机/点位/无人零售）。
#: 通用领域（体育/娱乐/财经等）用于**反向排除**。
DOMAIN_LEXICON: dict[str, list[str]] = {
    # ── 用户实际业务领域 ──
    "software": [
        "软件", "开发", "编程", "代码", "程序员", "系统", "应用", "APP", "小程序",
        "AI", "人工智能", "大模型", "算法", "数据", "数字化", "智能化",
        "自动化", "工具", "效率", "办公", "SaaS", "开源", "框架",
        "部署", "运维", "服务器", "数据库", "接口", "API", "bug", "漏洞",
        "定制开发", "外包", "获客", "转化", "引流", "运营",
    ],
    "retail": [
        "零售", "无人", "自动售货", "咖啡机", "冰淇淋", "饮品", "现制",
        "便利店", "门店", "开店", "选址", "点位", "租金", "加盟",
        "供应链", "库存", "补货", "运营", "坪效", "客单价", "复购",
        "消费", "商圈", "商场", "景区", "学校", "医院", "写字楼",
    ],
    # ── 通用领域（主要用于反向排除）──
    "sports": [
        # 项目
        "体育", "网球", "足球", "篮球", "排球", "乒乓", "羽毛球", "田径", "游泳",
        "滑雪", "赛车", "电竞", "棋牌",
        # 赛事/机构
        "比赛", "夺冠", "退赛", "晋级", "淘汰", "奥运", "世锦赛", "公开赛",
        "联赛", "杯赛", "亚运", "全运", "国家队",
        # 身份
        "运动员", "教练", "球员", "选手", "国奥", "国足",
        # ⚠️ 实测补充：具体赛事名与人名——「郑钦文退出武网」里
        # 既没有「网球」也没有「比赛」，只有「武网」（武汉网球公开赛简称）。
        # 词典法依赖字面词，必须补这类专有名词，否则会漏判。
        "武网", "中网", "温网", "美网", "法网", "澳网", "中超", "英超", "西甲",
        "NBA", "CBA", "世界杯", "欧洲杯",
    ],
    "entertainment": ["明星", "演唱会", "电影", "电视剧", "综艺", "偶像", "粉丝", "官宣", "恋情"],
    "finance": ["股市", "A股", "股价", "基金", "理财", "加息", "降息", "汇率", "上市"],
    "politics": ["政策", "会议", "主席", "总理", "外交", "访问", "领导人", "政府"],
    "tech_hardware": ["手机", "显卡", "芯片", "电脑", "硬件", "电池", "屏幕", "发布"],
}

#: 领域显示名
DOMAIN_LABELS: dict[str, str] = {
    "software": "软件/科技",
    "retail": "零售/无人零售",
    "sports": "体育",
    "entertainment": "娱乐",
    "finance": "财经",
    "politics": "时政",
    "tech_hardware": "数码硬件",
}

#: 内容形态 → 特征词
FORM_LEXICON: dict[str, list[str]] = {
    "image_text": ["攻略", "教程", "清单", "盘点", "推荐", "怎么", "如何", "方法", "技巧", "指南"],
    "video": ["现场", "视频", "直播", "镜头", "拍摄", "记录"],
}


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------


@dataclass
class AccountProfile:
    """账号定位。调用方传入，或从知识库预设读取。"""

    name: str = ""
    """账号名，如「小赵聊软件」。"""

    keywords: list[str] = field(default_factory=list)
    """业务关键词。这是最强的匹配信号。"""

    domains: list[str] = field(default_factory=list)
    """所属领域（对应 DOMAIN_LEXICON 的键）。"""

    platforms: list[str] = field(default_factory=list)
    """在哪些平台。"""

    form: str = "image_text"
    """内容形态：image_text（图文）/ video（视频）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "keywords": self.keywords,
            "domains": self.domains,
            "platforms": self.platforms,
            "form": self.form,
        }


@dataclass
class TopicMatch:
    """一个匹配结果。"""

    word: str
    """热点标题（代表表述）。"""

    score: float
    """综合匹配度 0~1。"""

    level: str
    """可操性分级：strong（可做）/ maybe（勉强）/ weak（不建议）。"""

    reasons: list[str] = field(default_factory=list)
    """匹配的理由——**这是本工具的核心价值**，让调用方能验证。"""

    domain_scores: dict[str, float] = field(default_factory=dict)
    """各领域的契合度。"""

    hit_keywords: list[str] = field(default_factory=list)
    """命中的业务关键词。"""

    corroboration: str = ""
    """交叉验证级别（confirmed/likely/single），来自 hot_topics。"""

    source_count: int = 0
    """独立源数量。"""

    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.word,
            "score": round(self.score, 3),
            "level": self.level,
            "reasons": self.reasons,
            "domain_scores": {k: round(v, 3) for k, v in self.domain_scores.items()},
            "hit_keywords": self.hit_keywords,
            "corroboration": self.corroboration,
            "source_count": self.source_count,
            "url": self.url,
        }


# --------------------------------------------------------------------------
# 打分
# --------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """匹配用归一化：转小写、去噪。保留中文。"""
    text = (text or "").lower()
    text = re.sub(r"[\s#＃【】\[\]（）()「」“”\"'、,，。.！!？?~～\-—_·|｜:：;；]+", "", text)
    return text


def _domain_score(text: str, domain: str) -> float:
    """文本属于某领域的程度 0~1。

    用「命中词数 / 领域词表大小」的平滑版本——
    避免长词表天然得分低。
    """
    words = DOMAIN_LEXICON.get(domain, [])
    if not words:
        return 0.0
    hits = sum(1 for w in words if w.lower() in text)
    if hits == 0:
        return 0.0
    # 命中 1 个给 0.5，2 个给 0.75，3+ 接近 1（饱和）
    return min(1.0, 0.5 + 0.25 * (hits - 1) + 0.1 * max(0, hits - 2))


def _level_of(score: float) -> str:
    if score >= 0.6:
        return "strong"
    if score >= 0.35:
        return "maybe"
    return "weak"


def match_topics(
    topics: list[HotTopic],
    profile: AccountProfile,
    *,
    corroboration_of: dict[str, dict[str, Any]] | None = None,
    top_n: int = 10,
) -> list[TopicMatch]:
    """把热点与账号定位匹配打分。

    参数
    ----
    topics           : 热点列表（来自 hot_topics）
    profile          : 账号定位
    corroboration_of : 可选，标题 → 交叉验证信息（level / source_count）
                       来自 corroborate 的结果，用于把「热点强度」和
                       「匹配度」分开呈现——两件事不能混。
    top_n            : 返回前 N 个
    """
    results: list[TopicMatch] = []

    # ⚠️ 跨源去重：同一话题可能在多个源出现（实测「霸王茶姬店员劝退消费者」
    # 就重复了）。不去重会让候选池里塞满重复项，挤掉真正的候选。
    seen: set[str] = set()
    for t in topics:
        key = _normalize(t.word)
        if not key or key in seen:
            continue
        seen.add(key)
        text = _normalize(t.word)
        if not text:
            continue

        # --- 信号 1：领域契合 ---
        domain_scores: dict[str, float] = {}
        for d in set(profile.domains) | set(DOMAIN_LEXICON):
            s = _domain_score(text, d)
            if s > 0:
                domain_scores[d] = s

        own = [domain_scores.get(d, 0.0) for d in profile.domains]
        own_score = max(own) if own else 0.0

        # 反向信号：命中了**无关**领域会拉低匹配（如体育热点对软件号）
        other = [
            s for d, s in domain_scores.items() if d not in profile.domains
        ]
        noise = max(other) if other else 0.0

        # --- 信号 2：关键词命中（最强）---
        hit_keywords = [k for k in profile.keywords if _normalize(k) in text]
        kw_score = min(1.0, 0.4 * len(hit_keywords) + (0.3 if hit_keywords else 0.0))

        # --- 信号 3：形态契合 ---
        #
        # ⚠️ 实测踩过的坑：形态契合**不能独立给分**。
        # 只看「适合做图文」的话，「对对联」「养鸡场」这类完全无关的话题
        # 也会因为含「怎么/如何」拿到 0.15 分，污染候选池。
        #
        # 所以形态契合是**乘数**（放大已有信号），不是独立加分：
        # 只有在领域或关键词已经命中时，形态契合才起作用。
        form_words = FORM_LEXICON.get(profile.form, [])
        form_hit = any(w in text for w in form_words)

        # --- 融合 ---
        base = 0.45 * own_score + 0.40 * kw_score
        if base > 0 and form_hit:
            base *= 1.15  # 放大已有信号，最多 +15%

        score = base - 0.30 * noise * (0.0 if hit_keywords else 1.0)
        score = max(0.0, min(1.0, score))

        # --- 理由（核心价值）---
        reasons: list[str] = []
        if hit_keywords:
            reasons.append(f"直接命中业务词：{'、'.join(hit_keywords[:5])}")
        for d in profile.domains:
            if domain_scores.get(d, 0) >= 0.5:
                reasons.append(f"属于你的领域「{DOMAIN_LABELS.get(d, d)}」")
        if noise >= 0.5 and not hit_keywords:
            top_noise = max(
                (d for d in domain_scores if d not in profile.domains),
                key=lambda d: domain_scores[d],
            )
            reasons.append(
                f"偏向「{DOMAIN_LABELS.get(top_noise, top_noise)}」，与你的定位关联弱"
            )
        if form_hit and base > 0 and profile.form == "image_text":
            reasons.append("适合做成图文（攻略/教程类）")
        if not reasons:
            reasons.append("与账号定位无明显关联")

        info = (corroboration_of or {}).get(t.word, {})
        results.append(
            TopicMatch(
                word=t.word,
                score=score,
                level=_level_of(score),
                reasons=reasons,
                domain_scores=domain_scores,
                hit_keywords=hit_keywords,
                corroboration=info.get("level", ""),
                source_count=info.get("source_count", 0),
                url=t.url,
            )
        )

    results.sort(key=lambda m: -m.score)
    return results[:top_n]


# --------------------------------------------------------------------------
# 预设账号（来自知识库的实测定位）
# --------------------------------------------------------------------------

#: 预设账号定位。**来源**：知识库「抖音 小赵聊软件」「小红书 小赵无人零售」实测页。
PRESET_PROFILES: dict[str, AccountProfile] = {
    "小赵聊软件": AccountProfile(
        name="小赵聊软件",
        keywords=["软件", "开发", "定制开发", "获客", "避坑", "效率", "工具", "AI", "自动化", "小程序"],
        domains=["software"],
        platforms=["douyin"],
        form="image_text",
    ),
    "小赵无人零售": AccountProfile(
        name="小赵无人零售",
        keywords=["无人零售", "咖啡机", "冰淇淋机", "无人咖啡机", "点位", "租金", "开店", "零售"],
        domains=["retail"],
        platforms=["xiaohongshu"],
        form="image_text",
    ),
}


def get_preset(name: str) -> AccountProfile:
    p = PRESET_PROFILES.get(name)
    if p is None:
        raise KeyError(f"未知预设账号：{name}。可用：{', '.join(PRESET_PROFILES)}")
    return p
