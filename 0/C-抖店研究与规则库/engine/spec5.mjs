// 抓完整预览区 + SKU 表格关键字段，并点「保存草稿」
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

// 抓预览区（消费者效果预览 到 保存草稿）
const pv = await c.eval(`(() => {
  const t=(document.body.innerText||'');
  const i=t.indexOf('消费者效果预览');
  const seg = i>=0 ? t.slice(i, i+700) : t.slice(-700);
  return seg.replace(/\\n{2,}/g,'\\n');
})()`);
console.log("--- 预览区 ---\n"+pv);

// SKU 表格关键字段
const sku = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const ins=[...document.querySelectorAll('input')].filter(e=>vis(e)&&e.closest('table'));
  return ins.map(e=>({ph:e.placeholder,v:e.value})).filter(x=>x.v!=='');
})()`);
console.log("--- SKU 表格已填字段 ---", JSON.stringify(sku));

await c.shot(OUT+"/spec5-preview.png").catch(()=>{});
await saveJson(OUT+"/spec5.json", {pv, sku});
c.close();