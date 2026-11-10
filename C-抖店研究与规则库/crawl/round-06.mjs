import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
const task = await taskSpace("抖店后台结构采集");
const page = task.page("p1");

await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(7000);

// 点第一个商品的「编辑」
let clicked = null;
try {
  await page.click('text="编辑"', { timeout: 5000 });
  clicked = "编辑";
} catch (e) {
  clicked = "FAIL:" + String(e).slice(0, 100);
}
await page.waitForTimeout(11000);

const dump = await page.evaluate(() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const labels = [...document.querySelectorAll("label")].filter(vis)
    .map((l) => ({ t: t(l.textContent).slice(0, 28), req: /[*＊]/.test(l.textContent || "") }))
    .filter((x) => x.t);
  const fields = [...document.querySelectorAll("input,textarea,select")].filter(vis)
    .map((e) => ({ tag: e.tagName.toLowerCase(), type: e.getAttribute("type") || "", ph: (e.getAttribute("placeholder") || "").slice(0, 30), val: (e.value || "").slice(0, 20), max: e.getAttribute("maxlength") || "" }))
    .filter((x) => x.ph || x.val);
  const buttons = [...new Set([...document.querySelectorAll("button,[role='button']")].filter(vis)
    .map((b) => t(b.textContent)).filter((x) => x && x.length <= 16))].slice(0, 80);
  const apis = [...new Set(performance.getEntriesByType("resource").map((e) => e.name)
    .filter((n) => /\/(api|pc\/api)\//i.test(n))
    .map((n) => n.replace(/^https?:\/\/[^/]+/, "").split("?")[0]))];
  return {
    url: location.href,
    title: document.title,
    text: (document.body?.innerText || "").slice(0, 6000),
    labels, fields, buttons, apis,
  };
});

await page.screenshot({ path: OUT + "/edit-page.png" });
await fs.writeFile(OUT + "/round-06-edit.json", JSON.stringify({ clicked, dump }, null, 2), "utf8");

console.log("click:", clicked);
console.log("URL:", dump.url, "| TITLE:", dump.title);
console.log("=== 正文（前 4000）===");
console.log(dump.text.slice(0, 4000));
console.log("\n=== LABELS (" + dump.labels.length + ") ===");
console.log(dump.labels.map((l) => (l.req ? "*" : "") + l.t).join(" / "));
console.log("\n=== FIELDS (" + dump.fields.length + ") ===");
console.log(dump.fields.slice(0, 70).map((f) => `${f.tag}:${f.ph}=${f.val}`).join(" | "));
console.log("\n=== BUTTONS ===");
console.log(dump.buttons.join(" | "));
console.log("\n=== APIS ===");
console.log(dump.apis.join("\n"));
