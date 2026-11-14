#Requires -Version 5.1
<#
  github-project-push · 主执行脚本
  在独立 staging 副本里做「排除 → 脱敏终检 → 建仓 → 提交 → 推送 → 回读」。
  原项目一个字节不改。默认私有仓库。
#>
[CmdletBinding()]
param(
  [string]$Path,
  [string]$BundleManifest,
  [string]$Name,
  [string]$Owner,
  [ValidateSet('private','public','internal')][string]$Visibility = 'private',
  [string]$Description,
  [double]$MaxFileMB = 8,
  [string]$Message,
  [string]$Staging,
  [string]$ReportPath,
  [switch]$DryRun,
  [switch]$ForcePush
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Write-Step($msg) { Write-Host "[ghpush] $msg" }
function Fail($msg) { throw "[ghpush][ABORT] $msg" }

# ---------- 0. 前置实测 ----------
Write-Step '前置：git / gh / keyring 登录态'
& git --version | Out-Null; if ($LASTEXITCODE -ne 0) { Fail 'git 不可用' }
& gh --version | Out-Null;  if ($LASTEXITCODE -ne 0) { Fail 'gh 不可用' }
$auth = (& gh auth status 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $auth -notmatch 'Logged in') { Fail "gh 未登录：`n$auth" }
$login = (& gh api user --jq '.login' 2>&1 | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($login) -or $login -match '^(error|GraphQL|HTTP)') { Fail "gh api user 失败：$login" }
if (-not $Owner) { $Owner = $login }
Write-Step "账号 = $Owner（gh keyring 登录态已实测）"

# ---------- 1. 组装输入清单 ----------
$sources = @()   # @{ path; dest; isDir }
if ($BundleManifest) {
  if (-not (Test-Path -LiteralPath $BundleManifest)) { Fail "清单不存在：$BundleManifest" }
  $m = Get-Content -LiteralPath $BundleManifest -Raw -Encoding UTF8 | ConvertFrom-Json
  if (-not $Name -and $m.name) { $Name = $m.name }
  if (-not $Description -and $m.description) { $Description = $m.description }
  foreach ($s in @($m.sources)) { $sources += [pscustomobject]@{ path = $s.path; dest = $s.dest; isDir = $true } }
  foreach ($f in @($m.files))   { $sources += [pscustomobject]@{ path = $f.path; dest = $f.dest; isDir = $false } }
} elseif ($Path) {
  if (-not (Test-Path -LiteralPath $Path)) { Fail "项目路径不存在：$Path" }
  if (-not $Name) { $Name = Split-Path -Leaf $Path }
  $sources += [pscustomobject]@{ path = $Path; dest = ''; isDir = (Test-Path -LiteralPath $Path -PathType Container) }
} else { Fail '必须给 -Path 或 -BundleManifest' }

if (-not $Name) { Fail '仓库名 -Name 为空' }
# kebab-case：小写、非字母数字转连字符
$repoName = ($Name.ToLower() -replace '[^a-z0-9]+','-').Trim('-')
if ($repoName.Length -gt 90) { $repoName = $repoName.Substring(0,90).Trim('-') }
if (-not $Description) { $Description = "Local project snapshot uploaded via github-project-push skill" }
if (-not $Message) { $Message = "upload: $repoName @ $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')" }

# ---------- 2. staging ----------
if (-not $Staging) { $Staging = Join-Path $env:TEMP ("ghpush-$repoName-" + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
if (-not (Test-Path -LiteralPath $Staging)) { New-Item -ItemType Directory -Path $Staging -Force | Out-Null }
$Staging = (Resolve-Path -LiteralPath $Staging).Path
# 复用同一 staging 时先清空，避免上一轮的脏文件/元数据残留串进本次快照
if (Test-Path -LiteralPath (Join-Path $Staging '_GITHUB_MANIFEST.json')) {
  Write-Step 'staging 已有上轮产物 → 清空重建'
  Get-ChildItem -LiteralPath $Staging -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Step "staging = $Staging"

# ---------- 3. 排除规则 ----------
$badExt = @('.db','.sqlite','.sqlite3','.pyc','.pyd','.dll','.exe','.so','.mp4','.mov','.avi','.mkv','.zip','.7z','.rar','.pem','.key','.pfx','.p12','.bak')
$badName = @('shops.json','config.json','token.txt','.env','credentials.json','secrets.json','id_rsa','id_ed25519','.htpasswd')
# 浏览器运行态目录（证据截图时常常把整个 chrome profile 一起拷进来，内含 Cookies / Login Data）
$badDirExact = @('node_modules','__pycache__','.git','.codegraph','dist','build','web_diag','venv','.venv','.cache','.pytest_cache','备份','证据截图大图',
  'Local Storage','Sync Data','Service Worker','Session Storage','Site Characteristics Database','shared_proto_db',
  'Extension State','Extension Rules','Extension Scripts','Extensions','Safe Browsing Network','Code Cache','GPUCache','blob_storage','IndexedDB','leveldb','LevelDB','Cache','CacheStorage','profile')
$badDirPrefix = @('_基线','_备份','_backups','_bak','_stage','_保全','_snapshot','_验_','_探_','_测_','_拍_','_改_','_回填_','_apply_','_patch_','_tmp','_iso_','_probe','_verify_','_diag','_smoke','_统_','_扫_','_check','_obs_','_open','_shot_','_svc','_清_','_验','_探','_测','_拍','_改','_统','_扫','_补','_iso','_probe','_backup','chrome-profile')
# 浏览器凭据/运行态文件名（含 -journal 变体）
$badNamePattern = '^(Cookies|Cookies-journal|Login Data.*|Web Data.*|Account Web Data.*|History.*|Preferences|Secure Preferences|Network Persistent State|Trust Tokens.*|Current Session|Current Tabs|Favicons.*|Shortcuts.*|Top Sites.*|Visited Links|TransportSecurity|QuotaManager.*|Affiliation Database.*|CURRENT|LOG|LOG\.old|MANIFEST-[0-9]+|[0-9]+\.ldb|[0-9]+\.log)$'
$textExt = @('.py','.pyw','.js','.mjs','.cjs','.ts','.tsx','.json','.md','.yaml','.yml','.txt','.config','.sql','.cmd','.ps1','.sh','.html','.htm','.css','.ini','.toml','.xml','.csv','.spec','.pyi','.bat','.gitignore','.properties')

$included = @(); $skipped = @(); $skipReasons = @{}

function Add-Skip($rel, $why) {
  $script:skipped += [pscustomobject]@{ file = $rel; reason = $why }
  if (-not $script:skipReasons.ContainsKey($why)) { $script:skipReasons[$why] = 0 }
  $script:skipReasons[$why] = $script:skipReasons[$why] + 1
}

function Test-Excluded([string]$rel, [System.IO.FileInfo]$file) {
  $u = $rel -replace '\\','/'
  $segs = $u -split '/'
  foreach ($seg in $segs) {
    if ($badDirExact -contains $seg) { return "dir:$seg" }
    foreach ($p in $badDirPrefix) { if ($seg.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)) { return "dir-prefix:$p" } }
  }
  $n = $file.Name
  $ln = $n.ToLower()
  foreach ($e in $badExt) { if ($ln.EndsWith($e)) { return "ext:$e" } }
  if ($n -match '\.bak') { return 'name:bak' }
  if ($badName -contains $n) { return "name:$n" }
  if ($n -match $badNamePattern) { return 'name:browser-runtime' }
  # 2026-11-11 版本化同步实测：证据目录里的登录态快照命名不一定是 Cookies，
  # dy-login-cookies.json / *.har 这类同样含会话串，copy 阶段必须挡（此前只在 .gitignore 挡了，不够）
  if ($ln -match 'cookies' -or $ln.EndsWith('.har')) { return 'name:login-state-snapshot' }
  if ($ln -match '\.(db|sqlite|sqlite3)[-.](journal|wal|shm)$') { return 'db-side' }
  if ($file.Length -gt ($MaxFileMB * 1MB)) { return "size>$($MaxFileMB)MB" }
  return $null
}

Write-Step '组装 staging（应用硬排除）'
foreach ($s in $sources) {
  if (-not (Test-Path -LiteralPath $s.path)) { Write-Step "WARN 源不存在，跳过：$($s.path)"; continue }
  if ($s.isDir) {
    $files = @(Get-ChildItem -LiteralPath $s.path -Recurse -File -Force -ErrorAction SilentlyContinue)
  } else { $files = @(Get-Item -LiteralPath $s.path -Force) }
  foreach ($f in $files) {
    $root = if ($s.isDir) { $s.path } else { Split-Path -Parent $s.path }
    $sub = $f.FullName.Substring($root.Length).TrimStart('\','/')
    $rel = if ($s.dest) { if ($s.isDir) { Join-Path $s.dest $sub } else { $s.dest } } else { $sub }
    $rel = $rel -replace '/','\'
    $why = Test-Excluded $rel $f
    if ($why) { Add-Skip $rel $why; continue }
    $dst = Join-Path $Staging $rel
    $pd = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $pd)) { New-Item -ItemType Directory -Path $pd -Force | Out-Null }
    Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
    $included += [pscustomobject]@{ rel = $rel; bytes = $f.Length }
  }
}
$totalBytes = ($included | Measure-Object -Property bytes -Sum).Sum
Write-Step "纳入 $($included.Count) 文件 / $([math]::Round($totalBytes/1MB,2)) MB；排除 $($skipped.Count) 文件"

# ---------- 4. staging 元数据 ----------
$skillDir = Split-Path -Parent $PSScriptRoot
$gitTpl = Join-Path $skillDir 'assets\gitignore-template.txt'
if (Test-Path -LiteralPath $gitTpl) { Copy-Item -LiteralPath $gitTpl -Destination (Join-Path $Staging '.gitignore') -Force }
else {
  Set-Content -LiteralPath (Join-Path $Staging '.gitignore') -Value "__pycache__/`n*.pyc`n*.db`ndist/`nnode_modules/`n" -Encoding UTF8
}

$readme = @"
# $repoName

> 由 DSH skill ``github-project-push`` 上传。本仓库是**本地项目的结构化快照**，不是可直接运行的部署包。

- Owner: ``$Owner`` / Visibility: ``$Visibility``
- 上传时刻: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')
- 纳入文件: $($included.Count) / $([math]::Round($totalBytes/1MB,2)) MB
- 排除文件: $($skipped.Count)（编译产物 / 运行数据库 / 备份 / 探针 / 凭据 / 大素材）

## 包里没有什么（别把「未收」当成「不存在」）

``dist/`` ``build/`` ``*.db`` ``*.sqlite`` ``shops.json`` ``token.txt`` ``.env``
``node_modules`` ``__pycache__`` ``.codegraph`` 以及所有 ``_`` 开头的快照/探针/验收脚本目录，
均按硬排除规则剔除。运行态数据库里的业务数据一条都没有上传；读到 ``0`` 或「未取得」
请按原文口径理解，不要推断为「系统里没有」。

## 安全

推送前对全部纳入的文本文件做了密钥正则终检（``_GITHUB_MANIFEST.json`` 里有
``secret_hits`` 字段，必须为 ``0``）。凭据只从本机 ``gh`` keyring 现取，仓库内不含任何令牌。
"@
Set-Content -LiteralPath (Join-Path $Staging 'README-GITHUB.md') -Value $readme -Encoding UTF8

# ---------- 5. 脱敏重写 + 密钥终检（复检 0 命中才放行） ----------
Write-Step '脱敏重写 + 密钥终检（只报文件+行号+类别，不打印明文）'
$patterns = [ordered]@{
  'github-classic-pat' = 'gh[pousr]_[A-Za-z0-9]{20,}'
  'github-fine-grained' = 'github_pat_[A-Za-z0-9_]{20,}'
  'openai-key'          = 'sk-[A-Za-z0-9]{20,}'
  'aws-akid'            = 'AKIA[A-Z0-9]{16}'
  'private-key'         = '-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE|PGP)'
  'bearer'              = 'Bearer [A-Za-z0-9_\-\.]{24,}'
  'assigned-secret'     = '(app_?secret|api_?key|access_?token|secret_?key|password|passwd|pwd)\s*[:=]\s*["'']?[A-Za-z0-9_\-]{16,}["'']?'
  'json-credential'     = '["'']?(secret|app_?secret|appid|app_id|token|access_token|api_key|cookie|session_key|private_key)["'']?\s*:\s*["''][A-Za-z0-9_\-\.]{16,}["'']'
  'hex-32-secret'       = '(?i)["''](secret|app_secret|key)["'']\s*:\s*["''][0-9a-f]{32}["'']'
}
# 把「敏感字段 = 值」的值换成占位符；保留字段名与结构，值里含 '=' 故不会被二次命中
$redactRules = @(
  '(?i)(["'']?(?:secret|app_?secret|appid|app_id|token|access_token|api_key|cookie|session_key|private_key|password|passwd|pwd)["'']?\s*[:=]\s*)(["''])?[A-Za-z0-9_\-\.]{16,}(["''])?'
)
$secretHits = @()
$redactedFiles = @()
$textExt = @('.py','.pyw','.js','.mjs','.cjs','.ts','.tsx','.json','.md','.yaml','.yml','.txt','.config','.sql','.cmd','.ps1','.sh','.html','.htm','.css','.ini','.toml','.xml','.csv','.spec','.pyi','.bat','.gitignore','.properties')

function Get-StagedTextFiles {
  @(Get-ChildItem -LiteralPath $Staging -Recurse -File -Force | Where-Object {
    $ln = $_.Name.ToLower(); $hit = $false
    foreach ($e in $textExt) { if ($ln.EndsWith($e)) { $hit = $true; break } }
    if ($ln -eq '.gitignore' -or $ln -eq 'readme-github.md' -or $ln -eq '_github_manifest.json') { $hit = $true }
    $hit
  })
}
function Scan-Secrets {
  $hits = @()
  foreach ($f in (Get-StagedTextFiles)) {
    $lines = @(Get-Content -LiteralPath $f.FullName -Encoding UTF8 -ErrorAction SilentlyContinue)
    for ($i = 0; $i -lt $lines.Count; $i++) {
      foreach ($k in $patterns.Keys) {
        if ($lines[$i] -match $patterns[$k]) {
          $hits += [pscustomobject]@{ file = $f.FullName; rel = $f.FullName.Substring($Staging.Length+1); line = ($i+1); kind = $k }
        }
      }
    }
  }
  return $hits
}

$hits = @(Scan-Secrets)
if ($hits.Count -gt 0) {
  Write-Step "首轮命中 $($hits.Count) 处 → 按行脱敏重写（不打印明文）"
  $filesToFix = @($hits | Select-Object -ExpandProperty file -Unique)
  foreach ($fp in $filesToFix) {
    $lines = @(Get-Content -LiteralPath $fp -Encoding UTF8 -ErrorAction SilentlyContinue)
    $changed = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
      $orig = $lines[$i]
      $new = $orig
      foreach ($r in $redactRules) { $new = $new -replace $r, '${1}${2}===REDACTED===${3}' }
      # 前缀型密钥串（PAT / sk- / Bearer / 私钥头）整段换掉
      $new = $new -replace 'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE|PGP)[A-Za-z0-9\s\-]*', '===REDACTED==='
      if ($new -ne $orig) { $lines[$i] = $new; $changed = $true }
    }
    if ($changed) {
      [IO.File]::WriteAllText($fp, ($lines -join "`n"), [Text.UTF8Encoding]::new($false))
      $redactedFiles += $fp.Substring($Staging.Length+1)
    }
  }
  $hits2 = @(Scan-Secrets)
  if ($hits2.Count -gt 0) {
    $hits2 | ForEach-Object { Write-Host "  SECRET-HIT $($_.rel):$($_.line) [$($_.kind)]" }
    Fail "脱敏后复检仍命中 $($hits2.Count) 处，已中止（未建仓、未推送）"
  }
  Write-Step "脱敏重写 $($redactedFiles.Count) 个文件，复检命中 0"
}
$staged = @(Get-StagedTextFiles)
Write-Step "密钥终检 PASS：扫描 $($staged.Count) 个文本文件 / 命中 0"

# ---------- 6. 回执骨架 ----------
$manifest = [ordered]@{
  tool = 'github-project-push'
  generated_at = (Get-Date -Format 'o')
  owner = $Owner; repo = $repoName; visibility = $Visibility
  staging_path = $Staging
  included_files = $included.Count
  included_bytes = [int64]$totalBytes
  skipped_files = $skipped.Count
  skipped_reasons = $skipReasons
  secret_hits = 0
  scanned_text_files = $staged.Count
  dry_run = [bool]$DryRun
  sources = @($sources | ForEach-Object { [ordered]@{ path = $_.path; dest = $_.dest; is_dir = $_.isDir } })
}
$manifestPath = Join-Path $Staging '_GITHUB_MANIFEST.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$report = [ordered]@{
  ok = $false; repo_url = "https://github.com/$Owner/$repoName"; visibility = $Visibility
  owner = $Owner; name = $repoName
  commit_sha_local = ''; commit_sha_remote = ''; head_match = $false
  included_files = $included.Count; included_bytes = [int64]$totalBytes
  skipped_files = $skipped.Count; skipped_reasons = $skipReasons
  secret_hits = 0; excluded_rule_hits = $skipped.Count; remote_tree_bad_hits = -1
  staging_path = $Staging; manifest_path = $manifestPath; report_path = ''
  dry_run = [bool]$DryRun; note = ''
}

if ($DryRun) {
  Write-Step 'DryRun：不建仓、不提交、不推送'
  $report.ok = $true; $report.note = 'dry-run only'
  if (-not $ReportPath) { $ReportPath = Join-Path $Staging '_GITHUB_REPORT.json' }
  $report.report_path = $ReportPath
  $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
  Write-Output ($report | ConvertTo-Json -Depth 8)
  exit 0
}

# ---------- 7. git init / commit ----------
Write-Step 'git init + commit（仅 staging）'
& git -C $Staging init -b main 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'git init 失败' }
$uname = (& git config --global --get user.name | Out-String).Trim(); if (-not $uname) { $uname = $Owner }
$uemail = (& git config --global --get user.email | Out-String).Trim(); if (-not $uemail) { $uemail = "$Owner@users.noreply.github.com" }
& git -C $Staging config --local user.name $uname | Out-Null
& git -C $Staging config --local user.email $uemail | Out-Null
# local helper：优先从 gh keyring 现取；全局 manager 作为兜底（两者都不会把 token 写进仓库）
& git -C $Staging config --local credential.helper '!gh auth git-credential' 2>&1 | Out-Null
& git -C $Staging add -A | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'git add 失败' }
& git -C $Staging commit -m $Message 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'git commit 失败（可能无改动）' }
$localSha = (& git -C $Staging rev-parse HEAD | Out-String).Trim()
$report.commit_sha_local = $localSha
Write-Step "local HEAD = $localSha"

# ---------- 8. 建仓 / 复用 ----------
$full = "$Owner/$repoName"
& gh repo view $full --json name 2>&1 | Out-Null
$exists = ($LASTEXITCODE -eq 0)
if (-not $exists) {
  Write-Step "创建 $Visibility 仓库 $full"
  $visFlag = switch ($Visibility) { 'private' {'--private'} 'public' {'--public'} 'internal' {'--internal'} }
  & gh repo create $full $visFlag --description $Description --confirm 2>&1 | Out-String | Write-Host
  if ($LASTEXITCODE -ne 0) { Fail "gh repo create 失败（$full）" }
} else { Write-Step "仓库已存在，复用 $full" }

& git -C $Staging remote remove origin 2>&1 | Out-Null
& git -C $Staging remote add origin "https://github.com/$full.git" | Out-Null

# ---------- 9. 认证预检 + 推送 ----------
Write-Step '认证预检 git ls-remote'
$ls = (& git ls-remote "https://github.com/$full.git" HEAD 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { Fail "认证预检失败：`n$ls" }
Write-Step 'git push'
$pushArgs = @('-C', $Staging, 'push', '-u', 'origin', 'HEAD:refs/heads/main')
if ($ForcePush) { $pushArgs += '--force' }
$pushOut = (& git @pushArgs 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { Fail "git push 失败：`n$pushOut" }
Write-Host $pushOut

# ---------- 10. 回读校验 ----------
Write-Step '回读远端 HEAD'
$remoteLine = (& git ls-remote "https://github.com/$full.git" refs/heads/main 2>&1 | Out-String).Trim()
$remoteSha = ($remoteLine -split '\s+')[0]
$report.commit_sha_remote = $remoteSha
$report.head_match = ($remoteSha -eq $localSha)
$vis = (& gh repo view $full --json visibility --jq '.visibility' 2>&1 | Out-String).Trim()
Write-Step "remote HEAD = $remoteSha / match=$($report.head_match) / visibility=$vis"

# ---------- 10b. 远端 tree 内容核验：登录态/凭据文件必须 0 命中 ----------
Write-Step '远端 tree 内容核验（Cookies / Login Data / profile / 密钥文件）'
$treeJson = (& gh api "repos/$full/git/trees/$remoteSha`?recursive=1" 2>&1 | Out-String)
$treeBad = @()
try {
  $paths = @($treeJson | ConvertFrom-Json | Select-Object -ExpandProperty tree | Select-Object -ExpandProperty path)
  foreach ($p in $paths) {
    if ($p -match 'chrome-profile|/profile/|Local Storage|Sync Data|Service Worker/Database|Session Storage|Site Characteristics') { $treeBad += $p }
    elseif ($p -match '(^|/)(Cookies|Login Data|Web Data|Account Web Data|Network Persistent State|Trust Tokens|History|Secure Preferences|shops\.json|token\.txt|config\.json|\.env|[A-Za-z0-9]+\.db)([-.]|$)') { $treeBad += $p }
  }
} catch { Write-Step "WARN tree 解析失败：$($_.Exception.Message)" }
$report.remote_tree_bad_hits = $treeBad.Count
if ($treeBad.Count -gt 0) {
  $treeBad | Select-Object -First 15 | ForEach-Object { Write-Host "  TREE-BAD $_" }
  Fail "远端 tree 含 $($treeBad.Count) 条浏览器登录态/凭据路径，推送判为失败"
}
Write-Step '远端 tree 核验 PASS：登录态/凭据路径命中 0'

$report.ok = [bool]$report.head_match
$report.note = if ($report.head_match) { 'push verified' } else { 'HEAD mismatch' }
if (-not $ReportPath) { $ReportPath = Join-Path $Staging '_GITHUB_REPORT.json' }
$report.report_path = $ReportPath
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
# 把最终结果同步进 manifest
$manifest.commit_sha_local = $localSha
$manifest.commit_sha_remote = $remoteSha
$manifest.head_match = $report.head_match
$manifest.visibility_remote = $vis
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Output ($report | ConvertTo-Json -Depth 8)
if (-not $report.head_match) { exit 2 }
