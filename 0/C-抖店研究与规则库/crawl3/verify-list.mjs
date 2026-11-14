/**
 * 快速核验：列表里同标题商品有几件、各自 ID 与状态
 */
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(`${ROOT}/plan-1531.json`, "utf8"));
const t30 = Array.from(plan.title || "").slice(0, 12).join("");

const task = await taskSpace("抖店批量上货");
const page = task.page("p1");
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(7000);

const r = await page.evaluate((key) => {
  const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
  const hits = rows
    .filter((tr) => (tr.innerText || "").includes(key))
    .map((tr) => {
      const txt = (tr.innerText || "").replace(/\s+/g, " ");
      return {
        id: (txt.match(/ID[：:]\s*(\d{10,})/) || [])[1],
        price: (txt.match(/￥[\d.]+/) || [])[0],
        status: (txt.match(/(售卖中|审核中|已售罄|已下架|草稿|待审核)/g) || []).join("/"),
        time: (txt.match(/\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}/) || [])[0],
        title: (tr.innerText || "").split("\n")[0].slice(0, 40),
      };
    });
  return { key, total: rows.length, hits, head: (document.body.innerText || "").slice(0, 200) };
}, t30);

console.log(JSON.stringify(r, null, 1));
await page.screenshot({ path: `${ROOT}/shots/verify-list-${Date.now()}.png` }).catch(() => {});
console.log("VERIFY_DONE");
