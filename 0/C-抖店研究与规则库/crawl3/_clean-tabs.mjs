const r = await fetch("http://127.0.0.1:9224/json/list");
const tabs = await r.json();
const kill = tabs.filter(t => t.type==="page" && /fxg\.jinritemai\.com\/ffa\/g\/(create|list)/.test(t.url||""));
console.log("要关闭的抖店页:", kill.length);
for (const t of kill) {
  try { await fetch("http://127.0.0.1:9224/json/close/" + t.id); console.log("  closed", t.id.slice(0,8)); } catch(e) { console.log("  fail", e.message); }
}
await new Promise(r=>setTimeout(r,1500));
const after = await (await fetch("http://127.0.0.1:9224/json/list")).json();
console.log("剩余 page:", after.filter(t=>t.type==="page").length);
