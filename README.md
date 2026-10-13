# social-insight-mcp

> 多平台**内容选题分析** MCP Server —— 热点聚合 · 跨源交叉验证 · 账号定位匹配

给定一个账号定位，告诉你**今天有哪些热点值得做**。

---

## 为什么需要它

热榜工具到处都是，但它们都停在同一层：**告诉你什么在火**。

创作者真正的问题是下一层：**哪个跟我有关**。

本项目补的就是这中间那一步 —— 把「全网热点」映射到「你的账号」，
并且**给出可验证的匹配理由**（命中了哪些词、属于哪个领域），
而不是一个无法追问的黑盒分数。

---

## 核心特性

### 1. 跨源交叉验证

单个热榜的排名是**该站编辑口味**的产物。
一个话题在多个**互相独立**的源上同时出现，才是它真的在火的证据。

| 级别 | 含义 |
|---|---|
| `confirmed` | ≥3 个独立源 —— 强信号 |
| `likely` | 2 个独立源 —— 值得做 |
| `single` | 仅 1 个源 —— 可能是该站特有 |

**镜像源不算数。** 实测某聚合站的抖音榜与抖音官方榜前 10 条重合 7 条 ——
它是镜像。若当作独立源，等于把同一份数据数两遍，
会得出**虚假的 confirmed**。

所以每个源都标注 `independence` 键，**同键只算一票**。

### 2. 账号定位匹配

匹配度由三个**正交**信号加权，且**分开报告命中证据**：

| 信号 | 权重 | 说明 |
|---|---|---|
| 领域契合 | 0.45 | 热点属于你的领域吗 |
| 关键词命中 | 0.40 | 直接命中你的业务词吗（最强） |
| 形态契合 | ×1.15 | 适合你的内容形态吗（**乘数**，不是加分） |
| — | 减分 | 命中无关领域（如体育热点对科技号）会拉低 |

**为什么分开报告**：调用方能看到「命中了什么」，
从而判断合不合理、甚至推翻结论。黑盒分数无法验证。

**刻意把「火」与「相关」分开**：`corroboration` 表示热点强度，
`score` 表示与你多相关。**很火但不相关的话题不该因为火就排前面。**

### 3. 趋势追踪

每次查询自动落快照到 SQLite，可查：

- 某话题的历史（热度/排名变化）
- 近期**持续在榜**的话题（出现次数越多越稳）

配套的 `snapshot` 模块可独立运行，配合定时任务让快照规律累积。

### 4. 确定性，无内置 AI

所有分析都是**确定性算法**：排序、聚合、bigram 词频、情绪词典。
结果可复现、可验证。

**不内置生成式 AI** —— 调用方就是 LLM，服务器再调一次是重复付费
加黑盒化。MCP 的定位是数据面，「为什么火」的归因留给调用方。

---

## 安装

```bash
git clone <repo>
cd social-insight-mcp
python -m venv .venv
.venv/bin/pip install -e .          # Linux/macOS
# .venv\Scripts\pip install -e .    # Windows
```

依赖只有 `mcp` 与 `httpx`。
HTTP 传输另需 `pip install fastmcp uvicorn`。

---

## 配置账号

复制示例并改成你自己的账号：

```bash
cp accounts.example.json accounts.local.json
# 编辑 accounts.local.json
```

```json
{
  "accounts": {
    "my-account": {
      "display_name": "我的科技号",
      "keywords": ["软件", "开发", "AI", "获客", "客户"],
      "domains": ["software", "business"],
      "platforms": ["douyin"],
      "form": "image_text"
    }
  }
}
```

`accounts.local.json` 已被 `.gitignore` 忽略，不会进仓库。
也可用环境变量 `SOCIAL_INSIGHT_ACCOUNTS` 指定配置文件路径。

**可用领域**：`software` / `retail` / `business` / `sports` /
`entertainment` / `finance` / `politics` / `tech_hardware`

> ⚠️ **写 keywords 的关键经验**：商业类账号必须**同时**写产品词与经营词。
>
> 只写「咖啡机、冰淇淋机」会漏掉「某品牌门店客流爆满」这类可做话题 ——
> 实测中这会让可用候选从 7 个掉到 0 个，而且**看起来像「今天没热点」**，
> 让人误以为数据源出问题了。

---

## 接入 MCP 客户端

### stdio（本地）

```json
{
  "mcpServers": {
    "social-insight": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "social_insight.server"]
    }
  }
}
```

### streamable-http（远程）

```bash
HOST=0.0.0.0 PORT=8100 .venv/bin/python -m social_insight.http_server
```

```json
{
  "mcpServers": {
    "social-insight": {
      "type": "streamable-http",
      "url": "http://<host>:8100/mcp"
    }
  }
}
```

---

## 工具

| 工具 | 说明 |
|---|---|
| `capabilities` | 能力矩阵。**建议任何操作前先调一次** |
| `hot_topics` | 多源热榜聚合 + 交叉验证。默认「少而准」：`topics` 只放 confirmed+likely（≤20 条），single 话题折叠进 `also_on_single`（默认 5 条样本，`single_limit=0` 看全部） |
| `topic_match` | **热点 × 账号定位匹配**（核心） |
| `topic_material` | **创作素材**：热点标题 + 精准简要 + 时效 + 来源。按 `query` 取材，给 AI 读着写脚本用 |
| `trend` | 热点趋势（依赖快照层） |
| `content_search` 等 7 个 | 平台内容查询（需平台鉴权，见下） |

### `hot_topics` 输出分层（2026-10-13 产品改造）

实测 204 条返回里 186 条是 single（91%），confirmed 只有 3 条——
用户要「今天值得做的选题」，却被迫在 200 条各站榜单里自己挑。
**筛选该由服务端做**，所以默认输出改成两层：

| 字段 | 内容 |
|---|---|
| `topics` | 只放被 ≥2 个独立源报道的话题（confirmed+likely），按「独立源数 → 最佳排名」排序，默认 ≤20 条（`top_n` 可调） |
| `also_on_single` | single 话题折叠：只给**数量** + 前 5 条样本 + 说明。想看全部显式 `single_limit=0` |

排序刻意**不用热度值**——跨源热度不可比（抖音 1200万 vs 今日热榜 4），
用它排序会让大体量源霸榜。跨源可比的只有「几个独立源在报」和「最好排名」。

### `topic_match` 输出示例

```json
{
  "word": "某AI工具链发布新版本",
  "score": 0.85,
  "level": "strong",
  "reasons": ["直接命中业务词：工具、AI", "属于你的领域「软件/科技」"],
  "hit_keywords": ["工具", "AI"],
  "corroboration": "single",
  "source_count": 1,
  "sources": ["某技术社区热榜"]
}
```

`reliability` 字段对结果可靠性做**诚实评估**，因为「没有候选」有几种
完全不同的成因，混在一起会误导人：

| 值 | 含义 |
|---|---|
| `weak-day` | 今天全网热点确实与你的号不搭 —— **别硬凑** |
| `single-source-domain` | 候选集中在单一垂直源。**这是正常的**：技术类在技术社区、美妆在小红书，受众不同，跨平台重合度天然低 |
| `mixed` | 来源分散 |
| `empty` | 完全没有。**先检查是不是定位写窄了** |

---

## 平台支持现状

| 平台 | 热榜 | 内容查询 | 说明 |
|---|---|---|---|
| 抖音 | ✅ | ⚠️ 需签名 | 搜索接口要求 `a_bogus` 签名，本项目不自研（持续对抗项） |
| 小红书 | ✅ | ⚠️ 需登录 | 无匿名路径；签名依赖登录态 cookie |
| 微信视频号 | ❌ | ❌ | 官方无内容检索 API，仅占位 |

**热榜全部无需登录** —— 这是本项目的设计前提。
需要鉴权的能力会返回**带解释与替代路径**的结构化错误，绝不静默返回空。

### 内置热点源

抖音 · 百度 · B站 · 掘金 · 今日热榜 ·
微博 / 知乎 / 小红书 / 头条 / 36氪（经聚合）—— 共 10 个，互相独立。

---

## 设计原则

1. **绝不静默失败** —— 能力缺失必须返回 `reason` + `alternatives`
2. **参数语义严格** —— `None`（未传）与 `[]`（显式传空）必须区分
3. **归一化只有一份实现** —— 三处各写一份会漂移，导致去重失效
4. **不宣传未实现的能力** —— `capabilities` 从工具表动态读取
5. **工具克制** —— 一个动词一个工具 + 参数化平台差异

---

## 开发

```bash
python -m pytest                  # 521 个测试
python -m pyflakes src/ tests/    # 应无输出
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 许可证

MIT。**只做只读的公开数据聚合** —— 不登录、不发布、不互动、不绕过访问控制。

使用者应自行遵守目标平台的服务条款与当地法律法规。
