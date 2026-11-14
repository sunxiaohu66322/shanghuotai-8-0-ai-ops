# -*- coding: utf-8 -*-
"""E1 Dry-run 测试 · 验证 wxapi_e1.py 的 4 个上传接口封装逻辑。全离线、零真实网络。

不碰生产 wxapi.py / 不读不写 v3.db / 不对任何真实商品执行 update_product。
运行：python test_e1.py
"""
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wxapi_e1  # noqa: E402

results = []


def rec(item, passed, detail=''):
    results.append({'item': item, 'pass': bool(passed), 'detail': str(detail)[:300]})


def make_shop(handler):
    """造一个 E1WxVideo 实例，把 _http 整体替换成 handler(url, data, headers)。"""
    shop = wxapi_e1.E1WxVideo(token_getter=lambda: 'E1_DRYRUN_TOKEN')
    def _fake_http(url, data=None, headers=None, timeout=30):
        return handler(url, data, headers or {})
    shop._http = _fake_http
    return shop


# ---------- Fake 微信后端：模拟 4 个接口的真实响应 ----------
class FakeWx(object):
    """按接口路径返回模拟 JSON，记录收到的 init/finish 请求参数。"""
    def __init__(self, key='E1VIDE0KEY', bad_play=False, bad_finish=False):
        self.key = key
        self.bad_play = bad_play      # 播放信息返回非微信前缀 url（模拟第三方直链）
        self.bad_finish = bad_finish  # finish 返回 errcode != 0
        self.got = {'init': None, 'finish': None}

    def handler(self, url, data, headers):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(url)
        p = u.path.replace(wxapi_e1.E1WxVideo.BASE, '')
        q = parse_qs(u.query)
        if p == '/shop/ec/basics/video/initupload':
            self.got['init'] = json.loads(data.decode('utf-8'))
            return 200, json.dumps({'errcode': 0, 'video_upload_key': self.key}).encode()
        if p == '/shop/ec/basics/video/uploadpart':
            # 官方：video_upload_key / part_number 在 URL 查询串，multipart body 只装媒体字节
            assert q.get('video_upload_key', [''])[0] == self.key, 'uploadpart 缺 video_upload_key'
            assert q.get('part_number', [''])[0], 'uploadpart 缺 part_number'
            self.got.setdefault('parts', []).append(q.get('part_number', [''])[0])
            return 200, json.dumps({'errcode': 0}).encode()
        if p == '/shop/ec/basics/video/finishupload':
            self.got['finish'] = json.loads(data.decode('utf-8'))
            if self.bad_finish:
                return 200, json.dumps({'errcode': -1, 'errmsg': 'sha mismatch'}).encode()
            return 200, json.dumps({'errcode': 0}).encode()
        if p == '/shop/ec/basics/video/getplayinfo':
            u = 'https://some-cdn.example.com/3rdparty.mp4' if self.bad_play \
                else 'https://mmecimage.cn/p/' + self.key + '.mp4'
            return 200, json.dumps({'errcode': 0, 'status': 'done', 'video_url': u}).encode()
        return 404, json.dumps({'errcode': -1, 'errmsg': 'unknown ' + p}).encode()


# ---------- 用例 ----------
def t_init_params():
    f = FakeWx(); shop = make_shop(f.handler)
    shop.video_initupload('a.mp4', 123)
    ok = f.got['init'] == {'file_name': 'a.mp4', 'file_size': 123}
    rec('1 initupload 参数完整(file_name+file_size)', ok, f.got['init'])


def t_init_errors():
    shop = make_shop(FakeWx().handler)
    errs = []
    for fn, sz in [('', 100), ('a.mp4', 0), ('a.mp4', -5), ('a.mp4', None)]:
        try:
            shop.video_initupload(fn, sz)
            errs.append('no-raise:%r,%r' % (fn, sz))
        except wxapi_e1.WxError:
            pass
    rec('2 initupload 非法参数报错', not errs, errs or '全部拒绝')


def t_upload_part_number():
    f = FakeWx(); shop = make_shop(f.handler)
    shop.video_initupload('a.mp4', 3 * wxapi_e1.PART_SIZE)  # 3MB -> 3片
    parts = []
    for i in range(3):
        parts.append(shop.video_uploadpart(f.key, i + 1, b'x' * 10))
    nums = [p['part_number'] for p in parts]
    rec('3 uploadpart 分片号连续(1..3)', nums == [1, 2, 3], nums)


def t_upload_errors():
    shop = make_shop(FakeWx().handler)
    errs = []
    for args in [(None, 1, b'x'), ('k', 0, b'x'), ('k', 1, b''), ('k', 1, b'y' * (2 * 1024 * 1024 + 1))]:
        try:
            shop.video_uploadpart(*args)
            errs.append('no-raise:%r' % (args,))
        except wxapi_e1.WxError:
            pass
    rec('4 uploadpart 非法参数报错(缺key/号<1/空片/超2MB)', not errs, errs or '全部拒绝')


def t_finish_integrity():
    f = FakeWx(); shop = make_shop(f.handler)
    ok_key = shop.video_initupload('a.mp4', 100) == f.key
    # 分片连续通过
    parts = [shop.video_uploadpart(f.key, i + 1, b'z' * 5) for i in range(2)]
    good_sha = hashlib.sha256(b'z' * 5 + b'z' * 5).hexdigest()  # 真实整体 SHA
    try:
        shop.video_finishupload(f.key, parts, good_sha)
        ok_good = True
    except wxapi_e1.WxError as e:
        ok_good = False
    rec('5 finishupload 连续分片+合法SHA通过', ok_key and ok_good)

    # 分片不连续被拒（本地侧校验，不发网络）
    try:
        shop.video_finishupload(f.key, [{'part_number': 1}, {'part_number': 3}], good_sha)
        ok_gap = False
    except wxapi_e1.WxError:
        ok_gap = True
    # 非法 SHA 被拒
    try:
        shop.video_finishupload(f.key, parts, 'deadbeef')
        ok_sha = False
    except wxapi_e1.WxError:
        ok_sha = True
    rec('6 finishupload 不连续分片/非法SHA被拒', ok_gap and ok_sha,
        'gap=%s sha=%s' % (ok_gap, ok_sha))


def t_finish_errcode():
    f = FakeWx(bad_finish=True)
    shop = make_shop(f.handler)
    parts = [shop.video_uploadpart(f.key, 1, b'z')]
    try:
        shop.video_finishupload(f.key, parts, hashlib.sha256(b'z').hexdigest())
        ok = False
    except wxapi_e1.WxError as e:
        ok = e.errcode == -1
    rec('7 finishupload 后端 errcode!=0 抛错', ok)


def t_getplayinfo_3rdparty():
    f = FakeWx(bad_play=True)
    shop = make_shop(f.handler)
    try:
        shop.video_getplayinfo(f.key, _sleep=lambda s: None)
        ok = False
    except wxapi_e1.WxError as e:
        ok = e.errcode == 10020035
    rec('8 getplayinfo 第三方临时直链被拒(10020035)', ok)


def t_getplayinfo_ok():
    shop = make_shop(FakeWx().handler)
    u = shop.video_getplayinfo('k', _sleep=lambda s: None)
    rec('9 getplayinfo 微信临时地址正常返回', u.startswith('https://mmecimage.cn/p/'), u)


def t_full_flow(tmp):
    """端到端：造本地视频 -> 4 步上传 -> 得临时 url。验证不写盘、返回标记 temp。"""
    f = FakeWx(); shop = make_shop(f.handler)
    fp = os.path.join(tmp, 'v.mp4')
    content = b'V' * (wxapi_e1.PART_SIZE + 500)  # 1MB+ 跨 2 片
    open(fp, 'wb').write(content)
    before = sorted(os.listdir(tmp))
    url, meta = shop.upload_video_file(fp)
    after = sorted(os.listdir(tmp))
    ok = (url.startswith('https://mmecimage.cn/p/')
          and meta['temp'] is True
          and meta['part_count'] == 2
          and meta['file_sha256'] == hashlib.sha256(content).hexdigest()
          and before == after)  # 全程未在目录新增任何持久化文件
    rec('10 端到端4步上传(2片)+SHA对+不持久化', ok, meta)
    return url


def t_head_videos():
    u = 'https://mmecimage.cn/p/abc.mp4'
    ok1 = wxapi_e1.build_head_videos(u) == {'video_url': u}
    ok2 = wxapi_e1.build_head_videos('') == {'video_url': ''}  # 删视频=显式空
    try:
        wxapi_e1.build_head_videos('https://cdn.foo.com/x.mp4')
        ok3 = False
    except wxapi_e1.WxError:
        ok3 = True
    rec('11 build_head_videos 合法/删除空/第三方拒', ok1 and ok2 and ok3)


def t_no_real_network():
    """确认 wxapi_e1 默认 _http 会抛错，证明真实联网被禁止。"""
    bare = wxapi_e1.E1WxVideo()
    try:
        bare._http('https://api.weixin.qq.com/x', b'{}', {})
        ok = False
    except RuntimeError:
        ok = True
    rec('12 默认_http禁止真实联网(仅FakeHttp可注入)', ok)


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        t_init_params()
        t_init_errors()
        t_upload_part_number()
        t_upload_errors()
        t_finish_integrity()
        t_finish_errcode()
        t_getplayinfo_3rdparty()
        t_getplayinfo_ok()
        t_full_flow(tmp)
        t_head_videos()
        t_no_real_network()
    npass = sum(1 for r in results if r['pass'])
    print('===== E1 Dry-run 测试结果 =====')
    for r in results:
        print('%s  %s  %s' % ('PASS' if r['pass'] else 'FAIL', r['item'], r['detail']))
    print('小计：%d/%d 通过' % (npass, len(results)))
    json.dump(results, open(os.path.join(HERE, 'test_e1_result.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    return 0 if npass == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
