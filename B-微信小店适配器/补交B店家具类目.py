# -*- coding: utf-8 -*-
"""B 店（崇耿）剩余家具类目：幂等补交 + 只读复核。

用法：
  python 补交B店家具类目.py --verify-only   # 只读：回读已开通数 / 当日额度，不写任何店铺数据
  python 补交B店家具类目.py                 # 提交 remaining.json 里未开通的，成功即销账

纪律：只调 addcategory / list/get / quota/get / stable_token；
      绝不调 缴纳·充值·支付·提现·删除·撤销·停用。
额度：addcategory 日限 100；remaining=62 < 100，一次可补完；
      重跑会先回读已开通并跳过，不会重复提交（幂等）。
"""
import json, os, sys, time, ssl, urllib.request, urllib.parse, urllib.error

OUT = r'D:\DSH_Cache\cache\tmp\ego'
BASE = 'https://api.weixin.qq.com'
APPID = 'wxfc4e24a2ebcb2b0a'          # B 店 崇耿
SECRET = 'b07ad018647b4af2de637952cacac937'
TOKF = os.path.join(OUT, 'tok_20261108.json')
REM = os.path.join(OUT, 'remaining.json')
RESULT = os.path.join(OUT, 'resume_result.json')


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def token(force=False):
    toks = _load(TOKF, {})
    d = toks.get(APPID) or {}
    if not force and d.get('v') and d.get('exp', 0) > time.time() + 120:
        return d['v']
    body = json.dumps({'grant_type': 'client_credential',
                       'appid': APPID, 'secret': SECRET}).encode()
    req = urllib.request.Request(BASE + '/cgi-bin/stable_token', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25,
                                context=ssl.create_default_context()) as r:
        j = json.loads(r.read().decode('utf-8', 'ignore'))
    if not j.get('access_token'):
        raise RuntimeError('stable_token fail: %s' % j)
    toks[APPID] = {'v': j['access_token'],
                   'exp': time.time() + int(j.get('expires_in') or 7200)}
    json.dump(toks, open(TOKF, 'w', encoding='utf-8'))
    return j['access_token']


def call(path, body=None, retry=True, timeout=30):
    url = BASE + path + '?' + urllib.parse.urlencode({'access_token': token()})
    data = json.dumps(body or {}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data,
                                 headers={'User-Agent': 'Mozilla/5.0',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            j = json.loads(r.read().decode('utf-8', 'ignore'))
    except urllib.error.HTTPError as e:
        j = {'errcode': -e.code, 'errmsg': e.read().decode('utf-8', 'ignore')[:300]}
    except Exception as e:
        j = {'errcode': -1, 'errmsg': '%s: %s' % (type(e).__name__, e)}
    if isinstance(j, dict) and j.get('errcode') in (40001, 42001, 40014) and retry:
        token(force=True)
        return call(path, body, retry=False, timeout=timeout)
    return j


def opened_ids():
    j = call('/channels/ec/category/list/get', {})
    return j, set(str(x.get('cat_id')) for x in (j.get('list') or []))


def build_body(r):
    return {'category_info': {
        'cats_v2': [{'cat_id': int(x)} for x in r['path_ids']],
        'is_new_apply_cat': True,
        'license_group_list': [],
    }}


def main():
    verify_only = '--verify-only' in sys.argv
    oj, have = opened_ids()
    q = call('/cgi-bin/openapi/quota/get',
             {'cgi_path': '/channels/ec/category/add'})
    quota = (q.get('quota') or {})
    print('[回读] list/get errcode=%s 已开通=%d' % (
        oj.get('errcode'), len(have)), flush=True)
    print('[回读] quota daily_limit=%s used=%s remain=%s' % (
        quota.get('daily_limit'), quota.get('used'), quota.get('remain')), flush=True)
    if verify_only:
        print('[verify-only] 未做任何写入。', flush=True)
        return

    rows = _load(REM, [])
    todo = [r for r in rows if str(r['cat_id']) not in have]
    print('[补交] remaining=%d 未开通待补=%d' % (len(rows), len(todo)), flush=True)
    if not todo:
        print('[补交] 已全部开通，无需提交。', flush=True)
        return

    log = open(os.path.join(OUT, 'resume_log.jsonl'), 'a', encoding='utf-8')
    ok, consec_fail = 0, 0
    done = set()
    for i, r in enumerate(todo, 1):
        body = build_body(r)
        j = call('/channels/ec/category/add', body)
        rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
               'cat_id': r['cat_id'], 'name': r['name'],
               'request': body, 'response': j}
        log.write(json.dumps(rec, ensure_ascii=False) + '\n')
        log.flush()
        if j.get('errcode') == 0 or j.get('audit_id'):
            ok += 1
            consec_fail = 0
            done.add(str(r['cat_id']))
        else:
            consec_fail += 1
        if i % 20 == 0 or i <= 3 or consec_fail >= 8:
            print('  %d/%d %s %s -> %s' % (
                i, len(todo), r['cat_id'], r['name'][:14],
                json.dumps(j, ensure_ascii=False)[:140]), flush=True)
        if consec_fail >= 8:
            print('[熔断] 连续 8 次失败（多为 45009 日限），停止；剩余留待次日。', flush=True)
            break
        time.sleep(0.18)
    log.close()

    left = [r for r in rows if str(r['cat_id']) not in done]
    json.dump(left, open(REM, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    vj, have2 = opened_ids()
    json.dump({'ts': time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
               'submitted_ok': ok, 'remaining': len(left),
               'opened_before': len(have), 'opened_after': len(have2)},
              open(RESULT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[完成] 本次成功=%d 剩余=%d 已开通 %d→%d' % (
        ok, len(left), len(have), len(have2)), flush=True)


if __name__ == '__main__':
    main()
