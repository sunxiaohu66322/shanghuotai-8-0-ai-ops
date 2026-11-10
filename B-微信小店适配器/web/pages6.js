/* 6.0 新增：图片编辑（照片墙 + 框选修补 + 检测打码）+ 微店后台管理序列
   依赖 util.js 的 U、pages.js 建立的 window.PAGES；本文件只往 PAGES 上挂页面。 */
(function () {
  const { esc } = U;
  const P = window.PAGES || (window.PAGES = {});

  const PS = { root: 0, page: 0, size: 60, total: 0, rows: [], sel: new Set(), cur: null, rects: [], engine: 'lama', pad: 2 };

  function norm(r, w, h) { return { x: r.x / w, y: r.y / h, w: r.w / w, h: r.h / h }; }

  /* ---------------- 照片墙 · 框选修补 ---------------- */
  P.photo = (root) => {
    root.innerHTML =
      '<div class="card">' +
      '<div class="card-h">照片墙 · 框选修补 <span class="tag ok">LaMa / TELEA / NS</span>' +
      '<span class="tiny" id="phLama">检查模型…</span></div>' +
      '<div class="tb">' +
      '<select id="phRoot"></select>' +
      '<button id="phReload" class="ghost">刷新</button>' +
      '<span class="sp"></span>' +
      '<span class="selinfo">已选 <b id="phSelN">0</b> 张 / 共 <b id="phTot">0</b> 张</span>' +
      '<button id="phAll" class="ghost">全选本页</button>' +
      '<button id="phNone" class="ghost">清空选择</button>' +
      '</div>' +
      '<p class="muted">用法：点任意一张进入编辑 → 在图上拖出矩形（可连拖多个，框会一直累积）→ 点「修补本张」；' +
      '要一次处理整组图，先勾选多张，再框一次，点「应用到选中」——框按比例套用到每张图，尺寸不同也对得上。</p>' +
      '<div id="phGrid" class="phgrid"><div class="loading">读取图库…</div></div>' +
      '<div class="tb mt12"><button id="phMore" class="ghost">加载更多</button></div>' +
      '</div>' +
      '<div id="phEdit" class="pedit" hidden></div>';

    const sel = U.q('#phRoot', root);
    U.get('/api/photo/roots').then(r => {
      if (!U.w(root, '#phRoot', (r.roots || []).map(x => '<option value="' + x.idx + '"' + (x.exists ? '' : ' disabled') + '>' +
        esc(x.name) + (x.exists ? '' : '（不存在）') + '</option>').join(''), 'innerHTML')) return;
      loadGrid(root, true);
    });
    U.get('/api/photo/status').then(r => {
      U.w(root, '#phLama', r.lama ? 'LaMa 就绪 · CPU' : '<span class="tag bad">LaMa 不可用</span> ' + esc(r.lama_err || ''), 'innerHTML');
    });

    sel.onchange = () => { PS.root = Number(sel.value); PS.sel.clear(); loadGrid(root, true); };
    U.q('#phReload', root).onclick = () => loadGrid(root, true);
    U.q('#phMore', root).onclick = () => loadGrid(root, false);
    U.q('#phAll', root).onclick = () => { PS.rows.forEach(x => PS.sel.add(x.id)); paintGrid(root); };
    U.q('#phNone', root).onclick = () => { PS.sel.clear(); paintGrid(root); };
  };

  async function loadGrid(root, reset) {
    if (reset) { PS.page = 0; PS.rows = []; PS.sel.clear(); U.w(root, '#phGrid', '<div class="loading">读取图库…</div>', 'innerHTML'); }
    const r = await U.get('/api/photo/list?root=' + PS.root + '&page=' + PS.page + '&size=' + PS.size);
    PS.total = r.total || 0;
    PS.rows = reset ? (r.rows || []) : PS.rows.concat(r.rows || []);
    if ((r.rows || []).length) PS.page++;
    paintGrid(root);
  }

  function paintGrid(root) {
    const g = U.q('#phGrid', root);
    if (!g) return;
    if (!PS.rows.length) { g.innerHTML = '<div class="empty">这个图库里没有图片</div>'; return; }
    g.innerHTML = PS.rows.map(x =>
      '<div class="phcell' + (PS.sel.has(x.id) ? ' on' : '') + '" data-id="' + esc(x.id) + '">' +
      '<div class="phchk"><input type="checkbox" class="phck" data-id="' + esc(x.id) + '"' + (PS.sel.has(x.id) ? ' checked' : '') + '></div>' +
      '<img loading="lazy" src="/api/photo/file?root=' + PS.root + '&id=' + encodeURIComponent(x.id) + '&w=260">' +
      '<div class="phname" title="' + esc(x.name) + '">' + esc(x.name) + '</div>' +
      '<div class="phsub">' + Math.round(x.size / 1024) + ' KB · ' + esc(x.mtime) + '</div>' +
      '</div>').join('');
    U.w(root, '#phTot', PS.total, 'textContent');
    U.w(root, '#phSelN', PS.sel.size, 'textContent');
    U.qa('.phck', g).forEach(c => c.onchange = (e) => {
      e.stopPropagation();
      const id = c.dataset.id;
      c.checked ? PS.sel.add(id) : PS.sel.delete(id);
      c.closest('.phcell').classList.toggle('on', c.checked);
      U.w(root, '#phSelN', PS.sel.size, 'textContent');
    });
    U.qa('.phcell', g).forEach(cell => cell.onclick = (e) => {
      if (e.target.classList.contains('phck')) return;
      openEditor(root, cell.dataset.id);
    });
  }

  function openEditor(root, id) {
    PS.cur = id; PS.rects = [];
    const box = U.q('#phEdit', root);
    box.hidden = false;
    box.innerHTML =
      '<div class="pe-card">' +
      '<div class="pe-bar">' +
      '<b class="pe-name" title="' + esc(id) + '">' + esc(id.split('/').pop()) + '</b>' +
      '<select id="peEng"><option value="lama">LaMa（AI·推荐）</option><option value="telea">TELEA（快）</option><option value="ns">NS（平滑）</option></select>' +
      '<label class="tiny">外扩 <input id="pePad" type="number" min="0" max="40" value="2" style="width:54px"></label>' +
      '<button id="peDetect" class="ghost">检测候选</button>' +
      '<button id="peClear" class="ghost">清空框</button>' +
      '<button id="peOne" class="primary">修补本张</button>' +
      '<button id="peAll" class="danger">应用到选中（0）</button>' +
      '<span class="sp"></span><button id="peClose" class="ghost">关闭</button>' +
      '</div>' +
      '<div class="pe-stage"><img id="peImg" src="/api/photo/file?root=' + PS.root + '&id=' + encodeURIComponent(id) + '&w=1100">' +
      '<canvas id="peCv"></canvas></div>' +
      '<div class="pe-info tiny" id="peInfo">在图上按住拖出矩形（可多个）。框按比例保存，套用到整组图时自动换算。</div>' +
      '</div>';

    const img = U.q('#peImg', box), cv = U.q('#peCv', box);
    const ctx = cv.getContext('2d');
    const sync = () => { cv.width = img.clientWidth; cv.height = img.clientHeight; draw(ctx, cv); };
    if (img.complete) sync(); else img.onload = sync;
    window.addEventListener('resize', sync, { once: true });

    let drag = null, lastPos = { x: 0, y: 0 };
    const pos = (e) => {
      const r = cv.getBoundingClientRect();
      return { x: Math.max(0, Math.min(cv.width, e.clientX - r.left)), y: Math.max(0, Math.min(cv.height, e.clientY - r.top)) };
    };
    cv.onmousedown = (e) => { drag = pos(e); lastPos = drag; };
    cv.onmousemove = (e) => {
      lastPos = pos(e);
      if (!drag) return;
      draw(ctx, cv, { x: Math.min(drag.x, lastPos.x), y: Math.min(drag.y, lastPos.y), w: Math.abs(lastPos.x - drag.x), h: Math.abs(lastPos.y - drag.y) });
    };
    cv.onmouseup = () => {
      if (!drag) return;
      const r = { x: Math.min(drag.x, lastPos.x), y: Math.min(drag.y, lastPos.y), w: Math.abs(lastPos.x - drag.x), h: Math.abs(lastPos.y - drag.y) };
      if (r.w > 4 && r.h > 4) { PS.rects.push(norm(r, cv.width, cv.height)); }
      drag = null; draw(ctx, cv);
    };

    U.q('#peAll', box).onclick = () => { U.w(box, '#peAll', '应用到选中（' + (PS.sel.size || 1) + '）', 'textContent'); };
    U.q('#peEng', box).onchange = (e) => { PS.engine = e.target.value; };
    U.q('#pePad', box).onchange = (e) => { PS.pad = Number(e.target.value) || 0; };
    U.q('#peClose', box).onclick = () => { box.hidden = true; };
    U.q('#peClear', box).onclick = () => { PS.rects = []; draw(ctx, cv); };
    U.q('#peDetect', box).onclick = async () => {
      U.w(box, '#peInfo', '检测中…', 'textContent');
      const r = await U.post('/api/photo/detect', { root: PS.root, id: PS.cur });
      if (!r.ok) { U.w(box, '#peInfo', '检测失败：' + (r.error || ''), 'textContent'); return; }
      PS.rects = r.rects || [];
      draw(ctx, cv);
      U.w(box, '#peInfo', '候选 ' + r.count + ' 处 —— ' + (r.note || '') + '。确认后再点修补。', 'textContent');
    };
    U.q('#peOne', box).onclick = async () => {
      if (!PS.rects.length) { U.w(box, '#peInfo', '先框选至少一处', 'textContent'); return; }
      U.w(box, '#peInfo', '修补中（LaMa 首张约 3-25 秒）…', 'textContent');
      const r = await U.post('/api/photo/inpaint', { root: PS.root, id: PS.cur, rects: PS.rects, engine: PS.engine, pad: PS.pad });
      if (!r.ok) { U.w(box, '#peInfo', '修补失败：' + (r.error || ''), 'textContent'); return; }
      img.src = r.out + '&t=' + Date.now();
      U.w(box, '#peInfo', '已完成 · ' + esc(r.engine) + ' · ' + r.ms + ' ms · ' +
        '<a href="' + r.out + '" target="_blank">打开结果大图</a> · 原图未改动（结果另存 data/photos/out）', 'innerHTML');
    };
    U.q('#peAll', box).onclick = async () => {
      const ids = PS.sel.size ? Array.from(PS.sel) : [PS.cur];
      if (!PS.rects.length) { U.w(box, '#peInfo', '先框选至少一处', 'textContent'); return; }
      if (!confirm('把这 ' + ids.length + ' 张按同一组框修补？')) return;
      U.w(box, '#peInfo', '批量修补中：' + ids.length + ' 张…', 'textContent');
      const r = await U.post('/api/photo/batch', { root: PS.root, ids: ids, rects: PS.rects, engine: PS.engine, pad: PS.pad });
      if (!r.ok) { U.w(box, '#peInfo', '批量失败：' + (r.error || ''), 'textContent'); return; }
      U.w(box, '#peInfo', '批量完成：成功 <b>' + r.okCount + '</b> / 失败 ' + r.failCount +
        '<div class="res"><table class="tbl small"><thead><tr><th>图</th><th>结果</th><th>耗时</th></tr></thead><tbody>' +
        r.rows.map(x => '<tr><td class="tiny">' + esc(x.id.split('/').pop()) + '</td><td>' +
          (x.ok ? '<a href="' + x.out + '" target="_blank">查看</a>' : '<span class="tag bad">失败</span> ' + esc(x.error || '')) +
          '</td><td class="tiny">' + (x.ms || '') + ' ms</td></tr>').join('') + '</tbody></table></div>', 'innerHTML');
    };
  }

  function draw(ctx, cv, live) {
    ctx.clearRect(0, 0, cv.width, cv.height);
    const paint = (r, color) => {
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      ctx.strokeRect(r.x * cv.width, r.y * cv.height, r.w * cv.width, r.h * cv.height);
      ctx.fillStyle = 'rgba(220,38,38,.16)';
      ctx.fillRect(r.x * cv.width, r.y * cv.height, r.w * cv.width, r.h * cv.height);
    };
    PS.rects.forEach(r => paint(r, '#dc2626'));
    if (live) {
      ctx.strokeStyle = '#1677ff'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
      ctx.strokeRect(live.x, live.y, live.w, live.h);
      ctx.setLineDash([]);
    }
  }

  /* ---------------- 检测打码 ---------------- */
  P.maskdetect = (root, leaf) => {
    P.photo(root, leaf);
    setTimeout(() => {
      const card = U.q('.card', root);
      if (!card) return;
      const h = U.q('.card-h', card);
      if (h) h.innerHTML = '检测打码 <span class="tag warn">候选框</span><span class="tiny">点图进编辑 → 「检测候选」→ 人工确认 → 「修补本张」用 LaMa 消除</span>';
    }, 30);
  };

  /* ---------------- 微店后台 · 概览 ---------------- */
  P.wxOverview = async (root) => {
    root.innerHTML = '<div class="grid4" id="wxo"><div class="card"><div class="loading">读取…</div></div></div>' +
      '<div class="card"><div class="card-h">连接与能力</div><div id="wxCap" class="loading">检查…</div></div>' +
      '<div class="card"><div class="card-h">规格图缺口 <span class="tiny">按缺口从大到小</span></div><div id="wxSku" class="loading">读取…</div></div>';
    const [ov, ph, cats] = await Promise.all([
      U.get('/api/overview').catch(() => null), U.get('/api/photo/status').catch(() => null),
      U.get('/api/categories?limit=1').catch(() => null)
    ]);
    const bs = (ov && ov.by_status) || {};
    const si = (ov && ov.shop) || {};
    U.w(root, '#wxo', [
      ['店铺', si.name || '—', si.subject_type || ''],
      ['商品', (ov && ov.total) || 0, '未上架 ' + (bs['0'] || 0) + ' / 已上架 ' + (bs['5'] || 0) + ' / 回收站 ' + (bs['6'] || 0)],
      ['类目（末级）', (cats && cats.total) || '—', '建品必须选末级'],
      ['图片修补', ph && ph.lama ? 'LaMa 就绪' : '不可用', '照片墙 → 框选 → 消除']
    ].map(c => '<div class="card stat"><div class="k">' + esc(c[0]) + '</div><div class="v">' + esc(c[1]) + '</div><div class="tiny">' + esc(c[2]) + '</div></div>').join(''), 'innerHTML');
    U.w(root, '#wxCap', '<table class="tbl small"><thead><tr><th>能力</th><th>状态</th><th>入口</th></tr></thead><tbody>' +
      [['商品读写', '可用', '/products · /create · 批量修改'], ['类目树 + 属性 + 资质', '可用', '/wx/categories'],
      ['图片上传（微信素材库）', '可用', '/wx/materials'], ['订单（7 天窗口）', '可用', '/orders'],
      ['运费模板 / 优惠券', '只读', '/freight · /coupons'], ['图片消除（LaMa）', ph && ph.lama ? '可用' : '不可用', '/photo/wall']]
        .map(r => '<tr><td>' + esc(r[0]) + '</td><td>' + esc(r[1]) + '</td><td class="tiny">' + esc(r[2]) + '</td></tr>').join('') +
      '</tbody></table>', 'innerHTML');
    /* 规格图缺口清单（/api/overview 里已带 top 20） */
    const sk = (ov && ov.sku) || {};
    if (!sk.sku_total) {
      U.w(root, '#wxSku', '<p class="muted">还没有扫描记录。命令行走一次全店扫描后会出现在这里。</p>', 'innerHTML');
    } else {
      const rows = (sk.top || []).map(r => '<tr><td class="tiny">' + esc(r.pid) + '</td>' +
        '<td>' + U.statusTag(r.st) + '</td><td><b>' + esc(r.noimg) + '</b> / ' + esc(r.total) + '</td>' +
        '<td class="tiny">' + esc(r.title) + '</td></tr>').join('');
      U.w(root, '#wxSku',
        '<div class="needbox"><b>全店 ' + sk.sku_total + ' 个规格，缺图 ' + sk.sku_noimg + ' 个</b>（有图率 ' +
        sk.img_rate + '%）· 涉及 ' + sk.bad_products + ' 件商品 · 扫描于 ' + esc(sk.at || '未知') + '</div>' +
        '<table class="tbl small"><thead><tr><th>商品ID</th><th>状态</th><th>缺图/总规格</th><th>标题</th></tr></thead><tbody>' +
        rows + '</tbody></table>', 'innerHTML');
    }
  };

  /* ---------------- 微店后台 · 类目属性资质（贯通） ---------------- */
  P.wxCats = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">类目 · 属性 · 资质 <span class="tag ok">贯通</span></div>' +
      '<div class="tb"><input id="wcq" placeholder="搜类目名 / cat_id"><button id="wcb" class="primary">搜索</button>' +
      '<span class="tiny">选中一行 → 右侧看该类目的必填属性与资质要求，建品时直接用</span></div>' +
      '<div class="wccols"><div id="wcList" class="loading">读取…</div><div id="wcDetail" class="muted">选一个类目看详情</div></div></div>';
    const load = async () => {
      const q = U.q('#wcq', root).value.trim();
      const r = await U.get('/api/categories?limit=200&q=' + encodeURIComponent(q));
      U.w(root, '#wcList', '<table class="tbl small"><thead><tr><th>类目路径</th><th>cat_id</th><th>资质</th></tr></thead><tbody>' +
        (r.rows || []).map(c => '<tr data-cat="' + esc(c.cat_id) + '" class="crow"><td>' + esc(c.path) + '</td>' +
          '<td class="tiny">' + esc(c.cat_id) + '</td><td class="tiny">' + ((c.qua || []).map(q2 => q2.qua_id).join(',') || '—') + '</td></tr>').join('') +
        '</tbody></table>', 'innerHTML');
      U.qa('.crow', root).forEach(tr => tr.onclick = () => detail(tr.dataset.cat));
    };
    const detail = async (id) => {
      if (!U.w(root, '#wcDetail', '<div class="loading">读取类目 ' + esc(id) + ' 详情…</div>', 'innerHTML')) return;
      const r = await U.get('/api/category?id=' + encodeURIComponent(id));
      const d = (r && r.data) || {};
      const info = d.info || {};
      const at = d.attr || {};
      const attrs = at.product_attr_list || [];
      const sales = at.sale_attr_list || [];
      const row = (a, kind) => '<tr><td>' + esc(a.name || '') + '</td><td class="tiny">' + esc(a.type || a.type_v2 || '') + '</td>' +
        '<td>' + (a.is_required ? '<span class="tag warn">必填</span>' : '可选') + '</td>' +
        '<td class="tiny">' + esc(String(a.value || '').split(';').slice(0, 12).join(' / ') || (a.append_allowed ? '可自定义' : '—')) + '</td></tr>';
      const head = '<div class="card-h">' + esc(info.name || '') + ' <span class="tiny">cat_id ' + esc(id) + '</span></div>' +
        '<div class="needbox"><b>保证金</b> ' + esc(at.deposit ? (Number(at.deposit) / 100).toFixed(2) + ' 元' : '—') +
        ' · <b>品牌</b> ' + esc((at.brand_list || []).length ? (at.brand_list || []).length + ' 个可选' : '无限制') + '</div>';
      const tbl = (title, list) => '<div class="needbox mt12"><b>' + title + '（' + list.length + '）</b></div>' +
        (list.length ? '<table class="tbl small"><thead><tr><th>属性</th><th>类型</th><th>必填</th><th>可选值</th></tr></thead><tbody>' +
          list.map(a => row(a)).join('') + '</tbody></table>' : '<p class="muted">该类目没有这一组属性。</p>');
      U.w(root, '#wcDetail', head + tbl('商品属性', attrs) + tbl('销售属性（规格）', sales) +
        '<details class="mt12"><summary class="tiny">原始返回</summary><pre class="raw">' + esc(JSON.stringify(d).slice(0, 3000)) + '</pre></details>', 'innerHTML');
    };
    U.q('#wcb', root).onclick = load;
    U.q('#wcq', root).onkeydown = e => { if (e.key === 'Enter') load(); };
    load();
  };

  /* ---------------- 微店后台 · 素材库（微信上传记录） ---------------- */
  P.wxMaterials = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">素材库 <span class="tag ok">上传到微信 CDN</span></div>' +
      '<p class="muted">上传即调 <code>/shop/ec/basics/img/upload</code>，返回 mmecimage.cn 永久地址。这里列出本台传过的图，可直接复制链接填进商品。</p>' +
      '<label class="fl">选图上传<input type="file" id="wmFile" accept="image/*" multiple></label>' +
      '<div id="wmOut" class="mt12"></div></div>' +
      '<div class="card"><div class="card-h">上传记录</div><div id="wmList" class="loading">读取…</div></div>';
    const load = async () => {
      const r = await U.get('/api/uploads?limit=100');
      const rows = r.rows || [];
      U.w(root, '#wmList', rows.length
        ? '<table class="tbl small"><thead><tr><th>时间</th><th>文件</th><th>大小</th><th>微信地址</th></tr></thead><tbody>' +
        rows.map(x => '<tr><td class="tiny">' + esc(x.ts) + '</td><td>' + esc(x.name) + '</td><td class="tiny">' + (x.size || 0) + '</td>' +
          '<td class="tiny"><a href="' + esc(x.url) + '" target="_blank">' + esc((x.url || '').slice(0, 60)) + '</a></td></tr>').join('') + '</tbody></table>'
        : '<p class="muted">还没有上传记录</p>', 'innerHTML');
    };
    U.q('#wmFile', root).onchange = async (e) => {
      const files = Array.from(e.target.files || []);
      if (!files.length) return;
      const out = U.q('#wmOut', root);
      if (out) out.innerHTML = '<div class="loading">上传 ' + files.length + ' 张…</div>';
      const done = [];
      for (const f of files) {
        const fd = new FormData(); fd.append('file', f);
        try {
          const r = await fetch('/api/upload', { method: 'POST', body: fd }).then(x => x.json());
          done.push({ name: f.name, ok: r.ok, url: r.img_url || '', err: r.error || '' });
        } catch (err) {
          done.push({ name: f.name, ok: false, err: String(err) });
        }
      }
      U.w(root, '#wmOut', '<table class="tbl small"><thead><tr><th>文件</th><th>结果</th></tr></thead><tbody>' +
        done.map(d => '<tr><td>' + esc(d.name) + '</td><td class="tiny">' + (d.ok
          ? '<a href="' + esc(d.url) + '" target="_blank">' + esc(d.url) + '</a>'
          : '<span class="tag bad">失败</span> ' + esc(d.err)) + '</td></tr>').join('') + '</tbody></table>', 'innerHTML');
      load();
    };
    load();
  };
  /* ---------------- 新建上货（类目 · 属性贯通版，覆盖 pages.js 的同名页）---------------- */
  P.create = (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">新建商品 <span class="tag warn">会真实写入店铺</span>' +
      '<span class="tiny">类目、属性、图片全部走同一套数据</span></div>' +
      '<div class="flrow">' +
      '<label class="fl">标题<input id="cTitle" placeholder="≤60 字"></label>' +
      '<label class="fl">末级类目 cat_id<input id="cCat" placeholder="如 547511"></label>' +
      '<label class="fl">价格（元）<input id="cPrice" type="number" value="99"></label>' +
      '<label class="fl">库存<input id="cStock" type="number" value="10"></label>' +
      '</div>' +
      '<div class="btns"><button id="cLoad" class="ghost">校验类目并载入必填属性</button>' +
      '<button id="cUp" class="ghost">上传主图</button><button id="cGo" class="danger">创建商品</button></div>' +
      '<div class="card" style="margin-top:10px;background:#f8fafc"><div class="card-h">上架目标店铺 ' +
      '<span class="tiny muted">勾一家 = 只上那家；勾多家 = 同一件商品同时上到多家，逐家报结果</span></div>' +
      '<div id="cShops" class="flrow tiny">读取账号中…</div>' +
      '<div class="tiny muted mt8">当前页面店号只决定「类目/属性读取」用哪家；真正上架以这里勾的为准。' +
      '主图会分别上传到每家店，各家用自己的图片地址创建，绝不串用。</div></div>' +
      '<label class="fl mt12">主图<input type="file" id="cImg" accept="image/*"></label>' +
      '<div id="cAttr"></div><div id="cOut" class="mt12"></div></div>';
    let imgUrl = '', REQUIRED = [], imgFile = null, SHOPS = [];
    const drawShops = async () => {
      const box = U.q('#cShops', root);
      if (!box) return;
      const r = await U.get('/api/shops');
      /* 三家店：2 微店(wx_api/默认) + 1 抖店(doudian)。
         抖店没有免签建品接口 → 勾它 = 生成 16 列上架包入队，不是即时建品。 */
      SHOPS = (r.shops || []).filter(s => s.enabled !== false && s.kind !== 'browser');
      const cur = U.shopId() || r.current || '';
      if (!SHOPS.length) { box.innerHTML = '<span class="err">还没有可用的店铺，先去「账号管理」添加</span>'; return; }
      box.innerHTML = SHOPS.map(s =>
        '<label style="display:flex;align-items:center;gap:5px;margin-right:14px">' +
        '<input type="checkbox" class="cShop" value="' + esc(s.appid) + '"' +
        (s.appid === cur ? ' checked' : '') + '>' + esc(s.name || s.mask) +
        (s.kind === 'doudian' ? ' <span class="tag warn">入队·非即时</span>' : '') +
        ' <span class="tiny muted">' + esc(s.mask) + '</span></label>').join('') +
        '<div class="tiny muted" style="width:100%;margin-top:4px">共 ' + SHOPS.length +
        ' 家店：微店即时建品；抖店生成 16 列上架包入队，由 7.0「上架执行」发售卖中。</div>';
    };
    drawShops();
    U.q('#cLoad', root).onclick = async () => {
      const id = U.q('#cCat', root).value.trim();
      if (!id) return U.toast('先填 cat_id', false);
      const r = await U.get('/api/category?id=' + encodeURIComponent(id));
      const d = (r && r.data) || {};
      const at = d.attr || {};
      const all = (at.product_attr_list || []).concat(at.sale_attr_list || []);
      REQUIRED = all.filter(a => a.is_required);
      U.w(root, '#cAttr', '<div class="needbox"><b>' + esc((d.info || {}).name || '') + '</b> · 属性 ' + all.length +
        ' 条，其中必填 <b>' + REQUIRED.length + '</b> 条 · 保证金 ' + esc(at.deposit ? (Number(at.deposit) / 100).toFixed(2) + ' 元' : '—') + '</div>' +
        '<table class="tbl small"><thead><tr><th>属性</th><th>必填</th><th>值</th></tr></thead><tbody>' +
        all.map(a => '<tr><td>' + esc(a.name) + '</td><td>' + (a.is_required ? '<span class="tag warn">必填</span>' : '可选') + '</td>' +
          '<td><input data-attr="' + esc(a.name) + '" placeholder="' + esc(String(a.value || '').split(';').slice(0, 4).join(' / ') || '可自定义') + '"></td></tr>').join('') +
        '</tbody></table>', 'innerHTML');
    };
    U.q('#cUp', root).onclick = async () => {
      const f = U.q('#cImg', root).files[0];
      if (!f) return U.toast('先选图片', false);
      imgFile = f;
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch('/api/upload', { method: 'POST', headers: U.hdr(), body: fd }).then(x => x.json());
      imgUrl = r.img_url || '';
      U.w(root, '#cOut', r.ok ? '<div class="ok">图片已上传到当前店：' + esc(imgUrl) + '</div>' : '<div class="err">' + esc(r.error) + '</div>', 'innerHTML');
    };
    U.q('#cGo', root).onclick = async () => {
      const title = U.q('#cTitle', root).value.trim();
      const cat = U.q('#cCat', root).value.trim();
      const price = Math.round(Number(U.q('#cPrice', root).value) * 100);
      const stock = Number(U.q('#cStock', root).value);
      if (!title || !cat || !price) return U.toast('标题/类目/价格都要有', false);
      const targets = U.qa('.cShop:checked', root).map(i => i.value);
      if (!targets.length) return U.toast('先在「上架目标店铺」勾至少一家', false);
      imgFile = U.q('#cImg', root).files[0] || imgFile;
      if (!imgFile) return U.toast('先选一张主图（会分别传到每家目标店）', false);
      const attrs = U.qa('[data-attr]', root).filter(i => i.value.trim())
        .map(i => ({ attr_key: i.dataset.attr, attr_value: i.value.trim() }));
      const missing = REQUIRED.filter(a => !attrs.some(x => x.attr_key === a.name)).map(a => a.name);
      if (missing.length && !confirm('必填属性还差：' + missing.join('、') + '。仍然提交？')) return;
      const go = U.q('#cGo', root);
      U.busy(go, true);
      const out = U.q('#cOut', root);
      if (out) out.innerHTML = '<div class="tiny">共 ' + targets.length + ' 家店，正在逐家上传主图…</div>';
      const items = [];       // 微店：传图 + product/add
      const ddItems = [];     // 抖店：生成 16 列上架包入队
      const errs = [];
      for (const sid of targets) {
        const meta = SHOPS.find(s => s.appid === sid) || {};
        if (meta.kind === 'doudian') {
          ddItems.push({ shop: sid, name: meta.name || sid });
          continue;
        }
        const fd = new FormData(); fd.append('file', imgFile);
        let u;
        try {
          u = await fetch('/api/upload', { method: 'POST', headers: U.hdr(sid), body: fd }).then(x => x.json());
        } catch (e) { u = { ok: false, error: String(e) }; }
        if (!u.ok || !u.img_url) {
          errs.push(sid + ' 图片上传失败：' + (u.error || '无 img_url'));
          continue;
        }
        items.push({ shop: sid, img: u.img_url, payload: {
          title, head_imgs: [u.img_url], cats: [{ cat_id: cat }], attrs,
          skus: [{ sale_price: price, stock_num: stock, sku_attrs: [] }]
        } });
      }
      if (!items.length && !ddItems.length) {
        if (out) out.innerHTML = '<div class="err">全部主图上传失败，未创建任何商品。<br>' + esc(errs.join('<br>')) + '</div>';
        U.busy(go, false); return;
      }
      const results = [];
      for (const it of items) {
        let rr;
        try {
          rr = await U.post('/api/product/add', { payload: it.payload }, it.shop);
        } catch (e) { rr = { ok: false, error: String(e) }; }
        const name = (SHOPS.find(s => s.appid === it.shop) || {}).name || it.shop;
        const good = !!(rr && rr.ok);
        results.push({ shop: it.shop, name, ok: good,
                       data: good ? rr.data : null,
                       error: good ? '' : (rr && (rr.error || rr.errmsg) || '提交失败') });
      }
      /* 抖店：不传微信图、不调 product/add，只把这件货落成 16 列 xlsx 入队。
         轮播图沿用本轮微店上传拿到的公网 URL（若一家微店都没勾，就留空并在入队结果里标出
         「缺图」——绝不拿本地路径冒充公网地址，也不虚构链接）。 */
      const pubImgs = items.map(x => x.img).filter(Boolean);
      for (const it of ddItems) {
        let rr;
        try {
          rr = await U.post('/api/dd/enqueue', { item: {
            title, cat, price, stock,
            head_imgs: pubImgs, detail_imgs: [], attrs
          } });
        } catch (e) { rr = { ok: false, error: String(e) }; }
        const good = !!(rr && rr.ok);
        results.push({ shop: it.shop, name: it.name + '（入队）', ok: good,
                       data: good ? { queued: true, xlsx: rr.xlsx, pid: rr.pid,
                                      img_warn: pubImgs.length ? '' : '缺公网主图，需在 5.0 出表补齐' } : null,
                       error: good ? '' : (rr && (rr.error || '入队失败')) });
      }
      const r = { ok: results.every(x => x.ok), ok_count: results.filter(x => x.ok).length,
                  total: results.length, results };
      const rows = (r.results || []).map(x =>
        '<tr><td>' + esc(x.name || x.shop) + '</td><td>' +
        (x.ok ? '<span class="tag ok">成功</span>' : '<span class="tag err">失败</span>') +
        '</td><td class="tiny">' + esc(x.ok ? JSON.stringify(x.data || {}).slice(0, 160) : (x.error || '')) + '</td></tr>').join('');
      if (out) out.innerHTML =
        '<div class="' + (r.ok ? 'ok' : 'warn') + '">上架结果：成功 ' + (r.ok_count || 0) + ' / ' + (r.total || 0) + ' 家' +
        (errs.length ? '；另有 ' + errs.length + ' 家上传主图失败，未提交' : '') + '</div>' +
        '<table class="tbl small"><thead><tr><th>店铺</th><th>结果</th><th>返回</th></tr></thead><tbody>' + rows + '</tbody></table>' +
        (errs.length ? '<div class="err tiny">' + esc(errs.join('；')) + '</div>' : '');
      U.busy(go, false);
    };
  };

  /* ================= 6.0 · 上架平台 =================
     后续上货、以及跳去网页后台改东西，都从这个页面走。
     数据全部来自 /api/publish/*（后端 publish.py）；队列只读，不移动任何文件。 */
  const PB = { pack: null, timer: null, curScript: '' };

  P.publish = async (root) => {
    if (PB.timer) { clearInterval(PB.timer); PB.timer = null; }

    root.innerHTML =
      '<div id="pbChk" class="statbar"><span class="tiny muted">自检中…</span></div>' +
      '<div style="display:grid;grid-template-columns:340px minmax(0,1fr);gap:12px;align-items:start">' +
        '<div>' +
          '<div class="card"><div class="card-h">① 待上架队列 · 上货台包</div>' +
            '<div class="tb" style="margin:0 0 8px"><input id="pbQ" placeholder="搜包名…">' +
              '<button id="pbQGo">搜</button><span class="sp"></span><span class="tiny" id="pbQn"></span></div>' +
            '<div id="pbPacks" style="max-height:330px;overflow:auto"><div class="tiny muted">读取中…</div></div>' +
          '</div>' +
          '<div class="card" style="margin-top:12px"><div class="card-h">② 网页后台 · 改东西从这跳</div>' +
            '<div id="pbLinks"><div class="tiny muted">读取中…</div></div>' +
          '</div>' +
        '</div>' +
        '<div>' +
          '<div class="card"><div class="card-h" id="pbPackH">③ 包内商品<span class="tiny muted"> · 左侧点一个包</span></div>' +
            '<div id="pbPackBody" style="max-height:360px;overflow:auto"><div class="tiny muted">还没选包。</div></div>' +
          '</div>' +
          '<div class="card" style="margin-top:12px"><div class="card-h">④ 上架执行</div>' +
            '<div class="tb" style="margin:0 0 8px">' +
              '<select id="pbScript" style="min-width:300px;max-width:100%"><option value="">读取脚本中…</option></select>' +
              '<button id="pbRun" class="primary">开始上架</button>' +
              '<span class="sp"></span><span id="pbJob" class="tiny muted">空闲</span>' +
            '</div>' +
            '<div class="needbox">上架跑的是抖店后台自动化（ego 专属空间，不占用你自己的窗口）。脚本固化了四段：传图/标题/类目 → 属性 → 规格价格库存 → 填写检查→存草稿。跑起来日志实时滚在下面。</div>' +
            '<div id="pbLog" style="height:250px;overflow:auto;background:#0f172a;color:#cbd5e1;padding:10px;border-radius:8px;white-space:pre-wrap;font:12px/1.6 ui-monospace,Consolas,monospace"></div>' +
          '</div>' +
        '</div>' +
      '</div>';

    const log = (s) => {
      const el = U.q('#pbLog', root);
      if (!el) return;
      el.textContent += (el.textContent ? '\n' : '') + s;
      el.scrollTop = el.scrollHeight;
    };

    /* ---- 自检 ---- */
    U.get('/api/publish/check').then(r => {
      const box = U.q('#pbChk', root);
      if (!box) return;
      box.innerHTML = (r.items || []).map(i =>
        '<span class="tag ' + (i.ok ? 'ok' : 'err') + '">' + esc(i.name) + (i.ok ? ' ✓' : ' ✗') + '</span>' +
        '<span class="tiny muted" style="margin-right:14px" title="' + esc(String(i.detail)) + '">…' +
          esc(String(i.detail).slice(-46)) + '</span>'
      ).join('') + '<span class="sp"></span><span class="tiny ' + (r.ok ? 'ok' : 'err') + '">' +
        (r.ok ? '三件齐备，可以跑' : '缺东西，先补') + '</span>';
    }).catch(e => U.w(root, '#pbChk', '<span class="err">自检失败：' + esc(e.message) + '</span>', 'innerHTML'));

    /* ---- ① 包列表 ---- */
    const loadPacks = async (kw) => {
      const r = await U.get('/api/publish/packs?q=' + encodeURIComponent(kw || '') + '&limit=80');
      const box = U.q('#pbPacks', root);
      if (!box) return;
      const rows = r.rows || [];
      U.w(root, '#pbQn', rows.length + ' 个包', 'textContent');
      if (!rows.length) { box.innerHTML = '<div class="tiny muted">没有匹配的包。' + esc(r.dir || '') + '</div>'; return; }
      box.innerHTML = rows.map((x, i) =>
        '<div class="row" data-i="' + i + '" style="padding:7px 4px;border-bottom:1px solid #eef1f6;cursor:pointer;align-items:flex-start">' +
          '<div style="min-width:0;flex:1">' +
            '<div style="font-size:12.5px;font-weight:600;word-break:break-all">' + esc(x.name) + '</div>' +
            '<div class="tiny muted">' + esc(x.dir) + ' · ' + (x.rows || 0) + ' 行 × ' + (x.cols || 0) + ' 列 · ' + Math.round((x.size || 0) / 1024) + 'KB · ' + esc(x.mtime || '') + '</div>' +
          '</div></div>'
      ).join('');
      U.qa('[data-i]', box).forEach(el => el.onclick = () => openPack(rows[+el.dataset.i]));
      /* 一进页面就给个默认包，省得 ③ 空着等你去点 */
      if (!PB.pack && rows.length) openPack(rows[0]);
    };

    /* ---- ③ 包内商品 ---- */
    const openPack = async (pk) => {
      PB.pack = pk;
      U.w(root, '#pbPackH', '③ ' + pk.name + '<span class="tiny muted"> · ' + pk.rows + ' 行 × ' + pk.cols + ' 列</span>', 'innerHTML');
      U.w(root, '#pbPackBody', '<div class="tiny muted">读取中…</div>', 'innerHTML');
      const r = await U.get('/api/publish/pack?path=' + encodeURIComponent(pk.path) + '&limit=200');
      const box = U.q('#pbPackBody', root);
      if (!box) return;
      if (!r.ok) { box.innerHTML = '<div class="err">' + esc(r.error) + '</div>'; return; }
      const head = r.head || [];
      box.innerHTML = '<table class="tt" style="font-size:11.5px"><thead><tr>' +
        head.map(h => '<th>' + esc(h) + '</th>').join('') + '</tr></thead><tbody>' +
        (r.rows || []).map(rw => '<tr>' + head.map((_, i) => {
          let v = rw[i] == null ? '' : String(rw[i]);
          if (/^https?:/.test(v)) v = v.slice(0, 34) + '…';
          return '<td>' + esc(v.slice(0, 60)) + '</td>';
        }).join('') + '</tr>').join('') +
        '</tbody></table><div class="tiny muted" style="margin-top:6px">共 ' + r.total + ' 行，显示前 ' + (r.rows || []).length + ' 行（只读）</div>';
    };

    /* ---- ② 网页后台跳转 ---- */
    U.get('/api/publish/links').then(r => {
      const box = U.q('#pbLinks', root);
      if (!box) return;
      box.innerHTML = (r.rows || []).map(x =>
        '<div class="row" style="padding:7px 0;border-bottom:1px solid #eef1f6;align-items:flex-start">' +
          '<div style="min-width:0;flex:1">' +
            '<a href="' + esc(x.url) + '" target="_blank" rel="noopener" style="font-size:12.5px;font-weight:600">' + esc(x.name) + ' ↗</a>' +
            (x.verified ? '' : ' <span class="tag warn">未验证</span>') +
            '<div class="tiny muted">' + esc(x.note || '') + '</div>' +
          '</div></div>'
      ).join('');
    }).catch(() => U.w(root, '#pbLinks', '<div class="err">跳转表读取失败</div>', 'innerHTML'));

    /* ---- ④ 上架执行 ---- */
    U.get('/api/publish/script').then(r => {
      const sel = U.q('#pbScript', root);
      if (!sel) return;
      if (!r.dir_ok) { sel.innerHTML = '<option value="">脚本目录不存在</option>'; return; }
      const ss = (r.scripts || []);
      sel.innerHTML = ss.length
        ? ss.map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('')
        : '<option value="">目录里没有 .mjs</option>';
      const pref = ss.find(s => /^publish/.test(s)) || ss[0] || '';
      if (pref) sel.value = pref;
    }).catch(() => U.w(root, '#pbScript', '<option value="">读取失败</option>', 'innerHTML'));

    U.q('#pbQGo', root).onclick = () => loadPacks(U.q('#pbQ', root).value.trim());
    U.q('#pbQ', root).onkeydown = (e) => { if (e.key === 'Enter') loadPacks(e.target.value.trim()); };

    U.q('#pbRun', root).onclick = async () => {
      const sel = U.q('#pbScript', root);
      const script = sel.value;
      if (!script) { U.toast('没有可跑的脚本', false); return; }
      if (!confirm('跑这个脚本？\n' + script + '\n\n它会在抖店后台真实操作（建品/存草稿），不会自动点发布。')) return;
      const btn = U.q('#pbRun', root);
      U.busy(btn, true);
      log('==> ' + script);
      const r = await U.post('/api/publish/run', { script });
      U.busy(btn, false);
      if (!r.ok) { log('[启动失败] ' + r.error); U.toast('启动失败', false); return; }
      log('[已启动] job ' + r.id + '\n[命令] ' + r.cmd);
      let n = -1;
      const tick = async () => {
        const j = await U.get('/api/publish/job');
        const el = U.q('#pbLog', root);
        if (!el) { clearInterval(PB.timer); PB.timer = null; return; }
        const job = j.job || {};
        const tail = job.tail || [];
        if (tail.length !== n) {
          el.textContent = '==> ' + script + '\n' + tail.join('\n');
          el.scrollTop = el.scrollHeight;
          n = tail.length;
        }
        const st = U.q('#pbJob', root);
        if (st) st.textContent = (job.state || 'idle') + (job.code == null ? '' : ' · exit ' + job.code);
        if (job.state === 'done' || job.state === 'failed') {
          clearInterval(PB.timer); PB.timer = null;
          U.toast(job.state === 'done' ? '脚本跑完了' : '脚本失败，退出码 ' + job.code, job.state === 'done');
        }
      };
      PB.timer = setInterval(tick, 1500);
      tick();
    };

    loadPacks('');
  };
})();



