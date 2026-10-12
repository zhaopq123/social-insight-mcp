#!/bin/bash
# social-insight-mcp · 启动脚本（在 proot Ubuntu 容器内运行）
#
# 由 Termux 层的守护循环调用：
#   while true; do
#     proot-distro login ubuntu -- /opt/social-insight-mcp/start-mcp.sh
#     sleep 10
#   done
#
# 为什么不写 systemd unit：proot 环境**没有 systemd**，systemctl 不存在。
# 为什么不后台 nohup：proot 带 --kill-on-exit，proot 一退容器内进程全被杀，
#                    后台脱离型进程活不下来。必须前台运行。
set -eu

TARGET=/opt/social-insight-mcp
ENVFILE=/etc/social-insight-mcp/env

cd "$TARGET"

# ⚠️ 必需：源码在 src/ 下，不设 PYTHONPATH 会
# ModuleNotFoundError: No module named 'social_insight'（实测踩过）
export PYTHONPATH="$TARGET/src"

# 环境变量（可选）
if [ -r "$ENVFILE" ]; then
  set -a
  . "$ENVFILE"
  set +a
fi

: "${HOST:=0.0.0.0}"
: "${PORT:=8100}"
: "${LOG_LEVEL:=INFO}"
export HOST PORT LOG_LEVEL

echo "[social-insight] 启动 $(date '+%F %T')  http://${HOST}:${PORT}/mcp"

# exec 让 python 成为主进程，SIGTERM 能直达
exec "$TARGET/venv/bin/python" -m social_insight.http_server
