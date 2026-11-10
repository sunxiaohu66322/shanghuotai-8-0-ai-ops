// 读取店铺商品（含草稿）：直接调 /product/tproduct/list，一次拿全量。
// 单位：接口返回的价格全部是「分」。
import { CDP, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/engine/out";
const PAGE_SIZE = Number(process.argv[2] || 50);
const MAX_PAGES = Number(process.argv[3] || 60);

const p = await CDP.newPage("about:blank");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { settle: 9000 });

const all = [];
let total = 0;
for (let page = 0; page < MAX_PAGES; page++) {
  const r = await p.eval(`(async () => {
    const u = '/product/tproduct/list?appid=1&page=${page}&pageSize=${PAGE_SIZE}&draft_status=0&tab=all';
    const res = await fetch(u, { credentials:'include', headers:{accept:'application/json'} });
    const t = await res.text();
    try { return { status: res.status, json: JSON.parse(t) }; } catch(e) { return { status: res.status, raw: t.slice(0,300) }; }
  })()`);
  if (r.status !== 200 || !r.json) { console.log("page", page, "fail", JSON.stringify(r).slice(0, 200)); break; }
  const items = r.json.data || [];
  total = r.json.total ?? total;
  all.push(...items);
  if (page === 0) console.log("total=", total, "first_page=", items.length);
  if (all.length >= total || items.length < PAGE_SIZE) { console.log("done at page", page); break; }
}

// 归一化：只留自动化要用的字段，价格转元并保留分
const slim = all.map((x) => ({
  product_id: String(x.product_id || ""),
  name: x.name || "",
  status: x.status,
  draft_status: x.draft_status,
  check_status: x.check_status,
  reason: x.reason || "",
  category: x.category_name || x.shop_category_path || x.category || "",
  leaf_cid: x.category_leaf_id || null,
  first_cid: x.first_cid || null,
  second_cid: x.second_cid || null,
  third_cid: x.third_cid || null,
  price_cents: { sell: x.discount_price, market: x.market_price, low: x.price_lower, high: x.price_higher },
  price_yuan: { sell: (x.discount_price || 0) / 100, market: (x.market_price || 0) / 100 },
  stock: x.stock_num,
  sold: x.sell_num,
  img: x.img || "",
  create_time: x.create_time,
  update_time: x.update_time,
  skus: (x.skus || []).map((s) => ({
    sku_id: String(s.sku_id || s.id || ""),
    name: s.name || s.spec_name || "",
    price_cents: s.price ?? s.discount_price ?? 0,
    market_cents: s.market_price ?? 0,
    stock: s.stock_num ?? s.stock ?? 0,
  })),
}));

const stat = slim.reduce((a, x) => {
  const k = x.draft_status === 1 ? "draft" : `status_${x.status}`;
  a[k] = (a[k] || 0) + 1;
  return a;
}, {});

await saveJson(OUT + "/store.json", { fetched_at: new Date().toISOString(), total, count: slim.length, stat, items: slim });
console.log("SAVED", slim.length, "items  stat=", JSON.stringify(stat));

// 顺手喂给价格计算器
console.log("\n下一步：python price_guard.py out/store.json");
p.close();
process.exit(0);
