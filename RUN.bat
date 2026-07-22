@echo off
setlocal
title BidBoard
cd /d "%~dp0"
set "PY=%~dp0app\python\python.exe"

if not exist "app\data\.setup-complete" goto :needs_setup

set "PLAYWRIGHT_BROWSERS_PATH=%~dp0app\browsers"
"%PY%" "%~dp0app\src\launcher.py"
if errorlevel 1 goto :crashed
exit /b 0

:needs_setup
cls
echo.
echo   It looks like Setup hasn't been completed yet.
echo.
echo   Please double-click SETUP first, wait for "SETUP COMPLETE",
echo   then double-click RUN again.
echo.
pause
exit /b 1

:crashed
echo.
echo   BidBoard stopped unexpectedly. This is usually harmless -
echo   just double-click RUN again.
echo.
echo   If it keeps happening, email whoever set this up for you the file:
echo     app\data\logs\server-log.txt
echo.
pause
exit /b 1
