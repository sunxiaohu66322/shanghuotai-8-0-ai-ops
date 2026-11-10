# -*- coding: utf-8 -*-
"""E1 开发副本 · 微信小店头图视频 4 个上传接口的独立实现（不碰生产 wxapi.py）。

红线（阶段E1）：
- 本文件只在 `微信小店自建上货台\\_e1dev\\` 里，绝不覆盖生产 `wxapi.py`。
- 只做「本地视频文件 → 微信侧临时 video_url」的封装 + Dry-run 可测逻辑。
- 不对任何真实/草稿商品执行 update_product；不读、不写 v3.db；不持久化临时 URL。

字段/路径依据（未对真实微信后台实测，E2 才做）：
  微信head_videos只读调查.md §2 / 微信开放文档「商品管理开发指南」+ product/add|update。
  分片口径：1MB~2MB，带 video_upload_key / part_number；finishupload 校验连续 + SHA；
  getplayinfo 返回的是临时地址，仅用于提交 head_videos，禁止当长期素材地址。
"""
import hashlib
import json
import os
import uuid

# 官方硬门槛：video_url 必须是 mmecimage.cn/p/ 前缀，第三方直链填进 head_videos
# 会被错误码 10020035 拒。
WX_VIDEO_URL_PREFIX = 'mmecimage.cn/p/'

# 分片大小：落在官方 1MB~2MB 区间内，取 1MB。
PART_SIZE = 1 * 1024 * 1024


class WxError(Exception):
    def __init__(self, errcode, errmsg, path=''):
        super().__init__('[%s] %s (%s)' % (errcode, errmsg, path))
        self.errcode, self.errmsg, self.path = errcode, errmsg, path


class E1WxVideo(object):
    """独立副本：只复刻 WxShop 的 token/call/_http 形态，便于 Dry-run 注入 mock。

    生产 wxapi.py 用的是 BASE='https://api.weixin.qq.com'；这里同口径，但
    _http 可被测试脚本整体替换，保证 E1 全程零真实网络请求。
    """
    BASE = 'https://api.weixin.qq.com'

    def __init__(self, token_getter=None):
        # token_getter 可注入；E1 不接触任何真实密钥/AppSecret。
        self._token_getter = token_getter or (lambda: 'E1_DRYRUN_TOKEN')
        self.calls = []  # Dry-run 记录每次接口调用，便于断言参数完整

    def token(self):
        return self._token_getter()

    def _http(self, url, data=None, headers=None, timeout=30):
        # 真实实现走 urllib；E1 的 Dry-run 用 FakeHttp 覆盖此方法，永不联网。
        raise RuntimeError('E1 副本禁止真实联网：请用 FakeHttp 覆盖 _http')

    def call(self, path, body=None, method='POST', raw_bytes=False):
        """JSON 接口统一入口。记录调用、解析 errcode、抛出 WxError。"""
        self.calls.append({'kind': 'json', 'path': path, 'body': body or {}})
        st, b = self._http(self.BASE + path, json.dumps(body or {}, ensure_ascii=False).encode('utf-8'),
                           {'Content-Type': 'application/json'})
        j = json.loads(b)
        ec = j.get('errcode', 0)
        if ec not in (0, None):
            raise WxError(ec, j.get('errmsg', ''), path)
        return j

    # ---------------- 4 个官方上传接口 ----------------
    def video_initupload(self, filename, size_bytes):
        """① 申请上传：传文件名+大小，得 video_upload_key。"""
        if not filename or not str(filename).strip():
            raise WxError(-1, 'video_initupload: filename 不能为空', 'video/initupload')
        try:
            size_bytes = int(size_bytes)
        except (TypeError, ValueError):
            raise WxError(-1, 'video_initupload: size_bytes 必须是整数', 'video/initupload')
        if size_bytes <= 0:
            raise WxError(-1, 'video_initupload: size_bytes 必须 > 0', 'video/initupload')
        j = self.call('/shop/ec/basics/video/initupload',
                      {'file_name': str(filename), 'file_size': size_bytes})
        key = j.get('video_upload_key')
        if not key:
            raise WxError(-1, 'video_initupload: 响应缺 video_upload_key', 'video/initupload')
        return key

    def video_uploadpart(self, key, part_number, data):
        """② 分块上传：multipart 传一片字节，带 video_upload_key / part_number。"""
        if not key:
            raise WxError(-1, 'video_uploadpart: 缺 video_upload_key', 'video/uploadpart')
        if not isinstance(part_number, int) or part_number < 1:
            raise WxError(-1, 'video_uploadpart: part_number 必须是 >=1 的整数', 'video/uploadpart')
        if not isinstance(data, (bytes, bytearray)) or len(data) == 0:
            raise WxError(-1, 'video_uploadpart: 分片数据为空', 'video/uploadpart')
        if len(data) > 2 * 1024 * 1024:
            raise WxError(-1, 'video_uploadpart: 单片超过 2MB 上限', 'video/uploadpart')
        boundary = '----e1wx%s' % uuid.uuid4().hex
        body = b''.join([
            ('--%s\r\nContent-Disposition: form-data; name="media"; filename="part_%d"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % (boundary, part_number)).encode(),
            bytes(data), b'\r\n', ('--%s--\r\n' % boundary).encode()])
        self.calls.append({'kind': 'part', 'path': '/shop/ec/basics/video/uploadpart',
                           'key': key, 'part_number': part_number, 'len': len(data)})
        # 官方 multipart 分片：video_upload_key / part_number 走 URL 查询串，
        # multipart body 只装媒体字节。缺这两项微信侧无法定位到本次上传与分片序号。
        from urllib.parse import urlencode
        qs = urlencode({'access_token': self.token(),
                        'video_upload_key': key,
                        'part_number': part_number})
        st, b = self._http('%s/shop/ec/basics/video/uploadpart?%s' % (self.BASE, qs), body,
                           {'Content-Type': 'multipart/form-data; boundary=' + boundary})
        j = json.loads(b)
        ec = j.get('errcode', 0)
        if ec not in (0, None):
            raise WxError(ec, j.get('errmsg', ''), 'video/uploadpart')
        return {'part_number': part_number, 'sha256': hashlib.sha256(bytes(data)).hexdigest(),
                'len': len(data)}

    def video_finishupload(self, key, parts, file_sha256):
        """③ 完成上传：校验分片号连续 + 整体 SHA 一致，再合并。"""
        if not key:
            raise WxError(-1, 'video_finishupload: 缺 video_upload_key', 'video/finishupload')
        nums = [p['part_number'] for p in (parts or [])]
        if not nums or sorted(nums) != list(range(1, len(nums) + 1)):
            raise WxError(-1, 'video_finishupload: 分片号不连续（%s）' % nums, 'video/finishupload')
        if not file_sha256 or len(file_sha256) != 64:
            raise WxError(-1, 'video_finishupload: 整体 sha256 非法', 'video/finishupload')
        return self.call('/shop/ec/basics/video/finishupload',
                         {'video_upload_key': key, 'part_count': len(nums),
                          'file_sha256': file_sha256})

    def video_getplayinfo(self, key, timeout=60, interval=2, _sleep=None):
        """④ 轮询播放信息：转码完成后取 video_url（临时地址，仅用于发品）。"""
        if not key:
            raise WxError(-1, 'video_getplayinfo: 缺 video_upload_key', 'video/getplayinfo')
        sleep = _sleep or (lambda s: None)  # Dry-run 注入，不真等
        waited = 0
        while waited <= timeout:
            j = self.call('/shop/ec/basics/video/getplayinfo', {'video_upload_key': key})
            url = j.get('video_url') or ''
            status = j.get('status')
            if url and status in ('done', 'finished', 1, None):
                assert_wx_video_url(url)
                return url
            if status == 'failed':
                raise WxError(-1, 'video_getplayinfo: 转码失败', 'video/getplayinfo')
            sleep(interval)
            waited += interval
        raise WxError(-1, 'video_getplayinfo: 轮询超时 %ds' % timeout, 'video/getplayinfo')

    # ---------------- 高层：本地文件 → 微信临时 video_url ----------------
    def upload_video_file(self, filepath):
        """把本地视频串走 4 步上传，返回 (video_url, meta)。

        返回的 video_url 标记 temp=True：调用方只可即时填进 head_videos，
        禁止写入任何缓存表/持久化素材库（对应官方「临时地址不可持久化」）。
        """
        if not os.path.isfile(filepath):
            raise WxError(-1, '本地视频文件不存在：' + filepath, 'upload_video_file')
        content = open(filepath, 'rb').read()
        file_sha = hashlib.sha256(content).hexdigest()
        key = self.video_initupload(os.path.basename(filepath), len(content))
        parts = []
        for i in range(0, len(content), PART_SIZE):
            chunk = content[i:i + PART_SIZE]
            parts.append(self.video_uploadpart(key, len(parts) + 1, chunk))
        self.video_finishupload(key, parts, file_sha)
        url = self.video_getplayinfo(key)
        return url, {'temp': True, 'file_sha256': file_sha,
                     'part_count': len(parts), 'video_upload_key': key,
                     'usage': '仅用于即时提交 product/add|update 的 head_videos，禁止持久化'}


def assert_wx_video_url(url):
    """第三方直链 / 空链接一律拒（等价于微信错误码 10020035）。"""
    if not url or WX_VIDEO_URL_PREFIX not in str(url):
        raise WxError(10020035,
                      '图片/视频 url 非法，url 前缀应为 %s；第三方临时直链必须先下载再走微信 4 步上传'
                      % WX_VIDEO_URL_PREFIX, 'head_videos')
    return True


def build_head_videos(video_url):
    """构造 product/add|update 的 head_videos 字段（Object，最多 1 个，仅 video_url）。

    删视频须显式传空 video_url，见调查报告 §2。
    """
    if video_url:
        assert_wx_video_url(video_url)
    return {'video_url': str(video_url or '')}
