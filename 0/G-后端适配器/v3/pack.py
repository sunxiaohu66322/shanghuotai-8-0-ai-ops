# -*- coding: utf-8 -*-
"""v3 · 数据包中心（第二步 → 第三步之间的那道闸）

一个「数据包」= 一份导入文件 + 它在库里的全部商品。

流程：
    选文件 → convert()  读列、归一化、入库、记 v3_pack
           → status()   看「转换状态」：converted 才允许进检测打码
           → delete()   删掉这个包（连同它的商品与图片，可回滚=重新导入）

支持的文件格式（按扩展名 + 表头嗅探，不靠人工选）：
    · 关键词采集 CSV   商品ID/标题/到手价/销量/主图/appid/关键词…  ← 主打
    · 微信小店 19 列 CSV
    · 小猪 packs 的 58 列 CSV
    · 拼多多 rawData JSON(.txt) / 通用 items JSON

铁律：
  · 只读源文件，绝不改源文件
  · pack_code 默认 = 文件名去扩展名；撞名自动加 -2/-3，不覆盖别人的包
  · 状态只有真实数字算出来，不预设、不乐观
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import time
from typing import Any

from . import importer, safedelete, store

# 收件箱：把 csv 丢这里，UI 一眼能看到
PKG_DIR = os.path.join(store.BASE_DIR, "packs")


def ensure_dir() -> str:
    os.makedirs(PKG_DIR, exist_ok=True)
    return PKG_DIR


# ---------------------------------------------------------------- 工具

def _now() -> str:
    return store.now()


def _kb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / 1024.0, 1)
    except OSError:
        return 0.0


def sniff(path: str) -> str:
    """返回 'csv' | 'json' | ''（不认识的格式）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".json", ".txt"):
        try:
            head = importer._read_text(path)[:4000].lstrip()
        except Exception:  # noqa: BLE001
            return ""
        return "json" if head[:1] in "[{" else "csv"
    return ""


def count_rows(path: str) -> int:
    """文件里有多少条数据行（不含表头）。读不出来返回 0。"""
    kind = sniff(path)
    if kind == "csv":
        try:
            text = importer._read_text(path)
            n = sum(1 for r in csv.DictReader(io.StringIO(text)) if any((v or "").strip() for v in r.values()))
            return n
        except Exception:  # noqa: BLE001
            return 0
    if kind == "json":
        try:
            import json
            data = json.loads(importer._read_text(path))
            if isinstance(data, dict):
                for k in ("items", "products", "list", "data", "goods"):
                    if isinstance(data.get(k), list):
                        return len(data[k])
                return 1
            return len(data) if isinstance(data, list) else 0
        except Exception:  # noqa: BLE001
            return 0
    return 0


def _uniq_code(conn: sqlite3.Connection, base: str, src: str) -> str:
    """撞名就加 -2/-3；同一个源文件重复导入则沿用原包码（=更新，不新建）。"""
    base = (base or "包").strip() or "包"
    row = conn.execute(
        "SELECT pack_code, source_file FROM v3_pack WHERE pack_code=?", (base,)).fetchone()
    if not row:
        return base
    if (row["source_file"] or "") == src:
        return base
    i = 2
    while True:
        code = "%s-%d" % (base, i)
        r = conn.execute("SELECT source_file FROM v3_pack WHERE pack_code=?", (code,)).fetchone()
        if not r:
            return code
        if (r["source_file"] or "") == src:
            return code
        i += 1


def _upsert_pack(conn: sqlite3.Connection, code: str, **kw) -> None:
    """写包元信息（幂等）。撞写锁时退避重试 —— 这一行丢了包就永远停在 converting。"""
    keys = ("source_file", "source_name", "source_dir", "size_kb", "rows",
            "products", "images", "skipped", "status", "message", "error",
            "converted_at")
    # NOT NULL 的计数列不能显式塞 None，补默认值
    _def = {"rows": 0, "products": 0, "images": 0, "skipped": 0, "status": "pending"}
    vals = {k: (kw[k] if kw.get(k) is not None else _def.get(k)) for k in keys if k in kw}

    def _write() -> None:
        row = conn.execute("SELECT pack_code FROM v3_pack WHERE pack_code=?", (code,)).fetchone()
        if row:
            sets, args = [], []
            for k in keys:
                if k in vals:
                    sets.append("%s=?" % k)
                    args.append(vals[k])
            sets.append("updated_at=?")
            args.extend([_now(), code])
            conn.execute("UPDATE v3_pack SET %s WHERE pack_code=?" % ", ".join(sets), args)
        else:
            conn.execute(
                "INSERT INTO v3_pack(pack_code,%s,created_at,updated_at)"
                " VALUES(?%s,?,?)" % (",".join(vals.keys()), ",?" * len(vals)),
                [code] + list(vals.values()) + [_now(), _now()])
        store.retry_locked(conn.commit)

    _retry_locked(_write)


# ---------------------------------------------------------------- 转换

def convert(path: str, pack_code: str = "", actor: str = "上货台导入") -> dict:
    """把一份文件转成数据包（读列 → 归一化 → 入库 → 记账）。"""
    out: dict[str, Any] = {"ok": False, "pack_code": pack_code, "path": path}
    if not path or not os.path.exists(path):
        out["error"] = "文件不存在：%s" % path
        return out
    kind = sniff(path)
    if not kind:
        out["error"] = "不认识的格式（只吃 csv / json / txt-json）：%s" % path
        return out

    conn = store.connect()
    try:
        store.init(conn)
        name = os.path.basename(path)
        code = _uniq_code(conn, pack_code or os.path.splitext(name)[0], os.path.realpath(path))
        out["pack_code"] = code
        row_n = count_rows(path)
        out["rows"] = row_n
        _upsert_pack(conn, code, source_file=os.path.realpath(path), source_name=name,
                     source_dir=os.path.dirname(os.path.realpath(path)),
                     size_kb=_kb(path), rows=row_n, status="converting",
                     message="正在转换…", error="")
        try:
            rep = (importer.import_csv(path, pack_code=code, actor=actor) if kind == "csv"
                   else importer.import_json(path, pack_code=code, actor=actor))
        except Exception as e:  # noqa: BLE001
            _upsert_pack(conn, code, status="error", error="%s: %s" % (type(e).__name__, e),
                         message="转换失败")
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out

        if rep.get("imported") is None:
            _upsert_pack(conn, code, status="error",
                         error=str(rep.get("error") or "导入返回空"), message="转换失败")
            out["error"] = rep.get("error") or "导入返回空"
            return out

        prod_n, img_n = _live_counts(conn, code)
        status = "converted" if prod_n else "empty"
        msg = ("%d 个商品 · %d 张图" % (prod_n, img_n)) if prod_n else "文件里没有可用商品（标题全空？）"
        n_rej = int(rep.get("rejected") or 0)
        if n_rej:
            # ★ 准入拦截必须让用户看见：这是"初筛记录被当商品卡导入"的唯一提示
            msg += " · 拒收 %d 行（不是正式商品卡：只有标题/店铺/链接）" % n_rej
            if not prod_n:
                status = "rejected"
                msg = "全部 %d 行都被拒收：这是第一步的初筛数据，不是商品详情卡" % n_rej
        _upsert_pack(conn, code, products=prod_n, images=img_n,
                     skipped=int(rep.get("skipped") or 0),
                     status=status, message=msg, error="",
                     converted_at=_now())
        out.update({
            "ok": True, "status": status, "message": msg,
            "imported": rep.get("imported"), "updated": rep.get("updated"),
            "images": img_n, "products": prod_n,
            "skipped": rep.get("skipped"), "rows": out.get("rows"),
            "rejected": n_rej, "rejected_samples": rep.get("rejected_samples") or [],
            "warnings": (rep.get("warnings") or [])[:12],
        })
        return out
    finally:
        conn.close()


def _live_counts(conn: sqlite3.Connection, code: str) -> tuple[int, int]:
    """包里真实有多少商品 / 多少图。

    ★ 成员包（精筛之后存下来的那批）商品不在 v3_product.pack_code 上，
      数的是关联表 v3_pack_member；老包（导入生成的）口径完全不变。
    """
    mids = _member_pids(conn, code)
    if mids:
        q = ",".join("?" * len(mids))
        i = conn.execute(
            "SELECT COUNT(*) FROM v3_image WHERE product_id IN (%s)" % q, mids).fetchone()[0]
        return len(mids), int(i or 0)
    p = conn.execute("SELECT COUNT(*) FROM v3_product WHERE pack_code=?", (code,)).fetchone()[0]
    i = conn.execute(
        "SELECT COUNT(*) FROM v3_image WHERE product_id IN"
        " (SELECT product_id FROM v3_product WHERE pack_code=?)", (code,)).fetchone()[0]
    return int(p or 0), int(i or 0)


# ---------------------------------------------------------------- 查询

def list_packs() -> list[dict]:
    """所有数据包 + 实时商品/图片数（库里的真数，不信记账附表）。"""
    conn = store.connect()
    try:
        store.init(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM v3_pack ORDER BY COALESCE(converted_at, created_at) DESC, pack_code")]
        for r in rows:
            p, i = _live_counts(conn, r["pack_code"])
            r["products_live"] = p
            r["images_live"] = i
            r["members"] = len(_member_pids(conn, r["pack_code"]))
            r["is_member_pack"] = bool(r["members"])
            if r.get("status") == "converted" and not p:
                r["status"] = "empty"
                r["message"] = "账上说转换过，但库里 0 个商品"
            r["ready"] = bool(p) and bool(i)
            r["reasons"] = _reasons(p, i)
        return rows
    finally:
        conn.close()


def _reasons(p: int, i: int) -> list[str]:
    out = []
    if not p:
        out.append("没有商品：文件没转成功，或标题列没认出来")
    if p and not i:
        out.append("没有图片：主图列是空的或不是 http 网址，检测打码无从下手")
    return out


def status(code: str) -> dict:
    conn = store.connect()
    try:
        store.init(conn)
        r = conn.execute("SELECT * FROM v3_pack WHERE pack_code=?", (code,)).fetchone()
        if not r:
            return {"ok": False, "error": "没有这个数据包：%s" % code}
        d = dict(r)
        p, i = _live_counts(conn, code)
        d.update({"products_live": p, "images_live": i,
                  "ready": bool(p) and bool(i), "reasons": _reasons(p, i)})
        no_img = conn.execute(
            "SELECT COUNT(*) FROM v3_product WHERE pack_code=? AND product_id NOT IN"
            " (SELECT DISTINCT product_id FROM v3_image WHERE role='main')", (code,)).fetchone()[0]
        d["products_without_main"] = int(no_img or 0)
        d["ok"] = True
        return d
    finally:
        conn.close()


def rename(code: str, new_name: str) -> dict:
    """改数据包的「显示名」，只写 v3_pack.display_name。

    ★ 为什么不改 pack_code：它是 v3_product / v3_pack_member / v3_upload_status /
      v3_publish_intent 等十几处的外键，直接改主键必漏，2026-10-24 的「176 条悬空成员」
      就是同类坑。显示名是纯展示层，空名 = 恢复默认（表格回退显示 pack_code）。
    """
    name = (new_name or "").strip()
    if not code:
        return {"ok": False, "error": "没有指定数据包"}
    if len(name) > 120:
        return {"ok": False, "error": "名字太长（最多 120 字）"}
    conn = store.connect()
    try:
        store.init(conn)
        row = conn.execute("SELECT pack_code, display_name FROM v3_pack WHERE pack_code=?",
                           (code,)).fetchone()
        if not row:
            return {"ok": False, "error": "没有这个数据包：%s" % code}
        old = row["display_name"]
        conn.execute("UPDATE v3_pack SET display_name=?, updated_at=? WHERE pack_code=?",
                     (name or None, _now(), code))
        conn.commit()
        try:
            store.audit(conn, "pack_rename", "pack", code,
                         {"display_name": old}, {"display_name": name or None},
                         actor="2.0改名")
            conn.commit()
        except Exception:                       # noqa: BLE001
            pass                                # 审计失败不影响改名本身
        return {"ok": True, "pack_code": code, "display_name": name,
                "message": ("已改名：%s → %s" % (code, name)) if name
                           else "已恢复默认名（%s）" % code}
    finally:
        conn.close()


def preview(path: str, limit: int = 8) -> dict:
    """不导入，只读前几行看列认不认得（防"导进去才发现全是空"）。"""
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "文件不存在"}
    kind = sniff(path)
    if not kind:
        return {"ok": False, "error": "不认识的格式"}
    try:
        if kind == "csv":
            text = importer._read_text(path)
            rd = csv.DictReader(io.StringIO(text))
            cols = list(rd.fieldnames or [])
            samples = []
            for n, r in enumerate(rd):
                if n >= limit:
                    break
                samples.append({k: (v or "")[:80] for k, v in list(r.items())[:14]})
            mapped = {c: importer._LOOKUP.get(importer._norm_key(c), "") for c in cols}
            is19 = importer.is_wechat19(cols)
            if is19:
                # 19 列里的 SKU 六列由专用解析处理（不是"认不出的列"）
                for c in cols:
                    if importer._norm_key(c) in {importer._norm_key(x) for x in
                                                 importer.WECHAT19_COLS}:
                        mapped[c] = mapped.get(c) or "wechat19"
            unknown = [c for c, f in mapped.items() if not f]
            return {"ok": True, "kind": "csv", "rows": count_rows(path),
                    "columns": cols, "mapped": mapped, "unknown": unknown,
                    "format": "wechat19" if is19 else "generic",
                    "samples": samples,
                    "guess_pack": os.path.splitext(os.path.basename(path))[0]}
        return {"ok": True, "kind": "json", "rows": count_rows(path),
                "guess_pack": os.path.splitext(os.path.basename(path))[0]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 删除

def delete_pack(code: str, purge_products: bool = False,
                purge_shared: bool = False, mode: str = "trash",
                cancel_check=None) -> dict:
    """删掉一个数据包。**2026-10-24 起委托给 safedelete（安全语义）**。

    旧行为（purge_products 默认 True、按 pack_code 取商品、不清别的包成员行）
    是 176 条悬空成员的根因，已废弃。新语义：
      · 默认只删「包 + 本包成员关系」，商品/图片/OCR 一律不动；
      · 仍被任何别的包引用的商品，**绝不删除**；
      · 真要连商品一起删，必须显式 purge_products=True，
        而"连共享商品也删"还要再加 purge_shared=True（界面上要二次确认）；
      · 全程一个事务，失败整体回滚；删商品时同步清所有包的成员行。
    兼容说明：本函数保留原名字与原调用方式，老调用方"不传参"从现在起=安全模式。

    ★ 2026-10-24 修（主人实测「点删除提示没有这个数据包，列表里又还在」）：
      三个历史集合（户外野营凳/商品明细/自检）**只在 v3_product.pack_code 上**，
      包表与成员表都没有行 —— 旧代码在这里直接 return「没有这个数据包」，
      而列表是从 pack_code 分组来的，刷新就又长出来。
      现在：包表/成员表都没有时，走 `trash.delete_collection`（按 pack_code 处理真实商品）。
      mode: detach 解除归属（商品保留）| trash 移入回收站（默认，可还原）| purge 彻底删
    """
    conn = store.connect()
    try:
        store.init(conn)
        r = conn.execute("SELECT 1 FROM v3_pack WHERE pack_code=?", (code,)).fetchone()
        m = 0
        try:
            m = conn.execute("SELECT COUNT(*) FROM v3_pack_member WHERE pack_code=?",
                             (code,)).fetchone()[0]
        except sqlite3.Error:
            m = 0
        if not r and not m:
            n_own = conn.execute("SELECT COUNT(*) FROM v3_product WHERE pack_code=?",
                                 (code,)).fetchone()[0]
            if not n_own:
                return {"ok": False, "error": "没有这个数据包：%s"
                                              "（包表、成员表、商品归属三处都没有）" % code}
            from . import trash as _trash
            # ★ 2026-10-24 主人口径：删除就是彻底删除，不再留回收站副本
            mode2 = (mode or "purge").lower()
            if purge_products:
                mode2 = "purge"
            out = _trash.delete_collection(conn, code, mode=mode2,
                                           purge_shared=purge_shared,
                                           cancel_check=cancel_check)
            left = _live_counts(conn, code)
            out.update({"pack_code": code, "products_left": left[0], "images_left": left[1],
                        "legacy_collection": True})
            return out
        out = safedelete.delete_pack(conn, code, purge_products=purge_products,
                                     purge_shared=purge_shared, cancel_check=cancel_check)
        left = _live_counts(conn, code)
        out.update({"pack_code": code, "products_left": left[0], "images_left": left[1]})
        return out
    finally:
        conn.close()


def _live_counts_after(conn, code) -> tuple[int, int]:
    return _live_counts(conn, code)


# ---------------------------------------------------------------- 送到小猪检测（第三步）

PIG_COLS = ["商品ID", "商品名称", "品牌", "分类", "库存",
            "团购价格", "单买价格",
            "商品主图", "商品轮播图", "商品详情图", "商品描述", "商品视频", "商品属性",
            "SKUID", "SKU名称", "SKU库存", "SKU团购价格", "SKU单买价格", "SKU预览图"]

PIG_PACKS = r"J:\aixitong\小猪\4.0.5本地化\packs"


def export_pig_csv(code: str, out_dir: str = "") -> dict:
    """把一个 v3 数据包导成小猪吃的「微信小店 19 列」CSV（GBK）。

    放在 packs\\_按包\\ 下 —— 启动链路时按显式路径导入，不靠 packs 目录扫描，
    免得把别的包一起重复导入。

    ★ 19 列口径统一走 `server/export.py: assemble_row`（轮播图=全部主图、
      价格缺了从 SKU 回落、库存优先 SKU 求和），不再自己拼一套。
    """
    from .. import export as _export

    conn = store.connect()
    try:
        store.init(conn)
        rows, warns, prods = _export.rows_from_v3(conn, pack=code)
        if not prods:
            return {"ok": False, "error": "这个包里 0 个商品（包码对不上？）：%s" % code}

        out_dir = out_dir or os.path.join(PIG_PACKS, "_按包")
        os.makedirs(out_dir, exist_ok=True)
        safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in code)
        path = os.path.join(out_dir, "%s_19列.csv" % safe)
        _export.write_csv(rows, path)
        s = _export.fill_summary(rows)
        return {"ok": True, "path": path, "rows": len(rows),
                "with_main": sum(1 for r in rows if r[7]),
                "fill": {c["column"]: c["ok"] for c in s["columns"]},
                "warnings": warns[:20], "warning_count": len(warns),
                "message": "%d 个商品 → %s" % (len(rows), path)}
    finally:
        conn.close()


def list_inbox() -> list[dict]:
    d = ensure_dir()
    out = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.isfile(p) and os.path.splitext(f)[1].lower() in (".csv", ".tsv", ".json", ".txt"):
            out.append({"name": f, "path": p, "size_kb": _kb(p), "rows": count_rows(p)})
    return out


# ---------------------------------------------------------------- 成员包（精筛 → 检测打码的数据传递）
#
# 用户场景：户外野营凳 184 件，精筛通过 115 件 —— 这一步要能把「通过的这 115 件」
# 存成一个数据包，交给第 4 步检测打码。
#
# 存的是**引用**：v3_pack_member 只记 product_id，商品行 / 图片行一个都不复制。
# 复制会让同一批图被下载两遍、打码两遍；引用则每个商品本体只有一份。

KEEP_EXCLUDED = ("blocked", "rejected", "pending")


def _retry_locked(fn, times: int = 4, wait: float = 0.25):
    """写库撞上 WAL 写锁时重试几次。

    界面上连点两次「保存」、或别的页签正在写，都会真的撞到 locked；
    写的内容是幂等 upsert，重试一次比弹一个报错好。
    """
    last: Exception | None = None
    for i in range(max(1, times)):
        try:
            return fn()
        except sqlite3.OperationalError as e:      # noqa: PERF203
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last = e
            time.sleep(wait * (i + 1))
    raise last if last else sqlite3.OperationalError("locked")


def _member_pids(conn: sqlite3.Connection, code: str) -> list[int]:
    """成员包的 product_id 列表（升序）；不是成员包、或表还没建 → []。"""
    if not code:
        return []
    try:
        return [int(r[0]) for r in conn.execute(
            "SELECT product_id FROM v3_pack_member WHERE pack_code=? ORDER BY product_id",
            (code,)).fetchall()]
    except sqlite3.Error:
        return []


def _code_taken(conn: sqlite3.Connection, code: str) -> bool:
    # fetchall 而非 fetchone：留半开的游标会握着共享锁，让别的连接写不进去
    if conn.execute("SELECT 1 FROM v3_pack WHERE pack_code=?", (code,)).fetchall():
        return True
    try:
        return bool(conn.execute(
            "SELECT 1 FROM v3_pack_member WHERE pack_code=? LIMIT 1", (code,)).fetchall())
    except sqlite3.Error:
        return False


def _uniq_member_code(conn: sqlite3.Connection, base: str) -> str:
    """成员包一律**新建**：同名加 -2/-3，绝不覆盖已经存过的成员包。"""
    base = (base or "包").strip() or "包"
    if not _code_taken(conn, base):
        return base
    i = 2
    while True:
        code = "%s-%d" % (base, i)
        if not _code_taken(conn, code):
            return code
        i += 1


def _pick_pids(conn: sqlite3.Connection, source_pack: str, only: str) -> list[int]:
    """按口径从源包里取商品：keep = 没被拦下的；all = 全部。

    ★ 2026-10-25 修（主人报「保存为数据包 → only-keep 找不到符合条件的商品」）：
      原来只查 `v3_product.pack_code`，**但真库这一列是空的**（成员关系存在
      `v3_pack_member` 表里）⇒ 永远 0 条。现在两处都取，UNION 去重。
    """
    sql_pc = "SELECT product_id FROM v3_product WHERE pack_code=?"
    sql_mem = "SELECT product_id FROM v3_pack_member WHERE pack_code=?"
    if only == "all":
        sql = "SELECT product_id FROM (%s UNION %s) ORDER BY product_id" % (sql_pc, sql_mem)
        rows = conn.execute(sql, (source_pack, source_pack))
    else:
        marks = ",".join("?" * len(KEEP_EXCLUDED))
        sql = ("SELECT product_id FROM ("
               " SELECT product_id FROM v3_product WHERE pack_code=?"
               "  AND COALESCE(status,'') NOT IN (%s)"
               " UNION"
               " SELECT m.product_id FROM v3_pack_member m"
               "  JOIN v3_product p ON p.product_id=m.product_id"
               " WHERE m.pack_code=? AND COALESCE(p.status,'') NOT IN (%s)"
               ") ORDER BY product_id" % (marks, marks))
        rows = conn.execute(sql, [source_pack] + list(KEEP_EXCLUDED) +
                             [source_pack] + list(KEEP_EXCLUDED))
    return [int(r[0]) for r in rows]


def save_members(conn: sqlite3.Connection, name: str = "", pids=None,
                 source_pack: str = "", only: str = "keep", note: str = "") -> dict:
    """把一批商品存成一个「成员包」（引用式，不复制商品/图片）。

    pids 给了就用 pids（不存在的会被剔掉并如实报在 exists 里）；
    pids 为空则按 (source_pack, only) 从 v3_product 现取：
        only='keep'  status 不是 blocked / rejected 的（= 精筛通过的）
        only='all'   源包全部商品

    包码：``{来源}_{名称}_{N}件_{YYYYMMDD_HHMM}``，撞名自动加 -2/-3。
    返回：{ok, pack_code, count, exists, source_pack, only, images, message}
    """
    out: dict[str, Any] = {"ok": False, "pack_code": "", "count": 0, "exists": [],
                           "source_pack": source_pack, "only": only}
    only = (only or "keep").strip().lower()
    if only not in ("keep", "all"):
        only = "keep"
        out["only"] = only

    want = [int(x) for x in (pids or []) if str(x).strip() != ""]
    if not want:
        if not source_pack:
            out["error"] = "pids 与 source_pack 至少要给一个"
            return out
        want = _pick_pids(conn, source_pack, only)
        if not want:
            out["error"] = ("源包 %s 里没有符合条件的商品（only=%s）" % (source_pack, only))
            return out

    # 只留真实存在的商品；不存在的如实报出来，不静默丢
    alive: set[int] = set()
    for k in range(0, len(want), 500):
        chunk = want[k:k + 500]
        q = ",".join("?" * len(chunk))
        alive |= {int(r[0]) for r in conn.execute(
            "SELECT product_id FROM v3_product WHERE product_id IN (%s)" % q, chunk)}
    out["exists"] = [p for p in want if p not in alive]
    want = [p for p in want if p in alive]
    if not want:
        out["error"] = "给的商品 id 在库里一个都不存在"
        return out

    n = len(want)
    src = (source_pack or "").strip() or _first_pack_of(conn, want)
    label = (name or "").strip() or ("精筛" if only == "keep" else "全部")
    base = "%s_%s_%d件_%s" % (src, label, n, time.strftime("%Y%m%d_%H%M"))
    code = _uniq_member_code(conn, base)

    _upsert_pack(conn, code, source_file="screen:%s" % src, source_name=label,
                 rows=n, products=n, images=0, skipped=0, status="ready",
                 message="%d 个商品（引用式成员包，源包 %s）" % (n, src),
                 error="", converted_at=_now())
    ts = _now()

    def _write() -> None:
        conn.executemany(
            "INSERT OR REPLACE INTO v3_pack_member"
            " (pack_code, product_id, source_pack, note, created_at) VALUES(?,?,?,?,?)",
            [(code, p, src, note or "", ts) for p in want])
        conn.commit()

    _retry_locked(_write)

    p2, i2 = _live_counts(conn, code)
    store.audit(conn, "save_members", "pack", code, None,
                {"count": n, "source_pack": src, "only": only})
    conn.commit()      # audit 自己不 commit；不提交会握着写锁，把下一个连接挡在外面
    out.update({"ok": True, "pack_code": code, "count": p2, "images": i2,
                "message": "%d 个商品 → 数据包「%s」" % (p2, code)})
    return out


def _first_pack_of(conn: sqlite3.Connection, pids: list[int]) -> str:
    """pids 来自哪个包（混合来源就报「混合」），只用于生成包名。"""
    packs: set[str] = set()
    for k in range(0, len(pids), 500):
        chunk = pids[k:k + 500]
        q = ",".join("?" * len(chunk))
        packs |= {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT pack_code FROM v3_product WHERE product_id IN (%s) AND pack_code IS NOT NULL"
            % q, chunk)}
    if len(packs) == 1:
        return packs.pop()
    return "混合" if packs else "包"


def members(conn: sqlite3.Connection, pack_code: str) -> list[int] | None:
    """成员包的 product_id 列表。

    **不是成员包返回 None** —— 调用方据此回落旧逻辑（按 v3_product.pack_code 取）。
    """
    mids = _member_pids(conn, pack_code)
    return mids or None


def delete_members(conn: sqlite3.Connection, pack_code: str) -> dict:
    """删掉一个**成员包**（只删关联表 + v3_pack 那行）。

    真实商品 / 图片 / OCR 缓存一行都不碰 —— 它们属于源包。
    对非成员包（导入生成的包）拒绝执行，避免误删别人的包。
    """
    if not pack_code:
        return {"ok": False, "error": "没有给包码"}
    mids = _member_pids(conn, pack_code)
    rows = conn.execute("SELECT source_file FROM v3_pack WHERE pack_code=?",
                        (pack_code,)).fetchall()
    src = (rows[0]["source_file"] or "") if rows else ""
    if not mids and not src.startswith("screen:"):
        return {"ok": False, "error": "不是成员包，拒绝删除：%s" % pack_code}
    n = len(mids)

    def _write() -> None:
        conn.execute("DELETE FROM v3_pack_member WHERE pack_code=?", (pack_code,))
        conn.execute("DELETE FROM v3_pack WHERE pack_code=?", (pack_code,))
        conn.commit()

    _retry_locked(_write)
    store.audit(conn, "delete_members", "pack", pack_code, {"members": n}, None)
    conn.commit()      # 同上：audit 不 commit，必须自己提交把锁放开
    return {"ok": True, "pack_code": pack_code, "removed": n}

