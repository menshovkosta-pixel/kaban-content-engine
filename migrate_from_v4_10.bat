@echo off
setlocal
chcp 65001 >nul

set "SOURCE=%~dp0..\CAELUS_generator_v4_10"
set "TARGET=%~dp0"

if not exist "%SOURCE%" (
  echo [ERROR] Не найдена соседняя папка CAELUS_generator_v4_10
  echo Ожидаемый путь: %SOURCE%
  pause
  exit /b 1
)

if exist "%SOURCE%\.env" (
  copy /Y "%SOURCE%\.env" "%TARGET%.env" >nul
  echo [OK] .env перенесен локально
) else (
  echo [INFO] .env в v4.10 не найден. Создайте его из .env.example при необходимости.
)

if exist "%SOURCE%\generated" (
  robocopy "%SOURCE%\generated" "%TARGET%generated" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo [ERROR] Не удалось перенести generated
    pause
    exit /b 1
  )
  echo [OK] generated перенесен
)

echo.
echo Миграция v4.10 -^> v4.11 завершена.
echo Настройки уникальности теперь можно менять в Review Console.
echo Запустите start_admin.bat
pause
