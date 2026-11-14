// 精确点「邮箱登录」：把候选元素全部列出来，再点最里层那个
import { CDP, sleep } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
await c.send("Runtime.enable").catch(() => {});

const cands = await c.eval(`(() => {
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    const tx = (e.textContent || '').trim();
    if (tx && tx.length <= 8 && tx.includes('邮箱')) {
      out.push({ tag: e.tagName, cls: (e.className||'').toString().slice(0,50), tx, kids: e.children.length, vis: e.offsetParent !== null });
    }
  }
  return out.slice(0, 20);
})()`);
console.log("候选:", JSON.stringify(cands, null, 1));

const r = await c.eval(`(() => {
  const hits = [...document.querySelectorAll('*')].filter(e => {
    const tx = (e.textContent || '').trim();
    return tx === '邮箱登录' && e.offsetParent !== null;
  });
  if (!hits.length) return { ok:false, n:0 };
  // 取最里层（children 最少的）
  hits.sort((a,b) => a.children.length - b.children.length);
  const el = hits[0];
  el.scrollIntoView({block:'center'});
  el.click();
  return { ok:true, n: hits.length, tag: el.tagName, cls:(el.className||'').toString().slice(0,40) };
})()`);
console.log("点击:", JSON.stringify(r));
await sleep(2000);

const form = await c.eval(`(() => {
  const ins = [...document.querySelectorAll('input')].filter(e => e.offsetParent !== null);
  return {
    inputs: ins.map(e => ({type:e.type, name:e.name, ph:e.placeholder, v:(e.value||'').length})),
    body: (document.body?.innerText||'').replace(/\\n+/g,' | ').slice(0, 300)
  };
})()`);
console.log("切换后表单:", JSON.stringify(form, null, 1));
await c.shot(OUT + "/login-email.png");
console.log("shot ok");
c.close();
