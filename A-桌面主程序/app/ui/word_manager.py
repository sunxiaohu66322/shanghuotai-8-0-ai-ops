# -*- coding: utf-8 -*-
"""品牌词管理块 —— 第一步初筛 / 第三步精筛 各挂一个，改的是同一份文件。

主人原话（2026-10-24）：
    「这个调整关键词的、调整筛选词的，随时都需要。第一步和第三步再加一个功能块，
      随时可以调整这些词：能把词从品牌命中里清掉，也可以添加。比如说石福康，
      我看到过的，它也是品牌，这时候我就要添加这个词。」

为什么做成独立控件：两步必须**同一份词表、同一个入口**，否则又会出现
「第一步说违规、第三步说没事」那种自相矛盾（10-24 已经踩过一次，见词库统一那轮）。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from .. import bridge, theme


class WordManager(QGroupBox):
    """加词 / 清词 / 查词 / 恢复。改完发 changed 信号，页面自己决定要不要重判。

    ★ 2026-10-25（P0-4）：新增 `vertical=True` 竖版排布。
      主人已多次要求「品牌词管理和敏感词管理放在页面右侧的竖向独立面板，
      不得横向放在页面顶部」—— 横版控件塞进窄栏会把输入框压到看不见，
      所以给同一个控件做一套竖排：标签在上、输入框占满宽、按钮一行两个、表格撑满剩余高度。
    """

    changed = pyqtSignal(str)          # "add" / "remove" / "restore"

    def __init__(self, on_changed=None, parent=None, vertical: bool = False):
        super().__init__("品牌词管理 —— 随时可加、可清，改完下一次判定立刻按新词表", parent)
        self.vertical = bool(vertical)
        self._rows: list[dict] = []
        self._on_changed = on_changed
        if self.vertical:
            self.setTitle("品牌词管理")
        self._build()
        self.refresh_stats()

    # ---------------- UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8 if self.vertical else 8, 6, 8, 6)
        v.setSpacing(4)

        tip = QLabel("原始词表（1.9 万条品牌收录表）是共享文件，本块**不改它**，"
                     "只在你身上盖一层人工裁决：清掉的词以后不算品牌，加进来的词以后一定算品牌。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s; font-size:11px;" % theme.TEXT_SUB)
        if self.vertical:
            tip.setText("原始 1.9 万条品牌表是共享文件，本块不改它，只盖一层人工裁决。")
        v.addWidget(tip)

        # 输入 + 加/清
        if self.vertical:
            v.addWidget(QLabel("词（逗号分隔可多个）"))
            self.ed_word = QLineEdit()
            self.ed_word.setPlaceholderText("石福康，XX")
            self.ed_word.setMinimumWidth(120)
            v.addWidget(self.ed_word)
            r1 = QHBoxLayout()
            r1.setSpacing(4)
            self.btn_add = QPushButton("＋ 加为品牌")
            self.btn_add.setToolTip("加进来的词，标题/店铺名里出现就按品牌保护风险处理")
            self.btn_add.clicked.connect(self._add)
            r1.addWidget(self.btn_add)
            self.btn_del = QPushButton("－ 清出品牌")
            self.btn_del.setToolTip("清掉的词以后不再算品牌命中（如「徒步」这种运动词）")
            self.btn_del.clicked.connect(self._remove)
            r1.addWidget(self.btn_del)
            v.addLayout(r1)
        else:
            r1 = QHBoxLayout()
            r1.setSpacing(6)
            r1.addWidget(QLabel("词"))
            self.ed_word = QLineEdit()
            self.ed_word.setPlaceholderText("一个词，或逗号分隔多个：石福康，XX")
            self.ed_word.setMinimumWidth(240)
            r1.addWidget(self.ed_word, 1)
            self.btn_add = QPushButton("＋ 加为品牌")
            self.btn_add.setToolTip("加进来的词，标题/店铺名里出现就按品牌保护风险处理")
            self.btn_add.clicked.connect(self._add)
            r1.addWidget(self.btn_add)
            self.btn_del = QPushButton("－ 清出品牌（这个不算品牌）")
            self.btn_del.setToolTip("清掉的词以后不再算品牌命中（如「徒步」这种运动词）")
            self.btn_del.clicked.connect(self._remove)
            r1.addWidget(self.btn_del)
            v.addLayout(r1)

        # 查询 + 恢复
        if self.vertical:
            self.ed_q = QLineEdit()
            self.ed_q.setPlaceholderText("查词表：徒步 / 石福 / TCL")
            self.ed_q.returnPressed.connect(self._search)
            v.addWidget(self.ed_q)
            r2 = QHBoxLayout()
            r2.setSpacing(4)
            b = QPushButton("查询")
            b.clicked.connect(self._search)
            r2.addWidget(b)
            self.btn_restore = QPushButton("恢复选中词")
            self.btn_restore.setToolTip("撤销人工裁决：加过的撤掉、清过的恢复")
            self.btn_restore.clicked.connect(self._restore)
            r2.addWidget(self.btn_restore)
            v.addLayout(r2)
        else:
            r2 = QHBoxLayout()
            r2.setSpacing(6)
            r2.addWidget(QLabel("查"))
            self.ed_q = QLineEdit()
            self.ed_q.setPlaceholderText("输入片段查词表里有没有：徒步 / 石福 / TCL")
            self.ed_q.returnPressed.connect(self._search)
            r2.addWidget(self.ed_q, 1)
            b = QPushButton("查询")
            b.clicked.connect(self._search)
            r2.addWidget(b)
            self.btn_restore = QPushButton("恢复选中词")
            self.btn_restore.setToolTip("撤销人工裁决：加过的撤掉、清过的恢复")
            self.btn_restore.clicked.connect(self._restore)
            r2.addWidget(self.btn_restore)
            v.addLayout(r2)

        # 表
        self.tb = QTableWidget(0, 3)
        self.tb.setHorizontalHeaderLabels(["词", "来源", "当前状态"])
        self.tb.verticalHeader().setVisible(False)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        if self.vertical:
            self.tb.setMinimumHeight(120)
            v.addWidget(self.tb, 1)          # 竖版：表格吃掉剩余高度
        else:
            self.tb.setMaximumHeight(150)
            v.addWidget(self.tb)
        h = self.tb.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tb.setColumnWidth(1, 72 if self.vertical else 80)
        self.tb.setColumnWidth(2, 84 if self.vertical else 90)

        self.lb_stat = QLabel("—")
        self.lb_stat.setWordWrap(True)
        self.lb_stat.setStyleSheet("color:%s; font-size:11px;" % theme.TEXT_SUB)
        v.addWidget(self.lb_stat)

    # ---------------- 数据
    @staticmethod
    def _split(text: str) -> list:
        out = []
        for part in (text or "").replace("，", ",").replace("；", ",").replace(";", ",").split(","):
            w = part.strip()
            if w and w not in out:
                out.append(w)
        return out

    def refresh_stats(self):
        d = bridge.brand_words_stats()
        if not d.get("ok"):
            self.lb_stat.setText("词表读不出来：%s" % (d.get("error") or "未知"))
            return
        self.lb_stat.setText(
            "收录表 %d 条 · 我加的 %d 条 · 我清掉的 %d 条　|　%s"
            % (d.get("registry", 0), d.get("added", 0), d.get("removed", 0),
               d.get("file") or ""))
        return d

    def _fill(self, items: list):
        self._rows = items
        self.tb.setRowCount(len(items))
        for r, it in enumerate(items):
            w = QTableWidgetItem(it.get("word") or "")
            src = QTableWidgetItem(it.get("source") or "")
            st = "算品牌" if (it.get("status") or "on") == "on" else "不算品牌"
            sti = QTableWidgetItem(st)
            sti.setForeground(QColor(theme.SUCCESS if st == "算品牌" else theme.DANGER))
            for c, x in ((0, w), (1, src), (2, sti)):
                self.tb.setItem(r, c, x)

    def _search(self):
        q = self.ed_q.text().strip()
        if not q:
            return
        d = bridge.brand_words_search(q, limit=80)
        if not d.get("ok"):
            self.lb_stat.setText("查询失败：%s" % (d.get("error") or "未知"))
            return
        self._fill(d.get("items") or [])
        self.lb_stat.setText("查到 %d 条（最多显示 80）。要清掉某条，选中它点「－ 清出品牌」"
                             % d.get("total", 0))

    def _selected_words(self) -> list:
        rows = sorted({i.row() for i in self.tb.selectedIndexes()})
        return [self._rows[r]["word"] for r in rows if 0 <= r < len(self._rows)]

    def _add(self):
        words = self._split(self.ed_word.text())
        if not words:
            return
        d = bridge.brand_words_add(words)
        if not d.get("ok"):
            self.lb_stat.setText("加词失败：%s" % (d.get("error") or "未知"))
            return
        self.ed_word.clear()
        self.refresh_stats()
        self._fill([{"word": w, "source": "我加的", "status": "on"}
                    for w in (d.get("added") or [])] or [{"word": w, "source": "我加的",
                                                          "status": "on"} for w in words])
        if self._on_changed:
            self._on_changed("add", words)
        self.changed.emit("add")

    def _remove(self):
        words = self._split(self.ed_word.text()) or self._selected_words()
        if not words:
            self.lb_stat.setText("先在输入框填词，或先在表里选中一行")
            return
        d = bridge.brand_words_remove(words)
        if not d.get("ok"):
            self.lb_stat.setText("清词失败：%s" % (d.get("error") or "未知"))
            return
        self.ed_word.clear()
        self.refresh_stats()
        self._fill([{"word": w, "source": "收录表", "status": "off"} for w in words])
        if self._on_changed:
            self._on_changed("remove", words)
        self.changed.emit("remove")

    def _restore(self):
        words = self._selected_words() or self._split(self.ed_word.text())
        if not words:
            self.lb_stat.setText("先在表里选中要恢复的词")
            return
        done = []
        for w in words:
            d = bridge.brand_words_restore(w)
            if d.get("ok") and d.get("found"):
                done.append(w)
        self.refresh_stats()
        self.lb_stat.setText("已恢复：%s" % ("、".join(done) if done else "（这些词本来没有人工裁决）"))
        if self._on_changed:
            self._on_changed("restore", done)
        self.changed.emit("restore")


# ============================================================
#  敏感词 / 违规词管理（3.0 右侧竖栏第二块，2026-10-25 新增）
# ============================================================
KIND_CN = {"violation": "违规词", "marketing": "营销词", "hype": "夸张词"}
STATE_CN = {"active": "生效中", "disabled": "已停用/已豁免"}
KIND_SRC = {"db:v3_keyword": "词库主表", "db:打码词": "打码词表",
            "human": "人工添加", "": ""}


class SensitiveWordManager(QGroupBox):
    """违规 / 营销 / 夸张词：查、停用（豁免）、恢复，并显示来源与生效状态。

    与品牌词**完全分开**（P0-4 §6.3）：品牌词走 brand_lexicon 那份人工裁决，
    这里走 v3_keyword 的 kind=violation/marketing/hype + v3_keyword_waiver 台账。
    两块面板各自读写自己那套，绝不把普通品牌词当违规词，反之亦然。
    """

    changed = pyqtSignal(str)

    def __init__(self, on_changed=None, parent=None, vertical: bool = True):
        super().__init__("敏感词 / 违规词管理", parent)
        self._rows: list[dict] = []
        self._on_changed = on_changed
        self.vertical = bool(vertical)
        self._build()
        self.refresh_stats()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)
        tip = QLabel("违规 / 营销 / 夸张词。停用 = 以后不算违规（可恢复），原始词表文件不改。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s; font-size:11px;" % theme.TEXT_SUB)
        v.addWidget(tip)

        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("查词：夸大 / 顶级 / 医疗")
        self.ed_q.returnPressed.connect(self._search)
        v.addWidget(self.ed_q)

        r = QHBoxLayout()
        r.setSpacing(4)
        b = QPushButton("查询")
        b.clicked.connect(self._search)
        r.addWidget(b)
        b_off = QPushButton("停用选中词")
        b_off.setToolTip("判成「不算违规」，以后精筛与打码都不再命中（可恢复）")
        b_off.clicked.connect(self._disable)
        r.addWidget(b_off)
        b_on = QPushButton("恢复")
        b_on.setToolTip("撤销停用，恢复命中")
        b_on.clicked.connect(self._enable)
        r.addWidget(b_on)
        v.addLayout(r)

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["词", "规则", "来源", "状态"])
        self.tb.verticalHeader().setVisible(False)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tb.setMinimumHeight(110)
        v.addWidget(self.tb, 1)
        h = self.tb.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
        self.tb.setColumnWidth(1, 62)
        self.tb.setColumnWidth(2, 66)
        self.tb.setColumnWidth(3, 86)

        self.lb_stat = QLabel("—")
        self.lb_stat.setWordWrap(True)
        self.lb_stat.setStyleSheet("color:%s; font-size:11px;" % theme.TEXT_SUB)
        v.addWidget(self.lb_stat)

    # ---------- 数据
    def refresh_stats(self):
        d = bridge.v3_word_stats()
        if not d.get("ok"):
            self.lb_stat.setText("词库读不出来：%s" % (d.get("error") or "未知"))
            return
        parts = []
        for k in ("violation", "marketing", "hype"):
            x = d.get(k) or {}
            if x:
                parts.append("%s 生效 %d / 停用 %d"
                             % (KIND_CN.get(k, k), x.get("active", 0), x.get("disabled", 0)))
        self.lb_stat.setText(" · ".join(parts) + "　|　已停用台账 %d 条" % (d.get("waived") or 0))

    def _fill(self, items: list):
        self._rows = items
        self.tb.setRowCount(len(items))
        for r, it in enumerate(items):
            kind = str(it.get("kind") or "")
            st = str(it.get("status") or "active")
            src = str(it.get("source") or "")
            cells = [
                QTableWidgetItem(str(it.get("text") or "")),
                QTableWidgetItem(KIND_CN.get(kind, kind)),
                QTableWidgetItem(KIND_SRC.get(src, src[:10])),
                QTableWidgetItem(STATE_CN.get(st, st)),
            ]
            cells[3].setForeground(QColor(theme.SUCCESS if st == "active" else theme.DANGER))
            for c, x in enumerate(cells):
                self.tb.setItem(r, c, x)

    def _search(self):
        q = self.ed_q.text().strip()
        d = bridge.v3_word_search(q, limit=100)
        if not d.get("ok"):
            self.lb_stat.setText("查询失败：%s" % (d.get("error") or "未知"))
            return
        self._fill(d.get("items") or [])
        self.lb_stat.setText("查到 %d 条（最多 100，共 %s）"
                             % (len(d.get("items") or []), d.get("total")))

    def _sel_words(self) -> list:
        rows = sorted({i.row() for i in self.tb.selectedIndexes()})
        return [self._rows[r]["text"] for r in rows if 0 <= r < len(self._rows)]

    def _disable(self):
        words = self._sel_words() or ([self.ed_q.text().strip()] if self.ed_q.text().strip() else [])
        if not words:
            self.lb_stat.setText("先在表里选中词，或在上面输入一个词")
            return
        d = bridge.v3_word_waive(words, reason="3.0 人工停用")
        if not d.get("ok"):
            self.lb_stat.setText("停用失败：%s" % (d.get("error") or "未知"))
            return
        self.refresh_stats()
        self._search()
        miss = d.get("not_in_dict") or []
        self.lb_stat.setText("已停用 %s 个%s"
                             % (len(d.get("waived") or []),
                                ("；不在词库：" + "、".join(miss)) if miss else ""))
        if self._on_changed:
            self._on_changed("waive", d.get("waived") or [])
        self.changed.emit("waive")

    def _enable(self):
        words = self._sel_words()
        if not words:
            self.lb_stat.setText("先在表里选中要恢复的词")
            return
        done, fail = [], []
        for w in words:
            d = bridge.v3_keyword_unwaive(w)
            (done if d.get("ok") else fail).append(w)
        self.refresh_stats()
        self._search()
        self.lb_stat.setText("已恢复 %d 个%s"
                             % (len(done), ("；失败：" + "、".join(fail)) if fail else ""))
        if self._on_changed:
            self._on_changed("unwaive", done)
        self.changed.emit("unwaive")
