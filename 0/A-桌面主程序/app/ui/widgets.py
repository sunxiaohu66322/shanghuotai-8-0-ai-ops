# -*- coding: utf-8 -*-
"""可复用小组件 —— 统计卡、键值行、表格工厂、图片预览。

形状对齐小猪：统计卡是圆角浅底块（MakeStatCard），表格是浅表头 + 白底。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QColor, QFont
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import theme


class StatCard(QFrame):
    """小猪 MakeStatCard 的形状：一个数字 + 一行小字，浅色底。"""

    def __init__(self, label: str, value: str = "—", bg: str = theme.PRIMARY_LIGHT,
                 color: str = theme.PRIMARY, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(theme.stat_style(bg))
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(1)

        self._val = QLabel(value)
        self._val.setObjectName("StatValue")
        self._val.setStyleSheet(f"color: {color};")
        self._lbl = QLabel(label)
        self._lbl.setObjectName("StatLabel")
        lay.addWidget(self._val)
        lay.addWidget(self._lbl)

    def set_value(self, v, color: str | None = None):
        self._val.setText(str(v))
        if color:
            self._val.setStyleSheet(f"color: {color};")


class KV(QWidget):
    """一行「键：值」，值可选中复制。"""

    def __init__(self, key: str, value: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(6)
        k = QLabel(f"{key}")
        k.setObjectName("FieldLabel")
        k.setFixedWidth(72)
        self.v = QLabel(value or "—")
        self.v.setWordWrap(True)
        self.v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(k, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.v, 1)

    def set_value(self, text: str):
        self.v.setText(text or "—")


def make_table(headers: list[str], widths: list[int] | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(26)
    t.setShowGrid(False)
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    if widths:
        for i, w in enumerate(widths[:len(headers)]):
            t.setColumnWidth(i, w)
    return t


def clear_table(t: QTableWidget):
    t.setRowCount(0)


def fill_table(t: QTableWidget, rows: list[list], colors: dict[int, dict[str, str]] | None = None):
    """rows: 二维列表；colors: {row_index: {col_index: '#RRGGBB'}}"""
    t.setRowCount(0)
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            item = QTableWidgetItem("" if val is None else str(val))
            item.setToolTip(str(val) if val is not None else "")
            if colors and r in colors and c in colors[r]:
                item.setForeground(QColor(colors[r][c]))
            t.setItem(r, c, item)


class ImageBox(QLabel):
    """预览框：显示"未选中"或一张图；不会去网上拉图。"""

    def __init__(self, w=260, h=260, parent=None):
        super().__init__(parent)
        self._w, self._h = w, h
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(
            f"background:#FBFAFE; border:1px dashed {theme.BORDER};"
            f"border-radius:8px; color:{theme.TEXT_SUB}; font-size:12px;"
        )
        self.setText("未选中商品")

    def show_text(self, text: str):
        self.setPixmap(QPixmap())
        self.setText(text)

    def show_path(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            self.show_text(f"本地没有这张图\n{p.name}")
            return False
        pm = QPixmap(str(p))
        if pm.isNull():
            self.show_text("图片打不开")
            return False
        self.setText("")
        self.setPixmap(pm.scaled(
            QSize(self._w - 8, self._h - 8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        return True


class Hint(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
