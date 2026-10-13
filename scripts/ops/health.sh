#!/bin/bash
# 服务健康巡检（在容器层执行）
#
# 一次性检查：进程 / 端口 / 工具注册 / 快照库 / 最近的错误
#
# 用法：
#   python scripts/run_remote.py scripts/ops/health.sh
set -u

TARGET=/opt/social-insight-mcp
PORT=8100
cd "$TARGET"
export PYTHONPATH="$TARGET/src"

echo "===== 1. 进程 ====="
ps aux | grep '[s]ocial_insight.http_server' | head -2 || echo "❌ 进程不存在"

echo ""
echo "===== 2. 端口 ====="
ss -tln 2>/dev/null | grep ":${PORT} " || echo "❌ 端口 $PORT 未监听"

echo ""
echo "===== 3. 工具注册 ====="
./venv/bin/python - <<'PYEOF'
import sys
sys.path.insert(0, 'src')
try:
    from social_insight.server import TOOL_DEFS
    names = [t['name'] for t in TOOL_DEFS]
    print("工具数: %d" % len(names))
    for n in names:
        print("  -", n)
except Exception as exc:
    print("❌ 导入失败:", exc)
PYEOF

echo ""
echo "===== 4. 快照库 ====="
./venv/bin/python - <<'PYEOF'
import sys, json
sys.path.insert(0, 'src')
try:
    from social_insight.store import SnapshotStore
    s = SnapshotStore('data/snapshots.db')
    st = s.stats()
    print("快照总数: %s" % st['total_snapshots'])
    print("去重话题: %s" % st['distinct_topics'])
    print("最早/最新: %s / %s" % (st['earliest'], st['latest']))
    s.close()
except Exception as exc:
    print("❌ 快照库读取失败:", exc)
PYEOF

echo ""
echo "===== 5. 数据目录占用 ====="
du -sh data 2>/dev/null || echo "无 data 目录"
