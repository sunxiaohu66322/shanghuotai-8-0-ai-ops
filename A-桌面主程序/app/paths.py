# -*- coding: utf-8 -*-
"""路径与全局常量：把散落在三处的既有程序串起来，一个地方改。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "上货台"
APP_VER = "0.6.0"

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller 打包后：程序目录 = exe 所在目录（data/ 日志落在 exe 旁边，
    # 不落在 _internal 里，方便用户找），静态资源在 _MEIPASS/app/assets。
    HERE = Path(sys.executable).resolve().parent           # ...\上货台\（exe 所在）
    ROOT = HERE
    ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "app" / "assets"
else:
    HERE = Path(__file__).resolve().parent                 # .../上货台/app
    ROOT = HERE.parent                                     # .../上货台
    ASSETS = HERE / "assets"

# ---------------- 三个外部工作区（既有程序，不搬不改） ----------------
# 采集 + 初筛（淘宝），后端是 Python 模块
TAOBAO_DIR = Path(r"G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker")
TAOBAO_SERVER = TAOBAO_DIR / "server"
# 4.1 主图 AI 重做：复用那套独立模块（不复制代码），库是它自己的 image_lab.db
IMAGE_LAB_DIR = TAOBAO_DIR / "image_lab"
IMAGE_LAB_DB = TAOBAO_DIR / "data" / "image_lab.db"
TAOBAO_RESULT = TAOBAO_DIR / "采集结果"          # 老落点（只好兼容，新导出不再写这里）
# 包仓库：流水线各道工序的交接台（导入原始 / URL初筛 / 商品细包 / 精筛结果 / 出表）
TAOBAO_PACKS = TAOBAO_DIR / "packs"
PACK_STAGE_RAW = TAOBAO_PACKS / "00_导入原始"
PACK_STAGE_URL = TAOBAO_PACKS / "01_URL初筛"
PACK_STAGE_PACK = TAOBAO_PACKS / "02_商品细包"
PACK_STAGE_FINE = TAOBAO_PACKS / "03_精筛结果"
PACK_STAGE_EXPORT = TAOBAO_PACKS / "04_出表"
DOWNLOADS = Path.home() / "Downloads"

# 小猪本地化：数据包 / 打码 / 出表
XIAOZHU_DIR = Path(r"J:\aixitong\小猪")
PIG_LOCAL = XIAOZHU_DIR / "4.0.5本地化"          # 本地服务.py / 出表 / 启动脚本
PIG_PACKS = PIG_LOCAL / "packs"                  # 数据包目录
PIG_CLIENT = XIAOZHU_DIR / "xiaozhu4"            # PicDetector.exe + 模型
PIG_READER = XIAOZHU_DIR / "本地鉴图"             # 商品.db / 导入 / 取数 / 出表
PIG_DB = PIG_READER / "data" / "商品.db"
PIG_WORDS = XIAOZHU_DIR / "词库"

LAUNCH_BAT = PIG_LOCAL / "启动本地版.cmd"
EXPORT_SCRIPT = PIG_LOCAL / "出表_微信小店.py"

# ---------------- 本程序自己的目录 ----------------
DATA = ROOT / "data"
LOG_DIR = DATA / "logs"
SETTINGS_FILE = DATA / "settings.json"
DATA.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------- 运行参数 ----------------
PY = Path(sys.executable)

# 无窗口标志：所有子进程必须带这个，否则在无控制台的父进程下会闪黑框
CREATE_NO_WINDOW = 0x08000000


def now_str() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_backend_path() -> bool:
    """把 taobao-picker/server 加进 sys.path（intake 用的是平铺 import）。"""
    if not TAOBAO_SERVER.exists():
        return False
    p = str(TAOBAO_SERVER)
    if p not in sys.path:
        sys.path.insert(0, p)
    return True


def rel(p) -> str:
    """尽量显示成短路径。"""
    try:
        p = str(p)
        for base in ("G:\\Users\\Administrator\\Documents\\deepseek-harness\\default-workspace\\",
                     "J:\\aixitong\\"):
            if p.startswith(base):
                return p[len(base):]
        return p
    except Exception:
        return str(p)
