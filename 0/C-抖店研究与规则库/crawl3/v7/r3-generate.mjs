// 第 3 轮：识别页 → 点「确认信息，继续生成」（坐标点击）→ 等表单 → 抓结构
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const task = await taskSpace(5);
const page = task.page("p1");
console.log("url =", await page.url());

const state = await page.evaluate(() => {
  const T = document.body.innerText || "";
  return {
    guide: /点击或拖拽文件到此处上传/.test(T),
    recognize: /按住图片可上下拖动|商品主图/.test(T),
    form: /基础信息/.test(T) && /图文信息/.test(T),
    gen: /确认信息，继续生成/.test(T),
    err: (T.match(/[^\n]{0,20}(?:必填|请选择|请填写|不通过|失败)[^\n]{0,30}/g) || []).slice(0, 5),
  };
});
console.log("状态:", JSON.stringify(state));

if (state.gen && !state.form) {
  // 拿按钮坐标，用真实鼠标点
  const box = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button,[class*=btn],div,span")]
      .filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "确认信息，继续生成")[0];
    if (!b) return null;
    b.scrollIntoView({ block: "center" });
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), h: Math.round(r.height), disabled: b.disabled === true };
  });
  console.log("按钮:", JSON.stringify(box));
  if (box) {
    await page.mouse.click(box.x, box.y, { label: "确认信息继续生成" });
    console.log("已坐标点击");
  }
}

// 等生成（最多 360s）
let ready = false;
for (let i = 1; i <= 72; i++) {
  await page.waitForTimeout(5000);
  const s = await page.evaluate(() => {
    const T = document.body.innerText || "";
    return {
      form: /基础信息/.test(T) && /图文信息/.test(T),
      gen: /确认信息，继续生成/.test(T),
      busy: /识别中|解析中|裁切中|正在处理|生成中/.test(T),
    };
  });
  if (i % 4 === 0) console.log(`  [${i * 5}s] form=${s.form} gen=${s.gen} busy=${s.busy}`);
  if (s.form) { ready = true; break; }
  if (!s.gen && !s.busy && i >= 4) { console.log("  按钮消失且不忙，可能已提交"); break; }
  // 每 30 秒补点一次
  if (i % 6 === 0 && s.gen) {
    const box = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const b = [...document.querySelectorAll("button,[class*=btn],div,span")]
        .filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "确认信息，继续生成")[0];
      if (!b) return null;
      b.scrollIntoView({ block: "center" });
      const r = b.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    });
    if (box) { await page.mouse.click(box.x, box.y, { label: "再次确认继续生成" }); console.log(`  [${i * 5}s] 补点一次`); }
  }
}
console.log("表单就绪:", ready);

if (!ready) {
  const shot = await page.screenshot({ path: `${ROOT}/v7/shot-r3-stuck.png` });
  console.log("未进入表单，截图:", shot);
  const txt = await page.evaluate(() => (document.body.innerText || "").slice(0, 800));
  console.log("--- 页面文字 ---\n" + txt);
}

// 抓表单结构
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
    mainImg: around(/主图3:4|上传主图|商品主图/, 2, 6),
    btns: [...new Set(btns)].slice(0, 50),
    files: files.slice(0, 45), fileCount: files.length,
  };
});
console.log("\ntabs:", JSON.stringify(form.tabs));
console.log("导购短标题:", JSON.stringify(form.shortTitle));
console.log("发货:", JSON.stringify(form.ship));
console.log("规格:", JSON.stringify(form.spec));
console.log("主图区:", JSON.stringify(form.mainImg));
console.log("按钮:", JSON.stringify(form.btns));
console.log("fileCount:", form.fileCount);
form.files.forEach((f) => console.log(`  [${f.i}] ${f.accept} mul=${f.multiple}`));
await fs.writeFile(`${ROOT}/v7/r3-form.json`, JSON.stringify(form, null, 2), "utf8");
