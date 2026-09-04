@echo off
title Stop Multi TG Manager
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "!ROOT:~-1!"=="\" set "ROOT=!ROOT:~0,-1!"
set "SERVER_PID="
for /f %%p in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue ^| Select-Object -First 1; if($c){$c.OwningProcess}"') do set "SERVER_PID=%%p"

if not defined SERVER_PID (
  echo [info] No process is listening on 127.0.0.1:8000.
  goto VERIFY_WAL
)

echo Stopping PID !SERVER_PID! on 127.0.0.1:8000...
set "SHUTDOWN_TOKEN="
pushd "!ROOT!\backend"
for /f %%t in ('".venv\Scripts\python.exe" -c "import hmac,hashlib; from app.config import settings; print(hmac.new(settings.SESSION_SECRET.encode('utf-8'), b'!SERVER_PID!', hashlib.sha256).hexdigest())"') do set "SHUTDOWN_TOKEN=%%t"
popd
if not defined SHUTDOWN_TOKEN goto FORCE
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod -Method Post -TimeoutSec 5 -Headers @{'X-MTM-Shutdown-Token'='!SHUTDOWN_TOKEN!'} http://127.0.0.1:8000/api/app/shutdown | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
set "SHUTDOWN_TOKEN="
if errorlevel 1 goto FORCE

set /a WAIT=0
:WAITLOOP
set /a WAIT+=1
if !WAIT! geq 30 goto FORCE
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue ^| Where-Object OwningProcess -eq !SERVER_PID!; if($c){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 goto VERIFY_WAL
timeout /t 1 /nobreak >nul
goto WAITLOOP

:FORCE
rem Force only the PID originally verified as the loopback listener, and only
rem if that same PID still owns the endpoint after the graceful wait.
powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue ^| Where-Object OwningProcess -eq !SERVER_PID!; if($c){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 goto VERIFY_WAL
echo [warn] Graceful shutdown exceeded 30 seconds; force-stopping PID !SERVER_PID!.
taskkill /PID !SERVER_PID! /F >nul 2>nul

:VERIFY_WAL
powershell -NoProfile -Command "$p='!ROOT!\data\database\app.db-wal'; if((Test-Path -LiteralPath $p) -and (Get-Item -LiteralPath $p).Length -gt 0){exit 1}else{exit 0}" >nul 2>nul
if errorlevel 1 (
  echo [warn] SQLite WAL is not empty; the last shutdown may not have checkpointed cleanly.
) else (
  echo [ok] SQLite WAL checkpoint verified.
)
echo Done.
timeout /t 2 >nul
endlocal
