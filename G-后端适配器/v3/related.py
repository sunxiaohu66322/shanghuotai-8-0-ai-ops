# -*- coding: utf-8 -*-
"""v3 · 关联词与标题改写（2026-10-26）

★ 为什么有这个模块：
  `titles._compose()` 只会把 core / attrs / scenes **换序 + 截取**，
  从来没有"换词"这一步。用户的原话是「改上一两个词语，或者换一下顺序」——
  前半句（换词）压根不存在。

  而改词如果没有证据，就是把卖家的话换个说法瞎编。
  所以本模块的顺序是：**先采证据，再改词；没证据的不改。**

三层能力：
  ① harvest()         淘宝下拉采集 → v3_keyword(kind='related')
  ② identify_core()   中心词识别（词典最大匹配 + 首次出现位置）
  ③ rewrite()         改词（A 同义替换 / B 换序 / C 增补）+ 五道验收

★ 三条铁律（踩过坑，不要放宽）：
  1. **材质 / 尺寸 / 功能词绝不允许凭空增补** —— 那是虚假宣传，不是改写。
     只有 场景 / 风格 / 时间(新款) 三类允许增补。
  2. **每个新增的「修饰+中心词」组合必须有搜索证据**；没有就明说"无搜索证据"，
     不许当成"编造"（实测：橡胶木茶桌 n=0 但橡胶木茶几 n=4，前者是换个搭法，后者才叫编）。
  3. **中心词是"族"不是"唯一词"** —— 茶桌/茶台/泡茶桌/茶几 同族，改词不许把中心词改跑。

用法：
    from server.v3 import related
    related.harvest(conn, ["茶桌", "茶几"])
    related.identify_core("移动茶桌小型家用功夫茶几茶车")
    related.rewrite(conn, "移动茶桌小型家用功夫茶几茶车", core="茶桌")
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Any, Iterable

# ---------------------------------------------------------------- 网络

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_SUG = "https://suggest.taobao.com/sug?code=utf-8&q="

# 实测：30 次无间隔 13.7 次/秒 无风控；生产仍压到 2-3 次/秒，别贪
_RATE = 0.35


def _sug_once(conn: sqlite3.Connection | None, q: str, timeout: float = 8.0,
              retry: int = 2) -> list[str]:
    """单次下拉查询。失败返回空列表，绝不抛给调用方。"""
    for k in range(retry + 1):
        try:
            url = _SUG + urllib.parse.quote(q)
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
            j = json.loads(raw)
            return [str(x[0]).strip() for x in (j.get("result") or [])
                    if x and x[0]]
        except Exception:
            if k >= retry:
                return []
            time.sleep(0.8 * (k + 1))
    return []


# ---------------------------------------------------------------- 词性判定

# 配件 / 邻品 / 非商品意图 —— 命中即 role=part，不参与改词
# 实测：8 种子 419 条里 28.6% 是这些. 不过滤的话标题会被改成卖桌布的.
_PART_TOKENS = (
    "桌布", "桌垫", "桌旗", "桌腿", "桌面贴", "摆件", "收纳盒", "盒子", "支架",
    "底座", "灯罩", "灯架", "椅子", "凳子", "长凳", "茶盘", "托盘", "垫子",
    "套罩", "贴纸", "配件", "零件", "清洁", "保养", "补漆",
    "价格", "多少钱", "图片", "拼音", "读音", "英文", "翻译", "怎么", "哪个好",
    "视频", "直播", "店铺", "厂家", "批发", "定制", "安装", "维修",
)

# 修饰词词典 —— 用来把采集池里的「rest」（多词连写，如"客厅家用现代简约"）
# 切成单个修饰词。只收**能安全改写**的词；材质/尺寸/功能也收，但只用于"识别"不用于"增补"。
# 依据：从 20 次真实采集的 187 条长尾里归纳。
_MOD_LEXICON = tuple(sorted({
    # 场景
    "客厅", "卧室", "阳台", "办公室", "书房", "茶室", "餐厅", "美容院", "酒店",
    "民宿", "飘窗", "玄关", "沙发旁", "沙发旁边", "电视柜旁边", "靠墙",
    # 风格
    "现代简约", "新中式", "北欧", "日式", "中古", "法式", "美式", "奶油风",
    "轻奢", "高级感", "高端大气", "极简", "复古", "侘寂", "宋氏美学", "宋式美学",
    "ins风",
    # 时间
    "2026新款", "2025新款", "新款", "爆款",
    # 尺寸/形态
    "小户型", "小型", "加长", "窄柜", "大容量", "多功能", "创意",
    # 功能
    "可移动", "带轮", "升降", "折叠", "旋转", "可调节", "储物", "收纳",
    "泡茶一体", "烧水壶一体", "置物架一体", "床头柜一体", "茶桌一体",
    "餐桌一体", "餐一体", "茶具一整套", "功夫茶几",
    # 材质（只识别不增补）
    "实木", "原木", "黑胡桃木", "胡桃木", "岩板", "藤编", "楠竹", "金属",
    "整板", "大板", "松木", "橡胶木",
    # 其他
    "高颜值", "超亮", "免安装", "静音轮", "万向轮", "前置开口",
}, key=len, reverse=True))

# 允许"凭空增补"的三类修饰（不含材质/尺寸/功能 —— 那是事实，不能编）
_ADDABLE = {
    "scene": ("客厅", "卧室", "阳台", "办公室", "书房", "茶室", "餐厅", "户外",
              "庭院", "酒店", "民宿", "飘窗", "玄关", "沙发旁", "沙发旁边"),
    "style": ("现代简约", "新中式", "北欧", "日式", "中古", "法式", "美式",
              "奶油风", "轻奢", "高级感", "高端大气", "极简", "复古", "侘寂",
              "宋氏美学", "宋代美学", "ins风"),
    "time": ("2026新款", "2025新款", "新款", "爆款", "当季"),
}


def _seg(rest: str) -> list[str]:
    """把「客厅家用现代简约」这类连写 rest 切成单个修饰词（最大匹配）。"""
    out: list[str] = []
    i, n = 0, len(rest)
    while i < n:
        for w in _MOD_LEXICON:
            if rest.startswith(w, i):
                out.append(w)
                i += len(w)
                break
        else:
            i += 1          # 词典外字符跳过（数字/字母/杂字）
    # 去重保序
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w); uniq.append(w)
    return uniq

# 同义替换：左=原标题可能出现的写法，右=有搜索证据的标准说法
# 依据：suggest 里 "可移动/原木/整板/黑胡桃木/可旋转" 频次远高于 "移动/实木/整料/胡桃木/旋转"
_SYNONYM = (
    ("整料", "整板"),
    ("移动", "可移动"),
    ("实木", "原木"),
    ("旋转", "可旋转"),
    ("折叠", "可折叠"),
    ("升降", "可升降"),
    ("胡桃木", "黑胡桃木"),
    ("收纳", "储物"),
)

# 材质/尺寸/功能 —— 这些只做**证据核验**，绝不做增补
_FACTUAL = ("实木", "原木", "岩板", "楠竹", "藤", "金属", "碳纤维", "铝框",
            "松木", "橡胶木", "黑胡桃木", "胡桃木", "整板", "大板", "板材",
            "60cm", "1.2米", "1.5米", "寸", "cm", "米",
            "可移动", "带轮", "升降", "折叠", "旋转", "静音轮", "万向轮",
            "前置开口", "免安装", "烧水壶一体", "泡茶一体", "置物架一体")


def _role(word: str) -> str:
    """词的角色：part=配件邻品噪声 / modifier=可用修饰。"""
    for t in _PART_TOKENS:
        if t in word:
            return "part"
    return "modifier"


def _is_addable(mod: str) -> bool:
    """这个词能不能凭空增补（只有场景/风格/时间可以）。"""
    for grp in _ADDABLE.values():
        for w in grp:
            if mod == w or w in mod:
                return True
    return False


# ---------------------------------------------------------------- 采集入库

def harvest(conn: sqlite3.Connection, cores: Iterable[str],
            prefixes: Iterable[str] = ("", "客厅", "小户型", "实木"),
            rate: float = _RATE, actor: str = "related") -> dict:
    """对每个中心词采集淘宝下拉，写入 v3_keyword(kind='related')。

    幂等：唯一索引 (text, kind, category_scope) 保证同词同中心只留一条。
    只 upsert 不 DELETE —— 新采的先落 status='review'，人工确认后才升 active。
    """
    core_list = [c.strip() for c in cores if c and c.strip()]
    stat = {"queries": 0, "raw": 0, "uniq": 0, "modifier": 0, "part": 0,
            "inserted": 0, "updated": 0, "failed_queries": [], "cores": core_list}
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    for core in core_list:
        seen: dict[str, list[str]] = {}      # phrase -> 来源 query 列表
        for pfx in prefixes:
            q = (core + pfx) if pfx else core
            ws = _sug_once(conn, q)
            stat["queries"] += 1
            if not ws:
                stat["failed_queries"].append(q)
            for w in ws:
                if len(w) < 3 or len(w) > 40:
                    continue
                if core not in w:
                    continue                  # 必须含中心词才与本中心词相关
                seen.setdefault(w, []).append(q)
                stat["raw"] += 1
            time.sleep(rate)

        for phrase, from_q in seen.items():
            # 修饰部分 = 剥掉中心词后的残串（只用于判 role 和写 metrics）
            rest = phrase.replace(core, "", 1).strip()
            role = _role(phrase) if _role(phrase) == "part" else _role(rest or phrase)
            stat["part" if role == "part" else "modifier"] += 1
            metrics = {
                "from_queries": sorted(set(from_q)),
                "hits": len(from_q),
                "role": role,
                "core": core,
                "rest": rest,
            }
            existed = conn.execute(
                "SELECT keyword_id FROM v3_keyword"
                " WHERE text=? AND kind='related' AND category_scope=?",
                (phrase, core)).fetchone()
            if existed:
                conn.execute(
                    "UPDATE v3_keyword SET metrics=?, source_date=?, updated_at=?,"
                    " confidence=? WHERE keyword_id=?",
                    (json.dumps(metrics, ensure_ascii=False), now[:10], now,
                     1.0, existed[0]))
                stat["updated"] += 1
            else:
                conn.execute(
                    "INSERT INTO v3_keyword(text, kind, category_scope, platform_scope,"
                    " source, source_date, metrics, confidence, status, tags, updated_at)"
                    " VALUES(?,'related',?,?,?,?,?,?, 'review', ?, ?)",
                    (phrase, core, "taobao", "taobao_suggest", now[:10],
                     json.dumps(metrics, ensure_ascii=False), 1.0,
                     "role:" + role, now))
                stat["inserted"] += 1
        conn.commit()
        stat["uniq"] += len(seen)

    conn.commit()
    return stat


# ---------------------------------------------------------------- 中心词识别

def _core_index(conn: sqlite3.Connection) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """返回 (词→所属族集合, 族→词集合)。族 = category_scope。

    ★ 2026-10-26 补丁：**同时并入 kind='related' 的中心词**。
      原因：`v3_keyword(kind='core')` 只有 7 族 38 词，连「边几」「边桌」「角几」
      这些实际在卖的都缺 —— 于是「北美黑胡桃木边几客厅沙发旁可移动」识别不出中心词，
      明明关联词已经采过了也用不上。
      并入 related 的 category_scope 是**只读不写**的，不会影响精筛的品类判定
      （精筛走 `positioning._layer_category`，用的是 core 词表 + 类目规则，跟这里无关）。
    """
    word_fam: dict[str, set[str]] = {}
    fam_word: dict[str, set[str]] = {}

    def _add(w: str, s: str) -> None:
        w = (w or "").strip()
        if not w:
            return
        word_fam.setdefault(w, set()).add(s)
        fam_word.setdefault(s, set()).add(w)

    for text, scope in conn.execute(
            "SELECT text, category_scope FROM v3_keyword"
            " WHERE kind='core' AND status='active' AND text<>''"):
        _add(text, scope or "")

    # 关联词采集过的中心词，本身也是可信的中心词
    for (scope,) in conn.execute(
            "SELECT DISTINCT category_scope FROM v3_keyword"
            " WHERE kind='related' AND category_scope<>''"):
        _add(scope, scope)

    return word_fam, fam_word


def identify_core(title: str, word_fam: dict[str, set[str]],
                  fam_word: dict[str, set[str]]) -> dict:
    """M4：词典最大匹配 + **首次出现位置最靠前**。

    ★ 为什么不用"尾部词"：中文电商标题是**词堆不是短语**，
      主词通常在前面，后面堆邻品蹭流量。
      实测 1531 尾部是"餐边柜"但真货是茶桌；1513 尾部是"可推"。
    """
    t = title or ""
    hits: list[tuple[int, int, str]] = []
    for w in word_fam:
        p = t.find(w)
        if p >= 0:
            hits.append((p, -len(w), w))
    if not hits:
        return {"core": "", "siblings": [], "all_hits": [], "method": "none"}
    hits.sort()
    core = hits[0][2]
    sibs: set[str] = set()
    for s in word_fam.get(core, set()):
        sibs |= fam_word.get(s, set())
    return {
        "core": core,
        "siblings": sorted(sibs - {core}),
        "all_hits": [{"pos": p, "word": w} for p, _, w in hits],
        "method": "max_match_first_pos",
    }


# ---------------------------------------------------------------- 证据检索

def related_pool(conn: sqlite3.Connection, core: str,
                 only_active: bool = False) -> list[dict]:
    """取某中心词的关联词池（含 review —— review 是"刚采到、待确认"，不是"不可用"）。"""
    sql = ("SELECT text, status, metrics FROM v3_keyword"
           " WHERE kind='related' AND category_scope=?")
    if only_active:
        sql += " AND status='active'"
    out: list[dict] = []
    for text, status, mj in conn.execute(sql, (core,)):
        try:
            m = json.loads(mj) if mj else {}
        except Exception:
            m = {}
        if m.get("role") == "part":
            continue
        out.append({"text": text, "status": status,
                    "rest": m.get("rest", ""), "hits": int(m.get("hits") or 1)})
    out.sort(key=lambda x: (-x["hits"], -len(x["rest"])))
    return out


def has_evidence(pool: list[dict], mod: str, core: str) -> tuple[bool, str]:
    """某个「修饰+中心词」组合是否有搜索证据。

    ★ 顺序陷阱（踩过）：采集池里的短语是 **「中心词+修饰」**（`茶桌客厅家用`），
      而直觉拼的是 `修饰+中心词`（`客厅茶桌`）—— 只查一种顺序会永远 L3，
      把 187 条真证据全判成"无证据"。必须两种顺序都查。

    三级口径：
      L1 短语级：池里有完整 "修饰+中心词" 或 "中心词+修饰"  → 最强
      L2 词组级：池里有同时含这两个片段的长尾 / rest 里含该修饰 → 中等
      L3 无证据：返回 False，调用方必须报"无搜索证据"，**不许当编造**
    """
    if not mod:
        return False, "L3"
    for p in pool:
        if (mod + core) in p["text"] or (core + mod) in p["text"]:
            return True, "L1"
    for p in pool:
        if mod in p["text"] and core in p["text"]:
            return True, "L2"
    for p in pool:
        if mod in (p.get("rest") or ""):
            return True, "L2"
    return False, "L3"


# ---------------------------------------------------------------- 改写

def _split_mods(text: str, core: str, pool: list[dict]) -> list[str]:
    """原标题里**已经含有的、且有搜索证据的**修饰词。

    用途：告诉用户"这条标题里哪些写法是有人搜的"，剩下的就是可以换掉的。
    """
    found: list[str] = []
    for p in pool:
        for w in _seg(p.get("rest") or ""):
            if w in text and w not in found:
                found.append(w)
    found.sort(key=len, reverse=True)
    return found


def suggest_swaps(title: str, core: str, pool: list[dict]) -> list[dict]:
    """找出"原标题里没人搜的写法 → 有搜索证据的说法"的对照。

    ★ 这是改词真正的价值点（子智能体实测）：原标题本身常含没人搜的词
      （带轮/整料/烧水/橡胶木茶桌），换成有人搜的（可移动/整板/烧水壶一体/原木）。
    """
    t = title or ""
    in_title = _split_mods(t, core, pool)
    pool_mods: list[str] = []
    for p in pool:
        for w in _seg(p.get("rest") or ""):
            if w not in pool_mods:
                pool_mods.append(w)
    # 池里高频、标题里没有的
    missing = [w for w in pool_mods if w not in t]
    return [{"has_evidence": in_title, "not_in_title": missing}]


def rewrite(conn: sqlite3.Connection, title: str, core: str = "",
            max_out: int = 4, min_jac: float = 0.70,
            max_jac: float = 0.98) -> dict:
    """对一条真实标题做改词。返回候选 + 逐条证据。

    ★ 只做"有证据"的改动；没有证据的方案直接不给，并说明原因。
    """
    t = (title or "").strip()
    if not t:
        return {"ok": False, "error": "空标题"}

    word_fam, fam_word = _core_index(conn)
    ident = identify_core(t, word_fam, fam_word)
    c = core or ident["core"]
    if not c:
        return {"ok": False, "error": "识别不出中心词，无法改词（先补品类词典）",
                "identify": ident}

    pool = related_pool(conn, c)
    if not pool:
        return {"ok": False, "retryable": True,
                "error": "中心词「%s」还没有采集关联词（先跑 harvest）" % c,
                "identify": ident}

    have_mods = _split_mods(t, c, pool)
    out: list[dict] = []
    notes: list[str] = []

    # ---------- A 同义替换
    for old, new in _SYNONYM:
        if old not in t or new in t:
            continue
        cand = t.replace(old, new)
        ok, lvl = has_evidence(pool, new, c)
        out.append({
            "kind": "A_synonym", "text": cand,
            "change": "%s → %s" % (old, new),
            "evidence": lvl, "evidence_ok": ok,
            "core_kept": c in cand,
        })

    # ---------- B 换序：场景词前置（中心词位置不动）
    scene_in_title = [w for w in have_mods
                      if any(w == x or x in w for x in _ADDABLE["scene"])]
    for sc in scene_in_title[:2]:
        if t.startswith(sc):
            continue
        cand = sc + t.replace(sc, "", 1)
        if cand != t and c in cand:
            out.append({
                "kind": "B_reorder", "text": cand,
                "change": "场景词「%s」前置（原位置不变中心词）" % sc,
                "evidence": "L1", "evidence_ok": True,
                "core_kept": c in cand,
            })

    # ---------- C 增补（只允许 场景/风格/时间 三类）
    # ★ 原标题已经很长（>44 字）时**不增补** —— 电商标题 50 字已经很挤，
    #   再往里塞只会更堆砌；这种标题该做的是 D 删冗余，不是加词。
    too_long_already = len(t) > 44
    addable: list[str] = []
    if not too_long_already:
        for p in pool:
            for w in _seg(p.get("rest") or ""):
                if len(w) < 2 or w in t or w in addable:
                    continue
                if not _is_addable(w):
                    continue
                ok, _lvl = has_evidence(pool, w, c)
                if not ok:
                    continue
                addable.append(w)
    for w in addable[:8]:
        # 插在**中心词之后**（电商标题惯例：时效/风格词常跟在主词后，
        # 如采集到的真实长尾「茶桌2026新款高端」）。
        # 插在中心词之前会切断原文已有的修饰词串（"移动|2026新款|茶桌"读起来是断的）。
        pos = t.find(c)
        pos = (pos + len(c)) if pos >= 0 else 0
        cand = t[:pos] + w + t[pos:]
        ok, lvl = has_evidence(pool, w, c)
        out.append({
            "kind": "C_add", "text": cand,
            "change": "增补「%s」（场景/风格/时间，非事实属性）" % w,
            "evidence": lvl, "evidence_ok": ok,
            "core_kept": c in cand,
        })

    # ---------- D 删冗余：标题里堆的、**没有人搜**的风格词
    # ★ 只删风格类（现代简约/轻奢/高级感这种堆砌），**绝不删材质/尺寸/功能** ——
    #   后者是事实，删掉等于漏了卖点，还可能变成虚假描述。
    style_segs = [w for w in _seg(t)
                  if any(w == x or x in w for x in _ADDABLE["style"])]
    for w in style_segs:
        if w in have_mods:
            continue                    # 有搜索证据的风格词，一个都不删
        cand = t.replace(w, "", 1)
        if cand == t or len(cand) < 8:
            continue
        out.append({
            "kind": "D_trim", "text": cand,
            "change": "删掉堆砌且无人搜索的风格词「%s」" % w,
            "evidence": "L3(该词无搜索量)", "evidence_ok": True,
            "core_kept": c in cand,
        })

    # ---------- 五道验收
    # ★ 同义替换的产物不算"凭空新增事实"：把「移动」换成「可移动」只是换说法，
    #   不是无中生有（曾把 A 类全判成"凭空新增事实属性「可移动」"，一条都出不来）。
    _syn_out = {n for _, n in _SYNONYM}
    accepted: list[dict] = []
    for cand in out:
        txt = cand["text"]
        problems: list[str] = []
        # ① 中心词仍在
        if c not in txt:
            problems.append("中心词丢失")
        # ② 不许新增原文没有的事实属性
        for f in _FACTUAL:
            if f in _syn_out:
                continue
            if f in txt and f not in t:
                problems.append("凭空新增事实属性「%s」" % f)
                break
        # ③ 长度
        if not (8 <= len(txt) <= 60):
            problems.append("长度 %d 越界" % len(txt))
        # ④ 证据
        if not cand["evidence_ok"]:
            problems.append("无搜索证据")
        # ⑤ 相似度（不能太像=没改，也不能太不像=换了个商品）
        jac = _jac(t, txt)
        if not (min_jac <= jac <= max_jac):
            problems.append("相似度 %.2f 越界" % jac)
        cand["jaccard"] = round(jac, 3)
        cand["problems"] = problems
        if not problems:
            accepted.append(cand)

    # 去重 + 排序（改动小的优先，用户要的是"改一两个词"）
    seen: set[str] = set()
    uniq: list[dict] = []
    for a in sorted(accepted, key=lambda x: -x["jaccard"]):
        if a["text"] in seen:
            continue
        seen.add(a["text"])
        uniq.append(a)

    if not uniq:
        notes.append("没有找到既符合内容真实、又有搜索证据的改法")
        if have_mods:
            notes.append("原标题已含这些有搜索证据的修饰：%s" % "、".join(have_mods[:8]))
        notes.append("不会为了改而改 —— 没证据的方案不输出")

    return {
        "ok": True,
        "origin": t,
        "core": c,
        "siblings": ident["siblings"],
        "pool_size": len(pool),
        "already_good": have_mods[:10],
        "candidates": uniq[:max_out],
        "total_tried": len(out),
        "notes": notes,
    }


def _jac(a: str, b: str) -> float:
    sa = set(re.findall(r"[\u4e00-\u9fff]", a or ""))
    sb = set(re.findall(r"[\u4e00-\u9fff]", b or ""))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def stats(conn: sqlite3.Connection) -> dict:
    """关联词库存现状。"""
    rows = list(conn.execute(
        "SELECT kind, status, COUNT(*) FROM v3_keyword GROUP BY kind, status"))
    by_kind: dict[str, dict[str, int]] = {}
    for k, s, n in rows:
        by_kind.setdefault(k, {})[s] = n
    rel = conn.execute(
        "SELECT COUNT(DISTINCT category_scope) FROM v3_keyword WHERE kind='related'"
    ).fetchone()[0]
    return {"by_kind": by_kind, "related_cores": rel}
