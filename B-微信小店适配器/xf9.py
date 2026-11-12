# -*- coding: utf-8 -*-
"""9.0 AI 运营中心后端：功能树 + 今日一趟状态机 + 任务账本。
只读 data/xf9_catalog.json；只写 data/xf9_state.json / xf9_ledger.jsonl。
不碰 v3.db、不发任何真实平台请求、不自动执行正式发布/报名/改价。
正式动作一律「AI 准备 → 人工确认」，状态机里的「成功」必须带平台回读结果。"""
import os, json, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
CATALOG = os.path.join(DATA, 'xf9_catalog.json')
STATE = os.path.join(DATA, 'xf9_state.json')
LEDGER = os.path.join(DATA, 'xf9_ledger.jsonl')
_LOCK = threading.Lock()

ALLOWED = {
    '待开始': ['进行中', '已终止'],
    '进行中': ['成功', '失败', '已暂停', '已终止'],
    '已暂停': ['进行中', '已终止'],          # 续跑复用「进行中」，与晓风一致
    '成功':   [],
    '失败':   ['进行中', '已终止'],          # 失败 → 进行中 = 重试
    '已终止': [],
}


def _catalog():
    with open(CATALOG, 'r', encoding='utf-8') as f:
        return json.load(f)


def _state():
    if os.path.isfile(STATE):
        try:
            with open(STATE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'day': time.strftime('%Y-%m-%d'), 'flow': [], 'seq': 0}


def _save(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _log(ev, **kw):
    rec = {'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'ev': ev}
    rec.update(kw)
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def _find(s, tid):
    return next((x for x in s['flow'] if x['id'] == tid), None)


def _ok(**kw):
    d = {'ok': True}
    d.update(kw)
    return d


def _no(msg):
    return {'ok': False, 'error': msg}


def api_get(p, q):
    sub = p[len('/api/xf9/'):].strip('/')
    if sub == 'health':
        c = _catalog()
        return _ok(version=c['version'], routes=c['counts']['routes'],
                   groups=c['counts']['groups'], built_at=c['built_at'],
                   catalog_exists=os.path.isfile(CATALOG))
    if sub == 'catalog':
        return _ok(**_catalog())
    if sub == 'state':
        s = _state()
        return _ok(day=s['day'], flow=s['flow'], allowed=ALLOWED, seq=s['seq'])
    if sub == 'tasks':
        rows = []
        if os.path.isfile(LEDGER):
            with open(LEDGER, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        return _ok(rows=rows[-200:], total=len(rows))
    return _no('unknown endpoint ' + p)


def api_post(p, b):
    sub = p[len('/api/xf9/'):].strip('/')
    b = b or {}
    with _LOCK:
        s = _state()

        if sub == 'flow/init':
            c = _catalog()
            titles = {i['path']: i['title'] for i in c['items']}
            s['flow'] = []
            s['seq'] = 0
            for path in c['flow']:
                s['seq'] += 1
                s['flow'].append({
                    'id': 'T%03d' % s['seq'], 'path': path,
                    'title': titles.get(path, path), 'status': '待开始',
                    'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'note': '', 'evidence': ''})
            s['day'] = time.strftime('%Y-%m-%d')
            _save(s)
            _log('flow/init', day=s['day'], steps=len(s['flow']))
            return _ok(flow=s['flow'], msg='今日一趟已按最短路径排好 %d 步' % len(s['flow']))

        if sub == 'flow/reset':
            _log('flow/reset', day=s['day'], steps=len(s['flow']))
            s['flow'] = []; s['seq'] = 0
            _save(s)
            return _ok(flow=[], msg='已清空')

        if sub == 'task/add':
            path = (b.get('path') or '').strip()
            title = (b.get('title') or path).strip()
            if not path:
                return _no('path 不能为空')
            hit = next((x for x in s['flow'] if x['path'] == path and x['status'] not in ('已终止',)), None)
            if hit:
                return _no('这一趟里已经有这一步了：' + hit['id'])
            c = _catalog()
            meta = next((i for i in c['items'] if i['path'] == path), None)
            s['seq'] += 1
            t = {'id': 'T%03d' % s['seq'], 'path': path, 'title': title,
                 'status': '待开始', 'at': time.strftime('%Y-%m-%d %H:%M:%S'),
                 'note': '', 'evidence': '', 'eps': (meta or {}).get('eps', [])}
            s['flow'].append(t)
            _save(s)
            _log('task/add', id=t['id'], path=path, title=title)
            return _ok(task=t, flow=s['flow'])

        if sub == 'task/advance':
            tid = (b.get('id') or '').strip()
            to = (b.get('to') or '').strip()
            t = _find(s, tid)
            if not t:
                return _no('任务不存在：' + tid)
            if to not in ALLOWED.get(t['status'], []):
                return _no('不允许 %s → %s（本态只能到：%s）'
                           % (t['status'], to, '/'.join(ALLOWED.get(t['status'], [])) or '无'))
            # 铁律 18：点成功必须带平台回读结果，不许「点了就报成功」
            if to == '成功' and not (b.get('evidence') or '').strip():
                return _no('要标「成功」必须填平台回读结果（前后状态确实变了才算）')
            frm = t['status']
            t['status'] = to
            t['at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            if b.get('note') is not None: t['note'] = str(b.get('note') or '')
            if b.get('evidence') is not None: t['evidence'] = str(b.get('evidence') or '')
            _save(s)
            _log('task/advance', id=tid, path=t['path'], frm=frm, to=to,
                 platform=b.get('platform') or '', shop=b.get('shop') or '',
                 product_id=b.get('product_id') or '',
                 before=b.get('before') or '', after=b.get('after') or '',
                 evidence=t['evidence'], note=t['note'])
            return _ok(task=t, flow=s['flow'])

        return _no('unknown endpoint ' + p)
