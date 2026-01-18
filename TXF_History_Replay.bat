@echo off
setlocal
cd /d "%~dp0"

:loop
cls
echo ========================================================
echo       TXF Gale Engine - History Replay Launcher
echo ========================================================
echo.

:ask_date
set "target_date="
echo.
set /p "target_date=Enter Date (YYYY-MM-DD) [q to quit]: "
if /i "%target_date%"=="q" goto end
if "%target_date%"=="" goto ask_date

:ask_session
set "session_input=day"
set /p "inp=Session (n=night, d=day [Enter]): "
if /i "%inp%"=="n" set "session_input=night"
if /i "%inp%"=="night" set "session_input=night"
set "inp="

:ask_source
set "source_input=kafka"
set /p "inp=Source (p=parquet, k=kafka [Enter]): "
if /i "%inp%"=="p" set "source_input=parquet"
if /i "%inp%"=="parquet" set "source_input=parquet"
set "inp="

:ask_speed
set "speed_input=0"
set /p "speed_input=Speed (1=Realtime, 10=10x, 0=Max [Enter]): "

echo.
echo --------------------------------------------------------
echo Running: python -m bin.run_supervisor --mode history --date %target_date% --session %session_input% --source %source_input% --speed %speed_input%
echo --------------------------------------------------------
echo.

REM using the venv python directly
.venv\Scripts\python.exe -m bin.run_supervisor --mode history --date %target_date% --session %session_input% --source %source_input% --speed %speed_input%

echo.
goto loop

:end
echo Bye!
pause
