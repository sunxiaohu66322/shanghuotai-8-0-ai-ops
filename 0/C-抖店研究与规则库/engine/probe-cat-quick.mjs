// ① 调 categoryOptionsN 拿真实类目树（家具/户外/灯具）
//  ② 点「快速发布」看它到底是什么（是否批量导入 Excel）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const R = {};

const c = await CDP.page({ urlIncludes: "fxg.jinritemai.com/ffa/g/list" });
await c.waitReady(3000);
console.log("== 连上", c.target.url);

// ---- categoryOptionsN ----
const cat = await c.eval(`(async () => {
  const tries = ['/product/tproduct/categoryOptionsN', '/product/tproduct/categoryOptionsN?level=1'];
  const out = [];
  for (const u of tries) {
    try {
      const r = await fetch(u, { credentials: 'include', headers: { 'x-requested-with':'XMLHttpRequest' } });
      const t = await r.text();
      out.push({ u, status: r.status, len: t.length, body: t.slice(0, 300000) });
      if (r.status === 200 && t.length > 200) break;
    } catch (e) { out.push({ u, err: String(e) }); }
  }
  return out;
})()`);
R.categoryOptionsN = cat.map(x => ({ u: x.u, status: x.status, len: x.len }));
const body = (cat.find(x => x.status === 200 && x.len > 200) || {}).body || "";
await saveJson(OUT + "/cat-options-raw.json", { body });
console.log("== categoryOptionsN:", JSON.stringify(R.categoryOptionsN));
if (body) {
  try {
    const j = JSON.parse(body);
    const keys = Object.keys(j);
    console.log("== 顶层键:", keys.join(","));
    const data = j.data || j;
    console.log("== data 键:", Object.keys(data).slice(0, 20).join(","));
    // 找出数组型的层级
    for (const k of Object.keys(data)) {
      const v = data[k];
      if (Array.isArray(v)) console.log(`   ${k}: 数组 ${v.length} 项, 首项键=${v[0] ? Object.keys(v[0]).join(",") : "-"}`);
      else if (v && typeof v === 'object') console.log(`   ${k}: obj 键=${Object.keys(v).slice(0, 10).join(",")}`);
    }
  } catch (e) { console.log("== 解析失败", String(e)); }
}

// ---- 快速发布 ----
const quick = await c.eval(`(() => {
  const els = [...document.querySelectorAll('span,div,a,button')]
    .filter(e => (e.innerText||'').trim() === '快速发布' && e.offsetParent !== null);
  return els.map(e => ({ tag: e.tagName, cls: (e.className||'').toString().slice(0,60), parentCls: (e.parentElement?.className||'').toString().slice(0,60) }));
})()`);
R.quickEls = quick;
console.log("== 快速发布 元素:", JSON.stringify(quick));

// 点它，看是不是弹层
if (quick.length) {
  await c.hookXhr();
  await c.eval(`(() => {
    const els=[...document.querySelectorAll('span,div,a,button')].filter(e=>(e.innerText||'').trim()==='快速发布'&&e.offsetParent!==null);
    (els[els.length-1]||els[0]).click(); return 1;
  })()`);
  await sleep(2500);
  const after = await c.eval(`(() => {
    const m = [...document.querySelectorAll('[class*=modal],[class*=drawer],[class*=popover],[class*=dropdown],[class*=Menu]')]
      .filter(e => e.offsetParent !== null).map(e => (e.innerText||'').trim().slice(0,300)).filter(t=>t.length>2);
    return { visible: [...new Set(m)].slice(0,6), bodyTail: (document.body.innerText||'').slice(0,1200) };
  })()`);
  R.quickAfterClick = after;
  console.log("== 点快速发布后可见弹层:", JSON.stringify(after.visible, null, 1));
  const xhr = await c.drainXhr();
  R.quickXhr = xhr.map(x => x.url || x.k);
  console.log("== 点击后请求:", R.quickXhr.join("\n"));
}

await saveJson(OUT + "/probe-cat-quick.json", R);
console.log("== 写出 probe-cat-quick.json");
c.close();
