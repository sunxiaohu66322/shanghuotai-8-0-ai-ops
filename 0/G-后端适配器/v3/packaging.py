# -*- coding: utf-8 -*-
"""v3 · 出包（P4）与发布结果台账

两件事：
  ① 用**处理后的**数据（选定的标题 + 确认过的主图）生成 19 列微信小店 CSV
     —— 列结构与 `server/export.py` 完全一致（那份已经 19/19 对上过真实模板）
  ② 把"哪些图还没有远程 URL（即还没上图床）"单独列出来
     —— **不假装上传成功**。图床是外部门，本程序只交代缺什么。

台账：v3_publish 记录每条商品的发布结果，支持人工回灌，回灌后才算闭环。
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime
from typing import Any

from .. import export
from . import images, store, titles

# 与 server/export.py 的 EXPORT_COLUMNS 逐列一致（不要改顺序、不要加列）
COLUMNS = [
    "商品ID", "商品名称", "品牌", "分类", "库存",
    "团购价格", "单买价格",
    "商品主图", "商品轮播图", "商品详情图", "商品描述", "商品视频", "商品属性",
    "SKUID", "SKU名称", "SKU库存", "SKU团购价格", "SKU单买价格", "SKU预览图",
]


def _out_dir() -> str:
    os.makedirs(store.OUT_DIR, exist_ok=True)
    return store.OUT_DIR


def _pick_url(img: dict) -> str:
    """优先处理后的远程 URL（图床），其次原图 URL。本地路径不能进这个字段。

    ★ 2026-10-25 阶段二：被替换过的图（current_local_path 已换成新文件）
      **不许再回落到 original_url** —— 那等于悄悄把原图当成品导出去。
      这种情况返回空串，由上层 warn「待图床」，让用户看得见。
    """
    cur = (img.get("current_remote_url") or "").strip()
    if cur.startswith("http"):
        return cur
    cl = (img.get("current_local_path") or "").strip()
    ol = (img.get("original_local_path") or "").strip()
    replaced = (img.get("status") == "replaced") or (
        bool(cl) and bool(ol) and os.path.normcase(cl) != os.path.normcase(ol))
    if replaced:
        return ""
    orig = (img.get("original_url") or "").strip()
    if orig.startswith("http"):
        return orig
    return ""


def _sku_arrays(skus: list[dict]) -> dict:
    d = {"ids": [], "names": [], "stocks": [], "gp": [], "sp": [], "pv": []}
    for s in skus or []:
        if not isinstance(s, dict):
            continue
        d["ids"].append(str(s.get("sku_id") or s.get("id") or ""))
        d["names"].append(str(s.get("name") or s.get("spec") or ""))
        d["stocks"].append(str(s.get("stock") if s.get("stock") not in (None, "") else ""))
        price = s.get("price")
        try:
            ps = "%.2f" % float(price) if price not in (None, "") else ""
        except Exception:
            ps = str(price or "")
        d["gp"].append(ps)
        d["sp"].append(ps)
        d["pv"].append(str(s.get("image") or ""))
    return d


def _brand_of(p: dict) -> str:
    attrs = p.get("attributes") or {}
    for k in ("品牌", "brand", "Brand"):
        if isinstance(attrs, dict) and attrs.get(k):
            return str(attrs[k])
    pl = p.get("original_payload") or {}
    if isinstance(pl, dict):
        for k in ("品牌", "brand"):
            if pl.get(k):
                return str(pl[k])
    return ""


def build_row(conn: sqlite3.Connection, p: dict, store_id: str,
              imgs: list[dict]) -> tuple[list, list]:
    """一个商品 → (19 列, 警告列表)。

    ★ 19 列的拼装口径只认 `server/export.py: assemble_row`（轮播图=全部主图、
      价格缺了从 SKU 回落、库存优先 SKU 求和）—— 这里只负责取数与告警，
      不自己另写一套，免得又和模板对不上。
    """
    warn: list[str] = []
    t = conn.execute(
        "SELECT title_text FROM v3_title_variant WHERE product_id=? AND store_id=?"
        " AND status='selected' ORDER BY variant_id DESC LIMIT 1",
        (p["product_id"], store_id)).fetchone()
    title = (t["title_text"] if t else p.get("original_title") or "")
    if not t:
        warn.append("没选标题，本次用原标题（原样输出，不算已优化）")

    main_id = p.get("main_image_id")
    by_id = {i["image_id"]: i for i in imgs}
    ordered_main = sorted([i for i in imgs if i["role"] == "main"],
                          key=lambda x: x["sequence"])
    main = by_id.get(main_id) if main_id else None
    if main is None:
        main = ordered_main[0] if ordered_main else None
        if main:
            warn.append("main_image_id 为空，按第一张 main 输出")
    # 选定的主图排在第一，其余主图跟着（模板：轮播图 = 全部主图）
    # ★ 空位也要占位（2026-10-25）：被替换未上传的那张不能让后面的图顶上来，
    #   否则「轮播图1」会悄悄变成另一张图。空串交给 export._urls(keep_empty=True)。
    mains = [_pick_url(main)] if main else []
    for i in ordered_main:
        if main is not None and i["image_id"] == main["image_id"]:
            continue                      # 选定的那张已经占了第一个位置
        u = _pick_url(i)
        if u and u in mains:
            continue                      # 同一张图重复，跳过（空位不参与去重）
        mains.append(u)
    if not any(mains):
        warn.append("主图没有可用 URL（本地路径不能直接上架，需先上图床）")
    elif any(not u for u in mains):
        warn.append("主图/轮播有 %d 个位置待上传（留空占位，未用原图顶替）"
                    % sum(1 for u in mains if not u))

    carousel = [_pick_url(i) for i in imgs if i["role"] == "carousel"]
    detail = [_pick_url(i) for i in imgs if i["role"] == "detail"]
    videos = [_pick_url(i) for i in imgs if i["role"] == "video"]
    sku_imgs = [_pick_url(i) for i in imgs if i["role"] == "sku"]

    missing = [(i["role"], i["image_id"]) for i in imgs
               if i["role"] in ("main", "carousel", "detail", "sku") and not _pick_url(i)]
    if missing:
        warn.append("%d 张图没有远程 URL（待图床）" % len(missing))

    pl = p.get("original_payload") or {}
    if not isinstance(pl, dict):
        pl = {}
    skus = pl.get("skus") or []
    attrs = p.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}

    # ★ 2026-10-25 修：顶层 `skus` 在真卡里**恒为空数组**（采集卡只给 _extra._raw），
    #   所以 SKU 六列一直出空。改为优先读 `v3_sku` 明细表（由 v3.sku 从
    #   _raw.productSkus + _extra.skuSpecs 搬出来的），payload 里真有才用它。
    sku_src = "original_payload.skus"
    if not skus:
        rows = conn.execute(
            "SELECT sku_id, spec, sku_code, price, price_origin, stock, img_url"
            " FROM v3_sku WHERE product_id=? ORDER BY seq, sku_id", (p["product_id"],)).fetchall()
        if rows:
            skus = [{"sku_id": r["sku_id"], "name": r["spec"] or r["sku_code"] or "",
                     "spec": r["spec"] or "", "stock": r["stock"],
                     "price": r["price"] if r["price"] is not None else r["price_origin"],
                     "image": r["img_url"] or ""} for r in rows]
            sku_src = "v3_sku"
    if not skus:
        warn.append("没有 SKU 明细（原始卡顶层 skus 为空且 v3_sku 无行）")

    desc = (pl.get("描述") or pl.get("description") or "")
    if not desc:
        raw = ((pl.get("_extra") or {}).get("_raw") or {})
        desc = ((raw.get("detail") or {}).get("desc") or "")
    if not desc:
        # 详情图 OCR 是**证据**，不是商家描述 —— 单独标记，绝不冒充
        n_ocr = conn.execute(
            "SELECT COUNT(*) FROM v3_image_ocr WHERE product_id=? AND full_text<>''",
            (p["product_id"],)).fetchone()[0]
        if n_ocr:
            warn.append("源侧无商品描述；已有 %d 张详情图 OCR 可作证据（不作为描述输出）" % n_ocr)

    stock = pl.get("库存")
    if stock in (None, ""):
        stock = ((pl.get("_extra") or {}).get("totalStock"))
    if stock in (None, ""):
        stock = (attrs.get("stock") or {}).get("stockNum") if isinstance(attrs.get("stock"), dict) else None
    if stock in (None, "") and skus:
        try:
            stock = sum(int(s.get("stock") or 0) for s in skus)
        except Exception:  # noqa: BLE001
            stock = None
    if stock in (None, ""):
        warn.append("库存未知（源里没有 totalStock / stockNum，SKU 也无法求和）")

    brand = _brand_of(p)
    category = p.get("category_path") or ""
    if not brand:
        warn.append("品牌：来源未提供（不按标题猜）")
    if not category:
        warn.append("分类：来源只有 ID 路径（_extra.catId），平台要中文类目，未映射前留空")

    row, w = export.assemble_row({
        "pid": p.get("source_product_id") or p.get("product_id") or "",
        "title": title,
        "brand": brand,
        "category": category,
        "stock": stock if stock is not None else "",
        "price_min": p.get("price_min"),
        "price_max": p.get("price_max"),
        "main": mains,
        # ★ 2026-10-25：**原样传，不在这里过滤空串** ——
        #   过滤会把「待上传」那格删掉，后面的图就顶上来了（位置错位）。
        #   export._urls(keep_empty=True) 会保留空位，拼出来是 `url1,,url3`。
        "carousel": carousel,
        "detail": detail,
        "desc": desc or "",
        "video": (videos[0] if videos else (pl.get("视频") or "")),
        "props": attrs,
        "skus": skus,
        "sku_images": sku_imgs,
        "_sku_src": sku_src,
    })
    warn.extend(w)
    return row, warn


def export_package(conn: sqlite3.Connection, pids: list[int] | None = None,
                   store_id: str = "default", limit: int = 5000,
                   include_blocked: bool = False,
                   actor: str = "packaging") -> dict:
    """生成 19 列 CSV + manifest + 待上传图清单。

    ★ 默认**排除 blocked 与 provenance='fixture'**：
      · blocked = 被细筛拦下的，不许混进商品包
      · fixture = 自检/测试数据，**绝不许出现在交付给外部软件的包里**（任务书 P0-A6）
      两者都在返回里报数，少了几条必须能看到原因，不能悄悄少。
    """
    if pids:
        rows_p = []
        for pid in pids:
            p = store.get_product(conn, pid)
            if p:
                rows_p.append(p)
        skipped_blocked = 0
    else:
        rows_p = store.list_products(conn, limit=limit)
        n0 = len(rows_p)
        if not include_blocked:
            rows_p = [p for p in rows_p if p.get("status") != "blocked"]
        skipped_blocked = n0 - len(rows_p)
    # fixture 一律不进包（人工指定 pids 时也拦）
    n_fx = len(rows_p)
    rows_p = [p for p in rows_p if (p.get("provenance") or "") != "fixture"]
    skipped_fixture = n_fx - len(rows_p)
    if not rows_p:
        return {"ok": False, "error": "没有可出包的商品（先导入；被拦下的不算）"}

    out_rows: list[list] = []
    all_warn: list[dict] = []
    pending_upload: list[dict] = []
    pending_review: list[dict] = []
    for p in rows_p:
        pid = p["product_id"]
        imgs = store.list_images(conn, pid)
        row, warn = build_row(conn, p, store_id, imgs)
        out_rows.append(row)
        prof = p.get("profile") or {}
        if isinstance(prof, dict) and prof.get("needs_review") and not prof.get("confirmed"):
            pending_review.append({"product_id": pid,
                                   "title": (p.get("original_title") or "")[:40],
                                   "confidence": prof.get("confidence"),
                                   "reasons": (prof.get("review_reasons") or [])[:3]})
        if warn:
            all_warn.append({"product_id": pid, "title": p.get("original_title")[:40],
                             "warnings": warn})
        for i in imgs:
            if i["role"] in ("main", "carousel", "detail", "sku") and not _pick_url(i):
                lp, ok = images.resolve_local(i)
                pending_upload.append({
                    "product_id": pid, "image_id": i["image_id"], "role": i["role"],
                    "sequence": i["sequence"], "local_path": lp, "file_exists": ok,
                    "original_url": i.get("original_url") or "",
                })

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = _out_dir()
    name = "v3_商品包_%s.csv" % ts
    path = os.path.join(d, name)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    w.writerows(out_rows)
    with open(path, "wb") as f:
        f.write(buf.getvalue().encode("gbk", errors="replace"))

    manifest = {
        "generated_at": store.now(), "file": name, "store_id": store_id,
        "count": len(out_rows), "columns": COLUMNS,
        "with_warnings": len(all_warn), "pending_upload_images": len(pending_upload),
        "skipped_blocked": skipped_blocked,
        "skipped_fixture": skipped_fixture,
        "pending_review": len(pending_review),
        "pending_review_sample": pending_review[:50],
        "note": "编码 GBK（对齐微信小店模板）；本地路径不会出现在任何 URL 列；"
                "blocked 的商品默认不导出",
        "warnings": all_warn[:200],
    }
    mpath = os.path.join(d, name.replace(".csv", ".manifest.json"))
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    upath = os.path.join(d, name.replace(".csv", "_待上传图.csv"))
    if pending_upload:
        with open(upath, "w", encoding="utf-8-sig", newline="") as f:
            w2 = csv.DictWriter(f, fieldnames=list(pending_upload[0].keys()))
            w2.writeheader()
            w2.writerows(pending_upload)

    for p in rows_p:
        if p.get("status") not in ("published",):
            store.set_product_status(conn, p["product_id"], "packaged")
    store.audit(conn, "package.export", "v3_product", "", None, manifest, actor=actor)
    conn.commit()
    return {"ok": True, "path": path, "name": name, "manifest": mpath,
            "pending_upload_csv": upath if pending_upload else "",
            "count": len(out_rows), "pending_upload_images": len(pending_upload),
            "skipped_blocked": skipped_blocked,
        "skipped_fixture": skipped_fixture,
        "pending_review": len(pending_review),
        "pending_review_sample": pending_review[:50],
            "warnings": all_warn[:20], "warning_count": len(all_warn)}


# ---------------------------------------------------------------- 台账

def record_publish(conn: sqlite3.Connection, pid: int, store_id: str = "default",
                   outcome: str = "unknown", external_listing_id: str = "",
                   note: str = "", actor: str = "publish") -> dict:
    """人工回灌：上架完把结果写回来。**没有第三方返回结果时不要标 published。**"""
    p = store.get_product(conn, pid)
    if not p:
        return {"ok": False, "error": "没有这个商品"}
    allowed = ("unknown", "submitted", "published", "rejected", "removed")
    if outcome not in allowed:
        return {"ok": False, "error": "outcome 只能是 %s" % "|".join(allowed)}
    tv = conn.execute("SELECT variant_id FROM v3_title_variant WHERE product_id=? AND store_id=?"
                      " AND status='selected' ORDER BY variant_id DESC LIMIT 1",
                      (pid, store_id)).fetchone()
    conn.execute(
        "INSERT INTO v3_publish(product_id, store_id, title_variant_id, image_id,"
        " external_listing_id, published_at, outcome, note, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (pid, store_id, tv["variant_id"] if tv else None, p.get("main_image_id"),
         external_listing_id,
         store.now() if outcome in ("published", "submitted") else None,
         outcome, note, store.now()))
    if outcome == "published":
        store.set_product_status(conn, pid, "published")
    conn.commit()
    return {"ok": True, "product_id": pid, "outcome": outcome,
            "note": "台账已记账；已上架商品下次选品时应优先跳过（防重复铺货只有真实反馈才能闭环）"}


def list_publish(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT r.*, p.original_title FROM v3_publish r"
            " LEFT JOIN v3_product p ON p.product_id=r.product_id"
            " ORDER BY r.record_id DESC LIMIT ?", (limit,)):
        out.append(dict(r))
    return out


def list_out_files() -> list[dict]:
    d = _out_dir()
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        p = os.path.join(d, f)
        out.append({"name": f, "path": p, "size": os.path.getsize(p),
                    "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")})
    return out
