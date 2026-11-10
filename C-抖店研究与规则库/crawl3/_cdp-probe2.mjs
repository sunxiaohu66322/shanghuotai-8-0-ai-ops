/** 二次探测：主图3:4 区的上传位 + 发货时间（分 tab 扫） */
const BASE = "http://127.0.0.1:9224";
const list = await (await fetch(`${BASE}/json/list`)).json();
const target = list.filter((t) => t.type === "page").find((t) => /fxg\.jinritemai\.com\/ffa\/g\/(create|edit)/.test(t.url || ""));
if (!target) { console.log("没找到抖店编辑页"); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const evalJs = async (e) => {
  const r = await send("Runtime.evaluate", { expression: e, returnByValue: true, awaitPromise: true });
  return r.result?.exceptionDetails ? { err: r.result.exceptionDetails.text } : r.result?.result?.value;
};

// 给每个 file input 标注它最近的、含关键字的上层区块文字，以及相对 y
const mapInputs = `(() => {
  const vv=(e)=>e.offsetParent!==null;
  const ins=[...document.querySelectorAll("input[type=file]")];
  return ins.map((e,i)=>{
    const r=e.getBoundingClientRect();
    let up=e, best="";
    for(let k=0;k<8&&up;k++){
      const t=((up.innerText||"")+"").replace(/\\s+/g," ").trim();
      if(t.length>0&&t.length<400){best=t;break;}
      up=up.parentElement;
    }
    return {i, accept:e.accept||"", multiple:e.multiple, y:Math.round(r.top), ctx:best.slice(0,110)};
  }).filter(o=>/主图|视频|白底|规格图|3:4|详情|上传/.test(o.ctx));
})()`;

const dump = `(() => {
  const vv=(e)=>e.offsetParent!==null;
  const T=document.body.innerText||"";
  const lines=T.split("\\n").map(s=>s.trim()).filter(Boolean);
  return {
    ship: [...new Set(lines.filter(s=>/发货|时效|承诺|小时内|现货|预售|次日|48小时|24小时|7天/.test(s)&&s.length<60))].slice(0,20),
    sel: [...document.querySelectorAll("select")].filter(vv).map(s=>[...s.options].map(o=>o.text).slice(0,8)),
    radios: [...new Set([...document.querySelectorAll("[role=radio],input[type=radio]")].filter(vv).map(r=>{const b=r.closest("label")||r.parentElement;return ((b&&b.innerText)||"").replace(/\\s+/g," ").trim().slice(0,30);}))].filter(Boolean).slice(0,20)
  };
})()`;

const goTab = (name) => `(() => {
  const dc=(el)=>{for(const t of ["pointerdown","mousedown","pointerup","mouseup","click"])el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:window}));};
  const vv=(e)=>e.offsetParent!==null;
  const t=[...document.querySelectorAll("div,span,a,li")].filter(e=>vv(e)&&e.children.length===0&&(e.innerText||"").trim()===${JSON.stringify(name)})[0];
  if(!t) return "absent"; dc(t); return "ok";
})()`;

console.log("=== 初始（当前 tab）===");
console.log("ship:", JSON.stringify((await evalJs(dump)).ship));

for (const tab of ["基础信息", "图文信息", "价格库存"]) {
  const r = await evalJs(goTab(tab));
  await new Promise((x) => setTimeout(x, 3000));
  const d = await evalJs(dump);
  console.log(`\n===== tab「${tab}」(${r}) =====`);
  console.log("发货/时效文案:", JSON.stringify(d.ship));
  if (d.sel?.length) console.log("select:", JSON.stringify(d.sel));
  if (d.radios?.length) console.log("radios:", JSON.stringify(d.radios));
  const ins = await evalJs(mapInputs);
  console.log("相关上传位:");
  for (const o of ins) console.log(`  [${o.i}] accept=${o.accept} mul=${o.multiple} y=${o.y} ctx="${o.ctx}"`);
}

// 专找「主图3:4」区块的 DOM 上下文
const r34 = await evalJs(`(() => {
  const vv=(e)=>e.offsetParent!==null;
  const hits=[...document.querySelectorAll("*")].filter(e=>vv(e)&&e.children.length===0&&/主图3:4/.test((e.innerText||"")));
  if(!hits.length) return "not-found";
  const h=hits[0];
  let box=h; for(let i=0;i<6&&box.parentElement;i++){ box=box.parentElement; if(/主图/.test(box.innerText||"")&&box.querySelectorAll("input[type=file]").length) break; }
  const ins=[...box.querySelectorAll("input[type=file]")].map((e,i)=>({i,accept:e.accept,multiple:e.multiple}));
  return {text:(box.innerText||"").replace(/\\s+/g," ").slice(0,260), inputs:ins};
})()`);
console.log("\n=== 主图3:4 区块 ===", JSON.stringify(r34));
ws.close();
