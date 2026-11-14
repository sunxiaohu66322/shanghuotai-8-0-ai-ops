#Requires -Version 5.1
<#
  github-project-push · sync.ps1（版本化增量同步）
  ------------------------------------------------------------------
  把「已登记项目」的本轮改动，作为远端历史之上的一个**新版本**推上去：
      1 个 commit + 1 个 annotated tag vN + CHANGELOG.md 顶部一条 + VERSIONS.md 一行
  绝不清空 .git、绝不 force push、生产真源零改动。
  组装阶段复用 push.ps1 -DryRun 的「硬排除 / 脱敏重写 / 密钥终检」，不重复造轮子。

  用法：
    sync.ps1 -Project shanghuotai-8-0-ai-ops -Note "本轮：新增每日巡店脚本..."
    sync.ps1 -Project shanghuotai-8-0-ai-ops -DryRun      # 只看会生成什么版本/哪些改动
    sync.ps1 -Note "..."                                   # 只登记了一个项目时可省 -Project
    sync.ps1 -Note "..." -Force                            # 仅时间戳变化时也出一个版本
#>
[CmdletBinding()]
param(
  [string]$Project = '',
  [string]$Note = '',
  [string]$Registry,
  [switch]$DryRun,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Write-Step($m) { Write-Host "[sync] $m" }
function Fail($m) { throw "[sync][ABORT] $m" }

$skillDir = Split-Path -Parent $PSScriptRoot
$pushScript = Join-Path $skillDir 'scripts\push.ps1'
if (-not (Test-Path -LiteralPath $pushScript)) { Fail "缺 push.ps1：$pushScript" }
if (-not $Registry) { $Registry = Join-Path $skillDir 'registry.json' }
if (-not (Test-Path -LiteralPath $Registry)) { Fail "登记表不存在：$Registry（先用 register.ps1 登记项目）" }

# ---------- 1. 读登记表 ----------
$reg = Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json
$keys = @($reg.projects.PSObject.Properties.Name)
$key = $Project
if (-not $key) {
  if ($keys.Count -eq 1) { $key = $keys[0] }
  else { Fail "登记表有 $($keys.Count) 个项目，请指定 -Project：$($keys -join ', ')" }
}
$prop = $reg.projects.PSObject.Properties[$key]
if (-not $prop) { Fail "未登记项目：$key（现有：$($keys -join ', ')）" }
$p = $prop.Value
$full = "$($p.owner)/$($p.name)"
$url = "https://github.com/$full.git"
$wt = $p.work_tree
$manifest = $p.manifest
$rootDocs = $p.root_docs
if (-not (Test-Path -LiteralPath $manifest)) { Fail "清单不存在：$manifest" }
Write-Step "项目 = $key → $full（工作树 $wt）"

# ---------- 2. 前置实测 ----------
& git --version 2>&1 | Out-Null; if ($LASTEXITCODE -ne 0) { Fail 'git 不可用' }
& gh --version 2>&1 | Out-Null;  if ($LASTEXITCODE -ne 0) { Fail 'gh 不可用' }
$auth = (& gh auth status 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $auth -notmatch 'Logged in') { Fail "gh 未登录：`n$auth" }
& gh repo view $full --json name 2>&1 | Out-Null
$repoExists = ($LASTEXITCODE -eq 0)

# ---------- 3. 版本号：max(登记表, 远端已有 tag) + 1 ----------
$vmax = 0
$tagLines = @(& git ls-remote --tags $url 2>$null)
foreach ($l in $tagLines) { if ($l -match 'refs/tags/v(\d+)(\^\{\})?\s*$') { $n = [int]$Matches[1]; if ($n -gt $vmax) { $vmax = $n } } }
$lastV = 0; try { $lastV = [int]$p.last_version } catch {}
$N = [math]::Max($lastV, $vmax) + 1
$tag = "v$N"
Write-Step "版本号 = $tag（登记表 last_version=$lastV / 远端最大 tag=$vmax）"

# ---------- 4. 保证「带完整历史」的持久工作树 ----------
if (-not (Test-Path -LiteralPath (Join-Path $wt '.git'))) {
  if (-not $repoExists) {
    Write-Step '远端仓库不存在 → 调 push.ps1 做首次全量上传，并在工作树留下 .git'
    & $pushScript -BundleManifest $manifest -Name $p.name -Owner $p.owner -Visibility $p.visibility `
      -Staging $wt -Message "init: $($p.name) baseline (github-project-push)" 2>&1 | Out-Host
    if (-not (Test-Path -LiteralPath (Join-Path $wt '.git'))) { Fail '首次全量上传未完成（无 .git）' }
  } else {
    if (Test-Path -LiteralPath $wt) {
      $aside = "$wt.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
      Move-Item -LiteralPath $wt -Destination $aside
      Write-Step "旧工作树无 .git，已挪到 $aside"
    }
    & git clone $url $wt 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'git clone 失败' }
  }
}
# 工作树是可丢弃的镜像：对齐到远端 main（这样历史连续、不会串进本地脏文件）
& git -C $wt fetch origin --tags 2>&1 | Out-Null
& git -C $wt reset --hard origin/main 2>&1 | Out-Null
& git -C $wt clean -fd 2>&1 | Out-Null
& git -C $wt config --local core.quotePath false 2>&1 | Out-Null
& git -C $wt config --local credential.helper '!gh auth git-credential' 2>&1 | Out-Null
$un = (& git config --global --get user.name 2>&1 | Out-String).Trim(); if (-not $un) { $un = $p.owner }
$ue = (& git config --global --get user.email 2>&1 | Out-String).Trim(); if (-not $ue) { $ue = "$($p.owner)@users.noreply.github.com" }
& git -C $wt config --local user.name $un 2>&1 | Out-Null
& git -C $wt config --local user.email $ue 2>&1 | Out-Null
$parentSha = (& git -C $wt rev-parse HEAD 2>&1 | Out-String).Trim()
Write-Step "基线 commit = $($parentSha.Substring(0, [math]::Min(9, $parentSha.Length)))"

# ---------- 5. 组装：复用 push.ps1 -DryRun（排除 / 脱敏 / 密钥终检） ----------
$assemble = Join-Path $env:TEMP ("ghpush-asm-$($p.name)-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Step "组装 → $assemble（复用 push.ps1 -DryRun）"
$asmJson = ''
try {
  $out = & $pushScript -BundleManifest $manifest -Name $p.name -Owner $p.owner `
    -Visibility $p.visibility -Staging $assemble -DryRun
  foreach ($o in @($out)) { if ($o -is [string] -and $o.TrimStart().StartsWith('{')) { $asmJson = $o } }
} catch { Fail "组装 / 密钥终检失败：$($_.Exception.Message)" }
if (-not $asmJson) { Fail '未取到 push.ps1 DryRun 回执' }
$asm = $asmJson | ConvertFrom-Json
if ([int]$asm.secret_hits -ne 0) { Fail "密钥终检命中 $($asm.secret_hits) 处，已中止" }
Write-Step "组装 PASS：纳入 $($asm.included_files) / 排除 $($asm.skipped_files) / secret_hits=0"

# ---------- 6. 把组装结果镜像进工作树（精确：多余的删、变化的盖） ----------
$desired = @{}
foreach ($f in @(Get-ChildItem -LiteralPath $assemble -Recurse -File -Force)) {
  $rel = $f.FullName.Substring($assemble.Length).TrimStart('\','/') -replace '\\','/'
  if ($rel -eq '.git' -or $rel.StartsWith('.git/')) { continue }
  # 一次性回执不进仓库（push.ps1 DryRun 生成，带时间戳，属噪音）
  if ($rel -eq '_GITHUB_REPORT.json') { continue }
  # 本机运行态登记表 / state 绝不进仓库：sync 成功后会回写 registry.json，
  # 若收录，则每次都被判「有改动」→ 无限刷版本。这是版本化系统的第二个命门。
  if ($rel -match 'github-project-push/registry\.json$' -or $rel -match 'github-project-push/state/') { continue }
  $desired[$rel] = $f.FullName
}
if (Test-Path -LiteralPath $rootDocs) {
  foreach ($f in @(Get-ChildItem -LiteralPath $rootDocs -File -Force)) { $desired[$f.Name] = $f.FullName }
}
foreach ($f in @(Get-ChildItem -LiteralPath $wt -Recurse -File -Force)) {
  $rel = $f.FullName.Substring($wt.Length).TrimStart('\','/') -replace '\\','/'
  if ($rel.StartsWith('.git/')) { continue }
  # 版本历史文件由本脚本自己维护（第 9 步），绝不能在镜像阶段当「多余文件」删掉；
  # 否则每轮只剩最新一条，第三方 AI 就看不到累积的更改记录 —— 这是版本化系统的命门。
  if ($rel -in @('CHANGELOG.md','VERSIONS.md')) { continue }
  if (-not $desired.ContainsKey($rel)) { Remove-Item -LiteralPath $f.FullName -Force }
}
foreach ($rel in $desired.Keys) {
  $dst = Join-Path $wt $rel
  $pd = Split-Path -Parent $dst
  if (-not (Test-Path -LiteralPath $pd)) { New-Item -ItemType Directory -Path $pd -Force | Out-Null }
  Copy-Item -LiteralPath $desired[$rel] -Destination $dst -Force
}

# ---------- 7. 计算「有意义的改动」（排除仅时间戳变化的生成文件） ----------
& git -C $wt add -A 2>&1 | Out-Null
$changes = @()
foreach ($l in @(& git -C $wt diff --cached --name-status 2>&1)) {
  if (-not $l) { continue }
  $parts = $l -split "`t"
  if ($parts.Count -ge 2 -and $parts[0] -match '^[AMDRT]') {
    $changes += [pscustomobject]@{ s = $parts[0]; f = $parts[-1] }
  }
}
$generated = @('README-GITHUB.md','_GITHUB_MANIFEST.json','_GITHUB_REPORT.json')
$meaningful = @($changes | Where-Object { $generated -notcontains $_.f })
Write-Step "改动 $($changes.Count) 项，其中有意义 $($meaningful.Count) 项"

$now = Get-Date
$ts = $now.ToString('yyyy-MM-dd HH:mm:ss zzz')
$noteShort = if ($Note) { ($Note -replace "`r?`n", ' ').Trim() } else { "同步 $($meaningful.Count) 处文件改动" }
if ($noteShort.Length -gt 160) { $noteShort = $noteShort.Substring(0,160) }

# ---------- 8. 无实质改动 → 不制造垃圾版本 ----------
if ($meaningful.Count -eq 0 -and -not $Force) {
  & git -C $wt reset --hard HEAD 2>&1 | Out-Null
  & git -C $wt clean -fd 2>&1 | Out-Null
  Write-Step '仅有生成文件时间戳变化 → 跳过版本（可用 -Force 强制）'
  $skip = [ordered]@{
    ok = $true; skipped = $true; project = $key; repo = $full; version = $null; tag = $null
    would_be_tag = $tag; reason = 'no meaningful change'; baseline_sha = $parentSha
    generated_only_changes = $changes.Count; time = $ts
  }
  Write-Output ($skip | ConvertTo-Json -Depth 8)
  return
}

if ($DryRun) {
  & git -C $wt reset --hard HEAD 2>&1 | Out-Null
  & git -C $wt clean -fd 2>&1 | Out-Null
  Write-Step 'DryRun：不写 CHANGELOG / 不提交 / 不推送 / 不改登记表'
  $dr = [ordered]@{
    ok = $true; dry_run = $true; project = $key; repo = $full; would_be_tag = $tag
    baseline_sha = $parentSha; note = $noteShort
    included_files = $asm.included_files; excluded_files = $asm.skipped_files; secret_hits = 0
    changes = @($changes | ForEach-Object { "$($_.s) $($_.f)" })
  }
  Write-Output ($dr | ConvertTo-Json -Depth 8)
  return
}

# ---------- 9. 写 CHANGELOG.md（顶部追加一条）与 VERSIONS.md（追加一行） ----------
$header = @"
# 版本变更记录（CHANGELOG）

> 本仓库由 ``github-project-push`` 版本化同步维护。**每次同步 = 远端历史之上的一个新 commit + 一个 tag**。
> 第三方 AI / 协作者：从最新一条往下读，就能看到这套系统每一次改了什么、为什么改、改动落在哪些文件。
> 看某一版完整差异：``git show <tag>``，或 GitHub 的 ``/commits/<tag>`` 页。
> 「基线 commit」= 这一版的上一版提交；本版 SHA 用 ``git rev-parse <tag>`` 取（提交自身不能写自己的 SHA）。
> 本文件与 ``VERSIONS.md`` 自身的更新不重复计入下面的改动清单。
> 注：``raw.githubusercontent.com`` 有 CDN 缓存，刚推完的几分钟内可能读到上一版；
> 想确认最新版，请看仓库根本文件的**网页渲染**或 ``/tags`` 页（实时），或 ``git show <tag>``。
"@

$lines = @("## $tag · $ts · $noteShort", '')
$lines += "- 标签：``$tag`` ｜ 基线 commit（上一版）：``$($parentSha.Substring(0,[math]::Min(9,$parentSha.Length)))`` ｜ 密钥终检：**0 命中** ｜ 纳入 **$($asm.included_files)** 文件 / 排除 **$($asm.skipped_files)**"
$lines += "- 本版有意义的改动 **$($meaningful.Count)** 项（A 新增 / M 修改 / D 删除 / R 改名）："
if ($meaningful.Count -eq 0) { $lines += '  - （仅生成文件时间戳变化，-Force 触发）' }
else {
  foreach ($c in ($meaningful | Select-Object -First 60)) { $lines += "  - ``$($c.s)`` ``$($c.f)``" }
  if ($meaningful.Count -gt 60) { $lines += "  - …另有 $($meaningful.Count - 60) 项，见 ``git show $tag --stat``" }
}
$lines += "- 本版提交 SHA：``git rev-parse $tag``；改动记录同步维护在 ``VERSIONS.md`` 与仓库 tags 页。"
$entry = ($lines -join "`n")

$chgPath = Join-Path $wt 'CHANGELOG.md'
$old = if (Test-Path -LiteralPath $chgPath) { Get-Content -LiteralPath $chgPath -Raw -Encoding UTF8 } else { '' }
$body = ''
if ($old) { $idx = $old.IndexOf('## v'); if ($idx -ge 0) { $body = $old.Substring($idx).TrimEnd() } }
$newChg = $header + "`n`n" + $entry + "`n`n" + $body + "`n"
Set-Content -LiteralPath $chgPath -Value $newChg -Encoding UTF8

$verPath = Join-Path $wt 'VERSIONS.md'
$oldV = if (Test-Path -LiteralPath $verPath) { Get-Content -LiteralPath $verPath -Raw -Encoding UTF8 } else { '' }
$rows = @()
if ($oldV) { foreach ($l in ($oldV -split "`n")) { if ($l -match '^\|\s*v\d+\s*\|') { $rows += $l.TrimEnd() } } }
$newRow = "| $tag | $ts | $noteShort | $($parentSha.Substring(0,[math]::Min(9,$parentSha.Length))) | $($meaningful.Count) |"
$verDoc = @"
# 版本索引（VERSIONS）

| 版本 | 时间 | 本轮说明 | 基线 commit | 有意义改动数 |
|:--|:--|:--|:--|--:|
$newRow
$(if ($rows.Count) { $rows -join "`n" })

> 完整改动清单看 ``CHANGELOG.md``；某一版完整差异 ``git show <tag>``。
> 版本规则由 ``github-project-push`` skill 的 ``registry.json`` 定义：vN 递增、每次一个新 commit + 一个 tag、绝不清空历史。
"@
Set-Content -LiteralPath $verPath -Value $verDoc -Encoding UTF8

# ---------- 10. 提交 + 打 tag + 推送（无 force） ----------
& git -C $wt add -A 2>&1 | Out-Null
$msg = "release: $tag · $noteShort | github-project-push versioned sync"
& git -C $wt commit -m $msg 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'git commit 失败' }
$sha = (& git -C $wt rev-parse HEAD 2>&1 | Out-String).Trim()
& git -C $wt tag -a $tag -m "$tag | $ts | sha=$sha | $noteShort" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "打 tag $tag 失败" }
Write-Step "本版 commit = $($sha.Substring(0,9)) / tag = $tag"

& git -C $wt push origin HEAD:refs/heads/main 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'push commit 失败（未使用 force）' }
& git -C $wt push origin "refs/tags/$tag" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "push tag $tag 失败" }

# ---------- 11. 回读校验 ----------
$remoteLine = (& git ls-remote $url refs/heads/main 2>&1 | Out-String).Trim()
$remoteSha = ($remoteLine -split '\s+')[0]
$tagLine = (& git ls-remote $url "refs/tags/$tag" 2>&1 | Out-String).Trim()
$tagOk = ($tagLine.Length -gt 0 -and $tagLine -match "$tag\s*$")
$headMatch = ($remoteSha -eq $sha)
$vis = (& gh repo view $full --json visibility --jq '.visibility' 2>&1 | Out-String).Trim()
Write-Step "回读：remote=$($remoteSha.Substring(0,[math]::Min(9,$remoteSha.Length))) match=$headMatch tag_pushed=$tagOk visibility=$vis"
if (-not $headMatch) { Fail '远端 HEAD 与本地不一致' }
if (-not $tagOk) { Fail "远端未见 tag $tag" }

# ---------- 12. 回写登记表（跨会话靠它记住状态） ----------
$hist = @($p.history)
$hist += [ordered]@{ version = $N; tag = $tag; sha = $sha; time = $ts; note = $noteShort;
                     changed = $meaningful.Count; baseline = $parentSha }
$p.last_version = $N
$p.last_sha = $sha
$p.last_sync = $now.ToString('o')
$p.history = $hist
$reg | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Registry -Encoding UTF8

$receipt = [ordered]@{
  ok = $true; project = $key; repo = $full; version = $N; tag = $tag
  commit_sha_local = $sha; commit_sha_remote = $remoteSha; head_match = $headMatch
  tag_pushed = $tagOk; visibility = $vis; baseline_sha = $parentSha
  note = $noteShort; changed_files = $meaningful.Count
  included_files = $asm.included_files; excluded_files = $asm.skipped_files; secret_hits = 0
  changelog_url = "https://raw.githubusercontent.com/$full/main/CHANGELOG.md"
  versions_url = "https://raw.githubusercontent.com/$full/main/VERSIONS.md"
  repo_url = "https://github.com/$full"; registry = $Registry; time = $ts
}
Write-Output ($receipt | ConvertTo-Json -Depth 8)
