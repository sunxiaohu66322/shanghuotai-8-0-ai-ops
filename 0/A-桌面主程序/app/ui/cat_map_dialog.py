# -*- coding: utf-8 -*-
"""4.2 · 类目映射对话框（D-35 的人工入口）

为什么要有它：源数据（微店/微信小店卡）**只给类目 ID 链**（如 `6932/7117/546488`），
**没有任何中文类目名**。平台表格要的是中文分类，所以必须有一步人工映射 ——
但不许让用户去改数据库，也不许程序按标题猜一个。

用法：左边选来源类目 → 右边填微信 / 抖店的目标类目名 → 保存 → 「应用到商品」。
`target_name` 清空即删除这条映射。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .. import bridge


class CatMapDialog(QDialog):
    def __init__(self, parent=None, pids: list | None = None):
        super().__init__(parent)
        self.setWindowTitle("4.2 · 类目映射（来源类目 → 平台类目）")
        self.resize(880, 560)
        self._pids = [int(x) for x in (pids or [])]
        self._groups: list[dict] = []
        self._cur: dict | None = None

        root = QVBoxLayout(self)
        tip = QLabel(
            "来源卡里只有类目 ID 链，没有中文名。这里把每个来源类目对到一个平台类目名，"
            "之后所有落在同一类目的商品会自动套用。\n"
            "空着就表示「还没映射」——导出时会被点名拦下，不会被塞一个默认分类。")
        tip.setWordWrap(True)
        root.addWidget(tip)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("来源类目（按商品数排序）"))
        self.lst = QListWidget()
        self.lst.currentItemChanged.connect(self._pick)
        left.addWidget(self.lst, 1)
        self.lb_stat = QLabel("")
        left.addWidget(self.lb_stat)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        self.lb_sel = QLabel("← 先选一个来源类目")
        self.lb_sel.setWordWrap(True)
        right.addWidget(self.lb_sel)

        right.addWidget(QLabel("微信小店类目名"))
        self.ed_wx = QLineEdit()
        self.ed_wx.setPlaceholderText("例：运动户外 > 健身器材 > 跑步机")
        right.addWidget(self.ed_wx)

        right.addWidget(QLabel("抖店类目名"))
        self.ed_dd = QLineEdit()
        self.ed_dd.setPlaceholderText("例：运动户外/健身训练/健身器材")
        right.addWidget(self.ed_dd)

        right.addWidget(QLabel("备注（可选）"))
        self.ed_note = QLineEdit()
        right.addWidget(self.ed_note)

        row = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存这两个映射")
        self.btn_save.setObjectName("Primary")
        self.btn_save.clicked.connect(self._save)
        row.addWidget(self.btn_save)
        self.btn_clear = QPushButton("清空本组映射")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        right.addLayout(row)

        right.addSpacing(10)
        right.addWidget(QLabel("映射保存后，把结果写进商品（导出读的就是这个字段）"))
        row2 = QHBoxLayout()
        self.btn_apply_wx = QPushButton("应用到商品（微信）")
        self.btn_apply_wx.clicked.connect(lambda: self._apply("wechat"))
        row2.addWidget(self.btn_apply_wx)
        self.btn_apply_dd = QPushButton("应用到商品（抖店）")
        self.btn_apply_dd.clicked.connect(lambda: self._apply("doudian"))
        row2.addWidget(self.btn_apply_dd)
        right.addLayout(row2)

        right.addSpacing(10)
        self.btn_check = QPushButton("🔍 导出前校验（谁还缺类目）")
        self.btn_check.clicked.connect(self._check)
        right.addWidget(self.btn_check)
        self.lb_check = QLabel("")
        self.lb_check.setWordWrap(True)
        right.addWidget(self.lb_check)
        right.addStretch(1)
        body.addLayout(right, 4)
        root.addLayout(body, 1)

        hl = QHBoxLayout()
        hl.addStretch(1)
        b = QPushButton("关闭")
        b.clicked.connect(self.accept)
        hl.addWidget(b)
        root.addLayout(hl)

        self.reload()

    # ------------------------------------------------------------ 数据
    def reload(self):
        r = bridge.cat_sources()
        if not r.get("ok"):
            QMessageBox.warning(self, "读不到类目", str(r.get("error"))[:300])
            return
        self._groups = r.get("groups") or []
        self.lst.clear()
        for g in self._groups:
            wx = g["mapped"].get("wechat") or ""
            dd = g["mapped"].get("doudian") or ""
            mark = "✓" if g.get("complete") else ("△" if (wx or dd) else "✗")
            label = "%s  %s   商品 %d 件   %s\n       微信：%s ｜ 抖店：%s" % (
                mark, g["source_cat_id"], g["count"], g["source_path"],
                wx or "（未映射）", dd or "（未映射）")
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, g)
            self.lst.addItem(it)
        self.lb_stat.setText("来源类目 %d 组 · 涉及商品 %d 件 · 两组都齐的 %d 组"
                             % (r.get("group_count"), r.get("products"),
                                r.get("complete")))
        if self.lst.count():
            self.lst.setCurrentRow(0)

    def _pick(self, cur, _prev=None):
        if cur is None:
            self._cur = None
            return
        g = cur.data(Qt.ItemDataRole.UserRole) or {}
        self._cur = g
        self.lb_sel.setText("来源类目：%s\nID 链：%s\n涉及商品：%s"
                            % (g.get("source_cat_id"), g.get("source_path") or "—",
                               ", ".join(str(x) for x in (g.get("product_ids") or []))))
        self.ed_wx.setText(g["mapped"].get("wechat") or "")
        self.ed_dd.setText(g["mapped"].get("doudian") or "")
        self.ed_note.setText("")

    # ------------------------------------------------------------ 动作
    def _save(self):
        if not self._cur:
            QMessageBox.information(self, "先选类目", "左边先点一个来源类目。")
            return
        sid = self._cur["source_cat_id"]
        note = self.ed_note.text().strip()
        okn = 0
        for plat, ed in (("wechat", self.ed_wx), ("doudian", self.ed_dd)):
            val = ed.text().strip()
            r = bridge.cat_set(sid, plat, val, "")
            if r.get("ok"):
                okn += 1
            else:
                QMessageBox.warning(self, "保存失败", "%s: %s" % (plat, r.get("error"))[:300])
                return
        if parent_log := getattr(self.parent(), "log", None):
            try:
                parent_log("类目映射已保存：%s（%d 项）" % (sid, okn))
            except Exception:      # noqa: BLE001
                pass
        self.reload()

    def _clear(self):
        if not self._cur:
            return
        sid = self._cur["source_cat_id"]
        for plat in ("wechat", "doudian"):
            bridge.cat_set(sid, plat, "", "")
        self.ed_wx.clear()
        self.ed_dd.clear()
        self.reload()

    def _apply(self, platform: str):
        r = bridge.cat_apply(platform, dry_run=False)
        if not r.get("ok"):
            QMessageBox.warning(self, "应用失败", str(r.get("error"))[:300])
            return
        msg = ("已应用（%s）：%d 件商品写上类目；仍有 %d 件没有映射"
               % (platform, r.get("applied_products"), r.get("unmapped_products")))
        self.lb_check.setText(msg)
        QMessageBox.information(self, "应用完成", msg)
        self.reload()

    def _check(self):
        r = bridge.cat_validate(self._pids or None)
        if not r.get("ok"):
            QMessageBox.warning(self, "校验失败", str(r.get("error"))[:300])
            return
        if not r.get("missing"):
            self.lb_check.setText("✓ 全部商品都有平台类目")
            return
        names = "、".join("%s" % x["product_id"] for x in r["rows"][:12])
        self.lb_check.setText("✗ %d 件缺类目：%s%s"
                              % (r["missing"], names,
                                 "…" if r["missing"] > 12 else ""))
        QMessageBox.information(
            self, "还缺类目",
            "%d 件商品没有目标平台类目。\n\n它们会被导出检查报告点名，"
            "不会被标成「可直接上架」。\n\n涉及：%s" % (r["missing"], names))
