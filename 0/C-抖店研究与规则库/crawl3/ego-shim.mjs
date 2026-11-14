/**
 * ego shim —— 让 batch-v6.mjs 脱离 ego 的 nodejs 模式，直接用 node 跑。
 * 实现 batch-v6 用到的 page API：evaluate / goto / click / setInputFiles /
 * waitForFunction / waitForTimeout / screenshot / keyboard / mouse / reload /
 * waitForFileChooser / context / url / title / bringToFront
 *
 * 用法（batch-v6.mjs 顶部已自动引入）：await import("./ego-shim.mjs")
 */
import fs from "node:fs";

const CDP_PORT = 9224;

async function pickPage(spaceName) {
  const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
  const pages = list.filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
  // 优先抖店页
  return pages.find((t) => /fxg\.jinritemai\.com/.test(t.url || "")) || pages[0];
}

class ShimPage {
  constructor(ws, info) {
    this.ws = ws; this.info = info; this.id = 0; this.pending = new Map();
    this.chooser = null; this._fireChooser = null;
  }
  async init() {
    await new Promise((res, rej) => { this.ws.onopen = res; this.ws.onerror = rej; });
    this.ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) { this.pending.get(m.id)(m); this.pending.delete(m.id); return; }
      if (m.method === "Page.fileChooserOpened" && this._fireChooser) { this._fireChooser(m.params); this._fireChooser = null; }
    };
    await this.send("Page.enable");
    await this.send("DOM.enable");
    await this.send("Runtime.enable");
    return this;
  }
  send(method, params = {}) {
    return new Promise((res) => { const i = ++this.id; this.pending.set(i, res); this.ws.send(JSON.stringify({ id: i, method, params })); });
  }
  async evaluate(fnOrExpr, arg) {
    let expr;
    if (typeof fnOrExpr === "function") {
      const src = fnOrExpr.toString();
      expr = arg === undefined ? `(${src})()` : `(${src})(${JSON.stringify(arg)})`;
    } else expr = String(fnOrExpr);
    const r = await this.send("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise: true, userGesture: true,
    });
    if (r.result?.exceptionDetails) {
      const t = r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text;
      throw new Error("evaluate: " + String(t).split("\n")[0]);
    }
    return r.result?.result?.value;
  }
  async waitForTimeout(ms) { await new Promise((r) => setTimeout(r, ms)); }
  async url() { return (await this.evaluate(() => location.href)); }
  async title() { return (await this.evaluate(() => document.title)); }
  async bringToFront() { try { await this.send("Page.bringToFront"); } catch {} }
  async goto(url, opts = {}) {
    await this.send("Page.navigate", { url });
    const to = opts.timeout || 60000;
    const t0 = Date.now();
    // 先等 readyState，再等 3 秒稳定（抖店 SPA 渲染慢）
    while (Date.now() - t0 < to) {
      await this.waitForTimeout(600);
      const st = await this.evaluate(() => document.readyState).catch(() => "loading");
      if (st === "interactive" || st === "complete") break;
    }
    await this.waitForTimeout(1500);
  }
  async reload(opts = {}) {
    await this.send("Page.reload");
    await this.waitForTimeout(opts.waitAfter || 4000);
  }
  async click(selector) {
    const ok = await this.evaluate((sel) => {
      const dc = (el) => { for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window })); };
      let el = null;
      let m = /^text=(.*)$/.exec(sel);
      if (m) {
        const want = m[1].replace(/^["']|["']$/g, "").trim();
        el = [...document.querySelectorAll("button,a,span,div")]
          .filter((e) => e.offsetParent !== null && e.children.length === 0 && (e.innerText || "").trim() === want)[0];
      } else el = document.querySelector(sel);
      if (!el) return false;
      el.scrollIntoView({ block: "center" });
      dc(el);
      return true;
    }, selector).catch(() => false);
    if (!ok) throw new Error("click 找不到: " + selector);
  }
  async setInputFiles(selector, files, opts = {}) {
    const arr = Array.isArray(files) ? files : [files];
    // 解析 "sel >> nth=N"
    let sel = selector, nth = 0;
    const m = /^(.*?)\s*>>\s*nth=(\d+)$/.exec(selector);
    if (m) { sel = m[1].trim(); nth = Number(m[2]); }
    const r = await this.send("Runtime.evaluate", {
      expression: `(() => { const els=[...document.querySelectorAll(${JSON.stringify(sel)})]; return els.length; })()`,
      returnByValue: true,
    });
    const n = r.result?.result?.value || 0;
    if (!n) throw new Error(`setInputFiles 找不到 input: ${sel}`);
    const obj = await this.send("Runtime.evaluate", {
      expression: `[...document.querySelectorAll(${JSON.stringify(sel)})][${nth}]`,
      returnByValue: false,
    });
    const objectId = obj.result?.result?.objectId;
    if (!objectId) throw new Error("setInputFiles 取不到 objectId: " + sel);
    const res = await this.send("DOM.setFileInputFiles", { files: arr.map(String), objectId });
    if (res.error) throw new Error("setFileInputFiles: " + res.error.message);
    // 触发 change（有些站点不自动触发）
    await this.send("Runtime.callFunctionOn", {
      objectId,
      functionDeclaration: `function(){ this.dispatchEvent(new Event("change", {bubbles:true})); }`,
    }).catch(() => {});
  }
  async waitForFunction(fn, arg, opts = {}) {
    const to = opts.timeout || 60000;
    const t0 = Date.now();
    while (Date.now() - t0 < to) {
      let v = null;
      try { v = await this.evaluate(fn, arg); } catch { v = null; }
      if (v) return v;
      await this.waitForTimeout(800);
    }
    throw new Error("waitForFunction 超时 " + to + "ms");
  }
  async screenshot(opts = {}) {
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    const b64 = r.result?.data;
    if (b64 && opts.path) { fs.mkdirSync(String(opts.path).replace(/[\\/][^\\/]*$/, ""), { recursive: true }); fs.writeFileSync(opts.path, Buffer.from(b64, "base64")); }
    return b64;
  }
  get keyboard() {
    const self = this;
    return {
      async type(text, opts = {}) {
        const delay = opts.delay || 0;
        for (const ch of String(text)) {
          await self.send("Input.insertText", { text: ch });
          if (delay) await self.waitForTimeout(delay);
        }
      },
      async press(key) {
        const map = {
          Tab: { windowsVirtualKeyCode: 9, key: "Tab" },
          Enter: { windowsVirtualKeyCode: 13, key: "Enter" },
          Escape: { windowsVirtualKeyCode: 27, key: "Escape" },
          Backspace: { windowsVirtualKeyCode: 8, key: "Backspace" },
          ArrowDown: { windowsVirtualKeyCode: 40, key: "ArrowDown" },
          ArrowUp: { windowsVirtualKeyCode: 38, key: "ArrowUp" },
        };
        if (/^Control\+a$/i.test(key)) {
          await self.send("Input.dispatchKeyEvent", { type: "rawKeyDown", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65 });
          await self.send("Input.dispatchKeyEvent", { type: "keyUp", modifiers: 2, key: "a", code: "KeyA", windowsVirtualKeyCode: 65 });
          return;
        }
        const k = map[key] || { key, windowsVirtualKeyCode: key.toUpperCase().charCodeAt(0) };
        await self.send("Input.dispatchKeyEvent", { type: "rawKeyDown", ...k });
        await self.send("Input.dispatchKeyEvent", { type: "keyUp", ...k });
      },
    };
  }
  get mouse() {
    const self = this;
    return {
      async move(x, y) { await self.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y }); },
      async click(x, y, opts = {}) {
        await self.send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
        await self.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
        await self.send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
      },
    };
  }
  // batch-v6 在 SKU 图那里用 chooser 拿文件输入；这里用一个包装让它能用
  waitForFileChooser(opts = {}) {
    const self = this;
    let done = false;
    const p = new Promise((res) => {
      const t0 = Date.now();
      const tick = async () => {
        if (done) return;
        // 找当前页面上新出现的 file input（最可能是刚点开的那个弹窗里的）
        const n = await self.evaluate(() => document.querySelectorAll("input[type=file]").length).catch(() => 0);
        if (n > 0) { done = true; res({ setFiles: async (files) => { await self.setLastFileInput(files); } }); return; }
        if (Date.now() - t0 > (opts.timeout || 10000)) { done = true; res(null); return; }
        setTimeout(tick, 400);
      };
      tick();
    });
    return p;
  }
  async setLastFileInput(files) {
    const arr = Array.isArray(files) ? files : [files];
    const obj = await this.send("Runtime.evaluate", {
      expression: `[...document.querySelectorAll("input[type=file]")].pop()`, returnByValue: false,
    });
    const objectId = obj.result?.result?.objectId;
    if (!objectId) throw new Error("没有 file input");
    await this.send("DOM.setFileInputFiles", { files: arr.map(String), objectId });
  }
  context() { return { newCDPSession: async () => null }; }
}

/** 返回一个 space 对象：space.page("p1") / space.pages() */
export async function taskSpace(name = "") {
  const info = await pickPage(name);
  if (!info) throw new Error("没有可用页面（CDP " + CDP_PORT + " 无 page）");
  const ws = new WebSocket(info.webSocketDebuggerUrl);
  const page = await new ShimPage(ws, info).init();
  return {
    name: name || "shim",
    page: () => page,
    pages: () => [page],
    info,
  };
}

export default { taskSpace };
