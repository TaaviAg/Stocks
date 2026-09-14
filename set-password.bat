@echo off
REM Set or change the password your phone uses to open the tracker.
REM Changing it signs every device out.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No virtual environment found. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\set_password.py
echo.
pause
