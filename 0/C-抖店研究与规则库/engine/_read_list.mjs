// 只读：连抖店商品列表页，读出真实商品数量与状态
const PORT = 9224;
const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const cands = list.filter((x) => x.type === "page" && /fxg\.jinritemai\.com/.test(x.url));
console.log("候选抖店页面:", cands.length);
for (const t of cands) console.log(" -", t.id.slice(0, 8), t.url.slice(0, 110));

const target = cands.find((x) => /\/g\/list/.test(x.url)) || cands[0];
if (!target) { console.log("没有抖店页面"); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0; const pend = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
});
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pend.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
  ws.send(JSON.stringify({ id, method, params }));
});
const evalJs = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) return { __err: r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description || "") };
  return r.result?.value;
};

await send("Runtime.enable");

const info = await evalJs(`(() => {
  const t = (document.body.innerText || "").replace(/\\n{2,}/g, "\\n");
  return { url: location.href, title: document.title, len: t.length, head: t.slice(0, 2500) };
})()`);
console.log("\n===== 页面信息 =====");
console.log("url:", info.url);
console.log("title:", info.title);
console.log("文本长度:", info.len);
console.log("\n---- 正文前 2500 字 ----");
console.log(info.head);

// 抓所有可见数字型统计 + 按钮
const ui = await evalJs(`(() => {
  const vis = e => e.offsetParent !== null;
  const btns = [...document.querySelectorAll("button,[class*=btn],[role=button]")]
    .filter(vis).map(e => ({ t: (e.innerText||"").trim().slice(0,24), d: e.disabled === true }))
    .filter(b => b.t);
  // 找"全部/在售/已下架/草稿"这类 tab
  const tabs = [...document.querySelectorAll("[class*=tab],[role=tab],a,span,div")]
    .filter(e => vis(e) && e.children.length === 0)
    .map(e => (e.innerText||"").trim())
    .filter(s => /^(全部|在售中?|已下架|草稿箱?|审核中|未通过|已售罄)\\s*\\(?\\d*\\)?$/.test(s));
  return { btns: btns.slice(0, 60), tabs: [...new Set(tabs)] };
})()`);
console.log("\n===== Tab 类目 =====");
console.log(JSON.stringify(ui.tabs));
console.log("\n===== 按钮 =====");
console.log(JSON.stringify(ui.btns, null, 1));

ws.close();
process.exit(0);
