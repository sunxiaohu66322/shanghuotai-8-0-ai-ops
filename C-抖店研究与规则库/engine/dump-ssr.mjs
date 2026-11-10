// 导出页面 SSR 初始化数据（含类目树的可能性最高），并列全部请求 URL（不过滤域名）。
import { CDP, saveJson, sleep } from "./cdp.mjs";
import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 10000 });
await p.hookXhr();
await p.click('text="查询"');
await sleep(5000);

const cap = await p.drainXhr();
console.log("REQ:", cap.length);
for (const c of cap) console.log(`${c.status} ${(c.url||"").slice(0,180)}`);

// 导出 SSR 里所有含 "cid" 的路径（递归找类目数组）
const found = await p.eval(`(() => {
  const root = window.__SSR_CONFIG_ECOM_FXG_ADMIN;
  if (!root) return { err: 'no ssr' };
  const hits = [];
  const seen = new Set();
  const walk = (n, path, depth) => {
    if (!n || typeof n !== 'object' || depth > 8 || seen.has(n)) return;
    seen.add(n);
    if (Array.isArray(n)) {
      if (n.length && n[0] && typeof n[0] === 'object' && !Array.isArray(n[0])) {
        const k0 = Object.keys(n[0]);
        if (k0.some(k => /^(cid|id)$/i.test(k)) && k0.some(k => /name|title/i.test(k))) {
          hits.push({ path, len: n.length, keys: k0.slice(0, 12), sample: JSON.stringify(n.slice(0,3)).slice(0, 400) });
        }
      }
      n.slice(0, 60).forEach((v, i) => walk(v, path + '[' + i + ']', depth + 1));
      return;
    }
    for (const k of Object.keys(n).slice(0, 200)) {
      if (/cat|cate|classify|property|prop|attr/i.test(k)) {
        try { hits.push({ path: path + '.' + k, len: Array.isArray(n[k]) ? n[k].length : -1, keys: (n[k] && typeof n[k]==='object') ? Object.keys(n[k]).slice(0,12) : [], sample: JSON.stringify(n[k]).slice(0,300) }); } catch(e){}
      }
      walk(n[k], path + '.' + k, depth + 1);
    }
  };
  walk(root, '$', 0);
  return { total: hits.length, hits: hits.slice(0, 40), size: JSON.stringify(root).length };
})()`);
console.log("SSR HITS:", JSON.stringify(found, null, 1).slice(0, 6000));

const raw = await p.eval("JSON.stringify(window.__SSR_CONFIG_ECOM_FXG_ADMIN)");
if (raw) {
  await fs.writeFile(OUT + "/ssr-config.json", raw, "utf8");
  console.log("ssr bytes:", raw.length);
}
await saveJson(OUT + "/xhr2.json", cap.map(c => ({ url: c.url, status: c.status, bytes: (c.body||"").length })));
p.close();
process.exit(0);
