/**
 * 探针：打开一件商品的编辑页，读「价格库存」tab 的 SKU 行（值/价格/库存）
 * 用法：batch 参数靠 PLAN_ID 环境无法传 → 直接改下面 LIVE 常量
 */
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const LIVE = process.env.LIVE_ID || "3851698328731517051";
const PLAN = process.env.PLAN_ID || "1391";

const log = (...a) => console.log(...a);
const task = await taskSpace("抖店批量上货");
const page = task.page("p1");
await page.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${LIVE}&cid=1000004518`);
await page.waitForTimeout(9000);

const tabNames = await page.evaluate(() => [...document.querySelectorAll("[role=tab],.ecom-g-tabs-tab,div")]
  .filter((e) => e.offsetParent !== null && /^(基础信息|图文信息|属性信息|价格库存)$/.test((e.innerText || "").trim()))
  .map((e) => { const r = e.getBoundingClientRect(); return { t: e.innerText.trim(), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; }));
log("TABS", JSON.stringify(tabNames));

const pt = tabNames.find((t) => t.t === "价格库存");
if (pt) { await page.mouse.click(pt.x, pt.y); await page.waitForTimeout(4500); }

const read = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const rows = [...document.querySelectorAll("tr")].filter(vv).map((tr) => {
    const t = (tr.innerText || "").replace(/\s+/g, " ").trim();
    if (!/[￥¥]/.test(t)) return null;
    const ins = [...tr.querySelectorAll("input")].filter(vv);
    return {
      text: t.slice(0, 80),
      vals: ins.map((e) => ({ ph: (e.placeholder || "").slice(0, 14), v: e.value || "" })),
      imgs: tr.querySelectorAll("img").length,
    };
  }).filter(Boolean);
  const body = (document.body.innerText || "");
  const i = body.indexOf("价格库存");
  return { rows, bodySeg: body.slice(Math.max(0, i), i + 600) };
});

log("ROWS", JSON.stringify(read.rows, null, 1));
log("SEG", JSON.stringify(read.bodySeg));
await page.screenshot({ path: `${ROOT}/shots/probe-sku-${LIVE}.png`, fullPage: false }).catch(() => {});
await fs.writeFile(`${ROOT}/probe-sku-${LIVE}.json`, JSON.stringify(read, null, 1), "utf8");
console.log("PROBE_DONE");
