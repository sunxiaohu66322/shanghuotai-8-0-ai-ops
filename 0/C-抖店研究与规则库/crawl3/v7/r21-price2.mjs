// 第 21 轮：按 rowspan（y 坐标）把颜色映射到 6 行 SKU，填价格与库存
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

const PRICE = {
  "胡桃色|长40": { p: 329, s: 9 },
  "胡桃色|长60": { p: 426, s: 0 },
  "白色|长40": { p: 329, s: 10 },
  "白色|长60": { p: 426, s: 10 },
  "黑色|长40": { p: 329, s: 0 },   // 源数据无此组合：同尺寸价 + 库存0
  "黑色|长60": { p: 426, s: 10 },
};

const res = await page.evaluate((PRICE) => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const setV = (el, v) => {
    el.focus();
    const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    s.call(el, String(v));
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  };
  const all = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.h > 25 && o.h < 200 && o.w > 500);

  // 颜色单元格：cells[0] 文本是颜色
  const colorCells = all.filter((o) => /^(胡桃色|白色|黑色)$/.test(((o.e.children[0] || {}).innerText || "").trim()))
    .map((o) => ({ color: (o.e.children[0].innerText || "").trim(), y: o.y }))
    .sort((a, b) => a.y - b.y);

  // 数据行：含 ￥ 的行
  const dataRows = all.filter((o) => /￥/.test(o.e.innerText || "")).sort((a, b) => a.y - b.y);

  const out = [];
  for (const r of dataRows) {
    const cells = [...r.e.children];
    const size = (cells[1].innerText || "").trim();
    // 该行的颜色 = y 不大于该行的最后一个颜色单元格
    const col = [...colorCells].reverse().find((c) => c.y <= r.y + 2);
    if (!col) { out.push({ skip: "no-color", y: r.y, size }); continue; }
    const key = `${col.color}|${size}`;
    const want = PRICE[key];
    if (!want) { out.push({ miss: key, y: r.y }); continue; }
    const priceIn = [...cells[2].querySelectorAll("input")].filter(vv)[0];
    const stockIn = [...cells[3].querySelectorAll("input")].filter(vv)[0];
    if (priceIn) setV(priceIn, want.p);
    if (stockIn) setV(stockIn, want.s);
    out.push({ key, y: r.y, price: want.p, stock: want.s, ok: !!(priceIn && stockIn) });
  }
  return { colorCells, count: dataRows.length, out };
}, PRICE);

log("① 颜色行合并点:", JSON.stringify(res.colorCells));
log("② 填写:", JSON.stringify(res.out, null, 1));
await page.waitForTimeout(1800);

// 回读：每个数据行的 价格 input / 库存 input 的 value
const back = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const all = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.h > 25 && o.h < 200 && o.w > 500);
  const colors = all.filter((o) => /^(胡桃色|白色|黑色)$/.test(((o.e.children[0] || {}).innerText || "").trim()))
    .map((o) => ({ color: (o.e.children[0].innerText || "").trim(), y: o.y })).sort((a, b) => a.y - b.y);
  return all.filter((o) => /￥/.test(o.e.innerText || "")).sort((a, b) => a.y - b.y).map((r) => {
    const cells = [...r.e.children];
    const col = [...colors].reverse().find((c) => c.y <= r.y + 2);
    const pi = [...cells[2].querySelectorAll("input")][0];
    const si = [...cells[3].querySelectorAll("input")][0];
    return `${col ? col.color : "?"}×${(cells[1].innerText || "").trim()} → 价=${pi ? pi.value || "∅" : "?"} 库存=${si ? si.value || "∅" : "?"}`;
  });
});
log("③ 回读:");
for (const b of back) log("   ", b);
await fs.writeFile(`${ROOT}/v7/r21-price.json`, JSON.stringify({ res, back }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r21.png` });
log("done");
