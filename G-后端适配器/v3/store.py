# -*- coding: utf-8 -*-
"""v3 上货流水线 · 数据层

独立库 `data/v3.db`，**绝不改 products.db / 商品.db 一张表**。
schema 对齐《上货流水线-v3-本地AI执行任务书》§3 的实体契约，字段名用 snake_case，
中文只在 UI 层出现。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Iterable

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(SERVER_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "v3.db")
IMG_DIR = os.path.join(DATA_DIR, "v3", "images")
OUT_DIR = os.path.join(DATA_DIR, "v3", "out")
BACKUP_DIR = os.path.join(DATA_DIR, "v3", "backup")

SCHEMA_VERSION = "3.0"

# ---------------------------------------------------------------- 媒体目录结构（2026-10-26 主人定）

# 一件商品一个文件夹，内部按「角色」再分子文件夹，商品信息也写进这个文件夹：
#
#   data\v3\images\<商品ID>\
#       main\        主图
#       detail\      详情图
#       sku\         SKU 规格图      ← 主人点名：单独一个子文件夹
#       video\       视频
#       other\       认不出角色的
#       product.json 这件商品的全部信息（标题/价格/SKU/媒体清单…）
#       README.txt   给人看的目录说明
#
# 为什么：以前主图/详情图/SKU 图全平铺在 <商品ID>\ 里、文件名是 image_<hash>.jpg，
# 人打开只有一堆乱码文件名，分不出哪张是什么；外部要按角色取图也只能全扫一遍。
ROLE_DIRS = ("main", "detail", "sku", "video", "other")


def product_dir(pid: int | str) -> str:
    """这件商品的证据根目录（**不变**：界面的「打开商品文件夹」仍指向这里）。"""
    d = os.path.join(IMG_DIR, str(int(pid)))
    os.makedirs(d, exist_ok=True)
    return d


def role_of(role: str | None) -> str:
    """把任意角色名归一到 ROLE_DIRS 里的一档，认不出就 other（不丢文件）。"""
    r = str(role or "").strip().lower()
    return r if r in ROLE_DIRS else ("other" if r != "other" else "other")


def role_dir(pid: int | str, role: str | None) -> str:
    r"""``<商品ID>\<角色>\`` —— 所有新下载的媒体都落这里。"""
    d = os.path.join(product_dir(pid), role_of(role))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 基础

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    for d in (DATA_DIR, IMG_DIR, OUT_DIR, BACKUP_DIR):
        os.makedirs(d, exist_ok=True)


def connect(path: str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """WAL + 外键。readonly=True 时以 ro 模式打开（导出/核对用）。"""
    ensure_dirs()
    p = path or DB_PATH
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True,
                               timeout=15)
    else:
        conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def j(v: Any) -> str:
    """任意值 → JSON 文本（None → '')"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        return str(v)


def unj(s: Any, default=None):
    if s in (None, ""):
        return default
    if isinstance(s, (list, dict)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


# ---------------------------------------------------------------- schema

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v3_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS v3_product (
    product_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_platform     TEXT NOT NULL DEFAULT 'other',
    source_url          TEXT,
    source_product_id   TEXT,
    original_title      TEXT NOT NULL,          -- 不可覆盖
    category_id         TEXT,
    category_path       TEXT,
    category_confidence REAL,
    attributes          TEXT,                   -- JSON {"键":"值"}，每条带 evidence 时见 profile
    main_image_id       INTEGER,
    original_payload    TEXT,                   -- JSON 原文快照
    provenance          TEXT NOT NULL DEFAULT 'manual_import',
    source_shop_name    TEXT,
    source_store_id     TEXT,
    pack_code           TEXT,
    price_min           REAL,
    price_max           REAL,
    status              TEXT NOT NULL DEFAULT 'imported',
    profile             TEXT,                   -- JSON ProductProfile
    note                TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_v3_product_src
    ON v3_product(source_platform, source_product_id)
    WHERE source_product_id IS NOT NULL AND source_product_id <> '';
CREATE INDEX IF NOT EXISTS ix_v3_product_status ON v3_product(status);
CREATE INDEX IF NOT EXISTS ix_v3_product_pack   ON v3_product(pack_code);

CREATE TABLE IF NOT EXISTS v3_pack (
    pack_code     TEXT PRIMARY KEY,             -- 数据包码（默认=文件名去扩展名）
    source_file   TEXT,                         -- 原始文件全路径
    source_name   TEXT,
    source_dir    TEXT,
    size_kb       REAL,
    rows          INTEGER NOT NULL DEFAULT 0,   -- 文件里读到的行数
    products      INTEGER NOT NULL DEFAULT 0,   -- 入库商品数
    images        INTEGER NOT NULL DEFAULT 0,   -- 入库图片数
    skipped       INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
                    -- pending 待转换 | converting | converted 已转换
                    -- empty 没有商品 | error 转换失败
    message       TEXT,
    error         TEXT,
    created_at    TEXT,
    converted_at  TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_pack_status ON v3_pack(status);

CREATE TABLE IF NOT EXISTS v3_image (
    image_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER NOT NULL REFERENCES v3_product(product_id) ON DELETE CASCADE,
    slot_key           TEXT NOT NULL,           -- role|seq|sku|slice，唯一性靠它
    role               TEXT NOT NULL,           -- main|carousel|detail|sku|video|unknown
    sequence           INTEGER NOT NULL DEFAULT 0,
    slice_index        INTEGER,
    sku_id             TEXT,
    original_url       TEXT,
    original_local_path TEXT,
    current_local_path TEXT,
    current_remote_url TEXT,
    content_sha256     TEXT,
    parent_image_id    INTEGER,
    status             TEXT NOT NULL DEFAULT 'original',
    width              INTEGER,
    height             INTEGER,
    note               TEXT,
    created_at         TEXT,
    updated_at         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_v3_image_slot ON v3_image(product_id, slot_key);
CREATE INDEX IF NOT EXISTS ix_v3_image_pid  ON v3_image(product_id);
CREATE INDEX IF NOT EXISTS ix_v3_image_role ON v3_image(product_id, role, sequence);

-- 4.1 主图人工审核的「编辑台账」（阶段 C）。版本本体仍在 v3_image，本表只记
-- 遮罩/算法/参数/检查结果/审核状态；回滚 = DROP TABLE，v3_image 不动。
CREATE TABLE IF NOT EXISTS v3_image_edit (
    edit_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id         INTEGER NOT NULL,
    product_id       INTEGER NOT NULL,
    version_image_id INTEGER,
    pack_code        TEXT,
    mask_path        TEXT,
    mask_sha256      TEXT,
    regions_json     TEXT,
    method           TEXT,
    radius           INTEGER,
    expand_px        INTEGER DEFAULT 0,
    inspect_json     TEXT,
    quality          TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',
    note             TEXT,
    actor            TEXT,
    created_at       TEXT,
    confirmed_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_edit_image ON v3_image_edit(image_id);
CREATE INDEX IF NOT EXISTS ix_v3_edit_ver   ON v3_image_edit(version_image_id);
CREATE INDEX IF NOT EXISTS ix_v3_edit_pid   ON v3_image_edit(product_id);

CREATE TABLE IF NOT EXISTS v3_store (
    store_id        TEXT PRIMARY KEY,
    platform        TEXT NOT NULL DEFAULT 'wechat',
    display_name    TEXT,
    title_policy_id TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS v3_title_policy (
    policy_id   TEXT PRIMARY KEY,
    platform    TEXT,
    min_len     INTEGER NOT NULL DEFAULT 8,
    max_len     INTEGER NOT NULL DEFAULT 60,
    min_cjk     INTEGER NOT NULL DEFAULT 6,
    source      TEXT,                -- 该限制的出处，必须可追溯
    note        TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS v3_title_variant (
    variant_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER NOT NULL REFERENCES v3_product(product_id) ON DELETE CASCADE,
    store_id     TEXT,
    title_text   TEXT NOT NULL,
    generation_index INTEGER NOT NULL DEFAULT 1,
    strategy     TEXT NOT NULL DEFAULT 'core_first',
    locked_core  TEXT,
    status       TEXT NOT NULL DEFAULT 'candidate',   -- candidate|selected|rejected|exported
    keyword_ids  TEXT,
    model_and_prompt_version TEXT,
    validation_report TEXT,
    text_hash    TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_tv_pid   ON v3_title_variant(product_id, store_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_v3_tv_hash
    ON v3_title_variant(product_id, IFNULL(store_id,''), text_hash);

CREATE TABLE IF NOT EXISTS v3_keyword (
    keyword_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text            TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- core|attribute|scene|synonym|brand|violation
    category_scope  TEXT NOT NULL DEFAULT '',
    platform_scope  TEXT,
    requires_attribute TEXT,
    source          TEXT,
    source_date     TEXT,
    metrics         TEXT,                   -- 只有来源可靠时才写；否则 NULL
    confidence      REAL,
    status          TEXT NOT NULL DEFAULT 'active',   -- active|review|disabled
    tags            TEXT,
    updated_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_v3_kw ON v3_keyword(text, kind, category_scope);
CREATE INDEX IF NOT EXISTS ix_v3_kw_kind ON v3_keyword(kind, status);

-- 词库豁免台账：人判定「这个词其实是通用词/噪声，不是违规词」时记在这里。
-- 生效方式 = 把 v3_keyword 里同 text 的行置 status='disabled'（risk_hits 只查 <>
-- 'disabled'，所以立刻不再命中）；prev_status 留原状态，随时可一键还原。
-- 不删原始词典文件（词库主表.tsv / 打码词.txt），因为那是小猪也在用的共享文件。
CREATE TABLE IF NOT EXISTS v3_keyword_waiver (
    text        TEXT PRIMARY KEY,
    kinds       TEXT,
    prev_status TEXT,
    reason      TEXT,
    hits        INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS v3_job (
    job_id          TEXT PRIMARY KEY,
    product_id      INTEGER,
    stage           TEXT NOT NULL,
    substage         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    idempotency_key TEXT UNIQUE,
    payload         TEXT,
    result          TEXT,
    started_at      TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_job_status ON v3_job(status, stage);
CREATE INDEX IF NOT EXISTS ix_v3_job_pid    ON v3_job(product_id);

CREATE TABLE IF NOT EXISTS v3_publish (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id       INTEGER NOT NULL,
    store_id         TEXT,
    title_variant_id INTEGER,
    image_id         INTEGER,
    external_listing_id TEXT,
    published_at     TEXT,
    outcome          TEXT NOT NULL DEFAULT 'unknown',
    note             TEXT,
    created_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_pub_pid ON v3_publish(product_id);

CREATE TABLE IF NOT EXISTS v3_audit (
    audit_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT,
    actor      TEXT,
    action     TEXT NOT NULL,
    entity     TEXT,
    entity_id  TEXT,
    before     TEXT,
    after      TEXT
);
CREATE INDEX IF NOT EXISTS ix_v3_audit_entity ON v3_audit(entity, entity_id);

-- 成员包：精筛之后把「这一轮通过的那批商品」存成一个数据包（**引用式，不复制商品/图片行**）
--   · 商品本体只有一份，图片只下载/打码一次
--   · 同一个商品可以同时属于多个包（老包 + 精筛新包），互不影响
CREATE TABLE IF NOT EXISTS v3_pack_member (
    pack_code   TEXT NOT NULL,                  -- 成员包的包码（同时是 v3_pack.pack_code）
    product_id  INTEGER NOT NULL,               -- 引用 v3_product.product_id，不复制
    source_pack TEXT,                           -- 这批是从哪个源包挑出来的
    note        TEXT,
    created_at  TEXT,
    PRIMARY KEY (pack_code, product_id)
);
CREATE INDEX IF NOT EXISTS ix_v3_pack_member_pid ON v3_pack_member(product_id);
CREATE INDEX IF NOT EXISTS ix_v3_pack_member_src ON v3_pack_member(source_pack);
"""

# 老库升级用：{表: [(列名, 定义)]}，缺了就 ADD COLUMN
MIGRATIONS = {
    "v3_pack": [
        ("display_name", "TEXT"),   # 用户可改的显示名；不改 pack_code 主键（零级联）
    ],
    "v3_image": [
        ("slice_index", "INTEGER"),
        ("sku_id", "TEXT"),
        ("parent_image_id", "INTEGER"),
        ("content_sha256", "TEXT"),
        ("width", "INTEGER"),
        ("height", "INTEGER"),
        # ★ 2026-11-14：全新空库被 cards.import_folder 直接创建时，
        #   media.stats / media._rows 会读 dl_state、restore_pack 会读 download_hint，
        #   但老库靠历史迁移才有这两列 —— 隔离验收实测报
        #   OperationalError: no such column: dl_state。补进 init 的迁移表，
        #   只 ADD COLUMN，不动已有库的数据。
        ("dl_state", "TEXT"),
        ("download_hint", "TEXT"),
    ],
    "v3_product": [
        ("profile", "TEXT"),
        ("note", "TEXT"),
        ("source_shop_name", "TEXT"),
        ("source_store_id", "TEXT"),
        ("pack_code", "TEXT"),
        ("data_grade", "TEXT"),
    ],
}


# T1-01（2026-10-25）：本进程内「这个库已经迁到当前版本」的标记。
# 为什么要有它：flow.conn() 每次建连接都会调 init()，而 init() 会跑
# executescript + 逐表 PRAGMA table_info + 4 条写入 + commit。
# 实测单次 connect 5ms、init 75ms（executescript 只占 0.4ms，98% 是那次
# 写事务的 WAL 提交）。界面每次刷新都走这条链 —— 这是 F5 慢的主因之一。
# 安全性：只在「同一个库文件 + 同一个 SCHEMA_VERSION + 本进程已经完整跑过一次」
# 三个条件同时成立时才跳过；换库、升级版本、换进程都会照常跑全流程。
_INITED: dict[str, int] = {}
_INIT_LOCK = threading.Lock()
_USER_VERSION = 300          # 与 init() 末尾写入的 PRAGMA user_version 保持一致


def _db_path_of(c: sqlite3.Connection) -> str:
    """从连接上取真实库文件路径（跨进程/临时库也能正确区分）。"""
    try:
        for _seq, _name, f in c.execute("PRAGMA database_list"):
            if f:
                return str(f)
    except Exception:            # noqa: BLE001
        pass
    return ""


def retry_locked(fn, times: int = 8, wait: float = 0.25, on_retry=None):
    """执行 fn()；撞 SQLite 写锁（locked / busy）时退避重试，其它异常照常抛。

    ★ 2026-10-26 为什么加这个：导一个 142 件的包会写近 6000 次状态
      （`media._set_state` 每张图一次、`images._save_ocr` 每张一次 commit），
      这期间任何别的页面（2.5 点下载、3.0 精筛、4.0 检测）一写库就撞
      `database is locked`，用户看到的是「下载没建立起来」。
      SQLite 的写锁是库级的，退避重试是唯一不引入外部队列的解法。

    退避：0.25 / 0.5 / 1 / 2 / 2 / 2 / 2 / 2 ≈ 累计 9.75 秒，
          够跨过一次单张 OCR 的写；跨不过也不无限等，如实抛出去。
    """
    last: Exception | None = None
    for i in range(max(1, int(times))):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last = e
            if on_retry:
                try:
                    on_retry(i + 1, e)
                except Exception:      # noqa: BLE001
                    pass
            time.sleep(wait * (2 ** min(i, 3)))
    raise last if last else sqlite3.OperationalError("database is locked")


def init(conn: sqlite3.Connection | None = None) -> dict:
    """建表 + 迁移 + 写入默认店铺与标题策略。幂等。"""
    own = conn is None
    c = conn or connect()
    try:
        cur = c.execute("PRAGMA user_version").fetchone()[0]
        _p = _db_path_of(c)
        _key = "%s|%s" % (_p, SCHEMA_VERSION)
        if _p and cur >= _USER_VERSION and _INITED.get(_key):
            # 快速路径：本进程已完整初始化过这个库，直接返回（不写库、不提交）
            return {"ok": True, "db": _p, "schema_version": SCHEMA_VERSION,
                    "from_user_version": cur, "skipped": "already_inited_in_process"}
        c.executescript(SCHEMA_SQL)
        # 补列（老库）
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute('PRAGMA table_info("%s")' % table)}
            for name, decl in cols:
                if name not in have:
                    c.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, decl))
        # 默认策略
        c.execute(
            "INSERT OR IGNORE INTO v3_title_policy"
            " (policy_id, platform, min_len, max_len, min_cjk, source, note, updated_at)"
            " VALUES ('wechat-default','wechat',8,60,6,"
            " '微信小店商品名称限制（以平台后台实际提示为准；本值可改，改后生效）',"
            " '默认值仅作起点，实际以平台报错为准', ?)", (now(),))
        c.execute("INSERT OR IGNORE INTO v3_store"
                  " (store_id, platform, display_name, title_policy_id, enabled, created_at, updated_at)"
                  " VALUES ('default','wechat','默认店铺','wechat-default',1,?,?)",
                  (now(), now()))
        c.execute("INSERT OR REPLACE INTO v3_meta(key, value) VALUES('schema_version', ?)",
                  (SCHEMA_VERSION,))
        c.execute("INSERT OR REPLACE INTO v3_meta(key, value) VALUES('schema_applied_at', ?)",
                  (now(),))
        c.execute("PRAGMA user_version = 300")
        c.commit()
        if _p:
            with _INIT_LOCK:
                _INITED[_key] = 1
        return {"ok": True, "db": _p or DB_PATH, "schema_version": SCHEMA_VERSION,
                "from_user_version": cur}
    finally:
        if own:
            c.close()


def backup(tag: str = "") -> str | None:
    """迁移前备份整库（WAL 安全：先 checkpoint）。"""
    if not os.path.exists(DB_PATH):
        return None
    ensure_dirs()
    c = connect()
    try:
        c.execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        pass
    finally:
        c.close()
    name = "v3.db.bak-%s-%s" % (tag or "manual", datetime.now().strftime("%Y%m%d-%H%M%S"))
    dst = os.path.join(BACKUP_DIR, name)
    shutil.copy2(DB_PATH, dst)
    return dst


# ---------------------------------------------------------------- 审计 / 任务

def audit(c: sqlite3.Connection, action: str, entity: str = "", entity_id: Any = "",
          before: Any = None, after: Any = None, actor: str = "system") -> None:
    c.execute("INSERT INTO v3_audit(ts, actor, action, entity, entity_id, before, after)"
              " VALUES(?,?,?,?,?,?,?)",
              (now(), actor, action, entity, str(entity_id or ""),
               j(before) if before is not None else "", j(after) if after is not None else ""))


def job_start(c: sqlite3.Connection, stage: str, product_id: int | None = None,
              payload: Any = None, idem: str | None = None) -> str:
    jid = "job-%d-%s" % (int(time.time() * 1000),
                         os.urandom(3).hex())
    if idem:
        old = c.execute("SELECT job_id FROM v3_job WHERE idempotency_key=?", (idem,)).fetchone()
        if old:
            return old["job_id"]
    c.execute("INSERT INTO v3_job(job_id, product_id, stage, status, payload,"
              " idempotency_key, started_at, updated_at)"
              " VALUES(?,?,?,'running',?,?,?,?)",
              (jid, product_id, stage, j(payload), idem, now(), now()))
    return jid


def job_finish(c: sqlite3.Connection, job_id: str, ok: bool, result: Any = None,
               err: str = "") -> None:
    c.execute("UPDATE v3_job SET status=?, result=?, last_error=?, updated_at=?"
              " WHERE job_id=?",
              ("succeeded" if ok else "failed", j(result), err or "", now(), job_id))


def job_get(c: sqlite3.Connection, job_id: str) -> dict | None:
    r = c.execute("SELECT * FROM v3_job WHERE job_id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def job_retry(c: sqlite3.Connection, job_id: str) -> dict:
    r = c.execute("SELECT * FROM v3_job WHERE job_id=?", (job_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "没有这个任务"}
    c.execute("UPDATE v3_job SET status='pending', retry_count=retry_count+1,"
              " last_error='', updated_at=? WHERE job_id=?", (now(), job_id))
    c.commit()
    return {"ok": True, "job": job_get(c, job_id), "note": "已标记待重试，请调对应阶段的接口再跑一次"}


# ---------------------------------------------------------------- 商品

def upsert_product(c: sqlite3.Connection, rec: dict) -> tuple[int, bool]:
    """按 (source_platform, source_product_id) 幂等写。返回 (product_id, created)。

    铁律：original_title / original_payload 只在首次写入，之后不覆盖。
    """
    plat = (rec.get("source_platform") or "other").strip() or "other"
    src_id = (rec.get("source_product_id") or "").strip()
    row = None
    if src_id:
        row = c.execute("SELECT * FROM v3_product WHERE source_platform=? AND source_product_id=?",
                        (plat, src_id)).fetchone()
    if not row and rec.get("source_url"):
        row = c.execute("SELECT * FROM v3_product WHERE source_url=?",
                        (rec["source_url"],)).fetchone()

    if row:
        pid = row["product_id"]
        # 只更新可变的业务字段，不动 original_title / original_payload / provenance
        sets, args = [], []
        for k in ("category_id", "category_path", "category_confidence", "attributes",
                  "source_shop_name", "source_store_id", "pack_code",
                  "price_min", "price_max", "note", "main_image_id"):
            if k in rec and rec[k] not in (None, ""):
                sets.append("%s=?" % k)
                args.append(j(rec[k]) if k == "attributes" else rec[k])
        if sets:
            sets.append("updated_at=?")
            args.append(now())
            args.append(pid)
            c.execute("UPDATE v3_product SET %s WHERE product_id=?" % ", ".join(sets), args)
        return pid, False

    cur = c.execute(
        "INSERT INTO v3_product(source_platform, source_url, source_product_id,"
        " original_title, category_id, category_path, category_confidence, attributes,"
        " original_payload, provenance, source_shop_name, source_store_id, pack_code,"
        " price_min, price_max, status, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'imported',?,?)",
        (plat, rec.get("source_url") or "", src_id,
         (rec.get("original_title") or rec.get("title") or "").strip(),
         rec.get("category_id") or "", rec.get("category_path") or "",
         rec.get("category_confidence"), j(rec.get("attributes")),
         j(rec.get("original_payload")), rec.get("provenance") or "manual_import",
         rec.get("source_shop_name") or "", rec.get("source_store_id") or "",
         rec.get("pack_code") or "",
         rec.get("price_min"), rec.get("price_max"), now(), now()))
    return int(cur.lastrowid), True


def get_product(c: sqlite3.Connection, pid: int) -> dict | None:
    r = c.execute("SELECT * FROM v3_product WHERE product_id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["attributes"] = unj(d.get("attributes"), {}) or {}
    d["profile"] = unj(d.get("profile"), None)
    d["original_payload"] = unj(d.get("original_payload"), None)
    return d


def pack_product_ids(c: sqlite3.Connection, code: str) -> list[int] | None:
    """包码 → 商品 id 列表。**成员包优先**（v3_pack_member 有行就用它），
    否则返回 None 表示「这是老式包，按 v3_product.pack_code 取」。

    ★ 为什么必须有这一层：第 3 步精筛把结果存成成员包（引用式，不复制商品行），
      而 v3_product.pack_code 上根本没有这个新包码 —— 所有按 pack_code 取件的地方
      （出表、图床上传、精筛重跑、打码）都会拿到 0 件。这里是唯一收口点。
    """
    if not code:
        return None
    try:
        rows = [int(r[0]) for r in c.execute(
            "SELECT product_id FROM v3_pack_member WHERE pack_code=? ORDER BY product_id",
            (code,))]
    except sqlite3.OperationalError:      # 老库还没建这张表
        return None
    return rows or None


def list_products(c: sqlite3.Connection, status: str | None = None, q: str | None = None,
                  pack: str | None = None, limit: int = 500, offset: int = 0) -> list[dict]:
    sql = ["SELECT p.*, "
           " (SELECT COUNT(*) FROM v3_image i WHERE i.product_id=p.product_id) AS n_img,"
           " (SELECT COUNT(*) FROM v3_title_variant t WHERE t.product_id=p.product_id"
           "   AND t.status='selected') AS n_selected"
           " FROM v3_product p WHERE 1=1"]
    args: list[Any] = []
    if status:
        sql.append("AND p.status=?")
        args.append(status)
    if pack:
        mem = pack_product_ids(c, pack)
        if mem:
            sql.append("AND p.product_id IN (%s)" % ",".join("?" * len(mem)))
            args += mem
        else:
            sql.append("AND p.pack_code=?")
            args.append(pack)
    if q:
        sql.append("AND (p.original_title LIKE ? OR p.source_product_id LIKE ?)")
        args += ["%" + q + "%", "%" + q + "%"]
    sql.append("ORDER BY p.product_id DESC LIMIT ? OFFSET ?")
    args += [int(limit), int(offset)]
    out = []
    for r in c.execute(" ".join(sql), args):
        d = dict(r)
        d["attributes"] = unj(d.get("attributes"), {}) or {}
        d["profile"] = unj(d.get("profile"), None)
        out.append(d)
    return out


PROTECTED_STATUS = "blocked"


def set_product_status(c: sqlite3.Connection, pid: int, status: str,
                       force: bool = False) -> bool:
    """改状态。

    ★ blocked 是「人工待定」状态：后续任何阶段都不许把它自动冲掉，
      否则细筛拦下的商品会在出包时悄悄变回正常（这正是最容易出事的地方）。
      要放行必须显式 force=True（对应 UI 上的人工放行按钮）。
    返回是否真的改了。
    """
    r = c.execute("SELECT status FROM v3_product WHERE product_id=?", (pid,)).fetchone()
    if not r:
        return False
    cur = r["status"]
    if cur == PROTECTED_STATUS and status != PROTECTED_STATUS and not force:
        return False
    c.execute("UPDATE v3_product SET status=?, updated_at=? WHERE product_id=?",
              (status, now(), pid))
    return True


# ---------------------------------------------------------------- 图片

ROLE_MAP = {
    "主图": "main", "轮播": "carousel", "轮播图": "carousel",
    "详情图": "detail", "详情": "detail", "SKU图": "sku", "sku图": "sku",
    "规格图": "sku", "视频": "video",
    "main": "main", "carousel": "carousel", "detail": "detail",
    "sku": "sku", "video": "video", "unknown": "unknown",
}


def norm_role(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return "unknown"
    return ROLE_MAP.get(s, ROLE_MAP.get(s.lower(), "unknown"))


def slot_key(role: str, seq: int, sku_id: str | None = None,
             slice_index: int | None = None) -> str:
    return "%s|%d|%s|%s" % (role, int(seq or 0), sku_id or "", "" if slice_index is None else slice_index)


def upsert_image(c: sqlite3.Connection, pid: int, rec: dict) -> tuple[int, bool]:
    """图片按 (product_id, slot_key) 幂等。role/sequence 一旦写入不被替换动作改变。"""
    role = norm_role(rec.get("role"))
    seq = int(rec.get("sequence") or 0)
    sku = (rec.get("sku_id") or "").strip() or None
    sl = int(rec["slice_index"]) if rec.get("slice_index") not in (None, "") else None
    key = slot_key(role, seq, sku, sl)
    r = c.execute("SELECT * FROM v3_image WHERE product_id=? AND slot_key=?",
                  (pid, key)).fetchone()
    if r:
        return int(r["image_id"]), False
    cur = c.execute(
        "INSERT INTO v3_image(product_id, slot_key, role, sequence, slice_index, sku_id,"
        " original_url, original_local_path, current_local_path, current_remote_url,"
        " content_sha256, parent_image_id, status, width, height, note, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'original',?,?,?,?,?)",
        (pid, key, role, seq, sl, sku,
         rec.get("original_url") or "", rec.get("original_local_path") or "",
         rec.get("current_local_path") or rec.get("original_local_path") or "",
         rec.get("current_remote_url") or "",
         rec.get("content_sha256") or "", rec.get("parent_image_id"),
         rec.get("width"), rec.get("height"), rec.get("note") or "", now(), now()))
    return int(cur.lastrowid), True


def list_images(c: sqlite3.Connection, pid: int, role: str | None = None) -> list[dict]:
    sql = "SELECT * FROM v3_image WHERE product_id=?"
    args: list[Any] = [pid]
    if role:
        sql += " AND role=?"
        args.append(role)
    sql += " ORDER BY CASE role WHEN 'main' THEN 0 WHEN 'carousel' THEN 1" \
           " WHEN 'sku' THEN 2 WHEN 'detail' THEN 3 ELSE 4 END, sequence, image_id"
    return [dict(r) for r in c.execute(sql, args)]


def set_current_image(c: sqlite3.Connection, pid: int, image_id: int | None) -> None:
    c.execute("UPDATE v3_product SET main_image_id=?, updated_at=? WHERE product_id=?",
              (image_id, now(), pid))
