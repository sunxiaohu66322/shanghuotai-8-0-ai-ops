// 按主人给的影刀式操作选品牌：聚焦下拉 → 方向键↓ → 回车
// 先探哪个 create 标签页还留着生成后的完整表单（含「品牌」）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = (await (await fetch("http://127.0.0.1:9224/json/list")).json())
  .filter(x => x.type === "page" && (x.url || "").includes("/ffa/g/create"));
console.log("create 标签页:", tabs.length);

const probe = `(() => {
  const t = (document.body.innerText || '');
  const labBrand = /^品牌$/m.test(t) || t.includes('品牌');
  const sel = [...document.querySelectorAll('div,span')].filter(e => (e.innerText||'').trim() === '请选择' && e.offsetParent !== null);
  return { len: t.length, hasBrand: labBrand,
           cat: (t.match(/家具\\s*>\\s*[^\\n]{0,40}/)||[''])[0],
           pickers: sel.length,
           brandRow: (t.split('\\n').findIndex(l => l.trim()==='品牌')) };
})()`;

const cands = [];
for (const t of tabs) {
  const c = await CDP.connect(t.webSocketDebuggerUrl);
  for (const m of ["Page.enable", "Runtime.enable", "DOM.enable"]) await c.send(m).catch(() => {});
  const s = await c.eval(probe).catch(e => ({ err: String(e) }));
  console.log("  ", t.id.slice(0, 8), JSON.stringify(s).slice(0, 200));
  if (s && s.hasBrand && s.len > 1200 && s.pickers > 0) cands.push({ t, c, s });
  else c.close();
}
if (!cands.length) { console.log("没有还带完整表单的 create 页"); process.exit(2); }

// 选「有品牌行且最靠近价格区」的那个（len 最大的通常最完整）
cands.sort((a, b) => b.s.len - a.s.len);
const { t, c, s } = cands[0];
console.log("\n使用标签:", t.id.slice(0, 8), "len=", s.len, "picker数=", s.pickers, "cat=", s.cat);

// 定位品牌那一行的「请选择」
const box = await c.eval(`(() => {
  const nodes = [...document.querySelectorAll('div,span')]
    .filter(e => (e.innerText||'').trim() === '请选择' && e.offsetParent !== null);
  // 品牌行：往上找容器，看容器文本是否包含"品牌"
  const hits = [];
  for (const n of nodes) {
    let p = n, depth = 0, hit = false;
    while (p && depth < 6) { const tx = (p.innerText||''); if (tx.includes('品牌')) { hit = true; break; }
      p = p.parentElement; depth++; }
    if (hit) { const r = n.getBoundingClientRect(); hits.push({ x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), w: Math.round(r.width), h: Math.round(r.height), txt:(n.innerText||'').trim() }); }
  }
  return { hits: hits.slice(0, 5), total: nodes.length };
})()`);
console.log("品牌候选点:", JSON.stringify(box));
if (!box.hits.length) { console.log("没定位到品牌选择框"); process.exit(3); }

const p0 = box.hits[0];
// 滚到视口内再点
await c.eval(`(() => { window.scrollBy(0, ${p0.y} - 400); })()`);
await sleep(600);
const p = await c.eval(`(() => {
  const nodes = [...document.querySelectorAll('div,span')]
    .filter(e => (e.innerText||'').trim() === '请选择' && e.offsetParent !== null);
  for (const n of nodes) {
    let q = n, d = 0, hit = false;
    while (q && d < 6) { if ((q.innerText||'').includes('品牌')) { hit = true; break; } q = q.parentElement; d++; }
    if (hit) { const r = n.getBoundingClientRect(); return { x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2) }; }
  }
  return null;
})()`);
console.log("点击坐标:", JSON.stringify(p));

// 1) 真实鼠标点开
await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
await sleep(200);
await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", clickCount: 1 });
await sleep(80);
await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", clickCount: 1 });
await sleep(900);

// 2) 浮层是否出现 + 有没有 LZL
const dd = await c.eval(`(() => {
  const opts = [...document.querySelectorAll('[class*=option],[class*=Option],[role=option],li,div')]
    .filter(e => e.offsetParent !== null && /LZL/.test((e.innerText||'')) && (e.innerText||'').length < 40);
  return { optCount: opts.length, texts: [...new Set(opts.map(e => (e.innerText||'').trim()))].slice(0, 6) };
})()`);
console.log("浮层探测:", JSON.stringify(dd));

// 3) 方向键 ↓ + 回车（主人给的影刀式动作）
async function key(code, keyName) {
  await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, key: keyName, code: keyName });
  await sleep(60);
  await c.send("Input.dispatchKeyEvent", { type: "keyUp", windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, key: keyName, code: keyName });
}
await key(40, "ArrowDown");
await sleep(500);
await key(13, "Enter");
await sleep(1200);

const after = await c.eval(`(() => {
  const t = (document.body.innerText || '');
  const lzl = [...document.querySelectorAll('div,span')].filter(e => (e.innerText||'').trim() === 'LZL' && e.offsetParent !== null).length;
  return { lzlVisible: lzl, tail: t.replace(/\\n{2,}/g,'\\n').slice(0, 700) };
})()`);
await c.shot(OUT + "/brand-after-key.png").catch(() => {});
await saveJson(OUT + "/brand-after-key.json", after);
console.log("\n选完状态: lzlVisible=", after.lzlVisible);
console.log(after.tail);
c.close();
