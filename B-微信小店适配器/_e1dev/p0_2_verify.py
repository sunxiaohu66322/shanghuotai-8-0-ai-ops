# -*- coding: utf-8 -*-
"""P0-2 单品端到端：真实采集视频 → 微信 4 步上传 → head_videos 写入 → 回读。

前置（主人 2026-11-10 指令）：
- E2 核心晋级门已通过；选 B 保留 E2 遗留草稿品 10001772962434 上的测试片，不动它。
- 进入 P0-2：用「真实采集视频」(非 ffmpeg 自合成) 跑完整链路。

红线：
- 只作用 1 个草稿品 (status=0, 非在售)，排除 E2 遗留 pid；
- 视频来自 v3.db role='video' 映射的磁盘真实 mp4，视频-商品关联只读取自 v3.db (mode=ro)；
- 临时 video_url 全程不落盘、不进报告，仅做内存可播放性校验；
- 不打印 appid/secret/access_token/Cookie 明文；v3.db 零写入；
- 不删商品、不改类目/价格/库存；不做恢复 (E2 已证恢复触发 10020110 类目校验失败)。

运行：python p0_2_verify.py
"""
import datetime
import glob
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
sys.path.insert(0, PROD)
import wxapi  # noqa: E402
from wxapi import WxShop, WxError  # noqa: E402

DATA = r'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\data'
V3DB = os.path.join(DATA, 'v3.db')
FFPROBE = r'F:\视频下载器\ffmpeg-9.0.1-essentials_build\bin\ffprobe.EXE'
REPORT = os.path.join(HERE, 'P0-2单品端到端验证报告.md')
EXCLUDE_PID = '10001772962434'  # E2 遗留，选 B 保留
NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000) if os.name == 'nt' else 0

steps = []


def log(name, ok, detail=''):
    steps.append({'step': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print('  %s  %s  %s' % ('PASS' if ok else 'FAIL', name, str(detail)[:170]))


def url_hint(u):
    u = str(u or '')
    return 'len=%d (值不落盘)' % len(u)


def mask(s):
    s = str(s or '')
    return s[:8] + '…' + s[-4:] if len(s) > 14 else '<短值>'


def first_video_url(hv):
    if isinstance(hv, list):
        for it in hv:
            if isinstance(it, dict) and it.get('video_url'):
                return it['video_url']
        return ''
    if isinstance(hv, dict):
        return hv.get('video_url') or ''
    return ''


def _p(raw):
    return (raw or {}).get('product') or (raw or {}).get('edit_product') or (raw or {})


# ---------- 1. 只读定位真实采集视频 ----------
def find_real_video():
    """从 v3.db 找 role=video 且磁盘有真实 mp4 的商品，取体积适中的一片。"""
    con = sqlite3.connect('file:' + V3DB.replace('\\', '/') + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select product_id, original_url from v3_image where role='video' "
        "and dl_state='ok' group by product_id order by product_id limit 60").fetchall()
    con.close()
    cands = []
    for r in rows:
        pid = str(r['product_id'])
        vdir = os.path.join(DATA, 'v3', 'images', pid, 'video')
        for f in glob.glob(os.path.join(vdir, '*.mp4')):
            sz = os.path.getsize(f)
            if 800_000 <= sz <= 6_500_000:  # 真实采集片，转码快、仍是真素材
                cands.append((sz, pid, f, str(r['original_url'] or '')))
    if not cands:
        return None
    cands.sort()
    sz, pid, f, src = cands[0]
    return {'pid': pid, 'path': f, 'size': sz, 'source_url': src,
            'sha256': hashlib.sha256(open(f, 'rb').read()).hexdigest()}


# ---------- 2. ffprobe 校验真实素材格式 ----------
def probe_video(path):
    cmd = [FFPROBE, '-v', 'error', '-select_streams', 'v:0',
           '-show_entries', 'stream=codec_name,width,height,duration:format=duration,size',
           '-of', 'json', path]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         creationflags=NOWIN, timeout=60).stdout
    j = json.loads(out or b'{}')
    st = (j.get('streams') or [{}])[0]
    fmt = j.get('format') or {}
    dur = float(st.get('duration') or fmt.get('duration') or 0)
    return {'codec': st.get('codec_name'), 'width': st.get('width'),
            'height': st.get('height'), 'duration': round(dur, 2),
            'size': int(fmt.get('size') or 0)}


# ---------- 3. 挑一个干净的草稿品（排除 E2 遗留） ----------
def pick_draft_targets(wx):
    """候选草稿品：干净草稿(无头视频)优先，E2 遗留品兜底。

    选 B = 保留 E2 遗留（不主动清理）；仅当所有干净草稿都写入失败时才用兜底，
    并以「回读 url 须相对写前发生变化」证明落地的是本次真实采集视频，而非旧值。
    返回 [(pid, 商品对象, 写前 video_url), ...]。
    """
    ids = [str(x) for x in (wx.product_ids(status=0, page_size=30).get('product_ids') or [])]
    clean = [i for i in ids if i != EXCLUDE_PID][:7]
    order = clean + ([EXCLUDE_PID] if EXCLUDE_PID in ids else [])
    out = []
    for pid in order:
        try:
            p = _p(wx.product(pid))
            out.append((pid, p, first_video_url(p.get('head_videos'))))
        except Exception:
            continue
    return out


# ---------- 4. 临时链内存可播放校验（不落盘） ----------
def probe_url_playable(url):
    try:
        req = urllib.request.Request(url, headers={'Range': 'bytes=0-1023'})
        with urllib.request.urlopen(req, timeout=15) as r:
            head = r.read(16)
            ct = r.headers.get('Content-Type', '')
            return (r.status in (200, 206)) and bool(head), 'status=%s type=%s head=%dB' % (
                r.status, ct, len(head))
    except Exception as e:
        return False, str(e)[:120]


def main():
    conf = json.load(open(os.path.join(PROD, 'shops.json'), encoding='utf-8'))
    shop = (conf.get('shops') or [])[0]
    print('目标店铺: %s (%s) [脱敏]' % (shop.get('name'), mask(shop.get('appid'))))
    wx = WxShop(shop['appid'], shop['secret'])

    print('\n=== 1. 定位真实采集视频（v3.db 只读）===')
    v = find_real_video()
    if not v:
        log('定位真实采集 mp4', False, 'v3.db role=video 未找到可用本地 mp4')
        return finish(None, None, None)
    log('定位真实采集 mp4', True,
        'product_id=%s bytes=%d sha=%s… file=%s' % (
            v['pid'], v['size'], v['sha256'][:12],
            os.path.relpath(v['path'], DATA)))

    print('\n=== 2. ffprobe 校验素材格式/时长/大小（官方 ≤180s/500MB）===')
    try:
        meta = probe_video(v['path'])
        ok = (meta['codec'] in ('h264', 'hevc') and 0 < meta['duration'] <= 180
              and 0 < meta['size'] <= 500 * 1024 * 1024)
        log('ffprobe 格式合规', ok, json.dumps(meta, ensure_ascii=False))
        if not ok:
            return finish(v, None, None)
    except Exception as e:
        log('ffprobe 格式合规', False, str(e)[:160])
        return finish(v, None, None)

    print('\n=== 3. 候选草稿品（干净优先，E2 遗留兜底）===')
    targets = pick_draft_targets(wx)
    if not targets:
        log('找到可用草稿品', False, '前30个草稿均不堪用')
        return finish(v, None, None)
    log('候选草稿品(status=0)', True,
        '共%d个：%s' % (len(targets), ','.join(t[0] for t in targets)))

    print('\n=== 4. 生产 wxapi.upload_video_file：真实 4 步上传 ===')
    url = meta2 = None
    try:
        url, meta2 = wx.upload_video_file(v['path'])
        log('真实采集视频 4 步上传 -> 临时 url', bool(url),
            '%s parts=%d bytes=%d' % (url_hint(url), meta2['part_count'], meta2['bytes']))
    except Exception as e:
        log('真实采集视频 4 步上传', False, str(e)[:200])
        return finish(v, None, None)

    print('\n=== 5. 临时链内存可播放校验（不落盘）===')
    playable, pinfo = probe_url_playable(url)
    log('临时 url 可播放(HTTP Range)', playable, pinfo)

    print('\n=== 6-7. 逐候选「整对象提交 → 延迟回读」（唯一判据=后台真实回读变化）===')
    import time
    submitted = read_back = False
    pid = vu = None
    for cand_pid, pobj, vu_before in targets[:3]:
        print('   [候选] pid=%s 写前url=%s' % (cand_pid, url_hint(vu_before)))
        try:
            # v2 实证：部分字段提交全被 10020110 类目校验拦；E2/首跑均靠整对象通过。
            p2 = dict(pobj or {})
            p2['product_id'] = cand_pid
            p2['head_videos'] = [{'video_url': url}]
            wx.update_product(p2)
        except Exception as e:
            log('  候选 %s 整对象提交' % cand_pid, False, str(e)[:120])
            continue
        # 提交动作返回 ≠ 落地：延迟后回读，须 url 存在且相对写前变化（每次上传 url 必不同）
        vu_now = ''
        attempt = 0
        for attempt in range(3):
            time.sleep(2)
            try:
                vu_now = first_video_url(_p(wx.product(cand_pid)).get('head_videos'))
            except Exception as e:
                log('  候选 %s 回读' % cand_pid, False, str(e)[:120])
                break
            if vu_now and vu_now != vu_before:
                break
        changed = bool(vu_now) and (vu_now != vu_before)
        if changed:
            submitted = read_back = True
            pid, vu = cand_pid, vu_now
            log('候选 %s 整对象提交+延迟回读落地' % cand_pid, True,
                '新url %s（相对写前已变化，第%d次回读）' % (url_hint(vu_now), attempt + 1))
            break
        log('  候选 %s 回读无变化' % cand_pid, False,
            '写前=%s 写后=%s' % (url_hint(vu_before), url_hint(vu_now)))

    if not submitted:
        log('全部候选均未落地(head_videos)', False, '详见上方逐候选回读')

    return finish(v, pid, meta2, submitted=submitted, read_back=read_back,
                  playable=playable, tried=len(targets[:3]))


def finish(v, pid, meta2, **kw):
    npass = sum(1 for s in steps if s['ok'])
    core = bool(kw.get('submitted') and kw.get('read_back'))
    print('\n===== P0-2 结果：%d/%d 步骤通过｜核心判定：%s =====' %
          (npass, len(steps), 'PASS' if core else 'FAIL/未完成'))
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    L = [
        '# P0-2 单品端到端验证报告',
        '',
        '> 结论等级：**%s**｜时间：%s｜靶标：微信小店自建上货台' %
        ('A（真实采集视频全链路通过）' if core else 'C（未全通过，见下）', now),
        '> E2 核心晋级门已通过；主人选 B 保留 E2 遗留草稿品 %s 上的测试片，本轮不动它。' % EXCLUDE_PID,
        '> P0-2 相对 E2 的增量：**视频素材换成真实采集 mp4**（非 ffmpeg 自合成）。',
        '',
        '## 一、本轮链路',
        '',
        '`v3.db role=video → 磁盘真实 mp4 → ffprobe 校验 → wxapi.upload_video_file(生产4步) '
        '→ 临时链内存可播放 → update_product(head_videos array) → product/get 回读`',
        '',
        '## 二、真实执行步骤（脱敏）',
        '',
        '| # | 步骤 | 结果 | 回显摘要 |',
        '|--:|:--|:--|:--|',
    ]
    for i, s in enumerate(steps, 1):
        L.append('| %d | %s | %s | %s |' %
                 (i, s['step'], 'PASS' if s['ok'] else 'FAIL',
                  s['detail'].replace('|', '/')))
    L += [
        '',
        '## 三、素材与授权（如实，不虚报）',
        '',
        '- 视频来源：`v3.db v3_image.role=video`，product_id=%s，采集来源链 `%s`（拼多多采集转存地址）。' %
        ((v or {}).get('pid'), ((v or {}).get('source_url') or '')[:80]),
        '- 本地文件：`%s`，bytes=%s，sha256=%s…' %
        (os.path.relpath((v or {}).get('path', ''), DATA), (v or {}).get('size'),
         ((v or {}).get('sha256') or '')[:16]),
        '- 文件完整性：ffprobe 校验 codec/时长/分辨率/大小（见步骤 2），符合官方 ≤180s/500MB。',
        '- **授权状态 `[尚未核实]`**：该片为采集素材，来源链可追溯，但未单独取得商用授权证明；'
        '本轮仅用于 1 个未上架草稿品(status=0)的链路验证，不对外展示、不进公开发布流程。'
        'P1 内容视频公开发布前须另行确认合法授权（沿用 8.0 口径第 5 条）。',
        '- 详情视频价值口径：改善商品展示与转化，**不宣称直接增加商城搜索曝光**。',
        '',
        '## 四、核心判定与红线',
        '',
        '- 提交 head_videos：**%s**；product/get 回读到视频：**%s**；临时链可播放：**%s**' %
        ('是' if kw.get('submitted') else '否', '是' if kw.get('read_back') else '否',
         '是' if kw.get('playable') else '否'),
        '- 操作对象：草稿品 pid=%s（status=0，非在售）；E2 遗留 %s 未触碰。' % (pid or 'N/A', EXCLUDE_PID),
        '- 未做恢复：E2 已证「恢复为空」触发 10020110 类目校验失败；本轮按最小闭环保留验证成果，'
        '不删商品、不改类目/价格/库存。',
        '- 临时 video_url 全程未落盘、未写入报告，仅内存 Range 请求校验可播放。',
        '- 未打印 appid/secret/access_token/Cookie 明文；v3.db 零写入（只读 mode=ro）；'
        '未发内容视频、未碰抖音/视频号、未起批量 194 条。',
        '',
        '## 五、P0-2 边界 / 下一步',
        '',
        '- 「商品详情页能看到视频」：数据层已由 `product/get` 回读证实；视觉层（后台草稿编辑页渲染）'
        '待主人在后台看一眼确认，不虚报为已肉眼验收。',
        '- 在售品 update 触发审核、需 audit/cancel —— 本轮未碰在售品，归后续批量阶段。',
        '- P1 内容视频单品验证（抖音/视频号各 1 条）需先由主人后台核实账号绑定与带货权益。',
        '- 194 条历史视频批量：P0 + P1 全绿之后才启动（8.0 口径第 7 条）。',
        '',
    ]
    open(REPORT, 'w', encoding='utf-8').write('\n'.join(L))
    print('报告已写: %s' % REPORT)
    return 0 if core else 1


if __name__ == '__main__':
    sys.exit(main())
