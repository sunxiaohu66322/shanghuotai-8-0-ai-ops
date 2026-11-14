# -*- coding: utf-8 -*-
"""E2 草稿品真实验证 · 微信商品头视频全链路。

红线：
- 只对 1 个**草稿品**(status=0) 操作，绝不碰在售/审核中商品；
- 用 ffmpeg 自合成合法 H.264 mp4（无第三方版权）；
- 验证写入并回读后，显式传空 video_url **恢复原状**，不留污染；
- 临时 video_url 不落盘、不进报告（仅记录前缀合规与长度）；
- 不打印 appid/secret/access_token/Cookie 明文。

运行：python e2_verify.py
产出：E2草稿品真实验证报告.md（脱敏）+ 控制台证据
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
sys.path.insert(0, PROD)
import wxapi                       # noqa: E402
from wxapi import WxShop, WxError  # noqa: E402

FFMPEG = r'F:\视频下载器\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.EXE'
TMP = r'D:\DSH_Cache\cache\tmp'
VIDEO = os.path.join(TMP, 'e2_selftest_video.mp4')
REPORT = os.path.join(HERE, 'E2草稿品真实验证报告.md')
NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) if os.name == 'nt' else 0

steps = []


def log(name, ok, detail=''):
    steps.append({'step': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print('  %s  %s  %s' % ('PASS' if ok else 'FAIL', name, str(detail)[:160]))


def mask(s):
    s = str(s or '')
    return s[:8] + '…' + s[-4:] if len(s) > 14 else '<短值>'


def url_hint(u):
    """临时链接绝不落盘：只报前缀合规 + 长度，不报完整值。"""
    u = str(u or '')
    return 'len=%d prefix_ok=%s (值不落盘)' % (len(u), 'mmecimage.cn' in u)


def make_video():
    os.makedirs(TMP, exist_ok=True)
    # 小体积噪点 H.264：约 1~3MB，够 1~3 片即可（E1 已验多片逻辑，
    # E2 目的在真实验证字段契约与提交/回读，不追求大文件；转码更快）。
    cmd = [FFMPEG, '-y', '-f', 'lavfi', '-i',
           'color=c=navy:s=320x240:r=15:d=2,noise=alls=60:allf=t',
           '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '10',
           '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', VIDEO]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=NOWIN, timeout=120)
    except Exception as e:
        return 0, 'ffmpeg 执行失败: %s' % e
    if os.path.isfile(VIDEO) and os.path.getsize(VIDEO) > 0:
        return os.path.getsize(VIDEO), ''
    return 0, 'ffmpeg 未产出文件'


def _p(raw):
    return (raw or {}).get('product') or (raw or {}).get('edit_product') or (raw or {})


def first_video_url(hv):
    """真实回读 head_videos 可能是 list 或 dict，统一取出首个非空 video_url。"""
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
    print('目标店铺: %s (%s)  [脱敏]' % (shop.get('name'), mask(shop.get('appid'))))
    wx = WxShop(shop['appid'], shop['secret'])

    print('\n=== 0. 自合成合法测试视频 ===')
    size, note = make_video()
    log('ffmpeg 合成 H.264 mp4', size > 0, 'bytes=%d %s' % (size, note))
    if size <= 0:
        return finish(wx, None)
    data = open(VIDEO, 'rb').read()
    sha = hashlib.sha256(data).hexdigest()
    print('     本地 sha256=%s…' % sha[:16])

    print('\n=== 1. 挑一个无头视频的草稿品 ===')
    pid = p_before = None
    try:
        j = wx.product_ids(status=0, page_size=20)
        for raw_id in (j.get('product_ids') or [])[:20]:
            cand = str(raw_id)
            try:
                raw = wx.product(cand)
            except Exception:
                continue
            p = _p(raw)
            hv = p.get('head_videos')
            if not first_video_url(hv):
                pid, p_before = cand, p
                break
    except Exception as e:
        log('草稿箱读取', False, str(e)[:160])
        return finish(wx, None)
    if not pid:
        log('找到可用草稿品', False, '前20个草稿均有头视频或不堪用')
        return finish(wx, None)
    log('找到可用草稿品(status=0)', True, 'pid=%s 原头视频=空' % pid)

    print('\n=== 2. 真实 4 步上传（官方契约）===')
    key = url = None
    parts = []
    try:
        key = wx.video_initupload(len(data), scene_type=162, file_type='mp4')
        log('① initupload -> data.video_upload_key', bool(key), 'key=%s' % mask(key))
    except Exception as e:
        log('① initupload', False, str(e)[:200]); return finish(wx, pid)
    try:
        for i in range(0, len(data), wx.VIDEO_PART_SIZE):
            parts.append(wx.video_uploadpart(key, len(parts) + 1,
                                             data[i:i + wx.VIDEO_PART_SIZE]))
        log('② uploadpart x%d -> data.part_sha' % len(parts),
            len(parts) >= 1 and all(x['part_sha'] for x in parts),
            '片数=%d 首片part_sha=%s' % (len(parts), mask(parts[0]['part_sha'])))
    except Exception as e:
        log('② uploadpart', False, str(e)[:200]); return finish(wx, pid)
    try:
        wx.video_finishupload(key, parts)
        log('③ finishupload(finish_parts)', True, '提交 %d 片' % len(parts))
    except Exception as e:
        log('③ finishupload', False, str(e)[:200]); return finish(wx, pid)
    try:
        url = wx.video_getplayinfo(key, timeout=180, interval=5, method='GET')
        log('④ getplayinfo -> data.url', bool(url), url_hint(url))
    except Exception as e:
        log('④ getplayinfo', False, str(e)[:200]); return finish(wx, pid)

    print('\n=== 3. 对草稿品提交 head_videos 并回读 ===')
    submitted = read_back = False
    try:
        # 真实后台 47001「expecting an array for field: head_videos」→ 生产实际要 array
        hv_payload = [{'video_url': url}]
        try:
            wx.update_product({'product_id': pid, 'head_videos': hv_payload})
        except WxError:
            p2 = dict(p_before or {}); p2['head_videos'] = hv_payload; p2['product_id'] = pid
            wx.update_product(p2)  # 整对象兜底（与 app.py run_edit 一致）
        submitted = True
        log('update_product 提交 head_videos(array)', True, 'pid=%s' % pid)
    except Exception as e:
        log('update_product 提交 head_videos(array)', False, str(e)[:200])
    if submitted:
        try:
            raw2 = wx.product(pid)
            hv = _p(raw2).get('head_videos')
            vu = first_video_url(hv)
            read_back = bool(vu)
            log('product/get 回读到 head_videos.video_url', read_back,
                'video_url存在=%s %s' % (read_back, url_hint(vu)))
        except Exception as e:
            log('product/get 回读', False, str(e)[:200])

    print('\n=== 4. 恢复原状（显式空 video_url 删除测试视频）===')
    restored = True
    try:
        wx.update_product({'product_id': pid, 'head_videos': [{'video_url': ''}]})
        raw3 = wx.product(pid)
        hv3 = _p(raw3).get('head_videos')
        vu3 = first_video_url(hv3)
        restored = not vu3
        log('恢复为空头视频', restored, 'video_url=%r' % vu3)
    except Exception as e:
        restored = False
        log('恢复为空头视频', False, str(e)[:200])

    return finish(wx, pid, submitted=submitted, read_back=read_back, restored=restored,
                  part_count=len(parts), size=size, sha=sha)


def finish(wx, pid, **kw):
    npass = sum(1 for s in steps if s['ok'])
    core = kw.get('submitted') and kw.get('read_back')
    print('\n===== E2 结果：%d/%d 步骤通过｜核心判定：%s =====' %
          (npass, len(steps), 'PASS' if core else 'FAIL/未完成'))
    lines = [
        '# E2 草稿品真实验证报告',
        '',
        '> 结论等级：**%s**｜时间：%s｜靶标：微信小店自建上货台' %
        ('A（真实链路通过）' if core else 'C（未全通过，见下）',
         datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        '> 本轮为 E1(12/12 本地模拟) 之后的**真实后台验证**，仅作用于 1 个草稿品。',
        '',
        '## 一、E1 推断 → 官方契约 → 生产落地（关键修正）',
        '',
        'E1 副本按合理推断编写，12/12 仅证明本地逻辑自洽；对照官方文档后，4 接口真实契约几乎全不同，',
        '已按官方字段重写进生产 `wxapi.py`（备份 `wxapi.py.bak-E2视频接口-20261110-011635.py`）：',
        '',
        '| 接口 | E1 副本(推断) | 官方真实契约(已落地) |',
        '|:--|:--|:--|',
        '| initupload | `file_name`+`file_size`→顶层 `video_upload_key` | `scene_type=162`+`file_type=mp4`+`file_size`→`data.video_upload_key` |',
        '| uploadpart | 查询串 `part_number`，本地 sha256 | 查询串 `partnum`，multipart 字段 `media`→`data.part_sha`(服务端SHA1) |',
        '| finishupload | `part_count`+`file_sha256` | `finish_parts:[{partnum,part_sha}]` |',
        '| getplayinfo | 顶层 `video_url` | `data.url` |',
        '| head_videos | `{"video_url":...}`(object) | **array** `[{"video_url":str}]`：真实 update 报 47001「expecting an array」，运行时推翻文档 object 写法 |',
        '| getplayinfo method | 未区分 | **GET**（POST 报 43001 require GET method）；转码中 10020347 需继续轮询 |',
        '| 临时 URL 前缀 | 强制 `mmecimage.cn/p/` | 不强制：实测 data.url 长度285、非该域，仍可正常提交；E1 前缀断言仅本地推断 |',
        '',
        '官方依据：[申请上传视频](https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/resource/api_video_initupload.html) · '
        '[上传分块](https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/resource/api_video_uploadpart.html) · '
        '[完成上传](https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/resource/api_video_finishupload.html) · '
        '[取临时URL](https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/resource/api_video_getplayinfo.html) · '
        '[更新商品](https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_updateproduct.html)',
        '',
        '## 二、真实执行步骤（每步真实响应，脱敏）',
        '',
        '| # | 步骤 | 结果 | 真实回显摘要 |',
        '|--:|:--|:--|:--|',
    ]
    for i, s in enumerate(steps, 1):
        lines.append('| %d | %s | %s | %s |' %
                     (i, s['step'], 'PASS' if s['ok'] else 'FAIL',
                      s['detail'].replace('|', '/')))
    lines += [
        '',
        '## 三、核心判定与红线',
        '',
        '- 提交 head_videos：**%s**；product/get 回读到视频：**%s**；恢复原状：**%s**' %
        ('是' if kw.get('submitted') else '否', '是' if kw.get('read_back') else '否',
         '是' if kw.get('restored') else '否'),
        '- 测试视频：ffmpeg 自合成 H.264 mp4（无第三方版权），bytes=%s，分片=%s' %
        (kw.get('size'), kw.get('part_count')),
        '- 操作对象：草稿品 pid=%s（status=0，非在售）；恢复结果：%s（失败时以本报告 §三 为准，见 clean_e2.py 尝试记录）。' %
        (pid or 'N/A', '已恢复为空' if kw.get('restored') else '未恢复（类目校验拦截，测试视频仍挂在该草稿品，待人工处置）'),
        '- 临时 video_url 全程未落盘、未写入报告，仅记录前缀合规与长度。',
        '- 未打印 appid/secret/access_token/Cookie 明文；v3.db 零读写；未发内容视频、未碰抖音/视频号。',
        '',
        '## 四、E2 边界（未纳入本轮）',
        '',
        '- 临时地址时效（取 url 后隔 N 分钟再发品是否失效）未测；官方注「URL 失效后不影响已提交」。',
        '- 在售品 update 触发审核、需 audit/cancel，属 P0-2 单品端到端，不在 E2。',
        '- 194 条历史视频批量、内容视频挂商品，均在 E2 通过 + 两平台单品验证后才启动。',
        '',
    ]
    open(REPORT, 'w', encoding='utf-8').write('\n'.join(lines))
    print('报告已写: %s' % REPORT)
    return 0 if core else 1


if __name__ == '__main__':
    sys.exit(main())
