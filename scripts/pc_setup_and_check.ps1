# AI PMO Assistant：PC のセットアップと確認（pc_setup_and_check.bat から呼ばれる）
# 何度実行しても安全。鍵の中身は表示しない。ANTHROPIC_API_KEY があれば止める。
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$App = Split-Path -Parent $PSScriptRoot
Set-Location $App
New-Item -ItemType Directory -Force -Path "$App\logs" | Out-Null
$Log = "$App\logs\pc_check_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $Log | Out-Null
$Results = [ordered]@{}

function Step($n, $msg) { Write-Host ""; Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Ok($key, $msg) { $script:Results[$key] = "成功"; Write-Host "  → 成功：$msg" -ForegroundColor Green }
function Skip($key, $msg) { $script:Results[$key] = "飛ばした"; Write-Host "  → 飛ばした：$msg" -ForegroundColor Yellow }
function Fail($key, $msg, $next) {
    $script:Results[$key] = "失敗"
    Write-Host "  → 失敗：$msg" -ForegroundColor Red
    Write-Host "  次の一手：$next" -ForegroundColor Red
}
function Summary {
    Write-Host ""; Write-Host "========== 結果のまとめ ==========" -ForegroundColor Cyan
    foreach ($k in $script:Results.Keys) { Write-Host ("  {0}：{1}" -f $k, $script:Results[$k]) }
    Write-Host "  記録：$Log"
    Write-Host "  困ったら、この記録ファイルの中身をチャットに貼ってください（鍵は書かれていません）。"
}
function Stop-All { Summary; Stop-Transcript | Out-Null; exit 1 }

# 1. API キーが無いこと
Step 1 "ANTHROPIC_API_KEY が無いことを確かめます（あると Claude Code が API 料金で動くため）"
$found = @()
foreach ($scope in "Process", "User", "Machine") {
    if ([Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", $scope)) { $found += $scope }
}
if ($found.Count -gt 0) {
    Fail "API キーの確認" "ANTHROPIC_API_KEY が設定されています（場所：$($found -join ', ')。中身は表示しません）" `
        "Windows の「環境変数を編集」で ANTHROPIC_API_KEY を削除し、PC を再起動してから、もう一度ダブルクリックしてください。"
    Stop-All
}
Ok "API キーの確認" "設定されていません"

# 2. Python
Step 2 "Python を確かめます"
$Py = $null
foreach ($cand in @(@("py", "-3"), @("python"))) {
    if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
        $pyArgs = @($cand | Select-Object -Skip 1) + "--version"
        $v = & $cand[0] @pyArgs 2>&1
        if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) { $Py = $cand; break }
    }
}
if (-not $Py) {
    Fail "Python" "Python 3.10 以上が見つかりません" "https://www.python.org/downloads/ から入れて（「Add python.exe to PATH」にチェック）、もう一度ダブルクリックしてください。"
    Stop-All
}
Ok "Python" "$($Py -join ' ') を使います（$v）"

# 3. 準備（仮想環境と部品）
Step 3 "準備をします（初回は数分かかります）"
if (-not (Test-Path "$App\.venv\Scripts\python.exe")) {
    $venvArgs = @($Py | Select-Object -Skip 1) + @("-m", "venv", "$App\.venv")
    & $Py[0] @venvArgs
}
$VPy = "$App\.venv\Scripts\python.exe"
& $VPy -m pip install --quiet --disable-pip-version-check -r requirements.txt -e .
if ($LASTEXITCODE -ne 0) { Fail "準備" "部品の取り込みに失敗しました" "ネットにつながっているか確かめて、もう一度ダブルクリックしてください。"; Stop-All }
Ok "準備" "仮想環境と部品がそろいました"

# 4. テスト
Step 4 "テストを動かします"
& $VPy -m pytest -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { Fail "テスト" "失敗したテストがあります" "記録ファイルをチャットに貼ってください。直します。"; Stop-All }
Ok "テスト" "すべて通りました"

# 5. pmo check（本番のデータベース data\pmo.db を使う）
Step 5 "設定を確かめます（鍵は「設定あり／未設定」だけを表示）"
& $VPy -m pmo check
if ($LASTEXITCODE -ne 0) { Fail "設定の確認" "問題があります" "上の表示を確かめてください。"; Stop-All }
Ok "設定の確認" "問題ありません"

# 6〜7 は試験用のデータベース data\pc_check.db で行う（本番のデータベースは汚さない）
$env:PMO_DB_PATH = "data/pc_check.db"

# 6. Claude Code での抽出（試験用の議事録1件だけ）
Step 6 "Claude Code（Claude Pro のログイン）で試験用の議事録を1件だけ分析します（1〜3分）"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Skip "Claude での抽出" "claude コマンドが見つかりません。Claude Code を入れてログインすると試せます（画面の「手で貼る」は使えます）"
} else {
    & $VPy -m pmo ingest-samples | Out-Null
    $out = & $VPy -m pmo analyze --source sample-alpha-01 --force 2>&1 | Out-String
    Write-Host $out
    if ($LASTEXITCODE -eq 0 -and $out -match '"status": "success"') {
        Ok "Claude での抽出" "候補を保存しました（画面の「抽出の確認」で中身を見られます）"
    } else {
        Fail "Claude での抽出" "分析に失敗しました" "PowerShell で claude を一度起動してログインしてから、もう一度ダブルクリックしてください。それでも失敗したら記録ファイルを貼ってください。"
    }
}

# 7. Notion の試験ページ
Step 7 "Notion の試験用データベースを読み取ります（.env に鍵があり、NOTION_DATABASE_KIND=test の時だけ）"
$envText = if (Test-Path "$App\.env") { Get-Content "$App\.env" -Raw -Encoding UTF8 } else { "" }
if ($envText -notmatch "(?m)^NOTION_TOKEN=\S+" -or $envText -notmatch "(?m)^NOTION_DATABASE_ID=\S+") {
    Skip "Notion の試験ページ" ".env に NOTION_TOKEN と NOTION_DATABASE_ID がありません（README の「Notion につなぐ」を参照）"
} elseif ($envText -notmatch "(?m)^NOTION_DATABASE_KIND=test\s*$") {
    Skip "Notion の試験ページ" "NOTION_DATABASE_KIND=test ではないため、本物の議事録とみなして読みません（実データは止めています）"
} else {
    $out = & $VPy -m pmo ingest-notion 2>&1 | Out-String
    Write-Host $out
    if ($LASTEXITCODE -eq 0) { Ok "Notion の試験ページ" "読み取れました（Notion には何も書き込んでいません）" }
    else { Fail "Notion の試験ページ" "読み取りに失敗しました" "インテグレーションをデータベースに「接続」したか確かめてください。" }
}
Remove-Item Env:\PMO_DB_PATH

# 8. BitLocker
Step 8 "ディスクの暗号化（BitLocker）を確かめます"
try {
    $bl = Get-BitLockerVolume -MountPoint "C:" -ErrorAction Stop
    if ("$($bl.ProtectionStatus)" -eq "On") { Ok "ディスクの暗号化" "C: は保護されています" }
    else { Fail "ディスクの暗号化" "C: の保護がオフです（$($bl.ProtectionStatus)）" "設定の「プライバシーとセキュリティ」→「デバイスの暗号化」をオンにすることを検討してください。" }
} catch {
    Skip "ディスクの暗号化" "管理者の権限が要るため確かめられませんでした。scripts\check_bitlocker.bat を右クリック→「管理者として実行」で確かめられます"
}

Summary
Stop-Transcript | Out-Null

# 9. 画面
Write-Host ""
Write-Host "[9] 画面を起動します。ブラウザが開きます。終える時はこの窓を閉じてください。" -ForegroundColor Cyan
Write-Host "   （はじめは左の「試験用の議事録10件を取り込む」を押すと、中身を試せます）"
& "$App\.venv\Scripts\streamlit.exe" run "$App\app\main.py"
