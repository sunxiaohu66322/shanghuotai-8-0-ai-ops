// 只针对 000748ED（茶几/边几那张完整表单）先把「品牌」这一行的真实控件结构打出来
import { CDP, sleep } from "./cdp.mjs";

const TID = process.argv[2] || "000748ED";
const tabs = (await (await fetch("http://127.0.0.1:9224/json/list")).json())
  .filter(x => x.type === "page" && x.id.startsWith(TID));
if (!tabs.length) { console.log("找不到标签", TID); process.exit(1); }
const c = await CDP.connect(tabs[0].webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});
console.log("URL:", (await c.eval("location.href")).slice(0, 90));

// 滚到品牌行
await c.eval(`(() => {
  const all=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='品牌' && e.offsetParent!==null);
  if(all.length) all[0].scrollIntoView({block:'center'});
})()`);
await sleep(700);

const out = await c.eval(`(() => {
  const labs=[...document.querySelectorAll('*')].filter(e=>(e.innerText||'').trim()==='品牌' && e.offsetParent!==null);
  if(!labs.length) return {err:'no label', bodyLen:(document.body.innerText||'').length};
  const lab = labs[0];
  // 找表单行容器：往上 4 层
  let row = lab;
  for (let i=0;i<4;i++) if (row.parentElement) row = row.parentElement;
  const desc = [...row.querySelectorAll('*')].filter(e=>e.offsetParent!==null).slice(0,40).map(e=>{
    const r=e.getBoundingClientRect();
    const own = [...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join('');
    return { tag:e.tagName.toLowerCase(), cls:(e.className||'').toString().slice(0,60),
             own: own.slice(0,30), ph: e.getAttribute&&e.getAttribute('placeholder')||'',
             role: (e.getAttribute&&e.getAttribute('role'))||'',
             x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width), h:Math.round(r.height) };
  });
  return { rowText: (row.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,300), desc };
})()`);
console.log(JSON.stringify(out, null, 1).slice(0, 4000));
c.close();
