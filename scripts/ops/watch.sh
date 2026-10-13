#!/bin/bash
# 长时间观察：追踪服务的内存/延迟/存储趋势
#
# ━━━ 为什么需要 ━━━
#
# 单次压力测试覆盖不到「泄漏类」问题——内存泄漏、句柄泄漏、
# 磁盘增长都只在长时间运行后才显现。只有持续采样才能看出趋势。
#
# ━━━ 开销 ━━━
#
# 脚本自身约 3 MB（99% 时间在 sleep），每次采样起一个短命 Python 进程。
# 对手机上的 proot 容器几乎无感。
#
# 用法（容器层）：
#   nohup env INTERVAL=600 ROUNDS=61 bash scripts/ops/watch.sh > /tmp/si-watch.log 2>&1 &
set -u

TARGET=/opt/social-insight-mcp
PORT=8100
INTERVAL=${INTERVAL:-600}     # 默认 10 分钟
ROUNDS=${ROUNDS:-60}
#: 可用内存低于此值（MB）时在日志里标 WARN
MEM_WARN_MB=${MEM_WARN_MB:-500}
#: 单次响应超过此值（秒）时标 SLOW
RESP_WARN_S=${RESP_WARN_S:-10}

cd "$TARGET"
export PYTHONPATH="$TARGET/src"

echo "# 观察开始 $(date '+%F %T')  间隔=${INTERVAL}s  轮数=$ROUNDS"
echo "# 告警阈值: 可用内存 < ${MEM_WARN_MB}MB 或 响应 > ${RESP_WARN_S}s"
echo "# 时间 | RSS(KB) | 可用MB | SwapMB | WAL(KB) | DB(KB) | 快照 | 响应(s) | HTTP | 标记"
echo "#------|---------|--------|--------|---------|--------|------|---------|------|-----"

for i in $(seq 1 "$ROUNDS"); do
  TS=$(date '+%m-%d %H:%M')
  MARKS=""

  PID=$(ps aux 2>/dev/null | grep '[s]ocial_insight.http_server' | awk '{print $2}' | head -1)
  if [ -z "$PID" ]; then
    echo "$TS | 进程消失！"
    continue
  fi
  RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' ')

  # 系统内存（available 与 swap used，单位 MB）
  AVAIL=$(free -m 2>/dev/null | awk '/^Mem:/ {print $7}')
  SWAPU=$(free -m 2>/dev/null | awk '/^Swap:/ {print $3}')
  [ -z "$AVAIL" ] && AVAIL=0
  [ -z "$SWAPU" ] && SWAPU=0

  WAL=$(stat -c%s data/snapshots.db-wal 2>/dev/null || echo 0)
  WAL=$((WAL / 1024))
  DB=$(stat -c%s data/snapshots.db 2>/dev/null || echo 0)
  DB=$((DB / 1024))

  PROBE=$(./venv/bin/python - <<PYEOF 2>/dev/null || echo "0|0|ERR"
import json, time, urllib.request
try:
    t0 = time.time()
    req = urllib.request.Request('http://127.0.0.1:${PORT}/mcp',
        data=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'watch','version':'1'}}}).encode(),
        headers={'Content-Type':'application/json',
                 'Accept':'application/json, text/event-stream'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    dt = time.time() - t0
    import sqlite3
    c = sqlite3.connect('data/snapshots.db')
    n = c.execute('SELECT COUNT(*) FROM hot_snapshots').fetchone()[0]
    c.close()
    print('%.2f|%d|200' % (dt, n))
except Exception as e:
    print('0|0|ERR')
PYEOF
)

  RESP=$(echo "$PROBE" | cut -d'|' -f1)
  SNAPS=$(echo "$PROBE" | cut -d'|' -f2)
  CODE=$(echo "$PROBE" | cut -d'|' -f3)

  # 告警标记
  if [ "$AVAIL" -gt 0 ] && [ "$AVAIL" -lt "$MEM_WARN_MB" ]; then
    MARKS="$MARKS LOW_MEM"
  fi
  if [ "$SWAPU" -gt 1800 ]; then
    MARKS="$MARKS HIGH_SWAP"
  fi
  # 响应时间比较（用整数避免浮点比较）
  RESP_INT=$(echo "$RESP" | cut -d. -f1)
  if [ -n "$RESP_INT" ] && [ "$RESP_INT" -ge "$RESP_WARN_S" ] 2>/dev/null; then
    MARKS="$MARKS SLOW"
  fi
  if [ "$CODE" != "200" ]; then
    MARKS="$MARKS ERR"
  fi

  echo "$TS | $RSS | $AVAIL | $SWAPU | $WAL | $DB | $SNAPS | $RESP | $CODE |$MARKS"
  sleep "$INTERVAL"
done

echo "# 观察结束 $(date '+%F %T')"
