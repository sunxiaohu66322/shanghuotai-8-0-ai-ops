# -*- coding: utf-8 -*-
"""选品条件（真源 + 提示词生成 + 本地硬闸门）
2026-10-28 主人要求：「商品精筛根本没有相应的条件，相当于提示词都没有，随随便便筛的，
你起码要有个依据吧…把这些因素都做成一个设置页放进去，方便后期变更…即便是后期换了 API，
它只要通过这道提示词知道我们选品的条件了，也可以很自然地把需要的品合适的品选出来。」

设计三条（为什么这样做，别改回去）：
  1. **条件只有一份真源** config/selection_criteria.json。界面改它、AI 读它、闸门用它 —— 三处不各写一套。
  2. **提示词由条件生成**，不手写长文：改一个阈值，提示词自动跟着变，避免「文档与代码对不上」。
  3. **本地闸门先判，AI 再复核**：能靠词/价位/类目判的绝不花 token；AI 只判语义与行情。
     硬拦项（质量风险、类目排除、品牌货）任何模型都不能推翻 —— 换 API 也不会跑偏。

历史依据（迁移来源，不许凭空编）：
  · config/selection_profile.json  v3 · 2026-10-18 主人定调（无货源 / 拼多多代发 / 微信小店 / 加价100%）
  · config/fine_positioning.json  细筛定位（45+ 微信私域、甜点 200–1500、品类偏好与排除）
  · 选品系统 v2 生效条件页：目标利润率 60%，下限 45%（目标的 75%）→ 低于则淘汰
  · 主人 2026-10-22 实测教训：「玻璃灯罩」「可躺儿童露营车」是好货 —— 材质/用途词只降档，不许一刀切
"""
from __future__ import annotations

import json
import os
import re
import time

_HERE = os.path.dirname(os.path.abspath(__file__))          # …\server\v3
CFG_DIR = os.path.join(os.path.dirname(_HERE), "..", "config")
CFG = os.path.abspath(os.path.join(CFG_DIR, "selection_criteria.json"))

# 热读缓存（按 mtime 失效）—— 改文件立刻生效，不用重启
_CACHE: dict = {"mtime": 0.0, "d": None}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 默认条件（首次运行写盘用）

def default() -> dict:
    return {
        "_README": "★ 选品条件唯一真源。界面「⚙ 选品条件」改的就是这里；"
                   "AI 判定提示词由本文件自动生成（不要手写长提示词，改这里的数值即可）。",
        "_改完怎么生效": "保存即热读生效，不用重启；已有判定不会自动重跑，重跑那一批才用新条件。",
        "meta": {"version": 1, "updated": _now(), "editor": "系统默认（迁移自 selection_profile + fine_positioning）"},

        "business": {
            "_说明": "经营模式决定什么算风险。无货源代发 → 明确品牌=风险，不是加分项。",
            "model": "无货源",
            "supply": "拼多多一件代发",
            "channel": "微信小店（视频号/朋友圈/私域）",
            "price_markup": 1.25,
            "brand_is_risk": True,
        },

        "audience": {
            "_说明": "买家画像。判定靠『卖点用词』，不猜年龄。",
            "age": "45 岁以上",
            "traits": ["重实用耐用，不为设计溢价", "怕麻烦：看重免安装/上门/保修",
                       "看实拍和短视频下单，不看长文案", "信任厂家直发/熟人推荐"],
            "positive": {"免安装": 3, "包安装": 3, "上门安装": 4, "送货上门": 3, "保修": 3, "质保": 3,
                         "承重": 2, "加厚": 2, "加固": 2, "耐用": 3, "结实": 2, "实拍": 2, "实测": 2,
                         "厂家直发": 3, "工厂直发": 3, "源头工厂": 3, "一件代发": 2,
                         "加大": 2, "加宽": 2, "稳固": 2, "省力": 2, "护腰": 2, "老人": 3},
            "negative": {"ins风": 4, "网红": 4, "潮流": 4, "小众设计": 4, "Z世代": 4, "学生": 3,
                         "宿舍": 4, "情侣": 3, "少女": 4, "可爱": 3, "卡通": 3, "潮玩": 4,
                         "二次元": 4, "汉服": 3, "显瘦": 4, "百搭": 3, "拍照出片": 3},
            "floor": 25,
            "weak_below": 45,
            "min_positive_hits": 1,
        },

        "price": {
            "_说明": "★ 2026-11-09 主人口径：不算利润，只看最低价是否约 200 元以上；低于硬刷线直接刷掉。",
            "min_price": 200, "min_price_tolerance_pct": 5,
            "sweet": [200, 1500],
            "acceptable": [100, 3000],
            "too_low_below": 100,
            "too_high_above": 3000,
            "penalty": {"too_low": 15, "too_high": 15},
            "card_price_trust": "low",
        },

        "category": {
            "_说明": "preferred 加权；excluded 命中即拦；excluded_soft 只是材质/用途词，只降档（实测教训：一刀切会杀光户外货）。",
            "preferred": {
                "户外野营装备": ["折叠椅", "月亮椅", "钓鱼椅", "露营桌", "野营桌", "天幕", "帐篷",
                                 "便携收纳", "露营", "折叠桌", "躺椅", "营地车", "露营车"],
                "灯具照明": ["庭院灯", "太阳能灯", "户外壁灯", "投光灯", "草坪灯", "露营灯",
                             "应急灯", "灯串", "落地灯"],
                "家居大件": ["置物架", "收纳架", "人体工学椅", "衣帽架", "鞋柜", "边几",
                             "晾衣架", "储物", "茶几", "餐桌"],
            },
            "excluded": {
                "服装鞋帽": ["T恤", "卫衣", "连衣裙", "裤子", "内衣", "袜子", "运动鞋", "鞋垫",
                             "围巾", "外套", "羽绒服", "尺码", "码数", "试穿"],
                "生鲜食品": ["鲜花", "生鲜", "水果", "蛋糕", "零食", "茶叶", "白酒", "红酒"],
                "快消复购": ["面膜", "化妆品", "护肤品", "洗发水", "纸巾", "洗衣液", "牙膏",
                             "保健品", "维生素", "药品", "膏药", "隐形眼镜"],
                "母婴宝宝": ["婴儿车", "宝宝", "童装", "纸尿裤", "奶瓶", "婴儿床", "辅食"],
                "数码小件": ["手机壳", "数据线", "耳机", "充电宝", "贴膜", "鼠标垫", "键盘膜"],
            },
            "excluded_soft": {
                "易碎材质": ["玻璃", "陶瓷", "石材", "大理石"],
                "可能含母婴宠物用途": ["儿童", "母婴", "宠物", "猫", "狗"],
                "服装相关": ["鞋", "靴", "帽", "袜"],
            },
        },

        "brand": {
            "_说明": "无货源模式下品牌=经营风险。旗舰店/官方店 + 标题含品牌名 = 直接拦。",
            "block_if": ["店铺为旗舰店或官方店", "标题命中品牌词库", "标题出现与店铺同名的品牌"],
            "warn_if": ["专卖店/专营店", "标题疑似品牌但证据不足"],
            "shop_types_block": ["旗舰店", "官方旗舰", "官方店", "品牌直营", "自营"],
            "shop_types_warn": ["专卖店", "专营店"],
            "shop_types_good": ["工厂店", "厂家直销", "源头工厂", "个人店", "个体店", "普通店"],
        },

        "quality_risk": {
            "_说明": "货本身有风险（侵权/翻新/尾货）。命中 ≥ block_at 直接拦，并留原文证据。",
            "words": {"高仿": 5, "复刻": 4, "原单": 4, "尾单": 4, "尾货": 4, "剪标": 5,
                      "瑕疵": 3, "次品": 4, "二手": 4, "拆机": 4, "翻新": 5, "洋垃圾": 5,
                      "代购": 3, "清仓": 2, "甩卖": 3, "外贸原单": 5, "贴牌": 2},
            "block_at": 4,
        },

        "market": {
            "_说明": "★ 行情与上架价值（2026-10-28 新增）：光『符合定位』不够，还要问值不值得上架。"
                     "利润按 JSON 里的数值算，不由模型猜；模型只判竞争强度/售后风险这类语义。",
            "target_margin_pct": 60,
            "min_margin_pct": 45,
            "min_price_gap_yuan": 30,
            "avoid_words": ["退货率高", "易变形", "需专业安装", "定制不退", "无售后", "三无产品"],
            "good_signals": ["厂家直发", "一件代发", "支持分销", "现货", "48小时发货"],
            "note": "卡片价不可信时，利润只做提示，不据此拦货。",
        },

        "mask": {
            "_说明": "★ 打码需求（2026-10-28 新增）：图上出现他人品牌/店铺名/水印/电话/网址/二维码 → 上架前必须打码。"
                     "判定只能给出『建议 + 依据』，最终由 4.x 人工确认（不自动改图）。",
            "must_mask_when": ["图上出现品牌名或 logo", "图上出现别家店铺名", "水印/网址/电话/微信",
                               "二维码或小程序码", "价格标签/促销贴"],
            "no_need_when": ["纯白底、无任何文字", "文字只有自己的商品名"],
            "auto_words": ["旗舰店", "官方", "专卖", "微信", "www.", ".com", "店", "厂家直销",
                           "扫码", "电话", "限时", "促销"],
            "note": "OCR 命中 auto_words 只是线索；模型要结合上下文判断是不是他人标识。",
        },

        "verdict": {
            "_说明": "判定优先级：硬拦 > 拦 > 警告 > 通过。任一层判拦就拦，并保留全部层的理由。",
            "block_if": ["quality_risk", "category_excluded", "brand_hit", "audience_mismatch"],
            "warn_if": ["no_description", "thin_info", "price_off_band", "audience_weak",
                        "category_soft", "market_unclear", "mask_needed"],
            "min_score_pass": 45,
            "score_weights": {"audience": 40, "category": 30, "price": 15, "info": 15},
        },

        "ai_prompt": {
            "_说明": "extra 会追加到生成提示词末尾；output_schema 不许删字段（界面/后续模块按字段读）。",
            "extra": "",
            "output_schema": {"verdict": "pass|block|warn", "comment": "一段人话，必须引用原文片段",
                              "mask": {"need": True, "why": "依据"}, "worth": "0-100",
                              "needs_detail": "true|false", "confidence": "0-100"},
        },
    }


# ---------------------------------------------------------------- 读 / 写

def _mtime() -> float:
    try:
        return os.path.getmtime(CFG)
    except Exception:                                    # noqa: BLE001
        return 0.0


def load(force: bool = False) -> dict:
    """读条件（热读）。文件不存在/坏了 → 用默认并**不覆盖用户文件**。"""
    mt = _mtime()
    if not force and _CACHE["d"] is not None and mt == _CACHE["mtime"]:
        return _CACHE["d"]
    d = None
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                                    # noqa: BLE001
        d = None
    if not isinstance(d, dict) or not d:
        d = default()
    _CACHE.update({"mtime": mt, "d": d})
    return d


def save(d: dict, editor: str = "界面") -> dict:
    """原子写回 + 备份上一版。写完立刻热读生效。"""
    if not isinstance(d, dict) or not d:
        return {"ok": False, "error": "条件为空，拒绝写入"}
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    # 备份（只留最近 10 份，别把目录撑爆）
    try:
        if os.path.exists(CFG):
            bak = CFG + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            with open(CFG, "rb") as s, open(bak, "wb") as t:
                t.write(s.read())
            olds = sorted([x for x in os.listdir(os.path.dirname(CFG))
                           if x.startswith("selection_criteria.json.bak-")])
            for x in olds[:-10]:
                try:
                    os.remove(os.path.join(os.path.dirname(CFG), x))
                except Exception:                        # noqa: BLE001
                    pass
    except Exception:                                    # noqa: BLE001
        pass
    d.setdefault("meta", {})
    d["meta"]["version"] = int(d["meta"].get("version") or 1) + 1
    d["meta"]["updated"] = _now()
    d["meta"]["editor"] = editor
    tmp = CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG)
    _CACHE.update({"mtime": _mtime(), "d": d})
    return {"ok": True, "path": CFG, "version": d["meta"]["version"],
            "updated": d["meta"]["updated"], "prompt_chars": len(render_rules(d))}


def ensure_file() -> dict:
    """首次运行：把默认条件落盘（有就什么都不做）。"""
    if not os.path.exists(CFG):
        return save(default(), editor="系统初始化")
    return {"ok": True, "existed": True, "path": CFG}


# ---------------------------------------------------------------- 摘要（界面显示用）

def summary(d: dict | None = None) -> dict:
    d = d or load()
    cat = d.get("category") or {}
    aud = d.get("audience") or {}
    mk = d.get("market") or {}
    ms = d.get("mask") or {}
    pr = d.get("price") or {}

    def _n(groups) -> int:
        return sum(len(v) for v in (groups or {}).values() if isinstance(v, list))

    return {
        "version": (d.get("meta") or {}).get("version"),
        "updated": (d.get("meta") or {}).get("updated"),
        "price": "%s–%s（可接受 %s–%s）" % (
            (pr.get("sweet") or ["?", "?"])[0], (pr.get("sweet") or ["?", "?"])[1],
            (pr.get("acceptable") or ["?", "?"])[0], (pr.get("acceptable") or ["?", "?"])[1]),
        "audience": "%s · 正向 %d 词 / 负向 %d 词 · 及格线 %s" % (
            aud.get("age") or "-", len(aud.get("positive") or {}),
            len(aud.get("negative") or {}), d.get("verdict", {}).get("min_score_pass")),
        "preferred_n": _n(cat.get("preferred")),
        "excluded_n": _n(cat.get("excluded")),
        "soft_n": _n(cat.get("excluded_soft")),
        "margin": "目标 %s%% / 下限 %s%%" % (mk.get("target_margin_pct"), mk.get("min_margin_pct")),
        "mask_rules": len(ms.get("must_mask_when") or []),
        "prompt_chars": len(render_rules(d)),
    }


# ---------------------------------------------------------------- 提示词生成

def render_rules(d: dict | None = None) -> str:
    """把结构化条件渲染成**给模型看的中文规则**。改条件 → 提示词自动变。"""
    d = d or load()
    b = d.get("business") or {}
    a = d.get("audience") or {}
    p = d.get("price") or {}
    c = d.get("category") or {}
    br = d.get("brand") or {}
    q = d.get("quality_risk") or {}
    mk = d.get("market") or {}
    ms = d.get("mask") or {}
    v = d.get("verdict") or {}

    def _words(groups, limit=40) -> str:
        out = []
        for gname, ws in (groups or {}).items():
            if str(gname).startswith("_"):
                continue
            if isinstance(ws, list):
                out.append("%s：%s" % (gname, "、".join(ws[:limit])))
        return "；".join(out)

    def _kw(mapping, limit=40) -> str:
        ks = [k for k in (mapping or {}) if not str(k).startswith("_")]
        return "、".join(ks[:limit])

    L = []
    L.append("【生意模型】%s；供货 %s；渠道 %s；定价 %s。"
             % (b.get("model") or "-", b.get("supply") or "-", b.get("channel") or "-",
                ("加价 %.0f%%" % ((float(b.get("price_markup") or 1) - 1) * 100))
                if b.get("price_markup") else "-"))
    if b.get("brand_is_risk"):
        L.append("★ 无货源代发拿不到品牌授权 → **明确品牌 = 经营风险**（可能被投诉下架），不是加分项。")

    L.append("\n【买家画像】%s，%s。"
             % (a.get("age") or "-", "；".join(a.get("traits") or []) or "-"))
    if _kw(a.get("positive")):
        L.append("正向卖点词（命中越多越对路）：%s" % _kw(a.get("positive")))
    if _kw(a.get("negative")):
        L.append("★ 反向词（说明卖家在打年轻人/潮流市场，与定位冲突）：%s" % _kw(a.get("negative")))
    L.append("人群分 0–100；低于 %s 判『定位不符』拦住；%s–%s 之间只警告。"
             % (a.get("floor"), a.get("floor"), a.get("weak_below")))

    sp, ac = p.get("sweet") or [None, None], p.get("acceptable") or [None, None]
    _fl = float(p.get("min_price") or 200)
    _tol = float(p.get("min_price_tolerance_pct") or 5)
    L.append("\n【价格门槛（★ 唯一价格判据）】最低价约 %.0f 元以上才留（含 %.0f%% 容差，"
             "硬刷线约 %.0f 元）；低于门槛直接刷掉，不进人工审核。甜点 %s–%s、可接受 %s–%s 只作记录。"
             % (_fl, _tol, _fl * (1 - _tol / 100.0), sp[0], sp[1], ac[0], ac[1]))
    L.append("★ 不算利润、不算加价率、不算价差、不猜采购成本 —— 本包没有这些数据，"
             "不许据此判「判不了」或要求人工看。")

    if c.get("preferred"):
        L.append("\n【优先品类（加权）】%s" % _words(c.get("preferred")))
    if c.get("excluded"):
        L.append("【排除品类（明确卖的就是这类货 → 拦）】%s" % _words(c.get("excluded")))
    if c.get("excluded_soft"):
        L.append("【软排除（只是材质/用途描述词 → 只警告，不许拦）】%s。"
                 "例：玻璃灯罩、可躺儿童露营车的『玻璃/儿童』不该杀货。" % _words(c.get("excluded_soft")))

    if br.get("shop_types_block"):
        L.append("\n【品牌与店铺】★ 拼多多采集的数据：**普通旗舰店不是删除条件，不许因为店名带『旗舰/官方』就拦**。"
                 "只有商品挂了黑底「品牌」角标（payload 里 ui.brand_section.black_brand / "
                 "secondary_api.params.black_brand / mall_official_qualification 官方品牌资质）"
                 "才必屏蔽；其余旗舰店、专卖店、专营店一律不拦。"
                 "非拼多多包（微信小店）仍按：店铺类型命中 %s 或标题命中品牌词库 → 拦。"
                 % "、".join(br.get("shop_types_block") or []))
        L.append("店铺类型命中 %s → 警告并压低分；命中 %s → 视为白牌正向信号。"
                 % ("、".join(br.get("shop_types_warn") or []),
                    "、".join(br.get("shop_types_good") or [])))

    if q.get("words"):
        L.append("\n【质量风险词（命中权重 ≥ %s 直接拦）】%s" % (q.get("block_at"), _kw(q.get("words"), 40)))

    L.append("\n【不评估的内容（★ 2026-11-09 主人口径）】")
    L.append("利润率 / 加价率 / 售价与采购价差 / 竞争强度 / 销量 / 退货率 / 采购成本 / 市场规模："
             "本包没有这些数据，**一律不评、不猜、不据此拦货、不据此要求人工核对**。")
    L.append("只判商品本身：品类、人群、品牌风险、质量风险、价格门槛。"
             "行情风险词与正向信号只作线索记录，不参与生死判定。")

    L.append("\n【打码需求（★ 第二轮必须回答）】")
    L.append("必须打码的情形：%s。" % "；".join(ms.get("must_mask_when") or []))
    L.append("无需打码的情形：%s。" % "；".join(ms.get("no_need_when") or []))
    L.append("OCR 出现这些词只是线索（%s），要结合上下文判断是不是**他人**标识；"
             "本店自己的商品名不算。你只给建议，不改图。"
             % "、".join((ms.get("auto_words") or [])[:20]))

    L.append("\n【判定口径】优先级 硬拦 > 拦 > 警告 > 通过：")
    L.append("拦住：%s" % "、".join(v.get("block_if") or []))
    L.append("警告：%s" % "、".join(v.get("warn_if") or []))
    L.append("综合分低于 %s 且没有硬拦项 → 至少给警告，不许静默通过。" % v.get("min_score_pass"))

    extra = str((d.get("ai_prompt") or {}).get("extra") or "").strip()
    if extra:
        L.append("\n【店长补充要求（最高优先）】%s" % extra)
    return "\n".join(L)


# ---------------------------------------------------------------- 本地硬闸门（不花 token）

def _hit_words(text: str, mapping: dict) -> list[tuple[str, int]]:
    t = str(text or "")
    out = []
    for w, wt in (mapping or {}).items():
        if str(w).startswith("_") or not w:
            continue
        if w in t:
            try:
                out.append((w, int(wt or 1)))
            except Exception:                            # noqa: BLE001
                out.append((w, 1))
    return out


def _first_group_hit(text: str, groups: dict) -> tuple[str, list[str]]:
    """命中哪个组的前几个词（组名, 词列表）。"""
    t = str(text or "")
    for gname, ws in (groups or {}).items():
        if str(gname).startswith("_") or not isinstance(ws, list):
            continue
        hit = [w for w in ws if w and w in t]
        if hit:
            return gname, hit
    return "", []


def pdd_brand_marks(payload) -> list[str]:
    """拼多多「品牌黑标 / 官方品牌店」实体证据（2026-11-09 主人口径）。

    ★ 对拼多多采集的数据：**普通旗舰店不是必要的删除条件**。只有
      ① 商品挂了黑底「品牌」角标（软件上那块黑色底图、写着品牌二字）
      ② 品牌官方旗舰店（有官方品牌资质）
      才屏蔽；其他普通旗舰店一律不管。
      判据一律取 payload 里的实体字段，**不看店名后缀** —— 实测 223 件包：
      40 件店名带「旗舰/官方」但无任何品牌标识（旧口径误拦），
      15 件有黑标但店名不带旗舰（旧口径漏拦）。

    字段来源（已逐件核验，均为拼多多 goods 详情响应）：
      ui.brand_section.black_brand                         黑底「品牌」角标
      secondary_api.params.black_brand                    同上镜像
      mall_entrance.mall_data.mall_official_qualification  官方品牌资质
      ...goods_detail_endorse_vo.brand_authorize_cell       品牌授权角标
      ...brand_more_enhance_endorse_vo...                   品牌增强标
    返回证据文字列表；空列表 = 不是品牌标识商品，**不拦**。
    """
    d = payload if isinstance(payload, dict) else {}
    g = d.get("goods") if isinstance(d.get("goods"), dict) else {}
    legacy = ((d.get("store") or {}).get("initDataObj") or {}).get("goods")
    if isinstance(legacy, dict) and legacy.get("goodsID"):
        g = legacy
        # 老版页面快照把品牌角标藏在 goods.ui / oakData.ui 里，镜像到新卡字段位置
        ui = legacy.get("ui") or (((d.get("store") or {}).get("initDataObj") or {})
                                 .get("oakData") or {}).get("ui") or {}
        d = dict(d)
        d["ui"] = ui if isinstance(ui, dict) else {}
    if not (g.get("goods_id") or g.get("goodsID")):
        # 不是拼多多包（微信小店是 _raw/itemId），旧口径不动
        return []
    out: list[str] = []
    ui = d.get("ui") or {}
    bb = (ui.get("brand_section") or {}).get("black_brand")
    if not (isinstance(bb, dict) and bb and (bb.get("brand") or bb.get("desc"))):
        bb = ((d.get("secondary_api") or {}).get("params") or {}).get("black_brand")
    if isinstance(bb, dict) and bb and (bb.get("brand") or bb.get("desc")):
        out.append("黑底「品牌」角标：%s" % str(bb.get("brand") or bb.get("desc"))[:24])
    md = ((d.get("mall_entrance") or {}).get("mall_data") or {})
    q = md.get("mall_official_qualification") or {}
    if isinstance(q, dict) and q.get("brand_icon") and q.get("brand_name"):
        out.append("官方品牌资质：%s" % str(q.get("brand_name"))[:24])
    ba = (md.get("goods_detail_endorse_vo") or {}).get("brand_authorize_cell") or {}
    if isinstance(ba, dict) and ba.get("brand_name"):
        out.append("品牌授权标：%s" % str(ba.get("brand_name"))[:24])
    bv = ((md.get("brand_more_enhance_endorse_vo") or {}).get("brand_more_enhance_cell_vo") or {})
    if isinstance(bv, dict) and bv.get("brand_name"):
        out.append("品牌增强标：%s" % str(bv.get("brand_name"))[:24])
    return out


def gate(title: str = "", price=None, desc: str = "", ocr_text: str = "",
         category: str = "", shop: str = "", reasons: list | None = None,
         payload=None, source_platform: str = "") -> dict:
    """本地硬闸门：能靠词/价位/类目判的，先判掉。AI 只复核剩下的。

    reasons = 规则层已经摆出的线索（[{level:'block'|'warn', msg:..}]），
              level=block 的并入硬拦 —— 这样品牌词库/违规词库的结论不会被 AI 推翻。

    返回 {verdict_hint, block[], warn[], score, need_mask, mask_why[], evidence[]}
    """
    d = load()
    a, p, c = d.get("audience") or {}, d.get("price") or {}, d.get("category") or {}
    br, q = d.get("brand") or {}, d.get("quality_risk") or {}
    ms, v = d.get("mask") or {}, d.get("verdict") or {}

    blob = "\n".join([str(title or ""), str(desc or ""), str(ocr_text or ""), str(category or ""),
                      str(shop or "")])
    # 打码只看「图上/文案里的字」，不看店铺名 —— 否则「工厂店」里的『店』会误判成要打码
    # 2026-10-28 再收窄：只看**图上读到的字（OCR）**。标题/描述里出现「厂家直销」是本店卖点，
    # 不是他人标识，算进去会让几乎每件货都被标「需打码」。
    mask_blob = str(ocr_text or "")
    block: list[str] = []
    warn: list[str] = []
    ev: list[dict] = []

    # ⓪ 规则层已经判死的（品牌词库/违规词库等）—— AI 不许推翻
    for r in (reasons or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("level") or "") == "block":
            msg = str(r.get("msg") or r.get("code") or "规则层判拦")[:80]
            if msg not in block:
                block.append("规则层：%s" % msg)
                ev.append({"kind": "rule_block", "msg": msg})

    # ① 质量风险
    qh = _hit_words(blob, q.get("words") or {})
    bad = [(w, wt) for w, wt in qh if wt >= int(q.get("block_at") or 4)]
    if bad:
        block.append("质量风险：%s" % "、".join("%s(%d)" % x for x in bad[:4]))
        ev.append({"kind": "quality_risk", "hits": [w for w, _ in bad]})
    elif qh:
        warn.append("疑似质量风险词：%s" % "、".join(w for w, _ in qh[:4]))

    # ② 品牌 / 店铺 —— ★ 2026-11-09 主人口径（拼多多专用）
    #    旧：店名含「旗舰店/官方店」一律硬拦 → 223 件包里 85 件这么被拦，
    #        其中 40 件 payload 里根本没有品牌标识，是纯店名误伤。
    #    新：拼多多包只认**商品上的品牌标识实体**（黑底「品牌」角标 / 官方品牌资质），
    #        普通旗舰店不拦；非拼多多包（微信小店）保持旧口径不动。
    # ★ 2026-11-14：老版拼多多页面快照卡平台码是 pdd_legacy，同样按拼多多口径走，
    #   否则会退回「非拼多多包」的旧店名规则，把「官方旗舰店」整批误拦（11-09 刚修过的乌龙）。
    is_pdd = str(source_platform or "").lower() in (
        "pdd", "pdd_legacy", "pinduoduo", "拼多多", "拼多多（老版详情卡）")
    marks = pdd_brand_marks(payload) if is_pdd else []
    if is_pdd:
        if marks:
            block.append("拼多多品牌标识商品（必屏蔽）：" + "；".join(marks[:2]))
            ev.append({"kind": "pdd_brand_mark", "marks": marks[:3]})
    else:
        sb = [t for t in (br.get("shop_types_block") or []) if t and t in str(shop or "")]
        if sb:
            block.append("店铺类型=%s（无货源拿不到品牌授权）" % sb[0])
            ev.append({"kind": "shop_type", "hit": sb[0]})
    sw = [t for t in (br.get("shop_types_warn") or []) if t and t in str(shop or "")]
    if sw:
        warn.append("店铺类型=%s（仅警告，不拦）" % sw[0])
    sg = [t for t in (br.get("shop_types_good") or []) if t and t in str(shop or "")]
    if sg:
        ev.append({"kind": "shop_good", "hit": sg[0]})

    # ③ 类目
    gname, hits = _first_group_hit(blob, c.get("excluded") or {})
    if gname:
        block.append("排除品类「%s」：命中 %s" % (gname, "、".join(hits[:3])))
        ev.append({"kind": "category_excluded", "group": gname, "hits": hits[:3]})
    else:
        gname2, hits2 = _first_group_hit(blob, c.get("excluded_soft") or {})
        if gname2:
            warn.append("软排除「%s」（只是描述词，不拦）：%s" % (gname2, "、".join(hits2[:3])))
            ev.append({"kind": "category_soft", "group": gname2, "hits": hits2[:3]})
    upname, uphits = _first_group_hit(blob, c.get("preferred") or {})
    if upname:
        ev.append({"kind": "category_preferred", "group": upname, "hits": uphits[:3]})

    # ④ 人群（正/负向词）
    pos = _hit_words(blob, a.get("positive") or {})
    neg = _hit_words(blob, a.get("negative") or {})
    pos_sc = min(60, sum(wt for _, wt in pos) * 6)
    neg_sc = min(60, sum(wt for _, wt in neg) * 8)
    aud = max(0, min(100, 55 + pos_sc - neg_sc))
    if aud < int(a.get("floor") or 25):
        block.append("人群分 %d < %d（定位不符，像卖给年轻人）" % (aud, int(a.get("floor") or 25)))
        ev.append({"kind": "audience_mismatch", "score": aud,
                   "neg": [w for w, _ in neg[:4]]})
    elif aud < int(a.get("weak_below") or 45):
        warn.append("人群分 %d 偏低（未到 %d）" % (aud, int(a.get("weak_below") or 45)))

    # ⑤ 价格 —— ★ 2026-11-09 主人口径：不算利润，只看「最低价约 200 以上」
    #   硬刷线 = min_price ×(1 - 容差%) ≈ 190；低于它 → 硬拦
    #   （gate.verdict_hint=block → judge() 直接返回 block，不花 token、不进人工）
    #   价格缺失 → 不硬拦，交 AI 按商品本身判，避免字段缺失把好货全刷掉
    _floor = float(p.get("min_price") or 200)
    _tol = float(p.get("min_price_tolerance_pct") or 5)
    _hard = _floor * (1.0 - _tol / 100.0)
    try:
        pv = float(price) if price not in (None, "", 0) else None
    except Exception:                                    # noqa: BLE001
        pv = None
    if pv is not None and pv < _hard:
        block.append("最低价 %.0f 元 < 约 %.0f 元门槛（容差 %.0f%% → 硬刷线 %.0f 元），刷掉"
                     % (pv, _floor, _tol, _hard))
        ev.append({"kind": "price_below_floor", "price": pv, "floor": _floor, "hard": _hard})
    price_sc = 60
    if pv is not None:
        lo, hi = (p.get("sweet") or [None, None])[0], (p.get("sweet") or [None, None])[1]
        alo, ahi = (p.get("acceptable") or [None, None])[0], (p.get("acceptable") or [None, None])[1]
        if lo and hi and lo <= pv <= hi:
            price_sc = 100
        elif alo and ahi and alo <= pv <= ahi:
            price_sc = 80
        else:
            price_sc = 45
            warn.append("价位 %s 元在可接受区间外（只降档，不判死）" % pv)
            ev.append({"kind": "price_off_band", "price": pv})

    # ⑥ 打码需求（线索级，最终 AI + 人工确认）
    #    只认长度 ≥2 的词：单字「店」会把「工厂店」这种店铺名误判成要打码（2026-10-28 实测）
    #    另外剔掉纯营销词：它们出现在图上多半是本店自己贴的卖点，不算他人标识
    _MASK_SKIP = {"厂家直销", "限时", "促销", "清仓", "包邮"}
    mask_hits = [w for w in (ms.get("auto_words") or [])
                 if w and len(str(w)) >= 2 and str(w) not in _MASK_SKIP
                 and str(w) in mask_blob]
    need_mask = bool(mask_hits)

    # ⑦ 信息完整度
    info = 40
    if str(desc or "").strip():
        info += 30
    if str(ocr_text or "").strip():
        info += 30
    if info <= 40:
        warn.append("详情/图片文字缺失，信息不足")
        ev.append({"kind": "thin_info"})

    w = v.get("score_weights") or {}
    score = int(round(
        aud * (float(w.get("audience") or 40) / 100.0)
        + (100 if upname else 50) * (float(w.get("category") or 30) / 100.0)
        + price_sc * (float(w.get("price") or 15) / 100.0)
        + info * (float(w.get("info") or 15) / 100.0)))
    if score < int(v.get("min_score_pass") or 45) and not block:
        warn.append("综合分 %d < 及格线 %s" % (score, v.get("min_score_pass")))

    verdict = "block" if block else ("warn" if warn else "pass")
    return {"verdict_hint": verdict, "block": block, "warn": warn, "score": score,
            "need_mask": need_mask, "mask_why": mask_hits[:6],
            "audience_score": aud, "evidence": ev,
            "conditions_version": (d.get("meta") or {}).get("version")}


def need_mask(ocr_text: str = "", title: str = "") -> dict:
    """单独查：这组图需不需要打码（给 4.x 打码步骤用）。

    ★ 只认 OCR（图上读到的字）。title 参数留着兼容老调用，但**不参与判定** ——
      本店标题里写「旗舰店同款」不构成打码需求。
    """
    d = load()
    blob = str(ocr_text or "")
    skip = {"厂家直销", "限时", "促销", "清仓", "包邮"}
    hits = [w for w in ((d.get("mask") or {}).get("auto_words") or [])
            if w and len(str(w)) >= 2 and str(w) not in skip and str(w) in blob]
    return {"need": bool(hits), "why": hits[:8],
            "rules": (d.get("mask") or {}).get("must_mask_when") or []}
