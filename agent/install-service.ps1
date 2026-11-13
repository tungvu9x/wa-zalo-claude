#Requires -RunAsAdministrator
# Cai agent thanh Windows service bang NSSM (https://nssm.cc).
# Chay 'npm start' mot lan trong console de quet QR WhatsApp/Zalo truoc, roi moi cai service.
param(
  [string]$ServiceName = 'WaDailyAgent',
  [string]$AppDir = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$node = (Get-Command node).Source
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw 'Chua co nssm trong PATH (winget install nssm)' }
if (-not (Test-Path "$AppDir\.env")) { throw "Thieu $AppDir\.env" }

New-Item -ItemType Directory -Force -Path "$AppDir\logs" | Out-Null
nssm install $ServiceName $node "$AppDir\src\index.js"
nssm set $ServiceName AppDirectory $AppDir
nssm set $ServiceName DisplayName 'WA/Zalo Daily Agent'
nssm set $ServiceName Start SERVICE_AUTO_START
nssm set $ServiceName AppStdout "$AppDir\logs\agent.log"
nssm set $ServiceName AppStderr "$AppDir\logs\agent.log"
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateOnline 1
nssm set $ServiceName AppRotateBytes 10485760
nssm set $ServiceName AppExit Default Restart
nssm set $ServiceName AppRestartDelay 10000

# .env chua DEVICE_SECRET: chi SYSTEM va Administrators duoc doc
icacls "$AppDir\.env" /inheritance:r /grant:r 'SYSTEM:(R)' 'BUILTIN\Administrators:(F)' | Out-Null
nssm start $ServiceName
Write-Host "Da cai va chay service $ServiceName. Log: $AppDir\logs\agent.log"
