@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo.
echo [OK] Окружение готово.
echo Для AI/Telegram создайте .env из .env.example и заполните реальные значения локально.
echo Затем запускайте start_admin.bat
pause
