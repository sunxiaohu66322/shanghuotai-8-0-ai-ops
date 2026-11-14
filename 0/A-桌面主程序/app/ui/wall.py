# -*- coding: utf-8 -*-
"""2.5 · 本地商品库（照片墙 + 完整商品详情）—— 上货台的本地商品资料中心。

本版（v0.5.4 / P0+P1+P2）相对阶段 B 原型的改变，全部由实测驱动：

【P0 性能】实测真库（504 件 / 7767 媒体 / 932 OCR）后逐条修：
  · `local_image()` 每件开 1 连接 + 2~3 条 SQL + exists()，504 件 **1788 ms**
    → 换成 `Library.local_images_map()` 一条 SQL 取一批（实测 ~15 ms）
  · `_fill_wall()` 一次性建 504 个 Card（625 ms）并全量提交缩略图任务
    → 分页（每页 60 件），滚到底再加载；缩略图只做当前页
  · `QTableWidget` 填 504×9 单元格 + resizeColumnsToContents（256 ms）
    → 换成 QAbstractTableModel（不建控件）
  · `reload()` 每次都重扫全部 + 重建全部控件，实测 **2785 ms**
    → 只查当前页（LIMIT/OFFSET），实测见交付报告
  · 快速切包/换尺寸时旧任务还在跑
    → 代数计数器 self._gen，过期任务直接丢弃

【P1 布局】浅色主题（跟随主程序 #F5F0FF + #7C3AED），三栏可拖：
  左 数据包/平台/类目/状态树 · 中 照片墙/列表 · 右 完整商品卡

【P2 详情卡】右侧不再是「图片属性栏」，而是纵向商品卡：
  主图 → 标题/ID/来源 → 价格库存 → SKU → 属性类目 → 店铺 → 描述
  → 详情图 → 视频 → OCR → 下载状态 → 原始来源
  并支持「独立窗口」全宽查看。来源没有的字段写「来源未提供」，不编。

仍然只读：本文件不写数据库、不改原图（批量写操作在阶段 D/E）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QSize, Qt,
                          QThread, QThreadPool, QTimer, QRunnable, pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                             QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea,
                             QProgressBar, QSizePolicy, QSlider, QSplitter, QStackedWidget,
                             QTableView, QTextEdit, QToolButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(os.path.dirname(_HERE))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, PKG_ROOT)
from server.library import Library, STATUS_LABEL   # noqa: E402

try:                                    # imgcap：图片解码闸门（并发 / 巨图 / 缩放）
    from .. import imgcap
except Exception:                       # 单独跑（无包上下文）时降级为最小实现
    class imgcap:                       # type: ignore[no-redef]
        @staticmethod
        def decode(p, max_side=0):
            from PyQt6.QtGui import QImageReader
            r = QImageReader(p)
            r.setAutoTransform(True)
            sz = r.size()
            if max_side and sz.isValid() and sz.width() > 0:
                k = max_side / float(max(sz.width(), sz.height()))
                r.setScaledSize(QSize(max(1, int(sz.width() * k)),
                                      max(1, int(sz.height() * k))))
            return r.read()

try:                                    # 跟主程序共用主题色；单独跑时用同值兜底
    from .. import theme as _T
    ACCENT = getattr(_T, "PRIMARY", "#7C3AED")
    ACCENT_L = getattr(_T, "PRIMARY_LIGHT", "#F5F0FF")
    OK_C = getattr(_T, "SUCCESS", "#16A34A")
    BAD_C = getattr(_T, "DANGER", "#DC2626")
    WARN_C = getattr(_T, "WARNING", "#D97706")
    BLUE_C = getattr(_T, "BLUE", "#2563EB")
    TXT = getattr(_T, "TEXT", "#1F2937")
    TXT_SUB = getattr(_T, "TEXT_SUB", "#6B7280")
    LINE = getattr(_T, "BORDER", "#E5E7EB")
    CARD_BG = getattr(_T, "CARD", "#FFFFFF")
except Exception:                        # noqa: BLE001
    ACCENT, ACCENT_L = "#7C3AED", "#F5F0FF"
    OK_C, BAD_C, WARN_C, BLUE_C = "#16A34A", "#DC2626", "#D97706", "#2563EB"
    TXT, TXT_SUB, LINE, CARD_BG = "#1F2937", "#6B7280", "#E5E7EB", "#FFFFFF"

PAGE_SIZE = 60          # 每页商品数（实测：60 张卡约 80 ms 建完）


class PurgeWorker(QThread):
    """彻底删除商品（P0-3：放后台，界面不假死）。

    以前 `_purge()` 在界面线程同步调 `v3_purge_products()`：
    删 1 件带 88 张图 / 61 个本地文件时要遍历磁盘 + 逐条删行，
    期间窗口完全不响应，主人以为「点了没反应」。

    ★ B-01（2026-10-25）：加取消通道 —— 界面点「⏹ 停止删除」→ bridge.cancel_fire(tag)
      → 后端在检查点收尾。**不杀线程**，所以不会留下半截事务。
    """
    done = pyqtSignal(dict)

    def __init__(self, pids: list, delete_files: bool = True, cancel_tag: str = ""):
        super().__init__()
        self.pids = [int(x) for x in (pids or [])]
        self.delete_files = bool(delete_files)
        self.cancel_tag = cancel_tag or ""

    def run(self):  # noqa: D102
        from .. import bridge as B
        r = {}
        try:
            if self.cancel_tag:
                B.cancel_begin(self.cancel_tag)
            r = B.v3_purge_products(self.pids, delete_files=self.delete_files,
                                    cancel_tag=self.cancel_tag)
        except Exception as e:                      # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            if self.cancel_tag:
                B.cancel_done(self.cancel_tag)
        self.done.emit(r or {"ok": False, "error": "无返回"})


class CleanupWorker(QThread):
    """清空回收站 + 清孤儿 OCR + 清孤儿文件（三步各自出真实数字）。

    ★ B-01：一个 tag 贯穿三步；取消后**停在当前那一步**，后面的步骤不再跑。
    """
    done = pyqtSignal(dict)

    def __init__(self, cancel_tag: str = ""):
        super().__init__()
        self.cancel_tag = cancel_tag or ""

    def run(self):  # noqa: D102
        from .. import bridge as B
        out = {"cancelled": False}
        tag = self.cancel_tag
        try:
            if tag:
                B.cancel_begin(tag)
            chk = (lambda: False)
            if tag:
                def chk():                                  # noqa: F811
                    ev = B._cancel_event(tag)                # noqa: SLF001
                    return bool(ev and ev.is_set())
            for k, fn in (("trash", lambda: B.v3_trash_clear(cancel_tag=tag)),
                          ("ocr", B.v3_orphan_ocr_clean),
                          ("files", lambda: B.v3_orphan_files_purge(cancel_tag=tag))):
                if chk():
                    out["cancelled"] = True
                    out.setdefault("stopped_at", k)
                    break
                try:
                    out[k] = fn()
                except Exception as e:                  # noqa: BLE001
                    out[k] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
                if isinstance(out.get(k), dict) and out[k].get("cancelled"):
                    out["cancelled"] = True
                    out.setdefault("stopped_at", k)
                    break
        finally:
            if tag:
                B.cancel_done(tag)
        self.done.emit(out)

QSS = """
QWidget#wallroot { background:#F7F8FA; color:%(TXT)s; font-size:12px; }
QTreeWidget, QTableView { background:%(CARD)s; border:1px solid %(LINE)s;
                          border-radius:6px; }
QTreeWidget::item { padding:4px 2px; }
QTreeWidget::item:selected, QTableView::item:selected {
        background:%(ACCENT_L)s; color:%(TXT)s; }
QTreeWidget::item:hover, QTableView::item:hover { background:#FAF7FF; }
QLineEdit, QComboBox { background:%(CARD)s; border:1px solid %(LINE)s;
        padding:4px 8px; border-radius:6px; min-height:22px; }
QLineEdit:focus, QComboBox:focus { border:1px solid %(ACCENT)s; }
QPushButton { background:%(CARD)s; border:1px solid %(LINE)s; padding:5px 12px;
        border-radius:6px; color:%(TXT)s; }
QPushButton:hover { border-color:%(ACCENT)s; color:%(ACCENT)s; }
QPushButton:checked { background:%(ACCENT_L)s; border-color:%(ACCENT)s;
        color:%(ACCENT)s; font-weight:600; }
QPushButton:disabled { color:#B6BCC6; border-color:%(LINE)s; background:#F3F4F6; }
QFrame#card { background:%(CARD)s; border:1px solid %(LINE)s; border-radius:8px; }
QFrame#card[sel="1"] { border:2px solid %(ACCENT)s; background:%(ACCENT_L)s; }
QFrame#pane { background:%(CARD)s; border:1px solid %(LINE)s; border-radius:8px; }
QLabel#title { color:%(TXT)s; font-weight:600; }
QLabel#meta  { color:%(TXT_SUB)s; }
QLabel#sect  { color:%(ACCENT)s; font-weight:700; font-size:12px; }
QLabel#price { color:%(BAD)s; font-size:15px; font-weight:700; }
QLabel#warn  { color:%(WARN)s; }
QLabel#ok    { color:%(OK)s; }
QScrollArea { border:0; background:#F7F8FA; }
QHeaderView::section { background:#F3F4F6; border:0; border-bottom:1px solid %(LINE)s;
        padding:5px; color:%(TXT_SUB)s; font-weight:600; }
QPlainTextEdit, QTextEdit { background:%(CARD)s; border:1px solid %(LINE)s;
        border-radius:6px; }
""" % {"TXT": TXT, "TXT_SUB": TXT_SUB, "LINE": LINE, "CARD": CARD_BG,
       "ACCENT": ACCENT, "ACCENT_L": ACCENT_L, "OK": OK_C, "BAD": BAD_C,
       "WARN": WARN_C}

ROLE_CN = {"main": "主图", "detail": "详情图", "sku": "SKU图", "video": "视频",
           "carousel": "轮播", "": "未分类"}
DL_CN = {"pending": ("待下载", WARN_C), "downloading": ("下载中", BLUE_C),
         "done": ("已下载", OK_C), "ok": ("已下载", OK_C),
         "failed": ("下载失败", BAD_C), "error": ("下载失败", BAD_C),
         "skipped": ("跳过", TXT_SUB), "": ("—", TXT_SUB)}


# ---------------------------------------------------------------- 缩略图
class _Sig(QObject):
    ready = pyqtSignal(int, QImage, int)      # pid, image, gen


def _fast_sig(path: str) -> str:
    """内容指纹：大小 + 头尾各 64KB 的 sha1。

    ★ 不只看 mtime —— 原图被「同尺寸同时间戳」替换过时 mtime 不变，
    只靠 mtime 的缓存键会拿旧图糊弄人（C1 验收点名要求）。
    """
    import hashlib
    try:
        n = os.path.getsize(path)
        h = hashlib.sha1(str(n).encode())
        with open(path, "rb") as f:
            h.update(f.read(65536))
            if n > 131072:
                f.seek(-65536, os.SEEK_END)
                h.update(f.read(65536))
        return h.hexdigest()[:12]
    except Exception:                       # noqa: BLE001
        try:
            return "%x" % int(os.path.getmtime(path))
        except Exception:                   # noqa: BLE001
            return "0"


class _Task(QRunnable):
    """后台生成缩略图：读盘 + 缩放，绝不占 UI 线程。"""

    def __init__(self, sig, cache_dir, pid, path, size, image_id=0, gen=0):
        super().__init__()
        self.sig, self.cache_dir, self.pid = sig, cache_dir, int(pid)
        self.path, self.size, self.image_id = path, int(size), int(image_id)
        self.gen = int(gen)

    def _cache_name(self) -> str:
        return "%d_%d_%s_%d.jpg" % (self.pid, self.image_id,
                                    _fast_sig(self.path), self.size)

    def _emit(self, img):
        """页面可能已被销毁（切走/关窗），此时 emit 会抛 RuntimeError —— 丢弃即可。"""
        try:
            self.sig.ready.emit(self.pid, img, self.gen)
        except RuntimeError:
            pass

    def run(self):                          # noqa: D102
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            cpath = os.path.join(self.cache_dir, self._cache_name())
            # ★ 2026-10-28 崩溃加固：原来 QImage(cpath) 与 rd.read() **都是全尺寸解码**，
            #   4 线程并发解大图最容易把 C 库堆写坏（事件日志 ntdll 0xc0000374）。
            #   现在统一走 imgcap：并发闸门 2 + 巨图拦截 + 解码时就缩到 self.size。
            img = imgcap.decode(cpath) if os.path.exists(cpath) else QImage()
            if img.isNull():
                img = imgcap.decode(self.path, max_side=self.size)
                if img.isNull():
                    if os.path.exists(cpath):
                        try:
                            os.remove(cpath)      # 坏缓存先删再建
                        except OSError:
                            pass
                    self._emit(QImage())
                    return
                img.save(cpath, "JPG", 88)
            self._emit(img)
        except Exception:                       # noqa: BLE001
            self._emit(QImage())


_PIX_CACHE: dict = {}
_PIX_MAX = 160


def _pix_cached(path, w, h):
    """详情卡解码缓存 —— 同一张图反复看（切商品/开独立窗口）不再重解码。

    ★ 实测：详情卡首次 516 ms，其中大头是把原图按 320px 解码。
    缓存键含 (路径, 尺寸, mtime, 文件大小)，换图必换键。
    """
    if not path or not os.path.exists(path):
        return placeholder(min(w, h), "无本地文件")
    try:
        stt = os.stat(path)
        key = (path, w, h, int(stt.st_mtime), stt.st_size)
    except OSError:
        return placeholder(min(w, h), "无本地文件")
    pm = _PIX_CACHE.get(key)
    if pm is not None:
        return pm
    # ★ 2026-10-28：统一走 imgcap（缩放解码 + 并发闸门 + 巨图拦截）
    im = imgcap.decode(path, max_side=max(int(w), int(h)))
    pm = QPixmap.fromImage(im) if not im.isNull() else placeholder(min(w, h), "无本地文件")
    if len(_PIX_CACHE) > _PIX_MAX:
        _PIX_CACHE.clear()
    _PIX_CACHE[key] = pm
    return pm


def placeholder(size=200, text="无本地图") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(QColor("#F3F4F6"))
    p = QPainter(pm)
    p.setPen(QColor("#C4CAD3"))
    p.drawRect(0, 0, size - 1, size - 1)
    p.setPen(QColor("#9AA3AF"))
    f = QFont(); f.setPointSize(9); p.setFont(f)
    p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), text)
    p.end()
    return pm


# ---------------------------------------------------------------- 卡片
class Card(QFrame):
    clicked = pyqtSignal(int, int)
    opened = pyqtSignal(int)

    def __init__(self, row: dict, size: int, has_local: bool = True):
        super().__init__()
        self.pid = int(row["product_id"])
        self.setObjectName("card")
        self.setProperty("sel", "0")
        self.setFixedWidth(size + 22)
        v = QVBoxLayout(self); v.setContentsMargins(5, 5, 5, 5); v.setSpacing(3)

        self.img = QLabel(); self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setFixedSize(size, size)
        self.img.setPixmap(placeholder(size))
        v.addWidget(self.img)

        t = QLabel(self._elide(row.get("title") or "(无标题)", int(size / 6.2)))
        t.setObjectName("title"); t.setToolTip(row.get("title") or "")
        t.setFixedWidth(size)
        v.addWidget(t)

        n_img = int(row.get("image_count") or 0)
        n_loc = int(row.get("local_count") or 0)
        m = QLabel("图 %d · 本地 %d" % (n_img, n_loc) if n_img else "无媒体引用")
        m.setObjectName("meta"); m.setFixedWidth(size)
        v.addWidget(m)

        # ★ 2026-10-25 主人要求：卡片上必须一眼看出「这条商品本来带视频 / 视频下来了没有」
        #   ★  = 来源给了视频，但还没落到本地（点下载才拿得到）
        #   ★★ = 视频已经下载到本地（可以直接看）
        #   没有视频的卡片不显示这一行，避免占位、避免误读成"零视频"是异常。
        n_vid = int(row.get("n_video") or 0)
        n_vid_loc = int(row.get("n_video_local") or 0)
        if n_vid:
            if n_vid_loc >= n_vid:
                star = QLabel("★★ 视频已下载 %d" % n_vid)
                star.setObjectName("ok")
                star.setToolTip("来源里有 %d 个视频，全部已下载到本地。双击商品可在详情里播放。"
                                % n_vid)
            elif n_vid_loc:
                star = QLabel("★☆ 视频 %d/%d" % (n_vid_loc, n_vid))
                star.setObjectName("warn")
                star.setToolTip("来源里有 %d 个视频，已下载 %d 个，还有 %d 个没下。"
                                % (n_vid, n_vid_loc, n_vid - n_vid_loc))
            else:
                star = QLabel("★ 视频未下载 %d" % n_vid)
                star.setObjectName("warn")
                star.setToolTip("来源里有 %d 个视频，一个都还没下载。\n"
                                "选中它（或选它所在的包）点「⬇ 下载本包媒体」就能拿到。"
                                % n_vid)
            star.setFixedWidth(size)
            v.addWidget(star)

        self.warn = QLabel("待下载" if (n_img and not n_loc) else "")
        if n_img and not n_loc:
            self.warn.setObjectName("warn")
        elif n_loc:
            self.warn.setText("本地就绪")
            self.warn.setObjectName("ok")
        self.warn.setFixedWidth(size)
        v.addWidget(self.warn)
        if not has_local:
            self.warn.setText("无本地图")
            self.warn.setObjectName("warn")

    @staticmethod
    def _elide(s, n):
        s = str(s or "")
        return s if len(s) <= n else s[:max(1, n - 1)] + "…"

    def set_selected(self, on: bool):
        self.setProperty("sel", "1" if on else "0")
        self.style().unpolish(self); self.style().polish(self)

    def set_pixmap(self, pm: QPixmap):
        if pm and not pm.isNull():
            self.img.setPixmap(pm)

    def mousePressEvent(self, e):           # noqa: N802
        self.clicked.emit(self.pid, int(e.modifiers().value))
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):     # noqa: N802
        self.opened.emit(self.pid)
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------- 列表模型
class TableModel(QAbstractTableModel):
    """用模型代替 QTableWidget：504 行也不建控件、不量列宽。"""

    COLS = ["ID", "标题", "价格", "平台", "包", "图", "本地", "OCR", "视频", "状态"]

    def __init__(self, rows=None):
        super().__init__()
        self.rows = rows or []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows or []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):        # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):     # noqa: N802
        return 0 if parent.isValid() else len(self.COLS)

    def headerData(self, sec, orient, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.COLS[sec] if orient == Qt.Orientation.Horizontal else sec + 1

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):          # noqa: N802
        if not idx.isValid() or idx.row() >= len(self.rows):
            return None
        r = self.rows[idx.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            c = idx.column()
            if c == 0:
                return str(r.get("product_id"))
            if c == 1:
                return (r.get("title") or "")[:70]
            if c == 2:
                v = r.get("price")
                return ("¥%s" % v) if v else ""
            if c == 3:
                return r.get("platform") or ""
            if c == 4:
                return r.get("pack_code") or ""
            if c == 5:
                return str(r.get("image_count") or 0)
            if c == 6:
                return str(r.get("local_count") or 0)
            if c == 7:
                return str(r.get("ocr_ok") or 0)
            if c == 8:
                # ★ 视频列：空=没有视频；★ N=有 N 个未下；★★ N=全下好
                nv = int(r.get("n_video") or 0)
                nl = int(r.get("n_video_local") or 0)
                if not nv:
                    return ""
                return ("★★ %d" % nv) if nl >= nv else (
                    ("★☆ %d/%d" % (nl, nv)) if nl else ("★ %d" % nv))
            if c == 9:
                return STATUS_LABEL.get(r.get("status") or "", r.get("status") or "未判")
        return None


# ---------------------------------------------------------------- 详情卡
class DetailCard(QWidget):
    """纵向商品卡：从上到下滚动看完整资料。数据全部来自 Library.detail()。"""

    def __init__(self, parent=None, wide: bool = False):
        super().__init__(parent)
        self.wide = wide
        self.pid = 0
        self.setObjectName("detailcard")
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.host = QWidget(); self.host.setObjectName("wallroot")
        self.v = QVBoxLayout(self.host)
        self.v.setContentsMargins(12, 12, 12, 12); self.v.setSpacing(8)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll)
        self._clear("未选中商品")

    # -- 基础
    def _clear(self, msg="未选中商品"):
        while self.v.count():
            it = self.v.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None); w.deleteLater()
        lb = QLabel(msg); lb.setObjectName("meta")
        lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.v.addWidget(lb); self.v.addStretch(1)

    def _sect(self, text, color=None):
        lb = QLabel(text)
        lb.setObjectName("sect")
        if color:
            lb.setStyleSheet("color:%s; font-weight:700;" % color)
        self.v.addWidget(lb)
        return lb

    def _kv(self, k, v, color=None):
        """一行 键: 值。v 为空时显示「来源未提供」。"""
        w = QWidget(); h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        a = QLabel(k); a.setObjectName("meta"); a.setFixedWidth(64)
        b = QLabel(str(v) if (v not in (None, "", [])) else "来源未提供")
        b.setWordWrap(True)
        if v in (None, "", []):
            b.setObjectName("meta")
        elif color:
            b.setStyleSheet("color:%s;" % color)
        h.addWidget(a, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(b, 1)
        self.v.addWidget(w)
        return b

    def _pix(self, path, w, h=None):
        lb = QLabel(); lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h = h or w
        lb.setFixedSize(w, h)
        lb.setPixmap(_pix_cached(path, w, h))
        return lb

    # -- 媒体行（2026-10-25 阶段一：视频从「一行字」变成能播、能定位）
    @staticmethod
    def _short(path: str) -> str:
        try:
            base = r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace"
            return path[len(base) + 1:] if path.startswith(base) else path
        except Exception:                      # noqa: BLE001
            return path

    def _video_row(self, i: dict):
        """一条视频：文件名 + 大小 + ▶播放 / 📂定位 + 本地路径（或待下载原因）。

        原来这里只有一行「<URL>（已下载）」纯文字 —— 用户既播不了、也不知道
        文件落在哪个盘。现在给动作按钮，路径直接可见；没有文件时说明原因。
        """
        path = i.get("abs") or i.get("current_local_path") or ""
        has = bool(path) and os.path.isfile(path)
        sz = ""
        if has:
            try:
                n = os.path.getsize(path)
                sz = ("%.1f MB" % (n / 1048576.0)) if n >= 1048576 else ("%d KB" % (n // 1024))
            except OSError:
                sz = ""
        box = QWidget(); v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 6); v.setSpacing(2)
        head = QHBoxLayout(); head.setSpacing(6)
        nm = QLabel(os.path.basename(path) if has else "（本地无文件）")
        nm.setObjectName("meta"); head.addWidget(nm, 0)
        if sz:
            q = QLabel(sz); q.setObjectName("meta"); head.addWidget(q, 0)
        head.addStretch(1)
        b1 = QPushButton("▶ 播放"); b1.setFixedHeight(22); b1.setEnabled(has)
        b1.clicked.connect(lambda _=False, p=path: self._play_media(p))
        b2 = QPushButton("📂 定位文件"); b2.setFixedHeight(22); b2.setEnabled(has)
        b2.clicked.connect(lambda _=False, p=path: self._reveal(p))
        head.addWidget(b1); head.addWidget(b2)
        v.addLayout(head)
        if has:
            pl = QLabel(self._short(path)); pl.setObjectName("meta")
            pl.setWordWrap(True); v.addWidget(pl)
        else:
            lb = QLabel("未下载 · 原始地址：%s"
                        % ((i.get("original_url") or "")[:110] or "来源未提供"))
            lb.setObjectName("meta"); lb.setWordWrap(True); v.addWidget(lb)
        self.v.addWidget(box)

    def _play_media(self, path: str):
        if not path or not os.path.isfile(path):
            return
        try:
            os.startfile(path)                 # noqa: S606  交给系统默认播放器
        except OSError as e:
            QMessageBox.warning(self, "打不开", "系统里没有能播放这个格式的程序：\n%s" % e)

    def _reveal(self, path: str):
        if not path:
            return
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except OSError:
            pass

    def _open_img_dir(self):
        d = getattr(self, "_img_dir", "")
        if not d or not os.path.isdir(d):
            QMessageBox.information(self, "没有目录", "这件商品还没有本地图片目录。")
            return
        try:
            import subprocess
            subprocess.Popen(["explorer", os.path.normpath(d)])
        except OSError:
            pass

    def _copy_paths(self, group, role_cn: str = ""):
        """把某一组（主图/详情图/SKU图）的本地文件路径复制到剪贴板。

        2026-10-26 新增：三种角色的图都落在同一个 <商品ID> 目录里、文件名是
        image_<hash>.jpg，从文件名看不出哪张是 SKU 图 —— 所以给每组一个
        「📋 路径」，把这一组的绝对路径一次性复制出去对照查看。
        """
        paths = [(i.get("abs") or "") for i in (group or []) if (i.get("abs") or "")]
        if not paths:
            QMessageBox.information(self, "没有路径", "这一组没有本地文件路径。")
            return
        QApplication.clipboard().setText("\n".join(paths))
        QMessageBox.information(
            self, "已复制",
            "已复制 %d 条%s路径到剪贴板。\n\n第一条：\n%s" % (
                len(paths), role_cn or "", paths[0]))

    # -- 主体
    def show_product(self, d: dict):
        self._clear()
        if not d or not d.get("product"):
            self._clear("该商品不在库中")
            return
        p = d["product"]
        imgs = d.get("images") or []
        ocrs = d.get("ocr") or []
        self.pid = int(p.get("product_id") or 0)
        # 图片目录：取第一个有本地文件的媒体所在目录（「📂 打开图片目录」用）
        self._img_dir = ""
        for _i in (imgs or []):
            _a = _i.get("abs") or ""
            if _a and os.path.isfile(_a):
                self._img_dir = os.path.dirname(_a)
                break

        mains = [i for i in imgs if i.get("role") == "main"]
        hero = next((i for i in mains if i.get("has_file")), None) or \
               next((i for i in imgs if i.get("has_file")), None)
        hw = 320 if not self.wide else 420
        self.v.addWidget(self._pix((hero or {}).get("abs"), hw, int(hw * 0.78)),
                         0, Qt.AlignmentFlag.AlignHCenter)

        title = QLabel(p.get("original_title") or "(无标题)")
        title.setObjectName("title"); title.setWordWrap(True)
        f = QFont(); f.setPointSize(11); f.setBold(True); title.setFont(f)
        self.v.addWidget(title)

        price = p.get("price_min")
        if price:
            pl = QLabel("¥%s" % price); pl.setObjectName("price")
            self.v.addWidget(pl)

        self._sect("基本信息")
        self._kv("商品 ID", p.get("product_id"))
        self._kv("源商品ID", p.get("source_product_id"))
        self._kv("平台", p.get("source_platform"))
        self._kv("来源包", p.get("pack_code"))
        self._kv("来源链接", p.get("source_url"))
        self._kv("来源店铺", p.get("source_shop_name"))
        self._kv("店铺类型", (p.get("profile") or "") if isinstance(p.get("profile"), str) else "")
        self._kv("价格", ("¥%s ~ ¥%s" % (p.get("price_min"), p.get("price_max")))
                 if p.get("price_max") and p.get("price_max") != p.get("price_min")
                 else (("¥%s" % price) if price else None))
        self._kv("类目", p.get("category_path"))
        self._kv("类目ID", p.get("category_id"))
        self._kv("精筛状态", STATUS_LABEL.get(p.get("status") or "", "未判"))
        self._kv("数据等级", p.get("data_grade"))
        self._kv("采集时间", p.get("created_at"))

        # 属性 / SKU
        attrs = {}
        raw = p.get("attributes")
        if raw:
            try:
                attrs = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:               # noqa: BLE001
                attrs = {}
        skus = []
        if isinstance(attrs, dict):
            for k in ("sku", "skus", "sku_list", "SkuList"):
                if isinstance(attrs.get(k), list):
                    skus = attrs[k]; break
        plain = {k: v for k, v in (attrs or {}).items()
                 if k not in ("sku", "skus", "sku_list", "SkuList")} \
            if isinstance(attrs, dict) else {}

        self._sect("SKU 与规格")
        # 2026-10-24：属性/规格补齐后（server\v3\enrich.py），真卡里的 SKU 在
        # sale_attrs（规格维度 → 每个值挂在哪些 sku_id）+ sku_spec（sku_id → "颜色:…; 尺码:…"）
        # 里，不再是老的 skus 列表。两种形状都认，旧数据照旧能显示。
        sale = attrs.get("sale_attrs") if isinstance(attrs, dict) else None
        spec_map = attrs.get("sku_spec") if isinstance(attrs, dict) else None
        shown = False
        if isinstance(spec_map, dict) and spec_map:
            lb = QLabel("规格组合 %d 个 SKU（来自原始卡 saleAttrs × skuIdList）"
                        % len(spec_map))
            lb.setObjectName("meta"); lb.setWordWrap(True)
            self.v.addWidget(lb)
            for sid, sp in list(spec_map.items())[:25]:
                q = QLabel("• %s　%s" % (str(sid)[:16], str(sp)[:90]))
                q.setWordWrap(True); q.setObjectName("meta")
                self.v.addWidget(q)
            if len(spec_map) > 25:
                lb = QLabel("…共 %d 个 SKU 规格组合" % len(spec_map))
                lb.setObjectName("meta"); self.v.addWidget(lb)
            shown = True
        if isinstance(sale, list) and sale:
            for dim in sale[:6]:
                if not isinstance(dim, dict):
                    continue
                vals = [str(x.get("value")) for x in (dim.get("values") or [])
                        if isinstance(x, dict) and x.get("value")]
                if not vals:
                    continue
                line = "%s（%d）：%s" % (dim.get("key") or "规格", len(vals),
                                        " / ".join(vals[:6]) + (" …" if len(vals) > 6 else ""))
                q = QLabel("· " + line); q.setWordWrap(True); q.setObjectName("meta")
                self.v.addWidget(q)
            shown = True
        if not shown and skus:
            for s in skus[:20]:
                if isinstance(s, dict):
                    txt = s.get("name") or s.get("sku_name") or s.get("spec") or ""
                    pz = s.get("price") or s.get("price_yuan") or ""
                    st = s.get("stock") or s.get("quantity") or ""
                    line = " · ".join(x for x in (str(txt), ("¥%s" % pz) if pz else "",
                                                  ("库存 %s" % st) if st != "" else "") if x)
                    q = QLabel("• " + (line or json.dumps(s, ensure_ascii=False)[:80]))
                    q.setWordWrap(True); self.v.addWidget(q)
            if len(skus) > 20:
                lb = QLabel("…共 %d 条 SKU" % len(skus)); lb.setObjectName("meta")
                self.v.addWidget(lb)
            shown = True
        if not shown:
            lb = QLabel("来源未提供 SKU 明细"); lb.setObjectName("meta")
            self.v.addWidget(lb)

        self._sect("商品属性")
        param = attrs.get("param") if isinstance(attrs, dict) else None
        stock = attrs.get("stock") if isinstance(attrs, dict) else None
        if isinstance(stock, dict) and stock:
            self._kv("库存/已售", "库存 %s · 已售 %s · 锁定 %s"
                     % (stock.get("stockNum", "-"), stock.get("soldStock", "-"),
                        stock.get("lockStock", "-")))
        if isinstance(param, dict) and param:
            for k, v in list(param.items())[:30]:
                self._kv(str(k)[:12], v)
        # 除上面已消费的结构键外，其余平铺键照旧显示（兼容旧数据与以后新加的）
        used = {"sku", "skus", "sku_list", "SkuList", "sale_attrs", "sku_spec",
                "param", "stock", "sku_ids", "_source", "_note", "_paths", "_counts",
                "_manual", "sale_attrs_text"}
        rest = {k: v for k, v in (plain or {}).items() if k not in used}
        for k, v in list(rest.items())[:30]:
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)[:90]
            self._kv(str(k)[:12], v)
        if not (param or rest or (isinstance(stock, dict) and stock)):
            lb = QLabel("来源未提供属性" if not attrs else
                        "属性已解析但源里没有键值对（param 为空）")
            lb.setObjectName("meta"); self.v.addWidget(lb)

        # 描述
        self._sect("商品描述")
        desc = ""
        for k in ("desc", "description", "描述", "desc_text"):
            if isinstance(plain, dict) and plain.get(k):
                desc = plain[k]; break
        if not desc and isinstance(attrs, dict):
            desc = attrs.get("desc") or attrs.get("description") or ""
        tb = QTextEdit(); tb.setReadOnly(True); tb.setFixedHeight(90)
        tb.setPlainText(str(desc) if desc else "来源未提供描述")
        self.v.addWidget(tb)

        # 图片
        need = [i for i in imgs if not i.get("has_file") and i.get("original_url")]
        self._sect("图片与视频")
        cnt = {}
        for i in imgs:
            cnt[i.get("role") or ""] = cnt.get(i.get("role") or "", 0) + 1
        self._kv("媒体引用", "%d 条（%s）" % (len(imgs), " · ".join(
            "%s %d" % (ROLE_CN.get(k, k or "未分类"), v) for k, v in cnt.items()) or "无"))
        if need:
            lb = QLabel("⚠ %d 条只有 URL、还没有本地文件（待下载）" % len(need))
            lb.setObjectName("warn"); lb.setWordWrap(True); self.v.addWidget(lb)

        have = [i for i in imgs if i.get("has_file") and i.get("role") != "video"]
        if have:
            # 2026-10-25 阶段一：原来固定只显示前 12 张，一件有 34 张详情图时
            # 用户根本看不到后面那些。现在按角色分组，并如实报「共 N 张 · 显示前 12 张」，
            # 外加「📂 打开图片目录」——图片到底落在哪个盘上一眼可查。
            by_role = {}
            for i in have:
                by_role.setdefault(i.get("role") or "?", []).append(i)
            order = [r for r in ("main", "detail", "sku") if r in by_role]
            order += [r for r in by_role if r not in order]
            # 2026-10-26：原来是「全局最多 12 张」，detail 一多就把 sku 组整组挤成一句灰字，
            # 用户看到「只有详情图、看不到 SKU 图」。改成每组各留 8 张，每组都能显示，
            # 并且每组自带「📂 定位」「📋 路径」——SKU 图在哪个目录、有哪些文件，当场可查。
            PER_GROUP = 8
            for role in order:
                group = sorted(by_role[role], key=lambda x: (x.get("sequence") or 0))
                take = group[:PER_GROUP]
                rcn = ROLE_CN.get(role, role or "未分类")
                hdr = QWidget(); hl = QHBoxLayout(hdr)
                hl.setContentsMargins(0, 6, 0, 0); hl.setSpacing(4)
                head = QLabel("%s · 共 %d 张%s" % (
                    rcn, len(group),
                    ("（显示前 %d 张）" % len(take)) if len(take) < len(group) else ""))
                head.setObjectName("meta")
                hl.addWidget(head, 1)
                b_rev = QPushButton("📂 定位")
                b_rev.setFixedHeight(20)
                b_rev.setToolTip("在资源管理器里选中本组第 1 张（其余同一目录）")
                b_rev.clicked.connect(
                    lambda _=False, grp=group: self._reveal((grp[0] or {}).get("abs") or ""))
                hl.addWidget(b_rev)
                b_cp = QPushButton("📋 路径")
                b_cp.setFixedHeight(20)
                b_cp.setToolTip("把本组全部文件路径复制到剪贴板")
                b_cp.clicked.connect(
                    lambda _=False, grp=group, nm=rcn: self._copy_paths(grp, nm))
                hl.addWidget(b_cp)
                self.v.addWidget(hdr)
                grid = QWidget(); g = QGridLayout(grid)
                g.setContentsMargins(0, 0, 0, 0); g.setSpacing(4)
                for n, i in enumerate(take):
                    cell = QVBoxLayout()
                    cell.addWidget(self._pix(i.get("abs"), 96, 96))
                    seq = i.get("sequence")
                    cap = QLabel(("#%s " % (seq + 1)) if isinstance(seq, int) else "")
                    cap.setObjectName("meta")
                    cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    cell.addWidget(cap)
                    w = QWidget(); w.setLayout(cell)
                    g.addWidget(w, n // 6, n % 6)
                self.v.addWidget(grid)
            btn_dir = QPushButton("📂 打开图片目录")
            btn_dir.setFixedHeight(22)
            btn_dir.clicked.connect(lambda _=False: self._open_img_dir())
            self.v.addWidget(btn_dir)
        else:
            lb = QLabel("本地没有任何已下载的图片"); lb.setObjectName("meta")
            lb.setWordWrap(True); self.v.addWidget(lb)

        vids = [i for i in imgs if (i.get("role") == "video" or
                                    (i.get("original_url") or "").lower().endswith(".mp4"))]
        if vids:
            self._sect("视频（%d 个）" % len(vids))
            for i in sorted(vids, key=lambda x: (x.get("sequence") or 0)):
                self._video_row(i)

        # OCR
        self._sect("OCR 文字证据")
        if not ocrs:
            lb = QLabel("未执行 OCR（本轮未对 176 件跑识别）"); lb.setObjectName("meta")
            lb.setWordWrap(True); self.v.addWidget(lb)
        else:
            ok = [o for o in ocrs if o.get("state") == "ok"]
            boxed = [o for o in ok if o.get("has_boxes")]
            self._kv("识别", "%d 条记录，成功 %d，带坐标 %d" % (len(ocrs), len(ok), len(boxed)))
            for o in ok[:3]:
                txt = (o.get("full_text") or "").strip().replace("\n", " ")
                if txt:
                    q = QLabel("• 图 %s：%s" % (o.get("image_id"), txt[:110]))
                    q.setWordWrap(True); self.v.addWidget(q)
            if ok and not boxed:
                lb = QLabel("这些 OCR 没有坐标，无法在图上定位高亮")
                lb.setObjectName("meta"); lb.setWordWrap(True); self.v.addWidget(lb)

        # 下载状态
        self._sect("资源下载状态")
        st = {}
        for i in imgs:
            k = (i.get("dl_state") or "").lower()
            if i.get("has_file"):
                k = "done"
            st[k or "pending"] = st.get(k or "pending", 0) + 1
        for k, n in sorted(st.items(), key=lambda x: -x[1]):
            cn, color = DL_CN.get(k, (k or "—", TXT_SUB))
            self._kv(cn, "%d 条" % n, color)

        self._sect("原始来源")
        self._kv("原始 JSON", ("已归档 %d 字节" % p.get("payload_bytes"))
                 if p.get("payload_bytes") else "库中无原文")
        self._kv("来源说明", p.get("provenance"))
        lb = QLabel("提示：本页的商品详情只读；批量下载 / 补跑 OCR / 移出包 / 彻底删除 / "
                    "清理回收站在上面那排按钮里，都走真后端。")
        lb.setObjectName("meta"); lb.setWordWrap(True)
        self.v.addWidget(lb)
        self.v.addStretch(1)


# ---------------------------------------------------------------- 审核进度线程
class _RevWorker(QThread):
    """主图审核进度查询（只读 5 条 SQL），跑在后台线程。

    T1-01（2026-10-25）：原来 `_refresh_review()` 在 GUI 线程里直接调
    `bridge.v3_review_progress()`，而 bridge 每次 `_wrap()` 都会
    `flow.conn() → store.init()`（建连 5ms + init 75ms，含一次 WAL 提交）。
    实测这一步占了页内 F5 的 215–248ms —— 几乎全部。现在放进后台线程，
    并且带「代数」保护：更晚发起的请求才算数，旧结果直接丢。
    """

    done = pyqtSignal(int, dict)

    def __init__(self, seq: int, pack: str, parent=None):
        super().__init__(parent)
        self.seq = int(seq)
        self.pack = pack or ""

    def run(self):                             # noqa: D102
        try:
            from .. import bridge as B          # noqa: PLC0415
            r = B.v3_review_progress(self.pack)
        except Exception as e:                  # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if not isinstance(r, dict):
            r = {"ok": False, "error": "后端返回了 %s" % type(r).__name__}
        self.done.emit(self.seq, r)


# ---------------------------------------------------------------- 下载线程
class _DlWorker(QThread):
    """按商品批量下载媒体 / 补跑 OCR。跑在后台线程，界面不冻结。

    走的是 bridge.v3_media_download → server\\v3\\media.py，与"导入即下载"**同一套代码**，
    所以幂等、去重、过期判定、失败原因都一致。
    """

    tick = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, pids: list, what: str = "下载", ocr: bool = True, parent=None):
        super().__init__(parent)
        self.pids = [int(x) for x in pids]
        self.what = what
        self.ocr = ocr

    def run(self):  # noqa: D102
        try:
            from .. import bridge as B
            self.tick.emit("%s：%d 件，正在下载 ……" % (self.what, len(self.pids)))
            r = B.v3_media_download(pids=self.pids, workers=4, ocr_after=self.ocr)
            self.done.emit(r or {"ok": False, "error": "后端没有返回"})
        except Exception as e:          # noqa: BLE001
            self.done.emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


def _mb(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    if n > 1024 * 1024:
        return "%.1f MB" % (n / 1048576)
    if n > 1024:
        return "%.0f KB" % (n / 1024)
    return "%.0f B" % n


# ---------------------------------------------------------------- 主页面
class WallPage(QWidget):
    """商品库：左树 / 中照片墙 / 右详情卡。只读。"""

    def __init__(self, db_path: str = "", thumb_dir: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("wallroot")
        self.lib = Library(db_path)
        self.thumb_dir = thumb_dir or os.path.join(PKG_ROOT, "_stageB", "thumbs")
        self.thumb_size = 180
        self.rows: list[dict] = []
        self.by_pid: dict[int, dict] = {}
        self.cards: dict[int, Card] = {}
        self.selected: set[int] = set()
        self.anchor = -1
        self._gen = 0                 # 代数：换页/切包后旧缩略图任务作废
        self._shown = 0               # 已建卡数量（分页游标）
        self._local: dict[int, tuple] = {}
        self._last_ms = {}
        # 主图审核进度的后台查询状态（T1-01）：代数 / 在途线程 / 排队标记
        self._rev_seq = 0
        self._rev_worker = None
        self._rev_pending = False
        self._rev_workers = set()      # 所有在途线程：关页面时逐个等，避免销毁时还在跑
        self.pool = QThreadPool(); self.pool.setMaxThreadCount(4)
        self.dl_worker = None          # ★ 下载/补跑 OCR 的线程（同一时间只跑一个）
        self.sig = _Sig(); self.sig.ready.connect(self._on_thumb)
        self.thumb_done = 0
        self.thumb_expected = 0
        self.thumb_first_at = None
        self.setStyleSheet(QSS)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._build()
        self.reload()

    # ---------------- UI
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(8, 8, 8, 8); root.setSpacing(6)

        bar = QHBoxLayout(); bar.setSpacing(6)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索标题 / 商品 ID（回车）")
        self.search.setFixedWidth(230)
        self.search.returnPressed.connect(lambda: self.reload())
        self.sort = QComboBox(); self.sort.addItems(
            ["本地图多→少", "图片多→少", "图片少→多", "价格高→低", "价格低→高", "最新", "标题"])
        self.sort.currentTextChanged.connect(lambda *_: self.reload())
        self.btn_wall = QPushButton("▦ 照片墙"); self.btn_table = QPushButton("▤ 列表")
        for b in (self.btn_wall, self.btn_table):
            b.setCheckable(True)
        self.btn_wall.setChecked(True)
        self.btn_wall.clicked.connect(lambda: self._set_view(0))
        self.btn_table.clicked.connect(lambda: self._set_view(1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(110, 260); self.slider.setValue(180); self.slider.setFixedWidth(100)
        self.slider.valueChanged.connect(self._on_size)
        bar.addWidget(self.search)
        bar.addWidget(QLabel("排序")); bar.addWidget(self.sort)
        bar.addSpacing(6); bar.addWidget(self.btn_wall); bar.addWidget(self.btn_table)
        bar.addWidget(QLabel("缩略图")); bar.addWidget(self.slider)
        bar.addStretch(1)
        self.lb_count = QLabel("—"); self.lb_count.setObjectName("meta")
        bar.addWidget(self.lb_count)
        root.addLayout(bar)

        bar2 = QHBoxLayout(); bar2.setSpacing(6)
        self.btns = {}
        # 已实现（本页内真能用）
        for name, tip, slot in (
                ("刷新 F5", "重新读库（只读当前筛选）", lambda: self.reload()),
                ("并排对比", "2–4 件并排看大图", self._light_table),
                ("打开证据目录", "在资源管理器里打开本地图片目录", self._open_dir),
                ("原始 JSON", "查看这件商品的原始 payload", self._show_raw)):
            b = QPushButton(name); b.setToolTip(tip); b.clicked.connect(slot)
            self.btns[name] = b; bar2.addWidget(b)
        # ★ 2026-10-24 主人报「2.5 里全是标题、没有开始下载的按钮」→ 这两个按钮改成真能用。
        #   口径：**按当前筛选范围**（左树选的那个包）或**选中的几件**去下，不偷偷跑全库。
        # ★ 同日第二批：主人口径「删除就是彻底删除」→ 「移出当前包」与「彻底删除选中」
        #   都接真后端（server\v3\purge.py）：
        #     移出当前包 = 只清归属，商品/图/OCR/文件全留
        #     彻底删除   = 删行 + 删本地文件（共享文件不删），**不进回收站**
        for name, tip, slot in (
                ("⬇ 下载本包媒体", "把当前范围（或选中的几件）的主图 / 详情图 / SKU图 / 视频全部下载，\n"
                                    "下完立刻 OCR。同一 URL 只下一份，失败的留下原因可重试", self._download),
                ("🔤 补跑 OCR", "已下载但还没读字的图补读一遍（读过的不会重读）", self._ocr),
                ("📋 下载任务/日志", "打开下载任务中心：每个文件一行（含视频真实进度）、\n"
                                    "可暂停/继续/停止，关掉窗口下载仍在后台继续", self._open_center),
                ("↩ 移出当前包", "只把这批商品从包里摘出来：商品、图片、OCR、本地文件全部保留，\n"
                                 "随时可以再打包进去", self._detach),
                ("🗑 彻底删除", "**永久删除**：删商品 + 媒体引用 + OCR + 本地文件。\n"
                                "删除前先给你看影响清单（几件/几图/几条 OCR/几个文件/多少 MB）。\n"
                                "同一份文件若还被别的商品引用，文件保留不删", self._purge),
                ("🧹 清理回收站与孤儿", "清空回收站（清了不能还原）+ 删除没有图片对应的 OCR 行 +\n"
                                       "删除媒体目录里没人引用的文件（先出清单再删）", self._cleanup),
                ("⏹ 停止删除", "给正在跑的「彻底删除 / 清理」叫停：\n"
                               "· 数据库阶段叫停 = 整体回滚，一件都不删\n"
                               "· 文件阶段叫停 = 停止继续删，已删的不回来（会如实报数字）\n"
                               "· 批量删包 = 按包提交，已删的包不回滚", self._stop_delete)):
            b = QPushButton(name)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.btns[name] = b
            bar2.addWidget(b)
        self.btns["⏹ 停止删除"].setEnabled(False)      # 只有任务在跑时才亮
        bar2.addStretch(1)
        self.lb_sel = QLabel("选中 0 件"); self.lb_sel.setObjectName("meta")
        bar2.addWidget(self.lb_sel)
        root.addLayout(bar2)

        # ★ 2026-10-24 新增：4.1 主图审核进度条（对接 4.1 的「进程条 + Table 页」）
        #   口径与 4.1 完全一致：审核记录 = v3_image_edit 里该图有一条 status='confirmed'
        #   （「确认原图」与「修补后确认」都写这张表，所以两边数字必然一致）。
        #   点按钮跳到 4.1 页签，按当前包筛选后人工审。
        bar3 = QHBoxLayout(); bar3.setSpacing(6)
        lb_t = QLabel("4.1 主图审核"); lb_t.setObjectName("meta")
        bar3.addWidget(lb_t)
        self.pb_review = QProgressBar()
        self.pb_review.setRange(0, 100)
        self.pb_review.setFixedWidth(170)
        self.pb_review.setFormat("%v%")
        self.pb_review.setToolTip("主图审核进度：已审 / 主图总数（来源：4.1 的审核记录表）")
        bar3.addWidget(self.pb_review)
        self.lb_review = QLabel("—"); self.lb_review.setObjectName("meta")
        bar3.addWidget(self.lb_review)
        self.btn_review = QPushButton("去 4.1 审核 →")
        self.btn_review.setToolTip("跳到 4.1 主图人工审核页签（那边审完，这里立刻跟着变）")
        self.btn_review.clicked.connect(self._goto_review)
        bar3.addWidget(self.btn_review)
        bar3.addStretch(1)
        root.addLayout(bar3)

        QTimer.singleShot(0, self._refresh_review)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["筛选", "数量"])
        self.tree.setMinimumWidth(180)
        self.tree.itemClicked.connect(self._on_tree)
        self.split.addWidget(self.tree)

        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.host = QWidget(); self.host.setObjectName("wallroot")
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(6, 6, 6, 6); self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.host)
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.stack.addWidget(self.scroll)

        self.model = TableModel([])
        self.table = QTableView(); self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        # ★ 不用 ResizeToContents：它要逐格量宽，504×9 实测把「切列表」拖到 783 ms。
        #   固定列宽 + 可拖，肉眼无差别，切视图降到几十毫秒。
        for c, w in ((0, 64), (2, 78), (3, 62), (4, 150), (5, 52), (6, 52), (7, 52), (8, 64)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        self.table.selectionModel().selectionChanged.connect(self._table_sel)
        self.table.doubleClicked.connect(
            lambda idx: self._show_detail(int(self.model.rows[idx.row()]["product_id"])))
        self.stack.addWidget(self.table)
        cl.addWidget(self.stack)
        self.more = QPushButton("载入更多…")
        self.more.clicked.connect(lambda: self._load_more())
        self.more.setVisible(False)
        cl.addWidget(self.more)
        self.split.addWidget(center)

        right = QFrame(); right.setObjectName("pane")
        rv = QVBoxLayout(right); rv.setContentsMargins(6, 6, 6, 6); rv.setSpacing(4)
        rh = QHBoxLayout()
        self.lb_side_title = QLabel("商品详情"); self.lb_side_title.setObjectName("title")
        rh.addWidget(self.lb_side_title); rh.addStretch(1)
        btn_win = QToolButton(); btn_win.setText("⤢ 独立窗口")
        btn_win.setToolTip("在独立窗口里全宽查看这件商品")
        btn_win.clicked.connect(self._open_detail_window)
        rh.addWidget(btn_win)
        rv.addLayout(rh)
        self.detail = DetailCard()
        rv.addWidget(self.detail, 1)
        self.split.addWidget(right)
        self.split.setChildrenCollapsible(False)
        self.split.setSizes([215, 760, 430])
        root.addWidget(self.split, 1)

        self.status = QLabel(""); self.status.setObjectName("meta")
        root.addWidget(self.status)

    # ---------------- 数据
    def reload(self, reset=True):
        """重查当前筛选条件下的商品（只取当前页，不再全量重扫）。"""
        t0 = time.perf_counter()
        kind = key = ""
        it = self.tree.currentItem()
        if it and it.parent() is not None:
            kind, key = it.parent().text(0), it.text(0).split("  (")[0]
        self.rows = self.lib.products(kind, key, self.search.text().strip(),
                                      self.sort.currentText())
        self.by_pid = {int(r["product_id"]): r for r in self.rows}
        self.selected.clear(); self.anchor = -1
        self._local = self.lib.local_images_map([int(r["product_id"]) for r in self.rows])
        self.model.set_rows(self.rows)
        self._fill_tree()
        self._reset_cards()
        self._update_sel()
        s = self.lib.stats()
        self.status.setText(
            "库内 %d 件 · 图 %d（本地 %d）· OCR %d（带坐标 %d）· 成员 %d（悬空 %d）"
            "　｜　本次查询 %.0f ms"
            % (s["products"], s["images"], s["local_files"], s["ocr_ok"],
               s["ocr_boxes"], s["members"], s["dangling"],
               (time.perf_counter() - t0) * 1000))
        self.lb_count.setText("当前 %d 件 · 已显示 %d" % (len(self.rows), self._shown))
        self._last_ms["reload"] = (time.perf_counter() - t0) * 1000
        self._refresh_review()

    # ---------------- 4.1 审核进度（与 4.1 同一口径，只读）
    def _cur_pack(self) -> str:
        """当前左树选中的「包」，没选就是空（= 全库）。"""
        try:
            it = self.tree.currentItem()
            if it and it.parent() is not None and it.parent().text(0) == "包":
                return it.text(0).split("  (")[0]
        except Exception:            # noqa: BLE001
            pass
        return ""

    def _refresh_review(self):
        """刷新「4.1 主图审核进度」—— 后台查、只更新最新的那一次结果。

        T1-01：原来这里同步调 bridge，实测 215–248ms 全部压在 GUI 线程上。
        现在：
        · 发起时代数 +1；在途时不并发，只记一笔 pending，等回来再跑一次（
          连续按 F5 不会堆线程，也不会漏掉最后一次）
        · 回调时 seq 与当前代数不符 = 过期结果，**只丢弃，不写界面**
        · 控件更新仍然只在 GUI 线程（信号回主线程）
        """
        if not hasattr(self, "pb_review"):
            return
        self._rev_seq += 1
        if self._rev_worker is not None:          # 已有在途 → 排队一次即可
            self._rev_pending = True
            return
        self._start_rev(self._rev_seq, self._cur_pack())

    def _start_rev(self, seq: int, pack: str):
        w = _RevWorker(seq, pack, self)
        self._rev_worker = w
        self._rev_workers.add(w)
        w.done.connect(self._on_rev)
        w.start()

    def _on_rev(self, seq: int, r: dict):
        """后台回来了。只有最新一次结果才允许写控件。"""
        old, self._rev_worker = self._rev_worker, None
        self._rev_workers.discard(old)
        if old is not None:
            try:
                old.deleteLater()
            except Exception:                     # noqa: BLE001
                pass
        try:
            if seq == self._rev_seq:
                self._apply_rev(r)
        except Exception:                         # noqa: BLE001
            pass                                  # 页面已销毁：丢弃，不抛
        # 期间又被 F5 过 → 用最新代数补跑一次
        if self._rev_pending:
            self._rev_pending = False
            self._start_rev(self._rev_seq, self._cur_pack())

    def _apply_rev(self, r: dict):
        """把进度结果画到控件上（只在 GUI 线程调用）。"""
        if not isinstance(r, dict):
            self.lb_review.setText("审核进度：返回格式不对")
            return
        if not r.get("ok"):
            self.lb_review.setText("审核进度：%s" % str(r.get("error"))[:40])
            return
        self.pb_review.setValue(int(round(r.get("pct") or 0)))
        self.lb_review.setText("已审 %d / %d　待审 %d%s"
                               % (r.get("reviewed", 0), r.get("main_total", 0),
                                  r.get("pending", 0),
                                  ("　修补版本 %d" % r["versions_saved"])
                                  if r.get("versions_saved") else ""))
        self.pb_review.setToolTip(
            "主图 %d 张 · 已审 %d · 待审 %d\n最后确认：%s\n口径：v3_image_edit 里 status='confirmed'"
            % (r.get("main_total", 0), r.get("reviewed", 0), r.get("pending", 0),
               r.get("last_confirmed_at") or "（还没有）"))

    def closeEvent(self, e):                      # noqa: N802
        """关页面时把后台查询收干净，避免「线程还在跑，控件已销毁」。"""
        self._rev_seq += 1                        # 让在途结果全部作废
        self._rev_pending = False
        waits = list(self._rev_workers)
        self._rev_worker = None
        self._rev_workers.clear()
        for w in waits:
            try:
                w.done.disconnect()
            except Exception:                     # noqa: BLE001
                pass
            try:
                w.wait(3000)
            except Exception:                     # noqa: BLE001
                pass
        super().closeEvent(e)

    def _goto_review(self):
        """跳到 4.1 主图人工审核页签。找不到就明说，不做无声失败。"""
        w = self
        while w is not None and not hasattr(w, "tabs"):
            w = w.parent() if hasattr(w, "parent") else None
        tgt = getattr(w, "t_wall_main", None) if w is not None else None
        if w is None or tgt is None:
            try:
                self.status.setText("4.1 页签没加载，去不了（看主窗口日志里 4.1 那一行）")
            except Exception:        # noqa: BLE001
                pass
            return
        idx = w.tabs.indexOf(tgt)
        if idx >= 0:
            w.tabs.setCurrentIndex(idx)

    def _fill_tree(self):
        # 左树右键菜单只装一次（2026-10-26：2.5 之前没有任何删包入口）
        if not getattr(self, "_tree_menu_on", False):
            try:
                self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.tree.customContextMenuRequested.connect(self._tree_menu)
                self._tree_menu_on = True
            except Exception:            # noqa: BLE001
                pass
        keep = None
        it = self.tree.currentItem()
        if it and it.parent() is not None:
            keep = (it.parent().text(0), it.text(0).split("  (")[0])
        self.tree.clear()
        f = self.lib.facets()
        allit = QTreeWidgetItem(self.tree, ["全部商品", str(sum(x["n"] for x in f["状态"]))])
        allit.setExpanded(True)
        for group in ("包", "平台", "类目", "状态"):
            g = QTreeWidgetItem(self.tree, [group, ""])
            g.setExpanded(group in ("包", "状态"))
            for x in f[group]:
                k = x["key"]
                label = STATUS_LABEL.get(k, k) if group == "状态" else str(k)
                child = QTreeWidgetItem(g, ["%s  (%d)" % (label, x["n"]), str(x["n"])])
                child.setData(0, Qt.ItemDataRole.UserRole, k)
                if keep and (group, k) == keep:
                    self.tree.setCurrentItem(child)
        if not self.tree.currentItem() or self.tree.currentItem().parent() is None:
            self.tree.setCurrentItem(allit)

    def _on_tree(self, item, col):
        if item is not None:
            self.reload()

    # ---------------- 左树「包」的右键菜单：删除包（2026-10-26 新增）
    # 主人报：「2.5 商品库里面的那些包，不能多选删除，一个一个删除也不行，
    #          文件列表的控制功能失效」。
    # 实测：wall.py 里 v3_pack_delete 出现 **0 次** —— 2.5 从来没接过删包能力，
    #       不是"点了没反应"，是**这个入口压根不存在**。后端 pack.delete_pack
    #       和 bridge.v3_pack_delete 都是现成的，这里把它接上。
    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        parent = it.parent()
        if parent is None or parent.text(0) != "包":
            return
        code = it.text(0).split("  (")[0]
        n = it.text(1) if it.columnCount() > 1 else ""
        m = QMenu(self)
        a_del = m.addAction("删除这个包（只删包与名册，商品和图片保留）")
        m.addSeparator()
        a_purge = m.addAction("连商品一起彻底删除（会删商品行与本地图片）")
        act = m.exec(self.tree.mapToGlobal(pos))
        if act is None:
            return
        if act is a_del:
            self._delete_pack(code, purge=False, n=n)
        elif act is a_purge:
            self._delete_pack(code, purge=True, n=n)

    def _delete_pack(self, code: str, purge: bool = False, n: str = ""):
        """删一个包。purge=False 只删包与成员名册；True 连商品行和本地文件一起删。"""
        if not code:
            return
        if purge:
            txt = ("要**连商品一起彻底删除**包「%s」（%s 件）吗？\n\n"
                   "· 会删掉这些商品行、它们的图片记录、以及本地已下载的图片文件\n"
                   "· 不可恢复（建议先确认这个包里的商品别处不需要）" % (code, n or "?"))
        else:
            txt = ("要删除包「%s」（%s 件）吗？\n\n"
                   "· 只删这个包和它的成员名册\n"
                   "· 商品本身、图片、OCR 全部保留，还能在「全部商品」里看到" % (code, n or "?"))
        if QMessageBox.question(self, "删除数据包", txt,
                                QMessageBox.StandardButton.Yes
                                | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            from .. import bridge as _b          # noqa: PLC0415
            try:
                r = _b.v3_pack_delete(code, purge=purge)
            except TypeError:
                r = _b.v3_pack_delete(code)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", "%s: %s" % (type(e).__name__, e))
            return
        if not isinstance(r, dict):
            r = {"ok": bool(r)}
        if r.get("ok"):
            QMessageBox.information(
                self, "已删除",
                "包「%s」已删除。\n\n商品删除 %s 件 · 成员行 %s 条 · 图片记录 %s 条"
                % (code, r.get("products_deleted", r.get("products", 0)),
                   r.get("members_deleted", 0), r.get("images_deleted", 0)))
        else:
            QMessageBox.warning(self, "没删成", str(r.get("error") or r))
        self.reload()

    # ---------------- 下载 / OCR（2026-10-24 新增，真跑后端）
    def _dl_pids(self) -> list:
        """要处理的商品：优先选中的，否则当前筛选范围内的全部。"""
        if self.selected:
            return [int(x) for x in sorted(self.selected)]
        return [int(r["product_id"]) for r in self.rows]

    def _download(self):
        self._start_dl("下载")

    def _ocr(self):
        self._start_dl("补跑 OCR", ocr_only=True)

    def _open_center(self, focus: bool = True):
        """打开下载监控 —— 默认切到「2.5.1 · 下载监控」页签，不再弹悬浮窗。

        ★ 2026-10-25 主人口径：
            「先做一个 tab 页，把它放到新界面里面，不要做一个悬浮窗了。
             做了悬浮窗反而让整个系统现在就卡 bug 一样的卡在那了。」

        行为：
          · 主窗口里有 2.5.1 页签 → 切过去（不抢焦点、不遮挡、可随时切回来）
          · 没有（独立运行/旧构建）→ 退回原来的悬浮窗，绝不静默失败
        """
        jid = int(getattr(self, "last_job", 0) or 0)
        # ---- 首选：切到 2.5.1 页签 ----
        w = self
        seen = 0
        while w is not None and not hasattr(w, "tabs") and seen < 8:
            w = w.parent() if hasattr(w, "parent") else None
            seen += 1
        tgt = getattr(w, "t_dlmon", None) if w is not None else None
        if w is not None and tgt is not None:
            try:
                if jid:
                    tgt.job_id = jid
                idx = w.tabs.indexOf(tgt)
                if idx >= 0:
                    w.tabs.setCurrentIndex(idx)
                    tgt.refresh(force=True)
                try:
                    self.status.setText("下载任务在「2.5.1 · 下载监控」页签里看"
                                        "（任务 #%s）" % (jid or "—"))
                except Exception:              # noqa: BLE001
                    pass
                return
            except Exception:                  # noqa: BLE001
                pass
        # ---- 兜底：原来的悬浮窗（只有拿不到主窗口页签时才会走到） ----
        try:
            from .dlcenter_win import DlCenterWindow
        except Exception as e:                 # noqa: BLE001
            self.status.setText("下载中心打不开：%s: %s" % (type(e).__name__, e))
            return
        if not getattr(self, "_dlwin", None):
            self._dlwin = DlCenterWindow(jid, self.window())
        else:
            self._dlwin.job_id = jid
            try:
                self._dlwin.refresh(force=True)
            except Exception:                  # noqa: BLE001
                pass
        if self._dlwin.isMinimized():
            self._dlwin.showNormal()
        self._dlwin.show()
        try:
            self._dlwin.refresh(force=True)
        except Exception:                      # noqa: BLE001
            pass
        if focus:
            self._dlwin.raise_()
            self._dlwin.activateWindow()

    def _probe_media(self, pids: list) -> dict | None:
        """★ P0 只读前置探测：这个范围到底有多少条媒体、多少已下载。
        为什么要有它：包里 18 件全是空壳时，create_job 只会回一句
        「没有可下载的媒体」，而那行字在状态栏小字里，主人看不见 ⇒ 以为"点了没反应"。"""
        from .. import bridge as B
        ids = [int(x) for x in (pids or [])]
        if not ids:
            return None

        def _do(m, c):
            qs = ",".join("?" * len(ids))
            rows = c.execute(
                "SELECT role, COUNT(*),"
                " SUM(CASE WHEN COALESCE(current_local_path,'')='' THEN 1 ELSE 0 END),"
                " SUM(CASE WHEN COALESCE(original_url,'')='' THEN 1 ELSE 0 END)"
                " FROM v3_image WHERE product_id IN (%s) GROUP BY role" % qs,
                ids).fetchall()
            by = {r[0]: {"total": r[1], "no_file": r[2] or 0, "no_url": r[3] or 0}
                  for r in rows}
            total = sum(v["total"] for v in by.values())
            no_file = sum(v["no_file"] for v in by.values())
            # 有图但一张都没落在本地的商品数（用来区分"空壳"与"待下载"）
            withimg = c.execute(
                "SELECT COUNT(DISTINCT product_id) FROM v3_image"
                " WHERE product_id IN (%s)" % qs, ids).fetchone()[0]
            return {"ok": True, "by_role": by, "total": total, "no_file": no_file,
                    "products": len(ids), "products_with_media": withimg}
        return B._wrap(_do)

    def _start_dl(self, what: str, ocr_only: bool = False):
        """★ 2026-10-24 按任务书改成「任务中心」：
        点一下 → 立刻建任务并起后台线程 → **马上弹任务窗口**（不用等，1 秒内有反馈）。
        下载/OCR 都不在 Qt 主线程跑；窗口只是读快照（600ms 一次）。
        ★ 2026-10-25 P0 修复：建任务前先只读探测媒体数量，为 0 时弹窗说清原因，
          不再只在状态栏写一行小字（主人看不到 ⇒ 以为按钮坏了）。"""
        from .. import bridge as B
        pids = self._dl_pids()
        if not pids:
            self.status.setText("没有可处理的商品：先在左树选一个包，或先选中几件")
            QMessageBox.warning(self, "没有可处理的商品",
                                "左树里没有选中任何包，照片墙里也没有勾选商品。\n"
                                "先在左边点一个包，再点「%s」。" % what)
            return
        pack = ""
        it = self.tree.currentItem()
        if it and it.parent() is not None and it.parent().text(0) == "包":
            pack = it.text(0).split("  (")[0]

        probe = self._probe_media(pids)
        if probe and probe.get("total") == 0:
            QMessageBox.warning(
                self, "这个范围里没有媒体，任务建不起来",
                "当前范围：%d 件商品（%s）\n"
                "媒体记录：0 条\n\n"
                "所以没有东西可以下载 —— 这不是按钮坏了。\n"
                "常见原因：这些商品是从 CSV 导入的「骨架记录」，"
                "原始数据里没有图片/视频字段被解析进来。\n\n"
                "可以这样做：先在左边换一个包含真实媒体的商品/包，"
                "或在照片墙里勾选那几件有图的商品再点「%s」。"
                % (len(pids), pack or "未按包筛选", what))
            self.status.setText("%s：范围内 %d 件商品、媒体 0 条，已提示原因，未建任务"
                                % (what, len(pids)))
            return
        if probe and probe.get("total") and probe.get("no_file") == 0 and not ocr_only:
            by = probe.get("by_role") or {}
            detail = "　".join("%s %d" % (k, v["total"]) for k, v in by.items())
            if QMessageBox.question(
                    self, "这些媒体都已经在本地了",
                    "当前范围：%d 件商品，%d 条媒体（%s）\n"
                    "本地缺失：0 条 —— 全部已经有文件了。\n\n"
                    "所以再点下载不会产生新文件，这是正常的。\n\n"
                    "要继续吗？\n"
                    "· 是 = 仍然建任务（会逐条校验已有文件、补跑 OCR）\n"
                    "· 否 = 不建任务，回到商品库"
                    % (probe["products"], probe["total"], detail)
            ) != QMessageBox.StandardButton.Yes:
                self.status.setText("%s：范围内 %d 条媒体全部已下载，已取消建任务"
                                    % (what, probe["total"]))
                return

        r = B.v3_dl_start(pids=pids, pack_code=pack, workers=4, ocr=True,
                          title="%s · %d 件" % (what, len(pids)), ocr_only=ocr_only)
        if not r.get("ok"):
            err = (r.get("error") or "?")[:400]
            self.status.setText("%s 没建起来：%s" % (what, err[:160]))
            QMessageBox.warning(self, "%s 没建起来" % what,
                                "原因：\n%s\n\n范围：%d 件商品（%s）"
                                % (err, len(pids), pack or "未按包筛选"))
            return
        self.last_job = int(r.get("job_id") or 0)
        self.status.setText(
            "%s：任务 #%s 已启动 · %d 件 · 引用 %s 条 → 去重 %s 个资源%s（跳过已下载 %s 条）"
            % (what, self.last_job, r.get("products", len(pids)), r.get("refs"),
               r.get("unique_urls"), "（OCR 任务）" if ocr_only else "",
               r.get("skipped_refs") or 0))
        self._open_center(focus=False)

    def _media_done(self, r: dict, what: str):
        if not r.get("ok"):
            self.status.setText("%s失败：%s" % (what, (r.get("error") or "未知")[:200]))
            return
        self.status.setText(
            "%s完成 · %d 件 · 引用 %s 条（待处理 %s）· 唯一 URL %s · 下载 %s 个文件 %s"
            " · 失败 %s · 过期 %s · 无地址 %s · OCR 成功 %s 张"
            % (what, r.get("products", 0), r.get("entries", "?"), r.get("todo", "?"),
               r.get("unique_urls", "?"), r.get("files", "?"), _mb(r.get("bytes", 0)),
               r.get("failed", "?"), r.get("expired", "?"), r.get("no_url", "?"),
               r.get("ocr_ok", "?")))
        errs = r.get("errors") or {}
        if errs:
            head = "；".join("%s（%s 次）" % (k[:60], v) for k, v in list(errs.items())[:3])
            self.status.setText(self.status.text() + "　｜ 主要失败原因：" + head)
        self.reload()

    # ---------------- 批量：移出包 / 彻底删除 / 清理回收站（2026-10-24 第二批）
    def _batch_pids(self) -> list:
        """批量操作只作用在**选中的商品**上；一件没选就明确拒绝，不偷偷跑整包。"""
        pids = [int(x) for x in sorted(self.selected)]
        if not pids:
            self.status.setText("先在照片墙/列表里选中商品（点卡片、Ctrl 多选、Shift 连选），"
                                "批量操作只作用于选中的那些")
        return pids

    def _detach(self):
        pids = self._batch_pids()
        if not pids:
            return
        if QMessageBox.question(
                self, "移出当前包",
                "把选中的 %d 件商品移出所在的包？\n\n"
                "· 商品、图片、OCR、本地文件**全部保留**\n"
                "· 只是不再属于这个包，随时能再打包进来\n"
                "· 不进回收站、不删文件" % len(pids)) != QMessageBox.StandardButton.Yes:
            return
        from .. import bridge as B
        r = B.v3_detach_products(pids)
        if not r.get("ok"):
            self.status.setText("移出失败：%s" % (r.get("error") or "?")[:200])
            return
        self.status.setText("已移出 %s 件（商品与文件都还在，只是不再属于这个包）"
                            % r.get("detached"))
        self.reload()

    def _purge(self):
        pids = self._batch_pids()
        if not pids:
            return
        if self._is_busy():
            self.status.setText("上一个删除/清理还没跑完，等它结束再来一次")
            return
        from .. import bridge as B
        imp = B.v3_purge_impact(pids)
        if not imp.get("ok"):
            self.status.setText("算影响清单失败：%s" % (imp.get("error") or "?")[:200])
            return
        shared = imp.get("shared_count") or 0
        extra = ("\n\n⚠ 其中 %d 件还被**别的包**引用 —— 这几件不会删，只会被跳过。"
                 % shared) if shared else ""
        msg = ("**永久删除**选中的 %d 件商品？删除后无法还原。\n\n"
               "· 商品行：%d\n· 媒体引用：%d 条\n· OCR 记录：%d 条\n"
               "· 本地文件：%d 个 / %.1f MB\n· 被别的商品共享、**会保留**的文件：%d 个\n"
               "· 回收站：不进（直接消失）%s"
               % (imp.get("products", 0), imp.get("products", 0), imp.get("images", 0),
                  imp.get("ocr", 0), imp.get("files_to_delete", 0),
                  (imp.get("bytes_to_delete") or 0) / 1048576.0,
                  imp.get("files_shared_kept", 0), extra))
        if QMessageBox.warning(self, "彻底删除 · 不可还原", msg,
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                               QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        # ★ 2026-10-25 P0-3：删除放后台跑，界面立刻有反馈（不再假死）
        # ★ B-01：同时开一条取消通道，按钮亮起来，随时能叫停
        self.status.setText("正在彻底删除 %d 件（后台执行，可继续看别的商品）…" % len(pids))
        self._cancel_tag = "purge-%d" % id(self)
        self.btns["⏹ 停止删除"].setEnabled(True)
        self._busy("purge", True)
        self._purge_worker = PurgeWorker(pids, cancel_tag=self._cancel_tag)
        self._purge_worker.done.connect(self._purged)
        self._purge_worker.start()

    def _stop_delete(self):
        """叫停正在跑的删除/清理（只打旗子，不杀线程 —— 由后端在检查点收尾）。"""
        from .. import bridge as B
        tag = getattr(self, "_cancel_tag", "")
        if not tag:
            self.status.setText("现在没有可以停止的删除任务")
            self.btns["⏹ 停止删除"].setEnabled(False)
            return
        r = B.cancel_fire(tag)
        if r.get("ok"):
            self.btns["⏹ 停止删除"].setEnabled(False)
            self.status.setText("已发出停止请求，正在等后台收尾…"
                                "（数据库还没提交的话会整体回滚，一件都不删）")
        else:
            self.status.setText("停止没生效：%s（任务可能已经结束了）"
                                % (r.get("error") or "?")[:120])

    def _purged(self, r: dict):
        self._busy("purge", False)
        self._cancel_tag = ""
        try:
            self.btns["⏹ 停止删除"].setEnabled(False)
        except Exception:                        # noqa: BLE001
            pass
        if r.get("cancelled"):
            if r.get("cancel_stage") == "files":
                self.status.setText(
                    "已取消（文件阶段）：数据库已删 %s 件**不可撤销**；"
                    "文件删了 %s / 计划 %s 个，其余保留"
                    % (r.get("purged_products", 0), r.get("files_deleted", 0),
                       r.get("files_planned", "?")))
            else:
                self.status.setText(
                    "已取消：**整体回滚，一件都没删**（商品 / 图片 / OCR / 文件都还在）"
                    "　原因：%s" % (r.get("error") or "用户取消")[:80])
            self.reload()
            return
        if not r.get("ok"):
            self.status.setText("删除失败：%s" % (r.get("error") or "?")[:200])
            return
        self.status.setText(
            "已彻底删除 %d 件 · 媒体引用 %s 条 · OCR %s 条 · 文件 %s 个 / %.1f MB"
            "%s%s"
            % (r.get("purged_products", 0), r.get("images", 0), r.get("ocr", 0),
               r.get("files_deleted", 0), (r.get("bytes_deleted") or 0) / 1048576.0,
               ("　｜ 共享而跳过 %d 件" % len(r.get("skipped_shared") or []))
               if r.get("skipped_shared") else "",
               ("　｜ 文件删除失败 %s 个" % r.get("file_failed")) if r.get("file_failed") else ""))
        self.reload()

    def _busy(self, what: str, on: bool):
        """长任务互斥：同一个删除/清理不许叠着跑（防重复点击，不做界面阻塞）。"""
        if not hasattr(self, "_busy_now"):
            self._busy_now = ""
        self._busy_now = (what if on else "")
        if on:
            self.status.setText(self.status.text() + "（%s 进行中）" % what)

    def _is_busy(self) -> bool:
        return bool(getattr(self, "_busy_now", ""))

    def _cleanup(self):
        """清空回收站 + 清孤儿 OCR + 清孤儿文件（三步各自出真实数字）。"""
        if self._is_busy():
            self.status.setText("上一个删除/清理还没跑完，等它结束再来一次")
            return
        from .. import bridge as B
        n_trash = 0
        try:
            lst = B.v3_trash_list(300)
            n_trash = len(lst.get("items") or [])
        except Exception:                       # noqa: BLE001
            pass
        scan = B.v3_orphan_files_scan()
        if not scan.get("ok"):
            self.status.setText("扫孤儿文件失败：%s" % (scan.get("error") or "?")[:160])
            return
        msg = ("清理三样东西：\n\n"
               "1. **清空回收站**：当前 %d 条（清了不能还原；0 条就是没事可清）\n"
               "2. **孤儿 OCR**：图片已经不在了的识别记录\n"
               "3. **孤儿文件**：媒体目录里没有任何商品引用的文件 —— "
               "%d 个 / %.1f MB\n\n会影响到的文件已经列出来，删前会再校验一次引用。"
               "继续吗？" % (n_trash, scan.get("orphan_files", 0),
                            (scan.get("bytes") or 0) / 1048576.0))
        if QMessageBox.warning(self, "清理回收站与孤儿文件", msg,
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                               QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        # ★ 2026-10-25 P0-3：三步都放后台；回收站 0 条时明确说「本来就是空的」，
        #   不再让人以为「点了没作用」。
        self.status.setText("正在清理（后台执行）… 回收站待清 %d 条" % n_trash)
        self._cancel_tag = "clean-%d" % id(self)
        self.btns["⏹ 停止删除"].setEnabled(True)
        self._busy("clean", True)
        self._clean_worker = CleanupWorker(cancel_tag=self._cancel_tag)
        self._clean_worker.done.connect(self._cleaned)
        self._clean_worker.start()

    def _cleaned(self, out: dict):
        self._busy("clean", False)
        self._cancel_tag = ""
        try:
            self.btns["⏹ 停止删除"].setEnabled(False)
        except Exception:                        # noqa: BLE001
            pass
        a, b, c = out.get("trash") or {}, out.get("ocr") or {}, out.get("files") or {}
        bad = [k for k, v in (("回收站", a), ("OCR", b), ("文件", c)) if not v.get("ok")]
        cleared = a.get("cleared", 0)
        head = ("回收站本来就是空的（0 条）" if not cleared
                else "回收站清掉 %s 条" % cleared)
        msg = ("清理完成 · %s · 孤儿 OCR 删 %s 条（原有 %s）· "
               "孤儿文件删 %s 个 / %.1f MB（保留被引用的 %s 个）%s"
               % (head, b.get("deleted", "?"), b.get("orphan", "?"),
                  c.get("deleted", "?"), (c.get("bytes") or 0) / 1048576.0,
                  c.get("skipped_alive", "?"),
                  ("　｜ 失败：" + "、".join(bad)) if bad else ""))
        if out.get("cancelled"):
            done_keys = [k for k in ("trash", "ocr", "files") if out.get(k)]
            msg = ("已取消（停在第 %s 步）· 已完成的：%s · 后面几步没跑"
                   % (out.get("stopped_at") or "?", "、".join(done_keys) or "无"))
        self.status.setText(msg)
        self.reload()

    # 兼容旧调用（tab_wall.refresh 会调这两个名字）
    def _fill_wall(self):
        self._reset_cards()

    def _reset_cards(self):
        self._gen += 1                     # 旧任务全部作废
        for c in self.cards.values():
            c.setParent(None); c.deleteLater()
        self.cards.clear()
        self._shown = 0
        self.thumb_done = 0; self.thumb_expected = 0; self.thumb_first_at = None
        self.more.setVisible(len(self.rows) > 0)
        self._load_more()

    def _load_more(self):
        if self._shown >= len(self.rows):
            self.more.setVisible(False)
            return
        gen = self._gen
        end = min(self._shown + PAGE_SIZE, len(self.rows))
        for row in self.rows[self._shown:end]:
            pid = int(row["product_id"])
            got = self._local.get(pid)
            c = Card(row, self.thumb_size, has_local=bool(got))
            c.clicked.connect(lambda p, m: self._click(p, m))
            c.opened.connect(self._show_detail)
            self.cards[pid] = c
            self.grid.addWidget(c, 0, 0)
            if got:
                iid, path = got
                self.thumb_expected += 1
                self.pool.start(_Task(self.sig, self.thumb_dir, pid, path,
                                      self.thumb_size, iid, gen))
        self._shown = end
        self._relayout()
        self.more.setVisible(self._shown < len(self.rows))
        self.more.setText("载入更多…（已显示 %d / %d）" % (self._shown, len(self.rows)))
        self.lb_count.setText("当前 %d 件 · 已显示 %d" % (len(self.rows), self._shown))
        if self.selected:
            self._apply_sel()

    def _maybe_more(self, v):
        sb = self.scroll.verticalScrollBar()
        if sb.maximum() and v >= sb.maximum() - 60:
            self._load_more()

    # ---------------- 视图
    def _set_view(self, i):
        self.btn_wall.setChecked(i == 0); self.btn_table.setChecked(i == 1)
        self.stack.setCurrentIndex(i)
        if i != 1:
            return
        # 同步选中：用一次 QItemSelection 批量选，别逐行 selectRow（504 行实测 336 ms）
        try:
            from PyQt6.QtCore import QItemSelection, QItemSelectionModel
            self.table.blockSignals(True)
            self.table.clearSelection()
            if self.selected:
                sel = QItemSelection()
                ncol = self.model.columnCount() - 1
                for r in range(self.model.rowCount()):
                    pid = int(self.model.rows[r]["product_id"])
                    if pid in self.selected:
                        sel.select(self.model.index(r, 0), self.model.index(r, ncol))
                self.table.selectionModel().select(
                    sel, QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows)
            self.table.blockSignals(False)
        except Exception:                    # noqa: BLE001
            pass

    def _on_size(self, v):
        self.thumb_size = int(v)
        gen = self._gen
        for pid, c in self.cards.items():
            c.setFixedWidth(self.thumb_size + 22)
            c.img.setFixedSize(self.thumb_size, self.thumb_size)
            got = self._local.get(pid)
            if got:
                self.pool.start(_Task(self.sig, self.thumb_dir, pid, got[1],
                                      self.thumb_size, got[0], gen))
        self._relayout()

    def _relayout(self):
        if not self.cards:
            return
        w = max(200, self.scroll.viewport().width() - 20)
        cols = max(1, w // (self.thumb_size + 26))
        for i, pid in enumerate(list(self.cards.keys())):
            self.grid.addWidget(self.cards[pid], i // cols, i % cols)

    def resizeEvent(self, e):              # noqa: N802
        super().resizeEvent(e)
        if not hasattr(self, "_rt"):
            self._rt = QTimer(self); self._rt.setSingleShot(True)
            self._rt.timeout.connect(self._relayout)
        self._rt.start(120)

    def _on_thumb(self, pid, img, gen):
        if gen != self._gen:
            return                          # 过期任务，直接丢
        self.thumb_done += 1
        if self.thumb_first_at is None:
            self.thumb_first_at = time.time()
        c = self.cards.get(pid)
        if c and not img.isNull():
            c.set_pixmap(QPixmap.fromImage(img))

    # ---------------- 选择
    @property
    def _order(self):
        return [int(r["product_id"]) for r in self.rows]

    def _click(self, pid, mods):
        order = self._order
        idx = order.index(pid) if pid in order else -1
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier.value)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier.value)
        if shift and self.anchor >= 0 and idx >= 0:
            a, b = sorted((self.anchor, idx))
            self.selected = set(order[a:b + 1])
        elif ctrl:
            if pid in self.selected:
                self.selected.discard(pid)
            else:
                self.selected.add(pid)
            self.anchor = idx
        else:
            self.selected = {pid}; self.anchor = idx
        self._apply_sel()
        if len(self.selected) == 1:
            self._show_detail(next(iter(self.selected)))
        elif not self.selected:
            self.detail._clear("未选中商品")
            self.lb_side_title.setText("商品详情")
        else:
            self.detail._clear("已选 %d 件（选 1 件看详情）" % len(self.selected))
            self.lb_side_title.setText("商品详情")

    def _apply_sel(self):
        for pid, c in self.cards.items():
            c.set_selected(pid in self.selected)
        self.lb_sel.setText("选中 %d 件" % len(self.selected))

    def _update_sel(self):
        self._apply_sel()

    def _table_sel(self, *a):
        pids = {int(self.model.rows[i.row()]["product_id"])
                for i in self.table.selectionModel().selectedRows()}
        self.selected = pids
        self._apply_sel()
        if len(pids) == 1:
            self._show_detail(next(iter(pids)))

    def keyPressEvent(self, e):            # noqa: N802
        if e.key() == Qt.Key.Key_A and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.selected = set(self._order); self._apply_sel(); return
        if e.key() == Qt.Key.Key_D and (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.selected.clear(); self._apply_sel(); return
        if e.key() == Qt.Key.Key_F5:
            self.reload(); return
        super().keyPressEvent(e)

    # ---------------- 详情
    def _show_detail(self, pid):
        t0 = time.perf_counter()
        try:
            d = self.lib.detail(int(pid))
        except Exception as ex:             # noqa: BLE001
            self.detail._clear("详情读取失败：%s" % ex)
            return
        self.detail.show_product(d)
        p = (d or {}).get("product") or {}
        self.lb_side_title.setText((p.get("original_title") or "商品详情")[:22])
        self._last_ms["detail"] = (time.perf_counter() - t0) * 1000

    def _open_detail_window(self):
        if not self.detail.pid:
            return
        dlg = QDialog(self); dlg.setWindowTitle("商品详情 · %d" % self.detail.pid)
        dlg.resize(880, 860)
        lay = QVBoxLayout(dlg); lay.setContentsMargins(0, 0, 0, 0)
        card = DetailCard(wide=True)
        lay.addWidget(card)
        try:
            card.show_product(self.lib.detail(self.detail.pid))
        except Exception:                    # noqa: BLE001
            pass
        dlg.exec()

    # ---------------- 只读工具
    def _later(self, name, stage):
        self.status.setText("%s：%s 才接线（不假装做过）" % (name, stage))

    def _light_table(self):
        pids = [p for p in self._order if p in self.selected][:4] or \
               ([self.detail.pid] if self.detail.pid else [])
        if not pids:
            self.status.setText("先选 2–4 件再并排对比")
            return
        dlg = QDialog(self); dlg.setWindowTitle("并排对比（%d 件）" % len(pids))
        dlg.resize(320 * len(pids), 460)
        h = QHBoxLayout(dlg)
        for pid in pids:
            col = QVBoxLayout()
            d = self.lib.detail(pid)
            p = (d or {}).get("product") or {}
            hero = next((i for i in (d.get("images") or []) if i.get("has_file")), None)
            lb = QLabel(); lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setPixmap(_pix_cached(hero["abs"] if hero else "", 300, 300))
            col.addWidget(lb)
            t = QLabel((p.get("original_title") or "")[:40]); t.setWordWrap(True)
            col.addWidget(t)
            h.addLayout(col)
        dlg.exec()

    def _open_dir(self):
        pid = next(iter(self.selected), self.detail.pid) if self.selected else self.detail.pid
        if not pid:
            return
        path = self.lib.local_file(pid)
        if not path:
            self.status.setText("这件商品还没有本地图片，无目录可打开")
            return
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    def _show_raw(self):
        pid = next(iter(self.selected), self.detail.pid) if self.selected else self.detail.pid
        if not pid:
            return
        con = sqlite3.connect("file:%s?mode=ro" % self.lib.db.replace("\\", "/"), uri=True)
        try:
            r = con.execute("select original_payload from v3_product where product_id=?",
                            (pid,)).fetchone()
        finally:
            con.close()
        txt = (r[0] if r and r[0] else "") or "（库中无原始 payload）"
        try:
            txt = json.dumps(json.loads(txt), ensure_ascii=False, indent=2)
        except Exception:                    # noqa: BLE001
            pass
        dlg = QDialog(self); dlg.setWindowTitle("原始 JSON · %d" % pid)
        dlg.resize(760, 620)
        v = QVBoxLayout(dlg)
        te = QTextEdit(); te.setReadOnly(True); te.setPlainText(txt[:400000])
        v.addWidget(te)
        dlg.exec()

    def perf(self) -> dict:
        """给验收用：本页最近一次 reload / 详情 的真实耗时（ms）。"""
        return dict(self._last_ms)


