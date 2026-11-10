# -*- coding: utf-8 -*-
"""4.1 · 主图人工审核 —— 页签容器 + 写库适配器

它把三件事粘起来：
  ① `wall_main.MainWallPage`（照片墙，按图片）
  ② `editor.EditorWindow`（局部修补编辑器）
  ③ `server/v3/edits.py`（**唯一的写入方**：版本仍进 v3_image，本表只记台账）

三条自我约束：
  · 页面只读打开数据库；**只有点「保存新版本 / 确认合格 / 批量确认原图」时才开写连接**
  · 第一次写之前，先把 v3.db 复制一份到 data\\backup（一次/进程）
  · 任何一步失败都如实返回，不在界面上写"已完成"

构造失败不许拖垮主程序：显示一行原因，其它页照常。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time

import numpy as np
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import paths

DB_PATH = os.path.join(str(paths.TAOBAO_DIR), "data", "v3.db")
THUMB_DIR = os.path.join(str(paths.DATA), "thumbs_41")
BACKUP_DIR = os.path.join(str(paths.TAOBAO_DIR), "data", "backup")


def _prep_path() -> None:
    for p in (str(paths.TAOBAO_DIR), str(paths.TAOBAO_SERVER)):
        if p not in sys.path:
            sys.path.insert(0, p)


class WallMainApi:
    """给照片墙与编辑器用的写入/查询适配器。

    **写连接按需开、用完关** —— 不长期持有写锁，避免和第二步/第五步撞锁。
    """

    def __init__(self, db_path: str = DB_PATH, root: str = "", log=None):
        self.db = db_path or DB_PATH
        self.root = root or str(paths.TAOBAO_DIR)
        self.log = log or (lambda s: None)
        self._backed = ""
        self.actor = "4.1"

    # ---------------- 连接
    def _rd(self) -> sqlite3.Connection:
        uri = "file:%s?mode=ro" % self.db.replace("\\", "/")
        c = sqlite3.connect(uri, uri=True, timeout=3)
        c.row_factory = sqlite3.Row
        return c

    def _wr(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db, timeout=8)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=8000")
        return c

    def _mods(self):
        _prep_path()
        from server.v3 import edits, images, store          # noqa: F401
        return edits, images, store

    def backup_once(self) -> str:
        """第一次写之前备份一次（同进程只备份一次）。返回备份路径。"""
        if self._backed:
            return self._backed
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            dst = os.path.join(BACKUP_DIR, "v3.db.bak-4.1-%s" % time.strftime("%Y%m%d-%H%M%S"))
            src = sqlite3.connect(self.db, timeout=8)
            try:
                out = sqlite3.connect(dst)
                try:
                    src.backup(out)
                finally:
                    out.close()
            finally:
                src.close()
            self._backed = dst
            self.log("4.1 写库前已备份：%s" % dst)
            return dst
        except Exception as e:          # noqa: BLE001
            self.log("4.1 备份失败（不阻断，但要知道）：%s: %s" % (type(e).__name__, e))
            return ""

    # ---------------- 读
    def ocr_boxes(self, image_id) -> list:
        _prep_path()
        from server.v3 import images
        c = self._rd()
        try:
            got = images.boxes_of(c, int(image_id))
        finally:
            c.close()
        if not got:
            return []
        if isinstance(got, dict):
            return got.get("boxes") or got.get("items") or []
        return got if isinstance(got, list) else []

    def state(self, image_id) -> dict:
        self._mods()
        from server.v3 import edits
        c = self._rd()
        try:
            st = edits.state_of(c, int(image_id))
            rows = edits.list_edits(c, image_id=int(image_id), limit=20)
            pend = [r for r in rows if r.get("status") == "saved"]
            st["pending_version_id"] = (pend[0].get("version_image_id") if pend else 0)
            st["edits"] = rows
            return st
        finally:
            c.close()

    def versions(self, image_id) -> list:
        _prep_path()
        c = self._rd()
        try:
            rows = c.execute(
                "select image_id, parent_image_id, status, current_local_path, content_sha256,"
                " created_at from v3_image where image_id=? or parent_image_id=? order by image_id",
                (int(image_id), int(image_id))).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()

    # ---------------- 写
    def save_draft(self, img: dict, mask, regions, method: str, radius: int,
                   expand_px: int = 0) -> dict:
        self.backup_once()
        edits, _images, _store = self._mods()
        c = self._wr()
        try:
            edits.ensure_table(c)
            return edits.save_draft(
                c, int(img["product_id"]), int(img["image_id"]),
                mask=mask, regions=regions, method=method, radius=radius,
                expand_px=expand_px, note="4.1 草稿", actor=self.actor)
        finally:
            c.close()

    def save_version(self, img: dict, result_bgr, mask, regions, method: str,
                     radius: int, expand_px: int = 0, quality_text: str = "",
                     auto_confirm: bool = True) -> dict:
        if result_bgr is None or getattr(result_bgr, "size", 0) == 0:
            return {"ok": False, "error": "没有修补结果可保存"}
        self.backup_once()
        edits, _images, _store = self._mods()
        import cv2
        pid = int(img["product_id"]); iid = int(img["image_id"])
        # 2026-10-24 修：原来用 time.strftime("%H%M%S%f") —— %f 是 datetime 专有指令，
        # Windows 的 time.strftime(MSVCRT) 不支持，必抛 ValueError: Invalid format string。
        # 用 time_ns() 代替，且改用 imencode + 写字节，避开 cv2.imwrite 对非 ASCII 路径的问题。
        tmp = os.path.join(tempfile.gettempdir(),
                           "41_%s_%s_%d.png" % (pid, iid, time.time_ns()))
        try:
            ok_enc, buf = cv2.imencode(".png", result_bgr)
            if not ok_enc:
                return {"ok": False, "error": "编码修补结果失败（内存不足？）"}
            with open(tmp, "wb") as fh:
                fh.write(buf.tobytes())
        except Exception as e:          # noqa: BLE001
            return {"ok": False, "error": "写临时文件失败：%s: %s" % (type(e).__name__, e)}
        c = self._wr()
        try:
            edits.ensure_table(c)
            r = edits.save_version(
                c, pid, iid, tmp, mask=mask, regions=regions, method=method,
                radius=radius, expand_px=expand_px,
                inspect_result=None,
                pack_code=(img.get("pack_code") or ""),
                note=("4.1 人工修补 · %s" % (quality_text or ""))[:480],
                actor=self.actor, keep_status=True)
            # ★★ 2026-10-27 主人反馈「他说处理好了，可照片墙上的大图一点没变」。
            #   真因不是没存、也不是缩略图缓存 —— 是**存成了候选版本，主图指针没动**：
            #     save_version 只新增一条 v3_image(candidate) + 一条编辑台账，
            #     而照片墙/详情窗显示的是 v3_product.main_image_id 指着的那条。
            #   只有「确认合格」(images.approve) 才会把 main_image_id 切到新版本。
            #   所以：默认保存即确认（换图），想留候选就传 auto_confirm=False。
            if r.get("ok") and auto_confirm:
                try:
                    # ★ 角色分流（2026-10-27）：只有主图才切 main_image_id。
                    #   详情图/SKU 图若也走 approve()，会把 v3_product.main_image_id
                    #   指到一张详情图上 —— 照片墙看不出来（它只取 role='main'），
                    #   但上传/出表那些模块会把详情图当主图用。那是数据污染。
                    #   非主图走 approve_media()：把新文件写进原行的 current_local_path，
                    #   原行立刻显示新图，不新增行、不动主图指针。
                    role = str(img.get("role") or "main")
                    if role == "main":
                        cf = edits.confirm(c, pid, int(r["version_image_id"]),
                                           actor=self.actor)
                        r["confirm_via"] = "主图指针对齐"
                    else:
                        cf = _images.approve_media(c, pid, int(r["version_image_id"]),
                                                   actor=self.actor)
                        r["confirm_via"] = "原行路径替换（%s）" % role
                    r["confirmed"] = bool(cf.get("ok"))
                    r["main_image_id"] = cf.get("main_image_id")
                    r["previous_main_image_id"] = cf.get("previous_main_image_id")
                    if not cf.get("ok"):
                        r["confirm_error"] = cf.get("error") or "确认接口没给原因"
                except Exception as e:          # noqa: BLE001
                    r["confirmed"] = False
                    r["confirm_error"] = "%s: %s" % (type(e).__name__, e)
            else:
                r["confirmed"] = False
            return r
        finally:
            c.close()
            try:
                os.remove(tmp)
            except OSError:
                pass

    def confirm(self, img: dict, version_image_id: int) -> dict:
        self.backup_once()
        edits, _images, _store = self._mods()
        c = self._wr()
        try:
            edits.ensure_table(c)
            return edits.confirm(c, int(img["product_id"]), int(version_image_id),
                                 actor=self.actor)
        finally:
            c.close()

    def confirm_original(self, pid: int, image_id: int, note: str = "") -> dict:
        self.backup_once()
        edits, _images, _store = self._mods()
        c = self._wr()
        try:
            edits.ensure_table(c)
            return edits.confirm_original(c, int(pid), int(image_id),
                                          note=note or "4.1 批量确认：原图即合格",
                                          actor=self.actor)
        finally:
            c.close()


class MainWallTab(QWidget):
    """4.1 页签。"""

    def __init__(self, win=None):
        super().__init__()
        self.win = win
        self.page = None
        self.err = ""
        self.logw = None
        self.api = WallMainApi(log=self._logV)
        self._loaded = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        try:
            _prep_path()
            os.makedirs(THUMB_DIR, exist_ok=True)
            if not os.path.isfile(DB_PATH):
                raise FileNotFoundError("生产库不存在：%s" % DB_PATH)
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QSplitter

            from .logwin import LogWindow
            from .wall_main import MainWallPage
            from .workbench import DEFAULT_W, MIN_W, Workbench

            # ★ 2026-10-27 用户要求：每个任务序列都要有**独立**的日志窗，
            #   右侧竖版、可缩可放、每次做完落盘保存（后续据此修）。
            #   session 名决定落盘文件名：wall41-YYYYMMDD.log
            self.logw = LogWindow(self, title="4.1 主图审核", session="wall41")

            # 左侧照片墙 + 右侧固定工作台（可拖、可收）
            self.split = QSplitter(Qt.Orientation.Horizontal)
            self.split.setChildrenCollapsible(False)
            self.page = MainWallPage(DB_PATH, THUMB_DIR, self.api, parent=self)
            self.page.logw = self.logw          # 页内直接调 logw.info/error
            self.wb = Workbench(self.win, self.api, self)
            self.split.addWidget(self.page)
            self.split.addWidget(self.wb)
            # 不用 setStretchFactor(1,0)：那会让 QSplitter 把余量全推给左边，
            # 工作台一上来就只剩最小宽度（实测 360，不是设计的 560）。
            self.split.setSizes([900, max(MIN_W, DEFAULT_W)])
            self.wb.collapsedChanged.connect(self._on_wb_collapsed)
            # 单击 ↔ 右侧工作台
            self.page.set_edit_handler(self.wb.load_row)
            self.wb.rowChanged.connect(self._on_row_changed)
            # ★ 2026-10-27 主人要求：右键主图 → 在右侧区域开详情图（不弹浮动窗）。
            #   详情图那块要用图库读 role='detail' 的行，所以把 page.lib 注进工作台。
            self.wb.lib = self.page.lib
            self.page.set_detail_handler(self.wb.load_detail)
            self.wb.wantWide.connect(self._on_want_wide)
            lay.addWidget(self.split)
        except Exception as e:          # noqa: BLE001
            self.err = "%s: %s" % (type(e).__name__, e)
            lay.addWidget(QLabel("4.1 主图人工审核加载失败：%s\n库：%s" % (self.err, DB_PATH)))

    # ---------------- 工作台宽度
    _sized = False

    def _apply_wb_width(self, collapsed: bool | None = None, w: int | None = None):
        """把工作台宽度落到 splitter 上（收起=46px 竖条，展开=记住的宽度）。"""
        try:
            total = max(int(self.split.width()), 900)
            is_c = self.wb.collapsed if collapsed is None else bool(collapsed)
            wb_w = 46 if is_c else int(w or self.wb.preferred_width())
            wb_w = max(46, min(wb_w, max(200, total - 420)))
            self.split.setSizes([max(300, total - wb_w), wb_w])
        except Exception:              # noqa: BLE001
            pass

    def _on_wb_collapsed(self, on: bool):
        self._apply_wb_width(collapsed=on)

    def _on_want_wide(self, w: int):
        """详情图页要更宽（里面是 大图 + 缩略图列表 两栏）。照片墙仍保留可读宽度。"""
        try:
            total = max(int(self.split.width()), 900)
            wb_w = max(480, min(int(w), total - 320))
            self.split.setSizes([max(320, total - wb_w), wb_w])
        except Exception:              # noqa: BLE001
            pass

    def _on_row_changed(self, row: dict, state: str):
        """保存/确认后就地改状态徽章 —— **不整表重载**，左侧滚动位置不动。"""
        try:
            from ..v3img import STATE_CONF, STATE_SAVED
        except Exception:              # noqa: BLE001
            return
        try:
            iid = int(row.get("image_id") or 0)
            r = self.page.wall.by_iid.get(iid)
            if r is None:
                return
            if state == "confirmed":
                r["state"] = STATE_CONF
                r["confirmed"] = True
            elif state == "saved":
                if not r.get("confirmed"):
                    r["state"] = STATE_SAVED
                r["has_version"] = True
            self.page.wall.cv.update()
        except Exception:              # noqa: BLE001
            pass

    def _logV(self, s: str):
        try:
            if self.win is not None:
                self.win.log(s)
        except Exception:              # noqa: BLE001
            pass
        # ★ 同步写进 4.1 自己的日志窗（全量落盘到 wall41-YYYYMMDD.log）
        try:
            if self.logw is not None:
                t = str(s)
                low = t.lower()
                if ("失败" in t or "✗" in t or "error" in low or "异常" in t
                        or "不可用" in t or "找不到" in t):
                    self.logw.error(t)
                elif "✓" in t or "完成" in t or "成功" in t:
                    self.logw.ok(t)
                elif "⚠" in t or "警告" in t:
                    self.logw.warn(t)
                else:
                    self.logw.detail(t)
        except Exception:              # noqa: BLE001
            pass

    # ---------- 主窗口会调 ----------
    def refresh(self):
        if self.page is None or self._loaded:
            return
        self._loaded = True
        try:
            self.page.fill_packs()
            self.page.refresh(pack=self.page._pack())
        except Exception as e:          # noqa: BLE001
            self._logV("4.1 刷新失败：%s: %s" % (type(e).__name__, e))
        if not self._sized:
            self._sized = True
            self._apply_wb_width()

    def summary(self) -> str:
        if self.page is None:
            return "主图审核 未就绪"
        try:
            return "主图 %d 张" % len(self.page.rows)
        except Exception:              # noqa: BLE001
            return "主图审核"
