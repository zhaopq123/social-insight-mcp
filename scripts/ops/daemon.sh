#!/bin/bash
# 建立服务守护循环（在 Termux 层执行）
#
# ━━━ 为什么是这样 ━━━
#   proot 没有 systemd（systemctl 不存在），且带 --kill-on-exit
#   （proot 一退，容器内进程全被杀）。所以后台脱离型服务活不下来，
#   必须让 proot 前台挂着，由外层 while 循环守护。
#
# 用法（在 Termux 层，8022）：
#   python scripts/run_remote.py scripts/ops/daemon.sh --termux
set -u

SESSION=social-insight
TARGET=/opt/social-insight-mcp
PORT=8100

echo "=== 1. 清理旧会话 ==="
if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
  echo "已停旧会话"
  sleep 2
else
  echo "无旧会话"
fi

echo ""
echo "=== 2. 停掉容器内的残留进程 ==="
proot-distro login ubuntu -- bash -c \
  "pkill -f social_insight.http_server 2>/dev/null; sleep 1; echo done" 2>&1 | tail -2

echo ""
echo "=== 3. 修复权限（tar 解包不带执行位）==="
proot-distro login ubuntu -- bash -c "chmod +x $TARGET/start-mcp.sh" 2>&1 | grep -v '^proot info'

echo ""
echo "=== 4. 建立守护会话 ==="
tmux new-session -d -s "$SESSION" \
  "while true; do
     echo \"[\$(date '+%F %T')] 启动 social-insight-mcp\"
     proot-distro login ubuntu -- $TARGET/start-mcp.sh
     echo \"[\$(date '+%F %T')] 进程退出，10 秒后重启\"
     sleep 10
   done"
echo "已创建会话: $SESSION"

echo ""
echo "=== 5. 等待就绪 ==="
for i in $(seq 1 45); do
  sleep 1
  if proot-distro login ubuntu -- bash -c \
       "ss -tln 2>/dev/null | grep -q ':${PORT} '" 2>/dev/null; then
    echo "端口 $PORT 已监听（${i}s）"
    break
  fi
  if [ "$i" -eq 45 ]; then
    echo "❌ 超时！会话输出："
    tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -25
    exit 1
  fi
done

echo ""
echo "=== 6. HTTP 探测（400 = 正常，MCP 要求 POST）==="
proot-distro login ubuntu -- bash -c \
  "curl -s -o /dev/null -w 'GET /mcp -> %{http_code}\n' --max-time 10 http://127.0.0.1:${PORT}/mcp" \
  2>&1 | grep -v '^proot info'
