// 抖店上架平台 · 通用上架器（CDP 直连真实浏览器，不抢焦点）
//
//   node publish.mjs --pid 1391 --stage probe    传图→下一步→识别→填标题→落盘状态
//   node publish.mjs --pid 1391 --stage fill     在已生成的编辑页上补品牌/规格/价格库存/服务
//   node publish.mjs --pid 1391 --stage save     填写检查→修错→保存草稿→读回
//
// 设计约束（1454 实测踩出来的）：
//  · create 页只有一个上传入口，AI 自动分流主图/详情 → 按「3 主图 + 1 详情」喂最稳
//  · 一律后台新窗口（Target.createTarget newWindow+background），绝不抢用户焦点
//  · 只在 stage=save 时点保存草稿，从不点发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const WS = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace";
const OUT = WS + "/抖店研究/crawl3";
const PORT = 9224;
const DD = "https://fxg.jinritemai.com";

const arg = (k, d = null) => {
  const i = process.argv.indexOf("--" + k);
  return i >= 0 ? process.argv[i + 1] : d;
};
const pid = arg("pid");
const stage = arg("stage", "probe");
if (!pid) { console.log("用法: node publish.mjs --pid N --stage probe|fill|save"); process.exit(2); }

const plan = JSON.parse(await fs.readFile(`${OUT}/plan-${pid}.json`, "utf8"));
console.log(`== 计划 pid=${pid} | ${String(plan.title).slice(0, 40)}`);
console.log(`   主图 ${plan.main.length} / 详情 ${plan.detail.length} / SKU ${plan.skus.length}`);

// ---- 浏览器级会话：开后台新窗口 ----
const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
const bws = new WebSocket(ver.webSocketDebuggerUrl);
await new Promise((r) => bws.addEventListener("open", r, { once: true }));
let seq = 0; const pend = new Map();
bws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
});
const bsend = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pend.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
  bws.send(JSON.stringify({ id, method, params }));
});
const { targetId } = await bsend("Target.createTarget", { url: "about:blank", newWindow: true, background: true });
let wsUrl = null;
for (let i = 0; i < 20; i++) {
  await sleep(500);
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const t = list.find((x) => x.id === targetId);
  if (t && t.webSocketDebuggerUrl) { wsUrl = t.webSocketDebuggerUrl; break; }
}
bws.close();
if (!wsUrl) { console.log("[!] 拿不到新窗口 ws"); process.exit(3); }
const c = await CDP.connect(wsUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});
console.log("== 后台新窗口 ok:", String(targetId).slice(0, 8));

// ---- 通用动作 ----
async function clickText(re) {
  const pos = await c.eval(`(() => {
    const e=[...document.querySelectorAll('button,[class*=btn],[role=button],a')]
      .filter(x=>x.offsetParent!==null && ${re}.test((x.innerText||'').trim()))[0];
    if(!e) return {found:false};
    const r=e.getBoundingClientRect();
    return {found:true, dis:e.disabled===true, txt:(e.innerText||'').trim().slice(0,20),
            x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  })()`);
  if (!pos.found || pos.dis) return pos;
  await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y, button: "none" });
  await sleep(120);
  await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  await sleep(90);
  await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  return pos;
}

async function uploadFiles(files) {
  const doc = await c.send("DOM.getDocument", { depth: -1, pierce: true });
  const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
  if (!q.nodeIds.length) return 0;
  await c.send("DOM.setFileInputFiles", { files, nodeId: q.nodeIds[0] });
  return q.nodeIds.length;
}

async function fillTitle(title) {
  return await c.eval(`(() => {
    const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
    const el = all.find(e => /2-60/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
    if(!el) return {ok:false};
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
    setter.call(el, ${JSON.stringify(title)});
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    return {ok:true, now: el.value};
  })()`);
}

async function dumpState(tag) {
  const s = await c.eval(`(() => {
    const vis=e=>e.offsetParent!==null;
    const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
    const errs=[...document.querySelectorAll('[class*="has-error"]')].map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,60));
    const prog=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&/^\\d+%$/.test((e.innerText||'').trim())).map(e=>e.innerText.trim());
    const btns=[...document.querySelectorAll('button,[class*=btn],[role=button]')].filter(vis)
      .map(e=>({t:(e.innerText||'').trim().slice(0,20),d:e.disabled===true})).filter(b=>b.t);
    return {url:location.href, len:t.length, errN:errs.length, errs:errs.slice(0,15), prog:prog.slice(0,5), btns:btns.slice(0,40), txt:t};
  })()`);
  await saveJson(`${OUT}/pub-${pid}-${tag}.json`, s);
  await c.shot(`${OUT}/pub-${pid}-${tag}.png`).catch(() => {});
  console.log(`[${tag}] url=${s.url} len=${s.len} err=${s.errN} prog=${JSON.stringify(s.prog)}`);
  return s;
}

// ---- stage: probe ----
if (stage === "probe") {
  await c.send("Page.navigate", { url: DD + "/ffa/g/create" }).catch(() => {});
  let ready = false;
  for (let i = 1; i <= 40; i++) {
    await sleep(1500);
    const s = await c.eval(`(() => ({i:document.querySelectorAll('input[type=file]').length,
      ui:/点击或拖拽|已上传/.test(document.body.innerText||''),
      btn:[...document.querySelectorAll('button')].some(b=>/下一步/.test(b.innerText||''))}))()`).catch(() => ({}));
    if (s.i > 0 && s.ui && s.btn) { ready = true; console.log(`== 表单就绪 ${(i * 1.5).toFixed(0)}s`); break; }
  }
  if (!ready) { console.log("[!] create 页未就绪"); process.exit(4); }

  const files = [...plan.main.slice(0, 3), plan.detail[0]].filter(Boolean);
  for (const f of files) await fs.access(f);
  console.log("== 投递", files.length, "张:", files.map((f) => f.split(/[\\/]/).pop()).join(","));
  await uploadFiles(files);

  let up = 0;
  for (let i = 1; i <= 60; i++) {
    await sleep(1500);
    const s = await c.eval(`(() => { const t=document.body.innerText||'';
      return {n:Number((t.match(/已上传 (\\d+)/)||[0,0])[1]), full:/容量已满/.test(t),
              fail:/上传失败/.test(t), busy:/上传中|正在上传|解析中|识别中/.test(t)}; })()`);
    if (s.full || s.fail) { console.log("[!] 上传受阻 full=", s.full, "fail=", s.fail); break; }
    if (s.n >= files.length && !s.busy) { up = s.n; break; }
    if (i % 6 === 0) console.log(`   ${(i * 1.5).toFixed(0)}s 已上传=${s.n}`);
  }
  console.log("== 上传完成", up, "/", files.length);
  if (up < files.length) { await dumpState("upload-fail"); process.exit(5); }

  console.log("== 点下一步:", JSON.stringify(await clickText("/下一步，生成商品/")));
  let rec = false;
  for (let i = 1; i <= 45; i++) {
    await sleep(2000);
    const s = await c.eval(`(() => { const t=document.body.innerText||'';
      return {rec:/已识别并分类商品材料/.test(t), busy:/生成中|识别中|请稍候/.test(t)}; })()`);
    if (s.rec && !s.busy) { rec = true; console.log(`== 已识别 ${i * 2}s`); break; }
    if (i % 6 === 0) console.log(`   ${i * 2}s rec=${s.rec} busy=${s.busy}`);
  }
  if (!rec) console.log("[!] 未进入识别完成态，继续 dump");

  console.log("== 填标题:", JSON.stringify(await fillTitle(plan.title)));
  await sleep(2500);
  const st = await dumpState("probe");
  console.log("\n---- 页面文本（截 3000）----\n" + st.txt.slice(0, 3000));
  console.log("\n---- 按钮 ----\n" + JSON.stringify(st.btns));
  console.log("\nTAB=" + targetId);
  c.close();
  process.exit(0);
}

console.log("[!] stage " + stage + " 未实现（先跑 probe）");
c.close();
process.exit(6);
