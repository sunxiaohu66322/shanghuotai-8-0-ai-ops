// 第 15 轮：按「规格组容器」定位第二组，选类型「尺寸」，加 长40/长60，验证 SKU 3×2=6
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① dump 所有规格组容器
const groups = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const containers = [...document.querySelectorAll("[class*=space-item],[class*=spec_group],[class*=specGroup]")].filter(vv)
    .map((e) => ({ e, ...box(e), cls: String(e.className).slice(0, 50) }))
    .filter((o) => o.h > 60 && o.h < 400 && o.w > 300);
  return containers.map((o) => ({
    ...box(o.e), cls: o.cls,
    text: (o.e.innerText || "").replace(/\n+/g, " | ").slice(0, 120),
    sels: [...o.e.querySelectorAll("[class*=ecom-g-select]")].filter(vv)
      .map((s) => ({ ...box(s), cls: String(s.className).slice(0, 45), t: (s.innerText || "").trim().slice(0, 12) }))
      .filter((s) => s.w > 60 && s.h >= 24 && s.h <= 45),
    ins: [...o.e.querySelectorAll("input[type=text]")].filter(vv).map((i) => ({ ...box(i), ph: i.placeholder, val: i.value })),
  }));
});
log("① 规格组容器:", JSON.stringify(groups, null, 1));
await fs.writeFile(`${ROOT}/v7/r15-groups.json`, JSON.stringify(groups, null, 2), "utf8");

if (groups.length < 2) { log("✗ 只找到", groups.length, "组，停止"); }
else {
  // ② 第二组（最后一组）的类型下拉：优先 placeholder「请选择」
  const g2 = groups[groups.length - 1];
  log("② 第二组:", JSON.stringify({ y: g2.y, h: g2.h, text: g2.text, sels: g2.sels }));

  const open2 = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const containers = [...document.querySelectorAll("[class*=space-item]")].filter(vv)
      .map((e) => ({ e, ...box(e) })).filter((o) => o.h > 60 && o.h < 400 && o.w > 300).sort((a, b) => a.y - b.y);
    if (containers.length < 2) return { found: false, n: containers.length };
    const g = containers[containers.length - 1].e;
    g.scrollIntoView({ block: "center" });
    // 该组内的 select 容器（不是 selection-item 自身）
    const sel = [...g.querySelectorAll("[class*=ecom-g-select]")].filter(vv)
      .map((e) => ({ e, ...box(e) }))
      .filter((o) => o.w > 60 && o.h >= 24 && o.h <= 45 && /ecom-g-select\b/.test(String(o.e.className)) && !/selection-item|selection-search|selection-placeholder/.test(String(o.e.className)))
      .sort((a, b) => a.x - b.x)[0];
    if (!sel) return { found: false, why: "no-select-in-group2" };
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) sel.e.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    return { found: true, at: { x: sel.x, y: sel.y }, cur: (sel.e.innerText || "").trim().slice(0, 12) };
  });
  log("② 点开第二组类型下拉:", JSON.stringify(open2));
  await page.waitForTimeout(1500);

  const pick2 = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
      .map((e) => ({ e, ...box(e), cls: String(e.className) }))
      .filter((o) => o.w > 80 && o.h > 30 && /spec_detail/.test(o.cls) && o.y > 0);
    if (!drops.length) return { found: false, n: 0 };
    const drop = drops[drops.length - 1];
    const opts = [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).filter((e) => (e.innerText || "").trim() === "尺寸");
    if (!opts.length) return { found: false, items: [...drop.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 30) };
    const el = opts[opts.length - 1];
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    return { found: true, at: box(el) };
  });
  log("② 选「尺寸」:", JSON.stringify(pick2));
  await page.waitForTimeout(2200);

  // ③ 第二组加两个值
  for (const v of ["长40", "长60"]) {
    const t = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
        .filter((e) => /请输入尺寸/.test(e.placeholder || "") && !e.value)
        .map((e) => ({ e, ...box(e) }))
        .sort((a, b) => a.y - b.y || a.x - b.x);
      if (!ins.length) return { ok: false, sizes: [...document.querySelectorAll("input[type=text]")].filter(vv).filter((e) => /请输入/.test(e.placeholder || "")).map((e) => e.placeholder) };
      const last = ins[ins.length - 1];
      last.e.scrollIntoView({ block: "center" });
      last.e.focus();
      return { ok: true, at: { x: last.x, y: last.y } };
    });
    if (!t.ok) { log(`   ✗ 「${v}」没有「请输入尺寸」框，现有 placeholder:`, JSON.stringify(t.sizes)); break; }
    await page.keyboard.type(v, { delay: 80 });
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(1800);
    log(`   + 「${v}」ok`);
  }
}

// ④ 最终状态
const final = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const groups = [...document.querySelectorAll("[class*=space-item]")].filter(vv)
    .map((e) => ({ e, ...box(e) })).filter((o) => o.h > 60 && o.h < 400 && o.w > 300).sort((a, b) => a.y - b.y)
    .map((o) => ({
      y: o.y, h: o.h,
      type: ([...o.e.querySelectorAll("[class*=select-selection-item],[class*=selection-placeholder]")].filter(vv)[0] || {}).innerText || "",
      vals: [...o.e.querySelectorAll("input[type=text]")].filter(vv).map((i) => i.value).filter(Boolean),
    }));
  const addBtn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 70)).filter((x) => x.length > 2);
  const head = [...document.querySelectorAll("[class*=table-header],thead,[class*=thead]")].filter(vv).map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 120));
  return { groups, addBtn: addBtn ? addBtn.innerText : null, skuRows: trs.length, rows: trs.slice(0, 15), head: head.slice(0, 3) };
});
log("④ 最终: 组数", final.groups.length, "| 按钮", final.addBtn);
log("   各组:", JSON.stringify(final.groups));
log("   SKU 表头:", JSON.stringify(final.head));
log("   SKU 行数", final.skuRows, ":", JSON.stringify(final.rows));
await fs.writeFile(`${ROOT}/v7/r15-final.json`, JSON.stringify(final, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r15.png` });
log("done");
