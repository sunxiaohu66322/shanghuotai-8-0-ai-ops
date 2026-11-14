// 第 4 轮：用语义点击过「确认信息，继续生成」，然后抓表单结构
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const task = await taskSpace(5);
const page = task.page("p1");
console.log("url =", await page.url());

const st = () => page.evaluate(() => {
  const T = document.body.innerText || "";
  return { form: /基础信息/.test(T) && /图文信息/.test(T), gen: /确认信息，继续生成/.test(T), busy: /识别中|生成中|裁切中/.test(T) };
});
console.log("初始:", JSON.stringify(await st()));

// 方式1：语义点击（ego 自己找可点击元素）
for (let round = 1; round <= 3; round++) {
  const s = await st();
  if (s.form) break;
  try {
    await page.click('text="确认信息，继续生成"', { label: "确认信息继续生成", timeout: 15000 });
    console.log(`第${round}次 page.click 已执行`);
  } catch (e) {
    console.log(`第${round}次 page.click 失败:`, String(e).slice(0, 120));
    // 兜底：找 button 元素本身（不是 span）
    const b = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const cands = [...document.querySelectorAll("button")].filter((e) => vv(e) && /确认信息，继续生成/.test(e.innerText || "") && !e.disabled);
      if (!cands.length) return null;
      const el = cands[0];
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), h: Math.round(r.height) };
    });
    console.log("  兜底 button:", JSON.stringify(b));
    if (b) await page.mouse.click(b.x, b.y, { label: "点确认生成按钮" });
  }
  // 等 60 秒看结果
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(5000);
    if ((await st()).form) break;
  }
  console.log(`第${round}轮后:`, JSON.stringify(await st()));
}

const final = await st();
if (!final.form) {
  await page.screenshot({ path: `${ROOT}/v7/shot-r4-stuck.png` });
  const diag = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const all = [...document.querySelectorAll("button")].filter(vv).map((e) => ({
      t: (e.innerText || "").trim().slice(0, 20), disabled: e.disabled,
      w: Math.round(e.getBoundingClientRect().width), h: Math.round(e.getBoundingClientRect().height),
    }));
    // 找页面上的红字/校验提示
    const reds = [...document.querySelectorAll("[class*=error],[class*=warn],[class*=tip]")]
      .filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 10);
    return { buttons: all.slice(0, 20), reds };
  });
  console.log("诊断 buttons:", JSON.stringify(diag.buttons));
  console.log("诊断 提示:", JSON.stringify(diag.reds));
}

// 抓表单
const form = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const T = document.body.innerText || "";
  const lines = T.split("\n").map((s) => s.trim()).filter(Boolean);
  const around = (re, before = 1, after = 8) => {
    const i = lines.findIndex((s) => re.test(s));
    return i < 0 ? [] : lines.slice(Math.max(0, i - before), i + after);
  };
  const btns = [...document.querySelectorAll("button,a,span,i,div")]
    .filter((e) => vv(e) && e.children.length === 0)
    .map((e) => (e.innerText || "").trim()).filter((t) => t && t.length < 14);
  const files = [...document.querySelectorAll("input[type=file]")].map((e, i) => ({ i, accept: e.accept || "", multiple: e.multiple }));
  return {
    tabs: [...new Set(lines.filter((s) => /^(基础信息|图文信息|价格库存|属性信息|服务与履约|其他信息)$/.test(s)))],
    shortTitle: around(/导购短标题|短标题/),
    ship: around(/现货发货时间/),
    spec: around(/规格类型|添加规格|规格值/),
    mainImg: around(/主图3:4|上传主图/, 2, 8),
    btns: [...new Set(btns)].slice(0, 55),
    files: files.slice(0, 45), fileCount: files.length,
  };
});
console.log("\n=== 表单 ===");
console.log("tabs:", JSON.stringify(form.tabs));
console.log("导购短标题:", JSON.stringify(form.shortTitle));
console.log("发货:", JSON.stringify(form.ship));
console.log("规格:", JSON.stringify(form.spec));
console.log("主图:", JSON.stringify(form.mainImg));
console.log("按钮:", JSON.stringify(form.btns));
console.log("fileCount:", form.fileCount);
form.files.forEach((f) => console.log(`  [${f.i}] ${f.accept} mul=${f.multiple}`));
await fs.writeFile(`${ROOT}/v7/r4-form.json`, JSON.stringify(form, null, 2), "utf8");
