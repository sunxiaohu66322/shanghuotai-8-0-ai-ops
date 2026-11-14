# -*- coding: utf-8 -*-
"""8.0 · 数据包统一命名（2026-11-13 主人指令）

痛点：一堆商品卡导入后，各页下拉里全是「批量导入_223件_11-9 拼多多_27件_20261109_1437」
这类长名，筛选前 / 筛选后看起来一模一样，1000 件一批时根本挑不出来。

口径（**只写 v3_pack.display_name 展示层，pack_code 主键与十几处外键一律不动**）：
    导入一批       → 11-13 第1批 · 筛选前
    该批精筛收口   → 11-13 第1批 · 筛选后      （继承同一批次号）
    用户 2.0 改名  → 直接改这个名字，所有页下次刷新自动跟随
    历史包         → 没有 display_name 就如实显示原 pack_code，不编造批次

「最新」判据：同批次「筛选后」优先于「筛选前」；跨批次按真实时间（created_at /
converted_at）倒序，时间缺失再退回 pack_code 里的日期串。绝不靠文件名相似度。
"""
from __future__ import annotations

import re
from datetime import datetime

# 友好名：MM-DD 第N批 · 阶段
RE_LABEL = re.compile(r"^(\d{2}-\d{2})\s*第(\d+)批\s*[·∙・]\s*(.+)$")
# 包码里可能带的 8 位日期（历史码 / 新码都覆盖）
RE_CODE_DATE = re.compile(r"(20\d{6})")

STAGE_RAW = "筛选前"
STAGE_FINE = "筛选后"


def now_md() -> str:
    """今天显示用日期，例如 11-13。"""
    return datetime.now().strftime("%m-%d")


def parse_label(name: str) -> dict:
    """把友好名拆成 {md, batch, stage}；不是友好名返回空 dict。"""
    m = RE_LABEL.match((name or "").strip())
    if not m:
        return {}
    return {"md": m.group(1), "batch": int(m.group(2)),
            "stage": m.group(3).strip()}


def label_of(md: str, batch: int, stage: str) -> str:
    return "%s 第%d批 · %s" % (md, int(batch), stage)


def next_batch(conn, stage: str = STAGE_RAW) -> str:
    """当天第几批：扫已有 display_name，取今天最大批次号 +1。

    同一天连导两次 = 第1批 / 第2批；换个日子从 1 重新数。
    不写库，纯计算；撞名由 pack_code 的唯一化负责，友好名允许重名（下拉靠 code 区分）。
    """
    md = now_md()
    mx = 0
    try:
        rows = conn.execute(
            "SELECT display_name, pack_code FROM v3_pack").fetchall()
    except Exception:                                    # noqa: BLE001
        rows = []
    for r in rows:
        try:
            nm = r["display_name"]
            code = r["pack_code"]
        except (TypeError, KeyError, IndexError):
            try:
                nm, code = r[0], r[1]
            except Exception:                            # noqa: BLE001
                continue
        info = parse_label(str(nm or ""))
        if info.get("md") == md:
            mx = max(mx, int(info.get("batch") or 0))
    return label_of(md, mx + 1, stage)


def fine_label_of(conn, source_code: str) -> str:
    """筛选后包继承源包批次；源包不是新命名（历史包）则给当天新批次。

    先按 display_name 解析；解析不到时按源包 created_at 的日期 + 当天已有批次推算，
    仍推不出就回落到「今天 第N批 · 筛选后」，绝不返回源包那一长串码。
    """
    md = now_md()
    batch = None
    try:
        row = conn.execute(
            "SELECT display_name, created_at, converted_at FROM v3_pack WHERE pack_code=?",
            (source_code,)).fetchone()
    except Exception:                                    # noqa: BLE001
        row = None
    if row:
        try:
            nm = row["display_name"]
        except (TypeError, KeyError, IndexError):
            try:
                nm = row[0]
            except Exception:                           # noqa: BLE001
                nm = ""
        info = parse_label(str(nm or ""))
        if info:
            md, batch = info["md"], int(info["batch"])
    if batch is None:
        # 源包是历史包：用它自己的创建日期，当天批次从 1 起
        ts = ""
        if row:
            for k in ("created_at", "converted_at"):
                try:
                    ts = str(row[k] or "")
                except (TypeError, KeyError, IndexError):
                    try:
                        ts = str(row[2] or "") if k == "converted_at" else str(row[1] or "")
                    except Exception:                     # noqa: BLE001
                        ts = ""
                if ts:
                    break
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", ts)
        if m:
            md = "%s-%s" % (m.group(2), m.group(3))
        batch = 1
    return label_of(md, batch, STAGE_FINE)


def friendly(it: dict) -> str:
    """下拉/表格显示名：display_name 优先；**历史包**按可查证事实压成短名。

    历史包没有 display_name（本机实测 6/6 全空），但 `source_file='screen:xxx'`
    与包码里的日期是真实存在的，据此给「MM-DD 历史 · 筛选后/筛选前」；
    连日期都读不出时如实显示原码，不编造批次号。「第N批」只给新导入的包。
    """
    if not isinstance(it, dict):
        return ""
    d = str(it.get("display_name") or "").strip()
    if d:
        return d
    code = str(it.get("pack_code") or it.get("code") or "").strip()
    src = str(it.get("source_file") or "")
    ts = ""
    for k in ("converted_at", "created_at"):
        v = str(it.get(k) or "")
        if v and v > ts:
            ts = v
    md = ""
    import re as _re
    m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", ts)
    if m:
        md = "%s-%s" % (m.group(2), m.group(3))
    else:
        m2 = RE_CODE_DATE.search(code)
        if m2:
            s = m2.group(1)
            md = "%s-%s" % (s[4:6], s[6:8])
    stage = STAGE_FINE if (src.startswith("screen:") or "筛选后" in code) else STAGE_RAW
    if md:
        return "%s 历史 · %s · %s" % (md, stage, code_hash(code))
    return code


def code_hash(code: str) -> str:
    """同日多个历史包的稳定短区分（取包码末尾 4~6 位数字/字符，不编造批次）。"""
    import hashlib
    h = hashlib.md5((code or "").encode("utf-8", "replace")).hexdigest()[:4].upper()
    tail = re.findall(r"(\d{3,6})", code or "")
    return (tail[-1] if tail else h)[-4:]


def stage_of(it: dict) -> str:
    """判定阶段：优先用 source_file='screen:xxx'（权威），再退回 display_name。"""
    if not isinstance(it, dict):
        return ""
    src = str(it.get("source_file") or "")
    if src.startswith("screen:"):
        return STAGE_FINE
    info = parse_label(str(it.get("display_name") or ""))
    if info.get("stage"):
        return info["stage"]
    return "" if src else ""


def sort_key(it: dict):
    """排序键（越大越新）：真实时间 → 包码日期 → 同批次筛选后优先 → code。

    返回 tuple，供 sorted(reverse=True) 使用。
    """
    ts = ""
    for k in ("converted_at", "created_at"):
        v = str((it or {}).get(k) or "")
        if v:
            ts = v
            break
    if not ts:
        ts = str((it or {}).get("updated_at") or "")
    code = str((it or {}).get("pack_code") or (it or {}).get("code") or "")
    m = RE_CODE_DATE.search(code)
    code_day = m.group(1) if m else ""
    st = stage_of(it)
    fine = 1 if st == STAGE_FINE else (0 if st == STAGE_RAW else -1)
    info = parse_label(str((it or {}).get("display_name") or ""))
    b = int(info.get("batch") or 0)
    return (ts, code_day, b, fine, code)


def choose_default(items: list, *, ready_only: bool = True) -> str:
    """「每个序列页默认挑最新数据包」的唯一口径。

    ① 先只看 ready 的包（没有商品/图的包不选）；
    ② 取最新批次（sort_key 最大）；
    ③ 同一批次里「筛选后」优先，没有就退「筛选前」；
    ④ 整批都没有时返回 ""（页面自己显示空态，不硬选第一个）。
    """
    pool = [x for x in (items or []) if isinstance(x, dict)
            and str(x.get("pack_code") or x.get("code") or "")]
    if ready_only:
        good = [x for x in pool if x.get("ready")]
        if good:
            pool = good
    if not pool:
        return ""
    ordered = sorted(pool, key=sort_key, reverse=True)
    top = ordered[0]
    info = parse_label(str(top.get("display_name") or ""))
    key = (info.get("md"), info.get("batch"))
    same = [x for x in ordered
            if (lambda i: (i.get("md"), i.get("batch")))(
                parse_label(str(x.get("display_name") or ""))) == key] \
        if key[0] else [top]
    for x in same:
        if stage_of(x) == STAGE_FINE:
            return str(x.get("pack_code") or x.get("code") or "")
    return str(top.get("pack_code") or top.get("code") or "")
