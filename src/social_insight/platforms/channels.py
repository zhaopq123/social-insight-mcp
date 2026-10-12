"""微信视频号适配器 —— **占位，不实现**。

━━━ 为什么占位而不实现（2026-10-12 调研结论）━━━

  ① 官方开放能力   ❌ 仅限自己账号
     微信开放文档「视频号」API 全清单只有：视频号助手 API（自己账号的
     直播记录/预约、橱窗商品、留资组件、直播大屏、罗盘达人版带货数据）。
     **无关键词检索、无他人作品与互动数据、无评论列表。**

  ② 搜一搜 / 搜狗   ❌ 无可编程接口
     weixin.sogou.com 只收录「订阅号及文章内容」，**不含视频号**。

  ③ 第三方数据平台   ⚠️ 有数据，但需商务采购
     友望数据、新视（新榜）确有视频号数据，但 API 需商务确认，
     且均未发现 MCP 形态。

  ④ 开源爬虫         ❌ 零覆盖
     MediaCrawler（66k★）平台表为 小红书/抖音/快手/B站/微博/贴吧/知乎，
     **无视频号**。

  ⑤ PC 微信 hook     ⚠️ 存在但高风险
     wxhelper 等通过逆向 + DLL 注入读取数据。**封号风险 + 违反用户协议
     + 法律风险（破坏计算机信息系统）**，本项目明确不采用。

━━━ 结论 ━━━

视频号是三平台中**难度最高、接近不可做**的。
唯一现实路径是付费第三方数据服务。

━━━ 为什么要保留这个占位 ━━━

调用方问起视频号时，能得到一个**诚实的、带解释的**答复，
而不是 404 或者一句「未知平台」。将来要接第三方数据源，
只需写一个新的 PlatformAdapter 子类替换掉它，
工具层和调用方一行都不用改。
"""

from __future__ import annotations

from .base import NotImplementedAdapter

_REASON = (
    "微信视频号未实现：官方开放 API 仅限自己账号的直播/橱窗/带货数据，"
    "无关键词检索、无他人作品、无评论列表；搜一搜无可编程接口；"
    "开源生态零覆盖（MediaCrawler 亦不支持）。"
)

_ALTERNATIVES = [
    "付费第三方数据服务：友望数据（youwant.cn）、新视（xs.newrank.cn）—— 需商务采购，API 未公开",
    "视频号助手（channels.weixin.qq.com）：仅自己账号数据，需扫码登录",
    "PC 微信 hook（wxhelper 等）：**不推荐**，有封号与法律风险",
    "若只需热点选题：用 hot_topics 覆盖抖音/小红书/微博等平台，无需视频号数据",
]


def build_channels_adapter() -> NotImplementedAdapter:
    return NotImplementedAdapter(
        platform="channels",
        display_name="微信视频号",
        reason=_REASON,
        alternatives=_ALTERNATIVES,
    )
