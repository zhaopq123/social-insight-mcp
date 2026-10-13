#!/bin/bash
# 守护会话健康检查 + 自愈（Termux 层）
#
# ━━━ 设计要点 ━━━
#
# 本脚本只跑在 Termux 层（8022），因此：
#   · 非 tmux 相关操作，都用 `exec host ...`（宿主层命令）而非找 /opt 路径
#   · 只有需要读容器内进程/文件时，才包一层 proot-distro
#   · 自身不依赖任何绝对路径
#
# 直接引用 /opt 是踩过的坑——容器内路径在 Termux 层不可见。
#
# 用法：
#   bash check-daemon.sh            # 只检查
#   bash check-daemon.sh --repair   # 检查并自愈
set -u

REPAIR=0
[ "${1:-}" = "--repair" ] && REPAIR=1

echo "===== 守护会话健康检查 $(date '+%F %T') ====="
echo ""

NEED_REPAIR=0
for SESSION in social-insight social-insight-snapshot; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "  ✅ tmux 会话 '$SESSION' 存活"
  else
    echo "  ❌ tmux 会话 '$SESSION' 缺失"
    NEED_REPAIR=1
  fi
done

echo ""
echo "===== 服务进程与端口（经容器层）====="
proot-distro login ubuntu -- bash -c '
  PID=$(ps aux 2>/dev/null | grep "[s]ocial_insight.http_server" | awk "{print \$2}" | head -1)
  if [ -n "$PID" ]; then
    ETIME=$(ps -o etime= -p "$PID" 2>/dev/null | tr -d " ")
    RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d " ")
    echo "  ✅ 服务运行中 PID=$PID 已运行=$ETIME RSS=$((RSS/1024))MB"
  else
    echo "  ❌ 服务进程不存在"
  fi
  if ss -tln 2>/dev/null | grep -q ":8100 "; then
    echo "  ✅ 端口 8100 监听中"
  else
    echo "  ❌ 端口 8100 未监听"
  fi
  WAL=$(stat -c%s /opt/social-insight-mcp/data/snapshots.db-wal 2>/dev/null || echo 0)
  echo "  WAL = $((WAL/1024)) KB"
' 2>/dev/null

echo ""
if [ "$NEED_REPAIR" -eq 1 ] && [ "$REPAIR" -eq 1 ]; then
  echo "─── 开始自愈（调用容器内的 restart-daemon.sh）──"
  # 容器内脚本，必须经 proot-distro 执行
  proot-distro login ubuntu -- \
    bash /opt/social-insight-mcp/scripts/ops/restart-daemon.sh 2>&1 | tail -30
  exit 0
fi

if [ "$NEED_REPAIR" -eq 1 ]; then
  echo "⚠️ 发现缺失，加 --repair 可自动重建"
else
  echo "✅ 一切正常"
fi
