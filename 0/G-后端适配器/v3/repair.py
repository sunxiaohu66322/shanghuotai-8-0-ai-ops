# -*- coding: utf-8 -*-
"""v3 · 包元信息自愈

★ 2026-10-26 为什么有这个模块：
  导一个大批次（实测 142 件 / 5878 张图）时，`cards.import_folder` 的收尾
  `_upsert_pack(status='converted')` 排在「下载 + OCR」之后。OCR 要跑近 30 分钟，
  这期间：
    ① `v3_pack` 一直是 status='converting' / products=0 / images=0
       → 界面看着像"数据是空的"，其实商品和图早就落库了；
    ② 中途任何异常（或被写锁打断）都让收尾彻底丢失，包永远停在 converting。

  本模块只做一件事：**用库里的事实重算包元信息并回写**。
  不动商品、不动图片、不动文件，纯元数据。

用法：
    from server.v3 import repair
    repair.repair_pack_stats(dry_run=True)     # 只看将要改什么
    repair.repair_pack_stats(dry_run=False)    # 真写
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import pack as _pack
from . import store

# ================================================================ 验收工具（补回）
# ★ 2026-10-26：本文件曾被「包元信息自愈」整文件覆盖，原有的 imread_any / inspect
#   两个验收工具函数随之丢失（且覆盖时未留原版备份 —— 这条记在报告里）。
#   这里按 _验_按坐标消除.py 的调用口径补回，口径与 logo 调研一致：
#   框内/框外改动像素数、最大差、均值差。业务代码不依赖它们，只服务验收脚本。

try:
    import cv2
    import numpy as _np
except Exception:                       # 没装 cv2 也不影响包自愈功能
    cv2 = None
    _np = None


def imread_any(path: str):
    """读图，**支持中文路径**。

    cv2.imread 在 Windows 上遇到中文路径会返回 None（它不走 Python 的路径层），
    所以先读成字节再 imdecode。
    """
    if cv2 is None or _np is None or not path:
        return None
    try:
        buf = _np.fromfile(path, dtype=_np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def inspect(orig, rep, mk=None) -> dict:
    """对比原图与修补结果，返回客观指标。

    mk=None → 全图对比；给了 mask（255=目标区域）→ 分别统计框内/框外。
    判据含义：**框外改动必须为 0**（修补不许碰非目标像素）；
    **框内"纹理恢复率"** 才是"是色块还是真消除"的客观指标（≈0 就是色块）。
    """
    if orig is None or rep is None or cv2 is None or _np is None:
        return {"ok": False, "error": "读图失败或缺少 cv2/numpy"}
    if orig.shape != rep.shape:
        return {"ok": False, "error": "尺寸不一致 %s vs %s" % (orig.shape, rep.shape)}

    a = orig.astype(_np.int16)
    b = rep.astype(_np.int16)
    d = _np.abs(a - b).max(axis=2)          # 每像素三通道最大差
    changed = d > 0
    out: dict[str, Any] = {
        "ok": True,
        "shape": list(orig.shape),
        "changed_total": int(changed.sum()),
        "max_diff": int(d.max()),
        "mean_diff": round(float(d.mean()), 4),
    }

    if mk is None:
        out["scope"] = "full"
        return out

    m = mk
    if m.ndim == 3:
        m = m[:, :, 0]
    m = (m > 0)
    out["scope"] = "masked"
    out["mask_px"] = int(m.sum())
    out["mask_ratio"] = round(float(m.sum()) / m.size, 4)
    inside = changed & m
    outside = changed & (~m)
    out["changed_inside"] = int(inside.sum())
    out["changed_outside"] = int(outside.sum())
    out["max_diff_inside"] = int(d[m].max()) if m.any() else 0
    out["max_diff_outside"] = int(d[~m].max()) if (~m).any() else 0
    out["mean_diff_inside"] = round(float(d[m].mean()), 4) if m.any() else 0.0
    out["mean_diff_outside"] = round(float(d[~m].mean()), 4) if (~m).any() else 0.0

    # 纹理恢复率：区域内 Laplacian 方差 ÷ 同尺寸外圈自然纹理方差
    # 1.0 = 纹理与周围一致（像真消除）；≈0 = 平滑（就是色块）
    try:
        g = cv2.cvtColor(rep, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        v_in = float(lap[m].var()) if m.any() else 0.0
        v_out = float(lap[~m].var()) if (~m).any() else 0.0
        out["tex_inside"] = round(v_in, 2)
        out["tex_outside"] = round(v_out, 2)
        out["tex_ratio"] = round(v_in / v_out, 3) if v_out > 1e-6 else None
    except Exception:
        pass
    return out

# 这些状态才需要自愈：已经 converted / empty 的不动
_STALE_STATUS = ("converting", "pending")


def _facts(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """一个包在库里的事实（不信包记录自己的计数）。"""
    ph = "?"
    n_prod = conn.execute(
        "SELECT COUNT(*) FROM v3_product WHERE pack_code=%s" % ph, (code,)).fetchone()[0]
    if code and n_prod:
        row = conn.execute(
            "SELECT COUNT(*),"
            " COUNT(DISTINCT CASE WHEN dl_state='ok' THEN image_id END),"
            " COUNT(DISTINCT CASE WHEN dl_state='fail' THEN image_id END),"
            " COUNT(DISTINCT CASE WHEN current_local_path IS NOT NULL"
            "                     AND current_local_path<>'' THEN image_id END)"
            " FROM v3_image WHERE product_id IN"
            " (SELECT product_id FROM v3_product WHERE pack_code=%s)" % ph, (code,)).fetchone()
    else:
        row = (0, 0, 0, 0)
    n_img, n_ok, n_fail, n_path = (int(x or 0) for x in row)
    n_ocr = 0
    if n_img:
        try:
            n_ocr = conn.execute(
                "SELECT COUNT(*) FROM v3_image_ocr o JOIN v3_image i"
                " ON i.image_id=o.image_id JOIN v3_product p ON p.product_id=i.product_id"
                " WHERE p.pack_code=%s AND o.state='ok'" % ph, (code,)).fetchone()[0]
        except sqlite3.OperationalError:
            n_ocr = 0
    n_sku = 0
    try:
        n_sku = conn.execute(
            "SELECT COUNT(*) FROM v3_sku s JOIN v3_product p"
            " ON p.product_id=s.product_id WHERE p.pack_code=%s" % ph, (code,)).fetchone()[0]
    except sqlite3.OperationalError:
        n_sku = 0
    return {"products": n_prod, "images": n_img, "img_ok": n_ok,
            "img_fail": n_fail, "img_path": n_path, "ocr_ok": int(n_ocr or 0),
            "skus": int(n_sku or 0)}


def scan(conn: sqlite3.Connection) -> dict:
    """扫出所有「包记录与库内事实不符」的包（只读）。

    ★ 2026-10-26 放宽判据：
      旧判据只认「stale status」或「记录=0 而实际>0」，于是
      「记录 9 件 / 实际 8 件」这种偏差永远修不到（实测 批量导入_9件）。
      现在：只要 products/images 与事实不等就纳入。
      注意 rows 是「导入时声明的行数」，它是历史事实，**不参与比对、也不改写**。
    """
    rows = conn.execute(
        "SELECT pack_code, status, products, images, rows, message, created_at,"
        " updated_at, converted_at"
        " FROM v3_pack ORDER BY created_at DESC").fetchall()
    out: list[dict] = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            code, st = r["pack_code"], r["status"]
            p_rec = int(r["products"] or 0)
            i_rec = int(r["images"] or 0)
            r_rows = int(r["rows"] or 0)
            cv_at = r["converted_at"]
        else:
            code, st = r[0], r[1]
            p_rec, i_rec = int(r[2] or 0), int(r[3] or 0)
            r_rows = int(r[4] or 0)
            cv_at = r[8]
        f = _facts(conn, code)
        need = (
            (st in _STALE_STATUS)
            or (f["products"] > 0 and p_rec == 0)
            or (f["images"] > 0 and i_rec == 0)
            or (p_rec != f["products"])          # ★ 记录多于/少于事实
            or (i_rec != f["images"])
            or (f["products"] > 0 and not cv_at)  # ★ 有货却没有完成时间
        )
        out.append({
            "pack_code": code, "status": st,
            "rec_products": p_rec, "rec_images": i_rec, "rec_rows": r_rows,
            "converted_at": cv_at,
            "real_products": f["products"], "real_images": f["images"],
            "img_ok": f["img_ok"], "img_fail": f["img_fail"],
            "img_path": f["img_path"], "ocr_ok": f["ocr_ok"], "skus": f["skus"],
            "need_fix": bool(need),
        })
    return {"total": len(out), "need_fix": sum(1 for x in out if x["need_fix"]),
            "packs": out}


def repair_pack_stats(conn: sqlite3.Connection | None = None, dry_run: bool = True,
                      codes: list[str] | None = None) -> dict:
    """用库内事实重算包元信息并回写。只动 v3_pack 一行，不碰商品/图片/文件。"""
    own = conn is None
    c = conn or store.connect()
    fixed: list[dict] = []
    try:
        info = scan(c)
        for x in info["packs"]:
            if codes and x["pack_code"] not in codes:
                continue
            if not x["need_fix"]:
                continue
            new_status = "converted" if x["real_products"] else "empty"
            msg = ("商品 %d · 媒体引用 %d 张（已就绪 %d · 失败 %d · 有本地文件 %d）"
                   " · OCR %d · SKU %d"
                   % (x["real_products"], x["real_images"], x["img_ok"],
                      x["img_fail"], x["img_path"], x["ocr_ok"], x["skus"]))
            # 导入声明的行数与现存不一致时，把差异写进 message（不改 rows，它是历史事实）
            decl = int(x.get("rec_rows") or 0)
            if decl and decl != x["real_products"]:
                msg += " · ⚠ 导入声明 %d 件，现存 %d 件（少 %d）" % (
                    decl, x["real_products"], decl - x["real_products"])
            extra: dict[str, Any] = {}
            # converted_at 一直是空的话补上（出现的时刻就是这次自愈的时刻）
            if x["real_products"] and not x.get("converted_at"):
                extra["converted_at"] = _pack._now()
                msg += " · 完成时间已补记"
            item = dict(x)
            item.update({"new_status": new_status, "new_message": msg})
            if not dry_run:
                _pack._upsert_pack(
                    c, x["pack_code"],
                    products=x["real_products"], images=x["real_images"],
                    status=new_status, message=msg, error="", **extra)
                store.audit(c, "repair_pack_stats", "pack", x["pack_code"],
                            {"status": x["status"], "products": x["rec_products"],
                             "images": x["rec_images"],
                             "converted_at": x.get("converted_at")},
                            {"status": new_status, "products": x["real_products"],
                             "images": x["real_images"],
                             "converted_at": extra.get("converted_at")},
                            actor="repair")
                c.commit()
            fixed.append(item)
        return {"ok": True, "dry_run": bool(dry_run), "scanned": info["total"],
                "need_fix": info["need_fix"], "fixed": len(fixed), "items": fixed}
    finally:
        if own:
            c.close()
