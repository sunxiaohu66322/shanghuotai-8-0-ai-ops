// 第 16 轮：精确 dump 第二组结构（按钮上方区域），再选「尺寸」+ 加值
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① 以「添加规格类型」按钮为基准，dump 它上方 200px 内的所有元素
const R = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false };
  btn.scrollIntoView({ block: "center" });
  const bb = box(btn);
  const lo = bb.y - 260, hi = bb.y + 10;
  const els = [...document.querySelectorAll("*")].filter(vv).map((e) => ({ e, ...box(e) }))
    .filter((o) => o.y >= lo && o.y <= hi && o.x >= 580 && o.x <= 1550 && o.h <= 120 && o.w <= 900)
    .map((o) => ({
      tag: o.e.tagName, ...box(o.e),
      cls: String(o.e.className || "").slice(0, 50),
      ph: o.e.placeholder || "", val: (o.e.value || "").slice(0, 14),
      t: (o.e.innerText || "").trim().slice(0, 18), kids: o.e.children.length,
    }));
  return { found: true, btn: { ...bb, t: btn.innerText }, els };
});
log("① 按钮:", JSON.stringify(R.btn));
log("① 上方区元素:");
for (const e of R.els || []) log(`   ${e.tag} ${e.cls.padEnd(26)} @${e.x},${e.y} ${e.w}x${e.h} ${e.ph ? "ph=" + e.ph : ""} ${e.val ? "val=" + e.val : ""} ${e.t ? "t=" + e.t.replace(/\n/g, "·") : ""}`);
await fs.writeFile(`${ROOT}/v7/r16-zone.json`, JSON.stringify(R, null, 2), "utf8");

// ② 尝试直接从 placeholder「请选择」定位第二组类型下拉并点开
const open2 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false };
  const bb = box(btn);
  // 按钮上方 260px 内、文本为「请选择」的 select 占位符
  const phs = [...document.querySelectorAll("[class*=selection-placeholder]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.y < bb.y && o.y > bb.y - 260 && o.x >= 580)
    .sort((a, b) => b.y - a.y);
  if (!phs.length) {
    return { found: false, allPh: [...document.querySelectorAll("[class*=selection-placeholder]")].filter(vv).map((e) => ({ t: (e.innerText || "").trim(), ...box(e) })).slice(0, 20) };
  }
  const p = phs[0];
  let wrap = p.e;
  for (let i = 0; i < 6 && wrap.parentElement; i++) {
    wrap = wrap.parentElement;
    if (/\becom-g-select\b/.test(String(wrap.className)) && !/selection-item|selection-search|selection-placeholder/.test(String(wrap.className))) break;
  }
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) wrap.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  return { found: true, ph: (p.e.innerText || "").trim(), at: { x: p.x, y: p.y }, wrapCls: String(wrap.className).slice(0, 50) };
});
log("② 点开第二组:", JSON.stringify(open2));
await page.waitForTimeout(1500);

const menu = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
    .map((e) => ({ e, ...box(e), cls: String(e.className) }))
    .filter((o) => o.w > 80 && o.h > 30 && /spec_detail/.test(o.cls) && o.y > 0);
  return { n: drops.length, items: drops.map((d) => [...d.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).slice(0, 30)) };
});
log("② 菜单:", JSON.stringify(menu));
await fs.writeFile(`${ROOT}/v7/r16-menu.json`, JSON.stringify({ open2, menu }, null, 2), "utf8");

await page.screenshot({ path: `${ROOT}/v7/shot-r16.png` });
log("done");
