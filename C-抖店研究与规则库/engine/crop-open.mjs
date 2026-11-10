// 点「多图裁切」→ 只读看弹窗结构与按钮（先不动刀）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.find(x => x.type === "page" && String(x.id).startsWith("E15357E6"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});

const pos = await c.eval(`(() => {
  const e=[...document.querySelectorAll('button,[class*=btn],[role=button],a,span,div')]
    .filter(x=>x.offsetParent!==null && /^多图裁切$/.test((x.innerText||'').trim()))[0];
  if(!e) return {found:false};
  const r=e.getBoundingClientRect();
  return {found:true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), tag:e.tagName};
})()`);
console.log("多图裁切:", JSON.stringify(pos));
if (!pos.found) process.exit(1);

await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y, button: "none" });
await sleep(120);
await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
await sleep(90);
await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
await sleep(2500);

const modal = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const btns=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null).map(e=>({txt:(e.innerText||'').trim().slice(0,20), dis:e.disabled===true}))
    .filter(b=>b.txt);
  return { len:t.length, tail:t.slice(-900), btns: btns.slice(0,25) };
})()`);
await c.shot(OUT + "/gen-13-crop.png").catch(() => {});
await saveJson(OUT + "/gen-13-crop.json", modal);
console.log("弹窗尾:\n" + modal.tail);
console.log("可见按钮:", JSON.stringify(modal.btns));
c.close();
