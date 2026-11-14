// 抖店 · 一件商品完整上架器（CDP 直连真实浏览器，后台窗口，不抢焦点）
//
//   node publish_one.mjs --pid 1531 --stage all
//   node publish_one.mjs --pid 1531 --stage probe|cat|attrs|spec|save|publish
//   node publish_one.mjs --pid 1531 --stage publish --dry     # 只找按钮，不点
//
// 数据来源：migrate mk_plan.py 生成的 crawl3/plan-<pid>.json（上货台 v3.db 的一件商品）
// 阶段划分是为了「卡在哪一步就只重跑那一步」—— 每一步都把页面状态 + 截图落盘。
import { CDP, sleep, saveJson } from "./cdp.mjs";
import fs from "node:fs/promises";

const WS = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace";
const OUT = WS + "/抖店研究/crawl3";
const PORT = Number(process.env.EGO_PORT || 9224);
const DD = "https://fxg.jinritemai.com";

const arg = (k, d = null) => {
  const i = process.argv.indexOf("--" + k);
  if (i < 0) return d;
  const v = process.argv[i + 1];
  return v && !v.startsWith("--") ? v : true;
};
const pid = String(arg("pid", ""));
const stage = String(arg("stage", "all"));
const dry = Boolean(arg("dry", false));
const weightKg = Number(arg("weight", 40));
if (!pid) { console.log("用法: node publish_one.mjs --pid N --stage probe|cat|attrs|spec|save|publish|all [--dry] [--weight 40]"); process.exit(2); }

const plan = JSON.parse(await fs.readFile(`${OUT}/plan-${pid}.json`, "utf8"));
const log = (...a) => console.log(...a);

// ---------- 连页面 ----------
async function findCreatePage() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  return list.find((t) => t.type === "page" && /fxg\.jinritemai\.com\/ffa\/g\/create/.test(t.url) && t.webSocketDebuggerUrl) || null;
}

async function openBackground() {
  const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const bws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((r) => bws.addEventListener("open", r, { once: true }));
  let seq = 0; const pend = new Map();
  bws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  });
  const bsend = (method, params = {}) => new Promise((res, rej) => {
    const id = ++seq;
    pend.set(id, (m) => (m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result)));
    bws.send(JSON.stringify({ id, method, params }));
  });
  const { targetId } = await bsend("Target.createTarget", { url: "about:blank", newWindow: true, background: true });
  let wsUrl = null;
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    const t = list.find((x) => x.id === targetId);
    if (t && t.webSocketDebuggerUrl) { wsUrl = t.webSocketDebuggerUrl; break; }
  }
  bws.close();
  if (!wsUrl) throw new Error("拿不到后台窗口 ws");
  return wsUrl;
}

let c;
const existing = await findCreatePage();
if ((stage === "probe" || stage === "all") && !existing) {
  log("== 开新后台窗口");
  c = await CDP.connect(await openBackground());
} else if (existing) {
  log("== 复用已有编辑页:", existing.url.slice(0, 70));
  c = await CDP.connect(existing.webSocketDebuggerUrl);
} else {
  c = await CDP.connect(await openBackground());
}
for (const m of ["Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"]) await c.send(m).catch(() => {});

// ---------- 通用动作 ----------
async function clickText(exact, { nth = -1 } = {}) {
  const r = await c.eval(`(() => {
    const t = ${JSON.stringify(exact)};
    const els = [...document.querySelectorAll('button,[class*=btn],[role=button]')]
      .filter(x => x.offsetParent !== null && (x.innerText||'').trim() === t);
    if (!els.length) return { found:false };
    const idx = ${nth} < 0 ? els.length - 1 : ${nth};
    const e = els[idx];
    if (!e) return { found:false, oob:true };
    const rc = e.getBoundingClientRect();
    return { found:true, dis: e.disabled === true, txt:(e.innerText||'').trim().slice(0,20),
             x: Math.round(rc.x + rc.width/2), y: Math.round(rc.y + rc.height/2) };
  })()`);
  if (!r.found) return r;
  if (r.dis) return { ...r, clicked: false };
  await mouseClick(r.x, r.y, "点 " + exact);
  return { ...r, clicked: true };
}

async function mouseClick(x, y, label = "") {
  await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, button: "none" });
  await sleep(120);
  await c.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await sleep(90);
  await c.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  if (label) log("   ↳", label, `(${x},${y})`);
}

async function uploadFiles(files) {
  const doc = await c.send("DOM.getDocument", { depth: -1, pierce: true });
  const q = await c.send("DOM.querySelectorAll", { nodeId: doc.root.nodeId, selector: "input[type=file]" });
  if (!q.nodeIds.length) return 0;
  await c.send("DOM.setFileInputFiles", { files, nodeId: q.nodeIds[0] });
  return q.nodeIds.length;
}

const errItems = () => c.eval(`[...document.querySelectorAll('[class*="has-error"]')].filter(e=>e.offsetParent!==null).map(e=>(e.innerText||'').replace(/\\s+/g,' ').trim().slice(0,70))`);
const progress = () => c.eval(`[...document.querySelectorAll("*")].filter(e=>e.children.length===0&&/^\\d+%$/.test((e.innerText||'').trim())).map(e=>e.innerText.trim())`);

async function dump(tag, { shot = true } = {}) {
  const s = await c.eval(`(() => {
    const vis = e => e.offsetParent !== null;
    const t = (document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
    const btns = [...document.querySelectorAll('button,[class*=btn],[role=button]')].filter(vis)
      .map(e=>({t:(e.innerText||'').trim().slice(0,26), d:e.disabled===true})).filter(b=>b.t);
    return { url: location.href, len: t.length, txt: t, btns: btns.slice(0,70) };
  })()`);
  await saveJson(`${OUT}/one-${pid}-${tag}.json`, s);
  if (shot) await c.shot(`${OUT}/one-${pid}-${tag}.png`).catch(() => {});
  log(`[${tag}] url=${s.url.slice(0,60)} len=${s.len} err=${(await errItems()).length} prog=${JSON.stringify(await progress())}`);
  return s;
}

// ---------- 阶段 1：传图 → 生成 → 标题 ----------
async function stageProbe() {
  const files = [...plan.main.slice(0, 3), plan.detail[0]].filter(Boolean);
  for (const f of files) await fs.access(f);

  // 已经在 create 页且已上传完成 → 跳过导航与上传（重跑 probe 时别把已传的图冲掉）
  const pre = await c.eval(`(() => { const t=document.body.innerText||'';
    return { onCreate: /ffa\\/g\\/create/.test(location.href),
             up: Number((t.match(/已上传 (\\d+)/)||[0,0])[1]),
             editing: /商品标题|填写检查/.test(t) }; })()`).catch(() => ({}));
  log("== 当前页状态:", JSON.stringify(pre));

  const skipUpload = pre.onCreate && !pre.editing && pre.up >= files.length;
  if (skipUpload) {
    log("== 已有", pre.up, "张上传完成，跳过导航与上传");
  } else if (pre.editing) {
    log("== 已在编辑页，跳过 probe");
    await dump("probe-already");
    return;
  } else {
    await c.send("Page.navigate", { url: DD + "/ffa/g/create" }).catch(() => {});
    let ready = false;
    for (let i = 1; i <= 40; i++) {
      await sleep(1500);
      const s = await c.eval(`(() => ({i:document.querySelectorAll('input[type=file]').length,
        ui:/点击或拖拽|已上传/.test(document.body.innerText||''),
        btn:[...document.querySelectorAll('button')].some(b=>/下一步/.test(b.innerText||''))}))()`).catch(() => ({}));
      if (s.i > 0 && s.ui && s.btn) { ready = true; log(`== 表单就绪 ${(i*1.5).toFixed(0)}s`); break; }
    }
    if (!ready) { log("[!] create 页未就绪"); process.exit(4); }

    log("== 投递", files.length, "张:", files.map((f) => f.split(/[\\/]/).pop()).join(","));
    await uploadFiles(files);

    let up = 0;
    for (let i = 1; i <= 60; i++) {
      await sleep(1500);
      const s = await c.eval(`(() => { const t=document.body.innerText||'';
        return {n:Number((t.match(/已上传 (\\d+)/)||[0,0])[1]), full:/容量已满/.test(t),
                fail:/上传失败/.test(t), busy:/上传中|正在上传|解析中|识别中/.test(t)}; })()`);
      if (s.full || s.fail) { log("[!] 上传受阻 full=", s.full, "fail=", s.fail); break; }
      if (s.n >= files.length && !s.busy) { up = s.n; break; }
      if (i % 6 === 0) log(`   ${(i*1.5).toFixed(0)}s 已上传=${s.n}`);
    }
    log("== 上传完成", up, "/", files.length);
    if (up < files.length) { await dump("upload-fail"); process.exit(5); }
  }

  const nx = await clickText("下一步，生成商品");
  log("== 下一步:", JSON.stringify(nx));
  let rec = false;
  for (let i = 1; i <= 50; i++) {
    await sleep(2000);
    const s = await c.eval(`(() => { const t=document.body.innerText||'';
      return {rec:/已识别并分类商品材料|确认信息，继续生成/.test(t), busy:/生成中|识别中|请稍候/.test(t)}; })()`);
    if (s.rec && !s.busy) { rec = true; log(`== 已识别 ${i*2}s`); break; }
    if (i % 8 === 0) log(`   ${i*2}s rec=${s.rec} busy=${s.busy}`);
  }
  if (!rec) log("[!] 未进入识别完成态，继续 dump");

  // 填标题
  const t = await c.eval(`(() => {
    const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
    const el = all.find(e => /2-60/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
    if(!el) return {ok:false};
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
    setter.call(el, ${JSON.stringify(plan.title)});
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    return {ok:true, now: el.value.slice(0,40)};
  })()`);
  log("== 填标题:", JSON.stringify(t));
  await sleep(2500);
  await dump("probe");
}

// ---------- 阶段 2：类目 + 确认生成 ----------
async function stageCat() {
  // 2.0 标题超长会被拦（抖店上限 60 字符 / 30 汉字）→ 先截断
  const tt = await c.eval(`(() => {
    const all=[...document.querySelectorAll('input,textarea')].filter(e=>e.offsetParent!==null);
    const el = all.find(e => /2-60/.test(e.placeholder||'')) || all.find(e=>e.maxLength===60);
    if(!el) return {ok:false,why:'no-title-input'};
    const m = (document.body.innerText||'').match(/(\\d+)\\/60/);
    const cur = el.value || '';
    const n = cur.length;
    if (n <= 60) return {ok:true,changed:false,len:n,now:cur.slice(0,40)};
    const cut = cur.slice(0, 60);
    const setter = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value').set;
    setter.call(el, cut);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    el.dispatchEvent(new Event('blur',{bubbles:true}));
    return {ok:true,changed:true,before:n,after:cut.length,now:cut};
  })()`);
  log("== 标题长度修正:", JSON.stringify(tt));
  await sleep(1500);

  // 2.1 删掉所有「长宽比需为1:1」的主图（否则确认按钮点了没反应）
  for (let round = 0; round < 6; round++) {
    const hit = await c.eval(`(() => {
      const vis=e=>e.offsetParent!==null;
      // 找含「长宽比需为1:1」的最小卡片容器，再在容器里点「删除」
      const marks=[...document.querySelectorAll('*')].filter(e=>vis(e)&&/长宽比需为1:1/.test(e.innerText||''));
      if(!marks.length) return {found:false,left:0};
      // 从最小的那个往上找同时含「删除」的容器
      const sorted=marks.sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);
      for(const m of sorted){
        let p=m;
        for(let i=0;i<6&&p;i++){
          const del=[...p.querySelectorAll('span,div,a,button')].filter(x=>vis(x)&&(x.innerText||'').trim()==='删除');
          if(del.length){
            const e=del[del.length-1];
            e.scrollIntoView({block:'center'});
            const r=e.getBoundingClientRect();
            return {found:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),left:sorted.length};
          }
          p=p.parentElement;
        }
      }
      return {found:false,left:sorted.length};
    })()`);
    if (!hit.found) { log("== 比例不符主图剩余:", hit.left || 0, "（不需要再删）"); break; }
    await mouseClick(hit.x, hit.y, `删除比例不符主图（剩 ${hit.left}）`);
    await sleep(2200);
  }

  const needCat = await c.eval(`/没有满意的推荐结果？手动选择类目/.test(document.body.innerText||'')`);
  const catNow = await c.eval(`(() => {
    const t=document.body.innerText||'';
    const m=t.match(/([\\u4e00-\\u9fa5]+\\s*>\\s*[\\u4e00-\\u9fa5]+\\s*>\\s*[\\u4e00-\\u9fa5]+)/);
    return m?m[1]:null;
  })()`);
  log("== 需要手动选类目:", needCat, "| 当前类目:", catNow);

  if (needCat) {
    const lk = await c.eval(`(() => {
      const e=[...document.querySelectorAll('a,span,div,button')]
        .filter(x=>x.offsetParent!==null && /手动选择类目/.test((x.innerText||'').trim()) && (x.innerText||'').trim().length<24).pop();
      if(!e) return null;
      const r=e.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
    })()`);
    if (lk) {
      await mouseClick(lk.x, lk.y, "手动选择类目");
      await sleep(3500);
      // 优先：最近用过的类目列表里，找「茶几/边几」或含茶桌的
      const recent = await c.eval(`(() => {
        const cands=[...document.querySelectorAll('div,span,li')]
          .filter(x=>x.offsetParent!==null && /茶几\\/边几|茶几|茶桌/.test(x.innerText||'') && (x.innerText||'').trim().length<44);
        if(!cands.length) return null;
        const e=cands[cands.length-1];
        const r=e.getBoundingClientRect();
        return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),t:(e.innerText||'').trim().slice(0,40)};
      })()`);
      if (recent) {
        await mouseClick(recent.x, recent.y, "选类目 " + recent.t);
        await sleep(2200);
        const okB = await c.eval(`(() => {
          const b=[...document.querySelectorAll('button,[class*=btn]')].filter(x=>x.offsetParent!==null && (x.innerText||'').trim()==='确认');
          const hit=b.map(e=>{const r=e.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),dis:e.disabled===true}}).find(o=>!o.dis);
          return hit||null;
        })()`);
        if (okB) { await mouseClick(okB.x, okB.y, "确认类目"); await sleep(4000); }
        else log("  [!] 没有可点的「确认」（类目弹窗未选中？）");
      } else log("  [!] 类目弹窗里没找到茶几/茶桌");
    } else log("  [!] 没找到「手动选择类目」");
  }

  const go = await clickText("确认信息，继续生成");
  log("== 确认生成:", JSON.stringify(go));
  for (let i = 1; i <= 50; i++) {
    await sleep(2000);
    const s = await c.eval(`(() => { const t=document.body.innerText||'';
      return {gone:!/确认信息，继续生成/.test(t), len:t.length}; })()`);
    if (s.gone && s.len > 1200) { log(`== 进入编辑页 ${i*2}s`); break; }
    if (i % 8 === 0) log(`   ${i*2}s gone=${s.gone} len=${s.len}`);
  }
  await dump("cat");
}

// ---------- 阶段 3：品牌 + 功能 + 木种 ----------
async function stageAttrs(opts = { brand: "LZL", func: "可耐磨", wood: "黑胡桃" }) {
  // 品牌：单选下拉 → 点开 → ArrowDown → Enter
  const bp = await c.eval(`(() => {
    const vis=e=>e.offsetParent!==null;
    const lab=[...document.querySelectorAll('div,span,label')].find(e=>vis(e)&&e.children.length===0&&(e.innerText||'').trim()==='品牌');
    if(!lab) return null;
    let row=lab;
    for(let i=0;i<8&&row;i++){
      const sel=row.querySelector&&row.querySelector('.ecom-g-select');
      if(sel){sel.scrollIntoView({block:'center'});const r=sel.getBoundingClientRect();
        return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}
      row=row.parentElement;
    }
    return null;
  })()`);
  if (bp) {
    await mouseClick(bp.x, bp.y, "打开品牌下拉");
    await sleep(1500);
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 }).catch(() => {});
    await c.send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowDown", code: "ArrowDown", windowsVirtualKeyCode: 40 }).catch(() => {});
    await sleep(350);
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }).catch(() => {});
    await c.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }).catch(() => {});
    await sleep(1200);
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }).catch(() => {});
  } else log("  [!] 没找到品牌字段");

  async function pickMulti(rowKey, want) {
    const ctl = await c.eval(`(() => {
      const vis=e=>e.offsetParent!==null;
      const els=[...document.querySelectorAll('[class*="select"],[class*="Select"]')].filter(e=>{
        if(!vis(e)) return false;
        let p=e,row='';
        for(let i=0;i<5&&p;i++){p=p.parentElement;if(p){const t=(p.innerText||'').replace(/\\s+/g,' ').trim();if(t.length>row.length)row=t;}}
        return new RegExp(${JSON.stringify(rowKey)}).test(row);
      });
      if(!els.length) return null;
      els[0].scrollIntoView({block:'center'});
      const r=els[0].getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
    })()`);
    if (!ctl) { log("  [!] 没找到", rowKey); return null; }
    await mouseClick(ctl.x, ctl.y, "打开 " + rowKey);
    await sleep(2400);
    const leaf = await c.eval(`(() => {
      const hits=[...document.querySelectorAll('div,span,li,label')]
        .filter(e=>e.offsetParent!==null && e.children.length===0 && (e.textContent||'').trim()===${JSON.stringify(want)})
        .map(e=>{const r=e.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})
        .filter(o=>o.y > ${ctl.y} + 20);
      return hits[0]||null;
    })()`);
    if (leaf) { await mouseClick(leaf.x, leaf.y, "选 " + want); await sleep(1600); }
    else log("  [!] 下拉里没找到选项:", want);
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }).catch(() => {});
    await sleep(800);
  }
  await pickMulti("功能", opts.func);
  await pickMulti("支撑件木种", opts.wood);
  log("== 属性后 err=", JSON.stringify(await errItems()), "prog=", JSON.stringify(await progress()));
  await dump("attrs");
}

// ---------- 阶段 4：规格 + 价格库存 + 重量 ----------
async function stageSpec() {
  const rows = plan.skus.map((s) => ({ spec: (s.spec && s.spec[0]) || s.name, price: s.price, stock: s.stock }));
  log("== SKU 行:", JSON.stringify(rows));

  // 规格名：款式 → 尺寸
  const nameCtl = await c.eval(`(() => {
    const sels=[...document.querySelectorAll('.ecom-g-select')].filter(e=>e.offsetParent!==null);
    const hit=sels.find(e=>/款式|尺寸/.test((e.innerText||'').replace(/\\s+/g,' ')));
    if(!hit) return null;
    hit.scrollIntoView({block:'center'});
    const r=hit.getBoundingClientRect();
    return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),now:(hit.innerText||'').replace(/\\s+/g,' ').trim()};
  })()`);
  log("== 规格名控件:", JSON.stringify(nameCtl));
  if (nameCtl && nameCtl.now === "款式") {
    await mouseClick(nameCtl.x, nameCtl.y, "打开规格名下拉");
    await sleep(2200);
    const dim = await c.eval(`(() => {
      return [...document.querySelectorAll('div,span,li')]
        .filter(e=>e.offsetParent!==null && e.children.length===0 && (e.textContent||'').trim()==='尺寸')
        .map(e=>{const r=e.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})
        .filter(o=>o.y > ${nameCtl.y} - 60 && o.y < ${nameCtl.y} + 420);
    })()`);
    if (dim.length) { await mouseClick(dim[0].x, dim[0].y, "选尺寸"); await sleep(2400); }
    else log("  [!] 规格名下拉里没找到「尺寸」");
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 }).catch(() => {});
  }

  // 规格值：原生 setter → 点击聚焦 → Enter 提交成一行
  for (const r of rows) {
    const ok = await c.eval(`(() => {
      const inputs=[...document.querySelectorAll('input.ecom-g-input')].filter(e=>e.offsetParent!==null && /请输入款式|请输入尺寸/.test(e.placeholder||''));
      const el=inputs.find(e=>!e.value);
      if(!el) return null;
      el.focus();
      const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      setter.call(el, ${JSON.stringify(r.spec)});
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      const rc=el.getBoundingClientRect();
      return {x:Math.round(rc.x+rc.width/2),y:Math.round(rc.y+rc.height/2)};
    })()`);
    if (!ok) { log("  [!] 没有空规格值框了（", r.spec, "）"); break; }
    await mouseClick(ok.x, ok.y, "聚焦规格值框 " + r.spec);
    await sleep(500);
    await c.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }).catch(() => {});
    await c.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 }).catch(() => {});
    await sleep(2000);
  }

  // SKU 价格/库存
  const filled = await c.eval(`(() => {
    const vis=e=>e.offsetParent!==null;
    const setV=(el,v)=>{el.focus();const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      s.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));el.dispatchEvent(new Event('blur',{bubbles:true}));};
    const data=${JSON.stringify(rows)};
    const trs=[...document.querySelectorAll('tr')].filter(vis);
    const done=[];
    for(const r of data){
      const tr=trs.find(t=>(t.innerText||'').trim().startsWith(r.spec));
      if(!tr){done.push({spec:r.spec,ok:false,why:'no-tr'});continue;}
      const inps=[...tr.querySelectorAll('input')].filter(vis);
      const priceEl=inps.find(e=>(e.className||'').toString().includes('number')&&!/库存/.test(e.placeholder||''));
      const stockEl=inps.find(e=>/库存/.test(e.placeholder||''));
      if(priceEl)setV(priceEl,String(r.price));
      if(stockEl)setV(stockEl,String(r.stock));
      done.push({spec:r.spec,ok:!!(priceEl&&stockEl),price:priceEl?priceEl.value:null,stock:stockEl?stockEl.value:null});
    }
    return done;
  })()`);
  log("== 价格库存填充:", JSON.stringify(filled));
  await sleep(3000);

  // 重量
  const chk = await clickText("填写检查");
  log("== 填写检查:", JSON.stringify(chk));
  await sleep(4500);
  const errs = await errItems();
  log("== 检查后错误:", JSON.stringify(errs));
  const w = await c.eval(`(() => {
    const box=[...document.querySelectorAll('[class*="has-error"]')].find(e=>e.offsetParent!==null);
    const inp=box&&box.querySelector('input');
    if(!inp) return {ok:false,why:'no-error-box'};
    inp.scrollIntoView({block:'center'});inp.focus();
    const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
    s.call(inp, ${JSON.stringify(String(weightKg))});
    inp.dispatchEvent(new Event('input',{bubbles:true}));
    inp.dispatchEvent(new Event('change',{bubbles:true}));
    inp.dispatchEvent(new Event('blur',{bubbles:true}));
    return {ok:true, label:(box.innerText||'').replace(/\\s+/g,' ').trim().slice(0,50)};
  })()`);
  log("== 重量:", JSON.stringify(w));
  await sleep(2500);
  await dump("spec");
}

// ---------- 阶段 5：保存草稿 ----------
async function stageSave() {
  await clickText("填写检查");
  await sleep(5000);
  const errs = await errItems();
  if (errs.length) {
    log("[!] 仍有必填未填:", JSON.stringify(errs));
    await dump("save-blocked");
    return { ok: false, errs };
  }
  const sv = await clickText("保存草稿");
  log("== 保存草稿:", JSON.stringify(sv));
  await sleep(9000);
  const saved = await c.eval(`(() => {
    const t=document.body.innerText||'';
    const m=t.match(/草稿最后保存于\\s*([\\d\\-: ]+)/);
    return {hit:/保存成功|已保存/.test(t), at:m?m[1].trim():null, url:location.href};
  })()`);
  log("== 保存结果:", JSON.stringify(saved));
  await dump("save");
  return saved;
}

// ---------- 阶段 6：提交上架 ----------
async function stagePublish() {
  const s = await dump("publish-before");
  // 找所有疑似「发布 / 提交」按钮
  const btns = s.btns || [];
  const cand = btns.filter((b) => /发布|提交|上架/.test(b.t));
  log("== 疑似发布按钮:", JSON.stringify(cand, null, 1));

  if (dry) { log("== [dry] 只看不点"); return; }

  const order = ["提交上架", "发布商品", "立即发布", "提交并上架", "发布", "提交"];
  let picked = null;
  for (const label of order) {
    const hit = btns.find((b) => b.t === label && !b.d);
    if (hit) { picked = hit; break; }
  }
  if (!picked) {
    // 退化：包含「发布」且不含「草稿」的
    picked = btns.find((b) => /发布/.test(b.t) && !/草稿/.test(b.t) && !b.d) || null;
  }
  if (!picked) { log("[!] 没找到可点的发布按钮"); return; }

  log("== 点发布:", picked.t);
  const r = await clickText(picked.t);
  log("== 点击结果:", JSON.stringify(r));
  await sleep(6000);

  // 可能有二次确认弹窗
  for (const label of ["确认发布", "确定", "确认", "我知道了"]) {
    const ok = await clickText(label);
    if (ok.found && ok.clicked) { log("== 二次确认:", label); await sleep(4000); break; }
  }

  for (let i = 1; i <= 30; i++) {
    await sleep(3000);
    const st = await c.eval(`(() => {
      const t=document.body.innerText||'';
      return {url:location.href, ok:/发布成功|提交成功|上架成功/.test(t),
              audit:/审核中/.test(t), err:/发布失败|提交失败|不通过/.test(t),
              len:t.length, tail:t.slice(-800)};
    })()`);
    log(`   ${i*3}s url=${st.url.slice(0,60)} ok=${st.ok} audit=${st.audit} err=${st.err}`);
    if (st.ok || st.err) { log("== 结果:", JSON.stringify(st).slice(0, 1200)); break; }
    if (i === 6) { await dump("publish-mid"); }
  }
  await dump("publish-after");
}

// ---------- 主流程 ----------
log(`== 计划 pid=${pid} | ${String(plan.title).slice(0, 44)}`);
log(`   主图 ${plan.main.length} / 详情 ${plan.detail.length} / SKU ${plan.skus.length} | stage=${stage}`);

if (stage === "probe" || stage === "all") await stageProbe();
if (stage === "cat" || stage === "all") await stageCat();
if (stage === "attrs" || stage === "all") await stageAttrs();
if (stage === "spec" || stage === "all") await stageSpec();
if (stage === "save" || stage === "all") await stageSave();
if (stage === "publish" || stage === "all") await stagePublish();

log("DONE");
c.close();
process.exit(0);
