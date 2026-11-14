# -*- coding: utf-8 -*-
"""v3 数据准入与等级（L0–L4）—— 2026-10-24 新增

主人原话：「商品数量对了、数据却是空的，或者 URL 能打开、商品详情却是假的问题，
后续不能反复出现。」所以这里把三件事彻底分开，且**全部按库里的真实记录计算**：

  ① 初筛信息（L0）  标题 / 店铺名 / URL —— 只在第一步用，**没有资格**进商品库
  ② 正式商品数据（L1）购买或明确导入的商品卡文件里**真实存在**的字段
  ③ 图片证据（L2/L3/L4）真下载的文件、OCR 文字与坐标、违规词命中

等级定义（只升不降由证据决定，不由人写）：
  L0  初筛数据       只有标题/店铺/URL，没有商品卡字段
  L1  正式商品卡     有源商品 ID 或真实媒体引用/SKU/属性（来自导入文件）
  L2  资源已下载     至少一张图真的落在磁盘上（按 os.path.isfile 判，不信库里的路径字符串）
  L3  证据已提取     有 OCR 成功记录 **且带坐标**（boxes_json 非空）
  L4  精筛就绪       L3 且关键字段不缺（标题 + 主图本地 + 价格或 SKU）

铁律：
  · **媒体 URL 数 ≠ 已下载数**。url 只证明"引用存在"。
  · 来源文件里没有的字段，写「来源未提供」，绝不因为页面能打开就去补。
  · 等级由 records 现算，`v3_product.data_grade` 只是缓存，不是真相。
"""
from __future__ import annotations

import json
import os
import sqlite3

L0, L1, L2, L3, L4 = "L0", "L1", "L2", "L3", "L4"
_ORDER = {L0: 0, L1: 1, L2: 2, L3: 3, L4: 4}

ROLE_CN = {"main": "主图", "detail": "详情图", "sku": "SKU图", "video": "视频",
           "carousel": "轮播图", "other": "其他"}

# 字段状态的统一词表（界面与报告共用，不要各写一套）
SRC_MISSING = "来源未提供"
NOT_DOWNLOADED = "未下载"
DOWNLOAD_FAILED = "下载失败"
DOWNLOADED = "已下载"
OCR_NOT_RUN = "OCR未执行"
OCR_FAILED = "OCR失败"
OCR_OK = "OCR已完成"
OCR_NO_BOX = "OCR无坐标"


def _cols(conn, table: str) -> list[str]:
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def unj(v, default=None):
    if v in (None, "", b""):
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return default


def _exists(p: str) -> bool:
    return bool(p) and os.path.isfile(p)


# ---------------------------------------------------------------- 库内测量

def media_of(conn, pid: int) -> dict:
    """这件商品的媒体引用实况（URL 层 / 文件层 / 下载态 分开算）。"""
    out = {"refs": 0, "roles": {}, "urls": 0, "unique_urls": 0,
           "local_refs": 0, "pending": 0, "failed": 0, "no_url": 0}
    cols = _cols(conn, "v3_image")
    if not cols:
        return out
    sel = ["role", "COALESCE(original_url,'')", "COALESCE(original_local_path,'')",
           "COALESCE(current_local_path,'')"]
    sel.append("COALESCE(dl_state,'')" if "dl_state" in cols else "''")
    urls: set[str] = set()
    for r in conn.execute(
            "SELECT %s FROM v3_image WHERE product_id=? ORDER BY image_id" % ",".join(sel),
            (pid,)):
        role = str(r[0] or "other")
        url, lp, cp, dl = (str(x or "") for x in r[1:5])
        out["refs"] += 1
        out["roles"][role] = out["roles"].get(role, 0) + 1
        if url:
            out["urls"] += 1
            urls.add(url)
        else:
            out["no_url"] += 1
        if _exists(cp) or _exists(lp):
            out["local_refs"] += 1
        elif dl == "failed" or str(dl).startswith("fail"):
            out["failed"] += 1
        else:
            out["pending"] += 1
    out["unique_urls"] = len(urls)
    return out


def ocr_of(conn, pid: int) -> dict:
    """OCR 实况：成功/失败/带坐标，三者分开。"""
    out = {"ok": 0, "failed": 0, "pending": 0, "boxes": 0, "hits": 0}
    if not _cols(conn, "v3_image_ocr"):
        return out
    for r in conn.execute(
            "SELECT COALESCE(state,''), COALESCE(boxes_json,''), COALESCE(hit_words,'')"
            " FROM v3_image_ocr WHERE product_id=?", (pid,)):
        st, bx, hw = str(r[0] or ""), str(r[1] or ""), str(r[2] or "")
        if st == "ok":
            out["ok"] += 1
            if bx.strip() not in ("", "[]", "null"):
                out["boxes"] += 1
        elif st in ("failed", "error"):
            out["failed"] += 1
        else:
            out["pending"] += 1
        if hw.strip() not in ("", "[]", "null"):
            out["hits"] += 1
    return out


def _field_state(val, *, source_known: bool = True) -> str:
    if val not in (None, "", [], {}, "None"):
        return "有"
    return SRC_MISSING if source_known else SRC_MISSING


def evidence(conn, pid: int) -> dict:
    """一件商品的完整证据画像（等级 + 缺失项 + 阻断原因）。**只读。**"""
    cols = _cols(conn, "v3_product")
    if not cols:
        return {"product_id": pid, "grade": L0, "ok": False, "error": "v3_product 不存在"}
    row = conn.execute("SELECT * FROM v3_product WHERE product_id=?", (pid,)).fetchone()
    if not row:
        return {"product_id": pid, "grade": L0, "ok": False, "error": "商品不存在"}
    d = dict(zip(cols, row))

    payload = unj(d.get("original_payload"), {}) or {}
    attrs = unj(d.get("attributes"), {}) or {}
    if not isinstance(attrs, dict):
        attrs = {}

    pid_str = str(d.get("source_product_id") or "").strip()
    title = str(d.get("original_title") or "").strip()
    url = str(d.get("source_url") or "").strip()
    shop = str(d.get("source_shop_name") or "").strip()
    cat_path = str(d.get("category_path") or "").strip()
    price_lo = d.get("price_min")
    desc = str(payload.get("描述") or payload.get("desc") or "").strip()

    skus = payload.get("skus") or payload.get("sku") or []
    if isinstance(skus, str):
        skus = unj(skus, []) or []
    # ★ 微信卡方言：规格在 `skuSpecs`（含价格/库存）与 `skuAttrs`（含规格值）里，
    #   没有 `skus` 键。之前只认 skus → 176 件全被判"没有SKU"，是错的。
    if not skus:
        skus = payload.get("skuSpecs") or payload.get("skuAttrs") or []
    n_sku = len(skus) if isinstance(skus, list) else 0

    # 类目：列里有中文路径就用它；没有则回落到 payload 的 catId（**只有 id 链，没有中文名**）
    cat_ids = str(payload.get("catId") or payload.get("cat_id") or "").strip()
    cat_note = ""
    if not cat_path and cat_ids:
        cat_path = cat_ids
        cat_note = "只有 id 链（%s），源文件没有中文类目名" % cat_ids

    # 视频：三种方言都认（标准 视频 / video / 微信卡 videoUrls）
    vid = (payload.get("视频") or payload.get("video") or payload.get("videoUrls")
           or payload.get("video_urls") or "")
    if isinstance(vid, list):
        vid = [x for x in vid if x]

    m = media_of(conn, pid)
    o = ocr_of(conn, pid)

    # ---- 等级（严格按证据，不看记账）
    has_card = bool(pid_str) or (m["urls"] > 0) or n_sku > 0 or len(attrs) > 0
    grade = L0
    if title and has_card:
        grade = L1
    if grade == L1 and m["local_refs"] > 0:
        grade = L2
    if grade == L2 and o["ok"] > 0 and o["boxes"] > 0:
        grade = L3
    if grade == L3 and (price_lo is not None or n_sku > 0):
        grade = L4

    # ---- 缺失项（区分「来源未提供」与「还没做」）
    missing: list[dict] = []

    def add(field: str, state: str, note: str = "") -> None:
        missing.append({"field": field, "state": state, "note": note})

    if not pid_str:
        add("源商品ID", SRC_MISSING, "导入文件里没有商品 ID，无法跨包/跨次去重")
    if not cat_path:
        add("类目", SRC_MISSING, "源头只给了类目 id 链，没有中文名")
    elif cat_note:
        add("类目中文名", SRC_MISSING, cat_note)
    if not shop:
        add("店铺名", SRC_MISSING, "商品卡文件里没有店铺字段")
    if not str(d.get("source_store_id") or "").strip():
        add("店铺类型", SRC_MISSING, "同一来源缺店铺信息，无法判旗舰/专卖/普通")
    if not desc:
        add("描述", SRC_MISSING, "源文件里描述键存在但内容为空")
    if not attrs:
        add("属性", SRC_MISSING, "源文件没有属性字段")
    if n_sku == 0:
        add("SKU", SRC_MISSING, "源文件没有规格数组")
    if price_lo is None and n_sku == 0:
        add("价格", SRC_MISSING)

    if m["refs"] == 0:
        add("媒体引用", SRC_MISSING, "源文件没有任何图片/视频 URL")
    else:
        if m["local_refs"] == 0:
            add("本地图片", NOT_DOWNLOADED, "%d 条引用，0 条落盘" % m["refs"])
        if m["failed"]:
            add("下载失败", DOWNLOAD_FAILED, "%d 条" % m["failed"])
        if m["pending"] and m["local_refs"]:
            add("待下载", NOT_DOWNLOADED, "%d 条" % m["pending"])
        if o["ok"] == 0 and m["local_refs"] > 0:
            add("OCR", OCR_NOT_RUN, "有本地图但没跑识别")
        if o["failed"]:
            add("OCR失败", OCR_FAILED, "%d 条" % o["failed"])
        if o["ok"] > 0 and o["boxes"] == 0:
            add("OCR坐标", OCR_NO_BOX, "识别成功但没存文字框，无法定位打码")
        if not vid:
            add("视频", SRC_MISSING, "源文件没有视频 URL（不算失败）")

    # ---- 进第三步的阻断原因（只有证据不足才阻断，不因缺字段一刀切）
    blocked: list[str] = []
    if grade == L0:
        blocked.append("L0 初筛信息：只有标题/店铺/URL，不是商品卡，不能进精筛")
    elif grade == L1:
        blocked.append("没有任何本地图片，图片证据为 0")
    elif grade == L2:
        blocked.append("图片已下载但未做 OCR，没有文字证据")
    elif grade == L3:
        blocked.append("OCR 有文字但缺少坐标，无法定位违规词（不阻断判定，阻断打码）")
    if grade in (L2, L3, L4) and not title:
        blocked.append("没有标题")

    return {
        "product_id": pid,
        "source_product_id": pid_str,
        "platform": str(d.get("source_platform") or ""),
        "title": title,
        "pack_code": str(d.get("pack_code") or ""),
        "source_url": url,
        "shop_name": shop,
        "shop_id": str(d.get("source_store_id") or ""),
        "category_path": cat_path,
        "price_min": price_lo, "price_max": d.get("price_max"),
        "n_sku": n_sku, "n_attr": len(attrs),
        "has_desc": bool(desc),
        "has_video": bool(vid),
        "payload_keys": sorted(payload.keys())[:40],
        "media": m, "ocr": o,
        "grade": grade, "grade_cache": str(d.get("data_grade") or ""),
        "missing": missing,
        "blocked": blocked,
        "ready": grade in (L3, L4),
    }


def grade_of(conn, pid: int) -> str:
    return evidence(conn, pid)["grade"]


def can_enter_fine(conn, pid: int) -> dict:
    """第三步准入：返回 {ok, grade, blocked}。**只有 L3/L4 才 ok。**"""
    e = evidence(conn, pid)
    return {"ok": e.get("ready", False), "grade": e["grade"],
            "blocked": e.get("blocked") or [], "missing": e.get("missing") or []}


# ---------------------------------------------------------------- 导入准入

# 只有这些键 = 典型的"第一步初筛记录"，不是商品卡
_L0_KEYS = {"title", "标题", "url", "source_url", "链接", "商品链接", "shop", "shop_name",
            "店铺", "店铺名", "店铺名称", "nick", "seller", "price_text", "价格", "备注"}


def admit_record(rec: dict) -> dict:
    """导入前准入判定。**纯 URL/标题记录一律拒收**（不写库，返回原因）。

    rec 是 importer.import_mapped 里那种归一化 dict（title/source_url/skus/props/images…）。
    判据（任一成立即为商品卡）：
      ① 有 source_product_id / product_id
      ② 有真实媒体引用（http 图片/视频）
      ③ 有 SKU 数组
      ④ 有属性 dict（非空）
    四条都不成立 → L0，拒收。
    """
    title = str(rec.get("original_title") or rec.get("title") or "").strip()
    sid = str(rec.get("source_product_id") or rec.get("product_id") or "").strip()
    urls = []
    for k in ("images_recs",):
        for img in rec.get(k) or []:
            u = str((img or {}).get("original_url") or "")
            if u.startswith("http"):
                urls.append(u)
    for k in ("main_image", "images", "detail_images", "video", "sku_images"):
        v = rec.get(k)
        if isinstance(v, str) and v.startswith("http"):
            urls.append(v)
        elif isinstance(v, list):
            urls.extend([str(x) for x in v if str(x).startswith("http")])
    skus = rec.get("skus") or rec.get("sku") or []
    if isinstance(skus, str):
        skus = unj(skus, []) or []
    props = rec.get("props") or rec.get("attributes") or {}
    if not isinstance(props, dict):
        props = unj(props, {}) or {}

    grade = L0
    why: list[str] = []
    # ★ 显式来源标记优先：第一步的卡片采集（capture_level=prescreen/coarse/card…）
    #   即使带了卡片图，也**不是**正式商品详情卡 —— 直接按 L0 拒收。
    #   实测 products.db：prescreen 198 条 / coarse 2 条 全是 0 详情图、0 SKU、0 属性；
    #   fine 5 条才有 detail_images+skus。所以只放行 fine/detail/full。
    lvl = str(rec.get("capture_level") or "").strip().lower()
    if not lvl:
        pl = rec.get("original_payload") or {}
        if isinstance(pl, dict):
            lvl = str(pl.get("capture_level") or "").strip().lower()
    if lvl and lvl not in ("fine", "detail", "full", "detail_full"):
        return {"ok": False, "grade": L0,
                "reasons": ["capture_level=%s：这是第一步的采集级别，不是商品详情卡"
                            "（只放行 fine/detail/full）" % lvl],
                "urls": 0, "skus": 0, "props": 0, "has_sid": bool(sid), "title": title}

    if sid or urls or (isinstance(skus, list) and skus) or props:
        grade = L1
    if not title:
        why.append("没有标题")
    if not sid:
        why.append("源文件没有商品 ID")
    if not urls:
        why.append("源文件没有任何媒体 URL")
    if not (isinstance(skus, list) and skus):
        why.append("源文件没有 SKU/规格")
    if not props:
        why.append("源文件没有属性")

    low_keys = set(rec.keys()) - {"images_recs"}
    if grade == L0 and low_keys and low_keys <= _L0_KEYS:
        why.append("这是第一步的初筛记录（只有标题/店铺/链接），不是商品卡")
    if grade == L0 and "初筛" not in " ".join(why):
        why.append("数据不足 L1：既无商品 ID，也无媒体/SKU/属性 —— 不是合格的详细商品卡")

    return {"ok": grade != L0, "grade": grade, "reasons": why,
            "urls": len(urls), "skus": len(skus) if isinstance(skus, list) else 0,
            "props": len(props), "has_sid": bool(sid), "title": title}


# ---------------------------------------------------------------- 报告

def audit_pack(conn, code: str, limit: int = 0) -> dict:
    """一个包逐件完整性。返回 {pack, count, by_grade, rows, summary}。"""
    pids = [int(r[0]) for r in conn.execute(
        "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]
    if not pids and _cols(conn, "v3_pack_member"):
        pids = [int(r[0]) for r in conn.execute(
            "SELECT product_id FROM v3_pack_member WHERE pack_code=? ORDER BY product_id",
            (code,))]
    if limit:
        pids = pids[:limit]
    rows = [evidence(conn, p) for p in pids]
    by_grade: dict[str, int] = {}
    for r in rows:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
    agg = {
        "products": len(rows),
        "with_local_image": sum(1 for r in rows if r["media"]["local_refs"] > 0),
        "with_ocr": sum(1 for r in rows if r["ocr"]["ok"] > 0),
        "with_boxes": sum(1 for r in rows if r["ocr"]["boxes"] > 0),
        "with_sku": sum(1 for r in rows if r["n_sku"] > 0),
        "with_attr": sum(1 for r in rows if r["n_attr"] > 0),
        "with_shop": sum(1 for r in rows if r["shop_name"]),
        "with_desc": sum(1 for r in rows if r["has_desc"]),
        "with_video": sum(1 for r in rows if r["has_video"]),
        "media_refs": sum(r["media"]["refs"] for r in rows),
        "media_local": sum(r["media"]["local_refs"] for r in rows),
        "ready_fine": sum(1 for r in rows if r["ready"]),
    }
    return {"pack": code, "count": len(rows), "by_grade": by_grade,
            "rows": rows, "summary": agg}
