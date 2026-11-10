const r = await fetch("http://127.0.0.1:9224/json/list");
const tabs = await r.json();
for (const t of tabs) { if (t.type==="page") console.log(t.id.slice(0,8), "|", (t.title||"").slice(0,40), "|", (t.url||"").slice(0,110)); }
