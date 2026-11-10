---
name: github-project-push
description: 把本机任意项目一条命令打包并上传到 GitHub 私有仓库。当用户说「上传到 GitHub / 推到仓库 / 同步到 GitHub / 一个提示词上传项目 / 把本地项目发到 GitHub / 建仓库并推送 / 打包给外部 AI 看架构」，或要求把 skill / 插件 / 源码目录做成可复用仓库时使用。默认私有、默认脱敏、默认不污染原项目（在临时副本里建 git），推送前强制密钥终检，推送后回读远端 HEAD 校验。
---

# github-project-push — 本地项目 → GitHub 私有仓库

## 一句话

**主脚本 `scripts/push.ps1`：吃一个本地目录（或一份多根清单），在独立 staging 副本里做排除 / 脱敏 / 建仓 / 提交 / 推送 / 回读，原项目一个字节不改。**

## 何时用

- 「整个项目帮我上传到 GitHub」「把 X 推到仓库」「以后我一句提示词就传」
- 要把某个 skill / 插件 / 工具链做成仓库给外部 AI 看
- 已上传过，想增量同步一次

## 硬约束（脚本强制，违反即中止）

1. **默认私有**（`--private`）。要公开必须显式传 `-Visibility public`。
2. **不取明文令牌**：`gh` 已登录 keyring（`gh auth status` / `gh api user` 实测）。脚本绝不用 `gh auth token` 写文件、绝不把 token 拼进 remote URL。凭据只通过 `git config --local credential."https://github.com".helper "!gh auth git-credential"` 从 keyring 现取。
   - 知识库《GitHub Token 总表.md》只作为**续期参考**；令牌过期/失效时提示主人去续，不自动复制明文。
3. **原项目零改动**：`git init` / `.git` / `.gitignore` / `README-GITHUB.md` / `_GITHUB_MANIFEST.json` 全部只写在 staging 副本里。生产目录不建 `.git`、不改任何文件。
4. **密钥终检 0 命中才允许推送**：扫 `gh[pousr]_*` / `github_pat_*` / `sk-` / `AKIA` / `-----BEGIN` / `app_secret` / `password=` / `token=` / `Bearer ` 等。命中只报**文件名 + 行号 + 类别**，不打印明文。
5. **硬排除**：`node_modules` / `__pycache__` / `.git` / `dist` / `build` / `.codegraph` / `*.db` / `*.sqlite*` / `*.pyc` / `*.pyd` / `*.dll` / `*.exe` / `*.mp4` / `*.zip` / `*.7z` / `*.bak*` / 所有 `_备份*` `_基线*` `_stage*` `_验_*` `_探_*` `_测_*` `_拍_*` `_apply_*` `_patch_*` `_tmp*` `_iso_*` `_probe*` 探针与快照目录 / `shops.json` / `config.json` / `token.txt` / `.env` / `*.pem` / `*.key` / 任何单文件 > `-MaxFileMB`（默认 8）。
   **★ 浏览器运行态（2026-11-11 实测踩过）**：证据目录里常常把整个 `chrome-profile/` / `profile/` 一起拷进来，里面有 `Cookies`、`Login Data`、`Web Data`、`History`、`Network Persistent State`、`Trust Tokens`、`Local Storage`、`Sync Data`、`Service Worker/Database`。这些**绝不能上仓库**（含登录态）。脚本按「目录名 + 文件名」双规则硬挡 `chrome-profile*` 整棵树与上述凭据文件名（含 `-journal` 变体）；截图 `.png` / 接口快照 `.json` 保留。**推完必须再跑一次远端 tree 核验 `Cookies|Login Data` 命中数 = 0。**
6. **脱敏重写**：首轮密钥正则命中不直接中止，而是把「敏感字段 = 值」的值就地替换为 `===REDACTED===`（保留字段名与结构），重写后复检；复检仍命中才中止。命中项只报「文件:行 + 类别」，不打印明文。
6. **不改全局 git 配置**：user / credential helper 都只写 staging 的 `--local`。
7. **不自动 PR / 不自动 release / 不自动删仓库**。

## 执行步骤（AI 照做）

```
① 前置实测：gh auth status → gh api user --jq .login → git --version
   （任一失败 → 停下报告，不硬推）
② 选范围：
   单根项目 → -Path <绝对路径>
   跨目录项目 → -BundleManifest <清单.json>（格式见下）
③ 先 -DryRun 跑一次，看「纳入文件数 / 总体积 / 硬排除命中 / 密钥命中」
④ 去掉 -DryRun 实跑
⑤ 实跑后必做回读：脚本内已做；AI 再补一条 gh repo view 确认 visibility=PRIVATE
```

## 参数

| 参数 | 默认 | 说明 |
|:--|:--|:--|
| `-Path` | — | 单根项目绝对路径（与 `-BundleManifest` 二选一） |
| `-BundleManifest` | — | 多根清单 JSON |
| `-Name` | 目录名 | 仓库名，自动转 kebab-case |
| `-Owner` | `gh api user` | 账号 |
| `-Visibility` | `private` | private / public / internal |
| `-Description` | 自动生成 | 仓库描述 |
| `-MaxFileMB` | `8` | 单文件上限，超过即排除并记入 skip |
| `-Message` | `upload: <name> @ <ts>` | 提交信息 |
| `-Staging` | `$env:TEMP\ghpush-*` | staging 目录；想留档就显式指到工作区 |
| `-DryRun` | 关 | 只组装 + 终检，不建仓不推送 |
| `-ReportPath` | staging 内 | 机读回执 JSON 落点 |

## BundleManifest 格式

```json
{
  "name": "my-project",
  "description": "一句话",
  "sources": [
    { "path": "G:\\...\\projA\\app", "dest": "A-core/app" },
    { "path": "G:\\...\\projB",       "dest": "B-adapter" }
  ],
  "files": [
    { "path": "G:\\...\\projA\\run.pyw", "dest": "A-core/run.pyw" }
  ]
}
```

`sources` 递归收目录，`files` 收单文件；`dest` 是包内相对路径。两者都过同一套排除与终检。

## 回执必须报（脚本输出 JSON，AI 复述）

```
repo_url / visibility / owner / name
commit_sha_local / commit_sha_remote / head_match (true 才算成功)
included_files / included_bytes / skipped_files / skipped_reasons
secret_hits (必须 0) / excluded_rule_hits
staging_path / manifest_path / report_path
```

## 已知环境事实（本机 2026-11-11 实测）

```
gh 2.87.3 · git 2.56.0.windows.3 · 账号 sunxiaohu66322 (id 130741856)
keyring 已登录 classic PAT，scope: repo / workflow / read:org
git http.proxy = https.proxy = http://127.0.0.1:7890  ← 必须走代理
知识库凭证表 G:\Users\Administrator\Documents\BaiduSyncdisk\obsidian\05-----------企业资料\计算机AI 相关\API 信息\GitHub Token 总表.md
  ※ 表内 classic PAT 标注 2026-11-22 到期；到期前续期，脚本不读取该文件内容。
```

## 增量同步（仓库已存在）

再跑一次同 `-Name` 即可：脚本检测到远端已存在 → 复用 remote → 新 staging 提交 → `git push -f` **不会**自动做（默认拒绝覆盖历史，避免误伤）。
需要覆盖时显式传 `-ForcePush`，并在报告里写明原因。

## 陷阱（踩过的）

- **别在原项目目录 `git init`**：上货台 436MB / 6508 文件，含 `dist\` 263MB 与 `v3.db` 业务库；`git add .` 会把编译产物和数据库一起塞进去。
- **别把 `shops.json` 传上去**：它含两个微信小店 appid/secret 明文。
- **`gh` 登录 ≠ `git push` 能认证**：必须给 staging 配 local credential helper，否则 push 会卡在要密码。
- **单文件 >100MB GitHub 直接拒**：脚本默认 8MB 上限提前挡掉。
- **「查不到密钥」不等于「没有密钥」**：终检用的是正则，扫完成功才写进回执，别口头保证。
