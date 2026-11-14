// 刷新草稿箱核实最新保存时间
import { CDP, sleep, saveJson } from "./cdp.mjs";
const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";
const c = await CDP.page({ urlIncludes: "ffa/g/draft" });
await c.send("Page.enable").catch(()=>{});
await c.send("Page.reload",{ignoreCache:true});
await sleep(4000);
await c.waitReady(5000).catch(()=>{});
await sleep(3000);

const row = await c.eval(`(() => {
  const vis=e=>e.offsetParent!==null;
  const rows=[...document.querySelectorAll('tr,[class*=table-row],li,div[class*=item]')]
    .filter(e=>vis(e)&&(e.innerText||'').includes('3851010940816458176'));
  if(!rows.length) return {found:false};
  const t=(rows[0].innerText||'').replace(/\\n{2,}/g,'\\n').trim();
  return {found:true, t};
})()`);
console.log("刷新后草稿箱:", JSON.stringify(row));
await c.shot(OUT+"/spec10-draftbox2.png").catch(()=>{});
await saveJson(OUT+"/spec10.json", row);
c.close();