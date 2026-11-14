/* 页面渲染：每个菜单项对应一个页面。
   批量修改组 = 同一套「商品表格 + 动作面板」，动作面板按实现程度分三档：
   real（真写）/ patch（字段补丁）/ shell（只有外壳，动作置灰并写清缺什么） */
(function () {
  const { esc, money } = U;

  const S = {
    rows: [], total: 0, byStatus: null, byStatusText: null, sel: new Set(),
    status: '', q: '', leaf: null, lastRows: []
  };
  window.PAGE_STATE = S;

  let ESPEC = null;
  async function loadSpec() {
    if (ESPEC) return ESPEC;
    try { const r = await U.get('/api/editSpec'); ESPEC = (r && r.spec) || {}; } catch (e) { ESPEC = {}; }
    return ESPEC;
  }

  /* 异步回写保护：等接口回来时用户可能已切页，容器已被清空 → 直接抛 null.innerHTML。
     一律走 paint()：容器不在就安静放弃，不报错、不写脏。 */
  function paint(root, sel, html) {
    const el = sel ? U.q(sel, root) : root;
    if (!el) return false;
    el.innerHTML = html;
    return true;
  }

  function fieldHtml(f) {    if (f.t === 'select') {
      return '<label class="fl">' + esc(f.label) + '<select name="' + f.k + '">' +
        (f.opts || []).map(o => '<option value="' + o[0] + '">' + o[1] + '</option>').join('') + '</select></label>';
    }
    if (f.t === 'textarea') {
      return '<label class="fl wide">' + esc(f.label) + '<textarea name="' + f.k + '" rows="3"></textarea></label>';
    }
    return '<label class="fl">' + esc(f.label) + '<input type="' + (f.t === 'number' ? 'number' : 'text') + '" name="' + f.k + '"></label>';
  }

  function editPanel(leaf, fields) {
    return '<div class="card act"><div class="card-h">' + esc(leaf.title) + ' <span class="tag ok">可用</span></div>' +
      '<p class="muted">整对象读取 → 就地修改 → 提交（最小字段失败时自动整对象兜底）。先预览，确认无误再写入。</p>' +
      '<div class="flrow">' + fields.map(fieldHtml).join('') + '</div>' +
      '<div class="btns"><button id="btnEPrev" class="ghost">先预览（不动数据）</button>' +
      '<button id="btnERun" class="danger">写入</button></div><div id="padRes"></div></div>';
  }

  async function runEdit(root, leaf) {
    const ids = Array.from(S.sel);
    if (!ids.length) return U.toast('先勾选商品', false);
    const card = U.q('.act', root);
    const args = {};
    U.qa('[name]', card).forEach(el => { args[el.name] = el.value; });
    const r = await U.post('/api/edit', { ids, op: leaf.act, args, dry_run: !!root.__dry });
    if (!r.ok) { U.w(root, '#padRes', '<div class="err">' + esc(r.error || '失败') + '</div>', 'innerHTML'); return; }
    U.w(root, '#padRes', resultBox(r.data.items) +
      '<div class="tiny">' + (root.__dry ? '预览（未写入）' : '真实写入') + ' · 成功 ' + r.data.ok + ' / 失败 ' + r.data.fail + '</div>', 'innerHTML');
  }

  /* ---------- 动作定义（real / patch） ---------- */
  const ACT = {
    price: {
      kind: 'batch', op: 'price', hint: '单位：元。按 SKU 逐条改，最小改动提交。',
      fields: [
        { k: 'mode', t: 'select', label: '方式', opts: [['set', '设为'], ['add', '加价'], ['sub', '减价'], ['percent', '按百分比 %']] },
        { k: 'value', t: 'number', label: '数值', def: '0' }
      ]
    },
    stock: {
      kind: 'batch', op: 'stock', hint: '按 SKU 逐条改库存，走 stock/update 增量接口。',
      fields: [
        { k: 'mode', t: 'select', label: '方式', opts: [['set', '设为'], ['add', '增加'], ['sub', '减少']] },
        { k: 'value', t: 'number', label: '数值', def: '0' }
      ]
    },
    title: {
      kind: 'batch', op: 'title', hint: '标题上限 60 字；替换格式「旧=>新」。',
      fields: [
        { k: 'mode', t: 'select', label: '方式', opts: [['set', '整体设为'], ['prefix', '加前缀'], ['suffix', '加后缀'], ['replace', '替换']] },
        { k: 'value', t: 'text', label: '内容', def: '' }
      ]
    },
    title_prefix: {
      kind: 'batch', op: 'title', mode: 'prefix', hint: '在原标题前加统一前缀。',
      fields: [{ k: 'value', t: 'text', label: '前缀', def: '' }]
    },
    status: {
      kind: 'batch', op: 'status', hint: '上架前微信会校验类目资质与必填项。',
      fields: [{ k: 'mode', t: 'select', label: '动作', opts: [['up', '上架'], ['down', '下架']] }]
    },
    delete: { kind: 'delete', hint: '删除进入回收站，可人工恢复；本工具不提供一键彻底删除。' },
    short_title: { kind: 'patch', field: 'sub_title', type: 'text', hint: '短标题（导购短标题），≤20 字。' },
    weight: { kind: 'patch', field: 'weight', type: 'number', hint: '重量单位克（g）。' },
    spu_code: { kind: 'patch', field: 'spu_code', type: 'text', hint: '商品货号（SPU 编码）。' }
  };

  /* ---------- 小组件 ---------- */
  function cols() {
    return '<colgroup><col style="width:38px"><col><col style="width:110px"><col style="width:80px">' +
      '<col style="width:90px"><col style="width:150px"><col style="width:60px"></colgroup>';
  }

  function tableHead(all) {
    return '<thead><tr>' +
      '<th><input type="checkbox" id="ckAll"' + (all ? ' checked' : '') + '></th>' +
      '<th>商品标题</th><th>价格</th><th>库存</th><th>状态</th><th>类目</th><th>图</th></tr></thead>';
  }

  function tableBody(rows) {
    if (!rows.length) return '<tbody><tr><td colspan="7" class="empty">没有数据（可能状态筛选不合适，或店里没有商品）</td></tr></tbody>';
    const q = S.q.trim().toLowerCase();
    const view = q ? rows.filter(r => (r.title || '').toLowerCase().includes(q) || String(r.product_id).includes(q)) : rows;
    return '<tbody>' + view.map(r => {
      const cat = (r.cat || []).map(c => c.cat_id || c).join(' / ') || '—';
      const price = r.price_min === r.price_max ? money(r.price_min * 100) : money(r.price_min * 100) + ' ~ ' + money(r.price_max * 100);
      return '<tr data-id="' + esc(r.product_id) + '"' + (S.sel.has(String(r.product_id)) ? ' class="on"' : '') + '>' +
        '<td><input type="checkbox" class="ck"' + (S.sel.has(String(r.product_id)) ? ' checked' : '') + ' data-id="' + esc(r.product_id) + '"></td>' +
        '<td><div class="ptitle">' + esc(r.title || '(无标题)') + '</div><div class="psub">' + esc(r.product_id) + ' · SKU ' + (r.sku_count || 0) + '</div></td>' +
        '<td>' + price + '</td><td>' + (r.stock_total || 0) + '</td>' +
        '<td>' + U.statusTag(r.status) + '</td>' +
        '<td class="tiny">' + esc(cat) + '</td>' +
        '<td>' + (r.head_img ? '<img class="thumb" src="' + esc(r.head_img) + '" loading="lazy">' : '<span class="muted">无</span>') + '</td>' +
        '</tr>';
    }).join('') + '</tbody>';
  }

  /* 小猪版式：上方筛选条 → 中间全宽表格 → 下方数量统计条 */
  function toolbar(extra) {
    return '<div class="tb">' +
      '<select id="fStatus">' +
      U.STATUS_OPTS.map(o => '<option value="' + o[0] + '"' + (S.status === o[0] ? ' selected' : '') + '>' + o[1] + '</option>').join('') +
      '</select>' +
      '<input id="fQ" placeholder="搜标题 / 商品ID" value="' + esc(S.q) + '">' +
      '<button id="btnReload" class="ghost">刷新</button>' +
      '<span class="sp"></span>' +
      '<span class="selinfo">已选 <b id="selN">' + S.sel.size + '</b> 件</span>' +
      (extra || '') + '</div>';
  }

  /* 底部统计条：各档数量一眼看全（照小猪「进去后下方显示总数与分状态数量」） */
  function statbar() {
    const bs = S.byStatus || {};
    const txt = (S.byStatusText || {});
    const order = ['5', '0', '6', '11', '12', '13'];
    const items = order.filter(k => bs[k] != null && bs[k] !== '')
      .map(k => '<span class="sb-item"><i class="dot s' + k + '"></i>' + esc(txt[k] || U.statusText(k)) + ' <b>' + (bs[k] || 0) + '</b></span>');
    return '<div class="statbar">' +
      '<span class="sb-total">共 <b>' + (S.total || 0) + '</b> 件</span>' +
      (items.length ? items.join('') : '<span class="tiny">（未筛选状态时显示分档数量）</span>') +
      '<span class="sb-page">本页 <b>' + S.rows.length + '</b> 件 · 已选 <b id="selN2">' + S.sel.size + '</b></span>' +
      '</div>';
  }

  function bindTable(root) {
    const ckAll = U.q('#ckAll', root);
    if (ckAll) ckAll.onclick = () => {
      const on = ckAll.checked;
      U.qa('.ck', root).forEach(c => {
        c.checked = on;
        const id = c.dataset.id;
        on ? S.sel.add(id) : S.sel.delete(id);
        const tr = c.closest('tr'); if (tr) tr.classList.toggle('on', on);
      });
      const n = U.q('#selN', root); if (n) n.textContent = S.sel.size;
    };
    U.qa('.ck', root).forEach(c => c.onchange = () => {
      const id = c.dataset.id;
      c.checked ? S.sel.add(id) : S.sel.delete(id);
      const tr = c.closest('tr'); if (tr) tr.classList.toggle('on', c.checked);
      const n = U.q('#selN', root); if (n) n.textContent = S.sel.size;
    });
    const fs = U.q('#fStatus', root);
    if (fs) fs.onchange = () => { S.status = fs.value; reload(root); };
    const fq = U.q('#fQ', root);
    if (fq) fq.onchange = () => { S.q = fq.value; paintTable(root); };
    const br = U.q('#btnReload', root);
    if (br) br.onclick = () => reload(root);
  }

  function paintTable(root) {
    const host = U.q('#tblHost', root);
    if (!host) return;
    const all = S.rows.length > 0 && S.rows.every(r => S.sel.has(String(r.product_id)));
    host.innerHTML = '<table class="tbl">' + cols() + tableHead(all) + tableBody(S.rows) + '</table>';
    bindTable(root);
  }

  async function reload(root) {
    const host = U.q('#tblHost', root);
    if (host) host.innerHTML = '<div class="loading">读取商品…</div>';
    const qs = '/api/products?page_size=30' + (S.status !== '' ? '&status=' + S.status : '');
    const res = await U.get(qs);
    S.rows = U.rows(res);
    S.total = res.data ? res.data.total : S.rows.length;
    S.byStatus = res.data ? res.data.by_status : null;
    S.byStatusText = res.data ? res.data.by_status_text : null;
    S.sel.clear();
    const bar = U.q('#tblBar', root);
    if (bar) bar.innerHTML = toolbar();
    paintTable(root);
    bindTable(root);
    const st = U.q('#tblStat', root);
    if (st) st.innerHTML = statbar();
  }

  function resultBox(rows) {
    if (!rows || !rows.length) return '';
    return '<div class="res"><table class="tbl small"><thead><tr><th>商品</th><th>结果</th><th>说明</th></tr></thead><tbody>' +
      rows.map(r => '<tr><td class="tiny">' + esc(r.id || r.product_id || '') + '</td>' +
        '<td>' + (r.ok ? '<span class="tag ok">成功</span>' : '<span class="tag bad">失败</span>') + '</td>' +
        '<td class="tiny">' + esc(r.msg || r.error || '') + '</td></tr>').join('') +
      '</tbody></table></div>';
  }

  /* ---------- 批量修改工作台 ---------- */
  function panel(leaf) {
    const a = leaf.act;
    const def = ACT[a];
    if (def) {
      const fields = (def.fields || []).map(f => {
        if (f.t === 'select') {
          return '<label class="fl">' + f.label + '<select name="' + f.k + '">' +
            f.opts.map(o => '<option value="' + o[0] + '">' + o[1] + '</option>').join('') + '</select></label>';
        }
        if (f.t === 'number') {
          return '<label class="fl">' + f.label + '<input type="number" step="0.01" name="' + f.k + '" value="' + (f.def || '') + '"></label>';
        }
        return '<label class="fl">' + f.label + '<input type="text" name="' + f.k + '" value="' + esc(f.def || '') + '"></label>';
      }).join('');

      if (def.kind === 'patch') {
        return '<div class="card act"><div class="card-h">' + esc(leaf.title) + ' <span class="tag ok">可用</span></div>' +
          '<p class="muted">' + esc(def.hint || '') + '</p>' +
          '<label class="fl">新值<input type="' + (def.type === 'number' ? 'number' : 'text') + '" id="patchVal"></label>' +
          '<div class="btns"><button id="btnPrev" class="ghost">先预览</button><button id="btnRun" class="danger">批量写入</button></div>' +
          '<div id="padRes"></div></div>';
      }
      if (def.kind === 'delete') {
        return '<div class="card act"><div class="card-h">删除商品 <span class="tag warn">不可逆提醒</span></div>' +
          '<p class="muted">' + esc(def.hint) + '</p>' +
          '<div class="btns"><button id="btnDel" class="danger">把选中商品移入回收站</button></div><div id="padRes"></div></div>';
      }
      return '<div class="card act"><div class="card-h">' + esc(leaf.title) + ' <span class="tag ok">可用</span></div>' +
        '<p class="muted">' + esc(def.hint || '') + '</p>' +
        '<div class="flrow">' + fields + '</div>' +
        '<div class="btns"><button id="btnPrev" class="ghost">先预览（不动数据）</button><button id="btnRun" class="danger">批量写入</button></div>' +
        '<div id="padRes"></div></div>';
    }
    if (ESPEC && ESPEC[a]) return editPanel(leaf, ESPEC[a]);

    /* 外壳动作 */
    return '<div class="card act"><div class="card-h">' + esc(leaf.title) + ' <span class="tag warn">未接入</span></div>' +
      '<p class="muted">原版行为：选中商品 → 填写/上传 → 批量提交，逐件返回结果与失败原因。</p>' +
      '<div class="skeleton"><div class="sk-line"></div><div class="sk-line short"></div><div class="sk-line"></div></div>' +
      '<div class="needbox"><b>接入还缺：</b>' + esc(leaf.need || '微信官方对应字段/接口') + '</div>' +
      '<div class="btns"><button disabled>批量执行（未接入）</button></div></div>';
  }

  async function runBatch(root, leaf) {
    const a = ACT[leaf.act];
    const ids = Array.from(S.sel);
    if (!ids.length) return U.toast('先勾选商品', false);
    if (a.kind === 'delete') {
      if (!confirm('把 ' + ids.length + ' 件商品移入回收站？')) return;
      const out = [];
      for (const id of ids) {
        const r = await U.post('/api/product/delete', { id });
        out.push({ id, ok: r.ok, msg: r.ok ? '已移入回收站' : (r.error || '') });
      }
      if (!U.w(root, '#padRes', resultBox(out), 'innerHTML')) return;
      return reload(root);
    }
    if (a.kind === 'patch') {
      const v = (U.q('#patchVal', root) || {}).value || '';
      const r = await U.post('/api/patch', { ids, patch: { [a.field]: a.type === 'number' ? Number(v) : v }, dry_run: !root.__dry });
      U.w(root, '#padRes', resultBox((r.data && r.data.items) || []), 'innerHTML');
      return;
    }
    const form = U.q('.act', root) || root;
    const fd = {};
    U.qa('[name]', form).forEach(el => { fd[el.name] = el.value; });
    const mode = fd.mode || a.mode || 'set';
    const r = await U.post('/api/batch', {
      ids, op: a.op, mode, value: fd.value, dry_run: !!root.__dry
    });
    if (!r.ok) { U.w(root, '#padRes', '<div class="err">' + esc(r.error || '失败') + '</div>', 'innerHTML'); return; }
    U.w(root, '#padRes', resultBox(r.data.items) +
      '<div class="tiny">模式：' + (root.__dry ? '预览（未写入）' : '真实写入') + ' · 成功 ' + r.data.ok + ' / 失败 ' + r.data.fail + '</div>', 'innerHTML');
  }

  /* ---------- 路由页 ---------- */
  const P = {};

  P.home = async (root) => {
    root.innerHTML = '<div class="grid4" id="hcards"><div class="card"><div class="loading">统计中…</div></div></div>' +
      '<div class="card"><div class="card-h">从这里开始</div><div class="quick">' +
      [['#/edit-price', '改价格'], ['#/edit-stock', '改库存'], ['#/edit-title', '改标题'], ['#/edit-list-status', '上下架'],
      ['#/importItem', '导入 Excel'], ['#/exportItem', '导出'], ['#/create', '新建上货'], ['#/diagnosis/home', '商品诊断']]
        .map(q => '<a class="qbtn" href="' + q[0] + '">' + q[1] + '</a>').join('') +
      '</div></div><div class="card"><div class="card-h">最近操作</div><div id="hTask" class="loading">读取…</div></div>';
    const [shop, ov, cats, fre, cou, tasks] = await Promise.all([
      U.get('/api/shop').catch(() => null), U.get('/api/overview').catch(() => null),
      U.get('/api/categories?limit=1').catch(() => null), U.get('/api/freight').catch(() => null),
      U.get('/api/coupons').catch(() => null), U.get('/api/tasks?limit=6').catch(() => null)
    ]);
    const bs = (ov && ov.by_status) || {};
    const si = (shop && shop.data && (shop.data.info || (shop.data.shop_info && shop.data.shop_info.info) || shop.data.shop_info || shop.data)) || (ov && ov.shop) || {};
    const cards = [
      ['店铺', si.nickname || si.name || si.shop_name || '—', si.subject_type || ''],
      ['商品总数', (ov && ov.total) || 0, (ov && ov.by_status_text ? ['5', '0', '6'].map(k => ov.by_status_text[k] + ' ' + ((bs[k]) || 0)).join(' / ') : '')],
      ['类目', (cats && cats.total) || '—', '末级可上货类目'],
      ['运费模板', fre && fre.data ? (fre.data.template_list || fre.data.templates || []).length : '—', '券 ' + (cou && cou.data ? (cou.data.coupon_list || cou.data.list || []).length : '—') + ' 张']
    ];
    U.w(root, '#hcards', cards.map(c => '<div class="card stat"><div class="k">' + esc(c[0]) + '</div><div class="v">' + esc(c[1]) + '</div><div class="tiny">' + esc(c[2]) + '</div></div>').join(''), 'innerHTML');
    const tr = (tasks && tasks.rows) || [];
    U.w(root, '#hTask', tr.length ? '<table class="tbl small"><thead><tr><th>时间</th><th>操作</th><th>结果</th></tr></thead><tbody>' +
      tr.map(t => '<tr><td class="tiny">' + esc(t.ts || '') + '</td><td>' + esc(t.op || '') + ' ' + esc(t.mode || '') + '</td><td class="tiny">成功 ' + (t.ok || 0) + ' / 失败 ' + (t.fail || 0) + '</td></tr>').join('') + '</tbody></table>' : '<p class="muted">还没有操作记录</p>', 'innerHTML');
  };

  async function workbench(root, leaf) {
    /* 来自主程序的定位参数：#/edit-stock?focus=<微店商品ID>
       桌面 6.0 页签「定位微店商品」用它直接筛出那一件（按 ID，不按标题）*/
    const fm = /[?&]focus=([^&#]+)/.exec(location.hash || '');
    S.q = fm ? decodeURIComponent(fm[1]) : '';
    if (leaf.act && !ACT[leaf.act]) await loadSpec();
    root.innerHTML = '<div class="wb">' +
      '<div class="card actwrap">' + panel(leaf) + '</div>' +
      '<div class="card listwrap"><div class="card-h">商品列表</div><div id="tblBar">' + toolbar() + '</div><div id="tblHost"><div class="loading">读取商品…</div></div><div id="tblStat">' + statbar() + '</div></div>' +
      '</div>';
    const act = U.q('.act', root);
    const bp = U.q('#btnPrev', root), br = U.q('#btnRun', root);
    if (bp) bp.onclick = () => { root.__dry = true; runBatch(root, leaf); };
    if (br) br.onclick = () => { root.__dry = false; runBatch(root, leaf); };
    const bd = U.q('#btnDel', root);
    if (bd) bd.onclick = () => runBatch(root, leaf);
    const ep = U.q('#btnEPrev', root), er = U.q('#btnERun', root);
    if (ep) ep.onclick = () => { root.__dry = true; runEdit(root, leaf); };
    if (er) er.onclick = () => { root.__dry = false; runEdit(root, leaf); };
    reload(root);
  }

  P.quick = (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">快速修改</div><p class="muted">原版这里是常用动作的快捷入口，选中商品后一键改。这里直接给出三个最常用的面板。</p></div>' +
      '<div class="wb"><div class="actwrap">' + panel({ title: '改价格', act: 'price' }) + panel({ title: '改库存', act: 'stock' }) + panel({ title: '改标题', act: 'title' }) + '</div>' +
      '<div class="card listwrap"><div class="card-h">商品列表</div><div id="tblBar">' + toolbar() + '</div><div id="tblHost"><div class="loading">读取商品…</div></div><div id="tblStat">' + statbar() + '</div></div></div>';
    U.qa('.actwrap .act', root).forEach((card, i) => {
      const leaf = { title: ['改价格', '改库存', '改标题'][i], act: ['price', 'stock', 'title'][i] };
      const bp = U.q('#btnPrev', card), br = U.q('#btnRun', card);
      if (bp) bp.onclick = () => { root.__dry = true; runBatchAct(root, leaf, card); };
      if (br) br.onclick = () => { root.__dry = false; runBatchAct(root, leaf, card); };
    });
    reload(root);
  };

  async function runBatchAct(root, leaf, card) {
    const ids = Array.from(S.sel);
    if (!ids.length) return U.toast('先勾选商品', false);
    const a = ACT[leaf.act]; const fd = {};
    U.qa('[name]', card).forEach(el => { fd[el.name] = el.value; });
    const r = await U.post('/api/batch', { ids, op: a.op, mode: fd.mode || 'set', value: fd.value, dry_run: !!root.__dry });
    if (!r.ok) { U.w(card, '#padRes', '<div class="err">' + esc(r.error || '') + '</div>', 'innerHTML'); return; }
    U.w(card, '#padRes', resultBox(r.data.items), 'innerHTML');
  }

  P.products = (root) => workbench(root, { title: '商品列表（只读）', act: 'status' });

  /* ---- 导入 ---- */
  P.import = (root) => {
    root.__norm = null;
    root.innerHTML = '<div class="card"><div class="card-h">导入 Excel / CSV</div>' +
      '<p class="muted">微信小店模板为 19 列 GBK CSV（商品ID/名称/品牌/分类/库存/团购价/单买价/主图/轮播图/详情图/描述/视频/属性/SKUID/SKU名称/SKU库存/SKU团购价/SKU单买价/SKU预览图）。' +
      '流程：读文件 → 解析预览 → 归一校验（看每行缺什么）→ 批量建草稿品（先试算，再真建）。</p>' +
      '<input type="file" id="fCsv" accept=".csv,.txt">' +
      '<div id="impOut" class="mt12"></div></div>' +
      '<div class="card" id="impGo" style="display:none"><div class="card-h">建草稿品（按类目建品，运费/地址/属性/价格系数用本店数据自动补齐）</div>' +
      '<div class="row"><label class="fl">末级类目 cat_id <input name="cat" type="text" value="549074" placeholder="如 549074 家用茶台/茶桌"></label>' +
      '<label class="fl">建几件 <input name="limit" type="number" value="3" min="1" max="20"></label></div>' +
      '<div class="btns mt8"><button id="btnImpDry" class="ghost">试算（不写微信）</button><button id="btnImpGo" class="primary danger">真建草稿品</button></div>' +
      '<div id="impGoOut" class="mt12"></div></div>' +
      '<div class="card"><div class="card-h">导入记录</div><div id="impRec" class="loading">读取…</div></div>';
    U.q('#fCsv', root).onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const txt = await f.text();
      const r = await U.post('/api/import/normalize', { text: txt, name: f.name });
      if (!r.ok) { U.w(root, '#impOut', '<div class="err">' + esc(r.error || '') + '</div>', 'innerHTML'); return; }
      root.__norm = r;
      const rows = r.items.map(i => '<tr><td class="tiny mono">' + esc(String(i.key).slice(0, 20)) + '</td>' +
        '<td>' + esc((i.title || '').slice(0, 30)) + '</td><td>' + i.sku_count + '</td><td>' + i.head_imgs.length + '</td>' +
        '<td>' + (i.ready ? '<span class="ok">可建</span>' : '<span class="err">' + esc(i.blocks.join('；')) + '</span>') + '</td></tr>').join('');
      U.w(root, '#impOut', '<div class="ok">识别 ' + r.total + ' 个商品 · 可建 ' + r.ready + ' · 有缺 ' + r.blocked + '</div>' +
        '<table class="tbl small"><thead><tr><th>商品</th><th>名称</th><th>SKU</th><th>主图</th><th>校验</th></tr></thead><tbody>' + rows + '</tbody></table>' +
        '<p class="tiny">只列前 100 个；外链图片在建品时才下载转存（同图只传一次）。</p>', 'innerHTML');
      U.w(root, '#impGo', r.ready ? '' : 'none', 'style.display');
    };
    const impRun = async (dry) => {
      if (!root.__norm) return U.toast('先选一个文件', false);
      const cat = ((U.q('[name=cat]', root) || {}).value || '').trim();
      const limit = parseInt(((U.q('[name=limit]', root) || {}).value || '3'), 10);
      if (!cat) return U.toast('先填末级类目 cat_id', false);
      const r = await U.post('/api/import/create', { norm: root.__norm, cat_id: cat, limit, dry_run: dry });
      if (!r.ok) { U.w(root, '#impGoOut', '<div class="err">' + esc(r.error || '') + '</div>', 'innerHTML'); return; }
      U.w(root, '#impGoOut', resultBox((r.items || []).map(x => ({ id: x.key, ok: x.ok, msg: x.msg }))) +
        '<p class="tiny mt8">' + (dry ? '试算：' : '已提交：') + '成功 ' + (r.ok || 0) + ' / ' + (r.total || 0) +
        ' 条。真建的都在 <a href="#/products">商品列表</a>（未上架）。建议先用微信官方后台核对再上架。</p>', 'innerHTML');
      if (!dry) loadImpRec();
    };
    U.q('#btnImpDry', root).onclick = () => impRun(true);
    U.q('#btnImpGo', root).onclick = () => {
      if (!confirm('确认按试算结果建草稿品？（外链图片会被下载并转存到你的微信素材库）')) return;
      impRun(false);
    };
    function loadImpRec() {
      U.get('/api/tasks?limit=20').then(r => {
        const rows = (r.rows || []).filter(x => String(x.op).indexOf('import') === 0);
        U.w(root, '#impRec', rows.length
          ? '<table class="tbl small"><thead><tr><th>操作</th><th>文件/模板</th><th>总数</th><th>成</th><th>败</th><th>试算</th><th>时间</th></tr></thead><tbody>' +
          rows.map(x => '<tr><td>' + esc(x.op) + '</td><td class="tiny">' + esc(x.mode + ' ' + (x.value || '')) + '</td><td>' + x.total + '</td><td>' + (x.ok || 0) + '</td><td>' + (x.fail || 0) + '</td><td>' + (x.dry_run ? '是' : '否') + '</td><td class="tiny">' + esc(x.ts) + '</td></tr>').join('') + '</tbody></table>'
          : '<p class="muted">还没有导入建品记录。</p>', 'innerHTML');
      });
    }
    loadImpRec();
  };

  /* ---- 导出 ---- */
  P.export = (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">导出商品</div>' +
      '<p class="muted">导出选中商品（未选则导出当前列表）。输出 CSV 到 data/exports/，同时给下载链接。</p>' +
      '<div class="btns"><button id="btnExp" class="primary">导出当前列表</button><button id="btnExpAll" class="ghost">导出全部状态</button></div><div id="expOut" class="mt12"></div></div>' +
      '<div class="card"><div class="card-h">导出记录</div><div id="expRec" class="loading">读取…</div></div>';
    const doExp = async (all) => {
      const ids = all ? [] : (S.sel.size ? Array.from(S.sel) : S.rows.map(r => String(r.product_id)));
      const r = await U.post('/api/export', { ids, status: all ? '' : S.status });
      U.w(root, '#expOut', r.ok
        ? '<div class="ok">已导出 ' + r.rows + ' 行 → <a href="/data/exports/' + encodeURIComponent(r.file) + '" target="_blank">' + esc(r.file) + '</a></div>'
        : '<div class="err">' + esc(r.error || '') + '</div>', 'innerHTML');
      loadExpRec();
    };
    U.q('#btnExp', root).onclick = () => doExp(false);
    U.q('#btnExpAll', root).onclick = () => doExp(true);
    function loadExpRec() {
      U.get('/api/exportRecords').then(r => {
        U.w(root, '#expRec', (r.rows && r.rows.length)
          ? '<table class="tbl small"><thead><tr><th>文件</th><th>行数</th><th>时间</th></tr></thead><tbody>' +
          r.rows.map(x => '<tr><td class="tiny"><a href="/data/exports/' + encodeURIComponent(x.file) + '" target="_blank">' + esc(x.file) + '</a></td><td>' + x.rows + '</td><td class="tiny">' + esc(x.ts) + '</td></tr>').join('') + '</tbody></table>'
          : '<p class="muted">还没有导出记录</p>', 'innerHTML');
      });
    }
    loadExpRec();
  };

  P.exportRecords = P.export;

  /* ---- 云库 ---- */
  P.yunku = (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">云库（本地备份）</div>' +
      '<p class="muted">原版云库存在它服务器上，用来「先备份再批量改，改坏可回滚」。这里换成纯本机：把选中商品的完整 JSON 存到 data/yunku/。</p>' +
      '<div class="btns"><button id="btnBk" class="primary">备份选中商品</button><button id="btnBkAll" class="ghost">备份当前列表</button></div><div id="bkOut" class="mt12"></div></div>' +
      '<div class="card"><div class="card-h">备份清单</div><div id="bkList" class="loading">读取…</div></div>';
    const doBk = async (all) => {
      const ids = all ? S.rows.map(r => String(r.product_id)) : Array.from(S.sel);
      if (!ids.length) return U.toast('先勾选或先刷新商品列表', false);
      const r = await U.post('/api/yunku/save', { ids });
      U.w(root, '#bkOut', r.ok ? '<div class="ok">已备份 ' + r.saved + ' 件 → ' + esc(r.file) + '</div>' : '<div class="err">' + esc(r.error) + '</div>', 'innerHTML');
      load();
    };
    U.q('#btnBk', root).onclick = () => doBk(false);
    U.q('#btnBkAll', root).onclick = () => doBk(true);
    function load() {
      U.get('/api/yunku').then(r => {
        const rows = r.rows || [];
        U.w(root, '#bkList', rows.length ? '<table class="tbl small"><thead><tr><th>备份文件</th><th>件数</th><th>时间</th><th></th></tr></thead><tbody>' +
          rows.map(x => '<tr><td class="tiny">' + esc(x.file) + '</td><td>' + x.count + '</td><td class="tiny">' + esc(x.ts) + '</td>' +
            '<td><button class="ghost sm" data-del="' + esc(x.file) + '">删除</button></td></tr>').join('') + '</tbody></table>'
          : '<p class="muted">还没有备份</p>', 'innerHTML');
        U.qa('[data-del]', root).forEach(b => b.onclick = async () => {
          await U.post('/api/yunku/remove', { name: b.dataset.del }); load();
        });
      });
    }
    load();
  };

  /* ---- 诊断 ---- */
  P.diagnose = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">商品诊断 <span class="tag ok">本地规则</span></div>' +
      '<p class="muted">按「能不能上架卖」的硬条件检查：无标题 / 标题过长 / 无主图 / 无 SKU / 价格 0 / 库存 0 / 无类目。原版还查平台合规，那些需要官方质检接口。</p>' +
      '<div id="dgOut" class="loading">检查中…</div></div>';
    const r = await U.get('/api/diagnose');
    if (!r.ok) { U.w(root, '#dgOut', '<div class="err">' + esc(r.error || '') + '</div>', 'innerHTML'); return; }
    const g = r.groups || {};
    const keys = Object.keys(g);
    U.w(root, '#dgOut', '<div class="chips">' + keys.map(k => '<span class="chip">' + esc(k) + ' <b>' + g[k].length + '</b></span>').join('') + '</div>' +
      (r.issues.length ? '<table class="tbl small"><thead><tr><th>商品</th><th>问题</th><th>建议</th></tr></thead><tbody>' +
        r.issues.map(i => '<tr><td class="tiny">' + esc(i.title || i.product_id) + '</td><td>' + esc(i.issue) + '</td><td class="tiny">' + esc(i.fix) + '</td></tr>').join('') + '</tbody></table>'
        : '<p class="ok">没有发现问题</p>'), 'innerHTML');
  };

  P.lowprice = async (root) => {
    const r = await U.get('/api/products?page_size=30');
    const rows = U.rows(r).filter(x => !x.price_min || x.price_min < 20);
    root.innerHTML = '<div class="card"><div class="card-h">低价商品检测 <span class="tag ok">本地规则</span></div>' +
      '<p class="muted">筛出价格为空或低于 20 元的商品（低于平台类目门槛容易被驳回）。原版还对比同行价，那需要抓取，不在本地做。</p>' +
      '<table class="tbl"><thead><tr><th>商品</th><th>价格</th><th>库存</th><th>状态</th></tr></thead><tbody>' +
      (rows.length ? rows.map(x => '<tr><td>' + esc(x.title) + '</td><td>' + money(x.price_min * 100) + '</td><td>' + (x.stock_total || 0) + '</td><td>' + U.statusTag(x.status) + '</td></tr>').join('')
        : '<tr><td colspan="4" class="empty">没有低价商品</td></tr>') + '</tbody></table></div>';
  };

  /* ---- 任务记录 ---- */
  P.tasks = async (root) => {
    const r = await U.get('/api/tasks?limit=200');
    const rows = r.rows || [];
    root.innerHTML = '<div class="card"><div class="card-h">任务记录</div>' +
      (rows.length ? '<table class="tbl small"><thead><tr><th>时间</th><th>操作</th><th>模式</th><th>总数</th><th>成功</th><th>失败</th><th>明细</th></tr></thead><tbody>' +
        rows.map(t => '<tr><td class="tiny">' + esc(t.ts || '') + '</td><td>' + esc(t.op || '') + '</td><td>' + esc(t.mode || '') + '</td><td>' + (t.total || 0) + '</td><td>' + (t.ok || 0) + '</td><td>' + (t.fail || 0) + '</td>' +
          '<td class="tiny">' + esc(((t.items || []).filter(i => !i.ok).map(i => (i.msg || '失败')).join('; ') || '全部成功').slice(0, 120)) + '</td></tr>').join('') + '</tbody></table>'
        : '<p class="muted">还没有操作记录</p>') + '</div>';
  };

  /* ---- 类目 ---- */
  P.cats = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">类目树（末级可上货）</div>' +
      '<div class="tb"><input id="cq" placeholder="搜类目名 / cat_id"><button id="cb" class="ghost">搜索</button><span class="tiny" id="ctot"></span></div>' +
      '<div id="cout" class="loading">读取…</div></div>';
    const load = async () => {
      const q = U.q('#cq', root).value.trim();
      const r = await U.get('/api/categories?limit=300&q=' + encodeURIComponent(q));
      U.w(root, '#ctot', '共 ' + r.total + ' 条', 'textContent');
      U.w(root, '#cout', '<table class="tbl small"><thead><tr><th>类目路径</th><th>cat_id</th><th>资质</th></tr></thead><tbody>' +
        (r.rows || []).map(c => '<tr><td>' + esc(c.path) + '</td><td class="tiny">' + esc(c.cat_id) + '</td><td class="tiny">' + ((c.qua || []).map(q => q.qua_id).join(',') || '—') + '</td></tr>').join('') +
        '</tbody></table>', 'innerHTML');
    };
    U.q('#cb', root).onclick = load;
    U.q('#cq', root).onkeydown = (e) => { if (e.key === 'Enter') load(); };
    load();
  };

  /* ---- 新建上货 ---- */
  P.create = (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">新建商品 <span class="tag warn">会真实写入店铺</span></div>' +
      '<div class="flrow">' +
      '<label class="fl">标题<input id="cTitle" placeholder="≤60 字"></label>' +
      '<label class="fl">末级类目 cat_id<input id="cCat" placeholder="用类目树查到"></label>' +
      '<label class="fl">价格（元）<input id="cPrice" type="number" value="99"></label>' +
      '<label class="fl">库存<input id="cStock" type="number" value="10"></label>' +
      '</div>' +
      '<label class="fl">主图<input type="file" id="cImg" accept="image/*"></label>' +
      '<div class="btns"><button id="cUp" class="ghost">先上传图片拿链接</button><button id="cGo" class="danger">创建商品</button></div>' +
      '<div id="cOut" class="mt12"></div></div>';
    let imgUrl = '';
    U.q('#cUp', root).onclick = async () => {
      const f = U.q('#cImg', root).files[0];
      if (!f) return U.toast('先选图片', false);
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch('/api/upload', { method: 'POST', body: fd }).then(x => x.json());
      imgUrl = r.img_url || '';
      U.w(root, '#cOut', r.ok ? '<div class="ok">图片已上传：' + esc(imgUrl) + '</div>' : '<div class="err">' + esc(r.error) + '</div>', 'innerHTML');
    };
    U.q('#cGo', root).onclick = async () => {
      const title = U.q('#cTitle', root).value.trim();
      const cat = U.q('#cCat', root).value.trim();
      const price = Math.round(Number(U.q('#cPrice', root).value) * 100);
      const stock = Number(U.q('#cStock', root).value);
      if (!title || !cat || !price) return U.toast('标题/类目/价格都要有', false);
      if (!imgUrl) return U.toast('先上传一张主图', false);
      const payload = {
        title, head_imgs: [imgUrl],
        cats: [{ cat_id: cat }],
        attrs: [], skus: [{ sale_price: price, stock_num: stock, sku_attrs: [] }]
      };
      const r = await U.post('/api/product/add', { payload });
      U.w(root, '#cOut', r.ok
        ? '<div class="ok">已创建：' + esc(JSON.stringify(r.data).slice(0, 200)) + '</div>'
        : '<div class="err">' + esc(JSON.stringify(r).slice(0, 400)) + '</div>', 'innerHTML');
    };
  };

  /* ---- 订单 / 运费 / 券 / 设置 ---- */
  P.orders = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">订单（最近 7 天，微信限制窗口）</div><div id="oOut" class="loading">读取…</div></div>';
    const r = await U.get('/api/orders');
    const rows = (r.data && (r.data.order_list || r.data.orders || r.data.list)) || [];
    U.w(root, '#oOut', rows.length
      ? '<table class="tbl small"><thead><tr><th>订单号</th><th>金额</th><th>状态</th><th>下单时间</th></tr></thead><tbody>' +
      rows.map(o => '<tr><td class="tiny">' + esc(o.order_id) + '</td><td>' + money(o.order_price || o.price) + '</td><td class="tiny">' + esc(o.status) + '</td><td class="tiny">' + esc(o.create_time) + '</td></tr>').join('') + '</tbody></table>'
      : '<p class="muted">没有订单，或接口返回：' + esc(JSON.stringify(r).slice(0, 200)) + '</p>', 'innerHTML');
  };

  P.freight = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">运费模板 <span class="tag ok">只读</span></div><div id="fOut" class="loading">读取…</div></div>';
    const r = await U.get('/api/freight');
    const rows = (r.data && (r.data.template_list || r.data.list || [])) || [];
    U.w(root, '#fOut', rows.length
      ? '<table class="tbl small"><thead><tr><th>模板 ID</th><th>名称</th><th>类型</th></tr></thead><tbody>' +
      rows.map(t => '<tr><td class="tiny">' + esc(t.template_id) + '</td><td>' + esc(t.name || t.template_name) + '</td><td class="tiny">' + esc(t.type || '') + '</td></tr>').join('') + '</tbody></table>'
      : '<p class="muted">' + esc(JSON.stringify(r).slice(0, 200)) + '</p>', 'innerHTML');
  };

  P.coupons = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">优惠券 <span class="tag ok">只读</span> <span class="tag warn">创建未接</span></div><div id="cOut" class="loading">读取…</div></div>';
    const r = await U.get('/api/coupons');
    const rows = (r.data && (r.data.coupon_list || r.data.list || [])) || [];
    U.w(root, '#cOut', rows.length
      ? '<table class="tbl small"><thead><tr><th>券 ID</th><th>名称</th><th>面额</th><th>状态</th></tr></thead><tbody>' +
      rows.map(c => '<tr><td class="tiny">' + esc(c.coupon_id || c.id) + '</td><td>' + esc(c.name || c.title) + '</td><td>' + money(c.discount || c.amount) + '</td><td class="tiny">' + esc(c.status) + '</td></tr>').join('') + '</tbody></table>'
      : '<p class="muted">本店暂无优惠券（接口返回 0 条）</p>', 'innerHTML');
  };

  P.settings = async (root) => {
    const [h, s] = await Promise.all([U.get('/api/health').catch(() => ({})), U.get('/api/shop').catch(() => ({}))]);
    root.innerHTML = '<div class="card"><div class="card-h">连接</div>' +
      '<p class="muted">健康检查：' + esc(JSON.stringify(h)) + '</p>' +
      '<p class="muted">店铺：' + esc(JSON.stringify(s.data || {}).slice(0, 300)) + '</p></div>' +
      '<div class="card"><div class="card-h">凭据（只在本机 config.json）</div>' +
      '<label class="fl">AppID<input id="sA" placeholder="留空不改"></label>' +
      '<label class="fl">AppSecret<input id="sS" type="password" placeholder="留空不改"></label>' +
      '<div class="btns"><button id="sGo" class="primary">保存并测试连接</button></div><div id="sOut" class="mt12"></div></div>';
    U.q('#sGo', root).onclick = async () => {
      const b = {};
      const a = U.q('#sA', root).value.trim(), sec = U.q('#sS', root).value.trim();
      if (a) b.appid = a; if (sec) b.secret = sec;
      const r = await U.post('/api/config', b);
      U.w(root, '#sOut', r.ok ? '<div class="ok">' + esc(r.msg) + '</div>' : '<div class="err">' + esc(r.error) + '</div>', 'innerHTML');
    };
  };

  /* ---- 外壳页（非商品类功能） ---- */
  P.shell = (root, leaf) => {
    root.innerHTML = '<div class="card"><div class="card-h">' + esc(leaf.title) + ' <span class="tag warn">未接入</span></div>' +
      '<div class="needbox"><b>这个页面是什么：</b>原版里它是「' + esc(leaf.title) + '」的独立工作区。' +
      '<br><b>接入还缺：</b>' + esc(leaf.need || '对应官方接口/第三方账号') + '</div>' +
      '<p class="muted">外壳已经按原版形状搭好：左侧菜单、顶部筛选、主表格、动作区都在，接上接口即可用。</p>' +
      '<div class="tb"><input placeholder="搜索（未接）" disabled><button disabled>筛选</button><button disabled>批量操作</button></div>' +
      '<div class="skeleton big"><div class="sk-line"></div><div class="sk-line short"></div><div class="sk-line"></div><div class="sk-line short"></div></div></div>';
  };

  P.jiantu = (root, leaf) => {
    P.shell(root, Object.assign({}, leaf, {
      need: '本地小猪鉴图链路：9109 服务 + PicDetector.exe（已本地化，14 处出口全部指回本机）。接进来后这里显示逐张进度与命中区域。'
    }));
  };

  P.workbench = workbench;
  /* 多账号独立空间：一账号一份凭据 + 一个独立标签页。
     参照 WorkBuddy Manager /accounts 的分割：账号列表带当前上下文、可停用、
     停用不删凭据；「进入后台」开新页面而不是改全局状态。 */
  P.accounts = async (root) => {
    root.innerHTML = '<div class="card"><div class="card-h">账号管理 <span class="tag ok">可用</span></div>' +
      '<p class="muted">一个账号 = 一份独立凭据。点「切到这家店」就在本页面整页切过去，不会弹小窗口；顶部店铺下拉也能随时切。' +
      '切换只改本页店号，不动服务端全局状态，所以不会串号。<b>上架到哪家店，在「新建上货」页里选</b>：可单选一家，也可勾多家同时上架。' +
      '微信小店用 AppID / AppSecret 直连接口，不用扫码；只有「浏览器登录」类账号才需要在 ego lite 里扫码。</p>' +
      '<div id="acctList" class="tiny">读取中…</div></div>' +
      '<div class="card act"><div class="card-h">添加账号</div>' +
      '<div class="flrow">' +
      '<label class="fl">账号标识 AppID<input id="acAppid" placeholder="wx..."></label>' +
      '<label class="fl">AppSecret（wx_api 必填）<input id="acSecret" placeholder="只存本机"></label>' +
      '<label class="fl">显示名称<input id="acName" placeholder="例如：欢轴家居"></label>' +
      '<label class="fl">类型<select id="acKind"><option value="wx_api">微信小店（API 直连）</option>' +
      '<option value="browser">浏览器登录（扫码 / ego 空间）</option></select></label>' +
      '<label class="fl">浏览器身份 profileId（browser 类填）<input id="acProfile" placeholder="Profile 5"></label>' +
      '<label class="fl">空间名（browser 类填）<input id="acSpace" placeholder="上货-B-某某店"></label>' +
      '</div><div class="btns"><button id="acAdd" class="danger">保存账号</button></div>' +
      '<div id="acRes" class="tiny"></div></div>';

    const list = async () => {
      const r = await U.get('/api/shops');
      const cur = U.shopId();
      const box = U.q('#acctList', root);
      if (!box) return;
      if (!r.ok) { box.innerHTML = '<span class="err">读取失败</span>'; return; }
      const rows = (r.shops || []).map(s =>
        '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #eee">' +
        '<b>' + esc(s.name || s.mask) + '</b>' +
        '<span class="tiny">' + esc(s.mask) + '</span>' +
        '<span class="tag ' + (s.enabled ? 'ok' : 'warn') + '">' + (s.enabled ? '启用' : '停用') + '</span>' +
        '<span class="tag">' + (s.kind === 'browser' ? '浏览器登录' : 'API 直连') + '</span>' +
        (s.appid === cur ? '<span class="tag ok">本页正在用</span>' : '') +
        '<span style="flex:1"></span>' +
        '<button class="mini" data-enter="' + esc(s.appid) + '">切到这家店</button>' +
        '<button class="mini" data-toggle="' + esc(s.appid) + '" data-dis="' + (s.enabled ? '1' : '0') + '">' +
        (s.enabled ? '停用' : '启用') + '</button></div>').join('');
      box.innerHTML = rows || '<span class="muted">还没有账号</span>';
      U.qa('[data-enter]', box).forEach(b => b.onclick = () => {
        U.openShop(b.getAttribute('data-enter'));
      });
      U.qa('[data-toggle]', box).forEach(b => b.onclick = async () => {
        const rr = await U.post('/api/account/disable',
          { appid: b.getAttribute('data-toggle'), disabled: b.getAttribute('data-dis') === '1' });
        U.toast(rr.msg || (rr.ok ? '已更新' : '失败'), rr.ok);
        list();
      });
    };

    const add = U.q('#acAdd', root);
    if (add) add.onclick = async () => {
      const b = {
        appid: (U.q('#acAppid', root).value || '').trim(),
        secret: (U.q('#acSecret', root).value || '').trim(),
        name: (U.q('#acName', root).value || '').trim(),
        kind: U.q('#acKind', root).value,
        profileId: (U.q('#acProfile', root).value || '').trim(),
        space: (U.q('#acSpace', root).value || '').trim()
      };
      if (!b.appid) { U.toast('先填账号标识', false); return; }
      const r = await U.post('/api/account/add', b);
      const res = U.q('#acRes', root);
      if (res) res.innerHTML = r.ok
        ? '<span class="ok">' + esc(b.name || r.appid || '账号') + ' 已保存</span>'
        : '<span class="err">' + esc(r.error || '失败') + '</span>';
      if (r.ok) {
        U.q('#acAppid', root).value = '';
        U.q('#acSecret', root).value = '';
        U.q('#acName', root).value = '';
      }
      list();
    };
    list();
  };

  window.PAGES = P;
})();



