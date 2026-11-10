# -*- coding: utf-8 -*-
"""4.1 · 右侧固定工作台（阶段 C.1 第二批）

一块地方装两样东西，默认显示「修补」，点一下切「设置」：

    ┌ 主图修补 │ 设置 │ 收起 › ┐
    │                            │
    │   EditorPanel（复用 editor）│  ← 单击左侧主图就在这里打开，不再弹独立窗口
    │   或 SettingsPanel          │
    └────────────────────────────┘

三条纪律：
  · **不新建编辑能力**：直接嵌 `editor.EditorPanel`，修补内核/版本管理一行没重写
  · **不删设置功能**：`SettingsPanel` 是同一套类，另一个实例（自带 refresh，无定时器）
  · **收起不等于销毁**：只改宽度与可见性，面板状态（选区/历史）原样保留

★ 为什么收起用 setMaximumWidth 而不是 hide()：
  收起后如果整块 hide，用户就没有"重新展开"的入口了。留一条 46px 竖条，
  照片墙照样自动扩展，随时能点回来。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

NARROW_W = 46           # 收起后的竖条宽度
MIN_W = 360             # 展开时的最小宽度
DEFAULT_W = 560         # 展开时默认宽度（约内容区 38%）
DETAIL_W = 860          # 详情图页的宽度（里面还要左右分栏）


class Workbench(QWidget):
    """右侧工作台：修补面板 + 设置面板 + 详情图 共用一块地。"""

    collapsedChanged = pyqtSignal(bool)
    rowChanged = pyqtSignal(dict, str)      # 把编辑面板的状态变化透传给外层
    wantWide = pyqtSignal(int)              # 请求外层把这个面板调宽（px）

    def __init__(self, win, api, parent=None):
        super().__init__(parent)
        self.win = win
        self.api = api
        self.lib = None                 # MainLib（由 tab_wall_main 注入，详情图要用）
        self.panel = None               # editor.EditorPanel（首次点图时才建）
        self.settings = None            # settings_panel.SettingsPanel（首次切换时才建）
        self.detail = None              # wall_main.DetailDialog（嵌进来当第 3 页）
        self._row: dict = {}
        self._collapsed = False
        self._expanded_w = DEFAULT_W
        self.setMinimumWidth(MIN_W)
        self._build()

    # ---------------- UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 收起态：只留一条竖条
        self.btn_expand = QPushButton("⟨\n展\n开")
        self.btn_expand.setFixedWidth(NARROW_W - 4)
        self.btn_expand.setToolTip("展开右侧工作台")
        self.btn_expand.setStyleSheet("font-size:11px; padding:2px;")
        self.btn_expand.clicked.connect(lambda: self.set_collapsed(False))
        self.btn_expand.hide()
        root.addWidget(self.btn_expand)

        # 展开态：标题行 + 切换 + 收起
        self.body = QWidget(self)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        head = QHBoxLayout(); head.setSpacing(4)
        self.btn_edit = QPushButton("✎ 修补")
        self.btn_detail = QPushButton("🖼 详情图")
        self.btn_sets = QPushButton("⚙ 设置")
        self.btn_detail.setToolTip(
            "在这个区域显示该商品的**详情图**（右键左侧主图直接开）。\n"
            "可以逐张标记消除、应用后自动换掉原详情图（不改主图指针）。")
        self.grp = QButtonGroup(self)
        for i, b in enumerate((self.btn_edit, self.btn_detail, self.btn_sets)):
            b.setCheckable(True)
            self.grp.addButton(b, i)
            head.addWidget(b)
        self.btn_edit.setChecked(True)
        self.btn_edit.clicked.connect(lambda: self.show_page(0))
        self.btn_detail.clicked.connect(lambda: self.show_page(2))
        self.btn_sets.clicked.connect(lambda: self.show_page(1))
        head.addStretch(1)
        self.lb_head = QLabel("")
        self.lb_head.setStyleSheet("color:#8b96a4;")
        head.addWidget(self.lb_head)
        self.btn_collapse = QPushButton("收起 ›")
        self.btn_collapse.setToolTip("收起工作台（照片墙会自动变宽；面板状态保留）")
        self.btn_collapse.clicked.connect(lambda: self.set_collapsed(True))
        head.addWidget(self.btn_collapse)
        lay.addLayout(head)

        self.stack = QStackedWidget(self)
        self.ph = QLabel("单击左侧照片墙里的一张主图，\n这张图会在这里打开修补。\n\n"
                         "双击 = 独立大窗口精修。")
        self.ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ph.setStyleSheet("color:#6d7887;")
        self.stack.addWidget(self.ph)          # index 0（面板建好后被替换）
        self.stack.addWidget(QWidget())        # index 1（设置占位）
        self.detail_ph = QWidget()             # index 2（详情图占位）
        self.stack.addWidget(self.detail_ph)
        lay.addWidget(self.stack, 1)
        root.addWidget(self.body, 1)

    # ---------------- 页切换
    def show_page(self, idx: int):
        idx = 2 if idx == 2 else (1 if idx == 1 else 0)
        self.btn_edit.setChecked(idx == 0)
        self.btn_sets.setChecked(idx == 1)
        self.btn_detail.setChecked(idx == 2)
        if idx == 2 and self.detail is None:
            # 直接从顶部按钮点进来、还没选定商品时，别给一块空白
            self.lb_head.setText("右键左侧任意主图 → 在这里列出该商品的详情图")
        if idx == 1:
            if self.settings is None:
                try:
                    from .settings_panel import SettingsPanel
                    self.settings = SettingsPanel(self.win, self)
                    self.stack.removeWidget(self.stack.widget(1))
                    self.stack.insertWidget(1, self.settings)
                except Exception as e:      # noqa: BLE001
                    self.lb_head.setText("设置加载失败：%s: %s" % (type(e).__name__, e))
                    return
            try:
                self.settings.refresh()
            except Exception:               # noqa: BLE001
                pass
        self.stack.setCurrentIndex(idx)

    # ---------------- 收起 / 展开
    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, on: bool):
        on = bool(on)
        if on == self._collapsed:
            return
        self._collapsed = on
        if on:
            w = self.width()
            if w > MIN_W:
                self._expanded_w = w        # 记住用户拖出来的宽度
            self.body.hide()
            self.btn_expand.show()
            self.setMaximumWidth(NARROW_W)
            self.setMinimumWidth(NARROW_W)
        else:
            self.setMaximumWidth(16777215)
            self.setMinimumWidth(MIN_W)
            self.btn_expand.hide()
            self.body.show()
        self.collapsedChanged.emit(on)

    def preferred_width(self) -> int:
        return max(MIN_W, int(self._expanded_w or DEFAULT_W))

    # ---------------- 载入一张主图
    def load_row(self, row: dict):
        """单击左侧主图 → 在这里打开。没有本地文件时只给原因，不进修补流程。"""
        self._row = dict(row or {})
        if self._collapsed:
            self.set_collapsed(False)
        self.show_page(0)
        if self.panel is None:
            try:
                from .editor import EditorPanel
                self.panel = EditorPanel(self.api, self)
                self.panel.changed.connect(self._on_changed)
                self.stack.removeWidget(self.ph)
                self.stack.insertWidget(0, self.panel)
                self.stack.setCurrentIndex(0)
            except Exception as e:          # noqa: BLE001
                self.ph.setText("编辑器加载失败：\n%s: %s" % (type(e).__name__, e))
                return
        iid = self._row.get("image_id")
        if not self._row.get("exists"):
            self.lb_head.setText("无本地图 · #%s" % iid)
            try:
                self.panel.load(self._row)      # 面板会把原因写进状态行
            except Exception:                   # noqa: BLE001
                pass
            return
        self.lb_head.setText("#%s · 序号 %s" % (iid, self._row.get("sequence")))
        try:
            self.panel.load(self._row)
        except Exception as e:              # noqa: BLE001
            self.lb_head.setText("载入失败：%s: %s" % (type(e).__name__, e))

    def _on_changed(self, row: dict, state: str):
        # 就地更新照片墙那颗状态徽章（不整表重载 ⇒ 左侧不跳回顶部）
        iid = int(row.get("image_id") or 0)
        if state == "saved":
            self._row["state"] = self._row.get("state")
        self.rowChanged.emit(row, state)

    # ---------------- 右键：详情图（在右侧区域打开，不弹窗）
    def load_detail(self, pid: int):
        """把该商品的详情图列在这里（第 3 页）。

        为什么复用 `wall_main.DetailDialog` 而不是另写一个：
          它已经做完了"左边大图可拖框标记 / 右边详情图列表 / 批量 LaMa 消除"，
          重写一遍只会引入第二套行为。这里只是把它从"独立窗口"变成"嵌进来的控件"：
            · setWindowFlags(Widget) —— 不再是窗口，不会浮起来
            · set_close_hook(...)    —— 点「关闭」切回修补页，而不是把整块藏掉
        """
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return
        if self.lib is None:
            self.lb_head.setText("详情图不可用：没有注入图库（lib）")
            return
        if self._collapsed:
            self.set_collapsed(False)
        # 换商品 → 重建（Dialog 的 pid 在构造时绑定，改成可变更容易出错）
        if self.detail is not None and int(getattr(self.detail, "pid", 0) or 0) != pid:
            try:
                self.stack.removeWidget(self.detail)
                self.detail.deleteLater()
            except Exception:               # noqa: BLE001
                pass
            self.detail = None
        if self.detail is None:
            try:
                from PyQt6.QtCore import Qt

                from .wall_main import DetailDialog
                self.detail = DetailDialog(self.lib, pid, self.api, self)
                self.detail.setWindowFlags(Qt.WindowType.Widget)
                self.detail.set_close_hook(lambda: self.show_page(0))
                try:
                    self.stack.removeWidget(self.detail_ph)
                except Exception:           # noqa: BLE001
                    pass
                self.stack.insertWidget(2, self.detail)
            except Exception as e:          # noqa: BLE001
                self.lb_head.setText("详情图加载失败：%s: %s" % (type(e).__name__, e))
                return
        self.lb_head.setText("商品 #%s · 详情图 %d 张"
                             % (pid, len(getattr(self.detail, "rows", []) or [])))
        self.show_page(2)
        # 详情图要左右分栏（大图 + 列表），560px 太挤 → 请求外层加宽
        self.wantWide.emit(DETAIL_W)
