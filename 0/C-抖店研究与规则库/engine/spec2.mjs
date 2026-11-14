// 探测规格面板：点开「请选择规格」，抓取规格名/规格值/可勾选项
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
if (!c.target) { console.log("找不到草稿页"); process.exit(1); }
await c.send("Page.enable").catch(()=>{});
await c.send("Runtime.enable").catch(()=>{});

// 滚到规格区
await c.eval(`(() => {
  const n=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='商品规格' && e.offsetParent!==null)[0];
  if(n) n.scrollIntoView({block:'center'});
})()`);
await sleep(600);

// 点开「请选择规格」
const pos = await c.eval(`(() => {
  const n=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='请选择规格' && e.offsetParent!==null)[0];
  if(!n) return null;
  const r=n.getBoundingClientRect();
  return { x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2) };
})()`);
if (pos) {
  await c.send("Input.dispatchMouseEvent",{type:"mousePressed",x:pos.x,y:pos.y,button:"left",clickCount:1});
  await c.send("Input.dispatchMouseEvent",{type:"mouseReleased",x:pos.x,y:pos.y,button:"left",clickCount:1});
  await sleep(1200);
}

// 抓规格卡片 DOM 结构：规格名、规格值 chip、勾选态
const info = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  // 定位「商品规格」卡
  const hd=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='商品规格' && vis(e))[0];
  let card=null;
  if(hd){ let p=hd; for(let i=0;i<6&&p;i++){ p=p.parentElement; if(p&&(p.innerText||'').includes('价格')&&(p.innerText||'').length<3000){ card=p; break; } } }
  const scope=card||document.body;
  const txt=(scope.innerText||'').replace(/\\n{2,}/g,'\\n');
  // 抓规格名下拉、规格值 chip（class 含 tag/chip/check/option）
  const chips=[...scope.querySelectorAll('span,div,label')].filter(e=>vis(e)&&(e.className||'').toString().toLowerCase().match(/tag|chip|option|check|spec|select/)&&(e.innerText||'').trim().length>0&&(e.innerText||'').trim().length<25)
    .map(e=>({t:(e.innerText||'').trim(),cls:(e.className||'').toString().slice(0,50),sel:!!e.querySelector('[class*=selected],[class*=checked],input:checked')}));
  const uniq=[...new Map(chips.map(o=>[o.t,o])).values()].slice(0,60);
  return { cardLen:(card?card.innerText.length:0), tail:txt.slice(-1200), chips:uniq,
           skuRows: scope.querySelectorAll('[class*=sku]').length,
           specHint: txt.includes('请选择规格') };
})()`);
console.log("卡片字符:", info.cardLen, "| 仍显示请选择规格:", info.specHint, "| sku元素:", info.skuRows);
console.log("--- 规格值候选 ---");
(info.chips||[]).forEach(o=>console.log("   ["+(o.sel?'√':' ')+"]", o.t, "|", o.cls));
console.log("--- 规格区尾文 ---\n"+info.tail);
await saveJson(OUT+"/spec2.json", info);
await c.shot(OUT+"/spec2.png").catch(()=>{});
c.close();