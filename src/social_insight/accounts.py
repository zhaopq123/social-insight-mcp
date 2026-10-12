"""账号定位配置。

━━━ 为什么要独立成文件 ━━━

**开源考虑**：源码里不应硬编码真实账号名与业务信息。
但工具又需要知道「你的号是干什么的」才能做匹配。

解法：把账号定义**外置到这个配置文件**。
  · 开源版本：仓库里只放通用示例（generic-*）
  · 自用版本：本地加一个 `accounts.local.json`（已被 .gitignore 忽略）

这样同一份代码既能开源，又能直接用。

━━━ 用法 ━━━

配置文件格式（JSON）::

    {
      "accounts": {
        "我的科技号": {
          "keywords": ["软件", "开发", "AI"],
          "domains": ["software", "business"],
          "platforms": ["douyin"],
          "form": "image_text"
        }
      }
    }

查找顺序：
  1. 环境变量 ``SOCIAL_INSIGHT_ACCOUNTS`` 指定的路径
  2. 项目根目录的 ``accounts.local.json``
  3. 内置的通用示例账号

━━━ 可用领域 ━━━

见 ``hot.match.DOMAIN_LEXICON`` 的键：
software / retail / business / sports / entertainment /
finance / politics / tech_hardware
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: 本地配置文件（不入库）
LOCAL_CONFIG_NAME = "accounts.local.json"

#: 环境变量覆盖
ENV_VAR = "SOCIAL_INSIGHT_ACCOUNTS"


#: 内置通用示例账号。
#:
#: ⚠️ 这些是**泛化的示例**，不含任何真实账号信息——
#: 它们演示「如何描述一个账号」，而不是某个具体的人。
#: 实际使用请写 ``accounts.local.json``。
GENERIC_ACCOUNTS: dict[str, dict[str, Any]] = {
    "tech-blogger": {
        "display_name": "科技/软件博主",
        "keywords": [
            "软件", "开发", "编程", "代码", "AI", "人工智能", "自动化",
            "工具", "效率", "系统", "小程序", "部署", "开源",
            "获客", "客户", "订单", "外包", "接单",
        ],
        "domains": ["software", "business"],
        "platforms": ["douyin"],
        "form": "image_text",
    },
    "retail-operator": {
        "display_name": "零售/无人设备运营",
        "keywords": [
            # 产品/设备维度
            "无人零售", "自动售货", "咖啡机", "冰淇淋机", "饮料机",
            "自助设备", "售货机", "点位", "设备",
            # 经营维度（**关键**：只写硬件会漏掉大量可做话题）
            "开店", "门店", "租金", "客流", "运营", "成本", "加盟",
            "零售", "消费", "品牌", "商家", "生意", "经营", "连锁",
        ],
        "domains": ["retail", "business"],
        "platforms": ["xiaohongshu"],
        "form": "image_text",
    },
    "parenting-blogger": {
        "display_name": "母婴/育儿博主",
        "keywords": [
            "育儿", "宝宝", "孩子", "教育", "辅食", "早教", "亲子",
            "母婴", "儿童", "健康", "成长", "入园",
        ],
        "domains": ["life", "business"],
        "platforms": ["xiaohongshu"],
        "form": "image_text",
    },
    "food-blogger": {
        "display_name": "美食/探店博主",
        "keywords": [
            "美食", "探店", "餐厅", "菜谱", "做饭", "小吃", "烘焙",
            "甜品", "茶饮", "咖啡", "食材", "味道",
        ],
        "domains": ["life", "business"],
        "platforms": ["xiaohongshu"],
        "form": "image_text",
    },
}


def _find_local_config() -> Path | None:
    """找本地账号配置。"""
    env_path = os.environ.get(ENV_VAR)
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return p

    # 从本文件往上找项目根（src/social_insight/accounts.py → 项目根）
    root = Path(__file__).resolve().parent.parent.parent
    candidate = root / LOCAL_CONFIG_NAME
    if candidate.is_file():
        return candidate
    return None


def load_accounts() -> dict[str, dict[str, Any]]:
    """加载账号定义。

    返回 ``{账号名: 定义}``。本地配置会**合并覆盖**内置示例。
    """
    accounts: dict[str, dict[str, Any]] = {
        name: dict(spec) for name, spec in GENERIC_ACCOUNTS.items()
    }

    path = _find_local_config()
    if path is None:
        return accounts

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 配置文件损坏不该让服务起不来——退回内置示例
        return accounts

    local = raw.get("accounts") if isinstance(raw, dict) else None
    if isinstance(local, dict):
        for name, spec in local.items():
            if isinstance(spec, dict):
                accounts[name] = dict(spec)
    return accounts


def config_source() -> str:
    """返回当前账号配置的来源说明（用于诊断）。"""
    path = _find_local_config()
    if path is not None:
        return f"local:{path}"
    return "builtin:generic"
