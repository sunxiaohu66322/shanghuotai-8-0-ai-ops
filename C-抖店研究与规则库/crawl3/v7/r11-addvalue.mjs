// 第 11 轮：摸清「规格类型下拉 + 规格值添加 + 加规格组」的交互
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId, "| url:", (await page.url()).slice(0, 80));

/** 只看得见且在视口内的 */
const VV = `(e)=>{const r=e.getBoundingClientRect();return e.offsetParent!==null&&r.y>=0&&r.y<window.innerHeight&&r.width>0&&r.height>0;}`;

// ---------- A. 规格区当前结构（细到每个元素） ----------
const A = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.y >= 0 && r.y < window.innerHeight && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  // 规格区 = y 320..560 的容器
  const all = [...document.querySelectorAll("*")].filter((e) => { const b = box(e); return vv(e) && b.y >= 325 && b.y <= 560 && b.x >= 580; });
  const rows = all.map((e) => ({
    tag: e.tagName,
    ...box(e),
    cls: String(e.className || "").slice(0, 55),
    ph: e.placeholder || "",
    val: (e.value || "").slice(0, 16),
    t: (e.innerText || "").trim().slice(0, 20),
    kids: e.children.length,
  })).filter((o) => o.h <= 120 && o.w <= 900);
  return rows.slice(0, 60);
});
log("A 规格区元素:");
for (const r of A) log(`   ${r.tag} ${r.cls.padEnd(28)} @${r.x},${r.y} ${r.w}x${r.h} ${r.ph ? "ph=" + r.ph : ""} ${r.val ? "val=" + r.val : ""} ${r.t ? "t=" + r.t : ""}`);

// ---------- B. 点开类型下拉，dump 选项 ----------
const B = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.y >= 0 && r.y < window.innerHeight && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const el = [...document.querySelectorAll("[class*=select-selection-item]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.x >= 580 && o.x <= 900 && o.w > 60 && o.w < 220 && o.h >= 24 && o.h <= 40)[0];
  if (!el) return { found: false };
  const sel = el.e.closest("[class*=ecom-g-select]") || el.e.parentElement;
  sel.scrollIntoView({ block: "center" });
  const b = box(sel);
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    sel.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  }
  return { found: true, cur: (el.e.innerText || "").trim(), sel: b, cls: String(sel.className).slice(0, 60) };
});
log("B 点开下拉:", JSON.stringify(B));
await page.waitForTimeout(1500);

const B2 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const pops = [...document.querySelectorAll("[class*=dropdown],[class*=popup],[class*=menu],[role=listbox]")]
    .filter(vv).map((e) => ({ ...box(e), cls: String(e.className).slice(0, 50), txt: (e.innerText || "").trim() }))
    .filter((o) => o.h > 40 && o.w > 80 && o.txt);
  return pops.slice(0, 5);
});
log("B2 下拉面板:", JSON.stringify(B2, null, 1));
await fs.writeFile(`${ROOT}/v7/r11-A.json`, JSON.stringify({ A, B, B2 }, null, 2), "utf8");

// 关掉下拉（Esc）
await page.keyboard.press("Escape");
await page.waitForTimeout(600);

// ---------- C. 在空规格值输入框里加一个值「胡桃色」 ----------
const C = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.y >= 0 && r.y < window.innerHeight; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => /请输入材质|请输入/.test(o.e.placeholder || "") && o.x >= 580)
    .sort((a, b) => a.x - b.x);
  if (!ins.length) return { found: false };
  const target = ins.find((o) => !o.e.value) || ins[ins.length - 1];
  target.e.scrollIntoView({ block: "center" });
  target.e.focus();
  return { found: true, n: ins.length, at: { x: target.x, y: target.y, w: target.w, h: target.h }, ph: target.e.placeholder, all: ins.map((o) => ({ x: o.x, v: o.e.value })) };
});
log("C 目标输入框:", JSON.stringify(C));

if (C.found) {
  await page.keyboard.type("胡桃色", { delay: 90 });
  await page.waitForTimeout(500);
  // 先试 Enter
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1500);
  const C2 = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.y >= 0 && r.y < window.innerHeight; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    // 规格值 chip：y 在 425..470 的可视元素
    const chips = [...document.querySelectorAll("span,div,input")].filter(vv)
      .map((e) => ({ e, ...box(e) }))
      .filter((o) => o.y >= 420 && o.y <= 475 && o.x >= 580 && o.w > 8 && o.h > 8 && o.h < 45)
      .map((o) => ({ tag: o.e.tagName, ...box(o.e), cls: String(o.e.className).slice(0, 40), val: (o.e.value || "").slice(0, 14), t: (o.e.innerText || "").trim().slice(0, 14) }));
    // SKU 表行数
    const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tr")].filter(vv)
      .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 60)).filter((x) => x);
    return { chips, trs };
  });
  log("C2 加值后:", JSON.stringify(C2.chips, null, 1));
  log("C2 SKU行:", JSON.stringify(C2.trs));
  await fs.writeFile(`${ROOT}/v7/r11-C.json`, JSON.stringify(C2, null, 2), "utf8");
}

await page.screenshot({ path: `${ROOT}/v7/shot-r11.png` });
log("done");
