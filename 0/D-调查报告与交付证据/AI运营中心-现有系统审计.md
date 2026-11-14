# AI运营中心 - 现有系统审计

> 调查角色：现有系统审计员（只读）。调查时间：2026-11-09 17:36–18:10。
> 证据口径：`[源码确认]`（附 `文件:行号`）/ `[本机实测]`（附命令与输出）/ `[官方文档]` / `[设计建议]` / `[尚未验证]`。
> 红线遵守：未改任何源码、未写任何生产库（全部 `mode=ro`）、未启停任何服务、未执行任何真实上架/发布。密钥一律「已脱敏（字段名 xxx，值不录）」。

---

## 结论摘要（≤200字）

跑的是 `上货台\`（PyQt6 壳）+ `taobao-picker\server`（后端）两棵树，进程 `pythonw run.pyw` 14:20:14 启动，`上货台.exe` 未运行；生产库是 `taobao-picker\data\v3.db`（111 MB / 28 表 / 372 商品），另有三处 0 字节同名 `v3.db` 是陷阱。**视频已入库**：`v3_image.role='video'` 194 行全部 `dl_state=ok` 且有本地 mp4（磁盘 210 个），但 `v3_product` 无视频字段。**抖店侧不是全新做**：`微信小店自建上货台` 里已有 7.0 抖店模块（`dd.py` + `pages7.js` + 5 个页面），形态是 ego 浏览器自动化 + 16 列 xlsx 导入，无开放平台 API。**微信侧 17 个官方接口已实现、零浏览器模拟，但视频/达人/活动全缺**。**任务恢复只有下载侧真实可用**（`dlcenter` 断点续传 + 启动自愈），`v3_task_log` 是死表、`v3_job` 只能标"待重试"。

---

## 一、真实运行目录与陷阱排除

### 1.1 结论

| 层 | 真实路径 | 判据 |
|:--|:--|:--|
| A 桌面壳（唯一入口） | `G:\Users\Administrator\Documents\deepseek-harness\default-workspace\上货台` | 进程命令行 + `paths.py` 推导 |
| B 后端引擎 | `...\default-workspace\taobao-picker\server`（含 `server\v3\` 46 个 .py） | `paths.py:27-28` 写死绝对路径并注入 `sys.path` |
| C 微信小店网页后台 | `...\default-workspace\微信小店自建上货台` | `wxserve.py:30` 写死绝对路径，**进程内**加载 |
| D 4.1 主图 AI 重做 | `taobao-picker\image_lab` + `data\image_lab.db` | `paths.py:29-31` |
| E 小猪本地化 | `J:\aixitong\小猪\{4.0.5本地化, xiaozhu4, 本地鉴图}` | `paths.py:43-52` |

### 1.2 判据链（不是看文件名猜的）

1. **进程**：`Win32_Process` 实测 `pythonw.exe 40624 / 55584`，命令行 `"F:\pyenvs\douyin\Scripts\pythonw.exe" run.pyw`，`CreationDate=2026/11/09 14:20:14`；其父进程是 DSH runner，启动脚本里写着 `$wd='...\default-workspace\上货台'`、`-WorkingDirectory $wd`。**同一进程即 9191 的属主**（`wxserve` 设计为进程内 HTTP 服务）。`[本机实测]`
2. **进程表里没有 `上货台.exe`** —— 打包版（`dist\上货台\上货台.exe`）本轮未运行，所以「旧 EXE / 新源码错位」这次不成立。`[本机实测]`
3. **源码自我定位**：`paths.py:21-23` 非 frozen 时 `HERE=...\上货台\app`、`ROOT=...\上货台`；`paths.py:14-19` 是 PyInstaller 分支（`HERE=exe 所在目录`、静态资源在 `_MEIPASS`）。`[源码确认]`
4. **版本号**：`APP_VER = "0.6.0"`（`paths.py:10`）—— 界面叫「6.0 微店后台管理」与此一致；**没有任何 7.x 版本号**。`[源码确认]`

### 1.3 排除的陷阱（全部实测）

| 陷阱 | 实测事实 | 结论 |
|:--|:--|:--|
| `taobao-picker\上货台\⚠这不是正式运行目录.md` | 目录存在，文件自述非运行目录 | **不是运行目录**，只是同名占位 `[本机实测]` |
| `上货台\data\v3.db` | **0 字节**，mtime 2026-10-27 | 空壳，误读会得到「库是空的」假结论 `[本机实测]` |
| `taobao-picker\data\v3\v3.db` | **0 字节**，mtime 2026-10-22 | 同上 `[本机实测]` |
| `taobao-picker\data\shanghuotai.db` | **0 字节** | 同上 `[本机实测]` |
| `_snapshot-全量-上货台-v20261022-*` / `_baseline` / `_backup-*` / `dist\` | 历史快照或打包产物 | 都不是运行目录 `[本机实测]` |

### 1.4 与说明书的偏差（底稿 §3.5 要求写明）

说明书 `01_架构.md` 等描述的目录与磁盘**不完全一致**：磁盘实际是「PyQt 壳 + 后端 server 包 + 三个外挂程序」四块，而不是单一应用树。**本报告一律以磁盘真身为准。** `[本机实测]`

### 1.5 顺带核实底稿的一条 `[未验]`

底稿 §3.2 把 **8326（node 38096）** 标为「需核实它到底提供什么接口」。实测其命令行为
`...\dependencies\node\bin\node.exe ... @deepseek-ai\dsh-desktop-host\lib\index.js ...`，
**它是 DSH 桌面宿主本体，不是独立的「本地 AI 网关」服务**。`[本机实测]`

---

## 二、数据库地图（v3.db 28 表 + 其它库）

### 2.1 主库 `taobao-picker\data\v3.db`

`111,321,088 B` · `schema_version=3.0`（`v3_meta`）· `schema_applied_at=2026-11-09 14:20:26` · 28 表。`[本机实测]`

按职责分组（行数为本轮只读实测）：

| 组 | 表（行数） | 管什么 |
|:--|:--|:--|
| 商品主档 | `v3_product` 372、`v3_sku` 8450、`v3_store` 1 | 商品 / SKU / 店铺；`v3_store` 只有 1 行 → **单店模型** |
| 媒体 | `v3_image` 18353、`v3_image_ocr` 18156、`v3_image_edit` 21 | 图片与视频同一张表（靠 `role` 区分，见 §四）；OCR 结果；修补台账 |
| 选品/词库 | `v3_keyword` 22052、`v3_keyword_waiver` 9、`v3_title_policy` 1、`v3_title_variant` 0 | 关键词库、豁免、标题策略；**标题候选 0 行** |
| 包（交接） | `v3_pack` 4、`v3_pack_member` 399 | 引用式成员包（不复制商品行） |
| 上架 | `v3_publish` 1、`v3_publish_intent` 854、`v3_upload_status` 149、`v3_upload_batch` 7 | 发布记录 / 幂等意图 / 19 列出表状态 |
| 微信缓存 | `v3_wx_img_cache` 11065、`v3_wx_title_idx` 764 | 图床 URL 缓存、标题索引 |
| 任务 | `v3_job` 1、`v3_task_log` 1、`v3_dl_job` 3、`v3_dl_task` 26、`v3_dl_log` 157 | **见 §七，这是最大缺口** |
| 审计/回收 | `v3_audit` 1581、`v3_trash` 0 | 操作留痕（写得很勤）、回收站空 |
| 其它 | `v3_category_map` 0、`v3_meta` 2、`sqlite_sequence` 14 | 类目映射未用 |

**关键读数**：`v3_product` 372 / `v3_publish` 1（且该 1 行 `external_listing_id='TEST-001'`、`note='自检'`）→ 上架链路「有意图、有状态、**几乎没有真实发布记录**」。`[本机实测]`

### 2.2 其它库（三个各有专职，没有一个能替代 v3.db）

| 库 | 大小 | 真实内容 | 管什么 |
|:--|--:|:--|:--|
| `taobao-picker\data\image_lab.db` | 167,936 B | `batch_jobs` 4 / `image_jobs` 20 / `image_job_steps` 69 / `image_assets` 36 / `image_regions` 10 / **`lab_writeback` 0** / `image_cost_ledger` 0 | **4.9 主图 AI 重做**的账本（任务、步骤、成本、资产）。`lab_writeback=0` 说明**从未自动写回 v3_image** |
| `taobao-picker\data\products.db` | 2,715,648 B | `products` 205 / `fine_queue` 205 / `analysis_items` 256 / `task_items` 352 / `capture_log` 389 / `img_cache` 192 / `phash_cache` 239 | **旧 V1/V2 采集-初筛库**（平台商品、精筛队列、分析结果）。与 v3 不同源、不复用 |
| `J:\aixitong\小猪\本地鉴图\data\商品.db` | 1,269,760 B | `商品` 251 / `SKU` 1810 / `图片` 1831 / `属性` 127 / `数据包` 6 / `包内商品` 257 / `记录` 4 | **小猪本地化侧**的鉴图/打码/出表库；上货台以 `importer.import_pig` 只读它 `[源码确认]` `importer.py:370-404` |
| `taobao-picker\data\backup\` + `data\v3\backup\` | 51 文件 541 MB + 17 文件 100 MB | `v3.before-*` 系列 | 历史备份，勿当生产库 |

`[本机实测]`（全部 `sqlite3.connect('file:...?mode=ro', uri=True)`）

**结论**：**统一商品库只能是 `v3.db`**，它已是单一真源（372 商品 / 18353 媒体）；`image_lab.db` 是它的旁挂账本，`products.db` 与 `商品.db` 是上下游外部系统的库。7.0 若再建第二个商品库，直接违反主人第 10 条约束。

---

## 三、采集→上架 全链路数据流（含表/函数/文件）

### 3.1 链路图（L=库，F=文件，W=网页/接口）

```
[F] 受控浏览器 CDP ── launch_collect_browser.py ──► 淘宝页面
        │  (ego lite 优先，端口 9223-9299，五层校验，回写 settings.json)
        ▼
1 · 采集初筛   tabs.py:62  bridge.run_intake(...)
        │         └─ 旧 server 采集器（server\*.py）
        ▼
[F] 采集结果 JSON / 初筛结果  ──►  packs\01_URL初筛\*.csv （本轮实测 20 个文件）
        │
        ▼
2 · 商品卡导入  cards.import_folder  (cards.py:85)
        │    ├─ importer.import_records / import_mapped（importer.py:308 / 607）
        │    └─ cards.fetch_video_urls（cards.py:264）→ 写入 role='video' 行
        ▼
[L] v3.db：v3_product(372) / v3_sku(8450) / v3_image(18353) / v3_image_ocr(18156)
        │    同时 INSERT OR IGNORE v3_pack_member（cards.py:174）
        ▼
2.5 商品库 / 2.5.1 下载监控
        │    下载：dlcenter.py（Range 断点续传）→ 写 v3_image.current_local_path/dl_state
        │    监控：tab_dlmon.py + dlcenter_win.py
        ▼
3 · 商品精筛   flow.py / criteria.py / grade.py / positioning.py
        │    store.job_start → [L] v3_job（stage='chain'）
        │    通过的商品存成包：pack.py:625 → [L] v3_pack + v3_pack_member
        ▼
4 · 检测打码   images.py / mask.py / detect_slim.py / inpaint_lama.py / repair.py / maskgen.py
        │    产出登记进 [L] v3_image_edit（edits.py:171/203/232/251；bridge.py:4150）
        ▼
4.1 主图人工审核（tab_wall_main.py + wall_main.py）
        │    审核状态也落在 v3_image_edit.status='confirmed'
        │
4.9 主图 AI 重做（tab_lab.py + image_lab/）→ [L] image_lab.db（旁挂，不直改 v3_image）
        ▼
5.0 直接上架   tab_publish.py → bridge → wxshop.py
        │    publish_guard.begin → [L] v3_publish_intent(854)（publish_guard.py:195）
        │    wxapi.WxShop.add_product → [W] api.weixin.qq.com /channels/ec/product/add
        │    record_publish → [L] v3_publish（packaging.py:337）
        ▼
5.5 出表       packaging.export_package（packaging.py:228）→ [F] 微信 19 列 xlsx
              doudian.export_xlsx（doudian.py:264）→ [F] 抖店 16 列 xlsx
              status.py:134 → [L] v3_upload_status(149)
        ▼
[F] 人工/自动化导入平台后台（见 §五）
```

### 3.2 每一跳的性质

| 跳 | 载体 | 证据 |
|:--|:--|:--|
| 浏览器 → 采集器 | **文件/JSON**（无库） | `tabs.py:62` `bridge.run_intake` `[源码确认]` |
| 采集 → URL 初筛包 | **文件 CSV**（`packs\01_URL初筛`，实测 20 个） | `[本机实测]` |
| 卡导入 → v3.db | **库**（事务写 product/sku/image/ocr） | `cards.py:85-174`、`importer.py:308` `[源码确认]` |
| 下载 → 素材 | **文件 + 库**（文件落 `data\v3\images\<pid>\<role>\`，路径回写 `v3_image.current_local_path`） | `dlcenter.py:598` `[源码确认]` |
| 精筛 → 包 | **库**（引用式，不复制） | `pack.py:625`、注释「只记 product_id」 `[源码确认]` |
| 上架 → 平台 | **网页/HTTP API**（微信走 OpenAPI；抖店走浏览器） | `wxapi.py`、`excel-route-4.mjs` `[源码确认]` |
| 出表 → 平台 | **文件 xlsx → 人工/自动化导入** | `doudian.py:264` `[源码确认]` |

### 3.3 链路中的空段（实测）

- `packs\02_商品细包`、`03_精筛结果`、`04_出表` **各 0 个文件** —— 说明这套「包仓库」当前只用到前两段，精筛/出表走的是库内查询而非包文件。`[本机实测]`
- 采集侧产物同时存在两套口径（`采集结果\` 与 `packs\`），`paths.py:32` 注明「老落点（只好兼容）」。`[源码确认]`

---

## 四、视频与图片：是否入库、落盘在哪、真实计数

### 4.1 视频**已入库**，用的是 `role` 而不是 kind/type

```
v3_image 列（22 列，无 kind / no type）：
image_id, product_id, slot_key, role, sequence, slice_index, sku_id,
original_url, original_local_path, current_local_path, current_remote_url,
content_sha256, parent_image_id, status, width, height, note,
created_at, updated_at, dl_state, download_hint
```
`[本机实测]`（`PRAGMA table_info(v3_image)`）

**区分视频与图片的字段是 `role`**：`main` / `detail` / `sku` / `video`。`[源码确认]` `taskcenter.py:208-232` 明确按 `i.role='video'` 聚合。

### 4.2 真实计数（只读实测）

| 口径 | 计数 |
|:--|--:|
| `v3_image` 总行 | **18,353** |
| `role='detail'` | 6,276（ok 6,259 / fail 1 / 待 16） |
| `role='main'` | 5,530（ok 5,488 / 待 42） |
| `role='sku'` | 6,353（ok 6,353） |
| **`role='video'`** | **194（ok 194 / fail 0 / 待 0）** |
| video 行中 `current_local_path` 非空 | **194 / 194（100%）** |
| 磁盘 mp4 文件 | **210**（比库多 16 → 孤儿文件或无主下载） |
| 含 `video` 子目录的商品目录 | **120**（194 个视频分布在 120 个商品上，平均 1.6 个/商品） |

`[本机实测]`

### 4.3 `v3_product` **没有**任何视频字段

23 列全列：`product_id, source_platform, source_url, source_product_id, original_title, category_id, category_path, category_confidence, attributes, main_image_id, original_payload, provenance, source_shop_name, source_store_id, pack_code, price_min, price_max, status, profile, note, created_at, updated_at, data_grade`。
→ 视频**不挂在商品上，只挂在媒体行上**。`[本机实测]`

### 4.4 已有但**没被调用**的视频聚合能力

`taskcenter.py:203-268` 已实现 `video_stats()` / `video_filter()`（5 种筛选桶）/ `video_items()`（含 `exists` / `size_bytes` / `duration_s=None` 不编）。
**但全仓 grep `taskcenter` 只有 2 个验证脚本 import**：`_验_第1批后端.py:7`、`_run_stage0_report.py:7`。`[源码确认]`
→ 这是 7.0 视频模块**可直接抄的现成实现**，但当前**界面上没有任何地方调它**。

### 4.5 视频元数据缺口（实测）

`role='video'` 行的 `width` / `height` **全部为 NULL**，`note` 里只有 `bytes=xxxx`。`[本机实测]`
`taskcenter.video_items` 里已写 `duration_s = None  # 未读时长就是 None，不编`（`taskcenter.py:265`）→ **时长/分辨率/编码格式都没采集**，做「视频生产/发布」前必须补。

### 4.6 微信侧视频通路是断的（关键）

| 位置 | 事实 |
|:--|:--|
| 19 列导入解析 | `app.py:1171` 解析了 `'video': col(first,'视频','商品视频')` |
| 但导入时 | `app.py:1300-1301`：`if it.get('video'): skip += '｜视频未接（源为第三方临时链）'` → **明确跳过** |
| 编辑动作 | `app.py:1778-1819` 有 `op='video'` → 键 `head_videos`，但仅作字段透传，`wxapi.py` **无任何视频上传接口** |

`[源码确认]`
**结论：视频「采集+入库+落盘」已完成，「上架到微信」未打通，「生成/发布短视频」完全未开始。**

---

## 五、抖店侧现状（脚本位置、调用方式、可复用度）

### 5.1 结论先说：**不是全新做，7.0 抖店模块已经存在**

`微信小店自建上货台\` 下已有一套完整的 7.0 抖店后台管理：

| 资产 | 位置 | 状态 |
|:--|:--|:--|
| 后端模块 | `微信小店自建上货台\dd.py`（241 行） | **已实现**，9 个 GET + 2 个 POST |
| 前端页面 | `web\pages7.js`（5 页：概览/草稿箱/货源/上架执行/后台跳转） | **已实现** |
| 菜单 | `web\menu.js:111-120` `key:'dd-admin', title:'抖店后台管理 7.0'` | **已挂上** |
| 路由分发 | `app.py:1402` `if p.startswith('/api/dd/')` → `dd.api_get/api_post` | **已接线** |
| 上架执行器 | `publish.py`（跑 `.mjs`，`creationflags=0x08000000` 无窗） | **已实现** |

`[源码确认]`

### 5.2 抖店「数据从哪来」——没有免签 API

`dd.py:5-6` 原文：
> 微店有官方 HTTP 接口，本机直连就能拉到 563 件真商品；**抖店没有可直连的免签接口，真实数据只能来自 ego 自动化跑出来的页面快照**。

数据源写死三类（`dd.py:8-11`）：① 页面快照 `抖店研究\work\<pid>\*draftbox*.json`、`抖店研究\crawl3\*draft*.json`；② 上货台 `packs` 的 CSV；③ `ego/doudian-publish-*` 下的 `.mjs`。
`dd.py:13`：**没有快照就如实报「还没采过」，绝不编造商品与件数。**

### 5.3 脚本位置（全部实测存在）

| 目录 | 内容 |
|:--|:--|
| `C:\Users\Administrator\ego\doudian-publish-20261029-2050\` | **42 个 .mjs**：`round-01..40.mjs`、`publish-1391.mjs`(16.8 KB)、`_selftest.mjs` |
| `C:\Users\Administrator\ego\main\` | **60+ 个 .mjs**：`excel-route-1..4.mjs`、`excel-route-all.mjs`、`dy-api-list/dy-cdp-*/dy-reject-*/dy-sold41/42`、`xz-*.mjs`（小猪） |
| `C:\Users\Administrator\ego\agentB\xlsx_fixed\` | `dy-<pid>.xlsx` 出表包（实测 8+ 个，6.0-7.1 KB/个） |
| `C:\Users\Administrator\ego\{dd-category-20261107, doudian-1454-20261029-1730, doudian-1531-*}` | 类目申请 / 单件跑批 |
| `抖店研究\` | 23 份策略文档 + `work\`(快照) + `crawl3/4` + `上货引擎\` |

`[本机实测]`

### 5.4 调用方式：**ego lite 浏览器自动化 + xlsx 导入，不是 HTTP API**

`excel-route-4.mjs` 实测四段（全文 48 行）：

```js
await page.goto("https://xiaozhu.huanhou.cn/c/excelCopyItem/dyForm", ...)   // ① 开小猪远端导入页
await page.evaluate(() => { #goTarget.value = "sale"; ... })                // ② 选「直接售卖中」
await page.setInputFiles("#excelFile", FILE)                                // ③ 上传 dy-<pid>.xlsx
await page.evaluate(() => document.querySelector("button.bottom-float-btn").click())  // ④ 点提交
await page.goto(".../batchCommon/copyManage?...")                           // ⑤ 回读任务 ID 判「识别失败」
```

`[源码确认]` `C:\Users\Administrator\ego\main\excel-route-4.mjs:12-46`

要点：
- **目标是 `xiaozhu.huanhou.cn`（小猪的远端页面），不是抖店后台**；抖店真实建品由小猪代劳。
- `goTarget=sale` → **直接落「售卖中」，不进草稿箱**（`dd.py:141-143` 注释核实过）。
- `dd.py:144` `DY_XLSX_DIR = C:\Users\Administrator\ego\agentB\xlsx_fixed` —— 上货台只负责「落 xlsx + 入队」，**没有即时建品能力**。
- 队列文件 `微信小店自建上货台\data\dd_queue.jsonl`（`dd.py:147`）—— 本轮实测该文件**尚未存在**（`data\` 目录列表里没有），说明 `enqueue` 从未被真实调用。`[本机实测]`

### 5.5 上货台侧的抖店铺垫（只有导出，没有连接器）

| 资产 | 位置 | 能力 |
|:--|:--|:--|
| `server\v3\doudian.py`（310 行） | taobao-picker | **只做 16 列 xlsx 导出**：`build_rows` / `export_xlsx` / `collect`。`doudian.py:21-24` 明确「模板没有视频列 ⇒ 绝不加列」 |
| `bridge.v3_export_doudian` | `bridge.py:1604-1640` | 调上面那个模块，落 `data\exports\抖店_16列_<stamp>.xlsx`，注释「能否真导入需你上传后自己确认」 |
| 模板 | `taobao-picker\data\templates\抖店模板二.xlsx` | 真模板（16 列，一 SKU 一行） |

`[源码确认]`
**bridge 里没有任何抖店 connector 方法**（grep `dd_*` / `doudian` 只有导出相关，`bridge.py:588-593/1604`）。

---

## 六、微信小店侧现状（API 已实现 / 浏览器模拟 / 缺失）

### 6.1 已实现的官方接口：**17 个**（`wxapi.py` 逐条列出）

| # | 方法 | 官方端点 | 行号 |
|--:|:--|:--|--:|
| 1 | `token()` / `stable_token` | `/cgi-bin/stable_token` | 59-83 |
| 2 | `shop_info()` | `/channels/ec/basics/info/get` | 112 |
| 3 | `product_ids()` | `/channels/ec/product/list/get` | 116 |
| 4 | `product()` | `/channels/ec/product/get`（`data_type=2` 才能读未上架） | 124 |
| 5 | `add_product()` | `/channels/ec/product/add` | 128 |
| 6 | `update_product()` | `/channels/ec/product/update` | 131 |
| 7 | `listing()` / `delisting()` | `/channels/ec/product/listing` / `delisting` | 134-138 |
| 8 | `cancel_audit()` | `/channels/ec/product/audit/cancel`（审核中必先撤审） | 140-143 |
| 9 | `delete_product()` | `/channels/ec/product/delete` | 145 |
| 10 | `stock_update()` | `/channels/ec/product/stock/update` | 148 |
| 11 | `categories()` | `/shop/ec/category/all`（带 24h 缓存） | 155 |
| 12 | `category_detail()` | `/shop/ec/category/detail` | 163 |
| 13 | `upload_image()` | `/shop/ec/basics/img/upload`（multipart + **必须带真实宽高**） | 202 |
| 14 | `freight_templates()` | `/channels/ec/merchant/getfreighttemplatelist` | 226 |
| 15 | `coupons()` | `/channels/ec/coupon/get_list` | 229 |
| 16 | `orders()` | `/channels/ec/order/list/get` | 233 |
| 17 | `img_size()` | 本地纯标准库解析 PNG/JPEG/GIF/BMP 宽高 | 167 |

`[源码确认]`；头注释 `wxapi.py:4-19` 自述「所有路径均在本机实测通过（2026-10-26）」。

### 6.2 浏览器模拟：**微信侧 0 处**

`app.py`（100 KB）全文 grep `ego|cdp|playwright|selenium|pyautogui|remote-debugging` → **只命中 `category_*` 的函数名误匹配，无一处浏览器驱动**。`[源码确认]`
→ 微信小店侧**纯 HTTP 直连**，这正是它比抖店侧可靠的原因。

### 6.3 缺失能力（做 AI 运营中心必须新建的）

| 缺失 | 说明 |
|:--|:--|
| **视频上传/发布** | `wxapi.py` 无视频接口；导入路径明确「视频未接」（`app.py:1300-1301`） |
| 商品卡曝光优化 | 无曝光/点击/搜索词数据接口引用 |
| 搜索商机 / 关键词趋势 | 无 |
| 活动报名 / 官方活动 | 无 |
| 优惠券**创建** | 只有 `coupon/get_list`（读），无 create |
| 达人推广 / 联盟 / 优选联盟 | 无 |
| 售后 / 退款 | 无（只有 `order/list/get` 读） |
| 物流发货 / 电子面单 | 无 |
| 评价管理 | 无 |
| 数据罗盘 / 经营数据 | 无 |
| 多店并发 | `v3_store` 只有 **1 行**；`wxshop.py:5558-5560` 有「多店」分支但库侧未落多店 |

`[源码确认]` + `[本机实测]`

### 6.4 凭据

`微信小店自建上货台\config.json`（103 B）与运行时账号空间（`/api/account/add`，`app.py:1539`）保存 appid/secret —— **已脱敏（字段名 `appid` / `secret`，值不录）**。
健康检查实测 9191 `/api/health` 返回 `{"ok":true,"appid":"wx6ff1****"}`（底稿 §3.2）→ **密钥有效，链路通**。

---

## 七、任务日志与恢复能力（真实缺口）

### 7.1 四张表 + 各自真实写入方

| 表 | 行数 | 写入方 | 是否被生产代码调用 |
|:--|--:|:--|:--|
| `v3_task_log` | **1** | `taskcenter.begin/update`（`taskcenter.py:85/101`） | **否** —— 全仓只有 `_验_第1批后端.py:7`、`_run_stage0_report.py:7` 两个验证脚本 import，`上货台\app\` 内 **0 处** |
| `v3_job` | **1** | `store.job_start/job_finish`（`store.py:507/522`） | **是**（`flow.py` 链式精筛）；但 `job_retry`（`store.py:534`）只把状态改 `pending` 并返回「**已标记待重试，请调对应阶段的接口再跑一次**」→ **不自动续跑** |
| `v3_dl_job` / `v3_dl_task` | 3 / 26 | `dlcenter.py` | **是，且是唯一真有恢复能力的** |
| `v3_dl_log` | 157 | `dlcenter._log` | 是（人读日志） |

`[源码确认]` + `[本机实测]`

### 7.2 下载侧：**真有断点续传 + 启动自愈**

`dlcenter.py` 头部自述（`dlcenter.py:14/26`）：
```
**继续**优先用 HTTP Range 从断点续；**停止**取消未开始的、保留已完成的。
recover(db_path)   程序重启后把半死任务收成 paused
```
实现证据：`resumable` 列（`dlcenter.py:90`）、`part_path`、`resume()`（:352）、`stop()`（:371）、`recover()`（:380）、416 处理「断点已到底」（:426）、`T_VERIFY` 校验（:566）。
**接线证据**：`main_window.py:337` 启动即调 `bridge.v3_dl_recover()` → `bridge.py:4157-4161` → `dlcenter.recover()`；成功后写日志「上次未跑完：N 个文件已置暂停，可在 2.5 的下载中心点「继续」」。`[源码确认]`
本轮实测 `v3_dl_job#1` 仍停在 `state='paused'`（2026-10-25 起），`note='跳过已下载 24 条引用'` —— **恢复机制生效过，但没人去点「继续」**。`[本机实测]`

### 7.3 结论：一句话说清能力边界

| 能力 | 现状 |
|:--|:--|
| 断点续传（单文件） | ✅ 有（HTTP Range + part 文件 + 校验） |
| 崩溃后任务回收 | ✅ 仅下载侧（启动时收成 paused，需人点继续） |
| 失败重试 | 🟡 下载侧 3 次（`max_tries=3`，实测 task 29/30/28 均 `tries=3` 后 failed）；链路侧只标 pending |
| 统一任务队列 / 调度器 | ❌ 无 |
| 任务日志（可查询） | ❌ `v3_task_log` **事实上的死表**（1 行，来自 2026-10-25 一次 404 下载） |
| JSONL 机读日志 | 🟡 `taskcenter.event` 会写 `data\v3\logs\tasks-YYYYMMDD.jsonl`（`taskcenter.py:152`），但因无人调用而不产生 |
| 进度上报 | ❌ 无统一进度源（各 tab 各自算） |
| 超时 / 退避 / 去重 | ❌ 无（`v3_job.idempotency_key` 字段存在且 UNIQUE，`store.py:307/511-514`，但只有一处传值） |

`[源码确认]` + `[本机实测]`
**所以 7.0「统一运营任务中心」必须新建调度层**，`v3_task_log` 只能当「已设计好的表结构」参考，不能当「已在用的基础设施」。

---

## 八、已有浏览器自动化能力盘点

### 8.1 三个组件（各自职责不同，别混）

| 组件 | 路径 | 干什么 | 关键实现 |
|:--|:--|:--|:--|
| **采集浏览器启动器** | `上货台\launch_collect_browser.py`（492 行） | 起一个**专用配置目录**的 Chromium 并开 CDP | `DEDICATED_ROOT=%LOCALAPPDATA%\dsh-collect-browser\<ego\|edge>`（:58-59）；`AUTO_ORDER=["ego","edge"]`（:55，**ego 优先**）；端口 9223-9299（:56）；五层校验 `verify()`（:215-277）；`ensure_page_target()` 解决 ego 冷启动停在引导页（:169-211）；回写 `settings.json.cdp_port`（:290-307） |
| **连接自检器** | `上货台\_验_ego连接.py`（168 行） | 验「能不能用来采集」 | 5 层：`/json/version` → `/json/list` 有 `type=page` → `websocket` 模块 → `Runtime.evaluate` → `Input.dispatchMouseEvent`（:40-139） |
| **拟人化注入** | `上货台\hooks\hook-humanize.py` + 3 个 html | 鼠标轨迹/打字拟人 | 仅采集页使用 |

`[源码确认]`

### 8.2 能做什么（实测存在的能力）

- 驱动 ego lite / Edge 的 CDP：`Runtime.evaluate` 执行任意 JS、`Input.dispatchMouseEvent` 合成鼠标（`_验_ego连接.py:100-139`）。
- 复用已登录态：`--user-data-dir` 专用目录，登录一次长期复用（`launch_collect_browser.py:480-482`）。
- **抖店后台上架全流程**：`page.goto` → 选择项 → `setInputFiles` 上传 xlsx → 点提交 → 回读结果（`excel-route-4.mjs`）。
- **页面快照抓取**：`抖店研究\work\<pid>\*draftbox*.json`（草稿箱文本快照），供 `dd.py` 解析。
- 弹窗/审核/违规页抓取：`dy-reject-*.mjs`、`dy-violated*`、`dy-sold41/42.mjs` 等 20+ 个一次性脚本。
- 后台无窗执行：`publish.py:153` `creationflags=0x08000000`。

### 8.3 不能做什么（必须新建或补齐的部分）

| 缺口 | 证据 |
|:--|:--|
| **没有执行器/任务队列** —— 全是「手写一次性 .mjs」 | `ego\main\` 里 60+ 个脚本，无 runner、无重试、无状态机 |
| **没有 Playwright 级抽象** | 只有 CDP 裸调用；无 `locator()` / `expect()` / 自动等待 |
| **没有失败自动重试** | `round-01..40.mjs` 各写一段固定点击，`excel-route-*.mjs` 只有 try/catch 打日志 |
| **没有多空间并发隔离的证明** | `taskSpace("dy-upload")` 单空间（`excel-route-4.mjs:4`），未见多店并发脚本 |
| **没有页面元素稳定性层** | 直接 `querySelector('#goTarget')` / `button.bottom-float-btn`，抖店/小猪改版即断 |
| **没有截图/证据留存规范** | 只有零星 `.png`（`work\1454\r43-draft.png`） |
| **微信侧完全不用浏览器** | `app.py` 零匹配 → 微信无浏览器能力，也不需要 |

`[源码确认]` + `[本机实测]`

---

## 九、7.0 可复用的现成资产清单

| # | 资产 | 位置 | 能直接用什么 | 要改什么 |
|--:|:--|:--|:--|:--|
| 1 | 商品主库 v3.db（28 表 / 372 商品 / 18353 媒体） | `taobao-picker\data\v3.db` | 商品/SKU/媒体/OCR/包/上架意图全部现成；**不要建第二套** | 只加新表（运营任务、曝光数据、视频元数据），不动已有列 |
| 2 | 视频行（194 条，全 ok、全有本地路径） | `v3_image.role='video'` | 直接按 `role='video'` 查，已有分布统计 | 补 `width/height/duration` 采集；清理 16 个孤儿 mp4 |
| 3 | 视频聚合函数 | `taskcenter.py:203-268` `video_stats/video_filter/video_items` | 五种筛选桶、`exists`/`size_bytes` 已写好，照抄即可 | 接进界面（当前零调用）；`duration_s` 从 None 变真值 |
| 4 | 统一任务表结构 | `taskcenter.py:51-71`（`v3_task_log` + 2 索引 + JSONL 事件） | 字段设计（`task_id/batch/stage/action/status/progress/error_code/payload`）可直接用 | 必须新建**调度器**去真正调用它；现在的表是空的 |
| 5 | 下载引擎（断点续传 + 恢复） | `dlcenter.py`（42 KB） | `start/pause/resume/stop/recover` 全部可用；启动自愈已接线（`main_window.py:337`） | 抽成通用「长任务执行器」底座，把「下载」泛化到「采集快照/上传/发视频」 |
| 6 | 微信 OpenAPI 客户端 | `微信小店自建上货台\wxapi.py`（17 个接口） | token 缓存/自动重取/错误码/图片宽高解析全部现成，**直接 import 复用** | 补视频、达人、活动、售后、物流、数据类接口 |
| 7 | 微信小店网页后台（含 7.0 抖店模块） | `微信小店自建上货台\`（app.py 100 KB + web\） | 5 个抖店页 + 9 个抖店接口 + 6.0 上架平台已可用，**通过 6.0 tab 内嵌即可访问** | 抖店「上架执行」要改成常驻队列（现在是一次性 `.mjs`） |
| 8 | 6.0 内嵌机制 | `上货台\app\wxserve.py`（进程内 HTTP + 20s 自愈 + `SO_EXCLUSIVEADDRUSE` 真空闲判定） | 端口复用/假死自愈/`pythonw` 无 stderr 三个坑都已修好，**新服务照这个模式写** | 7.0 独立进程时不要重复它的自愈逻辑，改为被它管 |
| 9 | 16 列抖店导出 | `server\v3\doudian.py` + `bridge.v3_export_doudian` | xlsx 生成、SKU 行模型、warning 体系全部现成 | 无需改（抖店模板无视频列，红线是「绝不加列」） |
| 10 | 采集浏览器启动器 | `上货台\launch_collect_browser.py` | 动态端口、专用配置、ego 优先、五层校验、回写配置 | 增加「多任务空间」参数（当前单实例单配置目录） |
| 11 | CDP 连接自检 | `上货台\_验_ego连接.py` | 5 层诊断可直接做成「浏览器自动化执行器」的健康检查 | 无 |
| 12 | 页签注册机制 | `main_window.py:249-272` | 往 `_pages` 尾部加一行就是新页签；**失败只写日志不崩**（`try/except` 逐页包） | 7.0 若要桌面页签，照这个模式加；**若走 web 内嵌则不用加** |
| 13 | 4.9 主图 AI 账本 | `image_lab.db`（jobs/steps/cost/assets/regions） | 成本记账、步骤追踪、区域标注的 schema 可参考 | `lab_writeback=0` → 写回链路从未跑通，需先打通再谈「AI 自动改图」 |
| 14 | 小猪链路 | `J:\aixitong\小猪` + `importer.import_pig`（`importer.py:404`） | 鉴图/打码/出表外部程序，`import_pig` 已能只读拉取 | 不搬不改（外部既有程序） |
| 15 | 操作审计 | `v3_audit`（1581 行）+ `store.audit()`（`store.py:500`） | 每步留痕已很完整，7.0 的 AI 动作应写同一张表 | 加 `actor='ai'` 口径 |

---

## 十、审计结论：7.0 能复用 vs 必须新建

### 10.1 能直接复用（≈70% 的地基已经在了）

1. **一个商品库**：`v3.db` 已是单一真源，禁止第二套。
2. **一个素材中心**：`data\v3\images\<pid>\<role>\` 已有 12,653 文件 / 4.3 GB，视频图片同构。
3. **一个桌面入口**：`上货台` 壳 + `_pages` 追加机制 + 6.0 内嵌 web 后台。
4. **微信连接器**：`wxapi.py` 17 个接口 + token 自愈 + 图片上传。
5. **抖店页面与接口**：`dd.py` + `pages7.js` + 5 页已存在（藏在 6.0 里）。
6. **长任务底座**：`dlcenter.py` 的 Range 续传 + `recover`。
7. **浏览器自动化**：CDP 起停、连接校验、无窗执行、专用配置隔离。
8. **视频数据与聚合**：194 条真视频 + `video_stats`。

### 10.2 必须新建（真空白）

| # | 必须新建 | 为什么（缺口证据） |
|--:|:--|:--|
| 1 | **统一运营任务中心 + 调度器** | `v3_task_log` 1 行且无生产调用；`v3_job` 只标 pending 不续跑；无队列/超时/退避/去重 |
| 2 | **抖店连接器的「API 化」或「常驻执行器」** | 现在只有一次性 `.mjs`；`dd_queue.jsonl` 尚未生成过；无重试、无状态机、无元素稳定层 |
| 3 | **微信侧运营类接口**（曝光/搜索词/活动/达人/数据/售后/物流/视频） | `wxapi.py` 17 个里**一个运营类都没有**，全是商品 CRUD |
| 4 | **视频生产 + 视频发布** | 库里有素材、有 194 条源视频；但无剪辑/生成、无上传接口、微信侧明确「视频未接」 |
| 5 | **曝光/流量数据模型** | 28 表中无任何流量/曝光/点击/转化表 |
| 6 | **AI 总调度与异常诊断** | 无 LLM 调用层接进运营链路（`ai_fine.py`/`positioning.py` 只服务选品） |
| 7 | **浏览器自动化执行器（常驻、带队列）** | 现有 60+ 一次性脚本，无 runner |
| 8 | **多店模型** | `v3_store` 1 行，`wxshop` 多店分支未落库 |

### 10.3 一句话给决策

**7.0 不需要重写采集/筛选/精修/上架（第 11 条约束成立），也不需要第二套商品库（第 10 条成立）；真正要新建的是「调度层 + 运营数据层 + 视频链路」这三块，而抖店页面层和微信接口层已有大半可复用。**

---

## 附录：本轮执行命令与输出摘录

### A1 进程与端口
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe' or Name='node.exe' or Name='上货台.exe'" |
  Select-Object ProcessId,Name,CreationDate,@{n='CL';e={$_.CommandLine}} | Format-List
```
摘录：
```
ProcessId 40624 / 55584  pythonw.exe  2026/11/9 14:20:14  "F:\pyenvs\douyin\Scripts\pythonw.exe" run.pyw
ProcessId 38096  node.exe  2026/11/9 1:49:42  ...\@deepseek-ai\dsh-desktop-host\lib\index.js ...
ProcessId 31892  python.exe  2026/11/9 15:40:02  ...\上货台\web_diag\webapp.py
ProcessId 40204  python.exe  2026/11/9 15:40:02  ...\上货台\web_diag\dlrunner.py
（无 上货台.exe）
```

### A2 主库表清单与关键计数（只读）
```python
sqlite3.connect("file:G:/.../taobao-picker/data/v3.db?mode=ro", uri=True)
```
摘录：
```
v3_image 18353 / v3_image_ocr 18156 / v3_sku 8450 / v3_product 372 / v3_pack_member 399
v3_publish 1 / v3_publish_intent 854 / v3_upload_status 149
v3_job 1 / v3_task_log 1 / v3_dl_job 3 / v3_dl_task 26 / v3_dl_log 157
role: detail 6276(ok 6259/fail 1/待 16) / main 5530(ok 5488/待 42) / sku 6353(全 ok) / video 194 (全 ok)
video: 194 行, current_local_path 非空 194
v3_publish 唯一行: product_id=334, external_listing_id='TEST-001', note='自检'
```

### A3 媒体落盘
```powershell
Get-ChildItem "$w\taobao-picker\data\v3\images" -Recurse -File -Filter *.mp4
```
摘录：
```
视频: files=210  MB=1206
图片: files=12392 MB=3117.3
v3\images 文件总数 = 12653  体积 = 4326.4 MB
含 video 子目录的商品数 = 120
masked 43 / 7.4 MB    out 935 / 531.9 MB    backup 17 / 100.2 MB    logs 46 / 0.4 MB
```

### A4 它与三处 0 字节库
```
taobao-picker\data\v3.db            111,321,088 B  2026-11-09 14:37:19   ← 真库
taobao-picker\data\v3\v3.db                   0 B  2026-10-22            ← 陷阱
上货台\data\v3.db                              0 B  2026-10-27            ← 陷阱
taobao-picker\data\shanghuotai.db             0 B  2026-10-27            ← 陷阱
```

### A5 其它库 schema（只读）
```
image_lab.db: batch_jobs 4 / image_jobs 20 / image_job_steps 69 / image_assets 36
              image_regions 10 / lab_writeback 0 / image_cost_ledger 0
products.db : products 205 / fine_queue 205 / analysis_items 256 / task_items 352
              capture_log 389 / img_cache 192 / phash_cache 239
小猪 商品.db: 商品 251 / SKU 1810 / 图片 1831 / 属性 127 / 数据包 6 / 包内商品 257
```

### A6 任务恢复接线
```powershell
grep -n "v3_dl_recover|dlcenter|recover" 上货台/app/**.py
```
摘录：
```
bridge.py:4157  def v3_dl_recover()  →  mod.recover(mod.db_path_of(c))
main_window.py:337  rc = bridge.v3_dl_recover()   # 启动即调用
dlcenter.py:380  def recover(db_path)   # 把半死任务收成 paused
store.py:534  def job_retry(...)  →  return "已标记待重试，请调对应阶段的接口再跑一次"
```

### A7 7.0 抖店模块存在性
```powershell
grep -n "7.0|dd-admin|/api/dd/" 微信小店自建上货台/web/*.js 微信小店自建上货台/app.py
```
摘录：
```
menu.js:111  /* 7.0 抖店后台管理 ... */
menu.js:115  key:'dd-admin', title:'抖店后台管理 7.0'
menu.js:116-120  /dd/overview · /dd/drafts · /dd/queue · /dd/publish · /dd/links
app.py:1402  if p.startswith('/api/dd/'):  → dd.api_get/api_post
pages7.js:1  /* 7.0 抖店后台管理 —— 概览/草稿箱/货源/上架执行/后台跳转 */
```

### A8 抖店执行脚本（浏览器自动化）
```powershell
Get-ChildItem 'C:\Users\Administrator\ego\doudian-publish-20261029-2050' -Filter *.mjs
```
摘录：42 个 .mjs（`round-01..40`、`publish-1391.mjs`、`_selftest.mjs`）；`ego\main\` 另有 60+ 个（`excel-route-1..4`、`dy-*`、`xz-*`）。

### A9 微信侧无浏览器驱动
```powershell
grep -n "ego|cdp|playwright|selenium|pyautogui|remote-debugging" 微信小店自建上货台/app.py
```
摘录：仅 `category_flat` / `category_detail` 等函数名误匹配，**无一处浏览器驱动**。

### A10 微信视频通路断裂点
```
app.py:1171   'video': col(first, '视频', '商品视频')
app.py:1300   if it.get('video'):
app.py:1301       skip += '｜视频未接（源为第三方临时链）'
app.py:1819   key = 'head_imgs' if op == 'main_pic' else ('head_videos' if op == 'video' else 'desc_info')
wxapi.py      ← 无任何视频上传方法（17 个接口全部为商品/类目/图片/物流/优惠券/订单）
```

### A11 taskcenter 未被调用
```powershell
grep -rn "taskcenter" taobao-picker 上货台
```
摘录：
```
taobao-picker\_验_第1批后端.py:7      from server.v3 import taskcenter as tc
taobao-picker\_run_stage0_report.py:7  from server.v3 import taskcenter as tc
上货台\app\                            （0 处）
server\v3\taskcenter.py                 （仅自引用）
```
