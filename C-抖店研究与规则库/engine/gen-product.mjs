// 素材库已腾出后的正式一步：补齐 4 图 → 点「下一步，生成商品」→ 读系统给的类目/表单
// 绝不点发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const rest = [plan.main[1], plan.main[2], plan.detail[0]].filter(Boolean);
console.log("补投:", rest.map(p => p.split("\\").pop()).join(", "));

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const cands = tabs.filter(x => x.type === "page" && (x.url || "").includes("/ffa/g/create"));
// 选已上传 1 的那个（新开的）
let c = null;
for (const t of cands) {
  const cc = await CDP.connect(t.webSocketDebuggerUrl);
  for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await cc.send(m).catch(() => {});
  const n = await cc.eval(`(document.body.innerText.match(/已上传 (\\d+)/)||[0,0])[1]`).catch(() => 0);
  console.log("候选 tab", String(t.id).slice(0, 8), "已上传=", n);
  if (Number(n) === 1 && !c) { c = cc; break; }
  cc.close();
}
if (!c) { console.log("没找到含 1 张图的 create 页"); process.exit(1); }

const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
await c.send("DOM.setFileInputFiles", { files: rest, nodeId: q.nodeIds[0] });
console.log("已补投 3 张，等上传 ...");

for (let i = 1; i <= 40; i++) {
  await sleep(1500);
  const st = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return { n:(t.match(/已上传 (\\d+)/)||[0,0])[1], full:/容量已满/.test(t), fail:/上传失败/.test(t), busy:/上传中|正在上传|解析中/.test(t) };
  })()`);
  if (i % 4 === 0 || !st.busy) console.log(`  ${(i * 1.5).toFixed(1)}s 已上传=${st.n} full=${st.full} fail=${st.fail}`);
  if (Number(st.n) >= 4 && !st.busy) break;
  if (st.full || st.fail) break;
}

// 点「下一步，生成商品」
const r = await c.eval(`(() => {
  const b=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null && /下一步/.test((e.innerText||'').trim()));
  if(!b.length) return {ok:false};
  b[0].click();
  return {ok:true, n:b.length, txt:(b[0].innerText||'').trim()};
})()`);
console.log("点下一步:", JSON.stringify(r));

// 等生成（AI 识别最长给 150s）
let lastTxt = "";
for (let i = 1; i <= 60; i++) {
  await sleep(2500);
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return { path: location.pathname + location.search.slice(0,60), gen:/生成中|识别中|正在生成|请稍候/.test(t),
             len:t.length, hasCat:/类目|cid/.test(t), head:t.replace(/\\n{2,}/g,'\\n').slice(0,200) };
  })()`);
  if (i % 4 === 0) console.log(`  ${(i * 2.5).toFixed(0)}s ${s.path} len=${s.len} gen=${s.gen} cat=${s.hasCat}`);
  if (s.head !== lastTxt && s.gen) lastTxt = s.head;
  if (!s.gen && i > 4) { console.log("生成结束:", JSON.stringify(s).slice(0, 600)); break; }
}
await c.shot(OUT + "/gen-01-result.png").catch(() => {});
const page = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const ins=[...document.querySelectorAll('input,textarea')].slice(0,60).map(e=>({
    tag:e.tagName.toLowerCase(), ph:(e.placeholder||'').slice(0,40),
    v:(e.value||'').slice(0,60), vis:e.offsetParent!==null }));
  return { url: location.href, len: t.length, txt: t.slice(0, 4000), inputs: ins };
})()`);
await saveJson(OUT + "/gen-01-page.json", page);
console.log("\nURL:", page.url, "LEN:", page.len);
console.log(page.txt);
c.close();
