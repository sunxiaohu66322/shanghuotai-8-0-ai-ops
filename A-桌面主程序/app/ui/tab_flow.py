# -*- coding: utf-8 -*-
"""6 · 上货流水线 —— 对接 taobao-picker 的 v3 后端（导入→细筛→画像→标题→主图→出包）

设计原则（与前面几页一致）：
    · 只调后端函数，不在界面里实现业务逻辑
    · 慢活全部丢后台线程，界面不卡；按钮点下去立刻禁用，回来再放
    · 不替主人做决定：认不出核心词就给建议让人选，不猜；
      被拦下的商品只标红不删，要放行得点「放行」
    · 只有点「出包」才写文件；只有点「确认替换」才换主图
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .. import bridge, paths, theme
from . import widgets as W
from .tabs import StepTab

STATUS_COLOR = {
    "imported": theme.TEXT_SUB,
    "profiled": "#3B82F6",
    "titled": "#8B5CF6",
    "image_handoff": "#F59E0B",
    "packaged": "#10B981",
    "published": "#059669",
    "blocked": "#DC2626",
}


class Task(QThread):
    """通用后台任务：跑一个 callable，把 dict 结果发回来。"""

    done = pyqtSignal(dict)
    note = pyqtSignal(str)

    def __init__(self, fn, label: str = "", parent=None):
        super().__init__(parent)
        self.fn = fn
        self.label = label

    def run(self):  # noqa: D102
        if self.label:
            self.note.emit(self.label)
        try:
            r = self.fn()
            self.done.emit(r if isinstance(r, dict) else {"ok": True, "result": r})
        except Exception as e:  # noqa: BLE001
            self.done.emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


class FlowTab(StepTab):
    title = "6 · 上货流水线"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self.worker: Task | None = None
        self.rows: list[dict] = []
        self.cur: dict | None = None
        self.store_id = "default"

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---------------- 工具条 ① 导入
        bar1 = QHBoxLayout()
        bar1.setSpacing(8)
        bar1.addWidget(QLabel("数据源"))
        self.cb_src = QComboBox()
        self.cb_src.setMinimumWidth(240)
        bar1.addWidget(self.cb_src)
        self.btn_import = QPushButton("导入")
        self.btn_import.clicked.connect(self._do_import)
        bar1.addWidget(self.btn_import)
        bar1.addStretch(1)
        self.btn_chain = QPushButton("一键：细筛 → 画像 → 标题")
        self.btn_chain.setObjectName("Primary")
        self.btn_chain.clicked.connect(self._do_chain)
        bar1.addWidget(self.btn_chain)
        root.addLayout(bar1)

        # ---------------- 工具条 ② 分步 + 出包
        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        self.btn_fine = QPushButton("细筛")
        self.btn_fine.clicked.connect(lambda: self._run(
            "细筛…", lambda: bridge.v3_fine_screen(), self._after_batch))
        bar2.addWidget(self.btn_fine)
        self.btn_prof = QPushButton("画像")
        self.btn_prof.clicked.connect(lambda: self._run(
            "画像…", lambda: bridge.v3_profile_all(), self._after_batch))
        bar2.addWidget(self.btn_prof)
        self.btn_title = QPushButton("批量标题")
        self.btn_title.clicked.connect(lambda: self._run(
            "批量标题…", lambda: bridge.v3_title_all(store_id=self.store_id,
                                                    count=3), self._after_batch))
        bar2.addWidget(self.btn_title)
        bar2.addSpacing(12)
        bar2.addWidget(QLabel("店铺"))
        self.cb_store = QComboBox()
        self.cb_store.setMinimumWidth(120)
        self.cb_store.currentIndexChanged.connect(self._on_store)
        bar2.addWidget(self.cb_store)
        self.btn_pkg = QPushButton("出包（19 列）")
        self.btn_pkg.clicked.connect(self._do_package)
        bar2.addWidget(self.btn_pkg)
        self.btn_out = QPushButton("打开输出目录")
        self.btn_out.clicked.connect(self._open_out)
        bar2.addWidget(self.btn_out)
        bar2.addStretch(1)
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self.refresh)
        bar2.addWidget(self.btn_reload)
        root.addLayout(bar2)

        # ---------------- 主体：左列表 / 右详情
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # 左
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        filt = QHBoxLayout()
        filt.setSpacing(6)
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("搜标题 / 商品ID")
        self.ed_q.returnPressed.connect(self.refresh)
        filt.addWidget(self.ed_q, 1)
        self.cb_status = QComboBox()
        self.cb_status.addItems(["全部", "被拦下", "已导入", "已画像", "已出标题", "已出包"])
        self.cb_status.currentIndexChanged.connect(self.refresh)
        filt.addWidget(self.cb_status)
        lv.addLayout(filt)

        self.tb = W.make_table(
            ["ID", "状态", "标题（原标题）", "图", "候选"],
            [46, 78, 300, 40, 44])
        self.tb.itemSelectionChanged.connect(self._on_pick)
        lv.addWidget(self.tb, 1)
        self.hint_l = W.Hint("")
        lv.addWidget(self.hint_l)
        split.addWidget(left)

        # 右
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        self.lb_title = QLabel("未选中商品")
        self.lb_title.setWordWrap(True)
        self.lb_title.setStyleSheet(f"color:{theme.TEXT}; font-weight:600;")
        rv.addWidget(self.lb_title)

        # 核心词
        core_row = QHBoxLayout()
        core_row.setSpacing(6)
        core_row.addWidget(QLabel("核心词"))
        self.ed_core = QLineEdit()
        self.ed_core.setPlaceholderText("认不出时按「建议」选一个，或自己填")
        core_row.addWidget(self.ed_core, 1)
        self.btn_sug = QPushButton("建议")
        self.btn_sug.clicked.connect(self._do_suggest)
        core_row.addWidget(self.btn_sug)
        self.btn_setcore = QPushButton("设为核心")
        self.btn_setcore.clicked.connect(self._do_setcore)
        core_row.addWidget(self.btn_setcore)
        rv.addLayout(core_row)

        # 交接
        hand = QHBoxLayout()
        hand.setSpacing(6)
        self.lb_path = QLabel("主图：—")
        self.lb_path.setWordWrap(True)
        self.lb_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hand.addWidget(self.lb_path, 1)
        for text, fn in (("复制路径", self._copy_path),
                         ("打开文件夹", self._open_folder),
                         ("复制标题", self._copy_title)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            hand.addWidget(b)
        rv.addLayout(hand)

        hand2 = QHBoxLayout()
        hand2.setSpacing(6)
        self.btn_newimg = QPushButton("导入新主图…")
        self.btn_newimg.clicked.connect(self._do_import_img)
        hand2.addWidget(self.btn_newimg)
        self.btn_approve = QPushButton("确认替换")
        self.btn_approve.clicked.connect(self._do_approve)
        hand2.addWidget(self.btn_approve)
        self.btn_revert = QPushButton("撤销")
        self.btn_revert.clicked.connect(self._do_revert)
        hand2.addWidget(self.btn_revert)
        hand2.addStretch(1)
        self.btn_confirm = QPushButton("确认画像")
        self.btn_confirm.clicked.connect(self._do_confirm_profile)
        hand2.addWidget(self.btn_confirm)
        self.btn_release = QPushButton("放行（解除拦截）")
        self.btn_release.clicked.connect(self._do_release)
        hand2.addWidget(self.btn_release)
        rv.addLayout(hand2)

        # 候选标题
        rv.addWidget(QLabel("标题候选（选一条才算定了）"))
        self.tb_tv = W.make_table(
            ["ID", "策略", "长度", "校验", "标题"],
            [44, 92, 50, 90, 340])
        self.tb_tv.itemDoubleClicked.connect(self._on_tv_dbl)
        rv.addWidget(self.tb_tv, 1)

        trow = QHBoxLayout()
        trow.setSpacing(6)
        self.btn_gen = QPushButton("生成更多")
        self.btn_gen.clicked.connect(self._do_gen)
        trow.addWidget(self.btn_gen)
        self.btn_sel = QPushButton("选用")
        self.btn_sel.setObjectName("Primary")
        self.btn_sel.clicked.connect(self._do_select)
        trow.addWidget(self.btn_sel)
        self.btn_rej = QPushButton("否决")
        self.btn_rej.clicked.connect(self._do_reject)
        trow.addWidget(self.btn_rej)
        trow.addStretch(1)
        self.lb_cur_title = QLabel("")
        self.lb_cur_title.setWordWrap(True)
        self.lb_cur_title.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        trow.addWidget(self.lb_cur_title, 1)
        rv.addLayout(trow)

        split.addWidget(right)
        split.setSizes([520, 640])
        root.addWidget(split, 1)

        self.note = QPlainTextEdit()
        self.note.setObjectName("LogView")
        self.note.setReadOnly(True)
        self.note.setFixedHeight(96)
        self.note.setMaximumBlockCount(500)
        root.addWidget(self.note)
        self._log("上货流水线就绪。后端：taobao-picker\\server\\v3（独立库 data\\v3.db）")

    # ------------------------------------------------------------ 小工具

    def _log(self, s: str):
        try:
            self.note.appendPlainText(s)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.log(s)
        except Exception:  # noqa: BLE001
            pass          # 构造期主窗口的日志框还没建好，不影响功能

    def _busy(self, on: bool):
        for b in (self.btn_import, self.btn_chain, self.btn_fine, self.btn_prof,
                  self.btn_title, self.btn_pkg, self.btn_gen, self.btn_sel,
                  self.btn_rej, self.btn_approve, self.btn_revert,
                  self.btn_newimg, self.btn_sug, self.btn_setcore,
                  self.btn_confirm, self.btn_release, self.btn_reload):
            b.setEnabled(not on)

    def _run(self, label: str, fn, after=None):
        if self.worker and self.worker.isRunning():
            self._log("上一个操作还在跑，等它完")
            return
        self._busy(True)
        self.worker = Task(fn, label, self)
        self.worker.note.connect(self._log)
        if after:
            self.worker.done.connect(after)
        self.worker.done.connect(lambda r: self._busy(False))
        self.worker.done.connect(self._err_check)
        self.worker.start()

    def _err_check(self, r: dict):
        if not r.get("ok"):
            self._log("✗ " + str(r.get("error") or "失败"))

    def _after_batch(self, r: dict):
        if r.get("ok"):
            bits = []
            for k in ("checked", "passed", "blocked", "duplicates", "done",
                      "needs_review", "products", "variants", "need_core_count"):
                if k in r:
                    bits.append("%s=%s" % ({"checked": "检查", "passed": "通过",
                                            "blocked": "拦下", "duplicates": "重复",
                                            "done": "成功", "needs_review": "待复核",
                                            "products": "商品", "variants": "候选",
                                            "need_core_count": "缺核心词"}.get(k, k), r[k]))
            self._log("✓ " + " · ".join(bits))
            if r.get("note"):
                self._log("  " + str(r["note"]))
        self.refresh()

    # ------------------------------------------------------------ 刷新

    def refresh(self):
        self._load_sources()
        st = self.cb_status.currentText()
        st_map = {"被拦下": "blocked", "已导入": "imported", "已画像": "profiled",
                  "已出标题": "titled", "已出包": "packaged"}
        q = self.ed_q.text().strip() or None
        r = bridge.v3_products(status=st_map.get(st), q=q, limit=400)
        if not r.get("ok"):
            self.hint_l.setText("读不到流水线库：" + str(r.get("error")))
            return
        self.rows = r.get("items") or []
        data = [[x["product_id"], x.get("status", ""), (x.get("original_title") or "")[:48],
                 x.get("n_img", 0), x.get("n_selected", 0)] for x in self.rows]
        colors = {i: {1: STATUS_COLOR.get(row[1], theme.TEXT_SUB)}
                  for i, row in enumerate(data)}
        W.fill_table(self.tb, data, colors)
        self.hint_l.setText(
            "共 %d 条（被拦下的不会进出包，点「放行」可解除）" % len(data))
        self._load_stores()

    def _load_stores(self):
        ov = bridge.v3_overview()
        if ov.get("ok"):
            cur = self.cb_store.currentText()
            self.cb_store.blockSignals(True)
            self.cb_store.clear()
            for s in ov.get("stores") or []:
                self.cb_store.addItem(s.get("display_name") or s["store_id"],
                                      s["store_id"])
            if cur:
                i = self.cb_store.findText(cur)
                if i >= 0:
                    self.cb_store.setCurrentIndex(i)
            self.cb_store.blockSignals(False)
            self._on_store()

    def _on_store(self):
        sid = self.cb_store.currentData()
        if sid:
            self.store_id = sid

    def _load_sources(self):
        if self.cb_src.count():
            return
        r = bridge.v3_sources()
        if not r.get("ok"):
            self.cb_src.addItem("（读不到数据源）", None)
            return
        self.cb_src.addItem("— 小猪本地库 · 全部 —", ("pig", ""))
        if r.get("taobao_db"):
            for k in (r.get("taobao_keywords") or [])[:20]:
                self.cb_src.addItem("淘宝采集：%s（%s 件）" % (k.get("kw"), k.get("n")),
                                    ("taobao", k.get("kw")))
        for p in r.get("pig_packs") or []:
            self.cb_src.addItem("小猪包：%s（约 %s 件）" % (p.get("pack"), p.get("n")),
                                ("pig", p.get("pack")))
        for f in (r.get("files") or [])[:40]:
            kind = "csv" if f["name"].lower().endswith(".csv") else "json"
            self.cb_src.addItem("文件：%s（%.0f KB）" % (f.get("rel"), f["size"] / 1024),
                                (kind, f.get("path")))

    # ------------------------------------------------------------ 动作

    def _do_import(self):
        d = self.cb_src.currentData()
        if not d:
            QMessageBox.information(self, "没有数据源", "先确认小猪本地库或 packs 目录在")
            return
        kind, val = d

        def _f():
            if kind in ("pig", "taobao"):
                return bridge.v3_import(kind, pack=val or "")
            return bridge.v3_import(kind, pack="", path=val)

        self._run("导入中…", _f, self._after_import)

    def _after_import(self, r: dict):
        if r.get("ok"):
            for line in r.get("lines") or []:
                self._log("✓ " + line)
            for w in (r.get("warnings") or [])[:5]:
                self._log("  ⚠ " + str(w))
        self.refresh()

    def _do_chain(self):
        self._run("一键跑：细筛 → 画像 → 标题 …",
                  lambda: bridge.v3_chain(store_id=self.store_id), self._after_chain)

    def _after_chain(self, r: dict):
        if r.get("ok"):
            fs = r.get("fine_screen") or {}
            pf = r.get("profile") or {}
            tt = r.get("title") or {}
            self._log("✓ 细筛：检查 %s · 通过 %s · 拦下 %s · 重复 %s" % (
                fs.get("checked"), fs.get("passed"), fs.get("blocked"), fs.get("duplicates")))
            self._log("✓ 画像：成功 %s · 待复核 %s" % (pf.get("done"), pf.get("needs_review")))
            self._log("✓ 标题：商品 %s · 候选 %s · 缺核心词 %s" % (
                tt.get("products"), tt.get("variants"), tt.get("need_core_count")))
            if tt.get("need_core_count"):
                self._log("  → 左边筛「已画像」挑一个，右边点「建议」选核心词")
        self.refresh()

    def _do_package(self):
        self._run("出包中…", lambda: bridge.v3_export_package(store_id=self.store_id),
                  self._after_pkg)

    def _after_pkg(self, r: dict):
        if r.get("ok"):
            self._log("✓ 已出包：%s（%d 行）" % (r.get("name"), r.get("count", 0)))
            if r.get("skipped_blocked"):
                self._log("  跳过被拦下的 %d 条" % r["skipped_blocked"])
            if r.get("pending_upload_images"):
                self._log("  ⚠ %d 张图还没有远程 URL（清单在同名 _待上传图.csv）"
                          % r["pending_upload_images"])
            if r.get("pending_review"):
                self._log("  ⚠ %d 条画像还没人工确认（能在包里，但清单里标了）"
                          % r["pending_review"])
            if r.get("pending_upload_csv"):
                self._log("  待上传图清单：%s" % r["pending_upload_csv"])
        self.refresh()

    def _open_out(self):
        d = bridge.v3_out_dir()
        if os.path.isdir(d):
            os.startfile(d)  # noqa: S606
        else:
            QMessageBox.information(self, "还没有输出", d)

    # ------------------------------------------------------------ 选中一行

    def _on_pick(self):
        it = self.tb.currentRow()
        if it < 0 or it >= len(self.rows):
            return
        pid = self.rows[it]["product_id"]
        r = bridge.v3_product(pid)
        if not r.get("ok"):
            self._log("✗ " + str(r.get("error")))
            return
        self.cur = r
        p = r["product"]
        prof = p.get("profile") or {}
        self.lb_title.setText("#%d %s" % (pid, p.get("original_title") or ""))
        self.ed_core.setText(prof.get("core_product_type") or "")
        conf = prof.get("confidence")
        flag = "需人工确认" if prof.get("needs_review") else "可用"
        self.hint_l.setText("状态 %s · 置信度 %s（%s）· 属性 %d 条" % (
            p.get("status"), conf, flag, len(prof.get("verified_attributes") or {})))
        h = bridge.v3_handoff(pid, self.store_id)
        if h.get("ok"):
            ok = "有" if h.get("file_exists") else "没有"
            self.lb_path.setText("主图：%s\n本地文件：%s" % (
                h.get("local_path") or "—", ok))
        self._fill_variants(r.get("variants") or [])

    def _fill_variants(self, items: list[dict]):
        data, colors = [], {}
        for i, v in enumerate(items):
            val = v.get("validation") or {}
            mark = "✓" if val.get("ok") else "✗"
            st = v.get("status")
            if st == "selected":
                mark = "★ 已选"
            elif st == "rejected":
                mark = "× 否决"
            data.append([v["variant_id"], v.get("strategy_label") or v.get("strategy"),
                         val.get("length") or len(v.get("title_text") or ""), mark,
                         v.get("title_text")])
            colors[i] = {3: ("#10B981" if st == "selected" else
                             ("#DC2626" if st == "rejected" or not val.get("ok")
                              else theme.TEXT_SUB))}
        W.fill_table(self.tb_tv, data, colors)
        sel = [v for v in items if v.get("status") == "selected"]
        self.lb_cur_title.setText(
            "已定标题：%s" % (sel[0]["title_text"] if sel else "（还没选）"))

    def _cur_pid(self) -> int | None:
        return (self.cur or {}).get("product", {}).get("product_id")

    def _do_suggest(self):
        pid = self._cur_pid()
        if not pid:
            return
        r = bridge.v3_core_suggest(pid)
        if not r.get("ok"):
            self._log("✗ " + str(r.get("error")))
            return
        cands = r.get("candidates") or []
        if not cands:
            self._log("给不出建议（标题里没有可用片段），请自己填一个核心词")
            return
        txt = "｜".join("%s%s" % (c["word"], "" if c["level"] == "A" else "?")
                        for c in cands)
        self._log("核心词建议（? = 需确认）：" + txt)
        self.ed_core.setText(cands[0]["word"])
        self.ed_core.setFocus()
        self.ed_core.selectAll()

    def _do_setcore(self):
        pid = self._cur_pid()
        if not pid:
            return
        w = self.ed_core.text().strip()
        if not w:
            return
        r = bridge.v3_core_set(pid, w)
        self._log(("✓ 核心词已设为「%s」" % w) if r.get("ok")
                  else ("✗ " + str(r.get("error"))))
        self.refresh()
        if r.get("ok"):
            self._on_pick()

    # ------------------------------------------------------------ 标题动作

    def _do_gen(self):
        pid = self._cur_pid()
        if not pid:
            return
        self._run("生成标题…", lambda: bridge.v3_generate_titles(
            pid, self.store_id, 3), self._after_gen)

    def _after_gen(self, r: dict):
        if r.get("ok"):
            self._log("✓ 生成 %d 条候选（跳过重复 %s 条）" % (
                r.get("count", 0), r.get("skipped_duplicate", 0)))
            if r.get("ai_note"):
                self._log("  " + str(r["ai_note"]))
        elif r.get("exhausted"):
            self._log("候选空间用尽：" + str(r.get("error")))
        self._on_pick()

    def _sel_vid(self) -> int | None:
        it = self.tb_tv.currentRow()
        if it < 0:
            return None
        cell = self.tb_tv.item(it, 0)
        return int(cell.text()) if cell else None

    def _do_select(self):
        vid = self._sel_vid()
        if not vid:
            return
        r = bridge.v3_select_variant(vid)
        self._log(("✓ 已选：%s" % r.get("title_text")) if r.get("ok")
                  else ("✗ " + str(r.get("error"))))
        self._on_pick()
        self.refresh()

    def _do_reject(self):
        vid = self._sel_vid()
        if not vid:
            return
        r = bridge.v3_reject_variant(vid)
        self._log("✓ 已否决" if r.get("ok") else ("✗ " + str(r.get("error"))))
        self._on_pick()

    def _on_tv_dbl(self, item):
        """双击候选标题 = 复制到剪贴板（人工核实用）。"""
        row = item.row()
        cell = self.tb_tv.item(row, 4)
        if cell:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(cell.text())
            self._log("已复制标题：" + cell.text())

    def _copy_title(self):
        pid = self._cur_pid()
        if not pid:
            return
        r = bridge.v3_copy_title(pid, self.store_id)
        if r.get("ok"):
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(r["copy_text"])
            self._log("已复制已定标题：" + r["copy_text"])
        else:
            self._log("✗ " + str(r.get("error")))

    # ------------------------------------------------------------ 主图动作

    def _copy_path(self):
        p = (self.lb_path.text() or "").split("\n")[0].replace("主图：", "").strip()
        if not p or p == "—":
            self._log("还没有可选的主图路径")
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(p)
        self._log("已复制路径：" + p)

    def _open_folder(self):
        p = (self.lb_path.text() or "").split("\n")[0].replace("主图：", "").strip()
        if not p or p == "—":
            return
        d = os.path.dirname(p)
        if os.path.isdir(d):
            os.startfile(d)  # noqa: S606
        else:
            self._log("目录不存在：" + d)

    def _do_import_img(self):
        pid = self._cur_pid()
        if not pid:
            return
        base = bridge.v3_out_dir()
        f, _ = QFileDialog.getOpenFileName(
            self, "选择处理好的新主图", os.path.dirname(base),
            "图片 (*.jpg *.jpeg *.png *.webp *.bmp);;所有文件 (*.*)")
        if not f:
            return
        r = bridge.v3_import_main(pid, f, note="界面导入")
        if r.get("ok"):
            self._log("✓ 已导入候选 image_id=%s（点「确认替换」才生效）" % r.get("image_id"))
            self._last_new_img = r.get("image_id")
        else:
            self._log("✗ " + str(r.get("error")))

    def _do_approve(self):
        pid = self._cur_pid()
        iid = getattr(self, "_last_new_img", None)
        if not pid or not iid:
            self._log("先「导入新主图」，再点确认替换")
            return
        r = bridge.v3_approve_main(pid, iid)
        self._log(("✓ 主图已替换（原图保留，可撤销）") if r.get("ok")
                  else ("✗ " + str(r.get("error"))))
        self._on_pick()

    def _do_revert(self):
        pid = self._cur_pid()
        if not pid:
            return
        r = bridge.v3_revert_main(pid)
        self._log(("✓ 已撤销回原主图") if r.get("ok") else ("✗ " + str(r.get("error"))))
        self._on_pick()

    def _do_confirm_profile(self):
        pid = self._cur_pid()
        if not pid:
            return
        r = bridge.v3_confirm_profile(pid)
        self._log(("✓ 画像已确认（属性我看过了）") if r.get("ok")
                  else ("✗ " + str(r.get("error"))))
        self._on_pick()

    def _do_release(self):
        pid = self._cur_pid()
        if not pid:
            return
        if QMessageBox.question(
                self, "放行", "这条被细筛拦下了。放行后它会进入后续流程与出包。\n确定？"
        ) != QMessageBox.StandardButton.Yes:
            return
        r = bridge.v3_release(pid)
        self._log(("✓ 已放行") if r.get("ok") else ("✗ " + str(r.get("error"))))
        self.refresh()
