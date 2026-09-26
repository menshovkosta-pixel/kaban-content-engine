@echo off
setlocal
chcp 65001 >nul

set "SOURCE=%~dp0..\CAELUS_generator_v4_4"
set "TARGET=%~dp0"

if not exist "%SOURCE%" (
  echo [ERROR] Не найдена соседняя папка CAELUS_generator_v4_4
  echo Ожидаемый путь: %SOURCE%
  pause
  exit /b 1
)

if exist "%SOURCE%\.env" (
  copy /Y "%SOURCE%\.env" "%TARGET%.env" >nul
  echo [OK] .env перенесен
) else (
  echo [WARN] .env в v4_4 не найден. Его нужно создать вручную.
)

if exist "%SOURCE%\generated" (
  robocopy "%SOURCE%\generated" "%TARGET%generated" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 (
    echo [ERROR] Не удалось перенести generated
    pause
    exit /b 1
  )
  echo [OK] generated перенесен: approval, publication history, cards, Telegram journal
) else (
  echo [WARN] Папка generated в v4_4 не найдена.
)

echo.
echo Миграция v4.4 -> v4.5 завершена.
echo Теперь запустите start_admin.bat
pause
