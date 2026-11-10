// 抖店邮箱登录（账号密码由用户提供）→ 勾「登录即代表同意」→ 点登录
import { CDP, sleep } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const EMAIL = "sundoudianqijian01@163.com";
const PASS = "Yxh65572589";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
let t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
if (!t) {
  console.log("没有抖店页，新建一个");
  const r = await fetch(`http://127.0.0.1:9224/json/new?${encodeURIComponent("https://fxg.jinritemai.com/ffa/g/list")}`, { method: "PUT" });
  t = await r.json();
  await sleep(4000);
}
const c = await CDP.connect(t.webSocketDebuggerUrl);
await c.send("Runtime.enable").catch(() => {});
await c.send("Page.enable").catch(() => {});

// 1) 确保在「邮箱登录」标签
const switched = await c.eval(`(() => {
  const btn = [...document.querySelectorAll('[class*=switch-button]')].find(e => (e.textContent||'').trim() === '邮箱登录');
  if (!btn) return 'no-btn';
  if (/true/.test(btn.className)) return 'already';
  btn.click(); return 'clicked';
})()`);
console.log("切标签:", switched);
await sleep(1200);

// 2) 填邮箱 + 密码（React 受控：native setter + input/change）
async function setVal(name, val) {
  return await c.eval(`(() => {
    const el = document.querySelector('input[name=${JSON.stringify(name)}]');
    if (!el) return {ok:false, reason:'no-input'};
    el.focus();
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    set.call(el, ${JSON.stringify(val)});
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
    el.dispatchEvent(new Event('blur', {bubbles:true}));
    return {ok:true, len: el.value.length, v: el.type === 'password' ? '***' : el.value};
  })()`);
}
console.log("邮箱:", JSON.stringify(await setVal("email", EMAIL)));
await sleep(400);
console.log("密码:", JSON.stringify(await setVal("password", PASS)));
await sleep(800);

// 3) 勾「登录即代表同意」
const cb = await c.eval(`(() => {
  const el = [...document.querySelectorAll('input[type=checkbox]')].find(e => e.offsetParent !== null);
  if (!el) return {ok:false};
  if (el.checked) return {ok:true, already:true};
  el.click();
  return {ok:true, clicked:true, now:!!el.checked};
})()`);
console.log("勾选框:", JSON.stringify(cb));
await sleep(500);

// 4) 点登录
const btn = await c.eval(`(() => {
  const b = [...document.querySelectorAll('button,[class*=submit]')]
    .filter(e => e.offsetParent !== null && /^登录$/.test((e.innerText||'').trim()));
  if (!b.length) return {ok:false};
  b[0].click();
  return {ok:true, cls:(b[0].className||'').toString().slice(0,40), n:b.length};
})()`);
console.log("点登录:", JSON.stringify(btn));

// 5) 等跳转
for (let i = 1; i <= 20; i++) {
  await sleep(1500);
  const s = await c.eval("({u: location.href, t: document.title})").catch(() => null);
  if (s && !s.u.includes("/login/")) { console.log(`已离开登录页（${i}）:`, s.u.slice(0,90), "|", s.t); break; }
  if (i % 4 === 0) console.log(`  等 ${i*1.5}s ... ` + (s ? s.u.slice(0,70) : "?"));
}
const fin = await c.eval(`({u: location.href, t: document.title, txt:(document.body?.innerText||'').replace(/\\n+/g,' | ').slice(0,220)})`).catch(() => null);
console.log("最终:", JSON.stringify(fin));
await c.shot(OUT + "/login-done.png").catch(() => {});
console.log("shot ok");
c.close();
