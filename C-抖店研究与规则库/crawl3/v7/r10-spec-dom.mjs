// 第 10 轮：搞清「多维规格」的真实 DOM —— 类型下拉 / 规格值输入 / 添加规格类型 / 自动生成 SKU 行
const fs = await import("node:fs/promises");
const ROOT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

const task = await taskSpace(5);
const page = task.page("p1");
log("空间", task.spaceId, "| url:", (await page.url()).slice(0, 80));

/** 把「规格」整个区块的结构全 dump 出来（不做过滤，宁多不漏） */
const dump = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };

  // 定位「添加规格类型（1/3）」或「添加规格图」，作为规格区锚点
  const anchorTxts = ["添加规格类型", "添加规格图", "批量上传规格图", "价格与库存"];
  let anchor = null, anchorName = null;
  for (const t of anchorTxts) {
    const els = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").includes(t));
    if (els.length) { anchor = els[0]; anchorName = t; break; }
  }
  if (!anchor) {
    // 兜底：SKU 表头
    const th = [...document.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0 && (e.innerText || "").trim() === "SKU状态");
    anchor = th[0] || null; anchorName = "SKU状态";
  }
  if (!anchor) return { found: false, bodyHead: (document.body.innerText || "").slice(0, 600) };

  // 从锚点往上找规格区块：含「添加规格类型」且文本量适中
  let block = anchor;
  for (let i = 0; i < 10 && block.parentElement; i++) {
    block = block.parentElement;
    const t = block.innerText || "";
    if (/添加规格类型|添加规格图/.test(t) && /价格与库存|SKU状态|规格/.test(t) && t.length > 60 && t.length < 4000) break;
  }

  // 1) 所有 select（类型下拉）
  const selects = [...block.querySelectorAll("[class*=select]")].filter(vv).map((e) => ({
    ...box(e),
    cls: String(e.className).slice(0, 70),
    text: (e.innerText || "").trim().slice(0, 40),
  })).filter((o) => o.w > 40 && o.h > 20 && o.h < 60);

  // 2) 所有 input / textarea（规格值、价格、库存）
  const inputs = [...block.querySelectorAll("input,textarea")].filter(vv).map((e) => ({
    ...box(e),
    tag: e.tagName,
    ph: e.placeholder || "",
    val: (e.value || "").slice(0, 20),
    type: e.type || "",
    ro: e.readOnly,
    cls: String(e.className || "").slice(0, 40),
  }));

  // 3) 所有疑似开关：不只 class*=switch，把 aria/role/checked 全扫
  const switches = [...block.querySelectorAll("*")].filter((e) => {
    if (!vv(e)) return false;
    const c = String(e.className || "");
    return /switch|toggle|checkbox|radio/i.test(c + (e.getAttribute("role") || "") + (e.getAttribute("aria-checked") || ""));
  }).map((e) => ({
    ...box(e),
    tag: e.tagName,
    role: e.getAttribute("role") || "",
    aria: e.getAttribute("aria-checked") || "",
    cls: String(e.className || "").slice(0, 60),
    text: (e.innerText || "").trim().slice(0, 20),
  })).filter((o) => o.w > 6 && o.h > 6 && o.h < 60);

  // 4) 按钮类（加号/添加/删除×）
  const btns = [...block.querySelectorAll("button,[role=button],svg,i,span")].filter(vv).map((e) => {
    const r = e.getBoundingClientRect();
    const tx = (e.innerText || "").trim();
    const tz = e.getAttribute("title") || e.getAttribute("aria-label") || "";
    return { ...box(e), tag: e.tagName, text: (tx || tz).slice(0, 24), cls: String(e.className || "").slice(0, 40) };
  }).filter((o) => o.w >= 8 && o.w <= 200 && o.h >= 8 && o.h <= 44 && (o.text || /icon|btn/i.test(o.cls)));

  // 5) SKU 表格行
  const rows = [...block.querySelectorAll("tr,[class*=table-row],[class*=row-vertical]")].filter(vv).map((e) => ({
    ...box(e),
    text: (e.innerText || "").replace(/\n/g, " | ").slice(0, 90),
  })).filter((o) => o.h > 20 && o.h < 200);

  // 6) 区块内全部短文本（带坐标，用来看结构顺序）
  const txts = [...block.querySelectorAll("*")].filter((e) => vv(e) && e.children.length === 0)
    .map((e) => ({ ...box(e), t: (e.innerText || "").trim() }))
    .filter((o) => o.t && o.t.length <= 24)
    .map((o) => `${o.t}@${o.x},${o.y}`);

  return { found: true, anchorName, block: box(block), selects, inputs, switches, btns, rows, txts: txts.slice(0, 80) };
});

log("锚点:", dump.anchorName, "| 区块:", JSON.stringify(dump.block));
log("select:", JSON.stringify(dump.selects, null, 1));
log("input:", JSON.stringify(dump.inputs, null, 1));
log("switch:", JSON.stringify(dump.switches, null, 1));
log("btn:", JSON.stringify((dump.btns || []).slice(0, 25), null, 1));
log("rows:", JSON.stringify(dump.rows, null, 1));
log("txts:", JSON.stringify(dump.txts, null, 1));

await fs.writeFile(`${ROOT}/v7/r10-spec-dom.json`, JSON.stringify(dump, null, 2), "utf8");

// 顺手把「规格类型下拉」点开，看菜单位里有哪些类型可选
const opened = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const cur = [...document.querySelectorAll("[class*=select-selection-item]")].filter(vv)
    .map((e) => ({ e, ...box(e), t: (e.innerText || "").trim() }))
    .find((o) => o.w > 60 && o.w < 220 && o.h > 20 && o.h < 45);
  if (!cur) return { found: false };
  cur.e.scrollIntoView({ block: "center" });
  const sel = cur.e.closest("[class*=select]") || cur.e.parentElement;
  sel.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
  sel.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
  sel.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  return { found: true, cur: cur.t, selCls: String(sel.className).slice(0, 60), at: { x: cur.x, y: cur.y, w: cur.w, h: cur.h } };
});
log("点开类型下拉:", JSON.stringify(opened));
await page.waitForTimeout(1200);

const menu = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const box = (e) => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  const pops = [...document.querySelectorAll("[class*=dropdown],[class*=popup],[class*=menu],[class*=options],[role=listbox]")]
    .filter(vv).map((e) => ({
      ...box(e),
      cls: String(e.className).slice(0, 50),
      items: [...e.querySelectorAll("[class*=option],[role=option],li,[class*=item]")].filter(vv)
        .map((o) => (o.innerText || "").trim()).filter(Boolean).slice(0, 60),
    })).filter((o) => o.h > 30 && o.w > 60 && o.items.length);
  return pops.slice(0, 6);
});
log("类型菜单:", JSON.stringify(menu, null, 1));
await fs.writeFile(`${ROOT}/v7/r10-type-menu.json`, JSON.stringify({ opened, menu }, null, 2), "utf8");

await page.screenshot({ path: `${ROOT}/v7/shot-r10-spec.png` });
log("done");
