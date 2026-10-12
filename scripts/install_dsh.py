#!/usr/bin/env python3
"""把 social-insight-mcp 挂进 dsh-mcp-manager 的配置。

━━━ 为什么需要这个脚本 ━━━

踩过的坑：PowerShell 5 的 ``Set-Content -Encoding UTF8`` 会写入 UTF-8 BOM，
而 dsh-mcp-manager 的 ``JSON.parse`` 遇到 BOM 会**静默失败**——
配置看起来写进去了，实际根本没加载，且没有任何报错。

所以这里用 Python 的 ``json.dump`` + 显式 ``encoding="utf-8"``（无 BOM），
并在写入后**回读验证**——确认能解析、且目标条目确实存在。

用法::

    python scripts/install_dsh.py                 # 安装/更新
    python scripts/install_dsh.py --check         # 只检查，不写入
    python scripts/install_dsh.py --remove        # 卸载
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "social-insight"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_dsh_home() -> Path:
    """定位 DSH_HOME。"""
    env = os.environ.get("DSH_HOME")
    if env:
        return Path(env)
    return Path.home() / ".dsh"


def find_config() -> Path:
    return find_dsh_home() / "@wingsky-1" / "dsh-mcp-manager" / "mcp.json"


def find_python() -> str:
    """找到能跑本项目的 python。

    优先用项目 venv；没有就用当前解释器。
    """
    for cand in (
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
        PROJECT_ROOT / ".venv" / "bin" / "python",           # Linux/macOS
        Path.home() / ".venvs" / "social-insight-mcp" / "Scripts" / "python.exe",
        Path.home() / ".venvs" / "social-insight-mcp" / "bin" / "python",
    ):
        if cand.exists():
            return str(cand)
    return sys.executable


def build_entry() -> dict:
    return {
        "name": SERVER_NAME,
        "transport": "stdio",
        "enabled": True,
        "command": find_python(),
        "args": ["-m", "social_insight.server"],
        "description": "多平台内容分析（热点选题 + 对标拆解）",
    }


def read_config(path: Path) -> dict:
    """读配置。**显式拒绝 BOM** —— 这是踩过的坑，必须报错而不是静默。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SystemExit(
            f"[FATAL] 配置文件带 UTF-8 BOM：{path}\n"
            "        dsh-mcp-manager 会静默解析失败。请先用无 BOM 方式重写该文件。"
        )
    return json.loads(raw.decode("utf-8"))


def write_config(path: Path, data: dict) -> None:
    """写配置，确保无 BOM，并回读验证。"""
    # 备份
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_suffix(f".json.bak-{stamp}")
        shutil.copy2(path, backup)
        print(f"  已备份: {backup.name}")

    text = json.dumps(data, ensure_ascii=False, indent=4)
    # 关键：用 newline="" 避免 Windows 把 \n 变 \r\n 时出岔子；
    # 编码显式 utf-8（Python 的 utf-8 不带 BOM）
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    # --- 回读验证 ---
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SystemExit("[FATAL] 写入后仍带 BOM，请检查环境。")
    verify = json.loads(raw.decode("utf-8"))
    names = [s.get("name") for s in verify.get("servers", [])]
    if SERVER_NAME not in names:
        raise SystemExit(f"[FATAL] 回读验证失败：{SERVER_NAME} 不在 servers 中")
    print(f"  回读验证通过（无 BOM，可解析，条目存在）")


def main() -> int:
    ap = argparse.ArgumentParser(description="挂载 social-insight-mcp 到 dsh-mcp-manager")
    ap.add_argument("--check", action="store_true", help="只检查，不写入")
    ap.add_argument("--remove", action="store_true", help="卸载")
    args = ap.parse_args()

    cfg = find_config()
    print(f"配置文件: {cfg}")

    if not cfg.exists():
        print(f"[ERROR] 配置不存在。dsh-mcp-manager 可能未安装。")
        return 1

    data = read_config(cfg)
    servers: list[dict] = data.setdefault("servers", [])
    existing = next((s for s in servers if s.get("name") == SERVER_NAME), None)

    if args.remove:
        if not existing:
            print(f"  未安装，无需卸载")
            return 0
        servers.remove(existing)
        if args.check:
            print("  [check] 将移除该条目")
            return 0
        write_config(cfg, data)
        print(f"  已卸载 {SERVER_NAME}")
        return 0

    entry = build_entry()
    print(f"\n将要写入的条目：")
    print(json.dumps(entry, ensure_ascii=False, indent=2))

    if existing:
        print(f"\n  已存在同名条目 → 更新")
        existing.update(entry)
    else:
        print(f"\n  新增条目")
        servers.append(entry)

    if args.check:
        print("\n[check] 未写入。去掉 --check 以实际写入。")
        return 0

    write_config(cfg, data)
    print(f"\n完成。重启 DSH 或等待 mcp-manager 热加载后即可使用。")
    print(f"验证：调用 capabilities 工具查看能力矩阵。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
