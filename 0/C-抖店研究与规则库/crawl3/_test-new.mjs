/**
 * 验证两个新函数能找到目标控件（不破坏数据：改完立刻还原成原值「次日发」）
 */
const shim = await import("./ego-shim.mjs");
const space = await shim.taskSpace("抖店批量上货");
const page = space.page("p1");
console.log("页面:", await page.url());
console.log("标题:", await page.title());

const goTab = async (name) => {
  const r = await page.evaluate((n) => {
    const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
    const vv = (e) => e.offsetParent !== null;
    const t = [...document.querySelectorAll("div,span,a,li")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === n)[0];
    if (!t) return "absent";
    dc(t); return "ok";
  }, name);
  await page.waitForTimeout(2500);
  return r;
};

// ---- A. 视频位是否存在 ----
const vid = await page.evaluate(() => {
  const ins = [...document.querySelectorAll('input[type=file][accept*=".mp4"]')];
  return { n: ins.length, accept: ins[0]?.accept || "" };
});
console.log("\n[视频位]", JSON.stringify(vid));

// ---- B. 发货时间：读当前选中，切到 48小时，再切回 ----
const readShip = () => page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const out = {};
  for (const label of ["当日发", "次日发", "48小时", "大于48小时发货"]) {
    const el = [...document.querySelectorAll("label,span,div,li")].filter((e) => vv(e) && (e.innerText || "").trim() === label).pop();
    if (!el) { out[label] = "absent"; continue; }
    const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
    if (rb) { out[label] = rb.checked ? "CHECKED" : "unchecked"; continue; }
    const p = el.parentElement;
    out[label] = /checked|active|selected/i.test(((el.className || "") + (p?.className || ""))) ? "CHECKED?" : "unchecked";
  }
  return out;
});

const clickOpt = (label) => page.evaluate((l) => {
  const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
  const vv = (e) => e.offsetParent !== null;
  const cands = [...document.querySelectorAll("label,span,div,li")].filter((e) => vv(e) && (e.innerText || "").trim() === l);
  if (!cands.length) return { ok: false };
  const el = cands[cands.length - 1];
  const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
  dc(rb || el);
  return { ok: true, via: rb ? "radio" : "elem" };
}, label);

console.log("\n[切 tab 基础信息]", await goTab("基础信息"));
const before = await readShip();
console.log("[发货时间 改前]", JSON.stringify(before));

const c1 = await clickOpt("48小时");
await page.waitForTimeout(1500);
const after = await readShip();
console.log("[点「48小时」]", JSON.stringify(c1), "->", JSON.stringify(after));

// 还原
const c2 = await clickOpt("次日发");
await page.waitForTimeout(1500);
const restored = await readShip();
console.log("[还原「次日发」]", JSON.stringify(c2), "->", JSON.stringify(restored));

const ok = after["48小时"]?.startsWith("CHECKED") && restored["次日发"]?.startsWith("CHECKED");
console.log("\n== 结论 ==", ok ? "✅ 发货时间可选可还原" : "❌ 未能确认");

process.exit(0);
