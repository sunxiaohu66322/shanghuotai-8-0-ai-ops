// 第 20 轮：按「颜色×尺寸」组合填 6 行 SKU 的价格与库存
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① dump SKU 表结构（行 × 列，含输入框坐标）
const dump = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const rows = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.h > 25 && o.h < 200 && o.w > 500);
  return rows.map((o) => ({
    y: o.y, h: o.h,
    cells: [...o.e.children].map((c) => ({
      ...box(c),
      t: (c.innerText || "").replace(/\n/g, "/").slice(0, 24),
      ins: [...c.querySelectorAll("input")].filter(vv).map((i) => ({ ...box(i), ph: i.placeholder || "", val: i.value, ro: i.readOnly })),
    })),
  }));
});
log("① SKU 行数:", dump.length);
for (const [i, r] of dump.entries()) {
  log(`  行${i} y=${r.y} cells=${r.cells.length}: ` + r.cells.map((c, j) => `[${j}]"${c.t}"${c.ins.length ? "{" + c.ins.map((x) => `${x.val || "∅"}@${x.x},${x.y}`).join(" ") + "}" : ""}`).join(" | "));
}
await fs.writeFile(`${ROOT}/v7/r20-rows.json`, JSON.stringify(dump, null, 2), "utf8");

// ② 组合价格表（来自 plan-1436.json）
const PRICE = {
  "胡桃色|长40": { p: 329, s: 9 },
  "白色|长40": { p: 329, s: 10 },
  "胡桃色|长60": { p: 426, s: 0 },
  "白色|长60": { p: 426, s: 10 },
  "黑色|长60": { p: 426, s: 10 },
  "黑色|长40": { p: 329, s: 0 },   // 源数据没有这个组合：沿用长40价、库存 0
};

// ③ 逐行填写
const filled = await page.evaluate((PRICE) => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const setV = (el, v) => {
    el.focus();
    const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    s.call(el, String(v));
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  };
  const rows = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.h > 25 && o.h < 200 && o.w > 500);

  const out = [];
  for (const o of rows) {
    const cells = [...o.e.children];
    if (cells.length < 4) continue;
    const c0 = (cells[0].innerText || "").trim();
    const c1 = (cells[1].innerText || "").trim();
    // 第一列要是颜色值、第二列要是尺寸值
    if (!/胡桃色|白色|黑色/.test(c0)) { out.push({ skip: c0.slice(0, 20) }); continue; }
    if (!/长40|长60/.test(c1)) { out.push({ skip: c0 + "/" + c1 }); continue; }
    const key = `${c0}|${c1}`;
    const want = PRICE[key];
    if (!want) { out.push({ miss: key }); continue; }
    // 价格列：找 cells 里的「￥」那一列
    const priceCell = cells.find((c) => /￥/.test(c.innerText || ""));
    const stockCell = cells.find((c) => /-/.test(c.innerText || "") && c.querySelectorAll("input").length === 1 && c !== priceCell && !/SKU/i.test(c.innerText || ""));
    const pi = priceCell ? [...priceCell.querySelectorAll("input")].filter(vv)[0] : null;
    // 库存列：按顺序取第 3 个有 input 的 cell
    const inputCells = cells.filter((c) => c.querySelectorAll("input").length > 0);
    let si = null;
    for (const c of inputCells) {
      const i = [...c.querySelectorAll("input")].filter(vv)[0];
      if (i && i !== pi && !/SKU/i.test(String(i.placeholder))) { si = i; break; }
    }
    if (pi) setV(pi, want.p);
    if (si) setV(si, want.s);
    out.push({ key, price: pi ? want.p : null, stock: si ? want.s : null, priceAt: pi ? box(pi) : null, stockAt: si ? box(si) : null });
  }
  return out;
}, PRICE);
for (const f of filled) log("③", JSON.stringify(f));
await page.waitForTimeout(1500);

// ④ 回读校验
const back = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  return [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").replace(/\t/g, "").slice(0, 90))
    .filter((x) => /胡桃色|白色|黑色/.test(x));
});
log("④ 回读:");
for (const r of back) log("   ", r);
await fs.writeFile(`${ROOT}/v7/r20-filled.json`, JSON.stringify({ filled, back }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r20.png` });
log("done");
