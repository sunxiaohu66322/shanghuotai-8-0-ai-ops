// 第 9 轮：规格设置（6 个材质值）+ 打开「添加规格图」+ 逐行上传 SKU 图
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const PID = "1436";
const plan = JSON.parse(await fs.readFile(`${ROOT}/plan-${PID}.json`, "utf8"));
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");

// 规格短名（去掉营销前缀，取括号里的颜色/尺寸更短，但先按 plan 原值）
const values = plan.skus.map((s) => s.name);
log("目标规格值", values.length, "个");

// ---------- 1) 找「添加规格图」开关状态 + 规格值行结构 ----------
const probe = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  // 「添加规格图」文字 + 它左边的 switch
  const lbl = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && /添加规格图/.test((e.innerText || "").trim()))[0];
  const lb = lbl ? box(lbl) : null;
  let sw = null;
  if (lb) {
    const cands = [...document.querySelectorAll("[class*=switch],[role=switch],[class*=Switch]")]
      .filter((e) => vv(e) && Math.abs(box(e).y - lb.y) < 20 && box(e).x < lb.x)
      .map((e) => ({ ...box(e), checked: e.getAttribute("aria-checked") || String(e.className).match(/checked/i) ? "checked" : "unchecked", cls: String(e.className).slice(0, 40) }));
    sw = cands[0] || null;
  }
  // 规格值单元格（带 × 删除图标的行）
  const valInput = [...document.querySelectorAll("input")].filter(vv).find((e) => /请输入材质|请输入/.test(e.placeholder || ""));
  const valBox = valInput ? box(valInput) : null;
  return { lb, sw, valBox, valInputPh: valInput ? valInput.placeholder : null };
});
log("开关探针:", JSON.stringify(probe));

// 用更稳的方式重探规格值区
const specArea = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && /添加规格图/.test((e.innerText || "").trim()))[0];
  if (!lab) return { found: false };
  lab.scrollIntoView({ block: "center" });
  const lb = box(lab);
  // 整个规格设置区块
  let block = lab;
  for (let i = 0; i < 8 && block.parentElement; i++) {
    block = block.parentElement;
    if (/批量上传规格图/.test(block.innerText || "") && (block.innerText || "").length < 900) break;
  }
  const switches = [...block.querySelectorAll("[class*=switch],[role=switch],[class*=Switch],[class*=toggle]")]
    .filter(vv).map((e) => ({ ...box(e), aria: e.getAttribute("aria-checked"), cls: String(e.className).slice(0, 50) }));
  const inputs = [...block.querySelectorAll("input")].filter(vv).map((e) => ({ ...box(e), ph: e.placeholder || "", val: e.value || "" }));
  const txts = [...block.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0)
    .map((e) => ({ ...box(e), t: (e.innerText || "").trim() })).filter((o) => o.t && o.t.length < 30);
  return { found: true, block: box(block), switches, inputs, txts: txts.slice(0, 30) };
});
log("规格区:", JSON.stringify(specArea.block));
log("开关:", JSON.stringify(specArea.switches));
log("输入框:", JSON.stringify(specArea.inputs));
log("文字:", JSON.stringify(specArea.txts?.map((o) => `${o.t}@${o.x},${o.y}`)));

await fs.writeFile(`${ROOT}/v7/r9-spec.json`, JSON.stringify(specArea, null, 2), "utf8");
