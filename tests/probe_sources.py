"""热点源连通性实测。

交叉验证的前提是：多个独立源都可用。
这个脚本对每个源单独测试，报告成功/失败与样本数据。
"""

import asyncio
import sys
import io
from pathlib import Path

# 强制 UTF-8 输出，避免 Windows 控制台编码问题
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

from social_insight.http import HttpClient
from social_insight.hot.sources import FETCHERS, SOURCE_META, fetch_source


async def main() -> None:
    client = HttpClient()
    print("=" * 72)
    print("热点源连通性实测")
    print("=" * 72)

    ok, failed = [], []
    for name in FETCHERS:
        display = SOURCE_META.get(name, (name, "", 0))[0]
        try:
            topics = await fetch_source(client, name, limit=8)
            ok.append(name)
            print(f"\n[OK] {name} ({display})  取回 {len(topics)} 条")
            for t in topics[:5]:
                hot = f" 热度={t.hot_value}" if t.hot_value else ""
                print(f"     #{t.position} {t.word[:52]}{hot}")
        except Exception as exc:
            failed.append(name)
            print(f"\n[FAIL] {name} ({display})")
            print(f"     {type(exc).__name__}: {str(exc)[:160]}")

    print("\n" + "=" * 72)
    print(f"成功 {len(ok)}/{len(FETCHERS)}：{', '.join(ok)}")
    if failed:
        print(f"失败：{', '.join(failed)}")
    print("=" * 72)

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
