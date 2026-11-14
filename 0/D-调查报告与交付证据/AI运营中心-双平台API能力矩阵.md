# AI运营中心 - 双平台 API 能力矩阵

> 调查员：平台 API 能力核查员（叶子节点，只读调查）
> 调查时间：2026-11-09 17:35–18:05
> 证据口径：`[官方文档]` / `[本机实测]` / `[源码确认]` / `[第三方整理]` / `[尚未验证]`
> 本报告**未调用任何真实下单/发布接口**，**未输出任何 AppSecret / Cookie 明文**（读到即写"已脱敏，见 shops.json 配置项"）。

## 结论摘要（≤200 字）

微信小店侧**最硬**：商品增删改查/上下架/库存/类目/图片上传/运费模板/订单，本机两个真实店铺 appid 已实测调通（`wxapi.py` 头注释「本机实测通过 2026-10-26」）；但**曝光/点击类数据接口实测报 48001 未授权**，优选联盟、带货达人、直播数据、compass 电商概览**接口存在、本机未调未授**。
抖音侧**几乎为零**：全仓 grep 无一处 `app_key/app_secret/sign_method/op.jinritemai` 调用，抖店商品/订单/数据全靠 ego 浏览器跑页面快照；视频发布要走**另一套体系**（open.douyin.com 的 `video.create`，与抖店开放平台账号、密钥、权限包完全分离）。
**视频号发布没有公开 API**（官方视频号能力目录只有直播/橱窗/留资/大屏/罗盘达人版），只能走视频号助手网页。
**结论：7.0 的"数据收集"与"视频发布"是两块最大缺口，且两块缺口性质不同 —— 微信是"有接口没权限"，抖音是"根本没接入 + 视频要另开账号体系"。**

---

## 一、核查方法与证据来源

### 1.1 两条腿（缺一条即标"尚未核实"）

| 腿 | 做了什么 | 结果 |
|:--|:--|:--|
| **官方文档** | web_search + web_fetch 读微信开放文档（微信小店 API 全表、优选联盟页、视频号 API 目录、抖音开放平台 video.create、用户类型及权限说明）、抖店开放平台 `op.jinritemai.com/docs/api-docs` | 微信侧拿到**接口路径原文表格**；抖音侧拿到 `video.create` 原文与权限表；抖店官方站是纯前端渲染，`web_fetch` 只回标题（**未取到官方原始接口表**，故抖店接口名标 `[第三方整理]`） |
| **本机代码** | 读 `微信小店自建上货台\wxapi.py`、`union3.py`、`dd.py`、`publish.py`、`app.py`（10 万字节，定向读路由与端点段）、`上货台\app\wxserve.py`；全仓 grep 抖店开放平台签名关键字 | 拿到**已调通端点清单**（最硬证据：能调通=有权限）+ **一次真实 48001 未授权记录**（最强否定证据） |

### 1.2 官方文档链接（真实抓取）

- 微信小店 API 全表：https://developers.weixin.qq.com/doc/store/shop/API
- 微信小店 · 优选联盟：https://developers.weixin.qq.com/doc/store/shop/API/league
- 微信小店 · 商品管理：https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product
- 视频号 · API 接口目录：https://developers.weixin.qq.com/doc/channels/ （`/doc/channels/api/`）
- 视频号 · 视频号助手 API：https://developers.weixin.qq.com/doc/channels/api/channels/
- 抖音开放平台 · 创建视频：https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/create-video
- 抖音开放平台 · 用户类型及权限说明：https://open.douyin.com/platform/resource/docs/accession-guide/type-and-permission
- 抖店开放平台 API 文档入口：https://op.jinritemai.com/docs/api-docs

### 1.3 本机代码证据（关键几处）

| 证据 | 位置 | 说明 |
|:--|:--|:--|
| 微信端点"本机实测通过"清单 | `微信小店自建上货台\wxapi.py:4-19` | 头注释逐条列出 15 个已实测路径 |
| 微信 token 获取 | `wxapi.py:74` `/cgi-bin/stable_token` | 本项目自己实现，非第三方 SDK |
| 商品读未上架 | `wxapi.py:125` `data_type=2` | 注释"`data_type=2` 才能读未上架" |
| 订货时间窗限制 | `app.py:2118` | "订单接口时间窗上限 7 天（超过报 40097）" |
| **曝光数据实测失败** | `app.py:2117` | 原文：`平台没有开放「展现/曝光」数据接口（实测 /channels/ec/product/data/get 报 48001 未授权，其余候选路径 40066 invalid url）` |
| 地址接口实测 | `app.py:1020` | `/channels/ec/merchant/address/list` |
| 双店凭据结构 | `shops.json` | `current`/`port`/`shops[{appid,secret,name}]`；**secret 已脱敏，未写入本报告** |
| 抖店无任何官方 API | 全仓 grep `open_api|op.jinritemai|app_key|app_secret|sign_method|/product/addProduct` | 命中全为 `server/v3/doudian.py`（16 列 xlsx **导表**模块）与 `抖店研究/engine/*.mjs`（ego 浏览器脚本），**零签名、零 AppKey** |
| 抖店数据源写死为快照 | `微信小店自建上货台\dd.py:4-13` | 原文：`抖店没有可直连的免签接口，真实数据只能来自 ego 自动化跑出来的页面快照` |
| 抖店上架走浏览器 | `dd.py:139-143` | 落 16 列 xlsx → `ego/main/excel-route-4.mjs` POST 到小猪 `excelCopyItem/dyForm`，`goTarget=sale` |
| 三店去重的抖店口径 | `union3.py:8-10, 116` | 抖店在售来自 `dy-sold-list.csv`（ego 抓的页面快照），"可能只覆盖第一页" |

---

## 二、抖音能力矩阵

> **前提事实（决定整张表）**：抖店有**两套完全独立的开放体系，账号/密钥/权限包互不相通**：
> ① **抖店开放平台**（`op.jinritemai.com`）—— 店铺商品/订单/售后/物流，授权方式为「自用型应用：AppKey+AppSecret+ShopID→access_token」或「工具型应用：授权 URL 换 code」；
> ② **抖音开放平台**（`open.douyin.com`）—— 视频发布/账号授权/视频数据，授权方式为 OAuth（用户扫码授权，scope 逐项申请）。
> 本机**两条都没建应用**。

| # | 能力 | 状态 | 接口名 / 路径 | 权限要求 | 证据 | 缺失影响 |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | 抖店商品 API（列表/详情/创建/编辑） | `官方API存在但账号未取得权限` | `/product/listV2`、`/product/detail`、`/product/addV2`、`/product/editV2`（[第三方整理]，官方站取不到原文） | 抖店开放平台自用型应用：AppKey+AppSecret+ShopID，IP 白名单 | [本机实测] 全仓无 `app_key/app_secret/sign_method`；`dd.py` 明写"没有可直连的免签接口" | 商品上架必须继续走 xlsx+ego，无法做增量同步/批量改价改库存 |
| 2 | 商品列表与详情 | `官方API存在但账号未取得权限` | 同 #1（`/product/listV2`、`/spu/getSpuInfoBySpuId`） | 同上，「商品信息查询」权限包 | [本机实测] `union3.py:26` 抖店在售只认 `dy-sold-list.csv` 快照，"可能只覆盖第一页" | 7.0 商品库的抖音侧永远是**残缺快照**，无法与微信侧对齐件数 |
| 3 | 商城活动相关接口（活动报名） | `尚未核实` | 未见官方接口证据（营销类目有优惠券/限时折扣一类，与"平台活动报名"不同） | — | [尚未验证] 官方站前端渲染取不到；第三方清单只列商品/店铺/SKU/机会品 | 活动报名只能人工在后台点；无法自动化抢活动坑位 |
| 4 | 搜索商机数据开放接口 | `尚未核实` | 有迹象：`/opptyProduct/apply`（机会品提报）、`/opptyProduct/clue`（机会品线索触达）、`/opptyProduct/getApplyProgress`（[第三方整理]，归属「机会品管理」权限包） | 未核实 | [第三方整理] 非官方原文，不敢当"已开放"用 | 商机只能靠人工看罗盘，AI 无法主动获取"该上什么品" |
| 5 | 猜你喜欢入池数据开放接口 | `尚未核实` | 无接口证据。仅知**罗盘口径**："商城推荐流量"= 抖音商城-推荐（猜你喜欢）渠道，不含百亿补贴/超值购 | — | [官方文档] 该口径出自抖店开放平台 FAQ 原文；**接口是否存在未核实** | "入池"是运营动作不是查询，现状只能人工在后台看是否入池 |
| 6 | 精选联盟商品推广 | `尚未核实` | 抖店侧未见官方开放接口原文；商家侧佣金/公开合作在后台设置 | 未核实 | [第三方整理] 只有商家后台操作说明，无 API 表 | 达人分销只能人工设佣金，无法批量给 N 个商品设计划 |
| 7 | 达人数据 | `尚未核实` | 未取得官方接口原文 | 未核实 | [尚未验证] | 达人筛选/带货能力评估无法进 AI 运营引擎 |
| 8 | 视频发布接口 | `官方API存在但账号未取得权限` | **`POST /video/create/`**（抖音开放平台） | scope **`video.create`**，普通权限、默认关闭、需**管理中心申请** + **用户授权**（access-token 由 `/oauth/access_token/` 产生） | [官方文档] 创建视频页原文："Scope: video.create 需要申请权限 需要用户授权"；"创建抖音视频后，会有一个审核过程，期间只有自己可见" | 7.0"视频发布"做不到全自动；且**开通抖店 API 不等于开通这个** |
| 9 | 发布账号授权 | `官方API存在但账号未取得权限` | OAuth：`/oauth/access_token/`（用户扫码授权换 token） | 需企业号认证 + `video.create` 权限；代用户创建视频须"用户明确感知" | [官方文档] 权限表逐条列 `video.create`（一键发布视频/单图/话题/小程序）默认关闭、管理中心申请 | 无抖音开放平台应用 = 视频链路从第一步就断 |
| 10 | 视频商品关联 | `尚未核实` | 官方 `video.create` **只列三类锚点：小程序 / 游戏 / poi**，原文"不支持携带多种类型的锚点"；**商品/购物车锚点未出现在该接口能力列表** | 未核实（可能另有电商能力或仅客户端） | [官方文档] 创建视频页锚点说明原文 | 即使拿到 `video.create`，"视频挂小黄车"仍需另找通道；现状只能人工在发布页选商品 |
| 11 | 视频发布结果查询 | `官方API存在但账号未取得权限` | `video.list`（查询特定视频数据）、`video.data`（查询授权用户视频数据）；创建返回 `item_id` | 普通权限、默认关闭、管理中心申请 | [官方文档] 权限表原文列出 `video.data` / `video.list`；**"按 item_id 查审核状态"未见官方接口 → 该细分未核实** | 发布后无法确认"过审没有"，只能人工看 |
| 12 | 商品曝光与点击数据 | `尚未核实` | 抖店侧未见官方数据接口原文（罗盘是**数据平台**，是否开放 API 未取证） | 未核实 | [尚未验证]；[官方文档] 只取到罗盘指标口径 FAQ，取不到接口 | 7.0"流量分析/自动优化"没有数据输入 → **这是抖音侧最大缺口** |
| 13 | 商品成交数据 | `官方API存在但账号未取得权限` | 抖店开放平台**订单类接口**（第三方整理：`/order/list`、`/order/searchList` 一类）+ 罗盘成交口径 | 自用型应用 + 订单权限包 | [第三方整理]；[官方文档] 抖店"710+ 接口覆盖商品、订单、售后、退款"（第三方测评文，非官方原文） | 成交只能靠"后台截图/人工导出"，无法做 T+1 自动报表 |

---

## 三、微信能力矩阵

| # | 能力 | 状态 | 接口名 / 路径 | 权限要求 | 证据 | 缺失影响 |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | 微信小店商品 API（列表/详情/创建/编辑） | `官方API开放且账号已有权限` | `/channels/ec/product/list/get`、`/channels/ec/product/get`(data_type=2)、`/channels/ec/product/add`、`/channels/ec/product/update`、`/channels/ec/product/auditfree`（免审更新，官方表有，本机未调） | AppID+AppSecret；本项目**两个真实店铺**均已配置并跑通 | [本机实测] `wxapi.py:4-19` 头注释"所有路径均在本机实测通过（2026-10-26）"；`app.py` 路由 `/api/product/add|update` 生产在用 | 无缺口（这是 7.0 唯一可直接复用的通道） |
| 2 | 商品列表与详情 | `官方API开放且账号已有权限` | `list/get`（status 0/5/11 分页 next_key）+ `get`（`data_type=2` 读未上架） | 同上 | [本机实测] `union3.py:50-67` 真拉全状态、按 appid 分片、`ThreadPoolExecutor` 24 并发取标题 | 无缺口 |
| 3 | 商品搜索运营数据 | `尚未核实` | 官方 compass 有"电商数据概览 / 商品列表 / 商品详细信息"，**未取到"搜索词/搜索渠道"拆分接口** | 未核实 | [官方文档] API 目录原文只列概览/商品/人群/带货达人/开播，无搜索词 | AI 无法拿到"用户搜了什么词找到我"，搜索优化只能靠猜 |
| 4 | 优选联盟 | `官方API存在但账号未取得权限` | `/channels/ec/league/item/batchadd`、`item/get`、`item/list/get`、`item/upd`、`item/headsupplier/batchadd`、`promotion/plan/list`、`promotion/talent/list` | 需小店开通优选联盟 + 接口权限 | [官方文档] 官方表格**路径原文**已抓；[本机实测] 全仓无 league 调用 | 达人分销计划只能人工在后台设 |
| 5 | 带货者数据 | `官方API存在但账号未取得权限` | `/channels/ec/league/promoter/get`、`promoter/list/get`、`talent/getproductdatalist`、`talent/update`；compass `/channels/ec/compass/shop/finder/list`（获取带货达人列表） | 联盟开通 + compass 权限 | [官方文档] 路径原文；[本机实测] 未调用 | 无法知道"哪个达人给我带了多少" |
| 6 | 视频号内容发布 | `仅支持浏览器操作` | **官方视频号 API 目录内无"发布视频/上传视频/草稿"接口**。目录原文仅含：直播基础信息、橱窗管理、留资组件、留资直播数据、直播大屏数据、罗盘达人版、本地生活 | — | [官方文档] `/doc/channels/api/channels/` 完整目录已抓，**无发布类接口**；第三方深度分析亦结论"公开层面没有面向普通开发者的完整发布 API" | 视频号发布**只能**驱动视频号助手网页（浏览器自动化），且平台规则风险自担 |
| 7 | 视频号账号授权 | `尚未核实` | 有 `/channels/api/channels/leadslive/api_getfinderattrbyappid`（获取视频号账号信息，appid→视频号） | 未核实 | [官方文档] 目录原文；账号级 OAuth 授权流程未取到原文 | 无法确认"这个 appid 能操作哪个视频号" → 多号运营要人工绑定 |
| 8 | 视频关联商品 | `仅支持浏览器操作` | 发布侧无接口（同 #6）。**但橱窗侧有 API**：`/channels/api/channels/windowproduct/api_addwindowproduct`（上架商品到橱窗）、`api_getwindowproductlist`、`api_offwindowproduct` | 橱窗接口需权限 | [官方文档] 橱窗接口路径原文；发布/挂载无接口 | 视频挂商品只能人工在发布页操作；橱窗可 API 化（可先做橱窗铺货） |
| 9 | 商品曝光与点击数据 | `官方API存在但账号未取得权限` | 候选：`/channels/ec/compass/shop/product/data/get`（商品详细信息）、`compass/shop/overall/get`（电商概览）。**实测 `/channels/ec/product/data/get` 报 48001 未授权、其余候选 40066 invalid url** | compass 数据权限（需申请） | [本机实测] `app.py:2117` 原文明写失败码；**48001 = 接口存在但账号未授权**（这是最硬的"有接口没权限"证据） | 7.0"曝光优化/流量分析"微信侧同样无数据 → 只能拿"上架率"顶替（本机现在就是这么做的） |
| 10 | 商品成交数据 | `官方API开放且账号已有权限` | `/channels/ec/order/list/get`（必须带 `create_time_range`，**时间窗上限 7 天，超过报 40097**） | AppID+AppSecret（订单读权限已具备） | [本机实测] `wxapi.py:19,233-238` 实测通过；`app.py:2118` 记录 7 天窗限制 | 成交可从订单自行聚合（需按 7 天切片）；**compass 的官方成交口径未实测**，口径与后台可能有差 |
| 11 | 直播相关数据 | `官方API存在但账号未取得权限` | `/channels/ec/compass/shop/live/list/get`（店铺开播列表）；`/channels/api/channels/livedashboard/api_getlivelist` + `api_getlivedata`（直播大屏） | compass / 直播数据权限 | [官方文档] 路径原文；[本机实测] 未调用 | 直播复盘无数据，只能人工看大屏 |
| 12 | 商品分享数据 | `官方API存在但账号未取得权限` | `/channels/ec/league/promotion/talent/list`（获取商品推广带货者列表）、`promotion/plan/list`（推广计划列表） | 联盟开通 + 权限 | [官方文档] 路径原文；[本机实测] 未调用 | 无法统计"哪个商品被谁分享了、带来多少" |

---

## 四、五种「最容易误判」的能力单独拆解

> 主人原话：视频上传、账号绑定、视频关联商品、正式发布、数据查询是**五种不同能力，必须分别验证**。以下逐条分开，不合并。

### 4.1 视频上传（≠ 发布）

| 平台 | 结论 | 证据 |
|:--|:--|:--|
| 抖音 | **有上传/创建接口，但需 `video.create` 权限 + 用户授权**。`POST /video/create/`，Header 带 `access-token`（由 `/oauth/access_token/` 生成）。返回 `item_id`，且**先审核、期间只有自己可见** | [官方文档] 创建视频页原文 |
| 微信小店 | **有"素材上传"接口**：`/doc/store/shop/API/apimgnt/resource/` 下「申请上传视频 / 上传视频数据分块 / 完成视频上传 / 获取上传后的视频临时URL」（本机未调）。这**只是把视频传进小店素材库**，用于商品视频/详情，**不是发到视频号** | [官方文档] 微信小店 API 目录原文 |
| 微信视频号 | **无上传接口**。视频号能力目录里没有上传/草稿/发布任何一项 | [官方文档] `/doc/channels/api/channels/` 完整目录 |

**误判风险**：把"小店素材上传"当成"视频号发布"来做 7.0 的发布模块 → 功能交付后主人会发现"视频传上去了，但号里没有作品"。

### 4.2 账号绑定（三套完全不同的东西）

| 体系 | 绑定方式 | 本机状态 |
|:--|:--|:--|
| 抖店开放平台（店铺） | **自用型应用**：AppKey + AppSecret + ShopID → `access_token`（`getShopAccessToken($shopid)`）；或工具型：授权 URL 换 code | **未建应用**（全仓无 AppKey） |
| 抖音开放平台（创作者/发布） | **OAuth 用户授权**：扫码 → code → `/oauth/access_token/` → token；每个 scope（如 `video.create`）需在管理中心单独申请，默认关闭 | **未建应用** |
| 微信小店（店铺） | **AppID + AppSecret → `/cgi-bin/stable_token`**（本机自实现，磁盘缓存 token 到过期前 300 秒） | **已通，两个真实店铺**（`shops.json` 中 `wx6ff10d****`=欢轴家居、`wxfc4e24****`=崇耿） |
| 视频号（账号） | 目录里只有「获取视频号账号信息」(appid→视频号)，**账号级授权流程未取到原文** | `尚未核实` |

**误判风险**：以为"抖店有 ShopID 就能发抖音视频"。**不能** —— 抖店店铺 token 和抖音创作者 token 是两个平台的两种 token。

### 4.3 视频关联商品

- 抖音：`video.create` 官方**只列三类锚点（小程序 / 游戏 / poi）**，原文"不支持携带多种类型的锚点"。**商品/购物车锚点未出现在该接口能力列表 → 视频挂商品 API 路径 `尚未核实`。**
- 微信：视频号发布侧无接口；**橱窗有 API**（`windowproduct/api_addwindowproduct` 上架商品到橱窗）—— 但"橱窗里有货"≠"这条视频挂了货"。
- **结论：两个平台的"视频挂商品"目前都只能人工在发布页操作（浏览器/客户端），不能作为 7.0 的 API 能力写进方案。**

### 4.4 正式发布（商品发布 vs 视频发布，四种组合）

| | 商品正式发布 | 视频正式发布 |
|:--|:--|:--|
| **抖音** | 有接口（`/product/addV2`、`/product/setOnline`，[第三方整理]），**账号未取得权限** → 现状走 xlsx + ego | 有接口（`video.create`），**账号未取得权限**；且审核中不可见 |
| **微信** | **已通**：`product/add` + `listing`（本机实测）；`audit/cancel` 解决"审核中不能改不能下"（10020047） | **无接口** → 只能浏览器 |

**唯一可以真·全自动"点一下就发出去"的，只有微信小店的商品发布。**

### 4.5 数据查询（分层最乱的一块）

| 数据 | 微信 | 抖音 |
|:--|:--|:--|
| 商品/订单基础 | ✅ 已通（订单 7 天窗限制） | ❌ 无（快照兜底） |
| 曝光/点击/流量 | ❌ **实测 48001 未授权**（有接口没权限） | ❌ 未取证 |
| 成交口径 | 🟡 订单可自算；compass 口径未实测 | ❌ 未取证 |
| 达人/联盟 | 🟡 接口在，未授权 | ❌ 未取证 |
| 直播 | 🟡 接口在，未授权 | ❌ 未取证 |

**误判风险**：把"订单接口能调通"当成"数据看板能做"。**订单 ≠ 流量**：曝光/点击/入池这些恰恰是运营优化最需要的，而它们**两个平台都拿不到（微信是没权限，抖音是没接口）。**

---

## 五、API 与浏览器操作的边界

### 5.1 能走 API（可自动化）
- **微信小店**：商品增删改查、上下架、免审更新、库存、类目/类目规则、图片上传、运费模板、优惠券（列表）、订单列表/详情/搜索、地址、店铺信息、**橱窗商品**（上架/详情/列表/下架）
- **抖音（需先开通权限）**：抖店商品/SKU/类目属性/订单/售后；抖音 `video.create` 上传发布、`video.data`/`video.list` 视频数据

### 5.2 只能走浏览器（本机现状即证据）
- **抖店全部商品操作与数据**：现状 = 16 列 xlsx + `ego/main/excel-route-4.mjs`（`goTarget=sale` 直发售卖中）+ 页面快照 `draftbox.json` / `dy-sold-list.csv`
- **视频号发布与视频挂商品**：视频号助手网页
- **抖店后台活动报名、商机/猜你喜欢入池查看**：后台页面
- **微信侧曝光/流量数据**：接口未授权 → 后台"数据罗盘"网页

### 5.3 边界提醒（写进 7.0 设计时必守）
1. 浏览器执行的抖店上架**不是"API"，不能承诺 SLA**：页面改版即失效，且快照"可能只覆盖第一页"（`union3.py:116` 原文）。
2. 视频号走浏览器 = 触碰平台规则灰区，**必须留人工确认闸门**，不能做成无人值守批量发布。
3. 微信订单 7 天窗、`data_type=2` 才能读未上架、审核中必须先 `audit/cancel` —— 这三条是已踩过的坑，7.0 复用时要写进接口适配层。

---

## 六、结论：7.0 各功能模块的 API 可行性评级

| 7.0 模块 | 微信小店 | 抖音小店 | 评级 | 依据 |
|:--|:--|:--|:--|:--|
| 商品同步 / 上架 | **A · 直接 API** | **C · 需先建自用型应用** | 微信 A / 抖音 C | 微信已实测；抖音零接入 |
| 统一商品库（数据对齐） | A | D · 只有残缺快照 | **B** | 微信真数，抖店快照不全 |
| 商品卡曝光优化 | D · 无权限 | D · 无接口 | **D** | 两边都没有曝光/点击数据 |
| 搜索商机 | D · 无搜索词接口 | D · 商机接口未核实 | **D** | 无可依赖接口 |
| 活动报名 | D | D | **D** | 未见官方报名接口 |
| 猜你喜欢入池 | D | D | **D** | 入池是后台动作 |
| 达人推广 / 联盟 | **B · 接口在，需开通+授权** | D | **B（微信）/ D（抖音）** | 微信 league 路径原文齐全 |
| 视频生产（本地） | A（本地 ffmpeg/AI，不涉平台） | A | **A** | 纯本地能力 |
| 视频上传（素材） | B（小店素材上传接口，未调） | B（需 `video.create`） | **B** | 有接口，都要权限 |
| 视频正式发布 | **D · 只能浏览器** | **B · 需账号体系+权限申请** | 微信 D / 抖音 B | 视频号无发布 API |
| 视频关联商品 | D | D（锚点不含商品） | **D** | 两平台都只能人工 |
| 数据收集（成交/订单） | **A · 订单已通（7 天窗）** | C · 需建应用 | **B** | 微信可做，抖音不行 |
| 流量分析 / 自动优化 | D | D | **D** | **没有输入数据，AI 优化无源可依** |

**一句话给 7.0 的方案约束**：把"商品上架（微信）+ 成交订单（微信）+ 视频生产（本地）"做成真自动；把"抖店商品/视频发布"设计成**需要主人先去开放平台建应用+申请权限**的前置任务（不是代码问题，是账号问题）；把"曝光/流量/商机/活动/猜你喜欢"明确标为**数据不可得**，只能做浏览器采集或人工录入，不要让 AI 运营引擎假装能算。

---

## 附录：抓取到的官方接口清单原文摘录

### A. 微信小店 · 商品管理（[官方文档] https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product）

| 接口名称 | 请求路径 |
|:--|:--|
| 添加商品 | `/channels/ec/product/add` |
| 删除商品 | `/channels/ec/product/delete` |
| 获取商品 | `/channels/ec/product/get` |
| 获取商品列表 | `/channels/ec/product/list/get` |
| 更新商品 | `/channels/ec/product/update` |
| 免审更新商品 | `/channels/ec/product/auditfree` |
| 上架商品 | `/channels/ec/product/listing` |
| 下架商品 | `/channels/ec/product/delisting` |
| 撤回商品审核 | `/channels/ec/product/audit/cancel` |
| 获取商品H5短链 | `/channels/ec/product/h5url/get` |
| 获取商品口令 | `/channels/ec/product/taglink/get` |
| 获取商品二维码 | `/channels/ec/product/qrcode/get` |
| 库存（4 条） | `stock/api_getstock`、`api_batchgetstock`、`api_getstockflow`、`api_updatestock` 对应 `/channels/ec/product/stock/*` |

### B. 微信小店 · 罗盘商家版（[官方文档]，路径原文见 API 全表）

| 接口名称 | 请求路径 |
|:--|:--|
| 获取授权视频号列表 | `/channels/ec/compass/shop/finder/authorization/list`（中文名确认，路径以 detail 页为准） |
| 获取带货达人列表 | `/channels/ec/compass/shop/finder/list` |
| 获取带货数据概览 | `/channels/ec/compass/shop/finder/overall` |
| 获取店铺开播列表 | `/channels/ec/compass/shop/live/list/get` |
| 获取电商数据概览 | `/channels/ec/compass/shop/overall/get` |
| 获取商品详细信息 | `/channels/ec/compass/shop/product/data/get` |
| 获取商品列表 | `/channels/ec/compass/shop/product/list/get` |
| 获取店铺人群数据 | `/channels/ec/compass/shop/sale/profile/data/get` |

> 注：表格中前三条路径为按官方 detail 页命名规则推得（**路径未逐字核到原文，标 [尚未验证]**）；后五条为抓取原文中直接出现的路径。

### C. 微信小店 · 优选联盟（[官方文档] https://developers.weixin.qq.com/doc/store/shop/API/league 原文表格）

| 接口名称 | 请求路径 |
|:--|:--|
| 获取带货者详情信息 | `/channels/ec/league/promoter/get` |
| 获取带货者列表 | `/channels/ec/league/promoter/list/get` |
| 获取带货者商品数据列表 | `/channels/ec/league/talent/getproductdatalist` |
| 编辑带货者 | `/channels/ec/league/talent/update` |
| 批量新增联盟商品 | `/channels/ec/league/item/batchadd` |
| 获取联盟推广计划详情 | `/channels/ec/league/item/get` |
| 批量新增联盟机构推广 | `/channels/ec/league/item/headsupplier/batchadd` |
| 获取联盟商品列表 | `/channels/ec/league/item/list/get` |
| 更新联盟推广计划信息 | `/channels/ec/league/item/upd` |
| 获取商品推广计划列表 | `/channels/ec/league/promotion/plan/list` |
| 获取商品推广带货者列表 | `/channels/ec/league/promotion/talent/list` |

### D. 视频号助手 API（[官方文档] `/doc/channels/api/channels/` 目录原文，**无发布类接口**）

| 分类 | 接口 |
|:--|:--|
| 直播基础信息 | 视频号获取当前的直播记录 · 获取当前的直播预约记录 |
| 橱窗管理 | 上架商品到橱窗 · 获取橱窗商品详情 · 获取已添加到橱窗的商品列表 · 下架橱窗商品 |
| 留资组件 | 按直播场次/按时间获取留资信息详情 · 获取留资 request_id 列表 · 获取留资组件直播推广记录 · 获取留资组件 Id 列表 |
| 留资服务的直播数据 | 获取视频号账号信息 · 获取留资直播数据详情 · 获取账号收集的留资数量 |
| 直播大屏数据 | 获取直播大屏直播列表 · 获取直播大屏数据 |
| 罗盘达人版 | 获取电商概览数据 · 获取带货商品数据 · 获取带货商品列表 · 获取带货人群数据 |

> **关键否定证据**：上述目录是官方给出的完整能力清单，**不含视频上传/草稿/发布/删除**任何一项。

### E. 抖音开放平台 · 创建视频（[官方文档] 原文摘录）

```
Scope: video.create 需要申请权限 需要用户授权
该接口用于创建抖音视频（支持话题, 小程序等功能）。该接口适用于抖音。

注意：
- 挂载小程序请先完成申请->申请及使用说明
- 创建抖音视频后, 会有一个审核过程, 期间只有自己可见。
- 如果发布视频想@用户，需要获取昵称与 open_id。
- 目前发布视频接口，支持三种锚点类型，包括：小程序、游戏、poi。不支持携带多种类型的锚点。
- 如需代用户创建视频，除授权外，每次调用都需要在产品设计中让用户明确感知相关操作。
  如发现未经用户感知代用户创建视频，或将回收相关接口权限并处罚应用及账号。

请求地址
POST /video/create/
请求头
 Content-Type: application/json
 access-token: 调用/oauth/access_token/生成的token，此token需要用户授权。

响应样例
{"extra":{"description":"","sub_error_code":0,"sub_description":"","logid":"...","now":1597213176393,"error_code":0},
 "data":{"error_code":0,"description":"","item_id":"@8hx..."}}
```

### F. 抖音开放平台 · 权限表（[官方文档] 用户类型及权限说明，原文摘录）

```
| 抖音视频发布及管理 | 视频发布及管理 | 一键发布视频 | video.create | 普通权限 | 默认关闭 | 管理中心申请 |
|                    |                | 一键发布单图 | video.create | 普通权限 | 默认关闭 | 管理中心申请 |
|                    |                | 发布携带话题 | video.create | 普通权限 | 默认关闭 | 默认开启 |
|                    |                | 发布携带小程序 | video.create | 普通权限 | 默认关闭 | 默认开启 |
| 删除内容           | delete         |              | video.delete | 普通权限 | 默认关闭 | 管理中心申请 |
| 查询授权用户的抖音视频数据 |        |              | video.data  | 普通权限 | 默认关闭 | 管理中心申请 |
| 查询特定抖音视频的视频数据 |        |              | video.list  | 普通权限 | 默认关闭 | 管理中心申请 |
```

### G. 抖店开放平台（[第三方整理]，**非官方原文**，仅作线索，不作权限承诺）

```
商品类：/product/listV2（获取商品列表新版，商品信息查询）
        /product/detail（商品查询，商品信息查询）
        /product/addV2（发布商品，商品编辑）
        /product/editV2（编辑商品，商品编辑）
        /product/setOnline（商品上架，商品上下架）
        /product/setOffline（商品下架，商品上下架）
        /product/qualityList（店铺商品质量查询，商品成长中心）
        /product/getCatePropertyV2（类目属性，商品类目属性查询）
        /sku/list、/sku/detail、/sku/editPrice、/sku/editCode、/sku/editStock、/sku/syncStockBatch
        /spu/getSpuInfoBySpuId、/spu/getSpuTpl、/spu/addShopSpu
        /opptyProduct/apply（机会品提报，机会品管理）
        /opptyProduct/clue（机会品线索触达）
        /opptyProduct/getApplyProgress（机会品提报进度查询）
        /shop/brandList、/shop/getShopCategory、/address/list、/freightTemplate/list
授权：自用型应用 = AppKey + AppSecret + ShopID → access_token（getShopAccessToken($shopid)）
      工具型应用 = 授权 URL → code → requestAccessToken() → access_token
签名：HMAC-SHA256；需配置 IP 白名单
```

---

## 报告自检

| 项 | 状态 |
|:--|:--|
| 抖音必答 13 条是否一条一行 | ✅ 全部逐条（表格 13 行），未合并 |
| 微信必答 12 条是否一条一行 | ✅ 全部逐条（表格 12 行），未合并 |
| 状态是否只用 6 个口径 | ✅ 仅用 `官方API开放且账号已有权限` / `官方API存在但账号未取得权限` / `尚未核实` / `仅支持浏览器操作`；`需要申请资质`、`仅支持人工操作` 本轮未使用（无对应证据） |
| 五种能力是否分别验证 | ✅ 第四章 4.1–4.5 分开 |
| 是否编造接口名 | ✅ 微信侧全部路径取自官方表格原文；抖店侧明确标注 `[第三方整理]`；推得路径逐条标 `[尚未验证]` |
| 是否泄露密钥 | ✅ 无 AppSecret / 无 Cookie；appid 已截断打码 |
| 是否调用真实发布/下单 | ✅ 零调用（全程只读代码 + 只读文档） |

**`尚未核实` 条目编号**：抖音 #3、#4、#5、#6、#7、#10、#12；微信 #3、#7。
