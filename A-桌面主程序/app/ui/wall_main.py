# -*- coding: utf-8 -*-
"""4.1 · 主图瀑布流照片墙（虚拟化绘制版 · 阶段 C.1）

与 2.5「商品库」的区别（这是设计的全部理由）：

    2.5   一格 = 一件商品（封面图代表它）
    4.1   一格 = **一张主图**  ← 因为这里要逐张修图，商品级的格子会隐藏其余主图

三条硬约束：
  ① 只显示 `role='main'`：详情图/SKU 图/轮播图**不混进来**（不靠文件名猜角色）
  ② 每格必须能追溯到「数据包 + 商品 + 图片 + 序号」
  ③ 任何写操作都必须有确认与台账：批量确认 = 逐张写 `v3_image_edit` 记录

C.1 的渲染方式改了（这是本次改动的全部要点）：
  旧版 1022 张主图 = **1022 个 ImgCard(QFrame)**，每格 5 个 QLabel ⇒ 5000+ 控件，滚动必然卡。
  新版 **0 个卡片控件**：一个绘制面 + 自算的矩形表，
    · 只画视口 ±OVERSCAN 范围内的格子（paintEvent 里按 rect 命中）
    · 只有视口需要的缩略图才排队解码（4 线程），其余连请求都不发
    · 内存里最多留 MAX_PIX 张 QPixmap（LRU），滚动再快也不涨
    · 尺寸滑块只重算内存矩形 + 重新为视口排队，**不重解码全库原图**
    · 格子高度按 `v3_image.width/height` 真实比例排（瀑布流），缺省按方形；
      绘制一律 KeepAspectRatio ⇒ **不裁切商品主体**
    · 悬停 1 秒的浮层是**画上去的**，不创建子控件 ⇒ 不挤压、不重排

公开接口保持不变（tab_wall_main / main_window 只用到 refresh / fill_packs / rows）：
    MainWallPage.rows / by_iid / order / sel / refresh() / fill_packs()
    opened_from_nav() / open_editor() / lb_count / lb_sel / lb_tip / 各 btn_*
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict

from PyQt6.QtCore import (QObject, QPoint, QRect, QSize, Qt, QThreadPool, QRunnable,
                          QTimer, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage, QImageReader,
                         QPainter, QPen, QPixmap)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from ..v3img import (MainLib, STATE_ALL, STATE_CONF, STATE_DRAFT, STATE_NONE,
                     STATE_NOLOCAL, STATE_SAVED, STATES)
from .. import imgcap, paths

# ★ 崩溃守夜器心跳：崩溃日志里能直接看到「最后一步停在哪张图、哪个阶段」。
#   2026-10-27 那次原生崩溃就死在 LaMa 推理里，业务日志停在框换算那一行，
#   有了心跳，崩溃日志会写明「LaMa推理 第7/9张 #14683 1440x1440 mask=112690px」。
try:
    from ..crashguard import beat as _beat
except Exception:                                   # noqa: BLE001

    def _beat(_s: str = "") -> None:                # noqa: D103
        return None

# 批量消除的落盘日志目录（用户要求：日志要能保存，后续据此修复）
LOG_DIR = str(getattr(paths, "LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data", "logs")))

BADGE = {
    STATE_NONE: ("未处理", "#5c6672"),
    STATE_DRAFT: ("有草稿", "#b98a2e"),
    STATE_SAVED: ("已存版本·待确认", "#2f7fb8"),
    STATE_CONF: ("已确认", "#3f9e63"),
    STATE_NOLOCAL: ("没有本地图", "#a8453b"),
}

GAP = 6                 # 格子间距（尽量少留白）
PAD = 8                 # 内容区外边距
OVERSCAN = 360          # 视口上下各多铺这么多像素（滚动预取）
MAX_PIX = 400           # 内存里最多留多少张缩略图（LRU）
HOVER_MS = 1000         # 悬停多久出浮层
CARD_RADIUS = 4


class _Sig(QObject):
    ready = pyqtSignal(int, QImage)
    meta = pyqtSignal(int, float)       # image_id, 真实宽高比（宽/高）


class _Thumb(QRunnable):
    """缩略图：磁盘缓存键含 path+size+mtime —— 换版后自动失效（不靠手动清缓存）。

    顺手报回**真实宽高比**（从文件头读，不解码全图）—— 库里的 width/height 是空的，
    瀑布流要靠它才排得准。
    """

    def __init__(self, sig, cache_dir: str, pid: int, image_id: int, path: str, size: int):
        super().__init__()
        self.sig, self.cache_dir = sig, cache_dir
        self.pid, self.image_id, self.path, self.size = int(pid), int(image_id), path, int(size)

    def _name(self) -> str:
        try:
            st = os.stat(self.path)
            fp = "%x_%x" % (int(st.st_size), int(st.st_mtime_ns or 0))
        except OSError:
            fp = "missing"
        return "m%d_%d_%s_%d.jpg" % (self.pid, self.image_id, fp, self.size)

    def run(self):
        img = QImage()
        aspect = 0.0
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            cache = os.path.join(self.cache_dir, self._name())
            if os.path.exists(cache) and os.path.getsize(cache) > 0:
                img = imgcap.decode(cache)          # 缓存已是小图，仍走闸门记账
            if img.isNull() and self.path and os.path.isfile(self.path):
                # ★ 2026-10-28 崩溃加固：先读文件头拿宽高比，再让解码器**直接产出**
                #   长边 ≤ self.size 的像素。原来 r.read() 是全尺寸解码再丢像素，
                #   4 线程并发 + 大图 = 事件日志里 ntdll 0xc0000374 的典型放大器。
                sz = imgcap.size_of(self.path)
                if sz.isValid():
                    aspect = sz.width() / float(sz.height())
                img = imgcap.decode(self.path, max_side=self.size)
                if not img.isNull():
                    img.save(cache, "JPG", 85)
        except Exception:              # noqa: BLE001
            pass
        if not aspect and not img.isNull() and img.height():
            aspect = img.width() / float(img.height())
        try:
            if aspect > 0:
                self.sig.meta.emit(self.image_id, float(aspect))
        except (RuntimeError, AttributeError):
            pass
        try:
            self.sig.ready.emit(self.image_id, img)
        except RuntimeError:
            pass


def _aspect(row: dict) -> float:
    """宽/高。没有就按 1.0（方形格），绘制仍 KeepAspectRatio ⇒ 不会裁切。"""
    try:
        w = float(row.get("width") or 0)
        h = float(row.get("height") or 0)
        if w > 0 and h > 0:
            return max(0.30, min(3.30, w / h))
    except Exception:                  # noqa: BLE001
        pass
    return 1.0


class WallCanvas(QWidget):
    """**唯一的绘制面**。所有格子都画在这里，不创建任何子控件。"""

    picked = pyqtSignal(int, int)      # image_id, modifiers
    opened = pyqtSignal(int)
    hovered = pyqtSignal(int)          # 0 = 离开
    detailRequested = pyqtSignal(int)  # ★ 右键：要看这个商品的详情图

    def __init__(self, wall: "VirtualWall"):
        super().__init__()
        self.owner = wall
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._hover_iid = 0
        self._hover_rect = QRect()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(HOVER_MS)
        self._timer.timeout.connect(self._fire_hover)
        # ★ 2026-10-26 快速通道：在缩略图上直接拖框标出要抹掉的地方
        self._drag_mark = None      # (iid, 起点 QPoint)
        self._tmp_mark = None       # (iid, QRect) 正在拖的框

    _pending = 0

    def _cell_at(self, pos) -> int:
        for iid in self.owner.visible_ids(extra=0):
            if self.owner.rects.get(iid, QRect()).contains(pos):
                return iid
        return 0

    def _fire_hover(self):
        iid = self._pending
        if not iid:
            return
        self._hover_iid = iid
        self._hover_rect = self.owner.rects.get(iid, QRect())
        self.hovered.emit(self._hover_iid)
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        # ★ 快速消除：正在拖框 —— 只更新临时框，不做任何选中/悬停
        if self._drag_mark:
            iid, start = self._drag_mark
            pr = self.owner.pic_rect(iid)
            if not pr.isEmpty():
                p = e.position().toPoint()
                a = QPoint(max(pr.left(), min(start.x(), pr.right())),
                           max(pr.top(), min(start.y(), pr.bottom())))
                b = QPoint(max(pr.left(), min(p.x(), pr.right())),
                           max(pr.top(), min(p.y(), pr.bottom())))
                self._tmp_mark = (iid, QRect(a, b).normalized())
                self.update()
            return
        iid = self._cell_at(e.position().toPoint())
        if iid != self._pending:
            self._pending = iid
            self._timer.stop()
            if self._hover_iid and self._hover_iid != iid:
                self._hover_iid = 0
                self.hovered.emit(0)
                self.update()
            if iid:
                self._timer.start()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._timer.stop()
        self._pending = 0
        if self._hover_iid:
            self._hover_iid = 0
            self.hovered.emit(0)
            self.update()
        super().leaveEvent(e)

    # ---------- 点击
    def mousePressEvent(self, e):  # noqa: N802
        # ★ 右键 = 看这个商品的详情图（主图有 logo 的，详情图通常也有，要一起修）
        if e.button() == Qt.MouseButton.RightButton:
            iid = self._cell_at(e.position().toPoint())
            if iid:
                self.hide_hover()
                self.detailRequested.emit(iid)
                return
        if e.button() == Qt.MouseButton.LeftButton:
            iid = self._cell_at(e.position().toPoint())
            if iid:
                # ★ 快速消除模式：在图上拖框 = 标出要抹的地方（不选中、不进工作台）
                if self.owner.mark_mode and \
                        self.owner.pic_rect(iid).contains(e.position().toPoint()):
                    self.hide_hover()
                    self._drag_mark = (iid, e.position().toPoint())
                    self._tmp_mark = None
                    return
                self.hide_hover()
                self.picked.emit(iid, int(e.modifiers().value))
                return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        # ★ 快速消除：松手落框 → 换算成「相对图片的 0–1 归一化坐标」存进墙里。
        #   归一化是必须的：缩略图尺寸和原图尺寸不同，存像素会在批量应用时错位。
        if e.button() == Qt.MouseButton.LeftButton and self._drag_mark:
            iid, _start = self._drag_mark
            self._drag_mark = None
            tmp = self._tmp_mark
            self._tmp_mark = None
            if tmp is not None and tmp[1].width() >= 4 and tmp[1].height() >= 4:
                _i, rect = tmp
                pr = self.owner.pic_rect(iid)
                if not pr.isEmpty() and pr.width() > 0 and pr.height() > 0:
                    def _nx(v):
                        return max(0.0, min(1.0, (v - pr.x()) / float(pr.width())))
                    def _ny(v):
                        return max(0.0, min(1.0, (v - pr.y()) / float(pr.height())))
                    nx0, nx1 = _nx(rect.x()), _nx(rect.x() + rect.width())
                    ny0, ny1 = _ny(rect.y()), _ny(rect.y() + rect.height())
                    if nx1 - nx0 > 0.004 and ny1 - ny0 > 0.004:
                        self.owner.set_mark(iid, (min(nx0, nx1), min(ny0, ny1),
                                                  max(nx0, nx1), max(ny0, ny1)))
            self.update()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and not self.owner.mark_mode:
            iid = self._cell_at(e.position().toPoint())
            if iid:
                self.opened.emit(iid)
                return
        super().mouseDoubleClickEvent(e)

    def hide_hover(self):
        self._timer.stop()
        self._pending = 0
        if self._hover_iid:
            self._hover_iid = 0
            self.hovered.emit(0)
            self.update()

    # ---------- 绘制
    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(e.rect(), QColor("#10141a"))

        ids = self.owner.visible_ids(extra=OVERSCAN)
        if not ids:
            p.setPen(QColor("#6f7b8a"))
            f = QFont(); f.setPointSizeF(10.0); p.setFont(f)
            p.drawText(self.rect().adjusted(24, 24, -24, -24),
                       int(Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                       "这个筛选下没有主图。\n（换个数据包，或把状态下拉框切回「全部」）")
            return

        view = self.owner.view_rect()
        for iid in ids:
            r = self.owner.rects.get(iid)
            if r is None or not r.intersects(view.adjusted(-GAP, -GAP, GAP, GAP)):
                continue
            self._draw_cell(p, iid, r)
            self._draw_marks(p, iid)
            if iid == self._hover_iid:
                self._draw_hover(p, iid, r)
        p.end()

    def _draw_marks(self, p: QPainter, iid: int):
        """画「已标记待消除」的框 + 正在拖的临时框（快速通道的核心视觉）。"""
        pr = self.owner.pic_rect(iid)
        if pr.isEmpty():
            return
        # ① 已存标记（归一化 → 屏幕矩形）
        mk = self.owner.marks.get(int(iid))
        if mk is not None:
            nx0, ny0, nx1, ny1 = mk
            box = QRect(
                pr.x() + int(nx0 * pr.width()),
                pr.y() + int(ny0 * pr.height()),
                max(1, int((nx1 - nx0) * pr.width())),
                max(1, int((ny1 - ny0) * pr.height())))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(239, 68, 68, 70))
            p.drawRect(box)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ef4444"), 2))
            p.drawRect(box)
            # 角标：这张已排进批量队列
            p.setPen(QColor("#ef4444"))
            f = QFont(); f.setPointSizeF(9.0); f.setBold(True); p.setFont(f)
            p.drawText(pr.adjusted(3, 2, -3, -2),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop), "⚡")
        # ② 正在拖的临时框
        tmp = self._tmp_mark
        if tmp is not None and tmp[0] == iid:
            p.setBrush(QColor(124, 58, 237, 60))
            p.setPen(QPen(QColor("#a78bfa"), 2, Qt.PenStyle.DashLine))
            p.drawRect(tmp[1])

    def _draw_cell(self, p: QPainter, iid: int, r: QRect):
        row = self.owner.by_iid.get(iid) or {}
        exists = bool(row.get("exists"))
        sel = iid in self.owner.sel
        pm = self.owner.pix(iid)

        # 底
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#171b21" if not sel else "#1d2330"))
        p.drawRoundedRect(r, CARD_RADIUS, CARD_RADIUS)

        inner = r.adjusted(2, 2, -2, -2)
        if pm is not None and not pm.isNull():
            pm = pm.scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            x = inner.x() + (inner.width() - pm.width()) // 2
            y = inner.y() + (inner.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        elif exists:
            # 还在解码：画个极简等待点
            p.setPen(QColor("#38414d"))
            f = QFont(); f.setPointSizeF(9.0); p.setFont(f)
            p.drawText(inner, int(Qt.AlignmentFlag.AlignCenter), "·")
        else:
            # 缺本地图：简洁占位（不再写多行"文件缺失"文字）
            p.setPen(QPen(QColor("#2b323c"), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor("#141820"))
            p.drawRoundedRect(inner.adjusted(1, 1, -1, -1), CARD_RADIUS, CARD_RADIUS)
            p.setPen(QColor("#4a5462"))
            f = QFont(); f.setPointSizeF(11.0); p.setFont(f)
            p.drawText(inner, int(Qt.AlignmentFlag.AlignCenter), "⊘")

        # 极简角标：★ 当前发布主图 / ● 状态点
        st = row.get("state") or STATE_NONE
        _nm, col = BADGE.get(st, (st, "#5c6672"))
        if row.get("is_confirmed_main"):
            p.setPen(QColor("#f0c04a"))
            f = QFont(); f.setPointSizeF(10.0); f.setBold(True); p.setFont(f)
            p.drawText(r.adjusted(5, 3, -5, -3), int(Qt.AlignmentFlag.AlignLeft |
                                                     Qt.AlignmentFlag.AlignTop), "★")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        p.drawEllipse(r.right() - 12, r.top() + 6, 6, 6)

        # 选中框
        if sel:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#7C3AED"), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), CARD_RADIUS, CARD_RADIUS)

    def _draw_hover(self, p: QPainter, iid: int, r: QRect):
        row = self.owner.by_iid.get(iid) or {}
        h = min(r.height(), 104)
        box = QRect(r.left(), r.bottom() - h + 1, r.width(), h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 14, 20, 226))
        p.drawRoundedRect(box, CARD_RADIUS, CARD_RADIUS)

        st = row.get("state") or STATE_NONE
        st_name, st_col = BADGE.get(st, (st, "#5c6672"))
        local = "本地可读" if row.get("exists") else "缺本地图"
        seq = row.get("sequence")
        lines = [
            ("标题", (row.get("title") or "（无标题）")),
            ("商品", str(row.get("product_id") or "")),
            ("图片", "#%s" % row.get("image_id")),
            ("序号", "#%s" % (seq if seq is not None else "—")),
            ("本地", local),
            ("审核", st_name),
        ]
        f = QFont(); f.setPointSizeF(8.2); p.setFont(f)
        fm = QFontMetrics(f)
        x = box.left() + 7
        y = box.top() + 6 + fm.ascent()
        for k, v in lines:
            p.setPen(QColor("#8e9aa9"))
            p.drawText(x, y, k)
            p.setPen(QColor(st_col if k == "审核" else "#e8eef5"))
            room = box.width() - 34
            p.drawText(x + 30, y, fm.elidedText(v, Qt.TextElideMode.ElideRight, room))
            y += fm.height() + 1


class VirtualWall(QScrollArea):
    """1022 张也是一次滚动 = 一次重绘；控件数量与图片数无关。"""

    picked = pyqtSignal(int, int)
    opened = pyqtSignal(int)
    selChanged = pyqtSignal()
    detailRequested = pyqtSignal(int)   # ★ 右键：看这个商品的详情图
    marksChanged = pyqtSignal()         # ★ 快速消除：标记集合变了

    def __init__(self, thumb_dir: str, parent=None):
        super().__init__(parent)
        self.thumb_dir = thumb_dir
        self.rows: list[dict] = []
        self.by_iid: dict[int, dict] = {}
        self.order: list[int] = []
        self.rects: dict[int, QRect] = {}
        self.sel: set[int] = set()
        self.thumb_size = 170
        self._cols = 1
        self._content_h = 0
        # ★ 2026-10-26 快速通道：直接在缩略图上标「要抹哪里」，可连标多张后一键应用
        self.mark_mode = False
        self.marks: dict[int, tuple] = {}     # iid → (nx0, ny0, nx1, ny1) 0–1 归一化
        self._pix: OrderedDict[int, QPixmap] = OrderedDict()
        self._want: set[int] = set()
        self._missing_asked: set[int] = set()
        self._ar: dict[int, float] = {}        # image_id → 真实宽高比（从文件头学到）
        self._ar_dirty = False
        self._meta_timer = QTimer(self)
        self._meta_timer.setSingleShot(True)
        self._meta_timer.setInterval(420)      # 学到新比例后延迟重排（暂停滚动才动布局）
        self._meta_timer.timeout.connect(self._apply_meta)

        self.pool = QThreadPool(); self.pool.setMaxThreadCount(4)
        self.sig = _Sig()
        self.sig.ready.connect(self._on_thumb)
        self.sig.meta.connect(self._on_meta)

        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cv = WallCanvas(self)
        self.setWidget(self.cv)
        self.cv.picked.connect(self.picked)
        self.cv.opened.connect(self.opened)
        self.cv.detailRequested.connect(self.detailRequested)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)

    # ---------- 快速消除标记（归一化坐标）
    def set_mark_mode(self, on: bool):
        self.mark_mode = bool(on)
        self.cv.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        self.cv.update()

    def pic_rect(self, iid: int) -> QRect:
        """图片在格子里的**实际绘制矩形**（KeepAspectRatio 居中之后那块）。

        ★ 必须和 `_draw_cell` 里的缩放算法逐字一致 —— 不然框选位置和消除位置会错开。
        """
        r = self.rects.get(int(iid))
        if r is None or r.isEmpty():
            return QRect()
        inner = r.adjusted(2, 2, -2, -2)
        pm = self._pix.get(int(iid))
        if pm is None or pm.isNull():
            return inner                       # 还没解码：用内区近似（解码后会重绘更准）
        sp = pm.scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        x = inner.x() + (inner.width() - sp.width()) // 2
        y = inner.y() + (inner.height() - sp.height()) // 2
        return QRect(x, y, sp.width(), sp.height())

    def set_mark(self, iid: int, box: tuple):
        self.marks[int(iid)] = box
        self.cv.update()
        self.marksChanged.emit()

    def clear_marks(self):
        if not self.marks:
            return
        self.marks.clear()
        self.cv.update()
        self.marksChanged.emit()

    def drop_mark(self, iid: int):
        if self.marks.pop(int(iid), None) is not None:
            self.cv.update()
            self.marksChanged.emit()

    # ---------- 数据
    def set_rows(self, rows: list[dict]):
        self.rows = rows
        self.by_iid = {int(r["image_id"]): r for r in rows}
        self.order = [int(r["image_id"]) for r in rows]
        self.sel &= set(self.order)
        self._pix.clear(); self._want.clear(); self._missing_asked.clear()
        self._relayout(keep_scroll=False)
        self.selChanged.emit()

    def set_thumb_size(self, px: int):
        px = int(px)
        if px == self.thumb_size:
            return
        self.thumb_size = px
        self._pix.clear()           # 尺寸变了，旧的缩略图不用了（磁盘缓存按尺寸分文件）
        self._want.clear()
        self._relayout(keep_scroll=True)

    # ---------- 布局（瀑布流：按真实宽高比、列高最矮优先）
    def _ar_of(self, iid: int, row: dict) -> float:
        """优先用库里的 width/height；没有就用从文件头学到的；再没有按 1.0（方形格）。"""
        a = _aspect(row)
        if a != 1.0:
            return a
        return max(0.30, min(3.30, float(self._ar.get(int(iid)) or 1.0)))

    def _anchor(self) -> tuple[int, int]:
        """当前视口里第一张可见图的 (image_id, 它距视口顶的偏移) —— 重排后靠它稳定视觉位置。"""
        if not self.order:
            return 0, 0
        top = self.verticalScrollBar().value()
        for iid in self.order:
            r = self.rects.get(iid)
            if r is not None and r.bottom() >= top:
                return iid, r.top() - top
        return 0, 0

    def _relayout(self, keep_scroll: bool = True):
        vw = max(120, self.viewport().width())
        keep_iid, keep_dy = self._anchor() if keep_scroll else (0, 0)
        cols = max(1, (vw - 2 * PAD + GAP) // (self.thumb_size + GAP))
        col_w = max(40.0, (vw - 2 * PAD - (cols - 1) * GAP) / float(cols))
        self._cols = cols
        self.rects.clear()
        if not self.order:
            self._content_h = 0
            self.cv.setFixedSize(vw, max(1, self.viewport().height()))
            self._update_scroll(keep_scroll)
            self.cv.update()
            return

        heights = [float(PAD)] * cols
        for iid in self.order:
            row = self.by_iid.get(iid) or {}
            c = min(range(cols), key=lambda i: heights[i])
            x = int(round(PAD + c * (col_w + GAP)))
            y = int(round(heights[c]))
            h = int(round(col_w / self._ar_of(iid, row)))
            h = max(int(col_w * 0.55), min(h, int(col_w * 2.8)))
            self.rects[iid] = QRect(x, y, int(round(col_w)), h)
            heights[c] = heights[c] + h + GAP
        self._content_h = int(max(heights) + PAD)
        self.cv.setFixedSize(vw, max(1, self._content_h))
        self._update_scroll(False)
        if keep_iid and keep_iid in self.rects:
            sb = self.verticalScrollBar()
            sb.setValue(max(0, min(sb.maximum(), self.rects[keep_iid].top() - keep_dy)))
        self.cv.update()

    # ---------- 学到真实比例后（暂停滚动 420ms）重排一次
    def _on_meta(self, iid: int, aspect: float):
        iid = int(iid)
        if iid not in self.by_iid or aspect <= 0:
            return
        old = self._ar.get(iid)
        self._ar[iid] = float(aspect)
        if old is None or abs(old - aspect) / max(0.01, aspect) > 0.03:
            self._ar_dirty = True
            self._meta_timer.start()

    def _apply_meta(self):
        if not self._ar_dirty:
            return
        self._ar_dirty = False
        self._relayout(keep_scroll=True)
        self._ensure_thumbs()

    def _update_scroll(self, keep: bool):
        sb = self.verticalScrollBar()
        old = sb.value()
        sb.setRange(0, max(0, self._content_h - self.viewport().height()))
        sb.setPageStep(max(10, self.viewport().height()))
        sb.setSingleStep(max(24, self.thumb_size // 3))
        if keep:
            sb.setValue(min(old, sb.maximum()))

    # ---------- 视口
    def view_rect(self) -> QRect:
        sb = self.verticalScrollBar()
        vh = self.viewport().height()
        return QRect(0, max(0, sb.value() - OVERSCAN), max(1, self.viewport().width()),
                     vh + 2 * OVERSCAN)

    def visible_ids(self, extra: int = 0) -> list[int]:
        vr = self.view_rect()
        if extra:
            vr = vr.adjusted(0, -extra, 0, extra)
        out = [iid for iid in self.order
               if (self.rects.get(iid) is not None and self.rects[iid].intersects(vr))]
        return out

    def _on_scrolled(self, _v):
        self.cv.hide_hover()
        self.cv.update()
        self._ensure_thumbs()

    def _ensure_thumbs(self):
        vr = self.view_rect()
        for iid in self.order:
            r = self.rects.get(iid)
            if r is None or not r.intersects(vr):
                continue
            row = self.by_iid.get(iid) or {}
            if not row.get("exists"):
                continue
            if iid in self._pix or iid in self._want:
                continue
            self._want.add(iid)
            self.pool.start(_Thumb(self.sig, self.thumb_dir, row.get("product_id") or 0,
                                   iid, row.get("abs_path") or "", self.thumb_size))

    def _on_thumb(self, iid: int, img: QImage):
        iid = int(iid)
        self._want.discard(iid)
        if iid not in self.by_iid:
            return
        if img.isNull():
            return
        try:
            self._pix[iid] = QPixmap.fromImage(img)
            self._pix.move_to_end(iid)
            while len(self._pix) > MAX_PIX:
                self._pix.popitem(last=False)
        except RuntimeError:
            return
        vr = self.view_rect()
        r = self.rects.get(iid)
        if r is not None and r.intersects(vr):
            self.cv.update(r.adjusted(-2, -2, 2, 2))

    def pix(self, iid: int):
        pm = self._pix.get(int(iid))
        if pm is not None:
            self._pix.move_to_end(int(iid))
        return pm

    # ---------- 选择
    def click(self, iid: int, mods: int):
        iid = int(iid)
        ctrl = bool(mods & int(Qt.KeyboardModifier.ControlModifier.value))
        shift = bool(mods & int(Qt.KeyboardModifier.ShiftModifier.value))
        if ctrl:
            self.sel.symmetric_difference_update({iid})
        elif shift and self.sel:
            anchor = min(self.sel, key=lambda i: self.order.index(i)
                         if i in self.order else 0)
            a = self.order.index(anchor) if anchor in self.order else 0
            b = self.order.index(iid) if iid in self.order else a
            self.sel = set(self.order[min(a, b):max(a, b) + 1])
        else:
            self.sel = {iid}
        self.cv.update()
        self.selChanged.emit()

    def select_all(self, on: bool):
        self.sel = set(self.order) if on else set()
        self.cv.update()
        self.selChanged.emit()

    def ensure(self, iid: int):
        r = self.rects.get(int(iid))
        if r is None:
            return
        self.verticalScrollBar().setValue(max(0, r.center().y() - self.viewport().height() // 2))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._relayout(keep_scroll=True)
        self._ensure_thumbs()


class _MarkSig(QObject):
    """批量消除的进度信号（必须在主线程创建，worker 只负责 emit）。"""
    one = pyqtSignal(int, bool, str)      # image_id, ok, msg
    done = pyqtSignal(int, int)           # ok 数, fail 数


def _lama_mod():
    """拿 inpaint_lama 模块。三条回退路，与 editor.py 的 v3mod() 同口径。"""
    try:
        from server.v3 import inpaint_lama as L   # noqa: PLC0415
        return L
    except Exception:                             # noqa: BLE001
        pass
    try:
        import inpaint_lama as L                  # noqa: PLC0415
        return L
    except Exception:                             # noqa: BLE001
        pass
    import importlib.util                          # noqa: PLC0415
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in (os.path.join(base, "..", "taobao-picker", "server", "v3", "inpaint_lama.py"),
                 os.path.join(base, "taobao-picker", "server", "v3", "inpaint_lama.py")):
        p = os.path.abspath(cand)
        if os.path.isfile(p):
            spec = importlib.util.spec_from_file_location("inpaint_lama_file", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise ImportError("找不到 inpaint_lama.py")


def _lower_thread_priority():
    """把**当前线程**降成「低于正常」优先级 —— 预热/修补时前台界面仍然跟手。

    用户原话（2026-10-27）：「我这边一预热，软件就卡住用不了，那这预热有什么用」。
    根因有两层：① 后台线程直接写界面（已按跨线程闸门修掉，那是会崩的那层）；
    ② ONNX 加载/推理把 CPU 吃满，界面线程抢不到时间片 —— 这一层就是本函数治的。
    Windows: SetThreadPriority(GetCurrentThread(), -1 = BELOW_NORMAL)。
    ONNX 自己开的线程由本线程创建，一般继承优先级，所以整条链路都会给界面让位。
    """
    try:
        import ctypes                              # noqa: PLC0415
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), -1)
    except Exception:                              # noqa: BLE001
        pass


class _MarkWorker(QRunnable):
    """后台逐张跑 LaMa 消除 → 存新版本。**原图不动，只生成新版本**。

    归一化坐标在这里换算成原图像素 —— 缩略图上框的是比例，原图尺寸不同，
    直接用屏幕像素会在批量时错位（这是快速通道唯一容易错的地方）。

    ★ 2026-10-27 用户反馈「成功0张失败5张，到底怎么失败的不知道」：
      原来每个失败分支只 emit 一句固定中文（"读图失败"/"保存失败"），
      不带 image_id、不带路径、不带异常原文；界面上又被下一张覆盖、最后被汇总覆盖，
      **故障现场被彻底销毁**。现在改成：
        ① 每个失败都带「图片号 + 卡在哪步 + 具体值 + 异常原文」
        ② 通过 log 回调逐条落盘（data/logs/mark-YYYYMMDD.log）
        ③ 每张报耗时，首张单独标「含模型加载」
    """

    def __init__(self, items, api, sig: _MarkSig, log=None):
        super().__init__()
        self.items = items          # [(image_id, (nx0,ny0,nx1,ny1), row_dict), ...]
        self.api = api
        self.sig = sig
        self._log = log or (lambda s: None)
        self._n_confirmed = 0

    def _L(self, s: str):
        try:
            self._log(s)
        except Exception:                          # noqa: BLE001
            pass

    def run(self):  # noqa: D401
        ok = fail = 0
        self._L("开始：待处理 %d 张，正在准备环境（首个 import 通常很快）" % len(self.items))
        t_env = time.time()
        try:
            import cv2
            import numpy as np
            L = _lama_mod()
        except Exception as e:                     # noqa: BLE001
            detail = "环境不可用：%s: %s" % (type(e).__name__, e)
            self._L("✗ " + detail)
            for iid, _b, _r in self.items:
                self.sig.one.emit(int(iid), False, detail)
            self.sig.done.emit(0, len(self.items))
            return
        self._L("环境就绪（%.2fs）→ 开始逐张处理；★ 第一张要加载 208MB 模型，约 30 秒" %
                (time.time() - t_env))

        t_all = time.time()
        for idx, (iid, box, row) in enumerate(self.items, 1):
            t0 = time.time()
            tag = "第 %d/%d 张 · 图 #%s" % (idx, len(self.items), iid)
            try:
                lp = (row.get("current_local_path") or row.get("original_local_path") or "")
                if not lp:
                    fail += 1
                    msg = "没有本地文件（库里 current_local_path / original_local_path 都是空）"
                    self._L("✗ %s：%s" % (tag, msg))
                    self.sig.one.emit(int(iid), False, msg)
                    continue
                if not os.path.exists(lp):
                    fail += 1
                    msg = "本地文件不存在：%s" % lp
                    self._L("✗ %s：%s" % (tag, msg))
                    self.sig.one.emit(int(iid), False, msg)
                    continue
                im = cv2.imdecode(np.fromfile(lp, dtype=np.uint8), cv2.IMREAD_COLOR)
                if im is None:
                    fail += 1
                    msg = "解码失败（文件可能损坏或不是图片）：%s" % lp
                    self._L("✗ %s：%s" % (tag, msg))
                    self.sig.one.emit(int(iid), False, msg)
                    continue
                h, w = im.shape[:2]
                nx0, ny0, nx1, ny1 = box
                # 归一化 → 原图像素（左闭右开再夹紧，保证至少 2px）
                x0 = max(0, min(int(nx0 * w), w - 2))
                y0 = max(0, min(int(ny0 * h), h - 2))
                x1 = max(x0 + 1, min(int(np.ceil(nx1 * w)), w - 1))
                y1 = max(y0 + 1, min(int(np.ceil(ny1 * h)), h - 1))
                mask = np.zeros((h, w), np.uint8)
                mask[y0:y1 + 1, x0:x1 + 1] = 255
                n_px = int((mask > 0).sum())
                self._L("· %s：图 %dx%d，框 归一化(%.3f,%.3f,%.3f,%.3f) → 像素(%d,%d)-(%d,%d) "
                        "= %d px" % (tag, w, h, nx0, ny0, nx1, ny1, x0, y0, x1, y1, n_px))
                if n_px <= 0:
                    fail += 1
                    msg = "框换算后 mask 为 0 像素（框太小或坐标异常）"
                    self._L("✗ %s：%s" % (tag, msg))
                    self.sig.one.emit(int(iid), False, msg)
                    continue
                t1 = time.time()
                # ★ 崩溃守夜器：把「正在对哪张图、多大、框多少像素」记进全局心跳。
                #   2026-10-27 那次原生崩溃就停在这一行之后的 sess.run 里，
                #   有了它，崩溃日志能直接指出「第 7/9 张 1440x1440 112690px」。
                _beat("LaMa推理 第%d/%d张 #%s %dx%d mask=%dpx"
                      % (idx, len(self.items), iid, w, h, n_px))
                out = None
                t_ipc = time.time()
                try:                                  # 先走子进程：主进程不加载模型，界面不卡
                    from .. import lama_ipc            # noqa: PLC0415
                    out = lama_ipc.inpaint(im, mask)
                except Exception as e:                # noqa: BLE001
                    self._L("· %s：子进程不可用（%s: %s），回退进程内"
                            % (tag, type(e).__name__, str(e)[:80]))
                if out is None:
                    out = L.inpaint(im, mask)         # 兜底（会占 GIL，但至少不中断）
                else:
                    self._L("· %s：子进程修补完成（%.1fs）" % (tag, time.time() - t_ipc))
                t_lama = time.time() - t1
                _beat("LaMa完成 第%d/%d张 #%s %.1fs"
                      % (idx, len(self.items), iid, t_lama))
                # 结果和原图是否一样（一样必然被判重复，提前说清而不是让用户猜）
                try:
                    same = bool(np.array_equal(out, im))
                except Exception:                  # noqa: BLE001
                    same = False
                self._L("· %s：LaMa %.1fs%s" %
                        (tag, t_lama, " ⚠ 输出与原图逐字节相同（模型没起作用）" if same else ""))
                regions = [{"kind": "rect", "rect": [x0, y0, x1, y1]}]
                _beat("保存入库 第%d/%d张 #%s %dx%d"
                      % (idx, len(self.items), iid, w, h))
                r = self.api.save_version(row, out, mask, regions, "lama", 3,
                                          0, "快速通道")
                if isinstance(r, dict) and r.get("ok"):
                    ok += 1
                    if r.get("confirmed"):
                        self._L("✓ %s：已存并**已换图**（新版本 #%s 已成为主图，"
                                "原主图 #%s 退为历史）（%.1fs）"
                                % (tag, r.get("version_image_id"),
                                   r.get("previous_main_image_id"), time.time() - t0))
                    else:
                        self._L("✓ %s：已存新版本 #%s（未换图：%s）（%.1fs）" %
                                (tag, r.get("version_image_id"),
                                 r.get("confirm_error") or "auto_confirm 关闭",
                                 time.time() - t0))
                    self.sig.one.emit(int(iid), True, "")
                    self._n_confirmed += 1 if r.get("confirmed") else 0
                else:
                    fail += 1
                    err = (r or {}).get("error") if isinstance(r, dict) else None
                    dup = (r or {}).get("duplicate_of") if isinstance(r, dict) else None
                    msg = err or "保存失败（保存接口没给原因）"
                    if dup:
                        msg += " ｜ 与已有图 #%s 内容相同" % dup
                    self._L("✗ %s：保存被拒 —— %s（%.1fs）" % (tag, msg, time.time() - t0))
                    self.sig.one.emit(int(iid), False, msg)
            except Exception as e:                 # noqa: BLE001
                fail += 1
                import traceback as _tb
                msg = "%s: %s" % (type(e).__name__, e)
                self._L("✗ %s：处理异常 %s" % (tag, msg))
                self._L(_tb.format_exc().rstrip())
                self.sig.one.emit(int(iid), False, msg)
        self._L("完成：成功 %d · 失败 %d · 总耗时 %.1fs" %
                (ok, fail, time.time() - t_all))
        self.sig.done.emit(ok, fail)


class _DetailMarkCanvas(QWidget):
    """详情图专用标记画布 —— 把一张详情图按 KeepAspectRatio 画出来，并在上面拖框。

    为什么单独写一个而不用 WallCanvas：
        WallCanvas 是「多格子虚拟墙」，详情窗口是「单张大图」，两者坐标体系不同
        （一个算 grid 里的 cell rect，一个算单张图居中后的 rect）。
        但**归一化坐标与落库格式完全一致**（0–1 的 nx0,ny0,nx1,ny1），
        所以标记可以直接交给同一批 _MarkWorker / 同一条写库链路。

    交互：
        · 标记模式开 → 按住左键拖 = 画框（松手即存，可反复重画覆盖）
        · 标记模式关 → 左键拖 = 平移查看（图片比控件大时）
    """

    boxChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm: QPixmap | None = None
        self.iid: int = 0
        self.box: tuple | None = None          # 归一化 (nx0, ny0, nx1, ny1)
        self.mark_mode = False
        self._drag_from = None                 # 正在拖的起点（控件坐标）
        self._drag_now = None
        self._pan_from = None
        self._pan_off = None
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---------- 数据
    def set_image(self, iid: int, pm: QPixmap | None, box: tuple | None = None):
        self.iid = int(iid or 0)
        self.pm = pm
        self.box = box
        self.update()

    def set_box(self, box: tuple | None):
        self.box = box
        self.update()
        self.boxChanged.emit()

    def set_mark_mode(self, on: bool):
        self.mark_mode = bool(on)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.OpenHandCursor)
        self.update()

    # ---------- 绘制几何（必须与 paintEvent 逐字一致，否则框会错位）
    def pic_rect(self) -> QRect:
        if self.pm is None or self.pm.isNull():
            return QRect()
        inner = self.rect().adjusted(6, 6, -6, -6)
        if inner.width() <= 0 or inner.height() <= 0:
            return QRect()
        sp = self.pm.scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        x = inner.x() + (inner.width() - sp.width()) // 2
        y = inner.y() + (inner.height() - sp.height()) // 2
        return QRect(x, y, sp.width(), sp.height())

    def _to_norm(self, p) -> tuple:
        r = self.pic_rect()
        if r.isEmpty():
            return (0.0, 0.0, 0.0, 0.0)
        nx = (p.x() - r.x()) / max(1, r.width())
        ny = (p.y() - r.y()) / max(1, r.height())
        return (min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0))

    # ---------- 事件
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.mark_mode:
            self._drag_from = e.position().toPoint()
            self._drag_now = self._drag_from
            self.update()
        else:
            self._pan_from = e.position().toPoint()
            self._pan_off = (0, 0)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self.mark_mode and self._drag_from is not None:
            self._drag_now = e.position().toPoint()
            self.update()
        elif not self.mark_mode and self._pan_from is not None:
            d = e.position().toPoint() - self._pan_from
            self._pan_off = (d.x(), d.y())

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.mark_mode and self._drag_from is not None:
            a = self._to_norm(self._drag_from)
            b = self._to_norm(self._drag_now or e.position().toPoint())
            self._drag_from = self._drag_now = None
            nx0, nx1 = sorted((a[0], b[0]))
            ny0, ny1 = sorted((a[1], b[1]))
            # 太小的框当误点，忽略（与照片墙同一口径：松手时至少 4×4 像素）
            r = self.pic_rect()
            if (nx1 - nx0) * r.width() < 4 or (ny1 - ny0) * r.height() < 4:
                self.update()
                return
            self.set_box((round(nx0, 4), round(ny0, 4), round(nx1, 4), round(ny1, 4)))
        else:
            self._pan_from = None

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#12161c"))
        r = self.pic_rect()
        if self.pm is None or r.isEmpty():
            p.setPen(QColor("#8b96a4"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "（无本地文件或解码失败）" if self.pm is None else "")
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(r, self.pm)
        # 已存的框
        if self.box:
            bx = QRect(r.x() + int(self.box[0] * r.width()),
                       r.y() + int(self.box[1] * r.height()),
                       max(1, int((self.box[2] - self.box[0]) * r.width())),
                       max(1, int((self.box[3] - self.box[1]) * r.height())))
            p.fillRect(bx, QColor(255, 60, 60, 64))
            p.setPen(QPen(QColor("#ff3b30"), 2))
            p.drawRect(bx)
            p.setPen(QColor("#ffffff"))
            p.drawText(bx.adjusted(3, 2, -3, -2),
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, "要消除")
        # 正在拖的框
        if self.mark_mode and self._drag_from is not None and self._drag_now is not None:
            d = QRect(self._drag_from, self._drag_now).normalized()
            p.fillRect(d, QColor(255, 220, 0, 50))
            p.setPen(QPen(QColor("#ffd60a"), 1, Qt.PenStyle.DashLine))
            p.drawRect(d)
        p.end()


class DetailDialog(QDialog):
    """右键商品 → 看/修这个商品的**详情图**。

    理由（用户原话）：主图里有 logo 的，详情页里一般也有 logo。
    要改就一起改，不然主图干净了详情图还挂着水印，等于白改。

    ★ 2026-10-27 用户要求：这里也要能标记消除（原来只能双击进独立编辑器）。
      现在：左边大图 + 右边缩略图条，开「🪄 标记消除」直接在详情图上拖框，
            可连标多张，一次「⚡ 应用」全部用 LaMa 抹掉。
    """

    def __init__(self, lib, pid: int, api=None, parent=None):
        super().__init__(parent)
        self.lib = lib
        self.pid = int(pid)
        self.api = api
        self.rows: list[dict] = []
        # ★ 2026-10-27 主人要求：右键看详情图要**在右边区域**打开，不要浮动窗。
        #   嵌进右侧工作台时，被嵌的 QDialog 没有"独立窗口"可关，
        #   所以「关闭」按钮改走这个钩子（由工作台切回修补页）。
        self.close_hook = None
        self.setWindowTitle("详情图 · 商品 %s" % self.pid)
        self.resize(1080, 720)
        self._build()
        self._load()

    def set_close_hook(self, fn):
        """嵌入模式：点「关闭」时调用 fn（不 accept，否则整块会消失）。"""
        self.close_hook = fn
        return self

    def _do_close(self):
        if callable(self.close_hook):
            try:
                self.close_hook()
                return
            except Exception:              # noqa: BLE001
                pass
        self.accept()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 8)
        root.setSpacing(6)
        self.lb_head = QLabel("加载中…")
        self.lb_head.setStyleSheet("color:#cfd8e3; font-size:13px;")
        self.lb_head.setWordWrap(True)
        root.addWidget(self.lb_head)

        # ===== 主体：左大图（可标记） + 右缩略图条 =====
        body = QHBoxLayout()
        body.setSpacing(8)
        self.canvas = _DetailMarkCanvas(self)
        self.canvas.boxChanged.connect(self._on_box_changed)
        body.addWidget(self.canvas, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        lb_r = QLabel("详情图列表")
        lb_r.setStyleSheet("color:#8b96a4;")
        right.addWidget(lb_r)
        self.lw = QListWidget()
        self.lw.setViewMode(QListWidget.ViewMode.IconMode)
        self.lw.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.lw.setMovement(QListWidget.Movement.Static)
        self.lw.setIconSize(QSize(96, 96))
        self.lw.setGridSize(QSize(112, 126))
        self.lw.setSpacing(3)
        self.lw.setWordWrap(True)
        self.lw.setFixedWidth(250)
        self.lw.currentItemChanged.connect(self._on_pick)
        self.lw.itemDoubleClicked.connect(self._open_one)
        right.addWidget(self.lw, 1)
        body.addLayout(right)
        root.addLayout(body, 1)

        # ===== 工具条 =====
        bar2 = QHBoxLayout()
        self.btn_mark = QPushButton("🪄 标记消除")
        self.btn_mark.setCheckable(True)
        self.btn_mark.setToolTip(
            "打开后：在左边大图上按住左键拖框，标出要抹掉的地方（logo / 促销大字 / 水印）。\n"
            "★ 可以一张一张往下标（右侧列表切换），标记会一直留着 —— "
            "主图有 logo 的，详情图通常也有，一起标完再一次性消除。\n"
            "再点一次关闭，恢复「左键拖动=平移查看」。")
        self.btn_mark.toggled.connect(self._on_mark_mode)
        self.btn_apply = QPushButton("⚡ 应用 0 张")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip("把已标记的详情图一次性用 LaMa 消除（约 3 秒/张，后台跑不卡界面）")
        self.btn_apply.clicked.connect(self._apply_marks)
        self.btn_clear_mk = QPushButton("清除全部标记")
        self.btn_clear_mk.setEnabled(False)
        self.btn_clear_mk.clicked.connect(self._clear_marks)
        self.btn_warm = QPushButton("🔥 预热模型")
        self.btn_warm.setToolTip(
            "先在后台加载模型（约 30 秒，不卡界面）。\n"
            "点它 → 立刻开始拖框标记，标完点「应用」时模型通常已就绪，省掉干等。")
        self.btn_warm.clicked.connect(self._warm_model)
        for w in (self.btn_mark, self.btn_apply, self.btn_clear_mk, self.btn_warm):
            bar2.addWidget(w)
        self.lb_tip = QLabel("点右侧列表切换图片；开「标记消除」后在大图上拖框圈要抹掉的地方")
        self.lb_tip.setStyleSheet("color:#8b96a4;")
        self.lb_tip.setWordWrap(True)
        bar2.addWidget(self.lb_tip, 1)
        btn_close = QPushButton("关闭")
        btn_close.setToolTip("嵌在右侧工作台时：切回「修补」页（不会关掉工作台）")
        btn_close.clicked.connect(self._do_close)
        bar2.addWidget(btn_close)
        root.addLayout(bar2)

        # 标记状态：{image_id: (nx0,ny0,nx1,ny1)}，与主图照片墙同一格式
        self.marks: dict[int, tuple] = {}

    def _conn(self):
        return self.lib._ro() if hasattr(self.lib, "_ro") else None

    def _load(self):
        rows: list[dict] = []
        title = ""
        try:
            c = self.lib._ro()
            try:
                p = c.execute("SELECT original_title FROM v3_product WHERE product_id=?",
                              (self.pid,)).fetchone()
                if p:
                    title = p[0] or ""
                cur = c.execute(
                    "SELECT image_id, current_local_path, original_local_path,"
                    " sequence, width, height FROM v3_image"
                    " WHERE product_id=? AND role='detail'"
                    " ORDER BY COALESCE(sequence, 999), image_id", (self.pid,))
                for r in cur.fetchall():
                    rows.append({"image_id": int(r[0]),
                                 "path": (r[1] or r[2] or ""),
                                 "sequence": r[3], "width": r[4], "height": r[5]})
            finally:
                c.close()
        except Exception as e:                     # noqa: BLE001
            self.lb_head.setText("读详情图失败：%s: %s" % (type(e).__name__, e))
            return
        self.rows = rows
        have = sum(1 for r in rows if r["path"] and os.path.exists(r["path"]))
        self.lb_head.setText(
            "%s　·　详情图 %d 张（本地可读 %d）%s"
            % (title or "（无标题）", len(rows), have,
               "　⚠ 有 %d 张缺本地文件" % (len(rows) - have) if have < len(rows) else ""))
        self.lw.clear()
        for r in rows:
            it = QListWidgetItem("#%s" % r["image_id"])
            p = r["path"]
            if p and os.path.exists(p):
                # ★ 2026-10-28：原来 QPixmap(p) 是**全尺寸解码**，详情图 30+ 张连解
                #   会把内存推高（事件日志有 RADAR_PRE_LEAK_64 预警）。改成解码时就缩到
                #   190（由 libjpeg 直接缩），内存与耗时都降一个量级。
                pm = imgcap.pixmap(p, 190)
                if not pm.isNull():
                    it.setIcon(QIcon(pm))
                else:
                    it.setText("#%s（解码失败）" % r["image_id"])
            else:
                it.setText("#%s（无本地文件）" % r["image_id"])
            it.setData(Qt.ItemDataRole.UserRole, r["image_id"])
            it.setToolTip("%d×%d　%s" % (r.get("width") or 0, r.get("height") or 0, p or ""))
            self.lw.addItem(it)
        # 自动选第一张，省得用户还要点一下才知道大图区在哪
        if self.lw.count():
            self.lw.setCurrentRow(0)

    # ---------------------------------------------------- 选图 / 标记
    def _row_of(self, iid: int) -> dict:
        return next((r for r in self.rows if int(r["image_id"]) == int(iid)), {})

    def _on_pick(self, cur, _prev=None):
        """右侧列表换一张 → 左边大图跟着换，并把这张已存的框画出来。"""
        if cur is None:
            return
        iid = int(cur.data(Qt.ItemDataRole.UserRole) or 0)
        if not iid:
            return
        row = self._row_of(iid)
        path = row.get("path") or ""
        pm = None
        if path and os.path.exists(path):
            # ★ 2026-10-28：大图显示也限边长（2200）。框存的是**归一化比例**
            #   （见 canvas.box 注释），所以缩显示尺寸不影响标记坐标。
            pm = imgcap.pixmap(path, 2200)
            if pm.isNull():
                pm = None
        self.canvas.set_image(iid, pm, self.marks.get(iid))
        st = ""
        if iid in self.marks:
            st = "　·　这张已标记，可继续标别的"
        self.lb_tip.setText(
            "当前 #%s　%d×%d%s%s"
            % (iid, row.get("width") or 0, row.get("height") or 0,
               "" if pm is not None else "　⚠ 无本地文件/解码失败", st))

    def _on_box_changed(self):
        iid = int(self.canvas.iid or 0)
        if not iid:
            return
        box = self.canvas.box
        if box:
            self.marks[iid] = box
        else:
            self.marks.pop(iid, None)
        self._refresh_marks()

    def _refresh_marks(self):
        n = len(self.marks)
        self.btn_apply.setText("⚡ 应用 %d 张" % n)
        busy = bool(getattr(self, "_marking", False))
        self.btn_apply.setEnabled(bool(n) and not busy)
        self.btn_clear_mk.setEnabled(bool(n) and not busy)
        # 右侧列表给已标记的加个 ✓ 前缀，一眼看出哪些标过
        for i in range(self.lw.count()):
            it = self.lw.item(i)
            iid = int(it.data(Qt.ItemDataRole.UserRole) or 0)
            base = "#%s" % iid
            it.setText(base + ("　✓已标" if iid in self.marks else ""))

    def _clear_marks(self):
        self.marks.clear()
        self.canvas.set_box(None)
        self._refresh_marks()
        self.lb_tip.setText("已清除全部标记")

    def _on_mark_mode(self, on: bool):
        self.canvas.set_mark_mode(bool(on))
        if on:
            self.btn_mark.setText("🪄 标记中…")
            self.lb_tip.setText(
                "标记模式：在左边大图上按住左键拖出框。★ 标完一张点右侧列表换下一张继续标，"
                "所有标记都会保留；全部标完点「⚡ 应用 N 张」一次抹掉。")
        else:
            self.btn_mark.setText("🪄 标记消除")

    # ---------------------------------------------------- 预热
    def _warm_model(self):
        if getattr(self, "_warming", False):
            self.lb_tip.setText("🔥 模型正在加载中…")
            return
        st = getattr(self, "_warm_state", None)
        if st and st.get("ok"):
            self.lb_tip.setText("🔥 模型已就绪（上次 %.1f 秒）" % float(st.get("seconds") or 0))
            return
        self._warming = True
        self.btn_warm.setEnabled(False)
        self.btn_warm.setText("🔥 加载中…")
        self.lb_tip.setText("🔥 后台加载模型（约 30 秒）—— 你可以现在就开始拖框标记，不用等。")
        t0 = time.time()
        dlg = self

        class _S(QObject):
            done = pyqtSignal(dict)

        sig = _S()
        sig.done.connect(dlg._on_warm_done)
        self._warm_sig = sig

        class _W(QRunnable):
            def run(self):                           # noqa: D401
                # ★ 2026-10-27 第三次修「预热把界面卡死」：改成**子进程**加载。
                #   进程内 InferenceSession 会长期持有 GIL，GIL 是进程内互斥锁，
                #   降线程优先级对它无效 —— 上一轮那个改法只减轻 CPU 抢占，界面照样卡。
                #   现在模型在 app.lama_worker.py 里加载，主进程只等一行 JSON。
                r = {"ok": False, "seconds": 0.0, "error": "未知"}
                try:
                    from .. import lama_ipc                  # noqa: PLC0415
                    r = lama_ipc.warmup()
                    r["via"] = "子进程"
                except Exception as e:               # noqa: BLE001
                    r = {"ok": False, "error": "子进程不可用：%s: %s"
                         % (type(e).__name__, e), "via": "子进程"}
                if not r.get("ok"):
                    try:                             # 回退：进程内（这一步真会卡，所以只做兜底）
                        r2 = _lama_mod().warmup()
                        r2["via"] = "进程内兜底（会卡一下）"
                        r2["error"] = r.get("error") or ""
                        r = r2 if r2.get("ok") else r
                    except Exception as e:           # noqa: BLE001
                        r["error"] = "%s ｜ 兜底也失败：%s" % (r.get("error"), e)
                sig.done.emit(r)

        pool = QThreadPool(self)
        pool.setMaxThreadCount(1)
        pool.start(_W())
        self._warm_pool = pool

    def _on_warm_done(self, r: dict):
        self._warming = False
        self._warm_state = r
        self.btn_warm.setEnabled(True)
        secs = float(r.get("seconds") or 0)
        if r.get("ok"):
            self.btn_warm.setText("🔥 已就绪")
            self.lb_tip.setText("✓ 模型已就绪（%.1f 秒）—— 现在点「应用」就是 3 秒/张" % secs)
        else:
            self.btn_warm.setText("🔥 预热模型")
            self.lb_tip.setText("✗ 预热失败：%s" % (r.get("error") or "未知"))

    # ---------------------------------------------------- 批量应用（复用主图那套 worker）
    def _apply_marks(self):
        if not self.marks:
            self.lb_tip.setText("⚠ 还没标任何图")
            return
        items = []
        for iid, box in dict(self.marks).items():
            row = self._row_of(iid)
            if not row:
                continue
            # _MarkWorker 读的是 current_local_path / original_local_path
            # ★ 2026-10-27 修（真 bug）：原字典只有 image_id + 两个路径，缺 product_id。
            #   save_version() 第一行就是 int(img["product_id"]) → KeyError，
            #   于是"详情图批量消除"每次都记成失败。这里补齐 product_id / role / pack_code，
            #   role='detail' 很关键：让 save_version 走 approve_media()（换原行路径），
            #   而不是 approve()（会把 v3_product.main_image_id 指到一张详情图上）。
            items.append((int(iid), box, {"image_id": int(iid),
                                          "product_id": self.pid,
                                          "role": "detail",
                                          "sequence": row.get("sequence"),
                                          "current_local_path": row.get("path") or "",
                                          "original_local_path": row.get("path") or ""}))
        if not items:
            self.lb_tip.setText("⚠ 标记里没有可处理的图")
            return
        self._marking = True
        self._refresh_marks()
        self.btn_mark.setEnabled(False)
        self._mark_total = len(items)
        self._mark_ok = self._mark_fail = 0
        self._mark_fails = []
        self._mark_t0 = time.time()
        self.lb_tip.setText(
            "⚡ 开始处理 %d 张详情图（首张含模型加载，约 30 秒）…" % len(items))

        sig = _MarkSig()
        sig.one.connect(self._on_mark_one)
        sig.done.connect(self._on_marks_done)
        self._mark_sig = sig
        w = _MarkWorker(items, self.api, sig, log=self._mark_log)
        pool = QThreadPool(self)
        pool.setMaxThreadCount(1)
        pool.start(w)
        self._mark_pool = pool

    def _mark_log(self, s: str):
        """详情图的批量消除同样落盘 —— 出问题我直接读文件。"""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(os.path.join(LOG_DIR, "mark-%s.log" % time.strftime("%Y%m%d")),
                      "a", encoding="utf-8") as fh:
                fh.write("[%s] [详情图] %s\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), s))
        except Exception:                            # noqa: BLE001
            pass
        try:
            lg = getattr(self.api, "log", None)
            if callable(lg):
                lg("[4.1详情] " + str(s))
        except Exception:                            # noqa: BLE001
            pass

    def _on_mark_one(self, iid: int, ok: bool, msg: str):
        if ok:
            self._mark_ok += 1
            self.marks.pop(int(iid), None)
            self.canvas.set_box(None) if int(iid) == int(self.canvas.iid) else None
        else:
            self._mark_fail += 1
            self._mark_fails.append((int(iid), msg))
        done = self._mark_ok + self._mark_fail
        head = "⚡ 修补中 %d/%d（成功 %d · 失败 %d）" % (
            done, self._mark_total, self._mark_ok, self._mark_fail)
        if self._mark_fails:
            li, lm = self._mark_fails[-1]
            head += "　✗ #%s：%s" % (li, lm)
        self.lb_tip.setText(head)
        self.btn_apply.setText("⚡ 修补中 %d/%d" % (done, self._mark_total))
        self._refresh_marks()

    def _on_marks_done(self, ok: int, fail: int):
        self._marking = False
        self.btn_mark.setEnabled(True)
        self._refresh_marks()
        if fail and self._mark_fails:
            lines = ["⚡ 完成：成功 %d · 失败 %d" % (ok, fail)]
            for i, m in self._mark_fails[:5]:
                lines.append("✗ #%s：%s" % (i, m))
            self.lb_tip.setText("　".join(lines))
        else:
            self.lb_tip.setText("✓ 完成：%d 张详情图已消除并另存新版本（原图未动）" % ok)
        # 刷新缩略图（图变了）
        try:
            self._reload_thumbs()
        except Exception:                            # noqa: BLE001
            pass

    def _reload_thumbs(self):
        for i in range(self.lw.count()):
            it = self.lw.item(i)
            iid = int(it.data(Qt.ItemDataRole.UserRole) or 0)
            row = self._row_of(iid)
            p = (row or {}).get("path") or ""
            if p and os.path.exists(p):
                pm = imgcap.pixmap(p, 96)
                if not pm.isNull():
                    it.setIcon(QIcon(pm))

    def _open_one(self, item: QListWidgetItem):
        iid = item.data(Qt.ItemDataRole.UserRole)
        if not iid:
            return
        if self.api is None:
            self.lb_tip.setText("没有编辑器可用（api 未接入）")
            return
        try:
            from .editor import EditorWindow   # noqa: PLC0415
            row = next((r for r in self.rows if r["image_id"] == int(iid)), None)
            win = EditorWindow({"image_id": int(iid), "product_id": self.pid,
                                "pack_code": "", "title": "",
                                "current_local_path": (row or {}).get("path", "")},
                               self.api, self)
            win.show()
        except Exception as e:                     # noqa: BLE001
            self.lb_tip.setText("打开编辑器失败：%s: %s" % (type(e).__name__, e))


class MainWallPage(QWidget):
    """主图照片墙。单击 = 选中（外层在右侧工作台加载）；双击 = 独立窗口精修。"""

    def __init__(self, db_path: str, thumb_dir: str, api, parent=None):
        super().__init__(parent)
        self.lib = MainLib(db_path)
        self.thumb_dir = thumb_dir
        self.api = api
        self.logw = None            # 由外层页签注入（MainWallTab.__init__）
        self.rows: list[dict] = []
        self.order: list[int] = []
        self._build()
        self.refresh(pack="")

    # 兼容旧属性名（外部/测试可能读）
    @property
    def by_iid(self) -> dict:
        return self.wall.by_iid

    @property
    def sel(self) -> set:
        return self.wall.sel

    @sel.setter
    def sel(self, v):
        self.wall.sel = set(v)

    # ---------------- UI
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        r1 = QHBoxLayout(); r1.setContentsMargins(8, 8, 8, 4); r1.setSpacing(6)
        r1.addWidget(QLabel("数据包"))
        self.cb_pack = QComboBox()
        self.cb_pack.setMinimumWidth(130)
        self.cb_pack.setMaximumWidth(260)
        self.cb_pack.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cb_pack.setMinimumContentsLength(10)
        self.cb_pack.currentIndexChanged.connect(lambda *_: self.refresh(pack=self._pack()))
        r1.addWidget(self.cb_pack)
        r1.addWidget(QLabel("搜索"))
        self.ed_q = QLineEdit(); self.ed_q.setPlaceholderText("标题 / 商品ID / 图片ID（回车）")
        self.ed_q.setMinimumWidth(120); self.ed_q.setMaximumWidth(190)
        self.ed_q.returnPressed.connect(
            lambda: self.refresh(pack=self._pack(), q=self.ed_q.text().strip()))
        r1.addWidget(self.ed_q)
        r1.addWidget(QLabel("状态"))
        self.cb_state = QComboBox(); self.cb_state.addItems(STATES)
        self.cb_state.setMaximumWidth(130)
        self.cb_state.currentIndexChanged.connect(
            lambda *_: self.refresh(pack=self._pack(), q=self.ed_q.text().strip()))
        r1.addWidget(self.cb_state)
        r1.addWidget(QLabel("图片大小"))
        self.sl = QSlider(Qt.Orientation.Horizontal); self.sl.setRange(96, 320)
        self.sl.setValue(170)
        self.sl.setMinimumWidth(70); self.sl.setMaximumWidth(120)
        self.sl.setToolTip("连续拖动：列数与布局实时重算")
        self.sl.valueChanged.connect(self._on_size)
        r1.addWidget(self.sl)
        # 4.1 是"审主图"，不是"浏览所有版本"：一个商品只留一张
        self.ck_one = QCheckBox("每商品一张")
        self.ck_one.setChecked(True)
        self.ck_one.setToolTip(
            "勾选（默认）：一个商品只显示一张 —— 优先「已确认的当前主图」，"
            "没确认过就取序号最小的那张。历史版本请到右侧工作台里看。\n"
            "取消勾选：显示库里全部 role='main' 记录（含历史版本）。")
        self.ck_one.toggled.connect(
            lambda *_: self.refresh(pack=self._pack(), q=self.ed_q.text().strip()))
        r1.addWidget(self.ck_one)
        r1.addStretch(1)
        # 顶部统一显示：总数 / 本地可读 / 缺失（要求 §五.3）
        # ★ 必须松掉 minimumSizeHint：否则这行长文本会把整页最小宽度顶到 1700px，
        #   右侧工作台被挤成 360px（实测：page.minimumSizeHint()=1710）。
        from PyQt6.QtWidgets import QSizePolicy
        self.lb_count = QLabel("—"); self.lb_count.setStyleSheet("color:#8b96a4;")
        self.lb_count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lb_count.setMinimumWidth(80)
        self.lb_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        r1.addWidget(self.lb_count, 1)
        root.addLayout(r1)

        r2 = QHBoxLayout(); r2.setContentsMargins(8, 0, 8, 6); r2.setSpacing(6)
        self.btn_refresh = QPushButton("刷新 F5"); self.btn_refresh.clicked.connect(self._reload_click)
        self.btn_all = QPushButton("全选"); self.btn_all.clicked.connect(lambda: self.wall.select_all(True))
        self.btn_none = QPushButton("取消选择"); self.btn_none.clicked.connect(lambda: self.wall.select_all(False))
        self.btn_prev = QPushButton("◀ 上一张"); self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next = QPushButton("下一张待审核 ▶"); self.btn_next.clicked.connect(self._next_todo)
        self.btn_open = QPushButton("独立窗口精修")
        self.btn_open.setToolTip("大图精修用；日常审核请直接点右侧工作台")
        self.btn_open.clicked.connect(self._open_one)
        self.btn_ok_orig = QPushButton("批量确认：原图即合格")
        self.btn_ok_orig.clicked.connect(self._confirm_orig)
        self.btn_ok_orig.setToolTip("只写审核记录，不生成版本、不改主图指针")
        # ★ 2026-10-26 快速通道：不进右侧工作台，直接在缩略图上标 + 一键批量消除
        self.btn_mark = QPushButton("🪄 标记消除")
        self.btn_mark.setCheckable(True)
        self.btn_mark.setToolTip(
            "打开后：直接在缩略图上拖框，标出要抹掉的地方（logo / 促销大字）。\n"
            "可以连着标很多张，标完点「⚡ 应用 N 张」一次性全修完 —— 不用进右侧对比、"
            "不用逐张确认。\n再点一次关闭，恢复正常「单击=右侧修补」。")
        self.btn_mark.toggled.connect(self._on_mark_mode)
        self.btn_apply = QPushButton("⚡ 应用 0 张")
        self.btn_apply.setEnabled(False)
        self.btn_apply.setToolTip("把已标记的图一次性用 LaMa 消除（约 3 秒/张，后台跑不卡界面）")
        self.btn_apply.clicked.connect(self._apply_marks)
        self.btn_clear_mk = QPushButton("清除标记")
        self.btn_clear_mk.setEnabled(False)
        self.btn_clear_mk.clicked.connect(lambda: self.wall.clear_marks())
        # ★ 2026-10-27 用户要求：一边标记、一边后台预热模型，标完刚好加载完，不用干等。
        #   模型 208MB / 首次加载约 28.8 秒（实测），之后每张只要 3 秒。
        self.btn_warm = QPushButton("🔥 预热模型")
        self.btn_warm.setToolTip(
            "先在后台把 AI 模型加载进内存（约 30 秒，不卡界面）。\n"
            "做法：点它 → 立刻开始拖框标记，标完点「⚡ 应用」时模型通常已经就绪，\n"
            "省掉第一张干等 30 秒。按钮会显示「加载中…」→「✓ 已就绪(28.6s)」。\n"
            "重复点不会重复加载（已就绪时直接提示）。")
        self.btn_warm.clicked.connect(self._warm_model)
        # ★ 2026-10-27 用户要求：日志做成右侧独立浮动窗（可缩可放、可保存）
        self.btn_log = QPushButton("📜 日志")
        self.btn_log.setCheckable(True)
        self.btn_log.setToolTip(
            "打开右侧独立日志窗（竖版、可缩可放）。\n"
            "批量消除的每一步、每张图的失败原因、耗时、异常栈都会记在里面，\n"
            "同时落盘到 data\\logs\\wall41-YYYYMMDD.log —— 出问题时我直接读那个文件。")
        self.btn_log.toggled.connect(self._on_log_toggle)
        for w in (self.btn_refresh, self.btn_all, self.btn_none, self.btn_prev, self.btn_next,
                  self.btn_open, self.btn_ok_orig,
                  self.btn_mark, self.btn_apply, self.btn_clear_mk,
                  self.btn_warm, self.btn_log):
            r2.addWidget(w)
        r2.addStretch(1)
        self.lb_sel = QLabel("选中 0 张"); self.lb_sel.setStyleSheet("color:#8b96a4;")
        r2.addWidget(self.lb_sel)
        root.addLayout(r2)

        self.wall = VirtualWall(self.thumb_dir, self)
        self.wall.picked.connect(self._pick)
        self.wall.opened.connect(self.open_editor)
        self.wall.selChanged.connect(self._update_sel)
        self.wall.detailRequested.connect(self.open_detail)
        self.wall.marksChanged.connect(self._update_marks)
        self.wall.verticalScrollBar().valueChanged.connect(lambda *_: self._ensure_visible_thumbs())
        root.addWidget(self.wall, 1)

        self.lb_tip = QLabel("单击=右侧修补 · 双击=大窗精修 · 悬停1秒看详情 · ★=当前发布主图 · "
                             "右键=看详情图")
        self.lb_tip.setStyleSheet("color:#8b96a4; padding:4px 8px;")
        self.lb_tip.setWordWrap(True)
        # 失败明细可能是多行 —— 给足高度，并允许选中复制（贴给我看）
        self.lb_tip.setMinimumHeight(18)
        self.lb_tip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lb_tip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.lb_tip)

    # 供右侧工作台挂钩（由 tab_wall_main 连接）
    def set_edit_handler(self, fn):
        """fn(row: dict) —— 单击某张图时调用（右侧工作台加载）。"""
        self._edit_handler = fn

    # 供右侧工作台挂钩：右键 → 在右侧区域打开该商品的详情图（不弹窗）
    def set_detail_handler(self, fn):
        """fn(pid: int) —— 右键某张主图时调用。"""
        self._detail_handler = fn

    _edit_handler = None
    _detail_handler = None

    def _pack(self) -> str:
        d = self.cb_pack.currentData()
        return "" if d in (None, "") else str(d)

    def _on_size(self, v):
        self.wall.set_thumb_size(int(v))
        self._ensure_visible_thumbs()

    def _ensure_visible_thumbs(self):
        self.wall._ensure_thumbs()

    # ---------------- 数据
    def _reload_click(self):
        self.refresh(pack=self._pack(), q=self.ed_q.text().strip())

    def refresh(self, pack: str = "", q: str = ""):
        state = self.cb_state.currentText() or STATE_ALL
        try:
            rows = self.lib.mains(pack=pack, q=q, state=state)
        except Exception as e:         # noqa: BLE001
            rows = []
            self.lb_tip.setText("读库失败：%s: %s" % (type(e).__name__, e))
        one = bool(getattr(self, "ck_one", None) and self.ck_one.isChecked())
        n_all = len(rows)
        if one:
            rows = self._one_per(rows)
        self.rows = rows
        self.order = [int(r["image_id"]) for r in rows]
        self.wall.set_rows(rows)
        self._ensure_visible_thumbs()
        n = len(rows)
        local = sum(1 for r in rows if r.get("exists"))
        if one:
            self.lb_count.setText("主图 %d · 本地 %d · 缺 %d" % (n, local, n - local))
            self.lb_count.setToolTip(
                "每商品只显示一张（本包 role=main 共 %d 条；含历史版本请取消勾选）" % n_all)
        else:
            self.lb_count.setText("主图 %d（含历史）· 本地 %d · 缺 %d" % (n, local, n - local))
            self.lb_count.setToolTip("显示库里全部 role='main' 记录，含历史版本")
        self.lb_count.setToolTip(self.lb_count.toolTip() + " ｜ 缺失的图进不了修补流程（不会自动下载）")
        self._update_sel()

    @staticmethod
    def _one_per(rows: list[dict]) -> list[dict]:
        """一个商品只留一张主图。

        取舍顺序（**只按库里已有的字段判断，不猜**）：
          ① `is_confirmed_main`（= p.main_image_id 指向它）优先
          ② 都没有指针时取 `sequence` 最小的那张
          ③ 再相同则取 image_id 最小的（稳定，不会每次刷新换一张）
        保持原始行顺序输出，照片墙不会因过滤而乱序。
        """
        best: dict[int, dict] = {}
        for r in rows:
            try:
                pid = int(r.get("product_id") or 0)
            except Exception:          # noqa: BLE001
                continue
            cur = best.get(pid)
            if cur is None:
                best[pid] = r
                continue
            if bool(r.get("is_confirmed_main")) != bool(cur.get("is_confirmed_main")):
                if r.get("is_confirmed_main"):
                    best[pid] = r
                continue
            k_new = (int(r.get("sequence") or 0), int(r.get("image_id") or 0))
            k_cur = (int(cur.get("sequence") or 0), int(cur.get("image_id") or 0))
            if k_new < k_cur:
                best[pid] = r
        keep = {id(v) for v in best.values()}
        return [r for r in rows if id(r) in keep]

    # ---------------- 选择
    def _pick(self, iid: int, mods: int):
        self.wall.click(iid, mods)
        if self._edit_handler is not None and len(self.wall.sel) == 1:
            r = self.wall.by_iid.get(int(iid))
            if r:
                try:
                    self._edit_handler(r)
                except Exception as e:  # noqa: BLE001
                    self.lb_tip.setText("右侧工作台加载失败：%s: %s" % (type(e).__name__, e))

    def _sel_all(self):
        self.wall.select_all(True)

    def _sel_none(self):
        self.wall.select_all(False)

    def _update_sel(self):
        n = len(self.wall.sel)
        self.lb_sel.setText("选中 %d 张" % n)
        self.btn_open.setEnabled(bool(self.wall.order))
        self.btn_ok_orig.setEnabled(bool(n))

    # ---------------- 快速通道：缩略图上标框 → 一键批量消除
    # ★ 2026-10-26 用户要求：「流程太麻烦…一张张看，每张图把左上角那个 logo 框出来，
    #   框完了统一一个按钮，把十多张图全都应用修补。不用进右侧对比修补，
    #   直接在左边预览图里面画，画了也不用确认。」
    #   所以这里**完全绕开右侧工作台和逐张确认**：标 → 攒 → 一次跑完。
    def _on_log_toggle(self, on: bool):
        """打开/关闭 4.1 自己的日志浮动窗（右侧竖版，可缩可放）。"""
        lw = getattr(self, "logw", None)
        if lw is None:
            self.lb_tip.setText("日志窗不可用（页签未接 logwin）")
            return
        try:
            if on:
                lw.show()
                lw.raise_()
            else:
                lw.hide()
        except Exception as e:          # noqa: BLE001
            self.lb_tip.setText("日志窗打开失败：%s: %s" % (type(e).__name__, e))

    def _on_mark_mode(self, on: bool):
        self.wall.set_mark_mode(bool(on))
        if on:
            self.btn_mark.setText("🪄 标记中…")
            self.lb_tip.setText(
                "🪄 标记模式：在缩略图上**按住左键拖一个框**，标出要抹掉的地方。\n"
                "标完一张接着标下一张（可滚动），标好的会显示红框和 ⚡。"
                "全标完点「⚡ 应用 N 张」一次性修完。"
                "（再点一次「标记中…」退出标记模式）")
        else:
            self.btn_mark.setText("🪄 标记消除")
            self.lb_tip.setText(
                "单击=右侧修补 · 双击=大窗精修 · 悬停1秒看详情 · "
                "★=当前发布主图 · 右键=看详情图")

    def _warm_model(self):
        """后台预热 LaMa（用户原话：我准备做这一行的时候点一下，它先启动模型，
        我这边边标记，标完它刚好加载完，节省时间，而不是让我干等）。"""
        if getattr(self, "_warming", False):
            self.lb_tip.setText("🔥 模型正在加载中，请稍候（已就绪会自动提示）…")
            return
        # 已就绪过就不要再跑一遍
        st = getattr(self, "_warm_state", None)
        if st and st.get("ok"):
            self.lb_tip.setText(
                "🔥 模型已就绪（上次加载 %.1f 秒）—— 现在点「⚡ 应用」不用再等。"
                % float(st.get("seconds") or 0))
            return
        self._warming = True
        self.btn_warm.setEnabled(False)
        self.btn_warm.setText("🔥 加载中…")
        t0 = time.time()
        self.lb_tip.setText(
            "🔥 正在后台加载 AI 模型（208MB，约 30 秒）—— "
            "★ 你可以现在就开始拖框标记，标完它通常已经好了，不用干等。")
        self._mark_log("🔥 预热开始：后台加载 LaMa 模型")

        page = self

        class _WarmSig(QObject):
            done = pyqtSignal(dict)

        sig = _WarmSig()
        sig.done.connect(page._on_warm_done)
        self._warm_sig = sig                    # 保住引用，否则被 GC

        class _WarmWorker(QRunnable):
            def run(self):                       # noqa: D401
                _lower_thread_priority()         # 预热不许把界面拖住
                r = {"ok": False, "seconds": 0.0, "error": "未知"}
                try:
                    L = _lama_mod()              # noqa: PLC0415
                    r = L.warmup()
                except Exception as e:           # noqa: BLE001
                    r = {"ok": False, "seconds": time.time() - t0,
                         "error": "%s: %s" % (type(e).__name__, e)}
                sig.done.emit(r)

        pool = QThreadPool(self)
        pool.setMaxThreadCount(1)
        pool.start(_WarmWorker())
        self._warm_pool = pool                  # 保住引用

    def _on_warm_done(self, r: dict):
        self._warming = False
        self._warm_state = r
        self.btn_warm.setEnabled(True)
        secs = float(r.get("seconds") or 0)
        if r.get("ok"):
            self.btn_warm.setText("🔥 已就绪")
            self.btn_warm.setToolTip(
                "模型已在内存（本次加裁 %.1f 秒）。点「⚡ 应用」立刻开始处理，"
                "第一张不再等 30 秒。" % secs)
            self.lb_tip.setText(
                "✓ 模型已就绪（%.1f 秒）—— 现在标完点「⚡ 应用」就是 3 秒/张，"
                "第一张不用再等。" % secs)
            self._mark_log("✓ 预热完成：%.1f 秒 · providers=%s"
                           % (secs, r.get("providers") or "-"))
        else:
            self.btn_warm.setText("🔥 预热模型")
            msg = str(r.get("error") or "未知")
            self.lb_tip.setText("✗ 模型预热失败：%s（可直接点「应用」再试，会报更详细的原因）" % msg)
            self._mark_log("✗ 预热失败：%s" % msg)

    def _update_marks(self):
        n = len(self.wall.marks)
        self.btn_apply.setText("⚡ 应用 %d 张" % n)
        self.btn_apply.setEnabled(bool(n) and not getattr(self, "_marking", False))
        self.btn_clear_mk.setEnabled(bool(n) and not getattr(self, "_marking", False))

    def _apply_marks(self):
        marks = dict(self.wall.marks)
        items = []
        for iid, box in marks.items():
            row = self.wall.by_iid.get(int(iid))
            if row:
                items.append((int(iid), box, row))
        if not items:
            self.lb_tip.setText("⚠ 没有可处理的标记（框可能没落上：拖框时要按住左键拉动，"
                                "松手时框至少 4×4 像素）")
            return
        self._marking = True
        self.btn_apply.setEnabled(False)
        self.btn_clear_mk.setEnabled(False)
        self.btn_mark.setEnabled(False)
        self.btn_apply.setText("⚡ 正在启动… 0/%d" % len(items))
        # ★ 2026-10-27：点下去必须立刻有反应（用户原话「我都没反应，不知道它动了没动」）
        self.lb_tip.setText(
            "⚡ 准备中：共 %d 张。★ 第一张要先加载 208MB 的 AI 模型，约需 30 秒 —— "
            "这期间界面会显示「模型加载中」，请勿关闭窗口。之后约 3 秒/张。" % len(items))
        # 失败原因逐条收集，不再被后一张覆盖（这是「不知道咋失败的」的根因）
        self._mark_fails: list[tuple[int, str]] = []
        self._mark_t0 = time.time()
        self._mark_log("=" * 60)
        self._mark_log("开始批量消除：%d 张 · 引擎 LaMa" % len(items))
        for iid, box, row in items:
            self._mark_log("  排队 图#%s 框=(%.3f,%.3f,%.3f,%.3f) 路径=%s" %
                           (iid, box[0], box[1], box[2], box[3],
                            (row.get("current_local_path") or row.get("original_local_path") or "(空)")))

        sig = _MarkSig()
        sig.one.connect(self._on_mark_one)
        sig.done.connect(self._on_marks_done)
        self._mark_sig = sig           # 保住引用，否则信号会被 GC
        self._mark_total = len(items)
        self._mark_ok = 0
        self._mark_fail = 0
        self._mark_first = True
        w = _MarkWorker(items, self.api, sig, log=self._mark_log)
        self._mark_pool = QThreadPool(self)
        self._mark_pool.setMaxThreadCount(1)     # 单线程：LaMa 吃 CPU，别并发
        self._mark_pool.start(w)

    def _mark_log(self, s: str):
        """批量消除的落盘日志（用户原话：日志要能保存，后续你还能读来修）。

        三条落点，各自服务不同读者：
          ① data/logs/mark-YYYYMMDD.log  —— 纯文本、带秒级时间，适合我 grep
          ② 4.1 日志浮动窗（logw）        —— 人在界面里看，按级别上色
          ③ 主窗口 run.log               —— 全台日志流，跨页签对时间线
        """
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), s)
            with open(os.path.join(LOG_DIR, "mark-%s.log" % time.strftime("%Y%m%d")),
                      "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:                            # noqa: BLE001
            pass
        # ② 4.1 浮动窗
        try:
            lw = getattr(self, "logw", None)
            if lw is not None:
                t = str(s)
                if "✗" in t or "失败" in t or "异常" in t:
                    lw.error(t)
                elif "✓" in t or "完成" in t:
                    lw.ok(t)
                elif "⚠" in t or "开始" in t:
                    lw.step(t)
                else:
                    lw.detail(t)
        except Exception:                            # noqa: BLE001
            pass
        # ③ 主窗口日志流
        try:
            lg = getattr(self.api, "log", None)
            if callable(lg):
                lg("[4.1消除] " + s)
        except Exception:                            # noqa: BLE001
            pass

    def _on_mark_one(self, iid: int, ok: bool, msg: str):
        if ok:
            self._mark_ok += 1
            self.wall.drop_mark(int(iid))        # 成功的从标记里移除（红框消失）
        else:
            self._mark_fail += 1
            self._mark_fails.append((int(iid), msg))
        done = self._mark_ok + self._mark_fail
        # ★ 关键：失败原因累积展示，不再用一句 setText 覆盖掉上一张
        head = "⚡ 修补中 %d/%d（成功 %d · 失败 %d）" % (
            done, self._mark_total, self._mark_ok, self._mark_fail)
        if self._mark_fails:
            last_iid, last_msg = self._mark_fails[-1]
            head += "\n✗ 图#%s：%s" % (last_iid, last_msg)
        if done == 1 and self._mark_first:
            self._mark_first = False
            head += "\n（首张含模型加载，约 30 秒属正常）"
        self.lb_tip.setText(head)
        self.btn_apply.setText("⚡ 修补中 %d/%d" % (done, self._mark_total))

    def _on_marks_done(self, ok: int, fail: int):
        self._marking = False
        self.btn_mark.setEnabled(True)
        self._update_marks()
        elapsed = time.time() - getattr(self, "_mark_t0", time.time())
        # ★ 汇总必须列出**每一条**失败原因（之前只写「失败 N 张」，等于没说）
        lines = ["⚡ 批量消除完成：成功 %d 张 · 失败 %d 张 · 耗时 %.1f 秒。"
                 % (ok, fail, elapsed)]
        if ok:
            lines.append("成功的已存为新版本**并自动换图**，照片墙上是修补后的新图"
                         "（原主图退为历史版本，可随时撤销重做）。")
        fails = getattr(self, "_mark_fails", [])
        if fails:
            lines.append("")
            lines.append("失败明细（红框保留，可重试）：")
            for iid, msg in fails[:12]:
                lines.append("  ✗ 图#%s：%s" % (iid, msg))
            if len(fails) > 12:
                lines.append("  …另有 %d 张，见日志" % (len(fails) - 12))
            lines.append("")
            lines.append("完整日志：%s" % LOG_DIR)
        self.lb_tip.setText("\n".join(lines))
        self._mark_log("本次结束：成功 %d · 失败 %d · 耗时 %.1fs" % (ok, fail, elapsed))
        # 缩略图缓存清掉，让改过的图重新出缩略
        try:
            self.wall._pix.clear()
            self.wall._want.clear()
            self.wall._relayout(keep_scroll=True)
        except Exception:  # noqa: BLE001
            pass
        # ★ 2026-10-27：主图指针已经切到新版本（save_version 里自动确认），
        #   但界面上的 self.rows 还是旧的 —— 不重读库就永远显示老图。
        #   这里重读一次（只读库，不重算缩略图），用户立刻就能看到换过的图。
        try:
            self.refresh(pack=self._pack(), q=self.ed_q.text().strip())
            self._mark_log("已重读主图指针：照片墙现在显示的是修补后的新图")
        except Exception as e:  # noqa: BLE001
            self._mark_log("重读主图失败（不影响已写入的结果）：%s" % e)
        self._ensure_visible_thumbs()
        # 失败数量多时弹一次摘要，避免被小字漏看
        if fail:
            try:
                head = "批量消除：成功 %d · 失败 %d" % (ok, fail)
                body = "\n".join("图#%s：%s" % (i, m) for i, m in fails[:10])
                if len(fails) > 10:
                    body += "\n…另有 %d 张，详见 %s" % (len(fails) - 10, LOG_DIR)
                QMessageBox.warning(self, head, body)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 右键：看这个商品的详情图
    def open_detail(self, image_id: int):
        r = self.wall.by_iid.get(int(image_id)) or {}
        pid = int(r.get("product_id") or 0)
        if not pid:
            return
        # ★ 2026-10-27 主人要求：「右击一个主图，应该在右边区域打开详情图，不要浮动窗」。
        #   外层（tab_wall_main）会把右侧工作台的 load_detail 挂上来；
        #   挂了就走右边，没挂（单测/旧调用）才退回独立窗口，保证不破坏旧行为。
        h = self._detail_handler
        if callable(h):
            try:
                h(pid)
                return
            except Exception as e:              # noqa: BLE001
                self.lb_tip.setText("右侧详情图打开失败，已回退独立窗口：%s: %s"
                                    % (type(e).__name__, e))
        dlg = DetailDialog(self.lib, pid, self.api, self)
        dlg.exec()
        # 详情图改完可能影响审核状态，刷新一次计数
        self._update_marks()

    # ---------------- 导航
    def _step(self, d: int):
        if not self.order:
            return
        cur = next(iter(self.wall.sel)) if len(self.wall.sel) == 1 else self.order[0]
        try:
            i = self.order.index(int(cur))
        except ValueError:
            i = 0
        i = max(0, min(len(self.order) - 1, i + d))
        self.opened_from_nav(self.order[i])

    def opened_from_nav(self, iid: int):
        iid = int(iid)
        self.wall.sel = {iid}
        self.wall.ensure(iid)
        self.wall.cv.update()
        self._update_sel()
        self._ensure_visible_thumbs()
        if self._edit_handler is not None:
            r = self.wall.by_iid.get(iid)
            if r:
                try:
                    self._edit_handler(r)
                except Exception as e:  # noqa: BLE001
                    self.lb_tip.setText("右侧工作台加载失败：%s: %s" % (type(e).__name__, e))

    def _next_todo(self):
        if not self.order:
            return
        start = 0
        if self.wall.sel:
            try:
                start = self.order.index(next(iter(self.wall.sel))) + 1
            except (ValueError, StopIteration):
                start = 0
        for k in range(len(self.order)):
            iid = self.order[(start + k) % len(self.order)]
            r = self.wall.by_iid[iid]
            if (r.get("state") or STATE_NONE) in (STATE_NONE, STATE_DRAFT, STATE_SAVED):
                self.opened_from_nav(iid)
                return
        QMessageBox.information(self, "都看过了", "当前筛选里没有待审核的主图。")

    def _open_one(self):
        if len(self.wall.sel) == 1:
            self.open_editor(next(iter(self.wall.sel)))
        elif self.order:
            self.open_editor(self.order[0])

    # ---------------- 独立窗口精修（保留为放大模式，不是默认入口）
    def open_editor(self, image_id: int):
        r = self.wall.by_iid.get(int(image_id))
        if not r:
            return
        if not r.get("exists"):
            QMessageBox.warning(self, "没有本地文件",
                                "这张图还没下载到本地：\n商品 %s · image_id=%s\n%s"
                                % (r.get("product_id"), image_id, r.get("abs_path") or ""))
            return
        try:
            from .editor import EditorWindow
        except Exception as e:         # noqa: BLE001
            QMessageBox.critical(self, "编辑器加载失败", "%s: %s" % (type(e).__name__, e))
            return
        try:
            dlg = EditorWindow(r, self.api, self)
            dlg.exec()
        except Exception as e:         # noqa: BLE001
            QMessageBox.critical(self, "编辑器异常", "%s: %s" % (type(e).__name__, e))
        finally:
            self.refresh(pack=self._pack(), q=self.ed_q.text().strip())

    # ---------------- 批量确认原图
    def _confirm_orig(self):
        ids = [i for i in self.wall.sel if self.wall.by_iid.get(i, {}).get("exists")]
        if not ids:
            QMessageBox.information(self, "没有可确认的", "选中的图里没有本地文件。")
            return
        ok = QMessageBox.question(
            self, "批量确认原图",
            "将对 %d 张主图写入「原图即合格」的审核记录。\n\n"
            "它只记一笔台账，**不生成版本、不改主图指针、不动任何图片文件**。\n"
            "确认这批图你都看过、内容没有问题？" % len(ids))
        if ok != QMessageBox.StandardButton.Yes:
            return
        done = fail = 0
        errs = []
        for iid in ids:
            r = self.wall.by_iid[iid]
            try:
                got = self.api.confirm_original(r["product_id"], iid)
            except Exception as e:     # noqa: BLE001
                got = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if got.get("ok"):
                done += 1
            else:
                fail += 1
                errs.append("#%s %s" % (iid, got.get("error")))
        self.refresh(pack=self._pack(), q=self.ed_q.text().strip())
        msg = "已记录 %d 张 · 失败 %d 张" % (done, fail)
        if errs:
            msg += "\n\n前几条失败原因：\n" + "\n".join(errs[:5])
        QMessageBox.information(self, "批量确认结果", msg)
        self.lb_tip.setText(msg.replace("\n\n", " ｜ "))

    # ---------------- 其他
    def fill_packs(self):
        try:
            packs = self.lib.packs()
        except Exception:              # noqa: BLE001
            packs = []
        cur = self._pack()
        self.cb_pack.blockSignals(True)
        self.cb_pack.clear()
        self.cb_pack.addItem("（全部数据包）", "")
        for p in packs:
            self.cb_pack.addItem("%s · %d 张" % (p["code"], p["n"]), p["code"])
        if cur:
            i = self.cb_pack.findData(cur)
            if i >= 0:
                self.cb_pack.setCurrentIndex(i)
        self.cb_pack.blockSignals(False)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._ensure_visible_thumbs()
