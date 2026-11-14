# -*- coding: utf-8 -*-
"""v3 · 详情细采回写 —— 把 fine_crawler 的详情页能力接进 v3 库

背景（2026-10-22 实测）：
    · `server/fine_crawler.py` 早就有详情页细采能力：`fetch_one(url)` 用 CDP
      打开商品详情页、滚动（详情长图懒加载）、返回 stock / skus[] /
      detail_images[] / description / props / video / images / title。
    · 但它只被旧的 Flask 接口（server/app.py）调用，**v3 库这条路一点没接**。
    · 而 v3 库里的「搜索页粗采」包只有 14 键 payload：没有库存、没有 SKU、
      没有详情图、没有描述 —— 出 19 列时这些列必然是 0。

本模块只做一件事：**对库里已有的商品，逐条去详情页把缺的字段补齐写回**。

三条硬规矩
----------
1. **不猜**：取不到就记 errors，不编数据、不拿粗采值冒充细采值。
2. **幂等**：重复跑不产生重复图（按 original_url 去重 / SKU 图按 sku_id 去重），
   不覆盖已有非空字段（标题、价格、已有的 stock/skus 都不动）。
3. **不改别人的文件**：只用 `store.upsert_image` / `store.get_product` /
   `store.audit` 这些既有函数；collector / humanize / flow / images / pack /
   importer / packaging / export / fine_crawler 一行不改。

写回对照（v3 库 + 19 列出表口径）
--------------------------------
    stock          → payload['stock']（同时写 payload['库存']）
                     ★ export.rows_from_v3 读的是 payload['库存']，所以两个键都写，
                       这样不改 export.py 也能让「库存」列有值。
    skus[]         → payload['skus']（19 列的 SKUID~SKU预览图 六列全靠它）
    detail_images[]→ v3_image 新增 role='detail'（按 URL 去重，sequence 递增）
    skus[].image   → v3_image role='sku' + sku_id（按 sku_id/URL 去重）
    description    → payload['description'] + payload['描述']
    props          → v3_product.attributes（JSON 合并）+ payload['props']
    video          → payload['video'] + payload['视频']
    title          → **不写**（original_title 是不可覆盖字段）

URL 从哪来
----------
    ① v3_product.source_url
    ② payload 里的 source_url / url / item_url
    ③ 都没有、但平台是 taobao/tmall 且 source_product_id 是 9–13 位数字时，
       按 https://item.taobao.com/item.htm?id=<id> 拼一条
    ④ 都不成立（例如微信小程序包：CSV 只有 appid + 小程序 CDN 主图，没有详情页链接）
       → 记 errors「无详情页地址」，**不编 URL、不假装采过**
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Callable, Iterable

from . import store

# fine_crawler 是 server/ 下的平铺模块（它自己 `import db`），要先把 server/ 放进 path
_SRV_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRV_DIR not in sys.path:
    sys.path.insert(0, _SRV_DIR)

DEFAULT_PORT = 9222
DEFAULT_HOST = "localhost"          # fine_crawler 的默认值（Tabbit 只监听 IPv6，用 localhost）

STAT_KEYS = ("products_total", "fetched", "failed", "with_stock", "with_sku",
             "detail_images_added", "sku_images_added", "with_description",
             "with_video", "blocked", "images_downloaded", "images_download_failed",
             "evidence_written")

_TAOBAO_PLATFORMS = ("taobao", "tmall", "taobao_live")

# 关键字段：全空 + 页面可疑 = 认为自己没采到（不许记成功）
_KEY_FIELDS = ("stock", "skus", "detail_images", "description", "props", "video")
_BLOCK_HINTS = ("验证", "captcha", "punish", "安全", "登录", "验证码")


# ---------------------------------------------------------------- 小工具

def _empty_stats() -> dict:
    return {k: 0 for k in STAT_KEYS}


def _num(v):
    """任意值 → float（拿不准就 None，绝不返回 0 冒充）。"""
    if v in (None, "", "0"):
        return None if v != "0" else 0.0
    try:
        return float(str(v).replace(",", "").replace("¥", "").replace("元", "").strip())
    except Exception:
        return None


def _has(v) -> bool:
    """「有值」的判据：None / 空串 / 空集合都算没有。"""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    if isinstance(v, (int, float)):
        return True
    return bool(v)


def _norm_url(u) -> str:
    """规整成可比较的 URL（只做去空白 / 补协议，不猜、不改路径）。"""
    if not u or not isinstance(u, str):
        return ""
    s = u.strip()
    if s.startswith("//"):
        s = "https:" + s
    if not s.lower().startswith("http"):
        return ""
    return s


def _crawler():
    """拿到 fine_crawler 模块（只 import，不修改）。"""
    try:
        import fine_crawler  # noqa: PLC0415
        return fine_crawler
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("加载 fine_crawler 失败：%s: %s" % (type(e).__name__, e))


def _blocked_text(s: str) -> bool:
    t = (s or "").lower()
    return any(k.lower() in t for k in _BLOCK_HINTS)


def _page_blocked(crawler, page) -> tuple[bool, dict]:
    """这个标签页当前是不是停在验证/登录页（fine_crawler 自带判据，只调用不改）。"""
    if page is None or crawler is None:
        return False, {}
    try:
        info = crawler._captcha_hit(page)   # noqa: SLF001 —— 复用既有判据，不改它
        return bool((info or {}).get("hit")), info or {}
    except Exception:  # noqa: BLE001
        return False, {}


def _data_blocked(data: dict) -> bool:
    """取到「页面」但其实是被拦：标题/URL 是验证页特征，且关键字段全空。"""
    if not isinstance(data, dict):
        return True
    if not _data_empty(data):
        return False
    return (_blocked_text(data.get("title")) or _blocked_text(data.get("source_url"))
            or _blocked_text((data.get("description") or "")[:40]))


def _data_empty(data: dict) -> bool:
    """六项关键字段全空 —— 说明这趟等于没采到（不是「确实没有」，见调用处如何区分）。"""
    if not isinstance(data, dict):
        return True
    for k in _KEY_FIELDS:
        if _has(data.get(k)):
            return False
    return True


def _detail_url(product: dict, payload: dict) -> str:
    """按 ① → ④ 的顺序找详情页地址，找不到返回 ''（不猜）。"""
    for k in ("source_url",):
        u = _norm_url(product.get(k))
        if u:
            return u
    for k in ("source_url", "url", "item_url", "link", "detail_url"):
        u = _norm_url(payload.get(k))
        if u:
            return u
    plat = (product.get("source_platform") or "").strip().lower()
    sid = str(product.get("source_product_id") or "").strip()
    if plat in _TAOBAO_PLATFORMS and re.fullmatch(r"\d{9,13}", sid or ""):
        return "https://item.taobao.com/item.htm?id=%s" % sid
    return ""


# ---------------------------------------------------------------- 图片写回

def _img_index(c, pid: int) -> dict:
    """这个商品已有的图片索引（去重靠它，不靠内存里的假设）。"""
    idx = {"detail_urls": set(), "sku_urls": set(), "sku_ids": set(), "max_seq": {}}
    for r in c.execute(
            "SELECT role, sequence, sku_id, original_url FROM v3_image WHERE product_id=?",
            (pid,)):
        role = (r["role"] or "").lower()
        u = _norm_url(r["original_url"])
        if role == "detail" and u:
            idx["detail_urls"].add(u)
        if role == "sku":
            if u:
                idx["sku_urls"].add(u)
            if r["sku_id"]:
                idx["sku_ids"].add(str(r["sku_id"]))
        idx["max_seq"][role] = max(idx["max_seq"].get(role, 0), int(r["sequence"] or 0))
    return idx


def _add_images(c, pid: int, idx: dict, data: dict) -> tuple[int, int]:
    """详情图 / SKU 图写回。返回 (detail 新增数, sku 新增数)。"""
    added_detail = added_sku = 0

    # ---- 详情图：按 URL 去重，sequence 接着已有最大值往上加 ----
    urls: list[str] = []
    for u in (data.get("detail_images") or []):
        n = _norm_url(u)
        if n and n not in urls:
            urls.append(n)
    seq = idx["max_seq"].get("detail", 0)
    for u in urls:
        if u in idx["detail_urls"]:
            continue
        seq += 1
        _iid, created = store.upsert_image(c, pid, {
            "role": "detail", "sequence": seq, "original_url": u, "note": "详情细采"})
        if created:
            added_detail += 1
            idx["detail_urls"].add(u)
    idx["max_seq"]["detail"] = seq

    # ---- SKU 图：同一个 sku_id 不重复加；同 URL 不重复加 ----
    seq_s = idx["max_seq"].get("sku", 0)
    for s in (data.get("skus") or []):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("sku_id") or "").strip()
        u = _norm_url(s.get("image"))
        if not u:
            continue
        if sid and sid in idx["sku_ids"]:
            continue
        if u in idx["sku_urls"]:
            continue
        seq_s += 1
        _iid, created = store.upsert_image(c, pid, {
            "role": "sku", "sequence": seq_s, "sku_id": sid or None,
            "original_url": u, "note": "详情细采"})
        if created:
            added_sku += 1
            idx["sku_urls"].add(u)
            if sid:
                idx["sku_ids"].add(sid)
    idx["max_seq"]["sku"] = seq_s
    return added_detail, added_sku


# ---------------------------------------------------------------- payload 合并

def _merge_payload(payload: dict, data: dict) -> dict:
    """把详情数据并进 payload：**只补空，不覆盖**。返回改动的键（给报告用）。"""
    changed: dict[str, Any] = {}

    # 库存：商品级 stock（SKU 求和由 fine_crawler 算好）→ payload['stock'] + ['库存']
    stock = data.get("stock")
    sn = _num(stock)
    if sn is not None and sn > 0:
        if not _has(payload.get("stock")) and not _has(payload.get("库存")):
            val = int(sn) if float(sn).is_integer() else sn
            payload["stock"] = val
            payload["库存"] = val          # export.rows_from_v3 读这个键
            changed["stock"] = val

    # SKU（19 列六列的数据源）
    if not _has(payload.get("skus")) and _has(data.get("skus")):
        payload["skus"] = data["skus"]
        changed["skus"] = len(data["skus"])

    # 描述
    desc = (data.get("description") or "").strip()
    if desc and not _has(payload.get("description")) and not _has(payload.get("描述")):
        payload["description"] = desc
        payload["描述"] = desc
        changed["description"] = len(desc)

    # 属性（payload 侧）
    props = data.get("props") or {}
    if isinstance(props, dict) and props and not _has(payload.get("props")):
        payload["props"] = props
        changed["props"] = len(props)

    # 视频
    video = _norm_url(data.get("video"))
    if video and not _has(payload.get("video")) and not _has(payload.get("视频")):
        payload["video"] = video
        payload["视频"] = video
        changed["video"] = video

    if changed:
        payload["detail_at"] = store.now()
        payload["detail_from"] = "fine_crawler"
    return changed


def _merge_attrs(attrs: dict, data: dict, payload: dict) -> dict:
    """v3_product.attributes：既有键一律保留，只补没有的键。"""
    props = data.get("props") or {}
    out = dict(attrs or {})
    if isinstance(props, dict):
        for k, v in props.items():
            if not k or not _has(v):
                continue
            if not _has(out.get(k)):
                out[k] = v
    if isinstance(payload.get("props"), dict):
        for k, v in payload["props"].items():
            if k and _has(v) and not _has(out.get(k)):
                out[k] = v
    return out


# ---------------------------------------------------------------- 证据缺口 / 幂等判据

# 本地图落盘根目录：taobao-picker/data/v3/images/<product_id>/<role>_<image_id>.jpg
# 与 images.py 的 IMG_DIR 同一个根（它按 img<id>_<md5> 命名，本模块按任务书要求
# 用 {role}_{image_id}.jpg；两边都写 current_local_path，互不冲突）。
IMAGE_ROOT = store.IMG_DIR

# 同一条商品最多试几次（失败才累加；成功/无 URL/被拦不计入重试次数）
RETRY_MAX = 3


def image_dir(pid: int) -> str:
    """这件商品的稳定证据目录（给界面「点商品名打开文件夹」用，外部可直接打开）。"""
    d = os.path.join(IMAGE_ROOT, str(int(pid)))
    os.makedirs(d, exist_ok=True)
    return d


def gap_info(payload: dict, imgs: Iterable[dict]) -> dict:
    """**唯一判据**：这件还缺哪些关键证据。

    flow.py 的 pending 判定、本模块的「要不要再采」都调这一个函数 ——
    两处各写一套判据迟早会打架（一边说缺、一边说齐）。
    只认「有没有」，不看日期、不看备注、不做推断。
    """
    payload = payload if isinstance(payload, dict) else {}
    has_img = any((str(i.get("role") or "").lower() in ("detail", "sku")) for i in (imgs or []))
    stock = payload.get("stock")
    if not _has(stock):
        stock = payload.get("库存")
    desc = payload.get("description")
    if not _has(desc):
        desc = payload.get("描述")
    return {
        "no_detail_image": not has_img,       # 详情图与 SKU 图都没有
        "no_stock": not _has(stock),
        "no_skus": not _has(payload.get("skus")),
        "no_description": not _has(desc),
    }


def gaps_of_pid(c, pid: int) -> dict:
    p = store.get_product(c, int(pid)) or {}
    payload = p.get("original_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    return gap_info(payload, store.list_images(c, int(pid)))


def fetch_mark(payload) -> dict:
    """上次细采的结论（payload['detail_fetch']），没有就给空 dict。"""
    m = (payload or {}).get("detail_fetch") if isinstance(payload, dict) else None
    return m if isinstance(m, dict) else {}


def need_fetch(c, pid: int, force: bool = False,
               max_tries: int = RETRY_MAX) -> tuple[bool, dict]:
    """要不要为这件去采详情页 —— **幂等条件写在这里，别在调用处另发明一套**。

    判据顺序（逐条都可单独验证）：
      ① 证据已齐（四缺口全 false）                → 不采（证据已齐）
      ② force=True                                → 采
      ③ 上次 state='ok'（采成功，哪怕是「页面本来就没描述」）→ 不采（已采过）
      ④ 上次 state='no_url'（库里没有详情页地址）  → 不采（采不了，重试无意义）
      ⑤ 上次 state='blocked'（验证/登录页）        → 不采（等人过验证，硬拉更像机器人）
      ⑥ 失败次数 ≥ max_tries                      → 不采（停止重试）
      否则 → 采，why 里写明缺哪几项
    """
    pid = int(pid)
    gaps = gaps_of_pid(c, pid)
    miss = [k for k, v in gaps.items() if v]
    info = {"pid": pid, "gap": miss, "fetch": False, "why": ""}
    if force:
        # force 排在「证据已齐」之前：它的含义就是「不管齐不齐都重采一次」
        info["fetch"] = True
        info["why"] = "force（强制重采）"
        return True, info
    if not miss:
        info["why"] = "证据已齐（不再重复采）"
        return False, info

    p = store.get_product(c, pid) or {}
    payload = p.get("original_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    m = fetch_mark(payload)
    state = str(m.get("state") or "")
    tries = int(m.get("tries") or 0)
    info["last"] = m
    info["tries"] = tries

    if state == "ok":
        info["why"] = "已采过（state=ok），不再重复采"
        return False, info
    if state == "no_url":
        info["why"] = "库内无详情页地址（state=no_url），重试无意义"
        return False, info
    if state == "blocked":
        info["why"] = "上次被验证/登录页拦下（state=blocked），等人过验证后人工重跑"
        return False, info
    if tries >= max_tries:
        info["why"] = "失败已重试 %d 次，停止重试" % tries
        return False, info
    info["fetch"] = True
    info["why"] = "缺 %s" % "、".join(miss)
    return True, info


def mark_fetch(c, pid: int, state: str, reason: str = "", found: dict | None = None,
               bump: bool = False) -> dict:
    """把「这一件采没采到、为什么」写进 payload['detail_fetch']（幂等的凭据）。

    state: ok 采到 / failed 网络或解析失败 / blocked 验证页 / no_url 没有详情页地址
    bump=True 时把 tries 加一（只有真正尝试过网络才加，避免「没试就累计失败」）
    """
    pid = int(pid)
    p = store.get_product(c, pid)
    if not p:
        return {}
    payload = p.get("original_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    old = fetch_mark(payload)
    tries = int(old.get("tries") or 0) + (1 if bump else 0)
    mark = {"state": state, "at": store.now(), "tries": tries,
            "reason": (reason or "")[:300]}
    if found:
        mark["found"] = found
    payload["detail_fetch"] = mark
    c.execute("UPDATE v3_product SET original_payload=?, updated_at=? WHERE product_id=?",
              (store.j(payload), store.now(), pid))
    c.commit()
    return mark


# ---------------------------------------------------------------- 本地图落盘

def _local_name(role: str, image_id: int) -> str:
    """任务书要求的稳定命名：{role}_{image_id}.jpg（外部脚本/人直接照着找得到）。"""
    return "%s_%d.jpg" % (str(role or "unknown").lower(), int(image_id))


def _default_fetch_bytes(url: str, dst: str, timeout: int = 25) -> int:
    """真下载：复用 images.py 已有的直连下载（不带 Referer，wxapp 图对它有反应）。"""
    from . import images as _img          # noqa: PLC0415
    return _img._fetch_direct(url, dst, timeout=timeout)   # noqa: SLF001


def _img_fail(c, image_id: int, status: str, note: str) -> None:
    """下载/取 URL 失败要**落库留痕**，不许静默（status 与 note 都写，界面两头都能看）。"""
    c.execute("UPDATE v3_image SET status=?, note=?, updated_at=? WHERE image_id=?",
              (status, (note or "")[:300], store.now(), int(image_id)))
    c.commit()


def download_role_images(c, pid: int, roles: Iterable[str] = ("detail", "sku"),
                         timeout: int = 25, fetch_bytes: Callable[..., int] | None = None) -> dict:
    """把 detail/sku 图落到 data/v3/images/<pid>/{role}_{image_id}.jpg 并写回 current_local_path。

    幂等：**文件已存在且非空就跳过**（不重复下载），只补写 current_local_path。
    失败分两类记：没有 URL → status=no_url；下载抛错 → status=download_failed。
    """
    pid = int(pid)
    roles = [str(x).lower() for x in roles]
    out = {"checked": 0, "downloaded": 0, "skipped": 0, "failed": 0, "errors": []}
    fb = fetch_bytes or _default_fetch_bytes
    for img in store.list_images(c, pid):
        role = str(img.get("role") or "").lower()
        if role not in roles:
            continue
        out["checked"] += 1
        iid = int(img["image_id"])
        # 2026-10-26：落到 <商品ID>\<角色>\ 子目录（主人要求 SKU 图单独一个文件夹）
        dst = os.path.join(store.role_dir(pid, role), _local_name(role, iid))
        cur = (img.get("current_local_path") or "").strip()
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            out["skipped"] += 1
            if cur != dst:
                c.execute("UPDATE v3_image SET current_local_path=?,"
                          " status=CASE WHEN status IN ('download_failed','no_url')"
                          "             THEN 'original' ELSE status END, updated_at=?"
                          " WHERE image_id=?", (dst, store.now(), iid))
                c.commit()
            continue
        url = _norm_url(img.get("original_url"))
        if not url:
            out["failed"] += 1
            _img_fail(c, iid, "no_url", "这张图没有原始 URL，无法下载")
            out["errors"].append({"image_id": iid, "role": role, "error": "no_url"})
            continue
        try:
            n = fb(url, dst, timeout)
        except Exception as e:                        # noqa: BLE001
            out["failed"] += 1
            msg = "下载失败：%s: %s" % (type(e).__name__, str(e)[:160])
            _img_fail(c, iid, "download_failed", msg)
            out["errors"].append({"image_id": iid, "role": role,
                                  "error": "download_failed", "detail": msg})
            continue
        out["downloaded"] += 1
        c.execute("UPDATE v3_image SET current_local_path=?,"
                  " status=CASE WHEN status IN ('download_failed','no_url')"
                  "             THEN 'original' ELSE status END,"
                  " note=CASE WHEN status IN ('download_failed','no_url') THEN '' ELSE note END,"
                  " updated_at=? WHERE image_id=?", (dst, store.now(), iid))
        c.commit()
        _ = n
    return out


# ---------------------------------------------------------------- 证据目录两个描述文件

def _atomic_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_manifest(c, pid: int) -> dict:
    """manifest.json 的内容：每张图的 id/角色/原 URL/本地路径/下载状态。"""
    pid = int(pid)
    p = store.get_product(c, pid) or {}
    payload = p.get("original_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    imgs = store.list_images(c, pid)
    d = image_dir(pid)
    items = []
    for i in imgs:
        role = str(i.get("role") or "unknown").lower()
        iid = int(i["image_id"])
        fn = _local_name(role, iid)
        full = os.path.join(d, fn)
        ok_file = os.path.isfile(full) and os.path.getsize(full) > 0
        items.append({"image_id": iid, "role": i.get("role"),
                      "sequence": i.get("sequence"), "sku_id": i.get("sku_id"),
                      "original_url": i.get("original_url") or "",
                      "local_path": i.get("current_local_path") or "",
                      "file": fn, "downloaded": ok_file,
                      "status": i.get("status") or "", "note": i.get("note") or ""})
    return {
        "product_id": pid,
        "title": p.get("original_title") or "",
        "source_url": p.get("source_url") or "",
        "generated_at": store.now(),
        "dir": d,
        "counts": {
            "images": len(items),
            "detail": sum(1 for x in items if str(x["role"] or "").lower() == "detail"),
            "sku": sum(1 for x in items if str(x["role"] or "").lower() == "sku"),
            "downloaded": sum(1 for x in items if x["downloaded"]),
            "download_failed": sum(1 for x in items if x["status"] == "download_failed"),
            "no_url": sum(1 for x in items if x["status"] == "no_url"),
        },
        "gaps": gap_info(payload, imgs),
        "fetch": fetch_mark(payload),
        "images": items,
    }


def build_ocr(c, pid: int) -> dict:
    """ocr.json 的内容：该商品所有 OCR 记录（image_id / 全文 / 命中词 / 片段）。"""
    pid = int(pid)
    rows: list[dict] = []
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM v3_image_ocr WHERE product_id=? ORDER BY image_id", (pid,))]
    except Exception:                                  # noqa: BLE001  表还没建
        rows = []
    items = []
    for r in rows:
        hits = [x for x in str(r.get("hit_words") or "").split(",") if x.strip()]
        try:
            snips = store.unj(r.get("snippets"), []) or []
        except Exception:                              # noqa: BLE001
            snips = []
        items.append({"image_id": int(r["image_id"]), "role": r.get("role"),
                      "seq": r.get("seq"), "state": r.get("state"),
                      "n_seg": r.get("n_seg"), "hit_words": hits,
                      "snippets": snips, "full_text": r.get("full_text") or "",
                      "engine": r.get("engine") or ""})
    return {"product_id": pid, "generated_at": store.now(),
            "count": len(items),
            "hit_images": sum(1 for x in items if x["hit_words"]),
            "images": items}


def write_evidence(c, pid: int) -> dict:
    """把 manifest.json + ocr.json 落到证据目录，返回两个路径（外部可直接打开）。"""
    pid = int(pid)
    d = image_dir(pid)
    man = build_manifest(c, pid)
    ocr = build_ocr(c, pid)
    mp = os.path.join(d, "manifest.json")
    op = os.path.join(d, "ocr.json")
    try:
        _atomic_write_json(mp, man)
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "dir": d, "error": "写 manifest 失败：%s" % e}
    try:
        _atomic_write_json(op, ocr)
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "dir": d, "manifest": mp, "error": "写 ocr.json 失败：%s" % e}
    return {"ok": True, "dir": d, "manifest": mp, "ocr": op,
            "images": man["counts"]["images"], "ocr_rows": ocr["count"]}


# ---------------------------------------------------------------- 补证据批处理（给 flow 用）

def ensure_evidence(c, pids: Iterable[int], limit: int = 0, gap: float = 0.8,
                    force: bool = False, port: int = DEFAULT_PORT,
                    host: str = DEFAULT_HOST, fetch: Callable[..., tuple] | None = None,
                    download: bool = True, timeout: int = 25,
                    progress: Callable[[dict], None] | None = None) -> dict:
    """**判定前先补齐证据**：对缺证据的商品采详情页 → 落图 → 写两个描述文件。

    只对 need_fetch() 判为「该采」的件开浏览器；证据已齐 / 已采过 / 重试超限的一律跳过
    （并在 returned 里逐条说明为什么跳过）—— 这就是重复跑不产生重复图的那道闸。
    """
    pids = [int(x) for x in pids]
    need: list[dict] = []
    skipped: list[dict] = []
    for pid in pids:
        ok, info = need_fetch(c, pid, force=force)
        (need if ok else skipped).append(info)

    out: dict[str, Any] = {"ok": True, "needed": len(need), "skipped": len(skipped),
                           "fetched": 0, "failed": 0, "with_images": 0,
                           "errors": [], "notes": [], "skipped_detail": skipped[:200],
                           "stats": _empty_stats()}
    if limit and int(limit) > 0:
        need = need[:int(limit)]
        out["notes"].append("按 limit=%d 只补前 %d 件" % (int(limit), len(need)))

    if not need:
        # 没有要采的：仍然刷新两个描述文件（人能点开目录看当前证据），不发任何网络请求
        refreshed = 0
        for pid in pids:
            try:
                if write_evidence(c, pid).get("ok"):
                    refreshed += 1
            except Exception:                          # noqa: BLE001
                pass
        out["notes"].append("无需补采（%d 件），已刷新 %d 件的证据目录" % (len(pids), refreshed))
        return out

    r = ingest_pack(conn=c, pids=[x["pid"] for x in need], limit=0, gap=gap,
                    port=port, host=host, fetch=fetch, download=download,
                    timeout=timeout, progress=progress)
    st = r.get("stats") or {}
    out["stats"] = st
    out["fetched"] = int(st.get("fetched") or 0)
    out["failed"] = int(st.get("failed") or 0)
    out["with_images"] = int(st.get("detail_images_added") or 0)
    out["errors"] = r.get("errors") or []
    out["notes"] = (out["notes"] + (r.get("notes") or []))
    if not r.get("ok"):
        out["ok"] = False
        out["error"] = r.get("error") or "补采未完成"

    # 收口：**每一件都刷一遍证据目录 + 两个描述文件**（含本轮跳过/失败的件）——
    # 界面「点商品名打开文件夹」要求每件都有稳定目录，缺证据的件也要能点开看到「缺什么」。
    wrote = int(st.get("evidence_written") or 0)
    for pid in pids:
        try:
            if write_evidence(c, pid).get("ok"):
                wrote += 1
        except Exception:                              # noqa: BLE001
            pass
    st["evidence_written"] = wrote
    out["stats"] = st
    return out


# ---------------------------------------------------------------- 单件

def _ingest_one(c, product: dict, data: dict, download: bool = True,
                evidence: bool = True, timeout: int = 25,
                fetch_bytes: Callable[..., int] | None = None) -> dict:
    """把一份详情数据写回一个商品。返回 {"changed":..., "detail_images_added":...}。"""
    pid = int(product["product_id"])
    payload = product.get("original_payload")
    if not isinstance(payload, dict):
        payload = store.unj(payload, {}) or {}
    attrs = product.get("attributes")
    if not isinstance(attrs, dict):
        attrs = store.unj(attrs, {}) or {}

    idx = _img_index(c, pid)
    added_detail, added_sku = _add_images(c, pid, idx, data)
    changed = _merge_payload(payload, data)
    new_attrs = _merge_attrs(attrs, data, payload)

    # 采到的凭据：state=ok + 这次实际落了哪些字段（下次 need_fetch 据此跳过）
    # ★ 必须在序列化 payload 之前写：args 里存的是**当场** dumps 出来的字符串，
    #   放到 sets 之后再改 payload，落库的就少了这一块（实测踩过一次）。
    payload["detail_fetch"] = {
        "state": "ok", "at": store.now(),
        "tries": int(fetch_mark(payload).get("tries") or 0),
        "found": {"stock": _has(payload.get("stock") or payload.get("库存")),
                  "skus": len(payload.get("skus") or []),
                  "detail_images": len(data.get("detail_images") or []),
                  "description": len(str(payload.get("description") or "")),
                  "props": len(payload.get("props") or {}),
                  "video": bool(payload.get("video"))},
        "reason": "详情页采集成功",
    }

    sets: list[str] = ["original_payload=?"]
    args: list[Any] = [store.j(payload)]
    if new_attrs != attrs:
        sets.append("attributes=?")
        args.append(store.j(new_attrs))
        changed["attributes"] = len(new_attrs)

    # 价格：只补空位；已有非空价格不动（这是「不覆盖已有非空字段」的落点之一）
    sku_prices = []
    for s in (data.get("skus") or []):
        p = _num((s or {}).get("price")) if isinstance(s, dict) else None
        if p is not None and p > 0:
            sku_prices.append(p)
    if product.get("price_min") in (None, "") and sku_prices:
        sets.append("price_min=?")
        args.append(min(sku_prices))
        changed["price_min"] = min(sku_prices)
    if product.get("price_max") in (None, "") and sku_prices:
        sets.append("price_max=?")
        args.append(max(sku_prices))
        changed["price_max"] = max(sku_prices)
    if sets:
        sets.append("updated_at=?")
        args.append(store.now())
        args.append(pid)
        c.execute("UPDATE v3_product SET %s WHERE product_id=?" % ", ".join(sets), args)
        try:
            store.audit(c, "detail_ingest", "product", pid, None, changed)
        except Exception:  # noqa: BLE001
            pass
    c.commit()

    dl = {"checked": 0, "downloaded": 0, "skipped": 0, "failed": 0, "errors": []}
    if download:
        try:
            dl = download_role_images(c, pid, timeout=timeout, fetch_bytes=fetch_bytes)
        except Exception as e:                        # noqa: BLE001
            dl = {"checked": 0, "downloaded": 0, "skipped": 0, "failed": 0,
                  "errors": [{"error": "download_exception", "detail": str(e)[:160]}]}
    ev = {"ok": False, "error": "evidence=off"} if not evidence else write_evidence(c, pid)

    return {"changed": changed, "detail_images_added": added_detail,
            "sku_images_added": added_sku,
            "stock": payload.get("stock"), "skus": len(payload.get("skus") or []),
            "description": bool(payload.get("description") or payload.get("描述")),
            "video": bool(payload.get("video") or payload.get("视频")),
            "download": dl, "evidence": ev}


# ---------------------------------------------------------------- 主入口

def ingest_pack(conn=None, pack_code: str = "", pids: Iterable[int] | None = None,
                limit: int = 0, gap: float = 1.0,
                progress: Callable[[dict], None] | None = None,
                fetch: Callable[..., tuple] | None = None,
                port: int = DEFAULT_PORT, host: str = DEFAULT_HOST,
                download: bool = True, evidence: bool = True,
                timeout: int = 25,
                fetch_bytes: Callable[..., int] | None = None) -> dict:
    """对指定包 / 商品逐条拉详情页并写回 v3 库。

    conn      已有连接（bridge 的 _wrap 会传）；None 时自己开一个并关掉
    pack_code 包码（pids 为空时用它取商品）
    pids      指定商品 id（优先于 pack_code）
    limit     最多处理几件（0 = 不限）
    gap       两件之间的人工停顿基准秒数（实际在 0.6~1.6 倍之间随机）
    progress  回调 dict：{i,total,pid,ok,note,...}
    fetch     测试注入用；默认 fine_crawler.fetch_one

    返回 {ok, pack_code, stats, errors, items, notes}
        errors 里的每一条都是真实失败原因，**不会因为一件失败就中断整批**。
    """
    own = conn is None
    c = conn or store.connect()
    stats = _empty_stats()
    errors: list[dict] = []
    items: list[dict] = []
    notes: list[str] = []
    started = time.time()

    try:
        if own:
            store.init(c)

        # ---------- 选商品 ----------
        if pids:
            ids = [int(x) for x in pids]
        elif pack_code:
            ids = [int(r["product_id"]) for r in c.execute(
                "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id",
                (pack_code,))]
        else:
            return {"ok": False, "error": "必须给 pack_code 或 pids（不允许默认全库跑）",
                    "stats": stats, "errors": [], "items": []}

        in_scope = len(ids)
        if limit and limit > 0:
            ids = ids[:int(limit)]
        stats["products_total"] = len(ids)
        if not ids:
            return {"ok": False, "error": "没有匹配的商品（包码对不上？）",
                    "stats": stats, "errors": [], "items": [],
                    "pack_code": pack_code, "products_in_scope": in_scope}

        # ---------- 先判 URL：全都没有就别开浏览器了 ----------
        rows = []
        no_url = 0
        for pid in ids:
            p = store.get_product(c, pid)
            if not p:
                errors.append({"product_id": pid, "error": "库里没有这个商品"})
                stats["failed"] += 1
                continue
            payload = p.get("original_payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            url = _detail_url(p, payload)
            if not url:
                no_url += 1
                msg = ("无详情页地址（库里没有 source_url，payload 里也没有链接；"
                       "微信小程序包属于这一类）")
                errors.append({"product_id": pid,
                               "title": (p.get("original_title") or "")[:40],
                               "url": "", "error": msg})
                stats["failed"] += 1
                # 采不了也要留痕：不写 no_url 的话，下一轮还会为这一件白开一次浏览器
                mark_fetch(c, pid, "no_url", msg)
                try:
                    if evidence and write_evidence(c, pid).get("ok"):
                        stats["evidence_written"] += 1
                except Exception:                  # noqa: BLE001
                    pass
                continue
            rows.append((pid, url))

        if not rows:
            notes.append("这一批没有任何一件有详情页地址，未开启浏览器（不是采过，是采不了）")
            return {"ok": False,
                    "error": "这一批全部没有详情页地址（%d 件），未做任何网络请求" % no_url,
                    "pack_code": pack_code, "stats": stats, "errors": errors,
                    "items": [], "notes": notes,
                    "products_in_scope": in_scope,
                    "elapsed_sec": round(time.time() - started, 1)}

        crawler = _crawler()
        fetcher = fetch
        if fetcher is None:
            alive = crawler.browser_alive(port, host)
            if not alive.get("ok"):
                return {"ok": False,
                        "error": "浏览器调试口 %s:%s 连不上（%s），联网段 SKIP"
                                 % (host, port, alive.get("error")),
                        "pack_code": pack_code, "stats": stats, "errors": errors,
                        "items": [], "notes": notes + ["联网段 SKIP：浏览器不在线"],
                        "products_in_scope": in_scope,
                        "elapsed_sec": round(time.time() - started, 1)}
            notes.append("浏览器已连上：%s" % alive.get("browser"))
            fetcher = crawler.fetch_one

        # ---------- 全程复用一个标签页（不开关开关，不 bringToFront） ----------
        page = None
        how = ""
        if fetch is None:
            try:
                page = crawler._Page(port, host)
                how = page.attach()
                notes.append("采集标签页：%s（只导航，不激活窗口）"
                             % ("复用已有" if how == "reuse" else "新建一个"))
            except Exception as e:  # noqa: BLE001
                return {"ok": False,
                        "error": "拿标签页失败：%s: %s" % (type(e).__name__, e),
                        "pack_code": pack_code, "stats": stats, "errors": errors,
                        "items": [], "notes": notes,
                        "elapsed_sec": round(time.time() - started, 1)}

        try:
            total = len(rows)
            for i, (pid, url) in enumerate(rows, 1):
                # ① 采之前先看一眼：上一件是不是把标签页留在了验证页
                blocked, binfo = _page_blocked(crawler, page)
                if blocked:
                    stats["blocked"] += 1
                    errors.append({"product_id": pid, "url": url,
                                   "error": "标签页停在验证/登录页（%s），本节停止："
                                            "请人工在那个标签页里过一次验证后再重跑"
                                            % (binfo.get("title") or binfo.get("url") or "")})
                    notes.append("第 %d 件之前就已在验证页，本节停止（未硬拉）" % i)
                    break

                note, data = "", None
                try:
                    if page is not None:
                        data, note = fetcher(url, port=port, host=host, page=page)
                    elif fetch is not None:
                        data, note = fetcher(url)
                    else:
                        data, note = fetcher(url, port=port, host=host)
                except Exception as e:  # noqa: BLE001
                    data, note = None, "%s: %s" % (type(e).__name__, e)

                # ② 采之后再判一次：fine_crawler 的 note 对验证页也写 "ok"，
                #    所以不能只看 note —— 必须自己核页面/数据，否则就是假成功。
                if data:
                    ablocked, ainfo = _page_blocked(crawler, page)
                    if ablocked or _data_blocked(data):
                        stats["failed"] += 1
                        stats["blocked"] += 1
                        msg = ("被验证/登录页拦下（%s），本节停止：请人工过验证后重跑"
                               % (ainfo.get("title") or "页面标题=%s" % (data.get("title") or "")))
                        errors.append({"product_id": pid, "url": url, "error": msg})
                        items.append({"product_id": pid, "url": url, "ok": False,
                                      "note": msg, "title": data.get("title")})
                        if progress:
                            _safe_progress(progress, {"i": i, "total": total, "pid": pid,
                                                      "ok": False, "note": msg})
                        notes.append("第 %d 件被拦，本节停止" % i)
                        break

                item = {"product_id": pid, "url": url}
                if not data:
                    stats["failed"] += 1
                    errors.append({"product_id": pid, "url": url,
                                   "error": note or "取不到数据"})
                    item.update({"ok": False, "note": note})
                    items.append(item)
                    # 失败原因分档落库：被拦 → blocked（不重试，等人过验证）；其余 → failed（累计重试次数）
                    if _looks_blocked(note):
                        mark_fetch(c, pid, "blocked", note or "被验证/登录页拦下")
                    else:
                        mark_fetch(c, pid, "failed", note or "取不到数据", bump=True)
                    try:
                        if evidence and write_evidence(c, pid).get("ok"):
                            stats["evidence_written"] += 1
                    except Exception:              # noqa: BLE001
                        pass
                    if progress:
                        _safe_progress(progress, {"i": i, "total": total, "pid": pid,
                                                  "ok": False, "note": note})
                    # 验证页 / 登录页：继续硬拉只会更像机器人，整批停下
                    if _looks_blocked(note):
                        stats["blocked"] += 1
                        errors.append({"product_id": 0, "url": "",
                                       "error": "被验证/登录页拦下，本节停止（剩余未跑）"})
                        notes.append("第 %d 件被拦，本节停止" % i)
                        break
                else:
                    try:
                        r = _ingest_one(c, store.get_product(c, pid) or {}, data,
                                        download=download, evidence=evidence,
                                        timeout=timeout, fetch_bytes=fetch_bytes)
                    except Exception as e:  # noqa: BLE001
                        stats["failed"] += 1
                        mark_fetch(c, pid, "failed", "写回失败：%s: %s"
                                   % (type(e).__name__, e), bump=True)
                        errors.append({"product_id": pid, "url": url,
                                       "error": "写回失败：%s: %s" % (type(e).__name__, e)})
                        item.update({"ok": False, "note": "写回失败"})
                        items.append(item)
                        if progress:
                            _safe_progress(progress, {"i": i, "total": total, "pid": pid,
                                                      "ok": False, "note": "写回失败"})
                        continue
                    stats["fetched"] += 1
                    stats["detail_images_added"] += r["detail_images_added"]
                    stats["sku_images_added"] += r["sku_images_added"]
                    dl = r.get("download") or {}
                    stats["images_downloaded"] += int(dl.get("downloaded") or 0)
                    stats["images_download_failed"] += int(dl.get("failed") or 0)
                    if (r.get("evidence") or {}).get("ok"):
                        stats["evidence_written"] += 1
                    if _has(r["stock"]):
                        stats["with_stock"] += 1
                    if r["skus"]:
                        stats["with_sku"] += 1
                    if r["description"]:
                        stats["with_description"] += 1
                    if r["video"]:
                        stats["with_video"] += 1
                    note2 = note or "ok"
                    if _data_empty(data):
                        note2 += "｜页面正常但六项关键字段全空（这件可能确实没有 SKU/详情图/描述）"
                    item.update({"ok": True, "note": note2, "stock": r["stock"],
                                 "skus": r["skus"],
                                 "detail_images_added": r["detail_images_added"],
                                 "sku_images_added": r["sku_images_added"],
                                 "description": r["description"],
                                 "video": r["video"], "changed": r["changed"]})
                    items.append(item)
                    if progress:
                        _safe_progress(progress, {"i": i, "total": total, "pid": pid,
                                                  "ok": True, "note": note,
                                                  "stock": r["stock"], "skus": r["skus"]})
                # 件与件之间随机停顿（gap 只是基准）
                if i < total and gap:
                    try:
                        crawler._human_pause(max(0.2, gap * 0.6), max(0.4, gap * 1.6))
                    except Exception:  # noqa: BLE001
                        time.sleep(max(0.2, gap))
        finally:
            if page is not None:
                try:
                    if how == "reuse":
                        page.detach()      # 复用别人的标签：只断 WS，标签留着
                    else:
                        # ★ 本次自己开的标签就关掉：fine_crawler.attach() 新建的标签
                        #   地址里没有 MARK_TAG，下一轮也认不出来，留着只会越攒越多
                        #   （实测一轮跑完浏览器里多出 1~3 个空白/验证页标签）。
                        page.close()
                except Exception:  # noqa: BLE001
                    pass

        stats["with_stock"] = _recount(c, [r[0] for r in rows], "payload", "stock")
        stats["with_sku"] = _recount(c, [r[0] for r in rows], "payload", "skus")
        stats["with_description"] = _recount(c, [r[0] for r in rows], "payload", "description")
        stats["with_video"] = _recount(c, [r[0] for r in rows], "payload", "video")

        return {"ok": True, "pack_code": pack_code, "stats": stats,
                "errors": errors, "items": items, "notes": notes,
                "products_in_scope": in_scope,
                "elapsed_sec": round(time.time() - started, 1)}
    finally:
        if own:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


def _safe_progress(progress, payload):
    try:
        progress(payload)
    except Exception:  # noqa: BLE001
        pass


def _looks_blocked(note: str) -> bool:
    s = (note or "").lower()
    return any(k in s for k in ("验证", "captcha", "punish", "登录页", "登录"))


def _recount(c, pids: list[int], where: str, key: str) -> int:
    """按库里**实际写后的状态**重数一遍（不信内存里的累加）。"""
    n = 0
    for pid in pids:
        p = store.get_product(c, pid)
        if not p:
            continue
        if where == "payload":
            payload = p.get("original_payload") or {}
            if not isinstance(payload, dict):
                continue
            v = payload.get(key)
            if key == "stock" and not _has(v):
                v = payload.get("库存")
            if key == "description" and not _has(v):
                v = payload.get("描述")
            if key == "video" and not _has(v):
                v = payload.get("视频")
            if _has(v):
                n += 1
    return n


# ---------------------------------------------------------------- 命令行

def main(argv=None):
    """python -m server.v3.detail_ingest 关键词采集3 --limit 3"""
    import argparse

    ap = argparse.ArgumentParser(description="v3 详情细采回写")
    ap.add_argument("pack", nargs="?", default="", help="包码")
    ap.add_argument("--pids", default="", help="逗号分隔的商品 id（优先于包码）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gap", type=float, default=1.0)
    a = ap.parse_args(argv)

    pids = [int(x) for x in a.pids.replace("，", ",").split(",") if x.strip().isdigit()]
    r = ingest_pack(None, pack_code=a.pack, pids=pids or None,
                    limit=a.limit, gap=a.gap)
    print(json.dumps({k: v for k, v in r.items() if k != "items"},
                     ensure_ascii=False, indent=2))
    for it in (r.get("items") or []):
        print(it)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
