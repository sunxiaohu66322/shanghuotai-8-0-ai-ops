# -*- coding: utf-8 -*-
"""E2 收尾清理：把草稿品 pid 上的测试头视频摘掉，回读确认恢复原状。
只做恢复，不改任何其它字段；多姿势尝试，取第一个回读为空的。
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
sys.path.insert(0, PROD)
import wxapi  # noqa: E402

PID = '10001772962434'


def _p(raw):
    return (raw or {}).get('product') or (raw or {}).get('edit_product') or (raw or {})


def first_video_url(hv):
    if isinstance(hv, list):
        for it in hv:
            if isinstance(it, dict) and it.get('video_url'):
                return it['video_url']
        return ''
    if isinstance(hv, dict):
        return hv.get('video_url') or ''
    return ''


def main():
    conf = json.load(open(os.path.join(PROD, 'shops.json'), encoding='utf-8'))
    shop = (conf.get('shops') or [])[0]
    wx = wxapi.WxShop(shop['appid'], shop['secret'])

    def status():
        try:
            return first_video_url(_p(wx.product(PID)).get('head_videos'))
        except Exception as e:
            return '<读取失败:%s>' % str(e)[:60]

    before = status()
    print('清理前 video_url:', '有(len=%s)' % len(before) if before and not before.startswith('<') else before[:80])

    # 候选姿势：按副作用从小到大
    candidates = [
        ('array 空字符串', {'head_videos': [{'video_url': ''}]}),
        ('空 array', {'head_videos': []}),
        ('object 空字符串', {'head_videos': {'video_url': ''}}),
    ]
    for label, patch in candidates:
        payload = {'product_id': PID}
        payload.update(patch)
        try:
            wx.update_product(payload)
            after = status()
            ok = (after == '')
            print('  [%s] update=OK 回读=%s %s' %
                  (label, '空' if ok else '仍有(len=%s)' % len(after) if isinstance(after, str) else after,
                   '✅已恢复' if ok else ''))
            if ok:
                return 0
        except Exception as e:
            print('  [%s] update失败 -> %s' % (label, str(e)[:140]))

    # 兜底：整对象（拿完整商品，去掉/清空 head_videos 再提交）
    try:
        p = _p(wx.product(PID))
        p = dict(p)
        p['product_id'] = PID
        p['head_videos'] = [{'video_url': ''}]
        wx.update_product(p)
        after = status()
        print('  [整对象兜底] 回读=%s' % ('空 ✅已恢复' if after == '' else '仍有 len=%s' % len(after)))
        return 0 if after == '' else 1
    except Exception as e:
        print('  [整对象兜底] 失败 -> %s' % str(e)[:160])
        return 1


if __name__ == '__main__':
    sys.exit(main())
