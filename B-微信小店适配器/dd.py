# -*- coding: utf-8 -*-
"""7.0 抖店后台管理 —— 概览 / 草稿箱 / 货源 / 上架执行 / 后台跳转。

和 6.0 微店后台管理的区别（这点决定数据怎么拿）：
- 微店有官方 HTTP 接口，本机直连就能拉到 563 件真商品；
- 抖店没有可直连的免签接口，真实数据只能来自**ego 自动化跑出来的页面快照**。

所以本模块的数据源写死为三类产物，全部只读：
1. 页面快照  抖店研究/work/<pid>/*draftbox*.json · 抖店研究/crawl3/*draft*.json
2. 货源包    上货台 packs 目录下的 CSV（复用 publish.py）
3. 上架脚本  ego/doudian-publish-*/ 下的 .mjs（复用 publish.py）

没有快照就如实报「还没采过」，绝不编造商品与件数。
"""
import json, os, re, time, csv

import publish as PB

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.normpath(os.path.join(ROOT, '..', '抖店研究', 'work'))
ART = os.path.normpath(os.path.join(ROOT, '..', '抖店研究', 'crawl3'))

_PID = re.compile(r'ID:\s*(\d{8,25})')

# ---- 抖店后台入口（verified 只有本轮实测打开过的才为 True） ----
LINKS = [
    {'k': 'draft', 'name': '草稿箱', 'url': 'https://fxg.jinritemai.com/ffa/g/draft',
     'note': '已存草稿都在这里，点「编辑」进去改', 'verified': True},
    {'k': 'create', 'name': '新建商品', 'url': 'https://fxg.jinritemai.com/ffa/g/create',
     'note': '从零建品（AI 建品页）', 'verified': True},
    {'k': 'list', 'name': '商品管理', 'url': 'https://fxg.jinritemai.com/ffa/g/list',
     'note': '在售 / 已下架商品列表', 'verified': False},
    {'k': 'home', 'name': '商家后台首页', 'url': 'https://fxg.jinritemai.com',
     'note': '其余入口都从这里进', 'verified': False},
    {'k': 'order', 'name': '订单管理', 'url': 'https://fxg.jinritemai.com/ffa/morder/order/list',
     'note': '订单与发货', 'verified': False},
    {'k': 'after', 'name': '售后工作台', 'url': 'https://fxg.jinritemai.com/ffa/mshop/after-sale/list',
     'note': '退款退货处理', 'verified': False},
]


# ------------------------------------------------------------------ 快照读取
def _parse_drafts(txts, src):
    """从一段文本里抽出 [标题 | ID:xxx | 状态 | 日期] 这样的行。"""
    rows = []
    for s in txts:
        parts = [x.strip() for x in str(s).split('|')]
        for i, p in enumerate(parts):
            m = _PID.search(p)
            if not m:
                continue
            pid = m.group(1)
            title = parts[i - 1] if i > 0 else ''
            if title in ('操作', '创建时间', '编辑删除', '删除', '') or _PID.search(title):
                title = ''
            status = parts[i + 1] if i + 1 < len(parts) else ''
            date = parts[i + 2] if i + 2 < len(parts) else ''
            rows.append({'pid': pid, 'title': title or '(标题未读到)', 'status': status,
                         'date': date, 'src': src})
    return rows


def scan_snapshots():
    """扫所有 draft 相关快照，返回 (草稿行, 店铺信息, 快照文件列表)。"""
    files, drafts, shop = [], [], {'name': '', 'type': '', 'state': ''}
    for base in (WORK, ART):
        if not os.path.isdir(base):
            continue
        for root, _d, fns in os.walk(base):
            for fn in fns:
                low = fn.lower()
                if not low.endswith('.json') or 'draft' not in low:
                    continue
                fp = os.path.join(root, fn)
                try:
                    j = json.load(open(fp, encoding='utf-8', errors='replace'))
                except Exception:
                    continue
                files.append({'file': fn, 'dir': os.path.basename(root), 'path': fp})
                txts = []
                for k in ('hits', 'msgs'):
                    v = j.get(k)
                    if isinstance(v, list):
                        txts += [str(x) for x in v]
                for k in ('tail', 'txt', 'text'):
                    v = j.get(k)
                    if isinstance(v, str):
                        txts.append(v)
                drafts += _parse_drafts(txts, fn)
                for x in (j.get('head') or []):
                    x = str(x).strip()
                    if not x:
                        continue
                    if not shop['name'] and x.endswith('店') and 3 < len(x) < 30:
                        shop['name'] = x
                    if x in ('专卖店', '旗舰店', '专营店', '个体店', '企业店', '普通店'):
                        shop['type'] = x
                    if ('营业' in x or '停业' in x) and not shop['state']:
                        shop['state'] = x
    # 按 pid 去重，保留 date 最新的一条（同一 pid 在多个快照里重复出现时，
    # 取创建日期最大的那条，而不是 os.walk 遍历到的首条）。
    def _dkey(r):
        return str(r.get('date') or '')
    seen = {}
    for r in sorted(drafts, key=_dkey, reverse=True):
        if r['pid'] not in seen:
            seen[r['pid']] = r
    uniq = list(seen.values())
    # 草稿清单按 date 降序（最新在前）；date 为空的排末尾。
    uniq.sort(key=_dkey, reverse=True)
    return uniq, shop, sorted(
        files, key=lambda x: _mtime(x['path']), reverse=True)


def _mtime(fp):
    """取文件 mtime；取不到按 0 处理，排到最旧。"""
    try:
        return os.path.getmtime(fp)
    except OSError:
        return 0.0


# ------------------------------------------------------------------ 对外数据
def overview():
    drafts, shop, files = scan_snapshots()
    st = PB.script_status()
    packs = PB.list_packs('', 200)
    return {
        'ok': True,
        'shop': shop,
        'drafts': len(drafts),
        'drafts_ready': len([d for d in drafts if '待提交' in d['status']]),
        'packs': len(packs),
        'packs_rows': sum(p.get('rows') or 0 for p in packs),
        'scripts': st['script_count'],
        'script_dir_ok': st['dir_ok'],
        'snapshots': len(files),
        'work_dir': WORK,
        'art_dir': ART,
        'note': '抖店数据来自 ego 页面快照；没跑过就没有，不会编。',
    }


def drafts():
    rows, _shop, files = scan_snapshots()
    return {'ok': True, 'rows': rows, 'files': files,
            'total': len(rows),
            'empty_hint': '' if rows else '还没采过草稿箱快照。跑一次草稿箱脚本（产出 *draftbox*.json）这里就会有。'}


# ------------------------------------------------------------------ 抖店上货入队
# 契约（2026-11-09 复核修正，之前这里写错了）：
#   enqueue 只做两件事：把这件货按 16 列真模板落成 dy-<pid>.xlsx，再往 dd_queue.jsonl
#   登记一条 queued 记录。它本身不点抖店后台，也不会真的发布。
#   真实执行者是 ego 空间里手工跑的 ego/main/excel-route-4.mjs（POST 小猪
#   excelCopyItem/dyForm，goTarget=sale 落「售卖中」）—— 该脚本**未被接入 7.0 run 通道**。
#   7.0「上架执行」页走的是 publish.py 的 publish-*.mjs（收尾=保存草稿），与这条 xlsx 队列
#   互不相通。所以 enqueue 产出的队列**当前无人自动消费**，须人工去 ego 空间跑
#   excel-route-4.mjs 才会真的落「售卖中」；这一步必须如实叫「入队」，不得谎称由 7.0 执行。
#   7.0 run 通道（链A）在 api_post 入口显式声明 publish_mode='draft'（见 DD_RUN_MODE），
#   publish.py run_script 对该模式做断言：缺/未知/非 draft 一律拒绝，不启动脚本。
DY_XLSX_DIR = r'C:\Users\Administrator\ego\agentB\xlsx_fixed'
TPL_XLSX = os.path.normpath(os.path.join(ROOT, '..', 'taobao-picker', 'data',
                                          'templates', '抖店模板二.xlsx'))
QUEUE = os.path.join(ROOT, 'data', 'dd_queue.jsonl')

DD_COLS = ['商品ID', '商品标题', '商品分类', '商品轮播图1', '商品轮播图2', '商品轮播图3',
           'SKU规格名称', '规格1', '规格2', '规格3', 'SKU编码', 'SKU库存',
           'SKU拼单价', 'SKU预览图', '商品详情图', '商品属性']


def enqueue(item):
    """把「直接上架」里的一件货落成抖店 16 列 xlsx 并入队。
    item: {title, cat, price(分), stock, head_imgs[], detail_imgs[], attrs[], pid?, publish_mode}
    只做本地落盘，不碰真实抖店后台；真实发布由链B（ego 空间手工跑）执行。
    """
    # 发布模式校验（链B，必须在写 xlsx / 写队列之前）：
    # 合法枚举 draft / submit_review / publish_live，但当前入口只放行显式声明的 draft。
    # 缺模式 / 未知 / 非 draft 一律拒绝，零副作用（不写 xlsx、不写 dd_queue.jsonl）。
    VALID = ('draft', 'submit_review', 'publish_live')
    pm = item.get('publish_mode')
    if pm is None or str(pm).strip() == '':
        return {'ok': False, 'error': 'publish_mode 缺失：链B 入队必须显式声明模式，未产生任何副作用'}
    pm = str(pm).strip()
    if pm not in VALID:
        return {'ok': False, 'error': 'publish_mode 未知："%s" 不在合法枚举 %s，未产生任何副作用' % (pm, list(VALID))}
    if pm != 'draft':
        return {'ok': False, 'error': 'publish_mode="%s" 当前不进入实际执行流程（仅 draft 可用），未产生任何副作用' % pm}
    import openpyxl
    if not os.path.isdir(DY_XLSX_DIR):
        return {'ok': False, 'error': '抖店上架包目录不存在：' + DY_XLSX_DIR}
    if not os.path.isfile(TPL_XLSX):
        return {'ok': False, 'error': '找不到抖店 16 列模板：' + TPL_XLSX}
    pid = str(item.get('pid') or ('t' + time.strftime('%H%M%S')))
    title = (item.get('title') or '').strip()
    if not title:
        return {'ok': False, 'error': '标题为空'}
    heads = [u for u in (item.get('head_imgs') or []) if u][:3]
    details = [u for u in (item.get('detail_imgs') or []) if u]
    price = round(float(item.get('price') or 0) / 100.0, 2)   # 微信口径是分，抖店列是元
    stock = int(item.get('stock') or 0)
    cat = (item.get('cat') or '').strip()
    attrs = '；'.join('%s:%s' % (a.get('attr_key', ''), a.get('attr_value', ''))
                      for a in (item.get('attrs') or []) if a.get('attr_value'))
    row = [pid, title, cat,
           (heads[0] if len(heads) > 0 else ''), (heads[1] if len(heads) > 1 else ''),
           (heads[2] if len(heads) > 2 else ''),
           '默认', '', '', '', pid + '-1', stock, price, (heads[0] if heads else ''),
           ','.join(details), attrs]
    fp = os.path.join(DY_XLSX_DIR, 'dy-' + pid + '.xlsx')
    try:
        wb = openpyxl.load_workbook(TPL_XLSX)
        ws = wb.active
        if ws.max_row >= 3:
            ws.delete_rows(3, ws.max_row - 2)
        for c, v in enumerate(row, 1):
            if v not in (None, ''):
                ws.cell(3, c).value = v
        wb.save(fp)
    except Exception as e:
        return {'ok': False, 'error': '写 xlsx 失败：%s' % e}
    rec = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'pid': pid, 'title': title,
           'xlsx': fp, 'state': 'queued',
           'note': '待 ego 空间手工跑 excel-route-4.mjs（goTarget=sale）；当前无自动消费者'}
    os.makedirs(os.path.dirname(QUEUE), exist_ok=True)
    with open(QUEUE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return {'ok': True, 'queued': True, 'pid': pid, 'xlsx': fp,
            'msg': '已生成抖店上架包并入队（须人工到 ego 空间跑 excel-route-4.mjs 才会真的发布）'}


def queue():
    rows = []
    if os.path.isfile(QUEUE):
        for line in open(QUEUE, encoding='utf-8').read().strip().split('\n'):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return {'ok': True, 'rows': rows[::-1], 'total': len(rows), 'dir': DY_XLSX_DIR}


# ------------------------------------------------------------------ HTTP
def api_get(p, q):
    g = lambda k, d='': (q.get(k) or [d])[0]
    if p == '/api/dd/overview':
        return overview()
    if p == '/api/dd/drafts':
        return drafts()
    if p == '/api/dd/links':
        return {'ok': True, 'rows': LINKS}
    if p == '/api/dd/queue':
        return {'ok': True, 'dir': PB.packs_dir(), 'rows': PB.list_packs(g('q'), int(g('limit', '80')))}
    if p == '/api/dd/pack':
        return PB.pack_rows(g('path'), int(g('limit', '200')))
    if p == '/api/dd/script':
        return dict({'ok': True}, **PB.script_status())
    if p == '/api/dd/job':
        return PB.api_get('/api/publish/job', q)
    if p == '/api/dd/check':
        return PB.check()
    if p == '/api/dd/enqueue_list':
        return queue()
    return {'ok': False, 'error': 'unknown dd endpoint: ' + p}


# 链A（7.0「上架执行」）固定草稿语义：入口显式声明 publish_mode='draft'，
# 不依赖 run_script 的默认参数。publish-*.mjs 收尾本就是 clickText("保存草稿")。
DD_RUN_MODE = 'draft'


def api_post(p, b):
    if p == '/api/dd/run':
        return PB.run_script(b.get('script'), b.get('args') or [], publish_mode=DD_RUN_MODE)
    if p == '/api/dd/enqueue':
        return enqueue(b.get('item') or {})
    return {'ok': False, 'error': 'unknown dd endpoint: ' + p}
