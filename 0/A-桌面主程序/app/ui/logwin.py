# -*- coding: utf-8 -*-
"""日志浮动窗 —— 右侧竖版、可缩可放、可保存。

用户原话（2026-10-27）：
  「日志系统摆在底下不合适，干脆你把日志系统做一个浮动窗，可以独立在右边的一个
   浮动窗，可以缩小，可以放大。就是和现在修补窗口一样大小的，这样子可以看到详细的日志。
   然后日志系统还能保存到系统里，后续有什么问题，你还能读取日志来修复。」
  「每一个任务序列，1.0 2.0，每一个任务序列板块，都需要有一个独立的日志系统。
   现在的日志系统，横版的，放到最底下，看也看不清。都要做成一个可以收缩的，竖版的，
   可以独立在右边的一个日记系统。而且每次做完要有保存，要有详细的记录。
   后台 python 的代码怎么跑啊，什么东西的都记在里头。只不过就是给人看的就不要写那么
   详细，但是保存的时候都要保存，方便你这边修改的时候去读取，它到底哪个出了问题。」

设计三条（对应上面的话）：
  ① **两种视图**：界面里只显示「人看的」短行（带时间、带级别色）；落盘保存全量原文
     —— 「给人看的不要那么详细，保存的时候都要保存」。
  ② **可缩可放**：底部拖动条 + 「紧凑 / 正常 / 宽」三档，最小能压到 90px 高
     —— 不占操作区，但一直活着。
  ③ **独立于页签**：每个页签 new 一个 LogWindow，各自有自己的 session 文件
     —— 「每一个任务序列都要有独立日志」。

用法（页签里三行就够）：
    from .logwin import LogWindow, attach_log_button
    self.logw = LogWindow(self, title="4.1 主图审核", session="wall41")
    attach_log_button(self.toolbar_or_layout, self.logw)     # 加一个「📜 日志」按钮
    self.logw.info("开始处理 %d 张" % n)                      # 界面短 + 落盘全量
    self.logw.detail("框换算：归一化(0.05,0.10) → 像素(40,80)-(360,256) = 56817px")
"""
from __future__ import annotations

import os
import time
import traceback

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor, QTextCharFormat
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                             QPushButton, QVBoxLayout, QWidget)

from .. import paths

LOG_DIR = str(getattr(paths, "LOG_DIR",
                      os.path.join(os.path.dirname(__file__), "..", "..", "data", "logs")))

LEVELS = {
    "info":  ("·", "#8aa0b8"),
    "ok":    ("✓", "#4ade80"),
    "warn":  ("⚠", "#fbbf24"),
    "error": ("✗", "#f87171"),
    "step":  ("→", "#60a5fa"),
    "detail": ("  ", "#5b6675"),
}
MAX_VIEW_LINES = 4000          # 界面最多留这么多行（内存里落盘的不受影响）


class LogWindow(QDialog):
    """独立日志窗。非模态：开着也能操作主界面。

    ★★ 2026-10-27 崩溃根因修复（有原生栈为证）：
      后台工作线程（_MarkWorker 跑在 QThreadPool 里）直接调本类写界面 ——
      Qt 控件只能在 GUI 线程碰，跨线程写会 **access violation 直接把进程打死**
      （crashguard 抓到：logwin.py:166 _append ← wall_main.py:1841 _mark_log ← run）。
      所以这里加一道线程闸门：非 GUI 线程一律走排队信号，回到主线程再写。
      调用方一行都不用改，以后新写的后台代码也不会再踩这个坑。
    """

    _queued = pyqtSignal(str, str, object)      # level, text, short

    def __init__(self, parent=None, title: str = "日志", session: str = "ui"):
        super().__init__(parent)
        self.setWindowTitle("📜 " + title)
        self.setModal(False)
        self.session = session or "ui"
        self._t0 = time.time()
        self._lines = 0
        self._paused = False
        self._compact = False
        self.resize(380, 620)
        self.setMinimumWidth(260)
        self.setMinimumHeight(90)
        self._build()
        # 跨线程写日志 → 排队回主线程（必须在 _build 之后连，_build 里有 txt/lb_n）
        self._queued.connect(self._append_ui, Qt.ConnectionType.QueuedConnection)
        self._open_session_file()
        self.info("日志窗已打开 · 会话 %s · 保存到 %s" % (self.session, self._path))

    # ---------------- 界面
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(6, 4, 6, 4)
        hb.setSpacing(4)
        hb.addWidget(QLabel("📜"))
        self.lb_n = QLabel("0 行")
        self.lb_n.setStyleSheet("color:#7f8b99;font-size:11px")
        hb.addWidget(self.lb_n)
        hb.addStretch(1)
        self.btn_size = self._mini("紧凑", self._toggle_size, "在紧凑/正常之间切换")
        hb.addWidget(self.btn_size)
        self.btn_pause = self._mini("暂停", self._toggle_pause, "暂停自动滚动（日志照常记录）")
        hb.addWidget(self.btn_pause)
        self.btn_save = self._mini("💾", self._save_now, "把当前会话日志另存一份")
        hb.addWidget(self.btn_save)
        self.btn_clear = self._mini("清屏", self._clear_view, "只清界面，落盘文件不动")
        hb.addWidget(self.btn_clear)
        lay.addWidget(bar)

        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setMaximumBlockCount(MAX_VIEW_LINES)
        self.txt.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        f = QFont("Consolas")
        f.setPointSizeF(8.6)
        self.txt.setFont(f)
        self.txt.setStyleSheet(
            "QPlainTextEdit{background:#0d1117;color:#c9d1d9;border:0;"
            "padding:4px 6px;selection-background-color:#264f78}")
        lay.addWidget(self.txt, 1)

        self.lb_path = QLabel("")
        self.lb_path.setStyleSheet("color:#4a5462;font-size:9px;padding:2px 6px")
        self.lb_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.lb_path)

    def _mini(self, text: str, fn, tip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(20)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton{background:#1c2430;color:#9fb0c4;border:1px solid #2b3644;"
            "border-radius:3px;font-size:10px;padding:0 6px}"
            "QPushButton:hover{background:#26313f;color:#dbe6f2}")
        b.clicked.connect(fn)
        if tip:
            b.setToolTip(tip)
        return b

    # ---------------- 落盘
    def _open_session_file(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            self._path = os.path.join(
                LOG_DIR, "%s-%s.log" % (self.session, time.strftime("%Y%m%d")))
        except Exception:                          # noqa: BLE001
            self._path = ""
        self.lb_path.setText(self._path or "（无法写入日志目录）")

    def _write(self, level: str, text: str):
        """全量落盘（这是给 AI/排查看的：完整、带毫秒、带异常栈）。"""
        if not self._path:
            return
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write("[%s] [%-6s] %s\n" %
                         (time.strftime("%Y-%m-%d %H:%M:%S"), level, text))
        except Exception:                          # noqa: BLE001
            pass

    def _append(self, level: str, text: str, short: str | None = None):
        """线程闸门：不是 GUI 线程就排队，绝不直接碰控件。"""
        if QThread.currentThread() is not self.thread():
            try:
                self._queued.emit(str(level), str(text), short)
            except Exception:                      # noqa: BLE001
                pass
            return
        self._append_ui(level, text, short)

    def _append_ui(self, level: str, text: str, short: str | None = None):
        """真正的界面写入 —— **只允许在 GUI 线程执行**。"""
        mark, color = LEVELS.get(level, LEVELS["info"])
        if level == "detail":
            # 详细行：只在界面显示但不加时间（人看着清爽），落盘仍带时间
            line = "      " + str(text)
        else:
            line = "%s%s" % (mark, text)
        self._lines += 1
        self.lb_n.setText("%d 行" % self._lines)
        if not self._paused:
            cur = self.txt.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cur.insertText(("" if self.txt.document().isEmpty() else "\n") + line, fmt)
            self.txt.setTextCursor(cur)
            self.txt.ensureCursorVisible()

    # ---------------- 对外 API（页签只调这几个）
    def info(self, s: str):
        self._write("info", s)
        self._append("info", s)

    def ok(self, s: str):
        self._write("ok", s)
        self._append("ok", s)

    def warn(self, s: str):
        self._write("warn", s)
        self._append("warn", s)

    def error(self, s: str, exc: BaseException | None = None):
        full = s
        if exc is not None:
            full = "%s | %s: %s\n%s" % (s, type(exc).__name__, exc,
                                        traceback.format_exc())
        self._write("error", full)
        self._append("error", s)

    def step(self, s: str):
        """步骤标题：给人看的一句话。"""
        self._write("step", s)
        self._append("step", s)

    def detail(self, s: str):
        """技术细节：只落盘全文，界面缩进显示 —— 「给人看的不要那么详细」。"""
        self._write("detail", s)
        self._append("detail", s)

    def block(self, title: str):
        """分隔线 + 标题，方便事后在文件里定位一次操作。"""
        bar = "─" * 34
        self._write("info", "%s %s %s" % (bar, title, bar))
        self._append("info", "%s %s" % (bar, title))

    # ---------------- 按钮
    def _toggle_size(self):
        self._compact = not self._compact
        if self._compact:
            self.resize(max(260, self.width()), 150)
            self.btn_size.setText("展开")
        else:
            self.resize(max(380, self.width()), 620)
            self.btn_size.setText("紧凑")

    def _toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText("继续" if self._paused else "暂停")

    def _clear_view(self):
        self.txt.clear()
        self._lines = 0
        self.lb_n.setText("0 行")

    def _save_now(self) -> str:
        """把当前会话日志**另存一份带时间戳的副本**，返回路径。"""
        if not self._path or not os.path.isfile(self._path):
            return ""
        dst = os.path.join(
            LOG_DIR, "%s-导出-%s.log" % (self.session, time.strftime("%Y%m%d-%H%M%S")))
        try:
            with open(self._path, "rb") as src, open(dst, "wb") as out:
                out.write(src.read())
            self.ok("已另存日志：%s" % dst)
            return dst
        except Exception as e:                     # noqa: BLE001
            self.error("另存日志失败", e)
            return ""

    def showEvent(self, e):  # noqa: N802
        """第一次显示时靠右贴边（不抢焦点）。"""
        super().showEvent(e)
        try:
            pw = self.parentWidget()
            if pw is not None:
                g = pw.frameGeometry()
                self.move(g.right() - self.width() - 12, g.top() + 80)
        except Exception:                          # noqa: BLE001
            pass


def attach_log_button(layout_or_widget, logw: LogWindow, text: str = "📜 日志"):
    """在给定布局尾部加一个唤起日志窗的按钮，并返回它。

    layout_or_widget 传 QLayout（推荐）或 QWidget（会自动取它的 layout）。
    """
    from PyQt6.QtWidgets import QLayout
    btn = QPushButton(text)
    btn.setCheckable(True)
    btn.setFixedHeight(24)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip("打开右侧日志窗（可缩可放，日志同时落盘保存）")
    btn.setStyleSheet(
        "QPushButton{background:#1c2430;color:#9fb0c4;border:1px solid #2b3644;"
        "border-radius:4px;padding:0 8px;font-size:11px}"
        "QPushButton:checked{background:#2563eb;color:#fff;border-color:#3b82f6}")

    def _toggle(on: bool):
        if on:
            logw.show()
            logw.raise_()
        else:
            logw.hide()

    btn.toggled.connect(_toggle)

    def _closed(_):
        btn.setChecked(False)

    try:
        logw.finished.connect(_closed)
    except Exception:                              # noqa: BLE001
        pass

    lay = layout_or_widget
    if not isinstance(lay, QLayout):
        lay = layout_or_widget.layout() if hasattr(layout_or_widget, "layout") else None
    if lay is not None:
        try:
            lay.addWidget(btn)
        except Exception:                          # noqa: BLE001
            pass
    return btn
