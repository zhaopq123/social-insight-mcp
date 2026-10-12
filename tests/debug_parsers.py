"""调试三个解析失败的源，dump 真实结构。"""

import asyncio
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


async def main() -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=25, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:

        # ---- 百度 ----
        print("=" * 70)
        print("百度 top.baidu.com")
        print("=" * 70)
        r = await c.get("https://top.baidu.com/api/board?platform=wise&tab=realtime")
        j = r.json()
        print("顶层键:", list(j.keys()))
        data = j.get("data", {})
        print("data 键:", list(data.keys()) if isinstance(data, dict) else type(data))
        cards = data.get("cards") if isinstance(data, dict) else None
        print("cards 数量:", len(cards) if cards else 0)
        if cards:
            c0 = cards[0]
            print("card[0] 键:", list(c0.keys()))
            content = c0.get("content")
            print("content 数量:", len(content) if content else 0)
            if content:
                print("content[0] 键:", list(content[0].keys()))
                print("content[0] 样本:", json.dumps(content[0], ensure_ascii=False)[:400])

        # ---- tophub ----
        print("\n" + "=" * 70)
        print("tophub.today")
        print("=" * 70)
        r = await c.get("https://tophub.today/")
        html = r.text
        print("长度:", len(html))
        for pat, label in [
            (r'class="[^"]*trow[^"]*"', "class 含 trow"),
            (r'<a[^>]+href="/n/[^"]+"', "href=/n/ 链接"),
            (r'cc-cd-cb-ll', "cc-cd-cb-ll 类"),
            (r'itemtitle', "itemtitle 类"),
        ]:
            print(f"  {label}: {len(re.findall(pat, html))} 处")
        m = re.search(r'<a[^>]+href="/n/[^"]+"[^>]*>.{0,300}', html, re.S)
        if m:
            print("  样本:", m.group(0)[:300].replace("\n", " "))

        # ---- rebang ----
        print("\n" + "=" * 70)
        print("rebang.today")
        print("=" * 70)
        r = await c.get("https://rebang.today/")
        html = r.text
        print("长度:", len(html))
        # 找可能的条目容器
        for pat, label in [
            (r'class="[^"]*item[^"]*"', "class 含 item"),
            (r'class="[^"]*card[^"]*"', "class 含 card"),
            (r'class="[^"]*title[^"]*"', "class 含 title"),
            (r'<a[^>]+href="https?://[^"]+"[^>]*>[^<]{6,60}</a>', "外链文本"),
        ]:
            print(f"  {label}: {len(re.findall(pat, html))} 处")
        # 看有没有内嵌 JSON
        for pat, label in [
            (r'__NEXT_DATA__', "Next.js __NEXT_DATA__"),
            (r'window\.__NUXT__', "Nuxt __NUXT__"),
            (r'<script[^>]*type="application/json"', "JSON script 块"),
        ]:
            print(f"  {label}: {len(re.findall(pat, html))} 处")
        m = re.search(r'__NEXT_DATA__[^>]*>(.{0,600})', html, re.S)
        if m:
            print("  NEXT_DATA 样本:", m.group(1)[:600])


asyncio.run(main())
