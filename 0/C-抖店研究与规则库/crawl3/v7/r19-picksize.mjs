// 第 19 轮：滚到可见 → 展开第二组下拉 → 派发点击「尺寸」→ 加值 → 验证
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① 滚到第二组并聚焦
const f = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false, why: "no-btn" };
  // 按钮可能在视口外，先滚
  btn.scrollIntoView({ block: "center" });
  const bb = box(btn);
  const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.y < bb.y && o.y > bb.y - 260 && o.w >= 150 && o.w <= 220)
    .sort((a, b) => b.y - a.y);
  if (!sels.length) return { found: false, why: "no-second", bb, cands: [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv).map((e) => box(e)).filter((o) => o.w >= 150 && o.w <= 220).slice(0, 10) };
  const s = sels[0];
  s.e.scrollIntoView({ block: "center" });
  const input = s.e.querySelector("input");
  if (input) input.focus();
  return { found: true, at: box(s.e), cur: (s.e.innerText || "").trim(), btnAt: bb };
});
log("① 第二组:", JSON.stringify(f));

if (!f.found) { log("✗ 停止"); }
else {
  await page.waitForTimeout(500);
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(1200);

  // ② dump 下拉确认打开
  const d = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
      .map((e) => ({ e, ...box(e), cls: String(e.className) }))
      .filter((o) => /spec_detail/.test(o.cls) && o.w > 80 && o.h > 30);
    return drops.map((o) => ({ ...box(o.e), cls: o.cls.slice(0, 60), n: o.e.querySelectorAll("[class*=option]").length, items: [...o.e.querySelectorAll("[class*=option]")].filter(vv).map((x) => (x.innerText || "").trim()).filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).slice(0, 10) }));
  });
  log("② 展开后:", JSON.stringify(d));

  if (!d.length) { log("✗ 下拉没打开"); }
  else {
    // ③ 派发点击「尺寸」
    const pick = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
        .map((e) => ({ e, ...box(e), cls: String(e.className) }))
        .filter((o) => /spec_detail/.test(o.cls) && o.w > 80 && o.h > 30);
      if (!drops.length) return { found: false, why: "no-drop" };
      const drop = drops[drops.length - 1];
      const opts = [...drop.e.querySelectorAll("[class*=option]")].filter(vv).filter((e) => (e.innerText || "").trim() === "尺寸");
      if (!opts.length) return { found: false, why: "no-尺寸", items: [...drop.e.querySelectorAll("[class*=option]")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 20) };
      const el = opts[0];
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
      return { found: true, at: box(el) };
    });
    log("③ 点「尺寸」:", JSON.stringify(pick));
    await page.waitForTimeout(2500);

    // ④ 状态
    const s1 = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
      const bb = btn ? btn.getBoundingClientRect() : null;
      const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
        .map((e) => ({ e, r: e.getBoundingClientRect() }))
        .filter((o) => bb && o.r.y < bb.y && o.r.y > bb.y - 300 && o.r.width >= 150 && o.r.width <= 220)
        .map((o) => (o.e.innerText || "").trim());
      const ins = [...document.querySelectorAll("input[type=text]")].filter(vv).filter((e) => /请输入/.test(e.placeholder || ""))
        .map((e) => `${e.placeholder}=${e.value || "∅"}`);
      return { sels, ins };
    });
    log("④ 状态: 类型", JSON.stringify(s1.sels));
    log("   输入框:", JSON.stringify(s1.ins));

    // ⑤ 加尺寸值
    for (const v of ["长40", "长60"]) {
      const t = await page.evaluate(() => {
        const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
        const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
        const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
          .filter((e) => /请输入尺寸/.test(e.placeholder || "") && !e.value)
          .map((e) => ({ e, ...box(e) })).sort((a, b) => a.y - b.y || a.x - b.x);
        if (!ins.length) return { ok: false, phs: [...document.querySelectorAll("input[type=text]")].filter(vv).map((e) => e.placeholder || "?").filter((p, i, a) => a.indexOf(p) === i) };
        const last = ins[ins.length - 1];
        last.e.scrollIntoView({ block: "center" });
        last.e.focus();
        return { ok: true, at: { x: last.x, y: last.y } };
      });
      if (!t.ok) { log(`   ✗ 「${v}」无尺寸框; ph 种类:`, JSON.stringify(t.phs)); break; }
      await page.keyboard.type(v, { delay: 80 });
      await page.waitForTimeout(400);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(1800);
      log(`   + 「${v}」ok @${t.at.x},${t.at.y}`);
    }
  }
}

// ⑥ 验收
const final = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const head = [...document.querySelectorAll("[class*=table-header],thead")].filter(vv).map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 150));
  const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 90)).filter((x) => x.length > 2);
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  return { head: head.slice(0, 2), rowCount: trs.length, rows: trs.slice(0, 12), addBtn: btn ? btn.innerText : null };
});
log("⑥ 按钮", final.addBtn, "| 表头:", JSON.stringify(final.head));
log("   SKU 行数", final.rowCount, ":");
for (const r of final.rows) log("     ", r);
await fs.writeFile(`${ROOT}/v7/r19-final.json`, JSON.stringify({ f, final }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r19.png` });
log("done");
