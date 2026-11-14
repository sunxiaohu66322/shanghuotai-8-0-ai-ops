// 只读：查建品页当前状态（不点击、不上传）
import { CDP, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const tabs = await (await fetch("http://127.0.0.1:9224/json/list")).json();
const pages = tabs.filter(x => x.type === "page");
console.log("jinritemai 页:");
pages.filter(p => (p.url || "").includes("jinritemai")).forEach(p => console.log("  ", String(p.id).slice(0, 8), p.url.slice(0, 110)));

const cand = pages.filter(p => (p.url || "").includes("ffa/g/"));
for (const t of cand) {
  const c = await CDP.connect(t.webSocketDebuggerUrl);
  for (const m of ["Runtime.enable", "Page.enable"]) await c.send(m).catch(() => {});
  const s = await c.eval(`(() => {
    const t=(document.body.innerText||'').replace(/\\n{2,}/g,'\\n');
    return { url: location.href.slice(0,140), len: t.length,
             uploaded:(t.match(/已上传 (\\d+)/)||[0,0])[1],
             gen:/生成中|识别中|正在生成|请稍候/.test(t),
             txt: t.slice(0, 1800) };
  })()`).catch(e => ({ err: String(e) }));
  console.log("\n=== tab " + String(t.id).slice(0, 8) + " ===");
  console.log(JSON.stringify({ url: s.url, len: s.len, uploaded: s.uploaded, gen: s.gen }));
  console.log(s.txt || s.err);
  await saveJson(OUT + "/state-" + String(t.id).slice(0, 8) + ".json", s).catch(() => {});
  c.close();
}
