"""边界与鲁棒性测试（之前未覆盖的维度）。

━━━ 为什么需要这一类 ━━━

之前的测试都在验证「正常输入给出正确结果」。
但**真实世界会送来奇怪的东西**：空字符串、超长文本、emoji、
控制字符、None、错误类型……这些在生产里迟早会遇到。

这类问题的特点是：**不测就不知道会崩在哪**。
"""

from __future__ import annotations

import pytest

from social_insight.errors import ErrorCode, ToolError
from social_insight.hot.corroborate import corroborate, normalize
from social_insight.hot.match import (
    AccountProfile,
    _domain_score,
    _normalize,
    match_topics,
)
from social_insight.models import ContentItem, Creator, HotTopic, Page
from social_insight.store import SnapshotStore, _normalize as store_normalize


def topic(word: str, platform: str = "douyin") -> HotTopic:
    return HotTopic(platform=platform, word=word, position=1, captured_at="2026-10-12T00:00:00Z")


# ══════════════════════════════════════════════════════════════════
# 1. 极端输入
# ══════════════════════════════════════════════════════════════════


class TestExtremeInputs:
    """超大 / 超小 / 畸形输入不该让系统崩溃。"""

    def test_very_long_word(self):
        """超长标题（10 万字符）不该崩，也不该卡死。"""
        long_word = "测试" * 50_000
        assert normalize(long_word)
        assert _normalize(long_word)
        assert store_normalize(long_word)

    def test_very_long_word_in_matching(self):
        long_word = "AI工具" * 20_000
        profile = AccountProfile(keywords=["AI"], domains=["software"])
        result = match_topics([topic(long_word)], profile)
        assert len(result) == 1
        assert 0.0 <= result[0].score <= 1.0

    def test_single_character(self):
        for f in (normalize, _normalize, store_normalize):
            assert f("a") == "a"
            assert f("中") == "中"

    def test_only_punctuation(self):
        for f in (normalize, _normalize, store_normalize):
            assert f("！！！") == ""
            assert f("###") == ""
            assert f("   ") == ""

    def test_emoji_only(self):
        """纯 emoji 会被当作噪声剥离。"""
        assert normalize("🎉🎉🎉") == ""

    def test_emoji_mixed(self):
        """emoji 混在中文里，中文要保留。"""
        assert "话题" in normalize("🎉话题🎉")

    def test_control_characters(self):
        """控制字符不该崩，也不该残留。"""
        result = normalize("话\x00题\x1f测试")
        assert "话题" in result or "话题测试" in result

    def test_newlines_and_tabs(self):
        assert normalize("话\n题\t测试") == "话题测试"

    def test_rtl_and_combining(self):
        """从右到左文字与组合字符不该崩。"""
        assert normalize("مرحبا") or True  # 不该抛异常即可

    def test_null_bytes_in_store(self, tmp_path):
        """含 \x00 的文本写 SQLite 不该崩。"""
        s = SnapshotStore(tmp_path / "t.db")
        n = s.save_hot_topics([topic("含\x00空字节")])
        assert n >= 0  # 存或不存都可接受，但不能崩
        s.close()

    def test_whitespace_variants(self):
        for ws in ("\n", "\r\n", "\t", "\u3000", "\u00a0"):
            assert normalize(f"话{ws}题") == "话题"


# ══════════════════════════════════════════════════════════════════
# 2. 类型与 None
# ══════════════════════════════════════════════════════════════════


class TestTypeRobustness:
    """错误类型不该产生难以理解的崩溃。"""

    def test_normalize_none(self):
        assert normalize(None) == ""  # type: ignore[arg-type]

    def test_normalize_empty_string(self):
        assert normalize("") == ""

    def test_domain_score_empty(self):
        assert _domain_score("", "retail") == 0.0

    def test_match_with_empty_profile(self):
        p = AccountProfile()  # 全空
        result = match_topics([topic("任意话题")], p)
        assert len(result) == 1
        assert result[0].score <= 0.2

    def test_match_empty_topics(self):
        assert match_topics([], AccountProfile(keywords=["a"])) == []

    def test_model_with_none_fields(self):
        """模型字段为 None 时序列化不该崩。"""
        c = ContentItem(platform="douyin", work_id="x", title=None)  # type: ignore[arg-type]
        d = c.to_dict()
        assert d["work_id"] == "x"

    def test_page_with_non_model_items(self):
        """Page 里混非模型对象不该崩。"""
        p = Page(items=[{"raw": "dict"}, "string"])
        d = p.to_dict()
        assert d["count"] == 2


# ══════════════════════════════════════════════════════════════════
# 3. 并发与幂等
# ══════════════════════════════════════════════════════════════════


class TestConcurrency:
    """并发场景之前完全没测过。"""

    def test_concurrent_store_writes(self, tmp_path):
        """多线程写快照——SQLite 连接不能跨线程共享。"""
        import threading

        s = SnapshotStore(tmp_path / "t.db")
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                for j in range(10):
                    s.save_hot_topics([topic(f"线程{i}话题{j}")])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发写失败: {errors[:3]}"
        assert s.stats()["total_snapshots"] == 80
        s.close()

    def test_concurrent_read_write(self, tmp_path):
        """读写并发——读不该被写阻塞到失败。"""
        import threading

        s = SnapshotStore(tmp_path / "t.db")
        errors: list[Exception] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                i = 0
                while not stop.is_set() and i < 50:
                    s.save_hot_topics([topic(f"话题{i}")])
                    i += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def reader() -> None:
            try:
                while not stop.is_set():
                    s.trending_topics(hours=24)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        tw = threading.Thread(target=writer)
        trs = [threading.Thread(target=reader) for _ in range(3)]
        tw.start()
        for t in trs:
            t.start()
        tw.join()
        stop.set()
        for t in trs:
            t.join()

        assert not errors, f"读写并发出错: {errors[:3]}"
        s.close()

    def test_idempotent_corroborate(self):
        """同一输入多次调用结果一致（无隐藏状态）。"""
        topics = [topic("话题A", "a"), topic("话题A", "b")]
        args = {
            "independence_of": {"a": "a", "b": "b"},
            "weight_of": {"a": 1.0, "b": 1.0},
            "display_of": {"a": "A", "b": "B"},
        }
        r1 = corroborate(topics, **args)
        r2 = corroborate(topics, **args)
        assert [(x.word, x.source_count) for x in r1] == [(x.word, x.source_count) for x in r2]

    def test_idempotent_match(self):
        topics = [topic("某AI工具"), topic("门店经营")]
        p = AccountProfile(keywords=["AI", "门店"], domains=["software", "retail"])
        r1 = match_topics(topics, p)
        r2 = match_topics(topics, p)
        assert [(m.word, m.score) for m in r1] == [(m.word, m.score) for m in r2]

    def test_match_does_not_mutate_input(self):
        """匹配不该改动传入的 profile 或 topics。"""
        p = AccountProfile(keywords=["AI"], domains=["software"])
        original_kw = list(p.keywords)
        original_dom = list(p.domains)
        topics = [topic("某AI工具")]
        original_word = topics[0].word

        match_topics(topics, p)

        assert p.keywords == original_kw
        assert p.domains == original_dom
        assert topics[0].word == original_word


# ══════════════════════════════════════════════════════════════════
# 4. 规模 / 性能
# ══════════════════════════════════════════════════════════════════


class TestScale:
    """大数据量下的行为——之前只测过几十条。"""

    def test_corroborate_with_1000_topics(self):
        """1000 条热点的交叉验证必须在合理时间内完成。"""
        import time

        topics = []
        for i in range(1000):
            topics.append(topic(f"话题{i}", f"src{i % 10}"))
        # 加一些重复项，制造真实聚类压力
        for i in range(200):
            topics.append(topic(f"话题{i}", f"other{i % 5}"))

        indep = {f"src{i}": f"src{i}" for i in range(10)}
        indep.update({f"other{i}": f"other{i}" for i in range(5)})

        t0 = time.time()
        results = corroborate(
            topics,
            independence_of=indep,
            weight_of={k: 0.5 for k in indep},
            display_of={k: k for k in indep},
        )
        elapsed = time.time() - t0
        assert len(results) > 0
        # 1000 条应在 10 秒内完成（正常是亚秒级）
        assert elapsed < 10, f"太慢: {elapsed:.1f}s"

    def test_match_with_1000_topics(self):
        import time

        topics = [topic(f"AI工具{i}") for i in range(500)] + [
            topic(f"门店经营{i}") for i in range(500)
        ]
        p = AccountProfile(keywords=["AI", "门店"], domains=["software", "retail"])

        t0 = time.time()
        results = match_topics(topics, p, top_n=50)
        elapsed = time.time() - t0
        assert len(results) == 50
        assert elapsed < 10, f"匹配太慢: {elapsed:.1f}s"

    def test_store_bulk_insert(self, tmp_path):
        import time

        s = SnapshotStore(tmp_path / "t.db")
        topics = [topic(f"话题{i}") for i in range(2000)]
        t0 = time.time()
        n = s.save_hot_topics(topics)
        elapsed = time.time() - t0
        assert n == 2000
        assert elapsed < 10, f"批量写入太慢: {elapsed:.1f}s"
        s.close()

    def test_query_performance_with_large_table(self, tmp_path):
        """大表查询必须有索引支撑，不能全表扫。"""
        import time

        s = SnapshotStore(tmp_path / "t.db")
        for batch in range(20):
            s.save_hot_topics([topic(f"话题{i}") for i in range(100)])

        t0 = time.time()
        for _ in range(50):
            s.trending_topics(hours=24, limit=20)
        elapsed = time.time() - t0
        assert elapsed < 5, f"查询太慢（可能缺索引）: {elapsed:.2f}s"
        s.close()


# ══════════════════════════════════════════════════════════════════
# 5. 数据一致性
# ══════════════════════════════════════════════════════════════════


class TestDataConsistency:
    """跨组件的一致性——同一文本在不同模块的归一化必须一致。"""

    def test_normalizers_agree(self):
        """corroborate / match / store 三处的归一化必须一致。

        ⚠️ 不一致会导致：去重失效、匹配漏判、趋势查不到。
        实测踩过：store 的归一化与 corroborate 的不完全一致，
        会让「同一话题」在不同模块被当作不同话题。
        """
        samples = [
            "测试话题",
            "#话题#",
            "话题 A！",
            "【突发】新闻",
            "ABc 测试",
            "话题\u3000带空格",
        ]
        for s in samples:
            a = normalize(s)
            b = _normalize(s)
            assert a == b, f"corroborate 与 match 归一化不一致: {s!r} → {a!r} vs {b!r}"

    def test_store_normalize_matches(self):
        """store 的归一化与 corroborate 对齐（去重依赖它）。"""
        for s in ["测试话题", "#话题#", "话题 A！"]:
            assert store_normalize(s) == normalize(s), f"store 与 corroborate 不一致: {s!r}"

    def test_dedup_consistent_across_layers(self, tmp_path):
        """同一话题在 store 与 corroborate 里应被识别为同一个。"""
        s = SnapshotStore(tmp_path / "t.db")
        s.save_hot_topics([topic("霸王茶姬店员劝退消费者")])
        s.save_hot_topics([topic("霸王茶姬店员劝退消费者！")])  # 只差标点
        stats = s.stats()
        assert stats["distinct_topics"] == 1, "store 层去重失效"
        s.close()

    def test_engagement_none_vs_zero_consistent(self):
        """engagement 的 None/0 语义在所有模型上一致。"""
        zero = ContentItem(platform="x", work_id="1", like_count=0, comment_count=0)
        none = ContentItem(platform="x", work_id="2")
        assert zero.engagement == 0
        assert none.engagement is None

    def test_creator_none_vs_zero(self):
        z = Creator(platform="x", creator_id="1", follower_count=0)
        n = Creator(platform="x", creator_id="2")
        assert z.to_dict()["follower_count"] == 0
        assert n.to_dict()["follower_count"] is None


# ══════════════════════════════════════════════════════════════════
# 6. 不变式（任何输入下都必须成立）
# ══════════════════════════════════════════════════════════════════


class TestInvariants:
    """这些性质必须**永远**成立，与输入无关。"""

    @pytest.mark.parametrize(
        "word",
        ["", "a", "话题", "🎉", "A" * 1000, "测试" * 500, "！@#￥%……", "test\n\r\t"],
    )
    def test_score_always_in_range(self, word):
        p = AccountProfile(keywords=["AI", "门店"], domains=["software", "retail"])
        for m in match_topics([topic(word)], p):
            assert 0.0 <= m.score <= 1.0, f"{word!r} 得分越界: {m.score}"

    @pytest.mark.parametrize("word", ["", "a", "话题", "🎉🎉", "！@#"])
    def test_normalize_never_raises(self, word):
        for f in (normalize, _normalize, store_normalize):
            f(word)  # 不抛异常即可

    def test_corroborate_count_never_exceeds_input(self):
        topics = [topic(f"话题{i}", f"s{i % 3}") for i in range(50)]
        results = corroborate(
            topics,
            independence_of={f"s{i}": f"s{i}" for i in range(3)},
            weight_of={f"s{i}": 1.0 for i in range(3)},
            display_of={f"s{i}": f"S{i}" for i in range(3)},
        )
        assert len(results) <= len(topics)

    def test_source_count_never_exceeds_distinct_sources(self):
        topics = [topic("同一话题", f"s{i}") for i in range(5)]
        results = corroborate(
            topics,
            independence_of={f"s{i}": f"s{i}" for i in range(5)},
            weight_of={f"s{i}": 1.0 for i in range(5)},
            display_of={f"s{i}": f"S{i}" for i in range(5)},
        )
        assert results[0].source_count <= 5

    def test_match_respects_top_n(self):
        topics = [topic(f"话题{i}") for i in range(100)]
        p = AccountProfile(keywords=["话题"], domains=["software"])
        for n in [0, 1, 5, 50, 200]:
            assert len(match_topics(topics, p, top_n=n)) <= max(0, n)

    def test_scores_sorted_descending(self):
        topics = [topic(f"AI工具{i}") for i in range(20)] + [topic("无关话题")]
        p = AccountProfile(keywords=["AI"], domains=["software"])
        scores = [m.score for m in match_topics(topics, p, top_n=30)]
        assert scores == sorted(scores, reverse=True)

    def test_error_always_has_code(self):
        """所有错误都必须有 code —— 这是对外契约的基础。"""
        for code in ErrorCode:
            e = ToolError(code=code, message="x")
            assert e.to_dict()["error"]["code"] == code.value
