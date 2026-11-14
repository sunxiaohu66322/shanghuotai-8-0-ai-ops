# -*- coding: utf-8 -*-
"""词表 · 违规/品牌/夸张/营销词的**唯一**来源（2026-10-24 第二阶段任务一）

## 这个文件为什么存在

实测同一张图，两条路径给出**相反结论**：

```
2.5 / 第 3 步   images.build_words()          20587 条
第 4 步打码     bridge._detect_words_file()   20614 条
   A−B 16 条 = 家电家具类目词（按摩椅/双人床/浴缸/空调/冰箱…）
               来自 category_rules.json:排除类目
   B−A 43 条 = 官方词库里的营销/水印词（包邮/热销/优惠券/VIP/水印…）
```

两条路径各读各的文件、各自合并，必然打架。现在合并逻辑只有这一处，
两边都调它；只要调用同一个 `build()`，同一张图的命中集就不可能不同。

## 一份词表、四个来源、五种类别

| 来源 | 文件 | 条数 | 归类 |
|:--|:--|--:|:--|
| 官方打码词库 | `xiaozhu4/keywords.txt` | 2698 | 命中 MARKETING_WORDS → marketing，其余 → official |
| 打码专用 | `词库/打码词.txt` | 2460 | mask（图上出现就要遮） |
| 词库主表 | `词库/词库主表.tsv` 第 2 列 | 20859 | brand / violation |
| 广告法夸张词 | `keywords.HYPE_WORDS` | 19 | hype |

再减去 `v3_keyword_waiver`（人手动豁免的词）。

### 一处刻意的排除：category_rules 类目词不进图片词表

`v3_keyword` 里有一批 source = `category_rules.json:*` 的词（按摩椅、双人床、浴缸、
空调、冰箱…），它们是**类目排除规则**，由 flow 的类目逻辑按标题/类目判，不该同时被
当成"图上印了违规字"去涂白 —— 一张图里出现"空调"两个字不是违规。旧版
`build_words` 把 kind='violation' 全收了，导致这 16 个词会在图片层直接 block。

## 类别怎么用（上层只读 kind，不再自己判来源）

```
hype       → 广告法绝对化用语，block（人工核过的小名单，再短也 block）
brand      → 图上出现品牌字，warn（可能是同牌别名，也可能真是别的牌子）
violation  → 违规字，block
marketing  → 营销/水印词，warn（第 4 步涂白就能救）
mask       → 打码词，**warn**（2026-10-26 改：主人定调「这种词是遮蔽，不是整个不通过」。
             原来这里标 block，一件货身上印两个「质保」「必备」就整件拦下，一包能全灭）
official   → 官方词库里的其余词，block
```
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any

# ---------------------------------------------------------------- 词表来源

XIAOZHU_WORDS = r"J:\aixitong\小猪\xiaozhu4\keywords.txt"
MASK_WORDS_FILE = r"J:\aixitong\小猪\词库\打码词.txt"
WORD_MASTER = r"J:\aixitong\小猪\词库\词库主表.tsv"

# 同一个词在多个来源出现时，取"更硬"的那个类别（决定第 3 步判 block 还是 warn）
_KIND_RANK = {"violation": 60, "brand": 50, "hype": 40, "official": 30,
              "mask": 20, "marketing": 10}

# 营销 / 水印类：图上天天出现、但涂白就能救，不能当违规字把货拦掉。
# 这份表同时被 flow._image_reasons 用来把命中降级成 warn（只此一份）。
MARKETING_WORDS = {
    # —— 原有（flow 里那份，一字未删）
    "特价", "包邮", "限时", "秒杀", "促销", "优惠", "清仓", "爆款",
    "热卖", "新品", "正品", "厂家直销", "买一送一", "满减", "折扣",
    "券后", "活动价", "到手价", "直播", "直播间", "同款", "网红",
    "ins风", "预售", "加赠", "赠品", "福利", "低价", "白菜价",
    "双11", "双十一", "双12", "双十二", "618", "年货节", "聚划算",
    "百亿补贴", "开学季", "母亲节", "父亲节", "情人节",
    # —— 2026-10-24 新增：官方词库里 43 条"2.5 没有、第 4 步有"的词。
    #    不并进这里的话，统一之后它们会以 official/violation 身份
    #    在第 3 步把「包邮」「热销」这种字判成 block，一拦一大片。
    "vip", "上新", "专柜", "价格", "优惠券", "低至", "全店包邮", "全网最低",
    "半透明水印", "品牌水印", "售价", "店铺水印", "意式", "打折", "折后",
    "折起", "抢购", "断货", "新品首发", "比某宝便宜", "水印", "消费券",
    "满额包邮", "热销", "特惠", "畅销", "疯抢", "立减", "红包", "脱销",
    "营销", "补货", "重复水印", "顺丰包邮",
}
_MARKETING_UP = {w.upper() for w in MARKETING_WORDS}

# 只在图片层排除的来源前缀（类目规则不是"图上违规字"）
_IMAGE_EXCLUDE_SOURCE = ("category_rules.json",)


# ---------------------------------------------------------------- 读文件

def _read_lines(path: str) -> list[str]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
        except UnicodeDecodeError:
            continue
        except OSError:
            return []
    return []


def _master_rows(path: str) -> list[tuple[str, str]]:
    """词库主表：`词<TAB>类别[<TAB>...]`。类别只取 brand/violation/both。"""
    out: list[tuple[str, str]] = []
    for ln in _read_lines(path):
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        w, k = parts[0].strip(), parts[1].strip().lower()
        if len(w) >= 2 and k in ("brand", "violation", "both"):
            out.append((w, "brand" if k == "both" else k))
    return out


# ---------------------------------------------------------------- 合并（唯一一处）

def pairs(conn: sqlite3.Connection, for_image: bool = True) -> list[tuple[str, str, str]]:
    """返回 [(词, 类别, 来源)]，已去重、已减豁免。**这是全系统唯一的合并逻辑。**"""
    acc: dict[str, tuple[str, str, str]] = {}

    def _add(word: str, kind: str, source: str) -> None:
        w = (word or "").strip()
        if len(w) < 2:
            return
        up = w.upper()
        try:
            up.encode("utf-8")
        except UnicodeEncodeError:
            return
        old = acc.get(up)
        if old is None or _KIND_RANK.get(kind, 0) > _KIND_RANK.get(old[1], 0):
            acc[up] = (w, kind, source)

    # ① 数据库词库（品牌/违规）——跳过类目规则来源（见文件头说明）
    try:
        for r in conn.execute(
                "SELECT text, kind, COALESCE(source,'') src FROM v3_keyword"
                " WHERE kind IN ('brand','violation') AND status<>'disabled'"
                " AND length(text)>=2"):
            src = (r["src"] or "") if hasattr(r, "keys") else (r[2] or "")
            if for_image and any(src.startswith(p) for p in _IMAGE_EXCLUDE_SOURCE):
                continue
            _add(r["text"] if hasattr(r, "keys") else r[0],
                 r["kind"] if hasattr(r, "keys") else r[1], "db:" + (src or "v3_keyword"))
    except Exception:  # noqa: BLE001
        pass

    # ② 官方词库（2698）——营销词按 MARKETING_WORDS 归类，其余算 official
    for w in _read_lines(XIAOZHU_WORDS):
        _add(w, "marketing" if w.upper() in _MARKETING_UP else "official",
             "file:xiaozhu4/keywords.txt")

    # ③ 打码专用词
    for w in _read_lines(MASK_WORDS_FILE):
        _add(w, "marketing" if w.upper() in _MARKETING_UP else "mask",
             "file:词库/打码词.txt")

    # ④ 词库主表
    for w, k in _master_rows(WORD_MASTER):
        _add(w, "marketing" if w.upper() in _MARKETING_UP else k,
             "file:词库/词库主表.tsv")

    # ⑤ 广告法夸张词（人工核过的小名单，优先级最高一档之外）
    try:
        from . import keywords as kw_mod  # noqa: PLC0415
        for w in (getattr(kw_mod, "HYPE_WORDS", None) or []):
            _add(str(w), "hype", "keywords.HYPE_WORDS")
    except Exception:  # noqa: BLE001
        pass

    # ⑥ 减掉人工豁免
    try:
        waived = {(r[0] or "").strip().upper()
                  for r in conn.execute("SELECT text FROM v3_keyword_waiver")}
    except Exception:  # noqa: BLE001
        waived = set()

    return [v for k, v in acc.items() if k not in waived]


# ---------------------------------------------------------------- 匹配器

class WordSet:
    """把「词 → 类别」装成按长度分桶的字典，一次扫描抠出全部命中（长词优先）。

    为什么不用 v3_keyword 逐词 SQL 查：一张图十几段字、一批上千张图，
    每段都去数据库查一遍是纯浪费；这里在内存里按长度滑窗，一张图毫秒级。
    """

    def __init__(self, pairs: list[tuple[str, str]]):
        self.by_len: dict[int, dict[str, str]] = {}
        for w, kind in pairs:
            w = (w or "").strip()
            if len(w) < 2:
                continue
            self.by_len.setdefault(len(w), {}).setdefault(w.upper(), kind or "")
        self.lens = sorted(self.by_len, reverse=True)   # 长词优先：避免「最」先命中「最高级」
        self.size = sum(len(v) for v in self.by_len.values())

    def match(self, text: str) -> list[tuple[str, str, int]]:
        if not text:
            return []
        up = text.upper()
        n, out, i = len(up), [], 0
        while i < n:
            for L in self.lens:
                if i + L > n:
                    continue
                seg = up[i:i + L]
                k = self.by_len[L].get(seg)
                if k is None:
                    continue
                # 纯 ASCII 词必须整词边界。实测必要性：词库里有个品牌词「Nature」，
                # 不设边界时 OCR 读到 Naturehike 会被切成 Nature+hike 误判成两个品牌词。
                if seg.isascii():
                    a = up[i - 1] if i > 0 else " "
                    b = up[i + L] if i + L < n else " "
                    if a.isalnum() or b.isalnum():
                        continue
                out.append((text[i:i + L], k, i))
                i += L
                break
            else:
                i += 1
        return out


def build(conn: sqlite3.Connection) -> WordSet:
    """唯一的成品词表。第 3 步、2.5、第 4 步、打码、定位全用这一份。"""
    return WordSet([(w, k) for w, k, _ in pairs(conn)])


# ---------------------------------------------------------------- 版本与导出

def stats(conn: sqlite3.Connection) -> dict:
    """来源分布 —— 给界面/报告看「这份词表到底由什么组成」。"""
    by_src: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for _w, k, s in pairs(conn):
        by_src[s] = by_src.get(s, 0) + 1
        by_kind[k] = by_kind.get(k, 0) + 1
    return {"by_source": by_src, "by_kind": by_kind}


def version(conn: sqlite3.Connection) -> dict:
    """词表指纹：条数 + sha256（按「词→类别」排序后算）。

    每跑一次 OCR / 检测都把指纹写进记录，将来词表变了能说清
    「这条结论是用哪一版词表算出来的」。
    """
    rows = sorted((w.upper() + "\t" + k) for w, k, _ in pairs(conn))
    h = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return {"count": len(rows), "sha256": h,
            "label": "words-%d-%s" % (len(rows), h[:8])}


def write_file(conn: sqlite3.Connection, out_dir: str = "") -> dict:
    """把当前词表写成「一行一个词」的文件，给 鉴图.py --words / detect_slim 用。

    文件名带指纹，内容与 build() 完全同源 —— 这是第 4 步和 2.5 不再分叉的关键。
    """
    v = version(conn)
    d = out_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "words")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "words_%d_%s.txt" % (v["count"], v["sha256"][:8]))
    ws = sorted({w for w, _k, _s in pairs(conn)},
                key=lambda x: (-len(x), x))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ws) + "\n")
    os.replace(tmp, path)
    return {"ok": True, "path": path, **v}


def load_words(path: str) -> "WordSet":
    """从写出来的词表文件反读成 WordSet（检测侧要"用哪份文件就用哪份匹配"）。"""
    return WordSet([(w, "official") for w in _read_lines(path)])


# 兼容旧名（bridge 里历史写法）
def build_words(conn: sqlite3.Connection) -> WordSet:  # noqa: D103
    return build(conn)
