# -*- coding: utf-8 -*-
"""下载任务中心 —— 独立窗口：任务头信息 + 每文件进度表 + 事件日志。

为什么必须是独立窗口（任务书 §二.1）：
  「只在主窗口底部打几行字」看不到任何一张图的进度，也无法暂停。
  这里按「一条媒体文件一行」展示：商品 / 媒体类型 / 文件 / 状态 / 进度 / 大小 /
  速度 / 重试 / 结果，并且**关掉窗口不等于取消下载**（下载在后台线程继续跑）。

界面只读数据库快照（600ms 一次），不参与下载本身 —— 所以下载再重界面也不卡。
暂停 / 继续 / 停止 三个按钮走 bridge → server\\v3\\dlcenter 的真实信号，
**不是只改文字**：暂停会中断在传的传输并保留 .part 断点。
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
                             QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QTableView,
                             QTextEdit, QVBoxLayout, QWidget)

try:
    from .. import theme as _T
    ACCENT = getattr(_T, "PRIMARY", "#7C3AED")
    ACCENT_L = getattr(_T, "PRIMARY_LIGHT", "#F5F0FF")
    OK_C = getattr(_T, "SUCCESS", "#16A34A")
    BAD_C = getattr(_T, "DANGER", "#DC2626")
    WARN_C = getattr(_T, "WARNING", "#EA580C")
    TXT = getattr(_T, "TEXT", "#1E1E3C")
    TXT_SUB = getattr(_T, "TEXT_SUB", "#787896")
    LINE = getattr(_T, "BORDER", "#D2D2E1")
except Exception:                        # noqa: BLE001
    ACCENT, ACCENT_L = "#7C3AED", "#F5F0FF"
    OK_C, BAD_C, WARN_C = "#16A34A", "#DC2626", "#EA580C"
    TXT, TXT_SUB, LINE = "#1E1E3C", "#787896", "#D2D2E1"

STATE_CN = {
    "waiting": "等待", "downloading": "下载中", "verifying": "校验中",
    "done": "成功", "failed": "失败", "paused": "暂停",
    "cancelled": "取消", "retry_wait": "等待重试", "skipped": "跳过",
    "queued": "已建任务", "running": "运行中", "stopping": "停止中",
    "stopped": "已停止", "paused_job": "已暂停",
}
STATE_COLOR = {
    "done": OK_C, "downloading": ACCENT, "verifying": ACCENT, "failed": BAD_C,
    "cancelled": TXT_SUB, "paused": WARN_C, "retry_wait": WARN_C, "waiting": TXT_SUB,
}
COLS = ["商品", "媒体类型", "文件", "状态", "进度", "大小", "速度", "重试", "结果"]


def _mb(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n >= 1048576:
        return "%.2f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%.0f B" % n if n else "—"


def _spd(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1048576:
        return "%.2f MB/s" % (n / 1048576.0)
    return "%.0f KB/s" % (n / 1024.0)


class TaskModel(QAbstractTableModel):
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = rows or []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows or []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, sec, orient, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return COLS[sec]
        return None

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if c == 0:
                return "%s" % r.get("product_id")
            if c == 1:
                return "%s ×%s" % (r.get("role_cn") or r.get("role"),
                                   r.get("refs") or 1)
            if c == 2:
                p = r.get("path") or r.get("url") or ""
                return os.path.basename(p)[:46] if r.get("path") else \
                    ("…" + p[-46:] if p else "—")
            if c == 3:
                return STATE_CN.get(r.get("state"), r.get("state") or "—")
            if c == 4:
                if r.get("pct") is None:
                    return "—" if not r.get("bytes") else _mb(r["bytes"])
                return "%.1f%%" % r["pct"]
            if c == 5:
                got = _mb(r.get("bytes"))
                tot = _mb(r.get("bytes_total")) if r.get("bytes_total") else "总大小未知"
                return "%s / %s" % (got, tot)
            if c == 6:
                return _spd(r.get("speed_bps"))
            if c == 7:
                return "%s/%s" % (r.get("tries") or 0, r.get("max_tries") or 3)
            if c == 8:
                t = r.get("error") or r.get("path") or ""
                if r.get("role") == "video" and not r.get("error"):
                    bits = []
                    if r.get("elapsed_sec"):
                        bits.append("已用 %ds" % r["elapsed_sec"])
                    if r.get("task_eta_sec"):
                        bits.append("剩余 %ds" % r["task_eta_sec"])
                    if r.get("resumable"):
                        bits.append("断点续传")
                    t = ("　".join(bits) + ("　" + t if t else "")) if bits else t
                if r.get("ocr_state"):
                    t = (t + "　｜OCR " + ("成功" if r["ocr_state"] == "ok" else "失败")
                         + (("%d 段" % r["ocr_seg"]) if r.get("ocr_seg") else ""))
                return t[:160]
        if role == Qt.ItemDataRole.ForegroundRole and c in (3, 8):
            col = STATE_COLOR.get(r.get("state"))
            if col and c == 3:
                return QColor(col)
            if r.get("error"):
                return QColor(BAD_C)
        if role == Qt.ItemDataRole.ToolTipRole:
            return "URL: %s\n本地: %s\n失败: %s\nOCR: %s" % (
                r.get("url"), r.get("path") or "（未落盘）", r.get("error") or "无",
                r.get("ocr_error") or r.get("ocr_state") or "未跑")
        return None


class DlCenterWindow(QWidget):
    """独立下载任务窗口。`job_id=0` 时自动取最近一个任务。"""

    def __init__(self, job_id: int = 0, parent=None):
        # ★ P0 修复（2026-10-25）：原来 `super().__init__(parent)` 传了主窗口却没带
        #   Window 标志 —— PyQt6 里这会让它变成**子控件**，实测 isWindow()=False、
        #   geometry=(0,0,1120,780)，正好贴在父窗口左上角盖住商品库，而且子控件
        #   close() 无效，用户只能重启软件。这里显式声明为独立顶层窗口。
        super().__init__(parent, Qt.WindowType.Window)
        self.job_id = int(job_id or 0)
        self.snap: dict = {}
        self._last_refresh = 0.0
        self.setWindowTitle("下载任务中心 · 2.5 商品库")
        self.resize(1120, 780)
        # 不置顶：否则又变成"盖住商品库"
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(600)
        QTimer.singleShot(50, self.refresh)

    # ---------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 顶栏：任务标识 + 三个真按钮
        top = QHBoxLayout(); top.setSpacing(8)
        self.lb_title = QLabel("下载任务")
        f = QFont(); f.setPointSize(11); f.setBold(True); f.setBold(True)
        self.lb_title.setFont(f)
        self.lb_title.setStyleSheet("font-weight:700; color:%s;" % TXT)
        top.addWidget(self.lb_title)
        self.lb_state = QLabel("—")
        self.lb_state.setStyleSheet("color:%s; font-weight:600;" % WARN_C)
        top.addWidget(self.lb_state)
        top.addStretch(1)
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setToolTip("停派发新任务 + 中断在传文件（保留断点），不会把半截文件标成功")
        self.btn_resume = QPushButton("▶ 继续")
        self.btn_resume.setToolTip("恢复等待中的任务；有 .part 的用 HTTP Range 从断点续传")
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setToolTip("取消未开始的任务；在传文件安全关闭；已完成的文件保留")
        self.btn_min = QPushButton("— 收起")
        self.btn_min.setToolTip("最小化到任务栏，下载继续跑；再点「下载任务/日志」就回来")
        self.btn_close = QPushButton("✕ 关闭")
        self.btn_close.setToolTip("只关窗口，不停止下载")
        for b in (self.btn_pause, self.btn_resume, self.btn_stop):
            b.setMinimumWidth(88); top.addWidget(b)
        self.btn_min.setMinimumWidth(76); top.addWidget(self.btn_min)
        self.btn_close.setMinimumWidth(76); top.addWidget(self.btn_close)
        self.btn_pause.clicked.connect(lambda: self._ctl("pause"))
        self.btn_resume.clicked.connect(lambda: self._ctl("resume"))
        self.btn_stop.clicked.connect(lambda: self._ctl("stop"))
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_close.clicked.connect(self.close)
        root.addLayout(top)

        # 头部统计
        box = QFrame(); box.setStyleSheet(
            "QFrame{background:%s; border:1px solid %s; border-radius:8px;}" % (ACCENT_L, LINE))
        g = QGridLayout(box); g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(18); g.setVerticalSpacing(4)
        self.kv = {}
        keys = [("job", "任务编号"), ("pack", "当前包"), ("products", "商品数"),
                ("refs", "媒体引用"), ("unique", "去重后资源"), ("roles", "主图/详情/SKU/视频"),
                ("counts", "完成/在传/等待/失败"), ("bytes", "已下载 / 已知总量"),
                ("speed", "当前速度"), ("eta", "预计剩余")]
        for i, (k, label) in enumerate(keys):
            cell = QVBoxLayout(); cell.setSpacing(0)
            cap = QLabel(label); cap.setStyleSheet("color:%s; font-size:11px;" % TXT_SUB)
            val = QLabel("—"); val.setStyleSheet("color:%s; font-size:13px; font-weight:600;" % TXT)
            cell.addWidget(cap); cell.addWidget(val)
            g.addLayout(cell, i // 5, i % 5)
            self.kv[k] = val
        root.addWidget(box)

        # 总进度
        pbar = QHBoxLayout(); pbar.setSpacing(8)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        self.bar.setStyleSheet(
            "QProgressBar{background:#EFEAFB;border:none;border-radius:7px;}"
            "QProgressBar::chunk{background:%s;border-radius:7px;}" % ACCENT)
        pbar.addWidget(self.bar, 1)
        self.lb_pct = QLabel("0%"); self.lb_pct.setStyleSheet("color:%s; font-weight:600;" % ACCENT)
        pbar.addWidget(self.lb_pct)
        root.addLayout(pbar)

        # 视频监控条 —— 主人点名：日志系统首先要能看到「视频下到哪了」
        vbox = QFrame()
        vbox.setStyleSheet("QFrame{background:#FFF7ED;border:1px solid #FED7AA;"
                           "border-radius:8px;}")
        vg = QHBoxLayout(vbox); vg.setContentsMargins(10, 6, 10, 6); vg.setSpacing(16)
        cap = QLabel("🎬 视频下载")
        cap.setStyleSheet("font-weight:700; color:%s;" % WARN_C)
        vg.addWidget(cap)
        self.vid = {}
        for k, label in (("total", "视频数"), ("done", "已完成"), ("bytes", "已接收"),
                         ("speed", "当前速度"), ("elapsed", "已用时间"),
                         ("eta", "预计剩余"), ("unknown", "总大小未知")):
            w = QLabel("%s —" % label)
            w.setStyleSheet("color:%s; font-size:12px;" % TXT)
            vg.addWidget(w)
            self.vid[k] = (label, w)
        vg.addStretch(1)
        root.addWidget(vbox)

        # 过滤条
        fl = QHBoxLayout(); fl.setSpacing(6)
        self.f_pid = QLineEdit(); self.f_pid.setPlaceholderText("商品 ID"); self.f_pid.setFixedWidth(90)
        self.f_role = QComboBox(); self.f_role.addItems(["全部类型", "主图", "详情图", "SKU图", "视频"])
        self.f_state = QComboBox()
        self.f_state.addItems(["全部状态", "等待", "下载中", "成功", "失败", "暂停", "取消", "等待重试"])
        self.f_q = QLineEdit(); self.f_q.setPlaceholderText("搜索 URL / 路径 / 失败原因")
        self.f_q.setFixedWidth(230)
        btn_apply = QPushButton("筛选")
        for w in (QLabel("范围"), self.f_pid, self.f_role, self.f_state, self.f_q, btn_apply):
            fl.addWidget(w)
        fl.addStretch(1)
        self.btn_open = QPushButton("▶ 打开")
        self.btn_open.setToolTip("图片用系统看图器打开；视频用系统播放器打开（都是真实本地文件）")
        self.btn_folder = QPushButton("📂 所在文件夹")
        self.btn_cppath = QPushButton("复制路径")
        self.btn_cpurl = QPushButton("复制来源")
        self.btn_swap = QPushButton("🔄 替换…")
        self.btn_swap.setToolTip("选一个新的本地文件作为候选版本，旧版本保留，确认后才生效")
        self.btn_revert = QPushButton("↩ 恢复原版")
        self.btn_fail = QPushButton("查看失败详情")
        self.btn_retry = QPushButton("重试失败项")
        for b in (self.btn_open, self.btn_folder, self.btn_cppath, self.btn_cpurl,
                  self.btn_swap, self.btn_revert, self.btn_fail, self.btn_retry):
            fl.addWidget(b)
        root.addLayout(fl)
        btn_apply.clicked.connect(lambda: self.refresh(force=True))
        self.f_q.returnPressed.connect(lambda: self.refresh(force=True))
        self.btn_open.clicked.connect(self._open_file)
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_cppath.clicked.connect(self._copy_path)
        self.btn_cpurl.clicked.connect(self._copy_url)
        self.btn_swap.clicked.connect(self._replace_file)
        self.btn_revert.clicked.connect(self._revert_file)
        self.btn_fail.clicked.connect(self._show_fail)
        self.btn_retry.clicked.connect(self._retry_failed)

        # 任务表
        self.model = TaskModel([])
        self.table = QTableView(); self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(23)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 66)
        for c, w in ((1, 86), (2, 240), (3, 76), (4, 70), (5, 140), (6, 88), (7, 52)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.table.setStyleSheet(
            "QTableView{background:#FFFFFF;border:1px solid %s;border-radius:6px;"
            "alternate-background-color:#FBFAFE;font-size:12px;}"
            "QHeaderView::section{background:%s;border:none;padding:4px;font-weight:600;}"
            % (LINE, ACCENT_L))
        self.table.doubleClicked.connect(lambda *_: self._open_file())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._ctx_menu)
        root.addWidget(self.table, 3)

        # 日志
        lg = QHBoxLayout(); lg.setSpacing(6)
        self.lf_level = QComboBox(); self.lf_level.addItems(["全部日志", "只看成功", "只看失败", "只看重试/暂停"])
        self.lf_role = QComboBox(); self.lf_role.addItems(["全部媒体", "主图", "详情图", "SKU图", "视频"])
        self.lf_q = QLineEdit(); self.lf_q.setPlaceholderText("日志关键字"); self.lf_q.setFixedWidth(180)
        b_copy = QPushButton("复制日志"); b_exp = QPushButton("导出日志")
        for w in (QLabel("事件日志"), self.lf_level, self.lf_role, self.lf_q, b_copy, b_exp):
            lg.addWidget(w)
        lg.addStretch(1)
        root.addLayout(lg)
        b_copy.clicked.connect(self._copy_log)
        b_exp.clicked.connect(self._export_log)
        self.lf_level.currentTextChanged.connect(lambda *_: self._paint_logs())
        self.lf_role.currentTextChanged.connect(lambda *_: self._paint_logs())
        self.lf_q.textChanged.connect(lambda *_: self._paint_logs())
        self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setFixedHeight(150)
        self.log_view.setStyleSheet(
            "QPlainTextEdit{background:#141322;color:#E6E1FA;border:1px solid %s;"
            "border-radius:6px;font-family:Consolas,monospace;font-size:12px;}" % LINE)
        root.addWidget(self.log_view)

        self.lb_foot = QLabel("")
        self.lb_foot.setStyleSheet("color:%s; font-size:11px;" % TXT_SUB)
        root.addWidget(self.lb_foot)

    # ---------------- 数据
    ROLE_MAP = {"主图": "main", "详情图": "detail", "SKU图": "sku", "视频": "video"}
    STATE_MAP = {"等待": "waiting", "下载中": "downloading", "成功": "done",
                 "失败": "failed", "暂停": "paused", "取消": "cancelled",
                 "等待重试": "retry_wait"}

    def refresh(self, force: bool = False):
        from .. import bridge as B
        role = self.ROLE_MAP.get(self.f_role.currentText(), "")
        state = self.STATE_MAP.get(self.f_state.currentText(), "")
        try:
            pid = int(self.f_pid.text().strip() or 0)
        except ValueError:
            pid = 0
        snap = B.v3_dl_snapshot(job_id=self.job_id, task_limit=800, log_limit=400,
                                f_pid=pid, f_role=role, f_state=state,
                                q=self.f_q.text().strip())
        if not snap.get("ok"):
            self.lb_title.setText("下载任务 · 打不开：%s" % (snap.get("error") or "?"))
            return
        job = snap.get("job")
        if not job:
            self.lb_title.setText("下载任务 · 还没有任务")
            self.lb_state.setText("—")
            self.model.set_rows([])
            return
        self.job_id = int(job["job_id"])
        self.snap = snap
        h = snap["header"]
        self.lb_title.setText("任务 #%s · %s" % (h["job_id"], h["title"] or "下载"))
        st = h["state"]
        self.lb_state.setText(STATE_CN.get(st, st))
        self.lb_state.setStyleSheet(
            "color:%s; font-weight:600;" % {"done": OK_C, "running": ACCENT,
                                            "paused": WARN_C, "failed": BAD_C,
                                            "stopped": TXT_SUB}.get(st, TXT))
        c = h["counts"]
        self.kv["job"].setText("#%s" % h["job_id"])
        self.kv["pack"].setText(h.get("pack_code") or "—")
        self.kv["products"].setText(str(h.get("products") or "—"))
        self.kv["refs"].setText(str(h["refs"]))
        self.kv["unique"].setText(str(h["unique_urls"]))
        rr = h["roles"]
        self.kv["roles"].setText("/".join(
            str((rr.get(k) or {}).get("total", 0)) for k in ("main", "detail", "sku", "video")))
        self.kv["counts"].setText("%d / %d / %d / %d" % (
            c.get("done", 0), c.get("downloading", 0) + c.get("verifying", 0),
            c.get("waiting", 0) + c.get("retry_wait", 0), c.get("failed", 0)))
        self.kv["bytes"].setText("%s / %s" % (
            _mb(h["bytes_done"]), _mb(h["bytes_known"]) if h["bytes_known"] else "未知"))
        self.kv["speed"].setText(_spd(h["speed_bps"]))
        self.kv["eta"].setText(h["eta_text"] or "暂不可估算")
        self.bar.setValue(int(min(1000, h["pct"] * 10)))
        self.lb_pct.setText("%.1f%%" % h["pct"])

        # 视频监控
        v = snap.get("video") or {}
        def _dur(s):
            try:
                s = int(s or 0)
            except (TypeError, ValueError):
                return "—"
            if s <= 0:
                return "0 秒"
            return "%d 分 %d 秒" % (s // 60, s % 60) if s >= 60 else "%d 秒" % s
        self.vid["total"][1].setText("%s %d" % (self.vid["total"][0], v.get("total", 0)))
        self.vid["done"][1].setText("%s %d" % (self.vid["done"][0], v.get("done", 0)))
        self.vid["bytes"][1].setText("%s %s%s" % (
            self.vid["bytes"][0], _mb(v.get("bytes")),
            (" / %s" % _mb(v["bytes_total"])) if v.get("bytes_total") else
            ("（总大小未知 · %d 个）" % v["unknown_total"] if v.get("unknown_total") else "")))
        self.vid["speed"][1].setText("%s %s" % (self.vid["speed"][0], _spd(v.get("speed"))))
        self.vid["elapsed"][1].setText("%s %s" % (self.vid["elapsed"][0],
                                                  _dur(v.get("elapsed"))))
        self.vid["eta"][1].setText("%s %s" % (self.vid["eta"][0],
                                              _dur(v["eta"]) if v.get("eta") else "暂不可估算"))
        self.vid["unknown"][1].setText("%s %d" % (self.vid["unknown"][0],
                                                  v.get("unknown_total", 0)))

        self.model.set_rows(snap["tasks"])
        self._paint_logs()
        self.lb_foot.setText(
            "引用 %d 条 → 去重 %d 个资源 · 并发 %d · 表内显示 %d 行 · 日志 %d 条 · 刷新 %.0f ms"
            % (h["refs"], h["unique_urls"], h["workers"], len(snap["tasks"]),
               len(snap["logs"]), (time.time() - self._last_refresh) * 1000 if self._last_refresh else 0))
        self._last_refresh = time.time()

    # ---------------- 日志绘制（客户端过滤，不重查库）
    def _logs_filtered(self) -> list[dict]:
        rows = (self.snap or {}).get("logs") or []
        lv = self.lf_level.currentText()
        role = self.ROLE_MAP.get(self.lf_role.currentText(), "")
        q = self.lf_q.text().strip()
        out = []
        for r in rows:
            if role and r.get("role") != role:
                continue
            if lv == "只看成功" and r.get("level") != "ok":
                continue
            if lv == "只看失败" and r.get("level") != "err":
                continue
            if lv == "只看重试/暂停" and r.get("level") != "warn":
                continue
            if q and q not in (r.get("text") or ""):
                continue
            out.append(r)
        return out

    def _paint_logs(self):
        rows = self._logs_filtered()
        lines = []
        for r in rows:
            tag = {"ok": "✔", "err": "✘", "warn": "!"}.get(r.get("level"), "·")
            pid = ("#%s " % r["product_id"]) if r.get("product_id") else ""
            lines.append("[%s] %s %s%s" % ((r.get("at") or "")[11:19], tag, pid,
                                           r.get("text") or ""))
        self.log_view.setPlainText("\n".join(lines[-800:]))
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------------- 动作
    def _ctl(self, what: str):
        from .. import bridge as B
        fn = {"pause": B.v3_dl_pause, "resume": B.v3_dl_resume, "stop": B.v3_dl_stop}[what]
        r = fn(self.job_id)
        if not r.get("ok"):
            self.lb_foot.setText("操作未生效：%s" % (r.get("error") or "?"))
        self.refresh(force=True)

    def _sel_task(self) -> dict | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        try:
            return self.model.rows[idx.row()]
        except Exception:  # noqa: BLE001
            return None

    def _require_file(self, want_video: bool = False):
        """取当前行 + 真实存在的本地文件；不满足就把原因写出来并返回 None。
        ★ 判据是 os.path.isfile，不是"表里有路径" —— 这一步是防"绿色成功但文件不在"。"""
        t = self._sel_task()
        if not t:
            self.lb_foot.setText("先在表里选一行（点一下那一行）")
            return None
        p = (t.get("path") or "").strip()
        if not p:
            self.lb_foot.setText("这一行还没有本地文件 · 状态=%s · 原始地址=%s"
                                 % (STATE_CN.get(t.get("state"), t.get("state")),
                                    (t.get("url") or "无")[:80]))
            return None
        if not os.path.isfile(p):
            self.lb_foot.setText("表里有路径但磁盘上没有这个文件：%s（状态=%s）"
                                 % (p, STATE_CN.get(t.get("state"), t.get("state"))))
            return None
        if want_video and not p.lower().endswith((".mp4", ".mov", ".m4v", ".webm")):
            self.lb_foot.setText("选中的不是视频行（%s · 角色=%s）"
                                 % (os.path.basename(p), t.get("role_cn") or t.get("role")))
            return None
        return t, p

    def _open_file(self):
        got = self._require_file()
        if not got:
            return
        t, p = got
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        if not ok:
            try:
                os.startfile(p)      # noqa: S606
                ok = True
            except Exception as e:   # noqa: BLE001
                self.lb_foot.setText("打开失败：%s" % e)
                return
        self.lb_foot.setText("已打开：%s（%s · %s）"
                             % (os.path.basename(p), t.get("role_cn") or t.get("role"),
                                _mb(os.path.getsize(p))))

    def _open_folder(self):
        got = self._require_file()
        if not got:
            return
        _t, p = got
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            self.lb_foot.setText("文件夹不存在：%s" % d)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))
        self.lb_foot.setText("已定位到：%s" % d)

    def _copy_path(self):
        got = self._require_file()
        if not got:
            return
        _t, p = got
        QApplication.clipboard().setText(p)
        self.lb_foot.setText("已复制本地路径：%s" % p)

    def _copy_url(self):
        t = self._sel_task()
        if not t:
            self.lb_foot.setText("先在表里选一行")
            return
        u = (t.get("url") or "").strip()
        if not u:
            self.lb_foot.setText("这一行没有记录来源地址（可能是本地导入的版本）")
            return
        QApplication.clipboard().setText(u)
        self.lb_foot.setText("已复制来源地址：%s" % u[:120])

    def _replace_file(self):
        """替换当前行的文件：导入为候选版本 → 旧版保留 → 由用户再点「确认这一版」。"""
        t = self._sel_task()
        if not t:
            self.lb_foot.setText("先在表里选一行")
            return
        pid = int(t.get("product_id") or 0)
        role = t.get("role") or "main"
        if not pid:
            self.lb_foot.setText("这一行没有商品 ID，不能替换")
            return
        is_video = role == "video"
        filt = "视频 (*.mp4 *.mov *.m4v *.webm)" if is_video else "图片 (*.jpg *.jpeg *.png *.webp)"
        p, _ = QFileDialog.getOpenFileName(self, "选择替换文件（%s · 商品 %s）" % (role, pid),
                                           "", filt + ";;所有文件 (*.*)")
        if not p:
            return
        if not os.path.isfile(p):
            self.lb_foot.setText("选的文件不存在：%s" % p)
            return
        from .. import bridge as B
        r = B.v3_import_media(pid, p, role=role, note="2.5 下载中心替换")
        if not r.get("ok"):
            self.lb_foot.setText("替换失败：%s" % (r.get("error") or "?")[:200])
            return
        new_id = r.get("image_id") or r.get("media_id")
        msg = ("已导入候选版本：商品 %s · %s · 新 ID=%s\n旧版本保留，原始文件与原始地址没动。"
               % (pid, role, new_id))
        if new_id:
            if QMessageBox.question(
                    self, "确认这一版？",
                    msg + "\n\n现在就把它确认为当前版本吗？\n（确认后会清空线上地址，"
                          "进入待上传队列）") == QMessageBox.StandardButton.Yes:
                a = B.v3_approve_media(pid, int(new_id))
                self.lb_foot.setText(("已确认新版本 · 待上传" if a.get("ok")
                                      else "确认失败：%s" % (a.get("error") or "?")))
                self.refresh(force=True)
                return
        self.lb_foot.setText(msg)

    def _revert_file(self):
        t = self._sel_task()
        if not t:
            self.lb_foot.setText("先在表里选一行")
            return
        pid = int(t.get("product_id") or 0)
        role = t.get("role") or "main"
        iid = t.get("image_id") or t.get("media_id")
        if not pid or not iid:
            self.lb_foot.setText("这一行缺商品 ID 或媒体 ID，无法恢复（image_id=%s）" % iid)
            return
        if QMessageBox.question(
                self, "恢复原版",
                "把商品 %s 的 %s 恢复成原始版本？\n\n原始文件不会被删，只是把当前版本指回原版。"
                % (pid, role)) != QMessageBox.StandardButton.Yes:
            return
        from .. import bridge as B
        r = B.v3_revert_media(pid, int(iid))
        self.lb_foot.setText(("已恢复原版" if r.get("ok")
                              else "恢复失败：%s" % (r.get("error") or "?")))
        self.refresh(force=True)

    def _ctx_menu(self, pos):
        t = self._sel_task()
        if not t:
            return
        m = QMenu(self)
        has_file = bool(t.get("path")) and os.path.isfile(t.get("path"))
        is_vid = (t.get("role") == "video")
        a1 = m.addAction("▶ 打开" + ("视频" if is_vid else "文件"))
        a1.setEnabled(has_file)
        a2 = m.addAction("📂 打开所在文件夹")
        a2.setEnabled(has_file)
        m.addSeparator()
        a3 = m.addAction("复制本地路径")
        a3.setEnabled(has_file)
        a4 = m.addAction("复制来源地址")
        a4.setEnabled(bool(t.get("url")))
        m.addSeparator()
        a5 = m.addAction("🔄 替换文件…")
        a6 = m.addAction("↩ 恢复原版")
        m.addSeparator()
        a7 = m.addAction("查看失败详情")
        if not has_file:
            a1.setToolTip(a2.setToolTip(a3.setToolTip("这一行还没有真实本地文件")))
        act = m.exec(self.table.viewport().mapToGlobal(pos))
        if act is None:
            return
        {"a1": self._open_file, "a2": self._open_folder, "a3": self._copy_path,
         "a4": self._copy_url, "a5": self._replace_file,
         "a6": self._revert_file, "a7": self._show_fail}.get(
            {a1: "a1", a2: "a2", a3: "a3", a4: "a4", a5: "a5", a6: "a6",
             a7: "a7"}.get(act, ""), lambda: None)()

    def _show_fail(self):
        t = self._sel_task()
        dlg = QDialog(self)
        dlg.setWindowTitle("失败详情")
        dlg.resize(720, 420)
        v = QVBoxLayout(dlg)
        te = QTextEdit(); te.setReadOnly(True)
        if not t:
            te.setPlainText("先在表里选一行")
        else:
            te.setPlainText(
                "商品：%s\n媒体类型：%s（%s 条引用共享这一份文件）\n状态：%s\n"
                "原始 URL：\n%s\n\n本地路径：%s\n临时文件(.part)：%s\n\n"
                "已下载：%s / %s\n速度：%s\n重试：%s/%s\n\n失败原因：\n%s\n\n"
                "可重试：%s\n断点续传过：%s\nOCR：%s %s\n"
                % (t.get("product_id"), t.get("role_cn"), t.get("refs"),
                   STATE_CN.get(t.get("state"), t.get("state")),
                   t.get("url") or "—", t.get("path") or "（无）",
                   (t.get("path") or "") + ".part",
                   _mb(t.get("bytes")), _mb(t.get("bytes_total")) if t.get("bytes_total") else "未知",
                   _spd(t.get("speed_bps")), t.get("tries") or 0, t.get("max_tries") or 3,
                   t.get("error") or "无", "是" if t.get("retryable") else "否",
                   "是" if t.get("resumable") else "否",
                   t.get("ocr_state") or "未跑", t.get("ocr_error") or ""))
        v.addWidget(te)
        b = QPushButton("关闭"); b.clicked.connect(dlg.accept); v.addWidget(b)
        dlg.exec()

    def _retry_failed(self):
        from .. import bridge as B
        r = B.v3_dl_retry_failed(self.job_id)
        self.lb_foot.setText("重试：%d 个失败项回到队列" % r.get("requeued", 0)
                             if r.get("ok") else "重试失败：%s" % (r.get("error") or "?"))
        self.refresh(force=True)

    def _copy_log(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.lb_foot.setText("日志已复制到剪贴板（%d 行）"
                             % len(self.log_view.toPlainText().splitlines()))

    def _export_log(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "导出任务日志", "下载日志_%s.txt" % self.job_id, "文本 (*.txt)")
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("任务 #%s\n%s\n\n" % (self.job_id, "=" * 60))
                f.write(self.log_view.toPlainText())
                f.write("\n\n--- 任务明细 ---\n")
                for t in (self.snap or {}).get("tasks") or []:
                    f.write("%s\t%s\t%s\t%s\t%s / %s\t%s\n" % (
                        t.get("product_id"), t.get("role_cn"),
                        STATE_CN.get(t.get("state"), t.get("state")),
                        t.get("path") or t.get("url"), _mb(t.get("bytes")),
                        _mb(t.get("bytes_total")), t.get("error") or ""))
            self.lb_foot.setText("已导出：%s" % p)
        except Exception as e:       # noqa: BLE001
            self.lb_foot.setText("导出失败：%s" % e)

    def closeEvent(self, e):  # noqa: N802
        # 关窗口 ≠ 取消下载：只停刷新定时器，任务线程继续跑
        try:
            self._timer.stop()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)
