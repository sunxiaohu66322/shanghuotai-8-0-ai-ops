// 第 17 轮：键盘选第二组类型「尺寸」→ 加 长40/长60 → 验证 SKU 6 行
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId);

// ① 定位第二组的 select 搜索框并聚焦
const f = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  if (!btn) return { found: false };
  const bb = box(btn);
  // 按钮上方最近的「未选类型」的 select 容器
  const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
    .map((e) => ({ e, ...box(e) }))
    .filter((o) => o.y < bb.y && o.y > bb.y - 200 && o.w >= 150 && o.w <= 220 && o.h >= 28 && o.h <= 40)
    .sort((a, b) => b.y - a.y);
  if (!sels.length) return { found: false, why: "no-second-select" };
  const s = sels[0];
  const input = s.e.querySelector("input");
  if (!input) return { found: false, why: "no-input" };
  input.focus();
  return { found: true, at: { x: s.x, y: s.y }, ph: (s.e.innerText || "").trim(), focused: document.activeElement === input };
});
log("① 聚焦第二组 select:", JSON.stringify(f));

if (!f.found) { log("✗ 停止"); }
else {
  // ② 键盘：输入「尺寸」筛选
  await page.keyboard.type("尺寸", { delay: 120 });
  await page.waitForTimeout(1200);
  const afterType = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
      .map((e) => ({ e, ...box(e), cls: String(e.className) }))
      .filter((o) => o.w > 80 && o.h > 20 && o.y > 0);
    return drops.map((d) => ({
      ...box(d.e), cls: d.cls.slice(0, 60),
      items: [...d.e.querySelectorAll("[class*=option],[role=option],li")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).filter((v, i, a) => a.indexOf(v) === i).slice(0, 12),
    }));
  });
  log("② 输入「尺寸」后下拉:", JSON.stringify(afterType));

  // ③ 回车选中第一项
  await page.keyboard.press("Enter");
  await page.waitForTimeout(2000);

  const afterEnter = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
    const bb = btn ? box(btn) : null;
    const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
      .map((e) => ({ e, ...box(e) }))
      .filter((o) => bb && o.y < bb.y && o.y > bb.y - 220 && o.w >= 150 && o.w <= 220)
      .map((o) => (o.e.innerText || "").trim());
    const ins = [...document.querySelectorAll("input[type=text]")].filter(vv).filter((e) => /请输入/.test(e.placeholder || ""))
      .map((e) => `${e.placeholder}=${e.value || "∅"}`);
    return { sels, ins, btn: btn ? btn.innerText : null };
  });
  log("③ 回车后:", JSON.stringify(afterEnter));

  // ④ 加两个尺寸值
  for (const v of ["长40", "长60"]) {
    const t = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      // 第二组的空值输入框：placeholder 不是「请输入颜色」
      const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
        .filter((e) => /请输入/.test(e.placeholder || "") && !/请输入颜色/.test(e.placeholder || "") && !e.value)
        .map((e) => ({ e, ...box(e) }))
        .sort((a, b) => a.y - b.y || a.x - b.x);
      if (!ins.length) {
        return { ok: false, allPh: [...document.querySelectorAll("input[type=text]")].filter(vv).filter((e) => /请输入/.test(e.placeholder || "")).map((e) => `${e.placeholder}=${e.value || "∅"}`) };
      }
      const last = ins[ins.length - 1];
      last.e.scrollIntoView({ block: "center" });
      last.e.focus();
      return { ok: true, at: { x: last.x, y: last.y }, ph: last.e.placeholder };
    });
    if (!t.ok) { log(`   ✗ 「${v}」无尺寸空框; 现有:`, JSON.stringify(t.allPh)); break; }
    await page.keyboard.type(v, { delay: 80 });
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(1800);
    log(`   + 「${v}」ok (@${t.at.x},${t.at.y} ph=${t.ph})`);
  }
}

// ⑤ 最终验收
const final = await page.evaluate(() => {
  const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
  const head = [...document.querySelectorAll("[class*=table-header],thead")].filter(vv).map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 140));
  const trs = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
    .map((e) => (e.innerText || "").replace(/\n/g, "|").slice(0, 80)).filter((x) => x.length > 2);
  const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
  const typeTexts = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean);
  return { head: head.slice(0, 2), rowCount: trs.length, rows: trs.slice(0, 12), addBtn: btn ? btn.innerText : null, typeTexts: typeTexts.slice(0, 6) };
});
log("⑤ 验收: 按钮", final.addBtn, "| 类型下拉", JSON.stringify(final.typeTexts));
log("   表头:", JSON.stringify(final.head));
log("   SKU 行数", final.rowCount, ":", JSON.stringify(final.rows));
await fs.writeFile(`${ROOT}/v7/r17-final.json`, JSON.stringify({ f, afterType, afterEnter, final }, null, 2), "utf8");
await page.screenshot({ path: `${ROOT}/v7/shot-r17.png` });
log("done");
