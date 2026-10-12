"""SQLite 快照存储层。

━━━ 为什么需要 ━━━

`hot_topics` 每次返回的都是**当下**的快照。要回答
「这个话题是在涨还是在跌」，必须有历史。

快照层的价值：
  · 热点趋势：某话题的热度/排名变化
  · 去重：避免同一话题反复入库
  · 缓存：减少上游请求

━━━ 为什么用 SQLite ━━━

单文件、零运维、标准库自带（Python 内置 sqlite3）。
服务器是手机上的 proot 容器，跑不起独立数据库服务。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hot_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    word        TEXT NOT NULL,
    norm_word   TEXT NOT NULL,
    position    INTEGER,
    hot_value   INTEGER,
    url         TEXT,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hot_norm ON hot_snapshots(norm_word);
CREATE INDEX IF NOT EXISTS idx_hot_time ON hot_snapshots(captured_at);
CREATE INDEX IF NOT EXISTS idx_hot_platform ON hot_snapshots(platform);

CREATE TABLE IF NOT EXISTS snapshots_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    """统一时间戳格式（UTC，ISO 8601）。

    ⚠️ **格式一致性极重要**：所有时间比较都在 SQL 里做字符串比较，
    所以写入与查询必须用**完全相同**的格式。

    实测踩过的坑：写入用 ISO 格式 ``2026-10-12T15:04:15+00:00``，
    而查询用 SQLite 的 ``datetime('now')``（返回 ``2026-10-12 15:04:15``），
    两者差在 ``T`` 与空格、以及时区后缀 —— 字符串比较会得出错误结果。

    解法：**不用 SQLite 的 datetime()，改在 Python 侧算好边界**。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cutoff(hours: int = 0, days: int = 0) -> str:
    """算出「N 小时/天前」的时间戳，格式与 `_now()` 一致。"""
    from datetime import timedelta

    delta = timedelta(hours=hours, days=days)
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")


def _normalize(word: str) -> str:
    """归一化标题，用于去重与查询。

    ⚠️ **转发到 corroborate.normalize**，不自己实现一遍。
    实测踩过：store 的归一化与 corroborate 不一致（一个剥离「突发」、
    另一个不剥），导致「同一话题」在存储层与匹配层被当作**不同话题**——
    表现为趋势数据被拆散、匹配漏判。

    延迟导入是为了避免模块级循环依赖（corroborate 不依赖 store）。
    """
    from .hot.corroborate import normalize

    return normalize(word)


class SnapshotStore:
    """热点快照存储。

    线程安全：SQLite 连接不能跨线程共享，所以每个线程一个连接
    （用 threading.local）。
    """

    def __init__(self, db_path: str | Path = "data/snapshots.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        with self._tx() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def save_hot_topics(self, topics: list[Any], *, batch_id: str | None = None) -> int:
        """保存一批热点快照。返回写入条数。

        同一批次内按 norm_word 去重——避免同一话题在多源重复入库。
        """
        ts = _now()
        seen: set[str] = set()
        rows = []
        for t in topics:
            norm = _normalize(getattr(t, "word", "") or "")
            if not norm or norm in seen:
                continue
            seen.add(norm)
            rows.append(
                (
                    getattr(t, "platform", ""),
                    getattr(t, "word", ""),
                    norm,
                    getattr(t, "position", None),
                    getattr(t, "hot_value", None),
                    getattr(t, "url", ""),
                    ts,
                )
            )
        if not rows:
            return 0

        with self._tx() as c:
            c.executemany(
                "INSERT INTO hot_snapshots "
                "(platform, word, norm_word, position, hot_value, url, captured_at) "
                "VALUES (?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def hot_history(self, word: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """某话题的历史快照（按时间正序）。

        ⚠️ 实测踩过的坑：只用 ``ORDER BY captured_at`` 排序时，
        同一秒内的多次写入顺序是**不确定的**（SQLite 不保证），
        会导致趋势线错乱（实测出现 [200, 100] 而非 [100, 200]）。

        所以加 ``id`` 作次级排序键——自增 id 严格反映插入顺序。
        """
        norm = _normalize(word)
        with self._tx() as c:
            rows = c.execute(
                "SELECT platform, word, position, hot_value, url, captured_at "
                "FROM hot_snapshots WHERE norm_word = ? "
                "ORDER BY captured_at DESC, id DESC LIMIT ?",
                (norm, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def trending_topics(self, *, hours: int = 24, limit: int = 20) -> list[dict[str, Any]]:
        """近期被反复抓到的话题——出现次数越多说明持续在榜。

        ⚠️ 时间边界在 Python 侧算好（见 `_cutoff` 的说明），
        不用 SQLite 的 ``datetime('now')`` —— 两者格式不同，字符串比较会出错。
        """
        with self._tx() as c:
            rows = c.execute(
                "SELECT word, norm_word, COUNT(*) AS hits, "
                "       MIN(position) AS best_position, "
                "       MAX(hot_value) AS max_hot, "
                "       MIN(captured_at) AS first_seen, "
                "       MAX(captured_at) AS last_seen "
                "FROM hot_snapshots "
                "WHERE captured_at >= ? "
                "GROUP BY norm_word "
                "ORDER BY hits DESC, best_position ASC "
                "LIMIT ?",
                (_cutoff(hours=hours), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        """存储层概况——用于诊断与验证。"""
        with self._tx() as c:
            total = c.execute("SELECT COUNT(*) FROM hot_snapshots").fetchone()[0]
            distinct = c.execute(
                "SELECT COUNT(DISTINCT norm_word) FROM hot_snapshots"
            ).fetchone()[0]
            earliest = c.execute("SELECT MIN(captured_at) FROM hot_snapshots").fetchone()[0]
            latest = c.execute("SELECT MAX(captured_at) FROM hot_snapshots").fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "total_snapshots": total,
            "distinct_topics": distinct,
            "earliest": earliest,
            "latest": latest,
        }

    def prune(self, *, keep_days: int = 30) -> int:
        """清理旧快照，防止无限增长（手机存储空间有限）。

        时间边界同样在 Python 侧算（见 `_cutoff`）。
        """
        with self._tx() as c:
            cur = c.execute(
                "DELETE FROM hot_snapshots WHERE captured_at < ?",
                (_cutoff(days=keep_days),),
            )
            return cur.rowcount
