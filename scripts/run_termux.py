#!/usr/bin/env python3
"""在 Termux 层执行脚本（正确处理 $PREFIX/tmp），支持参数透传。

用法::

    python scripts/run_termux.py <脚本> [参数...]

示例::

    python scripts/run_termux.py scripts/ops/check-daemon.sh           # 只检查
    python scripts/run_termux.py scripts/ops/check-daemon.sh --repair  # 自愈

⚠️ 踩过的坑（2026-10-13）：`--repair` 曾被本地 argparse 吃掉，
远端脚本收不到，导致 `--repair` 静默失效（打印「加 --repair」却修不了）。
现在 argv[2:] **原样透传**，不经过本地解析。

⚠️ 另一个坑：Termux 层的 `/tmp` 不可写/不存在，必须用 `$PREFIX/tmp`。
"""

from __future__ import annotations

import base64
import shlex
import subprocess
import sys
from pathlib import Path

# 连接参数统一从 scripts/deploy.py 读取 —— 该模块从 .env / 环境变量加载，
# 仓库里**不硬编码任何服务器地址**。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy import SSH_HOST, SSH_USER  # noqa: E402

SSH_PORT = "8022"          # Termux 层（容器层为 2222）


def run_termux(argv: list[str]) -> int:
    """执行远端脚本；argv[2:] 原样透传给远端。"""
    if len(argv) < 2:
        print(__doc__)
        return 1

    script_path = Path(argv[1])
    if not script_path.is_file():
        print(f"[ERROR] 脚本不存在: {script_path}")
        return 1

    passthrough = list(argv[2:])          # 不做任何解析
    key = str(Path.home() / ".ssh" / "id_ed25519")
    b64 = base64.b64encode(script_path.read_bytes()).decode()

    quoted = " ".join(shlex.quote(a) for a in passthrough)
    remote = (
        'set -e; F="${TMPDIR:-$PREFIX/tmp}/_run_$$.sh"; '
        f"base64 -d > $F && bash $F {quoted}; rc=$?; rm -f $F; exit $rc"
    )

    cmd = [
        "ssh", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=20",
        "-i", key, "-p", SSH_PORT, f"{SSH_USER}@{SSH_HOST}",
        remote,
    ]
    proc = subprocess.run(
        cmd, input=b64, capture_output=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(run_termux(sys.argv))
