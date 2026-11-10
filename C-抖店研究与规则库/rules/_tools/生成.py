# -*- coding: utf-8 -*-
"""抖店公开规则库 —— 生成器
输入：D:/DSH_Cache/cache/tmp/eschool/pages/*.json（ego-browser 真实渲染后抓到的 #article-main HTML）
输出：rules/md/*.md, rules/raw/*.html, rules/index.json, rules/changelog.md,
      rules/README.md, rules/抓取清单.md
"""
import json, re, html, hashlib, os, sys, datetime

PAGES = r"D:\DSH_Cache\cache\tmp\eschool\pages"
ROOT = r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\rules"
MD = os.path.join(ROOT, "md")
RAW = os.path.join(ROOT, "raw")
CATLOG = {"violation": "违规管理", "product": "商品发布规范", "experience": "体验分",
          "rating": "店铺评分与精选联盟", "price": "价格与促销", "aftersale": "售后与纠纷",
          "deposit": "保证金与扣罚", "announce": "平台公告"}
BASE = "https://school.jinritemai.com/doudian/web/article/"

os.makedirs(MD, exist_ok=True)
os.makedirs(RAW, exist_ok=True)

# ---------- 1. HTML -> Markdown ----------
from html.parser import HTMLParser

TAGBLOCK = ("div", "p", "li", "section", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "table")

class Walker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf, self.head = [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs); cls = (a.get("class") or "")
        if tag == "br":
            self.buf.append(" ")
        elif tag in TAGBLOCK:
            self.flush()
            m = re.search(r"heading-h(\d)", cls)
            if m:
                self.head = int(m.group(1))
        elif tag in ("img",):
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in TAGBLOCK:
            self.flush()

    def handle_data(self, d):
        t = d.replace("\u200b", "").replace("\xa0", " ")
        if t.strip():
            self.buf.append(t)

    def flush(self):
        s = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if s in ("全屏展示", "退出", "收起", "展开", "复制", "查看全部", "下载"):
            self.head = None
            return
        if s:
            h, self.head = self.head, None
            self.lines.append(("#" * min(h, 6)) + " " + s if h else s)


def cell_text(c):
    c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", c)
    t = html.unescape(t).replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip().replace("|", "\\|")


def table_to_md(t):
    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", t, re.I)
    grid = []
    for r in rows:
        cells = [cell_text(c) for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", r, re.I)]
        if any(cells):
            grid.append(cells)
    if not grid:
        return ""
    n = max(len(r) for r in grid)
    grid = [r + [""] * (n - len(r)) for r in grid]
    out = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * n) + " |"]
    for r in grid[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def cut_article_content(h):
    """从 #article-main 的 outerHTML 里切出 <div class="article-content ..."> 的平衡片段"""
    m = re.search(r'<div[^>]*class="[^"]*article-content[^"]*"', h)
    if not m:
        return h
    start = m.start()
    i, depth = start, 0
    for tag in re.finditer(r"<(/?)div\b[^>]*>", h[start:], re.I):
        if tag.group(1) == "":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return h[start:start + tag.end()]
    return h[start:]


def html_to_md(h):
    h = cut_article_content(h)
    tables = []

    def repl(m):
        tables.append(table_to_md(m.group(0)))
        return "\n\n@@TBL%d@@\n\n" % (len(tables) - 1)

    h = re.sub(r"<table[\s\S]*?</table>", repl, h, flags=re.I)
    h = re.sub(r'<div[^>]*class="[^"]*(?:ep-ranges|ep-cursors)[^"]*"[^>]*>[\s\S]*?</div>', "", h, flags=re.I)
    w = Walker(); w.feed(h); w.close()
    lines = []
    for ln in w.lines:
        mm = re.match(r"@@TBL(\d+)@@$", ln.strip())
        if mm:
            lines.append(tables[int(mm.group(1))])
        else:
            lines.append(ln)
    # 去连续空行
    out, blank = [], False
    for ln in lines:
        if not ln.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


# ---------- 2. 日期解析 ----------
D = r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"


def fmt(m):
    try:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def find_dates(text, category):
    """抖店规则的标准措辞：
       5.1【生效时间】本规则于2020年10月9日首次生效，于2026年9月26日最新修订生效
       公告：规则预计于2026年11月04日生效，公示期满平台将另行发布正式生效规则
    """
    first = rev = future = exp = None
    m = re.search(D + r"\s*(?:起)?\s*首次生效", text)
    if m:
        first = fmt(m)
    for pat in [D + r"\s*(?:已)?\s*(?:最新)?修订(?:并)?(?:正式)?生效",
                D + r"\s*(?:起)?\s*(?:正式)?(?:生效|执行|实施)(?!\s*日?前)"]:
        m = re.search(pat, text)
        if m:
            rev = fmt(m)
            break
    m = re.search(r"预计于\s*" + D + r"\s*(?:正式)?生效", text) or re.search(r"(?:将)?于\s*" + D + r"\s*(?:正式)?生效", text)
    if m:
        future = fmt(m)
    for pat in [r"有效期至\s*" + D, r"至\s*" + D + r"\s*(?:止|终止)", r"失效日期[:：]?\s*" + D,
                r"(?:于|自)\s*" + D + r"\s*起?(?:废止|失效)"]:
        m = re.search(pat, text)
        if m:
            exp = fmt(m)
            break
    if category == "announce":
        return future, exp, ("text_notice_effective" if future else None), first, rev
    if rev:
        return rev, exp, "text_latest_revision", first, rev
    if first:
        return first, exp, "text_first_effective", first, rev
    return None, exp, None, first, rev


def sha1_16(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def ymd(ts):
    return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else None


# ---------- 3. 主流程 ----------
cat = {x["knowledge_id"]: x for x in json.load(open(r"D:\DSH_Cache\cache\tmp\eschool\rule-catalog.json", encoding="utf-8"))}
now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")

records, misses = [], []
for fn in sorted(os.listdir(PAGES)):
    if not fn.endswith(".json"):
        continue
    d = json.load(open(os.path.join(PAGES, fn), encoding="utf-8"))
    tgt = d.get("target", {})
    rid = tgt.get("id") or fn[:-5]
    category = tgt.get("category", "other")
    err = d.get("error")
    h = d.get("html") or ""
    md_body = html_to_md(h) if h else ""
    plain = re.sub(r"[#|>*`\-\s]+", " ", md_body)
    plain = re.sub(r"\s+", " ", plain).strip()
    if err or len(plain) < 300:
        misses.append({"id": rid, "category": category, "title": d.get("title", ""),
                       "reason": err or "正文过短（%d 字），疑似 JS 空壳" % len(plain), "url": BASE + rid})
        continue
    title = (d.get("title") or tgt.get("title_hint") or "").strip()
    page_date = d.get("pageDate")
    eff, exp, eff_src, first_eff, rev_eff = find_dates(plain, category)
    if not eff and page_date:
        eff = page_date[:10]
        eff_src = "page_publish_time"
    row = cat.get(rid)
    notice_window = None
    if row:
        es, ee = ymd(row.get("effect_start")), ymd(row.get("effect_end"))
        if es and ee:
            notice_window = "%s~%s" % (es, ee)
    rec = {
        "id": rid,
        "title": title,
        "category": category,
        "category_cn": CATLOG.get(category, category),
        "url": BASE + rid,
        "effective_date": eff,
        "effective_date_source": eff_src,
        "first_effective_date": first_eff,
        "latest_revision_date": rev_eff,
        "expire_date": exp,
        "superseded_by": None,
        "source_type": "official",
        "verified": True,
        "fetched_at": now,
        "page_publish_time": page_date,
        "content_sha1": sha1_16(md_body),
        "md_file": "md/%s.md" % rid,
        "raw_file": "raw/%s.html" % rid,
        "chars": len(plain),
        "tables": d.get("tableCount", 0),
        "summary_100": plain[:100],
    }
    if notice_window:
        rec["notice_window"] = notice_window
    records.append((rec, md_body, h, row))

# 公告 -> 被修订规则 的挂接
ann = [r for r, _, _, _ in records if r["category"] == "announce"]
by_title = {}
for r, _, _, _ in records:
    by_title.setdefault(re.sub(r"\s+", "", r["title"]), r["id"])
for r, body, _, _ in records:
    if r["category"] != "announce":
        continue
    names = re.findall(r"《([^》]{2,60})》", r["title"])
    links = []
    for n in names:
        k = re.sub(r"\s+", "", n)
        for t, tid in by_title.items():
            if tid != r["id"] and (t == k or k in t or t in k):
                links.append(tid)
    if links:
        r["revises"] = sorted(set(links))
        for tid in r["revises"]:
            for rr, _, _, _ in records:
                if rr["id"] == tid:
                    rr.setdefault("pending_revision_notice", [])
                    rr["pending_revision_notice"].append(r["id"])

# 写 md / raw —— 带 sha1 跳过与 changelog 追加
import difflib

IDX_PATH = os.path.join(ROOT, "index.json")
CLOG = os.path.join(ROOT, "changelog.md")
old = {}
if os.path.exists(IDX_PATH):
    try:
        old = {x["id"]: x for x in json.load(open(IDX_PATH, encoding="utf-8"))}
    except Exception:
        old = {}

written = skipped = 0
log_lines = []
for r, body, h, _ in records:
    prev = old.get(r["id"])
    md_path = os.path.join(MD, r["id"] + ".md")
    changed = []
    if prev:
        for f in ("title", "content_sha1", "effective_date", "expire_date", "superseded_by",
                  "first_effective_date", "latest_revision_date", "url"):
            a, b = (prev.get(f) or ""), (r.get(f) or "")
            if a != b:
                changed.append("%s: `%s` → `%s`" % (f, a or "null", b or "null"))
    r["last_check_status"] = "new" if not prev else ("changed" if changed else "unchanged")
    if prev and not changed and os.path.exists(md_path):
        skipped += 1
        continue                      # sha1 未变 → 不重写 md
    head = []
    head.append("---")
    head.append("title: %s" % r["title"])
    head.append("url: %s" % r["url"])
    head.append("category: %s" % r["category"])
    head.append("effective_date: %s" % (r["effective_date"] or ""))
    head.append("effective_date_source: %s" % (r["effective_date_source"] or ""))
    head.append("first_effective_date: %s" % (r["first_effective_date"] or ""))
    head.append("latest_revision_date: %s" % (r["latest_revision_date"] or ""))
    head.append("expire_date: %s" % (r["expire_date"] or ""))
    head.append("superseded_by: %s" % (r["superseded_by"] or ""))
    head.append("source_type: %s" % r["source_type"])
    head.append("page_publish_time: %s" % (r["page_publish_time"] or ""))
    head.append("content_sha1: %s" % r["content_sha1"])
    head.append("fetched_at: %s" % r["fetched_at"])
    head.append("---")
    head.append("")
    head.append("> 来源：官方页面（抖店规则中心 / 抖音电商学习中心）｜抓取方式：真实浏览器渲染后提取 `.article-content` 正文")
    head.append("> 原文地址：%s" % r["url"])
    if r.get("notice_window"):
        head.append("> 公示/征集期：%s（来自规则中心列表接口 effect_start~effect_end）" % r["notice_window"])
    if r.get("pending_revision_notice"):
        head.append("> 有关修订公告（待生效）：%s" % "、".join(r["pending_revision_notice"]))
    head.append("")
    old_md = ""
    if os.path.exists(md_path):
        try:
            old_md = open(md_path, encoding="utf-8").read()
        except Exception:
            old_md = ""
    open(md_path, "w", encoding="utf-8").write("\n".join(head) + "\n" + body + "\n")
    open(os.path.join(RAW, r["id"] + ".html"), "w", encoding="utf-8").write(h)
    written += 1
    if changed:
        # 正文 diff 摘要（旧内容已在写入前读出）
        diff_txt = ""
        if prev and old_md:
            add = rem = 0
            for ln in difflib.unified_diff(old_md.split("\n"), body.split("\n"), lineterm="", n=0):
                if ln.startswith("+") and not ln.startswith("+++"):
                    add += 1
                elif ln.startswith("-") and not ln.startswith("---"):
                    rem += 1
            diff_txt = "；正文 diff：+%d 行 / -%d 行" % (add, rem)
        log_lines.append("- `%s` %s —— %s%s" % (r["id"], r["title"], "；".join(changed), diff_txt))
    elif not prev:
        log_lines.append("- `%s` %s —— 首次抓取（content_sha1 `%s`，%d 字 / %d 表）"
                         % (r["id"], r["title"], r["content_sha1"], r["chars"], r["tables"]))

if log_lines:
    new_file = not os.path.exists(CLOG)
    with open(CLOG, "a", encoding="utf-8") as f:
        if new_file:
            f.write("# 变更记录\n\n"
                    "只记「哪一天、哪条规则、哪个字段变了、正文 diff 摘要」。正文 sha1 未变的规则不会出现在这里。\n\n")
        f.write("## %s（抓取 %d 条 / 重写 %d 条 / 跳过未变化 %d 条）\n\n" % (now, len(records), written, skipped))
        f.write("\n".join(log_lines) + "\n\n")

idx = [r for r, _, _, _ in records]
idx.sort(key=lambda x: (list(CATLOG).index(x["category"]) if x["category"] in CATLOG else 99, -(x["chars"] or 0)))
json.dump(idx, open(os.path.join(ROOT, "index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("written md:", written, " misses:", len(misses))
print("eff none:", sum(1 for r in idx if not r["effective_date"]))
for src in ("text_latest_revision", "text_first_effective", "text_notice_effective", "page_publish_time"):
    print("  %-24s %d" % (src, sum(1 for r in idx if r.get("effective_date_source") == src)))
print("exp set:", sum(1 for r in idx if r["expire_date"]))
for r in idx:
    print("  %-16s %-10s eff=%-10s(%s) first=%-10s exp=%-6s %5dch %2dtbl %s" % (
        r["id"], r["category"], r["effective_date"], (r["effective_date_source"] or "-")[:12],
        r["first_effective_date"] or "-", r["expire_date"] or "-", r["chars"], r["tables"], r["title"][:32]))
json.dump(misses, open(os.path.join(ROOT, "_misses.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
