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

// 2026-11-02：ego 的 nodejs 模式实测不执行脚本（文件都不生成）→ 用 CDP shim 让 node 直跑
if (typeof globalThis.taskSpace !== "function") {
  try {
    const shim = await import("./ego-shim.mjs");
    globalThis.taskSpace = shim.taskSpace;
    console.log("[shim] 已接管 taskSpace（CDP 9224）");
  } catch (e) {
    console.log("[shim] 加载失败:", e.message);
  }
}

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
const fsSync = await import("node:fs");
const LOGF = path.join(ROOT, `run-${BATCH}.log`);
try { fsSync.writeFileSync(LOGF, ""); } catch {}
const log = (...a) => {
  const line = `[${ts()}] ` + a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ");
  console.log(line);
  try { fsSync.appendFileSync(LOGF, line + "\n"); } catch {}
};

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
    video: (raw.video || []).filter(Boolean),
    ship_days: Number(raw.ship_days || 2),
    ship_reason: raw.ship_reason || "",
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


// ============ 类目选择（v6 新增） ============
// 实测路径：公告栏「修改」→ 确认弹窗「修改类目」→ 推荐项里点目标 → 下一步
async function pickCategory(page, item, plan) {
  const catPath = plan.cat || [];
  const full = catPath.join(" > ");
  const leaf = catPath[catPath.length - 1] || "";
  const read = async () => await page.evaluate(() => {
    const t = document.body.innerText || "";
    const i = t.indexOf("商品类目");
    return i < 0 ? "" : t.slice(i, i + 140).replace(/\s+/g, " ");
  });

  let cur = await read();
  log(`   类目前: ${cur.slice(0, 80)}`);
  if (cur.includes(leaf) && !/请选择该类目/.test(cur)) { log("   类目已正确，跳过"); return true; }

  const clickText = async (txt, label) => {
    const p = await page.evaluate((t) => {
      const vv = (e) => e.offsetParent !== null;
      const b = [...document.querySelectorAll("button,a,div,span")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === t)[0];
      if (!b) return null;
      b.scrollIntoView({ block: "center" });
      const r = b.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }, txt);
    if (!p) return false;
    await page.mouse.click(p.x, p.y, { label });
    return true;
  };

  const findChip = async () => await page.evaluate(({ full, leaf }) => {
    const vv = (e) => e.offsetParent !== null;
    const cands = [...document.querySelectorAll("div,span,li,a,label")].filter((e) => vv(e) && e.children.length === 0)
      .map((e) => {
        const t = (e.innerText || "").replace(/\s+/g, " ").trim();
        const r = e.getBoundingClientRect();
        return { t, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width) };
      })
      .filter((o) => o.t && o.w > 20);
    return cands.find((o) => o.t === full) || cands.find((o) => o.t.includes(leaf)) || null;
  }, { full, leaf });

  // A. 先看推荐项是否已经摆在页面上
  let chip = await findChip();
  let opened = false;
  if (!chip) {
    // B. 点「修改」→ 确认「修改类目」
    const mod = await page.evaluate(() => {
      const el = document.querySelector('[class*="modifyButton"]');
      if (!el) return null;
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    });
    if (mod) {
      await page.mouse.click(mod.x, mod.y, { label: "点修改类目" });
      await page.waitForTimeout(1600);
      await clickText("修改类目", "确认修改类目");
      await page.waitForTimeout(4500);
      opened = true;
      chip = await findChip();
    }
  }

  if (!chip) {
    // C. 兜底：手动选择类目 → 搜索
    const opened2 = await clickText("手动选择类目", "手动选择类目");
    if (opened2) {
      await page.waitForTimeout(3500);
      const box = await page.evaluate(() => {
        const vv = (e) => e.offsetParent !== null;
        const el = [...document.querySelectorAll("input")].filter(vv).find((e) => /搜索|关键词|类目/.test(e.placeholder || ""));
        if (!el) return null;
        el.scrollIntoView({ block: "center" });
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
      });
      if (box) {
        await page.mouse.click(box.x, box.y, { label: "类目搜索框" });
        await page.waitForTimeout(600);
        await page.keyboard.type(leaf.split("/")[0], { delay: 60 }).catch(() => {});
        await page.waitForTimeout(2800);
        chip = await findChip();
      }
    }
  }

  if (!chip) { rec(item, "category", "商品类目", `选项里找不到「${full}」`); return false; }
  log(`   点类目项: 「${chip.t.slice(0, 40)}」`);
  await page.mouse.click(chip.x, chip.y, { label: "选目标类目" });
  await page.waitForTimeout(2800);

  // D. 下一步 / 确定
  for (const t of ["下一步", "确定", "确认", "完成"]) {
    if (await clickText(t, `类目-${t}`)) { await page.waitForTimeout(3500); break; }
  }
  await page.waitForTimeout(2500);

  cur = await read();
  const ok = cur.includes(leaf) && !/请选择该类目/.test(cur);
  log(`   类目后: ${cur.slice(0, 80)} → ${ok ? "✓" : "✗"}`);
  if (!ok) rec(item, "category", "商品类目", `未选中「${leaf}」，当前=${cur.slice(0, 60)}`);
  return ok;
}

// ============ 规格归一化（v5 新增） ============
// 背景：抖店生成后 AI 会自造一个规格组（材质/款式…）且只给 1 个值，
// 采集来的 4 个 SKU 因此全被压成 1 个。必须把值改成真实 SKU 规格值并补足个数。
async function specRead(page) {
  return await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const addBtn = [...document.querySelectorAll("button")].filter((e) => vv(e) && /添加规格类型/.test(e.innerText || ""))[0];
    let zone = addBtn;
    for (let k = 0; k < 12 && zone; k++) { if (/商品规格/.test(zone.innerText || "")) break; zone = zone.parentElement; }
    const sel = zone ? [...zone.querySelectorAll(".ecom-g-select")].filter(vv)[0] : null;
    const name = sel ? (sel.innerText || "").replace(/\s+/g, " ").trim() : "";
    const clean = name.replace(/[（(].*$/, "").trim();
    const ph = clean ? "请输入" + clean : null;
    const ins = ph ? [...document.querySelectorAll("input")].filter(vv).filter((e) => (e.placeholder || "") === ph) : [];
    const rowN = [...document.querySelectorAll("tr")].filter(vv).filter((tr) => {
      const t = tr.innerText || "";
      return /[￥¥]/.test(t) && tr.querySelectorAll("input").length >= 2;
    }).length;
    return { name, ph, vals: ins.map((e) => e.value || ""), n: ins.length, rowN };
  });
}

async function normalizeSpec(page, item, plan) {
  const want = [];
  for (const s of plan.skus || []) {
    const v = (Array.isArray(s.spec) && s.spec[0]) || s.name || "";
    if (v && !want.includes(v)) want.push(v);
  }
  // v9：单规格也走同一套（把 AI 编的多余规格清掉），不再直接返回

  let st = await specRead(page);
  log(`   规格现状: 组=「${st.name}」 值=${JSON.stringify(st.vals)} 行=${st.rowN}`);

  // 没有规格组 → 点「添加规格类型」并选第一个规格名
  if (!st.n) {
    const p = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && /添加规格类型/.test(e.innerText || ""))[0];
      if (!b) return null;
      b.scrollIntoView({ block: "center" });
      const r = b.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    });
    if (!p) { rec(item, "spec", "规格", "没找到「添加规格类型」按钮"); return { want, ok: false }; }
    await page.mouse.click(p.x, p.y, { label: "添加规格类型" });
    await page.waitForTimeout(3000);
    await page.keyboard.press("ArrowDown").catch(() => {});
    await page.waitForTimeout(1600);
    const opt = await page.evaluate(() => {
      const vv = (e) => e.offsetParent !== null;
      return [...document.querySelectorAll(".ecom-g-select-item")].filter(vv).map((e) => {
        const r = e.getBoundingClientRect();
        return { t: (e.innerText || "").trim(), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), h: Math.round(r.height) };
      }).filter((o) => o.t && o.h > 10);
    });
    if (opt.length) { await page.mouse.click(opt[0].x, opt[0].y, { label: "选规格名" }); await page.waitForTimeout(2500); }
    st = await specRead(page);
    log(`   加组后: 组=「${st.name}」 n=${st.n}`);
  }

  if (!st.ph) { rec(item, "spec", "规格", "没定位到规格值输入框（占位符未识别）"); return { want, ok: false }; }

  // ★ v9：幂等改写 —— 先把前 N 个框改成 want，再清掉多余值；已一致的不动
  const setVal = async (idx, val) => {
    const pos = await page.evaluate(({ ph, idx }) => {
      const vv = (e) => e.offsetParent !== null;
      const ins = [...document.querySelectorAll("input")].filter(vv).filter((e) => (e.placeholder || "") === ph);
      const el = ins[idx];
      if (!el) return null;
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }, { ph: st.ph, idx });
    if (!pos) return false;
    await page.mouse.click(pos.x, pos.y, { label: `规格值框${idx + 1}` });
    await page.waitForTimeout(280);
    await page.keyboard.press("Control+a").catch(() => {});
    await page.waitForTimeout(110);
    if (val) await page.keyboard.type(val, { delay: 14 }).catch(() => {});
    else await page.keyboard.press("Backspace").catch(() => {});
    await page.waitForTimeout(420);
    await page.keyboard.press("Tab").catch(() => {});
    await page.waitForTimeout(1500);
    return true;
  };

  let cur = st.vals.map((v) => (v || "").trim());
  // 1) 逐个校准前 want.length 个
  for (let i = 0; i < want.length; i++) {
    if (cur[i] === want[i]) continue;
    const okSet = await setVal(i, want[i]);
    if (!okSet) { rec(item, "spec", "规格值", `第 ${i + 1} 个规格值输入框不存在`); break; }
    cur = (await specRead(page)).vals.map((v) => (v || "").trim());
  }
  // 2) 输入框不够 → 补（找最后一个空框）
  st = await specRead(page);
  let have = st.vals.map((v) => (v || "").trim()).filter(Boolean);
  for (const w of want) {
    if (have.includes(w)) continue;
    const pos = await page.evaluate((ph) => {
      const vv = (e) => e.offsetParent !== null;
      const empt = [...document.querySelectorAll("input")].filter(vv)
        .filter((e) => (e.placeholder || "") === ph && !(e.value || "").trim());
      if (!empt.length) return null;
      const el = empt[empt.length - 1];
      el.scrollIntoView({ block: "center" });
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    }, st.ph);
    if (!pos) { rec(item, "spec", "规格值", `没有空输入框，补不进「${w}」`); break; }
    await page.mouse.click(pos.x, pos.y, { label: `补规格值` });
    await page.waitForTimeout(300);
    await page.keyboard.type(w, { delay: 14 }).catch(() => {});
    await page.waitForTimeout(420);
    await page.keyboard.press("Tab").catch(() => {});
    await page.waitForTimeout(1600);
    have = (await specRead(page)).vals.map((v) => (v || "").trim()).filter(Boolean);
  }
  // 3) 清掉多余（从后往前，避免索引漂移）
  st = await specRead(page);
  const extraIdx = st.vals.map((v, i) => ({ v: (v || "").trim(), i }))
    .filter((o) => o.v && !want.includes(o.v))
    .sort((a, b) => b.i - a.i);
  for (const o of extraIdx) {
    log(`   清多余规格值「${o.v}」@${o.i}`);
    await setVal(o.i, "");
  }

  await page.waitForTimeout(2000);
  const after = await specRead(page);
  log(`   规格结果: 值=${JSON.stringify(after.vals)} 行=${after.rowN}`);
  const missing = want.filter((w) => !after.vals.some((v) => (v || "").trim() === w.trim()));
  if (missing.length) rec(item, "spec", "规格值", `缺值: ${missing.join("、")}`);
  if (after.rowN < want.length) rec(item, "spec", "SKU行", `SKU 行数 ${after.rowN} 少于应有 ${want.length}`);
  const ok = !missing.length && after.rowN >= want.length;
  log(`   规格归一化 ${ok ? "✓ 通过" : "✗ 未通过"}`);
  return { want, ok, name: after.name, vals: after.vals, rowN: after.rowN };
}

// ============ 价格/库存/重量（按规格值逐行匹配） ============
async function fillPriceRows(page, item, plan) {
  const pairs = (plan.skus || []).map((s) => ({
    key: (((Array.isArray(s.spec) && s.spec[0]) || s.name) || "").trim(),
    price: s.price,
    stock: s.stock,
  })).sort((a, b) => b.key.length - a.key.length);
  const out = await page.evaluate(({ pairs, dprice, dstock, weight }) => {
    const vv = (e) => e.offsetParent !== null;
    const setV = (el, v) => {
      el.focus();
      const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      s.call(el, String(v));
      for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    };
    const all = [...document.querySelectorAll("tr")].filter(vv).filter((tr) => {
      const t = tr.innerText || "";
      return /[￥¥]/.test(t) && tr.querySelectorAll("input").length >= 2;
    });
    // 只保留「文字里出现某个规格值」的行，能滤掉汇总行/空行
    const keyed = all.filter((tr) => {
      const t = (tr.innerText || "").replace(/\s+/g, " ");
      return pairs.some((p) => p.key && t.includes(p.key.slice(0, 6)));
    });
    const trs = keyed.length === pairs.length ? keyed : all;
    const rows = [];
    const byIndex = trs.length === pairs.length;   // 行数与 SKU 数相等 → 按顺序对齐（最可靠）
    trs.forEach((tr, i) => {
      const label = (tr.innerText || "").replace(/\s+/g, " ").trim();
      const cell = ((tr.querySelector("td") || {}).innerText || "").replace(/\s+/g, " ").trim();
      const hit = byIndex ? pairs[i]
        : pairs.find((p) => p.key && (cell === p.key || cell.startsWith(p.key) || label.includes(p.key)));
      const ins = [...tr.querySelectorAll("input")].filter(vv);
      const pe = ins.find((e) => /number/.test(e.className || "") && !/库存/.test(e.placeholder || "")) || ins[0];
      const se = ins.find((e) => /请输入库存/.test(e.placeholder || "")) || ins[1];
      const price = hit ? hit.price : dprice;
      const stock = hit ? hit.stock : dstock;
      if (pe) setV(pe, price);
      if (se) setV(se, stock);
      rows.push({ i, match: !!hit, label: label.slice(0, 10), price: pe ? pe.value : null, stock: se ? se.value : null });
    });
    const wEl = [...document.querySelectorAll("input")].filter(vv).find((e) => /0.001到9999.999/.test(e.placeholder || ""));
    let w = null;
    if (wEl) { setV(wEl, weight); w = wEl.value; }
    return { rows, weight: w };
  }, { pairs, dprice: plan.price, dstock: plan.stock, weight: plan.weight });
  log(`   价格: ${JSON.stringify(out.rows)} 重量=${out.weight}`);
  if (!out.rows.length) rec(item, "price", "价格", "价格表格里没找到可填行");
  for (const r of out.rows) {
    if (!r.match) rec(item, "price", "价格", `第 ${r.i + 1} 行未按规格值对上，已填默认价`);
    if (!r.price) rec(item, "price", "价格", `第 ${r.i + 1} 行价格未写入`);
    if (!r.stock) rec(item, "price", "现货库存", `第 ${r.i + 1} 行库存未写入`);
  }
  if (out.rows.length && out.rows.length < (plan.skus || []).length) {
    rec(item, "price", "SKU行", `价格表 ${out.rows.length} 行 < SKU ${plan.skus.length} 个`);
  }
  return out;
}

// ============ 主图视频（2026-11-02 新增）============
async function uploadVideo(page, item, plan) {
  const vids = (plan.video || []).filter(Boolean);
  if (!vids.length) { rec(item, "video", "主图视频", "plan 里没有视频"); return { ok: false, why: "no-video" }; }
  const f = String(vids[0]).replace(/\\/g, "/");
  try { await fs.access(f); } catch { rec(item, "video", "主图视频", "本地文件不存在 " + f); return { ok: false, why: "file-missing" }; }

  // 视频位在「图文信息」tab
  log("   视频：切「图文信息」tab");
  await goTab("图文信息").catch(() => {});
  await page.waitForTimeout(2500);
  const vIdx = await page.evaluate(() => {
    const ins = [...document.querySelectorAll("input[type=file]")];
    return ins.findIndex((e) => /mp4|video/.test(e.accept || ""));
  }).catch(() => -1);
  log(`   视频位 index=${vIdx}`);

  let ins = 0;
  try {
    await page.setInputFiles('input[type=file][accept*=".mp4"]', f, { timeout: 15000 });
    ins = 1;
  } catch (e) {
    rec(item, "video", "主图视频", "投递失败: " + String(e).slice(0, 80));
  }
  if (!ins) {           // 兜底：accept 属性可能写成 video/*
    try { await page.setInputFiles('input[type=file][accept*="video"]', f, { timeout: 10000 }); ins = 1; } catch {}
  }
  if (!ins) return { ok: false, why: "no-input" };

  // 等转码/上传完成（视频比图片慢，最多 240s）
  let done = false, last = "";
  for (let i = 1; i <= 80; i++) {
    await page.waitForTimeout(3000);
    const s = await page.evaluate(() => {
      const t = document.body.innerText || "";
      const vv = (e) => e.offsetParent !== null;
      const txt = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0)
        .map((e) => (e.innerText || "").trim())
        .filter((x) => /视频|上传中|转码|替换|删除/.test(x) && x.length < 30);
      return { txt: [...new Set(txt)].slice(0, 6), busy: /上传中|转码中|处理中/.test(t) };
    });
    last = JSON.stringify(s.txt);
    if (!s.busy && /删除|替换|重新上传/.test(s.txt.join(" "))) { done = true; break; }
  }
  log(`   主图视频 ${done ? "已上传" : "耗时超限"} ${last}`);
  if (!done) rec(item, "video", "主图视频", "上传未在 240 秒内确认完成");
  return { ok: done, why: done ? "ok" : "timeout" };
}

// ============ 现货发货时间（2026-11-02 新增）============
// 平台选项：当日发 / 次日发 / 48小时 / 大于48小时发货
// 主人规则：源商品有 >48 小时的 → 选「大于48小时发货」，否则选「48小时」
async function setShipTime(page, item, plan) {
  const days = Number(plan.ship_days || 2);
  const want = days >= 7 ? "大于48小时发货" : "48小时";
  await goTab("基础信息").catch(() => {});
  await page.waitForTimeout(2500);

  const res = await page.evaluate((label) => {
    const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
    const vv = (e) => e.offsetParent !== null;
    // 精确等于 label 的最内层元素（避免「48小时」误命中「大于48小时发货」）
    const cands = [...document.querySelectorAll("label,span,div,li")].filter((e) => vv(e) && (e.innerText || "").trim() === label);
    if (!cands.length) return { ok: false, why: "找不到「" + label + "」选项" };
    const el = cands[cands.length - 1];
    const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
    dc(rb || el);
    return { ok: true, via: rb ? "radio" : "elem", tag: el.tagName };
  }, want);

  await page.waitForTimeout(1500);
  // 校验：目标选项是否呈现选中态
  const checked = await page.evaluate((label) => {
    const vv = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("label,span,div,li")].filter((e) => vv(e) && (e.innerText || "").trim() === label).pop();
    if (!el) return null;
    const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
    if (rb) return rb.checked;
    const cls = (el.className || "") + ((el.parentElement && el.parentElement.className) || "");
    return /checked|active|selected/i.test(cls);
  }, want);

  const ok = res.ok && checked !== false;
  log(`   发货时间 → 「${want}」 ${ok ? "✓" : "⚠未确认"} (${JSON.stringify(res)})`);
  if (!ok) rec(item, "ship", "发货时间", `设置「${want}」失败：${JSON.stringify(res)}，选中态=${checked}`);
  return { ok, want, checked, res };
}

// ============ SKU 规格图（批量上传 + 逐个匹配赋值） ============
async function uploadSkuImages(page, item, plan) {
  const skus = (plan.skus || []).filter((s) => s.img);
  if (!skus.length) { rec(item, "skuimg", "规格图", "plan 里没有 SKU 图"); return { ok: false, why: "no-img" }; }
  const imgs = skus.map((s) => String(s.img).replace(/\\/g, "/"));
  const keys = skus.map((s) => String((Array.isArray(s.spec) && s.spec[0]) || s.name || "").trim());

  const findBulk = () => page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const hits = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "批量上传规格图");
    if (!hits.length) return null;
    const e = hits[0];
    e.scrollIntoView({ block: "center" });
    const r = e.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });

  let bulk = await findBulk();
  if (!bulk) {
    for (const t of ["图文信息", "价格库存", "属性信息"]) {
      await page.evaluate((name) => {
        const dc = (el) => { for (const ev of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(ev, { bubbles: true, cancelable: true, view: window })); };
        const vv = (e) => e.offsetParent !== null;
        const tab = [...document.querySelectorAll("div")].filter((e) => vv(e) && (e.innerText || "").trim() === name)[0];
        if (tab) dc(tab);
      }, t);
      await page.waitForTimeout(3500);
      bulk = await findBulk();
      if (bulk) { log(`   规格图入口在「${t}」tab`); break; }
    }
  }
  if (!bulk) { rec(item, "skuimg", "规格图", "没找到「批量上传规格图」入口"); return { ok: false, why: "no-entry" }; }

  try {
    const ch = page.waitForFileChooser({ timeout: 10000 });
    await page.mouse.click(bulk.x, bulk.y, { label: "批量上传规格图" });
    const chooser = await ch;
    await chooser.setFiles(imgs);
    log(`   规格图投递 ${imgs.length} 张`);
    await page.waitForTimeout(9000);
  } catch (e) {
    rec(item, "skuimg", "规格图", `投递文件失败: ${String(e).slice(0, 60)}`);
    return { ok: false, why: "upload" };
  }

  const readSel = () => page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const sels = [...document.querySelectorAll('[class*="select"]')].filter(vv)
      .filter((e) => /未选择默认生成新规格|请选择规格/.test(e.innerText || ""))
      .map((e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width) }; })
      .filter((o) => o.w > 200);
    const seen = new Set(); const out = [];
    for (const s of sels) { const k = s.x + ":" + s.y; if (!seen.has(k)) { seen.add(k); out.push(s); } }
    return out;
  });

  let positions = await readSel();
  log(`   规格图下拉 ${positions.length} 个 / 需匹配 ${keys.length} 个`);

  const readOpts = () => page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const sel = '.ecom-g-select-item,[role="option"],[class*="select-item"],[class*="option-item"]';
    return [...document.querySelectorAll(sel)].filter(vv)
      .map((e) => { const r = e.getBoundingClientRect(); return { t: (e.innerText || "").trim(), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), h: Math.round(r.height) }; })
      .filter((o) => o.t && o.h > 10 && o.t.length < 80 && !/未选择默认生成新规格/.test(o.t));
  });

  const used = new Set();
  for (let i = 0; i < keys.length; i++) {
    // ★ 每轮重读位置：选中一个后表格会重排，缓存坐标会点空白
    const ps = await readSel();
    const pos = ps[i] || ps[ps.length - 1];
    if (!pos) { log(`     [${i}] 找不到第 ${i + 1} 个下拉（现有 ${ps.length} 个）`); continue; }
    // 已经选好的行不重复点（文字不再是「请选择规格」就不会出现在 readSel 里）
    let opts = [];
    for (let a = 0; a < 2 && !opts.length; a++) {
      await page.mouse.click(pos.x, pos.y, { label: `规格图下拉${i + 1}` });
      await page.waitForTimeout(1800 + a * 800);
      opts = await readOpts();
      if (!opts.length) { await page.keyboard.press("ArrowDown").catch(() => {}); await page.waitForTimeout(1400); opts = await readOpts(); }
    }
    const k = keys[i] || "";
    const kHead = k.replace(/[【\[].*$/, "").slice(0, 8);
    let want = opts.find((o) => o.t === k) || opts.find((o) => kHead && o.t.includes(kHead));
    if (!want) want = opts.find((o) => o.t && !used.has(o.t));   // 兜底：取一个还没用过的，绝不落到「默认生成新规格」
    log(`     [${i}] 「${k.slice(0, 14)}」 下拉@(${pos.x},${pos.y}) 选项=${opts.length} → ${want ? "命中 " + want.t.slice(0, 12) : "未命中"}`);
    if (want) { used.add(want.t); await page.mouse.click(want.x, want.y, { label: "匹配" }); await page.waitForTimeout(1600); }
    else { await page.keyboard.press("Escape").catch(() => {}); await page.waitForTimeout(400); }
  }

  const okBtn = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const btns = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "确定");
    if (!btns.length) return null;
    const b = btns[btns.length - 1];
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });
  if (okBtn) { await page.mouse.click(okBtn.x, okBtn.y, { label: "确定" }); await page.waitForTimeout(9000); }
  else rec(item, "skuimg", "规格图", "弹窗里没找到「确定」");

  const after = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const trs = [...document.querySelectorAll("tr")].filter(vv).filter((tr) => /[￥¥]/.test(tr.innerText || "") && tr.querySelectorAll("input").length >= 2);
    return trs.map((tr) => ({ label: (tr.innerText || "").replace(/\s+/g, " ").trim().slice(0, 10), hasImg: !!tr.querySelector("img") }));
  });
  log(`   规格图结果: ${JSON.stringify(after)}`);
  const ok = after.length > 0 && after.every((r) => r.hasImg);
  if (!ok) rec(item, "skuimg", "规格图", `有 ${after.filter((r) => !r.hasImg).length} 行没有图`);
  return { ok, rows: after };
}

// ============ 单件完整流程 ============
// v7：导航容错（ERR_ABORTED 是页面自己发起的跳转打断，重试即可）
async function safeGoto(page, url, item, label) {
  for (let i = 1; i <= 3; i++) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
      return true;
    } catch (e) {
      const m = String(e && e.message || e);
      log(`   [goto${i}] ${label} 失败: ${m.slice(0, 70)}`);
      if (i === 3) { rec(item, "goto", label, m.slice(0, 120)); throw e; }
      await page.waitForTimeout(2500 + i * 1500);
      try { await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 }); } catch {}
      await page.waitForTimeout(2000);
    }
  }
}

async function publishOne(page, plan) {
  const item = plan.id;
  const rec2 = { item, title: (plan.title || "").slice(0, 30), steps: [] };
  const step = (s) => { rec2.steps.push(s); setRun(item, { stage: s }); };

  step("open");
  await safeGoto(page, "https://fxg.jinritemai.com/ffa/g/create", item, "打开新建页");
  await page.waitForFunction(
    () => document.querySelectorAll("input[type=file]").length > 0 && /点击或拖拽|已上传/.test(document.body.innerText || ""),
    undefined, { timeout: 60000 }
  );
  await harvest(page, item, "open-empty");

  // 1) 传图（主图最多 5，详情最多 13，合计≤18；太多会让 AI 识别超时）
  const mains = plan.main.slice(0, 5);
  const details = plan.detail.slice(0, 13);
  const files = [...mains, ...details].map((p) => p.replace(/\\/g, "/"));
  for (const f of files) await fs.access(f).catch(() => { rec(item, "upload", "图片文件", `本地不存在 ${f}`); });
  const exist = [];
  for (const f of files) { try { await fs.access(f); exist.push(f); } catch {} }
  log(`   投递 ${exist.length} 张`);
  // 定位「上传主图」那个 input：取第一个 accept 含 image 且 multiple=true 的
  // （老代码写死 nth=0，页面一旦有"替换/编辑"位就会打错位置 → AI 识别拿不到主图）
  let upNth = 0;
  const upIdx = await page.evaluate(() => {
    const ins = [...document.querySelectorAll("input[type=file]")];
    return ins.findIndex((e) => e.multiple && /image/.test(e.accept || ""));
  });
  if (upIdx >= 0) upNth = upIdx;
  log(`   主图上床位 index=${upNth}`);
  if (exist.length) await page.setInputFiles(`input[type=file] >> nth=${upNth}`, exist);

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

  // 1.5) 主图视频（有就传，失败只记不阻断）
  try {
    const vr = await uploadVideo(page, item, plan);
    rec2.video = vr;
    await shot(page, item, "video");
  } catch (e) {
    rec(item, "video", "主图视频", "异常: " + String(e).slice(0, 80));
  }
  step("video");

  // 2) 生成
  await page.click('text="下一步，生成商品"').catch(() => {});
  let ok = false;
  for (let i = 1; i <= 120; i++) {
    await page.waitForTimeout(2000);
    if (await page.evaluate(() => /已识别并分类商品材料|确认信息，继续生成/.test(document.body.innerText || ""))) { ok = true; break; }
  }
  if (!ok) { rec(item, "recognize", "AI识别", "超过240秒未完成识别"); await shot(page, item, "recognize-timeout"); throw new Error("识别超时"); }
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

  // 5) 类目（v6：修改→确认→点推荐项→下一步）
  const catOk = await pickCategory(page, item, plan);
  if (!catOk) log("   ⚠ 类目没落到目标，品牌可能被拒");
  step("categorized");

  // 6) 确认生成（v7：点一次没动静就再点，最多 3 轮，每轮等 200s）
  const clickGen = () => page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button,[class*=btn]")].filter((e) => vv(e) && (e.innerText || "").trim() === "确认信息，继续生成" && !e.disabled)[0];
    if (!b) return "absent";
    b.scrollIntoView({ block: "center" });
    const r = b.getBoundingClientRect();
    dc(b);
    return JSON.stringify({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
  });
  const readEdit = () => page.evaluate(() => {
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

  let editing = false;
  for (let attempt = 1; attempt <= 3 && !editing; attempt++) {
    const hb = await clickGen();
    if (hb === "absent") { log(`   [gen] 第${attempt}次 按钮不在（可能已提交）`); }
    else {
      log(`   [gen] 第${attempt}次 点击 ${hb}`);
      // 鼠标再补一刀（有些按钮只认真鼠标）
      try { const o = JSON.parse(hb); await page.mouse.click(o.x, o.y, { label: "确认生成" }); } catch {}
    }
    for (let i = 1; i <= 80; i++) {
      await page.waitForTimeout(2500);
      let s;
      try { s = await readEdit(); } catch { continue; }
      if (s.tabFound.length >= 3) { editing = true; log(`   进编辑页 ${attempt}/${i * 2.5}s，tab=${s.tabFound.join("/")}`); break; }
      if (i % 8 === 0) log(`   [wait${attempt}] ${i * 2.5}s tabs=${s.tabFound.length} gen=${s.genBtn} parsing=${s.stillParsing}`);
      // 按钮重新可点 = 上一次没生效，提前退出这一轮重试
      if (i >= 6 && s.genBtn === "enabled") { log(`   [gen] 按钮回到可点，提前重试`); break; }
    }
  }
  if (!editing) { rec(item, "generate", "确认生成", "3 轮重试仍未进入编辑页（顶部 tab 栏未出现）"); await shot(page, item, "generate-timeout"); throw new Error("生成超时"); }
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

  // 8) 价格/库存/重量（在「价格库存」tab）—— v5：先归一化规格，再按规格值逐行填
  await goTab("价格库存");
  await harvest(page, item, "tab-price");
  const spec = await normalizeSpec(page, item, plan);
  rec2.spec = spec;
  await shot(page, item, "spec");
  await page.waitForTimeout(2000);
  const filled = await fillPriceRows(page, item, plan);
  rec2.priceRows = filled.rows;
  await page.waitForTimeout(2500);

  // 9.5) SKU 规格图（批量上传 + 逐行匹配）
  const skuimg = await uploadSkuImages(page, item, plan);
  rec2.skuImg = skuimg;
  await shot(page, item, "skuimg");
  await page.waitForTimeout(2000);
  // 9.6) 上传规格图可能新增行 → 补填一次价格/库存
  const refill = await fillPriceRows(page, item, plan);
  rec2.priceRows2 = refill.rows;
  await page.waitForTimeout(1500);

  // 9.7) 现货发货时间（主人规则：源商品 >48h → 大于48小时发货，否则 48小时）
  try {
    const sr = await setShipTime(page, item, plan);
    rec2.ship = sr;
    await shot(page, item, "ship");
  } catch (e) {
    rec(item, "ship", "发货时间", "异常: " + String(e).slice(0, 80));
  }
  step("ship");

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

    // 价格/库存/规格 类必须回「价格库存」tab 才补得动
    const PRICEY = /规格图|规格值|价格|现货库存|库存|重量|￥|¥/;
    const pricey = pend.filter((f) => PRICEY.test(f));
    const attry = pend.filter((f) => !PRICEY.test(f));

    if (pricey.length) {
      await goTab("价格库存");
      await page.waitForTimeout(1500);
      if (pricey.some((f) => /规格值/.test(f)) && spec.want && spec.want.length > 1) {
        await normalizeSpec(page, item, plan);
        await page.waitForTimeout(1200);
      }
      if (pricey.some((f) => /规格图/.test(f))) {
        const again = await uploadSkuImages(page, item, plan);
        rec2.skuImg2 = again;
        await page.waitForTimeout(1500);
      }
      const rf = await fillPriceRows(page, item, plan);
      rec2.priceRows3 = rf.rows;
      await page.waitForTimeout(1500);
    }
    if (attry.length) {
      await goTab("属性信息");
      await page.waitForTimeout(1200);
      for (const f of attry) await fillAttrSelect(f, f);
      await page.waitForTimeout(1200);
    }
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
  // 11.8) 先存草稿（发布前存，发布失败也不丢已填内容）
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv=(e)=>e.offsetParent!==null;
    const b=[...document.querySelectorAll("button")].filter((e)=>vv(e)&&(e.innerText||"").trim()==="保存草稿")[0];
    if(b){b.scrollIntoView({block:"center"});dc(b);}
  });
  await page.waitForTimeout(6000);
  const draftSaved = await page.evaluate(() => /保存成功|已保存/.test(document.body.innerText || ""));
  log(`   保存草稿 ${draftSaved ? "成功" : "未确认"}`);
  if (!draftSaved) rec(item, "draft", "保存草稿", "未收到保存成功回执");
  rec2.draftSaved = draftSaved;

  // 12.1) 点发布（真鼠标 + DOM 事件双发）
  const pubPos = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "发布商品")[0];
    if (!b) return null;
    b.scrollIntoView({ block: "center" });
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  });
  if (pubPos) { await page.mouse.click(pubPos.x, pubPos.y, { label: "发布商品" }).catch(() => {}); }
  await page.evaluate(() => {
    const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));}; const vv = (e) => e.offsetParent !== null;
    const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === "发布商品")[0];
    if (b) dc(b);
  });

  // 12.2) 可能有二次确认弹窗（确认发布 / 确定 / 继续发布）
  await page.waitForTimeout(3500);
  const confirmed = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const names = ["确认发布", "确定发布", "继续发布", "确认提交", "确定"];
    for (const n of names) {
      const b = [...document.querySelectorAll("button")].filter((e) => vv(e) && (e.innerText || "").trim() === n && !e.disabled)
        .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 40 && r.height > 20; })[0];
      if (b && /发布|提交/.test(document.body.innerText || "")) {
        const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};
        dc(b); return n;
      }
    }
    return null;
  });
  if (confirmed) log(`   二次确认弹窗 → 点了「${confirmed}」`);

  let pub = false;
  let pubId = null;
  for (let i = 1; i <= 22; i++) {
    await page.waitForTimeout(2500);
    const s = await page.evaluate(() => {
      const t = document.body.innerText || "";
      const m = t.match(/(?:商品\s*ID|商品id)[：:\s]*(\d{10,})/i);
      return {
        ok: /提交成功|发布成功|上架成功|商品已提交/.test(t),
        fail: /发布失败|提交失败|审核不通过/.test(t),
        url: location.href,
        id: m ? m[1] : null,
      };
    });
    if (s.id) pubId = s.id;
    if (s.ok) { pub = true; if (s.id) { log(`   发布成功，页面直接给出商品ID ${s.id}`); break; } }
    if (s.fail) { log(`   页面报失败`); break; }
    if (/\/ffa\/g\/list/.test(s.url)) { pub = true; log(`   已跳转商品列表 = 发布成功`); break; }
  }
  // 兜底：页面不弹成功提示时，直接去列表找标题，找到就算发布成功
  if (!pub) {
    log("   [兜底] 去商品列表按标题确认");
    try {
      await safeGoto(page, "https://fxg.jinritemai.com/ffa/g/list", item, "兜底查列表");
      await page.waitForTimeout(7000);
      const f = await page.evaluate((title) => {
        const key = Array.from(title || "").slice(0, 8).join("");
        const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
        const mine = rows.find((tr) => (tr.innerText || "").replace(/\s/g, "").includes(key));
        if (!mine) return null;
        const t = (mine.innerText || "").replace(/\s+/g, " ");
        return { id: (t.match(/ID[：:]\s*(\d{10,})/) || [])[1], status: (t.match(/(售卖中|审核中|已售罄|已下架)/g) || []).join("/") };
      }, plan.title);
      if (f && f.id) { pub = true; pubId = f.id; log(`   [兜底] 列表找到 ${f.id} ${f.status || ""}`); }
      else log("   [兜底] 列表里没找到该标题");
    } catch (e) { log(`   [兜底] 失败 ${String(e && e.message || e).slice(0, 70)}`); }
  }
  log(`   发布 ${pub ? "成功" : "未确认"}${pubId ? " id=" + pubId : ""}`);
  if (!pub) { await harvest(page, item, "publish-failed"); rec(item, "publish", "发布", "未收到成功回执"); }
  step(pub ? "published" : "publish-unconfirmed");
  rec2.published = pub;
  if (!pub) { await shot(page, item, "publish-unconfirmed"); log("   发布未确认 → 留在当前页，不跳列表"); return rec2; }
  if (pubId) { rec2.list = { found: true, id: pubId, via: "success-page" }; setRun(item, { pid: pubId }); step("done"); return rec2; }

  // 13) 查列表拿 ID（最多 3 轮刷新，列表常滞后）
  let lst = { found: false };
  for (let k = 0; k < 3; k++) {
    await safeGoto(page, "https://fxg.jinritemai.com/ffa/g/list", item, "打开商品列表");
    await page.waitForTimeout(9000 + k * 3000);
    lst = await page.evaluate((title) => {
      const rows = [...document.querySelectorAll("tr")].filter((tr) => /ID[：:]\s*\d{10,}/.test(tr.innerText || ""));
      const key = Array.from(title).slice(0, 8).join("");
      const mine = rows.find((tr) => (tr.innerText || "").replace(/\s/g, "").includes(key));
      if (!mine) return { found: false, rows: rows.length };
      const txt = (mine.innerText || "").replace(/\s+/g, " ");
      return {
        found: true, rows: rows.length,
        id: (txt.match(/ID[：:]\s*(\d{10,})/) || [])[1],
        price: (txt.match(/￥[\d.]+/) || [])[0],
        stock: (txt.match(/(\d+)\s+0\s+暂无评价/) || [])[1],
        status: (txt.match(/(售卖中|审核中|已售罄|已下架)/g) || []).join("/"),
      };
    }, t30);
    if (lst.found) break;
    log(`   列表第 ${k + 1} 轮未找到，刷新重试`);
  }
  log(`   列表: ${JSON.stringify(lst)}`);
  setRun(item, { pid: lst.id || null, status: lst.status || null, stock: lst.stock || null });
  if (!lst.found) rec(item, "verify", "商品列表", "列表里找不到刚发布的商品");
  rec2.list = lst;
  step("done");
  return rec2;
}

// ============ 主 ============
async function main() {
  await fs.mkdir(SHOTS, { recursive: true });
  log(`batch=${BATCH} items=${P.items.join(",")} space=${P.space} dry=${P.dryPublish}`);
  const task = await taskSpace(P.space);  const page = task.page("p1");
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
  console.log("BATCH_V6_DONE");
}

await main().catch(async (e) => { console.log("FATAL", e.message); await dump().catch(() => {}); process.exit(1); });
