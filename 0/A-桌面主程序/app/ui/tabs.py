# -*- coding: utf-8 -*-
"""五个步骤页 —— 一个步骤一个 Table 页（对齐主人的说法）。

    1 采集初筛   抓淘宝搜索页的卡 → 旗舰/品牌词/广告/死链剔除 → 出清单
    2 数据包导入 packs 里的 csv 包 → 本机商品库
    3 检测打码   小猪本地链路（9109 + PicDetector）
    4 出表       19 列微信小店表
    5 设置       环境自检 + 默认值

小猪原版那几个跟我们无关的页（店铺管理等）这里一概不做。
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from .. import bridge, dirbook, packlabel, paths, theme
from . import widgets as W
from .word_manager import WordManager


def _why(d: dict) -> str:
    """取一条剔除记录的原因文案（兼容两种数据结构）。

    ★ 2026-10-22 二修真 bug：界面表格以前只读 `hard_drop_reasons`（intake.screen 的
      原始 item 结构），但页签缓存 `sessions.save()` 存的是 `intake._rows()` 的成品行，
      **键名是中文「剔除原因」**。于是界面上那一列一直是空的 —— 主人才会说
      「剔除原因怎么没写」。**命令行看得到、界面看不到，就是因为这条取错了键。**
    """
    rows = d.get("hard_drop_reasons")
    if rows:
        return "；".join(str(x) for x in rows if x)
    return str(d.get("剔除原因") or "").strip()


# ============================================================
#  采集线程
# ============================================================
class IntakeWorker(QThread):
    """跑 intake.run（阻塞：连 CDP + 搜索/读页面 + 初筛）。"""
    done = pyqtSignal(dict)
    note = pyqtSignal(str)

    def __init__(self, kw: str, scroll: int, pages: int, auto_learn: bool,
                 use_ai: bool, page_gap: float = 4.5):
        super().__init__()
        self.kw, self.scroll, self.pages = kw, scroll, pages
        self.auto_learn, self.use_ai = auto_learn, use_ai
        self.page_gap = page_gap

    def run(self):  # noqa: D102
        self.note.emit("连接浏览器调试口，找/开淘宝搜索页 …")
        # write_csv=False —— 采完不落盘，等主人点「导出清单」
        r = bridge.run_intake(self.kw, self.scroll, self.pages,
                              self.auto_learn, self.use_ai,
                              write_csv=False, nav=True,
                              page_gap=self.page_gap)
        self.done.emit(r if isinstance(r, dict) else {"ok": False, "error": "后端返回异常"})


class ExportListWorker(QThread):
    """把界面上那份采集结果写成两份 CSV。"""
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, result: dict):
        super().__init__()
        self.result = result

    def run(self):  # noqa: D102
        try:
            self.done.emit(bridge.export_intake(self.result))
        except Exception as e:  # noqa: BLE001
            self.fail.emit(f"{type(e).__name__}: {e}")


class PackImportWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, files: list[str]):
        super().__init__()
        self.files = files

    def run(self):  # noqa: D102
        lines = []
        script = paths.PIG_READER / "导入CSV.py"
        for f in self.files:
            code, text = bridge._run([str(paths.PY), str(script), f],
                                     cwd=paths.PIG_READER, timeout=900)
            lines.append(f"{os.path.basename(f)} → 退出码 {code}")
            tail = (text or "").strip().splitlines()[-3:]
            lines += ["    " + t for t in tail]
        self.done.emit("\n".join(lines))


class PackConvertWorker(QThread):
    """把文件转成「数据包」（v3 库）。纯本地模块调用，不起服务、不跑子进程。"""
    done = pyqtSignal(str)

    def __init__(self, files: list[str]):
        super().__init__()
        self.files = files

    def run(self):  # noqa: D102
        lines = []
        # ★ 2026-10-26 主人要求「一次导入多个商品 → 自动合成一个包，不要分开」。
        #   原来是 for 循环逐文件调 v3_pack_convert，N 个文件 = N 个单件包，
        #   主人每次导入完还得手动删 N-1 个。现在整批走 v3_pack_convert_many，
        #   共享一个 pack_code、一次解析/下载/OCR/SKU 入库。
        if len(self.files) > 1:
            r = bridge.v3_pack_convert_many(self.files)
            if r.get("ok"):
                lines.append("✓ %d 个文件 → 包「%s」：%s" % (
                    len(self.files), r.get("pack_code"), r.get("message") or ""))
                lines.append("    路线：%s" % (r.get("route") or ""))
            else:
                lines.append("✗ 整批导入失败：%s" % (r.get("error") or "未知原因"))
                for d in (r.get("detail") or [])[:10]:
                    if not d.get("ok"):
                        lines.append("    ✗ %s → %s" % (d.get("file"), d.get("error")))
            for w in (r.get("warnings") or [])[:5]:
                lines.append("    ⚠ " + str(w))
            self.done.emit("\n".join(lines))
            return
        for f in self.files:
            r = bridge.v3_pack_convert(f)
            if r.get("ok"):
                lines.append("✓ %s → 包「%s」：%s（读 %s 行 · 新增 %s · 更新 %s · 跳过 %s）" % (
                    os.path.basename(f), r.get("pack_code"), r.get("message") or "",
                    r.get("rows"), r.get("imported"), r.get("updated"), r.get("skipped")))
            else:
                lines.append("✗ %s → %s" % (os.path.basename(f), r.get("error")))
            for w in (r.get("warnings") or [])[:5]:
                lines.append("    ⚠ " + str(w))
        self.done.emit("\n".join(lines))


class PackDeleteWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):  # noqa: D102
        self.done.emit(bridge.v3_pack_delete(self.code))


class PackDeleteManyWorker(QThread):
    """批量删包（收一列表，不是一个）。

    ★ 2026-10-25：加 purge 参数。主人报「2.0 删了全部商品卡，2.5 还全是商品」——
      根因就是这里一直只删包与名册，`purge=False`，商品行一件没动
      （台账 v3_audit #1375 记着 products_deleted=0）。现在由界面明确选择：
      只删包 / 连商品一起彻底删除。
    """
    done = pyqtSignal(dict)

    def __init__(self, codes: list[str], purge: bool = False):
        super().__init__()
        self.codes = list(codes or [])
        self.purge = bool(purge)

    def run(self):  # noqa: D102
        self.done.emit(bridge.v3_packs_delete_many(self.codes, purge=self.purge))


class PackPackWorker(QThread):
    """把选中的几个包打包成一个新数据包（dry_run=True 只算不写）。"""
    done = pyqtSignal(dict)

    def __init__(self, codes: list[str], name: str = "", dry_run: bool = True):
        super().__init__()
        self.codes = list(codes or [])
        self.name = name
        self.dry_run = dry_run

    def run(self):  # noqa: D102
        self.done.emit(bridge.v3_packs_pack(self.codes, self.name, self.dry_run))


class ExportWorker(QThread):
    done = pyqtSignal(int, str, str)

    def __init__(self, pack: str):
        super().__init__()
        self.pack = pack

    def run(self):  # noqa: D102
        code, text, out = bridge.export_pack(self.pack)
        self.done.emit(code, text, out)


class LocalDetectWorker(QThread):
    """跑本机鉴图引擎（PP-OCRv6 + 词库 + 涂白 + 图床），不需要官方客户端。

    为什么要有它：官方 PicDetector 只能人工点「开始检测违规」，
    整条链路断在一个人手上 —— 这条命令跑完就走，第 4 步才能无人化。
    """
    done = pyqtSignal(dict)

    def __init__(self, pack_code: str, limit: int = 0, no_ai: bool = True):
        super().__init__()
        self.pack_code, self.limit, self.no_ai = pack_code, limit, no_ai

    def run(self):  # noqa: D102
        try:
            self.done.emit(bridge.run_local_detect(self.pack_code, limit=self.limit,
                                                   no_ai=self.no_ai))
        except Exception as e:  # noqa: BLE001
            self.done.emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


class StackWorker(QThread):
    """起小猪本地链路（服务 → 导入 → 客户端）。"""
    done = pyqtSignal(dict)
    note = pyqtSignal(str)

    def __init__(self, only_csv: str = ""):
        super().__init__()
        self.only_csv = only_csv

    def run(self):  # noqa: D102
        r = bridge.start_pig_stack(log=self.note.emit, only_csv=self.only_csv)
        if self.only_csv:
            r.setdefault("steps", []).append("只导入了指定的包")
        self.done.emit(r)


# ============================================================
#  基类
# ============================================================
class StepTab(QWidget):
    title = "步骤"

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win

    def log(self, s: str):
        self.win.log(s)

    def refresh(self):
        """切到本页时调用；子类按需覆盖。"""


# ============================================================
#  1 采集初筛（多关键词 → 每个关键词一个结果页签）
# ============================================================
class IntakeManyWorker(QThread):
    """多关键词一条龙：每个关键词采完就存成一个「页签」缓存。"""
    done = pyqtSignal(dict)
    note = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, kws_text: str, scroll: int, pages: int, auto_learn: bool,
                 use_ai: bool, page_gap: float = 4.5):
        super().__init__()
        self.kws_text, self.scroll, self.pages = kws_text, scroll, pages
        self.auto_learn, self.use_ai = auto_learn, use_ai
        self.page_gap = page_gap

    def run(self):  # noqa: D102
        def _prog(i, n, kw):
            self.progress.emit(i, n, kw)
            self.note.emit(f"[{i}/{n}] 采集「{kw}」…")

        self.note.emit("连接浏览器调试口，逐个关键词采集 …")
        r = bridge.run_intake_many(
            self.kws_text, self.scroll, self.pages,
            self.auto_learn, self.use_ai,
            page_gap=self.page_gap, on_progress=_prog)
        self.done.emit(r if isinstance(r, dict) else {"ok": False, "error": "后端返回异常"})


class ExportListWorker(QThread):
    """把某个结果页签写成两份 CSV（保留 / 剔除）。"""
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)

    def __init__(self, result: dict, sid: str = ""):
        super().__init__()
        self.result, self.sid = result, sid

    def run(self):  # noqa: D102
        try:
            if self.sid:
                self.done.emit(bridge.session_export(self.sid))
            else:
                self.done.emit(bridge.export_intake(self.result))
        except Exception as e:  # noqa: BLE001
            self.fail.emit(f"{type(e).__name__}: {e}")


class CollectTab(StepTab):
    title = "1 · 采集初筛"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.panes: dict[int, dict] = {}      # id(pane_widget) -> 数据
        self.worker: IntakeManyWorker | None = None

        # ★ 2026-10-26：品牌词管理从「页面顶部横排」改成「整页右端竖版」
        #   主人原话：它现在的品牌词管理又是做成一个横排的，应该是做在整个界面的右端、
        #   一个竖版的。→ 外层横排：左 = 原来的整页（root），右 = 固定宽度的竖版词库栏。
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 8, 12)
        outer.setSpacing(8)

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        outer.addLayout(root, 1)

        # ---- 参数行 ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("关键词"))
        self.ed_kw = QLineEdit(win.settings.get("last_keyword", ""))
        self.ed_kw.setPlaceholderText("可一次输多个，用逗号分开：透明帐篷，露营灯,户外椅")
        self.ed_kw.setMinimumWidth(280)
        self.ed_kw.setToolTip("逗号 / 中文逗号 / 顿号 / 分号 都行。\n"
                              "留空 = 直接读你正开着的那个搜索页。\n"
                              "每个关键词会各生成一个结果页签，缓存在本机，可回看、可删。")
        bar.addWidget(self.ed_kw, 1)

        bar.addWidget(QLabel("滚动"))
        self.sp_scroll = QSpinBox()
        self.sp_scroll.setRange(0, 60)
        self.sp_scroll.setValue(int(win.settings.get("scroll", 6)))
        self.sp_scroll.setFixedWidth(64)
        bar.addWidget(self.sp_scroll)

        bar.addWidget(QLabel("页数"))
        self.sp_pages = QSpinBox()
        self.sp_pages.setRange(1, 20)
        self.sp_pages.setValue(int(win.settings.get("pages", 1)))
        self.sp_pages.setFixedWidth(64)
        self.sp_pages.setToolTip("每个关键词采几页。\n"
                                 "★ 淘宝第 2 页起要求登录；没登录会自动停在 1 页并提示原因。")
        bar.addWidget(self.sp_pages)

        bar.addWidget(QLabel("翻页间隔"))
        self.sp_gap = QDoubleSpinBox()
        self.sp_gap.setRange(2.0, 30.0)
        self.sp_gap.setSingleStep(0.5)
        self.sp_gap.setDecimals(1)
        self.sp_gap.setSuffix(" 秒")
        self.sp_gap.setValue(float(win.settings.get("page_gap", 4.5)))
        self.sp_gap.setFixedWidth(84)
        self.sp_gap.setToolTip("每点一次「下一页」之前等多久。别调太小 —— 点太快容易被判异常")
        bar.addWidget(self.sp_gap)

        self.ck_learn = QCheckBox("旗舰店名自动收品牌")
        self.ck_learn.setChecked(bool(win.settings.get("auto_learn", True)))
        bar.addWidget(self.ck_learn)

        self.ck_ai = QCheckBox("AI 复核")
        self.ck_ai.setToolTip("默认关：三件事都是确定性规则，开 AI 每 4 条一批、每批最多等 420 秒")
        bar.addWidget(self.ck_ai)

        self.btn_run = QPushButton("开始采集")
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self.start)
        bar.addWidget(self.btn_run)

        # ★ 停止：采集中才亮。后端在下一个检查点收尾，已采到的保留
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("叫停正在跑的采集。已采到的会保留并生成页签，不会白跑。")
        self.btn_stop.clicked.connect(self.stop)
        bar.addWidget(self.btn_stop)

        self.btn_export = QPushButton("导出当前页签")
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip("采集完先人工看一遍，确认没问题再点这里落盘")
        self.btn_export.clicked.connect(self.export_list)
        bar.addWidget(self.btn_export)

        # ★ 2026-10-23：URL 包也能走「初筛」这一关 —— 不用开浏览器采集，
        #   直接把别人导出的（链接/标题/店铺）包读进来，按同一套规则筛一遍。
        self.btn_urlpack = QPushButton("导入 URL 包初筛 …")
        self.btn_urlpack.setToolTip(
            "读一份 CSV/Excel 的 URL 包（商品链接、标题、店铺名）→ 走与采集完全相同的\n"
            "初筛规则（旗舰必删 · 品牌词 · 店铺类型）→ 出「通过 / 剔除 / 待复核」三份清单。\n"
            "★ 这是粗包，只能做第一步；第三步精筛要求细包（详情图 + SKU 图）。")
        self.btn_urlpack.clicked.connect(self.import_url_pack)
        bar.addWidget(self.btn_urlpack)

        # ★ 2026-10-26：主人要的「所有商品 ID 合并在一起，用 TXT 打开」。
        #   原来只有「导入 URL 包」弹窗里那条路会出 ID 清单；采完多个关键词、
        #   或者导了好几个包时，只能一个一个看，没法合并。这个按钮一次合并全部页签。
        self.btn_ids = QPushButton("⬇ 合并所有页签 → 商品ID")
        self.btn_ids.setToolTip(
            "把所有已筛完的页签（多个关键词 / 多个 URL 包）里「通过」那批的商品 ID\n"
            "合并去重，写成一个 TXT（一行一个）并用记事本打开，直接全选复制。\n"
            "★ 只取通过的那批；剔除和待复核的不进这个清单。")
        self.btn_ids.clicked.connect(self.export_all_ids)
        bar.addWidget(self.btn_ids)
        root.addLayout(bar)

        # ★ 2026-10-24：品牌词随时可改（清掉「徒步」这种运动词、加上「石福康」这种牌子）。
        #   与第三步挂的是**同一份** brand_words_user.json，改完下一次判定就按新词表。
        # ★ 2026-10-26：改到整页右端、竖版（不得横排在顶部）。
        self.wm = WordManager(on_changed=self._on_words_changed, vertical=True)
        self.wm.setMinimumWidth(280)
        self.wm.setMaximumWidth(340)
        outer.addWidget(self.wm, 0)

        # ---- 结果页签区（每个关键词一个）----
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(lambda *_: self._sync_cur())
        root.addWidget(self.tabs, 1)

        self._placeholder = QLabel(
            "还没有结果。\n\n"
            "填关键词（可多个，逗号分开）→ 点「开始采集」。\n"
            "每个关键词会各生成一个页签，内容缓存在本机；页签上的 × 就是删除它的缓存。")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color:{theme.TEXT_SUB};")
        root.addWidget(self._placeholder, 1)

        # ---- 选中详情 ----
        det = QHBoxLayout()
        det.setSpacing(10)
        self.img = W.ImageBox(150, 150)
        det.addWidget(self.img)

        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(2)
        self.kv = {}
        for i, k in enumerate(("商品ID", "标题", "店铺", "店铺类型", "价格", "原因", "完整URL")):
            row = W.KV(k)
            self.kv[k] = row
            g.addWidget(row, i, 0)
        det.addWidget(box, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.btn_open = QPushButton("在浏览器打开 ▸")
        self.btn_open.setEnabled(False)
        self.btn_open.setToolTip("人工二次核实：点一下就在你的浏览器里打开这个商品页")
        self.btn_open.clicked.connect(self._open_current)
        side.addWidget(self.btn_open)
        side.addWidget(W.Hint("双击任意一行\n也能直接打开"))
        side.addStretch(1)
        det.addLayout(side)
        root.addLayout(det)

        # ---- 历史页签 ----
        hist = QHBoxLayout()
        hist.setSpacing(8)
        hist.addWidget(QLabel("历史页签"))
        self.cb_hist = QComboBox()
        self.cb_hist.setMinimumWidth(320)
        self.cb_hist.setToolTip("以前采过的关键词结果都在这儿，删掉页签才会消失")
        hist.addWidget(self.cb_hist, 1)
        self.btn_load = QPushButton("载入")
        self.btn_load.clicked.connect(self._load_hist)
        hist.addWidget(self.btn_load)
        self.btn_hist_del = QPushButton("删除缓存")
        self.btn_hist_del.clicked.connect(self._del_hist)
        hist.addWidget(self.btn_hist_del)
        self.btn_hist_ref = QPushButton("刷新")
        self.btn_hist_ref.clicked.connect(self.load_history)
        hist.addWidget(self.btn_hist_ref)
        root.addLayout(hist)

        self._cur: dict = {}
        self.load_history()

    # ---------------- 页签 ----------------
    def _add_pane(self, keyword: str, keep_rows: list, drop_rows: list,
                  result: dict | None = None, meta: dict | None = None,
                  badge: str = ""):
        pane = QWidget()
        lay = QHBoxLayout(pane)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(5)
        lb_keep = QLabel(f"保留 · 可上货  {len(keep_rows)}")
        lb_keep.setStyleSheet(f"font-weight:700; color:{theme.SUCCESS};")
        lv.addWidget(lb_keep)
        tb_keep = W.make_table(["#", "标题", "店铺", "类型", "价格", "打开"],
                               [34, 268, 140, 74, 62, 60])
        W.fill_table(tb_keep, [[
            i + 1, d.get("商品标题"), d.get("店铺名称"),
            d.get("店铺类型") or "未知", d.get("价格") or "", "▸ 打开",
        ] for i, d in enumerate(keep_rows)])
        lv.addWidget(tb_keep)
        lay.addWidget(left, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(5)
        lb_drop = QLabel(f"剔除 · 不要  {len(drop_rows)}")
        lb_drop.setStyleSheet(f"font-weight:700; color:{theme.DANGER};")
        rv.addWidget(lb_drop)
        tb_drop = W.make_table(["#", "标题", "店铺", "剔除原因", "打开"],
                               [28, 176, 96, 236, 56])
        # ★ 2026-10-22 二修：原因列以前固定 190px，而原因文案有 30-40 个字 ——
        #   被截得只剩十二三个字，主人就说「原因怎么没写」。现在这一列自占剩余宽度，
        #   窗口越宽它越长；仍然放不下的，鼠标悬停有全文 tooltip。
        tb_drop.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        W.fill_table(tb_drop, [[
            i + 1, d.get("商品标题"), d.get("店铺名称"),
            _why(d), "▸ 打开",
        ] for i, d in enumerate(drop_rows)],
            colors={i: {3: theme.DANGER} for i in range(len(drop_rows))})
        rv.addWidget(tb_drop)
        lay.addWidget(right, 1)

        title = (keyword or "（无关键词）")[:16]
        head = f"{badge}·{title}" if badge else title
        idx = self.tabs.addTab(pane, f"{head}  {len(keep_rows)}/{len(drop_rows)}")
        self.tabs.setTabToolTip(idx, f"{badge + ' · ' if badge else ''}{keyword}"
                                     f"\n保留 {len(keep_rows)} · 剔除 {len(drop_rows)}"
                                     f"\n采集于 {((meta or {}).get('created') or '')}")

        self.panes[id(pane)] = {
            "pane": pane, "keyword": keyword,
            "keep": keep_rows, "drop": drop_rows,
            "result": result, "meta": meta or {},
            "sid": (meta or {}).get("id") or "",
            "tb_keep": tb_keep, "tb_drop": tb_drop,
            "lb_keep": lb_keep, "lb_drop": lb_drop,
        }

        tb_keep.itemSelectionChanged.connect(
            lambda t=tb_keep, k=keep_rows: self._pick(t, k))
        tb_drop.itemSelectionChanged.connect(
            lambda t=tb_drop, k=drop_rows: self._pick(t, k))
        tb_keep.cellDoubleClicked.connect(lambda *_: self._open_current())
        tb_drop.cellDoubleClicked.connect(lambda *_: self._open_current())
        tb_keep.cellClicked.connect(
            lambda r, c, t=tb_keep, k=keep_rows: self._open_at(t, k, r, c, 5))
        tb_drop.cellClicked.connect(
            lambda r, c, t=tb_drop, k=drop_rows: self._open_at(t, k, r, c, 5))

        self._placeholder.hide()
        self.tabs.setCurrentIndex(idx)
        return idx

    def _cur_pane(self) -> dict:
        w = self.tabs.currentWidget()
        return self.panes.get(id(w)) if w is not None else None

    # ---------------- 词表改动 ----------------
    def _on_words_changed(self, kind, words):
        """词表一改，这里只说清楚「已生效到哪一步」，不假装已经重判。"""
        cn = {"add": "加为品牌", "remove": "清出品牌", "restore": "撤销"}.get(kind, kind)
        self.log("词表已改（%s）：%s" % (cn, "、".join(words or [])))
        self.log("  下一次导入 URL 包 / 重跑采集就按新词表判；已出的页签要重判请重新导入那份包")
        if self.wm:
            self.wm.refresh_stats()

    def _sync_cur(self):
        d = self._cur_pane()
        self.btn_export.setEnabled(bool(d and (d["keep"] or d["drop"])))
        if d:
            self.lb_hint = None  # 占位，避免旧属性残留

    def _close_tab(self, idx):
        w = self.tabs.widget(idx)
        d = self.panes.get(id(w)) if w is not None else None
        if d and d.get("sid"):
            ok = QMessageBox.question(
                self, "删除这个页签？",
                f"「{d['keyword']}」的结果缓存会一起删掉（{len(d['keep'])} 保留 / "
                f"{len(d['drop'])} 剔除）。\n\n"
                "已经导出过的 CSV 文件不受影响，只删软件里的这份缓存。")
            if ok != QMessageBox.StandardButton.Yes:
                return
            bridge.session_delete(d["sid"])
            self.log(f"已删除页签缓存：{d['keyword']}")
        if w is not None:
            self.panes.pop(id(w), None)
            self.tabs.removeTab(idx)
            w.deleteLater()
        if self.tabs.count() == 0:
            self._placeholder.show()
            self.btn_export.setEnabled(False)
        self.load_history()

    # ---------------- 历史 ----------------
    def load_history(self):
        try:
            items = bridge.sessions_list()
        except Exception:  # noqa: BLE001
            items = []
        self.cb_hist.clear()
        for m in items:
            self.cb_hist.addItem(
                "%s  ·  保 %s / 剔 %s  ·  %s" % (
                    m.get("keyword"), m.get("keep"), m.get("drop"),
                    m.get("created", "")),
                m.get("id"))

    def _load_hist(self):
        sid = self.cb_hist.currentData()
        if not sid:
            return
        d = bridge.session_get(sid)
        if not d:
            QMessageBox.warning(self, "载入失败", "这个页签的缓存文件不在了")
            self.load_history()
            return
        self._add_pane(d.get("keyword") or "", d.get("keep") or [],
                       d.get("drop") or [], result=None,
                       meta={"id": d.get("id"), "created": d.get("created"),
                             "keyword": d.get("keyword")})
        self.log(f"载入历史页签：{d.get('keyword')}")

    def _del_hist(self):
        sid = self.cb_hist.currentData()
        if not sid:
            return
        name = self.cb_hist.currentText()
        ok = QMessageBox.question(self, "删除这个缓存？",
                                  f"删掉之后就没法在软件里回看这一批了：\n\n{name}\n\n"
                                  "已经导出过的 CSV 不受影响。")
        if ok != QMessageBox.StandardButton.Yes:
            return
        done = bridge.session_delete(sid)
        self.log(("已删除缓存：" if done else "没找到缓存：") + name)
        # 同时关掉打开着的同名页签
        for k, d in list(self.panes.items()):
            if d.get("sid") == sid:
                idx = self.tabs.indexOf(d["pane"])
                if idx >= 0:
                    self.panes.pop(k, None)
                    self.tabs.removeTab(idx)
                    d["pane"].deleteLater()
        if self.tabs.count() == 0:
            self._placeholder.show()
        self.load_history()

    # ---------------- 选择 / 打开 ----------------
    @staticmethod
    def _url_for(d: dict) -> str:
        """这一行能用浏览器打开的地址。**只拼真正存在的地址，不猜。**

        历史 bug（2026-10-24 主人报「打开按钮是灰的，点不了」）：
            旧实现只认 `完整URL` 一个字段，而微信/微店的包里**根本没有这一列**
            （只有商品ID + appid + 主图），于是按钮永远灰、双击也打不开。
        现在的顺序：
            ① 包里自带的链接（完整URL / 推广链接原文）
            ② 商品ID + 平台 → 该平台的商品页（淘宝 / 天猫 / 微店）
            ③ 都没有 → 空串，由调用方给出「这份包没有链接」的明确提示
        """
        for k in ("完整URL", "推广链接原文", "商品URL", "商品链接", "链接"):
            u = str(d.get(k) or "").strip()
            if u.startswith("http"):
                return u
        pid = str(d.get("商品ID") or "").strip()
        if pid.isdigit():
            plat = " ".join(str(d.get(k) or "") for k in ("平台", "来源", "数据来源")).lower()
            if "tmall" in plat or "天猫" in plat:
                return "https://detail.tmall.com/item.htm?id=" + pid
            if "taobao" in plat or "淘宝" in plat:
                return "https://item.taobao.com/item.htm?id=" + pid
            if "weidian" in plat or "微店" in plat:
                return "https://weidian.com/item.html?itemID=" + pid
            # ★ 平台不明就**不猜**：微信/微店的商品 ID 拼成淘宝地址一定是 404，
            #   那等于骗主人点一个死链。返回空串，由 _open_current 给出真实选项。
        return ""

    def _open_at(self, table, rows, r, c, open_col):
        if c != open_col:
            return
        if 0 <= r < len(rows):
            self._cur = rows[r]
            self._open_current()

    def _open_current(self):
        """只在主人点/双击时打开浏览器 —— 不在任何后台流程里自动跳。"""
        url = self._url_for(self._cur)
        if url.startswith("http"):
            QDesktopServices.openUrl(QUrl(url))
            self.log(f"已在浏览器打开：{url}")
            return
        # 微信/微店包常见情形：没有商品页链接（只有商品ID + appid）
        pid = str(self._cur.get("商品ID") or "")
        img = str(self._cur.get("主图") or "")
        if pid or img.startswith("http"):
            box = QMessageBox(self)
            box.setWindowTitle("这份包里没有商品页链接")
            box.setText(
                "这一行来自微信/微店的包，源文件里**没有商品页 URL**"
                "（只有商品ID、appid 和主图）。\n\n"
                "商品ID：%s\n\n"
                "要浏览器打开，只能打开主图，或者把商品ID复制出去用。" % (pid or "（无）"))
            b_img = box.addButton("打开主图", QMessageBox.ButtonRole.AcceptRole)
            b_copy = box.addButton("复制商品ID", QMessageBox.ButtonRole.ActionRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is b_img and img.startswith("http"):
                QDesktopServices.openUrl(QUrl(img))
                self.log(f"已在浏览器打开主图：{img}")
            elif box.clickedButton() is b_copy and pid:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(pid)
                self.log(f"已复制商品ID：{pid}")
            return
        self.log("这一行没有可用链接，打不开")

    def _pick(self, table, rows):
        r = table.currentRow()
        if r < 0 or r >= len(rows):
            return
        d = rows[r]
        self.kv["商品ID"].set_value(str(d.get("商品ID") or ""))
        self.kv["标题"].set_value(str(d.get("商品标题") or ""))
        self.kv["店铺"].set_value(str(d.get("店铺名称") or ""))
        self.kv["店铺类型"].set_value(str(d.get("店铺类型") or "未知"))
        self.kv["价格"].set_value(str(d.get("价格") or "（淘宝打码，取不到）"))
        self.kv["原因"].set_value(_why(d) or "—")
        self.kv["完整URL"].set_value(str(d.get("完整URL") or ""))
        url = str(d.get("主图") or "")
        self.img.show_text("主图 URL（未下载）\n" + (url.split("/")[-1] if url else "无"))
        self._cur = d
        # ★ 2026-10-24 修「打开按钮是灰的」：不再只看 完整URL ——
        #   微信/微店包没有这一列，但有商品ID/主图，按钮照样要能点（点了给出真实选项）。
        self.btn_open.setEnabled(bool(self._url_for(d))
                                 or str(d.get("主图") or "").startswith("http"))

    # ---------------- 跑 ----------------
    def start(self):
        if self.worker and self.worker.isRunning():
            return
        port = bridge.find_cdp_port(int(self.win.settings.get("cdp_port", 9222)))
        if port is None:
            QMessageBox.warning(
                self, "没找到浏览器调试口",
                "没连上任何 Chromium 调试端口。\n\n"
                "请先确保浏览器带调试端口启动（本程序不会替你开浏览器）。")

        self.btn_run.setEnabled(False)
        self.btn_run.setText("采集中 …")
        self.btn_stop.setEnabled(True)
        self.btn_export.setEnabled(False)
        kw = self.ed_kw.text().strip()
        n_kw = len([x for x in kw.replace("，", ",").replace("、", ",")
                    .replace("；", ",").replace(";", ",").split(",") if x.strip()]) or 1
        self.log(f"调试口 {port or '未探到'}，开始采集 {n_kw} 个关键词"
                 f"（滚动 {self.sp_scroll.value()} 次 / {self.sp_pages.value()} 页"
                 f" / 翻页间隔 {self.sp_gap.value():.1f} 秒）")

        self.worker = IntakeManyWorker(
            kw, self.sp_scroll.value(), self.sp_pages.value(),
            self.ck_learn.isChecked(), self.ck_ai.isChecked(),
            page_gap=self.sp_gap.value())
        self.worker.note.connect(self.log)
        self.worker.progress.connect(
            lambda i, n, k: self.win.log(f"[{i}/{n}] → {k}"))
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def stop(self):
        """叫停采集。连点几次也安全（后端幂等）。"""
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("正在停 …")
        self.log("⏹ 已发出停止请求，正在等当前这一页收尾（最多十几秒）…")
        r = bridge.stop_intake()
        if not r.get("ok"):
            self.log(f"✗ 停止请求没送到后端：{r.get('error')}")
            self.btn_stop.setEnabled(True)
            self.btn_stop.setText("■ 停止")
            return
        self.log("   后端已收到。已采到的卡片会照常生成页签 —— 不会因为停下就白采。")

    def _reset_stop_btn(self):
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("■ 停止")

    def import_url_pack(self):
        """读一份 URL 包（粗包）→ 走同一套初筛 → 生成页签 + 可导出三份清单。

        ★ 主人 2026-10-23：这种包只有「链接 / 标题 / 店铺名」，
          所以它**只能做第一步**。第三步精筛要求细包（多主图 + 详情图 + SKU 图），
          拿粗包去第三步会逼程序逐个打开商品页抓取 —— 那是违规的，必须拦在门外。
        """
        start = bridge.dirbook_start("url_pack", str(paths.DOWNLOADS))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 URL 包（CSV / Excel）", start,
            "表格 (*.csv *.tsv *.txt *.xlsx);;所有文件 (*.*)")
        if not path:
            return
        bridge.dirbook_remember(path, "url_pack")
        kw = (self.ed_kw.text().split(",")[0].strip()
              if self.ed_kw.text().strip() else "")
        self.btn_urlpack.setEnabled(False)
        self.btn_urlpack.setText("初筛中 …")
        self.log(f"读 URL 包：{os.path.basename(path)}")
        try:
            r = bridge.urlpack_run(path, keyword=kw, use_ai=self.ck_ai.isChecked())
        finally:
            self.btn_urlpack.setEnabled(True)
            self.btn_urlpack.setText("导入 URL 包初筛 …")
        if not r.get("ok"):
            self.log(f"✗ 初筛没跑起来：{r.get('error')}")
            QMessageBox.warning(self, "初筛没跑起来", str(r.get("error")))
            return

        info = r.get("info") or {}
        s = r.get("summary") or {}
        meta = bridge.urlpack_save(r) or {}
        keep = r.get("keep") or []
        drop = r.get("drop") or []
        rev = r.get("review") or []
        # 待复核与剔除同列显示，但原因前缀写清楚 —— 免得被当成「已否决」
        drop_view = list(drop) + [dict(x, 剔除原因="（待复核）" + str(x.get("复核原因") or ""))
                                  for x in rev]
        tag = kw or os.path.splitext(os.path.basename(path))[0]
        # ★ 2026-10-23 主人：同一个「导入 URL 包」按钮会吃两种来源的包 ——
        #   淘宝链接的、微信采集的。界面必须自己说清这一包是哪一种，不用他猜。
        src = info.get("source") or "unknown"
        src_label = info.get("source_label") or "来源认不出"
        badge = {"wechat": "微信", "taobao": "淘宝"}.get(src, "未识别")
        self._add_pane(tag, keep, drop_view, result={"ok": True},
                       meta={"id": meta.get("id") or "",
                             "created": meta.get("created") or ""},
                       badge=badge)
        self.log(f"✓ 包「{tag}」共 {s.get('total', 0)} 行 · 通过 {len(keep)} · "
                 f"剔除 {len(drop)} · 待复核 {len(rev)}")
        self.log(f"   包来源：{src_label}")
        for w in (info.get("source_why") or []):
            self.log(f"     依据：{w}")
        sk = s.get("shop_kind") or {}
        if sk:
            self.log("   店铺类型：" + " · ".join(f"{k} {v}" for k, v in sk.items() if v))
        for n in (info.get("notes") or []):
            self.log(f"   注意：{n}")
        if info.get("kind") == "url":
            self.log("   ⛔ 这是粗包：只能做第一步初筛；第三步精筛不接收它（否则会去逐个开页面抓）。")

        id_line = ("\n  · 商品ID —— 只有通过那批的 ID，一行一个，生成完自动打开\n"
                   if src == "wechat" else
                   "\n  · 商品ID —— 只有通过那批的 ID，一行一个，生成完自动打开\n")
        q = QMessageBox.question(
            self, "导出这一步的结果？",
            f"通过 {len(keep)} 条 · 剔除 {len(drop)} 条 · 待复核 {len(rev)} 条\n"
            f"包来源：{src_label}\n\n"
            "三份 CSV 是同一件事的三个结论，不是重复导出：\n"
            "  · 保留 —— 下游解析软件只要这一份\n"
            "  · 剔除 —— 每条写明为什么删，用来复核规则\n"
            "  · 待复核 —— 认不出店铺类型的那几条，要人看一眼\n"
            + id_line +
            f"\n落点：taobao-picker\\packs\\01_URL初筛\\{tag}_<时间>\\\n"
            "（导入的原包会同时拷一份进 00_导入原始 留档）\n\n"
            "现在导出吗？")
        if q == QMessageBox.StandardButton.Yes:
            self._export_urlpack(r, tag)

    def _export_urlpack(self, result: dict, tag: str) -> None:
        # open_txt=True：ID 清单生成完直接用记事本打开，主人可以直接全选复制
        r = bridge.urlpack_export(result, open_txt=True)
        if not r.get("ok"):
            self.log(f"✗ 导出失败：{r.get('error')}")
            QMessageBox.warning(self, "导出失败", str(r.get("error")))
            return
        c = r.get("counts") or {}
        self.log(f"✓ 已导出：通过 {c.get('keep', 0)} · 剔除 {c.get('drop', 0)} · "
                 f"待复核 {c.get('review', 0)}")
        if r.get("dir"):
            self.log(f"  所在文件夹：{r.get('dir')}")
        if r.get("id_txt"):
            self.log(f"  可上货商品ID：{r.get('id_count', 0)} 个 → {r.get('id_txt')}")
            self.log("  " + ("已用记事本打开，可直接全选复制。"
                             if r.get("opened") else "（自动打开没成功，按上面的路径手动打开）"))
        for k in ("keep", "drop", "review"):
            if r.get(k):
                self.log(f"  {k}: {r.get(k)}")
        # ★ 2026-10-26：这条路以前只出 CSV、不出商品 ID 清单。
        #   主人要「筛完把所有商品 ID 合并成 TXT 打开」，所以补上这一步。
        self._write_ids([result], tag)

    # ---------------- 商品 ID 清单（2026-10-26 主人要的）----------------
    def _write_ids(self, results: list, tag: str = "") -> None:
        """把一批结果的「通过」商品 ID 合并去重，写成 TXT 并用记事本打开。"""
        rr = bridge.urlpack_ids_export(results=results, tag=tag, open_txt=True)
        if not rr.get("ok"):
            self.log(f"✗ 商品ID清单没出成：{rr.get('error')}")
            return
        self.log(f"  可上货商品ID：{rr.get('id_count', 0)} 个"
                 f"（合并 {rr.get('merged', 0)} 份）→ {rr.get('id_txt')}")
        self.log("  " + ("已用记事本打开，可直接全选复制。"
                         if rr.get("opened") else "（自动打开没成功，按上面的路径手动打开）"))

    def export_all_ids(self):
        """工具栏按钮：把所有页签里「通过」的商品 ID 合并成一个 TXT 并打开。

        ★ 主人原话（2026-10-26）：「之前导入了 URL，它这边筛查完以后会把所有的
          商品 ID 都合并在一起，然后用 TXT 打开。现在这个功能没有了。」
          原因：原来只有「导入 URL 包」弹窗里那条路会出 TXT，而且每次只出一份；
          「导出当前页签」那条路从头到尾没碰过商品 ID。这里给一个显式入口。
        """
        ds = [d for d in self.panes.values() if (d.get("result") or {})]
        if not ds:
            QMessageBox.information(
                self, "还没有可合并的结果",
                "先采集一个关键词、或者导入一个 URL 包，筛完再来点这里。")
            return
        results = [d.get("result") or {} for d in ds]
        n_keep = sum(len((r.get("keep") or [])) for r in results)
        q = QMessageBox.question(
            self, "合并导出商品 ID",
            f"页签 {len(results)} 个 · 合计通过 {n_keep} 条\n\n"
            "会把所有页签里「通过」那批的商品 ID 合并去重，\n"
            "写成一个 TXT（一行一个）并用记事本打开。\n"
            "剔除和待复核的不进这个清单。\n\n现在导出吗？")
        if q == QMessageBox.StandardButton.Yes:
            self._write_ids(results, "全部页签")

    def export_list(self):
        """人工看过之后才导出（主人要求：不许采完就自动落盘）。"""
        d = self._cur_pane()
        if not d:
            return
        self.btn_export.setEnabled(False)
        self.btn_export.setText("导出中 …")
        self.worker_exp = ExportListWorker(d.get("result") or {}, d.get("sid") or "")
        self.worker_exp.done.connect(self._exported)
        self.worker_exp.fail.connect(self._export_fail)
        self.worker_exp.start()

    def _exported(self, r: dict):
        self.btn_export.setEnabled(True)
        self.btn_export.setText("导出当前页签")
        # ★ 2026-10-22 三修：后端 export() 以前不返回 ok，这里就一律判成失败
        #   —— CSV 明明写好了，界面却报「导出失败」。现在两种口径都认：
        #   有 error 才算失败；有 keep/drop（或 ok）就算成功。
        if r.get("error") or not (r.get("ok") or r.get("keep") or r.get("drop")):
            self.log(f"✗ 导出失败：{r.get('error')}")
            return
        c = r.get("counts") or {}
        self.log(f"✓ 已导出：保留 {c.get('keep', 0)} · 剔除 {c.get('drop', 0)}")
        self.log(f"  保留清单 {r.get('keep')}")
        self.log(f"  剔除清单 {r.get('drop')}")
        # ★ 2026-10-26：这条路以前只出两份 CSV，不出商品 ID 清单 ——
        #   主人说的「合并商品 ID 用 TXT 打开」在这条路上是缺失的，现在补上。
        d = self._cur_pane()
        if d and d.get("result"):
            self._write_ids([d.get("result")], d.get("keyword") or "当前页签")

    def _export_fail(self, msg: str):
        self.btn_export.setEnabled(True)
        self.btn_export.setText("导出当前页签")
        self.log(f"✗ 导出失败：{msg}")
        QMessageBox.warning(self, "导出没成功", msg)

    def on_done(self, r: dict):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("开始采集")
        self._reset_stop_btn()
        if r.get("stopped") or (r.get("summary") or {}).get("stopped"):
            self.log("⏹ 本次是手动停止 —— 已采到的部分照常列在下面。")

        items = r.get("items") or []
        if not items:
            err = r.get("error") or "未知错误"
            self.log(f"✗ {err}")
            pages = r.get("open_pages") or []
            if pages:
                self.log("当前打开的页面：" + " · ".join(str(p)[:60] for p in pages))
            QMessageBox.warning(self, "采集没成功", err)
            self.load_history()
            return

        ok_n = fail_n = 0
        for it in items:
            kw = it.get("keyword") or ""
            meta = it.get("session") or {}
            if not it.get("ok"):
                fail_n += 1
                self.log(f"✗ 「{kw}」没采到：{it.get('error')}")
                continue
            ok_n += 1
            s = it.get("summary") or {}
            sess = bridge.session_get(meta.get("id") or "") or {}
            keep_rows = sess.get("keep") or []
            drop_rows = sess.get("drop") or []
            self._add_pane(kw, keep_rows, drop_rows,
                           result={"ok": True}, meta=meta)

            by = s.get("drop_by_flag") or {}
            self.log(f"✓ 「{kw}」卡片 {s.get('counts', {}).get('collected', 0)} 张 · "
                     f"保留 {s.get('keep', 0)} · 剔除 {s.get('drop', 0)} · "
                     f"{s.get('elapsed_ms', 0)} ms")
            self.log("   剔除构成：" + " · ".join(f"{k} {v}" for k, v in by.items() if v))
            cs = s.get("collect") or {}
            if cs.get("pages_wanted", 1) > 1:
                self.log(f"   翻页：想采 {cs.get('pages_wanted')} 页，"
                         f"实际采到 {cs.get('pages_done')} 页")
            if cs.get("stop_reason"):
                self.log("   ⚠ " + str(cs["stop_reason"]))
            lr = (s.get("learn") or {}).get("counts") or {}
            if lr.get("added"):
                self.log(f"   品牌自学习：新增 {lr.get('added')} 个"
                         + ("（已重筛）" if s.get("rerun_after_learn") else ""))

        self.win.set_stats(
            cards=sum((it.get("summary") or {}).get("counts", {}).get("collected", 0)
                      for it in items if it.get("ok")),
            keep=sum((it.get("summary") or {}).get("keep", 0) for it in items if it.get("ok")),
            drop=sum((it.get("summary") or {}).get("drop", 0) for it in items if it.get("ok")),
            ms=max([(it.get("summary") or {}).get("elapsed_ms", 0)
                    for it in items if it.get("ok")] or [0]))

        self.log(f"共 {len(items)} 个关键词：成功 {ok_n} · 失败 {fail_n}"
                 f"（每个关键词各一个页签，已缓存在本机）")
        if r.get("note"):
            self.log("  " + str(r["note"]))
        self.load_history()
        self._sync_cur()

        self.win.settings["last_keyword"] = self.ed_kw.text().strip()
        self.win.settings["page_gap"] = self.sp_gap.value()
        bridge.save_settings(self.win.settings)


# ============================================================
#  2 数据包导入
# ============================================================
class PackTab(StepTab):
    title = "2 · 商品卡导入"

    STATUS_TEXT = {
        "pending": "⏳ 待转换",
        "converting": "… 转换中",
        "converted": "✅ 转换成功",
        "empty": "⚠ 空包（0 商品）",
        "error": "✗ 转换失败",
    }

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.worker = None
        self.del_worker = None
        self.pack_worker = None
        self._pack_codes: list[str] = []
        self._rows: list[dict] = []
        self._inbox: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)

        self.btn_inbox = QPushButton("打开收件箱 packs")
        self.btn_inbox.clicked.connect(self._open_inbox)
        bar.addWidget(self.btn_inbox)

        self.btn_pick = QPushButton("选择文件导入并转换 …")
        self.btn_pick.setObjectName("Primary")
        self.btn_pick.clicked.connect(self._pick_files)
        bar.addWidget(self.btn_pick)

        self.btn_del = QPushButton("批量删除选中的包")
        self.btn_del.setObjectName("Danger")
        self.btn_del.clicked.connect(self._delete)
        bar.addWidget(self.btn_del)

        self.btn_pack = QPushButton("打包成一个数据包")
        self.btn_pack.setObjectName("Primary")
        self.btn_pack.clicked.connect(self._pack)
        bar.addWidget(self.btn_pack)

        # ★ 2026-11-09 主人：数据包名称要能改。用独立「显示名」列，不改 pack_code
        #   （它是十几张表的外键，直改必漏）。选中一行 → 输入新名 → 只写展示层。
        self.btn_rename = QPushButton("✎ 改名")
        self.btn_rename.setToolTip(
            "给选中的数据包改个显示名（只改名字，不改数据、不改归属、不搬文件）。\n"
            "改完 3.0 精筛 / 4.0 检测打码 / 5.5 出表 / 发布 / 商品库 / 下载监控\n"
            "刷新后都会跟着显示新名字。留空 = 恢复默认短名。一次改一个包。")
        self.btn_rename.clicked.connect(self._rename_pack)
        bar.addWidget(self.btn_rename)

        # ★ 2026-10-25 主人要求：「在 2.0 就能选一条数据包 → 内部所有数据全量下载 →
        #   顺带跳 OCR 拿详细数据」。原来 2.0 只能导入/打包，下载必须切到 2.5 再做，
        #   而 2.5 里点下载又常常因为包里是空壳而"没反应" —— 这里补上直接入口。
        self.btn_dl = QPushButton("⬇ 下载本包全部媒体 + OCR")
        self.btn_dl.setObjectName("Primary")
        self.btn_dl.setToolTip(
            "对选中的数据包：把主图 / 详情图 / SKU图 / 视频全部下载到本地，\n"
            "下载完立刻做 OCR（带坐标），然后可以在 2.5 商品库和 2.5.1 监控里看。\n"
            "（已有文件会先校验复用，只下缺的；不会重复下载）")
        self.btn_dl.clicked.connect(self._dl_pack)
        bar.addWidget(self.btn_dl)

        bar.addStretch(1)
        self.lb_hint = W.Hint("只吃单商品详情卡（微信 .json / 拼多多详情 .txt 完整 JSON，含新版与老版页面快照）；纯列表 CSV/TSV/TXT 请走第 1 步 URL 包初筛")
        bar.addWidget(self.lb_hint)
        root.addLayout(bar)

        self.lb_tip = QLabel(
            "只有状态是「✅ 转换成功」且商品/图片都不为 0 的包，才能进第 3 步检测打码。")
        self.lb_tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(self.lb_tip)

        root.addWidget(QLabel(
            "导入记录（= 数据包）　·　Ctrl 点选多个 / Shift 连选 / Ctrl+A 全选 → 可批量删除、批量打包　·　选中一个点「✎ 改名」"))
        self.tb = W.make_table(
            ["数据包（显示名）", "状态", "商品", "图片", "行数", "大小KB", "可进第3步", "来源文件", "转换时间"],
            [170, 130, 70, 70, 70, 80, 90, 300, 150])
        # ★ 2026-10-24：主人要求「批量选择、批量删除、批量打包」，而 make_table 默认是
        #   单选（SingleSelection）—— 一手点只能选中一行，这就是"只能一个一个删"的直接原因。
        self.tb.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tb.itemSelectionChanged.connect(self._on_pick)
        root.addWidget(self.tb, 3)

        root.addWidget(QLabel("收件箱 packs 里的文件（双击即转换）"))
        self.tb_in = W.make_table(["文件", "行数", "大小KB"], [380, 80, 90])
        self.tb_in.itemDoubleClicked.connect(self._convert_inbox)
        root.addWidget(self.tb_in, 1)

        self.out = QPlainTextEdit()
        self.out.setObjectName("LogView")
        self.out.setReadOnly(True)
        self.out.setFixedHeight(110)
        root.addWidget(self.out)

    # ---------------------------------------------------------------- 动作

    def _open_inbox(self):
        d = self._inbox_dir or str(paths.PIG_PACKS)
        try:
            os.startfile(d)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            QMessageBox.information(self, "打不开这个目录", f"{d}\n{e}")

    _inbox_dir = ""

    def _pick_files(self):
        """★ 2026-11-14：改按**后端适配器真识别**判定，不再靠扩展名 + 头 4KB 猜。

        老版拼多多页面快照（H:\\bdpan-txt-download\\*\\*.txt）是
        {"store":{"initDataObj":{goods:{goodsID,goodsName,topGallery,
        detailGallery,skus...},mall,queries}}} —— 头 4KB 根本没有 goods_id /
        server_time/ui，旧 looks_detail_json 把它当「列表类 TXT」挡掉，实测 8 个
        被拒样本全是**合法详情卡**。现在统一交 bridge.cardfile_classify →
        adapters.detect_file 完整解析后判定；认不出的才是列表/坏文件。
        """
        start = bridge.dirbook_start("pack", self._inbox_dir or str(paths.PACK_STAGE_PACK))
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择商品卡文件（.json / 拼多多详情 .txt，新版与老版页面快照均可）", start,
            "商品卡 (*.json *.txt);;JSON (*.json);;拼多多详情 TXT (*.txt);;所有文件 (*.*)")
        if not files:
            return

        blocked, ok_files, cards = [], [], {}
        for f in files:
            info = bridge.cardfile_classify(f)
            if info.get("ok") and info.get("kind") == "card":
                ok_files.append(f)
                cards[f] = info
            else:
                blocked.append((f, info))
        if blocked:
            lines = []
            for f, info in blocked[:8]:
                why = str(info.get("reason") or "认不出商品卡格式").replace("\n", " ")[:70]
                lines.append("· %s（%s）" % (os.path.basename(f), why))
            QMessageBox.warning(
                self, "这类文件不能从这里进",
                "第二步只吃【单商品详情卡】。下面这些没认出来：\n\n"
                + "\n".join(lines) +
                "\n\n认不出的通常是纯列表文件（只有标题 / 店铺 / 链接），"
                "或不是完整 JSON 的 TXT，没有主图 / 详情图 / SKU 图，做不了精筛。\n\n"
                "已支持的详情卡：微信 .json · 拼多多新版详情 .txt · "
                "拼多多老版页面快照 .txt（store.initDataObj）。\n"
                "纯列表文件去「1 · 采集初筛」用「导入 URL 包初筛 …」走第一步；"
                "第一步筛完的链接交给外部程序解析成商品卡，再回到这里导入。")
        if not ok_files:
            return
        kinds = sorted({str(cards[f].get("display_name") or cards[f].get("platform") or "")
                        for f in ok_files})
        self.out.appendPlainText(
            "已识别商品卡 %d 个：%s" % (len(ok_files), "、".join(kinds) or "商品卡"))
        # ★ 已按后端适配器确认是正式商品卡（含主图/详情图/SKU 证据），
        #   不再喂给 url_pack.read_table 表格分类器 —— 那份会把完整 JSON 当表读，
        #   header 全是乱码片段，是 11-09 之前「老卡被误挡」的第二道误判。
        for f in ok_files:
            bridge.dirbook_remember(f, "pack")
        self._start_convert(ok_files)

    def _convert_inbox(self, item):
        row = item.row()
        if 0 <= row < len(self._inbox):
            self._start_convert([self._inbox[row]["path"]])

    def _start_convert(self, files: list[str]):
        if self.worker and self.worker.isRunning():
            return
        self.btn_pick.setEnabled(False)
        self.btn_pick.setText("转换中 …")
        self.out.appendPlainText(f"开始转换 {len(files)} 个文件 …")
        self.worker = PackConvertWorker(files)
        self.worker.done.connect(self._converted)
        self.worker.start()

    def _converted(self, text: str):
        self.btn_pick.setEnabled(True)
        self.btn_pick.setText("选择文件导入并转换 …")
        self.out.appendPlainText(text)
        self.refresh()
        self.log(text.replace("\n", " ｜ ")[:200])

    # ---------------------------------------------------------------- 选中
    def _selected_codes(self) -> list[str]:
        """表里当前选中的包码（按行序、去重）。多选靠 ExtendedSelection。"""
        try:
            sel = self.tb.selectionModel().selectedRows()
        except Exception:                              # noqa: BLE001
            return []
        out: list[str] = []
        for idx in sorted(x.row() for x in sel):
            if 0 <= idx < len(self._rows):
                code = str(self._rows[idx].get("pack_code") or "")
                if code and code not in out:
                    out.append(code)
        return out

    def _on_pick(self):
        codes = self._selected_codes()
        if not codes:
            return
        code = codes[0]                        # 详情只讲第一个，多选不刷屏
        detail = bridge.v3_pack_status(code)
        if not detail.get("ok"):
            self.out.appendPlainText(f"读包详情失败：{detail.get('error')}")
            return
        head = (f"已选中 {len(codes)} 个包（详情只显示第一个「{code}」）"
                if len(codes) > 1 else f"包「{code}」")
        lines = [
            head,
            f"  状态      {self.STATUS_TEXT.get(detail.get('status'), detail.get('status'))}"
            f"　{detail.get('message') or ''}",
            f"  商品/图片  {detail.get('products_live')} / {detail.get('images_live')}"
            f"　（缺主图的商品 {detail.get('products_without_main')} 个）",
            f"  源文件    {detail.get('source_file')}",
            f"  行数/大小  {detail.get('rows')} 行 · {detail.get('size_kb')} KB",
            f"  导入/转换  {detail.get('created_at')} → {detail.get('converted_at') or '—'}",
            "  能否进第3步 " + ("可以" if detail.get("ready") else "不行：" +
                             "；".join(detail.get("reasons") or ["转换未完成"])),
        ]
        if detail.get("error"):
            lines.append(f"  错误      {detail['error']}")
        self.out.appendPlainText("\n".join(lines))

    # ---------------------------------------------------------------- 批量删
    def _delete(self):
        codes = self._selected_codes()
        if not codes:
            QMessageBox.information(
                self, "先选包",
                "请在表里点选要删的数据包。\n\n"
                "· Ctrl 点 = 一个个加选\n· Shift 点 = 连选一段\n· Ctrl+A = 全选")
            return
        want = set(codes)
        picked = [x for x in self._rows if str(x.get("pack_code") or "") in want]
        n_prod = sum(int(x.get("products_live") or 0) for x in picked)
        n_img = sum(int(x.get("images_live") or 0) for x in picked)
        names = "\n".join("· " + str(x.get("pack_code")) for x in picked[:10])
        more = "" if len(picked) <= 10 else "\n… 另外还有 %d 个" % (len(picked) - 10)
        # ★ 2026-10-25：三个按钮，把「删包」和「删商品」彻底分开。
        # 之前只有一个 Yes/No，而且只能删包 —— 于是 2.0 删完了，2.5 里 17 件商品全在，
        # 主人以为「删除没生效」。这次让选择在对话框里说清，并显示各自动到多少。
        box = QMessageBox(self)
        box.setWindowTitle("删除这 %d 个包？" % len(codes))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"{names}{more}\n\n涉及 {n_prod} 个商品、{n_img} 张图。")
        box.setInformativeText(
            "【只删包】商品、图片、OCR 记录一件都不动 —— 2.5 商品库里仍然看得到它们。\n"
            "【连商品一起删】包 + 名册 + 属于这些包的商品行 + 它们的图/OCR/本地文件，"
            "回收站里也不留（不可还原）。\n"
            "仍被别的包引用的商品会自动跳过，不会误删共用的图。")
        b_pack = box.addButton("只删包（商品保留）", QMessageBox.ButtonRole.AcceptRole)
        b_both = box.addButton("连商品一起彻底删除", QMessageBox.ButtonRole.DestructiveRole)
        b_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_pack)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_cancel or clicked is None:
            return
        purge = (clicked is b_both)
        if purge:
            sure = QMessageBox.warning(
                self, "再确认一次：会真的删掉商品",
                "这会**永久删除**这 %d 个包里的商品（约 %d 件 / %d 张图），"
                "连同它们的本地图片与 OCR 记录，**不进回收站、不可还原**。\n\n"
                "仍被其他包引用的商品会被跳过并在结果里列出。\n\n确定继续？"
                % (len(codes), n_prod, n_img),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if sure != QMessageBox.StandardButton.Yes:
                return
        self.btn_del.setEnabled(False)
        self.btn_del.setText("批量删除中 …")
        self.out.appendPlainText(
            "开始批量删除 %d 个包（%s）…"
            % (len(codes), "包 + 商品一起删" if purge else "只删包，商品不动"))
        self.del_worker = PackDeleteManyWorker(codes, purge=purge)
        self.del_worker.done.connect(self._deleted)
        self.del_worker.start()

    def _deleted(self, d: dict):
        self.btn_del.setEnabled(True)
        self.btn_del.setText("批量删除选中的包")
        for x in (d.get("results") or []):
            if x.get("ok"):
                if x.get("kind") == "member":
                    self.out.appendPlainText(
                        "✓ 已删成员包「%s」（摘掉 %s 条名册，商品/图片未动）"
                        % (x.get("pack_code"), x.get("removed")))
                else:
                    self.out.appendPlainText(
                        "✓ 已删数据包「%s」（残留商品 %s · 图 %s）"
                        % (x.get("pack_code"), x.get("products_left"),
                           x.get("images_left")))
            else:
                self.out.appendPlainText("✗ 删不掉「%s」：%s"
                                         % (x.get("pack_code"), x.get("error")))
        self.out.appendPlainText(d.get("message") or "")
        if d.get("products_deleted"):
            self.out.appendPlainText(
                "　本次一并彻底删除商品 %d 件（含图片/OCR/本地文件，回收站不留）"
                % d.get("products_deleted"))
        if d.get("errors"):
            for e in (d.get("errors") or [])[:10]:
                self.out.appendPlainText("　失败：" + str(e))
        self.log(d.get("message") or "批量删除完成")
        self.refresh()

    # ---------------------------------------------------------------- 改名
    def _rename_pack(self):
        """改选中包的显示名（2.0 · 2026-11-09）。一次一个，空名=恢复默认。"""
        codes = self._selected_codes()
        if not codes:
            QMessageBox.information(self, "先选一个数据包",
                                    "在上面的表里点一行选中一个包，再点「✎ 改名」。")
            return
        if len(codes) > 1:
            QMessageBox.information(self, "一次改一个",
                                    "你选中了 %d 个包。改名一次只做一个 —— 先只选一个。" % len(codes))
            return
        code = codes[0]
        cur = ""
        for it in self._rows:
            if str(it.get("pack_code") or "") == code:
                cur = str(it.get("display_name") or "")
                break
        name, ok = QInputDialog.getText(
            self, "改数据包名称",
            "包代号：%s\n\n填新的显示名（留空 = 恢复默认名「%s」）。\n"
            "只改名字，商品/图片/归属一律不动。" % (code, code),
            QLineEdit.EchoMode.Normal, cur)
        if not ok:
            return
        name = (name or "").strip()
        if len(name) > 120:
            QMessageBox.warning(self, "名字太长", "最多 120 个字。")
            return
        r = bridge.v3_pack_rename(code, name)
        if not r.get("ok"):
            QMessageBox.warning(self, "改名失败", str(r.get("error") or "?")[:400])
            return
        self.out.appendPlainText(r.get("message") or "已改名")
        self.refresh()

    # ---------------------------------------------------------------- 批量打包
    def _pack(self):
        codes = self._selected_codes()
        if len(codes) < 2:
            QMessageBox.information(
                self, "再选几个",
                "打包至少要选 2 个数据包。\n\n"
                "· Ctrl 点 = 一个个加选\n· Shift 点 = 连选一段\n· Ctrl+A = 全选")
            return
        self.btn_pack.setEnabled(False)
        self.btn_pack.setText("统计中 …")
        self._pack_codes = codes
        self.pack_worker = PackPackWorker(codes, "", True)   # dry_run：先只算
        self.pack_worker.done.connect(self._pack_planned)
        self.pack_worker.start()

    def _dl_pack(self):
        """★ 2026-10-25 主人要求：2.0 里选中一个数据包 → 直接全量下载 + 跳 OCR。

        和 2.5 那个「下载本包媒体」是**同一个后端任务**（同 job、同表、同监控页），
        区别只是入口在 2.0。所以这里不另写下载逻辑，只做：
          取包成员 → 只读探测媒体量 → 说清情况 → v3_dl_start → 跳 2.5.1 监控。
        包里 0 媒体时**不静默**，直接弹窗告诉主人为什么。
        """
        from .. import bridge as B
        codes = self._selected_codes()
        if not codes:
            QMessageBox.information(
                self, "先选一个数据包",
                "在上面的「导入记录」里点一行选中一个包，再点这个按钮。\n"
                "（Ctrl 点=加选，Shift 点=连选；下载一次只做一个包）")
            return
        if len(codes) > 1:
            QMessageBox.information(
                self, "一次下载一个包",
                "你选中了 %d 个包。下载按单个包做 —— 先只选一个。\n"
                "（要合并成一包，先用「打包成一个数据包」）" % len(codes))
            return
        code = codes[0]

        pr = B.v3_pack_pids(code)
        if not pr.get("ok"):
            QMessageBox.warning(self, "取包成员失败", (pr.get("error") or "?")[:400])
            return
        pids = [int(x) for x in (pr.get("pids") or [])]
        if not pids:
            QMessageBox.warning(
                self, "这个包里没有商品",
                "包：%s\n成员：0 件\n\n"
                "所以没有东西可下载 —— 这不是按钮坏了。\n"
                "可能这个包的商品已经被删掉（成员行还留着）。" % code)
            return

        st = B.v3_media_stats(pids=pids)
        if not st.get("ok"):
            QMessageBox.warning(self, "读媒体统计失败", (st.get("error") or "?")[:400])
            return
        stats = st.get("stats") or {}
        # media.stats() 的真实键：entries / downloaded / by_role{role:{dl_state:n}}
        total = int(stats.get("entries") or stats.get("total") or 0)
        got = int(stats.get("downloaded") or 0)
        if total == 0:
            QMessageBox.warning(
                self, "这个包里的商品没有媒体",
                "包：%s\n商品：%d 件\n媒体记录：0 条\n\n"
                "所以没有东西可以下载。\n\n"
                "原因通常是：这些商品是**只导入了骨架**的记录，原始数据里的\n"
                "图片/视频字段没被解析进库。要修的是导入环节，不是下载按钮。"
                % (code, len(pids)))
            return

        by_role = stats.get("by_role") or {}
        detail = "　".join(
            "%s %d" % (k, sum(v.values()) if isinstance(v, dict) else int(v or 0))
            for k, v in by_role.items()) or "（未分类）"
        if QMessageBox.question(
                self, "开始下载这个包？",
                "包：%s\n商品：%d 件\n媒体引用：%d 条（已下载 %d 条）\n按角色：%s\n\n"
                "下载完会立刻做 OCR（带坐标）。已有文件先校验复用，只下缺的。\n\n"
                "现在开始吗？" % (code, len(pids), total, got, detail)
        ) != QMessageBox.StandardButton.Yes:
            return

        self.btn_dl.setEnabled(False)
        self.btn_dl.setText("正在建任务 …")
        try:
            r = B.v3_dl_start(pack_code=code, pids=pids, workers=4, ocr=True,
                              title="2.0 · %s · %d 件" % (code, len(pids)))
        finally:
            self.btn_dl.setEnabled(True)
            self.btn_dl.setText("⬇ 下载本包全部媒体 + OCR")

        if not r.get("ok"):
            QMessageBox.warning(self, "任务没建起来", (r.get("error") or "?")[:500])
            return
        jid = r.get("job_id") or 0
        self.out.appendPlainText(
            "下载任务已创建：job #%s · %d 件 · 媒体 %d 条 · 下载完自动 OCR"
            % (jid, len(pids), total))
        self._goto_dlmon(jid)

    def _goto_dlmon(self, job_id: int = 0):
        """跳到 2.5.1 下载监控（找不到就退回提示，不影响后台任务）。"""
        try:
            win = self.window()
            tabs = getattr(win, "tabs", None)
            if not tabs:
                return
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if w is not None and w.__class__.__name__ == "DlMonTab":
                    tabs.setCurrentIndex(i)
                    if job_id and hasattr(w, "job_id"):
                        w.job_id = int(job_id)
                    if hasattr(w, "refresh"):
                        try:
                            w.refresh()
                        except Exception:              # noqa: BLE001
                            pass
                    return
        except Exception:                              # noqa: BLE001
            pass

    def _pack_planned(self, d: dict):
        """先给主人看「要并进去多少件」，确认后才真写库。"""
        self.btn_pack.setEnabled(True)
        self.btn_pack.setText("打包成一个数据包")
        if not d.get("ok"):
            self.out.appendPlainText(f"打包前统计失败：{d.get('error')}")
            return
        per = d.get("per") or []
        lines = [f"· {x['pack_code']}：{x['products']} 件" for x in per[:12]]
        if len(per) > 12:
            lines.append(f"… 另外还有 {len(per) - 12} 个包")
        ok = QMessageBox.question(
            self, "打包成一个数据包？",
            "%s\n\n%d 个包 · 跨包去重后 %d 件商品。\n\n"
            "· 新包是「名册」，商品和图片不复制、不搬动\n"
            "· 原来的包一个都不会删\n"
            "· 同一个商品同时出现在两个包里，只算一次"
            % ("\n".join(lines), len(per), d.get("products") or 0))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.btn_pack.setEnabled(False)
        self.btn_pack.setText("打包中 …")
        self.pack_worker = PackPackWorker(self._pack_codes, "打包", False)
        self.pack_worker.done.connect(self._packed)
        self.pack_worker.start()

    def _packed(self, d: dict):
        self.btn_pack.setEnabled(True)
        self.btn_pack.setText("打包成一个数据包")
        if d.get("ok"):
            self.out.appendPlainText("✓ " + str(d.get("message") or ""))
            dd = d.get("dedup") or {}
            if dd:
                self.out.appendPlainText(
                    "  去重：%s 条引用 → %s 件（重复 %s 条已合并）"
                    % (dd.get("raw"), dd.get("unique"),
                       int(dd.get("raw") or 0) - int(dd.get("unique") or 0)))
            self.log(str(d.get("message") or "打包完成"))
        else:
            self.out.appendPlainText(f"打包失败：{d.get('error')}")
        self.refresh()

    # ---------------------------------------------------------------- 刷新

    def refresh(self):
        r = bridge.v3_packs()
        if not r.get("ok"):
            self.out.appendPlainText(f"读数据包失败：{r.get('error')}")
            self._rows, self._inbox = [], []
            W.fill_table(self.tb, [])
            W.fill_table(self.tb_in, [])
            return
        self._rows = r.get("items") or []
        self._inbox = r.get("inbox") or []
        self._inbox_dir = r.get("inbox_dir") or ""

        rows, colors = [], []
        for it in self._rows:
            code = it.get("pack_code")
            rows.append([
                packlabel.friendly(it),          # ★ 8.0：改名 / 历史包短名全项目统一
                self.STATUS_TEXT.get(it.get("status"), it.get("status") or ""),
                it.get("products_live"), it.get("images_live"), it.get("rows"),
                it.get("size_kb"), "✔ 可以" if it.get("ready") else "✘ 不行",
                it.get("source_name") or it.get("source_file") or "",
                it.get("converted_at") or "",
            ])
            colors.append(theme.SUCCESS if it.get("ready") else theme.DANGER)
        W.fill_table(self.tb, rows)
        try:
            for i, col in enumerate(colors):
                self.tb.item(i, 6).setForeground(QBrush(QColor(col)))
        except Exception:  # noqa: BLE001
            pass

        W.fill_table(self.tb_in, [[f["name"], f["rows"], f["size_kb"]] for f in self._inbox])
        self.lb_tip.setText(
            f"共 {len(self._rows)} 个数据包，其中 {sum(1 for x in self._rows if x.get('ready'))} 个可进第 3 步"
            f"　·　收件箱 {len(self._inbox)} 个待转换文件")


# ============================================================
#  3 检测打码
# ============================================================
class DetectTab(StepTab):
    title = "4 · 检测打码"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.worker: StackWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 状态卡
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.c_srv = W.StatCard("本地服务 9109", "—")
        self.c_cli = W.StatCard("PicDetector", "—")
        self.c_goods = W.StatCard("商品", "—", theme.BLUE_LIGHT, theme.BLUE)
        self.c_imgs = W.StatCard("图片", "—", theme.SUCCESS_LIGHT, theme.SUCCESS)
        for c in (self.c_srv, self.c_cli, self.c_goods, self.c_imgs):
            cards.addWidget(c)
        root.addLayout(cards)

        # ★ 指定数据包：第三步只认这里选中的那个包（原来这里是写死的）
        pk = QHBoxLayout()
        pk.setSpacing(8)
        lb = QLabel("数据包")
        lb.setStyleSheet(f"color:{theme.TEXT}; font-weight:600;")
        pk.addWidget(lb)
        self.cmb_pack = QComboBox()
        self.cmb_pack.setMinimumWidth(300)
        self.cmb_pack.currentIndexChanged.connect(self._pack_changed)
        pk.addWidget(self.cmb_pack)
        self.btn_pack_reload = QPushButton("刷新包")
        self.btn_pack_reload.clicked.connect(self.refresh_packs)
        pk.addWidget(self.btn_pack_reload)
        self.lb_pack = QLabel("—")
        self.lb_pack.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        pk.addWidget(self.lb_pack)
        pk.addStretch(1)
        root.addLayout(pk)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_start = QPushButton("启动本地链路")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self._start)
        bar.addWidget(self.btn_start)

        self.btn_local = QPushButton("本机引擎检测（免客户端）")
        self.btn_local.setToolTip(
            "直接用本机 PPT-OCR 引擎跑：读字 → 命中违规词 → 涂白 → 传图床 → 回写新URL。\n"
            "和官方客户端用同一个 商品.db，不需要人去点客户端上的按钮。")
        self.btn_local.clicked.connect(self._start_local)
        bar.addWidget(self.btn_local)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.clicked.connect(self._stop)
        bar.addWidget(self.btn_stop)

        self.btn_refresh = QPushButton("刷新状态")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)

        self.lb_stage = QLabel("未启动")
        self.lb_stage.setStyleSheet(f"color:{theme.TEXT_SUB};")
        bar.addWidget(self.lb_stage)
        bar.addStretch(1)
        root.addLayout(bar)

        self.pb = QProgressBar()
        self.pb.setRange(0, 0)          # 忙碌态
        self.pb.setVisible(False)
        root.addWidget(self.pb)

        note = W.Hint(
            "说明：本页只负责「把小猪那条本机链路拉起来」和「看库里的量」，"
            "不做假的进度。真正的逐张检测/打码进度还没有接口暴露出来 —— 那是下一步要接的。"
        )
        root.addWidget(note)

        self.tb = W.make_table(["项", "值"], [220, 460])
        root.addWidget(self.tb, 1)

    def refresh_packs(self):
        """把第 2 步转换成功的数据包列进下拉。"""
        cur = self.cmb_pack.currentData()
        self.cmb_pack.blockSignals(True)
        self.cmb_pack.clear()
        r = bridge.v3_packs()
        items = [x for x in (r.get("items") or []) if x.get("ready")]
        for it in items:
            self.cmb_pack.addItem(
                "%s（%s 商品 / %s 图）" % (packlabel.friendly(it), it.get("products_live"),
                                          it.get("images_live")), it.get("pack_code"))
        if not items:
            self.cmb_pack.addItem("（第 2 步还没有可用的数据包）", "")
        pick = ""
        if cur:
            i = self.cmb_pack.findData(cur)
            if i >= 0:
                pick = cur
        if not pick:
            pick = str((r or {}).get("default") or "")
        if pick:
            i = self.cmb_pack.findData(pick)
            if i >= 0:
                self.cmb_pack.setCurrentIndex(i)
        self.cmb_pack.blockSignals(False)
        self._pack_changed()

    def _pack_changed(self):
        code = self.cmb_pack.currentData() or ""
        if not code:
            self.lb_pack.setText("先去第 2 步导入并转换文件")
            return
        st = bridge.v3_pack_status(code)
        if st.get("ok"):
            self.lb_pack.setText(
                "源文件 %s · 商品 %s · 图片 %s · 缺主图 %s" % (
                    st.get("source_name") or "—", st.get("products_live"),
                    st.get("images_live"), st.get("products_without_main")))
        else:
            self.lb_pack.setText(str(st.get("error"))[:80])

    def _start(self):
        if self.worker and self.worker.isRunning():
            return
        code = self.cmb_pack.currentData() or ""
        if not code:
            QMessageBox.information(self, "先选数据包",
                                    "第 4 步要指定数据包。\n\n请先在第 2 步把一个文件导入并转换成功，再回到这里选中它。")
            return
        st = bridge.v3_pack_status(code)
        if not st.get("ok") or not st.get("ready"):
            QMessageBox.warning(self, "这个包还不能检测",
                                "包「%s」不可用：%s" % (code, "；".join(st.get("reasons") or [st.get("error") or "转换未完成"])))
            return
        # ★ 门禁：没精筛过不许进检测打码 —— 否则等于白跑一遍 OCR
        if not bridge.fine_done_of(code):
            QMessageBox.information(
                self, "这个包还没精筛",
                "检测打码排在第 3 步精筛之后。\n\n"
                "请先去「3 · 商品精筛」把包「%s」跑一遍，通过的商品再来这里打码。\n"
                "（品牌词、违规词、重复品不筛掉就开始 OCR，等于给不该上架的东西打码）" % code)
            return
        conv = bridge.v3_pack_to_pig(code)
        if not conv.get("ok"):
            QMessageBox.warning(self, "导成小猪 19 列失败", str(conv.get("error")))
            return
        self.log("✓ 包「%s」已导成 19 列：%s" % (code, conv.get("message")))
        self._only_csv = conv.get("path") or ""
        if self.worker and self.worker.isRunning():
            return
        self.btn_start.setEnabled(False)
        self.pb.setVisible(True)
        self.lb_stage.setText("启动中 …")
        self.worker = StackWorker(getattr(self, "_only_csv", ""))
        self.worker.note.connect(self._note)
        self.worker.done.connect(self._started)
        self.worker.start()

    def _start_local(self):
        """本机引擎检测：不需要客户端、不需要人点，跑完自己回读结果。"""
        if getattr(self, "local_worker", None) and self.local_worker.isRunning():
            return
        code = self.cmb_pack.currentData() or ""
        if not code:
            QMessageBox.information(self, "先选数据包",
                                    "要指定数据包。请先在「2 · 商品卡导入」把文件导入并转换成功。")
            return
        st = bridge.v3_pack_status(code)
        if not st.get("ok") or not st.get("ready"):
            QMessageBox.warning(self, "这个包还不能检测",
                                "包「%s」不可用：%s" % (code, "；".join(
                                    st.get("reasons") or [st.get("error") or "转换未完成"])))
            return
        if not bridge.fine_done_of(code):
            QMessageBox.information(
                self, "这个包还没精筛",
                "检测打码在第 3 步之后。请先去「3 · 商品精筛」跑一遍包「%s」。" % code)
            return
        conv = bridge.v3_pack_to_pig(code)
        if not conv.get("ok"):
            QMessageBox.warning(self, "导成小猪 19 列失败", str(conv.get("error")))
            return
        self._local_pack = code
        self._local_before = bridge.pig_baseline(code)
        self.log("本机引擎：包「%s」图片基线 %s（已处理 %s / 共 %s）" % (
            code, (self._local_before or {}).get("done"),
            (self._local_before or {}).get("done"), (self._local_before or {}).get("total")))
        self.btn_local.setEnabled(False)
        self.pb.setVisible(True)
        self.lb_stage.setText("本机引擎检测中 …（大包会跑几分钟，可随时看日志）")
        self.local_worker = LocalDetectWorker(code, no_ai=True)
        self.local_worker.done.connect(self._local_done)
        self.local_worker.start()

    def _local_done(self, r: dict):
        self.btn_local.setEnabled(True)
        self.pb.setVisible(False)
        code = getattr(self, "_local_pack", "")
        if not r.get("ok"):
            self.lb_stage.setText("本机引擎检测失败")
            self.log("✗ 本机引擎失败：%s" % (r.get("error") or r.get("tail") or "未知"))
            return
        st = r.get("stat") or {}
        after = bridge.pig_baseline(code)
        before = getattr(self, "_local_before", {}) or {}
        self.log("✓ 本机引擎跑完：违规 %s 张 · 干净 %s 张 · 上传 %s 张 · 失败 %s 张" % (
            st.get("viol"), st.get("clean"), st.get("up"), st.get("fail")))
        self.log("✓ 回读确认：已处理 %s → %s（共 %s）｜违规图 %s 张｜换到新URL %s 张" % (
            before.get("done"), after.get("done"), after.get("total"),
            after.get("violation"), after.get("new_url")))
        self.lb_stage.setText("检测完成：违规 %s 张，已换新图 %s 张" % (
            st.get("viol"), after.get("new_url")))
        self.refresh()

    def _note(self, s: str):
        self.lb_stage.setText(s)
        self.log(s)

    def _started(self, r: dict):
        self.btn_start.setEnabled(True)
        self.pb.setVisible(False)
        if r.get("ok"):
            self.lb_stage.setText("已就绪")
            self.log("✓ 本地链路已就绪：" + " · ".join(r.get("steps") or []))
        else:
            self.lb_stage.setText("启动失败")
            self.log("✗ " + (r.get("error") or "未知"))
            QMessageBox.warning(self, "启动没成功", r.get("error") or "未知")
        self.refresh()

    def _stop(self):
        r = bridge.stop_pig_stack()
        self.log("已停止：" + (" · ".join(r.get("stopped") or []) or "没有需要停的"))
        self.refresh()

    def refresh(self):
        self.refresh_packs()
        st = bridge.pig_status()
        self.c_srv.set_value("在线" if st["server_9109"] else "离线",
                             theme.SUCCESS if st["server_9109"] else theme.DANGER)
        self.c_cli.set_value("运行中" if st["client_running"] else "未启动",
                             theme.SUCCESS if st["client_running"] else theme.TEXT_SUB)

        ov = bridge.pig_overview()
        self.c_goods.set_value(ov.get("goods") or 0)
        self.c_imgs.set_value(ov.get("images") or 0)

        rows = [
            ["本地服务 9109", "在线" if st["server_9109"] else "离线"],
            ["PicDetector 进程", "运行中" if st["client_running"] else "未运行"],
            ["商品库", str(paths.PIG_DB) if st["db_ok"] else "不存在"],
            ["商品 / 图片 / SKU", f'{ov.get("goods")} / {ov.get("images")} / {ov.get("skus")}'],
            ["数据包", str(ov.get("packs"))],
        ]
        for k in ("by_下载状态", "by_类型"):
            if ov.get(k):
                rows.append([k.replace("by_", ""), " · ".join(f"{a} {b}" for a, b in ov[k].items())])
        if ov.get("error"):
            rows.append(["错误", ov["error"]])
        W.fill_table(self.tb, rows)


# ============================================================
#  4 出表
# ============================================================
class ExportTab(StepTab):
    title = "5 · 出表"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.worker: ExportWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("数据包"))
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(200)
        bar.addWidget(self.cmb)
        self.btn_reload = QPushButton("刷新包")
        self.btn_reload.clicked.connect(self.refresh)
        bar.addWidget(self.btn_reload)
        self.btn_go = QPushButton("生成 19 列上货表")
        self.btn_go.setObjectName("Primary")
        self.btn_go.clicked.connect(self._go)
        bar.addWidget(self.btn_go)
        bar.addStretch(1)
        root.addLayout(bar)

        self.tb = W.make_table(["文件", "大小KB", "路径"], [280, 90, 420])
        root.addWidget(self.tb, 1)

        self.out = QPlainTextEdit()
        self.out.setObjectName("LogView")
        self.out.setReadOnly(True)
        root.addWidget(self.out, 1)

    def refresh(self):
        cur = self.cmb.currentText()
        self.cmb.clear()
        self.cmb.addItem("（全部）", "")
        try:
            for r in bridge.list_packs():
                code = r.get("包码")
                if code:
                    self.cmb.addItem(str(code), str(code))
        except Exception:  # noqa: BLE001
            pass
        if cur:
            i = self.cmb.findText(cur)
            if i >= 0:
                self.cmb.setCurrentIndex(i)
        self._list_out()

    def _list_out(self):
        d = paths.PIG_LOCAL / "out"
        rows = []
        if d.exists():
            for f in sorted(d.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:40]:
                rows.append([f.name, round(f.stat().st_size / 1024, 1), str(f)])
        W.fill_table(self.tb, rows)

    def _go(self):
        if self.worker and self.worker.isRunning():
            return
        pack = self.cmb.currentData() or ""
        self.btn_go.setEnabled(False)
        self.btn_go.setText("生成中 …")
        self.out.appendPlainText(f"出表开始：pack={pack or '（全部）'}")
        self.worker = ExportWorker(pack)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, code: int, text: str, out_dir: str):
        self.btn_go.setEnabled(True)
        self.btn_go.setText("生成 19 列上货表")
        self.out.appendPlainText(text or "(无输出)")
        self.out.appendPlainText(f"退出码 {code} · 输出目录 {out_dir}")
        self._list_out()
        if code == 0:
            self.log(f"✓ 出表完成 → {out_dir}")
        else:
            self.log(f"✗ 出表失败，退出码 {code}")


# ============================================================
#  5 设置
# ============================================================
class SettingsTab(StepTab):
    title = "7 · 设置"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        root.addWidget(QLabel("环境自检（存在 / 缺失一目了然，缺什么自己就看得见）"))
        self.tb_env = W.make_table(["项", "路径", "状态"], [180, 460, 80])
        root.addWidget(self.tb_env, 1)

        root.addWidget(QLabel("采集默认值"))
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(QLabel("调试端口"), 0, 0)
        self.ed_port = QSpinBox()
        self.ed_port.setRange(1024, 65535)
        self.ed_port.setValue(int(win.settings.get("cdp_port", 9222)))
        grid.addWidget(self.ed_port, 0, 1)
        grid.addWidget(QLabel("滚动次数"), 0, 2)
        self.ed_scroll = QSpinBox()
        self.ed_scroll.setRange(0, 60)
        self.ed_scroll.setValue(int(win.settings.get("scroll", 6)))
        grid.addWidget(self.ed_scroll, 0, 3)
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self._save)
        grid.addWidget(self.btn_save, 1, 0, 1, 2)
        root.addLayout(grid)

        self.lb_brand = W.Hint("品牌库：读取中 …")
        root.addWidget(self.lb_brand)

    def _save(self):
        self.win.settings["cdp_port"] = self.ed_port.value()
        self.win.settings["scroll"] = self.ed_scroll.value()
        bridge.save_settings(self.win.settings)
        self.log("设置已保存")
        self.refresh()

    def refresh(self):
        rows = []
        for name, path, ok in bridge.env_report():
            rows.append([name, paths.rel(path), "✓" if ok else "✗"])
        W.fill_table(self.tb_env, rows,
                     colors={i: {2: theme.SUCCESS if r[2] == "✓" else theme.DANGER}
                             for i, r in enumerate(rows)})

        try:
            st = bridge.brand_stats()
            if st:
                parts = " · ".join(f"{k} {v}" for k, v in list(st.items())[:5]
                                   if isinstance(v, (int, str)))
                self.lb_brand.setText("品牌库：" + (parts or str(st)))
            else:
                self.lb_brand.setText("品牌库：读不到（后端未就绪）")
        except Exception as e:  # noqa: BLE001
            self.lb_brand.setText(f"品牌库：读取失败 {e}")
