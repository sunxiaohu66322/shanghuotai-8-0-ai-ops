/**
 * 核对：抖店线上商品 vs 上货台仓库（plan + v3.db）
 * 步骤：列表页抓全部商品 → 按标题前缀匹配 → 逐个打开编辑页读 SKU 行 → 对比
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const BATCH = process.env.BATCH || "B10";
const ITEMS = (process.env.ITEMS || "1390,1393,1409,1406,1404").split(",");

const log = (...a) => console.log(...a);
const task = await taskSpace("抖店批量上货");
const page = task.page("p1");

// ---------- 1) 列表页：抓全部商品 ----------
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(8000);
const listRows = await page.evaluate(() => {
  const out = [];
  for (const tr of document.querySelectorAll("tr")) {
    const t = (tr.innerText || "").replace(/\s+/g, " ");
    const m = t.match(/ID[：:]\s*(\d{10,})/);
    if (!m) continue;
    out.push({
      id: m[1],
      title: t.split(" ")[0].slice(0, 40),
      price: (t.match(/￥[\d.]+/) || [])[0] || "",
      status: (t.match(/(售卖中|审核中|已售罄|已下架|草稿|待审核|违规)/g) || []).join("/"),
    });
  }
  return out;
});
log(`列表商品数: ${listRows.length}`);

// ---------- 2) 匹配 ----------
const report = [];
for (const item of ITEMS) {
  let plan;
  try { plan = JSON.parse(await fs.readFile(path.join(ROOT, `plan-${item}.json`), "utf8")); }
  catch { report.push({ item, err: "no-plan" }); continue; }
  const head = Array.from(plan.title || "").slice(0, 12).join("");
  const hit = listRows.filter((r) => (r.title || "").replace(/\s/g, "").includes(head.replace(/\s/g, "").slice(0, 8)));
  report.push({ item, head, planSkus: plan.skus.length, planPrices: plan.skus.map((s) => s.price), listHits: hit });
}
log(JSON.stringify(report, null, 1));

// ---------- 3) 逐个打开编辑页读 SKU ----------
const verify = [];
for (const r of report) {
  if (!r.listHits || !r.listHits.length) { verify.push({ item: r.item, ok: false, why: "列表无此商品" }); continue; }
  const live = r.listHits[0];
  let plan = JSON.parse(await fs.readFile(path.join(ROOT, `plan-${r.item}.json`), "utf8"));
  await page.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${live.id}&cid=1000004518`);
  await page.waitForTimeout(9000);
  const tabs = await page.evaluate(() => [...document.querySelectorAll("div")]
    .filter((e) => e.offsetParent !== null && /^(价格库存)$/.test((e.innerText || "").trim()))
    .map((e) => { const b = e.getBoundingClientRect(); return { x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2) }; }));
  if (tabs.length) { await page.mouse.click(tabs[0].x, tabs[0].y); await page.waitForTimeout(4500); }
  const rows = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    return [...document.querySelectorAll("tr")].filter(vv).map((tr) => {
      const t = (tr.innerText || "").replace(/\s+/g, " ").trim();
      if (!/[￥¥]/.test(t)) return null;
      const ins = [...tr.querySelectorAll("input")].filter(vv);
      const price = (ins.find((e) => (e.placeholder || "") === "请输入") || {}).value || "";
      const stockTxt = (t.match(/￥\s*(\d+)/) || [])[1] || "";
      return { label: t.split(" ")[0].slice(0, 20), price, stockTxt, imgs: tr.querySelectorAll("img").length };
    }).filter(Boolean);
  });
  const want = plan.skus.map((s) => ({ name: s.name, price: s.price, stock: s.stock }));
  const got = rows.map((x) => ({ name: x.label, price: Number(x.price || 0), stock: Number(x.stockTxt || 0), imgs: x.imgs }));
  const sameCount = got.length === want.length;
  const priceOk = sameCount && want.every((w, i) => Math.abs((got[i].price || 0) - (w.price || 0)) < 1);
  const imgOk = got.length > 0 && got.every((g) => g.imgs > 0);
  verify.push({ item: r.item, liveId: live.id, status: live.status, want, got, sameCount, priceOk, imgOk });
  log(`${r.item}(${live.id}) SKU ${got.length}/${want.length} 价格${priceOk ? "✓" : "✗"} 图${imgOk ? "✓" : "✗"}`);
}

await fs.writeFile(path.join(ROOT, `verify-${BATCH}.json`), JSON.stringify({ listRows: listRows.length, report, verify }, null, 1), "utf8");
log("VERIFY_DONE");
