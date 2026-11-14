// 看素材库容量与条数（决定是否必须清理）
import { CDP, sleep } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable"]) await c.send(m).catch(() => {});

for (const u of ["/ffa/g/material/list", "/ffa/g/material", "/ffa/grs/material"]) {
  await c.send("Page.navigate", { url: "https://fxg.jinritemai.com" + u });
  await sleep(6000);
  const r = await c.eval(`({u: location.href.slice(0,90), t: document.title, txt:(document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,600)})`);
  console.log("=== " + u + " -> " + r.u + " | " + r.t);
  if (/素材/.test(r.txt) && !/404|不存在/.test(r.txt)) { console.log(r.txt); break; }
}
await c.shot(OUT + "/material.png").catch(() => {});
c.close();
