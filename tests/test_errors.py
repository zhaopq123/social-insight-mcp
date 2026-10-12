"""错误信封契约测试。

━━━ 为什么这些测试重要 ━━━

本项目的核心设计原则是「**绝不静默失败**」：
能力缺失必须返回**结构化的、带解释的、带替代路径的**错误。

如果错误信封结构不稳定，调用方（LLM）就无法可靠地处理失败——
它可能把「需要登录」误读为「没有数据」，把「签名缺失」误读为「平台没内容」。

所以错误信封的结构本身就是**对外契约**，必须锁住。
"""

from __future__ import annotations

import json


from social_insight.errors import (
    ErrorCode,
    ToolError,
    auth_required,
    platform_not_implemented,
    signature_required,
    success,
)


class TestToolErrorStructure:
    def test_minimal_error(self):
        e = ToolError(code=ErrorCode.NOT_FOUND, message="没找到")
        d = e.to_dict()
        assert d["ok"] is False
        assert d["error"]["code"] == "NOT_FOUND"
        assert d["error"]["message"] == "没找到"
        assert d["error"]["retryable"] is False

    def test_full_error(self):
        e = ToolError(
            code=ErrorCode.AUTH_REQUIRED,
            message="需要登录",
            platform="xiaohongshu",
            reason="无匿名路径",
            alternatives=["扫码登录", "用第三方服务"],
            retryable=True,
            details={"k": "v"},
        )
        d = e.to_dict()["error"]
        assert d["platform"] == "xiaohongshu"
        assert d["reason"] == "无匿名路径"
        assert len(d["alternatives"]) == 2
        assert d["retryable"] is True
        assert d["details"] == {"k": "v"}

    def test_optional_fields_omitted_when_empty(self):
        """空字段不该出现在 JSON 里——省 token，也让结构更清晰。"""
        e = ToolError(code=ErrorCode.NOT_FOUND, message="x")
        d = e.to_dict()["error"]
        assert "platform" not in d
        assert "reason" not in d
        assert "alternatives" not in d
        assert "details" not in d

    def test_is_exception(self):
        """必须是 Exception，适配层才能 raise。"""
        e = ToolError(code=ErrorCode.NOT_FOUND, message="x")
        assert isinstance(e, Exception)
        assert str(e) == "x"

    def test_json_serializable(self):
        e = ToolError(
            code=ErrorCode.UPSTREAM_ERROR,
            message="上游挂了",
            reason="中文原因",
            alternatives=["换源"],
        )
        json.dumps(e.to_dict(), ensure_ascii=False)

    def test_code_enum_values_stable(self):
        """错误码是对外契约，值不能随意改。"""
        expected = {
            "PLATFORM_NOT_IMPLEMENTED",
            "CAPABILITY_UNSUPPORTED",
            "AUTH_REQUIRED",
            "SIGNATURE_REQUIRED",
            "AUTH_EXPIRED",
            "INVALID_ARGUMENT",
            "NOT_FOUND",
            "RATE_LIMITED",
            "TIMEOUT",
            "UPSTREAM_ERROR",
            "INTERNAL_ERROR",
        }
        assert {c.value for c in ErrorCode} == expected


class TestConvenienceBuilders:
    def test_platform_not_implemented(self):
        e = platform_not_implemented("channels", "无公开 API", ["付费服务"])
        d = e.to_dict()["error"]
        assert d["code"] == "PLATFORM_NOT_IMPLEMENTED"
        assert d["platform"] == "channels"
        assert d["reason"] == "无公开 API"
        assert d["alternatives"] == ["付费服务"]

    def test_auth_required(self):
        e = auth_required("xiaohongshu", "内容搜索", "扫码登录")
        d = e.to_dict()["error"]
        assert d["code"] == "AUTH_REQUIRED"
        assert "登录" in d["reason"]
        assert "扫码登录" in d["alternatives"]

    def test_signature_required(self):
        e = signature_required("douyin", "搜索", "需要 a_bogus")
        d = e.to_dict()["error"]
        assert d["code"] == "SIGNATURE_REQUIRED"
        assert "a_bogus" in d["reason"]
        # 必须给出替代路径——否则调用方无从下手
        assert d["alternatives"]


class TestSuccessEnvelope:
    def test_basic(self):
        d = success({"a": 1})
        assert d["ok"] is True
        assert d["data"] == {"a": 1}
        assert "meta" not in d

    def test_with_meta(self):
        d = success([1, 2], meta={"count": 2})
        assert d["meta"] == {"count": 2}

    def test_symmetry_with_error(self):
        """成功与失败信封结构对称——调用方可以用同一个判断逻辑。"""
        ok = success({"x": 1})
        err = ToolError(code=ErrorCode.NOT_FOUND, message="x").to_dict()
        assert "ok" in ok and "ok" in err
        assert ok["ok"] is True and err["ok"] is False
        assert "data" in ok and "error" in err
