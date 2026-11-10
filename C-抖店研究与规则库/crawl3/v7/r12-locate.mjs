// 第 12 轮：① 清掉误填的型号 ② 用锚点精确锁规格区 ③ 点开规格类型下拉，dump 全部可选类型
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ---------- ① 清掉型号框里误填的「胡桃色」 ----------
const clean = await page.evaluate(() => {
  const ins = [...document.querySelectorAll("input[type=text]")].filter((e) => e.offsetParent !== null && e.value === "胡桃色");
  const done = [];
  for (const el of ins) {
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    setter.call(el, "");
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    done.push({ x: Math.round(el.getBoundingClientRect().x), y: Math.round(el.getBoundingClientRect().y) });
  }
  return { cleared: done.length, at: done };
});
log("① 清理误填:", JSON.stringify(clean));
await page.waitForTimeout(800);

// ---------- ② 规格区锚点定位（不靠 y 坐标） ----------
const P = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };

  // 锚点：「添加规格图」文字（规格区唯一）
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false, why: "no-添加规格图" };
  lab.scrollIntoView({ block: "center" });

  // 往上找规格 block：含「添加规格类型」
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) {
    block = block.parentElement;
    const t = block.innerText || "";
    if (/添加规格类型/.test(t) && t.length < 900) break;
  }

  const bb = box(block);
  // block 内的类型下拉：class 完全等于 ecom-g-select ... 的祖先
  const selects = [...block.querySelectorAll("[class*=select-selection-item]")].filter(vv).map((e) => {
    const wrap = e.parentElement?.parentElement?.parentElement || e.parentElement;
    return {
      ...box(e), text: (e.innerText || "").trim(), wrapCls: String(wrap.className).slice(0, 70), ...box(wrap),
    };
  });
  const inputs = [...block.querySelectorAll("input")].filter(vv).map((e) => ({
    ...box(e), ph: e.placeholder || "", val: (e.value || "").slice(0, 16), type: e.type,
  }));
  const switches = [...block.querySelectorAll("[role=switch]")].filter(vv).map((e) => ({
    ...box(e), aria: e.getAttribute("aria-checked"), cls: String(e.className).slice(0, 50),
  }));
  // 规格值 chip 区：block 内所有带删除图标的容器
  const delIcons = [...block.querySelectorAll("[class*=delete],[class*=Delete]")].filter(vv).map((e) => ({ ...box(e), cls: String(e.className).slice(0, 50) }));

  return { found: true, block: bb, blockText: (block.innerText || "").replace(/\n+/g, " | ").slice(0, 300), selects, inputs, switches, delIcons };
});
log("② 规格区:", JSON.stringify(P.block), "| 文本:", P.blockText);
log("   类型下拉:", JSON.stringify(P.selects));
log("   输入框:", JSON.stringify(P.inputs));
log("   开关:", JSON.stringify(P.switches));
log("   删除图标:", JSON.stringify(P.delIcons));
await fs.writeFile(`${ROOT}/v7/r12-P.json`, JSON.stringify(P, null, 2), "utf8");

// ---------- ③ 点开规格类型下拉 ----------
const open = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "添加规格图")[0];
  if (!lab) return { found: false };
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) { block = block.parentElement; if (/添加规格类型/.test(block.innerText || "") && (block.innerText || "").length < 900) break; }
  const item = [...block.querySelectorAll("[class*=select-selection-item]")].filter(vv)[0];
  if (!item) return { found: false, why: "no-item" };
  // 正确的祖先：class 含 ecom-g-select 但是 select 容器（不是 item）
  let wrap = item;
  for (let i = 0; i < 6 && wrap.parentElement; i++) {
    wrap = wrap.parentElement;
    if (/\becom-g-select\b/.test(String(wrap.className)) && !/selection-item/.test(String(wrap.className))) break;
  }
  wrap.scrollIntoView({ block: "center" });
  const b = box(wrap);
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    wrap.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  }
  return { found: true, cur: (item.innerText || "").trim(), wrapCls: String(wrap.className).slice(0, 70), at: b };
});
log("③ 点开类型下拉:", JSON.stringify(open));
await page.waitForTimeout(1500);

const menu = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  // 规格类型下拉是单选：找 ecom-g-select-dropdown 且不是 multiSelect
  const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
    .map((e) => ({ e, ...box(e), cls: String(e.className).slice(0, 60) }))
    .filter((o) => o.w > 80 && o.h > 30);
  return drops.map((o) => ({
    ...box(o.e), cls: o.cls,
    items: [...o.e.querySelectorAll("[class*=option],[role=option],li,[class*=item]")].filter(vv)
      .map((i) => (i.innerText || "").trim()).filter(Boolean).slice(0, 80),
  })).slice(0, 6);
});
log("③ 菜单:", JSON.stringify(menu, null, 1));
await fs.writeFile(`${ROOT}/v7/r12-menu.json`, JSON.stringify({ open, menu }, null, 2), "utf8");

await page.screenshot({ path: `${ROOT}/v7/shot-r12.png` });
log("done");
