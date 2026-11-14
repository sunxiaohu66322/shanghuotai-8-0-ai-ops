// 终验：进草稿箱，找 product_id=3851010940816458176，抓规格/价格/库存/图片
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});
await c.send("Network.enable").catch(()=>{});

// 关掉保存成功弹窗
await c.eval(`(() => {
  const b=[...document.querySelectorAll('button,.ecom-g-btn,[role=button]')].find(e=>(e.innerText||'').trim()==='我知道了'&&e.offsetParent!==null);
  if(b) b.click();
})()`).catch(()=>{});
await sleep(800);

// 导航到草稿箱
await c.send("Page.navigate",{url:"https://fxg.jinritemai.com/ffa/g/draft"});
await sleep(2500);
await c.waitReady(4000).catch(()=>{});
await sleep(2500);
console.log("URL:", (await c.eval("location.href")).slice(0,120));

// 找商品行
const row = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const rows=[...document.querySelectorAll('tr,[class*=table-row],[class*=list-item],li,div[class*=item]')]
    .filter(e=>vis(e)&&(e.innerText||'').includes('3851010940816458176'));
  if(!rows.length) return {found:false, body:(document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,1500)};
  const e=rows[0];
  return {found:true, text:(e.innerText||'').replace(/\\n{2,}/g,'\\n').trim().slice(0,600), tag:e.tagName};
})()`);
console.log("草稿箱:", JSON.stringify(row));
await c.shot(OUT+"/spec9-draftbox.png").catch(()=>{});
await saveJson(OUT+"/spec9.json", row);
c.close();