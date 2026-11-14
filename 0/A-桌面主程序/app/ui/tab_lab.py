# -*- coding: utf-8 -*-
"""4.9 · 主图 AI 重做

位置：第 4 步「检测打码」之后、第 5 步「出表」之前。

为什么不塞进第 4 步：
    第 4 步改的是**同一张图上**的违规字/字块（内容修补），产物还是那张图；
    这一页改的是**整张主图重做**（白底/换景/清促销/重绘），产物是**另一张候选图**。
    两者的失败回退、审计记录、费用台账都不一样，混一页会把账搅乱。

引擎不在这里重写 —— 直接调 taobao-picker\\image_lab（engine/providers/schema），
本页只做三件事：选图、跑、确认。

三条硬约束（照 v3.5 任务书，写进代码不是写在注释里）：
    ① 原图永不覆盖：AI 产物是 image_assets 新记录，is_original=0
    ② 未确认不进发布：出表读 v3_image.current_local_path，只有这里点确认才改
    ③ 每次替换留旧值：改指针前先写 lab_writeback，可一键退回
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import bridge, paths, theme


class LabWorker(QThread):
    """顺序跑一批主图（一张一张来：付费接口不能并发烧）。"""
    tick = pyqtSignal(int, int, str)      # done, total, 文字
    done = pyqtSignal(dict)

    def __init__(self, ids, mode, policy, dry_run, budget, parent=None):
        super().__init__(parent)
        self.ids = list(ids)
        self.mode = mode
        self.policy = policy
        self.dry_run = dry_run
        self.budget = budget

    def run(self):  # noqa: D102
        out = {"ok": 0, "fail": 0, "cost": 0.0, "jobs": [], "errors": []}
        n = len(self.ids)
        for i, iid in enumerate(self.ids, 1):
            self.tick.emit(i - 1, n, "image_id=%s 处理中…" % iid)
            try:
                r = bridge.lab_preview(iid, mode=self.mode, policy=self.policy,
                                      dry_run=self.dry_run, budget=self.budget)
            except Exception as ex:                        # noqa: BLE001
                r = {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
            if r.get("ok"):
                out["ok"] += 1
                out["cost"] += float(r.get("cost") or 0)
                out["jobs"].append({"image_id": iid, "job_id": r.get("job_id"),
                                    "asset": (r.get("assets") or [{}])[-1].get("asset_id")
                                    if r.get("assets") else None,
                                    "status": r.get("status"),
                                    "quality": r.get("quality"),
                                    "review": r.get("requires_review")})
                self.tick.emit(i, n, "image_id=%s → %s（%s）"
                               % (iid, r.get("status"), r.get("quality") or "无质检"))
            else:
                out["fail"] += 1
                out["errors"].append({"image_id": iid, "error": r.get("error")})
                self.tick.emit(i, n, "image_id=%s 失败：%s" % (iid, r.get("error")))
        self.done.emit(out)


class LabStepTab(QWidget):
    def __init__(self, mw, parent=None):
        super().__init__(parent)
        self.mw = mw
        self.rows: list[dict] = []
        self.cur: dict | None = None
        self.worker: LabWorker | None = None
        self._build()
        self.refresh()

    # ------------------------------------------------------------ 界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        # 第零行（★2026-10-25 阶段二第三批）：先选用途 —— 原来这里只能改主图，
        # 轮播/详情/SKU 图/视频在界面上根本选不到。默认「主图」，老习惯不变。
        r0 = QHBoxLayout()
        r0.setSpacing(6)
        r0.addWidget(QLabel("用途"))
        self.cb_role = QComboBox()
        self.cb_role.setMinimumWidth(120)
        for _lbl, _val in (("主图 / 轮播", "main"), ("详情图", "detail"),
                           ("SKU 图", "sku"), ("视频", "video")):
            self.cb_role.addItem(_lbl, _val)
        self.cb_role.currentIndexChanged.connect(lambda _: self.refresh())
        r0.addWidget(self.cb_role)
        self.lb_role_hint = QLabel("")
        self.lb_role_hint.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        r0.addWidget(self.lb_role_hint)
        r0.addStretch(1)
        v.addLayout(r0)

        # 第一行：范围
        r1 = QHBoxLayout()
        r1.setSpacing(6)
        r1.addWidget(QLabel("范围"))
        self.cb_scope = QComboBox()
        self.cb_scope.setMinimumWidth(210)
        self.cb_scope.currentIndexChanged.connect(lambda _: self.refresh())
        r1.addWidget(self.cb_scope)
        self.ed_q = QLineEdit()
        self.ed_q.setPlaceholderText("搜商品标题 / 图片ID")
        self.ed_q.setMaximumWidth(220)
        self.ed_q.returnPressed.connect(self.refresh)
        r1.addWidget(self.ed_q)
        self.ck_local = QCheckBox("只看有本地文件")
        self.ck_local.setChecked(True)
        self.ck_local.stateChanged.connect(lambda _: self.refresh())
        r1.addWidget(self.ck_local)
        btn_reload = QPushButton("刷新")
        btn_reload.clicked.connect(self.refresh)
        r1.addWidget(btn_reload)
        r1.addStretch(1)
        self.lb_stat = QLabel("—")
        self.lb_stat.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        r1.addWidget(self.lb_stat)
        v.addLayout(r1)

        # 第二行：模式与闸门
        r2 = QHBoxLayout()
        r2.setSpacing(6)
        r2.addWidget(QLabel("模式"))
        self.cb_mode = QComboBox()
        for m in bridge.lab_modes():
            self.cb_mode.addItem(m["label"], m)
        self.cb_mode.setMinimumWidth(200)
        self.cb_mode.currentIndexChanged.connect(self._mode_hint)
        r2.addWidget(self.cb_mode)
        r2.addWidget(QLabel("用哪家"))
        self.cb_policy = QComboBox()
        for k, lab in (("auto", "自动（本地优先）"), ("local_only", "只本地（不花钱）"),
                       ("aidge_only", "只阿里云 Aidge"), ("seedream_pro", "只火山 Pro"),
                       ("seedream_flash", "只火山 Flash")):
            self.cb_policy.addItem(lab, k)
        r2.addWidget(self.cb_policy)
        r2.addWidget(QLabel("单张预算"))
        self.sp_budget = QDoubleSpinBox()
        self.sp_budget.setRange(0.0, 20.0)
        self.sp_budget.setSingleStep(0.1)
        self.sp_budget.setValue(1.0)
        self.sp_budget.setPrefix("¥")
        self.sp_budget.setMaximumWidth(90)
        r2.addWidget(self.sp_budget)
        self.ck_paid = QCheckBox("允许付费")
        self.ck_paid.setToolTip("不勾 = 只预演（dry_run）。勾了才会真的调付费接口并产生费用。")
        r2.addWidget(self.ck_paid)
        r2.addStretch(1)
        self.btn_run = QPushButton("预演选中")
        self.btn_run.clicked.connect(self._run_selected)
        r2.addWidget(self.btn_run)
        self.btn_run5 = QPushButton("预演前 5 张")
        self.btn_run5.clicked.connect(lambda: self._run(self._first_ids(5)))
        r2.addWidget(self.btn_run5)
        v.addLayout(r2)

        self.lb_hint = QLabel("")
        self.lb_hint.setWordWrap(True)
        self.lb_hint.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:11px;")
        v.addWidget(self.lb_hint)

        # 主区：左表 右对比
        sp = QSplitter(Qt.Orientation.Horizontal)

        self.tb = QTableWidget(0, 6)
        self.tb.setHorizontalHeaderLabels(["商品", "图ID", "序", "本地", "当前版本", "包"])
        self.tb.verticalHeader().setVisible(False)
        self.tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.tb.itemSelectionChanged.connect(self._on_pick)
        sp.addWidget(self.tb)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 0, 0, 0)
        rv.setSpacing(6)
        cap = QHBoxLayout()
        cap.addWidget(QLabel("原图（当前版本）"))
        cap.addStretch(1)
        cap.addWidget(QLabel("AI 候选"))
        rv.addLayout(cap)
        imgs = QHBoxLayout()
        self.im_a = QLabel("未选")
        self.im_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.im_a.setMinimumSize(200, 200)
        self.im_a.setStyleSheet(f"border:1px solid {theme.BORDER}; background:#FFFFFF;"
                                f"color:{theme.TEXT_SUB};")
        self.im_b = QLabel("—")
        self.im_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.im_b.setMinimumSize(200, 200)
        self.im_b.setStyleSheet(f"border:1px solid {theme.PRIMARY}; background:#FFFFFF;"
                                f"color:{theme.TEXT_SUB};")
        imgs.addWidget(self.im_a, 1)
        imgs.addWidget(self.im_b, 1)
        rv.addLayout(imgs)

        self.lb_info = QLabel("选一张主图，再点「预演选中」。预演不花钱、不产生新图。")
        self.lb_info.setWordWrap(True)
        self.lb_info.setStyleSheet(f"color:{theme.TEXT}; font-size:12px;")
        rv.addWidget(self.lb_info)

        acts = QHBoxLayout()
        self.btn_ok = QPushButton("✓ 确认换成这张")
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._confirm)
        acts.addWidget(self.btn_ok)
        self.btn_back = QPushButton("↶ 退回上一版")
        self.btn_back.clicked.connect(self._revert)
        acts.addWidget(self.btn_back)
        self.btn_dir = QPushButton("打开图所在文件夹")
        self.btn_dir.clicked.connect(self._open_dir)
        acts.addWidget(self.btn_dir)
        # ★ 2026-10-25 第四批：外部加工好的新图/新视频要能导进来并确认成"当前版本"。
        #   之前只有主图能换（走 approve 改指针），详情图/SKU 图/视频在界面上根本没有入口。
        self.btn_imp = QPushButton("📥 导入新文件")
        self.btn_imp.setToolTip("选一张图或一段视频（mp4/mov/m4v/webm）→ 只生成候选，不动当前版本")
        self.btn_imp.clicked.connect(self._import_media)
        acts.addWidget(self.btn_imp)
        self.btn_use = QPushButton("✓ 用这一版")
        self.btn_use.setEnabled(False)
        self.btn_use.setToolTip("把选中的候选确认为该用途的当前版本；线上地址会清空 → 进入待上传")
        self.btn_use.clicked.connect(self._use_candidate)
        acts.addWidget(self.btn_use)
        self.btn_orig = QPushButton("↶ 恢复原版")
        self.btn_orig.setEnabled(False)
        self.btn_orig.setToolTip("把当前版本退回最初那张/那段（替换过的文件不删）")
        self.btn_orig.clicked.connect(self._revert_media)
        acts.addWidget(self.btn_orig)
        acts.addStretch(1)
        rv.addLayout(acts)
        sp.addWidget(right)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 2)
        v.addWidget(sp, 1)

        self._mode_hint()

    # ------------------------------------------------------------ 数据
    def refresh(self):
        role = (self.cb_role.currentData() if hasattr(self, "cb_role") else "main") or "main"
        hint = {"main": "轮播图 = 全部主图按顺序；改其中任一张即改轮播",
                "detail": "详情图按顺序出表，替换后需上传图床才能出表",
                "sku": "SKU 图按 sku_id 对应规格，替换不会改变归属",
                "video": "视频不参与图片排序；抖店模板无视频列，内部仍保留"}.get(role, "")
        if hasattr(self, "lb_role_hint"):
            self.lb_role_hint.setText(hint)
        try:
            sc = bridge.lab_scopes(role=role)
            if sc.get("ok"):
                keep = self.cb_scope.currentData()
                self.cb_scope.blockSignals(True)
                self.cb_scope.clear()
                for it in sc["items"]:
                    lbl = "%s（%d 张 / 本地 %d）" % (it["label"], it["n"], it["local"])
                    self.cb_scope.addItem(lbl, it["scope"])
                if keep:
                    i = self.cb_scope.findData(keep)
                    if i >= 0:
                        self.cb_scope.setCurrentIndex(i)
                self.cb_scope.blockSignals(False)
        except Exception as ex:                       # noqa: BLE001
            self.mw.log("4.9 取范围失败：%s" % ex)

        scope = self.cb_scope.currentData() or "all"
        r = bridge.lab_mains(scope=scope, only_local=self.ck_local.isChecked(),
                             limit=500, q=self.ed_q.text().strip(), role=role)
        if not r.get("ok"):
            self.mw.log("4.9 列表失败：%s" % r.get("error"))
            self.lb_stat.setText("列表不可用：%s" % r.get("error"))
            return
        self.rows = r["rows"]
        self.tb.setRowCount(0)
        for d in self.rows:
            i = self.tb.rowCount()
            self.tb.insertRow(i)
            title = (d.get("original_title") or "")[:46]
            cells = [title, str(d["image_id"]), str(d.get("sequence") or ""),
                     "✓" if d.get("exists") else "✗",
                     "当前主图" if d.get("is_main") else "", d.get("pack_code") or ""]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c in (1, 2, 3):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tb.setItem(i, c, it)
        self.lb_stat.setText("共 %d 张 · 本地可读 %d 张 · 库 %s"
                             % (r["n"], r["local"], os.path.basename(paths.IMAGE_LAB_DB)))

    def _mode_hint(self):
        m = self.cb_mode.currentData() or {}
        paid = "会花钱" if m.get("paid") else "不花钱"
        self.lb_hint.setText("%s ｜ %s ｜ %s" % (m.get("label", ""), paid, m.get("why", "")))

    def _selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.tb.selectedIndexes()})
        out = []
        for r in rows:
            try:
                out.append(int(self.tb.item(r, 1).text()))
            except Exception:                          # noqa: BLE001
                continue
        return out

    def _first_ids(self, n: int) -> list[int]:
        return [int(d["image_id"]) for d in self.rows
                if d.get("exists")][:n]

    def _on_pick(self):
        ids = self._selected_ids()
        if not ids:
            self.cur = None
            self.btn_ok.setEnabled(False)
            return
        d = bridge.lab_image(ids[0])
        self.cur = d if d.get("ok") else None
        if not self.cur:
            self.lb_info.setText("取图失败：%s" % (d.get("error") or "?"))
            return
        self._show(self.im_a, self.cur.get("current_local_path"))
        self.im_b.setText("—")
        self.btn_ok.setEnabled(False)
        st = (self.cur.get("status") or "")
        self.btn_use.setEnabled(st in ("candidate", "approved"))
        self.btn_orig.setEnabled(st == "replaced")
        t = (self.cur.get("original_title") or "")[:60]
        self.lb_info.setText(
            "图片 %s · 商品 %s · 第 %s 张 · %s\n当前文件：%s\n状态：%s"
            % (self.cur.get("image_id"), self.cur.get("product_id"),
               self.cur.get("sequence"), t,
               paths.rel(self.cur.get("current_local_path") or ""),
               {"original": "原始版本", "replaced": "已替换（当前版本）",
                "candidate": "候选（未确认）", "approved": "已确认过的候选"}.get(st, st or "—")))

    def _show(self, lab: QLabel, path: str):
        try:
            if path and os.path.exists(path):
                pm = QPixmap(path)
                if not pm.isNull():
                    lab.setPixmap(pm.scaled(
                        lab.width() or 240, lab.height() or 240,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                    return
            lab.setText("（无图）")
        except Exception:                              # noqa: BLE001
            lab.setText("（读图失败）")

    # ------------------------------------------------------------ 动作
    def _run_selected(self):
        ids = self._selected_ids()
        if not ids:
            self.mw.log("4.9：先在上表选至少一张主图")
            return
        self._run(ids)

    def _run(self, ids: list[int]):
        if not ids:
            self.mw.log("4.9：没有可跑的图（列表中本地可读为 0）")
            return
        if self.worker is not None and self.worker.isRunning():
            self.mw.log("4.9：上一批还在跑，等它结束")
            return
        m = self.cb_mode.currentData() or {}
        dry = not self.ck_paid.isChecked()
        if not dry and m.get("paid"):
            self.mw.log("4.9：注意 —— 已勾「允许付费」，这一批会真实产生费用（单张上限 ¥%.2f）"
                        % self.sp_budget.value())
        self.worker = LabWorker(ids, m.get("mode", "white_bg"),
                               self.cb_policy.currentData() or "auto",
                               dry, self.sp_budget.value(), self)
        self.worker.tick.connect(lambda a, b, s: (
            self.lb_stat.setText("进度 %d/%d · %s" % (a, b, s)), self.mw.log("4.9 " + s)))
        self.worker.done.connect(self._done)
        self.btn_run.setEnabled(False)
        self.btn_run5.setEnabled(False)
        self.worker.start()

    def _done(self, out: dict):
        self.btn_run.setEnabled(True)
        self.btn_run5.setEnabled(True)
        self.lb_stat.setText("完成：成功 %d · 失败 %d · 本批 ¥%.4f"
                             % (out["ok"], out["fail"], out["cost"]))
        self.mw.log("4.9 本批结束：成功 %d，失败 %d，花费 ¥%.4f"
                    % (out["ok"], out["fail"], out["cost"]))
        for e in out["errors"][:5]:
            self.mw.log("4.9 失败 image_id=%s：%s" % (e["image_id"], e["error"]))
        # 把最后一个成功的候选显示出来
        ok = [j for j in out["jobs"] if j.get("asset")]
        if ok:
            self._show_candidate(ok[-1])

    def _show_candidate(self, j: dict):
        try:
            w = bridge.lab_asset(j["asset"])
            if not w.get("ok"):
                return
            self._show(self.im_b, w.get("path"))
            kind = w.get("kind") or ""
            is_audit = kind == "compose" and "audit" in os.path.basename(
                w.get("path") or "").lower()
            head = ("预演产物：审计标注图（不是 AI 成品，图上标了识别框/差异）"
                    if is_audit else "AI 候选图")
            self.lb_info.setText(
                "%s ｜ %s ｜ 状态 %s ｜ 质检 %s ｜ 需人工复核 %s\n%s"
                % (head, w.get("asset_id"), j.get("status"), j.get("quality") or "—",
                   "是" if j.get("review") else "否",
                   ("预演不产生新图，也不花钱。要出真新图必须勾「允许付费」并把付费接口配好。"
                    if is_audit else
                    "确认后才改数据库指针；不确认就一直是候选，不影响出表。")))
            self._pending = {"image_id": j["image_id"], "asset_id": w.get("asset_id"),
                            "job_id": j.get("job_id")}
            self.btn_ok.setEnabled(not is_audit)
        except Exception as ex:                        # noqa: BLE001
            self.mw.log("4.9 取候选失败：%s" % ex)

    def _confirm(self):
        p = getattr(self, "_pending", None)
        if not p:
            return
        r = bridge.lab_confirm(p["image_id"], p["asset_id"], p.get("job_id") or "")
        if r.get("ok"):
            self.mw.log("4.9 已确认替换 image_id=%s（台账 #%s，可退回）"
                        % (r["image_id"], r.get("wb_id")))
            self._on_pick()
            self.btn_ok.setEnabled(False)
            self._pending = None
        else:
            self.mw.log("4.9 确认失败：%s" % r.get("error"))

    def _revert(self):
        cur = self.cur or {}
        iid = cur.get("image_id")
        if not iid:
            self.mw.log("4.9：先选一张图")
            return
        wb = bridge.lab_writebacks(image_id=iid, limit=1)
        rows = wb.get("rows") or []
        if not rows:
            self.mw.log("4.9：这张图没有替换记录，无需退回")
            return
        r = bridge.lab_revert(rows[0]["id"])
        if r.get("ok"):
            self.mw.log("4.9 已退回 image_id=%s → %s"
                        % (r["image_id"], paths.rel(r["restored"])))
            self._on_pick()
        else:
            self.mw.log("4.9 退回失败：%s" % r.get("error"))

    # ------------------------------------------- 导入 / 确认这一版 / 恢复原版（详情图·SKU图·视频）
    def _role_now(self) -> str:
        try:
            return self.cb_role.currentData() or "main"
        except Exception:                              # noqa: BLE001
            return "main"

    def _import_media(self):
        cur = self.cur or {}
        pid = cur.get("product_id")
        if not pid:
            self.mw.log("4.1：先在左边点一行 —— 导入要落到那件商品上")
            return
        role = self._role_now()
        kind = {"main": "主图/轮播", "detail": "详情图", "sku": "SKU 图",
                "video": "视频"}.get(role, role)
        if role == "video":
            filt = "视频 (*.mp4 *.mov *.m4v *.webm);;所有文件 (*)"
        else:
            filt = "图片 (*.jpg *.jpeg *.png *.webp *.bmp);;所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(
            self, "选要导入的%s文件（外部软件做好后导回来）" % kind, "", filt)
        if not path:
            return
        r = bridge.v3_import_media(int(pid), path, role=role)
        if r.get("ok"):
            self.mw.log("4.1 已导入候选 image_id=%s（%s）→ %s ｜ 还要点「✓ 用这一版」才生效"
                        % (r["image_id"], kind, paths.rel(r.get("local_path") or "")))
            if r.get("is_video"):
                self.mw.log("4.1 视频只做了文件头校验（不是图片解码），也没生成缩略图")
            self.refresh()
        else:
            self.mw.log("4.1 导入失败：%s" % r.get("error"))

    def _use_candidate(self):
        cur = self.cur or {}
        iid, pid = cur.get("image_id"), cur.get("product_id")
        if not iid or not pid:
            self.mw.log("4.1：先选一个候选")
            return
        r = bridge.v3_approve_media(int(pid), int(iid))
        if r.get("ok"):
            self.mw.log("4.1 ✓ 已换成这一版：%s（原行 image_id=%s）｜ 线上地址已清空 → 待上传"
                        % (r.get("role"), r.get("target_image_id")))
            self.mw.log("4.1 原始文件仍保留：%s"
                        % paths.rel((r.get("original_kept") or {}).get("original_local_path") or ""))
            self._on_pick()
            self.btn_use.setEnabled(False)
        else:
            self.mw.log("4.1 确认这一版失败：%s" % r.get("error"))

    def _revert_media(self):
        cur = self.cur or {}
        iid, pid = cur.get("image_id"), cur.get("product_id")
        if not iid or not pid:
            self.mw.log("4.1：先选一行")
            return
        r = bridge.v3_revert_media(int(pid), int(iid))
        if r.get("ok"):
            self.mw.log("4.1 ↶ 已恢复原版：%s → %s"
                        % (r.get("role"), paths.rel(r.get("restored") or "")))
            self._on_pick()
            self.btn_orig.setEnabled(False)
        else:
            self.mw.log("4.1 恢复原版失败：%s" % r.get("error"))

    def _open_dir(self):
        p = (self.cur or {}).get("current_local_path") or ""
        if p and os.path.exists(p):
            try:
                os.startfile(os.path.dirname(p))       # noqa: S606
            except Exception as ex:                    # noqa: BLE001
                self.mw.log("打开文件夹失败：%s" % ex)
        else:
            self.mw.log("4.9：这张图没有本地文件")
