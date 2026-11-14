"""微信小店商品卡（小葫芦导出）解析适配器。

真实样本（2026-10-23）：
    ...\\小葫芦导出商品卡\\链接解析_<时间>\\商品数据\\10001392325483.json

这份卡的结构坑（都已按实测处理，不要"顺手简化"）：
  · 顶层 `params` 是 **空字符串 ""**，不是数组；真正的属性在 `_raw.param`（6 组 name/name 对）
  · `_raw.param` 每组形如 {"categorys":[{"name":"品牌"},{"name":"shinetrip"}]}，前一个是键、后一个是值
  · 价格两处：`_raw.*Price` 是**分**（salePrice=108800），顶层 `minPrice/maxPrice/*_yuan` 已是**元**
  · `catId` 只有数字链（6932/545845/545875），**没有中文类目名** → 类目必须标 low 置信
  · `_raw.category` 的数组元素只有 catId，不要指望从里面拿名字
  · SKU 小图 `productSkuInfo.thumbImg` 可能与第 1 张主图**完全相同** → 必须共享文件但保留两种角色
  · 视频 `videoUrls=[]` **且** `_raw.videoUrls=null` → 记"来源未提供视频"，不是下载失败
  · 整卡**没有任何店铺字段** → shop_name 保持 None（未知），绝不编造
  · 商品 ID 一律用**字符串**（10001392325483 超过 32 位会丢精度）

对外契约（parse_card 返回）：
{
  "platform": "wechat",
  "source_product_id": "10001392325483",   # 字符串
  "title": "...", "short_title": "...", "sub_title": "...",
  "shop_name": None, "shop_type": None,     # 源没有就是 None
  "category": {"ids": ["6932"], "path": None, "confidence": 0.0, "name_missing": True},
  "attributes": {"品牌": "shinetrip", ...},  # 来自 _raw.param
  "attributes_extra": {"services": "...", "sale_attrs_text": "..."},
  "skus": [{"sku_id","sku_code","price","stock","specs":{...},"thumb_url","origin_price"}],
  "price_min": 1088.0, "price_max": 1088.0,
  "media": [{"role","sequence","url","sku_id","shared_key"}],   # 已按 url 去重、角色保留
  "media_stats": {"main","carousel","detail","sku","video","unique_files","shared"},
  "video_note": "来源未提供视频" | None,
  "raw_payload": {...},                      # 整卡原样保留
  "parse_warnings": [...],                   # 解析时发现的可疑点，不吞
}
"""

from __future__ import annotations

import os
from typing import Any


# ---------------------------------------------------------------- 识别

def detect(payload: dict) -> bool:
    """微信小店的卡：有 productId + _raw，且 _raw 里有 productSkus/headImg。"""
    if not isinstance(payload, dict):
        return False
    raw = payload.get("_raw")
    if not isinstance(raw, dict):
        return False
    if "productId" not in payload and "itemId" not in payload:
        return False
    return ("productSkus" in raw) or ("headImg" in raw)


# ---------------------------------------------------------------- 小工具

def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _fen_to_yuan(v: Any) -> float | None:
    """分 -> 元。只用于确认 _raw 里的价格；顶层 *_yuan 已带单位，不要再除。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(n / 100.0, 2)


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _urls(v: Any) -> list[str]:
    """把各种形状的图片字段统一成 url 列表（字符串 / [字符串] / [{url|imgUrl|thumbImg}]）。"""
    out: list[str] = []
    for it in _as_list(v):
        if isinstance(it, str):
            s = it.strip()
        elif isinstance(it, dict):
            s = _s(it.get("url") or it.get("imgUrl") or it.get("thumbImg")
                   or it.get("productSkuInfo", {}).get("thumbImg") if isinstance(it.get("productSkuInfo"), dict) else "")
            s = _s(s)
        else:
            s = ""
        if s.startswith("http"):
            out.append(s)
    return out


def _attrs_from_param(param: Any) -> tuple[dict, list[str]]:
    """_raw.param → {"键": "值"}。

    实测形状：每组 {"categorys":[{"name":键},{"name":值}, ...]}，
    键值按顺序成对出现（第 0 个键、第 1 个值）。遇到奇数个就记警告，不猜。
    """
    attrs: dict[str, str] = {}
    warns: list[str] = []
    for gi, group in enumerate(_as_list(param)):
        if not isinstance(group, dict):
            continue
        names = [_s(c.get("name")) for c in _as_list(group.get("categorys")) if isinstance(c, dict)]
        names = [n for n in names if n]
        if len(names) < 2:
            if names:
                warns.append(f"属性组 {gi} 只有 {len(names)} 个名字，跳过：{names}")
            continue
        if len(names) % 2:
            warns.append(f"属性组 {gi} 名字数是奇数（{len(names)}），按顺序成对取，末项丢弃")
        for i in range(0, len(names) - 1, 2):
            k, v = names[i], names[i + 1]
            if k and v and k not in attrs:
                attrs[k] = v
    return attrs, warns


# ---------------------------------------------------------------- 主解析

def parse_card(payload: dict, source_file: str = "") -> dict:
    raw = payload.get("_raw") or {}
    warns: list[str] = []

    pid = _s(payload.get("productId") or payload.get("itemId") or raw.get("productId"))
    if not pid:
        raise ValueError("卡里没有 productId/itemId，无法作为商品主键")
    if raw.get("productId") and _s(raw.get("productId")) != pid:
        warns.append(f"顶层 productId={pid} 与 _raw.productId={_s(raw.get('productId'))} 不一致，以顶层为准")
    if payload.get("itemId") and _s(payload.get("itemId")) != pid:
        warns.append(f"itemId={_s(payload.get('itemId'))} 与 productId={pid} 不同，用 productId 作主键")

    title = _s(payload.get("title") or raw.get("title"))
    if not title:
        raise ValueError("卡里没有 title")

    # ---- 类目：只有数字链，没有中文名
    cat_ids = [_s(c.get("catId")) for c in _as_list(raw.get("category")) if isinstance(c, dict) and c.get("catId")]
    if not cat_ids:
        cat_ids = [_s(payload.get("catId"))] if payload.get("catId") else []
        if cat_ids:
            warns.append("买不到 _raw.category 链，退回顶层 catId（可能只是叶子节点）")
    cat_conf = 0.0 if cat_ids else 0.0
    if cat_ids:
        warns.append(f"类目只有 id 链 {cat_ids}，源里没有中文类目名 → category_confidence=0")

    # ---- 属性：顶层 params 是空串，必须走 _raw.param
    attrs, awarn = _attrs_from_param(raw.get("param"))
    warns.extend(awarn)
    if not attrs:
        warns.append("_raw.param 里没解析出任何属性")
    top_params = payload.get("params")
    if not attrs and isinstance(top_params, dict) and top_params:
        attrs = {_s(k): _s(v) for k, v in top_params.items()}

    # ---- 价格：顶层已是元
    price_min = _num(payload.get("minPrice_yuan")) or _num(payload.get("minPrice"))
    price_max = _num(payload.get("maxPrice_yuan")) or _num(payload.get("maxPrice"))
    if price_min is None:
        # 退回 SKU 的分数换算
        pel = [_fen_to_yuan(s.get("productSkuInfo", {}).get("salePrice"))
               for s in _as_list(raw.get("productSkus")) if isinstance(s, dict)]
        pel = [p for p in pel if p is not None]
        if pel:
            price_min, price_max = min(pel), max(pel)
            warns.append("顶层没有 *_yuan 价格，用 SKU salePrice(分)/100 换算")

    # ---- SKU
    skus: list[dict] = []
    for it in _as_list(raw.get("productSkus")):
        if not isinstance(it, dict):
            continue
        info = it.get("productSkuInfo") or {}
        key = it.get("skuKey") or {}
        specs: dict[str, str] = {}
        for sp in _as_list(info.get("saleParam")):
            if not isinstance(sp, dict):
                continue
            names = [_s(c.get("name")) for c in _as_list(sp.get("categorys")) if isinstance(c, dict)]
            names = [n for n in names if n]
            for i in range(0, len(names) - 1, 2):
                specs[names[i]] = names[i + 1]
        st = info.get("stockInfo") or {}
        skus.append({
            "sku_id": _s(key.get("skuId") or info.get("skuId")),
            "sku_code": _s(info.get("skuCode")),
            "price": _fen_to_yuan(info.get("salePrice")),
            "origin_price": _fen_to_yuan(info.get("originPrice")),
            "stock": int(_num(st.get("stockNum")) or 0),
            "specs": specs,
            "thumb_url": _s((info.get("thumbImg") or "")),
            "status": it.get("status"),
        })
    if not skus:
        warns.append("没有解析到任何 SKU（库存/价格会缺证据）")

    # ---- 媒体（按 url 去重，但角色全部保留）
    media: list[dict] = []
    seen: dict[str, str] = {}       # url -> 第一次出现的 shared_key

    def add(role: str, seq: int, url: str, sku_id: str = "", shared_key: str = "") -> None:
        u = _s(url)
        if not u.startswith("http"):
            return
        sk = shared_key or seen.get(u, "")
        if not sk:
            sk = f"{role}:{seq}"
            seen[u] = sk
        media.append({"role": role, "sequence": seq, "url": u, "sku_id": sku_id or None,
                      "shared_key": sk})

    main_urls = _urls(payload.get("headImg")) or _urls(raw.get("headImg"))
    if not main_urls:
        main_urls = _urls(payload.get("headImgs"))
    for i, u in enumerate(main_urls):
        add("main", i, u)

    detail_urls = _urls((raw.get("detail") or {}).get("detailImg"))
    for i, u in enumerate(detail_urls):
        add("detail", i, u)

    for sk in skus:
        if sk["thumb_url"]:
            add("sku", 0, sk["thumb_url"], sku_id=sk["sku_id"])

    video_urls = _urls(payload.get("videoUrls")) or _urls(raw.get("videoUrls"))
    for i, u in enumerate(video_urls):
        add("video", i, u)

    uniq = len(seen)
    stats = {
        "main": sum(1 for m in media if m["role"] == "main"),
        "carousel": sum(1 for m in media if m["role"] == "carousel"),
        "detail": sum(1 for m in media if m["role"] == "detail"),
        "sku": sum(1 for m in media if m["role"] == "sku"),
        "video": sum(1 for m in media if m["role"] == "video"),
        "entries": len(media),
        "unique_files": uniq,
        "shared": len(media) - uniq,
    }
    video_note = None
    if stats["video"] == 0:
        video_note = "来源未提供视频"

    # ---- 店铺：源里没有就留 None
    shop_name = _s(payload.get("shopName") or raw.get("shopName") or payload.get("nickName")) or None
    if shop_name is None:
        warns.append("卡里没有店铺字段 → shop_name=None（未知，不编造）")

    return {
        "platform": "wechat",
        "source_product_id": pid,
        "appid": _s(payload.get("appid")),
        "title": title,
        "short_title": _s(payload.get("shortTitle") or raw.get("shortTitle")) or None,
        "sub_title": _s(payload.get("subTitle") or raw.get("subTitle")) or None,
        "shop_name": shop_name,
        "shop_type": None,
        "category": {"ids": cat_ids, "path": None,
                     "confidence": cat_conf if cat_ids else 0.0,
                     "name_missing": not bool(payload.get("categoryName"))},
        "attributes": attrs,
        "attributes_extra": {
            "services": _s(payload.get("services")) or None,
            "sale_attrs_text": _s(payload.get("saleAttrsText")) or None,
            "total_stock": payload.get("totalStock"),
            "total_sold": payload.get("totalSold"),
        },
        "skus": skus,
        "price_min": price_min,
        "price_max": price_max,
        "media": media,
        "media_stats": stats,
        "video_note": video_note,
        "raw_payload": payload,
        "source_file": source_file,
        "parse_warnings": warns,
    }


# ---------------------------------------------------------------- 扫文件夹

# ★ 2026-11-14：老版拼多多页面快照也是 .txt 完整 JSON（store.initDataObj），
#   与微信 .json 一样按内容由 adapters.detect_file 判定，扫描阶段不能按扩展名挡掉。
#   认不出的仍会在 cards.import_folder 里被记成 invalid，不会污染商品库。
CARD_EXT = (".json", ".txt")


def scan_cards(folder: str, recursive: bool = False) -> dict:
    """扫文件夹里的商品卡文件。**默认只扫一层**（用户口径：不要往下深挖）。

    返回 {"files": [全路径...], "scanned": n, "ignored": [{"path","why"}...]}
    """
    files: list[str] = []
    ignored: list[dict] = []
    if not os.path.isdir(folder):
        raise ValueError(f"不是文件夹：{folder}")

    if recursive:
        walker = os.walk(folder)
        for root, _dirs, names in walker:
            for n in names:
                p = os.path.join(root, n)
                (files if n.lower().endswith(CARD_EXT) else ignored).append(
                    p if n.lower().endswith(CARD_EXT)
                    else {"path": p, "why": "扩展名不是 .json / .txt"})
    else:
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            if os.path.isdir(p):
                ignored.append({"path": p, "why": "子文件夹（本次只扫一级）"})
            elif n.lower().endswith(CARD_EXT):
                files.append(p)
            else:
                ignored.append({"path": p, "why": "扩展名不是 .json / .txt"})

    return {"files": files, "scanned": len(files) + len(ignored), "ignored": ignored}
