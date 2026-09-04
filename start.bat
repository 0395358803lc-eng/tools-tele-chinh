@echo off
title Multi TG Manager
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "!ROOT:~-1!"=="\" set "ROOT=!ROOT:~0,-1!"
cd /d "!ROOT!"

echo.
echo ============================================
echo   Multi TG Manager
echo   !ROOT!
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  echo Install Python 3.10+ from https://python.org
  pause
  exit /b 1
)

set "VENV_PY=!ROOT!\backend\.venv\Scripts\python.exe"
if not exist "!VENV_PY!" (
  echo [setup] Creating Python venv...
  python -m venv "!ROOT!\backend\.venv"
)

set "LOCKFILE=!ROOT!\backend\requirements.lock"
set "LOCKMARK=!ROOT!\backend\.venv\.requirements-lock.sha256"
if not exist "!LOCKFILE!" (
  echo [ERROR] backend\requirements.lock is missing.
  pause
  exit /b 1
)
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '!LOCKFILE!').Hash"') do set "LOCKHASH=%%H"
set "OLDLOCKHASH="
if exist "!LOCKMARK!" set /p OLDLOCKHASH=<"!LOCKMARK!"
"!VENV_PY!" "!ROOT!\backend\check_requirements.py" >nul 2>nul
set "DEPS_OK=!errorlevel!"
if /I not "!OLDLOCKHASH!"=="!LOCKHASH!" set "DEPS_OK=1"

if not "!DEPS_OK!"=="0" (
  echo [setup] Installing Python packages... please wait 1-3 minutes
  "!VENV_PY!" -m pip install --upgrade pip
  "!VENV_PY!" -m pip install -r "!LOCKFILE!"
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
  >"!LOCKMARK!" echo !LOCKHASH!
)
"!VENV_PY!" -m pip check >nul
if errorlevel 1 (
  echo [ERROR] Installed Python packages have incompatible dependencies.
  pause
  exit /b 1
)

set "ENVFILE=!ROOT!\backend\.env"
set "ENVEXAMPLE=!ROOT!\backend\.env.example"
echo [check] Looking for env at: !ENVFILE!

if not exist "!ENVFILE!" (
  echo [setup] Creating backend\.env from .env.example...
  if not exist "!ENVEXAMPLE!" (
    echo [ERROR] backend\.env.example is missing.
    pause
    exit /b 1
  )
  copy /Y "!ENVEXAMPLE!" "!ENVFILE!" >nul
  "!VENV_PY!" -c "import secrets,re,pathlib,os; p=pathlib.Path(os.environ['ENVFILE']); t=p.read_text(encoding='utf-8'); t=re.sub(r'SESSION_SECRET=.*', 'SESSION_SECRET='+secrets.token_urlsafe(48), t, count=1); p.write_text(t, encoding='utf-8')"
  echo.
  echo ============================================
  echo   FIRST RUN - please fill backend\.env
  echo ============================================
  echo   - TG_API_ID    ^(from https://my.telegram.org^)
  echo   - TG_API_HASH  ^(from https://my.telegram.org^)
  echo   - APP_PASSWORD ^(your login password^)
  echo.
  echo   Save Notepad, close it, then re-run start.bat
  echo ============================================
  start "" notepad "!ENVFILE!"
  pause
  exit /b 0
) else (
  echo [check] backend\.env found.
)

rem ---- pre-flight: fail fast on missing/invalid Telegram API credentials ----
set "ENVCHECK="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "!ROOT!\backend\check_env.ps1" -EnvPath "!ENVFILE!"`) do set "ENVCHECK=%%A"

if not "!ENVCHECK!"=="OK" (
  echo.
  echo ============================================
  echo   INVALID BACKEND CONFIGURATION
  echo.
  if "!ENVCHECK!"=="BADHASH" (
    echo   TG_API_ID is set but TG_API_HASH is empty.
  ) else if "!ENVCHECK!"=="BADPASSWORD" (
    echo   APP_PASSWORD must be 12-256 characters and cannot be the example value.
  ) else if "!ENVCHECK!"=="BADSECRET" (
    echo   SESSION_SECRET must be 48-1024 characters.
  ) else (
    echo   TG_API_ID is empty or not a valid positive integer.
  )
  echo.
  echo   Get your own from https://my.telegram.org
  echo   Then fill TG_API_ID and TG_API_HASH in
  echo   backend\.env and run start.bat again.
  echo ============================================
  start "" notepad "!ENVFILE!"
  pause
  exit /b 1
)

rem ---- single instance / clear port conflict handling ----
powershell -NoProfile -Command "try { $h=Invoke-RestMethod -TimeoutSec 2 http://127.0.0.1:8000/api/health; if ($h.backend -eq 'ok') { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
  echo [info] Multi TG Manager is already running. Opening it now...
  start "" http://127.0.0.1:8000
  exit /b 0
)
netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul
if not errorlevel 1 (
  echo [ERROR] Port 8000 is already in use by another application.
  echo Close that application or free port 8000, then run START.bat again.
  pause
  exit /b 1
)

pushd "!ROOT!\backend"
"!VENV_PY!" -c "from app.config import settings; settings.ensure_data_directories(); p=settings.database_path.parent/'write.test'; p.write_text('ok'); p.unlink()" >nul 2>nul
set "DATA_OK=!errorlevel!"
popd
if not "!DATA_OK!"=="0" (
  echo [ERROR] The data folder is not writable: !ROOT!\data
  pause
  exit /b 1
)

rem Release ZIPs already contain backend\static and therefore need no Node.js.
rem A source checkout may not contain built assets, so build them deterministically
rem from package-lock.json when the frontend source tree is available.
if not exist "!ROOT!\backend\static\index.html" (
  if not exist "!ROOT!\frontend\package.json" (
    echo [ERROR] Built frontend is missing and no frontend source tree was found.
    echo Download a release ZIP or restore backend\static before starting.
    pause
    exit /b 1
  )
  where node >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] This is a source checkout and the frontend is not built.
    echo Install Node.js 20.19+ or 22.12+, then run START.bat again.
    echo Release ZIP users do not need Node.js.
    pause
    exit /b 1
  )
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] npm was not found on PATH.
    pause
    exit /b 1
  )
  echo [build] Frontend bundle is missing; building from source...
  pushd "!ROOT!\frontend"
  call npm ci
  if errorlevel 1 (
    popd
    echo [ERROR] npm ci failed.
    pause
    exit /b 1
  )
  call npm run build
  set "FRONTEND_RC=!errorlevel!"
  popd
  if not "!FRONTEND_RC!"=="0" (
    echo [ERROR] Frontend build failed.
    pause
    exit /b 1
  )
  if not exist "!ROOT!\backend\static\index.html" (
    echo [ERROR] Frontend build completed without producing backend\static\index.html.
    pause
    exit /b 1
  )
)

echo.
echo ============================================
echo   Server: http://localhost:8000
echo   Close this window to stop.
echo ============================================
echo.

start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"

cd /d "!ROOT!\backend"
set PYTHONUNBUFFERED=1
"!VENV_PY!" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info

echo.
echo Server stopped.
pause
endlocal
