// 探测：抖店规则详情页能否在真实浏览器里拿到正文（含表格）
import fs from "node:fs/promises";

const OUT = "D:/DSH_Cache/cache/tmp/eschool";
const URL1 = "https://school.jinritemai.com/doudian/web/article/103956";

const task = await taskSpace("抖店规则探测");
const page = task.page("p1");
await page.goto(URL1);
await new Promise((r) => setTimeout(r, 4000));

const info = await page.evaluate(() => {
  const norm = (s) => (s || "").replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").trim();
  const pick = () => {
    // 找到包含正文的容器：取文字最多的 article / div
    const cands = [
      document.querySelector("article"),
      document.querySelector("[class*='article-content']"),
      document.querySelector("[class*='articleContent']"),
      document.querySelector("[class*='detail']"),
      document.querySelector("main"),
      document.body,
    ].filter(Boolean);
    let best = null;
    let bestLen = 0;
    for (const c of cands) {
      const t = norm(c.innerText || "");
      if (t.length > bestLen) { bestLen = t.length; best = c; }
    }
    return best;
  };
  const root = pick();
  const text = norm(root ? root.innerText : "");
  return {
    title: document.title,
    url: location.href,
    bodyLen: text.length,
    tableCount: root ? root.querySelectorAll("table").length : 0,
    h1: Array.from(document.querySelectorAll("h1")).map((e) => norm(e.innerText)).slice(0, 3),
    head: text.slice(0, 400),
    tail: text.slice(-300),
    cls: root ? root.className : null,
  };
});

await fs.writeFile(`${OUT}/probe-browser.json`, JSON.stringify(info, null, 2), "utf8");
console.log(JSON.stringify(info, null, 1).slice(0, 2000));
