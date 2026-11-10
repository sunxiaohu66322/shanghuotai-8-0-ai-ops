// v2：在「后台新窗口」里跑（newWindow + background，绝不抢焦点）
// 流程：传图 → 下一步 → 填标题 → 读类目区。不点确认、不存草稿、不发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const WORK = "G:\\Users\\Administrator\\Documents\\deepseek-harness\\default-workspace\\抖店研究\\work\\1454";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const title = plan.title;
const files = [
  `${WORK}\\sq_image_3da0d718bbce.jpg`,
  `${WORK}\\sq_image_635d76352013.jpg`,
  `${WORK}\\sq_image_af26132d18c8.jpg`,
  `${WORK}\\dt_image_50e4f63db3f1.jpg`,
];

// —— 用浏览器级会话开一个「后台新窗口」，不抢焦点 ——
const ver = await (await fetch("http://127.0.0.1:9224/json/version")).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let seq = 0; const pend = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
});
const bsend = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq; pend.set(id, (m) => m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result));
  ws.send(JSON.stringify({ id, method, params }));
});
const { targetId } = await bsend("Target.createTarget", {
  url: "about:blank", newWindow: true, background: true,
});
console.log("后台新窗口 targetId:", String(targetId).slice(0, 8));

let wsUrl = null;
for (let i = 0; i < 20; i++) {
  await sleep(500);
  const list = await (await fetch("http://127.0.0.1:9224/json/list")).json();
  const t = list.find(x => x.id === targetId);
  if (t && t.webSocketDebuggerUrl) { wsUrl = t.webSocketDebuggerUrl; break; }
}
ws.close();
if (!wsUrl) { console.log("拿不到该 target 的 ws 地址"); process.exit(1); }

const c = await CDP.connect(wsUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});
await c.send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/create" }).catch(() => {});

// 等页面真正可交互：既要 input 出现，也要上传区文案渲染出来
let ready = false;
for (let i = 1; i <= 40; i++) {
  await sleep(1500);
  const s = await c.eval(`(() => ({
    inputs: document.querySelectorAll('input[type=file]').length,
    ui: /点击或拖拽|已上传/.test(document.body.innerText||''),
    btn: [...document.querySelectorAll('button')].some(b=>/下一步/.test(b.innerText||''))
  }))()`).catch(() => ({}));
  if (i % 4 === 0) console.log(`  ${(i * 1.5).toFixed(0)}s inputs=${s.inputs} ui=${s.ui} btn=${s.btn}`);
  if (s.inputs > 0 && s.ui && s.btn) { await sleep(3000); console.log(`✅ 可交互 (${(i * 1.5).toFixed(0)}s)`); ready = true; break; }
}
if (!ready) { console.log("页面未就绪"); process.exit(1); }

// 投图（每次现场重取 document，避免拿到已卸载节点）
async function upload(fs_) {
  const doc = await c.send("DOM.getDocument", { depth: 2, pierce: true });
  const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
  if (!q.nodeIds.length) return 0;
  await c.send("DOM.setFileInputFiles", { files: fs_, nodeId: q.nodeIds[0] });
  return q.nodeIds.length;
}
console.log("投递 4 张:", await upload(files));

let count = 0;
for (let r = 0; r < 3; r++) {
  let sawError = false;
  for (let i = 1; i <= 14; i++) {
    await sleep(1500);
    const s = await c.eval(`(() => {
      const t=(document.body.innerText||'');
      return { n:Number((t.match(/已上传 (\\d+)/)||[0,0])[1]), full:/容量已满/.test(t), fail:/上传失败/.test(t),
               busy:/上传中|正在上传|解析中|识别中/.test(t) };
    })()`);
    if (i % 3 === 0) console.log(`  轮${r + 1} ${(i * 1.5).toFixed(0)}s 已上传=${s.n} full=${s.full} fail=${s.fail}`);
    if (s.full) { console.log("素材库仍满"); process.exit(1); }
    if (s.fail) { sawError = true; break; }
    if (s.n >= 4 && !s.busy) { count = s.n; break; }
  }
  if (count >= 4) break;
  if (r < 2 && !sawError) { console.log("  重投一次 …"); await sleep(2000); await upload(files); }
}
if (count < 4) { console.log("上传未完成（已上传=" + count + "），停在这里"); process.exit(1); }
console.log("✅ 4 张已上传");

async function clickByText(src) {
  const pos = await c.eval(`(() => {
    const e=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
      .filter(x=>x.offsetParent!==null && ${src}.test((x.innerText||'').trim()))[0];
    if(!e) return {found:false};
    const r=e.getBoundingClientRect();
    return {found:true, dis:e.disabled===true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  })()`);
  if (!pos.found || pos.dis) return pos;
  await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y, button: "none" });
  await sleep(120);
  await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  await sleep(90);
  await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  return pos;
}
console.log("点下一步:", JSON.stringify(await clickByText("/下一步，生成商品/")));

let rec = false;
for (let i = 1; i <= 45; i++) {
  await sleep(2000);
  const s = await c.eval(`(() => { const t=document.body.innerText||'';
    return { rec:/已识别并分类商品材料/.test(t), busy:/生成中|识别中|请稍候/.test(t), len:t.length }; })()`);
  if (i % 5 === 0) console.log(`  ${i * 2}s 识别完成=${s.rec} busy=${s.busy} len=${s.len}`);
  if (s.rec && !s.busy) { rec = true; console.log("✅ 已识别"); break; }
}
if (!rec) { console.log("未进入识别完成态"); }

const filled = await c.eval(`(() => {
  const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
  const el = all.find(e => /2-60/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
  if(!el) return {ok:false};
  const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
  setter.call(el, ${JSON.stringify(title)});
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  return {ok:true, now: el.value};
})()`);
console.log("填标题:", JSON.stringify(filled));
await sleep(2500);

await c.shot(OUT + "/v2-recognized.png").catch(() => {});
const dump = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const btns=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null).map(e=>({txt:(e.innerText||'').trim().slice(0,22), dis:e.disabled===true}))
    .filter(b=>b.txt);
  return { url: location.href, len: t.length, txt: t, btns };
})()`);
await saveJson(OUT + "/v2-recognized.json", dump);
console.log("\nLEN:", dump.len);
console.log(dump.txt);
console.log("\n按钮:", JSON.stringify(dump.btns));
console.log("\nTAB=" + targetId);
c.close();
