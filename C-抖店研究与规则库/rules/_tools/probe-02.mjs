// 探测 2：找正文容器选择器（列 textLength 最大的若干元素）
import fs from "node:fs/promises";

const OUT = "D:/DSH_Cache/cache/tmp/eschool";
const URL1 = "https://school.jinritemai.com/doudian/web/article/101834";

const task = await taskSpace("抖店规则探测2");
const page = task.page("p1");
await page.goto(URL1);
await new Promise((r) => setTimeout(r, 3500));

const info = await page.evaluate(() => {
  const norm = (s) => (s || "").replace(/\u00a0/g, " ").trim();
  const all = Array.from(document.querySelectorAll("div,article,main,section"));
  const rows = all.map((e) => ({
    tag: e.tagName,
    cls: (e.className || "").toString().slice(0, 80),
    id: e.id || "",
    len: norm(e.innerText).length,
    tables: e.querySelectorAll("table").length,
    depth: (() => { let d = 0, p = e; while ((p = p.parentElement)) d++; return d; })(),
  })).filter((r) => r.len > 800);
  rows.sort((a, b) => b.len - a.len);
  // 找 len 最接近 3000..12000 且含表格的最小容器
  const withTables = rows.filter((r) => r.tables > 0).sort((a, b) => a.len - b.len);
  return { top: rows.slice(0, 14), smallestWithTables: withTables.slice(0, 6) };
});

await fs.writeFile(`${OUT}/probe-browser2.json`, JSON.stringify(info, null, 2), "utf8");
console.log(JSON.stringify(info.smallestWithTables, null, 1).slice(0, 1800));
console.log("--- top ---");
console.log(JSON.stringify(info.top.slice(0, 6), null, 1).slice(0, 1200));
