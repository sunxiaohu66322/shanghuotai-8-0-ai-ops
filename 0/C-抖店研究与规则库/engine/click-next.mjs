// 用 CDP 真实鼠标事件点「下一步，生成商品」（比 JS .click() 更接近真人，React 才认）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const cands = tabs.filter(x => x.type === "page" && (x.url || "").includes("/ffa/g/create"));
let c = null;
for (const t of cands) {
  const cc = await CDP.connect(t.webSocketDebuggerUrl);
  for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await cc.send(m).catch(() => {});
  const n = Number(await cc.eval(`(document.body.innerText.match(/已上传 (\\d+)/)||[0,0])[1]`).catch(() => 0));
  console.log("tab", String(t.id).slice(0, 8), "已上传=", n);
  if (n === 4 && !c) { c = cc; break; }
  cc.close();
}
if (!c) { console.log("没有已上传4张的页面"); process.exit(1); }

// 1) 看按钮几何与禁用态
const info = await c.eval(`(() => {
  const all=[...document.querySelectorAll('button,[class*=btn],[role=button],div,span')]
    .filter(e=>/^下一步，生成商品$/.test((e.innerText||'').trim()) && e.offsetParent!==null);
  const e=all[all.length-1];
  if(!e) return {found:false};
  const r=e.getBoundingClientRect();
  return {found:true, tag:e.tagName, cls:(e.className||'').toString().slice(0,120),
          dis:e.disabled===true||e.getAttribute('aria-disabled')==='true',
          x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), w:Math.round(r.width), h:Math.round(r.height)};
})()`);
console.log("按钮:", JSON.stringify(info));
if (!info.found) process.exit(1);

// 2) 真实鼠标点击
await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: info.x, y: info.y, button: "none" });
await sleep(150);
await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: info.x, y: info.y, button: "left", clickCount: 1 });
await sleep(90);
await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: info.x, y: info.y, button: "left", clickCount: 1 });
console.log("已派发真实点击");

// 3) 长观察（AI 识别可能较慢）
let prevPath = "";
for (let i = 1; i <= 72; i++) {
  await sleep(2500);
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
    return { path: location.pathname + (location.search||'').slice(0,80), len:t.length,
             gen:/生成中|识别中|正在生成|请稍候|解析/.test(t),
             hasCat:/类目/.test(t), head:t.slice(0,160) };
  })()`);
  if (s.path !== prevPath) { console.log(`  ${(i*2.5).toFixed(0)}s PATH→ ${s.path} len=${s.len}`); prevPath = s.path; }
  if (i % 8 === 0) console.log(`  ${(i*2.5).toFixed(0)}s len=${s.len} gen=${s.gen} cat=${s.hasCat}`);
  if (!s.gen && s.path !== "/ffa/g/create" && i > 3) { console.log("已离开建品页:", s.path); break; }
  if (i > 20 && s.len > 1500) { console.log("页面内容已变厚:", s.len); break; }
}
await c.shot(OUT + "/gen-02-result.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { url: location.href, len: t.length, txt: t.slice(0, 5000) };
})()`);
await saveJson(OUT + "/gen-02-page.json", page);
console.log("\nURL:", page.url, "LEN:", page.len);
console.log(page.txt);
c.close();
