/**
 * 抖店读取器（只读，不发任何写操作）
 * 用法：ego-browser nodejs -e "await import('file:///G:/.../上货引擎/read_products.mjs')"
 * 产物：同目录 out/products.json + out/product_detail.json
 */
import fs from "node:fs/promises";

const BASE = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/上货引擎";
const OUT = BASE + "/out";
await fs.mkdir(OUT, { recursive: true });
const SEL = JSON.parse(await fs.readFile(BASE + "/selectors.json", "utf8"));

const task = await taskSpace("抖店上货引擎");
const page = task.page("p1");

// ---------- 1. 读商品列表 ----------
await page.goto(SEL.urls.goods_list);
await page.waitForTimeout(8000);

const list = await page.evaluate(() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

  // 行容器：优先 tr，退化为含「ID：数字」的块
  let rows = [...document.querySelectorAll("tr")].filter(vis);
  if (rows.length < 2) {
    rows = [...document.querySelectorAll("div,li")]
      .filter(vis)
      .filter((e) => /ID：\d{6,}/.test(e.innerText || "") && (e.innerText || "").length < 800);
  }

  const out = [];
  const seen = new Set();
  for (const r of rows) {
    const txt = r.innerText || "";
    const m = txt.match(/ID：(\d{6,})/);
    if (!m) continue;
    if (seen.has(m[1])) continue;
    seen.add(m[1]);
    const lines = txt.split("\n").map((x) => x.trim()).filter(Boolean);
    const prices = txt.match(/￥[\d.]+/g) || [];
    out.push({
      product_id: m[1],
      title: lines[0] || "",
      price_text: prices.join(" ~ "),
      raw_lines: lines.slice(0, 14),
    });
  }

  const total = (document.body.innerText.match(/共\s*(\d+)\s*条/) || [])[1] || null;
  return { rows: out, total, tabs: [...document.querySelectorAll("[role='tab']")].filter(vis).map((e) => t(e.textContent)) };
});
await fs.writeFile(OUT + "/products.json", JSON.stringify(list, null, 2), "utf8");

// ---------- 2. 读单个商品详情 ----------
let detail = null;
if (list.rows.length) {
  const pid = list.rows[0].product_id;
  // cid 从列表页拿不到，用已知家具类目兜底；失败则退回列表页点击
  const cid = 1000004588;
  await page.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${pid}&cid=${cid}&entrance=edit`);
  await page.waitForTimeout(11000);

  detail = await page.evaluate((PID) => {
    const t = (s) => (s || "").replace(/\s+/g, " ").trim();
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const body = document.body.innerText || "";

    const pick = (ph) => [...document.querySelectorAll(`input[placeholder*="${ph}"]`)].filter(vis).map((e) => e.value);

    // 类目：取「商品类目」后那一行
    const catLine = (body.match(/商品类目\s*\n\s*([^\n]+)/) || [])[1] || "";

    // 已选 radio：抖店用 label + input[type=radio][checked]
    const checkedLabels = [...document.querySelectorAll("label")].filter(vis)
      .filter((l) => { const i = l.querySelector("input[type=radio]"); return i && i.checked; })
      .map((l) => t(l.textContent));

    // SKU：找含「颜色/请输入颜色」的行
    const skuInputs = [...document.querySelectorAll('input[placeholder*="颜色"]')].filter(vis);
    const skus = skuInputs.map((inp) => {
      let box = inp.closest("tr");
      if (!box) { box = inp.parentElement; for (let i = 0; i < 4 && box; i++) box = box.parentElement || box; }
      const lines = (box?.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean);
      return { name: inp.value, lines: lines.slice(0, 10) };
    });

    return {
      url: location.href,
      product_id: PID,
      category_path: t(catLine),
      title: (pick("8-30个汉字")[0] || ""),
      short_title: (pick("建议填写简明准确的标题")[0] || ""),
      detail_image_count: (body.match(/商详图片[（(](\d+)\/(\d+)[)）]/) || []).slice(1),
      price_inputs: pick("价格"),
      stock_inputs: pick("现货库存"),
      merchant_codes: pick("商家编码"),
      weight: (pick("0.001")[0] || ""),
      checked_option_labels: checkedLabels,
      sku_count: skus.length,
      skus: skus.slice(0, 40),
      section_order: (body.match(/(基础信息[\s\S]{0,80})/) || [])[1] || "",
    };
  }, pid);

  await fs.writeFile(OUT + "/product_detail.json", JSON.stringify(detail, null, 2), "utf8");
}

console.log("=== 列表：共", list.total, "件，解析到", list.rows.length, "行 ===");
for (const r of list.rows) console.log(`${r.product_id}  ${r.price_text}  ${r.title.slice(0, 30)}`);
console.log("\n=== 详情 ===");
if (detail) {
  console.log("商品ID:", detail.product_id);
  console.log("类目:", detail.category_path);
  console.log("标题:", detail.title);
  console.log("导购短标题:", detail.short_title);
  console.log("详情图:", detail.detail_image_count.join("/"));
  console.log("价格输入:", detail.price_inputs.length, JSON.stringify(detail.price_inputs.slice(0, 5)));
  console.log("库存输入:", detail.stock_inputs.length, JSON.stringify(detail.stock_inputs.slice(0, 5)));
  console.log("商家编码:", detail.merchant_codes.length);
  console.log("重量:", detail.weight);
  console.log("已选单选:", detail.checked_option_labels.join(" | "));
  console.log("SKU 数:", detail.sku_count);
  for (const s of detail.skus.slice(0, 3)) console.log("  ", s.name, "=>", s.lines.slice(0, 6).join(" / "));
} else {
  console.log("(未取到详情)");
}
console.log("\n落盘:", OUT);
