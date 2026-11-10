# -*- coding: utf-8 -*-
"""P0 配置：一律走环境变量，默认值指向「开发副本」，绝不默认连生产库。

环境变量
    OPS_DB        数据库路径（默认 <dev>/data/v3_dev.db）
    OPS_PORT      监听端口（默认 9192）
    OPS_HOST      监听地址（默认 127.0.0.1，只本机）
    OPS_WORKER    1 = 启动内置探针 worker（P0 只有 probe.* 三种 kind）
    OPS_RECOVER_S 回收线程间隔秒（默认 3）
    OPS_LEASE_S   默认租约秒数（默认 30）
    OPS_RO       1 = 商品/视频查询强制只读连接（默认 1）
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent          # dev/ops
DEV_ROOT = HERE.parent                          # dev
PKG_ROOT = DEV_ROOT.parent                      # 上货台7.0-P0-20261109

ENGINE_NAME = "上货台7.0-ops_engine"
ENGINE_VERSION = "P0-0.1.0"
SCHEMA_VERSION = "1"


def db_path() -> Path:
    p = os.environ.get("OPS_DB")
    if p:
        return Path(p)
    return DEV_ROOT / "data" / "v3_dev.db"


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
    return PKG_ROOT / "证据"


def ensure_dirs() -> None:
    (DEV_ROOT / "data").mkdir(parents=True, exist_ok=True)
    evidence_dir().mkdir(parents=True, exist_ok=True)
