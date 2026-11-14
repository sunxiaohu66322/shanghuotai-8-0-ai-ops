// 抓 SKU 区域当前真实状态：规格名/值/价格/库存，判断规格组合是否已生效
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

const st = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const t=(document.body.innerText||'');
  // 规格卡片区域：从「商品规格」到「价格与库存」到表格
  const gi=t.indexOf('商品规格');
  const seg=gi>=0?t.slice(gi, gi+900).replace(/\\n{2,}/g,'\\n'):'';
  // 所有可见 input（不限 table），带上下文
  const ins=[...document.querySelectorAll('input')].filter(e=>vis(e))
    .map((e,i)=>({i,ph:e.placeholder||'',al:e.getAttribute('aria-label')||'',v:e.value,cls:(e.className||'').toString().slice(0,28),
       inSpec: !!(e.closest('[class*=specName],[class*=specValue],[class*=sku],[class*=specTable]'))}))
    .filter(x=>x.inSpec||/价格|库存|尺寸|请输入/.test(x.ph));
  return { seg, ins, hasSpecBtn: t.includes('请选择规格'), hasConfirm: t.includes('确定')||t.includes('确认规格') };
})()`);
console.log("--- 规格区 ---\n"+st.seg);
console.log("仍显示请选择规格:", st.hasSpecBtn, "| 有确定按钮:", st.hasConfirm);
console.log("--- 相关 input ---");
(st.ins||[]).forEach(x=>console.log(`[${x.i}]${x.inSpec?'*':''} ph="${x.ph}" v="${x.v}" ${x.cls}`));
await c.shot(OUT+"/spec6-state.png").catch(()=>{});
await saveJson(OUT+"/spec6.json", st);
c.close();