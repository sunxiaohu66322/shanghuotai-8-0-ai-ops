# -*- coding: utf-8 -*-
"""4 · 检测打码 —— 图片墙看效果 + 本机引擎检测 + 按坐标消除。

四条改动原则（2026-10-26，主人四条诉求）：
  1. **不下载首图**：2.5 商品库（v3.db 的 v3_image）里首图早就下好了。
     这一页只按 `role='main'` 读 `current_local_path`；本地没有的才标「待下载」，
     并且只能**单件补**或「补齐缺失（只下没有的）」，绝不整批重下。
  2. **图片墙是默认视图**：一格一件商品的首图 + 标题 + ID + 打码状态；
     点一格，下方并排显示「改前 / 改后」大图（左右横排）。列表视图仍保留可切换。
  3. **数据源 = v3.db**（v3_product / v3_image / v3_image_ocr / masked 产物），
     与小猪 商品.db 零依赖。小猪库只用来回答一个问题：**引擎能不能碰这个包**。
  4. **口不能说谎**：这一页显示的每个数字都来自 SQL 或引擎输出，
     读不到就写「读不到」，不拿 0 冒充结论。

词表口径：bridge.detect_ocr_measure() 用 v3.words.WordSet（长词优先 + ASCII 整词边界），
与 2.5 / 3.0 同一份；不用 `w in text` 子串（实测会把命中数虚高 123 张）。
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
                             QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
                             QPushButton, QSlider, QSplitter, QStackedWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import bridge, imgcap, paths, theme
from .tabs import StepTab

CACHE = paths.DATA / "img_cache"
THUMBS = CACHE / "thumbs"
THUMB = 148                     # 缩略图边长
GRID = QSize(178, 218)          # 一格占地（图 + 两行字）

# 状态 → (底色, 前缀)。一眼能分出「没检测」「干净」「命中」「已消除」。
STATUS_COLOR = {
    "hit": ("#b3261e", "🚫"),
    "clean": ("#1b6b3a", "✅"),
    "masked": ("#1f5fa8", "🩹"),
    "no_ocr": ("#8a6d1f", "⏳"),
    "ocr_fail": ("#8a3d1f", "⚠"),
    "missing": ("#7a7a7a", "⬇"),
    "video": ("#6b6b6b", "🎬"),
}


def short_status(it: dict) -> str:
    """格子上那行短状态（长状态留给 tooltip 和列表视图）。"""
    c = it.get("status_code")
    if c == "hit":
        return "命中 %d 处" % it.get("hit_n", 0)
    if c == "masked":
        return "已消除"
    if c == "clean":
        return "干净"
    if c == "no_ocr":
        return "未检测"
    if c == "ocr_fail":
        return "检测失败"
    if c == "missing":
        return "待下载"
    return "视频"


class DetectWorker(QThread):
    """跑本机引擎。**必须在线程里**：这个函数是同步阻塞的，放主线程会假死。

    only_hit=True 走瘦身路径（只处理第 3 步命中的图），计划算不出来会自动回落全量。
    """
    line = pyqtSignal(str)
    plan = pyqtSignal(dict)
    done = pyqtSignal(dict)

    def __init__(self, code: str, only_hit: bool = True):
        super().__init__()
        self.code = code
        self.only_hit = only_hit

    def run(self):  # noqa: D102
        # ★ 2026-10-26 修：先走【纯 v3 库】检测（v3_image + v3_image_ocr + 当前词表），
        #   不再一上来就调小猪库那条 —— 那条对 v3 的包永远命中 0 行，
        #   跑完只会得到「违规 0」，把「没开门」显示成「没问题」。
        try:
            r = bridge.v3_detect_v3(pack_code=self.code, write=True, log=self.line.emit)
            if r.get("ok") and int(r.get("images") or 0) > 0:
                tot = int(r.get("images") or 0)
                hi = int(r.get("hit_images") or 0)
                nb = int(r.get("no_boxes") or 0)
                r["stat"] = {"viol": hi, "clean": max(0, tot - hi - nb),
                             "fail": int(r.get("redet_fail") or 0), "up": 0,
                             "no_ocr": nb}
                r["slim"] = {"mode": "v3", "images_total": tot, "hit_images": hi,
                             "will_process": hi, "downloaded": 0, "download_failed": 0}
                r["engine_used"] = "v3.db（纯本地）"
                r["note"] = ("v3 库直接检测：%d 张已下载的图，其中 %d 张有坐标、"
                             "%d 张命中（%d 处文字）。未碰小猪库。"
                             % (tot, int(r.get("with_boxes") or 0), hi,
                                int(r.get("hit_boxes") or 0)))
                self.line.emit("检测完成：%s" % r["note"])
                self.plan.emit({"ok": True, "note": r["note"], "images": tot,
                                "hit_images": hi, "with_boxes": r.get("with_boxes"),
                                "no_boxes": nb})
                self.done.emit(r)
                return
            self.line.emit("⚠ v3 库这次没选出图（%s），回落小猪库链路"
                           % (r.get("note") or r.get("error") or "未知"))
        except Exception as exc:                     # noqa: BLE001
            self.line.emit("⚠ v3 检测异常，回落小猪库链路：%s" % str(exc)[:150])
        r = bridge.run_local_detect_slim(self.code, only_hit=self.only_hit,
                                         log=self.line.emit, on_plan=self.plan.emit)
        self.done.emit(r or {"ok": False, "error": "没有返回"})


class HealWorker(QThread):
    r"""按已存坐标「消除」。

    走 server\v3\mask.py 的 apply(fill="lama")：**LaMa 生成式消除**（与 4.1 人工审核同一套），
    按「这块应该长什么样」补图，不是贴色块；LaMa 不可用时自动回退 TELEA 消除。
    产物写到 data\v3\masked\，然后**直接覆盖当前版本**（写进原行 current_local_path、
    原值留底 original_local_path、清空线上地址）—— 库里只有一套数据，出表立刻用打码图。
    """
    line = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, code: str, fill: str = "lama", only_hit: bool = True,
                 method: str = "telea", radius: int = 3):
        super().__init__()
        self.code = code
        self.fill = fill
        self.only_hit = only_hit
        self.method = method
        self.radius = radius

    def run(self):  # noqa: D102
        r = bridge.v3_mask_apply(pack_code=self.code, fill=self.fill,
                                 method=self.method, radius=self.radius,
                                 only_hit=self.only_hit, log=self.line.emit)
        self.done.emit(r or {"ok": False, "error": "没有返回"})


class HealOneWorker(QThread):
    """只消除当前这一张（右侧工作台按钮）。LaMa 首次约 28 秒加载，之后约 3 秒/张。"""
    line = pyqtSignal(str)
    done = pyqtSignal(dict)

    def __init__(self, image_id: int, code: str = "", fill: str = "lama"):
        super().__init__()
        self.image_id = int(image_id)
        self.code = code
        self.fill = fill

    def run(self):  # noqa: D102
        r = bridge.v3_mask_one(self.image_id, fill=self.fill, pack_code=self.code)
        self.done.emit(r or {"ok": False, "error": "没有返回"})


class WarmWorker(QThread):
    """在 LaMa **子进程**里加载模型（首次约 28 秒）。主进程全程可操作，界面不卡。"""
    done = pyqtSignal(dict)

    def run(self):  # noqa: D102
        self.done.emit(bridge.mask_warmup() or {"ok": False, "error": "没有返回"})


class FillWorker(QThread):
    """补齐缺失首图（**只下没有的**，调 2.5 的 media.run，它自己跳过已有文件）。"""
    done = pyqtSignal(dict)

    def __init__(self, code: str, pids: list | None = None, role: str = "main"):
        super().__init__()
        self.code = code
        self.pids = pids
        self.role = role

    def run(self):  # noqa: D102
        self.done.emit(bridge.detect_fill_missing(self.code, pids=self.pids, role=self.role))


class MeasureWorker(QThread):
    """用真词表复核本包命中/未命中（只读，界面据此说清「多张未命中」是哪一类）。"""
    done = pyqtSignal(dict)

    def __init__(self, code: str, role: str = ""):
        super().__init__()
        self.code = code
        self.role = role

    def run(self):  # noqa: D102
        self.done.emit(bridge.detect_ocr_measure(self.code, role=self.role))


class ThumbWorker(QThread):
    """缩略图生成。**QImage 在子线程里缩放并落盘，QPixmap 只在 GUI 线程用** ——
    QPixmap 不是线程安全的，直接在子线程 new 会偶发崩。

    落盘缓存名带 (image_id, mtime, 边长)：原图换了就自动失效，不换就零解码直接命中。
    """
    one = pyqtSignal(int, str)          # image_id, 缩略图路径（"" = 失败）

    def __init__(self, jobs: list[tuple[int, str]], gen: int, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.gen = gen

    def run(self):  # noqa: D102
        try:
            THUMBS.mkdir(parents=True, exist_ok=True)
        except Exception:              # noqa: BLE001
            pass
        for iid, src in self.jobs:
            if getattr(self, "_cancel", False):
                return
            try:
                if not src or not os.path.exists(src):
                    self.one.emit(int(iid), "")
                    continue
                mt = int(os.path.getmtime(src))
                dst = THUMBS / ("%d_%d_%d.jpg" % (int(iid), mt, THUMB))
                if not dst.exists():
                    # ★ 2026-10-28 崩溃加固：改成**缩放解码**。原来 QImage(src) 是
                    #   全尺寸解完再缩，大图在子线程里一次吃掉几十 MB —— 那是堆损坏的放大器。
                    img = imgcap.decode(src, max_side=THUMB)
                    if img.isNull():
                        self.one.emit(int(iid), "")
                        continue
                    img.save(str(dst), "JPG", 88)
                self.one.emit(int(iid), str(dst))
            except Exception:          # noqa: BLE001
                self.one.emit(int(iid), "")


class FetchWorker(QThread):
    """现从图床拉一次「改后图」来看（只在这时候联网）。"""
    done = pyqtSignal(str, str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):  # noqa: D102
        import urllib.request          # noqa: PLC0415
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            name = str(abs(hash(self.url)) % (10 ** 12)) + ".jpg"
            dst = CACHE / name
            if not dst.exists():
                req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read()
                if len(data) < 512:
                    raise RuntimeError("太小")
                dst.write_bytes(data)
            self.done.emit(self.url, str(dst))
        except Exception as e:         # noqa: BLE001
            self.done.emit(self.url, "ERR:" + str(e)[:80])


class DetectStepTab(StepTab):
    title = "4 · 检测打码"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self._code = ""
        self._items: list[dict] = []         # 一张图一条
        self._rows: list[dict] = []          # 一件商品一行（列表视图）
        self._by_image: dict[int, dict] = {}
        self._cur_image = 0
        self._worker = None
        self._heal_worker = None
        self._heal_one_w = None
        self._fill = None
        self._thumb = None
        self._thumb_gen = 0
        self._measure = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        tip = QLabel("第 3 步通过的包在这里过检测打码：读字 → 命中违规/夸张词 → 按坐标消除"
                     "（LaMa 生成式，与 4.1 人工审核同一套）→ 产物登记进库。"
                     "左墙点一格，右侧工作台看改前/改后。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(tip)

        # ---------------- 第一行：包 / 搜索 / 状态 / 图片大小 / 计数（与 4.1 同一版式） ----------------
        bar = QHBoxLayout()
        bar.addWidget(QLabel("数据包"))
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(180)
        self.cmb.setMaximumWidth(280)
        self.cmb.currentIndexChanged.connect(self._changed)
        bar.addWidget(self.cmb)

        bar.addWidget(QLabel("搜索"))
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("标题 / 商品ID / 图片ID（回车）")
        self.ed_q.setMinimumWidth(120)
        self.ed_q.setMaximumWidth(200)
        self.ed_q.returnPressed.connect(self._build_wall)
        bar.addWidget(self.ed_q)

        bar.addWidget(QLabel("状态"))
        self.cb_state = QComboBox()
        for label, val in (("全部", ""), ("命中", "hit"), ("已消除", "masked"),
                           ("干净", "clean"), ("未检测", "no_ocr"),
                           ("检测失败", "ocr_fail"), ("待下载", "missing")):
            self.cb_state.addItem(label, val)
        self.cb_state.setMaximumWidth(120)
        self.cb_state.currentIndexChanged.connect(self._build_wall)
        bar.addWidget(self.cb_state)

        bar.addWidget(QLabel("图片大小"))
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(96, 300)
        self.sl.setValue(178)
        self.sl.setMinimumWidth(70)
        self.sl.setMaximumWidth(120)
        self.sl.setToolTip("连续拖动：格子大小实时重算（缩略图已缓存，不重新解码）")
        self.sl.valueChanged.connect(self._on_size)
        bar.addWidget(self.sl)

        self.cmb_role = QComboBox()
        for label, val in (("首图", "main"), ("详情图", "detail"),
                           ("SKU 图", "sku"), ("全部（含视频）", "")):
            self.cmb_role.addItem(label, val)
        self.cmb_role.setToolTip("默认只看首图；首图在 2.5 就已经全部下载完成，这里不需要再下")
        self.cmb_role.setMaximumWidth(110)
        self.cmb_role.currentIndexChanged.connect(self._role_changed)
        bar.addWidget(QLabel("用途"))
        bar.addWidget(self.cmb_role)

        self.lb_count = QLabel("—")
        self.lb_count.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        self.lb_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.lb_count, 1)
        root.addLayout(bar)

        # ---------------- 第二行：动作（4.1 的按钮条形状） ----------------
        bar2 = QHBoxLayout()
        self.btn_reload = QPushButton("刷新 F5")
        self.btn_reload.clicked.connect(self.refresh)
        bar2.addWidget(self.btn_reload)

        self.btn_run = QPushButton("▶ 本机引擎检测")
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self._run)
        bar2.addWidget(self.btn_run)

        self.btn_heal = QPushButton("🩹 按坐标消除")
        self.btn_heal.setToolTip(
            "LaMa 生成式消除（与 4.1 人工审核同一套）：按周边像素把文字「补掉」，不贴色块。\n"
            "LaMa 不可用时自动回退 TELEA 消除。只处理命中的图；\n"
            "产物写到 data\\v3\\masked\\，同时登记进 v3_image_edit（4.1 与出表按库读）。")
        self.btn_heal.clicked.connect(self._heal)
        bar2.addWidget(self.btn_heal)

        self.btn_warm = QPushButton("🔥 预热模型")
        self.btn_warm.setToolTip(
            "先把 LaMa 模型加载进**子进程**（约 28 秒，界面不卡）。\n"
            "做法：点它 → 立刻去点「按坐标消除」，等跑到第一张时模型通常已就绪，\n"
            "省掉第一张干等半分钟。按钮会显示「加载中…」→「✓ 已就绪」。\n"
            "推理走子进程（与 4.1 同一套）：进程内加载会占住解释器、界面卡死，\n"
            "大图连跑还有被系统直接杀掉的风险。")
        self.btn_warm.clicked.connect(self._warm)
        bar2.addWidget(self.btn_warm)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.clicked.connect(self._stop)
        bar2.addWidget(self.btn_stop)

        self.btn_fill = QPushButton("🖼 补齐缺失首图")
        self.btn_fill.setToolTip("只补本地没有的（2.5 已经下过的一律跳过）；"
                                 "也可以用右侧「只补这一件」单件补。")
        self.btn_fill.clicked.connect(self._fill_missing)
        bar2.addWidget(self.btn_fill)

        self.btn_one = QPushButton("⬇ 只补这一件")
        self.btn_one.setToolTip("把当前选中的这件商品缺的图单独补下来（不整批）")
        self.btn_one.clicked.connect(self._fill_one)
        bar2.addWidget(self.btn_one)

        self.btn_view = QPushButton("📋 列表视图")
        self.btn_view.setCheckable(True)
        self.btn_view.setToolTip("列表视图保留链接列（首图/违规图/改后图）；图片墙看效果，列表对链接")
        self.btn_view.toggled.connect(self._toggle_view)
        bar2.addWidget(self.btn_view)

        self.chk_only_hit = QCheckBox("只处理命中图")
        self.chk_only_hit.setChecked(True)
        self.chk_only_hit.setToolTip(
            "第 3 步精筛已经把每张图 OCR 过（v3_image_ocr）。开着它时，第 4 步只对"
            "「命中词非空」的图消除；已判干净的图直接跳过。本包没跑过第 3 步 OCR 时自动回落全量。")
        bar2.addWidget(self.chk_only_hit)
        bar2.addStretch(1)
        root.addLayout(bar2)

        self.pb = QProgressBar()
        self.pb.setRange(0, 100)
        self.pb.setValue(0)
        self.pb.setFormat("%v%")
        root.addWidget(self.pb)

        self.lb = QLabel("—")
        self.lb.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
        root.addWidget(self.lb)

        # ---------------- 主体：左墙 / 右工作台（横排，照 4.1） ----------------
        split = QSplitter(Qt.Orientation.Horizontal)

        self.stack = QStackedWidget()
        self.wall = QListWidget()
        self.wall.setViewMode(QListView.ViewMode.IconMode)
        self.wall.setIconSize(QSize(THUMB, THUMB))
        self.wall.setGridSize(GRID)
        self.wall.setResizeMode(QListView.ResizeMode.Adjust)
        self.wall.setMovement(QListView.Movement.Static)
        self.wall.setUniformItemSizes(True)
        self.wall.setWordWrap(True)
        self.wall.setSpacing(4)
        self.wall.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.wall.currentItemChanged.connect(self._wall_pick)
        self.stack.addWidget(self.wall)

        self.tb = QTableWidget(0, 5)
        self.tb.setHorizontalHeaderLabels(["商品", "首图链接", "命中图原链接",
                                           "改后图（已消除）", "状态"])
        self.tb.verticalHeader().setVisible(False)
        hh = self.tb.horizontalHeader()
        for i in (0, 1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        for i in (1, 2, 3):
            self.tb.setColumnWidth(i, 250)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb.itemSelectionChanged.connect(self._pick)
        self.stack.addWidget(self.tb)
        split.addWidget(self.stack)

        # 右侧工作台：改前 / 改后 竖排（4.1 工作台的形状）
        wb = QWidget()
        wv = QVBoxLayout(wb)
        wv.setContentsMargins(8, 8, 8, 8)
        wv.setSpacing(6)
        self.lb_info = QLabel("点左边一格，这里显示这张图的命中词与状态")
        self.lb_info.setWordWrap(True)
        self.lb_info.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        wv.addWidget(self.lb_info)
        self.lb_before = QLabel("改前：—")
        self.lb_before.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_before.setMinimumSize(240, 160)
        self.lb_before.setStyleSheet("border:1px solid #ddd;")
        wv.addWidget(self.lb_before, 1)
        self.lb_after = QLabel("改后：—")
        self.lb_after.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_after.setMinimumSize(240, 160)
        self.lb_after.setStyleSheet("border:1px solid #ddd;")
        wv.addWidget(self.lb_after, 1)
        rowb = QHBoxLayout()
        self.btn_heal_one = QPushButton("🩹 消除这一张")
        self.btn_heal_one.setToolTip("只对当前这张图按坐标消除（不等整批）")
        self.btn_heal_one.clicked.connect(self._heal_one)
        rowb.addWidget(self.btn_heal_one)
        rowb.addStretch(1)
        wv.addLayout(rowb)
        wb.setMinimumWidth(320)
        wb.setMaximumWidth(520)
        split.addWidget(wb)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([760, 380])
        root.addWidget(split, 1)

        # ---------------- 底部状态行（4.1 的两行提示） ----------------
        self.lb_plan = QLabel("命中图 — · 已判干净跳过 — · 本次实际处理 —")
        self.lb_plan.setObjectName("PlanLine")
        self.lb_plan.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        self.lb_plan.setWordWrap(True)
        root.addWidget(self.lb_plan)

        self.lb_align = QLabel("—")
        self.lb_align.setObjectName("AlignLine")
        self.lb_align.setWordWrap(True)
        self.lb_align.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(self.lb_align)

    # ================= 数据 =================
    def refresh(self):
        """4.0 下拉：默认选「筛选后包」（只含精筛通过件），未筛原包不再默认进入检测。

        ★ 2026-11-09 主人报「4.0 读到没筛选过的原包、还特别慢」：原来把
          v3_packs() 的所有包平铺，默认停在第一个（223 件原包 / 12086 张图）。
          现在优先选最新的筛选后包（27 件 / 1537 张图，第 1 步就少读 87% 的图）；
          原包仍保留在下拉里可手动选，历史「以商品表分组为主」的口径不动。
        """
        cur = self.cmb.currentData()
        r = bridge.v3_packs()
        items = (r.get("items") or []) if isinstance(r, dict) else []
        try:
            ch = bridge.v3_screen_pack_choices() or {}
            screen = list(ch.get("choices") or [])
            default = str(ch.get("default") or "")
        except Exception:                     # noqa: BLE001
            screen, default = [], ""
        screen_codes = {str(x.get("pack_code") or "") for x in screen}
        self.cmb.blockSignals(True)
        self.cmb.clear()
        for it in items:
            code = it.get("pack_code") or ""
            st = bridge.v3_pack_status(code)
            if st.get("ok") and not st.get("ready"):
                continue
            n = it.get("products_live") or it.get("products") or 0
            mark = "✅ 筛选后 · " if code in screen_codes else "⚠ 未筛原包 · "
            self.cmb.addItem("%s%s（%s 件）" % (mark, code, n), code)
        self.cmb.blockSignals(False)
        pick = ""
        if cur:
            i = self.cmb.findData(cur)
            if i >= 0:
                pick = cur
        if not pick and default:
            i = self.cmb.findData(default)
            if i >= 0:
                pick = default
        if pick:
            i = self.cmb.findData(pick)
            if i >= 0:
                self.cmb.setCurrentIndex(i)
        self._changed()

    def _role_changed(self):
        self._load_rows()

    def _toggle_view(self, on: bool):
        self.stack.setCurrentIndex(1 if on else 0)
        self.btn_view.setText("🧱 图片墙" if on else "📋 列表视图")

    def _changed(self):
        self._code = self.cmb.currentData() or ""
        if not self._code:
            self.lb.setText("先在第 2 步导入一个包")
            self._set_buttons(False, False)
            return
        if not bridge.fine_done_of(self._code):
            self.lb.setText("⚠ 这个包还没做第 3 步精筛 —— 先去第 3 步精筛，再回来检测")
            self.lb.setStyleSheet(f"color:{theme.DANGER}; font-size:13px; font-weight:600;")
            self._set_buttons(False, False)
            return
        self.lb.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
        self._set_buttons(True, True)
        self._load_rows()

    def _set_buttons(self, fill: bool, run: bool):
        self.btn_fill.setEnabled(fill)
        self.btn_run.setEnabled(run)

    def _role(self) -> str:
        return self.cmb_role.currentData() or ""

    def _load_rows(self):
        if not self._code:
            return
        d = bridge.detect_wall(self._code, role=self._role())
        if not d.get("ok"):
            self.lb.setText("读图片墙失败：" + str(d.get("error"))[:140])
            return
        self._items = d.get("items") or []
        self._by_image = {x["image_id"]: x for x in self._items}

        by_status = d.get("by_status") or {}
        # ★ 2026-11-03 修：检测走的是 **v3.db**（v3_image + v3_image_ocr + 当前词表），
        #   所以按钮能不能点只看「v3 库有没有图」。
        #   原来这里用 detect_align 的 `engine_can_run`（判据是小猪 商品.db 对不对得上），
        #   于是 v3 有 576 张图、小猪库对不上的包会被**误判成引擎跑不了**、按钮一直灰着 ——
        #   明明这条路根本不碰小猪库。
        can = int(d.get("total") or 0) > 0
        self.lb.setText(
            "共 %d 张图 / %d 件商品 ｜ 命中 %d · 干净 %d · 已消除 %d · 未检测 %d"
            " · 检测失败 %d · 本地缺 %d · 视频 %d ｜ 本地已有 %d/%d%s"
            % (d.get("total") or 0, d.get("products") or 0,
               by_status.get("hit", 0), by_status.get("clean", 0),
               by_status.get("masked", 0), by_status.get("no_ocr", 0),
               by_status.get("ocr_fail", 0), by_status.get("missing", 0),
               by_status.get("video", 0), d.get("local_ok") or 0, d.get("total") or 0,
               "" if can else "　｜　⚠ 本机引擎跑不了这个包（原因见下一行）"))
        self.btn_fill.setEnabled(bool(d.get("missing")))
        self.btn_fill.setText("🖼 补齐缺失首图（缺 %d）" % (d.get("missing") or 0)
                              if d.get("missing") else "🖼 首图本地齐全")

        self._build_wall()
        self._build_table()
        self._refresh_plan()
        self._refresh_align(can=can, info=a)
        self._refresh_measure()

    # ---- 图片墙 ----
    def _on_size(self, v: int):
        """滑块只管「格子多大」——缩略图已缓存，不重新解码原图。"""
        s = int(v)
        self.wall.setIconSize(QSize(s, s))
        self.wall.setGridSize(QSize(s + 30, s + 70))

    def _wall_items(self) -> list[dict]:
        """按「搜索 / 状态」过滤后的图（与 4.1 顶部筛选同一套行为）。"""
        q = (self.ed_q.text() or "").strip().lower()
        st = self.cb_state.currentData() or ""
        out = []
        for it in self._items:
            if st and it["status_code"] != st:
                continue
            if q:
                hay = "%s %s %s" % (it.get("title") or "", it.get("product_id") or "",
                                    it.get("image_id") or "")
                if q not in hay.lower():
                    continue
            out.append(it)
        return out

    def _build_wall(self, *a):
        self._thumb_gen += 1
        if self._thumb and self._thumb.isRunning():
            self._thumb._cancel = True
        self.wall.clear()
        jobs = []
        shown = self._wall_items()
        for it in shown:
            color, icon = STATUS_COLOR.get(it["status_code"], ("#555", "·"))
            txt = "%s\n%s #%s · %s" % (str(it["title"])[:15], icon, it["product_id"],
                                       short_status(it))
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, it["image_id"])
            item.setForeground(QColor(color))
            item.setSizeHint(self.wall.gridSize())
            item.setToolTip("%s\n图片 #%s（%s 第 %s 张）\n商品 %s%s\n%s\n%s"
                            % (it["title"], it["image_id"], it["role"], it["sequence"],
                               it["product_id"],
                               "（初筛拦下 %d 条）" % it["fine_block"] if it["fine_block"] else "",
                               it["status_text"],
                               ("命中：" + "、".join(it["hit_words"][:8])) if it["hit_words"] else ""))
            if it["masked_path"]:
                item.setIcon(QIcon(QPixmap(it["masked_path"]).scaled(
                    THUMB, THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            self.wall.addItem(item)
            if not it["masked_path"] and it["local_path"]:
                jobs.append((it["image_id"], it["local_path"]))
        self.lb_count.setText("显示 %d / 共 %d 张" % (len(shown), len(self._items)))
        if jobs:
            self._thumb = ThumbWorker(jobs, self._thumb_gen, self)
            self._thumb.one.connect(self._thumb_ready)
            self._thumb.start()

    def _thumb_ready(self, image_id: int, path: str):
        if not path:
            return
        for i in range(self.wall.count()):
            it = self.wall.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == image_id:
                it.setIcon(QIcon(QPixmap(path)))
                return

    def _wall_pick(self, cur, _prev):
        if cur is None:
            return
        iid = int(cur.data(Qt.ItemDataRole.UserRole) or 0)
        if iid:
            self._show_pair(iid)

    # ---- 列表视图（按商品一行，保留链接列） ----
    def _build_table(self):
        agg: dict[int, dict] = {}
        for it in self._items:
            a = agg.setdefault(it["product_id"], {"title": it["title"], "main": "",
                                                  "hits": [], "masked": [], "codes": []})
            if it["role"] == "main" and not a["main"]:
                a["main"] = it["local_path"] or it["url"]
            if it["status_code"] in ("hit", "masked"):
                a["hits"].append(it["url"])
                a["masked"].append(it["masked_path"] or "—")
            a["codes"].append(it["status_code"])
        self._rows = []
        for pid, a in agg.items():
            n_hit = sum(1 for c in a["codes"] if c == "hit")
            n_masked = sum(1 for c in a["codes"] if c == "masked")
            n_clean = sum(1 for c in a["codes"] if c == "clean")
            n_miss = sum(1 for c in a["codes"] if c == "missing")
            if n_hit:
                st = "命中 %d 张未涂白" % n_hit
            elif n_masked:
                st = "已消除 %d 张" % n_masked
            elif n_miss:
                st = "本地缺 %d 张" % n_miss
            elif n_clean:
                st = "无违规"
            else:
                st = "未检测"
            self._rows.append({"product_id": pid, "title": a["title"],
                               "main_url": a["main"], "viol_images": a["hits"],
                               "masked": a["masked"], "status_text": st})
        self.tb.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            self.tb.setItem(i, 0, QTableWidgetItem(str(r["title"])[:40]))
            self.tb.setItem(i, 1, QTableWidgetItem(r["main_url"] or ""))
            self.tb.setItem(i, 2, QTableWidgetItem("\n".join(r["viol_images"]) or "—"))
            self.tb.setItem(i, 3, QTableWidgetItem("\n".join(r["masked"]) or "—"))
            it = QTableWidgetItem(str(r["status_text"]))
            if r["viol_images"]:
                it.setForeground(Qt.GlobalColor.darkRed)
            self.tb.setItem(i, 4, it)

    # ---- 瘦身计划 / 对齐 / 真命中 ----
    def _slim_text(self, p: dict) -> str:
        if not p or not p.get("ok"):
            return "命中图 — · 已判干净跳过 — · 本次实际处理 —"
        mode = p.get("mode")
        head = {"fallback-full": "模式：回落全量（本包没跑过第 3 步 OCR）",
                "empty": "模式：没有图",
                "slim": "模式：瘦身（只处理命中图）"}.get(mode, "模式：%s" % (mode or "—"))
        return ("%s　｜　命中图 %d · 已判干净跳过 %d · 本次实际处理 %d（含未判定 %d）"
                % (head, int(p.get("hit_images") or 0), int(p.get("skip_images") or 0),
                   int(p.get("will_process") or 0), int(p.get("unknown_images") or 0)))

    def _refresh_plan(self):
        if not self._code:
            self.lb_plan.setText("命中图 — · 已判干净跳过 — · 本次实际处理 —")
            return
        try:
            p = bridge.detect_slim_plan(self._code)
        except Exception as e:         # noqa: BLE001
            self.lb_plan.setText("算瘦身计划失败：%s" % str(e)[:120])
            return
        self.lb_plan.setText(self._slim_text(p))
        if p.get("ok") and p.get("note"):
            self.lb_plan.setToolTip(str(p.get("note")))

    def _refresh_align(self, can: bool = True, info: dict | None = None):
        """引擎能不能碰这个包 —— 对不上就把「0」的真实含义写在界面上（只写这一行，不追加）。"""
        if not self._code:
            self.lb_align.setText("—")
            return
        a = info if (info and info.get("ok")) else bridge.detect_align(self._code)
        if not a.get("ok"):
            self.lb_align.setText("对齐检查失败：%s" % str(a.get("error"))[:120])
            return
        can = int(a.get("v3_images") or 0) > 0      # ★ 2026-10-26：检测读 v3 库，只看 v3 有没有图
        self.lb_align.setText("v3 库 %d 张图 / %d 件 ｜ 小猪 商品.db 对得上 %d 张图 / %d 件"
                              " ｜ %s"
                              % (a.get("v3_images") or 0, a.get("products") or 0,
                                 a.get("pig_images") or 0, a.get("pig_products") or 0,
                                 "引擎可跑（鉴图.py 按小猪库的 goods_id 取图）" if can
                                 else "引擎碰不到（鉴图.py 按小猪库 goods_id 取图，对不上就 0 张）"))
        self.lb_align.setStyleSheet(
            "color:%s; font-size:12px;" % (theme.TEXT_SUB if can else theme.DANGER))
        self.lb_align.setToolTip(str(a.get("note") or ""))
        self.btn_run.setEnabled(can)
        self.btn_run.setToolTip("" if can else str(a.get("note") or ""))
        # 消除走的是 v3.mask（按 v3_image_ocr 里已存的框涂白），不依赖小猪库，永远可跑
        self.btn_heal.setEnabled(bool(self._code))

    def _refresh_measure(self):
        if not self._code or self._measure and self._measure.isRunning():
            return
        self._measure = MeasureWorker(self._code, role=self._role())
        self._measure.done.connect(self._measure_done)
        self._measure.start()

    def _measure_done(self, d: dict):
        if not d.get("ok"):
            return
        self.lb_plan.setText(self.lb_plan.text() +
                             "　｜　本用途真词表复核：真命中 %s 张 · 仅碎片 %s · 有字未匹配 %s"
                             " · 无文字 %s · 无 OCR %s · OCR 失败 %s（词表 %s 条）"
                             % (d.get("real_hit"), d.get("frag_hit"), d.get("unmatched"),
                                d.get("no_text"), d.get("no_ocr"), d.get("ocr_fail"),
                                d.get("words_n")))
        tops = "、".join("%s×%d" % (w, n) for w, n in (d.get("top_real") or [])[:8])
        frags = "、".join("%s×%d" % (w, n) for w, n in (d.get("top_frag") or [])[:8])
        self.lb_plan.setToolTip("命中词 Top：%s\n仅碎片（疑似 OCR 切断造成的误报）：%s"
                                % (tops or "—", frags or "—"))

    # ================= 动作 =================
    def _fill_missing(self):
        if not self._code:
            return
        if self._fill and self._fill.isRunning():
            return
        miss = sum(1 for x in self._items if x["status_code"] == "missing")
        if not miss:
            QMessageBox.information(self, "不用下", "本地已经齐全，没有缺的图。")
            return
        ok = QMessageBox.question(
            self, "只补缺的",
            "本地已齐 %d 张，缺 %d 张。\n\n只下载缺的这一批（已有的一个字节都不动），继续？"
            % (len(self._items) - miss, miss))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.btn_fill.setEnabled(False)
        self.log("补齐缺失（%s · %s）—— 只下没有的，已有的一律跳过…" % (self._code, self._role()))
        self._fill = FillWorker(self._code, role=self._role())
        self._fill.done.connect(self._fill_done)
        self._fill.start()

    def _fill_one(self):
        it = self._by_image.get(self._cur_image)
        if not it:
            QMessageBox.information(self, "先选一张", "先在图片墙里点一格（或列表里点一行）。")
            return
        if self._fill and self._fill.isRunning():
            return
        self.btn_fill.setEnabled(False)
        self.btn_one.setEnabled(False)
        self.log("只补这一件：商品 %s（%s）…" % (it["product_id"], self._role()))
        self._fill = FillWorker(self._code, pids=[it["product_id"]], role=self._role())
        self._fill.done.connect(self._fill_done)
        self._fill.start()

    def _fill_done(self, r: dict):
        self.btn_fill.setEnabled(True)
        self.btn_one.setEnabled(True)
        if not r.get("ok"):
            self.log("✗ 补图失败：" + str(r.get("error"))[:160])
        else:
            self.log("✓ " + str(r.get("message") or r))
        self._load_rows()

    def _warm(self):
        if getattr(self, "_warm_worker", None) and self._warm_worker.isRunning():
            self.log("模型正在加载，等它跑完（不会再起第二个）")
            return
        self.btn_warm.setEnabled(False)
        self.btn_warm.setText("🔥 加载中…")
        self.log("预热 LaMa 模型（子进程，约 28 秒）—— 期间界面可正常操作…")
        self._warm_worker = WarmWorker(self)
        self._warm_worker.done.connect(self._warm_done)
        self._warm_worker.start()

    def _warm_done(self, r: dict):
        self.btn_warm.setEnabled(True)
        if not r.get("ok"):
            self.btn_warm.setText("🔥 预热模型")
            self.log("✗ 预热失败：%s" % str(r.get("error") or r.get("warmup") or r)[:240])
            return
        w = r.get("warmup") or {}
        self.btn_warm.setText("🔥 ✓ 已就绪")
        self.log("✓ LaMa 已就绪：加载 %ss · 自检 %ss · 执行器 %s"
                 % (w.get("load"), w.get("seconds"), w.get("providers")))

    def _heal(self):
        if not self._code:
            QMessageBox.information(self, "先选包", "第 4 步按数据包跑，先在上面选一个。")
            return
        if self._heal_worker and self._heal_worker.isRunning():
            self.log("消除已经在跑，等它跑完（不会再起第二个）")
            return
        only_hit = bool(self.chk_only_hit.isChecked())
        self.btn_heal.setEnabled(False)
        self.pb.setValue(0)
        self.pb.setFormat("消除中…")
        self.log("按坐标消除开始（%s）—— 填法=LaMa（回退 TELEA）；产物直接覆盖当前版本（原图留底）" % self._code)
        self._heal_worker = HealWorker(self._code, only_hit=only_hit)
        self._heal_worker.line.connect(self.log)
        self._heal_worker.done.connect(self._heal_done)
        self._heal_worker.start()

    def _heal_done(self, r: dict):
        self.btn_heal.setEnabled(True)
        self.pb.setFormat("%v%")
        if not r.get("ok"):
            self.log("✗ 消除未完成：%s" % str(r.get("error") or r.get("tail"))[:300])
            return
        picked = r.get("picked") or {}
        self.log("✓ 消除完成：共 %s 张 · 出图 %s · 跳过（无命中）%s · 失败 %s ｜ 选法 %s"
                 % (r.get("total"), r.get("masked"), r.get("skipped"),
                    r.get("failed"), picked or "-"))
        led = r.get("ledger") or {}
        if led.get("ok"):
            self.log("　台账（v3_image_edit）：新增 %s 条 · 更新 %s 条"
                     % (led.get("inserted"), led.get("updated")))
        elif led.get("error"):
            self.log("　⚠ 台账没写成：%s（图已出，库里查不到这批记录）" % led.get("error"))
        ver = r.get("versions") or {}
        if ver.get("ok"):
            if ver.get("mode") == "overwrite":
                self.log("　✓ 已覆盖当前版本 %s 张（原图留底 original_local_path，随时可回退）"
                         "｜跳过 %s · 失败 %s"
                         % (ver.get("applied"), ver.get("skipped"), ver.get("failed")))
                if ver.get("need_upload"):
                    self.log("　⚠ 这 %s 张的线上地址已清空 —— 出表前必须先「上传图床」，"
                             "否则表里是空地址" % ver.get("need_upload"))
            else:
                self.log("　候选版本：新建 %s · 已有 %s —— 到 4.1 点「确认合格」才会生效"
                         % (ver.get("versions_new"), ver.get("versions_existing")))
            for e in (ver.get("errors") or [])[:3]:
                self.log("　⚠ %s" % e)
        elif ver.get("error"):
            self.log("　⚠ 覆盖没做成：%s" % ver.get("error"))
        self.log("　产物目录：%s" % r.get("out_dir"))
        self._load_rows()

    def _heal_one(self):
        """右侧工作台：只消除当前这一张。"""
        if not self._cur_image:
            QMessageBox.information(self, "先选一张", "先在左边墙上点一格。")
            return
        if self._heal_one_w and self._heal_one_w.isRunning():
            self.log("单张消除还在跑，等它完成")
            return
        self.btn_heal_one.setEnabled(False)
        self.btn_heal_one.setText("🩹 消除中…")
        self.log("消除这一张（#%s）—— LaMa；首次要加载模型（约 30 秒）" % self._cur_image)
        self._heal_one_w = HealOneWorker(self._cur_image, code=self._code)
        self._heal_one_w.done.connect(self._heal_one_done)
        self._heal_one_w.start()

    def _heal_one_done(self, r: dict):
        self.btn_heal_one.setEnabled(True)
        self.btn_heal_one.setText("🩹 消除这一张")
        one = r.get("one") or {}
        if not r.get("ok"):
            self.log("✗ 这一张没消成：%s" % str(r.get("error") or one.get("error"))[:240])
            return
        if not one.get("masked"):
            self.log("这一张没有命中，未改动（%s）" % (one.get("note") or one.get("error") or ""))
            return
        led = r.get("ledger") or {}
        ver = r.get("versions") or {}
        tail = ""
        if ver.get("ok") and ver.get("mode") == "overwrite":
            tail = "　✓ 已覆盖当前版本（原图留底）"
            if ver.get("need_upload"):
                tail += "，出表前要上传图床"
        elif ver.get("ok"):
            tail = "　候选已建，到 4.1 点确认才生效"
        elif ver.get("error"):
            tail = "　⚠ 覆盖没做成：%s" % ver.get("error")
        self.log("✓ 已消除 #%s：%s 处 · 用时 %ss · 选法 %s%s%s"
                 % (one.get("image_id"), one.get("painted"), one.get("secs"),
                    one.get("picked") or r.get("fill"), tail,
                    "" if led.get("ok") else "　⚠ 台账未写：%s" % led.get("error")))
        self._load_rows()
        self._show_pair(int(one.get("image_id") or self._cur_image))

    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._code:
            QMessageBox.information(self, "先选包", "第 4 步按数据包跑，先在上面选一个。")
            return
        self.btn_run.setEnabled(False)
        only_hit = bool(self.chk_only_hit.isChecked())
        self.log("本机引擎开始（%s）—— %s；直接读 v3 库已存坐标 + 当前词表重算命中"
                 "（不重跑 OCR、不碰小猪库）…"
                 % (self._code, "只处理命中的图" if only_hit else "全量扫描"))
        self._worker = DetectWorker(self._code, only_hit=only_hit)
        self._worker.line.connect(self._line)
        self._worker.plan.connect(self._plan_ready)
        self._worker.done.connect(self._done)
        self._worker.start()

    def _plan_ready(self, p: dict):
        self.lb_plan.setText(self._slim_text(p))
        if p.get("ok") and p.get("note"):
            self.lb_plan.setToolTip(str(p.get("note")))
            self.log("计划：" + str(p.get("note")))

    def _line(self, s: str):
        self.log(s)
        if s.startswith("[") and "/" in s[:14]:
            try:
                a, b = s[1:].split("]", 1)[0].split("/")
                n, t = int(a), int(b)
                self.pb.setValue(int(n * 100 / t) if t else 0)
                self.pb.setFormat("检测 %d/%d" % (n, t))
            except Exception:          # noqa: BLE001
                pass

    def _done(self, r: dict):
        self.btn_run.setEnabled(True)
        self.pb.setFormat("%v%")
        if not r.get("ok"):
            self.log("✗ 检测未完成：%s" % str(r.get("error") or r.get("tail"))[:300])
            if r.get("stopped"):
                self.log("（已按你的要求停止）")
        else:
            st = r.get("stat") or {}
            if not st:
                self.log("✓ 引擎跑完，但一张图都没处理（stat 为空）—— "
                         "看下面「小猪 商品.db 对得上」那行，对不上就是一张都没参与")
            else:
                self.log("✓ 检测完成：违规 %s 张 · 干净 %s · 失败 %s · 换新图 %s"
                         % (st.get("viol"), st.get("clean"), st.get("fail"), st.get("up")))
                if r.get("engine_used"):
                    self.log("　引擎：%s｜图 %s 张（有坐标 %s · 无坐标 %s · 命中图 %s / 命中文字 %s 处）"
                             % (r.get("engine_used"), r.get("images"), r.get("with_boxes"),
                                r.get("no_boxes"), r.get("hit_images"), r.get("hit_boxes")))
                    if st.get("no_ocr"):
                        self.log("　无坐标 %s 张：这些图还没跑过 OCR（勾「补跑无坐标的图」可补）"
                                 % st.get("no_ocr"))
        sl = r.get("slim") or {}
        if sl:
            self.log("　瘦身：命中 %s 张 · 已判干净跳过 %s 张 · 本次实际处理 %s 张"
                     "（其中未判定 %s）· 补下载 %s 张 · 耗时 %ss"
                     % (sl.get("hit_images"), sl.get("skipped_clean"),
                        sl.get("will_process"), sl.get("unknown_images"),
                        sl.get("downloaded"), r.get("seconds")))
        self._load_rows()

    def _stop(self):
        self.log("请求停止：" + str(bridge.detect_stop()))

    # ================= 改前 / 改后 =================
    def _pick(self):
        i = self.tb.currentRow()
        if i < 0 or i >= len(self._rows):
            return
        pid = self._rows[i]["product_id"]
        # 优先挑这个商品里「有命中/已消除」的那张；都没有就挑首图
        cand = [x for x in self._items if x["product_id"] == pid
                and x["status_code"] in ("hit", "masked")]
        if not cand:
            cand = [x for x in self._items if x["product_id"] == pid and x["role"] == "main"]
        if cand:
            self._show_pair(cand[0]["image_id"])

    def _show_pair(self, image_id: int):
        it = self._by_image.get(image_id)
        if not it:
            return
        self._cur_image = image_id
        hw = "、".join(it["hit_words"][:10]) if it["hit_words"] else "—"
        self.lb_info.setText("图片 #%s（%s 第 %s 张）· 商品 %s\n状态：%s\n命中词：%s%s"
                             % (it["image_id"], it["role"], it["sequence"], it["product_id"],
                                it["status_text"], hw,
                                ("　·　台账：%s" % it["mask_method"]) if it.get("mask_in_db") else ""))
        # 改前
        if it["local_path"]:
            self._show(self.lb_before, it["local_path"], "改前")
        else:
            self.lb_before.setPixmap(QPixmap())
            self.lb_before.setText("改前：本地没有这张图\n（点「只补这一件」补下来，不用整批下）")
        # 改后
        if it["masked_path"]:
            self._show(self.lb_after, it["masked_path"], "改后（已消除）")
            return
        if it["remote_url"]:
            self.lb_after.setText("改后：拉取中…")
            self._fetch = FetchWorker(it["remote_url"])
            self._fetch.done.connect(self._got_after)
            self._fetch.start()
            return
        self.lb_after.setPixmap(QPixmap())
        if it["status_code"] == "hit":
            self.lb_after.setText("改后：还没涂白\n（点「🩹 按坐标消除」就能出改后图）")
        elif it["status_code"] == "clean":
            self.lb_after.setText("改后：这张没有违规，未改动")
        else:
            self.lb_after.setText("改后：还没有改后图")

    def _got_after(self, url: str, path: str):
        if path.startswith("ERR:"):
            self.lb_after.setText("改后：拉不到（%s）\n%s" % (path[4:], url[:80]))
            return
        self._show(self.lb_after, path, "改后")

    def _show(self, lab: QLabel, path: str, cap: str):
        pm = QPixmap(path)
        if pm.isNull():
            lab.setText("%s：读不出图片（%s）" % (cap, os.path.basename(path)))
            return
        lab.setPixmap(pm.scaled(lab.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation))
        lab.setToolTip(path)
