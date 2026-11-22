@echo off
chcp 65001 >nul
rem BitLocker status. Right-click and choose "Run as administrator".
net session >nul 2>&1
if errorlevel 1 (
  echo [停止] 管理者の権限が要ります。
  echo 　次の一手：このファイルを右クリックして「管理者として実行」を選んでください。
  pause
  exit /b 1
)
echo ===== ディスクの暗号化（BitLocker）の状態 =====
manage-bde -status C:
echo.
echo 「保護状態：保護はオンです」または「Protection Status: Protection On」なら暗号化されています。
pause
