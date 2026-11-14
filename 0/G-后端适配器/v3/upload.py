# -*- coding: utf-8 -*-
"""v3 · 图床上传（第 5 步：先把数据库里的链接换成自己的图床，才允许出表）

为什么单独一步：导出的表格只认 URL，本地路径进不去。所以出表之前必须保证
「每张要用的图都已经在自己的图床上」。本模块只做这一件事，**不假装成功**。

图床复用：不另写一个图床客户端，直接读 `本地鉴图\\鉴图.py` 里的 HOST / API_KEY 常量
（正则抠出来，**不在本文件里存任何密钥**），协议照它那份实测过的实现：
    POST {HOST}/api/upload   header: X-API-Key
    multipart: file=<二进制>  data: path=<对象键>
    响应里若没有 url 就自己拼 {HOST}/{key}（线上 worker 只回 success/key/size）

依赖：只用标准库（urllib），不引入 requests。
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import keywords, store

JIANTU = Path(r"J:\aixitong\小猪\本地鉴图\鉴图.py")
PIG_READER = JIANTU.parent

_ROLES = ("main", "carousel", "detail", "sku")


# ---------------------------------------------------------------- 图床配置

def _cfg() -> tuple[str, str]:
    """从 鉴图.py 里读图床地址与密钥（只读，不落地）。"""
    try:
        src = JIANTU.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    host = re.search(r'^HOST\s*=\s*"([^"]+)"', src, re.M)
    key = re.search(r'^API_KEY\s*=\s*"([^"]+)"', src, re.M)
    return (host.group(1) if host else ""), (key.group(1) if key else "")


# ---------------------------------------------------------------- 微信图床（首选）
# ★ 2026-10-26：出表也改走微信官方图床，不再依赖自建服务器。
#   理由：微信只认自家 mmecimage.cn 地址（第三方图床填进商品会报 10020061），
#   而且自建图床是免费额度，稳定性和带宽都不如官方；图片占微信空间不花自己的。
#   自建图床保留为**回退通道**：微信凭据缺失或上传失败时才用。

_WX_WARNED: list[str] = []


def _wx():
    """延迟导入 wxshop，避免 upload.py 在无网络/无凭据环境下 import 就炸。"""
    from . import wxshop  # noqa: PLC0415
    return wxshop


def wechat_ready() -> tuple[bool, str]:
    """微信图床能不能用 → (可用?, 原因)。判据是**真能取到 access_token**。"""
    try:
        t = _wx().token()
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)
    if not t:
        return False, "微信 access_token 为空（检查 WX_SHOP_APPID / WX_SHOP_SECRET）"
    return True, ""


def _put_wechat(local: str) -> str:
    """传一张到微信图床 → mmecimage 永久链接。失败抛异常，不返回假地址。"""
    r = _wx().upload_image(local)
    url = (r.get("img_url") or "").strip()
    if url.startswith("http"):
        return url
    raise RuntimeError("微信图床拒绝: errcode=%s %s"
                       % (r.get("errcode"), (r.get("errmsg") or "")[:200]))


# ---------------------------------------------------------------- 待传盘点

def is_replaced(i: dict) -> bool:
    """这张图的当前版本是不是「被替换过」的本地新文件（而不是原始那张）。

    ★ 2026-10-25 阶段二：这是「4.1 修完图，5.0 导出还是原图」的判据核心。
      只认证据：current_local_path 与 original_local_path 不同，或 status 已被标成
      replaced。两者都没有，就是原图。
    """
    cur = (i.get("current_local_path") or "").strip()
    orig = (i.get("original_local_path") or "").strip()
    if (i.get("status") or "") == "replaced":
        return True
    return bool(cur) and bool(orig) and os.path.normcase(cur) != os.path.normcase(orig)


def needs_upload(i: dict) -> tuple[bool, str]:
    """这张图现在还需不需要传图床 → (需要?, 原因)。

    ★ 2026-10-25 阶段二修：原来用 `_best_url()` 判「有没有地址」，而它会把
      original_url 也算进去 —— 于是**任何替换过的图都因为还留着原图 URL 而被判
      「已就绪」，永不重传，导出拿到的始终是原图**。这就是回写闭环断掉的机械原因。
      现在的规则：
        ① 当前版本已有线上地址（current_remote_url / new_remote_url 是 http）→ 不用传
        ② 当前版本被替换过、但没有自己的线上地址 → 必须传（哪怕原图 URL 还躺在库里）
        ③ 没替换过、原图 URL 就是 http → 不用传（原图直接可用）
        ④ 其余 → 必须传（有本地文件就传，没本地文件就是缺料）
    """
    cur_remote = (i.get("current_remote_url") or "").strip()
    if cur_remote.startswith("http"):
        return False, ""
    if is_replaced(i):
        return True, "已替换成本地新图，还没有图床地址"
    if (i.get("original_url") or "").strip().startswith("http"):
        return False, ""
    return True, "没有可用远程地址"


def _local_of(i: dict) -> str:
    """这张图当前版本的本地文件。

    ★ 2026-10-25 阶段二：原代码读 `i["local_path"] / i["file_path"]`，而 v3_image
      真实列名是 `current_local_path / original_local_path` —— store.list_images 只
      返回原始列、不补别名，所以这两处**永远是空**，图床上传一直在报
      「既无本地文件也无地址」，等于从来没成功过。这里按真实列名取值。
    """
    for k in ("current_local_path", "original_local_path"):
        v = (i.get(k) or "").strip()
        if v:
            return v
    return (i.get("local_path") or i.get("file_path") or "").strip()


def pending(c, pack_code: str = "", pids: list[int] | None = None) -> dict:
    """哪些图还没有可用的远程地址（= 还没上图床）。"""
    ids = [int(x) for x in (pids or [])]
    if not ids and pack_code:
        ids = [int(x["product_id"]) for x in store.list_products(c, pack=pack_code,
                                                                 limit=100000)]
    rows, missing = [], 0
    for pid in ids:
        for i in store.list_images(c, pid):
            need, why = needs_upload(i)
            if not need:
                continue
            missing += 1
            lp = _local_of(i)
            rows.append({"image_id": i.get("image_id"), "product_id": pid,
                         "role": i.get("role") or "",
                         "local_path": lp,
                         "original_url": i.get("original_url") or "",
                         "replaced": is_replaced(i),
                         "reason": ("本地文件不存在" if (lp and not os.path.exists(lp))
                                    else (why if lp else "既无本地文件也无地址"))})
    return {"ok": True, "missing": missing, "total": len(rows), "rows": rows[:200],
            "host": _cfg()[0],
            "note": "" if missing == 0 else "这些图必须先上图床，否则出表里是空链接"}


def _best_url(i: dict) -> str:
    """只认真实存在的列。v3_image 里 url 相关列只有 original_url / current_remote_url ——
    `new_remote_url` 这一列**根本不存在**（曾导致每次上传 UPDATE 抛
    no such column: new_remote_url），所以这里彻底不碰它。"""
    return (i.get("current_remote_url") or i.get("original_url") or "")


# ---------------------------------------------------------------- 上传

def _multipart(fields: dict, file_field: str, filename: str, data: bytes,
               ctype: str = "image/jpeg"):
    boundary = "----dsh" + uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                 % (boundary, k, v)).encode("utf-8")
    body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
             "Content-Type: %s\r\n\r\n" % (boundary, file_field, filename, ctype)).encode("utf-8")
    body += data + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode("utf-8")
    return bytes(body), "multipart/form-data; boundary=%s" % boundary


def _cache_url(c, local_path: str, url: str) -> None:
    """把 path→url 写进 `v3_wx_img_cache`。

    ★ 2026-11-04 补的断点：出表（build_xlsx.ImgMap）**只读这张缓存表**，
      而 upload_one / upload_pending 以前只写 `current_remote_url` ——
      于是「上传成功了」但「出表拿不到地址」，表里落回原图或空地址。
      这里复用 publish_guard.img_put（key = abspath|mtime|size），不另造一份缓存。
    """
    try:
        from . import publish_guard as _pg          # noqa: PLC0415
        _pg.img_put(c, local_path, url, os.path.getsize(local_path))
    except Exception:                               # noqa: BLE001
        pass                                        # 缓存写失败不影响上传本身


def _put(host: str, api_key: str, local: str, key: str, tries: int = 3) -> str:
    """传一张，返回线上 URL。失败抛异常（**不返回假地址**）。

    ★ 2026-10-26：**先走微信官方图床**（mmecimage 永久链接），失败或未配置才回退
      自建图床。两者都失败 → 抛异常，current_remote_url 保持空，这张图留在待传清单。
    """
    raw = Path(local).read_bytes()
    if len(raw) < 512:
        raise RuntimeError("文件太小（%d 字节）" % len(raw))

    # ① 微信官方图床（首选）
    wx_err: Exception | None = None
    try:
        return _put_wechat(local)
    except Exception as e:  # noqa: BLE001
        wx_err = e
        if len(_WX_WARNED) < 3:
            _WX_WARNED.append(str(e)[:200])

    # ② 回退自建图床（只有配了 HOST/API_KEY 才有意义）
    if not host or not api_key:
        raise RuntimeError("微信图床失败（%s），且未配置自建图床" % wx_err)

    ctype = mimetypes.guess_type(local)[0] or "image/jpeg"
    body, ct = _multipart({"path": key}, "file", Path(local).name, raw, ctype)
    last: Exception | None = wx_err
    for i in range(tries):
        try:
            req = urllib.request.Request(
                host.rstrip("/") + "/api/upload", data=body, method="POST",
                headers={"X-API-Key": api_key, "Content-Type": ct,
                         "Content-Length": str(len(body))})
            with urllib.request.urlopen(req, timeout=180) as r:
                txt = r.read().decode("utf-8", "replace")
            import json as _json  # noqa: PLC0415
            j = _json.loads(txt)
            if not j.get("success"):
                raise RuntimeError("图床拒绝: %s" % txt[:160])
            return j.get("url") or "%s/%s" % (host.rstrip("/"), j.get("key") or key)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise last or RuntimeError("上传失败")


# ---------------------------------------------------------------- 全部换成微信地址
# ★ 2026-10-27 主人要求：商品要二次上架到抖音，抖音是「拿链接去拉图」，
#   所以出表里的图片地址必须是**公网可拉的微信图床地址（mmecimage.cn）**，
#   不能是自建图床、也不能指望某个代理域名长期有效。
#   本段只做一件事：把「要用的图」全部换成 mmecimage 永久链接。
#   - 只换角色 main / carousel / detail / sku（视频不是图片，微信图片接口不收；
#     视频单独走视频接口，本函数**跳过并如实计数**，不假装成功）
#   - 已经有 mmecimage 地址的跳过（可反复执行、可中断，天然幂等）

WX_PREFIX = "https://mmecimage.cn/"
WX_ROLES = ("main", "carousel", "detail", "sku")


def is_wx(url: str) -> bool:
    return (url or "").strip().startswith(WX_PREFIX)


def pending_wx(c, pack_code: str = "", pids: list[int] | None = None) -> dict:
    """哪些图还没换成微信图床地址。

    判据（三条都要满足才算「要换」）：
      ① 角色是 main/carousel/detail/sku（视频不在内）
      ② current_remote_url 不是 mmecimage 开头
      ③ 本地文件存在（没有本地文件就无从上传，单独计数 tell 主人）
    """
    ids = [int(x) for x in (pids or [])]
    if not ids and pack_code:
        ids = [int(x["product_id"]) for x in store.list_products(c, pack=pack_code,
                                                                 limit=100000)]
    rows, no_local, videos, done = [], 0, 0, 0
    for pid in ids:
        for i in store.list_images(c, pid):
            role = (i.get("role") or "").strip()
            if role == "video":
                videos += 1
                continue
            if role not in WX_ROLES:
                continue
            cur = (i.get("current_remote_url") or "").strip()
            if is_wx(cur):
                done += 1
                continue
            lp = _local_of(i)
            if not lp or not os.path.exists(lp):
                no_local += 1
                continue
            rows.append({"image_id": i.get("image_id"), "product_id": pid,
                         "role": role, "local_path": lp,
                         "old_url": cur or (i.get("original_url") or ""),
                         "replaced": is_replaced(i)})
    return {"ok": True, "total": len(rows), "rows": rows, "no_local": no_local,
            "video_skipped": videos, "already_wx": done,
            "note": ("" if not rows else
                     "这些图要换成微信图床地址，出表才能给抖音直接拉")}


def _shrink_to(local: str, limit_bytes: int = 1900 * 1024) -> str:
    """把超过微信上限的图压到 limit_bytes 以下，返回临时文件路径。

    ★ 2026-10-27 实测：微信图片接口对单文件有大小上限，超了报
      errcode=45002 content size out of limit（实测 2.74MB / 6.35MB / 9.92MB 全被拒，
      而 2MB 以下全部成功）。所以对超限图先降质量、再缩边长，压到 1.9MB 以内。
    临时文件写在 D 盘缓存目录（不进 C 盘、不覆盖原图），调用方用完删不删都行。
    """
    from PIL import Image  # noqa: PLC0415  仅超限时才导入
    src = Image.open(local)
    if src.mode not in ("RGB", "L"):
        src = src.convert("RGB")
    out_dir = Path(os.environ.get("TMP") or r"D:\DSH_Cache\cache\tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / ("wx_%s_%s.jpg" % (Path(local).stem[:24], uuid.uuid4().hex[:6]))
    cur = src
    for _ in range(8):
        for q in (88, 80, 72, 64):
            cur.save(tmp, "JPEG", quality=q, optimize=True)
            if tmp.stat().st_size <= limit_bytes:
                return str(tmp)
        w, h = cur.size
        if min(w, h) <= 800:
            break
        cur = cur.resize((max(1, int(w * 0.85)), max(1, int(h * 0.85))),
                         Image.LANCZOS)
    return str(tmp)          # 尽力了，交给调用方判断是否仍超限


def _put_wechat_fit(local: str) -> str:
    """传微信图床；遇到 45002（文件过大）自动压缩后重试一次。"""
    try:
        return _put_wechat(local)
    except Exception as e:  # noqa: BLE001
        if "45002" not in str(e) and "size out of limit" not in str(e):
            raise
        small = _shrink_to(local)
        before = Path(local).stat().st_size
        after = Path(small).stat().st_size
        try:
            url = _put_wechat(small)
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError("压缩后仍被拒（原 %.1fMB → 压后 %.1fMB）：%s"
                               % (before / 1048576, after / 1048576, e2)) from e2
        if len(_WX_WARNED) < 5:
            _WX_WARNED.append("图过大已压缩上传：%s（%.1fMB → %.1fMB）"
                              % (Path(local).name[-32:], before / 1048576,
                                 after / 1048576))
        return url


def upload_wx(c, pack_code: str = "", pids: list[int] | None = None,
              limit: int = 0,
              progress: Callable[[int, int, int, int], None] | None = None,
              should_stop: Callable[[], bool] | None = None) -> dict:
    """把所有要用的图换成微信图床地址（mmecimage 永久链接）。

    ★ 与 upload_pending 的区别：
      - upload_pending 是「补缺」（原图 URL 能用的就不动）
      - upload_wx 是「全换」（哪怕已有可用的腾讯/自建地址，也换成微信家的）
    失败绝不回退自建图床 —— 主人要的是微信地址，回退成别家等于悄悄换掉结论。
    单张失败只计失败并保留旧地址，不影响整批。
    """
    wx_ok, wx_msg = wechat_ready()
    if not wx_ok:
        return {"ok": False, "error": "微信图床不可用：%s" % wx_msg}
    pl = pending_wx(c, pack_code, pids)
    if not pl.get("ok"):
        return pl
    todo = pl["rows"]
    if limit:
        todo = todo[:int(limit)]
    total = len(todo)
    ok = fail = skip = 0
    errs: list[str] = []
    stopped = False
    for n, it in enumerate(todo, 1):
        if should_stop and should_stop():
            stopped = True
            break
        if progress:
            try:
                progress(n, total, ok, fail)
            except Exception:  # noqa: BLE001
                pass
        lp = it.get("local_path") or ""
        if not lp or not os.path.exists(lp):
            skip += 1
            continue
        try:
            url = _put_wechat_fit(lp)      # 只走微信；超限自动压一次，失败就失败
            c.execute("UPDATE v3_image SET current_remote_url=?, updated_at=?"
                      " WHERE image_id=?", (url, store.now(), int(it["image_id"])))
            c.commit()
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            if len(errs) < 20:
                errs.append("图 %s（%s）：%s: %s"
                            % (it.get("image_id"), it.get("role"),
                               type(e).__name__, str(e)[:160]))
        time.sleep(0.12)
    store.audit(c, "upload.wx", "v3_image", pack_code or "", None,
                {"uploaded": ok, "failed": fail, "skipped": skip,
                 "stopped": stopped, "limit": int(limit)}, actor="上货台")
    c.commit()
    left = pending_wx(c, pack_code, pids)
    return {"ok": True, "total": total, "uploaded": ok, "failed": fail,
            "skipped": skip, "errors": errs, "channel": "wechat",
            "stopped": stopped,
            "remain": len(left["rows"]), "no_local": left["no_local"],
            "video_skipped": left["video_skipped"], "already_wx": left["already_wx"],
            "wechat_warned": list(_WX_WARNED)}


def url_report(c, pack_code: str = "", pids: list[int] | None = None) -> dict:
    """把「每个商品每张图最终会用哪个地址」列出来（导出成表用）。

    这是主人要的「地址获取下来，再出一个表格」：一图一行，
    含商品ID / 角色 / 序号 / 微信地址 / 其它地址 / 本地路径 / 是否已换成微信。
    """
    ids = [int(x) for x in (pids or [])]
    if not ids and pack_code:
        ids = [int(x["product_id"]) for x in store.list_products(c, pack=pack_code,
                                                                 limit=100000)]
    rows = []
    for pid in sorted(ids):
        for i in store.list_images(c, pid):
            cur = (i.get("current_remote_url") or "").strip()
            orig = (i.get("original_url") or "").strip()
            final = cur or orig
            rows.append({
                "product_id": pid,
                "image_id": i.get("image_id"),
                "role": i.get("role") or "",
                "seq": i.get("sequence") or 0,
                "wx": "是" if is_wx(final) else "否",
                "used_url": final,
                "other_url": orig if (cur and cur != orig) else "",
                "local_path": _local_of(i),
                "replaced": "是" if is_replaced(i) else "",
            })
    rows.sort(key=lambda r: (r["role"] != "main", r["product_id"], r["seq"] or 0))
    stat = {"总图数": len(rows),
            "已微信": sum(1 for r in rows if r["wx"] == "是"),
            "非微信(腾讯/自建/空)": sum(1 for r in rows if r["wx"] != "是")}
    return {"ok": True, "rows": rows, "stat": stat, "pids": len(ids)}


def upload_one(c, image_id: int) -> dict:
    host, api_key = _cfg()
    wx_ok, wx_msg = wechat_ready()
    if not wx_ok and (not host or not api_key):
        return {"ok": False,
                "error": "微信图床不可用（%s），也没有自建图床配置" % wx_msg}
    row = None
    for pid in [r[0] for r in c.execute(
            "SELECT DISTINCT product_id FROM v3_image WHERE image_id=?", (image_id,))]:
        for i in store.list_images(c, int(pid)):
            if int(i.get("image_id") or 0) == int(image_id):
                row = i
                break
    if not row:
        return {"ok": False, "error": "没有这张图"}
    lp = _local_of(row)
    if not lp or not os.path.exists(lp):
        return {"ok": False, "error": "本地没有文件，先下载再上传"}
    key = "v3/%s/%s%s" % (row.get("product_id"),
                          hashlib.md5(Path(lp).read_bytes()).hexdigest()[:20],
                          Path(lp).suffix or ".jpg")
    try:
        url = _put(host, api_key, lp, key)
        # ★ 2026-10-25 阶段二：只写 current_remote_url —— 这是出表唯一读到的字段，
        #   也是表里真实存在的列（new_remote_url 列不存在，写它会 OperationalError）。
        c.execute("UPDATE v3_image SET current_remote_url=?, updated_at=?"
                  " WHERE image_id=?", (url, store.now(), int(image_id)))
        c.commit()
        _cache_url(c, lp, url)
    except Exception as ex:          # noqa: BLE001
        # 单张上传失败必须如实返回，不能把异常抛给界面 —— 失败就算失败，
        # current_remote_url 保持原样（空），这张图仍然留在待上传清单里。
        return {"ok": False, "image_id": int(image_id),
                "error": "%s: %s" % (type(ex).__name__, ex),
                "note": "上传失败，仍保留待上传状态；本地文件未动"}
    return {"ok": True, "image_id": int(image_id), "url": url}


def upload_pending(c, pack_code: str = "", pids: list[int] | None = None,
                   limit: int = 0,
                   progress: Callable[[int, int, int, int], None] | None = None) -> dict:
    """把缺地址的图逐张传上去。每 0.2 秒一张（图床偶发 ReadTimeout，慢一点更稳）。

    ★ 2026-10-26：通道优先顺序 = **微信官方图床 → 自建图床**。返回里的 `channel`
      说明这一轮实际用的是哪条，便于排查「为什么 URL 前缀不一样」。
    """
    host, api_key = _cfg()
    wx_ok, wx_msg = wechat_ready()
    if not wx_ok and (not host or not api_key):
        return {"ok": False,
                "error": "微信图床不可用（%s），也没有自建图床配置" % wx_msg}
    channel = "wechat" if wx_ok else "self"
    pl = pending(c, pack_code, pids)
    if not pl.get("ok"):
        return pl
    todo = pl["rows"]
    if limit:
        todo = todo[:int(limit)]
    total = len(todo)
    ok = fail = skip = 0
    errs: list[str] = []
    for n, it in enumerate(todo, 1):
        if progress:
            try:
                progress(n, total, ok, fail)
            except Exception:  # noqa: BLE001
                pass
        lp = it.get("local_path") or ""
        if not lp or not os.path.exists(lp):
            skip += 1
            if len(errs) < 20:
                errs.append("图 %s：%s" % (it.get("image_id"), it.get("reason")))
            continue
        try:
            key = "v3/%s/%s%s" % (it["product_id"],
                                  hashlib.md5(Path(lp).read_bytes()).hexdigest()[:20],
                                  Path(lp).suffix or ".jpg")
            url = _put(host, api_key, lp, key)
            # ★ 只写 current_remote_url（出表读的那个真实列）
            c.execute("UPDATE v3_image SET current_remote_url=?,"
                      " updated_at=? WHERE image_id=?",
                      (url, store.now(), int(it["image_id"])))
            _cache_url(c, lp, url)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            if len(errs) < 20:
                errs.append("图 %s：%s: %s" % (it.get("image_id"), type(e).__name__, e))
        time.sleep(0.2)
    c.commit()
    store.audit(c, "upload.pending", "v3_image", pack_code or "", None,
                {"uploaded": ok, "failed": fail, "skipped": skip}, actor="上货台")
    c.commit()
    return {"ok": True, "total": total, "uploaded": ok, "failed": fail,
            "skipped": skip, "errors": errs, "host": host,
            "channel": channel, "wechat_warned": list(_WX_WARNED)}
