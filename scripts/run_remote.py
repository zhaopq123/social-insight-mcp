#!/usr/bin/env python3
"""在远程服务器上运行诊断/运维脚本。

━━━ 两层结构 ━━━

目标机是 Termux + proot-distro Ubuntu 双层：

    Termux 层    <host>:8022        用户 <user>  ← 守护循环、tmux 在这层
    Ubuntu 容器  <host>:2222        用户 <user>  ← 服务本体跑在这层

两层**共享文件系统**（容器可读 Termux 的 home），但**进程命名空间不同**
（其实是共享的，但 proot 的 --kill-on-exit 决定了谁活谁死）。

用法::

    python scripts/run_remote.py <脚本>            # 默认容器层 (2222)
    python scripts/run_remote.py <脚本> --termux   # Termux 层 (8022)
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy import SSH_HOST, SSH_USER, _run, ssh_key

CONTAINER_PORT = "2222"   # Ubuntu 容器
TERMUX_PORT = "8022"      # Termux 宿主层


def run_remote(script: str, *, timeout: int = 600, port: str = CONTAINER_PORT) -> tuple[int, str]:
    """在远端执行脚本。

    ⚠️ 踩过的坑：**两层的 /tmp 是两个不同的目录**。
    Termux 层的 ``/tmp`` 通常不可写或不存在，必须用 ``$PREFIX/tmp``。
    容器层才是标准的 ``/tmp``。
    """
    if port == TERMUX_PORT:
        # Termux 层：用 $PREFIX/tmp
        remote = (
            'F="${TMPDIR:-$PREFIX/tmp}/_diag_$$.sh"; '
            'base64 -d > "$F" && bash "$F"; rc=$?; rm -f "$F"; exit $rc'
        )
    else:
        # 容器层：标准 /tmp
        remote = "base64 -d > /tmp/_diag.sh && bash /tmp/_diag.sh"

    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=20",
        "-i", ssh_key(),
        "-p", port,
        f"{SSH_USER}@{SSH_HOST}",
        remote,
    ]
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    try:
        proc = _run(cmd, input=b64, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "[超时]"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if not args:
        print(__doc__)
        return 1

    port = TERMUX_PORT if "--termux" in flags else CONTAINER_PORT
    layer = "Termux 层" if port == TERMUX_PORT else "Ubuntu 容器"
    print(f"# 目标: {layer} ({SSH_HOST}:{port})\n")

    script = Path(args[0]).read_text(encoding="utf-8")
    code, out = run_remote(script, port=port)
    print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
