# 微信小店 · 自营上货台（本地）

直连微信官方 `channels/ec` 接口的本地商品管理/上货工具。不经过任何第三方 SaaS，
凭据只写在本机 `config.json`，所有写操作都必须你点按钮才发生。

- 地址：http://127.0.0.1:9191
- 门牌：http://127.0.0.1:9191/llms.txt
- 启动：双击 `启动.cmd`（或 `F:\pyenvs\douyin\Scripts\python.exe app.py`）
- 端口：9191（9188/9189/9190 已被本机其它服务占用）

## 已实现（全部对真实店铺跑通）

| 模块 | 能力 | 对应官方接口 |
|:--|:--|:--|
| 概览 | 店铺名称/主体/状态、商品数按状态统计 | `/channels/ec/basics/info/get` |
| 商品管理 | 列表（未上架/已上架/回收站三路合并）、翻页、标题/ID 搜索、主图、价格区间、库存、SKU 数 | `/channels/ec/product/list/get` + `/channels/ec/product/get` |
| 商品详情 | 主图、详情图、类目、属性、SKU、原始 JSON | 同上（`data_type=2`） |
| 上/下架 | 单件与批量 | `/channels/ec/product/listing` `/delisting` |
| 批量改价 | 统一设/加减/百分比，先预览后执行，逐件结果 | `/channels/ec/product/update`（skus 最小改动，失败自动回退完整对象） |
| 批量改库存 | 覆盖/增量/减量 | `/channels/ec/product/stock/update` |
| 批量改标题 | 整条替换/前缀/后缀/片段替换（旧=>新），≤60 字校验 | `/channels/ec/product/update` |
| 新建上货 | 选末级类目 → 类目必填属性/销售属性 → 传图 → 标题/价格/库存 → 提交 | `/shop/ec/category/all` `/shop/ec/category/detail` `/shop/ec/basics/img/upload` `/channels/ec/product/add` |
| 类目浏览 | 5041 条末级类目搜索（带完整路径与资质要求） | `/shop/ec/category/all` |
| 任务记录 | 每次批量操作落 `data/tasks.jsonl`，含逐件结果 | 本地 |
| 设置 | AppID/AppSecret 保存并测试连接（只写不读） | 本地 |

## 目录

```
微信小店自建上货台\
  app.py              本地服务（stdlib http.server，无第三方依赖）
  wxapi.py            微信小店 OpenAPI 客户端（路径全部实测过）
  config.json         appid / secret / port  ← 只在本机，勿提交到任何仓库
  web/                前端（原生 JS，无构建）
  data/tasks.jsonl    批量操作留痕
  docs/               逆向报告 + 功能地图
  启动.cmd
```

## 安全

- 服务只监听 `127.0.0.1`，外网访问不到。
- `config.json` 里是明文 AppSecret。**这台机器上的任何程序都能读它**，请勿把该目录同步到网盘/仓库。
- 建议：跑顺之后到微信后台重置一次 AppSecret，并配置 **IP 白名单**（不配 = 谁拿到都能用）。
- 删除商品不在批量里做：必须在商品详情里逐件确认。

## 未实现（下一批）

订单/售后、运费模板、优惠券、SKU 增删改、商品复制（跨店）、1688 铺货、商品诊断/质量检测、水印、
Excel 导入导出。清单与优先级见 `docs/功能地图.md`。

---

## 2026-10-26 · 结构对齐小猪（taosoft）版

左侧菜单**逐字照抄** shipinhao.taosoft.com.cn 的侧边栏（视频号平台那一套）：12 个分组 / 67 个页面。
每项带实现程度角标：**可用 / 补丁 / 只读 / 壳**。详见 `docs/实现对照.md`。

| | 数量 |
|:--|--:|
| 页面 | 67 |
| 可用（真写入） | 28 |
| 补丁（title/sub_title/weight/spu_code） | 3 |
| 只读 | 4 |
| 壳（骨架齐、动作置灰并写明缺什么） | 32 |

**前端文件**（原生 JS，无构建）：
```
web/util.js    公共工具（fetch / 格式化 / toast）
web/menu.js    菜单树（取自原版 bundle）+ 每项 page/act/state/need
web/pages.js   页面渲染（工作台 / 外壳 / 各专页）+ ACT 动作注册表
web/app.js     哈希路由 + 侧边栏 + 角标
web/style.css  布局（含两栏工作台）
```

**本轮新增接口**
```
GET  /api/overview        店铺 + 三态计数（只读 id 列表，快）
GET  /api/diagnose        本地规则诊断（无标题/超60字/无主图/无SKU/价格0/库存0/无类目），结果缓存 90s
GET  /api/yunku           本机备份清单
POST /api/yunku/save      {ids} → data/yunku/bk-*.json（先备份再批量改）
POST /api/yunku/remove    {name}
POST /api/patch           {ids,patch,dry_run} 白名单字段最小改动
POST /api/export          {ids,status} → data/exports/exp-*.csv
GET  /api/exportRecords   导出记录
POST /api/import/preview  {text} → 识别列/行数（不写库）
GET  /data/exports/<file> 直接下载导出文件
```

**注意**：`/api/products` 有 120s 缓存；商品列表按 `page_size=30`（三态各 10）取，翻页用 `cursor`。

---

## 6.0 · 图片编辑系统 + 微店后台管理序列（2026-10-26）

**把桌面上货台那套「框选 → 修补」的编辑能力搬进网页版**，并新增「微店后台管理」一整条序列，类目/属性/资质全部贯通。

### 图片编辑（菜单：图片编辑 6.0）
```
照片墙        /photo/wall      三个图源（上货台素材 2998 张 · 采集库 · 本台上货），缩略图墙 + 多选
框选修补      在图上拖矩形（可连拖多个累积）→ 修补本张 / 应用到选中（同一组框按比例套到每张图）
引擎          LaMa（默认，AI 生成式）· TELEA（快）· NS；可设外扩像素
检测打码      /photo/mask      先跑「检测候选」（高梯度+形态学合并），人工确认后再消除
落盘          结果存 data/photos/out/，原图不动；除非显式 inplace
```
- 实测：LaMa 首张 26.3s（启动已后台预热），之后 **2.9s/张**；TELEA 2ms
- 检测实测：真实商品图上返回 4 处候选（底部文字/角标）
- 模型复用 `taobao-picker\models\lama_fp32.onnx`（208MB），走 onnxruntime CPU；DML 因 FFT 算子不可用

### 微店后台管理（菜单：微店后台管理）
```
后台概览      /wx/overview     店铺·商品三态·类目数·修补能力，一屏
商品列表      /products
类目·属性·资质 /wx/categories  左列 5041 末级类目，右列真实字段：商品属性 / 销售属性 / 保证金 / 资质
类目树        /cats
素材库        /wx/materials    上传即进微信 CDN（mmecimage.cn），记录落 data/uploads.jsonl
订单/运费/券/新建上货/连接设置
```
- 新建上货已贯通类目：填 cat_id → 「校验类目并载入必填属性」→ 真实属性表（含必填标记）→ 提交时带 attrs
- 实测（cat 547511）：属性 6 条 / 必填 3 条 / 保证金 2000 元

### 新增后端
```
photo.py                       照片墙/修补/批量/检测（归一化坐标，跨图复用）
GET  /api/photo/status|roots|list|file|out
POST /api/photo/inpaint|batch|detect
GET  /api/uploads              微信素材上传记录
GET  /api/overview             店铺 + 三态计数
POST /api/edit                 通用编辑引擎（上一版）
```

### 已知边界
- LaMa 输入固定 512×512：大图先缩后放，2000px 以上会有可见柔化
- 检测是启发式「高对比区域」，**只当候选**，必须人工确认
- 素材库列表只列本台上传记录（微信没有公开的素材枚举接口）
