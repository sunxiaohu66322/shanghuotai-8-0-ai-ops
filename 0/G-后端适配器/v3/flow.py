# -*- coding: utf-8 -*-
"""v3 · 流水线门面

把导入 → 细筛 → 画像 → 标题 → 主图交接 → 出包 串成**可恢复**的一条链：

    · 每一步都落 v3_job（谁、什么时候、成功失败、错误原文）
    · 每一步都幂等（同一阶段重跑不会产生重复数据）
    · 阶段之间的门必须由人推：await_package / await_image / await_publish

前端只需要调这里的函数；HTTP 只是它的薄壳。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from typing import Any, Iterable

from . import (finelog, images, importer, keywords, packaging,
               profile as prof_mod, store, titles)

STAGES = ["collect", "prescreen", "export_urls", "await_package", "import",
          "fine_screen", "image_check", "title", "await_image", "image_approve",
          "upload", "export_package", "await_publish", "done"]

STAGE_LABEL = {
    "collect": "① 采集", "prescreen": "② 初筛", "export_urls": "③ 出 URL 清单",
    "await_package": "④ 等外部下载", "import": "⑤ 导入数据包",
    "fine_screen": "⑥ 细筛去重", "image_check": "⑦ 图片合规",
    "title": "⑧ 标题优化", "await_image": "⑨ 等外部出图",
    "image_approve": "⑩ 确认换主图", "upload": "⑪ 图床上传",
    "export_package": "⑫ 出商品包", "await_publish": "⑬ 等外部上架",
    "done": "⑭ 完成",
}

STATUS_LABEL = {
    "imported": "已导入", "fine_screened": "已细筛", "blocked": "被拦下",
    "profiled": "已画像", "titled": "已出标题", "image_handoff": "待换主图",
    "packaged": "已出包", "published": "已上架",
}


def conn() -> sqlite3.Connection:
    c = store.connect()
    store.init(c)
    return c


# ---------------------------------------------------------------- 概览

def overview(c: sqlite3.Connection | None = None) -> dict:
    own = c is None
    cc = c or conn()
    try:
        by_status = {r["status"]: r["n"] for r in cc.execute(
            "SELECT status, COUNT(*) n FROM v3_product GROUP BY status")}
        total = sum(by_status.values())
        d = {
            "ok": True,
            "db": store.DB_PATH,
            "total": total,
            "by_status": by_status,
            "by_status_label": {STATUS_LABEL.get(k, k): v for k, v in by_status.items()},
            "images": cc.execute("SELECT COUNT(*) FROM v3_image").fetchone()[0],
            "images_missing_url": cc.execute(
                "SELECT COUNT(*) FROM v3_image WHERE role IN ('main','carousel','detail','sku')"
                " AND (current_remote_url IS NULL OR current_remote_url='')"
                " AND (original_url IS NULL OR original_url='')").fetchone()[0],
            "titles_selected": cc.execute(
                "SELECT COUNT(*) FROM v3_title_variant WHERE status='selected'").fetchone()[0],
            "titles_candidate": cc.execute(
                "SELECT COUNT(*) FROM v3_title_variant WHERE status='candidate'").fetchone()[0],
            "packages": len(packaging.list_out_files()),
            "published": cc.execute(
                "SELECT COUNT(*) FROM v3_publish WHERE outcome='published'").fetchone()[0],
            "keywords": keywords.stats(cc),
            "ai": titles.ai_status(),
            "stores": [dict(r) for r in cc.execute("SELECT * FROM v3_store ORDER BY store_id")],
            "stages": [{"code": s, "label": STAGE_LABEL[s]} for s in STAGES],
        }
        return d
    finally:
        if own:
            cc.close()


# ---------------------------------------------------------------- 细筛（P1）

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _norm_title(t: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t or "").lower()

# ---------------------------------------------------------------- 层公共件

_ROLE_CN = {"main": "主图", "carousel": "轮播图", "detail": "详情图",
            "sku": "SKU图", "video": "视频", "unknown": "图"}

# 材质词：只用来判「标题写 A、详情图里是 B」，**不参与违规判定**
MATERIAL_WORDS = ["铝合金", "不锈钢", "碳钢", "锰钢", "铁艺", "实木", "原木", "竹",
                  "藤", "真皮", "牛皮", "亚麻", "棉麻", "涤纶", "尼龙", "牛津布",
                  "塑料", "亚克力", "玻璃", "陶瓷", "硅胶", "ABS", "PP", "EVA"]

_DESC_KEYS = ("描述", "详情", "商品描述", "图文详情", "desc", "description", "detail")

# ★ 2026-10-26：电商常规用语白名单 —— 出现在图上/描述里是卖点，不是违规。
# 实测误拦：1382 详情图上的「当天发货」被判成 image_risk_word·block。
# 词库里为了管「虚假宣传」收了这些词，但它们对「能不能上架」不成立。
# 判据：这个词本身描述的是**服务承诺或交易方式**，不是商品属性、不是品牌、不是绝对化用语。
_ECOM_NORMAL = {
    "当天发货", "当日发货", "24小时发货", "48小时发货", "当天发出", "当日发出",
    "现货", "现货直发", "现货供应", "现货秒发",
    "正品", "正品保证", "正品保障", "官方正品",
    "一件代发", "厂家直销", "工厂直销", "源头工厂", "工厂直发",
    "七天无理由", "7天无理由", "无理由退换", "运费险", "退货包运费",
    "极速退款", "闪电发货", "顺丰包邮", "全国包邮",
}

# 营销词：第 4 步涂白就能救，精筛**只记 warn**。
# 判据：这些词不是违规、也不涉及别人的牌子，一刀切拦下来只会把整包都拦光。
# 营销词表已并入 words.MARKETING_WORDS（唯一来源，第 3 步与第 4 步共用同一份）。
# 2026-10-24 之前这里有一份手写清单，与 words 那份并存会再次分叉，故改为引用。
from .words import MARKETING_WORDS  # noqa: E402 


def _img_label(role: str, seq) -> str:
    """「主图#3」这种人话标签 —— 界面和 block 原因都用它，人一眼知道是哪张。"""
    return "%s#%s" % (_ROLE_CN.get(role or "", role or "图"), seq or 1)


def _core_words(cr: dict) -> set[str]:
    """类目核心词表：品牌词如果和它撞车，说明那是通用词（词库噪声）。"""
    out: set[str] = set()
    for _cat, _ws in (cr.get("category_keywords") or {}).items():
        if str(_cat).startswith("_"):
            continue
        for _w in (_ws or []):
            if _w:
                out.add(str(_w))
    for w in ("徒步", "户外", "露营", "野营", "运动", "登山"):
        out.add(w)
    return out


def _payload_of(p: dict) -> dict:
    try:
        d = store.unj(p.get("original_payload"), {}) or {}
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _desc_text(p: dict) -> str:
    """详情文本：导入的 payload 里那几个可能装描述的键，谁先有值用谁。"""
    d = _payload_of(p)
    for k in _DESC_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (list, dict)) and v:
            return store.j(v)
    return ""


def _attr_text(p: dict) -> str:
    """属性文本 = 导入的 attributes + 画像里人确认过的 verified_attributes。"""
    parts: list[str] = []
    try:
        a = store.unj(p.get("attributes"), {}) or {}
        if isinstance(a, dict):
            parts += ["%s=%s" % (k, v) for k, v in a.items() if v not in (None, "")]
    except Exception:  # noqa: BLE001
        pass
    try:
        pr = store.unj(p.get("profile"), {}) or {}
        va = (pr or {}).get("verified_attributes") or {}
        if isinstance(va, dict):
            parts += ["%s=%s" % (k, v) for k, v in va.items() if v not in (None, "")]
    except Exception:  # noqa: BLE001
        pass
    return "；".join(parts)


def _image_reasons(hits: list[dict], core_words: set[str], title: str = "",
                   cap: int = 6) -> list[dict]:
    """图片 OCR 命中 → 精筛理由。

    分类口径与文本层一致（同一套噪声判断），但**图片层不许重复扣分**：
      · 通用词 / 短词                → warn（疑似词库噪声，可豁免）
      · 该词**标题里也写着**          → warn（文本层已经判过，这里只当证据留着）
      · 标题没写、图上却有的品牌词     → block（这正是图片层要抓的漏网之鱼）
      · 营销词（特价/包邮/直播…）      → warn（第 4 步涂白就能救）
      · 其余违规词 / 夸张词 / 打码词   → block
    同一张图同一个词只留一条，最多 cap 条。
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    up_title = (title or "").upper()
    for h in hits:
        w = (h.get("word") or "").strip()
        if not w:
            continue
        key = (w, h.get("role"), h.get("sequence"))
        if key in seen:
            continue
        seen.add(key)
        generic = (w in core_words) or (len(w) <= 2 and not w.isascii())
        kind = h.get("kind") or ""
        label = _img_label(h.get("role"), h.get("sequence"))
        conf = h.get("conf")
        in_title = bool(up_title) and (w.upper() in up_title)
        base = {"word": w, "kind": kind, "waivable": True, "source": "image",
                "layer": "image", "image": label, "image_id": h.get("image_id"),
                "snippet": h.get("snippet") or "", "confidence": conf}
        if kind == "hype":
            # 广告法绝对化用语（最佳/独家/万能/特效/永久…）：这份表是人工核过的小名单，
            # 不是从词库主表里捞的，所以**再短也敢 block** —— 必须放在「短词当噪声」前面，
            # 否则「最佳」会被 2 字规则吞成 warn。
            base.update({"code": "image_hype_word", "level": "block",
                         "msg": "%s 上有绝对化用语「%s」，标题里没有（OCR 片段：%s｜"
                                "匹配置信度 %.1f）—— 广告法红线，第 4 步涂白或改图"
                                % (label, w, base["snippet"], conf or 0.0)})
        elif kind == "mask":
            # ★ 2026-10-26 主人定调（原话）：
            #   「这种词到时候是遮蔽，而不是说整个就不通过。照他这样的改法的话，我一件都别通过了。」
            # 打码词表（J:\aixitong\小猪\词库\打码词.txt，2460 条）里的「质保」「必备」这类，
            # 存在的意义是**图上那块要遮掉**，不是货不能上架。原来落到最后的 else 判 block，
            # 一件货身上随便印两个词就整件拦下 —— 一包能全灭。
            # ★ 必须放在 generic（短词噪声）前面：「质保」「必备」都是 2 字，
            #   否则会被当成词库噪声，文案写成"疑似噪声"、mask 标记丢失，第 4 步就不知道要遮。
            base.update({"code": "image_mask_word", "level": "warn",
                         "mask": True,
                         "msg": "%s 上有需遮蔽词「%s」（OCR 片段：%s｜置信度 %.1f）"
                                "—— 第 4 步涂白/遮蔽即可，**不拦货**"
                                % (label, w, base["snippet"], conf or 0.0)})
        elif w in MARKETING_WORDS:
            # ★ 同样提前到 generic 之前：营销词里大量是 2 字（包邮/热销/特价），
            #   否则会被短词规则吞成"疑似词库噪声"，失去"涂白就能救"这个结论。
            base.update({"code": "image_marketing_word", "level": "warn",
                         "msg": "%s 上有营销词「%s」—— 第 4 步涂白就能救，不拦"
                                "（OCR 片段：%s）" % (label, w, base["snippet"])})
        elif generic:
            base.update({"code": "image_suspected_brand", "level": "warn",
                         "msg": "%s 上读出「%s」，但它是通用词/短词 —— 疑似词库噪声，"
                                "请人工确认（OCR 片段：%s）"
                                % (label, w, base["snippet"])})
        elif in_title:
            base.update({"code": "image_word_in_title", "level": "warn",
                         "msg": "%s 上也读出「%s」—— 标题里本来就有，文本层已判过，"
                                "这里只留证据不重复扣分（OCR 片段：%s）"
                                % (label, w, base["snippet"])})
        elif kind == "brand":
            # 图上出现品牌字 → **只记 warn**，靠证据让人判，不直接拦货。
            # 实测依据（2026-10-22 商品明细 48 件）：
            #   ① OCR 没有中英别名归并 —— 标题写「斯凯奇」、图上印 SKECHERS，是同牌子，
            #      按 block 处理会把它当成图文不符，一拦一个准；
            #   ② 同批还出现 AR / AONIJIE 这类图上确实是别的牌子、但标题也自带品牌的情形，
            #      机器分不清「别名」与「另一个牌子」，这一刀只能人来补。
            base.update({"code": "image_brand_word", "level": "warn",
                         "msg": "%s 上有品牌字「%s」，标题里没有这个写法 —— 可能是同牌的"
                                "中英别名，也可能是别的牌子，请人工确认（OCR 片段：%s｜"
                                "匹配置信度 %.1f）"
                                % (label, w, base["snippet"], conf or 0.0)})
        elif w in _ECOM_NORMAL:
            # ★ 2026-10-26：电商常规用语不是违规。
            # 实测被误拦：1382 详情图上的「当天发货」被判成违规字（image_risk_word·block）。
            # 这类词是发货时效/服务承诺的卖点，出现在图上完全正常。
            # 词库里因为要管「虚假宣传」把它们收进去了，但对「能不能上架」不成立。
            base.update({"code": "image_normal_word", "level": "warn",
                         "msg": "%s 上有电商常规用语「%s」—— 是卖点不是违规，"
                                "**不拦货**（OCR 片段：%s）"
                                % (label, w, base["snippet"])})
        else:
            base.update({"code": "image_risk_word", "level": "block",
                         "msg": "%s 上有违规字「%s」，而**标题里没有**（OCR 片段：%s｜"
                                "匹配置信度 %.1f）"
                                % (label, w, base["snippet"], conf or 0.0)})
        out.append(base)
        if len(out) >= cap:
            break
    return out


def _evidence_lines(reasons: list[dict], cap: int = 4) -> list[str]:
    """给界面「证据」列用的短行：图角色#序号 · 命中词 · OCR 片段。"""
    lines = []
    for r in reasons:
        if r.get("layer") != "image" and r.get("code") not in (
                "desc_risk_word", "brand_mismatch", "material_mismatch", "ai_verdict"):
            continue
        head = r.get("image") or {"desc_risk_word": "描述", "brand_mismatch": "图文",
                                  "material_mismatch": "图文", "ai_verdict": "AI"}.get(
                                      r.get("code"), "详情")
        w = r.get("word") or ""
        sn = (r.get("snippet") or "").strip()
        if not sn:
            lines.append("%s · %s · %s" % (head, w, str(r.get("msg") or "")[:50]))
        else:
            lines.append("%s · %s · %s" % (head, w, sn))
        if len(lines) >= cap:
            break
    return lines


def _detail_layer(c: sqlite3.Connection, p: dict, title: str, img_hits: list[dict],
                  img_texts: dict[str, str], imgs: list[dict],
                  core_words: set[str]) -> tuple[list[dict], dict]:
    """详情层：描述文本 / 属性 / 详情图 OCR 三源合并后判定。

    覆盖三类（任务要求的三条，缺一不可）：
      ① 描述含违规词    → block（描述是买家最能看清的地方，写违规词比标题更危险）
      ② 图文不一致      → 品牌：标题没有、详情图上印着 → 2 张以上 block，1 张 warn
                          材质：标题写铝合金、详情图上写不锈钢 → warn
      ③ 缺关键信息      → warn（缺描述不拦：很多包压根没导描述，一刀切会全灭）
    """
    reasons: list[dict] = []
    stat = {"products_desc_hit": 0, "products_brand_mismatch": 0,
            "products_material_mismatch": 0, "products_no_desc": 0,
            "products_no_detail": 0, "products_thin_info": 0}
    desc = _desc_text(p)
    attrs = _attr_text(p)
    det_imgs = [i for i in imgs if i["role"] == "detail"]

    # ① 描述含违规词
    if desc:
        for h in keywords.risk_hits(c, desc)[:6]:
            w = h["text"]
            generic = (w in core_words) or (len(w) <= 2 and not w.isascii())
            pos = desc.find(w)
            snip = desc[max(0, pos - 24):pos + 34] if pos >= 0 else desc[:58]
            reasons.append({"code": "desc_suspected_brand" if generic else "desc_risk_word",
                            "level": "warn" if generic else "block",
                            "word": w, "kind": h["kind"], "waivable": True,
                            "source": h.get("source") or "", "layer": "detail",
                            "snippet": snip[:60],
                            "msg": ("商品描述里出现%s词「%s」（片段：%s）"
                                    % ("疑似噪声的" if generic else "",
                                       w, snip[:60]))})
        if any(r["level"] == "block" and r["code"] == "desc_risk_word" for r in reasons):
            stat["products_desc_hit"] = 1
    else:
        reasons.append({"code": "no_description", "level": "warn", "layer": "detail",
                        "msg": "没有商品描述（详情文本为空）—— 缺关键信息，记 warn 不拦"})
        stat["products_no_desc"] = 1

    # ③ 缺详情图（详情层的一半证据直接没有）
    if not det_imgs:
        reasons.append({"code": "no_detail_image", "level": "warn", "layer": "detail",
                        "msg": "没有详情图 —— 详情层只剩描述/属性可用，记 warn 不拦"})
        stat["products_no_detail"] = 1

    # 证据链覆盖率：标题 / 属性 / 描述 / 详情图 四样齐几样（候选方案，
    # 思路取自「10 维选品 SOP」的关键数据覆盖率，不照搬它的数字）
    sig = [bool((title or "").strip()), bool(attrs.strip()), bool(desc.strip()), bool(det_imgs)]
    got = sum(1 for x in sig if x)
    if got < 3:
        reasons.append({"code": "thin_info", "level": "warn", "layer": "detail",
                        "msg": "关键信息只有 %d/4 项（标题/属性/描述/详情图），"
                               "证据链太薄，选品结论难成立" % got})
        stat["products_thin_info"] = 1

    # ④ 关键证据偏薄 → warn（2026-11-09 主人口径：不挂人工，由 AI 按商品本身判两档）
    #    不再只是 warn：只有一张主图的货，连「图上看没看错」都没机会核，
    #    判「通过」等于让过。这一档既不是 blocked（没违规）也不是通过（没法判）。
    #    另外提醒：详情细采（detail_ingest）已在判定前跑过；到这里还缺，
    #    说明「页面确实没给」或「补采没成」——两种情况 payload['detail_fetch'] 里都有据可查。
    if not det_imgs and not desc.strip():
        miss = []
        if not det_imgs:
            miss.append("详情图 0 张")
        if not desc.strip():
            miss.append("描述为空")
        fmark = {}
        try:
            _pl = _payload_of(p)
            _fm = _pl.get("detail_fetch")
            fmark = _fm if isinstance(_fm, dict) else {}
        except Exception:                    # noqa: BLE001
            fmark = {}
        tail = ""
        if fmark.get("state") == "ok":
            tail = "；已细采过（state=ok），是页面本身没给"
        elif fmark.get("state"):
            tail = "；细采未成：state=%s %s" % (fmark.get("state"),
                                              str(fmark.get("reason") or "")[:60])
        else:
            tail = "；尚未细采（浏览器离线或未开细采）"
        reasons.append({"code": "evidence_missing", "level": "warn", "layer": "detail",
                        "waivable": False, "missing": miss,
                        "msg": "关键证据偏薄（%s）—— 只记线索，不挂起等人工；"
                               "由 AI 按商品本身判 pass/block %s" % ("、".join(miss), tail)})
        stat["products_evidence_missing"] = 1

    det_hits = [h for h in img_hits if (h.get("role") == "detail")]
    if det_hits:
        # ②a 图文不一致（品牌）：标题里没有的品牌，详情图上印着
        # 门槛要卡严：详情图多是长图/表格，OCR 会把表格线、标点读成字（实测读到
        # 「百学间凉感性能 防外机」这种串行），2 字中文品牌词在上面命中率极低。
        # 所以只认「3 字以上中文 / 4 字以上英文」的品牌词，短词一律当噪声丢掉。
        def _brand_ok(w: str) -> bool:
            if not w or w in core_words:
                return False
            return (len(w) >= 4) if w.isascii() else (len(w) >= 3)

        by_word: dict[str, set] = {}
        for h in det_hits:
            if (h.get("kind") or "") != "brand":
                continue
            w = h.get("word") or ""
            if not _brand_ok(w) or w in (title or ""):
                continue
            by_word.setdefault(w, set()).add((h.get("role"), h.get("sequence")))
        flagged = 0
        for w, where in by_word.items():
            n = len(where)
            reasons.append({"code": "brand_mismatch", "level": "block" if n >= 2 else "warn",
                            "word": w, "kind": "brand", "waivable": True,
                            "source": "image", "layer": "detail",
                            "snippet": next((h.get("snippet") or "" for h in det_hits
                                             if h.get("word") == w), "")[:60],
                            "msg": "图文不一致：标题里没有品牌「%s」，详情图 OCR 里出现了"
                                   "（%d 张）%s" % (w, n,
                                                   "" if n >= 2 else "，只有 1 张，先记 warn")})
            flagged = 1
            if len([r for r in reasons if r["code"] == "brand_mismatch"]) >= 3:
                break
        stat["products_brand_mismatch"] = flagged

        # ②b 图文不一致（材质）：标题写 A、详情图 OCR 里只有 B
        det_text = (img_texts or {}).get("detail") or ""
        t_mat = [m for m in MATERIAL_WORDS if m in (title or "")]
        d_mat = [m for m in MATERIAL_WORDS if m in det_text]
        if t_mat and d_mat and not (set(t_mat) & set(d_mat)):
            reasons.append({"code": "material_mismatch", "level": "warn",
                            "layer": "detail", "source": "image",
                            "word": t_mat[0], "waivable": False,
                            "msg": "图文不一致：标题写材质「%s」，详情图 OCR 里出现的是"
                                   "「%s」—— 请人工核对是不是同一件货"
                                   % ("、".join(t_mat[:2]), "、".join(d_mat[:2]))})
            stat["products_material_mismatch"] = 1
    return reasons, stat

def _positioning_layer(p: dict, title: str, img_texts: dict | None = None,
                       word_set=None) -> tuple[list[dict], dict]:
    """选品定位层 —— 把「卖给谁 / 什么品类 / 什么价位」变成可执行判定。

    口径来源：config\\fine_positioning.json（由 config\\selection_profile.json 提炼，
    用户 10-18 拍板：45+ 微信人群 · 200–1500 甜点价 · 户外/灯具/家居大件 · 排除鞋服与易碎）。

    返回 (reasons, 摘要)。reasons 与其它层同构（code/level/msg/word/waivable），
    另带 `positioning: True` + `layer: "positioning"`，界面据此单独归组，
    让人分得清「这个词判错了」和「这货不对路」——两者处置完全不同。
    """
    try:
        from . import positioning as _pos          # noqa: PLC0415
    except Exception:                              # noqa: BLE001
        try:
            import positioning as _pos             # noqa: PLC0415
        except Exception as e:                     # noqa: BLE001
            return [], {"enabled": False, "error": "%s: %s" % (type(e).__name__, e)}
    try:
        if not (_pos.cfg() or {}):
            return [], {"enabled": False, "error": "配置为空：%s" % _pos.config_path()}
        ocr_list = [(str(k).split("#")[0], 0, v)
                    for k, v in (img_texts or {}).items() if v]
        ev = _pos.evaluate(
            {"product_id": p.get("product_id"), "original_title": title,
             "price_min": p.get("price_min")},
            {"title": title, "ocr": ocr_list},
            price=p.get("price_min"), word_set=word_set)
    except Exception as e:                         # noqa: BLE001
        return [], {"enabled": True, "error": "%s: %s" % (type(e).__name__, e)}

    words = [str(x.get("word") or "") for x in (ev.get("evidence") or []) if x.get("word")]
    out: list[dict] = []
    for r in ev.get("reasons") or []:
        code = str(r.get("code") or "unknown")
        lvl = "block" if r.get("level") == "block" else "warn"
        it = {"code": "pos_" + code, "level": lvl,
              "msg": (r.get("detail") or r.get("msg") or code),
              "layer": "positioning", "positioning": True,
              "pos_code": code, "waivable": False}
        # 命中了具体词的（人群负向词 / 类目词 / 风险词），跟其它层一样允许按词豁免：
        # 用户能看到「拦我的是这个词」，才有办法把它判错的地方改掉。
        if words:
            it["word"] = words[0]
            it["kind"] = "positioning"
            it["waivable"] = True
        out.append(it)
    meta = {"enabled": True, "verdict": ev.get("verdict"), "score": ev.get("score"),
            "layers": ev.get("layers") or {}, "config": _pos.config_path(),
            "block": sum(1 for x in out if x["level"] == "block"),
            "warn": sum(1 for x in out if x["level"] == "warn")}
    return out, meta


def fine_screen(c: sqlite3.Connection, pids: Iterable[int] | None = None,
                actor: str = "fine", with_images: bool = False,
                with_detail: bool = False, limit: int = 0,
                max_images_per_product: int = 0,
                force_ocr: bool = False, progress=None,
                with_positioning: bool = True,
                with_ai: bool = True,
                with_fetch: bool | None = None, fetch_limit: int = 0,
                fetch_gap: float = 0.8, fetch_port: int = 0,
                fetch_host: str = "", fetch_force: bool = False) -> dict:
    """精筛：文本规则 + **图片层** + **详情层**，三层结论合成一次判定。

    ── 上限口径（2026-10-22 改：默认全量，不再有隐形名额）──────────────
      · limit=0（**默认**）或 limit=None → **筛全部**：包里有几件就过几件。
        调用方不传 limit 拿到的就是全量，想在界面上「设个上限」必须显式传 N。
      · limit=N（N>0）                   → 只处理前 N 件（试跑 / 分段跑）。
      · remaining 的语义：**这次没处理到的件数**（= 总数 − 本次处理数），
        不是「被 limit 挡掉的数」—— 筛全部时恒为 0，分段跑时如实报还剩多少。

    ── 可观测（新）──────────────────────────────────────────────
      progress 是可调用对象，签名 progress(event: dict)。事件顺序：
        start → (product_begin → product_done)×N → [checkpoint…] → done
          start         total / with_images / with_detail / limit / words / engine
          product_begin idx / total / pid / title(前30字) / images
          product_done  idx / total / pid / verdict / reasons / images /
                        downloaded / ocr / cached / hit_images / seconds
          checkpoint    processed / remaining / eta_seconds
                        （total>100 时每 25 件一次，省得长跑没声音）
          done          total / passed / blocked / warned / seconds / logs
      **回调抛异常一律吞掉**：日志自己的 bug 不许把精筛搞崩。
      同一批事件同时落一份 JSONL（一行一个 + ts），路径在返回体 log_path；
      返回体 stats["lines"] 另有人话版逐件日志（≤500 条），格式：
          [3/304] pid=123 图12 下载3 OCR9(缓存7) 命中2 → 拦(risk_word) 1.4s

    ⚠ 两个层的默认值是 **关** —— 老调用方（HTTP /api/v3/flow/fine-screen）
      行为与以前一模一样，不会突然开始下几百张图。要开就显式传
      with_images=True / with_detail=True（第 3 步页签就是这么调的）。
      图片层被跳过时，start 事件里必须写清缺什么，**不许静默**。

    原有（文本层，不变）：风险词 / 跨类目 / 类目黑名单 / 标题与主图重复。
    新增：
      · 图片层  把包内每张图下载下来、用第 4 步那台本机 PP-OCR 读字，产出
                「图角色#序号 + 命中词 + OCR 片段 + 匹配置信度」；命中即 block，
                原因里能一眼看出是**哪张图的哪段字**。
      · 详情层  用商品描述 + 属性 + 详情图 OCR 合并判定：
                描述含违规词(block)、图文不一致(品牌/材质)、缺关键信息(warn)。
                缺信息的只记 warn，不一刀切 —— 缺描述不等于不能上。

    可分段可止损：
      · max_images_per_product  单件最多读几张图（0=全部）
      · 断点续跑         已 OCR 的图吃 v3_image_ocr 缓存，不重复读、不重复下
      · 单件失败         记一条 warn 后继续下一件，不让一件卡死整包
      · 逐件 commit      不设上限时也不攒内存：每件判完就落库，中断不丢已判过的

    不删数据：命中就标 blocked + 写清原因，第 3 步本页随时可人工「放行」。

    ── 2026-10-22 起（用户指令，两处硬改）──────────────────────────────
      · **判定前先补证据**：with_fetch 默认跟随 with_detail —— 第 3 步传了
        with_detail=True 就等于开着。先用 detail_ingest.ensure_evidence() 对
        「缺详情图 / 缺库存 / 缺 SKU / 缺描述」的件采一次详情页，把图落到
        `data/v3/images/<pid>/{role}_{image_id}.jpg`、字段写回 payload，
        再写 `manifest.json` / `ocr.json`，然后才走三层判定。
        幂等判据只在 `detail_ingest.need_fetch()` 一处：已采过（state=ok）、
        无详情页地址（no_url）、被验证页拦下（blocked）、重试超限的都不再采。
        浏览器调试口不在线 → 整段 SKIP，**不假装采过**（返回体 fetch.reason 写明）。
      · **缺关键证据不得判通过**：无详情图 **且** 无描述 → verdict='pending'
        （待补充证据），v3_product.status='pending'，不计数为通过、不进 keep。
        通过必须有正向证据：有详情图或有描述，且图片层/定位层没有 block。
    """
    t_start = time.time()
    log = finelog.FineLog()          # 先开日志文件：后面每个事件都往它落一行
    MAX_LINES = 500
    lines: list[str] = []

    def emit(ev: dict) -> None:
        """落 JSONL + 喂 progress。**两头都不许抛**：日志坏了不能把精筛搞崩。"""
        try:
            log.emit(ev)
        except Exception:            # noqa: BLE001
            pass
        if progress is not None:
            try:
                progress(ev)
            except Exception:        # noqa: BLE001  回调自己的 bug 由回调自己负责
                pass

    if pids:
        items = [store.get_product(c, int(p)) for p in pids]
        items = [x for x in items if x]
    else:
        items = store.list_products(c, limit=100000)
    total_wanted = len(items)
    lim = int(limit or 0)            # 0 / None 都当「全量」，不许再有隐形名额
    if lim > 0:
        items = items[:lim]
    total_n = len(items)

    cr = keywords.load_category_rules()
    excl = [str(x) for x in (cr.get("exclude_categories") or [])]
    fragile = [str(x) for x in (cr.get("fragile_words") or [])]
    install = [str(x) for x in (cr.get("install_words") or [])]
    # 人已经判过「这个词不是违规词」的，一律不再参与判定。
    # ★ 这一层必须加：餐桌/沙发 这类词既在 v3_keyword 里、又在 category_rules 的
    #   exclude_categories 里；只把 v3_keyword 置 disabled 是拦不住这边的直连比较的
    #   （实测：豁免「餐桌」后通过数一件都没变 —— 就是漏了这一层）。
    try:
        waived = {r[0] for r in c.execute("SELECT text FROM v3_keyword_waiver")}
    except Exception:  # noqa: BLE001
        waived = set()
    excl = [w for w in excl if w not in waived]
    fragile = [w for w in fragile if w not in waived]
    install = [w for w in install if w not in waived]
    core_words = _core_words(cr)

    # ---- 图片层准备：引擎在不在、词表建没建 ----
    img_layer = {"enabled": bool(with_images), "skipped": False, "reason": "",
                 "engine": images.OCR_ENGINE_NAME, "words": 0}
    word_set = None
    # ★★ 2026-10-25 P0-01：**词表与引擎解耦**。
    #   旧实现在「引擎不可用」时连词表都不建 → start 事件写 `words: 0`，
    #   看上去像「词库空了」，实际是「引擎缺包把词表一起跳过了」。
    #   两个完全不同的故障被压成同一个数字，害人按错误方向查。
    #   词表只读文件 + 库，不需要引擎，所以**无论如何都先建**。
    try:
        word_set = images.build_words(c)
        img_layer["words"] = word_set.size
    except Exception as e:  # noqa: BLE001
        img_layer["words_error"] = "%s: %s" % (type(e).__name__, e)
    if with_images:
        av = images.ocr_available()
        img_layer["available"] = av.get("ok")
        img_layer["missing"] = av.get("missing") or []
        if not av.get("ok"):
            img_layer["skipped"] = True
            img_layer["reason"] = ("引擎不可用，本层记 SKIP：缺 %s"
                                   % "、".join(av.get("missing") or []))
        else:
            # 文件都在 ≠ 能跑：模型损坏、onnxruntime 与 numpy 版本打架，都要到真加载时才知道。
            # 这里先试加载一次（约 1.5 秒，只在这一层开的时候付），失败就整层记 SKIP ——
            # 否则每件商品都写一条「引擎不可用」，把真正的理由淹掉。
            if images._load_ocr() is None:      # noqa: SLF001 —— 同包内的能力探测
                img_layer["skipped"] = True
                img_layer["reason"] = ("引擎加载失败，本层记 SKIP：%s"
                                       % (images.ocr_engine_error() or "未知原因"))
    det_layer = {"enabled": bool(with_detail)}

    # 引擎状态**必须进 start 事件**：图片层被跳过时要把「缺什么」写在脸上，不许静默
    if not with_images:
        engine_txt = "off（本次没开图片层）"
    elif img_layer["skipped"]:
        engine_txt = "error：%s" % (img_layer["reason"] or "未知原因")
    else:
        engine_txt = "ok"

    emit({"stage": "start", "total": total_n, "with_images": bool(with_images),
          "with_detail": bool(with_detail), "limit": lim,
          "words": int(img_layer.get("words") or 0), "engine": engine_txt,
          "engine_name": images.OCR_ENGINE_NAME,
          "max_images_per_product": int(max_images_per_product or 0),
          "force_ocr": bool(force_ocr),
          "title": "精筛开始：词表 %d 条 / 引擎 %s"
                   % (int(img_layer.get("words") or 0), engine_txt)})

    # ---------------- 判定前先补证据（详情细采接线，2026-10-22） ----------------
    # 顺序很重要：必须在主循环之前。因为图片层(ocr_product)在每件开头读 v3_image，
    # 详情图若在主循环里才插进去，这一轮 OCR 就覆盖不到它们，详情层照样是空的。
    fetch_info = {"enabled": False, "reason": "", "needed": 0, "fetched": 0,
                  "skipped": 0, "failed": 0, "images_downloaded": 0,
                  "images_failed": 0, "evidence_written": 0, "errors": [],
                  "notes": [], "elapsed_sec": 0.0}
    # ★★ 2026-10-23 主人明确指令：**第三步不许自己开浏览器逐个抓商品页**。
    #   「他这边去识别了以后，他就要去淘宝一个一个打开页面去抓取，这个是不对的，
    #     这样要违规的。这种比较细的商品解析包，我这边会用别的软件去解析」
    #   所以：默认**不补采**；只有调用方显式传 with_fetch=True 才会去开页面。
    want_fetch = bool(with_fetch) if with_fetch is not None else False
    if want_fetch and items:
        t_fetch = time.time()
        fetch_info["enabled"] = True
        emit({"stage": "fetch_begin", "total": total_n,
              "title": "补齐详情证据：先采详情页再判定（浏览器调试口 127.0.0.1:%s）"
                       % (int(fetch_port or 0) or 9222)})
        di = None
        try:
            from . import detail_ingest as di          # noqa: PLC0415
        except Exception:                              # noqa: BLE001
            try:
                import detail_ingest as di             # noqa: PLC0415
            except Exception as e:                     # noqa: BLE001
                fetch_info["reason"] = "detail_ingest 加载失败：%s: %s" % (
                    type(e).__name__, e)
        if di is not None:
            try:
                fr = di.ensure_evidence(
                    c, [x["product_id"] for x in items],
                    limit=int(fetch_limit or 0), gap=float(fetch_gap or 0.8),
                    force=bool(fetch_force),
                    port=int(fetch_port or 0) or di.DEFAULT_PORT,
                    host=(fetch_host or di.DEFAULT_HOST),
                    progress=lambda ev: emit(dict({"stage": "fetch_item"}, **(ev or {}))))
                for k in ("needed", "skipped", "fetched", "failed"):
                    fetch_info[k] = int(fr.get(k) or 0)
                fst = fr.get("stats") or {}
                fetch_info["images_downloaded"] = int(fst.get("images_downloaded") or 0)
                fetch_info["images_failed"] = int(fst.get("images_download_failed") or 0)
                fetch_info["evidence_written"] = int(fst.get("evidence_written") or 0)
                fetch_info["errors"] = (fr.get("errors") or [])[:50]
                fetch_info["notes"] = fr.get("notes") or []
                if not fr.get("ok"):
                    fetch_info["reason"] = fr.get("error") or "补采未完成（原因未给）"
            except Exception as e:                     # noqa: BLE001
                fetch_info["reason"] = ("细采异常，判定按现有证据继续：%s: %s"
                                        % (type(e).__name__, str(e)[:160]))
        fetch_info["elapsed_sec"] = round(time.time() - t_fetch, 1)
        emit({"stage": "fetch_done",
              "ok": not (fetch_info["reason"] and not fetch_info["fetched"]),
              "needed": fetch_info["needed"], "fetched": fetch_info["fetched"],
              "skipped": fetch_info["skipped"], "failed": fetch_info["failed"],
              "images_downloaded": fetch_info["images_downloaded"],
              "reason": fetch_info["reason"],
              "title": "详情证据：需补 %d 件 → 采到 %d / 跳过 %d（已采过或采不了）/ 失败 %d；"
                       "落图 %d 张、失败 %d 张%s"
                       % (fetch_info["needed"], fetch_info["fetched"],
                          fetch_info["skipped"], fetch_info["failed"],
                          fetch_info["images_downloaded"], fetch_info["images_failed"],
                          ("；" + fetch_info["reason"]) if fetch_info["reason"] else "")})

    stat = {"products": 0, "images_total": 0, "images_downloaded": 0, "images_ocr": 0,
            "images_cached": 0, "images_failed": 0, "products_image_hit": 0,
            "products_image_block": 0,
            "products_desc_hit": 0, "products_brand_mismatch": 0,
            "products_material_mismatch": 0, "products_no_desc": 0,
            "products_no_detail": 0, "products_thin_info": 0,
            "products_pending": 0, "products_evidence_missing": 0,
            "products_warn_only": 0,
            "products_layer_error": 0, "ocr_seconds": 0.0}

    # 重复检测：标题归一化 + 主图 sha
    seen_t: dict[str, int] = {}
    seen_h: dict[str, int] = {}
    picked = {x["product_id"] for x in items}
    for p in store.list_products(c, limit=100000):
        if p["product_id"] in picked:
            continue
        seen_t.setdefault(_norm_title(p.get("original_title")), p["product_id"])
    for r in c.execute("SELECT product_id, content_sha256 FROM v3_image"
                       " WHERE role='main' AND content_sha256<>''"):
        seen_h.setdefault(r["content_sha256"], r["product_id"])

    passed, blocked, pending, dups = [], [], [], []
    pos_meta: dict = {"enabled": False}   # 最后一轮的定位层摘要（引擎/配置状态给界面看）
    rows: list[dict] = []          # 界面「证据」列：通过的也要能看到证据，不能只给拦下的
    for idx, p in enumerate(items, 1):
        t_item = time.time()
        pid = p["product_id"]
        stat["products"] += 1
        # 补证据那一步刚改过库：这里必须重读，否则判的还是补采之前的旧 payload/旧图
        fresh = store.get_product(c, pid)
        if fresh:
            p = fresh
        title = p.get("original_title") or ""
        reasons: list[dict] = []
        imgs = store.list_images(c, pid)
        # ---------------- 拼多多「品牌黑标」机器事实（2026-11-09 主人口径） ----------------
        #   ★ 普通旗舰店不删；只有商品挂了黑底「品牌」角标 / 官方品牌资质才必屏蔽。
        #   提在规则层（与重复图同级），这样**关掉 AI 判官也照样生效**，
        #   且 AI 判官在时不会被降级成 signal 放过去（ai_hard 兜底认它）。
        brand_hard = False
        try:
            if str(p.get("source_platform") or "").lower() in (
                    "pdd", "pdd_legacy", "pinduoduo", "拼多多", "拼多多（老版详情卡）"):
                from . import criteria as _crit       # noqa: PLC0415
                _marks = _crit.pdd_brand_marks(p.get("original_payload"))
                if _marks:
                    brand_hard = True
                    reasons.append({"code": "pdd_brand_mark", "level": "block",
                                    "layer": "rule", "waivable": False,
                                    "msg": "拼多多品牌标识商品（必屏蔽）：" + "；".join(_marks[:2])})
        except Exception as _e:                        # noqa: BLE001
            reasons.append({"code": "pdd_brand_mark_error", "level": "warn", "layer": "rule",
                            "msg": "品牌标识检查没跑成：%s: %s" % (type(_e).__name__, str(_e)[:80])})
        # 逐件计数：图片层没开时它们保持 0，事件里照样有字段（下游不用判 None）
        n_dl = n_ocr = n_cached = n_fail = 0
        hit_imgs: set = set()
        emit({"stage": "product_begin", "idx": idx, "total": total_n, "pid": pid,
              "title": title[:30], "images": len(imgs)})

        # ---------------- 文本层（原有规则，一行没动） ----------------
        hits = keywords.risk_hits(c, title)
        brand_pass_by: list[str] = []
        for h in hits:
            w = h["text"]
            # ★★ 2026-10-27 主人定：**品牌词筛只在 1.0 采集初筛做**（prescreen.py 已有品牌闸门），
            #    3.0 精筛不再重复判品牌。理由：同一条品牌词在两个步骤各拦一次，
            #    人改词表要改两处、还容易只改一处；而 1.0 是唯一入口，那里拦才是拦得住的地方。
            #    这里只记一条「过路提示」，不进阻断。
            if h.get("kind") == "brand":
                if w not in brand_pass_by:
                    brand_pass_by.append(w)
                continue
            generic = (w in core_words) or (len(w) <= 2 and not w.isascii())
            if generic:
                reasons.append({"code": "suspected_brand", "level": "warn",
                                # word / kind 是给界面用的：人点「这不是违规词」时要按词豁免，
                                # 不能只靠 msg 里那对「」去正则抠。
                                "word": w, "kind": h["kind"], "waivable": True,
                                "source": h.get("source") or "",
                                "msg": "「%s」被词库标为%s词，但它是通用词/短词 —— "
                                       "疑似词库噪声，请人工确认后再决定删不删（来源：%s）" % (
                                           w, "品牌" if h["kind"] == "brand" else "违规",
                                           h.get("source") or "-")})
            else:
                reasons.append({"code": "risk_word", "level": "block",
                                "word": w, "kind": h["kind"], "waivable": True,
                                "source": h.get("source") or "",
                                "msg": "命中%s词「%s」（来源：%s）" % (
                                    "品牌" if h["kind"] == "brand" else "违规",
                                    w, h.get("source") or "-")})
        if brand_pass_by:
            stat["products_brand_pass_by"] = stat.get("products_brand_pass_by", 0) + 1
            reasons.append({"code": "brand_pass_by", "level": "warn",
                            "word": brand_pass_by[0], "kind": "brand", "waivable": False,
                            "msg": "标题含品牌词「%s」—— 品牌筛在 1.0 采集初筛做，"
                                   "本步不据此拦货（要拦请回 1.0 调词表）"
                                   % "、".join(brand_pass_by[:3])})
        for w in excl:
            if w and w in title:
                reasons.append({"code": "exclude_category", "level": "block",
                                "word": w, "kind": "category", "waivable": True,
                                "msg": "命中不可做类目「%s」" % w})
        for w in install:
            if w and w in title:
                reasons.append({"code": "needs_install", "level": "warn",
                                "msg": "标题含安装类描述「%s」" % w})
        for w in fragile:
            if w and w in title:
                reasons.append({"code": "fragile", "level": "warn",
                                "msg": "标题含易碎材质「%s」" % w})
        nt = _norm_title(title)
        if nt and nt in seen_t and seen_t[nt] != pid:
            reasons.append({"code": "dup_title", "level": "block",
                            "msg": "与其他商品标题重复（product_id=%d）" % seen_t[nt]})
            dups.append(pid)
        elif nt:
            seen_t.setdefault(nt, pid)
        mains = [i for i in imgs if i["role"] == "main"]
        for m in mains:
            sha = m.get("content_sha256") or ""
            if not sha:
                continue
            owner = seen_h.get(sha)
            if owner and owner != pid:
                reasons.append({"code": "dup_image", "level": "block",
                                "msg": "主图与其他商品完全相同（product_id=%d）" % owner})
                dups.append(pid)
                break
            seen_h.setdefault(sha, pid)

        # ---------------- 图片层 ----------------
        img_hits: list[dict] = []
        img_texts: dict[str, str] = {}
        if with_images and word_set is not None:
            try:
                r = images.ocr_product(c, pid, max_images=max_images_per_product,
                                       force=force_ocr, words=word_set)
                img_hits = r.get("hits") or []
                img_texts = r.get("texts") or {}
                stat["images_total"] += int(r.get("images") or 0)
                stat["images_downloaded"] += int(r.get("downloaded") or 0)
                stat["images_ocr"] += int(r.get("ocr_done") or 0)
                stat["images_cached"] += int(r.get("cached") or 0)
                stat["images_failed"] += int(r.get("failed") or 0)
                stat["ocr_seconds"] += float(r.get("seconds") or 0)
                # 逐件视角的数字（给进度事件/人话日志用；不动上面那套累计口径）
                n_dl = int(r.get("downloaded") or 0)
                n_ocr = int(r.get("ocr_done") or 0)
                n_cached = int(r.get("cached") or 0)
                n_fail = int(r.get("failed") or 0)
                hit_imgs = {h.get("image_id") for h in img_hits if h.get("image_id")}
                for e in (r.get("errors") or []):
                    if len(reasons) < 40:
                        reasons.append({"code": "image_unreadable", "level": "warn",
                                        "msg": "%s 读不出来（%s）—— 这张图这次没参与判定"
                                               % (_img_label(e.get("role"), e.get("image_id")),
                                                  str(e.get("error"))[:80])})
                reasons += _image_reasons(img_hits, core_words, title)
                if img_hits:
                    stat["products_image_hit"] += 1
                if any(r.get("layer") == "image" and r.get("level") == "block"
                       for r in reasons):
                    stat["products_image_block"] += 1
            except Exception as e:  # noqa: BLE001  单件失败不许拖死整包
                stat["products_layer_error"] += 1
                reasons.append({"code": "image_layer_error", "level": "warn",
                                "msg": "这个商品的图片层没跑成：%s: %s（其余层照常判）"
                                       % (type(e).__name__, str(e)[:120])})

        # ---------------- 详情层 ----------------
        if with_detail:
            try:
                d_reasons, d_stat = _detail_layer(c, p, title, img_hits, img_texts,
                                                 imgs, core_words)
                reasons += d_reasons
                for k, v in d_stat.items():
                    stat[k] = stat.get(k, 0) + v
            except Exception as e:  # noqa: BLE001
                stat["products_layer_error"] += 1
                reasons.append({"code": "detail_layer_error", "level": "warn",
                                "msg": "这个商品的详情层没跑成：%s: %s" % (type(e).__name__,
                                                                           str(e)[:120])})

        # ---------------- 选品定位层（45+ 微信人群 / 品类 / 价位 / 风险） ----------------
        # 放在图片层与详情层之后：定位层要吃图上文字，先跑图才不会漏判。
        if with_positioning:
            try:
                p_reasons, p_meta = _positioning_layer(p, title, img_texts, word_set)
                reasons += p_reasons
                pos_meta = p_meta
                if p_meta.get("enabled"):
                    stat["products_positioning"] = stat.get("products_positioning", 0) + 1
                    if any(x["level"] == "block" for x in p_reasons):
                        stat["products_positioning_block"] = \
                            stat.get("products_positioning_block", 0) + 1
            except Exception as e:  # noqa: BLE001
                stat["products_layer_error"] += 1
                reasons.append({"code": "positioning_layer_error", "level": "warn",
                                "msg": "定位层没跑成：%s: %s" % (type(e).__name__,
                                                                str(e)[:120])})

        # ---------------- AI 判官（2026-10-27 主人的口径：整体判定权交 AI） ----------------
        #   规则层从此只负责「把证据摆出来」，生死由 AI 判 —— 因为关键词永远判不了
        #   「标题里出现'拍单号'到底是刷单暗示，还是详情页里的售后凭证模板」这类语义题。
        #   两条除外（机器事实，不问 AI）：同款标题重复、主图完全相同。
        ai_res = None
        # 机器事实，不问 AI：同款标题/主图重复 + 拼多多品牌黑标（主人明确要屏蔽的）。
        # 少了这条，AI 判官会把规则层 block 降级成 signal，黑标货就被放过去了。
        ai_hard = [r for r in reasons
                   if r["code"] in ("dup_title", "dup_image", "pdd_brand_mark")]
        if with_ai:
            ai_fine = None
            try:
                from . import ai_fine as _af            # noqa: PLC0415
                ai_fine = _af
            except Exception:                           # noqa: BLE001
                try:
                    import ai_fine as _af2              # noqa: PLC0415
                    ai_fine = _af2
                except Exception as e:                  # noqa: BLE001
                    reasons.append({"code": "ai_module_error", "level": "warn", "layer": "ai",
                                    "msg": "AI 精筛模块没加载上（%s: %s），本件按关键词规则判"
                                           % (type(e).__name__, str(e)[:100])})
            if ai_fine is not None:
                try:
                    p["_dup"] = bool(ai_hard)
                    ai_res = ai_fine.judge(p, title, reasons, img_hits, img_texts,
                                           price=p.get("price_min"))
                except Exception as e:                  # noqa: BLE001
                    ai_res = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
                if ai_res.get("ok"):
                    stat["products_ai"] = stat.get("products_ai", 0) + 1
                    # ★ 2026-10-27 主人口径：3.0 判定权全交 AI，关键词只当线索。
                    #   AI 既然已经判过，就把规则层的 block/pending 一律降成「参考」，
                    #   否则界面里会出现「写着拦住、结论却是放行」的自相矛盾。
                    #   重复（dup_title / dup_image）是机器事实，不降级，但也由 AI 定夺。
                    for _r in reasons:
                        if _r["level"] in ("block", "pending"):
                            _r["level"] = "signal"
                            _r["signal"] = True
                    tag = {"pass": "放行", "block": "拦住", "warn": "待复核"}.get(
                        ai_res["verdict"], "待复核")
                    reasons.append({"code": "ai_verdict", "level": "info", "layer": "ai",
                                    "ai": True, "ai_verdict": ai_res["verdict"],
                                    "model": ai_res.get("model") or "",
                                    "confidence": ai_res.get("confidence") or 0,
                                    "msg": "【AI·%s·%s】%s%s" % (
                                        tag, ai_res.get("model") or "模型",
                                        ai_res.get("comment") or "（AI 没给点评）",
                                        "（AI 要求人工看详情页核对）"
                                        if ai_res.get("needs_detail") else "")})
                else:
                    stat["products_ai_fail"] = stat.get("products_ai_fail", 0) + 1
                    reasons.append({"code": "ai_unavailable", "level": "warn", "layer": "ai",
                                    "msg": "AI 没参与判定（%s）—— 本件是按关键词规则判的，"
                                           "不能当最终结论，建议稍后重跑或人工看一遍"
                                           % (ai_res.get("error") or "未知原因")})

        # ---------------- 合成判定：机器事实 > AI > 规则 ----------------
        # pending = 关键证据缺失 / AI 判不了（_detail_layer 只在「无详情图 且 无描述」时给这一档）：
        #   它既不进 passed（不计数为通过），也不标 blocked（不是违规）—— 是「没法判」。
        # ★★ 2026-10-25 P0-03：「没执行」不许冒充「没违规」。
        #   图片层整层被跳过时，每件商品都会「零命中」—— 那是没跑，不是干净。
        #   旧实现在这种情况下照样判 passed，于是 18 件「全部通过」。
        #   现在：只要图片层被跳过，本件一律落 pending（证据未就绪），并在理由里写清缺什么。
        if with_images and img_layer.get("skipped"):
            reasons.append({"code": "image_layer_skipped", "level": "warn",
                            "layer": "image",
                            "msg": "图片检测未执行（%s）—— 只记线索，不挂起等人工；"
                                   "由 AI 按现有证据判 pass/block"
                                   % (img_layer.get("reason") or "引擎不可用")})
        blocks = [r for r in reasons if r["level"] == "block"]
        pends = [r for r in reasons if r["level"] == "pending"]
        if ai_res and ai_res.get("ok"):
            # ★★ 2026-11-09 主人口径：「通过的就通过，没通过的就直接刷掉，不要让我来人工审核；
            #   只有争议特别大、实在想不出来的，拉我来看一下」。
            #   两档：pass→通过，block→刷掉，warn 默认→刷掉；
            #   唯一例外 = warn 且模型自己给了 confidence<30（提示词要求「实在判不了才压到 30 以下」）
            #   → pending（争议件，极少数，界面单独列给主人看）。
            #   pends / needs_detail 一律不再影响结论，只留在 note 里做记录。
            if brand_hard:
                verdict, new_status = "blocked", "blocked"
            else:
                av = ai_res["verdict"]
                if av == "block":
                    verdict, new_status = "blocked", "blocked"
                elif av == "pass":
                    verdict, new_status = "passed", "fine_screened"
                else:
                    _conf = int(ai_res.get("confidence") or 0)
                    if _conf < 30:
                        verdict, new_status = "pending", "pending"
                        reasons.append({"code": "disputed", "level": "info", "layer": "ai",
                                        "msg": "★ 争议件：模型自己没把握（confidence=%d<30），"
                                               "留给店主看一眼" % _conf})
                    else:
                        verdict, new_status = "blocked", "blocked"
        elif ai_hard:
            # AI 不可用时的兜底：重复是逐字节相同的事实，不靠模型也能判
            verdict, new_status = "blocked", "blocked"
        elif with_ai:
            # AI 缺席属异常（模型挂了）：不硬判通过放死货，也不冒充已判 → pending 待重跑
            verdict, new_status = "pending", "pending"
        elif blocks:
            verdict, new_status = "blocked", "blocked"
        else:
            verdict, new_status = "passed", "fine_screened"
        # ★ 2026-10-27 主人要求「判定要可以单个单个修改」：人工改过的判定，
        #   重跑精筛（含 AI 重判）不许覆盖 —— 否则人工的结论会被下一批悄悄冲掉。
        #   规则：note.manual 存在 → 以人工为准；AI 判词照样写进 note.ai 供对照。
        _prev = store.unj(p.get("note"), {}) or {}
        manual = _prev.get("manual") if isinstance(_prev, dict) else None
        if isinstance(manual, dict) and manual.get("verdict") in ("pass", "block", "pending"):
            verdict = {"pass": "passed", "block": "blocked",
                       "pending": "pending"}[manual["verdict"]]
            new_status = {"pass": "fine_screened", "block": "blocked",
                          "pending": "pending"}[manual["verdict"]]
            stat["products_manual_kept"] = stat.get("products_manual_kept", 0) + 1
        if verdict == "pending":
            stat["products_pending"] += 1
        note = store.j({"fine_screen": reasons,
                        "manual": manual if isinstance(manual, dict) else None,
                        "verdict": verdict,
                        "ai": ({"verdict": ai_res.get("verdict"),
                                "comment": ai_res.get("comment"),
                                "model": ai_res.get("model"),
                                "confidence": ai_res.get("confidence"),
                                "needs_detail": ai_res.get("needs_detail"),
                                "secs": ai_res.get("secs"),
                                "ok": True} if (ai_res and ai_res.get("ok")) else
                               {"ok": False, "error": (ai_res or {}).get("error") or "",
                                "tried": (ai_res or {}).get("tried") or []}),
                        "judge": "ai" if (ai_res and ai_res.get("ok")) else "rules",
                        "layers": {"image": bool(with_images and word_set is not None),
                                   "detail": bool(with_detail),
                                   "ai": bool(with_ai),
                                   "fetch": bool(fetch_info.get("enabled"))},
                        "at": store.now()})
        c.execute("UPDATE v3_product SET note=?, status=?, updated_at=? WHERE product_id=?",
                  (note, new_status, store.now(), pid))
        if verdict == "blocked":
            blocked.append({"product_id": pid, "reasons": reasons, "title": title,
                            "evidence": _evidence_lines(reasons)})
        elif verdict == "pending":
            pending.append({"product_id": pid, "reasons": reasons, "title": title,
                            "evidence": _evidence_lines(reasons)})
        else:
            passed.append({"product_id": pid, "reasons": reasons, "title": title,
                           "evidence": _evidence_lines(reasons)})
        rows.append({"product_id": pid, "title": title,
                     "verdict": verdict,
                     "n_block": len(blocks), "n_pending": len(pends),
                     "n_warn": len(reasons) - len(blocks) - len(pends),
                     "evidence": _evidence_lines(reasons),
                     "ai_verdict": (ai_res or {}).get("verdict") if (ai_res or {}).get("ok") else "",
                     "ai_comment": (ai_res or {}).get("comment") if (ai_res or {}).get("ok") else
                                   ("AI 未参与：%s" % ((ai_res or {}).get("error") or "")),
                     "ai_model": (ai_res or {}).get("model") or "",
                     "manual": manual if isinstance(manual, dict) else None,
                     "why": "；".join(str(y.get("msg") or y.get("code")) for y in reasons)[:220]})
        c.commit()          # 逐件提交：中断也不丢已判过的

    stat["seconds"] = round(time.time() - t_start, 1)
    det_layer["pending"] = len(pending)
    det_layer["no_desc"] = int(stat.get("products_no_desc") or 0)
    det_layer["no_detail_image"] = int(stat.get("products_no_detail") or 0)
    store.audit(c, "fine_screen", "v3_product", "", None,
                {"passed": len(passed), "blocked": len(blocked),
                 "pending": len(pending), "dups": len(set(dups)),
                 "with_images": bool(with_images), "with_detail": bool(with_detail),
                 "with_ai": bool(with_ai),
                 "ai_ok": int(stat.get("products_ai") or 0),
                 "ai_fail": int(stat.get("products_ai_fail") or 0),
                 "brand_pass_by": int(stat.get("products_brand_pass_by") or 0),
                 "with_fetch": bool(fetch_info.get("enabled")),
                 "fetch": {k: fetch_info.get(k) for k in
                           ("needed", "fetched", "skipped", "failed", "reason")},
                 "limit": limit, "stat": stat},
                actor=actor)
    c.commit()
    # 把「疑似噪声词」聚合成一张清单：界面上直接照着这张表批量豁免，
    # 不用人一句句从商品行的理由里抠词。
    noise: dict[str, dict] = {}
    for x in passed + blocked + pending:
        for r in x["reasons"]:
            if not r.get("waivable") or not r.get("word"):
                continue
            # ★ 不再只收 warn 档：block 档里也有「词判错了」的情况（如被类目词拦下），
            #   以前那些词在豁免区根本找不到 —— 用户报的「找不到可豁免的项」就是这个。
            k = r["word"]
            it = noise.setdefault(k, {"word": k, "kind": r.get("kind") or "",
                                      "source": r.get("source") or "",
                                      "layer": r.get("layer") or "",
                                      "levels": [], "count": 0, "pids": []})
            lv = r.get("level") or "warn"
            if lv not in it["levels"]:
                it["levels"].append(lv)
            it["count"] += 1
            if len(it["pids"]) < 200:
                it["pids"].append(x["product_id"])
    remaining = max(0, total_wanted - stat["products"])
    note_txt = "被拦下的只标记不删除，可在列表里人工翻案"
    if limit and remaining:
        note_txt += "；本次按 limit=%d 只处理了 %d 件，还剩 %d 件（已读过的图不会重读）" % (
            limit, stat["products"], remaining)
    if img_layer["skipped"]:
        note_txt += "；" + img_layer["reason"]
    if pending:
        note_txt += ("；%d 件争议件（模型没把握 / AI 未参与）单列给店主看，"
                     "其余已按两档判完，无需人工审核" % len(pending))
    if fetch_info.get("enabled"):
        note_txt += ("；详情细采：需补 %d 件 → 采到 %d / 跳过 %d / 失败 %d（落图 %d 张）"
                     % (fetch_info["needed"], fetch_info["fetched"],
                        fetch_info["skipped"], fetch_info["failed"],
                        fetch_info["images_downloaded"]))
        if fetch_info.get("reason"):
            note_txt += "；细采未全覆盖：%s" % fetch_info["reason"]
    return {"ok": True, "checked": stat["products"], "passed": len(passed),
            "blocked": len(blocked), "pending": len(pending),
            "duplicates": len(set(dups)),
            "blocked_detail": [b for b in blocked][:50],
            "pending_detail": [b for b in pending][:50],
            "rows": rows[:500],
            "noise": sorted(noise.values(), key=lambda d: -d["count"]),
            "image_layer": img_layer, "detail_layer": det_layer,
            "fetch": fetch_info,
            "positioning": pos_meta,
            "stats": stat, "remaining": remaining,
            "note": note_txt}


# ---------------------------------------------------------------- 批量阶段

def profile_all(c: sqlite3.Connection, pids: Iterable[int] | None = None,
                actor: str = "profile") -> dict:
    ids = list(pids) if pids else [p["product_id"] for p in
                                   store.list_products(c, limit=100000)
                                   if p.get("status") not in ("blocked",)]
    ok, fail, need_review = 0, [], 0
    for pid in ids:
        r = prof_mod.refresh(c, int(pid), actor=actor)
        if r.get("ok"):
            ok += 1
            if r["profile"].get("needs_review"):
                need_review += 1
        else:
            fail.append({"product_id": pid, "error": r.get("error")})
    return {"ok": True, "done": ok, "failed": fail[:20], "needs_review": need_review,
            "note": "needs_review 的必须人工看一眼属性，不许直接拿去生成标题"}


def title_all(c: sqlite3.Connection, pids: Iterable[int] | None = None,
              store_id: str = "default", count: int = 3,
              use_ai: bool | None = None, actor: str = "title") -> dict:
    if pids:
        ids = [int(x) for x in pids]
    else:
        ids = [p["product_id"] for p in store.list_products(c, limit=100000)
               if p.get("status") not in ("blocked",)]
    ok, fail, total, need_core = 0, [], 0, []
    for pid in ids:
        r = titles.generate(c, int(pid), store_id=store_id, count=count,
                            use_ai=use_ai, actor=actor)
        if r.get("ok"):
            ok += 1
            total += r.get("count", 0)
        else:
            fail.append({"product_id": pid, "error": r.get("error")})
            if "核心商品词" in (r.get("error") or ""):
                need_core.append(pid)
    return {"ok": True, "products": ok, "variants": total, "failed": fail[:20],
            "need_core": need_core[:50], "need_core_count": len(need_core),
            "note": "" if not need_core else
            "有 %d 个商品认不出核心商品词 —— 在界面上点「建议核心词」选一个即可，"
            "不许让程序猜" % len(need_core)}


def image_status(c: sqlite3.Connection, limit: int = 100000) -> dict:
    """图片合规/可用性盘点（不下载、不打码，只报事实）。"""
    rows = [dict(r) for r in c.execute(
        "SELECT i.*, p.original_title FROM v3_image i"
        " JOIN v3_product p ON p.product_id=i.product_id LIMIT ?", (limit,))]
    n_local = n_url = n_none = 0
    missing = []
    for i in rows:
        lp, ok = images.resolve_local(i)
        if ok:
            n_local += 1
        elif (i.get("original_url") or "").startswith("http"):
            n_url += 1
            if len(missing) < 50:
                missing.append({"image_id": i["image_id"], "product_id": i["product_id"],
                                "role": i["role"], "url": i["original_url"]})
        else:
            n_none += 1
    return {"total": len(rows), "local_ok": n_local, "url_only": n_url,
            "no_source": n_none, "url_only_sample": missing,
            "note": "url_only 的可以按需下载（images.download_image），整批下载请一次少量"}


# ---------------------------------------------------------------- 一键

def run_chain(c: sqlite3.Connection, pids: Iterable[int] | None = None,
              store_id: str = "default", actor: str = "chain") -> dict:
    """细筛 → 画像 → 标题（不含人工环节）。失败不中断，逐条记账。"""
    jid = store.job_start(c, "chain", None, {"pids": list(pids) if pids else None})
    c.commit()
    out: dict[str, Any] = {}
    try:
        out["fine_screen"] = fine_screen(c, pids, actor=actor)
        ids = [x["product_id"] for x in out["fine_screen"].get("blocked_detail") or []]
        # ★ 指定了 pids 就只处理这些，不要扩散到全库 ——
        #   原来这里无条件 list_products(limit=100000)，导致「按包跑」实际跑了全部商品
        #   （实测：精筛 57 条，画像/标题却动了 116 条）。
        if pids:
            want = {int(p) for p in pids}
            live = [p["product_id"] for p in store.list_products(c, limit=100000)
                    if p.get("status") != "blocked" and int(p["product_id"]) in want]
        else:
            live = [p["product_id"] for p in store.list_products(c, limit=100000)
                    if p.get("status") != "blocked"]
        out["profile"] = profile_all(c, live, actor=actor)
        out["title"] = title_all(c, live, store_id=store_id, actor=actor)
        out["ok"] = True
        out["blocked"] = len(ids)
        store.job_finish(c, jid, True, {k: (v if k != "fine_screen" else
                                            {kk: vv for kk, vv in v.items()
                                             if kk != "blocked_detail"})
                                        for k, v in out.items()})
        c.commit()
        return out
    except Exception as e:  # noqa: BLE001
        store.job_finish(c, jid, False, None, "%s: %s" % (type(e).__name__, e))
        c.commit()
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "partial": out}


def jobs(c: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM v3_job ORDER BY started_at DESC LIMIT ?", (limit,))]
