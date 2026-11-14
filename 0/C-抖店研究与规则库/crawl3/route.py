#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SKU 上货路线分流器
==================
输入：plan-<pid>.json（采集来的商品数据，skus 是扁平组合）
输出：该商品走哪条上货路线 + 拆出的规格维度

分流规则（主人 2026-11-02 定）：
    SKU 数 <= 6  → manual  浏览器脚本逐项填（简单商品，手填快且稳）
    SKU 数 >  6  → excel   生成 Excel 走后台「Excel 导入商品」（多 SKU 用表格）

用法：
    python route.py            # 扫全部 plan-*.json，出一张分流表
    python route.py 1436       # 只看某个商品
    python route.py 1436 --json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------- 阈值 ----------
MANUAL_MAX_SKU = 6

# ---------- 维度词表 ----------
COLOR_WORDS = [
    "胡桃色", "白色", "黑色", "原木色", "灰色", "米色", "棕色", "咖色",
    "红色", "蓝色", "绿色", "黄色", "粉色", "紫色", "橙色", "青色",
    "浅灰", "深灰", "象牙白", "奶油色", "藤编色", "茶色", "银灰",
]
# 括号里是尺寸/规格短描述，例如（长40）（长60）（中号）（大号）
BRACKET_RE = re.compile(r"[（(]([^）)]{1,16})[）)]")
# 尺寸类关键词
SIZE_HINT_RE = re.compile(r"(长|宽|高|直径|厚)\s*\d+|\d+\s*(cm|CM|厘米|mm|米)")
SIZE_WORD_RE = re.compile(r"^(大号|中号|小号|加大|特大|标准|迷你|XL|L|M|S|XS)$")


def split_name(name: str) -> dict:
    """从一条扁平 SKU 名字里拆出颜色 / 尺寸 / 剩余款式描述"""
    color = None
    for c in COLOR_WORDS:
        if c in name:
            color = c
            break

    size = None
    for m in BRACKET_RE.finditer(name):
        inner = m.group(1).strip()
        if SIZE_HINT_RE.search(inner) or SIZE_WORD_RE.match(inner):
            size = inner
            break

    # 剩余部分（去掉颜色与括号内容后）视为款式描述
    rest = name
    if color:
        rest = rest.replace(color, "")
    rest = BRACKET_RE.sub("", rest).strip(" -—【】")
    return {"color": color, "size": size, "rest": rest}


def analyze(plan: dict) -> dict:
    skus = plan.get("skus") or []
    pid = str(plan.get("pid", "?"))

    rows = []
    for s in skus:
        name = s.get("name") or ""
        parts = split_name(name)
        rows.append({
            "name": name,
            "color": parts["color"],
            "size": parts["size"],
            "rest": parts["rest"],
            "price": s.get("price"),
            "stock": s.get("stock"),
            "sku_id": s.get("sku_id"),
            "img": s.get("img"),
        })

    colors = sorted({r["color"] for r in rows if r["color"]})
    sizes = sorted({r["size"] for r in rows if r["size"]})
    rests = sorted({r["rest"] for r in rows if r["rest"]})

    dims = {}
    if colors:
        dims["颜色"] = colors
    if sizes:
        dims["尺寸"] = sizes

    cartesian = 1
    for v in dims.values():
        cartesian *= len(v)
    if not dims:
        cartesian = len(rows)

    # 覆盖率：实际组合 / 理论笛卡尔积
    combos = set()
    for r in rows:
        key = (r["color"] or "", r["size"] or "")
        if dims:
            combos.add(key)
    coverage = (len(combos) / cartesian) if cartesian else 1.0

    # 缺失组合（理论有、实际无）
    missing = []
    if colors and sizes:
        have = {(r["color"], r["size"]) for r in rows}
        for c in colors:
            for s in sizes:
                if (c, s) not in have:
                    missing.append(f"{c}×{s}")

    n = len(skus)
    route = "manual" if n <= MANUAL_MAX_SKU else "excel"

    if route == "manual":
        reason = f"{n} 个 SKU ≤ {MANUAL_MAX_SKU}，浏览器逐项填：{len(dims)} 组规格共 {cartesian} 行，手填可控"
    else:
        reason = f"{n} 个 SKU > {MANUAL_MAX_SKU}，转 Excel 导入：{len(dims)} 组规格共 {cartesian} 行，逐项填易错"

    warnings = []
    if rests and len(rests) > 1:
        warnings.append(f"存在 {len(rests)} 种款式描述（{', '.join(rests[:3])}…），可能还需要第三组规格「款式」，或拆成多个商品")
    if missing:
        warnings.append(f"缺失组合 {len(missing)} 个：{', '.join(missing[:6])} —— 需人工决定是库存置 0、还是不上架")
    if any(r["size"] is None for r in rows) and sizes:
        warnings.append("有 SKU 拆不出尺寸，规格值可能不统一")

    return {
        "pid": pid,
        "title": plan.get("title", "")[:40],
        "sku_count": n,
        "dims": dims,
        "cartesian": cartesian,
        "coverage": round(coverage, 2),
        "missing_combos": missing,
        "route": route,
        "threshold": MANUAL_MAX_SKU,
        "reason": reason,
        "rests": rests,
        "warnings": warnings,
        "rows": rows,
    }


def load_plans(only_pid=None):
    out = []
    for p in sorted(ROOT.glob("plan-*.json")):
        pid = p.stem.replace("plan-", "")
        if only_pid and pid != str(only_pid):
            continue
        try:
            out.append((pid, json.loads(p.read_text(encoding="utf-8"))))
        except Exception as e:
            print(f"[warn] {p.name} 读取失败: {e}", file=sys.stderr)
    return out


def build_spec(r: dict) -> dict:
    """把分析结果转成浏览器脚本吃的 spec-<pid>.json"""
    price = {}
    for row in r["rows"]:
        key = f"{row['color'] or ''}|{row['size'] or ''}"
        price[key] = {"p": row["price"], "s": row["stock"]}

    # 缺失组合：沿用同尺寸价、库存 0（抖音价格必填，库存 0 等于不卖）
    if r["dims"].get("颜色") and r["dims"].get("尺寸"):
        for c in r["dims"]["颜色"]:
            for s in r["dims"]["尺寸"]:
                k = f"{c}|{s}"
                if k not in price:
                    same_size = [v for kk, v in price.items() if kk.endswith("|" + s) and v.get("p")]
                    price[k] = {"p": same_size[0]["p"] if same_size else None, "s": 0, "synthesized": True}

    return {
        "pid": r["pid"],
        "route": r["route"],
        "dims": r["dims"],
        "price": price,
        "missing_combos": r["missing_combos"],
        "spec_images": {},   # 规格图映射（仅第一组），由人工或后续脚本填
        "warnings": r["warnings"],
    }


def main():
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    do_write = "--write" in args
    args = [a for a in args if not a.startswith("--")]
    only = args[0] if args else None

    plans = load_plans(only)
    if not plans:
        print("没有找到 plan-*.json" + (f"（pid={only}）" if only else ""))
        return 1

    results = [analyze(p) for _, p in plans]

    if do_write:
        for r in results:
            out = ROOT / f"spec-{r['pid']}.json"
            out.write_text(json.dumps(build_spec(r), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写 {out.name}  (route={r['route']}, 维度 {len(r['dims'])} 组, 价格档 {len(build_spec(r)['price'])} 个)")
        return 0

    if as_json:
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))
        return 0

    print(f"{'pid':<10}{'SKU':>4}  {'路线':<8}{'规格维度':<34}{'理论行':>5}  覆盖")
    print("-" * 88)
    for r in results:
        dimtxt = " × ".join(f"{k}={len(v)}" for k, v in r["dims"].items()) or "无规格"
        print(f"{r['pid']:<10}{r['sku_count']:>4}  {r['route']:<8}{dimtxt:<34}{r['cartesian']:>5}  {r['coverage']:.0%}")
        if r["warnings"]:
            for w in r["warnings"]:
                print(f"{'':<10}  ⚠ {w}")
    print()
    m = sum(1 for r in results if r["route"] == "manual")
    e = len(results) - m
    print(f"汇总：手动 {m} 个 · Excel {e} 个 · 共 {len(results)} 个商品")
    return 0


if __name__ == "__main__":
    sys.exit(main())
