@echo off
setlocal
chcp 65001 >nul

set "SOURCE=%~dp0..\CAELUS_generator_v4_9"
set "TARGET=%~dp0"

if not exist "%SOURCE%" (
  echo [ERROR] Не найдена соседняя папка CAELUS_generator_v4_9
  echo Ожидаемый путь: %SOURCE%
  pause
  exit /b 1
)

if exist "%SOURCE%\.env" (
  copy /Y "%SOURCE%\.env" "%TARGET%.env" >nul
  echo [OK] .env перенесен локально

  rem v4.10 снижает blocking/auto-regeneration threshold с 86%% до 80%%.
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$p='%TARGET%.env'; $lines=@(Get-Content -LiteralPath $p); $found=$false; $out=@(); foreach($line in $lines){ if($line -match '^\s*CONTENT_UNIQUENESS_HARD_THRESHOLD\s*='){ $out += 'CONTENT_UNIQUENESS_HARD_THRESHOLD=0.80'; $found=$true } else { $out += $line } }; if(-not $found){ $out += 'CONTENT_UNIQUENESS_HARD_THRESHOLD=0.80' }; [System.IO.File]::WriteAllLines($p, $out, (New-Object System.Text.UTF8Encoding($false)))"
  if errorlevel 1 (
    echo [ERROR] Не удалось обновить CONTENT_UNIQUENESS_HARD_THRESHOLD в .env
    pause
    exit /b 1
  )
  echo [OK] Порог исторического повтора установлен на 80%%
) else (
  echo [INFO] .env в v4.9 не найден. Создайте его из .env.example при необходимости.
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
echo Миграция v4.9 -^> v4.10 завершена.
echo Запустите start_admin.bat
pause
