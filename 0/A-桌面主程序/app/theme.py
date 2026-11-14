# -*- coding: utf-8 -*-
"""配色与全局样式 —— 直接取自小猪 PicDetector 原版 MainForm 的颜色常量。

来源（反编译源码，逐行读出来的，不是猜的）：
    J:\\aixitong\\小猪\\小猪鉴图\\decompiled_389\\PicDetector\\MainForm.cs
        CPrimary      = FromArgb(124, 58, 237)
        CPrimaryDark  = FromArgb(109, 40, 217)
        CPrimaryLight = FromArgb(245, 240, 255)
        CSuccess      = FromArgb(22, 163, 74)
        CText         = FromArgb(30, 30, 60)
        CTextSub      = FromArgb(120, 120, 150)
        CWarning      = FromArgb(234, 88, 12)
        CDanger       = FromArgb(220, 38, 38)
        CBlue         = FromArgb(59, 130, 246)
        页面底 / 输入框底 = FromArgb(250, 248, 255)
        边框            = FromArgb(210, 210, 225)
        表头 / 统计卡底  = FromArgb(245, 240, 255)
        MinimumSize      = 750 x 600
"""
from __future__ import annotations

# ---- 小猪原版色板 ----
PRIMARY = "#7C3AED"
PRIMARY_DARK = "#6D28D9"
PRIMARY_LIGHT = "#F5F0FF"
SUCCESS = "#16A34A"
SUCCESS_LIGHT = "#F0FDF4"
TEXT = "#1E1E3C"
TEXT_SUB = "#787896"
WARNING = "#EA580C"
WARNING_LIGHT = "#FFF7ED"
DANGER = "#DC2626"
DANGER_LIGHT = "#FFF5F5"
BLUE = "#3B82F6"
BLUE_LIGHT = "#F8F7FF"

PAGE_BG = "#FAF8FF"
CARD_BG = "#FFFFFF"
BORDER = "#D2D2E1"

MIN_W, MIN_H = 750, 600
DEFAULT_W, DEFAULT_H = 1180, 820


STYLE = f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {PAGE_BG}; }}

/* ---------- 顶部标题条 ---------- */
#Header {{ background: {CARD_BG}; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 17px; font-weight: 700; color: {TEXT}; }}
#AppSub {{ color: {TEXT_SUB}; font-size: 11.5px; }}
#StatusDot {{ font-size: 12px; color: {TEXT_SUB}; }}

/* ---------- 输入区（对应小猪 pnlInput） ---------- */
#InputPanel {{ background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; }}
#FieldLabel {{ color: {TEXT_SUB}; font-size: 12px; }}

/* ---------- 统计卡（对应小猪 MakeStatCard） ---------- */
#StatCard {{ border: 1px solid {BORDER}; border-radius: 9px; background: {PRIMARY_LIGHT}; }}
#StatValue {{ font-size: 21px; font-weight: 700; }}
#StatLabel {{ color: {TEXT_SUB}; font-size: 11.5px; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 6px 14px; color: {TEXT};
}}
QPushButton:hover {{ background: {PRIMARY_LIGHT}; border-color: {PRIMARY}; }}
QPushButton:pressed {{ background: #EDE6FF; }}
QPushButton:disabled {{ color: #B4B4C8; background: #F7F6FB; border-color: #E4E3EF; }}

QPushButton#Primary {{
    background: {PRIMARY}; border: 1px solid {PRIMARY}; color: #FFFFFF; font-weight: 600;
    padding: 7px 20px;
}}
QPushButton#Primary:hover {{ background: {PRIMARY_DARK}; border-color: {PRIMARY_DARK}; }}
QPushButton#Primary:disabled {{ background: #C7B8F5; border-color: #C7B8F5; color: #FFFFFF; }}

QPushButton#Danger {{ color: {DANGER}; border-color: #F0C0C0; background: {DANGER_LIGHT}; }}
QPushButton#Danger:hover {{ background: #FFECEC; }}
QPushButton#Success {{ color: {SUCCESS}; border-color: #BFE5CC; background: {SUCCESS_LIGHT}; }}
QPushButton#Success:hover {{ background: #E5F8EC; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_SUB}; }}
QPushButton#Ghost:hover {{ background: {PRIMARY_LIGHT}; color: {TEXT}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {PAGE_BG}; border: 1px solid {BORDER}; border-radius: 7px;
    padding: 5px 9px; color: {TEXT};
    selection-background-color: {PRIMARY}; selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {{
    border-color: {PRIMARY}; background: {CARD_BG};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: #F4F3F9; color: #B4B4C8;
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {CARD_BG}; border: 1px solid {BORDER};
    selection-background-color: {PRIMARY_LIGHT}; selection-color: {TEXT};
}}

/* ---------- 表格（对应小猪 记录列表） ---------- */
QTableWidget, QTableView {{
    background: {CARD_BG}; alternate-background-color: #FCFBFE;
    border: 1px solid {BORDER}; border-radius: 9px;
    gridline-color: #F0EFF7;
}}
QHeaderView::section {{
    background: {PRIMARY_LIGHT}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 8px; color: {TEXT}; font-weight: 600; font-size: 12px;
}}
QTableWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected {{ background: #EDE6FF; color: {TEXT}; }}

/* ---------- Tabs（步骤切换） ---------- */
QTabWidget::pane {{
    border: 1px solid {BORDER}; border-radius: 10px; background: {CARD_BG}; top: -1px;
}}
QTabBar::tab {{
    background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 8px 18px; margin-right: 2px; color: {TEXT_SUB}; font-size: 13px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {PRIMARY_LIGHT}; border-radius: 7px 7px 0 0; }}
QTabBar::tab:selected {{
    color: {PRIMARY}; font-weight: 700; border-bottom: 2px solid {PRIMARY};
}}

/* ---------- 滚动条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D5D3E4; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #B9B6CE; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #D5D3E4; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ---------- 其它 ---------- */
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {BORDER};
    border-radius: 4px; background: {CARD_BG};
}}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
QProgressBar {{
    background: #F0EFF7; border: none; border-radius: 6px;
    height: 12px; text-align: center; color: {TEXT_SUB}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 6px; }}
QTextEdit#LogView {{
    background: #FBFAFE; border: 1px solid {BORDER}; border-radius: 9px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 12px; color: #3A3A5C;
}}
QToolTip {{ background: #2A2A45; color: #FFFFFF; border: none; padding: 5px 8px; }}
QMessageBox {{ background: {CARD_BG}; }}
"""


def stat_style(bg: str) -> str:
    """统计卡的独立样式（小猪 MakeStatCard 每种卡一个底色）。"""
    return f"#StatCard {{ background: {bg}; border: 1px solid {BORDER}; border-radius: 9px; }}"
