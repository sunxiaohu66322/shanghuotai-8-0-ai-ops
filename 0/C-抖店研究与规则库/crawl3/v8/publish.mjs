/**
 * 抖店上货主脚本 v8 —— 多维规格（颜色 × 尺寸 乘法）版
 *
 * 本版把 2026-11-02 实测通过的规格链路固化下来：
 *   ✅ 规格类型下拉：30 个可选类型（材质/颜色分类/颜色/尺寸/型号/款式/尺码/组合/套餐…）
 *    ✅ 规格值：每个值一个输入框，输入后 Enter 提交，自动新增下一个空框
 *    ✅ SKU 表：按「各维度值数之积」自动生成行（3 色 × 2 尺寸 = 6 行）—— 这就是抖音的乘法
 *    ✅ 价格/库存：表是 rowspan 结构，颜色单元格跨 N 行，靠 y 坐标映射到数据行
 *    ⏳ 规格图：仅第一组规格可加图，值为 1:1、≤5M；入口是每个规格值右侧 32×32 加号
 *
 * 分流守卫：SKU 数 > 6 的商品不在这里跑，走 Excel 导入（见 route.py 与 excel-import/）
 *
 * 用法：
 *   ego-browser nodejs -e "await import('file:///.../v8/publish.mjs')"
 * 改顶部 PID 换商品；spec-<PID>.json 由 route.py --write 生成。
 */
import fs from "node:fs/promises";

const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const PID = "1436";
const SPACE = 5;
const DRY = true;                       // true = 不点最终「发布」
const MANUAL_MAX_SKU = 6;               // 与 route.py 保持一致

const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);
const readJson = async (p) => JSON.parse(await fs.readFile(p, "utf8"));

const plan = await readJson(`${ROOT}/plan-${PID}.json`);
let spec;
try { spec = await readJson(`${ROOT}/spec-${PID}.json`); }
catch { spec = null; }

// ============ 分流传卫 ============
const skuCount = (plan.skus || []).length;
if (skuCount > MANUAL_MAX_SKU) {
  log(`⛔ 商品 ${PID} 有 ${skuCount} 个 SKU > ${MANUAL_MAX_SKU}，应走 Excel 导入路线。`);
  log(`   先跑: python route.py ${PID} --write  然后走 excel-import/`);
  process.exit(0);
}
if (!spec) log("⚠ 没找到 spec-<PID>.json，将按单规格处理（先跑 route.py --write 可自动拆维度）");

// ============ 通用原语 ============
const state = (page) => page.evaluate(() => {
  const T = document.body.innerText || "";
  return {
    guide: /点击或拖拽文件到此处上传/.test(T),
    form: /基础信息/.test(T) && /图文信息/.test(T),
    gen: /确认信息，继续生成/.test(T),
    busy: /识别中|生成中|裁切中|正在处理|上传中/.test(T),
  };
});

async function goTab(page, name) {
  const r = await page.evaluate((n) => {
    const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
    const vv = (e) => e.offsetParent !== null;
    const cands = [...document.querySelectorAll("div,span,a,li")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === n);
    if (!cands.length) return "absent";
    cands[0].scrollIntoView({ block: "center" });
    dc(cands[0]);
    return "ok";
  }, name);
  await page.waitForTimeout(2500);
  log(`tab「${name}」→ ${r}`);
  return r;
}

/** 点 pointer-events:none 的元素 */
async function clickRobust(page, text) {
  const found = await page.evaluate((t) => {
    const vv = (e) => e.offsetParent !== null;
    const els = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === t);
    if (!els.length) return null;
    const el = els[els.length - 1];
    el.scrollIntoView({ block: "center" });
    el.dataset.agentTarget = "1";
    return { pe: getComputedStyle(el).pointerEvents };
  }, text);
  if (!found) return { ok: false, why: "not-found" };
  await page.evaluate(() => { const el = document.querySelector('[data-agent-target="1"]'); if (el) el.click(); });
  await page.waitForTimeout(1000);
  if (found.pe === "none") {
    await page.evaluate(() => {
      const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
      const el = document.querySelector('[data-agent-target="1"]');
      if (el) { dc(el); if (el.parentElement) dc(el.parentElement); }
    });
    await page.waitForTimeout(1000);
  }
  await page.evaluate(() => { const el = document.querySelector('[data-agent-target="1"]'); if (el) delete el.dataset.agentTarget; });
  return { ok: true, pe: found.pe };
}

// ============ 阶段 ============

async function stageUpload(page) {
  const s = await state(page);
  if (s.form) { log("已在表单，跳过传图"); return; }
  const files = [...(plan.main || []).slice(0, 5), ...(plan.detail || []).slice(0, 13)].map((p) => p.replace(/\\/g, "/"));
  log("投递", files.length, "张");
  const idx = await page.evaluate(() => {
    const ins = [...document.querySelectorAll("input[type=file]")];
    return ins.findIndex((e) => e.multiple && /jpg|png|image/i.test(e.accept || ""));
  });
  if (idx < 0) throw new Error("找不到图片上传入口");
  await page.setInputFiles(`input[type=file] >> nth=${idx}`, files);
  for (let i = 1; i <= 60; i++) {
    await page.waitForTimeout(2000);
    const st = await page.evaluate(() => {
      const t = document.body.innerText || "";
      return { n: Number((t.match(/已上传\s*(\d+)/) || [0, 0])[1]), busy: /上传中|解析中/.test(t) };
    });
    if (st.n >= files.length && !st.busy) { log("已上传", st.n); break; }
  }
}

async function stageGenerate(page) {
  const s = await state(page);
  if (s.form) { log("已在表单，跳过生成"); return; }
  const t30 = Array.from(plan.title || "").slice(0, 30).join("");
  await page.evaluate((v) => {
    const vv = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("input,textarea")].filter(vv).find((e) => /2-60/.test(e.placeholder || "") || e.maxLength === 60);
    if (!el) return false;
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    setter.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    return true;
  }, t30);
  await page.waitForTimeout(1200);
  log("点「确认信息，继续生成」:", JSON.stringify(await clickRobust(page, "确认信息，继续生成")));
  for (let i = 1; i <= 72; i++) {
    await page.waitForTimeout(5000);
    const st = await state(page);
    if (st.form) { log(`表单就绪（${i * 5}s）`); return; }
    if (i % 12 === 0 && st.gen) await clickRobust(page, "确认信息，继续生成");
  }
  throw new Error("生成超时");
}

/**
 * 规格设置（核心）：逐组建规格类型 + 逐个加规格值
 * 依赖：规格区锚点「添加规格图」，类型下拉在「添加规格类型」按钮上方
 */
async function stageSpec(page) {
  const dims = spec?.dims && Object.keys(spec.dims).length ? spec.dims : null;
  if (!dims) { log("无多维规格，跳过（单 SKU 商品不需要建规格组）"); return { skipped: "no-dims" }; }

  const dimList = Object.entries(dims).map(([type, values]) => ({ type, values }));
  log(`准备建 ${dimList.length} 组规格:`, dimList.map((d) => `${d.type}(${d.values.length})`).join(" × "));

  // ---- 第一组：把它默认的「材质」改成目标类型，并清掉自动填的值 ----
  const first = dimList[0];
  await goTab(page, "价格库存");

  // 清掉平台自动填进来的第一个值
  await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const tgt = [...document.querySelectorAll("input[type=text]")].filter(vv).find((e) => /^请输入/.test(e.placeholder || "") && e.value && !/胡桃色|白色|黑色/.test(e.value));
    if (!tgt) return;
    tgt.scrollIntoView({ block: "center" });
    const tb = box(tgt);
    const dels = [...document.querySelectorAll("[class*=delete]")].filter(vv).map((e) => ({ e, ...box(e) }))
      .filter((o) => Math.abs(o.y + o.h / 2 - (tb.y + tb.h / 2)) < 22 && o.x > tb.x + tb.w - 60 && o.x < tb.x + tb.w + 80 && o.w < 30);
    if (dels[0]) for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) dels[0].e.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForTimeout(1500);

  // 改第一组类型（展开 → 点选项，r13 验证过可行）
  const setType = async (groupIndex, wantType) => {
    const opened = await page.evaluate(({ gi }) => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
      if (!btn) return { ok: false };
      const bb = box(btn);
      // 规格组类型下拉 = 按钮上方、宽 150-220 的 select，按 y 从下往上数
      const sels = [...document.querySelectorAll("[class*=ecom-g-select-single]")].filter(vv)
        .map((e) => ({ e, ...box(e) }))
        .filter((o) => o.y < bb.y && o.y > bb.y - 400 && o.w >= 150 && o.w <= 220)
        .sort((a, b) => a.y - b.y);
      const s = sels[gi];
      if (!s) return { ok: false, n: sels.length };
      s.e.scrollIntoView({ block: "center" });
      const input = s.e.querySelector("input");
      if (input) input.focus();
      return { ok: true, at: box(s.e), cur: (s.e.innerText || "").trim() };
    }, { gi: groupIndex });
    if (!opened.ok) return { ok: false, why: "no-select", n: opened.n };

    await page.keyboard.press("ArrowDown");          // 展开
    await page.waitForTimeout(1100);

    const picked = await page.evaluate(({ want }) => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const drops = [...document.querySelectorAll("[class*=ecom-g-select-dropdown]")].filter(vv)
        .map((e) => ({ e, ...box(e), cls: String(e.className) }))
        .filter((o) => /spec_detail/.test(o.cls) && o.w > 80 && o.h > 30);
      if (!drops.length) return { ok: false, why: "no-dropdown" };
      const drop = drops[drops.length - 1];
      const opts = [...drop.e.querySelectorAll("[class*=option]")].filter(vv).filter((e) => (e.innerText || "").trim() === want);
      if (!opts.length) return { ok: false, why: "no-option", items: [...drop.e.querySelectorAll("[class*=option]")].filter(vv).map((e) => (e.innerText || "").trim()).filter(Boolean).slice(0, 20) };
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) opts[0].dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
      return { ok: true };
    }, { want: wantType });
    await page.waitForTimeout(2200);
    log(`   组${groupIndex} 类型 →「${wantType}」:`, JSON.stringify(picked));
    return picked;
  };

  await setType(0, first.type);

  // 加第一组的值
  const addValues = async (type, values) => {
    for (const v of values) {
      const t = await page.evaluate(({ ph }) => {
        const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
        const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
        const ins = [...document.querySelectorAll("input[type=text]")].filter(vv)
          .filter((e) => e.placeholder === ph && !e.value)
          .map((e) => ({ e, ...box(e) })).sort((a, b) => a.y - b.y || a.x - b.x);
        if (!ins.length) return { ok: false };
        const last = ins[ins.length - 1];
        last.e.scrollIntoView({ block: "center" });
        last.e.focus();
        return { ok: true, at: { x: last.x, y: last.y } };
      }, { ph: `请输入${type}` });
      if (!t.ok) { log(`   ✗ 「${v}」找不到「请输入${type}」空框`); return false; }
      await page.keyboard.type(v, { delay: 80 });
      await page.waitForTimeout(350);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(1500);
      log(`   + ${type}「${v}」`);
    }
    return true;
  };

  await addValues(first.type, first.values);

  // 其余规格组
  for (let gi = 1; gi < dimList.length; gi++) {
    const d = dimList[gi];
    const added = await page.evaluate(() => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const btn = [...document.querySelectorAll("button")].filter(vv).find((e) => /添加规格类型/.test(e.innerText || ""));
      if (!btn) return { ok: false };
      btn.scrollIntoView({ block: "center" });
      for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) btn.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
      return { ok: true };
    });
    log(`   加第 ${gi + 1} 组:`, JSON.stringify(added));
    await page.waitForTimeout(2500);
    await setType(gi, d.type);
    await addValues(d.type, d.values);
  }

  const result = await page.evaluate(() => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const head = [...document.querySelectorAll("[class*=table-header],thead")].filter(vv).map((e) => (e.innerText || "").replace(/\s+/g, " ").trim()).slice(0, 1);
    const rows = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
      .filter((e) => /￥/.test(e.innerText || "")).length;
    return { head, skuRows: rows };
  });
  log("规格完成 → 表头:", JSON.stringify(result.head), "| SKU 行数:", result.skuRows);
  return result;
}

/**
 * 填价格与库存：表是 rowspan 结构，第一维度单元格跨行，
 * 用「该行 y ≥ 最近一个维度单元格的 y」把维度值映射到数据行。
 */
async function stagePrice(page) {
  if (!spec?.price) { log("无价格表，跳过"); return { skipped: "no-price" }; }
  const res = await page.evaluate((PRICE) => {
    const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
    const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
    const setV = (el, v) => {
      el.focus();
      const s = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
      s.call(el, String(v));
      for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    };
    const all = [...document.querySelectorAll("[class*=table-row],[class*=row-vertical],tbody tr")].filter(vv)
      .map((e) => ({ e, ...box(e) })).filter((o) => o.h > 25 && o.h < 200 && o.w > 500);
    const dimCells = all.filter((o) => {
      const t = ((o.e.children[0] || {}).innerText || "").trim();
      return t && !/￥|SKU|价格/.test(t);
    }).map((o) => ({ v: (o.e.children[0].innerText || "").trim(), y: o.y })).sort((a, b) => a.y - b.y);
    const out = [];
    for (const r of all.filter((o) => /￥/.test(o.e.innerText || "")).sort((a, b) => a.y - b.y)) {
      const cells = [...r.e.children];
      const d2 = (cells[1].innerText || "").trim();
      const d1 = [...dimCells].reverse().find((c) => c.y <= r.y + 2);
      const key = `${d1 ? d1.v : ""}|${d2}`;
      const want = PRICE[key];
      if (!want) { out.push({ miss: key }); continue; }
      const pi = [...cells[2].querySelectorAll("input")][0];
      const si = [...cells[3].querySelectorAll("input")][0];
      if (pi) setV(pi, want.p);
      if (si) setV(si, want.s);
      out.push({ key, p: want.p, s: want.s, ok: !!(pi && si) });
    }
    return out;
  }, spec.price);
  for (const r of res) log("   价格:", JSON.stringify(r));
  return res;
}

/**
 * 规格图（仅第一组可加）：每个规格值右侧 32×32 加号 → fileChooser → 单张
 * 限 1:1、≤5M。
 */
async function stageSpecImages(page) {
  if (!spec?.spec_images) { log("无规格图映射，跳过"); return { skipped: "no-images" }; }
  const entries = Object.entries(spec.spec_images);
  let ok = 0;
  for (const [value, imgPath] of entries) {
    const at = await page.evaluate((v) => {
      const vv = (e) => { const r = e.getBoundingClientRect(); return e.offsetParent !== null && r.width > 0 && r.height > 0; };
      const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
      const inp = [...document.querySelectorAll("input[type=text]")].filter(vv).find((e) => e.value === v);
      if (!inp) return null;
      inp.scrollIntoView({ block: "center" });
      const ib = box(inp);
      // 同一行的 32×32 加号（class 含 imgUpload）
      const up = [...document.querySelectorAll("[class*=imgUpload]")].filter(vv).map((e) => ({ e, ...box(e) }))
        .filter((o) => Math.abs(o.y + o.h / 2 - (ib.y + ib.h / 2)) < 24 && o.x > ib.x + ib.w && o.w <= 40 && o.h <= 40)[0];
      return up ? { x: up.x + Math.round(up.w / 2), y: up.y + Math.round(up.h / 2) } : null;
    }, value);
    if (!at) { log(`   ✗ 「${value}」找不到图位`); continue; }
    try {
      const [chooser] = await Promise.all([
        page.waitForFileChooser({ timeout: 8000 }),
        page.mouse.click(at.x, at.y, { label: `规格图 ${value}` }),
      ]);
      await chooser.setFiles(imgPath.replace(/\\/g, "/"));
      await page.waitForTimeout(4000);
      ok++;
      log(`   ✓ 规格图「${value}」`);
    } catch (e) {
      log(`   ✗ 规格图「${value}」: ${String(e).slice(0, 80)}`);
    }
  }
  return { ok, total: entries.length };
}

/** 发货时间 */
async function stageShip(page) {
  await goTab(page, "价格库存");
  const OPTS = ["当日发", "次日发", "48小时", "大于48小时发货"];
  const want = Number(plan.ship_days || 2) >= 7 ? "大于48小时发货" : "48小时";
  const cur = await page.evaluate((opts) => {
    const vv = (e) => e.offsetParent !== null;
    for (let i = 0; i < opts.length; i++) {
      const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === opts[i]).pop();
      if (!el) continue;
      const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
      if (rb && rb.checked) { rb.focus(); return i; }
    }
    return -1;
  }, OPTS);
  if (cur < 0) return { ok: false, why: "no-radio" };
  const idx = OPTS.indexOf(want);
  const key = idx > cur ? "ArrowRight" : "ArrowLeft";
  for (let i = 0; i < Math.abs(idx - cur); i++) { await page.keyboard.press(key); await page.waitForTimeout(550); }
  log(`发货时间 → 「${want}」`);
  return { ok: true, want };
}

// ============ 主流程 ============
async function main() {
  const task = await taskSpace(SPACE);
  const page = task.page("p1");
  log("空间", task.spaceId, "| 商品", PID, "|", (plan.title || "").slice(0, 24));
  log("SKU 数", skuCount, "| 路线 manual | DRY =", DRY);

  await stageUpload(page);
  await stageGenerate(page);

  const specRes = await stageSpec(page).catch((e) => { log("规格异常:", String(e).slice(0, 120)); return { error: String(e).slice(0, 120) }; });
  await stagePrice(page).catch((e) => log("价格异常:", String(e).slice(0, 120)));
  await stageSpecImages(page).catch((e) => log("规格图异常:", String(e).slice(0, 120)));
  await stageShip(page).catch((e) => log("发货异常:", String(e).slice(0, 120)));

  log("=== 阶段完成 ===");
  log("规格结果:", JSON.stringify(specRes));
  if (DRY) log("DRY 模式：未点最终发布");
  await page.screenshot({ path: `${ROOT}/v8/shot-${PID}.png` });
  log("截图已存");
}

await main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
