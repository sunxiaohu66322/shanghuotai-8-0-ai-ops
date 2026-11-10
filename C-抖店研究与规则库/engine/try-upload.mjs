// 素材库清理后重试：向 /ffa/g/create 投图，观察是否仍有「容量已满」
// 只上传，不点发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const imgs = [...plan.main, ...plan.detail].filter(Boolean);
const only = process.argv[2] === "--one" ? imgs.slice(0, 1) : imgs;
console.log("投递图片:", only.length);
only.forEach(p => console.log("   ", p.split("\\").pop(), (fs.stat(p).catch(() => null)) ? "" : "?"));

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const cands = tabs.filter(x => x.type === "page" && (x.url || "").includes("/ffa/g/create"));
console.log("create 页:", cands.length);
if (!cands.length) { console.log("没有 create 页"); process.exit(1); }
const c = await CDP.connect(cands[0].webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

console.log("当前 URL:", await c.eval("location.href"));
const before = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { len:t.length, tail:t.slice(-300), inputs:document.querySelectorAll('input[type=file]').length };
})()`);
console.log("上传前:", JSON.stringify(before));

const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
console.log("file inputs:", q.nodeIds.length);
if (!q.nodeIds.length) { console.log("没找到上传控件"); process.exit(1); }

await c.send("DOM.setFileInputFiles", { files: only, nodeId: q.nodeIds[0] });
console.log("已投递，等上传 ...");

let last = "";
for (let i = 1; i <= 30; i++) {
  await sleep(1500);
  const st = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return {
      full: /容量已满/.test(t), fail: /上传失败/.test(t), busy: /上传中|正在上传/.test(t),
      done: (t.match(/image_[0-9a-f]+\\.jpg/g)||[]).length,
      tail: t.replace(/\\n{2,}/g,'\\n').slice(-320)
    };
  })()`);
  const key = `done=${st.done} full=${st.full} fail=${st.fail}`;
  if (key !== last) { console.log(`  ${(i * 1.5).toFixed(1)}s ${key}`); last = key; }
  if (!st.busy && (st.full || st.fail || st.done >= only.length) && i > 2) {
    console.log("终止态 tail:\n" + st.tail);
    break;
  }
}
await c.shot(OUT + "/retry-upload.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { url: location.href.slice(0,120), len: t.length, txt: t.slice(0, 2500) };
})()`);
await saveJson(OUT + "/retry-upload.json", page);
console.log("\n最终:\n" + page.txt);
c.close();
