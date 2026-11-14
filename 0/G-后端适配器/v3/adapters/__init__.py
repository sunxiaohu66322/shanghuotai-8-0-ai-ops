"""平台解析适配器 —— 每个平台一个文件，互不影响。

为什么要这一层：第二步的输入从"商品列表 CSV"改成"单商品详情卡"，
而不同平台的卡字段完全不同（微店在 `_raw` 里、淘宝在别的结构里）。
把"认字段"集中在这里，`v3_product` / `v3_image` 这些通用表就不必为每个平台加列。

对外只暴露：
    detect(payload)              -> 平台码 | None
    parse_card(payload)          -> 标准化商品 dict（见 wechat.parse_card 文档）
    scan_cards(dir, recursive)   -> 扫文件夹里的卡文件（默认只扫一层）
    display_name(code)           -> 中文名（界面/日志用）

三种来源（用户口径）：拼多多 pdd · 淘宝 taobao · 微店 weidian
  · weidian  已实现（原 `wechat.py` 是它的字段事实层，`weidian.py` 加平台身份与视频）
  · wechat   保留给 10-24 之前导入的老数据（同一份提取器，平台码原样写 wechat）
  · pdd      已实现（2026-11-09）：多米数据助手导出的拼多多详情原始响应
             `D:\\多米采集导出\\*.txt`，扩展名是 .txt 但内容是完整 JSON
  · pdd_legacy 已实现（2026-11-14）：百度网盘老版页面快照
             `H:\\bdpan-txt-download\\<批次>\\<goods_id>.txt`，
             顶层 store.initDataObj.goods（goodsID/topGallery/detailGallery/skus）
  · taobao   尚未拿到真实卡样本 → 不注册，detect 返回 None（判"认不出来"，不硬猜）
"""

from . import wechat  # noqa: F401
from . import weidian  # noqa: F401
from . import pdd  # noqa: F401
from . import pdd_legacy  # noqa: F401

# 注册表：平台码 -> 模块。顺序 = 判定优先级。
# pdd 放首位：它的 detect 要求 goods.goods_id + server_time/ui，形状最特异。
# pdd_legacy 第二：老版页面快照顶层是 store.initDataObj，形状同样特异，
# 两个 detect 互不重叠（一个要 goods.goods_id，一个要 goods.goodsID/query.goods_id），
# 不会误判微店卡（微店要 _raw/productId）。
ADAPTERS = {
    "pdd": pdd,
    "pdd_legacy": pdd_legacy,
    "weidian": weidian,   # 微店/微信小店（当前真实数据来源）
    "wechat": wechat,     # 老数据兼容（同提取器，平台码保留）
}

DISPLAY = {
    "weidian": "微店",
    "wechat": "微店（老数据）",
    "pdd": "拼多多",
    "pdd_legacy": "拼多多（老版详情卡）",
    "taobao": "淘宝",
}


def display_name(code: str | None) -> str:
    if not code:
        return "来源未知"
    return DISPLAY.get(code, code)


def detect(payload: dict) -> str | None:
    """猜这份 payload 是哪个平台的卡。认不出返回 None（调用方据此判"无效文件"）。"""
    for code, mod in ADAPTERS.items():
        try:
            if mod.detect(payload):
                return code
        except Exception:  # noqa: BLE001  适配器自己有 bug 不该拖垮整批导入
            continue
    return None


def detect_file(path: str) -> tuple[str | None, dict | None, str]:
    """读文件 + 判定来源。返回 (平台码, payload, 错误说明)。

    认不出来时错误说明里带「看到哪些顶层键」，方便下次补适配器，而不是干瞪眼。
    """
    import json
    import os

    if not os.path.isfile(path):
        return None, None, "文件不存在"
    try:
        with open(path, "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                payload = json.loads(raw.decode(enc))
                break
            except UnicodeDecodeError:
                payload = None
                continue
            except json.JSONDecodeError as e:
                return None, None, "JSON 解析失败：%s" % e
        if payload is None:
            return None, None, "编码认不出（试过 utf-8 / gb18030）"
    except Exception as e:  # noqa: BLE001
        return None, None, "读取失败：%s" % e
    if not isinstance(payload, dict):
        return None, None, "顶层不是对象（是 %s）" % type(payload).__name__
    code = detect(payload)
    if not code:
        keys = ",".join(list(payload.keys())[:12])
        return None, payload, "认不出平台（顶层键：%s）" % (keys or "空")
    return code, payload, ""


def parse_card(payload: dict, source_file: str = "") -> dict:
    """按平台分派解析。认不出就抛 ValueError，由调用方记一条 invalid 并跳过。"""
    code = detect(payload)
    if not code:
        raise ValueError("认不出这是哪个平台的商品卡（顶层没有微店/淘宝/拼多多的特征字段）")
    return ADAPTERS[code].parse_card(payload, source_file=source_file)


def scan_cards(folder: str, recursive: bool = False) -> dict:
    return wechat.scan_cards(folder, recursive=recursive)
