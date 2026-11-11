/**
 * 8.0 · 每日真实巡店（只读 + 无副作用交互，复用项目现成 cdp.mjs，不造轮子）
 *
 * 目标（工程指令 §七 真实浏览器验收）：
 *   ① 真打开抖店后台商品/经营页；③ 实际点击菜单、切换页面、搜索商品、读数据；
 *   ④ 对真实商品诊断生成运营任务（Python 侧据本脚本真页面文字生成）；
 *   ⑤ 一项授权低风险任务真完成「浏览器输入 + 点击 + 回读」= 商品搜索（不改动任何平台数据）；
 *   ⑦ 操作后重新读页面确认结果变化。
 *
 * 硬边界：零发布 / 零改价 / 零改库存 / 零报名 / 零上下架；不输出 Cookie/Token；
 *         沿用 ego 9224 已登录 session；每步截图 + 回读，失败如实记录不伪造。
 * 产物：ops_patrol_result.json（Python 侧 browser_ops.py 读它入库）
 */
import { CDP, sleep, saveJson, listTargets } from
  "file:///G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/engine/cdp.mjs";
import fs from "node:fs/promises";

const PORT = Number(process.env.EGO_PORT || 9224);
const WS = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace";
// 生产：由 browser_ops.py 设 OPS_PATROL_OUT 指向 上货台/data/patrol；独立跑时落隔离证据目录
const OUTDIR = process.env.OPS_PATROL_OUT
  || (WS + "/_8.0运营升级-20261111/evidence/patrol");
await fs.mkdir(OUTDIR, { recursive: true });
const LIST_URL = "https://fxg.jinritemai.com/ffa/g/list";

// 真实存在的后台左侧菜单（取自真登录页 bodyHead 实测）
const SITES = [
  { key: "goods_list", menu: "商品管理", label: "商品列表/状态总览" },
  { key: "diagnosis",  menu: "商品诊断", label: "商品诊断/待优化商品" },
  { key: "oppty",      menu: "商机中心", label: "蓝海商机/机会品" },
  { key: "campaign",   menu: "活动广场", label: "营销活动广场" },
  { key: "search",     menu: "搜索运营", label: "搜索运营" },
  { key: "violation",  menu: "违规管理", label: "违规/整改" },
  { key: "growth",     menu: "商品成长", label: "商品成长" },
];

const result = {
  captured_at: new Date().toISOString(),
  port: PORT, platform: "dy", shop: "", login_state: "unknown",
  steps: [], search_task: null, summary: {},
};

async function readPage(c) {
  return await c.eval(`(() => {
    const b = (document.body && document.body.innerText) || "";
    const nums = {};
    // 抓真实计数：售卖中(n) / 审核驳回(n) / 已封禁(n) / (n)个商品 等
    const re = /([一-龥]{2,10})\s*[（(]?\s*(\d{1,5})\s*[)）]?/g;
    let m, k = 0;
    while ((m = re.exec(b)) && k < 40) {
      if (/商品|售卖|驳回|封禁|审核|草稿|下架|售罄|整改|优化|活动|商机|入池|曝光|点击/.test(m[1])) {
        nums[m[1]] = Number(m[2]); k++;
      }
    }
    return {
      url: location.href, title: document.title,
      body_len: b.length, body_head: b.slice(0, 1100),
      nums, is_login: /login|passport|扫码登录|请先登录/i.test(location.href)
                    || /扫码登录|请先登录|登录后查看/i.test(b.slice(0, 1500)),
    };
  })()`);
}

// 找一个抖店 tab（优先商品列表，其次任意 jinritemai），连接并返回 {c, target}
async function connectDy(preferUrl = "") {
  const tabs = await listTargets(PORT);
  const cand = tabs.filter(t => t.type === "page" && t.webSocketDebuggerUrl
                  && /jinritemai\.com/.test(t.url));
  const pick = (preferUrl && cand.find(t => t.url.includes(preferUrl)))
            || cand.find(t => t.url.includes("/ffa/g/list")) || cand[0];
  if (!pick) return null;
  const c = await CDP.connect(pick.webSocketDebuggerUrl);
  c.target = pick;
  await c.send("Runtime.enable").catch(() => {});
  await c.send("Page.enable").catch(() => {});
  return { c, target: pick };
}

let main = null;
try {
  // ---- 步骤0：真打开商品列表后台（复用已登录 tab 或新开） ----
  main = await connectDy("/ffa/g/list");
  if (!main) {
    main = { c: await CDP.newPage("about:blank", PORT) };
    await main.c.goto(LIST_URL, { settle: 8000 });
  } else {
    await main.c.goto(LIST_URL, { settle: 5000 });
  }
  const p0 = await readPage(main.c);
  result.login_state = p0.is_login ? "NOT_LOGGED_IN" : "session_present";
  // 店铺名（body 里 "LZL...专卖店" 一类）
  const sm = /([^\n]{2,20}(?:专卖店|专营店|旗舰店|官方店))/.exec(p0.body_head);
  result.shop = sm ? sm[1].trim() : (await main.c.eval("document.title") || "");
  result.steps.push({
    seq: 0, key: "open_goods_list", label: "打开抖店后台商品管理页",
    method: "goto+read", ok: !p0.is_login, before_url: "", after_url: p0.url,
    title: p0.title, body_len: p0.body_len, body_head: p0.body_head,
    nums: p0.nums, elapsed_ms: 0,
    screenshot: OUTDIR + "/patrol-0-goods_list.png",
  });
  try { await main.c.shot(OUTDIR + "/patrol-0-goods_list.png"); } catch {}
  console.log("[0] open " + result.login_state + " shop=" + result.shop);

  // ---- 步骤1..n：真点击菜单 → 切页 → 读数据 → 每步截图 ----
  let seq = 0;
  for (const s of SITES) {
    seq++;
    const rec = { seq, key: s.key, label: s.label, menu: s.menu,
                  method: "click", ok: false, before_url: "", after_url: "",
                  title: "", body_len: 0, body_head: "", nums: {},
                  click_receipt: null, elapsed_ms: 0,
                  screenshot: `${OUTDIR}/patrol-${seq}-${s.key}.png`, error: "" };
    const t0 = Date.now();
    try {
      // 每步重新连（点击可能开新标签导致原 ws 失效）
      const conn = await connectDy("") || main;
      const c = conn.c;
      const before = await c.eval("location.href").catch(() => "");
      rec.before_url = before;
      // 真点击菜单文字
      rec.click_receipt = await c.click(`text="${s.menu}"`).catch(e => ({ ok: false, err: String(e) }));
      await sleep(2600);
      // 点击后：优先读新出现的抖店 tab，否则读当前 tab
      let page = null, usedUrl = "";
      const tabsAfter = await listTargets(PORT);
      const dyTabs = tabsAfter.filter(t => t.type === "page" && /jinritemai\.com/.test(t.url));
      const fresh = dyTabs.find(t => t.url !== before && !t.url.includes("about:blank"));
      if (fresh && fresh.webSocketDebuggerUrl) {
        const c2 = await CDP.connect(fresh.webSocketDebuggerUrl);
        await c2.send("Runtime.enable").catch(() => {});
        await c2.send("Page.enable").catch(() => {});
        page = await readPage(c2).catch(() => null);
        usedUrl = fresh.url;
        try { await c2.shot(rec.screenshot); } catch {}
        c2.close();
      }
      if (!page) {
        page = await readPage(c).catch(() => null);
        usedUrl = page?.url || before;
        try { await c.shot(rec.screenshot); } catch {}
      }
      if (page) {
        rec.after_url = usedUrl;
        rec.title = page.title; rec.body_len = page.body_len;
        rec.body_head = page.body_head; rec.nums = page.nums;
        rec.ok = !!page.body_len && !page.is_login;
        rec.method = (usedUrl !== before) ? "click->switch" : "click->inpage";
      }
      if (!rec.ok && !rec.error) rec.error = "页面数据为空或点击未命中（如实记录）";
    } catch (e) {
      rec.error = String(e && e.message || e);
    }
    rec.elapsed_ms = Date.now() - t0;
    result.steps.push(rec);
    console.log(`[${seq}] ${s.key} ${rec.ok ? "OK" : "PARTIAL"} method=${rec.method} bodyLen=${rec.body_len}`);
  }

  // ---- 步骤末：授权低风险任务实证 = 商品搜索（输入 + 点击 + 回读，零数据改动） ----
  try {
    const conn = await connectDy("/ffa/g/list") || main;
    const c = conn.c;
    await c.goto(LIST_URL, { settle: 4000 });
    const beforeRows = await c.eval("document.querySelectorAll('tr').length").catch(() => 0);
    // 从当前页真实商品名里取一个关键词
    const kw = await c.eval(`(() => {
      const b = (document.body && document.body.innerText) || "";
      const lines = b.split("\\n").map(x => x.trim()).filter(x => x.length >= 8 && x.length <= 30
                    && /[\\u4e00-\\u9fa5]/.test(x) && !/[：:｜|]/.test(x));
      return lines.find(x => /沙发|茶几|边几|床头柜|家具|桌|柜|几/.test(x)) || lines[0] || "茶几";
    })()`);
    // 找可见搜索框并输入
    const filled = await c.eval(`(() => {
      const kw = ${JSON.stringify(kw)};
      const ins = [...document.querySelectorAll('input')].filter(e => {
        const r = e.getBoundingClientRect();
        return r.width > 60 && r.height > 10 && e.offsetParent !== null
               && (e.type === 'text' || e.type === 'search' || !e.type);
      });
      const el = ins[0];
      if (!el) return { ok: false, reason: 'no-visible-input' };
      const proto = el instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, kw);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true, placeholder: el.getAttribute('placeholder') || '', value: kw };
    })()`);
    await sleep(600);
    // 点搜索按钮 / 回车
    const clicked = await c.click('text="搜索"').catch(() => ({ ok: false }));
    if (!clicked?.ok) {
      await c.eval(`(() => {
        const el = [...document.querySelectorAll('input')].find(e => e.offsetParent !== null && e.value);
        if (el) { el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true}));
                  el.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',keyCode:13,bubbles:true})); }
        return true;
      })()`).catch(() => {});
    }
    await sleep(2800);
    const after = await readPage(c).catch(() => ({}));
    const afterRows = await c.eval("document.querySelectorAll('tr').length").catch(() => 0);
    const shot = OUTDIR + "/patrol-search-task.png";
    try { await c.shot(shot); } catch {}
    result.search_task = {
      label: "授权低风险任务：商品搜索（输入+点击+回读，零平台写操作）",
      keyword: kw, input: filled, click_receipt: clicked,
      before_rows: beforeRows, after_rows: afterRows,
      after_url: after.url || "", after_body_has_kw:
        (after.body_head || "").includes(String(kw).slice(0, 4)),
      readback_ok: !!after.body_len, screenshot: shot,
      ok: !!filled?.ok && !!after.body_len,
    };
    console.log("[S] search ok=" + result.search_task.ok + " kw=" + kw
                + " rows " + beforeRows + "->" + afterRows);
  } catch (e) {
    result.search_task = { ok: false, error: String(e && e.message || e) };
  }
} catch (e) {
  result.fatal = String(e && e.stack || e);
} finally {
  if (main?.c) { try { main.c.close(); } catch {} }
}

const okSteps = result.steps.filter(s => s.ok).length;
result.summary = {
  total_steps: result.steps.length, ok_steps: okSteps,
  fail_steps: result.steps.length - okSteps,
  search_task_ok: !!result.search_task?.ok,
  login_state: result.login_state,
};
await saveJson(OUTDIR + "/ops_patrol_result.json", result);
console.log("DONE steps=" + okSteps + "/" + result.steps.length
            + " login=" + result.login_state);
