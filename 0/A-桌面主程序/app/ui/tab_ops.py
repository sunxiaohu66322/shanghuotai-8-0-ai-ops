# -*- coding: utf-8 -*-
"""8.0 · AI 运营中心（生产页签，2026-11-10）

六个真实子页 + 模块2「真实业务运营」（全部来自 9192 ops_engine 的真接口，不写死假数据）：
    ① 运营总览   引擎/商品/店铺/队列/执行记录 一屏
    ② 商品运营   生产 v3.db 只读商品（product_id / 标题 / 状态 / 图片数 / 视频数）
    ③ 店铺账号   3 家真实店铺完整 shop_id（凭据不入库）
    ④ 任务队列   ops_task 真状态机
    ⑤ 执行记录   ops_task_event + ops_run（真状态转移证据）
    ⑥ 引擎       启动 / 停止 / 重启 / 检查（主程序负责启停监控关闭）
    ⑦ 模块1      双平台真实商品中心（平台真数 ≠ v3.db 本地数）
    ⑧ 模块2      抖店后台真商品 / 短视频 / 限时限量购·活动 / 账号 / 操作反馈
                 每个按钮一条 running→done/failed，结果立刻回读（主人原话：起码能看到反馈状态）

设计约束（§三 §九）
    * 不写 v3.db、不发布、不改价改库存、零平台调用；数据经 9192 引擎只读
    * 引擎没起 = 这一页显示「未启动」，1—6.0 任何一页不受影响（主窗口 try 挂载）
    * 进程内线程起引擎（与 wxserve 同范式），主程序退出时由 closeEvent 收掉
"""
from __future__ import annotations

import json
import os
import urllib.request

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import theme
from ..ops import engine as ops_engine

DEFAULT_BASE = "http://127.0.0.1:9192"


def _base() -> str:
    return (os.environ.get("OPS_ENGINE_URL") or DEFAULT_BASE).rstrip("/")


class OpsCenterTab(QWidget):
    """8.0 运营总览 + 真实数据子页 + 引擎启停。"""

    def __init__(self, win=None):
        super().__init__(win)
        self.win = win
        self.base = _base()
        self.last = {}
        self.err = ""
        self._booted = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.lb_state = QLabel("● 8.0 运营引擎：检查中…")
        self.lb_state.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_refresh = QPushButton("↻ 刷新")
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_sync = QPushButton("⟳ 快速刷新")
        self.btn_sync.setToolTip("每状态只读第 1 页×20 件，如实标未完成分页；只写 ops.db")
        self.btn_sync.clicked.connect(lambda: self.sync_platform("snapshot"))
        self.btn_full = QPushButton("⇩ 全量同步")
        self.btn_full.setToolTip("微信两店翻完所有分页到 next_key 空 / 达权威总数，ID 去重；耗时约数分钟")
        self.btn_full.clicked.connect(lambda: self.sync_platform("full"))
        self.btn_start = QPushButton("▶ 启动引擎")
        self.btn_start.clicked.connect(self.start_engine)
        self.btn_stop = QPushButton("■ 停止引擎")
        self.btn_stop.clicked.connect(self.stop_engine)
        self.btn_restart = QPushButton("⟳ 重启引擎")
        self.btn_restart.clicked.connect(self.restart_engine)
        self.lb_hint = QLabel("模块2：真业务 + 操作反馈 · v3.db 只读 · 零发布零改价")
        self.lb_hint.setStyleSheet("color:#888")
        bl.addWidget(self.lb_state)
        bl.addStretch(1)
        bl.addWidget(self.lb_hint)
        for b in (self.btn_refresh, self.btn_sync, self.btn_full, self.btn_start,
                  self.btn_stop, self.btn_restart):
            bl.addWidget(b)
        lay.addWidget(bar)

        self.lb_overview = QLabel("—")
        self.lb_overview.setWordWrap(True)
        self.lb_overview.setStyleSheet(
            "background:%s;border:1px solid %s;border-radius:6px;padding:8px;"
            % (theme.CARD_BG, theme.BORDER))
        lay.addWidget(self.lb_overview)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.t_products = self._table(["ID", "标题", "状态", "价(低)", "价(高)",
                                       "主图", "视频", "抖店", "微店", "更新时间"])
        self.t_platform_shops = self._table(
            ["平台", "店铺", "完整 shop_id", "平台商品总数", "已读明细", "数据覆盖率",
             "最近采集时间", "数据新鲜度", "采集状态", "采集错误",
             "在售", "未上架", "回收站", "审核失败/驳回", "来源", "说明"])
        self.t_platform_products = self._table(
            ["店铺", "平台商品ID", "标题", "状态", "价(低)", "库存", "规格",
             "本地商品ID", "匹配", "来源", "源时间", "读取", "备注"])
        self.t_platform_fetches = self._table(
            ["时间", "店铺", "来源", "方法", "成功", "行数", "耗时ms", "错误/说明"])
        # ---- 模块2 真实业务运营 + 操作反馈 ----
        self.t_dy_full = self._table(
            ["商品ID", "标题", "状态原值", "售价", "吊牌价", "库存",
             "本地匹配", "类目", "源时间", "来源"])
        self.t_biz_video = self._table(
            ["#", "类型", "平台", "商品ID", "标题", "状态", "需人工", "说明"])
        self.t_biz_campaign = self._table(
            ["#", "类型", "标题", "来源", "商品数", "售价", "吊牌", "差额", "状态", "说明"])
        self.t_biz_account = self._table(
            ["平台", "名称", "完整ID", "引擎", "登录检查", "最后检查", "启用", "备注"])
        self.t_biz_ops = self._table(
            ["#", "模块", "动作", "状态", "结果", "错误", "开始", "完成", "耗时"])
        # ---- 模块3 每日运营（真巡店 / 今日任务 / 日报）----
        self.t_daily_tasks = self._table(["类", "任务", "依据", "状态"])
        self.t_daily_steps = self._table(["#", "页面", "方式", "回读", "字数", "耗时", "错误"])
        self.t_daily_runs = self._table(["#", "店铺", "登录", "成功步", "搜索", "结果", "时间"])
        self.t_shops = self._table(["平台", "店名", "完整 shop_id", "引擎",
                                    "登录状态", "启用", "备注"])
        self.t_tasks = self._table(["task_id", "kind", "state", "attempts",
                                    "platform", "商品", "创建", "错误"])
        self.t_events = self._table(["时间", "task_id", "kind", "状态转移", "说明"])
        self.t_runs = self._table(["run_id", "task_id", "kind", "worker",
                                   "开始", "结束", "结果"])
        self.t_engine = self._table(["项", "值"])
        self.tab_platform = QWidget()
        _pl = QVBoxLayout(self.tab_platform)
        _pl.setContentsMargins(4, 4, 4, 4)
        _pl.setSpacing(4)
        self.lb_platform = QLabel("平台真实商品数据 · 点顶部「⟳ 采集平台真实数据」刷新")
        self.lb_platform.setWordWrap(True)
        self.lb_platform.setStyleSheet("color:#9aa4b8;font-size:12px")
        _pl.addWidget(self.lb_platform)
        self.subtabs = QTabWidget()
        self.subtabs.setDocumentMode(True)
        self.subtabs.addTab(self.t_platform_shops, "① 三店概览（权威总数/状态）")
        self.subtabs.addTab(self.t_platform_products, "② 商品明细（逐行真数据）")
        self.subtabs.addTab(self.t_platform_fetches, "③ 读取失败与来源记录")
        _pl.addWidget(self.subtabs, 1)
        # ---- 模块2 子页（真按钮 + 真反馈） ----
        self.tab_business = QWidget()
        _bl = QVBoxLayout(self.tab_business)
        _bl.setContentsMargins(4, 4, 4, 4)
        _bl.setSpacing(4)
        bizbar = QWidget()
        _bbl = QHBoxLayout(bizbar)
        _bbl.setContentsMargins(0, 0, 0, 0)
        _bbl.setSpacing(6)
        self.lb_biz = QLabel("模块2 · 真实业务运营（抖店后台 / 短视频 / 活动 / 账号 / 反馈）")
        self.lb_biz.setWordWrap(True)
        self.lb_biz.setStyleSheet("color:#9aa4b8;font-size:12px")
        _bbl.addWidget(self.lb_biz, 1)
        self.btn_biz_collect = QPushButton("⟳ 立即抓一次")
        self.btn_biz_collect.setToolTip(
            "真读抖店后台快照 3846 件 + 微信官方接口，只写 ops.db，零发布零改价")
        self.btn_biz_collect.clicked.connect(
            lambda: self.biz_action("collect"))
        self.btn_biz_account = QPushButton("检查账号状态")
        self.btn_biz_account.clicked.connect(
            lambda: self.biz_action("account.check"))
        self.btn_biz_video = QPushButton("生成视频发布准备")
        self.btn_biz_video.clicked.connect(
            lambda: self.biz_action("video.prepare"))
        self.btn_biz_camp = QPushButton("生成活动/限时限量购资料")
        self.btn_biz_camp.clicked.connect(
            lambda: self.biz_action("campaign.prepare"))
        self.btn_biz_refresh = QPushButton("↻ 刷新")
        self.btn_biz_refresh.clicked.connect(self.load_business)
        for b in (self.btn_biz_collect, self.btn_biz_account, self.btn_biz_video,
                  self.btn_biz_camp, self.btn_biz_refresh):
            _bbl.addWidget(b)
        # ---- 模块3：每日运营（真浏览器巡店 + 今日任务 + 经营日报）----
        self.tab_daily = QWidget()
        _dl = QVBoxLayout(self.tab_daily)
        _dl.setContentsMargins(4, 4, 4, 4)
        _dl.setSpacing(4)
        dbar = QWidget()
        _dbl = QHBoxLayout(dbar)
        _dbl.setContentsMargins(0, 0, 0, 0)
        _dbl.setSpacing(6)
        self.lb_daily = QLabel("每日运营 · 真登录抖店后台巡店 → 回读 → 今日任务 → 经营日报（零发布/零改价，未取得的数据不编造）")
        self.lb_daily.setWordWrap(True)
        self.lb_daily.setStyleSheet("color:#9aa4b8;font-size:12px")
        _dbl.addWidget(self.lb_daily, 1)
        self.btn_daily_patrol = QPushButton("▶ 现在真巡一次店")
        self.btn_daily_patrol.setToolTip("真打开后台、真点菜单、真搜索商品、真回读；全程留步骤与截图")
        self.btn_daily_web = QPushButton("打开经营页面")
        self.btn_daily_refresh = QPushButton("↻ 刷新")
        for b in (self.btn_daily_patrol, self.btn_daily_web, self.btn_daily_refresh):
            _dbl.addWidget(b)
        _dl.addWidget(dbar)
        self.daily_subtabs = QTabWidget()
        self.daily_subtabs.setDocumentMode(True)
        self.daily_subtabs.addTab(self.t_daily_tasks, "① 今日运营任务")
        self.daily_subtabs.addTab(self.t_daily_steps, "② 真实巡店步骤")
        self.daily_subtabs.addTab(self.t_daily_runs, "③ 历次巡店")
        self.txt_daily_report = QTextEdit()
        self.txt_daily_report.setReadOnly(True)
        self.txt_daily_report.setPlaceholderText("今日经营日报（点一次巡店后生成，讲经营结果，不是技术日志）")
        self.daily_subtabs.addTab(self.txt_daily_report, "④ 今日经营日报")
        _dl.addWidget(self.daily_subtabs, 1)
        self.btn_daily_patrol.clicked.connect(self.daily_patrol)
        self.btn_daily_web.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.base + "/api/ops/daily/page")))
        self.btn_daily_refresh.clicked.connect(self.load_daily)
        _bl.addWidget(bizbar)
        self.biz_subtabs = QTabWidget()
        self.biz_subtabs.setDocumentMode(True)
        self.biz_subtabs.addTab(self.t_biz_ops, "① 操作反馈（点了以后怎么样）")
        self.biz_subtabs.addTab(self.t_dy_full, "② 抖店后台真商品明细")
        self.biz_subtabs.addTab(self.t_biz_video, "③ 短视频运营")
        self.biz_subtabs.addTab(self.t_biz_campaign, "④ 限时限量购 · 活动")
        self.biz_subtabs.addTab(self.t_biz_account, "⑤ 账号运营")
        _bl.addWidget(self.biz_subtabs, 1)
        for t, name in ((self.tab_daily, "03 · 每日运营（真巡店/任务/日报）"),
                        (self.tab_business, "02 · 真实业务运营与反馈"),
                        (self.tab_platform, "01 · 双平台真实商品中心"),
                        (self.t_products, "商品运营（v3 只读）"),
                        (self.t_shops, "店铺与账号"),
                        (self.t_tasks, "任务队列"),
                        (self.t_events, "执行记录·状态事件"),
                        (self.t_runs, "执行记录·运行"),
                        (self.t_engine, "引擎详情")):
            self.tabs.addTab(t, name)
        lay.addWidget(self.tabs, 1)

        QTimer.singleShot(500, self.refresh)

    # ---------------- 内部 ----------------
    def _table(self, cols: list) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _get(self, path: str, timeout: float = 2.5):
        req = urllib.request.Request(self.base + path,
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def _rows(self, obj) -> list:
        if isinstance(obj, dict):
            return obj.get("rows") or []
        return obj or []

    def _data(self, obj) -> dict:
        return obj.get("data") if isinstance(obj, dict) else (obj or {})

    @staticmethod
    def _fill(table: QTableWidget, rows: list):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
        table.resizeColumnsToContents()

    def _log(self, s: str):
        try:
            if self.win is not None and hasattr(self.win, "log"):
                self.win.log("[8.0] " + s)
        except Exception:                                    # noqa: BLE001
            pass

    # ---------------- 引擎生命周期 ----------------
    def refresh(self):
        """主窗口切到这个页签时调用：第一次自动起引擎，之后只刷新数据。"""
        if not self._booted:
            self._booted = True
            if not ops_engine.is_alive():
                self.start_engine(silent=True)
        self.reload()

    def start_engine(self, silent: bool = False):
        ok = ops_engine.start_in_thread(worker_on=True)
        if ok:
            self._log("运营引擎已启动 9192")
            if not silent:
                self.lb_state.setText("● 8.0 运营引擎：已启动")
        else:
            self._log("运营引擎启动失败：" + (ops_engine._STATE.get("err") or "未知"))
        QTimer.singleShot(400, self.reload)

    def stop_engine(self):
        ops_engine.request_stop()
        self._log("运营引擎已停止")
        QTimer.singleShot(700, self.reload)

    def restart_engine(self):
        ops_engine.request_stop()
        self._log("运营引擎重启中…")
        QTimer.singleShot(900, lambda: self.start_engine(silent=True))

    def shutdown(self):
        """主窗口 closeEvent 调：引擎跟着主程序一起关，不留孤儿端口。"""
        try:
            ops_engine.request_stop()
            self._log("运营引擎已随主程序关闭")
        except Exception:                                    # noqa: BLE001
            pass

    def stop(self):
        self.shutdown()

    # ---------------- 数据 ----------------
    def reload(self):                                        # noqa: C901
        self.err = ""
        try:
            h = self._get("/api/ops/health")
            self.last["health"] = h
            q = h.get("queue") or {}
            v3 = h.get("v3") or {}
            self.lb_state.setText(
                "● 8.0 运营引擎 %s · %s · 商品 %s（只读） · 店铺 %s · 队列 排队 %s/运行 %s/完成 %s/失败 %s · integrity=%s"
                % (h.get("version"), h.get("listen"), v3.get("products"),
                   h.get("shops"), q.get("queued", 0), q.get("running", 0),
                   q.get("done", 0), q.get("failed", 0), h.get("db_integrity")))
            self.lb_state.setStyleSheet("color:#5ac85a")
            self.lb_overview.setText(
                "运营总览｜引擎 %s ｜ schema %s ｜ ops 表 %s ｜ 商品主库 %s 件（只读 ATTACH）"
                " ｜ 视频 %s ｜ 3 家店铺完整 shop_id ｜ 平台调用 %s ｜ 写入范围 %s"
                % ("在线" if h.get("ok") else "异常", h.get("schema_version"),
                   h.get("ops_tables"), v3.get("products"), v3.get("videos"),
                   h.get("platform_calls"), h.get("writes_scope")))
        except Exception as e:                               # noqa: BLE001
            self.err = "%s: %s" % (type(e).__name__, e)
            self.lb_state.setText("● 8.0 运营引擎未启动（%s）— 1—6.0 不受影响" % self.err[:70])
            self.lb_state.setStyleSheet("color:#e06c6c")
            self.lb_overview.setText("引擎未启动。点「▶ 启动引擎」即可，本页之外一切照常。")
            return

        # 00 总览 / 09 店铺 / 01 商品 / 07 任务 + 执行记录
        try:
            o = self._data(self._get("/api/ops/overview"))
            self.last["overview"] = o
            self._fill(self.t_engine, [
                ["架构", h.get("architecture")],
                ["引擎", h.get("engine")], ["版本", h.get("version")],
                ["实例", h.get("instance")], ["监听", h.get("listen")],
                ["ops.db", h.get("db")], ["ops.db 字节", h.get("db_bytes")],
                ["v3.db", v3.get("path")], ["v3 只读", v3.get("readonly")],
                ["v3 商品数", v3.get("products")], ["v3 视频数", v3.get("videos")],
                ["ops 表", h.get("ops_tables")], ["完整性", h.get("db_integrity")],
                ["执行记录 runs", o.get("runs")], ["状态事件 events", o.get("events")],
                ["视频资产", o.get("video_assets")], ["错误事件", o.get("errors")],
                ["worker", h.get("worker")], ["平台调用", h.get("platform_calls")],
            ])
        except Exception as e:                               # noqa: BLE001
            self.err += " overview:%s" % e

        try:
            p = self._rows(self._get("/api/ops/products?limit=200"))
            self.last["products"] = p
            self._fill(self.t_products, [
                [r.get("product_id"), (r.get("original_title") or "")[:40],
                 r.get("status"), r.get("price_min"), r.get("price_max"),
                 r.get("mains"), r.get("videos"), r.get("dy_state"),
                 r.get("wx_state"), r.get("updated_at")] for r in p])
        except Exception as e:                               # noqa: BLE001
            self.err += " products:%s" % e

        try:
            s = self._data(self._get("/api/ops/shops"))
            shops = s.get("shops") or []
            self.last["shops"] = shops
            self._fill(self.t_shops, [
                [x.get("platform"), x.get("display_name"), x.get("account_id"),
                 x.get("browser_engine"), x.get("login_status"), x.get("enabled"),
                 x.get("note")] for x in shops])
        except Exception as e:                               # noqa: BLE001
            self.err += " shops:%s" % e

        try:
            t = self._rows(self._get("/api/ops/tasks?limit=100"))
            self.last["tasks"] = t
            self._fill(self.t_tasks, [
                [r.get("task_id"), r.get("kind"), r.get("state"), r.get("attempts"),
                 r.get("platform"), r.get("product_id"), r.get("created_at"),
                 (r.get("last_error") or "")[:40]] for r in t])
        except Exception as e:                               # noqa: BLE001
            self.err += " tasks:%s" % e

        try:
            ev = self._rows(self._get("/api/ops/task/events?limit=100"))
            self.last["events"] = ev
            self._fill(self.t_events, [
                [r.get("at_text"), r.get("task_id"), r.get("kind"),
                 "%s → %s" % (r.get("from_state") or "-", r.get("to_state")),
                 (r.get("detail") or "")[:60]] for r in ev])
        except Exception as e:                               # noqa: BLE001
            self.err += " events:%s" % e

        try:
            runs = self._rows(self._get("/api/ops/task/runs?limit=100"))
            self.last["runs"] = runs
            self._fill(self.t_runs, [
                [r.get("run_id"), r.get("task_id"), r.get("kind"), r.get("worker"),
                 r.get("started_at"), r.get("finished_at"), r.get("outcome")]
                for r in runs])
        except Exception as e:                               # noqa: BLE001
            self.err += " runs:%s" % e

        try:
            self.load_platform()
        except Exception as e:                               # noqa: BLE001
            self.err += " platform:%s" % e
        try:
            self.load_business()
        except Exception as e:                               # noqa: BLE001
            self.err += " business:%s" % e
        try:
            self.load_daily()
        except Exception as e:                               # noqa: BLE001
            self.err += " daily:%s" % e

        if self.err:
            self.lb_state.setText(self.lb_state.text() + " · 部分接口异常：" + self.err[:80])

    # ---------------- 模块1：双平台真实商品 ----------------
    def sync_platform(self, mode: str = "snapshot"):
        """点按钮 → POST 9192 起后台采集（真读 9191 官方接口/抖店快照），轮询到结束再回读。
        P0.3.1：mode=snapshot 快速刷新 / mode=full 微信两店全量同步（翻完所有分页）。"""
        self.btn_sync.setEnabled(False)
        self.btn_full.setEnabled(False)
        tag = "全量同步" if mode == "full" else "快速刷新"
        self.lb_platform.setText("%s…（后台真读平台数据，只写 ops.db）" % tag)
        try:
            req = urllib.request.Request(
                self.base + "/api/ops/platform/collect",
                data=json.dumps({"mode": mode}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
        except Exception as e:                               # noqa: BLE001
            self.lb_platform.setText("采集请求失败：%s: %s" % (type(e).__name__, e))
            self.btn_sync.setEnabled(True)
            self.btn_full.setEnabled(True)
            return
        self._sync_polls = 0
        self._poll_sync()

    def _poll_sync(self):
        self._sync_polls = getattr(self, "_sync_polls", 0) + 1
        try:
            st = self._get("/api/ops/platform/status")
        except Exception as e:                               # noqa: BLE001
            self.lb_platform.setText("采集状态读取失败：%s" % e)
            self.btn_sync.setEnabled(True)
            self.btn_full.setEnabled(True)
            return
        if st.get("running") and self._sync_polls < 300:
            tail = (st.get("log") or ["采集中…"])[-1]
            pr = st.get("progress") or {}
            extra = ("（%s 状态%s 第%s页 %s/%s）" % (
                pr.get("shop_name", ""), pr.get("status", ""), pr.get("page", ""),
                pr.get("got", ""), pr.get("total", ""))) if pr else ""
            self.lb_platform.setText("采集中… " + str(tail) + extra)
            QTimer.singleShot(1200, self._poll_sync)
            return
        self.btn_sync.setEnabled(True)
        self.btn_full.setEnabled(True)
        self.lb_platform.setText("采集完成 %s" % (st.get("finished_at") or ""))
        self.load_platform()
        self.load_business()

    def load_platform(self):
        """回读三张平台真实表（只读接口），填三个表格。"""
        try:
            d = self._data(self._get("/api/ops/platform/summary"))
            shops = d.get("shops") or []
            self.last["platform_shops"] = shops
            rows = []
            for x in shops:
                bs = x.get("by_status") or {}
                # P0.3.1 三店 7 字段：总数/明细/覆盖率/采集时间/新鲜度/状态/错误
                rows.append([
                    x.get("platform"), x.get("shop_name"), x.get("shop_id"),
                    "未取得" if x.get("total") is None else x.get("total"),
                    x.get("detail_rows"),
                    x.get("coverage_text") or "未取得",
                    x.get("last_success_at") or x.get("fetched_at") or "—",
                    x.get("freshness_text") or "—",
                    x.get("data_status") or ("成功" if x.get("read_ok") else "失败"),
                    (x.get("error") or "—")[:60],
                    bs.get("5", bs.get("上架", "—")), bs.get("0", bs.get("未上架", "—")),
                    bs.get("6", bs.get("回收站", "—")),
                    bs.get("审核驳回", bs.get("13", "—")),
                    x.get("source_kind"), (x.get("note") or "")[:60]])
            self._fill(self.t_platform_shops, rows)
        except Exception as e:                               # noqa: BLE001
            self.lb_platform.setText("三店概览读取失败：%s" % e)
        try:
            p = self._rows(self._get("/api/ops/platform/products?limit=300"))
            self.last["platform_products"] = p
            self._fill(self.t_platform_products, [
                [r.get("shop_name"), r.get("product_id"), (r.get("title") or "")[:38],
                 r.get("status_text"), r.get("price_min"), r.get("stock_total"),
                 r.get("sku_count"), r.get("local_product_id"),
                 "已匹配" if r.get("match_state") == "matched" else "未匹配",
                 r.get("source_kind"), r.get("source_time") or r.get("fetched_at"),
                 "OK" if r.get("read_ok") else "失败", (r.get("note") or "")[:40]]
                for r in p])
        except Exception as e:                               # noqa: BLE001
            self.err += " platform_products:%s" % e
        try:
            f = self._rows(self._get("/api/ops/platform/fetches?limit=100"))
            self.last["platform_fetches"] = f
            self._fill(self.t_platform_fetches, [
                [r.get("fetched_at"), r.get("shop_id") or "—",
                 (r.get("source") or "")[-52:], r.get("method"),
                 "OK" if r.get("read_ok") else "失败", r.get("rows"),
                 r.get("elapsed_ms"), (r.get("error") or r.get("note") or "")[:70]]
                for r in f])
        except Exception as e:                               # noqa: BLE001
            self.err += " platform_fetches:%s" % e

    # ---------------- 模块2：真实业务运营与操作反馈 ----------------
    def biz_action(self, action: str, payload: dict | None = None):
        """点按钮 → POST 9192 → 立刻回读反馈表。每个动作一条 running→done/failed。"""
        self.lb_biz.setText("已提交 %s，后台执行中…" % action)
        for b in (self.btn_biz_collect, self.btn_biz_account,
                  self.btn_biz_video, self.btn_biz_camp):
            b.setEnabled(False)
        try:
            req = urllib.request.Request(
                self.base + "/api/ops/business/action",
                data=json.dumps({"module": "业务工作台", "action": action,
                                 "payload": payload or {}, "actor": "gui"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode("utf-8") or "{}")
            self.lb_biz.setText(
                ("✔ " if res.get("ok") else "✘ ") +
                str(res.get("result") or res.get("error") or "无回执")[:180])
            self._log("业务动作 %s → %s" % (action, res.get("state")))
        except Exception as e:                               # noqa: BLE001
            self.lb_biz.setText("动作请求失败：%s: %s" % (type(e).__name__, e))
        finally:
            for b in (self.btn_biz_collect, self.btn_biz_account,
                      self.btn_biz_video, self.btn_biz_camp):
                b.setEnabled(True)
            self.load_business()

    def load_business(self):
        """回读模块2 五张真表 + 操作反馈，全从 9192 真接口，不写死。"""
        try:
            st = self._data(self._get("/api/ops/business/snapshot"))
            tm = st.get("timed") or {}
            self.lb_biz.setText(
                "抖店后台真商品 落库 %s 行｜源文件 %s（%s）｜定时采集 %s 次｜下次 %s｜%s"
                % (st.get("db_rows"), st.get("file_mtime") or "—",
                   "浏览器新快照" if st.get("fresh_browser") else "历史全店快照(非实时)",
                   tm.get("runs", 0), tm.get("next_at") or "—",
                   (tm.get("last_result") or "")[:60]))
            self.last["biz_snapshot"] = st
        except Exception as e:                               # noqa: BLE001
            self.err += " biz_snapshot:%s" % e
        try:
            ops = self._rows(self._get("/api/ops/business/feedback?limit=60"))
            self.last["biz_ops"] = ops
            self._fill(self.t_biz_ops, [
                [r.get("op_id"), r.get("module"), r.get("action"), r.get("state"),
                 (r.get("result") or "")[:70], (r.get("error") or "")[:50],
                 r.get("started_at"), r.get("finished_at"), r.get("elapsed_ms")]
                for r in ops])
        except Exception as e:                               # noqa: BLE001
            self.err += " biz_ops:%s" % e
        try:
            p = self._rows(self._get("/api/ops/business/dy_products?limit=200"))
            self.last["dy_full"] = p
            self._fill(self.t_dy_full, [
                [r.get("product_id"), (r.get("title") or "")[:38],
                 r.get("status_code"), r.get("price_min"), r.get("price_max"),
                 r.get("stock_total"),
                 "已匹配" if r.get("match_state") == "matched" else "未匹配",
                 (r.get("note") or "")[:20], r.get("source_time"),
                 r.get("source_kind")] for r in p])
        except Exception as e:                               # noqa: BLE001
            self.err += " dy_full:%s" % e
        try:
            v = self._rows(self._get("/api/ops/business/content?limit=100"))
            self.last["biz_video"] = v
            self._fill(self.t_biz_video, [
                [r.get("pub_id"),
                 "商品详情视频" if r.get("content_type") == "product_head_video"
                 else "内容视频", r.get("platform"), r.get("product_id"),
                 (r.get("title") or "")[:30], r.get("state"),
                 "是" if r.get("need_human") else "否", (r.get("note") or "")[:50]]
                for r in v])
        except Exception as e:                               # noqa: BLE001
            self.err += " biz_video:%s" % e
        try:
            c = self._rows(self._get("/api/ops/business/campaigns?limit=100"))
            self.last["biz_campaign"] = c
            self._fill(self.t_biz_campaign, [
                [r.get("campaign_id"),
                 "限时限量购" if r.get("kind") == "flash_sale" else "规则依据",
                 (r.get("title") or "")[:34], r.get("source_type"),
                 r.get("item_count"), r.get("cost_yuan"), r.get("price_yuan"),
                 r.get("margin"), r.get("state"), (r.get("note") or "")[:40]]
                for r in c])
        except Exception as e:                               # noqa: BLE001
            self.err += " biz_campaign:%s" % e
        try:
            a = self._rows(self._get("/api/ops/business/accounts"))
            self.last["biz_account"] = a
            self._fill(self.t_biz_account, [
                [r.get("platform"), r.get("display_name"), r.get("account_id"),
                 r.get("browser_engine"), r.get("login_status"),
                 r.get("last_check_at"), r.get("enabled"), (r.get("note") or "")[:40]]
                for r in a])
        except Exception as e:                               # noqa: BLE001
            self.err += " biz_account:%s" % e

    # ---------------- 模块3：每日运营（真巡店 / 任务 / 日报）----------------
    def daily_patrol(self):
        """点一下 → 9192 真跑巡店（约 1–3 分钟）→ 立刻回读任务/步骤/日报/反馈。"""
        self.lb_daily.setText("已提交，真浏览器正在巡店（打开后台、点菜单、搜索、回读）…")
        self.btn_daily_patrol.setEnabled(False)
        try:
            req = urllib.request.Request(
                self.base + "/api/ops/daily/patrol",
                data=json.dumps({"actor": "gui"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=320) as r:
                res = json.loads(r.read().decode("utf-8") or "{}")
            self.lb_daily.setText(
                ("✔ " if res.get("ok") else "✘ ") +
                str(res.get("result") or res.get("error") or res.get("state") or "无回执")[:200])
            self._log("每日巡店 → %s" % (res.get("state") or res.get("ok")))
        except Exception as e:                               # noqa: BLE001
            self.lb_daily.setText("巡店请求失败：%s: %s" % (type(e).__name__, e))
        finally:
            self.btn_daily_patrol.setEnabled(True)
            self.load_daily()
            self.load_business()

    def load_daily(self):
        """回读模块3 四张真表，全部来自 9192，不写死。"""
        try:
            ov = self._data(self._get("/api/ops/daily/overview"))
            self.last["daily_overview"] = ov
            self.lb_daily.setText(
                "今日 %s｜AI 已完成 %s 条｜等你确认 %s 条｜人工/不可得 %s 条｜"
                "巡店累计 %s 次｜流量实测数据 %s 条"
                % (ov.get("day"), ov.get("today_auto"), ov.get("today_need_human"),
                   ov.get("today_manual"), ov.get("patrol_runs"),
                   ov.get("metrics_measured")))
        except Exception as e:                               # noqa: BLE001
            self.err += " daily_overview:%s" % e
        try:
            t = self._rows(self._get("/api/ops/daily/tasks?limit=100"))
            self.last["daily_tasks"] = t
            self._fill(self.t_daily_tasks, [
                [r.get("cls"), (r.get("title") or "")[:80],
                 (r.get("evidence") or "")[:40], r.get("state")]
                for r in t])
        except Exception as e:                               # noqa: BLE001
            self.err += " daily_tasks:%s" % e
        try:
            s = self._rows(self._get("/api/ops/daily/steps?limit=60"))
            self.last["daily_steps"] = s
            self._fill(self.t_daily_steps, [
                [r.get("seq"), (r.get("label") or "")[:38], r.get("method"),
                 "成功" if r.get("readback_ok") else "未通过", r.get("body_len"),
                 r.get("elapsed_ms"), (r.get("error") or "")[:40]]
                for r in s])
        except Exception as e:                               # noqa: BLE001
            self.err += " daily_steps:%s" % e
        try:
            rr = self._rows(self._get("/api/ops/daily/runs?limit=30"))
            self.last["daily_runs"] = rr
            self._fill(self.t_daily_runs, [
                [r.get("run_id"), r.get("shop"), r.get("login_state"),
                 "%s/%s" % (r.get("ok_steps"), r.get("total_steps")),
                 "通过" if r.get("search_ok") else "未通过", r.get("state"),
                 r.get("started_at")] for r in rr])
        except Exception as e:                               # noqa: BLE001
            self.err += " daily_runs:%s" % e
        try:
            rp = self._rows(self._get("/api/ops/daily/reports?limit=1"))
            self.txt_daily_report.setPlainText(
                (rp[0].get("body") if rp else "") or
                "还没有日报。点一次「现在真巡一次店」即可生成。")
        except Exception as e:                               # noqa: BLE001
            self.err += " daily_report:%s" % e

    def counts(self) -> dict:
        return {"products": self.t_products.rowCount(),
                "shops": self.t_shops.rowCount(),
                "tasks": self.t_tasks.rowCount(),
                "events": self.t_events.rowCount(),
                "runs": self.t_runs.rowCount(),
                "engine_ok": bool(self.last.get("health", {}).get("ok")),
                                 "platform_shops": self.t_platform_shops.rowCount(),
                 "platform_products": self.t_platform_products.rowCount(),
                 "platform_fetches": self.t_platform_fetches.rowCount(),
                 "dy_full": self.t_dy_full.rowCount(),
                "biz_video": self.t_biz_video.rowCount(),
                "biz_campaign": self.t_biz_campaign.rowCount(),
                "biz_account": self.t_biz_account.rowCount(),
                "biz_ops": self.t_biz_ops.rowCount(),
                "err": self.err}
