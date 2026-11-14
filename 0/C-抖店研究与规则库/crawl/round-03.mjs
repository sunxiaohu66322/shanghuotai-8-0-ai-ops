import fs from "node:fs/promises";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl";
const task = await taskSpace("抖店后台结构采集");
const page = task.page("p1");

await page.goto("https://fxg.jinritemai.com/ffa/g/create");
await page.waitForTimeout(9000);

const dump = await page.evaluate(() => {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  // 表单项：label + 必填星号
  const labels = [...document.querySelectorAll("label")]
    .filter(vis)
    .map((l) => ({
      text: norm(l.textContent).slice(0, 40),
      required: /[*＊]/.test(l.textContent || ""),
      for: l.getAttribute("for"),
    }))
    .filter((x) => x.text);

  // 输入控件
  const fields = [...document.querySelectorAll("input,textarea,select")]
    .filter(vis)
    .map((el) => ({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute("type") || "",
      name: el.getAttribute("name") || "",
      ph: (el.getAttribute("placeholder") || "").slice(0, 40),
      maxlen: el.getAttribute("maxlength") || "",
      aria: (el.getAttribute("aria-label") || "").slice(0, 30),
    }))
    .filter((x) => x.ph || x.name || x.aria)
    .slice(0, 80);

  // 区块标题（h1-h5 与 类名含 title 的短文本）
  const heads = [
    ...new Set(
      [
        ...document.querySelectorAll("h1,h2,h3,h4,h5"),
        ...[...document.querySelectorAll("div,span")]
          .filter((e) => /title|header|section/i.test(e.className || "") && e.children.length === 0)
          .slice(0, 400),
      ]
        .filter(vis)
        .map((e) => norm(e.textContent))
        .filter((t) => t && t.length <= 24)
    ),
  ].slice(0, 80);

  const buttons = [
    ...new Set(
      [...document.querySelectorAll("button,[role='button']")]
        .filter(vis)
        .map((b) => norm(b.textContent))
        .filter((t) => t && t.length <= 14)
    ),
  ].slice(0, 80);

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
    bodyLen: (document.body?.innerText || "").length,
    labels,
    fields,
    heads,
    buttons,
    apis,
  };
});

await fs.writeFile(OUT + "/round-03-create.json", JSON.stringify(dump, null, 2), "utf8");
console.log("URL:", dump.url, "TITLE:", dump.title, "bodyLen:", dump.bodyLen);
console.log("--- LABELS (" + dump.labels.length + ") ---");
for (const l of dump.labels) console.log((l.required ? "*" : " ") + l.text);
console.log("--- FIELDS (" + dump.fields.length + ") ---");
for (const f of dump.fields) console.log(`${f.tag}/${f.type}\tph=${f.ph}\tname=${f.name}\tmax=${f.maxlen}`);
console.log("--- HEADS ---");
console.log(dump.heads.join(" | "));
console.log("--- BUTTONS ---");
console.log(dump.buttons.join(" | "));
console.log("--- APIS ---");
console.log(dump.apis.join("\n"));
