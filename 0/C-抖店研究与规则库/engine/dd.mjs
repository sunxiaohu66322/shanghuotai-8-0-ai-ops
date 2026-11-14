// 抖店送货引擎（上货台 → 抖店草稿箱）
//
// 命令：
//   node dd.mjs cats                 拉类目白名单（家具/户外装备/家装灯饰光源）
//   node dd.mjs push  --pid 1234 [--dry]   上货台一件商品 → 抖店草稿（默认不发布）
//   node dd.mjs audit                读回草稿箱，逐件查信息完整度
//
// 设计约束（都是实测踩出来的）：
//  · 抖店没有可用 OpenAPI（无软著）⇒ 全部走真实页面
//  · 新版 /ffa/g/create 不手选类目（select_category_limit=not_support_manual_select）
//    ⇒ 上主图 + 标题 → 点「确认信息，继续生成」→ 系统给类目，再校验在白名单内
//  · 端口每次启动可能不同（ego 重启会丢）⇒ 自动扫 9224 + 9223..9299
//  · 一律存草稿，**从不点发布**
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const WS = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace";
const OUT = WS + "/抖店研究/crawl3";
const DD = "https://fxg.jinritemai.com";

// 用户点名的类目（一级类目名 → 只准这些）
const ALLOW_L1 = ["家具", "户外装备", "家装灯饰光源", "居家日用"];

async function findPort() {
  // ★ 只认 9224（我们自己的约定端口），或命令行 --port 显式指定。
  //   曾经试过「扫 9223-9299 自动发现」——**放弃**：
  //   实测 9229 是本机另一个服务，它对 /json/list 能回一段像样的 JSON，
  //   但 /json/new 返回纯文本 "WebSockets request was expected"，
  //   于是自动扫会把脚本引到一个假浏览器上，报错还看不出根因。
  //   宁可明确报「没端口，去启动浏览器」，也不要猜。
  const want = Number(arg("port", 0)) || 9224;
  try {
    const r = await fetch(`http://127.0.0.1:${want}/json/list`, { signal: AbortSignal.timeout(900) });
    if (!r.ok) return null;
    const arr = JSON.parse(await r.text());
    if (Array.isArray(arr) && arr.some((t) => t.webSocketDebuggerUrl)) return want;
  } catch {}
  return null;
}

async function openDd(port, url) {
  // ★ 不依赖 /json/new（有的 CDP 实现不支持，会回纯文本）。
  //   优先 Target.createTarget；失败才退回「连一个已有页再导航」。
  try {
    const r = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT", signal: AbortSignal.timeout(3000) });
    const t = await r.json();
    if (t && t.webSocketDebuggerUrl) {
      const c = await CDP.connect(t.webSocketDebuggerUrl);
      c.target = t;
      await c.send("Network.enable").catch(() => {});
      await c.send("Page.enable").catch(() => {});
      await c.send("Runtime.enable").catch(() => {});
      await c.waitReady(3500);
      return c;
    }
  } catch (e) { console.log("   /json/new 不可用（" + e.message.slice(0, 40) + "），退回复用已有标签页"); }
  const c = await CDP.page({});
  await c.send("Page.enable").catch(() => {});
  await c.goto(url, { settle: 5000 });
  return c;
}

// ---------------- cats ----------------
async function cmdCats(port) {
  const c = await openDd(port, DD + "/ffa/g/create?cid=1000004588&entrance=create");
  await sleep(4000);
  // 页面自己会发 categoryOptionsN / 类目相关请求，从 CDP 事件里拿响应
  const reqs = c.netRequests(/categor/i);
  console.log("命中类目请求:", reqs.length);
  const got = {};
  for (const r of reqs) {
    const b = await c.bodyOf(r.reqId);
    if (!b || b.length < 200) continue;
    const name = r.url.split("?")[0].split("/").pop();
    got[name] = { url: r.url, len: b.length };
    await saveJson(`${OUT}/cat-${name}.json`, { url: r.url, body: JSON.parse(safeJson(b) || "null") });
  }
  console.log(JSON.stringify(got, null, 1));
  // 兜底：页面内同步 XHR（给超时，不再像上次挂死）
  const one = await c.eval(`(() => {
    return new Promise((res) => {
      const x = new XMLHttpRequest();
      x.open('GET', '/product/tproduct/categoryOptionsN', true);
      x.timeout = 8000;
      x.setRequestHeader('x-requested-with','XMLHttpRequest');
      x.onload = () => res({ status: x.status, len: (x.responseText||'').length, head: (x.responseText||'').slice(0,300) });
      x.onerror = () => res({ err: 'onerror' });
      x.ontimeout = () => res({ err: 'timeout' });
      x.send();
    });
  })()`);
  console.log("categoryOptionsN:", JSON.stringify(one).slice(0, 400));
  await c.close();
}

function safeJson(s) { try { JSON.parse(s); return s; } catch { return null; } }

// ---------------- push ----------------
async function cmdPush(port, pid, dry) {
  // 1) 从上货台 v3.db 取这一件商品（复用 doudian.build_rows 已产出的数据：
  //    这里用 python 侧导出的 JSON，避免在 node 里重写一遍 SQL）
  const planPath = `${OUT}/plan-${pid}.json`;
  let plan;
  try {
    plan = JSON.parse(await fs.readFile(planPath, "utf8"));
  } catch {
    console.log(`[!] 缺计划文件 ${planPath}`);
    console.log(`    先生成： python ${WS}/抖店研究/engine/mk_plan.py --pid ${pid}`);
    return 2;
  }
  console.log("== 计划：", plan.title?.slice(0, 40), "| 主图", plan.main?.length || 0, "张 | SKU", plan.skus?.length || 0);

  const c = await openDd(port, DD + "/ffa/g/create");
  await c.waitReady(4000);
  const body = await c.text("body");
  if (!body.includes("商品主图")) {
    console.log("[!] 页面上没看到「商品主图」——可能未登录，或页面结构变了。当前页文本前 200：");
    console.log(body.slice(0, 200));
    await c.close();
    return 3;
  }

  if (dry) {
    const inputs = await c.eval(`([...document.querySelectorAll('input[type=file]')].map(i=>({accept:i.accept,multiple:i.multiple})))`);
    console.log("== 干跑：找到 file input", JSON.stringify(inputs));
    await c.shot(`${OUT}/dry-create.png`);
    await c.close();
    return 0;
  }

  // 2) 传主图（第一个 file input 通常是主图）
  const files = [plan.main?.[0], ...(plan.main || []).slice(1, 5)].filter(Boolean);
  console.log("== 要上传", files.length, "张主图/辅助图");
  const n = await c.eval(`document.querySelectorAll('input[type=file]').length`);
  if (!n) { console.log("[!] 没有 file input"); await c.close(); return 4; }
  for (let i = 0; i < Math.min(files.length, n); i++) {
    const doc = await c.send("DOM.getDocument", { depth: -1 });
    const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
    if (!q.nodeIds[i]) break;
    await c.send("DOM.setFileInputFiles", { nodeId: q.nodeIds[i], files: [files[i]] });
    await sleep(2500);
    console.log(`   上传 ${i + 1}/${files.length}`);
  }
  await sleep(3000);

  // 3) 填标题（找 placeholder 含「标题」的 textarea/input）
  const t = await c.eval(`(() => {
    const els=[...document.querySelectorAll('textarea,input')].filter(e=>e.offsetParent!==null);
    const el=els.find(e=>/标题/.test(e.placeholder||'')||/标题/.test(e.getAttribute('aria-label')||''));
    if(!el) return {ok:false,count:els.length};
    el.focus(); el.value=${JSON.stringify(plan.title || "")};
    el.dispatchEvent(new Event('input',{bubbles:true}));
    return {ok:true, val:el.value.slice(0,40)};
  })()`);
  console.log("== 填标题:", JSON.stringify(t));

  // 4) 点「确认信息，继续生成」
  const go = await c.click('text="确认信息，继续生成"');
  console.log("== 继续生成:", JSON.stringify(go));
  await sleep(12000);

  const after = await c.text("body");
  await c.shot(`${OUT}/after-generate-${pid}.png`);
  // 5) 读回系统给的类目，校验白名单
  const cat = c.netRequests(/categor|predict/i);
  console.log("== 生成后类目相关请求:", cat.length);
  await saveJson(`${OUT}/push-${pid}.json`, { title: plan.title, after: after.slice(0, 4000) });
  console.log("== 截图 after-generate-" + pid + ".png；**没有点发布**");
  await c.close();
  return 0;
}

// ---------------- audit ----------------
async function cmdAudit(port) {
  const c = await openDd(port, DD + "/ffa/g/list?sov_draft_status=0");
  await c.waitReady(5000);
  const raw = await c.eval(`(() => new Promise((res) => {
    const x = new XMLHttpRequest();
    x.open('POST','/product/tproduct/list',true);
    x.timeout = 12000;
    x.setRequestHeader('content-type','application/json');
    x.onload=()=>res({status:x.status, body:(x.responseText||'').slice(0,900000)});
    x.onerror=()=>res({err:'onerror'});
    x.ontimeout=()=>res({err:'timeout'});
    x.send(JSON.stringify({page:0,pageSize:50,draft_status:0,tab:'all'}));
  }))()`);
  if (!raw || !raw.body) { console.log("[!] 拉草稿失败:", JSON.stringify(raw)); await c.close(); return 1; }
  const j = JSON.parse(raw.body);
  const list = j.data?.products || j.data?.list || j.products || [];
  console.log("== 草稿条数:", list.length);
  const rows = list.map(p => ({
    id: p.product_id || p.id,
    title: (p.name || p.title || "").slice(0, 30),
    cat: p.category_leaf_id || p.category_id || "",
    price: p.discount_price ?? p.price ?? "",
    stock: p.stock_num ?? "",
    imgs: (p.img_list || p.images || []).length || (p.cover_url ? 1 : 0),
    check: p.check_status ?? "",
    reason: (p.reason || "").slice(0, 40),
  }));
  await saveJson(`${OUT}/drafts.json`, rows);
  // 完整度判定
  const bad = rows.filter(r => !r.cat || r.price === "" || r.imgs === 0 || !r.title);
  console.log("== 不完整:", bad.length, "/", rows.length);
  bad.slice(0, 20).forEach(r => console.log("   ", JSON.stringify(r)));
  await c.close();
  return 0;
}

// ---------------- main ----------------
const [, , cmd, ...rest] = process.argv;
const arg = (k, d = null) => { const i = rest.indexOf("--" + k); return i >= 0 ? rest[i + 1] : d; };
const port = await findPort();
if (!port) {
  console.log("[!] 9224 上没有可用的浏览器调试端口。");
  console.log("    先把浏览器带端口起来（双击这个就行）：");
  console.log("    抖店研究\\起浏览器.cmd");
  console.log("    —— 它用独立 profile 启动 ego，不碰你日常那个 170 标签的窗口；");
  console.log("       起来后在那个窗口里登录一次抖店，之后就能跑。");
  process.exit(9);
}
console.log("== CDP 端口", port);
if (cmd === "cats") process.exit(await cmdCats(port));
else if (cmd === "push") process.exit(await cmdPush(port, arg("pid"), rest.includes("--dry")));
else if (cmd === "audit") process.exit(await cmdAudit(port));
else console.log("用法: node dd.mjs cats | push --pid N [--dry] | audit");
process.exit(0);
