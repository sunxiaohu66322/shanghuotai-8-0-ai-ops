// 品牌已确认 LZL。下一步：打开「请选择规格」看规格面板结构（价格/库存挂在规格上）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = (await (await fetch("http://127.0.0.1:9224/json/list")).json())
  .filter(x => x.type === "page" && (x.url || "").includes("product_id=3851010940816458176"));
if (!tabs.length) { console.log("找不到草稿页"); process.exit(1); }
const c = await CDP.connect(tabs[0].webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});

// 滚到「商品规格」
await c.eval(`(() => {
  const n=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='商品规格' && e.offsetParent!==null)[0];
  if(n) n.scrollIntoView({block:'center'});
})()`);
await sleep(700);

const before = await c.eval(`(() => {
  const t=(document.body.innerText||'');
  return { specHint: t.includes('请选择规格'), priceHint: (t.match(/价格：[^\\n]{0,12}/)||[''])[0],
           stockHint: (t.match(/库存：[^\\n]{0,8}/)||[''])[0],
           skuRows: document.querySelectorAll('[class*=sku]').length };
})()`);
console.log("点了之前:", JSON.stringify(before));

// 找「请选择规格」并真实点击
const pos = await c.eval(`(() => {
  const n=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='请选择规格' && e.offsetParent!==null)[0];
  if(!n) return null;
  const r=n.getBoundingClientRect();
  return { x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2), w:Math.round(r.width) };
})()`);
console.log("请选择规格位置:", JSON.stringify(pos));
if (!pos) { console.log("没找到"); process.exit(2); }

await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y, button: "none" });
await sleep(150);
await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
await sleep(80);
await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
await sleep(1500);

await c.shot(OUT + "/spec-panel.png").catch(() => {});
const after = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  // 抓所有可见的勾选项/下拉浮层里的选项
  const opts=[...document.querySelectorAll('[class*=option],[class*=Option],[role=option],li,label')]
    .filter(e=>vis(e) && (e.innerText||'').trim().length>0 && (e.innerText||'').trim().length<40)
    .map(e=>({t:(e.innerText||'').trim(), cls:(e.className||'').toString().slice(0,40)}));
  const uniq=[...new Map(opts.map(o=>[o.t,o])).values()].slice(0,40);
  return { len:t.length, opts:uniq, tail:t.slice(-800) };
})()`);
await saveJson(OUT + "/spec-panel.json", after);
console.log("\n可见选项/标签:", after.opts.length);
after.opts.forEach(o=>console.log("   ", o.t, "|", o.cls));
console.log("\n页尾:\n"+after.tail);
c.close();
