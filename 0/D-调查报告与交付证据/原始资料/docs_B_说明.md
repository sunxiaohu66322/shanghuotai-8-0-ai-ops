
---

## v0.1.3 · 拟人输入（2026-10-22）

主人原话：「最好是打开主页网页，然后剪切板粘贴关键词再去搜索。然后滚动，下一页的话也跳个什么东西，模拟人工点击，不要直接发命令」

### 三处都换成真实输入事件

| 环节 | 旧（发命令） | 新（像人） |
|:--|:--|:--|
| 搜索 | 直接把网址改成 `s.taobao.com/search?q=…` | 打开 `www.taobao.com` → 点搜索框 → 粘贴关键词 → 点搜索按钮（找不到按钮才按回车） |
| 滚动 | `window.scrollTo(0, 文档高度)` 一跳到底 | 真鼠标滚轮：一小段一小段推，中间停顿，偶尔回滚一点 |
| 下一页 | `button.click()` 脚本触发 | 读按钮坐标 → 鼠标分 7~14 段移过去 → 悬停 → 按下抬起 |

**为什么必须换**：`element.click()` 和 `scrollTo()` 产生的事件 `isTrusted=false`，是"页内脚本执行"；真鼠标/滚轮产生的事件 `isTrusted=true`，就是人在操作。淘宝风控看的就是这个差别。

### 失败会自动降级（并且记账）

拟人搜索走不通（首页改版、找不到搜索框）时，自动退回"直接打搜索页"，并在 `stats.nav_warn` 里写明：

> 拟人搜索没走通（原因），已降级为直接打搜索页 —— 这一次不是「像人一样」搜的，风控风险更高

翻页同理：真鼠标点不动时才退回脚本点击，`page_log[].via` 会写 `mouse` / `js-fallback(原因)`。

### 开关

后端 `collect(human=True)` 默认开启；传 `human=False` 可完全退回旧写法（旧代码一行没删）。
每轮结果里 `stats.input_mode` 会写 `mouse/wheel` 或 `js`，`stats.nav_log` 是那五步的逐条结果。

### 离线自测（不碰淘宝）

```
F:\pyenvs\douyin\Scripts\python.exe _验_拟人输入.py
```
它开一个本地 `file://` 模拟页，验证 16 项：真点击 / 粘贴 / 滚轮 / trusted 标记 /
`home_search` 五步全流程 / `click_next` 点完页码真变。**全过才算改对。**

---

## v0.1.4 · 打开主页先关掉广告浮层（2026-10-22）

主人原话：

> 「淘宝唯一一个风险就是打开主页的时候，它有一个弹窗，就是覆盖前端的一个广告页。
>   你这边要去找到那个叉，把它关掉。……搜索栏永远是在那个位置固定的，
>   就是前端会弹出广告页，可能会遮挡。这个时候就要想办法找到那个叉，把它关了」

### 怎么认「这是浮层广告」

三层判据，缺一层都不算：

1. `position` 是 `fixed` / `absolute`，且 `z-index >= 100`
2. 占屏面积 ≥ 10%，且在视口内可见
3. 父子里套的一串只算一个（只留最外层）

### 怎么认「那个是叉」

按分数挑，不按固定选择器（淘宝改版就得改选择器，分数法能扛）：

| 特征 | 加分 |
|:--|--:|
| 文字是 `× ✕ ✖ x X ╳ ✗` 或 `关闭/close` | +6 |
| `aria-label` 含「关闭 / close」 | +6 |
| `class`/`id` 含 `close / 关闭 / icon-close / dialog-close / popup-close` | +5 |
| `title` 含「关闭 / close」 | +5 |
| 位于浮层右上角区域 | +3 |
| 是 `a / button / i / span` 或 `role=button` | +1 |
| 一段长文字（多半是广告正文，不是叉） | **-4** |

**尺寸门槛**：10~72 px 之间、面积 ≥ 90 px² —— 太大的是内容区，太小的点不着。

**防误点**：**分数不到最高分一半的候选一律不点**。页面里常见的诱饵（广告正文中的一个 `x`、
"全场 ×2 倍积分"里的 ×）分数低，点它等于替淘宝点广告。

### 点完怎么算「真关掉了」

**不能只看总面积** —— 淘宝常常「关掉一个又弹一个」，总面积没怎么变，但刚才那个确实没了。

所以逐浮层比对几何位置：点之前记下那个浮层的 `x/y/w/h`，点完重新扫，
**那个位置（±8px、尺寸±14px）上已经没有浮层了，才算关掉**；然后继续关剩下的。

最多 4 轮（对应「连弹好几个」）。

### 还有两道保险

1. **搜索框被盖住就再关一次**（有的广告是延迟弹出来的）。判据：
   `document.elementFromPoint(搜索框中心)` 返回的不是搜索框自己。
2. **关键词没粘进框就重来一次**。点框时若被浮层吃掉，粘贴会落到空处 ——
   这时读回 `input.value`，不包含关键词就再关浮层、再点、再粘一次；
   两次都不行就**如实报错停下**，而不是带着空词去搜。

### 跨域 iframe 挡在前面会如实报

`frames` 字段会列出占屏 >15% 的 iframe。那种浮层从主文档点不到，
程序会把它写到 `rounds[].frames` 里，不会假装成功。

### 离线自测（不碰淘宝）

```
F:\pyenvs\douyin\Scripts\python.exe _验_浮层广告.py
```

本地模拟页 `humanize_overlay.html` 里故意放了三样东西：

- 一个盖住搜索框的广告浮层（带 `×` 关闭、`aria-label="关闭"`）
- **关掉第一个之后才弹出第二个**（模拟淘宝"关一个弹一个"）
- 一个**诱饵**：广告正文里的假 `x`（点了会记账 `window.__wrong++`）

验 **16 项**：认出浮层 / 认出搜索框被盖 / 找到叉且位置在右上角 /
两个浮层都关掉 / **诱饵一次都没被点** / 关完不误报 / `home_search` 全流程出 40 张卡 /
点叉用的是 `trusted` 真鼠标 / 干净页不误报。

**全过才算改对。**

---

## v0.1.5（2026-10-22）

主人这一轮的四个要求：关悬浮窗 / 用 AdGuard 屏蔽浮层 / 做日志系统 / 点击要在后台不抢焦点。

### 1. 日志系统（新）

```
server\logbook.py        事件日志（一行一条 JSON）+ 人能读的文本日志
日志\events-YYYYMMDD.jsonl
日志\human-YYYYMMDD.log
```

- **接口**：`GET /api/v3/logs?q=&kind=&level=&limit=&days=` · `GET /api/v3/logs/tail?n=` · `GET /api/v3/logs/stats`
- **界面**：浏览器打开 `http://127.0.0.1:9277/logs`（搜索框 + 类型/级别筛选 + 关键词高亮 + 30 秒自刷）
- **已埋点**：`collect.start` / `collect.end` / `page.turn` / `search.start` / `search.step`（每一步）/ `search.done` / `overlay.click` / `overlay.noclose`
- **查询口径**：空格分隔的多词按「全部命中」；不存在的词返回空，不误报
- **不落敏感值**：`pass/pwd/secret/token/cookie/unb/key` 这类字段只记 `<N字符>`，**永不记明文**

### 2. 后台点击 —— 已实测，不抢焦点

自测结论（`_验_日志与后台点击.py`，17/17 全过）：

```
点击前 前台窗口 pid=56832
点击后 前台窗口 pid=56832      ← 没被抢走
页面收到 click · isTrusted=true
滚轮事件 also trusted · 前台仍未变
```

代码里**没有任何** `Page.bringToFront` / `Target.activateTarget`（已全局 grep 确认为空）。
CDP 的 `Input.dispatchMouseEvent` 是**投递给目标标签页的合成事件**，标签页在后台照样收得到，不会把窗口提到前面。

### 3. 登录态判据纠正（重要）

**旧写法有致命缺陷**：`document.cookie` **看不见 HttpOnly 的 cookie**，而淘宝的 `unb`（真登录票据）正是 HttpOnly。

```
实测（2026-10-22 · www.taobao.com）
  Network.getCookies  → 43 个 cookie，unb 存在（8 位），tracknick=sun66322   → 已登录
  document.cookie     → has_unb = false                                     → 会误判「没登录」
```

→ 已改为 `collector.login_cookies()` 走 `Network.getCookies`；`login_wall()` 的提示语也不再武断说「cookie 里没有 unb」，而是分三种情况：真没票据 / 有票据所以是风控降级 / 取不到无法判断。

### 4. 浮层：AdGuard 与 CSS 隐藏的实际结论

- **AdGuard 装在夸克浏览器**（`F:\...\Quark\User Data`，v5.4.3.125，MV3），**不在 Tabbit**（`C:\TabbitData`）。
  采集用的调试端口 9222 属于 **Tabbit**，所以 AdGuard 的规则**对采集这个页面不生效**。
- **那个浮层的 class 是空的、id 也没有、z-index 是 2147483647** —— 按选择器写的 AdGuard 元素隐藏规则几乎无从下笔，很容易误伤。
  实测：`DIV cls=（空） 308x518 @(1332,376) z=2147483647`
- **CSS 隐藏本身有效**（把浮层 `display:none` 后浮层数 1 → 0，不动任何输入事件），
  但注意：**这次那个浮层并没有盖住搜索框**（`covered=false`），所以不能宣称「已经解决遮挡」。
- 结论：**「找叉点掉」这条路保留**（几何 + 语义打分，比 CSS 选择器稳），
  CSS 隐藏作为**可选加速**（先隐藏，找不到叉就退回点）。

### 5. 新增/改动文件

```
server\logbook.py            🆕 日志系统
server\humanize.py           + _lb() 埋点；home_search 每步入日志；overlay.click 入日志
server\collector.py          + login_cookies()（Network.getCookies）；login_wall 措辞改准；
                             + collect.start/end、page.turn 埋点
server\app.py                + /api/v3/logs · /logs/tail · /logs/stats · /logs 页面
_验_日志与后台点击.py         🆕 17 项自测
_v3\_探_解封与浮层.py         🆕 探针（登录态 / 浮层 / CSS 隐藏对比）
_清_还原页面.py              🆕 把注入的隐藏样式还原（收尾用）
```

**跑法**：`F:\pyenvs\douyin\Scripts\python.exe _验_日志与后台点击.py`（必须用这个解释器，系统那个没装 websocket）


---

## 扩展界面已关闭（2026-10-22）

插件现在只做**采集接口**：上货台走 CDP(9222) 直接读页面、直接调接口，不再需要插件自带的界面。

已关掉两处：

| 位置 | 元素 | 开关 |
|:--|:--|:--|
| 搜索页右下角悬浮面板 | `#tp-bar` | `capture-list.js` → `EXT_UI.panel = false` |
| 卡片上的 A/B/C 角标、「就采这件」标记 | `.tp-badge` 等 | `capture-list.js` → `EXT_UI.badge = false` |
| 商品详情页右下角浮窗 | `#tp-bar-detail` | `capture-detail.js` → `EXT_UI_PANEL = false` |

**行为变化**：面板不创建（连 DOM 都不进），所以不会再「关掉→刷新又冒出来」。
**功能不变**：详情页「打开即自动入库」照旧（偏好仍从 `chrome.storage.autoDetail` 读）；
采集、细采、消息通道全部未改。

**要恢复界面**：把上面三个开关改回 `true` → `chrome://extensions` 点该插件的「重新加载」→ 刷新淘宝页。
扩展 ID：`aehjhlhdfcefcekhbmdgepgekfopdlgd`；实际加载路径：`_sandbox\overnight\legacy_s2\s2_extension`。

**踩到的坑**：改完文件**只刷新网页不够** —— Chrome 会沿用内存里的旧内容脚本，必须重载扩展
（`chrome.developerPrivate.reload(id)`，或在扩展页点重新加载）。本轮就是先只刷新网页，面板照样出现。
