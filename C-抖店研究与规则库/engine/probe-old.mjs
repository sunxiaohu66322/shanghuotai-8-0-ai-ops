// 探索：从哪里能拿到「类目树」。
// 事实依据：新版 /ffa/g/create?cid= 依旧只给 AI 创建页（无类目选择器）。
// 因此走「返回旧版」，并同时 hook XHR，捞类目相关接口。
import { CDP, saveJson, sleep } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl2";
const CID = process.argv[2] || "1000004588";

const p = await CDP.newPage("about:blank");
await p.goto(`https://fxg.jinritemai.com/ffa/g/create?cid=${CID}&entrance=create`, { settle: 8000 });

console.log("STEP1 url=", await p.eval("location.href"));

// 找「返回旧版」
const back = await p.click('text="返回旧版"');
console.log("click 返回旧版 ->", JSON.stringify(back));
await sleep(9000);
console.log("STEP2 url=", await p.eval("location.href"));

await p.hookXhr(); // 导航后再 hook，才有意义

const snap = await p.eval(`(() => {
  const vis = (e) => e.offsetParent !== null;
  const labels = [...document.querySelectorAll('label,.auxo-form-item-label,div')]
    .filter(vis).map(e => (e.innerText||'').trim())
    .filter(t => t && t.length < 18 && /类目|标题|主图|规格|价格|库存|运费|重量|售后/.test(t));
  const inputs = [...document.querySelectorAll('input,textarea')].filter(vis).map(e => ({
    tag: e.tagName, type: e.type, ph: e.placeholder || '', val: (e.value||'').slice(0,40), cls: (e.className||'').slice(0,40)
  }));
  return {
    url: location.href,
    textLen: (document.body?.innerText||'').length,
    text: (document.body?.innerText||'').replace(/\\n{2,}/g,'\\n').slice(0, 2500),
    labels: [...new Set(labels)].slice(0, 80),
    inputCount: inputs.length,
    inputs: inputs.slice(0, 60),
    hasCategoryTrigger: [...document.querySelectorAll('*')].filter(vis)
      .some(e => /^(选择类目|商品类目|修改类目|选择分类)$/.test((e.innerText||'').trim())),
  };
})()`);

console.log("TEXT LEN:", snap.textLen, "inputs:", snap.inputCount, "catTrigger:", snap.hasCategoryTrigger);
console.log("URL:", snap.url);
console.log("--- labels ---");
console.log(snap.labels.join(" | ").slice(0, 1200));

await p.shot(OUT + "/old-create.png", { fullPage: true });
await saveJson(OUT + "/old-create.json", snap);
console.log("saved old-create");
p.close();
process.exit(0);
