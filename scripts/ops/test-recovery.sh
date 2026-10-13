#!/bin/bash
# 崩溃恢复测试：强杀服务进程，验证守护循环能否自动拉起
#
# ⚠️ 本脚本跑在 **Termux 层**（8022），不是容器层。
#    原因：要读 tmux 会话输出（守护循环在 Termux 层），
#    且 `proot-distro` 本身是 Termux 命令。
#
#    以前在容器层跑，报 `proot-distro: command not found`。
#
# 用法：
#   python scripts/run_termux.py scripts/ops/test-recovery.sh
set -u

echo "===== 1. 测试前状态 ====="
if tmux has-session -t social-insight 2>/dev/null; then
  echo "  ✅ 守护会话存在"
else
  echo "  ❌ 守护会话不存在——先跑 restart-daemon.sh"
  exit 1
fi

HTTP_BEFORE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8100/mcp 2>/dev/null)
echo "  杀之前 HTTP: $HTTP_BEFORE"

PID_BEFORE=$(proot-distro login ubuntu -- bash -c \
  "ps aux | grep '[s]ocial_insight.http_server' | awk '{print \$2}' | head -1" 2>/dev/null | tr -d '[:space:]')
echo "  服务 PID: ${PID_BEFORE:-无}"

echo ""
echo "===== 2. 强杀服务进程（模拟崩溃）====="
if [ -n "$PID_BEFORE" ]; then
  proot-distro login ubuntu -- bash -c "kill -9 $PID_BEFORE" 2>/dev/null
  echo "  ✅ 已 kill -9"
else
  echo "  ⚠️ 进程本就不在"
fi

echo ""
echo "===== 3. 等待自动恢复（最多 90s）====="
OK=0
for i in $(seq 1 90); do
  sleep 1
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8100/mcp 2>/dev/null)
  if [ "$CODE" = "400" ] || [ "$CODE" = "200" ]; then
    echo "  ✅ ${i}s 内恢复（HTTP $CODE）"
    OK=1
    break
  fi
  [ $((i % 15)) -eq 0 ] && echo "  ...已等 ${i}s"
done

if [ "$OK" -eq 0 ]; then
  echo "  ❌ 90s 内未恢复——守护可能已失效。会话输出："
  tmux capture-pane -t social-insight -p 2>/dev/null | tail -15
  echo ""
  echo "  修复：python scripts/run_termux.py scripts/ops/restart-daemon.sh"
  exit 1
fi

echo ""
echo "===== 4. 恢复后的服务是否真能用 ====="
NEW_PID=$(proot-distro login ubuntu -- bash -c \
  "ps aux | grep '[s]ocial_insight.http_server' | awk '{print \$2}' | head -1" 2>/dev/null | tr -d '[:space:]')
echo "  新 PID: $NEW_PID（旧的是 $PID_BEFORE）"
if [ "$NEW_PID" = "$PID_BEFORE" ]; then
  echo "  ⚠️ PID 没变——可能根本没被杀掉"
fi

# 试一次真实工具调用
RESULT=$(proot-distro login ubuntu -- bash -c '
  cd /opt/social-insight-mcp
  PYTHONPATH=/opt/social-insight-mcp/src ./venv/bin/python - <<PYEOF 2>/dev/null || echo "调用失败"
import json, urllib.request
sid = None
def post(p):
    global sid
    h = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream",
         "MCP-Protocol-Version": "2025-06-18"}
    if sid: h["Mcp-Session-Id"] = sid
    req = urllib.request.Request("http://127.0.0.1:8100/mcp",
        data=json.dumps(p).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        sid = r.headers.get("Mcp-Session-Id", sid)
        return json.loads(r.read().decode())
d = post({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2025-06-18","capabilities":{},
    "clientInfo":{"name":"recovery","version":"1"}}})
d = post({"jsonrpc":"2.0","method":"tools/call","id":2,
    "params":{"name":"hot_topics","arguments":{"limit_per_source":10}}})
tool = d["result"]["content"][0]["text"]
parsed = json.loads(tool)
print("工具调用成功：%d 个话题" % parsed["data"]["summary"]["total"])
PYEOF
' 2>/dev/null)
echo "  $RESULT"

echo ""
echo "===== 5. 守护会话输出（确认是它拉起的）====="
tmux capture-pane -t social-insight -p 2>/dev/null | tail -8
