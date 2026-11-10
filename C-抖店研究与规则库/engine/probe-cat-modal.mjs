// 打开商品编辑页 → 点类目旁「修改」→ 抓类目弹窗真实接口与结构。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const PID = process.argv[2] || "3783077346274312845";
const CID = process.argv[3] || "1000004588";

const p = await CDP.newPage("about:blank");
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${PID}&cid=${CID}&entrance=edit`, { settle: 14000 });
console.log("EDIT URL ok, textLen=", (await p.text()).length);

await p.hookXhr();

// 类目块旁边的「修改」
const clicks = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const btns = [...document.querySelectorAll('a,button,span,div')]
    .filter(vis).filter(e => (e.textContent||'').trim() === '修改' && e.children.length === 0);
  const out = btns.map((e, i) => {
    const box = e.getBoundingClientRect();
    return { i, top: Math.round(box.top), left: Math.round(box.left), cls: (e.className||'').slice(0,40),
             ctx: (e.parentElement?.parentElement?.innerText||'').replace(/\\n+/g,'/').slice(0,90) };
  });
  return out;
})()`);
console.log("修改 buttons:", JSON.stringify(clicks));

// 点最靠上那个（类目在基础信息第一项）
const r = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const btns = [...document.querySelectorAll('a,button,span,div')]
    .filter(vis).filter(e => (e.textContent||'').trim() === '修改' && e.children.length === 0)
    .sort((a,b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  if (!btns.length) return { ok:false };
  const el = btns[0];
  const bx = el.getBoundingClientRect();
  const o = { bubbles:true, cancelable:true, clientX: bx.left+bx.width/2, clientY: bx.top+bx.height/2, view: window };
  for (const ev of ['pointerdown','mousedown','pointerup','mouseup','click']) el.dispatchEvent(new MouseEvent(ev, o));
  return { ok:true, ctx: (el.parentElement?.parentElement?.innerText||'').replace(/\\n+/g,'/').slice(0,90) };
})()`);
console.log("clicked 修改:", JSON.stringify(r));
await sleep(5000);

const cap = await p.drainXhr();
console.log("REQ:", cap.length);
for (const c of cap) {
  const u = c.url || "";
  if (/mon\.|mcs\.|feelgood|byteimg|zijieapi/.test(u)) continue;
  console.log(`${c.status} ${u.slice(0, 170)} [${(c.body||"").length}]`);
}

const modal = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const dlg = [...document.querySelectorAll('[class*=modal],[class*=Modal],[class*=dialog],[class*=Dialog],[role=dialog]')].filter(vis);
  return dlg.slice(-3).map(d => ({
    cls: (d.className||'').slice(0,60),
    txt: (d.innerText||'').replace(/\\n+/g,'/').slice(0, 900),
    lvl1: [...d.querySelectorAll('li')].filter(vis).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0, 60)
  }));
})()`);
console.log("MODAL:", JSON.stringify(modal, null, 1).slice(0, 3500));

await p.shot(OUT + "/cat-modal-live.png");
await saveJson(OUT + "/cat-modal-live.json", { clicks, clicked: r, req: cap.map(c=>({url:c.url,status:c.status,body:(c.body||"").slice(0,300000)})), modal });
console.log("saved");
p.close();
process.exit(0);
