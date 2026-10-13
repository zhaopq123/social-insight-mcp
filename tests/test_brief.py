"""compress_title 测试：精准简要压缩。

━━━ 被铁律约束的模块━━━

用户要求：
1. **规则全部写死可查** —— 每条规则有编号、有注释、有独立正则
2. **绝不添加原文没有的信息** —— 只做删除/归一化，不改写

所以测试必须验证这两条，而不只是"压缩效果好不好"。
"""

from __future__ import annotations

from collections import Counter

import pytest

from social_insight.hot.brief import compress_title


def core(s: str) -> str:
    """忽略标点与空白的核心内容（用于比较信息量）。"""
    import re

    return re.sub(r"[？?。！!，,；;\s「」“”]", "", s)


class TestStripRules:
    """每条规则单独验证（写死可查）。"""

    def test_r1_asks_clause_removed(self):
        """R1a 「，如何看待…？」整段剥掉。"""
        w = "郑钦文中网夺冠，霸王茶姬被冲崩，如何看待这波狂欢？"
        b, did = compress_title(w)
        assert did is True
        assert "如何看待" not in b
        assert "郑钦文中网夺冠" in b
        assert "霸王茶姬被冲崩" in b

    def test_r1_question_mark_only_stripped(self):
        """R1c 主体是问句时只去问号，不动内容。

        ⚠️ 但 fuse 4 会把「仅去问号」判为无信息增量 → 返回原文。
        这是**刻意行为**（宁实勿虚）：调用方拿到的 brief 与原题一致，
        不会被虚假的"压缩感"误导。所以这里断言：
          · 问号可去掉（当内容也有实质精简时）
          · 但只去问号时 → 原样返回
        """
        w = "为什么坚果炒货卖不动了？"
        b, did = compress_title(w)
        # 只去问号 = 无增量 → 原样返回
        assert b == w
        assert did is False
        # 内容没被改
        assert "坚果炒货" in b

    def test_r1_real_compression_still_works(self):
        """有实质精简时，问号应该被去掉。"""
        w = "郑钦文中网夺冠，霸王茶姬被冲崩，如何看待这波狂欢？"
        b, did = compress_title(w)
        assert did is True
        assert "？" not in b
        assert "如何看待" not in b

    def test_r2_hype_prefix_removed(self):
        """R2 情绪前缀连标点一起剥，不留孤立符号。"""
        assert compress_title("刚刚！A股三大指数集体上涨")[0] == "A股三大指数集体上涨"
        assert compress_title("突发：某地发生地震")[0] == "某地发生地震"
        # 不能留下「：」开头
        for w in ("刚刚！某新闻", "突发：某新闻", "重磅：某新闻"):
            assert not compress_title(w)[0].startswith((":", "：", "！", "!"))

    def test_r3_decor_quotes_removed(self):
        """R3 装饰引号去掉，内容不动。

        ⚠️ 「如何看待『这波操作』？」整句是提问 + 核心内容极短 →
        fuse 4 判为无增量 → 原样返回（正确：这句话没有"事实"可压）。
        有实质内容时才生效。
        """
        # 有实质内容 → 引号被剥
        assert compress_title("李佳琦回应“疑似有孩子”")[0] == "李佳琦回应疑似有孩子"

        # 纯提问 + 极短内容 → 原样返回（不硬压）
        b, did = compress_title("如何看待「这波操作」？")
        assert "这波操作" in b
        # 剥掉问号与引号是无增量的 → did=False；保留原样也是可接受的诚实行为
        assert did is False or "「" not in b

    def test_r4_source_tail_removed(self):
        """R4 来源尾巴剥掉。"""
        b, _ = compress_title("给猫剃毛后它抑郁了，网友：哈哈哈哈哈")
        assert "网友" not in b
        assert "给猫剃毛" in b

    def test_r5_repeated_exclamations(self):
        """R5 重复感叹归一。"""
        w = "太离谱了！！！"
        b, _ = compress_title(w)
        assert "！！！" not in b


class TestNoNewInformation:
    """━━━ 铁律：绝不添加原文没有的信息 ━━━"""

    REAL_TITLES = [
        "郑钦文中网夺冠，十多个品牌集体发文祝贺，霸王茶姬被流量冲崩小程序，如何看待这波冠军商业「狂欢」？",
        "三天11万星的逆天开源REA，逆向分析一切，软件开发的门槛被打到了零，最该慌的为什么不是大厂？",
        "开源 Vue 3 组件库 Morya UI：把组件、文档、AI 工具链一起做进一个包",
        "李佳琦回应“疑似有孩子”",
        "2026诺贝尔经济学奖揭晓",
        "电脑卖不动了",
        "为什么会这样？",
        "为什么坚果炒货卖不动了？",
        "刚刚！A股三大指数集体上涨",
        "榴莲仅退款事件商家又遭仅退款",
        "红星照耀中国：新时代的伟大征程",
        "给猫剃毛后它抑郁了，网友：哈哈哈哈哈",
        "如何看？",
    ]

    @pytest.mark.parametrize("word", REAL_TITLES)
    def test_no_added_characters(self, word):
        """压缩结果不能出现原文没有的字符。

        这是最强的形式化检查：用 Counter 相减，
        新增字符必然显现。规则全是"删除"，所以恒为空。
        """
        brief, _ = compress_title(word)
        added = Counter(brief) - Counter(word)
        assert not added, f"出现原文没有的字符 {''.join(added)}: {word} → {brief}"

    @pytest.mark.parametrize("word", REAL_TITLES)
    def test_brief_is_subsequence_of_original(self, word):
        """压缩结果应是原文的子序列（顺序不被重排）。"""
        brief, did = compress_title(word)
        if not did:
            return
        # 允许字符被删除，不允许重排
        it = iter(word)
        assert all(c in it for c in brief), f"顺序被改动: {word} → {brief}"

    @pytest.mark.parametrize("word", REAL_TITLES)
    def test_core_content_preserved(self, word):
        """压缩不能丢核心信息（剥掉的内容应≤50%）。"""
        brief, did = compress_title(word)
        if not did:
            return
        ratio = len(core(brief)) / max(len(core(word)), 1)
        assert ratio >= 0.5, f"压缩过度（核心只剩 {ratio:.0%}）: {word} → {brief}"


class TestFuses:
    """保险丝——宁可少压缩，不可失真。"""

    @pytest.mark.parametrize(
        "word",
        [
            "为什么会这样？",     # 纯问句
            "如何看？",           # 极短问句
            "？",                 # 只有问号
            "",                  # 空
            "   ",               # 空白
        ],
    )
    def test_uncompressible_returns_original(self, word):
        brief, did = compress_title(word)
        if not word.strip():
            assert brief == ""
        else:
            assert did is False or core(brief) == core(word), (
                f"不可压缩的标题被改了: {word!r} → {brief!r}"
            )

    def test_too_short_keeps_original(self):
        """压缩后过短（<4 字）返回原文。"""
        w = "电脑卖不动了"
        b, did = compress_title(w)
        assert core(b) == core(w)

    def test_no_change_means_no_compression_flag(self):
        """只去问号 = 无信息增量 → 标为未压缩。"""
        w = "为什么会这样？"
        b, did = compress_title(w)
        if core(b) == core(w):
            assert did is False, "无信息增量却标为压缩过——宁实勿虚"


class TestNoFalseCompressionFlag:
    """━━━ 宁实勿虚：没压缩就别标 compressed ━━━

    实测踩过（2026-10-13）：服务端返回 `brief_compressed=true`，
    但 brief 与 title **完全一致**——调用方会误以为"做过精简"，
    实际上什么都没发生。

    所以必须断言：`did=True` ⟹ brief 与原文有实质差异。
    """

    @pytest.mark.parametrize(
        "word",
        [
            "红星照耀中国：新时代的伟大征程",   # 标点被清但核心未动
            "理记被禁止关注了",                # 完全没动
            "为什么会这样？",                  # 只去问号
            "2026诺贝尔经济学奖揭晓",
            "国考",
            "电脑卖不动了",
            "郑钦文登长城拍中网冠军写真",
        ],
    )
    def test_no_change_means_false(self, word):
        brief, did = compress_title(word)
        if core(brief) == core(word) or brief == word:
            assert did is False, (
                f"内容未变却标为压缩过（虚假标记）: {word} → {brief}"
            )

    @pytest.mark.parametrize(
        "word",
        [
            "郑钦文中网夺冠，霸王茶姬被流量冲崩，如何看待这波狂欢？",
            "刚刚！A股三大指数集体上涨",
            "李佳琦回应“疑似有孩子”",
            "给猫剃毛后它抑郁了，网友：哈哈哈哈哈",
        ],
    )
    def test_real_compression_means_true(self, word):
        """真压过的必须标 true（不能反向造假）。"""
        brief, did = compress_title(word)
        assert did is True, f"明明压过却标未压缩: {word} → {brief}"
        assert brief != word
    """正常标题不该被误伤。"""

    @pytest.mark.parametrize(
        "word",
        [
            "开源 Vue 3 组件库 Morya UI：把组件、文档、AI 工具链一起做进一个包",
            "2026诺贝尔经济学奖揭晓",
            "榴莲仅退款事件商家又遭仅退款",
            "郑钦文登长城拍中网冠军写真",
            "红星照耀中国：新时代的伟大征程",
        ],
    )
    def test_normal_title_unchanged(self, word):
        b, did = compress_title(word)
        assert core(b) == core(word), f"正常标题被改: {word} → {b}"
