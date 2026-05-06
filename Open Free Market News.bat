@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python app\launcher.py
) else (
    py -3 app\launcher.py
)

if errorlevel 1 (
    echo.
    echo Free Market News could not start. Make sure Python is installed.
    pause
)
