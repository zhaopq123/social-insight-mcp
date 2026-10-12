"""快照存储层测试。

覆盖：写入去重、历史查询、持续在榜统计、清理、线程安全。
"""

from __future__ import annotations

import pytest

from social_insight.models import HotTopic
from social_insight.store import SnapshotStore, _normalize


def make_topic(word: str, platform: str = "douyin", hot: int | None = None,
               position: int = 1) -> HotTopic:
    return HotTopic(
        platform=platform,
        word=word,
        position=position,
        hot_value=hot,
        captured_at="2026-10-12T00:00:00Z",
    )


@pytest.fixture
def store(tmp_path):
    s = SnapshotStore(tmp_path / "test.db")
    yield s
    s.close()


class TestNormalize:
    def test_strips_noise(self):
        assert _normalize("#话题#") == "话题"

    def test_lowercase(self):
        assert _normalize("ABC") == "abc"

    def test_empty(self):
        assert _normalize("") == ""


class TestSave:
    def test_saves_topics(self, store):
        n = store.save_hot_topics([make_topic("话题A"), make_topic("话题B")])
        assert n == 2
        assert store.stats()["total_snapshots"] == 2

    def test_dedup_within_batch(self, store):
        """同批次内重复话题只存一次。"""
        n = store.save_hot_topics([
            make_topic("同一话题", platform="douyin"),
            make_topic("同一话题", platform="baidu"),
            make_topic("同一话题", platform="weibo"),
        ])
        assert n == 1

    def test_dedup_is_normalized(self, store):
        """标点不同的同一话题也应去重。"""
        n = store.save_hot_topics([make_topic("话题 A！"), make_topic("话题A")])
        assert n == 1

    def test_empty_input(self, store):
        assert store.save_hot_topics([]) == 0

    def test_skips_empty_words(self, store):
        assert store.save_hot_topics([make_topic(""), make_topic("###")]) == 0

    def test_multiple_batches(self, store):
        """不同批次相同话题会各存一次——这是趋势的来源。"""
        store.save_hot_topics([make_topic("话题A")])
        store.save_hot_topics([make_topic("话题A")])
        assert store.stats()["total_snapshots"] == 2
        assert store.stats()["distinct_topics"] == 1


class TestHistory:
    def test_empty_when_no_data(self, store):
        assert store.hot_history("不存在的") == []

    def test_returns_chronological(self, store):
        """历史必须按时间**正序**——调用方要画趋势线。"""
        for hot in [100, 200, 300]:
            store.save_hot_topics([make_topic("话题A", hot=hot)])
        h = store.hot_history("话题A")
        assert len(h) == 3
        assert [r["hot_value"] for r in h] == [100, 200, 300]

    def test_limit(self, store):
        for _ in range(5):
            store.save_hot_topics([make_topic("话题A")])
        assert len(store.hot_history("话题A", limit=2)) == 2


class TestTrending:
    def test_counts_occurrences(self, store):
        """出现次数越多 = 持续在榜越久。"""
        for _ in range(3):
            store.save_hot_topics([make_topic("热门话题")])
        store.save_hot_topics([make_topic("冷门话题")])

        items = store.trending_topics(limit=10)
        assert items[0]["word"] == "热门话题"
        assert items[0]["hits"] == 3
        assert items[1]["hits"] == 1

    def test_best_position(self, store):
        store.save_hot_topics([make_topic("话题", position=5)])
        store.save_hot_topics([make_topic("话题", position=1)])
        items = store.trending_topics()
        assert items[0]["best_position"] == 1

    def test_empty(self, store):
        assert store.trending_topics() == []


class TestMaintenance:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["total_snapshots"] == 0
        assert s["distinct_topics"] == 0
        assert s["earliest"] is None

    def test_prune_old(self, store):
        """清理旧数据——手机存储空间有限。"""
        store.save_hot_topics([make_topic("话题")])
        # 手动把时间改旧
        with store._tx() as c:
            c.execute("UPDATE hot_snapshots SET captured_at = '2020-01-01T00:00:00+00:00'")
        deleted = store.prune(keep_days=30)
        assert deleted >= 1
        assert store.stats()["total_snapshots"] == 0

    def test_prune_keeps_recent(self, store):
        store.save_hot_topics([make_topic("话题")])
        assert store.prune(keep_days=30) == 0
        assert store.stats()["total_snapshots"] == 1


class TestTimeFormatConsistency:
    """时间格式一致性——隐蔽但会静默给出错误结果的 bug。

    实测踩过的坑：写入用 ISO 格式（含 `T` 和 `+00:00`），
    查询用 SQLite 的 `datetime('now')`（空格分隔、无时区），
    字符串比较必然出错 —— 表现为「查不到任何数据」，但不报错。
    """

    def test_cutoff_format_matches_now(self):
        from social_insight.store import _cutoff, _now

        now, cut = _now(), _cutoff(hours=1)
        # 格式必须一致：要么都带 T，要么都不带
        assert ("T" in now) == ("T" in cut), f"格式不一致: {now} vs {cut}"
        assert now[:4] == cut[:4]  # 年份对齐
        # 字符串比较必须成立（这是查询依赖的前提）
        assert cut < now, "1 小时前的截断点必须小于当前时间"

    def test_cutoff_ordering(self):
        """更大的窗口 → 更早的截断点。"""
        from social_insight.store import _cutoff

        assert _cutoff(hours=48) < _cutoff(hours=1)
        assert _cutoff(days=30) < _cutoff(days=1)

    def test_trending_finds_recent_data(self, store):
        """刚写入的数据必须能被 trending 查到（这是格式一致的实战验证）。"""
        store.save_hot_topics([make_topic("刚写入的话题")])
        items = store.trending_topics(hours=1)
        assert len(items) == 1, "刚写入的数据查不到——时间格式不一致"
        assert items[0]["word"] == "刚写入的话题"

    def test_prune_format_consistency(self, store):
        """新数据不该被 prune 误删。"""
        store.save_hot_topics([make_topic("话题")])
        assert store.prune(keep_days=30) == 0, "新数据被误删——时间格式不一致"


class TestThreadSafety:
    def test_concurrent_writes(self, store):
        """多线程写入不能崩（SQLite 连接不跨线程共享）。"""
        import threading

        errors = []

        def worker(i: int) -> None:
            try:
                store.save_hot_topics([make_topic(f"话题{i}")])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写入出错: {errors}"
        assert store.stats()["total_snapshots"] == 10
