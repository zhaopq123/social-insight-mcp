# 贡献指南

## 快速开始

```bash
git clone <repo>
cd social-insight-mcp
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Linux/macOS
# .venv\Scripts\pip install -e ".[dev]"  # Windows

python -m pytest                       # 应全部通过
python -m pyflakes src/                # 应无输出
```

## 项目结构

```
src/social_insight/
├── accounts.py          账号定位配置（支持外置文件）
├── errors.py            统一错误信封
├── models.py            数据模型
├── http.py              HTTP 客户端（重试/节流/缓存）
├── store.py             SQLite 快照存储
├── snapshot.py          定时快照采集（可独立运行）
├── service.py           业务逻辑（工具实现）
├── server.py            MCP stdio 传输
├── http_server.py       MCP streamable-http 传输
├── hot/
│   ├── sources.py       热点源适配器
│   ├── corroborate.py   跨源交叉验证 + **归一化（唯一实现）**
│   └── match.py         热点 × 账号定位匹配
└── platforms/
    ├── base.py          平台适配器接口
    ├── registry.py      平台注册表
    ├── douyin.py        抖音
    ├── xiaohongshu.py   小红书
    └── channels.py      微信视频号（占位）
```

## 几条硬性约定

这些不是风格偏好，都是**踩过坑**才定下的：

### 1. 绝不静默失败

能力缺失必须返回带 `reason` + `alternatives` 的结构化错误。
**静默返回空数据比报错危险得多**——调用方会把「没有数据」误读为
「平台没内容」。

### 2. 参数缺省禁用 `x or DEFAULT`

```python
# ❌ 空列表会被静默替换成默认值
chosen = sources or DEFAULT_SOURCES

# ✅ 显式区分 None 与 []
chosen = list(DEFAULT_SOURCES) if sources is None else list(sources)
```

`[] or DEFAULT` 得到 `DEFAULT`（空列表是 falsy）。调用方显式传空列表时
会被静默忽略，**结果看起来完全合理但来源不对**。

`tests/test_param_semantics.py` 会扫全 `src/` 拦截这个写法。

### 3. 归一化只有一份实现

`hot/corroborate.py:normalize()` 是**唯一**的归一化函数，
`match.py` 与 `store.py` 都转发到它。

三处各写一份会导致规则漂移 → 跨模块去重失效（实测踩过）。

### 4. 时间比较不用 SQLite 的 `datetime()`

写入用 ISO 格式（`2026-10-12T15:04:15+00:00`），
SQLite 的 `datetime('now')` 返回空格分隔、无时区的格式，
字符串比较必然出错且**不报错**。

用 `store._cutoff()` 在 Python 侧算边界。

### 5. 新增字段必须有测试验证「被填充」

```python
# ❌ 只测字段存在 —— 声明了但没赋值也能过
assert "sources" in m.to_dict()

# ✅ 测它真的被填上了
assert m.sources == ["微博", "百度"]
```

实测踩过：加了 `sources` 字段但构造时忘了传，
调用方看到空列表后**误判了结果可靠性**。

### 6. 不要提交真实账号信息

账号定义放 `accounts.local.json`（已被 .gitignore 忽略），
仓库里只保留 `accounts.py` 的通用示例。

## 加新热点源

1. 在 `hot/sources.py` 写一个 `fetch_xxx(client, *, limit) -> list[HotTopic]`
2. 在 `FETCHERS` 注册
3. 在 `SOURCE_META` 声明元信息——**特别是 `independence` 键**：
   同键的源被视为镜像，交叉验证时只算一票
4. **实测验证**：单独抓一次，确认能拿到数据
5. 若解析逻辑特殊，在 `tests/test_sources.py` 加真实 HTML/JSON 片段的测试

⚠️ **`independence` 别填错**。实测教训：`uapis` 的抖音榜与抖音官方榜
前 10 条重合 7 条——它是镜像。若当作独立源，会把同一份数据数两遍，
得出**虚假的「confirmed」**。

## 加新平台适配器

1. 继承 `PlatformAdapter`，声明 `platform` / `display_name` / `specs`
2. 在 `registry.py` 注册（含中文别名）
3. 每个能力如实声明 `supported` 与 `auth` 级别
4. 不支持的能力要有 `note` 说明原因
5. 在 `tests/test_platforms.py` 加测试

**平台做不了就把原因写清楚**，这比硬凑一个半成品更有价值——
`channels.py`（视频号）就是纯占位，它返回的错误信息包含完整的调研结论。

## 测试要求

```bash
python -m pytest                  # 全部通过
python -m pyflakes src/ tests/    # 无输出
```

新功能请覆盖：

- 正常路径
- **边界**（空输入、超长、emoji、控制字符）
- **错误路径**（错误码、reason、alternatives 都要对）
- 若改了共享行为，加**结构性测试**防止复犯

## 提交

- 一个提交做一件事
- 提交信息说明「为什么」而不只是「改了什么」
- 破坏性改动请在信息里点明
