# 验收清单

> 每次改完代码部署后，按这个清单逐项确认。
> **不是形式主义**——每一项都对应一个真实踩过的坑。

---

## 一、本机（提交前）

```bash
# 1. 单元测试
python -m pytest                    # 期望: 275 passed

# 2. 静态检查
python -m pyflakes src/             # 期望: 无输出，退出码 0

# 3. 确认没有 falsy 回退写法
grep -rn "sources or DEFAULT" src/  # 期望: 只出现在注释里
```

- [ ] 275 个测试通过
- [ ] pyflakes 零警告
- [ ] 无 `x or DEFAULT` 形式（会被 test_param_semantics 拦住）

---

## 二、部署

```bash
python scripts/deploy.py            # 打包 → 上传 → 装依赖 → 冒烟测试
```

- [ ] 冒烟测试输出「链路: ['A_hot_topics', 'B_platform_content']」
- [ ] 冒烟测试输出「平台: ['douyin', 'xiaohongshu', 'channels']」
- [ ] 真实拉取热点成功（话题数 > 0）

⚠️ **部署完必须重启**，否则跑的还是旧代码：

```bash
python scripts/run_remote.py scripts/ops/restart.sh --termux
```

- [ ] 重启后端口 8100 已监听
- [ ] HTTP 探测返回 400（正常，MCP 要求 POST）

---

## 三、服务端验收

```bash
# 健康巡检
python scripts/run_remote.py scripts/ops/health.sh
```

- [ ] 进程存在
- [ ] 端口 8100 监听
- [ ] 工具数正确（当前 11 个）
- [ ] 快照库可读

```bash
# 全工具验收（最有价值的一项）
python tests/verify_all_tools.py
```

- [ ] 服务端工具数 = 预期
- [ ] 链路 A 三个工具（`hot_topics` / `topic_match` / `trend`）返回 ok
- [ ] 链路 B 七个工具返回**结构化错误**（带 reason + alternatives）
- [ ] 参数校验返回 `INVALID_ARGUMENT`（不是 `UPSTREAM_ERROR`）
- [ ] 中文平台别名可解析

---

## 四、可靠性验证

```bash
# 强杀恢复
python scripts/run_remote.py scripts/ops/test-recovery.sh --termux
```

- [ ] 强杀后 10 秒内自动恢复
- [ ] 守护会话仍存活

---

## 五、数据源健康

```bash
python scripts/run_remote.py scripts/ops/diag-sources.sh
```

- [ ] 至少 8/10 个源正常
- [ ] 若有源长期返回空 → 该源可能改版，需要修解析器

---

## 六、收尾

- [ ] 代码已 `git commit` + `git push`（备份在 Termux `~/git/`）
- [ ] 知识库已 `tiddlywiki_git_sync`
- [ ] 若新增工具 → **提醒用户重启 DSH host**（客户端工具表是会话建立时固化的）

---

## 附：各层职责速查

| 检查项 | 在哪跑 | 命令 |
|---|---|---|
| 单元测试 | 本机 | `python -m pytest` |
| 静态检查 | 本机 | `python -m pyflakes src/` |
| 部署 | 本机 | `python scripts/deploy.py` |
| 重启服务 | Termux 层 | `run_remote.py scripts/ops/restart.sh --termux` |
| 健康巡检 | 容器层 | `run_remote.py scripts/ops/health.sh` |
| 全工具验收 | 本机（连服务） | `python tests/verify_all_tools.py` |
| 恢复测试 | Termux 层 | `run_remote.py scripts/ops/test-recovery.sh --termux` |
| 源诊断 | 容器层 | `run_remote.py scripts/ops/diag-sources.sh` |

## 附：出问题先看哪

| 症状 | 先跑 |
|---|---|
| 工具调用报 unknown tool | 不是故障 → 重启 DSH host |
| 服务无响应 | `restart.sh --termux` |
| 热点拿不到 | `diag-sources.sh` |
| trend 空 | 正常（还没积累）或 `snapshot` 手动采一次 |
| 排查不了 | `health.sh` + 看 tmux 日志 |
