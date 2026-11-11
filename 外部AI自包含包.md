# 上货台 8.0 · AI 运营中心 — 给外部 AI 的自包含审阅包

> 本文件**自包含**。读它的人不需要联网、不需要打开任何链接、不需要访问 GitHub。
> 原仓库：`https://github.com/sunxiaohu66322/shanghuotai-8-0-ai-ops`（PUBLIC / main / 8.0-P0.3.0）。
> 本包 = 仓库根目录六件套全文 + 关键源码索引 + 交付报告全文。快照时间 2026-11-11。
> **口径**：生产真源 = `A-桌面主程序/app/ops/`；`E-P0开发副本参考/` 是历史副本。
> 「未收 / 未取得」≠「不存在」。已实测 / 未做 / 未取得 三态分开，别把没查当没问题。

## 本包目录

1. README.md
2. llms.txt
3. 00_读我_AI-FIRST.md
4. 01_架构.md
5. 02_现状与边界.md
6. 03_怎么改.md
7. 附录 A · 关键源码索引（函数 / 类 / 路由，逐文件行号）
8. 附录 B · 交付报告全文（P0 / 模块1 / 模块2 / 总体架构设计 / 双平台 API 能力矩阵）
9. 附录 C · 给外部 AI 提建议的四条格式


---

# 【README.md】

# 上货台 8.0 · AI 运营中心（双平台日常运营系统）

> 一套**已经能打开、能点、接真实平台数据、每次操作都有反馈**的 Windows 桌面运营系统。
> 本仓库是**完整结构化快照**（源码 + 报告 + 交付证据），供第三方 AI / 协作方直接阅读、评审、给修改建议。
> 公开仓库 · owner `sunxiaohu66322` · 快照时间 2026-11-11 · 版本 `8.0-P0.3.0`

---

## 0. 给外部 AI 的第一句话（先读这段）

- 阅读顺序：**本 README → [llms.txt](llms.txt)（全文索引）→ [00 读我](00_读我_AI-FIRST.md) → [01 架构](01_架构.md) → [02 现状与边界](02_现状与边界.md) → [03 怎么改](03_怎么改.md)**。
- 想给修改建议，请**逐条说明**：① 依据哪个文件（`路径:行`）② 属于「现有能力」还是「需新增」③ 改动落在哪一层（桌面 UI / 9192 引擎 / ops.db / 平台连接器 / 浏览器脚本）④ 怎么验收。**不要给脱离目录结构的泛泛建议。**
- 这是**结构化快照，不是可直接运行的部署包**：运行时数据库（`ops.db` / `v3.db`）、密钥、编译产物 `dist/`、浏览器登录态按规则未收。**「包里没有」= 未收，不等于「系统里不存在」**；读到 `0` 或「未取得」按原文口径理解。
- 三层可信度：交付报告与实测 JSON 为 `[事实]`；架构设计文档含 `[设计建议]`；「未取得 / 尚未核实」项是**真的没拿到**，不是「没有」。

---

## 1. 这套系统是干什么的

主人每天要**管理两个平台的日常运营**（抖店 + 两个微信小店），而不是只看任务队列和数据库状态。系统把这件事做进原「上货台」桌面程序的最后一个页签 **「8.0 · AI 运营中心」**：

- 看**平台真实商品数**（不拿本地库冒充）；
- 看**商品异常 / 视频 / 活动 / 账号运营**；
- **点按钮执行**，并且**每次操作都有结果反馈**（成功 / 失败 / 耗时 / 错误原文）；
- 定时自动采集、汇总。

底座是 **Python 3.13 + PyQt6 原生窗口**（不是网页套壳、不内嵌浏览器），原 1—6.0 上货流水线完整保留、一件未动。

---

## 2. 一句话现状（做到哪一步）

> **P0 底座 + 模块1（双平台真实商品中心）+ 模块2（真实业务运营与反馈）已交付并实测**；模块3—6 按优先级排期，未冒充完成。

| 阶段 | 内容 | 状态 |
|:--|:--|:--|
| P0 底座 | 8.0 页签入口 / 9192 引擎 / ops.db / 任务队列 / 执行日志 / v3.db 只读 ATTACH | ✅ 交付 |
| 模块1 | 双平台真实商品中心：微信官方 API + 抖店后台快照 + 商品ID匹配 + 状态统计 | ✅ 交付 |
| 模块2 | 5 个业务子页（操作反馈 / 抖店明细 / 短视频 / 限时限量购 / 账号）+ 定时采集 | ✅ 交付 |
| 模块3 | 商品视频自动匹配 / 上传 / 绑定 | ⏳ 未做 |
| 模块4 | 平台活动发现 / 报名辅助 | ⏳ 部分（报名资料草稿，无实时活动列表） |
| 模块5 | 抖音 / 视频号内容推广 | ⏳ 未做（且红线：不自动发布真实视频） |
| 模块6 | 每日自动运营 / AI 分析 | ⏳ 部分（定时采集已跑，日报未做） |

---

## 3. 六层目录 → 真源码对应关系

| 目录 | 是什么 | 关键文件 |
|:--|:--|:--|
| **A-桌面主程序/** | 生产真源，PyQt6 桌面壳 | `run.pyw`、`说明.md`、`app/bridge.py`(266KB 既有主逻辑)、`app/ui/main_window.py`、`app/ui/tab_ops.py`(8.0 页签)、**`app/ops/`** |
| A / `app/ops/` | **8.0 引擎核心** | `engine.py`(9192 路由+页面)、`platform.py`(平台真数接入)、`business.py`(业务页+反馈)、`schema.sql`(ops_* 表 DDL)、`migrate.py`、`queue.py`、`store.py`、`worker.py`、`config.py`、`dy_timed_capture.mjs`(抖店定时刷新脚本) |
| **B-微信小店适配器/** | 9191 官方 API 服务（18 端点已通） | `app.py`、`wxapi.py`、`publish.py`、`dd.py`、`photo.py`、`README.md` |
| **C-抖店研究与规则库/** | 抖店无 API → 后台结构 / 快照 / 39 条官方规则 | `rules/`(index.json + md 全文)、`crawl4/all-products-trim.json`(3846 件后台快照)、`engine/`、`上货台-对接抖店方案.md`、`任务书-抖店后台与运营规则.md` |
| **D-调查报告与交付证据/** | 24 份报告 + 逐模块验收证据 | `AI运营中心-总体架构设计.md`、`AI运营中心-统一数据库设计.md`、`AI运营中心-双平台API能力矩阵.md`、`8.0-P0-交付报告-20261110.md`、`8.0-模块1-...md`、`8.0-模块2-...md`、各证据目录 |
| **E-P0开发副本参考/ops/** | 早期开发副本的 ops 实现 | `engine.py`(21KB 旧版) 等 — **参考用，不是生产真源**（真源是 `A/app/ops/`） |
| **F-工具链/github-project-push/** | 把本机项目一条命令打包上传 GitHub 的 skill | `SKILL.md`、`scripts/push.ps1`、`assets/gitignore-template.txt` |

> ⚠️ 判断「生产真源」的口径：**`A-桌面主程序/app/ops/` 是唯一在跑的代码**；`E-` 是历史副本，`D-` 是文档证据。改代码改 A，别改 E。

---

## 4. 架构（三进程 + 两库 + 两连接器 + 队列 + 审批闸）

```
┌─ ① 桌面 PyQt6  run.pyw（现有进程，12 个页签，末项 = 8.0）─────────┐
│   1—6.0 上货流水线（原样）        +  8.0 · AI 运营中心（只读+发命令）│
│   app/ui/tab_ops.py  ← 页面与按钮                                   │
└──────────────────┬────────────────────────────────────────────────┘
                   │ HTTP 127.0.0.1:9192  /api/ops/*
┌──────────────────▼────────────────────────────────────────────────┐
│ ② ops_engine（9192，唯一写 ops.db 者 / 唯一调度者）                  │
│   engine.py 路由 + HTML 子页 │ queue.py 队列(租约/幂等/重试)         │
│   platform.py 平台采集 │ business.py 业务判断+反馈 │ worker.py       │
└───┬───────────────┬──────────────────────┬────────────────────────┘
    │ 写/读          │ 官方 API             │ 浏览器快照
┌───▼──────┐  ┌─────▼──────────┐  ┌───────▼───────────────────────┐
│ ops.db   │  │ ③ 微信连接器    │  │ ④ 抖店连接器（ego 固定脚本）    │
│ 18 张表  │  │ 9191 wxapi.py   │  │ dy_timed_capture.mjs → 快照JSON │
│ (可写)   │  │ 18 端点 / 2 店  │  │ 无免签官方 API                  │
└──────────┘  └────────────────┘  └─────────────────────────────────┘
   只读 ATTACH → v3.db（本地商品库 28 表，业务数据零写入）
```

**四个必须分清的边界**（最容易误读）：
1. `ops.db` **可写**、`v3.db` **只读 ATTACH**（`UPDATE v3_product` 被 SQLite 拦，实测）。
2. `v3.db` 的 372 件商品**是本地货源库，不是平台商品数**；平台数来自微信 API / 抖店后台快照。
3. 微信小店（`op.jinritemai.com` 之外的 `channels/ec/*`）与**抖音开放平台 `open.douyin.com` 是两套账号体系**：开通店铺 ≠ 能发视频。
4. **商品详情视频** 与 **内容视频发布** 是两类，系统里严格分开，不混淆。

---

## 5. 真实数据（全部有出处，不虚构）

| 平台 / 项 | 数 | 来源 | 口径 |
|:--|:--|:--|:--|
| 微信小店 · 欢轴家居 `wx6ff10d09ff0ee823` | **700** 件 | 官方 API `/api/overview` | 在售 249 / 未上架 356 / 回收站 95 |
| 微信小店 · 崇耿 `wxfc4e24a2ebcb2b0a` | **143** 件 | 官方 API | 在售 136 / 未上架 5 / 回收站 1 / 自主下架 1 |
| 抖店后台 | **3846** 件 | ego 浏览器 `/ffa/g/list` 全状态 tab 快照 | `status 0=3630 / 1=216`；源 2026-10-29 16:03:37，**页面标「历史全店快照（非实时）」** |
| 逐行商品明细 | 94 + 3846 | 微信 API 分页 + 抖店快照 | 含商品ID / 标题 / 状态 / 价格 / 库存 / 本地ID匹配 |
| `v3.db` 本地库 | 372 商品 / 194 视频 / 18353 图 / 28 表 | 只读 ATTACH | **本地货源，不等于平台数** |
| 短视频草稿 | 194 详情视频 + 194 内容视频 | 从 v3.db 视频记录派生 | 两类分开，均为草稿 |
| 活动 | 39 条官方规则 / 24 报名资料草稿 / 20 条限时限量购 | `C/rules/` + 本地成本核算 | **无平台实时活动列表** |
| 引擎 | `version=8.0-P0.3.0` `schema=4` `ops_tables=18` `integrity=ok` `writes_scope=ops_* only` | `/api/ops/health` | 实测 |

**抖店为什么是「快照」**：抖店没有可直连的免签官方 API。做法是**登录商品后台、固定浏览器脚本读全状态 tab**，落快照 JSON 入库；`business.auto_loop` 每 **1800 秒** 自动重采（真跑过：`runs=1 / errors=0`）。固定脚本在 `A/app/ops/dy_timed_capture.mjs`，产物 `dy-timed-snapshot.json` 一出现即被下一次采集替换为实时数。

---

## 6. 已经能直接操作的页面（不是空菜单）

**模块1 子页「01 · 双平台真实商品中心」**：3 张真表 + 顶栏「⟳ 采集平台真实数据」按钮（真点、后台采集、轮询、自动回读）；三店概览 / 逐行明细 / 状态统计 / 读取失败记录 / 商品ID匹配 / 页面截图，全部落在 `D-.../8.0-模块1-证据-20261110/`。

**模块2 子页「02 · 真实业务运营与反馈」** 五个可点子页：
| 子页 | 内容 | 按钮反馈 |
|:--|:--|:--|
| 操作反馈 | 每次操作一条 `running→done/failed`，含结果/错误/开始/完成/耗时 | 6 秒自刷 + 点完立即回读 |
| 抖店后台真商品明细 | 3846 行 | — |
| 短视频运营 | 194+194 两类草稿 | `video.prepare` / `video.approve` |
| 限时限量购·活动 | 24 报名草稿 + 20 条核算 | `campaign.approve` |
| 账号运营 | 3 家店铺探活 | `account.probe` |

> 真点 9 个动作 + 定时 1 次 = **10 条反馈，done=10 / failed=0**（详见模块2 交付报告）。

---

## 7. 怎么改（详见 [03_怎么改.md](03_怎么改.md)）

加一个业务模块的**固定六步**：
1. `A/app/ops/schema.sql` 加 `ops_*` 表 + 升 schema 版本；
2. `migrate.py` 幂等建表（重跑不炸）；
3. `A/app/ops/<模块>.py`：采集 / 业务判断 / 只写 `ops_*`；
4. `engine.py` 加 GET/POST 路由 + HTML 子页；
5. `A/app/ui/tab_ops.py` 加子页入口与按钮；
6. 每个按钮写一条 `ops` 反馈（running→done/failed）+ 回读接口 + 失败记录。

**验收口径**：`/health` 版本号与表数 ↑、offscreen `MainWindow()` 仍 12 页签、新接口 200、真点按钮反馈 done、`v3.db` 逐表行数与 `integrity=ok` 不变、原 1—6.0 页签全在。改前留 `.bak`，`ops_*` 表独立可删，回滚见 03。

---

## 8. 红线（改代码时别踩）

- **不自动发布真实视频**；发布前必须人工审批。
- **不未经确认改商品价格 / 库存 / 报名活动 / 上架下架**。
- `v3.db` **业务零写入**，只 `mode=ro` ATTACH。
- **不影响原上货台 1—6.0**：纯增量挂载，`bridge.py` 既有函数签名不改。
- **不虚构**曝光 / 点击 / 违规 / 活动 / 成交数据；拿不到就标「未取得」并写原因。
- 两个微信小店 + 抖店**独立识别**，不混用 shop_id。
- 本地库数量**不得冒充**平台实际商品数量。

---

## 9. 怎么在本地跑起来

```
桌面快捷方式「上货台（源码版·改动立刻生效）.lnk」
或 F:\pyenvs\douyin\Scripts\python.exe  A-桌面主程序/run.pyw
→ 主窗口最后一个页签「8.0 · AI 运营中心」
```
- 9192 引擎由主窗口 `QTimer` 后台起，健康检查 `http://127.0.0.1:9192/api/ops/health`。
- 微信适配器（9191）与抖店 ego 浏览器空间是外部依赖；本仓库**不含**它们的运行时登录态与数据库。
- 自测：`python A-桌面主程序/tests_selftest.py`（offscreen 建窗口，21 项断言）。
- 更多见 [A-桌面主程序/说明.md](A-桌面主程序/说明.md)。

---

## 10. 未取得 / 没做完（如实，别当成「没问题」）

- 抖店**实时**在售数：只有历史全店快照；实时快照脚本需在 ego 已登录空间跑一次才生效。
- 平台**当前可报名活动列表**、**曝光 / 点击 / 成交数据**：两平台都无开放数据接口 → AI 优化无流量数据可算，只能给定性建议。
- 视频号**无官方发布 API**；抖音视频发布需 `open.douyin.com` 另一套应用与权限。
- 模块3—6 未做完（见 §2）；「限时限量购」是本地成本核算草稿，**不是已报名的平台活动**。
- 微信部分接口报 `48001 未授权`（接口存在、账号未开通），不是「没有接口」——详见 `D-.../AI运营中心-双平台API能力矩阵.md`。

---

*本仓库由 `github-project-push` skill 生成并公开。反馈与修改建议：请按 §0 的四条格式给，落到具体文件与层。*


---

# 【llms.txt】

# shanghuotai-8-0-ai-ops — 上货台 8.0 AI 运营中心

> 双平台（抖店 + 两个微信小店）日常运营桌面系统，Python 3.13 + PyQt6。
> 结构化快照：源码 + 报告 + 交付证据，供外部 AI 阅读与评审。版本 8.0-P0.3.0 · 2026-11-11。
> 生产真源 = `A-桌面主程序/app/ops/`；`E-P0开发副本参考/` 是历史副本，不是真源。

## 阅读顺序（AI / 评审用）

- [README.md](README.md): 总览 · 现状 · 六层目录 · 架构 · 真实数据 · 红线 · 怎么跑
- [00_读我_AI-FIRST.md](00_读我_AI-FIRST.md): 给外部 AI 的读法、边界、提建议的四条格式
- [01_架构.md](01_架构.md): 三进程 + 两库 + 两连接器 + 队列 + 审批闸，端口与数据流
- [02_现状与边界.md](02_现状与边界.md): 已交付 / 未做 / 未取得 / 别误读清单
- [03_怎么改.md](03_怎么改.md): 加模块六步、改哪个文件、验收、回滚

## 生产源码入口

- [A-桌面主程序/app/ops/engine.py](A-桌面主程序/app/ops/engine.py): 9192 引擎路由 + HTML 子页
- [A-桌面主程序/app/ops/platform.py](A-桌面主程序/app/ops/platform.py): 微信官方 API + 抖店快照接入
- [A-桌面主程序/app/ops/business.py](A-桌面主程序/app/ops/business.py): 业务页 + 操作反馈 + 定时采集
- [A-桌面主程序/app/ops/schema.sql](A-桌面主程序/app/ops/schema.sql): ops_* 表 DDL（schema v4 / 18 表）
- [A-桌面主程序/app/ui/tab_ops.py](A-桌面主程序/app/ui/tab_ops.py): 8.0 页签与所有子页/按钮
- [B-微信小店适配器/wxapi.py](B-微信小店适配器/wxapi.py): 微信 9191 官方 API 封装
- [C-抖店研究与规则库/](C-抖店研究与规则库/): 抖店后台快照 + 39 条官方规则全文

## 交付与证据报告

- [D-调查报告与交付证据/8.0-P0-交付报告-20261110.md](D-调查报告与交付证据/8.0-P0-交付报告-20261110.md): P0 底座验收
- [D-调查报告与交付证据/8.0-模块1-双平台真实商品中心-交付报告-20261110.md](D-调查报告与交付证据/8.0-模块1-双平台真实商品中心-交付报告-20261110.md): 模块1 验收
- [D-调查报告与交付证据/8.0-模块2-真实业务运营-交付报告-20261110.md](D-调查报告与交付证据/8.0-模块2-真实业务运营-交付报告-20261110.md): 模块2 验收
- [D-调查报告与交付证据/AI运营中心-总体架构设计.md](D-调查报告与交付证据/AI运营中心-总体架构设计.md): 架构设计全文
- [D-调查报告与交付证据/AI运营中心-双平台API能力矩阵.md](D-调查报告与交付证据/AI运营中心-双平台API能力矩阵.md): 两平台 25 项能力逐条口径
- [D-调查报告与交付证据/AI运营中心-统一数据库设计.md](D-调查报告与交付证据/AI运营中心-统一数据库设计.md): 库表设计

## 边界提醒

本仓库 = 快照，不是可直接运行的部署包。运行时 `ops.db` / `v3.db`、密钥、`dist/`、浏览器登录态未收。
「未取得」项是真的没拿到，不是「没有」。给建议请注明依据文件、现有/新增、落在哪一层、怎么验收。


---

# 【00_读我_AI-FIRST.md】

# 00 · 读我（AI-FIRST）

> 本文件是**给外部 AI / 协作评审方**读的第一站。目标是让你在不看本机、不跑服务的前提下，
> 读懂这套系统「是什么 / 由哪几层组成 / 现在做到哪 / 哪些没做 / 怎么改」，并给出能落地的建议。

---

## 1. 这是什么

「上货台 8.0 · AI 运营中心」是一套 **Windows 桌面运营系统**（Python 3.13 + PyQt6 原生窗口），
主人用它每天管理 **抖店 + 两个微信小店** 的日常运营：看平台真实商品、看异常和优化、看视频/活动/账号、
点按钮执行、看每次操作的反馈。它不是网页套壳，也不内嵌浏览器。

原来的「上货台 1—6.0」上货流水线（采集 → 精筛 → 打码 → 出表）**完整保留**，
8.0 是主窗口最后新增的一个页签，纯增量挂载。

## 2. 这个仓库是什么、不是什么

| 是 | 不是 |
|:--|:--|
| 生产源码的结构化快照（`A/app/ops/` 是真正在跑的代码） | 可直接 `pip install` 跑起来的部署包 |
| 24 份调查/设计/交付报告 + 逐模块验收证据 | 一份「未来打算做」的规划书 |
| 供你评审、给修改建议的输入 | 需要你补写或重写的东西 |

**未收的东西**：运行时数据库 `ops.db` / `v3.db`、密钥与登录态、编译产物 `dist/` `build/`、
`node_modules`、`__pycache__`、备份 `_backup-*`、探针 `_验_*`/`_探_*`、单文件 >8MB 的大素材。
**读到 0、读到「未取得」、读到「包里没有」——按原文口径理解，不要推断为「系统里没有」。**

## 3. 阅读顺序

```
README.md            → 总览/现状/目录/架构/真数据/红线
llms.txt             → 全文索引（含每个关键源码与报告的链接）
00_读我_AI-FIRST.md  → 本文件：读法与边界
01_架构.md           → 三进程/两库/两连接器/队列/审批闸
02_现状与边界.md     → 已交付/未做/未取得/别误读
03_怎么改.md         → 加模块六步 + 验收 + 回滚
```

**想深入某一块**：直接跳 `llms.txt` 里「生产源码入口」或「交付与证据报告」那两节的链接。

## 4. 提建议的四条格式（务必遵守）

每/条建议都写清：

1. **依据哪个文件**（`路径:行` 或具体函数/表名）—— 没出处的结论不采纳；
2. **是现有能力还是需新增** —— 别把「已经有」当「要开发」，也别把「没做」当「有 bug」；
3. **改动落在哪一层** —— 桌面 UI(`app/ui/`) / 9192 引擎(`app/ops/engine.py`) /
   `ops.db`(schema.sql) / 平台连接器(`platform.py`、`B/wxapi.py`、`C/` 快照) /
   浏览器脚本(`dy_timed_capture.mjs`)；
4. **怎么验收** —— 接口 200？health 表数？真点按钮反馈 done？`v3.db` 行数不变？

## 5. 三个最容易踩的误读

- **别拿 `E-P0开发副本参考/` 当真源**：那是早期开发副本，生产真源是 `A-桌面主程序/app/ops/`。
- **别把 `v3.db` 的 372 件商品当平台商品数**：那是本地货源库；平台数来自微信官方 API(700/143) 与抖店后台快照(3846)。
- **别把「限时限量购 20 条」「报名资料 24 条」当已报名的平台活动**：那是本地核算/资料草稿，平台实时活动列表**未取得**。

## 6. 数据与口径

- 每条结论带等级：交付报告/实测 JSON = `[事实]`；架构设计里有 `[设计建议]`；「未取得」= 真的没拿到。
- 版本：`8.0-P0.3.0` · `schema=4` · `ops_tables=18` · `writes_scope=ops_* only` · `v3.db` 只读 ATTACH。
- 三店独立识别：欢轴家居 / 崇耿 / 抖店，shop_id 不混用。

## 7. 来源与冲突裁决

同一件事以**运行时实测 > 交付报告 > 设计文档 > 代码注释**为准。
设计文档里写「打算做」而交付报告写「已实测」的，以后者为准；
两者都没写的，视为**未做**，请按「需新增」提建议，不要假设已存在。

## 8. 一句话请求

**请基于这个仓库，给「怎么把 8.0 从『底座 + 模块1/2』推进到模块3—6」的具体、可落地、逐文件、可验收的修改建议**，
并指出你认为当前架构里最该先补的一处。不要重写整体架构（架构已定），不要泛泛而谈。


---

# 【01_架构.md】

# 01 · 架构

> 依据：`D-调查报告与交付证据/AI运营中心-总体架构设计.md`（含 `[设计建议]`）、
> `8.0-P0-交付报告-20261110.md`（`[事实]` 实测）、`A/app/ops/*` 源码。
> 设计文档里的部分内容是**目标态**，当前落地到「模块2」，以 §6 的实际落地为准。

---

## 1. 一句话拓扑

**三进程 + 两个库 + 两条连接器 + 一条队列 + 一道审批闸。**

```
① 桌面 PyQt6 (run.pyw)          ← 只读展示 + 发命令，12 页签，末项 8.0
② ops_engine (9192)             ← 唯一写 ops.db 者、唯一调度者
③ 浏览器执行器 (ego 固定脚本)   ← 只跑固定脚本读抖店后台，不思考、不发布
```

## 2. 分层与端口

| 层 | 进程/端口 | 代码 | 职责 |
|:--|:--|:--|:--|
| 桌面 UI | 主进程 | `A/app/ui/main_window.py`、`tab_ops.py` | 12 页签；8.0 内 7+ 子页；按钮发 HTTP |
| 运营引擎 | **9192** | `A/app/ops/engine.py`、`platform.py`、`business.py`、`queue.py`、`store.py`、`worker.py` | 路由 + HTML 子页 + 采集 + 业务判断 + 反馈 + 定时 |
| 微信连接器 | **9191** | `B/app.py`、`wxapi.py`、`publish.py`、`dd.py` | 官方 API，18 端点已通，2 个真实店铺 |
| 抖店连接器 | ego 浏览器空间（非常驻 HTTP） | `A/app/ops/dy_timed_capture.mjs`、`C/engine/*.mjs` | 登录后台读 `/ffa/g/list` 全状态 tab → 快照 JSON |
| 数据 | 文件 | `ops.db`（可写）/ `v3.db`（只读 ATTACH） | 运营表 / 本地货源库 |

## 3. 数据流（谁写谁）

```
微信官方API ──(X-Shop-Id 切店)──┐
                                ├─→ platform.py 采集 ─→ 只写 ops.db 的 ops_platform_* 表
抖店后台快照JSON ───────────────┘        │
                                         ├─→ 每条来源/更新时间/成功失败 落 ops_platform_fetch
v3.db (mode=ro) ─→ TEMP VIEW ───────────┴─→ 商品ID匹配 / 状态统计（只读）
                                         
按钮点击 ─→ engine.py POST ─→ business.py ─→ ops_ops(反馈表) running→done/failed
定时(1800s) ─→ business.auto_loop ─→ 重采快照 ─→ 回读
```

**唯一写库者 = 9192 引擎**；桌面 UI 不直接碰库；`v3.db` 通过 `ATTACH 'file:<v3.db>?mode=ro' AS vsrc` +
`CREATE TEMP VIEW` 只读，`UPDATE v3_product` 被 SQLite 拦（实测）。

## 4. 两个库、两种权限（硬边界）

| | `ops.db` | `v3.db` |
|:--|:--|:--|
| 权限 | 可写（rwc） | **只读 ATTACH（ro）** |
| 内容 | 18 张 `ops_*` 表：平台商品/状态/采集/业务/视频/活动/账号/反馈/队列/日志 | 28 张表：372 商品 / 194 视频 / 18353 图 |
| 谁建 | `migrate.py` 幂等建表，schema v4 | 既有生产库，不改列、不写业务行 |
| 迁移动作 | 加表 + 升版本号 | 零改动，前后取 sha256 + 逐表行数对账 |

## 5. ops.db 表分组（schema v4 / 18 表）

```
P0 底座    ops_task / ops_task_event / ops_run / ops_shop / ops_config ...
模块1      ops_platform_product / ops_platform_fetch / ops_platform_status ...
模块2      ops_biz_*（视频草稿 / 活动草稿 / 账号 / 操作反馈）...
```
> 精确 DDL 与字段名见 `A/app/ops/schema.sql`；设计全貌见 `D-.../AI运营中心-统一数据库设计.md`。

## 6. 队列、审批闸、AI 的位置

- **队列** `ops_task`：租约 120s / 幂等键 / `max_attempts` 重试上限 / `claim·heartbeat·complete·fail·cancel·recover` 状态机。
- **审批闸**：改价、改库存、报名活动、发布视频属高风险，**必须人工确认**才执行；低风险（采集、草稿、标记）可自动。
- **AI 不在执行链上**：固定脚本跑采集、规则查判断；只有异常才叫本地 AI 诊断，诊断结果只进「候选修复」，不直接改生产流程。
  当前仓库里**没有**接入常驻大模型推理，业务判断是确定性的 Python 规则。

## 7. 抖店为什么走浏览器（不是偷懒）

`op.jinritemai.com` 的自研应用需软著 + 审核，本机**没有**可直连的免签官方商品 API（`dd.py` 原文有说明）。
因此：登录商品后台 → 固定脚本读全状态 tab（在售/草稿/审核/驳回）→ 拦接口 + DOM 兜底 → 落快照 JSON。
`business.auto_loop` 每 1800s 重采；`dy-timed-snapshot.json` 一旦由浏览器刷新产出，下一次采集自动换成实时数。

## 8. 当前实际落地（对照设计文档）

| 设计目标 | 落地 |
|:--|:--|
| 独立进程引擎 9192 | ✅ 进程内线程启停（主窗口 QTimer 后台起） |
| ops.db + v3.db 只读 | ✅ 12→18 表，ro ATTACH 实测 |
| 微信连接器 18 端点 | ✅ 复用 9191 既有服务 |
| 抖店连接器 | ✅ 快照链路 + 定时脚本（实时刷新待 ego 登录态跑一次） |
| 审批闸 L1–L4 | 🟡 部分：红线在业务代码里硬拦（无发布/无改价改库存/无报名） |
| browser_executor 独立进程 | 🟡 现为固定 .mjs 脚本 + 定时重采，未拆独立常驻进程 |
| ops_metric_daily / 曝光点击 | ❌ 两平台无开放数据，未做（如实标「未取得」） |
| ops_content / ops_publish_job 9 状态机 | ❌ 模块5 未做，且不自动发布 |

---

*架构已定，请勿在建议里重提「合并还是分开」「要不要重写桌面壳」——这些决策已完成，见 `README.md` §0。*


---

# 【02_现状与边界.md】

# 02 · 现状与边界

> 全部 `[事实]` 来自交付报告与实测 JSON；「未取得 / 未做」是如实登记，不是「没问题」。
> 判据：**交付报告写了「已实测」= 已交付；只写在设计文档里 = 未做。**

---

## 1. 已交付（能在软件里直接操作和验收）

### P0 底座（`8.0-P0-交付报告-20261110.md`）
- 主窗口 **12 个页签**，末项「8.0 · AI 运营中心」，原 1—6.0 一个不少。
- 9192 引擎：启动/停止/健康检查全流程实测（优雅停无孤儿端口）。
- `ops.db` 12→18 张 `ops_*` 表；`v3.db` 只读 ATTACH（`UPDATE v3_product` 被拦）。
- `health`: `ok=true / version=8.0-P0.1.0→P0.3.0 / writes_scope=ops_* only / platform_calls=0`。
- 任务队列 6 条、状态事件 22 条、运行记录 8 条，页签可见。

### 模块1 双平台真实商品中心（`8.0-模块1-...-交付报告-20261110.md`）
- 微信官方 API 权威数：欢轴 **700**（249/356/95）、崇耿 **143**（136/5/1/1）。
- 逐件明细 94 行；商品ID匹配 `matched=32 / unmatched=62`。
- 抖店在售总数**如实留空「未取得」**，只摆草稿快照 3 + 审核驳回 CSV 4 共 7 行真明细 —— **不拿本地 372 件冒充**。
- 6 个接口 200、14 次读取零失败、三张真表 + 采集按钮（真点、轮询、回读）。

### 模块2 真实业务运营与反馈（`8.0-模块2-...-交付报告-20261110.md`）
- 抖店后台 **3846 件**真商品逐行入库（status 0=3630 / 1=216）。
- 定时采集 `business.auto_loop` 每 **1800s**，真跑 `runs=1 / errors=0`；固定刷新脚本 `dy_timed_capture.mjs` 落盘且语法通过。
- 5 个业务子页：操作反馈 / 抖店明细 / 短视频 / 限时限量购·活动 / 账号运营。
- 真点 9 动作 + 定时 1 次 = **10 条反馈，done=10 / failed=0**。
- 版本 `8.0-P0.3.0 / schema=4 / ops_tables=18 / integrity=ok`。

## 2. 未做（按优先级排期，别当成 bug）

| 模块 | 内容 | 状态 |
|:--|:--|:--|
| 3 | 商品视频自动匹配 / 上传 / 绑定 / 失败重试 / 绑定状态表 | ⏳ 未做 |
| 4 | 平台活动发现 / 筛选 / 报名辅助 | ⏳ 仅 24 条报名资料草稿，无实时活动列表 |
| 5 | 抖音 / 视频号内容推广、发布时挂商品 | ⏳ 未做，且红线「不自动发布真实视频」 |
| 6 | 每日自动运营 + AI 分析日报 | ⏳ 定时采集已跑，日报未做 |

## 3. 未取得（真的没拿到，不是「没有」）

- **抖店实时在售数**：只有 2026-10-29 16:03:37 的历史全店快照，页面明确标「非实时」；实时刷新脚本需在 ego 已登录空间跑一次。
- **平台当前可报名活动列表**：官方无开放接口 → 不允许虚构，标未取得。
- **曝光 / 点击 / 成交 / 搜索商机 / 猜你喜欢入池数据**：两平台都无开放数据 → AI 优化**无流量数据可算**，只能给定性建议。
- **微信部分数据接口 `48001 未授权`**：接口存在、账号未开通，不是「没有接口」（详见 API 能力矩阵）。
- **体验分权重 40/25/35**：官方页只写「按行业差异化」，权重表是图片/附件，正文无数值 → 标未核准。

## 4. 设计如此（不是缺陷）

- **视频号无官方发布 API**；抖音视频发布走 `open.douyin.com` 另一套账号与权限，**开通抖店 ≠ 能发视频**。
- **商品详情视频** 与 **内容视频发布** 是两类，系统里严格分开。
- 抖店自研应用需软件著作权 + 审核（1–3 工作日），微信小店填了就有 —— 两边门槛不同，别混用经验。
- 微信「10 单出分」vs 抖店「近 30 天有效支付 ≥30 单出分」，权重口径也不同，别互相套用。

## 5. 别误读清单

| 容易看错 | 正确口径 |
|:--|:--|
| `E-P0开发副本参考/ops/` 是生产代码 | 生产真源是 `A-桌面主程序/app/ops/`；E 是历史副本 |
| `v3.db` 372 件 = 平台商品数 | 本地货源库；平台数 = 微信 700/143 + 抖店快照 3846 |
| 「限时限量购 20 条」= 已报名的活动 | 本地成本核算草稿，不是平台活动 |
| 「操作反馈 done=10」= 已发布/已改价 | 只是采集/草稿/标记/审批动作成功，零发布零改价零库存零报名 |
| 抖店「3846」是此刻实时数 | 历史全店快照（源 2026-10-29），页面标非实时 |
| 页面看到「0」= 系统里没有 | 可能是硬排除未收 / 接口未取得 / 草稿未生成，按原文理解 |

## 6. 红线（验收与二次开发都要守）

不自动发布真实视频 · 不未经确认改商品价格/库存/报名活动/上下架 · `v3.db` 业务零写入 ·
不影响原 1—6.0 · 不虚构曝光/点击/违规/活动/成交 · 三店独立识别 · 本地库数量不冒充平台数。

## 7. 已知小问题（登记未动，范围外）

- `tab_detect.py:600` `info=a` 未定义（既有，上轮已登记，本任务未触碰）。
- 9192 曾出现启动器父进程 + 实际 LISTEN 子进程，health 三次同一 instance，无双活。
- `v3.db` 文件头 SHA 在 offscreen 完整 MainWindow 实例化期间变化过一次（WAL/元数据触碰），逐表行数与 `integrity=ok` 未变。


---

# 【03_怎么改.md】

# 03 · 怎么改

> 给准备接手二次开发的人 / AI。架构已定（见 `01_架构.md`），**不要重写桌面壳、不要改 `bridge.py` 既有函数签名**。
> 所有改动落在 `A-桌面主程序/app/ops/` 与 `A-桌面主程序/app/ui/tab_ops.py`，纯增量。

---

## 1. 改之前先记牢的四条

1. **生产真源 = `A-桌面主程序/app/ops/`**；`E-P0开发副本参考/` 只读作参考，改了不生效。
2. 改任何代码/配置前**先留 `.bak-<用途>-<时间戳>`**，改完能一句话回滚。
3. **只写 `ops_*` 表**；`v3.db` 全程 `mode=ro` ATTACH，不改列、不写业务行。
4. 每个新按钮必须配**一条操作反馈**（running→done/failed）+ 回读接口 + 失败记录，否则「操作以后看不到状态」。

---

## 2. 加一个业务模块：固定六步

以「模块3 商品视频自动匹配 / 上传 / 绑定」为例，任何模块照此套：

### Step 1 · 加表
`A/app/ops/schema.sql` 里加 `ops_video_bind` 等 `ops_*` 表，**把 schema 版本号 +1**（当前 v4）。
字段带：来源、商品ID、平台、状态、结果、错误、开始/完成时间、更新时间。

### Step 2 · 幂等迁移
`A/app/ops/migrate.py`：新表 `CREATE TABLE IF NOT EXISTS`；重跑不炸、不覆盖旧数据；
跑前后对 `v3.db` 取 sha256 + 逐表行数，断言 `v3_unchanged=True`。

### Step 3 · 业务实现
新建 `A/app/ops/<模块>.py`（参照 `platform.py` 的采集写法、`business.py` 的判断+反馈写法）：
- **采集**：微信走 `B/wxapi.py` 官方端点；抖店走 `dy_timed_capture.mjs` 产出的快照 JSON；
- **判断**：确定性规则（缺图/缺视频/标题/详情/库存/价格异常…），抓不到就标「未取得」并写原因；
- **落库**：只写 `ops_*`，每条带 `source / updated_at / read_ok / error`；
- **匹配**：按商品ID关联两平台与 `v3.db` 的 TEMP VIEW（只读）。

### Step 4 · 引擎路由 + 页面
`A/app/ops/engine.py`：
- 加 `GET /api/ops/<模块>/...`（列表 / 统计 / 状态 / 回读）与 `POST /api/ops/<模块>/<动作>`；
- 加一个 HTML 子页（照现有 `PLATFORM_HTML` / 业务页的模板），含表格 + 可操作按钮；
- 动作进 `worker.py` 队列或直接同步执行，写一条 `ops` 反馈记录。

### Step 5 · UI 入口
`A/app/ui/tab_ops.py`：在 8.0 页签里加一个子页入口与按钮，按钮 `clicked` → 调 Step 4 的 POST；
点完**立即回读一次**，页面 6 秒自刷（照模块2「操作反馈」子页）。

### Step 6 · 验收 + 证据
- `/api/ops/health` 版本号与 `ops_tables` 上升、`integrity=ok`、`writes_scope=ops_* only`；
- offscreen `MainWindow()` 仍 **12 页签**，新子页出现，原 1—6.0 全在；
- 新接口逐条 200、真点按钮反馈 `done`、失败项有 `error` 原文；
- `v3.db` 逐表行数与 `integrity=ok` 与开工前一致；
- 截图 + 接口 JSON + 指纹落 `D-调查报告与交付证据/8.0-模块N-证据-<日期>/`，写一份交付报告。

---

## 3. 分模块「改哪里」速查

| 想改 | 文件 |
|:--|:--|
| 平台商品采集逻辑 / 微信 API 调用 / 抖店快照解析 | `A/app/ops/platform.py` |
| 业务判断 / 操作反馈 / 定时 `auto_loop` | `A/app/ops/business.py` |
| 新接口 / 新 HTML 子页 / health 字段 | `A/app/ops/engine.py` |
| 表结构 / schema 版本 | `A/app/ops/schema.sql` + `migrate.py` |
| 队列状态机 / 重试 | `A/app/ops/queue.py` + `worker.py` |
| 8.0 页签 / 子页 / 按钮 | `A/app/ui/tab_ops.py` |
| 微信官方 API 封装 | `B/wxapi.py` + `B/app.py` |
| 抖店后台快照脚本 | `A/app/ops/dy_timed_capture.mjs` + `C/engine/*.mjs` |
| 版本常量 | `A/app/ops/config.py` |
| 抖店规则库 | `C/rules/index.json` + `C/rules/md/*.md`（有 `更新.py`，sha1 未变跳过） |

---

## 4. 红线怎么在代码里落

- **发布/改价/改库存/报名**：动作不要直接调用平台写接口；先写「候选/审批」状态，
  由人工确认后置 `approved` 才执行；未确认一律 `manual.pending`。
- **不自动发布视频**：`video.prepare` / `video.approve` 只到「草稿/已审批」，没有真实 POST 到发布端点。
- **`v3.db` 只读**：`store.py` 用 TEMP VIEW，别改成可写 ATTACH。
- **不虚构数据**：拿不到的字段写 `NULL` + `error` 原因 + 「未取得」，不要用本地库或随机数填。

---

## 5. 验收命令（照抄）

```
# 引擎健康
curl http://127.0.0.1:9192/api/ops/health
#   → ok=true version=8.0-P0.3.0 schema=4 ops_tables=18 writes_scope=ops_* only

# 桌面 12 页签（offscreen，不抢焦点）
python A-桌面主程序/tests_selftest.py        # 21 项断言

# 原上货台未受影响
curl http://127.0.0.1:9191/                 # 200
curl http://127.0.0.1:9191/api/shops        # 200，3 店
```

---

## 6. 回滚

- **代码**：把对应的 `.bak-<用途>-<时间戳>` 复制覆盖回同名文件即可。
- **表**：`ops_*` 表全部独立，删掉新增的那几张 + 把 `config.py` 版本号退回，即恢复到改前；
  `v3.db` 与 1—6.0 页面不受影响。
- **仓库**：本仓库每次交付留一份报告与证据目录，按日期可对照回滚。

---

## 7. 建议外部 AI 优先回答的一件事

**模块3（视频自动匹配/上传/绑定）先落哪一步最划算？** 请按本文件 §2 六步给出逐文件 patch 计划，
并说明「商品详情视频」与「内容视频发布」在哪一层分开、绑定状态表字段怎么设计、失败重试怎么写。
不要给脱离 `A/app/ops/` 目录结构的抽象方案。


---

# 附录 A · 关键源码索引（真实行号，从仓库源码抓取）

只列函数 / 类 / 路由签名，用于定位「改哪个文件、哪一行」。完整源码在同名仓库 ZIP 内。

### A-桌面主程序\app\ops\business.py  （30 处）

```
108: def now() -> str:
112: def ensure_tables(con) -> None:
119: def _dy_source() -> tuple:
141: def import_dy_snapshot(con) -> dict:
209: def dy_products(con, q: str = "", limit: int = 200) -> list:
220: def snapshot_status(con) -> dict:
238: def _op(con, module, action, target, actor, state, result="", error="",
248: def _op_done(con, op_id, started_ts, result, note=""):
255: def _op_fail(con, op_id, started_ts, err):
262: def feedback(con, limit: int = 60) -> list:
270: def _http(url: str, timeout: float = 8.0):
279: def account_check(con) -> dict:
316: def account_status(con) -> list:
323: def video_prepare(con) -> dict:
368: def video_rows(con, content_type: str = "", limit: int = 200) -> list:
379: def campaign_prepare(con) -> dict:
427: def campaigns(con, limit: int = 200) -> list:
432: def _set_state(con, table, key, row_id, state, note) -> dict:
449: def run_action(module: str, action: str, payload: dict | None = None,
533: def auto_loop(stop: threading.Event, interval: int | None = None) -> None:
560: def business_overview(con) -> dict:
561: def n(sql, a=()):
666: function loadOverview(){return get('/api/ops/business/overview').then(function(d){
678: function loadOps(){return get('/api/ops/business/feedback?limit=40').then(function(d){
685: return Promise.all([get('/api/ops/business/snapshot'),
686: get('/api/ops/business/dy_products?q='+encodeURIComponent(q)+'&limit=200')])
697: function loadVideo(){return get('/api/ops/business/content?limit=80').then(function(d){
705: function loadCamp(){return get('/api/ops/business/campaigns?limit=80').then(function(d){
713: function loadAcc(){return get('/api/ops/business/accounts').then(function(d){
727: post('/api/ops/business/action',{module:'业务工作台',action:action,payload:payload})
```

### A-桌面主程序\app\ops\config.py  （10 处）

```
46: def db_path() -> Path:
51: def v3_path() -> Path:
56: def port() -> int:
60: def host() -> str:
64: def worker_enabled() -> bool:
68: def recover_interval() -> float:
72: def default_lease() -> int:
76: def ro_enabled() -> bool:
80: def evidence_dir() -> Path:
84: def ensure_dirs() -> None:
```

### A-桌面主程序\app\ops\engine.py  （78 处）

```
6: * 新增 GET  /api/ops/overview | /shops | /task/events | /task/runs
8: * POST /api/ops/engine/stop = 优雅停止（主程序也用进程内 request_stop）
42: def log(msg: str) -> None:
55: def _con():
59: def integrity(refresh: bool = False) -> str:
75: def acquire_file_lock():
101: def release_file_lock():
121: def reaper_loop():
139: def lock_loop():
155: class Handler(BaseHTTPRequestHandler):
159: def log_message(self, fmt, *args):                        # noqa: A003
162: def _send(self, obj, code: int = 200):
174: def _send_html(self, html: str, code: int = 200):
183: def _body(self) -> dict:
192: def _local_only(self) -> bool:
195: def do_GET(self):                                        # noqa: N802
201: if p == "/api/ops/health":
203: if p == "/api/ops/overview":
205: if p == "/api/ops/shops":
207: if p == "/api/ops/products":
213: if p == "/api/ops/videos":
219: if p == "/api/ops/videos/stats":
221: if p == "/api/ops/tasks":
225: if p == "/api/ops/tasks/stats":
227: if p == "/api/ops/task":
230: if p == "/api/ops/task/events":
233: if p == "/api/ops/task/runs":
236: if p == "/api/ops/metrics":
240: if p == "/api/ops/accounts":
242: if p == "/api/ops/errors":
245: if p == "/api/ops/tables":
247: if p == "/api/ops/recovery":
252: if p == "/api/ops/platform/summary":
254: if p == "/api/ops/platform/products":
261: if p == "/api/ops/platform/stats":
263: if p == "/api/ops/platform/status_stats":
265: if p == "/api/ops/platform/fetches":
268: if p == "/api/ops/platform/status":
270: if p == "/api/ops/platform/page":
273: if p == "/api/ops/business/page":
275: if p == "/api/ops/business/overview":
277: if p == "/api/ops/business/snapshot":
279: if p == "/api/ops/business/dy_products":
284: if p == "/api/ops/business/content":
289: if p == "/api/ops/business/campaigns":
293: if p == "/api/ops/business/accounts":
295: if p == "/api/ops/business/feedback":
304: def do_POST(self):                                       # noqa: N802
310: if p == "/api/ops/task/enqueue":
323: if p == "/api/ops/task/claim":
332: if p in ("/api/ops/task/heartbeat", "/api/ops/task/complete",
333: "/api/ops/task/fail"):
349: if p == "/api/ops/task/cancel":
355: if p == "/api/ops/recover":
364: if p == "/api/ops/engine/stop":
367: if p == "/api/ops/media/reconcile":
370: if p == "/api/ops/platform/collect":
375: if p == "/api/ops/business/action":
385: def with_store(self, fn):
391: def api_health(self) -> dict:
437: def api_reconcile(self) -> dict:
450: def _reconcile_impl(self) -> dict:
485: # 8.0 运营总览（自包含 HTML，真数据由 JS 拉 /api/ops/overview + /health）
521: Promise.all([get('/api/ops/health'),get('/api/ops/overview'),get('/api/ops/task/events?limit=12')])
560: # 模块1 · 双平台真实商品中心（自包含页面，真数据由 JS 拉 /api/ops/platform/*）
637: return get('/api/ops/platform/summary').then(function(d){
651: var u='/api/ops/platform/products?limit=300&shop='+encodeURIComponent(sh)+
673: return Promise.all([get('/api/ops/platform/stats'),get('/api/ops/platform/fetches?limit=100')])
693: post('/api/ops/platform/collect',{mode:mode}).then(function(){
695: get('/api/ops/platform/status').then(function(s){
710: get('/api/ops/platform/products?limit=1').then(function(d){
712: return get('/api/ops/platform/products?limit=300').then(function(a){
725: def _serve() -> int:
813: def start_in_thread(worker_on: bool = True) -> bool:
837: def request_stop() -> bool:
851: def is_alive() -> bool:
855: "http://127.0.0.1:%d/api/ops/health" % config.port(),
863: def main() -> int:
```

### A-桌面主程序\app\ops\migrate.py  （5 处）

```
30: def _sha256(path: Path) -> str:
38: def v3_snapshot() -> dict:
63: def _fetch_shops() -> dict:
75: def sync_accounts(con, shops: list) -> dict:
106: def main(argv=None) -> int:
```

### A-桌面主程序\app\ops\platform.py  （18 处）

```
102: def now() -> str:
106: def ensure_tables(con) -> None:
113: def _http(url: str, headers: dict | None = None, timeout: float = 30.0):
125: def _record_fetch(con, shop_id, source, method, http_ok, rows, ms, error, note=""):
136: def _id_maps(con) -> tuple:
150: def _match(pid, wx_map, dy_map, platform) -> tuple:
162: def _wx_shop(con, shop, wx_map, dy_map, pages_per_status: int) -> dict:
231: def _upsert_shop(con, shop, out, pages_per_status, elapsed_ms):
251: def _dy_shop(con, shop, wx_map, dy_map) -> dict:
357: def collect_all(mode: str = "snapshot", shop_filter: str = "") -> dict:
399: def collect_async(mode: str = "snapshot", shop_filter: str = "") -> dict:
406: def _run():
422: def status() -> dict:
432: def summary(con) -> dict:
456: def products(con, shop: str = "", status_code: str = "", q: str = "",
474: def stats(con) -> dict:
488: def fetches(con, limit: int = 100) -> list:
493: def status_stats(con) -> dict:
```

### A-桌面主程序\app\ops\queue.py  （20 处）

```
34: def _attach_v3(con: sqlite3.Connection) -> None:
51: def connect(db=None, readonly: bool = False, timeout: float = 5.0) -> sqlite3.Connection:
71: def tx(con: sqlite3.Connection):
85: def now_text() -> str:
89: def now_epoch() -> int:
96: def _event(con, task_id: str, from_state, to_state: str, actor: str, detail: str = "") -> None:
107: def enqueue(con, kind: str, payload=None, platform=None, product_id=None,
136: def claim(con, worker: str, lease_s: int = None, kinds=None):
168: def heartbeat(con, task_id: str, worker: str, lease_s: int = None) -> bool:
179: def complete(con, task_id: str, worker: str = None, result=None) -> bool:
195: def fail(con, task_id: str, worker: str = None, error: str = "", retry: bool = True,
225: def cancel(con, task_id: str, actor: str = "api") -> bool:
237: def recover_expired(con, actor: str = "reaper") -> dict:
275: def stats(con) -> dict:
284: def get_task(con, task_id: str, with_events: bool = True) -> dict | None:
297: def list_tasks(con, state: str = None, limit: int = 50) -> list:
308: def list_events(con, limit: int = 100) -> list:
314: def list_runs(con, limit: int = 100) -> list:
320: def acquire_lock(con, key: str, owner: str, ttl: int = 30) -> bool:
336: def release_lock(con, key: str, owner: str) -> None:
```

### A-桌面主程序\app\ops\store.py  （16 处）

```
15: def _ro(db=None):
19: def _fallback(db=None):
23: def query(db, fn):
40: def health(con: sqlite3.Connection, integrity: str = None) -> dict:
65: def products(con, limit: int = 50, q: str = None, status: str = None) -> list:
88: def product_one(con, product_id: int) -> dict | None:
96: def videos(con, limit: int = 50, product_id: int = None) -> list:
115: def video_stats(con) -> dict:
131: def metrics(con, day: str = None, platform: str = None, limit: int = 200) -> dict:
150: def accounts(con) -> list:
160: def errors(con, limit: int = 50) -> list:
168: def table_counts(con) -> dict:
183: def v3_attached(con) -> bool:
192: def v3_counts(con) -> dict:
209: def shops(con) -> dict:
226: def overview(con) -> dict:
```

### A-桌面主程序\app\ops\worker.py  （3 处）

```
16: def _sleep_with_heartbeat(con, task_id, worker, seconds, lease_s):
27: def run_one(task: dict, worker: str, lease_s: int) -> tuple:
52: def worker_loop(ttl_lease: int, stop_flag):
```

### A-桌面主程序\app\ui\buildstamp.py  （9 处）

```
27: def process_started() -> float:
73: def fmt(ts: float) -> str:
77: def mode_label() -> str:
81: def source_max_mtime() -> tuple[float, str]:
100: def exe_mtime() -> float:
109: def status(proc_started: float | None = None) -> dict:
135: def _pyw() -> str:
147: def restart_plan() -> tuple[list[str] | None, str]:
167: def spawn(argv: list[str]) -> bool:
```

### A-桌面主程序\app\ui\cat_map_dialog.py  （8 处）

```
22: class CatMapDialog(QDialog):
23: def __init__(self, parent=None, pids: list | None = None):
110: def reload(self):
133: def _pick(self, cur, _prev=None):
147: def _save(self):
169: def _clear(self):
179: def _apply(self, platform: str):
190: def _check(self):
```

### A-桌面主程序\app\ui\criteria_dialog.py  （11 处）

```
45: class CriteriaDialog(QDialog):
46: def __init__(self, parent=None):
150: def _load(self):
163: def _load_section(self):
171: def _commit_section(self) -> bool:
189: def _switch(self, _idx=None):
194: def _refresh_prompt(self):
203: def _save(self):
215: def _reset(self):
229: def _try(self):
256: def closeEvent(self, e):                            # noqa: N802
```

### A-桌面主程序\app\ui\dlcenter_win.py  （30 处）

```
55: def _mb(n) -> str:
67: def _spd(n) -> str:
79: class TaskModel(QAbstractTableModel):
80: def __init__(self, rows=None, parent=None):
84: def set_rows(self, rows):
89: def rowCount(self, parent=QModelIndex()):  # noqa: N802
92: def columnCount(self, parent=QModelIndex()):  # noqa: N802
95: def headerData(self, sec, orient, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
100: def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
157: class DlCenterWindow(QWidget):
160: def __init__(self, job_id: int = 0, parent=None):
181: def _build(self):
363: def refresh(self, force: bool = False):
445: def _logs_filtered(self) -> list[dict]:
465: def _paint_logs(self):
478: def _ctl(self, what: str):
486: def _sel_task(self) -> dict | None:
495: def _require_file(self, want_video: bool = False):
518: def _open_file(self):
535: def _open_folder(self):
547: def _copy_path(self):
555: def _copy_url(self):
567: def _replace_file(self):
607: def _revert_file(self):
629: def _ctx_menu(self, pos):
661: def _show_fail(self):
689: def _retry_failed(self):
696: def _copy_log(self):
702: def _export_log(self):
722: def closeEvent(self, e):  # noqa: N802
```

### A-桌面主程序\app\ui\editor.py  （79 处）

```
41: def v3mod(name: str):
84: def bgr_to_qimage(bgr: np.ndarray) -> QImage:
91: def qimage_to_bgr(img: QImage) -> np.ndarray:
103: class Canvas(QWidget):
111: def __init__(self, title: str = "", parent=None):
145: def set_bgr(self, bgr: np.ndarray):
150: def set_mask_ref(self, mask: np.ndarray):
154: def set_boxes(self, boxes: list[dict]):
158: def dirty_overlay(self):
162: def _fit(self):
174: def set_view(self, zoom: float, ox: float, oy: float):
178: def zoom_100(self):
189: def zoom_by(self, factor: float, anchor: QPoint | None = None):
202: def to_image(self, p: QPoint) -> tuple[int, int] | None:
211: def to_image_clamped(self, p: QPoint) -> tuple[int, int] | None:
225: def _img_rect(self) -> QRectF:
232: def _overlay_qimage(self) -> QImage | None:
262: def paintEvent(self, e):  # noqa: N802
311: def _paint_circle(self, ix: int, iy: int, value: int):
317: def _stamp_to(self, ix: int, iy: int):
342: def mousePressEvent(self, e):  # noqa: N802
389: def mouseMoveEvent(self, e):  # noqa: N802
427: def mouseReleaseEvent(self, e):  # noqa: N802
473: def mouseDoubleClickEvent(self, e):  # noqa: N802
485: def _after_edit(self):
489: def wheelEvent(self, e):  # noqa: N802
493: def resizeEvent(self, e):  # noqa: N802
498: class _Sig(QObject):
503: class _RepairTask(QRunnable):
519: def __init__(self, sig, bgr, mask, method: str, radius: int):
523: def run(self):
552: class _WarmTask(QRunnable):
555: def __init__(self, cb):
559: def run(self):
568: class EditorPanel(QWidget):
580: def __init__(self, api, parent=None):
604: def showEvent(self, e):  # noqa: N802
608: def resizeEvent(self, e):  # noqa: N802
620: def _do_fit(self):
632: def clear(self):
657: def load(self, row: dict) -> bool:
711: def _load(self):
716: def _load_boxes(self):
725: def _show_detail_hint(self):
741: def _build(self):
922: def _mirror(self, src, z, x, y):
927: def _sync_view(self, src):
931: def _stamp_src(self, x, y):
934: def _set_tool(self, code):
943: def _apply_tool_opts(self):
950: def _toggle_mask(self, on):
954: def _toggle_boxes(self, on):
958: def _sync_method(self):
979: def _on_warm(self, r: dict):
990: def _on_mask_touch(self):
995: def _on_canvas_error(self, msg: str):
1001: def _update_sizes(self):
1013: def _snap(self) -> dict:
1023: def _push_hist(self, label: str):
1037: def _trim_hist(self):
1043: def _restore(self, s: dict):
1059: def undo(self):
1067: def redo(self):
1075: def _update_btns(self):
1087: def apply_repair(self):
1119: def _show_status(self, text: str, bad: bool = False):
1124: def bgr_empty(self) -> bool:
1127: def _on_repaired(self, r: dict):
1172: def _on_repair_fail(self, msg: str):
1178: def _clear_mask(self):
1187: def reset_orig(self):
1203: def _regions(self) -> list[dict]:
1219: def _require_result(self) -> bool:
1227: def save_draft(self):
1236: def save_version(self):
1265: def confirm_ok(self):
1300: def confirm_original(self, quiet: bool = False):
1333: class EditorWindow(QDialog):
1339: def __init__(self, img: dict, api, parent=None):
```

### A-桌面主程序\app\ui\fine_detail.py  （8 处）

```
38: class ProductDetailDialog(QDialog):
41: def __init__(self, pid, parent=None):
136: def _load(self):
202: def _pick_image(self, row: int):
232: def _open_dir(self):
245: def _release(self):
249: def _set_verdict(self, v: str):
259: def _comment(self):
```

### A-桌面主程序\app\ui\logwin.py  （23 处）

```
56: class LogWindow(QDialog):
69: def __init__(self, parent=None, title: str = "日志", session: str = "ui"):
88: def _build(self):
129: def _mini(self, text: str, fn, tip: str = "") -> QPushButton:
143: def _open_session_file(self):
152: def _write(self, level: str, text: str):
163: def _append(self, level: str, text: str, short: str | None = None):
173: def _append_ui(self, level: str, text: str, short: str | None = None):
193: def info(self, s: str):
197: def ok(self, s: str):
201: def warn(self, s: str):
205: def error(self, s: str, exc: BaseException | None = None):
213: def step(self, s: str):
218: def detail(self, s: str):
223: def block(self, title: str):
230: def _toggle_size(self):
239: def _toggle_pause(self):
243: def _clear_view(self):
248: def _save_now(self) -> str:
263: def showEvent(self, e):  # noqa: N802
275: def attach_log_button(layout_or_widget, logw: LogWindow, text: str = "📜 日志"):
291: def _toggle(on: bool):
300: def _closed(_):
```

### A-桌面主程序\app\ui\main_window.py  （17 处）

```
33: class MainWindow(QMainWindow):
34: def __init__(self):
350: def log(self, s: str):
366: def _log_ui(self, s: str):
380: def set_stats(self, cards=None, keep=None, drop=None, ms=None):
395: def _refresh_build_status(self):
416: def _show_build_detail(self):
444: def _do_restart(self):
470: def _open_settings(self):
490: def _perf_selftest(self):
558: def _first_load(self):
580: def _on_tab(self, idx: int):
588: def _tick(self):
608: def _boot_wx_bg(self):
630: def _boot_ops_bg(self):
655: def _start_probe(self):
677: def closeEvent(self, e):  # noqa: N802, D102
```

### A-桌面主程序\app\ui\settings_panel.py  （35 处）

```
26: class _FnWorker(QThread):
31: def __init__(self, fn, parent=None):
35: def run(self):                                   # noqa: D102
43: class _ProbeWorker(QThread):
48: def __init__(self, role: str = "text", parent=None):
52: def run(self):                                   # noqa: D102
60: class SettingsPanel(QWidget):
63: def __init__(self, win, parent=None):
280: def _toggle(self):
284: def refresh(self):
294: def _refresh_criteria(self):
314: def _open_criteria(self):
325: def _preview_prompt(self):
344: def _refresh_ports(self):
356: def _refresh_paths(self):
369: def _refresh_host(self):
380: def _save_host(self):
387: def _refresh_waivers(self):
404: def _refresh_ai(self):
437: def _toggle_ai(self):
444: def _probe_ai(self):
451: def _probe_done(self, r):
472: def _use_selected_model(self, *a):
485: def _remove_model(self):
500: def _add_model(self):
513: def _refresh_rules(self):
527: def _save_rules(self):
538: def _reset_rules(self):
543: def _refresh_fb(self):
562: def _apply_fb(self):
569: def _refine_fb(self):
579: def _refined(self, r):
590: def _del_fb(self):
602: def log_do(self, s: str):
606: def _restore(self, word: str):
```

### A-桌面主程序\app\ui\tab_detect.py  （62 处）

```
51: def short_status(it: dict) -> str:
69: class DetectWorker(QThread):
78: def __init__(self, code: str, only_hit: bool = True):
83: def run(self):  # noqa: D102
118: class HealWorker(QThread):
129: def __init__(self, code: str, fill: str = "lama", only_hit: bool = True,
138: def run(self):  # noqa: D102
145: class HealOneWorker(QThread):
150: def __init__(self, image_id: int, code: str = "", fill: str = "lama"):
156: def run(self):  # noqa: D102
161: class WarmWorker(QThread):
165: def run(self):  # noqa: D102
169: class FillWorker(QThread):
173: def __init__(self, code: str, pids: list | None = None, role: str = "main"):
179: def run(self):  # noqa: D102
183: class MeasureWorker(QThread):
187: def __init__(self, code: str, role: str = ""):
192: def run(self):  # noqa: D102
196: class ThumbWorker(QThread):
204: def __init__(self, jobs: list[tuple[int, str]], gen: int, parent=None):
209: def run(self):  # noqa: D102
236: class FetchWorker(QThread):
240: def __init__(self, url: str):
244: def run(self):  # noqa: D102
262: class DetectStepTab(StepTab):
265: def __init__(self, win, parent=None):
494: def refresh(self):
538: def _role_changed(self):
541: def _toggle_view(self, on: bool):
545: def _changed(self):
560: def _set_buttons(self, fill: bool, run: bool):
564: def _role(self) -> str:
567: def _load_rows(self):
604: def _on_size(self, v: int):
610: def _wall_items(self) -> list[dict]:
626: def _build_wall(self, *a):
660: def _thumb_ready(self, image_id: int, path: str):
669: def _wall_pick(self, cur, _prev):
677: def _build_table(self):
719: def _slim_text(self, p: dict) -> str:
730: def _refresh_plan(self):
743: def _refresh_align(self, can: bool = True, info: dict | None = None):
767: def _refresh_measure(self):
774: def _measure_done(self, d: dict):
789: def _fill_missing(self):
810: def _fill_one(self):
824: def _fill_done(self, r: dict):
833: def _warm(self):
844: def _warm_done(self, r: dict):
855: def _heal(self):
872: def _heal_done(self, r: dict):
907: def _heal_one(self):
922: def _heal_one_done(self, r: dict):
950: def _run(self):
967: def _plan_ready(self, p: dict):
973: def _line(self, s: str):
984: def _done(self, r: dict):
1015: def _stop(self):
1019: def _pick(self):
1032: def _show_pair(self, image_id: int):
1066: def _got_after(self, url: str, path: str):
1072: def _show(self, lab: QLabel, path: str, cap: str):
```

### A-桌面主程序\app\ui\tab_dlmon.py  （30 处）

```
60: def _mb(n) -> str:
72: class _Model(QAbstractTableModel):
73: def __init__(self, parent=None):
77: def set_rows(self, rows):
82: def rowCount(self, parent=QModelIndex()):      # noqa: N802
85: def columnCount(self, parent=QModelIndex()):   # noqa: N802
88: def headerData(self, sec, orient, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
93: def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
127: class DlMonTab(QWidget):
130: def __init__(self, win=None):
256: def refresh(self, force: bool = False):
275: def _snap(self):
286: def _apply(self, r: dict, force: bool = False):
329: def _fill_jobs(self, cur_job: dict):
353: def _on_pick_job(self, _i):
360: def _ctl(self, what: str):
395: def _sel_rows(self) -> list[dict]:
400: def _sel_ids(self) -> list[int]:
412: def _sync_sel(self):
417: def _select_all(self):
421: def _confirm_box(self, title: str, text: str, yes: str = "确定"):
433: def _confirm(self, title: str, text: str, yes: str = "确定") -> bool:
439: def _delete_selected(self):
481: def _clear_finished(self):
535: def _require_file(self, p: str, want_video: bool = False) -> str:
548: def _open_sel(self):
559: def _open_sel_folder(self):
569: def _copy_path(self, want_url: bool = False):
582: def _menu(self, pos):
604: def summary(self) -> str:
```

### A-桌面主程序\app\ui\tab_export.py  （49 处）

```
19: class UpStatusWorker(QThread):
22: def __init__(self, code: str):
26: def run(self):  # noqa: D102
30: class UpWorker(QThread):
34: def __init__(self, code: str):
38: def run(self):  # noqa: D102
42: class WxStatWorker(QThread):
45: def __init__(self, code: str):
49: def run(self):  # noqa: D102
53: class WxUpWorker(QThread):
59: def __init__(self, code: str, limit: int = 0):
65: def stop(self):
68: def run(self):  # noqa: D102
74: class UrlWorker(QThread):
77: def __init__(self, code: str):
81: def run(self):  # noqa: D102
85: class ExpWorker(QThread):
97: def __init__(self, code: str, pids: list):
102: def run(self):  # noqa: D102
139: class DyExcelWorker(QThread):
149: def __init__(self, min_sku: int, fill_images: bool = True):
154: def run(self):  # noqa: D102
164: class ExportStepTab(StepTab):
167: def __init__(self, win, parent=None):
306: def refresh(self):
344: def _changed(self):
360: def _refresh_status(self):
368: def _got_status(self, r: dict):
395: def _refresh_wx(self):
404: def _got_wx(self, r: dict):
429: def _wx_upload(self):
450: def _todo_wx(self) -> int:
456: def _wx_stop(self):
463: def _wx_prog(self, n: int, total: int, ok: int, fail: int):
467: def _wx_done(self, r: dict):
486: def _urls(self):
496: def _urls_done(self, r: dict):
513: def _upload(self):
536: def _prog(self, n: int, total: int, ok: int, fail: int):
540: def _up_done(self, r: dict):
553: def _export(self):
564: def _exp_done(self, r: dict):
586: def _dy_excel(self):
597: def _dy_excel_prog(self, d: int, t: int, name: str):
603: def _dy_excel_done(self, r: dict):
643: def _open_dir(self):
652: def _export_dd(self):
687: def _open_cat(self):
699: def _bundle(self):
```

### A-桌面主程序\app\ui\tab_fine.py  （95 处）

```
131: def _hint(text: str) -> QLabel:
155: def _ev(ev: dict, key: str, default=""):
165: def _as_int(v, default: int = 0) -> int:
175: class FineWorker(QThread):
183: def __init__(self, pids: list, pack_code: str = "", with_images: bool = True,
193: def _on_ev(self, ev):  # noqa: D102
199: def run(self):  # noqa: D102
216: class ReleaseWorker(QThread):
220: def __init__(self, pids: list[int]):
224: def run(self):  # noqa: D102
242: class WaiveWorker(QThread):
246: def __init__(self, words: list[str], pids: list[int], reason: str):
252: def run(self):  # noqa: D102
260: class WaiverListDialog(QDialog):
263: def __init__(self, parent=None):
291: def _load(self):
314: def _restore(self, word: str):
326: class FineStepTab(StepTab):
329: def __init__(self, win, parent=None):
803: def _set_waiver_open(self, on: bool):
821: def _toggle_log(self):
824: def _set_log_open(self, on: bool):
837: def log(self, s: str):
853: def _run_log_dir() -> str:
859: def _current_jsonl(self) -> str:
876: def _tail(path: str, n: int) -> list[str]:
892: def _fmt_jsonl(s: str) -> str:
925: def _load_run_tail(self):
941: def _on_log_tab(self, i: int):
948: def refresh(self):
952: def _load_packs(self):
969: def _pack_changed(self):
1008: def _load_evidence(self):
1037: def _limit_changed(self):
1051: def _on_words_changed(self, kind, words):
1066: def _run(self, pids=None):
1101: def _log_header(self, wi: bool, wd: bool, lim: int, wa: bool = True,
1155: def _apply_event(self, ev: dict):
1178: def _acc_line(self, ev: dict):
1239: def _done(self, r: dict):
1308: def _render(self, r: dict):
1390: def _verdict_of(row: dict) -> str:
1411: def _words_of_row(self, row: dict) -> list[dict]:
1446: def _render_rows(self, rows: list[dict]):
1516: def _count_text(self, pid) -> str:
1522: def _refresh_counts(self):
1563: def _open_ai_cfg(self):
1584: def _cell_clicked(self, row: int, col: int):
1599: def _open_detail(self, pid):
1612: def _ctx_menu(self, pos):
1618: def _build_ctx_menu(self, pos) -> QMenu | None:
1625: def _menu_for_row(self, i: int) -> QMenu | None:
1682: def _set_verdict(self, pid, v: str):
1695: def _revert_verdict(self, pid):
1701: def _comment_verdict(self, pid, row: dict):
1753: def _menu_words(self, row: dict, pid) -> list[dict]:
1773: def _reasons_from_backend(self, pid) -> list[dict]:
1797: def _copy_row_reason(self, i: int):
1814: def _on_item_changed(self, _item):
1827: def _sel_notice(self, msg: str):
1832: def _selected_row_count(self) -> int:
1838: def _set_all_checks(self, state) -> int:
1860: def _select_all_rows(self):
1871: def _select_no_rows(self):
1884: def _select_invert(self):
1927: def _check_by_verdict(self, want: str) -> int:
1956: def _select_all_blocked(self):
1959: def _select_all_passed(self):
1962: def _checked_pids_all(self) -> list[int]:
1975: def _check_selected_rows(self) -> int:
2006: def _batch_verdict(self, v: str):
2043: def _select_all_pending(self):
2046: def _checked_rows(self) -> list[dict]:
2056: def _checked_pids(self, kinds=("blocked", "pending")) -> list[int]:
2078: def _update_pick(self) -> int:
2113: def _checkbox_of(self, i: int):
2116: def _set_rows_checked(self, rows: list[int], state=Qt.CheckState.Checked) -> int:
2131: def _check_first(self, n: int) -> int:
2141: def _check_visible(self) -> int:
2154: def _checked_any_pids(self) -> list[int]:
2167: def _run_picked(self):
2181: def _clear_checks(self):
2194: def _release(self):
2231: def _released(self, r: dict):
2250: def _save_as_pack(self):
2314: def _show_noise(self, noise: list[dict]):
2318: def _fill_waiver_panel(self):
2370: def _fill_struct(self):
2401: def _fetch_struct_reasons(self):
2431: def _checked_words(self) -> list[str]:
... 其余 5 处见源码
```

### A-桌面主程序\app\ui\tab_flow.py  （40 处）

```
37: class Task(QThread):
43: def __init__(self, fn, label: str = "", parent=None):
48: def run(self):  # noqa: D102
58: class FlowTab(StepTab):
61: def __init__(self, win, parent=None):
255: def _log(self, s: str):
265: def _busy(self, on: bool):
273: def _run(self, label: str, fn, after=None):
286: def _err_check(self, r: dict):
290: def _after_batch(self, r: dict):
308: def refresh(self):
328: def _load_stores(self):
344: def _on_store(self):
349: def _load_sources(self):
371: def _do_import(self):
385: def _after_import(self, r: dict):
393: def _do_chain(self):
397: def _after_chain(self, r: dict):
411: def _do_package(self):
415: def _after_pkg(self, r: dict):
430: def _open_out(self):
439: def _on_pick(self):
464: def _fill_variants(self, items: list[dict]):
485: def _cur_pid(self) -> int | None:
488: def _do_suggest(self):
507: def _do_setcore(self):
523: def _do_gen(self):
530: def _after_gen(self, r: dict):
540: def _sel_vid(self) -> int | None:
547: def _do_select(self):
557: def _do_reject(self):
565: def _on_tv_dbl(self, item):
574: def _copy_title(self):
588: def _copy_path(self):
597: def _open_folder(self):
607: def _do_import_img(self):
624: def _do_approve(self):
635: def _do_revert(self):
643: def _do_confirm_profile(self):
652: def _do_release(self):
```

### A-桌面主程序\app\ui\tab_lab.py  （23 处）

```
34: class LabWorker(QThread):
39: def __init__(self, ids, mode, policy, dry_run, budget, parent=None):
47: def run(self):  # noqa: D102
75: class LabStepTab(QWidget):
76: def __init__(self, mw, parent=None):
86: def _build(self):
259: def refresh(self):
308: def _mode_hint(self):
313: def _selected_ids(self) -> list[int]:
323: def _first_ids(self, n: int) -> list[int]:
327: def _on_pick(self):
353: def _show(self, lab: QLabel, path: str):
368: def _run_selected(self):
375: def _run(self, ids: list[int]):
397: def _done(self, out: dict):
411: def _show_candidate(self, j: dict):
435: def _confirm(self):
449: def _revert(self):
469: def _role_now(self) -> str:
475: def _import_media(self):
502: def _use_candidate(self):
519: def _revert_media(self):
534: def _open_dir(self):
```

### A-桌面主程序\app\ui\tab_ops.py  （41 处）

```
38: def _base() -> str:
42: class OpsCenterTab(QWidget):
45: def __init__(self, win=None):
193: def _table(self, cols: list) -> QTableWidget:
203: def _get(self, path: str, timeout: float = 2.5):
209: def _rows(self, obj) -> list:
214: def _data(self, obj) -> dict:
218: def _fill(table: QTableWidget, rows: list):
225: def _log(self, s: str):
233: def refresh(self):
241: def start_engine(self, silent: bool = False):
251: def stop_engine(self):
256: def restart_engine(self):
261: def shutdown(self):
269: def stop(self):
273: def reload(self):                                        # noqa: C901
276: h = self._get("/api/ops/health")
301: o = self._data(self._get("/api/ops/overview"))
319: p = self._rows(self._get("/api/ops/products?limit=200"))
330: s = self._data(self._get("/api/ops/shops"))
341: t = self._rows(self._get("/api/ops/tasks?limit=100"))
351: ev = self._rows(self._get("/api/ops/task/events?limit=100"))
361: runs = self._rows(self._get("/api/ops/task/runs?limit=100"))
383: def sync_platform(self):
389: self.base + "/api/ops/platform/collect",
401: def _poll_sync(self):
404: st = self._get("/api/ops/platform/status")
419: def load_platform(self):
422: d = self._data(self._get("/api/ops/platform/summary"))
441: p = self._rows(self._get("/api/ops/platform/products?limit=300"))
454: f = self._rows(self._get("/api/ops/platform/fetches?limit=100"))
466: def biz_action(self, action: str, payload: dict | None = None):
474: self.base + "/api/ops/business/action",
492: def load_business(self):
495: st = self._data(self._get("/api/ops/business/snapshot"))
507: ops = self._rows(self._get("/api/ops/business/feedback?limit=60"))
517: p = self._rows(self._get("/api/ops/business/dy_products?limit=200"))
529: v = self._rows(self._get("/api/ops/business/content?limit=100"))
541: c = self._rows(self._get("/api/ops/business/campaigns?limit=100"))
553: a = self._rows(self._get("/api/ops/business/accounts"))
563: def counts(self) -> dict:
```

### A-桌面主程序\app\ui\tab_publish.py  （32 处）

```
36: class _Worker(QThread):
47: def __init__(self, fn, with_progress=False, **kw):
51: def run(self):  # noqa: D102
61: class PublishTab(StepTab):
64: def __init__(self, win, parent=None):
324: def _pack_key(it: dict) -> str:
337: def refresh(self):
364: def _reload_shops(self):
394: def _selected_shop_ids(self) -> list[str]:
398: def _selected_shop_names(self) -> list[str]:
401: def _add_shop_dialog(self):
425: def _changed(self):
436: def _mark_hint(self):
446: def _calc_discount(self, v: float | None = None):
474: def _load_batches(self, select_id: int | None = None):
509: def _on_batch_tab(self, i: int):
534: def _fill_batch(self, items: list[dict]):
550: def _load_state(self):
576: def _reconcile(self):
584: def _on_reconcile(self, r: dict):
603: def _retry_failed(self):
622: def _params(self) -> dict:
633: def _rule_txt(self) -> str:
641: def _busy(self, on: bool, txt: str = ""):
647: def _preview(self):
659: def _on_preview(self, r: dict):
688: def _publish(self, *a, pids_override=None):
749: def _on_progress(self, d: dict):
778: def _on_publish(self, r: dict):
827: def _fill(self, items: list[dict], mode: str = "preview"):
855: def _fill_publish(self, items: list[dict], errors: list[dict]):
878: def _open_shop(self):
```

### A-桌面主程序\app\ui\tab_wall_main.py  （23 处）

```
35: def _prep_path() -> None:
41: class WallMainApi:
47: def __init__(self, db_path: str = DB_PATH, root: str = "", log=None):
55: def _rd(self) -> sqlite3.Connection:
61: def _wr(self) -> sqlite3.Connection:
67: def _mods(self):
72: def backup_once(self) -> str:
96: def ocr_boxes(self, image_id) -> list:
110: def state(self, image_id) -> dict:
124: def versions(self, image_id) -> list:
137: def save_draft(self, img: dict, mask, regions, method: str, radius: int,
151: def save_version(self, img: dict, result_bgr, mask, regions, method: str,
224: def confirm(self, img: dict, version_image_id: int) -> dict:
235: def confirm_original(self, pid: int, image_id: int, note: str = "") -> dict:
248: class MainWallTab(QWidget):
251: def __init__(self, win=None):
308: def _apply_wb_width(self, collapsed: bool | None = None, w: int | None = None):
319: def _on_wb_collapsed(self, on: bool):
322: def _on_want_wide(self, w: int):
331: def _on_row_changed(self, row: dict, state: str):
353: def _logV(self, s: str):
377: def refresh(self):
390: def summary(self) -> str:
```

### A-桌面主程序\app\ui\tab_wall.py  （4 处）

```
28: class WallStepTab(QWidget):
31: def __init__(self, win=None):
57: def refresh(self):
79: def summary(self) -> str:
```

### A-桌面主程序\app\ui\tab_wx.py  （22 处）

```
36: class WxAdminTab(QWidget):
37: def __init__(self, win=None):
109: def refresh(self):
118: def reload_view(self):
136: def _show_ok(self, base: str):
141: def restart(self):
149: def open_product(self, pid: str):
161: def shutdown(self):
208: def stop(self):
218: def _log(self, s: str):
225: def _watch_view(self):
237: def _on_render_dead(self, status=None, code=0):
241: def _on_loaded(self, ok):
246: def _selfcheck(self):
257: def _after_check(self, n):
274: def _purge_profile(self):
286: def _mk(self, text):
296: def _boot(self):
307: def _show_err(self):
317: def _load_map(self):
338: def _open_selected(self):
346: def summary(self) -> str:
```

### A-桌面主程序\app\ui\tabs.py  （105 处）

```
30: def _why(d: dict) -> str:
47: class IntakeWorker(QThread):
52: def __init__(self, kw: str, scroll: int, pages: int, auto_learn: bool,
59: def run(self):  # noqa: D102
69: class ExportListWorker(QThread):
74: def __init__(self, result: dict):
78: def run(self):  # noqa: D102
85: class PackImportWorker(QThread):
88: def __init__(self, files: list[str]):
92: def run(self):  # noqa: D102
104: class PackConvertWorker(QThread):
108: def __init__(self, files: list[str]):
112: def run(self):  # noqa: D102
146: class PackDeleteWorker(QThread):
149: def __init__(self, code: str):
153: def run(self):  # noqa: D102
157: class PackDeleteManyWorker(QThread):
167: def __init__(self, codes: list[str], purge: bool = False):
172: def run(self):  # noqa: D102
176: class PackPackWorker(QThread):
180: def __init__(self, codes: list[str], name: str = "", dry_run: bool = True):
186: def run(self):  # noqa: D102
190: class ExportWorker(QThread):
193: def __init__(self, pack: str):
197: def run(self):  # noqa: D102
202: class LocalDetectWorker(QThread):
210: def __init__(self, pack_code: str, limit: int = 0, no_ai: bool = True):
214: def run(self):  # noqa: D102
222: class StackWorker(QThread):
227: def __init__(self, only_csv: str = ""):
231: def run(self):  # noqa: D102
241: class StepTab(QWidget):
244: def __init__(self, win, parent=None):
248: def log(self, s: str):
251: def refresh(self):
258: class IntakeManyWorker(QThread):
264: def __init__(self, kws_text: str, scroll: int, pages: int, auto_learn: bool,
271: def run(self):  # noqa: D102
284: class ExportListWorker(QThread):
289: def __init__(self, result: dict, sid: str = ""):
293: def run(self):  # noqa: D102
303: class CollectTab(StepTab):
306: def __init__(self, win, parent=None):
487: def _add_pane(self, keyword: str, keep_rows: list, drop_rows: list,
564: def _cur_pane(self) -> dict:
569: def _on_words_changed(self, kind, words):
577: def _sync_cur(self):
583: def _close_tab(self, idx):
606: def load_history(self):
619: def _load_hist(self):
634: def _del_hist(self):
660: def _url_for(d: dict) -> str:
688: def _open_at(self, table, rows, r, c, open_col):
695: def _open_current(self):
727: def _pick(self, table, rows):
748: def start(self):
779: def stop(self):
792: def _reset_stop_btn(self):
796: def import_url_pack(self):
875: def _export_urlpack(self, result: dict, tag: str) -> None:
899: def _write_ids(self, results: list, tag: str = "") -> None:
910: def export_all_ids(self):
935: def export_list(self):
947: def _exported(self, r: dict):
966: def _export_fail(self, msg: str):
972: def on_done(self, r: dict):
1045: class PackTab(StepTab):
1056: def __init__(self, win, parent=None):
1149: def _open_inbox(self):
1158: def _pick_files(self):
1244: def _convert_inbox(self, item):
1249: def _start_convert(self, files: list[str]):
1259: def _converted(self, text: str):
1267: def _selected_codes(self) -> list[str]:
1281: def _on_pick(self):
1309: def _delete(self):
1364: def _deleted(self, d: dict):
1393: def _rename_pack(self):
1429: def _pack(self):
1444: def _dl_pack(self):
1529: def _goto_dlmon(self, job_id: int = 0):
1551: def _pack_planned(self, d: dict):
1577: def _packed(self, d: dict):
1595: def refresh(self):
1635: class DetectTab(StepTab):
1638: def __init__(self, win, parent=None):
1719: def refresh_packs(self):
1739: def _pack_changed(self):
1753: def _start(self):
1790: def _start_local(self):
... 其余 15 处见源码
```

### A-桌面主程序\app\ui\wall_main.py  （121 处）

```
56: def _beat(_s: str = "") -> None:                # noqa: D103
78: class _Sig(QObject):
83: class _Thumb(QRunnable):
90: def __init__(self, sig, cache_dir: str, pid: int, image_id: int, path: str, size: int):
95: def _name(self) -> str:
103: def run(self):
136: def _aspect(row: dict) -> float:
148: class WallCanvas(QWidget):
156: def __init__(self, wall: "VirtualWall"):
173: def _cell_at(self, pos) -> int:
179: def _fire_hover(self):
188: def mouseMoveEvent(self, e):  # noqa: N802
214: def leaveEvent(self, e):  # noqa: N802
224: def mousePressEvent(self, e):  # noqa: N802
247: def mouseReleaseEvent(self, e):  # noqa: N802
272: def mouseDoubleClickEvent(self, e):  # noqa: N802
280: def hide_hover(self):
289: def paintEvent(self, e):  # noqa: N802
314: def _draw_marks(self, p: QPainter, iid: int):
346: def _draw_cell(self, p: QPainter, iid: int, r: QRect):
396: def _draw_hover(self, p: QPainter, iid: int, r: QRect):
429: class VirtualWall(QScrollArea):
438: def __init__(self, thumb_dir: str, parent=None):
478: def set_mark_mode(self, on: bool):
483: def pic_rect(self, iid: int) -> QRect:
501: def set_mark(self, iid: int, box: tuple):
506: def clear_marks(self):
513: def drop_mark(self, iid: int):
519: def set_rows(self, rows: list[dict]):
528: def set_thumb_size(self, px: int):
538: def _ar_of(self, iid: int, row: dict) -> float:
545: def _anchor(self) -> tuple[int, int]:
556: def _relayout(self, keep_scroll: bool = True):
589: def _on_meta(self, iid: int, aspect: float):
599: def _apply_meta(self):
606: def _update_scroll(self, keep: bool):
616: def view_rect(self) -> QRect:
622: def visible_ids(self, extra: int = 0) -> list[int]:
630: def _on_scrolled(self, _v):
635: def _ensure_thumbs(self):
650: def _on_thumb(self, iid: int, img: QImage):
669: def pix(self, iid: int):
676: def click(self, iid: int, mods: int):
693: def select_all(self, on: bool):
698: def ensure(self, iid: int):
704: def resizeEvent(self, e):  # noqa: N802
710: class _MarkSig(QObject):
716: def _lama_mod():
741: def _lower_thread_priority():
758: class _MarkWorker(QRunnable):
773: def __init__(self, items, api, sig: _MarkSig, log=None):
781: def _L(self, s: str):
787: def run(self):  # noqa: D401
916: class _DetailMarkCanvas(QWidget):
932: def __init__(self, parent=None):
946: def set_image(self, iid: int, pm: QPixmap | None, box: tuple | None = None):
952: def set_box(self, box: tuple | None):
957: def set_mark_mode(self, on: bool):
963: def pic_rect(self) -> QRect:
975: def _to_norm(self, p) -> tuple:
984: def mousePressEvent(self, e):  # noqa: N802
995: def mouseMoveEvent(self, e):  # noqa: N802
1003: def mouseReleaseEvent(self, e):  # noqa: N802
1021: def paintEvent(self, e):  # noqa: N802
1054: class DetailDialog(QDialog):
1065: def __init__(self, lib, pid: int, api=None, parent=None):
1080: def set_close_hook(self, fn):
1085: def _do_close(self):
1094: def _build(self):
1167: def _conn(self):
1170: def _load(self):
1223: def _row_of(self, iid: int) -> dict:
1226: def _on_pick(self, cur, _prev=None):
1251: def _on_box_changed(self):
1262: def _refresh_marks(self):
1275: def _clear_marks(self):
1281: def _on_mark_mode(self, on: bool):
1292: def _warm_model(self):
1343: def _on_warm_done(self, r: dict):
1356: def _apply_marks(self):
1400: def _mark_log(self, s: str):
1417: def _on_mark_one(self, iid: int, ok: bool, msg: str):
1435: def _on_marks_done(self, ok: int, fail: int):
1452: def _reload_thumbs(self):
1463: def _open_one(self, item: QListWidgetItem):
1482: class MainWallPage(QWidget):
1485: def __init__(self, db_path: str, thumb_dir: str, api, parent=None):
1498: def by_iid(self) -> dict:
1502: def sel(self) -> set:
1506: def sel(self, v):
... 其余 31 处见源码
```

### A-桌面主程序\app\ui\wall.py  （103 处）

```
55: class imgcap:                       # type: ignore[no-redef]
88: class PurgeWorker(QThread):
100: def __init__(self, pids: list, delete_files: bool = True, cancel_tag: str = ""):
106: def run(self):  # noqa: D102
122: class CleanupWorker(QThread):
129: def __init__(self, cancel_tag: str = ""):
133: def run(self):  # noqa: D102
209: class _Sig(QObject):
213: def _fast_sig(path: str) -> str:
236: class _Task(QRunnable):
239: def __init__(self, sig, cache_dir, pid, path, size, image_id=0, gen=0):
245: def _cache_name(self) -> str:
249: def _emit(self, img):
256: def run(self):                          # noqa: D102
284: def _pix_cached(path, w, h):
309: def placeholder(size=200, text="无本地图") -> QPixmap:
323: class Card(QFrame):
327: def __init__(self, row: dict, size: int, has_local: bool = True):
390: def _elide(s, n):
394: def set_selected(self, on: bool):
398: def set_pixmap(self, pm: QPixmap):
402: def mousePressEvent(self, e):           # noqa: N802
406: def mouseDoubleClickEvent(self, e):     # noqa: N802
412: class TableModel(QAbstractTableModel):
417: def __init__(self, rows=None):
421: def set_rows(self, rows):
426: def rowCount(self, parent=QModelIndex()):        # noqa: N802
429: def columnCount(self, parent=QModelIndex()):     # noqa: N802
432: def headerData(self, sec, orient, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
437: def data(self, idx, role=Qt.ItemDataRole.DisplayRole):          # noqa: N802
474: class DetailCard(QWidget):
477: def __init__(self, parent=None, wide: bool = False):
492: def _clear(self, msg="未选中商品"):
502: def _sect(self, text, color=None):
510: def _kv(self, k, v, color=None):
526: def _pix(self, path, w, h=None):
535: def _short(path: str) -> str:
542: def _video_row(self, i: dict):
580: def _play_media(self, path: str):
588: def _reveal(self, path: str):
597: def _open_img_dir(self):
608: def _copy_paths(self, group, role_cn: str = ""):
626: def show_product(self, d: dict):
901: class _RevWorker(QThread):
913: def __init__(self, seq: int, pack: str, parent=None):
918: def run(self):                             # noqa: D102
930: class _DlWorker(QThread):
940: def __init__(self, pids: list, what: str = "下载", ocr: bool = True, parent=None):
946: def run(self):  # noqa: D102
956: def _mb(n) -> str:
969: class WallPage(QWidget):
972: def __init__(self, db_path: str = "", thumb_dir: str = "", parent=None):
1004: def _build(self):
1163: def reload(self, reset=True):
1191: def _cur_pack(self) -> str:
1201: def _refresh_review(self):
1219: def _start_rev(self, seq: int, pack: str):
1226: def _on_rev(self, seq: int, r: dict):
1245: def _apply_rev(self, r: dict):
1264: def closeEvent(self, e):                      # noqa: N802
1282: def _goto_review(self):
1298: def _fill_tree(self):
1328: def _on_tree(self, item, col):
1338: def _tree_menu(self, pos):
1359: def _delete_pack(self, code: str, purge: bool = False, n: str = ""):
1398: def _dl_pids(self) -> list:
1404: def _download(self):
1407: def _ocr(self):
1410: def _open_center(self, focus: bool = True):
1470: def _probe_media(self, pids: list) -> dict | None:
1499: def _start_dl(self, what: str, ocr_only: bool = False):
1567: def _media_done(self, r: dict, what: str):
1585: def _batch_pids(self) -> list:
1593: def _detach(self):
1613: def _purge(self):
1650: def _stop_delete(self):
1667: def _purged(self, r: dict):
1700: def _busy(self, what: str, on: bool):
1708: def _is_busy(self) -> bool:
1711: def _cleanup(self):
1748: def _cleaned(self, out: dict):
1774: def _fill_wall(self):
1777: def _reset_cards(self):
1787: def _load_more(self):
1814: def _maybe_more(self, v):
1820: def _set_view(self, i):
1844: def _on_size(self, v):
1856: def _relayout(self):
1864: def resizeEvent(self, e):              # noqa: N802
1871: def _on_thumb(self, pid, img, gen):
... 其余 13 处见源码
```

### A-桌面主程序\app\ui\widgets.py  （15 处）

```
21: class StatCard(QFrame):
24: def __init__(self, label: str, value: str = "—", bg: str = theme.PRIMARY_LIGHT,
44: def set_value(self, v, color: str | None = None):
50: class KV(QWidget):
53: def __init__(self, key: str, value: str = "", parent=None):
67: def set_value(self, text: str):
71: def make_table(headers: list[str], widths: list[int] | None = None) -> QTableWidget:
89: def clear_table(t: QTableWidget):
93: def fill_table(t: QTableWidget, rows: list[list], colors: dict[int, dict[str, str]] | None = None):
106: class ImageBox(QLabel):
109: def __init__(self, w=260, h=260, parent=None):
121: def show_text(self, text: str):
125: def show_path(self, path: str) -> bool:
143: class Hint(QLabel):
144: def __init__(self, text: str = "", parent=None):
```

### A-桌面主程序\app\ui\word_manager.py  （20 处）

```
21: class WordManager(QGroupBox):
32: def __init__(self, on_changed=None, parent=None, vertical: bool = False):
43: def _build(self):
151: def _split(text: str) -> list:
159: def refresh_stats(self):
170: def _fill(self, items: list):
182: def _search(self):
194: def _selected_words(self) -> list:
198: def _add(self):
215: def _remove(self):
231: def _restore(self):
257: class SensitiveWordManager(QGroupBox):
267: def __init__(self, on_changed=None, parent=None, vertical: bool = True):
275: def _build(self):
325: def refresh_stats(self):
338: def _fill(self, items: list):
355: def _search(self):
365: def _sel_words(self) -> list:
369: def _disable(self):
388: def _enable(self):
```

### A-桌面主程序\app\ui\workbench.py  （10 处）

```
35: class Workbench(QWidget):
42: def __init__(self, win, api, parent=None):
57: def _build(self):
116: def show_page(self, idx: int):
142: def collapsed(self) -> bool:
145: def set_collapsed(self, on: bool):
165: def preferred_width(self) -> int:
169: def load_row(self, row: dict):
200: def _on_changed(self, row: dict, state: str):
208: def load_detail(self, pid: int):
```

### B-微信小店适配器\补交B店家具类目.py  （6 处）

```
24: def _load(path, default):
31: def token(force=False):
51: def call(path, body=None, retry=True, timeout=30):
71: def opened_ids():
76: def build_body(r):
84: def main():
```

### B-微信小店适配器\app.py  （74 处）

```
36: def set_shop_id(sid):
41: def current_shop_id():
45: def _cur_cache():
56: def sku_health_cached():
89: def start_sku_scan(shop_id=None):
95: def run():
139: def overview_all(force=False):
202: def _seed_overview_cache():
221: def _load_shops():
247: def _cur_shop(d=None):
257: def _save_shops(d):
261: def load_cfg():
277: def get_shop(force=False):
315: def log_task(rec):
321: def read_tasks(limit=200):
335: def yuan(fen):
355: def status_text(st):
362: def slim_product(p):
394: def _p(x):
400: def _all_ids(s, status, max_pages=60, page=50):
412: def products_page(status=None, page_size=20, cursor=''):
416: def one(pid):
446: def category_flat():
478: def do_price(s, prod, mode, value):
518: def _calc_stock(old, mode, value):
528: def do_stock(s, prod, mode, value):
580: def do_title(s, prod, mode, value):
608: def do_status(s, prod, mode, value):
648: def run_batch(ids, op, mode, value, dry_run=False):
655: def one(pid):
718: def run_patch(ids, patch, dry_run=False):
726: def one(pid):
751: def diagnose_all():
760: def add(pid, title, issue, fix):
764: def one(pid):
795: def yunku_save(ids):
799: def one(pid):
813: def yunku_list():
827: def export_csv(ids, status=None):
850: def export_records():
864: def import_preview(text, name=''):
888: def _imgcache():
895: def _imgcache_put(url, wx):
903: def to_fen(v):
921: def _to_int(v, d=None):
927: def _cat_path_by_id(cat_id):
956: def _complete_product_payload(s, payload):
1054: def _norm_headers(hs):
1084: def import_normalize(text, name=''):
1093: def col(row, *cands):
1114: def _sp(v):
1121: def _pk(lst, k):
1181: def import_fetch_images(urls):
1215: def import_create(norm, cat_id, limit=5, dry_run=True):
1320: class H(BaseHTTPRequestHandler):
1323: def log_message(self, fmt, *a):
1327: def _send(self, code, body, ctype='application/json; charset=utf-8'):
1342: def _json(self, obj, code=200):
1345: def _body(self):
1349: def _jsonbody(self):
1356: def do_GET(self):
1385: def do_POST(self):
1400: def _api_get(self, p, q):
1530: def _api_post(self, p, b):
1736: def _upload(self):
1766: def do_DELETE(self):
1804: def _skus(p):
1808: def _urls(v):
1815: def edit_apply(op, p, a):
2042: def run_edit(ids, op, args, dry_run=False):
2048: def one(pid):
2123: def _ensure_streams():
2149: def main():
2153: def _warm():
```

### B-微信小店适配器\dd.py  （10 处）

```
43: def _parse_drafts(txts, src):
63: def scan_snapshots():
102: def _dkey(r):
115: def _mtime(fp):
124: def overview():
144: def drafts():
172: def enqueue(item):
232: def queue():
244: def api_get(p, q):
272: def api_post(p, b):
```

### B-微信小店适配器\photo.py  （13 处）

```
39: def lama():
52: def roots():
66: def _root(idx):
76: def _safe(root_path, rel):
86: def list_images(idx, rel='', page=0, size=60):
115: def read_file(idx, rel, max_w=0):
136: def _load_bgr(fp):
144: def build_mask(shape, rects, pad=0):
160: def _engine_apply(bgr, mask, engine='lama'):
172: def inpaint_one(idx, rel, rects, engine='lama', pad=0, inplace=False):
199: def batch(idx, ids, rects, engine='lama', pad=0):
212: def detect(idx, rel):
267: def status():
```

### B-微信小店适配器\publish.py  （14 处）

```
41: def cfg():
48: def packs_dir():
56: def _read_text(fp, limit=None):
66: def list_packs(keyword='', limit=60):
98: def pack_rows(path, limit=200):
113: def script_status():
122: def _log(rec):
128: def log_tail(n=80):
141: def _runner(cmd, cwd):
149: def run():
176: def run_script(script, args=None, publish_mode=None):
204: def check():
217: def api_get(p, q):
237: def api_post(p, b):
```

### B-微信小店适配器\union3.py  （11 处）

```
36: def norm(t):
42: def _wx_shops():
50: def _ids(s, status):
61: def _title(s, pid):
70: def _dy_on():
81: def compute():
119: def _union(d):
144: def cached():
154: def refresh_async():
163: def run():
177: def status():
```

### B-微信小店适配器\verify_batch1.py  （3 处）

```
25: def _forbidden_post(*a, **k):
36: def rec(item, passed, detail=''):
40: def sha(path):
```

### B-微信小店适配器\wxapi.py  （30 处）

```
28: class WxError(Exception):
29: def __init__(self, errcode, errmsg, path=''):
34: def _ensure(p):
38: class WxShop(object):
39: def __init__(self, appid, secret):
48: def _http(self, url, data=None, headers=None, timeout=30):
59: def token(self, force=False):
85: def call(self, path, body=None, method='POST', query=None, retry=True, raw_bytes=False):
112: def shop_info(self):
116: def product_ids(self, status=None, page_size=30, next_key=''):
124: def product(self, pid):
128: def add_product(self, payload):
131: def update_product(self, payload):
134: def listing(self, pid):
137: def delisting(self, pid):
140: def cancel_audit(self, pid):
145: def delete_product(self, pid):
148: def stock_update(self, pid, sku_id, diff_type=1, num=0):
155: def categories(self, use_cache=True, ttl=86400):
163: def category_detail(self, cat_id):
168: def img_size(data):
202: def upload_image(self, filename, content, width=0, height=0):
240: def video_initupload(self, file_size, scene_type=162, file_type='mp4'):
250: def video_uploadpart(self, key, partnum, chunk):
270: def video_finishupload(self, key, parts):
276: def video_getplayinfo(self, key, timeout=180, interval=5, _sleep=None, method='GET'):
310: def upload_video_file(self, filepath, _sleep=None):
328: def freight_templates(self):
331: def coupons(self, page=0, size=20):
335: def orders(self, start_ts, end_ts, page_size=20, next_key=''):
```

---

# 附录 B · 交付报告全文

## D-调查报告与交付证据\8.0-P0-交付报告-20261110.md

# 8.0 AI 运营中心 · P0 交付报告（2026-11-10 17:40）

> 指令：《8.0 AI运营中心正式恢复主体开发——项目执行总指令》§八「本轮立即执行」+ §十「交付标准」。
> 本轮不是调查报告，是**真实能打开的 8.0 运营中心 P0 底座**。生产真源沿用路径纠偏：`default-workspace\上货台\`。

## 1. 当前结论

✅ **P0 八项交付标准逐条达成（全部实测）**

| # | 标准 | 实测 |
|:--:|:--|:--|
| 1 | 启动原上货台 → 点击 8.0 | offscreen 完整 `MainWindow()` 注册 **12 个页签**，末项 `8.0 · AI 运营中心`，1—6.0 一个不少 |
| 2 | 看到运营总览 | 页签状态栏 + 总览条 + 六个子页（总览/商品/店铺/任务/状态事件/运行记录/引擎详情），真数据 |
| 3 | 看到真实店铺和商品 | 3 店完整 shop_id（欢轴家居 `wx6ff10d09ff0ee823`、崇耿 `wxfc4e24a2ebcb2b0a`、抖店 `dd_lzl`）；生产 v3.db **372 件真商品 / 194 视频 / 18353 图**，只读 ATTACH |
| 4 | 查看任务队列 | `ops_task` 6 条（done 4 / failed 2），状态机真跑 |
| 5 | 查看任务执行记录 | `ops_task_event` 22 条状态转移 + `ops_run` 8 条运行记录，页签可见 |
| 6 | 启动、停止、检查引擎 | 生命周期序列：初始停 → 启动 True/端口起/health ok → 停止 → 停后端口关 → 重启 → 重启后 health ok；优雅停后无孤儿端口 |
| 7 | 确认原上货台正常 | 9191 `/`=200、`/api/shops`=200（3 店）；`bridge.py` + `main_window.py` 编译 OK；原 1—6.0 页签全在 |
| 8 | 页面截图/接口/测试/指纹 | 4 张截图 + 9 个接口快照 + 全套测试 + 文件指纹，全部落盘（见 §4） |

✅ **架构按 §三 落地**：`ops.db` 独立可写（12 张 ops_* 表）· `v3.db` 只读 ATTACH（实测 `UPDATE v3_product` 被 SQLite 拦）· 9192 引擎进程内线程启停 · 主窗口统一入口 · 完整 shop_id · `platform_calls=0`。
🔴 **一处如实报（不掩饰）**：`v3.db` 文件头 SHA256 从 `CB8BB32B…` 变为 `D11EAD99…`（mtime 17:36:58）。查证：**28 张表逐表行数与 11-08 基线完全一致、`integrity=ok`、字节数 111321088 不变**；`v3.db-wal`(0B)/`-shm` 在 offscreen 完整 MainWindow 实例化期间出现 → [事实] 业务商品数据零改动，[推断] 为 `bridge` 原有启动逻辑（`v3_dl_recover`/WAL checkpoint）触碰元数据，非 ops 引擎（全程 `mode=ro` ATTACH）。
🟡 **P0 边界**：本轮只交付「00 运营总览 + 09 账号底座 + 07 队列/日志底座」；十模块中 01–08 的业务逻辑、OpsCoordinator / wx_adapter / dy_adapter / media_adapter / browser_exec / AI 决策均属 P1+，清单已登记（施工清单 §2③），未假装完成。

## 2. 需求是否满足

满足。§七「不重复调查、不推翻成果」：九份报告/Maxun/浏览器对比/API 矩阵/视频设计/E1/E2/批次一 9/9 一件未动，只读取证。`8.0-开发优先级调整与施工口径.md` 把完整中心缩成「一个视频页签」的旧范围裁决，按本轮总指令恢复十模块方向（文件本身保留）。旧 P0 28/28 是开发副本验收，未当作新架构通过；本轮另跑真数据验收。§九 禁止项全部守住：`pddsrc` 零改动、手机零操作、`v3.db` 业务零写、`ego` 未碰、无发布/改价/改库存/批量上传、无密钥明文、未重跑 E1/E2。

## 3. 下一步行动

| # | 事 | 谁 | 完成判据 |
|:--:|:--|:--|:--|
| 1 | P0 交付已完 | 我做（已完成） | 本报告 + 证据目录 |
| 2 | 双击源码版 `上货台（源码版·改动立刻生效）.lnk` 现场点开 8.0 | **需要你做（看一眼）** | 页签可见、总览出数、启停按钮各点一次 |
| 3 | P1 双平台适配器（wx_adapter 复用 9191 18 端点 / dy_adapter 走 ego / 多店隔离 / 商品状态巡检） | 我做，等一句「P1 开工」 | 每模块独立页面+业务逻辑+验收 |
| 4 | P2 视频与内容运营（E2 生产实现接入、发布前人工审批） | 我做 | 审批闸门先于任何真实发布 |
| 5 | 需要外部 AI / 别的会话配合 | 无 | 施工清单 + 本报告 + 证据可整段复制 |

## 4. 技术细节

**改动文件（生产真源）**
```
新增  上货台\app\ops\  __init__.py(758) config.py(2827) queue.py(14071)
      store.py(9338) migrate.py(8257) worker.py(3341) engine.py(27648) schema.sql(8814)
新建  上货台\app\ui\tab_ops.py(13698)   8.0 页签六子页
改    上货台\app\ui\main_window.py  665→713 行，+48/-0（纯增量，5 处：try 挂载 / _pages 追加 /
      QTimer 3200ms 后台起引擎 / _boot_ops_bg / closeEvent 收引擎）
新建  上货台\data\ops.db(147456B)  12 张 ops_* 表 + 3 店铺 + 队列/事件/运行记录
备份  main_window.py.bak-8.0接入-20261110-172401
回滚  copy 备份覆盖 main_window.py + 删 app\ops\、app\ui\tab_ops.py、data\ops.db（ops 独立，删了不影响 1—6.0）
```

**双库实现（[事实]，实测）**
```
queue.connect(): 主连接 uri=True mode=rwc/ro → ATTACH 'file:<v3.db>?mode=ro' AS vsrc
                 → CREATE TEMP VIEW v3_product/v3_image/v3_upload_status
store.py 裸表名 SQL 一行未改；UPDATE v3_product → OperationalError（视图不可写）
migrate.py 只建 ops.db；前后对 v3.db 取 sha256 + 28 表行数，报告 v3_unchanged=True
```

**接口（9192，常驻引擎实测全 200）**
```
GET  / (运营总览 HTML) /health /overview /shops /products /videos /videos/stats
     /tasks /tasks/stats /task /task/events /task/runs /metrics /accounts
     /errors /tables /recovery
POST /task/enqueue /claim /heartbeat /complete /fail /cancel /recover
     /engine/stop /media/reconcile
health: ok=true version=8.0-P0.1.0 ops_tables=12 v3.products=372 shops=3
        writes_scope=ops_* only readonly_queries=true platform_calls=0
```

**测试真输出**
```
py_compile 9 文件 OK（含 bridge.py 回归）
migrate --apply : 12 表 / 3 店铺 / v3_unchanged=True / v3_tables=28 / 2.245s
引擎 E2E : health ok=True · probe.noop→done · probe.sleep→done · probe.fail→failed
           reaper 真回收过期租约 · 20 事件 / 7 运行记录 · request_stop→端口关闭
生命周期 : 初始停 → 启动(端口+health True) → 停止(端口关) → 重启(health True)
页签 offscreen : counts={"products":200,"shops":3,"tasks":6,"events":22,
                 "runs":8,"engine_ok":true,"err":""}
MainWindow offscreen : 12 页签，1—6.0 全在 + 8.0，切到 8.0 自动出真数据
9192 总览 headless Chrome : 渲染 DOM 命中「在线 / 372 / 194 / 3 / 12 / 0 平台调用」
                           真店 wx6ff10d09ff0ee823 命中
9191 回归 : / = 200 · /api/shops = 200（3 店）
无孤儿 : 9192 已关、无 app.ops.engine 残留 pythonw
```

**证据目录** `上货台7.0调查-20261109\8.0-P0-证据\`
```
8.0-P0-运营中心页签-offscreen.png       14162B
8.0-P0-主窗口含8.0页签-offscreen.png    15863B
8.0-P0-运营总览-9192.png               82910B
8.0-P0-运营总览-9192.html              8116B
接口结果快照.json                      8377B  （9 个接口全文）
文件指纹.json                          1626B  （11 个文件 SHA256 前 16）
页签验收.json / 页签与主窗口验收.json
```
其它：`8.0-P0-施工清单-20261110.md` · `上货台\data\_migrate_apply.json`（建库报告）· `上货台\data\ops_engine.log`。

**发现的其他问题（§0.10 只登记，未动手）**
- `app\ui\tab_detect.py:600` `self._refresh_align(can=can, info=a)` 中 `a` 未定义（上一轮子任务已登记）；影响 = `_load_rows()` 末尾 NameError 吞掉后续 `_refresh_plan/_align/_measure`；是否已解决 = 否；是否需主人处理 = 否（P1 顺手修）。
- `v3.db` 文件头哈希变化见 §1 红字，业务数据零改动，未回滚（回滚会动业务库，风险更大）。

**红线复核**：`pddsrc\skutest\auto_browser.py` 35637B / 12:58:37 未变；手机零 adb 操作；零平台 API 调用；零上传/发布；未输出任何密钥；未 git commit。


## D-调查报告与交付证据\8.0-模块1-双平台真实商品中心-交付报告-20261110.md

# 8.0 AI 运营中心 · 模块1「双平台真实商品中心」交付（2026-11-10 19:44）

## 一、结论

第一阶段按指令落地：**真正读取抖店与两个微信小店的商品数据，建立平台真实商品列表与状态页面**。
不是调查、不是测试脚本、不是又一张展示卡 —— 8.0 页签里新增可操作真页（刷新采集按钮 + 三张真实子表），数据经 9192 引擎真读 9191 官方接口 / 抖店快照，落独立 `ops.db`。

保留 P0 全部成果：主窗口 8.0 入口、9192、ops.db、12 张原表、任务队列/日志、v3.db 只读 ATTACH、E1/E2、原上货台 1—6.0，一件未推翻。

## 二、每平台实际读取数量（真数据，非本地 v3.db 冒充）

| 店铺 | 完整 shop_id | 平台 | 平台实际商品总数 | 上架/在售 | 未上架 | 回收站 | 自主下架 | 逐件明细行 | 来源 | 读取 |
|:--|:--|:--|--:|--:|--:|--:|--:|--:|:--|:--|
| 欢轴家居 | `wx6ff10d09ff0ee823` | 微信 | **700** | 249 | 356 | 95 | 0 | 60 | `/api/overview` 权威 + `/api/products` 分页 | 成功 |
| 崇耿（欢轴崇耿） | `wxfc4e24a2ebcb2b0a` | 微信 | **143** | 136 | 5 | 1 | 1 | 27 | 同上 | 成功 |
| 抖店·LZL乐群云家具 | `dd_lzl` | 抖店 | **未取得**（如实留空） | 草稿3/待提交3/上架包21/包内行1412/审核驳回4（快照口径） | — | — | — | 7 | ego 页面快照 + 真实违规 CSV | 成功 |

- 微信两店总数为**官方 API 权威数**，逐状态为 `by_status` 真值；逐件明细按状态分页抓取，每状态默认 1 页×20 件（点「抓取更多明细」= full 模式最多 10 页/状态）。
- **抖店无免签逐商品接口**：页面明确标「平台实际在售总数未取得」，**不拿货源包 1412 行、不拿本地 v3.db 372 件冒充**。真实拿到的 7 行 = 草稿箱快照 3 行（含真实商品ID/标题/状态/日期/来源文件）+ 审核驳回 CSV 4 行（含违规类型/处理建议）。
- 已定位现成固定浏览器脚本 `C:\Users\Administrator\ego\main\dy-g-list.mjs`（读 `/ffa/g/list` 全部状态 tab + 商品行），以及 `抖店研究\crawl3\_chk-list.mjs`/`engine\_read_list.mjs`。ego 空间靠 `taskSpace` 运行时手跑、非常驻 HTTP，本轮未强跑、不虚构在售逐件，如实登记为下一阶段接入点。

## 三、页面与操作（软件内可验收）

打开上货台 → 最后一页「8.0 · AI 运营中心」→ 首个子页「**01 · 双平台真实商品中心**」：

1. **① 三店概览（权威总数/状态）**：三店各一行，平台实际商品数、在售/未上架/回收站/审核失败、明细行数、读取成败、来源、更新时间、说明。
2. **② 商品明细（逐行真数据）**：94 行真实商品，逐行含店铺、平台商品ID、标题、状态、价格、库存、规格数、**本地商品ID匹配**、来源、源时间、读取状态、备注。
3. **③ 读取失败与来源记录**：每次 HTTP/CSV 读取都留痕（时间/店铺/来源/方法/成败/行数/耗时ms/错误原文）。

顶部新增「**⟳ 采集平台真实数据**」按钮：真点 → POST 9192 起后台采集 → 轮询到完成 → 三表自动回读。网页版另有 `/api/ops/platform/page`（「刷新真实数据」「抓取更多明细」「按店/状态/关键词筛选」）。

## 四、验收结果（全部真跑）

```
health  ok=true version=8.0-P0.2.0 schema=3 ops_tables=15 platform_calls=0 writes_scope=ops_* only
采集    DONE 19:42:15 error="" 
        欢轴家居 total=700 detail=60 ok=True ｜ 崇耿 total=143 detail=27 ok=True ｜ 抖店 total=None detail=7 ok=True
接口    /summary /products /stats /status_stats /fetches /status 全部 200，结果落盘 evidence 目录
GUI     offscreen 完整 MainWindow：12 页签（1—6.0 全在 + 8.0）；8.0 内 7 子页（模块1 排最前）
        真点「采集平台真实数据」→ sync_label="采集完成 2026-11-10 19:43:31"
        counts_after: platform_shops=3 platform_products=94 platform_fetches=28 err=""
ID 匹配 94 行：matched=32 / unmatched=62（依据 v3_upload_status.wx_product_id 141 / dy_tid 10，只读）
失败记录 14 次读取 read_ok=0 → 0 条；真实失败会写 error 原文，不用 0 冒充
v3.db   v3_product=372 v3_image=18353 v3_upload_status=149 integrity=ok size=111321088（与开工前一致，零业务写入）
ops.db  ops_platform_shop=3 ops_platform_product=94 ops_platform_fetch=28；ops_task=6 ops_task_event=22（P0 未动）
9191 回归 / =200 /api/shops=200 /api/dd/overview=200（原上货台未受影响）
红线   pddsrc 零改动 · 手机零操作 · v3.db 业务零写 · 零发布/改价/改库存/报名 · 零密钥明文 · 未重跑 E1/E2
```

## 五、改动文件与回滚

```
新增  上货台\app\ops\platform.py            23030B  sha16 49edd43edc8bd8ed  采集/回读/ID匹配
改    上货台\app\ops\engine.py              39391B  sha16 2ac3920107df43a9   +7 GET +1 POST +平台HTML +启动建表
改    上货台\app\ops\schema.sql             11297B  sha16 494a4685f3c0c60a   +3 表（schema v3）
改    上货台\app\ops\config.py              2827B   version/schema → P0.2.0 / 3
改    上货台\app\ui\tab_ops.py              20348B  sha16 6646170e16a3ee18   +模块1 子页 +采集按钮 +回读
脚本  上货台\_apply_模块1_tabops.py / _验_模块1_GUI.py（一次性施工与验收件）
备份  5 份 *.bak-模块1双平台真实商品-20261110-1930（engine/schema/config/tab_ops/migrate）
```

回滚：copy 5 份 .bak 覆盖同名 → 删 `app\ops\platform.py`、`_apply_*`、`_验_*` → ops.db 里 3 张 `ops_platform_*` 表可留可删（独立表，删掉不影响 P0 12 表与 1—6.0）。

## 六、证据目录

`上货台7.0调查-20261109\8.0-模块1-证据-20261110\`

```
01-双平台真实商品中心-页面.png   211945B  网页真渲染（DOM 标记 10/10 命中）
02-上货台主窗口-8.0页签.png      17541B   offscreen 完整主窗口
03-三店概览.png / 04-商品明细.png / 05-读取失败记录.png   三个子页截图
06-GUI验收.json                  3909B    页签清单、真点按钮前后 counts、真表格行样本
summary.json / products_limit_300.json / stats.json / status_stats.json
fetches_limit_100.json / status.json / .._health.json   接口原始结果
```

## 七、边界与下一步（如实）

**本阶段只做模块1（第一阶段）。** 模块2 异常诊断、模块3 视频匹配上传绑定、模块4 活动、模块5 内容视频、模块6 每日自动运营 —— 按第六节优先级逐阶段交付，不冒充已完成。

抖店在售逐件明细 / 平台实际在售总数：固定脚本已定位，需 ego 空间在线跑 `dy-g-list.mjs` 产出快照后由本采集器接入；在此之前页面明确标「未取得」，不编造。

当前 9192 为独立进程（pid 36900）跑新代码；原上货台主进程 28456 未重启，9191 未断。主人双击「源码版」快捷方式后主程序会接管 9192（`is_alive()` 幂等，不抢端口）。


## D-调查报告与交付证据\8.0-模块2-真实业务运营-交付报告-20261110.md

# 8.0 模块2 · 真实业务运营与操作反馈 · 交付报告（2026-11-10）

## 1. 结论
✅ 8.0 由「底座 + 队列」推进到**能日常用的业务工作台**：新增子页「02 · 真实业务运营与反馈」，五个可点子页，全部真数据、真按钮、真反馈。
✅ 抖店无免签商品 API → 走**已登录商品后台固定脚本**：3846 件后台真商品逐行入 ops.db（含 ID / 标题 / 状态原值 / 售价 / 吊牌价 / 库存 / 类目 / 本地 ID 匹配），并建 **30 分钟定时采集**（Python 侧已真跑 1 次）+ 浏览器刷新脚本。
✅ 主人点名的四件事全部落在同一页：短视频运营 / 限时限量购 / 活动 / 账号运营 / **操作以后看得见反馈状态**。
🔴 如实边界：浏览器刷新脚本 `dy_timed_capture.mjs` 已写好且 `node --check` 通过，但**本轮未在 ego 登录态下真跑一次**（9222 未监听、ego 空间非常驻 HTTP），当前页面显示「历史全店快照（非实时）· 源时间 2026-10-29 16:03:37」，不冒充实时。

## 2. 需求对照
| 主人原话 | 落地 |
|:--|:--|
| 抖店没 API，登录商品后台直接抓、做定时脚本、明细都能抓到、调好 | 3846 行真明细入 `ops_platform_product(source_kind=dy_ego_full)`；`dy_timed_capture.mjs` 读 `/ffa/g/list` 全状态 tab + 拦截商品接口 + DOM 兜底；`business.auto_loop` 每 1800s 重采（op=10 已真跑）；页面标源文件/源时间/是否实时/下次采集 |
| 这套运营中心到底能干嘛 | 8.0 新增 5 个业务子页，每页有真数据与真按钮，不再只有队列和数据库状态 |
| 短视频运营、限时限量购、之前要的活动都没表现在里面 | ③ 短视频运营（194 真视频→商品头视频 194 + 内容视频 194 草稿，两类严格分开）；④ 限时限量购 20 条 + 官方规则库 4 条报名资料草稿，含售价/吊牌/差额本地核算 |
| 一个账号运营在哪里，页面里都看得见、可操作 | ⑤ 账号运营：3 家完整 shop_id、浏览器引擎、登录检查真探活结果、最后检查时间；「检查账号状态」按钮真写 `ops_account` |
| 操作交由你来弄，起码能看到操作以后的反馈状态 | ① 操作反馈：每个按钮一条 `running→done/failed`，含结果原文/错误/开始/完成/耗时；页面 6 秒自刷 + 动作后立刻回读 |

## 3. 真跑数据（全部实测）
```
health   8.0-P0.3.0 / schema=4 / ops_tables=18 / integrity=ok / writes_scope=ops_* only
平台真数 抖店后台 3846（status 0=3630 / 1=216；draft 1=2633 / 0=1205 / 4=6 / 3=2）
         微信 欢轴 700（249/356/95）· 崇耿 143（136/5/1/1）· 逐行 87
真视频   v3.db role='video' 194 → 商品详情视频 194 草稿 + 内容视频 194 草稿
活动     规则库 39 条（命中活动/促销/价格类 4）+ 限时限量购 20 条草稿
账号     3 家；9191 台账 ok / 抖店 ego 快照 ok / 店铺状态「正常营业」
按钮     9 个动作 + 定时 1 次 = 10 条反馈，done=10 failed=0
         （op=2/4 是修复前的真实历史回执，保留作审计证据，不粉饰）
定时     runs=1 last=22:57:53 next=23:27:53 interval=1800s errors=0
GUI      offscreen 完整 MainWindow 12 页签（1—6.0 全在 + 8.0），
         切到 8.0 后 dy_full=200 / biz_video=100 / biz_campaign=24 /
         biz_account=3 / biz_ops=10，err=""
回归     9191 / · /api/shops · /api/dd/overview 全 200
```

## 4. 改动与备份
```
新增  上货台\app\ops\business.py                40307B  sha16 4F63AC160E3E4599
      上货台\app\ops\dy_timed_capture.mjs      7269B   sha16 ABBCDE1AC2160BB5
改    上货台\app\ops\engine.py                 41697B  sha16 B1097CEA86D23AFA
      上货台\app\ops\schema.sql                13412B  sha16 2CEA7AD771BCE9D8
      上货台\app\ops\config.py                 2893B   sha16 A33654A61A554E8C
      上货台\app\ui\tab_ops.py                30013B  sha16 30E9F009E898E90C
迁移  python -m app.ops.migrate --apply  → 新增 ops_operation / ops_campaign / ops_content_publish
备份  5 份 *.bak-8.0业务运营-20261110-2235（platform/engine/schema/config/tab_ops）
回滚  copy 5 份 .bak 覆盖同名 + 删 business.py / dy_timed_capture.mjs；
      ops_operation / ops_campaign / ops_content_publish 三表独立，可直接 drop
```
**回滚命令**
```powershell
$ws='G:\Users\Administrator\Documents\deepseek-harness\default-workspace'
$tag='bak-8.0业务运营-20261110-2235'
foreach($r in '上货台\app\ops\platform.py','上货台\app\ops\engine.py','上货台\app\ops\schema.sql','上货台\app\ops\config.py','上货台\app\ui\tab_ops.py'){
  Copy-Item (Join-Path $ws "$r.$tag") (Join-Path $ws $r) -Force }
Remove-Item (Join-Path $ws '上货台\app\ops\business.py'),
            (Join-Path $ws '上货台\app\ops\dy_timed_capture.mjs') -Force
```

## 5. 红线核验
- `v3.db`：28 表逐表行数与开工前**完全一致**、`integrity=ok`、111,321,088 B；`UPDATE` 零业务写入。文件头 SHA16 由 `2ea9c90a…` → `81e47013…`，[推断] offscreen 完整 MainWindow 实例化时原有 `bridge` 启动逻辑触碰 WAL/元数据，与 ops 引擎无关（全程 `mode=ro`）。
- 零发布、零改价、零改库存、零报名、零自动上传真实视频；报名/发布草稿全部 `need_human=1`，人工确认只改本地状态标记。
- `pddsrc` 零改动、手机零操作、ego 现有生产脚本零改动、9191 零改动、无密钥明文（`AppSecret/Cookie/PRIVATE KEY/PDDAccessToken` 全包扫描 0 命中）。
- 「平台当前可报名活动列表」「曝光/点击数据」**未取得**，页面如实标注，不拿规则库/本地库冒充。

## 6. 证据
`上货台7.0调查-20261109\8.0-模块2-证据-20261110\`：
业务运营页.png（169KB 真浏览器截图）· 业务运营页.html（DOM 130KB，10 个页面标记全命中）·
GUI验收.json · api_health/overview/snapshot/feedback/dy_products/content/campaigns/accounts 八份接口快照 ·
v3-db核验.txt · 迁移报告 `上货台7.0调查-20261109\8.0-模块2-迁移报告.json`

## 7. 发现的其他问题（§0.10 只登记、未动手）
- `tab_detect.py:600 info=a` 未定义（上轮已登记，仍只登记）。
- 定时首趟与手动采集曾并发写同一批表，已用 `_COLLECT_LOCK` 串行化；`video.prepare` 首跑因 `v3_image` 无 `local_path` 列失败，已改 `current_local_path/original_local_path` 并复跑成功；`campaign.prepare` 加去重防重复点按钮堆脏数据。
- 9192 账面两个 `python.exe` 进程：37112 为启动器父进程、33452 为实际 LISTEN 子进程，health 三次均返回同一 `instance=e-ace5bc8d`，单应答，无双活。


## D-调查报告与交付证据\AI运营中心-总体架构设计.md

# AI运营中心 - 总体架构设计

> 结论等级：**B（条件不满足）** —— 架构可落地，但「曝光/流量/商机/活动」四类数据两平台都拿不到，
> 依赖它们的「自动优化」只能做到「基于本地事实的定性建议」，不能宣称能算 ROI。
> 证据标注：`[源码确认]` / `[本机实测]` / `[官方文档]` / `[设计建议]` / `[尚未验证]`

## 结论摘要（≤300 字）

**三进程 + 一个库 + 两条连接器 + 一条队列 + 一道审批闸。**

```
① 桌面 PyQt（现有，只加 1 个 7.0 页签组）   ← 只读 + 发命令
② ops_engine（新，独立进程，9192）          ← 唯一写库者、唯一调度者
③ browser_executor（新，独立进程）           ← 只跑固定脚本，不思考
        ↕ 同一个库：taobao-picker\data\v3.db（新增 13 组 ops_* 表）
① 微信连接器 = 官方 HTTP API（wxapi.py 已通 18 个端点，两个真实店铺）
② 抖音连接器 = ego 固定脚本 + 页面快照（dd.py 已建骨架，无 API 可走）
① ops_task 持久化队列（租约+幂等+重试上限）
② ops_approval 审批闸（L1–L4 写库前拦截）
```

**AI 的位置**：不在执行链上。固定脚本跑；规则查；**只有异常才叫本地 AI 诊断**，且诊断结果只能进「候选修复」，
不能直接改生产流程。这条既是主人的要求，也是平台风控的唯一安全姿势。

---

## 一、先划清「不改什么」（比写新代码更重要）

| 不改 | 依据 |
|:--|:--|
| 1 · 采集初筛 → 5.5 出表 的既有 10 个页签 | 主人原话：「不要重写已经稳定运行的商品采集、筛选、精修、上架功能」 |
| `app\bridge.py` 既有函数签名（265KB / 351 个顶层函数 `[本机实测]`） | 加函数可以，改签名会连锁炸所有 Tab；主人明确「不要把运营业务代码全部写进 bridge.py」 |
| `v3_product` / `v3_sku` / `v3_image` 的列结构 | ops 表只加外键，不 ALTER 现有列 |
| `dist\` / `build\` / `_backup-*` / `_snapshot-*` | `[源码确认]` `01_架构.md` §九：「改到这些就是挂点选错了」 |
| 9186 / 9187 诊断层 | 独立进程，7.0 不依赖也不改它 |
| `taobao-picker\上货台\`（同名目录）与 `上货台\data\v3.db`（0 字节） | `[本机实测]` 陷阱：不是运行目录、不是真库 |

## 二、分层与进程拓扑

```
┌──────────────────────────────────────────────────────────────┐
│ ① 桌面 PyQt6  run.pyw （现有进程）                            │
│    1 采集 · 2 导入 · 2.5 商品库 · 2.5.1 下载 · 3 精筛 · 4 打码 │
│    4.1 审核 · 4.9 AI重做 · 5.0 上架 · 5.5 出表 · 6.0 微店后台  │
│    ─────────────────────────────────────────────────────────  │
│    ➕ 7.0 · AI运营中心（本期新增，5 个子页，只读+发命令）      │
│    经 HTTP 调 ops_engine（只加 1 个 bridge 代理函数）         │
└───────────────┬──────────────────────────────────────────────┘
                │ http://127.0.0.1:9192/api/ops/*
┌───────────────▼──────────────────────────────────────────────┐
│ ② ops_engine（新进程 · 唯一写库者 · 唯一调度者 · 9192）        │
│    ├─ 队列内核 ops_task（租约 120s / 幂等 / max_attempts）     │
│    ├─ 审批闸 ops_approval（L1–L4）                            │
│    ├─ 素材编排 ops_media_version / ops_media_right            │
│    ├─ 内容编排 ops_content（脚本/文案/字幕）                   │
│    ├─ 发布编排 ops_publish_job（9 状态机）                    │
│    ├─ 指标聚合 ops_metric_daily（带 source 标记）              │
│    └─ 建议生成 ops_advice（只出定性建议）                      │
└───┬───────────────┬───────────────────┬──────────────────────┘
    │               │                   │
    │ 调 API        │ 下任务            │ 只读查询
┌───▼──────────┐ ┌──▼──────────────┐ ┌─▼─────────────────────┐
│ 微信连接器    │ │ ③ browser_      │ │ ④ 本地 AI 网关 8326    │
│ wxapi.py 复用 │ │ executor（新）  │ │ (现成，401 需 key)     │
│ /channels/ec/*│ │ ego lite 固定   │ │ 仅在「异常诊断」时调用  │
│ 18 端点已通   │ │ 脚本 .mjs，不   │ │ 不做常规执行           │
│               │ │ 思考、不重试风控 │ │                        │
└───────────────┘ └──┬──────────────┘ └───────────────────────┘
                     │ 抖音后台 / 视频号助手（浏览器）
                     ▼
              ┌───────────────────────────────────┐
              │ ⑤ 唯一数据库 v3.db（schema 300→400）│
              │    28 张老表不动 + 13 组 ops_* 新增  │
              │    素材盘：taobao-picker\data\v3\   │
              │    生成物：D:\DSH_Cache\（禁写 C 盘）│
              └───────────────────────────────────┘
```

## 三、端口与进程（实测 + 规划）

| 端口 | 归属 | 状态 | 说明 |
|:--|:--|:--|:--|
| 9191 | 6.0 微店后台 | `[本机实测]` LISTEN pid 55584 | 不动 |
| 9186 / 9187 | 诊断层 / 下载执行器 | `[本机实测]` LISTEN 31892 / 40204 | 不动 |
| 8326 | 本地 AI 网关（OpenAI 兼容） | `[本机实测]` `/v1/models` 与 `/` 均 **401** | 不动；7.0 复用其 key 机制 |
| **9192** | **ops_engine** | `[本机实测]` **空闲** | 用它；不新起 9193 之类多余端口，执行器由 ops_engine 内部驱动 |
| 19387 | DSH web GUI（与 8326 同 node 进程） | `[本机实测]` | 与本项目无关，别混 |

**为什么执行器不单独开端口**：多一个监听面就多一处鉴权与状态不一致风险。
`browser_executor` 作为 ops_engine 的**子进程**启动，用 `stdin/stdout JSON 行协议` 通信；
它自己的日志与截图落 `ops_run`。这样「独立运行」（崩溃不拖死引擎、可单独重启）与「不增攻击面」两件事都成立 `[设计建议]`。

## 四、两条连接器（能力差异是架构的第一驱动力）

| | 微信小店 | 抖音/抖店 |
|:--|:--|:--|
| 通道 | **官方 HTTP API** | **浏览器固定脚本**（无免签接口） |
| 现状 | `[本机实测]` `wxapi.py` 18 个端点已通，两个真实店铺，`wx_state=success 141` | `[源码确认]` `dd.py:4-6`「抖店没有可直连的免签接口」；全仓无 `app_key/sign_method` |
| 商品 | 增删改查/上下架/改库存/审核取消 **可 API** | 走 16 列 xlsx → `ego/main/excel-route-4.mjs` → 小猪 `excelCopyItem/dyForm` |
| 订单 | `/channels/ec/order/list/get` 可读（**时间窗 7 天，超时报 40097**）`[源码确认]` app.py:2118 | 页面快照（`dy-sold-list.csv`，**可能只覆盖第一页**）`[源码确认]` union3.py:8-10 |
| 曝光/点击 | 候选接口存在，实测 `/channels/ec/product/data/get` **48001 未授权** | 罗盘是否有 API **未核实** |
| 视频发布 | **视频号无任何发布/上传接口** → 只能视频号助手网页 | `POST /video/create/` 存在，scope `video.create` 需申请+用户授权；**锚点只列小程序/游戏/poi，商品锚点未列** |
| 素材上传 | 小店素材库有分块上传接口（≠ 发布） | 无 |
| 结论 | **API 优先，浏览器兜底** | **只有浏览器**，稳定性受页面改版支配 |

`[官方文档]` 依据见《双平台API能力矩阵》§1.2 的链接清单；本节结论与它逐条一致。

## 五、执行器选型（三条腿，各管一段）

| 腿 | 用什么 | 管什么 | 为什么不用别的 |
|:--|:--|:--|:--|
| **主执行** | **ego lite 固定脚本**（`.mjs` 流程 + 微信 HTTP 调用） | 90% 的常规动作（点击/填表/选下拉/上传/截图，复用已登录配置档） | 确定性最高、零 token、**已在产线跑**（`launch_collect_browser.py:55` 主浏览器=ego） |
| **副手** | **Playwright** | 无头批量、trace 取证、回归测试 | `[本机实测]` Python 包已装；但**本期不强制引入**，只在「需要 headless 批量或回放排障」时启用 |
| **一次性探路** | **browser-use / 本地 AI（8326）** | 页面改版、脚本点不动时探一次 | 探通后**必须固化成固定脚本**，不常驻、不参与日常执行 |
| **不采用** | **Maxun** | — | 录制器不支持文件上传（全仓 `setInputFiles`/`filechooser` 零命中）、失败不置错、登录态不持久、要 5 个容器、AGPL |

> 口径以《AI运营中心-浏览器自动化对比》§4「最终组合结论」为准；本表是它在架构中的落点。

**硬规则（写进代码，不做成可配置项）**：
- 同一流程两次失败 → **停止并置 `paused`**，不自动第三次（主人「AI 不能自动绕过风控」）。
- 命中 `captcha` / `risk_control` / `login_expired` → **立即停**，等人在界面上处理。
- AI 提出的修复**只能写 `ops_flow_version(status='draft')`**，人工批准后才转 `active`；**永不直接改正在跑的版本**。

## 六、端到端数据流（六跳）

```
① 选品/采集（现有 1–2.5）→ v3_product / v3_sku / v3_image（含 role='video' 194 条）
② 精筛/打码/审核（现有 3–4.9）→ v3_product.status = fine_screened（34 件）
③ 上架（现有 5.0）
      微信：wxshop.add_product / update_product → v3_upload_status.wx_state=success（141）
      抖音：16 列 xlsx → ego excel-route-4 → v3_upload_status.dy_state=success（8）
④ 【7.0】素材层：v3_image(role='video') → ops_media_version(vkind='orig')
      → 生成 ai_a / ai_b / ai_imgtext / dy_version / wx_version / cover / subtitle / audio
      → ops_media_right 记授权（authorized=0 禁入发布）
⑤ 【7.0】发布：ops_publish_job 九状态机
      素材准备 → AI检查 → 视频生成 → 视频审核 → 账号选择 → 视频上传 → 商品关联 → 发布确认 → 结果核对
      微信/抖音各走自己的通道；每步失败存「六件套」进 ops_run
⑥ 【7.0】回流：ops_metric_daily（带 source）→ ops_advice（定性建议）→ 桌面 7.0 总览
```

`[本机实测]` ③ 的真实分布：`v3_upload_status` 里 `wx_state` success 141 / none 8；`dy_state` success 8 / fail 2 / none 139。
**抖音侧 139 件「none」是 7.0 的第一批可见价值** —— 它不是 bug，是「还没上过抖音」。

## 七、7.0 在桌面上的落点（5 个子页，不是 15 个平铺）

主人列了 15 个页面，直接平铺会让主窗口页签变成 27 个，不可用。按「谁在看」归并成 5 个，内部二级切换：

| 7.0 子页 | 内含主人列的哪些页 | 读写 |
|:--|:--|:--|
| **A 运营总览** | 运营总览、每日数据报表 | 只读 |
| **B 商品与素材** | 商品流量诊断、视频素材中心、AI视频生产 | 读 + 发起生成 |
| **C 发布中心** | 视频发布中心、商品关联管理 | 读 + 发起（需审批） |
| **D 平台运营** | 抖音运营、微信运营、达人管理、活动管理 | 读 + 发起 |
| **E 任务与治理** | 运营任务队列、人工审批中心、错误和恢复中心、自动化流程管理 | 读 + 操作队列 |

**硬约束**：视频渲染、浏览器执行、AI 任务**全部在 ops_engine 侧异步跑**；桌面只显示进度。
`[源码确认]` 现有 `_Worker(QThread)` 模式（`tab_publish.py:653/742`）可照用，但**不许在 UI 线程里等渲染**。

## 八、权限分级落点（L1–L4 在架构里是一道闸，不是一句口号）

```
用户/引擎发起动作
      ↓
ops_engine.require_approval(ref_type, ref_id, level)
      ↓  level<=2 → 直接执行（L2 执行后失败即 paused）
      ↓  level>=3 → 查 ops_approval(state='approved')；查不到 → 写入 pending + 返回「已排队待批」
      ↓  level==4 → 引擎里根本没有执行分支（只有「打开后台给人点」）
```

主人点名的 8 类不得未批自动执行：改价 / 活动价确认 / 佣金 / 广告预算 / 正式视频发布(初期) / 批量删除 / 授权变更 / 隐私相关。
→ 对应 `ref_type`：`price_change` / `activity_price` / `commission` / `ad_budget` / `publish_job` / `batch_delete` / `auth_change` / `privacy`。

## 九、可观测性（照本项目已立的规矩，不另造）

| 项 | 做法 |
|:--|:--|
| 双轨日志 | `logs/ops/human-*.log` + `events-*.jsonl`（沿用 `日志中心\logkit.py`） |
| 浮动面板 | `http://127.0.0.1:9195/?embed=1&project=ops` 嵌进 7.0 页 |
| 无窗启动 | ops_engine / executor 由计划任务经 `wscript` 壳拉起，`CREATE_NO_WINDOW` |
| 门牌 | 9192 挂 `项目门牌`（`/llms.txt`）|
| 健康自证 | `GET /api/ops/health` 返回 pid / 启动时间 / 代码 mtime / 队列深度 / 最后成功任务时间 —— **判「起来了没」只认它**，不认 `schtasks` 返回码 |
| 版本可辨 | 页签上显示「引擎版本 + 进程启动时间」，防止旧进程假装新代码 |

## 十、故障域与降级（每一条都必须有降级路径）

| 故障 | 影响 | 降级 |
|:--|:--|:--|
| ops_engine 挂 | 7.0 页签全灰 | 1–6.0 完全不受影响（独立进程）；桌面显示红条 + 一键重启 |
| executor 挂 | 抖音侧全停 | 队列任务停 `leased`，租约 120s 过期后自动回 `pending`，重启即续 |
| 微信 token 失效 | 微信 API 全 401 | 沿用 `wxapi.token(force=True)` 刷新；两次失败 → `ops_account.status='expired'` + 界面提示重授权 |
| 抖音登录态失效 | 抖音侧全停 | `ops_account.cookie_state_path` 失效 → 只提示「请扫码」，**不做自动重登** |
| 8326 网关不可用 | 无法 AI 诊断 | 队列照跑，诊断任务进 `dead` 并标「待网关恢复」；**不让整个运营停摆** |
| 库被锁 | 全部写失败 | 单写入口 + 短事务；连续 3 次 SQLITE_BUSY → 暂停队列并报错，不硬重试 |
| 磁盘满 | 渲染/下载失败 | 渲染前查余量（占位检查 ≥20GB），不足即拒并提示 |

## 十一、坚决不做（架构层面的四个「不做」）

1. **不做「全自动无人值守发布」** —— 抖音正式发布初期必须人批；视频号只能浏览器，风控随时会拦。
2. **不做「两平台统一运营 API 抽象层」** —— 两边能力差太远（一边 18 个 API，一边 0 个），
   强行抽象会得到一个「两边都别扭」的中间层。**连接器各自独立，共享的是队列与素材，不是接口形状。**
3. **不做「AI 直接改生产脚本并立即执行」** —— 只能出 `draft` 版本。
4. **不做「用曝光/成交数据驱动自动调价」** —— 数据两平台都不全，基于残缺数据自动调价是财务风险。

## 十二、总体验收判据（架构层，一把尺子）

1. 1–6.0 十条既有链路**逐条走一遍不回归**（回归脚本见 `_验_*` 家族，共 40+ 个）。
2. `bridge.py` 只增加 **1** 个代理函数、0 个签名变更（diff 可数）。
3. 7.0 五个子页在 ego lite 里被像客户一样点过一遍（不是只看接口 200）。
4. 关掉 ops_engine，桌面 1–6.0 全部正常 —— 证明解耦。
5. 杀掉 executor 再启动，任务能从租约过期处续上 —— 证明队列真持久化。
6. 一次真实视频生成的中间产物全部落在 `D:\DSH_Cache\`，C 盘零写入。

## 十三、尚未验证（如实标）

- 9193 及以后端口未逐一确认空闲（本设计不用它们，改由子进程驱动，故不构成阻塞）。`[尚未验证]`
- 视频渲染/配音的真实耗时与磁盘峰值，未实测。`[尚未验证]`
- 视频号助手网页的自动化可行性（有无强风控/滑块/设备指纹校验）未实测。`[尚未验证]`
- 抖店后台当前页面结构是否仍与 `抖店研究\抖店后台结构地图.md`（2026-10-29）一致，未验证。`[尚未验证]`

---

# 第二轮修订（2026-11-09 · 8.0）

> 本节由第二轮整合审查追加，**不推翻上文**，只覆盖与上文冲突的部分。完整说明见
> [`AI运营中心-第二轮整合补充审查.md`](AI运营中心-第二轮整合补充审查.md)。命名：7.0 已被 C 层抖店模块占用 → 新中心定名 **8.0**。

## 1 架构改为四层 + 双库

```
A 桌面上货台（PyQt6，1…6.0 不动 + 8.0 只读页签）
B taobao-picker（商品/素材主库 v3.db）
C 微信小店自建上货台 9191（6.0 微信后台 + 7.0 抖店后台）
D 8.0 运营引擎（9192，独立进程，独立 ops.db）
```

- D 层对 `v3.db` **只读**（`mode=ro`），对 `ops.db` 读写 → 权限边界可被第三方验证。
- 上文「运营服务使用 9192」成立；「ops 表放 v3.db」**作废**，改独立库（实测见下）。

## 2 为什么改独立库（实测，不是偏好）

| 项 | ops 表放 v3.db | 独立 ops.db |
|:--|--:|--:|
| ops_task 写入 | 2.084 ms/条 | 1.655 ms/条 |
| 按 product_id 读商品 | 0.011 ms（同库） | 0.009 ms（ATTACH 跨库） |
| 完整性检查 | 0.35 s 热 / **39.9 s 冷** | **0.00 s** |
| 库体积 | 111,321,088 B | 147,456 B |

跨库关联**不慢**，而自检成本差 4 个数量级 → 独立库。

## 3 调度的唯一入口：OpsCoordinator

AI 不再直接调 Python 函数，只调白名单方法（13 个：`get_product` / `get_platform_listing` /
`check_account` / `check_duplicate_build` / `prepare_video` / `generate_video` /
`prepare_publish` / `run_workflow` / `check_publish_result` / `get_daily_metrics` /
`diagnose_product` / `request_approval` / `list_products`）。
风险级 L0 读 / L1 本地写 / L2 入队 / L3 平台写（须审批）/ L4 只能人工。

## 4 页签挂载（原样保留，仅改号）

`main_window.py` 用 `try: from .tab_ops8 import OpsCenterTab` 挂载，排在 6.0 之后；
失败只写日志；引擎未起 = 页内「未连接」，**已实测 1—6.0 不受影响**。


## D-调查报告与交付证据\AI运营中心-双平台API能力矩阵.md

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


---

# 附录 C · 提建议的四条格式

每条建议必须写明：① 依据哪个文件（`路径:行`）② 属于「现有能力」还是「需新增」③ 改动落在哪一层（桌面 UI / 9192 引擎 / ops.db / 平台连接器 / 浏览器脚本）④ 怎么验收。不要给脱离目录结构的泛泛建议。

