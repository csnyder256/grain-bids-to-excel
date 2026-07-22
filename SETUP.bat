@echo off
setlocal enabledelayedexpansion
title BidBoard Setup
color 0A
cd /d "%~dp0"
set "LOG=%~dp0setup-log.txt"
set "PY=%~dp0app\python\python.exe"
echo ================ Setup started %date% %time% ================>> "%LOG%"

REM ---- Step 0: did they actually extract the zip? -------------------
if not exist "%PY%" goto :not_extracted
echo %~dp0 | find /I "\Temp\" >nul && goto :not_extracted

cls
echo.
echo   ============================================================
echo                     BidBoard  -  Setup
echo   ============================================================
echo.
echo   This gets everything ready. It takes about 3 to 6 minutes
echo   and needs an internet connection.
echo.
echo   You'll see some technical text scroll by during step 4 -
echo   that is completely normal.
echo.
echo   ------------------------------------------------------------
echo.

REM ---- Step 1: internet check --------------------------------------
echo   Step 1 of 6  -  Checking your internet connection...
"%PY%" -c "import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=10)" >> "%LOG%" 2>&1
if errorlevel 1 goto :no_internet
echo                  Connected.
echo.

REM ---- Step 2: pip bootstrap (skip if present) --------------------
echo   Step 2 of 6  -  Preparing the installer...
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
    "%PY%" "%~dp0app\get-pip.py" --no-warn-script-location >> "%LOG%" 2>&1
    if errorlevel 1 goto :pip_failed
)
echo                  Ready.
echo.

REM ---- Step 3: dependencies ---------------------------------------
echo   Step 3 of 6  -  Installing the app's components...
echo                  (This is the longest step - 2 to 4 minutes.)
echo.
"%PY%" -m pip install --no-warn-script-location -r "%~dp0app\requirements.txt"
if errorlevel 1 goto :deps_failed
"%PY%" -m pip freeze >> "%LOG%" 2>&1
echo.
echo                  Components installed.
echo.

REM ---- Step 4: optional mini-browser ------------------------------
echo   Step 4 of 6  -  The mini-browser (for JavaScript-based sites)
echo.
echo                  Some grain websites can only be read with a
echo                  built-in mini-browser (a one-time ~400 MB
echo                  download). Most people should install it now.
echo.
choice /c YN /t 30 /d Y /m "                 Install it now? (Y = yes, recommended)"
if errorlevel 2 (
    echo                  Skipped - you can add it later from Settings.>> "%LOG%"
    echo                  Skipped. You can add it later inside the app.
) else (
    echo                  Downloading Chromium - you'll see a progress bar.
    set "PLAYWRIGHT_BROWSERS_PATH=%~dp0app\browsers"
    "%PY%" -m playwright install chromium
    if errorlevel 1 (
        echo                  The download had a problem - you can retry from
        echo                  Settings inside the app later.
    ) else (
        echo                  Mini-browser installed.
    )
)
echo.

REM ---- Step 5: self-test ------------------------------------------
echo   Step 5 of 6  -  Checking everything works...
"%PY%" "%~dp0app\src\selftest.py" >> "%LOG%" 2>&1
if errorlevel 1 goto :selftest_failed
echo                  All checks passed.
echo.

REM ---- Step 6: done -----------------------------------------------
echo   Step 6 of 6  -  Finishing up...
echo ================ Setup completed OK %date% %time% ================>> "%LOG%"
echo.
echo   ============================================================
echo                     SETUP COMPLETE
echo.
echo     You're ready to go. Double-click RUN to start BidBoard.
echo     (You only need to run SETUP again if asked.)
echo   ============================================================
echo.
pause
exit /b 0

REM ================= failure handlers ==============================
:not_extracted
cls
echo.
echo   It looks like this file is still inside the zip.
echo.
echo   Please:
echo     1. Right-click the BidBoard zip file
echo     2. Choose "Extract All..."
echo     3. Open the new folder
echo     4. Double-click SETUP there
echo.
pause
exit /b 1

:no_internet
echo.
echo   I couldn't reach the internet. Please check your connection
echo   (or your firewall) and double-click SETUP again. Nothing was
echo   broken - it's safe to retry.
echo.
pause
exit /b 1

:pip_failed
echo.
echo   The installer couldn't finish setting itself up.
echo   Please double-click SETUP again. If it still fails, email the
echo   file "setup-log.txt" (right next to SETUP) to whoever set this up for you.
echo.
pause
exit /b 1

:deps_failed
echo.
echo   Installing the components didn't finish. This is usually a
echo   dropped internet connection. Please double-click SETUP again.
echo   If it keeps happening, email "setup-log.txt" (next to SETUP)
echo   to whoever set this up for you - it tells them exactly what happened.
echo.
pause
exit /b 1

:selftest_failed
echo.
echo   Setup installed everything but the final check didn't pass.
echo   Please email the file "setup-log.txt" (right next to SETUP)
echo   to whoever set this up for you - it tells them exactly what to fix.
echo.
pause
exit /b 1
