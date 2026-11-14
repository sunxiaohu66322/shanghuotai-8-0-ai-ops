// 把登录页切到「扫码登录」，并截图给用户扫
import { CDP, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const c = await CDP.page({ urlIncludes: "jinritemai" });
await c.send("Page.enable").catch(() => {});
await sleep(1500);

// 1) 看当前有几个 tab
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
console.log("TABS:", tabs.filter(t => t.type === "page").map(t => t.url.slice(0, 70)).join("\n      "));

// 2) 找扫码入口
const before = await c.eval(`(() => ({
  txt: (document.body?.innerText||'').slice(0,300),
  btns: [...document.querySelectorAll('div,span,a,button')].filter(e=>e.offsetParent!==null && /扫码|二维码|其他登录/.test((e.innerText||'').trim()) && (e.innerText||'').trim().length<14).map(e=>(e.innerText||'').trim()).slice(0,10)
}))()`);
console.log("BEFORE:", JSON.stringify(before));

// 点「扫码登录」或二维码切换
let r = await c.click('text="扫码登录"');
if (!r || !r.ok) r = await c.click('text="扫码"');
if (!r || !r.ok) r = await c.click('text="其他登录方式"');
console.log("CLICK:", JSON.stringify(r));
await sleep(2500);

const after = await c.eval(`(() => ({
  txt: (document.body?.innerText||'').slice(0,400),
  imgs: [...document.querySelectorAll('img,canvas')].filter(e=>e.offsetParent!==null).map(e=>({tag:e.tagName,w:e.width,h:e.height,cls:(e.className||'').toString().slice(0,30)})).slice(0,8)
}))()`);
console.log("AFTER:", JSON.stringify(after).slice(0, 1200));

await c.shot(OUT + "/login-qr.png");
console.log("shot ok");
c.close();
