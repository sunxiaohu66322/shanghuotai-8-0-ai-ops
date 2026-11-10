// 定契约：在已登录页面里直接调三个关键接口，确认最小请求体。
// 1) predictCategoryN 传什么能给 cid   2) getSchema 传什么能出表单   3) tproduct/list 怎么读草稿
import { CDP, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const CID = "1000004588";

const p = await CDP.newPage("about:blank");
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=3783077346274312845&cid=${CID}&entrance=edit`, { settle: 12000 });

const r = await p.eval(`(async () => {
  const out = {};
  const post = async (u, body) => {
    const res = await fetch(u, { method:'POST', credentials:'include',
      headers:{'content-type':'application/json'}, body: JSON.stringify(body) });
    return { status: res.status, body: (await res.text()) };
  };
  const get = async (u) => {
    const res = await fetch(u, { credentials:'include', headers:{'accept':'application/json'} });
    return { status: res.status, body: (await res.text()) };
  };

  // 1) predictCategoryN：只给标题
  out.predict_title_only = await post('/product/tproduct/predictCategoryN?appid=1', { title: '实木床架 双人床 1.8米 卧室家具 现代简约' });
  // 2) getSchema：只给 category_id
  out.schema_min = await post('/product/tproduct/getSchema?appid=1', { category_id: 1000004588 });
  // 3) 草稿箱列表
  out.draft_list = await get('/product/tproduct/list?page=0&pageSize=10&draft_status=1&tab=draft');
  out.draft_list2 = await get('/product/tproduct/list?page=0&pageSize=10&draft_status=0&tab=all');
  return out;
})()`);

for (const [k, v] of Object.entries(r)) {
  console.log(`\n=== ${k}  status=${v.status} len=${v.body.length}`);
  console.log(v.body.slice(0, 900));
}

// 把 predict 的候选类目整理出来
try {
  const pj = JSON.parse(r.predict_title_only.body);
  const cands = pj?.data?.candidate_category_details || [];
  console.log("\nCANDIDATES:", cands.length);
  for (const c of cands) console.log(`  [${c.status}] ${c.category_full_name}  leaf=${c.schema_value?.category_leaf_id}  fill=${c.is_fill_category_tree}`);
} catch (e) { console.log("parse fail", e.message); }

await saveJson(OUT + "/api-contract-min.json", r);
console.log("\nsaved api-contract-min.json");
p.close();
process.exit(0);
