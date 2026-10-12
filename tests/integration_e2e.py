"""端到端验证。

覆盖三件事：
  1. capabilities 能力矩阵完整
  2. 链路 A（热点 + 交叉验证）真实可用
  3. 视频号占位返回**诚实的结构化错误**，不是 404 也不是空数据
  4. 小红书/抖音的未实现能力返回带解释的错误
"""

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.errors import ToolError
from social_insight.service import SocialInsightService


def show(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


async def main() -> int:
    svc = SocialInsightService()
    failures: list[str] = []

    # ---------- 1. capabilities ----------
    show("1. capabilities —— 能力矩阵")
    cap = await svc.capabilities()
    data = cap["data"]

    print(f"链路：{', '.join(data['chains'])}")
    print(f"热点源：{len(data['hot_sources'])} 个")
    for name, p in data["platforms"].items():
        supported = sum(1 for c in p["capabilities"].values() if c["supported"])
        total = len(p["capabilities"])
        print(f"  {p['display_name']:10s} ({name:12s}) 支持 {supported}/{total} 项能力")
        for cname, c in p["capabilities"].items():
            if not c["supported"]:
                print(f"       ✗ {cname:18s} auth={c['auth']}")

    # 视频号必须出现在矩阵里（"得有位置"）
    if "channels" not in data["platforms"]:
        failures.append("capabilities 里没有视频号")
    else:
        print("\n  [OK] 视频号在能力矩阵中占位")

    # ---------- 2. 链路 A：热点 + 交叉验证 ----------
    show("2. 链路 A —— 多源热点聚合 + 交叉验证")
    hot = await svc.hot_topics(limit_per_source=50)
    s = hot["data"]["summary"]
    print(f"交叉验证后话题数：{s['total']}")
    print(f"  confirmed(≥3源): {s['confirmed']}")
    print(f"  likely(2源)    : {s['likely']}")
    print(f"  single(1源)    : {s['single']}")
    print(f"失败源：{len(hot['meta']['failures'])} 个")

    confirmed = [t for t in hot["data"]["topics"] if t["level"] == "confirmed"]
    print(f"\n【强信号话题】")
    for t in confirmed[:8]:
        print(f"  [{t['source_count']}源] {t['word'][:50]}")
        print(f"        来源: {', '.join(t['sources'])}")

    if s["total"] == 0:
        failures.append("热点聚合返回 0 条")
    if not confirmed:
        failures.append("没有 confirmed 级别话题——交叉验证可能失效")

    # ---------- 3. 视频号占位 ----------
    show("3. 视频号占位 —— 必须返回诚实错误")
    for tool_name, call, expect_codes in [
        (
            "content_search",
            lambda: svc.content_search(platform="channels", keyword="测试"),
            {"PLATFORM_NOT_IMPLEMENTED"},
        ),
        (
            "creator_profile",
            lambda: svc.creator_profile(platform="channels", id_or_url="x"),
            {"PLATFORM_NOT_IMPLEMENTED"},
        ),
        (
            "hot_topics(源名写错)",
            lambda: svc.hot_topics(sources=["channels"]),
            # channels 是平台名不是源名 → 应报参数错误（而非上游失败），
            # 且必须列出可用源名，否则调用方无从修正
            {"INVALID_ARGUMENT"},
        ),
    ]:
        try:
            await call()
            failures.append(f"视频号 {tool_name} 竟然成功了——占位失效")
            print(f"  [FAIL] {tool_name} 没有报错")
        except ToolError as exc:
            d = exc.to_dict()["error"]
            ok = d["code"] in expect_codes and d.get("reason") and d.get("alternatives")
            mark = "OK" if ok else "FAIL"
            if not ok:
                failures.append(f"视频号 {tool_name} 错误结构不完整：{d['code']}")
            print(f"  [{mark}] {tool_name} → {d['code']}")
            print(f"         原因: {d.get('reason', '')[:76]}")
            alts = d.get("alternatives", [])
            print(f"         替代: {len(alts)} 条" + (f" 例: {alts[0][:50]}" if alts else ""))

    # ---------- 4. 平台别名 ----------
    show("4. 中文平台别名解析")
    for alias in ["抖音", "小红书", "视频号", "微信视频号", "xhs", "dy"]:
        try:
            await svc.content_search(platform=alias, keyword="测试", limit=1)
            print(f"  [OK]   {alias} → 调用成功")
        except ToolError as exc:
            code = exc.to_dict()["error"]["code"]
            ok = code in ("SIGNATURE_REQUIRED", "AUTH_REQUIRED", "CAPABILITY_UNSUPPORTED", "PLATFORM_NOT_IMPLEMENTED")
            mark = "OK" if ok else "FAIL"
            if not ok:
                failures.append(f"别名 {alias} 解析失败：{code}")
            print(f"  [{mark}] {alias:8s} → {code}")

    # ---------- 5. 未实现能力的错误结构 ----------
    show("5. 未实现能力的错误必须带解释与替代路径")
    try:
        await svc.content_search(platform="xiaohongshu", keyword="无人咖啡机")
        failures.append("小红书搜索竟然成功了")
    except ToolError as exc:
        d = exc.to_dict()["error"]
        print(f"  code        : {d['code']}")
        print(f"  message     : {d['message']}")
        print(f"  reason      : {d.get('reason', '')[:150]}")
        print(f"  alternatives: {d.get('alternatives')}")
        if not d.get("reason") or not d.get("alternatives"):
            failures.append("小红书错误缺少 reason/alternatives")

    await svc.aclose()

    show("结果")
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
