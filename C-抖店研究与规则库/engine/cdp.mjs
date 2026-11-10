// 极简 CDP 客户端：直接连 ego 的 --remote-debugging-port，不装 puppeteer。
// 用途：所有抖店脚本共用这一层，页面读写全部走已登录的真实浏览器。
import fs from "node:fs/promises";

export const DEFAULT_PORT = 9224;

export async function listTargets(port = DEFAULT_PORT) {
  const r = await fetch(`http://127.0.0.1:${port}/json/list`);
  return await r.json();
}

/** 找一个可用页面：优先 url 匹配，其次 title 匹配 */
export async function findPage({ urlIncludes, titleIncludes, port = DEFAULT_PORT, skip = [] } = {}) {
  const ts = await listTargets(port);
  const pages = ts.filter((t) => t.type === "page" && !skip.includes(t.id));
  if (urlIncludes) {
    const hit = pages.find((p) => p.url.includes(urlIncludes) && p.webSocketDebuggerUrl);
    if (hit) return hit;
  }
  if (titleIncludes) {
    const hit = pages.find((p) => (p.title || "").includes(titleIncludes) && p.webSocketDebuggerUrl);
    if (hit) return hit;
  }
  return pages.find((p) => p.webSocketDebuggerUrl) || null;
}

export class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method) {
        this.events.push(msg);
        if (this.events.length > 4000) this.events.splice(0, 2000);
      }
    });
  }

  static async connect(wsUrl, timeoutMs = 15000) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error("ws timeout " + wsUrl)), timeoutMs);
      ws.addEventListener("open", () => { clearTimeout(t); res(); }, { once: true });
      ws.addEventListener("error", (e) => { clearTimeout(t); rej(new Error("ws error")); }, { once: true });
    });
    return new CDP(ws);
  }

  /** 连到某个页面：给 urlIncludes / titleIncludes，或直接 newTarget */
  static async page(opts = {}) {
    const t = await findPage(opts);
    if (!t) throw new Error("no page target matched " + JSON.stringify(opts));
    const c = await CDP.connect(t.webSocketDebuggerUrl);
    c.target = t;
    await c.send("Runtime.enable").catch(() => {});
    await c.send("Page.enable").catch(() => {});
    await c.send("Network.enable").catch(() => {});
    return c;
  }

  /** 新开一个标签页 */
  static async newPage(url = "about:blank", port = DEFAULT_PORT) {
    const r = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
    const t = await r.json();
    const c = await CDP.connect(t.webSocketDebuggerUrl);
    c.target = t;
    await c.send("Runtime.enable").catch(() => {});
    await c.send("Page.enable").catch(() => {});
    await c.send("Network.enable").catch(() => {});
    return c;
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("cdp timeout: " + method));
        }
      }, 60000);
    });
  }

  /** 在页面里求值，自动 await Promise + 按值返回 */
  async eval(expr, { awaitPromise = true, byValue = true } = {}) {
    const r = await this.send("Runtime.evaluate", {
      expression: expr,
      awaitPromise,
      returnByValue: byValue,
      userGesture: true,
    });
    if (r.exceptionDetails) {
      throw new Error("page-exception: " + JSON.stringify(r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    }
    return r.result?.value;
  }

  async goto(url, { settle = 6000, waitFor = null } = {}) {
    await this.send("Page.navigate", { url });
    await sleep(1200);
    await this.waitReady(settle);
    if (waitFor) await this.waitFor(waitFor, 20000);
  }

  async waitReady(settle = 4000) {
    const t0 = Date.now();
    while (Date.now() - t0 < 30000) {
      const st = await this.eval("document.readyState").catch(() => null);
      if (st === "complete") break;
      await sleep(400);
    }
    await sleep(settle);
  }

  /** 轮询直到页面里出现某元素（按 selector 或文本） */
  async waitFor(what, timeout = 20000) {
    const t0 = Date.now();
    const isSel = /^[.#\[a-z]/i.test(what) && !what.includes("text=");
    while (Date.now() - t0 < timeout) {
      const ok = await this.eval(`(() => {
        ${isSel ? `return !!document.querySelector(${JSON.stringify(what)});` : `return (document.body?.innerText||"").includes(${JSON.stringify(what.replace(/^text=/, "").replace(/^"|"$/g, ""))});`}
      })()`).catch(() => false);
      if (ok) return true;
      await sleep(500);
    }
    return false;
  }

  async text(sel = "body") {
    return await this.eval(`(document.querySelector(${JSON.stringify(sel)})?.innerText || "")`);
  }

  async click(locator, { nth = 0, index = null } = {}) {
    const js = `(() => {
      const toks = ${JSON.stringify(locator)};
      const m = /^text="(.*)"$/.exec(toks);
      let els;
      if (m) {
        els = [...document.querySelectorAll('a,button,span,div,li,p,label')]
          .filter(e => (e.textContent||'').trim() === m[1] && e.offsetParent !== null);
        if (!els.length) els = [...document.querySelectorAll('a,button,span,div,li,p,label')]
          .filter(e => (e.textContent||'').trim().includes(m[1]) && e.offsetParent !== null);
      } else {
        els = [...document.querySelectorAll(toks)].filter(e => e.offsetParent !== null);
      }
      if (!els.length) return { ok:false, reason:'not-found', locator: toks };
      const i = ${index === null ? nth : index};
      const el = els[i];
      if (!el) return { ok:false, reason:'index-oob', count: els.length };
      el.scrollIntoView({block:'center'});
      el.click();
      return { ok:true, count: els.length, tag: el.tagName, text: (el.innerText||'').trim().slice(0,40) };
    })()`;
    return await this.eval(js);
  }

  /** 输入：先聚焦再设值，并派发 input/change 事件（React 受控组件必须这样喂） */
  async fill(selector, value, { nth = 0 } = {}) {
    return await this.eval(`(() => {
      const els = [...document.querySelectorAll(${JSON.stringify(selector)})].filter(e => e.offsetParent !== null);
      const el = els[${nth}];
      if (!el) return { ok:false, reason:'not-found', selector: ${JSON.stringify(selector)}, count: els.length };
      el.focus();
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, ${JSON.stringify(String(value))});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
      return { ok:true, value: el.value };
    })()`);
  }

  async shot(path, { fullPage = false } = {}) {
    const r = await this.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: fullPage });
    await fs.mkdir(path.split(/[\\/]/).slice(0, -1).join("/") || ".", { recursive: true });
    await fs.writeFile(path, Buffer.from(r.data, "base64"));
    return path;
  }

  /** 抓自本次 hook 起的所有 XHR/fetch */
  async hookXhr() {
    await this.eval(`(() => {
      if (window.__cap) return 'already';
      window.__cap = [];
      const rec = (o) => { try { window.__cap.push(o); } catch(e){} };
      const of = window.fetch;
      window.fetch = function(...a) {
        const url = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url);
        const p = of.apply(this, a);
        p.then(r => {
          const ct = r.headers.get('content-type')||'';
          if (ct.includes('json')) r.clone().text().then(t => rec({k:'fetch', url, status:r.status, body:t.slice(0,400000)})).catch(()=>{});
          else rec({k:'fetch', url, status:r.status, body:''});
        }).catch(e => rec({k:'fetch', url, err:String(e)}));
        return p;
      };
      const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(m, u, ...r) { this.__u = u; this.__m = m; return oo.call(this, m, u, ...r); };
      XMLHttpRequest.prototype.send = function(...a) {
        this.addEventListener('load', () => {
          let b = '';
          try { b = (this.responseType === '' || this.responseType === 'text') ? String(this.responseText||'').slice(0,400000) : ''; } catch(e){}
          rec({k:'xhr', m:this.__m, url:this.__u, status:this.status, body:b});
        });
        return os.apply(this, a);
      };
      return 'hooked';
    })()`);
  }

  async drainXhr() {
    return await this.eval("(window.__cap || []).splice(0)");
  }

  /** 从 CDP 原生 Network 事件里取请求（含 POST body）—— 比页面内 hook 覆盖更全 */
  netRequests(filter = null) {
    return this.events
      .filter((e) => e.method === "Network.requestWillBeSent")
      .map((e) => ({
        url: e.params.request.url,
        method: e.params.request.method,
        postData: e.params.request.postData || "",
        reqId: e.params.requestId,
        type: e.params.type,
      }))
      .filter((r) => (filter ? filter.test(r.url) : true));
  }

  /** 取某个 requestId 的响应体（可能已被释放，失败返回 null） */
  async bodyOf(reqId) {
    try {
      const r = await this.send("Network.getResponseBody", { requestId: reqId });
      return r.base64Encoded ? Buffer.from(r.body, "base64").toString("utf8") : r.body;
    } catch {
      return null;
    }
  }

  /** 一次性把「请求 + 响应体」配成对 */
  async netPairs(filter = null) {
    const reqs = this.netRequests(filter);
    const out = [];
    for (const r of reqs) {
      const body = await this.bodyOf(r.reqId);
      out.push({ ...r, resp: body });
    }
    return out;
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function saveJson(path, obj) {
  await fs.mkdir(path.replace(/[\\/][^\\/]+$/, ""), { recursive: true });
  await fs.writeFile(path, JSON.stringify(obj, null, 2), "utf8");
  return path;
}
