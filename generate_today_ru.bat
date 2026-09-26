@echo off
chcp 65001 >nul
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe run_daily.py --language ru
) else (
  python run_daily.py --language ru
)
pause
