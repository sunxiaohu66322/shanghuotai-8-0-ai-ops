// 第二跳：点「修改」→「修改类目」→ 进入真正的类目树，抓接口与三级结构。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const PID = process.argv[2] || "3783077346274312845";
const CID = process.argv[3] || "1000004588";

const p = await CDP.newPage("about:blank");
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${PID}&cid=${CID}&entrance=edit`, { settle: 13000 });
await p.hookXhr();

const press = async (label, nth = 0) => {
  const js = `(() => {
    const vis = e => e.offsetParent !== null;
    const els = [...document.querySelectorAll('a,button,span,div,li')].filter(vis)
      .filter(e => (e.textContent||'').trim() === ${JSON.stringify(label)} && e.children.length === 0);
    const el = els[${nth}];
    if (!el) return { ok:false, n: els.length };
    const b = el.getBoundingClientRect();
    const o = { bubbles:true, cancelable:true, clientX: b.left+b.width/2, clientY: b.top+b.height/2, view: window };
    for (const ev of ['pointerdown','mousedown','pointerup','mouseup','click']) el.dispatchEvent(new MouseEvent(ev, o));
    return { ok:true, n: els.length };
  })()`;
  const r = await p.eval(js);
  console.log(`press ${label} ->`, JSON.stringify(r));
  return r;
};

await press("修改");
await sleep(2500);
await press("修改类目");
await sleep(6000);

const cap = await p.drainXhr();
console.log("REQ:", cap.length);
for (const c of cap) {
  const u = c.url || "";
  if (/mon\.|mcs\.|feelgood|zijieapi|report\/submit/.test(u)) continue;
  console.log(`${c.status} ${u.slice(0, 190)} [${(c.body||"").length}]`);
}

const tree = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const dlg = [...document.querySelectorAll('[class*=modal],[class*=Modal],[class*=dialog],[class*=Dialog],[role=dialog]')].filter(vis).pop();
  if (!dlg) return { err:'no-dialog' };
  const cols = [...dlg.querySelectorAll('[class*=col],[class*=Col],[class*=list],[class*=List],[class*=panel],[ul]')].filter(vis);
  return {
    title: (dlg.querySelector('[class*=title],[class*=Title]')?.innerText || '').slice(0, 80),
    txtHead: (dlg.innerText||'').replace(/\\n+/g,'/').slice(0, 700),
    cols: cols.map(c => ({ cls:(c.className||'').slice(0,44), n: c.querySelectorAll('li,[class*=item]').length,
                           items: [...c.querySelectorAll('li')].filter(vis).map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,40) }))
            .filter(c => c.n > 0).slice(0, 6)
  };
})()`);
console.log("TREE:", JSON.stringify(tree, null, 1).slice(0, 4000));

await p.shot(OUT + "/cat-tree-live.png");
await saveJson(OUT + "/cat-tree-live.json", { req: cap.map(c=>({url:c.url,status:c.status,body:(c.body||"").slice(0,400000)})), tree });
console.log("saved");
p.close();
process.exit(0);
