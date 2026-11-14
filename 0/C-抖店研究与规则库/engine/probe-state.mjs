// 看当前 9224 浏览器里的登录状态与页面内容
import { CDP, sleep, saveJson } from "./cdp.mjs";

const OUT = "G:/Users/Administrator/Documents/deepseek-harness/default-workspace/抖店研究/crawl3";

const c = await CDP.page({ urlIncludes: "jinritemai" });
await c.send("Page.enable").catch(() => {});
await c.waitReady(4000);

const info = await c.eval(`(() => {
  const txt = (document.body?.innerText || "");
  return {
    url: location.href,
    title: document.title,
    len: txt.length,
    head: txt.slice(0, 1200),
    hasLoginWords: /登录|扫码|密码登录|验证码/.test(txt),
    hasShopWords: /商品管理|订单|店铺|体验分|保证金|草稿/.test(txt),
    cookiesLen: document.cookie.length,
  };
})()`);
console.log(JSON.stringify(info, null, 1).slice(0, 3000));
await c.shot(OUT + "/state-01.png");
console.log("shot ->", OUT + "/state-01.png");
c.close();
