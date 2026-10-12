#!/bin/bash
# 建立快照定时采集（在 Termux 层执行）
#
# ━━━ 为什么需要 ━━━
#   trend 的价值取决于**时间序列密度**。只在有人调 hot_topics 时才落快照，
#   数据点稀疏且不规则，「这在涨吗」就答不准。
#
# ━━━ 为什么用 while+sleep 而不是 cron ━━━
#   proot 里没有 systemd，cron 也不一定可用。用 tmux 里的常驻循环最稳，
#   且和 daemon.sh 的做法一致（同一套心智模型）。
#
# 用法：
#   python scripts/run_remote.py scripts/ops/snapshot-cron.sh --termux
set -u

SESSION=social-insight-snapshot
TARGET=/opt/social-insight-mcp
INTERVAL=7200   # 2 小时。对热点趋势足够，也不给源站压力。

echo "=== 1. 清理旧会话 ==="
tmux kill-session -t "$SESSION" 2>/dev/null && echo "已停旧会话" || echo "无旧会话"

echo ""
echo "=== 2. 立即采一次（验证可用）==="
proot-distro login ubuntu -- bash -c "
  cd $TARGET
  export PYTHONPATH=$TARGET/src
  ./venv/bin/python -m social_insight.snapshot --limit 50 --prune 30
" 2>&1 | grep -v '^proot info'

echo ""
echo "=== 3. 建立定时循环（每 $((INTERVAL/3600)) 小时）==="
tmux new-session -d -s "$SESSION" \
  "while true; do
     sleep $INTERVAL
     echo \"[\$(date '+%F %T')] 采集快照\"
     proot-distro login ubuntu -- bash -c \
       'cd $TARGET && export PYTHONPATH=$TARGET/src && \
        ./venv/bin/python -m social_insight.snapshot --quiet --prune 30' \
       || echo '采集失败'
   done"
echo "已创建会话: $SESSION"

echo ""
echo "=== 4. tmux 会话清单 ==="
tmux ls 2>&1
