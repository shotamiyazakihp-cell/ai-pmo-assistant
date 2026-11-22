# Phase 4：自動実行をタスクスケジューラに登録する（任意。登録するかはオーナーが決める）
# 登録：powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
# 解除：powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove
# 毎時：Notion の更新確認（Claude は使わない）。毎朝 7:40：日次の確認レポート（data\reports）。
# Claude での自動分析は入れない（使う場合は -WithAnalyze。利用枠を使う）。
param([switch]$Remove, [switch]$WithAnalyze)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$App = Split-Path -Parent $PSScriptRoot
$Py = "$App\.venv\Scripts\python.exe"
$names = "AIPMO_Hourly", "AIPMO_Daily"
if ($Remove) {
    foreach ($n in $names) { schtasks /Delete /TN $n /F 2>$null | Out-Null }
    Write-Host "自動実行の登録を解除しました。"
    exit 0
}
if (-not (Test-Path $Py)) { Write-Host "先に pc_setup_and_check.bat を実行してください。"; exit 1 }
$analyze = if ($WithAnalyze) { " --analyze" } else { "" }
schtasks /Create /F /TN "AIPMO_Hourly" /SC HOURLY /TR "`"$Py`" -m pmo auto hourly" | Out-Null
schtasks /Create /F /TN "AIPMO_Daily" /SC DAILY /ST 07:40 /TR "`"$Py`" -m pmo auto daily$analyze" | Out-Null
Write-Host "登録しました：AIPMO_Hourly（毎時）、AIPMO_Daily（毎朝 7:40）。PC が起動している時だけ動きます。"
Write-Host "結果は data\reports と、画面の「処理の履歴・設定」で見られます。"
