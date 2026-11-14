// 旧版建品页：dump 结构 + 试传 1 张主图（看是否绕开素材库容量限制）
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

await c.send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/create?entrance=create" });
await sleep(8000);

const ui = await c.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  return {
    url: location.href.slice(0,110),
    files: [...document.querySelectorAll('input[type=file]')].map((e,i)=>({i, accept:e.accept, multiple:e.multiple, vis:vis(e)})),
    inputs: [...document.querySelectorAll('input,textarea')].filter(vis).map(e=>({tag:e.tagName,type:e.type,ph:e.placeholder,cls:(e.className||'').toString().slice(0,30)})).slice(0,15),
    btns: [...new Set([...document.querySelectorAll('button,[class*=btn],[role=button]')].filter(vis).map(e=>(e.innerText||'').trim()))].filter(x=>x&&x.length<14).slice(0,20),
    txt: (document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0,700)
  };
})()`);
console.log(JSON.stringify(ui, null, 1).slice(0, 2500));

// 试传第一张主图
const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: 'input[type=file]' });
console.log("file inputs:", q.nodeIds.length);
if (q.nodeIds.length && plan.main[0]) {
  await c.send("DOM.setFileInputFiles", { files: [plan.main[0]], nodeId: q.nodeIds[0] });
  console.log("投递:", plan.main[0].split("\\").pop());
  await sleep(6000);
  const aft = await c.eval(`(() => {
    const txt = (document.body.innerText||'');
    return { full: /素材库容量已满|上传失败/.test(txt), n: document.querySelectorAll('img').length,
             tail: txt.replace(/\\n{2,}/g,'\\n').slice(-320) };
  })()`);
  console.log("结果:", JSON.stringify(aft));
  await c.shot(OUT + "/old-01-upload.png").catch(() => {});
}
c.close();
