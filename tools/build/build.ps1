# Windows 打包脚本快捷入口
# 实际构建逻辑在 tools/build/build.py 中
# 用法: .\tools\build\build.ps1 [patch|minor|major|x.y.z] [--installer] [--delta]

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Resolve-Path (Join-Path $scriptDir "..\\..")
Set-Location $projectRoot

Get-Process | Where-Object { $_.Name -like "*通卡通*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

python (Join-Path $scriptDir "build.py") @args
