# -*- coding: utf-8 -*-
"""8.0 · AI 运营中心 · 配置（生产版，2026-11-10）

与旧 P0 的唯一根本差异：**双库**。
    ops.db   = 本程序自有，可写（12 张 ops_* 表 + 任务队列 + 事件 + 执行记录）
    v3.db    = 商品主库，**只读 ATTACH**，绝不写、绝不 ALTER、绝不塞 ops_ 表

环境变量（全部可覆盖，默认即生产安全值）
    OPS_DB          运营库路径（默认 <上货台>/data/ops.db）
    OPS_V3_DB       商品主库路径（默认 生产 taobao-picker/data/v3.db）
    OPS_PORT        监听端口（默认 9192）
    OPS_HOST        监听地址（默认 127.0.0.1，只本机）
    OPS_WORKER      1 = 启动内置探针 worker（P0 只认 probe.* 三种）
    OPS_RECOVER_S   回收线程间隔秒（默认 3）
    OPS_LEASE_S     默认租约秒数（默认 30）
    OPS_RO          1 = 商品/视频查询强制只读连接（默认 1）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # .../上货台/app/ops
APP_DIR = HERE.parent                                  # .../上货台/app
ROOT = APP_DIR.parent                                  # .../上货台
WS = ROOT.parent                                       # default-workspace
DATA = ROOT / "data"
LOG_DIR = DATA / "logs"
DB_PATH = DATA / "ops.db"
LOCK_PATH = DATA / "ops_engine.lock"
LOG_PATH = DATA / "ops_engine.log"

# ★ 生产商品主库真源（与 paths.py / tab_wx.py 同一路径，路径纠偏沿用）
V3_DB = Path(os.environ.get("OPS_V3_DB")
             or (WS / "taobao-picker" / "data" / "v3.db"))
# 9191 店铺台账（只读 HTTP，不直连它的库）
WX_WEB_BASE = "http://127.0.0.1:9191"

ENGINE_NAME = "上货台8.0-ops_engine"
ENGINE_VERSION = "8.0-P0.4.0"                          # 模块3 每日真实运营闭环（真巡店/任务/日报）
SCHEMA_VERSION = "5"                                   # 5 = 模块3 每日运营表
FROZEN = bool(getattr(sys, "frozen", False))


def db_path() -> Path:
    p = os.environ.get("OPS_DB")
    return Path(p) if p else DB_PATH


def v3_path() -> Path:
    p = os.environ.get("OPS_V3_DB")
    return Path(p) if p else V3_DB


def port() -> int:
    return int(os.environ.get("OPS_PORT") or 9192)


def host() -> str:
    return os.environ.get("OPS_HOST") or "127.0.0.1"


def worker_enabled() -> bool:
    return (os.environ.get("OPS_WORKER") or "0") == "1"


def recover_interval() -> float:
    return float(os.environ.get("OPS_RECOVER_S") or 3)


def default_lease() -> int:
    return int(os.environ.get("OPS_LEASE_S") or 30)


def ro_enabled() -> bool:
    return (os.environ.get("OPS_RO") or "1") == "1"


def evidence_dir() -> Path:
    return WS / "上货台7.0调查-20261109"


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
