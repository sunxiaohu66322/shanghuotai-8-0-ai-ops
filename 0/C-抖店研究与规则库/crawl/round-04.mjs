import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
const task = await taskSpace("抖店后台结构采集");
const page = task.page("p1");
const norm = `(s)=> (s||"").replace(/\\s+/g," ").trim()`;

// A. 商品创建页：整页文字 + 可点项
await page.goto("https://fxg.jinritemai.com/ffa/g/create");
await page.waitForTimeout(8000);
const createDump = await page.evaluate(() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clickables = [...document.querySelectorAll("button,[role='tab'],[role='button'],a,li")]
    .map((e) => {
      const r = e.getBoundingClientRect();
      return { t: t(e.textContent).slice(0, 30), tag: e.tagName.toLowerCase(), w: Math.round(r.width), top: Math.round(r.top) };
    })
    .filter((x) => x.t && x.w > 0)
    .slice(0, 120);
  return {
    url: location.href,
    text: (document.body?.innerText || "").slice(0, 2500),
    clickables,
  };
});
await page.screenshot({ path: OUT + "/create-page.png" });

// B. 回到商品列表，点「更多操作」看批量入口
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(7000);
let moreMenu = null;
try {
  await page.click('text="更多操作"', { timeout: 4000 });
  await page.waitForTimeout(1500);
  moreMenu = await page.evaluate(() => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    const items = [...document.querySelectorAll("[role='menuitem'],li,[class*='dropdown'] [class*='item'], [class*='menu'] [class*='item']")]
      .map((e) => t(e.textContent))
      .filter((x) => x && x.length <= 20);
    return [...new Set(items)];
  });
} catch (e) {
  moreMenu = ["CLICK_FAIL: " + String(e).slice(0, 120)];
}

await fs.writeFile(OUT + "/round-04.json", JSON.stringify({ createDump, moreMenu }, null, 2), "utf8");
console.log("=== 创建页 URL:", createDump.url);
console.log(createDump.text);
console.log("--- 可点项 ---");
console.log(createDump.clickables.map((c) => `${c.t}[${c.tag}]`).join(" | "));
console.log("=== 更多操作菜单 ===");
console.log((moreMenu || []).join(" | "));
