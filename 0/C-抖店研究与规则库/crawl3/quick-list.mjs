/** 列出商品管理最新几件（按上架时间降序），确认新上架的商品 */
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const task = await taskSpace("抖店批量上货");
const page = task.page("p1");
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(8000);

const r = await page.evaluate(() => {
  const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
  const items = rows.map((tr) => {
    const txt = (tr.innerText || "").replace(/\s+/g, " ");
    const first = (tr.innerText || "").split("\n").map((s) => s.trim()).filter(Boolean)[0] || "";
    return {
      title: first.slice(0, 34),
      id: (txt.match(/ID[：:]\s*(\d{10,})/) || [])[1],
      price: (txt.match(/￥[\d.]+/) || [])[0],
      status: (txt.match(/(售卖中|审核中|已售罄|已下架|草稿|待审核)/g) || []).join("/"),
      time: (txt.match(/\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}/) || [])[0],
    };
  });
  return { total: items.length, items: items.slice(0, 12) };
});
console.log(JSON.stringify(r, null, 1));
console.log("LIST_DONE");
