// 草稿编辑页状态体检：品牌是否已选、还差哪些必填、价格库存值、使用品牌名勾选
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = (await (await fetch("http://127.0.0.1:9224/json/list")).json())
  .filter(x => x.type === "page" && (x.url || "").includes("/ffa/g/create"));
const t = tabs.find(x => (x.url||"").includes("product_id=3851010940816458176")) || tabs.find(x => x.id.startsWith("000748ED"));
if (!t) { console.log("找不到草稿编辑页"); process.exit(1); }
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});
console.log("URL:", (await c.eval("location.href")).slice(0, 120));

const st = await c.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  // 1) 品牌值
  const lab = [...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='品牌' && vis(e))[0];
  let brand = null, brandOk = null;
  if (lab) { let row = lab; for (let i=0;i<4;i++) if (row.parentElement) row = row.parentElement;
    brand = (row.innerText||'').replace(/\\n{2,}/g,'\\n').trim();
    brandOk = !!row.querySelector('[class*=has-success]'); }
  // 2) 使用品牌名
  const ub = [...document.querySelectorAll('input[type=checkbox]')].filter(e=>{const l=e.closest('label')||e.parentElement; return /使用品牌名/.test((l&&l.innerText)||'');})[0];
  // 3) 还缺哪些必填（校验态）
  const errs = [...document.querySelectorAll('[class*="has-error"]')].filter(vis).map(e=>{
    let row=e; for(let i=0;i<3;i++) if(row.parentElement) row=row.parentElement;
    const txt=(row.innerText||'').replace(/\\n{2,}/g,'\\n').trim().split('\\n');
    return { label: (txt.find(x=>x && x!=='*')||'').slice(0,20), msg:(txt.filter(x=>/必填|请选择|请输入/.test(x))[0]||'').slice(0,40) };
  }).filter((v,i,a)=>a.findIndex(x=>x.label===v.label)===i);
  // 4) 价格/库存
  const num = [...document.querySelectorAll('input')].filter(vis).map(e=>({ph:e.placeholder||'',v:e.value})).filter(x=>x.v);
  return { brand, brandOk, useBrandName: ub ? ub.checked : null,
           errCount: errs.length, errs: errs.slice(0,12),
           nums: num.filter(x=>/价|库存|重量|量/.test(x.ph)).slice(0,14),
           bodyTail: (document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(-500) };
})()`);
await saveJson(OUT + "/draft-edit-state.json", st);
console.log("\n品牌行:", JSON.stringify(st.brand), "has-success:", st.brandOk);
console.log("使用品牌名勾选:", st.useBrandName);
console.log("剩余校验错误:", st.errCount);
st.errs.forEach((e,i)=>console.log("  ",i+1,e.label,"|",e.msg));
console.log("\n价格库存输入:");
st.nums.forEach(n=>console.log("   ", n.ph, "=", n.v));
console.log("\n页尾:\n"+st.bodyTail);
c.close();
