// 找规格面板的确认按钮：抓「商品规格」卡片内所有 button/可点元素，并统计 SKU 行
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

const r = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const hd=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='商品规格' && vis(e))[0];
  let card=null,p=hd;
  for(let i=0;i<8&&p;i++){ p=p.parentElement; if(p&&(p.innerText||'').includes('价格填写进度')&&p.innerText.length<6000){card=p;break;} }
  const scope=card||document.body;
  // 卡片内所有 button
  const btns=[...scope.querySelectorAll('button,[role=button],.ecom-g-btn,span[class*=button]')]
    .filter(e=>vis(e)&&(e.innerText||'').trim().length>0&&(e.innerText||'').trim().length<14)
    .map(e=>({t:(e.innerText||'').trim(),cls:(e.className||'').toString().slice(0,32)}));
  const uniq=[...new Map(btns.map(b=>[b.t,b])).values()];
  // SKU 表格行数：按「30*50cm」出现次数 + 尺寸输入框数
  const dimIns=scope.querySelectorAll('input[placeholder="请输入尺寸"]').length;
  return { cardLen:scope.innerText.length, uniq, dimIns };
})()`);
console.log("规格卡片字符:", r.cardLen, "| 尺寸输入框数:", r.dimIns);
console.log("--- 规格卡片按钮 ---");
(r.uniq||[]).forEach(b=>console.log("  ", b.t, "|", b.cls));
await c.shot(OUT+"/spec7-btns.png").catch(()=>{});
await saveJson(OUT+"/spec7.json", r);
c.close();