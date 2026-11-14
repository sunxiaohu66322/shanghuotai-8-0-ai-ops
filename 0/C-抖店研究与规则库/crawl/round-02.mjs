import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
await fs.mkdir(OUT, { recursive: true });

const task = await taskSpace("抖店后台结构采集");
const page = task.page("p1");

// 1) 进商品管理
await page.goto("https://fxg.jinritemai.com/ffa/g/list");
await page.waitForTimeout(7000);

// 2) 展开导航（找不到就算了，不报错）
for (const t of ["展开导航", "展开", "全部菜单"]) {
  try {
    await page.click(`text="${t}"`, { timeout: 2500 });
    await page.waitForTimeout(1500);
    console.log("clicked:", t);
    break;
  } catch (e) {
    /* next */
  }
}
await page.waitForTimeout(1500);

const dump = await page.evaluate(() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  // 所有可见导航链接（含分组顺序）
  const navLinks = [...document.querySelectorAll("a[href]")]
    .map((a) => {
      const r = a.getBoundingClientRect();
      return {
        text: norm(a.textContent),
        href: a.getAttribute("href"),
        top: Math.round(r.top),
        left: Math.round(r.left),
        w: Math.round(r.width),
      };
    })
    .filter(
      (x) =>
        x.text &&
        x.w > 0 &&
        x.href &&
        !/^javascript/i.test(x.href) &&
        !/^(https?:)?\/\/(?!fxg)/.test(x.href)
    );

  // 去重（同一 href 出现两次取第一条）
  const seen = new Set();
  const uniq = [];
  for (const l of navLinks) {
    const k = l.href + "|" + l.text;
    if (seen.has(k)) continue;
    seen.add(k);
    uniq.push(l);
  }
  uniq.sort((a, b) => a.left - b.left || a.top - b.top);

  // 表头
  const tableHeads = [...document.querySelectorAll("th, [role='columnheader']")]
    .map((th) => norm(th.textContent))
    .filter((t) => t && t.length < 30);

  // 按钮文案
  const buttons = [
    ...new Set(
      [...document.querySelectorAll("button, [role='button'], .auxo-btn")]
        .map((b) => norm(b.textContent))
        .filter((t) => t && t.length <= 14)
    ),
  ].slice(0, 120);

  // 页签
  const tabs = [
    ...new Set(
      [...document.querySelectorAll("[role='tab'], .auxo-tabs-tab")]
        .map((b) => norm(b.textContent))
        .filter(Boolean)
    ),
  ];

  // 已发起的接口（去 query）
  const apis = [
    ...new Set(
      performance
        .getEntriesByType("resource")
        .map((e) => e.name)
        .filter((n) => /\/(api|pc\/api)\//i.test(n))
        .map((n) => n.replace(/^https?:\/\/[^/]+/, "").split("?")[0])
    ),
  ];

  return {
    url: location.href,
    title: document.title,
    breadcrumb: norm(
      [...document.querySelectorAll("[class*='breadcrumb'] , [class*='crumb']")]
        .map((e) => e.textContent)
        .join(" ")
    ).slice(0, 200),
    navLinks: uniq,
    tableHeads: [...new Set(tableHeads)],
    buttons,
    tabs,
    apis: apis.slice(0, 120),
  };
});

await fs.writeFile(OUT + "/round-02-goodlist.json", JSON.stringify(dump, null, 2), "utf8");

console.log("URL:", dump.url, "| TITLE:", dump.title);
console.log("navLinks:", dump.navLinks.length, "| tableHeads:", dump.tableHeads.length, "| apis:", dump.apis.length);
console.log("--- NAV (left,top,text,href) ---");
for (const l of dump.navLinks) console.log(`${l.left},${l.top}\t${l.text}\t${l.href}`);
console.log("--- TABLE HEADS ---");
console.log(dump.tableHeads.join(" | "));
console.log("--- TABS ---");
console.log(dump.tabs.join(" | "));
console.log("--- BUTTONS ---");
console.log(dump.buttons.join(" | "));
console.log("--- APIS ---");
console.log(dump.apis.join("\n"));
