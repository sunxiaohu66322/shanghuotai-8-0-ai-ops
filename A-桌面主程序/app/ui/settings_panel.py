# -*- coding: utf-8 -*-
"""右侧设置抽屉 —— 原来的「7 · 设置」页不再占页签，改挂在主界面右边。

五块内容（顺序固定）：
  ① 本机状态：9277 / 9109 / 9222 三个端口在不在听
  ② 路径：仓库、出表目录、小猪目录、图片目录，存在打 ✓
  ③ 图床：域名（可改，存回 settings）
  ④ 词库治理：已豁免的词 + 一键还原（这是第 3 步那个按钮的台账页）
  ⑤ 折叠：点标题条收起/展开
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QDialog, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QScrollArea, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from .. import bridge, paths, theme

PORTS = (9277, 9109, 9222)


class _FnWorker(QThread):
    """把一个慢函数放到后台跑（探活 / 让模型提炼规则都会卡几十秒）。"""

    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):                                   # noqa: D102
        try:
            r = self.fn()
        except Exception as e:                       # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.done.emit(r)


class _ProbeWorker(QThread):
    """探活要在后台跑：4 个候选 × 最长 25 秒，放界面线程就会假死。"""

    done = pyqtSignal(object)

    def __init__(self, role: str = "text", parent=None):
        super().__init__(parent)
        self.role = role

    def run(self):                                   # noqa: D102
        try:
            r = bridge.ai_pool_probe(self.role)
        except Exception as e:                       # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.done.emit(r)


class SettingsPanel(QWidget):
    """右侧设置抽屉：点标题条可折叠。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setMinimumWidth(260)
        self.setMaximumWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.head = QPushButton("⚙ 设置")
        self.head.setCheckable(True)
        self.head.setChecked(True)
        self.head.setStyleSheet(
            "QPushButton{text-align:left;padding:6px 10px;font-weight:600;"
            f"background:{theme.PRIMARY_LIGHT};color:{theme.PRIMARY};border:none;}}")
        self.head.clicked.connect(self._toggle)
        outer.addWidget(self.head)

        self.body = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.body)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        v = QVBoxLayout(self.body)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # ① 本机状态
        g1 = QGroupBox("本机状态")
        l1 = QVBoxLayout(g1)
        self.lb_ports = QLabel("—")
        self.lb_ports.setWordWrap(True)
        l1.addWidget(self.lb_ports)
        b1 = QPushButton("刷新")
        b1.clicked.connect(self._refresh_ports)
        l1.addWidget(b1)
        v.addWidget(g1)

        # ② 路径
        g2 = QGroupBox("路径")
        l2 = QVBoxLayout(g2)
        self.lb_paths = QLabel("—")
        self.lb_paths.setWordWrap(True)
        self.lb_paths.setStyleSheet("font-size:11px;")
        l2.addWidget(self.lb_paths)
        b2 = QPushButton("刷新")
        b2.clicked.connect(self._refresh_paths)
        l2.addWidget(b2)
        v.addWidget(g2)

        # ③ 图床
        g3 = QGroupBox("图床")
        l3 = QVBoxLayout(g3)
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("https://img.example.com")
        l3.addWidget(self.ed_host)
        b3 = QPushButton("保存图床")
        b3.clicked.connect(self._save_host)
        l3.addWidget(b3)
        self.lb_host = QLabel("—")
        self.lb_host.setWordWrap(True)
        self.lb_host.setStyleSheet("font-size:11px;")
        l3.addWidget(self.lb_host)
        v.addWidget(g3)

        # ④ 词库治理
        g4 = QGroupBox("词库治理（已豁免的词）")
        l4 = QVBoxLayout(g4)
        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(["词", "命中", ""])
        self.tb.verticalHeader().setVisible(False)
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb.setMinimumHeight(140)
        l4.addWidget(self.tb)
        b4 = QPushButton("刷新")
        b4.clicked.connect(self._refresh_waivers)
        l4.addWidget(b4)
        v.addWidget(g4)

        # ⑤ AI 模型（精筛判官 + 免费模型池）—— 2026-10-27 主人要求：
        #   「还要有一个设置面板，可以设置不同的免费模型」「有的时候免费，有的时候它不免费」
        #   所以这里既管「AI 判不判」，也管「池子里有哪几个免费模型、现在用哪个」。
        g5 = QGroupBox("AI 模型（精筛判官 · 免费模型池）")
        l5 = QVBoxLayout(g5)
        self.cb_ai = QCheckBox("精筛判定权交 AI（关掉 = 退回关键词判定）")
        self.cb_ai.stateChanged.connect(self._toggle_ai)
        l5.addWidget(self.cb_ai)
        self.lb_ai = QLabel("—")
        self.lb_ai.setWordWrap(True)
        self.lb_ai.setStyleSheet("font-size:11px;")
        l5.addWidget(self.lb_ai)
        self.tb_ai = QTableWidget(0, 3)
        self.tb_ai.setHorizontalHeaderLabels(["模型", "地址", "状态"])
        self.tb_ai.verticalHeader().setVisible(False)
        ha = self.tb_ai.horizontalHeader()
        ha.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        ha.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ha.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tb_ai.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb_ai.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tb_ai.setMinimumHeight(150)
        self.tb_ai.cellDoubleClicked.connect(self._use_selected_model)
        l5.addWidget(self.tb_ai)
        row5 = QHBoxLayout()
        self.btn_probe = QPushButton("探活全部")
        self.btn_probe.clicked.connect(self._probe_ai)
        row5.addWidget(self.btn_probe)
        b5b = QPushButton("设为当前")
        b5b.clicked.connect(self._use_selected_model)
        row5.addWidget(b5b)
        b5c = QPushButton("删除")
        b5c.clicked.connect(self._remove_model)
        row5.addWidget(b5c)
        l5.addLayout(row5)
        self.ed_ai_base = QLineEdit()
        self.ed_ai_base.setPlaceholderText("地址 https://…/v1（本机网关填 http://127.0.0.1:8326/v1）")
        l5.addWidget(self.ed_ai_base)
        self.ed_ai_model = QLineEdit()
        self.ed_ai_model.setPlaceholderText("模型名，例如 qodercn/qfmodel")
        l5.addWidget(self.ed_ai_model)
        row5b = QHBoxLayout()
        self.ed_ai_key = QLineEdit()
        self.ed_ai_key.setPlaceholderText("密钥（留空则沿用当前角色的）")
        self.ed_ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        row5b.addWidget(self.ed_ai_key)
        b5d = QPushButton("加入池")
        b5d.clicked.connect(self._add_model)
        row5b.addWidget(b5d)
        l5.addLayout(row5b)
        v.addWidget(g5)

        # ⑤b 选品条件（真源）—— 2026-10-28 主人要求：
        #   「现在的商品精筛根本就没有相应的条件，相当于提示词都没有，随随便便筛的…
        #    把这些因素都做成一个设置页放进去，方便后期变更。」
        #   这里是「入口 + 当前口径摘要」；真正的编辑在 CriteriaDialog（结构化分区 + 提示词实时预览 + 试判）。
        g5c = QGroupBox("选品条件（真源 · AI 提示词由它生成）")
        l5c = QVBoxLayout(g5c)
        self.lb_crit = QLabel("—")
        self.lb_crit.setWordWrap(True)
        self.lb_crit.setStyleSheet("font-size:11px;")
        l5c.addWidget(self.lb_crit)
        row5c = QHBoxLayout()
        b5c1 = QPushButton("✎ 编辑条件")
        b5c1.setToolTip("改价位/人群/品类/品牌/质量风险/行情/打码条件；保存即热读生效，不用重启")
        b5c1.clicked.connect(self._open_criteria)
        row5c.addWidget(b5c1)
        b5c2 = QPushButton("👁 提示词")
        b5c2.setToolTip("看 AI 现在到底收到了什么（就是「它凭什么这么判」）")
        b5c2.clicked.connect(self._preview_prompt)
        row5c.addWidget(b5c2)
        l5c.addLayout(row5c)
        v.addWidget(g5c)
        #   「这个提示词后面要改的话，现在就没有地方改」「我点评它的结果，它会根据我的话
        #     进一步把之前的提示词进行丰富」
        g6 = QGroupBox("判定提示词（AI 按这段判）")
        l6 = QVBoxLayout(g6)
        self.lb_rules = QLabel("—")
        self.lb_rules.setWordWrap(True)
        self.lb_rules.setStyleSheet("font-size:11px;")
        l6.addWidget(self.lb_rules)
        self.tx_rules = QPlainTextEdit()
        self.tx_rules.setMinimumHeight(200)
        self.tx_rules.setStyleSheet("font-size:11px;")
        self.tx_rules.setToolTip("这段会原样喂给 AI 当判定依据。\n"
                                 "改完点「保存提示词」立即生效（下一件就按新的判），不用重启。\n"
                                 "点「恢复默认」会清空手写内容、改回由选品配置自动生成。")
        l6.addWidget(self.tx_rules)
        row6 = QHBoxLayout()
        b6a = QPushButton("保存提示词")
        b6a.clicked.connect(self._save_rules)
        row6.addWidget(b6a)
        b6b = QPushButton("恢复默认")
        b6b.clicked.connect(self._reset_rules)
        row6.addWidget(b6b)
        l6.addLayout(row6)

        l6.addWidget(QLabel("店主点评（作为「已确认口径」优先于上面的规则）"))
        self.tb_fb = QTableWidget(0, 3)
        self.tb_fb.setHorizontalHeaderLabels(["时间", "商品", "点评"])
        self.tb_fb.verticalHeader().setVisible(False)
        hf = self.tb_fb.horizontalHeader()
        hf.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hf.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hf.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tb_fb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tb_fb.setMinimumHeight(110)
        l6.addWidget(self.tb_fb)
        row6b = QHBoxLayout()
        b6c = QPushButton("合并进提示词")
        b6c.setToolTip("把上面每条点评原样追加成规则条目（不调模型，结果可预期）")
        b6c.clicked.connect(self._apply_fb)
        row6b.addWidget(b6c)
        self.btn_refine = QPushButton("让 AI 提炼")
        self.btn_refine.setToolTip("让模型把多条点评归纳成 6 条以内规则，结果填进上面的框，"
                                  "你看过再点保存（不自动改口径）")
        self.btn_refine.clicked.connect(self._refine_fb)
        row6b.addWidget(self.btn_refine)
        b6d = QPushButton("删掉选中")
        b6d.clicked.connect(self._del_fb)
        row6b.addWidget(b6d)
        l6.addLayout(row6b)
        v.addWidget(g6)
        # ★ 2026-10-27：AI 模型这块要放最上面。主人原话「我还是没有看到 AI 的调节窗口」——
        #   它原来排在第五块，得往下滚才看得见，等于藏起来了。
        v.insertWidget(0, g5)

        v.addStretch(1)
        self.refresh()

    # ---------------- 折叠 ----------------
    def _toggle(self):
        self.body.setVisible(self.head.isChecked())

    # ---------------- 刷新 ----------------
    def refresh(self):
        self._refresh_ports()
        self._refresh_paths()
        self._refresh_host()
        self._refresh_waivers()
        self._refresh_ai()
        self._refresh_rules()
        self._refresh_criteria()

    # ---------------- 选品条件（2026-10-28） ----------------
    def _refresh_criteria(self):
        try:
            r = bridge.criteria_status()
        except Exception as e:                       # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if not r.get("ok"):
            self.lb_crit.setText("读不到选品条件：%s" % (r.get("error") or ""))
            return
        self.lb_crit.setText(
            "版本 v%s（%s）\n"
            "价位 %s\n"
            "人群 %s\n"
            "品类：优先 %s 词 / 排除 %s 词 / 软排除 %s 词\n"
            "%s ｜ 打码规则 %s 条\n"
            "AI 提示词 %s 字"
            % (r.get("version"), r.get("updated"),
               r.get("price"), r.get("audience"),
               r.get("preferred_n"), r.get("excluded_n"), r.get("soft_n"),
               r.get("margin"), r.get("mask_rules"), r.get("prompt_chars")))

    def _open_criteria(self):
        try:
            from .criteria_dialog import CriteriaDialog        # noqa: PLC0415
        except Exception as e:                                 # noqa: BLE001
            self.lb_crit.setText("打不开编辑窗口：%s: %s" % (type(e).__name__, e))
            return
        dlg = CriteriaDialog(self)
        dlg.exec()
        self._refresh_criteria()
        self._refresh_rules()                                  # 提示词可能跟着变了

    def _preview_prompt(self):
        try:
            r = bridge.ai_prompt_now()
        except Exception as e:                                 # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if not r.get("ok"):
            self.lb_crit.setText("取不到提示词：%s" % (r.get("error") or ""))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 判官现在收到的规则（%s 字%s）"
                           % (r.get("chars"), " · 由选品条件自动生成"
                              if r.get("using_default") else " · 有手写覆盖"))
        lay = QVBoxLayout(dlg)
        tx = QPlainTextEdit(str(r.get("text") or ""))
        tx.setReadOnly(True)
        tx.setMinimumSize(760, 560)
        lay.addWidget(tx)
        dlg.exec()

    def _refresh_ports(self):
        bits = []
        for p in PORTS:
            try:
                on = bridge.port_open(p)
            except Exception:  # noqa: BLE001
                on = False
            bits.append("%d %s" % (p, "● 在听" if on else "○ 未启动"))
        self.lb_ports.setText("\n".join(bits))
        self.lb_ports.setStyleSheet(
            f"font-size:12px;color:{theme.SUCCESS if bridge.port_open(9109) else theme.TEXT_SUB};")

    def _refresh_paths(self):
        rows = []
        try:
            for label, val, ok in bridge.env_report():
                rows.append("%s %s" % ("✓" if ok else "✗", label))
        except Exception as e:  # noqa: BLE001
            rows.append("读不到环境：%s" % e)
        for name in ("TAOBAO_DIR", "PIG_READER"):
            p = getattr(paths, name, None)
            if p:
                rows.append("%s %s" % ("✓" if os.path.exists(str(p)) else "✗", name))
        self.lb_paths.setText("\n".join(rows) or "—")

    def _refresh_host(self):
        try:
            from v3 import upload as _up  # noqa: PLC0415
            host = _up._cfg()[0] or ""
        except Exception:  # noqa: BLE001
            host = ""
        cur = str(bridge.load_settings().get("img_host") or "") or host
        if not self.ed_host.text():
            self.ed_host.setText(cur)
        self.lb_host.setText(("当前生效：%s" % (host or "（读不到，检查 本地鉴图\\鉴图.py）")))

    def _save_host(self):
        d = bridge.load_settings()
        d["img_host"] = self.ed_host.text().strip()
        bridge.save_settings(d)
        self.lb_host.setText("已保存到本机设置：%s（改图床域名还要改 鉴图.py 的 HOST 才真生效）"
                             % d["img_host"])

    def _refresh_waivers(self):
        r = bridge.v3_keyword_waivers()
        items = (r.get("items") or []) if r.get("ok") else []
        self.tb.setRowCount(len(items))
        for i, it in enumerate(items):
            self.tb.setItem(i, 0, QTableWidgetItem(str(it.get("text") or "")))
            self.tb.setItem(i, 1, QTableWidgetItem(str(it.get("hits") or 0)))
            w = str(it.get("text") or "")
            b = QPushButton("还原")
            b.clicked.connect(lambda _=False, ww=w: self._restore(ww))
            self.tb.setCellWidget(i, 2, b)
        if not items:
            self.tb.setRowCount(1)
            self.tb.setItem(0, 0, QTableWidgetItem("还没有豁免过任何词"))
            self.tb.setSpan(0, 0, 1, 3)

    # ---------------- AI 模型 ----------------
    def _refresh_ai(self):
        st = bridge.ai_status()
        fs = bridge.ai_fine_status()
        if not st.get("ok"):
            self.lb_ai.setText("读不到 AI 配置：%s" % st.get("error"))
            return
        self.cb_ai.blockSignals(True)
        self.cb_ai.setChecked(bool(fs.get("on")))
        self.cb_ai.blockSignals(False)
        av = (fs.get("available") or {})
        _base = ((st.get("roles") or {}).get("text") or {}).get("base") or "—"
        self.lb_ai.setText(
            "判官：%s ｜ 当前模型 %s\n接口地址：%s ｜ 池内可用 %s 个%s" % (
            "开" if fs.get("on") else "关",
            (av.get("model") or "—"), _base,
            av.get("pool") if av.get("ok") else 0,
            "" if av.get("ok") else ("（%s）" % av.get("why"))))
        self.lb_ai.setStyleSheet(
            "font-size:11px;color:%s;" % (theme.SUCCESS if av.get("ok") else theme.DANGER))
        rows = st.get("pool") or []
        self.tb_ai.setRowCount(len(rows))
        for i, r in enumerate(rows):
            it = QTableWidgetItem(r.get("label") or r.get("model") or "")
            it.setData(Qt.ItemDataRole.UserRole, r.get("idx"))
            it.setData(Qt.ItemDataRole.UserRole + 1, r.get("pool_idx"))
            self.tb_ai.setItem(i, 0, it)
            self.tb_ai.setItem(i, 1, QTableWidgetItem(r.get("base") or ""))
            tag = "★ 当前" if r.get("active") else ""
            cell = QTableWidgetItem(tag)
            if r.get("active"):
                cell.setForeground(Qt.GlobalColor.darkGreen)
            self.tb_ai.setItem(i, 2, cell)

    def _toggle_ai(self):
        r = bridge.ai_fine_set(bool(self.cb_ai.isChecked()))
        if self.win and hasattr(self.win, "log"):
            self.win.log("AI 判官已%s%s" % ("打开" if r.get("ok") and r.get("on") else "关闭",
                                            "" if r.get("ok") else "（失败：%s）" % r.get("error")))
        self._refresh_ai()

    def _probe_ai(self):
        self.btn_probe.setEnabled(False)
        self.lb_ai.setText("正在逐个探活（每个都真发一句话，慢是正常的）…")
        self._pw = _ProbeWorker("text", self)
        self._pw.done.connect(self._probe_done)
        self._pw.start()

    def _probe_done(self, r):
        self.btn_probe.setEnabled(True)
        if not r.get("ok"):
            self.lb_ai.setText("探活失败：%s" % r.get("error"))
            return
        bad = [x for x in r["rows"] if not x["ok"]]
        for x in r["rows"]:
            for i in range(self.tb_ai.rowCount()):
                it = self.tb_ai.item(i, 0)
                if it and it.text() == (x.get("label") or x.get("model")):
                    c = QTableWidgetItem(("★ 当前 · " if x.get("active") else "") +
                                         ("可用 %ss" % x["secs"] if x["ok"]
                                          else "不可用：%s" % (x.get("why") or "")[:28]))
                    c.setForeground(Qt.GlobalColor.darkGreen if x["ok"] else Qt.GlobalColor.red)
                    self.tb_ai.setItem(i, 2, c)
        self.lb_ai.setText("探活完成：可用 %d/%d%s" % (
            r["usable"], r["total"],
            ("；不可用 %d 个（双击换到别的）" % len(bad)) if bad else ""))
        if self.win and hasattr(self.win, "log"):
            self.win.log("AI 池探活：可用 %d/%d" % (r["usable"], r["total"]))

    def _use_selected_model(self, *a):
        i = self.tb_ai.currentRow()
        if i < 0:
            return
        it = self.tb_ai.item(i, 0)
        idx = it.data(Qt.ItemDataRole.UserRole) if it else None
        r = bridge.ai_set_active(int(idx or 0))
        if self.win and hasattr(self.win, "log"):
            self.win.log("切换 AI 模型：%s" % (
                "成功 → %s" % ((r.get("active") or {}).get("model")) if r.get("ok")
                else "失败（%s）" % r.get("error")))
        self._refresh_ai()

    def _remove_model(self):
        i = self.tb_ai.currentRow()
        if i < 0:
            return
        it = self.tb_ai.item(i, 0)
        pidx = it.data(Qt.ItemDataRole.UserRole + 1) if it else -1
        if pidx is None or int(pidx) < 0:
            self.lb_ai.setText("这一条是角色自身配置，不能从池里删（改了它等于改了默认）")
            return
        r = bridge.ai_pool_remove(int(pidx))
        if self.win and hasattr(self.win, "log"):
            self.win.log("从池里删除 %s：%s" % (r.get("removed") or "", "成功" if r.get("ok")
                                                else r.get("error")))
        self._refresh_ai()

    def _add_model(self):
        r = bridge.ai_pool_add(label=self.ed_ai_model.text().strip(),
                               base=self.ed_ai_base.text().strip(),
                               model=self.ed_ai_model.text().strip(),
                               api_key=self.ed_ai_key.text().strip())
        if self.win and hasattr(self.win, "log"):
            self.win.log("加入 AI 池：%s" % ("成功（%s）" % self.ed_ai_model.text().strip()
                                            if r.get("ok") else "失败（%s）" % r.get("error")))
        if r.get("ok"):
            self.ed_ai_key.clear()
        self._refresh_ai()

    # ---------------- 判定提示词 + 点评 ----------------
    def _refresh_rules(self):
        r = bridge.ai_rules_get()
        if not r.get("ok"):
            self.lb_rules.setText("读不到判定提示词：%s" % r.get("error"))
            return
        self.tx_rules.setPlainText(r.get("text") or "")
        self.lb_rules.setText("来源：%s ｜ %d 字 ｜ 上次保存：%s" % (
            "手写（界面保存过）" if not r.get("using_default") else "由选品配置自动生成",
            int(r.get("chars") or 0), r.get("saved_at") or "—"))
        self.lb_rules.setStyleSheet(
            "font-size:11px;color:%s;" % (theme.SUCCESS if not r.get("using_default")
                                          else theme.TEXT_SUB))
        self._refresh_fb()

    def _save_rules(self):
        t = self.tx_rules.toPlainText().strip()
        if not t:
            self.lb_rules.setText("内容为空 —— 空着等于用自动生成的那份；"
                                  "要恢复自动生成请点「恢复默认」")
            return
        r = bridge.ai_rules_set(t)
        self.log_do("保存判定提示词：%s" % ("成功（%d 字，下一件就生效）" % r.get("chars")
                                            if r.get("ok") else "失败（%s）" % r.get("error")))
        self._refresh_rules()

    def _reset_rules(self):
        r = bridge.ai_rules_reset()
        self.log_do("判定提示词恢复默认：%s" % ("成功" if r.get("ok") else r.get("error")))
        self._refresh_rules()

    def _refresh_fb(self):
        r = bridge.ai_feedback_list()
        items = r.get("items") or []
        self.tb_fb.setRowCount(len(items))
        for i, x in enumerate(items):
            it = QTableWidgetItem(str(x.get("at") or "")[5:16])
            it.setData(Qt.ItemDataRole.UserRole, x.get("idx"))
            self.tb_fb.setItem(i, 0, it)
            self.tb_fb.setItem(i, 1, QTableWidgetItem("#%s %s" % (
                x.get("pid"), str(x.get("title") or "")[:14])))
            c = QTableWidgetItem(str(x.get("comment") or ""))
            c.setToolTip("当时 AI 判成：%s\nAI 原话：%s" % (x.get("ai_verdict") or "-",
                                                          x.get("ai_comment") or ""))
            self.tb_fb.setItem(i, 2, c)
        if not items:
            self.tb_fb.setRowCount(1)
            self.tb_fb.setItem(0, 0, QTableWidgetItem("还没有点评 —— 在 3.0 表格里右键商品 →「✍ 点评这个结果」"))
            self.tb_fb.setSpan(0, 0, 1, 3)

    def _apply_fb(self):
        r = bridge.ai_feedback_apply()
        self.log_do("把点评合并进提示词：%s" % (
            "成功（新增 %s 条口径）" % r.get("added") if r.get("ok")
            else r.get("error")))
        self._refresh_rules()

    def _refine_fb(self):
        if not (bridge.ai_feedback_list().get("items") or []):
            self.lb_rules.setText("还没有点评，先右键商品点评一两条再来提炼")
            return
        self.btn_refine.setEnabled(False)
        self.lb_rules.setText("正在让模型把点评归纳成规则（几十秒）…")
        self._rfw = _FnWorker(lambda: bridge.ai_feedback_refine(120), self)
        self._rfw.done.connect(self._refined)
        self._rfw.start()

    def _refined(self, r):
        self.btn_refine.setEnabled(True)
        if not r.get("ok"):
            self.lb_rules.setText("提炼失败：%s" % r.get("error"))
            return
        self.tx_rules.setPlainText(r.get("text") or "")
        self.lb_rules.setText("模型提炼好了（%s 条点评 → 新规则，%ss）。"
                              "**看过没问题再点「保存提示词」**，没保存前不生效。"
                              % (r.get("from"), r.get("secs")))
        self.log_do("AI 提炼规则完成（%ss），待你确认后保存" % r.get("secs"))

    def _del_fb(self):
        i = self.tb_fb.currentRow()
        if i < 0:
            return
        it = self.tb_fb.item(i, 0)
        idx = it.data(Qt.ItemDataRole.UserRole) if it else None
        if idx is None:
            return
        r = bridge.ai_feedback_del(int(idx))
        self.log_do("删除点评：%s" % ("成功" if r.get("ok") else r.get("error")))
        self._refresh_fb()

    def log_do(self, s: str):
        if self.win and hasattr(self.win, "log"):
            self.win.log(s)

    def _restore(self, word: str):
        r = bridge.v3_keyword_unwaive(word)
        if self.win and hasattr(self.win, "log"):
            self.win.log("还原豁免「%s」：%s" % (word, "成功" if r.get("ok") else r.get("error")))
        self._refresh_waivers()
