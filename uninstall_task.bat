@echo off
schtasks /Delete /F /TN "DailyCheckIn\daily" 2>nul
if errorlevel 1 (
    echo [INFO] Daily task not found or already removed.
) else (
    echo [OK] Daily task removed.
)

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v DailyCheckIn /f >nul 2>nul
if errorlevel 1 (
    echo [INFO] Logon run entry not found or already removed.
) else (
    echo [OK] Logon run entry removed.
)
pause
