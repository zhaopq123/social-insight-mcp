"""验证 MCP 协议层。

service 层测试通过 ≠ MCP 能起来。这一层验证：
  1. 工具 schema 合法（JSON Schema 结构、required 与 properties 一致）
  2. dispatch 正确路由
  3. 错误能转成结构化信封（不抛裸异常给调用方）
"""

import asyncio
import io
import sys
from pathlib import Path

# 模块级只 reconfigure（就地改编码），**不替换 stdout 对象**。
# 原先这里是 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`，
# 它在导入时换掉全局 stdout，pytest 终端仍指向旧流 ->
# ValueError: I/O operation on closed file，整个会话崩掉（实测 428 个测试全跑不出来）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from social_insight.errors import ErrorCode, ToolError
from social_insight.server import TOOL_DEFS, _dispatch
from social_insight.service import SocialInsightService


def check_schemas() -> list[str]:
    problems: list[str] = []
    names = [t["name"] for t in TOOL_DEFS]

    if len(names) != len(set(names)):
        problems.append("工具名有重复")

    for t in TOOL_DEFS:
        name = t["name"]
        schema = t["inputSchema"]
        props = schema.get("properties", {})
        required = schema.get("required", [])

        # required 里的字段必须在 properties 里
        for r in required:
            if r not in props:
                problems.append(f"{name}: required 字段 {r} 不在 properties 中")

        # 每个属性必须有 type
        for pname, pspec in props.items():
            if "type" not in pspec:
                problems.append(f"{name}.{pname}: 缺 type")
            if not pspec.get("description"):
                problems.append(f"{name}.{pname}: 缺 description（LLM 靠它理解参数）")

        if not t.get("description"):
            problems.append(f"{name}: 缺工具 description")

    return problems


async def main() -> int:
    problems: list[str] = []
    svc = SocialInsightService()

    print("=" * 74)
    print(f"1. 工具 schema 校验（{len(TOOL_DEFS)} 个工具）")
    print("=" * 74)
    problems += check_schemas()
    for t in TOOL_DEFS:
        props = list(t["inputSchema"].get("properties", {}).keys())
        req = t["inputSchema"].get("required", [])
        print(f"  {t['name']:20s} 参数 {len(props):2d} 个，必填 {len(req)} 个")
    if not problems:
        print("  [OK] schema 全部合法")

    print("\n" + "=" * 74)
    print("2. dispatch 路由")
    print("=" * 74)
    for name, args, expect_ok in [
        ("capabilities", {}, True),
        ("hot_topics", {"limit_per_source": 10}, True),
        ("content_search", {"platform": "channels", "keyword": "x"}, False),
        ("analyze_viral", {"platform": "douyin"}, False),  # 缺 keyword/creator
    ]:
        try:
            res = await _dispatch(svc, name, args)
            got_ok = res.get("ok", False)
            mark = "OK" if got_ok == expect_ok else "FAIL"
            if got_ok != expect_ok:
                problems.append(f"dispatch {name}: 期望 ok={expect_ok}，得到 {got_ok}")
            print(f"  [{mark}] {name:20s} ok={got_ok}")
        except ToolError as exc:
            got_ok = False
            mark = "OK" if expect_ok is False else "FAIL"
            if expect_ok is not False:
                problems.append(f"dispatch {name}: 不该抛错但抛了 {exc.code}")
            print(f"  [{mark}] {name:20s} 抛 ToolError({exc.code.value})")
        except Exception as exc:
            problems.append(f"dispatch {name}: 抛了裸异常 {type(exc).__name__}: {exc}")
            print(f"  [FAIL] {name:20s} 裸异常 {type(exc).__name__}: {exc}")

    print("\n" + "=" * 74)
    print("3. 未知工具")
    print("=" * 74)
    try:
        await _dispatch(svc, "no_such_tool", {})
        problems.append("未知工具没有报错")
        print("  [FAIL] 未知工具没有报错")
    except ToolError as exc:
        d = exc.to_dict()["error"]
        # 判定标准：错误码正确 **且** 给出可用工具列表（reason 或 alternatives 二者其一）
        has_list = bool(d.get("reason")) or bool(d.get("alternatives"))
        ok = d["code"] == ErrorCode.INVALID_ARGUMENT.value and has_list
        mark = "OK" if ok else "FAIL"
        if not ok:
            problems.append(f"未知工具错误结构不完整：code={d['code']} has_list={has_list}")
        print(f"  [{mark}] no_such_tool → {d['code']}")
        print(f"         reason: {d.get('reason', '')[:80]}")
        if not has_list:
            problems.append("未知工具的错误没有列出可用工具，调用方无从修正")

    await svc.aclose()

    print("\n" + "=" * 74)
    if problems:
        print(f"失败 {len(problems)} 项：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
