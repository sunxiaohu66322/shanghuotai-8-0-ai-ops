-- =====================================================================
-- 上货台 8.0 · AI 运营中心 · ops_* 表结构（生产版 2026-11-10）
-- ★ 只建在独立的 ops.db 里；绝不往商品主库 v3.db 建表（v3.db 只读 ATTACH）
-- 约定：
--   1) 所有表以 ops_ 前缀，不与 v3_* 业务表混名；不改任何 v3_* 表结构。
--   2) 时间：*_at（TEXT，本地时间 %Y-%m-%d %H:%M:%S）给人看；
--            *_epoch（INTEGER，unix 秒）给租约/退避做算术。
--   3) 流量数据一律带 data_status，绝不用 0 冒充「没采到」。
--   4) Cookie / AppSecret / Token 的明文一律不进本库（见 ops_account 注释）。
-- 版本：2（独立 ops.db 架构）
-- =====================================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------- 元信息 ----------
CREATE TABLE IF NOT EXISTS ops_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT
);

-- ---------- 账号（浏览器登录状态用「配置档引用」，不存 Cookie） ----------
CREATE TABLE IF NOT EXISTS ops_account (
    account_id            TEXT PRIMARY KEY,
    platform              TEXT NOT NULL,                  -- wx | dy
    display_name          TEXT,
    external_id           TEXT,                           -- 平台侧账号标识（wx appid 6 位前缀等）
    browser_engine        TEXT,                           -- ego | playwright | none
    browser_profile_ref   TEXT,                           -- 引擎侧配置档名/目录引用，**不是** cookie 本体
    login_status          TEXT NOT NULL DEFAULT 'unknown',-- logged_in | logged_out | expired | unknown
    last_check_at         TEXT,
    enabled               INTEGER NOT NULL DEFAULT 1,
    note                  TEXT,
    created_at            TEXT,
    updated_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_account_plat ON ops_account(platform, enabled);

-- ---------- 任务队列（持久化 + 租约 + 去重 + 退避） ----------
CREATE TABLE IF NOT EXISTS ops_task (
    task_id          TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,                       -- 任务种类（见 docs/任务种类.md）
    platform         TEXT,                                -- wx | dy | none
    product_id       INTEGER,
    dedupe_key       TEXT,                                -- 去重键：同键在 queued/running 只允许一条
    payload          TEXT,                                -- JSON
    state            TEXT NOT NULL DEFAULT 'queued',      -- queued|running|done|failed|cancelled
    priority         INTEGER NOT NULL DEFAULT 100,        -- 小 = 先跑
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    lease_owner      TEXT,
    lease_epoch      INTEGER,                             -- 租约到期时刻（unix 秒）
    next_run_epoch   INTEGER,                             -- 退避后的可执行时刻
    last_error       TEXT,
    result           TEXT,
    created_at       TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_task_state   ON ops_task(state, next_run_epoch, priority);
CREATE INDEX IF NOT EXISTS ix_ops_task_lease   ON ops_task(state, lease_epoch);
CREATE INDEX IF NOT EXISTS ix_ops_task_product ON ops_task(product_id, kind);
-- 去重：同一个 dedupe_key 在「排队中/运行中」只能存在一条
CREATE UNIQUE INDEX IF NOT EXISTS ux_ops_task_dedupe
    ON ops_task(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND state IN ('queued','running');

-- ---------- 任务状态事件（状态转移的真实证据） ----------
CREATE TABLE IF NOT EXISTS ops_task_event (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    actor       TEXT,                                     -- engine | worker | reaper | api | init
    detail      TEXT,
    at_epoch    INTEGER,
    at_text     TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_task_event_task ON ops_task_event(task_id, event_id);

-- ---------- 每次执行（一个 task 可多次 run） ----------
CREATE TABLE IF NOT EXISTS ops_run (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    worker      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    outcome     TEXT,                                     -- done | failed | lease_lost | killed
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_run_task ON ops_run(task_id);

-- ---------- 视频素材（原始 194 条 + 后续生成件，全部独立存放） ----------
CREATE TABLE IF NOT EXISTS ops_video_asset (
    asset_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER,
    image_id     INTEGER,                                 -- 对应 v3_image.image_id（原始素材）
    origin_kind  TEXT NOT NULL DEFAULT 'original',        -- original | generated
    file_path    TEXT NOT NULL UNIQUE,
    file_bytes   INTEGER,
    sha256       TEXT,
    duration_s   REAL,
    width        INTEGER,
    height       INTEGER,
    vcodec       TEXT,
    fps          REAL,
    data_status  TEXT NOT NULL DEFAULT 'not_collected',    -- measured|not_collected|fetch_failed|page_absent|api_unauthorized
    source       TEXT,                                     -- db_scan | ffprobe | manual
    coverage     TEXT,
    fetched_at   TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_video_asset_pid ON ops_video_asset(product_id);
CREATE INDEX IF NOT EXISTS ix_ops_video_asset_sha ON ops_video_asset(sha256);

-- ---------- 视频版本（同一素材的多平台/多版本输出） ----------
CREATE TABLE IF NOT EXISTS ops_video_version (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    INTEGER,
    variant     TEXT,                                     -- A | B | image2video | dy | wx
    platform    TEXT,                                     -- dy | wx | none
    file_path   TEXT,
    sha256      TEXT,
    script      TEXT,
    caption     TEXT,
    cover_path  TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',             -- draft|ready|approved|published|rejected
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_video_ver_asset ON ops_video_version(asset_id, variant);

-- ---------- 每日流量指标（没有数据 ≠ 0） ----------
CREATE TABLE IF NOT EXISTS ops_metric_daily (
    platform     TEXT NOT NULL,
    product_id   INTEGER NOT NULL,
    day          TEXT NOT NULL,                            -- YYYY-MM-DD
    metric       TEXT NOT NULL,                            -- exposure|click|cart|pay_amount|pay_orders
    value        REAL,
    data_status  TEXT NOT NULL,                            -- measured|zero_confirmed|api_unauthorized|page_absent|fetch_failed|not_collected
    source       TEXT,                                     -- wx_compass | dy_page | manual
    coverage     TEXT,                                     -- 例：单商品单日 / 全店
    fetched_at   TEXT,
    PRIMARY KEY (platform, product_id, day, metric)
);
CREATE INDEX IF NOT EXISTS ix_ops_metric_day ON ops_metric_daily(day, platform);

-- ---------- 自动化流程版本（固定脚本，可回滚） ----------
CREATE TABLE IF NOT EXISTS ops_flow_version (
    flow_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    platform    TEXT,
    script_path TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',             -- draft|active|retired
    created_at  TEXT,
    updated_at  TEXT
);

-- ---------- 浏览器错误事件（失败现场） ----------
CREATE TABLE IF NOT EXISTS ops_error_event (
    error_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT,
    kind           TEXT,
    page_url       TEXT,
    screenshot_path TEXT,
    dom_path       TEXT,
    console_log    TEXT,
    last_ok_step   TEXT,
    locator        TEXT,
    detail         TEXT,
    created_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_error_task ON ops_error_event(task_id);

-- ---------- 人工审批 ----------
CREATE TABLE IF NOT EXISTS ops_approval (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                             -- price|video_publish|commission|batch_delete
    ref_id      TEXT,
    decision    TEXT NOT NULL,                             -- approved | rejected
    decided_by  TEXT,
    note        TEXT,
    created_at  TEXT
);

-- ---------- 单实例锁（进程级互斥，防止双开引擎抢任务） ----------
CREATE TABLE IF NOT EXISTS ops_lock (
    lock_key   TEXT PRIMARY KEY,
    owner      TEXT,
    acquired_at TEXT,
    expires_epoch INTEGER
);

-- =====================================================================
-- 模块1 · 双平台真实商品中心（2026-11-10，schema v3）
-- ★ 平台真实商品数据，与 v3.db 本地商品严格分开；不拿本地数冒充平台数
--   ops_platform_shop    每店权威总数/状态统计/来源/成败
--   ops_platform_product 逐商品明细（含 ID 匹配、来源、更新时间、read_ok）
--   ops_platform_fetch   每次读取的成败记录（失败原文留痕，不用 0 冒充）
-- =====================================================================
CREATE TABLE IF NOT EXISTS ops_platform_product (
    shop_id           TEXT NOT NULL,
    platform          TEXT NOT NULL,
    shop_name         TEXT,
    product_id        TEXT NOT NULL,
    title             TEXT,
    status_code       TEXT,
    status_text       TEXT,
    price_min         REAL,
    price_max         REAL,
    stock_total       INTEGER,
    sku_count         INTEGER,
    head_img          TEXT,
    source            TEXT,
    source_kind       TEXT NOT NULL,
    source_time       TEXT,
    fetched_at        TEXT,
    read_ok           INTEGER DEFAULT 1,
    local_product_id  INTEGER,
    match_state       TEXT,
    coverage          TEXT,
    note              TEXT,
    PRIMARY KEY (shop_id, source_kind, product_id)
);
CREATE INDEX IF NOT EXISTS ix_ops_pp_shop ON ops_platform_product(shop_id, platform);
CREATE INDEX IF NOT EXISTS ix_ops_pp_status ON ops_platform_product(shop_id, status_code);
CREATE INDEX IF NOT EXISTS ix_ops_pp_match ON ops_platform_product(match_state);

CREATE TABLE IF NOT EXISTS ops_platform_shop (
    shop_id       TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    shop_name     TEXT,
    source_kind   TEXT,
    total         INTEGER,
    by_status     TEXT,
    status_text   TEXT,
    detail_rows   INTEGER,
    coverage      TEXT,
    read_ok       INTEGER DEFAULT 1,
    fetched_at    TEXT,
    elapsed_ms    INTEGER,
    error         TEXT,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS ops_platform_fetch (
    fetch_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id     TEXT,
    source      TEXT,
    method      TEXT,
    http_ok     INTEGER,
    read_ok     INTEGER,
    rows        INTEGER,
    elapsed_ms  INTEGER,
    error       TEXT,
    fetched_at  TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_pf_at ON ops_platform_fetch(fetched_at);
-- =====================================================================
-- ★ 模块2 · 真实业务运营 + 操作反馈（2026-11-10）
--   ops_operation        每个按钮一条 running→done/failed，主人能看见操作后的反馈
--   ops_campaign         限时限量购 / 活动报名资料草稿（need_human，绝不真报名）
--   ops_content_publish  商品详情视频 / 内容视频发布准备草稿（绝不自动发）
--   与 business.DDL 一致（CREATE IF NOT EXISTS，重复执行安全）
-- =====================================================================
CREATE TABLE IF NOT EXISTS ops_operation (
    op_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    module       TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    actor        TEXT,
    state        TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    elapsed_ms   INTEGER,
    result       TEXT,
    error        TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_op_at ON ops_operation(op_id DESC);

CREATE TABLE IF NOT EXISTS ops_campaign (
    campaign_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    title          TEXT,
    source_rule_id TEXT,
    source_url     TEXT,
    source_type    TEXT,
    platform       TEXT,
    shop_id        TEXT,
    item_count     INTEGER DEFAULT 0,
    cost_yuan      REAL,
    price_yuan     REAL,
    margin         REAL,
    state          TEXT DEFAULT 'draft',
    need_human     INTEGER DEFAULT 1,
    created_at     TEXT,
    updated_at     TEXT,
    note           TEXT
);

CREATE TABLE IF NOT EXISTS ops_content_publish (
    pub_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL,
    platform     TEXT,
    shop_id      TEXT,
    product_id   INTEGER,
    image_id     INTEGER,
    title        TEXT,
    file_path    TEXT,
    state        TEXT DEFAULT 'prepared',
    need_human   INTEGER DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_cp_type ON ops_content_publish(content_type, state);
-- 版本：4（模块2 真实业务运营 + 操作反馈）

-- =====================================================================
-- ★ 模块3 · 每日真实运营闭环（2026-11-11）
--   ops_patrol_run   一次真巡店（登录态/步数/成败/搜索任务/耗时）
--   ops_patrol_step  每一步真实操作：操作前后URL / 点击 / 回读 / 截图 / 错误
--   ops_daily_task   今日运营任务（A 自动 / B 人工确认 / C 人工操作）
--   ops_daily_report 经营日报（白话，流量未取得如实标，不编造）
--   与 browser_ops.DDL 一致（CREATE IF NOT EXISTS，重跑安全）
-- =====================================================================
CREATE TABLE IF NOT EXISTS ops_patrol_run (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    shop          TEXT,
    login_state   TEXT,
    started_at    TEXT,
    finished_at   TEXT,
    elapsed_ms    INTEGER,
    total_steps   INTEGER DEFAULT 0,
    ok_steps      INTEGER DEFAULT 0,
    fail_steps    INTEGER DEFAULT 0,
    search_ok     INTEGER DEFAULT 0,
    result_json   TEXT,
    state         TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_pr_at ON ops_patrol_run(run_id DESC);

CREATE TABLE IF NOT EXISTS ops_patrol_step (
    step_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    seq           INTEGER,
    key           TEXT,
    label         TEXT,
    platform      TEXT,
    shop          TEXT,
    method        TEXT,
    before_url    TEXT,
    after_url     TEXT,
    body_len      INTEGER,
    nums          TEXT,
    click_ok      INTEGER,
    readback_ok   INTEGER,
    screenshot    TEXT,
    elapsed_ms    INTEGER,
    error         TEXT,
    at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_ps_run ON ops_patrol_step(run_id, seq);

CREATE TABLE IF NOT EXISTS ops_daily_task (
    task_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL,
    platform      TEXT,
    shop          TEXT,
    product_id    TEXT,
    title         TEXT,
    kind          TEXT,
    source_step   TEXT,
    evidence      TEXT,
    cls           TEXT,
    state         TEXT DEFAULT 'open',
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_dt_day ON ops_daily_task(day, platform);

CREATE TABLE IF NOT EXISTS ops_daily_report (
    report_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    day           TEXT NOT NULL UNIQUE,
    platform      TEXT,
    shop          TEXT,
    body          TEXT,
    patrol_run_id INTEGER,
    auto_done     INTEGER DEFAULT 0,
    need_human    INTEGER DEFAULT 0,
    manual_only   INTEGER DEFAULT 0,
    created_at    TEXT
);
-- 版本：5（模块3 每日真实运营闭环）

-- =====================================================================
-- ★ P0.3.1 · 采集真实性 / 快照新鲜度（2026-11-12）
--   ops_sync_state  每店一行：数据来源 / 起止时间 / 快照文件时间 / 最后成功采集 /
--                   平台总数 / 明细数 / 覆盖率及依据 / 新鲜度 / 失败原因
--   与 sync_freshness.DDL 一致（CREATE IF NOT EXISTS，重跑安全）
--   微信=完整分页后才有资格算覆盖率；抖店历史快照按文件时间判过期，不冒充最新
-- =====================================================================
CREATE TABLE IF NOT EXISTS ops_sync_state (
    shop_id               TEXT PRIMARY KEY,
    platform              TEXT NOT NULL,
    shop_name             TEXT,
    source                TEXT,
    source_kind           TEXT,
    capture_started_at    TEXT,
    capture_finished_at   TEXT,
    snapshot_file_mtime   TEXT,
    last_success_at       TEXT,
    total_count           INTEGER,
    detail_rows           INTEGER,
    coverage              REAL,
    coverage_basis        TEXT,
    read_ok               INTEGER DEFAULT 0,
    data_status           TEXT,
    freshness_text        TEXT,
    age_hours             REAL,
    stale_after_hours     REAL,
    error                 TEXT,
    note                  TEXT,
    updated_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_ops_sync_at ON ops_sync_state(updated_at);
-- 版本：6（P0.3.1 采集真实性）
