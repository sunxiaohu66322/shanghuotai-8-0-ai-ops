/* 公共小工具（无依赖） */
window.U = {
  esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  },
  money(fen) {
    if (fen == null || fen === '') return '—';
    const n = Number(fen) / 100;
    return isFinite(n) ? '¥' + n.toFixed(2) : '—';
  },
  /* 多账号独立空间（2026-11-07）：一个标签页 = 一家店。
     店号来自 URL ?shop= 或本标签页的 sessionStorage —— 标签页级隔离，
     localStorage 是全域共享的，用了必串。所有请求都带 X-Shop-Id，
     服务端据此选凭据；页面切换账号绝不改服务端全局 current。 */
  shopId() {
    const m = new URLSearchParams(location.search).get('shop');
    if (m) { try { sessionStorage.setItem('sht_shop', m); } catch (e) {} return m; }
    try { return sessionStorage.getItem('sht_shop') || ''; } catch (e) { return ''; }
  },
  setShop(id) {
    try { sessionStorage.setItem('sht_shop', id || ''); } catch (e) {}
  },
  /* 站内切换店铺：不弹新窗口。
     桌面内嵌壳里 window.open 只会开出一个小框、进不去（2026-11-07 实测）。
     所有请求本就带 X-Shop-Id，把本页店号换掉，整页数据与写操作立刻全部落到新店，
     不需要第二个页面，也不会出现「一个小框然后进不去」。 */
  openShop(id) {
    U.setShop(id || '');
    try {
      const u = new URL(location.href);
      if (id) u.searchParams.set('shop', id); else u.searchParams.delete('shop');
      history.replaceState(null, '', u.toString());
    } catch (e) {}
    location.hash = '#/home';
    /* 整页重载一次：所有缓存、概览、列表按新店号重新拉，不留旧店数据。
       URL 里已带 ?shop=，重载后本页仍锁在新店。 */
    try { location.reload(); } catch (e) {}
  },
  /* 第三参可显式指定店号（多店上架逐家提交时用），不改动本页店号、不碰服务端全局。 */
  hdr(shopId) {
    const h = { 'Accept': 'application/json' };
    const s = shopId === undefined ? U.shopId() : (shopId || '');
    if (s) h['X-Shop-Id'] = s;
    return h;
  },
  async get(p, shopId) {
    const r = await fetch(p, { headers: U.hdr(shopId) });
    return r.json();
  },
  async post(p, b, shopId) {
    const r = await fetch(p, {
      method: 'POST',
      headers: Object.assign(U.hdr(shopId), { 'Content-Type': 'application/json' }),
      body: JSON.stringify(b || {})
    });
    return r.json();
  },
  q(s, root) { return (root || document).querySelector(s); },
  qa(s, root) { return Array.from((root || document).querySelectorAll(s)); },
  /* 异步回写守卫：接口返回时用户可能已切页，容器被清空 → 直接赋值会抛
     "Cannot set properties of null"。U.w 容器不在就安静返回 false，不抛错。 */
  w(root, sel, val, prop) {
    const el = (root || document).querySelector(sel);
    if (!el) return false;
    if (prop === 'style.display') el.style.display = val;
    else el[prop] = val;
    return true;
  },
  toast(msg, ok) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast show ' + (ok === false ? 'bad' : 'good');
    clearTimeout(window.__tt);
    window.__tt = setTimeout(() => { t.className = 'toast'; }, 3600);
  },
  /* 微信商品状态真值 —— 官方 product/get 枚举（此前把 6 当「出售中」是错的：6=回收站，5=上架） */
  STATUS_TEXT: { 0: '未上架', 5: '上架', 6: '回收站', 11: '自主下架', 12: '售罄下架', 13: '违规下架', 14: '保证金不足', 15: '品牌过期', 20: '封禁' },
  STATUS_OPTS: [['', '全部状态（合并）'], ['5', '上架'], ['0', '未上架'], ['6', '回收站'], ['11', '自主下架'], ['12', '售罄下架'], ['13', '违规下架']],
  statusText(s) {
    const k = String(s);
    return U.STATUS_TEXT[k] || ('状态' + k);
  },
  statusTag(s) {
    const m = { 0: '', 5: 'ok', 6: '', 11: 'warn', 12: 'warn', 13: 'err', 14: 'err', 15: 'err', 20: 'err' };
    return '<span class="tag ' + (m[String(s)] || '') + '">' + U.esc(U.statusText(s)) + '</span>';
  },
  rows(res) {
    if (!res) return [];
    if (res.data && res.data.rows) return res.data.rows;
    if (res.rows) return res.rows;
    return [];
  },
  busy(btn, on) {
    if (!btn) return;
    if (on) { btn.dataset.o = btn.textContent; btn.disabled = true; btn.textContent = '执行中…'; }
    else { btn.disabled = false; if (btn.dataset.o) btn.textContent = btn.dataset.o; }
  }
};
