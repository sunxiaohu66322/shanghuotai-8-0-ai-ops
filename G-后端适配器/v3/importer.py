# -*- coding: utf-8 -*-
"""v3 · 数据包导入（P1）

支持三种真实来源，全部走同一条归一化管线：
  ① 小猪本地库   J:\\aixitong\\小猪\\本地鉴图\\data\\商品.db  （只读，154 商品 / 1732 图）
  ② 通用 CSV     packs\\商品明细.csv 等（列名自动映射，含 58 列版）
  ③ JSON 包      packs\\*.json（椅子包.json 等）

铁律：
  · 只读源库，一个字节不写
  · 原始标题 / 原始图 URL / 原始 payload 只在首次入库写入，之后不覆盖
  · 图片角色与序号原样保留；判不出的写 unknown，不猜
  · provenance 三值：taobao_live | manual_import | fixture
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from typing import Any

from . import store

PIG_DB = r"J:\aixitong\小猪\本地鉴图\data\商品.db"
PIG_PACKS = r"J:\aixitong\小猪\4.0.5本地化\packs"

# ---------------------------------------------------------------- 平台判定

_PLAT_RULES = [
    ("taobao", ("taobao.com", "tmall.com", "tb.cn")),
    ("pdd", ("yangkeduo.com", "pinduoduo.com", "pddpic.com", "pdd.com")),
    ("wechat", ("weixin.qq.com", "weixin.com", "channels.weixin",
                "wxapp.tc.qq.com", "servicewechat.com", "mmbiz.qpic.cn")),
]


def guess_platform(*urls: Any) -> str:
    blob = " ".join(str(u) for u in urls if u).lower()
    for plat, keys in _PLAT_RULES:
        if any(k in blob for k in keys):
            return plat
    return "other"


_FIXTURE_HINTS = ("fixture", "测试商品", "test-data", "sample_test")


def guess_provenance(plat: str, *texts: Any) -> str:
    blob = " ".join(str(t) for t in texts if t).lower()
    if any(h in blob for h in _FIXTURE_HINTS):
        return "fixture"
    if plat in ("taobao", "wechat"):
        return "taobao_live"
    return "manual_import"


# ---------------------------------------------------------------- 列名映射

# 原始列名 → v3 字段（大小写/空格/全角不敏感）
_COLMAP = {
    "product_id": ("goods_id", "商品id", "商品ID", "id", "productid", "商品编号", "spuid"),
    "title": ("标题", "商品名称", "商品标题", "title", "name", "goods_name", "product_name"),
    "source_url": ("分享链接", "商品链接", "链接", "url", "source_url", "detail_url", "商品url"),
    "price_min": ("最低价", "团购价格", "价格", "price", "price_min", "售价",
                  "到手价", "券后价", "实付价"),
    "price_max": ("最高价", "price_max", "原价", "单买价格"),
    "shop_name": ("店铺名", "店铺名称", "shop_name", "卖家", "seller_nick", "店铺"),
    "shop_id": ("店铺ID", "店铺id", "shop_id", "seller_id"),
    "category": ("类目", "分类", "category", "类目1", "category_path", "分类路径"),
    "brand": ("品牌", "brand"),
    "desc": ("描述", "商品描述", "description", "详情"),
    "video": ("视频", "商品视频", "video"),
    "main_image": ("商品主图", "主图", "main_image", "主图url"),
    "images": ("轮播图", "商品轮播图", "images", "轮播图url"),
    "detail_images": ("详情图", "商品详情图", "detail_images"),
    "props": ("属性", "商品属性", "props", "attributes"),
    "sku": ("SKU", "skus", "sku列表"),
    "stock": ("库存", "stock"),
    "source_file": ("来源文件", "source_file"),
    "sales": ("销量", "sales"),
    "sales_text": ("销量原文", "sales_text"),
    "appid": ("appid", "小程序appid", "小程序id"),
    "keyword": ("关键词", "keyword", "搜索词", "采集关键词"),
    "comments": ("评论数", "评价数", "comments"),
    "good": ("好评数", "good"),
    "bad": ("差评数", "bad"),
    "with_img": ("有图评论数", "with_img"),
    "shop_sales": ("店铺已售", "shop_sales"),
}


def _norm_key(s: Any) -> str:
    t = str(s or "").strip().lower()
    return re.sub(r"[\s_\-（）()【】\[\]:：]", "", t)


_LOOKUP: dict[str, str] = {}
for _field, _names in _COLMAP.items():
    for _n in _names:
        _LOOKUP[_norm_key(_n)] = _field


def map_row(row: dict) -> dict:
    """任意列名的 dict → 归一化 dict。认不出的列放进 extra。"""
    out: dict[str, Any] = {"extra": {}}
    for k, v in (row or {}).items():
        f = _LOOKUP.get(_norm_key(k))
        if f:
            if out.get(f) in (None, ""):
                out[f] = v
        else:
            out["extra"][str(k)] = v
    # 头图1..N / 详情图1..N 这种「一图一列」的宽表：拼成多值
    if not out.get("main_image") or not out.get("images"):
        heads = _numbered(row, r"^头图(\d+)$")
        if heads:
            if not out.get("main_image"):
                out["main_image"] = heads[0]
            if not out.get("images"):
                out["images"] = ",".join(heads)
    if not out.get("detail_images"):
        dets = _numbered(row, r"^详情图(\d+)$")
        if dets:
            out["detail_images"] = ",".join(dets)
    if not out.get("video"):
        for k in ("视频链接", "视频", "video_url"):
            v = (row or {}).get(k)
            if v and str(v).strip().startswith("http"):
                out["video"] = str(v).strip()
                break
    return out


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


# ---------------------------------------------------------------- 图片拆分

def _split_urls(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, dict):
                out.append(str(x.get("url") or x.get("src") or ""))
            else:
                out.append(str(x))
        return [x for x in out if x.startswith("http")]
    s = str(v)
    parts = re.split(r"[,\|;；\n\r\t]+", s)
    return [p.strip() for p in parts if p.strip().startswith("http")]


def build_image_recs(mapped: dict, skus: list[dict] | None = None) -> list[dict]:
    """mapped → v3_image 记录。主图/轮播/详情/视频 + SKU 预览图（按 SKUID 对齐）。"""
    recs: list[dict] = []
    for i, u in enumerate(_split_urls(mapped.get("main_image")), start=1):
        recs.append({"role": "main", "sequence": i, "original_url": u})
    for i, u in enumerate(_split_urls(mapped.get("images")), start=1):
        recs.append({"role": "carousel", "sequence": i, "original_url": u})
    for i, u in enumerate(_split_urls(mapped.get("detail_images")), start=1):
        recs.append({"role": "detail", "sequence": i, "original_url": u})
    v = mapped.get("video")
    if v and str(v).startswith("http"):
        recs.append({"role": "video", "sequence": 1, "original_url": str(v)})
    # SKU 预览图：能对上 SKUID 的带 sku_id，对不上的按序号落库（不猜归属）
    for i, u in enumerate(_split_urls(mapped.get("sku_images")), start=1):
        sk = (skus or [])[i - 1] if skus and len(skus) >= i else {}
        recs.append({"role": "sku", "sequence": i,
                     "sku_id": str(sk.get("sku_id") or "") or None,
                     "original_url": u})
    return recs


# ---------------------------------------------------------------- 微信小店 19 列

WECHAT19_COLS = [
    "商品ID", "商品名称", "品牌", "分类", "库存",
    "团购价格", "单买价格",
    "商品主图", "商品轮播图", "商品详情图", "商品描述", "商品视频", "商品属性",
    "SKUID", "SKU名称", "SKU库存", "SKU团购价格", "SKU单买价格", "SKU预览图",
]

_W19_KEYS = {_norm_key(c) for c in WECHAT19_COLS}


def is_wechat19(headers: Any) -> bool:
    """表头是不是微信小店 19 列（19 列里认得出 ≥15 列，且必须有商品ID/商品名称）。"""
    have = {_norm_key(h) for h in (headers or []) if str(h or "").strip()}
    return len(have & _W19_KEYS) >= 15 and {"商品id", "商品名称"} <= have


def _row19(row: dict) -> dict:
    """把一行的表头全部归一化，原始表头带空格/全角/大小写差异也能对上。"""
    return {_norm_key(k): v for k, v in (row or {}).items()}


def _pipes(v: Any) -> list[str]:
    s = str(v or "").strip()
    if not s:
        return []
    return [p.strip() for p in re.split(r"[|｜]", s)]


def parse_attributes(v: Any) -> dict:
    """'功能:超轻,透气|风格:国潮' → {'功能': '超轻,透气', '风格': '国潮'}"""
    out: dict[str, str] = {}
    for part in _pipes(v):
        if not part:
            continue
        m = re.split(r"[:：]", part, 1)
        if len(m) == 2 and m[0].strip():
            out[m[0].strip()] = m[1].strip()
    return out


def _numbered(row: dict, pattern: str) -> list[str]:
    """从 头图1..头图9 / 详情图1..N 这类列里按序号拼出 URL 列表。"""
    hit = []
    for k, v in (row or {}).items():
        m = re.match(pattern, str(k or "").strip())
        if m:
            u = str(v or "").strip()
            if u.startswith("http"):
                hit.append((int(m.group(1)), u))
    return [u for _i, u in sorted(hit)]


def parse_wechat19_row(row: dict) -> dict:
    """一行 19 列 → importer 归一化 dict（形状同 map_row 的输出）。"""
    r = _row19(row)

    def g(k: str) -> str:
        return str(r.get(_norm_key(k)) or "").strip()

    ids = _pipes(g("SKUID"))
    names = _pipes(g("SKU名称"))
    stocks = _pipes(g("SKU库存"))
    gps = _pipes(g("SKU团购价格"))
    sps = _pipes(g("SKU单买价格"))
    n = max([len(x) for x in (ids, names, stocks, gps, sps)] or [0])
    skus: list[dict] = []
    for i in range(n):
        def at(a: list[str]) -> str:
            return a[i] if i < len(a) else ""
        gp, sp = at(gps), at(sps)
        skus.append({"sku_id": at(ids), "name": at(names), "stock": at(stocks),
                     "price": gp or sp, "list_price": sp or gp,
                     "group_price": gp, "single_price": sp, "image": ""})

    attrs = parse_attributes(g("商品属性"))
    brand = g("品牌")
    if brand:
        attrs.setdefault("品牌", brand)

    return {
        "product_id": g("商品ID"),
        "title": g("商品名称"),
        "brand": brand,
        "category": g("分类"),
        "stock": g("库存"),
        "price_min": g("团购价格"),
        "price_max": g("单买价格"),
        "main_image": g("商品主图"),
        "images": g("商品轮播图"),
        "detail_images": g("商品详情图"),
        "desc": g("商品描述"),
        "video": g("商品视频"),
        "props": attrs,
        "skus": skus,
        "sku_images": g("SKU预览图"),
        "extra": {"_format": "wechat19"},
    }


# ---------------------------------------------------------------- 主流程

class ImportReport(dict):
    """导入结果：就地可读的 dict，同时带 .ok / .lines"""

    @property
    def ok(self) -> bool:
        return bool(self.get("imported") is not None)

    @property
    def lines(self) -> list[str]:
        out = ["导入 %s：新增 %d · 更新 %d · 图片新增 %d" % (
            self.get("pack") or "-", self.get("imported", 0),
            self.get("updated", 0), self.get("images", 0))]
        if self.get("rejected"):
            out.append("  ⛔ 拒收 %d 行（不是正式商品卡，见 warnings）" % self["rejected"])
        for w in self.get("warnings") or []:
            out.append("  ⚠ " + w)
        return out


def import_records(conn: sqlite3.Connection, records: list[dict],
                   pack_code: str = "", actor: str = "import") -> ImportReport:
    """一批归一化商品记录 → v3 库。records 每项是 store.upsert_product 的入参形状。

    ★ 2026-10-24：这里是**所有写入路径的唯一漏斗**，准入判定强制放在这一层——
      任何新加的数据源（淘宝卡片、URL 清单、第三方导出）都必须过 `grade.admit_record`，
      L0（只有标题/店铺/链接、或 capture_level=card 的初筛数据）一律不入库。
    """
    from . import grade as _grade
    rep = ImportReport(pack=pack_code, imported=0, updated=0, images=0,
                       skipped=0, warnings=[], rejected=0)
    for rec in records:
        title = (rec.get("original_title") or rec.get("title") or "").strip()
        if not title:
            rep["skipped"] += 1
            continue
        adm = _grade.admit_record(rec)
        if not adm["ok"]:
            rep["rejected"] = int(rep.get("rejected") or 0) + 1
            samples = rep.get("rejected_samples") or []
            if len(samples) < 8:
                samples.append({"title": title[:40], "reasons": adm["reasons"]})
            rep["rejected_samples"] = samples
            continue
        if not rec.get("source_platform"):
            rec["source_platform"] = guess_platform(rec.get("source_url"))
        if not rec.get("provenance"):
            rec["provenance"] = guess_provenance(
                rec["source_platform"], rec.get("source_url"),
                rec.get("pack_code"), rec.get("source_shop_name"))
        if pack_code and not rec.get("pack_code"):
            rec["pack_code"] = pack_code
        try:
            pid, created = store.upsert_product(conn, rec)
        except Exception as e:  # noqa: BLE001
            rep["warnings"].append("「%s」入库失败：%s" % (title[:20], e))
            rep["skipped"] += 1
            continue
        rep["imported" if created else "updated"] += 1
        n = 0
        for img in rec.get("images_recs") or []:
            try:
                _, new = store.upsert_image(conn, pid, img)
                n += 1 if new else 0
            except Exception as e:  # noqa: BLE001
                rep["warnings"].append("图片入库失败（%s）：%s" % (img.get("role"), e))
        rep["images"] += n
    if rep.get("rejected"):
        rep["warnings"].append(
            "拒收 %d 行：不是正式商品卡（第一步的初筛/卡片数据不许进商品库）"
            % rep["rejected"])
    conn.commit()
    store.audit(conn, "import", "batch", pack_code, None,
                {"imported": rep["imported"], "updated": rep["updated"],
                 "images": rep["images"], "rejected": rep.get("rejected") or 0,
                 "rejected_samples": rep.get("rejected_samples") or []}, actor=actor)
    conn.commit()
    return rep


# ---------------------------------------------------------------- 源 ①：小猪库

def _pig_connect() -> sqlite3.Connection | None:
    if not os.path.exists(PIG_DB):
        return None
    c = sqlite3.connect("file:%s?mode=ro" % PIG_DB.replace("\\", "/"), uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def list_pig_packs() -> list[dict]:
    c = _pig_connect()
    if not c:
        return []
    try:
        rows = []
        for r in c.execute("SELECT 包码, 名称, 文件, 导入时间 FROM 数据包 ORDER BY 导入时间 DESC"):
            rows.append({"pack": r["包码"], "name": r["名称"], "file": r["文件"],
                         "imported_at": r["导入时间"]})
        # 商品数按 来源文件 归到包
        fmap: dict[str, int] = {}
        for r in c.execute("SELECT 来源文件, COUNT(*) n FROM 商品 GROUP BY 来源文件"):
            for f in str(r["来源文件"] or "").split(","):
                f = f.strip()
                if f:
                    fmap[f] = fmap.get(f, 0) + r["n"]
        for it in rows:
            n = 0
            for f in str(it["file"] or "").split(","):
                n += fmap.get(f.strip(), 0)
            it["n"] = n
        return rows
    finally:
        c.close()


def import_pig(pack: str = "", limit: int = 0, actor: str = "import") -> ImportReport:
    """从 小猪 商品.db 只读导入。pack 为空 = 全部。"""
    src = _pig_connect()
    if not src:
        return ImportReport(pack=pack, imported=None, error="找不到小猪库：%s" % PIG_DB,
                            warnings=[])
    conn = store.connect()
    try:
        store.init(conn)
        where, args = "", []
        if pack:
            where = " WHERE 来源文件 LIKE ?"
            args = ["%" + pack + "%"]
        sql = "SELECT * FROM 商品" + where + " ORDER BY goods_id"
        if limit:
            sql += " LIMIT %d" % int(limit)
        goods = [dict(r) for r in src.execute(sql, args)]
        if not goods:
            return ImportReport(pack=pack or "(全部)", imported=0, updated=0, images=0,
                                skipped=0,
                                warnings=["这个包里没有商品（包码可能不是 来源文件 名）"])

        gids = [g["goods_id"] for g in goods]
        cols = {k: [] for k in ("类型", "序号", "本地路径", "新URL", "处理状态", "url", "goods_id")}
        imgs: dict[str, list[dict]] = {}
        # 分批查图，避免 IN 过长
        for i in range(0, len(gids), 400):
            chunk = gids[i:i + 400]
            q = "SELECT * FROM 图片 WHERE goods_id IN (%s)" % ",".join("?" * len(chunk))
            for r in src.execute(q, chunk):
                imgs.setdefault(r["goods_id"], []).append(dict(r))
        skus: dict[str, list[dict]] = {}
        props: dict[str, dict] = {}
        for i in range(0, len(gids), 400):
            chunk = gids[i:i + 400]
            q = ("SELECT sku_id, goods_id, 规格, 图URL, 价格, 原价, 库存 FROM SKU"
                 " WHERE goods_id IN (%s)") % ",".join("?" * len(chunk))
            for r in src.execute(q, chunk):
                skus.setdefault(r["goods_id"], []).append(dict(r))
            q2 = "SELECT goods_id, 键, 值 FROM 属性 WHERE goods_id IN (%s)" % ",".join(
                "?" * len(chunk))
            for r in src.execute(q2, chunk):
                props.setdefault(r["goods_id"], {})[r["键"]] = r["值"]

        recs: list[dict] = []
        for g in goods:
            gid = g["goods_id"]
            share = (g.get("分享链接") or "").strip()
            if share and share.startswith("goods.html"):
                share = "https://mobile.yangkeduo.com/" + share
            plat = guess_platform(share)
            cat = " > ".join([x for x in (g.get("类目1"), g.get("类目2"), g.get("类目3")) if x])
            precs = []
            for im in imgs.get(gid, []):
                role = store.norm_role(im.get("类型"))
                seq = 0
                try:
                    seq = int(float(im.get("序号") or 0))
                except Exception:
                    seq = 0
                new_url = (im.get("新URL") or "").strip()
                precs.append({
                    "role": role, "sequence": seq,
                    "original_url": im.get("url") or "",
                    "original_local_path": im.get("本地路径") or "",
                    "current_local_path": im.get("本地路径") or "",
                    "current_remote_url": new_url if new_url and new_url != im.get("url") else "",
                    "note": "小猪：%s/%s" % (im.get("下载状态") or "-", im.get("处理状态") or "-"),
                })
            sku_list = []
            for s in skus.get(gid, []):
                sku_list.append({
                    "sku_id": s.get("sku_id") or "", "name": s.get("规格") or "",
                    "price": s.get("价格") or "", "stock": s.get("库存"),
                    "image": s.get("图URL") or "",
                })
            for i, s in enumerate(sku_list, start=1):
                if s.get("image"):
                    precs.append({"role": "sku", "sequence": i, "sku_id": s["sku_id"],
                                  "original_url": s["image"]})
            recs.append({
                "source_platform": plat,
                "source_product_id": gid,
                "source_url": share,
                "original_title": g.get("标题") or "",
                "category_path": cat,
                "category_id": (g.get("类目1") or "").strip(),
                "attributes": props.get(gid) or {},
                "source_shop_name": "",
                "source_store_id": g.get("店铺ID") or "",
                "price_min": _num(g.get("最低价")),
                "price_max": _num(g.get("最高价")),
                "provenance": guess_provenance(plat, share, g.get("来源文件")),
                "pack_code": pack or (g.get("来源文件") or ""),
                "original_payload": {
                    "goods_id": gid, "标题": g.get("标题"), "销量": g.get("销量"),
                    "类目1": g.get("类目1"), "类目2": g.get("类目2"), "类目3": g.get("类目3"),
                    "品牌": g.get("品牌"), "描述": g.get("描述"), "视频": g.get("视频"),
                    "来源文件": g.get("来源文件"), "skus": sku_list,
                },
                "images_recs": precs,
            })
        return import_records(conn, recs, pack_code=pack, actor=actor)
    finally:
        conn.close()
        src.close()


# ---------------------------------------------------------------- 源 ②③：CSV / JSON

def _read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def import_csv(path: str, pack_code: str = "", actor: str = "import") -> ImportReport:
    if not os.path.exists(path):
        return ImportReport(pack=path, imported=None, error="文件不存在", warnings=[])
    text = _read_text(path)
    rd = csv.DictReader(io.StringIO(text))
    headers = list(rd.fieldnames or [])
    raw_rows = list(rd)
    if is_wechat19(headers):
        # 微信小店 19 列：走专用解析（品牌/属性/SKU 六列/多值图）
        rows = [parse_wechat19_row(r) for r in raw_rows]
        rep = import_mapped(rows, pack_code or os.path.splitext(os.path.basename(path))[0],
                            actor)
        rep["format"] = "wechat19"
        return rep
    rows = [map_row(r) for r in raw_rows]
    rep = import_mapped(rows, pack_code or os.path.splitext(os.path.basename(path))[0], actor)
    rep["format"] = "generic"
    return rep


def import_json(path: str, pack_code: str = "", actor: str = "import") -> ImportReport:
    if not os.path.exists(path):
        return ImportReport(pack=path, imported=None, error="文件不存在", warnings=[])
    try:
        data = json.loads(_read_text(path))
    except Exception as e:  # noqa: BLE001
        return ImportReport(pack=path, imported=None, error="JSON 解析失败：%s" % e, warnings=[])
    if isinstance(data, dict):
        for k in ("items", "products", "list", "data", "goods"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = [data]
    rows = [map_row(r) if isinstance(r, dict) else {} for r in data]
    return import_mapped(rows, pack_code or os.path.splitext(os.path.basename(path))[0], actor)


def _sku_list(m: dict) -> list[dict]:
    """m['skus'] / m['sku'] → 规整的 SKU 列表（19 列与 JSON 两条路共用）。"""
    v = m.get("skus") if m.get("skus") not in (None, "") else m.get("sku")
    if not v:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return []
    return [s for s in v if isinstance(s, dict)] if isinstance(v, list) else []


def _props_of(m: dict) -> dict:
    v = m.get("props")
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            if isinstance(d, dict):
                return d
        except Exception:
            return parse_attributes(v)
    return {}


def _price_span(m: dict, skus: list[dict]) -> tuple[float | None, float | None]:
    """价格：先用商品级价格；缺了就从 SKU 价回落（19 列里 SKU 一定有价）。"""
    lo = _num(m.get("price_min"))
    hi = _num(m.get("price_max"))
    vals = []
    for s in skus:
        for k in ("price", "group_price", "single_price", "list_price"):
            n = _num(s.get(k))
            if n is not None:
                vals.append(n)
                break
    if vals:
        lo = lo if lo is not None else min(vals)
        hi = hi if hi is not None else max(vals)
    return lo, hi


def import_mapped(rows: list[dict], pack_code: str = "", actor: str = "import") -> ImportReport:
    """已 map_row / parse_wechat19_row 过的记录 → v3。"""
    conn = store.connect()
    try:
        store.init(conn)
        from . import grade as _grade   # 准入判定（2026-10-24）
        recs = []
        warn = []
        rejected: list[dict] = []
        for m in rows:
            title = m.get("title") or ""
            if not title:
                warn.append("有一行没有标题，已跳过")
                continue
            url = m.get("source_url") or ""
            plat = guess_platform(url, m.get("main_image"))
            skus = _sku_list(m)
            props = _props_of(m)
            brand = str(m.get("brand") or "").strip()
            if brand:
                props.setdefault("品牌", brand)
            lo, hi = _price_span(m, skus)
            # extra 里的列（评论数/好评数/appid/关键词…）一并存进 payload，不丢
            _payload = {k: v for k, v in m.items()
                        if k not in ("extra", "skus", "sku", "props")}
            _payload["skus"] = skus
            for k_src, k_dst in (("stock", "库存"), ("desc", "描述"), ("video", "视频"),
                                 ("brand", "品牌")):
                if m.get(k_src) not in (None, ""):
                    _payload[k_dst] = m.get(k_src)
            if isinstance(m.get("extra"), dict) and m["extra"]:
                _payload["_extra"] = m["extra"]
            # ★ 来源等级标记必须**活到 payload 里**，否则准入判定看不见 → 初筛数据混进商品库
            _lvl = (m.get("capture_level")
                    or (m.get("extra") or {}).get("capture_level")
                    or m.get("capture"))
            if _lvl:
                _payload["capture_level"] = _lvl
            rec = {
                "source_platform": plat,
                "source_product_id": str(m.get("product_id") or "").strip(),
                "source_url": url if str(url).startswith("http") else "",
                "original_title": str(title).strip(),
                "category_path": str(m.get("category") or ""),
                "source_shop_name": str(m.get("shop_name") or ""),
                "source_store_id": str(m.get("shop_id") or ""),
                "price_min": lo,
                "price_max": hi,
                "provenance": guess_provenance(plat, url, pack_code),
                "pack_code": pack_code,
                "attributes": props,
                "original_payload": _payload,
                "images_recs": build_image_recs({
                    "main_image": m.get("main_image"),
                    "images": m.get("images"),
                    "detail_images": m.get("detail_images"),
                    "video": m.get("video"),
                    "sku_images": m.get("sku_images"),
                }, skus),
            }
            # ★ 2026-10-24 准入：只有标题/店铺/URL 的初筛记录**不许**进商品库。
            #   判据与报告口径统一在 server/v3/grade.py（L0/L1），这里只执行。
            rec["skus"] = skus
            rec["props"] = props
            _adm = _grade.admit_record(rec)
            if not _adm["ok"]:
                rejected.append({"title": str(title)[:40], "reasons": _adm["reasons"]})
                continue
            recs.append(rec)
        rep = import_records(conn, recs, pack_code=pack_code, actor=actor)
        if rejected:
            rep["rejected"] = len(rejected)
            rep["rejected_samples"] = rejected[:8]
            rep["warnings"].append(
                "拒绝导入 %d 行：不是正式商品卡（只有标题/店铺/链接，缺商品 ID 与媒体）。"
                "请回第一步做初筛，别把初筛记录当详情卡。" % len(rejected))
        if warn:
            rep["warnings"].extend(warn[:5])
        return rep
    finally:
        conn.close()


# ---------------------------------------------------------------- 扫描待导入

def scan_sources() -> dict:
    """列出所有可导入的源，供 UI 一个下拉选。"""
    out = {"pig_packs": list_pig_packs(), "files": [], "pig_db": os.path.exists(PIG_DB),
           "taobao_db": os.path.exists(TAOBAO_DB), "taobao_keywords": list_taobao_keywords()}
    if os.path.isdir(PIG_PACKS):
        for root, _dirs, files in os.walk(PIG_PACKS):
            for f in files:
                if os.path.splitext(f)[1].lower() in (".csv", ".json", ".xlsx", ".txt"):
                    p = os.path.join(root, f)
                    out["files"].append({
                        "path": p,
                        "name": f,
                        "rel": os.path.relpath(p, PIG_PACKS),
                        "size": os.path.getsize(p),
                    })
    return out

# ---------------------------------------------------------------- 源 ④：淘宝采集库（只读）

TAOBAO_DB = os.path.join(store.BASE_DIR, "data", "products.db")


def _taobao_connect() -> sqlite3.Connection | None:
    if not os.path.exists(TAOBAO_DB):
        return None
    c = sqlite3.connect("file:%s?mode=ro" % TAOBAO_DB.replace("\\", "/"), uri=True,
                        timeout=15)
    c.row_factory = sqlite3.Row
    return c


def list_taobao_keywords() -> list[dict]:
    """采集库里有哪些搜索关键词、各多少条（给界面做筛选）。"""
    c = _taobao_connect()
    if not c:
        return []
    try:
        out = []
        for r in c.execute("SELECT COALESCE(NULLIF(search_keyword,''),'(未标)') kw,"
                           " COUNT(*) n FROM products GROUP BY 1 ORDER BY n DESC"):
            out.append({"kw": r["kw"], "n": r["n"]})
        return out
    finally:
        c.close()


def import_taobao(keyword: str = "", limit: int = 0,
                  actor: str = "import") -> ImportReport:
    """从本项目自己的采集库导入（淘宝卡片采集的成果）。

    ★ 只读打开 products.db，一个字节不写。
    ★ 淘宝侧没有图片角色信息（只有 main_image/images/detail_images 三个字段），
      所以 role 按字段推：main_image → main，images → carousel，detail_images → detail，
      SKU 预览图 → sku。推不出的写 unknown，不猜。
    """
    src = _taobao_connect()
    if not src:
        return ImportReport(pack=keyword or "(淘宝)", imported=None,
                            error="找不到采集库：%s" % TAOBAO_DB, warnings=[])
    conn = store.connect()
    try:
        store.init(conn)
        where, args = "", []
        if keyword and keyword != "(未标)":
            where = " WHERE search_keyword=?"
            args = [keyword]
        elif keyword == "(未标)":
            where = " WHERE COALESCE(search_keyword,'')=''"
        sql = "SELECT * FROM products" + where + " ORDER BY id DESC"
        if limit:
            sql += " LIMIT %d" % int(limit)
        rows = [dict(r) for r in src.execute(sql, args)]
        if not rows:
            return ImportReport(pack=keyword or "(淘宝)", imported=0, updated=0,
                                images=0, skipped=0,
                                warnings=["这个关键词下没有商品"])
        recs = []
        for p in rows:
            precs = []
            for i, u in enumerate(_split_urls(p.get("main_image")), start=1):
                precs.append({"role": "main", "sequence": i, "original_url": u})
            for i, u in enumerate(_split_urls(p.get("images")), start=1):
                precs.append({"role": "carousel", "sequence": i, "original_url": u})
            for i, u in enumerate(_split_urls(p.get("detail_images")), start=1):
                precs.append({"role": "detail", "sequence": i, "original_url": u})
            for i, s in enumerate(store.unj(p.get("skus"), []) or [], start=1):
                if isinstance(s, dict) and s.get("image"):
                    precs.append({"role": "sku", "sequence": i,
                                  "sku_id": str(s.get("sku_id") or s.get("id") or ""),
                                  "original_url": str(s["image"])})
            cat = " > ".join(x for x in [
                (p.get("category") or "").strip(),
                (p.get("brand_type") or "").strip()] if x)
            recs.append({
                "source_platform": "taobao",
                "source_product_id": str(p.get("platform_item_id") or p.get("id")),
                "source_url": p.get("source_url") or "",
                "original_title": p.get("title") or "",
                "category_path": (p.get("category") or "").strip() or cat,
                "attributes": store.unj(p.get("props"), {}) or {},
                "source_shop_name": p.get("shop_name") or "",
                "source_store_id": p.get("shop_id") or "",
                "price_min": p.get("price_min"),
                "price_max": p.get("price_max"),
                "provenance": "taobao_live",
                "pack_code": keyword or "(淘宝)",
                "original_payload": {
                    "platform_item_id": p.get("platform_item_id"),
                    "title": p.get("title"), "shop_name": p.get("shop_name"),
                    "shop_type": p.get("shop_type"), "brand": p.get("brand"),
                    "category": p.get("category"), "sales": p.get("sales"),
                    "capture_level": p.get("capture_level"),
                    "select_grade": p.get("select_grade"),
                    "search_keyword": p.get("search_keyword"),
                    "skus": store.unj(p.get("skus"), []) or [],
                },
                "images_recs": precs,
            })
        return import_records(conn, recs, pack_code=keyword or "(淘宝)", actor=actor)
    finally:
        conn.close()
        src.close()
