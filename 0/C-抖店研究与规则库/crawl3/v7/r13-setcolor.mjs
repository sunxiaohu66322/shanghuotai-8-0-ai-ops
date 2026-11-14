// 第 13 轮：第一组规格类型 材质 → 颜色，并依次加 3 个规格值
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① 现状
const S0 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false };
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
  const b = [...block.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  return {
    found: true,
    text: (block.innerText || "").replace(/\n+/g, " | ").slice(0, 200),
    type: ([...block.querySelectorAll("[class*=select-selection-item]")].filter(vv)[0] || {}).innerText || "",
    vals: [...block.querySelectorAll("input[type=text]")].filter(vv).map((e) => ({ ...box(e), ph: e.placeholder, val: e.value })),
    addBtn: b ? { ...box(b), t: b.innerText } : null,
  };
});
log("① 现状:", JSON.stringify(S0));

// ② 点开类型下拉
const openDrop = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false };
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
  const item = [...block.querySelectorAll("[class*=select-selection-item]")].filter(vv)[0];
  if (!item) return { found: false, why: "no-item" };
  let wrap = item;
  for (let i = 0; i < 6 && wrap.parentElement; i++) {
    wrap = wrap.parentElement;
    if (/\becom-g-select\b/.test(String(wrap.className)) && !/selection-item/.test(String(wrap.className))) break;
  }
  wrap.scrollIntoView({ block: "center" });
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) wrap.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, cur: (item.innerText || "").trim(), at: box(wrap) };
});
log("② 点开下拉:", JSON.stringify(openDrop));
await page.waitForTimeout(1500);

// ③ 选「颜色」
const pickColor = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
    .map((e) => ({ e, ...box(e), cls: String(e.className) }))
    .filter((o) => o.w > 80 && o.h > 30 && /spec_detail/.test(o.cls));
  if (!drops.length) return { found: false, why: "no-spec-dropdown" };
  const drop = drops[0];
  const opts = [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).filter((e) => (e.innerText || "").trim() === "颜色");
  if (!opts.length) {
    return { found: false, items: [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 40) };
  }
  const el = opts[opts.length - 1];
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, picked: (el.innerText || "").trim(), at: box(el) };
});
log("③ 选「颜色」:", JSON.stringify(pickColor));
await page.waitForTimeout(2000);

// ④ 改类型后状态
const S1 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false };
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
  return {
    type: ([...block.querySelectorAll("[class*=select-selection-item]")].filter(vv)[0] || {}).innerText || "",
    vals: [...block.querySelectorAll("input[type=text]")].filter(vv).map((e) => ({ ...box(e), ph: e.placeholder, val: e.value })),
    text: (block.innerText || "").replace(/\n+/g, " | ").slice(0, 200),
  };
});
log("④ 改类型后:", JSON.stringify(S1));
await fs.writeFile(`${ROOT}/v7/r13-S1.json`, JSON.stringify({ S0, openDrop, pickColor, S1 }, null, 2), "utf8");

// ⑤ 依次加 3 个值
const VALUES = ["胡桃色", "白色", "黑色"];
for (const v of VALUES) {
  const target = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
    if (!lab) return null;
    let block = lab;
    for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
    const ins = [...block.querySelectorAll("input[type=text]")].filter(vv).filter((e) => !e.value);
    const t = ins[ins.length - 1];
    if (!t) return null;
    t.scrollIntoView({ block: "center" });
    t.focus();
    return { at: box(t), ph: t.placeholder };
  });
  if (!target) { log(`   ✗ 「${v}」无空输入框`); continue; }
  await page.keyboard.type(v, { delay: 80 });
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1600);
  const now = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
    if (!lab) return null;
    let block = lab;
    for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
    return [...block.querySelectorAll("input[type=text]")].filter(vv).map((e) => `${e.placeholder}=${e.value || "∅"}`);
  });
  log(`   + 「${v}」→`, JSON.stringify(now));
}

// ⑥ 结果
const S2 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false };
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
  const b = [...block.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  return {
    type: ([...block.querySelectorAll("[class*=select-selection-item]")].filter(vv)[0] || {}).innerText || "",
    vals: [...block.querySelectorAll("input[type=text]")].filter(vv).map((e) => ({ ...box(e), ph: e.placeholder, val: e.value })),
    text: (block.innerText || "").replace(/\n+/g, " | ").slice(0, 300),
    addBtn: b ? { ...box(b), t: b.innerText } : null,
  };
});
log("⑥ 三值后:", JSON.stringify(S2));

const rows = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  return [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 70)).filter((x) => x && x.length > 2).slice(0, 20);
});
log("⑥ SKU 行:", JSON.stringify(rows));
await fs.writeFile(`${ROOT}/v7/r13-S2.json`, JSON.stringify({ S2, rows }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r13.png` });
log("done");
