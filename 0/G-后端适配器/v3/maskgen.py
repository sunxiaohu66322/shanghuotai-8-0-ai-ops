# -*- coding: utf-8 -*-
r"""v3 · 遮罩生成（阶段 B · 无状态）

为什么单独一个模块
------------------
修补内核 `repair.py` 只认一张 8 位掩码，**不关心遮罩怎么来的**。把「怎么画」独立出来有两个
好处：① 阶段 C 的人工精修工作台可以复用完全同一套画法（矩形/多边形/画笔/扩边），界面上画
的和自动生成的结果因此可以逐像素对比；② 遮罩能单独存成 PNG 存档，一个遮罩可以喂给 TELEA
和 NS 两种算法做公平对比（同一遮罩，只换算法）。

无状态
------
本模块**不碰数据库、不碰 v3_image、不读配置文件**。所有函数都是「进参 → 出 ndarray / 出
文件」，调用方给什么数组就处理什么数组。原图永远不会被写。

坐标口径
--------
与 `mask.py` 保持一致：框一律用 **x1,y1,x2,y2（含端点）**，边距用 `padding` 像素统一外扩
（负数 = 内缩）。真实 OCR 框在 `v3_image_ocr.boxes_json` 里是 `box`（tight）/`box_loose`
（unclip 放大框）/`poly`（四个角点），本模块的 `regions_from_boxes` 三种都能吃。

四种画法
--------
    rect   矩形：{'kind':'rect','rect':[x1,y1,x2,y2]}
    poly   多边形（任意顶点，套索/文字四角）：{'kind':'poly','points':[[x,y],...]}
    brush  画笔（沿折线扫出圆形笔迹）：{'kind':'brush','points':[[x,y],...],'radius':12}
    扩边   任何遮罩都能再 grow（正=外扩，负=内缩），用于解决文字边缘残留

确定性
------
同参数两次调用，输出逐字节相同：`cv2.rectangle / fillPoly / line / circle / dilate` 全部
是确定性的，没有随机、没有时间戳参与内容。
"""
from __future__ import annotations

import os

import cv2
import numpy as np

KIND_RECT = "rect"
KIND_POLY = "poly"
KIND_BRUSH = "brush"

REGION_TIGHT = "tight"
REGION_LOOSE = "loose"

MASK_SUFFIX = ".mask.png"          # 无损；JPEG 会把 0/255 边界磨成灰，绝不用


# ------------------------------------------------------------------ 基础几何

def _as_size(image_size):
    """(w,h) 或 (h,w,3) 形状都能吃；返回 (w,h)。"""
    if isinstance(image_size, (tuple, list)):
        if len(image_size) == 2:
            w, h = int(image_size[0]), int(image_size[1])
        elif len(image_size) == 3:          # numpy shape = (h,w,c)
            h, w = int(image_size[0]), int(image_size[1])
        else:
            raise ValueError("image_size 只接受 (w,h) 或 (h,w,c)")
    else:                                   # 直接传 ndarray
        h, w = int(image_size.shape[0]), int(image_size.shape[1])
    if w <= 0 or h <= 0:
        raise ValueError("image_size 非法：%r" % (image_size,))
    return w, h


def empty_mask(image_size) -> np.ndarray:
    """全 0 的 8 位掩码（0=不动，255=要修补）。"""
    w, h = _as_size(image_size)
    return np.zeros((h, w), dtype=np.uint8)


# ------------------------------------------------------------------ 画

def draw_rect(mask, rect, value: int = 255) -> np.ndarray:
    """轴对齐矩形，含端点（与 mask.py 的 inclusive 口径一致）。"""
    h, w = mask.shape[:2]
    x1, y1, x2, y2 = (int(round(float(v))) for v in rect)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w - 1))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h - 1))
    if x2 < x1 or y2 < y1:
        return mask
    mask[y1:y2 + 1, x1:x2 + 1] = value
    return mask


def draw_poly(mask, points, value: int = 255) -> np.ndarray:
    """任意多边形（>=3 点）；顶点自动夹到图内。"""
    if not points or len(points) < 3:
        return mask
    h, w = mask.shape[:2]
    pts = np.array([[int(round(float(x))), int(round(float(y)))] for x, y in points], dtype=np.int32)
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    cv2.fillPoly(mask, [pts], value)
    return mask


def draw_brush(mask, points, radius: int = 12, value: int = 255) -> np.ndarray:
    """画笔：沿折线扫出圆形笔迹（点与点之间连线，保证连续不断笔）。"""
    if not points:
        return mask
    r = max(1, int(round(radius)))
    h, w = mask.shape[:2]
    pts = np.array([[int(round(float(x))), int(round(float(y)))] for x, y in points], dtype=np.int32)
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    if len(pts) == 1:
        cv2.circle(mask, tuple(pts[0]), r, value, -1)
        return mask
    cv2.polylines(mask, [pts], False, value, thickness=r * 2, lineType=cv2.LINE_8)
    for p in pts:                      # 补两头圆头，避免方角
        cv2.circle(mask, tuple(p), r, value, -1)
    return mask


def draw_region(mask, region, value: int = 255) -> np.ndarray:
    """按 region 字典的画法落笔（不处理 padding）。"""
    kind = str((region or {}).get("kind") or KIND_RECT).lower()
    if kind == KIND_RECT:
        r = region.get("rect") or region.get("box") or []
        if len(r) == 4:
            draw_rect(mask, r, value)
    elif kind == KIND_POLY:
        pts = region.get("points") or region.get("poly") or []
        draw_poly(mask, pts, value)
    elif kind == KIND_BRUSH:
        pts = region.get("points") or []
        draw_brush(mask, pts, int(region.get("radius") or 12), value)
    else:
        raise ValueError("未知 region.kind：%r" % (kind,))
    return mask


def grow_mask(mask, px: int) -> np.ndarray:
    """像素级外扩（px>0）或内缩（px<0）。返回新数组，不改入参。"""
    px = int(round(px or 0))
    if px == 0:
        return mask.copy()
    k = 2 * abs(px) + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    if px > 0:
        return cv2.dilate(mask, kernel)
    return cv2.erode(mask, kernel)


# ------------------------------------------------------------------ 主入口

def build_mask(image_size, regions, padding: int = 0, grow: int = 0) -> np.ndarray:
    """把一组 region 画成一张掩码。

    参数
    ----
    image_size : (w,h) 或 (h,w,c) 或 ndarray  —— 掩码尺寸以它为准
    regions    : list[dict]，见模块头四行示例；空/None → 全 0 掩码
    padding    : 每个 region 统一外扩（负数内缩）的像素数
    grow       : 画完之后对**整张**掩码再外扩/内缩一次（用于批量修边）

    返回 8 位单通道 ndarray，255 = 要修补。
    """
    mask = empty_mask(image_size)
    for region in (regions or []):
        sub = empty_mask(mask)
        draw_region(sub, region, 255)
        if padding:
            sub = grow_mask(sub, padding)
        mask = np.maximum(mask, sub)
    if grow:
        mask = grow_mask(mask, grow)
    return mask


def build_mask_from_boxes(image_size, boxes, region: str = REGION_TIGHT,
                          padding: int = 0, shape: str = KIND_RECT,
                          shift=(0, 0), scale: float = 1.0) -> np.ndarray:
    """真实 OCR 框直接转掩码（阶段 D 的自动修补就走这里）。

    boxes  : `v3_image_ocr.boxes_json` 解出来的 list[dict]
    region : 'tight'（文字真实框）| 'loose'（unclip 放大框）
    shape  : 'rect'（轴对齐矩形，稳定）| 'poly'（用框自带四角点，更贴字）
    shift  : (dx,dy) 像素平移 —— 专门用来复现「OCR 坐标偏移」
    scale  : 相对框中心缩放（<1 选区过小，>1 选区过大）
    """
    regions = regions_from_boxes(boxes, region=region, shape=shape, shift=shift, scale=scale)
    return build_mask(image_size, regions, padding=padding)


def box_rect(box: dict, region: str = REGION_TIGHT):
    """取这一段要处理的矩形（与 mask.py 同一取法）。"""
    key = "box_loose" if str(region).lower() == REGION_LOOSE else "box"
    r = (box or {}).get(key) or (box or {}).get("box") or (box or {}).get("box_loose") or []
    if len(r) == 4:
        return [float(v) for v in r]
    return []


def box_poly(box: dict):
    """取框自带的四角点；没有就退回矩形四角。"""
    p = (box or {}).get("poly") or []
    if isinstance(p, (list, tuple)) and len(p) >= 3:
        return [[float(x), float(y)] for x, y in p]
    r = box_rect(box)
    if len(r) == 4:
        x1, y1, x2, y2 = r
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return []


def _transform(points, anchor, shift=(0, 0), scale: float = 1.0):
    cx, cy = anchor
    dx, dy = shift
    out = []
    for x, y in points:
        nx = cx + (float(x) - cx) * scale + dx
        ny = cy + (float(y) - cy) * scale + dy
        out.append([nx, ny])
    return out


def regions_from_boxes(boxes, region: str = REGION_TIGHT, shape: str = KIND_RECT,
                       shift=(0, 0), scale: float = 1.0):
    """OCR 框 → region 列表（可整体平移/缩放，用于偏移与大小实验）。"""
    out = []
    for b in (boxes or []):
        if shape == KIND_POLY:
            pts = box_poly(b)
            if len(pts) < 3:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            anchor = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
            out.append({"kind": KIND_POLY, "points": _transform(pts, anchor, shift, scale)})
        else:
            r = box_rect(b, region)
            if len(r) != 4:
                continue
            x1, y1, x2, y2 = r
            anchor = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            pts = _transform([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], anchor, shift, scale)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append({"kind": KIND_RECT,
                        "rect": [min(xs), min(ys), max(xs), max(ys)]})
    return out


def brush_path_from_rect(rect, steps: int = 9, radius: int = 12):
    """给「手动画笔」造一条确定性的折线（沿框中心来回扫），仅测试与演示用。"""
    x1, y1, x2, y2 = [float(v) for v in rect]
    cy = (y1 + y2) / 2.0
    pts = []
    for i in range(max(2, int(steps))):
        x = x1 + (x2 - x1) * i / float(steps - 1)
        pts.append([x, cy + (radius * 0.25 if i % 2 else -radius * 0.25)])
    return {"kind": KIND_BRUSH, "points": pts, "radius": int(radius)}


# ------------------------------------------------------------------ 统计 / 存盘

def mask_stats(mask) -> dict:
    """面积、占比、外接框、连通块数。给验收与 inspect 用。"""
    h, w = mask.shape[:2]
    on = mask > 0
    area = int(on.sum())
    if area == 0:
        return {"w": w, "h": h, "area_px": 0, "ratio": 0.0, "bbox": [0, 0, 0, 0],
                "components": 0, "touches_border": False}
    ys, xs = np.nonzero(on)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    n, _lab = cv2.connectedComponents(on.astype(np.uint8), connectivity=8)
    return {"w": w, "h": h, "area_px": area, "ratio": round(area / float(w * h), 6),
            "bbox": bbox, "components": int(n - 1),
            "touches_border": bool(bbox[0] <= 0 or bbox[1] <= 0
                                   or bbox[2] >= w - 1 or bbox[3] >= h - 1)}


def save_mask(mask, path: str) -> str:
    """存 PNG（无损）。父目录自动建。"""
    p = str(path)
    if not p.lower().endswith(".png"):
        p = p + MASK_SUFFIX if not p.lower().endswith(MASK_SUFFIX) else p
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    ok, buf = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError("遮罩编码失败：%s" % p)
    with open(p, "wb") as f:
        f.write(buf.tobytes())
    return p


def load_mask(path: str) -> np.ndarray:
    """读 PNG 遮罩，统一成 8 位单通道（0/255）。"""
    buf = np.fromfile(str(path), dtype=np.uint8)
    m = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if m is None:
        raise RuntimeError("遮罩读取失败：%s" % path)
    return m


def overlay_mask(bgr, mask, color=(255, 0, 255), alpha: float = 0.45):
    """可视化：把遮罩半透明叠到图上（只用于出对比图，不进生产链路）。"""
    out = bgr.copy()
    on = mask > 0
    if on.any():
        layer = np.zeros_like(out)
        layer[on] = color
        out[on] = (out[on] * (1.0 - alpha) + layer[on] * alpha).astype(np.uint8)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, color, 1)
    return out


# ------------------------------------------------------------------ CLI（自检用）

def _main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description="遮罩生成自检（阶段 B）")
    ap.add_argument("--w", type=int, default=400)
    ap.add_argument("--h", type=int, default=300)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    size = (a.w, a.h)
    regions = [
        {"kind": KIND_RECT, "rect": [20, 20, 120, 60]},
        {"kind": KIND_POLY, "points": [[160, 20], [260, 30], [240, 90], [150, 80]]},
        {"kind": KIND_BRUSH, "points": [[60, 160], [180, 180], [300, 150]], "radius": 10},
    ]
    m = build_mask(size, regions, padding=2)
    st = mask_stats(m)
    print(json.dumps({"stats": st, "shape": list(m.shape), "dtype": str(m.dtype)},
                     ensure_ascii=False, indent=1))
    if a.out:
        print("saved:", save_mask(m, a.out))
    return 0 if st["area_px"] > 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())
