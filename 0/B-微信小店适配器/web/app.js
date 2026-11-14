/* 路由 + 顶部横排导航 + 左侧分组栏 + 首页图标墙
   界面版式照小猪（顶部两行 / 左侧白底窄栏 / 首页图标墙），配色见 style.css 末尾覆盖层。
   页面渲染仍全部交给 pages.js，本文件不碰任何业务逻辑。 */
(function () {
  const STATE_LABEL = { real: ['可用', 'ok'], patch: ['补丁', 'warn'], read: ['只读', 'warn'], shell: ['壳', ''] };
  const norm = k => (String(k).charAt(0) === '/' ? String(k) : '/' + String(k));
  const MENU = window.MENU || [];

  /* key -> { it, grp }  —— grp 为 null 表示顶层单项 */
  const flat = {};
  MENU.forEach(g => {
    if (g.children) g.children.forEach(c => { flat[norm(c.key)] = { it: c, grp: g }; });
    else flat[norm(g.key)] = { it: g, grp: null };
  });

  function badge(st) {
    /* 照原版：左侧是纯文字列表。只在「未接入」的页上打一个小标，别让 70 多个"可用"刷屏。 */
    if (st !== 'shell') return '';
    return '<em class="mb">未接入</em>';
  }

  /* ---------- 顶部横排：每个顶层分组一个入口（照小猪：首页/商品复制/多店关联/1688铺货/快速修改/任务记录） ---------- */
  function buildTop(active) {
    const box = document.getElementById('topnav');
    if (!box) return;
    box.innerHTML = MENU.map(g => {
      const hit = g.children
        ? g.children.some(c => norm(c.key) === active)
        : norm(g.key) === active;
      const href = '#' + (g.children ? norm(g.children[0].key) : norm(g.key));
      return '<a href="' + href + '" class="' + (hit ? 'on' : '') + '" data-g="' + U.esc(g.key) + '">' +
        U.esc(g.title) + '</a>';
    }).join('');
  }

  /* ---------- 左侧：只列「当前顶层分组」的项（照小猪：顶部选大类，左边列该类） ---------- */
  function buildSide(active, keyword) {
    const nav = document.getElementById('nav');
    const hd = document.getElementById('sideHd');
    if (!nav) return;

    /* 搜索模式：跨组列出所有命中项 */
    const q = (keyword || '').trim();
    if (q) {
      const hits = [];
      MENU.forEach(g => {
        (g.children || [g]).forEach(c => {
          if (String(c.title).indexOf(q) >= 0 || norm(c.key).indexOf(q) >= 0) hits.push({ c: c, g: g });
        });
      });
      hd.textContent = '搜索「' + q + '」· ' + hits.length + ' 项';
      nav.innerHTML = hits.length
        ? hits.map(h => {
          const k = norm(h.c.key);
          const gname = h.g.children ? '<span class="tiny" style="color:#b3b9c2"> · ' + U.esc(h.g.title) + '</span>' : '';
          return '<a href="#' + k + '" class="' + (active === k ? 'on' : '') + '">' +
            U.esc(h.c.title) + gname + badge(h.c.state) + '</a>';
        }).join('')
        : '<div class="tiny" style="padding:8px 10px;color:#a8aeb7">没有匹配的功能</div>';
      return;
    }

    const cur = flat[active];
    const grp = (cur && cur.grp) || MENU.find(g => g.children) || null;
    const items = grp ? grp.children : MENU.filter(g => !g.children);
    hd.textContent = grp ? grp.title : '功能';
    nav.innerHTML = items.map(c => {
      const k = norm(c.key);
      return '<a href="#' + k + '" class="' + (active === k ? 'on' : '') + '">' +
        U.esc(c.title) + badge(c.state) + '</a>';
    }).join('');
  }

  /* ---------- 首页：图标墙（分组 + 彩色图标块），照小猪首页 ---------- */
  const COLORS = ['y', 'g', 'b', 'r', 'p', 'c'];
  function shortIcon(title) {
    const t = String(title || '');
    const stripped = t.replace(/^(改|添加|删除|复制|同步|恢复|清理|多店|商品|自定义)/, '');
    return (stripped.charAt(0) || t.charAt(0) || '·');
  }

  /* ---------- 展示台：顶部那排真数字（2026-11-01 主人要求对接后台） ----------
     数据全部来自 /api/overview 一次拉齐；不再用 /api/products?page_size=1 拼。 */
  const OV = { data: null, at: 0, timer: null, busy: false };

  async function ovLoad(quiet) {
    if (OV.busy) return OV.data;
    OV.busy = true;
    const btn = document.getElementById('btnRefresh');
    if (btn && !quiet) { btn.disabled = true; btn.textContent = '刷新中…'; }
    try {
      const d = await U.get('/api/overview');
      OV.data = d; OV.at = Date.now();
      ovPaint(d);
      document.dispatchEvent(new CustomEvent('ov', { detail: d }));
    } catch (e) {
      const s = document.getElementById('syncAt');
      if (s) { s.textContent = '读取失败'; s.className = 'tiny bad'; }
    } finally {
      OV.busy = false;
      if (btn) { btn.disabled = false; btn.textContent = '↻ 刷新'; }
      const s = document.getElementById('syncAt');
      if (s && OV.at) s.textContent = '同步于 ' + new Date(OV.at).toTimeString().slice(0, 8);
    }
    return OV.data;
  }

  function card(id, v, sub, cls) {
    return '<div class="hs' + (cls ? ' ' + cls : '') + '"><b id="' + id + '">' + U.esc(v) + '</b>' +
      '<span>' + U.esc(sub) + '</span></div>';
  }

  /* 2026-11-10 主人口径：不再有「全店商品 / 上架中 / 未上架」这种把三家店
     混成一个总数的排卡；改成每家店一张独立卡，各读各的 /api/overview。
     抖店无免签商品接口，用 /api/dd/overview 的草稿/上架包数如实标注口径。 */
  async function paintPerShop(box) {
    let r;
    try { r = await U.get('/api/shops'); } catch (e) {
      box.innerHTML = '<div class="hs"><b>—</b><span>店铺台账读取失败</span></div>';
      return;
    }
    const shops = (r.shops || []).filter(s => s.enabled !== false);
    if (!shops.length) { box.innerHTML = '<div class="hs"><b>—</b><span>没有已启用店铺</span></div>'; return; }
    box.innerHTML = '<div class="hs"><b>' + shops.length + '</b><span>独立店铺（各自取数，不混算）</span></div>' +
      shops.map(s => '<div class="hs" id="opsHome-' + U.esc(s.appid) + '"><b>—</b><span>' +
        U.esc(s.name || s.mask) + ' · 读取中</span></div>').join('') +
      '<a class="hs" href="#/ops/shops" style="text-decoration:none"><b>›</b><span>三店独立数据 / 商品状态卡</span></a>';
    for (const s of shops) {
      const el = document.getElementById('opsHome-' + s.appid);
      if (!el) continue;
      try {
        if (s.kind === 'doudian') {
          const d = await U.get('/api/dd/overview');
          el.innerHTML = '<b>' + U.esc(d.drafts != null ? d.drafts : '—') + '</b><span>' +
            U.esc(s.name) + ' · 草稿（快照口径）</span>';
        } else {
          const d = await U.get('/api/overview', s.appid);
          const bs = d.by_status || {};
          el.innerHTML = '<b>' + U.esc(d.total != null ? d.total : '—') + '</b><span>' +
            U.esc(s.name) + ' · 上架 ' + U.esc(bs['5'] || 0) + ' / 未上架 ' + U.esc(bs['0'] || 0) + '</span>';
        }
      } catch (e) {
        el.innerHTML = '<b>—</b><span>' + U.esc(s.name) + ' · 读取失败</span>';
      }
    }
  }

  function ovPaint(d) {
    const box = document.getElementById('homestat');
    if (!box) return;
    /* 首页只放「每店独立卡」，总数混算那排已按主人指令清掉。 */
    paintPerShop(box);
  }

  /* 店铺下拉 = 站内切换当前店铺（不弹窗、不改服务端全局 current）。
     整页所有请求都带 X-Shop-Id，换掉本页店号后，浏览与上架都落到选中的那家。 */
  async function loadShops() {
    const sel = document.getElementById('shopSel');
    if (!sel) return;
    try {
      const r = await U.get('/api/shops');
      const cur = U.shopId() || r.current || '';
      /* 抖店(kind=doudian)只在「上架目标勾选」里出现；它没有微信 AppID 空间，
         顶部切店下拉不能带上，否则 U.openShop 会切到不存在的账号。 */
      const shops = (r.shops || []).filter(s => s.enabled !== false && s.kind !== 'doudian');
      sel.innerHTML = shops.map(s =>
        '<option value="' + U.esc(s.appid) + '"' + (s.appid === cur ? ' selected' : '') + '>' +
        U.esc(s.name || s.mask) + '（' + U.esc(s.mask) + '）</option>').join('') +
        '<option value="__add">＋ 账号管理 / 添加账号…</option>';
      const here = shops.find(s => s.appid === cur) || null;
      const sp = document.getElementById('shopSpace');
      if (sp) {
        sp.textContent = here ? ('当前空间：' + (here.name || here.mask)) : '主空间';
        sp.className = 'tag ' + (here ? 'ok' : '');
      }
      sel.onchange = async () => {
        const v = sel.value;
        if (v === '__add') { location.hash = '#/accounts'; loadShops(); return; }
        U.openShop(v);
        U.toast('已切到 ' + (sel.options[sel.selectedIndex].text || '该账号') + '，本页数据会重新拉取');
        try { ovLoad(true); } catch (e) {}
        loadShops();
      };
    } catch (e) { sel.innerHTML = '<option>读取失败</option>'; }
  }

  /* 自动刷新：连上立刻刷一次；保持连接时每 10 分钟一轮（主人要求） */
  function startAutoRefresh() {
    ovLoad(true);
    loadShops();
    const btn = document.getElementById('btnRefresh');
    if (btn) btn.onclick = () => { ovLoad(false); U.toast('正在重新拉取后台数据…'); };
    clearInterval(OV.timer);
    OV.timer = setInterval(() => {
      if (document.visibilityState === 'visible') ovLoad(true);
    }, 10 * 60 * 1000);
    // 回到页面超过 10 分钟没刷过 → 立刻补一次（主人要的「每次连接自动刷新」）
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && Date.now() - OV.at > 10 * 60 * 1000) ovLoad(true);
    });
  }

  function homeGrid(holder) {
    let h = '<div class="homebanner"><span class="hb-emoji">👍</span>批量修改用这套工具！工具好，下班走得早！</div>';
    /* 2026-11-10：不再摆「全店/上架中/未上架」混算总数卡，改由 paintPerShop 按店独立渲染。 */
    h += '<div class="homestat" id="homestat">' +
      '<div class="hs"><b>—</b><span>按店独立读取中…</span></div></div>';

    const groups = MENU.filter(g => g.children).slice();
    const singles = MENU.filter(g => !g.children);
    if (singles.length) groups.push({ title: '其它功能', children: singles });

    groups.forEach((g, gi) => {
      const items = g.children || [];
      if (!items.length) return;
      h += '<div class="hgroup"><div class="hgroup-h">' + U.esc(g.title) + '</div><div class="hgrid">';
      items.forEach((c, i) => {
        const k = norm(c.key);
        const col = COLORS[(gi + i) % COLORS.length];
        h += '<a class="hitem" href="#' + k + '" title="' + U.esc(c.title) +
          (c.state === 'shell' ? '（未接入）' : '') + '">' +
          '<span class="hicon ' + col + '">' + U.esc(shortIcon(c.title)) + '</span>' +
          '<span class="hlabel">' + U.esc(c.title) + '</span></a>';
      });
      h += '</div></div>';
    });
    holder.innerHTML = h;

    /* 统计来自全局 OV 缓存（/api/overview 一次拉齐）；没缓存就现在去拉 */
    if (OV.data) ovPaint(OV.data);
    else ovLoad(true);
  }

  /* ---------- 渲染 ---------- */
  function render() {
    const hash = norm((location.hash || '#/home').replace(/^#/, '') || '/home');
    const leaf = (flat[hash] || {}).it || (flat['/home'] || {}).it || { title: '首页', page: 'home' };
    buildTop(hash);
    buildSide(hash, (document.getElementById('funcSearch') || {}).value || '');

    const crumb = document.getElementById('crumb');
    if (crumb) {
      crumb.innerHTML = '<b>' + U.esc(leaf.title) + '</b>' + (leaf.state ? ' ' + badge(leaf.state) : '') +
        (leaf.need ? '<span class="tiny need"> · ' + U.esc(leaf.need) + '</span>' : '');
    }
    document.title = leaf.title + ' · 微店后台管理系统';

    const view = document.getElementById('view');
    view.innerHTML = '';
    /* 每次渲染换新容器：老页面的 await 回来时写的是已被摘掉的旧节点，不会覆盖新页面 */
    const holder = document.createElement('div');
    holder.className = 'pageholder';
    view.appendChild(holder);

    /* 首页走图标墙；其余全部交给 pages.js */
    if (hash === '/home') { homeGrid(holder); return; }

    const P = window.PAGES;
    try {
      if (!P) throw new Error('页面模块未加载（pages.js）');
      if (leaf.page && P[leaf.page]) P[leaf.page](holder, leaf);
      else if (leaf.act && P.workbench) P.workbench(holder, leaf);
      else if (P.shell) P.shell(holder, leaf);
      else throw new Error('没有可用的渲染函数：' + leaf.title);
    } catch (e) {
      view.innerHTML = '<div class="card"><div class="err">页面出错：' + U.esc(e.message) +
        '<br><span class="tiny">' + U.esc(String(e.stack || '').slice(0, 300)) + '</span></div></div>';
      console.error(e);
    }
    const main = document.querySelector('.main');
    if (main) main.scrollTop = 0;
  }

  /* ---------- 顶部工具条 ---------- */
  function wireTopbar() {
    const imp = document.getElementById('btnImport');
    const exp = document.getElementById('btnExport');
    if (imp) imp.onclick = () => { location.hash = '#/importItem'; };
    if (exp) exp.onclick = () => { location.hash = '#/exportItem'; };

    const f = document.getElementById('funcSearch');
    if (f) {
      let t = null;
      f.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(() => {
          const hash = norm((location.hash || '#/home').replace(/^#/, '') || '/home');
          buildSide(hash, f.value);
        }, 120);
      });
      f.addEventListener('keydown', e => {
        if (e.key === 'Escape') { f.value = ''; f.dispatchEvent(new Event('input')); }
        if (e.key === 'Enter') {
          const first = document.querySelector('#nav a');
          if (first) location.hash = first.getAttribute('href');
        }
      });
    }
  }

  async function boot() {
    wireTopbar();
    /* 主人 2026-11-07：「6.0 里没看到新加微信小店的按钮」→ 顶部红色常驻入口，
       点一下直达账号管理页；桌面 5.0 页另有「＋ 添加店铺」按钮，写的是同一份台账。 */
    const addShop = document.getElementById('btnAddShop');
    if (addShop) addShop.onclick = () => { location.hash = '#/accounts'; };
    try {
      const h = await U.get('/api/health');
      const conn = document.getElementById('conn');
      if (conn) {
        conn.textContent = h.ok ? '已连接微信' : '未连接';
        conn.className = 'tag ' + (h.ok ? 'ok' : 'bad');
      }
    } catch (e) {
      const conn = document.getElementById('conn');
      if (conn) { conn.textContent = '后端未响应'; conn.className = 'tag bad'; }
    }
    render();
    /* 展示台：连上就刷一次，之后每 10 分钟一轮，随时可手动刷（主人 2026-11-01 要求） */
    startAutoRefresh();
    /* 任何页面切回首页时，若数据超过 10 分钟就补一次 */
    window.addEventListener('hashchange', () => {
      if (norm(location.hash.replace(/^#/, '')) === '/home' && Date.now() - OV.at > 10 * 60 * 1000) ovLoad(true);
    });
  }

  window.addEventListener('hashchange', () => {
    const f = document.getElementById('funcSearch');
    if (f) f.value = '';
    render();
  });
  document.addEventListener('DOMContentLoaded', boot);
  if (document.readyState !== 'loading') boot();
})();
