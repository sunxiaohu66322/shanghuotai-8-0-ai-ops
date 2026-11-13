# -*- coding: utf-8 -*-
"""2.5.1 · 下载监控 —— 把原来的「下载任务中心悬浮窗」改成主窗口里的页签。

为什么改（2026-10-25 主人口径）：
  「先做一个 tab 页，把它放到新界面里面，不要做一个悬浮窗了。做了悬浮窗反而
    让整个系统现在就卡 bug 一样的卡在那了。」
  「把 2.5 阶段里面的那个下载任务日志那个按钮直接做 2.51，相当于就是监督，
    看那些下载的状态。」

三条设计约束：
  ① **纯只读**：只调 `v3_dl_snapshot()` 读库快照，不参与下载本身 ⇒ 下载再重本页也不卡
  ② **不抢焦点、不遮挡**：它是页签，天然不覆盖商品库；关掉主窗口才影响程序
  ③ **不动业务**：暂停/继续/停止走的就是 `v3_dl_pause/resume/stop`，
     与 Web 端、与原来的悬浮窗是同一套后端信号

失败不影响主程序启动：本页构造失败时显示一行原因，其余页照常。
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                             QPlainTextEdit, QPushButton, QTableView, QVBoxLayout,
                             QWidget)

try:
    from .. import theme as _T
    ACCENT = getattr(_T, "PRIMARY", "#7C3AED")
    OK_C = getattr(_T, "SUCCESS", "#16A34A")
    BAD_C = getattr(_T, "DANGER", "#DC2626")
    WARN_C = getattr(_T, "WARNING", "#EA580C")
    TXT_SUB = getattr(_T, "TEXT_SUB", "#787896")
    LINE = getattr(_T, "BORDER", "#D2D2E1")
except Exception:                        # noqa: BLE001
    ACCENT, OK_C, BAD_C, WARN_C = "#7C3AED", "#16A34A", "#DC2626", "#EA580C"
    TXT_SUB, LINE = "#787896", "#D2D2E1"

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
COLS = ["商品", "媒体", "文件", "状态", "进度", "大小", "重试", "结果"]

# 删记录的分档（2026-10-26）：进行中的行在任务真跑时不许删；已完成的一键清
LIVE_STATES = ("waiting", "downloading", "verifying", "retry_wait")
FIN_STATES = ("done", "failed", "cancelled", "skipped")


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


class _Model(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows or [])
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):      # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):   # noqa: N802
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
                return str(r.get("product_id") or "—")
            if c == 1:
                return "%s ×%s" % (r.get("role_cn") or r.get("role") or "—",
                                   r.get("refs") or 1)
            if c == 2:
                p = r.get("path") or ""
                if p:
                    return os.path.basename(p)[:44]
                u = r.get("url") or ""
                return ("…" + u[-44:]) if u else "—"
            if c == 3:
                return STATE_CN.get(r.get("state"), r.get("state") or "—")
            if c == 4:
                pct = r.get("pct")
                return "—" if pct is None else "%.1f%%" % pct
            if c == 5:
                return _mb(r.get("bytes"))
            if c == 6:
                return str(r.get("tries") or 0)
            if c == 7:
                return str(r.get("error") or "")[:80]
        if role == Qt.ItemDataRole.ForegroundRole and c == 3:
            col = STATE_COLOR.get(r.get("state"))
            return QColor(col) if col else None
        return None


class DlMonTab(QWidget):
    """2.5.1 · 下载监控（页签，不是悬浮窗）。"""

    def __init__(self, win=None):
        super().__init__()
        self.win = win
        self.job_id = int(getattr(getattr(win, "t_wall", None), "page", None)
                          and getattr(win.t_wall.page, "last_job", 0) or 0)
        self.err = ""
        self._inflight = False
        self._last_ts = 0.0
        self._rows: list[dict] = []
        self._header: dict = {}
        # ★ 8.0：下载监控补「数据包名字选择」——code → 友好名映射 + 默认最新
        self._pack_names: dict = {}
        self._pack_default = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # ---- 顶栏：数据包 + 任务选择 + 统计 + 三个控制 ----
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("数据包"))
        self.cb_pack = QComboBox()
        self.cb_pack.setMinimumWidth(190)
        self.cb_pack.setToolTip("按数据包看下载任务；默认停在最新一批。改过的名字会在这里同步。")
        self.cb_pack.currentIndexChanged.connect(lambda _: self.refresh(force=True))
        top.addWidget(self.cb_pack, 0)
        top.addWidget(QLabel("任务"))
        self.cb_job = QComboBox()
        self.cb_job.setMinimumWidth(260)
        self.cb_job.currentIndexChanged.connect(self._on_pick_job)
        top.addWidget(self.cb_job, 0)

        self.lb_sum = QLabel("—")
        self.lb_sum.setStyleSheet("color:%s" % TXT_SUB)
        top.addWidget(self.lb_sum, 1)

        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_resume = QPushButton("▶ 继续")
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_retry = QPushButton("↻ 重试失败")
        self.btn_refresh = QPushButton("⟳ 刷新")
        for b in (self.btn_pause, self.btn_resume, self.btn_stop,
                  self.btn_retry, self.btn_refresh):
            b.setMinimumWidth(84)
            top.addWidget(b)
        self.btn_pause.clicked.connect(lambda: self._ctl("pause"))
        self.btn_resume.clicked.connect(lambda: self._ctl("resume"))
        self.btn_stop.clicked.connect(lambda: self._ctl("stop"))
        self.btn_retry.clicked.connect(lambda: self._ctl("retry"))
        self.btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        lay.addLayout(top)

        # ---- 过滤 ----
        fl = QHBoxLayout()
        fl.setSpacing(8)
        fl.addWidget(QLabel("只看"))
        self.cb_state = QComboBox()
        self.cb_state.addItem("全部", "")
        for k in ("downloading", "waiting", "failed", "done", "paused"):
            self.cb_state.addItem(STATE_CN[k], k)
        self.cb_state.currentIndexChanged.connect(lambda _: self.refresh(force=True))
        fl.addWidget(self.cb_state)
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("按商品 ID / 文件名过滤…")
        self.ed_q.setClearButtonEnabled(True)
        self.ed_q.returnPressed.connect(lambda: self.refresh(force=True))
        fl.addWidget(self.ed_q, 1)
        lay.addLayout(fl)

        # ---- 记录管理：批量选择 / 删除选中 / 清空已完成 ----
        # 主人口径（2026-10-25）：「所有的信息都是要可以增改删除、批量选择之类的」。
        # 这里删的是数据库记录行，磁盘上已下载的图/视频一个不动（另有说明）。
        rl = QHBoxLayout()
        rl.setSpacing(8)
        self.lb_sel = QLabel("已选 0 条")
        self.lb_sel.setStyleSheet("color:%s" % TXT_SUB)
        rl.addWidget(self.lb_sel)

        self.btn_all = QPushButton("全选")
        self.btn_del = QPushButton("🗑 删除选中")
        self.btn_clear = QPushButton("🧹 清空已完成")
        self.btn_all.setToolTip("把当前表格里能看到的行全部选中（受上面的「只看」与过滤词限制）")
        self.btn_del.setToolTip("删掉选中的下载记录（Ctrl/Shift 可多选）。进行中的行在任务运行时会被跳过")
        self.btn_clear.setToolTip("只删「成功/失败/已取消/已跳过」的记录；正在下载、等待、校验、暂停的一条都不动")
        self.btn_del.setStyleSheet("color:%s" % BAD_C)
        for b in (self.btn_all, self.btn_del, self.btn_clear):
            b.setMinimumWidth(96)
            rl.addWidget(b)
        rl.addStretch(1)
        self.lb_deln = QLabel("删记录不动磁盘文件")
        self.lb_deln.setStyleSheet("color:%s" % TXT_SUB)
        rl.addWidget(self.lb_deln)
        lay.addLayout(rl)

        self.btn_all.clicked.connect(self._select_all)
        self.btn_del.clicked.connect(self._delete_selected)
        self.btn_clear.clicked.connect(self._clear_finished)

        # ---- 表 ----
        self.model = _Model(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(lambda _i: self._open_sel())
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 3)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._sync_sel())

        # ---- 底部提示 + 日志 ----
        self.lb_tip = QLabel("双击任意一行 = 打开这个文件（会先确认磁盘上真有它）；"
                             "右键 = 打开所在文件夹 / 复制路径 / 删除选中记录")
        self.lb_tip.setStyleSheet("color:%s" % TXT_SUB)
        lay.addWidget(self.lb_tip)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setPlaceholderText("任务事件时间线会出现在这里")
        self.log.setMinimumHeight(90)
        lay.addWidget(self.log, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self.refresh)
        self._sync_sel()

    # ---------------- 生命周期 ----------------
    def refresh(self, force: bool = False):
        """只读快照。切到本页时自动开始轮询，切走不需要停（1.2 秒一次，极轻）。"""
        if not self._timer.isActive():
            self._timer.start()
        now = time.time()
        if self._inflight or (not force and now - self._last_ts < 0.8):
            return
        self._inflight = True
        try:
            r = self._snap()
        finally:
            self._inflight = False
        self._last_ts = time.time()
        if not r or not r.get("ok"):
            msg = str((r or {}).get("error") or "快照没取到")
            self.lb_sum.setText("读任务状态失败：%s" % msg[:70])
            return
        self._apply(r, force)

    def _snap(self):
        from .. import bridge as B
        try:
            return B.v3_dl_snapshot(job_id=int(self.job_id or 0), task_limit=800,
                                    log_limit=120,
                                    f_state=(self.cb_state.currentData() or ""),
                                    q=(self.ed_q.text() or "").strip())
        except Exception as e:                 # noqa: BLE001
            self.err = "%s: %s" % (type(e).__name__, e)
            return {"ok": False, "error": self.err}

    def _apply(self, r: dict, force: bool = False):
        job = r.get("job") or {}
        tasks = r.get("tasks") or r.get("items") or []
        logs = r.get("logs") or []
        stats = r.get("stats") or {}
        # dlcenter.snapshot 把统计放在 header 里（不是 stats）——原来读 stats 所以
        # 头部永远显示「成功 0 · 失败 0」。这里以 header 为准，stats 只作老数据兜底。
        hdr = r.get("header") or {}
        self._header = hdr
        cnt = hdr.get("counts") or {}

        if force or not self.cb_job.count():
            self._fill_packs()
            self._fill_jobs(job)

        jid = job.get("id") or job.get("job_id") or self.job_id
        # 快照说“现在看的是哪个任务”就是哪个 —— 原来只在用户手动切换下拉框时才更新
        # self.job_id，从别处（2.5 建任务）进来时它一直是 0，「清空已完成」就会传 0。
        try:
            if jid and int(jid) != int(self.job_id or 0):
                self.job_id = int(jid)
        except (TypeError, ValueError):
            pass
        total = (sum(int(v or 0) for v in cnt.values()) if cnt
                 else (stats.get("total") or len(tasks)))
        done = int(cnt.get("done") or stats.get("done") or 0)
        failed = int(cnt.get("failed") or stats.get("failed") or 0)
        state = STATE_CN.get(job.get("state"), job.get("state") or "—")
        pcode = str(job.get("pack_code") or "")
        pname = self._pack_names.get(pcode) or pcode or "全库"
        self.lb_sum.setText(
            "%s · 任务 #%s · %s · 共 %s 条 · 成功 %s · 失败 %s"
            % (pname[:26], jid or "—", state, total, done, failed))

        self._rows = list(tasks)
        self.model.set_rows(tasks)

        if logs:
            txt = "\n".join(
                "%s  %s" % (l.get("ts") or l.get("at") or "",
                            str(l.get("msg") or l.get("text") or "")[:160])
                for l in logs[-80:])
            self.log.setPlainText(txt)
            self.log.verticalScrollBar().setValue(
                self.log.verticalScrollBar().maximum())

    def _fill_packs(self):
        """2.5.1 · 数据包名字选择（2026-11-13 主人：「下载监控里面，没有名字选择」）。

        数据源就是 2.0 那张 v3_pack 表，显示名与「✎ 改名」完全同源；
        默认选 r["default"]（全项目统一的最新口径），用户手动选过的保留。
        """
        from .. import bridge as B, packlabel as PL
        try:
            r = B.v3_packs() or {}
        except Exception:                      # noqa: BLE001
            return
        items = r.get("items") or []
        self._pack_names = {str(x.get("pack_code") or ""): PL.friendly(x)
                            for x in items}
        self._pack_default = str(r.get("default") or "")
        cur = self.cb_pack.currentData() or self._pack_default
        self.cb_pack.blockSignals(True)
        self.cb_pack.clear()
        self.cb_pack.addItem("（全部数据包）", "")
        for x in items:
            code = str(x.get("pack_code") or "")
            if not code:
                continue
            self.cb_pack.addItem("%s（%s 件）"
                                 % (self._pack_names.get(code, code),
                                    x.get("products_live") or 0), code)
        i = self.cb_pack.findData(cur)
        self.cb_pack.setCurrentIndex(i if i >= 0 else 0)
        self.cb_pack.blockSignals(False)

    def _cur_pack(self) -> str:
        try:
            return str(self.cb_pack.currentData() or "")
        except Exception:                      # noqa: BLE001
            return ""

    def _fill_jobs(self, cur_job: dict):
        from .. import bridge as B
        try:
            r = B.v3_dl_jobs(limit=30)
        except Exception:                      # noqa: BLE001
            return
        jobs = (r or {}).get("jobs") or []
        pack = self._cur_pack()
        if pack:
            jobs = [j for j in jobs if str(j.get("pack_code") or "") == pack]
        if not jobs and not pack:
            return
        self.cb_job.blockSignals(True)
        self.cb_job.clear()
        keep = 0
        for i, j in enumerate(jobs):
            jid = j.get("id") or j.get("job_id")
            pcode = str(j.get("pack_code") or "")
            pname = self._pack_names.get(pcode) or pcode or "全库"
            label = "【%s】#%s · %s · %s 条 · %s" % (
                pname[:22], jid, STATE_CN.get(j.get("state"), j.get("state") or "—"),
                j.get("total") or j.get("refs") or 0,
                (j.get("title") or "")[:40])
            self.cb_job.addItem(label, int(jid or 0))
            if int(jid or 0) == int(self.job_id or 0):
                keep = i
        if not jobs:
            self.cb_job.addItem("（这个数据包还没有下载任务）", 0)
            keep = 0
        self.cb_job.setCurrentIndex(keep)
        self.cb_job.blockSignals(False)

    def _on_pick_job(self, _i):
        jid = self.cb_job.currentData()
        if jid:
            self.job_id = int(jid)
            self.refresh(force=True)

    # ---------------- 控制 ----------------
    def _ctl(self, what: str):
        from .. import bridge as B
        jid = int(self.job_id or 0)
        if not jid:
            QMessageBox.information(self, "还没有任务",
                                    "这里显示的是下载任务的状态。\n"
                                    "先去 2.5 · 商品库 选一个包，点「下载本包媒体」，"
                                    "任务会自动出现在这一页。")
            return
        fn = {"pause": B.v3_dl_pause, "resume": B.v3_dl_resume,
              "stop": B.v3_dl_stop, "retry": B.v3_dl_retry_failed}.get(what)
        if fn is None:
            return
        try:
            r = fn(jid)
        except Exception as e:                 # noqa: BLE001
            QMessageBox.warning(self, "操作失败", "%s" % e)
            return
        if not (r or {}).get("ok"):
            QMessageBox.warning(self, "%s 没生效" % what,
                                "原因：%s" % str((r or {}).get("error") or "?")[:300])
        else:
            extra = ""
            if what == "pause":
                extra = "（pause_set=%s）" % r.get("pause_set")
            elif what == "resume":
                extra = "（pause_set=%s thread_alive=%s）" % (
                    r.get("pause_set"), r.get("thread_alive"))
            elif what == "stop":
                extra = "（cancelled=%s）" % r.get("cancelled")
            self.log.appendPlainText("%s  %s 已下发%s" % (
                time.strftime("%H:%M:%S"), what, extra))
        self.refresh(force=True)

    # ---------------- 文件操作 ----------------
    def _sel_rows(self) -> list[dict]:
        idx = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return [self._rows[i.row()] for i in idx if 0 <= i.row() < len(self._rows)]

    # ---------------- 选择 / 删除（2026-10-26 新增） ----------------
    def _sel_ids(self) -> list[int]:
        """选中行的 task_id —— v3_dl_task 的主键，删记录就按它删。"""
        out = []
        for r in self._sel_rows():
            try:
                tid = int(r.get("task_id") or 0)
            except (TypeError, ValueError):
                tid = 0
            if tid and tid not in out:
                out.append(tid)
        return out

    def _sync_sel(self):
        n = len(self._sel_rows())
        self.lb_sel.setText("已选 %d 条" % n)
        self.btn_del.setEnabled(n > 0)

    def _select_all(self):
        if self.model.rows:
            self.table.selectAll()

    def _confirm_box(self, title: str, text: str, yes: str = "确定"):
        """搭一个确认框（不 exec）—— 拆出来是为了能离线断言默认按钮是「取消」。"""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        b_yes = box.addButton(yes, QMessageBox.ButtonRole.DestructiveRole)
        b_no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_no)
        box.setEscapeButton(b_no)
        return box, b_yes, b_no

    def _confirm(self, title: str, text: str, yes: str = "确定") -> bool:
        """二次确认框；默认按钮与 Esc 都落在「取消」上 —— 回车不会误删。"""
        box, b_yes, _b_no = self._confirm_box(title, text, yes)
        box.exec()
        return box.clickedButton() is b_yes

    def _delete_selected(self):
        from .. import bridge as B
        rows = self._sel_rows()
        if not rows:
            QMessageBox.information(
                self, "没有选中",
                "先在上面的表里选中要删的记录 —— 按住 Ctrl 或 Shift 可以多选，"
                "也可以点「全选」。")
            return
        ids = self._sel_ids()
        if not ids:
            QMessageBox.information(
                self, "这些行删不了",
                "选中的行里没有任务编号（可能表格刚刷新过），先点「⟳ 刷新」再试。")
            return
        live = [r for r in rows if str(r.get("state") or "") in LIVE_STATES]
        txt = "将从数据库里删除 %d 条下载记录。" % len(ids)
        if live:
            txt += ("\n\n其中 %d 条是进行中的（等待/下载/校验/等重试）。\n"
                    "任务正在跑时这些行会被跳过不删 —— 想停它们请先点「⏹ 停止」。"
                    % len(live))
        txt += "\n\n磁盘上已经下载好的图片/视频文件一个都不会删。\n这个动作不能撤销。"
        if not self._confirm("删除下载记录", txt, yes="删除 %d 条" % len(ids)):
            return
        try:
            r = B.v3_dl_delete_tasks(task_ids=ids)
        except Exception as e:                 # noqa: BLE001
            QMessageBox.warning(self, "删除失败", "%s" % e)
            return
        if not (r or {}).get("ok"):
            QMessageBox.warning(self, "删除失败",
                                "原因：%s" % str((r or {}).get("error") or "?")[:300])
            return
        n = int(r.get("deleted") or 0)
        skip = int(r.get("skipped_live") or 0)
        msg = "已删除 %d 条记录" % n
        if skip:
            msg += "（跳过进行中的 %d 条）" % skip
        self.lb_deln.setText(msg)
        self.log.appendPlainText("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        self.refresh(force=True)

    def _clear_finished(self):
        from .. import bridge as B
        cnt = (self._header or {}).get("counts") or {}
        if not cnt:
            QMessageBox.information(
                self, "还没读到任务",
                "这一页还没读到任务统计，先点「⟳ 刷新」或等一次自动刷新。")
            return
        d = int(cnt.get("downloading") or 0)
        w = int(cnt.get("waiting") or 0)
        v = int(cnt.get("verifying") or 0)
        rt = int(cnt.get("retry_wait") or 0)
        pa = int(cnt.get("paused") or 0)
        n_fin = sum(int(cnt.get(k) or 0) for k in FIN_STATES)
        live = d + w + v + rt
        if not n_fin:
            QMessageBox.information(
                self, "没有可清空的记录",
                "这个任务里没有「成功/失败/已取消/已跳过」的记录。\n"
                "进行中与暂停的一条都没动。")
            return
        txt = ("将删除任务 #%s 里 %d 条已结束的记录：\n"
               "成功 %d · 失败 %d · 已取消 %d · 已跳过 %d。\n"
               % (self.job_id or "—", n_fin,
                  int(cnt.get("done") or 0), int(cnt.get("failed") or 0),
                  int(cnt.get("cancelled") or 0), int(cnt.get("skipped") or 0)))
        keep = []
        if live:
            keep.append("进行中 %d 条（下载中 %d · 等待 %d · 校验 %d · 等重试 %d）"
                        % (live, d, w, v, rt))
        if pa:
            keep.append("暂停 %d 条" % pa)
        if keep:
            txt += "\n保留 " + "、".join(keep) + " —— 一条都不动。\n"
        txt += "\n磁盘上已下载的文件不删。这个动作不能撤销。"
        if not self._confirm("清空已完成记录", txt, yes="清空 %d 条" % n_fin):
            return
        try:
            r = B.v3_dl_clear_finished(job_id=int(self.job_id or 0))
        except Exception as e:                 # noqa: BLE001
            QMessageBox.warning(self, "清空失败", "%s" % e)
            return
        if not (r or {}).get("ok"):
            QMessageBox.warning(self, "清空失败",
                                "原因：%s" % str((r or {}).get("error") or "?")[:300])
            return
        n = int(r.get("deleted") or 0)
        kept = int(r.get("kept_live") or 0) + int(r.get("kept_paused") or 0)
        msg = "已删除 %d 条（清空已完成，保留进行中/暂停 %d 条）" % (n, kept)
        self.lb_deln.setText(msg)
        self.log.appendPlainText("%s  %s" % (time.strftime("%H:%M:%S"), msg))
        self.refresh(force=True)


    def _require_file(self, p: str, want_video: bool = False) -> str:
        """打开前先确认真文件 —— 表里有路径不算数。"""
        if not p:
            return "这一行没有本地文件路径（可能还没下载完）"
        if not os.path.isfile(p):
            return ("数据库里记着这个路径，但磁盘上没有这个文件：\n%s\n\n"
                    "可以点「↻ 重试失败」或回 2.5 重新下载。" % p)
        if want_video and not p.lower().endswith((".mp4", ".mov", ".m4v", ".webm")):
            return "这个文件不是视频：%s" % os.path.basename(p)
        if os.path.getsize(p) <= 0:
            return "文件是 0 字节，等于没下成功：%s" % os.path.basename(p)
        return ""

    def _open_sel(self):
        rows = self._sel_rows()
        if not rows:
            return
        p = rows[0].get("path") or ""
        bad = self._require_file(p)
        if bad:
            QMessageBox.warning(self, "打不开", bad)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def _open_sel_folder(self):
        rows = self._sel_rows()
        if not rows:
            return
        p = rows[0].get("path") or ""
        if not p or not os.path.isfile(p):
            QMessageBox.warning(self, "找不到文件", self._require_file(p))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))

    def _copy_path(self, want_url: bool = False):
        rows = self._sel_rows()
        if not rows:
            return
        v = (rows[0].get("url") if want_url else rows[0].get("path")) or ""
        if not v:
            QMessageBox.information(self, "没有内容", "这一行没有%s。" %
                                    ("来源地址" if want_url else "本地路径"))
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(v)
        self.log.appendPlainText("%s  已复制：%s" % (time.strftime("%H:%M:%S"), v[:120]))

    def _menu(self, pos):
        rows = self._sel_rows()
        if not rows:
            return
        m = QMenu(self)
        m.addAction("打开文件", self._open_sel)
        m.addAction("打开所在文件夹", self._open_sel_folder)
        m.addSeparator()
        m.addAction("复制本地路径", lambda: self._copy_path(False))
        m.addAction("复制来源地址", lambda: self._copy_path(True))
        m.addSeparator()
        ad = m.addAction("🗑 删除选中记录（%d 条）…" % len(rows), self._delete_selected)
        ad.setEnabled(bool(self._sel_ids()))
        m.addAction("🧹 清空已完成…", self._clear_finished)
        m.addSeparator()
        r = rows[0]
        err = r.get("error") or ""
        a = m.addAction("看失败原因…", lambda: QMessageBox.information(
            self, "失败原因", err or "这一行没有记录失败原因"))
        a.setEnabled(bool(err))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def summary(self) -> str:
        try:
            n = len(self._rows)
            return "下载监控 %d 条" % n
        except Exception:                      # noqa: BLE001
            return "下载监控"
