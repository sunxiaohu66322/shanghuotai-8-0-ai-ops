// 清空多余预留库存 → 点「保存草稿」→ 校验保存结果
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

// 1) 清空预留库存：点 SKU 表格「预留库存」列旁的「清空」
const clr = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  // 预留库存单元格：表格里含「预留库存」表头，下面那行的清空按钮
  const th=[...document.querySelectorAll('th,td')].filter(e=>(e.innerText||'').trim()==='预留库存'&&vis(e))[0];
  if(!th) return {ok:false,reason:'no-th'};
  const row=th.closest('tr')||th.closest('table');
  const btn=[...row.querySelectorAll('a,button,span,[class*=link]')]
    .filter(e=>vis(e)&&(e.innerText||'').trim()==='清空')[0];
  if(btn){ btn.click(); return {ok:true, t:'清空预留库存'}; }
  return {ok:false,reason:'no-btn', rowText:(row.innerText||'').slice(0,200)};
})()`);
console.log("清空预留库存:", JSON.stringify(clr));
await sleep(1000);

// 2) 点「保存草稿」
const save = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const b=[...document.querySelectorAll('button,.ecom-g-btn,[role=button]')]
    .filter(e=>vis(e)&&(e.innerText||'').trim()==='保存草稿')[0];
  if(!b) return {ok:false,reason:'no-save-btn'};
  b.scrollIntoView({block:'center'}); b.click();
  return {ok:true};
})()`);
console.log("保存草稿:", JSON.stringify(save));
await sleep(3500);

// 3) 抓结果：弹窗/提示/是否跳转
const res = await c.eval(`(() => {
  const t=(document.body.innerText||'');
  const url=location.href;
  const toast=(t.match(/保存成功[^\\n]{0,30}/)||t.match(/成功[^\\n]{0,30}/)||['']);
  const dialog=[...document.querySelectorAll('[class*=dialog],[class*=modal],[class*=toast]')]
    .filter(e=>e.offsetParent!==null&&(e.innerText||'').trim().length>0)
    .map(e=>(e.innerText||'').trim().slice(0,120))[0]||'';
  return {url, toast:toast[0], dialog};
})()`);
console.log("结果:", JSON.stringify(res));
await c.shot(OUT+"/spec8-saved.png").catch(()=>{});
await saveJson(OUT+"/spec8.json", {clr, save, res});
c.close();