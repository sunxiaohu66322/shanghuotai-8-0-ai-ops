// 抖店 AI 建品：上传图片 → 「下一步，生成商品」 → 读系统给的类目与表单
// 绝不点发布；只走到生成/填表阶段。
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const imgs = [...plan.main, ...plan.detail].filter(Boolean);
console.log("待上传图片:", imgs.length);
for (const p of imgs) console.log("   ", p.split("\\").pop());

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

// 确保在 create 页
const cur = await c.eval("location.href");
if (!cur.includes("/ffa/g/create")) {
  await c.send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/create" });
  await sleep(7000);
}
console.log("页面:", (await c.eval("location.href")).slice(0, 90));

// 找 file input 的 nodeId
const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: 'input[type=file]' });
console.log("file inputs:", q.nodeIds.length);
if (!q.nodeIds.length) { console.log("没找到上传控件"); process.exit(1); }

// 多图上传：先试第一个（multiple=true）
await c.send("DOM.setFileInputFiles", { files: imgs, nodeId: q.nodeIds[0] });
console.log("已投递文件，等上传 ...");

for (let i = 1; i <= 24; i++) {
  await sleep(1500);
  const st = await c.eval(`(() => {
    const txt = (document.body.innerText||'');
    return { pending: /上传中|正在上传/.test(txt), imgs: document.querySelectorAll('img').length,
             next: [...document.querySelectorAll('button,[class*=btn]')].filter(e=>e.offsetParent!==null && /下一步/.test(e.innerText||'')).length,
             tail: txt.replace(/\\n{2,}/g,'\\n').slice(-260) };
  })()`);
  if (i % 4 === 0) console.log(`  ${i*1.5}s imgs=${st.imgs} 下一步btn=${st.next} pending=${st.pending}`);
  if (!st.pending && i > 3) break;
}
await c.shot(OUT + "/create-02-uploaded.png").catch(() => {});

// 点「下一步，生成商品」
const r = await c.eval(`(() => {
  const b = [...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e => e.offsetParent !== null && /下一步/.test((e.innerText||'').trim()));
  if (!b.length) return {ok:false, n:0};
  b[0].click();
  return {ok:true, n:b.length, txt:(b[0].innerText||'').trim()};
})()`);
console.log("点下一步:", JSON.stringify(r));

// 等生成
for (let i = 1; i <= 30; i++) {
  await sleep(2000);
  const s = await c.eval(`(() => {
    const txt = (document.body.innerText||'');
    return { gen: /生成中|识别中|正在生成/.test(txt),
             hasCat: /类目/.test(txt), len: txt.length,
             head: txt.replace(/\\n{2,}/g,'\\n').slice(0, 300) };
  })()`);
  if (i % 5 === 0) console.log(`  ${i*2}s len=${s.len} gen=${s.gen} cat=${s.hasCat}`);
  if (!s.gen && i > 2) { console.log("生成结束态:", JSON.stringify(s).slice(0, 400)); break; }
}
await c.shot(OUT + "/create-03-generated.png").catch(() => {});

const page = await c.eval(`(() => {
  const txt = (document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  return { url: location.href.slice(0,110), title: document.title, len: txt.length, txt: txt.slice(0, 2200) };
})()`);
await saveJson(OUT + "/create-03-page.json", page);
console.log("\nURL:", page.url, "| LEN:", page.len);
console.log(page.txt);
c.close();
