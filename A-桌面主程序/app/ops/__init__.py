# -*- coding: utf-8 -*-
"""上货台 8.0 · AI 运营中心 · ops_engine（生产版 2026-11-10）

架构：ops.db 独立可写 + v3.db 商品主库只读 ATTACH（绝不写、绝不塞表）。
模块：
    config   环境变量与生产路径（默认 ops.db / 生产 v3.db 只读）
    queue    连接层（挂只读视图）+ ops_task 队列状态机/租约/去重/退避/回收
    store    只读查询：商品/视频/任务/指标/账号 + 运营总览
    migrate  只建 ops.db、同步 9191 三店台账、核对 v3.db 前后哈希
    worker   P0 探针 worker（只认 probe.* 三种 kind）
    engine   9192 HTTP 服务 + 进程内线程启停（主程序负责启停监控）
"""
from . import config

__version__ = config.ENGINE_VERSION
