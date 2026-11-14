# -*- coding: utf-8 -*-
"""E2 前置只读探活：草稿箱 / token 状态 / 测试视频工具。全程只读，不改商品、不写业务数据、不打印密钥。

运行：python probe_e2.py
"""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)  # 微信小店自建上货台
sys.path.insert(0, PROD)
import wxapi  # noqa: E402


def mask(s):
    s = str(s or '')
    return s[:8] + '…' + s[-4:] if len(s) > 14 else '<短值>'


def main():
    cfg = json.load(open(os.path.join(PROD, 'shops.json'), encoding='utf-8'))
    shops = cfg.get('shops') or []
    print('=== 店铺（脱敏，仅字段名/前缀）===')
    for s in shops:
        print('  appid=%s name=%s enabled=%s' %
              (mask(s.get('appid')), s.get('name'), s.get('enabled', True)))
    print('\n=== token 缓存（不打印 token 明文）===')
    now = time.time()
    for s in shops:
        cf = os.path.join(PROD, '.cache', 'token_%s.json' % s.get('appid'))
        if os.path.isfile(cf):
            try:
                d = json.load(open(cf, encoding='utf-8'))
                left = int(d.get('exp', 0) - now)
                print('  %s: has_token=%s 剩余=%ds %s' %
                      (mask(s.get('appid')), bool(d.get('access_token')), left,
                       '(有效)' if left > 60 else '(过期,需重取)'))
            except Exception as e:
                print('  %s: 缓存读取失败 %s' % (mask(s.get('appid')), e))
        else:
            print('  %s: 无缓存' % mask(s.get('appid')))

    print('\n=== 草稿箱只读探活（product/list/get status=0, page_size=5）===')
    for s in shops:
        try:
            wx = wxapi.WxShop(s['appid'], s['secret'])
            j = wx.product_ids(status=0, page_size=5)
            ids = j.get('product_ids') or j.get('data', {}).get('product_ids') or []
            # 兼容不同返回结构
            if not ids:
                d = j.get('data') or {}
                ids = d.get('list') or d.get('product_ids') or []
            print('  %s (%s): errcode=%s 草稿数(取样)=%d total=%s' %
                  (mask(s.get('appid')), s.get('name'),
                   j.get('errcode', 0), len(ids),
                   j.get('total_num') or (j.get('data') or {}).get('total_num', '?')))
            for pid in ids[:5]:
                # 只打印 id，不拉详情
                print('     草稿ID:', (pid.get('product_id') if isinstance(pid, dict) else pid))
        except Exception as e:
            print('  %s (%s): 探活失败 -> %s' % (mask(s.get('appid')), s.get('name'), str(e)[:160]))

    print('\n=== 测试视频工具（自合成合法视频，避免第三方版权）===')
    import shutil
    ff = shutil.which('ffmpeg')
    print('  ffmpeg:', ff or '不在 PATH（需全路径或改用现成小 mp4）')


if __name__ == '__main__':
    main()
