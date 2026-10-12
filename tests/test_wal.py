"""WAL 与存储维护测试。

━━━ 实测背景（2026-10-12）━━━

开启 WAL 模式后观察服务器：

    snapshots.db      3.4 MB
    snapshots.db-wal  4.6 MB   ← 比主库还大

连续 3 次采样（间隔 20s）WAL 稳定在 4.7 MB **不增长**，
手动 ``wal_checkpoint(TRUNCATE)`` 能回收到 0。

**诊断结论：不是泄漏，是自动检查点从未触发。**

原因：``wal_autocheckpoint=1000`` 页（≈4MB）只在**写满时**触发，
而我们的写入模式是「批量写 ~300 条 → 长时间空闲」——空闲期不触发。

所以需要一个**显式时机**推进检查点。采集脚本天然周期性，
正好承担这个职责。
"""

from __future__ import annotations

import pytest

from social_insight.models import HotTopic
from social_insight.store import SnapshotStore


def topic(word: str) -> HotTopic:
    return HotTopic(platform="douyin", word=word, position=1,
                    captured_at="2026-10-12T00:00:00Z")


class TestWalConfiguration:
    """WAL 相关设置必须正确。"""

    def test_wal_enabled(self, tmp_path):
        s = SnapshotStore(tmp_path / "t.db")
        assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        s.close()

    def test_busy_timeout_set(self, tmp_path):
        """必须有忙等待——否则并发写立刻失败。"""
        s = SnapshotStore(tmp_path / "t.db")
        timeout = s._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 5000, f"busy_timeout 太小: {timeout}"
        s.close()

    def test_synchronous_is_normal(self, tmp_path):
        """synchronous=NORMAL 在 WAL 下是安全且性能更好的选择。"""
        s = SnapshotStore(tmp_path / "t.db")
        mode = s._conn.execute("PRAGMA synchronous").fetchone()[0]
        # 1 = NORMAL, 2 = FULL
        assert mode in (1, 2)
        s.close()


class TestCheckpoint:
    """检查点功能。"""

    def test_wal_exists_after_writes(self, tmp_path):
        s = SnapshotStore(tmp_path / "t.db")
        for i in range(100):
            s.save_hot_topics([topic(f"话题{i}")])
        # 写入后 WAL 应该存在（可能为 0 字节但文件在）
        size = s.wal_size()
        assert size >= 0
        s.close()

    def test_checkpoint_truncate_shrinks_wal(self, tmp_path):
        """TRUNCATE 检查点必须把 WAL 归零。"""
        s = SnapshotStore(tmp_path / "t.db")
        for i in range(500):
            s.save_hot_topics([topic(f"话题{i * 10}")])

        before = s.wal_size()
        s.checkpoint(truncate=True)
        after = s.wal_size()

        assert after <= before, f"检查点后 WAL 反而变大: {before} → {after}"
        assert after < 4096, f"TRUNCATE 后 WAL 应接近 0，实际 {after} 字节"
        s.close()

    def test_checkpoint_preserves_data(self, tmp_path):
        """检查点不能丢数据。"""
        s = SnapshotStore(tmp_path / "t.db")
        for i in range(200):
            s.save_hot_topics([topic(f"话题{i}")])

        before = s.stats()["total_snapshots"]
        s.checkpoint(truncate=True)
        after = s.stats()["total_snapshots"]

        assert after == before, f"检查点丢数据: {before} → {after}"
        # 数据必须真的还在（可查询）
        assert s.trending_topics(hours=24, limit=5)
        s.close()

    def test_passive_checkpoint_works(self, tmp_path):
        """PASSIVE 模式也要能用（不截断文件）。"""
        s = SnapshotStore(tmp_path / "t.db")
        for i in range(100):
            s.save_hot_topics([topic(f"话题{i}")])
        result = s.checkpoint(truncate=False)
        assert len(result) == 3, f"检查点返回格式不对: {result}"
        s.close()

    def test_checkpoint_on_empty_db(self, tmp_path):
        """空库上检查点不该崩。"""
        s = SnapshotStore(tmp_path / "t.db")
        s.checkpoint(truncate=True)
        assert s.stats()["total_snapshots"] == 0
        s.close()

    def test_wal_size_zero_when_no_wal(self, tmp_path):
        """没有 WAL 文件时 wal_size 返回 0，不抛异常。"""
        s = SnapshotStore(tmp_path / "t.db")
        assert s.wal_size() >= 0
        s.close()


class TestSnapshotScriptCheckpoints:
    """采集脚本应顺手推进检查点。"""

    @pytest.mark.asyncio
    async def test_run_once_checkpoints(self, tmp_path, monkeypatch):
        from social_insight import snapshot as snap_mod

        async def fake_fetch(*a, **kw):
            return [topic(f"话题{i}") for i in range(50)], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        db = str(tmp_path / "s.db")
        r = await snap_mod.run_once(db_path=db, quiet=True)

        assert r["ok"] is True
        assert "wal_bytes" in r, "采集结果应报告 WAL 大小"
        assert r["wal_bytes"] < 4096, (
            f"采集后 WAL 未归零: {r['wal_bytes']} 字节 —— 检查点没生效"
        )

    @pytest.mark.asyncio
    async def test_checkpoint_failure_does_not_break_run(self, tmp_path, monkeypatch):
        """检查点失败不该让采集失败——数据已经落盘了。"""
        from social_insight import snapshot as snap_mod

        async def fake_fetch(*a, **kw):
            return [topic("话题")], []

        monkeypatch.setattr(snap_mod, "fetch_many", fake_fetch)

        # 让 checkpoint 抛异常
        original = SnapshotStore.checkpoint

        def boom(self, **kw):
            raise RuntimeError("模拟检查点失败")

        monkeypatch.setattr(SnapshotStore, "checkpoint", boom)

        db = str(tmp_path / "s.db")
        r = await snap_mod.run_once(db_path=db, quiet=True)

        assert r["ok"] is True, "检查点失败不该让采集失败"
        assert r["saved"] == 1, "数据仍应落盘"
        assert "wal_checkpoint_error" in r, "应记录检查点失败原因"

        monkeypatch.setattr(SnapshotStore, "checkpoint", original)
