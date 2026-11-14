# -*- coding: utf-8 -*-
"""3.0 精筛的 AI 判官 —— 规则当证据，AI 当判官。

2026-10-27 主人定的口径（原话）：
    「每一条后面的证据什么东西的，都是要 AI 的点评，就拦住的东西都要 AI 的点评」
    「他很怀疑一个商品详情页里面怎么会有拍单号的，你都要自己去看一下，
      确定没问题，然后再通过」

所以职责这样分：
    规则（关键词 / 图片 OCR / 定位层）→ 只负责**把证据摆出来**，不再单独定生死；
    AI                                → 读证据，给 verdict + 一段人话点评；
    点评原文                               入 note.fine_screen 与 note.ai，界面直接显示。

三条硬约束（都是踩过的坑）：
  1. **不许编证据**：prompt 里只放库里真实存在的字段，没采到就写「未采到」。
  2. **不许沉默地降级**：模型不通必须返回 ok=False + 原因，由调用方明写「AI 未参与判定」。
  3. **无法判断时明说**：AI 可以回 needs_detail=true（要求人看点开详情页核对），
     这种情况进 pending，绝不硬判通过。
"""
from __future__ import annotations

import json
import os
import re
import time

try:
    import ai_providers as A                      # server/ 在 sys.path 上时
except Exception:                                 # noqa: BLE001
    A = None

_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config", "ai_fine.json")

_DEFAULT = {
    "enabled": True,
    "role": "text",
    "timeout": 90,
    "max_tokens": 700,
    "temperature": 0.2,
    "extra": "",
    # ★ 2026-10-27 主人要求：「这个提示词后面要改的话，现在就没有地方改」。
    #   所以判定依据从「代码里拼」改成「可以被界面改的一整段文字」：
    #     空字符串 = 用 fine_positioning.json 自动生成（老行为）
    #     非空     = 就用这段（改它等于改判定口径，热读，不用重启）
    "rules_text": "",
    # 店主对某次判定的点评（「这一点判错了」）。它会作为「已确认口径」喂给 AI，
    # 并且可以一键合并进 rules_text —— 这就是主人说的「一点点优化那个提示词」。
    "feedback": [],
}

SYSTEM = (
    "你是电商选品审核员，替店主决定一件商品能不能上架到微信小店。"
    "你只依据给你的证据说话，不许脑补证据里没有的东西。"
    "结论只有两档：pass（通过）或 block（刷掉）；只有商品本身证据互相冲突、实在判不了时"
    "才允许 warn，且必须把 confidence 压到 30 以下，交由店主看一眼。"
    "不许评估利润/竞争/行情——那些数据本包没有，不许因此判 warn 或要求人工核对。"
    "输出必须是一个 JSON 对象，不要解释、不要 Markdown 代码块。"
)


def cfg() -> dict:
    d = dict(_DEFAULT)
    try:
        with open(_CFG, "r", encoding="utf-8") as f:
            d.update(json.load(f) or {})
    except Exception:                             # noqa: BLE001
        pass
    return d


def available() -> dict:
    if A is None:
        return {"ok": False, "why": "ai_providers 导入失败"}
    c = cfg()
    if not c.get("enabled"):
        return {"ok": False, "why": "AI 精筛已在 config/ai_fine.json 里关掉"}
    st = A.status()["roles"].get(c.get("role") or "text") or {}
    if not st.get("enabled"):
        return {"ok": False, "why": "角色 %s 未启用" % (c.get("role") or "text")}
    return {"ok": True, "model": st.get("model"), "pool": len(A.pool_of(c.get("role") or "text"))}


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n]


def _shop_rules() -> str:
    """把「我们之前定的选品规则」原文取出来喂给 AI。

    ★ 2026-10-27 主人原话：「只有 AI 点评，它根据商品的详情，还有我们之前定的那些选品的规则，
      来看这个商品需不需要继续下去」。所以规则不能只留在代码里当判据，
      **必须整段给到 AI 看**，否则它只能凭常识判，跟店主的口径对不上。

    ★ 2026-10-28 改为优先用 criteria（唯一真源 → 生成提示词）：
      主人原话「商品精筛根本没有相应的条件，相当于提示词都没有，随随便便筛的…把这些因素
      都做成一个设置页放进去」。旧版只拼 6 行摘要（人群/价位/品类/风险），
      缺行情、缺打码、缺判定优先级 —— AI 收到的东西太少，只能自由发挥。
      现在：config/selection_criteria.json → criteria.render_rules() 生成完整规则（约 1800 字）。
      兜底仍保留 fine_positioning 摘要，避免条件文件坏掉时整步瘫痪。
    """
    try:
        try:
            from . import criteria as _cr                 # noqa: PLC0415
        except Exception:                                 # noqa: BLE001
            import criteria as _cr                        # noqa: PLC0415
        t = _cr.render_rules()
        if t and len(t) > 200:
            return t
    except Exception:                                     # noqa: BLE001
        pass

    try:
        from . import positioning as _pos          # noqa: PLC0415
        c = _pos.cfg() or {}
    except Exception:                              # noqa: BLE001
        try:
            import positioning as _pos             # noqa: PLC0415
            c = _pos.cfg() or {}
        except Exception:                          # noqa: BLE001
            return "（没读到选品规则配置文件，按常识判）"
    if not c:
        return "（选品规则配置为空，按常识判）"

    def _keys(d) -> list:
        """配置里 _说明/_备注 这类注释键不是词，必须滤掉（否则会被当成推荐词喂给 AI）。"""
        return [k for k in (d or {}) if not str(k).startswith("_")]

    out = []
    a = c.get("audience") or {}
    if _keys(a.get("positive")):
        out.append("目标人群偏好：%s" % "、".join(_keys(a["positive"])[:25]))
    if _keys(a.get("negative")):
        out.append("明确不要的人群/风格：%s" % "、".join(_keys(a["negative"])[:25]))
    p = c.get("price") or {}
    if p.get("sweet_spot"):
        out.append("甜点价位：%s–%s 元（可接受 %s–%s）"
                   % (p["sweet_spot"][0], p["sweet_spot"][1],
                      (p.get("acceptable") or ["?", "?"])[0],
                      (p.get("acceptable") or ["?", "?"])[1]))
    cat = c.get("category") or {}
    if _keys(cat.get("preferred")):
        out.append("优先做的品类：%s" % "、".join(_keys(cat["preferred"])[:12]))
    if _keys(cat.get("excluded")):
        out.append("不做的品类（有尺码/易碎/复购/母婴/数码小件）：%s"
                   % "、".join(_keys(cat["excluded"])[:12]))
    q = (c.get("quality_risk") or {}).get("words") or {}
    if _keys(q):
        out.append("质量风险关注词：%s" % "、".join(_keys(q)[:25]))
    v = c.get("verdict") or {}
    if v.get("min_score_pass"):
        out.append("规则口径里的及格线：%s 分" % v["min_score_pass"])
    return "\n".join(out) if out else "（规则里没有可读条目）"


def save_cfg(d: dict) -> None:
    """原子写回 config/ai_fine.json（写一半被读到会丢规则，所以先写 .tmp 再替换）。"""
    tmp = _CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CFG)


# ---------------------------------------------------------------- 判定提示词（可编辑）

def default_rules_text() -> str:
    """没人改过提示词时用的默认规则文本（由 fine_positioning.json 现拼）。"""
    return _shop_rules()


def rules_text() -> str:
    """当前真正喂给 AI 的那段规则。改过就用改过的，没改过就用自动生成的。"""
    t = str(cfg().get("rules_text") or "").strip()
    return t or default_rules_text()


def set_rules_text(text: str) -> dict:
    d = cfg()
    t = str(text or "").strip()
    d["rules_text"] = t
    d["_rules_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_cfg(d)
    return {"ok": True, "chars": len(t), "using_default": not t}


def reset_rules_text() -> dict:
    d = cfg()
    d["rules_text"] = ""
    d["_rules_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_cfg(d)
    return {"ok": True, "using_default": True, "text": default_rules_text()}


# ---------------------------------------------------------------- 店主点评（纠错口径）

def feedback_list() -> list:
    out = []
    for i, x in enumerate(cfg().get("feedback") or []):
        if isinstance(x, dict):
            out.append({"idx": i, **x})
    return out


def feedback_add(pid, title: str, comment: str, ai_verdict: str = "",
                 ai_comment: str = "", verdict_now: str = "") -> dict:
    """记一条「这条判定我不认同，原因是…」。不调模型、不花钱，随时可撤。"""
    c = str(comment or "").strip()
    if not c:
        return {"ok": False, "error": "点评内容不能为空"}
    d = cfg()
    fb = d.setdefault("feedback", [])
    if not isinstance(fb, list):
        fb = d["feedback"] = []
    fb.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": pid,
               "title": str(title or "")[:60], "comment": c[:600],
               "ai_verdict": ai_verdict or "", "ai_comment": str(ai_comment or "")[:300],
               "verdict_now": verdict_now or ""})
    del fb[:-200]                       # 只留最近 200 条，别把配置文件撑爆
    save_cfg(d)
    return {"ok": True, "count": len(fb)}


def feedback_del(idx: int) -> dict:
    d = cfg()
    fb = d.get("feedback") or []
    i = int(idx)
    if not (0 <= i < len(fb)):
        return {"ok": False, "error": "序号越界"}
    gone = fb.pop(i)
    save_cfg(d)
    return {"ok": True, "removed": gone.get("comment", "")[:60], "count": len(fb)}


def feedback_apply(keep: bool = False) -> dict:
    """把点评逐条合并进规则文本（不调模型，绝对可控）。

    合并方式：在规则末尾追加一节「店主逐条纠正过的口径」，
    每条带日期与商品号；重复的完全相同句子不重复写。
    """
    d = cfg()
    fb = [x for x in (d.get("feedback") or []) if isinstance(x, dict)]
    if not fb:
        return {"ok": False, "error": "还没有任何点评"}
    base = str(d.get("rules_text") or "").strip() or default_rules_text()
    head = "【店主逐条纠正过的口径（与上面的规则冲突时，以这几条为准）】"
    lines = [x.get("comment", "").strip() for x in fb if x.get("comment")]
    new = []
    for s in lines:
        if s and ("- %s" % s) not in base:
            new.append("- %s" % s)
    if not new:
        return {"ok": True, "added": 0, "text": base, "note": "这几条已经在规则里了"}
    text = base.rstrip() + "\n\n" + head + "\n" + "\n".join(new)
    d["rules_text"] = text
    d["_rules_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if not keep:
        d["feedback"] = []
    save_cfg(d)
    return {"ok": True, "added": len(new), "text": text}


def refine(timeout: int | None = None) -> dict:
    """让模型把多条点评**归纳**成几条规则（只返回建议，不自动改，交人确认）。

    为什么要人确认：模型归纳会改口径，直接落库等于让模型偷偷改判官的标准。
    """
    if A is None:
        return {"ok": False, "error": "ai_providers 导入失败"}
    fb = [x.get("comment", "").strip() for x in feedback_list() if x.get("comment")]
    if not fb:
        return {"ok": False, "error": "还没有任何点评可以提炼"}
    base = rules_text()
    prompt = """下面是本店现有的选品判定规则，以及店主对若干次判定的逐条纠正。
请把「纠正」归纳成**不超过 6 条**可直接执行的规则，合并进原规则，输出合并后的完整规则文本。
要求：
1. 保留原规则里没有被否定的内容；
2. 被纠正的说法直接改掉，不要保留两套互相矛盾的口径；
3. 只输出规则文本本身，不要解释、不要 Markdown 代码块、不要加标题层级。

【现有规则】
%s

【店主逐条纠正】
%s
""" % (base[:4000], "\n".join("- " + s for s in fb[:40]))
    t0 = time.time()
    try:
        txt = A.chat("text", prompt, system="你是把店主口径整理成规则条文的助手，只输出规则文本。",
                     timeout=int(timeout or 120), max_tokens=1400)
    except Exception as e:                        # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    txt = re.sub(r"^```[a-z]*|```$", "", (txt or "").strip(), flags=re.M).strip()
    if not txt:
        return {"ok": False, "error": "模型返回空"}
    return {"ok": True, "text": txt, "secs": round(time.time() - t0, 1),
            "model": (A.last_call() or {}).get("used") or "", "from": len(fb)}


def build_prompt(p: dict, title: str, reasons: list[dict], img_hits: list[dict] | None,
                 texts: dict | None, price=None, gate_res: dict | None = None) -> str:
    """把库里真实存在的证据 + 本店选品规则拼成 prompt。没有的字段一律写「未采到」。"""
    desc = _clip(p.get("description") or p.get("desc") or "", 900)
    attrs = p.get("attributes") or p.get("attrs") or ""
    if isinstance(attrs, (dict, list)):
        attrs = json.dumps(attrs, ensure_ascii=False)
    attrs = _clip(attrs, 400)

    sig = []
    for r in (reasons or [])[:16]:
        lv = {"block": "拦", "pending": "缺证据", "warn": "疑",
              "signal": "参考"}.get(r.get("level"), "?")
        sig.append("- [%s] %s%s" % (lv, _clip(r.get("msg") or r.get("code"), 120),
                                    ("（词：%s）" % r.get("word")) if r.get("word") else ""))
    if not sig:
        sig.append("- 规则层没有命中任何词")

    ocr = []
    for h in (img_hits or [])[:10]:
        ocr.append("- %s 读到「%s」：…%s…" % (h.get("image") or "图",
                                             h.get("word") or "",
                                             _clip(h.get("snippet") or "", 50)))
    if not ocr and texts:
        for k, v in list(texts.items())[:5]:
            ocr.append("- %s 文字：%s" % (k, _clip(v, 60)))
    if not ocr:
        ocr.append("- 未采到图片文字（图片层没开或没读到字）")

    price_txt = ("%.0f 元" % float(price)) if isinstance(price, (int, float)) else "未采到"
    extra = _clip(cfg().get("extra"), 400)
    # 判定依据：可被界面改的那段（空则用 configuration 自动生成）
    rules = rules_text()
    # 店主点评：最近 30 条，作为「已确认口径」压在规则之上
    fbs = [str(x.get("comment") or "").strip() for x in feedback_list()]
    fbs = [s for s in fbs if s][-30:]
    fb_block = ("【店主当面纠正过的口径（优先级最高；与上面的规则冲突时，以这几条为准）】\n"
                + "\n".join("- " + s for s in fbs)) if fbs else ""

    # 本地闸门已经算出来的东西（换哪个模型都一致，AI 只需复核语义）
    g = gate_res or {}
    gtxt = []
    if g:
        if g.get("block"):
            gtxt.append("- 硬拦：" + "；".join(g["block"]))
        if g.get("warn"):
            gtxt.append("- 警告：" + "；".join(g["warn"][:6]))
        gtxt.append("- 本地综合分 %s（及格线 45）｜人群分 %s｜疑似需打码：%s %s"
                    % (g.get("score"), g.get("audience_score"),
                       "是" if g.get("need_mask") else "否",
                       ("（线索：" + "、".join(g.get("mask_why") or []) + "）")
                       if g.get("mask_why") else ""))
        gtxt.append("- ★ 硬拦项不得推翻；警告项你可以据证据降级或升级。")

    return """请按**本店选品规则**判定这件商品：要不要继续（进打码/出表），还是拦掉；
以及**要不要打码**、**现在值不值得上架**。

【本店选品规则（店主定的，必须遵守）】
%s

【商品】
标题：%s
店铺：%s
价格：%s
描述：%s
属性：%s

【规则层摆出的线索（只是线索，你可以推翻，但要说清为什么）】
%s

【图片 OCR 读到的字（机器识字，会认错，也可能只是「能装内衣」「玻璃灯罩」这类用途/材质词）】
%s

【已确认的机器事实（这些不要推翻）】
- 同款标题/主图重复：%s
%s
%s
%s

【判断要求】
1. 先按上面那套规则看：人群对不对、价位对不对、品类做不做、有没有质量风险、是不是品牌货。
2. 再逐条看线索，判断是真风险还是误判（OCR 噪声、用途词、行业通用词）。
3. 「刷单/拍单号」这类词要分清：是卖家在暗示刷单，还是详情页里的售后凭证模板。判不了就说判不了。
4. **打码**：只有图上/文案里出现**他人**的品牌名、店铺名、水印、电话、网址、二维码、促销贴才需要打码；
   本店自己的商品名不算。你只给建议，不改图。
5. **只判商品本身**：品类该不该做、人群对不对、有无品牌/质量风险、最低价是否达到约 200 元门槛。
   **不许评估利润、加价率、价差、竞争、销量、退货率、采购成本**——本包没有这些数据，
   一律不评、不猜、不据此判「判不了」、不要求人工核对；worth 一律打 50。
6. 结论两档：能留 → pass，不能留 → block。**不许因为「信息不足/行情不明」就给 warn**。
   只有商品本身关键事实互相矛盾、你实在想不出来时，才给 warn + confidence<30（拉店主看一眼）。
7. 只输出 JSON：
{"verdict":"pass|block|warn","comment":"一段人话点评，说清依据（引用你看到的具体字句）","mask":{"need":true/false,"why":"依据一句话"},"worth":0-100,"needs_detail":false,"confidence":0-100}
""" % (
        rules,
        _clip(title, 120) or "未采到",
        _clip(p.get("shop") or p.get("shop_name") or p.get("source_shop_name") or "", 40) or "未采到",
        price_txt,
        desc or "未采到",
        attrs or "未采到",
        "\n".join(sig),
        "\n".join(ocr),
        "是" if p.get("_dup") else "否",
        ("\n" + fb_block) if fb_block else "",
        ("\n【本地闸门已算出的结论（换模型也不变，供你复核）】\n" + "\n".join(gtxt)) if gtxt else "",
        ("\n补充要求：" + extra) if extra else "",
    )


def _parse(txt: str) -> dict:
    """从模型输出里抠出 JSON。模型爱套 ```json、爱在前后加话、也爱把 comment 截断
    或往里塞未转义的引号（2026-11-09 实测 223 件包有 4 件栽在这）。
    三级兜底：整段 JSON → raw_decode 取第一个对象 → 字段级正则抠 verdict。
    verdict 总在最前、格式固定，只要它在就能判两档，不许因解析失败挂人工。"""
    s = (txt or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    m = re.search(r"\{.*", s, re.S)          # 从第一个 { 到结尾（容忍结尾被截断）
    j = None
    cand = m.group(0) if m else ""
    if cand:
        try:
            j = json.loads(cand)
        except Exception:                       # noqa: BLE001
            try:
                j, _ = json.JSONDecoder().raw_decode(cand)
            except Exception:                   # noqa: BLE001
                j = None
    if not isinstance(j, dict):
        # 字段级兜底：verdict 是关键；comment 拿不全不影响判两档
        mv = re.search(r'"verdict"\s*:\s*"(pass|block|warn|通过|拦住|待复核)"', s, re.I)
        if not mv:
            raise ValueError("模型没返回 JSON：%s" % s[:120])
        mc = re.search(r'"comment"\s*:\s*"((?:[^"\\]|\\.)*)"', s, re.S)
        mf = re.search(r'"confidence"\s*:\s*(\d+)', s)
        j = {"verdict": mv.group(1),
             "comment": (mc.group(1) if mc else ""),
             "confidence": (mf.group(1) if mf else 0),
             "needs_detail": False, "worth": 50, "mask": {"need": False, "why": ""}}
    v = str(j.get("verdict") or "").lower().strip()
    if v in ("pass", "通过", "ok"):
        v = "pass"
    elif v in ("block", "拦住", "blocked", "reject"):
        v = "block"
    else:
        v = "warn"
    # 打码建议（模型可能给 bool，也可能给 {need,why}）
    mk = j.get("mask")
    if isinstance(mk, dict):
        mask = {"need": bool(mk.get("need")), "why": str(mk.get("why") or "").strip()[:160]}
    elif isinstance(mk, bool):
        mask = {"need": mk, "why": ""}
    elif isinstance(mk, str):
        mask = {"need": mk.strip() in ("1", "true", "True", "需要", "要"), "why": mk[:160]}
    else:
        mask = {"need": False, "why": ""}
    try:
        worth = int(round(float(j.get("worth"))))
        worth = max(0, min(100, worth))
    except Exception:                            # noqa: BLE001
        worth = None
    return {"verdict": v,
            "comment": str(j.get("comment") or j.get("reason") or "").strip()[:400],
            "needs_detail": bool(j.get("needs_detail")),
            "confidence": int(j.get("confidence") or 0),
            "mask": mask, "worth": worth}


def judge(p: dict, title: str, reasons: list[dict], img_hits=None, texts=None,
          price=None) -> dict:
    """调模型判一件。返回 {ok, verdict, comment, needs_detail, model, secs} 或 {ok:False, error}。

    ★ 2026-10-28：先跑本地闸门（不花钱）。
      · 闸门判「硬拦」（质量风险/排除品类/品牌货/规则层已判死）→ **不进模型**，直接返回 block，
        comment 写明依据。这样换任何 API、模型抽风都不会把死货放出去，也省 token。
      · 否则把闸门结论一起塞进 prompt，让模型复核语义（打码/行情/是否误判）。
    """
    # —— 本地闸门先行 ——
    g = {}
    try:
        try:
            from . import criteria as _cr        # noqa: PLC0415
        except Exception:                        # noqa: BLE001
            import criteria as _cr               # noqa: PLC0415
        ocr_txt = " ".join(str((h or {}).get("word") or "") for h in (img_hits or [])[:12])
        if not ocr_txt and texts:
            ocr_txt = " ".join(str(v)[:80] for v in list(texts.values())[:5])
        g = _cr.gate(
            title=title or "",
            price=price,
            desc=str(p.get("description") or p.get("desc") or ""),
            ocr_text=ocr_txt,
            category=str(p.get("category_path") or p.get("category") or ""),
            shop=str(p.get("shop") or p.get("shop_name") or p.get("source_shop_name") or ""),
            reasons=reasons or [],
            # ★ 2026-11-09：拼多多品牌判定改看 payload 里的「黑底品牌角标 / 官方品牌资质」，
            #   不再按店名后缀一刀切。非 pdd 包照旧。
            payload=p.get("original_payload"),
            source_platform=str(p.get("source_platform") or ""),
        )
    except Exception:                            # noqa: BLE001
        g = {}

    if g.get("verdict_hint") == "block":
        return {"ok": True, "verdict": "block",
                "comment": "本地闸门判拦（未花 token）：" + "；".join(g.get("block") or [])[:300],
                "needs_detail": False, "confidence": 95,
                "mask": {"need": bool(g.get("need_mask")), "why": "、".join(g.get("mask_why") or [])},
                "worth": None, "model": "(本地闸门)", "secs": 0.0,
                "prompt_chars": 0, "gate": g}

    if A is None:
        return {"ok": False, "error": "ai_providers 导入失败", "gate": g}
    av = available()
    if not av.get("ok"):
        return {"ok": False, "error": av.get("why") or "AI 不可用", "gate": g}
    c = cfg()
    t0 = time.time()
    prompt = build_prompt(p, title, reasons, img_hits, texts, price, gate_res=g)
    try:
        txt = A.chat(c.get("role") or "text", prompt, system=SYSTEM,
                     timeout=int(c.get("timeout") or 90),
                     max_tokens=int(c.get("max_tokens") or 700))
        j = _parse(txt)
    except Exception as e:                        # noqa: BLE001
        lc = A.last_call() if hasattr(A, "last_call") else {}
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                "tried": lc.get("tried") or [], "secs": round(time.time() - t0, 2),
                "prompt_chars": len(prompt), "gate": g}
    used = (A.last_call() or {}).get("used") or ""
    return {"ok": True, **j, "model": used, "secs": round(time.time() - t0, 2),
            "prompt_chars": len(prompt), "gate": g}
