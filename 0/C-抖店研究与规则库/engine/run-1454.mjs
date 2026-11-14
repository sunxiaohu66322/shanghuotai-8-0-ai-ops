// 端到端（到「已识别」为止）：新标签 → 传 1:1 主图+详情 → 下一步 → 填标题 → 读类目区
// 不点「确认信息，继续生成」，不存草稿、不发布
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const WORK = "G:\\Users\\Administrator\\Documents\\deepseek-harness\\default-workspace\\抖店研究\\work\\1454";
const plan = JSON.parse(await fs.readFile(OUT + "/plan-1454.json", "utf8"));
const title = plan.title;

const files = [
  `${WORK}\\sq_image_3da0d718bbce.jpg`,
  `${WORK}\\sq_image_635d76352013.jpg`,
  `${WORK}\\sq_image_af26132d18c8.jpg`,
  `${WORK}\\dt_image_50e4f63db3f1.jpg`,
];
for (const f of files) { await fs.access(f); }
console.log("待上传:", files.map(f => f.split("\\").pop()).join(", "));

// 1) 新标签
const nr = await fetch("http://127.0.0.1:9224/json/new?url=about:blank", { method: "PUT" });
const tgt = await nr.json();
const c = await CDP.connect(tgt.webSocketDebuggerUrl);
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});
await c.send("Page.navigate", { url: "https://fxg.jinritemai.com/ffa/g/create" }).catch(() => {});

let ok = 0;
for (let i = 1; i <= 30; i++) {
  await sleep(1500);
  ok = await c.eval(`document.querySelectorAll('input[type=file]').length`).catch(() => 0);
  if (ok > 0) { console.log(`${(i * 1.5).toFixed(0)}s 表单就绪 inputs=${ok}`); break; }
}
if (!ok) { console.log("表单未就绪"); process.exit(1); }

// 2) 传 4 张
const doc = await c.send("DOM.getDocument", { depth: -1 });
const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
await c.send("DOM.setFileInputFiles", { files, nodeId: q.nodeIds[0] });
console.log("已投递 4 张");

for (let i = 1; i <= 60; i++) {
  await sleep(1500);
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return { n:Number((t.match(/已上传 (\\d+)/)||[0,0])[1]), full:/容量已满/.test(t), fail:/上传失败/.test(t),
             busy:/上传中|正在上传|解析中|识别中/.test(t) };
  })()`);
  if (i % 4 === 0 || !s.busy) console.log(`  ${(i * 1.5).toFixed(0)}s 已上传=${s.n} full=${s.full} fail=${s.fail}`);
  if (s.full || s.fail) { console.log("上传受阻，停"); process.exit(1); }
  if (s.n >= 4 && !s.busy) break;
}

// 3) 点「下一步，生成商品」
async function clickByText(re) {
  const pos = await c.eval(`(() => {
    const e=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
      .filter(x=>x.offsetParent!==null && ${re}.test((x.innerText||'').trim()))[0];
    if(!e) return {found:false};
    const r=e.getBoundingClientRect();
    return {found:true, dis:e.disabled===true, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  })()`);
  if (!pos.found) return pos;
  await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y, button: "none" });
  await sleep(120);
  await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  await sleep(90);
  await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: pos.x, y: pos.y, button: "left", clickCount: 1 });
  return pos;
}
console.log("点下一步:", JSON.stringify(await clickByText("/下一步，生成商品/")));

for (let i = 1; i <= 40; i++) {
  await sleep(2000);
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'');
    return { rec:/已识别并分类商品材料/.test(t), busy:/生成中|识别中|请稍候/.test(t), len:t.length };
  })()`);
  if (i % 4 === 0) console.log(`  ${i * 2}s 识别完成=${s.rec} busy=${s.busy} len=${s.len}`);
  if (s.rec && !s.busy) { console.log("✅ 已识别，进入复核页"); break; }
}

// 4) 填标题
const filled = await c.eval(`(() => {
  const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
  const el = all.find(e => /2-60/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
  if(!el) return {ok:false};
  const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
  setter.call(el, ${JSON.stringify(title)});
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  return {ok:true, now: el.value};
})()`);
console.log("填标题:", JSON.stringify(filled));
await sleep(2000);

// 5) 读类目区 + 按钮态
await c.shot(OUT + "/run-1454-recognized.png").catch(() => {});
const dump = await c.eval(`(() => {
  const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
  const btns=[...document.querySelectorAll('button,[class*=btn],[role=button]')]
    .filter(e=>e.offsetParent!==null).map(e=>({txt:(e.innerText||'').trim().slice(0,24), dis:e.disabled===true}))
    .filter(b=>b.txt);
  return { url: location.href, len: t.length, txt: t, btns: btns.slice(0,30) };
})()`);
await saveJson(OUT + "/run-1454-recognized.json", dump);
console.log("\nLEN:", dump.len);
console.log(dump.txt);
console.log("\n按钮:", JSON.stringify(dump.btns));
console.log("\nTAB=" + tgt.id);
c.close();
