@echo off
setlocal
chcp 65001 >nul
title Gale Engine Supervisor

:: ==========================================
:: Gale Engine Launcher
:: ==========================================

:: 1. Graceful Shutdown Request
echo [1/4] ♻️ Requesting Graceful Shutdown...
:: Ensure we are in the project root
if not exist "bin\run_supervisor.py" (
    echo [ERROR] Not in project root! Cannot find bin\run_supervisor.py
    pause
    exit /b 1
)
echo. > .restart_signal

:: Wait loop (Max 10 seconds - Supervisor sleeps 1s so we need >1s)
set "wait_count=0"
:WAIT_LOOP
timeout /t 1 /nobreak >nul
set /a wait_count+=1

:: Check if Supervisor is still running
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bin.run_supervisor*' -and $_.Name -like 'python*' }) { exit 1 } else { exit 0 }"
if %errorlevel%==0 (
    echo [INFO] Supervisor has exited gracefully.
    goto :CLEAN_START
)

echo ... waiting for shutdown (%wait_count%/10)
if %wait_count% lss 10 goto :WAIT_LOOP

:: 2. Force Kill (Optimization: If graceful fails)
echo [2/4] ⚠️ Graceful shutdown timed out. Force killing...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -like '*bin.run_supervisor*' -or $_.CommandLine -like '*gale.feed.ingest*' -or $_.CommandLine -like '*bin.run_dashboard*') -and $_.Name -like 'python*' } | Stop-Process -Force -ErrorAction SilentlyContinue"

:: 3. Port Cleanup (Safety Net inspired by AutoRun.md)
echo [3/4] 🧹 Cleaning up ports 8050/8051...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8050" ^| find "LISTENING"') do taskkill /f /pid %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8051" ^| find "LISTENING"') do taskkill /f /pid %%a >nul 2>&1


:CLEAN_START
if exist .restart_signal del .restart_signal

:: 2. Activate Environment
echo [2/3] Activating Virtual Environment...
call .venv\Scripts\activate.bat

:: 3. Run Supervisor
echo [3/3] Starting Supervisor...
echo.

:: Check if arguments are provided. If not, default to the last used history command (optional, but safer for testing).
:: Uncomment the next line to set default args if you double-click directly.
:: set "DEFAULT_ARGS=--mode history --date 2026-01-14 --session night"

if "%~1"=="" (
    echo No arguments provided. Running with defaults...
    :: Use DEFAULT_ARGS if set, otherwise just run raw (which defaults to Live Mode)
    python -m bin.run_supervisor %DEFAULT_ARGS%
) else (
    echo Running with provided arguments: %*
    python -m bin.run_supervisor %*
)

echo.
echo ===========================================
echo  Engine stopped. (Exit Code: %errorlevel%)
echo ===========================================
pause
