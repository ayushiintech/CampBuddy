@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    python start_campbuddy.py
) else (
    py -3 start_campbuddy.py
)
if errorlevel 1 pause
