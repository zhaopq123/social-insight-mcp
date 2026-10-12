#!/bin/bash
# 长时间观察：定期采样服务指标，追踪内存/延迟/存储的增长趋势
#
# 用途：单次压力测试覆盖不到「泄漏类」问题——
# 只有持续观察才能发现缓慢增长的内存或存储。
#
# 用法（在容器层执行，后台跑）：
#   nohup bash scripts/ops/watch.sh > /tmp/si-watch.log 2>&1 &
set -u

TARGET=/opt/social-insight-mcp
PORT=8100
INTERVAL=${INTERVAL:-300}     # 每 5 分钟采一次
ROUNDS=${ROUNDS:-24}          # 采 24 次 = 2 小时

cd "$TARGET"
export PYTHONPATH="$TARGET/src"

echo "# 长时间观察开始 $(date '+%F %T')  间隔=${INTERVAL}s  轮数=$ROUNDS"
echo "# 时间 | RSS(KB) | CPU% | WAL(KB) | DB(KB) | 快照数 | 响应(s) | HTTP"
echo "#------|---------|------|---------|--------|--------|---------|-----"

for i in $(seq 1 "$ROUNDS"); do
  TS=$(date '+%H:%M:%S')

  # 进程指标
  PID=$(ps aux 2>/dev/null | grep '[s]ocial_insight.http_server' | awk '{print $2}' | head -1)
  if [ -z "$PID" ]; then
    echo "$TS | 进程消失！"
    continue
  fi
  RSS=$(ps -o rss= -p "$PID" 2>/dev/null | tr -d ' ')
  CPU=$(ps -o pcpu= -p "$PID" 2>/dev/null | tr -d ' ')

  # 存储指标
  WAL=$(stat -c%s data/snapshots.db-wal 2>/dev/null || echo 0)
  DB=$(stat -c%s data/snapshots.db 2>/dev/null || echo 0)
  WAL=$((WAL / 1024))
  DB=$((DB / 1024))

  # 快照数 + 响应时间（用一次轻量调用探测）
  PROBE=$(./venv/bin/python - <<PYEOF
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
    print('0|0|ERR:%s' % str(e)[:20])
PYEOF
)

  RESP=$(echo "$PROBE" | cut -d'|' -f1)
  SNAPS=$(echo "$PROBE" | cut -d'|' -f2)
  CODE=$(echo "$PROBE" | cut -d'|' -f3)

  echo "$TS | $RSS | $CPU | $WAL | $DB | $SNAPS | $RESP | $CODE"
  sleep "$INTERVAL"
done

echo "# 观察结束 $(date '+%F %T')"
