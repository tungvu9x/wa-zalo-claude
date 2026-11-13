<#
Chay wa-zalo-daily: kiem tra PostgreSQL, bat Cloud (cua so thu nho) va Agent (cua so co QR).
Dong cua so 'WA-Zalo Agent' / 'WA-Zalo Cloud' de dung. Tin nhan dang cho van nam trong outbox.
#>
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Cloud = Join-Path $Root 'cloud'
$Agent = Join-Path $Root 'agent'
$VenvPy = Join-Path $Cloud '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPy) -or -not (Test-Path (Join-Path $Agent '.env'))) {
  Write-Host 'Chua cai dat. Chay install-windows.ps1 truoc.' -ForegroundColor Red
  Read-Host 'Enter de thoat'
  exit 1
}

$port = '8080'
foreach ($l in [IO.File]::ReadAllLines((Join-Path $Cloud '.env'))) {
  if ($l -match '^\s*API_PORT\s*=\s*(\d+)') { $port = $Matches[1] }
}
$Api = "http://127.0.0.1:$port"
function Test-Health {
  try { return ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$Api/healthz").StatusCode -eq 200) } catch { return $false }
}
function Find-Proc([string]$name, [string]$pattern) {
  Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like $pattern }
}

# PostgreSQL (service tu bat cung Windows; phong khi bi tat)
$pg = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue
foreach ($s in $pg) {
  if ($s.Status -ne 'Running') {
    try { Start-Service $s.Name } catch { Write-Warning "Service $($s.Name) dang tat va can quyen Admin de bat: services.msc > $($s.Name) > Start" }
  }
}

# Cloud
if (Test-Health) {
  Write-Host "Cloud da chay ($Api)" -ForegroundColor Green
}
else {
  Write-Host 'Bat Cloud...'
  Start-Process -FilePath 'cmd.exe' -WorkingDirectory $Cloud -WindowStyle Minimized `
    -ArgumentList '/k', "title WA-Zalo Cloud && `"$VenvPy`" run_cloud.py"
  for ($i = 0; $i -lt 60 -and -not (Test-Health); $i++) { Start-Sleep -Seconds 1 }
  if (-not (Test-Health)) {
    Write-Host 'Cloud khong len sau 60 giay. Mo cua so WA-Zalo Cloud de xem loi.' -ForegroundColor Red
    Read-Host 'Enter de thoat'
    exit 1
  }
  Write-Host "Cloud san sang ($Api)" -ForegroundColor Green
}

# Agent
if (Find-Proc 'node.exe' '*src\index.js*') {
  Write-Host 'Agent da chay' -ForegroundColor Green
}
else {
  Write-Host 'Bat Agent...'
  Start-Process -FilePath 'cmd.exe' -WorkingDirectory $Agent -WindowStyle Minimized -ArgumentList '/k', 'title WA-Zalo Agent && node src\index.js'
}
# Trang quan tri: trang thai, ma QR dang nhap, file, bao cao
Start-Process "$Api/admin"
Write-Host ''
Write-Host 'Xong. Co the dong cua so nay.' -ForegroundColor Green
Start-Sleep -Seconds 5
