# 拼多多详情 TXT 接入「2.0 商品卡导入」· 修复报告 · 2026-11-09

> 结论等级：**A**（修复已落地、真样本真落库验收通过；界面需重启桌面版后生效）
> 触发：主人导入 `D:\多米采集导出\*.txt` 时报错「第二步只吃单商品详情卡 JSON…这些是列表类文件」。
> 直言结论：**不是数据包不对，是这活儿今天没做完**——缺了三层适配，现已补齐。

## 1. 根因（三层缺口，逐条有证据）

| # | 层 | 位置 | 病灶 | 证据 |
|:--|:--|:--|:--|:--|
| ① | 桌面版 UI 拦截 | `上货台\app\ui\tabs.py:1161`（改前） | `ok_ext={".json"}` 按扩展名一刀切，`.txt` 直接弹错，**没读内容** | 报错文案原文 + 8 个文件实测 7万~15万字节、单行完整 JSON |
| ② | 后端平台识别 | `taobao-picker\server\v3\adapters\__init__.py:16`（改前） | 注释白纸黑字「pdd/taobao 尚未拿到真实卡样本 → **不注册**，detect 返回 None」 | 即使 UI 放行，`detect_file` 也会判「认不出平台」 |
| ③ | SKU 落库 | `taobao-picker\server\v3\sku.py:80`（改前） | `sku.parse` 只认微店 `_extra.skuSpecs / _raw.productSkus`，拼多多是**顶层 `sku[]`** | 首次落库验收 `v3_sku 行数=0`（适配器解析出 60 条，库里 0 条） |

**数据包本身完全合规**（实测 `D:\多米采集导出` 223 个 .txt，逐个 `json.loads` 通过）：

```
goods{ goods_id, goods_name, hd_url, gallery[11~39], decoration[].contents[].img_url,
       goods_property[], cat_id_1..4, market_price, quantity, sold_quantity }
sku[]              顶层（5 / 43 / 12 条），group_price='43899' 是「分」的字符串
price{ min/max_group_price }   单位分
ui.carousel_section.video.url / goods.gallery[] type=9 的 video_url   视频
mall_entrance.mall_data   是 Python-repr 字符串，店铺名 mall_name 藏在其中
```

报错说「只有标题、店铺、链接，没有主图/详情图/SKU 图」——与文件内容相反，属**按扩展名的误判**。

## 2. 改了什么（4 个文件，改前全部备份）

| 文件 | 改动 |
|:--|:--|
| `v3\adapters\pdd.py` | **新建**。detect（goods.goods_id + server_time/ui + gallery/sku）+ parse_card（输出与 wechat 同构）：价格分→元、店铺名从 mall_data 正则提、specs repr 解析、主图/详情图/SKU 图/视频按 (role,url) 去重、类目标 `confidence=0.0`（拼多多 cat_id 不套微信类目，不编造） |
| `v3\adapters\__init__.py` | 注册 `pdd` 到 ADAPTERS 首位；更新过时注释 |
| `v3\sku.py` | `parse()` 加拼多多顶层 `sku[]` 分支（微店三段原样不动），价格分→元、规格从 `specs` repr 提 |
| `上货台\app\ui\tabs.py` | `_pick_files` 改为**按内容判定**：`.txt` 文件头 4KB 含 `goods/goods_id/(server_time|ui)` 即放行；已确认的详情卡跳过 `urlpack_classify`（它把 JSON 当表格读会乱码误判）；文件对话框加 `*.txt`；hint 与弹窗文案改准。**真·列表 CSV/TSV/TXT 仍挡回第一步** |

备份：`*.bak-pdd卡接入-20261109-0042`（3 个既有文件；pdd.py 是新建无备份）。

## 3. 真验收（临时库，未碰生产库，未下图/未 OCR）

```
py_compile 4 文件                          rc=0
[1] detect_file + parse_card  3 个真样本   platform='pdd' err='' 全过
    1282410911  美粮坊旗舰店   ¥13.9~39.0    SKU 5   主图12/详情11/视频2
    994506130839 艺颂旗舰店    ¥228.99~578.99 SKU 43  主图39/详情38/视频1
    996950437061 杰木尊祺…旗舰店 ¥132.72~287.12 SKU 12 主图23/详情23/视频1
[2] import_folder(files_override=3) 临时库
    ok=True products=3 images=210 skus=60 invalid=0 platforms={'pdd':3}
    DB v3_image roles = main 74 / detail 72 / sku 60 / video 4
    DB v3_sku 样例 = {'sku_id':'1865257609631',
        'spec':'款式:老上海风味…/组合:【批发】50片…', 'price':39.0,
        'stock':1000, 'source':'pdd.sku[]'}
[3] UI 内容判定
    3 个详情 .txt → 放行 True
    真列表 CSV 风格 TXT → 放行 False（仍走第一步）
=== ALL PASS ===  rc=0
```

**验收中暴露并修掉的两处**（如实）：
1. 空库 `store.init` 不建 `v3_image.dl_state`（生产库历史迁移里才有）→ 验收夹具补 `restore_pack.ensure_schema`，与适配器无关；
2. `v3_sku` 首次 0 行 → 定位到 `sku.parse` 不认顶层 `sku[]`，补分支后 60/60 落库。

## 4. 边界与下一步

- **桌面版需重启才加载新 `tabs.py`**（Python 进程启动时载入模块）；后端适配器由 bridge 动态 import，随桌面版一起生效。重启后 2.0「选择文件导入并转换」直接选 `D:\多米采集导出\*.txt`（可多选，整批合成一个包）。
- **本轮未做**：未在真界面点一遍（桌面版重启属主人操作侧）、未真下图/OCR（验收是临时库、`download=False`）、未导入生产库 223 件（真实导入会下图+OCR+入库，属不可逆写入，等主人发话）。
- **类目口径**：拼多多 `cat_id_1..4` 只作来源记录，`confidence=0.0 / name_missing=True`，**不用于微信/抖店上架类目**（历史铁律：类目编号是平台货架编号，不可乱改）。
- **视频**：`ui.carousel_section.video` 与 `gallery[] type=9` 均落 `role=video`；拼多多视频无 `wdis_t` 过期参数，`video_expired=None`。

## 5. 证据文件

```
探针  D:\DSH_Cache\cache\tmp\ego\probe_pdd_card.py / probe_pdd_card2.py
验收  D:\DSH_Cache\cache\tmp\ego\verify_pdd_import.py
任务图谱 n-06c594327a（pending=1，未代转正）
```
