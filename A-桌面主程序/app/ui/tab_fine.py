# -*- coding: utf-8 -*-
"""3 · 商品精筛 —— 第 2 步导入的详情全部就位后，在这里过一遍「能不能上」。

三层判定，都是本机跑：

  · 文本层   品牌鉴定 / 违规词 / 跨类目 / 类目黑名单 / 标题与主图重复
             （词库主表 20,861 条 + 打码词 + keywords.txt 2376 条）
  · **图片层** 把包内每张图下载下来，用第 4 步那台本机 PP-OCR 读字（不联网），
              命中就写清「哪张图的哪段字」：图角色#序号 + 命中词 + OCR 片段 + 匹配置信度。
              可断点续跑：读过的图记在 v3_image_ocr + v3_image.note 里，第二次不重读。
  · **详情层** 用商品描述 / 属性 / 详情图 OCR 合并判：
              描述含违规词(拦)、图文不一致(品牌/材质)、缺关键信息(**只记 warn，不一刀切**)。
  · 定位层   45+ 微信人群 / 品类 / 价位 / 风险（fine_positioning.json）。

**2026-10-22 改版（主人投诉四条，逐条对上）**：

  1. **不设筛选上限**：上限默认 0 = 全部，按钮直接写「筛选全部 N 件」，旁边显示本包件数。
  2. **开跑就有实时日志**：本页自带滚动日志区（只读、自动到底、最多 5000 行），
     先打引擎状态 / 词表条数 / 两个开关 / 本次范围，再逐件打
     `[i/N] pid · 图 x/下 y/OCR z/缓存 c · 命中 h · 判定 · 耗时`。
     后端支持 progress 回调就实时出，没支持就明确写「逐件事件不可用」，不假装有进度。
  3. **本页就能放行**：拦住的勾上 → 「放行所选」→ 状态改回 imported 并刷新表格。
  4. **图数 / OCR 数直接上表**：不用猜 OCR 有没有跑。

**2026-10-22 二改（界面重排，主人五条原话逐条对上）**：

  1. 「实时日志 + 运行日志占得满满的」→ 两块日志**合成一个 QTabWidget**（`实时` / `运行`），
     默认停在「实时」；整块日志区可折叠（「日志 ▾」按钮），收起后表格拿回全部竖向空间。
     **页面上只有一个日志实例**，不再上下各摊一块。
  2. 「字体小 10%」→ 本文件所有字号收进模块常量 `FS_11/FS_12/FS_13/FS_14/FS_16`
     （12→11 / 13→12 / 14→12 / 16→14），页面级 QSS 统一压一档；
     **不动 theme.py**（别的页在用）。
  3. 「豁免区尾部 80% 空白」→ 主区改成 `QSplitter(Horizontal)`：左边表格 + 日志，
     右边一栏「豁免 / 噪声词」（280–340 宽，**默认收起**，点「豁免 N 条 ▸」展开）。
     原「词库噪声」GroupBox 从正文移除，内容整体搬进右栏。
  4. 「拦我的词在豁免区找不到」→ 右栏候选池**不再只吃后端的 noise(warn 级)**，
     而是把本包证据里出现过的**所有命中词**（含 block 级）+ 后端 noise 合并去重后列出；
     表格「原因」列可右键 **「豁免这个词」**；没有词的结构性判定（人群/品类/价位/信息）
     单独列一节并写明为什么豁免不了。
  5. 「筛完怎么传给第 4 步」→ 工具条新增 **「💾 存为数据包」**，调
     `bridge.v3_save_screen(pack_code, name, only="keep")`，成功后把新包码打在界面上
     （第 4 步包下拉里选它）。

复核后不删数据：命中就标 blocked 并写清原因，随时可在本页勾选放行。

**2026-10-26 三改（主人原话：「这么多信息，你让我一个一个去打勾吗？这个需要有全选的呀，
那些信息都要可以处理的」）**：

  1. **选择条新增三按钮**：`全选` / `全不选` / `反选` —— 纯按钮操作行选中，不依赖 Ctrl+A。
  2. **按判定批量勾选**：`全选拦住` / `全选通过` / `全选待复核` / `清空勾选`。
     三种判定与后端 `flow.fine_screen` 一致（blocked / pending / passed）。
  3. **判定不再折叠成两档**：原来「待复核(pending)」被显示成「通过」—— 界面把
     「没判的」说成「判过了」。现在按 verdict + 商品真实 status 三档显示，橙色「待复核」。
  4. **放行口径修正**：`_checked_pids` 原来只收 blocked，「待复核」行永远放不出去，
     新按钮就成了摆设；现在收 blocked + pending。**passed 一律不动**（它的 status 是
     fine_screened，改回 imported 会把「已精筛」标记抹掉，第 4 步门禁认不出来）。
  5. **N=0 不许静默**：每个批量按钮都会说「将处理 N 条 / 已处理 N 条」，
     N=0 时明确提示「没有选中任何行」，一件都不执行。写库仍在后台线程
     （`ReleaseWorker` / `WaiveWorker`），界面不卡。

**词库治理（v0.5）**：词库里有脏词（如「餐桌」被当违规词、「老船木」被当品牌）。
那种命中会写成 warn「疑似词库噪声」。人一旦确认它确实是通用词，就在右栏点
「豁免勾选的词」—— 该词被置 disabled 并记台账，**以后不再命中**，不用每轮来回搞。
判错了也能一键还原。**图片层与文本层共用同一套豁免。**

后端：taobao-picker\\server\\v3\\flow.py → fine_screen(with_images=, with_detail=, limit=)
      taobao-picker\\server\\v3\\images.py → ocr_product() / ocr_image()
      taobao-picker\\server\\v3\\keywords.py → waive() / unwaive() / waivers()
      taobao-picker\\server\\v3\\finelog.py → data/v3/logs/fine_<时间戳>.jsonl（运行日志）
桥：  bridge.v3_fine_preflight / v3_fine_stream / v3_image_ocr_counts / v3_release_many
      bridge.v3_fine_evidence / v3_product（取结构化 reasons）/ v3_save_screen（存为新包）
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from PyQt6.QtCore import (QItemSelection, QItemSelectionModel, Qt, QThread,
                          pyqtSignal)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                             QListWidget, QMenu, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea, QSpinBox,
                             QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from .. import bridge, paths, theme
from .tabs import StepTab

MAX_LOG_LINES = 5000
RUN_TAIL_LINES = 400          # 「运行」tab 只读 JSONL 尾部这么多行

# ============================================================
#  字号常量 —— 本页统一 **-10%**（主人 2026-10-22：「字体小个 10%」）
#    原值 → 现值：12 → 11 · 13 → 12 · 14 → 12 · 16 → 14
#  ★ 只在本文件内生效；**不碰 theme.py**（第 1/2/4/5 步都在用它）。
# ============================================================
FS_11 = 10
FS_12 = 11
FS_13 = 12
FS_14 = 12
FS_16 = 14
MONO_FONT = "Consolas,'Cascadia Mono','Microsoft YaHei',monospace"

# 右栏（豁免 / 噪声词）宽度
PANEL_W_MIN, PANEL_W_MAX, PANEL_W_CLOSED = 280, 340, 150
# 右侧竖向词库面板宽度（P0-4：品牌词 + 敏感词，主人要求固定在右侧竖列）
WORDS_W_MIN, WORDS_W_MAX, WORDS_W_DEF = 280, 360, 300

_PAGE_QSS = f"""
QWidget {{ font-size: {FS_12}px; }}
QHeaderView::section {{ font-size: {FS_11}px; padding: 4px 6px; }}
QTableWidget::item {{ padding: 2px 5px; }}
QPlainTextEdit#FineLog {{ font-family: {MONO_FONT}; font-size: {FS_12}px;
                          background: #FBFAFE; border-radius: 7px; }}
QLabel#FineStat {{ font-size: {FS_13}px; font-weight: 600; }}
QLabel#FineSub  {{ font-size: {FS_12}px; }}
QTabWidget#FineLogTabs::pane {{ border: 1px solid {theme.BORDER}; border-radius: 7px; }}
QTabWidget#FineLogTabs QTabBar::tab {{ padding: 4px 12px; font-size: {FS_11}px; }}
QPushButton#FineGhost {{ background: transparent; border: 1px solid {theme.BORDER};
                         color: {theme.TEXT_SUB}; padding: 4px 8px; }}
QPushButton#FineGhost:hover {{ background: {theme.PRIMARY_LIGHT}; color: {theme.TEXT}; }}
"""


# ============================================================
#  取事件字段的小工具：后端事件键名可能与预期不同，按别名取，取不到给默认值
# ============================================================
def _hint(text: str) -> QLabel:
    """选择条上的灰色小标签（与页面字号一致，不另立样式）。"""
    lb = QLabel(text)
    lb.setStyleSheet("color:%s; font-size:%dpx;" % (theme.TEXT_SUB, FS_12))
    return lb


_ALIAS = {
    "i": ("i", "idx", "index", "done", "processed", "n_done", "count"),
    "n": ("n", "total", "of", "n_total", "size"),
    "pid": ("pid", "product_id"),
    "title": ("title", "name"),
    "imgs": ("images", "images_total", "n_images", "img", "imgs"),
    "dl": ("downloaded", "images_downloaded", "dl", "downloads"),
    "ocr": ("ocr", "images_ocr", "ocr_done", "ocrs"),
    "cache": ("cached", "images_cached", "cache", "skipped"),
    "hit": ("hits", "hit", "n_hit", "hit_count"),
    "verdict": ("verdict", "result", "judge", "status"),
    "ms": ("ms", "elapsed_ms", "cost_ms", "duration_ms"),
    "sec": ("seconds", "sec", "elapsed", "cost", "duration"),
    "msg": ("msg", "message", "text", "line", "log", "detail"),
}


def _ev(ev: dict, key: str, default=""):
    for k in _ALIAS.get(key, (key,)):
        if k in ev:
            v = ev[k]
            if v is None or v == "":
                continue
            return v
    return default


def _as_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:  # noqa: BLE001
        return default


# ============================================================
#  线程
# ============================================================
class FineWorker(QThread):
    """跑精筛。**必须在线程里**：下载 + OCR 是同步阻塞的，放主线程界面必假死。

    逐件事件走 `ev` 信号（跨线程自动排队），界面收到就追加一行日志。
    """
    done = pyqtSignal(dict)
    ev = pyqtSignal(dict)

    def __init__(self, pids: list, pack_code: str = "", with_images: bool = True,
                 with_detail: bool = True, limit: int = 0, with_ai: bool = True):
        super().__init__()
        self.pids = pids
        self.pack_code = pack_code
        self.with_images = with_images
        self.with_detail = with_detail
        self.limit = limit
        self.with_ai = with_ai

    def _on_ev(self, ev):  # noqa: D102
        try:
            self.ev.emit(ev if isinstance(ev, dict) else {"msg": str(ev)})
        except Exception:  # noqa: BLE001
            pass

    def run(self):  # noqa: D102
        fn = getattr(bridge, "v3_fine_stream", None)
        if fn is None:                    # 老 bridge：退回一次性调用，界面会说明没有逐件事件
            self._on_ev({"kind": "warn",
                         "msg": "bridge 里没有 v3_fine_stream，退回一次性调用（无逐件事件）"})
            self.done.emit(bridge.v3_fine_screen_pro(
                pack_code=self.pack_code, pids=self.pids or None,
                with_images=self.with_images, with_detail=self.with_detail,
                limit=self.limit, with_ai=self.with_ai))
            return
        self.done.emit(fn(pack_code=self.pack_code, pids=self.pids or None,
                          with_images=self.with_images,
                          with_detail=self.with_detail,
                          limit=self.limit, with_ai=self.with_ai,
                          on_event=self._on_ev))


class ReleaseWorker(QThread):
    """批量放行（要写库，放后台，界面不卡）。"""
    done = pyqtSignal(dict)

    def __init__(self, pids: list[int]):
        super().__init__()
        self.pids = list(pids or [])

    def run(self):  # noqa: D102
        fn = getattr(bridge, "v3_release_many", None)
        if fn is not None:
            self.done.emit(fn(self.pids))
            return
        ok, fail = [], []
        for pid in self.pids:
            try:
                r = bridge.v3_release(pid)
            except Exception as e:        # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            if r.get("ok"):
                ok.append(pid)
            else:
                fail.append({"product_id": pid, "error": r.get("error")})
        self.done.emit({"ok": True, "released": ok, "failed": fail, "count": len(ok)})


class WaiveWorker(QThread):
    """豁免提交（会顺带重筛，几十秒），不能卡界面。"""
    done = pyqtSignal(dict)

    def __init__(self, words: list[str], pids: list[int], reason: str):
        super().__init__()
        self.words = words
        self.pids = pids
        self.reason = reason

    def run(self):  # noqa: D102
        self.done.emit(bridge.v3_keyword_waive(self.words, reason=self.reason,
                                               pids=self.pids))


# ============================================================
#  已豁免台账
# ============================================================
class WaiverListDialog(QDialog):
    """已豁免的词台账 —— 判错了在这里还原。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("已豁免的词（判错了可以还原）")
        self.resize(640, 400)
        v = QVBoxLayout(self)
        tip = QLabel("这些词已被判定为「不是违规词」，精筛（含图片层）和打码都不会再命中它们。\n"
                     "还原后立刻恢复命中（原始词典文件从没被改过）。")
        tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.tb = QTableWidget(0, 5)
        self.tb.setHorizontalHeaderLabels(["词", "原因", "当时命中", "时间", ""])
        self.tb.verticalHeader().setVisible(False)
        self.tb.setStyleSheet(f"QTableWidget {{ font-size:{FS_12}px; }}")
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.tb, 1)
        self.restored: list[str] = []
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)
        self._load()

    def _load(self):
        r = bridge.v3_keyword_waivers()
        items = (r.get("items") or []) if r.get("ok") else []
        if not r.get("ok"):
            self.tb.setRowCount(1)
            self.tb.setItem(0, 0, QTableWidgetItem("读不到：" + str(r.get("error"))[:120]))
            return
        self.tb.setRowCount(len(items))
        for i, it in enumerate(items):
            self.tb.setItem(i, 0, QTableWidgetItem(str(it.get("text") or "")))
            self.tb.setItem(i, 1, QTableWidgetItem(str(it.get("reason") or "")[:120]))
            self.tb.setItem(i, 2, QTableWidgetItem(str(it.get("hits") or 0)))
            self.tb.setItem(i, 3, QTableWidgetItem(str(it.get("created_at") or "")[5:19]))
            b = QPushButton("还原")
            b.setFixedWidth(56)
            w = str(it.get("text") or "")
            b.clicked.connect(lambda _=False, ww=w: self._restore(ww))
            self.tb.setCellWidget(i, 4, b)
        if not items:
            self.tb.setRowCount(1)
            self.tb.setItem(0, 0, QTableWidgetItem("还没有豁免过任何词"))
            self.tb.setSpan(0, 0, 1, 5)

    def _restore(self, word: str):
        r = bridge.v3_keyword_unwaive(word)
        if not r.get("ok"):
            QMessageBox.warning(self, "还原失败", str(r.get("error"))[:200])
            return
        self.restored.append(word)
        self._load()


# ============================================================
#  页签
# ============================================================
class FineStepTab(StepTab):
    title = "3 · 商品精筛"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.worker: FineWorker | None = None
        self.releaser: ReleaseWorker | None = None
        self.waiver: WaiveWorker | None = None
        self._pids: list[int] = []
        self._rows: list[dict] = []
        self._row_words: list[list[dict]] = []
        self._noise: list[dict] = []
        self._waive_map: dict[str, dict] = {}
        self._reasons_cache: dict[int, list[dict]] = {}
        self._acc: dict[str, int] = {}
        self._rendering = False
        self._total_n = 0
        self._counts: dict[str, dict] = {}
        self._code = ""
        self._log_path = ""
        self._log_open = True
        self._waiver_open = False                 # 右栏默认收起

        self.setStyleSheet(_PAGE_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(5)

        tip = QLabel("三层判定：文本（品牌/违规词/跨类目/重复）· 图片（下载后本机 OCR 读字）"
                     "· 详情（描述与详情图）。缺描述/缺详情图只记「警告」不拦货。"
                     "默认整包全量筛；拦住的在本页勾选放行，或右键原因里的词直接豁免。")
        tip.setWordWrap(True)
        tip.setObjectName("FineSub")
        tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        tip.setToolTip("第 2 步把详情导进来之后在这里过三层。读过的图不会重读，可以分批跑。\n"
                       "「缺描述/缺详情图/信息太薄」只记 warn —— 缺信息不等于不能上。\n"
                       "词库脏词（如「餐桌」被当违规词）可在右栏豁免，图片层同样生效。")
        root.addWidget(tip)

        # ★ 2026-10-25（P0-4）：词库管理**不再横向铺在本页顶部** ——
        #   主人已多次要求「品牌词管理和敏感词管理放在页面右侧的竖向独立面板」。
        #   这里只留占位，真正的控件在下面「右栏一：竖向词库面板」里创建（vertical=True）。
        self.wm = None
        self.wm_sens = None

        # ---------------- 第一行：包 / 两层开关 / 范围 / 跑 / 存包 ----------------
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("数据包"))
        self.cmb_pack = QComboBox()
        self.cmb_pack.setMinimumWidth(190)
        self.cmb_pack.currentIndexChanged.connect(self._pack_changed)
        bar.addWidget(self.cmb_pack)

        self.cb_img = QCheckBox("图片层")
        self.cb_img.setChecked(True)
        self.cb_img.setToolTip("下载包内图片并用本机 PP-OCR 读字；读过的图会跳过（断点续跑）")
        bar.addWidget(self.cb_img)

        self.cb_det = QCheckBox("详情层")
        self.cb_det.setChecked(True)
        self.cb_det.setToolTip("用描述/属性/详情图 OCR 判定：描述违规词、图文不一致、缺关键信息")
        bar.addWidget(self.cb_det)

        # ★ 2026-10-27 主人的口径：**整体判定权交 AI**，规则层从此只摆证据。
        #   放成勾选框而不是写死，是因为「模型今天是免费、明天可能收费/下线」——
        #   关掉它就能退回纯关键词判定，不至于因为模型不可用整步停摆。
        self.cb_ai = QCheckBox("AI 判官")
        self.cb_ai.setChecked(True)
        self.cb_ai.setToolTip("由 AI 读证据给结论并写点评（每条拦住/放行都有 AI 判词）。\n"
                              "关掉 = 退回关键词判定，结论会标注「AI 未参与」。\n"
                              "换模型/探活在右侧「⚙ 设置 → AI 模型」。")
        bar.addWidget(self.cb_ai)

        # 主人原话：「我还是没有看到 AI 的调节窗口」。放在勾选框旁边，一步就能打开。
        self.btn_ai_cfg = QPushButton("⚙ AI 设置")
        self.btn_ai_cfg.setToolTip("打开右侧「设置 → AI 模型」：\n"
                                   "· 看现在用的是哪个模型\n"
                                   "· 一键探活整池（谁还活着）\n"
                                   "· 点一下换模型 / 加新的免费模型")
        self.btn_ai_cfg.clicked.connect(self._open_ai_cfg)
        bar.addWidget(self.btn_ai_cfg)

        bar.addWidget(QLabel("上限"))
        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(0, 100000)
        self.sp_limit.setValue(0)                       # ★ 默认 0 = 全量
        self.sp_limit.setSuffix(" 件")
        self.sp_limit.setToolTip("0 = 全部（默认）。只有想先小批试跑时才填数字；"
                                 "分批跑不会重复读已读过的图")
        self.sp_limit.setFixedWidth(90)
        self.sp_limit.valueChanged.connect(self._limit_changed)
        bar.addWidget(self.sp_limit)

        self.lb_limit_hint = QLabel("0 = 全部")
        self.lb_limit_hint.setObjectName("FineSub")
        self.lb_limit_hint.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        bar.addWidget(self.lb_limit_hint)

        self.lb_total = QLabel("本包 0 件")
        self.lb_total.setStyleSheet(
            f"color:{theme.TEXT}; font-size:{FS_12}px; font-weight:600;")
        bar.addWidget(self.lb_total)

        self.btn_run = QPushButton("▶ 筛选全部")
        self.btn_run.setObjectName("Primary")
        self.btn_run.setToolTip("筛整个包（顺序＝表格自上而下，也就是入库顺序）")
        self.btn_run.clicked.connect(self._run)
        bar.addWidget(self.btn_run)

        # ★ 2026-10-27 主人要求：「最好是有一个勾选多少个项目，就筛选勾选的这种选项」
        self.btn_run_pick = QPushButton("▶ 筛选勾选 0 件")
        self.btn_run_pick.setToolTip("只筛表格里**打了勾**的那些商品。\n"
                                     "先在上面的勾选框里选，或点右边「选择 ▾」批量选。")
        self.btn_run_pick.setEnabled(False)
        self.btn_run_pick.clicked.connect(self._run_picked)
        bar.addWidget(self.btn_run_pick)

        self.btn_sel = QPushButton("选 ▾")
        _m = QMenu(self)
        # ★ 2026-10-27 主人原话：「我按着 shift 滚滑轮多选几件，它只有一个选中状态，
        #   但前面打勾这个功能是没有的，应该给我加上」—— 所以这一项放在**第一条**。
        _m.addAction("☑ 打勾（按住 Shift 选中的这几行）", self._check_selected_rows)
        _m.addSeparator()
        _sec = _m.addAction("— 按范围打勾 —")
        _sec.setEnabled(False)
        _m.addAction("☑ 全部", self._select_all_rows)
        _m.addAction("前 10 件", lambda: self._check_first(10))
        _m.addAction("前 20 件", lambda: self._check_first(20))
        _m.addAction("前 50 件", lambda: self._check_first(50))
        _m.addAction("当前看得见的这几行", self._check_visible)
        _m.addSeparator()
        _sec2 = _m.addAction("— 清除 —")
        _sec2.setEnabled(False)
        _m.addAction("☐ 全不选（取消打勾）", self._select_no_rows)
        _m.addAction("⇄ 反选（都翻过来）", self._select_invert)
        self.btn_sel.setMenu(_m)
        self.btn_sel.setToolTip(
            "Shift 多选只是「选中」，勾选框要另外打勾 —— 用第一项「打勾（选中的这几行）」。\n"
            "「前 N 件」＝表格自上而下前 N 行（入库顺序），不是随机取。")
        bar.addWidget(self.btn_sel)

        self.btn_save = QPushButton("🧹 清掉未通过 → 筛选后包")
        self.btn_save.setToolTip(
            "精筛后收口：把本包「通过」的商品另存成一个**筛选后数据包**，\n"
            "第 4 步（检测打码）与出表默认只读它 —— 未通过的商品不再进后续环节。\n"
            "不删任何东西：原包、223 件商品与判定记录都还在，随时可回滚。\n"
            "（精筛跑完也会自动做一次，重复点只会复用同一个包）")
        self.btn_save.clicked.connect(self._save_as_pack)
        bar.addWidget(self.btn_save)

        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self.refresh)
        bar.addWidget(self.btn_reload)
        bar.addStretch(1)
        root.addLayout(bar)

        # ---------------- 第二行：进度条 + 进度文字 ----------------
        prow = QHBoxLayout()
        prow.setSpacing(6)
        self.pb = QProgressBar()
        self.pb.setRange(0, 100)
        self.pb.setValue(0)
        self.pb.setFormat("待开始")
        self.pb.setTextVisible(True)
        self.pb.setFixedHeight(16)
        prow.addWidget(self.pb, 3)
        self.lb_prog = QLabel("已处理 0/0")
        self.lb_prog.setStyleSheet(
            f"color:{theme.TEXT}; font-size:{FS_12}px; font-weight:600;")
        prow.addWidget(self.lb_prog, 4)
        root.addLayout(prow)

        # ---------------- 第三行：总账（左）+ 层摘要（右），各一行不换行 ----------------
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.lb_stat = QLabel("—")
        self.lb_stat.setObjectName("FineStat")
        self.lb_stat.setStyleSheet(
            f"color:{theme.TEXT}; font-size:{FS_13}px; font-weight:600;")
        srow.addWidget(self.lb_stat, 3)
        self.lb_layer = QLabel("")
        self.lb_layer.setObjectName("FineSub")
        self.lb_layer.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        srow.addWidget(self.lb_layer, 2)
        root.addLayout(srow)

        # ================= 主区：左（表格+日志） / 右（豁免栏） =================
        self.split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)

        self.vsplit = QSplitter(Qt.Orientation.Vertical)

        # ★ 2026-10-27 主人原话：「3.0 的筛选全交由 AI 来操作，不要再有那些词条词断来筛。
        #   里面的什么证据啊、原因啊，这东西其实都应该是不存在的，只有 AI 点评。」
        #   所以表格从 7 列砍到 4 列：勾选 / 商品 / 判定 / AI 点评。
        #   图数、OCR 数、逐条理由都还在（tooltip、详情窗里能看），只是不再占列。
        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["", "商品", "判定", "AI 点评"])
        # ★ 2026-10-26 主人原话：「这些列表不能批量处理，批量选择，增减三查都不能批量做。」
        #   原来这里没设任何选择模式 —— Qt 默认单选，所以 Ctrl 点、Shift 拉、Ctrl+A 全都不生效，
        #   下面那个「豁免勾选的词」也就永远只能豁免一个。改成整行多选。
        self.tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tb.setToolTip("支持批量选择：Ctrl 点加选、Shift 拉一片、Ctrl+A 全选\n"
                           "点商品名 → 打开详情窗（看图核对）")
        self.tb.verticalHeader().setVisible(False)
        self.tb.setWordWrap(True)
        self.tb.setStyleSheet(f"QTableWidget {{ font-size:{FS_12}px; }}")
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tb.setColumnWidth(0, 26)
        self.tb.setColumnWidth(1, 340)
        self.tb.setColumnWidth(3, 520)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb.itemChanged.connect(self._on_item_changed)
        # 点商品名 → 弹独立详情窗（2026-10-27 主人要求：跟 2.5 商品库一样点开看详情）
        self.tb.cellClicked.connect(self._cell_clicked)
        # 右键：对「原因」里的词直接豁免
        self.tb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tb.customContextMenuRequested.connect(self._ctx_menu)
        self.tb.setToolTip("支持批量选择：Ctrl 点加选、Shift 拉一片、Ctrl+A 全选\n"
                           "右键任意一行 → 「豁免这个词」，把那句拦你的词判成不是违规词")
        self.vsplit.addWidget(self.tb)

        # ---------------- 日志：实时 / 运行 两个 tab，整块可折叠 ----------------
        self.logtabs = QTabWidget()
        self.logtabs.setObjectName("FineLogTabs")
        self.logtabs.setDocumentMode(True)

        self.logv = QPlainTextEdit()
        self.logv.setObjectName("FineLog")
        self.logv.setReadOnly(True)
        self.logv.setMaximumBlockCount(MAX_LOG_LINES)
        self.logv.setMinimumHeight(90)
        self.logtabs.addTab(self.logv, "实时")

        runpane = QWidget()
        rv = QVBoxLayout(runpane)
        rv.setContentsMargins(4, 4, 4, 4)
        rv.setSpacing(3)
        rh = QHBoxLayout()
        rh.setSpacing(6)
        self.lb_runpath = QLabel("运行日志：还没跑过（跑一次会写一份 fine_<时间>.jsonl）")
        self.lb_runpath.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_11}px;")
        rh.addWidget(self.lb_runpath, 1)
        self.btn_run_reload = QPushButton("重读")
        self.btn_run_reload.setObjectName("FineGhost")
        self.btn_run_reload.setFixedWidth(56)
        self.btn_run_reload.clicked.connect(self._load_run_tail)
        rh.addWidget(self.btn_run_reload)
        rv.addLayout(rh)
        self.runv = QPlainTextEdit()
        self.runv.setObjectName("FineLog")
        self.runv.setReadOnly(True)
        self.runv.setMaximumBlockCount(RUN_TAIL_LINES)
        self.runv.setMinimumHeight(70)
        rv.addWidget(self.runv, 1)
        self.logtabs.addTab(runpane, "运行（JSONL 尾部）")
        self.logtabs.currentChanged.connect(self._on_log_tab)
        self.vsplit.addWidget(self.logtabs)
        self.vsplit.setSizes([520, 170])

        lv.addWidget(self.vsplit, 1)

        # ============ 选择条：全选 / 全不选 / 反选 + 按判定勾选 ============
        # ★ 2026-10-26 主人原话：「这么多信息，你让我一个一个去打勾吗？这个需要有全选的呀，
        #   那些信息都要可以处理的」。
        #   原状：只有「全选拦住」一个；「全选」只能靠键盘 Ctrl+A（很多人根本不知道）。
        #   现在：三种判定的勾选 + 三种行选择**全部做成按钮**，一律不依赖键盘。
        #   判定口径与后端一致（flow.fine_screen）：blocked=拦住 / pending=待复核 / passed=通过。
        selbar = QHBoxLayout()
        selbar.setSpacing(6)

        # ★ 2026-10-26 主人原话：「全选它其实就是应该是全部勾选。我现在选了全选，它不会打勾。
        #   还是得让我一个一个去打勾。那你还要加一个功能，全选，然后打勾选择之类的这种逻辑」
        #   原状：这三个按钮只调 selectAll()/clearSelection()，只让行变蓝底高亮，
        #   **勾选框一个都不动** —— 而「放行所选」读的是勾选框 ⇒ 全选之后一点放行就说
        #   「一条都没勾」。这是逻辑断层，不是用户不会用。
        #   现在：三个按钮**同时做选中行 + 打勾**，提示里两个数都报出来。
        self.btn_sel_all = QPushButton("☑ 全选（选中+打勾）")
        self.btn_sel_all.setToolTip(
            "把全部行**都打勾**，同时选中它们（等价 Ctrl+A，但不依赖键盘）。\n"
            "打勾决定「✅ 放行所选」处理哪些商品；只勾「拦住 / 待复核」会被放行，"
            "勾了「通过」也会被跳过。")
        self.btn_sel_all.clicked.connect(self._select_all_rows)
        selbar.addWidget(self.btn_sel_all)

        self.btn_sel_none = QPushButton("☐ 全不选（取消打勾）")
        self.btn_sel_none.setToolTip("取消全部行选中，并把全部勾选框清空")
        self.btn_sel_none.clicked.connect(self._select_no_rows)
        selbar.addWidget(self.btn_sel_none)

        self.btn_sel_inv = QPushButton("⇄ 反选（都翻过来）")
        self.btn_sel_inv.setToolTip("已选中的取消、没选中的选中；勾选框同样逐个翻转")
        self.btn_sel_inv.clicked.connect(self._select_invert)
        selbar.addWidget(self.btn_sel_inv)

        selbar.addWidget(_hint("｜按判定勾选"))
        self.btn_sel_blocked = QPushButton("全选拦住")
        self.btn_sel_blocked.setToolTip("勾上全部「拦住」的行（再点「✅ 放行所选」）")
        self.btn_sel_blocked.clicked.connect(self._select_all_blocked)
        selbar.addWidget(self.btn_sel_blocked)

        self.btn_sel_passed = QPushButton("全选通过")
        self.btn_sel_passed.setToolTip("勾上全部「通过」的行（它们已可继续流转，放行不会动它们）")
        self.btn_sel_passed.clicked.connect(self._select_all_passed)
        selbar.addWidget(self.btn_sel_passed)

        self.btn_sel_pending = QPushButton("全选争议件")
        self.btn_sel_pending.setToolTip("勾上全部「争议件」的行（模型没把握、极少数，才需要你看一眼）")
        self.btn_sel_pending.clicked.connect(self._select_all_pending)
        selbar.addWidget(self.btn_sel_pending)

        self.btn_uncheck = QPushButton("清空勾选")
        self.btn_uncheck.clicked.connect(self._clear_checks)
        selbar.addWidget(self.btn_uncheck)

        self.lb_selstat = _hint("")
        selbar.addWidget(self.lb_selstat, 1)
        lv.addLayout(selbar)

        # ---------------- 放行条（含日志折叠按钮） ----------------
        rbar = QHBoxLayout()
        rbar.setSpacing(6)
        self.btn_log_toggle = QPushButton("日志 ▾")
        self.btn_log_toggle.setObjectName("FineGhost")
        self.btn_log_toggle.setFixedWidth(84)
        self.btn_log_toggle.setToolTip("收起 / 展开下面的日志区（收起后表格拿回全部高度）")
        self.btn_log_toggle.clicked.connect(self._toggle_log)
        rbar.addWidget(self.btn_log_toggle)

        self.lb_pick = QLabel("已勾选 0 件")
        self.lb_pick.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        rbar.addWidget(self.lb_pick)

        self.btn_release = QPushButton("✅ 放行所选")
        self.btn_release.setObjectName("Primary")
        self.btn_release.setToolTip("把勾选的「刷掉 / 争议件」改回可继续流转"
                                    "（只改状态，不删数据；「通过」的不动）")
        self.btn_release.clicked.connect(self._release)
        rbar.addWidget(self.btn_release)

        rbar.addStretch(1)
        self.lb_saved = QLabel("")
        self.lb_saved.setStyleSheet(f"color:{theme.SUCCESS}; font-size:{FS_12}px;")
        rbar.addWidget(self.lb_saved)
        lv.addLayout(rbar)

        self.split.addWidget(left)
        self.split.setStretchFactor(0, 1)

        # ---------------- 右栏一：竖向词库面板（品牌词 / 敏感词） ----------------
        # P0-4 §6.2：右侧固定宽度竖栏，内部垂直排列、可独立滚动，不遮挡左侧列表。
        self.pnl_words = QScrollArea()
        self.pnl_words.setObjectName("WordsPanel")
        self.pnl_words.setWidgetResizable(True)
        self.pnl_words.setMinimumWidth(WORDS_W_MIN)
        self.pnl_words.setMaximumWidth(WORDS_W_MAX)
        self.pnl_words.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(2, 2, 2, 2)
        hv.setSpacing(6)
        try:
            from .word_manager import SensitiveWordManager, WordManager
            self.wm = WordManager(on_changed=self._on_words_changed, vertical=True)
            hv.addWidget(self.wm, 3)
            self.wm_sens = SensitiveWordManager(on_changed=self._on_words_changed,
                                                vertical=True)
            hv.addWidget(self.wm_sens, 2)
        except Exception as _e:          # noqa: BLE001  词表块坏了不能拖垮精筛页
            self.wm = None
            self.wm_sens = None
            hv.addWidget(QLabel("词表管理块没加载：%s: %s" % (type(_e).__name__, _e)))
            self.log("词表管理块没加载：%s: %s" % (type(_e).__name__, _e))
        self.pnl_words.setWidget(holder)

        # ---------------- 右栏：豁免 / 噪声词（默认收起） ----------------
        self.pnl_waive = QWidget()
        self.pnl_waive.setObjectName("WaiverPanel")
        pw = QVBoxLayout(self.pnl_waive)
        pw.setContentsMargins(4, 0, 0, 0)
        pw.setSpacing(4)

        self.btn_waive_head = QPushButton("豁免 0 条 ▸")
        self.btn_waive_head.setObjectName("FineGhost")
        self.btn_waive_head.setToolTip("展开右栏：本包命中的词都在这儿，勾上就能豁免")
        self.btn_waive_head.clicked.connect(lambda: self._set_waiver_open(not self._waiver_open))
        pw.addWidget(self.btn_waive_head)

        self.waive_body = QWidget()
        wb = QVBoxLayout(self.waive_body)
        wb.setContentsMargins(0, 0, 0, 0)
        wb.setSpacing(4)

        self.lb_noise = QLabel("跑一次精筛后，这里列出本包命中过的词（含「拦住」的）")
        self.lb_noise.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_12}px;")
        self.lb_noise.setWordWrap(True)
        wb.addWidget(self.lb_noise)

        self.tb_noise = QTableWidget(0, 3)
        self.tb_noise.setHorizontalHeaderLabels(["", "词", "命中"])
        # ★ 2026-10-26：候选词池同样要多选 —— 否则「豁免勾选的词」一次只能处理一个。
        self.tb_noise.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb_noise.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tb_noise.verticalHeader().setVisible(False)
        self.tb_noise.setStyleSheet(f"QTableWidget {{ font-size:{FS_12}px; }}")
        nh = self.tb_noise.horizontalHeader()
        nh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        nh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        nh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tb_noise.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        wb.addWidget(self.tb_noise, 2)

        self.btn_waive = QPushButton("🚫 豁免勾选的词")
        self.btn_waive.setToolTip("确认这些词不是违规词 → 置 disabled 并记台账，以后不再命中")
        self.btn_waive.clicked.connect(self._waive_selected)
        self.btn_waive_all = QPushButton("全选词")
        self.btn_waive_all.setToolTip("把候选词池里的词全部勾上（再一次处理完，不用一个个点）")
        self.btn_waive_all.clicked.connect(self._check_all_words)
        wrow = QHBoxLayout()
        wrow.setSpacing(4)
        wrow.addWidget(self.btn_waive_all)
        wrow.addWidget(self.btn_waive, 1)
        wb.addLayout(wrow)

        self.btn_waivers = QPushButton("查看已豁免 / 还原")
        self.btn_waivers.clicked.connect(self._open_waivers)
        wb.addWidget(self.btn_waivers)

        self.btn_struct = QPushButton("🔎 取结构性原因")
        self.btn_struct.setObjectName("FineGhost")
        self.btn_struct.setToolTip("逐条读被拦住商品的结构化判定（只读库，不重跑 OCR）")
        self.btn_struct.clicked.connect(self._fetch_struct_reasons)
        wb.addWidget(self.btn_struct)

        self.lb_struct = QLabel("不可豁免（结构性判定）")
        self.lb_struct.setStyleSheet(
            f"color:{theme.WARNING}; font-size:{FS_12}px; font-weight:600;")
        wb.addWidget(self.lb_struct)
        self.lb_struct_tip = QLabel(
            "这些不是「某个词」引起的，是按整件商品算的：人群是否对得上 45+、品类是不是在"
            "排除名单、价位、信息够不够。豁免词表改不了它们 —— 要改得改定位规则"
            "（fine_positioning.json）或者换货。")
        self.lb_struct_tip.setWordWrap(True)
        self.lb_struct_tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:{FS_11}px;")
        wb.addWidget(self.lb_struct_tip)

        self.lst_struct = QListWidget()
        self.lst_struct.setWordWrap(True)
        self.lst_struct.setStyleSheet(f"QListWidget {{ font-size:{FS_11}px; }}")
        wb.addWidget(self.lst_struct, 1)
        pw.addWidget(self.waive_body, 1)

        self.split.addWidget(self.pnl_words)
        self.split.setStretchFactor(1, 0)
        self.split.addWidget(self.pnl_waive)
        self.split.setStretchFactor(2, 0)
        self.split.setSizes([760, WORDS_W_DEF, PANEL_W_CLOSED])
        root.addWidget(self.split, 1)

        self._set_waiver_open(False)
        self._limit_changed()

    # ============================================================
    #  折叠：右栏 / 日志区
    # ============================================================
    def _set_waiver_open(self, on: bool):
        """右栏展开 / 收起（收起时只留一行「豁免 N 条 ▸」）。"""
        self._waiver_open = bool(on)
        self.waive_body.setVisible(self._waiver_open)
        n = len([v for v in (self._waive_map or {}).values()
                 if v.get("waivable", True)])
        self.btn_waive_head.setText("豁免 %d 条 %s" % (n, "▾" if self._waiver_open else "▸"))
        if self._waiver_open:
            self.pnl_waive.setMinimumWidth(PANEL_W_MIN)
            self.pnl_waive.setMaximumWidth(PANEL_W_MAX)
            self.split.setSizes([max(320, self.split.width() - PANEL_W_MIN - WORDS_W_DEF),
                                 WORDS_W_DEF, PANEL_W_MIN])
        else:
            self.pnl_waive.setMinimumWidth(PANEL_W_CLOSED)
            self.pnl_waive.setMaximumWidth(PANEL_W_CLOSED)
            self.split.setSizes([max(320, self.split.width() - PANEL_W_CLOSED - WORDS_W_DEF),
                                 WORDS_W_DEF, PANEL_W_CLOSED])

    def _toggle_log(self):
        self._set_log_open(not self._log_open)

    def _set_log_open(self, on: bool):
        self._log_open = bool(on)
        self.logtabs.setVisible(self._log_open)
        self.btn_log_toggle.setText("日志 ▾" if self._log_open else "日志 ▸（点开）")
        if self._log_open:
            self.btn_log_toggle.setFixedWidth(84)
            self.vsplit.setSizes([max(200, self.vsplit.height() - 170), 170])
        else:
            self.btn_log_toggle.setFixedWidth(110)

    # ============================================================
    #  日志
    # ============================================================
    def log(self, s: str):
        """本页日志：写进第 3 步自己的滚动区；关键行同时进全局日志。"""
        txt = str(s)
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in txt.splitlines() or [""]:
            self.logv.appendPlainText("[%s] %s" % (stamp, line))
        sb = self.logv.verticalScrollBar()
        sb.setValue(sb.maximum())
        if txt.startswith(("✓", "✗", "▶", "⚠")):
            try:
                self.win.log("[第3步] " + txt)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 「运行」tab：JSONL 尾部 ----------------
    @staticmethod
    def _run_log_dir() -> str:
        try:
            return str(paths.TAOBAO_DIR / "data" / "v3" / "logs")
        except Exception:  # noqa: BLE001
            return ""

    def _current_jsonl(self) -> str:
        p = self._log_path or ""
        if p and os.path.isfile(p):
            return p
        d = self._run_log_dir()
        if not d or not os.path.isdir(d):
            return ""
        try:
            fs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")]
            if not fs:
                return ""
            fs.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return fs[0]
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _tail(path: str, n: int) -> list[str]:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                data = b""
                while pos > 0 and data.count(b"\n") <= n:
                    step = min(8192, pos)
                    pos -= step
                    f.seek(pos)
                    data = f.read(step) + data
            return data.decode("utf-8", "replace").splitlines()[-n:]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _fmt_jsonl(s: str) -> str:
        s = (s or "").strip()
        if not s:
            return ""
        try:
            d = json.loads(s)
        except Exception:  # noqa: BLE001
            return s[:300]
        if not isinstance(d, dict):
            return s[:300]
        ts = str(d.get("ts") or "")
        if "T" in ts:
            ts = ts.split("T")[-1][:8]
        parts = [ts] if ts else []
        kind = str(d.get("kind") or d.get("type") or "")
        if kind:
            parts.append(kind)
        for k, label in (("idx", "i"), ("total", "n"), ("pid", "pid"),
                         ("verdict", "判定"), ("images", "图"),
                         ("downloaded", "下载"), ("ocr", "OCR"),
                         ("cached", "缓存"), ("hit_images", "命中"),
                         ("seconds", "秒"), ("passed", "通过"),
                         ("blocked", "拦住"), ("remaining", "剩余"),
                         ("processed", "已处理")):
            v = d.get(k)
            if v is not None and v != "":
                parts.append("%s=%s" % (label, v))
        if d.get("title"):
            parts.append(str(d["title"])[:30])
        if d.get("msg"):
            parts.append(str(d["msg"])[:120])
        return " · ".join(parts)

    def _load_run_tail(self):
        p = self._current_jsonl()
        if not p:
            self.lb_runpath.setText("运行日志：还没有（跑一次精筛会生成 fine_<时间>.jsonl）")
            self.runv.setPlainText("")
            return
        self.lb_runpath.setText(p)
        self.lb_runpath.setToolTip(p)
        lines = self._tail(p, RUN_TAIL_LINES)
        if not lines:
            self.runv.setPlainText("（日志还是空的，或读不出来）")
            return
        self.runv.setPlainText("\n".join(x for x in (self._fmt_jsonl(s) for s in lines) if x))
        sb = self.runv.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_log_tab(self, i: int):
        if i == 1:
            self._load_run_tail()

    # ============================================================
    #  数据
    # ============================================================
    def refresh(self):
        self._load_packs()
        self._load_evidence()

    def _load_packs(self):
        cur = self.cmb_pack.currentData()
        r = bridge.v3_packs()
        items = r.get("items") if isinstance(r, dict) else None
        self.cmb_pack.blockSignals(True)
        self.cmb_pack.clear()
        for it in (items or []):
            code = it.get("pack_code") or ""
            n = it.get("products_live") or it.get("products") or 0
            self.cmb_pack.addItem("%s（%s 件）" % (code, n), code)
        self.cmb_pack.blockSignals(False)
        if cur:
            i = self.cmb_pack.findData(cur)
            if i >= 0:
                self.cmb_pack.setCurrentIndex(i)
        self._pack_changed()

    def _pack_changed(self):
        code = self.cmb_pack.currentData() or ""
        self._code = code
        if not code:
            self.lb_stat.setText("先去第 2 步导入并转换一个数据包")
            self.lb_total.setText("本包 0 件")
            self.lb_layer.setText("")
            self._pids = []
            self._total_n = 0
            self._limit_changed()
            return
        r = bridge.v3_pack_pids(code)
        self._pids = (r.get("pids") or []) if r.get("ok") else []
        if not r.get("ok"):
            self.lb_stat.setText("读这个包的商品失败：%s" % str(r.get("error"))[:90])
            self.lb_total.setText("本包 0 件")
            self._total_n = 0
            self._limit_changed()
            return
        self._total_n = len(self._pids)
        self.lb_total.setText("本包 %d 件" % self._total_n)
        done_at = bridge.fine_done_of(code)
        self.lb_stat.setText("包「%s」· %d 件 · %s"
                             % (code, self._total_n,
                                ("已精筛于 %s" % done_at) if done_at else "还没精筛过"))
        ocr = bridge.v3_ocr_status(code)
        if ocr.get("ok"):
            av = ocr.get("available") or {}
            self.lb_layer.setText(
                "引擎 %s · 已读 %s 张图（命中 %s 张）"
                % ("可用" if av.get("ok") else "不可用",
                   ocr.get("ocr_images", 0), ocr.get("hit_images", 0)))
            self.lb_layer.setToolTip("OCR 引擎：%s\n本包已读 %s 张图，其中 %s 张有命中"
                                     % (av.get("engine") or "-",
                                        ocr.get("ocr_images", 0),
                                        ocr.get("hit_images", 0)))
        self._limit_changed()
        self._load_evidence()

    def _load_evidence(self):
        """把「上次精筛的判定与证据」摆出来，不重跑 OCR。"""
        code = self._code or ""
        if not code:
            return
        r = bridge.v3_fine_evidence(code, pids=self._pids or None)
        if not r.get("ok"):
            self.log("⚠ 读上次精筛证据失败：%s" % str(r.get("error"))[:160])
            return
        rows = r.get("rows") or []
        if not rows:
            return
        rows.sort(key=lambda x: (0 if x.get("verdict") == "blocked" else 1,
                                 str(x.get("at") or "")))
        self._render_rows([{"product_id": x.get("product_id"),
                            "title": x.get("title"), "verdict": x.get("verdict"),
                            # ★ status 别丢：v3_fine_evidence 把 verdict 归一成 blocked/passed，
                            #   「待复核（pending）」只能靠商品真实状态认回来。
                            "status": x.get("status"),
                            "evidence": x.get("evidence") or [],
                            "why": x.get("why") or "",
                            "n_block": x.get("n_block"), "n_warn": x.get("n_warn")}
                           for x in rows])
        self._refresh_counts()
        self._fill_waiver_panel()

    # ============================================================
    #  上限 / 按钮文案
    # ============================================================
    def _limit_changed(self):
        n = int(self.sp_limit.value() or 0)
        self.lb_limit_hint.setText("0 = 全部" if n == 0
                                   else "只跑前 %d 件（表格自上而下）" % n)
        total = self._total_n
        if n == 0:
            self.btn_run.setText("▶ 筛选全部 %d 件" % total if total else "▶ 筛选全部")
        else:
            self.btn_run.setText("▶ 筛前 %d 件（共 %d）" % (n, total))
        self._update_pick()

    # ============================================================
    #  执行
    # ============================================================
    def _on_words_changed(self, kind, words):
        """词表一改 → 立刻按当前包重筛（第三步本来就有重筛能力，不另造一套）。"""
        cn = {"add": "加为品牌", "remove": "清出品牌", "restore": "撤销人工裁决"}.get(kind, kind)
        self.log("词表已改（%s）：%s" % (cn, "、".join(words or [])))
        if self.wm:
            self.wm.refresh_stats()
        if not self._pids:
            self.log("  当前没有选中数据包，改完的词会在下次精筛时生效")
            return
        if self.worker and self.worker.isRunning():
            self.log("  正在跑，等这一轮结束再重筛才准")
            return
        self.log("  立刻按新词表重筛当前包 …")
        self._run()

    def _run(self, pids=None):
        """筛整包（pids=None）或只筛指定的一批（「筛选勾选」走这里）。"""
        if self.worker and self.worker.isRunning():
            return
        targets = [int(x) for x in pids] if pids else list(self._pids or [])
        if not targets:
            QMessageBox.information(self, "先选数据包",
                                    "第 3 步精筛按数据包跑。\n\n请在第 2 步导入并转换好，再回来选中它。")
            return
        code = self._code or ""
        wi = self.cb_img.isChecked()
        wd = self.cb_det.isChecked()
        wa = self.cb_ai.isChecked()
        lim = int(self.sp_limit.value() or 0)
        if pids:
            lim = 0            # 勾选就是明确指定了哪几件，上限不再参与（否则会静默少跑）
        self._run_code = code
        self._acc = {}
        self._log_header(wi, wd, lim, wa, picked=bool(pids), n_target=len(targets))

        self.btn_run.setEnabled(False)
        self.btn_run_pick.setEnabled(False)
        self.btn_release.setEnabled(False)
        self.pb.setVisible(True)
        nmax = len(targets) if not lim else min(lim, len(targets))
        self.pb.setRange(0, nmax if nmax else 100)
        self.pb.setValue(0)
        self.pb.setFormat("%v/%m 件")
        self.lb_prog.setText("已处理 0/%d · 正在启动引擎…" % nmax)

        self.worker = FineWorker(targets, code, wi, wd, lim, wa)
        self.worker.ev.connect(self._apply_event)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _log_header(self, wi: bool, wd: bool, lim: int, wa: bool = True,
                    picked: bool = False, n_target: int = 0):
        """开跑先把「引擎 / 词表 / 开关 / 范围 / 本包件数」写进日志 —— 用户要的第一条。"""
        code = self._code or ""
        pre = {}
        fn = getattr(bridge, "v3_fine_preflight", None)
        if fn is not None:
            try:
                pre = fn(code) or {}
            except Exception as e:  # noqa: BLE001
                pre = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        n = len(self._pids)
        scope = ("勾选的 %d 件（手动指定，按表格顺序）" % n_target) if picked else (
            ("全部 %d 件（上限 0 = 不设上限）" % n) if not lim
            else "前 %d 件（本包 %d 件，表格自上而下）" % (lim, n))
        self.log("")
        self.log("════════ 精筛开始 %s ════════" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.log("数据包：%s · 本包 %d 件" % (code, n))
        self.log("本次范围：%s" % scope)
        self.log("两层开关：图片层(OCR)=%s · 详情层=%s"
                 % ("开" if wi else "关", "开" if wd else "关"))
        # AI 判官这一行必须显式写出来：它是「谁在判」的唯一交代，不许让用户猜
        if wa:
            st = {}
            try:
                st = bridge.ai_fine_status()
            except Exception as e:                   # noqa: BLE001
                st = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            av = (st.get("available") or {}) if st.get("ok") else {}
            if st.get("ok") and av.get("ok"):
                self.log("AI 判官：开 ｜ 模型 %s ｜ 池内 %s 个候选（失败自动换下一个）"
                         % (av.get("model") or "—", av.get("pool")))
            else:
                self.log("AI 判官：开 但是**不可用**（%s）—— 本批会按关键词规则判，"
                         "结果里会写明「AI 未参与」"
                         % (av.get("why") or st.get("error") or "未知原因"))
        else:
            self.log("AI 判官：关 —— 本批只按关键词规则判，结论会标注「AI 未参与」")
        if pre and pre.get("ok"):
            eng = "可用 · %s" % (pre.get("engine") or "?") if pre.get("engine_ok") \
                else "**不可用** · 缺 %s" % "、".join(pre.get("engine_missing") or ["?"])
            self.log("OCR 引擎：%s" % eng)
            self.log("词表：图片层 %s 条 · v3_keyword 共 %s 条"
                     % (pre.get("ocr_words", 0), pre.get("keyword_total", 0)))
            self.log("本包已读过 %s 张图（有命中 %s 张）—— 读过的图不会重读"
                     % (pre.get("ocr_images", 0), pre.get("hit_images", 0)))
            if not pre.get("engine_ok") and wi:
                self.log("⚠ 引擎不可用，图片层会被后端记成 SKIP（不会拦货，也不会报错当成功）")
        elif pre:
            self.log("⚠ 开跑前自检没跑成：%s" % str(pre.get("error"))[:160])
        self.log("▶ 交给后端：fine_screen(pids=%d, limit=%d, with_images=%s, with_detail=%s,"
                 " with_ai=%s)" % (n_target if picked else n, lim, wi, wd, wa))
        self.log("──────── 逐件日志 ────────")

    def _apply_event(self, ev: dict):
        """后端事件 → 一行日志 + 进度条 + 累计数。键名用别名取，取不到就原样打。"""
        if not isinstance(ev, dict):
            self.log(str(ev)[:400])
            return
        kind = str(ev.get("kind") or ev.get("type") or "").lower()
        if kind == "start":
            n = _as_int(_ev(ev, "n"), 0)
            self.lb_total.setText("本包 %d 件" % (n or self._total_n))
            if ev.get("stream"):
                self.log("✓ 引擎提供逐件事件：每处理完一件就会在这里出一行")
            else:
                self.log("⚠ 引擎这一版没提供逐件事件（progress 回调未落地）——"
                         "本次只能等跑完看总账，进度条按不确定态显示")
                self.pb.setRange(0, 0)
            return
        if kind in ("warn", "warning"):
            self.log("⚠ " + str(_ev(ev, "msg", str(ev)))[:300])
            return
        if kind in ("done", "finish", "end"):
            return                          # 总账在 _done 里统一打，避免重复
        self._acc_line(ev)

    def _acc_line(self, ev: dict):
        i = _ev(ev, "i", "")
        n = _ev(ev, "n", "")
        for k in ("imgs", "dl", "ocr", "cache", "hit"):
            v = _ev(ev, k, None)
            if v is not None:
                try:
                    self._acc[k] = self._acc.get(k, 0) + int(v)
                except Exception:  # noqa: BLE001
                    pass

        parts = []
        if i != "" or n != "":
            parts.append("[%s/%s]" % (i if i != "" else "?", n if n != "" else "?"))
        pid = _ev(ev, "pid", "")
        if pid != "":
            parts.append("pid=%s" % pid)
        for key, label in (("imgs", "图"), ("dl", "下载"), ("ocr", "OCR"),
                           ("cache", "缓存"), ("hit", "命中")):
            v = _ev(ev, key, None)
            if v is not None:
                parts.append("%s %s" % (label, v))
        verdict = str(_ev(ev, "verdict", ""))
        if verdict:
            vmap = {"blocked": "拦住", "block": "拦住", "passed": "通过",
                    "pass": "通过", "ok": "通过"}
            parts.append(vmap.get(verdict.lower(), verdict))
        sec = _ev(ev, "sec", None)
        if sec is not None:
            parts.append("%ss" % sec)
        else:
            ms = _ev(ev, "ms", None)
            if ms is not None:
                try:
                    parts.append("%.1fs" % (float(ms) / 1000.0))
                except Exception:  # noqa: BLE001
                    pass
        title = str(_ev(ev, "title", ""))
        if title:
            parts.append(title[:40])
        msg = str(_ev(ev, "msg", ""))
        if msg and not title:
            parts.append(msg[:180])
        if not parts:
            parts = [str(ev)[:300]]
        self.log(" · ".join(parts))

        # 进度条 / 进度文字
        ii, nn = _as_int(i, -1), _as_int(n, -1)
        if nn > 0:
            self.pb.setRange(0, nn)
            if ii >= 0:
                self.pb.setValue(min(ii, nn))
            self.pb.setFormat("%v/%m 件")
        if ii >= 0 and nn > 0:
            self.lb_prog.setText(
                "已处理 %d/%d · 图 %d / 下载 %d / OCR %d / 缓存 %d / 命中 %d"
                % (ii, nn, self._acc.get("imgs", 0), self._acc.get("dl", 0),
                   self._acc.get("ocr", 0), self._acc.get("cache", 0),
                   self._acc.get("hit", 0)))

    def _done(self, r: dict):
        self.btn_run.setEnabled(True)
        self.btn_release.setEnabled(True)
        if not r.get("ok"):
            self.pb.setRange(0, 100)
            self.pb.setValue(0)
            self.pb.setFormat("失败")
            self.log("✗ 精筛失败：" + str(r.get("error"))[:200])
            self.lb_prog.setText("已停止：%s" % str(r.get("error"))[:80])
            QMessageBox.warning(self, "精筛没跑成", str(r.get("error"))[:300])
            return
        if r.get("stream"):
            self.pb.setRange(0, 100)
            self.pb.setValue(100)
            self.pb.setFormat("完成")
        else:
            self.pb.setRange(0, 100)
            self.pb.setValue(100)
            self.pb.setFormat("完成（无逐件进度）")
        self._render(r)
        code = getattr(self, "_run_code", "") or ""
        if code:
            bridge.mark_fine_done(code, self.lb_stat.text())
            # ★ 2026-11-09 精筛收口：跑完自动落成「只含通过件」的筛选后包。
            #   不删原包、不删商品/图片；4.0 检测打码与出表默认改为读这个包，
            #   于是未通过件不再进后续环节。幂等：已存在同样的包就复用，不重复造。
            try:
                rp = bridge.v3_fine_release_pack(code)
                if rp.get("ok"):
                    new_code = str(rp.get("pack_code") or "")
                    self.lb_saved.setText(
                        "筛选后包 %s（通过 %d 件 / 未通过 %d 件不进后续）"
                        % (new_code, rp.get("count") or 0, rp.get("skipped") or 0))
                    self.lb_saved.setToolTip("第 4 步「检测打码」默认会选 %s" % new_code)
                    self.log("✓ %s（%s）" % (
                        rp.get("message") or "筛选后包已就绪",
                        "复用已有包" if rp.get("reused") else "新建引用式包，原数据未删"))
                else:
                    self.log("⚠ 筛选后包未落成：%s" % str(rp.get("error"))[:160])
            except Exception as e:                # noqa: BLE001
                self.log("⚠ 筛选后包未落成：%s: %s" % (type(e).__name__, str(e)[:120]))
        lp = str(r.get("log_path") or "")
        if lp:
            self._log_path = lp
            self.log("JSONL 日志：%s（「运行」tab 可直接看）" % lp)
            if self.logtabs.currentIndex() == 1:
                self._load_run_tail()
        lines = r.get("lines") or []
        if lines:
            self.log("后端日志 %d 行，尾部 10 行：" % len(lines))
            for x in lines[-10:]:
                self.log("   " + str(x)[:200])
        blocked = int(r.get("blocked") or 0)
        pending = int(r.get("pending") or 0)
        if blocked or pending:
            self.log("▶ 要放行的：表格里勾上「拦住 / 待复核」的行 → 「✅ 放行所选」"
                     "（不想一个个勾就点「全选拦住」「全选待复核」）；"
                     "只想豁免某个词 → 右键那一行的「原因」")
        else:
            self.log("✓ 本包没有拦住 / 待复核的商品，可以继续下一步")
        self.log("✓ 精筛收口已完成：只留通过件的「筛选后包」已就绪（原包与判定记录未删），"
                 "第 4 步「检测打码」与出表默认只读它；也可点「🧹 清掉未通过 → 筛选后包」再收一次")
        self.log("════════ 精筛结束 ════════")
        try:
            self.win.log("精筛完成（%s）：%s" % (code, self.lb_stat.text()))
        except Exception:  # noqa: BLE001
            pass
        self._refresh_counts()

    def _render(self, r: dict):
        st = r.get("stats") or {}
        checked = int(r.get("checked") or 0)
        passed = int(r.get("passed") or 0)
        blocked = int(r.get("blocked") or 0)
        pending = int(r.get("pending") or 0)
        dups = int(r.get("duplicates") or 0)
        self.lb_stat.setText(
            "✓ 检查 %d · 通过 %d · 拦住 %d · 待复核 %d · 重复 %d · %ss%s"
            % (checked, passed, blocked, pending, dups, st.get("seconds", "?"),
               "" if not r.get("remaining") else "（还剩 %d 件没跑）" % r["remaining"]))
        self.log(self.lb_stat.text())
        self.lb_prog.setText("已处理 %d/%d · 通过 %d · 拦住 %d · 待复核 %d · 用时 %ss"
                             % (checked, checked + int(r.get("remaining") or 0),
                                passed, blocked, pending, st.get("seconds", "?")))

        img = r.get("image_layer") or {}
        det = r.get("detail_layer") or {}
        if img.get("skipped"):
            layer = "图片层：未启用 —— " + str(img.get("reason") or "")
        elif img.get("enabled"):
            layer = ("图片层：开（%s，词表 %s 条）｜图 %s 张 · 下载 %s · 本次OCR %s 张 · "
                     "缓存跳过 %s · 读不出 %s 张｜有命中 %s 件（其中标题没写、图上才有的 %s 件）"
                     % (img.get("engine") or "-", img.get("words") or 0,
                        st.get("images_total", 0), st.get("images_downloaded", 0),
                        st.get("images_ocr", 0), st.get("images_cached", 0),
                        st.get("images_failed", 0), st.get("products_image_hit", 0),
                        st.get("products_image_block", 0)))
        else:
            layer = "图片层：关（本次没跑）"
        layer += ("｜详情层：%s" % ("开" if det.get("enabled") else "关"))
        if det.get("enabled"):
            layer += ("（描述命中 %s 件 · 图文品牌不一致 %s 件 · 材质不一致 %s 件 · "
                      "缺描述 %s 件 · 缺详情图 %s 件 · 信息太薄 %s 件）"
                      % (st.get("products_desc_hit", 0), st.get("products_brand_mismatch", 0),
                         st.get("products_material_mismatch", 0),
                         st.get("products_no_desc", 0), st.get("products_no_detail", 0),
                         st.get("products_thin_info", 0)))
        # 摘要只占一行，全文进 tooltip + 日志（主人要的是少占地方）
        self.lb_layer.setText("图 %s 张 / OCR %s｜%s · %s"
                              % (st.get("images_total", 0), st.get("images_ocr", 0),
                                 "图片层开" if img.get("enabled") else "图片层关",
                                 "详情层开" if det.get("enabled") else "详情层关"))
        self.lb_layer.setToolTip(layer)
        self.log(layer)

        # 结构化 reasons（blocked_detail 里带 word/kind/waivable）先入缓存，
        # 右栏的「可豁免词」与「不可豁免」两节都吃它
        for b in (r.get("blocked_detail") or []):
            pid = b.get("product_id")
            if pid is not None and b.get("reasons"):
                try:
                    self._reasons_cache[int(pid)] = list(b["reasons"])
                except Exception:  # noqa: BLE001
                    pass

        rows = r.get("rows") or []
        if not rows:
            rows = [{"product_id": x.get("product_id"),
                     "title": x.get("title"), "verdict": "blocked",
                     "evidence": x.get("evidence") or [],
                     "reasons": x.get("reasons") or [],
                     "why": "；".join(str(y.get("msg") or y.get("code") or "")
                                     for y in (x.get("reasons") or []))}
                    for x in (r.get("blocked_detail") or [])]
        self._render_rows(rows)
        self._show_noise(r.get("noise") or [])

    # ============================================================
    #  表格
    # ============================================================
    #  判定口径（与后端 flow.fine_screen 一致，三档而不是两档）：
    #    blocked  拦住     —— 命中违规证据
    #    pending  待复核   —— 关键证据缺失（无详情图且无描述 / 图片层未执行），
    #                         **既不算通过也不算违规**，不能当「干净」用
    #    passed   通过
    #  ★ 该修的老毛病：`v3_fine_evidence` 只把 verdict 归一成 blocked/passed，
    #    pending 会被显示成「通过」—— 界面把一个「没判的」说成「判过了」。
    #    这里不再信单一字段：verdict 优先，其次看商品真实 status。
    _VERDICT_CN = {"blocked": "刷掉", "pending": "争议件", "passed": "通过"}

    @staticmethod
    def _verdict_of(row: dict) -> str:
        """判定归一：**先看商品真实 status，再看 verdict**。

        倒过来会出错 —— `v3_fine_evidence` 把 verdict 归一成 blocked/passed，
        pending 的行带出来是 `verdict=passed, status=pending`；若先信 verdict，
        「没判的」就会被显示成「判过了」。
        """
        st = str(row.get("status") or "").strip().lower()
        if st == "blocked":
            return "blocked"
        if st in ("pending", "review"):
            return "pending"
        v = str(row.get("verdict") or "").strip().lower()
        if v in ("blocked", "block", "拦", "拦住"):
            return "blocked"
        if v in ("pending", "review", "待复核", "待补充证据"):
            return "pending"
        if v in ("passed", "pass", "ok", "通过", "fine_screened"):
            return "passed"
        return "passed"

    def _words_of_row(self, row: dict) -> list[dict]:
        """一行里所有「可豁免的词」—— 优先结构化 reasons，退化为从证据串里抠。

        证据串的两种形状（flow._evidence_lines）：
            图证据   `主图#2 · 品牌词 · OCR片段…`
            详情证据 `详情 · 违规词 · 说明…`
        所以第 2 段就是词。结构化 reasons 里直接有 word/kind/waivable。
        """
        out, seen = [], set()
        for r in (row.get("reasons") or []):
            if not isinstance(r, dict):
                continue
            w = str(r.get("word") or "").strip()
            if not w or w in seen:
                continue
            seen.add(w)
            out.append({"word": w,
                        "kind": str(r.get("kind") or r.get("layer") or "")[:20],
                        "level": str(r.get("level") or ""),
                        "msg": str(r.get("msg") or "")[:100],
                        "waivable": r.get("waivable") is not False})
        if out:
            return out
        for e in (row.get("evidence") or []):
            parts = [p.strip() for p in str(e).split(" · ")]
            if len(parts) < 2:
                continue
            w = parts[1]
            if not w or w in ("—", "-") or w in seen:
                continue
            seen.add(w)
            out.append({"word": w, "kind": parts[0][:20], "level": "",
                        "msg": "", "waivable": True})
        return out

    def _render_rows(self, rows: list[dict]):
        self._rows = list(rows or [])
        self._row_words = [self._words_of_row(r) for r in self._rows]
        self._rendering = True
        try:
            self.tb.clearSpans()
            if not self._rows:
                self.tb.setRowCount(1)
                self.tb.setItem(0, 0, QTableWidgetItem("（这个包还没有精筛结果）"))
                self.tb.setSpan(0, 0, 1, 4)
            else:
                self.tb.setRowCount(len(self._rows))
                for i, x in enumerate(self._rows):
                    verdict = self._verdict_of(x)
                    blocked = (verdict == "blocked")
                    cb = QTableWidgetItem("")
                    cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    cb.setCheckState(Qt.CheckState.Unchecked)
                    self.tb.setItem(i, 0, cb)

                    t = QTableWidgetItem(str(x.get("title") or "")[:60])
                    t.setData(Qt.ItemDataRole.UserRole, x.get("product_id"))
                    self.tb.setItem(i, 1, t)

                    g = self._VERDICT_CN.get(verdict, "通过")
                    _man = x.get("manual") if isinstance(x.get("manual"), dict) else None
                    if _man and _man.get("verdict"):
                        g += "（人工改判）"
                    it = QTableWidgetItem(g)
                    it.setForeground({"blocked": QColor("#b3261e"),
                                      "pending": QColor("#b06f00")}.get(
                                          verdict, QColor("#1b6e3c")))
                    if verdict == "pending":
                        it.setToolTip("争议件 = 模型自己没把握（confidence<30）或 AI 临时没参与，"
                                      "极少数，需要你看一眼。\n其余已按「通过 / 刷掉」两档判完，不用人工审核。\n"
                                      "勾上 →「✅ 放行所选」可留用；不想要就直接刷掉。")
                    self.tb.setItem(i, 2, it)

                    # ★ 只有 AI 点评这一列（2026-10-27 主人要求）。
                    #   逐条理由、图数/OCR 数不删，全部收进 tooltip 与详情窗 —— 想看还能看，
                    #   但界面上只有一段人话结论。
                    av = str(x.get("ai_verdict") or "")
                    pid = x.get("product_id")
                    ai_txt = str(x.get("ai_comment") or "—")
                    if _man and _man.get("verdict"):
                        ai_txt = "【人工改判为%s】%s" % (
                            {"pass": "通过", "pending": "待复核",
                             "block": "拦住"}.get(_man["verdict"], _man["verdict"]),
                            ai_txt)
                    ai = QTableWidgetItem(ai_txt)
                    ai.setForeground({"block": QColor("#b3261e"),
                                      "pass": QColor("#1b6e3c"),
                                      "warn": QColor("#b06f00")}.get(av, QColor("#666")))
                    ws = self._row_words[i] if i < len(self._row_words) else []
                    tip = ["AI 判词（%s）：%s" % (
                        {"block": "拦住", "pass": "放行", "warn": "待复核"}.get(
                            av, av or "未参与"), x.get("ai_model") or "—"),
                        "图数：%s" % self._count_text(pid),
                        "",
                        "点商品名 → 打开详情窗（看原图 + 逐条理由）"]
                    if ws:
                        tip.append("规则层参考词：%s" % "、".join(
                            w["word"] for w in ws[:10]))
                    ai.setToolTip("\n".join(tip))
                    self.tb.setItem(i, 3, ai)
                self.tb.resizeRowsToContents()
        finally:
            self._rendering = False
        self._update_pick()

    def _count_text(self, pid) -> str:
        c = self._counts.get(str(pid)) if pid is not None else None
        if not c:
            return "—"
        return "%d 张 / OCR %d" % (int(c.get("imgs") or 0), int(c.get("ocr") or 0))

    def _refresh_counts(self):
        """把每个商品的 图数 / 已 OCR 数 填到表格第三列（只读库，不重跑 OCR）。"""
        fn = getattr(bridge, "v3_image_ocr_counts", None)
        code = self._code or ""
        if fn is None or not code:
            return
        pids = [x.get("product_id") for x in self._rows if x.get("product_id")]
        if not pids:
            return
        try:
            r = fn(code, pids=pids)
        except Exception as e:  # noqa: BLE001
            self.log("⚠ 读图数/OCR 数失败：%s" % str(e)[:120])
            return
        if not r.get("ok"):
            return
        self._counts = r.get("counts") or {}
        self._rendering = True
        try:
            # 图数/OCR 数不再单独占一列（2026-10-27 砍到 4 列），改进 AI 点评格的 tooltip，
            # 想看的人照样能看得到 —— 删的是列，不是数据。
            for i, x in enumerate(self._rows):
                pid = x.get("product_id")
                ai = self.tb.item(i, 3)
                if ai is None:
                    continue
                tip = ai.toolTip() or ""
                if "图数：" in tip:
                    head, _, tail = tip.partition("图数：")
                    rest = tail.split("\n", 1)
                    tip = head + "图数：%s" % self._count_text(pid) + (
                        "\n" + rest[1] if len(rest) > 1 else "")
                else:
                    tip = tip + "\n图数：%s" % self._count_text(pid)
                ai.setToolTip(tip)
        finally:
            self._rendering = False

    # ============================================================
    #  右键：豁免「这个词」
    # ============================================================
    def _open_ai_cfg(self):
        """打开右侧设置抽屉并把「AI 模型」那块露出来（不弹新窗口，主人已经在用的布局不动）。"""
        w = getattr(self, "win", None)
        fn = getattr(w, "_open_settings", None) if w is not None else None
        if callable(fn):
            try:
                fn()
            except Exception as e:                    # noqa: BLE001
                self.log("打开设置失败（不影响筛选）：%s" % e)
        sp = getattr(w, "settings_panel", None) if w is not None else None
        if sp is None:
            self.log("右侧设置抽屉不可用 —— AI 模型配置在 config\\ai_providers.json 与 config\\ai_fine.json")
            return
        try:
            sp.head.setChecked(True)
            sp.body.setVisible(True)
            sp._refresh_ai()
            self.log("已打开「设置 → AI 模型」：当前 %s" % sp.lb_ai.text()[:80])
        except Exception as e:                        # noqa: BLE001
            self.log("展开 AI 设置失败：%s" % e)

    def _cell_clicked(self, row: int, col: int):
        """点「商品」列 → 弹独立详情窗（2026-10-27 主人要求：要能点开看详情核对原图）。

        刻意只在第 1 列触发：勾选框、判定、理由这些格子点一下就弹窗会很难用。
        """
        if col != 1:
            return
        try:
            pid = (self._rows[row] or {}).get("product_id")
        except Exception:                            # noqa: BLE001
            return
        if pid is None:
            return
        self._open_detail(pid)

    def _open_detail(self, pid):
        try:
            from .fine_detail import ProductDetailDialog   # noqa: PLC0415
        except Exception as e:                             # noqa: BLE001
            self.log("详情窗打不开：%s: %s" % (type(e).__name__, e))
            return
        dlg = ProductDetailDialog(pid, self)
        dlg.exec()
        try:                                          # 详情窗里可能点了「放行」，回来刷新
            self._load_evidence()
        except Exception as e:                        # noqa: BLE001
            self.log("刷新精筛结果失败（不影响已有数据）：%s" % e)

    def _ctx_menu(self, pos):
        m = self._build_ctx_menu(pos)
        if m is None:
            return
        m.exec(self.tb.viewport().mapToGlobal(pos))

    def _build_ctx_menu(self, pos) -> QMenu | None:
        """按点击位置建右键菜单（单独拆出来，离屏自检里可以直接断言）。"""
        idx = self.tb.indexAt(pos)
        if not idx.isValid():
            return None
        return self._menu_for_row(idx.row())

    def _menu_for_row(self, i: int) -> QMenu | None:
        """给第 i 行建右键菜单 —— 与 `_build_ctx_menu` 同一条逻辑，便于离屏断言。"""
        if i < 0 or i >= len(self._rows):
            return None
        row = self._rows[i]
        pid = row.get("product_id")
        ws = self._menu_words(row, pid)

        m = QMenu(self)
        head = m.addAction("第 %d 行 · pid %s" % (i + 1, pid))
        head.setEnabled(False)
        waivable = [w for w in ws if w.get("waivable", True)]
        for w in waivable[:15]:
            act = m.addAction("🚫 豁免这个词：%s" % w["word"])
            act.triggered.connect(
                lambda _=False, ww=w["word"], pp=pid: self._waive_words([ww], [pp] if pp else None))
        if not waivable:
            a = m.addAction("（这行没有可豁免的词 —— 结构性判定）")
            a.setEnabled(False)
        else:
            a = m.addAction("🚫 豁免这行的全部 %d 个词" % len(waivable))
            a.triggered.connect(
                lambda _=False, wws=list(ww["word"] for ww in waivable), pp=pid:
                self._waive_words(wws, [pp] if pp else None))
        m.addSeparator()
        # ★ 2026-10-27 主人要求：「有些商品他判定是待查还是通过，这个要可以单个单个修改」
        #   + 「我可以点评这个它的结果，他会根据我的话进一步把之前的提示词进行丰富」
        cur = self._verdict_of(row)
        mm = m.addMenu("✎ 判定改为…")
        for lab, v, cur_v in (("✅ 通过（放进下一批）", "pass", "passed"),
                              ("⏸ 待复核（先放着）", "pending", "pending"),
                              ("⛔ 拦住（不往下走）", "block", "blocked")):
            a = mm.addAction(lab + ("　← 现在就是这个" if cur == cur_v else ""))
            a.triggered.connect(lambda _=False, vv=v, pp=pid: self._set_verdict(pp, vv))
        if isinstance(row.get("manual"), dict):
            a = mm.addAction("↩ 撤销人工改判（回到 AI 当时的判定）")
            a.triggered.connect(lambda _=False, pp=pid: self._revert_verdict(pp))
        # ★ 2026-10-27 主人要求：「打勾的列表它的状态判定要可以批量修改，
        #   现在批量修改要么过、要么都不过」—— 这里给四档，作用对象是**勾选的那一批**。
        n_chk = len(self._checked_pids_all())
        mb = m.addMenu("⚖ 批量判定（勾选的 %d 件）" % n_chk)
        mb.setEnabled(n_chk > 0)
        mb.addAction("✅ 全改为通过", lambda _=False: self._batch_verdict("pass"))
        mb.addAction("⏸ 全改为待复核", lambda _=False: self._batch_verdict("pending"))
        mb.addAction("⛔ 全改为拦住", lambda _=False: self._batch_verdict("block"))
        mb.addSeparator()
        mb.addAction("☑ 先把「选中的行」打勾（Shift 多选后用）",
                     lambda _=False: self._check_selected_rows())
        a = m.addAction("✍ 点评这个结果（AI 会记住我的口径）")
        a.triggered.connect(lambda _=False, pp=pid, rr=row: self._comment_verdict(pp, rr))
        m.addSeparator()
        a2 = m.addAction("📋 复制这行的原因")
        a2.triggered.connect(lambda _=False, k=i: self._copy_row_reason(k))
        a3 = m.addAction("查看已豁免 / 还原")
        a3.triggered.connect(self._open_waivers)
        return m

    def _set_verdict(self, pid, v: str):
        """单件改判定（写库，保留 AI 判词，重跑精筛也不会被覆盖）。"""
        if pid is None:
            return
        r = bridge.v3_set_verdict(int(pid), v)
        txt = {"pass": "通过", "block": "拦住", "pending": "待复核"}.get(v, v)
        if r.get("ok"):
            self.log("✎ 人工改判 #%s → %s（AI 判词保留，下次重筛不覆盖）" % (pid, txt))
        else:
            self.log("✎ 改判失败 #%s：%s" % (pid, r.get("error")))
        self._reasons_cache.pop(int(pid), None)
        self._load_evidence()

    def _revert_verdict(self, pid):
        r = bridge.v3_reset_verdict(int(pid))
        self.log("↩ 撤销人工改判 #%s：%s" % (
            pid, "已回到 AI 判定" if r.get("ok") else r.get("error")))
        self._load_evidence()

    def _comment_verdict(self, pid, row: dict):
        """就地点评这次判定 —— 存成「纠错口径」，下次判定直接喂给 AI。"""
        if pid is None:
            return
        prev = row.get("ai_comment") or ""
        dlg = QDialog(self)
        dlg.setWindowTitle("点评这个结果 · #%s" % pid)
        dlg.resize(680, 460)
        lv = QVBoxLayout(dlg)
        lb = QLabel("AI 当时说：\n%s" % (prev or "（这件没有 AI 判词）"))
        lb.setWordWrap(True)
        lb.setStyleSheet("font-size:12px;padding:6px;background:#f6f6f6;border-radius:4px;")
        lv.addWidget(lb)
        lv.addWidget(QLabel("我不同意的地方 / 应该怎么判（写给 AI 看，会累加成口径）："))
        ed = QPlainTextEdit()
        ed.setPlaceholderText("例如：安装不是风险，不要因为要安装就扣分；"
                              "这件是实木大件，符合 45+ 人群，应该通过")
        lv.addWidget(ed, 1)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        b1 = QPushButton("保存点评")
        b1.setObjectName("Primary")
        b2 = QPushButton("取消")
        row2.addWidget(b1)
        row2.addWidget(b2)
        lv.addLayout(row2)
        out = {}

        def _save():
            t = ed.toPlainText().strip()
            if not t:
                return
            out["r"] = bridge.ai_feedback_add(
                int(pid), row.get("title") or "", t,
                row.get("ai_verdict") or "", prev, row.get("verdict") or "")
            dlg.accept()

        b1.clicked.connect(_save)
        b2.clicked.connect(dlg.reject)
        dlg.exec()
        if out.get("r", {}).get("ok"):
            n = out["r"].get("count")
            self.log("✍ 已记下点评（共 %s 条）。到右侧「⚙ 设置 → 判定提示词」点"
                     "「合并进提示词」或「让 AI 提炼」，下次判定就按新口径走。" % n)
            if self.win is not None and hasattr(self.win, "settings_panel"):
                try:
                    self.win.settings_panel._refresh_fb()
                except Exception:                    # noqa: BLE001
                    pass
        elif out.get("r"):
            self.log("✍ 点评没存上：%s" % out["r"].get("error"))

    def _menu_words(self, row: dict, pid) -> list[dict]:
        """右键时先取结构化 reasons（后端有就带 word/waivable 标记），取不到用证据串。"""
        ws = self._words_of_row(row)
        if pid is None:
            return ws
        try:
            key = int(pid)
        except Exception:  # noqa: BLE001
            return ws
        rs = self._reasons_cache.get(key)
        if not rs:
            rs = self._reasons_from_backend(key)
            if rs:
                self._reasons_cache[key] = rs
        if rs:
            better = self._words_of_row({"reasons": rs})
            if better:
                return better
        return ws

    def _reasons_from_backend(self, pid) -> list[dict]:
        """从商品 note 里读结构化精筛理由（只读库，不重跑 OCR）。失败一律空列表。"""
        fn = getattr(bridge, "v3_product", None)
        if fn is None:
            return []
        try:
            r = fn(int(pid))
        except Exception as e:  # noqa: BLE001
            self.log("⚠ 取 pid %s 的判定失败：%s" % (pid, str(e)[:100]))
            return []
        if not isinstance(r, dict) or not r.get("ok"):
            return []
        p = r.get("product") or {}
        note = p.get("note")
        if isinstance(note, str):
            try:
                note = json.loads(note)
            except Exception:  # noqa: BLE001
                note = {}
        if not isinstance(note, dict):
            return []
        rs = note.get("fine_screen") or []
        return [x for x in rs if isinstance(x, dict)]

    def _copy_row_reason(self, i: int):
        if i < 0 or i >= len(self._rows):
            return
        x = self._rows[i]
        txt = "pid %s · %s\n判定：%s\n原因：%s\n证据：\n%s" % (
            x.get("product_id"), x.get("title") or "", x.get("verdict") or "",
            x.get("why") or "", "\n".join(str(e) for e in (x.get("evidence") or [])))
        try:
            from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
            QApplication.clipboard().setText(txt)
            self.log("✓ 已复制这行的原因到剪贴板")
        except Exception as e:  # noqa: BLE001
            self.log("⚠ 复制失败：%s" % str(e)[:100])

    # ============================================================
    #  放行
    # ============================================================
    def _on_item_changed(self, _item):
        if self._rendering:
            return
        self._update_pick()

    # ------------------------------------------------------------
    #  选行 + 打勾（不依赖键盘的 Ctrl+A）
    #  ★ 2026-10-26 逻辑修正：原来这里是两个脱节的概念 ——
    #      选行 = self.tb.selectAll() / clearSelection()  只让行变蓝底
    #      打勾 = item.setCheckState(...)                  才决定「放行」处理谁
    #    用户点「全选」看到行蓝了、勾选框还是空的，点放行就说「一条都没勾」。
    #    现已合并：三个按钮**同时**做选中与打勾，并在提示里分别报数。
    # ------------------------------------------------------------
    def _sel_notice(self, msg: str):
        """选行/勾选类操作的即时反馈：写日志 + 显示在选择条上（不弹模态框）。"""
        self.log(msg)
        self.lb_selstat.setText(msg)

    def _selected_row_count(self) -> int:
        try:
            return len(self.tb.selectionModel().selectedRows())
        except Exception:                # noqa: BLE001  选行模型没了也不能崩
            return 0

    def _set_all_checks(self, state) -> int:
        """把全部行的勾选框设成 state，返回实际改到的行数。

        `_rendering=True` 期间 `_on_item_changed` 直接 return，
        所以批量改 N 行只触发一次 `_update_pick`（由调用方补），不会卡。
        """
        n = len(self._rows)
        if not n:
            return 0
        hit = 0
        self._rendering = True
        try:
            for i in range(n):
                it = self.tb.item(i, 0)
                if it is None:
                    continue
                it.setCheckState(state)
                hit += 1
        finally:
            self._rendering = False
        return hit

    def _select_all_rows(self):
        n = len(self._rows)
        if not n:
            self._sel_notice("没有可选的商品行：这个包还没有精筛结果")
            return
        self.tb.selectAll()
        n_chk = self._set_all_checks(Qt.CheckState.Checked)
        self._update_pick()
        self._sel_notice("✓ 全选：选中 %d 行 · 打勾 %d 件 / 共 %d 行「✅ 放行所选」现在可用"
                         % (self._selected_row_count(), n_chk, n))

    def _select_no_rows(self):
        n_all = len(self._rows)
        if not n_all:
            self._sel_notice("没有可选的商品行：这个包还没有精筛结果")
            return
        had = self._selected_row_count()
        had_chk = len(self._checked_rows())
        self.tb.clearSelection()
        n_chk = self._set_all_checks(Qt.CheckState.Unchecked)
        self._update_pick()
        self._sel_notice("✓ 全不选：取消选中 %d 行 · 清空打勾 %d 件（共 %d 行，%d 个勾选框已置空）"
                         % (had, had_chk, n_all, n_chk))

    def _select_invert(self):
        n = len(self._rows)
        if not n:
            self._sel_notice("没有可选的商品行：这个包还没有精筛结果")
            return
        # ① 行选中反向
        model = self.tb.selectionModel()
        mdl = self.tb.model()
        last = max(0, self.tb.columnCount() - 1)
        chosen = {ix.row() for ix in model.selectedRows()}
        add, sub = QItemSelection(), QItemSelection()
        for i in range(n):
            if i in chosen:
                sub.select(mdl.index(i, 0), mdl.index(i, last))
            else:
                add.select(mdl.index(i, 0), mdl.index(i, last))
        flag = QItemSelectionModel.SelectionFlag
        if not sub.isEmpty():
            model.select(sub, flag.Deselect | flag.Rows)
        if not add.isEmpty():
            model.select(add, flag.Select | flag.Rows)
        # ② 勾选框逐个翻转（与行选中同步）
        before_chk = len(self._checked_rows())
        self._rendering = True
        try:
            for i in range(n):
                it = self.tb.item(i, 0)
                if it is None:
                    continue
                it.setCheckState(Qt.CheckState.Unchecked
                                 if it.checkState() == Qt.CheckState.Checked
                                 else Qt.CheckState.Checked)
        finally:
            self._rendering = False
        # ③ 两路都报出来，不再只报选中
        after_chk = self._update_pick() or len(self._checked_rows())
        self._sel_notice(
            "✓ 反选：选中 %d 行 → %d 行 ｜ 打勾 %d 件 → %d 件（共 %d 行）"
            % (len(chosen), self._selected_row_count(), before_chk, after_chk, n))

    # ------------------------------------------------------------
    #  勾选（checkbox）：按判定批量勾
    # ------------------------------------------------------------
    def _check_by_verdict(self, want: str) -> int:
        """把某个判定的行全部勾上；一条都没有时说清楚，不静默。"""
        cn = self._VERDICT_CN.get(want, want)
        n_all = len(self._rows)
        if not n_all:
            self._sel_notice("没有可勾选的商品行：这个包还没有精筛结果")
            return 0
        hit = 0
        self._rendering = True
        try:
            for i, x in enumerate(self._rows):
                it = self.tb.item(i, 0)
                if not it:
                    continue
                if self._verdict_of(x) != want:
                    continue
                it.setCheckState(Qt.CheckState.Checked)
                hit += 1
        finally:
            self._rendering = False
        self._update_pick()
        if not hit:
            self._sel_notice("没有「%s」的行：一条都没勾（共 %d 行，勾选框保持原样）"
                             % (cn, n_all))
        else:
            self._sel_notice("✓ 全选「%s」：将处理 %d 条 · 已勾选 %d 条（共 %d 行）"
                             % (cn, hit, len(self._checked_rows()), n_all))
        return hit

    def _select_all_blocked(self):
        self._check_by_verdict("blocked")

    def _select_all_passed(self):
        self._check_by_verdict("passed")

    def _checked_pids_all(self) -> list[int]:
        """被勾选的全部商品 id —— **不看判定**（批量改判定要用它，不能用只收拦/待的版本）。"""
        out = []
        for x in self._checked_rows():
            pid = x.get("product_id")
            if pid is None or str(pid).strip() == "":
                continue
            try:
                out.append(int(pid))
            except Exception:            # noqa: BLE001
                continue
        return out

    def _check_selected_rows(self) -> int:
        """把「按住 Shift / Ctrl 选中的行」打上勾（2026-10-27 主人点名要的功能）。

        ★ 为什么必须有它：表格里有两套独立状态 —— 行选中（蓝底，管复制/右键）
          和勾选框（管筛选/放行/改判）。Shift 多选只动前者，所以人勾了 8 行，
          实际操作面是 0 条。这个入口把前者直接变成后者。
        """
        sm = self.tb.selectionModel()
        rows = sorted(i.row() for i in (sm.selectedRows() if sm else []))
        if not rows:
            # 有些平台 selectedRows 只报第一列，兜底用 selection 区间
            rows = sorted({i.row() for i in (sm.selection().indexes() if sm else [])})
        if not rows:
            self._sel_notice("先用鼠标拖选 / 按住 Shift 点几行，再点这个")
            return 0
        hit = 0
        self._rendering = True
        try:
            for r in rows:
                it = self.tb.item(r, 0)
                if it is None:
                    continue
                it.setCheckState(Qt.CheckState.Checked)
                hit += 1
        finally:
            self._rendering = False
        self._update_pick()
        self._sel_notice("☑ 已给选中的 %d 行打勾（第 %d–%d 行）· 共勾选 %d 件"
                         % (hit, rows[0] + 1, rows[-1] + 1, len(self._checked_rows())))
        return hit

    def _batch_verdict(self, v: str):
        """批量人工改判（勾选的那一批，一次改完）。"""
        pids = self._checked_pids_all()
        cn = {"pass": "通过", "block": "拦住", "pending": "待复核"}.get(v, v)
        if not pids:
            self._sel_notice("没有勾选任何商品 —— 先打勾，或用「选 ▾」批量勾")
            return 0
        ok = QMessageBox.question(
            self, "批量改判定",
            "把勾选的 %d 件全部改成「%s」？\n\n"
            "· 会记成「人工改判」，AI 判词照样保留\n"
            "· 以后重跑精筛**不会覆盖**这个判定\n"
            "· 想撤回：右键 →「↩ 撤销人工改判」" % (len(pids), cn),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ok != QMessageBox.StandardButton.Yes:
            return 0
        fn = getattr(bridge, "v3_set_verdict_many", None)
        if fn is None:
            r = {"ok": False, "error": "bridge 没有 v3_set_verdict_many"}
        else:
            try:
                r = fn(pids, v)
            except Exception as e:       # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if r.get("ok"):
            self.log("✎ 批量改判 %d 件 → %s（失败 %d 件）"
                     % (r.get("changed") or 0, cn, r.get("failed_n") or 0))
            self._sel_notice("✓ 已批量改为「%s」：%d 件" % (cn, r.get("changed") or 0))
        else:
            self.log("✎ 批量改判失败：%s" % r.get("error"))
            self._sel_notice("批量改判失败：%s" % r.get("error"))
        for pid in pids:
            self._reasons_cache.pop(int(pid), None)
        self._load_evidence()
        return int(r.get("changed") or 0)

    def _select_all_pending(self):
        self._check_by_verdict("pending")

    def _checked_rows(self) -> list[dict]:
        """被勾选的行（不分判定）。"""
        out = []
        for i, x in enumerate(self._rows):
            it = self.tb.item(i, 0)
            if it is None or it.checkState() != Qt.CheckState.Checked:
                continue
            out.append(x)
        return out

    def _checked_pids(self, kinds=("blocked", "pending")) -> list[int]:
        """被勾选、且「放行」该处理的商品 id。

        ★ 2026-10-26 口径修正：原来只收 blocked —— 于是「待复核」的行**永远放不出去**，
          新加的「全选待复核」就是个摆设。待复核（pending）本来就是「没法定性、等人判」，
          人来点放行 = 判它可以继续流转，所以一并收。
        ★ 但「通过（passed）」一格都不收：它的 status 是 fine_screened，改回 imported
          会把「已精筛」标记抹掉，第 4 步的门禁就认不出来了。
        """
        out = []
        for x in self._checked_rows():
            if self._verdict_of(x) not in kinds:
                continue
            pid = x.get("product_id")
            if pid is None or str(pid).strip() == "":
                continue
            try:
                out.append(int(pid))
            except Exception:            # noqa: BLE001
                continue
        return out

    def _update_pick(self) -> int:
        """刷新「已勾选 / 可放行 / 已选中」三个数，返回打勾件数。

        ★ 2026-10-26：原来只报「已勾选 N 件」—— 用户点「全选」把行选中了，
          这里仍显示 0，看起来像按钮没生效。现在把「已选中 N 行」也报出来，
          两个数**同时可见**，能一眼看出「选中」和「打勾」是两件事。
        """
        n_chk = len(self._checked_rows())
        n_ok = len(self._checked_pids())
        n_sel = self._selected_row_count()
        skip = n_chk - n_ok
        parts = ["已勾选 %d 件" % n_chk]
        if n_ok:
            parts.append("可放行 %d 件" % n_ok)
        if skip:
            parts.append("跳过「通过」%d 件" % skip)
        if n_sel:
            parts.append("已选中 %d 行" % n_sel)
        self.lb_pick.setText(" · ".join(parts))
        # ★ 按钮**不因为「一件没勾」而变灰**（2026-10-26）：灰掉等于「点了没反应」，
        #   用户不知道是坏了还是没选。保持可点，由 `_release` 说清「没有选中任何行」。
        #   只有写库那一轮进行中才禁用，防重复提交。
        self.btn_release.setEnabled(
            not (self.releaser and self.releaser.isRunning()))
        # 「▶ 筛选勾选 N 件」跟着勾选数实时变（0 件时灰掉，避免点了没反应）
        try:
            busy = bool(self.worker and self.worker.isRunning())
            self.btn_run_pick.setText("▶ 筛选勾选 %d 件" % n_chk if n_chk
                                      else "▶ 筛选勾选 0 件")
            self.btn_run_pick.setEnabled(bool(n_chk) and not busy)
        except Exception:                            # noqa: BLE001
            pass
        return n_chk

    # ---------------- 批量勾选（按范围，不按判定） ----------------
    def _checkbox_of(self, i: int):
        return self.tb.item(i, 0)

    def _set_rows_checked(self, rows: list[int], state=Qt.CheckState.Checked) -> int:
        hit = 0
        self._rendering = True
        try:
            for i in rows:
                it = self._checkbox_of(i)
                if it is None:
                    continue
                it.setCheckState(state)
                hit += 1
        finally:
            self._rendering = False
        self._update_pick()
        return hit

    def _check_first(self, n: int) -> int:
        total = len(self._rows)
        if not total:
            self._sel_notice("这个包还没有精筛结果，先点「▶ 筛选全部」")
            return 0
        k = min(int(n), total)
        hit = self._set_rows_checked(list(range(k)))
        self._sel_notice("☑ 已勾选前 %d 件（表格自上而下 / 共 %d 行）" % (hit, total))
        return hit

    def _check_visible(self) -> int:
        """勾选「当前看得见的那几行」—— 滚动到哪就选哪，符合「这批先跑」的直觉。"""
        r0 = self.tb.rowAt(0)
        r1 = self.tb.rowAt(max(0, self.tb.viewport().height() - 2))
        if r0 < 0:
            r0 = 0
        if r1 < 0:
            r1 = min(len(self._rows) - 1, r0 + 9)
        rows = list(range(r0, r1 + 1))
        hit = self._set_rows_checked(rows)
        self._sel_notice("☑ 已勾选当前可见的 %d 行（第 %d–%d 行）" % (hit, r0 + 1, r1 + 1))
        return hit

    def _checked_any_pids(self) -> list[int]:
        """所有打了勾的 product_id（**不分判定**）—— 「筛选勾选」用它。"""
        out = []
        for x in self._checked_rows():
            pid = x.get("product_id")
            if pid is None or str(pid).strip() == "":
                continue
            try:
                out.append(int(pid))
            except Exception:                        # noqa: BLE001
                continue
        return out

    def _run_picked(self):
        pids = self._checked_any_pids()
        if not pids:
            self._sel_notice("没有勾选任何商品 —— 先勾选，或用「选择 ▾ → 前 20 件」")
            return
        ok = QMessageBox.question(
            self, "只筛勾选的 %d 件" % len(pids),
            "将只对勾选的 %d 件跑精筛（其余不动）。\n\n"
            "顺序＝表格自上而下；勾选数量已覆盖「上限」设置，上限不再生效。\n\n继续？"
            % len(pids))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._run(pids=pids)

    def _clear_checks(self):
        n = len(self._checked_rows())
        self._rendering = True
        try:
            for i in range(len(self._rows)):
                it = self.tb.item(i, 0)
                if it:
                    it.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._rendering = False
        self._update_pick()
        self._sel_notice("✓ 清空勾选：取消 %d 件（表格与后续步骤都没动）" % n)

    def _release(self):
        if self.releaser and self.releaser.isRunning():
            return
        checked = self._checked_rows()
        pids = self._checked_pids()
        if not checked:
            msg = ("表格里一条都没勾。\n\n点上面的「全选拦住」「全选待复核」，"
                   "或手动勾选要放行的商品，再点这个按钮。")
            self.log("✗ 没有选中任何行：先说清楚，这次什么都没执行")
            QMessageBox.information(self, "没有选中任何行", msg)
            return
        if not pids:
            msg = ("勾了 %d 件，但它们都是「通过」—— 放行只对「拦住」和「待复核」生效。\n\n"
                   "「通过」的商品已经是可继续流转状态，再放行会把「已精筛」标记抹掉，"
                   "所以这里一件都不动。" % len(checked))
            self.log("⚠ " + msg.replace("\n\n", " ").replace("\n", " "))
            QMessageBox.information(self, "这些不用放行", msg)
            return
        ok = QMessageBox.question(
            self, "确认放行",
            "将处理 %d 条：把这 %d 件从「拦住 / 待复核」改成「可继续流转」？\n\n%s"
            % (len(pids), len(pids),
               "、".join(str(p) for p in pids[:15])
               + ("…" if len(pids) > 15 else ""))
            + "\n\n放行后状态回到 imported，第 4/5 步可以继续用它。\n"
              "（只是改状态，不删数据、不改判定记录，随时能重筛回来）")
        if ok != QMessageBox.StandardButton.Yes:
            self.log("已取消：这次什么都没改")
            return
        self.btn_release.setEnabled(False)
        self.log("▶ 将处理 %d 条：放行 %s%s"
                 % (len(pids), "、".join(str(p) for p in pids[:15]),
                    "…" if len(pids) > 15 else ""))
        self.releaser = ReleaseWorker(pids)
        self.releaser.done.connect(self._released)
        self.releaser.start()

    def _released(self, r: dict):
        if not r.get("ok"):
            self.log("✗ 已处理 0 条：放行失败 " + str(r.get("error"))[:200])
            QMessageBox.warning(self, "放行没做成", str(r.get("error"))[:300])
            self._update_pick()
            return
        done = r.get("released") or []
        fail = r.get("failed") or []
        self.log("✓ 已处理 %d 条：成功 %d · 失败 %d%s"
                 % (len(done) + len(fail), len(done), len(fail),
                    ("（" + "、".join(str(x) for x in done[:20]) + "）") if done else ""))
        for f in fail[:10]:
            self.log("⚠ 没放成：%s —— %s" % (f.get("product_id"), str(f.get("error"))[:120]))
        self.refresh()
        self.log("表格已刷新（放行过的行会变成「通过」或从拦住里消失）")

    # ============================================================
    #  保存为新数据包（交给第 4 步）
    # ============================================================
    def _save_as_pack(self):
        """3.0 收口按钮：清掉未通过（落成筛选后包，不删原数据）。

        优先走幂等的 v3_fine_release_pack（只收 fine_screened，pending 不算通过、
        已有同样的包就复用）；老版本 bridge 没有它时回退原来的 v3_save_screen。
        """
        code = self._code or ""
        if not code:
            QMessageBox.information(self, "先选数据包",
                                    "先在左上角选一个数据包，筛完再点这里。")
            return
        n_pass = sum(1 for x in self._rows if (x.get("verdict") or "") == "passed")
        fn = getattr(bridge, "v3_fine_release_pack", None)
        if fn is not None:
            # 新口径：只收 fine_screened 通过件，幂等复用，不弹命名框
            self.btn_save.setEnabled(False)
            self.log("▶ 收口：只留通过件（表格里判通过 %d 件），未通过不进后续…" % n_pass)
            try:
                r = fn(code)
            except Exception as e:                # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            finally:
                self.btn_save.setEnabled(True)
        else:
            # 老版本 bridge 回退：仍走原来的「存为数据包」
            default = "本包_通过_%d件_%s" % (n_pass, datetime.now().strftime("%m%d-%H%M"))
            name, ok = QInputDialog.getText(
                self, "保存筛选结果为数据包",
                "新数据包名称（第 4 步的包下拉里会多出这一个）：", text=default)
            if not ok:
                return
            old = getattr(bridge, "v3_save_screen", None)
            if old is None:
                QMessageBox.warning(self, "保存不了", "bridge 里没有存包函数，这次什么都没改")
                return
            self.btn_save.setEnabled(False)
            self.log("▶ 保存为数据包：%s（只存「通过」的 %d 件）…" % (name, n_pass))
            try:
                r = old(code, (name or "").strip() or default, only="keep")
            except Exception as e:                # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            finally:
                self.btn_save.setEnabled(True)
        if not isinstance(r, dict) or not r.get("ok"):
            err = str((r or {}).get("error") or "返回异常")[:300]
            self.log("✗ 收口没做成：" + err)
            QMessageBox.warning(self, "收口没做成", err)
            return
        new_code = str(r.get("pack_code") or "")
        cnt = r.get("count")
        self.lb_saved.setText(
            "筛选后包 %s（通过 %s 件 / 未通过 %s 件不进后续）"
            % (new_code, cnt, r.get("skipped") if r.get("skipped") is not None else "—"))
        self.log("✓ %s。第 4 步「检测打码」与出表默认会选它。"
                 % (r.get("message") or ("已存为数据包 %s（%s 件）" % (new_code, cnt))))
        self.lb_saved.setToolTip("第 4 步「检测打码」默认会选 %s（原数据未删）" % new_code)
        try:
            self._load_packs()
        except Exception as e:  # noqa: BLE001
            self.log("⚠ 刷新包列表失败（不影响已保存的包）：%s" % str(e)[:100])

    # ============================================================
    #  豁免 / 噪声词（右栏）
    # ============================================================
    def _show_noise(self, noise: list[dict]):
        self._noise = list(noise or [])
        self._fill_waiver_panel()

    def _fill_waiver_panel(self):
        """右栏候选池：把本包**所有命中词**（含 block 级）列出来，不只后端给的 warn 噪声。

        根因（主人 2026-10-22 投诉「拦我的词在豁免区找不到」）：后端 `noise` 只收
        `level=warn 且 waivable` 的词，**block 级命中词一个都不进表**；而且刷新页面时
        这张表根本不会被重填。所以这里改成从「每行证据里的词 + 结构化 reasons + noise」
        三路合并去重，任何出现在原因里的词都能在这里找到。
        """
        agg: dict[str, dict] = {}
        for row in self._rows:
            for w in self._words_of_row(row):
                k = w["word"]
                d = agg.setdefault(k, {"word": k, "count": 0,
                                       "kind": w.get("kind") or "", "waivable": True})
                d["count"] += 1
                if w.get("waivable") is False:
                    d["waivable"] = False
        for it in (self._noise or []):
            k = str(it.get("word") or "").strip()
            if not k:
                continue
            d = agg.setdefault(k, {"word": k, "count": 0,
                                   "kind": str(it.get("kind") or it.get("source") or ""),
                                   "waivable": True})
            d["count"] = max(int(d.get("count") or 0), int(it.get("count") or 0))
            if not d.get("kind"):
                d["kind"] = str(it.get("kind") or it.get("source") or "")
        self._waive_map = agg

        ws = sorted([v for v in agg.values() if v.get("waivable", True)],
                    key=lambda d: (-int(d.get("count") or 0), d["word"]))
        self.tb_noise.setRowCount(len(ws))
        for i, d in enumerate(ws):
            cb = QTableWidgetItem("")
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb.setCheckState(Qt.CheckState.Unchecked)
            self.tb_noise.setItem(i, 0, cb)
            w = QTableWidgetItem(d["word"])
            w.setToolTip("来源 / 类型：%s" % (d.get("kind") or "—"))
            self.tb_noise.setItem(i, 1, w)
            self.tb_noise.setItem(i, 2, QTableWidgetItem(str(d.get("count") or 0)))
        if ws:
            self.lb_noise.setText(
                "本包命中 %d 个词（含「拦住」的）。勾上确实不是违规的词 → 点豁免；"
                "或者直接在表格上右键那一行。" % len(ws))
        else:
            self.lb_noise.setText("本包还没有命中词；跑一次精筛后这里会列出来")
        # 头部按钮上的条数同步
        self.btn_waive_head.setText(
            "豁免 %d 条 %s" % (len(ws), "▾" if self._waiver_open else "▸"))
        self._fill_struct()

    def _fill_struct(self):
        """右栏下半节：没有词的判定（人群/品类/价位/信息）—— 写明为什么豁免不了。"""
        self.lst_struct.clear()
        seen = set()
        for row in self._rows:
            pid = row.get("product_id")
            rs = self._reasons_cache.get(int(pid)) if pid is not None else None
            if rs:
                for r in rs:
                    if not isinstance(r, dict):
                        continue
                    if r.get("waivable") is True and r.get("word"):
                        continue
                    txt = "%s · %s · %s" % (
                        pid, r.get("layer") or r.get("code") or "判定",
                        str(r.get("msg") or r.get("code") or "")[:90])
                    if txt in seen:
                        continue
                    seen.add(txt)
                    if self.lst_struct.count() < 80:
                        self.lst_struct.addItem(txt)
            elif (row.get("verdict") or "") == "blocked" and not self._words_of_row(row):
                txt = "%s · %s" % (pid, str(row.get("why") or "")[:100])
                if txt in seen:
                    continue
                seen.add(txt)
                if self.lst_struct.count() < 80:
                    self.lst_struct.addItem(txt)
        if not self.lst_struct.count():
            self.lst_struct.addItem("（没有结构性判定；想看清每一条就点上面「🔎 取结构性原因」）")

    def _fetch_struct_reasons(self):
        """逐条读被拦住商品的结构化 reasons（用户点按钮才跑，不占刷新路径）。"""
        blocked = [x for x in self._rows if (x.get("verdict") or "") == "blocked"]
        if not blocked:
            self.log("本包没有拦住的行，不需要取结构化原因")
            return
        todo = []
        for x in blocked:
            pid = x.get("product_id")
            try:
                key = int(pid)
            except Exception:  # noqa: BLE001
                continue
            if key not in self._reasons_cache:
                todo.append(key)
        todo = todo[:80]
        if not todo:
            self.log("✓ 结构化原因已经全在缓存里了（右栏下半节就是）")
            self._fill_struct()
            return
        self.log("▶ 取 %d 行的结构化原因（只读库，不重跑 OCR）…" % len(todo))
        got = 0
        for pid in todo:
            rs = self._reasons_from_backend(pid)
            if rs:
                self._reasons_cache[pid] = rs
                got += 1
        self.log("✓ 取到 %d 行的结构化原因" % got)
        self._fill_waiver_panel()

    def _checked_words(self) -> list[str]:
        out = []
        for i in range(self.tb_noise.rowCount()):
            it = self.tb_noise.item(i, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                w = self.tb_noise.item(i, 1)
                if w and w.text():
                    out.append(w.text())
        return out

    def _check_all_words(self):
        """候选词池全选 —— 与「全选拦住」同一套口径：一条都没有就说清楚。"""
        n = self.tb_noise.rowCount()
        if not n:
            self.log("✗ 没有选中任何词：候选词池是空的（先跑一次精筛）")
            self.lb_noise.setText("候选词池是空的 —— 先点「▶ 筛选全部」跑一轮")
            return
        self._rendering = True
        try:
            for i in range(n):
                it = self.tb_noise.item(i, 0)
                if it:
                    it.setCheckState(Qt.CheckState.Checked)
        finally:
            self._rendering = False
        self.log("✓ 已勾选全部 %d 个词：将处理 %d 条（点「🚫 豁免勾选的词」提交）"
                 % (n, n))

    def _waive_selected(self):
        words = self._checked_words()
        if not words:
            msg = ("右边候选词池里一条都没勾。\n\n"
                   "勾上确实不是违规词的词（或点「全选词」），再点这个按钮。")
            self.log("✗ 没有选中任何词：先说清楚，这次什么都没执行")
            QMessageBox.information(self, "没有选中任何词", msg)
            return
        self.log("▶ 将处理 %d 条：豁免 %s" % (len(words), "、".join(words[:20])))
        self._waive_words(words, self._pids or None)

    def _waive_words(self, words: list[str], pids: list | None = None):
        """提交豁免（表格右键与右栏按钮共用这一条路径）。失败只记日志，不崩。"""
        if self.waiver and self.waiver.isRunning():
            self.log("⚠ 上一次豁免还在跑，等它完再点")
            return
        words = [w for w in dict.fromkeys([str(x).strip() for x in (words or [])]) if w]
        if not words:
            return
        ok = QMessageBox.question(
            self, "确认豁免",
            "把下面这些词从违规词库里停用？\n\n%s\n\n"
            "停用后：精筛（含图片层 OCR）不再因它们拦货、第 4 步打码也不会再涂它们。\n"
            "判错了可以在「查看已豁免 / 还原」里一键恢复。\n"
            "原始词库文件不会被修改。" % "、".join(words[:20]))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.btn_waive.setEnabled(False)
        self.pb.setVisible(True)
        self.pb.setRange(0, 0)
        self.log("提交豁免：%s（并重筛本包）…" % "、".join(words[:20]))
        self.waiver = WaiveWorker(words, pids if pids is not None else (self._pids or []),
                                  "人工确认：这个词不是违规词（第 3 步界面豁免）")
        self.waiver.done.connect(self._waived)
        self.waiver.start()

    def _waived(self, r: dict):
        self.btn_waive.setEnabled(True)
        self.pb.setRange(0, 100)
        if not isinstance(r, dict) or not r.get("ok"):
            err = str((r or {}).get("error") or "返回异常")[:200]
            self.log("✗ 已处理 0 条：豁免失败 " + err)
            QMessageBox.warning(self, "豁免没做成", err)
            return
        done = r.get("waived") or []
        miss = r.get("not_in_dict") or []
        self.log("✓ 已处理 %d 条：豁免成功 %d · 词库里没有 %d"
                 % (len(done) + len(miss), len(done), len(miss)))
        self.log("  已豁免：%s" % ("、".join(done) if done else "（无）"))
        if miss:
            self.log("⚠ 这些词不在词库里，没动：%s" % "、".join(miss))
        re_r = r.get("rescreen") or {}
        if isinstance(re_r, dict) and re_r.get("ok"):
            self._render(re_r)
            self.log("已按新词库重筛：通过 %s · 拦住 %s"
                     % (re_r.get("passed"), re_r.get("blocked")))
            self._refresh_counts()
        code = self._code or ""
        if code:
            bridge.mark_fine_done(code, self.lb_stat.text())

    def _open_waivers(self):
        dlg = WaiverListDialog(self)
        dlg.exec()
        if dlg.restored:
            self.log("已还原：%s —— 建议点一次「筛选全部」让判定跟上" % "、".join(dlg.restored))
