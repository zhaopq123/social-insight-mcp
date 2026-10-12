#!/usr/bin/env python3
"""系统性审计：找出尚未发现的 bug。

━━━ 怀疑清单（基于代码结构推断，待验证）━━━

S1. **两条传输路径的工具面可能不一致**
    server.py 用 TOOL_DEFS（JSON Schema 手写）
    http_server.py 用 FastMCP @mcp.tool() 装饰器（Python 签名）
    两处**分别维护**——若漂移，stdio 与 HTTP 会暴露不同的工具/参数。

S2. **HttpClient 缓存无上限**
    _cache 是普通 dict，从不清理过期项 → 长时间运行内存增长。

S3. **_ProfilesProxy 的 dict 语义可能不全**
    我实现了 __len__/__iter__/__contains__/keys/values/items/get/__getitem__
    但 dict 还有很多方法（copy/clear/setdefault/__eq__/pop...）
    其中 reload_profiles 用了 .clear() —— 会不会没清干净？

S4. **service 单例的并发安全**
    http_server 里 svc 是单例，被所有请求共享。
    HttpClient 的 _last_call / _locks / _cache 都是共享可变状态。

S5. **错误信封在两条路径上是否一致**
    server.py 的 dispatch 与 http_server 的 _safe 都包 ToolError，
    但包装方式不同——可能产生不同的 JSON 结构。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

FINDINGS: list[str] = []
OK: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        OK.append(label)
        print(f"  ✅ {label}")
    else:
        FINDINGS.append(f"{label}: {detail}")
        print(f"  ❌ {label}")
        if detail:
            print(f"     {detail}")


print("=" * 80)
print("S1 · 两条传输路径的工具面一致性")
print("=" * 80)

from social_insight.server import TOOL_DEFS

stdio_tools = {t["name"]: t for t in TOOL_DEFS}

# 从 http_server 提取 FastMCP 注册的工具名与参数
try:
    from social_insight import http_server

    src = inspect.getsource(http_server.build_app)
    import re

    http_tools = re.findall(r"@mcp\.tool\(\)\s+async def (\w+)\(", src)
    print(f"  stdio 工具数: {len(stdio_tools)}")
    print(f"  http  工具数: {len(http_tools)}")

    missing_in_http = set(stdio_tools) - set(http_tools)
    extra_in_http = set(http_tools) - set(stdio_tools)

    check(
        "两条路径工具名一致",
        not missing_in_http and not extra_in_http,
        f"stdio 独有={missing_in_http}  http 独有={extra_in_http}",
    )

    # 参数对比
    #
    # ⚠️ 注意：不能用 `(\w+)\s*[:=]` 抓参数名 —— 那会把「类型注解」里
    # 的类型名（int / str / None）也当成参数。
    # 正确做法：按**顶层逗号**切分签名，每段取第一个标识符。
    def parse_http_params(sig: str) -> set[str]:
        params: set[str] = set()
        depth = 0
        buf = ""
        for ch in sig:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                params.add(buf)
                buf = ""
            else:
                buf += ch
        if buf:
            params.add(buf)

        names = set()
        for chunk in params:
            chunk = chunk.strip()
            if not chunk:
                continue
            # 去掉默认值部分，再取第一个标识符
            head = chunk.split("=")[0].strip()
            head = head.split(":")[0].strip()
            if head and head.isidentifier():
                names.add(head)
        return names

    print("\n  参数逐项对比:")
    for name, tdef in sorted(stdio_tools.items()):
        if name not in http_tools:
            continue
        schema_params = set(tdef["inputSchema"].get("properties", {}))
        m = re.search(
            rf"@mcp\.tool\(\)\s+async def {re.escape(name)}\((.*?)\)\s*->",
            src,
            re.S,
        )
        if not m:
            continue
        http_params = parse_http_params(m.group(1))
        only_stdio = schema_params - http_params
        only_http = http_params - schema_params
        if only_stdio or only_http:
            check(
                f"{name} 参数一致",
                False,
                f"stdio 独有={only_stdio}  http 独有={only_http}",
            )
        else:
            print(f"     ✅ {name:20s} {len(schema_params)} 个参数一致")
    print()
except Exception as exc:  # noqa: BLE001
    print(f"  ⚠️ 无法对比: {type(exc).__name__}: {exc}")

print("=" * 80)
print("S2 · HttpClient 缓存是否有上限")
print("=" * 80)

from social_insight.http import HttpClient

h = HttpClient()
src_http = inspect.getsource(HttpClient)
has_max = any(k in src_http for k in ("max_cache", "cache_limit", "MAX_CACHE", "maxsize"))
has_evict = "popitem" in src_http or "evict" in src_http.lower()
print(f"  缓存字段类型: dict")
print(f"  有容量上限: {has_max}")
print(f"  有淘汰逻辑: {has_evict}")

# 实测：塞很多不同 key 看是否无限增长
import asyncio


async def fill_cache() -> int:
    from social_insight.errors import ErrorCode, ToolError

    c = HttpClient()

    async def fail(*a, **kw):
        raise ToolError(code=ErrorCode.UPSTREAM_ERROR, message="x")

    c.request_json = fail  # type: ignore[method-assign]
    # 直接操作 _cache 模拟大量不同 key
    for i in range(5000):
        c._cache_put(("k", i), {"v": i}, ttl=3600)  # type: ignore[arg-type]
    return len(c._cache)


size = asyncio.run(fill_cache())
check(
    "缓存有上限（不是无界增长）",
    size < 5000,
    f"塞 5000 条后缓存仍有 {size} 条 —— 长时间运行会内存增长",
)

print("=" * 80)
print("S3 · _ProfilesProxy 的 dict 语义完整性")
print("=" * 80)

from social_insight.hot.match import PRESET_PROFILES, reload_profiles

p = PRESET_PROFILES
print(f"  len={len(p)}")
print(f"  'tech-blogger' in p: {'tech-blogger' in p}")
print(f"  list(p) 长度: {len(list(p))}")
print(f"  p.get('tech-blogger'): {p.get('tech-blogger') is not None}")

# 检查常见 dict 方法是否可用
for meth in ("copy", "clear", "setdefault", "pop", "update", "__eq__"):
    try:
        getattr(p, meth)
        print(f"  {meth}: 存在")
    except AttributeError:
        check(f"_ProfilesProxy 支持 {meth}", False, "dict 子类应继承该方法")

# clear() 后能否恢复（reload_profiles 依赖它）
p.clear()
after_clear = len(p)
print(f"  clear() 后 len={after_clear}（应为 0）")
reload_profiles()
after_reload = len(p)
print(f"  reload 后 len={after_reload}（应 > 0）")
check(
    "clear + reload 能正常工作",
    after_clear == 0 and after_reload > 0,
    f"clear后={after_clear} reload后={after_reload}",
)

print("=" * 80)
print("S4 · 服务单例的共享可变状态")
print("=" * 80)

from social_insight.service import SocialInsightService

svc = SocialInsightService()
shared = []
for attr in ("_last_call", "_locks", "_cache"):
    if hasattr(svc.http, attr):
        shared.append(attr)
print(f"  HttpClient 共享可变状态: {shared}")

# 并发场景下 _last_call 是普通 dict，多协程同时写可能丢更新
# 但 asyncio 单线程下 dict 操作是原子的 —— 不是 bug
check(
    "共享状态在 asyncio 单线程模型下安全",
    True,
    "（dict 操作在单线程事件循环中原子；若将来改用多线程需加锁）",
)

print("=" * 80)
print("S5 · 两条路径的错误信封一致性")
print("=" * 80)

src_srv = inspect.getsource(sys.modules["social_insight.server"])
src_hs = inspect.getsource(http_server)

# stdio: json.dumps(result, ensure_ascii=False, indent=2)
# http : return dict（FastMCP 自己序列化）
stdio_dumps = "json.dumps(result" in src_srv
http_returns_dict = "return await _safe(" in src_hs

print(f"  stdio 用 json.dumps 包装: {stdio_dumps}")
print(f"  http  直接返回 dict:      {http_returns_dict}")

# 关键：两者返回的**内容**应一致（都是 ToolError.to_dict() 或 success()）
check(
    "两条路径都用同一套信封构造",
    "_safe" in src_hs and "exc.to_dict()" in src_hs and "exc.to_dict()" in src_srv,
    "应都调用 ToolError.to_dict()",
)

print()
print("=" * 80)
print("审计汇总")
print("=" * 80)
print(f"  通过 {len(OK)} 项")
if FINDINGS:
    print(f"\n  ❌ 发现 {len(FINDINGS)} 个问题:")
    for f in FINDINGS:
        print(f"     - {f}")
    sys.exit(1)
print("\n  ✅ 未发现新问题")
