// 只做一件事：正确填标题，并报告按钮是否解锁（不点击）
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const title = plan.title;

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.find(x => x.type === "page" && String(x.id).startsWith("E15357E6"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});

// 定位那个标题框：可见 input，placeholder 含 2-60
const found = await c.eval(`(() => {
  const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
  const el = all.find(e => /2-60|请输入2/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
  if(!el) return {ok:false, all: all.map(e=>({tag:e.tagName, ph:e.placeholder, ml:e.maxLength}))};
  const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
  setter.call(el, ${JSON.stringify(title)});
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  el.focus(); el.blur();
  return {ok:true, now: el.value, ml: el.maxLength};
})()`);
console.log("填标题:", JSON.stringify(found));
await sleep(1500);

const st = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const btns=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null && /确认信息/.test((e.innerText||'').trim()))
    .map(e=>({dis:e.disabled===true||e.getAttribute('aria-disabled')==='true', cls:(e.className||'').toString().slice(0,60)}));
  return { cnt:(t.match(/(\\d+)\\/60/)||[0,'?'])[1],
           warn:(t.match(/[^\\n]{0,30}(不符合要求|需为1:1)[^\\n]{0,20}/g)||[]),
           btns, title:(document.querySelector('input[maxlength="60"]')||{}).value };
})()`);
console.log("状态:", JSON.stringify(st, null, 1));

// 若仍有比例警告，找「多图裁切」按钮几何
const crop = await c.eval(`(() => {
  const e=[...document.querySelectorAll('button,[class*=btn],[role=button],a,span')]
    .filter(x=>x.offsetParent!==null && /多图裁切/.test((x.innerText||'').trim()))[0];
  if(!e) return {found:false};
  const r=e.getBoundingClientRect();
  return {found:true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
})()`);
console.log("多图裁切:", JSON.stringify(crop));
await c.shot(OUT + "/gen-12-titled.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { url: location.href, len: t.length, txt: t.slice(0,2500) };
})()`);
await saveJson(OUT + "/gen-12-page.json", page);
console.log(page.txt);
c.close();
