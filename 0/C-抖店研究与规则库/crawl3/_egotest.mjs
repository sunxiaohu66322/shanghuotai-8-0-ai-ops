import fs from "node:fs";
const out = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3/_egotest.txt";
const ts = () => console.log(typeof taskSpace);
let r = "taskSpace=" + (typeof taskSpace);
try { const s = await taskSpace("抖店批量上货"); r += " | space=" + (s ? "ok" : "null"); const p = s.page("p1"); r += " | page=" + (p ? "ok" : "null") + " | url=" + String(p && p.url ? p.url() : "").slice(0,60); } catch(e) { r += " | ERR=" + String(e).slice(0,100); }
fs.writeFileSync(out, r);
