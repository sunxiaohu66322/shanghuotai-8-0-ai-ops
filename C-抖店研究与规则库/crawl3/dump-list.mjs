// 抓抖店商品列表全部行（含分页信息），确认到底有几件、状态如何
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const task = await taskSpace("抖店批量上货");
const page = task.page("p1");
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(9000);
const r = await page.evaluate(() => {
  const trs = [...document.querySelectorAll("tr")].map((tr) => (tr.innerText || "").replace(/\s+/g, " ").trim()).filter((t) => t.length > 10);
  const body = (document.body.innerText || "");
  const nav = (body.match(/共\s*\d+\s*[条件个]/g) || []).slice(0, 5);
  const tabs = [...document.querySelectorAll("[role=tab],div,span")].filter((e) => e.offsetParent !== null && /^(全部|售卖中|审核中|已下架|草稿箱|已售罄)/.test((e.innerText || "").trim())).map((e) => (e.innerText || "").trim()).slice(0, 12);
  return { trs, nav, tabs, url: location.href };
});
console.log(JSON.stringify(r, null, 1).slice(0, 4000));
await page.screenshot({ path: `${ROOT}/shots/list-all.png` }).catch(() => {});
console.log("LIST_DUMP_DONE");
