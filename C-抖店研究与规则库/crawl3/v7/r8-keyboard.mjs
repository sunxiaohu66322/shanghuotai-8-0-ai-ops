// 第 8 轮：键盘路线验证 —— 用方向键把「次日发」切到「48小时」，再切回
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const task = await taskSpace(5);
const page = task.page("p1");

const readShip = () => page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const out = {};
  for (const label of ["当日发", "次日发", "48小时", "大于48小时发货"]) {
    const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === label).pop();
    if (!el) { out[label] = "absent"; continue; }
    const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
    out[label] = rb ? (rb.checked ? "CHECKED" : "-") : "-";
  }
  // 当前焦点元素
  const ae = document.activeElement;
  out._focus = ae ? (ae.tagName + "|" + (ae.type || "") + "|" + (ae.innerText || ae.value || "").slice(0, 12)) : "none";
  return out;
});

console.log("键盘前:", JSON.stringify(await readShip()));

// ① 聚焦当前选中的 radio（点它的 label 文字即可聚焦；也用键盘兜底）
const focused = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === "次日发").pop();
  if (!el) return "not-found";
  el.scrollIntoView({ block: "center" });
  const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
  if (rb) { rb.focus(); return "radio-focused"; }
  el.focus?.(); return "el-focused";
});
await page.waitForTimeout(500);
console.log("聚焦结果:", focused, "| 焦点:", (await readShip())._focus);

// ② 按方向键：次日发 → 48小时（右移一次）
await page.keyboard.press("ArrowRight");
await page.waitForTimeout(1200);
const after1 = await readShip();
console.log("ArrowRight ×1:", JSON.stringify(after1));

// ③ 再右移到「大于48小时发货」看看
await page.keyboard.press("ArrowRight");
await page.waitForTimeout(1200);
const after2 = await readShip();
console.log("ArrowRight ×2:", JSON.stringify(after2));

// ④ 回到 48小时（左移一次）
await page.keyboard.press("ArrowLeft");
await page.waitForTimeout(1200);
const after3 = await readShip();
console.log("ArrowLeft ×1:", JSON.stringify(after3));

// ⑤ 结论
const okTo48 = after1["48小时"] === "CHECKED" || after3["48小时"] === "CHECKED";
console.log("\n== 键盘可切到 48小时 ==", okTo48 ? "✅ 是" : "❌ 否");
console.log("当前终态:", JSON.stringify(after3));

await fs.writeFile(`${ROOT}/v7/r8-keyboard.json`, JSON.stringify({ after1, after2, after3 }, null, 2), "utf8");
