// 精确打开列表页的「类目名称」选择器，抓类目树数据 + 对应接口。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 12000 });
await p.hookXhr();

// 找占位符为「请选择类目」的 select 容器，并对其派发真实鼠标事件
const opened = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const ph = [...document.querySelectorAll('*')].filter(vis)
    .filter(e => (e.textContent||'').trim() === '请选择类目' && e.children.length === 0);
  if (!ph.length) return { ok:false, reason:'no-placeholder' };
  const sel = ph[ph.length-1].closest('[class*=select]') || ph[ph.length-1].parentElement;
  const target = sel.querySelector('[class*=selector]') || sel;
  const r = target.getBoundingClientRect();
  const opt = { bubbles:true, cancelable:true, clientX: r.left + r.width/2, clientY: r.top + r.height/2, view: window };
  for (const ev of ['pointerdown','mousedown','pointerup','mouseup','click']) {
    target.dispatchEvent(new MouseEvent(ev, opt));
  }
  return { ok:true, cls: (sel.className||'').slice(0,60), rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)] };
})()`);
console.log("open:", JSON.stringify(opened));
await sleep(3000);

// 读弹出的树
const tree = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const cands = [...document.querySelectorAll('[class*=dropdown],[class*=popup],[class*=Popover],[class*=cascader],[class*=Cascader],[class*=menu],[class*=panel]')]
    .filter(vis)
    .map(e => ({ cls:(e.className||'').slice(0,70), n: e.querySelectorAll('li,[class*=item],[class*=option]').length,
                 txt:(e.innerText||'').replace(/\\n+/g,'/').slice(0,800) }))
    .filter(x => x.txt && x.txt.length > 20)
    .sort((a,b) => b.n - a.n);
  return cands.slice(0, 6);
})()`);
console.log("TREE PANELS:", JSON.stringify(tree, null, 1).slice(0, 4000));

const cap = await p.drainXhr();
const hits = cap.filter(c => /categor|category|classify|/i.test(c.url||'') && /categor|category/i.test(c.url||''));
console.log("CAT-APIS:", hits.length);
for (const h of hits) console.log(" ", h.status, (h.url||'').slice(0,170), "|", (h.body||'').slice(0,300));

await p.shot(OUT + "/cat-tree.png");
await saveJson(OUT + "/cat-tree.json", { opened, tree, hits: hits.map(h=>({url:h.url,status:h.status,body:(h.body||'').slice(0,300000)})) });
console.log("saved");
p.close();
process.exit(0);
