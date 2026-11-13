# -*- coding: utf-8 -*-
"""4.1 主图人工审核 · 数据层（只读）

与 2.5 商品库 **同一个库**（`taobao-picker\\data\\v3.db`），这里只做两件事：

  ① 按「图片」而不是「商品」取数 —— 照片墙一格 = 一张主图
  ② 把编辑台账（`v3_image_edit`）的状态合并进来，卡片上就能看到
     未处理 / 有草稿 / 已存版本 / 已确认

只读：`mode=ro` 打开，任何写入都会当场抛错。写操作一律走
`server/v3/edits.py`（版本本体仍在 v3_image，不另建库）。

包口径与 2.5 一致：先看成员表（v3_pack_member），空则回落 v3_product.pack_code
—— 两个页面显示同一份包清单，不允许各算一套。
"""
from __future__ import annotations

import os
import sqlite3

IMG = "current_local_path"

STATE_ALL = "全部"
STATE_NONE = "未处理"
STATE_DRAFT = "有草稿"
STATE_SAVED = "已存版本(未确认)"
STATE_CONF = "已确认"
STATE_NOLOCAL = "没有本地图"
STATES = [STATE_ALL, STATE_NONE, STATE_DRAFT, STATE_SAVED, STATE_CONF, STATE_NOLOCAL]


class MainLib:
    def __init__(self, db_path: str, root: str = ""):
        self.db = db_path
        self.root = root or os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(db_path))))

    # ---------------------------------------------------------------- 基础
    def _ro(self) -> sqlite3.Connection:
        uri = "file:%s?mode=ro" % self.db.replace("\\", "/")
        con = sqlite3.connect(uri, uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        return con

    def ok(self) -> tuple[bool, str]:
        if not os.path.isfile(self.db):
            return False, "库不存在：%s" % self.db
        try:
            con = self._ro()
            try:
                n = con.execute("select count(*) from v3_image").fetchone()[0]
            finally:
                con.close()
            return True, "v3_image %d 行" % n
        except Exception as e:          # noqa: BLE001
            return False, "%s: %s" % (type(e).__name__, e)

    def abspath(self, p: str) -> str:
        if not p:
            return ""
        return p if os.path.isabs(p) else os.path.join(self.root, p)

    # ---------------------------------------------------------------- 包
    def packs(self) -> list[dict]:
        """包清单 + 各包主图数（与 2.5 同口径）。"""
        con = self._ro()
        try:
            codes: list[str] = []
            seen = set()

            def add(c):
                c = (c or "").strip()
                if c and c not in seen:
                    seen.add(c); codes.append(c)

            try:
                for r in con.execute("select pack_code from v3_pack order by pack_code"):
                    add(r[0])
            except sqlite3.OperationalError:
                pass
            try:
                for r in con.execute("select distinct pack_code from v3_pack_member"):
                    add(r[0])
            except sqlite3.OperationalError:
                pass
            for r in con.execute("select distinct pack_code from v3_product "
                                 "where pack_code is not null and pack_code<>''"):
                add(r[0])
            out = []
            for c in codes:
                n = self._count_mains(con, c)
                if n:
                    d = {"code": c, "n": n, "display_name": ""}
                    try:
                        r = con.execute(
                            "SELECT display_name, created_at, converted_at FROM v3_pack"
                            " WHERE pack_code=?", (c,)).fetchone()
                        if r:
                            d["display_name"] = r[0] or ""
                            d["created_at"] = r[1] or ""
                            d["converted_at"] = r[2] or ""
                    except sqlite3.OperationalError:
                        pass
                    out.append(d)
            # ★ 8.0：最新批次优先（与全项目 choose_default 同口径），
            #   显示名留给用户 2.0 改名；历史包无 display_name 时回退原码。
            from . import packlabel as _pl  # 延迟导入，避免 v3img 被单独 import 时绕环
            out.sort(key=lambda x: _pl.sort_key(
                {"pack_code": x["code"], "display_name": x.get("display_name"),
                 "created_at": x.get("created_at"), "converted_at": x.get("converted_at")}),
                reverse=True)
            return out
        finally:
            con.close()

    def _pack_cond(self, con, code: str) -> tuple[str, tuple]:
        try:
            nmem = con.execute("select count(*) from v3_pack_member where pack_code=?",
                              (code,)).fetchone()[0]
        except sqlite3.OperationalError:
            nmem = 0
        if nmem:
            return ("exists(select 1 from v3_pack_member m where m.product_id=p.product_id"
                    " and m.pack_code=?)", (code,))
        return ("p.pack_code = ?", (code,))

    def _count_mains(self, con, code: str) -> int:
        cond, args = self._pack_cond(con, code)
        return con.execute(
            "select count(*) from v3_image i join v3_product p on p.product_id=i.product_id"
            " where i.role='main' and " + cond, args).fetchone()[0]

    # ---------------------------------------------------------------- 主图
    def mains(self, pack: str = "", q: str = "", state: str = STATE_ALL,
              limit: int = 3000) -> list[dict]:
        con = self._ro()
        try:
            where, args = ["i.role='main'"], []
            if pack:
                cond, a = self._pack_cond(con, pack)
                where.append(cond); args += list(a)
            if q:
                where.append("(p.original_title like ? or cast(i.product_id as text) like ?"
                             " or cast(i.image_id as text) like ?)")
                args += ["%%%s%%" % q] * 3
            # ★ 2026-10-27：current_local_path 必须**同时**以原名字段返回。
            #   查因：批量消除 _MarkWorker 读 row["current_local_path"]，而这里只给了
            #   别名 local_path → worker 永远取到空 → 误报「没有本地文件」，6/6 全失败。
            #   做法：原名字段 + local_path 别名并存，两个消费端都不用改。
            sql = ("select i.image_id, i.product_id, i.role, i.sequence, i.status,"
                   " i.current_local_path, i.current_local_path as local_path,"
                   " i.original_local_path, i.parent_image_id, i.content_sha256,"
                   " i.width, i.height, i.current_remote_url,"
                   " p.original_title as title, p.price_min as price, p.pack_code,"
                   " p.main_image_id, p.source_platform as platform"
                   " from v3_image i join v3_product p on p.product_id=i.product_id"
                   " where " + " and ".join(where) +
                   " order by i.product_id, i.sequence limit ?")
            rows = [dict(r) for r in con.execute(sql, tuple(args) + (int(limit),))]

            edits = {}
            try:
                from server.v3 import edits as E   # 同进程内复用，避免再写一份 SQL
                edits = E.summary_map(con, [r["image_id"] for r in rows])
            except Exception:          # noqa: BLE001
                edits = {}

            out = []
            for r in rows:
                full = self.abspath(r.get("local_path") or "")
                r["abs_path"] = full
                r["exists"] = bool(full and os.path.isfile(full))
                r["is_confirmed_main"] = (r.get("main_image_id") == r["image_id"])
                st = edits.get(int(r["image_id"])) or {}
                r["has_draft"] = bool(st.get("has_draft"))
                r["has_version"] = bool(st.get("has_version"))
                r["confirmed"] = bool(st.get("confirmed"))
                r["last_method"] = st.get("last_method") or ""
                r["last_quality"] = st.get("last_quality") or ""
                if not r["exists"]:
                    r["state"] = STATE_NOLOCAL
                elif r["confirmed"]:
                    r["state"] = STATE_CONF
                elif r["has_version"]:
                    r["state"] = STATE_SAVED
                elif r["has_draft"]:
                    r["state"] = STATE_DRAFT
                else:
                    r["state"] = STATE_NONE
                out.append(r)

            if state and state != STATE_ALL:
                out = [r for r in out if r["state"] == state]
            return out
        finally:
            con.close()

    # ---------------------------------------------------------------- 单张
    def image(self, image_id: int) -> dict | None:
        con = self._ro()
        try:
            r = con.execute(
                "select i.*, p.original_title as title, p.price_min as price,"
                " p.pack_code, p.main_image_id from v3_image i"
                " join v3_product p on p.product_id=i.product_id where i.image_id=?",
                (int(image_id),)).fetchone()
            if not r:
                return None
            d = dict(r)
            d["abs_path"] = self.abspath(d.get(IMG) or d.get("original_local_path") or "")
            d["exists"] = bool(d["abs_path"] and os.path.isfile(d["abs_path"]))
            return d
        finally:
            con.close()

    def versions(self, image_id: int) -> list[dict]:
        """这张图的版本链：自己 + 所有 parent_image_id 指向它的行。"""
        con = self._ro()
        try:
            rows = con.execute(
                "select image_id, parent_image_id, status, %s as local_path,"
                " content_sha256, sequence, created_at from v3_image"
                " where image_id=? or parent_image_id=? order by image_id" % IMG,
                (int(image_id), int(image_id))).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["abs_path"] = self.abspath(d.get("local_path") or "")
                d["exists"] = bool(d["abs_path"] and os.path.isfile(d["abs_path"]))
                out.append(d)
            return out
        finally:
            con.close()

    def ocr_boxes(self, image_id: int) -> list[dict]:
        """复用第二步已存的 OCR 坐标（不重跑识别）。"""
        con = self._ro()
        try:
            r = con.execute("select boxes_json from v3_image_ocr where image_id=?"
                            " and state='ok'", (int(image_id),)).fetchone()
            if not r or not r[0]:
                return []
            import json
            try:
                data = json.loads(r[0])
            except Exception:          # noqa: BLE001
                return []
            return data if isinstance(data, list) else []
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()

    def stats(self) -> dict:
        con = self._ro()
        try:
            g = lambda s: con.execute(s).fetchone()[0]
            n_main = g("select count(*) from v3_image where role='main'")
            n_local = g("select count(*) from v3_image where role='main'"
                        " and %s is not null and %s<>''" % (IMG, IMG))
            return {"mains": n_main, "local": n_local,
                    "products": g("select count(*) from v3_product")}
        finally:
            con.close()
