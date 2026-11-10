# AI 运营中心 · 浏览器自动化四方案对比

> 对比对象：**A. Maxun** ／ **B. Playwright** ／ **C. browser-use** ／ **D. 本机 ego lite**
> 依据来源：A 用《AI运营中心-Maxun源码研究.md》的真源码 + 隔离实测；B 用官方文档 + 本机实测；C 用官方 README/文档；D 用本机已有脚本 + ego-browser 当版手册（v2.2.0）
> 目标场景：抖音小店 / 微信小店**后台写入**（填表 + 上传图片视频 + 提交），并作为「上货台 7.0 浏览器自动化执行器」
> 标注：`[源码确认]` 附路径:行号 ／ `[本机实测]` 附命令与输出 ／ `[官方文档]` 附链接 ／ `[设计建议]` ／ `[尚未验证]`

---

## 0. 结论先行（一屏看完）

| 方案 | 一句话定性 | 能不能做「填表 + 上传 + 提交」 |
|:--|:--|:--|
| **A Maxun** | 带浏览器的**采集机**（录→放→取数），不是 RPA | ❌ 上传录不下来；登录态不持久；失败不置错 → **不能用于写入** |
| **B Playwright** | 工业级**浏览器驱动库**，确定性强、取证全 | ✅ **能**，但要自己写全部业务代码与登录态管理 |
| **C browser-use** | **LLM 驱动的浏览器 Agent**，附带「录一次→确定性重放」的旁路 | ✅ 能力上能，**但每步都要模型判断，成本与不确定性高** |
| **D ego lite（本机）** | **已装好、已带登录态**的浏览器 + 一套干净 Page API（**不是 Playwright**） | ✅ **能**，且是四者里唯一「复用你现有登录状态」的 |

**最终组合（一句话）**：**ego lite 当确定性执行主力 → Playwright 当批量/回归与取证副手 → browser-use 只在页面改版、流程跑不通时做一次性探索并把结果固化成固定脚本 → Maxun 不进生产链路。**

---

## 1. 各方案的事实底座（先把「它到底是什么」钉死）

### A · Maxun

| 项 | 事实 | 来源 |
|:--|:--|:--|
| 版本 | 根包 `0.1.0`，`maxun-core 0.0.38`，`browser-service 1.0.0` | `[源码确认]` `package.json:3`、`maxun-core/package.json:3`、`browser/package.json:3` |
| 最近提交 | 2026-11-07 21:07:13 +0530（HEAD `99c6bd0`） | `[本机实测]` `git log -1` |
| 许可 | **AGPL-3.0-or-later** | `[源码确认]` `LICENSE:1-2`、`package.json:4` |
| 引擎 | **Playwright 1.57.0**（`playwright-extra` + stealth 插件） | `[源码确认]` `browser/server.ts:1-7,20-38`、`browser/package.json:4` |
| 部署 | Docker Compose 5 服务（postgres:13 / minio / backend / frontend / browser）；也可不用 Docker，但需自备 PG + MinIO + Redis | `[源码确认]` `docker-compose.yml:1-134`、`SETUP.md:14-40` |
| 登录态 | **无 `storageState`、无 `userDataDir`**；每次 `newContext()` | `[源码确认]` `RemoteBrowser.ts:581`；`[本机实测]` 全仓 grep `storageState` 零命中 |
| 上传 | **录制器不支持**；只能手写 workflow 调 `setInputFiles`，**无 filechooser 处理** | `[本机实测]` 全仓 grep `setInputFiles\|filechooser` 零命中；`[源码确认]` `preprocessor.ts:35-40` 只校验 action 是字符串 |
| 失败处理 | 动作失败 `continue`，**run 仍算成功**；无失败自动截图 | `[源码确认]` `interpret.ts:2180-2185` |
| 隔离实测 | `maxun-core` 单独驱动：`goto` 之后 click/fill/select/upload **全部超时**；原生 Playwright 同一页面 **131ms 全成功** | `[本机实测]` `result-round-02.json` / `run-test-03.cjs` `N1_native_playwright` |

### B · Playwright

| 项 | 事实 | 来源 |
|:--|:--|:--|
| 定位 | 浏览器自动化**库**（不是产品），Node/Python/Java/.NET | `[官方文档]` https://playwright.dev/docs/library |
| Windows 支持 | 一等公民：`npx playwright install chromium`，Win10+ 原生支持 | `[官方文档]` https://playwright.dev/docs/intro ；`[本机实测]` 本机装成 Chromium Headless Shell 143.0.7499.4（build v1200） |
| 多 context | `browser.newContext()` 每次独立 → cookie/storage 天然隔离 | `[官方文档]` https://playwright.dev/docs/browser-contexts |
| 登录态持久化 | **`context.storageState({ path })`** 存成文件，下次 `newContext({ storageState })` 复用；**支持 IndexedDB**（`{ indexedDB: true }`） | `[官方文档]` https://playwright.dev/docs/auth （原文：`await page.context().storageState({ path: authFile })`；`indexedDB: true` 段） |
| 多账号 | 官方推荐「一个 worker 一个账号」，用 `testInfo.parallelIndex` 区分 | `[官方文档]` 同上「Moderate: one account per parallel worker」 |
| 文件上传 | `setInputFiles()` + `page.on('filechooser')` | `[本机实测]` 本机原生 Playwright `page.setInputFiles('#file', 路径)` → `files:1, fileName: upload-target.txt` |
| 截图 | `page.screenshot({ fullPage })` | `[本机实测]` 同页可用；`[官方文档]` https://playwright.dev/docs/screenshots |
| 取证 | **Trace Viewer**（网络/快照/源码/时间轴），失败自动留 trace | `[官方文档]` https://playwright.dev/docs/trace-viewer |

### C · browser-use

| 项 | 事实 | 来源 |
|:--|:--|:--|
| 版本/许可 | Python 库，**MIT**；`uv add browser-use`，**Python ≥ 3.11** | `[官方文档]` https://github.com/browser-use/browser-use README（"The Python library is free and MIT-licensed"、"Python >= 3.11"） |
| 核心形态 | **LLM 驱动的 Agent**：`Agent(task="自然语言", llm=...)` → `agent.run()` | `[官方文档]` README Quickstart Path 3 |
| 是否「LLM 驱动点击」 | **是**。每个 step 由模型读页面快照后决定动作；`Agent(...)` 自动注入 system prompt | `[官方文档]` README FAQ「Do I need to provide a system prompt?」→ "No. `Agent(...)` supplies the Browser Use system prompt automatically" |
| **但有确定性回放** | `agent.save_history('x.json')` → `Agent(task='').load_and_rerun('x.json', variables={...})`，**逐步点击/输入不再每步调模型** | `[官方文档]` README FAQ「Can I rerun a task without calling the model on every step?」 |
| 回放的限制 | ① 抽取步骤与收尾总结**仍调模型**；② **页面一改就在第一个对不上的步骤抛 `RuntimeError`**，之前的步骤已执行；③ 保存文件含页面快照与结果，**可能含敏感信息** | `[官方文档]` 同上条目内三条说明 |
| 登录态 | `Browser.from_system_chrome()` 复用本机 Chrome profile；或 cloud `cloud_profile_id`（只同步 cookie，不含 localStorage/IndexedDB/扩展） | `[官方文档]` README FAQ「How do I handle authentication?」 |
| 成本模型 | 每步一次 LLM 调用（含页面快照进 prompt）；可用 `ChatBrowserUse(model='bu-2-0')`、`ChatOpenAI`、`ChatAnthropic`、本地 Ollama；cloud browser **$0.02/浏览器小时 + 流量** | `[官方文档]` README「What's the best model to use?」「Cloud browsers cost $0.02 per browser-hour plus network traffic」 |
| Windows 缺口 | Bash 工具集**要求 Linux/macOS 的 `/bin/bash`，Windows 需 WSL** | `[官方文档]` README「Bash requires a Linux or macOS host with `/bin/bash`; use WSL on Windows」 |

### D · 本机 ego lite

| 项 | 事实 | 来源 |
|:--|:--|:--|
| 是什么 | 本机**统一浏览器入口**，命令 `ego-browser`；本机版本 `0.6.2.8` | `[本机实测]` `C:\Program Files\Citro Labs\ego lite\Application\0.6.2.8\ego-browser.exe`；`ego-browser skill` 输出 `ego-browser usage guide v2.2.0 (windows)` |
| 是不是 Playwright | **不是**。手册原文：「Ego-browser deliberately exposes a small custom API. **It is not Playwright**, even where method names and options look similar.」；**无** `locator()`/`getByRole()`/`context()`/`expect()`/`route()` | `[本机实测]` `ego-browser skill` |
| 页与人的隔离 | `task.page()` 是任务自己的页；**`task.userPage()` 是主人的真实标签页** —— 两者互不抢焦点 | `[本机实测]` 手册「Spaces, rounds, and pages」 |
| Page API（完整清单） | 状态：`url()` `title()` `info()` `snapshot()` `screenshot()`；导航：`goto()` `reload()` `waitForURL()` `waitForEvent()` `waitForSelector()` `waitForLoadState()` `waitForFunction()` `waitForTimeout()`；元素：`click()` `dblclick()` `hover()` `dragAndDrop()` `fill()` `selectOption()` `focus()` `press()` **`setInputFiles()`** **`waitForFileChooser()`** `close()`；对话框：`acceptDialog()` `dismissDialog()`；鼠标：`mouse.click/move/down/up/wheel`；键盘：`keyboard.down/up/press/type/insertText/paste`；代码：`evaluate()` `fetch()` **`cdp()`** | `[本机实测]` `ego-browser skill`（Page API 段逐条列出） |
| 选择器 | snapshot 引用 `@21`/`ref=21`、`text=`、`loc=css:`、`loc=role:`、`loc=href:`、`xpath=`、原生 CSS、`:has-text()`、`:text-is()`、`>> nth=N`；**只支持 Playwright 选择器的一个小子集** | `[本机实测]` 手册「Semantic pages: snapshot and selectors」 |
| 上传限制 | `setInputFiles()` 只会「继续到一个唯一的兼容控件」；**隐藏 input 可行，弹系统文件框的需配合 `waitForFileChooser()`** | `[本机实测]` 手册原文：「`fill()` and `setInputFiles()` only continue to a unique compatible control」 |
| 反检测 | 页会话**默认对 CDP 隐藏 console**，站点无法检测自动化；`page.cdp("egoAutomated.disable")` 可临时打开（开了就能被检测到） | `[本机实测]` 手册「Page JavaScript and CDP」末段 |
| 本机现成脚本 | `launch_collect_browser.py`（19,593 B）：CDP 端口自动挑选 9223–9299、**专用配置目录** `%LOCALAPPDATA%\dsh-collect-browser`、5 项校验（`/json/version`、`/json/list`、websocket、`Runtime.evaluate`、`Input.dispatchMouseEvent`）、端口回写 `settings.json` | `[本机实测]` `上货台\launch_collect_browser.py:1-30,140-260` |
| `_验_ego连接.py` | 6,685 B，四层逐项体检；明确指出「采集器 `find_search_page` 只认 `type=page`，`browser_ui`/`iframe` 都不算」；ego 冷启动停在 `chrome://onboarding/` 时**只有 `browser_ui` 没有 page**，需 `Target.createTarget` 才能拿到网页目标 | `[本机实测]` `上货台\_验_ego连接.py:1-190`；`launch_collect_browser.py:170-230` |
| 登录态 | **天然复用**：同一配置档/同一浏览器进程，主人已登录的店铺后台直接可用（不需 `storageState`） | `[设计建议]` 基于「统一入口=一份登录态」的设计（AGENTS.md 浏览器统一入口条）；`[本机实测]` 专用配置目录与日常 ego 隔离，两者可同时开 |
| 多账号 | 靠**配置档（User Data 目录）** 隔离；`launch_collect_browser.py:57` `DEDICATED_ROOT` + `--profile dedicated\|default` | `[本机实测]` `launch_collect_browser.py:45-60,270-300` |

---

## 2. 十四维度逐行对比

> 每个格子只给**可核验的结论**，不写「优秀/一般」这类空话。

| # | 维度 | A Maxun | B Playwright | C browser-use | D ego lite（本机） |
|:--|:--|:--|:--|:--|:--|
| 1 | **固定流程执行稳定性** | 🟡 结构上有 `Where-What` + `maxRepeats`，但**隔离实测中 `goto` 后交互全超时**；且动作失败 `continue` 不置错（`interpret.ts:2180-2185`）→ 稳定性**不可信** | ✅ **最稳**。确定性代码、`auto-waiting`、可设 `timeout`/`expect`；本机实测 click/fill/select/upload **131ms 全通过** | 🟡 有 `load_and_rerun` 确定性回放，但**页面一改就在第一个不匹配步骤抛 `RuntimeError`**，且之前的步骤已执行完（可能已提交） → 回放不安全 | ✅ **稳**。原生 API 直接操作浏览器；无中间解释层；手册明确「动作不产生预期结果时先查页面再决定，不要盲目重试」 |
| 2 | **页面变化后恢复能力** | 🟡 靠 `Where` 条件匹配（url/cookies/selectors/`$before`/`$after`）；条件不成立就重复/跳过。**但无自愈，无候选选择器回退**（`Generator.ts` 只产出单一 selector） | 🟡 **代码层要自己写回退**（`page.locator(A).or(B)`、`getByRole` 语义定位比 CSS 抗改）。库本身不替你决定 | ✅ **最强**（这是它的卖点）：LLM 读当前页面重新判断该点哪里，页面改版也能走通 | 🟡 支持 `loc=role:`、`text=`、`:has-text()` 等**语义选择器**（比纯 CSS 抗改），加 `snapshot` 可现场重建目标；但**不自愈**，要人/上层判断 |
| 3 | **本地浏览器控制** | ❌ 不控制你本机浏览器：`browser` 服务 **`headless: true` 硬编码**，通过 WS `:3001` 连接，是**它自己的容器内 Chromium**（`browser/server.ts:20-38`） | ✅ 完全可控：可 `headless:false`、可 `channel:'chrome'`、可 `launchPersistentContext(userDataDir)` | ✅ 可控：`Browser.from_system_chrome()` 复用本机 Chrome profile；也支持连远端 CDP | ✅ **本机就是它**：`ego-browser` 即本机浏览器；`task.userPage()` 可接主人当前标签页 |
| 4 | **登录状态保持** | ❌ **最弱**。无 `storageState`、无 `userDataDir`，每次 `newContext()`；只把「登录」当一种 robot 类型（`isLogin`）。10 分钟录制超时（`controller.ts:16`） | ✅ **标准答案**：`storageState({path})` 存文件、`newContext({storageState})` 复用，含 IndexedDB（`{indexedDB:true}`） | ✅ `from_system_chrome()` 复用真实 profile；cloud 侧 `profile sync` **只传 cookie，不传 localStorage/IndexedDB** | ✅ **最省事**：同一配置档，**主人扫码登录一次，之后一直有效**；`launch_collect_browser.py` 已把「专用配置目录」与日常浏览隔离做好 |
| 5 | **多账号隔离** | 🟡 隔离维度是 **`userId` + 状态**（`BrowserPool.ts:10`、`controller.ts:252-266`），**不是「多店铺」**；每 userId 每状态只有 1 个实例 | ✅ **最灵活**：一个店铺一个 `BrowserContext`（+ 一份 `storageState` 文件），官方「一个 worker 一个账号」范式 | 🟡 本地靠多个 Chrome profile；cloud 靠 `cloud_profile_id`（cookie 级） | 🟡 **可行但重**：一个店铺一个 User Data 目录 = 一个浏览器进程；`launch_collect_browser.py` 已有 `--profile` 机制，但目前只支持单一 `dedicated` 目录（扩展需改脚本） |
| 6 | **视频上传** | ❌ **录制器做不到**（无 upload 动作）；且无 filechooser 处理 | ✅ `setInputFiles()`；大文件可配合 `page.waitForEvent('filechooser')`；**无大小限制（由站点决定）** | 🟡 有 upload 工具，但**走 LLM 判断**（"上传这个文件"），大文件易超时/多步；token 成本随页面上传进度提示增长 | ✅ **`setInputFiles()` + `waitForFileChooser()` 都在手册里**；隐藏 input 与「点击弹框」两种都能处理 |
| 7 | **文件选择器操作** | ❌ 无 `filechooser` 代码（全仓 grep 零命中） → 遇「点击弹系统文件框」的站点**无法处理** | ✅ `page.on('filechooser')` / `waitForEvent('filechooser')` | 🟡 有对应 action，但由模型决定何时调用 | ✅ `waitForFileChooser()` 明列在 Page API；手册还说明 `setInputFiles` 会「继续到唯一兼容控件」 |
| 8 | **页面截图** | 🟡 有 `screenshot` 动作（参数完整：fullPage/clip/mask/quality）+ `screenshot-visible/fullpage` 输出；**但无失败自动截图** | ✅ `page.screenshot({fullPage})`；`expect(...).toHaveScreenshot()` 可做视觉回归 | ✅ 有截图动作；cloud 侧还有 recordings | ✅ `page.screenshot({path})`，**路径必须绝对**（Windows 用正斜杠）；另有 `snapshot()` 给语义树 |
| 9 | **错误追踪** | 🟡 有 `Run` 记录 + debug 消息 + **run diff**（`storage.ts:1284`）+ webhook；**但动作失败不置错、无 trace、无失败截图** → 出错难定位 | ✅ **最强**：Trace Viewer（时间轴/网络/DOM 快照/源码）、`--debug`、视频、`test-results` | 🟡 `history` 含每步思考与动作；`save_history` 可落盘；**但 LLM 的「为什么这么点」不可复现** | 🟡 有 `receipt.popups`、`page.events()`、结构化错误码（如 `EGO_PAGE_UNRESPONSIVE`，会告诉你现在停在哪份文档）；**无 trace** |
| 10 | **API 调用（外部触发）** | ✅ 有，且成体系：`POST /api/robots/:id/runs`（`record.ts:1579`）、`POST /api/sdk/robots/:id/execute`（`sdk.ts:673`）、状态 `GET …/runs/:runId`（`record.ts:528`）、中止 `storage.ts:1564` | ✅ 库级 API（你自己包一层）；`@playwright/test` 有 CLI/报告器 | ✅ Python API + **MCP server** + hosted Cloud Agent API | ✅ `ego-browser nodejs -e "await import('file:///...mjs')"`：**每条命令一个进程**，天然可被上层（Python/Node）当子进程调用；无常驻服务、无端口 |
| 11 | **资源占用** | 🔴 **最重**：5 个容器（PG + MinIO + backend + frontend + browser），`mem_limit: 6g`、`shm_size: 2gb`、`cap_add: SYS_ADMIN`（`docker-compose.yml:40-53,110-134`） | 🟢 轻：一个 Chromium 进程 + node 进程；无外部服务 | 🟡 中：Python + 一个浏览器 + **每步一次 LLM 请求**（网络与 GPU/云成本） | 🟢 **最轻**：就是本机已有的 ego lite（**本来就在跑**），零新增常驻服务 |
| 12 | **Token 消耗** | 🟢 录制回放**零 token**；只有 AI Extract 才调模型，且 provider 可设 `ollama` 本地（`Robot.ts:21`） | 🟢 **零 token**（纯代码） | 🔴 **最高**：每步把页面快照送模型；`load_and_rerun` 可省大部分，但**抽取与收尾仍调模型**；官方建议用 `bu-2-0` 或 GPT/Claude 高档模型 | 🟢 **零 token**（纯 API 调用）；只有上层若接 AI 诊断才产生 |
| 13 | **Windows 兼容性** | 🟡 官方主推 Docker；无 Docker 路径要求 PG+MinIO+Redis；`docs/generate-env.sh` 是 bash；容器参数（`seccomp`/`SYS_ADMIN`/`dbus` 挂载）是 Linux 语义 | ✅ **一等公民**：`npx playwright install`，Win10+ 原生；本机实测装成并跑通 | 🟡 Python 库跨平台；但 **Bash 工具集需 WSL**（README 明写）；`from_system_chrome()` 在 Windows 可用性 `[尚未验证]` | ✅ **为 Windows 而生**：本机 `0.6.2.8` 已装、已在用；`launch_collect_browser.py` 里 `NO_WINDOW=0x08000000` 专门处理 Windows 无窗启动 |
| 14 | **与现有上货台集成成本** | 🔴 **高**：要引 PG/MinIO/Redis 或 Docker；要重写「登录态」；要绕过它「失败不算失败」的语义；AGPL 传染风险；且它**不控制你已登录的浏览器** | 🟡 **中**：要新写全部脚本 + 登录态管理 + 与 PyQt6 主程序通信（子进程/HTTP）；但技术栈独立、无外部依赖 | 🔴 **高**：要引入 Python 运行时 + 模型配置 + 密钥管理；每步结果不确定 → 需大量重试与护栏；与「确定性优先」原则冲突 | 🟢 **最低**：`launch_collect_browser.py` 与 `_验_ego连接.py` **已经在本机跑通**；`settings.json` 的 `cdp_port` 已回写；PyQt6 侧只要起子进程 |

---

## 3. 三张关键对照（回答具体疑问）

### 3.1 「Maxun 到底能不能填表 + 上传 + 提交」

| 动作 | 录制器能产 | 手写 workflow 能跑 | 实测证据 | 判定 |
|:--|:--|:--|:--|:--|
| 填表（`fill`/`type`） | ✅ | ✅ | `[源码确认]` `Generator.ts:373,413,433,1521` | **能** |
| 下拉（`selectOption`） | ✅ | ✅ | `[源码确认]` `Generator.ts:393`；`inputHandlers.ts:940` | **能** |
| 点击提交（`click`） | ✅ | ✅ | `[源码确认]` `Generator.ts:456,464,682,703` | **能** |
| 上传文件 | ❌ | ✅（仅 `setInputFiles`） | `[本机实测]` grep 零命中；手写后进入 `page.setInputFiles` 路径（超时日志证明未被拦） | **只能绕过** |
| 「点按钮弹系统文件框」式上传 | ❌ | ❌（无 filechooser） | `[本机实测]` 全仓无 `filechooser` 代码 | **不能** |
| 提交后置「成功/失败」 | ❌ | ❌ | `[源码确认]` `interpret.ts:2180-2185` 失败 `continue` | **不能** |

→ **「能填能点不能上传、且无法判定成败」。**

### 3.2 「browser-use 是不是 LLM 驱动点击」

**是。** 三个层面确认：

1. 形态：`Agent(task="自然语言", llm=...)` → `agent.run()`（`[官方文档]` README Quickstart）；
2. 系统提示词由库自动注入，说明「每步怎么点」由模型决定（README FAQ：「No. `Agent(...)` supplies the Browser Use system prompt automatically」）；
3. 成本模型即证据：**每步一次模型调用**，官方专门给了「如何不每步调模型」的答案 —— `save_history` + `load_and_rerun`，且明确「**抽取步骤与收尾总结仍调模型**」。

**成本量化（`[官方文档]` 部分 + `[设计建议]` 推算）**：

| 项 | 数字/口径 | 来源 |
|:--|:--|:--|
| cloud 浏览器 | $0.02 / 浏览器小时 + 流量 | README |
| 模型 | `bu-2-0`（官方推荐）或自带 OpenAI/Anthropic/本地 Ollama | README |
| 每步 token | **官方未公布固定值**（取决于页面快照大小） | `[尚未验证]` |
| 一个「上货」任务步数（估） | 打开→登录→选类目→填标题→填价格→传主图→传详情→传视频→填 SKU→提交 ≈ **15–40 步** | `[设计建议]` |

→ **一条上货 = 15~40 次模型调用**。用 `load_and_rerun` 可把「点击/输入」变成确定性回放，但**每一步的页面快照仍要进模型做抽取**，省不干净。

### 3.3 「ego lite 到底能做什么」（不是 Playwright，这点必须钉死）

| Playwright 有 | ego lite 有没有 | 替代路径 |
|:--|:--|:--|
| `locator()` / `getByRole()` | ❌ | `loc=role:button[name='提交']`、`text=`、snapshot 引用 `@21` |
| `context()` / 多 context | ❌（有 TaskSpace + Page） | 一个目标一个 TaskSpace；多账号靠多个 User Data 目录 |
| `expect()` | ❌ | `waitForSelector()` / `waitForFunction()` 自己断言 |
| `route()`（网络拦截） | ❌ | `page.cdp('Network.setRequestInterception')` 一类 CDP 原生调用 |
| `storageState` | ❌（但**不需要**——配置档本身就是登录态） | 直接复用浏览器 profile |
| `setInputFiles()` | ✅ **有** | — |
| `waitForFileChooser()` | ✅ **有** | — |
| `screenshot()` | ✅ 有（路径必须绝对，Windows 用 `/`） | — |
| `evaluate()` | ✅ 有（一个 JSON 参数、返回 JSON） | — |
| `cdp()` | ✅ 有（Page/Runtime/DOM/Network/Input） | — |
| trace | ❌ | 自己做截图 + `page.events()` + 结构化日志 |

**结论**：ego lite 覆盖了「上货」需要的 **全部关键动作**（点击、填表、下拉、上传、文件框、截图、执行 JS、CDP 兜底），**只缺 trace 与多 context 抽象** —— 而这两件正是「自己写固定脚本」时最容易补的。

---

## 4. 最终组合结论（明确到「用谁、不用谁、为什么」）

### 4.1 选定的组合

```
┌─────────────────────────────────────────────────────────────┐
│  上货台 7.0 · 浏览器自动化执行层                              │
│                                                             │
│  ① 主力执行器：ego lite（Page API，固定脚本）                 │
│     · 复用主人已登录的店铺后台（零登录成本）                  │
│     · 确定性脚本：点击/填表/下拉/上传/截图 全部原生支持        │
│     · 每个动作写「成功判据」，失败即抛，不静默继续             │
│                                                             │
│  ② 副手：Playwright（headless，独立 profile + storageState）   │
│     · 批量并行（多店铺）、离线回归、trace 取证                │
│     · 只在「不需要人看着」的批处理任务里用                     │
│                                                             │
│  ③ 异常探针：browser-use（只在跑不通时用，不进生产链路）       │
│     · 页面改版 / 选择器全失效 → 让它探索一次                  │
│     · 成功后用 save_history 固化，人工改写成 ① 的固定脚本      │
│                                                             │
│  ④ 不采用：Maxun                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 为什么这样分（对齐主人原则）

> 主人原话：「**优先采用确定性的固定脚本，AI 只处理异常和需要理解页面的复杂判断**。」

| 环节 | 谁干 | 为什么符合这条原则 |
|:--|:--|:--|
| 99% 的日常上货 | **ego lite 固定脚本** | 确定性、零 token、可复现、可审计。改一次商品字段就是改一个参数 |
| 页面改版导致脚本失败 | **browser-use 探一次** | 这正是「需要理解页面的复杂判断」——一次性、可隔离、不常驻 |
| 探索结果落地 | **人工/我改写成固定脚本** | 把不确定性**一次性固化**，之后又回到确定性 |
| 批量跑无人值守 | **Playwright** | 隔离 profile + `storageState`，可并行、可出 trace |

### 4.3 为什么**不用** Maxun（逐条，不含糊）

| 理由 | 硬证据 |
|:--|:--|
| 1. **上传录不下来** | 录制器无 upload 动作；全仓无 `filechooser`（`[本机实测]` grep 零命中）→ 上货核心动作缺位 |
| 2. **登录态不持久** | 无 `storageState`/`userDataDir`，每次 `newContext()`（`RemoteBrowser.ts:581`）→ 每次都要重登，遇扫码即断 |
| 3. **失败不置错** | 动作失败 `continue`，run 仍成功（`interpret.ts:2180-2185`）→ 与「上货不能出错」直接冲突 |
| 4. **无失败截图** | 只有显式 `screenshot` 动作（`interpret.ts:782`）→ 出错无现场 |
| 5. **不控制你的浏览器** | `headless: true` 硬编码 + WS 连它自己容器里的 Chromium（`browser/server.ts:20-38`）→ 无法「人工接管」，也无法复用已登录会话 |
| 6. **依赖重** | 5 容器 / PG + MinIO + Redis（`docker-compose.yml`），本机**无 Docker**（`[本机实测]` `docker --version` 报错） |
| 7. **AGPL-3.0** | `LICENSE:1-2` → 改造后有源码开放义务 |
| 8. **0.1.0 早期** | `package.json:3`，2026-11 仍在 renovate 合并 → API 会变 |
| 9. **隔离实测不可靠** | `goto` 之后交互全超时，原生 Playwright 同页 131ms 全过（`result-round-02/03/04/05`） |

### 4.4 为什么**不以 Playwright 为唯一主力**

它技术上完全够（甚至更强：trace、多 context、storageState、filechooser 全有），但：

- **登录态要自己养**：`storageState` 文件会过期，抖店/微信小店的扫码登录得定期人工介入；
- **要新开浏览器进程**：与主人「一个浏览器 = 一份登录态」的统一入口约定冲突（AGENTS.md 浏览器统一入口条）；
- **isolation 要自己搭**：多店铺 = 多 profile + 多 storageState 文件 + 自己调度。

→ 所以它是**副手**，不是主力。**唯一例外**：批量无人值守（如夜间批量改标题）适合它，因为那时不需要人扫码。

### 4.5 为什么**不让 browser-use 常驻**

- 每步一次模型调用 → 一条上货 15~40 次调用，**成本与延迟不可控**；
- 页面一改，`load_and_rerun` 会在**第一个对不上的步骤抛错，而之前的步骤已执行**（官方原文）→ 对「提交」类动作**极其危险**（可能已提交一半）；
- 它的长处（理解页面）恰好只在「异常时」需要 —— 放进生产主链路等于用最贵的手段做最确定的事。

---

## 5. 落地建议（给架构组的具体动作）

`[设计建议]`

1. **执行器接口先定成三个方法**（与用哪个引擎解耦）：
   `open(url)` / `run_steps(steps: Step[])` / `verify(assertions)`，`Step` 里**必带 `expect_after`**（这一步做完页面该变成什么样）。这样将来换引擎不改业务代码。
2. **每个动作必须有成功判据**：`click` 之后 `waitForSelector(成功标志)`，超时即抛。**禁止「点了就算完」**（这正是 Maxun 的病）。
3. **失败必留三件套**：截图（绝对路径）+ 当前 URL + 页面语义快照（`page.snapshot()`）。**由执行器统一兜，不靠每个脚本自己写**。
4. **上传两条路都要实现**：① 隐藏 input → `setInputFiles()`；② 点击弹框 → `waitForFileChooser()` 后 `setInputFiles()`。上货台的视频/主图上传必然两种都遇到。
5. **登录态策略**：主用 ego lite 的配置档（人登录一次，长期有效）；Playwright 只在批处理里用 `storageState`，且**给它独立的 profile 目录**（`launch_collect_browser.py` 的 `DEDICATED_ROOT` 已示范）。
6. **不要引入 Maxun**，即便只想「抓同行数据」—— 抓取用 Playwright 30 行就够，没必要背 5 个容器。
7. **browser-use 只装在「诊断机」上**，不装上货机；触发条件写死（连续 N 次选择器失效才调用），结果必须人工固化成脚本。

---

## 6. 未完成与未验证（如实）

| 项 | 状态 | 原因 |
|:--|:--|:--|
| Maxun 全栈运行（Docker / 无 Docker 两条路） | ❌ 未做 | 本机无 Docker；缺 PG / MinIO / Redis |
| Maxun 真实录制器 UI 操作 | ❌ 未做 | 依赖 frontend + backend |
| 从 Maxun API 触发 run | ❌ 未做 | 同上 |
| `maxun-core` goto 后交互超时的根因行号 | `[尚未验证]` | 已排除 3 个假设（scraper 注入 / where 重放 / headless），未继续深挖（诊断时限） |
| browser-use 在本机 Windows 实跑 | ❌ 未做 | 需 Python ≥3.11 + 模型密钥；本轮不引入新运行时 |
| `Browser.from_system_chrome()` 在 Windows 的可用性 | `[尚未验证]` | 官方未明示 Windows；未实测 |
| ego lite `waitForFileChooser()` 实测 | `[尚未验证]` | 手册列出该 API，本轮未对真实文件框跑（避免与主人浏览器抢焦点） |
| 抖店/微信小店真实后台的上传控件形态 | `[尚未验证]` | 本轮禁止在真实后台做测试性操作 |
| Playwright 在本机的多 profile 并行实测 | ❌ 未做 | 本轮只验了单 context 的 click/fill/select/upload |

---

## 附录 · 证据文件位置

```
Maxun 源码       D:\DSH_Cache\downloads\maxun\            （HEAD 99c6bd0，2026-11-07）
隔离测试环境      D:\DSH_Cache\downloads\maxun-test\
  ├─ testpage.html            本地测试页（含 input/select/button/file）
  ├─ upload-target.txt        上传探针文件
  ├─ run-test.cjs             整流程（含 scrape）
  ├─ run-test-02.cjs          8 项单动作逐项验证
  ├─ run-test-03.cjs          原生 Playwright vs maxun 选择器对照
  ├─ run-test-04.cjs          scraper.js 注入 / 预导航 归因
  ├─ run-test-05.cjs          where 条件重放归因
  └─ result-round-0{1..5}.json  逐轮原始结果
本机 ego 证据     上货台\launch_collect_browser.py（19,593 B）
                  上货台\_验_ego连接.py（6,685 B）
                  ego-browser skill 手册 v2.2.0（本机 0.6.2.8）
```

## 附录 · 引用链接

- Playwright 认证 / storageState：https://playwright.dev/docs/auth
- Playwright 浏览器上下文隔离：https://playwright.dev/docs/browser-contexts
- Playwright 截图：https://playwright.dev/docs/screenshots
- Playwright Trace Viewer：https://playwright.dev/docs/trace-viewer
- browser-use 仓库：https://github.com/browser-use/browser-use
- browser-use 文档：https://docs.browser-use.com
- Maxun 仓库：https://github.com/getmaxun/maxun
- Maxun 文档：https://docs.maxun.dev
