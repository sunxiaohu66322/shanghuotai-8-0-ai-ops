/**
 * 抖店批量上货引擎 · 边跑边采错  v3
 * 参数走 params.json（ego 子进程不继承环境变量，也不吃 -- 之后参数）
 *   { "batch": "B1", "plans": ["1531"], "mode": "probe|nav|run" }
 */
const fs = await import("node:fs/promises");
const path = await import("node:path");

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const SHOTS = path.join(OUT, "shots");

let P = { batch: "PROBE", plans: ["1531"], mode: "probe", space: "抖店批量上货" };
try {
  P = { ...P, ...JSON.parse(await fs.readFile(path.join(OUT, "params.json"), "utf8")) };
} catch {}

const BATCH = P.batch;
const MODE = P.mode;
const errors = [];
const runs = [];

function cat(field, msg) {
  const s = `${field || ""} ${msg || ""}`;
  if (/品牌/.test(s)) return "brand";
  if (/类目|开通/.test(s)) return "category";
  if (/主图|视频|图片|详情图/.test(s)) return "media";
  if (/该项为必填|请输入|必填/.test(s)) return "required";
  if (/不能重复|格式|超出|限制/.test(s)) return "format";
  return "other";
}
function rec(item, stage, field, msg) {
  const m = (msg || "").replace(/\s+/g, " ").trim();
  if (!m || m.length > 200) return;
  const f = (field || "").trim() || "unknown";
  if (errors.some((e) => e.item === item && e.field === f && e.msg === m)) return;
  errors.push({ item, stage, field: f, msg: m, category: cat(f, m) });
}

const SNAP_ERRORS = () => {
  const out = { panel: [], inline: [], red: [], todoCount: null, url: location.href };
  const vis = (e) => e.offsetParent !== null;
  const t = document.body.innerText || "";

  const idx = t.indexOf("待完善信息");
  if (idx >= 0) {
    const seg = t.slice(idx, idx + 2000);
    const lines = seg.split("\n").map((s) => s.trim()).filter(Boolean);
    for (let i = 1; i < lines.length - 1; i++) {
      if (lines[i] === "错误") {
        const field = lines[i - 1];
        const msg = lines[i + 1];
        if (field && msg && !/^\d+$/.test(field) && field !== "待完善信息") out.panel.push({ field, msg });
      }
    }
    for (let i = 1; i < lines.length - 1; i++) {
      const nxt = lines[i + 1];
      if (/^商品/.test(nxt) && /^(主图视频|主图|白底图|商品详情|属性信息|价格库存)$/.test(lines[i])) {
        out.panel.push({ field: lines[i], msg: nxt });
      }
    }
    const m = lines.slice(0, 3).join("|").match(/待完善信息\|(\d+)/);
    if (m) out.todoCount = parseInt(m[1], 10);
  }

  for (const e of document.querySelectorAll('[class*="has-error"]')) {
    if (!vis(e)) continue;
    const s = (e.innerText || "").replace(/\s+/g, " ").trim();
    if (s.length < 2 || s.length > 150) continue;
    out.inline.push(s);
  }

  for (const e of document.querySelectorAll("div,span,p")) {
    if (!vis(e) || e.children.length > 0) continue;
    const s = (e.innerText || "").trim();
    if (s.length < 4 || s.length > 110) continue;
    const c = getComputedStyle(e).color;
    const mm = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (mm && +mm[1] >= 150 && +mm[2] < 100 && +mm[3] < 100) out.red.push(s);
  }
  return out;
};

async function shoot(page, item, stage) {
  try { await page.screenshot({ path: path.join(SHOTS, `${BATCH}-${item}-${stage}.png`) }); } catch {}
}
async function harvest(page, item, stage) {
  let snap;
  try { snap = await page.evaluate(SNAP_ERRORS); } catch (e) { console.log(`   [harvest] ERR ${e.message}`); return 0; }
  const before = errors.length;
  for (const p of snap.panel || []) rec(item, stage, p.field, p.msg);
  for (const s of snap.inline || []) rec(item, stage, "表单", s);
  console.log(`   [harvest:${stage}] todo=${snap.todoCount} panel=${(snap.panel || []).length} inline=${(snap.inline || []).length} red=${(snap.red || []).length} → 新增 ${errors.length - before}`);
  return errors.length - before;
}
async function dump() {
  await fs.writeFile(path.join(OUT, `errors-${BATCH}.json`), JSON.stringify(errors, null, 2), "utf8");
  await fs.writeFile(path.join(OUT, `runs-${BATCH}.json`), JSON.stringify(runs, null, 2), "utf8");
}

async function main() {
  await fs.mkdir(SHOTS, { recursive: true });
  console.log(`== params=${JSON.stringify(P)}`);
  const task = await taskSpace(P.space);
  const page = task.page("p1");
  console.log(`== space=${task.spaceId}`);

  if (MODE === "nav") {
    await page.goto("https://fxg.jinritemai.com/ffa/g/list");
    await page.waitForTimeout(8000);
    const st = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      nl: (document.body.innerText || "").length,
      head: (document.body.innerText || "").slice(0, 300),
    }));
    console.log("== 导航后:", JSON.stringify(st, null, 1));
    await shoot(page, "nav", "list");
    await dump();
    console.log("NAV_DONE");
    return;
  }

  if (MODE === "probe") {
    const st = await page.evaluate(() => ({ url: location.href, title: document.title, nl: (document.body.innerText || "").length }));
    console.log("== 当前页:", JSON.stringify(st));
    const n = await harvest(page, "probe", "probe");
    await shoot(page, "probe", "probe");
    await dump();
    console.log(`== 采到 ${n} 条`);
    console.log(JSON.stringify(errors, null, 1));
    console.log("PROBE_DONE");
    return;
  }

  await dump();
  console.log("RUN_DONE");
}

await main().catch(async (e) => { console.log("FATAL", e.message); await dump().catch(() => {}); process.exit(1); });
