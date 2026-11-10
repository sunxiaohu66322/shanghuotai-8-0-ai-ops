# -*- coding: utf-8 -*-
"""3.0 商品详情窗 —— 点表格里的商品名弹出来（2026-10-27 主人要求）。

为什么必须单独一个窗（主人的原话）：
    「如果它是拦住的，我再去点这个商品单词，我要可以打开这个商品的详细情况，
      就是跟那个 2.5 商品库一样，点开一个图片，它可以转到一个独立的窗口看到商品的详情」
    「他说是拍单号有这个字，所以会被拦下。但是我很怀疑一个商品详情页里面怎么会有拍单号的，
      你都要自己去看一下」

所以这个窗的唯一职责是**让人亲眼看证据**：
    左边大图（本地原图，不是缩略图）· 右边图列表 · 下面是 AI 判词原文与逐条理由。
三条硬规矩：
    1. 图没有本地文件就明说「没有本地文件」，不许拿网址当图糊上去；
    2. AI 判词原样显示，界面不重新润色；
    3. 判不了就写「AI 未参与判定」并把原因列出来。
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout,
                             QWidget)

from .. import bridge, theme

_ROLE_CN = {"main": "主图", "carousel": "轮播", "detail": "详情图",
            "sku": "SKU 图", "video": "视频封面", "white": "白底图"}
# ★ 2026-10-27：3.0 判定权已交给 AI，规则层的 block 现在只是「参考」——
#   标签必须跟着改，否则人会以为这件还被关键词拦着。
_LV_CN = {"block": "规则参考", "pending": "证据缺口", "warn": "规则参考",
          "signal": "规则参考", "info": "AI 判词"}


class ProductDetailDialog(QDialog):
    """看图 + 读理由 + 就地放行。"""

    def __init__(self, pid, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.data = {}
        self._cur_path = ""
        self.setWindowTitle("商品详情 · #%s" % pid)
        self.resize(1120, 780)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        self.lb_title = QLabel("正在读取…")
        self.lb_title.setWordWrap(True)
        self.lb_title.setStyleSheet("font-size:15px;font-weight:600;")
        v.addWidget(self.lb_title)

        self.lb_sub = QLabel("")
        self.lb_sub.setWordWrap(True)
        self.lb_sub.setStyleSheet("font-size:12px;color:%s;" % theme.TEXT_SUB)
        v.addWidget(self.lb_sub)

        split = QSplitter(Qt.Orientation.Horizontal)
        # 左：大图
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.lb_img = QLabel("（选右边一张图）")
        self.lb_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_img.setMinimumSize(420, 420)
        self.lb_img.setStyleSheet(
            "background:%s;border:1px solid %s;color:%s;font-size:12px;"
            % (theme.CARD_BG, theme.BORDER, theme.TEXT_SUB))
        lv.addWidget(self.lb_img, 1)
        self.lb_imginfo = QLabel("")
        self.lb_imginfo.setWordWrap(True)
        self.lb_imginfo.setStyleSheet("font-size:11px;color:%s;" % theme.TEXT_SUB)
        lv.addWidget(self.lb_imginfo)
        split.addWidget(left)

        # 右：AI 判词 + 理由 + 图列表
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.lb_ai = QLabel("AI 判词：—")
        self.lb_ai.setWordWrap(True)
        self.lb_ai.setStyleSheet("font-size:13px;padding:6px;border:1px solid %s;"
                                 "border-radius:6px;background:%s;"
                                 % (theme.BORDER, theme.CARD_BG))
        rv.addWidget(self.lb_ai)
        rv.addWidget(QLabel("规则层看到的东西（3.0 判定权在 AI，这里只供你核对）"))
        self.tx_reasons = QPlainTextEdit()
        self.tx_reasons.setReadOnly(True)
        self.tx_reasons.setStyleSheet("font-size:12px;")
        self.tx_reasons.setMinimumHeight(180)
        rv.addWidget(self.tx_reasons, 1)
        rv.addWidget(QLabel("图片（点一张看大图）"))
        self.ls_img = QListWidget()
        self.ls_img.setStyleSheet("font-size:12px;")
        self.ls_img.setMinimumHeight(150)
        self.ls_img.currentRowChanged.connect(self._pick_image)
        rv.addWidget(self.ls_img, 1)
        split.addWidget(right)
        split.setSizes([560, 540])
        v.addWidget(split, 1)

        row = QHBoxLayout()
        # ★ 2026-10-27：判定要能单件改（主人要求），并且能就地点评这次判定
        self.btn_pass = QPushButton("✅ 判定为通过")
        self.btn_pass.clicked.connect(lambda: self._set_verdict("pass"))
        row.addWidget(self.btn_pass)
        self.btn_pend = QPushButton("⏸ 判定为待复核")
        self.btn_pend.clicked.connect(lambda: self._set_verdict("pending"))
        row.addWidget(self.btn_pend)
        self.btn_block = QPushButton("⛔ 判定为拦住")
        self.btn_block.clicked.connect(lambda: self._set_verdict("block"))
        row.addWidget(self.btn_block)
        self.btn_comment = QPushButton("✍ 点评这个结果")
        self.btn_comment.setToolTip("写下「哪判错了、应该怎么判」—— 它会成为 AI 的纠错口径，"
                                   "下次判定直接按你说的走")
        self.btn_comment.clicked.connect(self._comment)
        row.addWidget(self.btn_comment)
        self.btn_open_dir = QPushButton("📂 打开图片目录")
        self.btn_open_dir.clicked.connect(self._open_dir)
        row.addWidget(self.btn_open_dir)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        row.addWidget(bb)
        v.addLayout(row)

        self._load()

    # ---------------- 读数据 ----------------
    def _load(self):
        try:
            r = bridge.fine_product_detail(self.pid)
        except Exception as e:                       # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        self.data = r
        if not r.get("ok"):
            self.lb_title.setText("读不到这件商品：%s" % r.get("error"))
            return
        p = r["product"]
        title = p.get("original_title") or p.get("title") or ""
        self.lb_title.setText("#%s %s" % (self.pid, title))
        ai = r.get("ai") or {}
        av = str(ai.get("verdict") or "")
        status = {"blocked": "被拦下", "pending": "待复核",
                  "fine_screened": "已精筛", "imported": "已导入"}.get(
                      p.get("status") or "", p.get("status") or "")
        imgs = r.get("images") or []
        n_ok = sum(1 for i in imgs if i.get("exists"))
        self.lb_sub.setText(
            "状态：%s ｜ 判定：%s ｜ 图 %d 张（本地有文件 %d 张）｜ 价格：%s ｜ 精筛时间：%s"
            % (status, r.get("verdict") or "—", len(imgs), n_ok,
               p.get("price_min") if p.get("price_min") is not None else "未采到",
               r.get("at") or "—"))

        if ai.get("ok"):
            self.lb_ai.setText("AI 判词（%s ｜ %s ｜ 置信 %s）：%s" % (
                {"pass": "放行", "block": "拦住", "warn": "待复核"}.get(av, av),
                ai.get("model") or "模型",
                ai.get("confidence") or "—",
                ai.get("comment") or "（AI 没给点评）"))
        else:
            if not ai:
                self.lb_ai.setText("这件还没跑过 AI 判官 —— 回 3.0 勾上「AI 判官」重跑一次精筛，"
                                   "这里就会出现它的逐条点评；下面的理由目前只有关键词规则")
            else:
                self.lb_ai.setText("AI 未参与判定：%s —— 下面的理由只有关键词规则，"
                                   "不能当成最终结论" % (ai.get("error") or "未知原因"))

        lines = []
        for x in (r.get("reasons") or []):
            lv = _LV_CN.get(x.get("level") or "", x.get("level") or "?")
            lines.append("[%s] %s" % (lv, x.get("msg") or x.get("code") or ""))
        if not lines:
            lines.append("（这件没有任何理由记录 —— 可能还没跑过精筛）")
        self.tx_reasons.setPlainText("\n".join(lines))

        self.ls_img.clear()
        for i in imgs:
            tag = _ROLE_CN.get(i.get("role") or "", i.get("role") or "图")
            name = "%s#%s" % (tag, i.get("seq") or "")
            if not i.get("exists"):
                name += "（没有本地文件）"
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.ls_img.addItem(it)
        if imgs:
            self.ls_img.setCurrentRow(0)
        # 三个改判按钮：把「当前就是这一档」的那个按钮标出来，避免重复点
        cur = str(r.get("verdict") or "")
        for btn, v in ((self.btn_pass, "passed"), (self.btn_pend, "pending"),
                       (self.btn_block, "blocked")):
            btn.setEnabled(cur != v)
            btn.setToolTip("当前已经是这一档" if cur == v else "点了立即改判（AI 判词保留）")

    # ---------------- 看图 ----------------
    def _pick_image(self, row: int):
        if row < 0:
            return
        it = self.ls_img.item(row)
        if it is None:
            return
        info = it.data(Qt.ItemDataRole.UserRole) or {}
        path = str(info.get("path") or "")
        self._cur_path = path
        if not path:
            self.lb_img.setPixmap(QPixmap())
            self.lb_img.setText("这张图没有本地文件\n（只有网址，本机没下载过）\n%s"
                                % (info.get("url") or "")[:120])
            self.lb_imginfo.setText(str(info.get("url") or ""))
            return
        if not os.path.isfile(path):
            self.lb_img.setPixmap(QPixmap())
            self.lb_img.setText("库里记了路径，但文件不在：\n%s" % path)
            self.lb_imginfo.setText(path)
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.lb_img.setPixmap(QPixmap())
            self.lb_img.setText("这个文件读不成图片：\n%s" % path)
            return
        self.lb_img.setPixmap(pm.scaled(self.lb_img.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
        self.lb_imginfo.setText("%s ｜ %d×%d ｜ %s"
                                % (os.path.basename(path), pm.width(), pm.height(), path))

    def _open_dir(self):
        d = os.path.dirname(self._cur_path) if self._cur_path else ""
        if not d or not os.path.isdir(d):
            d = ""
            for i in (self.data.get("images") or []):
                if i.get("path"):
                    d = os.path.dirname(i["path"])
                    break
        if d and os.path.isdir(d):
            bridge.open_path(d)
        else:
            self.lb_imginfo.setText("这件商品没有本地图片目录（图都只有网址）")

    def _release(self):
        """兼容老调用点：等价于「判定为通过」。"""
        self._set_verdict("pass")

    def _set_verdict(self, v: str):
        r = bridge.v3_set_verdict(int(self.pid), v)
        if not r.get("ok"):
            QMessageBox.warning(self, "改判失败", str(r.get("error") or "未知原因"))
            return
        self._load()                                  # 重读，界面上会显示「人工改判」
        self.lb_sub.setText(self.lb_sub.text() + " ｜ 已改判为「%s」"
                            % {"pass": "通过", "pending": "待复核",
                               "block": "拦住"}.get(v, v))

    def _comment(self):
        """点评这次判定 —— 存成纠错口径，下次判定直接喂给 AI。"""
        ai = (self.data or {}).get("ai") or {}
        prev = ai.get("comment") or ""
        txt, ok = QInputDialog.getMultiLineText(
            self, "点评这个结果 · #%s" % self.pid,
            "AI 当时说：\n%s\n\n我不同意的地方 / 应该怎么判（会累加成 AI 的口径）：\n"
            % (prev or "（这件没有 AI 判词）"),
            "")
        if not ok or not txt.strip():
            return
        p = (self.data or {}).get("product") or {}
        r = bridge.ai_feedback_add(int(self.pid), p.get("original_title") or "",
                                   txt.strip(), ai.get("verdict") or "", prev,
                                   (self.data or {}).get("verdict") or "")
        if r.get("ok"):
            self.lb_ai.setText("已记下你的点评（共 %s 条）。到右侧「⚙ 设置 → 判定提示词」"
                               "点「合并进提示词」或「让 AI 提炼」，下次判定就按新口径走。"
                               % r.get("count"))
        else:
            QMessageBox.warning(self, "点评没存上", str(r.get("error") or "未知原因"))
