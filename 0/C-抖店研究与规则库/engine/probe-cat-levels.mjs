// 逐级点开类目，dump 每级的 DOM 结构，确认 cid 藏在哪（data-* / key / 点击后 URL）。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";

const open = async (p) => p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const ph = [...document.querySelectorAll('*')].filter(vis)
    .filter(e => (e.textContent||'').trim() === '请选择类目' && !e.children.length);
  if (!ph.length) return {ok:false};
  const sel = ph[ph.length-1].closest('[class*=select]');
  const t = sel.querySelector('[class*=selector]') || sel;
  const r = t.getBoundingClientRect();
  const o = {bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window};
  for (const ev of ['pointerdown','mousedown','pointerup','mouseup','click']) t.dispatchEvent(new MouseEvent(ev,o));
  return {ok:true};
})()`);

/** 点某一个面板里文本完全匹配的项 */
const clickIn = (p, panelSel, text) => p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const panel = [...document.querySelectorAll(${JSON.stringify(panelSel)})].filter(vis).pop();
  if (!panel) return {ok:false, reason:'no-panel'};
  const items = [...panel.querySelectorAll('li,[class*=option],[class*=item],[class*=Item]')].filter(vis);
  const hit = items.find(e => (e.innerText||'').trim() === ${JSON.stringify(text)});
  if (!hit) return {ok:false, reason:'no-item', sample: items.slice(0,15).map(e=>(e.innerText||'').trim())};
  hit.scrollIntoView({block:'center'});
  const r = hit.getBoundingClientRect();
  const o = {bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window};
  for (const ev of ['pointerdown','mousedown','pointerup','mouseup','click']) hit.dispatchEvent(new MouseEvent(ev,o));
  return {ok:true, html: hit.outerHTML.slice(0,600), attrs: [...hit.attributes].map(a=>a.name+'='+a.value).slice(0,12)};
})()`);

const dumpPanels = (p) => p.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const ps = [...document.querySelectorAll('[class*=categoryDropDown],[class*=dropdown]')].filter(vis);
  return ps.map(pa => {
    const lis = [...pa.querySelectorAll('li')].filter(vis);
    return { cls:(pa.className||'').slice(0,70), liCount: lis.length,
             items: lis.map(e=> (e.innerText||'').trim()).filter(Boolean).slice(0,80),
             firstHtml: lis[0] ? lis[0].outerHTML.slice(0,500) : '' };
  }).filter(x => x.liCount > 0);
})()`);

const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 12000 });

await open(p);
await sleep(2000);

const L1 = ["家具", "户外装备", "家装灯饰光源"];
const result = {};

for (const lv1 of L1) {
  const r1 = await clickIn(p, '[class*=categoryDropDown]', lv1);
  console.log(`L1 ${lv1} ->`, r1.ok ? `ok attrs=${JSON.stringify(r1.attrs)}` : JSON.stringify(r1));
  await sleep(2500);
  const panels = await dumpPanels(p);
  console.log(`  panels after L1: ${panels.length}`, panels.map(x=>`${x.cls.slice(0,28)}(${x.liCount})`).join(" "));
  result[lv1] = { l1Click: r1, panels };
  if (panels.length >= 2) {
    result[lv1].l2Sample = panels[panels.length-1].items;
    result[lv1].l2Html = panels[panels.length-1].firstHtml;
  }
}

await p.shot(OUT + "/cat-levels.png");
await saveJson(OUT + "/cat-levels.json", result);
console.log("saved cat-levels");
p.close();
process.exit(0);
