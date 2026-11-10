# AI 运营中心 · Maxun 源码研究

> 调查对象：`https://github.com/getmaxun/maxun`
> 落地目录：`D:\DSH_Cache\downloads\maxun\`（`git clone --depth 50`，HEAD `99c6bd0d42b14336b706ba121a3757f62a9814f7`）
> 研究方式：真下载真读源码 + 隔离实测（本地测试页，非真实电商后台）
> 本文所有结论标注：`[源码确认]` 附 repo 路径:行号 ／ `[本机实测]` 附命令与输出 ／ `[官方文档]` 附链接 ／ `[设计建议]` ／ `[尚未验证]`

---

## 0. 一句话结论

Maxun 是一个**以「网页数据抓取 / 采集」为中心**的开源平台：核心能力是「录制 → 回放 → 取数」，浏览器侧是 Playwright。它**具备**点击、填表、下拉、导航、回放的框架能力，**但不具备**「文件上传」的录制能力，**不具备**失败自动截图，**没有** Playwright 式的 `storageState` 登录态持久化；隔离实测中，仅用 `maxun-core` 包单独驱动时 `goto` 之后的交互动作全部超时（原生 Playwright 同一页面同一浏览器全部成功），根因未定位到具体行。

**判断：不适合承担「商品后台写入 + 提交」的执行器角色**（详见 §5、§24）。

---

## 1. 版本与仓库状态

| 项 | 结论 | 依据 |
|:--|:--|:--|
| 根包版本 | **0.1.0** | `[源码确认]` `package.json:3` `"version": "0.1.0"` |
| 子包版本 | `maxun-core` **0.0.38**；`maxun-browser-service` **1.0.0** | `[源码确认]` `maxun-core/package.json:3`、`browser/package.json:3` |
| 最新 tag | **v0.1.0**（远端 `refs/tags/v0.1.0` → `e95c4e2`） | `[本机实测]` `git ls-remote --tags origin` 输出 `e95c4e25796881e30207f1f72dc91e638dcc7e94 refs/tags/v0.1.0`，其后无更高 tag |
| 最近提交时间 | **2026-11-07 21:07:13 +0530** | `[本机实测]` `git log -1 --format='%ci%n%s'` → `2026-11-07 21:07:13 +0530` / `Merge pull request #1271 from getmaxun/renovate/configure` |
| 提交数 | `--depth 50` 浅克隆，无法给全量历史 | `[本机实测]` `git log --oneline \| Measure-Object -Line` 在浅克隆下返回空统计，故不外推总提交数 |
| 仓库热度 | GitHub REST API 被限流（本机出口 IP 共享），**未能取到 stars/forks** | `[本机实测]` `api.github.com` 返回 `API rate limit exceeded for 108.181.23.123` → 记为 `[尚未验证]`，不编数字 |

**版本判断**：`0.1.0` + 2026-11-07 仍在活跃合并 renovate PR —— **处于 0.x 早期活跃期**，API 与数据结构会变（`maxun-core` 已到 0.0.38，说明核心包迭代密集）。

---

## 2. 许可证

`[源码确认]` `LICENSE:1-2`：

```
                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007
```

`package.json:4` 同时声明 `"license": "AGPL-3.0-or-later"`。

**含义（对集成决策有实质影响）**：AGPL-3.0 是**强著佐权 + 网络条款**。若把 Maxun 作为**独立进程**、仅通过 HTTP/WebSocket 调用（进程边界隔离），通常不触发衍生作品义务；但**一旦把它的代码嵌入本机上货台并对外提供服务/分发**，AGPL 的网络条款会要求提供对应源码。**若要改它的代码再自用，必须按 AGPL 处理**。

---

## 3. 本地部署方式

`[源码确认]` `SETUP.md:14-40` 给出两条路径：

**路径 A · Docker Compose（官方主推）**

```
docker-compose up -d        # SETUP.md:22
前端 http://localhost:5173/   后端 http://localhost:8080/     # SETUP.md:26
```

`[源码确认]` `docker-compose.yml:1-134` 定义 **5 个服务**：

| 服务 | 镜像 | 端口 | 说明 |
|:--|:--|:--|:--|
| `postgres` | `postgres:13` | `${DB_PORT:-5432}` | 主数据库，`healthcheck: pg_isready` |
| `minio` | `minio/minio` | `9000` / 控制台 `9001` | 对象存储，**存 Run 截图** |
| `backend` | `getmaxun/maxun-backend:latest` | `8080` | 依赖 postgres + minio |
| `frontend` | `getmaxun/maxun-frontend:latest` | `5173` | 依赖 backend |
| `browser` | `getmaxun/maxun-browser:latest` | `3001`(WS) / `3002`(health) | **Playwright 浏览器服务**，`cap_add: SYS_ADMIN`、`shm_size: 2gb` |

**路径 B · 不用 Docker** `[源码确认]` `SETUP.md:24-40`：

> 1. Ensure you have **Node.js, PostgreSQL, MinIO and Redis** installed on your system.
> 2. `git clone … && npm install && cd maxun-core && npm install && cd .. && npx playwright install --with-deps chromium && npm run start`

即：**脱离 Docker 是官方支持的**，但**必须自备 4 个外部依赖**：Node.js、PostgreSQL、MinIO、Redis。

---

## 4. Windows 部署条件

| 条件 | 结论 | 依据 |
|:--|:--|:--|
| 是否依赖 Docker | **官方两条路都可**；Docker 是「一条命令」的路径，非唯一 | `[源码确认]` `SETUP.md:14` / `SETUP.md:24` |
| 能否脱离 Docker | **能**，但要自己装 PostgreSQL + MinIO + Redis + Node | `[源码确认]` `SETUP.md:25` |
| PostgreSQL | **硬依赖**，`connect-pg-simple`（session 存储）+ Sequelize 方言 | `[源码确认]` `package.json:31` `"connect-pg-simple": "^10.0.0"`；`.sequelizerc` 存在 |
| MinIO | **硬依赖**（截图与二进制输出） | `[源码确认]` `server/src/storage/mino.ts`（文件名原文拼写即 `mino`）；`docker-compose.yml:19-33` |
| Redis | 用于 Graphile Worker 队列 | `[源码确认]` `server/src/storage/graphileWorker.ts`；`SETUP.md:25` |
| Node 版本 | **无 `engines` 字段**；`@types/node` 锁 `22.7.9`，启动脚本带 `--max-old-space-size=4096` | `[源码确认]` `package.json:22`、`package.json:99`、`package.json:101`（`grep engines` 无命中） |
| 本机现状 | `docker` 命令**不存在**；`node v25.2.1`；`git 2.56.0` | `[本机实测]` `docker --version` → `术语 'docker' 不会被识别…`；`node --version` → `v25.2.1` |

**Windows 特有风险**（`[设计建议]`，基于部署结构推断）：
- `docker-compose.yml` 里 `security_opt: seccomp=unconfined`、`cap_add: SYS_ADMIN`、`shm_size: 2gb`、`/var/run/dbus` 挂载 —— 这些是 **Linux 容器语义**，Docker Desktop on Windows 需 WSL2 后端才成立；
- `docs/generate-env.sh` 是 **bash 脚本**，Windows 原生 PowerShell 下不能直接跑（需 Git Bash）；
- `npm run start` 用的是 `cross-env`，跨平台 OK（`package.json:99`）。

---

## 5. 是否依赖 Docker / 能否脱离

| 问题 | 答案 |
|:--|:--|
| 必须 Docker 吗 | **不必须** `[源码确认]` `SETUP.md:24`「Without Docker」 |
| 不做 Docker 的代价 | 要自己维护 **PostgreSQL 13 + MinIO + Redis + Node** 四套外部服务；`backend` 容器里还设了 `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`，脱离容器后须自行 `npx playwright install chromium`（`SETUP.md:34`） |
| 对「上货台」的意味 | 为一个「浏览器执行器」引入 PG + MinIO + Redis 三套常驻服务，**与本机现有的 SQLite 单机架构（`v3.db`）完全不同代**，运维面显著变大 |

---

## 6. 浏览器引擎

`[源码确认]` 明确是 **Playwright**，不是 Puppeteer（Puppeteer 只以 `puppeteer-extra-plugin-stealth` 插件形式借用其 stealth 插件生态）：

| 证据 | 内容 |
|:--|:--|
| `browser/server.ts:1` | `import { chromium } from 'playwright-extra';` |
| `browser/server.ts:2` | `import stealthPlugin from 'puppeteer-extra-plugin-stealth';` |
| `browser/server.ts:3` | `import type { BrowserServer } from 'playwright';` |
| `browser/server.ts:7` | `chromium.use(stealthPlugin());` |
| `browser/server.ts:20-38` | `chromium.launchServer({ headless: true, args: [...] })` |
| `browser/package.json:4` | `"playwright": "1.57.0"`（精确锁版本） |
| `browser/package.json:5-6` | `playwright-extra ^4.3.6`、`puppeteer-extra-plugin-stealth ^2.11.2` |
| `maxun-core/src/types/workflow.ts:1` | `import { Page } from 'playwright-core';` |
| `server/src/task-runner.ts:14`、`api/record.ts:13`、`browser-management/controller.ts:7` 等 20+ 文件 | 均从 `playwright-core` 导入 `Page` / `BrowserContext` / `CDPSession` |

**关键实现细节** `[源码确认]` `browser/server.ts:20-38`：

```ts
browserServer = await chromium.launchServer({
  headless: true,
  args: ['--disable-blink-features=AutomationControlled', '--disable-web-security',
         '--disable-features=IsolateOrigins,site-per-process', '--no-sandbox',
         '--disable-gpu', '--ignore-certificate-errors', ...],
  port: BROWSER_WS_PORT,
});
```

**这三点对「上货」有实质影响**：
1. **`headless: true` 硬编码** —— 不是可配项（该文件内无环境变量控制 headless）。抖店/微信小店后台对无头浏览器有检测面。
2. **`--disable-web-security`** —— 关闭同源策略，属反检测取向而非生产取向。
3. 通过 **WebSocket（`:3001`）** 暴露 `wsEndpoint`，后端用 `chromium.connectOverCDP`/`connect` 方式接（`server/src/browser-management/browserConnection.ts:84-90`），**不回落到用户本机已登录的浏览器**。

---

## 7. 是否基于 Playwright —— 是

见 §6 的 import 证据行。补充：`maxun-core` 的 `package.json:36` 依赖 `"playwright-core": "1.57.0"`，**精确锁版本**（无 `^`），说明它与 Playwright 内部行为耦合较紧。

---

## 8. 浏览器流程录制

**有。** `[源码确认]`

| 环节 | 位置 |
|:--|:--|
| 启动录制会话 | `server/src/routes/record.ts:55` `router.post('/start', requireSignIn, …)` → `initializeRemoteBrowserForRecording(req.user.id)`（`record.ts:37` GET 版同样存在） |
| 停止录制 | `record.ts:66` `router.get('/stop/:browserId', …)` |
| 录制会话状态 | `record.ts:85` `/active`、`record.ts:96` `/can-create/:state`、`record.ts:108` `/active/url`、`record.ts:123` `/active/tabs` |
| 实时 DOM 流 | `controller.ts:27-60` + `RemoteBrowser.ts:352` `initializeRRWebRecording`（**rrweb** 注入，`RemoteBrowser.ts:368` `page.context().addInitScript(rrwebScriptContent)`） |
| 录制超时 | `controller.ts:16` `RECORDING_TIMEOUT_MS = 10 * 60 * 1000`（10 分钟自动丢弃） |
| 事件通道 | socket.io，`browser-management/inputHandlers.ts:934-948` |

**录制的事件类型**（`[源码确认]` `inputHandlers.ts:934-948`）：

```ts
socket.on("input:keyup", …)            // 键盘
socket.on("input:url", …)              // URL 导航
socket.on("input:refresh", …)
socket.on("input:back", …)  socket.on("input:forward", …)
socket.on("input:date", …)             // 日期选择器
socket.on("input:dropdown", …)         // 下拉
socket.on("input:time", …)  socket.on("input:datetime-local", …)
socket.on("action", …)                 // 自定义动作（scrape 等）
socket.on("removeAction", …)
socket.on("dom:click", …)  socket.on("dom:keypress", …)
```

**步骤怎么存** `[源码确认]`：

- 生成器把每次交互转成 `WhereWhatPair`：`{ where: {...}, what: [{ action, args, name }] }`（`maxun-core/src/types/workflow.ts:47-53`），整体是 `Workflow = WhereWhatPair[]`（`:55`）。
- 写入数据库：`Generator.saveNewWorkflow(fileName, userId, isLogin, robotId, compareRuns)`（`server/src/workflow-management/classes/Generator.ts:1059-1114`）→ 落 `Robot` 模型（`server/src/models/Robot.ts`）。
- **注意 `isLogin` 字段**（`Generator.ts:112`、`:1114`）—— Maxun 把「登录流程」作为一种特殊 robot 类型记录，而不是保存 cookie。

---

## 9. 固定流程回放

**有。** `[源码确认]`

| 环节 | 位置 |
|:--|:--|
| 执行器 | `maxun-core/src/interpret.ts:3224` `public async run(page: Page, params?: ParamType): Promise<void>` |
| 动作分发 | `interpret.ts:2107-2192`：先查 `wawActions`（自定义动作表），否则按 `step.action` 的**点号路径**去 `page` 上取方法并调用 |
| 点号支持 | `interpret.ts:2131-2138`：`const levels = String(step.action).split('.'); … invokee = invokee[level]` —— 支持 `keyboard.press`、`mouse.click` 这类嵌套调用 |
| 后端任务执行 | `server/src/task-runner.ts`（queue worker，`QUEUE_NAMES` 见 `task-runner.ts:727`） |
| 中止 | `interpret.ts:151` `abort()`；HTTP 侧 `server/src/routes/storage.ts:1564` `POST /storage/runs/abort/:id`、`sdk.ts:992` `POST /api/sdk/robots/:id/runs/:runId/abort` |
| 单步间隔 | `interpret.ts:2192` 固定 `setTimeout(res, 500)` |

**关键机制（对稳定性判断很重要）** `[源码确认]` `interpret.ts:2107-2192`：

- 动作失败时**不抛异常**，只 `this.log('Action ... failed', Level.ERROR)` 然后 **`continue`**（`:2180-2185`）。→ 一条流程里某步失败，**整条 run 仍算成功**。
- `click` 有**一次自动降级重试**：首次失败后用 `{ force: true }` 再点一次（`:2166-2172`）。

---

## 10. 支持的 action 清单（真实枚举，不猜）

Maxun 有**三套并行**的动作来源，必须分开说：

### 10.1 录制器真正能产出的动作（用户点鼠标能录下来的）

`[源码确认]` `server/src/workflow-management/classes/Generator.ts`：

| action | 生成位置 | 说明 |
|:--|:--|:--|
| `waitForLoadState` | `:327`、`:341`、`:1524` | 每次录制动作前插一条等待 |
| `fill` | `:373`、`:413`、`:433` | 文本框 / 文本域 / contenteditable |
| `selectOption` | `:393` | **下拉选择** |
| `click` | `:456`、`:464`、`:682`、`:703` | 元素点击 / 坐标点击 |
| `press` | `:490` | 键盘按键 |
| `type` | `:1521` | 逐字输入 |
| `goto` | `:511`、`:726` | 导航 |
| `goBack` / `goForward` | `:1272` / `:1290` | 前进后退 |
| `flag` | `:1030` | 流程标记 |

### 10.2 自定义动作（`CustomActions` 类型，前端按钮触发）

`[源码确认]` `src/shared/types.ts:26`：

```ts
export declare type CustomActions =
  'scrape' | 'scrapeSchema' | 'scroll' | 'screenshot' | 'script'
  | 'enqueueLinks' | 'flag' | 'scrapeList' | 'scrapeListAuto';
```

`maxun-core/src/types/workflow.ts:41` 的 `CustomFunctions` 在此基础上多 `'crawl' | 'search'`。执行表 `interpret.ts:782` `const wawActions: Record<CustomFunctions, …>`。

### 10.3 框架层「任意 Page 方法」

`[源码确认]` `maxun-core/src/preprocessor.ts:35-40`（build 版 `preprocessor.js:44-48`）校验 schema：

```js
what: joi.array().items({ action: joi.string().required(), args: joi.array()... })
```

**只校验 `action` 是字符串，不校验是不是白名单**。执行时 `interpret.ts:2131-2145` 直接在 `page` 上取同名方法调用。

→ **结论：手写 workflow JSON 可以调用任意 Playwright `Page` 方法**（含 `setInputFiles`、`addScriptTag`、`evaluate`），但**录制器 UI 不会产出这些动作**。

### 10.4 录制器的动作类型枚举（`ActionType`，前端用）

`[源码确认]` `server/src/types/index.ts:92-103`：

```ts
export enum ActionType {
  AwaitText = 'awaitText', Click = 'click', DragAndDrop = 'dragAndDrop',
  Screenshot = 'screenshot', Hover = 'hover', Input = 'input',
  Keydown = 'keydown', Load = 'load', Navigate = 'navigate', Scroll = 'scroll',
}
```

**注意**：枚举里有 `Hover` / `DragAndDrop`，但 `Generator.ts` 里 **`grep 'hover|dragAndDrop'` 零命中** —— 即**类型声明存在、录制实现没有**（不在 `onDOMClickAction` / `onDOMKeyboardAction` 等任何一个 handler 内）。

---

## 11. 支持文件上传？——**录制器不支持；框架层可绕过**

| 层面 | 结论 | 证据 |
|:--|:--|:--|
| 录制器/UI | **不提供** | `[本机实测]` 全仓 grep `setInputFiles\|FileChooser\|filechooser`（排除 `node_modules`）**零命中**；`ActionType` 枚举（`types/index.ts:92-103`）无 upload 项；`inputHandlers.ts:934-948` 无 file 事件 |
| `CustomActions` | **不提供** | `[源码确认]` `src/shared/types.ts:26` 9 个动作里无 upload |
| 框架层（手写 workflow） | **能调用**（因为 action 只校验是字符串） | `[源码确认]` `maxun-core/src/preprocessor.ts:35-40`；`[本机实测]` 手写 `{action:'setInputFiles', args:['#file', 路径]}` 后，执行日志出现 `Action setInputFiles failed: Timeout 4000ms exceeded … waiting for locator('#file')` → **证明它确实进到了 `page.setInputFiles` 调用路径**（不是被白名单拦掉） |

**对上货的意味**：主图/详情图/视频上传是这个需求的核心写入动作。Maxun **要自己手写 workflow JSON 才能调 `setInputFiles`**，而这恰好等于「放弃它的录制/可视化优势，退化成手写 Playwright 脚本」—— 那还不如直接用 Playwright。

**额外限制**（`[设计建议]`）：抖店/微信小店的图片上传多为**点击触发 `<input type=file>` 的隐藏控件**，`setInputFiles` 对隐藏 input 可行，但**对「点击后弹出系统文件选择器」的实现无效**（Playwright 需 `page.on('filechooser')`，而 Maxun **无任何 filechooser 代码**）。

---

## 12. 登录状态持久化

| 问题 | 结论 | 证据 |
|:--|:--|:--|
| 有没有 `storageState`（Playwright 官方登录态序列化） | **没有** | `[本机实测]` 全仓 grep `storageState` **零命中** |
| 浏览器 profile 复用 | **没有 userDataDir / persistent context** | `[本机实测]` grep `userDataDir` 零命中；`RemoteBrowser.ts:581` 每次 `this.browser.newContext(contextOptions)` 新建临时 context |
| Cookie 怎么处理 | **只读，不写**：`where.cookies` 是**匹配条件**（判断页面是否处于某登录态），不是注入 | `[源码确认]` `maxun-core/src/types/workflow.ts:18` `'cookies': Record<string, RegexableString>`；`interpret.ts:279` `cookies: (await page.context().cookies([page.url()]))` —— 只 `cookies()` 读取 |
| 有没有「登录」概念 | 有 **`isLogin` 标记**，把登录流程当成一种 robot 类型 | `[源码确认]` `Generator.ts:112` `isLogin?: boolean`；`Generator.ts:1114` 写入 |
| 长会话靠什么 | 靠**同一个 BrowserContext 存活期间**的 cookie（`BrowserPool` 保持 instance 存活）+ 10 分钟录制超时 | `[源码确认]` `controller.ts:16` |

**结论**：**没有可持久化、可跨进程复用的登录态机制**。一次 run 结束、context 关闭即丢；下次 run 需要 workflow 里重新走登录流程（而登录流程会遇验证码/扫码）。这是「上货」场景的**硬伤**，也解释了为什么 Maxun 更适合抓公开数据。

---

## 13. 多账号隔离

| 问题 | 结论 | 证据 |
|:--|:--|:--|
| 有没有「多账号 profile」 | **没有按账号设计**；隔离维度是 **`userId`（平台用户）+ 状态（recording/run）** | `[源码确认]` `BrowserPool.ts:10` `type BrowserState = "recording" \| "run"`；`controller.ts:252-266` `getActiveBrowserIdByState(userId, state)`、`canCreateBrowserInState(userId, state)` |
| 每个用户几个浏览器 | 每个 `userId` 在每种状态下**只有 1 个活跃实例**（`getActiveBrowserId` 单值返回） | `[源码确认]` `controller.ts:241-242` |
| 不同账号的 cookie 会串吗 | **不会串，但也不持久** —— 每次 `newContext()` 全新，代价是**每次都要重新登录** | `[源码确认]` `RemoteBrowser.ts:555-587` `contextOptions` + `browser.newContext(contextOptions)` |
| 有没有代理隔离 | **有**，且支持**粘性代理会话** | `[源码确认]` `RemoteBrowser.ts:17` `expandSessionPlaceholder, newProxySessionId`；`:46-51` `proxySessionId`（context 重建时复用，保证出口 IP 稳定）；`:572` `contextOptions.proxy = {...}`；HTTP 配置接口 `routes/proxy.ts:15` `POST /proxy/config`、`:61` `GET /proxy/test` |
| 有没有指纹伪装 | **有** | `[源码确认]` `RemoteBrowser.ts:490-497` `applyEnhancedFingerprinting(context)` → `fingerprintInjector.attachFingerprintToPlaywright(context, fingerprint)` |

**结论**：Maxun 的「多账号」= **多平台用户（多套部署账号）**，不是「同一平台多店铺账号轮换」。要做「抖店 A 店 / B 店 / 微信小店」并行，得自己造「每个店铺一个 BrowserContext + 一份持久 cookie」，而它**没有现成槽位**。

---

## 14. 截图（含失败截图）

| 能力 | 结论 | 证据 |
|:--|:--|:--|
| 流程内主动截图 | **有**，`screenshot` 动作，支持 `fullPage` / `clip` / `mask` / `quality` 等 Playwright 全参数 | `[源码确认]` `interpret.ts:782-812` `wawActions.screenshot`；`src/shared/types.ts:5-24` `ScreenshotSettings` |
| 抓取时顺带截图 | **有**，output format `screenshot-visible` / `screenshot-fullpage` | `[源码确认]` `interpret.ts:441-488`；`mcp-worker.ts:16` `OUTPUT_FORMATS` 含两项 |
| 截图落哪 | **MinIO 对象存储**，run 记录里存 key | `[源码确认]` `task-runner.ts:348` `new BinaryOutputService('maxun-run-screenshots')`；`record.ts:456` `formattedRun.screenshots.push(run.binaryOutput[key])` |
| **失败时自动截图** | **不提供** | `[本机实测]` ① 全仓 grep 只在 output-format 与显式 `screenshot` 动作处出现；② 隔离实测：故意点不存在的 `#no-such-button`，run **正常返回不抛异常**，**只在 debug 日志留一条** `Click action failed: page.click: Timeout 4500ms exceeded`；截图 `shot-after-fail`（4253 B）是**我在 workflow 里显式写的 `screenshot` 动作**产出的，不是框架自动拍的 |

**结论**：想做「失败必留现场图」，得自己在 workflow 末尾/每个关键步骤后插 `screenshot`，并**自己判断成功与否**（因为框架不置失败态）。这在「上货提交」场景是必要的安全网，而 Maxun 不给。

---

## 15. DOM 记录（是否保存 DOM/HTML 快照）

| 能力 | 结论 | 证据 |
|:--|:--|:--|
| 录制期实时 DOM 流 | **有**（rrweb） | `[源码确认]` `RemoteBrowser.ts:352` `initializeRRWebRecording(page)`；`:368` `addInitScript(rrwebScriptContent)`；`:110` `isDOMStreamingActive`；`:114` 注释「robot run mode, rrweb is skipped」 |
| 是否**落盘**成 DOM 快照文件 | **未确认**（内存流为主，落库路径未见） | `[尚未验证]` 未在 `server/src` 找到「把 rrweb 事件持久化到 DB/磁盘」的写入点；`docs/` 无说明 |
| 抓取结果是否含 HTML | **有**，output format `html` | `[源码确认]` `markdownify/scrape.ts` 导出 `convertPageToHTML`；`mcp-worker.ts:16` |

**结论**：录制期的 DOM 是**实时流**（给前端编辑器画页面用），不是「可回放的 DOM 快照档案」。要「页面改版后回看当时的 HTML」，只能用 `html` 输出格式在 run 时抓。

---

## 16. API 启动任务

**有。两套（都要鉴权）。** `[源码确认]`

| 用途 | 路由 | 鉴权 | 位置 |
|:--|:--|:--|:--|
| 网页版触发 run | `POST /api/robots/:id/runs` | `requireAPIKey` | `server/src/routes/record.ts:1579` |
| SDK 触发 run | `POST /api/sdk/robots/:id/execute` | `requireAPIKey` | `server/src/routes/sdk.ts:673` |
| 创建 robot（含 workflow） | `POST /api/sdk/robots` | `requireAPIKey` | `sdk.ts:125` |
| 更新 robot（含 workflow） | `PUT /api/sdk/robots/:id` | `requireAPIKey` | `sdk.ts:365` |
| 抓单页 / 爬站 / 搜索 / LLM 抽取 | `POST /api/sdk/crawl`、`/sdk/search`、`/sdk/extract/llm` | `requireAPIKey` | `sdk.ts:1175` / `:1323` / `:1448` |
| 文档抽取（**文件上传端点**） | `POST /api/sdk/robots/document`、`…/document-parse` | `requireAPIKey` + `multer.single('file')` | `sdk.ts:1603` / `:1677` |

**注意**：`sdk.ts:1603` 的 `multer` 上传是**把文件传给 Maxun 做文档解析**，不是「让浏览器上传文件到目标网站」——两件事，不要混。

挂载总览 `[源码确认]` `server.ts:125-137`：`/webhook`、`/record`、`/workflow`、`/storage`、`/auth`、`/proxy`、`/api-docs`、`/api`。

---

## 17. 任务状态查询

**有。** `[源码确认]`

| 用途 | 路由 | 位置 |
|:--|:--|:--|
| run 列表 | `GET /api/robots/:id/runs` | `record.ts:355` |
| **单次 run 状态/结果** | `GET /api/robots/:id/runs/:runId` | `record.ts:528` |
| SDK 版 | `GET /api/sdk/robots/:id/runs`、`…/runs/:runId` | `sdk.ts:835` / `:874` |
| **两次 run 差异** | `GET /api/sdk/robots/:id/runs/:runId/diff`；`GET /api/storage/runs/:id/diff` | `sdk.ts:921`；`storage.ts:1284` |
| 全局 run 列表 | `GET /api/storage/runs` | `storage.ts:1041` |
| 中止 | `POST /api/storage/runs/abort/:id` | `storage.ts:1564` |

---

## 18. 失败重试

| 层 | 有没有 | 证据 |
|:--|:--|:--|
| run 级重试计数 | **有（上限 3）** | `[源码确认]` `record.ts:765-771`：`const retryCount = plainRun.retryCount \|\| 0; if (retryCount >= 3) … 'Max retries exceeded (3/3) - Run permanently failed'` |
| 定时任务重试 | **有（`maxAttempts: 6`）** | `[源码确认]` `schedule-worker.ts:135` `addJob(..., { maxAttempts: 6 })` |
| 浏览器连接重试 | **有（3 次 / 2s 间隔）** | `[源码确认]` `browserConnection.ts:6-10` `maxRetries: 3, retryDelay: 2000` |
| **动作级重试（可配置）** | **不提供可配置项**；只有硬编码行为：① `click` 失败后 `{force:true}` 再点一次 ② workflow 的 `maxRepeats` 让整段 where 条件块重复（`interpret.ts` `maxRepeats: 5` 默认） | `[源码确认]` `interpret.ts:2166-2172`；`interpret.ts:113` `maxRepeats: 5` |
| 失败是否让 run 变 failed | **不**（动作失败只 `continue`） | `[源码确认]` `interpret.ts:2180-2185` |

**结论**：有「任务重跑」，**没有「步骤重试策略」**；且**动作失败不置失败态** —— 对上货提交这种「错一步就是脏数据」的流程，这是危险默认值。

---

## 19. 计划任务

**有。** `[源码确认]`

| 环节 | 位置 |
|:--|:--|
| 数据结构 | `Robot.schedule` 内 `cronExpression` / `timezone` / `nextRunAt` → `schedule-worker.ts:45-48` 用 `Sequelize.literal("schedule->>'cronExpression' IS NOT NULL")` 与 `nextRunAt <= now` 取到期任务 |
| 计算下次 | `schedule-worker.ts:92` `computeNextRun(cronExpression, timezone)`（`server/src/utils/schedule.ts`，依赖 `cron-parser`） |
| 轮询 | `schedule-worker.ts:14-17`：每 `30s` 一批、批量 `10`、抢锁 10 分钟、advisory lock `43821742` |
| HTTP 管理 | `POST /api/robots/:id/schedule`（`record.ts:1843`）、`GET`（`:1952`）、`DELETE`（`:2012`）；另一套 `PUT /api/storage/schedule/:id`（`storage.ts:1423`）、`GET`（`:1499`）、`DELETE`（`:1518`） |
| 依赖 | `package.json:66` `node-cron ^3.0.3`（前端展示用），后端真正调度靠 DB + Graphile Worker |

---

## 20. 外部 AI 调用

**有，且是同一条能力主线。** `[源码确认]`

| 项 | 内容 |
|:--|:--|
| 支持的 provider | `anthropic` / `openai` / `ollama` 三家（`server/src/models/Robot.ts:21` `promptLlmProvider?: 'anthropic' \| 'openai' \| 'ollama'`） |
| 默认 | `ollama`（`task-runner.ts:292` `recording_meta.promptLlmProvider \|\| 'ollama'`） |
| SDK | `package.json:5` `@anthropic-ai/sdk ^0.71.2` |
| 代码位置 | `server/src/sdk/aiExtract/`（`pipeline.ts` / `llmClient.ts` / `pageScope.ts` / `pagination.ts` / `reveal.ts` / `spec.ts` / `target.ts`）、`server/src/sdk/browserAgent.ts`、`anthropicTools.ts`、`anthropicToolHelper.ts`、`utils/llm-endpoint.ts`、`utils/llm-models.ts`、`utils/llm-config-validation.ts` |
| 环境变量 | `MAXUN_LLM_PROVIDER` / `MAXUN_LLM_MODEL` / `MAXUN_LLM_API_KEY` / `MAXUN_LLM_BASE_URL`（`mcp-worker.ts:124-127`、`:343`）；`OLLAMA_BASE_URL` / `OLLAMA_API_KEY` / `OLLAMA_MODEL`（`ENVEXAMPLE`） |
| 用途**是什么** | **「AI 抽取」**：自然语言描述「我要哪些字段」→ LLM 帮你定位并取数（`docs` 称 AI Mode / Agent）。`aiExtract/reveal.ts:66` 的 `DISMISS` 正则是**关掉 cookie 弹窗**的正则 |
| 用途**不是什么** | **不是**「LLM 决定下一步点哪里」的自主 agent 执行流。`browserAgent.ts` 属 `sdk/` 下的抽取代理，不是 `interpret.ts` 回放链路上的决策器 |

**对上货的意味**：Maxun 的 AI 是**「看懂页面取什么数据」**，不是**「替你做业务判断」**。与主人「AI 只处理异常判断」的原则方向一致，但它作用于**抓取**，不作用于**提交**。

---

## 21. 自定义代码 / 脚本扩展

| 方式 | 能力 | 证据 |
|:--|:--|:--|
| `script` 自定义动作 | workflow 里可执行任意浏览器侧 JS | `[源码确认]` `src/shared/types.ts:26` 含 `'script'`；`workflow.ts:41`；执行表 `interpret.ts:782` |
| 手写 `page` 方法 | 任意 Playwright Page 方法名（含点号嵌套） | `[源码确认]` `interpret.ts:2131-2145` |
| 浏览器侧注入脚本 | `maxun-core/build/browserSide/scraper.js`（62,915 B），提供 `window.scrape` / `scrapeSchema` / `scrapeList` / `scrapeListAuto` / `scrollDown` / `scrollUp` | `[源码确认]` `interpret.ts:2717-2750` `ensureScriptsLoaded`；`:2729` 用 `page.addInitScript({path})` |
| 直接改源码 | 可以（AGPL，自建自用），但见 §2 的许可义务 | `[源码确认]` `LICENSE:1-2` |

---

## 22. 流程导出 / 版本管理

| 能力 | 结论 | 证据 |
|:--|:--|:--|
| 复制流程（克隆为新 robot） | **有** | `[源码确认]` `POST /api/robots/:id/duplicate`（`record.ts:1652`）、`POST /api/storage/recordings/:id/duplicate`（`storage.ts:921`）、SDK 版 `sdk.ts:1053` |
| 版本对比（两次 run 结果 diff） | **有** | `[源码确认]` `storage.ts:1284` `GET /storage/runs/:id/diff`；`utils/run-comparison.ts`；`diff ^9.0.0`（`package.json`） |
| **导出/导入 .json 流程文件** | **未见专用端点** | `[尚未验证]` 全仓 grep `export.*workflow\|import.*workflow` 只命中 `index.ts` 的 `export *` 语句（ES module 再导出，不是功能）；**不能据此断言「一定没有」** —— 可能在前端（`src/`）用 `Blob` 下载实现，本轮未审前端 |
| 变更通知 | **有 webhook** | `[源码确认]` `routes/webhook.ts:51` `/webhook/add`、`:113` `/update`、`:219` `/list/:robotId`、`:250` `/test` |

**结论**：**「同一流程的多次运行结果对比」有；「流程定义本身的可导出文件格式」未证实**（前端未审）。对本项目的意味：流程没有「版本化存档」的现成机制，改错难回滚。

---

## 23. 四级能力等级判定（不许把抓取工具当完整 RPA）

| 等级 | Maxun 是否具备 | 判定依据 |
|:--|:--|:--|
| **① 网页数据抓取** | ✅ **强项**。`scrape` / `scrapeSchema` / `scrapeList` / `scrapeListAuto` / `crawl` / `search` / markdown / html / links / 截图 / 文档解析 | `src/shared/types.ts:26`；`maxun-core/src/types/workflow.ts:41`；`server/src/markdownify/`、`sdk/aiExtract/` |
| **② 网页浏览器操作** | 🟡 **有框架，实测有坑**。录制器能产 click/fill/selectOption/press/type/goto/back/forward；**无 hover/drag 实现**；隔离实测中 `goto` 之后的交互全部超时（§24.2） | `Generator.ts:373-1524`；`types/index.ts:92-103` vs `Generator.ts` 无 hover |
| **③ 表单提交** | 🟡 **技术上可以**（click 提交按钮），但**无提交语义、无幂等、失败不置错、无二次确认机制** | `interpret.ts:2166-2185`（失败 continue）；无 transaction/verify 概念 |
| **④ 文件上传** | ❌ **录制器不提供，UI 不提供**；只能手写 workflow 调 `setInputFiles`，且**无 filechooser 处理**（无法应付「点击弹系统文件框」的站点） | §11 |
| **⑤ 复杂业务流程** | ❌ **不具备**。无状态机、无任务依赖、无补偿/回滚、无「成功判据」定义、无跨账号会话编排 | 全仓无 `state machine` / `compensat` / `rollback` 相关实现；`v3_task_log` 类比在 Maxun 侧对应 `Run` 表（单次记录，无编排） |

**一句话**：**它是一台「带浏览器的采集机」，不是「业务流程机器人」。**

---

## 24. 是否适合「商品后台写入 + 提交」

### 24.1 结论

**不适合作为主执行器。** 理由按权重排序：

| # | 硬伤 | 后果 |
|:--|:--|:--|
| 1 | **没有文件上传的录制能力**（§11） | 上货的核心动作（主图/详情图/视频）录不下来，等于弃用它的核心优势 |
| 2 | **没有登录态持久化**（§12，无 `storageState`、无 `userDataDir`） | 每次 run 都要重新登录抖店/微信小店；遇扫码/验证码即断链 |
| 3 | **动作失败不置失败态**（§9、§18：`continue` 而非 throw） | 「提交点了一半」会被判成成功，产生脏数据；这与「上货不能出错」直接冲突 |
| 4 | **没有失败自动截图**（§14） | 出错无现场证据，排查成本转嫁给人 |
| 5 | **headless 硬编码 + 反检测启动参数**（§6） | 平台风控面变大，且无法切「可见浏览器让人工接管」 |
| 6 | **依赖 PG + MinIO + Redis**（§3-§5） | 与本机 SQLite 单机架构不同代，为一个执行器引入三套常驻服务 |
| 7 | **AGPL-3.0**（§2） | 深度集成/改造后有源码开放义务 |
| 8 | **0.1.0 早期版本**（§1） | API 与数据结构仍在变 |

### 24.2 唯一有价值的用法（如果要用）

**只把它当「竞争对手/参考实现」，或只用于「公开页面采集」这一件事**（例如采集同行商品页做比价、采集平台公告），**不用于向平台写入**。若要写入，用 Playwright 原生（详见《AI运营中心-浏览器自动化对比.md》）。

---

## 25. 隔离实测记录（逐项）

**环境**：`D:\DSH_Cache\downloads\maxun-test\`
`node v25.2.1`、`playwright 1.57.0`（`PLAYWRIGHT_BROWSERS_PATH=D:\...\pw-browsers`，Chromium Headless Shell 143.0.7499.4 build v1200）、`maxun-core 0.0.38`（npm 包，与 Maxun 后端同一执行器）
测试页：`testpage.html`（本地静态页，含文本框 `#name`、下拉 `#sel`、按钮 `#btn`、提交按钮 `#post`、文件框 `#file`）
测试脚本：`run-test.cjs` / `run-test-02.cjs` / `run-test-03.cjs` / `run-test-04.cjs` / `run-test-05.cjs`
结果文件：`result-round-01.json` … `result-round-05.json`
**全程未连接任何真实电商后台。**

### 25.1 Docker 起 Maxun 全栈 —— **未做成**

**卡在哪**：`[本机实测]` `docker --version` → `术语 'docker' 不会被识别为 cmdlet、函数、脚本文件或可执行程序的名称`。**本机无 Docker**，`docker-compose.yml` 全部 5 个服务都起不来；不用 Docker 的路子又缺 PostgreSQL / MinIO / Redis 三套服务（本机未安装）。

→ 记为**未做**，不假装跑过。

### 25.2 已做成的项（直接用 npm 包 `maxun-core` 驱动真实 Playwright）

| # | 验证项 | 结果 | 证据 |
|:--|:--|:--|:--|
| 1 | 打开页面 | ✅ 成功。`title=Maxun Isolated Test Page`、`url=/testpage.html`、582ms | `result-round-02.json` `A_goto_only` |
| 2 | 点击按钮 | ❌ `page.click('#btn')` 4000ms 超时；页面 DOM 里 `#btn` 存在且 `rect 72.3×21`、`visibility: visible`、`pointer-events: auto` | `result-round-02.json` `B_click_button`、`run-test-03` `M1/M2/M3` |
| 3 | 填表单 | ❌ `page.fill('#name', …)` 超时，`#name.value` 仍为 `initial` | `result-round-02` `D_fill_input` |
| 4 | 选下拉 | ❌ `page.selectOption('#sel','c')` 超时，`#sel.value` 仍为 `a` | `result-round-02` `E_select_option` |
| 5 | 上传本地文件 | ❌ `setInputFiles` 超时（但**进入了调用路径**，见 §11） | `result-round-02` `F_set_input_files` |
| 6 | 保存页面数据（scrape） | ❌ `window.scrape is not a function`，重试到上限后整条 run 抛 `Action scrape exceeded max retries (2)` | `result-round-01` `01-workflow-run-ok`；`result-round-02` `I_scrape_action` |
| 7 | 跑固定流程 | 🟡 流程能「跑完」（不抛错），但**每一步都没生效** | `result-round-05` 三组均为 `out: NOT-CLICKED / name: initial / sel: a / files: 0`，`failures` 列出 4 条超时 |
| 8 | 造不存在的按钮 → 失败截图与日志 | ✅ 行为已确认：**不抛异常**、debug 日志留 `Click action failed: page.click: Timeout 4500ms exceeded`、截图需 workflow 显式写 `screenshot`（产出 4253 B PNG） | `result-round-02` `H_missing_button`；`result-round-01` `05-fail-path` |
| 9 | 从外部 API 触发运行 | ❌ **未做**：需要 Maxun backend（Express + PG + MinIO），本机无 Docker/无 PG，起不来（同 §25.1） | — |

### 25.3 定位过程（诚实记录，含被推翻的假设）

| 假设 | 验证方式 | 结果 |
|:--|:--|:--|
| 「是 headless 浏览器不让点」 | 同一浏览器用**原生 Playwright** 跑同一套操作 | ❌ 假设否定：`N1_native_playwright` **全部成功**（click→`CLICKED-OK`、fill→`NativeFillOK`、select→`c`、上传→`files:1 / upload-target.txt`），**131ms** |
| 「是 maxun 注入的 `scraper.js` 破坏页面」 | 手动 `addInitScript(scraper.js)` 后再点 | ❌ 假设否定：元素仍可见可点、点击成功（`B_scraper_injected` → `CLICKED-OK`） |
| 「是 `where` 条件不匹配导致 `goto` 被反复重放」 | 三种 `where` 变体（空 / `url` / `$before`）分别跑 | ❌ 假设否定：三者行为一致（`mainFrameNavigations: 1`，只导航一次），仍全失败 |
| 「是 page 上下文丢失」 | 先由测试脚本 `goto`，再让 maxun 只跑 click（workflow 不含 `goto`） | ✅ **唯一成功组合**：`A_pre_goto_then_maxun_click` → `CLICKED-OK`，618ms |

**最终判定**：`maxun-core 0.0.38` 在**自行执行 `goto` 动作之后**，后续交互动作全部 `waiting for locator` 超时；而**同一页面由外部先导航、maxun 只做交互**时正常。根因**未定位到具体一行**（已排除 scraper 注入、where 重放、headless）。**这可能与「后端 `RemoteBrowser` 里 page 的构造方式（含 rrweb/CDP/init 脚本注入了额外上下文）」有关**，但**未验证**，故标注 `[尚未验证]`。

**对结论的影响**：不能据此断言「Maxun 产品不能点击」——它的后端有自己的 page 装配路径；但**可以确定**「把 `maxun-core` 当独立执行库、脱离它的后端来用，是不可靠的」。

---

## 26. 对上货台 7.0 的直接建议

`[设计建议]`

1. **不要把 Maxun 接入生产执行链路**（写入/提交），理由见 §24.1 的 8 条硬伤。
2. **可以借鉴它的三个设计**（不引代码）：
   - 「`Where-What` 条件对」结构：把「页面处于什么状态」和「该做什么」显式分开，比线性脚本抗页面变化；
   - 「录制 → 生成步骤 JSON → 回放」这条产品形态（与主人「确定性固定脚本」原则一致）；
   - 「截图 + run diff」作为变更监控（`storage.ts:1284`）。
3. **如果要抓公开数据**（同行商品页、平台公告），Maxun 可以单独容器化跑，**与上货台只做单向数据出口**，避免 AGPL 渗透与依赖污染。
4. **它明确不能替代的东西**：登录态保持、文件上传、失败判定、失败取证。这四件必须自己做。

---

## 附录 A · 关键文件行号速查

| 内容 | 位置 |
|:--|:--|
| 版本 | `package.json:3`（0.1.0）；`maxun-core/package.json:3`（0.0.38）；`browser/package.json:3`（1.0.0） |
| 许可证 | `LICENSE:1-2`；`package.json:4` |
| 部署（Docker） | `SETUP.md:14-26`；`docker-compose.yml:1-134` |
| 部署（无 Docker） | `SETUP.md:24-40` |
| Playwright | `browser/server.ts:1-7,20-38`；`browser/package.json:4` |
| 录制入口 | `server/src/routes/record.ts:37,55,66,85,96,108,123` |
| 录制事件 | `server/src/browser-management/inputHandlers.ts:934-948` |
| 录制超时 | `server/src/browser-management/controller.ts:16` |
| 动作生成 | `server/src/workflow-management/classes/Generator.ts:327-1524` |
| 自定义动作枚举 | `src/shared/types.ts:26`；`maxun-core/src/types/workflow.ts:41` |
| 前端动作类型 | `server/src/types/index.ts:92-103` |
| 执行器 | `maxun-core/src/interpret.ts:3224`（run）、`:2107-2192`（分发）、`:2166-2172`（click force 重试）、`:2180-2185`（失败 continue） |
| 校验（不查 action 白名单） | `maxun-core/src/preprocessor.ts:35-40` |
| 注入脚本 | `maxun-core/src/interpret.ts:2717-2750`；`build/browserSide/scraper.js`（62,915 B） |
| 登录态 | 无 `storageState`/`userDataDir`；`RemoteBrowser.ts:581` `newContext`；`interpret.ts:279` 只读 cookies |
| 多用户隔离 | `BrowserPool.ts:10`；`controller.ts:241-266` |
| 代理/指纹 | `RemoteBrowser.ts:17,46-51,490-497,572`；`routes/proxy.ts:15,61` |
| 截图 | `interpret.ts:782-812`；`task-runner.ts:348`；`record.ts:456` |
| DOM 流 | `RemoteBrowser.ts:110,114,352,368` |
| API 触发 | `record.ts:1579`；`sdk.ts:673` |
| 状态查询 | `record.ts:528`；`sdk.ts:874`；`storage.ts:1041` |
| 重试 | `record.ts:765-771`；`schedule-worker.ts:135`；`browserConnection.ts:6-10` |
| 计划任务 | `schedule-worker.ts:14-17,45-48,92`；`record.ts:1843,1952,2012` |
| LLM | `Robot.ts:21`；`task-runner.ts:292`；`server/src/sdk/aiExtract/*`；`ENVEXAMPLE` |
| 复制流程 | `record.ts:1652`；`storage.ts:921`；`sdk.ts:1053` |
| run diff | `storage.ts:1284`；`sdk.ts:921`；`utils/run-comparison.ts` |

## 附录 B · 复现命令

```powershell
# 1) 取源码
git clone --depth 50 https://github.com/getmaxun/maxun.git D:\DSH_Cache\downloads\maxun
git -C D:\DSH_Cache\downloads\maxun log -1 --format='%H%n%ci%n%s'
git -C D:\DSH_Cache\downloads\maxun ls-remote --tags origin

# 2) 装隔离测试环境（不动 C 盘）
$t='D:\DSH_Cache\downloads\maxun-test'; cd $t; npm init -y
npm install maxun-core@latest playwright@1.57.0
$env:PLAYWRIGHT_BROWSERS_PATH="$t\pw-browsers"; npx playwright install chromium

# 3) 跑逐项验证
node run-test-02.cjs   # 8 项单动作
node run-test-03.cjs   # 原生 Playwright vs maxun 选择器对照
node run-test-04.cjs   # scraper.js 注入 / 预导航 归因
node run-test-05.cjs   # where 条件重放归因
```

## 附录 C · 未完成与未验证清单（如实）

| 项 | 状态 | 原因 |
|:--|:--|:--|
| Maxun 全栈启动（Docker） | ❌ 未做 | 本机无 Docker |
| Maxun 全栈启动（无 Docker） | ❌ 未做 | 缺 PostgreSQL / MinIO / Redis |
| 从外部 API 触发 run | ❌ 未做 | 依赖 backend，未起 |
| 真实录制器 UI 操作 | ❌ 未做 | 依赖 frontend + backend，未起 |
| `maxun-core` goto 后交互超时的根因具体行 | `[尚未验证]` | 已排除 3 个假设，未继续深挖（诊断时限） |
| 流程 .json 导出/导入 | `[尚未验证]` | 前端 `src/` 未审 |
| rrweb DOM 是否落盘 | `[尚未验证]` | 未找到持久化写入点 |
| stars / forks / release 明细 | `[尚未验证]` | GitHub API 限流 |
