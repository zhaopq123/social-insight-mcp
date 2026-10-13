#!/bin/bash
# 服务重启（在 Termux 层执行）
#
# 用途：部署新代码后重启服务。守护循环会在 10 秒内自动拉起新进程。
#
# 用法：
#   python scripts/run_remote.py scripts/ops/restart.sh --termux
set -u

TARGET=/opt/social-insight-mcp
PORT=8100

echo "=== 1. 强制停掉服务进程 ==="
proot-distro login ubuntu -- bash -c \
  "pkill -9 -f social_insight.http_server 2>/dev/null; echo killed" 2>&1 | grep -v '^proot info'

echo ""
echo "=== 2. 等待守护循环拉起（最多 60s）==="
for i in $(seq 1 60); do
  sleep 1
  if proot-distro login ubuntu -- bash -c \
       "ss -tln 2>/dev/null | grep -q ':${PORT} '" 2>/dev/null; then
    echo "已恢复（${i}s）"
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "❌ 60s 内未恢复！检查守护会话："
    tmux capture-pane -t social-insight -p 2>/dev/null | tail -25
    exit 1
  fi
done

echo ""
echo "=== 3. 健康探测 ==="
proot-distro login ubuntu -- bash -c \
  "curl -s -o /dev/null -w 'GET /mcp -> %{http_code}（400 = 正常）\n' --max-time 10 http://127.0.0.1:${PORT}/mcp" \
  2>&1 | grep -v '^proot info'

echo ""
echo "=== 4. 守护会话状态 ==="
tmux ls 2>&1 | grep social-insight || echo "⚠️ 守护会话不见了，需要重建：daemon.sh"
