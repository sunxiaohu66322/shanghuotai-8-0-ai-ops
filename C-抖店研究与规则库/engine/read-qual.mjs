// 抖店「资质中心 / 类目资质」读取：抓真实接口响应，落盘
// 不改任何数据，只读。
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const DD = "https://fxg.jinritemai.com";

const path = process.argv[2] || "/ffa/grs/qualification/list?source=product&type=3";
const tag = process.argv[3] || "qual";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
let t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
if (!t) {
  const r = await fetch(`http://127.0.0.1:9224/json/new?${encodeURIComponent(DD + "/ffa/g/list")}`, { method: "PUT" });
  t = await r.json();
  await sleep(3000);
}
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable"]) await c.send(m).catch(() => {});

await c.send("Page.navigate", { url: DD + path });
await sleep(7000);

const reqs = c.netRequests(/qualification|categor|categ|grs|template/i);
console.log("命中请求:", reqs.length);
const saved = [];
for (const r of reqs) {
  const b = await c.bodyOf(r.reqId);
  if (!b || b.length < 120) continue;
  const name = (r.url.split("?")[0].split("/").pop() || "x").replace(/[^\w.-]/g, "_");
  const f = `${OUT}/${tag}-${name}-${saved.length}.json`;
  let parsed = null; try { parsed = JSON.parse(b); } catch {}
  if (!parsed) continue;
  await saveJson(f, { url: r.url, method: r.method, postData: r.postData, body: parsed });
  saved.push({ f, url: r.url.slice(0, 110), len: b.length });
}
console.log(JSON.stringify(saved, null, 1));

const txt = await c.eval(`({u: location.href, t: document.title, body: (document.body?.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,2500)})`);
console.log("URL:", txt.u.slice(0, 110));
console.log("TITLE:", txt.t);
console.log("BODY:\n" + txt.body);
await c.shot(`${OUT}/${tag}-page.png`).catch(() => {});
c.close();
