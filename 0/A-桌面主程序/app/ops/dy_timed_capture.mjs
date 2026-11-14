/**
 * 8.0 · 抖店商品后台定时抓取器（只读，固定脚本，不点任何写操作）
 *
 * 目的：抖店没有免签逐商品 API → 用已登录的 ego 浏览器空间读 /ffa/g/list 全店商品，
 *       产出 抖店研究/上货引擎/out/dy-timed-snapshot.json，
 *       由 ops_engine 的 business.py 定时自动并进 ops.db（模块1 真商品明细）。
 *
 * 跑法（在 ego-browser 的 nodejs 运行环境里）：
 *   await import('file:///G:/Users/Administrator/Documents/deepseek-harness/default-workspace/上货台/app/ops/dy_timed_capture.mjs')
 * 或由 Windows 计划任务 / ego 的定时入口按 30 分钟一次调用。
 *
 * 定时真正在哪一层：
 *   Python 侧 ops_engine.business.auto_loop（默认 1800s）每次「重读本快照 + 采微信官方接口」，
 *   不需要 ego 常驻；本脚本只负责「刷新后台真明细」。没跑本脚本时，
 *   页面会如实显示「历史全店快照(非实时) + 源时间」，不会编造实时数据。
 *
 * 硬边界：只 goto 商品列表页、只读 DOM/网络响应；不点发布/改价/改库存/报名；
 *         不输出 Cookie/Token；登录态沿用 ego 空间已有的 session，不改动它。
 */
import fs from "node:fs/promises";

const BASE = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace";
const OUT = BASE + "/抖店研究/上货引擎/out";
const SNAP = OUT + "/dy-timed-snapshot.json";
await fs.mkdir(OUT, { recursive: true });

const task = await taskSpace("dy-upload");
const page = (await task.pages())[0] || await task.newPage();

// ---- 1. 被动拦网络响应：商品列表接口真 JSON（最全，含价/库存/类目） ----
const captured = [];
const seen = new Set();
const grab = (obj) => {
  if (!obj || typeof obj !== "object") return;
  // 兼容多种包装：{data:{...}} / {data:[...]} / {list:[...]} / {products:[...]}
  const cands = [obj, obj.data, obj.Data, obj.result, obj.body];
  for (const c of cands) {
    if (!c || typeof c !== "object") continue;
    const arrs = [c.list, c.products, c.items, c.rows, c.data, c.commodity_list];
    for (const a of arrs) {
      if (!Array.isArray(a)) continue;
      for (const p of a) {
        if (!p || typeof p !== "object") continue;
        const pid = String(p.product_id ?? p.id ?? p.commodity_id ?? p.promotion_id ?? "").trim();
        if (!/^\d{6,}$/.test(pid) || seen.has(pid)) continue;
        const name = String(p.name ?? p.title ?? p.product_name ?? "").trim();
        if (!name) continue;                       // 没有标题的纯统计行不收
        seen.add(pid);
        captured.push({
          pid,
          name,
          status: p.status ?? p.state ?? p.commodity_status ?? null,
          draft: p.draft ?? p.draft_status ?? null,
          check: p.check ?? p.check_status ?? p.audit_status ?? null,
          // 价格单位口径：后台原值（分）；business.py 统一 /100 转元
          mp: p.market_price ?? p.mp ?? p.line_price ?? null,
          dp: p.discount_price ?? p.dp ?? p.price ?? p.sale_price ?? null,
          stock: p.stock ?? p.stock_num ?? p.quantity ?? null,
          cname: p.category_name ?? p.cname ?? p.third_category_name ?? "",
          cid: p.cid ?? p.category_id ?? p.third_category_id ?? null,
        });
      }
    }
  }
};

const origFetch = window.__opsOrigFetch || window.fetch;
window.__opsOrigFetch = origFetch;
window.fetch = async function (...args) {
  const resp = await origFetch.apply(this, args);
  try {
    const u = String(typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "");
    if (/commodity|product|g\/list|goods/i.test(u)) {
      const d = await resp.clone().json().catch(() => null);
      grab(d);
    }
  } catch {}
  return resp;
};

// ---- 2. 打开商品列表（沿用登录态），切全部状态 tab，逐页滚动触发加载 ----
await page.goto("https://fxg.jinritemai.com/ffa/g/list", {
  waitUntil: "domcontentloaded", timeout: 45000,
});
await page.waitForTimeout(10000);

const tabs = await page.evaluate(() => {
  const out = [];
  document.querySelectorAll("[role='tab'], a, li, span, div").forEach((el) => {
    const t = (el.innerText || "").replace(/\s+/g, " ").trim();
    if (t.length < 24 && /(全部|在售|售卖|上架|驳回|审核|未通过|下架|草稿|库存)/.test(t)
        && el.getBoundingClientRect().width > 0) out.push(t);
  });
  return [...new Set(out)].slice(0, 20);
});

for (const label of tabs) {
  try {
    await page.evaluate((txt) => {
      const el = [...document.querySelectorAll("[role='tab'],a,li,span,div")].find(
        (e) => (e.innerText || "").replace(/\s+/g, " ").trim() === txt
        && e.getBoundingClientRect().width > 0);
      if (el) el.click();
    }, label);
    await page.waitForTimeout(2600);
    // 滚到底触发分页/懒加载，最多 6 次
    for (let i = 0; i < 6; i++) {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(900);
    }
  } catch {}
}

// ---- 3. DOM 兜底：拦不到接口时，从可见行里读 ID/标题/价 ----
if (captured.length === 0) {
  const domRows = await page.evaluate(() => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const out = [];
    const nodes = [...document.querySelectorAll("tr")]
      .concat([...document.querySelectorAll("div,li")].filter(vis)
        .filter((e) => /ID[：:]\s*\d{6,}/.test(e.innerText || "")
                    && (e.innerText || "").length < 900));
    const seen2 = new Set();
    for (const n of nodes) {
      const txt = n.innerText || "";
      const m = txt.match(/ID[：:]\s*(\d{6,})/) || txt.match(/\b(3\d{18})\b/);
      if (!m || seen2.has(m[1])) continue;
      seen2.add(m[1]);
      const lines = txt.split("\n").map((x) => x.trim()).filter(Boolean);
      const prices = (txt.match(/￥\s*([\d.]+)/g) || []).map((s) => s.replace(/[^\d.]/g, ""));
      out.push({ pid: m[1], name: lines[0] || "", status: null, draft: null,
                 check: null, dp: prices[0] || null, mp: prices[prices.length - 1] || null,
                 stock: null, cname: "", cid: null });
    }
    return out;
  });
  for (const r of domRows) if (r.pid && !seen.has(r.pid)) { seen.add(r.pid); captured.push(r); }
}

// ---- 4. 落盘（原子写；失败不覆盖上一份好快照） ----
const body = {
  captured_at: new Date().toISOString(),
  url: await page.url(),
  shop: "LZL乐群云家具专卖店",
  login_state: /login|passport/i.test(await page.url()) ? "not_logged_in" : "session_present",
  tabs,
  total: captured.length,
  source_kind: "ego_browser_timed",
  rows: captured,
};
if (body.login_state === "not_logged_in" || captured.length === 0) {
  console.log("CAPTURE_SKIPPED login=" + body.login_state + " rows=" + captured.length
              + "（保留上一份快照，不写空数据）");
} else {
  const tmp = SNAP + ".tmp";
  await fs.writeFile(tmp, JSON.stringify(body, null, 2), "utf8");
  await fs.rename(tmp, SNAP);
  console.log("CAPTURE_OK rows=" + captured.length + " tabs=" + JSON.stringify(tabs)
              + " at=" + body.captured_at);
  console.log("SNAPSHOT " + SNAP);
}
console.log("DONE");
