# -*- coding: utf-8 -*-
r"""第 4 步「检测打码」瘦身：只处理第 3 步 OCR 判定命中的图。

为什么能瘦
----------
第 3 步精筛已经把每张图跑过 PP-OCRv6 det+rec，结果落在 v3.db 的 ``v3_image_ocr``
（``image_id`` 主键、``state``、``hit_words``、``full_text``）。到第 4 步时：

  · ``hit_words`` 非空（或 ``v3_image.note`` 含 ``OCR:命中``）  → **命中图**，需要涂白；
  · ``state='ok'`` 且 ``hit_words`` 为空                        → **已判干净**，涂白没意义，跳过；
  · 没有 OCR 记录、或 ``state!='ok'``（OCR 失败）                → **未判定**，按保守策略照常处理。

注意：**OCR 引擎本身删不掉**。涂白要靠 det（找文字框）才知道涂哪里，第 4 步对命中图
仍然要跑 det+rec。被删掉的是「对全量图重做一遍判定」这一步。

实现方式（不改 鉴图.py 一行）
----------------------------
``鉴图.py`` 的取图条件是 ``处理状态 NOT IN ('已处理','不可读')``。所以本模块在第 4 步开跑前，
把**已判干净**的图在 ``商品.db`` 里预先标成 ``已处理``（说明写清「第3步OCR判干净·跳过」），
引擎就自然只挑到命中图和未判定图 —— 不出图、不下载、不重跑判定。

数据源两套：
  · ``v3.db``    —— 第 3 步的 OCR 判定（v3_image / v3_image_ocr / v3_product）
  · ``商品.db``  —— 小猪本地库（图片.url / 本地路径 / 处理状态 …），与官方客户端共用
两者按**图片原始 URL** 对齐（实测 97/97、61/61 全对得上）。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

MODE_SLIM = "slim"                 # 走了瘦身：只处理命中 + 未判定
MODE_FALLBACK = "fallback-full"    # 本包没有第 3 步 OCR 记录 → 回落全量
MODE_EMPTY = "empty"               # 这批商品没有图

CLEAN_NOTE = "第3步OCR判干净·跳过"   # 写进 商品.db 图片.说明
DONE_STATES = ("已处理", "已替换", "不可读")


# ============================================================
#  v3 侧：把图分成 命中 / 已判干净 / 未判定
# ============================================================
def _chunks(seq, n=400):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def products_v3(c, pids) -> list[dict]:
    """product_id → source_product_id（后者才是 商品.db 的 goods_id）。"""
    out: list[dict] = []
    for ch in _chunks([int(x) for x in pids]):
        marks = ",".join("?" * len(ch))
        for r in c.execute(
                "SELECT product_id, source_product_id, pack_code, original_title"
                " FROM v3_product WHERE product_id IN (%s)" % marks, ch):
            out.append({"product_id": int(r[0]),
                        "source_product_id": str(r[1] or ""),
                        "pack_code": str(r[2] or ""),
                        "title": str(r[3] or "")})
    return out


def pids_for_goods(c, goods) -> list[int]:
    """小猪 goods_id → v3 product_id（第 4 步按包/按商品调用时的换算法）。"""
    goods = [str(g) for g in (goods or []) if str(g)]
    out: list[int] = []
    for ch in _chunks(goods, 400):
        marks = ",".join("?" * len(ch))
        for r in c.execute(
                "SELECT product_id FROM v3_product WHERE CAST(source_product_id AS TEXT)"
                " IN (%s)" % marks, ch):
            out.append(int(r[0]))
    return out


def load_words(path) -> list[str]:
    """读合并词库（一行一个词，长词优先，与引擎的匹配口径一致）。"""
    try:
        ws = [w.strip() for w in Path(path).read_text(encoding="utf-8").splitlines() if w.strip()]
    except Exception:            # noqa: BLE001
        return []
    ws.sort(key=len, reverse=True)
    return ws


def classify(c, pids, words=None) -> dict:
    """按第 3 步 OCR 结果把图分三类（纯只读）。

    `words` 给了就用**当前**合并词库对第 3 步存下的 `full_text` **重新匹配一遍**
    （纯字符串比对，不跑 OCR）。为什么必须复核：`v3_image_ocr.hit_words` 是第 3 步当时的
    判定结果，词库后来加过词、也豁免过词 —— 直接信旧结论会出现「第 3 步说干净、现在的引擎
    却会涂白」的偏差，瘦身就会漏掉该涂的图。实测这批数据偏差 11 : 6。
    """
    hit: list[dict] = []
    clean: list[dict] = []
    unknown: list[dict] = []
    total = 0
    ocr_rows = 0
    wl = [w.lower() for w in (words or [])]
    for ch in _chunks([int(x) for x in pids]):
        marks = ",".join("?" * len(ch))
        sql = (
            "SELECT i.image_id AS image_id, i.product_id AS product_id, i.role AS role,"
            "       i.sequence AS sequence, i.original_url AS original_url,"
            "       i.current_local_path AS current_local_path, i.note AS note,"
            "       o.state AS state, o.hit_words AS hit_words, o.full_text AS full_text"
            " FROM v3_image i LEFT JOIN v3_image_ocr o ON o.image_id=i.image_id"
            " WHERE i.product_id IN (%s)" % marks)
        try:
            rows = list(c.execute(sql, ch))
        except Exception:          # noqa: BLE001  v3_image_ocr 还没建表
            rows = list(c.execute(
                "SELECT i.image_id AS image_id, i.product_id AS product_id, i.role AS role,"
                "       i.sequence AS sequence, i.original_url AS original_url,"
                "       i.current_local_path AS current_local_path, i.note AS note,"
                "       NULL AS state, '' AS hit_words, '' AS full_text"
                " FROM v3_image i WHERE i.product_id IN (%s)" % marks, ch))
        for r in rows:
            total += 1
            item = {
                "image_id": int(r["image_id"]),
                "product_id": int(r["product_id"]),
                "role": (r["role"] or ""),
                "sequence": r["sequence"] or 0,
                "url": (r["original_url"] or ""),
                "local_path": (r["current_local_path"] or ""),
                "words": (r["hit_words"] or ""),
            }
            state = (r["state"] or "").strip()
            if state:
                ocr_rows += 1
            note = (r["note"] or "")
            txt = (r["full_text"] or "").lower()
            rehit = [w for w in wl if w in txt] if (wl and txt) else []
            is_hit = bool(item["words"].strip()) or ("OCR:命中" in note) or bool(rehit)
            if is_hit:
                item["reason"] = ("第3步命中：" + (item["words"] or note[-30:])) if item["words"] \
                    else ("按当前词库复核命中：" + "、".join(rehit[:3]) if rehit else "第3步命中")
                hit.append(item)
            elif state == "ok":
                item["reason"] = CLEAN_NOTE
                clean.append(item)
            else:
                item["reason"] = ("没有第3步OCR记录" if not state
                                  else "第3步OCR未成功(%s)" % state)
                unknown.append(item)
    return {"hit": hit, "clean": clean, "unknown": unknown,
            "images_total": total, "ocr_rows": ocr_rows}


# ============================================================
#  商品.db 侧：对齐、跳过标记、下载
# ============================================================
def _pig_conn(pig_db):
    con = sqlite3.connect(str(pig_db), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def pig_rows(pig_db, goods) -> dict:
    """goods_id → {url: row}（只取 handle 得着的商品）。"""
    goods = [str(g) for g in goods if str(g)]
    out: dict[str, dict] = {}
    if not goods:
        return out
    con = _pig_conn(pig_db)
    try:
        for ch in _chunks(goods):
            marks = ",".join("?" * len(ch))
            for r in con.execute(
                    "SELECT rowid AS rid, url, goods_id, 类型, 序号, 本地路径,"
                    "       处理状态, 违规类型, 命中词, 说明, 新URL"
                    " FROM 图片 WHERE goods_id IN (%s)" % marks, ch):
                d = {k: r[k] for k in r.keys()}
                out.setdefault(str(d["goods_id"]), {})[str(d["url"] or "")] = d
    finally:
        con.close()
    return out


def match_pig(pig_index: dict, items: list[dict]) -> list[dict]:
    """给每个 v3 图挂上 商品.db 的行（按 URL 精确对齐）；对不上的挂 None。"""
    out = []
    for it in items:
        row = (pig_index.get(str(it.get("goods_id") or "")) or {}).get(it["url"])
        if row is None:
            # goods 未随 item 带出时，全表找一次（URL 唯一，成本可接受）
            for g, m in pig_index.items():
                if it["url"] in m:
                    row = m[it["url"]]
                    it = dict(it, goods_id=g)
                    break
        out.append(dict(it, pig=row))
    return out


def mark_clean(pig_db, urls, note: str = CLEAN_NOTE) -> dict:
    """把已判干净的图在 商品.db 里标成「已处理」。

    只动**还没处理**的行（``处理状态 NOT IN ('已处理','已替换','不可读')``），已处理的
    一个字节都不改 —— 反复跑第 4 步不会把状态越改越乱。``新URL`` 为空时补成原 URL
    （和 鉴图.py 对干净图的写法一致：干净的图直接用原图）。
    """
    urls = [u for u in (urls or []) if u]
    if not urls:
        return {"marked": 0, "already": 0, "missing": 0, "note": note}
    con = _pig_conn(pig_db)
    marked = already = missing = 0
    try:
        for ch in _chunks(urls):
            marks = ",".join("?" * len(ch))
            rows = list(con.execute(
                "SELECT rowid AS rid, COALESCE(新URL,'') AS new_url, 本地路径,"
                "       COALESCE(处理状态,'') AS st FROM 图片 WHERE url IN (%s)" % marks, ch))
            found = {int(r["rid"]) for r in rows}
            for r in rows:
                if (r["st"] or "") in DONE_STATES:
                    already += 1
                    continue
                con.execute(
                    "UPDATE 图片 SET 处理状态='已处理', 违规类型='', 命中词='',"
                    " 说明=?, 新URL=CASE WHEN COALESCE(新URL,'')='' THEN url ELSE 新URL END"
                    " WHERE rowid=?", (note, r["rid"]))
                marked += 1
            for u in ch:
                # 找不到行 = 这张图不在 商品.db 里（v3 有、小猪没有）
                pass
            missing += max(0, len([u for u in ch]) - len(found))
        con.commit()
    finally:
        con.close()
    return {"marked": marked, "already": already, "missing": missing, "note": note}


def download_missing(pig_db, urls, base_dir, log=None, timeout: int = 25) -> dict:
    """只给这些 URL 对应的图下载本地文件（本地已有就跳过）。"""
    urls = [u for u in (urls or []) if u]
    base = Path(base_dir)
    ok = skip = fail = 0
    errors: list[str] = []
    if not urls:
        return {"ok": True, "downloaded": 0, "skipped": 0, "failed": 0, "errors": []}
    con = _pig_conn(pig_db)
    try:
        for n, url in enumerate(urls, 1):
            rows = list(con.execute(
                "SELECT rowid AS rid, 本地路径, goods_id FROM 图片 WHERE url=?", (url,)))
            if not rows:
                fail += 1
                if len(errors) < 10:
                    errors.append("商品.db 里没有这张图：%s" % url[:80])
                continue
            r = rows[0]
            lp = (r["本地路径"] or "").strip()
            if lp and os.path.exists(lp):
                skip += 1
                continue
            if not url.lower().startswith("http"):
                fail += 1
                if len(errors) < 10:
                    errors.append("%s 不是 http 地址" % url[:80])
                continue
            ext = ".jpg"
            for e in (".jpeg", ".png", ".webp", ".gif"):
                if e in url.lower():
                    ext = e
                    break
            gid = str(r["goods_id"] or "0")
            dst = base / gid / (hashlib.md5(url.encode()).hexdigest()[:16] + ext)
            got = False
            last = ""
            for _try in range(2):
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0", "Referer": "https://item.taobao.com/"})
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        data = resp.read()
                    if len(data) < 1024:
                        raise RuntimeError("太小：%d 字节" % len(data))
                    dst.write_bytes(data)
                    got = True
                    break
                except Exception as e:      # noqa: BLE001
                    last = "%s: %s" % (type(e).__name__, e)
            if got:
                ok += 1
                con.execute("UPDATE 图片 SET 本地路径=?, 下载状态='成功' WHERE rowid=?",
                            (str(dst), r["rid"]))
            else:
                fail += 1
                if len(errors) < 10:
                    errors.append("%s %s" % (gid, last or "下载失败"))
            if log and n % 5 == 0:
                try:
                    log("下载命中图 %d/%d · 成功 %d 跳过 %d 失败 %d" % (n, len(urls), ok, skip, fail))
                except Exception:       # noqa: BLE001
                    pass
        con.commit()
    finally:
        con.close()
    return {"ok": True, "downloaded": ok, "skipped": skip, "failed": fail, "errors": errors}


# ============================================================
#  汇总：这一步到底要处理多少张
# ============================================================
def plan(c, pig_db, pids=None, limit: int = 0, words=None) -> dict:
    """算出瘦身计划（只读，不改任何库）。`words` = 当前合并词库的**词列表**（用于复核旧 OCR 原文）。"""
    pids = [int(x) for x in (pids or [])]
    if not pids:
        return {"ok": False, "error": "必须指定商品（pids 为空）", "mode": MODE_EMPTY}
    prods = products_v3(c, pids)
    if not prods:
        return {"ok": False, "error": "这些商品在 v3 库里不存在", "mode": MODE_EMPTY}
    goods_by_pid = {p["product_id"]: p["source_product_id"] for p in prods}
    cls = classify(c, pids, words=words)
    for key in ("hit", "clean", "unknown"):
        for it in cls[key]:
            it["goods_id"] = goods_by_pid.get(it["product_id"], "")

    if cls["images_total"] == 0:
        return {"ok": True, "mode": MODE_EMPTY, "products": len(prods),
                "images_total": 0, "ocr_rows": 0,
                "hit_images": 0, "clean_images": 0, "unknown_images": 0,
                "will_process": 0, "skip_images": 0, "hit_products": [],
                "hit": [], "clean": [], "unknown": [],
                "note": "这批商品在 v3 库里没有图，没什么可做的"}

    if cls["ocr_rows"] == 0:
        return {"ok": True, "mode": MODE_FALLBACK, "products": len(prods),
                "images_total": cls["images_total"], "ocr_rows": 0,
                "hit_images": 0, "clean_images": 0,
                "unknown_images": len(cls["unknown"]),
                "will_process": cls["images_total"], "skip_images": 0,
                "hit_products": [], "hit": [], "clean": [], "unknown": cls["unknown"],
                "note": "本包没跑过第 3 步精筛 OCR（v3_image_ocr 里没有这批商品的记录）"
                        " —— 已回落到全量扫描"}

    hit = cls["hit"]
    unknown = cls["unknown"]
    clean = cls["clean"]
    will = len(hit) + len(unknown)
    if limit:
        will = min(will, int(limit))
    hit_products = sorted({it["goods_id"] for it in (hit + unknown) if it["goods_id"]})
    note = ("只处理第 3 步命中的图：命中 %d 张 + 未判定 %d 张，已判干净 %d 张跳过"
            % (len(hit), len(unknown), len(clean)))
    return {"ok": True, "mode": MODE_SLIM, "products": len(prods),
            "images_total": cls["images_total"], "ocr_rows": cls["ocr_rows"],
            "hit_images": len(hit), "clean_images": len(clean),
            "unknown_images": len(unknown),
            "will_process": will, "skip_images": len(clean),
            "hit_products": hit_products,
            "hit": hit, "clean": clean, "unknown": unknown,
            "note": note}
