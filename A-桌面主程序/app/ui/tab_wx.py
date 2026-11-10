# -*- coding: utf-8 -*-
"""6.0 · 微店后台管理（主窗口内的一个页签，不是第二套软件）。

构成：一条工具栏（服务状态 / 刷新 / 重启 / 定位商品）+ 一个内嵌 WebEngine 视图。
Web 侧就是 6.0 那套（小猪风格左侧菜单 + 右侧工作区），服务由 wxserve 在本进程拉起。

跨序列：5.0 直接上架写进 v3_publish.external_listing_id 的映射在这里可查，
选中一条即可让 6.0 直接定位到那件商品（按微店商品 ID，不按标题）。
"""
from __future__ import annotations

import sqlite3

from PyQt6.QtCore import QUrl, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .. import paths, theme
from ..wxserve import SixtyWeb

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    _HAS_WEB = True
except Exception as _we:                              # noqa: BLE001
    _WEB_ERR = "%s: %s" % (type(_we).__name__, _we)
    QWebEngineView = None                             # type: ignore
    QWebEngineProfile = None                          # type: ignore
    _HAS_WEB = False

V3_DB = paths.TAOBAO_DIR / "data" / "v3.db"


class WxAdminTab(QWidget):
    def __init__(self, win=None):
        super().__init__(win)
        self.win = win
        self.serve = SixtyWeb()
        self.err = ""
        self._booted = False
        self._recovered = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(38)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 4, 10, 4)
        bl.setSpacing(8)

        self.lb = QLabel("● 6.0 服务：未启动")
        self.lb.setStyleSheet("font-size:12px;color:%s;" % theme.TEXT_SUB)
        bl.addWidget(self.lb)

        self.btn_reload = self._mk("↻ 刷新")
        self.btn_reload.clicked.connect(self.reload_view)
        bl.addWidget(self.btn_reload)

        self.btn_restart = self._mk("⟳ 重启服务")
        self.btn_restart.clicked.connect(self.restart)
        bl.addWidget(self.btn_restart)

        bl.addSpacing(12)
        bl.addWidget(QLabel("定位微店商品："))
        self.cb_map = QComboBox()
        self.cb_map.setMinimumWidth(300)
        self.cb_map.setToolTip("来自 5.0 直接上架写入的映射（v3_publish.external_listing_id）")
        bl.addWidget(self.cb_map)
        self.btn_open = self._mk("打开")
        self.btn_open.clicked.connect(self._open_selected)
        bl.addWidget(self.btn_open)

        self.ed_pid = QLineEdit()
        self.ed_pid.setPlaceholderText("或直接粘微店商品 ID")
        self.ed_pid.setFixedWidth(190)
        self.ed_pid.returnPressed.connect(lambda: self.open_product(self.ed_pid.text().strip()))
        bl.addWidget(self.ed_pid)

        bl.addStretch(1)
        lay.addWidget(bar)

        if _HAS_WEB:
            prof = QWebEngineProfile("wx60", self)
            try:
                prof.setPersistentStoragePath(str(paths.ROOT / "data" / "webprofile"))
                prof.setCachePath(str(paths.ROOT / "data" / "webprofile" / "cache"))
            except Exception:                          # noqa: BLE001
                pass
            self.view = QWebEngineView(self)
            lay.addWidget(self.view, 1)
            self._watch_view()
        else:
            self.view = None
            tip = QLabel(
                "内嵌浏览器组件未安装（PyQt6-WebEngine）。\n"
                "安装命令：pip install PyQt6-WebEngine\n"
                "装好后重开本程序即可。")
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tip.setStyleSheet("color:%s;font-size:13px;" % theme.TEXT_SUB)
            lay.addWidget(tip, 1)

        self._load_map()

    # ---------------- 对外 ----------------
    def refresh(self):
        """主窗口切到这个页签时调用：只在第一次真正启动服务。"""
        if not _HAS_WEB:
            return
        if not self._booted:
            self._boot()
        elif self.view is not None and self.view.url().isEmpty():
            self.reload_view()

    def reload_view(self):
        if not _HAS_WEB:
            return
        # ★ 用 ensure 而不是 start：start 只在「没起来」时起，假死（端口在听但不应答）
        #   它看不出来 —— 那正是用户说的「打不开，但端口明明在」。
        if not self.serve.ensure():
            self._show_err()
            return
        base = self.serve.base
        self._booted = True
        self._show_ok(base)
        if self.view is not None:
            url = self.view.url()
            if url.isEmpty() or not url.toString().startswith(base):
                self.view.load(QUrl(base + "/#/home"))
            else:
                self.view.reload()

    def _show_ok(self, base: str):
        n = getattr(self.serve, "restarts", 0)
        self.lb.setText("● 6.0 服务：%s%s" % (base, ("（自愈 %d 次）" % n) if n else ""))
        self.lb.setStyleSheet("font-size:12px;color:%s;" % theme.SUCCESS)

    def restart(self):
        self.serve.stop()
        self._booted = False
        self.lb.setText("● 6.0 服务：重启中…")
        if self.view is not None:
            self.view.setHtml("<body style='font:13px Microsoft YaHei;padding:24px'>正在重启 6.0 服务…</body>")
        QTimer.singleShot(200, self._boot)

    def open_product(self, pid: str):
        """让 6.0 的批量修改页按微店商品 ID 定位（页内 #/edit-stock?focus=ID）。"""
        pid = (pid or "").strip()
        if not pid or not _HAS_WEB:
            return
        base = self.serve.start()
        if not base or self.view is None:
            self._show_err()
            return
        self.view.load(QUrl("%s/#/edit-stock?focus=%s" % (base, pid)))
        self._booted = True

    def shutdown(self):
        """**【关闭顺序】** 主窗口退出时调这个，不要只调 stop()。

        为什么：事件日志里有一次 `Qt6Gui.dll 0xc0000005`（访问冲突）。
        QWebEngineView 在**页面还在加载 / 渲染进程还活着**的时候被直接销毁，
        是 Qt WebEngine 上最常见的退出期崩溃 —— 正确顺序必须是：
            停加载 → 解绑页面 → 从父级摘掉 → deleteLater → 让事件循环真的跑掉
        最后才关本地服务（服务先关会让页面里的请求报错、渲染进程异常退出）。
        全部包在 try 里：任何一步失败都不许拦住程序退出。
        """
        v = getattr(self, "view", None)
        if v is not None:
            for step in ("stop", ):
                try:
                    getattr(v, step)()
                except Exception:                      # noqa: BLE001
                    pass
            try:                                       # 解绑页面（先断开渲染进程与 view）
                v.setPage(None)                        # type: ignore[arg-type]
            except Exception:                          # noqa: BLE001
                pass
            try:
                pg = v.page()
                if pg is not None:
                    pg.deleteLater()
            except Exception:                          # noqa: BLE001
                pass
            try:
                v.setParent(None)
            except Exception:                          # noqa: BLE001
                pass
            try:
                v.deleteLater()
            except Exception:                          # noqa: BLE001
                pass
            self.view = None                           # 之后任何代码碰 view 都拿到 None
            try:
                app = QApplication.instance()
                if app is not None:
                    app.processEvents()                # 让上面的 deleteLater 真的执行掉
            except Exception:                          # noqa: BLE001
                pass
        try:
            self.serve.close()
        except Exception:                              # noqa: BLE001
            pass

    def stop(self):
        """兼容旧调用：等同 shutdown()（旧的只关服务不拆 WebEngine，退出时可能崩）。"""
        self.shutdown()

    # ---------------- 加载监视 / 白屏自检 ----------------
    # 2026-10-29：用户报「6.0 打得开但一片白」。
    # 当时服务端 HTTP 200、静态资源全 200、独立 WebEngine 里能正常渲染，
    # 但内嵌视图白屏且 run.log 一个字都没有 —— 查不出来，因为没有观测点。
    # 这段的目的就一个：把「加载/崩溃/是否白屏」写进日志，并在白屏时自己救一次。

    def _log(self, s: str):
        try:
            if self.win is not None and hasattr(self.win, "log"):
                self.win.log("[6.0] " + s)
        except Exception:                              # noqa: BLE001
            pass

    def _watch_view(self):
        v = self.view
        if v is None:
            return
        try:
            v.loadStarted.connect(lambda: self._log("开始加载 %s" % v.url().toString()))
            v.loadProgress.connect(lambda p: self._log("加载进度 %d%%" % p) if p in (50, 100) else None)
            v.loadFinished.connect(self._on_loaded)
            v.renderProcessTerminated.connect(self._on_render_dead)
        except Exception as e:                         # noqa: BLE001
            self._log("绑定加载信号失败：%s: %s" % (type(e).__name__, e))

    def _on_render_dead(self, status=None, code=0):
        self._log("渲染进程终止（status=%s code=%s）→ 1 秒后重新加载" % (status, code))
        QTimer.singleShot(1000, self.reload_view)

    def _on_loaded(self, ok):
        self._log("加载完成 ok=%s url=%s" % (ok, self.view.url().toString() if self.view else "-"))
        if ok:
            QTimer.singleShot(3500, self._selfcheck)

    def _selfcheck(self):
        """页面骨架总是会立刻出现（HTML 里写死），所以只看"有没有白"分辨不出问题。
        真正的判据是：JS 有没有跑起来、把左侧菜单渲染出来（#nav 有子节点）。"""
        if self.view is None:
            return
        js = "((document.getElementById('nav')||{}).innerHTML||'').length"
        try:
            self.view.page().runJavaScript(js, self._after_check)
        except Exception as e:                         # noqa: BLE001
            self._log("自检脚本执行失败：%s" % e)

    def _after_check(self, n):
        try:
            n = int(n or 0)
        except Exception:                              # noqa: BLE001
            n = 0
        self._log("自检：左侧菜单节点长度 %d%s" % (n, "（渲染正常）" if n > 0 else "（白屏）"))
        if n > 0 or self._recovered:
            if n == 0:
                self._log("已自动恢复过一次仍为空 —— 点上方「⟳ 重启服务」，仍不行看 data\\logs\\run.log")
            return
        self._recovered = True
        self._log("检出白屏 → 清 WebEngine 缓存后重载一次")
        self._purge_profile()
        base = self.serve.base or ""
        if self.view is not None and base:
            self.view.load(QUrl(base + "/#/home"))

    def _purge_profile(self):
        try:
            import shutil
            d = paths.ROOT / "data" / "webprofile" / "cache"
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            self._log("缓存已清并重建：%s" % d)
        except Exception as e:                         # noqa: BLE001
            self._log("清缓存失败：%s: %s" % (type(e).__name__, e))

    # ---------------- 内部 ----------------
    def _mk(self, text):
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton{border:1px solid %s;border-radius:6px;padding:4px 10px;"
            "background:%s;color:%s;font-size:12px;}"
            "QPushButton:hover{border-color:%s;color:%s;}"
            % (theme.BORDER, theme.CARD_BG, theme.TEXT, theme.PRIMARY, theme.PRIMARY))
        return b

    def _boot(self):
        if not self.serve.ensure():
            self._booted = True
            self._show_err()
            return
        base = self.serve.base
        self._booted = True
        self._show_ok(base)
        if self.view is not None:
            self.view.load(QUrl(base + "/#/home"))

    def _show_err(self):
        self.err = self.serve.err or "启动失败"
        self.lb.setText("● 6.0 服务：起不来（%s）" % self.err[:60])
        self.lb.setStyleSheet("font-size:12px;color:%s;" % theme.DANGER)
        if self.view is not None:
            self.view.setHtml(
                "<body style='font:13px Microsoft YaHei;padding:24px;line-height:1.9'>"
                "<b>6.0 服务没有起来</b><br>%s<br><br>"
                "点上方「⟳ 重启服务」重试；仍不行看 data\\logs\\run.log。" % self.err)

    def _load_map(self):
        """5.0 发布的映射：本地商品 → 微店商品 ID（真数据，读不到就留空）。"""
        self.cb_map.clear()
        rows = []
        try:
            c = sqlite3.connect("file:%s?mode=ro" % str(V3_DB).replace("\\", "/"), uri=True)
            rows = list(c.execute(
                "select p.product_id, p.external_listing_id, coalesce(v.original_title,'') "
                "from v3_publish p left join v3_product v on v.product_id=p.product_id "
                "where coalesce(p.external_listing_id,'')<>'' order by p.record_id desc limit 50"))
            c.close()
        except Exception as e:                          # noqa: BLE001
            self.cb_map.addItem("（读映射失败：%s）" % type(e).__name__, "")
            return
        if not rows:
            self.cb_map.addItem("（5.0 还没发布过商品）", "")
            return
        for pid, ext, title in rows:
            label = "#%s → %s %s" % (pid, ext, (title or "")[:22])
            self.cb_map.addItem(label, str(ext))

    def _open_selected(self):
        pid = self.cb_map.currentData()
        if not pid:
            self.win.log("6.0 定位：当前下拉没有可用的微店商品 ID") if self.win else None
            return
        self.ed_pid.setText(str(pid))
        self.open_product(str(pid))

    def summary(self) -> str:
        return "6.0 服务 %s" % (self.serve.base or "未启动")

