// 抓页面**自己**发的 categoryOptionsN 响应（不在页面里 fetch，避免挂死）
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const R = {};

const c = await CDP.page({ urlIncludes: "fxg.jinritemai.com/ffa/g/list" });
await c.waitReady(2000);
console.log("== 连上", c.target.url);

// 先 reload，让它自己重新发请求
await c.send("Network.enable");
await c.send("Page.reload", { ignoreCache: false });
await sleep(11000);

const wants = [/categoryOptionsN/, /\/product\/tproduct\/list/, /category/i];
const reqs = c.netRequests(/categoryOptionsN|\/tproduct\/list|categor/i);
console.log("== 命中请求 " + reqs.length);
for (const r of reqs) console.log("   " + r.method + " " + r.url.split("?")[0]);

for (const r of reqs.slice(0, 6)) {
  const body = await c.bodyOf(r.reqId);
  const name = r.url.split("?")[0].split("/").pop();
  R["body_" + name] = { url: r.url, len: body ? body.length : 0, head: body ? body.slice(0, 500) : null };
  if (body && body.length > 500) {
    await saveJson(OUT + "/resp-" + name + ".json", { url: r.url, body });
  }
  console.log("== " + name + " len=" + (body ? body.length : 0));
}

await saveJson(OUT + "/probe-cat2.json", R);
console.log("== done");
c.close();
process.exit(0);
