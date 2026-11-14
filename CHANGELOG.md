# 版本变更记录（CHANGELOG）

> 本仓库由 `github-project-push` 版本化同步维护。**每次同步 = 远端历史之上的一个新 commit + 一个 tag**。
> 第三方 AI / 协作者：从最新一条往下读，就能看到这套系统每一次改了什么、为什么改、改动落在哪些文件。
> 看某一版完整差异：`git show <tag>`，或 GitHub 的 `/commits/<tag>` 页。
> 「基线 commit」= 这一版的上一版提交；本版 SHA 用 `git rev-parse <tag>` 取（提交自身不能写自己的 SHA）。
> 本文件与 `VERSIONS.md` 自身的更新不重复计入下面的改动清单。
> 注：`raw.githubusercontent.com` 有 CDN 缓存，刚推完的几分钟内可能读到上一版；
> 想确认最新版，请看仓库根本文件的**网页渲染**或 `/tags` 页（实时），或 `git show <tag>`。

## v8 · 2026-11-14 18:16:35 +08:00 · 本地服务控制中心一键上传 · 2026-11-14 18:15:33

- 标签：`v8` ｜ 基线 commit（上一版）：`a60b870b5` ｜ 密钥终检：**0 命中** ｜ 纳入 **1291** 文件 / 排除 **1305**
- 本版有意义的改动 **1294** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `R100` `0/.gitignore`
  - `R100` `0/A-桌面主程序/app/__init__.py`
  - `R100` `0/A-桌面主程序/app/assets/上货台.ico`
  - `R100` `0/A-桌面主程序/app/boot.py`
  - `R100` `0/A-桌面主程序/app/bridge.py`
  - `R100` `0/A-桌面主程序/app/crashguard.py`
  - `R100` `0/A-桌面主程序/app/dirbook.py`
  - `R100` `0/A-桌面主程序/app/excel_route.py`
  - `R100` `0/A-桌面主程序/app/imgcap.py`
  - `R100` `0/A-桌面主程序/app/lama_ipc.py`
  - `R100` `0/A-桌面主程序/app/lama_worker.py`
  - `R100` `0/A-桌面主程序/app/ops/__init__.py`
  - `R100` `0/A-桌面主程序/app/ops/browser_ops.py`
  - `R100` `0/A-桌面主程序/app/ops/business.py`
  - `R100` `0/A-桌面主程序/app/ops/config.py`
  - `R100` `0/A-桌面主程序/app/ops/dy_daily_patrol.mjs`
  - `R100` `0/A-桌面主程序/app/ops/dy_timed_capture.mjs`
  - `R100` `0/A-桌面主程序/app/ops/engine.py`
  - `R100` `0/A-桌面主程序/app/ops/migrate.py`
  - `R100` `0/A-桌面主程序/app/ops/platform.py`
  - `R100` `0/A-桌面主程序/app/ops/queue.py`
  - `R100` `0/A-桌面主程序/app/ops/schema.sql`
  - `R100` `0/A-桌面主程序/app/ops/store.py`
  - `R100` `0/A-桌面主程序/app/ops/sync_freshness.py`
  - `R100` `0/A-桌面主程序/app/ops/worker.py`
  - `R100` `0/A-桌面主程序/app/packlabel.py`
  - `R100` `0/A-桌面主程序/app/paths.py`
  - `R100` `0/A-桌面主程序/app/theme.py`
  - `R100` `0/A-桌面主程序/app/ui/__init__.py`
  - `R100` `0/A-桌面主程序/app/ui/buildstamp.py`
  - `R100` `0/A-桌面主程序/app/ui/cat_map_dialog.py`
  - `R100` `0/A-桌面主程序/app/ui/criteria_dialog.py`
  - `R100` `0/A-桌面主程序/app/ui/dlcenter_win.py`
  - `R100` `0/A-桌面主程序/app/ui/editor.py`
  - `R100` `0/A-桌面主程序/app/ui/fine_detail.py`
  - `R100` `0/A-桌面主程序/app/ui/logwin.py`
  - `R100` `0/A-桌面主程序/app/ui/main_window.py`
  - `R100` `0/A-桌面主程序/app/ui/settings_panel.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_detect.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_dlmon.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_export.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_fine.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_flow.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_lab.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_ops.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_publish.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_wall.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_wall_main.py`
  - `R100` `0/A-桌面主程序/app/ui/tab_wx.py`
  - `R100` `0/A-桌面主程序/app/ui/tabs.py`
  - `R100` `0/A-桌面主程序/app/ui/wall.py`
  - `R100` `0/A-桌面主程序/app/ui/wall_main.py`
  - `R100` `0/A-桌面主程序/app/ui/widgets.py`
  - `R100` `0/A-桌面主程序/app/ui/word_manager.py`
  - `R100` `0/A-桌面主程序/app/ui/workbench.py`
  - `R100` `0/A-桌面主程序/app/v3img.py`
  - `R100` `0/A-桌面主程序/app/wxserve.py`
  - `R100` `0/A-桌面主程序/docs/4.1主图人工审核-阶段A审计.md`
  - `R100` `0/A-桌面主程序/docs/4.1主图人工审核-阶段C1第二批.md`
  - `R100` `0/A-桌面主程序/docs/4.1主图人工审核-阶段C交付.md`
  - …另有 1234 项，见 `git show v8 --stat`
- 本版提交 SHA：`git rev-parse v8`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v7 · 2026-11-14 18:02:07 +08:00 · P0.3.2 老版拼多多详情卡接入：新增 store.initDataObj 方言适配器，2.0 按后端识别放行，SKU/主图/详情图/SKU图绑定；8拒收样本+195跨批次抽样全过

- 标签：`v7` ｜ 基线 commit（上一版）：`7ae5ed63a` ｜ 密钥终检：**0 命中** ｜ 纳入 **1291** 文件 / 排除 **1305**
- 本版有意义的改动 **55** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `A-桌面主程序/app/bridge.py`
  - `M` `A-桌面主程序/app/ui/tabs.py`
  - `M` `B-微信小店适配器/data/logs/wx60-std.log`
  - `M` `B-微信小店适配器/data/overview.json`
  - `A` `G-后端适配器/v3/__init__.py`
  - `A` `G-后端适配器/v3/adapters/__init__.py`
  - `A` `G-后端适配器/v3/adapters/pdd.py`
  - `A` `G-后端适配器/v3/adapters/pdd_legacy.py`
  - `A` `G-后端适配器/v3/adapters/wechat.py`
  - `A` `G-后端适配器/v3/adapters/weidian.py`
  - `A` `G-后端适配器/v3/ai_fine.py`
  - `A` `G-后端适配器/v3/api.py`
  - `A` `G-后端适配器/v3/batches.py`
  - `A` `G-后端适配器/v3/bundle.py`
  - `A` `G-后端适配器/v3/cards.py`
  - `A` `G-后端适配器/v3/catmap.py`
  - `A` `G-后端适配器/v3/criteria.py`
  - `A` `G-后端适配器/v3/detail_ingest.py`
  - `A` `G-后端适配器/v3/detect_slim.py`
  - `A` `G-后端适配器/v3/dlcenter.py`
  - `A` `G-后端适配器/v3/doudian.py`
  - `A` `G-后端适配器/v3/edits.py`
  - `A` `G-后端适配器/v3/enrich.py`
  - `A` `G-后端适配器/v3/fieldaudit.py`
  - `A` `G-后端适配器/v3/finelog.py`
  - `A` `G-后端适配器/v3/flow.py`
  - `A` `G-后端适配器/v3/grade.py`
  - `A` `G-后端适配器/v3/images.py`
  - `A` `G-后端适配器/v3/importer.py`
  - `A` `G-后端适配器/v3/inpaint_lama.py`
  - `A` `G-后端适配器/v3/keywords.py`
  - `A` `G-后端适配器/v3/mask.py`
  - `A` `G-后端适配器/v3/maskgen.py`
  - `A` `G-后端适配器/v3/media.py`
  - `A` `G-后端适配器/v3/pack.py`
  - `A` `G-后端适配器/v3/pack_merge.py`
  - `A` `G-后端适配器/v3/packaging.py`
  - `A` `G-后端适配器/v3/packsrc.py`
  - `A` `G-后端适配器/v3/positioning.py`
  - `A` `G-后端适配器/v3/profile.py`
  - `A` `G-后端适配器/v3/publish_guard.py`
  - `A` `G-后端适配器/v3/purge.py`
  - `A` `G-后端适配器/v3/related.py`
  - `A` `G-后端适配器/v3/repair.py`
  - `A` `G-后端适配器/v3/restore_pack.py`
  - `A` `G-后端适配器/v3/safedelete.py`
  - `A` `G-后端适配器/v3/sku.py`
  - `A` `G-后端适配器/v3/status.py`
  - `A` `G-后端适配器/v3/store.py`
  - `A` `G-后端适配器/v3/taskcenter.py`
  - `A` `G-后端适配器/v3/titles.py`
  - `A` `G-后端适配器/v3/trash.py`
  - `A` `G-后端适配器/v3/upload.py`
  - `A` `G-后端适配器/v3/words.py`
  - `A` `G-后端适配器/v3/wxshop.py`
- 本版提交 SHA：`git rev-parse v7`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v6 · 2026-11-13 17:43:46 +08:00 · 8.0 数据包统一命名：新增 app/packlabel.py（日期+第N批+筛选前/后，历史包诚实短名，只写 display_name 不动 pack_code）；导入与精筛收口自动命名；v3_packs 统一 default 最新口径，3.0/4.0/5.5/发布/2.0/商品库/本地链路下拉全部显示友好名并默认选

- 标签：`v6` ｜ 基线 commit（上一版）：`e1362ca8e` ｜ 密钥终检：**0 命中** ｜ 纳入 **1240** 文件 / 排除 **1208**
- 本版有意义的改动 **12** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `A-桌面主程序/app/bridge.py`
  - `A` `A-桌面主程序/app/packlabel.py`
  - `M` `A-桌面主程序/app/ui/tab_detect.py`
  - `M` `A-桌面主程序/app/ui/tab_dlmon.py`
  - `M` `A-桌面主程序/app/ui/tab_export.py`
  - `M` `A-桌面主程序/app/ui/tab_fine.py`
  - `M` `A-桌面主程序/app/ui/tab_publish.py`
  - `M` `A-桌面主程序/app/ui/tabs.py`
  - `M` `A-桌面主程序/app/ui/wall_main.py`
  - `M` `A-桌面主程序/app/v3img.py`
  - `M` `B-微信小店适配器/data/logs/wx60-std.log`
  - `M` `B-微信小店适配器/data/overview.json`
- 本版提交 SHA：`git rev-parse v6`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

## v5 · 2026-11-12 21:32:48 +08:00 · P0.3.6 新手可用：8.0 首页顶部新增『今天照着做』店主操作层（4行：货状态/报活动/视频挂链/卖了多少，可勾选记忆），系统自检7字段折叠下移并标注『你不用管』；新增独立自包含 HTML 说明页；engine.py py_compile + 真起9192 HTTP回读7断言全绿 + 首页/模块2 双截图验收

- 标签：`v5` ｜ 基线 commit（上一版）：`a2b6038c4` ｜ 密钥终检：**0 命中** ｜ 纳入 **1239** 文件 / 排除 **1207**
- 本版有意义的改动 **20** 项（A 新增 / M 修改 / D 删除 / R 改名）：
  - `M` `A-桌面主程序/app/ops/engine.py`
  - `M` `B-微信小店适配器/app.py`
  - `M` `B-微信小店适配器/data/logs/wx60-std.log`
  - `M` `B-微信小店适配器/data/overview.json`
  - `A` `B-微信小店适配器/data/xf9_catalog.json`
  - `A` `B-微信小店适配器/data/xf9_ledger.jsonl`
  - `A` `B-微信小店适配器/data/xf9_state.json`
  - `M` `B-微信小店适配器/web/index.html`
  - `M` `B-微信小店适配器/web/menu.js`
  - `A` `B-微信小店适配器/web/pages9.js`
  - `A` `B-微信小店适配器/web/vendor9/crypto-browserify-DuEXuEwu.js`
  - `A` `B-微信小店适配器/web/vendor9/dist-CGwuciAw.js`
  - `A` `B-微信小店适配器/web/vendor9/naive-ui-BLRq4YLq.js`
  - `A` `B-微信小店适配器/web/vendor9/preload-helper-DYl5dUZ5.js`
  - `A` `B-微信小店适配器/web/vendor9/rolldown-runtime-DAXXjFlN.js`
  - `A` `B-微信小店适配器/web/vendor9/tos.esm-D9GlSBMx.js`
  - `A` `B-微信小店适配器/web/vendor9/useLocalShortVideo-Drins-0G.js`
  - `A` `B-微信小店适配器/web/vendor9/utils-libs-CxrTFSuI.js`
  - `A` `B-微信小店适配器/xf9.py`
  - `A` `D-调查报告与交付证据/新手运营-今天照着做.html`
- 本版提交 SHA：`git rev-parse v5`；改动记录同步维护在 `VERSIONS.md` 与仓库 tags 页。

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

