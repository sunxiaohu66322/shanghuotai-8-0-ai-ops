// 探针：新开标签页打开「新建商品(带cid)」，hook 全部 XHR/fetch，捞出真实接口清单。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const CID = process.argv[2] || "1000004588";

const p = await CDP.newPage("about:blank");
console.log("new tab id=", p.target.id);

await p.hookXhr();
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?cid=${CID}&entrance=create`, { settle: 9000 });

const info = await p.eval(`({ url: location.href, title: document.title, len: (document.body?.innerText||'').length })`);
console.log("PAGE:", JSON.stringify(info));

const cap = await p.drainXhr();
const slim = cap.map((c) => ({
  m: c.m || "GET",
  url: c.url,
  status: c.status,
  bytes: (c.body || "").length,
  head: (c.body || "").slice(0, 200),
}));
console.log("CAPTURED:", slim.length);
for (const s of slim) console.log(`${s.status} ${s.m} ${s.url.slice(0, 150)}  [${s.bytes}]`);

await saveJson(OUT + "/probe-create-cid.json", { page: info, captured: slim, raw: cap });
await p.shot(OUT + "/probe-create-cid.png", { fullPage: true });
console.log("saved");
p.close();
process.exit(0);
