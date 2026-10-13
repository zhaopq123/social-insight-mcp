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
# ━━━ ⚠️ 跨层陷阱（实测踩过 2026-10-13）━━━
#
# 曾把「端口是否监听」的判断写在 Termux 层：
#     ss -tln | grep :8100
# 但 **8100 是容器内的端口，Termux 层根本看不到**——ss 为空 →
# 判断成「没人监听」→ 启动新进程 → 因端口占用退出 → 循环重启刷屏。
#
# 所以凡涉及容器内状态的判断，必须包一层 proot-distro login ubuntu。
# Termux 层只负责：tmux 会话、守护循环本身、文件标记。
#
# ━━━ 两种模式 ━━━
#
# 默认：端口已被占用 → 静默待命，不重复启动
#   （避免守护循环刚重建、旧进程还活着时反复重启）
#
# 升级模式（存在 /opt/social-insight-mcp/.upgrade）：
#   杀掉旧进程 → 启动新代码 → 删标记。
#   → 部署场景必需，否则新代码永远不生效（实测踩过）

#: 在容器内判断端口（Termux 层看不到容器端口）
port_busy() {
  proot-distro login ubuntu -- bash -c \
    "ss -tln 2>/dev/null | grep -q ':8100 '" 2>/dev/null
}

#: 在容器内取服务 PID
service_pids() {
  proot-distro login ubuntu -- bash -c \
    "ps aux 2>/dev/null | grep '[s]ocial_insight.http_server' | awk '{print \$2}'" \
    2>/dev/null | tr -d '[:space:]'
}

#: 升级标记是否存在（**必须在容器内判断**——标记文件在 /opt 下）
upgrade_pending() {
  proot-distro login ubuntu -- bash -c \
    "[ -f /opt/social-insight-mcp/.upgrade ]" 2>/dev/null
}

#: 删除升级标记（同样在容器内）
clear_upgrade_flag() {
  proot-distro login ubuntu -- bash -c \
    "rm -f /opt/social-insight-mcp/.upgrade" 2>/dev/null
}

while true; do
  # ── 升级模式：杀掉旧进程 ──
  if upgrade_pending; then
    echo "[$(date '+%F %T')] 检测到升级标记，杀掉旧进程"
    for pid in $(service_pids); do
      proot-distro login ubuntu -- kill -9 "$pid" 2>/dev/null && echo "  已杀 $pid"
    done
    # 等端口真正释放（容器内判断）
    for i in $(seq 1 10); do
      sleep 1
      if ! port_busy; then echo "  端口已释放（${i}s）"; break; fi
    done
    clear_upgrade_flag
  fi

  if port_busy; then
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

# 若带了升级意图，放升级标记
# ⚠️ 标记文件在 /opt/... 下 = 容器内路径，Termux 层**写不进去、也看不见**
#    （实测踩过：直接 touch 静默失败，[ -f ] 永远为假，升级标记从未生效）。
#    必须经 proot-distro 在容器内创建与判断。
if [ "${1:-}" = "--upgrade" ] || proot-distro login ubuntu -- \
     bash -c "[ -f $TARGET/.upgrade ]" 2>/dev/null; then
  proot-distro login ubuntu -- bash -c "touch $TARGET/.upgrade" \
    && echo "  → 已放置升级标记，守护循环将重启服务加载新代码" \
    || echo "  ⚠️ 升级标记创建失败（容器内 touch 失败）"
fi

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
