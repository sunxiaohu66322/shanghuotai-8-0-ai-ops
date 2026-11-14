# -*- coding: utf-8 -*-
"""v3 · 抖店导出（16 列，一 SKU 一行）

模板依据：本机真实 `抖店模板二.xlsx`（实测结构，不是照文档猜的）
  行1   A1 合并 —— 模板说明三行文字（必须原样保留）
  行2   表头 16 列
  行3+  数据行：**一个 SKU 一行**

实测出来的行模型（与微信 19 列不同，别混用）：
  商品ID                                  → **每行都填**（模板示例里 4 个 SKU 行都写 31054）
  商品标题 / 商品分类 / 轮播图1-3 /
  商品详情图 / 商品属性                    → **只在该商品的首行填**，其余 SKU 行留空
  SKU规格名称 / 规格1-3 / SKU编码 /
  SKU库存 / SKU拼单价 / SKU预览图          → 每行都填

字段格式（实测）：
  · 商品详情图：多个 URL 用**半角逗号**分隔
  · 商品属性  ：`键:值；键:值`（**全角分号**）
  · 轮播图只有 3 列 → 超出 3 张**不静默丢弃**，写进 warn 并记入本地物料包

红线：
  · 模板没有视频列 ⇒ **绝不加列**；视频仍留在 v3_image 与本地物料包里
  · 图片 URL 只认 `packaging._pick_url`（被替换但未上传 ⇒ 返回空 ⇒ 记「待上传」）
    —— 绝不把本地路径当公网地址，也绝不回落原图冒充新版
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from . import packaging

COLS = [
    "商品ID", "商品标题", "商品分类",
    "商品轮播图1", "商品轮播图2", "商品轮播图3",
    "SKU规格名称", "规格1", "规格2", "规格3",
    "SKU编码", "SKU库存", "SKU拼单价", "SKU预览图",
    "商品详情图", "商品属性",
]
CAROUSEL_MAX = 3


# ---------------------------------------------------------------- 取数助手

def _json_load(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if not v:
        return None
    try:
        import json
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return None


def spec_values(sku: dict) -> list[str]:
    """SKU 的规格值列表（最多 3 级），按 spec_json 的原始顺序，**不重排、不猜**。

    spec_json 形如 [{"key":"颜色","value":"枫叶红棕"},{"key":"尺码","value":"36"}]
    没有 spec_json 时退回 parse `spec` 的 "颜色:x/尺码:y"。两者都没有 ⇒ 空列表。
    """
    sj = _json_load(sku.get("spec_json"))
    out: list[str] = []
    if isinstance(sj, list):
        for it in sj:
            if isinstance(it, dict) and str(it.get("value") or "").strip():
                out.append(str(it["value"]).strip())
    if not out:
        s = str(sku.get("spec") or "")
        for part in s.split("/"):
            if ":" in part:
                k, _, v = part.partition(":")
                if v.strip():
                    out.append(v.strip())
            elif part.strip():
                out.append(part.strip())
    return out[:3]


def attr_text(p: dict) -> str:
    """商品属性 → `键:值；键:值`（全角分号）。只搬源数据里有的键，不补猜。"""
    at = _json_load(p.get("attributes")) or {}
    if not isinstance(at, dict):
        return ""
    param = at.get("param")
    if not isinstance(param, dict):
        param = {}
    pairs = []
    for k, v in param.items():
        if str(k).startswith("_"):
            continue
        if v is None or str(v).strip() == "":
            continue
        pairs.append("%s:%s" % (str(k).strip(), str(v).strip()))
    return "；".join(pairs)


def _category(p: dict) -> tuple[str, str]:
    """返回 (抖店分类文本, 警告)。来源类目只作参考，缺就留空并说明，不编造。"""
    cp = str(p.get("category_path") or "").strip()
    if not cp:
        return "", "分类空缺（源数据无类目路径，需人工在 4.2 里映射后再导）"
    norm = cp.replace(">", " > ").replace("  >  ", " > ").strip()
    return norm, ""


def _title(conn: sqlite3.Connection, p: dict, store_id: str) -> tuple[str, str]:
    row = conn.execute(
        "SELECT title_text FROM v3_title_variant WHERE product_id=? AND store_id=?"
        " AND status='selected' ORDER BY variant_id DESC LIMIT 1",
        (p["product_id"], store_id)).fetchone()
    if row and (row["title_text"] or "").strip():
        return row["title_text"].strip(), ""
    t = (p.get("original_title") or "").strip()
    return t, "" if t else "标题为空"


# ---------------------------------------------------------------- 行拼装

def build_rows(conn: sqlite3.Connection, p: dict, store_id: str,
               imgs: list[dict]) -> tuple[list[list], list[str], dict]:
    """一个商品 → (数据行列表, 警告列表, 摘要)。数据行已按 16 列排好。

    行数与 v3_sku 条数一致：**一 SKU 一行**；没有 SKU 的商品出 1 行（SKU 列留空）。
    """
    warn: list[str] = []
    pid_src = str(p.get("source_product_id") or p.get("product_id") or "")

    title, tw = _title(conn, p, store_id)
    if tw:
        warn.append(tw)
    category, cw = _category(p)
    if cw:
        warn.append(cw)

    # 轮播图 = 全部 role='main' 按 sequence（主图排第一）—— 与微信口径同源
    main_id = p.get("main_image_id")
    by_id = {i["image_id"]: i for i in imgs}
    ordered_main = sorted([i for i in imgs if i.get("role") == "main"],
                          key=lambda x: (x.get("sequence") or 0, x["image_id"]))
    main = by_id.get(main_id) if main_id else None
    if main is None and ordered_main:
        main = ordered_main[0]
    carousel_all: list[str] = []
    if main:
        # ★ 空位也占位：被替换未上传 ⇒ 这一格留空，后面的图**不许顶上来**
        carousel_all.append(packaging._pick_url(main))
    for i in ordered_main:
        if main is not None and i["image_id"] == main["image_id"]:
            continue          # 选定的那张已经占了第一格（否则会重复占位）
        u = packaging._pick_url(i)
        if u and u in carousel_all:
            continue          # 同一张图重复出现，跳过（空位不参与去重）
        carousel_all.append(u)
    carousel = carousel_all[:CAROUSEL_MAX]
    dropped = len(carousel_all) - len(carousel)
    if dropped > 0:
        warn.append("轮播图共 %d 张，模板只有 3 列，%d 张未进表（已留在本地物料包）"
                    % (len(carousel_all), dropped))
    n_gap_c = sum(1 for u in carousel if not u)
    if n_gap_c:
        warn.append("轮播有 %d 格待上传（已留空占位，后面的图没有顶上来）" % n_gap_c)
    if not carousel:
        warn.append("轮播图为空（没有已确认且可用的线上地址；被替换未上传的图不算）")

    # 详情图：role='detail'，按 sequence，逗号分隔（**保留空位**）
    detail_urls = [packaging._pick_url(i)
                   for i in sorted([x for x in imgs if x.get("role") == "detail"],
                                   key=lambda x: (x.get("sequence") or 0, x["image_id"]))]
    detail_cell = ",".join(detail_urls)
    n_detail_all = len([x for x in imgs if x.get("role") == "detail"])
    n_gap_d = sum(1 for u in detail_urls if not u)
    if n_gap_d:
        warn.append("详情图 %d 张里有 %d 张待上传（已留空占位，未压缩顺序）"
                    % (n_detail_all, n_gap_d))

    attrs = attr_text(p)
    if not attrs:
        warn.append("商品属性为空（模板说明第 3 条：非强校验，为空也可导入）")

    # SKU 图：按 sku_id 精确对，**不按顺序对**（防串位）
    sku_imgs: dict[str, str] = {}
    for i in imgs:
        if i.get("role") != "sku":
            continue
        sid = str(i.get("sku_id") or "")
        if not sid:
            continue
        u = packaging._pick_url(i)
        if u and sid not in sku_imgs:
            sku_imgs[sid] = u

    skus = [dict(r) for r in conn.execute(
        "SELECT sku_id, seq, spec, spec_json, sku_code, price, stock"
        " FROM v3_sku WHERE product_id=? ORDER BY seq, sku_id", (p["product_id"],))]
    if not skus:
        warn.append("该商品没有 SKU 记录，只出一行（SKU 列留空）")
        skus = [{"sku_id": "", "spec": "", "spec_json": "", "sku_code": "",
                 "price": p.get("price_min"), "stock": None}]

    # 商品级最低价：SKU 自身没有价格时的**唯一**合法回落（抖店「SKU拼单价」必填）
    pid_price = p.get("price_min")
    price_gap: list[str] = []

    rows: list[list] = []
    for idx, s in enumerate(skus):
        first = idx == 0
        vals = spec_values(s)
        name = " ".join(vals) if vals else str(s.get("sku_code") or "").strip()
        if not name:
            name = "默认规格"
        img = sku_imgs.get(str(s.get("sku_id") or "")) or ""
        if not img:
            # 没有专属 SKU 图时用主图（模板要求每行都有预览图，用主图是**可解释的回落**，
            # 不是编造；同时记 warn 让人知道这张不是专属图）
            img = carousel[0] if carousel else ""
            if img:
                warn.append("SKU「%s」没有专属预览图，已用主图代替" % name)
        vals3 = (vals + ["", "", ""])[:3]
        # ★ 2026-10-29 修：原来 SKU 无价格就直接留空 —— 而抖店「SKU拼单价」是必填，
        #   留空必然整行导入失败（36 项验收里唯一的一条真 bug）。
        #   现在回落到**商品级最低价**（源数据本来就有的数），并逐条记 warn 让人知道这不是该 SKU 自己的价。
        sku_price = s.get("price")
        if sku_price is None or str(sku_price).strip() == "":
            sku_price = pid_price
            if sku_price is None or str(sku_price).strip() == "":
                price_gap.append(name)
            else:
                price_gap.append("%s(回落 %s)" % (name, sku_price))
        rows.append([
            pid_src,
            title if first else "",
            category if first else "",
            (carousel[0] if len(carousel) > 0 else "") if first else "",
            (carousel[1] if len(carousel) > 1 else "") if first else "",
            (carousel[2] if len(carousel) > 2 else "") if first else "",
            name, vals3[0], vals3[1], vals3[2],
            str(s.get("sku_code") or ""),
            "" if s.get("stock") is None else s.get("stock"),
            "" if sku_price is None else sku_price,
            img,
            detail_cell if first else "",
            attrs if first else "",
        ])

    if price_gap:
        warn.append("SKU 拼单价有 %d 行源数据为空，已回落到商品最低价：%s"
                    % (len(price_gap), "、".join(price_gap[:5])))

    meta = {"product_id": p["product_id"], "source_product_id": pid_src,
            "rows": len(rows), "skus": len(skus),
            "carousel_in_table": len(carousel), "carousel_total": len(carousel_all),
            "carousel_dropped": dropped,
            "detail_in_table": len(detail_urls), "detail_total": n_detail_all,
            "sku_img_matched": len(sku_imgs),
            "videos_in_library": len([x for x in imgs if x.get("role") == "video"])}
    return rows, warn, meta


# ---------------------------------------------------------------- 写文件

def export_xlsx(template: str, out_path: str, blocks: list[list[list]]) -> dict:
    """以真实模板为基底写 xlsx：保留行1 说明、行2 表头，从行3 起写数据。

    blocks = [[row16, row16, ...], ...] 每个商品一块。模板行 3 起的示例数据会被清掉
    （模板说明第 2 条要求「导入前请删除」示例）。
    """
    import openpyxl
    if not os.path.isfile(template):
        return {"ok": False, "error": "找不到模板：%s" % template}
    wb = openpyxl.load_workbook(template)
    ws = wb.active
    if ws.max_row >= 3:
        ws.delete_rows(3, ws.max_row - 2)
    r = 3
    n = 0
    for block in blocks:
        for row in block:
            for c, v in enumerate(row, 1):
                if v is None or v == "":
                    continue          # 空格子保持模板样式，不写空串
                ws.cell(r, c).value = v
            r += 1
            n += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    wb.save(out_path)
    return {"ok": True, "path": out_path, "rows": n,
            "cols": ws.max_column, "header_row": 2}


def collect(conn: sqlite3.Connection, pids: list[int], store_id: str = "default"
            ) -> dict:
    """把若干商品拼成一份抖店导出计划（不写文件，先给界面看缺什么）。"""
    from . import store
    blocks, warns_all, metas = [], [], []
    for pid in pids:
        p = store.get_product(conn, int(pid))
        if not p:
            warns_all.append({"product_id": pid, "warns": ["商品不存在"]})
            continue
        imgs = store.list_images(conn, int(pid))
        rows, warn, meta = build_rows(conn, p, store_id, imgs)
        blocks.append(rows)
        metas.append(meta)
        if warn:
            warns_all.append({"product_id": pid, "warns": warn})
    return {"ok": True, "blocks": blocks, "warns": warns_all, "metas": metas,
            "products": len(blocks), "rows": sum(len(b) for b in blocks)}
