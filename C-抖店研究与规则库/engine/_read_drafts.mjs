// 只读：点「草稿箱」「全部」等 tab，读出各状态真实数量
const PORT = 9224;
const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const target = list.find((x) => x.type === "page" && /fxg\.jinritemai\.com\/ffa\/g\/list/.test(x.url));
if (!target) { console.log("没有抖店列表页"); process.exit(1); }

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
  if (r.exceptionDetails) return { __err: r.exceptionDetails.text };
  return r.result?.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await send("Runtime.enable");

// tab 名与计数
const tabs = await evalJs(`(() => {
  const vis = e => e.offsetParent !== null;
  const out = [];
  const all = [...document.querySelectorAll("*")].filter(e => vis(e) && e.children.length === 0);
  for (const e of all) {
    const s = (e.innerText || "").trim();
    if (/^(全部|售卖中|已售罄|已下架|已封禁|审核中|审核驳回|审核通过|编辑记录|草稿箱|回收站)\\s*(\\(\\d+\\))?$/.test(s)) {
      const r = e.getBoundingClientRect();
      out.push({ t: s, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) });
    }
  }
  return out;
})()`);
console.log("===== tab 及坐标 =====");
console.log(JSON.stringify(tabs, null, 1));

// 点「草稿箱」
const draft = tabs.find((t) => t.t.startsWith("草稿箱"));
if (draft) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: draft.x, y: draft.y, button: "none" });
  await sleep(120);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: draft.x, y: draft.y, button: "left", clickCount: 1 });
  await sleep(90);
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: draft.x, y: draft.y, button: "left", clickCount: 1 });
  await sleep(3500);

  const after = await evalJs(`(() => {
    const t = (document.body.innerText || "").replace(/\\n{2,}/g, "\\n");
    const m = t.match(/共\\s*(\\d+)\\s*条/);
    const rows = [...document.querySelectorAll("tr")].filter(tr => /ID[：:]\\s*\\d{10,}/.test(tr.innerText || ""))
      .map(tr => (tr.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 160));
    return { count: m ? Number(m[1]) : null, rows: rows.slice(0, 30), tail: t.slice(-1200) };
  })()`);
  console.log("\n===== 草稿箱 =====");
  console.log("共", after.count, "条");
  after.rows.forEach((r) => console.log("  •", r));
  console.log("\n---- 尾部文本 ----");
  console.log(after.tail);
} else {
  console.log("没找到草稿箱 tab");
}

ws.close();
process.exit(0);
