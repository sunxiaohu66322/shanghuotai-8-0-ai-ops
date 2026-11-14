# AI 运营中心 · 第二轮整合补充审查（8.0）

> 版本：R2 · 2026-11-09
> 依据：主人第二轮审查指令 + 九份首轮调查报告 + **本机源码真读**（不是凭压缩包推断）
> 标注口径：`[源码确认]` 附 `文件:行号` ／ `[本机实测]` 附命令与输出 ／ `[设计建议]` 未被验证 ／ `[尚未验证]`
> **本轮边界**：只做审计与设计，**未改生产源码、未改生产库、未上架、未发布**。

---

## 0. 三句话结论（先说最要紧的）

1. **命名改对了**：C 层 `dd.py` 的 docstring 写的就是「7.0 抖店后台管理」，7.0 已被占用。新中心定名 **8.0**，页签 `8.0 · AI 运营中心`。
2. **双库是对的，而且有实测支撑**：跨库 `ATTACH` 按 `product_id` 读商品 **0.009 ms/次**，比同库 **0.011 ms** 还略快；而 `v3.db`（111 MB）一次完整性检查 **0.35 s（热）/ 39.9 s（冷）**，`ops.db`（147 KB）**0.00 s**。运营表放 `v3.db` 只会让每次运营自检都拖着 111 MB 走路。
3. **三个模块的关系比主人担心的更清楚**：5.0（`bridge.py`）已有完整建品协调能力（预览/多店/查重/意图/图缓存），6.0（`app.py`）已有 `X-Shop-Id` 头机制，7.0（`dd.py`）确实有一处**不可达路由**和一处**草稿/直发口径冲突**。缺口不是"没有能力"，是**没有统一入口和唯一状态真源**。

---

## 1. 总体架构（A / B / C / D 四层）

```
┌─ A 层 · 桌面上货台 PyQt6 ─────────────────────────────────────────┐
│  1…6.0 原页签（一行不动）  +  【8.0 · AI 运营中心】只读页签        │
│  只通过 HTTP 访问 D 层；绝不 import D 层代码                        │
└───────────────┬───────────────────────────────────────────────────┘
                │ GET 只读 API（127.0.0.1:9192）
┌───────────────▼───────────────────────────────────────────────────┐
│ D 层 · 8.0 运营引擎（独立进程）                                     │
│  ops_engine  = HTTP 路由 + OpsCoordinator + ops_task 队列 + 回收器  │
│  只写 ops.db ／ 对 v3.db **只读**（mode=ro，文件级可验证）           │
└──────┬──────────────────────────┬─────────────────────────────────┘
       │ 读（ro）                  │ 调（白名单，不直连平台）
┌──────▼──────────┐      ┌────────▼──────────────────────────────────┐
│ v3.db 商品主库   │      │ 适配器层                                   │
│ 372 商品/8450SKU │      │  wx_adapter   → 9191（必须带 X-Shop-Id）   │
│ 18353 图/194 视频│      │  dy_adapter   → dd.py / publish.py（eg息） │
│ **唯一商品身份**  │      │  media        → ffprobe/ffmpeg（只读原件） │
└─────────────────┘      │  browser_exec → EGO 配置档（账号隔离）     │
                          └───────────────────────────────────────────┘
```

**四条硬规则**

| # | 规则 | 原因 |
|:--|:--|:--|
| 1 | `v3.db` 是**唯一商品身份**来源；`ops.db` 只存运营过程、映射、衍生内容 | 主人明文：不许复制第二套商品主数据 |
| 2 | D 层对 `v3.db` **只读**（`mode=ro`），文件级权限即可验证 | 「运营引擎永不改商品主库」要能被第三方核验，不能只靠自觉 |
| 3 | D 层不直接写平台，只调 A/C 已有链路或 EGO 固定脚本 | 不重写 1—6.0，不造第二套抖店后台 |
| 4 | 任何写操作先过 `risk_level` + 审批闸门 | 价/佣金/正式发布不可自动执行 |

---

## 2. 三个现有模块的关系核实（逐条带行号）

### 2.1 原上货台 5.0 微信建品 —— 已有完整协调能力 `[源码确认]`

`上货台\app\bridge.py`（5,700 行 / 265,607 B）：

| 函数 | 行号 | 实际职责 |
|:--|:--|:--|
| `v3_wx_status()` | 5179 | 建品前置状态（店铺/凭据/额度） |
| `v3_wx_preview(pids, template_id=10001773856919, price_markup=1.25, …)` | 5189 | **试算**：把 v3 商品映射成微信 payload，不落地 |
| `v3_wx_shops()` | 5283 | 多店铺清单 |
| `v3_wx_shop_add(appid, secret, name)` | 5307 | 加店（凭据写 `shops.json`，不进 v3.db） |
| `v3_wx_publish(pids, …)` | 5358 | 正式建品：查重 → 意图 → 上传图 → 建品 → 回写 |
| 注释 | 5413 | 「③ 图片上传走 `v3_wx_img_cache`，已传的 0 次网络」 |

**结论**：5.0 已经是「正式建品协调入口」的事实实现 —— 有预览（可预演）、有查重、有 `v3_publish_intent` 意图记录、有 `v3_wx_img_cache`（11,065 行）图去重。**8.0 应当调用它，不另写建品**。

### 2.2 微信小店 6.0 —— 已有 `X-Shop-Id`，不需要新造多店机制 `[源码确认]`

`微信小店自建上货台\app.py`（2,176 行）：

| 事实 | 行号 | 说明 |
|:--|:--|:--|
| `set_shop_id(self.headers.get('X-Shop-Id'))` | 1359、1388 | **请求头已生效**，GET/POST 都设 |
| 注释「店号来自请求头 X-Shop-Id（thread-local）；不传时退回 shops.json 的 current」 | 282 | 兼容老链接，主路径已是请求头 |
| `if p == '/api/product/add':` | 1653 → `s.add_product(pl)` | 6.0 **自己的建品入口存在** |
| 内部另一处 `j = s.add_product(payload)` | 1296 | 供 6.0 内部流程复用 |

`web\pages6.js`：

- 423：`U.post('/api/product/add', {payload: it.payload}, it.shop)` —— **前端已经按店铺传参**
- 431 注释：抖店「不传微信图、不调 `product/add`，只把这件货落成 16 列 xlsx 入队」—— 双平台分流已存在

**结论**：`X-Shop-Id` 服务端已支持，8.0 要补的是**客户端一律显式传**（`wx_adapter` 强制带 header，不依赖 `shops.json current`），并且**建品只走 5.0**、6.0 的 `product/add` 降级为「单件手工补建」并加同源查重。

### 2.3 抖店 7.0 —— 两处真缺陷 `[源码确认]`

| 缺陷 | 证据 | 影响 |
|:--|:--|:--|
| **`/api/dd/queue` 重复路由** | `dd.py:221` 返回货源包；`dd.py:231` 返回 `queue()`；**231 永远不可达** | 前端 `pages7.js:138` 调的正是 `?q=&limit=`（期望货源包）→ 现在能用，但 `dd_queue.jsonl` 那条永远读不到；将来谁想读任务队列会误以为空 |
| **草稿/直发口径冲突** | `pages7.js:30`「跑完落草稿箱，不自动点发布」 vs `dd.py:139-141`「`excel-route-4.mjs`…`goTarget=sale` 直接落『售卖中』，绝不进草稿箱」 | **同一功能两套说法**。实际以脚本为准 = **直发**。UI 在撒谎，运营按 UI 理解会出事故 |
| 快照新旧无排序 | `dd.py:63 scan_snapshots()` 只收集 `files[]`，没有 mtime 排序 | 历史快照当实时数据（详见 §7.2） |
| 任务态在内存 | `publish.py:37 _JOB = {...}`（进程内 dict）+ `publish-log.jsonl` 仅日志 | 进程重启 = 执行状态丢失，无法对账（详见 §7.4） |

---

## 3. 统一商品身份与平台映射：`ops_platform_listing`

### 3.1 为什么现有 `v3_upload_status` 不够 `[源码确认]`

`v3_upload_status` 的列是「一商品一平台一状态」：`product_id`（唯一）、`dy_state/dy_tid`、`wx_state/wx_product_id`。
**同平台第二家店无处可放**（两店都开微店时，`wx_product_id` 只能存一个）。
→ 它继续作为**旧页面兼容快照**，不再作为多店状态真源。

### 3.2 新表 DDL `[设计建议]`

```sql
CREATE TABLE ops_platform_listing (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          INTEGER NOT NULL,            -- v3_product.product_id（跨库逻辑外键，不建 FK）
    platform            TEXT    NOT NULL,            -- wx | dy
    account_id          TEXT    NOT NULL,            -- → ops_platform_account.account_id
    external_product_id TEXT,                        -- 平台商品 ID（wx product_id / dy item_id）
    external_status     TEXT,                        -- 平台原始状态（上架/审核中/下架/回收站）
    sync_status         TEXT    NOT NULL DEFAULT 'unknown',
                                                     -- unknown|in_sync|drifted|missing|not_created
    source              TEXT,                        -- v3_upload_status | wx_api | dy_snapshot | manual
    last_verified_at    TEXT,                        -- 最后一次**读回平台**核对的时间
    last_error          TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX ux_opl_prod_acct ON ops_platform_listing(platform, account_id, product_id);
CREATE UNIQUE INDEX ux_opl_ext       ON ops_platform_listing(platform, account_id, external_product_id)
    WHERE external_product_id IS NOT NULL;
CREATE INDEX ix_opl_pid ON ops_platform_listing(product_id);
```

**三条不变式（写进代码断言，不靠文档）**

1. 任何 `ops_platform_listing` 行的 `product_id` **必须**在 `v3.db` 存在（写入前 `SELECT 1 FROM v3.v3_product`，缺失即拒）。
2. `external_product_id` 跨店唯一 —— 同一平台同一店，一个平台商品只能挂一个 `product_id`（防"一货两档"）。
3. `sync_status != in_sync` 的行，**不允许**进入自动改价/改库存/发布队列。

### 3.3 与 5.0 的关系（防重复建品）

建品前的**唯一查重口径**（按顺序）：

```
① ops_platform_listing(platform, account_id, product_id) 命中 → 已有映射，拒绝再建（除非 --force-rebuild 并留审批）
② 该店铺下按 external_product_id 反查 → 命中另一个 product_id → 报「撞车」，人工裁决
③ v3_publish_intent(未完成) 命中同 product_id+同店 → 复用意图，不新建
④ v3_upload_status.wx_product_id 有值 → 视为已建（旧数据兜底）
⑤ 都没有 → 允许建
```

`[设计建议]` 这五步落成 `OpsCoordinator.check_duplicate_build()`，返回 `{can_build, reason, evidence}`，**建品动作必须先拿这个结果**。

---

## 4. 账号中心：`ops_platform_account`

### 4.1 四类账号必须分开 `[设计建议]`

| 账号类型 | platform | 能做什么 | 不能假设 |
|:--|:--|:--|:--|
| 抖店店铺账号 | `dy` + `role=shop` | 商品/订单/后台操作（浏览器） | ≠ 能发短视频 |
| 抖音内容发布账号 | `douyin` + `role=creator` | 发视频/挂车（浏览器） | ≠ 抖店店主，需单独授权 |
| 微信小店账号 | `wx` + `role=shop` | 官方商品 API（appid/secret） | ≠ 视频号作者 |
| 微信视频号账号 | `wxch` + `role=creator` | 发视频号作品（**无官方发布 API**） | ≠ 小店商品权限 |

```sql
CREATE TABLE ops_platform_account (
    account_id          TEXT PRIMARY KEY,          -- 内部 ID，如 wx-shop-01 / dy-creator-main
    platform            TEXT NOT NULL,             -- dy|douyin|wx|wxch
    role                TEXT NOT NULL,             -- shop|creator
    display_name        TEXT,
    external_shop_id    TEXT,                      -- 店铺 ID / 作者 ID（可空）
    browser_engine      TEXT,                      -- ego|playwright|none
    browser_profile_ref TEXT,                      -- EGO 配置档目录名（**不是 cookie**）
    credential_ref      TEXT,                      -- 凭据引用键：dpapi:wx-shop-01 / env:WXSHOP_SECRET
    auth_status         TEXT NOT NULL DEFAULT 'unknown',  -- logged_in|logged_out|expired|unknown
    enabled             INTEGER NOT NULL DEFAULT 1,
    last_verified_at    TEXT,
    note TEXT, created_at TEXT, updated_at TEXT
);
```

### 4.2 凭据保护方案（调查结论）`[本机实测]`

| 方案 | 可用性 | 结论 |
|:--|:--|:--|
| Windows **DPAPI**（`CryptProtectData`，用户级） | 系统自带，`ctypes` 可调 | ✅ **采用**：`shops.json` 的 `secret` 改为 `enc:` 前缀密文，解密只在本进程内存 |
| **Credential Manager**（`cmdkey`/win32cred） | 系统自带 | 🟡 备选：适合人工维护少量凭据，批量读写不如 DPAPI 顺手 |
| 明文 JSON（现状 `shops.json` 378 B） | 已在用 | ❌ 现状保留兼容，但**新增账号一律写密文**；`ops.db` 只存 `credential_ref` |

**硬约束**：`ops.db` 内**任何列都不得**出现 AppSecret / Cookie / access_token 明文；日志过滤词表 `secret|token|cookie|appsecret`（`[设计建议]` 落成 `_redact()`，单元测试断言）。

### 4.3 `X-Shop-Id` 强制化

- `wx_adapter` 的每个请求**必须**带 `X-Shop-Id: <account.external_shop_id>`；
- 服务端已支持（`app.py:1359/1388`），所以**无需改 6.0**；
- 缺头时 `wx_adapter` 直接抛错，**不回退 `shops.json current`**（回退是两店串号的根源）。

---

## 5. OpsCoordinator：白名单工具接口

**原则：AI 永远不 import 任意 Python 函数；只能调白名单方法，方法内部再决定调 9191 / dd.py / EGO。**

| 方法 | 读/写 | 风险级 | 内部去向 | 说明 |
|:--|:--|:--|:--|:--|
| `get_product(product_id)` | 读 | L0 | v3.db（ro） | 商品+SKU+图+视频计数 |
| `list_products(filters)` | 读 | L0 | v3.db（ro） | 分页 |
| `get_platform_listing(product_id)` | 读 | L0 | ops.db | 平台映射与核对时间 |
| `check_account(account_id)` | 读 | L0 | 适配器 | 登态/权限/最近核对 |
| `check_duplicate_build(product_id, platform, account_id)` | 读 | L0 | 5.0 + ops.db | 五步查重（§3.3） |
| `prepare_video(product_id, variants)` | 写(本地) | L1 | media | 生成计划，不渲染 |
| `generate_video(plan_id)` | 写(本地) | L1 | ffmpeg | 只写 ops 目录，不覆盖原件 |
| `prepare_publish(content_id, platform, account_id)` | 写(队列) | L2 | 队列 | 建任务，不执行 |
| `run_workflow(workflow_id, params)` | 写(执行) | **L3** | EGO 固定脚本 | 需审批（正式发布类） |
| `check_publish_result(task_id)` | 读 | L0 | 平台读回 | 超时后必须先跑这个 |
| `get_daily_metrics(platform, day, product_ids)` | 读 | L0 | 适配器 | 未采集返回 `data_status` |
| `diagnose_product(product_id)` | 读 | L0 | 本地规则 | 曝光/点击/标题/图完整度 |
| `request_approval(kind, ref, note)` | 写 | L1 | ops.db | 挂起等人工 |

**风险分级**（沿用主人 L1–L4）：`L0 纯读` / `L1 本地可逆写` / `L2 进队列待执行` / `L3 平台写操作（须审批）` / `L4 只能人工`。
**价格、活动价、佣金、广告预算、正式发布、批量删除、账号授权变更 = L3 起，且默认 L4。**

---

## 6. 任务状态机（8 态）

```
                 ┌──────────┐
   入队 ────────▶│ pending  │
                 └────┬─────┘
        claim(租约)    │
                 ┌────▼─────┐  超时/心跳丢 ──┐
                 │ running  │◀───────────────┘
                 └──┬──┬──┬─┘
        成功        │  │  │  需要人工
      ┌─────────────┘  │  └──────────────▶ waiting_approval ──批准──▶ pending
      ▼                │                        │ 驳回
 ┌───────────┐  可重试 │                        ▼
 │ succeeded │         ▼                    canceled
 └───────────┘   ┌───────────┐
                 │ retry_wait│──退避到点──▶ pending
                 └─────┬─────┘
              次数用尽 ▼
                 ┌───────────┐
                 │  failed   │
                 └───────────┘
   外部结果不明 ──▶ unknown ──核对成功──▶ succeeded / 确认未执行──▶ pending
```

| 字段 | 用途 |
|:--|:--|
| `correlation_id` | 一条业务流贯穿 9191 / B 层 / EGO / 日志的唯一串号 |
| `idempotency_key` | 同键**成功过**就不再执行（与"去重"不同：去重管排队中，幂等管已完成） |
| `lease_owner` / `lease_expires_epoch` | 租约；靠 OS 文件锁 + 心跳续期 |
| `external_ref` | 平台侧任务/作品 ID，用于读回核对 |
| `verify_state` | `unverified / verified_ok / verified_absent / verified_conflict` |

**「超时 ≠ 失败」硬规则**：任何平台写操作超时 → 先置 `unknown` → 调 `check_publish_result()` 读回 →
- 平台有 → `succeeded`（并记 `external_ref`）
- 平台明确无 → `pending`（允许重试）
- 读不回 → 保持 `unknown` 并挂 `waiting_approval`

`[设计建议]` 这条要写成队列层的**唯一入口函数** `resolve_unknown(task_id, evidence)`，禁止业务代码自己改 `unknown`。

---

## 7. 现有代码缺陷清单（逐条可修）

### 7.1 `dd.py` 重复路由 `[源码确认]`

```python
221: if p == '/api/dd/queue':   # 返回货源包（PB.list_packs）
231: if p == '/api/dd/queue':   # 返回 queue()  ← 永不可达
```

**修法**：拆成 `/api/dd/packs`（221 分支，保留语义）+ `/api/dd/jobs`（231 分支），`pages7.js:138` 改调 `/api/dd/packs`。
**风险**：低。老链接 `/api/dd/queue` 保留 301 式兼容（返回 packs + `deprecated:true`），避免外部脚本断。

### 7.2 快照新旧判断 `[源码确认]`

`scan_snapshots()`（`dd.py:63`）目前只 `files.append(fn)`，**没有按 mtime 排序，也没有采集时间字段**。
**修法**：返回每个快照 `{path, mtime, snapshot_at, age_s, stale}`，`stale = age_s > 6h`；总览里 `drafts` 一律附 `snapshot_at` + `stale` 标记。
**UI 规则**：`stale=true` 时数字旁必须显示「快照 · 3 小时前」，禁止当实时。

### 7.3 上架模式冲突（**最危险的一条**）`[源码确认]`

- `pages7.js:30`：告诉用户「跑完落草稿箱，不自动点发布」
- `dd.py:139-141`：注释明确 `goTarget=sale 直接落「售卖中」，绝不进草稿箱`

两者**不可能同时为真**。按脚本注释（且 `dd.py:193` 入队备注也写 `goTarget=sale 直发售卖中`）判断：**实际是直发**。

**修法（两步，先声明后执行）**
1. **脚本声明制**：每个 EGO 脚本首行加机器可读声明 `// @publish-mode: draft|submit_review|publish_live`，`dd.py` 读声明并在 UI 显示真实模式；缺声明 = `unknown` = **禁止执行**。
2. **UI 说真话**：删除「落草稿箱」文案，改为显示脚本声明的真实模式 + 一行红字「本脚本会直接上架，不进草稿箱」。
3. `publish_live` 默认关闭，需 `ops_approval` 有 `approved` 记录才允许（`[设计建议]`）。

### 7.4 任务持久化 `[源码确认]`

`publish.py:37` `_JOB` 是进程内 dict；`publish-log.jsonl` 只是日志，不能恢复。
**修法**：`publish.py` 起任务时写 `ops_task`（`kind=dy.script.run`，`correlation_id`），进程重启后由队列把 `running` 且租约过期的任务置 `unknown`（不是 failed），再读 `publish-log.jsonl` 尾部 + 平台状态判定。
**注意**：不删 `_JOB`（页面还在轮询它），改为「双写」：内存态留给页面，`ops_task` 做真源。

### 7.5 路径配置 `[源码确认]`

- `paths.py:27-52`：`TAOBAO_DIR` / `XIAOZHU_DIR` / `PIG_*` 全是**绝对路径字面量**（G: 盘、J: 盘）
- `paths.py:87-88`：`rel()` 里还有两条硬编码前缀（**仅用于显示，低危**）
- `dd.py` / `publish.py` / `app.py` / `wxserve.py`：**0 处**硬编码盘符（都走 paths/pb）→ 这块主人担心的范围比实际小

**修法**：新建 `app/pathcfg.py`：
```
优先级：环境变量 OPS_PATHS_JSON > data/paths.json > paths.py 内置默认
启动自检：每个根目录 exists() + 关键子目录；缺失 → 启动即报红并在设置页列出，不静默回退
打包：FROZEN 时以 exe 目录为基准（paths.py 已有 FROZEN 分支，扩展即可）
```

### 7.6 `menu.js` 重复 key `[本机实测]`

```
total key entries: 88   unique: 86
duplicates: {'/importItem': 2, '/exportItem': 2}
```
**修法**：`menu.js` 末尾加 20 行自检 —— 加载时统计 key，发现重复 `console.error` 并在开发页显示；CI/自检脚本里跑同一个函数，**重复即红灯**。

---

## 8. 双平台视频链路（两套业务，不许混）

**必须分开的四件事**（首轮已列，这里落成数据模型）：

| # | 业务 | 落点 | 平台能力 |
|:--|:--|:--|:--|
| A | 微信小店**商品主图视频** | 微信商品 payload `head_videos` | 官方 API（`product/update` 带视频字段，需实测） |
| B | 微信小店**详情视频** | 同上/详情模块 | 同上 `[尚未验证]` |
| C | 抖音**短视频作品** | 抖音创作者侧 | 只有浏览器（无商品挂车 API） |
| D | 微信**视频号作品** | 视频号助手 | **无官方发布 API，只能人工/浏览器** |

**发布记录 7 段**（缺一段就不许叫"发布成功"）：

```
1 video_uploaded      文件已进平台素材库（返回 material_id）
2 content_created     作品已创建（草稿，返回 content_id）
3 product_linked      商品已挂到该作品（返回关联结果）
4 submitted           已提交发布
5 platform_review     平台审核中/通过
6 published_live      正式可见（有公开 URL）
7 verified            用公开 URL 或平台读回**核对成功**
```

`ops_content_publish` 每次只推进一段，`stage` 字段 + `evidence`（返回体片段/截图路径）。**上传成功 ≠ 挂车成功**，这条写成 DB 约束级检查：`product_linked` 之前的记录不允许 `stage >= 4`。

**素材闸门**：`ops_video_asset.license_state ∈ {own, authorized, unknown, forbidden}`；
`unknown/forbidden` 的素材 **禁止**进入发布队列（队列层拒绝，不只是 UI 灰掉）。

---

## 9. 平台能力注册表

```sql
CREATE TABLE ops_capability (
    platform TEXT, feature TEXT,            -- dy|douyin|wx|wxch + feature 码
    api_exists INTEGER,                     -- 官方 API 是否存在
    api_granted INTEGER,                    -- 当前账号是否已授权（0/1/NULL 未测）
    browser_possible INTEGER,
    manual_only INTEGER,
    risk_level TEXT,                        -- L0..L4
    last_verified_at TEXT,
    evidence TEXT,                          -- 文档链接 / 错误码 / 截图
    PRIMARY KEY (platform, feature)
);
```

**为什么必须建**：首轮把「曝光数据」写成"平台不给"，实测证明是**两条路径两种结论**（见 §11）。能力是**会变**的，必须可重测、可切换通道。

**初始快照（本轮实测部分）**

| platform | feature | api_exists | api_granted | browser | 证据 |
|:--|:--|:--|:--|:--|:--|
| wx | product.crud | 1 | **1** | – | `wxapi.py` 17 端点，两店在跑 |
| wx | product.data.get（曝光） | 1 | **0** | 0 | `errcode=48001 api unauthorized`（本机实测） |
| wx | compass.shop.product.data.get | 1 | **可能是 1** | 0 | `10021069 日期格式`/`886`，**不是 48001**（本机实测） |
| wx | product.video（head_videos） | 1 | `[尚未验证]` | 1 | 需真调一次 update 才知道 |
| wxch | content.publish | 0 | – | 1 | 官方目录无发布接口 |
| dy | product.crud | 0 | – | 1 | 全仓 0 处 app_key/sign |
| dy | content.publish（视频） | 1（开放平台） | 0（未申请） | 1 | 需单独建应用+用户授权 |
| dy | product.link（挂车） | 0 | – | 1 | 官方锚点只有小程序/游戏/POI |

---

## 10. 数据同步中心

```sql
CREATE TABLE ops_metric (
    platform TEXT, account_id TEXT, product_id INTEGER, content_id TEXT,
    metric_name TEXT,            -- exposure|click|cart|pay_orders|pay_amount|play|like
    metric_value REAL,
    metric_source TEXT,          -- wx_api | dy_snapshot | manual_report
    time_window TEXT,            -- 2026-11-09 | 2026-11-02~11-08
    coverage TEXT,               -- 单商品单日 / 全店 / 单作品
    fetched_at TEXT,
    data_status TEXT NOT NULL,   -- measured|zero_confirmed|api_unauthorized|page_absent|fetch_failed|not_collected
    PRIMARY KEY (platform, account_id, product_id, content_id, metric_name, time_window)
);
```

**三条纪律**

1. **未采集不许写 0**：只有 `zero_confirmed` 才是真 0；`not_collected` 在 UI 显示「—」。
2. **失败不覆盖**：新一次采集失败时，**保留上一条 `measured`**，只更新 `fetch_failed` 的尝试记录（另一张 `ops_metric_attempt`）。
3. **快照必带时间**：抖店侧数字一律显示 `snapshot_at` + `stale`（§7.2）。

---

## 11. 双库形态决策（含实测对比）

主人问：「如果最终决定所有数据仍放在 v3.db，必须先做并发和性能对比测试，说明理由」。

**实测**（同一台机、同一份 111 MB 库副本、300 次短事务）`[本机实测]`：

| 项 | ops 表放 v3.db | ops 表放独立 ops.db | 结论 |
|:--|--:|--:|:--|
| `ops_task` 写入 | 2.084 ms/条 | **1.655 ms/条** | 独立快 ~20% |
| 老链路 `v3_task_log` 写入 | 1.441 ms/条 | – | 对照组 |
| 按 product_id 读商品 | 0.011 ms（同库） | **0.009 ms（ATTACH 跨库）** | 跨库**不慢** |
| 单次完整性检查 | 0.35 s（热）/ **39.9 s（冷）** | **0.00 s** | 差距最大项 |
| 库体积 | 111,321,088 B | 147,456 B（空）/ 208,896 B（300 任务） | 备份/搬运成本 |
| 并发（120×2 轮，两进程） | **0 error**，3.0 s，WAL，integrity ok | 同机制可复用 | 两者都能做到零锁冲突 |

**决策：独立 `ops.db`**，理由按重要性排序：

1. **权限可验证**：D 层对 `v3.db` 只读 —— 文件句柄层面就能证明「运营不碰商品主库」；同库做不到（同库必然有写权）。
2. **自检成本**：111 MB 冷检 39.9 s。运营引擎每次启动若做完整性检查，要么慢 40 s，要么不做 —— 独立库 0.00 s，随便做。
3. **爆炸半径**：运营写坏只毁 ops.db，商品主库零风险；备份策略也可分开（商品库日备、运营库可重建）。
4. **跨库代价可忽略**：0.009 ms/次 ATTACH 读，比同库还快。
5. **并发不是理由**：两者都能零冲突（实测 120×2 轮 0 error）。所以**不要**拿"同库更省事"当理由。

**代价（必须承认）**：跨库无事务原子性 → 建品/映射必须**幂等 + 可对账**（`sync_status` + `last_verified_at` + 读回核对），不能指望"一个事务搞定"。

---

## 12. P0 验收标准（修订为 15 项，全部可实测）

| # | 验收项 | 判定方法（命令级） |
|:--|:--|:--|
| 1 | v3.db 仍是唯一商品主库 | `v3_product` 行数（372）与 `ops_*` 无商品主数据；ops.db 内 `SELECT COUNT(*) FROM v3_product` 报错 |
| 2 | ops.db 能按 product_id 读商品 | `ATTACH v3.db` 后 JOIN 返回标题/SKU 数，实测 0.009 ms |
| 3 | 两库不重复生成商品主记录 | ops.db 无 `product` 主表；`ops_platform_listing` 行数 ≤ 商品×店数 |
| 4 | 两个微信店铺准确区分 | 同一 product_id 在 `wx-shop-A/B` 各一行，`external_product_id` 不同 |
| 5 | 每次 9191 调用带 X-Shop-Id | 抓 `wx_adapter` 出站请求头；缺头调用**必须抛错**（负例） |
| 6 | 同商品双平台正确映射 | 一行 dy + 一行 wx，`product_id` 相同 |
| 7 | 5.0 与 6.0 建品不重复 | 连点两次建品，第二次返回 `can_build=false`（查重第①步） |
| 8 | dd.py 路由冲突已解 | `GET /api/dd/packs` 200 且 `GET /api/dd/jobs` 200，两者 payload 不同 |
| 9 | 抖店快照显示真实采集时间 | `overview` 返回 `snapshot_at` + `stale` |
| 10 | 发布脚本区分草稿/正式 | 脚本声明 `@publish-mode`；`publish_live` 无审批 → 拒绝执行 |
| 11 | 任务可暂停/恢复/对账 | `pending→waiting_approval→pending→running→succeeded`；杀掉引擎后 `unknown→核对→终态` |
| 12 | 运营服务退出不影响 1—6.0 | kill 引擎后 9191/9186/9187 仍 200 |
| 13 | 原始视频不被覆盖 | 194 条原件 sha256 迁移前后一致 |
| 14 | 浏览器账号不串号 | 两个 EGO 配置档各跑一次读页面，返回的店铺名不同 |
| 15 | 结果可读回核实 | 每条发布记录有 `verify_state`，`verified_ok` 必须带公开 URL 或平台返回体 |

**P0' 交付（按新架构重排）**
1. `v3.db` 只读副本 + 源码哈希清单（**已完成**）
2. 独立 `dev` 工作区 + git（**已完成**）
3. `ops.db` 迁移脚本（多一个文件，不再是给 v3.db 加表）
4. 核心表：`ops_task / ops_task_event / ops_run / ops_platform_listing / ops_platform_account / ops_capability / ops_metric / ops_video_asset / ops_content_publish / ops_approval / ops_flow_version / ops_lock`
5. 队列 8 态 + 租约 + 幂等 + `correlation_id`（机制已验证，见 §13.2）
6. `ops_engine` 独立进程（9192，只监听 127.0.0.1）
7. 健康检查（含 `db_integrity` 后台算、`ops.db` 秒级）
8. 只读查询 API（商品/视频/任务/映射）
9. 8.0 只读页签（PyQt6，try 挂载，失败只写日志）
10. 失败/重试/租约过期恢复（**机制已验证**）
11. 并发测试（老链路写 v3.db × 引擎写 ops.db）→ **改测跨库**，报告见 §13.2
12. 1—6.0 回归（编译 + 端点 + 库完整性）

---

## 13. 本轮已做的实测（不是空设计）

### 13.1 视频三重核对（解开 12 vs 16 的差异）

```
v3_image(role='video') = 194 行，全部 dl_state=ok，全部有路径，路径互不重复
磁盘 mp4              = 210 个 / 1,264,549,593 B
194 条 DB 路径在盘上  = 194/194 存在（0 缺失）
sha256 逐一比对       = 194/194 一致（0 不符）
孤儿文件              = 16 个（12 个在 video\ 目录 + 2 个在 1329\ + 2 个在 1335\）
```
→ **首轮"12"的口径是只数了 `video\` 子目录**；按全树算是 **16**。**禁止自动删除**，需人工确认（其中 4 个位于商品目录，可能是别的流程产物）。

### 13.2 队列机制验证（P0 试验件，跑在开发副本上）

**最终结果：28/28 全 PASS**（`上货台7.0-P0-20261109\证据\P0-验收结果.json`）

- 迁移：27 张业务表行数 **零变化**，`integrity_check=ok`，新增 12 张 `ops_*`
- 健康：`127.0.0.1:9192`、`db_integrity=ok`、`ops_tables=12`、写范围 `ops_* only`
- 只读查询：商品 200 行、视频 194 行、`mode=ro`
- 队列四态真跑：`queued→running→done`；失败任务 `queued→running→(退避)queued→running→failed`（attempts=2）
- 去重：同 `dedupe_key` 第二次 `created=False`
- **杀引擎→重启→租约回收**：`running`（lease 剩 4 s）→ kill → 重启 → `running→queued(lease_expired)` ✅
- 业务表零变化：整套跑完 `0 diffs`
- 生产库：`integrity_check=ok`、**库内 0 张 `ops_` 表**（没被迁移过）
- 1—6.0：9191 `200` / 9186 `200` / 9187 `404（服务在，只是根路径没端点）`；**关掉引擎后三个端点仍全在**
- 8.0 页签（offscreen 真渲染）：商品 200 行、视频 194 行、任务 5 行，截图 10,045 B 落盘

**跨进程并发**（独立库副本）：老链路写 `v3.db` 120 轮 × 引擎队列 120 轮 → **0 次 SQLITE_BUSY**，3.0 s，WAL，`integrity ok`。

**踩到并修掉三个真坑（都写进设计约束）**

1. `PRAGMA quick_check` 冷缓存 **39.9 s**（热 0.3 s）→ 放请求路径会把并发全堵死 → 必须后台算 + 缓存 + 健康检查只读缓存值。
2. DB 里的"单实例锁行"被 kill 后**留 30 s 僵尸锁**，重启被拒 → 改用 **OS 字节范围文件锁**（进程死即释放）。
3. worker 线程早期版本**异常即静默死亡**（任务永远停在 queued）→ 已加 `STATS` 计数 + `try/except` + 健康检查暴露 `worker_stats`，死没死一眼可见。

> ⚠️ 该试验件按**旧架构**（`ops_*` 在 `v3.db`）实现，**不是最终 P0**；正式开发时迁移目标改指向 `ops.db`，队列/引擎/页签代码可复用约 80%（`store.py` 的只读查询、`queue.py` 状态机、`engine.py` 路由、`tab_ops.py` 全保留，只改 `config.db_path()` 与双库 ATTACH）。

### 13.3 平台能力两条路径的错误码差异（首轮结论修正）

```
POST /channels/ec/product/data/get             -> 48001 api unauthorized
POST /channels/ec/compass/shop/product/data/get -> 10021069 请求参数日期格式有误
GET  /channels/ec/compass/shop/product/data/get -> 43002 require POST method
{"ds":"20261101"} / {"ds":"20261008"}           -> 886（无 errmsg）
{"ds": 20261108}（数字）                        -> 47001 field "ds" doesn't expect uint32
```
→ **两条路径不是同一个结论**：前者**未授权**；后者**已过鉴权**（否则先报 48001），只是参数/数据问题。
→ **不能**再说"微信没有曝光数据"。正确表述：`product/data/get` 无权限；`compass` 路径鉴权通过，`886` 含义待查（非标准码），**曝光数据可用性 = 尚未验证**。

---

## 14. 八个问题的直接回答

**1. 原系统哪些能力可以直接复用？**
`bridge.py` 的 5.0 建品全家桶（`v3_wx_status/preview/shops/publish`，5,179–5,413 行）、`app.py` 的 17 个微信官方端点 + `X-Shop-Id`（1,359/1,388）、`dd.py` 的抖店概览/草稿/货源/脚本执行、`publish.py` 的脚本运行 + 日志、`paths.py` 的路径集中、`v3_image` 的 194 条视频与 18,353 张图。

**2. 哪些必须新增？**
`ops.db`（12 张表）、`OpsCoordinator` 白名单、8 态队列、`ops_platform_listing`、`ops_platform_account`、`ops_capability`、`ops_metric`、`ops_content_publish`（7 段）、8.0 只读页签、EGO 配置档隔离与账号校验、DPAPI 凭据封装。

**3. 哪些原有功能需要修复？**
`dd.py` 重复路由（221/231）、`scan_snapshots` 无时间序、草稿/直发文案冲突（`pages7.js:30` vs `dd.py:139-141`）、`publish.py:_JOB` 内存态、`paths.py` 硬编码根目录、`menu.js` 两个重复 key。

**4. 哪些接口存在重复？**
① `/api/dd/queue`（两条）；② 建品有两处：`bridge.v3_wx_publish` 与 `app.py:1653 /api/product/add`；③ 任务有三套半：`v3_task_log`（1 行，形同虚设）、`v3_publish_intent`（854）、`tasks.jsonl`/`dd_queue.jsonl`/`publish-log.jsonl`、`publish.py:_JOB`。
**处置**：建品以 5.0 为唯一入口，6.0 的 `product/add` 保留但降级为"单品手工补建"并加同源查重；任务全部收口到 `ops_task`，原 JSONL 降级为**日志**（可读、不可作真源）。

**5. 两个数据库如何保持商品身份一致？**
`v3.db` 唯一发号（`product_id`/`image_id`）；`ops.db` 只存引用；写入前断言存在；`sync_status + last_verified_at + 读回核对` 做最终一致；不建跨库 FK，靠「写入断言 + 定期对账任务（`ops.reconcile`）」兜底。

**6. 新运营引擎如何调度现有系统？**
`ops_engine`（9192）→ `OpsCoordinator`（白名单，13 个方法）→ 适配器：`wx_adapter`（9191 + X-Shop-Id）、`dy_adapter`（dd.py/publish.py）、`media`（ffmpeg/ffprobe）、`browser_exec`（EGO 配置档）。**AI 只调白名单，不 import 任意函数。**

**7. 8.0 页签怎么接？**
与 2.5/6.0 同一写法：`main_window.py` 里 `try: from .tab_ops8 import OpsCenterTab`，失败只写日志；`_pages.append((self.t_ops, "8.0 · AI 运营中心"))` 排在 6.0 之后；页面只走 HTTP，不 import 引擎；引擎未起 → 页内提示"未连接"，1—6.0 不受影响（**已实测：kill 引擎后 9191/9186/9187 仍正常**）。

**8. P0 应该怎么调整？**
见 §12：由「给 v3.db 加 12 张表」改为「独立 `ops.db` + 15 项验收」；新增 `ops_platform_listing`/`ops_platform_account`/`ops_capability`/`ops_content_publish` 四张表与 `X-Shop-Id` 强制、`publish-mode` 声明、`snapshot_at` 三项行为修正；原 P0 试验件复用引擎与队列代码，迁移目标改指向 `ops.db`。

---

## 15. 本轮未做 / 待批准

| 项 | 状态 |
|:--|:--|
| 改 `dd.py` 路由与文案 | ❌ 未做（属生产源码，等批准） |
| 加 `@publish-mode` 声明到 EGO 脚本 | ❌ 未做（需确认脚本目录可写 + 备份） |
| `ops.db` 正式迁移脚本 | 🟡 设计完成，未落地（等批准） |
| `ops_capability` 初始 20 项逐条实测 | 🟡 只做了微信 3 条（含修正），抖音/视频号仍为文档级 |
| 视频号「无发布 API」二次确认 | 🟡 首轮文档级，建议本轮后续用官方文档 + 后台实测再确认一次 |
| 素材授权闸门 | ❌ 未实现（设计已给：队列层拒绝 `unknown/forbidden`） |
