// 第 14 轮：① 删掉「实木橡胶木」 ② 加第二组规格「尺寸」 ③ 加 长40/长60 ④ 验证 SKU 是否 3×2=6
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

/** 全页读规格区快照：所有规格值输入框（按 y,x 排序）+ 添加规格类型按钮 + SKU 行 */
const snap = async (tag) => {
  const s = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
      .filter((e) => /请输入/.test(e.placeholder || ""))
      .map((e) => ({ ...box(e), ph: e.placeholder, val: e.value }))
      .sort((a, b) => a.y - b.y || a.x - b.x);
    const addBtn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
    const del = [...document.querySelectorAll("[class*=delete],[class*=Delete]")].filter(vv)
      .map((e) => ({ ...box(e), cls: String(e.className).slice(0, 50) }))
      .filter((o) => o.w >= 8 && o.w <= 24 && o.h >= 8 && o.h <= 26);
    const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
      .map((e) => ({ ...box(e), t: (e.innerText || "").replace(/\n/g, "|").slice(0, 60) }))
      .filter((o) => o.h > 20 && o.h < 200);
    return { ins, addBtn: addBtn ? { ...box(addBtn), t: addBtn.innerText } : null, del, rowCount: trs.length, trs: trs.map((o) => o.t).slice(0, 12) };
  });
  log(`【${tag}】输入框 ${s.ins.length}: ${JSON.stringify(s.ins.map((o) => `${o.val || "∅"}@${o.x},${o.y}`))}`);
  log(`【${tag}】添加按钮: ${JSON.stringify(s.addBtn)} | 删除图标 ${s.del.length}: ${JSON.stringify(s.del.map((o) => `${o.x},${o.y}`))} | SKU行 ${s.rowCount}: ${JSON.stringify(s.trs)}`);
  return s;
};

await snap("起始");

// ---------- ① 删掉「实木橡胶木」 ----------
const delR = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const tgt = [...document.querySelectorAll("input[type=text]")].filter(vv).find((e) => /请输入/.test(e.placeholder || "") && e.value === "实木橡胶木");
  if (!tgt) return { found: false };
  tgt.scrollIntoView({ block: "center" });
  const tb = box(tgt);
  // 同一行（y 差 < 20）、在其右侧 40px 内的删除图标
  const dels = [...document.querySelectorAll("[class*=delete],[class*=Delete]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => Math.abs(o.y + o.h / 2 - (tb.y + tb.h / 2)) < 22 && o.x > tb.x + tb.w - 60 && o.x < tb.x + tb.w + 80 && o.w < 30);
  if (!dels.length) return { found: false, why: "no-del-icon", tb, allDel: [...document.querySelectorAll("[class*=delete],[class*=Delete]")].filter(vv).map((e) => box(e)) };
  const d = dels[0];
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) d.e.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, delAt: { x: d.x, y: d.y }, target: "实木橡胶木" };
});
log("① 删值:", JSON.stringify(delR));
await page.waitForTimeout(1800);
await snap("删除后");

// ---------- ② 加第二组规格 ----------
const addG = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false };
  btn.scrollIntoView({ block: "center" });
  const b = box(btn);
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) btn.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, text: btn.innerText, at: b };
});
log("② 点「添加规格类型」:", JSON.stringify(addG));
await page.waitForTimeout(2500);
const afterAdd = await snap("加组后");

// ---------- ③ 第二组选「尺寸」 ----------
const setSecond = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  // 规格类型下拉有几个？取第二个
  const items = [...document.querySelectorAll("[class*=select-selection-item]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.w > 60 && o.w < 220 && o.h >= 24 && o.h <= 40 && o.y > 300)
    .sort((a, b) => a.y - b.y || a.x - b.x);
  if (items.length < 2) return { found: false, n: items.length, items: items.map((o) => ({ t: o.e.innerText, x: o.x, y: o.y })) };
  const second = items[1];
  let wrap = second.e;
  for (let i = 0; i < 6 && wrap.parentElement; i++) {
    wrap = wrap.parentElement;
    if (/\becom-g-select\b/.test(String(wrap.className)) && !/selection-item/.test(String(wrap.className))) break;
  }
  wrap.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
  return { found: true, n: items.length, cur: (second.e.innerText || "").trim(), at: { x: second.x, y: second.y }, all: items.map((o) => `${o.e.innerText}@${o.y}`) };
});
log("③ 点开第二组下拉:", JSON.stringify(setSecond));
await page.waitForTimeout(1500);

const pick = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
    .map((e) => ({ e, ...box(e), cls: String(e.className) }))
    .filter((o) => o.w > 80 && o.h > 30 && /spec_detail/.test(o.cls) && o.y > 0);
  if (!drops.length) return { found: false };
  const drop = drops[drops.length - 1];
  const opts = [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).filter((e) => (e.innerText || "").trim() === "尺寸");
  if (!opts.length) return { found: false, items: [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 40) };
  const el = opts[opts.length - 1];
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, picked: (el.innerText || "").trim(), at: box(el) };
});
log("③ 选「尺寸」:", JSON.stringify(pick));
await page.waitForTimeout(2000);
await snap("第二组就位");

// ---------- ④ 给第二组加两个值 ----------
for (const v of ["长40", "长60"]) {
  const t = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
      .filter((e) => /请输入尺寸/.test(e.placeholder || "") && !e.value)
      .map((e) => ({ e, ...box(e) }))
      .sort((a, b) => a.y - b.y || a.x - b.x);
    if (!ins.length) return null;
    ins[ins.length - 1].e.scrollIntoView({ block: "center" });
    ins[ins.length - 1].e.focus();
    return { at: { x: ins[ins.length - 1].x, y: ins[ins.length - 1].y }, ph: ins[ins.length - 1].e.placeholder };
  });
  if (!t) { log(`   ✗ 「${v}」无「请输入尺寸」空框`); break; }
  await page.keyboard.type(v, { delay: 80 });
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(1800);
  log(`   + 「${v}」ok`);
}

const final = await snap("完成");
await fs.writeFile(`${ROOT}/v7/r14-final.json`, JSON.stringify({ addG, afterAdd, setSecond, pick, final }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r14.png` });
log("done");
