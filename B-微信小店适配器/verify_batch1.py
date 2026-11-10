# -*- coding: utf-8 -*-
"""批次一验收：抖店缺陷1/2/3 修复后的 9 项实测。全离线、零真实副作用。

运行：python verify_batch1.py
- 缺陷1：/api/dd/queue 返回货源包（PB.list_packs），/api/dd/enqueue_list 才是入队记录。
- 缺陷2：enqueue 缺/未知/非 draft 模式一律拒绝且零副作用；draft 正常入队；
          run_script 非 draft 拒绝；api_post('/api/dd/run') 显式传 draft 走链A。
- 缺陷3：草稿清单按 date 降序 + 同 pid 去重保留最新快照。
- 零真实副作用：所有会写盘的操作都把路径 monkeypatch 到临时目录。
"""
import hashlib
import importlib
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 验收环境无 requests：注入零依赖占位，避免 import publish 失败。
# 占位 post 一旦被真调用会直接抛错，从而证明验收全程没发任何真实上传/更新请求。
if 'requests' not in sys.modules:
    _r = types.ModuleType('requests')
    def _forbidden_post(*a, **k):
        raise AssertionError('验收期间不应发生真实 requests.post')
    _r.post = _forbidden_post
    sys.modules['requests'] = _r

import dd            # noqa: E402
import publish as PB  # noqa: E402

results = []


def rec(item, passed, detail=''):
    results.append({'item': item, 'pass': bool(passed), 'detail': str(detail)[:400]})


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------- 1. py_compile ----------
import py_compile
c1 = py_compile.compile(os.path.join(HERE, 'dd.py'), cfile=os.path.join(tempfile.mkdtemp(), 'dd.pyc'), doraise=True)
c2 = py_compile.compile(os.path.join(HERE, 'publish.py'), cfile=os.path.join(tempfile.mkdtemp(), 'p.pyc'), doraise=True)
rec('1 py_compile (dd.py+publish.py)', os.path.isfile(c1) and os.path.isfile(c2), 'dd=%s publish=%s' % (c1, c2))

# ---------- 缺陷1：/api/dd/queue = 货源包 ----------
q = dd.api_get('/api/dd/queue', {'q': [''], 'limit': ['80']})
rows = q.get('rows') or []
is_pack = isinstance(rows, list)
if rows:
    keys = set(rows[0].keys())
    # 货源包字段 name/dir/rows/cols/size/mtime；入队记录是 ts/pid/title/xlsx/state
    is_pack = is_pack and 'pid' not in keys and 'xlsx' not in keys and 'state' not in keys
rec('2 缺陷1: /api/dd/queue 返回货源包', is_pack,
    'rows=%d keys=%s' % (len(rows), sorted(rows[0].keys()) if rows else '空'))

# ---------- 缺陷1：/api/dd/enqueue_list = 入队记录 ----------
el = dd.api_get('/api/dd/enqueue_list', {})
has_total = 'total' in el and 'rows' in el
rec('3 缺陷1: /api/dd/enqueue_list 返回入队记录', has_total, 'total=%s' % el.get('total'))

# ---------- 缺陷2：enqueue 拒绝 + 零副作用（隔离目录） ----------
with tempfile.TemporaryDirectory() as td:
    dd.DY_XLSX_DIR = td
    dd.QUEUE = os.path.join(td, 'dd_queue.jsonl')
    open(dd.QUEUE, 'w', encoding='utf-8').close()
    # 造一个 16 列假模板
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    for c, v in enumerate(dd.DD_COLS, 1):
        ws.cell(1, c).value = v
    tpl = os.path.join(td, 'tpl.xlsx')
    wb.save(tpl); dd.TPL_XLSX = tpl

    base = {'title': '验收测试', 'pid': 'E1VERIFY', 'head_img': 'x', 'video_url': '', 'detail': '', 'price': '100', 'op': 'add'}
    rejects = [
        ('缺 publish_mode', dict(base)),
        ('未知模式', dict(base, publish_mode='weird')),
        ('submit_review', dict(base, publish_mode='submit_review')),
        ('publish_live', dict(base, publish_mode='publish_live')),
    ]
    all_ok = True
    for label, item in rejects:
        before = sorted(os.listdir(td))
        r = dd.enqueue(item)
        after = sorted(os.listdir(td))
        ok = (not r.get('ok')) and ('publish_mode' in r.get('error', '')) and before == after
        all_ok = all_ok and ok
        print('  [拒绝] %-16s -> %s | 零副作用=%s' % (label, r.get('error'), before == after))
    rec('4 缺陷2: enqueue 缺/未知/非draft 拒绝+零副作用', all_ok)

    # draft 正常入队（隔离目录）
    r = dd.enqueue(dict(base, publish_mode='draft'))
    xlsx_path = os.path.join(td, 'dy-E1VERIFY.xlsx')
    qf = os.path.join(td, 'dd_queue.jsonl')
    ok5 = bool(r.get('ok')) and os.path.isfile(xlsx_path) and os.path.isfile(qf)
    qlines = open(qf, encoding='utf-8').read().strip().splitlines() if os.path.isfile(qf) else []
    rec('5 缺陷2: draft 正常入队(生成xlsx+写队列)', ok5,
        'xlsx=%s qlines=%d state=%s' % (os.path.isfile(xlsx_path), len(qlines), r.get('item', {}).get('state')))

    # ---------- 缺陷2：run_script 非 draft 拒绝（_runner 已改成桩，永不启子进程） ----------
    ok6 = True
    for pm in [None, 'weird', 'submit_review', 'publish_live']:
        r = PB.run_script('publish-1391.mjs', [], publish_mode=pm)
        good = (not r.get('ok')) and ('publish_mode' in r.get('error', ''))
        ok6 = ok6 and good
        print('  [run_script拒] pm=%-14s -> %s' % (pm, r.get('error')))
    rec('6 缺陷2: run_script 非 draft 拒绝(不启动子进程)', ok6)

    # ---------- 缺陷2：入口显式传 draft 走链A；不传则被拒（用不存在脚本名，绝不启动真跑） ----------
    r_explicit = PB.run_script('__nonexist__.mjs', [], publish_mode='draft')
    explicit_not_mode_reject = 'publish_mode' not in r_explicit.get('error', '')
    r_default = PB.run_script('__nonexist__.mjs', [])
    default_rejected = (not r_default.get('ok')) and ('publish_mode' in r_default.get('error', ''))
    rec('7 缺陷2: 链A必须显式draft(显式放行/默认拒绝)',
        explicit_not_mode_reject and default_rejected,
        '显式->%s | 默认->%s' % (r_explicit.get('error', '已过模式断言'), r_default.get('error')))

# ---------- 缺陷3：草稿按 date 降序 + 同 pid 留最新（隔离 WORK/ART） ----------
with tempfile.TemporaryDirectory() as wk, tempfile.TemporaryDirectory() as art:
    dd.WORK = wk
    dd.ART = art
    # 造 3 个快照：pid=111 有两份(10/22旧、10/29新)，pid=222 一份(11/01)
    hits = [
        (os.path.join(wk, 'draft_s1.json'), '矮茶桌 | ID:11111111 | 待提交 | 2026/10/22', 'draft'),
        (os.path.join(wk, 'draft_s2.json'), '矮茶桌 | ID:11111111 | 待提交 | 2026/10/29', 'draft'),
        (os.path.join(art, 'draft_s3.json'), '茶台 | ID:22222222 | 待提交 | 2026/11/01', 'draft'),
    ]
    for fp, line, st in hits:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump({'hits': [line], 'status': st, 'ts': 1}, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    rows3 = dd.api_get('/api/dd/drafts', {'q': [''], 'limit': ['80']}).get('rows') or []
    pids = [r['pid'] for r in rows3]
    ok8 = (pids == ['22222222', '11111111']) and (len(rows3) == 2) and rows3[1]['date'] == '2026/10/29'
    rec('8 缺陷3: 草稿按date降序+同pid留最新', ok8, 'pids=%s dates=%s' % (pids, [r['date'] for r in rows3]))

# ---------- 回滚：改后 sha != 备份，备份可恢复 ----------
bak = os.path.join(os.pardir, '_P0-准备', '批次一备份-20261109-221756', 'dd.py')
rec('9 dd.py 改后与备份不同(备份可回滚)',
    os.path.isfile(bak) and sha(os.path.join(HERE, 'dd.py')) != sha(bak), '备份存在=%s' % os.path.isfile(bak))

print('\n===== 批次一验收结果 =====')
npass = sum(1 for r in results if r['pass'])
for r in results:
    print('%s  %s' % ('PASS' if r['pass'] else 'FAIL', r['item']))
print('小计：%d/%d 通过' % (npass, len(results)))
json.dump(results, open(os.path.join(HERE, '验收结果-batch1.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
sys.exit(0 if npass == len(results) else 1)
