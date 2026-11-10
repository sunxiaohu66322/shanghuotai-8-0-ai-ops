#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
to_excel.py —— 把 抖店研究/crawl3/plan-*.json 转成「抖店 Excel 导入商品」模板格式 xlsx

用法：
  python to_excel.py plan-1436.json
  python to_excel.py plan-1436.json --out out/1436.xlsx --img-mode empty
  python to_excel.py "plan-*.json" --img-mode map --img-map img_url_map.json
  python to_excel.py plan-1436.json --fill-missing zero --unparsed as-is

设计依据（全部来自 UPLOAD_DEMO1.xlsx 实测，见 01-模板结构.md）：
  · 表头在第 2 行；第 1 行 A1 是合并的「模板说明」，数据从第 3 行开始
  · 一个商品 = 连续多行；首行写商品级字段（标题/分类/轮播图1-3/详情图/属性），
    后续行只写 SKU 级字段（商品ID/规格名/规格1-3/编码/库存/价格/预览图）
  · SKU规格名称 = 规格1 + " " + 规格2 (+ " " + 规格3)  [示例：'HP31054 M']
  · 轮播图 = 3 个独立列，每列 1 个 URL；详情图 = 单列内多个 URL 用 ASCII 逗号分隔
  · 商品属性 = "键:值；键:值"（ASCII 冒号 + 全角分号）

图片：模板示例里全是 https URL，本脚本不负责上传。
      --img-mode placeholder（默认）= 用 IMG_BASE 拼一个占位 URL，结构可验证但不可真导入
      --img-mode empty        = 图片列留空
      --img-mode map          = 读 --img-map 提供的 {本地绝对路径: 公网URL} 映射
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------- 模板常量
# 列顺序严格照抄 UPLOAD_DEMO1.xlsx 第 2 行（A..P）
COLUMNS = [
    "商品ID", "商品标题", "商品分类",
    "商品轮播图1", "商品轮播图2", "商品轮播图3",
    "SKU规格名称", "规格1", "规格2", "规格3",
    "SKU编码", "SKU库存", "SKU拼单价", "SKU预览图",
    "商品详情图", "商品属性",
]
COL_DETAIL_IMG = "商品详情图"
COL_ATTR = "商品属性"
# 商品级字段：只在每个商品的首行写（实测：示例第 4-6 行这些列全空）
PRODUCT_LEVEL = {"商品标题", "商品分类", "商品轮播图1", "商品轮播图2",
                 "商品轮播图3", "商品详情图", "商品属性"}
DETAIL_SEP = ","          # 实测：O3 里 16 个 URL 之间是 U+002C
ATTR_KV_SEP = ":"         # 实测：P3 '材质:棉；品牌:范小懒' 用 U+003A
ATTR_ITEM_SEP = "；"       # 实测：U+FF1B
TEMPLATE_NOTE = (
    "模板说明：\n"
    "1、当前模板为示例参考，导入数据包可兼容其他软件导出字段信息，详见教程帮助文档\n"
    "2、下述红色字体为商品参考示例，导入前请删除\n"
    "3、商品属性字段非强校验，若无也可导入"
)

# ---------------------------------------------------------------- 规格拆解
_BRACKET_RE = re.compile(r"【([^】]*)】")                    # 【实木-可移动-翻盖可调节】
_PAREN_TAIL_RE = re.compile(r"[（(]([^（）()]*)[)）]\s*$")     # （长40）
_LEAD_CODE_RE = re.compile(r"^\s*(\d{2,6})(?=[^\d]|$)")       # 527 / 528

# 维度命名启发：命中关键词就给中文规格名，否则退化成 规格N
DIM_KEYWORDS = OrderedDict([
    ("颜色分类", ("色", "白", "黑", "蓝", "红", "绿", "灰", "粉", "黄", "紫", "棕", "胡桃", "原木", "米")),
    ("尺寸", ("长", "宽", "高", "cm", "CM", "cm", "寸", "尺", "码", "M码", "L码", "号")),
])


def parse_sku_name(name: str) -> dict:
    """
    把扁平 SKU 名拆成结构。返回：
      {'code': '527', 'bracket': '实木-可移动-翻盖可调节',
       'size': '长40', 'rest': '胡桃色', 'ok': True}
    ok=False 表示没拆出任何维度（例如促销款名）。
    """
    raw = name or ""
    s = raw.strip()
    out = {"raw": raw, "code": "", "bracket": "", "size": "", "rest": s, "ok": False}

    m = _BRACKET_RE.search(s)
    if m:
        out["bracket"] = m.group(1)
        s = (s[:m.start()] + s[m.end():]).strip()

    m = _PAREN_TAIL_RE.search(s)
    if m:
        out["size"] = m.group(1).strip()
        s = s[:m.start()].strip()

    m = _LEAD_CODE_RE.match(s)
    if m:
        out["code"] = m.group(1)
        s = s[m.end():].strip(" -_·")

    out["rest"] = s
    out["ok"] = bool(out["size"] or s)
    return out


def _keyword_name(values, fallback):
    joined = " ".join(str(v) for v in values)
    for dim, kws in DIM_KEYWORDS.items():
        if any(k in joined for k in kws):
            return dim
    return fallback


def build_dimensions(parsed: list) -> list:
    """
    从多条 parse 结果里求出维度列表：[{'name': '颜色分类', 'values': [...]}, ...]
    规则：某个位置（rest / size）在全商品内只有 1 个取值 → 不是维度，折叠成公共前缀。
    顺序：颜色类维度在前（规格1），尺寸类在后（规格2）—— 与 SKU 预览图通常跟颜色走一致。
    """
    rests = [p["rest"] for p in parsed if p["rest"]]
    sizes = [p["size"] for p in parsed if p["size"]]
    dims = []
    if len(set(rests)) >= 2 or (len(set(rests)) == 1 and not sizes):
        vals = list(OrderedDict.fromkeys(rests))
        dims.append({"name": _keyword_name(vals, "规格1"), "values": vals, "src": "rest"})
    if len(set(sizes)) >= 2 or (len(set(sizes)) == 1 and not rests):
        vals = list(OrderedDict.fromkeys(sizes))
        dims.append({"name": _keyword_name(vals, "尺寸"), "values": vals, "src": "size"})
    return dims


def _price_str(v, fmt):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fmt == "int" and abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    if fmt == "dec2":
        return "%.2f" % f
    # raw: 419.0 -> "419"，419.5 -> "419.5"
    return (str(int(f)) if abs(f - round(f)) < 1e-9 else ("%g" % f))


class ImgResolver:
    def __init__(self, mode, base=None, mapping=None):
        self.mode = mode
        self.base = (base or "https://example.invalid/dy").rstrip("/")
        self.map = mapping or {}
        self.missing = 0
        self.hits = 0

    def __call__(self, local_path):
        if not local_path:
            self.missing += 1
            return ""
        if self.mode == "empty":
            self.missing += 1
            return ""
        if self.mode == "map":
            url = self.map.get(local_path) or self.map.get(os.path.normpath(local_path))
            if url:
                self.hits += 1
                return url
            self.missing += 1
            return ""
        # placeholder
        self.hits += 1
        return "%s/%s" % (self.base, os.path.basename(local_path).replace(" ", "%20"))


def convert(plan, args, resolver, report):
    """返回 (rows, stats)。rows 是 list[dict]，键是模板列名。"""
    skus = plan.get("skus") or []
    parsed = [parse_sku_name(s.get("name", "")) for s in skus]
    # 只用「两个位置都有值」的那批来学维度，避免促销款名（如『新品限时特卖【2026款…】』）
    # 把假颜色混进取值集合（实测踩过：颜色分类被污染成 4 种）
    full = [p for p in parsed if p["ok"] and p["rest"] and p["size"]]
    base = full if len(full) >= 2 else [p for p in parsed if p["ok"]]
    dims = build_dimensions(base)

    # ---- 每个 SKU 的维度取值（按 rest / size 取值定位）
    def dim_values_for(p):
        vals = []
        for d in dims:
            vals.append(p["rest"] if d["src"] == "rest" else p["size"])
        return vals

    usable = []   # (parsed, sku, dim_values)
    for p, s in zip(parsed, skus):
        if not p["ok"] or not dims:
            report["unparsed"].append({"name": s.get("name"), "reason": "拆不出任何规格维度"})
            if args.unparsed == "as-is":
                usable.append((p, s, ["", ""]))
            continue
        vals = dim_values_for(p)
        # 关键：任何一个维度取不到值（例如促销款名没有尺寸）→ 它不是这个商品的规格行，
        # 塞进去会凭空造出一个假的规格值（实测：'新品限时特卖' 被当成第 4 种颜色）
        if any(v == "" for v in vals):
            missing_dims = [d["name"] for d, v in zip(dims, vals) if v == ""]
            report["unparsed"].append({
                "name": s.get("name"),
                "reason": "缺维度取值: %s" % "/".join(missing_dims),
            })
            if args.unparsed == "as-is":
                usable.append((p, s, vals))
            continue
        if len(set(vals)) == 1 and len(dims) > 1:
            report["unparsed"].append({"name": s.get("name"), "reason": "所有维度取值相同"})
            if args.unparsed == "as-is":
                usable.append((p, s, vals))
            continue
        usable.append((p, s, vals))

    # ---- 缺失组合
    missing_combos = []
    if dims:
        existing = {tuple(u[2]) for u in usable}
        combos = [[]]
        for d in dims:
            combos = [c + [v] for c in combos for v in d["values"]]
        for c in combos:
            if tuple(c) not in existing:
                missing_combos.append(c)
    for c in missing_combos:
        report["missing_combos"].append(c)
        if args.fill_missing == "zero":
            usable.append(({"ok": True, "code": "", "bracket": ""}, {"name": " / ".join(c),
                                                                     "price": "", "stock": 0,
                                                                     "sku_id": "", "img": ""},
                           c))

    # ---- 商品级字段
    main = plan.get("main") or []
    detail = plan.get("detail") or []
    cat = plan.get("cat") or []
    cat_str = " > ".join(str(x) for x in cat if x)
    attrs = []
    attr_map = dict(args.attr or {})
    for key, getter in (("品牌", lambda p: p.get("brand")),
                        ("材质", lambda p: p.get("wood")),
                        ("安装方式", lambda p: p.get("install")),
                        ("发货时效", lambda p: (str(p.get("ship_days")) + "天") if p.get("ship_days") else None)):
        v = getter(plan)
        if v in (None, "", "None"):
            continue
        attrs.append("%s%s%s" % (key, ATTR_KV_SEP, v))
    attr_str = ATTR_ITEM_SEP.join(attrs)

    prod_id = str(plan.get("pid") or "")

    rows = []
    for i, (p, s, vals) in enumerate(usable):
        row = {c: "" for c in COLUMNS}
        row["商品ID"] = prod_id
        if i == 0:
            row["商品标题"] = plan.get("title") or ""
            row["商品分类"] = cat_str
            for n in range(3):
                row["商品轮播图%d" % (n + 1)] = resolver(main[n] if n < len(main) else "")
            row[COL_DETAIL_IMG] = DETAIL_SEP.join(resolver(u) for u in detail if resolver and u)
            row[COL_ATTR] = attr_str
        spec_name = " ".join(v for v in vals if v)
        if not spec_name:
            spec_name = (p.get("raw") or "")[:40]
        row["SKU规格名称"] = spec_name
        for n in range(3):
            row["规格%d" % (n + 1)] = vals[n] if n < len(vals) else ""
        row["SKU编码"] = str(s.get("sku_id") or "")
        row["SKU库存"] = int(s.get("stock") or 0)
        row["SKU拼单价"] = _price_str(s.get("price"), args.price_format)
        row["SKU预览图"] = resolver(s.get("img"))
        rows.append(row)

    stats = {
        "pid": prod_id,
        "title": plan.get("title"),
        "skus_total": len(skus),
        "rows_written": len(rows),
        "dimensions": [{"name": d["name"], "values": d["values"]} for d in dims],
        "missing_combos": missing_combos,
        "unparsed": report["unparsed"],
    }
    return rows, stats


def write_xlsx(items, args, out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = args.sheet or "示例2模板"

    # 第 1 行：模板说明（实测 A1:N1 合并、红字、自动换行）
    ws["A1"] = TEMPLATE_NOTE
    ws["A1"].font = Font(color="FFFF0000", size=11)
    ws["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=14)

    # 第 2 行：表头（实测加粗）
    for idx, name in enumerate(COLUMNS, start=1):
        c = ws.cell(row=2, column=idx, value=name)
        c.font = Font(bold=True)

    # 第 3 行起：数据
    r = 3
    for rows, _stats in items:
        for row in rows:
            for idx, name in enumerate(COLUMNS, start=1):
                v = row.get(name, "")
                if v == "":
                    ws.cell(row=r, column=idx, value="")
                else:
                    ws.cell(row=r, column=idx, value=v)
            r += 1

    # 列宽照模板（D/F/O/P 很宽，因为是图片 URL）
    widths = {"A": 13.25, "B": 62.9, "C": 23.5, "D": 92.6, "E": 25.4, "G": 21.8,
              "H": 15.0, "M": 11.9, "N": 92.6, "O": 77.5, "P": 153.4}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A3"
    wb.save(out_path)
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="plan-*.json -> 抖店 Excel 导入模板 xlsx")
    ap.add_argument("plans", nargs="+", help="plan-*.json 路径或通配符")
    ap.add_argument("--out", default=None, help="输出 xlsx（单个 plan 时生效）")
    ap.add_argument("--outdir", default="out", help="多 plan 时的输出目录")
    ap.add_argument("--sheet", default="示例2模板", help="sheet 名（默认照模板）")
    ap.add_argument("--img-mode", choices=["placeholder", "empty", "map"], default="placeholder")
    ap.add_argument("--img-base", default="https://example.invalid/dy")
    ap.add_argument("--img-map", default=None, help="JSON: {本地路径: 公网URL}")
    ap.add_argument("--fill-missing", choices=["skip", "zero"], default="skip",
                    help="笛卡尔积缺失组合：skip=不生成行 / zero=生成库存0的行")
    ap.add_argument("--unparsed", choices=["skip", "as-is"], default="skip",
                    help="拆不出规格的 SKU（如促销款名）：skip / as-is=原样单行")
    ap.add_argument("--price-format", choices=["raw", "int", "dec2"], default="raw")
    ap.add_argument("--attr", action="append", default=None,
                    help="额外/覆盖属性，形如 --attr 风格:简约（键需与平台标准属性名对齐）")
    ap.add_argument("--report", default=None, help="把拆解统计写成 JSON")
    args = ap.parse_args(argv)

    files = []
    for pat in args.plans:
        files.extend(sorted(glob.glob(pat)) or [pat])

    mapping = {}
    if args.img_map:
        mapping = json.load(open(args.img_map, encoding="utf-8"))
    resolver = ImgResolver(args.img_mode, args.img_base, mapping)

    items, all_stats = [], []
    for f in files:
        try:
            plan = json.load(open(f, encoding="utf-8"))
        except Exception as e:                       # noqa: BLE001
            print("!! 读不了 %s: %s" % (f, e), file=sys.stderr)
            continue
        report = {"unparsed": [], "missing_combos": []}
        rows, stats = convert(plan, args, resolver, report)
        items.append((rows, stats))
        all_stats.append(stats)
        print("[%s] %s | 行 %d | 维度 %s | 缺失组合 %d | 跳过 %d" % (
            stats["pid"], (stats["title"] or "")[:22], stats["rows_written"],
            "×".join("%s(%d)" % (d["name"], len(d["values"])) for d in stats["dimensions"]) or "无",
            len(stats["missing_combos"]), len(stats["unparsed"])))
        for u in stats["unparsed"]:
            print("   跳过: %s  <- %s" % (u["reason"], u["name"]))
        for c in stats["missing_combos"]:
            print("   缺失组合: %s" % (" / ".join(c) if isinstance(c, list) else c))

    if not items:
        print("没有可转换的 plan", file=sys.stderr)
        return 2

    if args.out and len(items) == 1:
        out = args.out
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    else:
        os.makedirs(args.outdir, exist_ok=True)
        tag = all_stats[0]["pid"] if len(items) == 1 else "batch"
        out = os.path.join(args.outdir, "dy-excel-%s.xlsx" % tag)

    write_xlsx(items, args, out)
    print("\n写出: %s" % os.path.abspath(out))
    print("图片: 命中 %d / 未解析 %d（img-mode=%s）" % (resolver.hits, resolver.missing, args.img_mode))
    if args.img_mode == "placeholder":
        print("!! placeholder 模式只用于验证结构，不能真导入 —— 图片必须先有公网 URL")

    if args.report:
        json.dump(all_stats, open(args.report, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("统计: %s" % os.path.abspath(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
