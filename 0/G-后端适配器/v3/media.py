"""媒体下载与证据管线（2.5 的核心动作：从"只有 URL"变成"本地有文件 + 可 OCR"）。

三条铁律（对应任务书）：
  1. **URL 数 ≠ 下载成功数**。每条 v3_image 单独记 `dl_state`：
     pending / ok / fail / skipped，失败同时写 `download_hint`（人话原因 + 是否可重试）。
  2. **同一 URL 只下一份文件**，但**每条引用行都指向它**（角色/顺序不因去重丢失）。
  3. **视频必须导入当时就下**，不留给"独立窗口展示"；链接过期要判「不可重试·需重新采集」，
     而不是反复重试同一条死链。

公开接口：
    run(conn, pids, roles=None, workers=4, ocr_after=True, limit=0, progress=None) -> stats
    download_pending(conn, pids, ...)      同 run（语义别名）
    update_video_urls(conn, pid, urls)     换新视频地址 → 重置状态 → 立刻下载
    stats(conn, pids)                      按角色/状态统计（给界面用）
    dedupe_report(conn, pids)              引用数 vs 唯一 URL 数
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from . import images as _images
from . import store

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REFERER = "https://wxapp.tc.qq.com/"

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
VID_EXT = (".mp4", ".mov", ".m4v", ".webm")

ST_OK = "ok"
ST_FAIL = "fail"
ST_PENDING = "pending"
ST_SKIP = "skipped"
ST_DEAD = "dead"          # 源头死链 / 平台占位图：采集数据本身如此，重试无用，别空转


def _IMG_MAGIC(h: bytes) -> bool:
    """真图片魔数（至少传前 12 字节）。占位缩略图也带真魔数，要再配最小尺寸判定。"""
    return (h[:2] == b"\xff\xd8" or h[:8] == b"\x89PNG\r\n\x1a\n"
            or (h[:4] == b"RIFF" and h[8:12] == b"WEBP") or h[:4] == b"GIF8")


# ---------------------------------------------------------------- 小工具

def token_expiry(url: str) -> int | None:
    """从 URL 取 wdis_t（微信 CDN 的失效时刻，Unix 秒）。取不到 None。"""
    try:
        from urllib.parse import parse_qs, urlparse
        v = (parse_qs(urlparse(url).query).get("wdis_t") or [""])[0]
        return int(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _ext_of(url: str, default: str) -> str:
    e = os.path.splitext(url.split("?")[0])[1].lower()
    return e if e in IMG_EXT + VID_EXT else default


def _set_state(conn: sqlite3.Connection, image_id: int, state: str,
               hint: str = "", path: str = "", sha: str = "",
               size: int | None = None) -> None:
    sets = ["dl_state=?", "download_hint=?", "updated_at=?"]
    args: list[Any] = [state, hint or "", store.now()]
    if path:
        sets.append("current_local_path=?")
        args.append(path)
    if sha:
        sets.append("content_sha256=?")
        args.append(sha)
    if size is not None:
        sets.append("note=?")
        args.append("bytes=%d" % size)
    args.append(image_id)
    # ★ 2026-10-26：一次下载完成就 UPDATE 一次；被别的写者占着锁时退避重试，
    #   不要因为 3.0/4.0/别的页签正在写就把这张图的下载结果丢掉。
    store.retry_locked(lambda: conn.execute(
        "UPDATE v3_image SET %s WHERE image_id=?" % ", ".join(sets), args))


def _fetch(url: str, timeout: int, want: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = resp.read()
    if not data:
        raise ValueError("服务器返回 0 字节")
    if want == "image" and ctype and not ctype.startswith("image/"):
        raise ValueError("返回的不是图片（Content-Type=%s）" % ctype)
    if want == "video" and ctype and not (ctype.startswith("video/") or ctype == "application/octet-stream"):
        raise ValueError("返回的不是视频（Content-Type=%s）" % ctype)
    return data, ctype


# ---------------------------------------------------------------- 单条下载

def _download_url(url: str, dst_dir: str, want: str, timeout: int) -> dict:
    """下载一个 URL 到 dst_dir（文件名按 URL 的 sha1，天然幂等/去重）。"""
    os.makedirs(dst_dir, exist_ok=True)
    # 过期判据先看，能省一次必死的请求
    exp = token_expiry(url)
    if exp and exp <= int(time.time()):
        return {"ok": False, "hint": "链接已过期（wdis_t=%d）· 需重新采集，重试无用" % exp,
                "retryable": False, "expired": True}
    ext = _ext_of(url, ".mp4" if want == "video" else ".jpg")
    dst = os.path.join(dst_dir, "%s_%s%s" % (want, hashlib.sha1(url.encode()).hexdigest()[:12], ext))
    if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
        return {"ok": True, "path": dst, "already": True, "bytes": os.path.getsize(dst),
                "sha256": _images.sha256_file(dst)}
    try:
        data, ctype = _fetch(url, timeout, want)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        code = getattr(e, "code", None)
        # ★ 2026-11-09：拼多多 pfs/water-mark-permanent 是**带 q-sign 签名的临时地址**，
        #   导出包里存的是采集那一刻的快照，过期后必返 401/403。重试 3 次只是白等，
        #   且每次都把同一条引用重新记 fail。判 dead（不可重试），写明"需重新采集"。
        if code in (401, 403) and ("pinduoduo.com" in url or "pddpic.com" in url):
            return {"ok": False, "dead": True, "expired": True, "retryable": False,
                    "hint": "源地址已失效（HTTP %s，带签名的临时图片地址，采集时已过期）"
                            "· 需重新采集，重试无用" % code}
        return {"ok": False, "hint": "下载失败：%s" % msg, "retryable": True}
    # ★ 图片最小尺寸从 512 提到 1024：实测有一批 545~830 字节的**平台占位缩略图**
    #   （魔数是真 JPEG/PNG，但只有几百字节）被旧阈值当成成功写进了库。
    lo = 1024 if want == "image" else 4096
    if len(data) < lo:
        # 平台给的占位/防盗链小图，重试一百次还是同一张 → 判 dead，不再空转
        if want == "image" and _IMG_MAGIC(data[:12]) and any(
                h in url for h in ("pddpic.com", "wxapp.tc.qq.com", "pinduoduo.com")):
            return {"ok": False, "dead": True, "retryable": False,
                    "hint": "源头就是占位小图（%d 字节，平台占位/防盗链图）"
                            "· 需重新采集，重试无用" % len(data)}
        return {"ok": False, "hint": "文件太小（%d 字节），可能不是真%s"
                % (len(data), want), "retryable": True}
    if want == "video" and b"ftyp" not in data[:64]:
        return {"ok": False, "hint": "文件头不像 mp4（前 32 字节：%r）" % data[:32],
                "retryable": True}
    with open(dst, "wb") as f:
        f.write(data)
    return {"ok": True, "path": dst, "bytes": len(data),
            "sha256": _images.sha256_file(dst), "ctype": ctype}


# ---------------------------------------------------------------- OCR

def _ocr_one(conn: sqlite3.Connection, image_id: int) -> dict:
    """调 images.ocr_image(conn, img_dict)。

    ★ 实测签名是 `ocr_image(conn, img: dict, words=None, force=False)` ——
      传 image_id 进去会在 `img["image_id"]` 那行 KeyError。这里自己取整行再传。
    """
    fn = getattr(_images, "ocr_image", None)
    if fn is None:
        return {"ok": False, "hint": "images.ocr_image 不存在"}
    try:
        row = conn.execute("SELECT * FROM v3_image WHERE image_id=?", (image_id,)).fetchone()
        if not row:
            return {"ok": False, "hint": "图不存在"}
        r = fn(conn, dict(row))
        return r if isinstance(r, dict) else {"ok": True, "raw": r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "hint": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 主流程

def _rows(conn: sqlite3.Connection, pids: list[int], roles: list[str] | None) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(pids), 400):
        chunk = pids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        sql = ("SELECT image_id, product_id, role, sequence, original_url, dl_state,"
               " current_local_path FROM v3_image WHERE product_id IN (%s)" % ph)
        args: list[Any] = list(chunk)
        if roles:
            sql += " AND role IN (%s)" % ",".join("?" * len(roles))
            args += list(roles)
        out += [dict(r) for r in conn.execute(sql, args)]
    return out


def run(conn: sqlite3.Connection, pids: list[int], roles: list[str] | None = None,
        workers: int = 4, ocr_after: bool = True, limit: int = 0,
        progress: Callable[[dict], None] | None = None,
        retry_failed: bool = True) -> dict:
    """按商品批量下载媒体（图 + 视频），同一 URL 只下一份。

    roles: None=全部；['main','detail','sku']=只下图；['video']=只下视频。
    """
    rows = _rows(conn, list(pids), roles)
    # 源头死链（dead）不再进待下载队列：不空转、不重复记 fail。其余判据保持原样。
    todo = [r for r in rows
            if r["original_url"].startswith("http")
            and r["dl_state"] != ST_DEAD
            and (r["dl_state"] != ST_OK or not (r["current_local_path"] or ""))]
    if not retry_failed:
        todo = [r for r in todo if r["dl_state"] != ST_FAIL]
    no_url = [r for r in rows if not r["original_url"].startswith("http")]
    if limit:
        todo = todo[:limit]

    # 同 URL 归并：一份文件，多条引用
    by_url: dict[str, list[dict]] = {}
    for r in todo:
        by_url.setdefault(r["original_url"], []).append(r)

    st = {"entries": len(rows), "todo": len(todo), "unique_urls": len(by_url),
          "downloaded": 0, "failed": 0, "expired": 0, "no_url": len(no_url),
          "files": 0, "bytes": 0, "ocr_ok": 0, "ocr_fail": 0, "ocr_seg": 0,
          "errors": {}}
    done = {"n": 0}
    ocr_pids: set[int] = set()

    def task(url: str, refs: list[dict]) -> dict:
        first = refs[0]
        want = "video" if first["role"] == "video" else "image"
        # 2026-10-26：落到 <商品ID>\<角色>\ 子目录（主人要求 SKU 图单独一个文件夹）
        d = store.role_dir(int(first["product_id"]), first.get("role"))
        r = _download_url(url, d, want, timeout=90 if want == "video" else 30)
        r["refs"] = refs
        r["want"] = want
        return r

    lock_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(task, u, refs): u for u, refs in by_url.items()}
        for fu in as_completed(futs):
            try:
                r = fu.result()
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "hint": "线程异常：%s" % e, "refs": [], "want": "image"}
            refs = r.get("refs") or []
            for ref in refs:
                if r.get("ok"):
                    _set_state(conn, int(ref["image_id"]), ST_OK, r.get("ctype") or "",
                               path=r.get("path") or "", sha=r.get("sha256") or "",
                               size=r.get("bytes"))
                else:
                    _set_state(conn, int(ref["image_id"]),
                               ST_DEAD if r.get("dead") else ST_FAIL,
                               r.get("hint") or "", path="")
            if r.get("ok"):
                st["files"] += 1
                st["bytes"] += int(r.get("bytes") or 0)
                st["downloaded"] += len(refs)
                if not r.get("already") and refs and refs[0]["role"] != "video":
                    ocr_pids.add(int(refs[0]["product_id"]))
                    lock_rows.append((refs[0], r))
            else:
                st["failed"] += len(refs)
                if r.get("expired"):
                    st["expired"] += len(refs)
                e = (r.get("hint") or "")[:60]
                st["errors"][e] = st["errors"].get(e, 0) + 1
            done["n"] += 1
            # ★ 2026-10-26：**分批提交**。
            #   原来整个下载阶段（实测 5878 张图）只累积 UPDATE、到循环结束才
            #   一次 commit —— 那意味着整段下载期间库被一个长事务的 RESERVED 锁独占，
            #   别的页面（2.5 点下载 / 3.0 精筛 / 4.0 检测）一写就是
            #   `database is locked`。用户看到的就是「下载没建立起来」。
            #   每 25 条提交一次：锁窗口从"整段下载"缩到"25 张图的耗时"。
            if done["n"] % 25 == 0:
                store.retry_locked(conn.commit)
            if progress and (done["n"] % 5 == 0 or done["n"] == len(by_url)):
                try:
                    progress({"done": done["n"], "total": len(by_url),
                              "downloaded": st["downloaded"], "failed": st["failed"]})
                except Exception:  # noqa: BLE001
                    pass
    store.retry_locked(conn.commit)

    # ---- OCR：只对"本次真下下来的图"跑，避免整库重扫
    if ocr_after and lock_rows:
        for ref, r in lock_rows:
            o = _ocr_one(conn, int(ref["image_id"]))
            if o.get("ok") or o.get("state") == "ok":
                st["ocr_ok"] += 1
                st["ocr_seg"] += int(o.get("n_seg") or o.get("segments") or 0)
            else:
                st["ocr_fail"] += 1
                e = str(o.get("hint") or o.get("error") or "")[:60]
                if e:
                    st["errors"]["ocr:" + e] = st["errors"].get("ocr:" + e, 0) + 1
        store.retry_locked(conn.commit)

    st["errors"] = dict(sorted(st["errors"].items(), key=lambda kv: -kv[1])[:8])
    st["retryable_hint"] = ("expired>0 说明视频链接整批过期，重试无用，需重新采集；"
                            "其它 fail 可再跑一次 run(..., retry_failed=True)")
    return st


def download_pending(conn: sqlite3.Connection, pids: list[int], **kw) -> dict:
    return run(conn, pids, **kw)


# ---------------------------------------------------------------- 视频换地址

def update_video_urls(conn: sqlite3.Connection, pid: int, urls: list[str],
                      download: bool = True, workers: int = 2) -> dict:
    """给某件商品换一批新的视频地址（用户重新采集后拿到的），并立刻下载。

    · 按顺序对上已有的 video 槽位（sequence 0..n）；多出来的新增槽位；少的不删（留给用户判断）
    · 换地址会把该槽位状态重置为 pending，旧文件不删（保留证据，改由 current_local_path 指向新文件）
    """
    clean = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip().startswith("http")]
    if not clean:
        return {"ok": False, "error": "没有可用的 http 视频地址"}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM v3_image WHERE product_id=? AND role='video' ORDER BY sequence, image_id",
        (pid,))]
    changed, added = [], []
    for i, u in enumerate(clean):
        if i < len(rows):
            conn.execute(
                "UPDATE v3_image SET original_url=?, current_local_path='', content_sha256='',"
                " dl_state=?, download_hint=?, note='', updated_at=? WHERE image_id=?",
                (u, ST_PENDING, "等待下载（已换新地址）", store.now(), rows[i]["image_id"]))
            changed.append(rows[i]["image_id"])
        else:
            from . import store as _s
            key = _s.slot_key("video", i)
            cur = conn.execute(
                "INSERT INTO v3_image(product_id, slot_key, role, sequence, original_url,"
                " dl_state, download_hint, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,'original',?,?)",
                (pid, key, "video", i, u, ST_PENDING, "新补视频地址", store.now(), store.now()))
            added.append(int(cur.lastrowid))
    conn.commit()
    store.audit(conn, "video_update", "product", str(pid), None,
                {"changed": changed, "added": added, "urls": len(clean)}, actor="视频补链")
    conn.commit()
    out = {"ok": True, "changed": changed, "added": added, "urls": len(clean)}
    if download:
        out["download"] = run(conn, [pid], roles=["video"], workers=workers, ocr_after=False)
    return out


# ---------------------------------------------------------------- 统计

def stats(conn: sqlite3.Connection, pids: list[int]) -> dict:
    ph = ",".join("?" * len(pids)) if pids else "NULL"
    args = list(pids)
    by_role: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT role, dl_state, COUNT(*) n FROM v3_image WHERE product_id IN (%s)"
            " GROUP BY role, dl_state" % ph, args):
        d = by_role.setdefault(r["role"], {})
        d[str(r["dl_state"])] = r["n"]
    total = 0
    ok = 0
    for role, d in by_role.items():
        n = sum(d.values())
        total += n
        ok += d.get(ST_OK, 0)
    return {"entries": total, "downloaded": ok, "by_role": by_role,
            "downloaded_pct": round(ok * 100.0 / total, 1) if total else 0.0}


def dedupe_report(conn: sqlite3.Connection, pids: list[int]) -> dict:
    ph = ",".join("?" * len(pids)) if pids else "NULL"
    rows = [dict(r) for r in conn.execute(
        "SELECT role, original_url, COUNT(*) n FROM v3_image WHERE product_id IN (%s)"
        " GROUP BY role, original_url" % ph, list(pids))]
    entries = sum(r["n"] for r in rows)
    uniq = len({r["original_url"] for r in rows if r["original_url"]})
    dup = sum(r["n"] - 1 for r in rows)
    return {"entries": entries, "unique_urls": uniq, "duplicate_refs": dup,
            "refs_per_url": round(entries / uniq, 2) if uniq else 0.0}
