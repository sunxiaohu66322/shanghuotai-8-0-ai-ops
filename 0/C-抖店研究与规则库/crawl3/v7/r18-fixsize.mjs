// 第 18 轮：① 清掉误填到无关字段的「长40/长60」 ② 循环下移到「尺寸」并回车 ③ 加尺寸值 ④ 验证 6 行
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ========== ① 清理污染 ==========
const clean = await page.evaluate(() => {
  const setter = (el, v) => {
    el.focus();
    const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    s.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  };
  const touched = [];
  for (const el of [...document.querySelectorAll("input,textarea")]) {
    const v = el.value || "";
    if (!/长40|长60/.test(v)) continue;
    if (/请输入颜色|请输入尺寸|请输入材质/.test(el.placeholder || "")) continue; // 规格值框不动
    const nv = v.replace(/长40|长60/g, "").trim();
    setter(el, nv);
    touched.push({ ph: (el.placeholder || "").slice(0, 20), from: v.slice(-30), to: nv.slice(-30) });
  }
  return touched;
});
log("① 清理污染:", JSON.stringify(clean, null, 1));
await page.waitForTimeout(1000);

// ========== ② 第二组类型：展开 → 循环下移到「尺寸」==========
const focus2 = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false };
  const bb = box(btn);
  const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.y < bb.y && o.y > bb.y - 220 && o.w >= 150 && o.w <= 220)
    .sort((a, b) => b.y - a.y);
  if (!sels.length) return { found: false };
  const s = sels[0];
  const input = s.e.querySelector("input");
  if (!input) return { found: false, why: "no-input" };
  input.focus();
  return { found: true, at: { x: s.x, y: s.y }, cur: (s.e.innerText || "").trim() };
});
log("② 聚焦第二组:", JSON.stringify(focus2));

if (!focus2.found) { log("✗ 停止"); }
else {
  // 展开
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(900);

  // 循环下移，直到 active 项 == 尺寸
  let hit = null;
  for (let i = 0; i < 40; i++) {
    const a = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const act = [...document.querySelectorAll("[class*=option-active],[class*=option-selected]")].filter(vv)[0];
      const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv).filter((e) => /spec_detail_1/.test(String(e.className)));
      const open = drops.length > 0;
      return { open, active: act ? (act.innerText || "").trim() : null };
    });
    if (!a.open) { log(`   ✗ 下拉没打开（第 ${i} 次）`); break; }
    if (a.active === "尺寸") { hit = a.active; break; }
    await page.keyboard.press("ArrowDown");
    await page.waitForTimeout(280);
  }
  log("② 高亮到:", JSON.stringify(hit));

  if (hit === "尺寸") {
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2200);
  }

  const afterPick = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
    const bb = btn ? box(btn) : null;
    const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
      .map((e) => ({ e, ...box(e) }))
      .filter((o) => bb && o.y < bb.y && o.y > bb.y - 260 && o.w >= 150 && o.w <= 220)
      .map((o) => (o.e.innerText || "").trim());
    const ins = [...document.querySelectorAll("input[type=text]")].filter(vv).filter((e) => /请输入/.test(e.placeholder || ""))
      .map((e) => `${e.placeholder}=${e.value || "∅"}`);
    return { sels, ins, btn: btn ? btn.innerText : null };
  });
  log("② 选完后:", JSON.stringify(afterPick));

  // ========== ③ 加尺寸值（只在「请输入尺寸」框内）==========
  for (const v of ["长40", "长60"]) {
    const t = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
        .filter((e) => /请输入尺寸/.test(e.placeholder || "") && !e.value)
        .map((e) => ({ e, ...box(e) }))
        .sort((a, b) => a.y - b.y || a.x - b.x);
      if (!ins.length) {
        return { ok: false, allPh: [...document.querySelectorAll("input[type=text]")].filter(vv).map((e) => e.placeholder || "?").filter((p, i, a) => a.indexOf(p) === i).slice(0, 20) };
      }
      const last = ins[ins.length - 1];
      last.e.scrollIntoView({ block: "center" });
      last.e.focus();
      return { ok: true, at: { x: last.x, y: last.y } };
    });
    if (!t.ok) { log(`   ✗ 「${v}」无「请输入尺寸」框; placeholder 种类:`, JSON.stringify(t.allPh)); break; }
    await page.keyboard.type(v, { delay: 80 });
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(1800);
    log(`   + 「${v}」ok @${t.at.x},${t.at.y}`);
  }
}

// ========== ④ 最终验收 ==========
const final = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const head = [...document.querySelectorAll("[class*=table-header],thead")].filter(vv).map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 150));
  const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 90)).filter((x) => x.length > 2);
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  return { head: head.slice(0, 2), rowCount: trs.length, rows: trs.slice(0, 12), addBtn: btn ? btn.innerText : null };
});
log("④ 验收: 按钮", final.addBtn);
log("   表头:", JSON.stringify(final.head));
log("   SKU 行数", final.rowCount, ":");
for (const r of final.rows) log("     ", r);
await fs.writeFile(`${ROOT}/v7/r18-final.json`, JSON.stringify({ clean, focus2, final }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r18.png` });
log("done");
