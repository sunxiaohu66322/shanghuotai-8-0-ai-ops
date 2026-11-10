/**
 * 抖店批量上货引擎 v4 · 边跑边采错
 * ============================================================
 * 主人 2026-11-02 09:15 的架构：
 *   ① 脚本先全部跑过去，不管对错
 *   ② 每次操作后把页面红字/待完善信息原样收集
 *   ③ 跑完统一入库（错误库.py）
 *   ④ AI 回头按提示逐条补
 *   ⑤ 修法沉淀成 lessons，脚本下次照方抓药
 *
 * 参数：params.json
 *   { "batch":"B1", "items":["1391","1454","1526","1531"],
 *     "space":"抖店批量上货", "stopOnFail":false, "dryPublish":false }
 *
 * 产出：
 *   errors-<batch>.json   每次采到的错误（给 错误库.py 入库）
 *   runs-<batch>.json     每件商品走到哪个阶段
 *   shots/<batch>-<item>-<stage>.png
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");

const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const SHOTS = path.join(ROOT, "shots");

let P = { batch: "B0", items: ["1531"], space: "抖店批量上货", stopOnFail: false, dryPublish: false };
try { P = { ...P, ...JSON.parse(await fs.readFile(path.join(ROOT, "params.json"), "utf8")) }; } catch {}

const BATCH = P.batch;
const errors = [];
const runs = [];
const DC = `const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};`;
const vis = `const vis=(e)=>e.offsetParent!==null;`;

const ts = () => new Date().toISOString().slice(11, 19);
const log = (...a) => console.log(`[${ts()}]`, ...a);

function cat(field, msg) {
  const s = `${field || ""} ${msg || ""}`;
  if (/品牌/.test(s)) return "brand";
  if (/类目|开通/.test(s)) return "category";
  if (/主图|视频|图片|详情图/.test(s)) return "media";
  if (/该项为必填|请输入|必填/.test(s)) return "required";
  if (/不能重复|格式|超出|限制|不支持/.test(s)) return "format";
  return "other";
}
function rec(item, stage, field, msg) {
  const m = (msg || "").replace(/\s+/g, " ").trim();
  if (!m || m.length > 200) return;
  const f = (field || "").trim() || "unknown";
  if (errors.some((e) => e.item === item && e.field === f && e.msg === m)) return;
  errors.push({ item, stage, field: f, msg: m, category: cat(f, m) });
}

const SNAP = () => {
  const out = { panel: [], inline: [], red: [], todoCount: null };
  const vv = (e) => e.offsetParent !== null;
  const t = document.body.innerText || "";
  const idx = t.indexOf("待完善信息");
  if (idx >= 0) {
    const seg = t.slice(idx, idx + 2200);
    const lines = seg.split("\n").map((s) => s.trim()).filter(Boolean);
    for (let i = 1; i < lines.length - 1; i++) {
      if (lines[i] === "错误") {
        const f = lines[i - 1], m = lines[i + 1];
        if (f && m && !/^\d+$/.test(f)) out.panel.push({ field: f, msg: m });
      }
    }
    for (let i = 1; i < lines.length - 1; i++) {
      if (/^商品/.test(lines[i + 1]) && /^(主图视频|主图|白底图|商品详情)$/.test(lines[i])) {
        out.panel.push({ field: lines[i], msg: lines[i + 1] });
      }
    }
    const m2 = seg.slice(0, 40).replace(/\n/g, "|").match(/待完善信息\|(\d+)/);
    if (m2) out.todoCount = parseInt(m2[1], 10);
  }
  for (const e of document.querySelectorAll('[class*="has-error"]')) {
    if (!vv(e)) continue;
    const s = (e.innerText || "").replace(/\s+/g, " ").trim();
    if (s.length < 2 || s.length > 150) continue;
    out.inline.push(s);
  }
  for (const e of document.querySelectorAll("div,span,p")) {
    if (!vv(e) || e.children.length > 0) continue;
    const s = (e.innerText || "").trim();
    if (s.length < 4 || s.length > 110) continue;
    const mm = getComputedStyle(e).color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (mm && +mm[1] >= 150 && +mm[2] < 100 && +mm[3] < 100) out.red.push(s);
  }
  return out;
};

async function harvest(page, item, stage) {
  let s;
  try { s = await page.evaluate(SNAP); } catch (e) { return 0; }
  const before = errors.length;
  for (const p of s.panel || []) rec(item, stage, p.field, p.msg);
  for (const x of s.inline || []) rec(item, stage, "表单", x);
  const n = errors.length - before;
  log(`   · harvest[${stage}] todo=${s.todoCount} panel=${(s.panel || []).length} inline=${(s.inline || []).length} red=${(s.red || []).length} +${n}`);
  return n;
}
async function shot(page, item, stage) {
  try { await page.screenshot({ path: path.join(SHOTS, `${BATCH}-${item}-${stage}.png`) }); } catch {}
}
async function dump() {
  await fs.writeFile(path.join(ROOT, `errors-${BATCH}.json`), JSON.stringify(errors, null, 2), "utf8");
  await fs.writeFile(path.join(ROOT, `runs-${BATCH}.json`), JSON.stringify(runs, null, 2), "utf8");
}

function adapt(raw, id) {
  const skus = (raw.skus || []).map((s) => ({
    name: s.name || (Array.isArray(s.spec) ? s.spec.join("/") : "") || "",
    price: Number(s.price) || 0,
    stock: Number(s.stock) || 0,
    img: s.img || null,
    code: s.code || "",
  }));
  return {
    id,
    title: raw.title || "",
    main: (raw.main || []).filter(Boolean),
    detail: (raw.detail || []).filter(Boolean),
    price: raw.price || (skus[0] && skus[0].price) || 550,
    stock: raw.stock || (skus[0] && skus[0].stock) || 20,
    weight: 10,
    cat: raw.cat || ["家具", "几类", "茶几/边几"],
    brand: raw.brand || "LZL",
    wood: raw.wood || null,
    install: raw.install || "不提供上门安装",
    skus,
  };
}

function setRun(item, patch) {
  let r = runs.find((x) => x.item === item);
  if (!r) { r = { item, stage: "queued" }; runs.push(r); }
  Object.assign(r, patch);
}

// ============ 单件完整流程 ============
async function publishOne(page, plan) {
  const item = plan.id;
  const rec2 = { item, title: (plan.title || "").slice(0, 30), steps: [] };
  const step = (s) => { rec2.steps.push(s); setRun(item, { stage: s }); };

  step("open");
  await page.goto("https://fxg.jinritemai.com/ffa/g/create");
  await page.waitForFunction(
    () => document.querySelectorAll("input[type=file]").length > 0 && /点击或拖拽|已上传/.test(document.body.innerText || ""),
    undefined, { timeout: 60000 }
  );
  await harvest(page, item, "open-empty");

  // 1) 传图
  const files = [...plan.main, ...plan.detail].map((p) => p.replace(/\\/g, "/"));
  for (const f of files) await fs.access(f).catch(() => { rec(item, "upload", "图片文件", `本地不存在 ${f}`); });
  const exist = [];
  for (const f of files) { try { await fs.access(f); exist.push(f); } catch {} }
  log(`   投递 ${exist.length} 张`);
  if (exist.length) await page.setInputFiles("input[type=file] >> nth=0", exist);

  let up = 0;
  for (let i = 1; i <= 80; i++) {
    await page.waitForTimeout(1500);
    const s = await page.evaluate(() => {
      const t = document.body.innerText || "";
      return { n: Number((t.match(/已上传 (\d+)/) || [0, 0])[1]), busy: /上传中|解析中/.test(t), full: /容量已满/.test(t) };
    });
    if (s.full) { rec(item, "upload", "素材库", "容量已满无法上传"); break; }
    if (s.n >= exist.length && !s.busy) { up = s.n; break; }
  }
  log(`   上传 ${up}/${exist.length}`);
  step("uploaded");

  // 2) 生成
  await page.click('text="下一步，生成商品"').catch(() => {});
  let ok = false;
  for (let i = 1; i <= 60; i++) {
    await page.waitForTimeout(2000);
    if (await page.evaluate(() => /已识别并分类商品材料|确认信息，继续生成/.test(document.body.innerText || ""))) { ok = true; break; }
  }
  if (!ok) { rec(item, "recognize", "AI识别", "超过120秒未完成识别"); await shot(page, item, "recognize-timeout"); throw new Error("识别超时"); }
  step("recognized");

  // 3) 标题
  const t30 = Array.from(plan.title || "").slice(0, 30).join("");
  await page.evaluate((v) => {
    const all = [...document.querySelectorAll("input,textarea")].filter((e) => e.offsetParent !== null);
    const el = all.find((e) => /2-60/.test(e.placeholder || "")) || all.find((e) => e.maxLength === 60);
    if (!el) return;
    el.focus();
    Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }, t30);
  await page.waitForTimeout(1500);
  step("titled");

  // 4) 多图裁切
  const cut = await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const e = [...document.querySelectorAll("button,a,span,div")].filter((x) => vv(x) && x.children.length === 0 && (x.innerText || "").trim() === "多图裁切")[0];
    if (!e) return false; dc(e); return true;
  });
  if (cut) {
    await page.waitForTimeout(4000);
    await page.evaluate(() => {
      const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
      const e = [...document.querySelectorAll("button,a,span,div")].filter((x) => vv(x) && x.children.length === 0 && /应用图片/.test((x.innerText || "").trim()))[0];
      if (e) dc(e);
    });
    await page.waitForTimeout(5000);
    await page.keyboard.press("Escape").catch(() => {});
    await page.waitForTimeout(1200);
  }
  step("cropped");

  // 5) 类目
  const catPath = plan.cat, leaf = catPath[catPath.length - 1];
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const e = [...document.querySelectorAll("a,span,div,button")].filter((x) => vv(x) && x.children.length === 0 && /手动选择类目/.test((x.innerText || "").trim()))[0];
    if (e) dc(e);
  });
  await page.waitForTimeout(4000);
  const direct = await page.evaluate((leafName) => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const hits = [...document.querySelectorAll("div,span,li,a,button")]
      .filter((e) => vv(e) && new RegExp(leafName.replace(/\//g, "\\/")).test(e.innerText || "") && (e.innerText || "").length < 80);
    if (!hits.length) return false;
    hits.sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
    dc(hits[0]); return true;
  }, leaf);
  await page.waitForTimeout(3500);
  if (!direct) {
    const box = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const el = [...document.querySelectorAll("input")].filter(vv).find((e) => /类目关键词搜索|搜索一级类目/.test(e.placeholder || ""));
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    });
    if (box) {
      await page.mouse.click(box.x, box.y);
      await page.waitForTimeout(800);
      await page.keyboard.type(leaf, { delay: 60 }).catch(() => {});
      await page.waitForTimeout(2500);
      await page.evaluate((n) => {
        const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
        const h = [...document.querySelectorAll("div,span,li,a")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === n);
        if (h.length) dc(h[0]);
      }, leaf);
      await page.waitForTimeout(2500);
    }
  }
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "确认" && !e.disabled)[0];
    if (b) dc(b);
  });
  await page.waitForTimeout(4000);
  const catNow = await page.evaluate(() => {
    const t = document.body.innerText || ""; const i = t.indexOf("商品类目");
    return i >= 0 ? t.slice(i, i + 60).replace(/\s+/g, " ") : "";
  });
  log(`   类目: ${catNow}`);
  if (!catNow.includes(leaf)) rec(item, "category", "商品类目", `未选中「${leaf}」，当前=${catNow.slice(0, 40)}`);
  step("categorized");

  // 6) 确认生成
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button,[class*=btn]")].filter((e) => vv(e) && (e.innerText || "").trim() === "确认信息，继续生成" && !e.disabled)[0];
    if (b) dc(b);
  });
  let editing = false;
  for (let i = 1; i <= 80; i++) {
    await page.waitForTimeout(2500);
    const s = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const tabs = ["基础信息", "图文信息", "属性信息", "价格库存"];
      const found = tabs.filter((n) =>
        [...document.querySelectorAll("div,span,a,li")].some(
          (e) => vv(e) && (e.innerText || "").trim() === n && e.getBoundingClientRect().height < 60
        )
      );
      return {
        tabFound: found,
        stillParsing: /继续解析你上传的材料/.test(document.body.innerText || ""),
        genBtn: (() => {
          const b = [...document.querySelectorAll("button")].filter((x) => vv(x) && (x.innerText || "").trim() === "确认信息，继续生成")[0];
          return b ? (b.disabled ? "disabled" : "enabled") : "absent";
        })(),
      };
    });
    if (s.tabFound.length >= 3) { editing = true; log(`   进编辑页 ${i * 2.5}s，tab=${s.tabFound.join("/")}`); break; }
    if (i % 8 === 0) log(`   [wait] ${i * 2.5}s tabs=${s.tabFound.length} gen=${s.genBtn} parsing=${s.stillParsing}`);
  }
  if (!editing) { rec(item, "generate", "确认生成", "超过200秒未进入编辑页（顶部 tab 栏未出现）"); await shot(page, item, "generate-timeout"); throw new Error("生成超时"); }
  await page.waitForTimeout(4000);
  await harvest(page, item, "editing-empty");
  step("editing");

  // 6.5) 切到「属性信息」tab（品牌/属性都在这页）
  const goTab = async (name) => {
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(900);
    const r = await page.evaluate((n) => {
      const vv = (e) => e.offsetParent !== null;
      const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};
      const all = [...document.querySelectorAll("div,span,a,li,button,[role=tab]")];
      // 1. 精确文本且小高度
      let hits = all
        .filter((e) => vv(e) && (e.innerText || "").trim() === n)
        .map((e) => ({ e, r: e.getBoundingClientRect() }))
        .filter((o) => o.r.y >= -5 && o.r.y < 220 && o.r.height > 8 && o.r.height < 70)
        .sort((a, b) => a.r.y - b.r.y);
      if (hits.length) { dc(hits[0].e); return { ok: true, y: Math.round(hits[0].r.y), mode: "exact" }; }
      // 2. 包含文本（tab 里可能有图标/角标）
      hits = all
        .filter((e) => vv(e) && (e.innerText || "").trim().includes(n) && (e.innerText || "").trim().length < n.length + 6)
        .map((e) => ({ e, r: e.getBoundingClientRect() }))
        .filter((o) => o.r.y >= -5 && o.r.y < 220 && o.r.height > 8 && o.r.height < 70)
        .sort((a, b) => a.r.y - b.r.y);
      if (hits.length) { dc(hits[0].e); return { ok: true, y: Math.round(hits[0].r.y), mode: "contains" }; }
      // 3. 诊断：把所有 y<220、高度 10-60 的可点元素列出来
      const cands = all
        .filter((e) => vv(e) && e.children.length === 0)
        .map((e) => ({ t: (e.innerText || "").trim().slice(0, 14), r: e.getBoundingClientRect() }))
        .filter((o) => o.r.y >= -5 && o.r.y < 220 && o.r.height > 8 && o.r.height < 60 && o.t)
        .map((o) => `${o.t}@${Math.round(o.r.y)}`).slice(0, 25);
      return { ok: false, cands };
    }, name);
    await page.waitForTimeout(3000);
    log(`   tab「${name}」: ${r.ok ? "已切换 y=" + r.y + " (" + r.mode + ")" : "未找到 | 候选: " + (r.cands || []).join(" , ")}`);
    if (!r.ok) rec(item, "tab", name, `找不到页签「${name}」`);
    return r.ok;
  };

  // 7) 品牌（键盘法）
  await goTab("属性信息");
  await harvest(page, item, "tab-attrs");
  const bb = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "品牌");
    for (const lab of labs) {
      let p = lab;
      for (let i = 0; i < 8 && p; i++) {
        const sel = p.querySelector && p.querySelector(".ecom-g-select");
        if (sel && vv(sel)) {
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
    await page.mouse.click(bb.x, bb.y);
    await page.waitForTimeout(1500);
    await page.keyboard.press("ArrowDown").catch(() => {});
    await page.waitForTimeout(1200);
    await page.keyboard.press("Enter").catch(() => {});
    await page.waitForTimeout(2500);
  }
  const brandNow = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "品牌");
    for (const lab of labs) {
      let p = lab;
      for (let i = 0; i < 8 && p; i++) {
        const s = p.querySelector && p.querySelector(".ecom-g-select");
        if (s && vv(s)) return (s.innerText || "").trim().slice(0, 26);
        p = p.parentElement;
      }
    }
    return null;
  });
  log(`   品牌: ${brandNow}`);
  if (!brandNow || brandNow === "请选择") rec(item, "brand", "品牌", "未能选中品牌");
  step("branded");

  // 7.5) 照「待完善信息」逐字段补属性（不预设字段名，AI 每次生成的必填项都不一样）
  const fillAttrSelect = async (labelText, hint) => {
    const wb = await page.evaluate((lt) => {
      const vv = (e) => e.offsetParent !== null;
      const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};
      const labs = [...document.querySelectorAll("div,span,label")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === lt);
      const BAD = /该项为必填|请输入|请选择|最多选|不能重复|^错误$/;
      const out = [];
      for (const lab of labs) {
        let p = lab;
        for (let i = 0; i < 9 && p; i++) {
          const sel = p.querySelector && p.querySelector(".ecom-g-select");
          if (sel && vv(sel)) {
            sel.scrollIntoView({ block: "center" });
            const r = sel.getBoundingClientRect();
            const item = sel.querySelector('[class*="selection-item"]');
            const cur = item ? (item.innerText || "").trim().slice(0, 24) : "";
            out.push({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), cur });
            break;
          }
          // 也可能是普通 input（文本框类属性）
          const inp = p.querySelector && p.querySelector("input:not([type=file])");
          if (inp && vv(inp) && (inp.value || "").trim() === "") {
            const r = inp.getBoundingClientRect();
            if (r.width > 20) { out.push({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), cur: "", isInput: true }); break; }
          }
          p = p.parentElement;
        }
      }
      return out;
    }, labelText);
    if (!wb.length) { log(`   ${hint}: 无对应控件（可能已在别页或非必填）`); return false; }
    // 选第一个「还没值」的
    const target = wb.find((o) => !o.cur || o.cur === "请选择") || wb[0];
    if (!target.cur || target.cur === "请选择") {
      await page.mouse.click(target.x, target.y);
      await page.waitForTimeout(1400);
      await page.keyboard.press("ArrowDown").catch(() => {});
      await page.waitForTimeout(900);
      await page.keyboard.press("Enter").catch(() => {});
      await page.waitForTimeout(1600);
      log(`   ${hint}: 已选`);
      return true;
    }
    log(`   ${hint}: 已有值「${target.cur}」，跳过`);
    return true;
  };

  const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // 循环补 3 轮（每轮补完页面会重算「待完善信息」）
  const MEDIA_RE = /主图视频|商品详情|白底图|主图|图片|视频|包装|重量|尺寸|规格/;
  for (let round = 1; round <= 3; round++) {
    const todo = await page.evaluate(() => {
      const t = document.body.innerText || "";
      const i = t.indexOf("待完善信息");
      if (i < 0) return [];
      const lines = t.slice(i, i + 1800).split("\n").map((s) => s.trim()).filter(Boolean);
      const BAD = /该项为必填|请输入|请选择|最多选|不能重复|^错误$/;
      const out = [];
      for (let k = 1; k < lines.length - 1; k++) {
        if (lines[k] === "错误") out.push(lines[k - 1]);
      }
      for (let k = 1; k < lines.length - 1; k++) {
        if (/^商品/.test(lines[k + 1]) && /^(主图视频|主图|白底图|商品详情)$/.test(lines[k])) out.push(lines[k]);
      }
      return [...new Set(out)];
    });
    const attrs = todo.filter((f) => !MEDIA_RE.test(f));
    log(`   第${round}轮 待完善=${todo.length} 属性类=${attrs.length} [${attrs.join(" / ")}]`);
    if (!attrs.length) break;
    let done = 0;
    for (const f of attrs) {
      const ok = await fillAttrSelect(f, f);
      if (ok) done++;
    }
    await page.waitForTimeout(2000);
    if (!done) break;
  }
  await harvest(page, item, "attrs-filled");
  step("attrs");

  // 8) 价格/库存/重量（在「价格库存」tab）
  await goTab("价格库存");
  await harvest(page, item, "tab-price");
  const filled = await page.evaluate((payload) => {
    const vv = (e) => e.offsetParent !== null;
    const setV = (el, v) => {
      el.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, String(v));
      for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    };
    const out = { rows: [], weight: null };

    // A. SKU 表格逐行填（每行 input[0]=价格 input[1]=库存）
    const rows = [...document.querySelectorAll("tr")].filter((tr) => {
      const ins = [...tr.querySelectorAll("input")].filter(vv);
      return ins.length >= 2 && /[￥¥]/.test(tr.innerText || "");
    });
    rows.forEach((tr, i) => {
      const ins = [...tr.querySelectorAll("input")].filter(vv);
      const sku = payload.skus[i];
      if (!sku) { out.rows.push({ i, skip: "无对应SKU" }); return; }
      setV(ins[0], sku.price);
      setV(ins[1], sku.stock);
      out.rows.push({ i, name: (sku.name || "").slice(0, 12), price: ins[0].value, stock: ins[1].value });
    });

    // B. 重量（在属性页，不在价格页；这里兜底）
    const all = [...document.querySelectorAll("input")].filter(vv);
    const weight = all.find((e) => /0.001到9999.999/.test(e.placeholder || ""));
    if (weight) { setV(weight, payload.weight); out.weight = weight.value; }

    return out;
  }, { skus: plan.skus || [], price: plan.price, stock: plan.stock, weight: plan.weight });
  log(`   填值: ${JSON.stringify(filled)}`);
  if (!filled.rows.length) rec(item, "price", "价格", "价格表格里没找到可填行");
  for (const r of filled.rows) {
    if (r.skip) rec(item, "price", "价格", `第 ${r.i + 1} 行规格在 SKU 数据里没有对应项`);
    else if (!r.price) rec(item, "price", "价格", `第 ${r.i + 1} 行价格未写入`);
    else if (!r.stock) rec(item, "price", "现货库存", `第 ${r.i + 1} 行库存未写入`);
  }
  await page.waitForTimeout(2500);

  // 10) 安装模式
  await page.evaluate((mode) => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const h = [...document.querySelectorAll("label,span,div,li")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === mode);
    if (h.length) dc(h[0]);
  }, plan.install);
  await page.waitForTimeout(2500);
  step("filled");

  // 11) 填写检查 → 采错
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "填写检查")[0];
    if (b) dc(b);
  });
  await page.waitForTimeout(6000);
  await harvest(page, item, "checked");

  // 11.5) 点填写检查后才暴露的属性缺失 → 回属性页补 → 再检查（最多 3 轮）
  const doCheck = async () => {
    await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};
      const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "填写检查")[0];
      if (b) dc(b);
    });
    await page.waitForTimeout(6000);
  };

  for (let r = 1; r <= 3; r++) {
    const pend = await page.evaluate(() => {
      const t = document.body.innerText || "";
      const i = t.indexOf("待完善信息");
      if (i < 0) return [];
      const lines = t.slice(i, i + 1800).split("\n").map((s) => s.trim()).filter(Boolean);
      const BAD = /该项为必填|请输入|请选择|最多选|不能重复|^错误$/;
      const out = [];
      for (let k = 1; k < lines.length - 1; k++) if (lines[k] === "错误") out.push(lines[k - 1]);
      for (let k = 1; k < lines.length - 1; k++) if (/^商品/.test(lines[k + 1]) && /^(主图视频|主图|白底图|商品详情)$/.test(lines[k])) out.push(lines[k]);
      return [...new Set(out)].filter((f) => !/主图视频|商品详情|白底图|主图|图片|视频/.test(f) && !BAD.test(f));
    });
    log(`   [recheck ${r}] 属性类待完善: ${pend.length} [${pend.join(" / ")}]`);
    if (!pend.length) break;
    await goTab("属性信息");
    for (const f of pend) await fillAttrSelect(f, f);
    await doCheck();
    await harvest(page, item, `recheck-${r}`);
  }

  await goTab("价格库存");
  await page.waitForTimeout(1000);
  await harvest(page, item, "final-check");
  await shot(page, item, "checked");

  const pre = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const errs = [...document.querySelectorAll('[class*="has-error"]')].filter(vv).map((e) => (e.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80));
    const pub = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "发布商品")[0];
    return { errs: errs.slice(0, 12), pubDisabled: pub ? pub.disabled : null };
  });
  log(`   发布前 errs=${pre.errs.length} disabled=${pre.pubDisabled}`);

  // 12) 发布（可 dryPublish 只检查不发）
  if (P.dryPublish) {
    rec2.published = false; rec2.dry = true;
    step("dry-checked");
    return rec2;
  }
  if (pre.pubDisabled !== false) {
    rec(item, "publish", "发布按钮", `不可用，errs=${JSON.stringify(pre.errs).slice(0, 160)}`);
    step("blocked");
    return rec2;
  }
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "发布商品")[0];
    if (b) { b.scrollIntoView({ block: "center" }); dc(b); }
  });
  let pub = false;
  for (let i = 1; i <= 20; i++) {
    await page.waitForTimeout(2500);
    const s = await page.evaluate(() => {
      const t = document.body.innerText || "";
      return { ok: /提交成功|发布成功|上架成功/.test(t), fail: /发布失败|提交失败/.test(t) };
    });
    if (s.ok) { pub = true; break; }
    if (s.fail) break;
  }
  log(`   发布 ${pub ? "成功" : "未确认"}`);
  if (!pub) { await harvest(page, item, "publish-failed"); rec(item, "publish", "发布", "未收到成功回执"); }
  step(pub ? "published" : "publish-unconfirmed");
  rec2.published = pub;

  // 13) 查列表拿 ID
  await page.goto("https://fxg.jinritemai.com/ffa/g/list");
  await page.waitForTimeout(6000);
  const lst = await page.evaluate((title) => {
    const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
    const mine = rows.find((tr) => (tr.innerText || "").includes(title.slice(0, 10)));
    if (!mine) return { found: false };
    const txt = (mine.innerText || "").replace(/\s+/g, " ");
    return {
      found: true,
      id: (txt.match(/ID[：:]\s*(\d{10,})/) || [])[1],
      price: (txt.match(/￥[\d.]+/) || [])[0],
      status: (txt.match(/(售卖中|审核中|已售罄|已下架)/g) || []).join("/"),
    };
  }, t30);
  log(`   列表: ${JSON.stringify(lst)}`);
  setRun(item, { pid: lst.id || null, status: lst.status || null });
  if (!lst.found) rec(item, "verify", "商品列表", "列表里找不到刚发布的商品");
  rec2.list = lst;
  step("done");
  return rec2;
}

// ============ 主 ============
async function main() {
  await fs.mkdir(SHOTS, { recursive: true });
  log(`batch=${BATCH} items=${P.items.join(",")} space=${P.space} dry=${P.dryPublish}`);
  const task = await taskSpace(P.space);
  const page = task.page("p1");
  log("space", task.spaceId, "| page", page.label);

  for (const id of P.items) {
    log(`\n===== ${id} =====`);
    setRun(id, { stage: "starting" });
    let raw;
    try {
      raw = JSON.parse(await fs.readFile(path.join(ROOT, `plan-${id}.json`), "utf8"));
    } catch {
      rec(id, "load", "plan", `plan-${id}.json 不存在或解析失败`);
      setRun(id, { stage: "no-plan" });
      await dump();
      if (P.stopOnFail) break;
      continue;
    }
    const plan = adapt(raw, id);
    try {
      await publishOne(page, plan);
    } catch (e) {
      log(`   ✗ ${id} 中断: ${e.message}`);
      rec(id, "fatal", "fatal", e.message.slice(0, 160));
      setRun(id, { stage: "failed", err: e.message.slice(0, 120) });
      await harvest(page, id, "after-fail").catch(() => {});
      await shot(page, id, "failed").catch(() => {});
      if (P.stopOnFail) break;
    }
    await dump();
    await page.waitForTimeout(3000);
  }

  await dump();
  log(`\n===== 汇总 =====`);
  log(`错误 ${errors.length} 条 / 商品 ${runs.length} 件`);
  const byStage = {};
  for (const r of runs) byStage[r.stage] = (byStage[r.stage] || 0) + 1;
  log(JSON.stringify(byStage));
  console.log("BATCH_V4_DONE");
}

await main().catch(async (e) => { console.log("FATAL", e.message); await dump().catch(() => {}); process.exit(1); });
