// 第 6 轮：表单已就绪，抓「基础信息/图文信息/价格库存」三处的完整结构
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const task = await taskSpace(5);
const page = task.page("p1");
console.log("url =", await page.url());

const grab = () => page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const T = document.body.innerText || "";
  const lines = T.split("\n").map((s) => s.trim()).filter(Boolean);
  const around = (re, before = 1, after = 10) => {
    const i = lines.findIndex((s) => re.test(s));
    return i < 0 ? [] : lines.slice(Math.max(0, i - before), i + after);
  };
  // 所有可点元素（含 AI 图标按钮）
  const clickables = [...document.querySelectorAll("button,span,div,i,svg,a")]
    .filter((e) => vv(e) && e.children.length === 0)
    .map((e) => {
      const t = (e.innerText || "").trim();
      const r = e.getBoundingClientRect();
      const pe = getComputedStyle(e).pointerEvents;
      return t && t.length < 20 ? { t, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), pe } : null;
    })
    .filter(Boolean);
  // 图标按钮（无文字但有 title / aria-label）
  const iconBtns = [...document.querySelectorAll("[title],[aria-label]")]
    .filter(vv).map((e) => ({ title: e.getAttribute("title") || "", aria: e.getAttribute("aria-label") || "", x: Math.round(e.getBoundingClientRect().x), y: Math.round(e.getBoundingClientRect().y) }))
    .filter((o) => o.title || o.aria).slice(0, 20);
  const files = [...document.querySelectorAll("input[type=file]")].map((e, i) => {
    const r = e.getBoundingClientRect();
    return { i, accept: e.accept || "", multiple: e.multiple, y: Math.round(r.y) };
  });
  return {
    tabs: [...new Set(lines.filter((s) => /^(基础信息|图文信息|价格库存|属性信息|服务与履约|其他信息)$/.test(s)))],
    shortTitle: around(/导购短标题|短标题/),
    titleArea: around(/商品标题|请输入2-60/),
    ship: around(/现货发货时间|发货模式/, 2, 12),
    spec: around(/规格类型|添加规格|规格值|销售规格/, 2, 14),
    mainImg: around(/主图3:4|上传主图|商品主图/, 2, 8),
    clickables: clickables.slice(0, 60),
    iconBtns,
    files: files.slice(0, 45), fileCount: files.length,
  };
});

await page.waitForTimeout(3000);
const base = await grab();
console.log("\n=== tabs ===", JSON.stringify(base.tabs));
console.log("=== 导购短标题区 ===", JSON.stringify(base.shortTitle));
console.log("=== 标题区 ===", JSON.stringify(base.titleArea));
console.log("=== 发货区 ===", JSON.stringify(base.ship));
console.log("=== 规格区 ===", JSON.stringify(base.spec));
console.log("=== 主图区 ===", JSON.stringify(base.mainImg));
console.log("=== 图标按钮 ===", JSON.stringify(base.iconBtns));
console.log("=== file inputs ===", base.fileCount);
base.files.forEach((f) => console.log(`   [${f.i}] ${f.accept} mul=${f.multiple} y=${f.y}`));
console.log("=== 可点元素（前 55）===");
base.clickables.slice(0, 55).forEach((c) => console.log(`   "${c.t}" (${c.x},${c.y}) ${c.w}x${c.h} pe=${c.pe}`));

await fs.writeFile(`${ROOT}/v7/r6-base.json`, JSON.stringify(base, null, 2), "utf8");
console.log("\n已存 r6-base.json");
