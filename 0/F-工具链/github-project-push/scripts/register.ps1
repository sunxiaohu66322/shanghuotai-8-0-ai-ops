#Requires -Version 5.1
<#
  github-project-push · register.ps1（项目登记）
  ------------------------------------------------------------------
  主人说「这套系统需要上传到 GitHub」时用它把项目登记进 registry.json。
  登记后，此项目每一次对话收尾都必须跑 sync.ps1 出一个新版本。

  用法（新对话照抄，只改 4 项）：
    register.ps1 -Project my-proj -Manifest "<BundleManifest.json>" `
      -RootDocs "<仓库根文档目录>" -WorkTree "<持久工作树>" `
      -Owner sunxiaohu66322 -Visibility public -Note "登记原因"

  行为：
    ① 校验 manifest 存在、可解析、sources/files 路径真实存在；
    ② RootDocs 不存在时，若 WorkTree 已有根目录文档则复制过去（防止增量重建丢文档）；
    ③ 写 registry.json（幂等：已登记则只更新字段，不重置 last_version/history）；
    ④ 输出登记回执；不建仓、不推送、不碰生产文件。
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Project,
  [Parameter(Mandatory)][string]$Manifest,
  [string]$RootDocs,
  [string]$WorkTree,
  [string]$Owner,
  [ValidateSet('private','public','internal')][string]$Visibility = 'public',
  [string]$Registry,
  [string]$Note = '',
  [string]$RepoName
)

$ErrorActionPreference = 'Stop'
$skillDir = Split-Path -Parent $PSScriptRoot
if (-not $Registry) { $Registry = Join-Path $skillDir 'registry.json' }
function Fail($m) { throw "[register][ABORT] $m" }

# ---------- 1. 校验 manifest ----------
if (-not (Test-Path -LiteralPath $Manifest)) { Fail "清单不存在：$Manifest" }
try { $m = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Fail "清单不是合法 JSON：$($_.Exception.Message)" }
$srcs = @($m.sources) + @($m.files)
if ($srcs.Count -eq 0) { Fail '清单里没有 sources / files' }
$missing = @()
foreach ($s in $srcs) { if ($s.path -and -not (Test-Path -LiteralPath $s.path)) { $missing += $s.path } }
if ($missing.Count -gt 0) { Fail "清单里有 $($missing.Count) 个路径不存在：`n$($missing -join "`n")" }

# ---------- 2. 默认值 ----------
if (-not $Owner) {
  $Owner = (& gh api user --jq '.login' 2>&1 | Out-String).Trim()
  if (-not $Owner -or $Owner -match '^(error|GraphQL|HTTP)') { Fail '取不到 gh 账号，请显式给 -Owner' }
}
if (-not $RepoName) { $RepoName = ($Project.ToLower() -replace '[^a-z0-9]+','-').Trim('-') }
if (-not $WorkTree) { $WorkTree = Join-Path $env:TEMP ("ghpush-$RepoName-wt") }
if (-not $RootDocs) { $RootDocs = Join-Path (Split-Path -Parent $Manifest) "$RepoName-仓库根文档" }

# ---------- 3. 根文档真源：不存在就从工作树抢救 ----------
if (-not (Test-Path -LiteralPath $RootDocs)) {
  New-Item -ItemType Directory -Path $RootDocs -Force | Out-Null
  $docNames = @('README.md','llms.txt','00_读我_AI-FIRST.md','01_架构.md','02_现状与边界.md','03_怎么改.md','CHANGELOG.md','VERSIONS.md')
  $copied = 0
  if (Test-Path -LiteralPath $WorkTree) {
    foreach ($n in $docNames) {
      $src = Join-Path $WorkTree $n
      if ((Test-Path -LiteralPath $src) -and -not (Test-Path -LiteralPath (Join-Path $RootDocs $n))) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $RootDocs $n) -Force; $copied++
      }
    }
  }
  Write-Host "[register] 根文档目录新建：$RootDocs（从工作树抢救 $copied 份）"
} else { Write-Host "[register] 根文档真源：$RootDocs" }

# ---------- 4. 写 registry（幂等） ----------
if (Test-Path -LiteralPath $Registry) {
  $reg = Get-Content -LiteralPath $Registry -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
  $reg = [pscustomobject]@{
    _readme = 'github-project-push 版本化同步登记表。projects 以注册表 key 索引；每次 sync.ps1 成功后回写 last_version / last_sha / last_sync / history。跨会话靠本文件落盘记住状态，不靠会话记忆。'
    versioning = [pscustomobject]@{
      scheme = 'vN（N 从 1 递增，取 max(registry.last_version, 远端已有 tag 最大 N) + 1）'
      changelog = '仓库根 CHANGELOG.md：每次同步在顶部追加一条（tag / 时间 / 说明 / 变更文件表 / 密钥终检 / 基线 commit）'
      tag = '每个版本打一个 annotated tag vN 并 push'
      history = '绝不清空 .git、绝不 force push；每次 = 远端历史之上的一个新 commit'
      trigger = '主人说「这套系统要上传 GitHub」= 登记；此后每次对话收尾前必须跑一次 sync.ps1 把本轮改动作为一个新版本推上去'
      assembly = '复用 push.ps1 -DryRun 完成排除 / 脱敏 / 密钥终检，产物铺进持久工作树，不重复造轮子'
    }
    projects = [pscustomobject]@{}
  }
}
if (-not $reg.projects) { $reg.projects = [pscustomobject]@{} }
$existing = $reg.projects.PSObject.Properties[$Project]
$now = (Get-Date).ToString('o')
if ($existing) {
  $p = $existing.Value
  $p.repo = "$Owner/$RepoName"; $p.owner = $Owner; $p.name = $RepoName
  $p.visibility = $Visibility; $p.manifest = $Manifest
  $p.root_docs = $RootDocs; $p.work_tree = $WorkTree
  Write-Host "[register] 已登记项目 $Project → 仅更新字段（保留 last_version/history）"
} else {
  $p = [ordered]@{
    repo = "$Owner/$RepoName"; owner = $Owner; name = $RepoName; visibility = $Visibility
    manifest = $Manifest; root_docs = $RootDocs; work_tree = $WorkTree
    registered_at = $now; last_version = 0; last_sha = ''; last_sync = ''; history = @()
  }
  $reg.projects | Add-Member -NotePropertyName $Project -NotePropertyValue ([pscustomobject]$p) -Force
  Write-Host "[register] 新登记项目 $Project"
}
$reg | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Registry -Encoding UTF8

$receipt = [ordered]@{
  ok = $true; project = $Project; repo = "$Owner/$RepoName"; visibility = $Visibility
  registry = $Registry; manifest = $Manifest; sources = $srcs.Count
  root_docs = $RootDocs; work_tree = $WorkTree; note = $Note
  next = "sync.ps1 -Project $Project -Note '<本轮改了什么>'"
  time = $now
}
Write-Output ($receipt | ConvertTo-Json -Depth 8)
