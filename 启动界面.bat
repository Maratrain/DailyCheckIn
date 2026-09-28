@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYW="

for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYW set "PYW=%%i"
)
if not defined PYW for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYW set "PYW=%%i"
)

if not defined PYW (
    echo [ERROR] Python not found in PATH. Please install Python first.
    pause
    exit /b 1
)

start "" "%PYW%" "%SCRIPT_DIR%gui.py"
