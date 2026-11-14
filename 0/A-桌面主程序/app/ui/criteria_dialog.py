# -*- coding: utf-8 -*-
"""选品条件编辑器（2026-10-28 主人要求）

主人原话：
  「现在的商品精筛根本就没有相应的条件，就相当于是提示词都没有，它就随随便便筛的，
    你起码要有个依据吧…把这些因素都做成一个设置页放进去，方便后期变更。
    这样即便是后期换了 API，他只要通过这道提示词知道我们选品的条件了，
    也可以很自然地把需要的品合适的品选出来。」

这一页做三件事：
  ① 按分区改条件（生意模型/买家画像/价位/品类/品牌/质量风险/行情/打码/判定口径）
  ② 右边实时显示「AI 会收到的提示词」—— 改一个数，提示词立刻跟着变（不是手写长文）
  ③ 拿一件真货试判：本地闸门当场告诉你拦不拦、要不要打码、分数多少（不花 token）

为什么不让你手写整段提示词：手写会和条件脱节。条件是真源，提示词是它的投影 ——
换模型/换 API 时，只要条件在，任何模型都能按同一套口径判。
"""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from .. import bridge, theme

# 分区：显示名 → 配置键（顺序就是人的思路顺序：先定生意，再定人，再定价，再定货）
SECTIONS = [
    ("① 生意模型", "business"),
    ("② 买家画像（人群词/及格线）", "audience"),
    ("③ 价位区间", "price"),
    ("④ 品类（优先/排除/软排除）", "category"),
    ("⑤ 品牌与店铺类型", "brand"),
    ("⑥ 质量风险词", "quality_risk"),
    ("⑦ 行情与上架价值", "market"),
    ("⑧ 打码需求", "mask"),
    ("⑨ 判定口径与权重", "verdict"),
    ("⑩ 店长补充要求", "ai_prompt"),
]


class CriteriaDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选品条件（改这里 = 改 AI 的判定依据）")
        self.resize(1180, 720)
        self.data: dict = {}
        self.cur_key = ""
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.lb_head = QLabel("读取中…")
        self.lb_head.setWordWrap(True)
        self.lb_head.setStyleSheet("font-size:12px;")
        root.addWidget(self.lb_head)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        # ---- 左：分区 + 编辑 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.cb = QComboBox()
        for name, key in SECTIONS:
            self.cb.addItem(name, key)
        self.cb.currentIndexChanged.connect(self._switch)
        lv.addWidget(self.cb)

        self.tx = QPlainTextEdit()
        self.tx.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        self.tx.setToolTip("这一段就是配置文件里的原文（JSON）。\n"
                           "改完点「保存条件」立即生效 —— 下一件精筛就按新口径判，不用重启。\n"
                           "带下划线 _ 开头的键是说明文字，可以留着，不影响判定。")
        lv.addWidget(self.tx, 1)
        self.lb_err = QLabel("")
        self.lb_err.setWordWrap(True)
        self.lb_err.setStyleSheet("font-size:11px;color:%s;" % theme.DANGER)
        lv.addWidget(self.lb_err)
        split.addWidget(left)

        # ---- 右：提示词预览 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("AI 会收到的提示词（只看不改 · 随左边改动实时更新）"))
        self.tx_prompt = QPlainTextEdit()
        self.tx_prompt.setReadOnly(True)
        self.tx_prompt.setStyleSheet("font-size:12px;background:#FAFAFA;")
        rv.addWidget(self.tx_prompt, 1)
        split.addWidget(right)
        split.setSizes([560, 600])

        # ---- 底部：试判 ----
        g = QGroupBox("试判一件（本地闸门，不花钱）")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("商品标题，例如：户外折叠露营桌 免安装 加厚 承重100kg")
        row.addWidget(self.ed_title, 3)
        self.ed_price = QLineEdit()
        self.ed_price.setPlaceholderText("价格")
        self.ed_price.setMaximumWidth(90)
        row.addWidget(self.ed_price, 1)
        f.addRow("标题/价格", row)
        row2 = QHBoxLayout()
        self.ed_shop = QLineEdit()
        self.ed_shop.setPlaceholderText("店铺名（含『旗舰店』会直接拦）")
        row2.addWidget(self.ed_shop, 2)
        self.ed_ocr = QLineEdit()
        self.ed_ocr.setPlaceholderText("图上读到的字（OCR）")
        row2.addWidget(self.ed_ocr, 3)
        f.addRow("店铺/OCR", row2)
        bt = QPushButton("试判")
        bt.clicked.connect(self._try)
        f.addRow("", bt)
        self.lb_try = QLabel("—")
        self.lb_try.setWordWrap(True)
        self.lb_try.setStyleSheet("font-size:12px;")
        f.addRow("结果", self.lb_try)
        root.addWidget(g)

        # ---- 按钮 ----
        bar = QHBoxLayout()
        b_save = QPushButton("💾 保存条件")
        b_save.setDefault(True)
        b_save.clicked.connect(self._save)
        bar.addWidget(b_save)
        b_reset = QPushButton("↺ 还原默认")
        b_reset.clicked.connect(self._reset)
        bar.addWidget(b_reset)
        self.lb_status = QLabel("")
        self.lb_status.setWordWrap(True)
        self.lb_status.setStyleSheet("font-size:11px;")
        bar.addWidget(self.lb_status, 1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_close)
        root.addLayout(bar)

        self._load()

    # ---------------------------------------------------------------- 读 / 写
    def _load(self):
        r = bridge.criteria_get()
        if not r.get("ok"):
            self.lb_head.setText("读不到选品条件：%s" % (r.get("error") or ""))
            self.data = {}
            return
        self.data = r.get("data") or {}
        meta = self.data.get("meta") or {}
        self.lb_head.setText(
            "真源：%s　｜　版本 v%s（%s）"
            % (r.get("path"), meta.get("version"), meta.get("updated")))
        self._load_section()

    def _load_section(self):
        key = self.cb.currentData()
        self.cur_key = key
        val = self.data.get(key)
        self.tx.setPlainText(json.dumps(val, ensure_ascii=False, indent=2))
        self.lb_err.setText("")
        self._refresh_prompt()

    def _commit_section(self) -> bool:
        """把编辑框内容解析回 self.data。语法错 → 返回 False 并提示行号。"""
        key = self.cur_key
        if not key:
            return True
        raw = self.tx.toPlainText().strip()
        if not raw:
            self.data[key] = {}
            return True
        try:
            self.data[key] = json.loads(raw)
            self.lb_err.setText("")
            return True
        except json.JSONDecodeError as e:
            self.lb_err.setText("第 %d 行第 %d 列 JSON 有误：%s —— 先改对再切/保存"
                                % (e.lineno, e.colno, e.msg))
            return False

    def _switch(self, _idx=None):
        if not self._commit_section():
            return
        self._load_section()

    def _refresh_prompt(self):
        r = bridge.criteria_preview(self.data)
        if r.get("ok"):
            self.tx_prompt.setPlainText(str(r.get("text") or ""))
            self.lb_status.setText("提示词 %s 字（未保存前只是预览）" % r.get("chars"))
        else:
            self.tx_prompt.setPlainText("预览失败：%s" % (r.get("error") or ""))

    # ---------------------------------------------------------------- 动作
    def _save(self):
        if not self._commit_section():
            return
        r = bridge.criteria_save(self.data, editor="界面·选品条件页")
        if not r.get("ok"):
            QMessageBox.warning(self, "保存失败", str(r.get("error") or ""))
            return
        self.data = bridge.criteria_get().get("data") or self.data
        self.lb_status.setText("✔ 已保存 v%s（%s）· 提示词 %s 字 · 下一件精筛即生效"
                               % (r.get("version"), r.get("updated"), r.get("prompt_chars")))
        self._refresh_prompt()

    def _reset(self):
        r = QMessageBox.question(
            self, "还原默认",
            "会把当前条件换成系统默认（价位 200–1500、45+ 微信人群、排除服装/生鲜/快消/母婴/数码）。\n"
            "当前文件会备份成 .bak-时间戳，可回退。继续？")
        if r != QMessageBox.StandardButton.Yes:
            return
        res = bridge.criteria_reset(confirm=True)
        if not res.get("ok"):
            QMessageBox.warning(self, "还原失败", str(res.get("error") or ""))
            return
        self._load()
        self.lb_status.setText("↺ 已还原默认 v%s" % res.get("version"))

    def _try(self):
        if not self._commit_section():
            return
        price = None
        try:
            price = float(self.ed_price.text().strip()) if self.ed_price.text().strip() else None
        except Exception:                              # noqa: BLE001
            price = None
        r = bridge.criteria_gate(title=self.ed_title.text().strip(), price=price,
                                 shop=self.ed_shop.text().strip(),
                                 ocr_text=self.ed_ocr.text().strip())
        if not r.get("ok"):
            self.lb_try.setText("试判失败：%s" % (r.get("error") or ""))
            return
        bits = ["判定：**%s**　综合分 %s　人群分 %s　需打码：%s"
                % ({"block": "拦住", "warn": "警告", "pass": "通过"}.get(r.get("verdict_hint"),
                                                                       r.get("verdict_hint")),
                   r.get("score"), r.get("audience_score"),
                   "是" if r.get("need_mask") else "否")]
        for b in (r.get("block") or []):
            bits.append("　⛔ " + str(b))
        for w in (r.get("warn") or [])[:5]:
            bits.append("　⚠ " + str(w))
        if r.get("mask_why"):
            bits.append("　打码线索：%s" % "、".join(r.get("mask_why") or []))
        self.lb_try.setText("<br>".join(bits))

    def closeEvent(self, e):                            # noqa: N802
        if not self._commit_section():
            e.ignore()
            return
        super().closeEvent(e)
