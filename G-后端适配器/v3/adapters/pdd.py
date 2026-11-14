# -*- coding: utf-8 -*-
"""拼多多商品详情卡适配器（多米数据助手导出 · ``D:\\多米采集导出\\*.txt``）。

真实结构（2026-11-09 实测该目录 223 个文件、逐个 json.loads 通过）：
    顶层是拼多多商品详情接口的**原始响应 JSON**（单行、6万~17万字节、扩展名却是 .txt）：:

        goods{ goods_id, goods_name, short_name, hd_url, thumb_url,
               gallery[11~39 张]（type=2 图 / type=9 视频位）,
               decoration[floors].contents[].img_url（详情图）,
               goods_property[{key, values}], cat_id_1..4, market_price,
               quantity, sold_quantity, ... }
        sku[]        顶层（!! 不是 goods.sku，后者实测常为空）:
                     sku_id / thumb_url / quantity / spec / 各价格字段
        price{ min/max_on_sale_group_price, min/max_group_price, ... } 单位「分」
        ui.carousel_section.video.{url, high_priority_url}   主图视频
        goods.gallery[] 的 type=9 项带 video_url               轮播视频
        mall_entrance.mall_data  是**字符串化的 dict**（Python repr），店铺名藏在其中

关键取舍（不照抄微店适配器）：
  · 价格一律「分 → 元」（price.min_group_price=1390 → 13.9 元），
    market_price 只是划线价，作兜底不作首选。
  · cat_id_1..4 是**拼多多自有类目编号**，不是微信/抖店类目 →
    category.confidence=0.0、name_missing=True，并在 parse_warnings 里写明，
    绝不拿它去套微信类目。
  · 店铺名源里没有结构化字段，从 mall_data 字符串正则提 mall_name；
    提不到就是 None（不编造），与 wechat 口径一致。
  · 视频无 wdis_t 过期参数 → video_expired=None（不像微店会整批过期）。
  · 主图/详情图大量重合（实测 decoration 与 gallery 同图），按 (role,url)
    去重而非全局去重，保证 detail / sku 角色不空（精筛判据要看这三类）。

detect: 顶层有 goods(dict)+server_time，goods 带 goods_id 且 (gallery|sku|hd_url) 非空。
parse_card 输出契约见 ``wechat.py`` 顶部；下游 ``cards._rec_of`` / ``importer`` 认这套。
"""

from __future__ import annotations

import ast
import re
from typing import Any

PLATFORM = "pdd"
DISPLAY = "拼多多"


# ---------------------------------------------------------------- 识别

def detect(payload: dict) -> bool:
    """拼多多详情原始响应：goods.goods_id + (gallery | 顶层 sku | hd_url)。

    这个形状足够特异，不会误判 weidian（要 _raw/productId）或 wechat（要 productSkus）。
    """
    if not isinstance(payload, dict):
        return False
    g = payload.get("goods")
    if not isinstance(g, dict) or not g.get("goods_id"):
        return False
    if "server_time" not in payload and "ui" not in payload:
        return False
    return bool(g.get("gallery") or g.get("hd_url") or payload.get("sku"))


# ---------------------------------------------------------------- 小工具

def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _yuan(v: Any) -> float | None:
    """分 → 元。拼多多价格字段一律是分。"""
    n = _num(v)
    return round(n / 100.0, 2) if n is not None else None


# SKU 定价字段实测全是**字符串数字**（'43899'），且混着
# normal_save_price（省下多少）、old_group_price（划线价）、price（恒 0）等干扰项。
# 只认这 3 个真售价字段，按 group→normal→line 的优先序取第一个正数。
_PRICE_KEYS = ("group_price", "normal_price", "line_price")


def _sku_price(s: dict) -> float | None:
    for k in _PRICE_KEYS:
        n = _num(s.get(k))
        if n and n > 0:
            return round(n / 100.0, 2)
    return None


def _specs(s: dict) -> dict:
    """sku.specs 实测是 repr 字符串：``[{'spec_key':'款式','spec_value':'尼龙脚…'}]``。"""
    raw = s.get("specs")
    try:
        items = ast.literal_eval(raw) if isinstance(raw, str) else (raw or [])
    except (ValueError, SyntaxError):
        items = []
    out: dict[str, str] = {}
    for it in items if isinstance(items, (list, tuple)) else []:
        if isinstance(it, dict) and _s(it.get("spec_key")):
            out[_s(it["spec_key"])] = _s(it.get("spec_value"))
    if not out and _s(s.get("spec")):
        out["规格"] = _s(s["spec"])
    return out


def _shop_name(payload: dict) -> str | None:
    """mall_entrance.mall_data 实测是 Python-repr 字符串，不是 dict。"""
    md = ((payload.get("mall_entrance") or {}).get("mall_data") or "")
    if isinstance(md, dict):
        return _s(md.get("mall_name")) or None
    m = re.search(r"mall_name['\"]\s*:\s*['\"]([^'\"]+)", str(md))
    return _s(m.group(1)) or None


def _video_urls(payload: dict, g: dict) -> list[str]:
    out: list[str] = []
    cs = ((payload.get("ui") or {}).get("carousel_section") or {})
    v = cs.get("video")
    if isinstance(v, dict):
        out += [_s(v.get("url")), _s(v.get("high_priority_url"))]
    for x in (g.get("gallery") or []):
        if isinstance(x, dict):
            out.append(_s(x.get("video_url")))
    return [u for u in out if u.startswith("http")]


# ---------------------------------------------------------------- 解析

def parse_card(payload: dict, source_file: str = "") -> dict:
    g = payload.get("goods") or {}
    pr = payload.get("price") or {}
    warns: list[str] = []

    pid = _s(g.get("goods_id"))
    title = _s(g.get("goods_name")) or _s(g.get("short_name"))

    # 价格：拼单价 min/max 为主；取不到才用 goods.market_price（划线价）
    lo = _yuan(pr.get("min_on_sale_group_price") or pr.get("min_group_price"))
    hi = _yuan(pr.get("max_on_sale_group_price") or pr.get("max_group_price"))
    if lo is None or hi is None:
        mk = _yuan(g.get("market_price"))
        lo, hi = lo or mk, hi or mk
        warns.append("价格段取不到 min/max_group_price，退回 market_price（划线价，仅兜底）")

    # 类目：拼多多自有 cat_id，标低置信
    cids = [_s(g.get(k)) for k in ("cat_id_1", "cat_id_2", "cat_id_3", "cat_id_4")
            if _s(g.get(k)) not in ("", "0", "None")]
    warns.append("cat_id_1..4 是拼多多自有类目编号，非微信/抖店类目，不作上架类目用")

    # 属性
    attrs: dict[str, str] = {}
    for it in (g.get("goods_property") or []):
        if isinstance(it, dict) and _s(it.get("key")):
            vals = it.get("values") or []
            attrs[_s(it["key"])] = "；".join(_s(x) for x in vals if _s(x))

    # SKU：顶层 sku[]（goods.sku 实测常空）
    skus: list[dict] = []
    for it in (payload.get("sku") or []):
        if not isinstance(it, dict):
            continue
        specs = _specs(it)
        skus.append({
            "sku_id": _s(it.get("sku_id")),
            "sku_code": "",
            "price": _sku_price(it),
            "origin_price": _yuan(it.get("normal_price") or it.get("old_group_price")),
            "stock": int(_num(it.get("quantity")) or 0),
            "specs": specs,
            "thumb_url": _s(it.get("thumb_url")),
            "status": it.get("is_onsale"),
        })
    if not skus:
        warns.append("顶层 sku[] 为空，SKU 价格/库存缺证据")

    # 店铺名
    shop = _shop_name(payload)
    if not shop:
        warns.append("mall_data 里没提到 mall_name → shop_name=None（不编造）")

    # 媒体：按 (role, url) 去重，角色全保留
    media: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(role: str, u: Any, sku_id: str = "") -> None:
        s = _s(u)
        if not s.startswith("http") or (role, s) in seen:
            return
        seen.add((role, s))
        media.append({"role": role, "sequence": len(media), "url": s,
                      "sku_id": sku_id or None,
                      "shared_key": "%s:%d" % (role, len(media))})

    add("main", g.get("hd_url"))
    for x in (g.get("gallery") or []):
        if isinstance(x, dict) and str(x.get("type")) == "2":
            add("main", x.get("url"))
    for floor in (g.get("decoration") or []):
        if isinstance(floor, dict):
            for c in (floor.get("contents") or []):
                if isinstance(c, dict):
                    add("detail", c.get("img_url"))
    for sk in skus:
        if sk["thumb_url"]:
            add("sku", sk["thumb_url"], sku_id=sk["sku_id"])
    for u in _video_urls(payload, g):
        add("video", u)

    stats = {
        "main": sum(1 for m in media if m["role"] == "main"),
        "carousel": 0,
        "detail": sum(1 for m in media if m["role"] == "detail"),
        "sku": sum(1 for m in media if m["role"] == "sku"),
        "video": sum(1 for m in media if m["role"] == "video"),
        "entries": len(media),
        "unique_files": len({m["url"] for m in media}),
    }
    stats["shared"] = stats["entries"] - stats["unique_files"]
    if not stats["main"]:
        warns.append("没有主图（hd_url / gallery 都没拿到）")

    return {
        "platform": PLATFORM,
        "source_product_id": pid,
        "appid": _s(g.get("mall_id")),   # 拼多多店铺 id，供反查
        "title": title,
        "short_title": _s(g.get("short_name")) or None,
        "sub_title": None,
        "shop_name": shop,
        "shop_type": None,
        "category": {"ids": cids, "path": None,
                     "confidence": 0.0, "name_missing": True},
        "attributes": attrs,
        "attributes_extra": {
            "services": None, "sale_attrs_text": None,
            "total_stock": g.get("quantity"),
            "total_sold": g.get("sold_quantity"),
        },
        "skus": skus,
        "price_min": lo,
        "price_max": hi,
        "media": media,
        "media_stats": stats,
        "video_note": None if stats["video"] else "来源未提供视频",
        "video_expired": None,
        "video_meta": [],
        "raw_payload": payload,
        "source_file": source_file,
        "parse_warnings": warns,
    }


def display_name(platform: str) -> str:
    return DISPLAY if platform == PLATFORM else platform
