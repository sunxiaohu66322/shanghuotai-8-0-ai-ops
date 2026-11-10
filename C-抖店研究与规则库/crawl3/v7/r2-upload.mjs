// 第 2 轮：续用空间 5，传图 → 生成 → 抓取表单全结构（为后续操作做准备）
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const PID = "1436";

const plan = JSON.parse(await fs.readFile(`${ROOT}/plan-${PID}.json`, "utf8"));
const mains = (plan.main || []).slice(0, 5);
const details = (plan.detail || []).slice(0, 13);
const files = [...mains, ...details].map((p) => p.replace(/\\/g, "/"));
console.log("计划投递", files.length, "张（主图", mains.length, "+ 详情", details.length, "）");

const task = await taskSpace(5);
const page = task.page("p1");
console.log("续用空间:", task.spaceId, "| url:", await page.url());

// 引导页：唯一兼容的 multiple 图片 input
const picker = await page.evaluate(() => {
  const ins = [...document.querySelectorAll("input[type=file]")];
  const idx = ins.findIndex((e) => e.multiple && /jpg|png|image/i.test(e.accept || ""));
  return { idx, count: ins.length, hasGuide: /点击或拖拽文件到此处上传/.test(document.body.innerText || "") };
});
console.log("传图入口:", JSON.stringify(picker));
if (picker.idx < 0) throw new Error("找不到图片上传入口");

await page.setInputFiles("input[type=file] >> nth=" + picker.idx, files);

// 等上传计数
let up = 0;
for (let i = 1; i <= 60; i++) {
  await page.waitForTimeout(2000);
  const s = await page.evaluate(() => {
    const t = document.body.innerText || "";
    return { n: Number((t.match(/已上传\s*(\d+)/) || [0, 0])[1]), busy: /上传中|解析中|处理中/.test(t), next: /下一步，生成商品/.test(t) };
  });
  if (s.n >= files.length && !s.busy) { up = s.n; break; }
  if (i % 5 === 0) console.log(`  等待上传 ${i * 2}s 已上传=${s.n}`);
}
console.log("已上传:", up);

await page.click('text="下一步，生成商品"', { label: "生成商品" }).catch((e) => console.log("点生成失败:", String(e).slice(0, 100)));

// 等识别+生成（最多 300s）
let ready = false;
for (let i = 1; i <= 60; i++) {
  await page.waitForTimeout(5000);
  const s = await page.evaluate(() => {
    const t = document.body.innerText || "";
    return {
      tabs: /基础信息/.test(t) && /图文信息/.test(t),
      gen: /确认信息，继续生成/.test(t),
      parsing: /识别中|解析中|裁切中|正在处理/.test(t),
    };
  });
  if (i % 3 === 0) console.log(`  [生成 ${i * 5}s] tabs=${s.tabs} gen按钮=${s.gen} 处理中=${s.parsing}`);
  if (s.tabs || (!s.gen && !s.parsing && i > 3)) { ready = true; break; }
}
console.log("表单就绪:", ready);

// 抓表单全结构
const form = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const T = document.body.innerText || "";
  const lines = T.split("\n").map((s) => s.trim()).filter(Boolean);

  // 所有小按钮文字（找 AI 按钮、加号、添加规格类型）
  const btns = [...document.querySelectorAll("button,a,span,i,div")]
    .filter((e) => vv(e) && e.children.length === 0)
    .map((e) => (e.innerText || "").trim())
    .filter((t) => t && t.length < 14);

  // 导购短标题附近
  const shortIdx = lines.findIndex((s) => /导购短标题|短标题/.test(s));
  const shortArea = shortIdx >= 0 ? lines.slice(Math.max(0, shortIdx - 2), shortIdx + 5) : [];

  // 发货时间及前后文
  const shipIdx = lines.findIndex((s) => /现货发货时间/.test(s));
  const shipArea = shipIdx >= 0 ? lines.slice(shipIdx, shipIdx + 8) : [];

  // 规格区
  const specIdx = lines.findIndex((s) => /规格类型|添加规格|规格值/.test(s));
  const specArea = specIdx >= 0 ? lines.slice(Math.max(0, specIdx - 2), specIdx + 10) : [];

  const files = [...document.querySelectorAll("input[type=file]")].map((e, i) => ({ i, accept: e.accept || "", multiple: e.multiple }));

  return {
    tabs: [...new Set(lines.filter((s) => /^(基础信息|图文信息|价格库存|属性信息|服务与履约|其他信息)$/.test(s)))],
    shortArea, shipArea, specArea,
    btnSample: [...new Set(btns)].slice(0, 40),
    files: files.slice(0, 40), fileCount: files.length,
    headText: lines.slice(0, 30),
  };
});

console.log("\n--- tabs ---", JSON.stringify(form.tabs));
console.log("--- 导购短标题区 ---", JSON.stringify(form.shortArea));
console.log("--- 发货时间区 ---", JSON.stringify(form.shipArea));
console.log("--- 规格区 ---", JSON.stringify(form.specArea));
console.log("--- 按钮样本 ---", JSON.stringify(form.btnSample));
console.log("--- file inputs ---", form.fileCount);
form.files.forEach((f) => console.log(`   [${f.i}] accept=${f.accept} mul=${f.multiple}`));

await fs.writeFile(`${ROOT}/v7/r2-form.json`, JSON.stringify(form, null, 2), "utf8");
console.log("\n已存 r2-form.json");
