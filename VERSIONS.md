# 版本索引（VERSIONS）

| 版本 | 时间 | 本轮说明 | 基线 commit | 有意义改动数 |
|:--|:--|:--|:--|--:|
| v2 | 2026-11-11 15:52:29 +08:00 | 版本化系统收尾：CHANGELOG/VERSIONS 累积保护、运行态登记表排除防无限刷版、_bak 备份目录硬排除、note 放宽 | b5ab36398 | 2 |
| v1 | 2026-11-11 15:49:14 +08:00 | 8.0 每日自动运营：browser_ops/dy_daily_patrol 真实巡店、engine/schema/config/tab_ops 升级；新增 github-project-push 版本化同步系统（reg | 35efb6d05 | 13 |

> 完整改动清单看 `CHANGELOG.md`；某一版完整差异 `git show <tag>`。
> 版本规则由 `github-project-push` skill 的 `registry.json` 定义：vN 递增、每次一个新 commit + 一个 tag、绝不清空历史。
