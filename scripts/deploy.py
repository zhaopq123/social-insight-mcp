#!/usr/bin/env python3
"""部署 social-insight-mcp 到远程服务器（Termux + proot Ubuntu）。

━━━ 环境约束（决定部署方式）━━━

目标机是 vivo 手机上的 Termux + proot-distro Ubuntu 双层结构：
  · **无 systemd** —— systemctl 不存在
  · proot 带 ``--kill-on-exit`` —— 后台脱离型进程活不下来
  · 服务必须**前台运行**，由 Termux 层的守护循环拉起

所以部署分两步：
  1. 把代码放进容器内 ``/opt/social-insight-mcp``，建 venv，装依赖
  2. 在 **Termux 层** 起一个守护循环（tmux 里），持续拉起容器内的服务

━━━ 为什么不复用 ssh_ops 的 SFTP ━━━

调研已知：paramiko SFTP 在 Termux 下不可用（sftp-server 存在也会报
FileNotFoundError）。所以这里用 ``base64 内联 + 远端解码`` 传文件，
这也是该环境已验证可行的方式。
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REMOTE_TARGET = "/opt/social-insight-mcp"

#: SSH 连接参数（来自 ~/.ssh/config 的 vivo-152-ubuntu）
def _load_dotenv() -> None:
    """从项目根的 .env 读取环境变量（不覆盖已存在的）。

    本地部署配置放在 .env（已被 .gitignore 忽略），
    仓库里因此不含任何服务器地址。
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

SSH_HOST = os.environ.get("SOCIAL_INSIGHT_SSH_HOST", "127.0.0.1")
SSH_PORT = os.environ.get("SOCIAL_INSIGHT_SSH_PORT", "22")
SSH_USER = os.environ.get("SOCIAL_INSIGHT_SSH_USER", "")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """统一的 subprocess 封装。

    ⚠️ 踩过的坑：Windows 上 ``text=True`` 会用**系统 locale 编码**（中文系统是
    GBK）去解码子进程输出，而远端返回的是 UTF-8 中文 —— 直接
    ``UnicodeDecodeError: 'gbk' codec can't decode``。
    必须显式指定 ``encoding="utf-8", errors="replace"``。
    """
    kw.setdefault("capture_output", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, **kw)


def ssh_key() -> str:
    return str(Path.home() / ".ssh" / "id_ed25519")


def run_ssh(script: str, *, timeout: int = 600, label: str = "") -> tuple[int, str]:
    """在容器内跑一段 bash（base64 传输，避开引号嵌套）。"""
    if label:
        print(f"\n─── {label} " + "─" * max(0, 60 - len(label)))

    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    # 走 stdin 而非命令行参数——Windows 命令行长度上限约 32KB，
    # 超了会报 WinError 206（实测踩过）。
    remote = "base64 -d > /tmp/_si.sh && bash /tmp/_si.sh"

    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-i", ssh_key(),
        "-p", SSH_PORT,
        f"{SSH_USER}@{SSH_HOST}",
        remote,
    ]
    try:
        proc = _run(cmd, input=b64, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"[超时 {timeout}s]"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def pack_project() -> Path:
    """打包项目（排除 venv / 缓存）。"""
    tmp = Path(tempfile.gettempdir()) / "social-insight-mcp.tar.gz"
    if tmp.exists():
        tmp.unlink()

    def skip(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(ti.name).parts
        if any(p in {".venv", "__pycache__", ".git", ".pytest_cache"} for p in parts):
            return None
        if ti.name.endswith(".pyc"):
            return None
        return ti

    with tarfile.open(tmp, "w:gz") as tar:
        for item in sorted(PROJECT_ROOT.iterdir()):
            if item.name in {".venv", "__pycache__", ".git"}:
                continue
            tar.add(item, arcname=item.name, filter=skip)
    return tmp


def upload(tarball: Path) -> tuple[int, str]:
    """上传 tarball 到远端。

    ⚠️ 踩过的坑：把 base64 放进命令行参数会触发
    ``WinError 206: 文件名或扩展名太长``（Windows 命令行长度上限约 32KB）。
    改成 **stdin 管道** 传输，绕开这个限制。
    """
    print(f"\n─── 上传 {tarball.name} ({tarball.stat().st_size / 1024:.1f} KB)")
    b64 = base64.b64encode(tarball.read_bytes()).decode("ascii")
    print(f"    base64 长度 {len(b64)} 字符（走 stdin，不占命令行）")

    # 远端：把 stdin 写进文件再解码
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-i", ssh_key(),
        "-p", SSH_PORT,
        f"{SSH_USER}@{SSH_HOST}",
        "cat > /tmp/_si.tgz.b64 && base64 -d /tmp/_si.tgz.b64 > /tmp/_si.tgz && "
        "ls -la /tmp/_si.tgz && echo '--- 内容条数 ---' && tar -tzf /tmp/_si.tgz | wc -l",
    ]
    try:
        proc = _run(cmd, input=b64, timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "[上传超时]"

    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return proc.returncode, f"上传失败: {out}"
    return 0, out


INSTALL_SCRIPT = r"""
set -eu
TARGET=/opt/social-insight-mcp
mkdir -p "$TARGET"
cd "$TARGET"

echo "=== 解包 ==="
tar -xzf /tmp/_si.tgz -C "$TARGET"
ls -la "$TARGET" | head -12

echo ""
echo "=== 修复执行权限 ==="
# ⚠️ tar 解包不带执行位（实测踩过 3 次）：
#   1. start-mcp.sh Permission denied → 守护循环死循环
#   2. check-daemon.sh 加好了权限但下一次部署又被解包覆盖
#   3. 脚本写在本机是 UTF-8 + LF，部署到服务器也要保持可执行
#
# 结论：**每次部署都要把所有 .sh 和 .py 脚本重打执行位**，
#       不能依赖「上次已经 chmod 过」。
chmod +x "$TARGET/start-mcp.sh"
chmod +x "$TARGET"/scripts/*.py 2>/dev/null || true
find "$TARGET/scripts" -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
echo "  已设置执行位:"
ls -la "$TARGET/start-mcp.sh"
find "$TARGET/scripts" -name '*.sh' | head -12 | sed 's/^/    /'

echo ""
echo "=== venv ==="
if [ ! -x "$TARGET/venv/bin/python" ]; then
  python3 -m venv "$TARGET/venv"
fi
"$TARGET/venv/bin/python" --version

echo ""
echo "=== 安装依赖（可能耗时）==="
"$TARGET/venv/bin/pip" install --quiet --upgrade pip 2>&1 | tail -2
"$TARGET/venv/bin/pip" install --quiet fastmcp uvicorn httpx 2>&1 | tail -5

echo ""
echo "=== 已装关键包 ==="
"$TARGET/venv/bin/pip" list 2>/dev/null | grep -iE 'fastmcp|uvicorn|httpx|^mcp ' || true

echo ""
echo "=== 冒烟测试：导入 + 能力矩阵 ==="
cd "$TARGET"
PYTHONPATH="$TARGET/src" "$TARGET/venv/bin/python" -c "
import asyncio, sys
sys.path.insert(0, 'src')
from social_insight.service import SocialInsightService
async def main():
    svc = SocialInsightService()
    r = await svc.capabilities()
    d = r['data']
    print('链路:', list(d['chains']))
    print('热点源:', len(d['hot_sources']))
    print('平台:', list(d['platforms']))
    await svc.aclose()
asyncio.run(main())
"

echo ""
echo "=== 真实拉取热点（端到端）==="
PYTHONPATH="$TARGET/src" "$TARGET/venv/bin/python" -c "
import asyncio, sys
sys.path.insert(0, 'src')
from social_insight.service import SocialInsightService
async def main():
    svc = SocialInsightService()
    r = await svc.hot_topics(limit_per_source=20)
    s = r['data']['summary']
    print('话题总数:', s['total'], '| confirmed:', s['confirmed'], '| likely:', s['likely'])
    for t in r['data']['topics'][:3]:
        print('  [%d源] %s' % (t['source_count'], t['word'][:40]))
    await svc.aclose()
asyncio.run(main())
"
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="部署 social-insight-mcp 到远程服务器")
    ap.add_argument("--skip-upload", action="store_true", help="跳过上传（只重装/重启）")
    ap.add_argument("--check", action="store_true", help="只探测环境")
    ap.add_argument(
        "--skip-restart",
        action="store_true",
        help="跳过重建长驻 tmux 会话（不推荐：长驻任务会跑旧代码）",
    )
    args = ap.parse_args()

    print("=" * 68)
    print("部署 social-insight-mcp")
    print(f"目标: {SSH_USER}@{SSH_HOST}:{SSH_PORT}  →  {REMOTE_TARGET}")
    print("=" * 68)

    code, out = run_ssh(
        "uname -m; cat /etc/os-release | head -2; "
        "python3 --version; command -v tmux >/dev/null && echo 'tmux OK' || echo 'tmux 缺失'",
        label="探测环境",
        timeout=60,
    )
    print(out.strip())
    if code != 0:
        print(f"\n[ERROR] 环境探测失败 (exit={code})")
        return 1
    if args.check:
        return 0

    if not args.skip_upload:
        tarball = pack_project()
        print(f"\n打包: {tarball}")
        code, out = upload(tarball)
        print(out.strip())
        if code != 0:
            print(f"\n[ERROR] 上传失败")
            return 1

    code, out = run_ssh(INSTALL_SCRIPT, label="安装与验证", timeout=1200)
    print(out)
    if code != 0:
        print(f"\n[ERROR] 安装失败 (exit={code})")
        return 1

    print("\n" + "=" * 68)
    print("部署完成")
    print("=" * 68)
    if not args.skip_restart:
        code, out = restart_daemons()
        print(out)
        if code != 0:
            print("\n[WARN] 守护会话重建失败（服务可能仍在跑旧代码）")
    else:
        print("已跳过守护会话重建（--skip-restart）")
        print("  ⚠️ 注意：tmux 里的长驻任务仍在跑**旧代码**——")
        print("     手动重启：python scripts/run_termux.py scripts/ops/restart-daemon.sh")
    return 0


#: 需要重建的长驻会话（Termux 层）
DAEMON_SESSIONS = ("social-insight", "social-insight-snapshot")


def restart_daemons() -> tuple[int, str]:
    """部署后自动重建长驻 tmux 会话。

    ⚠️ 为什么必须做：改了源码，服务是热加载新代码，
    但 tmux 里的长驻任务（守护循环、定时采集）跑的还是**部署前那份**。
    实测后果（2026-10-13）：snapshot 采集脚本旧代码跑一整天，
    WAL 一路涨到 58 MB 才被发现。

    所以部署完必须重建这两个会话，否则改了也白改。
    """
    print("\n─── 重建长驻会话 " + "─" * 40)
    script = Path(__file__).resolve().parent / "ops" / "restart-daemon.sh"
    if not script.is_file():
        return 1, f"[ERROR] 找不到 {script}"

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_termux import run_termux

    # ⚠️ --upgrade：让守护循环**杀掉旧进程**再启动新代码。
    #
    # 实测踩过（2026-10-13）：部署后验收仍显示 11 个工具（应为 12）。
    # 新代码已上传，但旧服务进程（已运行 1h18m）仍占着端口，
    # 守护循环的「端口已监听 → 静默待命」逻辑阻止了新进程启动。
    #
    # 所以部署**必须**带升级意图，否则等于没部署。
    # （run_termux 收 argv 列表，argv[2:] 原样透传）
    code = run_termux(["run_termux.py", str(script), "--upgrade"])
    return (0, "守护会话已重建") if code == 0 else (code, f"[ERROR] 重建失败 exit={code}")


if __name__ == "__main__":
    sys.exit(main())
