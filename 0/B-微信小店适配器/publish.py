# -*- coding: utf-8 -*-
"""6.0 上架平台 —— 待上架队列 / 网页后台跳转 / 上架执行 / 运行日志。

设计原则（写死，改之前想清楚）：
- 队列只读：packs 里的包、微店商品，一律不移动、不删除、不覆盖任何文件。
- 上架动作一律先落日志再执行；失败原因原样回传，不吞错、不编成功。
- 只有实测过的后台地址才标 verified=True，未验证的在前端显示「未验证」。
"""
import csv, io, json, os, subprocess, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
LOG = os.path.join(DATA, 'publish-log.jsonl')
CFG = os.path.join(ROOT, 'config.json')
os.makedirs(DATA, exist_ok=True)

# ---- 网页后台跳转（verified 表示本轮实测能打开） ----
LINKS = [
    {'k': 'dd_draft', 'name': '抖店 · 草稿箱', 'url': 'https://fxg.jinritemai.com/ffa/g/draft',
     'note': '已存草稿都在这里，点「编辑」进去改', 'verified': True},
    {'k': 'dd_create', 'name': '抖店 · 新建商品', 'url': 'https://fxg.jinritemai.com/ffa/g/create',
     'note': '从零建品（AI 建品页）', 'verified': True},
    {'k': 'dd_product', 'name': '抖店 · 商品管理', 'url': 'https://fxg.jinritemai.com/ffa/g/list',
     'note': '在售 / 下架商品列表', 'verified': False},
    {'k': 'dd_home', 'name': '抖店 · 商家后台', 'url': 'https://fxg.jinritemai.com',
     'note': '后台首页，其余入口从这里进', 'verified': False},
    {'k': 'wx_overview', 'name': '微店 · 后台概览（本机）', 'url': '/wx/overview',
     'note': '本机页面，不用登录微信后台', 'verified': True},
    {'k': 'wx_products', 'name': '微店 · 商品列表（本机）', 'url': '/products',
     'note': '直连 channels/ec，563 件真数据', 'verified': True},
]

# ---- 上架脚本（ego 自动化，跑抖店后台） ----
SCRIPT_DIR = os.environ.get('PUBLISH_SCRIPT_DIR', r'C:\Users\Administrator\ego\doudian-publish-20261029-2050')
NODE = os.environ.get('PUBLISH_NODE', r'C:\Program Files\nodejs\node.exe')

_JOB = {'id': None, 'state': 'idle', 'cmd': '', 'started': None, 'ended': None, 'code': None, 'tail': []}
_JOB_LOCK = threading.Lock()


def cfg():
    try:
        return json.load(open(CFG, encoding='utf-8'))
    except Exception:
        return {}


def packs_dir():
    c = cfg().get('packs_dir')
    if c and os.path.isdir(c):
        return c
    p = os.path.normpath(os.path.join(ROOT, '..', 'taobao-picker', 'packs'))
    return p if os.path.isdir(p) else ''


def _read_text(fp, limit=None):
    raw = open(fp, 'rb').read(limit or 400000)
    for enc in ('gb18030', 'utf-8-sig', 'utf-8'):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode('latin-1')


def list_packs(keyword='', limit=60):
    """扫 packs 下所有 csv，返回包清单（只读表头与前几行，不载全文）。"""
    d = packs_dir()
    if not d:
        return []
    out = []
    for root, _dirs, files in os.walk(d):
        for fn in files:
            if not fn.lower().endswith('.csv'):
                continue
            fp = os.path.join(root, fn)
            if keyword and keyword not in fn:
                continue
            try:
                st = os.stat(fp)
                txt = _read_text(fp, 60000)
                rows = list(csv.reader(io.StringIO(txt)))
                head = rows[0] if rows else []
                body = [r for r in rows[1:] if any(c.strip() for c in r)]
                out.append({
                    'name': os.path.splitext(fn)[0], 'file': fn,
                    'dir': os.path.basename(root) or 'packs',
                    'path': fp, 'size': st.st_size, 'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime)),
                    'cols': len(head), 'rows': len(body),
                    'head': head[:19], 'sample': (body[0][:6] if body else []),
                })
            except Exception as e:
                out.append({'name': fn, 'file': fn, 'dir': '', 'path': fp, 'error': str(e), 'rows': 0, 'cols': 0})
    out.sort(key=lambda x: x.get('mtime', ''), reverse=True)
    return out[:limit]


def pack_rows(path, limit=200):
    """读一个包的前 N 行，返回 {head, rows}。路径必须在本项目的 packs 根内。"""
    d = packs_dir()
    if not d or not os.path.isfile(path):
        return {'ok': False, 'error': '文件不存在'}
    ap, ad = os.path.abspath(path), os.path.abspath(d)
    if not ap.startswith(ad):
        return {'ok': False, 'error': '包不在允许的目录内'}
    txt = _read_text(ap)
    rows = list(csv.reader(io.StringIO(txt)))
    head = rows[0] if rows else []
    body = [r for r in rows[1:] if any(c.strip() for c in r)][:limit]
    return {'ok': True, 'head': head, 'rows': body, 'total': len(rows) - 1}


def script_status():
    dd = os.path.isdir(SCRIPT_DIR)
    files = []
    if dd:
        files = sorted([f for f in os.listdir(SCRIPT_DIR) if f.endswith('.mjs')])
    return {'dir': SCRIPT_DIR, 'dir_ok': dd, 'node': NODE, 'node_ok': os.path.isfile(NODE),
            'scripts': files[:40], 'script_count': len(files)}


def _log(rec):
    rec['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def log_tail(n=80):
    if not os.path.exists(LOG):
        return []
    lines = open(LOG, encoding='utf-8').read().strip().split('\n')
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[::-1]


def _runner(cmd, cwd):
    """后台跑脚本，stdout/stderr 进日志。不阻塞 HTTP。"""
    with _JOB_LOCK:
        jid = time.strftime('%Y%m%d-%H%M%S')
        _JOB.update({'id': jid, 'state': 'running', 'cmd': cmd, 'started': time.strftime('%H:%M:%S'),
                     'ended': None, 'code': None, 'tail': []})
    _log({'kind': 'start', 'id': jid, 'cmd': cmd})

    def run():
        try:
            p = subprocess.Popen(cmd, cwd=cwd, shell=False, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                 creationflags=0x08000000)  # CREATE_NO_WINDOW
            for line in p.stdout:
                line = line.rstrip()
                if not line:
                    continue
                with _JOB_LOCK:
                    _JOB['tail'] = (_JOB['tail'] + [line])[-300:]
                _log({'kind': 'out', 'id': jid, 'line': line[:2000]})
            p.wait()
            state = 'done' if p.returncode == 0 else 'failed'
            with _JOB_LOCK:
                _JOB.update({'state': state, 'ended': time.strftime('%H:%M:%S'), 'code': p.returncode})
            _log({'kind': 'end', 'id': jid, 'code': p.returncode, 'state': state})
        except Exception as e:
            with _JOB_LOCK:
                _JOB.update({'state': 'failed', 'ended': time.strftime('%H:%M:%S'), 'code': -1,
                             'tail': (_JOB['tail'] + ['启动失败：' + str(e)])[-300:]})
            _log({'kind': 'error', 'id': jid, 'error': str(e)})

    threading.Thread(target=run, daemon=True).start()
    return {'ok': True, 'id': jid, 'state': 'running', 'cmd': cmd}


def run_script(script, args=None, publish_mode=None):
    """跑一个上架脚本。script 必须落在 SCRIPT_DIR 内（防越界）。

    发布模式断言（链A）：本上货台当前只允许显式声明的 draft 语义。
    publish_mode 缺省 / 未知 / submit_review / publish_live 一律拒绝执行，
    不启动任何子进程（零副作用）。不依赖默认参数兜底。
    """
    VALID = ('draft', 'submit_review', 'publish_live')
    pm = None if publish_mode is None else str(publish_mode).strip()
    if pm is None or pm == '':
        return {'ok': False, 'error': 'publish_mode 缺失：上架执行必须显式声明模式，未启动脚本'}
    if pm not in VALID:
        return {'ok': False, 'error': 'publish_mode 未知："%s" 不在合法枚举 %s，未启动脚本' % (pm, list(VALID))}
    if pm != 'draft':
        return {'ok': False, 'error': 'publish_mode="%s" 当前不进入实际执行流程（仅 draft 可用），未启动脚本' % pm}
    st = script_status()
    if not st['dir_ok']:
        return {'ok': False, 'error': '上架脚本目录不存在：' + SCRIPT_DIR}
    if not st['node_ok']:
        return {'ok': False, 'error': '找不到 node：' + NODE}
    name = os.path.basename(str(script or ''))
    fp = os.path.join(SCRIPT_DIR, name)
    if not name.endswith('.mjs') or not os.path.isfile(fp):
        return {'ok': False, 'error': '脚本不存在：' + name}
    cmd = [NODE, fp] + [str(a) for a in (args or [])]
    return _runner(cmd, SCRIPT_DIR)


def check():
    """一键自检：三个真东西在不在——包目录、node、上架脚本。"""
    d = packs_dir()
    st = script_status()
    items = [
        {'name': '上货台包目录', 'ok': bool(d), 'detail': d or '未找到 taobao-picker/packs'},
        {'name': 'node 运行时', 'ok': st['node_ok'], 'detail': NODE},
        {'name': '上架脚本目录', 'ok': st['dir_ok'], 'detail': '%s（%d 个脚本）' % (SCRIPT_DIR, st['script_count'])},
    ]
    return {'ok': all(i['ok'] for i in items), 'items': items}


# ---------------------------------------------------------------- HTTP 分发
def api_get(p, q):
    g = lambda k, d='': (q.get(k) or [d])[0]
    if p == '/api/publish/links':
        return {'ok': True, 'rows': LINKS}
    if p == '/api/publish/packs':
        return {'ok': True, 'dir': packs_dir(), 'rows': list_packs(g('q'), int(g('limit', '60')))}
    if p == '/api/publish/pack':
        return pack_rows(g('path'), int(g('limit', '200')))
    if p == '/api/publish/script':
        return dict({'ok': True}, **script_status())
    if p == '/api/publish/log':
        return {'ok': True, 'rows': log_tail(int(g('n', '80')))}
    if p == '/api/publish/job':
        with _JOB_LOCK:
            return {'ok': True, 'job': dict(_JOB)}
    if p == '/api/publish/check':
        return check()
    return {'ok': False, 'error': 'unknown publish endpoint: ' + p}


def api_post(p, b):
    if p == '/api/publish/run':
        # 6.0「上架平台」同样走链A，显式声明 draft，不依赖默认值。
        return run_script(b.get('script'), b.get('args') or [], publish_mode='draft')
    return {'ok': False, 'error': 'unknown publish endpoint: ' + p}
