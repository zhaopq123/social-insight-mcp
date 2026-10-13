#!/usr/bin/env python3
"""在 Termux 层执行脚本（正确处理 $PREFIX/tmp）。

⚠️ 踩过的坑：Termux 层的 `/tmp` 不可写/不存在——
之前多次在这里失败。必须用 `$PREFIX/tmp`（默认 /data/data/com.termux/files/usr/tmp）。
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

SSH_HOST = "192.168.3.35"
SSH_PORT = "8022"          # Termux 层
SSH_USER = "zpq"


def run_termux(script_path: str) -> int:
    key = str(Path.home() / ".ssh" / "id_ed25519")
    b64 = base64.b64encode(Path(script_path).read_bytes()).decode()

    cmd = [
        "ssh", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=20",
        "-i", key, "-p", SSH_PORT, f"{SSH_USER}@{SSH_HOST}",
        # 关键：用 $PREFIX/tmp，不是 /tmp
        'F="${TMPDIR:-$PREFIX/tmp}/_run_$(date +%s).sh"; '
        f"base64 -d > $F && bash $F; rc=$?; rm -f $F; exit $rc",
    ]
    # base64 通过 stdin 传
    proc = subprocess.run(cmd, input=b64, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=180)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(run_termux(sys.argv[1]))
