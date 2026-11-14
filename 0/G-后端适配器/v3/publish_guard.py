"""发布幂等护栏 —— 防「中断后重跑 = 同一件货上架两次」（2026-10-27 主人要求）

主人原话：
  「图片已经上传过一部分了，你这边停掉了，那我再上传，它会是什么样的情形？
    会不会因为这种中断，一件货同时上架两次，导致后台出现了重复的铺货？」

四层保护（每层都独立生效，任一层失效其余三层仍拦得住）：

  ① 图片上传缓存  v3_wx_img_cache：同一文件（path+mtime+size）只传一次
     → 中断重跑不重传，几百张图 0 次网络

  ② 发布意图先行  v3_publish_intent：**建之前**先落一条 status='creating' 并提交
     → 进程被杀/断电也留下痕迹，下次能对账

  ③ 建前查重     ：本地成功记录（指纹）+ 微信侧实时商品索引（标题+首图）双查
     → 已建过的直接跳过，不产生第二件

  ④ 对账修复     reconcile()：把遗留的 creating 行拿到微信侧去认领
     → 认领成功 → created（不重建）；找不到 → failed（可安全重试）
     → 标题重名的不猜，留「需人工确认」

⚠️ 查重不是为了阻止建货：微信列表拉不到时只**降级告警**并继续，不让网络故障变成「建不了货」。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

# 微信侧商品索引缓存（秒）。批量建货时只拉一次。
INDEX_TTL = 120
_INDEX: dict[str, dict] = {}          # 2026-11-07：按 appid 分片的微信侧索引


def _index_slot() -> dict:
    sid = _shop()
    return _INDEX.setdefault(sid, {"ts": 0.0, "d": {}, "err": "", "stamp": "", "cov": {}, "ids": set()})


# ---------------------------------------------------------------- 基础设施

def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _shop() -> str:
    """当前店铺 appid（发布缓存/查重索引都按它分片）。"""
    try:
        return _wx().shop_id() or ""
    except Exception:                       # noqa: BLE001
        return ""


def _wx():
    """拿到 wxshop 模块（bridge 是用 importlib 加载的，模块名不固定，按真实模块名找）。"""
    import sys
    for name, mod in list(sys.modules.items()):
        if name.split(".")[-1] == "wxshop" and mod is not None:
            return mod
    try:                                    # 作为包导入（server.v3.publish_guard）
        from . import wxshop as w           # type: ignore
        return w
    except Exception:                       # noqa: BLE001
        import wxshop as w                  # type: ignore  平铺导入
        return w


def ensure(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS v3_publish_intent (
      record_id     INTEGER PRIMARY KEY AUTOINCREMENT,
      product_id    INTEGER NOT NULL,
      pack_code     TEXT    DEFAULT '',
      title         TEXT    DEFAULT '',
      fingerprint   TEXT    DEFAULT '',
      status        TEXT    DEFAULT 'creating',   -- creating|created|failed|skipped
      wx_product_id TEXT    DEFAULT '',
      errcode       INTEGER DEFAULT 0,
      errmsg        TEXT    DEFAULT '',
      batch_id      INTEGER DEFAULT 0,
      note          TEXT    DEFAULT '',
      created_at    TEXT    DEFAULT '',
      updated_at    TEXT    DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_pi_pid ON v3_publish_intent(product_id);
    CREATE INDEX IF NOT EXISTS ix_pi_st  ON v3_publish_intent(status);
    CREATE INDEX IF NOT EXISTS ix_pi_fp  ON v3_publish_intent(fingerprint);

    CREATE TABLE IF NOT EXISTS v3_wx_img_cache (
      k          TEXT PRIMARY KEY,   -- sha1(abspath|mtime|size)
      path       TEXT DEFAULT '',
      url        TEXT DEFAULT '',
      bytes      INTEGER DEFAULT 0,
      created_at TEXT DEFAULT ''
    );

    -- ★ 2026-10-29：微信侧商品标题索引（落库复用，避免每次重拉几百件详情）
    --   查重必须看到**全店**商品，而列表接口一页只 50 条、详情要逐件拉。
    --   落库后首次全量构建一次，之后只对新增 id 拉详情。
    CREATE TABLE IF NOT EXISTS v3_wx_title_idx (
      product_id TEXT PRIMARY KEY,
      title      TEXT DEFAULT '',
      head       TEXT DEFAULT '',
      wx_status  INTEGER DEFAULT 0,
      updated_at TEXT DEFAULT ''
    );
    """)
    conn.commit()


# ---------------------------------------------------------------- ① 图片缓存

def img_key(local_path: str) -> str:
    try:
        p = os.path.abspath(local_path)
        st = os.stat(p)
        # ★ 2026-11-07 多店：mmecimage 地址带 appid，A 店传的图 B 店用不了，
        #   所以缓存键必须拼店号 —— 换店会重传，这是对的，不是浪费。
        raw = "|".join([_shop(), p, "%.3f" % st.st_mtime, str(st.st_size)])
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:32]
    except Exception:                       # noqa: BLE001
        return ""


def img_get(conn: sqlite3.Connection, local_path: str) -> str:
    k = img_key(local_path)
    if not k:
        return ""
    r = conn.execute("SELECT url FROM v3_wx_img_cache WHERE k=?", (k,)).fetchone()
    return (r[0] if r else "") or ""


def img_put(conn: sqlite3.Connection, local_path: str, url: str, nbytes: int = 0) -> None:
    k = img_key(local_path)
    if not k or not url:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO v3_wx_img_cache(k,path,url,bytes,created_at)"
            " VALUES(?,?,?,?,?)", (k, os.path.abspath(local_path), url, int(nbytes), _now()))
        conn.commit()
    except Exception:                       # noqa: BLE001
        pass                                # 缓存写失败不能影响上传本身


def img_stats(conn: sqlite3.Connection) -> dict:
    try:
        n = conn.execute("SELECT COUNT(*) FROM v3_wx_img_cache").fetchone()[0]
    except Exception:                       # noqa: BLE001
        n = 0
    return {"cached": int(n)}


# ---------------------------------------------------------------- ② 指纹与意图

def fingerprint(conn: sqlite3.Connection, pid: int, title: str = "",
                price_markup: float = 1.0, sku_limit: int = 0,
                stock_per_sku: int = 7) -> str:
    """这件货"建出来会长什么样"的指纹。同指纹 = 同一件货 = 不该建第二次。

    取：最终标题 + 主图集合 + 规格数上限 + 价格系数 + 每SKU库存。
    **不含**时间，所以「同一件货重跑」指纹相同，「改过标题/改过价」指纹不同（可当新品建）。
    """
    t = (title or "").strip()
    if not t:
        r = conn.execute("SELECT original_title FROM v3_product WHERE product_id=?",
                         (int(pid),)).fetchone()
        t = ((r[0] if r else "") or "")[:60]
    heads = [str(x[0]) for x in conn.execute(
        "SELECT current_local_path FROM v3_image WHERE product_id=? AND role='main'"
        " AND current_local_path IS NOT NULL AND current_local_path<>''"
        " ORDER BY sequence LIMIT 9", (int(pid),))]
    # 主图用「文件名+size」而不是全路径：换目录不该被当成新品
    sig = []
    for p in heads:
        try:
            sig.append("%s:%d" % (os.path.basename(p), os.path.getsize(p)))
        except Exception:                   # noqa: BLE001
            sig.append(os.path.basename(p))
    raw = json.dumps({"shop": _shop(), "t": t, "h": sig, "sku": int(sku_limit),
                      "mu": round(float(price_markup), 3), "st": int(stock_per_sku)},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def begin(conn: sqlite3.Connection, pid: int, pack_code: str = "", title: str = "",
          fp: str = "", batch_id: int = 0) -> int:
    """**建之前**落一条 creating（立刻提交）—— 这是崩溃后能对账的唯一依据。"""
    ensure(conn)
    cur = conn.execute(
        "INSERT INTO v3_publish_intent(product_id,pack_code,title,fingerprint,status,"
        "batch_id,created_at,updated_at) VALUES(?,?,?,?,'creating',?,?,?)",
        (int(pid), pack_code or "", title or "", fp or "", int(batch_id or 0), _now(), _now()))
    conn.commit()
    return int(cur.lastrowid)


def finish(conn: sqlite3.Connection, rid: int, status: str, wx_product_id="",
           errcode: int = 0, errmsg: str = "", note: str = "") -> None:
    conn.execute(
        "UPDATE v3_publish_intent SET status=?, wx_product_id=?, errcode=?, errmsg=?,"
        " note=?, updated_at=? WHERE record_id=?",
        (status, str(wx_product_id or ""), int(errcode or 0), (errmsg or "")[:300],
         note or "", _now(), int(rid)))
    conn.commit()


def created_by_fp(conn: sqlite3.Connection, fp: str) -> list[dict]:
    if not fp:
        return []
    rows = conn.execute(
        "SELECT record_id,product_id,title,wx_product_id,created_at FROM v3_publish_intent"
        " WHERE fingerprint=? AND status='created' ORDER BY record_id DESC", (fp,)).fetchall()
    return [{"record_id": r[0], "product_id": r[1], "title": r[2],
             "wx_product_id": str(r[3] or ""), "at": r[4]} for r in rows]


# ---------------------------------------------------------------- ③ 微信侧索引

def wx_index(conn: sqlite3.Connection | None = None, force: bool = False,
             max_items: int = 300, max_fetch: int = 200) -> dict:
    """微信侧现有商品索引 {wx_id: {title,head}}，缓存 INDEX_TTL 秒。

    ★ 2026-10-29 重写（主人问「检查已上传商品」时顺带查出的硬伤）：
      微信商品**列表接口一页只返回 50 条**（`page_size=100` 也只给 50），
      而 `total_num` 报的是全店真实数（实测 status=6 → **457** 件、status=5 → **98** 件）。
      原实现只调一次、不翻页 → 索引里最多 100 件 →
      **超过 100 件之后「已建过」判不出来，直接有重复铺货风险。**

      现在三层：
        ① 翻页拿**全量** id（列表接口便宜，一页 50 条）
        ② 详情逐件拉，但**落库复用**（v3_wx_title_idx），下次只补新 id
        ③ 返回 coverage：`store_total` / `indexed` / `pending` / `truncated`
           —— 索引不全时**显式暴露**，不再静默当成「微信侧没有」

    max_fetch：本次最多新拉多少件详情（0 = 不限）。首次全量构建可传 0。
    只读；失败返回 {"ok":False,"err":...} 且**不抛**（查重失败不能拦住建货）。
    """
    now = time.time()
    S = _index_slot()
    if (not force) and S["d"] and (now - S["ts"] < INDEX_TTL):
        return {"ok": True, "cached": True, "items": S["d"],
                "count": len(S["d"]),
                "coverage": S.get("cov") or {}, "err": ""}
    try:
        wx = _wx()
    except Exception as e:                  # noqa: BLE001
        S.update({"err": "没有 wxshop 模块: %s" % e})
        return {"ok": False, "err": S["err"], "items": {}, "count": 0,
                "coverage": {}}

    # ---- ① 全量 id（翻页）----
    try:
        if hasattr(wx, "all_products_by_status"):
            # 不传 statuses → 自动扫 0..13 全部有量的状态（新建/补图后的商品会落到 0）
            lst = wx.all_products_by_status()
            ids = [str(x) for x in (lst.get("ids") or [])]
            # 多个状态会重叠（同一商品同时在 0 与 1 里），所以总数取**去重后**的条数，
            # 不能把各状态 total 相加（那样会虚高，coverage 会永远显示"还差很多"）
            store_total = len(ids)
            truncated = bool(lst.get("truncated"))
        else:                                # 老版 wxshop：退回单页
            ids, store_total, truncated = [], 0, True
            for st in (6, 5):
                r = wx.list_products(st, 100)
                store_total += int(r.get("total") or 0)
                for x in (r.get("ids") or []):
                    s = str(x)
                    if s not in ids:
                        ids.append(s)
    except Exception as e:                  # noqa: BLE001
        S.update({"err": "list_products 失败: %s" % e})
        return {"ok": False, "err": S["err"], "items": {}, "count": 0,
                "coverage": {}}

    # ---- ② 已有索引（落库）+ 补新 id ----
    d: dict[str, dict] = {}
    pend: list[str] = []
    if conn is not None:
        try:
            ensure(conn)
            # 2026-11-07：落库键 = "appid|微信商品ID"，两家店的索引互不覆盖。
            for r in conn.execute("SELECT product_id,title,head FROM v3_wx_title_idx"
                                  " WHERE product_id LIKE ?", (_shop() + "|%",)):
                d[str(r[0]).split("|", 1)[-1]] = {"title": (r[1] or "")[:60], "head": r[2] or ""}
            pend = [i for i in ids if i not in d]
        except Exception:                   # noqa: BLE001
            pend = list(ids)
    else:
        pend = list(ids)

    todo = pend if (max_fetch <= 0) else pend[:int(max_fetch)]
    fetched = 0
    rows = []
    for i in todo:
        try:
            g = wx.get_product(i)
        except Exception:                   # noqa: BLE001
            continue
        if not g.get("ok"):
            continue
        ep = g.get("edit_product") or {}
        head = ""
        for h in (ep.get("head_imgs") or []):
            head = h if isinstance(h, str) else (h.get("img_url") or "")
            if head:
                break
        t = (ep.get("title") or "").strip()[:60]
        d[str(i)] = {"title": t, "head": head}
        rows.append((_shop() + "|" + str(i), t, head, int(ep.get("status") or 0), _now()))
        fetched += 1

    if conn is not None and rows:
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO v3_wx_title_idx"
                "(product_id,title,head,wx_status,updated_at) VALUES(?,?,?,?,?)", rows)
            conn.commit()
        except Exception:                   # noqa: BLE001
            pass

    # 只留本次真实存在过的 id（被删掉的商品不该继续参与查重）
    alive = {i: v for i, v in d.items() if i in set(ids)} if ids else d
    indexed = len(alive)
    pending = max(0, len(ids) - indexed) if ids else 0
    if max_items and len(alive) > max_items:
        alive = dict(list(alive.items())[:max_items])
    cov = {"store_total": store_total, "store_ids": len(ids), "indexed": indexed,
           "pending": pending, "fetched_now": fetched, "truncated": truncated,
           "complete": bool(ids) and pending == 0 and not truncated}
    S.update({"ts": now, "d": alive, "err": "", "stamp": _now(), "cov": cov,
              "ids": set(ids)})
    return {"ok": True, "cached": False, "items": alive, "count": len(alive),
            "coverage": cov, "err": ""}


def wx_live_ids() -> set[str]:
    r = wx_index()
    return set((r.get("items") or {}).keys())


# ---------------------------------------------------------------- 建前查重

def dedup_check(conn: sqlite3.Connection, pid: int, title: str, fp: str,
                idx: dict | None = None) -> dict:
    """返回 {'action': 'skip'|'go', 'reason': str, 'wx': str, 'warn': str}

    skip 的三个来源：
      a. 本地已有同指纹的 created 记录，且该 wx_id 现在还在微信侧 → 铁证重复
      b. 本地有 created 记录但微信侧已不存在（被删了）→ 允许重建
      c. 微信侧有同标题唯一匹配的商品（本地没记录，可能是手动建的或被清库）→ 跳过并提示人工核对
    """
    out = {"action": "go", "reason": "", "wx": "", "warn": ""}

    # ★ 2026-10-28 防御：调用方传错类型（真实事故：循环变量把 wx_index 的结果覆盖成 int，
    #   每件都 AttributeError → 被上层记成「护栏降级」，查重静默失效）。
    #   这里不抛错、也不假装查过：传错就当没给索引，自己去拉一次，并把这件事写进 warn。
    if idx is not None and not isinstance(idx, dict):
        out["warn"] = ("调用方传的索引类型不对（%s），已改为自行拉取微信列表"
                       % type(idx).__name__)
        idx = None

    # a/b
    for h in created_by_fp(conn, fp):
        wid = h["wx_product_id"]
        if not wid:
            continue
        if idx is None:
            idx = wx_index(conn)
        items = idx.get("items") or {}
        if wid in items:
            out.update({"action": "skip", "wx": wid,
                        "reason": "本地已在 %s 建过同款（微信商品 %s 仍在），跳过"
                                  % (h["at"], wid)})
            return out
        out["warn"] = ("本地记过 %s，但微信侧已找不到（可能被删），按新货重建"
                       % wid)

    # c（只在本地完全没记录时做，避免误伤）
    items = (idx or wx_index(conn)).get("items") or {}
    tt = (title or "").strip()[:60]
    if tt and not created_by_fp(conn, fp):
        same = [k for k, v in items.items() if v.get("title") == tt]
        if len(same) == 1:
            out.update({"action": "skip", "wx": same[0],
                        "reason": "微信侧已有一件同名商品（%s），本地无记录 → 跳过防重复；"
                                  "若确属另一件，请在微信后台改名后重试" % same[0]})
            return out
        if len(same) > 1:
            out.update({"action": "skip", "wx": same[0],
                        "reason": "微信侧有 %d 件同名商品，风险高 → 跳过，请人工确认"
                                  % len(same)})
            return out
    return out


# ---------------------------------------------------------------- ④ 对账

def reconcile(conn: sqlite3.Connection, limit: int = 200, force_index: bool = False) -> dict:
    """把遗留的 creating 行拿去微信侧认领。

    - wx_product_id 命中 → created（不重建）
    - 标题唯一匹配     → created，note 标明「按标题对账回填」
    - 无匹配           → failed，note「微信侧无此商品，可安全重试」
    - 标题重名         → 保持 creating，note「需人工确认」
    """
    ensure(conn)
    rows = conn.execute(
        "SELECT record_id,product_id,title,wx_product_id,note FROM v3_publish_intent"
        " WHERE status='creating' ORDER BY record_id LIMIT ?", (int(limit),)).fetchall()
    if not rows:
        return {"ok": True, "checked": 0, "fixed": 0, "failed": 0, "hold": 0, "items": []}

    idx = wx_index(conn, force=force_index)
    if not idx.get("ok"):
        return {"ok": False, "checked": len(rows), "fixed": 0, "failed": 0, "hold": len(rows),
                "error": "微信列表取不到，无法对账：%s" % idx.get("err"), "items": []}
    items = idx.get("items") or {}

    fixed = failed = hold = 0
    out: list[dict] = []
    for rid, pid, title, wid, _note in rows:
        wid = str(wid or "")
        if wid and wid in items:
            finish(conn, rid, "created", wid, note="对账回填：微信侧已存在")
            fixed += 1
            out.append({"record_id": rid, "product_id": pid, "result": "created",
                        "wx": wid, "by": "id"})
            continue
        tt = (title or "").strip()[:60]
        cands = [k for k, v in items.items() if tt and v.get("title") == tt]
        if len(cands) == 1:
            finish(conn, rid, "created", cands[0], note="对账回填：按标题唯一匹配")
            fixed += 1
            out.append({"record_id": rid, "product_id": pid, "result": "created",
                        "wx": cands[0], "by": "title"})
        elif len(cands) == 0:
            finish(conn, rid, "failed", errmsg="对账未找到",
                   note="微信侧无此商品，可安全重试")
            failed += 1
            out.append({"record_id": rid, "product_id": pid, "result": "failed"})
        else:
            finish(conn, rid, "creating",
                   note="对账：微信侧有 %d 件同名，需人工确认" % len(cands))
            hold += 1
            out.append({"record_id": rid, "product_id": pid, "result": "hold",
                        "candidates": cands[:5]})
    return {"ok": True, "checked": len(rows), "fixed": fixed, "failed": failed,
            "hold": hold, "items": out}


# ---------------------------------------------------------------- 状态/重试

def state(conn: sqlite3.Connection, limit: int = 40) -> dict:
    ensure(conn)
    cnt = {}
    for st, n in conn.execute(
            "SELECT status, COUNT(*) FROM v3_publish_intent GROUP BY status"):
        cnt[str(st)] = int(n)
    rows = conn.execute(
        "SELECT record_id,product_id,title,status,wx_product_id,errcode,errmsg,note,"
        "created_at,updated_at FROM v3_publish_intent ORDER BY record_id DESC LIMIT ?",
        (int(limit),)).fetchall()
    items = [{"record_id": r[0], "product_id": r[1], "title": r[2], "status": r[3],
              "wx_product_id": str(r[4] or ""), "errcode": r[5], "errmsg": r[6],
              "note": r[7], "created_at": r[8], "updated_at": r[9]} for r in rows]
    return {"ok": True,
            "creating": cnt.get("creating", 0), "created": cnt.get("created", 0),
            "failed": cnt.get("failed", 0), "skipped": cnt.get("skipped", 0),
            "items": items, "img": img_stats(conn)}


def failed_pids(conn: sqlite3.Connection, limit: int = 5000) -> list[int]:
    """最近一次「没建成」的 product_id（去重，供「只重试失败」用）。"""
    ensure(conn)
    rows = conn.execute(
        "SELECT DISTINCT product_id FROM v3_publish_intent WHERE status='failed'"
        " ORDER BY record_id DESC LIMIT ?", (int(limit),)).fetchall()
    return [int(r[0]) for r in rows]
