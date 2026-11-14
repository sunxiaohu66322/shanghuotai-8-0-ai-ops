const r = await fetch("http://127.0.0.1:9224/json/list").catch(()=>null);
if (!r) { console.log("CDP 不可达"); } else {
  const tabs = await r.json();
  const pages = tabs.filter(t=>t.type==="page");
  console.log("pages:", pages.length);
  for (const t of pages.slice(0,12)) console.log("  ", (t.url||"").slice(0,90));
}
