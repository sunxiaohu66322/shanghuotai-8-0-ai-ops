"""文件夹商品卡导入（第二步的唯一入口）—— 一个文件夹 = 一个数据包。

为什么单独一个模块：现有 `pack.convert()` 是「一个文件 = 一个包」，
那是老 UI 逐文件循环调出来的形状。这里按用户口径重做：

    选一个文件夹 → 只扫一层 → 认来源（微店/淘宝/拼多多）
      → 逐卡解析 → 写 v3_product / v3_image
      → **导入完成立刻下载**（图 + 视频，同一 URL 只下一份）
      → 下载成功的图立刻 OCR（用第四步同一套 PP-OCR，带坐标）
      → 记账，并把十项统计写进包的 message

关键取舍：
· 不下载"以后再点" —— 视频链接（wdis_t）会整批过期，晚一步就只能重新采集。
  所以 `download=True` 是默认，且下载失败要落 `dl_state=fail` + 人话原因。
· 媒体引用数与下载成功数**分开记**，`media.stats()` 负责给界面用。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Callable

from . import adapters
from . import importer
from . import media
from . import pack as _pack
from . import sku as _sku
from . import store


def _now() -> str:
    return store.now()


def _code_for(conn: sqlite3.Connection, base: str, src: str) -> str:
    """包码：优先文件夹名；重名且来源不同才加后缀（同来源重复导入 = 幂等更新同一个包）。"""
    base = (base or "商品卡导入").strip() or "商品卡导入"
    row = conn.execute("SELECT pack_code, source_file FROM v3_pack WHERE pack_code=?",
                       (base,)).fetchone()
    if not row:
        return base
    if (row["source_file"] or "") == src:
        return base
    return _pack._uniq_code(conn, base, src)


def _rec_of(card: dict, pack_code: str) -> dict:
    """适配器输出 → importer.import_records 认的形状（与 import_mapped 同构）。"""
    cat = card.get("category") or {}
    ids = [str(x) for x in (cat.get("ids") or []) if str(x).strip()]
    attrs = dict(card.get("attributes") or {})
    extra = card.get("attributes_extra") or {}
    for k, v in extra.items():
        if v not in (None, ""):
            attrs.setdefault("_" + k, v)
    payload = card.get("raw_payload")
    return {
        "source_platform": card.get("platform") or "weidian",
        "source_product_id": str(card.get("source_product_id") or ""),
        "source_url": "",
        "original_title": card.get("title") or "",
        "category_id": "/".join(ids),
        "category_path": "",
        "category_confidence": cat.get("confidence"),
        "attributes": attrs,
        "source_shop_name": card.get("shop_name") or "",
        "source_store_id": card.get("appid") or "",
        "price_min": card.get("price_min"),
        "price_max": card.get("price_max"),
        "provenance": "card_import",
        "pack_code": pack_code,
        "original_payload": payload if isinstance(payload, (dict, list)) else {},
        "skus": card.get("skus") or [],
        "props": attrs,
        "images_recs": [{
            "role": m.get("role"), "sequence": m.get("sequence"),
            "original_url": m.get("url"), "sku_id": m.get("sku_id"),
            "note": m.get("shared_key") or "",
        } for m in (card.get("media") or [])],
    }


def import_folder(folder: str, pack_name: str = "", db_path: str | None = None,
                  download: bool = True, ocr: bool = True, workers: int = 4,
                  recursive: bool = False, actor: str = "商品卡导入",
                  progress: Callable[[dict], None] | None = None,
                  files_override: list | None = None) -> dict:
    """把一个文件夹里的商品卡导成一个数据包，并（默认）立刻下载 + OCR。

    ★ 2026-10-25 修：`folder` 也可以是**单个文件**。
    为什么加这条：2.0 的单文件导入按钮原来走 `pack.convert → importer.import_json`，
    那是「19 列出表模板」的解析器 —— 商品卡里的 `headImgs / videoUrls / skuSpecs /
    _raw.detail.detailImg` 一个都读不到，于是「导入成功但 0 图 0 SKU」。
    同一份 payload（实测 18775 B）10-24 走 adapters 导进来有 25 张图，
    10-25 从界面按钮导进来 0 张 —— 差别只在走了哪条解析路，不是数据丢了。
    这里让单文件也走 adapters 那条真解析路。
    """
    out: dict[str, Any] = {"ok": False, "folder": folder}
    single = bool(folder) and os.path.isfile(folder)
    # ★ 2026-10-26：主人要求「一次导入多个文件 → 自动合成一个包，不要一个文件一个包」。
    #   界面多选文件时通过 files_override 传进来，这里跳过 scan_cards 直接吃这个列表，
    #   于是整批商品共享同一个 pack_code（一个包），不再产生 N 个需要手动清理的单件包。
    if files_override:
        files = [str(x) for x in files_override if x and os.path.isfile(str(x))]
        if not files:
            out["error"] = "传进来的文件列表里没有一个有效文件"
            return out
        out.update({"scanned": len(files), "json_files": len(files),
                    "ignored": [], "multi_file": True})
    elif single:
        files = [folder]
        out.update({"scanned": 1, "json_files": 1, "ignored": [], "single_file": True})
    else:
        if not folder or not os.path.isdir(folder):
            out["error"] = "既不是文件夹也不是文件：%s" % folder
            return out
        scan = adapters.scan_cards(folder, recursive=recursive)
        files = scan.get("files") or []
        out.update({"scanned": scan.get("scanned"), "json_files": len(files),
                    "ignored": (scan.get("ignored") or [])[:8]})
        if not files:
            out["error"] = "这个文件夹（只扫一层）里没有 .json 商品卡"
            return out

    conn = store.connect(db_path)
    try:
        store.init(conn)
        base = (pack_name
                or ("%s_%d件" % (os.path.splitext(os.path.basename(files[0]))[0],
                                 len(files)) if (files_override and len(files) > 1)
                    else (os.path.splitext(os.path.basename(folder))[0] if single
                          else os.path.basename(os.path.normpath(folder)))))
        src_key = os.path.realpath(folder) if folder else files[0]
        if files_override:
            src_key = "batch:" + str(len(files)) + ":" + os.path.basename(files[0])
        code = _code_for(conn, base, src_key)
        out["pack_code"] = code

        recs: list[dict] = []
        invalid: list[dict] = []
        platforms: dict[str, int] = {}
        video_expired = 0
        warns: list[str] = []
        for p in files:
            platform, payload, err = adapters.detect_file(p)
            if not platform:
                invalid.append({"file": os.path.basename(p), "why": err})
                continue
            try:
                card = adapters.parse_card(payload, source_file=p)
            except Exception as e:  # noqa: BLE001
                invalid.append({"file": os.path.basename(p), "why": "%s: %s" % (type(e).__name__, e)})
                continue
            platforms[platform] = platforms.get(platform, 0) + 1
            if card.get("video_expired"):
                video_expired += 1
            warns.extend([w for w in (card.get("parse_warnings") or [])][:2])
            recs.append(_rec_of(card, code))

        plat_main = max(platforms, key=platforms.get) if platforms else None
        _pack._upsert_pack(conn, code, source_file=os.path.realpath(folder),
                           source_name=os.path.basename(os.path.normpath(folder)),
                           source_dir=os.path.realpath(folder), rows=len(files),
                           status="converting", message="正在解析 %d 个商品卡…" % len(files),
                           error="")

        rep = importer.import_records(conn, recs, pack_code=code, actor=actor)
        pids = [int(r[0]) for r in conn.execute(
            "SELECT product_id FROM v3_product WHERE pack_code=? ORDER BY product_id", (code,))]
        # 成员表也写一份（第二步的"成员包"、删除链路都要用）
        conn.executemany(
            "INSERT OR IGNORE INTO v3_pack_member(pack_code, product_id, source_pack, note, created_at)"
            " VALUES(?,?,?,?,?)",
            [(code, pid, code, "文件夹导入", _now()) for pid in pids])
        conn.commit()

        out.update({"platform": plat_main, "platform_name": adapters.display_name(plat_main),
                    "platforms": platforms, "products": len(pids),
                    "rejected": rep.get("rejected") or 0,
                    "rejected_samples": (rep.get("rejected_samples") or [])[:5],
                    "invalid": invalid[:8], "invalid_n": len(invalid),
                    "video_expired_products": video_expired,
                    "warnings": list(dict.fromkeys(warns))[:8]})

        # ---- 立刻下载（图 + 视频）
        # ★ 2026-10-26：**先把包标成 converted，再开始下载。**
        #   原来收尾（下面的 _upsert_pack 224 行）排在「下载 + OCR」之后：
        #   142 件要 OCR 近 6000 张、跑 30 分钟，这期间 v3_pack 一直是
        #   status='converting' / products=0 / images=0 —— 界面上看着像"数据是空的"，
        #   而 2.5 点「下载本包媒体」时又会撞上导入线程的写锁报 database is locked。
        #   实测就是这么卡住的（包永远停在 converting，商品和图其实早已落库）。
        med = {"entries": 0, "downloaded": 0, "failed": 0}
        if pids:
            _n0 = conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
                               % ",".join("?" * len(pids)), pids).fetchone()[0]
            _pack._upsert_pack(
                conn, code, rows=len(files), products=len(pids), images=_n0,
                skipped=len(invalid), status="converted",
                message="已导入 %d 件 · 媒体引用 %d 张 · 正在下载…" % (len(pids), _n0),
                error="")
        if download and pids:
            med = media.run(conn, pids, roles=None, workers=workers, ocr_after=ocr,
                            progress=progress)
            out["media"] = med
        # ---- 记账
        n_img = conn.execute("SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)"
                             % ",".join("?" * len(pids)), pids).fetchone()[0] if pids else 0
        # ★ 2026-10-26：SKU 入库。主人报「SKU 没下载，只下了主图/轮播/详情」——
        #   cards.py 原来**一个字都没提 v3_sku**（实测 mentions=0），
        #   而 sku.py 早就有完整能力（parse/rebuild/apply/read/stats）。
        #   apply(conn, pids) 就是把 payload 里的 skuSpecs+skuAttrs 落成 v3_sku 行
        #   （规格名、价格、库存、sku_id、缩略图关联），改这一行 SKU 才真的进库。
        n_sku = 0
        sku_err = ""
        if pids:
            try:
                _sku.ensure(conn)
                sku_r = _sku.apply(conn, pids)
                n_sku = int((sku_r or {}).get("skus") or 0)
                if (sku_r or {}).get("failed"):
                    sku_err = "%d 件 SKU 写入失败" % (sku_r.get("failed") or 0)
            except Exception as e:  # noqa: BLE001
                sku_err = "%s: %s" % (type(e).__name__, e)
        ded = media.dedupe_report(conn, pids) if pids else {}
        st = media.stats(conn, pids) if pids else {}
        msg = ("%s · 商品 %d/%d · 媒体引用 %d（唯一 URL %d）· "
               "**下载文件 %d** · 就绪引用 %d · 失败引用 %d · OCR %d · SKU %d"
               % (adapters.display_name(plat_main), len(pids), len(files), n_img,
                  ded.get("unique_urls") or 0, med.get("files") or 0,
                  med.get("downloaded") or 0, med.get("failed") or 0,
                  med.get("ocr_ok") or 0, n_sku))
        if sku_err:
            msg += " · ⚠ SKU 写入异常：%s" % sku_err
        if video_expired:
            msg += " · ⚠ %d 件的视频链接已过期" % video_expired
        _pack._upsert_pack(conn, code, rows=len(files), products=len(pids),
                           images=n_img, skipped=len(invalid),
                           status="converted" if pids else "empty",
                           message=msg, error="", converted_at=_now())
        out.update({"ok": bool(pids), "message": msg, "images": n_img,
                    "skus": n_sku, "sku_error": sku_err,
                    "dedupe": ded, "stats": st,
                    "skipped_invalid": len(invalid)})
        out["warnings"] = out.get("warnings") or []
        if video_expired:
            out["warnings"].append(
                "%d 件商品的视频链接已过期（wdis_t 到点）→ 请重新采集后调 "
                "media.update_video_urls(pid, [新地址]) 补下，不要重复重试旧链"
                % video_expired)
        if med.get("errors"):
            out["download_errors"] = med["errors"]
        store.audit(conn, "import_folder", "batch", code, None,
                    {"files": len(files), "products": len(pids),
                     "downloaded": med.get("downloaded"), "failed": med.get("failed")},
                    actor=actor)
        conn.commit()
        return out
    finally:
        conn.close()


def fetch_video_urls(card_path: str) -> dict:
    """给"我这边重新采集了新的视频链接"用：从一张新卡里把视频地址读出来，
    直接喂给 `media.update_video_urls`，不用手工复制几十个 URL。"""
    platform, payload, err = adapters.detect_file(card_path)
    if not platform:
        return {"ok": False, "error": err}
    card = adapters.parse_card(payload, source_file=card_path)
    urls = [m["url"] for m in card["media"] if m.get("role") == "video"]
    return {"ok": True, "product_id": card.get("source_product_id"),
            "platform": platform, "urls": urls,
            "expired": card.get("video_expired"), "note": card.get("video_note")}
