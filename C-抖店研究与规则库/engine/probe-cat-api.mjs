// 在已登录页面上下文里直接试类目相关接口（内部接口走 cookie，不需要签名）。
// 依据：/product/tproduct/list 是真实商品列表接口（GET + query，无签名）。
import { CDP, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 8000 });

const CANDS = [
  "/product/category/getCategoryList",
  "/product/category/getCategory",
  "/product/category/list",
  "/product/category/tree",
  "/product/category/getTree",
  "/product/category/getCascadeCategories",
  "/product/category/getCategoryTree",
  "/product/tcategory/list",
  "/product/category/getCategoryInfo",
  "/product/category/getCategories",
  "/product/cat/list",
  "/product/category/getCateList",
  "/product/category/getAllCategory",
  "/product/category/search",
];

const res = await p.eval(`(async () => {
  const out = [];
  for (const u of ${JSON.stringify(CANDS)}) {
    try {
      const r = await fetch(u, { credentials: 'include', headers: { 'accept': 'application/json' } });
      const t = await r.text();
      out.push({ u, status: r.status, ct: r.headers.get('content-type'), len: t.length, head: t.slice(0, 300) });
    } catch (e) { out.push({ u, err: String(e).slice(0, 120) }); }
  }
  return out;
})()`);

for (const r of res) {
  console.log(`${r.status || "-"} ${r.u} len=${r.len || 0} ${r.head ? r.head.slice(0, 160).replace(/\n/g, " ") : r.err || ""}`);
}
await saveJson(OUT + "/cat-api-try.json", res);
p.close();
process.exit(0);
