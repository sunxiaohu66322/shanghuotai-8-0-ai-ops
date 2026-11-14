// 第 7 轮：定位①导购短标题 AI 按钮 ②规格值↔图位映射 ③发货 radio 精确坐标
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const task = await taskSpace(5);
const page = task.page("p1");

// 先切到「价格库存」（规格 + 发货都在那儿），再抓
const goTab = async (name) => {
  const r = await page.evaluate((n) => {
    const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
    const vv = (e) => e.offsetParent !== null;
    const cands = [...document.querySelectorAll("div,span,a,li")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === n);
    if (!cands.length) return "absent";
    const el = cands[0];
    el.scrollIntoView({ block: "center" });
    dc(el); return "ok";
  }, name);
  await page.waitForTimeout(3500);
  return r;
};
console.log("切价格库存:", await goTab("价格库存"));

const dump = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };

  // ① 导购短标题：找 label 同行的输入框 + 右侧所有图标
  const shortLabel = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "导购短标题")[0];
  const shortBox = shortLabel ? box(shortLabel) : null;
  let shortIcons = [];
  if (shortBox) {
    shortIcons = [...document.querySelectorAll("img,svg,i,button,span[class]")]
      .filter((e) => vv(e) && Math.abs(box(e).y - shortBox.y) < 40)
      .map((e) => ({ ...box(e), cls: String(e.className || "").slice(0, 40), title: e.getAttribute?.("title") || "", aria: e.getAttribute?.("aria-label") || "" }))
      .filter((o) => o.w > 0);
  }

  // ② 规格区：所有「规格值」单元格 + 每个单元格里的图位
  const specTitle = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && /^商品规格$/.test((e.innerText || "").trim()))[0];
  const specY = specTitle ? box(specTitle).y : null;
  const rows = [...document.querySelectorAll("tr,li,[class*=row],[class*=item]")]
    .filter((e) => vv(e) && specY !== null && box(e).y > specY && box(e).y < specY + 700)
    .map((e) => ({ ...box(e), cls: String(e.className || "").slice(0, 50), text: (e.innerText || "").replace(/\s+/g, " ").slice(0, 70) }))
    .filter((o) => o.w > 100 && o.h > 20).slice(0, 25);

  // 规格值文字（材质下的选项）
  const specVals = [...document.querySelectorAll("*")]
    .filter((e) => vv(e) && e.children.length === 0 && specY !== null && box(e).y > specY && box(e).y < specY + 600)
    .map((e) => ({ ...box(e), t: (e.innerText || "").trim() }))
    .filter((o) => o.t && o.t.length < 24).slice(0, 30);

  // ③ 发货 radio 精确坐标
  const shipOpts = {};
  for (const label of ["当日发", "次日发", "48小时", "大于48小时发货"]) {
    const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === label).pop();
    if (el) {
      const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
      shipOpts[label] = { ...box(el), checked: rb ? rb.checked : null, hasRadio: !!rb };
    } else shipOpts[label] = null;
  }

  const files = [...document.querySelectorAll("input[type=file]")].map((e, i) => ({ i, accept: e.accept || "", multiple: e.multiple }));
  return { shortBox, shortIcons: shortIcons.slice(0, 15), specY, rows, specVals, shipOpts, files: files.slice(0, 40) };
});

console.log("\n=== 导购短标题 label ===", JSON.stringify(dump.shortBox));
console.log("=== 同行图标 ===");
dump.shortIcons.forEach((o) => console.log(`   (${o.x},${o.y}) ${o.w}x${o.h} cls=${o.cls} title=${o.title} aria=${o.aria}`));
console.log("\n=== 发货选项 ===", JSON.stringify(dump.shipOpts));
console.log("\n=== 规格区行 ===");
dump.rows.forEach((r) => console.log(`   y=${r.y} ${r.w}x${r.h} "${r.text}"`));
console.log("\n=== 规格值 ===");
dump.specVals.forEach((v) => console.log(`   (${v.x},${v.y}) "${v.t}"`));
console.log("\n=== file inputs ===", dump.files.length);
dump.files.forEach((f) => console.log(`   [${f.i}] ${f.accept} mul=${f.multiple}`));

await fs.writeFile(`${ROOT}/v7/r7-spec.json`, JSON.stringify(dump, null, 2), "utf8");
console.log("\n已存 r7-spec.json");
