/**
 * 抖店上货主脚本 v7 —— ego AI 空间 + 键盘优先
 *
 * 已验证（2026-11-02）：
 *   ✅ 独立 AI 空间（spaceId 5，不碰用户页面）
 *   ✅ 引导页传图（官方 setInputFiles，18 张不卡）
 *   ✅ 过「确认信息，继续生成」关（pointer-events:none → el.click()）
 *   ✅ 商品标题填写
 *   ✅ 发货时间键盘切换（聚焦 radio → ArrowRight/ArrowLeft）
 *
 * 待验证：
 *   ⏳ 导购短标题 AI 按钮
 *   ⏳ 主图加号补图（waitForFileChooser）
 *   ⏳ 多维规格设置 + SKU 图逐行上传
 *
 * 用法：ego-browser nodejs -e "await import('file:///.../v7/publish.mjs')"
 * 改文件顶部 PID 即可换商品。
 */
import fs from "node:fs/promises";

const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const PID = "1436";                    // 商品 id
const SPACE = 5;                       // 续用空间
const DRY = true;                      // true=不点最终发布

const plan = JSON.parse(await fs.readFile(`${ROOT}/plan-${PID}.json`, "utf8"));
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

// ============ 通用原语 ============

/** 页面状态 */
const state = (page) => page.evaluate(() => {
  const T = document.body.innerText || "";
  return {
    guide: /点击或拖拽文件到此处上传/.test(T),
    recognize: /按住图片可上下拖动/.test(T),
    form: /基础信息/.test(T) && /图文信息/.test(T),
    gen: /确认信息，继续生成/.test(T),
    busy: /识别中|生成中|裁切中|正在处理|上传中/.test(T),
  };
});

/** 切换 tab（键盘友好：先派发鼠标事件，失败再坐标点） */
async function goTab(page, name) {
  const r = await page.evaluate((n) => {
    const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
    const vv = (e) => e.offsetParent !== null;
    const cands = [...document.querySelectorAll("div,span,a,li")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === n);
    if (!cands.length) return "absent";
    const el = cands[0];
    el.scrollIntoView({ block: "center" });
    dc(el);
    return "ok";
  }, name);
  await page.waitForTimeout(3000);
  log(`tab「${name}」→ ${r}`);
  return r;
}

/** 点一个 pointer-events:none 的元素：依次尝试 el.click() → 事件派发 → 键盘聚焦+回车 */
async function clickRobust(page, text) {
  const found = await page.evaluate((t) => {
    const vv = (e) => e.offsetParent !== null;
    const els = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === t);
    if (!els.length) return null;
    const el = els[els.length - 1];
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    el.dataset.agentTarget = "1";
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), h: Math.round(r.height), pe: getComputedStyle(el).pointerEvents };
  }, text);
  if (!found) return { ok: false, why: "not-found" };

  // ① el.click()
  await page.evaluate(() => { const el = document.querySelector('[data-agent-target="1"]'); if (el) el.click(); });
  await page.waitForTimeout(1200);
  // ② pointer-events:none 的话，el.click() 通常已够；不够再派发
  if (found.pe === "none") {
    await page.evaluate(() => {
      const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
      const el = document.querySelector('[data-agent-target="1"]');
      if (el) { dc(el); if (el.parentElement) dc(el.parentElement); }
    });
    await page.waitForTimeout(1200);
  }
  await page.evaluate(() => { const el = document.querySelector('[data-agent-target="1"]'); if (el) delete el.dataset.agentTarget; });
  return { ok: true, pe: found.pe };
}

/** radio 组：聚焦当前选中项，按方向键到目标值 */
async function pickRadio(page, options, want) {
  const idx = options.indexOf(want);
  if (idx < 0) return { ok: false, why: "want-not-in-options" };

  const cur = await page.evaluate((opts) => {
    const vv = (e) => e.offsetParent !== null;
    for (let i = 0; i < opts.length; i++) {
      const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === opts[i]).pop();
      if (!el) continue;
      const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
      if (rb && rb.checked) { rb.focus(); return i; }
    }
    // 没有选中项 → 聚焦第一个
    const el0 = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === opts[0]).pop();
    const rb0 = el0 && (el0.querySelector("input[type=radio]") || (el0.closest("label") || {}).querySelector?.("input[type=radio]"));
    if (rb0) { rb0.focus(); return 0; }
    return -1;
  }, options);

  if (cur < 0) return { ok: false, why: "no-radio" };
  const delta = idx - cur;
  const key = delta > 0 ? "ArrowRight" : "ArrowLeft";
  for (let i = 0; i < Math.abs(delta); i++) {
    await page.keyboard.press(key);
    await page.waitForTimeout(600);
  }
  const now = await page.evaluate((w) => {
    const vv = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("label,span,div")].filter((e) => vv(e) && (e.innerText || "").trim() === w).pop();
    if (!el) return null;
    const rb = el.querySelector("input[type=radio]") || (el.closest("label") || {}).querySelector?.("input[type=radio]");
    return rb ? rb.checked : null;
  }, want);
  return { ok: now === true, from: options[cur], to: want, checked: now };
}

// ============ 各阶段 ============

async function stageUpload(page) {
  const s = await state(page);
  if (s.form) { log("已在表单，跳过传图"); return; }
  if (!s.guide) log("⚠ 当前不在引导页，状态:", JSON.stringify(s));

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

  // 先补标题（空标题会导致生成按钮不可点）
  const t30 = Array.from(plan.title || "").slice(0, 30).join("");
  const filled = await page.evaluate((v) => {
    const vv = (e) => e.offsetParent !== null;
    const el = [...document.querySelectorAll("input,textarea")].filter(vv).find((e) => /2-60/.test(e.placeholder || "") || e.maxLength === 60);
    if (!el) return false;
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value").set;
    setter.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
    return true;
  }, t30);
  log("标题填写:", filled ? "ok" : "未找到输入框", "|", t30.slice(0, 20));
  await page.waitForTimeout(1500);

  // 过生成关
  const r = await clickRobust(page, "确认信息，继续生成");
  log("点「确认信息，继续生成」:", JSON.stringify(r));

  for (let i = 1; i <= 72; i++) {
    await page.waitForTimeout(5000);
    const st = await state(page);
    if (st.form) { log(`表单就绪（${i * 5}s）`); return; }
    if (i % 6 === 0) log(`  等待表单 ${i * 5}s gen=${st.gen} busy=${st.busy}`);
    if (i % 12 === 0 && st.gen) await clickRobust(page, "确认信息，继续生成");
  }
  throw new Error("生成超时，未进入表单");
}

/** 导购短标题：点它右边的 AI 按钮 */
async function stageShortTitle(page) {
  const info = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "导购短标题")[0];
    if (!lab) return { found: false };
    lab.scrollIntoView({ block: "center" });
    const lb = lab.getBoundingClientRect();
    // 同一行（y 差 < 30）内的按钮/图标，排除 label 自己
    const cands = [...document.querySelectorAll("button,span,i,svg,img,div")]
      .filter((e) => vv(e) && e !== lab)
      .map((e) => { const r = e.getBoundingClientRect(); return { e, r, t: (e.innerText || "").trim(), title: e.getAttribute?.("title") || "", aria: e.getAttribute?.("aria-label") || "" }; })
      .filter((o) => Math.abs(o.r.y + o.r.height / 2 - (lb.y + lb.height / 2)) < 26 && o.r.width > 8 && o.r.width < 120 && o.r.x > lb.x)
      .sort((a, b) => a.r.x - b.r.x);
    return {
      found: true,
      lab: { x: Math.round(lb.x), y: Math.round(lb.y) },
      cands: cands.slice(0, 8).map((o) => ({ t: o.t.slice(0, 10), title: o.title, aria: o.aria, x: Math.round(o.r.x), y: Math.round(o.r.y), w: Math.round(o.r.width), h: Math.round(o.r.height), cls: String(o.e.className || "").slice(0, 30) })),
    };
  });
  log("导购短标题 AI 候选:", JSON.stringify(info.cands || []));
  if (!info.found || !info.cands?.length) return { ok: false, why: "no-ai-button" };

  // 优先挑 aria/title 含 AI 或 sparkle 的；否则挑最靠右的小图标
  const pick = info.cands.find((c) => /ai|spark|magic|推荐/i.test(c.title + c.aria + c.cls)) || info.cands[info.cands.length - 1];
  log("选中 AI 按钮:", JSON.stringify(pick));
  await page.mouse.click(pick.x + Math.round(pick.w / 2), pick.y + Math.round(pick.h / 2), { label: "AI生成短标题" });
  await page.waitForTimeout(6000);
  const val = await page.evaluate(() => {
    const vv = (e) => e.offsetParent !== null;
    const lab = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "导购短标题")[0];
    if (!lab) return null;
    const lb = lab.getBoundingClientRect();
    const inp = [...document.querySelectorAll("input,textarea")].filter(vv)
      .map((e) => ({ e, r: e.getBoundingClientRect() }))
      .filter((o) => Math.abs(o.r.y + o.r.height / 2 - (lb.y + lb.height / 2)) < 40)[0];
    return inp ? inp.e.value : null;
  });
  log("短标题结果:", JSON.stringify(val));
  return { ok: !!val, value: val };
}

/** 主图补图：点「上传主图」加号 → fileChooser → 单张/多张 */
async function stageMainImages(page, page2Files) {
  if (!page2Files?.length) return { ok: true, skipped: "no-extra" };
  const before = await page.evaluate(() => document.querySelectorAll("input[type=file]").length);
  try {
    const [chooser] = await Promise.all([
      page.waitForFileChooser({ timeout: 8000 }),
      page.click('text="上传主图"', { label: "打开主图选择器" }),
    ]);
    await chooser.setFiles(page2Files);
    log("主图补传:", page2Files.length, "张");
    await page.waitForTimeout(6000);
    return { ok: true };
  } catch (e) {
    log("主图 fileChooser 失败，退回 setInputFiles:", String(e).slice(0, 100));
    const upIdx = await page.evaluate(() => {
      const ins = [...document.querySelectorAll("input[type=file]")];
      return ins.findIndex((e) => e.multiple && /image/.test(e.accept || ""));
    });
    if (upIdx >= 0) await page.setInputFiles(`input[type=file] >> nth=${upIdx}`, page2Files);
    return { ok: upIdx >= 0, via: "setInputFiles" };
  }
}

/** 发货时间：48小时；有更长档位时按需选 */
async function stageShip(page) {
  await goTab(page, "价格库存");
  const OPTS = ["当日发", "次日发", "48小时", "大于48小时发货"];
  const days = Number(plan.ship_days || 2);
  const want = days >= 7 ? "大于48小时发货" : "48小时";
  const r = await pickRadio(page, OPTS, want);
  log(`发货时间 → 「${want}」:`, JSON.stringify(r));
  return r;
}

/** 主流程 */
async function main() {
  const task = await taskSpace(SPACE);
  const page = task.page("p1");
  log("空间", task.spaceId, "| url:", (await page.url()).slice(0, 70));
  log("商品", PID, plan.title.slice(0, 26));

  await stageUpload(page);
  await stageGenerate(page);

  const extra = (plan.main || []).slice(5, 12).map((p) => p.replace(/\\/g, "/"));
  await stageShortTitle(page).catch((e) => log("短标题异常:", String(e).slice(0, 80)));
  await stageMainImages(page, extra).catch((e) => log("主图异常:", String(e).slice(0, 80)));
  await stageShip(page).catch((e) => log("发货异常:", String(e).slice(0, 80)));

  log("=== 阶段完成（规格/SKU图 待实现）===");
  const shot = await page.screenshot({ path: `${ROOT}/v7/shot-publish-${PID}.png` });
  log("截图:", shot);
}

await main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
