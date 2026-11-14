/**
 * 纯 CDP 探测（不依赖 taskSpace）：dump 抖店编辑页三个未知区块
 * 用法: node _cdp-probe.mjs
 */
const BASE = "http://127.0.0.1:9224";

const list = await (await fetch(`${BASE}/json/list`)).json();
const pages = list.filter((t) => t.type === "page");
console.log("=== 打开的页面 ===");
pages.forEach((t, i) => console.log(` [${i}] ${(t.title || "").slice(0, 30)} | ${(t.url || "").slice(0, 100)}`));

let target = pages.find((t) => /fxg\.jinritemai\.com/.test(t.url || ""));
if (!target) {
  console.log("\n没有抖店页面，新开一个列表页…");
  const r = await fetch(`${BASE}/json/new?${encodeURIComponent("https://fxg.jinritemai.com/ffa/g/list")}`, { method: "PUT" });
  target = await r.json();
  await new Promise((r) => setTimeout(r, 6000));
}
console.log("\n目标页:", target.url.slice(0, 110));

const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
};
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { err: r.result.exceptionDetails.text || String(r.result.exceptionDetails) };
  return r.result?.result?.value;
};

const P = (await evalJs("({url:location.href,title:document.title})"));
console.log("当前:", JSON.stringify(P));

// 如果不在编辑/新建页，去列表页找第一件「编辑」
if (!/\/g\/(create|edit)/.test(P.url || "")) {
  console.log("\n→ 导航到列表页并尝试进入编辑");
  await send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/list" });
  await new Promise((r) => setTimeout(r, 9000));
  const clicked = await evalJs(`(() => {
    const vv=(e)=>e.offsetParent!==null;
    const els=[...document.querySelectorAll("a,button,span,div")].filter(e=>vv(e)&&e.children.length===0&&/^(编辑|去编辑)$/.test((e.innerText||"").trim()));
    if(!els.length) return "no-edit-btn";
    els[0].click(); return "clicked:" + els[0].innerText.trim();
  })()`);
  console.log("  点击结果:", clicked);
  await new Promise((r) => setTimeout(r, 9000));
  console.log("  现在:", JSON.stringify(await evalJs("({url:location.href,title:document.title})")));
}

const RESULT = await evalJs(`(() => {
  const vv = (e) => e.offsetParent !== null;
  const T = document.body.innerText || "";
  const lines = T.split("\\n").map(s=>s.trim()).filter(Boolean);

  const tabs = [...new Set([...document.querySelectorAll("div,span,a,li")].filter(e=>vv(e)&&e.children.length===0&&/^(基础信息|图文信息|属性信息|价格库存)$/.test((e.innerText||"").trim())).map(e=>e.innerText.trim()))];

  const files = [...document.querySelectorAll("input[type=file]")].map(e=>{
    const r=e.getBoundingClientRect();
    const box=e.closest("div[class]")||e.parentElement;
    let up=box; for(let i=0;i<4&&up;i++){ if(/主图|视频|详情|素材/.test(up.innerText||"")) break; up=up.parentElement; }
    return {accept:e.accept||"", multiple:e.multiple, y:Math.round(r.y),
      ctx:((up&&up.innerText)||"").replace(/\\s+/g," ").slice(0,60)};
  });

  const mediaLines = lines.filter(s=>/主图|视频|3:4|3：4|白底|详情图|张|上限|最多/.test(s)&&s.length<70);
  const shipLines = lines.filter(s=>/发货|时效|承诺|小时内|现货|预售|次日|48|24小时|7天/.test(s)&&s.length<70);
  const radios = [...new Set([...document.querySelectorAll("[role=radio],input[type=radio]")].filter(vv).slice(0,40).map(r=>{
    const b=r.closest("label")||r.parentElement; return ((b&&b.innerText)||"").replace(/\\s+/g," ").trim().slice(0,40);
  }))];

  return {tabs, files, mediaLines:[...new Set(mediaLines)].slice(0,25), shipLines:[...new Set(shipLines)].slice(0,25), radios};
})()`);

console.log("\n=== tabs ===", JSON.stringify(RESULT.tabs));
console.log("\n=== file inputs (" + RESULT.files.length + ") ===");
RESULT.files.forEach((f, i) => console.log(`  [${i}] accept=${f.accept||"(空)"} multiple=${f.multiple} y=${f.y} ctx="${f.ctx}"`));
console.log("\n=== 主图/视频 文案 ===");
RESULT.mediaLines.forEach((s) => console.log("  ·", s));
console.log("\n=== 发货/时效 文案 ===");
RESULT.shipLines.forEach((s) => console.log("  ·", s));
console.log("\n=== radios ===", JSON.stringify(RESULT.radios));

ws.close();
