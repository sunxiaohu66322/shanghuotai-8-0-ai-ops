# -*- coding: utf-8 -*-
"""微信小店 · 自营上货台 —— 本地服务（纯标准库，无第三方依赖）。

启动：python app.py        默认 http://127.0.0.1:9191
配置：同目录 config.json（appid / secret / port）；也可用环境变量 WXSHOP_APPID / WXSHOP_SECRET。
"""
import json, os, sys, time, threading, traceback, urllib.parse, mimetypes, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from wxapi import WxShop, WxError
import photo as PH
import publish as PB
import dd as DD
import union3 as U3
import xf9 as XF9

WEB = os.path.join(ROOT, 'web')
DATA = os.path.join(ROOT, 'data')
CONFIG = os.path.join(ROOT, 'config.json')
SHOPS_FILE = os.path.join(ROOT, 'shops.json')
os.makedirs(DATA, exist_ok=True)
TASKS = os.path.join(DATA, 'tasks.jsonl')
EXPORTS = os.path.join(DATA, 'exports'); os.makedirs(EXPORTS, exist_ok=True)
YUNKU = os.path.join(DATA, 'yunku'); os.makedirs(YUNKU, exist_ok=True)
UPLOADS = os.path.join(DATA, 'uploads.jsonl')
PATCH_FIELDS = {'title', 'sub_title', 'weight', 'spu_code'}

_state = {'shop': None, 'cfg': None, 'cache': {}, 'cache_at': 0,
          'shops': {}, 'caches': {}}
_lock = threading.RLock()          # 可重入：get_shop 持锁时 _cur_cache() 也会取锁
_ctx = threading.local()           # 每个请求线程一份「当前账号」，物理隔离，绝不串号


def set_shop_id(sid):
    """请求入口：把这一页 / 这一请求锁到某家店（不带 = 清掉线程残留）。"""
    _ctx.shop_id = sid or None


def current_shop_id():
    return getattr(_ctx, 'shop_id', None) or None


def _cur_cache():
    """按店分片缓存：A 店的商品/类目/概览缓存绝不能被 B 店命中。"""
    sid = current_shop_id() or '__default__'
    return _state['caches'].setdefault(sid, {})


# ---------------------------------------------------------------- 后台概览（展示台数据）
SKU_AUDIT = os.path.join(DATA, 'sku_audit.json')
OVERVIEW_FILE = os.path.join(DATA, 'overview.json')


def sku_health_cached():
    """读最近一次全店 SKU 图扫描的落盘结果（不实时扫，实时扫要 40 秒）。"""
    if not os.path.exists(SKU_AUDIT):
        return None
    try:
        rows = json.load(open(SKU_AUDIT, encoding='utf-8'))
    except Exception:
        return None
    sku_tot = noimg = 0
    bad_p = 0
    by = {}
    for r in rows:
        if (r.get('total') or 0) < 0:
            continue
        st = str(r.get('status'))
        sku_tot += r.get('total') or 0
        noimg += r.get('noimg') or 0
        if (r.get('noimg') or 0) > 0:
            bad_p += 1
            by[st] = by.get(st, 0) + 1
    return {'products': len(rows), 'sku_total': sku_tot, 'sku_noimg': noimg,
            'bad_products': bad_p, 'bad_by_status': by,
            'img_rate': round(100.0 * (sku_tot - noimg) / sku_tot, 1) if sku_tot else 0,
            'at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(SKU_AUDIT))),
            'top': [{'pid': r.get('pid'), 'st': r.get('status'), 'noimg': r.get('noimg'),
                     'total': r.get('total'), 'title': r.get('title')}
                    for r in sorted(rows, key=lambda x: -(x.get('noimg') or 0))
                    if (r.get('noimg') or 0) > 0][:20]}


_scan_state = {'on': False, 'at': '', 'done': 0, 'total': 0}


def start_sku_scan(shop_id=None):
    """后台起一次全店 SKU 图扫描（560 件约 45 秒，不能卡在请求里）。"""
    if _scan_state['on']:
        return {'ok': True, 'msg': '已在扫描中', 'running': True}
    _scan_state.update({'on': True, 'done': 0, 'total': 0, 'at': time.strftime('%H:%M:%S')})

    def run():
        set_shop_id(shop_id)          # 后台线程也锁在同一家店，别扫到别家去
        try:
            s = get_shop()
            jobs = []
            for st in ALL_STATUS:
                for i in _all_ids(s, st):
                    jobs.append((st, i))
            _scan_state['total'] = len(jobs)

            def one(job):
                st, pid = job
                try:
                    j = s.product(pid)
                    p = j.get('product') or j.get('edit_product') or {}
                    skus = p.get('skus') or []
                    noimg = sum(1 for k in skus if not (k.get('thumb_img') or '') and not (k.get('sku_imgs') or []))
                    return {'pid': pid, 'status': st, 'title': (p.get('title') or '')[:40],
                            'total': len(skus), 'noimg': noimg, 'noimg_ids': [],
                            'head': len(p.get('head_imgs') or []),
                            'desc': len((p.get('desc_info') or {}).get('imgs') or [])}
                except Exception as e:
                    return {'pid': pid, 'status': st, 'title': 'ERR ' + str(e)[:60],
                            'total': -1, 'noimg': -1, 'noimg_ids': [], 'head': 0, 'desc': 0}

            from concurrent.futures import ThreadPoolExecutor
            out = []
            with ThreadPoolExecutor(max_workers=8) as ex:
                for r in ex.map(one, jobs):
                    out.append(r)
                    _scan_state['done'] = len(out)
            tmp = SKU_AUDIT + '.tmp'
            json.dump(out, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
            os.replace(tmp, SKU_AUDIT)
            _scan_state['at'] = time.strftime('%H:%M:%S')
        except Exception as e:
            sys.stderr.write('sku scan failed: %s\n' % e)
        finally:
            _scan_state['on'] = False

    threading.Thread(target=run, name='sku-scan', daemon=True).start()
    return {'ok': True, 'msg': '已开始后台扫描（约 45 秒）', 'running': True}


def overview_all(force=False):
    """顶部「展示台」要的全部数字：一次拉齐，前端只调这一个接口。

    60 秒缓存：一次真实调用要翻 6 个状态的商品列表（实测 3–5 秒），
    页面切来切去每次都等 5 秒会像卡死；10 分钟自动刷新也远大于这个 TTL。
    """
    with _lock:
        hit = _cur_cache().get('overview')
        if hit and not force and time.time() - hit[0] < 60:
            d = dict(hit[1])
            d['cached'] = True
            return d
    s = get_shop()
    bs = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        ids_by = dict(zip(ALL_STATUS, ex.map(lambda st: _all_ids(s, st), ALL_STATUS)))
    for st, ids in ids_by.items():
        bs[str(st)] = len(ids)
    total = sum(bs.values())
    try:
        info = s.shop_info()
    except Exception as e:
        info = {'err': str(e)}
    si = info.get('info') or info.get('shop_info') or info or {}
    # 近 7 天订单（官方窗口上限 7 天，超了报 40097）
    now = int(time.time())
    orders = {'total': 0, 'err': ''}
    try:
        oj = s.orders(now - 7 * 86400 + 1, now, page_size=50)
        orders['total'] = len(oj.get('order_id_list') or [])
        orders['has_more'] = bool(oj.get('has_more'))
    except Exception as e:
        orders['err'] = str(e)[:120]
    live = bs.get('5', 0)
    out = {
        'ok': True, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'scan': dict(_scan_state),
        'shop': {'name': si.get('nickname') or '', 'username': si.get('username') or '',
                 'subject_type': si.get('subject_type') or '', 'status': si.get('status') or ''},
        'by_status': bs, 'by_status_text': {str(k): status_text(k) for k in ALL_STATUS},
        'total': total, 'live': live, 'recycle': bs.get('6', 0),
        # 平台没有开放的「展现/曝光」接口（实测 48001 api unauthorized + 40066 invalid url），
        # 所以用「上架率」代替，并在界面上写明它跟展现率不是一回事。
        'list_rate': round(100.0 * live / total, 1) if total else 0,
        'orders7': orders,
        'sku': sku_health_cached(),
        'shops': load_cfg().get('shops') or [],
        'cur_name': load_cfg().get('name') or '',
        # 三店去重总览（2 微店 + 抖店）：同款在任意店出现都只算一件。
        # 主人 2026-11-08 定的口径，顶部那排数字优先用它，不用单店叠加。
        'union3': U3.cached(),
    }
    with _lock:
        _cur_cache()['overview'] = (time.time(), out)
    # 落盘一份：服务刚起来时 LaMa 预热会占满 CPU，首次真实查询要 30 秒；
    #   先从磁盘把上次的数字顶上去，界面不至于空白（数字带 ts，用户看得到是哪次的）。
    try:
        json.dump(out, open(OVERVIEW_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass
    return out


def _seed_overview_cache():
    """启动时把上次落盘的概览塞进内存缓存（标 cached，60 秒后自然过期重算）。"""
    if not os.path.exists(OVERVIEW_FILE):
        return
    try:
        d = json.load(open(OVERVIEW_FILE, encoding='utf-8'))
        if isinstance(d, dict) and d.get('ok'):
            d['stale'] = True
            d['ts'] = d.get('ts') or ''
            _cur_cache()['overview'] = (time.time(), d)
    except Exception:
        pass


# ---------------------------------------------------------------- 配置（多店铺）
# 2026-11-01 主人要求：现在只有一家微信小店，但要把「切换其它店铺」的口子做好。
#   真源从单一 config.json 升级为 shops.json：
#     {"current":"wx6ff1...","port":9191,"shops":[{"appid":..,"secret":..,"name":..}]}
#   旧的 config.json 仍然读（首次自动迁一份出来，原文件不删），老部署不受影响。
def _load_shops():
    """读 shops.json；不存在就从 config.json 迁一份出来。"""
    if os.path.exists(SHOPS_FILE):
        try:
            d = json.load(open(SHOPS_FILE, encoding='utf-8'))
            if isinstance(d, dict) and d.get('shops'):
                return d
        except Exception:
            pass
    cfg = {'appid': os.environ.get('WXSHOP_APPID', ''), 'secret': os.environ.get('WXSHOP_SECRET', ''),
           'port': 9191}
    if os.path.exists(CONFIG):
        try:
            cfg.update(json.load(open(CONFIG, encoding='utf-8')))
        except Exception:
            pass
    d = {'current': cfg.get('appid', ''), 'port': int(cfg.get('port') or 9191), 'shops': []}
    if cfg.get('appid'):
        d['shops'].append({'appid': cfg['appid'], 'secret': cfg.get('secret', ''), 'name': '默认店铺'})
    try:
        json.dump(d, open(SHOPS_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass
    return d


def _cur_shop(d=None):
    """当前店铺那条记录（找不到就退回第一条）。"""
    d = d or _load_shops()
    cur = d.get('current') or ''
    for x in (d.get('shops') or []):
        if x.get('appid') == cur:
            return x
    return (d.get('shops') or [{}])[0] if d.get('shops') else {}


def _save_shops(d):
    json.dump(d, open(SHOPS_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def load_cfg():
    d = _load_shops()
    cur = d.get('current') or ''
    s = {}
    for x in (d.get('shops') or []):
        if x.get('appid') == cur:
            s = x
            break
    if not s and d.get('shops'):
        s = d['shops'][0]
    return {'appid': s.get('appid', ''), 'secret': s.get('secret', ''),
            'port': int(d.get('port') or 9191), 'name': s.get('name', ''),
            'shops': [{'appid': x.get('appid'), 'name': x.get('name') or ('店铺' + str(i + 1))}
                      for i, x in enumerate(d.get('shops') or [])]}


def get_shop(force=False):
    """按当前请求绑定的账号取凭据。

    ★ 2026-11-07 多账号独立空间：每家店一份 WxShop 实例，缓存按 appid 分片。
      原实现是全局单值 _state['shop']，A 页切店会让 B 页跟着变 —— 串号根因。
      店号来自请求头 X-Shop-Id（thread-local）；不传时退回 shops.json 的 current，
      老链接与后台扫描线程照旧可用。
    """
    d = _load_shops()
    sid = current_shop_id()
    s = None
    for x in (d.get('shops') or []):
        if sid:
            if x.get('appid') == sid:
                s = x
                break
        elif x.get('appid') == (d.get('current') or ''):
            s = x
            break
    if s is None:
        if sid:
            raise WxError(-2, '没有这个账号：' + str(sid), 'config')
        s = _cur_shop(d)
    if not s.get('appid') or not s.get('secret'):
        raise WxError(-2, '该账号未配置 AppID / AppSecret，请到「账号管理」填写', 'config')
    if s.get('enabled', True) is False:
        raise WxError(-2, '账号已停用：' + str(s.get('name') or s.get('appid')), 'config')
    key = s['appid']
    with _lock:
        slot = _state['shops'].setdefault(key, {})
        if slot.get('shop') is None or force or slot.get('secret') != s['secret']:
            slot['shop'] = WxShop(s['appid'], s['secret'])
            slot['secret'] = s['secret']
        _state['shop'] = slot['shop']       # 兼容仍读旧全局键的少量代码
        _state['cfg'] = {'appid': s['appid'], 'secret': s['secret'], 'name': s.get('name', '')}
        return slot['shop']


def log_task(rec):
    rec['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(TASKS, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def read_tasks(limit=200):
    if not os.path.exists(TASKS):
        return []
    lines = open(TASKS, encoding='utf-8').read().strip().split('\n')[-limit:]
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out[::-1]


# ---------------------------------------------------------------- 业务
def yuan(fen):
    try:
        return round(int(fen) / 100.0, 2)
    except Exception:
        return 0


# 微信商品状态真值 —— 以官方文档 product/get 的 Res.product.status 枚举为准
# (2026-10-29 依 https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproduct.html 校正；
#  此前把 6 当成「出售中」是错的：6 = 回收站，5 才是上架)
STATUS_TEXT = {
    0: '未上架', 5: '上架', 6: '回收站', 11: '自主下架',
    12: '售罄下架', 13: '违规下架', 14: '保证金不足', 15: '品牌过期', 20: '封禁',
}
# 编辑状态（edit_status）：商品草稿/提审这条线，和线上 status 是两套
EDIT_STATUS_TEXT = {0: '初始', 1: '编辑中', 2: '审核中', 3: '审核失败', 4: '审核成功', 7: '上传中', 8: '上传失败'}
# 「全部」合并的线上档位：上架 / 未上架 / 回收站 / 自主下架 / 售罄下架 / 违规下架
ALL_STATUS = (5, 0, 6, 11, 12, 13)


def status_text(st):
    try:
        return STATUS_TEXT.get(int(st), '状态' + str(st))
    except Exception:
        return '状态' + str(st)


def slim_product(p):
    """把微信商品结构压成表格需要的字段。"""
    if not p:
        return {}
    p = p.get('product') or p.get('edit_product') or p
    skus = p.get('skus') or []
    prices = [s.get('sale_price', 0) for s in skus if s.get('sale_price')]
    stocks = [s.get('stock_num', 0) for s in skus]
    return {
        'product_id': p.get('product_id'),
        'title': p.get('title'),
        'sub_title': p.get('sub_title') or '',
        'status': p.get('status'),
        'status_text': status_text(p.get('status')),
        'edit_status': p.get('edit_status'),
        'edit_status_text': EDIT_STATUS_TEXT.get(p.get('edit_status'), ''),
        'head_img': (p.get('head_imgs') or [None])[0],
        'head_imgs': p.get('head_imgs') or [],
        'desc_imgs': (p.get('desc_info') or {}).get('imgs') or p.get('desc_imgs') or [],
        'cat': p.get('cats') or [],
        'skus': [{'sku_id': s.get('sku_id'), 'sale_price': s.get('sale_price'),
                  'price_yuan': yuan(s.get('sale_price')), 'stock_num': s.get('stock_num', 0),
                  'sku_code': s.get('sku_code'), 'attrs': s.get('sku_attrs') or []} for s in skus],
        'sku_count': len(skus),
        'price_min': yuan(min(prices)) if prices else 0,
        'price_max': yuan(max(prices)) if prices else 0,
        'stock_total': sum(stocks) if stocks else 0,
        'updated': p.get('update_time') or p.get('last_update_time') or '',
        'raw_keys': sorted(list(p.keys()))[:40],
    }


def _p(x):
    """data_type=2 的商品详情放在 edit_product 里，普通查询放在 product 里。"""
    x = x or {}
    return x.get('product') or x.get('edit_product') or x


def _all_ids(s, status, max_pages=60, page=50):
    """按 next_key 翻页收全量商品 ID（微信 total_num 不可靠，next_key 才作数）。"""
    ids, key = [], ''
    for _ in range(max_pages):
        j = s.product_ids(status=status, page_size=page, next_key=key)
        ids += [str(i) for i in (j.get('product_ids') or [])]
        key = j.get('next_key') or ''
        if not key:
            break
    return ids


def products_page(status=None, page_size=20, cursor=''):
    """status 为空时合并 未上架(0)/已上架(5)/出售中(6)/回收站(11) —— 缺 6 会整批漏掉在售商品。"""
    s = get_shop()

    def one(pid):
        try:
            return slim_product(s.product(pid))
        except WxError as e:
            return {'product_id': pid, 'title': '(读取失败) ' + e.errmsg, 'status_text': 'err'}

    if status is not None and status != '':
        ids = _all_ids(s, status)
        total = len(ids)
        off = int(cursor or 0)
        page = ids[off:off + page_size]
        rows = list(ThreadPoolExecutor(max_workers=8).map(one, page)) if page else []
        return {'total': total, 'count': len(rows), 'rows': rows,
                'next_key': str(off + page_size) if off + page_size < total else ''}

    groups = [(st, _all_ids(s, st)) for st in ALL_STATUS]
    total = sum(len(v) for _, v in groups)
    rows, meta = [], {}
    per = max(4, page_size // len(ALL_STATUS))
    for st, ids in groups:
        for pid in ids[:per]:
            meta[pid] = st
            rows.append(pid)
    out = list(ThreadPoolExecutor(max_workers=8).map(one, rows)) if rows else []
    out.sort(key=lambda r: (meta.get(str(r.get('product_id')), 9), str(r.get('title') or '')))
    return {'total': total, 'count': len(out), 'rows': out, 'next_key': '',
            'by_status': {str(k): len(v) for k, v in groups},
            'by_status_text': {str(k): status_text(k) for k, _ in groups}}


def category_flat():
    """把 5041 条类目树摊平成可搜索列表（只保留叶子）。"""
    with _lock:
        if _cur_cache().get('cats') and time.time() - _cur_cache().get('cats_at', 0) < 6 * 3600:
            return _cur_cache()['cats']
    j = get_shop().categories()
    out, by_id = [], {}
    for grp in (j.get('cats') or []):
        for item in (grp.get('cat_and_qua') or []):
            cat = item.get('cat') or {}
            if not cat:
                continue
            by_id[cat['cat_id']] = cat
            if cat.get('leaf'):
                qua = item.get('product_qua_list') or []
                out.append({'cat_id': cat['cat_id'], 'name': cat.get('name'),
                            'f_cat_id': cat.get('f_cat_id'), 'level': cat.get('level'),
                            'qua': [{'qua_id': q.get('qua_id'), 'need_apply': q.get('need_to_apply')} for q in qua]})
    # 补全父级路径名
    for it in out:
        names, cid, guard = [], it['f_cat_id'], 0
        while cid and cid in by_id and guard < 6:
            names.append(by_id[cid].get('name'))
            cid = by_id[cid].get('f_cat_id')
            guard += 1
        it['path'] = ' > '.join(list(reversed(names)) + [it['name']])
    with _lock:
        _cur_cache()['cats'] = out
        _cur_cache()['cats_at'] = time.time()
    return out


def do_price(s, prod, mode, value):
    """改价格：mode=set/add/sub/percent；单位元。返回 (ok, msg)."""
    skus = (_p(prod).get('skus') or [])
    if not skus:
        return False, '无 SKU'
    new = []
    for sk in skus:
        old = int(sk.get('sale_price') or 0)
        if mode == 'set':
            v = int(round(float(value) * 100))
        elif mode == 'add':
            v = old + int(round(float(value) * 100))
        elif mode == 'sub':
            v = old - int(round(float(value) * 100))
        else:
            v = int(round(old * (1 + float(value) / 100.0)))
        if v <= 0:
            return False, 'SKU %s 算出的价格 <= 0' % sk.get('sku_id')
        new.append({'sku_id': sk.get('sku_id'), 'sale_price': v})
    pid = _p(prod).get('product_id') or prod.get('product_id')
    try:
        s.update_product({'product_id': str(pid), 'skus': new})
        return True, '已提交 %d 个 SKU 新价（最小改动模式）' % len(new)
    except WxError as e:
        # 兜底必须递**整本商品对象**（只补全 SKU 字段仍不够：官方 update 要看到类目等必填结构，
        # 与 do_stock 的 6600016 同源）。这里在原始 p 上就地换 sale_price 后整本回传。
        try:
            full = dict(_p(prod))
            before0 = skus[0].get('sale_price')   # 就地覆盖前先留原值，否则日志写「6660→6660」
            for sk, n in zip(skus, new):
                sk['sale_price'] = n['sale_price']
            full['skus'] = skus
            full['product_id'] = str(pid)
            s.update_product(full)
            return True, '已提交 %d 个 SKU 新价（整对象模式，%s→%s）' % (
                len(new), before0, new[0]['sale_price'])
        except WxError as e2:
            return False, '最小改动失败(%s)；整对象也失败(%s)' % (e.errmsg[:80], e2.errmsg[:80])


def _calc_stock(old, mode, value):
    if mode == 'set':
        v = int(float(value))
    elif mode == 'add':
        v = int(old) + int(float(value))
    else:
        v = int(old) - int(float(value))
    return max(v, 0)


def do_stock(s, prod, mode, value):
    """改库存。首选官方快速接口 stock/update（只对已上架商品有效）；
    草稿/审核中的商品它一律报「商品不存在」，这时退回 product/update 整 SKU 提交。"""
    p = _p(prod)
    pid = p.get('product_id')
    skus = p.get('skus') or []
    if not skus:
        return False, '无 SKU'
    try:
        target = int(float(value))
    except Exception:
        return False, '库存值不是数字：%s' % value
    if target < 0:
        return False, '库存不能为负'
    # 先算出新库存（add/sub 基于当前值），dry 与两条通路共用同一份算法
    plan = [{'sku': sk, 'new': _calc_stock(sk.get('stock_num') or 0, mode, value)} for sk in skus]
    diff_type = 1 if mode == 'set' else (2 if mode == 'add' else 3)
    okc, errs, need_fallback = 0, [], False
    for it in plan:
        try:
            s.stock_update(pid, it['sku'].get('sku_id'), diff_type, target)
            okc += 1
        except WxError as e:
            errs.append('%s' % e.errmsg[:60])
            # 「商品不存在」= 该商品还没上架，快速接口不适用，必须走整对象更新
            if '不存在' in e.errmsg or e.errcode in (40009, 47001):
                need_fallback = True
    if okc and not errs:
        return True, '已更新 %d 个 SKU 库存（快速接口）' % okc
    if not need_fallback:
        if okc:
            return True, '成功 %d / 失败 %d：%s' % (okc, len(errs), '; '.join(errs[:2]))
        return False, '; '.join(errs[:3]) or '未做任何修改'
    # 兜底：product/update 提交**完整商品对象**（只换 stock_num）。
    # 只传 {product_id, skus} 会被拒：6600016 类目错误 —— 官方 update 要看到类目等必填结构，
    # 与 run_edit 的整对象兜底同理。
    try:
        full = dict(p)
        # 先留一份改前值：下面会就地覆盖 stock_num，若事后再取，日志会写成「298→298」
        # 这种自欺文案（本轮实测就是这么错了一次），操作员据此无法判断到底写没写对。
        before0 = plan[0]['sku'].get('stock_num')
        for it in plan:
            it['sku']['stock_num'] = it['new']
        full['skus'] = skus
        full['product_id'] = str(pid)
        s.update_product(full)
        return True, '已更新 %d 个 SKU 库存（整对象模式，%s→%s）' % (
            len(skus), before0, plan[0]['new'])
    except WxError as e2:
        return False, '快速接口不可用(%s)；整对象也失败(%s)' % (errs[0][:60] if errs else '?', e2.errmsg[:100])


def do_title(s, prod, mode, value):
    p = _p(prod)
    pid = p.get('product_id')
    old = p.get('title') or ''
    if mode == 'prefix':
        new = value + old
    elif mode == 'suffix':
        new = old + value
    elif mode == 'replace':
        import re
        try:
            a, b = value.split('=>', 1)
            new = old.replace(a, b)
        except Exception:
            return False, '替换格式应为 旧=>新'
    else:
        new = value
    if not new.strip():
        return False, '标题为空'
    if len(new) > 60:
        return False, '标题超过 60 字（当前 %d）' % len(new)
    try:
        s.update_product({'product_id': str(pid), 'title': new})
        return True, '标题已更新：%s' % new[:40]
    except WxError as e:
        return False, e.errmsg[:120]


def do_status(s, prod, mode, value):
    """上/下架。官方 listing 是**异步审核**：errcode=0 只代表"受理"，商品随后进"审核中"，
    status 仍是 0，并且审核期间 delisting/update/delete 全部报 10020047。
    所以提交后必须重新读商品状态再下结论，不能拿接口返回当成功（任务书§一第 8 条）。"""
    pid = (_p(prod)).get('product_id')
    up = mode in ('up', 'listing')
    try:
        if up:
            s.listing(pid)
        else:
            try:
                s.delisting(pid)
            except WxError as e:
                if '审核中' in (e.errmsg or ''):
                    s.cancel_audit(pid)
                    return False, '商品在审核中已撤回（现在可编辑/删除；未下架）'
                raise
    except WxError as e:
        return False, e.errmsg[:120]
    # 读回核对：状态有没有真的变
    time.sleep(1.5)
    try:
        p = _p(s.product(pid))
        st = p.get('status')
        if not up:
            if st == 5:
                return True, '已读回确认：仍在架（status=5），下架可能未生效'
            return True, '已下架并读回确认（status=%s）' % st
        # 上架：受理进审核中
        if st == 5:
            return True, '上架已生效并读回确认（status=5）'
        return True, ('上架请求已受理，商品进入审核（读回 status=%s，官方审核通过后才变 5）；'
                      '审核期间无法编辑/删除，如需回退用「撤回审核」' % st)
    except WxError as e:
        return True, '已提交（读回失败：%s）' % e.errmsg[:60]


OPS = {'price': do_price, 'stock': do_stock, 'title': do_title, 'status': do_status}


def run_batch(ids, op, mode, value, dry_run=False):
    s = get_shop()
    fn = OPS.get(op)
    if not fn:
        raise WxError(-3, '未知操作 %s' % op, 'batch')
    results = []

    def one(pid):
        t0 = time.time()
        try:
            prod = s.product(pid)
            if dry_run:
                # 预览必须与真实执行同一套入参校验，否则「预览通过 → 执行被拒」，
                # 预览就失去意义（本轮实测：add=-5 预览放行、执行报「库存不能为负」）。
                if op in ('price', 'stock'):
                    try:
                        fv = float(value)
                    except Exception:
                        return {'id': pid, 'ok': False, 'msg': '数值不是数字：%s' % value}
                    # 库存是整数（int 截断会吞小数），价格是元（必须留小数，66.60→66 会少 6 毛）
                    if op == 'stock':
                        if int(fv) != fv:
                            return {'id': pid, 'ok': False, 'msg': '库存必须是整数：%s' % value}
                        if fv < 0:
                            return {'id': pid, 'ok': False, 'msg': '库存不能为负（%s=%s）' % (mode, int(fv))}
                    if op == 'price' and fv < 0:
                        return {'id': pid, 'ok': False, 'msg': '价格不能为负（%s=%s）' % (mode, fv)}
                if op == 'price':
                    sk = (_p(prod).get('skus') or [])
                    cur = [x.get('sale_price') or 0 for x in sk]
                    nxt = []
                    for c in cur:
                        if mode == 'set':
                            v = int(round(fv * 100))
                        elif mode == 'add':
                            v = c + int(round(fv * 100))
                        elif mode == 'sub':
                            v = c - int(round(fv * 100))
                        else:
                            v = int(round(c * (1 + fv / 100.0)))
                        nxt.append(v)
                    return {'id': pid, 'ok': True,
                            'msg': '预览：%d 个 SKU，价格 %s → %s 元（%s=%s）｜未上架商品走整对象更新'
                            % (len(sk), [round(c / 100.0, 2) for c in cur[:3]],
                               [round(v / 100.0, 2) for v in nxt[:3]], mode, fv)}
                if op == 'stock':
                    sk = (_p(prod).get('skus') or [])
                    cur = [x.get('stock_num') or 0 for x in sk]
                    nxt = [_calc_stock(c, mode, value) for c in cur]
                    return {'id': pid, 'ok': True,
                            'msg': '预览：%d 个 SKU，库存 %s → %s（合计 %d → %d）｜未上架商品走整对象更新'
                            % (len(sk), cur[:3], nxt[:3], sum(cur), sum(nxt))}
                return {'id': pid, 'ok': True, 'msg': '预览：将对 %s 执行 %s=%s' % (pid, op, value)}
            ok, msg = fn(s, prod, mode, value)
            return {'id': pid, 'ok': ok, 'msg': msg, 'ms': int((time.time() - t0) * 1000)}
        except WxError as e:
            return {'id': pid, 'ok': False, 'msg': '[%s] %s' % (e.errcode, e.errmsg)}
        except Exception as e:
            return {'id': pid, 'ok': False, 'msg': str(e)}

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(one, ids))
    okc = sum(1 for r in results if r['ok'])
    rec = {'op': op, 'mode': mode, 'value': value, 'dry_run': dry_run, 'total': len(ids),
           'ok': okc, 'fail': len(ids) - okc, 'items': results}
    log_task(rec)
    return rec


# ---------------------------------------------------- 字段补丁 / 诊断 / 云库 / 导出
def run_patch(ids, patch, dry_run=False):
    """只允许白名单字段的最小改动（title / sub_title / weight / spu_code）。"""
    bad = [k for k in patch if k not in PATCH_FIELDS]
    if bad:
        raise WxError(-4, '不支持的字段：%s（可用：%s）' % (','.join(bad), ','.join(sorted(PATCH_FIELDS))), 'patch')
    s = get_shop()
    items = []

    def one(pid):
        try:
            if dry_run:
                p = _p(s.product(pid))
                old = {k: p.get(k) for k in patch}
                return {'id': pid, 'ok': True, 'msg': '预览：%s → %s' % (json.dumps(old, ensure_ascii=False),
                                                                     json.dumps(patch, ensure_ascii=False))}
            payload = {'product_id': str(pid)}
            payload.update(patch)
            s.update_product(payload)
            return {'id': pid, 'ok': True, 'msg': '已更新：%s' % json.dumps(patch, ensure_ascii=False)}
        except WxError as e:
            return {'id': pid, 'ok': False, 'msg': '[%s] %s' % (e.errcode, e.errmsg)}
        except Exception as e:
            return {'id': pid, 'ok': False, 'msg': str(e)}

    with ThreadPoolExecutor(max_workers=4) as ex:
        items = list(ex.map(one, ids))
    okc = sum(1 for i in items if i['ok'])
    rec = {'op': 'patch', 'mode': ','.join(patch), 'value': '', 'dry_run': dry_run,
           'total': len(ids), 'ok': okc, 'fail': len(ids) - okc, 'items': items}
    log_task(rec)
    return rec


def diagnose_all():
    """本地规则诊断：能不能卖的硬条件。"""
    s = get_shop()
    ids = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for part in ex.map(lambda st: _all_ids(s, st), (0, 5, 11)):
            ids += part
    issues, groups = [], {}

    def add(pid, title, issue, fix):
        issues.append({'product_id': pid, 'title': title, 'issue': issue, 'fix': fix})
        groups[issue] = groups.get(issue, 0) + 1

    def one(pid):
        try:
            p = _p(s.product(pid))
        except WxError as e:
            add(pid, '(读取失败)', '读取失败', e.errmsg[:60]); return
        title = (p.get('title') or '').strip()
        skus = p.get('skus') or []
        imgs = p.get('head_imgs') or []
        cats = p.get('cats') or []
        if not title:
            add(pid, '(无标题)', '无标题', '补标题后才能上架')
        elif len(title) > 60:
            add(pid, title, '标题超 60 字', '缩短到 60 字内')
        if not imgs:
            add(pid, title, '无主图', '上传至少一张主图')
        if not skus:
            add(pid, title, '无 SKU', '添加销售规格与价格库存')
        if not cats:
            add(pid, title, '无类目', '选到末级类目')
        if skus:
            if all((k.get('sale_price') or 0) <= 0 for k in skus):
                add(pid, title, '价格 ≤ 0', '设置正常售价')
            if all((k.get('stock_num') or 0) <= 0 for k in skus):
                add(pid, title, '库存为 0', '补库存，否则上架即缺货')

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(one, ids))
    return {'ok': True, 'scanned': len(ids), 'issues': issues,
            'groups': {k: [0] * v for k, v in groups.items()}}


def yunku_save(ids):
    s = get_shop()
    items = []

    def one(pid):
        try:
            return {'product_id': str(pid), 'data': s.product(pid)}
        except WxError as e:
            return {'product_id': str(pid), 'error': e.errmsg}

    with ThreadPoolExecutor(max_workers=4) as ex:
        items = list(ex.map(one, ids))
    name = 'bk-%s.json' % time.strftime('%Y%m%d-%H%M%S')
    json.dump({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'count': len(items), 'items': items},
              open(os.path.join(YUNKU, name), 'w', encoding='utf-8'), ensure_ascii=False)
    return {'ok': True, 'file': name, 'saved': len(items)}


def yunku_list():
    rows = []
    for f in sorted(os.listdir(YUNKU), reverse=True):
        if not f.endswith('.json'):
            continue
        fp = os.path.join(YUNKU, f)
        try:
            j = json.load(open(fp, encoding='utf-8'))
            rows.append({'file': f, 'count': j.get('count', len(j.get('items') or [])), 'ts': j.get('ts', '')})
        except Exception:
            rows.append({'file': f, 'count': 0, 'ts': ''})
    return {'ok': True, 'rows': rows[:200]}


def export_csv(ids, status=None):
    """导出商品清单 CSV（UTF-8 BOM，Excel 直接打开）。"""
    s = get_shop()
    if ids:
        page = [slim_product(s.product(i)) for i in ids]
    else:
        page = products_page(status=status, page_size=120).get('rows') or []
    name = 'exp-%s.csv' % time.strftime('%Y%m%d-%H%M%S')
    fp = os.path.join(EXPORTS, name)
    cols = ['product_id', 'title', 'status_text', 'price_min', 'price_max', 'stock_total',
            'sku_count', 'cat', 'head_img', 'updated']
    with open(fp, 'w', encoding='utf-8-sig', newline='') as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(['商品ID', '标题', '状态', '最低价', '最高价', '总库存', 'SKU数', '类目', '主图', '更新时间'])
        for r in page:
            w.writerow([r.get('product_id'), r.get('title'), r.get('status_text'), r.get('price_min'),
                        r.get('price_max'), r.get('stock_total'), r.get('sku_count'),
                        ' / '.join(str(c.get('cat_id') if isinstance(c, dict) else c) for c in (r.get('cat') or [])),
                        r.get('head_img'), r.get('updated')])
    return {'ok': True, 'file': name, 'rows': len(page)}


def export_records():
    rows = []
    for f in sorted(os.listdir(EXPORTS), reverse=True)[:200]:
        if not f.endswith('.csv'):
            continue
        fp = os.path.join(EXPORTS, f)
        try:
            n = sum(1 for _ in open(fp, encoding='utf-8-sig')) - 1
        except Exception:
            n = 0
        rows.append({'file': f, 'rows': max(n, 0), 'ts': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(fp)))})
    return {'ok': True, 'rows': rows}


def import_preview(text, name=''):
    """只解析、不写库：认列、数行。"""
    import csv as _csv
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        return {'ok': False, 'error': '文件是空的'}
    sample = text[:4000]
    delim = '\t' if sample.count('\t') > sample.count(',') else ','
    rd = list(_csv.reader(text.split('\n'), delimiter=delim))
    rd = [r for r in rd if any((c or '').strip() for c in r)]
    if not rd:
        return {'ok': False, 'error': '没读到有效行'}
    headers = [c.strip() for c in rd[0]]
    rows = rd[1:]
    return {'ok': True, 'name': name, 'delim': delim, 'headers': headers,
            'rows': rows[:200], 'total': len(rows)}


# ---------------------------------------------------------------- 导入建品（Excel/CSV → 草稿品）
IMG_CACHE_F = os.path.join(DATA, 'imgcache.json')
IMPORT_DEFAULT_CAT = '549074'   # 默认末级类目：家用茶台/茶桌（A/B 店均已授权）
# 建品必填项统一由 _complete_product_payload 用「当前店自己」的数据补齐，不再抄模板商品。


def _imgcache():
    try:
        return json.load(open(IMG_CACHE_F, encoding='utf-8'))
    except Exception:
        return {}


def _imgcache_put(url, wx):
    c = _imgcache()
    c[url] = wx
    tmp = IMG_CACHE_F + '.tmp'
    json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, IMG_CACHE_F)


def to_fen(v):
    """元→分。容错：'549.00~959.00' 取低值；'￥12.5元'、空、非数 → None。
    （价格写成区间是上游导出 130 条失败的元凶，这里强制拆掉。）"""
    import re as _re
    s = str(v or '').strip()
    if not s:
        return None
    s = s.split('~')[0].split('～')[0]
    s = _re.sub(r'[^\d.]', '', s)
    if not s:
        return None
    try:
        f = round(float(s) * 100)
        return f if f > 0 else None
    except Exception:
        return None


def _to_int(v, d=None):
    import re as _re
    s = _re.sub(r'[^\d]', '', str(v or ''))
    return int(s) if s else d


def _cat_path_by_id(cat_id):
    """根据末级类目 id 反推完整 3 级链路（[{cat_id,level},…]），从叶子往上拼。
    用 category_flat() 里已缓存的 by_id 关系；找不到就退回单级（微信会再报 6600036，
    但至少不崩）。"""
    try:
        from wxapi import WxShop
    except Exception:
        pass
    j = get_shop().categories()
    by_id = {}
    for grp in (j.get('cats') or []):
        for item in (grp.get('cat_and_qua') or []):
            cat = item.get('cat') or {}
            if cat.get('cat_id'):
                by_id[str(cat['cat_id'])] = cat
    cid = str(cat_id)
    if cid not in by_id:
        return [{'cat_id': cid, 'level': 3}]
    chain = []
    guard = 0
    while cid and cid in by_id and guard < 6:
        cat = by_id[cid]
        chain.append({'cat_id': cid, 'level': int(cat.get('level') or 3)})
        cid = str(cat.get('f_cat_id') or '')
        guard += 1
    chain.reverse()
    return chain


def _complete_product_payload(s, payload):
    """建品前自动补齐微信必填字段（一处修，所有建品链路受益）。

    微信 /channels/ec/product/add 对「茶台/家具」类目必填：
      · 3 级类目 cats（6600036）
      · 必填属性：产地/是否上门安装/主体材质/是否上门测量（6600045）
      · 头图 ≥3 张（6600067）
      · 运费模板 + 商品重量（6600112，按重量计费时重量不能为 0）
      · 售后地址（6600077）
    前端「新建上货」只传 1 张图 + 1 级类目 + 无模板/地址/重量/属性，必然全线失败。
    这里把缺的都补上；payload 已带的字段不动（尊重显式传入）。
    """
    p = dict(payload or {})

    # 1) 3 级类目：只有 1 个元素（或都是末级 id）时，补全父级链路
    cats = p.get('cats') or p.get('cats_v2') or []
    if cats:
        leaf = cats[-1].get('cat_id') if isinstance(cats[-1], dict) else str(cats[-1])
        if len(cats) < 3:
            p['cats'] = _cat_path_by_id(leaf)
    else:
        # 完全没传类目：拿不到，放弃补（交给微信报错，别瞎猜）
        return p

    # 2) 必填属性：从 category_detail 拿 is_required 项，缺的用合法值第一个兜底
    try:
        leaf_id = str(p['cats'][-1].get('cat_id') or '')
        det = s.category_detail(leaf_id)
        attr = (det.get('attr') or {})
        req_attrs = [(a.get('name'), (a.get('value') or '').split(';')[0] if a.get('value') else '')
                     for a in (attr.get('product_attr_list') or []) if a.get('is_required')]
        have = {(a.get('attr_key') or ''): a for a in (p.get('attrs') or [])}
        for name, first_val in req_attrs:
            if name and name not in have:
                # 主体材质之类没有合法值列表时给个安全默认；有就取第一个
                p.setdefault('attrs', []).append({'attr_key': name, 'attr_value': first_val or '否'})
    except Exception:
        pass  # 属性补全失败不阻断，让微信报具体缺失项

    # 3) 头图 ≥3 张：不足就复制第一张补到 3 张
    heads = list(p.get('head_imgs') or [])
    if heads and len(heads) < 3:
        heads += [heads[0]] * (3 - len(heads))
        p['head_imgs'] = heads

    # 4) 运费模板：缺了取本店第一个模板；同时补 weight（按重量计费时必填，6600112）
    if not p.get('express_info') or not isinstance(p.get('express_info'), dict) or not p['express_info'].get('template_id'):
        try:
            ft = s.freight_templates()
            tids = ft.get('template_id_list') or []
            if tids:
                p['express_info'] = dict(p.get('express_info') or {})
                p['express_info']['template_id'] = str(tids[0])
        except Exception:
            pass
    # weight 在 express_info 里（单位克），按重量计费时不能为 0
    if not p.get('express_info') or not isinstance(p.get('express_info'), dict):
        p['express_info'] = {}
    if not p['express_info'].get('weight'):
        p['express_info']['weight'] = 1000

    # 5) 售后地址：缺了取本店第一个地址
    if not p.get('after_sale_info') or not (p['after_sale_info'].get('after_sale_address_id') if isinstance(p.get('after_sale_info'), dict) else True):
        try:
            al = s.call('/channels/ec/merchant/address/list', {'limit': 50, 'offset': 0})
            aids = al.get('address_id_list') or []
            if aids:
                p['after_sale_info'] = dict(p.get('after_sale_info') or {})
                p['after_sale_info']['after_sale_address_id'] = str(aids[0])
        except Exception:
            pass

    # 6) 商品重量已并入第 4 步 express_info.weight（按重量计费必填，6600112）。
    #    weight 是 express_info 对象内的字段，不是商品顶层、也不是 deliver_method。
    #    （deliver_method 是 number：0=快递/1=无需快递/3=可选发货账号，与重量无关。）

    # 7) 7 天无理由：家具/茶台类目强制 seven_day_return=1（6600079）
    if not p.get('extra_service'):
        p['extra_service'] = {}
    if not p['extra_service'].get('seven_day_return'):
        p['extra_service']['seven_day_return'] = 1

    # 8) 价格系数：shops.json 顶层 price_factor（默认 1.25），统一乘到每个 SKU 的 sale_price。
    #    sale_price 单位是分（前端已 *100），这里直接乘系数再四舍五入。
    try:
        d = _load_shops()
        factor = float(d.get('price_factor') or 1.25)
        if factor and factor != 1:
            for sk in (p.get('skus') or []):
                sp = sk.get('sale_price')
                if isinstance(sp, (int, float)) and sp:
                    sk['sale_price'] = int(round(sp * factor))
    except Exception:
        pass

    return p


def _norm_headers(hs):
    """把 19 列模板表头映射到内部字段名：真实表头带序号后缀（商品轮播图1/商品详情图2…），
    按关键词认列，认不全也不至于整行读不到。"""
    m = {}
    for i, h in enumerate(hs or []):
        k = (h or '').replace('\ufeff', '').replace(' ', '').rstrip('0123456789')
        g = lambda key: m.setdefault(key, i)
        if '商品ID' in h or '微信商品ID' in h: g('商品ID')
        elif k in ('商品名称', '商品标题', '名称', '标题'): g('商品名称')
        elif k == '品牌': g('品牌')
        elif k in ('分类', '商品分类'): g('分类')
        elif k == '库存': g('库存')
        elif k in ('团购价', '团购价格'): g('团购价')
        elif k in ('单买价', '单买价格'): g('单买价')
        elif k in ('商品主图', '主图'): g('主图')
        elif k.startswith('商品轮播图') or k.startswith('轮播图'): g('轮播图')
        elif k.startswith('商品详情图') or k.startswith('详情图'): g('详情图')
        elif k in ('商品描述', '描述'): g('描述')
        elif k in ('商品视频', '视频'): g('视频')
        elif k.startswith('商品属性') or k == '属性': g('属性')
        elif k == 'SKUID': g('SKUID')
        elif k in ('SKU名称', 'SKU规格', '规格'): g('SKU名称')
        elif k == 'SKU库存': g('SKU库存')
        elif k in ('SKU团购价', 'SKU团购价格'): g('SKU团购价')
        elif k in ('SKU单买价', 'SKU单买价格'): g('SKU单买价')
        elif k.startswith('SKU预览图') or k.startswith('SKU图'): g('SKU预览图')
        elif k in ('货号', '商品货号', '商家编码'): g('货号')
    return m


def import_normalize(text, name=''):
    """把 19 列模板文本归一成商品草稿列表：按 商品ID/名称 分行归组，SKU 聚成数组。
    只算不发网络：返回每行缺什么（blocks），够不够建。"""
    pr = import_preview(text, name)
    if not pr.get('ok'):
        return pr
    idx = _norm_headers(pr['headers'])
    rows = pr['rows']

    def col(row, *cands):
        for c in cands:
            i = idx.get(c)
            if i is not None and i < len(row):
                v = (row[i] or '').strip()
                if v:
                    return v
        return ''

    groups, order = {}, []
    for row in pr['rows']:
        pid = col(row, '商品ID', '商品id', '微信商品ID')
        title = col(row, '商品名称', '商品标题', '名称', '标题')
        key = pid or title or col(row, '货号')
        if not key:
            continue
        if key not in groups:
            groups[key] = {'product_id': pid, 'title': title, 'rows': []}
            order.append(key)
        groups[key]['rows'].append(row)

    def _sp(v):
        """SKU 列可能用 | 分隔成数组（一行一商品），也可能一行一 SKU。"""
        v = str(v or '').strip()
        if not v:
            return []
        return [x.strip() for x in v.split('|')] if '|' in v else [v]

    def _pk(lst, k):
        if not lst:
            return ''
        if k < len(lst):
            return lst[k]
        return lst[-1] if len(lst) == 1 else ''

    items = []
    for key in order:
        g = groups[key]
        rows = g['rows']
        first = rows[0]
        head0 = col(first, '主图', '商品主图')
        carousel = col(first, '轮播图', '商品轮播图')
        heads = ([head0] if head0 else []) + [u for u in _urls(carousel) if u != head0]
        dimgs = _urls(col(first, '详情图', '商品详情图'))
        skus = []
        for r in rows:
            names = _sp(col(r, 'SKU名称', '规格', 'SKU规格'))
            pids = _sp(col(r, 'SKUID'))
            stocks = _sp(col(r, 'SKU库存'))
            tgs = _sp(col(r, 'SKU团购价', 'SKU团购价格'))
            djs = _sp(col(r, 'SKU单买价', 'SKU单买价格'))
            sims = _sp(col(r, 'SKU预览图', 'SKU图'))
            n = max(len(names), len(pids), len(stocks), len(tgs), len(djs), 1)
            for k in range(n):
                price = (to_fen(_pk(tgs, k)) or to_fen(_pk(djs, k))
                         or to_fen(col(first, '团购价', '团购价格'))
                         or to_fen(col(first, '单买价', '单买价格')))
                stock = _to_int(_pk(stocks, k), _to_int(col(first, '库存'), 0)) or 0
                skus.append({'name': _pk(names, k), 'price': price, 'stock': stock,
                             'img': _pk(sims, k), 'out_sku_id': _pk(pids, k)})
        if not skus or all(not s['price'] for s in skus):
            skus = [{'name': '', 'price': to_fen(col(first, '团购价', '团购价格')) or to_fen(col(first, '单买价', '单买价格')),
                     'stock': _to_int(col(first, '库存'), 0) or 0, 'img': '', 'out_sku_id': ''}]
        blocks = []
        if not g['title']:
            blocks.append('缺商品名称')
        if not heads:
            blocks.append('缺主图')
        nprice = sum(1 for s in skus if s['price'])
        if nprice == 0:
            blocks.append('缺价格（所有 SKU 都没有可解析的数字价格）')
        if len(skus) > 1 and sum(1 for s in skus if s['name']) < len(skus):
            blanks = len(skus) - sum(1 for s in skus if s['name'])
            blocks.append('%d/%d 个 SKU 名称为空（会报空指针，建不了）' % (blanks, len(skus)))
        need_up = sum(1 for u in heads + dimgs + [s['img'] for s in skus if s['img']]
                      if u and 'mmecimage.cn' not in u)
        items.append({'key': key, 'title': g['title'], 'sku_count': len(skus),
                      'ok_skus': nprice, 'head_imgs': heads, 'desc_imgs': dimgs,
                      'desc': col(first, '描述', '商品描述'), 'video': col(first, '视频', '商品视频'),
                      'attrs': col(first, '属性', '商品属性'), 'brand': col(first, '品牌'),
                      'skus': skus, 'need_upload': need_up, 'blocks': blocks,
                      'ready': not blocks})
    return {'ok': True, 'name': name, 'total': len(items),
            'ready': sum(1 for i in items if i['ready']),
            'blocked': sum(1 for i in items if not i['ready']),
            'items': items[:100]}


def import_fetch_images(urls):
    """外部图片 URL → 微信永久链接（mmecimage.cn）。带 md5 缓存，同图只传一次。"""
    import hashlib as _h
    s = get_shop()
    cache = _imgcache()
    out, errs = [], []
    for u in urls:
        if not u:
            continue
        if 'mmecimage.cn' in u:
            out.append(u)
            continue
        if u in cache:
            out.append(cache[u])
            continue
        try:
            req = urllib.request.Request(u, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://item.taobao.com/'})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            if len(data) < 100 or len(data) > 9 * 1024 * 1024:
                raise ValueError('图片太大或为空(%dB)' % len(data))
            ext = '.png' if data[:4] == b'\x89PNG' else '.jpg'
            wx = s.upload_image(_h.md5(data).hexdigest() + ext, data)
            if not wx:
                raise ValueError('上传未返回链接')
            _imgcache_put(u, wx)
            out.append(wx)
        except Exception as e:
            errs.append('%s → %s' % (u[:60], str(e)[:80]))
    return out, errs


def import_create(norm, cat_id, limit=5, dry_run=True):
    """按归一结果建草稿品（2026-11-08 改）。

    ★ 不再抄「模板商品」：那个写死的 10001792323089 是 A 店的茶桌，
      B 店根本没有这件商品，导致任何品类都在第一步被「模板没有类目」全量拒绝
      （与类目授权无关，主人 11-08 现场纠正）。现在改为：
        · 类目由前端传入的末级 cat_id 决定，A/B 各自的类目链路各自反推；
        · 必填字段（运费模板/售后地址/必填属性/头图≥3/7天无理由/价格系数 1.25）
          统一由 _complete_product_payload 用「当前店自己」的数据补齐。
    """
    if not norm.get('ok'):
        return norm
    s = get_shop()
    cid = str(cat_id or '').strip()
    if not cid:
        return {'ok': False, 'error': '缺末级类目 cat_id：请在导入页填类目ID（如 549074 家用茶台/茶桌）'}
    base_cats = _cat_path_by_id(cid)   # 用当前店的类目树反推 3 级链路
    ready = [i for i in norm['items'] if i['ready']]
    todo = ready[:max(1, min(int(limit or 1), 20))]
    items = []
    for it in todo:
        try:
            if dry_run:
                items.append({'key': it['key'], 'ok': True,
                              'msg': '试算：%d 个 SKU，主图 %d 张，详情图 %d 张（外链待传共 %d）｜类目 %s（本店字段自动补齐，价格×1.25）'
                              % (it['sku_count'], len(it['head_imgs']),
                                 len(it['desc_imgs']), it['need_upload'],
                                 '>'.join(c.get('cat_id', '') for c in base_cats))})
                continue
            heads, e1 = import_fetch_images(it['head_imgs'])
            dims, e2 = import_fetch_images(it['desc_imgs'])
            if e1 or e2:
                items.append({'key': it['key'], 'ok': False,
                              'msg': '图片上传失败：%s' % '；'.join((e1 + e2)[:2])})
                continue
            skus = []
            for k, sr in enumerate(it['skus'][:50]):
                simg = ''
                if sr.get('img'):
                    got, ee = import_fetch_images([sr['img']])
                    if ee or not got:
                        items.append({'key': it['key'], 'ok': False,
                                      'msg': 'SKU 图失败：%s' % (ee[0] if ee else '空')})
                        simg = None
                        break
                    simg = got[0]
                sa = []
                if sr.get('name'):
                    sa.append({'attr_key': '销售属性1', 'attr_value': sr['name']})
                skus.append({'sku_id': '0', 'out_sku_id': '', 'thumb_img': simg or '',
                             'sale_price': int(sr['price']), 'stock_num': int(sr['stock'] or 0),
                             'sku_code': '', 'sku_attrs': sa or [{'attr_key': '销售属性1',
                                                                   'attr_value': '默认规格%d' % (k + 1)}],
                             'sku_deliver_info': {'stock_type': 0}})
            if simg is None:
                continue
            payload = {'cats': [dict(c) for c in base_cats]}
            # CSV 属性列 "产地:中国大陆|主体材质:橡胶木" → [{attr_key,attr_value}]
            # （6600045：类目必填参数缺失；不传属性必被拒）
            attrs = []
            for kv in str(it.get('attrs') or '').replace('\n', '|').split('|'):
                if ':' in kv:
                    k, v = kv.split(':', 1)
                elif '：' in kv:
                    k, v = kv.split('：', 1)
                else:
                    continue
                k, v = k.strip(), v.strip()
                if k and v and not any(a['attr_key'] == k for a in attrs):
                    attrs.append({'attr_key': k, 'attr_value': v})
            payload.update({'title': it['title'][:60], 'sub_title': '',
                            'attrs': attrs,
                            'head_imgs': heads[:9], 'desc_info': {'imgs': dims[:20], 'desc': it['desc'] or ''},
                            'skus': skus})
            # ★ 用「当前店自己」的数据补齐所有微信必填项：运费模板 / 售后地址 /
            #   必填属性 / 头图≥3 / 7天无理由 / 价格系数(shops.json price_factor=1.25)。
            #   一处修，A/B 店各自生效，不再依赖任何模板商品。
            payload = _complete_product_payload(s, payload)
            payload.pop('product_id', None)
            payload.pop('status', None)
            payload.pop('edit_status', None)
            j = s.add_product(payload)
            skip = ''
            if it.get('attrs'):
                skip += '｜属性已带 %d 项' % len(attrs)
            if it.get('video'):
                skip += '｜视频未接（源为第三方临时链）'
            new_id = j.get('product_id') or (j.get('data') or {}).get('product_id') or '（已建，见草稿箱）'
            items.append({'key': it['key'], 'ok': True,
                          'msg': '已建草稿 product_id=%s｜%d SKU｜%d 张图已转存%s'
                          % (new_id, len(skus), len(heads) + len(dims), skip)})
        except WxError as e:
            items.append({'key': it['key'], 'ok': False, 'msg': '[%s] %s' % (e.errcode, e.errmsg)})
        except Exception as e:
            items.append({'key': it['key'], 'ok': False, 'msg': str(e)[:120]})
    okc = sum(1 for i in items if i['ok'])
    rec = {'op': 'import_create' + ('_dry' if dry_run else ''), 'mode': 'cat:' + cid,
           'value': norm.get('name', ''), 'dry_run': dry_run,
           'total': len(items), 'ok': okc, 'fail': len(items) - okc, 'items': items}
    log_task(rec)
    return rec



# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = 'WxShopDesk/1.0'

    def log_message(self, fmt, *a):
        sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % a))

    # -- helpers
    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, obj)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def _jsonbody(self):
        try:
            return json.loads(self._body() or b'{}')
        except Exception:
            return {}

    # -- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        set_shop_id(self.headers.get('X-Shop-Id'))
        try:
            if p == '/llms.txt':
                return self._send(200, LLMS, 'text/plain; charset=utf-8')
            if p.startswith('/api/'):
                return self._api_get(p, q)
            if p.startswith('/data/exports/'):
                fp = os.path.join(EXPORTS, os.path.basename(p))
                if os.path.isfile(fp):
                    return self._send(200, open(fp, 'rb').read(), 'text/csv; charset=utf-8')
                return self._send(404, 'not found', 'text/plain; charset=utf-8')
            # 静态
            rel = 'index.html' if p in ('/', '') else p.lstrip('/')
            fp = os.path.normpath(os.path.join(WEB, rel))
            if not fp.startswith(WEB) or not os.path.isfile(fp):
                return self._send(404, 'not found', 'text/plain; charset=utf-8')
            mt = mimetypes.guess_type(fp)[0] or 'application/octet-stream'
            if mt.startswith('text/') or mt in ('application/javascript', 'application/json'):
                mt += '; charset=utf-8'
            return self._send(200, open(fp, 'rb').read(), mt)
        except WxError as e:
            return self._json({'ok': False, 'errcode': e.errcode, 'error': e.errmsg, 'path': e.path}, 200)
        except Exception as e:
            traceback.print_exc()
            return self._json({'ok': False, 'error': str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        set_shop_id(self.headers.get('X-Shop-Id'))
        try:
            if p == '/api/upload':
                return self._upload()
            return self._api_post(p, self._jsonbody())
        except WxError as e:
            return self._json({'ok': False, 'errcode': e.errcode, 'error': e.errmsg, 'path': e.path}, 200)
        except Exception as e:
            traceback.print_exc()
            return self._json({'ok': False, 'error': str(e)}, 500)

    # -- API
    def _api_get(self, p, q):
        g = lambda k, d='': (q.get(k) or [d])[0]
        if p.startswith('/api/xf9/'):              # 9.0 AI 运营中心（独立模块 xf9.py）
            return self._json(XF9.api_get(p, q))
        if p.startswith('/api/dd/'):                # 7.0 抖店后台管理（独立模块 dd.py）
            return self._json(DD.api_get(p, q))
        if p.startswith('/api/publish/'):          # 6.0 上架平台（独立模块 publish.py）
            return self._json(PB.api_get(p, q))
        if p == '/api/health':
            cfg = load_cfg()
            return self._json({'ok': True, 'appid': (cfg.get('appid') or '')[:6] + '****',
                               'ts': time.strftime('%Y-%m-%d %H:%M:%S')})
        if p == '/api/shop':
            j = get_shop().shop_info()
            return self._json({'ok': True, 'data': j})
        if p == '/api/products':
            ck = 'plist:%s:%s:%s' % (g('status'), g('page_size', '20'), g('cursor'))
            with _lock:
                hit = _cur_cache().get(ck)
                if hit and time.time() - hit[0] < 120:
                    return self._json({'ok': True, 'data': hit[1], 'cached': True})
            data = products_page(
                status=g('status') if g('status') != '' else None,
                page_size=int(g('page_size', '20')), cursor=g('cursor'))
            with _lock:
                _cur_cache()[ck] = (time.time(), data)
            return self._json({'ok': True, 'data': data})
        if p == '/api/product':
            j = get_shop().product(g('id'))
            return self._json({'ok': True, 'data': j, 'slim': slim_product(j)})
        if p == '/api/categories':
            rows = category_flat()
            kw = g('q').strip()
            if kw:
                rows = [r for r in rows if kw in r['path'] or kw == r['cat_id']]
            lim = int(g('limit', '200'))
            return self._json({'ok': True, 'total': len(rows), 'rows': rows[:lim]})
        if p == '/api/category':
            return self._json({'ok': True, 'data': get_shop().category_detail(g('id'))})
        if p == '/api/freight':
            return self._json({'ok': True, 'data': get_shop().freight_templates()})
        if p == '/api/coupons':
            return self._json({'ok': True, 'data': get_shop().coupons()})
        if p == '/api/orders':
            now = int(time.time())
            # 2026-11-01 实测：官方窗口最多 7 天（>7 天报 40097），默认改成 7 天
            st = int(g('start') or now - 7 * 86400)
            et = int(g('end') or now)
            if et - st > 7 * 86400:
                st = et - 7 * 86400
            return self._json({'ok': True, 'data': get_shop().orders(st, et),
                               'range': {'start': st, 'end': et}})
        if p == '/api/tasks':
            return self._json({'ok': True, 'rows': read_tasks(int(g('limit', '100')))})
        if p == '/api/editSpec':
            return self._json({'ok': True, 'spec': {k: [{'k': f[0], 't': f[1], 'label': f[2], 'opts': f[3]} for f in v] for k, v in EDIT_SPEC.items()}})
        if p == '/api/overview':
            return self._json(overview_all(force=(g('force') == '1')))
        if p == '/api/union3':
            # 三店去重总览：默认读缓存（秒开）；force=1 触发后台重算（1-2 分钟）
            d = U3.cached()
            if g('force') == '1':
                return self._json(U3.refresh_async())
            d['refresh'] = U3.status()
            return self._json(d)
        if p == '/api/shops':
            d = _load_shops()
            cur = _cur_shop(d)
            shops = []
            for x in (d.get('shops') or []):
                aid = x.get('appid') or ''
                shops.append({'appid': aid,
                              'name': x.get('name') or '',
                              'mask': aid[:6] + '****',
                              'enabled': x.get('enabled', True) is not False,
                              'kind': x.get('kind') or 'wx_api',
                              'profileId': x.get('profileId') or '',
                              'space': x.get('space') or '',
                              'has_secret': bool(x.get('secret'))})
            # 抖店作为第三家店：没有免签 AppID 接口，上货走 7.0 的 16 列上架包通道。
            # 只在「上架目标勾选」里出现，顶部切店下拉按 kind 过滤掉它（它没有微信空间）。
            shops.append({'appid': 'dd_lzl', 'name': '抖店·LZL乐群云家具',
                          'mask': 'dd_lzl', 'enabled': True, 'kind': 'doudian',
                          'profileId': '', 'space': '', 'has_secret': False})
            return self._json({'ok': True, 'current': d.get('current') or '',
                               'cur_name': cur.get('name') or cur.get('appid') or '',
                               'count': len(shops), 'shops': shops})
        if p == '/api/sku/audit':
            """全店扫一遍 SKU 有无图（约 45 秒）。mode=scan 触发后台重扫，mode=get 读上次结果。"""
            if g('mode') == 'scan':
                return self._json(start_sku_scan(current_shop_id()))
            return self._json({'ok': True, 'data': sku_health_cached(), 'running': bool(_scan_state['on'])})
        if p == '/api/uploads':
            rows = []
            if os.path.exists(UPLOADS):
                for line in open(UPLOADS, encoding='utf-8').read().strip().split(chr(10))[-int(g('limit', '100')):]:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
            return self._json({'ok': True, 'rows': rows[::-1]})
        if p == '/api/photo/status':
            return self._json(PH.status())
        if p == '/api/photo/roots':
            return self._json({'ok': True, 'roots': PH.roots()})
        if p == '/api/photo/list':
            return self._json(PH.list_images(int(g('root', '0')), g('dir'), int(g('page', '0')), int(g('size', '60'))))
        if p == '/api/photo/file':
            rid = g('id')
            raw = PH.read_file(int(g('root', '0')), rid, int(g('w', '0')))
            mt = mimetypes.guess_type(rid)[0] or 'image/jpeg'
            return self._send(200, raw, mt)
        if p == '/api/photo/out':
            fp = os.path.join(PH.OUT_DIR, os.path.basename(g('f')))
            if os.path.isfile(fp):
                return self._send(200, open(fp, 'rb').read(), 'image/jpeg')
            return self._send(404, b'', 'image/jpeg')
        if p == '/api/diagnose':
            with _lock:
                hit = _cur_cache().get('diag')
                if hit and time.time() - hit[0] < 90:
                    return self._json(dict(hit[1], cached=True))
            data = diagnose_all()
            with _lock:
                _cur_cache()['diag'] = (time.time(), data)
            return self._json(data)
        if p == '/api/yunku':
            return self._json(yunku_list())
        if p == '/api/exportRecords':
            return self._json(export_records())
        return self._json({'ok': False, 'error': 'unknown endpoint ' + p}, 404)

    def _api_post(self, p, b):
        if p.startswith('/api/xf9/'):              # 9.0 AI 运营中心
            return self._json(XF9.api_post(p, b))
        if p.startswith('/api/dd/'):                # 7.0 抖店后台管理
            return self._json(DD.api_post(p, b))
        if p.startswith('/api/publish/'):          # 6.0 上架平台
            return self._json(PB.api_post(p, b))
        # ---- 多账号独立空间（2026-11-07）----
        # 一账号一份凭据、可停用、绝不改全局 current。
        # 参照 WorkBuddy Manager /accounts：账号文件物理隔离 + 停用不删凭据 +
        # 列表永远带当前上下文。这里 current 只作"老链接默认值"，主路径是 X-Shop-Id。
        if p == '/api/account/add':
            aid = (b.get('appid') or '').strip()
            sec = (b.get('secret') or '').strip()
            name = (b.get('name') or '').strip()
            kind = (b.get('kind') or 'wx_api').strip()
            if not aid:
                return self._json({'ok': False, 'error': '账号标识（AppID）不能为空'})
            d = _load_shops()
            hit = next((x for x in d['shops'] if x.get('appid') == aid), None)
            if hit is None:
                if not sec and kind == 'wx_api':
                    return self._json({'ok': False, 'error': '新账号必须填 AppSecret'})
                hit = {'appid': aid, 'secret': sec, 'name': name or ('店铺' + str(len(d['shops']) + 1)),
                       'kind': kind, 'enabled': True,
                       'profileId': (b.get('profileId') or '').strip(),
                       'space': (b.get('space') or '').strip()}
                d['shops'].append(hit)
            else:
                if sec:
                    hit['secret'] = sec
                if name:
                    hit['name'] = name
                if b.get('kind'):
                    hit['kind'] = kind
                hit['enabled'] = True
                if b.get('profileId') is not None:
                    hit['profileId'] = str(b.get('profileId') or '').strip()
                if b.get('space') is not None:
                    hit['space'] = str(b.get('space') or '').strip()
            _save_shops(d)
            return self._json({'ok': True, 'msg': '账号已保存：' + (hit.get('name') or aid),
                               'appid': aid})
        if p == '/api/account/disable':
            aid = (b.get('appid') or '').strip()
            d = _load_shops()
            hit = next((x for x in d['shops'] if x.get('appid') == aid), None)
            if hit is None:
                return self._json({'ok': False, 'error': '没有这个账号：' + aid})
            hit['enabled'] = not bool(b.get('disabled', True))
            _save_shops(d)
            return self._json({'ok': True, 'enabled': hit['enabled'],
                               'msg': ('已启用 ' if hit['enabled'] else '已停用 ') + (hit.get('name') or aid)})
        if p == '/api/config':
            # 多店铺：写 shops.json。appid 已存在 = 改它；不存在 = 新增一家并切过去。
            d = _load_shops()
            aid = (b.get('appid') or '').strip()
            sec = (b.get('secret') or '').strip()
            name = (b.get('name') or '').strip()
            # 兼容老调用（不带 appid/secret 时，只改当前店的显示名）
            if not aid:
                cur = _cur_shop(d)
                if name:
                    cur['name'] = name
                    _save_shops(d)
                get_shop(force=True)
                return self._json({'ok': True, 'msg': '已保存'})
            hit = None
            for x in d['shops']:
                if x.get('appid') == aid:
                    hit = x
                    break
            if hit is None:
                hit = {'appid': aid, 'secret': sec, 'name': name or ('店铺' + str(len(d['shops']) + 1))}
                d['shops'].append(hit)
            if sec:
                hit['secret'] = sec
            if name:
                hit['name'] = name
            d['current'] = aid
            if b.get('port'):
                try:
                    d['port'] = int(b['port'])
                except Exception:
                    pass
            _save_shops(d)
            get_shop(force=True)
            try:
                get_shop().token(force=True)
                info = get_shop().shop_info()
                si = info.get('info') or info.get('shop_info') or {}
                if si.get('nickname') and not name:
                    for x in d['shops']:
                        if x.get('appid') == aid:
                            x['name'] = si['nickname']
                    _save_shops(d)
                return self._json({'ok': True, 'msg': '已保存并切到这家店：' + (si.get('nickname') or aid),
                                   'shop': info})
            except WxError as e:
                return self._json({'ok': False, 'error': '保存成功但连接失败：%s' % e.errmsg})
        if p == '/api/shop/switch':
            d = _load_shops()
            aid = (b.get('appid') or '').strip()
            if not any(x.get('appid') == aid for x in d['shops']):
                return self._json({'ok': False, 'error': '没有这家店：' + aid})
            d['current'] = aid
            _save_shops(d)
            get_shop(force=True)
            try:
                info = get_shop().shop_info()
                si = info.get('info') or info.get('shop_info') or {}
                return self._json({'ok': True, 'msg': '已切换到 ' + (si.get('nickname') or aid), 'shop': info})
            except WxError as e:
                return self._json({'ok': False, 'error': '已切换但连接失败：%s' % e.errmsg})
        if p == '/api/shop/remove':
            d = _load_shops()
            aid = (b.get('appid') or '').strip()
            if len(d['shops']) <= 1:
                return self._json({'ok': False, 'error': '至少留一家店'})
            d['shops'] = [x for x in d['shops'] if x.get('appid') != aid]
            if d.get('current') == aid:
                d['current'] = (d['shops'][0] or {}).get('appid', '')
            _save_shops(d)
            get_shop(force=True)
            return self._json({'ok': True, 'msg': '已移除'})
        if p == '/api/product/add':
            s = get_shop()
            pl = _complete_product_payload(s, b.get('payload') or b)
            j = s.add_product(pl)
            return self._json({'ok': True, 'data': j})
        if p == '/api/product/update':
            j = get_shop().update_product(b.get('payload') or b)
            return self._json({'ok': True, 'data': j})
        if p == '/api/product/status':
            s = get_shop()
            pid = b.get('id')
            if b.get('action') == 'up':
                j = s.listing(pid)
            else:
                j = s.delisting(pid)
            log_task({'op': 'status', 'mode': b.get('action'), 'total': 1, 'ok': 1, 'fail': 0,
                      'items': [{'id': pid, 'ok': True, 'msg': 'ok'}]})
            return self._json({'ok': True, 'data': j})
        if p == '/api/product/delete':
            j = get_shop().delete_product(b.get('id'))
            log_task({'op': 'delete', 'mode': '', 'total': 1, 'ok': 1, 'fail': 0,
                      'items': [{'id': b.get('id'), 'ok': True, 'msg': 'ok'}]})
            return self._json({'ok': True, 'data': j})
        if p == '/api/batch':
            ids = [str(x) for x in (b.get('ids') or []) if str(x).strip()]
            if not ids:
                return self._json({'ok': False, 'error': '没有选中商品'})
            if len(ids) > 200:
                return self._json({'ok': False, 'error': '单次最多 200 件'})
            rec = run_batch(ids, b.get('op'), b.get('mode', 'set'), b.get('value'), bool(b.get('dry_run')))
            return self._json({'ok': True, 'data': rec})
        if p == '/api/edit':
            ids = [str(x) for x in (b.get('ids') or []) if str(x).strip()]
            if not ids:
                return self._json({'ok': False, 'error': '没有选中商品'})
            return self._json({'ok': True, 'data': run_edit(ids, b.get('op'), b.get('args') or {}, bool(b.get('dry_run')))})
        if p == '/api/photo/inpaint':
            return self._json(PH.inpaint_one(int(b.get('root') or 0), b.get('id'), b.get('rects') or [],
                                             b.get('engine') or 'lama', int(b.get('pad') or 0),
                                             bool(b.get('inplace'))))
        if p == '/api/photo/batch':
            return self._json(PH.batch(int(b.get('root') or 0), b.get('ids') or [], b.get('rects') or [],
                                       b.get('engine') or 'lama', int(b.get('pad') or 0)))
        if p == '/api/photo/detect':
            return self._json(PH.detect(int(b.get('root') or 0), b.get('id')))
        if p == '/api/patch':
            ids = [str(x) for x in (b.get('ids') or []) if str(x).strip()]
            if not ids:
                return self._json({'ok': False, 'error': '没有选中商品'})
            rec = run_patch(ids, b.get('patch') or {}, bool(b.get('dry_run')))
            return self._json({'ok': True, 'data': rec})
        if p == '/api/yunku/save':
            ids = [str(x) for x in (b.get('ids') or []) if str(x).strip()]
            if not ids:
                return self._json({'ok': False, 'error': '没有选中商品'})
            return self._json(yunku_save(ids))
        if p == '/api/yunku/remove':
            name = os.path.basename(str(b.get('name') or ''))
            fp = os.path.join(YUNKU, name)
            if name.endswith('.json') and os.path.isfile(fp):
                os.remove(fp)
                return self._json({'ok': True})
            return self._json({'ok': False, 'error': '文件不存在'})
        if p == '/api/export':
            ids = [str(x) for x in (b.get('ids') or []) if str(x).strip()]
            st = b.get('status')
            return self._json(export_csv(ids, status=st if st not in (None, '') else None))
        if p == '/api/import/preview':
            return self._json(import_preview(b.get('text') or '', b.get('name') or ''))
        if p == '/api/import/normalize':
            return self._json(import_normalize(b.get('text') or '', b.get('name') or ''))
        if p == '/api/import/create':
            nm = b.get('norm') or import_normalize(b.get('text') or '', b.get('name') or '')
            # 末级类目：新参数 cat_id 优先；兼容老前端仍传 template_id（数字类目ID直接用，
            # 若是商品ID长度则回退默认茶台类目，避免老链接再次静默失败）。
            cat = str(b.get('cat_id') or '').strip()
            if not cat:
                old = str(b.get('template_id') or '').strip()
                cat = old if (old.isdigit() and len(old) <= 8) else IMPORT_DEFAULT_CAT
            return self._json(import_create(nm, cat,
                                            b.get('limit') or 5, bool(b.get('dry_run', True))))
        return self._json({'ok': False, 'error': 'unknown endpoint ' + p}, 404)

    def _upload(self):
        ctype = self.headers.get('Content-Type', '')
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n)
        if 'boundary=' not in ctype:
            return self._json({'ok': False, 'error': '需要 multipart/form-data'})
        bd = ctype.split('boundary=', 1)[1].strip().strip('"')
        # 取第一个文件段
        parts = raw.split(('--' + bd).encode())
        for part in parts:
            if b'filename=' not in part:
                continue
            head, _, body = part.partition(b'\r\n\r\n')
            if not body:
                continue
            body = body.rstrip(b'\r\n')
            fn = 'upload.jpg'
            for seg in head.split(b';'):
                if b'filename=' in seg:
                    fn = seg.split(b'=')[1].strip().strip(b'"').decode('utf-8', 'replace')
            url = get_shop().upload_image(fn, body)
            try:
                with open(UPLOADS, 'a', encoding='utf-8') as uf:
                    uf.write(json.dumps({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'name': fn,
                                         'size': len(body), 'url': url}, ensure_ascii=False) + '\n')
            except Exception:
                pass
            return self._json({'ok': True, 'img_url': url, 'size': len(body)})
        return self._json({'ok': False, 'error': '没找到文件字段'})

    def do_DELETE(self):
        self._json({'ok': False, 'error': 'use POST'}, 405)


# ---------------------------------------------------------------- 通用编辑（整对象更新）
EDIT_SPEC = {
    'main_pic': [('mode', 'select', '方式', [('replace', '整组替换'), ('append', '追加'), ('remove', '按下标删除')]),
                 ('urls', 'textarea', '图片地址（逗号或换行分隔）', None),
                 ('index', 'number', '删除时用（0 起）', None)],
    'desc_pic': [('mode', 'select', '方式', [('replace', '整组替换'), ('append', '追加'), ('remove', '按下标删除')]),
                 ('urls', 'textarea', '图片地址（逗号或换行分隔）', None),
                 ('index', 'number', '删除时用（0 起）', None)],
    'video': [('mode', 'select', '方式', [('replace', '整组替换'), ('append', '追加')]),
              ('urls', 'textarea', '视频地址（逗号或换行分隔）', None)],
    'sku_pic': [('sku_id', 'text', 'SKU ID（空=全部）', None), ('value', 'text', '图片地址', None)],
    'sku_name': [('sku_id', 'text', 'SKU ID', None), ('value', 'text', '新属性值（即 SKU 名）', None)],
    'sku_code': [('sku_id', 'text', 'SKU ID（空=全部）', None), ('value', 'text', '新编码', None)],
    'sku_add': [('attr_key', 'text', '属性名（默认 销售属性1）', None), ('attr_value', 'text', '新规格值', None),
                ('price', 'number', '售价（元）', None), ('stock', 'number', '库存', None)],
    'sku_del': [('sku_id', 'text', '要删除的 SKU ID', None)],
    'sku_copy': [('sku_id', 'text', '复制哪个 SKU ID', None), ('attr_value', 'text', '新规格值', None)],
    'category': [('cat_ids', 'text', '末级类目链，逗号分隔（如 1247,549041,549074）', None)],
    'brand': [('brand_id', 'text', '品牌 ID（数字）', None)],
    'weight': [('value', 'number', '重量（克）', None)],
    'spu_code': [('value', 'text', '货号', None)],
    'sub_title': [('value', 'text', '短标题（≤20 字）', None)],
    'short_title': [('value', 'text', '导购短标题', None)],
    'aftersale_desc': [('value', 'text', '售后说明', None)],
    'sale_limit': [('num', 'number', '限购数量（0=不限）', None), ('period', 'select', '周期', [('0', '不限'), ('1', '终身'), ('2', '每天'), ('3', '每周'), ('4', '每月')])],
    'attr': [('mode', 'select', '方式', [('merge', '逐条覆盖（保留未提及项）'), ('replace', '整组替换（清空重建）')]),
             ('attrs', 'textarea', '属性，格式 键:值|键:值', None)],
    'freight': [('template_id', 'text', '运费模板 ID', None), ('express_type', 'text', '计费方式 0按件/1按重（可空）', None)],
    'stock_type': [('value', 'select', '发货时效', [('0', '现货'), ('1', '预售')])],
    'size_chart': [('enable', 'select', '尺码表', [('1', '启用'), ('0', '关闭')])],
    'deliver': [('method', 'select', '发货方式', [('0', '快递'), ('1', '虚拟')]), ('weight', 'number', '重量（克，可空）', None)],
}


def _skus(p):
    return p.get('skus') or []


def _urls(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    import re as _re
    return [u.strip() for u in _re.split(r'[,;\s\n]+', str(v or '')) if u.strip()]


def edit_apply(op, p, a):
    """就地修改 edit_product 结构；返回改了什么（中文短句），空列表=没得改。"""
    notes = []
    if op in ('main_pic', 'desc_pic', 'video'):
        key = 'head_imgs' if op == 'main_pic' else ('head_videos' if op == 'video' else 'desc_info')
        if op == 'desc_pic':
            di = p.get('desc_info') or {}
            cur = list(di.get('imgs') or [])
        else:
            cur = list(p.get(key) or [])
        mode = a.get('mode') or 'replace'
        urls = _urls(a.get('urls'))
        if mode == 'replace':
            if not urls:
                return []
            new = urls
            notes.append('%s 整组替换：%d → %d' % (key, len(cur), len(new)))
        elif mode == 'append':
            if not urls:
                return []
            new = cur + urls
            notes.append('%s 追加 %d 张（%d → %d）' % (key, len(urls), len(cur), len(new)))
        else:
            idx = int(a.get('index') or 0)
            if idx < 0 or idx >= len(cur):
                return []
            new = cur[:idx] + cur[idx + 1:]
            notes.append('%s 删除第 %d 张（%d → %d）' % (key, idx, len(cur), len(new)))
        if op == 'desc_pic':
            di = p.get('desc_info') or {}
            di['imgs'] = new
            p['desc_info'] = di
        else:
            p[key] = new
        return notes

    if op in ('sku_pic', 'sku_name', 'sku_code'):
        sid = str(a.get('sku_id') or '').strip()
        val = a.get('value')
        if val is None or str(val) == '':
            return []
        hit = 0
        for sk in _skus(p):
            if sid and str(sk.get('sku_id')) != sid:
                continue
            if op == 'sku_pic':
                sk['thumb_img'] = str(val)
            elif op == 'sku_code':
                sk['sku_code'] = str(val)
            else:
                ats = sk.get('sku_attrs') or [{'attr_key': '销售属性1'}]
                ats[0]['attr_value'] = str(val)
                sk['sku_attrs'] = ats
            hit += 1
        if hit:
            notes.append('%s：改 %d 个 SKU' % (op, hit))
        return notes

    if op == 'sku_add':
        av = str(a.get('attr_value') or '').strip()
        if not av:
            return []
        skus = _skus(p)
        if len(skus) >= 100:
            return []
        base = dict(skus[0]) if skus else {}
        ak = str(a.get('attr_key') or (base.get('sku_attrs') or [{}])[0].get('attr_key') or '销售属性1')
        new = {
            'sale_price': int(round(float(a.get('price') or 0) * 100)),
            'stock_num': int(a.get('stock') or 0),
            'sku_attrs': [{'attr_key': ak, 'attr_value': av}],
            'sku_code': '', 'thumb_img': '', 'bar_code': '',
            'sku_deliver_info': base.get('sku_deliver_info') or {'stock_type': 0},
        }
        if new['sale_price'] <= 0:
            return []
        p['skus'] = skus + [new]
        notes.append('新增 SKU「%s」售价 %.2f 库存 %d' % (av, new['sale_price'] / 100.0, new['stock_num']))
        return notes

    if op == 'sku_del':
        sid = str(a.get('sku_id') or '').strip()
        skus = _skus(p)
        if not sid or len(skus) <= 1:
            return []
        keep = [s for s in skus if str(s.get('sku_id')) != sid]
        if len(keep) == len(skus):
            return []
        p['skus'] = keep
        notes.append('删除 SKU %s（%d → %d）' % (sid, len(skus), len(keep)))
        return notes

    if op == 'sku_copy':
        sid = str(a.get('sku_id') or '').strip()
        av = str(a.get('attr_value') or '').strip()
        skus = _skus(p)
        src = next((s for s in skus if str(s.get('sku_id')) == sid), None)
        if not src or not av:
            return []
        import copy as _copy
        new = _copy.deepcopy(src)
        new.pop('sku_id', None)
        ats = new.get('sku_attrs') or [{'attr_key': '销售属性1'}]
        ats[0]['attr_value'] = av
        new['sku_attrs'] = ats
        p['skus'] = skus + [new]
        notes.append('复制 SKU %s 为「%s」' % (sid, av))
        return notes

    if op == 'category':
        ids = [x.strip() for x in str(a.get('cat_ids') or '').split(',') if x.strip()]
        if len(ids) < 2:
            return []
        p['cats'] = [{'cat_id': c} for c in ids]
        notes.append('类目改为 ' + ' > '.join(ids) + '（属性可能需要同步调整）')
        return notes

    if op == 'brand':
        v = str(a.get('brand_id') or '').strip()
        if not v:
            return []
        p['brand_id'] = v
        notes.append('品牌改为 %s' % v)
        return notes

    if op == 'weight':
        v = a.get('value')
        if v in (None, ''):
            return []
        ei = p.get('express_info') or {}
        ei['weight'] = int(float(v))
        p['express_info'] = ei
        notes.append('重量改为 %d g' % ei['weight'])
        return notes

    if op == 'deliver':
        m = str(a.get('method') or '')
        if m == '':
            return []
        p['deliver_method'] = int(m)
        if a.get('weight') not in (None, ''):
            ei = p.get('express_info') or {}
            ei['weight'] = int(float(a.get('weight')))
            p['express_info'] = ei
        notes.append('发货方式改为 %s' % ('快递' if m == '0' else '虚拟'))
        return notes

    if op == 'sale_limit':
        li = p.get('limited_info') or {}
        li['limited_buy_num'] = int(a.get('num') or 0)
        li['period_type'] = int(a.get('period') or 0)
        p['limited_info'] = li
        notes.append('限购：%d 件 / 周期 %s' % (li['limited_buy_num'], li['period_type']))
        return notes

    if op == 'attr':
        raw = str(a.get('attrs') or '').strip()
        if not raw:
            return []
        items = []
        for seg in raw.replace('\n', '|').split('|'):
            seg = seg.strip()
            if not seg:
                continue
            if ':' not in seg:
                return []
            k, v = seg.split(':', 1)
            items.append({'attr_key': k.strip(), 'attr_value': v.strip()})
        if not items:
            return []
        # 默认「逐条覆盖」而不是整组替换：官方按类目校验必填属性（6600045），
        # 整组替换会把用户没提到的必填项一起删掉，提交必被拒且原因难懂（本轮实测撞到）。
        mode = str(a.get('mode') or 'merge')
        old = p.get('attrs') or []
        if mode == 'replace':
            p['attrs'] = items
            return ['属性整组替换为 %d 条（原 %d 条全部丢弃）' % (len(items), len(old))]
        keep = [x for x in old if x.get('attr_key') and
                not any(n['attr_key'] == x['attr_key'] for n in items)]
        p['attrs'] = items + keep
        return ['属性覆盖 %d 条，保留未提及的 %d 条（共 %d 条；要整组清空请选「整组替换」）'
                % (len(items), len(keep), len(items) + len(keep))]

    if op == 'freight':
        tid = str(a.get('template_id') or '').strip()
        if not tid:
            return []
        ei = p.get('express_info') or {}
        ei['template_id'] = tid
        if str(a.get('express_type') or '') != '':
            ei['express_type'] = int(a.get('express_type'))
        p['express_info'] = ei
        return ['运费模板改为 %s' % tid]

    if op == 'stock_type':
        v = a.get('value')
        if v in (None, ''):
            return []
        n = 0
        for sk in _skus(p):
            di = sk.get('sku_deliver_info') or {'stock_type': 0}
            di['stock_type'] = int(v)
            sk['sku_deliver_info'] = di
            n += 1
        return ['发货时效改为 %s（%d 个 SKU）' % ('预售' if str(v) == '1' else '现货', n)] if n else []

    if op == 'size_chart':
        e = str(a.get('enable') or '')
        if e == '':
            return []
        sc = p.get('size_chart') or {'enable': False, 'specification_list': []}
        sc['enable'] = (e == '1')
        p['size_chart'] = sc
        return ['尺码表 ' + ('启用' if sc['enable'] else '关闭')]

    field = {'spu_code': 'spu_code', 'sub_title': 'sub_title', 'short_title': 'short_title',
             'aftersale_desc': 'aftersale_desc'}.get(op)
    if field:
        v = a.get('value')
        if v is None:
            return []
        p[field] = str(v)
        notes.append('%s 改为「%s」' % (field, str(v)[:30]))
        return notes
    return []


def run_edit(ids, op, args, dry_run=False):
    if op not in EDIT_SPEC:
        raise WxError(-5, '未知编辑动作 %s' % op, 'edit')
    s = get_shop()
    items = []

    def one(pid):
        try:
            raw = s.product(pid)
            p = _p(raw)
            import copy as _copy
            before = _copy.deepcopy(p)
            notes = edit_apply(op, dict(p), args)  # 试算（不改原对象）
            if not notes:
                return {'id': pid, 'ok': False, 'msg': '参数不足，未做任何修改'}
            # 真改：重新取一份并修改
            raw2 = s.product(pid)
            p2 = _p(raw2)
            notes = edit_apply(op, p2, args)
            changed = [k for k in p2 if json.dumps(p2.get(k), ensure_ascii=False, sort_keys=True) !=
                       json.dumps(before.get(k), ensure_ascii=False, sort_keys=True)]
            if dry_run:
                return {'id': pid, 'ok': True, 'msg': '预览：' + '；'.join(notes) + '｜提交字段 ' + ','.join(changed)}
            payload = {k: p2[k] for k in changed}
            payload['product_id'] = str(pid)
            try:
                s.update_product(payload)
                extra = ''
            except WxError:
                s.update_product(p2)
                extra = '（整对象兜底）'
            return {'id': pid, 'ok': True, 'msg': '已提交' + extra + '：' + '；'.join(notes)}
        except WxError as e:
            return {'id': pid, 'ok': False, 'msg': '[%s] %s' % (e.errcode, e.errmsg)}
        except Exception as e:
            return {'id': pid, 'ok': False, 'msg': str(e)}

    with ThreadPoolExecutor(max_workers=2) as ex:
        items = list(ex.map(one, ids))
    okc = sum(1 for i in items if i['ok'])
    rec = {'op': 'edit:' + op, 'mode': '', 'value': json.dumps(args, ensure_ascii=False)[:200],
           'dry_run': dry_run, 'total': len(ids), 'ok': okc, 'fail': len(ids) - okc, 'items': items}
    log_task(rec)
    return rec


LLMS = """# 微信小店·自营上货台 (127.0.0.1:9191)

自建的微信小店（视频号小店）商品管理/上货工具，直连微信官方 channels/ec 接口，
不经过任何第三方 SaaS。凭据只存在本机 config.json。

## 能力
- 商品列表/详情（含未上架、回收站）
- 批量改价格、改库存、改标题、上下架、删除
- 图片上传（→ mmecimage.cn 永久链接）
- 类目树 5041 条 + 类目必填属性/资质
- 运费模板、优惠券、订单
- 每次批量操作写 data/tasks.jsonl

## 关键 API
GET  /api/health  /api/shop  /api/products  /api/product?id=
GET  /api/overview            展示台数据（一次拉齐；60 秒内存缓存 + data/overview.json 兜底）
GET  /api/shops               已配的店铺列表与当前店铺
GET  /api/sku/audit?mode=get|scan   全店 SKU 规格图缺口（scan 后台重扫约 45 秒）
GET  /api/categories?q=  /api/category?id=  /api/freight  /api/coupons  /api/orders  /api/tasks
POST /api/upload  /api/product/add  /api/product/update  /api/product/status  /api/product/delete
POST /api/batch {ids,op:price|stock|title|status,mode,value,dry_run}
POST /api/config {appid,secret,name}   写 shops.json：已存在=改它，不存在=新增并切过去
POST /api/shop/switch {appid}    /api/shop/remove {appid}

## 多店铺
凭据真源是 shops.json（{"current","port","shops":[{appid,secret,name}]}）；
config.json 仍兼容（首次自动迁移）。界面右上角下拉可切店/加店。

## 已知限制
平台没有开放「展现/曝光」数据接口（实测 /channels/ec/product/data/get 报 48001 未授权，
其余候选路径 40066 invalid url），所以概览里的比率是**上架率 = 上架/全店**，不是展现率。
订单接口时间窗上限 7 天（超过报 40097）。
"""


def _ensure_streams():
    """pythonw 下 sys.stdout / sys.stderr 是 None —— 不补的话：
      · main() 里的 print 直接抛 AttributeError；
      · 更致命的是 BaseHTTPRequestHandler.log_message() 第一行就写 sys.stderr，
        于是**每个请求在回包之前就断**：端口在听、TCP 能连、发请求立刻 RemoteDisconnected。
      这条坑 2026-10-27 在 wxserve 里踩过，这里补上同样的兜底（2026-11-01）。"""
    logdir = os.path.join(DATA, 'logs')
    try:
        os.makedirs(logdir, exist_ok=True)
    except Exception:
        pass
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is not None:
            continue
        f = None
        try:
            f = open(os.path.join(logdir, 'wx60-std.log'), 'a', encoding='utf-8', buffering=1)
        except Exception:
            try:
                f = open(os.devnull, 'w', encoding='utf-8')
            except Exception:
                f = None
        if f is not None:
            setattr(sys, name, f)


def main():
    _ensure_streams()
    _seed_overview_cache()
    # 6.0：LaMa 后台预热（首次加载约 26s，不预热第一次点修补像卡死）
    def _warm():
        try:
            m = PH.lama()
            if m is not None:
                m.warmup()
        except Exception as e:
            sys.stderr.write('lama warmup failed: %s\n' % e)
    threading.Thread(target=_warm, daemon=True).start()
    cfg = load_cfg()
    port = int(cfg.get('port', 9191))
    srv = ThreadingHTTPServer(('127.0.0.1', port), H)
    print('微信小店·自营上货台  ->  http://127.0.0.1:%d' % port)
    print('AppID: %s' % ((cfg.get('appid') or '(未配置)')[:8] + '****' if cfg.get('appid') else '(未配置)'))
    srv.serve_forever()


if __name__ == '__main__':
    main()






