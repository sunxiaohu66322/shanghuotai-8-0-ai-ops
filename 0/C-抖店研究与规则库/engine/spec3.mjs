// 填 SKU 价格 399：先 dump SKU 表格 input，定位价格框，填入，再验证预览区
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

// dump SKU 表格所有可见 input
const dump = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const ins=[...document.querySelectorAll('input')].filter(e=>vis(e)&&(e.closest('table')||e.closest('[class*=sku],[class*=specValue],[class*=specName]')));
  return ins.slice(0,60).map((e,i)=>({i, ph:e.placeholder||'', al:e.getAttribute('aria-label')||'', name:e.name||'', v:e.value, cls:(e.className||'').toString().slice(0,30)}));
})()`);
console.log("--- SKU 区 input ---");
(dump||[]).forEach(d=>console.log(`[${d.i}] ph="${d.ph}" al="${d.al}" name="${d.name}" v="${d.v}" cls=${d.cls}`));

// 定位价格框：placeholder 含价格 或 aria-label 含价格，且 value 为空
const pIdx = (dump||[]).findIndex(d=>/价格|price/i.test(d.ph+d.al) );
console.log("价格框索引:", pIdx);
let filled = null;
if (pIdx >= 0) {
  filled = await c.eval(`(() => {
    const vis=e=>e.offsetParent!==null;
    const ins=[...document.querySelectorAll('input')].filter(e=>vis(e)&&(e.closest('table')||e.closest('[class*=sku],[class*=specValue]')));
    const el=ins[${pIdx}];
    if(!el) return {ok:false,reason:'oob'};
    el.focus(); el.select();
    const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    setter.call(el,'399');
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    el.dispatchEvent(new Event('blur',{bubbles:true}));
    return {ok:true, ph:el.placeholder, v:el.value};
  })()`);
  console.log("填价格:", JSON.stringify(filled));
}
await sleep(1500);
await c.shot(OUT+"/spec3-price.png").catch(()=>{});
await saveJson(OUT+"/spec3.json", {dump, filled});
c.close();