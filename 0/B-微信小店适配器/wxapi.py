# -*- coding: utf-8 -*-
"""微信小店 OpenAPI 客户端（纯标准库）。

所有路径均在本机实测通过（2026-10-26）：
  /cgi-bin/stable_token                       取 access_token（缓存到过期前 300 秒）
  /channels/ec/basics/info/get                店铺基础信息
  /channels/ec/product/list/get               商品 ID 列表（status: 0 草稿 / 5 上架 / 11 回收站）
  /channels/ec/product/get                    商品详情（data_type=2 才能读未上架）
  /channels/ec/product/add                    新建商品
  /channels/ec/product/update                 更新商品
  /channels/ec/product/listing|delisting      上架 / 下架
  /channels/ec/product/delete                 删除商品
  /channels/ec/product/stock/update           快速改库存
  /shop/ec/category/all                       全量类目树（含名称与资质要求）
  /shop/ec/category/detail                    类目详情（必填属性、销售属性、保证金）
  /shop/ec/basics/img/upload                  图片上传（multipart，字段名 media）
  /channels/ec/merchant/getfreighttemplatelist 运费模板列表
  /channels/ec/coupon/get_list                优惠券列表
  /channels/ec/order/list/get                 订单列表（必须带 create_time_range）
"""
import json, os, time, ssl, uuid, threading, urllib.request, urllib.parse, urllib.error

BASE = 'https://api.weixin.qq.com'
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, '.cache')


class WxError(Exception):
    def __init__(self, errcode, errmsg, path=''):
        super().__init__('[%s] %s (%s)' % (errcode, errmsg, path))
        self.errcode, self.errmsg, self.path = errcode, errmsg, path


def _ensure(p):
    os.makedirs(p, exist_ok=True)


class WxShop(object):
    def __init__(self, appid, secret):
        self.appid, self.secret = appid, secret
        self._token = None
        self._exp = 0
        self._lock = threading.Lock()
        _ensure(CACHE)
        self._cachefile = os.path.join(CACHE, 'token_%s.json' % appid)

    # ---------------- 底层 ----------------
    def _http(self, url, data=None, headers=None, timeout=30):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            raise WxError(-1, 'network: %s' % e, url)

    def token(self, force=False):
        with self._lock:
            if not force and self._token and time.time() < self._exp:
                return self._token
            # 磁盘缓存（跨进程复用，避免频繁刷新）
            if not force and os.path.exists(self._cachefile):
                try:
                    d = json.load(open(self._cachefile, encoding='utf-8'))
                    if d.get('access_token') and d.get('exp', 0) > time.time() + 60:
                        self._token, self._exp = d['access_token'], d['exp']
                        return self._token
                except Exception:
                    pass
            body = json.dumps({'grant_type': 'client_credential', 'appid': self.appid,
                               'secret': self.secret}).encode()
            st, b = self._http(BASE + '/cgi-bin/stable_token', body,
                               {'Content-Type': 'application/json'})
            j = json.loads(b)
            if not j.get('access_token'):
                raise WxError(j.get('errcode'), j.get('errmsg', 'token 获取失败'), 'stable_token')
            self._token = j['access_token']
            self._exp = time.time() + int(j.get('expires_in', 7200)) - 300
            json.dump({'access_token': self._token, 'exp': self._exp},
                      open(self._cachefile, 'w', encoding='utf-8'))
            return self._token

    def call(self, path, body=None, method='POST', query=None, retry=True, raw_bytes=False):
        q = {'access_token': self.token()}
        if query:
            q.update(query)
        url = BASE + path + '?' + urllib.parse.urlencode(q)
        data = None
        headers = {}
        if method == 'POST':
            data = json.dumps(body or {}, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        st, b = self._http(url, data, headers)
        if raw_bytes:
            return b
        try:
            j = json.loads(b)
        except Exception:
            raise WxError(-1, 'non-json: %s' % b[:200], path)
        ec = j.get('errcode', 0)
        # token 失效自动重取一次
        if ec in (40001, 42001, 40014) and retry:
            self.token(force=True)
            return self.call(path, body, method, query, retry=False, raw_bytes=raw_bytes)
        if ec not in (0, None):
            raise WxError(ec, j.get('errmsg', ''), path)
        return j

    # ---------------- 店铺 ----------------
    def shop_info(self):
        return self.call('/channels/ec/basics/info/get', method='GET')

    # ---------------- 商品 ----------------
    def product_ids(self, status=None, page_size=30, next_key=''):
        body = {'page_size': page_size}
        if status is not None and status != '':
            body['status'] = int(status)
        if next_key:
            body['next_key'] = next_key
        return self.call('/channels/ec/product/list/get', body)

    def product(self, pid):
        j = self.call('/channels/ec/product/get', {'product_id': str(pid), 'data_type': 2})
        return j

    def add_product(self, payload):
        return self.call('/channels/ec/product/add', payload)

    def update_product(self, payload):
        return self.call('/channels/ec/product/update', payload)

    def listing(self, pid):
        return self.call('/channels/ec/product/listing', {'product_id': str(pid)})

    def delisting(self, pid):
        return self.call('/channels/ec/product/delisting', {'product_id': str(pid)})

    def cancel_audit(self, pid):
        """撤回审核。上架后商品进「审核中」，此时 delisting/delete/update 全部报
        10020047「商品正在审核中」，必须先撤审才能继续操作。"""
        return self.call('/channels/ec/product/audit/cancel', {'product_id': str(pid)})

    def delete_product(self, pid):
        return self.call('/channels/ec/product/delete', {'product_id': str(pid)})

    def stock_update(self, pid, sku_id, diff_type=1, num=0):
        """diff_type: 1=全量覆盖 2=增量 3=减量（按官方快速更新库存口径）"""
        return self.call('/channels/ec/product/stock/update',
                         {'product_id': str(pid), 'sku_id': str(sku_id),
                          'diff_type': diff_type, 'num': int(num)})

    # ---------------- 类目 ----------------
    def categories(self, use_cache=True, ttl=86400):
        f = os.path.join(CACHE, 'cats.json')
        if use_cache and os.path.exists(f) and time.time() - os.path.getmtime(f) < ttl:
            return json.load(open(f, encoding='utf-8'))
        j = self.call('/shop/ec/category/all', method='GET')
        json.dump(j, open(f, 'w', encoding='utf-8'), ensure_ascii=False)
        return j

    def category_detail(self, cat_id):
        return self.call('/shop/ec/category/detail', {'cat_id': str(cat_id)})

    # ---------------- 媒体 ----------------
    @staticmethod
    def img_size(data):
        """从字节流解析图片宽高（纯标准库，覆盖 PNG/JPEG/GIF/BMP）。返回 (w,h) 或 (0,0)。
        注意：/shop/ec/basics/img/upload 必须带真实宽高，缺省或 0 一律报 10020055 参数有误。"""
        import struct
        try:
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                w, h = struct.unpack('>II', data[16:24])
                return w, h
            if data[:6] in (b'GIF87a', b'GIF89a'):
                w, h = struct.unpack('<HH', data[6:10])
                return w, h
            if data[:2] == b'BM' and len(data) >= 26:
                w, h = struct.unpack('<ii', data[18:26])
                return abs(w), abs(h)
            if data[:2] == b'\xff\xd8':  # JPEG：扫 SOF marker
                i, n = 2, len(data)
                while i < n - 9:
                    if data[i] != 0xFF:
                        i += 1
                        continue
                    m = data[i + 1]
                    if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        h, w = struct.unpack('>HH', data[i + 5:i + 9])
                        return w, h
                    if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                        i += 2
                        continue
                    seg = struct.unpack('>H', data[i + 2:i + 4])[0] if i + 4 <= n else 0
                    i += 2 + seg
        except Exception:
            pass
        return 0, 0

    def upload_image(self, filename, content, width=0, height=0):
        """multipart 上传（字段名 media），返回 img_url。宽高缺省时自动解析。"""
        boundary = '----wxb%s' % uuid.uuid4().hex
        low = filename.lower()
        mime = 'image/png' if low.endswith('.png') else ('image/gif' if low.endswith('.gif') else 'image/jpeg')
        if not width or not height:
            width, height = self.img_size(content)
        if not width or not height:
            raise WxError(-3, '无法识别图片尺寸（%s）：接口必须带真实宽高' % filename, 'img/upload')
        body = b''.join([
            ('--%s\r\nContent-Disposition: form-data; name="media"; filename="%s"\r\n'
             'Content-Type: %s\r\n\r\n' % (boundary, filename, mime)).encode(),
            content, b'\r\n', ('--%s--\r\n' % boundary).encode()])
        q = {'access_token': self.token(), 'upload_type': 0, 'resp_type': 1,
             'width': width, 'height': height}
        url = BASE + '/shop/ec/basics/img/upload?' + urllib.parse.urlencode(q)
        st, b = self._http(url, body,
                           {'Content-Type': 'multipart/form-data; boundary=' + boundary}, timeout=60)
        j = json.loads(b)
        if j.get('errcode'):
            raise WxError(j.get('errcode'), j.get('errmsg', ''), 'img/upload')
        return (j.get('pic_file') or {}).get('img_url')

    # ---------------- 商品视频（官方 4 步；scene_type=162 商品视频，≤180s/500MB）----------------
    # 依据：微信开放文档「通用-资源管理」4 篇（api_video_initupload/uploadpart/
    # finishupload/getplayinfo）。与 _e1dev 副本不同，真实契约为：
    #   initupload   传 scene_type/file_type/file_size，读 data.video_upload_key
    #   uploadpart   查询串 partnum（非 part_number）、multipart 字段 media、
    #                读 data.part_sha（服务端 SHA1，本地不自算）
    #   finishupload 传 finish_parts=[{partnum, part_sha}]（非 part_count+file_sha256）
    #   getplayinfo  读 data.url（非顶层 video_url）
    # 拿到的 url 是临时地址：仅可即时填 product/add|update 的 head_videos.video_url，
    # 禁止持久化；URL 失效后不影响已提交的商品。
    VIDEO_PART_SIZE = 1024 * 1024  # 官方：除最后一片外每片 ≥1M，支持 1M~2MB
    # getplayinfo 真实回包：转码未完成时返回 10020347「视频还在转码中」，
    # 属可重试状态（实测 2026-11-10 E2），非硬错误。
    VIDEO_TRANSCODING_CODES = (10020347,)

    def video_initupload(self, file_size, scene_type=162, file_type='mp4'):
        """① 申请上传视频，返回 data.video_upload_key。"""
        j = self.call('/shop/ec/basics/video/initupload',
                      {'scene_type': int(scene_type), 'file_type': str(file_type),
                       'file_size': int(file_size)})
        key = (j.get('data') or {}).get('video_upload_key') or j.get('video_upload_key')
        if not key:
            raise WxError(-1, 'initupload 响应缺 video_upload_key', 'video/initupload')
        return key

    def video_uploadpart(self, key, partnum, chunk):
        """② 上传一片：查询串 video_upload_key+partnum，multipart 字段名 media。
        返回服务端计算的 data.part_sha，finishupload 原样传回。"""
        boundary = '----wxv%s' % uuid.uuid4().hex
        body = b''.join([
            ('--%s\r\nContent-Disposition: form-data; name="media"; filename="part_%d"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % (boundary, partnum)).encode(),
            bytes(chunk), b'\r\n', ('--%s--\r\n' % boundary).encode()])
        q = {'access_token': self.token(), 'video_upload_key': key, 'partnum': int(partnum)}
        url = BASE + '/shop/ec/basics/video/uploadpart?' + urllib.parse.urlencode(q)
        st, b = self._http(url, body,
                           {'Content-Type': 'multipart/form-data; boundary=' + boundary}, timeout=60)
        j = json.loads(b)
        if j.get('errcode'):
            raise WxError(j.get('errcode'), j.get('errmsg', ''), 'video/uploadpart')
        sha = (j.get('data') or {}).get('part_sha') or j.get('part_sha')
        if not sha:
            raise WxError(-1, 'uploadpart 响应缺 part_sha', 'video/uploadpart')
        return {'partnum': int(partnum), 'part_sha': sha, 'len': len(chunk)}

    def video_finishupload(self, key, parts):
        """③ 完成上传：parts=[{partnum, part_sha}, ...]，按块号升序且连续。"""
        finish_parts = [{'partnum': int(p['partnum']), 'part_sha': p['part_sha']} for p in parts]
        return self.call('/shop/ec/basics/video/finishupload',
                         {'video_upload_key': key, 'finish_parts': finish_parts})

    def video_getplayinfo(self, key, timeout=180, interval=5, _sleep=None, method='GET'):
        """④ 轮询取临时 URL（data.url）。本接口为 GET（实测 POST 报 43001
        require GET method）；转码未完成时 errcode=10020347 或无 url，继续轮询。"""
        sleep = _sleep or time.sleep
        waited, last = 0, {}
        while waited <= timeout:
            q = {'access_token': self.token(), 'video_upload_key': key}
            url = BASE + '/shop/ec/basics/video/getplayinfo?' + urllib.parse.urlencode(q)
            if method == 'GET':
                st, b = self._http(url, None, {})
            else:
                st, b = self._http(url, b'{}', {'Content-Type': 'application/json'})
            try:
                j = json.loads(b)
            except Exception:
                raise WxError(-1, 'getplayinfo 非 JSON: %s' % b[:200], 'video/getplayinfo')
            last = j
            ec = j.get('errcode', 0)
            u = (j.get('data') or {}).get('url') or j.get('url') or j.get('video_url')
            if u and ec in (0, None):
                return u
            if ec in (40001, 42001, 40014):   # token 失效：重取后继续
                self.token(force=True)
                sleep(interval); waited += interval
                continue
            if ec in (0, None) or ec in self.VIDEO_TRANSCODING_CODES:
                # errcode=0 无 url，或明确「转码中」→ 继续等待
                sleep(interval); waited += interval
                continue
            raise WxError(ec, j.get('errmsg', ''), 'video/getplayinfo')  # 其它明确错误立即抛
        raise WxError(last.get('errcode', -1),
                      'getplayinfo 轮询超时/转码未完成(等待%ds): %s' % (waited, last.get('errmsg', '')),
                      'video/getplayinfo')

    def upload_video_file(self, filepath, _sleep=None):
        """本地 mp4 → 微信临时 video_url：串 4 步，返回 (url, meta)。
        url 只可即时填 head_videos.video_url，禁止持久化。"""
        if not os.path.isfile(filepath):
            raise WxError(-1, '本地视频不存在：%s' % filepath, 'upload_video_file')
        content = open(filepath, 'rb').read()
        key = self.video_initupload(len(content))
        parts = []
        for i in range(0, len(content), self.VIDEO_PART_SIZE):
            parts.append(self.video_uploadpart(key, len(parts) + 1,
                                               content[i:i + self.VIDEO_PART_SIZE]))
        self.video_finishupload(key, parts)
        url = self.video_getplayinfo(key, _sleep=_sleep)
        return url, {'temp': True, 'video_upload_key': key, 'part_count': len(parts),
                     'bytes': len(content),
                     'usage': '仅用于即时提交 head_videos.video_url，禁止持久化'}

    # ---------------- 营销 / 物流 ----------------
    def freight_templates(self):
        return self.call('/channels/ec/merchant/getfreighttemplatelist', {'limit': 20, 'offset': 0})

    def coupons(self, page=0, size=20):
        return self.call('/channels/ec/coupon/get_list', {'page_index': page, 'page_size': size})

    # ---------------- 订单 ----------------
    def orders(self, start_ts, end_ts, page_size=20, next_key=''):
        body = {'create_time_range': {'start_time': int(start_ts), 'end_time': int(end_ts)},
                'page_size': page_size}
        if next_key:
            body['next_key'] = next_key
        return self.call('/channels/ec/order/list/get', body)
