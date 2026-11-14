# AI运营中心 - 统一数据库设计

> 结论等级：**B（条件不满足）** —— 设计可落地，但 P1 前置（平台账号授权、API 权限）尚未取得，不能直接开工。
> 证据标注：`[源码确认]` / `[本机实测]` / `[官方文档]` / `[设计建议]` / `[尚未验证]`

## 结论摘要（≤200 字）

**不新建第二个库。** 7.0 全部新表落在现有 `v3.db` 里，前缀 `ops_`，`schema_version` 300 → 400。
商品主表 `v3_product` **一行不复制**，ops 表只存 `product_id` 外键与平台侧映射。
现有 28 张表里 11 张可直接复用、9 张只读引用、8 张不碰。
必须新建 13 组 ops 表，其中 4 组是「现在完全没有」的硬缺口：**平台账号、素材授权、持久化队列、人工审批**。
迁移只增不改、可回滚，禁止 `ALTER` 已有列。

---

## 一、现有库现状（实测，非推断）

`G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data\v3.db`
`[本机实测]` 111,321,088 B · `schema_version=300` · `v3_meta.schema_applied_at=2026-11-09 14:20:26` · 28 张表。

| 表 | 行数 | 现用途 | 7.0 怎么对待 |
|:--|--:|:--|:--|
| `v3_product` | 372 | 商品主表（`status`: blocked 213 / fine_screened 34 / pending 125） | **复用，不复制**。7.0 只读它的字段，所有运营数据挂在 `product_id` 上 |
| `v3_sku` | 8,450 | SKU（价格/库存/规格） | 只读引用；运营改价走 `ops_price_change` 留痕，**不直接写 `v3_sku.price`** |
| `v3_image` | 18,353 | 媒体：`role` 有 `main 5530 / detail 6276 / sku 6353 / video 194` | **视频已在库**（194 行带真实 `current_local_path`），素材中心直接以它为源，不另建媒体表主键 |
| `v3_image_edit` | 21 | 图片改版记录 | 复用思路，ops 素材版本表照它的形状扩 |
| `v3_image_ocr` | 18,156 | OCR 结果 | 只读（可给「视频/图片内容识别」当旁证） |
| `v3_store` | 1 | 店铺（`platform` 默认 `wechat`） | **扩展点**：加 `ops_account` 把「店」与「账号」分开，`v3_store` 保留不动 |
| `v3_publish` | 1 | 实际发布记录（`outcome`） | 7.0 不复用它做主表（字段太少），改由 `ops_publish_job` 承担；`v3_publish` 继续给 5.0 用 |
| `v3_publish_intent` | 854 | 微信发布意图（`status`: creating/created/failed/skipped） | 只读；7.0 的意图写 `ops_publish_job`，避免两套意图打架 |
| `v3_upload_status` | 149 | **双平台上架状态已分列**：`dy_state`(success 8 / fail 2 / none 139)、`wx_state`(success 141 / none 8) + `dy_tid`/`wx_product_id`/`*_reason` | **最值钱的现成资产**。`ops_platform_item` 以它为历史源，做「平台商品ID ↔ 本地商品」正查表 |
| `v3_task_log` | **1** | 设计完整（stage/action/status/progress/error_code/payload）但**几乎没在用** | **不能当任务队列用**。ops_task 新建，不复用它（读写者不同，混用会互相污染） |
| `v3_job` | **1** | 有 `idempotency_key UNIQUE`，设计意图正确 | 同上：设计对但空转；ops_task 沿用它的 `idempotency_key` 思路 |
| `v3_audit` | 1,581 | 写操作留痕（ts/actor/action/entity/before/after） | **复用**：7.0 所有写操作一律写它，`entity='ops_*'` |
| `v3_dl_job` / `v3_dl_task` | 3 / 26 | 下载任务（字节数/重试/断点续传） | 只读；素材「拉取远端视频」可复用这套下载器，不新建 |
| `v3_pack` / `v3_pack_member` | 4 / 399 | 引用式成员包 | 只读引用，运营不参与包 |
| `v3_keyword` / `v3_keyword_waiver` | 22,052 / 9 | 词库 | **复用给搜索商机**：做搜索词热度时以它为词源 |
| `v3_title_policy` / `v3_title_variant` | 1 / 0 | 标题策略 | 只读（视频标题文案可参照） |
| `v3_wx_img_cache` / `v3_wx_title_idx` | 11,065 / 764 | 微信图缓存/标题索引 | 只读 |
| `v3_dl_log` | 157 | 下载日志 | 只读 |
| `v3_category_map` | 0 | 类目映射 | 空表，7.0 双平台类目映射可**启用**它 |
| `v3_meta` | 2 | 键值元数据 | **这里存 schema_version**，迁移的落点 |
| `v3_trash` | 0 | 回收站 | 不碰 |
| `sqlite_sequence` | 14 | 内部 | 不碰 |

`[本机实测]` 媒体盘：`taobao-picker\data\v3\images` 共 **12,653 个文件、其中 .mp4 210 个 / 1,206 MB** —— 素材体量已经真实存在，素材中心不是从零开始。

### 1.1 三个必须写进设计前提的硬事实

1. **视频已经在库里**（`v3_image.role='video'`，194 行，`current_local_path` 是真路径，带 `content_sha256`）→ 素材中心**不做重复入库**，只做「版本层」。
2. **任务队列是真空缺**（`v3_task_log` 1 行、`v3_job` 1 行）→ 7.0 的持久化队列必须是新表，且**必须有租约与幂等**，否则两个进程同时跑会互相踩。
3. **抖店现在不是 API 上架**：`[源码确认]` `server/v3/doudian.py` 是**16 列 xlsx 导出器**（模块 docstring 明写「模板没有视频列 ⇒ 绝不加列；视频仍留在 v3_image 与本地物料包里」）→ 抖音侧的运营数据（曝光/点击/成交）**没有本地来源**，只能靠 API 或浏览器采集，`ops_metric_daily` 必须能记「数据来源」。

---

## 二、总体原则（五条，缺一条就会退化成第二套库）

1. **一个库**：全部进 `v3.db`。不建 `ops.db`、不建 `douyin.db`。理由：主人明确「一个统一商品数据库」；跨库 JOIN 在 SQLite 里要么 `ATTACH` 要么复制，两条路都会把一致性搞坏。
2. **不复制商品**：ops 表永不出现 `title` / `price` / `main_image` 的商品副本字段。要显示就地 JOIN `v3_product`。
3. **只增不改**：迁移只 `CREATE TABLE` / `CREATE INDEX` / `CREATE VIEW`，**禁止 `ALTER` 现有列、禁止 `DROP`**。回滚 = 删掉新建的 ops 表（数据可弃）。
4. **写操作必留痕**：所有 ops 写操作写 `v3_audit`（`entity='ops_xxx'`，`actor='ops-engine'` 或 `'ui'` 或角色名）。
5. **时间统一文本**：沿用现有 `TEXT` + `YYYY-MM-DD HH:MM:SS`（本地时区）。**不引入新的时间格式**，否则跨表对账会错。

## 三、新建 13 组 ops 表（DDL 草案）

> 全部 `[设计建议]`，未建库。字段类型与现有风格保持一致（`TEXT`/`INTEGER`/`REAL`，时间用 TEXT）。

### 3.1 `ops_account` —— 平台账号（**硬缺口 ①**）

现有 `v3_store` 只有 1 行且 `platform` 默认 `wechat`，**没有「谁在操作」这个概念**。

```sql
CREATE TABLE ops_account (
  account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  platform      TEXT NOT NULL,              -- douyin | wechat | videochannel
  shop_id       TEXT NOT NULL,              -- 平台侧店铺 ID；对应 v3_store.store_id 时可同值
  account_name  TEXT NOT NULL,              -- 显示名（如「晨飞-抖店A」）
  account_uid   TEXT DEFAULT '',            -- 平台账号唯一标识（openid/appid 等）
  auth_type     TEXT NOT NULL,              -- api_appsecret | browser_cookie | oauth
  credential_ref TEXT DEFAULT '',           -- ★ 只存引用：'config/accounts.json#douyin-A'，绝不存明文
  cookie_state_path TEXT DEFAULT '',        -- 浏览器自动化用的 storageState 文件路径
  status        TEXT NOT NULL DEFAULT 'active', -- active | expired | revoked
  last_check_at TEXT DEFAULT '',
  note          TEXT DEFAULT '',
  created_at    TEXT, updated_at TEXT,
  UNIQUE(platform, shop_id, account_name)
);
```
**红线**：`credential_ref` 只放「去哪读」的引用，**AppSecret / Cookie 明文一律不进库** `[设计建议]`。

### 3.2 `ops_platform_item` —— 商品↔平台商品映射

现有 `v3_upload_status.dy_tid / wx_product_id` 是**单值**，且只覆盖 149 件。多店/多账号就会不够。

```sql
CREATE TABLE ops_platform_item (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id    INTEGER NOT NULL REFERENCES v3_product(product_id),
  platform      TEXT NOT NULL,
  account_id    INTEGER NOT NULL REFERENCES ops_account(account_id),
  external_id   TEXT NOT NULL,              -- 平台商品 ID（抖店 product_id / 微信 product_id）
  external_url  TEXT DEFAULT '',
  listing_state TEXT NOT NULL DEFAULT 'unknown', -- draft|on_shelf|off_shelf|rejected|auditing
  reject_reason TEXT DEFAULT '',
  mapped_at     TEXT, updated_at TEXT,
  UNIQUE(platform, account_id, external_id)
);
CREATE INDEX idx_ops_pi_product ON ops_platform_item(product_id);
```
**迁移初始数据**：从 `v3_upload_status` 回填（`dy_state='success'` 8 条 → `dy_tid`；`wx_state='success'` 141 条 → `wx_product_id`）。**回填是只读现有表 + 插新表，不动老表** `[设计建议]`。

### 3.3 `ops_media_version` —— 素材版本层（**不改 v3_image，只挂在它下面**）

```sql
CREATE TABLE ops_media_version (
  version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id    INTEGER NOT NULL,
  base_image_id INTEGER,                    -- 关联 v3_image.image_id；纯生成物可为 NULL
  vkind         TEXT NOT NULL,              -- orig|ai_a|ai_b|ai_imgtext|dy_version|wx_version|cover|subtitle|audio
  file_path     TEXT NOT NULL,              -- 生成物真实路径（独立目录，绝覆盖原片）
  file_sha256   TEXT DEFAULT '',
  bytes         INTEGER DEFAULT 0,
  width INTEGER, height INTEGER, duration_ms INTEGER,
  fmt           TEXT DEFAULT '',            -- mp4/mov/jpg/srt/mp3...
  render_engine TEXT DEFAULT '',            -- ffmpeg|remotion|manual
  script_ref    INTEGER DEFAULT 0,          -- → ops_content.content_id（生成它的脚本）
  is_current    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT,
  UNIQUE(product_id, vkind, file_sha256)
);
CREATE INDEX idx_ops_mv_product ON ops_media_version(product_id, vkind);
```
**「原始不覆盖」的落地方式**：`vkind='orig'` 的行只由系统在发现阶段插入，`file_path` 指向原片；
**任何生成流程只允许 INSERT 新行，代码层禁止对 `vkind='orig'` 做 UPDATE `file_path`** `[设计建议]`。
（建议再加一个 CHECK 约束或触发器，但 SQLite 触发器容易和历史脚本冲突 → 先用代码层守卫 + `v3_audit` 双保险。）

### 3.4 `ops_media_right` —— 素材来源与授权（**硬缺口 ②**）

```sql
CREATE TABLE ops_media_right (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  media_version_id INTEGER NOT NULL,
  source_kind  TEXT NOT NULL,               -- self_captured|supplier_provided|platform_official|third_party
  license      TEXT DEFAULT '',             -- 授权说明（一句话）
  proof_path   TEXT DEFAULT '',             -- 授权凭证文件路径（截图/合同/链接）
  authorized   INTEGER NOT NULL DEFAULT 0,  -- 1=可商用
  expire_at    TEXT DEFAULT '',
  note         TEXT DEFAULT '',
  created_at   TEXT,
  UNIQUE(media_version_id, source_kind)
);
```
**红线落地**：`authorized=0` 的素材**禁止进入发布流程** —— 发布前置检查第一步查这张表 `[设计建议]`。

### 3.5 `ops_content` —— AI 生成内容（脚本/文案/字幕/封面参数）

```sql
CREATE TABLE ops_content (
  content_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   INTEGER NOT NULL,
  ctype        TEXT NOT NULL,               -- script|copy|title|hashtag|subtitle_text|cover_spec
  platform     TEXT DEFAULT '',             -- 空=通用；douyin|wechat
  body         TEXT NOT NULL,               -- 正文（JSON 或纯文本，按 ctype）
  model        TEXT DEFAULT '',             -- 哪个模型生成的
  prompt_hash  TEXT DEFAULT '',
  review_state TEXT NOT NULL DEFAULT 'draft', -- draft|approved|rejected
  created_at   TEXT, updated_at TEXT
);
```
**价值**：AI 产物一律进库，**不进文件**；出问题能追「哪条 prompt、哪个模型、哪一版」。

### 3.6 `ops_video_job` + `ops_publish_job` —— 视频生产与发布

```sql
CREATE TABLE ops_video_job (
  job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   INTEGER NOT NULL,
  target_kind  TEXT NOT NULL,               -- ai_a|ai_b|ai_imgtext|dy_version|wx_version
  state        TEXT NOT NULL DEFAULT 'queued', -- queued|preparing|scripting|rendering|checking|done|failed|cancelled
  progress     REAL DEFAULT NULL,           -- NULL=未知，不伪造（照 v3_task_log 的既有口径）
  input_json   TEXT DEFAULT '',             -- 用了哪些素材版本
  output_version_id INTEGER DEFAULT 0,
  error_code   TEXT DEFAULT '', error_message TEXT DEFAULT '',
  attempts     INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2,
  created_at TEXT, started_at TEXT, finished_at TEXT, updated_at TEXT
);

CREATE TABLE ops_publish_job (
  job_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   INTEGER NOT NULL,
  platform     TEXT NOT NULL,
  account_id   INTEGER NOT NULL,
  channel      TEXT NOT NULL,               -- video|livestream|activity|product
  state        TEXT NOT NULL DEFAULT 'draft',
  -- draft|media_ready|ai_checked|approved|uploading|uploaded|linking|linked|published|verify_failed|failed|cancelled
  video_version_id INTEGER DEFAULT 0,
  title        TEXT DEFAULT '', copy_text TEXT DEFAULT '',
  cover_path   TEXT DEFAULT '',
  platform_video_id TEXT DEFAULT '',
  linked_external_item TEXT DEFAULT '',     -- 关联的平台商品 ID
  require_approval INTEGER NOT NULL DEFAULT 1,
  approval_id  INTEGER DEFAULT 0,
  error_code   TEXT DEFAULT '', error_message TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT, published_at TEXT
);
```
`[设计建议]` 状态机字段 `state` 的取值就是主人列的那 9 步（素材准备→AI检查→视频生成→视频审核→账号选择→视频上传→商品关联→发布确认→发布结果核对），**一步一个 state，不许合并**。

### 3.7 `ops_task` —— 持久化任务队列（**硬缺口 ③**，最关键的基建）

```sql
CREATE TABLE ops_task (
  task_id       TEXT PRIMARY KEY,           -- uuid，外部可引用
  queue         TEXT NOT NULL DEFAULT 'ops',-- ops | video | browser | metric
  kind          TEXT NOT NULL,              -- 见下表 kind 词表
  ref_type      TEXT DEFAULT '', ref_id TEXT DEFAULT '',
  payload       TEXT DEFAULT '{}',
  state         TEXT NOT NULL DEFAULT 'pending',
  -- pending|leased|running|done|failed|dead|cancelled|paused
  priority      INTEGER NOT NULL DEFAULT 5, -- 1 高 … 9 低
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  idempotency_key TEXT UNIQUE,              -- 幂等：同 key 第二次提交直接返回原任务
  lease_owner   TEXT DEFAULT '',            -- 哪个进程拿到（pid/host）
  lease_until   TEXT DEFAULT '',            -- 租约到期时间；过期即可被别的 worker 抢 —— 治崩死锁
  run_after     TEXT DEFAULT '',            -- 最早可执行时间（退避）
  last_error    TEXT DEFAULT '',
  created_at TEXT, started_at TEXT, finished_at TEXT, updated_at TEXT
);
CREATE INDEX idx_ops_task_pick ON ops_task(state, queue, priority, run_after);
```

**租约设计要点**（`[设计建议]`，与现有 `v3_task_log` 的 `progress=NULL` 口径一致）：
- Worker 抢任务用 **单条 UPDATE 带条件**：`UPDATE ops_task SET state='leased', lease_owner=?, lease_until=? WHERE task_id=? AND state='pending'` —— 看 `changes()` 是否 1，**不靠先 SELECT 再 UPDATE**（会双抢）。
- 租约 120 秒，`lease_until` 过期自动回到 `pending`（由任意 worker 的清扫循环做，不另开服务）。
- `attempts >= max_attempts` → `dead`，**不无限重试**。
- 重启后 `leased/running` 且租约已过期 → 拾回；未过期 → 等它过期（防双跑）。

`kind` 词表（建议固定，别自由发挥）：`metric_pull` / `video_generate` / `video_render` / `publish_prepare` / `publish_upload` / `publish_link` / `publish_verify` / `advice_refresh` / `browser_flow_run`。

### 3.8 `ops_flow` + `ops_flow_version` + `ops_run` —— 自动化流程版本化

```sql
CREATE TABLE ops_flow (
  flow_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT NOT NULL UNIQUE,        -- 如 dy_video_upload_v1
  platform     TEXT NOT NULL,
  purpose      TEXT DEFAULT '',
  current_version INTEGER NOT NULL DEFAULT 0,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE ops_flow_version (
  version_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_id      INTEGER NOT NULL REFERENCES ops_flow(flow_id),
  version_no   INTEGER NOT NULL,
  spec_json    TEXT NOT NULL,               -- 步骤/选择器/超时/重试 的完整定义
  entry_url    TEXT DEFAULT '',
  account_id   INTEGER DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'draft', -- draft|testing|active|retired
  created_by   TEXT DEFAULT '',             -- 人 / ai
  approved_by  TEXT DEFAULT '',
  created_at TEXT,
  UNIQUE(flow_id, version_no)
);

CREATE TABLE ops_run (
  run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  flow_version_id INTEGER NOT NULL,
  task_id      TEXT DEFAULT '',             -- → ops_task.task_id
  state        TEXT NOT NULL DEFAULT 'running', -- running|succeeded|failed|aborted
  started_at TEXT, finished_at TEXT,
  last_ok_step INTEGER DEFAULT 0,
  error_step   INTEGER DEFAULT 0,
  screenshot_path TEXT DEFAULT '',
  dom_path     TEXT DEFAULT '',
  url_at_fail  TEXT DEFAULT '',
  console_log  TEXT DEFAULT ''
);
```
这四列（`screenshot_path` / `dom_path` / `url_at_fail` / `console_log`）就是主人要求的「异常六件套」里可入库的部分；
另两件（最后成功步骤、失败元素定位）分别落 `last_ok_step` 与 `ops_flow_version.spec_json` 的对应 step `id` `[设计建议]`。

### 3.9 `ops_approval` —— 人工审批（**硬缺口 ④**）

```sql
CREATE TABLE ops_approval (
  approval_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  ref_type     TEXT NOT NULL,               -- publish_job|price_change|commission|flow_version|batch_delete
  ref_id       TEXT NOT NULL,
  level        INTEGER NOT NULL,            -- 1 全自动 / 2 自动+异常暂停 / 3 AI准备+人批 / 4 必须人工
  state        TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected|expired
  requested_by TEXT DEFAULT '',             -- 哪个进程/角色
  decided_by   TEXT DEFAULT '',             -- 人
  decided_at   TEXT DEFAULT '',
  reason       TEXT DEFAULT '',
  created_at   TEXT,
  UNIQUE(ref_type, ref_id, level)
);
```
`[设计建议]` 与主人第十一项「自动化权限等级 L1–L4」直接对齐：**L3/L4 动作在写 `ops_publish_job`/改价前必须先有 `approved` 的 approval 行**，否则引擎拒绝执行。

### 3.10 `ops_price_change` —— 改价留痕（价格改动必须可回滚）

```sql
CREATE TABLE ops_price_change (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   INTEGER NOT NULL,
  sku_id       TEXT DEFAULT '',
  platform     TEXT DEFAULT '', account_id INTEGER DEFAULT 0,
  old_price    REAL, new_price REAL,
  reason       TEXT DEFAULT '',
  approval_id  INTEGER DEFAULT 0,
  applied_at   TEXT DEFAULT '', state TEXT DEFAULT 'proposed', -- proposed|approved|applied|rolled_back
  created_at TEXT
);
```
**红线**：主人在第十一项点名「重要商品价格修改 / 活动价格确认 / 佣金修改」不得未批准自动执行 → 这张表就是那道闸的账本。

### 3.11 `ops_metric_daily` —— 每日流量指标（双平台分表还是分列？答案是分列）

```sql
CREATE TABLE ops_metric_daily (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  day          TEXT NOT NULL,               -- YYYY-MM-DD
  platform     TEXT NOT NULL,
  account_id   INTEGER DEFAULT 0,
  product_id   INTEGER DEFAULT 0,           -- 0 = 店铺级
  external_id  TEXT DEFAULT '',
  impressions  INTEGER DEFAULT 0,
  clicks       INTEGER DEFAULT 0,
  ctr          REAL DEFAULT NULL,
  orders       INTEGER DEFAULT 0,
  gmv          REAL DEFAULT 0,
  video_views  INTEGER DEFAULT 0,
  source       TEXT NOT NULL DEFAULT 'unknown', -- api|browser|manual|unknown  ← 必填，防「编出来的数据」
  fetched_at   TEXT DEFAULT '',
  UNIQUE(day, platform, account_id, product_id)
);
```
**`source` 这一列是设计里最重要的一列**：没有它，一个月后没人能分清哪些数字是真 API 拿的、哪些是浏览器抄的、哪些是人填的 `[设计建议]`。

### 3.12 `ops_browser_error` —— 浏览器事件（也可直接并进 `ops_run`，但单独成表便于查）

```sql
CREATE TABLE ops_browser_error (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER DEFAULT 0,
  platform     TEXT DEFAULT '',
  error_kind   TEXT NOT NULL,               -- selector_missing|timeout|login_expired|captcha|risk_control|network|unknown
  message      TEXT DEFAULT '',
  screenshot_path TEXT DEFAULT '', dom_path TEXT DEFAULT '',
  occurrence   INTEGER DEFAULT 1,
  first_seen   TEXT, last_seen TEXT,
  UNIQUE(error_kind, message, platform)     -- 同错误归并计数，避免日志爆表
);
```
`risk_control` / `captcha` 两类**必须只登记、不自动绕过**（主人红线：AI 不能自动绕过验证码/风控）。

### 3.13 `ops_advice` —— 运营诊断建议（曝光优化/搜索商机的落点）

```sql
CREATE TABLE ops_advice (
  advice_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   INTEGER NOT NULL,
  platform     TEXT DEFAULT '',
  kind         TEXT NOT NULL,               -- title|main_image|price|video|keyword|activity
  basis_json   TEXT DEFAULT '',             -- 依据（来自哪些指标行）
  suggestion   TEXT NOT NULL,
  expected_gain TEXT DEFAULT '',            -- 定性描述，不写编造的数字
  state        TEXT NOT NULL DEFAULT 'open', -- open|adopted|ignored|expired
  created_at TEXT
);
```

## 四、迁移方案（`schema_version` 300 → 400）

`[设计建议]` 五步，全部可回滚，**上线前先在库副本上跑一遍**：

1. **备份**：沿用现有命名习惯 —— `v3.db.bak-ops迁移-YYYYMMDD-HHMM`（现存目录里已有 10 个同风格 `.bak-*`，说明这套习惯是被接受的）。
2. **建表脚本**：`taobao-picker\server\v3\migrate_ops.py`，只含 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`。**幂等**：跑两遍不报错、不重复建。
3. **回填**（只读老表 → 写新表）：`v3_upload_status` → `ops_platform_item`；`v3_image(role='video')` → `ops_media_version(vkind='orig')`。
4. **版本号**：`UPDATE v3_meta SET value='400' WHERE key='schema_version'`，并新增 `schema_applied_at`。
5. **验收**：`PRAGMA integrity_check` + 28 张老表行数逐一比对（**必须与迁移前完全一致**）+ 新表 13 组存在。

**回滚**：`DROP TABLE ops_*`（13 条）+ 把 `schema_version` 改回 300。因为 ops 表不涉及老表列，回滚**不会丢任何原有业务数据** `[设计建议]`。

## 五、必须避免的三个坑（都是本项目已踩过的形状）

| 坑 | 本项目的现成证据 | 7.0 怎么防 |
|:--|:--|:--|
| **设计完整但没人写** | `v3_task_log` 有全套字段，只有 1 行；`v3_job` 有 `idempotency_key UNIQUE`，只有 1 行 `[本机实测]` | ops_task **第一版就必须有真实读写者**；建表当天跑一次「入队→租约→完成」闭环并把 3 行数据留在表里作为存活证据 |
| **两套意图打架** | `v3_publish_intent` 854 行 + `v3_publish` 1 行，语义重叠 `[本机实测]` | 7.0 **不写 `v3_publish_intent`**，只写 `ops_publish_job`；老表保持原样给 5.0 用 |
| **静默降级被当成正常** | 6.0 挂载失败只写日志不崩（`main_window.py:249-251` 的 `except` 分支）`[源码确认]` | ops 引擎启动失败**必须**写 `ops_browser_error` 或 `v3_audit` + 界面显示红点，不许只进 log |

## 六、索引与性能

- 3 个高频查询必须建索引：`ops_task(state,queue,priority,run_after)`、`ops_platform_item(product_id)`、`ops_media_version(product_id,vkind)`。
- `ops_metric_daily` 已有 `UNIQUE` 天然当索引；按 `day` 查询再加 `CREATE INDEX idx_ops_metric_day ON ops_metric_daily(day, platform)`。
- **SQLite 单写者**：ops 引擎独立进程 + 桌面进程同时写会锁库。落地方式：**所有 ops 写操作只经 ops 引擎的单一写入口**，桌面只读（沿用 9186 诊断层的「每请求起干净子进程只读」思路）`[设计建议]`。若必须双写，开 `WAL`（现有 `products.db-wal` 说明本机已在用 WAL 模式）并保证单写短事务。

## 八、与其它分报告的表名对齐（勘误，避免两份报告各叫各的）

《AI运营中心-视频生产与发布设计》独立提出了三张表。为避免两套命名并行，**以本文件为准做映射**（`[设计建议]`）：

| 该报告的表名 | 本文件对应 | 处理 |
|:--|:--|:--|
| `v3_video_asset`（生成物版本，`UNIQUE(product_id,asset_kind,version)`） | **`ops_media_version`** | 合并。它多出的 `version` 序号字段**采纳**，加为 `ops_media_version.version_no INTEGER DEFAULT 0` |
| `v3_video_publish`（13 字段发布记录） | **`ops_publish_job`** | 合并。其防重复发布的 `UNIQUE(platform, shop_id, account_id, asset_id, attempt_no)` **采纳**，落为 `ops_publish_job` 上的唯一索引 |
| `v3_media_probe`（补 duration/codec/fps/faststart，旁挂不改 `v3_image`） | **`ops_media_probe`**（本文件新增，见下） | **单独保留**——理由成立：`v3_image` 没有时长/编码字段，而探测结果属于「运营侧派生数据」，不该回写商品主表 |

```sql
CREATE TABLE ops_media_probe (
  media_version_id INTEGER PRIMARY KEY,   -- 或 image_id，二选一，按写入者定
  image_id      INTEGER DEFAULT 0,
  duration_ms   INTEGER DEFAULT 0,
  codec         TEXT DEFAULT '',          -- hevc/h264
  fps           REAL DEFAULT NULL,
  width INTEGER, height INTEGER,
  faststart     INTEGER DEFAULT 0,
  probed_at     TEXT DEFAULT '',
  tool          TEXT DEFAULT 'ffprobe'
);
```

**统一后的口径**：所有 7.0 新表前缀一律 `ops_`；`v3_` 前缀只属于 1–6.0 既有链路，**新增表不再用 `v3_`**。
（原因：`v3_` 在本项目里已被当作「既有生产表」的标识，混用会让后人分不清哪些是原装、哪些是 7.0 加的。）

## 九、与主人的三个硬要求逐条对照

| 主人要求 | 本设计怎么满足 | 状态 |
|:--|:--|:--|
| 一个统一商品数据库 | 全部进 `v3.db`，商品主表不复制 | `[设计建议]` 未建库 |
| 不复制第二套商品数据库 | ops 表只有 `product_id` 外键，无商品副本字段 | 设计已守 |
| 新模块尽可能复用现有项目 | 复用 `v3_image`(含 194 条视频)、`v3_upload_status`(双平台状态)、`v3_audit`、`v3_dl_*`(下载器)、`v3_keyword`(搜索词)、`v3_category_map`(空表可用) | 已列清单 |

## 八、尚未验证 / 未做的事（如实标）

- **未建库**：本设计未写入 `v3.db`，未跑迁移脚本。`[尚未验证]`
- **未在库副本上试跑**：迁移与回填的幂等性只在设计层论证，未实跑。`[尚未验证]`
- **平台侧真实字段未定**：`ops_platform_item.external_id` / `listing_state` 的取值集合依赖抖店与微信真实返回码，S3 核查结果回来前不锁定。`[尚未验证]`
- **佣金与广告预算**：主人点名「佣金修改 / 广告预算增加」须批准，但抖店侧是否有对应 API 未核实 → 本设计只留 `ops_approval` 的 `ref_type` 占位，不建具体表。`[尚未验证]`

---

# 第二轮修订（2026-11-09 · 双库定案）

> 与上文的冲突点：上文把运营表设计成 `ops_*` 放进 `v3.db`。**修订为独立 `ops.db`**。
> 详细 DDL 与理由见 [`AI运营中心-第二轮整合补充审查.md`](AI运营中心-第二轮整合补充审查.md) §3/§11。

## 1 两库职责（不得越界）

| 库 | 职责 | 写权限 |
|:--|:--|:--|
| `v3.db` | **唯一商品身份**：product / sku / image(含 video) / store / publish_intent | 1—6.0 只；D 层 **只读** |
| `ops.db` | 运营过程：任务/事件/运行/平台映射/账号/能力/指标/内容版本/发布/审批 | 仅 D 层 |

`ops.db` **不得**出现商品主表（无 `product`/`sku`/`image` 主数据），只存 `product_id` / `image_id` 引用。

## 2 新增/修订的表

- 新增 `ops_platform_listing`：`UNIQUE(platform, account_id, product_id)`；`UNIQUE(platform, account_id, external_product_id) WHERE external_product_id IS NOT NULL`
- 新增 `ops_platform_account`：区分 `dy-shop` / `douyin-creator` / `wx-shop` / `wxch-creator` 四类身份
- 新增 `ops_capability`（平台能力注册表，可重测可切换通道）
- 新增 `ops_metric`（带 `data_status` / `coverage` / `fetched_at` / `source`）
- 修订 `ops_task`：状态由 5 态扩为 **8 态**（`pending/running/waiting_approval/succeeded/failed/retry_wait/unknown/canceled`），新增 `correlation_id`、`idempotency_key`、`external_ref`、`verify_state`
- 修订 `ops_video_asset`：新增 `license_state`（`own/authorized/unknown/forbidden`），`unknown|forbidden` **禁止入发布队列**
- 新增 `ops_content_publish`：7 段发布记录（upload→create→link→submit→review→live→verified），`stage<4` 不允许 `product_linked` 之前越级

## 3 一致性机制（跨库无事务）

1. 写 `ops_platform_listing` 前断言 `v3.v3_product` 存在，缺失即拒；
2. 建品前五步查重（映射 → 反查 → 意图 → 旧字段 → 允许）；
3. `sync_status` + `last_verified_at` + `ops.reconcile` 对账任务；
4. 不用跨库 FK；用「写入断言 + 定期对账」。

## 4 并发（实测）

老链路写 `v3.db` × 引擎写 `ops.db` 各 120 轮：**0 次 SQLITE_BUSY**，3.0 s，WAL，`integrity ok`。
两库各自独立 WAL + `busy_timeout=5000` + 短事务（`BEGIN IMMEDIATE`）。
