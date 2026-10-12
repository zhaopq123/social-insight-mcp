#!/bin/bash
# 强杀恢复测试（在 Termux 层执行）
#
# 用途：验证「服务挂了能自动恢复」这个承诺是否真的成立。
#       proot 环境没有 systemd，可靠性全靠外层守护循环——
#       所以要定期证明它真的有效，而不是假设。
#
# 用法：
#   python scripts/run_remote.py scripts/ops/test-recovery.sh --termux
set -u

TARGET=/opt/social-insight-mcp
PORT=8100

echo "=== 1. 测试前状态 ==="
proot-distro login ubuntu -- bash -c \
  "ss -tln 2>/dev/null | grep ':${PORT} '" 2>&1 | grep -v '^proot info'
BEFORE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/mcp" 2>/dev/null || echo "000")
echo "杀之前 HTTP: $BEFORE"

echo ""
echo "=== 2. 强杀服务进程（模拟崩溃）==="
proot-distro login ubuntu -- bash -c \
  "pkill -9 -f social_insight.http_server; sleep 1; echo '已杀'" 2>&1 | grep -v '^proot info'

echo ""
echo "=== 3. 等待自动恢复（最多 60s）==="
RECOVERED=0
for i in $(seq 1 60); do
  sleep 1
  if proot-distro login ubuntu -- bash -c \
       "ss -tln 2>/dev/null | grep -q ':${PORT} '" 2>/dev/null; then
    echo "✅ 已恢复（${i}s）"
    RECOVERED=1
    break
  fi
done

if [ "$RECOVERED" -eq 0 ]; then
  echo "❌ 60s 内未恢复！守护可能已失效。会话输出："
  tmux capture-pane -t social-insight -p 2>/dev/null | tail -25
  echo ""
  echo "修复：重新建守护循环 → scripts/ops/daemon.sh"
  exit 1
fi

echo ""
echo "=== 4. 恢复后验证 ==="
AFTER=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/mcp" 2>/dev/null || echo "000")
echo "恢复后 HTTP: $AFTER（400 = 正常，MCP 要求 POST）"

echo ""
echo "=== 5. 守护会话仍存活 ==="
tmux ls 2>&1 | grep social-insight || echo "⚠️ 会话不见了"
