// 目标：拿到完整类目树（cid + 三级路径）。
// 路线 A：商品管理列表页的「商品类目」筛选器 —— 纯读，无副作用。
// 路线 B（兜底）：页面 store 里的类目缓存。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";

const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 12000 });
await p.hookXhr();

const s1 = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const t = [...document.querySelectorAll('*')].filter(vis)
    .map(e => (e.innerText||'').trim())
    .filter(x => x && x.length < 14 && /类目|分类/.test(x));
  return { url: location.href, len: (document.body?.innerText||'').length, catTexts: [...new Set(t)].slice(0,40) };
})()`);
console.log("LIST:", JSON.stringify(s1));

// 点开类目筛选
for (const cand of ['text="商品类目"', 'text="类目"', 'text="全部类目"', 'text="选择类目"']) {
  const r = await p.click(cand).catch(() => null);
  if (r && r.ok) { console.log("opened by", cand, JSON.stringify(r)); break; }
}
await sleep(3500);

const s2 = await p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const pop = [...document.querySelectorAll('[class*=popover],[class*=dropdown],[class*=Popover],[class*=Dropdown],[class*=select],[class*=Select],[role=menu],[role=listbox]')]
    .filter(vis).map(e => ({ cls:(e.className||'').slice(0,60), txt:(e.innerText||'').replace(/\\n+/g,'/').slice(0,600) }))
    .filter(x => x.txt);
  return { pops: pop.slice(0,12) };
})()`);
console.log("POPUPS:", JSON.stringify(s2, null, 1).slice(0, 3000));

const cap = await p.drainXhr();
const hits = cap.filter(c => /categor|category|cat\b|classify/i.test(c.url || ""));
console.log("CAT-APIS:", hits.length);
for (const h of hits) console.log(" ", h.status, h.url.slice(0, 160), "|", (h.body||"").slice(0, 160));

await p.shot(OUT + "/list-cat.png", { fullPage: true });
await saveJson(OUT + "/list-cat.json", { s1, s2, catApis: hits.map(h => ({ url: h.url, status: h.status, body: (h.body||"").slice(0, 200000) })) });
console.log("saved");
p.close();
process.exit(0);
