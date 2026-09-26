@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

if "%~1"=="" (
  echo Использование: publish_telegram_example.bat YYYY-MM-DD
  echo Обычный daily workflow рекомендуется выполнять через Review Console.
  pause
  exit /b 1
)

"%PY%" publish_telegram.py --date %~1 --language ru
pause
