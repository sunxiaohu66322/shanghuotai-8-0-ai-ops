/* 9.0 AI 运营中心 · 晓风搬家上货全功能复刻页
   数据源：GET /api/xf9/catalog（91 路由 / 8 组 / 视频原理）
   状态机：/api/xf9/state + /api/xf9/task/advance（add/start/stop/end/list/logList 同构）
   本地试合成：动态 import vendor9/useLocalShortVideo-Drins-0G.js（原版合成器，闭包已拷全）
   红线：本页不自动发任何真实平台请求；正式发布/报名/改价一律「AI 准备 → 人工确认」。
*/
(function () {
  'use strict';
  const U = window.U;
  const P = window.PAGES = window.PAGES || {};
  const API = '/api/xf9/';
  const S = { cat: null, state: null, tab: 'flow', grp: 'all', q: '', busy: false };

  const esc = s => U.esc(String(s == null ? '' : s));
  const post = (p, b) => U.post ? U.post(API + p, b) : fetch(API + p, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(b || {})
  }).then(r => r.json());
  const get = p => U.get(API + p);

  function badge(st) {
    const m = { '待开始': '', '进行中': 'warn', '成功': 'ok', '失败': 'bad', '已暂停': 'warn', '已终止': '' };
    return '<span class="tag ' + (m[st] || '') + '">' + esc(st) + '</span>';
  }

  async function load(box) {
    try {
      S.cat = S.cat || (await get('catalog'));
      const r = await get('state');
      S.state = r;
      paint(box);
    } catch (e) {
      box.innerHTML = '<div class="card"><div class="err">9.0 数据读取失败：' + esc(e.message || e) +
        '<br><span class="tiny">检查 /api/xf9/health 与 data/xf9_catalog.json</span></div></div>';
    }
  }

  function paint(box) {
    const c = S.cat, st = S.state;
    const tabs = [
      ['flow', '今日一趟 · 最短运营闭环'], ['all', '全套功能树（' + c.counts.routes + ' 项）'],
      ['remake', '爆款视频复刻（老师教的那条）'], ['principle', '视频原理 · 本地试合成'],
      ['tasks', '任务账本 / 回读证据']
    ];
    box.innerHTML =
      '<div class="xf9">' +
      '<div class="xf9-h card"><div><b>9.0 · AI 运营中心</b> <span class="tag ok">晓风全功能复刻</span> ' +
        '<span class="tiny">' + esc(c.source) + '</span></div>' +
        '<div class="tiny">路线 ' + esc(c.counts.routes) + ' · 分组 ' + esc(c.counts.groups) +
        ' · 构建 ' + esc(c.built_at) + ' · 今天 ' + esc(st.day) + '</div></div>' +
      '<div class="xf9-tabs">' + tabs.map(t =>
        '<button class="xf9-tab' + (S.tab === t[0] ? ' on' : '') + '" data-t="' + t[0] + '">' + esc(t[1]) + '</button>'
      ).join('') + '</div><div id="xf9body" class="xf9-body"></div></div>';
    box.querySelectorAll('.xf9-tab').forEach(b => b.onclick = async () => {
      S.tab = b.dataset.t;
      if (S.tab === 'flow' || S.tab === 'tasks') {
        try { S.state = await get('state'); } catch (e) {}
      }
      paint(box);
    });
    const body = box.querySelector('#xf9body');
    if (S.tab === 'flow') paintFlow(body);
    else if (S.tab === 'all') paintAll(body);
    else if (S.tab === 'remake') paintRemake(body);
    else if (S.tab === 'principle') paintPrinciple(body);
    else paintTasks(body);
  }

  /* ---------- 今日一趟：状态机可推进，标成功必须带平台回读 ---------- */
  function paintFlow(box) {
    const rows = S.state.flow || [];
    const meta = {}; (S.cat.items || []).forEach(i => meta[i.path] = i);
    box.innerHTML =
      '<div class="card xf9-bar"><b>每天一趟 · ' + rows.length + ' 步</b> ' +
        '<span class="tiny">读平台线索 → 每步一个动作 → 平台回读，数字真变了才算有效</span> ' +
        '<button class="mini" id="xf9Init">排今日一趟</button> ' +
        '<button class="mini" id="xf9Reset">清空</button></div>' +
      (rows.length ? '<div class="xf9-flow">' + rows.map((t, i) => {
        const m = meta[t.path] || {};
        const next = (S.state.allowed[t.status] || []);
        return '<div class="card xf9-step">' +
          '<div class="xf9-step-h"><span class="xf9-n">' + (i + 1) + '</span> <b>' + esc(t.title) + '</b> ' +
            '<span class="tiny">' + esc(t.id) + ' · ' + esc(t.path) + '</span> ' + badge(t.status) +
            '<span class="tag">' + esc(m.owner || 'local') + '</span></div>' +
          '<div class="tiny xf9-steps">' + (m.steps || []).map((s, j) => (j + 1) + '. ' + esc(s)).join('　→　') + '</div>' +
          (t.evidence ? '<div class="tiny">回读：' + esc(t.evidence) + '</div>' : '') +
          '<div class="xf9-ops">' + next.map(n =>
            '<button class="mini" data-id="' + esc(t.id) + '" data-to="' + esc(n) + '">' + esc(n) + '</button>').join('') +
            ' <span class="tiny">' + esc(t.at) + '</span></div></div>';
      }).join('') + '</div>'
        : '<div class="card tiny">还没排今天这一趟。点「排今日一趟」，按最短路径自动生成 ' + S.cat.flow.length + ' 步。</div>');

    const init = box.querySelector('#xf9Init');
    if (init) init.onclick = async () => { await post('flow/init', {}); await load(box.closest('.pageholder') || box.parentNode.parentNode.parentNode); };
    const rst = box.querySelector('#xf9Reset');
    if (rst) rst.onclick = async () => { await post('flow/reset', {}); await load(box.closest('.pageholder') || box.parentNode.parentNode.parentNode); };
    box.querySelectorAll('[data-to]').forEach(b => b.onclick = () => advance(b.dataset.id, b.dataset.to, box));
  }

  async function advance(id, to, box) {
    let evidence = '', note = '';
    if (to === '成功') {
      evidence = window.prompt('填平台回读结果（前后状态确实变了才算成功，不许只填「提交成功」）：', '') || '';
      if (!evidence.trim()) { alert('没有回读证据，不能标成功'); return; }
    }
    if (to === '失败') note = window.prompt('失败原因（原文）：', '') || '';
    const r = await post('task/advance', { id: id, to: to, evidence: evidence, note: note });
    if (!r.ok) { alert(r.error || '推进失败'); return; }
    const holder = box.closest('.pageholder');
    await load(holder);
  }

  /* ---------- 全套功能树 ---------- */
  function paintAll(box) {
    const groups = S.cat.groups;
    const q = S.q.trim();
    const items = (S.cat.items || []).filter(i =>
      (S.grp === 'all' || i.group === S.grp) &&
      (!q || (i.title + i.path + (i.eps || []).join(' ') + (i.steps || []).join(' ')).indexOf(q) >= 0));
    box.innerHTML =
      '<div class="card xf9-bar"><input id="xf9q" class="func" type="search" placeholder="搜功能 / 端点 / 步骤" value="' + esc(S.q) + '">' +
      '<select id="xf9grp"><option value="all">全部分组（' + S.cat.counts.routes + '）</option>' +
        groups.map(g => '<option value="' + g.id + '"' + (S.grp === g.id ? ' selected' : '') + '>' + esc(g.icon + ' ' + g.title) + '</option>').join('') +
      '</select><span class="tiny">命中 ' + items.length + ' 项 · 每项都标了端点 / 步骤 / 出处</span></div>' +
      items.map(i => '<div class="card xf9-item"><div class="xf9-step-h"><b>' + esc(i.title) + '</b> ' +
        '<span class="tiny">' + esc(i.path) + '</span> <span class="tag">' + esc(i.owner) + '</span></div>' +
        '<div class="tiny">' + (i.eps || []).map(e => '<code>' + esc(e) + '</code>').join(' ') + '</div>' +
        '<div class="tiny xf9-steps">' + (i.steps || []).map((s, j) => (j + 1) + '. ' + esc(s)).join('　→　') + '</div>' +
        '<div class="tiny">出处：' + esc(i.src) + '</div></div>').join('');
    const qi = box.querySelector('#xf9q');
    if (qi) qi.oninput = () => { S.q = qi.value; const p = qi.selectionStart; paintAll(box); const n = box.querySelector('#xf9q'); n.focus(); n.setSelectionRange(p, p); };
    const gs = box.querySelector('#xf9grp');
    if (gs) gs.onchange = () => { S.grp = gs.value; paintAll(box); };
  }

  /* ---------- 爆款视频复刻：老师教的那条完整链 ---------- */
  function paintRemake(box) {
    const it = (S.cat.items || []).find(i => i.path === '/viral_remake/video') || {};
    const c = S.cat;
    box.innerHTML =
      '<div class="card"><b>老师教的那条：同款商品的短视频既然已经火过，说明素材对这类人群有效</b>' +
      '<div class="tiny">拿它去投流或混剪，火的概率比自己原创高很多，还省下拍摄剪辑时间。' +
      '本页复刻它的完整操作路径，不自动下载、不自动复刻、不自动投流。</div></div>' +
      '<div class="card"><div class="xf9-step-h"><b>① 选本店已上架商品 → 后台自动匹配同款竞品数据</b></div>' +
      '<div class="tiny">转化率 / 成交金额 / 广告费 / 哪些视频爆火（18 秒那段后台录屏示意的就是这块数据面板）。' +
      '数据源：<code>compass.jinritemai.com/.../competitor_analysis/product_rank_v2</code> + ' +
      '<code>business_chance_center/clue/mixed/real_time_list</code>、<code>profit/list</code>。</div></div>' +
      '<div class="card"><div class="xf9-step-h"><b>② 看爆款视频 → 一键复刻 / 直接下载</b></div>' +
      '<div class="tiny">' + (it.eps || []).map(e => '<code>' + esc(e) + '</code>').join(' ') + '</div>' +
      '<div class="xf9-steps tiny">' + (it.steps || []).map((s, j) => (j + 1) + '. ' + esc(s)).join('　→　') + '</div>' +
      '<div class="tiny">筛选维度原文：广告素材 / 非广告素材 · 销售额 · 商品点击 · 数据日期；' +
      '复刻任务状态：待开始 → 进行中 → 成功 / 失败 → 重试；「提交后不能取消」「消耗算力确认」。</div></div>' +
      '<div class="card"><div class="xf9-step-h"><b>③ 拿到成片 → 投流 / 二次剪辑 / 混剪</b></div>' +
      '<div class="tiny">作品列表「下载成片 / 下载全部 / 视频分析 / 图文分析」，下载后走本地混剪（下面那个合成器就是混剪底座）。' +
      '本页不做去水印、不做绕过平台规则，素材须有合法使用权限。</div></div>' +
      '<div class="card"><div class="xf9-step-h"><b>④ 回读：复刻任务真的成了吗</b></div>' +
      '<div class="tiny"><code>/productRecommendation/task/list</code> + <code>task/detail</code> 读状态；' +
      '投流效果回官方千川页看。点「成功」必须在这套 9.0 状态机里填回读结果。</div></div>' +
      '<div class="card tiny">出处：' + esc(it.src || 'recommendation-8zc_19MH.js / works-6NgiIQtl.js / video-CgSHeDQ0.js') + '</div>';
  }

  /* ---------- 视频原理 + 本地试合成 ---------- */
  function paintPrinciple(box) {
    const v = S.cat.video_principle;
    box.innerHTML =
      '<div class="card"><div class="xf9-step-h"><b>先回答：视频的原理到底怎么弄</b> <span class="tag ok">已拆到函数级</span></div>' +
      '<div class="tiny">' + esc(v.file) + ' · 导出 <code>' + esc(v.export) + '</code> · 默认 ' +
      esc(JSON.stringify(v.defaults)) + '</div>' +
      '<ol class="xf9-ol">' + v.pipeline.map(s => '<li>' + esc(s) + '</li>').join('') + '</ol>' +
      '<div class="tiny"><b>三种视频来源（上一轮漏掉的就是这里）：</b><br>' +
      v.three_sources.map(s => '· ' + esc(s)).join('<br>') + '</div>' +
      '<div class="tiny"><b>挂到商品：</b>' + esc(v.apply_to_product) + '</div>' +
      '<div class="tiny"><b>回读：</b>' + esc(v.readback) + '</div>' +
      '<div class="tiny"><b>限制：</b>' + esc(v.limits) + ' · ' + esc(v.concurrency) + '</div></div>' +
      '<div class="card"><div class="xf9-step-h"><b>本地试合成（原版合成器，全程不联网）</b></div>' +
      '<div class="tiny">选 1–5 张图 → 调 <code>Ea()</code>（=chrome 里那个 WebCodecs 合成器）→ 出 mp4 → 直接下载。' +
      '这一步证明原理能跑，不是「搬了个界面」。</div>' +
      '<div class="xf9-bar"><input type="file" id="xf9imgs" accept="image/*" multiple> ' +
      '<select id="xf9mode"><option value="kenburns">Ken Burns 缩放平移</option><option value="fade">淡入淡出</option><option value="slide">左右滑动</option></select> ' +
      '<select id="xf9dur"><option value="5">5 秒</option><option value="15" selected>15 秒</option><option value="30">30 秒</option></select> ' +
      '<button class="mini" id="xf9gen">开始合成</button></div>' +
      '<div id="xf9prog" class="tiny">未开始</div><div id="xf9out"></div></div>';
    const btn = box.querySelector('#xf9gen');
    if (btn) btn.onclick = async () => {
      const fs = Array.from(box.querySelector('#xf9imgs').files || []);
      const pr = box.querySelector('#xf9prog');
      if (!fs.length) { pr.textContent = '请先选图片'; return; }
      btn.disabled = true; pr.textContent = '加载合成器…';
      try {
        const mod = await import('/vendor9/useLocalShortVideo-Drins-0G.js');
        const Ea = mod.r, Da = mod.n, Oa = mod.t;
        const urls = fs.map(f => URL.createObjectURL(f));
        pr.textContent = '合成中（原版 Ea）：0%';
        const out = await Ea({
          images: urls, bgm: null,
          transformMode: box.querySelector('#xf9mode').value,
          duration: +box.querySelector('#xf9dur').value,
          width: 720, height: 1280, fps: 30, bgmVolume: 0.6
        }, e => { pr.textContent = '合成中（原版 Ea）：' + Math.round(e.progress * 100) + '% · ' + esc(e.message || ''); });
        pr.textContent = '完成：' + out.width + '×' + out.height + ' · ' + out.duration + 's · ' +
          Math.round(out.blob.size / 1024) + ' KB · ' + out.mimeType;
        const a = document.createElement('a');
        a.href = out.url; a.download = '9.0-本地试合成-' + Date.now() + '.mp4';
        a.textContent = '↓ 下载合成出来的 mp4'; a.className = 'mini';
        const vv = document.createElement('video');
        vv.src = out.url; vv.controls = true; vv.width = 260;
        const o = box.querySelector('#xf9out'); o.innerHTML = ''; o.appendChild(vv); o.appendChild(a);
        urls.forEach(u => URL.revokeObjectURL(u));
      } catch (e) {
        pr.textContent = '合成失败：' + esc(e.message || e);
      } finally { btn.disabled = false; }
    };
  }

  /* ---------- 任务账本 ---------- */
  async function paintTasks(box) {
    const r = await get('tasks');
    box.innerHTML = '<div class="card"><b>任务账本</b> <span class="tiny">共 ' + r.total +
      ' 条 · 只记本地真实状态转移，不冒充平台结果</span></div>' +
      (r.rows || []).slice().reverse().map(x =>
        '<div class="card tiny"><b>' + esc(x.ev) + '</b> · ' + esc(x.t) +
        (x.id ? ' · ' + esc(x.id) : '') + (x.path ? ' · ' + esc(x.path) : '') +
        (x.frm ? ' · ' + esc(x.frm) + ' → ' + esc(x.to) : '') +
        (x.evidence ? ' · 回读：' + esc(x.evidence) : '') +
        (x.note ? ' · ' + esc(x.note) : '') + '</div>').join('');
  }

  /* ---------- 页面注册 ---------- */
  P.xf9Hub = function (holder) { load(holder); };
  P.xf9All = function (holder) { S.tab = 'all'; load(holder); };
  P.xf9Remake = function (holder) { S.tab = 'remake'; load(holder); };
  P.xf9Principle = function (holder) { S.tab = 'principle'; load(holder); };
  P.xf9Tasks = function (holder) { S.tab = 'tasks'; load(holder); };
})();
