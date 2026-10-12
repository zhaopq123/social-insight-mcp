# 运维脚本

这些都是**实战用过**的脚本，不是示例。每个都标注了「在哪一层执行」。

## 执行层级

目标机是 Termux + proot Ubuntu **双层**，脚本必须在对的层跑：

| 层 | SSH 端口 | 怎么跑 |
|---|---|---|
| **Termux 层** | 8022 | `python scripts/run_remote.py scripts/ops/<脚本> --termux` |
| **容器层** | 2222 | `python scripts/run_remote.py scripts/ops/<脚本>` |

**判断依据**：脚本要用 `proot-distro login` 就是在 Termux 层；
直接用 `./venv/bin/python` 就是在容器层。

## 脚本清单

| 脚本 | 层 | 用途 |
|---|---|---|
| `daemon.sh` | Termux | **建服务守护循环**（首次部署、守护失效时） |
| `restart.sh` | Termux | **重启服务**（部署新代码后必做） |
| `snapshot-cron.sh` | Termux | 建快照定时采集（每 2 小时） |
| `test-recovery.sh` | Termux | 强杀恢复测试（验证守护真的有效） |
| `health.sh` | 容器 | 健康巡检（进程/端口/工具/快照库） |
| `diag-sources.sh` | 容器 | 热点源连通性诊断（哪个源挂了） |

## 典型流程

### 部署新代码后（最常见）

```bash
cd <项目目录>
python scripts/deploy.py                        # 打包 → 上传 → 装依赖 → 冒烟测试
python scripts/run_remote.py scripts/ops/restart.sh --termux   # 重启
python scripts/run_remote.py scripts/ops/health.sh             # 确认
```

⚠️ **忘了重启 = 跑的还是旧代码**。这是最容易犯的错。

### 首次部署 / 守护失效

```bash
python scripts/ops/daemon.sh --termux     # 建守护
python scripts/run_remote.py scripts/ops/daemon.sh --termux
python scripts/run_remote.py scripts/ops/test-recovery.sh --termux   # 验证
```

### 排查问题

```bash
python scripts/run_remote.py scripts/ops/health.sh             # 先看整体
python scripts/run_remote.py scripts/ops/diag-sources.sh       # 源的问题
ssh -p 8022 <user>@<your-server> "tmux capture-pane -t social-insight -p | tail -30"  # 看日志
```

## 设计约束（改脚本前必读）

1. **proot 没有 systemd** —— 别写 unit 文件
2. **proot 带 `--kill-on-exit`** —— 后台进程活不下来，必须前台运行 + 外层循环
3. **两层 `/tmp` 不同** —— Termux 层写 `/tmp` 会失败，用 `$PREFIX/tmp`
4. **tar 解包不带执行位** —— `start-mcp.sh` 需要 `chmod +x`（daemon.sh 里已含）
5. **`PYTHONPATH` 必须设** —— 源码在 `src/` 下，不设会 `ModuleNotFoundError`
