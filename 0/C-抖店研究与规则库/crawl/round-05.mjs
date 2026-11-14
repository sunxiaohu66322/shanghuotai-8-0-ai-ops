import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
const task = await taskSpace("抖店后台结构采集");
const page = task.page("p1");

// ---------- A. 创建页 → 返回旧版（传统表单） ----------
await page.goto("https://fxg.jinritemai.com/ffa/g/create");
await page.waitForTimeout(7000);
let oldDump = null;
try {
  await page.click('text="返回旧版"', { timeout: 5000 });
  await page.waitForTimeout(9000);
  oldDump = await page.evaluate(() => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const labels = [...document.querySelectorAll("label")].filter(vis)
      .map((l) => ({ t: t(l.textContent).slice(0, 30), req: /[*＊]/.test(l.textContent || "") }))
      .filter((x) => x.t);
    const fields = [...document.querySelectorAll("input,textarea,select")].filter(vis)
      .map((e) => ({ tag: e.tagName.toLowerCase(), type: e.getAttribute("type") || "", ph: (e.getAttribute("placeholder") || "").slice(0, 36), max: e.getAttribute("maxlength") || "" }))
      .filter((x) => x.ph).slice(0, 60);
    const sections = [...new Set([...document.querySelectorAll("h1,h2,h3,h4,[class*='section'] [class*='title']")]
      .filter(vis).map((e) => t(e.textContent)).filter((x) => x && x.length <= 20))].slice(0, 40);
    const buttons = [...new Set([...document.querySelectorAll("button,[role='button']")].filter(vis)
      .map((b) => t(b.textContent)).filter((x) => x && x.length <= 14))].slice(0, 60);
    return { url: location.href, text: (document.body?.innerText || "").slice(0, 3000), labels, fields, sections, buttons };
  });
  await page.screenshot({ path: OUT + "/create-old.png" });
} catch (e) {
  oldDump = { error: String(e).slice(0, 200) };
}

// ---------- B. 商品列表 → 更多操作 → Excel导入 ----------
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(7000);
let impDump = null;
try {
  await page.click('text="更多操作"', { timeout: 4000 });
  await page.waitForTimeout(1200);
  await page.click('text="Excel导入"', { timeout: 4000 });
  await page.waitForTimeout(6000);
  impDump = await page.evaluate(() => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const links = [...document.querySelectorAll("a[href]")].filter(vis)
      .map((a) => ({ t: t(a.textContent).slice(0, 30), h: a.getAttribute("href") }))
      .filter((x) => x.t).slice(0, 40);
    const buttons = [...new Set([...document.querySelectorAll("button,[role='button']")].filter(vis)
      .map((b) => t(b.textContent)).filter((x) => x && x.length <= 16))].slice(0, 60);
    const fileInputs = [...document.querySelectorAll("input[type=file]")].length;
    return { url: location.href, text: (document.body?.innerText || "").slice(0, 2500), links, buttons, fileInputs };
  });
  await page.screenshot({ path: OUT + "/excel-import.png" });
} catch (e) {
  impDump = { error: String(e).slice(0, 200) };
}

await fs.writeFile(OUT + "/round-05.json", JSON.stringify({ oldDump, impDump }, null, 2), "utf8");

console.log("=== A 旧版创建页 ===");
if (oldDump.error) console.log("ERR:", oldDump.error);
else {
  console.log("URL:", oldDump.url);
  console.log("TEXT:", oldDump.text.slice(0, 1400));
  console.log("LABELS:", oldDump.labels.map((l) => (l.req ? "*" : "") + l.t).join(" / "));
  console.log("FIELDS:", oldDump.fields.map((f) => `${f.tag}:${f.ph}`).join(" / "));
  console.log("SECTIONS:", oldDump.sections.join(" | "));
  console.log("BUTTONS:", oldDump.buttons.join(" | "));
}
console.log("\n=== B Excel导入 ===");
if (impDump.error) console.log("ERR:", impDump.error);
else {
  console.log("URL:", impDump.url, "fileInputs:", impDump.fileInputs);
  console.log("TEXT:", impDump.text);
  console.log("LINKS:", JSON.stringify(impDump.links));
  console.log("BUTTONS:", impDump.buttons.join(" | "));
}
