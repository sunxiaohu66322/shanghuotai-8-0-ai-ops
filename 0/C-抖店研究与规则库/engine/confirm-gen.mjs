// 生成页 → 填标题 → 点「确认信息，继续生成」→ 读系统定的类目
// 不点发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const title = plan.title;

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.find(x => x.type === "page" && String(x.id).startsWith("E15357E6"));
if (!t) { console.log("找不到目标 tab"); process.exit(1); }
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

await c.shot(OUT + "/gen-10-recognized.png").catch(() => {});
console.log("当前:", await c.eval("location.href"));

// 1) 填商品标题（用原生 setter，React 才认）
const filled = await c.eval(`(() => {
  const t = ${JSON.stringify(title)};
  const cands = [...document.querySelectorAll('input,textarea')].filter(e => e.offsetParent!==null);
  const el = cands.find(e => (e.placeholder||'').includes('标题')) || cands.find(e => e.maxLength===60 || e.getAttribute('maxlength')==='60');
  if (!el) return {ok:false, n: cands.length, phs: cands.slice(0,12).map(e=>e.placeholder||e.tagName)};
  const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value').set;
  setter.call(el, t);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true, now: el.value};
})()`);
console.log("填标题:", JSON.stringify(filled));
await sleep(800);

// 2) 找「确认信息，继续生成」并真实点击
const info = await c.eval(`(() => {
  const all=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null && /确认信息/.test((e.innerText||'').trim()));
  if(!all.length) return {found:false};
  const e=all[0], r=e.getBoundingClientRect();
  return {found:true, dis:e.disabled===true||e.getAttribute('aria-disabled')==='true',
          x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), txt:(e.innerText||'').trim()};
})()`);
console.log("按钮:", JSON.stringify(info));
if (!info.found) {
  const txt = await c.eval(`(document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(-600)`);
  console.log("页面尾部:\n" + txt); process.exit(1);
}

await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: info.x, y: info.y, button: "none" });
await sleep(120);
await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: info.x, y: info.y, button: "left", clickCount: 1 });
await sleep(90);
await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: info.x, y: info.y, button: "left", clickCount: 1 });
console.log("已点「确认信息，继续生成」");

// 3) 观察
let prev = "";
for (let i = 1; i <= 60; i++) {
  await sleep(2500);
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
    return { path: location.pathname + (location.search||'').slice(0,70), len:t.length,
             cat:(t.match(/类目[^\\n]{0,60}/)||[''])[0], busy:/生成中|识别中|请稍候|保存中/.test(t),
             head:t.slice(0,120) };
  })()`);
  if (s.path !== prev) { console.log(`  ${(i*2.5).toFixed(0)}s PATH→ ${s.path} len=${s.len}`); prev = s.path; }
  if (i % 8 === 0) console.log(`  ${(i*2.5).toFixed(0)}s len=${s.len} cat=${s.cat}`);
  if (s.path !== "/ffa/g/create" && !s.busy && i > 3) { console.log("已进入下一阶段"); break; }
  if (i > 24 && s.len > 2000) { console.log("内容变厚 len=" + s.len); break; }
}
await c.shot(OUT + "/gen-11-next.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const inp=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null)
    .slice(0,50).map(e=>({ph:(e.placeholder||'').slice(0,30), v:(e.value||'').slice(0,40), ml:e.maxLength}));
  return { url: location.href, len: t.length, txt: t.slice(0, 4500), inputs: inp };
})()`);
await saveJson(OUT + "/gen-11-page.json", page);
console.log("\nURL:", page.url, "LEN:", page.len);
console.log(page.txt);
c.close();
