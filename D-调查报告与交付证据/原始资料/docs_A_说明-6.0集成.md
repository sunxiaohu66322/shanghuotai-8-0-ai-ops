# 6.0 · 微店后台管理 —— 集成说明（2026-10-27）

## 一句话

**6.0 不是第二套软件**：它是主窗口里的一个页签（排在 5.5 之后），内嵌的是
`default-workspace\微信小店自建上货台` 那套已开发成果；服务由主程序在本进程内拉起、
退出时自动收掉，用户不需要手动启动任何东西。

## 架构

```
上货台.exe（主程序，唯一入口）
  └ app/ui/tab_wx.py        6.0 页签 = 工具栏 + QWebEngineView（内嵌，非外开浏览器）
       └ app/wxserve.py     服务托管：
            ① 9191 已有健康实例 → 复用（不抢端口）
            ② 否则本进程 import 微信小店自建上货台\app.py → 线程 serve_forever
            ③ 主程序 closeEvent → srv.shutdown()+server_close() → 轮询确认端口放掉
  真源（代码/数据/图片）不复制：app.py / wxapi.py / photo.py / web\* 全部原地读取
```

- WebEngine 硬要求：`QtWebEngineWidgets` 必须在 QApplication 之前导入 → 已在 `boot.py` 开头
  预 import + 设 `AA_ShareOpenGLContexts`；没装 WebEngine 时 6.0 页签自动降级为提示页，
  **其余页签照常**（懒加载 + try/except 双保险）。
- 打包：`上货台.spec` 已补 `PyQt6.QtWebEngineWidgets/Core` hiddenimports；真源路径写死绝对值
  （frozen 后 ROOT.parent 不再是 default-workspace，与 paths.TAOBAO_DIR 同一处理）。

## 页签顺序（本轮后）

1 · 采集初筛 / 2 · 商品卡导入 / 2.5 · 商品库 / 2.5.1 · 下载监控 / 3 · 商品精筛 /
4 · 检测打码 / 4.1 · 主图人工审核 / 4.9 · 主图 AI 重做 / 5.0 · 直接上架 / 5.5 · 出表 /
**6.0 · 微店后台管理**

## 跨序列映射（已实现的桥）

```
taobao-picker\data\v3.db::v3_publish.external_listing_id   ←→   微店商品 ID
6.0 页签工具栏「定位微店商品」：下拉选映射（或手粘 ID）→ 内嵌页跳 #/edit-stock?focus=<ID>
6.0 页内按该 ID 过滤商品表（pages.js workbench 读 focus 参数）
```

## 改动清单（均有 .bak-* 备份）

| 文件 | 改动 |
|:--|:--|
| `app/wxserve.py` | **新增** 服务托管（复用/拉起/健康等待/干净关闭） |
| `app/ui/tab_wx.py` | **新增** 6.0 页签（WebEngine 内嵌 + 定位商品桥） |
| `app/ui/main_window.py` | 追加 6.0 页签注册 + closeEvent 收服务 |
| `app/boot.py` | QApplication 前预 import WebEngine（硬要求） |
| `app/paths.py` | APP_VER 0.5.3 → 0.6.0 |
| `上货台.spec` | hiddenimports 补 WebEngine 两项 |
| `微信小店自建上货台\web\pages.js` | workbench 支持 `?focus=`（4 行） |

## 验证记录（2026-10-27 00:48）

```
A 自持端口 9195：start→health ok→stop→3 次探测 0 应答（端口真放掉）
B 真窗口：11 页签、6.0 在最后；切页即起服务（9191 复用外部实例）；DOM 10880 字符
  含「微店后台管理」；截图 data\shots\6.0-in-desktop.png / 6.0-window.png
C 定位桥：open_product("10001792323089") → URL 变 #/edit-stock?focus=… ✓
D 回归：tests_selftest.py 44 项 43 过；唯一失败项
  「打开浏览器的调用都在 _open_current 里」涉及 dlcenter_win/tabs/tab_dlmon/tab_publish
  —— 全部是本轮未触碰的文件（另一会话 10-26 改动），与 6.0 无关，未修（手术刀原则）。
```

## 已知边界

1. exe 仍依赖 `微信小店自建上货台` 目录在盘上（真源不复制，属设计取舍）。
2. 9191 被外部独立进程占用时会复用之（此时生命周期不归主程序管）；外部进程退出后
   主程序下次启动 6.0 会自己拉起并托管。
3. 「2.5 选商品 → 6.0 修图 → 回写 2.5」完整闭环未打通（当前只做到「定位到微店商品」）；
   图片编辑产出落 6.0 自己的 data/photos/out，回写商品媒体是下一步。
