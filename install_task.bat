@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE="

for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python not found in PATH. Please install Python first.
    pause
    exit /b 1
)

schtasks /Create /F /TN "DailyCheckIn\daily" /TR "\"%PYTHON_EXE%\" \"%SCRIPT_DIR%checkin.py\" run" /SC DAILY /ST 08:05
if errorlevel 1 (
    echo [WARN] Failed to create daily scheduled task.
) else (
    echo [OK] Daily task registered: every day 08:05
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v DailyCheckIn /t REG_SZ /d "\"%PYTHON_EXE%\" \"%SCRIPT_DIR%checkin.py\" run" /f >nul
if errorlevel 1 (
    echo [WARN] Failed to register logon run entry.
) else (
    echo [OK] Logon task registered: runs once at user logon
)

echo.
echo Done. To change the run time, edit /ST 08:05 in this file and run it again.
echo To remove both tasks, run uninstall_task.bat
pause
