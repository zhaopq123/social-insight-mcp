#!/bin/bash
# 热点源连通性诊断（在容器层执行）
#
# 用途：当 hot_topics 报「所有源都失败」，或某个源长期返回 0 条时，
#       用它逐源排查——是哪几个源挂了、挂在哪一步。
#
# 用法：
#   python scripts/run_remote.py scripts/ops/diag-sources.sh
set -u

TARGET=/opt/social-insight-mcp
cd "$TARGET"
export PYTHONPATH="$TARGET/src"

./venv/bin/python - <<'PYEOF'
import asyncio, sys, time
sys.path.insert(0, 'src')
from social_insight.http import HttpClient
from social_insight.hot.sources import FETCHERS, SOURCE_META, fetch_source


async def main():
    client = HttpClient()
    ok, failed = [], []
    print("%-22s %-10s %s" % ("源", "状态", "详情"))
    print("-" * 74)
    for name in FETCHERS:
        display = SOURCE_META.get(name, {}).get("display", name)
        t0 = time.time()
        try:
            topics = await fetch_source(client, name, limit=20)
            dt = time.time() - t0
            if topics:
                ok.append(name)
                print("%-22s %-10s %3d 条  %.1fs   例: %s" % (
                    name, "OK", len(topics), dt, topics[0].word[:24]))
            else:
                failed.append((name, "返回空"))
                print("%-22s %-10s 返回空  %.1fs" % (name, "EMPTY", dt))
        except Exception as e:
            failed.append((name, str(e)[:40]))
            print("%-22s %-10s %s" % (name, "FAIL", str(e)[:44]))
    await client.aclose()

    print("-" * 74)
    print("成功 %d/%d" % (len(ok), len(FETCHERS)))
    if failed:
        print("失败:")
        for n, r in failed:
            print("   - %s: %s" % (n, r))
        print()
        print("提示：单个源挂掉不影响整体（其他源照常工作）。")
        print("      若全部失败，多半是该环境网络问题。")

asyncio.run(main())
PYEOF
