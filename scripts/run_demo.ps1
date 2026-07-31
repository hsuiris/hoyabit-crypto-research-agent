param([string]$Coin = "ETH", [string]$Question = "分析近期市場狀況、主要驅動因素與風險")

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot
Write-Host "1. 執行完整測試"
python -m unittest discover -s tests -v
Write-Host "2. 啟動 Web Demo：http://127.0.0.1:8000"
Write-Host "3. 使用 Windows Game Bar（Win+Alt+R）開始／停止錄影"
python -m src.app
