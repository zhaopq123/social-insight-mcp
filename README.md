# social-insight-mcp

> 自部署的多平台内容分析 MCP Server
> 覆盖：**抖音** · **小红书** · 微信视频号（占位）
> 定位：只读内容分析 —— 热点选题、对标拆解、评论舆情

---

## 核心特性

### 1. 两条独立链路

| | 链路 A · 热点选题 | 链路 B · 对标拆解 |
|---|---|---|
| 回答什么 | 现在什么话题火？哪个适合我？ | 同行发了什么？为什么火？ |
| 需要登录 | ❌ **完全不需要** | ⚠️ 抖音需签名，小红书需 cookie |
| 风控暴露 | 零 | 有 |
| 状态 | ✅ **已可用** | 部分可用（见下） |

**链路 A 不碰任何平台登录态** —— 它从公开热榜聚合数据。
小红书只是发布平台，不是热点来源。

### 2. 跨源交叉验证（核心算法）

单个热榜的排名是**该站编辑口味**的产物。一个话题在多个**互相独立**的源上
同时出现，才是它真的在火的证据。

```
[3源] 郑钦文因伤退出武汉网球公开赛
      来源: weibo, zhihu, 抖音热榜
[3源] 武网官方回应郑钦文退赛
      来源: weibo, 抖音热榜, 百度热搜
```

可信度分级：

| 级别 | 含义 |
|---|---|
| `confirmed` | ≥3 个独立源同时报道 —— 强信号，可以放心做 |
| `likely` | 2 个独立源 —— 值得做 |
| `single` | 仅 1 个源 —— 可能是该站特有，需自行判断 |

**镜像源不算数。** 实测 `uapis` 的抖音榜与抖音官方榜前 10 条重合 7 条——
它是镜像。如果拿它给抖音热点做交叉验证，等于把同一份数据数两遍，
会得出虚假信心。所以每个源都标注**独立性键**，同键的源只算一票。

### 3. 热点 × 账号定位匹配（`topic_match`）

`hot_topics` 回答「现在什么火」，`topic_match` 回答「**哪个跟我的号有关**」。

匹配度由三个**正交**信号加权，且**分开报告命中证据**：

| 信号 | 权重 | 说明 |
|---|---|---|
| 领域契合 | 0.45 | 热点属于你的领域吗 |
| 关键词命中 | 0.40 | 直接命中业务词吗（最强） |
| 形态契合 | ×1.15 | **乘数**，放大已有信号 |

减分项：命中无关领域（如体育热点对软件号）会拉低。

**设计要点**：

- **形态契合是乘数不是加分**。实测踩过坑：独立给分时，
  「对对联」「养鸡场」这类完全无关的话题也会因为含「怎么/如何」得分。
- **刻意把「火」与「相关」分开**：`corroboration` 表示热点强度，
  `score` 表示匹配度。很火但不相关的话题不该因为火就排前面。
- **预设账号**：`小赵聊软件`（软件/科技）、`小赵无人零售`（零售/无人零售），
  定位来自知识库实测页。也可用 `keywords`/`domains` 临时指定。

**实测效果**（同一批 290 条热点）：

```
【小赵聊软件】  strong=5
  0.85 开源 Vue 3 组件库 Morya UI        ← 命中：工具、AI
  0.62 国庆7天AI圈3次大翻车              ← 命中：AI
  0.62 LLM 驱动的自动化测试系列           ← 命中：自动化

【小赵无人零售】strong=0（今日热点里确实无咖啡机/冰淇淋机话题）
  0.00 武网官方回应郑钦文退赛            ← 偏向「体育」，关联弱
```

**两个账号零重合** —— 匹配确实生效了。

### 4. 确定性分析（不含生成式 AI）

`analyze_viral` / `analyze_comments` 是**确定性算法**：
排序、聚合、词频（bigram）、情绪（词典法）。结果可复现、可验证。

**「为什么火」的归因由调用方的 LLM 完成** —— MCP 只提供数据与原语。
这样避免了双重付费、双重延迟与黑盒化。

---

## 安装

```bash
# 需要 Python ≥ 3.10
python -m venv .venv
.venv/bin/pip install -e .          # Linux
# .venv\Scripts\pip install -e .    # Windows
```

依赖：`mcp`、`httpx`。无其他运行时依赖。

---

## 接入 MCP 客户端

### 方式一：stdio（本地）

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

### 方式二：挂进 DSH 的 dsh-mcp-manager

编辑 `<DSH_HOME>/@wingsky-1/dsh-mcp-manager/mcp.json`，在 `servers` 数组里加：

```json
{
  "name": "social-insight",
  "transport": "stdio",
  "enabled": true,
  "command": "C:\\Users\\zpq\\.venvs\\social-insight-mcp\\Scripts\\python.exe",
  "args": ["-m", "social_insight.server"],
  "description": "多平台内容分析（热点选题 + 对标拆解）"
}
```

> ⚠️ **写 JSON 配置时注意**：PowerShell 5 的 `Set-Content -Encoding UTF8`
> 会写入 UTF-8 BOM，导致 `JSON.parse` 静默失败。用 `[IO.File]::WriteAllText()`
> 配合 `UTF8Encoding($false)`，或直接用本仓库的 `scripts/install_dsh.py`。

---

## 工具清单

| 工具 | 链路 | 说明 |
|---|---|---|
| `capabilities` | — | 能力矩阵。**建议任何操作前先调一次** |
| `hot_topics` | A | 多源热榜聚合 + 交叉验证 |
| `topic_match` | A | **热点 × 账号定位匹配**（选题闭环核心） |
| `trend` | A | 热点趋势（依赖快照层） |
| `content_search` | B | 按关键词搜索内容 |
| `content_detail` | B | 单条内容详情 |
| `content_comments` | B | 评论列表 |
| `creator_profile` | B | 创作者主页 |
| `creator_works` | B | 创作者作品列表 |
| `analyze_viral` | B | 爆款拆解（确定性统计） |
| `analyze_comments` | B | 评论挖掘（词频 + 情绪） |

---

## 平台支持现状（2026-10-12 实测）

### 抖音

| 能力 | 状态 | 说明 |
|---|---|---|
| 热榜 | ✅ 可用 | 官方接口，无需登录。48 条实测通过 |
| 搜索/详情/评论/主页 | ⚠️ 需签名 | 裸请求返回 `status_code=2483` |

**签名策略**：`a_bogus` 是持续对抗项，**不自研**。
等待接入成熟库（f2 / douyin-tiktok-scraper）。

### 小红书

| 能力 | 状态 |
|---|---|
| 热搜 | ✅ 可用（经聚合源，**无需登录**） |
| 搜索/详情/评论/主页 | ⚠️ 必须登录 |

**实测证据**：搜索 API 直接返回
`{"code":-101,"success":false,"msg":"无登录信息，或登录信息为空"}`。

**为什么没有匿名路径**：签名头 `x-s`/`x-t` 的计算依赖登录后才有的 `a1` cookie。
没有 cookie → 算不出签名 → 连门都进不去。

**接入策略：编排，不重造。** 推荐后端
[xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)
（Apache-2.0 可商用，活跃维护）。

### 微信视频号

❌ **不实现（占位）**。调用时返回结构化错误说明原因与替代路径。

依据：官方开放 API 仅限自己账号的直播/橱窗/带货数据，
无关键词检索、无他人作品、无评论列表；搜一搜无可编程接口；
开源生态零覆盖（MediaCrawler 亦不支持）；PC 微信 hook 有封号与法律风险。

---

## 热点源

| 源名 | 显示名 | 独立性键 | 权重 |
|---|---|---|---|
| `douyin` | 抖音热榜 | douyin | 1.0 |
| `baidu` | 百度热搜 | baidu | 0.95 |
| `uapis:weibo` | 微博热搜 | weibo | 0.9 |
| `uapis:zhihu` | 知乎热榜 | zhihu | 0.85 |
| `uapis:xiaohongshu` | 小红书热搜 | xiaohongshu | 0.9 |
| `uapis:toutiao` | 头条热榜 | toutiao | 0.85 |
| `uapis:36kr` | 36氪热榜 | 36kr | 0.8 |
| `bilibili` | B站排行 | bilibili | 0.8 |
| `juejin` | 掘金热榜 | juejin | 0.85 |
| `tophub` | 今日热榜 | tophub | 0.7 |

---

## 已验证的坑

### 1. httpx 0.28 环境代理 bug

`httpx` 解析形如 `http://127.0.0.1:7897` 的环境代理时会抛
`InvalidURL: Invalid port: ':1]'`，导致**所有**请求失败。

**解决**：`HttpClient(trust_env=False)` —— 国内热榜源本就不应走代理。

### 2. 中文路径下 venv 创建失败

在含中文的路径（如 `乱七八糟`）下 `python -m venv` 会因编码问题
以 exit code 103 失败。**解决**：venv 建在 ASCII 路径。

### 3. 反义词陷阱

中文新闻标题里「同实体 + 相反动作」是高频模式，措辞相似度可能极高：

```
「A股三大指数集体上涨」 vs 「A股三大指数集体下跌」   jac=0.64
「郑钦文夺冠」        vs 「郑钦文退赛」            jac=0.33
```

仅靠相似度无法区分「同一事件的不同措辞」与「相反事件」。
**解决**：极性守卫（`_ANTONYM_PAIRS`），只做减法不做加法。

### 4. 无效参数不能报成上游错误

传错源名时若返回 `UPSTREAM_ERROR`，调用方会以为是网络问题而重试。
**解决**：参数校验在抓取之前，返回 `INVALID_ARGUMENT` 并列出可用值。

---

## 部署（已在 vivo 服务器上线）

**部署位置**：`192.168.3.35` 的 Termux + proot Ubuntu 容器，`/opt/social-insight-mcp`
**服务地址**：`http://192.168.3.35:8100/mcp`（streamable-http）
**已挂载**：dsh-mcp-manager（`social-insight`，无 BOM 写入）

### 环境约束（决定部署方式）

目标机是 **Termux + proot-distro Ubuntu 双层**结构：

| 约束 | 后果 | 解法 |
|---|---|---|
| 无 systemd | `systemctl` 不存在 | 不用 unit 文件 |
| proot `--kill-on-exit` | 后台进程活不下来 | 前台运行 + 外层守护循环 |
| 两层 `/tmp` 不同 | Termux 层写 `/tmp` 失败 | 用 `$PREFIX/tmp` |

### 守护方式

在 **Termux 层**（8022）用 tmux 挂着守护循环：

```bash
tmux new-session -d -s social-insight \
  "while true; do
     proot-distro login ubuntu -- /opt/social-insight-mcp/start-mcp.sh
     sleep 10
   done"
```

**实测**：强杀服务进程后 **9 秒自动恢复**。

### 部署命令

```bash
python scripts/deploy.py                    # 全量部署（打包→上传→装依赖→验证）
python scripts/deploy.py --check            # 只探测环境
python scripts/run_remote.py <脚本>          # 在容器层执行脚本
python scripts/run_remote.py <脚本> --termux # 在 Termux 层执行脚本
```

### 验收

```bash
python tests/verify_remote_mcp.py           # MCP 协议级端到端验证
```

---

## 测试

```bash
# 单元测试（189 个，全部通过）
python -m pytest

# 集成/探测脚本（需网络）
python tests/integration_matching.py        # 匹配逻辑
python tests/integration_protocol.py        # 协议层
python tests/integration_e2e.py             # 端到端
python tests/probe_sources.py               # 热点源连通性
python tests/probe_corroborate.py           # 交叉验证实测
python tests/verify_remote_mcp.py           # 远程 MCP 协议验收
```

### 测试覆盖

| 文件 | 覆盖 | 用例数 |
|---|---|---|
| `test_models.py` | 数据模型（None vs 0 语义） | 17 |
| `test_errors.py` | 错误信封契约 | 16 |
| `test_corroborate.py` | 交叉验证算法（含极性守卫） | 60+ |
| `test_http.py` | HTTP 层（代理 bug 回归、重试、缓存） | 25+ |
| `test_platforms.py` | 适配器与能力守卫 | 45+ |
| `test_sources.py` | 热点源解析（真实 HTML 片段） | 28 |
| `test_capabilities_consistency.py` | capabilities 与实际工具一致性 | 12 |

---

## 已验证的坑

### 1. httpx 0.28 环境代理 bug

`httpx` 解析形如 `http://127.0.0.1:7897` 的环境代理时会抛
`InvalidURL: Invalid port: ':1]'`，导致**所有**请求失败。

**解决**：`HttpClient(trust_env=False)`。

### 2. 中文路径下 venv 创建失败

含中文的路径下 `python -m venv` 因编码问题 exit code 103。
**解决**：venv 建在 ASCII 路径。

### 3. 反义词陷阱

```
「A股三大指数集体上涨」 vs 「A股三大指数集体下跌」   jac=0.64
「郑钦文夺冠」        vs 「郑钦文退赛」            jac=0.33
```

仅靠相似度无法区分「同一事件的不同措辞」与「相反事件」。
**解决**：极性守卫（`_ANTONYM_PAIRS`），只做减法。

### 4. 短标题相似度虚高

```
「单源话题」 vs 「三源话题」   4 字标题差 1 字，Jaccard 恰好 0.50
```

短标题里单字权重太大。**解决**：短标题（≤6 字）用更严的阈值 0.7。

### 5. 镜像源不能算交叉验证

实测 uapis 的抖音榜与抖音官方榜前 10 条重合 7 条——是镜像。
**解决**：独立性键去重，同键只算一票。

### 6. tophub 标题不在 `<a>` 里

首页条目标题在 `<span class="t">`，按 `<a>` 解析返回 0 条。
且首页混着**淘宝商品**（带 `¥`/`热销`），必须过滤。

### 7. 模块级改 sys.stdout 会摧毁 pytest

`sys.stdout = io.TextIOWrapper(...)` 在模块级执行会替换掉 pytest 的捕获流，
导致 `ValueError: I/O operation on closed file` + **0 个测试被收集**。
**解决**：这类脚本改名为 `integration_*.py`，不被 pytest 收集。

### 8. tar 解包不带执行位

`start-mcp.sh` 权限丢失 → 守护循环陷入「失败→重试」死循环。
**解决**：部署脚本里显式 `chmod +x`。

### 9. start-mcp.sh 缺 PYTHONPATH

源码在 `src/` 下，不设 `PYTHONPATH` 会 `ModuleNotFoundError`。

### 10. capabilities 宣传未注册的工具

早期版本列了 `topic_match` / `compare_creators` / `trend` 但未注册，
调用方会照着去调然后收到「未知工具」。
**解决**：`capabilities` 从 `TOOL_DEFS` 动态读取，并单列 `planned_tools`。

### 13. `x or DEFAULT` 会静默吃掉空列表

Python 里 `[] or DEFAULT` 得到 `DEFAULT` —— 空列表是 falsy。所以写成
`sources or DEFAULT_SOURCES` 时，**调用方显式传空列表会被静默替换成默认源**。

后果比报错更危险：结果看起来完全合理（有数据、结构正确），但来源不对，
**无从察觉**。而且它会让下面的「空列表检查」变成死代码（实测踩过）。

**解决**：显式区分 `None`（未传）与 `[]`（显式传空）。
并加了结构性测试，源码里再出现该写法就会红。

### 14. 同秒写入导致排序不确定

只用 `ORDER BY captured_at` 排序时，同一秒内的多次写入顺序**不确定**
（SQLite 不保证），趋势线会错乱。**解决**：加自增 `id` 作次级排序键。

### 15. 时间格式不一致导致静默查不到数据

写入用 ISO 格式（`2026-10-12T15:04:15+00:00`），
查询用 SQLite 的 `datetime('now')`（`2026-10-12 15:04:15`）——
差在 `T` 与空格、以及时区后缀，**字符串比较必然出错**。
表现为「刚写入的数据查不到」，但不报错。

**解决**：不用 SQLite 的 `datetime()`，改在 Python 侧算好边界时间戳。

### 16. 字典过期导致漏判

「郑钦文退出武网」里既无「网球」也无「比赛」，只有「武网」——
词典法依赖字面词，必须补专有名词（武网/中网/温网/NBA 等）。

### 11. Windows subprocess 默认 GBK 解码

`text=True` 用系统 locale 解码，遇到 UTF-8 中文输出直接崩。
**解决**：显式 `encoding="utf-8", errors="replace"`。

### 12. Windows 命令行长度限制

base64 放命令行参数触发 `WinError 206`。
**解决**：走 stdin 管道。

---

## 设计原则

1. **不内置 AI** —— 调用方就是 LLM。服务器只做确定性数据面。
2. **绝不静默失败** —— 能力缺失必须显式说明原因与替代路径。
3. **工具克制** —— 一个动词一个工具 + `platform` 参数。
4. **可插拔** —— 平台差异收敛在适配层，换采集后端不影响调用方。
5. **不宣传未实现的能力** —— `capabilities` 与实际注册严格一致。

---

## 许可证

本项目的适配层与工具层代码：MIT。

注意：接入的第三方采集后端有各自的许可证要求——
`MediaCrawler` 为非商用学习许可，`Spider_XHS` 无 LICENSE 文件，
`xiaohongshu-mcp` 为 Apache-2.0。生产使用前请自行核实。
