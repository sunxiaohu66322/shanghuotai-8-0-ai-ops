// 抖店登录：邮箱登录 → 让密码插件自动填充 → 勾「自动登录」→ 点登录
// 铁律：不关浏览器、不重开、不复位 profile；全程在同一个 9224 实例里用页面操作。
import { CDP, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const DD = "https://fxg.jinritemai.com";
const log = (...a) => console.log(...a);

// 1) 复用已有抖店页；没有就新建（Target.createTarget，不经 /json/new）
async function pickPage() {
  const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
  const hit = tabs.filter(t => t.type === "page").find(t => (t.url || "").includes("jinritemai"));
  if (hit) { log("复用已有抖店页:", hit.url.slice(0, 80)); return await CDP.connect(hit.webSocketDebuggerUrl); }
  const r = await fetch(`http://127.0.0.1:9224/json/new?${encodeURIComponent(DD + "/ffa/g/list")}`, { method: "PUT" });
  const t = await r.json();
  log("新建页:", (t.url || "").slice(0, 80));
  return await CDP.connect(t.webSocketDebuggerUrl);
}

const c = await pickPage();
await c.send("Page.enable").catch(() => {});
await c.send("Runtime.enable").catch(() => {});
await c.send("DOM.enable").catch(() => {});

// 2) 如果不在登录页，导航过去（不重开浏览器，只导航）
const cur = await c.eval("location.href").catch(() => "");
if (!cur.includes("/login/")) {
  await c.send("Page.navigate", { url: DD + "/ffa/g/list" });
  await sleep(3500);
}
let url = await c.eval("location.href").catch(() => "");
log("当前页:", url.slice(0, 100));
if (!url.includes("/login/")) {
  log("看起来已登录，直接结束。");
  await c.shot(OUT + "/login-step0.png").catch(() => {});
  c.close(); process.exit(0);
}

// 3) 点「邮箱登录」
for (const label of ['text="邮箱登录"', 'text="邮箱"']) {
  const r = await c.click(label);
  log("click", label, JSON.stringify(r));
  if (r && r.ok) break;
}
await sleep(1500);
await c.shot(OUT + "/login-step1-email.png").catch(() => {});

// 4) 看表单：有哪些输入框、是否已被插件填充
async function dumpForm(tag) {
  const f = await c.eval(`(() => {
    const ins = [...document.querySelectorAll('input')].filter(e => e.offsetParent !== null);
    const cbs = [...document.querySelectorAll('input[type=checkbox],span[class*=checkbox],div[role=checkbox]')]
      .map(e => ({tag:e.tagName, type:e.type||'', checked:!!e.checked, txt:(e.closest('label')?.innerText||e.parentElement?.innerText||'').trim().slice(0,30)}));
    return {
      inputs: ins.map(e => ({type:e.type, name:e.name, ph:e.placeholder, val:(e.value||'').length > 0 ? (e.type==='password' ? '***'+e.value.length : e.value.slice(0,32)) : ''})),
      checkboxes: cbs.slice(0,8),
      bodyTxt: (document.body?.innerText||'').replace(/\\n+/g,' | ').slice(0, 400)
    };
  })()`);
  log(`[${tag}]`, JSON.stringify(f, null, 1).slice(0, 1800));
  return f;
}
let f = await dumpForm("邮箱登录后");

// 5) 如果密码没被填，等插件一会儿（插件异步注入）
let tries = 0;
while (tries++ < 12) {
  const ok = await c.eval(`(() => {
    const p = [...document.querySelectorAll('input[type=password]')].filter(e=>e.offsetParent!==null)[0];
    const u = [...document.querySelectorAll('input')].filter(e=>e.offsetParent!==null && (e.type==='text'||e.type==='tel'||e.type==='email'))[0];
    return { pw: p ? (p.value||'').length : -1, user: u ? (u.value||'') : '' };
  })()`);
  if (ok.pw > 0) { log(`插件已填充（第${tries}次轮询）: user=${ok.user} pw长度=${ok.pw}`); break; }
  await sleep(1000);
}
f = await dumpForm("等待填充后");

// 6) 勾「自动登录/记住」类勾选框
const cb = await c.eval(`(() => {
  const cands = [...document.querySelectorAll('input[type=checkbox],div[role=checkbox],span[class*=checkbox],i[class*=checkbox]')]
    .filter(e => e.offsetParent !== null);
  const info = cands.map(e => ({ tag:e.tagName, cls:(e.className||'').toString().slice(0,40),
      label: ((e.closest('label')||e.parentElement||{}).innerText||'').trim().slice(0,24),
      checked: e.checked === undefined ? null : !!e.checked }));
  return info;
})()`);
log("勾选框候选:", JSON.stringify(cb, null, 1).slice(0, 1200));

const clickCb = await c.eval(`(() => {
  const wanted = /自动登录|记住|同意|下次/;
  const cands = [...document.querySelectorAll('input[type=checkbox],div[role=checkbox],span[class*=checkbox],i[class*=checkbox]')]
    .filter(e => e.offsetParent !== null);
  for (const e of cands) {
    const lab = ((e.closest('label')||e.parentElement||{}).innerText||'').trim();
    if (wanted.test(lab)) {
      const willBe = !(e.checked);
      e.click();
      return { ok:true, label: lab.slice(0,30), nowChecked: willBe };
    }
  }
  // 兜底：如果只有唯一一个勾选框，就勾它
  if (cands.length === 1) { const e = cands[0]; const willBe = !(e.checked); e.click(); return { ok:true, fallback:true, nowChecked: willBe }; }
  return { ok:false, count: cands.length };
})()`);
log("勾选结果:", JSON.stringify(clickCb));
await sleep(500);
await c.shot(OUT + "/login-step2-checked.png").catch(() => {});

// 7) 点登录（仅当密码已填）
const pwLen = await c.eval(`(() => { const p=[...document.querySelectorAll('input[type=password]')].filter(e=>e.offsetParent!==null)[0]; return p?(p.value||'').length:-1; })()`);
log("密码长度:", pwLen);
if (pwLen > 0) {
  const r = await c.click('text="登录"');
  log("点登录:", JSON.stringify(r));
  await sleep(6000);
  const after = await c.eval("({url: location.href, title: document.title, txt:(document.body?.innerText||'').slice(0,160)})").catch(() => null);
  log("登录后:", JSON.stringify(after));
} else {
  log("密码框为空 —— 插件没填，先不点登录，等人工处理。");
}
await c.shot(OUT + "/login-step3-result.png").catch(() => {});
c.close();
