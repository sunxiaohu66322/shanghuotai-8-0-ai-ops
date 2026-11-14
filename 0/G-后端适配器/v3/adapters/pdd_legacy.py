# -*- coding: utf-8 -*-
"""拼多多「老版页面快照」商品卡适配器（百度网盘 txt 导出 · ``H:\\bdpan-txt-download``）。

真实结构（2026-11-14 实测 8 个被拒样本 + 跨 12 个目录随机样本，全部 json.loads 通过）::

    {
      "store": {
        "initDataObj": {
          "goods": {
            "goodsID", "goodsName", "catID1..4", "thumbUrl", "hdThumbUrl",
            "topGallery":[{url,id,aspectRatio}],        ← 主图/轮播
            "viewImageData":[url...],                    ← 主图备份
            "detailGallery":[{url,width,height}],       ← 详情图
            "videoGallery":[...], "descVideoGallery":[...],
            "skus":[{skuId,goodsId,thumbUrl,quantity,isOnsale,
                     spec, specs:[{spec_key,spec_value,...}],
                     groupPrice, normalPrice, oldGroupPrice, ...}],  ← 真 SKU
            "goodsProperty":[{key, values:[...]}],
            "minGroupPrice"/"maxGroupPrice" ...         ← 单位「元」字符串
          },
          "mall": {"mallName","mallId","salesTip"},
          "queries": {"goods_id","_x_query","_oak_gallery"}
        }
      }
    }

与已支持的「多米数据助手」新卡（``adapters/pdd.py``）**是两套方言**，不能混用：

===================  ==========================  =============================
项                    新卡 pdd.py                 本文件（老卡）
===================  ==========================  =============================
顶层                  goods / sku / price / ui    store.initDataObj.goods / mall / queries
商品 ID               goods.goods_id              goods.goodsID 或 queries.goods_id
SKU                   顶层 sku[]                  goods.skus[]
SKU 规格              specs 是 repr 字符串        specs 是真 list[dict]
主图                  gallery[].url(type=2)       topGallery[].url
详情图                decoration[].img_url        detailGallery[].url
价格                  「分」字符串，要 /100       「元」字符串，**不除 100**
店铺                  mall_entrance.mall_data串   mall.mallName
===================  ==========================  =============================

★ 价格是这两个方言最容易出错的一处：老卡 ``groupPrice='427.8'`` 已经是元，
  除 100 会变成 4.28 元；而 ``oldGroupPrice=46500`` 才是分（划线价，不取）。
  只认 groupPrice / normalPrice / minGroupPrice / maxGroupPrice 这些元字段。

★ 原样保留 ``raw_payload``（整个 {"store":...}），下游 ``sku.apply`` 会读它
  写 v3_sku（见 ``sku.parse`` 的 pdd_legacy 分支），``v3_image.sku_id`` 则由
  ``cards._rec_of`` 从 media[].sku_id 带过去，两张表按同一个 skuId 对得上。

parse_card 输出契约见 ``wechat.py`` 顶部；detect 形状足够特异，不会误判
新多米卡（顶层 goods）或微店卡（_raw/productId）。
"""

from __future__ import annotations

from typing import Any

PLATFORM = "pdd_legacy"
DISPLAY = "拼多多（老版详情卡）"


# ---------------------------------------------------------------- 识别

def detect(payload: dict) -> bool:
    """老版页面快照：store.initDataObj.goods.goodsID + (topGallery | skus | detailGallery)。"""
    if not isinstance(payload, dict):
        return False
    ini = (payload.get("store") or {}).get("initDataObj")
    if not isinstance(ini, dict):
        return False
    g = ini.get("goods")
    if not isinstance(g, dict):
        return False
    gid = g.get("goodsID") or (ini.get("queries") or {}).get("goods_id")
    if not gid:
        return False
    return bool(g.get("topGallery") or g.get("detailGallery") or g.get("skus"))


# ---------------------------------------------------------------- 小工具

def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _yuan(v: Any) -> float | None:
    """老卡的价格字段**本来就是元**（字符串），只转 float，不除 100。"""
    n = _num(v)
    return round(n, 2) if n is not None and n > 0 else None


def _skus(g: dict) -> list[dict]:
    """goods.skus[] → 标准 SKU 行。specs 是真 list[dict]（不是 repr 字符串）。"""
    out: list[dict] = []
    for it in (g.get("skus") or []):
        if not isinstance(it, dict):
            continue
        sid = _s(it.get("skuId") or it.get("skuID") or it.get("sku_id"))
        if not sid:
            continue
        specs_raw = it.get("specs") or []
        if isinstance(specs_raw, str):
            # 极少数导出把 specs 转成了 Python repr 字符串，兼容一下
            import ast
            try:
                specs_raw = ast.literal_eval(specs_raw)
            except (ValueError, SyntaxError):
                specs_raw = []
        pairs: dict[str, str] = {}
        for sp in specs_raw if isinstance(specs_raw, (list, tuple)) else []:
            if isinstance(sp, dict) and _s(sp.get("spec_key")):
                pairs[_s(sp["spec_key"])] = _s(sp.get("spec_value"))
        if not pairs and _s(it.get("spec")):
            pairs["规格"] = _s(it["spec"])
        # ★ 只认「元」字段，取第一个正数；price 恒 0、oldGroupPrice 是分（划线价）都不取
        price = None
        for k in ("groupPrice", "normalPrice", "skuPrice"):
            n = _num(it.get(k))
            if n and n > 0:
                price = round(n, 2)
                break
        out.append({
            "sku_id": sid,
            "sku_code": "",
            "price": price,
            "origin_price": _yuan(it.get("normalPrice")),
            "stock": None if _num(it.get("quantity")) is None else int(_num(it.get("quantity"))),
            "specs": pairs,
            "thumb_url": _s(it.get("thumbUrl") or it.get("thumb_url")),
            "status": it.get("isOnsale") if "isOnsale" in it else it.get("is_onsale"),
        })
    return out


def _media(g: dict, ini: dict, skus: list[dict]) -> list[dict]:
    """主图 / 详情图 / SKU 图 / 视频；SKU 图带 sku_id。

    ★ 2026-11-14 验收实测：``60149359.txt`` 14 个 SKU 只有 11 张不同缩略图，
      ``366341950.txt`` 19 个 SKU 只有 17 张 —— 多个规格共用同一张图。
      所以 SKU 图去重键必须是 ``(role,url,sku_id)``，不能按 (role,url) 全局压掉，
      否则那几个共用图的规格在 ``v3_image.sku_id`` 上就丢了归属，
      下游 ``wxshop`` / ``doudian`` 出表按 sku_id 找图会找不着。
      物理文件仍由 ``media.run`` 按 URL 归并只下一份，不重复占盘。
    """
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(role: str, u: Any, sku_id: str = "") -> None:
        s = _s(u)
        if not s.startswith("http"):
            return
        key = (role, s, sku_id or "")
        if key in seen:
            return
        seen.add(key)
        out.append({"role": role, "sequence": len(out), "url": s,
                    "sku_id": sku_id or None,
                    "shared_key": "%s:%d" % (role, len(out))})

    # 主图：高清主图 → 轮播 → 缩略图 → viewImageData 备份
    q = ini.get("queries") or {}
    add("main", q.get("_oak_gallery"))
    add("main", g.get("hdThumbUrl") or g.get("hd_url"))
    for x in (g.get("topGallery") or []):
        if isinstance(x, dict):
            add("main", x.get("url"))
        elif isinstance(x, str):
            add("main", x)
    add("main", g.get("thumbUrl") or g.get("thumb_url"))
    for u in (g.get("viewImageData") or []):
        add("main", u)

    # 详情图
    for x in (g.get("detailGallery") or []):
        if isinstance(x, dict):
            add("detail", x.get("url") or x.get("img_url"))
        elif isinstance(x, str):
            add("detail", x)

    # SKU 图：★ 带 sku_id，v3_image.sku_id 与 v3_sku.sku_id 靠它对上
    for sk in skus:
        if sk["thumb_url"]:
            add("sku", sk["thumb_url"], sku_id=sk["sku_id"])

    # 视频（url 键在不同版本里叫法不一，全部兼容；取不到不算失败）
    for key in ("videoGallery", "descVideoGallery"):
        for v in (g.get(key) or []):
            if isinstance(v, dict):
                for k in ("url", "videoUrl", "fileUrl", "src", "playUrl", "highPriorityUrl"):
                    add("video", v.get(k))
            elif isinstance(v, str):
                add("video", v)
    return out


# ---------------------------------------------------------------- 解析

def parse_card(payload: dict, source_file: str = "") -> dict:
    ini = (payload.get("store") or {}).get("initDataObj") or {}
    g = ini.get("goods") or {}
    mall = ini.get("mall") or {}
    q = ini.get("queries") or {}
    warns: list[str] = []

    pid = _s(g.get("goodsID") or q.get("goods_id") or q.get("goodsId"))
    title = _s(g.get("goodsName") or g.get("goods_name") or g.get("weakenGoodsName"))
    shop = _s(mall.get("mallName") or mall.get("mall_name")) or None

    # 价格：老卡元字段，绝不 /100
    lo = _yuan(g.get("minOnSaleGroupPrice") or g.get("minGroupPrice") or g.get("minNormalPrice"))
    hi = _yuan(g.get("maxOnSaleGroupPrice") or g.get("maxGroupPrice") or g.get("maxNormalPrice"))
    if lo is None and hi is None:
        warns.append("价格段取不到 min/maxGroupPrice（老卡元字段）")

    # 类目：拼多多自有 catID，标低置信，不套微信/抖店类目
    cids = [_s(g.get(k)) for k in ("catID1", "catID2", "catID3", "catID4", "catID")
            if _s(g.get(k)) not in ("", "0", "None")]
    cids = list(dict.fromkeys([x for x in cids if x]))
    warns.append("catID1..4 是拼多多自有类目编号，非微信/抖店类目，不作上架类目用")

    # 属性
    attrs: dict[str, str] = {}
    for it in (g.get("goodsProperty") or []):
        if isinstance(it, dict) and _s(it.get("key")):
            vals = it.get("values") or []
            attrs[_s(it["key"])] = "；".join(_s(x) for x in vals if _s(x))

    skus = _skus(g)
    if not skus:
        warns.append("goods.skus[] 为空，SKU 价格/库存缺证据")
    media = _media(g, ini, skus)
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
        warns.append("没有主图（topGallery / hdThumbUrl 都没拿到）")

    return {
        "platform": PLATFORM,
        "source_product_id": pid,
        "appid": _s(mall.get("mallId") or mall.get("mallID") or g.get("mallID")),
        "title": title,
        "short_title": _s(g.get("weakenGoodsName")) or None,
        "sub_title": _s(g.get("goodsDesc")) or None,
        "shop_name": shop,
        "shop_type": None,
        "category": {"ids": cids, "path": None,
                     "confidence": 0.0, "name_missing": True},
        "attributes": attrs,
        "attributes_extra": {
            "services": None, "sale_attrs_text": None,
            "total_stock": g.get("quantity"),
            "total_sold": g.get("sideSalesTip") or mall.get("salesTip"),
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
