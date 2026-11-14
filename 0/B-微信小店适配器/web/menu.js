/* 菜单树：逐字取自 shipinhao.taosoft.com.cn 前端 bundle 的侧边栏数组（视频号平台那一套）
   children 的 key/title 与原版完全一致；state 标明本地实现程度：
   real=接真数据可执行 / patch=可用字段补丁执行 / read=只读真数据 / shell=只有外壳 */
window.MENU = [
  { key: '/home', title: '首页', icon: '⌂', page: 'home', state: 'real' },
  { key: '/accounts', title: '账号管理', icon: '⚿', page: 'accounts', state: 'real' },
  { key: '/direct-publish', title: '直接上架', icon: '⬆', page: 'create', state: 'real' },
  {
    key: 'bulk-edit', title: '批量修改', icon: '✎', children: [
      { key: '/edit-price', title: '改价格', act: 'price', state: 'real' },
      { key: '/edit-stock', title: '改库存', act: 'stock', state: 'real' },
      { key: '/edit-list-status', title: '上下架', act: 'status', state: 'real' },
      { key: '/restore-recycle', title: '恢复回收站', act: 'recycle', state: 'read' },
      { key: '/edit-title', title: '改标题', act: 'title', state: 'real' },
      { key: '/edit-short-title', title: '改短标题', act: 'short_title', state: 'real' },
      { key: '/edit-main-pic', title: '改主图', act: 'main_pic', state: 'real' },
      { key: '/edit-category', title: '改类目', act: 'category', state: 'real' },
      { key: '/edit-attr', title: '改属性', act: 'attr', state: 'real' },
      { key: '/edit-qualification', title: '改资质', act: 'qualification', state: 'shell', need: '商品资质字段 qua_info' },
      { key: '/edit-spu-code', title: '改货号', act: 'spu_code', state: 'real' },
      { key: '/edit-brand', title: '改品牌', act: 'brand', state: 'real' },
      { key: '/edit-sale-limit', title: '改限购', act: 'sale_limit', state: 'real' },
      { key: '/edit-size-chart', title: '改尺码表', act: 'size_chart', state: 'real' },
      { key: '/edit-logistics-template', title: '改运费模板', act: 'freight', state: 'real' },
      { key: '/add-sku', title: '添加SKU', act: 'sku_add', state: 'real' },
      { key: '/delete-sku', title: '删除SKU', act: 'sku_del', state: 'real' },
      { key: '/copy-sku', title: '复制SKU', act: 'sku_copy', state: 'real' },
      { key: '/edit-sku-name', title: '改SKU名', act: 'sku_name', state: 'real' },
      { key: '/edit-sku-pic', title: '改SKU图', act: 'sku_pic', state: 'real' },
      { key: '/edit-sku-code', title: '改sku编码', act: 'sku_code', state: 'real' },
      { key: '/edit-logistics-type', title: '改发货方式', act: 'deliver', state: 'real' },
      { key: '/edit-after-sale', title: '改售后', act: 'after_sale', state: 'shell', need: '售后支持字段' },
      { key: '/edit-after-sale-desc', title: '改售后说明', act: 'aftersale_desc', state: 'real' },
      { key: '/del-product', title: '删除商品', act: 'delete', state: 'real' },
      { key: '/edit-stock-type', title: '改发货时效', act: 'stock_type', state: 'real' },
      { key: '/edit-weight', title: '改重量', act: 'weight', state: 'real' },
      { key: '/edit-title-prefix', title: '改标题前缀', act: 'title_prefix', state: 'real' },
      { key: '/edit-desc-pic', title: '改详情图', act: 'desc_pic', state: 'real' },
      { key: '/edit-video', title: '改主图视频', act: 'video', state: 'real' },
      { key: '/edit-refund-address', title: '改售后地址', act: 'refund_address', state: 'shell', need: '售后地址字段' },
      { key: '/edit-exchange-support', title: '改支持换货', act: 'exchange', state: 'shell', need: '换货支持字段' },
      { key: '/add-to-league', title: '改优选联盟', act: 'league', state: 'shell', need: '优选联盟功能需先在后台开通' },
      { key: '/discount', title: '限时抢购', act: 'discount', state: 'shell', need: 'discount/task 接口（本店未开通）' },
      { key: '/coupon', title: '优惠券', act: 'coupon', state: 'read', need: '券列表已可读；创建需 coupon/create 完整字段' }
    ]
  },
  {
    key: 'optimize', title: '商品优化', icon: '◎', children: [
      { key: '/importItem', title: '商品导入', page: 'import', state: 'real' },
      { key: '/exportItem', title: '商品导出', page: 'export', state: 'real' },
      { key: '/jiantu', title: '人工鉴图', page: 'jiantu', state: 'shell', need: '本地小猪鉴图链路（9109 服务 + PicDetector）' },
      { key: '/detect-product', title: '低价商品检测', page: 'lowprice', state: 'real' },
      { key: '/publish-quality', title: '发品质量检测', page: 'diagnose', state: 'real' },
      { key: '/apply-category', title: '申请类目', page: 'cats', state: 'read', need: '类目树已可读；申请需资质材料' }
    ]
  },
  {
    key: 'product-management', title: '商品复制', icon: '⧉', children: [
      { key: '/product-copy/copyHome', title: '商品复制', page: 'shell', state: 'shell', need: '跨店复制：原版靠自建"授权码"体系，微信官方无此能力' },
      { key: '/product-copy/liebian', title: '商品裂变', page: 'shell', state: 'shell', need: '同上，依赖授权码体系' },
      { key: '/product-copy/importIndex', title: '商品导入', page: 'import', state: 'real' },
      { key: '/product-copy/refreshIndex', title: '商品刷新', page: 'shell', state: 'shell', need: '跨店同步，依赖授权码体系' },
      { key: '/yunku', title: '云库', page: 'yunku', state: 'real' },
      { key: '/product-copy/recover', title: '同步原商品信息', page: 'shell', state: 'shell', need: '依赖授权码体系' },
      { key: '/product-copy/copyList', title: '复制记录', page: 'tasks', state: 'real' }
    ]
  },
  { key: 'multi-shop', title: '多店关联', icon: '⌘', page: 'shell', state: 'shell', need: '原版是 ISV 授权码把多个店绑到一个账号；自研模式只能管自己一个店，结构可留、功能不适用' },
  { key: '/diagnosis/home', title: '商品诊断', icon: '✚', page: 'diagnose', state: 'real' },
  {
    key: 'b1688', title: '1688铺货', icon: '⇄', children: [
      { key: '/b1688/link', title: '链接铺货', page: 'shell', state: 'shell', need: '1688 开放平台账号 + 采集接口' },
      { key: '/b1688/recommend', title: '货源推荐', page: 'shell', state: 'shell', need: '1688 选品 API' },
      { key: '/b1688/search', title: '货源搜索', page: 'shell', state: 'shell', need: '1688 搜索 API' }
    ]
  },
  {
    key: 'export-management', title: '自定义导出', icon: '↓', children: [
      { key: '/exportItem', title: '自定义导出商品', page: 'export', state: 'real' },
      { key: '/exportRecord', title: '导出记录', page: 'exportRecords', state: 'real' }
    ]
  },
  {
    key: 'import-management', title: '导入', icon: '↑', children: [
      { key: '/importItem', title: '导入Excel', page: 'import', state: 'real' },
      { key: '/importRecord', title: '导入修改记录', page: 'tasks', state: 'real' }
    ]
  },
  /* 运营中心（2026-11-10）：上货台顶部那个「全店/上架中/未上架」总统计已删，
     数据挪到这里，按三个店各自独立呈现 + 每商品一张状态卡。 */
  {
    key: 'ops-center', title: '运营中心', icon: '◈', children: [
      { key: '/ops/shops', title: '三店独立数据', page: 'opsShops', state: 'real' },
      { key: '/ops/cards', title: '商品状态卡', page: 'opsCards', state: 'real' }
    ]
  },
  { key: '/task-records', title: '任务记录', icon: '☰', page: 'tasks', state: 'real' },
  { key: '/quick-modify', title: '快速修改', icon: '⚡', page: 'quick', state: 'real' },
  {
    key: 'wx-admin', title: '微店后台管理', icon: '▣', children: [
      { key: '/wx/overview', title: '后台概览', page: 'wxOverview', state: 'real' },
      { key: '/products', title: '商品列表', page: 'products', state: 'real' },
      { key: '/wx/categories', title: '类目·属性·资质', page: 'wxCats', state: 'real' },
      { key: '/cats', title: '类目树（检索）', page: 'cats', state: 'real' },
      { key: '/wx/materials', title: '素材库', page: 'wxMaterials', state: 'real' },
      { key: '/orders', title: '订单（7 天）', page: 'orders', state: 'real' },
      { key: '/freight', title: '运费模板', page: 'freight', state: 'read' },
      { key: '/coupons', title: '优惠券', page: 'coupons', state: 'read' },
      { key: '/create', title: '新建上货', page: 'create', state: 'real' },
      { key: '/settings', title: '连接与凭据', page: 'settings', state: 'real' }
    ]
  },
  {
    key: 'photo-edit', title: '图片编辑 6.0', icon: '✂', children: [
      { key: '/photo/wall', title: '照片墙·框选修补', page: 'photo', state: 'real' },
      { key: '/photo/mask', title: '检测打码', page: 'maskdetect', state: 'real' }
    ]
  },
  /* 7.0 抖店后台管理：抖店侧的一切——看草稿、选货源、上架、跳后台改东西，都从这里走。
     与 6.0 的区别：6.0 管微店（有免签接口，能拉 563 件真商品）；
     7.0 管抖店（没有免签接口，真数据只能来自 ego 跑出的页面快照，没跑过就没有）。 */
  {
    key: 'dd-admin', title: '抖店后台管理 7.0', icon: '⬆', children: [
      { key: '/dd/overview', title: '后台概览', page: 'ddOverview', state: 'real' },
      { key: '/dd/drafts', title: '草稿箱', page: 'ddDrafts', state: 'real' },
      { key: '/dd/queue', title: '待上架货源', page: 'ddQueue', state: 'real' },
      { key: '/dd/publish', title: '上架执行', page: 'ddPublish', state: 'real' },
      { key: '/dd/links', title: '后台跳转', page: 'ddLinks', state: 'real' }
    ]
  },
  /* 9.0 AI 运营中心（2026-11-12）：晓风搬家上货全部 91 条运营功能复刻 + 流转状态机。
     与 8.0 的区别：8.0 是散功能，9.0 是「今日一趟」的工作流 + 状态机 + 平台回读写死。
     数据源 data/xf9_catalog.json（静态还原，不是假数据）；任务账本 xf9_state.json / xf9_ledger.jsonl。
     红线：本页不发真实平台请求；正式发布/报名/改价一律「AI 准备 → 人工确认」。 */
  {
    key: 'xf9', title: '9.0 AI 运营中心', icon: '✦', children: [
      { key: '/xf9/hub', title: '今日一趟 · 运营闭环', page: 'xf9Hub', state: 'real' },
      { key: '/xf9/all', title: '全套功能树（91项）', page: 'xf9All', state: 'real' },
      { key: '/xf9/remake', title: '爆款视频复刻', page: 'xf9Remake', state: 'real' },
      { key: '/xf9/principle', title: '视频原理 · 本地试合成', page: 'xf9Principle', state: 'real' },
      { key: '/xf9/tasks', title: '任务账本 / 回读证据', page: 'xf9Tasks', state: 'real' }
    ]
  }
];

