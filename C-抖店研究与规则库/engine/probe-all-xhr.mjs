// 抓列表页「全部」请求清单，定位类目数据到底由哪个接口提供（不限关键词）。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 10000 });
await p.hookXhr();
await sleep(1000);

// 触发一次刷新查询，确保业务请求都出来
const btn = await p.click('text="查询"');
console.log("query btn:", JSON.stringify(btn));
await sleep(6000);

const cap = await p.drainXhr();
console.log("TOTAL:", cap.length);
const seen = new Map();
for (const c of cap) {
  const u = (c.url || "").split("?")[0];
  if (!u.includes("jinritemai")) continue;
  seen.set(u, c);
}
const list = [...seen.entries()].map(([u, c]) => ({ url: u, status: c.status, bytes: (c.body || "").length, head: (c.body || "").slice(0, 160) }));
console.log("UNIQ:", list.length);
for (const x of list.sort((a, b) => a.url.localeCompare(b.url))) console.log(`${x.status} [${x.bytes}] ${x.url}`);

// 同时在页面里搜内联的类目数据（webpack 模块可能把它挂在 window 上）
const inline = await p.eval(`(() => {
  const out = [];
  const keys = Object.keys(window).filter(k => /cat|Cat|CATE|__|store|STORE|state/.test(k));
  for (const k of keys.slice(0, 60)) {
    try {
      const v = window[k];
      if (!v || typeof v !== 'object') continue;
      const s = JSON.stringify(v).slice(0, 120);
      out.push({ k, type: typeof v, size: JSON.stringify(v).length, head: s });
    } catch (e) {}
  }
  return out.sort((a,b)=>b.size-a.size).slice(0, 20);
})()`);
console.log("WIN OBJECTS:", JSON.stringify(inline, null, 1).slice(0, 2500));

await saveJson(OUT + "/all-xhr.json", { list, inline, raw: cap });
console.log("saved all-xhr");
p.close();
process.exit(0);
