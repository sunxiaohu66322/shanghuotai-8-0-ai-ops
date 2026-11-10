### None
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 首页 | `/home` | real |  |

### 批量修改
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 改价格 | `/edit-price` | real |  |
| 改库存 | `/edit-stock` | real |  |
| 上下架 | `/edit-list-status` | real |  |
| 恢复回收站 | `/restore-recycle` | read |  |
| 改标题 | `/edit-title` | real |  |
| 改短标题 | `/edit-short-title` | real |  |
| 改主图 | `/edit-main-pic` | real |  |
| 改类目 | `/edit-category` | real |  |
| 改属性 | `/edit-attr` | real |  |
| 改资质 | `/edit-qualification` | shell | 商品资质字段 qua_info |
| 改货号 | `/edit-spu-code` | real |  |
| 改品牌 | `/edit-brand` | real |  |
| 改限购 | `/edit-sale-limit` | real |  |
| 改尺码表 | `/edit-size-chart` | real |  |
| 改运费模板 | `/edit-logistics-template` | real |  |
| 添加SKU | `/add-sku` | real |  |
| 删除SKU | `/delete-sku` | real |  |
| 复制SKU | `/copy-sku` | real |  |
| 改SKU名 | `/edit-sku-name` | real |  |
| 改SKU图 | `/edit-sku-pic` | real |  |
| 改sku编码 | `/edit-sku-code` | real |  |
| 改发货方式 | `/edit-logistics-type` | real |  |
| 改售后 | `/edit-after-sale` | shell | 售后支持字段 |
| 改售后说明 | `/edit-after-sale-desc` | real |  |
| 删除商品 | `/del-product` | real |  |
| 改发货时效 | `/edit-stock-type` | real |  |
| 改重量 | `/edit-weight` | real |  |
| 改标题前缀 | `/edit-title-prefix` | real |  |
| 改详情图 | `/edit-desc-pic` | real |  |
| 改主图视频 | `/edit-video` | real |  |
| 改售后地址 | `/edit-refund-address` | shell | 售后地址字段 |
| 改支持换货 | `/edit-exchange-support` | shell | 换货支持字段 |
| 改优选联盟 | `/add-to-league` | shell | 优选联盟功能需先在后台开通 |
| 限时抢购 | `/discount` | shell | discount/task 接口（本店未开通） |
| 优惠券 | `/coupon` | read | 券列表已可读；创建需 coupon/create 完整字段 |

### 商品优化
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 商品导入 | `/importItem` | real |  |
| 商品导出 | `/exportItem` | real |  |
| 人工鉴图 | `/jiantu` | shell | 本地小猪鉴图链路（9109 服务 + PicDetector） |
| 低价商品检测 | `/detect-product` | real |  |
| 发品质量检测 | `/publish-quality` | real |  |
| 申请类目 | `/apply-category` | read | 类目树已可读；申请需资质材料 |

### 商品复制
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 商品复制 | `/product-copy/copyHome` | shell | 跨店复制：原版靠自建"授权码"体系，微信官方无此能力 |
| 商品裂变 | `/product-copy/liebian` | shell | 同上，依赖授权码体系 |
| 商品导入 | `/product-copy/importIndex` | real |  |
| 商品刷新 | `/product-copy/refreshIndex` | shell | 跨店同步，依赖授权码体系 |
| 云库 | `/yunku` | real |  |
| 同步原商品信息 | `/product-copy/recover` | shell | 依赖授权码体系 |
| 复制记录 | `/product-copy/copyList` | real |  |

### 多店关联
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 商品诊断 | `/diagnosis/home` | real |  |

### 1688铺货
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 链接铺货 | `/b1688/link` | shell | 1688 开放平台账号 + 采集接口 |
| 货源推荐 | `/b1688/recommend` | shell | 1688 选品 API |
| 货源搜索 | `/b1688/search` | shell | 1688 搜索 API |

### 自定义导出
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 自定义导出商品 | `/exportItem` | real |  |
| 导出记录 | `/exportRecord` | real |  |

### 导入
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 导入Excel | `/importItem` | real |  |
| 导入修改记录 | `/importRecord` | real |  |
| 任务记录 | `/task-records` | real |  |
| 快速修改 | `/quick-modify` | real |  |

### 微店后台管理
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 后台概览 | `/wx/overview` | real |  |
| 商品列表 | `/products` | real |  |
| 类目·属性·资质 | `/wx/categories` | real |  |
| 类目树（检索） | `/cats` | real |  |
| 素材库 | `/wx/materials` | real |  |
| 订单（7 天） | `/orders` | real |  |
| 运费模板 | `/freight` | read |  |
| 优惠券 | `/coupons` | read |  |
| 新建上货 | `/create` | real |  |
| 连接与凭据 | `/settings` | real |  |

### 图片编辑 6.0
| 页面 | key | 菜单自述 | 备注 |
|:--|:--|:--|:--|
| 照片墙·框选修补 | `/photo/wall` | real |  |
| 检测打码 | `/photo/mask` | real |  |

TOTAL 71
