# -*- coding: utf-8 -*-
"""图片编辑子系统（6.0）：照片墙 / 框选修补（LaMa·TELEA·NS）/ 批量应用 / 候选检测。

坐标约定：前端传的 rects 一律是**归一化**的 {x,y,w,h}（0~1），
          服务端按每张图自己的尺寸换算 —— 这样一次框选可以套用到整组图。
输出统一落在 data/photos/out/ 下，原图永不改动（除非显式 mode=inplace）。
"""
import hashlib
import io
import os
import sys
import time

import numpy as np

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT_DIR, 'data')
OUT_DIR = os.path.join(DATA, 'photos', 'out')
UP_DIR = os.path.join(DATA, 'photos', 'up')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(UP_DIR, exist_ok=True)

WS = r'G:\Users\Administrator\Documents\deepseek-harness\default-workspace'
ROOTS = [
    ('上货台素材', os.path.join(WS, '上货台', 'data')),
    ('采集库', os.path.join(WS, 'taobao-picker', 'data')),
    ('本台上货', os.path.join(DATA, 'photos')),
]
EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')

# ---- 复用 taobao-picker 的 LaMa 实现（同一份模型，不复制代码）----
_V3 = os.path.join(WS, 'taobao-picker', 'server')
if _V3 not in sys.path:
    sys.path.insert(0, os.path.dirname(_V3))
_LAMA = None
_LAMA_ERR = ''


def lama():
    global _LAMA, _LAMA_ERR
    if _LAMA is not None:
        return _LAMA
    try:
        from server.v3 import inpaint_lama as m
        _LAMA = m
        return m
    except Exception as e:                                   # pragma: no cover
        _LAMA_ERR = '%s: %s' % (type(e).__name__, e)
        return None


def roots():
    out = []
    for i, (name, path) in enumerate(ROOTS):
        ok = os.path.isdir(path)
        n = 0
        if ok:
            try:
                n = sum(1 for _ in os.scandir(path))
            except Exception:
                n = 0
        out.append({'idx': i, 'name': name, 'path': path, 'exists': ok, 'items': n})
    return out


def _root(idx):
    i = int(idx or 0)
    if i < 0 or i >= len(ROOTS):
        raise ValueError('root 越界')
    p = ROOTS[i][1]
    if not os.path.isdir(p):
        raise ValueError('目录不存在：%s' % p)
    return p


def _safe(root_path, rel):
    rel = (rel or '').replace('\\', '/').strip('/')
    if '..' in rel.split('/'):
        raise ValueError('非法路径')
    fp = os.path.normpath(os.path.join(root_path, rel))
    if not fp.startswith(os.path.normpath(root_path)):
        raise ValueError('越界')
    return fp


def list_images(idx, rel='', page=0, size=60):
    rp = _root(idx)
    start = os.path.join(rp, rel) if rel else rp
    items = []
    if os.path.isdir(start):
        stack = [start]
        while stack and len(items) < 6000:
            cur = stack.pop()
            try:
                for e in os.scandir(cur):
                    if e.is_dir():
                        stack.append(e.path)
                    elif e.name.lower().endswith(EXTS):
                        items.append((e.path, e.stat()))
            except Exception:
                continue
    items.sort(key=lambda x: -x[1].st_mtime)
    total = len(items)
    page = max(0, int(page))
    size = max(1, min(200, int(size)))
    chunk = items[page * size:(page + 1) * size]
    rows = []
    for fp, st in chunk:
        relp = os.path.relpath(fp, rp).replace('\\', '/')
        rows.append({'id': relp, 'name': os.path.basename(fp), 'size': st.st_size,
                     'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))})
    return {'ok': True, 'total': total, 'page': page, 'size': size, 'rows': rows}


def read_file(idx, rel, max_w=0):
    rp = _root(idx)
    fp = _safe(rp, rel)
    if not os.path.isfile(fp):
        raise ValueError('文件不存在')
    raw = open(fp, 'rb').read()
    if not max_w:
        return raw
    import cv2
    im = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return raw
    h, w = im.shape[:2]
    mw = int(max_w)
    if w > mw:
        nh = max(1, int(h * mw / w))
        im = cv2.resize(im, (mw, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', im, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return buf.tobytes() if ok else raw


def _load_bgr(fp):
    import cv2
    im = cv2.imdecode(np.fromfile(fp, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError('图片解码失败：%s' % os.path.basename(fp))
    return im


def build_mask(shape, rects, pad=0):
    """归一化矩形 → 二值 mask；pad 为像素外扩（遮住边缘留白）。"""
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for r in rects or []:
        x = float(r.get('x', 0)) * w
        y = float(r.get('y', 0)) * h
        rw = float(r.get('w', 0)) * w
        rh = float(r.get('h', 0)) * h
        x0 = int(max(0, x - pad)); y0 = int(max(0, y - pad))
        x1 = int(min(w, x + rw + pad)); y1 = int(min(h, y + rh + pad))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
    return mask


def _engine_apply(bgr, mask, engine='lama'):
    import cv2
    engine = (engine or 'lama').lower()
    if engine == 'lama':
        m = lama()
        if m is None:
            raise ValueError('LaMa 不可用：%s' % _LAMA_ERR)
        return m.inpaint(bgr, mask), 'lama'
    method = cv2.INPAINT_NS if engine == 'ns' else cv2.INPAINT_TELEA
    return cv2.inpaint(bgr, mask, 3, method), engine


def inpaint_one(idx, rel, rects, engine='lama', pad=0, inplace=False):
    rp = _root(idx)
    fp = _safe(rp, rel)
    bgr = _load_bgr(fp)
    h, w = bgr.shape[:2]
    mask = build_mask(bgr.shape, rects, int(pad or 0))
    if not mask.any():
        raise ValueError('没有框选区域')
    t0 = time.time()
    out, used = _engine_apply(bgr, mask, engine)
    ms = int((time.time() - t0) * 1000)
    import cv2
    if inplace:
        ok, buf = cv2.imencode(os.path.splitext(fp)[1] or '.jpg', out,
                               [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        open(fp, 'wb').write(buf.tobytes())
        return {'ok': True, 'id': rel, 'out': '/api/photo/file?root=%d&id=%s&v=%d' % (idx, rel, int(time.time())),
                'ms': ms, 'engine': used, 'inplace': True}
    key = hashlib.sha1(('%s|%s|%s|%s' % (rel, rects, engine, time.time())).encode()).hexdigest()[:12]
    name = '%s-%s.jpg' % (key, used)
    outp = os.path.join(OUT_DIR, name)
    ok, buf = cv2.imencode('.jpg', out, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    open(outp, 'wb').write(buf.tobytes())
    return {'ok': True, 'id': rel, 'out': '/api/photo/out?f=' + name, 'ms': ms,
            'engine': used, 'size': [w, h], 'boxes': len(rects or [])}


def batch(idx, ids, rects, engine='lama', pad=0):
    res, okc, fail = [], 0, 0
    for rel in ids or []:
        try:
            r = inpaint_one(idx, rel, rects, engine, pad)
            okc += 1
            res.append(r)
        except Exception as e:
            fail += 1
            res.append({'ok': False, 'id': rel, 'error': str(e)})
    return {'ok': True, 'total': len(ids or []), 'okCount': okc, 'failCount': fail, 'rows': res}


def detect(idx, rel):
    """候选框检测：高梯度 + 形态学膨胀 + 轮廓合并，只输出「像文字/角标」的矩形（归一化）。"""
    import cv2
    rp = _root(idx)
    fp = _safe(rp, rel)
    bgr = _load_bgr(fp)
    h, w = bgr.shape[:2]
    scale = 900.0 / max(w, h) if max(w, h) > 900 else 1.0
    small = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale != 1.0 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hs, ws = small.shape[:2]
    boxes = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        ar = bw / float(bh or 1)
        area = bw * bh / float(hs * ws)
        if bh < 8 or bw < 12:
            continue
        if area < 0.0004 or area > 0.20:
            continue
        if ar < 1.2 or ar > 30:          # 文字/角标多横长
            continue
        fill = cv2.contourArea(c) / float(max(1, bw * bh))
        if fill < 0.12:
            continue
        boxes.append([x, y, bw, bh])
    # 合并重叠/相邻
    boxes.sort(key=lambda b: -b[2] * b[3])
    merged = []
    for b in boxes:
        x, y, bw, bh = b
        hit = False
        for m in merged:
            mx, my, mw2, mh2 = m
            if not (x > mx + mw2 or x + bw < mx or y > my + mh2 or y + bh < my):
                m[0] = min(mx, x); m[1] = min(my, y)
                m[2] = max(mx + mw2, x + bw) - m[0]; m[3] = max(my + mh2, y + bh) - m[1]
                hit = True
                break
        if not hit:
            merged.append(b[:])
    merged.sort(key=lambda b: -b[2] * b[3])
    merged = merged[:24]
    out = [{'x': b[0] / float(ws), 'y': b[1] / float(hs),
            'w': b[2] / float(ws), 'h': b[3] / float(hs)} for b in merged]
    return {'ok': True, 'id': rel, 'count': len(out), 'rects': out,
            'note': '候选框（高对比区域），务必人工确认后再消除'}


def status():
    m = lama()
    info = {}
    if m is not None:
        try:
            info = m.available()
        except Exception as e:
            info = {'error': str(e)}
    return {'ok': True, 'lama': m is not None, 'lama_err': _LAMA_ERR,
            'model': info, 'out_dir': OUT_DIR, 'roots': roots()}
