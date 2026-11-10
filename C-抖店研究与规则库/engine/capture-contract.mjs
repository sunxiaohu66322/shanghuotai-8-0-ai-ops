// 抓「新建商品」这条链路的完整请求契约（含 POST body）：
// predictCategoryN（类目预测）、getSchema（表单定义）、tproduct/list（商品/草稿列表）。
import { CDP, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const PID = process.argv[2] || "3783077346274312845";
const CID = process.argv[3] || "1000004588";
const WANT = /predictCategoryN|getSchema|refetchSchema|tproduct\/list/;

const p = await CDP.newPage("about:blank");
await p.send("Network.enable", { maxPostDataSize: 10 * 1024 * 1024 }).catch(() => {});
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?product_id=${PID}&cid=${CID}&entrance=edit`, { settle: 15000 });

const pairs = await p.netPairs(WANT);
console.log("PAIRS:", pairs.length);
const saved = [];
for (const x of pairs) {
  const u = x.url.split("?")[0];
  console.log(`\n### ${x.method} ${u}`);
  console.log("   query:", (x.url.split("?")[1] || "").replace(/__token=[^&]*&?/g, "").slice(0, 260));
  console.log("   postData:", (x.postData || "(none)").slice(0, 1200));
  console.log("   resp:", (x.resp || "").length, "bytes");
  saved.push({ method: x.method, url: x.url, postData: x.postData, resp: (x.resp || "").slice(0, 1500000) });
}
await saveJson(OUT + "/contract-create.json", saved);
console.log("\nsaved contract-create.json");
p.close();
process.exit(0);
