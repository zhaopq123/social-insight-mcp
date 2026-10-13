#!/data/data/com.termux/files/usr/bin/sh
# social-insight-mcp 重启的正确姿势（Termux 宿主侧执行）。
#
# ━━━ 为什么不用 sv restart ━━━
#
# runsv supervise 的链头是 proot（proot-distro login），
# sv restart 的 TERM 只发给 proot。实测（2026-10-13 反复验证）：
# proot 收到 TERM **既不退出也不转发给容器内 python**，
# sv 报 "got TERM" 后石沉大海，服务照常跑，runsv 也不会重拉。
#
# 唯一有效的重启 = 让 TERM 直达容器内 python：
#   python 优雅关闭（uvicorn 正常 shutdown 流程）
#   → proot 跟随退出 → runsv 检测到 child 退出 → 自动拉起新实例。
#
# ━━━ 为什么用 [.] 正则 ━━━
#
# pkill -f 会匹配**自己所在 bash -c 的命令行**（参数里含同样字符串），
# 直接把自己和调用链杀了（实测踩过两次，ssh exit 255 无输出）。
# 用 [.] 技巧：正则 social_insight[.]http_server 匹配目标，
# 但字面串 "social_insight[.]http_server" 不被同一正则匹配。
#
# 用法：sh scripts/ops/restart-social.sh
# 等价操作：proot-distro login ubuntu -- bash -c 'pkill -f "social_insight[.]http_server"'

exec proot-distro login ubuntu -- bash -c 'pkill -f "social_insight[.]http_server"'
