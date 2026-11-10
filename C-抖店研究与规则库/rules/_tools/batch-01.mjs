// 批量抓取抖店规则正文（真实浏览器渲染，取 #article-main 的 outerHTML）
import fs from "node:fs/promises";

const OUT = "D:/DSH_Cache/cache/tmp/eschool/pages";
const LIST = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/rules/_tools/targets.json";

await fs.mkdir(OUT, { recursive: true });
const targets = JSON.parse(await fs.readFile(LIST, "utf8"));

const task = await taskSpace("抖店规则批量抓取");
const page = task.page("p1");

for (const t of targets) {
  const url = `https://school.jinritemai.com/doudian/web/article/${t.id}`;
  try {
    await page.goto(url);
    await new Promise((r) => setTimeout(r, 3400));
    const info = await page.evaluate(() => {
      const norm = (s) => (s || "").replace(/\u00a0/g, " ").trim();
      const main = document.querySelector("#article-main") || document.querySelector(".article-main") || document.querySelector("#article-box");
      const body = norm(document.body.innerText);
      const m = body.match(/(20\d\d-\d\d-\d\d \d\d:\d\d:\d\d)/);
      const crumbs = (() => {
        const el = document.querySelector(".article-header, .breadcrumb, #article-box");
        return el ? norm(el.innerText).slice(0, 300) : "";
      })();
      return {
        url: location.href,
        title: document.title,
        html: main ? main.outerHTML : "",
        textLen: main ? norm(main.innerText).length : 0,
        tableCount: main ? main.querySelectorAll("table").length : 0,
        pageDate: m ? m[1] : null,
        head: body.slice(0, 600),
        crumbs,
      };
    });
    await fs.writeFile(`${OUT}/${t.id}.json`, JSON.stringify({ target: t, ...info }, null, 1), "utf8");
    console.log(`OK ${t.id} len=${info.textLen} tables=${info.tableCount} date=${info.pageDate} | ${info.title.slice(0, 30)}`);
  } catch (e) {
    await fs.writeFile(`${OUT}/${t.id}.json`, JSON.stringify({ target: t, error: String(e) }, null, 1), "utf8");
    console.log(`ERR ${t.id} ${String(e).slice(0, 120)}`);
  }
}
console.log("DONE " + targets.length);
