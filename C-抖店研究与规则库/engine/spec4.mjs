// 填 SKU 价格 = 399（input[3]），随后校验预览区与必填错误
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "product_id=3851010940816458176" });
await c.send("Page.enable").catch(()=>{});

// 定位 SKU 表格数字输入框（价格），填 399
const r = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const ins=[...document.querySelectorAll('input.ecom-g-input-number-input,input[type=number]')]
    .filter(e=>vis(e)&&(e.closest('table')||e.closest('[class*=specValue],[class*=sku]')));
  const el=ins[0];
  if(!el) return {ok:false,reason:'no-number-input',count:ins.length};
  el.focus(); el.select();
  const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  setter.call(el,'399');
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  el.dispatchEvent(new Event('blur',{bubbles:true}));
  return {ok:true, count:ins.length, v:el.value, ph:el.placeholder};
})()`);
console.log("填价格:", JSON.stringify(r));
await sleep(1600);

// 校验：预览区价格/库存、必填错误数
const chk = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const errs=document.querySelectorAll('.ecom-g-zform-item-has-error,[class*=has-error]').length;
  const m=/价格：[^\\n]{0,14}/.exec(t)||[''];
  const s=/库存：[^\\n]{0,8}/.exec(t)||[''];
  const skuLine=(t.split('\\n').find(l=>l.includes('30*50cm')&&l.includes('￥'))||'').trim();
  return { errs, price:m[0], stock:s[0], skuLine };
})()`);
console.log("校验:", JSON.stringify(chk));
await c.shot(OUT+"/spec4-filled.png").catch(()=>{});
await saveJson(OUT+"/spec4.json", {r, chk});
c.close();