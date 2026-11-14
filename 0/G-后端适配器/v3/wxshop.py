# -*- coding: utf-8 -*-
"""v3 · 微信小店直连上货（2026-10-26）

★ 为什么有这个模块：
  以前要出 GBK 表格，再用别人的服务商（小猪）上传。现在有了自家小店的
  AppID/AppSecret，可以绕过那层壳直连官方接口 —— 不出表，直接建商品。

实测确认的四个环节（每一项都真跑过，不是照文档抄）：
  ① POST /cgi-bin/stable_token                          → token 7200s
  ② POST /channels/ec/category/list/get                 → 1562 个已开通类目
                                                           ★ 字段名是 `list`，不是 `cat_list`
  ③ POST /shop/ec/basics/img/upload?upload_type=0
          &resp_type=1&height=&width=                     → mmecimage.cn 永久链接
                                                           ★ height/width 必填，缺了报 10020055 参数有误
                                                           ★ multipart 字段名 `file`
  ④ POST /channels/ec/product/add                       → 建商品（草稿）

★ 三条踩过的坑（不要"优化"掉）：
  1. `product/list/get` **必须带 status**（0=未上架 / 5=上架），
     不带参数返回 0 条 —— 会误判成"商品没了"。
  2. `product/get` 读未上架商品**必须带 data_type=2**，否则 10020065。
  3. 商品图片不接受任意 URL，只接受 `resp_type=1` 上传后返回的
     `mmecimage.cn/p/{appid}/{key}`；以及**已经转换过的 mmecimage 前缀**可以直接提交。
     源站的 `wst.wxapp.tc.qq.com` 链接会被判 10020061 图片URL非法。

★ 凭据只从环境变量读：
     WX_SHOP_APPID / WX_SHOP_SECRET
  不写进代码、不写进配置文件、不进日志。
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

BASE = "https://api.weixin.qq.com"

# 价格单位 = 分（实测：某茶台 sale_price 758000 ↔ 售价 7580 元）
FEN = 100

_token_cache: dict[str, dict[str, Any]] = {}       # appid -> {v, exp}
_SHOP_CTX = threading.local()


def use_shop(appid: str = "", secret: str = "", name: str = "") -> None:
    """把**当前线程**绑定到某家店；传空 = 回到环境变量单店老路。"""
    _SHOP_CTX.appid = appid or ""
    _SHOP_CTX.secret = secret or ""
    _SHOP_CTX.name = name or ""


def clear_shop() -> None:
    use_shop("", "", "")


def current_shop() -> dict:
    return {"appid": getattr(_SHOP_CTX, "appid", "") or "",
            "secret": getattr(_SHOP_CTX, "secret", "") or "",
            "name": getattr(_SHOP_CTX, "name", "") or ""}


def shop_id() -> str:
    """当前生效的 appid（多店缓存分片键）。"""
    return getattr(_SHOP_CTX, "appid", "") or _env("WX_SHOP_APPID")


@contextlib.contextmanager
def shop_context(appid: str = "", secret: str = "", name: str = ""):
    prev = current_shop()
    use_shop(appid, secret, name)
    try:
        yield
    finally:
        use_shop(prev["appid"], prev["secret"], prev["name"])


def _env(name: str) -> str:
    if name == "WX_SHOP_APPID":
        v = getattr(_SHOP_CTX, "appid", "") or ""
        if v:
            return v
    elif name == "WX_SHOP_SECRET":
        v = getattr(_SHOP_CTX, "secret", "") or ""
        if v:
            return v
    v = os.environ.get(name) or ""
    if not v:
        # 回落到 User 级（新起的进程可能没继承）
        try:
            import subprocess
            v = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetEnvironmentVariable('%s','User')" % name],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            v = ""
    return v


def token(force: bool = False, timeout: float = 20) -> str:
    """取 access_token（进程内缓存，提前 5 分钟刷新）。"""
    now = time.time()
    appid, secret = _env("WX_SHOP_APPID"), _env("WX_SHOP_SECRET")
    if not appid or not secret:
        raise RuntimeError("缺 WX_SHOP_APPID / WX_SHOP_SECRET 环境变量")
    slot = _token_cache.get(appid) or {"v": "", "exp": 0.0}
    if not force and slot["v"] and now < slot["exp"] - 300:
        return slot["v"]
    body = {"grant_type": "client_credential", "appid": appid, "secret": secret}
    url = BASE + "/cgi-bin/stable_token"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "ignore"))
    tok = j.get("access_token") or ""
    if not tok:
        raise RuntimeError("取 token 失败: %s" % json.dumps(j, ensure_ascii=False)[:300])
    _token_cache[appid] = {"v": tok, "exp": now + int(j.get("expires_in") or 7200)}
    return tok


def _post(path: str, body: dict | None = None, timeout: float = 30) -> dict:
    """JSON POST（access_token 走 query）。"""
    url = BASE + path + ("&" if "?" in path else "?") + "access_token=" + token()
    req = urllib.request.Request(
        url, data=json.dumps(body or {}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"errcode": -e.code, "errmsg": "HTTP %s: %s" % (
            e.code, e.read().decode("utf-8", "ignore")[:300])}
    except Exception as e:
        return {"errcode": -1, "errmsg": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 图片

def _upload_once(local_path: str, timeout: float = 120) -> dict:
    """单次上传（不压缩）。上层 upload_image 会在 45002 体积超限时压缩重试。"""
    if not local_path or not os.path.exists(local_path):
        return {"errcode": -2, "errmsg": "文件不存在: %s" % local_path}
    try:
        import cv2
        import numpy as np
        im = cv2.imdecode(np.fromfile(local_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return {"errcode": -3, "errmsg": "读图失败"}
        h, w = im.shape[:2]
    except Exception as e:
        return {"errcode": -3, "errmsg": "取尺寸失败: %s" % e}

    ext = os.path.splitext(local_path)[1].lower()
    ctype = {".png": "image/png", ".webp": "image/webp",
             ".bmp": "image/bmp"}.get(ext, "image/jpeg")
    q = "upload_type=0&resp_type=1&height=%d&width=%d" % (h, w)
    boundary = "----wximg" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in (("upload_type", "0"), ("resp_type", "1"),
                 ("height", str(h)), ("width", str(w))):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, k, v)).encode())
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\";"
                  " filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                  % (boundary, os.path.basename(local_path), ctype)).encode())
    parts.append(open(local_path, "rb").read())
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    body = b"".join(parts)

    url = BASE + "/shop/ec/basics/img/upload?" + q + "&access_token=" + token()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"errcode": -e.code, "errmsg": "HTTP %s: %s" % (
            e.code, e.read().decode("utf-8", "ignore")[:300])}
    except Exception as e:
        return {"errcode": -1, "errmsg": "%s: %s" % (type(e).__name__, e)}

    if j.get("errcode") == 0:
        j["img_url"] = ((j.get("pic_file") or {}).get("img_url") or "")
        j["bytes"] = os.path.getsize(local_path)
        j["px"] = [w, h]
    return j


def upload_image(local_path: str, timeout: float = 120) -> dict:
    """上传本地图片 → 返回 mmecimage 永久链接。

    ★ height/width 必须给真实像素，缺了报 10020055「参数有误」。
    ★ 2026-10-27（主人问「图片上传失败的怎么处理」）：微信单图上限约 2MB，
      超了报 errcode 45002 content size out of limit。这里**自动压到 1.9MB 以内重传**
      （先降质量、再缩边长），压出来的只是临时副本，**原图一个字节不动**。
      返回里带 shrunk=True 与 from_bytes，方便界面说明"这张是压缩过的"。
    """
    r = _upload_once(local_path, timeout)
    if int(r.get("errcode") or 0) != 45002:
        return r
    try:
        from . import upload as _up                     # 5.5 里的压缩实现（复用，不复制逻辑）
        tmp = _up._shrink_to(local_path, int(1.9 * 1024 * 1024))  # type: ignore[attr-defined]
    except Exception:                                   # noqa: BLE001
        tmp = ""
    if not tmp or not os.path.exists(tmp):
        r["errmsg"] = (r.get("errmsg") or "") + "（自动压缩也失败）"
        return r
    try:
        r2 = _upload_once(tmp, timeout)
    finally:
        try:
            os.remove(tmp)                              # 临时副本用完就删
        except Exception:                               # noqa: BLE001
            pass
    if int(r2.get("errcode") or 0) == 0:
        r2["shrunk"] = True
        r2["from_bytes"] = r.get("bytes") or (
            os.path.getsize(local_path) if os.path.exists(local_path) else 0)
        return r2
    return r2


def _guard(conn):
    """拿到发布护栏模块（拿不到就返回 None —— 缓存失效不能影响上传）。"""
    if conn is None:
        return None
    try:
        from . import publish_guard as pg          # type: ignore
    except Exception:                              # noqa: BLE001
        try:
            import publish_guard as pg             # type: ignore
        except Exception:                          # noqa: BLE001
            return None
    try:
        pg.ensure(conn)
    except Exception:                              # noqa: BLE001
        return None
    return pg


def upload_many(paths: list[str], limit: int = 30, rate: float = 0.15,
                conn=None) -> dict:
    """批量上传，跳过重复/失败，返回 {urls, failed, n_cached}。

    ★ 2026-10-27（主人问「中断后重跑会不会重传/重复铺货」）：
      传 conn 就走发布缓存 v3_wx_img_cache —— **同一个文件只传一次**。
      这样「传了 800 张图然后程序被关掉」重跑时，已传的 0 次网络，从断点继续。
      缓存键 = 绝对路径 + mtime + size，图片被修补过（mtime 变）会自动重传。
    """
    urls: list[str] = []
    failed: list[dict] = []
    seen: set[str] = set()
    n_cached = 0
    pg = _guard(conn)
    for p in paths[:limit]:
        if not p or p in seen:
            continue
        seen.add(p)
        hit = ""
        if pg is not None:
            try:
                hit = pg.img_get(conn, p)
            except Exception:                      # noqa: BLE001
                hit = ""
        if hit:
            if hit not in urls:
                urls.append(hit)
            n_cached += 1
            continue
        r = upload_image(p)
        if r.get("errcode") == 0 and r.get("img_url"):
            if r["img_url"] not in urls:
                urls.append(r["img_url"])
            if pg is not None:
                try:
                    pg.img_put(conn, p, r["img_url"], r.get("bytes") or 0)
                except Exception:                  # noqa: BLE001
                    pass
        else:
            failed.append({"path": p, "errcode": r.get("errcode"),
                           "errmsg": (r.get("errmsg") or "")[:140]})
        time.sleep(rate)
    return {"ok": True, "urls": urls, "failed": failed,
            "n_ok": len(urls), "n_fail": len(failed), "n_cached": n_cached}


def upload_map(paths: list[str], conn=None, rate: float = 0.15) -> dict:
    """**路径 → mmecimage URL** 的映射（upload_many 只给列表，对不回是哪张图）。

    ★ 2026-10-29：SKU 图（thumb_img）需要「这个规格用哪张图」，必须按路径对上号。
      同一个文件只传一次（多个规格同色共用一张图时，只花一次上传）；
      传不上的路径映射成空串（不编、不猜）。

    走与 upload_many 同一份缓存 v3_wx_img_cache —— 中断重跑不重传。
    """
    out: dict[str, str] = {}
    pg = _guard(conn)
    seen: set[str] = set()
    for p in (paths or []):
        if not p or p in seen:
            continue
        seen.add(p)
        hit = ""
        if pg is not None:
            try:
                hit = pg.img_get(conn, p)
            except Exception:                      # noqa: BLE001
                hit = ""
        if hit:
            out[p] = hit
            continue
        r = upload_image(p)
        if r.get("errcode") == 0 and r.get("img_url"):
            out[p] = r["img_url"]
            if pg is not None:
                try:
                    pg.img_put(conn, p, r["img_url"], r.get("bytes") or 0)
                except Exception:                  # noqa: BLE001
                    pass
        else:
            out[p] = ""                            # 明确失败：留空，不编地址
        time.sleep(rate)
    return out


# ---------------------------------------------------------------- 读

def list_categories() -> dict:
    r = _post("/channels/ec/category/list/get", {})
    lst = r.get("list") or []          # ★ 字段是 list
    return {"ok": r.get("errcode") == 0, "count": len(lst),
            "items": lst, "raw_errcode": r.get("errcode")}


def list_products(status: int = 6, page_size: int = 100, next_key: str = "") -> dict:
    """查商品列表（单页）。

    ★ 2026-10-26 实测修正（重要，别再按直觉写）：
        `status=6` → 56 件（未上架/草稿，含新建的）
        `status=0` → 1 件（只剩一个特例状态）
        `status=5` → 0 件（已上架，当前店里没有上架商品）
        `status=2/3/4/7/10/11/13` → 0 件

      也就是说：**未上架的真实状态码是 6，不是 0**。
      早期测到 status=0 返回 56 件、status=1 也是 56，后来变了 ——
      平台状态码会随商品流转改变，**不要把它当常量**。

      判定方法（遇到"商品全没了"先用这个）：
        把 0..13 挨个跑一遍 list_products，看哪个 total 最大，
        再用 get_product 回读一件，确认 title 还在。

    ★ 2026-10-29 修（主人：检查已上传商品时发现 SKU 图全空，顺带查出查重覆盖不全）：
        **这个接口一次最多只返回 50 条**（page_size 传 100 也只给 50），
        而 total_num 报的是全店真实数（实测 status=6 → 457 件）。
        原来只调一次、不翻页 → 查重只看得到 100 件（两个状态各 50），
        超过 100 件后「已建过」判不出来 → **存在重复铺货风险**。
        现在返回 next_key，配合 all_product_ids() 翻页取全量。

    参数：
        status     不传会返回 0 条（接口默认行为）
        page_size  **不影响 total_num**，且实际单页最多 50 条
        next_key   翻页游标（上一页返回的 next_key；空 = 第一页）
    """
    body = {"page_size": page_size, "status": status}
    if next_key:
        body["next_key"] = next_key
    r = _post("/channels/ec/product/list/get", body)
    return {"ok": r.get("errcode") == 0, "total": r.get("total_num") or 0,
            "ids": r.get("product_ids") or [], "next_key": r.get("next_key") or "",
            "status": status, "errcode": r.get("errcode"), "errmsg": r.get("errmsg")}


def all_product_ids(status: int = 6, page_size: int = 50,
                    max_pages: int = 80) -> dict:
    """翻页取某个状态下的**全量**商品 id（列表接口便宜：一页 50 条）。

    返回 {"ok", "ids", "total", "pages", "truncated"}；truncated=True 时 ids 不全。
    """
    ids: list[str] = []
    seen: set[str] = set()
    nk, pages, total = "", 0, 0
    while pages < max_pages:
        r = list_products(status, page_size, nk)
        if not r["ok"]:
            return {"ok": False, "ids": ids, "total": total, "pages": pages,
                    "truncated": True,
                    "error": "list_products 失败: errcode=%s %s"
                             % (r.get("errcode"), r.get("errmsg") or "")}
        total = r["total"] or total
        got = 0
        for x in r["ids"]:
            s = str(x)
            if s not in seen:
                seen.add(s)
                ids.append(s)
                got += 1
        nk = r.get("next_key") or ""
        pages += 1
        if not nk or got == 0 or (total and len(ids) >= total):
            break
    return {"ok": True, "ids": ids, "total": total, "pages": pages,
            "truncated": bool(total and len(ids) < total)}


def all_products_by_status(statuses=None, scan_max: int = 13) -> dict:
    """多个状态合并去重拿全量 id。

    ★ 2026-10-29 实测教训：**商品状态不止 6 和 5**。
      新建商品（add_product）与 update 过之后的商品都会落到 **status=0**，
      已上架是 5，草稿/未上架是 6，另有 1/2 等零散状态。
      只扫 (6,5) 会让「刚建过 / 刚补过图」的商品从索引里消失 → 判不出重复 → 重复铺货。
      所以 statuses=None 时**自动扫 0..scan_max 里所有有量的状态**再去重。
    """
    ids: list[str] = []
    seen: set[str] = set()
    detail = {}
    probe = list(range(0, scan_max + 1)) if statuses is None else list(statuses)
    for st in probe:
        r = all_product_ids(st)
        n = len(r.get("ids") or [])
        if not (r.get("total") or n):
            continue
        detail[str(st)] = {"total": r.get("total"), "got": n,
                           "truncated": r.get("truncated"), "error": r.get("error")}
        for x in (r.get("ids") or []):
            if x not in seen:
                seen.add(x)
                ids.append(x)
    return {"ok": True, "ids": ids, "by_status": detail,
            "truncated": any(bool(v.get("truncated")) for v in detail.values())}


def scan_status_codes(max_status: int = 13) -> dict:
    """把 0..N 全跑一遍，报每个状态码能查到多少件 —— 「商品全没了」时的第一步。"""
    out = {}
    for st in range(0, max_status + 1):
        r = list_products(st, 100)
        out[st] = r["total"]
    best = max(out.items(), key=lambda x: x[1]) if out else (None, 0)
    return {"by_status": out, "max_status": best[0], "max_total": best[1]}


def get_product(product_id: int | str) -> dict:
    """★ data_type=2 才能读未上架商品。"""
    r = _post("/channels/ec/product/get",
              {"product_id": int(product_id), "data_type": 2})
    return {"ok": r.get("errcode") == 0, "errcode": r.get("errcode"),
            "errmsg": r.get("errmsg"),
            "edit_product": r.get("edit_product") or {}}


# ---------------------------------------------------------------- 写

def add_product(payload: dict, timeout: float = 60) -> dict:
    """建商品（进草稿）。listing=0 只建不上架。

    ★ 建草稿不动线上数据；上架要另外调 /channels/ec/product/listing。
    """
    body = dict(payload)
    body.setdefault("listing", 0)
    r = _post("/channels/ec/product/add", body, timeout=timeout)
    return {"ok": r.get("errcode") == 0, "errcode": r.get("errcode"),
            "errmsg": r.get("errmsg"),
            "product_id": r.get("product_id") or r.get("data", {}).get("product_id"),
            "raw": r}


def delete_product(product_id: int | str) -> dict:
    r = _post("/channels/ec/product/delete", {"product_id": int(product_id)})
    return {"ok": r.get("errcode") == 0, "errcode": r.get("errcode"),
            "errmsg": r.get("errmsg")}


def update_product(product_id: int | str, payload: dict, timeout: float = 60) -> dict:
    """改已有商品（★ 2026-10-29 新增，补 SKU 图需要它）。

    用法：`ep = get_product(pid)["edit_product"]` → 只改要改的字段 →
    `update_product(pid, ep)` —— body 就是 edit_product 原样加 `product_id`。

    ⚠️ 这是**改线上商品**：已上架（status=5）的改动会触发平台重新审核；
      未上架/草稿（status=6）改完仍是草稿。调用方应回读比对，确认没改坏别处。
    """
    body = dict(payload)
    body["product_id"] = int(product_id)
    r = _post("/channels/ec/product/update", body, timeout=timeout)
    return {"ok": r.get("errcode") == 0, "errcode": r.get("errcode"),
            "errmsg": r.get("errmsg"), "raw": r}


def _sku_key(attrs) -> tuple:
    """sku 的属性指纹：((attr_key, attr_value), ...) 排序后成元组，用于配对。"""
    pairs = []
    for a in (attrs or []):
        if not isinstance(a, dict):
            continue
        k = (a.get("attr_key") or "").strip()
        v = (a.get("attr_value") or "").strip()
        if k or v:
            pairs.append((k, v))
    return tuple(sorted(pairs))


_POS_KEYS = {"销售属性1", "销售属性2", "销售属性3", "", "默认规格"}


def attrs_flat_str(attrs) -> str:
    """把 sku_attrs 归一成一个可比较的字符串（★ 2026-10-29 两形态兼容）。

    实测线上有两种形态，而本地 spec 只有一种写法：
      ① 多维：  [('规格','雪山白'),('尺码大小','60*40*55')]  ← 本地 '规格:雪山白/尺码大小:60*40*55'
      ② 拍平：  [('销售属性1','配套:1.2m茶桌单桌')]          ← 本地 '配套:1.2m茶桌单桌'

    归一规则：位置键（销售属性N/空）只有一个时**剥掉键名只留值**；
              其余情况按 `k:v/k:v` 拼接。这样两种形态都能与本地 spec 对上。
    """
    pairs = []
    for a in (attrs or []):
        if not isinstance(a, dict):
            continue
        k = (a.get("attr_key") or "").strip()
        v = (a.get("attr_value") or "").strip()
        if k or v:
            pairs.append((k, v))
    if not pairs:
        return ""
    if len(pairs) == 1 and pairs[0][0] in _POS_KEYS:
        return pairs[0][1]
    return "/".join("%s:%s" % (k, v) if k else v for k, v in pairs)


def spec_flat_str(spec: str) -> str:
    """本地 v3_sku.spec → 与 attrs_flat_str 同口径的字符串。"""
    return attrs_flat_str(_spec_dims(spec or ""))


def attrs_values_str(attrs) -> str:
    """只拼「值」不拼键名 —— 覆盖第三种线上形态（拍平后键变成「销售属性1」且值里不含原键）。

    实测：本地 spec='规格:1.【禅心茶桌100】…'
          线上   attrs=[('销售属性1','1.【禅心茶桌100】…')]   ← 值里没有「规格:」
    所以必须再给一版「纯值串」当候选键。
    """
    vals = []
    for a in (attrs or []):
        if not isinstance(a, dict):
            continue
        v = (a.get("attr_value") or "").strip()
        if v:
            vals.append(v)
    return "/".join(vals)


def sku_lookup_keys(attrs) -> list:
    """一个规格的全部候选查找键（精确 tuple 优先，其次归一化串，最后纯值串）。"""
    keys = [_sku_key(attrs), attrs_flat_str(attrs), attrs_values_str(attrs)]
    out = []
    for k in keys:
        if k and k not in out:
            out.append(k)
    return out


def fill_sku_thumb(product_id: int | str, url_map: dict,
                   dry_run: bool = True, timeout: float = 60) -> dict:
    """**只补 SKU 图**：读回商品 → 按属性配对 → 写 thumb_img → update → 回读校验。

    url_map: {_sku_key(attrs): "https://mmecimage..."}
             键用 wxshop._sku_key(本地 _spec_dims(spec)) 生成；对不上的规格跳过。

    dry_run=True 只报告会改什么（不写线上）。
    返回 {"ok", "total", "matched", "changed", "unmatched":[...], "errcode", "errmsg", "verify"}
    """
    g = get_product(product_id)
    if not g.get("ok"):
        return {"ok": False, "error": "读商品失败: %s" % (g.get("errmsg") or g.get("errcode"))}
    ep = dict(g.get("edit_product") or {})
    skus = [dict(x) for x in (ep.get("skus") or [])]
    out = {"ok": True, "total": len(skus), "matched": 0, "changed": 0,
           "url_map_size": len(url_map or {}), "unmatched": [],
           "dry_run": bool(dry_run), "product_id": str(product_id)}
    for s in skus:
        cand = sku_lookup_keys(s.get("sku_attrs"))
        u = ""
        for kk in cand:
            u = (url_map or {}).get(kk, "") or ""
            if u:
                break
        if not u:
            # ★ 2026-10-29：把「键对不上」与「本地没图」分开报 ——
            #   原来统一写「属性配不上」，导致 89 件被误诊（实际有 65 张本地图的也被归进去）。
            hit_key = any(kk in (url_map or {}) for kk in cand)
            out["unmatched"].append({
                "sku_id": str(s.get("sku_id") or ""),
                "reason": "本地无图（键对上了但没有可用图）" if hit_key else "键不匹配（两边属性写法不同）",
                "attrs": [[a.get("attr_key"), a.get("attr_value")] for a in (s.get("sku_attrs") or [])]})
            continue
        out["matched"] += 1
        if (s.get("thumb_img") or "") != u:
            s["thumb_img"] = u
            out["changed"] += 1
    if dry_run:
        out["would_change"] = out["changed"]
        return out
    if not out["changed"]:
        out["updated"] = False
        out["note"] = "所有规格图已经是目标值，无需改动"
        return out
    ep["skus"] = skus
    u = update_product(product_id, ep, timeout=timeout)
    out.update({"updated": True, "errcode": u.get("errcode"),
                "errmsg": u.get("errmsg"), "ok": bool(u.get("ok"))})
    # 回读校验
    g2 = get_product(product_id)
    ep2 = (g2.get("edit_product") or {})
    sk2 = ep2.get("skus") or []
    out["verify"] = {
        "ok": bool(g2.get("ok")),
        "skus": len(sk2),
        "with_thumb": sum(1 for x in sk2 if x.get("thumb_img")),
        "head_imgs": len(ep2.get("head_imgs") or []),
        "desc_imgs": len((ep2.get("desc_info") or {}).get("imgs") or []),
        "title": (ep2.get("title") or "")[:40],
    }
    return out


def listing(product_id: int | str) -> dict:
    """上架（会触发平台审核）。"""
    r = _post("/channels/ec/product/listing", {"product_id": int(product_id)})
    return {"ok": r.get("errcode") == 0, "errcode": r.get("errcode"),
            "errmsg": r.get("errmsg")}


# ---------------------------------------------------------------- 从 v3 库组装

# 模板里那些「平台必需的骨架字段」—— 不跟商品内容走，直接复制
_SKELETON_KEYS = ("express_info", "after_sale_info", "extra_service",
                  "limited_info", "deliver_method", "deliver_acct_type",
                  "brand_id", "product_type", "aftersale_desc", "size_chart")

# ---------------------------------------------------------------- 按店兜底骨架
# ★ 2026-11-08：跨店读模板必失败。
#   5.0 界面写死模板商品 10001773856919（A店欢轴的茶台），B店崇耿没有这件货 →
#   product/get 报 10020052「商品不存在」→ build_from_db 第一步 return，
#   142 件全部「组装失败 读模板失败」，图片上传根本没机会跑（主人日志实证）。
#   更隐蔽的是：模板里的 express_info.template_id / after_sale_address_id 都是
#   A 店编号，就算能读到，拿到 B 店建品一样被拒。
#   正解：骨架字段一律用「当前店」实时 API 重新构造，不跨店抄商品。
# 茶台类目（主人在卖的品类，A/B 两店实测都已开通 549074）
_CAT_CHAIN_OLD = [{"cat_id": "1247"}, {"cat_id": "549041"}, {"cat_id": "549074"}]
_CAT_CHAIN_V2 = [{"cat_id": "10000033"}, {"cat_id": "10000837"}, {"cat_id": "549074"}]
# 类目必填属性兜底值（取自 549074 类目详情的合法值，实测必填 4 项）
_ATTR_FALLBACK = [
    {"attr_key": "产地", "attr_value": "中国大陆"},
    {"attr_key": "是否提供上门安装", "attr_value": "是"},
    {"attr_key": "主体材质", "attr_value": "实木生态板材"},
    {"attr_key": "是否支持上门测量", "attr_value": "是"},
    {"attr_key": "商品产地", "attr_value": "中国大陆"},
]
# 按店缓存兜底骨架（运费/地址每店只查一次，换店用 shop_id 分片）
_SHOP_SKEL_CACHE: dict[str, dict] = {}


def shop_skeleton(cat_override: list[str] | None = None,
                  cats_v2: list | None = None) -> dict:
    """用当前店实时 API 构造平台必需骨架，不依赖任何模板商品。

    返回 {skel, cats, cats_v2, attrs}：
      skel     express_info/after_sale_info/extra_service 等 _SKELETON_KEYS
      cats     三级类目（cat_override 优先，否则茶台链路）
      attrs    类目必填属性（模板没给时用 _ATTR_FALLBACK）
    """
    sid = shop_id() or "default"
    hit = _SHOP_SKEL_CACHE.get(sid)
    if hit:
        base = dict(hit)
    else:
        base = {}
        # 运费模板：取当前店列表第一个（B店实测 1102884194004 / A店 1084999806004）
        try:
            f = _post("/channels/ec/merchant/getfreighttemplatelist",
                      {"limit": 20, "offset": 0})
            tids = f.get("template_id_list") or f.get("list") or []
            if tids:
                base["express_info"] = {"template_id": str(tids[0]),
                                        "weight": 100, "express_type": 0}
        except Exception:
            pass
        # 售后地址：取当前店列表第一个（B店实测 142745872094 / A店 139408356002）
        try:
            ad = _post("/channels/ec/merchant/address/list",
                       {"limit": 50, "offset": 0})
            aids = ad.get("address_id_list") or ad.get("list") or []
            if aids:
                base["after_sale_info"] = {"after_sale_address_id": str(aids[0])}
        except Exception:
            pass
        # 6600079：家具/茶台类目必须支持 7 天无理由
        base["extra_service"] = {"seven_day_return": 1, "pay_after_use": 0,
                                 "freight_insurance": 0, "damage_guarantee": 0,
                                 "fake_one_pay_three": 0, "exchange_support": 1}
        base["brand_id"] = "2100000000"
        base["deliver_method"] = 0
        base["product_type"] = 1
        base["limited_info"] = {"period_type": 0, "limited_buy_num": 0}
        _SHOP_SKEL_CACHE[sid] = dict(base)
    if cat_override:
        cats = [{"cat_id": str(c)} for c in cat_override]
    else:
        cats = [dict(x) for x in _CAT_CHAIN_OLD]
    cv2 = list(cats_v2) if cats_v2 else [dict(x) for x in _CAT_CHAIN_V2]
    return {"skel": base, "cats": cats, "cats_v2": cv2,
            "attrs": [dict(a) for a in _ATTR_FALLBACK]}


def _spec_clean(spec: str) -> str:
    """v3_sku.spec 形如「规格:281#黑金花微晶石【40*80*67】」→ 去掉前缀键名。"""
    s = (spec or "").strip()
    for pre in ("规格:", "规格：", "规格", "销售属性1:", "销售属性1："):
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    return s[:60] or "默认规格"


def _spec_dims(spec: str) -> list:
    """把 v3_sku.spec 拆成微信 sku_attrs 的多维结构。

    ★ 2026-10-26 实测：v3 库里的 spec 用 **斜杠 /** 分隔多个维度，
      不是分号。12 个多规格商品全部是 2 维组合。

        「尺码:单人-可拆洗/颜色:水洗蓝」
            → [{attr_key:"尺码", attr_value:"单人-可拆洗"},
               {attr_key:"颜色", attr_value:"水洗蓝"}]

        「颜色:【米白色】【右款】+实木+岩板+免安装/尺寸:【30宽*80深*55高】cm+送货上门」
            → [{attr_key:"颜色", attr_value:"【米白色】【右款】+实木+岩板+免安装"},
               {attr_key:"尺寸", attr_value:"【30宽*80深*55高】cm+送货上门"}]

        「规格:281#黑金花微晶石【40*80*67】」（单维，无斜杠）
            → [{attr_key:"规格", attr_value:"281#黑金花微晶石【40*80*67】"}]

    没写 key 的段按位置命名（销售属性1 / 销售属性2…）。
    """
    s = (spec or "").strip()
    if not s:
        return [{"attr_key": "销售属性1", "attr_value": "默认规格"}]
    segs = [x.strip() for x in s.replace("／", "/").split("/") if x.strip()]
    out: list = []
    for i, seg in enumerate(segs):
        k = v = ""
        for sep in (":", "："):
            idx = seg.find(sep)
            if idx > 0:
                k, v = seg[:idx].strip(), seg[idx + 1:].strip()
                break
        if not v:
            # 整段没有 key:value 形态 → 整段当值，key 按位置起名
            k = "销售属性1" if len(segs) == 1 else "销售属性%d" % (i + 1)
            v = seg
        if v:
            out.append({"attr_key": (k[:20] or "销售属性1"), "attr_value": v[:60]})
    return out or [{"attr_key": "销售属性1", "attr_value": "默认规格"}]


def _spec_flat(spec: str) -> str:
    """单维兜底：把多维 spec 拍平成一个 attr_value（类目不认多维时用）。"""
    dims = _spec_dims(spec)
    return " / ".join(d["attr_value"] for d in dims)[:60] or "默认规格"


# ---------------------------------------------------------------- 商品描述
# ★ 2026-10-26 严重修复：`desc_info.desc` 原来用 `p.get("note")`，
#   而 v3_product.note 里装的是**精筛结果 JSON**（实测 142/142 全是）：
#     {"fine_screen":[{...}], "verdict":"blocked", "layers":{...}, "at":"..."}
#   于是整段检查报告被当成商品描述写进微信后台。
#   真正的描述在 original_payload._raw.detail.desc，实测 142 件**全为空串** ——
#   也就是说源数据本来就没有描述文字（精筛报 no_description 是对的）。
#   所以：有真描述就用，没有就用明确写死的模板话术，**永不捡 note**。
_DESC_FALLBACK = "实木家具 · 厂家直发 · 支持送货上门安装 · 7天无理由退换"
_DESC_BAD_MARKS = ("fine_screen", "verdict", "layers", "\"pos_code\"",
                   "violation_word", "image_suspected_brand")


def _desc_text(p: dict) -> str:
    """取商品描述文字。绝不返回精筛报告、绝不返回 JSON。"""
    try:
        op = json.loads(p.get("original_payload") or "{}")
        d = ((op.get("_raw") or {}).get("detail") or {})
        t = (d.get("desc") or "").strip()
        if t and not t.startswith("{") and not any(m in t for m in _DESC_BAD_MARKS):
            return t[:2000]
    except Exception:  # noqa: BLE001
        pass
    return _DESC_FALLBACK


def _desc_is_dirty(s: str) -> bool:
    """描述里混进了精筛报告 / JSON 的特征串。"""
    s = s or ""
    return s.lstrip().startswith("{") or any(m in s for m in _DESC_BAD_MARKS)


# ---------------------------------------------------------------- 模板缓存
# ★ 2026-10-26：`build_from_db` 每次都 `get_product(template_id)`，
#   批量预览 142 件 = 142 次网络请求 = 46.8 秒（实测）。
#   模板在一次批量里不会变，缓存 5 分钟就够；改用缓存后应降到秒级。
_TPL_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
_TPL_TTL = 300.0


def get_template_cached(template_id: int) -> dict:
    """读模板商品，带 TTL 缓存。**按店分片**：A 店的模板商品号在 B 店不存在。"""
    now = time.time()
    key = (shop_id(), int(template_id))
    hit = _TPL_CACHE.get(key)
    if hit and (now - hit[0]) < _TPL_TTL:
        return hit[1]
    g = get_product(int(template_id))
    if not g.get("ok"):
        return {"ok": False, "error": "读模板失败: %s" % g.get("errmsg") or g.get("error")}
    _TPL_CACHE[key] = (now, g)
    return g


def clear_template_cache() -> int:
    """清模板缓存（换了模板商品、或模板被改过时调用）。"""
    n = len(_TPL_CACHE)
    _TPL_CACHE.clear()
    return n


def build_from_db(conn: sqlite3.Connection, pid: int, template_id: int,
                  title: str = "", cat_override: list[str] | None = None,
                  upload: bool = True, max_head: int = 9, max_desc: int = 20,
                  price_markup: float = 1.0, stock_per_sku: int = 7,
                  sku_limit: int = 0, multi_dim: bool = True,
                  skip_upload: bool = False, reuse_urls: list[str] | None = None,
                  reuse_desc: list[str] | None = None) -> dict:
    """把 v3 库里的一件商品，组装成 product/add 的 payload。

    title         不填则用原标题
    cat_override  三级 cat_id 列表，不填则用模板商品的类目
    upload        True = 真上传图片（慢）；False = 只组装结构（不联网）
    price_markup  价格倍数（1.0 = 原价；2.0 = 翻一倍）
    stock_per_sku **每个 SKU 的库存件数**，默认 7（用户口径：每个 SKU 七件货）
    sku_limit     >0 = 只保留前 N 个规格；**默认 0 = 全部上，不截断**
                  ★ 2026-10-26 纠正：之前把「每个 SKU 七件货」误解成「只上 7 个 SKU」，
                    多规格商品被砍掉 95%（1398 从 136 砍到 7）。规格数量不该减。
    multi_dim     True = 按 spec 里的 `/` 拆成多维 sku_attrs（尺码/颜色…）
                  False = 拍平成单维「销售属性1」（类目不认多维时的兜底开关）
    skip_upload   跳过上传（配合 reuse_urls 用，避免同一商品重复传图）
    reuse_urls    已经传好的主图 URL（重试时用，省一次上传）
    reuse_desc    已经传好的详情图 URL
    """
    p = conn.execute("SELECT * FROM v3_product WHERE product_id=?", (pid,)).fetchone()
    if not p:
        return {"ok": False, "error": "库里没有 product_id=%s" % pid}
    p = dict(p)

    # 1. 模板骨架
    # ★ 2026-11-08：模板商品是 A 店的，B 店读不到；且模板里的运费/售后地址
    #   都是 A 店编号，跨店无效。两种情况下都用「当前店」实时 API 兜底构造。
    tpl: dict = {}
    fb = None
    if template_id:
        g = get_template_cached(int(template_id))
        if not g.get("ok"):
            # 读不到模板（B店常态）→ 不再直接失败，改用当前店兜底骨架
            fb = shop_skeleton(cat_override)
        else:
            tpl = g["edit_product"]
    else:
        fb = shop_skeleton(cat_override)
    if fb is not None:
        skel = dict(fb["skel"])
        cats = fb["cats"]
        cats_v2 = fb["cats_v2"]
    else:
        skel = {k: tpl[k] for k in _SKELETON_KEYS if k in tpl and tpl[k] is not None}
        # 即使模板读到了，运费/售后地址仍强制用当前店自己的（模板是别店的编号）
        cur = shop_skeleton(cat_override)
        if cur["skel"].get("express_info"):
            skel["express_info"] = cur["skel"]["express_info"]
        if cur["skel"].get("after_sale_info"):
            skel["after_sale_info"] = cur["skel"]["after_sale_info"]
        if not skel.get("extra_service"):
            skel["extra_service"] = cur["skel"]["extra_service"]
        cats = [{"cat_id": str(c)} for c in (cat_override or
                [x.get("cat_id") for x in (tpl.get("cats") or [])])]
        cats_v2 = tpl.get("cats_v2") or cur["cats_v2"]

    # 3. 图片
    imgs_main = [r["current_local_path"] for r in conn.execute(
        "SELECT current_local_path FROM v3_image WHERE product_id=? AND role='main'"
        " AND current_local_path IS NOT NULL AND current_local_path<>''"
        " ORDER BY sequence, image_id", (pid,))]
    imgs_desc = [r["current_local_path"] for r in conn.execute(
        "SELECT current_local_path FROM v3_image WHERE product_id=? AND role='detail'"
        " AND current_local_path IS NOT NULL AND current_local_path<>''"
        " ORDER BY sequence, image_id", (pid,))]

    head_urls: list[str] = list(reuse_urls or [])
    desc_urls: list[str] = list(reuse_desc or [])
    up_stat: dict = {"head": None, "desc": None, "reused": bool(reuse_urls)}
    if upload and not skip_upload and not reuse_urls:
        r1 = upload_many(imgs_main, limit=max_head, conn=conn)
        head_urls = r1["urls"]
        up_stat["head"] = {"ok": r1["n_ok"], "fail": r1["n_fail"],
                           "failed": r1["failed"][:3]}
        r2 = upload_many(imgs_desc, limit=max_desc, conn=conn)
        desc_urls = r2["urls"]
        up_stat["desc"] = {"ok": r2["n_ok"], "fail": r2["n_fail"],
                           "failed": r2["failed"][:3]}
    elif reuse_urls and not desc_urls and upload:
        r2 = upload_many(imgs_desc, limit=max_desc, conn=conn)
        desc_urls = r2["urls"]
        up_stat["desc"] = {"ok": r2["n_ok"], "fail": r2["n_fail"],
                           "failed": r2["failed"][:3]}

    # 4. SKU
    # ★ 2026-10-29 修（主人：上传到微店的商品所有 SKU 图片都没有上传）
    #   根因：这里原来只 SELECT spec/price/stock/img_url，组装 skus 时**只写价格与库存**，
    #        `thumb_img`（微信 SKU 图的字段名，实测模板 skus[0] 的 key 就是它）从未被赋值。
    #        平台因此认为每个规格都没有图 —— 后台看起来就是「SKU 图片全空」。
    #   现在：按 (product_id, sku_id) 找到 role='sku' 的本地文件 → 上传拿 mmecimage → 填 thumb_img。
    #        同一文件只传一次（同色多尺寸共用一张）；找不到本地文件就留空，不编地址。
    raw_skus = [dict(s) for s in conn.execute(
        "SELECT s.spec, s.price, s.stock, s.img_url, s.sku_id,"
        " (SELECT i.current_local_path FROM v3_image i"
        "   WHERE i.product_id=s.product_id AND i.role='sku' AND i.sku_id=s.sku_id"
        "     AND i.current_local_path<>'' ORDER BY i.image_id LIMIT 1) AS sku_local"
        " FROM v3_sku s WHERE s.product_id=?"
        " ORDER BY s.seq, s.sku_id", (pid,))]
    sku_paths: list[str] = []
    for _s in raw_skus:
        _p = _s.get("sku_local") or ""
        if _p and _p not in sku_paths:
            sku_paths.append(_p)
    sku_img_map: dict[str, str] = {}
    if upload and not skip_upload and sku_paths:
        sku_img_map = upload_map(sku_paths, conn=conn)
    sku_total = len(raw_skus)
    trimmed = 0
    if sku_limit and sku_total > sku_limit:
        # 截断时保留**价格最高**的几个？不 —— 保留原顺序前 N 个，
        # 因为 v3_sku 的 seq 就是采集时的展示顺序（通常第一个是主推款）。
        raw_skus = raw_skus[:sku_limit]
        trimmed = sku_total - sku_limit

    skus: list[dict] = []
    dim_keys: set = set()
    for s in raw_skus:
        price_yuan = float(s.get("price") or p.get("price_min") or 0) * price_markup
        if price_yuan <= 0:
            continue
        if multi_dim:
            attrs_one = _spec_dims(s.get("spec"))
        else:
            attrs_one = [{"attr_key": "销售属性1",
                          "attr_value": _spec_flat(s.get("spec"))}]
        for d in attrs_one:
            dim_keys.add(d["attr_key"])
        skus.append({
            "sale_price": int(round(price_yuan * FEN)),
            # ★ 每个 SKU 的库存件数（默认 7），不是 SKU 的个数
            "stock_num": max(0, int(stock_per_sku)),
            "sku_attrs": attrs_one,
            # ★ 2026-10-29：规格图（微信字段名 thumb_img）。空串 = 该规格没有图。
            "thumb_img": sku_img_map.get(s.get("sku_local") or "", ""),
        })

    # 5. 属性：模板有就用模板（按标题微调），模板没读到用类目必填兜底
    base_attrs = (tpl.get("attrs") or
                  (fb["attrs"] if fb is not None else shop_skeleton(cat_override)["attrs"]))
    attrs = [dict(a) for a in base_attrs]

    payload = dict(skel)
    payload.update({
        "title": (title or p.get("original_title") or "")[:60],
        "cats": cats,
        "head_imgs": head_urls,
        # ★ desc 只能是描述文字：源数据没有就用模板话术。
        #   绝不能用 p["note"] —— 那是精筛报告 JSON。
        "desc_info": {"imgs": desc_urls, "desc": _desc_text(p)},
        "skus": skus,
        "attrs": attrs,
    })
    if cats_v2:
        payload["cats_v2"] = cats_v2
    if skus:
        payload["min_price"] = min(s["sale_price"] for s in skus)

    # 校验（本地，不联网）
    problems: list[str] = []
    warns: list[str] = []
    if not payload["title"]:
        problems.append("标题为空")
    # ★ 2026-10-26 修：预览模式（upload=False）不联网就没有 mmecimage URL，
    #   原来一律判「没有主图 URL」，导致 142 件全被判不可建（预览 100% 失败）。
    #   正确判据：该模式下能不能**真建出来** ——
    #     真建模式 → 必须有已上传的 URL（否则 payload 里 head_imgs 是空的，平台会拒）
    #     预览模式 → 只要求本地有图文件（真建时会上传）
    if not head_urls:
        if upload:
            problems.append("没有主图 URL")
        elif not imgs_main:
            problems.append("本地没有主图文件")
    if not skus:
        problems.append("没有可用 SKU（价格或库存缺失）")
    if not cats:
        problems.append("没有类目")
    # ★ 2026-10-26 新增：描述里绝不能混进精筛报告 / JSON
    _d = (payload.get("desc_info") or {}).get("desc") or ""
    if _desc_is_dirty(_d):
        problems.append("描述里混入了精筛报告/JSON（前 40 字：%s）" % _d[:40])
    # ★ 库存必须是 stock_per_sku，不能沿用库里脏数据（实测有 33 万、100 万的占位值）
    _bad_stock = [s["stock_num"] for s in skus if s["stock_num"] != int(stock_per_sku)]
    if _bad_stock:
        problems.append("有 %d 个 SKU 库存不等于 %d：%s"
                        % (len(_bad_stock), stock_per_sku, sorted(set(_bad_stock))[:5]))
    if sku_limit and sku_total < sku_limit:
        warns.append("库里只有 %d 个规格，不足 %d —— 保留原样，不编造规格"
                     % (sku_total, sku_limit))
    if trimmed:
        warns.append("规格从 %d 个截到 %d 个" % (sku_total, sku_limit))
    if len(dim_keys) >= 2:
        warns.append("多维规格：%s（%d 个组合）"
                     % (" × ".join(sorted(dim_keys)), len(skus)))
    elif dim_keys:
        warns.append("单维规格：%s" % sorted(dim_keys)[0])
    # 库存：统一改成 stock_per_sku；库里原有库存只在「不覆盖」时才提示差异
    _orig_stock_sum = sum(int(s.get("stock") or 0) for s in raw_skus[:len(skus)])
    if stock_per_sku > 0 and _orig_stock_sum and _orig_stock_sum != stock_per_sku * len(skus):
        warns.append("库存统一为每 SKU %d 件（库里原合计 %d 件）"
                     % (stock_per_sku, _orig_stock_sum))

    return {"ok": not problems, "problems": problems, "warns": warns,
            "payload": payload, "upload": up_stat,
            # ★ 顶层平铺一份，界面直接读（原来只在 stats 子字典里，界面读顶层拿不到 → 全显示 0/None）
            #   判据用「head_urls 有没有真的拿到」而不是 upload 标志 ——
            #   preview 走 upload=False，但也有 skip_upload=True（不传图但 upload 标志没改）的路径，
            #   只看 upload 会误判成「已上传」而显示 0。
            #   真传成功 → 给已传张数（能看出失败）；没传 → 给本地有几张。
            "head": len(head_urls) if head_urls else len(imgs_main),
            "desc": len(desc_urls) if desc_urls else len(imgs_desc),
            "sku": len(skus),
            "sku_total": sku_total,
            # ★ 2026-10-29：SKU 图口径。sku_img = 已拿到地址的规格数（真建）；
            #   预览时不上传，恒 0，所以同时给 sku_img_local = 本地有几张能填。
            "sku_img": sum(1 for x in skus if x.get("thumb_img")),
            "sku_img_local": len(sku_paths),
            "sku_dims": sorted(dim_keys),
            "stock_per_sku": int(stock_per_sku),
            "head_local": len(imgs_main),
            "desc_local": len(imgs_desc),
            "stats": {"head": len(head_urls), "desc": len(desc_urls),
                      "sku": len(skus), "sku_total": sku_total,
                      "sku_trimmed": trimmed,
                      "sku_dims": sorted(dim_keys),
                      "stock_per_sku": int(stock_per_sku),
                      "price_markup": price_markup,
                      "attr": len(attrs),
                      "title_len": len(payload["title"]),
                      "has_local_main": len(imgs_main),
                      "has_local_desc": len(imgs_desc),
                      # ★ 2026-10-29 SKU 图覆盖：本地可填张数 / 已拿到地址的规格数 / 传失败张数
                      "sku_img_local": len(sku_paths),
                      "sku_img": sum(1 for x in skus if x.get("thumb_img")),
                      "sku_img_fail": (sum(1 for p in sku_paths
                                           if not sku_img_map.get(p, ""))
                                       if sku_paths else 0)}}


def status(conn: sqlite3.Connection | None = None) -> dict:
    """连通性自检。"""
    out: dict[str, Any] = {"token_ok": False, "product_total": None,
                           "category_count": None}
    try:
        tok = token()
        out["token_ok"] = bool(tok)
        out["token_len"] = len(tok)
    except Exception as e:
        out["token_error"] = str(e)
        return out
    # ★ status=6 才是「未上架/草稿」的真实状态码（2026-10-26 实测纠正，原来写的 0）
    r = list_products(6, 100)
    out["product_total"] = r.get("total")
    out["product_status_used"] = 6
    c = list_categories()
    out["category_count"] = c.get("count")
    return out
