# -*- coding: utf-8 -*-
"""v3 上货流水线（后端）

按《上货流水线-v3-本地AI执行任务书》落地，基座是本项目既有的
`taobao-picker/server`（Python + Flask + SQLite），不引入新框架、不换语言。

对外三个入口：
    from v3 import flow          # 纯 Python，无需起服务（上货台客户端走这条）
    from v3.api import register_v3 ; register_v3(app)   # HTTP
    python -m v3.cli --help      # 命令行自检

模块：
    store      数据层（v3.db，独立库，不动 products.db / 商品.db）
    importer   数据包导入（小猪库 / CSV / JSON 三源）
    keywords   统一词条注册表（brand/violation/core/attribute/scene/synonym）
    profile    商品画像（属性必须能在原文找到，不编造）
    titles     标题引擎（三策略 + 六项校验 + 多店 + 可接外部 AI）
    images     主图交接（路径 / 导入候选 / 确认 / 撤销，不动角色与序号）
    packaging  19 列出包 + 待上传图清单 + 发布台账
    flow       流水线门面（阶段、恢复、一键）
    api        Flask 路由注册
"""
__version__ = "3.0.0"
SCHEMA_VERSION = "3.0"
