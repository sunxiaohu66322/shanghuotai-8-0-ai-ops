// 把「资质中心 · 已开通类目」整表抽出来 → 结构化 JSON
import { CDP } from "./cdp.mjs";
import fs from "node:fs/promises";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const t = tabs.filter(x => x.type === "page").find(x => (x.url || "").includes("jinritemai"));
const c = await CDP.connect(t.webSocketDebuggerUrl);
await c.send("Runtime.enable").catch(() => {});

const rows = await c.eval(`(() => {
  const out = [];
  for (const tr of document.querySelectorAll('tr')) {
    const tds = [...tr.querySelectorAll('td')].map(td => (td.innerText||'').trim());
    if (!tds.length) continue;
    out.push(tds);
  }
  return out;
})()`);

// 合并单元格：一级为空则沿用上一行
const recs = [];
let l1 = "";
for (const r of rows) {
  const a = (r[0] || "").trim();
  const b = (r[1] || "").trim();
  const d = (r[2] || "").trim();
  const e = (r[3] || "").trim();
  if (a) l1 = a;
  if (!l1) continue;
  // d 里可能用、分隔多个末级
  const leaves = d ? d.split(/[、,，]/).map(s => s.trim()).filter(Boolean) : [];
  recs.push({ l1: l1, l2: b, l3: d, l4: e, leaves });
}

const l1set = [...new Set(recs.map(r => r.l1))];
const dec = (s) => decodeURIComponent(escape(s));
await fs.writeFile(OUT + "/qual-table.json", JSON.stringify({ count: recs.length, l1: l1set, rows: recs }, null, 1), "utf8");

console.log("行数:", recs.length);
console.log("已开通一级类目（" + l1set.length + "）:");
console.log("  " + l1set.join(" / "));

// 用户关心的三类
for (const want of ["家具", "户外装备", "家装灯饰光源", "居家日用", "运动户外服装", "室内运动", "运动休闲用品"]) {
  const hit = l1set.includes(want);
  console.log(`  [${hit ? "有" : "无"}] ${want}`);
}

// 家具 & 户外 明细
for (const key of ["家具", "户外装备", "家装灯饰光源"]) {
  const rs = recs.filter(r => r.l1 === key);
  if (!rs.length) { console.log(`\n== ${key} == 未开通`); continue; }
  console.log(`\n== ${key} == 共 ${rs.length} 行`);
  for (const r of rs.slice(0, 30)) console.log(`   ${r.l2} > ${r.l3}${r.l4 ? " > " + r.l4 : ""}`);
}
c.close();
