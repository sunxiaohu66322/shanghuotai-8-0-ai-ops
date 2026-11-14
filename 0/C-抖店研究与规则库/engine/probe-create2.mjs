// 探新建商品页：有哪些输入、上传控件、按钮；抓接口
import { CDP, sleep } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const DD = "https://fxg.jinritemai.com";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

await c.send("Page.navigate", { url: DD + "/ffa/g/create" });
await sleep(8000);

const ui = await c.eval(`(() => {
  const vis = e => e.offsetParent !== null;
  const inputs = [...document.querySelectorAll('input,textarea')].filter(vis).map(e => ({
    tag: e.tagName, type: e.type, name: e.name, ph: e.placeholder,
    cls: (e.className||'').toString().slice(0,40), maxlen: e.maxLength
  }));
  const files = [...document.querySelectorAll('input[type=file]')].map(e => ({
    accept: e.accept, multiple: e.multiple, cls: (e.className||'').toString().slice(0,40), hidden: !vis(e)
  }));
  const btns = [...document.querySelectorAll('button,[class*=btn],[role=button]')].filter(vis)
    .map(e => (e.innerText||'').trim()).filter(x => x && x.length < 16);
  const steps = [...document.querySelectorAll('[class*=step],[class*=Step]')].filter(vis).map(e=>(e.innerText||'').trim().slice(0,40)).filter(Boolean).slice(0,10);
  return {
    url: location.href.slice(0, 100),
    title: document.title,
    inputs, files, btns: [...new Set(btns)].slice(0, 25), steps,
    body: (document.body.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0, 900)
  };
})()`);
console.log(JSON.stringify(ui, null, 1).slice(0, 3500));

const seen = new Map();
for (const r of c.netRequests()) {
  if (!/fxg\.jinritemai|\/product\/|\/category/.test(r.url)) continue;
  const k = r.url.split("?")[0].replace("https://fxg.jinritemai.com", "");
  if (!seen.has(k)) seen.set(k, 0);
  seen.set(k, seen.get(k) + 1);
}
console.log("\n建品页接口:");
for (const [k, n] of [...seen].sort((a,b)=>b[1]-a[1]).slice(0,30)) console.log(`  ${n}x ${k}`);
await c.shot(OUT + "/create-01.png").catch(() => {});
c.close();
