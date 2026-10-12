"""审计发现的两个真 bug 的回归测试。

━━━ 这两个 bug 的共同点 ━━━

都是**长期运行才会暴露**的资源问题，短测试看不出来：

  · 缓存无上限 —— 跑几天才 OOM
  · clear() 被立刻重填 —— 改了配置不生效，且不报错

这类问题的教训：**测试要模拟「长期运行/重复操作」，而不只是「跑一次对不对」**。
"""

from __future__ import annotations


from social_insight.hot.match import (
    PRESET_PROFILES,
    _ProfilesProxy,
    get_preset,
    reload_profiles,
)


class TestCacheBounded:
    """HttpClient 缓存必须有上限——服务是长期运行的。"""

    def test_cache_evicts_when_over_limit(self):
        """塞超过上限的条目后，缓存必须自动收敛。"""
        from social_insight.http import HttpClient

        c = HttpClient()
        limit = c.max_cache_entries
        assert limit > 0 and limit < 100_000, f"上限不合理: {limit}"

        # 塞到上限的 2 倍
        for i in range(limit * 2):
            c._cache_put(f"key{i}", {"v": i}, ttl=3600)

        size = c.cache_size()
        assert size <= limit, (
            f"缓存超限未淘汰: {size} > {limit} —— 长时间运行会内存增长"
        )

    def test_cache_evicts_expired_first(self):
        """淘汰优先清已过期的。"""
        from social_insight.http import HttpClient

        c = HttpClient()
        # 先放一批已过期的（ttl 负数 → expires_at 在过去）
        for i in range(c.max_cache_entries):
            c._cache_put(f"expired{i}", {"v": i}, ttl=-1)
        # 再放一批有效的，触发淘汰
        for i in range(10):
            c._cache_put(f"fresh{i}", {"v": i}, ttl=3600)

        # 过期项应被清掉，新项保留
        assert c.cache_size() < c.max_cache_entries
        assert c._cache_get("fresh0") == {"v": 0}, "有效条目不该被误删"

    def test_eviction_keeps_most_valuable(self):
        """按过期时间淘汰时，保留「活得更久」的。"""
        from social_insight.http import HttpClient

        small = HttpClient()
        small.max_cache_entries = 10  # 临时调小便于测试

        # 5 条快过期 + 15 条慢过期，共 20 → 超 10，应触发淘汰
        for i in range(5):
            small._cache_put(f"short{i}", {"v": i}, ttl=1)
        for i in range(15):
            small._cache_put(f"long{i}", {"v": i}, ttl=99999)

        assert small.cache_size() <= 10
        # 慢过期的应优先保留
        kept_long = sum(1 for i in range(15) if small._cache_get(f"long{i}") is not None)
        assert kept_long > 0, "淘汰策略把长期有效的都删了"

    def test_no_eviction_under_limit(self):
        """未超限时不该淘汰任何东西（缓存要真的起作用）。"""
        from social_insight.http import HttpClient

        c = HttpClient()
        for i in range(10):
            c._cache_put(f"k{i}", {"v": i}, ttl=3600)
        assert c.cache_size() == 10


class TestProfilesReload:
    """_ProfilesProxy 的 clear / reload 必须真的生效。"""

    def test_clear_actually_clears(self):
        """clear 之后不该被 _ensure 立刻重填。

        ⚠️ 这是审计发现的真 bug：朴素实现里 clear() 之后
        下一次访问立刻把 6 个账号填回来，表现为「清了还是 6 个」。
        """
        p = _ProfilesProxy()
        # 先触发加载
        assert len(p) > 0

        p.clear()
        assert len(p) == 0, (
            f"clear() 后被立刻重填（{len(p)} 个）—— reload 将无法生效"
        )

    def test_clear_then_access_stays_empty(self):
        """clear 后反复访问仍是空的（直到显式 reload）。"""
        p = _ProfilesProxy()
        _ = len(p)
        p.clear()

        for _ in range(5):
            assert len(p) == 0
            assert "tech-blogger" not in p
            assert list(p) == []

    def test_reload_restores(self):
        """reload_profiles 应能恢复（且允许自动填充）。"""
        reload_profiles()
        assert len(PRESET_PROFILES) > 0, "reload 后应为非空"
        assert "tech-blogger" in PRESET_PROFILES

    def test_reload_picks_up_new_account(self, tmp_path, monkeypatch):
        """reload 必须能读到**新增**的账号——这才是它的用途。

        模拟：改了 accounts.local.json 后调 reload，新账号应立即可见。
        """
        import json

        cfg = tmp_path / "accounts.local.json"
        cfg.write_text(
            json.dumps({
                "accounts": {
                    "brand-new-account": {
                        "display_name": "新账号",
                        "keywords": ["测试"],
                        "domains": ["software"],
                    }
                }
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("SOCIAL_INSIGHT_ACCOUNTS", str(cfg))

        reload_profiles()
        try:
            assert "brand-new-account" in PRESET_PROFILES, (
                "reload 后新账号不可见 —— 配置热更新失效"
            )
            p = get_preset("brand-new-account")
            assert p.name == "新账号"
        finally:
            monkeypatch.delenv("SOCIAL_INSIGHT_ACCOUNTS", raising=False)
            reload_profiles()

    def test_get_preset_reflects_reload(self, tmp_path, monkeypatch):
        """get_preset 走的路径也要能感知 reload。

        ⚠️ 语义说明：内置通用账号（tech-blogger 等）**始终可用** ——
        `load_accounts()` 把本地配置**合并覆盖**到内置示例之上，
        所以本地配置只增不减（这是刻意的：通用示例永远能用）。
        """
        import json

        cfg = tmp_path / "accounts.local.json"
        cfg.write_text(
            json.dumps({
                "accounts": {
                    "only-this": {"keywords": ["x"], "domains": ["software"]}
                }
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("SOCIAL_INSIGHT_ACCOUNTS", str(cfg))
        reload_profiles()
        try:
            # 新账号立即可见（热更新生效）
            assert get_preset("only-this").keywords == ["x"]
            # 内置示例仍然可用（合并语义）
            assert get_preset("tech-blogger") is not None
        finally:
            monkeypatch.delenv("SOCIAL_INSIGHT_ACCOUNTS", raising=False)
            reload_profiles()

    def test_local_config_overrides_builtin(self, tmp_path, monkeypatch):
        """同名时本地配置**覆盖**内置示例。"""
        import json

        cfg = tmp_path / "accounts.local.json"
        cfg.write_text(
            json.dumps({
                "accounts": {
                    "tech-blogger": {
                        "display_name": "被覆盖的科技号",
                        "keywords": ["只留这个"],
                        "domains": ["software"],
                    }
                }
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("SOCIAL_INSIGHT_ACCOUNTS", str(cfg))
        reload_profiles()
        try:
            p = get_preset("tech-blogger")
            assert p.name == "被覆盖的科技号", "本地配置没覆盖内置示例"
            assert p.keywords == ["只留这个"]
        finally:
            monkeypatch.delenv("SOCIAL_INSIGHT_ACCOUNTS", raising=False)
            reload_profiles()

    def test_proxy_is_dict_subclass(self):
        """必须保持 dict 语义（既有代码依赖）。"""
        assert isinstance(PRESET_PROFILES, dict)
        assert isinstance(PRESET_PROFILES, _ProfilesProxy)
        # 常见 dict 操作都要能用
        assert sorted(PRESET_PROFILES)
        assert list(PRESET_PROFILES.keys())
        assert list(PRESET_PROFILES.items())
        assert PRESET_PROFILES.get("tech-blogger") is not None
