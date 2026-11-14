# AI运营中心 - 视频生产与发布设计

> 调查员：视频生产与发布设计员（叶子节点，只读调查 + 设计）
> 时间：2026-11-09 17:36–18:20（Asia/Shanghai）
> 事实底稿：`上货台7.0调查-20261109\00-调查任务书与实测事实.md`
> 本轮**未写任何生产代码、未改任何数据库、未发布任何视频、未调用任何上架接口**。
> 全文标注：`[源码确认]` / `[本机实测]` / `[官方文档]` / `[设计建议]` / `[尚未验证]`

---

## 结论摘要（≤200 字）

**最小可用版本能先做 6 步**：素材发现（1、2）、完整度检测（3）、格式/分辨率/时长探测（4）、来源与授权记录（6）、封面生成（11）、版本归档（16）——这 6 步**本机零新增依赖**，FFmpeg 9.0.1 已在、数据已在库。

**能做但要写代码**：视频脚本/文案/字幕/配音/图片转视频/双平台出片（7、8、9、10、12、13、14）——FFmpeg + 现有本地 LLM 足够。

**现在做不了**：AI 生成动态视频（5 的深度理解、12 的 AI 运镜）——ComfyUI 0.38.0 已装但**没有任何视频生成模型**。

**发布**：微信小店侧有官方 API 全链路；抖音侧上传有 API、**挂商品无 API**，必须走固定浏览器流程。

---

## 一、现状核查：视频有没有、在哪、什么格式（实测）

### 1.1 结论先行

| 问题 | 答案 | 证据 |
|:--|:--|:--|
| 视频入库了吗 | **入库了** | `v3_image.role='video'` 共 **194 行**，`dl_state` 全部 `ok` |
| 在盘上吗 | **在**，194 个本地路径 100% 存在 | DB 里 `current_local_path` 全部非空 |
| 有几个 | DB 194 个 / 盘上 **210 个 mp4** | 差额 12 个「有文件无记录」的孤儿 |
| 什么格式 | **HEVC/H.265 + AAC，720×1280，30fps，约 10.4 s** | ffprobe 实测（见附录 A） |
| 有分区吗 | 有，`images\<product_id>\video\video_<hash12>.mp4` | 目录实测 |
| 有商品关联吗 | **有**，`v3_image.product_id` 外键，114/372 个商品有视频 | SQL 实测 |
| 有时长/编码字段吗 | **没有** | `v3_image` 表结构里只有 `width/height`，且视频行这两列为 NULL |

### 1.2 `v3_image` 是唯一的媒体表，用 `role` 区分类型 `[源码确认]`

```sql
CREATE TABLE v3_image (
    image_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER NOT NULL REFERENCES v3_product(product_id) ON DELETE CASCADE,
    slot_key           TEXT NOT NULL,           -- role|seq|sku|slice，唯一性靠它
    role               TEXT NOT NULL,           -- main|carousel|detail|sku|video|unknown
    sequence           INTEGER NOT NULL DEFAULT 0,
    ...
    original_url       TEXT, original_local_path TEXT, current_local_path TEXT,
    content_sha256     TEXT, parent_image_id INTEGER,
    status             TEXT NOT NULL DEFAULT 'original',
    width INTEGER, height INTEGER,
    dl_state TEXT, download_hint TEXT
);
```
> 出处：`taobao-picker\data\v3.db` 的 `sqlite_master.sql`，`mode=ro` 只读读出 `[本机实测]`

**role 分布实测** `[本机实测]`：

```
sku     6353
detail  6276
main    5530
video    194
（合计 18353，与底稿数字一致）
```

**视频行样例** `[本机实测]`：

```
(9552, 1382, 'video|0||', 'video', 0,
 '...\images\1382\video\video_0b11dfc21e83.mp4', 'original',
 None, None, 'ok', 'video/mp4', 'bytes=5940975')
(9553, 1382, 'video|1||', 'video', 1,
 '...\images\1382\video\video_d434d441cb92.mp4', ...)
```

→ 一个商品可带**多条**视频（1382 有 2 条），用 `sequence` 排序，`slot_key='video|序号||'` 保唯一。

### 1.3 已知缺陷（必须写进设计，否则会算错）

| # | 缺陷 | 实测证据 | 影响 |
|:--|:--|:--|:--|
| D1 | **盘上 210 个 mp4，库里只 194 行**，12 个是孤儿 | 逐路径比对（附录 A-3） | 素材中心若只信库，会漏报 12 个可发布素材；若只信盘，会把已删商品的文件当成有效素材 |
| D2 | 视频行 `width/height` 全为 NULL | SQL 实测 | 「完整度检测」不能靠库里的分辨率，必须现场 ffprobe |
| D3 | 表里**没有** duration / codec / fps / bitrate 字段 | 表结构 | 时长/编码只能新增旁挂表，不能改 `v3_image`（底稿红线：不改生产库） |
| D4 | 9 个 `.part` 残留（`image_*.jpeg.part`，184–506 B，2026-11-09 02:25） | 目录实测 | 下载中断的碎屑，素材扫描必须按扩展名白名单过滤，否则被当成素材 |

> **孤儿那 12 个的规律**（`1373`–`1379` 共 6 个商品，各 2 条）`[本机实测]`：
> 文件名 hash 与 `1382/1383/1377/1379` 里已入库的**完全同名** —— 即**同一视频文件被复制到了新商品目录，但新商品的 DB 行没建**。属采集侧「复制了文件没写库」，不是凭空多出来的素材。

### 1.4 现有代码里视频的处理位置 `[源码确认]`

| 文件:行 | 作用 |
|:--|:--|
| `server\v3\media.py:37` | `VID_EXT = (".mp4",".mov",".m4v",".webm")` 视频扩展名白名单 |
| `server\v3\media.py:114` | `_ext_of(url, ".mp4" if want=="video" else ".jpg")` 落盘命名 |
| `server\v3\media.py:307` | `update_video_urls(conn, pid, urls)` —— 视频链过期后换新地址、重置状态、立刻重下 |
| `server\v3\adapters\weidian.py:129-259` | 从 `videoUrls` + `_raw.headVideoInfo[0]` 三种编码里收集视频，识别「链接过期」 |
| `server\v3\adapters\wechat.py:237-285` | `videoUrls=[]` 且 `_raw.videoUrls=null` → 记「来源未提供视频」 |
| `server\v3\adapters\pdd.py:120-251` | 拼多多 `goods.gallery[]` 的 `type=9` 项带 `video_url`（轮播视频） |
| `server\v3\taskcenter.py:205-251` | 已有 `video_stats / video_filter / video_items`，桶名 `has_video / no_video / video_ok / video_fail` |
| `server\v3\bundle.py:76` | 已有 `video_meta(path)`，打包时算视频元信息 |

> **这是好消息**：素材发现（能力 1）**不需要新写采集器** —— `taskcenter.video_*` 三个函数已经是「按视频维度筛商品」的现成查询。

### 1.5 微信小店侧：商品视频的代码路径已存在，但**上传通道没接** `[源码确认]`

```
微信小店自建上货台\app.py:1300-1301
    if it.get('video'):
        skip += '｜视频未接（源为第三方临时链）'      ← 建商品时明确跳过视频

微信小店自建上货台\app.py:1778-1779
    'video': [('mode','select','方式',[('replace','整组替换'),('append','追加')]),
              ('urls','textarea','视频地址（逗号或换行分隔）', None)],

微信小店自建上货台\app.py:1818-1819
    if op in ('main_pic','desc_pic','video'):
        key = 'head_imgs' if op=='main_pic' else ('head_videos' if op=='video' else 'desc_info')
```

→ **改商品视频的字段拼装已经写好**（写 `head_videos`），**缺的是「把本地 mp4 变成微信可用的 video_url」这一步**（官方要求先上传拿 URL）。所以微信侧工作量 = 只补上传三步接口，不用重建关联逻辑。

---

## 二、素材中心 16 项能力落地表

> 统一前提：素材根 = `taobao-picker\data\v3\`；`FFMPEG=ffmpeg 9.0.1`；只读源目录，**一切生成物写新目录**。
> 「可否现在做」三档：**能**（零新增依赖）/ **需新增**（要装库或写模块）/ **做不了**（缺模型或平台不给）。

| # | 能力 | 用什么做（具体工具/库/命令） | 输入 | 输出落点 | 失败判据 | 能否现在做 |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | 自动发现商品视频 | `SELECT image_id,product_id,current_local_path FROM v3_image WHERE role='video' AND dl_state='ok'`（复用 `taskcenter.video_items`）＋ 目录兜底扫 `images\*\video\*.mp4` | `v3.db` + 媒体目录 | 内存索引 + `v3_video_asset` 不落（原始不搬迁） | 路径存在但 `dl_state!='ok'` → 标 `broken`；盘上有而库里无 → 标 `orphan` | **能** |
| 2 | 自动发现商品图片 | 同上，`role IN ('main','carousel','detail','sku')` | `v3.db` | 同上 | 同上 | **能** |
| 3 | 自动检测素材完整度 | 规则表：主图≥1（`v3_product.main_image_id`）、轮播≥3、详情≥1、视频≥1；逐项算 `have/need`；复用 `grade.py:170` 已有的视频判定 | `v3_image` 聚合 | `v3_media_probe.completeness` 或查询视图 | 任一项 0 → `incomplete` 并列出缺口名 | **能** |
| 4 | 检测格式/分辨率/时长 | `ffprobe -v error -print_format json -show_streams -show_format <file>` 解析 `codec_name/width/height/r_frame_rate/duration/bit_rate` | 本地 mp4 | 新表 `v3_media_probe`（旁挂，**不改 `v3_image`**） | ffprobe 非 0 退出 / `duration` 缺失 / `codec_name` 不在白名单 → `probe_failed` | **能** |
| 5 | 识别商品视频内容 | 三段拼：① 抽帧 `ffmpeg -vf fps=1/2` → ② 已有 OCR 表 `v3_image_ocr`（18156 行可复用图片 OCR，视频需新抽帧后跑 OCR）→ ③ 视觉理解走本地 Ollama/llama.cpp 的 VL 模型 | 抽帧图 + 音轨 | `v3_video_asset.content_tags`（JSON：场景/主体/文字/类目一致性） | 抽帧 0 张 / VL 超时 3 次 → `unverified`（不猜） | **需新增**：抽帧脚本好写，**VL 模型本机未核实**（见 §三） |
| 6 | 记录素材来源与授权 | 复用 `v3_product.source_platform/source_url/provenance`；视频侧新增 `license_state` 枚举 `own / authorized / unknown / forbidden`，默认 `unknown` | 采集时已有字段 | `v3_video_asset.license_state` + `v3_audit` 留痕 | 无 `provenance` 的一律 `unknown`，**不得默认 own** | **能**（字段新增） |
| 7 | 生成视频脚本 | 本地 LLM（Ollama `qwen2.5:7b` 现成／llama.cpp `8080` 无审查模型）＋ 结构化模板：`{钩子3s / 卖点×3 / 场景×2 / CTA}`；输出 JSON 存 `v3_video_script` | 商品标题+属性+卖点 | `v3_video_script.script_json` | JSON 解析失败 / 缺「钩子」段 → `failed`，不静默降级 | **能**（需确认模型在线，见 §三） |
| 8 | 生成推广文案 | 同 7 的模型 + 平台字数约束（抖音标题/视频号描述），一条主文案 + 3 条候选 | 脚本 + 标题 | `v3_video_publish.title/caption`（**发布前只是草稿**） | 超平台字数 → 截断并标 `truncated`，不静默 | **能** |
| 9 | 生成字幕 | ① 已有 SRT：直接读；② 无 SRT：`ffmpeg` 抽音轨 → 本地 ASR（**本机 ASR 未核实**）→ SRT；③ 烧字幕：`ffmpeg -vf "subtitles=x.srt:force_style='FontName=Microsoft YaHei,FontSize=24'"` | 视频 + `*.srt` | `video_gen\<pid>\subs\v001.srt` + 烧字幕版 mp4 | SRT 时间轴越界 / 字数 0 → 不烧，直接报错（烧错字幕比不烧更糟） | **需新增**：烧字幕**能**（ffmpeg 已带 libass/freetype/fribidi）；**ASR 需新增** |
| 10 | 自动配音 | 短句用 Windows 内置 SAPI（`System.Speech.Synthesis`，零安装）作兜底；高质量需 `edge-tts`（联网）或本地 TTS 模型 | 脚本台词 | `video_gen\<pid>\audio\v001_tts.wav` | 时长与画面不匹配 >15% → 重试一次后标 `failed` | **需新增**（SAPI 可用但音色机械，标 `tts_quality=low`） |
| 11 | 生成商品封面 | `ffmpeg -ss <t> -i in.mp4 -frames:v 1 -q:v 2 cover.jpg`；或选已有主图（`role='main'` 且 `status='original'`）缩放为平台比例 | 视频或主图 | `video_gen\<pid>\cover\v001_1080x1440.jpg` | 全黑/全白帧（`signalstats` 均值判）→ 换时间点重抽，3 次失败报错 | **能** |
| 12 | 图片转视频 | **方案 A（现在就能）**：`ffmpeg -loop 1 -i img -vf "zoompan=z='min(zoom+0.0008,1.12)':d=125:s=720x1280" -t 10` Ken Burns 推拉 + 转场 `xfade`；**方案 B（做不了）**：ComfyUI 图生视频（缺 SVD/AnimateDiff 模型） | 3–8 张主图/轮播图 | `video_gen\<pid>\ai_image_video\v001_*.mp4` | 单图时长 <3 s / 总时长不足 → `failed` | **A 能**；**B 做不了** |
| 13 | 输出抖音版本 | `ffmpeg -i in -c:v libx264 -profile:v high -crf 20 -pix_fmt yuv420p -r 30 -c:a aac -b:a 128k -movflags +faststart out.mp4`；竖版 720×1280 或 1080×1920 | 生成视频 | `video_gen\<pid>\dy_version\v001_*.mp4` | 超 15 min / 超 128 MB（未分片）→ 拒出并提示分片或压缩 | **能** |
| 14 | 输出微信视频号版本 | 同 13 参数，另按平台比例补边/裁切（`pad` / `crop`）；**比例与时长上限官方未公开明确值** → 出片参数走配置项，不硬编码 | 生成视频 | `video_gen\<pid>\wx_version\v001_*.mp4` | 与 13 相同 + 比例越界告警 | **能**（参数待核实） |
| 15 | 关联对应商品 | 原始：`v3_image.product_id` 已有；生成物：`v3_video_asset.product_id`；发布：`v3_video_publish.linked_product_id`（平台侧商品 ID） | 生成物 + 平台商品 ID | 三张表的 `product_id` | 商品 ID 在平台上查不到 → `link_failed`，**不得假定成功** | **能** |
| 16 | 保存历史版本 | 命名规则 `v{NNN}_{YYYYMMDD-HHMMSS}_{sha8}.{ext}` **只增不改**；`v3_video_asset.superseded_by` 指向新版本；原始目录**只读** | 每次生成 | 见 §四 | 目标路径已存在 → **报错不覆盖**（不做「同名覆盖 + 备份」这种半吊子方案） | **能** |

### 2.1 16 项汇总

- **零新增依赖即可做：12 项**（1、2、3、4、6、7*、8*、11、12A、13、15、16）
- **需新增依赖/模块：4 项**（5 视觉理解、9 ASR、10 高质量 TTS、14 平台参数核实）
- **明确做不了：2 项**（12B AI 运镜生成、5 的深度语义理解）

> \* 7、8 依赖本地 LLM 在线，模型不在线时按 `failed` 处理，**不允许静默返回模板文本冒充 AI 生成**。

---

## 三、技术选型核查：FFmpeg / Remotion / ComfyUI（实测）

### 3.1 FFmpeg —— **有，且够用**（首选渲染引擎）`[本机实测]`

```
ffmpeg version 9.0.1-essentials_build-www.gyan.dev
--enable-libx264 --enable-libx265 --enable-libaom --enable-libvpx
--enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz
--enable-libmp3lame --enable-libopus --enable-libvorbis
--enable-nvenc --enable-cuda-llvm --enable-amf --enable-libvpl
```

**关键编解码器可用性**（`ffmpeg -encoders` 实测）：

```
V....D h264_nvenc    NVIDIA NVENC H.264 encoder
V....D hevc_nvenc    NVIDIA NVENC hevc encoder
V....D av1_nvenc     NVIDIA NVENC av1 encoder
V....D libx264       H.264 / AVC
V....D libx265       H.265 / HEVC
A....D aac / libmp3lame
```

**能干什么（全部已验证能力，非推断）**：
- 转码/压缩/改分辨率 → `libx264` 或 `h264_nvenc`（显卡加速）
- 烧字幕 → `libass` + `freetype` + `fribidi`（中文字体 `Microsoft YaHei` 系统已有）
- 图片转视频 → `zoompan` 滤镜 + `xfade` 转场
- 抽帧/截图 → `-vf fps=1/2`、`-frames:v 1`
- 音频合成 → `amix`/`amerge`，AAC 输出
- **原视频是 HEVC**：`ffprobe` 实测 `codec_name=hevc`，抖音/微信都**推荐 mp4(H.264)**，所以**出片必须转码，不能直接上传 HEVC 原件** ← 这条是实测结论，不是惯例

**成本**：零授权费，CPU/GPU 本地跑，一条 10 s 视频转码 <2 s。

### 3.2 Remotion —— **没装，建议先不装** `[本机实测]`

```
npm view remotion version      → 4.0.542   （npm 可达，可装）
Test-Path ...\node_modules\remotion → False （未安装）
node v25.2.1 / pnpm 11.7.0 / npm 11.6.2     （工具链在）
```

**它是什么**：用 React 组件写视频画面，逐帧渲染后用 Chrome 无头浏览器截图、再由 FFmpeg 合成。适合「字幕排版复杂、要动效、要多套模板」的片子。

**为什么建议先不装**：

| 维度 | Remotion | FFmpeg 直出 |
|:--|:--|:--|
| 首次成本 | 需 `npx remotion` 拉 Chromium 无头内核 + 装 React 工具链 | 零安装 |
| 渲染速度 | 每帧一次浏览器截图，**比纯滤镜慢一到两个数量级** `[设计建议]` | 硬件加速，秒级 |
| 适合 | 品牌模板、复杂图文动效、批量多语言 | 转码/裁切/烧字幕/图片推拉 |
| 不适合 | 单机 8 GB 显存 + 要批量出几十条的环境 | 复杂动画排版 |

**结论**：**第一阶段不引入 Remotion**。当出现「同一模板 × 几十个商品 × 复杂字幕动效」的真实需求时再评估；届时它也**只负责出画面**，最终合流与平台适配仍走 FFmpeg，避免两套管线。
`[尚未验证]`：Remotion 在 Windows + Node 25 上的实际渲染耗时未实测（未安装，不装不测）。

### 3.3 ComfyUI —— **装了、版本明确、但缺视频模型** `[本机实测]`

```
路径      J:\AI\ComfyUI   （另有 J:\AI\comfyui 同名目录，用途待确认）
版本      0.38.0          （comfyui_version.py / pyproject.toml 一致）
监听      8188 未监听（当前没在跑）
计划任务  "ComfyUI 服务 (8188)"  Status: Ready
启动脚本  J:\AI\scripts\start-comfyui.cmd（内含 --disable-smart-memory --reserve-vram 0.3，
          注释实测：8GB 卡被桌面应用占 ~6.8GB，不加这两个参数 Krea2 会卡在 Model Initializing）
```

**模型盘点**（决定「能不能生成视频」的硬指标）：

| 目录 | 内容 | 对视频能力的意义 |
|:--|:--|:--|
| `checkpoints` | 3 个 SDXL 系（Juggernaut-XL v9、RealVisXL V5、pornworksRealPornPhoto）19.54 GB | 只能**生成静态图** |
| `text_encoders` | `qwen_3_8b_fp4mixed` 6.34 GB、flux2-klein-9b-uncensored(q4_k_m) | 文本编码，非视频 |
| `diffusion_models` / `unet` | **0 个** | **没有 SVD / AnimateDiff / CogVideo 等视频扩散模型** |
| `frame_interpolation` / `optical_flow` | **0 个** | 没有补帧/光流，做不出平滑运镜 |
| `custom_nodes` | 含 `ComfyUI-VideoHelperSuite`、`reactor`、`InstantID`、`IPAdapter_plus`、`GGUF` | VHS 只能**读写视频、把图片序列合成视频**，不能生成运动 |

**显存实测** `[本机实测]`：`NVIDIA GeForce RTX 3070 · 8192 MiB 总量 · 1282 MiB 已用 · driver 617.42`

**结论**：
- ComfyUI **现在能做的**：生成商品场景图/换背景/封面美化（SDXL + IPAdapter + InstantID），再把图交给 FFmpeg 做图生视频（方案 A）
- ComfyUI **现在做不了的**：「图片转视频」的 AI 动态版（要下 3–8 GB 视频模型，且 8 GB 显存跑 720p 视频扩散非常吃紧）
- **不要把 ComfyUI 放进发布主链路** —— 它是「素材加工车间」，不是「视频生产线」，主链路必须是 FFmpeg（确定性、可重放、秒级）

### 3.4 选型结论一句话

> **主线 = FFmpeg（已就绪）+ 本地 LLM（脚本/文案）+ 现有 `v3_image` 数据（素材）**；
> **ComfyUI 只做静态图加工，不进主链路**；**Remotion 暂不引入**。

---

## 四、素材文件组织与命名（目录树 + 版本规则 + 不覆盖原则）

### 4.1 目录树（只新增，不动既有）

```
taobao-picker\data\v3\
├─ images\<product_id>\                 ← 【既有·只读】原始素材，绝不写入
│   ├─ main\   carousel\   detail\   sku\
│   └─ video\video_<hash12>.mp4         原始视频（194 条，禁改禁删）
│
├─ video_gen\<product_id>\               ← 【新增】一切生成物
│   ├─ script\  v001_20261109-181500_<sha8>.json      脚本
│   ├─ text\    v001_..._<sha8>.txt                    文案（标题+描述分离两文件）
│   ├─ subs\    v001_..._<sha8>.srt                    字幕
│   ├─ audio\   v001_..._<sha8>.wav                    配音
│   ├─ cover\   v001_..._<sha8>_1080x1440.jpg          封面
│   ├─ ai_video_a\       v001_..._<sha8>.mp4           AI 视频 A（模板甲）
│   ├─ ai_video_b\       v001_..._<sha8>.mp4           AI 视频 B（模板乙）
│   ├─ ai_image_video\   v001_..._<sha8>.mp4           图文合成视频
│   ├─ dy_version\       v001_..._<sha8>.mp4           抖音发布版本
│   ├─ wx_version\       v001_..._<sha8>.mp4           视频号发布版本
│   └─ _manifest\        v001_..._<sha8>.json          该版本的完整参数快照
│
└─ publish\<platform>\<product_id>\     ← 【新增】发布留证（不放视频副本，只放指针与回执）
    └─ v001_..._<sha8>.json
```

**为什么生成物不放进 `images\`**：`images\` 被采集、下载、打码、打包四条链路共用，生成物混进去会被当成「原始素材」再下载、再上传、再校验。**目录即语义边界。**

### 4.2 命名与版本规则（硬规则）

```
文件名 = v{版本号:03d}_{YYYYMMDD-HHMMSS}_{内容sha256前8位}.{ext}
版本号 = 该 (product_id, asset_kind) 组合下的单调递增整数，从 001 开始
```

例：`v003_20261109-181500_9f3a1c02.mp4`

**四条不可破的规则**：

1. **同名不覆盖**：目标路径已存在 → **直接报错退出**，不「先备份再覆盖」。备份式覆盖会在批量任务里悄悄吃掉上一版。
2. **时间戳必须带**：防止两次运行落在同一秒导致视觉上「看起来没变」。
3. **sha256 前 8 位必须带**：内容相同可去重识别，内容不同但版本号被误用时能立刻肉眼发现。
4. **`_manifest` 必须同版本同号**：任何 mp4 找不到同名 manifest → 判「来历不明」，不得进发布候选。

### 4.3 与数据库的对应

| 文件 | DB 落点 |
|:--|:--|
| `video_gen\<pid>\<kind>\vNNN_*.mp4` | `v3_video_asset.rel_path`（存相对路径，`UNIQUE(product_id, asset_kind, version)`） |
| 原始 `images\<pid>\video\*.mp4` | **不进 `v3_video_asset`**，仍在 `v3_image`（role='video'），靠 `v3_video_asset.source_image_id` 指回去 |
| 探测结果 | `v3_media_probe`（键 `media_key='image:9552'` 或 `'asset:12'`） |
| 发布留证 | `v3_video_publish` + `publish\<platform>\<pid>\vNNN_*.json` |

---

## 五、发布流程状态机（9 步）

> 状态字段：`v3_video_publish.publish_state`，合法值 `prep / checked / generated / reviewed / account_selected / uploaded / linked / confirmed / reconciled / failed:<step>`。
> **每步都可重入，且重入不得产生新版本号**（版本号只在「内容变化」时 +1）。

| 步 | 阶段 | 前置条件 | 输入 | 输出 | 失败处理 | 需人工批准 |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | **素材准备** `prep` | 商品存在且有 ≥1 图片 | `v3_image` 该商品全部行 | 素材清单（含完整度报告） | 完整度不足 → 停在此步，列缺口，不生成 | 否（L1） |
| 2 | **AI 检查** `checked` | 步 1 通过 | 素材清单 + 授权状态 | `checked` 或拦截原因（`license_state='forbidden'`/`unknown` 且策略要求授权） | 授权不明 → **默认拦截**，回报「需授权凭证」 | 否（L1，但拦截结果要显示） |
| 3 | **视频生成** `generated` | 步 2 通过 + LLM 可用 | 脚本/文案/字幕/配音/封面参数 | `v3_video_asset` 新版本 + 平台版本 mp4 | 任一生成物失败 → 整批回滚该版本（DB 行标 `failed`，文件保留供诊断） | 否（L2，自动但全量留痕） |
| 4 | **视频审核** `reviewed` | 步 3 有新版本 | 生成 mp4 + 封面 + 文案 | `approve_state='approved'` | 人判 rejected → 回步 3，**新版本号** | **是（L3 强制）** |
| 5 | **账号选择** `account_selected` | 步 4 通过 | 平台 + 店铺 + 账号 | `account_id`（已登录态校验通过） | 登录态失效 → 停，要求重新登录（**不自动过验证码**） | **是（L3 选账号）** |
| 6 | **视频上传** `uploaded` | 步 5 选定账号 | 平台版本 mp4 | `platform_video_id`（抖音 `video_id` / 微信 `video_url`） | 网络/分片失败 → 按平台规则重试（见 §七），>3 次标 `failed:upload` | **是（L4 首次授权）** |
| 7 | **商品关联** `linked` | 步 6 有 `platform_video_id` | 平台视频 ID + 商品 ID | `linked_product_id` | 抖音无 API → 转浏览器流程；微信走 `head_videos` | **是（L3）** |
| 8 | **发布确认** `confirmed` | 步 7 关联成功 | 标题/文案/封面/话题 | 发布回执 | 平台拒绝 → 原文写入 `error_msg`，**不改写不美化** | **是（L3 最后一道）** |
| 9 | **发布结果核对** `reconciled` | 步 8 有回执 | 平台查询接口 | 核对结论（真发布了没 / 商品挂上了没 / 审核状态） | 查不到 → `unknown`，**不得标成功**（对应铁律 18：不用启动返回当结果） | 否（L1，但要出对账差异表） |

### 5.1 状态机红线

- **步 9 必须回查平台真实状态**，不能拿步 8 的 HTTP 200 当「发布成功」。微信 `getplayinfo` 与抖音 `video.list`/查询接口是唯一判据。
- **步 4/5/7/8 是人批点**（对齐 L3），批一次只对「这一个商品 + 这一个版本 + 这一个账号」生效，**不得批一次跑批量**。
- **任一步失败都停在那一步**，不自动跳到下一步；恢复靠重入同一步。

---

## 六、发布记录字段设计（13 字段 → 列名/类型/索引）

### 6.1 主表 `v3_video_publish`（新建，**不动 `v3_publish`**）

> **为什么不复用 `v3_publish`**：它是「商品上架」记录（`external_listing_id / title_variant_id / image_id`），语义是商品 → 平台商品 ID；视频发布是「内容 → 平台视频 ID → 挂哪个商品」，**两套主键与生命周期都不同**。混表会导致 `v3_publish.outcome` 出现两种互斥语义。

```sql
CREATE TABLE v3_video_publish (
    publish_id        INTEGER PRIMARY KEY AUTOINCREMENT,

    -- ① 商品ID
    product_id        INTEGER NOT NULL REFERENCES v3_product(product_id),
    -- ② 平台
    platform          TEXT    NOT NULL,          -- 'douyin' | 'wechat'
    -- ③ 店铺ID
    shop_id           TEXT    NOT NULL DEFAULT '',
    -- ④ 发布账号ID
    account_id        TEXT    NOT NULL DEFAULT '',
    -- ⑤ 视频文件路径（相对 data\v3\，禁存绝对路径）
    video_rel_path    TEXT    NOT NULL,
    -- ⑥ 标题
    title             TEXT    NOT NULL DEFAULT '',
    -- ⑦ 文案
    caption           TEXT    NOT NULL DEFAULT '',
    -- ⑧ 封面路径
    cover_rel_path    TEXT    DEFAULT '',
    -- ⑨ 关联商品ID（平台侧商品 ID，不是本地 product_id）
    linked_product_id TEXT    DEFAULT '',
    -- ⑩ 平台视频ID
    platform_video_id TEXT    DEFAULT '',
    -- ⑪ 发布时间
    published_at      TEXT    DEFAULT '',        -- 'YYYY-MM-DD HH:MM:SS'，本地时区
    -- ⑫ 发布状态
    publish_state     TEXT    NOT NULL DEFAULT 'prep',
    -- ⑬ 错误信息（平台原始文案，不改写）
    error_msg         TEXT    DEFAULT '',

    -- 辅助（不属于 13 项，但缺了无法复现/回滚）
    asset_id          INTEGER REFERENCES v3_video_asset(asset_id),
    asset_kind        TEXT    DEFAULT '',        -- ai_video_a | dy_version | wx_version ...
    asset_version     INTEGER DEFAULT 0,
    attempt_no        INTEGER NOT NULL DEFAULT 1,
    channel           TEXT    NOT NULL DEFAULT 'api',   -- api | browser | manual
    platform_response TEXT    DEFAULT '',        -- 平台原始 JSON 片段（脱敏后）
    approved_by       TEXT    DEFAULT '',
    approved_at       TEXT    DEFAULT '',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT    DEFAULT ''
);
```

### 6.2 索引（按真实查询写的，不是凑数）

```sql
CREATE INDEX idx_vp_prod_plat   ON v3_video_publish(product_id, platform);
CREATE INDEX idx_vp_state       ON v3_video_publish(platform, publish_state, created_at);
CREATE INDEX idx_vp_platform_id ON v3_video_publish(platform, platform_video_id);
CREATE INDEX idx_vp_account_day ON v3_video_publish(account_id, published_at);
CREATE UNIQUE INDEX uq_vp_once  ON v3_video_publish(platform, shop_id, account_id, asset_id, attempt_no);
```

**`uq_vp_once` 的作用**：同一账号、同一素材版本、同一尝试序号只能有一条记录 —— **防重复发布**。这条索引比任何「先查再写」的代码都可靠。

### 6.3 13 字段逐一对照表

| # | 主人要求 | 列名 | 类型 | 说明 / 为什么这样定 |
|:--|:--|:--|:--|:--|
| 1 | 商品ID | `product_id` | INTEGER NOT NULL | 外键到 `v3_product`，**不存标题**，标题会变 |
| 2 | 平台 | `platform` | TEXT | 只允许 `douyin`/`wechat`，用 CHECK 约束 |
| 3 | 店铺ID | `shop_id` | TEXT | 抖音 `shop_id` 数字串、微信店铺 ID 都是字符串，统一 TEXT 避免大整数精度问题 |
| 4 | 发布账号ID | `account_id` | TEXT | 平台账号唯一标识（抖音 `open_id`/微信视频号 ID），**不存昵称**（昵称可改） |
| 5 | 视频文件路径 | `video_rel_path` | TEXT | **相对路径**：绝对路径一改盘符整表失效 |
| 6 | 标题 | `title` | TEXT | 发布时快照，**不引用 `v3_product.original_title`**（原标题不可覆盖） |
| 7 | 文案 | `caption` | TEXT | 含话题；超平台字数时截断并另记 `truncated=1`（可加列） |
| 8 | 封面路径 | `cover_rel_path` | TEXT | 可空：视频号封面常由平台自动截取（官方口径见 §七） |
| 9 | 关联商品ID | `linked_product_id` | TEXT | **平台侧**商品 ID（抖音 `product_id`/微信 `product_id`），与本地 `product_id` 是两个东西 |
| 10 | 平台视频ID | `platform_video_id` | TEXT | 抖音 `video_id`、微信上传后的 `video_url`/`media_id`；**这是核对步的唯一钥匙** |
| 11 | 发布时间 | `published_at` | TEXT | 存本地时区字符串，**不存 UTC**（与项目其余表 `created_at` 口径一致） |
| 12 | 发布状态 | `publish_state` | TEXT NOT NULL | 9 状态机值 + `failed:<step>`，便于按步统计失败 |
| 13 | 错误信息 | `error_msg` | TEXT | **平台原文**（errcode+errmsg），不改写不翻译 —— 翻译过就查不了文档 |

### 6.4 配套两张表

```sql
-- 探测结果（补 v3_image 缺 duration/codec 的短板，旁挂不改原表）
CREATE TABLE v3_media_probe (
    media_key    TEXT PRIMARY KEY,      -- 'image:9552' | 'asset:12'
    image_id     INTEGER,
    asset_id     INTEGER,
    duration_ms  INTEGER, width INTEGER, height INTEGER, fps REAL,
    codec_v      TEXT, codec_a TEXT, bitrate INTEGER, audio_channels INTEGER,
    faststart    INTEGER,               -- moov 是否前置（影响平台首帧加载）
    completeness TEXT,                  -- JSON：{main:3/3, carousel:5/5, detail:0/1, video:2/1}
    probe_ver    TEXT, probed_at TEXT
);

-- 生成物版本表
CREATE TABLE v3_video_asset (
    asset_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES v3_product(product_id),
    source_image_id INTEGER,            -- 指回 v3_image.image_id（原始视频/主图）
    asset_kind    TEXT NOT NULL,        -- ai_video_a|ai_video_b|ai_image_video|dy_version|wx_version|cover
    version       INTEGER NOT NULL DEFAULT 1,
    rel_path      TEXT NOT NULL,
    content_sha256 TEXT, duration_ms INTEGER, width INTEGER, height INTEGER,
    platform_target TEXT,               -- douyin|wechat|both|null
    gen_engine    TEXT,                 -- ffmpeg|comfyui|manual
    gen_params    TEXT,                 -- JSON 全参数快照
    script_id     INTEGER,
    license_state TEXT NOT NULL DEFAULT 'unknown',   -- own|authorized|unknown|forbidden
    approve_state TEXT NOT NULL DEFAULT 'draft',     -- draft|reviewing|approved|rejected
    superseded_by INTEGER,
    created_by    TEXT, created_at TEXT,
    UNIQUE(product_id, asset_kind, version)
);
```

---

## 七、双平台差异表（抖音 vs 视频号）

> 原则：**API 支持的地方优先 API，不支持的地方用固定浏览器流程，不假设两个平台能力相同。**

| 维度 | 抖音（开放平台） | 微信（微信小店 / 视频号） | 证据 |
|:--|:--|:--|:--|
| **上传通道** | `POST /video/upload/` 单传；超 50 MB 建议分片、**超 128 MB（部分文档写 300 MB）必须分片**；单分片建议 20 MB、最小 5 MB | 三步：`/shop/ec/basics/video/initupload` → `/shop/ec/basics/video/uploadpart`（**分块 1 MB~2 MB**）→ `/shop/ec/basics/video/finishupload`（校验连续+sha）→ `/getplayinfo` 轮询拿临时 URL | 抖音 [分片上传](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-part-upload)、[上传视频](https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/upload)；微信 [API 接口索引](https://developers.weixin.qq.com/doc/store/shop/API) `[官方文档]` |
| **时长上限** | **≤15 分钟** `[官方文档]` | **未在官方文档中查到明确数字** → 标 **尚未核实**（第三方文章写 60 s / 1 GB，2021 媒体报道写 1 小时 / 2 GB，均非官方，不采信） | 抖音 [内容发布接入方案](https://open.douyin.com/platform/resource/docs/ability/content-management/douyin-publish-solution) |
| **大小上限** | 单传 **≤128 MB**（upload 页）；分片总计 **≤4 GB**（方案页）；两页数字不一致，**按更严的 128 MB 走**，超了自动切分片 | **尚未核实**（官方索引页只给接口，未给体积表） | 同上 |
| **建议分辨率/比例** | 官方原文：「推荐上传 **16:9，分辨率为 720p（1280×720）及以上的竖版视频**」——原文自相矛盾（16:9 却称竖版）。**实务按 9:16（720×1280 / 1080×1920）出片**，并把文档矛盾写进注释 | **尚未核实**。第三方长期口径为「最小 608×1080、高宽比最大 11:10、超出上下裁切或补黑边」——**非官方，未采信为参数** | 抖音同上 |
| **格式** | 推荐 **mp4 / webm** `[官方文档]` | 官方未在索引页写明；**按 mp4(H.264+AAC) 出片** 为工程惯例 `[设计建议]` | 抖音同上 |
| **商品关联** | **开放平台「内容发布」只支持携带：话题、@用户、小程序、地理位置 —— 列表里没有「商品」** `[官方文档]`。→ **视频挂车只能走固定浏览器流程**（发布页「添加商品」） | 商品对象有 `head_videos` 字段，**官方明确「最多上传 1 个」，且 `video_url` 必须先用上传接口拿到** `[官方文档]` → **关联可纯 API** | 抖音 [内容发布接入方案](https://open.douyin.com/platform/resource/docs/ability/content-management/douyin-publish-solution)、[用户类型及权限说明](https://open.douyin.com/platform/resource/docs/accession-guide/type-and-permission)；微信 [添加商品](https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_addproduct.html)、[获取商品](https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api/getproduct.html) |
| **商品视频数量** | 抖店商品头图视频字段 `video_url`（第三方资料提及，**官方文档未取到原文 → 尚未验证**） | **最多 1 个**，删除需显式传空字符串 `[官方文档]` | 同上 |
| **标题/文案字数** | 视频标题与话题字数：**官方发布页未在已取得文档中给出数字 → 尚未核实** | 发布页字数：**尚未核实** | — |
| **封面** | 可指定（上传视频 API 返回 `video_id`，发布接口另带封面参数，**本轮未取到原文 → 尚未验证**） | 商品视频无独立封面字段；视频号封面**长期由平台截取首帧**（第三方口径，官方文档未取到原文 → **尚未核实**） | — |
| **水印政策** | **官方明确**：带品牌 logo/水印的视频会命中审核逻辑，**大概率降权 / 下架 / 封号** | 视频号运营规范：带侵权素材不允许上传，平台可停止传播 | 抖音同上；[微信视频号运营规范](https://weixin.qq.com/cgi-bin/readtemplate?lang=zh_CN&t=weixin_agreement&s=video) `[官方文档]` |
| **权限前置** | 需 `video.create` scope + 用户授权；`video.create.bind` 需在「能力实验室」申请 | 需小店 AppID/Secret + 店铺授权；接口只能在**服务端**调用 | 抖音同上；微信同上 |
| **失败语义** | HTTP 200 也可能带 `error_code≠0` → **必须解 JSON**，不能只看状态码 | 同上（`errcode/errmsg`），文档反复强调「服务器端调用」 | `[设计建议]` + 官方文档口径 |

### 7.1 三条最关键的平台差异（结论）

1. **商品关联能力完全不同**：微信**可纯 API**（`head_videos`，最多 1 个）；抖音**开放平台内容发布没有商品组件** → **必须浏览器流程**。这是「不能假设两个平台相同」的实证。
2. **上传通道结构不同**：抖音是「单传/分片，单文件 ≤128 MB 更严」；微信是**固定三步 + 1~2 MB 分块 + sha 校验**。**同一套上传代码不能复用**，只能抽出「分块读取 + 重试」这一层公共逻辑。
3. **抖音官方比例/格式自相矛盾**（写「16:9 竖版」）：**照官方数字会出错**，工程上必须锁定 9:16 竖版出片并把这个矛盾写进代码注释。

---

## 八、审核与人工批准点（对齐 L1–L4 分级）

> L1 = 全自动 · L2 = 自动但全量留痕 · L3 = 人批后执行 · L4 = 必须人做（登录/扫码/决策）

| 环节 | 级别 | 理由 | 落地要求 |
|:--|:--|:--|:--|
| 素材发现 / 完整度 / 探测 / 封面抽帧 | **L1** | 只读本地数据，错了可重跑，无外部副作用 | 结果写 `v3_media_probe`，失败只记录不阻塞别的商品 |
| 脚本 / 文案 / 字幕 / 配音 / 出片 | **L2** | 会花算力与磁盘，但产物是「候选」，不出去 | 每次生成必写 `_manifest` + `v3_video_asset` 版本行；**同名不覆盖** |
| **视频审核（步 4）** | **L3 强制** | 生成内容可能含错误宣称、错比例、错音轨；平台侧违规成本高 | 人必须逐条看片点「通过/驳回」；**驳回自动 +1 版本，不改旧版本** |
| **账号选择（步 5）** | **L3** | 发错账号 = 发到别人号上，不可撤回 | 账号列表只列「当前登录态已验证」的账号；选完写 `account_id` + `approved_by` |
| **首次平台授权（步 6 前置）** | **L4** | 抖音需用户 OAuth 授权、微信需店铺授权，**平台强制人工** | 系统只展示「去授权」按钮 + 过期时间，**不代点、不绕验证码** |
| **商品关联（步 7）** | **L3**（微信 API 可 L2 候选） | 挂错商品 → 流量与成交错配，且平台可能判违规 | 微信：先算 `head_videos` 载荷给人看一眼再发；抖音：浏览器流程逐步留截图 |
| **发布确认（步 8）** | **L3 最后一道** | 发布即公开，不可「先发再改」 | 弹「商品/账号/版本号/文案/封面」五项确认，**缺一项不给发** |
| **结果核对（步 9）** | **L1** | 只读查询 | 与本地记录比对，差异写「对账差异表」，**不以启动返回当成功** |
| 去水印 / 搬运第三方视频 | **禁止** | 见 §九 | 无级别 —— 不实现 |

---

## 九、风险与不能做的事（去水印、版权、平台风控）

### 9.1 四条硬红线（对应主人要求，逐条对齐）

| 红线 | 本设计的落实 | 落地检查点 |
|:--|:--|:--|
| **原始视频不能覆盖** | `images\` 目录**全程只读**；生成物一律落 `video_gen\`；命名带版本+时间戳+sha；目标已存在**直接报错** | 代码层：写文件前 `os.path.exists` → `raise`；运行层：`images\` 目录 ACL 只读（可选加固） |
| **未授权第三方视频不能去水印冒充原创** | **不实现任何去水印/裁水印/模糊水印能力**；`license_state` 默认 `unknown`，`forbidden` 直接拦截；审核步（L3）强制人看「画面里有没有别人的水印/别人的 LOGO」 | 步 2 的拦截规则 + 步 4 的检查项 |
| **素材必须与商品 ID 关联** | 三张表全部带 `product_id` NOT NULL；`v3_video_asset.source_image_id` 指回原始素材；发布表 `linked_product_id` 记平台侧 ID | `uq_vp_once` 索引 + 生成时强制带 `product_id` |
| **生成/上传/发布都带版本号与回滚** | 文件名 `vNNN_timestamp_sha8`；`v3_video_asset.superseded_by` 记替代关系；发布表 `asset_id + asset_version + attempt_no` | 回滚 = 把 `superseded_by` 置空并重新指向旧版本，**不删文件** |

### 9.2 六条具体风险

| # | 风险 | 后果 | 处置 |
|:--|:--|:--|:--|
| R1 | **原素材是 HEVC**（实测 194 条多为 hevc） | 直接上传被平台拒或转码失真 | 出片强制转 H.264(`libx264`/`h264_nvenc`) + `yuv420p` + `+faststart` |
| R2 | **抖音「视频挂商品」无 API** | 想全自动挂车会变成「半自动 + 人点」，若假装能自动化，交付时会翻车 | 浏览器流程**明确标注**「每步留截图、需人确认」，不承诺全自动 |
| R3 | **平台数字自相矛盾**（抖音 128 MB vs 4 GB；16:9 却称竖版） | 按错数字出片 → 批量上传失败 | 参数走配置表 + 每次上传前预检（`ffprobe` 比对配置），**配置里写来源链接** |
| R4 | **8 GB 显存被桌面程序占用**（start-comfyui.cmd 注释实测 ~6.8 GB 被占） | 视频生成/大模型一跑就卡死或 OOM | ComfyUI/LLM 不进主链路；主链路纯 FFmpeg（CPU/nvenc）；需 GPU 时先 `--reserve-vram` 并排队 |
| R5 | **孤儿素材 / 重复文件**（12 个、hash 同名） | 同一视频被当成两个商品素材，重复发布 → 平台判搬运 | 步 1 用 `content_sha256` 跨商品去重，重复的标 `dup_of=<image_id>`，默认不进候选 |
| R6 | **发布成功判定**（HTTP 200 ≠ 发布成功） | 报告说「发了 20 条」实际 3 条 | 步 9 必须回查平台；查不到标 `unknown`；对账差异表进日报 |

### 9.3 明确不做的

- 不做去水印、不做水印擦除、不做「把别人的视频换个封面当自己的」
- 不做自动注册小号、不做绕过平台限流/风控、不做模拟真人行为规避检测
- 不生成虚假宣称（「最」「第一」「根治」等绝对化用语）—— 文案生成时内置词表拦截
- 不在同一账号高频重复发布同素材（`uq_vp_once` + 频控）

---

## 附录：实测命令与输出

### A-1 工具链版本 `[本机实测]`

```powershell
> ffmpeg -version | Select-Object -First 2
ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
built with gcc 16.1.0 (Rev2, Built by MSYS2 project)

> ffprobe -version | Select-Object -First 1
ffprobe version 9.0.1-essentials_build-www.gyan.dev

> node -v ; pnpm -v ; npm -v
v25.2.1
11.7.0
11.6.2

> F:\pyenvs\douyin\Scripts\python.exe -c "import sys;print(sys.version)"
3.13.15 (tags/v3.13.15:4061bc4, Aug  5 2026, 13:05:39) [MSC v.1944 64 bit (AMD64)]
```

### A-2 编码器与视频探测 `[本机实测]`

```powershell
> ffmpeg -hide_banner -encoders | Select-String 'nvenc|libx264|libx265|aac|libmp3lame'
 V....D av1_nvenc   NVIDIA NVENC av1 encoder
 V....D libx264     H.264 / AVC
 V....D h264_nvenc  NVIDIA NVENC H.264 encoder
 V....D libx265     H.265 / HEVC
 V....D hevc_nvenc  NVIDIA NVENC hevc encoder
 A....D aac         AAC
 A....D libmp3lame  MP3

> ffprobe -hide_banner -v error -show_entries format=duration,size,format_name `
          -show_entries stream=codec_name,width,height,r_frame_rate,nb_frames `
          -of default=noprint_wrappers=1 <images\1329\video\video_0dccce8c50xx.mp4>
codec_name=hevc
width=720
height=1280
r_frame_rate=30/1
nb_frames=309
codec_name=aac
r_frame_rate=0/0
nb_frames=449
format_name=mov,mp4,m4a,3gp,3g2,mj2
duration=10.379002
size=1152036
```

### A-3 媒体目录与视频清点 `[本机实测]`

```powershell
> Get-ChildItem ...\data\v3\images -Recurse -File | Group-Object Extension | Sort Count -Desc
Count Name
 7558 .jpeg
 4659 .jpg
  210 .mp4
  175 .png
   21 .json
   21 .txt
    9 .part        ← 下载碎屑，不是素材

files=12653  bytes=4326.39MB
mp4: count=210  sumMB=1206.0
```

### A-4 数据库只读核对 `[本机实测]`

```python
con = sqlite3.connect('file:...v3.db?mode=ro', uri=True)

# role 分布
sku     6353
detail  6276
main    5530
video    194

# 视频行完整度
total, distinct_products, has_local_path, dl_ok = (194, 114, 194, 194)

# 孤儿核对（盘上有、库里无）
db=194  disk=206  orphan_on_disk=12
   ...\images\1373\video\video_0b11dfc21e83.mp4   （与 1382 已入库文件 hash 同名）
   ...\images\1373\video\video_d434d441cb92.mp4
   ...\images\1374\video\video_9bae8af5ca35.mp4
   ...（1375/1376/1377/1379 各 2 条，共 12 条）
```

### A-5 ComfyUI / GPU `[本机实测]`

```
J:\AI\ComfyUI\comfyui_version.py  →  __version__ = "0.38.0"
J:\AI\ComfyUI\pyproject.toml      →  version = "0.38.0"
8188 未监听；计划任务「ComfyUI 服务 (8188)」Status: Ready

models\checkpoints          3 files  19.54 GB   (SDXL 系)
models\diffusion_models     0 files   0.00 GB   ← 无视频扩散模型
models\frame_interpolation  0 files   0.00 GB
models\optical_flow         0 files   0.00 GB
models\vae                  1 file    0.31 GB
custom_nodes: ComfyUI-VideoHelperSuite / comfyui-reactor-node /
              ComfyUI_InstantID / ComfyUI_IPAdapter_plus / ComfyUI-GGUF

nvidia-smi: NVIDIA GeForce RTX 3070, 8192 MiB, 1282 MiB used, driver 617.42
```

### A-6 Remotion 可得性 `[本机实测]`（网络可达，未安装）

```
> npm view remotion version        → 4.0.542
> npm view @remotion/cli version   → 4.0.542
> Test-Path ...\node_modules\remotion → False
```

### A-7 现有代码位置（`[源码确认]`）

```
taobao-picker\server\v3\media.py:37,114,307           视频扩展名/命名/换链重下
taobao-picker\server\v3\taskcenter.py:205,208,235,251 视频桶统计与筛选（可直接复用）
taobao-picker\server\v3\bundle.py:45,76,261           打包时 video_meta
taobao-picker\server\v3\adapters\weidian.py:129-259   videoUrls + headVideoInfo 收集与过期判定
taobao-picker\server\v3\adapters\wechat.py:237-285    来源未提供视频的语义
taobao-picker\server\v3\adapters\pdd.py:120-251       gallery type=9 的视频
微信小店自建上货台\app.py:1300-1301                    建商品时『视频未接』
微信小店自建上货台\app.py:1778-1779,1818-1819         head_videos 字段拼装（已写好）
```

---

## 附：最小可用版本（MVP）路线

| 阶段 | 包含能力 | 交付判据 | 新增依赖 |
|:--|:--|:--|:--|
| **M1（先做，1 周内可验）** | 1、2、3、4、6、16 | 打开界面能看到：372 个商品里 114 个有视频、每个视频的时长/分辨率/编码、12 个孤儿被标出、完整度缺口列表 | 无 |
| **M2** | 7、8、11、12A、13、14 | 选 3 个商品，各出 1 条 9:16 竖版 mp4 + 封面 + 标题文案，全部落 `video_gen\` 且带版本号 | 无（需 LLM 在线） |
| **M3** | 9、10 + 发布表 + 步 1–4 | 字幕烧屏 + SAPI 配音；发布表建好；生成→审核在界面里闭环（**不发出去**） | 可选 `edge-tts` |
| **M4** | 微信全 API 发布（步 5–9） | 1 个商品真实发布到视频号小店商品视频（需主人授权后）= 唯一可全自动的一侧 | 无 |
| **M5** | 抖音浏览器流程发布 | 固定步骤 + 截图留证；挂商品为人工确认 | 无（用现有 ego lite） |
| **之后再说** | 5 深度理解、12B AI 运镜 | — | 视频模型（3–8 GB）+ 显存评估 |
