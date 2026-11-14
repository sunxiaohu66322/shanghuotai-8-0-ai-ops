# -*- coding: utf-8 -*-
"""v3 · 本地最终媒体包（阶段二第三项）

产出**一个可以直接拿去用的文件夹**，而不是只给一张表格：

    上货包_YYYYmmdd-HHMMSS/
    ├─ 微信小店_19列.csv        真实 19 列（GBK，与旧出口径一致）
    ├─ 抖店_16列.xlsx           真实 16 列（openpyxl 基于真模板）
    ├─ media/<商品ID>_<序号>/
    │    ├─ main/   carousel/   detail/   sku/   video/
    ├─ 商品媒体对应清单.csv      每条逻辑引用一行（去重只影响物理文件，不影响清单）
    ├─ 待上传媒体.csv
    ├─ 已上传媒体.csv
    └─ 导出检查报告.txt

三条硬规矩（任务书 §3.3 / §3.5）：
  ① 只读**当前确认版本**（`images.resolve_local` 优先 current_local_path），
     绝不因为原始文件还在就用原始文件。
  ② 文件缺失**不许悄悄用原图顶**，逐条写进检查报告，并把该商品标成「不可上架」。
  ③ 先建临时目录，全部落盘校验通过后才原子改名成正式目录 —— 不留半成品。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
from datetime import datetime
from typing import Any

from . import images, store
from . import doudian as dd

ROLE_ORDER = {"main": 0, "carousel": 1, "sku": 2, "detail": 3, "video": 4}
ROLE_DIR = {"main": "main", "carousel": "carousel", "sku": "sku",
            "detail": "detail", "video": "video"}
ROLE_CN = {"main": "主图/轮播", "carousel": "轮播", "sku": "SKU图",
           "detail": "详情图", "video": "视频"}

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
VID_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


# ---------------------------------------------------------------- 文件识别

def sniff_kind(path: str) -> str:
    """按**文件头**判真伪，不信扩展名。返回 image / video / unknown。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return "unknown"
    if head[:2] == b"\xff\xd8":
        return "image"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if head[:4] in (b"RIFF",) and head[8:12] == b"WEBP":
        return "image"
    if head[:3] == b"GIF":
        return "image"
    if head[:2] == b"BM":
        return "image"
    if head[4:8] == b"ftyp":          # mp4 / mov / m4v
        return "video"
    if head[:4] == b"\x1a\x45\xdf\xa3":   # matroska / webm
        return "video"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    return "unknown"


def video_meta(path: str) -> dict:
    """尽量读视频元数据（ftyp 品牌 / 时长）。读不到就写「未知」，不编。"""
    meta = {"container": "未知", "duration_sec": "未知", "brand": ""}
    try:
        size = os.path.getsize(path)
    except OSError:
        return meta
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head[4:8] == b"ftyp":
                meta["brand"] = head[8:12].decode("latin-1", "replace").strip()
                meta["container"] = "mp4/mov"
                return meta
            if head[:4] == b"\x1a\x45\xdf\xa3":
                meta["container"] = "webm/mkv"
                return meta
            if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
                meta["container"] = "avi"
                return meta
    except OSError:
        pass
    # 无 ffprobe 时用 mvhd 粗读时长（mp4 家族）
    try:
        with open(path, "rb") as f:
            data = f.read(min(size, 1 << 20))
        i = data.find(b"mvhd")
        if i > 0 and meta["container"] == "未知":
            meta["container"] = "mp4/mov"
        if i > 0 and i + 20 < len(data):
            ver = data[i + 4]
            if ver == 0:
                timescale = struct.unpack(">I", data[i + 16:i + 20])[0]
                duration = struct.unpack(">I", data[i + 20:i + 24])[0]
            else:
                timescale = struct.unpack(">I", data[i + 24:i + 28])[0]
                duration = struct.unpack(">Q", data[i + 28:i + 36])[0]
            if timescale:
                meta["duration_sec"] = round(duration / timescale, 2)
    except Exception:      # noqa: BLE001
        pass
    return meta


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ---------------------------------------------------------------- 主流程

def build(conn: sqlite3.Connection, pids: list[int] | None = None,
          store_id: str = "default", out_root: str | None = None,
          make_xlsx: bool = True, actor: str = "bundle",
          include_blocked: bool = False) -> dict:
    """生成完整本地媒体包。返回 {ok, dir, stats, blockers,...}。

    include_blocked=False（默认）：`status='blocked'` 的商品**不进包** ——
      那是细筛判下来的不合格品，混进交付包会出事。
    include_blocked=True：仍然进包，但在统计与检查报告里**单独点名**
      「仅供验收，不得直接上架」。这一刀只在用户明确要求时才切。
    """
    # ---- 选商品：默认排除 blocked 与 fixture（与 export_package 同口径）
    if pids:
        prods = [p for p in (store.get_product(conn, int(x)) for x in pids) if p]
        skipped = {"blocked": 0, "fixture": 0}
        n_b = len([p for p in prods if p.get("status") == "blocked"])
        prods = [p for p in prods if (p.get("provenance") or "") != "fixture"]
        if not include_blocked:
            skipped["blocked"] = n_b
            prods = [p for p in prods if p.get("status") != "blocked"]
    else:
        allp = store.list_products(conn, limit=100000)
        prods = [p for p in allp if (p.get("provenance") or "") != "fixture"]
        nb = len([p for p in prods if p.get("status") == "blocked"])
        if not include_blocked:
            prods = [p for p in prods if p.get("status") != "blocked"]
            skipped = {"blocked": nb, "fixture": len(allp) - len(prods) - nb}
        else:
            skipped = {"blocked": 0, "fixture": len(allp) - len(prods)}
    blocked_in = [int(p["product_id"]) for p in prods if p.get("status") == "blocked"]
    if not prods:
        tail = ""
        if skipped.get("blocked"):
            tail = ("（另有 %d 件被细筛标为 blocked，已排除；要连它们一起出包需显式要求）"
                    % skipped["blocked"])
        return {"ok": False, "error": "没有可出包的商品（先导入；被拦下的不算）" + tail,
                "skipped": skipped}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = out_root or str(store.OUT_DIR)
    os.makedirs(out_root, exist_ok=True)
    final_dir = os.path.join(out_root, "上货包_%s" % ts)
    tmp_dir = os.path.join(out_root, ".tmp_bundle_%s" % ts)
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir)

    media_root = os.path.join(tmp_dir, "media")
    os.makedirs(media_root)

    rows_media: list[dict] = []
    rows_pending: list[dict] = []
    rows_uploaded: list[dict] = []
    problems: list[str] = []
    blocks: dict[int, list[str]] = {}
    seen_sha: dict[str, str] = {}      # sha → 已复制的相对路径（物理去重）
    n_copy = n_dedup = n_missing = 0

    for idx, p in enumerate(prods, 1):
        pid = int(p["product_id"])
        imgs = store.list_images(conn, pid)
        dir_name = "%d_%02d" % (pid, idx)
        pdir = os.path.join(media_root, dir_name)
        os.makedirs(pdir, exist_ok=True)
        blocks.setdefault(pid, [])
        title = (p.get("original_title") or "")[:30]

        ordered = sorted(imgs, key=lambda x: (
            ROLE_ORDER.get(x["role"], 9), x.get("sequence") or 0, x["image_id"]))
        seq_per_role: dict[str, int] = {}
        for i in ordered:
            role = i.get("role") or "other"
            seq_per_role[role] = seq_per_role.get(role, 0) + 1
            n = seq_per_role[role]
            src, exists = images.resolve_local(i)
            # 当前确认版本是不是替换过的
            replaced = (i.get("status") == "replaced") or bool(
                (i.get("current_local_path") or "").strip()
                and (i.get("original_local_path") or "").strip()
                and os.path.normcase(i["current_local_path"]) != os.path.normcase(i["original_local_path"]))
            remote = (i.get("current_remote_url") or "").strip()
            if not remote.startswith("http"):
                remote = ""
            # 上传状态：与 upload.needs_upload 同判据
            if remote:
                up_state = "已上传"
            elif replaced or (i.get("original_url") or "")[:4] != "http":
                up_state = "待上传"
            else:
                up_state = "沿用原图地址"

            rel = ""
            size = ""
            sha = ""
            kind = ""
            extra = ""
            note = ""
            if not exists or not src or not os.path.isfile(src):
                n_missing += 1
                note = "当前版本文件不存在（不得用原图顶替）"
                blocks[pid].append("%s#%d %s" % (ROLE_CN.get(role, role), n, note))
                problems.append("商品 %d（%s）媒体 %d %s 序号%d：%s｜路径=%s"
                                % (pid, title, i["image_id"], role, n, note, src or "(空)"))
            else:
                ext = os.path.splitext(src)[1].lower() or ".bin"
                kind = sniff_kind(src)
                if kind == "unknown":
                    note = "文件头无法识别为图片或视频（扩展名 %s）" % ext
                    blocks[pid].append("%s#%d %s" % (ROLE_CN.get(role, role), n, note))
                    problems.append("商品 %d 媒体 %d：%s" % (pid, i["image_id"], note))
                size = os.path.getsize(src)
                try:
                    sha = sha256_of(src)
                except OSError:
                    sha = ""
                sub = ROLE_DIR.get(role, "other")
                sdir = os.path.join(pdir, sub)
                os.makedirs(sdir, exist_ok=True)
                fname = "%s_%03d%s" % (sub, n, ext)
                dst_rel = os.path.join("media", dir_name, sub, fname)
                dst = os.path.join(tmp_dir, dst_rel)
                # 物理去重：同一 sha 且角色/序号不同 → 仍复制（避免用户困惑），但记 dedup
                if sha and sha in seen_sha:
                    n_dedup += 1
                    note = (note + "；" if note else "") + "同内容已在 %s" % seen_sha[sha]
                shutil.copy2(src, dst)
                n_copy += 1
                if sha and sha not in seen_sha:
                    seen_sha[sha] = dst_rel
                rel = dst_rel.replace("\\", "/")
                if role == "video":
                    m = video_meta(dst)
                    extra = "container=%s;duration=%s;brand=%s" % (
                        m["container"], m["duration_sec"], m["brand"])

            row = {
                "商品ID": p.get("source_product_id") or pid,
                "SKU_ID": i.get("sku_id") or "",
                "媒体ID": i["image_id"],
                "媒体角色": role,
                "角色中文": ROLE_CN.get(role, role),
                "顺序": i.get("sequence") if i.get("sequence") is not None else "",
                "角色内序号": n,
                "原始文件路径": (i.get("original_local_path") or ""),
                "当前确认文件路径": src or "",
                "打包后相对路径": rel,
                "文件大小": size,
                "SHA256": sha,
                "当前远程URL": remote,
                "上传状态": up_state,
                "是否替换版": "是" if replaced else "否",
                "实际类型": kind,
                "附加信息": extra,
                "备注": note,
            }
            rows_media.append(row)
            if up_state == "待上传":
                rows_pending.append({
                    "商品ID": row["商品ID"], "媒体ID": i["image_id"],
                    "角色": role, "角色内序号": n,
                    "当前确认文件路径": src or "",
                    "打包后相对路径": rel,
                    "原图URL(不可用)": (i.get("original_url") or ""),
                    "原因": "替换后的新文件没有线上地址" if replaced else "无远程地址",
                })
            if remote:
                rows_uploaded.append({
                    "商品ID": row["商品ID"], "媒体ID": i["image_id"], "角色": role,
                    "角色内序号": n, "当前远程URL": remote,
                    "是否替换版": "是" if replaced else "否",
                })

    # ---- 微信 19 列（复用既有 assembler，不改口径）
    wx_name = ""
    try:
        from . import packaging as _pk     # noqa: PLC0415
        out_rows = []
        for p in prods:
            imgs = store.list_images(conn, int(p["product_id"]))
            row, _w = _pk.build_row(conn, p, store_id, imgs)
            out_rows.append(row)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(_pk.COLUMNS)
        w.writerows(out_rows)
        wx_name = "微信小店_19列.csv"
        with open(os.path.join(tmp_dir, wx_name), "wb") as f:
            f.write(buf.getvalue().encode("gbk", errors="replace"))
    except Exception as e:      # noqa: BLE001
        problems.append("微信 19 列生成失败：%s: %s" % (type(e).__name__, e))

    # ---- 抖店 16 列
    dd_name = ""
    if make_xlsx:
        try:
            # 模板路径与 bridge.v3_export_doudian 保持一致
            tpl = os.path.join(str(store.BASE_DIR), "data", "templates", "抖店模板二.xlsx")
            if not os.path.isfile(tpl):
                problems.append("找不到抖店模板：%s" % tpl)
            else:
                r = dd.collect(conn, [int(p["product_id"]) for p in prods], store_id=store_id)
                if r.get("ok"):
                    dd_name = "抖店_16列.xlsx"
                    ex = dd.export_xlsx(tpl, os.path.join(tmp_dir, dd_name), r["blocks"])
                    if not ex.get("ok"):
                        dd_name = ""
                        problems.append("抖店 xlsx 写入失败：%s" % ex.get("error"))
                else:
                    problems.append("抖店 16 列生成失败：%s" % (r.get("error") or "未知"))
        except Exception as e:      # noqa: BLE001
            problems.append("抖店 16 列异常：%s: %s" % (type(e).__name__, e))

    # ---- 清单 CSV（utf-8-sig，Excel 双击可读）
    def _csv(name: str, rows: list[dict]) -> None:
        if not rows:
            with open(os.path.join(tmp_dir, name), "w", encoding="utf-8-sig", newline="") as f:
                f.write("")
            return
        with open(os.path.join(tmp_dir, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    _csv("商品媒体对应清单.csv", rows_media)
    _csv("待上传媒体.csv", rows_pending)
    _csv("已上传媒体.csv", rows_uploaded)

    # ---- 检查报告
    lines = [
        "上货包检查报告",
        "生成时间：%s" % store.now(),
        "商品数：%d" % len(prods),
        "媒体逻辑引用：%d 条" % len(rows_media),
        "实际复制文件：%d 个（其中同内容重复 %d 个）" % (n_copy, n_dedup),
        "待上传：%d 条 ｜ 已上传：%d 条" % (len(rows_pending), len(rows_uploaded)),
        "",
        "一、能否直接上架",
    ]
    can_ok = [p for p in prods if not blocks.get(int(p["product_id"]))]
    for p in prods:
        pid = int(p["product_id"])
        bad = blocks.get(pid) or []
        lines.append("  %s：%s" % (
            p.get("source_product_id") or pid,
            "可生成（媒体齐全）" if not bad else "**不可上架** —— " + "；".join(bad)))
    lines.append("")
    lines.append("  可上架 %d 件 / 共 %d 件" % (len(can_ok), len(prods)))
    if blocked_in:
        lines.append("")
        lines.append("一·补、包内已拦下的商品（仅供验收，不得直接上架）")
        lines.append("  %d 件被细筛标为 blocked：%s"
                     % (len(blocked_in), "、".join(str(x) for x in blocked_in)))
    lines.append("")
    lines.append("二、媒体缺口与异常")
    if problems:
        lines.extend("  - " + x for x in problems)
    else:
        lines.append("  无")
    lines.append("")
    lines.append("三、表格")
    lines.append("  微信 19 列：%s" % (wx_name or "未生成"))
    lines.append("  抖店 16 列：%s" % (dd_name or "未生成"))
    lines.append("")
    lines.append("四、说明")
    lines.append("  · 媒体均取自「当前确认版本」（current_local_path），未替换的取原始文件。")
    lines.append("  · 本地相对路径不可当作公网地址；远程 URL 一栏为空即表示需先上传图床。")
    lines.append("  · 替换后未上传的媒体，表格里对应的图列会留空并在本报告标注，不会回落到原图。")
    with open(os.path.join(tmp_dir, "导出检查报告.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ---- 原子提交
    try:
        if os.path.exists(final_dir):
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(tmp_dir, final_dir)
        committed = True
        err = ""
    except OSError as e:
        committed = False
        err = "%s: %s" % (type(e).__name__, e)

    stats = {
        "products": len(prods), "media_refs": len(rows_media),
        "copied": n_copy, "same_content": n_dedup, "missing": n_missing,
        "pending": len(rows_pending), "uploaded": len(rows_uploaded),
        "can_list": len(can_ok), "blocked": len(prods) - len(can_ok),
        "blocked_included": len(blocked_in),
        "wx": wx_name, "dd": dd_name, "skipped": skipped,
    }
    try:
        store.audit(conn, "bundle.build", "v3_product", ",".join(str(p["product_id"]) for p in prods)[:200],
                    None, stats, actor=actor)
        conn.commit()
    except Exception:       # noqa: BLE001
        pass
    return {"ok": committed, "dir": final_dir if committed else tmp_dir,
            "committed": committed, "error": err, "stats": stats,
            "problems": problems[:200], "blockers": {str(k): v for k, v in blocks.items() if v}}
