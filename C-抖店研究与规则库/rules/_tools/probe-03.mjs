// 探测 3：#article-main 的直接子节点结构（找出推荐位/目录等杂质）
import fs from "node:fs/promises";
const OUT = "D:/DSH_Cache/cache/tmp/eschool";
const ids = ["103956", "101936"];
const task = await taskSpace("抖店结构探测");
const page = task.page("p1");
const res = {};
for (const id of ids) {
  await page.goto(`https://school.jinritemai.com/doudian/web/article/${id}`);
  await new Promise((r) => setTimeout(r, 3000));
  res[id] = await page.evaluate(() => {
    const norm = (s) => (s || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    const main = document.querySelector("#article-main");
    const kids = main ? Array.from(main.children) : [];
    return kids.map((e, i) => ({
      i,
      tag: e.tagName,
      cls: (e.className || "").toString().slice(0, 60),
      id: e.id || "",
      len: norm(e.innerText).length,
      tables: e.querySelectorAll("table").length,
      head: norm(e.innerText).slice(0, 60),
    }));
  });
}
await fs.writeFile(`${OUT}/probe-children.json`, JSON.stringify(res, null, 1), "utf8");
for (const id of ids) {
  console.log("=== " + id);
  for (const k of res[id]) console.log(` ${k.i} ${k.tag}.${k.cls} len=${k.len} tbl=${k.tables} | ${k.head}`);
}
