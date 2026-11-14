# -*- coding: utf-8 -*-
"""v3 · 统一词条注册表（P1/P2 共用）

一个池子，按 kind 分类，**风险词永远不进推荐**：
    core       类目核心词（"户外帐篷"）
    attribute  属性词（"双人""自动开合"）—— 必须有属性证据才可用
    scene      场景词（"露营""野营""阳台"）
    synonym    同义词（"帐篷"≈"帐篷屋"）
    brand      品牌词 —— 只用于风险拦截，不用于推荐
    violation  违规/打码词 —— 只用于风险拦截，不用于推荐

数据来源（全部是**既有、已经过人工积累**的文件，不新造词）：
    J:\\aixitong\\小猪\\词库\\词库主表.tsv        20,861 行（词/类型/来源）
    J:\\aixitong\\小猪\\词库\\打码词.txt            打码词
    taobao-picker\\config\\category_rules.json     类目关键词 / 排除类目 / 易碎 / 安装
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any, Iterable

from . import store

try:                                   # ★ 2026-10-24 人工加词/清词（与第一步共用一份）
    from .. import brand_lexicon
except Exception:                      # noqa: BLE001
    try:
        import brand_lexicon           # v3 被当顶层包导入时（脚本直接跑）走这条
    except Exception:                  # noqa: BLE001
        brand_lexicon = None

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = os.path.dirname(SERVER_DIR)
CFG_DIR = os.path.join(BASE_DIR, "config")

PIG_WORDS = r"J:\aixitong\小猪\词库"
WORD_MAIN = os.path.join(PIG_WORDS, "词库主表.tsv")
MASK_WORDS = os.path.join(PIG_WORDS, "打码词.txt")
CATEGORY_RULES = os.path.join(CFG_DIR, "category_rules.json")

# 属性词白名单：这些词只有在原文/属性里能找到时才进候选（禁止编造）
ATTRIBUTE_LEXICON = [
    "单人", "双人", "三人", "四人", "多人", "加大", "加厚", "轻薄", "超轻", "便携",
    "折叠", "可折叠", "自动开合", "免安装", "免搭", "速开", "手开",
    "防雨", "防水", "防紫外线", "防晒", "透气", "防风", "保暖", "速干",
    "大帽檐", "遮脸", "一体式", "可拆卸", "可机洗", "可收纳",
    "升级款", "加宽", "加深", "高靠背", "护腰", "人体工学",
    "USB充电", "充电款", "可充电", "太阳能", "遥控", "声控", "感应",
    "不锈钢", "铝合金", "碳钢", "实木", "棉麻", "牛津布", "涤纶", "尼龙",
    "超纤皮", "PU皮", "真皮",
]

# 场景词：只用于组合，不改变商品事实
SCENE_LEXICON = {
    "户外": ["户外", "露营", "野营", "野外", "徒步", "登山", "自驾", "郊游", "过夜"],
    "家居": ["客厅", "卧室", "书房", "阳台", "玄关", "餐厅", "小户型", "出租屋", "宿舍"],
    "办公": ["办公室", "工位", "办公", "电脑桌", "书桌"],
    "运动": ["健身", "运动", "跑步", "骑行", "钓鱼"],
}

# 同义词组：只做等价替换，不扩义
SYNONYMS = {
    "帐篷": ["帐篷", "帐篷屋"],
    "椅子": ["椅子", "座椅", "凳"],
    "灯": ["灯", "灯具", "照明"],
    "野营": ["野营", "露营"],
}

# 广告法/极限词：标题里出现即判虚假描述
HYPE_WORDS = [
    "国家级", "世界级", "最高级", "最佳", "第一品牌", "绝无仅有", "独家",
    "百分百", "100%", "永久", "永不", "根治", "特效", "万能", "顶级",
    "史上最", "全网最", "销量第一", "官方唯一",
]


def _read_tsv(path: str) -> list[list[str]]:
    rows = []
    if not os.path.exists(path):
        return rows
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    line = line.rstrip("\n\r")
                    if not line or line.startswith("#"):
                        continue
                    rows.append(line.split("\t"))
            return rows
        except UnicodeDecodeError:
            rows = []
            continue
    return rows


def _read_lines(path: str) -> list[str]:
    out = []
    if not os.path.exists(path):
        return out
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        out.append(s)
            return out
        except UnicodeDecodeError:
            out = []
            continue
    return out


def load_category_rules() -> dict:
    if not os.path.exists(CATEGORY_RULES):
        return {}
    try:
        with open(CATEGORY_RULES, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------- 构建

def build(conn: sqlite3.Connection, actor: str = "keywords") -> dict:
    """从既有文件重建注册表。幂等：同一 (text, kind, scope) 只留一条。"""
    store.init(conn)
    stat = {"brand": 0, "violation": 0, "core": 0, "attribute": 0,
            "scene": 0, "synonym": 0, "source_files": []}
    rows: list[tuple] = []

    # ① 词库主表：类型 brand / violation / both
    main = _read_tsv(WORD_MAIN)
    if main:
        stat["source_files"].append(WORD_MAIN)
    for r in main:
        if len(r) < 2:
            continue
        w = r[0].strip()
        t = r[1].strip().lower()
        src = (r[2].strip() if len(r) > 2 else "")
        if not w:
            continue
        kinds = []
        if t in ("brand", "both"):
            kinds.append("brand")
        if t in ("violation", "both"):
            kinds.append("violation")
        for k in kinds:
            rows.append((w, k, "", "", "", src or "词库主表.tsv", None,
                         None, 1.0, "active", ""))

    # ② 打码词 → violation
    mw = _read_lines(MASK_WORDS)
    if mw:
        stat["source_files"].append(MASK_WORDS)
    for w in mw:
        if 1 <= len(w) <= 30:
            rows.append((w, "violation", "", "", "", "打码词.txt", None,
                         None, 1.0, "active", "mask"))

    # ③ 类目规则 → core / attribute / scene / violation
    cr = load_category_rules()
    if cr:
        stat["source_files"].append(CATEGORY_RULES)
    for cat, words in (cr.get("category_keywords") or {}).items():
        if cat.startswith("_"):
            continue
        for w in words or []:
            rows.append((str(w), "core", cat, "", "", "category_rules.json",
                         None, None, 1.0, "active", "类目:" + cat))
        # 类目名本身也是核心词
        rows.append((cat, "core", cat, "", "", "category_rules.json",
                     None, None, 1.0, "active", "类目名"))
    for w in cr.get("exclude_categories") or []:
        rows.append((str(w), "violation", "", "", "", "category_rules.json:排除类目",
                     None, None, 1.0, "active", "cross_category"))
    for w in cr.get("fragile_words") or []:
        rows.append((str(w), "attribute", "", "", "", "category_rules.json:易碎",
                     None, None, 0.8, "review", "fragile"))
    for w in cr.get("install_words") or []:
        rows.append((str(w), "attribute", "", "", "", "category_rules.json:需安装",
                     None, None, 0.8, "review", "install"))

    # ④ 属性 / 场景 / 同义（本地词典，reviews 状态，需人工确认后升 active）
    for w in ATTRIBUTE_LEXICON:
        rows.append((w, "attribute", "", "", "", "内置属性词典", None,
                     None, 0.9, "review", ""))
    for grp, words in SCENE_LEXICON.items():
        for w in words:
            rows.append((w, "scene", "", "", "", "内置场景词典:" + grp, None,
                         None, 0.9, "review", "场景:" + grp))
    for head, words in SYNONYMS.items():
        for w in words:
            rows.append((w, "synonym", "", "", "", "内置同义词:" + head, None,
                         None, 0.9, "review", "同义:" + head))

    # 写库
    ts = store.now()
    for row in rows:
        text, kind, scope = row[0], row[1], row[2]
        try:
            conn.execute(
                "INSERT INTO v3_keyword(text, kind, category_scope, platform_scope,"
                " requires_attribute, source, source_date, metrics, confidence, status,"
                " tags, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(text, kind, category_scope) DO UPDATE SET"
                " source=excluded.source, confidence=excluded.confidence,"
                " status=CASE WHEN v3_keyword.status='disabled' THEN 'disabled'"
                "             ELSE excluded.status END,"
                " tags=excluded.tags, updated_at=excluded.updated_at",
                (text, kind, scope, row[3], row[4], row[5], row[6], row[7],
                 row[8], row[9], row[10], ts))
            stat[kind] = stat.get(kind, 0) + 1
        except Exception:
            pass
    # 重建之后把「已豁免」的词重新置回 disabled —— 否则重建会把它们复活，
    # 人做的判定就白做了（这是豁免功能最容易漏的一环）。
    try:
        n = conn.execute("UPDATE v3_keyword SET status='disabled'"
                         " WHERE text IN (SELECT text FROM v3_keyword_waiver)").rowcount
        stat["reapplied_waivers"] = n
        stat["waiver_words"] = conn.execute(
            "SELECT COUNT(*) FROM v3_keyword_waiver").fetchone()[0]
    except Exception:
        pass
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM v3_keyword").fetchone()[0]
    stat["total_in_db"] = total
    stat["ok"] = True
    store.audit(conn, "keywords.build", "v3_keyword", "", None, stat, actor=actor)
    conn.commit()
    return stat


# ---------------------------------------------------------------- 查询

def _kinds_sql(kinds: Iterable[str]) -> tuple[str, list]:
    ks = [k for k in kinds]
    return ("(" + ",".join("?" * len(ks)) + ")", list(ks))


def terms(conn: sqlite3.Connection, kinds: Iterable[str], category: str = "",
          limit: int = 4000) -> list[dict]:
    """按 kind 取词。category 非空时优先该类目 + 通用（scope=''）。"""
    ks, args = _kinds_sql(kinds)
    sql = ("SELECT text, kind, category_scope, confidence, tags, status FROM v3_keyword"
           " WHERE kind IN %s AND status<>'disabled'" % ks)
    a = list(args)
    if category:
        sql += " AND (category_scope='' OR ? LIKE '%'||category_scope||'%'" \
               " OR category_scope LIKE '%'||?||'%')"
        a += [category, category]
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, confidence DESC LIMIT ?"
    a.append(int(limit))
    return [dict(r) for r in conn.execute(sql, a)]


def risk_hits(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[dict]:
    """文本里命中的品牌词/违规词（去重，长词优先）。

    两级判定，避免 2-3 字符的英文缩写误命中（如 "IK" 命中 "Nike"）：
      · 中文词 / 长英文词：子串命中即可
      · 短纯 ASCII 词（<=3 字符）：必须整词边界命中
    """
    if not text:
        return []
    sql = ("SELECT text, kind, source FROM v3_keyword"
           " WHERE kind IN ('brand','violation') AND status<>'disabled'"
           " AND length(text)>=2 AND instr(upper(?), upper(text))>0"
           " ORDER BY length(text) DESC LIMIT ?")
    up = text.upper()
    out, seen = [], set()

    # ★ 2026-10-24：人工裁决层。清掉的词（如「徒步」）在这里就不返回，
    #   加进来的词（如「石福康」）即使原始词表没有也照样算品牌命中。
    _rm, _add = set(), []
    if brand_lexicon is not None:
        try:
            _rm = brand_lexicon.removed()
            _add = brand_lexicon.added()
        except Exception:              # noqa: BLE001
            _rm, _add = set(), []

    for w in _add:
        if len(w) < 2 or w in seen:
            continue
        if w.upper() in up or w in text:
            seen.add(w)
            out.append({"text": w, "kind": "brand", "source": "人工添加"})

    for r in conn.execute(sql, (text, int(limit) * 3)):
        w = r["text"]
        if w in seen:
            continue
        if r["kind"] == "brand" and w in _rm:
            continue                   # 人工清掉的词 → 不算品牌命中
        if w.isascii() and len(w) <= 3:
            if not re.search(r"(?<![A-Z0-9])%s(?![A-Z0-9])" % re.escape(w.upper()), up):
                continue
        elif w.isascii() and len(w) <= 6:
            # 中等长度英文词也要求不是更长单词的一部分
            if not re.search(r"(?<![A-Z0-9])%s(?![A-Z0-9])" % re.escape(w.upper()), up):
                continue
        seen.add(w)
        out.append(dict(r))
        if len(out) >= int(limit):
            break
    return out


# ---------------------------------------------------------------- 词库豁免（人判定噪声）

def waive(conn: sqlite3.Connection, words: Iterable[str], reason: str = "",
          actor: str = "人", hits: int = 0) -> dict:
    """把词判为「不是违规词」：置 disabled + 记台账，以后不再命中。

    **不删原始词典文件**（词库主表.tsv / 打码词.txt 是小猪也在用的共享文件）。
    生效点：risk_hits() 只查 status<>'disabled'，所以置完立刻不再命中。
    可逆：prev_status 留了原状态，unwaive() 一键还原。
    """
    store.init(conn)
    done, missing = [], []
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        rows = [dict(r) for r in conn.execute(
            "SELECT keyword_id, kind, status FROM v3_keyword WHERE text=?", (w,))]
        if not rows:
            missing.append(w)
            continue
        prev = {r["kind"]: r["status"] for r in rows}
        conn.execute("UPDATE v3_keyword SET status='disabled', updated_at=? WHERE text=?",
                     (store.now(), w))
        conn.execute(
            "INSERT INTO v3_keyword_waiver(text, kinds, prev_status, reason, hits,"
            " created_by, created_at) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(text) DO UPDATE SET kinds=excluded.kinds,"
            " prev_status=excluded.prev_status, reason=excluded.reason,"
            " hits=excluded.hits, created_by=excluded.created_by,"
            " created_at=excluded.created_at",
            (w, store.j(sorted(prev)), store.j(prev), reason or "", int(hits or 0),
             actor, store.now()))
        done.append(w)
    conn.commit()
    store.audit(conn, "keywords.waive", "v3_keyword", ",".join(done)[:200], None,
                {"waived": done, "not_in_dict": missing, "reason": reason}, actor=actor)
    conn.commit()
    return {"ok": True, "waived": done, "not_in_dict": missing,
            "note": "" if done else "这些词不在词库里（可能拼错了，或者它来自类目规则而不是词库）"}


def unwaive(conn: sqlite3.Connection, text: str, actor: str = "人") -> dict:
    """撤销豁免：把状态还原成豁免前的样子，并删掉台账那一行。"""
    store.init(conn)
    text = (text or "").strip()
    row = conn.execute("SELECT * FROM v3_keyword_waiver WHERE text=?", (text,)).fetchone()
    if not row:
        return {"ok": False, "error": "「%s」不在豁免台账里" % text}
    prev: dict = {}
    try:
        prev = json.loads(row["prev_status"] or "{}")
    except Exception:
        prev = {}
    for kind, st in prev.items():
        conn.execute("UPDATE v3_keyword SET status=?, updated_at=? WHERE text=? AND kind=?",
                     (st or "active", store.now(), text, kind))
    # 兜底：台账里没记到的行（例如后来新插入的同名词）也放开
    conn.execute("UPDATE v3_keyword SET status='active', updated_at=?"
                 " WHERE text=? AND status='disabled'", (store.now(), text))
    conn.execute("DELETE FROM v3_keyword_waiver WHERE text=?", (text,))
    conn.commit()
    store.audit(conn, "keywords.unwaive", "v3_keyword", text, None, {"restored": prev},
                actor=actor)
    conn.commit()
    return {"ok": True, "text": text, "restored": prev}


def waivers(conn: sqlite3.Connection) -> list[dict]:
    store.init(conn)
    return [dict(r) for r in conn.execute(
        "SELECT text, kinds, reason, hits, created_by, created_at"
        " FROM v3_keyword_waiver ORDER BY created_at DESC")]


def stats(conn: sqlite3.Connection) -> dict:
    d = {}
    for r in conn.execute("SELECT kind, status, COUNT(*) n FROM v3_keyword GROUP BY 1,2"):
        d.setdefault(r["kind"], {})[r["status"]] = r["n"]
    d["total"] = conn.execute("SELECT COUNT(*) FROM v3_keyword").fetchone()[0]
    return d


def safe_recommendable(conn: sqlite3.Connection, words: Iterable[str]) -> list[str]:
    """过滤掉任何风险词（品牌/违规）。推荐用词必须过这一道。"""
    risk = set()
    for r in conn.execute("SELECT text FROM v3_keyword WHERE kind IN ('brand','violation')"
                          " AND status<>'disabled'"):
        risk.add(r["text"].upper())
    out = []
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        if any((rw in w.upper()) or (w.upper() in rw) for rw in risk if len(rw) >= 2):
            continue
        out.append(w)
    return out
