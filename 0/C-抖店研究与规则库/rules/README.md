# 抖店公开运营规则库（可查询 / 可增量更新 / 判有效期与替代关系）

抓取时间：**2026-10-29**　｜　口径：**只用公开页面，不登录、不碰抖店后台、不动浏览器登录态**
库内：**39 条规则全文**（30.5 万字 / 128 张表），来源全部为官方页面正文
目标站点：`https://school.jinritemai.com/doudian/web/...`（抖店规则中心 / 抖音电商学习中心）

---

## 一、探测结论（开工前先跑的那一次）

| 探测 | 结果 |
|---|---|
| `GET https://school.jinritemai.com/doudian/web/rules` | **200**，HTML **28,881 B**，但去掉脚本/样式后**正文只有 65 字**（就是 `<title>抖音电商-学习中心</title>`） |
| `GET https://school.jinritemai.com/doudian/web/article/104353` | **200**，同样是 **28,881 B** —— 所有 `/doudian/web/article/*` 路由服务端返回**同一个 JS 空壳**，正文由前端渲染 |
| `GET https://school.jinritemai.com/`（首页） | **200**，**333,560 B**，正文 **3,177 字** —— 首页是服务端渲染，规则页不是 |
| 是否需要登录 | **不需要**。页面右上角有「立即登录」，但规则正文与目录**匿名可读**；本次全程未登录 |
| 结论 | **纯 HTTP 拿不到正文**（判 JS 空壳，正文 < 300 字）。正文必须在**真实浏览器渲染后**从 `#article-main .article-content` 取 |

**最终采用的渠道**：**ego-browser（Chromium）渲染 → 提取 `#article-main .article-content` 的 DOM → 自写 HTML→Markdown（保留表格）**。
一页约 3.4 秒，39 页约 3 分钟；过程中无验证码、无封禁、无登录墙。

**顺手挖到的官方 JSON 接口**（用于枚举与元数据，不是正文来源）：

| 接口 | 用途 | 备注 |
|---|---|---|
| `/api/eschool/v2/library/article/detail?id=<id>` | 单篇元数据 | 老文章 `content` 是 OT 格式（有现成 `html` 字段）；新文章是 aisle `deltas` 编码，**正文不宜从接口还原** |
| `/api/eschool/v2/library/article/search?keyword=<词>` | 按规则名找文章 id | 返回 exact_match，本次用它把规则名解析成 id |
| `/api/eschool/v1/rule/list?page=&page_size=200` | **规则中心全量目录**（3,658 条公示/征集通知） | 每条带 `effect_start` / `effect_end` / `status_code`（201=征集 / 204=生效 …） |
| `/api/eschool/v1/rule/center/main` | 首页「必读规则」27 条 | 含 `latest_rule` / 违规量排行 |

> 注意：`/doudian/web/article/<id>` 会 **302 到 `/doudian/web/articlev0/<id>`**，两者等价，抓的时候按跳转后的页面取。

---

## 二、库结构

```
rules/
  index.json          39 条索引（字段见第三节）
  md/<id>.md          规则正文（Markdown，表格已保留）+ 文件头 front matter
  raw/<id>.html       原始 `.article-content` DOM（用于复核与再次转换）
  changelog.md        变更记录：只记「哪天 / 哪条 / 哪个字段变了 / 正文 diff 摘要」
  抓取清单.md          逐条：抓到全文、字数、表数、生效日与来源
  README.md           本文件
  _raw/               原始素材：规则中心完整目录（3657 条）、首页必读规则
  _tools/
    生成.py           主生成器：HTML→Markdown、日期解析、sha1 比对、跳过未变化、写 changelog
    更新.py           增量更新入口：重抓 → 交给 生成.py 比对
    targets.json      本批要抓的 id 清单（规则）
    targets2.json     本批要抓的 id 清单（公告）
    batch-01.mjs      批量渲染抓取（规则）
    batch-02.mjs      批量渲染抓取（公告）
    probe-01/02/03.mjs 探测脚本（能否拿到正文 / 正文容器 / 子树杂质）
```

**覆盖情况（8 类全覆盖）**

| 类别 | 条数 | 代表 |
|---|---:|---|
| ① 违规管理（总则 + 各类细则） | **14** | 商家违规行为管理总则 101663、虚假宣传 101936、虚假交易 113470、发布法规禁止商品/信息 101805、价格违规 aJ2ymdS5jWGZ、诱导第三方 aHq5uvgaYmeC、行业/商品资质 105806、骚扰他人 105788、出售假冒/盗版 101890、品质差 aJHWfjABuVQM、核心信息误导 101939、消极服务 113278、遮挡/规避品牌 aJ8Z3L3r3qNg、品质抽检 101682 |
| ② 商品发布规范 | **5** | 商品信息发布规范 101800、商品信息质量分规范 aHT3QsZmcp6K、商品主图发布规范 aHean96XQDdv、招商标准及入驻规范 101818、0元入驻试运营规范 aHibaVrJVc8f |
| ③ 体验分规则 | **2** | 商家体验分规范 103956、【全球购】商家体验分规范 aHMSpgQDfmxk |
| ④ 店铺评分 / 精选联盟准入 | **3** | 精选联盟平台管理规则 101665、团长准入准出 112732、商品品退率准入实施细则 aHe9ZMdtRC5d |
| ⑤ 价格与促销 | **4** | 商品价格管理规则 101802、平台营销活动基础规则 109003、营销优惠叠加规则 aHwMeX7MccpH、「平台加补」优惠通用规则 aJsKYbh2NDvA |
| ⑥ 售后与纠纷 | **5** | 售后争议处理总则 101834、七天无理由退货服务规范 101835、商家发货行为管理规则 101706、商家售后服务管理规范 104600、【商家-发货超时】实施细则 aHwH9wK4Je2N |
| ⑦ 保证金与扣罚 | **1** | 保证金管理规范 101830（具体扣罚金额在各违规细则的处置表里） |
| ⑧ 平台公告 | **5** | 最新公示/意见征集通知（含 3 条直接修订本库规则的） |

---

## 三、index.json 字段与口径

```json
{
  "id": "103956",                      // 官方文章 id（= URL 尾段）
  "title": "商家体验分规范",             // 页面 <title>，非人工改写
  "category": "experience",            // 八类之一
  "category_cn": "体验分",
  "url": "https://school.jinritemai.com/doudian/web/article/103956",
  "effective_date": "2026-09-26",      // 当前版本生效日
  "effective_date_source": "text_latest_revision",
  "first_effective_date": "2020-10-09",// 首次生效日（规则自述）
  "latest_revision_date": "2026-09-26",
  "expire_date": null,                 // 失效日（绝大多数规则不自述）
  "superseded_by": null,               // 被哪条替代（见下）
  "pending_revision_notice": ["aJsb..."],  // 已有修订公告但尚未改正文的，挂在待生效的
  "source_type": "official",           // official / reprint / secondhand
  "verified": true,
  "fetched_at": "2026-10-29T14:13:00+08:00",
  "content_sha1": "64a81d8519ed11be",  // 正文 sha1 前 16 位（增量更新的判据）
  "md_file": "md/103956.md",
  "raw_file": "raw/103956.html",
  "chars": 8490, "tables": 7,
  "summary_100": "…", 
  "last_check_status": "new | changed | unchanged"
}
```

### `effective_date` 的三种来源（**必须看这一栏，别把页面的发布时间当成规则的生效时间**）

| effective_date_source | 含义 | 条数 |
|---|---|---:|
| `text_latest_revision` | 正文【生效时间】里写明「于 X 年 X 月 X 日最新修订生效」——**最可信**，就是当前版本生效日 | 13 |
| `text_first_effective` | 正文只写了「首次生效」没写修订日（如保证金管理规范）| 2 |
| `text_notice_effective` | 公告里「规则预计于 X 年 X 月 X 日生效」——**这是未来生效日** | 3 |
| `page_publish_time` | 正文没有日期，退化为**页面标注的发布时间**（严格说不是生效日，只作弱证据）| 20 |
| `null` | 正文与页面都没有任何日期 | 1（113278 消极服务细则）|

### `superseded_by` 与 `pending_revision_notice` 的分工

- **`superseded_by`** = 已经被另一条规则**替代**（老规则 v1 → 新规则 v2）。本库内每条都是当前唯一版本，**暂无被替代项，全部 null**。规则一旦改版，旧版会在这里指向新版 id。
- **`pending_revision_notice`** = 官方已发**公示/征集通知**（新版本还没生效）。这是「即将被替代」，**不等于是已被替代**——本库不把公示当既成事实。本次挂上 3 条：

| 规则 | 修订公告 | 公告类型 |
|---|---|---|
| 售后争议处理总则 `101834` | `aJsrmz8ggKea` | 意见征集（2026-10-28~11-04）|
| 商家【行业/商品资质违规】处置细则 `105806` | `aJsb1PjXxMK7` | 意见征集（2026-10-26~11-02）|
| 精选联盟商品品退率准入实施细则 `aHe9ZMdtRC5d` | `aJsaq17us1rZ` | 公示（2026-10-28~10-28，规则预计 11-04 生效）|

---

## 四、怎么更新（判据写死在这里）

```powershell
$py = 'F:\pyenvs\douyin\Scripts\python.exe'
cd 'G:\Users\Administrator\Documents\deepseek-harness\default-workspace\抖店研究\rules\_tools'

& $py 更新.py --list          # 看库里有什么（含 sha1）
& $py 更新.py --id 103956     # 只更新一条：重抓 → 比对 → 变了才重写
& $py 更新.py                 # 全量重抓 + 比对
& $py 更新.py --dry           # 只抓不写
```

**判据（`生成.py` 里实现，不靠人肉）**：

1. 重抓同一条 → 用**同一套 HTML→Markdown 逻辑**重算正文 → 取 `sha1(正文)[:16]`
2. `sha1` 与 `index.json` 记录一致 → 标 `unchanged`，**md 文件不重写**（mtime 不变，raw 也不写）
3. `sha1` 不一致 → 重写 `md/<id>.md` + `raw/<id>.html`，并在 `changelog.md` 追加一行：
   `- \`103956\` 商家体验分规范 —— content_sha1: \`64a81d…\` → \`ab12cd…\`；effective_date: \`2026-09-26\` → \`2026-10-20\`；正文 diff：+12 行 / -3 行`
4. 第二次跑同一批数据 → 输出 `written md: 0`（**已实测**）

**注意**：`更新.py` 必须走 ego-browser。用 requests 只能拿到 28KB 空壳，正文是 0 字，会被判成「未抓到」。

---

## 五、已知缺口（如实写，不用记忆补）

| # | 缺口 | 影响 | 后续怎么补 |
|---|---|---|---|
| 1 | **`expire_date` 39 条里 0 条有值** | 规则几乎不自述「失效日」，只写「首次生效 / 最新修订生效」 | 只能用「新版生效 + 旧版 `superseded_by`」来表达有效期；等平台改版时由 `changelog` + `superseded_by` 承担 |
| 2 | **体验分行业权重系数表未核准** | 官方 2.3.3 只写「不同行业的商品/物流/服务体验权重存在差异」，**具体权重表以附件/图片/外链给出，正文文本里没有数值**（该页 10 张图全是懒加载占位图 `default-cover`） | 需要人工登抖店后台看「商家体验分 → 全部考核指标」，或抓附件图片后 OCR |
| 3 | 只抓了 **39 / 3657** | 规则中心目录共 **3,658 条**（含大量行业规范与公示通知），本次按 8 类优先级取样 | 完整目录已存 `_raw/规则中心目录-3657条.json`（带 `effect_start/effect_end`），按关键词批量扩抓即可 |
| 4 | **禁售商品目录没有统一页** | 平台按行业拆（药品 / 医疗器械 / 二手 3C …），没有「全平台禁售商品目录」单页 | 本次用《【发布法规禁止商品/信息】处置细则》101805 + 《商品品质抽检明细》101682 兜住主干 |
| 5 | **保证金具体扣罚金额不在保证金规范里** | 101830 只讲缴存/冻结/划扣机制；金额在各违规细则的处置表（已抓到的 14 条细则含金额表） | 需要「每一类违规罚多少钱」时，查各细则的处置表，别查保证金规范 |
| 6 | 达人侧口碑分没抓 | 达人带货口碑分是达人指标，不是商家规则 | 需要时补达人侧页面（如 `cHV9xoGDGA2plHVAEUQ3NTdv`） |
| 7 | `raw/*.html` 是**渲染后 DOM**，不是服务端原文 | 图片是懒加载占位、不含登录态内容 | 要「服务端原文」只能拿空壳，没有意义；要图片需另开滚动加载再抓 |
| 8 | 正文里的表格工具栏残留已过滤 | 原页表格带「全屏展示 / 退出」按钮文字，生成器已按白名单剔除 | — |

---

## 六、可信度声明

- **39/39 条 `source_type=official`、`verified=true`**：全部取自 `school.jinritemai.com` 官方页面的渲染正文，**没有一条来自转载、二手文章或记忆**。
- **没有编造条文**：抓不到就记「未抓到」（本次 0 条），缺口全部写进第五节。
- **正文里所有数字都可在 `md/<id>.md` 原文里 grep 到**，例如体验分的「≥30 单 / 每天上午更新 / 近 30 天」都在 `md/103956.md`。
- 页面自带「2026-09-26 08:00:03」这类时间戳已单独存为 `page_publish_time`，**没有冒充生效日**。

---

## 附：本批 39 条一览（id / 生效日 / 来源）

见 `抓取清单.md`（按八类分表，含字数、表数、生效日与来源口径）。
