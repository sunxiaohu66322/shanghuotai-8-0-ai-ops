// 第 5 轮：先补商品标题（AI 推荐 / 手填），再点「确认信息，继续生成」
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const pid = "1436";
const plan = JSON.parse(await fs.readFile(`${ROOT}/plan-${pid}.json`, "utf8"));

const task = await taskSpace(5);
const page = task.page("p1");

const snap = () => page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const T = document.body.innerText || "";
  const titleIn = [...document.querySelectorAll("input,textarea")].filter(vv).map((e) => ({
    ph: e.placeholder || "", val: e.value || "", max: e.maxLength,
  }));
  return {
    form: /基础信息/.test(T) && /图文信息/.test(T),
    gen: /确认信息，继续生成/.test(T),
    title: titleIn.filter((x) => /60/.test(x.ph) || /60/.test(String(x.max)) || x.val),
    emptyCount: titleIn.filter((x) => !x.val).length,
  };
});

console.log("初始:", JSON.stringify(await snap()));

// 1) 点「AI 推荐」（会生成一批标题）
const aiBtn = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const e = [...document.querySelectorAll("span,div,button,a")].filter((x) => vv(x) && x.children.length === 0 && (x.innerText || "").trim() === "AI 推荐")[0];
  if (!e) return null;
  e.scrollIntoView({ block: "center" });
  const r = e.getBoundingClientRect();
  return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
});
console.log("AI 推荐按钮:", JSON.stringify(aiBtn));
if (aiBtn) {
  await page.mouse.click(aiBtn.x, aiBtn.y, { label: "AI推荐标题" });
  await page.waitForTimeout(8000);
  console.log("点后:", JSON.stringify(await snap()));
}

// 2) 若标题仍空，直接填 plan.title（30 字内）
let s = await snap();
const hasTitle = (s.title || []).some((x) => (x.val || "").trim().length > 3);
if (!hasTitle) {
  const t30 = Array.from(plan.title || "").slice(0, 30).join("");
  await page.evaluate((v) => {
    const vv = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("input,textarea")].filter(vv).find((e) => /2-60|商品标题/.test(e.placeholder || "") || e.maxLength === 60);
    if (!el) return;
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    setter.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }, t30);
  await page.waitForTimeout(2000);
  console.log("手填标题后:", JSON.stringify(await snap()));
}

// 3) 点「确认信息，继续生成」—— 先滚到底再点
const genInfo = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const els = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "确认信息，继续生成");
  if (!els.length) return { found: false };
  const el = els[0];
  el.scrollIntoView({ block: "center" });
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  // 逐层看有没有 pointer-events:none 或遮挡
  let p = el, blocked = [];
  for (let i = 0; i < 6 && p; i++) {
    const c = getComputedStyle(p);
    if (c.pointerEvents === "none") blocked.push(p.tagName + "." + (p.className || "").slice(0, 30));
    if (c.opacity === "0") blocked.push("opacity0:" + p.tagName);
    p = p.parentElement;
  }
  return { found: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), h: Math.round(r.height), pe: cs.pointerEvents, blocked };
});
console.log("生成按钮:", JSON.stringify(genInfo));

if (genInfo.found) {
  // 依次尝试三种点法
  const tries = [
    ["mouse.click", async () => page.mouse.click(genInfo.x, genInfo.y, { label: "确认生成" })],
    ["el.click()", async () => page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const el = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "确认信息，继续生成")[0];
      if (el) el.click();
    })],
    ["事件派发", async () => page.evaluate(() => {
      const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
      const vv = (e) => e.offsetParent !== null;
      const el = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "确认信息，继续生成")[0];
      if (el) { dc(el); dc(el.parentElement); }
    })],
  ];
  for (const [name, fn] of tries) {
    await fn().catch((e) => console.log(`  ${name} 出错:`, String(e).slice(0, 80)));
    await page.waitForTimeout(12000);
    const now = await snap();
    console.log(`  ${name} → form=${now.form} gen=${now.gen}`);
    if (now.form || !now.gen) { console.log("  ✅ 生效:", name); break; }
  }
}

// 4) 等表单
for (let i = 1; i <= 36; i++) {
  await page.waitForTimeout(5000);
  const now = await snap();
  if (now.form) { console.log(`表单就绪（${i * 5}s）`); break; }
  if (i % 4 === 0) console.log(`  [${i * 5}s] form=${now.form} gen=${now.gen}`);
}

const end = await snap();
console.log("最终:", JSON.stringify({ form: end.form, gen: end.gen }));
if (!end.form) {
  await page.screenshot({ path: `${ROOT}/v7/shot-r5.png` });
  console.log("截图 v7/shot-r5.png");
}
