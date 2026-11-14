const t = await taskSpace("抖店批量上货");
const p = t.page("p1");
await p.goto("https://fxg.jinritemai.com/ffa/g/list", { waitUntil: "domcontentloaded" });
await new Promise(r=>setTimeout(r,9000));
const rows = await p.evaluate(() => {
  return [...document.querySelectorAll("tr")].filter(tr=>/ID[：:]\s*\d{10,}/.test(tr.innerText||""))
    .map(tr => (tr.innerText||"").replace(/\s+/g," ").trim().slice(0,140)).slice(0,25);
});
console.log("行数:", rows.length);
for (const r of rows) console.log(" ·", r);
