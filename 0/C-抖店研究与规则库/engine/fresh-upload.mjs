// 全新标签页重试：避免旧页面「已上传 4」的重复选择缓存
// 另外顺手读一次素材库用量接口（best-effort，失败不阻塞）
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const imgs = [...plan.main, ...plan.detail].filter(Boolean);

// 1) 新开标签页
const r = await fetch("http://127.0.0.1:9224/json/new?url=" + encodeURIComponent("https://fxg.jinritemai.com/ffa/g/create"), { method: "PUT" });
const tgt = await r.json();
console.log("新标签:", String(tgt.id).slice(0, 8), tgt.url);
await sleep(6000);

const c = await CDP.connect(tgt.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

// /json/new 的 url 参数可能被忽略 → 显式导航
await c.send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/create" }).catch(() => {});

// 等表单出现
let ready = 0;
for (let i = 1; i <= 30; i++) {
  await sleep(1500);
  const ok = await c.eval(`document.querySelectorAll('input[type=file]').length`).catch(() => 0);
  if (ok > 0) { console.log(`${(i * 1.5).toFixed(1)}s 表单就绪 file inputs=${ok}`); ready = ok; break; }
  if (i % 6 === 0) console.log(`  ${(i * 1.5).toFixed(1)}s 等待… url=${(await c.eval("location.pathname").catch(()=>"?"))}`);
}
if (!ready) { console.log("表单未就绪，URL=", await c.eval("location.href").catch(()=>"?")); }
console.log("URL:", await c.eval("location.href"));

// 2) 读素材库用量（best-effort）
const usage = await c.eval(`(function(){
  return new Promise(function(res){
    var x = new XMLHttpRequest();
    x.open('POST','https://fxg.jinritemai.com/ffa/grs/material/capacity',true);
    x.setRequestHeader('Content-Type','application/json');
    x.timeout = 8000;
    x.onload = function(){ res({s:x.status, b:(x.responseText||'').slice(0,400)}); };
    x.onerror = function(){ res({s:-1}); };
    x.ontimeout = function(){ res({s:-2}); };
    x.send('{}');
  });
})()`).catch(e => ({ err: String(e) }));
console.log("素材库用量:", JSON.stringify(usage));

// 3) 投 1 张
const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
console.log("file inputs:", q.nodeIds.length);
await c.send("DOM.setFileInputFiles", { files: imgs.slice(0, 1), nodeId: q.nodeIds[0] });
console.log("已投递 1 张，等 ...");

let last = "";
for (let i = 1; i <= 30; i++) {
  await sleep(1500);
  const st = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return { full:/容量已满/.test(t), fail:/上传失败/.test(t), busy:/上传中|正在上传|解析中/.test(t),
             done:(t.match(/image_[0-9a-f]+\\.jpg/g)||[]).length,
             tail:t.replace(/\\n{2,}/g,'\\n').slice(-300) };
  })()`);
  const key = `done=${st.done} full=${st.full} fail=${st.fail} busy=${st.busy}`;
  if (key !== last) { console.log(`  ${(i * 1.5).toFixed(1)}s ${key}`); last = key; }
  if (!st.busy && i > 3) { console.log("终止 tail:\n" + st.tail); break; }
}
await c.shot(OUT + "/retry-fresh.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { url: location.href.slice(0,120), len: t.length, txt: t.slice(0, 2000) };
})()`);
await saveJson(OUT + "/retry-fresh.json", page);
console.log("\n最终:\n" + page.txt);
console.log("\nTAB_ID=" + tgt.id);
c.close();
