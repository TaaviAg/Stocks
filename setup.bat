@echo off
REM One-time setup: create the virtual environment and install dependencies.
cd /d "%~dp0"
python -m venv .venv || goto :err
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :err
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
echo.
echo Done. Start the app with run.bat
pause
exit /b 0
:err
echo Setup failed.
pause
exit /b 1
