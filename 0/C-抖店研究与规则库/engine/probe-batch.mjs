// 探两件事：
//  ① 抖店后台有没有「批量导入/批量创建」Excel 入口（商品管理页 + 创建页）
//  ② 类目接口的真实契约（抓 create 页所有 /product/ 请求）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const R = {};

const c = await CDP.page({ urlIncludes: "fxg.jinritemai.com/ffa/g/list" });
console.log("== 连上:", c.target.url);

// ---------- 1. 商品管理页：所有可见交互元素文本 ----------
await c.waitReady(4000);
const btn = await c.eval(`(() => {
  const out = [];
  document.querySelectorAll('button,a,[role=button],span,div').forEach(e => {
    const t = (e.innerText||'').trim();
    if (!t || t.length > 14) return;
    if (e.offsetParent === null) return;
    if (/批量|导入|导出|Excel|模板|复制|新建|发布/.test(t)) out.push({ tag:e.tagName, t, cls:(e.className||'').toString().slice(0,50) });
  });
  // 去重
  const m = new Map();
  out.forEach(o => m.set(o.t + '|' + o.tag, o));
  return [...m.values()];
})()`);
R.listButtons = btn;
console.log("== 商品管理页『批量/导入』类元素:", JSON.stringify(btn, null, 1));

// 顶部页签/二级导航
const tabs = await c.eval(`([...document.querySelectorAll('[role=tab],.auxo-tabs-tab,.auxo-menu-item')].map(e=>({t:(e.innerText||'').trim(),c:(e.className||'').toString().slice(0,40)})).filter(x=>x.t&&x.t.length<16).slice(0,40))`);
R.listTabs = tabs;

// ---------- 2. 抓这个页的 /product/ 请求 ----------
await c.send("Network.enable");
await c.send("Page.reload");
await sleep(9000);
const reqs = c.netRequests(/jinritemai|product|category/i);
R.listReqs = [...new Set(reqs.map(r => r.method + " " + r.url.split("?")[0]))];
console.log("== 商品管理页请求数:", reqs.length);
console.log(R.listReqs.slice(0, 40).join("\n"));

// ---------- 3. 创建页：抓类目 & 批量相关 ----------
const c2 = await CDP.page({ urlIncludes: "ffa/g/create" });
await c2.waitReady(6000);
const btn2 = await c2.eval(`(() => {
  const out = [];
  document.querySelectorAll('button,a,[role=button],span,div').forEach(e => {
    const t = (e.innerText||'').trim();
    if (!t || t.length > 14 || e.offsetParent === null) return;
    if (/批量|导入|Excel|模板|类目|分类/.test(t)) out.push({tag:e.tagName,t});
  });
  const m = new Map(); out.forEach(o=>m.set(o.t+'|'+o.tag,o));
  return [...m.values()];
})()`);
R.createButtons = btn2;
console.log("== 创建页『批量/类目』类元素:", JSON.stringify(btn2, null, 1));

const reqs2 = c2.netRequests(/category|product\/|predict/i);
R.createReqs = [...new Set(reqs2.map(r => r.method + " " + r.url.split("?")[0]))];
console.log("== 创建页请求:", R.createReqs.length);

await saveJson(OUT + "/probe-batch.json", R);
console.log("== 已写", OUT + "/probe-batch.json");
c.close(); c2.close();
