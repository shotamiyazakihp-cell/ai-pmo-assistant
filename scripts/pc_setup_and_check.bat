@echo off
chcp 65001 >nul
setlocal
rem AI PMO Assistant: PC setup and check. Double-click this file. Safe to run many times.
set "REPO_URL=https://github.com/shotamiyazakihp-cell/ai-pmo-assistant.git"
set "APPDIR=%USERPROFILE%\Documents\ai-pmo-assistant"
echo ==========================================================
echo   AI PMO Assistant　セットアップと確認
echo   置き場所：%APPDIR%
echo ==========================================================
where git >nul 2>&1
if errorlevel 1 (
  echo [停止] Git が見つかりません。
  echo 　次の一手：https://git-scm.com/download/win から Git を入れて、もう一度このファイルをダブルクリックしてください。
  pause
  exit /b 1
)
if not exist "%APPDIR%\.git" (
  echo [取得] 初回のため GitHub から取得します。ログインの画面が出たら、GitHub にログインしてください。
  git clone "%REPO_URL%" "%APPDIR%"
  if errorlevel 1 (
    echo [停止] 取得に失敗しました。
    echo 　次の一手：GitHub にログインできているか、ネットにつながっているかを確かめて、もう一度ダブルクリックしてください。
    pause
    exit /b 1
  )
) else (
  echo [更新] 最新のコードを取得します。
  git -C "%APPDIR%" pull --ff-only
  if errorlevel 1 echo [注意] 最新の取得に失敗しました。手元の版のまま続けます。
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\scripts\pc_setup_and_check.ps1"
echo.
echo 終わりました。この窓は閉じてかまいません。
pause
