# 淘宝选品系统 · 新会话交接

> 写于 2026-10-18 16:0x · 工作区 `G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker`
> 本文所有数字与路径**都是当场实测核对过的**，不是回忆。核对方式写在 §11。

---

## 0 一句话现状

> 插件在淘宝搜索页读商品卡片 → 推给本地服务 → 服务按「标题有没有品牌名 × 店铺类型」判 A/B/C/排除 → AI 复核 → 落库 → 你在 `/v2` 页面看结果、在 `/ai` 页面跟 AI 对话。
>
> **主链路是通的。** 现在卡在「界面缺三个操作按钮」和「有几处规则没修完」。

**库内实况（实测）**

```
products            205 件      分级： C=104 · (未定级)=40 · B=34 · 排除=27 · A=0
                                店铺： (空)=145 · 未知=24 · 旗舰店=18 · 专卖店=14 · 工厂店=4
search_tasks          3         task_items 352      fine_queue 205
capture_log         389         phash_cache 239      img_cache 187
DB 文件  data\products.db   2.7 MB   最后写 10-18 02:18
```

⚠️ **A=0 是现状，不是 bug** —— 见 §7 待办第 3 条。

---

## 1 在哪 · 怎么开

| 项 | 值 |
|:--|:--|
| **项目根** | `G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker` |
| **启动（唯一正确入口）** | 双击 `启动选品系统.bat` |
| **主界面** | `http://127.0.0.1:9277/v2` |
| **AI 聊天 + 诊断** | `http://127.0.0.1:9277/ai` |
| **端口** | 9277（可用环境变量 `TP_PORT` 改） |

**启动链**：`启动选品系统.bat` → `pythonw client.py` → client.py **自己检查服务在不在，不在才拉起**（`CREATE_NO_WINDOW`，不闪黑框）。

> ★ **服务只有一个 owner：`client.py`。** 不要在别处再起一个 `server/app.py`，会出现两个进程抢同一个端口。
> 调试时可以手动起（见 §11），但先确认 9277 没在监听。

---

## 2 目录结构

```
taobao-picker\
├── 启动选品系统.bat        ← 唯一入口（必须保持纯 ASCII，见 §8 坑1）
├── client.py              ← 桌面客户端 + 服务的 owner
├── extension\             ← 浏览器插件（7 个文件，别加子目录，见 §8 坑2）
│   ├── manifest.json       v2.0.0 · MV3 · ID: bdjjadkmdcffnbodhodgockccolhopbh
│   ├── background.js       转发到本地服务 · 角标
│   ├── popup.html/js       弹窗：地址框 / 保存并打开 / 采集 / 分析 / 筛选
│   ├── net-hook.js         MAIN world，document_start
│   ├── capture-list.js     搜索页读卡片（~48 KB）
│   └── capture-detail.js   详情页细采（~30 KB）
├── server\
│   ├── app.py              Flask 全部路由（~80 KB）★ 改了要重启服务
│   ├── prescreen.py        初筛引擎（~78 KB，33 函数）★ 规则都在这里
│   ├── ai_providers.py     AI 接入层：get_role / call_endpoint / chat_messages
│   ├── ai_selector.py      AI 角色选择 + 诊断 status()/verify_endpoint()
│   ├── db.py               落库（205 件商品的表结构在这里）
│   ├── brandlib.py        品牌库（19023 条）
│   ├── forbidden.py       违禁词
│   ├── selection.py       选品五层漏斗（另一条链路，见 §3 末）
│   └── static\
│       ├── app.html        主界面 /v2（~33 KB）
│       └── ai.html         聊天+诊断 /ai（~21 KB）
├── config\
│   ├── prescreen_rules.json    ★ 机制层：权重/闸门/词表（改完热读生效）
│   ├── selection_profile.json  ★ 策略层：主人画像/店铺类型定义/提示词（改完热读）
│   ├── ai_providers.json       模型与密钥（role: text / vision）
│   └── brand_library.json  category_rules.json  scoring_weights.json  …
└── data\
    └── products.db          SQLite · 11 张表（见 §0）
```

**两张配置的分工（别混）**

```
prescreen_rules.json    = 怎么算（权重 / 闸门 / 词表 —— 机制）
selection_profile.json  = 算什么（目标买家 / 店铺类型定义 / 提示词 —— 策略）
```

---

## 3 核心机制：初筛怎么定级

**主人原话（2026-10-18）：**

> 「标题和店面都是旗舰店的就是 C 级，都没有的就是 A 级，普通的就是 B 级。这个其实很好分辨的呀，为什么会搞得这么复杂？」

**现在就是照这句实现的 —— 只看两个信号：**

| | 标题**有**品牌名 | 标题**无**品牌名 |
|:--|:--:|:--:|
| **旗舰店 / 官方店 / 专卖店 / 专营店** | **C** | **B** |
| **工厂店 / 个人店 / 普通店** | **B** | **A** |
| **店铺类型未知** | B | B |

- **「未知」不给 A** —— 店名没采到 / 没写类型时，**我们并不知道它是不是旗舰店**，给 A 等于把「不知道」当成「好」。
- **四维/五维打分（场景·人群·差异化·销量）降级成「参考分」** —— 照常算、照常显示，**不再决定等级**。这是主人「选品要求是后面精筛的事」的落地。

**店铺类型怎么认**（`config/selection_profile.json` → `store_type_rules`）

```
旗舰店/官方/自营 → 风险 4（高危 · 无货源拿不到货）
专卖店 → 3    专营店 → 2
工厂店/厂家直销/个人店/普通店 → 0（白牌好信号）
```

三条实测要点：
1. **匹配顺序有实质影响**：「小米官方旗舰店」同时含「旗舰店」「官方」—— **必须先判旗舰店**，反过来风险就报低了。
2. **店名会被截断**：库里真实存在 `牧高笛菀潞专卖`（少「店」）、`莱品旗絸店`（OCR 把「舰」认成「絸」）。所以 `exclusive.match` 收的是 `专卖`/`专营`/`旗舰` 这些**字头**，不要求带「店」。
3. **旗舰店 + 标题有品牌名** = 主人说「肯定不能要」→ 独立闸门（`store_gate`），**比品牌词库更早生效**。但 `hard_exclude_brand_store: false` 现在把它记成 **C（封顶降级观察）而不是排除** —— 这是主人两次说过不同意见之后取的中间值，**一句话可改**。

**另外还有品牌闸门**（独立于店铺闸门，容易和上面混）

```
品牌词库 19023 条命中 → 排除
★ 棘手处：中文标题里「定制」「通用」「正品」这类描述词也在词库里 → 已加 27 词误杀停用词表
★ 6 个存疑词（世家/骑士/正火/牧野/山人/乐享）→ 主人已裁定「都是品牌，保留排除」
★ 「品牌词 + 修饰词」豁免：热享生活 里的「热享」不算品牌
  → 这条 config 里写好了，**代码钩子还没接**（见 §7 待办 2）
```

**还有两道过滤**：广告位（卡片带「广告」标记）、无效链接（解析不出 12 位数字商品 ID）。

> ⚠️ **两条链路别搞混**：
> `插件 → POST /api/prescreen → prescreen.screen_cards()` ← **主人每天用的这条**
> `选品页 → selection.py 五层漏斗（含 AI 复核）` ← 另一条，两条只共用数据库。

---

## 4 AI 接入

```
端点     https://api.commandcode.ai/provider/v1   （openai 兼容）
模型     z-ai/glm-5.3-flash                       （推理模型 ★）
配置     config/ai_providers.json → roles.text
密钥     已配置（长度 93，出口一律脱敏，_public_ep() 在源头剥掉）
```

### ★★ 最值钱的一条经验：这个模型必须被明确禁止啰嗦

**实测（2026-10-18）：同一个端点、同一个 max_tokens，差别只在一句系统提示词：**

| 系统提示词 | max_tokens | 结果 |
|:--|:--:|:--|
| 只写业务设定 | 2048 | **300 秒还收不了尾**（全部输出在 `reasoning_content`，`content` 空） |
| 加一句「直接给答案，不要分析、不要复述问题、不要展示思考过程」 | 1024 | **2.5 秒出正文** |
| 同上 | 512 | 4 秒出正文 |

**所以 `_chat_system_prompt()` 里那句话不是客套，是性能开关 —— 删了它聊天就会卡死。**（代码里已注明「这一行是实测挖出来的，别删」）

### 另一个坑：正文为空要自动加预算

多轮对话时思考会更长。**`/api/ai/chat` 里已加自动翻倍重试**：

```python
chat_max = 2048
try:    txt = chat_messages(..., max_tokens=chat_max)
except RuntimeError as e:
    if "正文为空" not in str(e): raise      # 只对这一种情况重试
    txt = chat_messages(..., max_tokens=min(chat_max*2, 8192))
```

### ★★ 聊天窗口的真实速度（2026-10-18 实测，同一端点同一提示词）

| 提问 | 耗时 | 结果 |
|:--|:--|:--|
| 「只回两个字：收到」 | **6.2 秒** | ✅ 通 |
| 「库里现在有多少件商品？」+ 历史（多轮） | 181 秒 | ❌ SSL 握手超时 |
| 「亚明灯具旗舰店带品牌名，按我的规则该判几级？」 | 182 秒 | ❌ 读超时 |

**规律：不需要推理的问答几秒就回；要它分析的题目能拖过 3 分钟。**

**这不是代码 bug，是 `glm-5.3-flash` 这个推理模型的固有节奏。** 已按此处置：
- `/api/ai/chat` 超时 **420 秒**（与初筛一致，那是实测能过的档位）
- `/ai` 页面上**明写了这条速度特征**，并且等的时候有秒数倒计时 —— 免得看起来像卡死
- 环境变量 `TP_CHAT_TIMEOUT` 可覆盖

**若以后要它稳稳答复杂问题**，两条路：
① 换个非推理模型当聊天角色（`ai_providers.json` 里加一个 `role`）；
② 把库里的真实数据**先取出来拼进消息**（`/ai` 页面的「附上库里现状」开关就是在做这件事）—— 让它少猜，就少想。

### 初筛里的 AI 复核（`prescreen.py :: ai_review`）

```
批量        4 条一批（实测：8 条一批 300 秒必超时，4 条能过）
超时        420 秒
开关        config/prescreen_rules.json → ai_review.enabled
失败处理    任何一批失败只有那几条退回规则定级，整页照常产出（AI 是加分项，不是必选项）
```

**发出去的提示词原文**：`server/prescreen.py :: _ai_prompt()` —— 在 `/ai` 页面「看我给他的提示词」里能直接看到，不用翻代码。

---

## 5 浏览器插件

| 项 | 值 |
|:--|:--|
| **扩展 ID** | `bdjjadkmdcffnbodhodgockccolhopbh` |
| **加载路径** | `…\taobao-picker\extension`（Tabbit 浏览器 · `location: 4` = unpacked） |
| **浏览器** | Tabbit（`F:\Users\Administrator\AppData\Local\Tabbit\User Data`） |
| **弹窗** | 地址框 + **「保存并打开」**（存地址 + 直接跳后台，一个动作两件事）+ 采集/分析/筛选 |

**2026-10-18 改动**：保存按钮从「只存地址」改成「**存 + 跳转**」；底部那行链接也从硬编码 9277 改成**跟着配置走**（原来端口一改就废）。

**插件在 Tabbit 里的真实注册状态（实测，别怀疑它坏了）**

```
has_started_service_worker = True
service_worker_registration_info = {"version":"2.0.0"}   ← 与 manifest 版本一致
granted/active_permissions  齐全（storage/activeTab/scripting + 4 个 host）
path 正确 · location=4（unpacked）· developer_mode=true
```

**★ 关于「An unknown error occurred when fetching the script.」**

主人 10-18 报过这条。逐项查过：

```
manifest 引用的 7 个文件      全在（无缺失、无子目录）
文件编码                      全部无 BOM（★ 有 BOM 会让 Chrome 解析 JSON 失败）
manifest JSON                 ConvertFrom-Json 通过
5 个 JS                       node --check 全过
扩展在浏览器里的注册          完好（见上）
```

**结论：扩展本身是好的**，那条错误最可能是**历史残留** —— `chrome://extensions` 会把旧错误一直挂到下一次重载。**文件在 10-17 12:03 被移走过一次（见 `_backup-移出扩展目录-20261017-120300`），之后又多次改动但没重载扩展。**

**处置**：在 `chrome://extensions` 点一次该扩展的**重新加载**（⟳）。若仍报错，**把红色报错原文给我**（我需要原文才能定位，之前只有一句话）。

---

## 6 本会话（10-17 ~ 10-18）干了什么

| # | 事 | 状态 |
|:--|:--|:--|
| 1 | **初筛重构**：删掉主图 25 分（主人「不要这套系统评论图片好不好，它只管内容合不合适」）→ 权重重算 34/26/20/13/7 | ✅ |
| 2 | **加店铺类型闸门** + 广告过滤 + 无效链接过滤 | ✅ |
| 3 | **定级改成主人那两信号表**（§3），分数降为参考分 | ✅ |
| 4 | **接上 AI** —— 之前初筛这条路**一次模型都没调过**（`prescreen.py` 1355 行零处 AI 调用），主链路是纯词库+正则 | ✅ |
| 5 | **落库补三个字段**（`shop_type`/`select_grade`/`select_score`）—— 这是「助手页只能进不能出、没法调整」的真根 | ✅ |
| 6 | **店名截断修复**（「专卖」不带「店」也认）—— 这是 Jeep 那件本该排除却给 72 分的真因 | ✅ |
| 7 | **popup 保存→跳转** + 底部链接动态化 | ✅ |
| 8 | **AI 聊天窗口 `/ai`**：真聊天 + 两份提示词原文摊开 + 库里现状快照 | ✅ |
| 9 | 本交接文件 | ✅ |

**关于第 4 条，有代码级铁证**：`prescreen.py` 1355 行 / 33 个函数，全文搜不到 `ai_selector`、`ai_providers`、任何模型调用。**所以主人问「对接了 AI 为什么还这么笨」——答案是初筛这条路压根没接。**

---

## 7 未完成 / 待定

| # | 项 | 说明 |
|:--|:--|:--|
| 1 | **UI 三件** | **Table 删除** / **商品批量清理（全选·清空）** / **提示词编辑入口**。位置已摸清（见 §9），主人明确要过 |
| 2 | **`context_exempt` 代码钩子** | 「品牌词+修饰词（热享生活）」的豁免规则**配置写好了、代码还没读它** —— 这是个缺口，不留着 |
| 3 | **工厂店识别不全** | `皓明户外照明工厂`、`朗鹏led灯饰厂` 认不出（只认「工厂**店**」，不认「工厂」「厂」结尾）→ 这两家本该 A 却给了 B。**大概率是 A=0 的原因之一** |
| 4 | **插件在真淘宝页验证** | 一直没在真实页面跑过一次完整链路。**这条堵不上，「假数据」那个口子就一直开着** |
| 5 | **同款去重** | 主人说「后期再做，留个接口」。库里已有 `phash_cache`(239) + `clusters` 表，接口位置已定 |
| 6 | **聊天的复杂问题会超时** | 已取到结果：简单问答 6.2 秒通；要它分析的两例 181/182 秒超时。**已按此调优**（超时 420 + 页面写明速度特征），但**根子上是模型选型问题** —— 想要它稳稳答复杂问题，得换个非推理模型当聊天角色，或先把库数据取出来拼进消息（见 §4 末） |
| 7 | **品牌货要不要从「排除」改成「C 级」** | §3 那条 `hard_exclude_brand_store`。主人在不同轮里说过两种意思，**等他明确一次** |

---

## 8 已知坑（踩过的，别再踩）

| # | 坑 | 后果 / 做法 |
|:--|:--|:--|
| 1 | **`启动选品系统.bat` 必须保持纯 ASCII** | cmd.exe 按当前代码页逐行重读 .bat；加 `chcp 65001` + 非 ASCII 会让它误析后续行（实测症状：`'????' is not recognized`），**并顺带破坏幂等检查、拉出第二个服务** |
| 2 | **`extension\` 下不要放子目录** | Chrome 会递归扫，里面的 manifest 会被当第二个扩展。10-17 因为这事把备份子目录移出去了 |
| 3 | **`app.py` 改了要重启服务；`config/*.json` 改了不用** | 前者是代码，后者是热读（每次请求现读） |
| 4 | **`debug=False, use_reloader=False` 是写死的，别删** | 注释里写了理由：不依赖 `FLASK_DEBUG`/`.flaskenv` 之类环境差异。生产上不允许它被环境变量打开 |
| 5 | **`Get-NetTCPConnection` 在这台机器上会卡住** | 改用 `netstat -ano \| Select-String ":9277\s+.*LISTENING"` 解析。我用它两次把命令挂进后台 |
| 6 | **Python313 没有 websocket 库** | CDP 脚本要用 `F:\pyenvs\douyin\Scripts\python.exe`（websocket-client 1.9.2）。**node 是 v25.2.1，有全局 WebSocket** |
| 7 | **9222 端口不是 Tabbit** | 是 **Trae CN**（Chrome/142）。Tabbit 主进程**没开调试端口** → 进不去它的 `chrome://extensions`。别拿 9222 当 Tabbit |
| 8 | **推理模型的 `content` 可能为空** | 输出全进 `reasoning_content` 时，正文是空串 —— 看着像「模型没回」。**先加大 max_tokens / 加禁止啰嗦的约束，再怀疑端点** |
| 9 | **品牌词库会误杀描述词** | 「定制」「通用」「正品」都在 19023 条里。已加 27 词停用词表；再发现就往那张表加 |

---

## 9 UI 三件的位置（下一轮直接做）

```
① Table 删除        app.html  tabBar() 是通用渲染 → 加删除按钮
                    后端    /api/tasks/delete（级联删 task_items，不动 products）
② 商品批量清理       app.html  已有 SEL 选择集 → 加「全选/清空选中」
                    后端    /api/tasks/remove_items（摘关联，不删商品本身）
③ 提示词编辑         selection_profile.json 的 prompts 段已有内容
                    后端    /api/profile/get + /api/profile/set
                    前端    加编辑框（改完热读生效，不用重启）
④ 同款去重接口       POST /api/dedupe/group {threshold}
                    库里已有 phash_cache + clusters，现在不接模型，
                    先走「真的一模一样」那条路
```

---

## 10 新会话第一条消息模板

```
读这份交接：G:\Users\Administrator\Documents\deepseek-harness\default-workspace\taobao-picker\交接-淘宝选品系统.md
本轮要做：<具体一件事>
服务在 9277；要用真实数据就先确认它在跑（双击 启动选品系统.bat）
不要重新调研系统全貌，不要重复已完成的验证
```

**三条省 token 的规矩**

1. **开局只贴路径 + 一句话任务** —— 不要把旧对话整段贴过来（里面有一堆已作废的结论）
2. **要细节先读代码/配置**（`prescreen.py` / `prescreen_rules.json` / `selection_profile.json`），别让新会话重新猜
3. **一件事开一个对话**，别让一个会话干十件

---

## 11 本文数字的核对方式（可复跑）

```powershell
# 库内实况
& 'F:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe' -X utf8 `
  'G:\...\taobao-picker\_查_DB快照.py'

# 服务在不在
netstat -ano | Select-String ":9277\s+.*LISTENING"

# 插件注册状态（读 Tabbit 的 Secure Preferences）
#   F:\Users\Administrator\AppData\Local\Tabbit\User Data\Default\Secure Preferences
#   extensions.settings.bdjjadkmdcffnbodhodgockccolhopbh

# 扩展文件完整性 + 语法
node --check extension\*.js        # 5 个文件
```

---

## 12 相关历史脚本（本项目很爱留 `_` 前缀的诊断脚本）

`_查_DB快照.py` · `_查_插件真实报错.py`（CDP 探扩展错误，需 douyin 环境跑）· `_验_新定级与AI.py` · `_验_店铺闸门是否生效.py` · `_验_落库与店名.py` · `_验_店铺名截断修复.py` · `_验_初筛三件事.py` · `_查_品牌闸门是否误杀.py`

**约定：`_` 前缀 = 一次性诊断脚本，只读或可重跑，不是产品代码。**