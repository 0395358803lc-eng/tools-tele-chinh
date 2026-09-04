@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Restore Multi TG Manager Backup

set "ROOT=%~dp0"
if "!ROOT:~-1!"=="\" set "ROOT=!ROOT:~0,-1!"
set "VENV_PY=!ROOT!\backend\.venv\Scripts\python.exe"
set "BACKUPS=!ROOT!\data\backups"

if not exist "!VENV_PY!" (
  echo [ERROR] Python environment is missing.
  echo Run START.bat once to install the backend dependencies, then stop the app.
  pause
  exit /b 1
)

if not exist "!BACKUPS!" (
  echo [ERROR] No backup folder exists yet: !BACKUPS!
  echo Create a backup from Settings before attempting a restore.
  pause
  exit /b 1
)

set "BACKUP_NAME=%~1"
if not defined BACKUP_NAME (
  echo.
  echo Available backups:
  echo --------------------------------------------
  set "FOUND="
  for /f "delims=" %%D in ('dir /b /ad /o-n "!BACKUPS!" 2^>nul') do (
    if exist "!BACKUPS!\%%D\manifest.json" (
      echo   %%D
      set "FOUND=1"
    )
  )
  if not defined FOUND (
    echo   ^(none^)
    echo.
    echo [ERROR] No valid backup containing manifest.json was found.
    pause
    exit /b 1
  )
  echo --------------------------------------------
  set /p "BACKUP_NAME=Type the backup name exactly as shown above: "
)

if not defined BACKUP_NAME (
  echo [ERROR] Backup name is required.
  pause
  exit /b 1
)

echo.
echo [restore] Validating and restoring "!BACKUP_NAME!"...
pushd "!ROOT!\backend"
"!VENV_PY!" restore_backup.py "!BACKUP_NAME!"
set "RC=!errorlevel!"
popd

if not "!RC!"=="0" (
  echo.
  echo [ERROR] Restore failed. Existing runtime data was kept or rolled back.
  pause
  exit /b !RC!
)

echo.
echo [OK] Restore completed successfully.
echo You can now run START.bat.
pause
exit /b 0
