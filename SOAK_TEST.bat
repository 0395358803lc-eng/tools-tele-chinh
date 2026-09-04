@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0soak_test.ps1" %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%
