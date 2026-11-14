// 看邮箱登录表单现状：字段、是否被密码插件填充、勾选框、按钮
import { CDP, sleep } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
await c.send("Runtime.enable").catch(() => {});

const st = await c.eval(`(() => {
  const ins = [...document.querySelectorAll('input')].filter(e => e.offsetParent !== null);
  const rows = [...document.querySelectorAll('[class*=input-row],[class*=input-item],label')].filter(e=>e.offsetParent!==null).map(e=>(e.innerText||'').trim().slice(0,20)).filter(Boolean);
  const btns = [...document.querySelectorAll('button,[class*=btn],[class*=login-btn],[class*=submit]')].filter(e=>e.offsetParent!==null).map(e=>({t:(e.innerText||'').trim().slice(0,12),cls:(e.className||'').toString().slice(0,36)})).filter(x=>x.t);
  const cbs = [...document.querySelectorAll('input[type=checkbox]')].map(e=>({vis:e.offsetParent!==null, checked:!!e.checked, name:e.name, id:e.id,
      lab:((e.closest('label')||e.parentElement?.parentElement||{}).innerText||'').trim().slice(0,30)}));
  return {
    url: location.href.slice(0,80),
    inputs: ins.map(e=>({type:e.type,name:e.name,id:e.id,ph:e.placeholder,vlen:(e.value||'').length,v: e.type==='password' ? '' : (e.value||'').slice(0,40)})),
    rows, btns, cbs,
    body: (document.body?.innerText||'').replace(/\\n+/g,' | ').slice(0, 500)
  };
})()`);
console.log(JSON.stringify(st, null, 1));
await c.shot(OUT + "/login-form.png");
console.log("shot ok");
c.close();
