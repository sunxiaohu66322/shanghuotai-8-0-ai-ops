/* 运营中心 · 三店独立数据 + 每商品独立状态卡
   2026-11-10 主人指令：
     「把这个全店数据、上架中未上架直接清理掉，这个数据不要放这里，放了没用、
       纯粹浪费时间；我应该是要三个店、每个店独立的数据；把这个数据放到运营软件
       里面，然后再做每个商品的独立状态卡。」
   落点：微信小店自建上货台（127.0.0.1:9191）网页版后台。
   数据源（全部只读，实测可用）：
     GET /api/shops                 三店台账（含 kind/name/appid）
     GET /api/overview?X-Shop-Id   单店概览（按店号分片，不混算）
     GET /api/products?status=&page_size=  单店商品行（含 status/edit_status/skus/图）
     GET /api/sku/audit?mode=get  全店 SKU 缺图明细（top 按商品给出 noimg/total）
     GET /api/dd/overview          抖店（页面快照口径，草稿/上架包）
   红线：不写任何商品数据、不改 v3.db、不触发上下架；只做展示。
*/
(function () {
  'use strict';
  const U = window.U;
  const P = window.PAGES = window.PAGES || {};

  /* 每商品状态卡的「图片齐不齐」：
     商品自带 head_imgs / skus，SKU 缺图清单来自 /api/sku/audit 的 top（按商品 pid）。
     三者拼出「主图 / 详情图 / 规格图」三项，缺哪标哪，不猜。 */
  function imgFlags(row, skuMap) {
    const heads = (row.head_imgs || []).length || (row.head_img ? 1 : 0);
    const descs = (row.desc_imgs || []).length;
    const skus = row.sku_count != null ? row.sku_count : (row.skus || []).length;
    const bad = skuMap[String(row.product_id)];
    let spec;
    if (bad) spec = { text: '缺 ' + bad.noimg + '/' + bad.total, ok: false };
    else if (skus > 0) spec = { text: '已齐（' + skus + '）', ok: true };
    else spec = { text: '无规格', ok: false };
    return [
      { k: '主图', text: heads + ' 张', ok: heads >= 3 },
      { k: '详情图', text: descs + ' 张', ok: descs > 0 },
      { k: '规格图', text: spec.text, ok: spec.ok }
    ];
  }

  function statusCard(row, skuMap, shopName) {
    const st = row.status_text || U.statusText(row.status);
    const es = row.edit_status_text || '';
    const flags = imgFlags(row, skuMap);
    const price = (row.price_min != null && row.price_max != null)
      ? (U.money(row.price_min * 100) + (row.price_max !== row.price_min ? ' ~ ' + U.money(row.price_max * 100) : ''))
      : '—';
    const good = flags.every(f => f.ok);
    return '<div class="opscard card' + (good ? '' : ' opscard-warn') + '">' +
      '<div class="opscard-h">' +
        '<span class="ops-title">' + U.esc((row.title || '(无标题)').slice(0, 46)) + '</span>' +
        U.statusTag(row.status) +
        (es ? '<span class="tag">' + U.esc(es) + '</span>' : '') +
      '</div>' +
      '<div class="ops-meta tiny">' +
        '<span>ID ' + U.esc(row.product_id) + '</span>' +
        '<span>所属店 <b>' + U.esc(shopName) + '</b></span>' +
        '<span>状态 <b>' + U.esc(st) + '</b></span>' +
        '<span>价格 ' + U.esc(price) + '</span>' +
        '<span>库存 ' + U.esc(row.stock_total == null ? '—' : row.stock_total) + '</span>' +
        '<span>规格 ' + U.esc(row.sku_count == null ? (row.skus || []).length : row.sku_count) + ' 个</span>' +
      '</div>' +
      '<div class="ops-flags">' + flags.map(f =>
        '<span class="tag ' + (f.ok ? 'ok' : 'warn') + '">' + U.esc(f.k) + '：' + U.esc(f.text) + '</span>'
      ).join('') + '</div>' +
      (row.head_img ? '<img class="ops-thumb" src="' + U.esc(row.head_img) + '" alt="" loading="lazy">' : '') +
      '</div>';
  }

  function shopBlock(s, ov, rows, skuMap, err) {
    const bs = (ov && ov.by_status) || {};
    const total = ov && ov.total != null ? ov.total : '—';
    const rate = ov && ov.list_rate != null ? ov.list_rate + '%' : '—';
    const o7 = (ov && ov.orders7) || {};
    const sk = (ov && ov.sku) || {};
    const name = s.name || s.mask || s.appid;
    const head =
      '<div class="card opsshop">' +
      '<div class="ops-shop-h"><b>' + U.esc(name) + '</b>' +
        '<span class="tag">' + U.esc(s.kind === 'doudian' ? '抖店（页面快照）' : '微信小店（官方接口）') + '</span>' +
        '<span class="tiny">' + U.esc(s.mask || s.appid) + '</span>' +
        (err ? '<span class="tag bad">读取失败：' + U.esc(err) + '</span>' : '') +
      '</div>' +
      '<div class="grid4 ops-stats">' +
        [['本店商品', total], ['上架', bs['5'] != null ? bs['5'] : '—'],
         ['未上架', bs['0'] != null ? bs['0'] : '—'],
         ['回收站', bs['6'] != null ? bs['6'] : '—'],
         ['上架率', rate], ['近 7 天订单', o7.total != null ? o7.total : '—'],
         ['规格图完整度', sk.img_rate != null ? sk.img_rate + '%' : '—'],
         ['缺规格图商品', sk.bad_products != null ? sk.bad_products : '—']
        ].map(x => '<div class="card stat"><div class="k">' + x[0] +
          '</div><div class="v">' + U.esc(x[1]) + '</div></div>').join('') +
      '</div>';
    const cards = (rows || []).map(r => statusCard(r, skuMap, name)).join('');
    const foot = '<div class="ops-note tiny">本店独立取数，不与其它店叠加 · ' +
      '状态卡 ' + (rows || []).length + ' 张（前 ' + (rows || []).length + ' 件）· ' +
      '状态取 status / edit_status，图片齐不齐取 head_imgs / desc_imgs / SKU 缺图清单</div>';
    return head + (cards || '<div class="muted">' +
      (err ? '本店数据读取失败，未生成状态卡。' : '本店本页暂无商品行。') + '</div>') + foot + '</div>';
  }

  /* 三店独立：逐家带自己的 X-Shop-Id 请求，绝不合并成一个总数。 */
  async function oneWxShop(s) {
    const sid = s.appid;
    const [ov, pr, au] = await Promise.all([
      U.get('/api/overview', sid).catch(() => null),
      U.get('/api/products?page_size=12', sid).catch(() => null),
      U.get('/api/sku/audit?mode=get', sid).catch(() => null)
    ]);
    const rows = U.rows(pr);
    const auData = (au && au.data) || {};
    const skuMap = {};
    (auData.top || []).forEach(x => { skuMap[String(x.pid)] = x; });
    const err = (!ov && !pr) ? '接口无响应' : '';
    return shopBlock(s, ov, rows, skuMap, err);
  }

  async function oneDdShop(s) {
    let d = null, err = '';
    try { d = await U.get('/api/dd/overview'); } catch (e) { err = String(e.message || e); }
    const name = s.name || '抖店';
    /* 抖店没有免签商品接口：草稿/上架包是页面快照口径，如实写明，不冒充商品状态卡。 */
    return '<div class="card opsshop"><div class="ops-shop-h"><b>' + U.esc(name) +
      '</b><span class="tag">抖店（页面快照）</span>' +
      (err ? '<span class="tag bad">读取失败：' + U.esc(err) + '</span>' : '') + '</div>' +
      '<div class="grid4 ops-stats">' +
      [['店铺', (d && d.shop && d.shop.name) || '—'], ['草稿箱', d ? d.drafts : '—'],
       ['待提交草稿', d ? d.drafts_ready : '—'], ['上架包', d ? d.packs : '—'],
       ['包内行数', d ? d.packs_rows : '—'], ['脚本', d ? d.scripts : '—'],
       ['页面快照', d ? d.snapshots : '—'], ['数据口径', '快照']]
        .map(x => '<div class="card stat"><div class="k">' + x[0] +
          '</div><div class="v">' + U.esc(x[1]) + '</div></div>').join('') + '</div>' +
      '<div class="needbox">抖店没有可直连的免签商品接口，逐商品状态卡依赖 ego 页面快照明细；' +
      '当前只拿到汇总草稿/上架包数，<b>不编造单件状态</b>。快照明细落地后在本店下补卡片。</div>' +
      '<div class="ops-note tiny">本店独立取数，不与两家微信小店叠加。</div></div>';
  }

  /* 页面入口：/ops/shops —— 三店各自独立数据 */
  P.opsShops = async function (root) {
    root.innerHTML = '<div class="card"><div class="card-h">三店独立数据 · 每商品独立状态卡' +
      '<span class="tiny">2026-11-10 从上货台顶部总统计迁来；不再混算成一个全店总数</span></div>' +
      '<div id="opsBody" class="loading">正在按店逐家拉取…</div></div>';
    let shops = [];
    try {
      const r = await U.get('/api/shops');
      shops = (r.shops || []).filter(x => x.enabled !== false);
    } catch (e) {
      U.w(root, '#opsBody', '店铺台账读取失败：' + U.esc(e.message || e), 'innerHTML');
      return;
    }
    const blocks = [];
    for (const s of shops) {
      blocks.push(s.kind === 'doudian' ? await oneDdShop(s) : await oneWxShop(s));
    }
    U.w(root, '#opsBody', blocks.join(''), 'innerHTML');
  };

  /* 页面入口：/ops/cards —— 当前店的逐商品状态卡（可切状态、可翻更多） */
  P.opsCards = async function (root, leaf) {
    root.innerHTML = '<div class="card"><div class="card-h">每商品独立状态卡' +
      '<span class="tiny">单件一张卡 · 状态 / 编辑状态 / 主图·详情图·规格图齐不齐</span></div>' +
      '<div class="tb"><select id="opsSt">' +
      U.STATUS_OPTS.map(o => '<option value="' + o[0] + '">' + U.esc(o[1]) + '</option>').join('') +
      '</select><select id="opsNum"><option value="12">12 件</option><option value="30">30 件</option>' +
      '<option value="60">60 件</option></select>' +
      '<button id="opsGo" class="primary" type="button">刷新</button>' +
      '<span id="opsHint" class="tiny">—</span></div></div>' +
      '<div id="opsCards" class="loading">读取中…</div>';
    const go = async () => {
      const st = U.q('#opsSt', root).value;
      const n = U.q('#opsNum', root).value;
      U.w(root, '#opsCards', '读取中…', 'textContent');
      const [sh, pr, au] = await Promise.all([
        U.get('/api/shops').catch(() => null),
        U.get('/api/products?status=' + encodeURIComponent(st) + '&page_size=' + n).catch(() => null),
        U.get('/api/sku/audit?mode=get').catch(() => null)
      ]);
      const cur = (sh && sh.current) || '';
      const name = ((sh && sh.shops) || []).find(x => x.appid === cur);
      const shopName = (name && name.name) || '当前店';
      const rows = U.rows(pr);
      const auData = (au && au.data) || {};
      const skuMap = {};
      (auData.top || []).forEach(x => { skuMap[String(x.pid)] = x; });
      U.w(root, '#opsHint', '当前店：' + shopName + ' · 本页 ' + rows.length + ' 张卡', 'textContent');
      U.w(root, '#opsCards',
        '<div class="ops-grid">' + rows.map(r => statusCard(r, skuMap, shopName)).join('') + '</div>',
        'innerHTML');
    };
    U.q('#opsGo', root).onclick = go;
    U.q('#opsSt', root).onchange = go;
    U.q('#opsNum', root).onchange = go;
    go();
  };
})();
