# -*- coding: utf-8 -*-
"""三店去重总览（2 家微店 + 1 家抖店）。

主人 2026-11-08 定的口径：三个店都上架了同一个品，那就只算一件，不叠加装。
「同款」判定 = 标题去空白/去括号后取前 30 字（微店同款多铺、标题微调都能并掉）。

数据全部真拉，绝不编：
  · 微店  wxapi.WxShop 官方 product_ids + product 读标题（按 appid 分片，独立空间）
  · 抖店  没有免签接口，在售只认 ego 抓的后台快照 抖店研究/crawl3/dy-sold-list.csv
          快照没跑过就是不全，如实写进 dy_note，不补齐、不猜。

慢（1-2 分钟）只在后台线程 / 手动刷新时跑；结果落 data/union3.json，
HTTP 层只读文件，秒开。CLI: python union3.py compute
"""
import os, re, json, csv, time, threading
from concurrent.futures import ThreadPoolExecutor

try:
    from wxapi import WxShop
except Exception:                                    # pragma: no cover
    WxShop = None

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, 'data', 'union3.json')
ART = os.path.normpath(os.path.join(ROOT, '..', '抖店研究', 'crawl3'))
DY_SOLD = os.path.join(ART, 'dy-sold-list.csv')

# 只有这三个状态有货（overview 实测 11/12/13 恒为 0），少拉三趟省一半时间
PULL_STATUS = (5, 0, 6)
STATUS_NAME = {5: '上架', 0: '未上架', 6: '回收站'}

_LOCK = threading.Lock()
_RUN = {'on': False, 'at': '', 'err': ''}


def norm(t):
    t = re.sub(r'\s+', '', t or '')
    t = re.sub(r'[【\[（(].*?[】\]）)]', '', t)
    return t[:30]


def _wx_shops():
    d = json.load(open(os.path.join(ROOT, 'shops.json'), encoding='utf-8'))
    return [x for x in (d.get('shops') or [])
            if (x.get('kind') or 'wx_api') == 'wx_api'
            and x.get('enabled', True) is not False
            and x.get('appid') and x.get('secret')]


def _ids(s, status):
    ids, key = [], ''
    for _ in range(60):
        j = s.product_ids(status=status, page_size=50, next_key=key)
        ids += [str(i) for i in (j.get('product_ids') or [])]
        key = j.get('next_key') or ''
        if not key:
            break
    return ids


def _title(s, pid):
    try:
        x = s.product(pid)
        p = x.get('product') or x.get('edit_product') or x
        return norm(p.get('title'))
    except Exception:
        return ''


def _dy_on():
    rows = []
    if os.path.isfile(DY_SOLD):
        with open(DY_SOLD, encoding='utf-8-sig') as fh:
            for r in csv.DictReader(fh):
                t = norm(r.get('title'))
                if t and '售卖中' in (r.get('status') or ''):
                    rows.append(t)
    return rows


def compute():
    """真拉三店，产出全状态 / 上架 两套去重数。慢，别在请求线程里直接调。"""
    out = {
        'ok': True, 'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'rule': '标题去空白/括号后取前30字；同款在任意店/任意状态出现都只算一件',
        'shops': {}, 'union': {}, 'dy_note': '',
    }
    all_styles, on_styles, off_styles = {}, {}, {}
    for cfg in _wx_shops():
        name = cfg.get('name') or cfg['appid']
        s = WxShop(cfg['appid'], cfg['secret'])
        per, styles_all, styles_on = {}, set(), set()
        for st in PULL_STATUS:
            ids = _ids(s, st)
            per[STATUS_NAME[st]] = len(ids)
            with ThreadPoolExecutor(max_workers=24) as ex:
                ts = set(t for t in ex.map(lambda p: _title(s, p), ids) if t)
            styles_all |= ts
            if st == 5:
                styles_on |= ts
            per[STATUS_NAME[st] + '·款式'] = len(ts)
        all_styles[name] = styles_all
        on_styles[name] = styles_on
        off_styles[name] = styles_all - styles_on
        out['shops'][name] = {'件数': per, '款式数': len(styles_all),
                              '在架款式': len(styles_on), 'appid': cfg['appid']}
        print('[union3]', name, '件数', per, '款式', len(styles_all), flush=True)

    dy = _dy_on()
    dys = set(dy)
    all_styles['抖店·LZL'] = dys
    on_styles['抖店·LZL'] = dys
    off_styles['抖店·LZL'] = set()
    out['shops']['抖店·LZL'] = {'件数': {'上架': len(dy)}, '款式数': len(dys),
                                '在架款式': len(dys), 'appid': 'dd_lzl'}
    out['dy_note'] = ('抖店在售来自 dy-sold-list.csv（ego 抓的后台商品列表快照，'
                      '可能只覆盖第一页；没采过就是不全，绝不编造补齐）')

    def _union(d):
        u = set()
        for v in d.values():
            u |= v
        return u

    wx_names = [n for n in all_styles if n != '抖店·LZL']
    common_ab = (on_styles.get(wx_names[0], set()) & on_styles.get(wx_names[1], set())) if len(wx_names) >= 2 else set()
    common3 = common_ab & dys
    off_union = _union(off_styles)
    out['union'] = {
        'all_styles': len(_union(all_styles)),          # 全店商品（三店去重，含历史/回收站款）
        'on_styles': len(_union(on_styles)),            # 上架中（三店去重）★ 主人说的「100多件」
        'off_styles': len(off_union),                   # 未上架/回收站里没在架的款
        'common_ab_on': len(common_ab),                 # 两微店共同在架
        'common3_on': len(common3),                     # 三店共同在架
        'per_shop_on': {n: len(v) for n, v in on_styles.items()},
        'per_shop_on_items': {n: out['shops'][n]['件数'].get('上架', 0) for n in out['shops']},
    }
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(out, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('[union3] DONE', json.dumps(out['union'], ensure_ascii=False), flush=True)
    return out


def cached():
    """HTTP 层只读这个，秒开。没算过就给空壳 + 明确提示，不拿单店数冒充。"""
    if not os.path.isfile(CACHE):
        return {'ok': False, 'empty': True, 'hint': '三店去重还没算过，点「重算三店」拉一次真数'}
    try:
        return json.load(open(CACHE, encoding='utf-8'))
    except Exception as e:
        return {'ok': False, 'empty': True, 'hint': '缓存读失败：%s' % e}


def refresh_async():
    """触发后台重算；已在跑就直接回报，不叠线程。"""
    with _LOCK:
        if _RUN['on']:
            return {'ok': True, 'running': True, 'started': _RUN['at'], 'msg': '已在重算，稍等刷新'}
        _RUN['on'] = True
        _RUN['at'] = time.strftime('%H:%M:%S')
        _RUN['err'] = ''

    def run():
        try:
            compute()
        except Exception as e:
            with _LOCK:
                _RUN['err'] = str(e)
        finally:
            with _LOCK:
                _RUN['on'] = False

    threading.Thread(target=run, name='union3-refresh', daemon=True).start()
    return {'ok': True, 'running': True, 'started': _RUN['at'], 'msg': '已开始后台重算（约 1-2 分钟）'}


def status():
    with _LOCK:
        return dict(_RUN)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'compute':
        compute()
    else:
        print(json.dumps(cached(), ensure_ascii=False, indent=2))
