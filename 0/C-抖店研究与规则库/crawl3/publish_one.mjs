// ============================================================
// 抖店一键上架 · 完整自动化脚本
// 用法：
//   & 'C:\Program Files\Citro Labs\ego lite\Application\0.6.2.5\ego-browser.exe' nodejs `
//     -e "await import('file:///G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3/publish_one.mjs')"
//
// 输入：同目录 plan.json（采集数据）
//   {
//     "title": "商品标题（可超长，脚本自动截 30 汉字）",
//     "main":  ["主图1.jpg", ...],         // 最多 5 张进主图区，多的自动进详情区
//     "detail":["详情1.jpg", ...],
//     "price": 550, "stock": 29, "weight": 40,
//     "cat": ["家具","几类","茶几/边几"],   // 必须是店铺已开通的类目
//     "brand": "LZL",                     // 可选，默认选第一个
//     "wood": "榆木",                     // 支撑件木种，可选
//     "install": "不提供上门安装"          // 安装模式
//   }
//
// 输出：<OUT>/result-<时间戳>.json（含商品ID / 状态 / 截图路径）
// ============================================================
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const OUT = ROOT + "/runs";
await fs.mkdir(OUT, { recursive: true });
const T0 = Date.now();
const ts = () => new Date().toISOString().slice(11, 19);
const log = (...a) => console.log(`[${ts()}]`, ...a);

const plan = JSON.parse(await fs.readFile(`${ROOT}/plan.json`, "utf8"));
const SPACE = `抖店上架-${plan.id || "auto"}`;
const task = await taskSpace(SPACE);
const page = task.page("p1");
log("space", task.spaceId, "| page", page.label);

const shot = async (name) => {
  const p = `${OUT}/${plan.id || "item"}-${name}.png`;
  await page.screenshot({ path: p }).catch(() => null);
  return p;
};

// 通用：DOM 事件点击（写在每个 evaluate 内部，ego 不共享作用域）
const DC = `const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};`;

// ---------- 1. 传图 ----------
await page.goto("https://fxg.jinritemai.com/ffa/g/create");
await page.waitForFunction(
  () => document.querySelectorAll("input[type=file]").length > 0 && /点击或拖拽|已上传/.test(document.body.innerText || ""),
  undefined, { timeout: 60000 }
);
log("表单就绪");

const files = [...(plan.main || []), ...(plan.detail || [])].filter(Boolean).map((p) => p.replace(/\\/g, "/"));
for (const f of files) await fs.access(f);
log("投递", files.length, "张");
await page.setInputFiles("input[type=file] >> nth=0", files);

let uploaded = 0;
for (let i = 1; i <= 80; i++) {
  await page.waitForTimeout(1500);
  const s = await page.evaluate(() => {
    const t = document.body.innerText || "";
    return { n: Number((t.match(/已上传 (\d+)/) || [0, 0])[1]), busy: /上传中|解析中/.test(t), full: /容量已满/.test(t) };
  });
  if (s.full) throw new Error("素材库容量已满，无法上传");
  if (s.n >= files.length && !s.busy) { uploaded = s.n; break; }
}
log("上传完成", uploaded, "/", files.length);
if (uploaded < files.length) log("[!] 未全部上传完成，继续尝试");

// ---------- 2. 生成 ----------
await page.click('text="下一步，生成商品"').catch(() => {});
let rec = false;
for (let i = 1; i <= 60; i++) {
  await page.waitForTimeout(2000);
  const s = await page.evaluate(() => /已识别并分类商品材料|确认信息，继续生成/.test(document.body.innerText || ""));
  if (s) { rec = true; log("已识别", i * 2, "s"); break; }
}
if (!rec) throw new Error("AI 识别未完成");

// ---------- 3. 标题（抖店 60 字符 = 30 汉字） ----------
const title30 = Array.from(plan.title || "").slice(0, 30).join("");
await page.evaluate((v) => {
  const all = [...document.querySelectorAll("input,textarea")].filter((e) => e.offsetParent !== null);
  const el = all.find((e) => /2-60/.test(e.placeholder || "")) || all.find((e) => e.maxLength === 60);
  if (!el) return;
  el.focus();
  const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
  s.call(el, v);
  for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
}, title30);
log("标题", Array.from(plan.title || "").length, "汉字 →", Array.from(title30).length);
await page.waitForTimeout(1500);

// ---------- 4. 多图裁切 ----------
const cut = await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const e = [...document.querySelectorAll("button,a,span,div")]
    .filter((x) => vis(x) && x.children.length === 0 && (x.innerText || "").trim() === "多图裁切")[0];
  if (!e) return false;
  dc(e); return true;
}, DC);
log("多图裁切:", cut ? "已点开" : "无此按钮（图片比例都对）");
await page.waitForTimeout(4000);
if (cut) {
  await page.evaluate((src) => {
    eval(src);
    const vis = (e) => e.offsetParent !== null;
    const e = [...document.querySelectorAll("button,a,span,div")]
      .filter((x) => vis(x) && x.children.length === 0 && /应用图片/.test((x.innerText || "").trim()))[0];
    if (e) dc(e);
  }, DC);
  await page.waitForTimeout(5000);
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(1200);
}

// ---------- 5. 手动选类目（关键：必须是已开通类目） ----------
const catPath = plan.cat || ["家具", "几类", "茶几/边几"];
const leaf = catPath[catPath.length - 1];
await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const e = [...document.querySelectorAll("a,span,div,button")]
    .filter((x) => vis(x) && x.children.length === 0 && /手动选择类目/.test((x.innerText || "").trim()))[0];
  if (e) dc(e);
}, DC);
await page.waitForTimeout(4000);

// 「最近创建」里如果直接有，就点它
const hitDirect = await page.evaluate((src, leafName) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const hits = [...document.querySelectorAll("div,span,li,a,button")]
    .filter((e) => vis(e) && new RegExp(leafName.replace(/\//g, "\\/")).test(e.innerText || "") && (e.innerText || "").length < 80);
  if (!hits.length) return false;
  hits.sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
  dc(hits[0]);
  return true;
}, DC, leaf);
log("类目「" + leaf + "」:", hitDirect ? "最近创建直接选中" : "需搜索选择");
await page.waitForTimeout(3500);

// 若没命中，用搜索框
if (!hitDirect) {
  const box = await page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("input")].filter(vis).find((e) => /类目关键词搜索|搜索一级类目/.test(e.placeholder || ""));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });
  if (box) {
    await page.mouse.click(box.x, box.y, { label: "聚焦类目搜索" });
    await page.waitForTimeout(800);
    await page.keyboard.type(leaf, { delay: 60 }).catch(() => {});
    await page.waitForTimeout(2500);
    await page.evaluate((src, leafName) => {
      eval(src);
      const vis = (e) => e.offsetParent !== null;
      const hits = [...document.querySelectorAll("div,span,li,a")]
        .filter((e) => vis(e) && e.children.length === 0 && (e.innerText || "").trim() === leafName);
      if (hits.length) dc(hits[0]);
    }, DC, leaf);
    await page.waitForTimeout(2500);
  }
}

// 点「确认」
await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const b = [...document.querySelectorAll("button")].filter((e) => vis(e) && (e.innerText || "").trim() === "确认" && !e.disabled)[0];
  if (b) dc(b);
}, DC);
await page.waitForTimeout(4000);
const catNow = await page.evaluate(() => {
  const t = document.body.innerText || "";
  const i = t.indexOf("商品类目");
  return i >= 0 ? t.slice(i, i + 70).replace(/\s+/g, " ") : "";
});
log("类目:", catNow);

// ---------- 6. 确认生成 ----------
await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const b = [...document.querySelectorAll("button,[class*=btn]")].filter((e) => vis(e) && (e.innerText || "").trim() === "确认信息，继续生成" && !e.disabled)[0];
  if (b) dc(b);
}, DC);
let editing = false;
for (let i = 1; i <= 50; i++) {
  await page.waitForTimeout(2500);
  const s = await page.evaluate(() => /填写检查|商品规格|价格与库存/.test(document.body.innerText || ""));
  if (s) { editing = true; log("进编辑页", i * 2.5, "s"); break; }
}
if (!editing) throw new Error("未能进入编辑页");
await page.waitForTimeout(4000);

// ---------- 7. 品牌（键盘法：点框 → ArrowDown → Enter） ----------
const bb = await page.evaluate(() => {
  const vis = (e) => e.offsetParent !== null;
  const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vis(e) && e.children.length === 0 && (e.innerText || "").trim() === "品牌");
  for (const lab of labs) {
    let p = lab;
    for (let i = 0; i < 8 && p; i++) {
      const sel = p.querySelector && p.querySelector(".ecom-g-select");
      if (sel && vis(sel)) {
        sel.scrollIntoView({ block: "center" });
        const r = sel.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), cur: (sel.innerText || "").trim().slice(0, 24) };
      }
      p = p.parentElement;
    }
  }
  return null;
});
if (bb && bb.cur === "请选择") {
  await page.waitForTimeout(800);
  await page.mouse.click(bb.x, bb.y, { label: "聚焦品牌" });
  await page.waitForTimeout(1500);
  await page.keyboard.press("ArrowDown").catch(() => {});
  await page.waitForTimeout(1200);
  await page.keyboard.press("Enter").catch(() => {});
  await page.waitForTimeout(2500);
}
log("品牌:", await page.evaluate(() => {
  const vis = (e) => e.offsetParent !== null;
  const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vis(e) && e.children.length === 0 && (e.innerText || "").trim() === "品牌");
  for (const lab of labs) {
    let p = lab;
    for (let i = 0; i < 8 && p; i++) {
      const s = p.querySelector && p.querySelector(".ecom-g-select");
      if (s && vis(s)) return (s.innerText || "").trim().slice(0, 26);
      p = p.parentElement;
    }
  }
  return null;
}));

// ---------- 8. 价格 / 库存 / 重量 ----------
const filled = await page.evaluate((p) => {
  const vis = (e) => e.offsetParent !== null;
  const setV = (el, v) => {
    el.focus();
    const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    s.call(el, String(v));
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  };
  const all = [...document.querySelectorAll("input")].filter(vis);
  const out = {};
  const price = all.find((e) => /请输入/.test(e.placeholder || "") && !/库存|erp|折扣|数字/.test(e.placeholder || "") && (e.className || "").toString().includes("number"));
  const stock = all.find((e) => /请输入库存/.test(e.placeholder || ""));
  const weight = all.find((e) => /0.001到9999.999/.test(e.placeholder || ""));
  if (price) { setV(price, p.price); out.price = price.value; }
  if (stock) { setV(stock, p.stock); out.stock = stock.value; }
  if (weight) { setV(weight, p.weight); out.weight = weight.value; }
  return out;
}, { price: plan.price || 999, stock: plan.stock || 10, weight: plan.weight || 40 });
log("填值:", JSON.stringify(filled));
await page.waitForTimeout(2500);

// ---------- 9. 支撑件木种（若必填） ----------
const needWood = await page.evaluate(() => /支撑件木种\s*\n?错误|支撑件木种[\s\S]{0,40}该项为必填/.test(document.body.innerText || ""));
if (needWood) {
  const wb = await page.evaluate((src) => {
    eval(src);
    const vis = (e) => e.offsetParent !== null;
    const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vis(e) && e.children.length === 0 && /支撑件木种/.test((e.innerText || "").trim()));
    for (const lab of labs) {
      let p = lab;
      for (let i = 0; i < 8 && p; i++) {
        const sel = p.querySelector && p.querySelector(".ecom-g-select");
        if (sel && vis(sel)) { sel.scrollIntoView({ block: "center" }); const r = sel.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; }
        p = p.parentElement;
      }
    }
    return null;
  }, DC);
  if (wb) {
    await page.mouse.click(wb.x, wb.y, { label: "聚焦木种" });
    await page.waitForTimeout(1500);
    await page.keyboard.press("ArrowDown").catch(() => {});
    await page.waitForTimeout(1000);
    await page.keyboard.press("Enter").catch(() => {});
    await page.waitForTimeout(2000);
    log("木种已选");
  }
}

// ---------- 10. 安装模式 ----------
const ins = await page.evaluate((src, mode) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const hits = [...document.querySelectorAll("label,span,div,li")].filter((e) => vis(e) && e.children.length === 0 && (e.innerText || "").trim() === mode);
  if (!hits.length) return false;
  dc(hits[0]); return true;
}, DC, plan.install || "不提供上门安装");
log("安装模式:", ins ? "已选" : "无需填写");
await page.waitForTimeout(2500);

// ---------- 11. 填写检查 + 发布 ----------
await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const b = [...document.querySelectorAll("button")].filter((e) => vis(e) && (e.innerText || "").trim() === "填写检查")[0];
  if (b) dc(b);
}, DC);
await page.waitForTimeout(6000);

const pre = await page.evaluate(() => {
  const vis = (e) => e.offsetParent !== null;
  const errs = [...document.querySelectorAll('[class*="has-error"]')].filter(vis).map((e) => (e.innerText || "").replace(/\s+/g, " ").trim().slice(0, 70));
  const pub = [...document.querySelectorAll("button")].filter((e) => vis(e) && (e.innerText || "").trim() === "发布商品")[0];
  return { errs: errs.slice(0, 10), pubDisabled: pub ? pub.disabled : null };
});
log("发布前 errs:", JSON.stringify(pre.errs));
await shot("before-publish");

if (pre.pubDisabled !== false) throw new Error("发布按钮不可用：" + JSON.stringify(pre.errs));

await page.evaluate((src) => {
  eval(src);
  const vis = (e) => e.offsetParent !== null;
  const b = [...document.querySelectorAll("button")].filter((e) => vis(e) && (e.innerText || "").trim() === "发布商品")[0];
  if (b) { b.scrollIntoView({ block: "center" }); dc(b); }
}, DC);
log("已点发布");

let published = false;
for (let i = 1; i <= 20; i++) {
  await page.waitForTimeout(2500);
  const s = await page.evaluate(() => {
    const t = document.body.innerText || "";
    return { ok: /提交成功|发布成功|上架成功/.test(t), fail: /发布失败|提交失败/.test(t) };
  });
  if (s.ok) { published = true; log("发布成功"); break; }
  if (s.fail) throw new Error("平台返回失败");
}
if (!published) log("[!] 未确认成功，请看截图");

// ---------- 12. 验证：查列表拿商品 ID ----------
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(6000);
const result = await page.evaluate((title) => {
  const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
  const mine = rows.find((tr) => (tr.innerText || "").includes(title.slice(0, 12)));
  if (!mine) return { found: false };
  const txt = (mine.innerText || "").replace(/\s+/g, " ");
  return {
    found: true,
    id: (txt.match(/ID[：:]\s*(\d{10,})/) || [])[1],
    price: (txt.match(/￥[\d.]+/) || [])[0],
    status: (txt.match(/(售卖中|审核中|已售罄|已下架)/g) || []).join("/"),
    time: (txt.match(/\d{4}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}/) || [])[0],
  };
}, title30);
log("列表核对:", JSON.stringify(result));
await shot("final");

const report = {
  item: plan.id, title: title30, cat: catPath.join(" > "),
  published, list: result,
  elapsed_sec: Math.round((Date.now() - T0) / 1000),
  at: new Date().toISOString(),
};
await fs.writeFile(`${OUT}/result-${plan.id || "item"}-${Date.now()}.json`, JSON.stringify(report, null, 2), "utf8");
log("完成，用时", report.elapsed_sec, "秒");
log(JSON.stringify(report));
console.log("PUBLISH_ONE_DONE");
