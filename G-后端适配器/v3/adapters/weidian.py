"""微店 / 微信小店商品卡适配器（用户口径：三种来源之一 = 「微店」）。

为什么在 `wechat.py` 外面再包一层，而不是改 `wechat.py`：
  `wechat.py` 的字段坑（空 params、分/元两套价格、类目只有 id）都是实测结果，
  是**平台事实**，不该为改名而动。这一层只做三件「平台身份与补充信息」的事：

  1. 平台码改成 `weidian`（用户按「微店」称呼这批数据；`wechat` 继续保留，老数据可解析）
  2. `source_store_id` = 卡里的 `appid`（这是这份导出里**唯一**能标识店铺的真字段）
  3. 视频补全：`wechat.py` 只读 `videoUrls`，而真卡的 `_raw.headVideoInfo[0]`
     同时给了 `videoUrl / playableVideoUrl / videoH265Url`（同一文件的三种编码）。
     这里按 URL 去重后补进去，并**优先用 playableVideoUrl**（可播放的那份）。

店铺名：这份导出**没有店铺名字段**（实测 51 个 `_raw` 键里没有 shop/mall/nick）。
所以 `shop_name` 保持空，并在 `parse_warnings` 里写明「源未提供，appid=<x> 可反查」——
不编造。用户以后要显示店名，走 appid→店名的映射表（后续任务）。

视频「过期」的判据也在这里给：URL 里的 `wdis_t` 是失效时刻（Unix 秒）。
`video_note` 会带上它，下载器据此判「不可重试·需重新采集」。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import wechat

PLATFORM = "weidian"
DISPLAY = "微店"


def detect(payload: dict) -> bool:
    """微店/微信小店卡：沿用 wechat 的判据（productId + _raw.productSkus/headImg）。

    ★ 2026-10-24 补第二种形状 ——「**详情页卡**」：
        主人花钱抓的商品卡是 {appid, product_id, title, skus, _extra} 这种结构，
        图片全在 `_extra.headImgs` / `_extra._raw.detail.detailImg` / `_extra._raw.skuList`。
        老判据只认真卡的 `_raw.productSkus`，于是这种卡被判成 other →
        **18 件商品进了库、媒体一条没写**，2.5 打开就是「只有标题」（主人原话）。
    """
    if wechat.detect(payload):
        return True
    ex, raw = _extra_shape(payload)
    pid = _s(payload.get("product_id") or payload.get("productId") or ex.get("itemId"))
    if not pid:
        return False
    return bool(_main_urls(ex, raw) or _detail_urls(raw) or _sku_urls(raw))


def _extra_shape(payload: dict) -> tuple[dict, dict]:
    ex = payload.get("_extra") if isinstance(payload.get("_extra"), dict) else {}
    raw = ex.get("_raw") if isinstance(ex.get("_raw"), dict) else \
        (payload.get("_raw") if isinstance(payload.get("_raw"), dict) else {})
    return ex, raw


def _uniq(seq) -> list:
    out, seen = [], set()
    for u in seq:
        s = _s(u)
        if s.startswith("http") and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _main_urls(ex: dict, raw: dict) -> list:
    a = raw.get("headImg") if isinstance(raw.get("headImg"), list) else []
    b = ex.get("headImgs") if isinstance(ex.get("headImgs"), list) else []
    return _uniq(list(a) + list(b))


def _detail_urls(raw: dict) -> list:
    det = raw.get("detail") if isinstance(raw.get("detail"), dict) else {}
    di = det.get("detailImg") if isinstance(det.get("detailImg"), list) else []
    return _uniq(di)


def _sku_urls(raw: dict) -> list:
    """SKU 图：skuList 可能是 dict（skuInfo 列表），也可能是 list。"""
    sk = raw.get("skuList")
    items = []
    if isinstance(sk, dict):
        for v in sk.values():
            if isinstance(v, list):
                items += v
    elif isinstance(sk, list):
        items = sk
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            if isinstance(v, str) and v.startswith("http"):
                out.append(v)
            elif isinstance(v, list):
                out += [x for x in v if isinstance(x, str) and x.startswith("http")]
    return _uniq(out)


def _detail_shape_media(payload: dict) -> list:
    """把「详情页卡」的图片整理成与主流程一致的 media 条目。"""
    ex, raw = _extra_shape(payload)
    out: list[dict] = []
    for role, urls in (("main", _main_urls(ex, raw)),
                       ("detail", _detail_urls(raw)),
                       ("sku", _sku_urls(raw))):
        for i, u in enumerate(urls):
            out.append({"role": role, "sequence": i, "url": u, "sku_id": None,
                        "shared_key": "%s:%d" % (role, i)})
    return out


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def token_expiry(url: str) -> int | None:
    """从 URL 里取 `wdis_t`（失效 Unix 秒）。取不到返回 None。"""
    try:
        q = parse_qs(urlparse(url).query)
        v = (q.get("wdis_t") or [""])[0]
        return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _video_items(payload: dict) -> list[dict]:
    """收集卡里所有视频 URL：videoUrls（老字段）+ headVideoInfo（真卡主视频）。

    ★ 2026-10-24 修：**详情页卡**的视频在 `_extra._raw.headVideoInfo`，
      而本函数原来只读顶层 `payload["_raw"]` —— 于是 18 件商品的视频全部漏读
      （实测：`_extra._raw.headVideoInfo` 在 18 件里出现 78 次，库里 video 行 0 条）。
      这里把两个位置都读，并读 `_extra.videoUrls`。

    同一个文件会给多个编码地址（avc / playable / h265），**按 URL 去重**，
    但保留第一个（优先 playableVideoUrl，它是浏览器能直接播的那份）。
    """
    ex, raw_top = _extra_shape(payload)
    raw = dict(raw_top)
    out: list[dict] = []
    seen: set[str] = set()

    def add(u: Any, kind: str) -> None:
        s = _s(u)
        if not s.startswith("http") or s in seen:
            return
        seen.add(s)
        exp = token_expiry(s)
        out.append({"url": s, "kind": kind, "expires_at": exp})

    for src in (payload.get("videoUrls"), raw.get("videoUrls"), ex.get("videoUrls")):
        for it in (src or []):
            if isinstance(it, str):
                add(it, "videoUrls")
            elif isinstance(it, dict):
                add(it.get("url") or it.get("videoUrl"), "videoUrls")

    for src in (raw.get("headVideoInfo"), ex.get("headVideoInfo")):
        for it in (src or []):
            if not isinstance(it, dict):
                continue
            # 优先可播放那份，其次原始，最后 h265
            add(it.get("playableVideoUrl") or it.get("videoUrl"), "headVideoInfo")
            add(it.get("videoH265Url"), "headVideoInfo.h265")
            cover = _s(it.get("videoHeadImg"))
            if cover.startswith("http"):
                out.append({"url": cover, "kind": "video_cover",
                            "expires_at": token_expiry(cover)})
    return out


def parse_card(payload: dict, source_file: str = "") -> dict:
    """调 wechat 解析，再补平台身份 / 店铺标识 / 视频。

    ★ 详情页卡（product_id 蛇形 + _extra）先做一次键名归一，再交给 wechat：
      wechat 只认驼峰 productId，不归一就会抛「卡里没有 productId」。
    """
    p2 = payload
    if not (payload.get("productId") or payload.get("itemId")):
        pid0 = _s(payload.get("product_id") or payload.get("productId"))
        if pid0:
            _ex0, _raw0 = _extra_shape(payload)
            inner = dict(_raw0)
            inner.setdefault("productId", pid0)
            p2 = dict(payload)
            p2["productId"] = pid0
            p2["_raw"] = inner

    card = wechat.parse_card(p2, source_file=source_file)
    ex_shape, raw = _extra_shape(payload)
    warns = list(card.get("parse_warnings") or [])

    # ---- 详情页卡形状：wechat 读不到媒体（它的键名不同），这里补上 ----
    extra = _detail_shape_media(payload)
    if extra:
        have = {m.get("url") for m in (card.get("media") or [])}
        n0 = len(card.get("media") or [])
        for m in extra:
            if m["url"] not in have:
                (card.setdefault("media", [])).append(m)
                have.add(m["url"])
        if not n0:
            warns.append("这张是「详情页卡」形状（_extra.headImgs / _raw.detail.detailImg）"
                         "—— 由 weidian 适配器补出媒体 %d 条" % len(extra))

    card["platform"] = PLATFORM
    appid = _s(payload.get("appid") or raw.get("appid"))
    card["appid"] = appid
    # 店铺名源里没有 → 不编；但 appid 是真的，存进 source_store_id 供以后反查
    card["shop_name"] = card.get("shop_name") or None
    if not card.get("shop_name"):
        warns.append("源未提供店铺名（appid=%s 可反查店铺，后续接映射表）" % (appid or "无"))

    # ---- 视频：把 headVideoInfo 合进来（wechat 只读了 videoUrls）
    vids = _video_items(payload)
    have = {m["url"] for m in card["media"] if m.get("role") == "video"}
    seq = max([m["sequence"] for m in card["media"] if m.get("role") == "video"] or [-1]) + 1
    vmeta: list[dict] = []
    for v in vids:
        if v["kind"] == "video_cover":
            continue
        want = v["url"]
        if want not in have:
            card["media"].append({
                "role": "video", "sequence": seq, "url": want,
                "sku_id": None, "shared_key": "video:%d" % seq,
            })
            have.add(want)
            seq += 1
        vmeta.append(v)

    stats = card.get("media_stats") or {}
    stats["video"] = sum(1 for m in card["media"] if m.get("role") == "video")
    stats["entries"] = len(card["media"])
    uniq = len({m["url"] for m in card["media"]})
    stats["unique_files"] = uniq
    stats["shared"] = stats["entries"] - uniq
    card["media_stats"] = stats

    # 视频有效期：拿最早过期的那个作判据
    exps = [v["expires_at"] for v in vmeta if v.get("expires_at")]
    card["video_meta"] = vmeta
    if stats["video"]:
        if exps:
            soon = min(exps)
            expired = soon <= int(time.time())
            card["video_note"] = (
                "视频链接 %s 过期（wdis_t=%d），需重新采集"
                % ("已" if expired else "将于", soon) if expired
                else "视频链接有效至 %d" % soon)
            card["video_expired"] = expired
        else:
            card["video_note"] = None
            card["video_expired"] = None
    else:
        card["video_note"] = card.get("video_note") or "来源未提供视频"
        card["video_expired"] = None

    card["parse_warnings"] = warns
    return card


# ---------------------------------------------------------------- 扫文件夹

CARD_EXT = wechat.CARD_EXT


def scan_cards(folder: str, recursive: bool = False) -> dict:
    return wechat.scan_cards(folder, recursive=recursive)


def display_name(platform: str) -> str:
    return DISPLAY if platform == PLATFORM else platform
