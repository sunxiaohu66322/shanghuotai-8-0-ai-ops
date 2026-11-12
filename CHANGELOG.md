# 版本变更记录（CHANGELOG）

> 本仓库由 `github-project-push` 版本化同步维护。**每次同步 = 远端历史之上的一个新 commit + 一个 tag**。
> 第三方 AI / 协作者：从最新一条往下读，就能看到这套系统每一次改了什么、为什么改、改动落在哪些文件。
> 看某一版完整差异：`git show <tag>`，或 GitHub 的 `/commits/<tag>` 页。
> 「基线 commit」= 这一版的上一版提交；本版 SHA 用 `git rev-parse <tag>` 取（提交自身不能写自己的 SHA）。
> 本文件与 `VERSIONS.md` 自身的更新不重复计入下面的改动清单。
> 注：`raw.githubusercontent.com` 有 CDN 缓存，刚推完的几分钟内可能读到上一版；
> 想确认最新版，请看仓库根本文件的**网页渲染**或 `/tags` 页（实时），或 `git show <tag>`。

## v4 · 2026-11-12 20:45:01 +08:00 · P0.3.1 真实同步：微信两店全量分页(700/143) + ops_sync_state 新鲜度(抖店3846标历史14.2天) + 首页/卡片/桌面三店7字段 + 10项测试14/14

- 标签：`v4` ｜ 基线 commit（上一版）：`196e0c6b5` ｜ 密钥终检：**0 命中** ｜ 纳入 **1225** 文件 / 排除 **1202**
- 本版有意义的改动 **10** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `A-桌面主程序/app/ops/business.py`
  - `M` `A-桌面主程序/app/ops/config.py`
  - `M` `A-桌面主程序/app/ops/engine.py`
  - `M` `A-桌面主程序/app/ops/platform.py`
  - `M` `A-桌面主程序/app/ops/schema.sql`
  - `A` `A-桌面主程序/app/ops/sync_freshness.py`
  - `M` `A-桌面主程序/app/ui/tab_ops.py`
  - `A` `A-桌面主程序/tests_ops_sync.py`
  - `M` `B-微信小店适配器/data/overview.json`
  - `A` `D-调查报告与交付证据/8.0-P0.3.1-真实同步交付报告-20261112.md`
- 本版提交 SHA：`git rev-parse v4`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v3 · 2026-11-11 15:57:06 +08:00 · 第三方入口定稿：README §0 与 llms.txt 顶部加入 CHANGELOG/VERSIONS 版本记录入口；CHANGELOG 头部加 raw CDN 缓存提示；SKILL 补入口常驻与权威读法

- 标签：`v3` ｜ 基线 commit（上一版）：`df6490adf` ｜ 密钥终检：**0 命中** ｜ 纳入 **1222** 文件 / 排除 **1201**
- 本版有意义的改动 **4** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `F-工具链/github-project-push/SKILL.md`
  - `M` `F-工具链/github-project-push/scripts/sync.ps1`
  - `M` `README.md`
  - `M` `llms.txt`
- 本版提交 SHA：`git rev-parse v3`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v2 · 2026-11-11 15:52:29 +08:00 · 版本化系统收尾：CHANGELOG/VERSIONS 累积保护、运行态登记表排除防无限刷版、_bak 备份目录硬排除、note 放宽

- 标签：`v2` ｜ 基线 commit（上一版）：`b5ab36398` ｜ 密钥终检：**0 命中** ｜ 纳入 **1222** 文件 / 排除 **1201**
- 本版有意义的改动 **2** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `D` `F-工具链/github-project-push/registry.json`
  - `M` `F-工具链/github-project-push/scripts/sync.ps1`
- 本版提交 SHA：`git rev-parse v2`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v1 · 2026-11-11 15:49:14 +08:00 · 8.0 每日自动运营：browser_ops/dy_daily_patrol 真实巡店、engine/schema/config/tab_ops 升级；新增 github-project-push 版本化同步系统（reg

- 标签：`v1` ｜ 基线 commit（上一版）：`35efb6d05` ｜ 密钥终检：**0 命中** ｜ 纳入 **1222** 文件 / 排除 **1201**
- 本版有意义的改动 **13** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `.gitignore`
  - `A` `A-桌面主程序/app/ops/browser_ops.py`
  - `M` `A-桌面主程序/app/ops/config.py`
  - `A` `A-桌面主程序/app/ops/dy_daily_patrol.mjs`
  - `M` `A-桌面主程序/app/ops/engine.py`
  - `M` `A-桌面主程序/app/ops/schema.sql`
  - `M` `A-桌面主程序/app/ui/tab_ops.py`
  - `M` `B-微信小店适配器/data/overview.json`
  - `M` `F-工具链/github-project-push/SKILL.md`
  - `A` `F-工具链/github-project-push/registry.json`
  - `M` `F-工具链/github-project-push/scripts/push.ps1`
  - `A` `F-工具链/github-project-push/scripts/register.ps1`
  - `A` `F-工具链/github-project-push/scripts/sync.ps1`
- 本版提交 SHA：`git rev-parse v1`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

