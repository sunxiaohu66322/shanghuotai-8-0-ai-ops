# -*- coding: utf-8 -*-
"""5 · 出表 —— 顺序是硬的：**先上传图床替换数据库，才允许生成表格**。

为什么必须先上传：导出的表格只认 URL，本地路径进不去。库里 `new_remote_url` 还是空的
就出表，等于交一半空链接的废表 —— 所以「生成表格」在 missing>0 时是禁用的，
不是弹个提示糊弄过去。
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel,
                             QMessageBox, QProgressBar, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import bridge, theme
from .tabs import StepTab


class UpStatusWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):  # noqa: D102
        self.done.emit(bridge.upload_status(self.code))


class UpWorker(QThread):
    prog = pyqtSignal(int, int, int, int)
    done = pyqtSignal(dict)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):  # noqa: D102
        self.done.emit(bridge.upload_pending(self.code, progress=self.prog.emit))


class WxStatWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):  # noqa: D102
        self.done.emit(bridge.upload_wx_status(self.code))


class WxUpWorker(QThread):
    """把要用的图全部换成微信图床地址（mmecimage）。可中断。"""

    prog = pyqtSignal(int, int, int, int)
    done = pyqtSignal(dict)

    def __init__(self, code: str, limit: int = 0):
        super().__init__()
        self.code = code
        self.limit = limit
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):  # noqa: D102
        self.done.emit(bridge.upload_wx(self.code, limit=self.limit,
                                        progress=self.prog.emit,
                                        should_stop=lambda: self._stop))


class UrlWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):  # noqa: D102
        self.done.emit(bridge.v3_image_urls(self.code))


class ExpWorker(QThread):
    """一次出两样东西，都来自 v3 库（不再是喂 14 列 CSV 给小猪）：

    ① 上传包  packaging.export_package(pids)  —— 逐商品的清单与图
    ② 19 列表 pack.export_pig_csv(包码)        —— 对齐 importCreate.csv 模板的 GBK 单表

    ②才是「能直接导进微信小店后台」的那张表；①只是本地留档。两条都跑，
    哪条失败只报哪条，不把另一条的成功当成失败（历史上 export 误报就是这么来的）。
    """

    done = pyqtSignal(dict)

    def __init__(self, code: str, pids: list):
        super().__init__()
        self.code = code
        self.pids = pids

    def run(self):  # noqa: D102
        out: dict = {"ok": False, "files": [], "errors": []}
        try:
            pkg = bridge.v3_export_package(self.pids or None)
        except Exception as e:  # noqa: BLE001
            pkg = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if pkg.get("ok"):
            out["ok"] = True
            out["package"] = pkg.get("path") or ""
            if pkg.get("path"):
                out["files"].append(pkg["path"])
        else:
            out["errors"].append("上传包：" + str(pkg.get("error"))[:200])

        pig = {"ok": False, "error": "没选数据包"}
        if self.code:
            try:
                pig = bridge.v3_pack_to_pig(self.code)
            except Exception as e:  # noqa: BLE001
                pig = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        if pig.get("ok"):
            out["ok"] = True
            out["pig_csv"] = pig.get("path") or ""
            out["rows"] = pig.get("rows")
            out["pig_summary"] = pig.get("summary") or {}
            if pig.get("path"):
                out["files"].append(pig["path"])
        else:
            out["errors"].append("19 列表：" + str(pig.get("error"))[:200])

        if not out["ok"]:
            out["error"] = "；".join(out["errors"])[:400]
        elif out["errors"]:
            out["warning"] = "；".join(out["errors"])[:400]
        self.done.emit(out)


class DyExcelWorker(QThread):
    """Excel 路线一键出表：抖音采集计划 plan-*.json → 小猪 16 列标准模板。

    和上面那些 worker 不是一条链：它们出的是 v3 库里的商品与数据包，
    这个读的是 抖店研究\\crawl3 下的采集计划，一行一个 SKU，图片一律换成图床公网地址。
    """

    prog = pyqtSignal(int, int, str)
    done = pyqtSignal(dict)

    def __init__(self, min_sku: int, fill_images: bool = True):
        super().__init__()
        self.min_sku = min_sku
        self.fill_images = fill_images

    def run(self):  # noqa: D102
        try:
            r = bridge.excel_route_export(
                min_sku=self.min_sku, fill_images=self.fill_images,
                progress=lambda d, t, name: self.prog.emit(d, t, name))
        except Exception as e:      # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "items": []}
        self.done.emit(r)


class ExportStepTab(StepTab):
    title = "5.5 · 出表"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self._code = ""
        self._missing = -1
        self._pids: list[int] = []
        self._w = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        tip = QLabel("出表里的图片地址必须是**公网能拉的链接**（抖音二次上架是拿链接去拉图）。"
                     "推荐先把所有图换成微信图床 mmecimage 的永久链接：点「☁ 全部换成微信地址」，"
                     "换完表里就是微信地址。缺地址时「生成表格」是禁用的。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(tip)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("数据包"))
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(240)
        self.cmb.currentIndexChanged.connect(self._changed)
        bar.addWidget(self.cmb)

        # 主线：全部换成微信图床地址（mmecimage 永久链接）
        self.btn_wx = QPushButton("☁ 全部换成微信地址")
        self.btn_wx.setObjectName("Primary")
        self.btn_wx.setToolTip(
            "把 main/轮播/详情/SKU 图逐张传到微信图床（api.weixin.qq.com → mmecimage.cn），\n"
            "写回 v3_image.current_remote_url。已经有微信地址的自动跳过，\n"
            "所以可以反复点、也可以中途停止再继续。\n"
            "视频不是图片，微信图片接口不收，会跳过并如实计数。")
        self.btn_wx.clicked.connect(self._wx_upload)
        bar.addWidget(self.btn_wx)

        self.btn_wx_stop = QPushButton("⏹ 停")
        self.btn_wx_stop.setEnabled(False)
        self.btn_wx_stop.clicked.connect(self._wx_stop)
        bar.addWidget(self.btn_wx_stop)

        # 旧的「只补缺」通道保留（原图 URL 能用的就不动）
        self.btn_up = QPushButton("☁ 只补缺地址")
        self.btn_up.setToolTip("旧逻辑：只把「没有可用远程地址 / 被替换过还没地址」的图传上去，\n"
                               "原图地址能用的不动。要表里全是微信地址请用左边那个。")
        self.btn_up.clicked.connect(self._upload)
        bar.addWidget(self.btn_up)

        self.btn_urls = QPushButton("🔗 导出图片地址清单")
        self.btn_urls.setToolTip("一图一行：商品ID / 图片ID / 角色 / 是否微信图床 / 出表将用的地址 / 本地文件")
        self.btn_urls.clicked.connect(self._urls)
        bar.addWidget(self.btn_urls)

        self.btn_exp = QPushButton("📄 生成表格")
        self.btn_exp.clicked.connect(self._export)
        bar.addWidget(self.btn_exp)

        # ★ 2026-10-25 第四批：抖店 16 列（一 SKU 一行，和微信 19 列不是一套东西）
        self.btn_dd = QPushButton("🏪 导出抖店 16 列")
        self.btn_dd.setToolTip("以真实抖店模板为基底：保留说明行与表头，一 SKU 一行；"
                               "被替换但没上传的图会留空并列出原因，不会拿原图顶上")
        self.btn_dd.clicked.connect(self._export_dd)
        bar.addWidget(self.btn_dd)

        # ★ Excel 路线（2026-11-02）：采集计划 → 小猪 16 列，一键全量导出，不用再一个个导
        bar.addWidget(QLabel("SKU≥"))
        self.sp_dysku = QSpinBox()
        self.sp_dysku.setRange(1, 999)
        self.sp_dysku.setValue(7)
        self.sp_dysku.setFixedWidth(64)
        self.sp_dysku.setToolTip("只导出 SKU 数 ≥ 这个值的采集计划。\n"
                                 "推荐 7：SKU 少的走手工上架，SKU 多的走 Excel 批量。\n"
                                 "填 1 就是全部导出。")
        bar.addWidget(self.sp_dysku)

        self.btn_dyex = QPushButton("🧩 一键导出多规格 Excel")
        self.btn_dyex.setObjectName("Primary")
        self.btn_dyex.setToolTip(
            "把 抖店研究\\crawl3 下的 plan-*.json 一次全部导成小猪标准模板 xlsx：\n"
            "· 表头在第 2 行、16 列，与官方 UPLOAD_DEMO1.xlsx 逐字一致\n"
            "· 一个 SKU 一行，每行都写商品ID（文本格式，防 15 位被舍入）\n"
            "· 图片一律换公网图床地址，缺的现场补传，拿不到就留空并点名\n"
            "· SKU规格名称 = 规格1 + 空格 + 规格2\n"
            "产出：上货台\\data\\excel-out\\dy-<pid>.xlsx + _报告.md（逐商品清单）")
        self.btn_dyex.clicked.connect(self._dy_excel)
        bar.addWidget(self.btn_dyex)

        self.btn_open = QPushButton("打开出表目录")
        self.btn_open.clicked.connect(self._open_dir)
        bar.addWidget(self.btn_open)

        # 完整上货包（2026-10-25 第五批）：表格 + 真实媒体 + 清单 + 检查报告
        self.btn_pkg = QPushButton("📦 生成完整上货包")
        self.btn_pkg.setObjectName("Primary")
        self.btn_pkg.setToolTip(
            "把**当前确认版本**的主图/轮播/详情/SKU图/视频真实复制进一个文件夹，\n"
            "连同微信 19 列、抖店 16 列、商品媒体对应清单、待上传清单、检查报告一起给出。\n"
            "被替换但还没上传的媒体会在表里留空并标注，不会用原图顶替。")
        self.btn_pkg.clicked.connect(self._bundle)
        bar.addWidget(self.btn_pkg)

        # 类目映射（D-35 的人工入口，2026-10-25 第五批）
        self.btn_cat = QPushButton("📋 类目映射")
        self.btn_cat.setToolTip(
            "来源卡里只有类目 ID（如 6932/7117/546488），没有中文名。\n"
            "这里把来源类目对到微信/抖店的目标类目名；没映射的商品会被导出检查点名，\n"
            "不会被塞一个默认分类。")
        self.btn_cat.clicked.connect(self._open_cat)
        bar.addWidget(self.btn_cat)
        bar.addStretch(1)
        root.addLayout(bar)

        self.pb = QProgressBar()
        self.pb.setRange(0, 100)
        self.pb.setFormat("%v%")
        root.addWidget(self.pb)

        self.lb = QLabel("—")
        self.lb.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
        self.lb.setWordWrap(True)
        root.addWidget(self.lb)

        self.lb_wx = QLabel("图片来源：正在检查微信图床…")
        self.lb_wx.setWordWrap(True)
        self.lb_wx.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(self.lb_wx)

        self.tb = QTableWidget(0, 4)
        self.tb.setHorizontalHeaderLabels(["商品ID", "角色", "本地文件", "缺什么"])
        self.tb.verticalHeader().setVisible(False)
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.tb, 1)

    # ---------------- 数据 ----------------
    def refresh(self):
        """出表下拉：与 4.0 同口径，默认选「筛选后包」，未通过商品不进表。"""
        cur = self.cmb.currentData()
        r = bridge.v3_packs()
        items = (r.get("items") or []) if isinstance(r, dict) else []
        try:
            ch = bridge.v3_screen_pack_choices() or {}
            screen_codes = {str(x.get("pack_code") or "")
                            for x in (ch.get("choices") or [])}
            default = str(ch.get("default") or "")
        except Exception:                     # noqa: BLE001
            screen_codes, default = set(), ""
        self.cmb.blockSignals(True)
        self.cmb.clear()
        for it in items:
            code = it.get("pack_code") or ""
            st = bridge.v3_pack_status(code)
            if st.get("ok") and not st.get("ready"):
                continue
            mark = "✅ 筛选后 · " if code in screen_codes else "⚠ 未筛原包 · "
            self.cmb.addItem("%s%s（%s 件）"
                             % (mark, code, it.get("products_live") or 0), code)
        self.cmb.blockSignals(False)
        pick = ""
        if cur:
            i = self.cmb.findData(cur)
            if i >= 0:
                pick = cur
        if not pick and default:
            i = self.cmb.findData(default)
            if i >= 0:
                pick = default
        if pick:
            i = self.cmb.findData(pick)
            if i >= 0:
                self.cmb.setCurrentIndex(i)
        self._changed()

    def _changed(self):
        self._code = self.cmb.currentData() or ""
        if not self._code:
            self.lb.setText("先在第 2 步导入一个包")
            self.lb_wx.setText("图片来源：先选数据包")
            self.btn_up.setEnabled(False)
            self.btn_wx.setEnabled(False)
            self.btn_urls.setEnabled(False)
            self.btn_exp.setEnabled(False)
            return
        self._pids = bridge.v3_pack_pids(self._code).get("pids") or []
        self.btn_up.setEnabled(True)
        self.btn_urls.setEnabled(True)
        self._refresh_status()
        self._refresh_wx()

    def _refresh_status(self):
        if not self._code:
            return
        self.lb.setText("正在盘点缺图床地址的图…")
        self._w = UpStatusWorker(self._code)
        self._w.done.connect(self._got_status)
        self._w.start()

    def _got_status(self, r: dict):
        if not r.get("ok"):
            self._missing = -1
            self.lb.setText("盘点失败：%s" % str(r.get("error"))[:160])
            self.btn_exp.setEnabled(False)
            self.tb.setRowCount(0)
            return
        self._missing = int(r.get("missing") or 0)
        host = r.get("host") or ""
        if self._missing == 0:
            self.lb.setText("✓ 图床已就绪（%s）—— 全部图都有远程地址，可以生成表格了" % host)
            self.lb.setStyleSheet(f"color:{theme.SUCCESS}; font-size:13px; font-weight:600;")
            self.btn_exp.setEnabled(True)
        else:
            self.lb.setText("还有 %d 张图没有图床地址 —— 先点「上传图床替换数据库」，"
                            "传完才能出表（%s）" % (self._missing, host))
            self.lb.setStyleSheet(f"color:{theme.DANGER}; font-size:13px; font-weight:600;")
            self.btn_exp.setEnabled(False)
        rows = r.get("rows") or []
        self.tb.setRowCount(len(rows))
        for i, it in enumerate(rows):
            self.tb.setItem(i, 0, QTableWidgetItem(str(it.get("product_id") or "")))
            self.tb.setItem(i, 1, QTableWidgetItem(str(it.get("role") or "")))
            self.tb.setItem(i, 2, QTableWidgetItem(str(it.get("local_path") or "（无）")))
            self.tb.setItem(i, 3, QTableWidgetItem(str(it.get("reason") or "")))

    # ---------------- 微信图床状态 ----------------
    def _refresh_wx(self):
        if not self._code:
            self.lb_wx.setText("图片来源：先选数据包")
            return
        self.lb_wx.setText("图片来源：正在检查微信图床与待换张数…")
        self._wxst = WxStatWorker(self._code)
        self._wxst.done.connect(self._got_wx)
        self._wxst.start()

    def _got_wx(self, r: dict):
        if not r.get("ok"):
            self.lb_wx.setText("微信图床状态查不出来：%s" % str(r.get("error"))[:160])
            return
        todo = int(r.get("todo") or 0)
        done = int(r.get("already_wx") or 0)
        noloc = int(r.get("no_local") or 0)
        vid = int(r.get("video_skipped") or 0)
        ok = bool(r.get("wx_ok"))
        head = ("微信图床可用 → 出表用 mmecimage 永久链接" if ok
                else "微信图床不可用（%s）" % str(r.get("wx_msg"))[:100])
        tail = ("已换微信 %d 张 · 待换 %d 张" % (done, todo))
        if noloc:
            tail += " · %d 张无本地文件" % noloc
        if vid:
            tail += " · %d 段视频（图片接口不收，跳过）" % vid
        self.lb_wx.setText("图片来源：%s ｜ %s" % (head, tail))
        color = theme.SUCCESS if (ok and todo == 0) else (
            theme.WARNING if ok else theme.DANGER)
        self.lb_wx.setStyleSheet(f"color:{color}; font-size:12px;")
        self.btn_wx.setEnabled(ok)
        self.btn_wx.setText("☁ 全部换成微信地址（%d 张）" % todo if todo
                            else "☁ 全部已换成微信地址")

    # ---------------- 动作 ----------------
    def _wx_upload(self):
        if not self._code:
            return
        n = self._todo_wx()
        ans = QMessageBox.question(
            self, "确认换成微信地址",
            "把 %s 张图逐张传到微信图床（api.weixin.qq.com → mmecimage.cn），\n"
            "并把返回的永久链接写回数据库。\n\n"
            "· 已经有微信地址的会自动跳过，可反复点\n"
            "· 中途可点「⏹ 停」，已传的保留\n"
            "· 每张约 0.5–1 秒，量大时会跑比较久\n\n继续？" % (n if n >= 0 else "待统计"))
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.btn_wx.setEnabled(False)
        self.btn_wx_stop.setEnabled(True)
        self.log("开始换成微信图床地址…")
        self._wxw = WxUpWorker(self._code)
        self._wxw.prog.connect(self._wx_prog)
        self._wxw.done.connect(self._wx_done)
        self._wxw.start()

    def _todo_wx(self) -> int:
        t = self.lb_wx.text()
        import re as _re  # noqa: PLC0415
        m = _re.search(r"待换 (\d+) 张", t)
        return int(m.group(1)) if m else -1

    def _wx_stop(self):
        w = getattr(self, "_wxw", None)
        if w and w.isRunning():
            w.stop()
            self.log("已请求停止 —— 当前这张传完就停")
            self.btn_wx_stop.setEnabled(False)

    def _wx_prog(self, n: int, total: int, ok: int, fail: int):
        self.pb.setValue(int(n * 100 / total) if total else 0)
        self.pb.setFormat("换地址 %d/%d · 成功 %d 失败 %d" % (n, total, ok, fail))

    def _wx_done(self, r: dict):
        self.btn_wx_stop.setEnabled(False)
        self.pb.setFormat("%v%")
        if not r.get("ok"):
            self.log("✗ 换地址失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "换地址没做成", str(r.get("error"))[:300])
            self._refresh_wx()
            return
        self.log("✓ 本轮换地址：共 %s · 成功 %s · 失败 %s · 跳过 %s%s"
                 % (r.get("total"), r.get("uploaded"), r.get("failed"),
                    r.get("skipped"), "（已中断）" if r.get("stopped") else ""))
        self.log("   仍待换 %s 张 · 无本地文件 %s 张 · 视频跳过 %s 段 · 已是微信 %s 张"
                 % (r.get("remain"), r.get("no_local"), r.get("video_skipped"),
                    r.get("already_wx")))
        for e in (r.get("errors") or [])[:8]:
            self.log("   " + str(e)[:160])
        self._refresh_wx()
        self._refresh_status()

    def _urls(self):
        if not self._code:
            QMessageBox.information(self, "还没有数据包", "先选一个数据包。")
            return
        self.btn_urls.setEnabled(False)
        self.log("导出图片地址清单…")
        self._uw = UrlWorker(self._code)
        self._uw.done.connect(self._urls_done)
        self._uw.start()

    def _urls_done(self, r: dict):
        self.btn_urls.setEnabled(True)
        if not r.get("ok"):
            self.log("✗ 地址清单失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "地址清单没做成", str(r.get("error"))[:300])
            return
        st = r.get("stat") or {}
        self.log("✓ 地址清单：%s 张图 · 已微信 %s · 非微信 %s"
                 % (st.get("总图数"), st.get("已微信"), st.get("非微信(腾讯/自建/空)")))
        self.log("   产出：" + str(r.get("path") or ""))
        QMessageBox.information(
            self, "地址清单已导出",
            "总图 %s 张\n已微信图床 %s 张\n非微信（腾讯/自建/空）%s 张\n\n%s"
            % (st.get("总图数"), st.get("已微信"), st.get("非微信(腾讯/自建/空)"),
               r.get("path") or ""))

    # ---------------- 动作 ----------------
    def _upload(self):
        if not self._code:
            return
        if self._missing == 0:
            self.log("没有需要上传的图（全部已有地址）")
            return
        if self._missing < 0:
            QMessageBox.information(self, "先刷新", "还没盘点出缺哪些图，等一下再点。")
            return
        ok = QMessageBox.question(
            self, "确认上传",
            "把 %d 张缺地址的图传到你自己的图床，并把地址写回数据库？\n\n"
            "这是真上传（会走网络，每张间隔 0.2 秒）。" % self._missing)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.btn_up.setEnabled(False)
        self.btn_exp.setEnabled(False)
        self.log("开始上传 %d 张图…" % self._missing)
        self._w = UpWorker(self._code)
        self._w.prog.connect(self._prog)
        self._w.done.connect(self._up_done)
        self._w.start()

    def _prog(self, n: int, total: int, ok: int, fail: int):
        self.pb.setValue(int(n * 100 / total) if total else 0)
        self.pb.setFormat("上传 %d/%d · 成功 %d 失败 %d" % (n, total, ok, fail))

    def _up_done(self, r: dict):
        self.btn_up.setEnabled(True)
        self.pb.setFormat("%v%")
        if not r.get("ok"):
            self.log("✗ 上传失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "上传没做成", str(r.get("error"))[:300])
        else:
            self.log("✓ 上传完成：共 %s · 成功 %s · 失败 %s · 跳过 %s"
                     % (r.get("total"), r.get("uploaded"), r.get("failed"), r.get("skipped")))
            for e in (r.get("errors") or [])[:8]:
                self.log("   " + str(e)[:160])
        self._refresh_status()

    def _export(self):
        if self._missing != 0:
            QMessageBox.information(self, "还不能出表",
                                    "还有图没有图床地址。先点「上传图床替换数据库」。")
            return
        self.btn_exp.setEnabled(False)
        self.log("生成表格：%d 个商品 · 数据包「%s」…" % (len(self._pids), self._code))
        self._w = ExpWorker(self._code, self._pids)
        self._w.done.connect(self._exp_done)
        self._w.start()

    def _exp_done(self, r: dict):
        self.btn_exp.setEnabled(True)
        if not r.get("ok"):
            self.log("✗ 出表失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "出表没做成", str(r.get("error"))[:300])
            return
        files = r.get("files") or []
        self.log("✓ 已出表：19 列 %s 行" % (r.get("rows") if r.get("rows") is not None else "—"))
        ps = r.get("pig_summary") or {}
        if ps:
            self.log("   逐列非空：" + " · ".join("%s %s" % (k, v) for k, v in list(ps.items())[:8]))
        for f in files:
            self.log("   产出：" + str(f))
        if r.get("warning"):
            self.log("   ⚠ 部分未成：" + str(r["warning"]))
        QMessageBox.information(self, "出表完成",
                                "19 列表：%s 行\n目录：%s\n\n%s"
                                % (r.get("rows") if r.get("rows") is not None else "—",
                                   bridge.v3_out_dir(),
                                   "\n".join(str(x) for x in files[:6])))

    # ---------------------------------------------------------- 多规格 Excel（小猪路线）
    def _dy_excel(self):
        n = self.sp_dysku.value()
        self.btn_dyex.setEnabled(False)
        self.pb.setRange(0, 0)
        self.lb.setText("Excel 路线：导出 SKU≥%d 的采集计划…" % n)
        self.log("━━ Excel 路线：SKU≥%d 的采集计划 → 小猪 16 列模板 ━━" % n)
        self._w = DyExcelWorker(n, True)
        self._w.prog.connect(self._dy_excel_prog)
        self._w.done.connect(self._dy_excel_done)
        self._w.start()

    def _dy_excel_prog(self, d: int, t: int, name: str):
        self.pb.setRange(0, max(t, 1))
        self.pb.setValue(d)
        self.pb.setFormat("%v/%m")
        self.lb.setText("Excel 路线：%s（%d/%d）" % (name, d + 1, t))

    def _dy_excel_done(self, r: dict):
        self.btn_dyex.setEnabled(True)
        self.pb.setRange(0, 100)
        self.pb.setValue(100 if r.get("ok") else 0)
        self.pb.setFormat("%v%")
        if r.get("error"):
            self.log("✗ Excel 路线失败：" + str(r["error"])[:200])
            QMessageBox.warning(self, "导出没做成", str(r["error"])[:400])
            return
        for it in (r.get("items") or []):
            if it.get("error"):
                self.log("   ✗ %s  %s" % (it.get("pid"), str(it["error"])[:90]))
            else:
                self.log("   ✓ %-6s %3s SKU → %3s 行 · SKU图 %s/%s%s"
                         % (it.get("pid"), it.get("skus_total"), it.get("rows"),
                            it.get("img_sku_filled"), it.get("img_sku_total"),
                            ("  ⚠ " + str(it.get("warn"))) if it.get("warn") else ""))
        filled = sum(int(x.get("img_sku_filled") or 0) for x in (r.get("items") or []))
        total = sum(int(x.get("img_sku_total") or 0) for x in (r.get("items") or []))
        self.lb.setText("Excel 路线完成：%d 个商品 · %d 行 · %s 秒 · 缺图 %d 张"
                        % (r.get("count", 0), r.get("rows_total", 0),
                           r.get("secs", "?"), r.get("img_miss_unique", 0)))
        msg = ("商品 %s 个（成功 %s / 失败 %s）\n"
               "合计 %s 行\nSKU 预览图：%s/%s 行有地址\n本次补传图床：%s 张\n\n"
               "输出目录：%s\n逐商品清单：_报告.md"
               % (r.get("count", 0), r.get("ok_count", 0), r.get("fail_count", 0),
                  r.get("rows_total", 0), filled, total, r.get("img_uploaded", 0),
                  r.get("out")))
        if r.get("img_miss_unique"):
            msg += "\n\n⚠ 还有 %s 张图没拿到公网地址，已在报告里点名。" % r["img_miss_unique"]
        warn = [str(x.get("pid")) for x in (r.get("items") or []) if x.get("warn")]
        if warn:
            msg += "\n⚠ 类目为空的商品：%s（小猪按类目建品，空类目会失败）" % "、".join(warn)
        QMessageBox.information(self, "Excel 导出完成", msg)
        try:
            import os  # noqa: PLC0415
            os.startfile(r.get("out") or "")  # noqa: S606
        except Exception:      # noqa: BLE001
            pass

    def _open_dir(self):
        import os  # noqa: PLC0415
        d = bridge.v3_out_dir()
        try:
            os.startfile(d)  # noqa: S606
        except Exception as e:  # noqa: BLE001
            self.log("打不开目录：%s（%s）" % (d, e))

    # ---------------------------------------------------------- 抖店 16 列
    def _export_dd(self):
        pids = self._pids or []
        if not pids:
            QMessageBox.information(self, "还没有商品", "先选一个数据包，或先在商品库里选商品。")
            return
        self.btn_dd.setEnabled(False)
        self.log("导出抖店 16 列：%d 个商品 · 一 SKU 一行…" % len(pids))
        try:
            r = bridge.v3_export_doudian(pids=pids)
        except Exception as ex:      # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
        self.btn_dd.setEnabled(True)
        if not r.get("ok"):
            self.log("✗ 抖店导出失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "抖店导出没做成", str(r.get("error"))[:300])
            return
        self.log("✓ 抖店已出表：%s 个商品 / %s 行 SKU / %s 列（表头在第 %s 行）"
                 % (r.get("products"), r.get("rows"), r.get("cols"), r.get("header_row")))
        for f in r.get("files") or []:
            self.log("   产出：" + str(f))
        warns = r.get("warns") or []
        if warns:
            self.log("   ⚠ %d 个商品有待处理项：" % len(warns))
            for w in warns[:8]:
                self.log("     商品 %s：%s" % (w.get("product_id"),
                                               "；".join(str(x) for x in (w.get("warns") or [])[:3])))
        line = "抖店 16 列：%s 个商品 / %s 行\n目录：%s" % (
            r.get("products"), r.get("rows"), bridge.v3_out_dir())
        if self._missing:
            line += ("\n\n⚠ 还有 %d 张/段媒体没有线上地址：被替换过的那些在表里留空（不会用原图顶）。"
                     "先点「上传图床替换数据库」再导一次，才是可直接导入的正式表。" % self._missing)
        line += "\n\n注：这是模板级结构验证；抖店后台能否导入，要你上传后自己确认。"
        QMessageBox.information(self, "抖店导出完成", line)

    # ---------------------------------------------------------- 类目映射
    def _open_cat(self):
        try:
            from .cat_map_dialog import CatMapDialog  # noqa: PLC0415
        except Exception as ex:      # noqa: BLE001
            QMessageBox.warning(self, "类目映射打不开",
                                "%s: %s" % (type(ex).__name__, ex))
            return
        dlg = CatMapDialog(self, pids=self._pids or [])
        dlg.exec()
        self.refresh()

    # ---------------------------------------------------------- 完整上货包
    def _bundle(self):
        """表格 + 真实媒体文件 + 三张清单 + 检查报告，一次给全。"""
        pids = self._pids or []
        if not pids:
            QMessageBox.information(self, "还没有商品",
                                    "先选一个数据包，或先在商品库里选商品。")
            return
        self.btn_pkg.setEnabled(False)
        self.log("生成完整上货包：%d 个商品（含全部确认版媒体，可能要几十秒）…" % len(pids))
        try:
            r = bridge.bundle_build(pids=pids)
        except Exception as ex:      # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
        self.btn_pkg.setEnabled(True)
        # 选中的商品全被细筛拦下时，问一次再决定（这是业务语义，不是选择题）
        if not r.get("ok") and "blocked" in str(r.get("error") or ""):
            if QMessageBox.question(
                    self, "这批商品被细筛拦下了",
                    "%s\n\n要把它们一起打进去吗？\n\n"
                    "选「Yes」= 出包，但包内检查报告会单独点名「已拦下、仅供验收、不得直接上架」。\n"
                    "选「No」= 先只导合格商品。" % str(r.get("error"))[:300]) \
                    == QMessageBox.StandardButton.Yes:
                self.btn_pkg.setEnabled(False)
                try:
                    r = bridge.bundle_build(pids=pids, include_blocked=True)
                except Exception as ex:      # noqa: BLE001
                    r = {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex)}
                self.btn_pkg.setEnabled(True)
        if not r.get("ok"):
            self.log("✗ 上货包失败：" + str(r.get("error"))[:200])
            QMessageBox.warning(self, "上货包没做成", str(r.get("error"))[:400])
            return
        st = r.get("stats") or {}
        self.log("✓ 上货包已生成：%s" % r.get("dir"))
        self.log("   媒体 %s 条引用 · 复制 %s 个文件（同内容 %s）· 待上传 %s 条 · 可上架 %s/%s 件"
                 % (st.get("media_refs"), st.get("copied"), st.get("same_content"),
                    st.get("pending"), st.get("can_list"), st.get("products")))
        if st.get("blocked"):
            self.log("   ⚠ %s 件不可上架（媒体缺失或无法识别，详见包内检查报告）"
                     % st.get("blocked"))
        for x in (r.get("problems") or [])[:8]:
            self.log("   · " + str(x)[:160])
        line = ("上货包：%s\n\n"
                "商品 %s 件 · 媒体 %s 条（复制 %s 个文件）\n"
                "待上传 %s 条 · 已上传 %s 条\n"
                "可上架 %s 件 · 不可上架 %s 件\n\n"
                "包内含：微信小店_19列.csv / 抖店_16列.xlsx / media\\ /\n"
                "        商品媒体对应清单.csv / 待上传媒体.csv / 已上传媒体.csv / 导出检查报告.txt"
                % (r.get("dir"), st.get("products"), st.get("media_refs"),
                   st.get("copied"), st.get("pending"), st.get("uploaded"),
                   st.get("can_list"), st.get("blocked")))
        if st.get("blocked"):
            line += "\n\n⚠ 有商品被标为「不可上架」——先看导出检查报告里点名的那几条。"
        QMessageBox.information(self, "上货包完成", line)
        try:
            import os  # noqa: PLC0415
            os.startfile(r.get("dir") or "")  # noqa: S606
        except Exception:      # noqa: BLE001
            pass
