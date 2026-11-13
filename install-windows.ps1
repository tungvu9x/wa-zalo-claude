<#
Cai wa-zalo-daily tren mot may Windows, chay truc tiep (khong Docker):
  PostgreSQL + Cloud (Python) + Agent (Node) + ket noi Claude Desktop qua MCP.

Chay (PowerShell thuong, khong can Admin; winget se hoi UAC khi cai PostgreSQL):
  powershell -ExecutionPolicy Bypass -File .\install-windows.ps1

Chay lai nhieu lan duoc: giu nguyen secret, mat khau DB va device da tao.
#>
param(
  [int]$PgPort = 5432,
  [int]$ApiPort = 8080,
  [string]$TenantName = 'KNXStore',
  [switch]$SkipClaudeConfig
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Cloud = Join-Path $Root 'cloud'
$Agent = Join-Path $Root 'agent'
$Tools = Join-Path $Root 'tools'
$CloudEnvPath = Join-Path $Cloud '.env'
$AgentEnvPath = Join-Path $Agent '.env'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$Api = "http://127.0.0.1:$ApiPort"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "    $m" -ForegroundColor Green }
function Info($m) { Write-Host "    $m" }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function New-RandomBytes([int]$n) {
  $b = New-Object byte[] $n
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return , $b
}
function New-Token([int]$n = 48) {
  ([Convert]::ToBase64String((New-RandomBytes $n))).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function New-FernetKey {
  ([Convert]::ToBase64String((New-RandomBytes 32))).Replace('+', '-').Replace('/', '_')
}
function New-Password {
  $chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  -join ((New-RandomBytes 24) | ForEach-Object { $chars[$_ % $chars.Length] })
}
function Read-EnvFile($path) {
  $h = [ordered]@{}
  if (Test-Path $path) {
    foreach ($l in [IO.File]::ReadAllLines($path)) {
      if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() }
    }
  }
  return $h
}
function Write-EnvFile($path, $map, [string]$header) {
  $lines = @($header)
  foreach ($k in $map.Keys) { $lines += "$k=$($map[$k])" }
  [IO.File]::WriteAllText($path, (($lines -join "`r`n") + "`r`n"), $Utf8)
}
function Get-Or($map, $key, [scriptblock]$make) {
  if ($map.Contains($key) -and $map[$key]) { return $map[$key] }
  return (& $make)
}
# Chay lenh ngoai, khong de stderr bien thanh exception cua PowerShell 5.1
function Invoke-Native([string]$exe, [string[]]$argv) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $out = & $exe @argv 2>&1 } finally { $ErrorActionPreference = $old }
  return [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out | Out-String).Trim() }
}
function Install-Pkg([string]$id, [string]$name, [string[]]$extra = @()) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "Khong co winget. Cai 'App Installer' tu Microsoft Store roi chay lai."
  }
  Info "Cai $name ($id) bang winget, co the mat vai phut..."
  $r = Invoke-Native 'winget' (@('install', '-e', '--id', $id, '--accept-package-agreements', '--accept-source-agreements', '--silent') + $extra)
  # -1978335189 = da cai, khong co ban moi hon
  if ($r.Code -ne 0 -and $r.Code -ne -1978335189) { throw "winget cai $id that bai (ma $($r.Code)):`n$($r.Out)" }
  Refresh-Path
}
function Test-Health {
  try { return ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$Api/healthz").StatusCode -eq 200) } catch { return $false }
}

# ------------------------------------------------------------------ 1. Python 3.12
Step 'Python 3.12'
function Find-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    $r = Invoke-Native 'py' @('-3.12', '-c', 'import sys;print(sys.executable)')
    if ($r.Code -eq 0 -and $r.Out -and (Test-Path $r.Out)) { return $r.Out }
  }
  foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $c) { return $c }
  }
  return $null
}
$Python = Find-Python
if (-not $Python) { Install-Pkg 'Python.Python.3.12' 'Python 3.12'; $Python = Find-Python }
if (-not $Python) { throw 'Khong tim thay Python 3.12 sau khi cai. Mo PowerShell moi va chay lai script.' }
Ok $Python

# ------------------------------------------------------------------ 2. Node.js
Step 'Node.js 20+'
function Get-NodeMajor {
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return 0 }
  $r = Invoke-Native 'node' @('-p', 'process.versions.node.split(".")[0]')
  if ($r.Code -ne 0) { return 0 }
  return [int]$r.Out
}
if ((Get-NodeMajor) -lt 20) { Install-Pkg 'OpenJS.NodeJS.LTS' 'Node.js LTS' }
if ((Get-NodeMajor) -lt 20) { throw 'Can Node.js 20+. Mo PowerShell moi va chay lai script.' }
$NodeExe = (Get-Command node).Source
Ok "$NodeExe (v$(Get-NodeMajor))"

# ------------------------------------------------------------------ 3. PostgreSQL
Step 'PostgreSQL'
$envCloud = Read-EnvFile $CloudEnvPath
function Find-Psql {
  $found = Get-ChildItem "$env:ProgramFiles\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Directory.Parent.Name -match '^\d+$' -and [int]$_.Directory.Parent.Name -ge 14 } |
    Sort-Object { [int]$_.Directory.Parent.Name } -Descending | Select-Object -First 1
  if ($found) { return $found.FullName }
  return $null
}
$Psql = Find-Psql
$pgSuper = $envCloud['PG_SUPERUSER_PASSWORD']
if (-not $Psql) {
  if (-not $pgSuper) { $pgSuper = New-Password }
  # Luu truoc khi cai: neu cai xong ma script loi, lan chay sau van biet mat khau
  $envCloud['PG_SUPERUSER_PASSWORD'] = $pgSuper
  New-Item -ItemType Directory -Force $Cloud | Out-Null
  Write-EnvFile $CloudEnvPath $envCloud '# Tao boi install-windows.ps1 - KHONG chia se file nay'
  Install-Pkg 'PostgreSQL.PostgreSQL.16' 'PostgreSQL 16' @('--override',
    "--mode unattended --unattendedmodeui none --superpassword $pgSuper --serverport $PgPort --disable-components stackbuilder")
  $Psql = Find-Psql
  if (-not $Psql) { throw 'Cai PostgreSQL xong nhung khong thay psql.exe trong C:\Program Files\PostgreSQL' }
}
elseif (-not $pgSuper) {
  $sec = Read-Host -AsSecureString "    PostgreSQL da co san. Nhap mat khau user 'postgres'"
  $pgSuper = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
Ok $Psql

Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Where-Object { $_.Status -ne 'Running' } | ForEach-Object {
  $svc = $_.Name
  try { Start-Service $svc; Ok "Da bat service $svc" } catch { Write-Warning "Khong bat duoc service $svc (can quyen Admin)" }
}

$env:PGPASSWORD = $pgSuper
function Psql([string]$sql, [string]$db = 'postgres') {
  $r = Invoke-Native $Psql @('-h', '127.0.0.1', '-p', "$PgPort", '-U', 'postgres', '-d', $db, '-v', 'ON_ERROR_STOP=1', '-tAc', $sql)
  if ($r.Code -ne 0) { throw "psql loi ($sql):`n$($r.Out)" }
  return $r.Out
}
$dbPw = Get-Or $envCloud 'WADAILY_DB_PASSWORD' { New-Password }
if ((Psql "SELECT 1 FROM pg_roles WHERE rolname = 'wadaily'") -ne '1') {
  Psql "CREATE ROLE wadaily LOGIN PASSWORD '$dbPw'" | Out-Null
}
else {
  Psql "ALTER ROLE wadaily PASSWORD '$dbPw'" | Out-Null
}
if ((Psql "SELECT 1 FROM pg_database WHERE datname = 'wadaily'") -ne '1') {
  Psql "CREATE DATABASE wadaily OWNER wadaily ENCODING 'UTF8' TEMPLATE template0" | Out-Null
}
Psql 'CREATE EXTENSION IF NOT EXISTS unaccent' 'wadaily' | Out-Null
Remove-Item Env:\PGPASSWORD
Ok 'Database wadaily san sang'

# ------------------------------------------------------------------ 4. Cloud .env
Step 'Cau hinh Cloud'
$storage = Join-Path $Root 'data\files'
New-Item -ItemType Directory -Force $storage | Out-Null
$cfg = [ordered]@{
  DATABASE_URL          = "postgresql://wadaily:$dbPw@127.0.0.1:$PgPort/wadaily"
  MASTER_KEY            = (Get-Or $envCloud 'MASTER_KEY' { New-FernetKey })
  ADMIN_TOKEN           = (Get-Or $envCloud 'ADMIN_TOKEN' { New-Token })
  MCP_TOKEN             = (Get-Or $envCloud 'MCP_TOKEN' { New-Token })
  ADMIN_UI_PASSWORD     = (Get-Or $envCloud 'ADMIN_UI_PASSWORD' { (New-Password).Substring(0, 12) })
  MCP_TENANT_ID         = (Get-Or $envCloud 'MCP_TENANT_ID' { '' })
  STORAGE_DIR           = $storage
  API_HOST              = '127.0.0.1'
  API_PORT              = "$ApiPort"
  MAX_FILE_MB           = (Get-Or $envCloud 'MAX_FILE_MB' { '100' })
  WADAILY_DB_PASSWORD   = $dbPw
  PG_SUPERUSER_PASSWORD = $pgSuper
}
Write-EnvFile $CloudEnvPath $cfg '# Tao boi install-windows.ps1 - KHONG chia se file nay'
Ok $CloudEnvPath

# ------------------------------------------------------------------ 5. Python venv
Step 'Thu vien Python (cloud\.venv)'
$VenvPy = Join-Path $Cloud '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
  $r = Invoke-Native $Python @('-m', 'venv', (Join-Path $Cloud '.venv'))
  if ($r.Code -ne 0) { throw "Tao venv loi:`n$($r.Out)" }
}
$r = Invoke-Native $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '--upgrade', 'pip')
$r = Invoke-Native $VenvPy @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', (Join-Path $Cloud 'requirements.txt'))
if ($r.Code -ne 0) { throw "pip install loi:`n$($r.Out)" }
Ok 'Da cai'

# ------------------------------------------------------------------ 6. Chay thu Cloud, tao tenant + device
Step 'Khoi dong Cloud de dang ky thiet bi'
$cloudProc = $null
if (-not (Test-Health)) {
  $cloudProc = Start-Process -FilePath $VenvPy -ArgumentList 'run_cloud.py' -WorkingDirectory $Cloud -NoNewWindow -PassThru `
    -RedirectStandardOutput (Join-Path $Root 'data\install-cloud.log') -RedirectStandardError (Join-Path $Root 'data\install-cloud.err.log')
  for ($i = 0; $i -lt 60 -and -not (Test-Health); $i++) {
    if ($cloudProc.HasExited) { break }
    Start-Sleep -Seconds 1
  }
  if (-not (Test-Health)) {
    throw "Cloud khong khoi dong duoc. Xem $Root\data\install-cloud.err.log"
  }
}
Ok "Cloud chay tai $Api"

$hdr = @{ Authorization = "Bearer $($cfg.ADMIN_TOKEN)" }
$envAgent = Read-EnvFile $AgentEnvPath
try {
  if (-not $cfg.MCP_TENANT_ID) {
    $t = Invoke-RestMethod -Method Post -Uri "$Api/admin/tenants" -Headers $hdr -ContentType 'application/json' `
      -Body (@{ name = $TenantName; retention_days = 150 } | ConvertTo-Json)
    $cfg.MCP_TENANT_ID = $t.tenant_id
    Write-EnvFile $CloudEnvPath $cfg '# Tao boi install-windows.ps1 - KHONG chia se file nay'
    Ok "Tao tenant $($t.tenant_id)"
  }
  if (-not ($envAgent['DEVICE_ID'] -and $envAgent['DEVICE_SECRET'])) {
    $d = Invoke-RestMethod -Method Post -Uri "$Api/admin/devices" -Headers $hdr -ContentType 'application/json' `
      -Body (@{ tenant_id = $cfg.MCP_TENANT_ID; name = $env:COMPUTERNAME } | ConvertTo-Json)
    $envAgent['DEVICE_ID'] = $d.device_id
    $envAgent['DEVICE_SECRET'] = $d.device_secret
    Ok "Tao device $($d.device_id)"
  }
  else { Ok "Giu device $($envAgent['DEVICE_ID'])" }
}
finally {
  if ($cloudProc -and -not $cloudProc.HasExited) { Stop-Process -Id $cloudProc.Id -Force }
}

# ------------------------------------------------------------------ 7. Agent
Step 'Agent (npm install, tai Chromium cho WhatsApp)'
$agentCfg = [ordered]@{
  CLOUD_URL       = $Api
  DEVICE_ID       = $envAgent['DEVICE_ID']
  DEVICE_SECRET   = $envAgent['DEVICE_SECRET']
  DATA_DIR        = './data'
  ENABLE_WHATSAPP = (Get-Or $envAgent 'ENABLE_WHATSAPP' { 'true' })
  ENABLE_ZALO     = (Get-Or $envAgent 'ENABLE_ZALO' { 'true' })
  MAX_FILE_MB     = (Get-Or $envAgent 'MAX_FILE_MB' { '100' })
}
if ($envAgent['CHROME_PATH']) { $agentCfg['CHROME_PATH'] = $envAgent['CHROME_PATH'] }
Write-EnvFile $AgentEnvPath $agentCfg '# Tao boi install-windows.ps1 - KHONG chia se file nay'
Push-Location $Agent
try {
  $r = Invoke-Native 'npm.cmd' @('install', '--no-audit', '--no-fund')
  if ($r.Code -ne 0) { throw "npm install loi:`n$($r.Out)" }
}
finally { Pop-Location }
Ok 'Da cai'

# ------------------------------------------------------------------ 8. Claude Desktop
Step 'Ket noi Claude Desktop'
New-Item -ItemType Directory -Force $Tools | Out-Null
$r = Invoke-Native 'npm.cmd' @('install', '--prefix', (Join-Path $Tools 'mcp-remote'), 'mcp-remote@0.16.6', '--no-audit', '--no-fund')
if ($r.Code -ne 0) { throw "Cai mcp-remote loi:`n$($r.Out)" }
$Proxy = Join-Path $Tools 'mcp-remote\node_modules\mcp-remote\dist\proxy.js'
if (-not (Test-Path $Proxy)) { throw "Khong thay $Proxy" }

function Set-ClaudeConfig([string]$file) {
  New-Item -ItemType Directory -Force (Split-Path $file) | Out-Null
  $conf = New-Object psobject
  if (Test-Path $file) {
    Copy-Item $file "$file.bak-$(Get-Date -Format yyyyMMddHHmmss)"
    $raw = [IO.File]::ReadAllText($file)
    if ($raw.Trim()) { $conf = $raw | ConvertFrom-Json }
  }
  if (-not $conf.PSObject.Properties['mcpServers']) {
    $conf | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue (New-Object psobject)
  }
  $server = [pscustomobject]@{
    command = $NodeExe
    args    = @($Proxy, "$Api/mcp", '--header', 'Authorization:${AUTH_HEADER}')
    env     = [pscustomobject]@{ AUTH_HEADER = "Bearer $($cfg.MCP_TOKEN)" }
  }
  $conf.mcpServers | Add-Member -NotePropertyName 'wa-zalo-daily' -NotePropertyValue $server -Force
  [IO.File]::WriteAllText($file, ($conf | ConvertTo-Json -Depth 20), $Utf8)
  Ok "Da ghi $file"
}

if ($SkipClaudeConfig) {
  Info 'Bo qua (-SkipClaudeConfig)'
}
else {
  $targets = @("$env:APPDATA\Claude\claude_desktop_config.json")
  # Ban Claude Desktop cai tu Microsoft Store (MSIX) doc config trong thu muc Packages
  Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue | ForEach-Object {
    $targets += (Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json')
  }
  foreach ($f in $targets) { Set-ClaudeConfig $f }
  $claudeExe = @("$env:LOCALAPPDATA\AnthropicClaude\claude.exe", "$env:LOCALAPPDATA\Programs\Claude\Claude.exe") | Where-Object { Test-Path $_ }
  $claudeMsix = Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue
  if (-not $claudeExe -and -not $claudeMsix) {
    Write-Warning 'Chua thay Claude Desktop. Tai tai https://claude.ai/download, cai xong KHONG can chay lai script (config da ghi san).'
  }
}

# ------------------------------------------------------------------ 9. Shortcut
Step 'Tao shortcut tren Desktop'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'WA-Zalo Daily.lnk'))
$lnk.TargetPath = Join-Path $Root 'start.cmd'
$lnk.WorkingDirectory = $Root
$lnk.Description = 'Chay Cloud + Agent wa-zalo-daily'
$lnk.Save()
Ok 'WA-Zalo Daily.lnk'
$url = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'WA-Zalo Admin.url'))
$url.TargetPath = "$Api/admin"
$url.Save()
Ok 'WA-Zalo Admin.url'

Write-Host ''
Write-Host 'CAI DAT XONG' -ForegroundColor Green
Write-Host "Trang quan tri: $Api/admin   Mat khau: $($cfg.ADMIN_UI_PASSWORD)" -ForegroundColor Yellow
Write-Host "(mat khau luu trong cloud\.env, dong ADMIN_UI_PASSWORD)"
Write-Host @"
Buoc tiep theo:
  1. Double-click 'WA-Zalo Daily' tren Desktop (hoac chay start.cmd).
     - Trang quan tri tu mo: muc 'Dang nhap tai khoan' hien ma QR WhatsApp va Zalo de quet.
  2. Thoat han Claude Desktop (chuot phai icon o khay he thong > Quit) roi mo lai.
  3. Hoi Claude: "goi system_status cua wa-zalo-daily".
"@
