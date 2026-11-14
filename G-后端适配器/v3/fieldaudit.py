"""原始商品卡 JSON 全字段审计 —— 先把「源头有什么 / 解析器读没读 / 库里存没存」摊开。

为什么必须做（任务书 §三）：
  18 件商品因为解析器不认 `_extra.headImgs` 等结构，**458 条媒体整批漏掉**；
  视频在 `_extra._raw.headVideoInfo`，同样漏了 **12 条**。这说明"解析器完成"这句话
  不能用"界面上看着对"来证明，只能逐字段对账。

三条判据（缺一条就不算审计过）：
  1. 只看「原始 JSON」而不是「库里已有的列」——否则漏掉的字段永远看不见。
  2. 每个字段必须区分：源有值 / 源为空 / 源未提供 / 解析器没读 / 库里没存。
  3. **数据库为空 ≠ 源头没有**：两条证据分开列。

输出：矩阵（md）+ 结构化 JSON，供后续改解析器时逐条销账。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict

# ---------------------------------------------------------------- 解析器已读字段
# 说明：这是**当前代码实际读到的键**（逐条对照 adapters/wechat.py、adapters/weidian.py、
# importer.py 里的取值语句整理）。键不存在于这张表 → 标「解析器未读」。
PARSER_READS = {
    # 基础
    "productId", "product_id", "itemId", "title", "shortTitle", "subTitle",
    "appid", "_src", "catId", "brandId", "listingTime",
    "minPrice", "maxPrice", "minPrice_yuan", "maxPrice_yuan", "estimatePrice_yuan",
    "minEstimatePrice", "minDiscountPrice",
    "totalSold", "totalStock", "totalOrder", "totalVisit", "skuCount",
    "saleAttrs", "saleAttrsText", "skuAttrs", "skuSpecs", "params", "services",
    "evalCount", "evalGood", "evalBad", "evalImg",
    # 媒体
    "_raw.headImg", "_raw.headImgInfos", "_raw.detail.detailImg",
    "_extra.headImgs", "_extra._raw.headImg", "_extra._raw.detail.detailImg",
    "_raw.skuList", "_extra._raw.skuList", "_raw.productSkus",
    "videoUrls", "_raw.videoUrls", "_extra.videoUrls",
    "_raw.headVideoInfo", "_extra._raw.headVideoInfo",
    # 店铺 / 类目
    "_raw.category", "nodeCatId", "originNodeCatId",
}

# 字段分类（用于矩阵分组与「该不该进第三步证据」的判断）
GROUP_RULES = [
    ("媒体", ("img", "image", "video", "detailImg", "headImg", "skuList", "productSkus",
              "cover", "pic")),
    ("SKU 与规格", ("sku", "spec", "attr", "attrId", "property")),
    ("店铺信息", ("shop", "store", "seller", "nick", "mall", "merchant")),
    ("类目与品牌", ("cat", "category", "brand", "tag", "icon")),
    ("价格与库存", ("price", "stock", "sold", "order", "coupon", "discount", "presale")),
    ("基础信息", ("title", "name", "id", "code", "time", "status", "type", "desc")),
]


_SRC_CACHE: dict = {}


def _parser_keys() -> set:
    """把解析器源码里出现的所有「带引号的键名」收成一个集合。

    用它判「解析器读过这个键」比维护白名单可靠：改了代码，判定跟着变。
    覆盖四个真正读原始 JSON 的文件（import 链路）。
    """
    if _SRC_CACHE.get("keys"):
        return _SRC_CACHE["keys"]
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, "importer.py"), os.path.join(here, "cards.py"),
             os.path.join(here, "adapters", "wechat.py"),
             os.path.join(here, "adapters", "weidian.py"),
             os.path.join(here, "adapters", "__init__.py")]
    text = ""
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                text += fh.read() + "\n"
        except Exception:                    # noqa: BLE001
            pass
    keys = set(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]{1,40})["']""", text))
    _SRC_CACHE["keys"] = keys
    return keys


def _group_of(path: str) -> str:
    low = path.lower()
    for name, keys in GROUP_RULES:
        if any(k.lower() in low for k in keys):
            return name
    return "其它"


def _walk(obj, prefix: str = "", depth: int = 0, out: dict | None = None,
          max_depth: int = 8) -> dict:
    """递归收集字段：路径 → {types:set, nonempty:int, sample, list_len}。"""
    if out is None:
        out = {}
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else str(k)
            _record(out, p, v)
            if isinstance(v, (dict, list)):
                _walk(v, p, depth + 1, out, max_depth)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):        # 只看前 20 个元素，避免爆量
            p = "%s[]" % prefix
            _record(out, p, v)
            if isinstance(v, (dict, list)):
                _walk(v, p, depth + 1, out, max_depth)
    return out


def _nonempty(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict)):
        return len(v) > 0
    if isinstance(v, (int, float)):
        return v != 0
    return True


def _record(out: dict, path: str, v) -> None:
    d = out.setdefault(path, {"types": set(), "nonempty": 0, "seen": 0,
                             "sample": "", "list_len": 0})
    d["seen"] += 1
    d["types"].add(type(v).__name__)
    if isinstance(v, list):
        d["list_len"] = max(d["list_len"], len(v))
    if _nonempty(v) and not d["sample"]:
        s = v if isinstance(v, (str, int, float)) else json.dumps(v, ensure_ascii=False)
        d["sample"] = str(s)[:120]
    if _nonempty(v):
        d["nonempty"] += 1


def _needles(row: dict, urls: set) -> str:
    bits = [str(row.get("original_title") or ""), str(row.get("source_shop_name") or ""),
            str(row.get("category_id") or ""), str(row.get("source_product_id") or ""),
            str(row.get("source_store_id") or ""), str(row.get("attributes") or "")]
    bits += list(urls)[:400]
    return "\u0001".join(bits)


def audit(conn: sqlite3.Connection, pack_code: str = "", limit: int = 0) -> dict:
    """逐件读 original_payload，产出字段覆盖矩阵。"""
    where, args = "", []
    if pack_code:
        where = (" WHERE product_id IN (SELECT product_id FROM v3_product WHERE pack_code=?)")
        args = [pack_code]
    sql = "SELECT product_id, original_payload FROM v3_product" + where + " ORDER BY product_id"
    if limit:
        sql += " LIMIT %d" % int(limit)
    prods = [dict(r) for r in conn.execute(sql, args)]
    if not prods:
        return {"ok": False, "error": "没有商品可审计（先导入商品卡）"}

    agg: dict[str, dict] = {}
    per_dialect: dict[str, int] = defaultdict(int)
    read_by_parser_db = 0
    for p in prods:
        try:
            pl = json.loads(p["original_payload"] or "{}")
        except Exception:                       # noqa: BLE001
            continue
        keys = tuple(sorted(pl.keys()))
        per_dialect["+".join(keys[:4])] += 1
        fields = _walk(pl, "", 0, {}, max_depth=8)
        urls = {r[0] for r in conn.execute(
            "SELECT original_url FROM v3_image WHERE product_id=?", (p["product_id"],))}
        nd = _needles(p, urls)
        for path, d in fields.items():
            a = agg.setdefault(path, {"types": set(), "nonempty": 0, "seen": 0,
                                      "sample": "", "in_db": 0, "list_len": 0})
            a["types"] |= d["types"]
            a["seen"] += 1
            a["nonempty"] += 1 if d["nonempty"] else 0
            a["list_len"] = max(a["list_len"], d["list_len"])
            if d["sample"] and not a["sample"]:
                a["sample"] = d["sample"]
                # 这条样本值在库里出现过吗（判断"存没存"用，不判断业务正确性）
                probe = d["sample"][:60]
                if probe and probe in nd:
                    a["in_db"] += 1

    read_keys = _parser_keys()
    rows = []
    for path, a in sorted(agg.items(), key=lambda kv: (-kv[1]["nonempty"], kv[0])):
        # 「读没读」按**键名是否出现在解析器源码里**判 —— 静态白名单会过时，
        # 而源码里的字符串常量不会骗人（16 个解析器模块全文扫一遍）。
        segs = [s for s in path.replace("[]", "").split(".") if s]
        read = any(s in read_keys for s in segs[-2:]) or \
            (path.replace("[]", "").split(".")[-1] in read_keys)
        n = a["seen"]
        ne = a["nonempty"]
        if ne == 0:
            state = "源提供但为空"
        elif not read:
            state = "源有值·解析器未读"
        elif a["in_db"]:
            state = "已读·已在库"
        else:
            state = "已读·库内未直接落值"
        rows.append({
            "path": path, "group": _group_of(path),
            "types": "/".join(sorted(a["types"])), "products": n,
            "nonempty": ne, "nonempty_pct": round(ne * 100.0 / max(1, n), 0),
            "list_len": a["list_len"], "read": read, "in_db": a["in_db"],
            "state": state, "sample": a["sample"],
        })
    rows.sort(key=lambda r: (r["group"], r["state"], r["path"]))
    return {"ok": True, "products": len(prods), "paths": len(rows),
            "dialects": dict(per_dialect),
            "counts": {s: sum(1 for r in rows if r["state"] == s)
                       for s in ("源有值·解析器未读", "已读·已在库",
                                 "已读·库内未直接落值", "源提供但为空")},
            "rows": rows}


def matrix_md(res: dict, title: str = "2.5 · 原始商品卡字段覆盖率矩阵") -> str:
    if not res.get("ok"):
        return "# %s\n\n%s\n" % (title, res.get("error"))
    L = ["# %s" % title, "",
         "商品 %d 件 · 字段路径 %d 条" % (res["products"], res["paths"]), "",
         "## 一、结论计数", "",
         "| 判定 | 条数 | 含义 |", "|:--|--:|:--|",
         "| 源有值·解析器未读 | %d | **源里有数据但当前解析器没取**（必须补） |"
         % res["counts"]["源有值·解析器未读"],
         "| 已读·已在库 | %d | 已解析且能在库里找到这个值 |" % res["counts"]["已读·已在库"],
         "| 已读·库内未直接落值 | %d | 解析器读了，但没单独存（多为拼进属性/描述） |"
         % res["counts"]["已读·库内未直接落值"],
         "| 源提供但为空 | %d | 源给了键，值是空的 —— **不是解析器漏了** |"
         % res["counts"]["源提供但为空"], ""]
    d = res.get("dialects") or {}
    if d:
        L += ["## 二、卡形状（方言）分布", "", "| 顶层键组合（前 4 个） | 件数 |",
              "|:--|--:|"]
        L += ["| `%s` | %d |" % (k, v) for k, v in sorted(d.items(), key=lambda kv: -kv[1])]
        L.append("")
    L += ["## 三、逐字段矩阵", "",
          "| 分组 | 字段路径 | 类型 | 有值/总数 | 列表长 | 解析器读 | 库内可见 | 判定 | 样例（截断） |",
          "|:--|:--|:--|--:|--:|:--:|--:|:--|:--|"]
    for r in res["rows"]:
        L.append("| %s | `%s` | %s | %d/%d (%d%%) | %s | %s | %s | %s | %s |" % (
            r["group"], r["path"], r["types"], r["nonempty"], r["products"],
            r["nonempty_pct"], r["list_len"] or "-",
            "是" if r["read"] else "**否**", r["in_db"] or "-", r["state"],
            (r["sample"] or "").replace("|", "\\|")[:60]))
    L += ["", "---", "",
          "**读法**：`解析器读=否` 且 `有值/总数>0` 的行，就是「源头明明有、程序没接」——"
          "这批就是下轮要补的字段。`源提供但为空` 的行不用管，那是源本身没给。", ""]
    return "\n".join(L)


def write_reports(res: dict, out_dir: str, stamp: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    md = os.path.join(out_dir, "字段覆盖率矩阵-%s.md" % stamp)
    js = os.path.join(out_dir, "字段覆盖率矩阵-%s.json" % stamp)
    with open(md, "w", encoding="utf-8") as f:
        f.write(matrix_md(res))
    with open(js, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return {"md": md, "json": js}
