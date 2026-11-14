-- =====================================================================
-- 上货台 7.0 · AI 运营中心 · ops_* 表结构（P0）
-- 约定：
--   1) 所有表以 ops_ 前缀，不与 v3_* 业务表混名；不改任何 v3_* 表结构。
--   2) 时间：*_at（TEXT，本地时间 %Y-%m-%d %H:%M:%S）给人看；
--            *_epoch（INTEGER，unix 秒）给租约/退避做算术。
--   3) 流量数据一律带 data_status，绝不用 0 冒充「没采到」。
--   4) Cookie / AppSecret / Token 的明文一律不进本库（见 ops_account 注释）。
-- 版本：1
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
