#!/bin/bash
# 重建/修复长驻守护会话（幂等，可反复跑）
#
# ━━━ 存在的理由 ━━━
#
# 两个真实故障（2026-10-13 收尾时发现）：
#
#   1. **改了代码，长驻任务还在跑旧代码**
#      tmux 里的采集脚本是部署前启动的，新加的 checkpoint 逻辑
#      一直没生效，WAL 一路涨到 58 MB 才被发现。
#
#   2. **Android 杀后台导致会话丢失**
#      tmux 凌晨 02:21 被系统重建，social-insight 会话没恢复，
#      服务进程虽然活着，但没了守护循环——崩了不会自动拉起。
#
# 所以：① 部署后必须重建会话（deploy.py 已自动调用本脚本）
#      ② 本脚本幂等，随时可手动跑来自愈
#
# 用法（本机）：
#   python scripts/run_termux.py scripts/ops/restart-daemon.sh
set -u

TARGET=/opt/social-insight-mcp
TARGET_DIR="/opt/social-insight-mcp"

echo "===== 1. 重建前状态 ====="
tmux ls 2>/dev/null || echo "（无 tmux 会话）"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "会话 A：服务守护（social-insight）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
tmux kill-session -t social-insight 2>/dev/null && echo "  已停旧会话" || echo "  无旧会话"
sleep 2

# 守护循环脚本写成文件，避免 tmux 命令的引号地狱
HELPER="$HOME/.si-guard.sh"
cat > "$HELPER" <<'GUARD'
#!/bin/bash
# 服务守护循环（Termux 层）
#
# ━━━ 端口占用时静默等待 ━━━
#
# 守护循环刚重建、而旧服务进程还活着时，新进程会因端口占用退出。
# 旧实现会立刻重试（每 10s）→ 日志刷爆 + proot 反复报警。
# 现在：检测到 8100 已监听就静默待命，不重复启动。
while true; do
  if ss -tln 2>/dev/null | grep -q ':8100 '; then
    echo "[$(date '+%F %T')] 8100 已监听，守护待命"
    sleep 30
    continue
  fi

  echo "[$(date '+%F %T')] 启动 social-insight"
  proot-distro login ubuntu -- /opt/social-insight-mcp/start-mcp.sh
  echo "[$(date '+%F %T')] 进程退出，10s 后重试"
  sleep 10
done
GUARD
chmod +x "$HELPER"

tmux new-session -d -s social-insight "$HELPER"
echo "  ✅ 已创建（端口占用时静默待命，不刷日志）"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "会话 B：快照采集（social-insight-snapshot）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
tmux kill-session -t social-insight-snapshot 2>/dev/null && echo "  已停旧会话" || echo "  无旧会话"
sleep 2
tmux new-session -d -s social-insight-snapshot \
  "while true; do
     sleep 7200
     echo \"[\$(date '+%F %T')] 采集快照\"
     proot-distro login ubuntu -- bash -c 'cd $TARGET_DIR && export PYTHONPATH=$TARGET_DIR/src && ./venv/bin/python -m social_insight.snapshot --quiet --prune 30' \
       || echo '采集失败'
   done"
echo "  ✅ 已创建（每 2 小时，含 checkpoint + prune）"

echo ""
echo "===== 2. 等服务就绪（守护循环会拉起）====="
OK=0
for i in $(seq 1 40); do
  sleep 1
  if proot-distro login ubuntu -- bash -c \
       "ss -tln 2>/dev/null | grep -q ':8100 '" 2>/dev/null; then
    echo "  ✅ 8100 已监听（${i}s）"
    OK=1
    break
  fi
done
[ "$OK" -eq 0 ] && echo "  ⚠️ 40s 内未监听——看会话输出"

echo ""
echo "===== 3. 会话输出 ====="
tmux capture-pane -t social-insight -p 2>/dev/null | tail -6
echo ""
echo "--- snapshot ---"
tmux capture-pane -t social-insight-snapshot -p 2>/dev/null | tail -3

echo ""
echo "===== 4. 服务健康 ====="
proot-distro login ubuntu -- bash -c "ps aux | grep '[s]ocial_insight.http_server' | head -1" 2>/dev/null
proot-distro login ubuntu -- bash -c "curl -s -o /dev/null -w 'GET /mcp -> %{http_code}（400=正常）\n' --max-time 10 http://127.0.0.1:8100/mcp" 2>/dev/null

echo ""
echo "===== 5. WAL 状态（新代码应定期归零）====="
proot-distro login ubuntu -- bash -c "ls -lh $TARGET_DIR/data/snapshots.db* 2>/dev/null" 2>/dev/null

echo ""
echo "===== 全部会话 ====="
tmux ls 2>/dev/null
