# -*- coding: utf-8 -*-
r"""v3 · 按已存坐标涂白（第 4 步打码的正路）

问题背景
--------
第 4 步原来只能整张重跑 det：把图丢给 `鉴图.py`，让引擎重新找出所有文字框，再把命中的
那块涂掉。这条路有三个毛病：

  1. **慢** —— 每张图都要跑一遍 det+rec，实测 0.26~0.99s/张（引擎无关，这是模型本身）；
  2. **不幂等** —— 同一张图跑两次 det 未必给出同一组框（概率图阈值边界上的小块会抖动），
     涂出来的图因此不稳定；
  3. **整张糊的根源就在这里** —— 没有可信的「文字在哪」，只能退而求其次做大范围处理。

这一版：第 3 步 OCR 时就把每个文字段的框存进 `v3_image_ocr.boxes_json`（见 images.py），
第 4 步直接按坐标涂，**不再碰 det**。

涂白口径（与 鉴图.py 的 `mask_regions` **逐条对齐**，不是"差不多"）
------------------------------------------------------------
  · 边距：`pad = max(2, int(min(h, w) * 0.004))`，与引擎侧同一个公式；
  · 取框：多边形 x/y 各取 min/max 的**轴对齐矩形**，与引擎侧同一个取法；
  · 填充色：框内像素**均值色**（不是纯白）。引擎侧这么做的理由是「涂成平均底色比纯白
    更不显眼」，本模块默认照做；确实要纯白时传 `fill="white"`；
  · 输出：JPEG quality 92，与引擎侧同一个编码参数。

所以同一张图、同一组框，本模块的输出与「引擎重跑一遍再涂」**应当逐字节相同** ——
`_验_坐标与涂白.py` 里对这一条有实测断言。

无坐标怎么办（历史图）
----------------------
第 3 步加坐标之前跑过的图，`boxes_json` 是 NULL。策略是**只补齐那些确实要涂的图**：
`mask_image()` 发现无坐标 → `backfill_boxes()` 只为这一张跑一次 det → 立刻写回库 →
下一次直接走坐标。不做全量重扫。

产物路径固定 `data/v3/masked/<image_id>_masked.jpg`，**绝不覆盖原图**。
"""
from __future__ import annotations

import os
import time

from . import images, store

MASK_DIR = os.path.join(store.DATA_DIR, "v3", "masked")

PAD_MIN = 2            # 与 鉴图.py:220 一致
PAD_RATIO = 0.004      # 与 鉴图.py:220 一致
JPEG_QUALITY = 92      # 与 鉴图.py:231 一致

FILL_MEAN = "mean"       # 框内均值色（与引擎侧一致）
FILL_WHITE = "white"     # 纯白
FILL_INPAINT = "inpaint"  # ★ 2026-10-24 新增：按周边像素补（消除，不贴色块）
FILL_AUTO = "auto"       # ★ 逐块自动：消除 / 均值色 谁离周边底色近用谁（永不比色块差）
FILL_LAMA = "lama"       # ★ 2026-10-26 新增：LaMa 生成式消除（效果最好，约 3s/张）

METHOD_TELEA = "telea"
METHOD_NS = "ns"
DEFAULT_RADIUS = 3


def _local_color_diff(im, rects, ring: int = 4) -> float:
    """框内均色 vs 紧邻外环均色的平均绝对差 —— 只用来给两种填法打分，不下结论。"""
    import numpy as np  # noqa: PLC0415
    h, w = im.shape[:2]
    tot, n = 0.0, 0
    for r in rects:
        x0, y0, x1, y1 = (int(v) for v in r)
        x0 = max(0, x0); y0 = max(0, y0); x1 = min(w - 1, x1); y1 = min(h - 1, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        inside = im[y0:y1 + 1, x0:x1 + 1].reshape(-1, 3).astype(np.float32)
        ox0, oy0 = max(0, x0 - ring), max(0, y0 - ring)
        ox1, oy1 = min(w - 1, x1 + ring), min(h - 1, y1 + ring)
        outer = im[oy0:oy1 + 1, ox0:ox1 + 1].reshape(-1, 3).astype(np.float32)
        if not inside.size or not outer.size:
            continue
        tot += float(np.abs(inside.mean(axis=0) - outer.mean(axis=0)).mean())
        n += 1
    return (tot / n) if n else 0.0

REGION_TIGHT = "tight"  # 默认：真实文字框（core）—— 按位置精确涂，不糊
REGION_LOOSE = "loose"  # unclip 放大框 —— 与 鉴图.py 现在的涂法逐字节一致
# ★ 2026-11-03 新增：按字形外扩的中间口径。
#   实测（#9487「极致收纳」）：引擎给的 core 框只有字形中间那一横条（高 43px），
#   按 tight 消完**字的上下半截都还在**，人眼看就是"没消掉"；
#   而 loose 是 unclip 8~12 倍面积，会把**隔壁一行字**一起抹掉（实测抹掉了下一行）。
#   所以这里取中间值：core 框上下各外扩 55% 框高、左右各 8% 框宽，再夹进 loose 之内
#   —— 盖得住整个字形，又不会越界吃邻居。
REGION_ADAPT = "adapt"
EXPAND_Y = 0.55        # 上下外扩比例（按 core 框高度）
EXPAND_X = 0.10        # 左右外扩比例（按 core 框宽度）


# ------------------------------------------------------------------ 路径 / 尺寸

def mask_dir(out_dir: str | None = None) -> str:
    d = out_dir or MASK_DIR
    os.makedirs(d, exist_ok=True)
    return d


def masked_path(image_id: int, out_dir: str | None = None) -> str:
    """一张图的固定产物路径。固定而非带时间戳 —— 重跑幂等，不留一堆垃圾文件。"""
    return os.path.join(mask_dir(out_dir), "%d_masked.jpg" % int(image_id))


def default_pad(w: int, h: int) -> int:
    """与 鉴图.py 完全相同的边距公式（2~4px 量级）。"""
    return max(PAD_MIN, int(min(h, w) * PAD_RATIO))


# ------------------------------------------------------------------ 画

def _decode(path: str):
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _encode(im, out_path: str):
    """编码落盘。

    ★ 2026-10-24：原来写死 .jpg（quality 92）。做「消除」验收时发现一个问题 ——
    整图重压会让**遮罩外的像素也变**，于是 repair.inspect 的 outside_changed_px
    永远是几十万，那个指标就废了（它本来是查"有没有误改遮罩外"的）。
    所以改成**按扩展名编码**：.png 走无损，.jpg 仍走原质量；验收用 png。
    """
    import cv2  # noqa: PLC0415
    ext = os.path.splitext(str(out_path))[1].lower()
    if ext in (".png", ".webp"):
        ok, buf = cv2.imencode(ext, im)
    else:
        ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        return None
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(buf.tobytes())
    return out_path


def padded_rect(rect, pad: int, w: int, h: int) -> list[int]:
    """轴对齐矩形 + 边距，夹在图内。与 鉴图.py 的 `min-pad / max+pad` 同一个算法。"""
    x1, y1, x2, y2 = (int(v) for v in rect)
    return [max(0, min(x1 - pad, w - 1)), max(0, min(y1 - pad, h - 1)),
            max(0, min(x2 + pad, w - 1)), max(0, min(y2 + pad, h - 1))]


_INPAINT_IMPL = None      # 可由宿主注入（上货台注入 lama_ipc → 推理走子进程）


def set_inpaint_impl(fn) -> None:
    """注入一个「消除实现」替换进程内 LaMa 推理。

    ★ 2026-11-03 为什么留这个口子：进程内 InferenceSession 会把 GIL 长占，界面必卡；
      而且 2026-10-27 有现场记录 —— 1440×1440 连跑 7 张，进程被系统直接干掉
      （原生 OOM，Python 的 try/except 抓不到，日志停在框换算那行）。
      4.1 早就把推理放进子进程（app/lama_ipc.py），4.0 一直没接上。
      注入点放在这里，是为了**不改任何调用方**：apply() / mask_image() / paint() 一行都不用动，
      默认 None 时行为与原来完全一致（其他调用方不受影响）。
    """
    global _INPAINT_IMPL
    _INPAINT_IMPL = fn


def _lama_try(im, m):
    """试 LaMa 生成式消除 → 成功返回新图，不可用/失败返回 None（调用方回退 cv2）。

    ★ 2026-10-26：打码也从「贴色块 / 边缘扩散」升级到生成式。原因：
      cv2.inpaint 在几百像素的大字块上只能糊成一片，logo 和促销大字都是这个量级；
      LaMa 按"这块应该长什么样"生成，纹理和渐变能接得上。
      模型缺失或 onnxruntime 起不来时**静默回退**，不让打码整体失败。
    ★ 2026-11-03：若宿主注入了实现（见 set_inpaint_impl），**优先走它**；
      它返回 None 时仍按老路走进程内推理，最后再回退 cv2 —— 三层都不会整体失败。
    """
    if _INPAINT_IMPL is not None:
        try:
            out = _INPAINT_IMPL(im, m)
            if out is not None:
                return out
        except Exception:  # noqa: BLE001
            pass
    try:
        from . import inpaint_lama  # noqa: PLC0415
        if not inpaint_lama.available().get("ok"):
            return None
        return inpaint_lama.inpaint(im, m)
    except Exception:  # noqa: BLE001
        return None


def paint(im, rects, fill: str = FILL_MEAN, method: str = METHOD_TELEA,
          radius: int = DEFAULT_RADIUS):
    """把 rects 逐块涂掉 / 补掉，返回处理了多少块。

    `fill="mean"` 时**逐块、按当前图取样**（前面涂过的块会影响后面块的取样）——
    这是照抄 鉴图.py 的行为，为的是两边产物一致；框互不重叠时与"一次性取样"无差别。

    `fill="inpaint"`（2026-10-24 新增）**不贴色块**，而是把框内做成 mask 后按周边像素
    往里推（cv2.inpaint）。这是 4.1 修补用的同一套算法，目的是让文字"消失"而不是
    盖一块颜色 —— 纹理/渐变背景上色块会露馅，消除不会。

    `fill="lama"`（2026-10-26 新增）用 LaMa 生成式模型消除；**LaMa 不可用时自动退回
    cv2.inpaint**，行为与 `inpaint` 相同。`fill="inpaint"` 也会**优先试 LaMa**，
    失败再走 cv2 —— 也就是说默认的消除路径已经是生成式了。
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    h, w = im.shape[:2]

    if fill in (FILL_INPAINT, FILL_LAMA):
        m = np.zeros((h, w), np.uint8)
        n = 0
        for r in rects:
            x0, y0, x1, y1 = (int(v) for v in r)
            if x1 <= x0 or y1 <= y0:
                continue
            x0 = max(0, min(x0, w - 1)); x1 = max(0, min(x1, w - 1))
            y0 = max(0, min(y0, h - 1)); y1 = max(0, min(y1, h - 1))
            m[y0:y1 + 1, x0:x1 + 1] = 255
            n += 1
        if n == 0:
            return 0
        out = _lama_try(im, m)
        if out is None:
            flag = cv2.INPAINT_NS if str(method).lower() == METHOD_NS else cv2.INPAINT_TELEA
            out = cv2.inpaint(im, m, float(radius), flag)
        im[:] = out          # 保持调用方手里的对象引用不变
        return n

    n = 0
    for r in rects:
        x0, y0, x1, y1 = (int(v) for v in r)
        if x1 <= x0 or y1 <= y0:
            continue
        if fill == FILL_WHITE:
            color = (255, 255, 255)
        else:
            patch = im[y0:y1 + 1, x0:x1 + 1]
            if patch.size:
                m = patch.reshape(-1, 3).mean(axis=0).astype(np.uint8).tolist()
                color = (int(m[0]), int(m[1]), int(m[2]))
            else:
                color = (255, 255, 255)
        cv2.rectangle(im, (x0, y0), (x1, y1), color, -1)
        n += 1
    return n


# ------------------------------------------------------------------ 挑命中框

def box_of(b: dict, region: str = REGION_TIGHT) -> list[int]:
    """取这一段要涂的矩形：tight=真实文字框（默认），loose=unclip 放大框（旧口径），
    adapt=core 框按字形外扩（推荐用于「消除」：盖住整字又不吃邻居）。"""
    if region == REGION_ADAPT:
        t = [int(v) for v in (b.get("box") or b.get("box_loose") or [])]
        l = [int(v) for v in (b.get("box_loose") or b.get("box") or [])]
        if len(t) != 4:
            return []
        if len(l) != 4:
            l = t
        h = t[3] - t[1]
        w = t[2] - t[0]
        dy = int(round(h * EXPAND_Y))
        dx = int(round(w * EXPAND_X))
        return [max(l[0], t[0] - dx), max(l[1], t[1] - dy),
                min(l[2], t[2] + dx), min(l[3], t[3] + dy)]
    key = "box_loose" if region == REGION_LOOSE else "box"
    r = b.get(key) or b.get("box") or b.get("box_loose") or []
    return [int(v) for v in r] if len(r) == 4 else []


def target_boxes(boxes: list[dict], words=None) -> list[dict]:
    """从已存坐标里挑出「文字命中词表」的框。

    `words` 三种形态：
      · None      → 用第 3 步存下的 `word` 字段（当时的判定结果）
      · list[str] → 按当前词表做子串比对，长词优先（词库改过也能重涂）
      · WordSet   → 走 images.WordSet.match，与第 3 步完全同一套匹配规则
    """
    if words is None:
        return [b for b in (boxes or []) if (b.get("word") or "").strip()]
    if hasattr(words, "match"):            # images.WordSet
        return [b for b in (boxes or []) if words.match(b.get("text") or "")]
    wl = sorted([str(w) for w in (words or []) if str(w)], key=len, reverse=True)
    if not wl:
        return []
    out = []
    for b in (boxes or []):
        up = (b.get("text") or "").upper()
        if any(w.upper() in up for w in wl):
            out.append(b)
    return out


# ------------------------------------------------------------------ 回填坐标

def backfill_boxes(conn, image_id: int, local_path: str | None = None,
                   words=None) -> dict | None:
    """为「命中词已知但没坐标」的图**只跑这一次 det**，拿到框立刻回写库。

    返回 `{"img_w","img_h","boxes","boxes_at"}`；引擎不可用或图读不出来时返回 None
    （调用方据此报「补齐失败」，**不许**把失败当成「无命中」）。
    """
    iid = int(image_id)
    path = local_path
    if not path or not os.path.isfile(path):
        r = conn.execute("SELECT current_local_path, original_local_path FROM v3_image"
                         " WHERE image_id=?", (iid,)).fetchone()
        if r:
            for k in ("current_local_path", "original_local_path"):
                p = (r[k] or "").strip()
                if p and os.path.isfile(p):
                    path = p
                    break
    if not path or not os.path.isfile(path):
        return None
    iw, ih = images.image_size(path)
    if not iw or not ih:
        return None
    eng = images._load_ocr()               # 同包内复用引擎单例，不新建实例
    if eng is None:
        return None
    texts = eng.run(path)
    ws = words if words is not None else images.build_words(conn)
    boxes = images.boxes_from_ocr(texts, ws, iw, ih)
    images.save_boxes(conn, iid, boxes, iw, ih)
    return {"img_w": iw, "img_h": ih, "boxes": boxes, "boxes_at": store.now()}


# ------------------------------------------------------------------ 主入口

def mask_image(conn, image_id: int, local_path: str | None = None,
               out_path: str | None = None, words=None, pad: int | None = None,
               fill: str = FILL_MEAN, allow_redet: bool = True,
               force_redet: bool = False, out_dir: str | None = None,
               region: str = REGION_TIGHT, method: str = METHOD_TELEA,
               radius: int = DEFAULT_RADIUS) -> dict:
    """按已存坐标把命中文字涂掉，写到独立产物路径。**不动原图、不动 商品.db。**

    `words`     见 target_boxes；None = 用第 3 步当时的命中判定
    `pad`       像素边距；None = 用与引擎一致的公式
    `fill`      "mean"（同引擎）| "white" | **"inpaint"（消除）** | **"auto"（逐块取更近底色的那种）**
    `method`    inpaint 算法："telea"（默认，细笔画/小面积最好）| "ns"
    `radius`    inpaint 半径，默认 3
    `region`    "tight"（默认，真实文字框）| "loose"（unclip 放大框，= 鉴图.py 现行口径）
    `allow_redet` 无坐标时是否允许补跑一次 det（历史图靠它）
    """
    t_all = time.time()
    iid = int(image_id)
    images._ensure_table(conn)
    row = conn.execute("SELECT * FROM v3_image WHERE image_id=?", (iid,)).fetchone()
    if row is None:
        return {"ok": False, "image_id": iid, "error": "v3 库里没有这张图"}
    img = dict(row)

    path = local_path
    if not path or not os.path.isfile(path):
        path = images.resolve_local(img)[0]
    if not path or not os.path.isfile(path):
        return {"ok": False, "image_id": iid, "error": "本地没有这张图（先下载）"}

    im = _decode(path)
    if im is None:
        return {"ok": False, "image_id": iid, "error": "图片解码失败：%s" % path}
    h, w = im.shape[:2]

    # ① 取坐标：库里已有 → 直接用；尺寸对不上 / 要求强制重跑 → 走 det
    mode = "boxes"
    note = ""
    bo = None if force_redet else images.boxes_of(conn, iid)
    if bo is not None:
        sw, sh = bo.get("img_w"), bo.get("img_h")
        if sw and sh and (int(sw), int(sh)) != (w, h):
            # 坐标是按另一张（或另一尺寸）的图算的 —— 涂上去就会错位，宁可重跑
            note = "存图尺寸 %sx%s ≠ 当前 %dx%d，放弃旧坐标" % (sw, sh, w, h)
            bo = None
    if bo is None:
        if not allow_redet:
            return {"ok": False, "image_id": iid, "mode": "none", "need_redet": True,
                    "error": "没有坐标，且本次不允许补跑 det", "note": note}
        t0 = time.time()
        bo = backfill_boxes(conn, iid, path, words)
        if bo is None:
            return {"ok": False, "image_id": iid, "mode": "redet-failed",
                    "error": "没有坐标，补跑 det 也失败（引擎不可用或图读不出来）",
                    "note": note}
        mode = "redet"
        redet_secs = round(time.time() - t0, 3)
    else:
        redet_secs = 0.0

    boxes = bo.get("boxes") or []
    targets = target_boxes(boxes, words)
    if not targets:
        return {"ok": True, "image_id": iid, "masked": False, "mode": mode,
                "boxes_total": len(boxes), "boxes_hit": 0, "img_w": w, "img_h": h,
                "n_boxes": len(boxes), "img_size_stored": (bo.get("img_w"), bo.get("img_h")),
                "secs": round(time.time() - t_all, 3),
                "note": (note + " " if note else "") + "当前词表在这张图上没有命中，不产图",
                "error": ""}

    # ② 涂
    p = default_pad(w, h) if pad is None else int(pad)
    rects = [padded_rect(box_of(b, region), p, w, h) for b in targets]
    rects = [r for r in rects if len(r) == 4 and r[2] > r[0] and r[3] > r[1]]
    n_painted = paint(im, rects, fill=fill, method=method, radius=radius)
    chosen = fill
    if fill == FILL_AUTO:
        # 两种都做一份，选「离周边底色更近」的那个 —— 保证不劣于贴色块
        im_i = im.copy()
        n_i = paint(im_i, rects, fill=FILL_INPAINT, method=method, radius=radius)
        im_m = im.copy()
        n_m = paint(im_m, rects, fill=FILL_MEAN)
        d_i = _local_color_diff(im_i, rects)
        d_m = _local_color_diff(im_m, rects)
        if d_i <= d_m:
            im[:] = im_i
            n_painted, chosen = n_i, FILL_INPAINT
        else:
            im[:] = im_m
            n_painted, chosen = n_m, FILL_MEAN
        auto_pick = {"inpaint": round(d_i, 2), "mean": round(d_m, 2)}
    else:
        auto_pick = None

    dst = out_path or masked_path(iid, out_dir)
    if _encode(im, dst) is None:
        return {"ok": False, "image_id": iid, "error": "JPEG 编码失败"}

    return {"ok": True, "image_id": iid, "masked": True, "mode": mode,
            "out_path": dst, "bytes": os.path.getsize(dst),
            "img_w": w, "img_h": h, "pad": p, "fill": fill, "picked": chosen,
            "auto_pick": auto_pick, "region": region,
            "method": (method if fill == FILL_INPAINT else ""),
            "radius": (int(radius) if fill == FILL_INPAINT else 0),
            "boxes_total": len(boxes), "boxes_hit": len(targets),
            "painted": n_painted, "rects": rects,
            "hit_texts": [b.get("text") for b in targets][:8],
            "redet_secs": redet_secs, "secs": round(time.time() - t_all, 3),
            "note": note, "error": ""}


# ------------------------------------------------------------------ 计划 / 批量

def plan(conn, pids=None, image_ids=None, words=None) -> dict:
    """第 4 步开跑前的盘点：哪些图有坐标、哪些无坐标但确实要涂（需要补跑一次 det）。

    纯只读。给 上货台 面板与验收脚本看的，**不写库、不产图**。
    """
    images._ensure_table(conn)
    ids: list[int] = [int(x) for x in (image_ids or [])]
    if not ids and pids:
        marks = ",".join("?" * len(pids))
        ids = [int(r[0]) for r in conn.execute(
            "SELECT image_id FROM v3_image WHERE product_id IN (%s)" % marks,
            [int(x) for x in pids])]
    have = need = 0
    need_ids: list[int] = []
    for iid in ids:
        bo = images.boxes_of(conn, iid)
        if bo is None:
            need += 1
            need_ids.append(iid)
        else:
            have += 1
    return {"ok": True, "images": len(ids), "with_boxes": have, "no_boxes": need,
            "need_redet_ids": need_ids[:200], "engine": images.OCR_ENGINE_NAME}


def apply(conn, image_ids, words=None, fill: str = FILL_MEAN,
          out_dir: str | None = None, log=None,
          region: str = REGION_TIGHT, method: str = METHOD_TELEA,
          radius: int = DEFAULT_RADIUS) -> dict:
    """批量按坐标涂/补（顺序执行，单线程 —— 涂白是纯 CPU 轻活，不需要并发）。"""
    ok = masked = skipped = failed = 0
    results = []
    for n, iid in enumerate([int(x) for x in (image_ids or [])], 1):
        r = mask_image(conn, iid, words=words, fill=fill, out_dir=out_dir,
                       region=region, method=method, radius=radius)
        if not r.get("ok"):
            failed += 1
        elif r.get("masked"):
            masked += 1
            ok += 1
        else:
            skipped += 1
            ok += 1
        results.append(r)
        if log and n % 10 == 0:
            try:
                log("坐标涂白 %d/%d · 涂了 %d 跳过 %d 失败 %d"
                    % (n, len(image_ids), masked, skipped, failed))
            except Exception:          # noqa: BLE001
                pass
    return {"ok": failed == 0, "total": len(results), "masked": masked,
            "skipped": skipped, "failed": failed, "results": results}


# ------------------------------------------------------------------ CLI

def _main(argv=None):
    """命令行自检用：只对指定 image_id 出图，不批量扫库、不连网络。"""
    import argparse
    import json

    ap = argparse.ArgumentParser(description="按已存坐标涂白/消除（v3 第 4 步）")
    ap.add_argument("--image", type=int, action="append", required=True,
                    help="image_id，可重复")
    ap.add_argument("--words", default="", help="逗号分隔的命中词；省略=用第3步判定")
    ap.add_argument("--pad", type=int, default=-1)
    ap.add_argument("--fill", default=FILL_MEAN,
                    choices=[FILL_MEAN, FILL_WHITE, FILL_INPAINT, FILL_AUTO])
    ap.add_argument("--method", default=METHOD_TELEA, choices=[METHOD_TELEA, METHOD_NS])
    ap.add_argument("--radius", type=int, default=DEFAULT_RADIUS)
    ap.add_argument("--region", default=REGION_TIGHT, choices=[REGION_TIGHT, REGION_LOOSE])
    ap.add_argument("--db", default="")
    ap.add_argument("--out-dir", default="")
    a = ap.parse_args(argv)

    conn = store.connect(a.db or None)
    try:
        words = [w.strip() for w in a.words.split(",") if w.strip()] or None
        out = []
        for iid in a.image:
            out.append(mask_image(conn, iid, words=words,
                                  pad=(None if a.pad < 0 else a.pad),
                                  fill=a.fill, out_dir=(a.out_dir or None),
                                  region=a.region, method=a.method, radius=a.radius))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if all(r.get("ok") for r in out) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    sys.exit(_main())

