import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
const task = await taskSpace("抖店后台结构采集");
const p2 = task.page("p2");
const p1 = task.page("p1");
const res = {};

// A. 编辑页：点「商品类目」旁的「修改」，抓类目树
try {
  await p2.click('text="修改" >> nth=0', { timeout: 5000 });
  await p2.waitForTimeout(3500);
  res.category = await p2.evaluate(() => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const modal = [...document.querySelectorAll("[class*='modal'],[class*='dialog'],[role='dialog']")].filter(vis).pop();
    const root = modal || document.body;
    return {
      text: (root.innerText || "").slice(0, 2000),
      items: [...root.querySelectorAll("li,[class*='item'],[class*='node']")].filter(vis)
        .map((e) => t(e.textContent)).filter((x) => x && x.length < 40).slice(0, 150),
    };
  });
  await p2.screenshot({ path: OUT + "/category-modal.png" });
  try { await p2.press("Escape"); } catch (e) {}
} catch (e) {
  res.category = { error: String(e).slice(0, 160) };
}

// B. 商家体验分页
await p1.goto("https://fxg.jinritemai.com/ffa/eco/experience-score?source=fxg-menu");
await p1.waitForTimeout(9000);
res.score = await p1.evaluate(() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  return {
    url: location.href,
    text: (document.body?.innerText || "").slice(0, 4000),
  };
});
await p1.screenshot({ path: OUT + "/experience-score.png" });

// C. 违规管理页
await p1.goto("https://fxg.jinritemai.com/ffa/govern-guarantee/regulation");
await p1.waitForTimeout(8000);
res.violation = await p1.evaluate(() => ({
  url: location.href,
  text: (document.body?.innerText || "").slice(0, 3000),
}));

await fs.writeFile(OUT + "/round-09.json", JSON.stringify(res, null, 2), "utf8");

console.log("=== A 类目弹窗 ===");
if (res.category.error) console.log("ERR:", res.category.error);
else {
  console.log(res.category.text.slice(0, 900));
  console.log("ITEMS:", res.category.items.join(" / ").slice(0, 900));
}
console.log("\n=== B 体验分 ===");
console.log(res.score.url);
console.log(res.score.text.slice(0, 1800));
console.log("\n=== C 违规管理 ===");
console.log(res.violation.url);
console.log(res.violation.text.slice(0, 1200));
