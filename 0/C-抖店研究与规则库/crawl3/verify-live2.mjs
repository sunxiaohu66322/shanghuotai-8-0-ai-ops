/**
 * 核对 v2：抖店线上 vs 上货台仓库
 * 用法：ITEMS="1416,1419,..." BATCH=B12
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const BATCH = process.env.BATCH || "B12";
const ITEMS = (process.env.ITEMS || "").split(",").filter(Boolean);

const log = (...a) => console.log(...a);
const task = await taskSpace("抖店批量上货");
const page = task.page("p1");

const norm = (s) => String(s || "").replace(/[\s\p{P}]/gu, "").slice(0, 8);

await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(9000);
const listRows = await page.evaluate(() => [...document.querySelectorAll("tr")]
  .map((tr) => (tr.innerText || "").replace(/\s+/g, " ").trim())
  .filter((t) => /ID[：:]\s*\d{10,}/.test(t))
  .map((t) => ({
    id: (t.match(/ID[：:]\s*(\d{10,})/) || [])[1],
    title: t.split(" ID")[0].slice(0, 40),
    price: (t.match(/￥[\d.]+/g) || []).join("~"),
    stock: (t.match(/(\d+)\s+0\s+暂无评价/) || [])[1] || "",
    status: (t.match(/(售卖中|审核中|已售罄|已下架|草稿)/g) || []).join("/"),
  })));
log(`列表商品 ${listRows.length} 件`);

const out = [];
for (const item of ITEMS) {
  let plan;
  try { plan = JSON.parse(await fs.readFile(path.join(ROOT, `plan-${item}.json`), "utf8")); }
  catch { out.push({ item, ok: false, why: "no-plan" }); continue; }
  const key = norm(plan.title);
  const hit = listRows.find((r) => norm(r.title) === key);
  if (!hit) { out.push({ item, ok: false, why: "列表无此商品", key }); continue; }

  await page.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${hit.id}&cid=1000004518`);
  await page.waitForTimeout(9000);
  const tabs = await page.evaluate(() => [...document.querySelectorAll("div")]
    .filter((e) => e.offsetParent !== null && (e.innerText || "").trim() === "价格库存")
    .map((e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; }));
  if (tabs.length) { await page.mouse.click(tabs[0].x, tabs[0].y); await page.waitForTimeout(4500); }
  const rows = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    return [...document.querySelectorAll("tr")].filter(vv).map((tr) => {
      const t = (tr.innerText || "").replace(/\s+/g, " ").trim();
      if (!/[￥¥]/.test(t)) return null;
      const ins = [...tr.querySelectorAll("input")].filter(vv);
      const price = (ins.find((e) => (e.placeholder || "") === "请输入") || {}).value || "";
      const st = (t.match(/￥\s*(\d+)/) || [])[1] || "";
      return { label: t.split(" ")[0].slice(0, 16), price: Number(price || 0), stock: Number(st || 0), imgs: tr.querySelectorAll("img").length };
    }).filter(Boolean);
  });
  const want = plan.skus.map((s) => ({ name: s.name.slice(0, 16), price: s.price, stock: s.stock }));
  const gotN = rows.length, wantN = want.length;
  const priceOk = gotN === wantN && want.every((w, i) => Math.abs(rows[i].price - w.price) < 1);
  const stockOk = gotN === wantN && want.every((w, i) => rows[i].stock === w.stock);
  const imgOk = rows.length > 0 && rows.every((r) => r.imgs > 0);
  out.push({ item, liveId: hit.id, status: hit.status, liveStock: hit.stock, gotN, wantN, priceOk, stockOk, imgOk, rows, want });
  log(`${item} ${hit.id} [${hit.status}] SKU ${gotN}/${wantN} 价${priceOk ? "✓" : "✗"} 存${stockOk ? "✓" : "✗"} 图${imgOk ? "✓" : "✗"}`);
}

await fs.writeFile(path.join(ROOT, `verify2-${BATCH}.json`), JSON.stringify(out, null, 1), "utf8");
const pass = out.filter((o) => o.gotN === o.wantN && o.priceOk && o.stockOk && o.imgOk).length;
log(`\n合格 ${pass}/${out.length}`);
log("VERIFY2_DONE");
