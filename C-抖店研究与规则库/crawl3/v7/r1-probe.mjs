// 第 1 轮：在独立 AI 空间里开一个抖店新建页，确认空间与页面状态
const NAME = "抖店AI-上货-" + new Date().toISOString().slice(11, 19).replace(/:/g, "");

const task = await taskSpace(NAME);
const page = task.page("p1");
console.log("spaceId =", task.spaceId, "| name =", task.name, "| ownership =", task.ownership);
console.log("page label =", page.label, "| openedBy =", page.openedBy);

await page.goto("https://fxg.jinritemai.com/ffa/g/create", { waitUntil: "domcontentloaded" }).catch(async (e) => {
  console.log("goto err:", String(e).slice(0, 120));
});
await page.waitForTimeout(8000);
console.log("url =", await page.url());
console.log("title =", await page.title());

// 只打印页面骨架，不操作
const info = await page.evaluate(() => {
  const vv = (e) => e.offsetParent !== null;
  const files = [...document.querySelectorAll("input[type=file]")].map((e, i) => ({
    i, accept: e.accept || "", multiple: e.multiple,
  }));
  const btns = [...document.querySelectorAll("button,a,span,div")]
    .filter((e) => vv(e) && e.children.length === 0)
    .map((e) => (e.innerText || "").trim())
    .filter((t) => t && t.length < 12 && /AI|智|上传|加号|添加|生成|下一步/.test(t));
  return {
    files: files.slice(0, 12), fileCount: files.length,
    btns: [...new Set(btns)].slice(0, 25),
    bodyHead: (document.body.innerText || "").slice(0, 200),
  };
});
console.log("fileCount =", info.fileCount);
console.log("files =", JSON.stringify(info.files));
console.log("候选按钮 =", JSON.stringify(info.btns));
console.log("正文头 =", JSON.stringify(info.bodyHead));

console.log("SPACE_ID_KEEP=" + task.spaceId);
