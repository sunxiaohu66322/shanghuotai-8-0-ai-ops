/* 7.0 抖店后台管理 —— 概览 / 草稿箱 / 货源 / 上架执行 / 后台跳转
   全部数据来自 /api/dd/*（后端 dd.py）。抖店没有免签直连接口，
   真实数据只能来自 ego 跑出来的页面快照，所以这里从不编数字。 */
(function () {
  const P = window.PAGES || (window.PAGES = {});
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const CARD = (h, body, extra) =>
    '<div class="card"' + (extra ? ' style="' + extra + '"' : '') + '><div class="card-h">' + h + '</div>' + body + '</div>';

  /* ============================================================ ① 概览 */
  P.ddOverview = async (root) => {
    root.innerHTML =
      '<div class="statbar" id="ddTop"><span class="tiny muted">读取中…</span></div>' +
      '<div style="display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px;align-items:start">' +
        '<div>' +
          CARD('① 店铺', '<div id="ddShop"><div class="tiny muted">读取中…</div></div>') +
          CARD('② 上架链路 · 一件货要走完的四段',
            '<div style="display:grid;gap:8px">' +
              [['货源包', '上货台 packs 里的 19 列 CSV，一件一行'],
               ['传图/标题/类目', '主图 3 张 1:1 + 详情图 + 标题 + 手动选末级类目'],
               ['属性', '品牌 LZL · 功能 · 支撑件木种（必填项靠填写检查定位）'],
               ['规格价格库存', '规格名/值、SKU 价格库存、重量；运费模板断言']]
              .map((x, i) => '<div class="row" style="align-items:flex-start;gap:10px">' +
                '<span class="tag">' + (i + 1) + '</span>' +
                '<div><div style="font-size:12.5px;font-weight:600">' + x[0] + '</div>' +
                '<div class="tiny muted">' + x[1] + '</div></div></div>').join('') +
            '</div>' +
            '<div class="needbox" style="margin-top:8px">跑完落草稿箱，不自动点发布。发布是你自己按。</div>', 'margin-top:12px') +
        '</div>' +
        '<div>' +
          CARD('③ 数字', '<div id="ddNums"><div class="tiny muted">读取中…</div></div>') +
          CARD('④ 去干活', '<div id="ddGo" style="display:grid;gap:6px"></div>', 'margin-top:12px') +
        '</div>' +
      '</div>';

    U.get('/api/dd/overview').then(r => {
      const s = r.shop || {};
      U.w(root, '#ddShop', '<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
        '<span class="tag ok">' + esc(s.name || '店铺名未读到') + '</span>' +
        (s.type ? '<span class="tag">' + esc(s.type) + '</span>' : '') +
        (s.state ? '<span class="tag ok">' + esc(s.state) + '</span>' : '') +
        '</div><div class="tiny muted" style="margin-top:6px">' + esc(r.note || '') + '</div>', 'innerHTML');

      const nums = [
        ['草稿箱商品', r.drafts, '从页面快照解析'],
        ['其中待提交', r.drafts_ready, '可编辑 / 可发布'],
        ['货源包', r.packs, r.packs_rows + ' 行商品'],
        ['上架脚本', r.scripts, r.script_dir_ok ? '目录正常' : '目录缺失'],
        ['快照文件', r.snapshots, 'work + crawl3'],
      ];
      U.w(root, '#ddNums', nums.map(n =>
        '<div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid #eef1f6">' +
          '<span style="font-size:12.5px">' + esc(n[0]) + '</span>' +
          '<span><b>' + esc(n[1]) + '</b> <span class="tiny muted">' + esc(n[2]) + '</span></span></div>').join(''), 'innerHTML');

      U.w(root, '#ddTop', '<span class="tag ' + (r.scripts > 0 ? 'ok' : 'warn') + '">脚本 ' + r.scripts + '</span>' +
        '<span class="tag ' + (r.packs > 0 ? 'ok' : 'warn') + '">货源包 ' + r.packs + '</span>' +
        '<span class="tag ' + (r.drafts > 0 ? 'ok' : 'warn') + '">草稿 ' + r.drafts + '</span>' +
        '<span class="sp"></span><span class="tiny muted">' + esc(r.work_dir || '') + '</span>', 'innerHTML');

      U.w(root, '#ddGo', [
        ['/dd/drafts', '打开草稿箱（真实清单）'],
        ['/dd/queue', '打开待上架货源'],
        ['/dd/publish', '打开上架执行'],
        ['/dd/links', '打开后台跳转'],
      ].map(x => '<button data-go="' + x[0] + '">' + esc(x[1]) + ' →</button>').join(''), 'innerHTML');
      U.qa('[data-go]', root).forEach(b => b.onclick = () => { location.hash = '#' + b.dataset.go; });
    }).catch(e => U.w(root, '#ddNums', '<span class="err">读取失败：' + esc(e.message) + '</span>', 'innerHTML'));
  };

  /* ============================================================ ② 草稿箱 */
  P.ddDrafts = async (root) => {
    root.innerHTML =
      CARD('抖店草稿箱 · 真实清单',
        '<div class="tb" style="margin:0 0 8px"><span class="tiny muted" id="ddDn">读取中…</span>' +
        '<span class="sp"></span><button id="ddDopen">直接在抖店打开草稿箱 ↗</button></div>' +
        '<div id="ddDbox"><div class="tiny muted">读取中…</div></div>');

    U.q('#ddDopen', root).onclick = () =>
      window.open('https://fxg.jinritemai.com/ffa/g/draft', '_blank', 'noopener');

    U.get('/api/dd/drafts').then(r => {
      const rows = r.rows || [];
      U.w(root, '#ddDn', rows.length + ' 件' + (r.files && r.files.length ? ' · 来源 ' + r.files.length + ' 份快照' : ''), 'textContent');
      const box = U.q('#ddDbox', root);
      if (!rows.length) {
        box.innerHTML = '<div class="needbox">' + esc(r.empty_hint || '没有数据。') + '</div>';
        return;
      }
      box.innerHTML = '<table class="tt" style="font-size:12px"><thead><tr>' +
        '<th style="width:44%">标题</th><th>商品 ID</th><th>状态</th><th>日期</th><th>操作</th></tr></thead><tbody>' +
        rows.map(x =>
          '<tr><td>' + esc(x.title) + '</td>' +
          '<td style="font-family:ui-monospace,Consolas,monospace">' + esc(x.pid) + '</td>' +
          '<td><span class="tag ' + (/待提交/.test(x.status) ? 'warn' : 'ok') + '">' + esc(x.status || '—') + '</span></td>' +
          '<td class="tiny muted">' + esc(x.date || '') + '</td>' +
          '<td><a href="https://fxg.jinritemai.com/ffa/g/create?product_id=' + esc(x.pid) +
            '&entrance=draft" target="_blank" rel="noopener" class="tiny">编辑 ↗</a></td></tr>').join('') +
        '</tbody></table>' +
        '<div class="tiny muted" style="margin-top:8px">编辑链接必须带 <code>entrance=draft</code>，否则页面是空壳。</div>';
    }).catch(e => U.w(root, '#ddDbox', '<div class="err">读取失败：' + esc(e.message) + '</div>', 'innerHTML'));
  };

  /* ============================================================ ③ 货源 */
  P.ddQueue = async (root) => {
    root.innerHTML =
      '<div style="display:grid;grid-template-columns:340px minmax(0,1fr);gap:12px;align-items:start">' +
        '<div>' + CARD('待上架货源 · 上货台包',
          '<div class="tb" style="margin:0 0 8px"><input id="ddQ" placeholder="搜包名…"><button id="ddQGo">搜</button>' +
          '<span class="sp"></span><span class="tiny" id="ddQn"></span></div>' +
          '<div id="ddPacks" style="max-height:560px;overflow:auto"><div class="tiny muted">读取中…</div></div>') + '</div>' +
        '<div>' + CARD('<span id="ddPackH">包内商品<span class="tiny muted"> · 左侧点一个包</span></span>',
          '<div id="ddPackBody" style="max-height:620px;overflow:auto"><div class="tiny muted">还没选包。</div></div>') + '</div>' +
      '</div>';

    let cur = null;
    const openPack = async (pk) => {
      cur = pk;
      U.w(root, '#ddPackH', esc(pk.name) + ' <span class="tiny muted">· ' + pk.rows + ' 行 × ' + pk.cols + ' 列</span>', 'innerHTML');
      U.w(root, '#ddPackBody', '<div class="tiny muted">读取中…</div>', 'innerHTML');
      const r = await U.get('/api/dd/pack?path=' + encodeURIComponent(pk.path) + '&limit=200');
      const box = U.q('#ddPackBody', root);
      if (!r.ok) { box.innerHTML = '<div class="err">' + esc(r.error) + '</div>'; return; }
      const head = r.head || [];
      box.innerHTML = '<table class="tt" style="font-size:11.5px"><thead><tr>' +
        head.map(h => '<th>' + esc(h) + '</th>').join('') + '</tr></thead><tbody>' +
        (r.rows || []).map(rw => '<tr>' + head.map((_, i) => {
          let v = rw[i] == null ? '' : String(rw[i]);
          if (/^https?:/.test(v)) v = v.slice(0, 30) + '…';
          return '<td>' + esc(v.slice(0, 56)) + '</td>';
        }).join('') + '</tr>').join('') + '</tbody></table>' +
        '<div class="tiny muted" style="margin-top:6px">共 ' + r.total + ' 行，显示前 ' + (r.rows || []).length + ' 行（只读）</div>';
    };

    const load = async (kw) => {
      const r = await U.get('/api/dd/queue?q=' + encodeURIComponent(kw || '') + '&limit=80');
      const box = U.q('#ddPacks', root);
      const rows = r.rows || [];
      U.w(root, '#ddQn', rows.length + ' 个包', 'textContent');
      if (!rows.length) { box.innerHTML = '<div class="tiny muted">没有匹配的包。' + esc(r.dir || '') + '</div>'; return; }
      box.innerHTML = rows.map((x, i) =>
        '<div class="row" data-i="' + i + '" style="padding:7px 4px;border-bottom:1px solid #eef1f6;cursor:pointer;align-items:flex-start">' +
          '<div style="min-width:0;flex:1"><div style="font-size:12.5px;font-weight:600;word-break:break-all">' + esc(x.name) + '</div>' +
          '<div class="tiny muted">' + esc(x.dir) + ' · ' + (x.rows || 0) + ' 行 × ' + (x.cols || 0) + ' 列 · ' + Math.round((x.size || 0) / 1024) + 'KB · ' + esc(x.mtime || '') + '</div>' +
          '</div></div>').join('');
      U.qa('[data-i]', box).forEach(el => el.onclick = () => openPack(rows[+el.dataset.i]));
      if (!cur && rows.length) openPack(rows[0]);
    };

    U.q('#ddQGo', root).onclick = () => load(U.q('#ddQ', root).value.trim());
    U.q('#ddQ', root).onkeydown = (e) => { if (e.key === 'Enter') load(e.target.value.trim()); };
    load('');
  };

  /* ============================================================ ④ 上架执行 */
  P.ddPublish = async (root) => {
    root.innerHTML =
      CARD('上架执行 · ego 自动化跑抖店后台',
        '<div class="tb" style="margin:0 0 8px">' +
          '<select id="ddScript" style="min-width:320px;max-width:100%"><option value="">读取脚本中…</option></select>' +
          '<button id="ddRun" class="primary">开始上架</button>' +
          '<span class="sp"></span><span id="ddJob" class="tiny muted">空闲</span></div>' +
        '<div class="needbox">脚本在 ego 的 AI 专属空间里跑，不占用你自己的窗口和标签页。四段固定动作：' +
          '传图/标题/类目 → 属性 → 规格价格库存 → 填写检查→存草稿。' +
          '跑完只落草稿箱，不会替你点「发布商品」。</div>' +
        '<div id="ddLog" style="height:420px;overflow:auto;background:#0f172a;color:#cbd5e1;padding:10px;' +
          'border-radius:8px;white-space:pre-wrap;font:12px/1.6 ui-monospace,Consolas,monospace;margin-top:8px"></div>') +
      CARD('自检', '<div id="ddChk"><div class="tiny muted">读取中…</div></div>', 'margin-top:12px');

    const log = (s) => {
      const el = U.q('#ddLog', root);
      if (!el) return;
      el.textContent += (el.textContent ? '\n' : '') + s;
      el.scrollTop = el.scrollHeight;
    };

    U.get('/api/dd/check').then(r => {
      U.w(root, '#ddChk', (r.items || []).map(i =>
        '<span class="tag ' + (i.ok ? 'ok' : 'err') + '">' + esc(i.name) + (i.ok ? ' ✓' : ' ✗') + '</span>' +
        '<span class="tiny muted" style="margin-right:14px">' + esc(String(i.detail).slice(-52)) + '</span>').join(''),
        'innerHTML');
      (r.items || []).forEach(i => { if (!i.ok) log('[自检] ✗ ' + i.name + ' — ' + i.detail); });
    }).catch(e => log('[自检失败] ' + e.message));

    U.get('/api/dd/script').then(r => {
      const sel = U.q('#ddScript', root);
      if (!r.dir_ok) { sel.innerHTML = '<option value="">脚本目录不存在：' + esc(r.dir || '') + '</option>'; return; }
      const ss = r.scripts || [];
      sel.innerHTML = ss.length ? ss.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('')
        : '<option value="">目录里没有 .mjs</option>';
      const pref = ss.find(s => /^publish/.test(s)) || ss[0];
      if (pref) sel.value = pref;
    }).catch(e => log('[脚本读取失败] ' + e.message));

    let timer = null;
    const poll = () => {
      U.get('/api/dd/job').then(r => {
        const j = r.job || {};
        const el = U.q('#ddJob', root);
        if (el) el.textContent = j.state === 'running' ? '运行中 · ' + (j.started || '') :
          j.state === 'done' ? '完成 · 退出码 ' + j.code : j.state === 'failed' ? '失败 · 退出码 ' + j.code : '空闲';
        if (j.state === 'running' && j.tail && j.tail.length) {
          const el2 = U.q('#ddLog', root);
          if (el2) { el2.textContent = j.tail.join('\n'); el2.scrollTop = el2.scrollHeight; }
        }
        if (j.state !== 'running' && timer) { clearInterval(timer); timer = null; }
      }).catch(() => { });
    };

    U.q('#ddRun', root).onclick = async () => {
      const script = U.q('#ddScript', root).value;
      if (!script) { U.toast('没有可跑的脚本', false); return; }
      if (!confirm('跑这个脚本？\n' + script + '\n\n它会在抖店后台真实操作（建品/存草稿），不会自动点发布。')) return;
      log('=== 启动 ' + script + ' ===');
      const r = await U.post('/api/dd/run', { script });
      if (!r.ok) { log('[启动失败] ' + (r.error || '')); return; }
      log('[已启动] id=' + r.id);
      if (timer) clearInterval(timer);
      timer = setInterval(poll, 1500);
    };
    poll();
  };

  /* ============================================================ ⑤ 跳转 */
  P.ddLinks = async (root) => {
    root.innerHTML = CARD('抖店后台 · 改东西从这跳',
      '<div class="needbox" style="margin-bottom:8px">标「未验证」的入口我没有实测打开过，地址可能变；点了打不开就回后台首页找。</div>' +
      '<div id="ddLbox"><div class="tiny muted">读取中…</div></div>');
    U.get('/api/dd/links').then(r => {
      U.w(root, '#ddLbox', (r.rows || []).map(x =>
        '<div class="row" style="padding:8px 0;border-bottom:1px solid #eef1f6;align-items:flex-start">' +
          '<div style="min-width:0;flex:1">' +
          '<a href="' + esc(x.url) + '" target="_blank" rel="noopener" style="font-size:12.5px;font-weight:600">' +
            esc(x.name) + ' ↗</a>' + (x.verified ? '' : ' <span class="tag warn">未验证</span>') +
          '<div class="tiny muted">' + esc(x.note || '') + '</div></div></div>').join(''), 'innerHTML');
    }).catch(e => U.w(root, '#ddLbox', '<div class="err">读取失败：' + esc(e.message) + '</div>', 'innerHTML'));
  };
})();
