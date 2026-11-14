import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
await fs.mkdir(OUT, { recursive: true });

const task = await taskSpace("抖店后台结构采集");
console.log("spaceId=", task.spaceId, "ownership=", task.ownership, "name=", task.name);

const page = task.page("p1");

const tryGoto = async (u) => {
  try {
    await page.goto(u);
    await page.waitForTimeout(7000);
    return true;
  } catch (e) {
    console.log("goto-fail", u, String(e).slice(0, 160));
    return false;
  }
};

await tryGoto("https://fxg.jinritemai.com/ffa/mshop/homepage/index");
let url = await page.url();
if (!url.includes("jinritemai")) {
  await tryGoto("https://fxg.jinritemai.com/");
  url = await page.url();
}

const title = await page.title();
console.log("URL:", url);
console.log("TITLE:", title);

const probe = await page.evaluate(() => {
  const txt = (document.body?.innerText || "").replace(/\n{2,}/g, "\n").slice(0, 1200);
  const anchors = [...document.querySelectorAll("a[href]")]
    .slice(0, 80)
    .map((a) => ({ t: (a.textContent || "").trim().slice(0, 24), h: a.getAttribute("href") }))
    .filter((x) => x.h && !x.h.startsWith("javascript"));
  return {
    bodyText: txt,
    anchorCount: anchors.length,
    anchors,
    hasLoginForm: !!document.querySelector('input[type="password"]'),
    lsKeys: Object.keys(localStorage).slice(0, 40),
  };
});

console.log(JSON.stringify(probe, null, 1).slice(0, 6000));
await fs.writeFile(OUT + "/round-01.json", JSON.stringify({ url, title, probe }, null, 2), "utf8");
console.log("saved round-01.json");
