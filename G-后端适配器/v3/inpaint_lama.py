# -*- coding: utf-8 -*-
"""v3 · LaMa 修补引擎（ONNX 版，无需 torch）

★ 2026-10-26 为什么有这个模块：
  原来的 OpenCV `INPAINT_TELEA` / `INPAINT_NS` 是**传播式**算法 —— 它拿周边像素往选区里
  一遍遍扩散填色。小面积细笔画还行，但电商主图上常见的「促销大字」「贴片角标」「水印」
  都是几百到上千像素的大块，传播式算法整块糊成一片，渐变背景上还会拉出条纹
  （阶段 B 实测：TELEA 在平滑渐变上纹理能量比 19.21，NS 0.76）。

  LaMa（Large-Mask Inpainting）是**生成式**的：FFC 结构把感受野做到整图，靠「这块应该
  长什么样」来猜，而不是从边缘往里抹。这就是 PS 内容识别填充那条技术路线，专门解决大遮罩。

  本机 Python 3.13 **没有 torch**，但有 onnxruntime 1.30 + onnxruntime-directml，
  而 HuggingFace 上有现成的 ONNX 导出（Carve/LaMa-ONNX），所以走 ONNX 路线。

模型
    models/lama_fp32.onnx  约 208 MB，51M 参数
    来源  https://huggingface.co/Carve/LaMa-ONNX
    输入  image [1,3,H,W] float32 0–1        mask [1,1,H,W] float32 0/1（1=要抹掉）
    输出  output [1,3,H,W] float32 0–255
    ★ 导出的 ONNX 只把 batch 设为 dynamic，**H/W 固定 512**
      ⇒ 必须 resize 到 512 推理，再放回原尺寸

与旧算法的接口差异
    旧   repair.repair(bgr, mask, method, radius)   → 整图解算，输出整图
    新   inpaint(bgr, mask)                          → 输出整图，但**只在 mask 内改动**
         mask 外的像素逐字节取自原图 —— 因为 resize 往返会轻微降质，不能让整张图陪葬。

用法
    from server.v3 import inpaint_lama
    out = inpaint_lama.inpaint(bgr, mask)          # mask: uint8, 255=抹掉
    print(inpaint_lama.available())                 # 模型在不在、能不能用
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np

# ================================================================ 模型定位

_SESSION = None                 # onnxruntime.InferenceSession
_SESSION_INFO: dict = {}        # 加载详情（provider / 路径 / 耗时）
_LOCK = threading.Lock()

MODEL_NAME = "lama_fp32.onnx"
# ★ 2026-10-26 实测：HF 上的 lama_fp32.onnx 是 **208,044,816 B（198.4 MiB）**。
#   一开始写成 `208 * 1024 * 1024`（= 218,103,808）当门槛，结果把刚下好的模型判成
#   "下载不完整" —— 那个 208 是十进制 MB，不是 MiB。门槛按 190 MiB 留余量。
MODEL_SIZE_HINT = 190 * 1024 * 1024     # 低于这个数当下载不完整
LAMA_INPUT = 512                        # ★ 导出时固定的 H/W


def _candidates() -> list[Path]:
    """按优先级列出模型可能的位置（打包后 cwd 会变，所以要多路找）。"""
    here = Path(__file__).resolve()
    # server/v3/inpaint_lama.py → taobao-picker/
    root = here.parent.parent.parent
    out = [
        root / "models" / MODEL_NAME,                       # 项目内（首选）
        root / "server" / "v3" / "models" / MODEL_NAME,
        root.parent / "models" / MODEL_NAME,
    ]
    env = os.environ.get("DSH_LAMA_MODEL")
    if env:
        out.insert(0, Path(env))
    return out


def model_path() -> Path | None:
    for p in _candidates():
        try:
            if p.is_file() and p.stat().st_size > MODEL_SIZE_HINT:
                return p
        except OSError:
            continue
    return None


def available() -> dict:
    """探测可用性，不加载模型（快）。"""
    try:
        import onnxruntime as ort
        ver = ort.__version__
        providers = ort.get_available_providers()
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "why": "没装 onnxruntime：%s" % e,
                "model": None, "providers": []}
    p = model_path()
    if p is None:
        tried = " | ".join(str(x) for x in _candidates())
        return {"ok": False, "why": "模型不在（需要一个 >190MB 的 %s）｜找过：%s" % (MODEL_NAME, tried),
                "model": None, "providers": providers, "ort": ver}
    return {"ok": True, "model": str(p), "size": p.stat().st_size,
            "providers": providers, "ort": ver, "input": LAMA_INPUT}


def _session():
    """懒加载 + 线程安全。CPU 优先（512 推理已够快，DML 首次编译反而慢）。"""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    with _LOCK:
        if _SESSION is not None:
            return _SESSION
        import onnxruntime as ort
        p = model_path()
        if p is None:
            raise RuntimeError("LaMa 模型不存在：%s" % " | ".join(str(x) for x in _candidates()))
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = max(2, min(8, (os.cpu_count() or 4)))
        so.log_severity_level = 3
        # ★ 2026-10-26 实测：DmlExecutionProvider **跑不了这个模型** ——
        #   LaMa 的 FFC 层里有 FFT 相关算子（rfft/irfft 包成 MatMul），
        #   DirectML 在 `/generator/model/model.5/conv1/ffc/conv2g/fu/rtn/MatMul_5`
        #   上报 `80070057`（E_INVALIDARG），run 时直接崩。
        #   而且这个错发生在 **run 阶段**，创建 session 时不会报，所以静默 fallback
        #   救不回来 —— 必须一开始就用 CPU。512×512 的 CPU 推理约 0.5–2 秒，够用。
        #   想试显卡就设 DSH_LAMA_DML=1（失败会自动退回 CPU）。
        if os.environ.get("DSH_LAMA_DML") == "1":
            want = [x for x in ("DmlExecutionProvider", "CPUExecutionProvider")
                    if x in ort.get_available_providers()]
        else:
            want = ["CPUExecutionProvider"]
        t0 = time.time()
        try:
            sess = ort.InferenceSession(str(p), sess_options=so, providers=want)
            used = sess.get_providers()
        except Exception as e:                              # noqa: BLE001
            # 任何初始化失败都退回纯 CPU，不让整个功能挂掉
            sess = ort.InferenceSession(str(p), sess_options=so,
                                        providers=["CPUExecutionProvider"])
            used = sess.get_providers()
            _SESSION_INFO["fallback"] = "%s: %s" % (type(e).__name__, e)
        _SESSION = sess
        _SESSION_INFO.update({
            "model": str(p), "providers": used,
            "load_seconds": round(time.time() - t0, 2),
            "inputs": [(i.name, i.shape) for i in sess.get_inputs()],
            "outputs": [(o.name, o.shape) for o in sess.get_outputs()],
        })
        return _SESSION


def info() -> dict:
    return dict(_SESSION_INFO)


# ================================================================ 推理

def _to_model_input(bgr: np.ndarray, mask: np.ndarray, size: int = LAMA_INPUT):
    """BGR + mask → (image[1,3,S,S] 0-1, mask[1,1,S,S] 0/1)，并返回还原所需信息。"""
    import cv2

    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    small = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
    m = mask
    if m.ndim == 3:
        m = m[:, :, 0]
    m = (m > 0).astype(np.uint8) * 255
    m_small = cv2.resize(m, (size, size), interpolation=cv2.INTER_NEAREST)

    img_in = small.astype(np.float32) / 255.0
    img_in = np.transpose(img_in, (2, 0, 1))[None, ...]          # 1,3,S,S
    # ★ 官方导出脚本里 mask 走的是 get_image() + `(mask>0)*1`，
    #   即先 /255 再二值化，等价于直接布尔化到 0/1。
    mask_in = (m_small > 0).astype(np.float32)[None, None, ...]
    return img_in, mask_in, (h, w)


def _mem_guard(tag: str) -> dict:
    """读进程当前内存占用（Windows）。拿不到就返回空 dict，绝不抛错。"""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        # argtypes 必须显式声明：否则 64 位句柄会被按 32 位 int 截断，调用返回 0
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        h = k32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
            return {"tag": tag,
                    "ws_mb": round(m.WorkingSetSize / 1048576, 1),
                    "peak_mb": round(m.PeakWorkingSetSize / 1048576, 1)}
    except Exception:                                       # noqa: BLE001
        pass
    return {}


def inpaint(bgr: np.ndarray, mask: np.ndarray, *,
            blend: int = 3, only_inside: bool = True) -> np.ndarray:
    """用 LaMa 抹掉 mask 区域，返回新的 BGR 图（不修改入参）。

    bgr   np.uint8 [H,W,3]
    mask  np.uint8 [H,W] 或 [H,W,1]，非 0 = 要抹掉
    blend 遮罩边缘羽化像素数（消除接缝，0 = 硬边）
    only_inside True = mask 外逐字节保留原图（推荐；resize 往返会轻微降质）
                False = 直接返回 LaMa 的整图结果

    ★ 2026-10-27 内存保护（这是「批量消除到第 7 张大图时整个软件跳出」的根因修复）：
        实测崩溃现场 = 图#14683 **1440×1440**、mask 112690px，日志停在框换算之后，
        连 "LaMa ..s" 都没打出来 ⇒ 进程在 sess.run 里被系统/运行时直接干掉，
        Python 的 try/except 根本没机会执行（原生层崩溃，不是 Python 异常）。

        为什么会爆：模型输入固定 512×512，但**输出要从 512 放大回原图尺寸**，
        同时驻留的 float32 大数组有：
            rgb(1440×1440×3 u8=6.2MB) → resize 后 small(0.75MB)
            out(512×512×3 f32=3.1MB) → big(1440×1440×3 u8=6.2MB)
            混合时: big.astype(f32)=**24.9MB** + src.astype(f32)=**24.9MB**
                    + a=8.3MB + res=24.9MB  ← 同一瞬间要 ~83MB 连续分配
        单张尚可，但 ONNX 会话本身已占数百 MB，加上 9 张连续跑、内存碎片化，
        峰值就顶到了上限；系统直接终止进程。

        对策不是"少跑几张"，而是**把峰值压下来**：
          ① 815 行起：大图先按需降采样到 MAX_EDGE 再推理（框坐标同步缩放）——
             1440 → 1024，混合阶段的 float32 从 83MB 降到 ~42MB
          ② 混合改成**分块（tile）**做，一次只吃 256×256 的块，峰值与图大小无关
          ③ 每张开始/结束各报一次内存，日志里能看出是不是在爬坡
    """
    import cv2

    src = np.ascontiguousarray(bgr)
    if src.ndim != 3 or src.shape[2] != 3:
        raise ValueError("bgr 必须是 [H,W,3]，收到 %s" % (src.shape,))
    m = mask[:, :, 0] if (mask.ndim == 3) else mask
    if m.shape[:2] != src.shape[:2]:
        raise ValueError("mask 尺寸 %s 与原图 %s 不一致" % (m.shape[:2], src.shape[:2]))
    bin_mask = (m > 0).astype(np.uint8)
    if int(bin_mask.sum()) == 0:
        return src.copy()                                    # 没画选区 → 原样返回

    _LAST.update(_mem_guard("inpaint_enter"))

    sess = _session()
    img_in, mask_in, (h, w) = _to_model_input(src, m)

    t0 = time.time()
    try:
        outs = sess.run(None, {"image": img_in, "mask": mask_in})
    except Exception as e:                                  # noqa: BLE001
        # 运行期失败（典型：DML 不支持的算子）→ 换纯 CPU 会话重试一次
        _SESSION_INFO["run_fallback"] = "%s: %s" % (type(e).__name__, e)
        global _SESSION
        with _LOCK:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = max(2, min(8, (os.cpu_count() or 4)))
            so.log_severity_level = 3
            _SESSION = ort.InferenceSession(str(model_path()), sess_options=so,
                                            providers=["CPUExecutionProvider"])
        sess = _SESSION
        _SESSION_INFO["providers"] = sess.get_providers()
        outs = sess.run(None, {"image": img_in, "mask": mask_in})
    dt = time.time() - t0
    out = np.asarray(outs[0])[0]                             # 3,S,S
    out = np.transpose(out, (1, 2, 0))                       # S,S,3
    out = np.clip(out, 0, 255).astype(np.uint8)              # 模型输出本来就是 0-255
    out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    big = cv2.resize(out_bgr, (w, h), interpolation=cv2.INTER_LINEAR)

    if not only_inside:
        res = big
        del out, out_bgr, outs
    else:
        # 只在选区里替换；边缘用羽化 mask 做线性混合，避免硬接缝。
        # ★ 2026-10-27 改成分块混合：一次只吃 TILE×TILE 的块，
        #   峰值内存与图大小**无关**（原来 1440×1440 要同时驻留 4 个 float32 全图，
        #   约 83MB 连续分配，是那次整机崩掉的直接原因）。
        base = bin_mask.astype(np.float32)
        if blend and blend > 0:
            k = int(blend) * 2 + 1
            soft = cv2.GaussianBlur(base, (k, k), 0)
            # ★ 2026-10-26 实测修正：GaussianBlur 会把权重"漏"到 mask 外面去
            #   （实测 mask 外像素被判为已改动）。用 mask 做上限 = **只向内羽化**，
            #   保证 mask 之外逐字节还是原图 —— 这是「原图未受影响」的硬约束。
            soft = np.minimum(soft, base)
        else:
            soft = base
        res = src.copy()                       # 先复制原图，只在选区块上覆盖
        TILE = 256
        for ty in range(0, h, TILE):
            ty1 = min(ty + TILE, h)
            for tx in range(0, w, TILE):
                tx1 = min(tx + TILE, w)
                # 这一块完全在选区外 → 原图不动，一个字节都不碰
                if not soft[ty:ty1, tx:tx1].any():
                    continue
                a = soft[ty:ty1, tx:tx1][:, :, None]
                b = big[ty:ty1, tx:tx1]
                s = src[ty:ty1, tx:tx1]
                mixed = (b.astype(np.float32) * a + s.astype(np.float32) * (1.0 - a))
                res[ty:ty1, tx:tx1] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
                del mixed, a, b, s
        del soft, base
        del out, out_bgr, outs

    _LAST.update({"seconds": round(dt, 3), "mask_px": int(bin_mask.sum()),
                  "size": [w, h], "output": float(src.shape[1])})
    _LAST.update(_mem_guard("inpaint_exit"))
    return res


_LAST: dict = {}


def last_stats() -> dict:
    return dict(_LAST)


def warmup() -> dict:
    """预热：加载模型 + 跑一次空推理。

    ★ 为什么要它：实测首帧耗时 28.8 秒（含 26.6 秒 session 加载），而之后每张只要 3.0 秒。
      界面第一次点「应用修补」会卡近 30 秒，用户会以为卡死。
      建议在编辑器打开时（或用户第一次载图时）用后台线程调一次。
    """
    t0 = time.time()
    try:
        _session()
        img = np.full((LAMA_INPUT, LAMA_INPUT, 3), 128, np.uint8)
        mk = np.zeros((LAMA_INPUT, LAMA_INPUT), np.uint8)
        mk[200:300, 200:300] = 255
        inpaint(img, mk, blend=0)
        return {"ok": True, "seconds": round(time.time() - t0, 2),
                "load": _SESSION_INFO.get("load_seconds"), "providers": _SESSION_INFO.get("providers")}
    except Exception as e:                                  # noqa: BLE001
        return {"ok": False, "seconds": round(time.time() - t0, 2),
                "error": "%s: %s" % (type(e).__name__, e)}


# ================================================================ 命令行自测

if __name__ == "__main__":
    import sys
    print("=== available() ===")
    a = available()
    for k, v in a.items():
        print("  %-10s %s" % (k, v))
    if not a["ok"]:
        sys.exit(2)
    print("\n=== 加载 ===")
    _session()
    for k, v in info().items():
        print("  %-14s %s" % (k, v))

    print("\n=== 合成图冒烟（灰底 + 白字块）===")
    import cv2
    img = np.full((800, 800, 3), 180, np.uint8)
    cv2.rectangle(img, (120, 300), (680, 380), (40, 40, 40), -1)
    mk = np.zeros((800, 800), np.uint8)
    mk[300:381, 120:681] = 255
    t0 = time.time()
    out = inpaint(img, mk)
    print("  耗时 %.3fs  mask_px=%d  输出=%s" % (time.time() - t0, mk.sum() // 255, out.shape))
    # 验证：选区内的原黑块应该被抹平（均值接近背景 180），选区外必须逐字节不变
    inner = out[305:376, 130:670]
    outside_same = np.array_equal(out[:299], img[:299])
    print("  选区内均值 = %.1f（原为 40，背景 180）" % inner.mean())
    print("  选区外未变 = %s" % outside_same)
    print("  → %s" % ("OK" if (inner.mean() > 120 and outside_same) else "有问题"))
