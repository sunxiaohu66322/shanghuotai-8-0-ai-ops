# -*- coding: utf-8 -*-
"""4.1 · 主图局部修补编辑器（阶段 C）

定位：**看准、改对、留证**。不追求 Photoshop 全能，只做「主图上不想要的那一小块」
——促销字、贴片、水印、二维码框——在本地抹干净，并留下可复核的遮罩与参数。

不做的三件事（写死）：
  · 不覆盖原图：编辑器只读源文件，一切产物写新文件（`repair_to_file` 同款安全闸）
  · 不自动确认：`确认合格` 必须人手点，且写台账
  · 不假装成功：修补失败 → 状态 `repair_failed`，保留原图与上一版

坐标铁律：**所有选区都存「原图像素坐标」**，随缩放/平移变换的只是显示。
这样框选不会错位，导出遮罩也能直接喂给 `repair.repair()`。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import time

import numpy as np
from PyQt6.QtCore import QObject, QPoint, QRectF, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

# ---------------- 后端模块加载（只读调用 server.v3）
# 为什么不直接写 `from server.v3 import maskgen`：
#   `paths.ensure_backend_path()` 只把 `taobao-picker\server` 加进 sys.path，那时顶层包名是
#   `v3`，**不是** `server`；`server` 能不能导入取决于别处有没有顺手把 `taobao-picker` 也加进去。
#   万一某条启动顺序 / 打包产物下 `server` 不可导入，异常会落在 Qt 事件循环里被吞掉，
#   界面表现就是「拖了矩形，什么都没发生」—— 这是最难查的一种「没效果」。
#   所以这里双路探测 + 文件级兜底，且调用方一律把异常显示到状态栏，不再静默。
_V3_CACHE: dict = {}


def v3mod(name: str):
    """取 `server.v3.<name>`：先 server.v3，再平铺 v3，最后按文件路径加载。"""
    hit = _V3_CACHE.get(name)
    if hit is not None:
        return hit
    errs = []
    for pkg in ("server.v3.", "v3."):
        try:
            m = importlib.import_module(pkg + name)
            _V3_CACHE[name] = m
            return m
        except Exception as e:          # noqa: BLE001
            errs.append("%s%s → %s: %s" % (pkg, name, type(e).__name__, e))
    try:                                # 兜底：直接按磁盘文件加载
        from ..paths import TAOBAO_SERVER
        f = TAOBAO_SERVER / "v3" / (name + ".py")
        if f.is_file():
            spec = importlib.util.spec_from_file_location("_v3_" + name, str(f))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            _V3_CACHE[name] = m
            return m
        errs.append("文件不存在：%s" % f)
    except Exception as e:              # noqa: BLE001
        errs.append("文件级加载失败：%s: %s" % (type(e).__name__, e))
    raise ImportError("加载 v3.%s 失败 ｜ %s" % (name, " ｜ ".join(errs)))

# ---------------- 工具
T_RECT = "rect"
T_BRUSH = "brush"
T_ERASE = "erase"
T_POLY = "poly"
T_STAMP = "stamp"

TOOL_LABEL = {T_RECT: "矩形", T_BRUSH: "画笔", T_ERASE: "橡皮", T_POLY: "套索", T_STAMP: "仿制图章"}

# 2026-10-24：画笔/橡皮/套索/图章在真机上体验不稳（卡顿、误触），按用户要求**先隐藏**。
# 代码一行没删 —— 把下面这个开关改回 True 就全部回来（Canvas 里的实现仍然完整可用）。
ENABLE_EXTRA_TOOLS = False

MAX_HIST_MB = 320           # 撤销栈内存上限（按图大小自适应深度）


def bgr_to_qimage(bgr: np.ndarray) -> QImage:
    import cv2
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def qimage_to_bgr(img: QImage) -> np.ndarray:
    import cv2
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape((h, img.bytesPerLine() // 1))[:, : 3 * w]
    arr = arr.reshape(h, w, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------- 画布
class Canvas(QWidget):
    """单张图的显示与选区编辑。缩放/平移只是显示变换，选区存原图坐标。"""

    maskEdited = pyqtSignal()
    maskError = pyqtSignal(str)                       # 画遮罩失败（导入/越界/其它）→ 显式报到状态栏
    viewChanged = pyqtSignal(float, float, float)     # zoom, ox, oy（给对侧同步）
    stampSourceSet = pyqtSignal(int, int)

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.qimg: QImage | None = None
        self.bgr: np.ndarray | None = None
        self.mask: np.ndarray | None = None          # 与编辑器共享同一个数组
        self.boxes: list[dict] = []
        self.zoom = 1.0
        self.ox = 0.0
        self.oy = 0.0
        self.show_mask = True
        # 遮罩显示样式：
        #   "fill"  = 半透明紫色填充（**只在左图「原图」用**，标出「这块会被抹掉」）
        #   "line"  = 只画选区细线边框，不填充（**右图「修补结果」用**，否则紫色盖住修补效果、
        #             看不出补得好不好）
        self.mask_style = "fill"
        self._ov_style = None
        self.show_boxes = False
        self.tool = T_RECT
        self.brush = 20
        self.expand_px = 0
        self.editable = True
        self._ov = None                              # 遮罩叠加缓存
        self._dirty_ov = True
        self._drag = None
        self._last = None
        self._tmp_rect = None
        self.stamp_pt = None                         # 采样源（原图坐标）
        self.stamp_off = None
        self.setMinimumSize(240, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------- 数据
    def set_bgr(self, bgr: np.ndarray):
        self.bgr = bgr
        self.qimg = bgr_to_qimage(bgr) if bgr is not None else None
        self._fit()

    def set_mask_ref(self, mask: np.ndarray):
        self.mask = mask
        self.dirty_overlay()

    def set_boxes(self, boxes: list[dict]):
        self.boxes = boxes or []
        self.update()

    def dirty_overlay(self):
        self._dirty_ov = True
        self.update()

    def _fit(self):
        if self.qimg is None or not self.qimg.width():
            return
        iw, ih = self.qimg.width(), self.qimg.height()
        zw = max(1, self.width() - 12) / iw
        zh = max(1, self.height() - 12) / ih
        self.zoom = max(0.02, min(zw, zh))
        self.ox = (self.width() - iw * self.zoom) / 2
        self.oy = (self.height() - ih * self.zoom) / 2
        self.viewChanged.emit(self.zoom, self.ox, self.oy)
        self.update()

    def set_view(self, zoom: float, ox: float, oy: float):
        self.zoom, self.ox, self.oy = float(zoom), float(ox), float(oy)
        self.update()

    def zoom_100(self):
        if self.qimg is None:
            return
        cx, cy = self.width() / 2, self.height() / 2
        ix, iy = self.to_image(QPoint(int(cx), int(cy))) or (0, 0)
        self.zoom = 1.0
        self.ox = cx - ix * self.zoom
        self.oy = cy - iy * self.zoom
        self.viewChanged.emit(self.zoom, self.ox, self.oy)
        self.update()

    def zoom_by(self, factor: float, anchor: QPoint | None = None):
        if self.qimg is None:
            return
        a = anchor or QPoint(self.width() // 2, self.height() // 2)
        ip = self.to_image(a)
        self.zoom = max(0.02, min(16.0, self.zoom * factor))
        if ip:
            self.ox = a.x() - ip[0] * self.zoom
            self.oy = a.y() - ip[1] * self.zoom
        self.viewChanged.emit(self.zoom, self.ox, self.oy)
        self.update()

    # ---------------- 坐标
    def to_image(self, p: QPoint) -> tuple[int, int] | None:
        if self.qimg is None or self.zoom <= 0:
            return None
        x = int((p.x() - self.ox) / self.zoom)
        y = int((p.y() - self.oy) / self.zoom)
        if 0 <= x < self.qimg.width() and 0 <= y < self.qimg.height():
            return x, y
        return None

    def to_image_clamped(self, p: QPoint) -> tuple[int, int] | None:
        """鼠标拖到图片外面时也不丢坐标 —— 夹到图像边界内。

        矩形框选必须用它：以前用的是 to_image()，出界就返回 None，回退成起点坐标，
        结果「拖到图边就变成零面积」，看起来就像选区错位/变形。
        """
        if self.qimg is None or self.zoom <= 0:
            return None
        x = int(round((p.x() - self.ox) / self.zoom))
        y = int(round((p.y() - self.oy) / self.zoom))
        x = max(0, min(self.qimg.width() - 1, x))
        y = max(0, min(self.qimg.height() - 1, y))
        return x, y

    def _img_rect(self) -> QRectF:
        if self.qimg is None:
            return QRectF(0, 0, 0, 0)
        return QRectF(self.ox, self.oy,
                      self.qimg.width() * self.zoom, self.qimg.height() * self.zoom)

    # ---------------- 绘制
    def _overlay_qimage(self) -> QImage | None:
        if self.mask is None:
            return None
        if not self._dirty_ov and self._ov is not None and self._ov_style == self.mask_style:
            return self._ov
        m = (self.mask > 0)
        if not m.any():
            self._ov = None
            self._dirty_ov = False
            self._ov_style = self.mask_style
            return None
        h, w = m.shape[:2]
        rgba = np.zeros((h, w, 4), np.uint8)
        if self.mask_style == "line":
            # 只要细线轮廓：不填充，右图才能看清修补结果
            try:
                import cv2
                m8 = m.astype(np.uint8)
                er = cv2.erode(m8, np.ones((3, 3), np.uint8), iterations=1)
                edge = (m8 - er) > 0
            except Exception:          # noqa: BLE001
                edge = m
            rgba[edge] = (255, 90, 90, 235)
        else:
            rgba[m] = (255, 0, 200, 120)
        self._ov = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()
        self._dirty_ov = False
        self._ov_style = self.mask_style
        return self._ov

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#14171c"))
        if self.qimg is None:
            p.setPen(QColor("#6d7887"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "没有可显示的图片")
            return
        r = self._img_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        p.drawImage(r, self.qimg, QRectF(0, 0, self.qimg.width(), self.qimg.height()))

        if self.show_mask:
            ov = self._overlay_qimage()
            if ov is not None:
                p.drawImage(r, ov, QRectF(0, 0, ov.width(), ov.height()))

        if self.show_boxes and self.boxes:
            p.setPen(QPen(QColor(80, 200, 255, 200), 1))
            for b in self.boxes:
                box = b.get("box") or b.get("rect") or b
                try:
                    x, y, bw, bh = [float(v) for v in box[:4]]
                except Exception:      # noqa: BLE001
                    continue
                p.drawRect(QRectF(self.ox + x * self.zoom, self.oy + y * self.zoom,
                                  bw * self.zoom, bh * self.zoom))

        if self._tmp_rect is not None:
            p.setPen(QPen(QColor(255, 220, 0), 1, Qt.PenStyle.DashLine))
            p.drawRect(self._tmp_rect)

        if self.stamp_pt is not None:
            sxp = self.ox + self.stamp_pt[0] * self.zoom
            syp = self.oy + self.stamp_pt[1] * self.zoom
            p.setPen(QPen(QColor(120, 255, 140), 1, Qt.PenStyle.DotLine))
            rr = max(3.0, self.brush * self.zoom / 2)
            p.drawEllipse(QRectF(sxp - rr, syp - rr, rr * 2, rr * 2))
            p.drawLine(QPoint(int(sxp - 8), int(syp)), QPoint(int(sxp + 8), int(syp)))
            p.drawLine(QPoint(int(sxp), int(syp - 8)), QPoint(int(sxp), int(syp + 8)))

        # 角标：工具 / 缩放 / 尺寸
        p.setPen(QColor("#9aa6b5"))
        if self.qimg is not None:
            p.drawText(8, self.height() - 8,
                       "%s · %.0f%% · %d×%d" % (TOOL_LABEL.get(self.tool, self.tool),
                                                self.zoom * 100,
                                                self.qimg.width(), self.qimg.height()))

    # ---------------- 交互
    def _paint_circle(self, ix: int, iy: int, value: int):
        maskgen = v3mod("maskgen")
        if self.mask is None:
            return
        maskgen.draw_brush(self.mask, [(ix, iy)], radius=max(1, self.brush), value=value)

    def _stamp_to(self, ix: int, iy: int):
        if self.stamp_off is None or self.bgr is None:
            return
        r = max(1, int(self.brush))
        sx, sy = ix - self.stamp_off[0], iy - self.stamp_off[1]
        h, w = self.bgr.shape[:2]
        x0, x1 = max(0, ix - r), min(w, ix + r + 1)
        y0, y1 = max(0, iy - r), min(h, iy + r + 1)
        sx0, sy0 = max(0, sx - r), max(0, sy - r)
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        circle = ((xx - ix) ** 2 + (yy - iy) ** 2) <= r * r
        if not circle.any():
            return
        src = self.bgr[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
        dst = self.bgr[y0:y1, x0:x1]
        if src.shape != dst.shape:
            return
        a = circle[..., None].astype(np.float32) * float(self._stamp_opacity)
        self.bgr[y0:y1, x0:x1] = (dst * (1 - a) + src * a).astype(np.uint8)
        self.qimg = bgr_to_qimage(self.bgr)

    _stamp_opacity = 0.9

    def mousePressEvent(self, e):  # noqa: N802
        if self.qimg is None:
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self._drag = ("pan", e.position().toPoint(), self.ox, self.oy)
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.editable:
            return
        ip = self.to_image(e.position().toPoint())
        if ip is None:
            # 矩形允许从图片外起手（用户常常从留白处拖进来）；其它工具必须落在图内
            if self.tool == T_RECT:
                ip = self.to_image_clamped(e.position().toPoint())
                if ip is None:
                    return
            else:
                return
        if self.tool == T_STAMP and (e.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.stamp_pt = ip
            self.stampSourceSet.emit(ip[0], ip[1])
            self.update()
            return
        if self.tool == T_RECT:
            self._drag = ("rect", e.position().toPoint(), ip)
            self._tmp_rect = None
            return
        if self.tool == T_POLY:
            if self._drag is None:
                self._drag = ("poly", e.position().toPoint(), [ip])
            else:
                self._drag[2].append(ip)
            self.update()
            return
        # 画笔 / 橡皮 / 图章：落笔前先让编辑器压一次历史
        self.maskEdited.emit()
        self._drag = ("paint", e.position().toPoint(), ip)
        self._last = ip
        if self.tool == T_BRUSH:
            self._paint_circle(ip[0], ip[1], 255)
        elif self.tool == T_ERASE:
            self._paint_circle(ip[0], ip[1], 0)
        elif self.tool == T_STAMP:
            if self.stamp_pt and self.stamp_off is None:
                self.stamp_off = (self.stamp_pt[0] - ip[0], self.stamp_pt[1] - ip[1])
            self._stamp_to(ip[0], ip[1])
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag is None:
            return
        kind = self._drag[0]
        pt = e.position().toPoint()
        if kind == "pan":
            _, p0, ox0, oy0 = self._drag
            self.ox = ox0 + (pt.x() - p0.x())
            self.oy = oy0 + (pt.y() - p0.y())
            self.viewChanged.emit(self.zoom, self.ox, self.oy)
            self.update()
            return
        if kind == "rect":
            p0 = self._drag[1]
            # 2026-10-24 修：原来写 abs(pt.x() - x0)，x0 已经是两点的较小值，
            # 往左/上拖时算出来恒为 0 —— 橡皮筋被压成一条线，看着就是"选区变形"。
            # 必须用相对起点的位移。
            self._tmp_rect = QRectF(min(p0.x(), pt.x()), min(p0.y(), pt.y()),
                                    abs(pt.x() - p0.x()), abs(pt.y() - p0.y()))
            self.update()
            return
        ip = self.to_image(pt)
        if ip is None:
            return
        if kind == "paint":
            if self.tool == T_STAMP:
                self._stamp_to(ip[0], ip[1])
            elif self._last is not None:
                # 快速拖动时插值，避免出现断续的点
                lx, ly = self._last
                n = int(max(abs(ip[0] - lx), abs(ip[1] - ly)) / max(1, self.brush / 3)) + 1
                for i in range(1, n + 1):
                    x = int(lx + (ip[0] - lx) * i / n)
                    y = int(ly + (ip[1] - ly) * i / n)
                    self._paint_circle(x, y, 255 if self.tool == T_BRUSH else 0)
                self._last = ip
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._drag is None:
            return
        kind = self._drag[0]
        if kind == "pan":
            self._drag = None
            return
        if kind == "rect":
            p0 = self._drag[1]
            ip0 = self._drag[2]
            # 松开点出界时用夹取版，别回退成起点（否则选区直接消失）
            ip1 = self.to_image_clamped(e.position().toPoint()) or ip0
            x0, x1 = sorted([ip0[0], ip1[0]])
            y0, y1 = sorted([ip0[1], ip1[1]])
            # 至少 2×2 像素才落遮罩（防手抖的单击误选区）
            if x1 - x0 >= 2 and y1 - y0 >= 2 and self.mask is not None:
                # ★★ 2026-10-24 修：「选区错位 / 平移」的真凶在这里。
                # maskgen.draw_rect 的第 2 个参数是 (x1, y1, x2, y2) **含端点**，
                # 而旧代码传的是 (x0, y0, 宽, 高) —— 把宽高当成了右下角坐标。
                # 于是选区落在 (x0,y0)-(w,h) 这个完全不相干的位置：
                #   x0>w 时排序后变成 [w, x0]，看上去就是"整体平移 + 变形"。
                # ★★ 2026-10-26：调用包了 try —— 以前这里任何异常都被 Qt 事件循环吞掉，
                # 界面既不报错也不落遮罩，用户只能看到「拖了矩形没效果」。
                try:
                    before = int((self.mask > 0).sum())
                    v3mod("maskgen").draw_rect(self.mask, (x0, y0, x1, y1), 255)
                    after = int((self.mask > 0).sum())
                    if after <= before:
                        self.maskError.emit(
                            "选区没有写进遮罩：图像坐标 (%d,%d)-(%d,%d) 面积 %d px，"
                            "遮罩前后都是 %d 像素" % (x0, y0, x1, y1, after - before, before))
                    self._after_edit()
                except Exception as ex:          # noqa: BLE001
                    self.maskError.emit("写遮罩失败（%s: %s）· 图像坐标 (%d,%d)-(%d,%d)"
                                        % (type(ex).__name__, ex, x0, y0, x1, y1))
            self._tmp_rect = None
            self._drag = None
            self.update()
            return
        if kind == "paint":
            if self.tool == T_STAMP:
                self.qimg = bgr_to_qimage(self.bgr) if self.bgr is not None else self.qimg
            self._after_edit()
        self._drag = None
        self._last = None

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self._drag and self._drag[0] == "poly":
            pts = self._drag[2]
            if len(pts) >= 3 and self.mask is not None:
                try:
                    v3mod("maskgen").draw_poly(self.mask, pts, 255)
                    self._after_edit()
                except Exception as ex:      # noqa: BLE001
                    self.maskError.emit("写遮罩失败（%s: %s）" % (type(ex).__name__, ex))
            self._drag = None
            self.update()

    def _after_edit(self):
        self.dirty_overlay()
        self.maskEdited.emit()

    def wheelEvent(self, e):  # noqa: N802
        self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15,
                     e.position().toPoint())

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)


# ---------------------------------------------------------------- 修补任务
class _Sig(QObject):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)


class _RepairTask(QRunnable):
    """修补任务（后台线程）。

    ★ 2026-10-26 重要修复：这里原来调 `v3mod("repair").repair(...)`，
      但 `server/v3/repair.py` 已被「包元信息自愈」整文件覆盖（那个文件自己的注释
      写着"原有的 imread_any / inspect 两个验收工具函数随之丢失"），
      **`repair()` / `repair_to_file()` 从此不存在** ——
      所以点「应用修补」必然 `AttributeError: module '...repair' has no attribute 'repair'`。
      这是「修补功能全废」的直接原因。

    现在改成两条独立的引擎：
      · lama  → `inpaint_lama.inpaint()` 生成式大遮罩修补（PS 内容识别填充同路线）
      · telea / ns → 直接 `cv2.inpaint()`（不再依赖那个被覆盖的模块）
    inspect 仍用 `repair.inspect`（它被补回来了），失败不影响出图。
    """

    def __init__(self, sig, bgr, mask, method: str, radius: int):
        super().__init__()
        self.sig, self.bgr, self.mask, self.method, self.radius = sig, bgr, mask, method, radius

    def run(self):
        try:
            import cv2

            if self.method == "lama":
                L = v3mod("inpaint_lama")
                av = L.available()
                if not av.get("ok"):
                    raise RuntimeError("LaMa 不可用：%s" % av.get("why"))
                out = L.inpaint(self.bgr, self.mask)
                st = L.last_stats()
                meta = {"engine": "lama", "seconds": st.get("seconds"),
                        "mask_px": st.get("mask_px"), "model": av.get("model")}
            else:
                flag = cv2.INPAINT_NS if self.method == "ns" else cv2.INPAINT_TELEA
                out = cv2.inpaint(self.bgr, self.mask, float(self.radius), flag)
                meta = {"engine": self.method, "radius": self.radius,
                        "mask_px": int((self.mask > 0).sum())}

            insp = {}
            try:
                insp = v3mod("repair").inspect(self.bgr, out, self.mask)
            except Exception as ie:                 # noqa: BLE001
                insp = {"error": "%s: %s" % (type(ie).__name__, ie)}
            self.sig.done.emit({"bgr": out, "meta": meta, "inspect": insp})
        except Exception as e:          # noqa: BLE001
            self.sig.fail.emit("%s: %s" % (type(e).__name__, e))


class _WarmTask(QRunnable):
    """后台预热 LaMa（首次加载 26 秒，不预热的话第一次点修补会像卡死）。"""

    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def run(self):
        try:
            L = v3mod("inpaint_lama")
            self.cb(L.warmup())
        except Exception as e:                      # noqa: BLE001
            self.cb({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


# ---------------------------------------------------------------- 编辑面板
class EditorPanel(QWidget):
    """原图 ↔ 修补图 双栏；所有编辑以「原图像素坐标」记录。

    2026-10-24 拆成「面板」：
      · 装进 4.1 右侧固定工作台 = **默认入口**（单击左图就在这里打开）
      · 被 `EditorWindow` 包一层 = 双击后的独立大窗口（放大精修）
    **构建一次、可重复 load()** —— 旧版 `_load()` 里调 `_build()`，
    点一次「恢复上一版」会把整套控件重新造一遍。
    """

    changed = pyqtSignal(dict, str)      # row, 新状态：saved / confirmed

    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.img: dict = {}
        self.pool = QThreadPool(); self.pool.setMaxThreadCount(2)
        self.sig = _Sig()
        self.sig.done.connect(self._on_repaired)
        self.sig.fail.connect(self._on_repair_fail)

        self.orig = None
        self.result = None
        self.mask = None
        self.method, self.radius = "telea", 3
        self.regions = []
        self.hist = []
        self.hidx = -1
        self._busy = False
        self._ok_vid = 0
        self._fitted = False
        self._need_fit = False
        self._build()
        self.clear()

    # ---------------- 载入
    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._do_fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        # ★ 2026-10-26 修（用户要求）：对比图**一律左右横排**。
        # 旧版按宽度切换：宽 >= 900 才左右，窄了自动改成上下堆叠。
        # 而右侧工作台默认 560px，于是真机上永远是「改前在上、改后在下」的竖列 ——
        # 修补效果在下方那一张里，用户盯着上面那张看，自然说「没效果」。
        # 横排之后两张同高并排，左改前右改后，一眼可比。
        if self.split_inner.orientation() != Qt.Orientation.Horizontal:
            self.split_inner.setOrientation(Qt.Orientation.Horizontal)
            self.split_inner.setSizes([1000, 1000])
        self._do_fit()

    def _do_fit(self):
        """只在「刚载入 / 布局刚定下来」时自动适配一次。

        这也是坐标错位的一个隐形来源：旧版在控件还是 240×240 时就 _fit()，
        之后窗口变大了也不再算，图缩在一个角落、用户按屏幕位置去框选就会觉得偏。
        """
        if self._need_fit and self.thumb_cv.qimg is not None and self.thumb_cv.width() > 60:
            self._need_fit = False
            self._fitted = True
            self.thumb_cv._fit()
            self._sync_view(self.thumb_cv)

    def clear(self):
        """清空成占位状态（工作台还没选图时用）。"""
        self.img = {}
        self.orig = None
        self.result = None
        self.mask = None
        self.hist = []; self.hidx = -1
        self._ok_vid = 0
        self._fitted = False
        self._need_fit = False
        for cv in (self.thumb_cv, self.edit_cv):
            cv.qimg = None; cv.bgr = None
            cv.set_mask_ref(None); cv.set_boxes([]); cv.update()
        self.lb_title.setText("（左侧点一张主图）")
        self.lb_pos.setText("—")
        self.lb_quality.setText("质量检查：—")
        self.lb_ocr.setText("OCR 文字框 —")
        self._show_status("单击左侧照片墙里的主图，这张图会在这里打开。")
        self._update_sizes()
        self.btn_save.setEnabled(False)
        self.btn_ok.setEnabled(False)
        self.btn_ok_orig.setEnabled(False)
        self.btn_repair.setEnabled(False)
        self._update_btns()

    def load(self, row: dict) -> bool:
        """载入一张主图。返回 False = 没有本地文件（界面会写明原因，不弹窗打断）。"""
        import cv2
        self.img = dict(row or {})
        self._ok_vid = 0
        self.hist = []; self.hidx = -1
        p = self.img.get("abs_path") or ""
        self.lb_title.setText((self.img.get("title") or "")[:60]
                              or "image_id=%s" % self.img.get("image_id"))
        self.lb_pos.setText("商品 #%s · 图片 #%s · 序号 %s · 角色 %s"
                            % (self.img.get("product_id"), self.img.get("image_id"),
                               self.img.get("sequence"), self.img.get("role")))
        self.lb_quality.setText("质量检查：—（还没修补）")

        if not p or not os.path.isfile(p):
            self.orig = None; self.result = None; self.mask = None
            for cv in (self.thumb_cv, self.edit_cv):
                cv.qimg = None; cv.bgr = None
                cv.set_mask_ref(None); cv.update()
            self._show_status("这张图没有本地文件，不能修补（4.1 不会自动下载）：%s"
                              % (p or "（路径为空）"), bad=True)
            self._update_sizes()
            self._update_btns()
            return False

        buf = np.fromfile(p, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if bgr is None:
            self.orig = None; self.result = None; self.mask = None
            for cv in (self.thumb_cv, self.edit_cv):
                cv.qimg = None; cv.bgr = None
                cv.set_mask_ref(None); cv.update()
            self._show_status("这个文件解不出图（可能不是有效图片）：%s" % p, bad=True)
            self._update_sizes()
            self._update_btns()
            return False

        self.orig = bgr
        self.result = bgr.copy()
        self.mask = np.zeros(bgr.shape[:2], np.uint8)
        self.thumb_cv.set_bgr(self.orig)
        self.edit_cv.set_bgr(self.result)
        self.thumb_cv.set_mask_ref(self.mask)
        self.edit_cv.set_mask_ref(self.mask)
        self._load_boxes()
        self._push_hist("载入")
        self._update_sizes()
        self._need_fit = True
        self._do_fit()
        self._show_status("在左图（改前）拖出矩形选区（要抹掉的那块），再点「应用修补」。"
                          "紫色 = 将被抹掉；右侧「改后」只画红框，不遮挡修补效果。")
        self._update_btns()
        return True

    def _load(self):
        """兼容旧调用（「恢复上一版」用它重载磁盘文件）。"""
        if self.img:
            self.load(self.img)

    def _load_boxes(self):
        try:
            boxes = self.api.ocr_boxes(self.img.get("image_id")) or []
        except Exception:              # noqa: BLE001
            boxes = []
        self.thumb_cv.set_boxes([b for b in boxes if isinstance(b, dict)])
        self.edit_cv.set_boxes([b for b in boxes if isinstance(b, dict)])
        self.lb_ocr.setText("OCR 文字框 %d 个（勾选显示）" % len(boxes))

    def _show_detail_hint(self):
        """详情图入口：本轮只保留入口，不把详情图流程搬进 4.1（用户要求 §三）。"""
        pid = self.img.get("product_id")
        if not pid:
            QMessageBox.information(self, "先选图", "先在左边点一张主图，才有对应商品。")
            return
        QMessageBox.information(
            self, "详情图入口（预留）",
            "商品 #%s 的详情图**不在 4.1 处理**：\n\n"
            "· 4.1 只审主图，详情图不混进照片墙\n"
            "· 详情图的违规检测与打码仍在「4 检测打码」那一步\n"
            "· 这里只是入口预留，本轮不做跳转，也不重复检测\n\n"
            "要看详情图请回到第 4 步。" % pid)
        self._show_status("详情图请在「4 检测打码」处理；4.1 只负责主图。")

    # ---------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8); root.setSpacing(6)

        # 标题行
        head = QHBoxLayout()
        self.lb_title = QLabel("（左侧点一张主图）")
        self.lb_title.setStyleSheet("font-weight:600;")
        head.addWidget(self.lb_title, 1)
        self.lb_pos = QLabel("—")
        self.lb_pos.setStyleSheet("color:#8b96a4;")
        head.addWidget(self.lb_pos)
        # 详情图入口：本轮只留入口，不重做详情图流程（用户要求 §三）
        self.btn_detail = QPushButton("查看该商品详情图")
        self.btn_detail.setToolTip("本轮只保留入口；详情图检测/打码仍在第 4 步，不在这里做")
        self.btn_detail.clicked.connect(self._show_detail_hint)
        head.addWidget(self.btn_detail)
        root.addLayout(head)

        # 工具条
        bar = QHBoxLayout(); bar.setSpacing(6)
        self.tool_group = QButtonGroup(self)
        # 只放矩形：画笔/橡皮/套索/图章真机不稳，先隐藏（ENABLE_EXTRA_TOOLS 打开即恢复）
        codes = [T_RECT]
        if ENABLE_EXTRA_TOOLS:
            codes += [T_BRUSH, T_ERASE, T_POLY, T_STAMP]
        for code in codes:
            b = QPushButton(TOOL_LABEL[code]); b.setCheckable(True)
            b.setToolTip({"rect": "拖出矩形选区（从哪拖都行，四个方向都支持）",
                          "brush": "涂抹选区",
                          "erase": "擦掉选错的区域", "poly": "逐点点击、双击闭合",
                          "stamp": "Alt+左键取源，再拖动复制"}[code])
            b.clicked.connect(lambda _=False, c=code: self._set_tool(c))
            self.tool_group.addButton(b)
            bar.addWidget(b)
            if code == T_RECT:
                b.setChecked(True)
        bar.addSpacing(10)
        bar.addWidget(QLabel("笔刷"))
        self.sp_brush = QSpinBox(); self.sp_brush.setRange(2, 400); self.sp_brush.setValue(24)
        self.sp_brush.setSuffix(" px"); self.sp_brush.valueChanged.connect(self._apply_tool_opts)
        bar.addWidget(self.sp_brush)
        bar.addWidget(QLabel("扩边"))
        self.sp_expand = QSpinBox(); self.sp_expand.setRange(-50, 200); self.sp_expand.setValue(2)
        self.sp_expand.setSuffix(" px"); bar.addWidget(self.sp_expand)
        bar.addSpacing(10)
        self.btn_clear = QPushButton("清除选区"); self.btn_clear.clicked.connect(self._clear_mask)
        self.btn_undo = QPushButton("撤销"); self.btn_undo.clicked.connect(self.undo)
        self.btn_redo = QPushButton("重做"); self.btn_redo.clicked.connect(self.redo)
        self.ck_mask = QCheckBox("显示遮罩"); self.ck_mask.setChecked(True)
        self.ck_mask.toggled.connect(self._toggle_mask)
        self.ck_boxes = QCheckBox("显示 OCR 框"); self.ck_boxes.toggled.connect(self._toggle_boxes)
        for w in (self.btn_clear, self.btn_undo, self.btn_redo, self.ck_mask, self.ck_boxes):
            bar.addWidget(w)
        bar.addStretch(1)
        self.btn_fit = QPushButton("适应窗口")
        self.btn_fit.clicked.connect(lambda: (self.thumb_cv._fit(), self._sync_view(self.thumb_cv)))
        self.btn_100 = QPushButton("100%")
        self.btn_100.clicked.connect(lambda: (self.thumb_cv.zoom_100(), self._sync_view(self.thumb_cv)))
        bar.addWidget(self.btn_fit); bar.addWidget(self.btn_100)
        root.addLayout(bar)

        # 对比区：**固定左右横排**（改前在左、改后在右），窄窗口靠横向滚动，不缩成一条线
        self.split_inner = QSplitter(Qt.Orientation.Horizontal)
        self.split_inner.setChildrenCollapsible(False)
        self.split_inner.setHandleWidth(10)

        left = QFrame(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        caprow = QHBoxLayout(); caprow.setContentsMargins(0, 0, 0, 0); caprow.setSpacing(6)
        self.cap_before = QLabel("改前 · 原图（唯一修改源 · 紫色=将被抹掉）")
        self.cap_before.setStyleSheet("color:#8b96a4;")
        self.lb_size_before = QLabel("—")
        self.lb_size_before.setStyleSheet("color:#6d7887;")
        caprow.addWidget(self.cap_before, 1); caprow.addWidget(self.lb_size_before)
        lv.addLayout(caprow)
        self.thumb_cv = Canvas("原图", self)
        self.thumb_cv.mask_style = "fill"
        self.thumb_cv.setMinimumSize(240, 180)      # 再窄也不低于 240，横向滚动兜底
        lv.addWidget(self.thumb_cv, 1)

        right = QFrame(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        caprow2 = QHBoxLayout(); caprow2.setContentsMargins(0, 0, 0, 0); caprow2.setSpacing(6)
        self.cap_after = QLabel("→ 改后 · 修补结果（红框=选区，不遮图；未确认不替换发布图）")
        self.cap_after.setStyleSheet("color:#8b96a4;")
        self.lb_size_after = QLabel("—")
        self.lb_size_after.setStyleSheet("color:#6d7887;")
        caprow2.addWidget(self.cap_after, 1); caprow2.addWidget(self.lb_size_after)
        rv.addLayout(caprow2)
        self.edit_cv = Canvas("结果", self)
        self.edit_cv.mask_style = "line"
        self.edit_cv.setMinimumSize(240, 180)
        rv.addWidget(self.edit_cv, 1)

        self.split_inner.addWidget(left); self.split_inner.addWidget(right)
        self.split_inner.setSizes([500, 500])
        # 外套 QScrollArea：窗口被拖得很窄时出横向滚动条，两张图各自保底 240px 不变形。
        # QSizePolicy.Ignored 是必须的 —— 否则 QSplitter 会坚持按 sizeHint 撑宽（实测 552 宽时
        # 撑到 708，"改后"那张被推到可视区外，横排等于白做），装上它才会老实填满视口。
        self.split_inner.setSizePolicy(QSizePolicy.Policy.Ignored,
                                       QSizePolicy.Policy.Expanding)
        self.split_inner.setMinimumWidth(2 * 240 + 10)
        self.cmp_scroll = QScrollArea()
        self.cmp_scroll.setWidgetResizable(True)
        self.cmp_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cmp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cmp_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cmp_scroll.setWidget(self.split_inner)
        root.addWidget(self.cmp_scroll, 1)

        # 底栏：算法 + 质量 + 保存
        foot = QHBoxLayout(); foot.setSpacing(8)
        # ★ LaMa 放第一位并默认选中：它是生成式修补（PS 内容识别填充同路线），
        #   大遮罩、渐变背景、压在产品上的文字都比 TELEA/NS 强得多。
        #   TELEA/NS 保留 —— 它们 0.02 秒出图，适合极小面积的细笔画。
        self.rb_lama = QRadioButton("LaMa（AI·大面积推荐）")
        self.rb_telea = QRadioButton("TELEA（快·小面积）")
        self.rb_ns = QRadioButton("NS（快·较平滑）")
        self.rb_lama.setChecked(True)
        grp = QButtonGroup(self)
        for b in (self.rb_lama, self.rb_telea, self.rb_ns):
            grp.addButton(b); b.toggled.connect(self._sync_method)
        for b in (self.rb_lama, self.rb_telea, self.rb_ns):
            foot.addWidget(b)
        foot.addWidget(QLabel("半径"))
        self.sp_radius = QSpinBox(); self.sp_radius.setRange(1, 40); self.sp_radius.setValue(3)
        self.sp_radius.valueChanged.connect(self._sync_method)
        foot.addWidget(self.sp_radius)
        self.btn_repair = QPushButton("应用修补 ▶")
        self.btn_repair.setStyleSheet("font-weight:600; padding:4px 14px;")
        self.btn_repair.clicked.connect(self.apply_repair)
        foot.addWidget(self.btn_repair)
        foot.addStretch(1)
        self.lb_ocr = QLabel("OCR 文字框 —"); self.lb_ocr.setStyleSheet("color:#8b96a4;")
        foot.addWidget(self.lb_ocr)
        root.addLayout(foot)

        foot2 = QHBoxLayout(); foot2.setSpacing(8)
        self.lb_quality = QLabel("质量检查：—")
        self.lb_quality.setWordWrap(True)
        self.lb_quality.setStyleSheet("color:#c9d3df;")
        foot2.addWidget(self.lb_quality, 1)
        self.btn_reset = QPushButton("恢复原图"); self.btn_reset.clicked.connect(self.reset_orig)
        self.btn_prev = QPushButton("重载文件")
        self.btn_prev.setToolTip("重新读磁盘上这张图（放弃本次未保存修改）。不重建控件，不会出现两套界面")
        self.btn_prev.clicked.connect(self._load)
        self.btn_draft = QPushButton("存草稿"); self.btn_draft.clicked.connect(self.save_draft)
        self.btn_save = QPushButton("存新版本"); self.btn_save.clicked.connect(self.save_version)
        self.btn_ok_orig = QPushButton("✓ 原图合格")
        self.btn_ok_orig.setToolTip("没改图也能确认：写入「原图即合格」审核记录，不生成版本、不动主图指针")
        self.btn_ok_orig.clicked.connect(self.confirm_original)
        self.btn_ok = QPushButton("✓ 确认新版本")
        self.btn_ok.setStyleSheet("font-weight:600;")
        self.btn_ok.setToolTip("把刚存的新版本设为该商品的发布主图（原图保留，可退回）")
        self.btn_ok.clicked.connect(self.confirm_ok)
        btns = QHBoxLayout()
        for w in (self.btn_reset, self.btn_prev, self.btn_draft, self.btn_save,
                  self.btn_ok_orig, self.btn_ok):
            btns.addWidget(w)
        foot2.addLayout(btns)
        root.addLayout(foot2)

        self.lb_status = QLabel("提示：在左图拖矩形选区，再点「应用修补」。" +
                                ("" if ENABLE_EXTRA_TOOLS else
                                 "（画笔/橡皮/套索/图章已暂时隐藏）"))
        self.lb_status.setStyleSheet("color:#8b96a4;")
        self.lb_status.setWordWrap(True)
        root.addWidget(self.lb_status)

        # 视图同步
        self.thumb_cv.viewChanged.connect(lambda z, x, y: self._mirror(self.thumb_cv, z, x, y))
        self.edit_cv.viewChanged.connect(lambda z, x, y: self._mirror(self.edit_cv, z, x, y))
        self.thumb_cv.maskEdited.connect(self._on_mask_touch)
        self.edit_cv.maskEdited.connect(self._on_mask_touch)
        self.thumb_cv.maskError.connect(self._on_canvas_error)
        self.edit_cv.maskError.connect(self._on_canvas_error)
        self.stamp_src = None
        self.edit_cv.stampSourceSet.connect(self._stamp_src)
        self.thumb_cv.stampSourceSet.connect(self._stamp_src)
        self._apply_tool_opts()
        self._update_btns()

    def _mirror(self, src, z, x, y):
        dst = self.edit_cv if src is self.thumb_cv else self.thumb_cv
        dst.set_view(z, x, y)
        self.stamp_src = getattr(src, "stamp_pt", None)

    def _sync_view(self, src):
        dst = self.edit_cv if src is self.thumb_cv else self.thumb_cv
        dst.set_view(src.zoom, src.ox, src.oy)

    def _stamp_src(self, x, y):
        self.stamp_src = (x, y)

    def _set_tool(self, code):
        for cv in (self.thumb_cv, self.edit_cv):
            cv.tool = code
            if code != T_STAMP:
                cv.stamp_off = None
            cv.update()
        self._apply_tool_opts()
        self._show_status("工具：%s" % TOOL_LABEL[code])

    def _apply_tool_opts(self):
        for cv in (self.thumb_cv, self.edit_cv):
            cv.brush = int(self.sp_brush.value())
            cv.brush_size = int(self.sp_brush.value())
            cv._stamp_opacity = 1.0
            cv.update()

    def _toggle_mask(self, on):
        for cv in (self.thumb_cv, self.edit_cv):
            cv.show_mask = bool(on); cv.update()

    def _toggle_boxes(self, on):
        for cv in (self.thumb_cv, self.edit_cv):
            cv.show_boxes = bool(on); cv.update()

    def _sync_method(self):
        if self.rb_lama.isChecked():
            self.method = "lama"
        elif self.rb_telea.isChecked():
            self.method = "telea"
        else:
            self.method = "ns"
        self.radius = int(self.sp_radius.value())
        # LaMa 不吃半径（它是生成式，不是扩散半径），置灰免得用户以为能调
        try:
            self.sp_radius.setEnabled(self.method != "lama")
        except Exception:               # noqa: BLE001
            pass
        # 第一次切到 LaMa 时后台预热（模型加载 26 秒，不预热第一次点修补像卡死）
        if self.method == "lama" and not getattr(self, "_warmed", False):
            self._warmed = True
            try:
                self.pool.start(_WarmTask(self._on_warm))
            except Exception:           # noqa: BLE001
                pass

    def _on_warm(self, r: dict):
        try:
            if r.get("ok"):
                self._show_status("LaMa 已预热（%.1fs · %s）"
                                  % (r.get("seconds") or 0, ",".join(r.get("providers") or [])))
            else:
                self._show_status("LaMa 预热失败：%s（可改用 TELEA/NS）"
                                  % (r.get("error") or r), bad=True)
        except Exception:               # noqa: BLE001
            pass

    def _on_mask_touch(self):
        """落笔前压历史（一次笔画 = 一步撤销）。"""
        self._push_hist("选区")
        self._update_btns()

    def _on_canvas_error(self, msg: str):
        """画布层面的失败（遮罩没写进去 / 模块加载失败）必须看得见 —— 不许静默。"""
        self.lb_status.setStyleSheet("color:#ff8a8a;")
        self._show_status("⚠ " + msg + "（原图未受影响）", bad=True)
        self._update_btns()

    def _update_sizes(self):
        """两侧小标题旁的图片尺寸（改前 / 改后各一份，便于确认修补没改分辨率）。"""
        def sz(cv):
            q = cv.qimg
            return "—" if q is None else "%d×%d" % (q.width(), q.height())
        try:
            self.lb_size_before.setText(sz(self.thumb_cv))
            self.lb_size_after.setText(sz(self.edit_cv))
        except Exception:              # noqa: BLE001
            pass

    # ---------------- 历史（按内存预算自适应深度）
    def _snap(self) -> dict:
        packed = np.packbits((self.mask > 0).astype(np.uint8))
        raw = self.result.tobytes() if self.result is not None else b""
        return {"mask_shape": self.mask.shape,
                "mask_packed": packed,
                "result": raw,
                "result_shape": (self.result.shape if self.result is not None else None),
                "method": self.method, "radius": self.radius,
                "label": "", "bytes": len(packed) + len(raw)}

    def _push_hist(self, label: str):
        if self.mask is None:
            return
        s = self._snap(); s["label"] = label
        if self.hidx >= 0 and self.hidx < len(self.hist):
            self.hist = self.hist[: self.hidx + 1]
        # 同一次笔画多次触发时，别把同样的遮罩压两次
        if self.hist and np.array_equal(self.hist[-1]["mask_packed"], s["mask_packed"]) \
                and self.hist[-1]["label"] == label:
            return
        self.hist.append(s)
        self.hidx = len(self.hist) - 1
        self._trim_hist()

    def _trim_hist(self):
        total = sum(h["bytes"] for h in self.hist)
        while total > MAX_HIST_MB * 1024 * 1024 and len(self.hist) > 2:
            total -= self.hist.pop(0)["bytes"]
            self.hidx = max(0, self.hidx - 1)

    def _restore(self, s: dict):
        n = int(np.prod(s["mask_shape"]))
        m = np.unpackbits(s["mask_packed"])[:n].reshape(s["mask_shape"])
        self.mask[:] = (m * 255).astype(np.uint8)
        shp = s.get("result_shape")
        if shp and s.get("result"):
            img = np.frombuffer(s["result"], np.uint8).reshape(shp).copy()
            self.result = img
            self.edit_cv.bgr = img
            self.edit_cv.set_bgr(img)
        for cv in (self.thumb_cv, self.edit_cv):
            cv.dirty_overlay()
        self._sync_method()
        self._update_sizes()
        self._update_btns()

    def undo(self):
        if self.hidx <= 0:
            self._show_status("没有可撤销的步骤")
            return
        self.hidx -= 1
        self._restore(self.hist[self.hidx])
        self._show_status("已撤销 · 剩余 %d 步" % self.hidx)

    def redo(self):
        if self.hidx >= len(self.hist) - 1:
            self._show_status("没有可重做的步骤")
            return
        self.hidx += 1
        self._restore(self.hist[self.hidx])
        self._show_status("已重做 · 第 %d/%d 步" % (self.hidx, len(self.hist) - 1))

    def _update_btns(self):
        self.btn_undo.setEnabled(self.hidx > 0)
        self.btn_redo.setEnabled(self.hidx < len(self.hist) - 1)
        n = int((self.mask > 0).sum()) if self.mask is not None else 0
        self.btn_repair.setEnabled(n > 0 and not self._busy)
        self.btn_clear.setEnabled(n > 0)
        has = self.orig is not None
        self.btn_ok_orig.setEnabled(has)          # 没改图也能确认原图
        self.btn_save.setEnabled(has)
        self.btn_ok.setEnabled(bool(self._ok_vid))

    # ---------------- 修补
    def apply_repair(self):
        if self.mask is None or self.bgr_empty():
            return
        if int((self.mask > 0).sum()) == 0:
            self._show_status("先画选区：现在遮罩是空的")
            return
        try:
            maskgen = v3mod("maskgen")
            self._push_hist("补前")
            g = int(self.sp_expand.value())
            if g > 0:
                m = maskgen.grow_mask(self.mask, g)
                self.mask[:] = m
                for cv in (self.thumb_cv, self.edit_cv):
                    cv.dirty_overlay()
            elif g < 0:
                import cv2
                k = max(1, abs(g) * 2 + 1)
                kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
                self.mask[:] = cv2.erode((self.mask > 0).astype(np.uint8), kern) * 255
                for cv in (self.thumb_cv, self.edit_cv):
                    cv.dirty_overlay()
        except Exception as ex:          # noqa: BLE001
            self._on_canvas_error("准备遮罩失败（%s: %s）" % (type(ex).__name__, ex))
            return
        self._sync_method()
        self._busy = True
        self._update_btns()
        self.btn_repair.setText("修补中…")
        self.pool.start(_RepairTask(self.sig, self.result.copy(), self.mask.copy(),
                                    self.method, self.radius))

    def _show_status(self, text: str, bad: bool = False):
        """统一的状态行出口：正常灰、异常红，避免"失败看着像没发生"。"""
        self.lb_status.setStyleSheet("color:#ff8a8a;" if bad else "color:#8b96a4;")
        self.lb_status.setText(text)

    def bgr_empty(self) -> bool:
        return self.result is None or getattr(self.result, "size", 0) == 0

    def _on_repaired(self, r: dict):
        self._busy = False
        self.btn_repair.setText("应用修补 ▶")
        out = r.get("bgr")
        if out is None:
            self._on_repair_fail("修补内核没有返回图像")
            return
        self.result = out
        self.edit_cv.bgr = out
        self.edit_cv.set_bgr(out)
        self._sync_view(self.thumb_cv)
        self._update_sizes()
        insp = r.get("inspect") or {}
        verdict = str(insp.get("verdict") or "")
        met = insp.get("metrics") or {}
        bits = []
        for k, label in (("color_diff", "色差"), ("sharpness_ratio", "纹理"),
                         ("edge_keep_ratio", "边缘"), ("residual_hf_ratio", "残留")):
            v = met.get(k)
            if v is not None:
                try:
                    bits.append("%s %.2f" % (label, float(v)))
                except Exception:      # noqa: BLE001
                    pass
        rs = insp.get("review_reasons") or []
        head = {"ok": "自动检查未发现问题", "review": "需人工复核",
                "bad": "自动检查判为不合格"}.get(verdict, verdict or "—")
        self.lb_quality.setText("自动检查：%s ｜ %s ｜ %s%s" % (
            head, " · ".join(bits) or "无指标", "",
            ("　理由：" + "；".join(rs[:2])) if rs else ""))
        self._push_hist("修补")
        self._update_btns()
        # ★ 2026-10-26：修补结果只进「改后」那一张，左图按设计永远是原图。
        # 旧版什么都不说，用户盯着左图看 → 结论是「点了没效果」。现在把话说清 + 报坐标。
        box = ""
        try:
            ys, xs = np.nonzero(self.mask)
            if len(xs):
                box = "（改前坐标 x%d–%d, y%d–%d，共 %d px）" % (
                    xs.min(), xs.max(), ys.min(), ys.max(), len(xs))
        except Exception:              # noqa: BLE001
            pass
        self._show_status("修补已应用 → 见右侧「改后」那张%s。左图是原图，按设计不变；"
                          "要落到盘上请点「存新版本」。" % box)

    def _on_repair_fail(self, msg: str):
        self._busy = False
        self.btn_repair.setText("应用修补 ▶")
        self._show_status("修补失败：%s（原图未受影响）" % msg, bad=True)
        self._update_btns()

    def _clear_mask(self):
        if self.mask is None:
            return
        self._push_hist("清空")
        self.mask[:] = 0
        for cv in (self.thumb_cv, self.edit_cv):
            cv.dirty_overlay()
        self._update_btns()

    def reset_orig(self):
        if self.orig is None:
            return
        self._push_hist("恢复原图")
        self.result = self.orig.copy()
        self.mask[:] = 0
        self.edit_cv.set_bgr(self.result)
        for cv in (self.thumb_cv, self.edit_cv):
            cv.dirty_overlay()
        self._sync_view(self.thumb_cv)
        self.lb_quality.setText("质量检查：—（已恢复原图）")
        self._update_sizes()
        self._update_btns()
        self._show_status("已恢复原图（磁盘文件一个字节没动）")

    # ---------------- 保存
    def _regions(self) -> list[dict]:
        """把遮罩落成可读的 regions（矩形近似 + 面积），供台账复核。"""
        import cv2
        out = []
        if self.mask is None:
            return out
        n, lab, stats, _ = cv2.connectedComponentsWithStats((self.mask > 0).astype(np.uint8), 8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < 4:
                continue
            out.append({"kind": "rect", "points": [int(x), int(y), int(x + w), int(y + h)],
                        "area": int(area)})
        out.sort(key=lambda r: -r["area"])
        return out[:50]

    def _require_result(self) -> bool:
        if self.result is None:
            return False
        if self.orig is not None and np.array_equal(self.result, self.orig):
            QMessageBox.information(self, "还没修", "结果跟原图一样 —— 先画选区并点「应用修补」。")
            return False
        return True

    def save_draft(self):
        try:
            r = self.api.save_draft(self.img, self.mask, self._regions(),
                                    self.method, self.radius, int(self.sp_expand.value()))
        except Exception as e:         # noqa: BLE001
            QMessageBox.warning(self, "草稿保存失败", "%s: %s" % (type(e).__name__, e))
            return
        self._show_status("草稿已存 edit_id=%s（没生成版本、没动任何指针）" % r.get("edit_id"))

    def save_version(self):
        if not self._require_result():
            return
        ok = QMessageBox.question(
            self, "存为新版本",
            "会做三件事：\n"
            "① 把修补结果复制成新文件（原图不动）\n"
            "② 在 v3_image 里加一条候选版本（parent 指向本张）\n"
            "③ 台账记下遮罩、算法、半径、检查结果\n\n"
            "注意：这不会替换发布主图 —— 要另点「确认合格」。继续？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            r = self.api.save_version(self.img, self.result, self.mask, self._regions(),
                                      self.method, self.radius, int(self.sp_expand.value()),
                                      self.lb_quality.text())
        except Exception as e:         # noqa: BLE001
            QMessageBox.warning(self, "保存失败", "%s: %s" % (type(e).__name__, e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "没存上", r.get("error") or "未知错误")
            self._show_status("保存失败：%s" % (r.get("error") or ""), bad=True)
            return
        self._show_status("已存新版本 image_id=%s（候选）· 还需点「确认新版本」" %
                               r.get("version_image_id"))
        self.btn_ok.setEnabled(True)
        self._ok_vid = r.get("version_image_id")
        self.changed.emit(dict(self.img), "saved")

    def confirm_ok(self):
        vid = getattr(self, "_ok_vid", 0)
        if not vid:
            try:
                st = self.api.state(self.img.get("image_id")) or {}
            except Exception:          # noqa: BLE001
                st = {}
            vid = st.get("pending_version_id") or 0
        if not vid:
            # 没改图时不再强迫"先存版本" —— 直接走"原图即合格"
            if self.orig is not None and self.result is not None \
                    and np.array_equal(self.result, self.orig):
                return self.confirm_original()
            QMessageBox.information(self, "先存版本",
                                    "还没有新版本可确认。先点「存新版本」，或用「✓ 原图合格」。")
            return
        ok = QMessageBox.question(
            self, "确认合格",
            "确认后：这张图会成为该商品的发布主图（出表取它）。\n"
            "原图仍然保留，随时可以退回。\n\n确定这张合格？")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            r = self.api.confirm(self.img, vid)
        except Exception as e:         # noqa: BLE001
            QMessageBox.warning(self, "确认失败", "%s: %s" % (type(e).__name__, e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "没确认上", r.get("error") or "未知错误")
            return
        self._ok_vid = 0
        self._update_btns()
        self._show_status("已确认合格 · 主图指针 → image_id=%s（原图仍在）" % vid)
        self.changed.emit(dict(self.img), "confirmed")

    def confirm_original(self, quiet: bool = False):
        """不修补、直接确认「原图即合格」（只写审核记录，不生成版本、不动指针）。"""
        if self.orig is None or not self.img:
            if not quiet:
                QMessageBox.information(self, "先选图", "先在左边点一张主图。")
            return
        edited = not (self.result is not None and np.array_equal(self.result, self.orig))
        if edited and not quiet:
            ans = QMessageBox.question(
                self, "这张改过了",
                "当前结果和原图**不一样**。\n\n"
                "· 选「Yes」= 我确认的是**原图**，本次修补丢弃（不存版本）\n"
                "· 选「No」 = 我要保存修补结果：回去点「存新版本」再确认\n\n"
                "确定按「原图合格」记录吗？")
            if ans != QMessageBox.StandardButton.Yes:
                self._show_status("已取消：要保留修补结果请点「存新版本」。")
                return
        try:
            r = self.api.confirm_original(int(self.img["product_id"]),
                                          int(self.img["image_id"]),
                                          note="4.1 人工确认：原图即合格")
        except Exception as e:         # noqa: BLE001
            QMessageBox.warning(self, "确认失败", "%s: %s" % (type(e).__name__, e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "没确认上", r.get("error") or "未知错误")
            return
        self._show_status("已记录：原图合格（商品 #%s · 图片 #%s）· 未生成版本、未改主图指针"
                               % (self.img.get("product_id"), self.img.get("image_id")))
        self.changed.emit(dict(self.img), "confirmed")


# ---------------------------------------------------------------- 独立大窗口（双击精修）
class EditorWindow(QDialog):
    """把 `EditorPanel` 包成独立弹窗 —— **只是放大精修用，不是默认入口**。

    默认入口是 4.1 右侧的工作台（单击左图即在那里编辑）。
    """

    def __init__(self, img: dict, api, parent=None):
        super().__init__(parent)
        self.setWindowTitle("4.1 主图局部修补（大窗精修）· image_id=%s · 商品 %s"
                            % (img.get("image_id"), img.get("product_id")))
        self.setMinimumSize(760, 560)
        self.resize(1280, 860)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.panel = EditorPanel(api, self)
        self.panel.setMinimumSize(400, 400)
        lay.addWidget(self.panel, 1)
        self.panel.load(img)
