"""验证源的独立性 + 找 rebang 的接口。

交叉验证的核心前提：源必须互相独立。
如果 B 站只是 A 的镜像，那「两个源都报道」没有任何信息量。
"""

import asyncio
import io
import re
import sys
from pathlib import Path

# ⚠️ 原本这里是 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`，
# 那会在**模块导入时**替换掉全局 stdout 对象。
# 后果：pytest 的终端对象仍指向旧流，之后任何写操作抛
# `ValueError: I/O operation on closed file`，整个测试会话崩掉。
# （实测：这会让 428 个测试一个都跑不出来，与被测代码无关。）
#
# reconfigure() 是**就地**改编码，不换对象 —— 这才是模块级该做的事。
try:  # stdout 可能被 pytest 换成了无 buffer 的对象
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


async def main() -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=25, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:

        # ---- 1. uapis 支持哪些类型？是不是独立源？----
        print("=" * 70)
        print("1. uapis 支持的类型")
        print("=" * 70)
        for t in ["douyin", "weibo", "zhihu", "bilibili", "baidu", "xiaohongshu", "toutiao", "36kr"]:
            try:
                r = await c.get(f"https://uapis.cn/api/v1/misc/hotboard?type={t}")
                j = r.json()
                items = j.get("list") or j.get("data") or []
                first = ""
                if items and isinstance(items[0], dict):
                    first = (items[0].get("title") or items[0].get("word") or "")[:40]
                print(f"  type={t:14s} -> {len(items):3d} 条  {first}")
            except Exception as e:
                print(f"  type={t:14s} -> ERR {str(e)[:60]}")

        # ---- 2. rebang.today 有没有 API ----
        print("\n" + "=" * 70)
        print("2. rebang.today 接口探测")
        print("=" * 70)
        cands = [
            "https://rebang.today/api/hot",
            "https://rebang.today/api/list",
            "https://rebang.today/api/v1/hot",
            "https://api.rebang.today/hot",
        ]
        for u in cands:
            try:
                r = await c.get(u)
                print(f"  [{r.status_code}] {u}  len={len(r.text)}  {r.text[:100]}")
            except Exception as e:
                print(f"  ERR {u} -> {str(e)[:70]}")

        # 看首页里有没有 XHR 端点线索
        r = await c.get("https://rebang.today/")
        html = r.text
        apis = set(re.findall(r'["\'](/(?:api|_next/data)[^"\']{0,80})["\']', html))
        print("  首页出现的 API 路径:", list(apis)[:10] or "无")

        # ---- 3. 关键：验证 douyin 与 uapis 是否同源 ----
        print("\n" + "=" * 70)
        print("3. 独立性验证：douyin vs uapis")
        print("=" * 70)
        rd = await c.get("https://www.douyin.com/aweme/v1/web/hot/search/list/"
                         "?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1",
                         headers={"Referer": "https://www.douyin.com/"})
        dwords = [w.get("word") for w in (rd.json().get("data") or {}).get("word_list") or []][:10]
        ru = await c.get("https://uapis.cn/api/v1/misc/hotboard?type=douyin")
        uitems = ru.json().get("list") or []
        uwords = [(i.get("title") or "") for i in uitems][:10]

        print("  抖音官方:", dwords[:5])
        print("  uapis   :", uwords[:5])
        overlap = len(set(dwords) & set(uwords))
        print(f"  前 10 条重合数: {overlap}/10")
        if overlap >= 5:
            print("  >>> 结论：uapis 的 douyin 榜是抖音官方榜的镜像，不是独立源")
        else:
            print("  >>> 结论：两者数据不同，可视为不同源")


asyncio.run(main())
