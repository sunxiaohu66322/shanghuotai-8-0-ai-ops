// 第二批：平台公告（公示/意见征集通知）
import fs from "node:fs/promises";

const OUT = "D:/DSH_Cache/cache/tmp/eschool/pages";
const LIST = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/rules/_tools/targets2.json";

await fs.mkdir(OUT, { recursive: true });
const targets = JSON.parse(await fs.readFile(LIST, "utf8"));

const task = await taskSpace("抖店公告抓取");
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
      return {
        url: location.href,
        title: document.title,
        html: main ? main.outerHTML : "",
        textLen: main ? norm(main.innerText).length : 0,
        tableCount: main ? main.querySelectorAll("table").length : 0,
        pageDate: m ? m[1] : null,
        head: body.slice(0, 600),
      };
    });
    await fs.writeFile(`${OUT}/${t.id}.json`, JSON.stringify({ target: t, ...info }, null, 1), "utf8");
    console.log(`OK ${t.id} len=${info.textLen} tables=${info.tableCount} date=${info.pageDate}`);
  } catch (e) {
    await fs.writeFile(`${OUT}/${t.id}.json`, JSON.stringify({ target: t, error: String(e) }, null, 1), "utf8");
    console.log(`ERR ${t.id} ${String(e).slice(0, 120)}`);
  }
}
console.log("DONE " + targets.length);
