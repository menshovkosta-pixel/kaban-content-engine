@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

REM Безопасная тестовая дата, чтобы не перезаписать рабочий daily dataset.
"%PY%" run_daily.py --date 2099-12-31 --language ru --mock
pause
