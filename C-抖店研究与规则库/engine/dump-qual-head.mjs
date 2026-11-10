import { CDP } from "./cdp.mjs";
import fs from "node:fs/promises";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
await c.send("Runtime.enable").catch(() => {});
const r = await c.eval(`(() => {
  const txt = document.body.innerText || '';
  // 找表格
  const rows = [...document.querySelectorAll('tr')].slice(0,6).map(tr =>
     [...tr.querySelectorAll('th,td')].map(td => (td.innerText||'').trim().slice(0,20)));
  const ths = [...document.querySelectorAll('th')].map(e=>(e.innerText||'').trim()).filter(Boolean);
  const tabsEl = [...document.querySelectorAll('[class*=tab],[class*=Tab],[role=tab]')].filter(e=>e.offsetParent!==null).map(e=>(e.innerText||'').trim().slice(0,16)).filter(Boolean).slice(0,15);
  return { len: txt.length, head: txt.slice(0, 1500), ths, rowSample: rows, tabNames: [...new Set(tabsEl)] };
})()`);
await fs.writeFile(OUT + "/qual-body.txt", r.head, "utf8");
console.log("LEN:", r.len);
console.log("TH:", JSON.stringify(r.ths));
console.log("TABS:", JSON.stringify(r.tabNames));
console.log("ROWS:", JSON.stringify(r.rowSample).slice(0,600));
console.log("HEAD:\n" + r.head);
c.close();
