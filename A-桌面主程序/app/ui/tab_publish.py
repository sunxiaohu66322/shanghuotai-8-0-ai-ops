# -*- coding: utf-8 -*-
"""5.0 · 直接上架（微信小店直连）

★ 2026-10-26 新增。和 5.5「出表」是两条路，**不是新旧替代关系**：

    5.0 直接上架 —— 调官方接口，把商品直接建到店里。**不需要图床、不需要出表。**
                    微信只认它自己的 mmecimage.cn 地址，第三方图床的 URL 会被
                    10020061「图片URL非法」拒掉 —— 所以这一步省掉图床是**对的**。
    5.5 出表     —— 仍然走 19 列模板那条老路（给小猪/别的服务商用），要图床。

为什么要做个页签而不是直接一个按钮：
    价格系数、SKU 数量、模板商品、标题改写都要能调，后面还会加（批量重试、
    上架、下架、改价、查审核状态）。一个按钮放不下，所以按 5.0 起一个独立页签。

两条硬约束（写在代码里，别放宽）：
    ① SKU 数量只能**截断**，不能凭空补 —— 库里 4 个规格要 7 个，只能上 4 个。
       编出来的规格是假货描述，平台驳回是轻的。
    ② 价格系数只乘**售价**，不碰成本和库存。
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                              QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                              QMessageBox, QProgressBar, QPushButton,
                              QSpinBox, QTabBar, QTableWidget, QTableWidgetItem,
                              QVBoxLayout, QWidget)

from .. import bridge, theme
from .tabs import StepTab

# 已验证可用的模板商品（茶台，类目 1247>549041>549074，平台已审过）
TPL_DEFAULT = "10001773856919"


class _Worker(QThread):
    """预览 / 建草稿共用一个 worker —— 两者都是「拿参数调 bridge，回结果」。

    with_progress=True 时给被调函数注入一个 progress 回调，它每件报一次，
    这里转成 Qt 信号（跨线程安全），界面就能实时显示「第几件、做到哪一步」。
    """
    done = pyqtSignal(dict)
    fail = pyqtSignal(str)
    note = pyqtSignal(str)
    prog = pyqtSignal(dict)

    def __init__(self, fn, with_progress=False, **kw):
        super().__init__()
        self.fn, self.kw, self.with_progress = fn, kw, with_progress

    def run(self):  # noqa: D102
        try:
            kw = dict(self.kw)
            if self.with_progress:
                kw["progress"] = lambda d: self.prog.emit(d or {})
            self.done.emit(self.fn(**kw) or {"ok": False, "error": "空返回"})
        except Exception as e:  # noqa: BLE001
            self.fail.emit(f"{type(e).__name__}: {e}")


class PublishTab(StepTab):
    title = "5.0 · 直接上架"

    def __init__(self, win, parent=None):
        super().__init__(win, parent)
        self._code = ""
        self._w = None
        self._last: list[dict] = []
        self._batches: list[dict] = []
        self._batch_items: list[dict] = []
        self._batch_ready = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        tip = QLabel(
            "直接调微信小店官方接口建商品，**不需要图床、不需要出表**。"
            "微信只认它自己 mmecimage.cn 的图片地址，所以图直接传给它。"
            "建出来的是**草稿**（顾客看不见），你在后台可以先看再决定上架。")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        root.addWidget(tip)

        # ---------- 第 0 行：目标平台 + 上架到哪些店（2026-11-07 主人要求）
        #   主人在**软件端**操作：这里直接选店/加店，不用去 6.0 网页。
        #   微店 与 微信小店 是同一批 shops.json 账号的两种叫法，切换只改文案与后台入口；
        #   真正决定「上到哪家」的是右边这一排勾选框，可单选也可多选同时上。
        r0 = QHBoxLayout()
        r0.addWidget(QLabel("目标平台"))
        self.cmb_platform = QComboBox()
        self.cmb_platform.addItems(["微信小店（官方接口直连）", "微店（同一批店铺账号）"])
        self.cmb_platform.setMaximumWidth(210)
        self.cmb_platform.setToolTip(
            "两种叫法共用同一批店铺账号（ shops.json ）。\n"
            "切换只改确认文案与「打开后台」的地址；上架目标由右边勾选决定。")
        r0.addWidget(self.cmb_platform)
        r0.addSpacing(10)
        r0.addWidget(QLabel("上架到"))
        self.shop_row = QHBoxLayout()
        self.shop_row.setSpacing(10)
        r0.addLayout(self.shop_row, 1)
        self.shop_checks: list[QCheckBox] = []
        self.btn_shop_add = QPushButton("＋ 添加店铺")
        self.btn_shop_add.setToolTip("在软件端直接填 AppID / AppSecret / 名称，写进与 6.0 同一份店铺台账")
        self.btn_shop_add.clicked.connect(self._add_shop_dialog)
        r0.addWidget(self.btn_shop_add)
        root.addLayout(r0)
        self.lb_platform_tip = QLabel("")
        self.lb_platform_tip.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:11px;")
        self.lb_platform_tip.setWordWrap(True)
        root.addWidget(self.lb_platform_tip)
        self._reload_shops()

        # ---------- 第一行：包 + 模板
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("数据包"))
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(260)
        self.cmb.currentIndexChanged.connect(self._changed)
        r1.addWidget(self.cmb)
        r1.addSpacing(12)
        r1.addWidget(QLabel("模板商品"))
        self.ed_tpl = QLineEdit(TPL_DEFAULT)
        self.ed_tpl.setMaximumWidth(160)
        self.ed_tpl.setToolTip(
            "照抄这件商品的类目/运费模板/售后地址/品牌等骨架字段。\n"
            "必须是一件**已经通过平台审核**的商品 —— 它的类目一定合法。\n"
            "换品类（餐边柜/落地灯/书桌…）时要换对应的模板。")
        r1.addWidget(self.ed_tpl)
        r1.addStretch(1)
        root.addLayout(r1)

        # ---------- 第二行：价格系数 + 每个SKU库存 + 精筛开关
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("价格系数"))
        self.sp_mark = QDoubleSpinBox()
        self.sp_mark.setRange(0.10, 20.00)
        self.sp_mark.setSingleStep(0.10)
        self.sp_mark.setDecimals(2)
        self.sp_mark.setValue(1.25)
        self.sp_mark.setPrefix("× ")
        self.sp_mark.setToolTip("所有规格的售价都乘这个数。1.25 = 加价 25%（默认，2026-11-08 主人口径）。只影响售价，不动库存。")
        self.sp_mark.valueChanged.connect(self._mark_hint)
        r2.addWidget(self.sp_mark)
        self.lb_mark = QLabel("—")
        self.lb_mark.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        r2.addWidget(self.lb_mark)

        # ★ 2026-10-27 主人要求：价格系数这一行旁边要有个「活动折扣计算器」。
        #   原话：「100 块钱，系数 ×1.25 上架，我到时候网站里做活动要打 8 折，
        #   才会跟我的价格保持一致……还要加一个利润率，比如说要获得 30% 的利润，
        #   系数 ×1.25 那折扣应该是打多少。就是个简易的计算器，只显示不输入。」
        #   公式（只算售价，不碰成本与库存）：
        #       保本折扣      = 1 / 系数          → 打这个折刚好等于原价
        #       保利润折扣    = (1 + 利润率) / 系数 → 打这个折等于「原价 × (1+利润率)」
        #   结果 <1 才叫"能打折"；≥1 说明这个系数本身就撑不起该利润率。
        r2.addSpacing(10)
        r2.addWidget(QLabel("利润率"))
        self.sp_profit = QDoubleSpinBox()
        self.sp_profit.setRange(0.0, 200.0)
        self.sp_profit.setSingleStep(5.0)
        self.sp_profit.setDecimals(0)
        self.sp_profit.setValue(0.0)
        self.sp_profit.setSuffix(" %")
        self.sp_profit.setMaximumWidth(90)
        self.sp_profit.setToolTip(
            "你想留的利润率（相对**原价**，不是相对上架价）。\n"
            "0% = 只要不亏就行；30% = 卖一件至少赚原价的 30%。\n"
            "它只参与右边的折扣计算，不改任何上架价格。")
        self.sp_profit.valueChanged.connect(self._mark_hint)
        r2.addWidget(self.sp_profit)
        self.lb_disc = QLabel("—")
        self.lb_disc.setStyleSheet(
            f"color:{theme.TEXT}; font-size:12px; font-weight:600;"
            "background:rgba(90,140,255,0.12); border-radius:6px; padding:3px 8px;")
        self.lb_disc.setToolTip(
            "活动折扣参考（只显示，不改价格）：\n"
            "· 保本折扣 = 1 ÷ 价格系数 —— 打这个折刚好等于你的原价\n"
            "· 保利润折扣 = (1 + 利润率) ÷ 价格系数 —— 打这个折仍能拿到该利润率\n"
            "显示「不能打折」= 这个系数撑不住该利润率，要提系数。")
        r2.addWidget(self.lb_disc)

        r2.addSpacing(18)
        # ★ 2026-10-26 语义纠正：这里是**每个 SKU 的库存件数**，不是 SKU 的个数。
        #   用户原话：「我说的是每个 SKU 七件货，不是上七个 SKU」。
        #   SKU 个数不限制（全上），多规格商品按维度组合，不截断。
        r2.addWidget(QLabel("每SKU库存"))
        self.sp_sku = QSpinBox()
        self.sp_sku.setRange(1, 99999)
        self.sp_sku.setValue(7)
        self.sp_sku.setSuffix(" 件")
        self.sp_sku.setToolTip(
            "**每个规格上多少件货**，默认 7 件。\n"
            "这不是「只上几个 SKU」—— 规格全部上，按尺码×颜色等维度组合。\n"
            "库里原有的库存数多半是脏数据（实测合计 1.36 亿），统一覆盖成这个值。")
        r2.addWidget(self.sp_sku)

        r2.addSpacing(18)
        # ★ 默认不勾 = 不生效：全建（含未精筛的）
        self.ck_screened = QCheckBox("只建精筛通过的")
        self.ck_screened.setChecked(False)
        self.ck_screened.setToolTip(
            "**默认不勾 = 不生效**，包里所有商品都建（含还没跑精筛的 imported）。\n"
            "打勾后才只建精筛通过（fine_screened）的，其余跳过。")
        r2.addWidget(self.ck_screened)
        r2.addStretch(1)
        root.addLayout(r2)

        # ---------- 第二行·补：多维度开关
        r2b = QHBoxLayout()
        self.ck_multi = QCheckBox("按维度拆分规格（尺码 × 颜色…）")
        self.ck_multi.setChecked(True)
        self.ck_multi.setToolTip(
            "打勾：spec 里的「尺码:单人/颜色:水洗蓝」拆成两个销售属性维度（推荐）。\n"
            "不勾：整串拍平成一个「销售属性1」的值（类目不认多维时的兜底）。")
        r2b.addWidget(self.ck_multi)

        r2b.addSpacing(18)
        self.ck_only_ok = QCheckBox("只建预览通过的")
        self.ck_only_ok.setChecked(True)
        self.ck_only_ok.setToolTip("勾了就先预览，只建预览没问题的那些。避免结构有问题的也被建出去。")
        r2b.addWidget(self.ck_only_ok)
        r2b.addStretch(1)
        root.addLayout(r2b)

        # ---------- 第三行：按钮
        r3 = QHBoxLayout()
        self.btn_pre = QPushButton("🔍 预览（不联网）")
        self.btn_pre.setObjectName("Primary")
        self.btn_pre.setToolTip("只组装结构，不上传图片、不建商品。先看会变成什么样。")
        self.btn_pre.clicked.connect(self._preview)
        r3.addWidget(self.btn_pre)

        self.btn_go = QPushButton("🚀 建草稿（真上传 + 真建商品）")
        self.btn_go.setToolTip("会上传图片到微信、调用 product/add 建商品。\n"
                               "建出来是草稿状态，顾客看不见。")
        self.btn_go.clicked.connect(self._publish)
        r3.addWidget(self.btn_go)

        self.btn_shop = QPushButton("打开微信小店后台")
        self.btn_shop.clicked.connect(self._open_shop)
        r3.addWidget(self.btn_shop)
        r3.addStretch(1)
        root.addLayout(r3)

        # ---------- 进度 + 结果
        self.pb = QProgressBar()
        self.pb.setRange(0, 1)
        self.pb.setValue(0)
        self.pb.setFormat("%v/%m")
        root.addWidget(self.pb)

        self.lb = QLabel("—")
        self.lb.setStyleSheet(f"color:{theme.TEXT}; font-size:13px; font-weight:600;")
        self.lb.setWordWrap(True)
        root.addWidget(self.lb)

        # ---------- 发布台账（防重复铺货，2026-10-27 主人追问）
        # 「中断后重跑会不会同一件货上架两次」→ 这里就是那道闸。
        r5 = QHBoxLayout(); r5.setSpacing(6)
        r5.addWidget(QLabel("发布台账"))
        self.lb_state = QLabel("—")
        self.lb_state.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        self.lb_state.setWordWrap(True)
        self.lb_state.setToolTip(
            "已建 = 微信侧确实有这件草稿。**重跑会自动跳过，不会再建第二件。**\n"
            "待对账 = 上次建到一半就断了（关窗口/断电）：建之前必须先点对账，\n"
            "          对账会去微信侧认领，认领到的不重建。\n"
            "失败 = 明确没建成，可以只重试这几件。\n"
            "已缓存图 = 这些图传过微信，重跑不会再传一次。")
        r5.addWidget(self.lb_state, 1)
        self.btn_recon = QPushButton("🔁 对账")
        self.btn_recon.setToolTip("去微信侧核对「上次中断留下的记录」：\n"
                                  "· 微信侧已存在 → 认领回来，不会重建\n"
                                  "· 微信侧没有 → 标失败，可安全重试\n"
                                  "· 标题有重名 → 不动，等你自己确认")
        self.btn_recon.clicked.connect(self._reconcile)
        r5.addWidget(self.btn_recon)
        self.btn_retry = QPushButton("↻ 只重试失败 0 件")
        self.btn_retry.setToolTip("只把上次明确失败的几件重跑一遍；\n"
                                  "已建成的不动，避免重复铺货。")
        self.btn_retry.clicked.connect(self._retry_failed)
        r5.addWidget(self.btn_retry)
        root.addLayout(r5)

        # ---------- 批次页签：哪天上了哪一批（名字＝日期＋第几批）
        r4 = QHBoxLayout(); r4.setSpacing(6)
        r4.addWidget(QLabel("批次"))
        self.tabbar = QTabBar()
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setToolTip(
            "每次「建草稿」成功后自动存一条，名称是「日期 第几批」。\n"
            "点页签就能看到那一批上了哪些货、哪些成了、哪些失败了。\n"
            "下一次上货会新开一个页签，旧批次不会被动。")
        self.tabbar.currentChanged.connect(self._on_batch_tab)
        r4.addWidget(self.tabbar, 1)
        self.btn_batch_reload = QPushButton("↻")
        self.btn_batch_reload.setMaximumWidth(34)
        self.btn_batch_reload.setToolTip("重新读批次列表（别的窗口上过货也会出现在这里）")
        self.btn_batch_reload.clicked.connect(lambda: self._load_batches())
        r4.addWidget(self.btn_batch_reload)
        root.addLayout(r4)

        self.tb = QTableWidget(0, 8)
        self.tb.setHorizontalHeaderLabels(
            ["商品ID", "标题", "主图", "详情", "SKU", "价格区间", "微信商品ID", "状态"])
        self.tb.verticalHeader().setVisible(False)
        hh = self.tb.horizontalHeader()
        for i in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.tb.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.tb, 1)

        self._mark_hint()          # 一进来就把折扣算好，别等用户去拧旋钮

    # ---------------- 数据 ----------------
    @staticmethod
    def _pack_key(it: dict) -> str:
        """给数据包排序用的"新不新"键。

        包里最靠谱的时间证据是名字里的时间戳（如 `混合_打包_18件_20261025_1527`），
        其次是记账行的时间字段。**两样都没有就把名字倒序**（后建的通常名字更长/更新）。
        这里只做排序，不改任何展示文案。
        """
        import re
        code = str(it.get("pack_code") or "")
        m = re.findall(r"(20\d{6}(?:_?\d{4,6})?)", code)
        stamp = m[-1].replace("_", "") if m else ""
        return "%s|%s|%s" % (stamp, str(it.get("created_at") or ""), code)

    def refresh(self):
        cur = self.cmb.currentData()
        r = bridge.v3_packs()
        items = (r.get("items") or []) if isinstance(r, dict) else []
        items = [it for it in items
                 if (it.get("pack_code") or "") and (it.get("products_live") or 0)]
        # ★ 2026-10-27 主人要求：数据包默认选**最新的**，不用每次自己找。
        items.sort(key=self._pack_key, reverse=True)
        self.cmb.blockSignals(True)
        self.cmb.clear()
        for it in items:
            code = it.get("pack_code") or ""
            n = it.get("products_live") or 0
            self.cmb.addItem("%s（%s 件）" % (code, n), code)
        self.cmb.blockSignals(False)
        if cur:
            i = self.cmb.findData(cur)
            if i >= 0:
                self.cmb.setCurrentIndex(i)
        else:
            self.cmb.setCurrentIndex(0)          # 第一次进来 = 最新那个包
        self._changed()
        self._load_batches()
        self._load_state()
        self._reload_shops()

    # ---------------- 店铺 ----------------
    def _reload_shops(self):
        """从 shops.json 重建店铺勾选框；默认只勾当前店，多选由主人自己勾。"""
        while self.shop_row.count():
            it = self.shop_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self.shop_checks = []
        r = bridge.v3_wx_shops()
        shops = (r.get("shops") or []) if isinstance(r, dict) else []
        cur = (r.get("current") or "") if isinstance(r, dict) else ""
        for s in shops:
            if s.get("enabled") is False:
                continue
            cb = QCheckBox(s.get("name") or s.get("appid", "")[:8])
            cb.setProperty("appid", s.get("appid") or "")
            cb.setChecked(s.get("appid") == cur)
            cb.setToolTip("AppID %s\n勾一家 = 只上这一家；勾多家 = 同一件货同时上到多家（图片各店各传）"
                          % s.get("appid", ""))
            self.shop_row.addWidget(cb)
            self.shop_checks.append(cb)
        if not self.shop_checks:
            self.shop_row.addWidget(QLabel("（还没有店铺，点右边「＋ 添加店铺」）"))
        n = len(self.shop_checks)
        self.lb_platform_tip.setText(
            "共 %d 家店铺可选。微店 / 微信小店是同一批账号的两种叫法；"
            "切平台只改文案与后台入口，真正上到哪家由上面的勾选决定。" % n
            if n else "还没有店铺账号。点「＋ 添加店铺」填 AppID / AppSecret 即可，"
                      "与 6.0「账号管理」是同一份台账。")

    def _selected_shop_ids(self) -> list[str]:
        return [str(cb.property("appid") or "") for cb in self.shop_checks
                if cb.isChecked() and cb.property("appid")]

    def _selected_shop_names(self) -> list[str]:
        return [cb.text() for cb in self.shop_checks if cb.isChecked()]

    def _add_shop_dialog(self):
        """软件端直接加店，不用去 6.0 网页。"""
        aid, ok = QInputDialog.getText(self, "添加店铺 1/3", "AppID（wx 开头）：")
        if not ok or not aid.strip():
            return
        sec, ok = QInputDialog.getText(self, "添加店铺 2/3", "AppSecret：",
                                       QLineEdit.EchoMode.Password)
        if not ok or not sec.strip():
            return
        name, ok = QInputDialog.getText(self, "添加店铺 3/3", "显示名称（例如：第二家店）：")
        if not ok:
            return
        r = bridge.v3_wx_shop_add(aid.strip(), sec.strip(), name.strip())
        if not r.get("ok"):
            QMessageBox.warning(self, "没加成功", str(r.get("error") or "未知错误"))
            return
        self._reload_shops()
        for cb in self.shop_checks:
            if cb.property("appid") == r.get("appid"):
                cb.setChecked(True)
        QMessageBox.information(self, "已添加",
                                "店铺「%s」已保存，并已勾选为本次上架目标。\n"
                                "6.0「账号管理」里也能看到同一家店。" % r.get("name"))

    def _changed(self):
        self._code = self.cmb.currentData() or ""
        if not self._code:
            self.lb.setText("选一个数据包")
            return
        r = bridge.v3_pack_pids(self._code)
        n = r.get("count") if isinstance(r, dict) else 0
        self.lb.setText("当前包：%s · 商品 %s 件" % (self._code, n or 0))
        self.pb.setRange(0, max(1, int(n or 1)))
        self.pb.setValue(0)

    def _mark_hint(self):
        v = self.sp_mark.value()
        if abs(v - 1.0) < 1e-6:
            self.lb_mark.setText("原价")
        elif v > 1:
            self.lb_mark.setText("加价 %d%%" % round((v - 1) * 100))
        else:
            self.lb_mark.setText("降价 %d%%" % round((1 - v) * 100))
        self._calc_discount(v)

    def _calc_discount(self, v: float | None = None):
        """折扣计算器（只显示）：保本折扣 + 保利润率折扣。

        验算（主人举的两个例子都在里面）：
          系数 1.25 / 利润 0%  → 保本 0.80 = 8.0 折        ← 主人原例
          系数 2.00 / 利润 0%  → 保本 0.50 = 5.0 折
          系数 1.25 / 利润 30% → 1.30/1.25 = 1.04 → 不能打折（系数需 ≥1.30）
          系数 2.00 / 利润 30% → 1.30/2.00 = 0.65 = 6.5 折
        """
        try:
            m = float(v if v is not None else self.sp_mark.value()) or 1.0
            p = float(self.sp_profit.value()) / 100.0
        except Exception:                      # noqa: BLE001
            return
        be = 1.0 / m                           # 保本折扣
        need = (1.0 + p) / m                   # 保利润折扣
        be_txt = "原价" if be >= 1.0 else "%.1f 折" % (be * 10)
        if p <= 1e-9:
            self.lb_disc.setText("保本折扣 %s（活动价 = 原价）" % be_txt)
        elif need > 1.0 + 1e-9:
            self.lb_disc.setText(
                "保 %.0f%% 利润：不能打折 —— 系数得 ≥ ×%.2f" % (p * 100, 1.0 + p))
        else:
            self.lb_disc.setText(
                "保本 %s ｜ 保 %.0f%% 利润最低打 %.1f 折"
                % (be_txt, p * 100, need * 10))

    # ---------------- 批次台账（哪天上了哪批）
    def _load_batches(self, select_id: int | None = None):
        try:
            r = bridge.v3_batch_list(60)
        except Exception as e:                 # noqa: BLE001
            self.log("[5.0] 读批次失败：%s: %s" % (type(e).__name__, e))
            return
        self._batches = (r.get("items") or []) if isinstance(r, dict) else []
        self.tabbar.blockSignals(True)
        while self.tabbar.count():
            self.tabbar.removeTab(0)
        self.tabbar.addTab("本次")
        for b in self._batches:
            nm = str(b.get("name") or "")
            day = str(b.get("day") or "")
            # 页签上留「10-27 第1批」（年份挤不下，完整名在 tooltip）
            short = nm
            if day and nm.startswith(day):
                short = ("%s%s" % (day[5:], nm[len(day):])).strip()
            self.tabbar.addTab(short or nm or "批次")
            if nm:
                self.tabbar.setTabToolTip(self.tabbar.count() - 1,
                                          "%s · %s 件 · 成功 %s / 失败 %s"
                                          % (nm, b.get("total"), b.get("created"),
                                             b.get("failed")))
        self.tabbar.blockSignals(False)
        self._batch_ready = True
        idx = 0
        if select_id:
            for i, b in enumerate(self._batches, 1):
                if int(b.get("batch_id") or 0) == int(select_id):
                    idx = i
                    break
        self.tabbar.setCurrentIndex(idx)
        self._on_batch_tab(idx)

    def _on_batch_tab(self, i: int):
        if not self._batch_ready:
            return
        if i <= 0:
            # 「本次」= 当前预览/刚上完的那批（不落盘也看得见）
            self._batch_items = list(self._last or [])
            if self._last:
                self._fill(self._last, mode="preview")
            return
        b = self._batches[i - 1] if i - 1 < len(self._batches) else None
        if not b:
            return
        r = bridge.v3_batch_get(int(b.get("batch_id") or 0))
        if not isinstance(r, dict) or not r.get("ok"):
            self.lb.setText("这批读不出来：%s" % ((r or {}).get("error") if isinstance(r, dict) else "未知"))
            return
        items = r.get("items") or []
        self._batch_items = items
        self.lb.setText(
            "%s ｜ 包 %s ｜ 系数 ×%.2f · 利润率 %.0f%% · 每SKU %s 件\n成功 %s 件 · 失败 %s 件 · 共 %s 件 ｜ %s"
            % (r.get("name"), r.get("pack_code") or "—", float(r.get("markup") or 1),
               float(r.get("profit_pct") or 0), r.get("stock_per_sku"),
               r.get("created"), r.get("failed"), r.get("total"), r.get("created_at") or ""))
        self._fill_batch(items)

    def _fill_batch(self, items: list[dict]):
        self.tb.setRowCount(len(items))
        for i, x in enumerate(items):
            ok = bool(x.get("wx_product_id"))
            n_sku, n_all = (x.get("sku") or 0), (x.get("sku_total") or 0)
            sku_txt = ("%d/%d" % (n_sku, n_all)) if n_all and n_all != n_sku else str(n_sku or "")
            vals = [str(x.get("product_id") or ""), x.get("title") or "",
                    str(x.get("head") or ""), str(x.get("desc") or ""), sku_txt,
                    ("%.2f–%.2f" % (x.get("price_low", 0), x.get("price_high", 0)))
                    if x.get("price_low") else "—",
                    str(x.get("wx_product_id") or "—"),
                    ("✓ 草稿已建" if ok else (x.get("error") or "失败"))]
            for j, v in enumerate(vals):
                self.tb.setItem(i, j, QTableWidgetItem(v))

    # ---------------- 发布台账（防重复铺货） ----------------
    def _load_state(self):
        """刷新「已建 / 待对账 / 失败 / 已缓存图」四个数。纯本地查询，不联网。"""
        try:
            r = bridge.v3_publish_state(limit=120)
        except Exception as e:  # noqa: BLE001
            self.lb_state.setText("台账读不到：%s: %s" % (type(e).__name__, e))
            return
        if not r.get("ok"):
            self.lb_state.setText("台账不可用：%s" % (r.get("error") or ""))
            return
        c, d, f = int(r.get("created") or 0), int(r.get("creating") or 0), int(r.get("failed") or 0)
        img = int((r.get("img") or {}).get("cached") or 0)
        txt = "已建 %d · 待对账 %d · 失败 %d" % (c, d, f)
        if img:
            txt += " · 已缓存图 %d" % img
        if d:
            txt += "　← 先点「🔁 对账」再建，否则可能重复铺货"
            self.lb_state.setStyleSheet(f"color:{theme.DANGER}; font-size:12px;")
        else:
            self.lb_state.setStyleSheet(f"color:{theme.TEXT_SUB}; font-size:12px;")
        self.lb_state.setText(txt)
        self.btn_recon.setText("🔁 对账（%d）" % d if d else "🔁 对账")
        self.btn_recon.setEnabled(d > 0)
        self.btn_retry.setText("↻ 只重试失败 %d 件" % f)
        self.btn_retry.setEnabled(f > 0)

    def _reconcile(self):
        self.btn_recon.setEnabled(False)
        self.log("[5.0] 对账：去微信侧核对上次中断留下的记录…（只读，不建商品）")
        self._w = _Worker(lambda **k: bridge.v3_publish_reconcile(force=True))
        self._w.done.connect(self._on_reconcile)
        self._w.fail.connect(lambda s: (self.log("[5.0] 对账失败：" + s), self._load_state()))
        self._w.start()

    def _on_reconcile(self, r: dict):
        if not r.get("ok"):
            self.log("[5.0] ⚠ 对账没做成：%s（不影响已建的，只是这次没核对）"
                     % (r.get("error") or ""))
        else:
            self.log("[5.0] 对账完成：检查 %d · 认领 %d · 未找到 %d · 待人工 %d"
                     % (int(r.get("checked") or 0), int(r.get("fixed") or 0),
                        int(r.get("failed") or 0), int(r.get("hold") or 0)))
            for it in (r.get("items") or []):
                if it.get("result") == "created":
                    self.log("[5.0]   ✓ 商品 #%s 已在微信侧（%s，按 %s 认领）→ 不再重建"
                             % (it.get("product_id"), it.get("wx"), it.get("by")))
                elif it.get("result") == "failed":
                    self.log("[5.0]   · 商品 #%s 微信侧没有 → 可安全重试" % it.get("product_id"))
                elif it.get("result") == "hold":
                    self.log("[5.0]   ⚠ 商品 #%s 标题有重名 %s → 留在待确认"
                             % (it.get("product_id"), it.get("candidates")))
        self._load_state()

    def _retry_failed(self):
        try:
            r = bridge.v3_publish_failed()
        except Exception as e:  # noqa: BLE001
            self.log("[5.0] 读失败清单出错：%s: %s" % (type(e).__name__, e))
            return
        pids = [int(x) for x in (r.get("pids") or [])]
        if not pids:
            QMessageBox.information(self, "没有失败的", "台账里没有状态为「失败」的商品。")
            return
        ok = QMessageBox.question(
            self, "只重试失败的",
            "只重跑这 %d 件（上次明确没建成的）。\n\n"
            "已建成的不会动 —— 不会产生第二件。\n\n商品号：%s%s\n\n继续？"
            % (len(pids), pids[:15], " …" if len(pids) > 15 else ""))
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._publish(pids_override=pids)

    def _params(self) -> dict:
        """★ 2026-10-26 语义纠正：`sp_sku` 是**每个 SKU 的库存件数**，不是 SKU 个数。
        规格数量恒为不截断（sku_limit=0），多规格按维度组合全上。"""
        return {"pack_code": self._code,
                "template_id": int(self.ed_tpl.text().strip() or TPL_DEFAULT),
                "price_markup": float(self.sp_mark.value()),
                "stock_per_sku": int(self.sp_sku.value()),
                "sku_limit": 0,
                "multi_dim": self.ck_multi.isChecked(),
                "only_screened": self.ck_screened.isChecked()}

    def _rule_txt(self) -> str:
        """把当前规则拼成一句人话，用于日志与确认框。"""
        return ("系数 ×%.2f · 每SKU %d 件 · 规格%s%s"
                % (self.sp_mark.value(), self.sp_sku.value(),
                   "按维度拆分" if self.ck_multi.isChecked() else "拍平单维",
                   " · 只建精筛通过" if self.ck_screened.isChecked() else " · 全建（不筛）"))

    # ---------------- 动作 ----------------
    def _busy(self, on: bool, txt: str = ""):
        self.btn_pre.setEnabled(not on)
        self.btn_go.setEnabled(not on)
        if txt:
            self.lb.setText(txt)

    def _preview(self):
        if not self._code:
            QMessageBox.information(self, "先选数据包", "上面那个下拉里选一个数据包。")
            return
        self._busy(True, "正在组装预览（不联网，不上传）…")
        self.log(f"[5.0] 预览 {self._code}：{self._rule_txt()}")
        self._w = _Worker(bridge.v3_wx_preview, **self._params())
        self._w.done.connect(self._on_preview)
        self._w.fail.connect(lambda s: (self._busy(False, "预览失败：" + s),
                                        self.log("[5.0] 预览失败：" + s)))
        self._w.start()

    def _on_preview(self, r: dict):
        self._busy(False)
        if not r.get("ok"):
            self.lb.setText("预览失败：" + str(r.get("error")))
            self.log("[5.0] 预览失败：" + str(r.get("error")))
            return
        items = r.get("items") or []
        self._last = items
        ok_n = sum(1 for x in items if x.get("ok"))
        _sk = "（已跳过 %d 件未精筛）" % r["skipped_unscreened"] \
            if r.get("only_screened") else ""
        self.lb.setText("预览完成：%d 件可建 · %d 件有问题%s · 每SKU %s 件"
                        % (ok_n, len(items) - ok_n, _sk,
                           r.get("stock_per_sku") or self.sp_sku.value()))
        self._fill(items, mode="preview")
        self.pb.setRange(0, max(1, len(items)))
        self.pb.setValue(len(items))
        if r.get("only_screened"):
            self.log("[5.0] 只建精筛通过：跳过 %d 件未精筛 %s"
                     % (r.get("skipped_unscreened") or 0,
                        (r.get("skipped_ids") or [])[:10]))
        for x in items:
            if not x.get("ok"):
                self.log("[5.0] #%s 不可建：%s" % (x.get("product_id"),
                                                   "; ".join(x.get("problems") or [])))
            elif x.get("warns"):
                self.log("[5.0] #%s 提示：%s" % (x.get("product_id"),
                                                 "; ".join(x["warns"])))

    def _publish(self, *a, pids_override=None):
        shop_ids = self._selected_shop_ids()
        if not shop_ids:
            QMessageBox.warning(self, "先选店铺",
                                "在「上架到」那一排至少勾一家店。\n"
                                "没有店铺就点「＋ 添加店铺」填 AppID / AppSecret。")
            return
        if pids_override:
            pids = [int(x) for x in pids_override]
            n_txt = "%d 件（重试上次失败的）" % len(pids)
        else:
            if not self._code:
                QMessageBox.information(self, "先选数据包", "上面那个下拉里选一个数据包。")
                return
            # ★ 中断闸门：上次有建到一半的，必须先对账，否则可能重复铺货
            try:
                st = bridge.v3_publish_state(limit=1)
                nd = int(st.get("creating") or 0) if st.get("ok") else 0
            except Exception:  # noqa: BLE001
                nd = 0
            if nd:
                QMessageBox.warning(
                    self, "先对账，再建",
                    "上次有 %d 条货建到一半就断了（关窗口/断电/被强制结束）。\n\n"
                    "那时候微信侧可能已经把商品建出来了，只是本机没来得及记账。\n"
                    "**现在直接建，同一件货可能被建两次。**\n\n"
                    "请先点下面那个「🔁 对账」：它会去微信侧把这几件认回来，"
                    "认领到的就不再重建。对账完再点建草稿。" % nd)
                return
            if self.ck_only_ok.isChecked() and not self._last:
                QMessageBox.information(self, "先预览", "勾了「只建预览通过的」，请先点一次预览。")
                return
            pids = [x["product_id"] for x in self._last if x.get("ok")] \
                if self.ck_only_ok.isChecked() else []
            n_txt = ("%d 件（预览通过的）" % len(pids)) if pids else "整包"
        shop_txt = "、".join(self._selected_shop_names()) or "（未选）"
        multi_tip = ("\n· 已勾 %d 家店：同一件货会分别建到每家，图片各店各传，逐家报成败"
                     % len(shop_ids)) if len(shop_ids) > 1 else ""
        plat = self.cmb_platform.currentText()
        ok = QMessageBox.question(
            self, "确认建商品",
            "目标平台：%s\n目标店铺：%s\n\n"
            "要在这些店里建 %s 件**草稿**商品。\n\n"
            "· 会真上传图片到微信服务器\n"
            "· 建出来是草稿，顾客看不见，你后台能改能删%s\n"
            "· %s\n\n继续？" % (plat, shop_txt, n_txt, multi_tip, self._rule_txt()))
        if ok != QMessageBox.StandardButton.Yes:
            return
        kw = self._params()
        if pids:
            kw["pids"] = pids
        kw["shop_ids"] = shop_ids
        self._busy(True, "正在上传图片并建商品…（每件约 20-60 秒，别关窗口）")
        self.log("[5.0] 开始建 %s：%s" % (n_txt, self._rule_txt()))
        self._w = _Worker(bridge.v3_wx_publish, with_progress=True, **kw)
        self._w.prog.connect(self._on_progress)
        self._w.done.connect(self._on_publish)
        self._w.fail.connect(lambda s: (self._busy(False, "建商品失败：" + s),
                                        self.log("[5.0] 建商品失败：" + s)))
        self._w.start()

    def _on_progress(self, d: dict):
        """逐件进度：更新进度条 + 状态行 + 日志。"""
        i = int(d.get("i") or 0)
        total = int(d.get("total") or 0) or 1
        stage = d.get("stage") or ""
        pid = d.get("pid")
        self.pb.setRange(0, total)
        # 「上传中」时进度条停在 i-1（这一件还没完），「已建/失败」才前进
        self.pb.setValue(max(0, i - 1) if stage.endswith("中") else i)

        parts = []
        if d.get("shop"):
            parts.append("[%s]" % d["shop"])
        if pid:
            parts.append("[%d/%d] pid=%s" % (i, total, pid))
        else:
            parts.append("[%d/%d]" % (i, total))
        parts.append(stage)
        if d.get("head") is not None:
            parts.append("主图%s/详情%s/规格%s"
                         % (d.get("head"), d.get("desc"), d.get("sku")))
        if d.get("wx"):
            parts.append("→ %s" % d["wx"])
        if d.get("detail"):
            parts.append(str(d["detail"]))
        line = " ".join(parts)
        self.lb.setText(line)
        self.log("[5.0] " + line)

    def _on_publish(self, r: dict):
        self._busy(False)
        if not r.get("ok"):
            self.lb.setText("建商品失败：" + str(r.get("error")))
            self.log("[5.0] 建商品失败：" + str(r.get("error")))
            return
        self.lb.setText("建完：成功 %d 件 · 跳过(已建过) %d 件 · 失败 %d 件"
                        % (r.get("created") or 0, r.get("skipped_dup") or 0,
                           r.get("failed") or 0))
        self.pb.setRange(0, max(1, r.get("total") or 1))
        self.pb.setValue((r.get("created") or 0) + (r.get("failed") or 0)
                         + (r.get("skipped_dup") or 0))
        self._fill_publish(r.get("items") or [], r.get("errors") or [])
        self.log("[5.0] 建完：成功 %d · 跳过(已建过) %d · 失败 %d"
                 % (r.get("created") or 0, r.get("skipped_dup") or 0,
                    r.get("failed") or 0))
        for d in (r.get("dups") or []):
            self.log("[5.0] ⏭ 跳过 #%s：%s" % (d.get("product_id"), d.get("reason")))
        for e in (r.get("errors") or []):
            self.log("[5.0] 失败 #%s：%s" % (e.get("product_id"), e.get("error")))
        for s in (r.get("shops") or []):
            self.log("[5.0] 店铺 %s：%s 成功 %s · 失败 %s · 跳过 %s %s"
                     % (s.get("name"), "✓" if s.get("ok") else "✗",
                        s.get("created") or 0, s.get("failed") or 0,
                        s.get("skipped_dup") or 0,
                        ("（%s）" % s.get("error")) if s.get("error") else ""))
        # ★ 2026-10-27：这一批上完就落一条台账（日期＋第几批），下次上货另开一条。
        try:
            sv = bridge.v3_batch_save(
                pack_code=self._code, template_id=self.ed_tpl.text().strip(),
                markup=float(self.sp_mark.value()),
                profit_pct=float(self.sp_profit.value()),
                stock_per_sku=int(self.sp_sku.value()),
                items=r.get("items") or [],
                created=int(r.get("created") or 0),
                failed=int(r.get("failed") or 0),
                note=self._rule_txt())
            if isinstance(sv, dict) and sv.get("ok"):
                self.log("[5.0] 批次已存：%s（成功 %s / 失败 %s）"
                         % (sv.get("name"), sv.get("created"), sv.get("failed")))
                self._load_batches(select_id=sv.get("batch_id"))
            else:
                self.log("[5.0] ⚠ 批次没存上：%s"
                         % ((sv or {}).get("error") if isinstance(sv, dict) else "未知"))
        except Exception as e:                 # noqa: BLE001
            self.log("[5.0] ⚠ 批次没存上：%s: %s" % (type(e).__name__, e))
        self._load_state()                     # 台账：已建/待对账/失败 立刻刷新

    # ---------------- 表格 ----------------
    def _fill(self, items: list[dict], mode: str = "preview"):
        self.tb.setRowCount(len(items))
        for i, x in enumerate(items):
            # ★ 2026-10-26 修：原来从顶层取 head/desc/sku，而它们在 stats 子字典里，
            #   于是主图/详情显示 None、SKU 恒为 0。后端已在顶层平铺这三个字段。
            n_sku, n_all = (x.get("sku") or 0), (x.get("sku_total") or 0)
            sku_txt = ("%d/%d" % (n_sku, n_all)) if n_all and n_all != n_sku else str(n_sku)
            vals = [str(x.get("product_id") or ""),
                    x.get("title") or "",
                    str(x.get("head") or 0),
                    str(x.get("desc") or 0),
                    sku_txt,
                    ("%.2f–%.2f" % (x.get("price_low", 0), x.get("price_high", 0)))
                    if x.get("price_low") else "—",
                    x.get("wx_product_id") or "—",
                    ("可建" if x.get("ok") else
                     "; ".join(x.get("problems") or []) or "见日志")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j in (2, 3, 4):
                    it.setToolTip("本地有 %s 张主图 / %s 张详情图 · %s 个规格%s"
                                  % (x.get("head_local", x.get("head") or 0),
                                     x.get("desc_local", x.get("desc") or 0),
                                     n_all or n_sku,
                                     ("（库里共 %d，按设定截断）" % n_all)
                                     if n_all and n_all != n_sku else ""))
                self.tb.setItem(i, j, it)

    def _fill_publish(self, items: list[dict], errors: list[dict]):
        err_map = {str(e.get("product_id")): e.get("error") for e in errors}
        self.tb.setRowCount(len(items) + len(errors))
        r = 0
        for x in items:
            # ★ 2026-10-26 修：原来主图/详情两列硬编码成空字符串，建完也看不见数量。
            n_sku, n_all = (x.get("sku") or 0), (x.get("sku_total") or 0)
            sku_txt = ("%d/%d" % (n_sku, n_all)) if n_all and n_all != n_sku else str(n_sku)
            vals = [str(x.get("product_id") or ""), x.get("title") or "",
                    str(x.get("head") or 0), str(x.get("desc") or 0),
                    sku_txt, "",
                    str(x.get("wx_product_id") or ""),
                    "✓ 草稿已建"]
            for j, v in enumerate(vals):
                self.tb.setItem(r, j, QTableWidgetItem(v))
            r += 1
        for e in errors:
            pid = str(e.get("product_id") or "")
            vals = [pid, "", "", "", "", "", "—", err_map.get(pid) or "失败"]
            for j, v in enumerate(vals):
                self.tb.setItem(r, j, QTableWidgetItem(v))
            r += 1

    def _open_shop(self):
        import webbrowser
        if self.cmb_platform.currentIndex() == 1:
            webbrowser.open("http://127.0.0.1:9191/#/accounts")   # 6.0 账号管理（同一软件内的页签）
        else:
            webbrowser.open("https://store.weixin.qq.com/")
