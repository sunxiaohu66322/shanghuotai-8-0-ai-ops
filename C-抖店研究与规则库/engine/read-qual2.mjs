// 找出「资质中心」真正的列表接口 + 抓页面正文（跳过左侧导航）
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const DD = "https://fxg.jinritemai.com";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable"]) await c.send(m).catch(() => {});

await c.send("Page.navigate", { url: DD + "/ffa/grs-new/qualification/list?source=product&type=3" });
await sleep(9000);

// 所有 xhr/fetch 的 URL（去重）
const all = c.netRequests();
const seen = new Map();
for (const r of all) {
  if (!/fxg\.jinritemai/.test(r.url)) continue;
  const k = r.url.split("?")[0];
  if (!seen.has(k)) seen.set(k, { n: 0, sample: r.url });
  seen.get(k).n++;
}
const list = [...seen.entries()].map(([k, v]) => ({ path: k.replace("https://fxg.jinritemai.com", ""), n: v.n }));
list.sort((a, b) => b.n - a.n);
console.log("接口清单（" + list.length + "）:");
for (const x of list) console.log(`  ${x.n}x  ${x.path}`);
await saveJson(`${OUT}/qual-endpoints.json`, list);

// 正文：取右侧内容区
const body = await c.eval(`(() => {
  const txt = document.body.innerText || '';
  const idx = txt.indexOf('资质中心');
  return { len: txt.length, tail: txt.slice(Math.max(0, txt.length - 3000)) };
})()`);
console.log("BODY LEN:", body.len);
console.log("BODY TAIL:\n" + body.tail);
await c.shot(`${OUT}/qual-list.png`).catch(() => {});
c.close();
