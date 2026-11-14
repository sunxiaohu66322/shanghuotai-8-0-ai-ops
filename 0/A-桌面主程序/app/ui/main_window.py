# -*- coding: utf-8 -*-
"""主窗口 —— 布局位置照小猪 PicDetector 原版：

    ┌ 标题条 ──────────────────────────────────────┐
    │ 上货台 · 采集→初筛→打码→出表         ● 状态  │
    ├ 统计卡（小猪 MakeStatCard 那一排）─────────────┤
    │ [采集卡片] [保留] [剔除] [耗时]               │
    ├ 步骤 Tabs（小猪 TabControl 的位置）───────────┤
    │ 1 采集初筛 │ 2 数据包导入 │ 3 检测打码 │ 4 出表 │ 5 设置 │
    │            （各页自己的 Table）                │
    ├ 运行日志（小猪 运行日志 那个位置）─────────────┤
    └──────────────────────────────────────────────┘

不做的事（主人点名删掉的）：店铺管理、以及小猪那几个跟我们无关的页。
绝不打开浏览器 —— 本程序没有一处 QDesktopServices。
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
    QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .. import bridge, paths, theme
from . import buildstamp, tabs as T
from . import widgets as W


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = bridge.load_settings()

        self.setWindowTitle(f"{paths.APP_NAME} v{paths.APP_VER}")
        # 任务栏/标题栏图标：不设的话 Windows 会显示 pythonw.exe 的 Python 图标
        try:
            from .. import boot
            _ic = boot.icon_path()
            if _ic:
                self.setWindowIcon(QIcon(str(_ic)))
        except Exception:
            pass
        self.resize(theme.DEFAULT_W, theme.DEFAULT_H)
        self.setMinimumSize(theme.MIN_W, theme.MIN_H)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- 标题条 ----------
        header = QWidget()
        header.setObjectName("Header")
        header.setFixedHeight(52)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 8, 16, 8)

        box = QVBoxLayout()
        box.setSpacing(0)
        t = QLabel(f"{paths.APP_NAME}")
        t.setObjectName("AppTitle")
        s = QLabel("采集 → 数据包 → 商品库 → 精筛 → 检测打码 → 出表　·　设置点右上角 ⚙，不上货")
        s.setObjectName("AppSub")
        box.addWidget(t)
        box.addWidget(s)
        hl.addLayout(box)
        hl.addStretch(1)

        # ---------- 构建状态灯 + 一键重启（2026-11-09 主人要求）----------
        #   背景：桌面有两个图标 ——「源码版」直读 app/*.py，「上货台.lnk」读半个月前
        #   打包的 EXE。Python 改动不会热加载进运行中的进程，打包版重启一百次也拿不到
        #   新代码。这三样必须让主人一眼看见，不能靠「关了重开」去猜。
        self.lb_build = QToolButton()
        self.lb_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lb_build.setToolTip("点一下看详情：现在读的是哪套代码、是不是最新")
        self.lb_build.setStyleSheet(
            f"QToolButton{{border:1px solid {theme.BORDER}; border-radius:6px;"
            f" padding:6px 10px; background:{theme.CARD_BG}; color:{theme.TEXT};"
            f" font-size:11.5px;}}"
            f"QToolButton:hover{{border-color:{theme.PRIMARY};}}")
        self.lb_build.clicked.connect(self._show_build_detail)
        hl.addWidget(self.lb_build)
        hl.addSpacing(6)

        self.btn_restart = QToolButton()
        self.btn_restart.setText("⟳ 重启")
        self.btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart.setStyleSheet(
            f"QToolButton{{border:1px solid {theme.BORDER}; border-radius:6px;"
            f" padding:6px 12px; background:{theme.CARD_BG}; color:{theme.TEXT};}}"
            f"QToolButton:hover{{border-color:{theme.PRIMARY}; color:{theme.PRIMARY};}}")
        self.btn_restart.clicked.connect(self._do_restart)
        hl.addWidget(self.btn_restart)
        hl.addSpacing(8)
        self._refresh_build_status()
        self._build_timer = QTimer(self)
        self._build_timer.timeout.connect(self._refresh_build_status)
        self._build_timer.start(30000)   # 我在这边改文件，30 秒内灯自己变黄

        # 设置不再常驻右侧（P1 验收要求）—— 收进齿轮弹窗，功能一个不删
        self.btn_settings = QToolButton()
        self.btn_settings.setText("⚙ 设置")
        self.btn_settings.setToolTip("本机服务状态 / 路径 / 图床 / 词库 / 环境信息")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet(
            f"QToolButton{{border:1px solid {theme.BORDER}; border-radius:6px;"
            f" padding:6px 12px; background:{theme.CARD_BG}; color:{theme.TEXT};}}"
            f"QToolButton:hover{{border-color:{theme.PRIMARY}; color:{theme.PRIMARY};}}")
        self.btn_settings.clicked.connect(self._open_settings)
        hl.addWidget(self.btn_settings)
        hl.addSpacing(8)

        self.lb_status = QLabel("● 待机")
        self.lb_status.setObjectName("StatusDot")
        hl.addWidget(self.lb_status)
        root.addWidget(header)

        # 2026-11-10 主人指令：顶部「全店 / 上架中 / 未上架」一排总统计卡整体移除。
        #   原话「这个数据不要放这里，放了没用、纯粹浪费时间；我应该是要三个店、
        #   每个店独立的数据；把这个数据放到运营软件里面，然后再做每个商品的独立状态卡」。
        #   展示层已迁到运营软件（微信小店自建上货台 127.0.0.1:9191 → #/ops/shops）。
        #   真实商品库 / v3.db 一行未动：清的是「显示」，不是「数据」。

        # ---------- 统计卡（采集流水线自己那组，保留不动） ----------
        stats_wrap = QWidget()
        sl = QHBoxLayout(stats_wrap)
        sl.setContentsMargins(12, 10, 12, 6)
        sl.setSpacing(8)
        self.c_cards = W.StatCard("采集卡片", "—", theme.PRIMARY_LIGHT, theme.PRIMARY)
        self.c_keep = W.StatCard("保留 · 可上货", "—", theme.SUCCESS_LIGHT, theme.SUCCESS)
        self.c_drop = W.StatCard("剔除 · 不要", "—", theme.DANGER_LIGHT, theme.DANGER)
        self.c_ms = W.StatCard("耗时", "—", theme.BLUE_LIGHT, theme.BLUE)
        for c in (self.c_cards, self.c_keep, self.c_drop, self.c_ms):
            sl.addWidget(c)
        root.addWidget(stats_wrap)

        # ---------- 步骤 Tabs ----------
        # 第 6 步「上货流水线」已删：上货是用别的软件做的，这个程序不管上货。
        # 第 7 步「设置」不再占页签 —— 改成右侧抽屉（SettingsPanel）。
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.t_collect = T.CollectTab(self)
        self.t_pack = T.PackTab(self)
        from .tab_fine import FineStepTab  # 延迟导入：第 3 步精筛
        self.t_fine = FineStepTab(self)
        # 第 4/5 步是新写的页，缺了也不许让整个程序起不来 —— 退回旧页
        try:
            from .tab_detect import DetectStepTab
            self.t_detect = DetectStepTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_detect = T.DetectTab(self)
            self._detect_old = True
        try:
            from .tab_export import ExportStepTab
            self.t_export = ExportStepTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_export = T.ExportTab(self)
        # 5.0 · 直接上架（微信小店直连）：**不出表、不走图床**，直接建商品。
        # ★ 2026-10-26：原来「5 · 出表」往后挪成 5.5，把 5.0 让给直连上架 ——
        #   两条路并存：5.0 走官方接口，5.5 仍走 19 列模板（给小猪/服务商用）。
        #   缺了不许让整个程序起不来 —— 直接不显示这个页签。
        try:
            from .tab_publish import PublishTab
            self.t_publish = PublishTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_publish = None
            self.log(f"5.0 直接上架未启用：{type(_e).__name__}: {_e}")
        # 2.5 · 本地商品库（照片墙）：第二步与第三步之间，只读看档案、挑商品
        # 缺了也不许让整个程序起不来 —— 直接不显示这个页签
        try:
            from .tab_wall import WallStepTab
            self.t_wall = WallStepTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_wall = None
            self.log(f"2.5 商品库页未启用：{type(_e).__name__}: {_e}")
        # 2.5.1 · 下载监控：原来是「悬浮窗」，主人口径改成页签
        #   「先做一个 tab 页，放到新界面里面，不要做一个悬浮窗了。
        #    做了悬浮窗反而让整个系统卡 bug 一样的卡在那了。」
        # 缺了也不影响其他页
        try:
            from .tab_dlmon import DlMonTab
            self.t_dlmon = DlMonTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_dlmon = None
            self.log(f"2.5.1 下载监控未启用：{type(_e).__name__}: {_e}")
        # 4.1 · 主图人工审核：按「图片」看主图 + 内置局部修补编辑器（阶段 C）
        # 4.9 · 主图 AI 重做：原来的 4.1 后移，功能与数据关系都不变
        try:
            from .tab_wall_main import MainWallTab
            self.t_wall_main = MainWallTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_wall_main = None
            self.log(f"4.1 主图人工审核未启用：{type(_e).__name__}: {_e}")
        try:
            from .tab_lab import LabStepTab
            self.t_lab = LabStepTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_lab = None
            self.log(f"4.9 主图 AI 重做未启用：{type(_e).__name__}: {_e}")
        # 6.0 · 微店后台管理（6.0）：把「微信小店自建上货台」那套 6.0 内嵌进主窗口。
        #   ★ 2026-10-27 主人口径：**不要第二套软件**，主程序才是唯一入口；
        #   服务由 wxserve 在本进程内拉起，退出时由 closeEvent 收掉，不留孤儿端口。
        #   缺了（比如没装 WebEngine）不许让整个程序起不来 —— 直接不显示这个页签。
        try:
            from .tab_wx import WxAdminTab
            self.t_wx = WxAdminTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_wx = None
            self.log(f"6.0 微店后台管理未启用：{type(_e).__name__}: {_e}")
        # 8.0 · AI 运营中心（2026-11-10）：独立 ops.db + v3.db 只读 ATTACH。
        #   数据全来自本机 9192 ops_engine；引擎没起只影响这一页，1—6.0 不受影响。
        #   照 2.5/6.0 的写法用 try 挂载，失败只写日志、不拖垮主窗口。
        try:
            from .tab_ops import OpsCenterTab
            self.t_ops = OpsCenterTab(self)
        except Exception as _e:  # noqa: BLE001
            self.t_ops = None
            self.log(f"8.0 AI 运营中心未启用：{type(_e).__name__}: {_e}")

        _pages = [
            (self.t_collect, "1 · 采集初筛"),
            (self.t_pack, "2 · 商品卡导入"),
        ]
        if getattr(self, "t_wall", None) is not None:
            _pages.append((self.t_wall, "2.5 · 商品库"))
        if getattr(self, "t_dlmon", None) is not None:
            _pages.append((self.t_dlmon, "2.5.1 · 下载监控"))
        _pages += [
            (self.t_fine, "3 · 商品精筛"),
            (self.t_detect, "4 · 检测打码"),
        ]
        if getattr(self, "t_wall_main", None) is not None:
            _pages.append((self.t_wall_main, "4.1 · 主图人工审核"))
        if getattr(self, "t_lab", None) is not None:
            _pages.append((self.t_lab, "4.9 · 主图 AI 重做"))
        if getattr(self, "t_publish", None) is not None:
            _pages.append((self.t_publish, "5.0 · 直接上架"))
        _pages.append((self.t_export, "5.5 · 出表"))
        # 6.0 排在 5.5 之后（原有序列一个不动，只往后加）
        if getattr(self, "t_wx", None) is not None:
            _pages.append((self.t_wx, "6.0 · 微店后台管理"))
        # 8.0 排在 6.0 之后（原有序列一个不动，只往后加）
        if getattr(self, "t_ops", None) is not None:
            _pages.append((self.t_ops, "8.0 · AI 运营中心"))
        for tab, name in _pages:
            self.tabs.addTab(tab, name)
        self.tabs.currentChanged.connect(self._on_tab)

        # ---------- 右侧设置抽屉 ----------
        try:
            from .settings_panel import SettingsPanel
            self.settings_panel = SettingsPanel(self)
        except Exception as _e:  # noqa: BLE001
            self.settings_panel = T.SettingsTab(self)
            self.settings_panel.setMinimumWidth(260)
            self.settings_panel.setMaximumWidth(340)
        self.t_settings = self.settings_panel   # 老代码里有 t_settings.refresh()

        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(self.tabs)
        body.setStretchFactor(0, 1)
        body.setChildrenCollapsible(False)
        root.addWidget(body, 1)

        # ---------- 日志 ----------
        log_wrap = QWidget()
        ll = QVBoxLayout(log_wrap)
        ll.setContentsMargins(12, 6, 12, 8)
        ll.setSpacing(4)
        cap = QLabel("运行日志")
        cap.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px; font-weight:600;")
        ll.addWidget(cap)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(104)
        self.log_view.setMaximumBlockCount(2000)
        ll.addWidget(self.log_view)
        root.addWidget(log_wrap)

        self.statusBar().showMessage("就绪")

        self.log(f"{paths.APP_NAME} 启动 · 后端目录 {paths.rel(paths.TAOBAO_SERVER)}")
        n = len(bridge.env_report())
        bad = [r[0] for r in bridge.env_report() if not r[2]]
        self.log(f"环境自检 {n} 项，缺失 {len(bad)} 项" + (f"：{'、'.join(bad)}" if bad else ""))
        # 4.1 / 4.9 就绪度（写进日志文件，出问题能事后翻）
        try:
            if getattr(self, "t_wall_main", None) is None:
                self.log("4.1 主图人工审核：页签未加载")
            elif getattr(self.t_wall_main, "err", ""):
                self.log("4.1 主图人工审核不可用：%s" % self.t_wall_main.err)
            else:
                self.log("4.1 主图人工审核就绪（照片墙 + 局部修补，写库前自动备份）")
            if getattr(self, "t_lab", None) is None:
                self.log("4.9 主图 AI 重做：页签未加载")
            else:
                st = bridge.lab_status()
                if st.get("ok"):
                    self.log("4.9 主图 AI 重做就绪：%d 种模式 · 已花 ¥%.4f · 库 %s"
                             % (len(st.get("modes") or []), float(st.get("cost_total") or 0),
                                paths.rel(st.get("db") or "")))
                else:
                    self.log(f"4.9 主图 AI 重做不可用：{st.get('error')}")
        except Exception as _e:  # noqa: BLE001
            self.log(f"4.1/4.9 自检异常：{type(_e).__name__}: {_e}")

        # 上次没跑完的下载任务：启动时收成「已暂停」，界面里可一键继续。
        # 不收的话它会永远显示"下载中"，而线程早就随进程没了（假状态比没有更糟）。
        try:
            rc = bridge.v3_dl_recover()
            if rc.get("ok") and rc.get("recovered"):
                for x in rc["recovered"]:
                    self.log("下载任务 #%s 上次未跑完：%d 个文件已置暂停，可在 2.5 的下载中心点「继续」"
                             % (x.get("job_id"), x.get("paused_tasks") or 0))
        except Exception as _e:  # noqa: BLE001
            self.log(f"下载任务恢复检查异常：{type(_e).__name__}: {_e}")

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(5000)
        # 跨线程日志排队（后台线程写日志 → 回主线程再动控件）
        self._log_queued.connect(self._log_ui, Qt.ConnectionType.QueuedConnection)
        self._probe = {"up": None, "cdp": None, "ts": 0.0}
        self._start_probe()
        QTimer.singleShot(200, self._first_load)
        # ★ 2026-10-27：6.0 的服务不再等用户点进那个页签才起。
        #   主人实测「打不开」时，根因是端口在听、应答线程已死 —— 而它只在点页签时才起，
        #   所以「进都没进」和「进去了但死了」在界面上长得一模一样。
        #   现在：启动 3 秒后在后台把它拉起来，并由 wxserve 自己的守护线程每 20 秒体检。
        QTimer.singleShot(3000, self._boot_wx_bg)
        # 2026-11-10：8.0 运营引擎随主程序启动（进程内线程，退出时 closeEvent 收掉）。
        #   比 6.0 晚 200ms，避免两个本地服务同时抢启动瞬间的 CPU。
        QTimer.singleShot(3200, self._boot_ops_bg)
        # 2026-11-10：顶部店铺数据总统计已移除，不再起 10 分钟定时拉取线程。
        #   店铺/商品数据改在运营软件（9191 → #/ops/shops）按店独立查看。
        # 性能自测钩子：只在设置了 SHANGHUOTAI_PERF=1 时运行，生产默认关闭。
        # 目的：让「切到 2.5 到底几毫秒」在**正式 exe**里可测可复核（不是脚本里的数字）。
        if os.environ.get("SHANGHUOTAI_PERF") == "1":
            QTimer.singleShot(2500, self._perf_selftest)

    # ---------- 对外 ----------
    _log_queued = pyqtSignal(str)

    def log(self, s: str):
        """★ 2026-10-27：跨线程保护。

        后台线程（批量消除 worker / 预热线程）会调 api.log ——
        直接 appendPlainText 是**在非 GUI 线程碰控件**，Qt 会 access violation
        把整个进程打死（crashguard 抓到过 wall_main._mark_log → logwin）。
        这里同样走排队信号回主线程，调用方无感。
        """
        if QThread.currentThread() is not self.thread():
            try:
                self._log_queued.emit(str(s))
            except Exception:                      # noqa: BLE001
                pass
            return
        self._log_ui(str(s))

    def _log_ui(self, s: str):
        self.log_view.appendPlainText(f"[{paths.now_str()[11:]}] {s}")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 落盘：界面里的日志关窗就没了，出了问题要能翻（改名保留一份上一次的）
        try:
            f = paths.LOG_DIR / "run.log"
            if f.exists() and f.stat().st_size > 2 * 1024 * 1024:
                f.replace(paths.LOG_DIR / "run.1.log")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"[{paths.now_str()}] {s}\n")
        except Exception:      # noqa: BLE001
            pass

    def set_stats(self, cards=None, keep=None, drop=None, ms=None):
        if cards is not None:
            self.c_cards.set_value(cards)
        if keep is not None:
            self.c_keep.set_value(keep)
        if drop is not None:
            self.c_drop.set_value(drop)
        if ms is not None:
            self.c_ms.set_value(f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms")

    # 2026-11-10：店铺总统计的取数 / 排队回填 / 刷新日志三个方法一并移除。
    #   数据展示已迁到运营软件（9191），按三个店各自独立、且每商品一张状态卡。
    #   本文件不再向 /api/overview 发起轮询；商品库与 v3.db 保持原样。

    # ---------- 构建状态 / 一键重启（2026-11-09） ----------
    def _refresh_build_status(self):
        """只读刷新右上角那颗灯：源码 mtime vs 本进程启动时间 / EXE mtime。"""
        try:
            s = buildstamp.status(getattr(self, "_proc_started_ui", None))
        except Exception as e:                  # noqa: BLE001
            self.lb_build.setText("● 状态未知")
            self.lb_build.setToolTip("构建状态读取失败：%s" % e)
            return
        fresh = not s["needs_restart"]
        self.lb_build.setText(("✅ 最新 · " if fresh else "⚠ 待重启 · ") + s["mode"])
        self.btn_restart.setToolTip(s["hint"])
        self.lb_build.setToolTip(
            "%s\n%s\n点一下看完整详情" % (s["summary"], s["hint"]))
        self.lb_build.setStyleSheet(
            f"QToolButton{{border:1px solid "
            f"{theme.SUCCESS if fresh else theme.WARNING}; border-radius:6px;"
            f" padding:6px 10px; background:"
            f"{theme.SUCCESS_LIGHT if fresh else theme.WARNING_LIGHT};"
            f" color:{theme.TEXT}; font-size:11.5px;}}"
            f"QToolButton:hover{{border-color:{theme.PRIMARY};}}")

    def _show_build_detail(self):
        """点状态灯 → 把「到底读的是哪套代码」一次讲清，不摆 A/B 选项。"""
        s = buildstamp.status(getattr(self, "_proc_started_ui", None))
        argv, why = buildstamp.restart_plan()
        txt = (
            "%s\n\n"
            "运行模式：%s\n"
            "当前程序：%s\n"
            "本进程启动：%s\n"
            "源码最后改动：%s（%s）\n"
            % (s["summary"], s["mode"], s["exe"], buildstamp.fmt(s["proc_started"]),
               buildstamp.fmt(s["src_mtime"]), s["src_file"]))
        if s["frozen"]:
            txt += "打包 EXE 生成：%s\n" % buildstamp.fmt(s["exe_mtime"])
        txt += (
            "\n改动会不会直接生效：不会。程序运行后代码已在内存里，改文件不影响它，\n"
            "必须重启进程重新读入。\n"
            "点「⟳ 重启」会：%s。\n" % why)
        if argv is None:
            txt += "\n⚠ 找不到可启动的入口，重启暂时不可用。"
        elif s["stale_pkg"]:
            txt += (
                "\n⚠ 你现在这个打包版停在 %s，源码 %s 之后改过。\n"
                "重启这个 EXE 一百次也还是旧的；按钮会改用「源码版」入口启动。\n"
                "以后请固定用桌面上那个「上货台（源码版·改动立刻生效）」图标。"
                % (buildstamp.fmt(s["exe_mtime"]), buildstamp.fmt(s["src_mtime"])))
        QMessageBox.information(self, "上货台 · 当前代码状态", txt)

    def _do_restart(self):
        """一键重启：先拉起新进程，再关自己。closeEvent 会收掉 6.0 服务与 LaMa 子进程。"""
        s = buildstamp.status(getattr(self, "_proc_started_ui", None))
        argv, why = buildstamp.restart_plan()
        if argv is None:
            QMessageBox.warning(self, "重启", why)
            return
        fresh = not s["needs_restart"]
        ok = QMessageBox.question(
            self, "重启上货台",
            ("%s\n\n现在：%s\n%s\n\n重启会关闭当前窗口并重新打开，"
             "未保存的界面状态会重置（数据库/商品不受影响）。\n继续？"
             % (why, s["summary"], "当前已是最新，确认要重开一次吗？" if fresh
                else "当前不是最新，重启后才能用到新代码。")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ok != QMessageBox.StandardButton.Yes:
            return
        if not buildstamp.spawn(argv):
            QMessageBox.critical(self, "重启失败",
                                 "新进程没能拉起，当前窗口保持不动。\n入口：%s" % " ".join(argv))
            return
        self.log("一键重启：已拉起 %s（%s），本窗口即将关闭" % (" ".join(argv), why))
        QApplication.instance().quit()

    # ---------- 内部 ----------
    def _open_settings(self):
        """设置弹窗：原来是右侧常驻面板，P1 要求收进齿轮里（功能不删）。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("设置 · 本机状态与参数")
        dlg.resize(420, 640)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        panel = self.settings_panel
        panel.setParent(dlg)
        panel.setMinimumWidth(0); panel.setMaximumWidth(16777215)
        lay.addWidget(panel)
        try:
            panel.refresh()
        except Exception:                     # noqa: BLE001
            pass
        dlg.exec()
        # 弹窗关掉后把面板还给主窗口，避免下一次打开时已被销毁
        panel.setParent(self)

    def _perf_selftest(self):
        """在正式 exe 里量：切到 2.5 / 点开详情 / 换视图 的真实耗时，写日志后退出。

        只在 SHANGHUOTAI_PERF=1 时被调度。写入 data\\logs\\wall_perf.log，一行一项。
        """
        import time
        lines = ["=== 上货台 %s 2.5 商品库性能自测（正式 exe）===" % paths.APP_VER,
                 "时间: %s" % paths.now_str(),
                 "库: %s" % os.path.join(str(paths.TAOBAO_DIR), "data", "v3.db")]
        try:
            w = getattr(self, "t_wall", None)
            if w is None or getattr(w, "page", None) is None:
                lines.append("2.5 页未就绪：%s" % getattr(w, "err", "未加载"))
            else:
                page = w.page
                idx = self.tabs.indexOf(w)
                # 先切到别的页，再切回 2.5 —— 量的就是「切页」这件事
                self.tabs.setCurrentIndex(0)
                for _ in range(3):
                    QApplication.processEvents()
                t0 = time.perf_counter()
                self.tabs.setCurrentIndex(idx)
                for _ in range(3):
                    QApplication.processEvents()
                lines.append("切换到 2.5 页签            : %.1f ms" % ((time.perf_counter() - t0) * 1000))
                lines.append("当前筛选结果件数            : %d 件（已建卡 %d）"
                             % (len(page.rows), page._shown))
                t0 = time.perf_counter(); page.reload()
                for _ in range(2):
                    QApplication.processEvents()
                lines.append("页内 F5 重新查询（全量条件） : %.1f ms" % ((time.perf_counter() - t0) * 1000))
                if page.rows:
                    pid = int(page.rows[0]["product_id"])
                    t0 = time.perf_counter(); page._show_detail(pid)
                    for _ in range(2):
                        QApplication.processEvents()
                    lines.append("点开第一件详情（首次解码）  : %.1f ms"
                                 % ((time.perf_counter() - t0) * 1000))
                    t0 = time.perf_counter(); page._show_detail(pid)
                    QApplication.processEvents()
                    lines.append("再点同一件（命中缓存）      : %.1f ms"
                                 % ((time.perf_counter() - t0) * 1000))
                t0 = time.perf_counter(); page._load_more()
                for _ in range(2):
                    QApplication.processEvents()
                lines.append("滚动加载下一批 +60 件       : %.1f ms（累计 %d）"
                             % ((time.perf_counter() - t0) * 1000, page._shown))
                t0 = time.perf_counter(); page._on_size(240)
                for _ in range(2):
                    QApplication.processEvents()
                lines.append("缩略图尺寸切到 240px        : %.1f ms"
                             % ((time.perf_counter() - t0) * 1000))
                t0 = time.perf_counter(); page._set_view(1)
                for _ in range(2):
                    QApplication.processEvents()
                lines.append("切列表视图                  : %.1f ms"
                             % ((time.perf_counter() - t0) * 1000))
                lines.append("page.perf(): %s" % page.perf())
        except Exception as ex:                # noqa: BLE001
            lines.append("自测异常：%s: %s" % (type(ex).__name__, ex))
        try:
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(paths.LOG_DIR / "wall_perf.log", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception:                      # noqa: BLE001
            pass
        QApplication.quit()

    def _first_load(self):
        try:
            self.t_pack.refresh()
            self.t_export.refresh()
            if getattr(self, "t_publish", None) is not None:
                self.t_publish.refresh()
            self.t_settings.refresh()
            self.t_detect.refresh()
            # ★ 2026-10-25 主人口径：商品库要跟着商品卡导入一起更新。
            #   原来这里漏了 t_wall，切页签时又只有 tab_wall.refresh() 的一次性门闩
            #   ⇒ 新导入的 6 件在 2.5 里看不到，必须手动 F5 或重开程序。
            if getattr(self, "t_wall", None) is not None:
                self.t_wall.refresh()
            if getattr(self, "t_dlmon", None) is not None:
                self.t_dlmon.refresh()
            if getattr(self, "t_wall_main", None) is not None:
                self.t_wall_main.refresh()
            if getattr(self, "t_lab", None) is not None:
                self.t_lab.refresh()
        except Exception as e:  # noqa: BLE001
            self.log(f"初次加载某项失败：{e}")

    def _on_tab(self, idx: int):
        w = self.tabs.widget(idx)
        if hasattr(w, "refresh"):
            try:
                w.refresh()
            except Exception as e:  # noqa: BLE001
                self.log(f"刷新「{self.tabs.tabText(idx)}」失败：{e}")

    def _tick(self):
        """只渲染缓存，绝不在界面线程探活（P0-1 卡顿根因，2026-10-25）。

        改前实测：`port_open(9109)` 411ms + `find_cdp_port()` 407ms
        —— 每 5 秒硬卡界面约 0.8 秒（端口离线时最坏 2.8 秒），
        表现就是「点一下没反应、拖窗口发顿」。现在这两件事在后台线程做。
        """
        try:
            p = self._probe
            up, det = p.get("up"), p.get("cdp")
            bits = []
            bits.append("9109 在线" if up else ("9109 离线" if up is not None else "9109 探测中"))
            bits.append(("浏览器口 %s" % det) if det else
                        ("浏览器口 未找到" if det is False else "浏览器口 探测中"))
            self.lb_status.setText("● " + " · ".join(bits))
            self.lb_status.setStyleSheet(
                f"color:{theme.SUCCESS if up else theme.TEXT_SUB}; font-size:12px;")
        except Exception:  # noqa: BLE001
            pass

    def _boot_wx_bg(self):
        """后台把 6.0 的服务拉起来 —— 放线程里做，界面不许等 HTTP 健康检查。"""
        import threading

        tw = getattr(self, "t_wx", None)
        if tw is None:
            return

        def run():
            try:
                ok = tw.serve.ensure()
                base = tw.serve.base
            except Exception as e:                     # noqa: BLE001
                self.log("6.0 服务自动启动异常：%s: %s" % (type(e).__name__, e))
                return
            if ok:
                self.log("6.0 服务已在后台就绪：%s（每 20 秒自检，死了自己重启）" % base)
            else:
                self.log("6.0 服务没能起来：%s" % (tw.serve.err or "原因未给"))

        threading.Thread(target=run, name="wx60-boot", daemon=True).start()

    def _boot_ops_bg(self):
        """后台把 8.0 运营引擎（9192）拉起来 —— 界面不等它。"""
        import threading

        tw = getattr(self, "t_ops", None)
        if tw is None:
            return

        def run():
            try:
                from ..ops import engine as ops_engine
                if ops_engine.is_alive():
                    self.log("8.0 运营引擎已在运行（9192），本进程接管监控")
                    return
                ok = ops_engine.start_in_thread(worker_on=True)
                if ok:
                    self.log("8.0 运营引擎已在后台就绪：http://127.0.0.1:9192/（v3.db 只读）")
                else:
                    self.log("8.0 运营引擎没能起来：%s"
                             % (ops_engine._STATE.get("err") or "原因未给"))
            except Exception as e:                     # noqa: BLE001
                self.log("8.0 运营引擎自动启动异常：%s: %s" % (type(e).__name__, e))

        threading.Thread(target=run, name="ops80-boot", daemon=True).start()

    def _start_probe(self):
        """后台探活：10 秒一轮，结果写进 self._probe，界面只读。"""
        import threading

        self._probe = {"up": None, "cdp": None, "ts": 0.0}
        port = int(self.settings.get("cdp_port", 9222))

        def loop():
            while True:
                up = False
                cdp = None
                try:
                    up = bridge.port_open(9109, timeout=0.15)
                    cdp = bridge.find_cdp_port(port, timeout=0.15)
                except Exception:  # noqa: BLE001
                    up, cdp = False, None
                self._probe = {"up": bool(up), "cdp": cdp,
                               "ts": __import__("time").time()}
                __import__("time").sleep(10)

        threading.Thread(target=loop, name="dsh-probe", daemon=True).start()

    def closeEvent(self, e):  # noqa: N802, D102
        try:
            bridge.save_settings(self.settings)
        except Exception:  # noqa: BLE001
            pass
        # 6.0 的内嵌服务必须跟着主程序一起关，否则会留下占着端口的孤儿
        try:
            w = getattr(self, "t_wx", None)
            if w is not None and hasattr(w, "shutdown"):
                # ★ 2026-10-28：先按 Qt 要求的顺序拆 WebEngine（停加载→解绑→摘父级→
                #   deleteLater→跑事件循环），再关本地服务。原来只关服务、不拆视图，
                #   页面还在加载时销毁视图会崩在 Qt6Gui（事件日志 0xc0000005）。
                w.shutdown()
                self.log("6.0 内嵌页与服务已随主程序关闭")
            elif w is not None and hasattr(w, "stop"):
                w.stop()
                self.log("6.0 服务已随主程序关闭")
        except Exception:  # noqa: BLE001
            pass
        # 8.0 运营引擎（9192）必须跟着主程序一起关，否则留下孤儿端口
        try:
            w = getattr(self, "t_ops", None)
            if w is not None and hasattr(w, "shutdown"):
                w.shutdown()
                self.log("8.0 运营引擎已随主程序关闭")
        except Exception:  # noqa: BLE001
            pass
        # LaMa 修补子进程也要跟着关：它占着显存，留着会变成抢卡的孤儿
        try:
            from .. import lama_ipc
            lama_ipc.stop()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(e)



