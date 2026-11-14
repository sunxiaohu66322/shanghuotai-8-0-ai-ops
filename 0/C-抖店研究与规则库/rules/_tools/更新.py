# -*- coding: utf-8 -*-
"""抖店公开规则库 —— 增量更新器

判据（README 里对主人的承诺，实现就在这里）：
  重新抓取同一条规则 → 正文 sha1(16)
    · sha1 与 index.json 里的记录一致 → 标 unchanged，**不重写 md**（mtime 不变）
    · sha1 不一致                  → 重写 md + raw，并在 changelog.md 追加一行
                                      （哪一天 / 哪条规则 / 哪个字段变了 / diff 摘要）

用法
  python 更新.py --list                 # 看库里有什么
  python 更新.py                        # 全量重抓 + 比对（会写盘，等同 --apply）
  python 更新.py --id 103956            # 只更新一条
  python 更新.py --dry                  # 只抓不比、不写盘

为什么必须用浏览器
  目标站正文是 JS 渲染的：纯 HTTP 拿到的 HTML 只有 28KB 空壳（正文文本 65 字），
  真实正文得等前端渲染完从 `.article-content` 里取。所以本脚本调 ego-browser，
  而不是 requests。

流程
  1) 把要更新的 id 写成 targets.json
  2) ego-browser 渲染抓 #article-main → pages/<id>.json
  3) 调 生成.py：它负责 HTML→Markdown、日期解析、sha1 比对、跳过未变化的 md、
     并在 changelog.md 追加真正的 diff 行
"""
import json, os, sys, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.dirname(ROOT)
IDX = os.path.join(RULES, "index.json")
EGO = r"F:\Users\Administrator\.local\share\ego\active_version_dir\ego-browser.exe"
PAGES = r"D:\DSH_Cache\cache\tmp\eschool\pages"
GEN = os.path.join(ROOT, "生成.py")

MJS = r"""
import fs from "node:fs/promises";
const LIST = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/rules/_tools/_update-targets.json";
const OUT = "D:/DSH_Cache/cache/tmp/eschool/pages";
const targets = JSON.parse(await fs.readFile(LIST, "utf8"));
await fs.mkdir(OUT, { recursive: true });
const task = await taskSpace("抖店规则增量更新");
const page = task.page("p1");
for (const t of targets) {
  try {
    await page.goto(`https://school.jinritemai.com/doudian/web/article/${t.id}`);
    await new Promise((r) => setTimeout(r, 3400));
    const info = await page.evaluate(() => {
      const norm = (s) => (s || "").replace(/\u00a0/g, " ").trim();
      const main = document.querySelector("#article-main");
      const body = norm(document.body.innerText);
      const m = body.match(/(20\d\d-\d\d-\d\d \d\d:\d\d:\d\d)/);
      return { url: location.href, title: document.title,
               html: main ? main.outerHTML : "",
               textLen: main ? norm(main.innerText).length : 0,
               tableCount: main ? main.querySelectorAll("table").length : 0,
               pageDate: m ? m[1] : null };
    });
    await fs.writeFile(`${OUT}/${t.id}.json`, JSON.stringify({ target: t, ...info }, null, 1), "utf8");
    console.log(`OK ${t.id} len=${info.textLen}`);
  } catch (e) {
    console.log(`ERR ${t.id} ${String(e).slice(0, 100)}`);
  }
}
"""


def main():
    args = sys.argv[1:]
    idx = json.load(open(IDX, encoding="utf-8")) if os.path.exists(IDX) else []
    if "--list" in args:
        for r in idx:
            print("%-16s %-10s %-10s %s" % (r["id"], r["category"], r.get("content_sha1", ""), r["title"][:44]))
        print("\n共 %d 条" % len(idx))
        return
    only = args[args.index("--id") + 1] if "--id" in args else None
    targets = [{"id": r["id"], "category": r["category"], "title_hint": r["title"]}
               for r in idx if only is None or r["id"] == only]
    if not targets:
        print("index.json 里没有可更新的目标")
        return
    lf = os.path.join(ROOT, "_update-targets.json")
    json.dump(targets, open(lf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    mjs = os.path.join(ROOT, "_update-fetch.mjs")
    open(mjs, "w", encoding="utf-8").write(MJS)
    print("① 抓取 %d 条（真实浏览器渲染）…" % len(targets))
    subprocess.run([EGO, "nodejs", "-e", "await import('file:///%s')" % mjs.replace("\\", "/")], check=False)
    if "--dry" in args:
        print("② --dry：跳过写盘。")
        return
    print("② 交给 生成.py 做 sha1 比对 / 跳过未变化 / 追加 changelog …")
    subprocess.run([sys.executable, GEN], check=False)


if __name__ == "__main__":
    main()
