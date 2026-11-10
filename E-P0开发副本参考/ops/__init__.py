# -*- coding: utf-8 -*-
"""上货台 7.0 · AI 运营中心 · ops_engine（P0）

模块：
    config   环境变量与默认路径（默认指向开发副本，不默认连生产库）
    queue    ops_task 队列：状态机 / 租约 / 去重 / 退避 / 过期回收
    store    只读查询：商品 / 视频 / 任务 / 指标 / 账号
    migrate  ops_* 表迁移（只 CREATE IF NOT EXISTS）
    worker   P0 探针 worker（只有 probe.* 三种 kind）
    engine   9192 HTTP 服务
"""
from . import config

__version__ = config.ENGINE_VERSION
