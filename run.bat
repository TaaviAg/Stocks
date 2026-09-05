@echo off
REM Start the Stock Trading Tracker and open it in the default browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No virtual environment found. Run setup.bat first.
  pause
  exit /b 1
)
echo Starting on http://127.0.0.1:8000  --  Ctrl+C to stop.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
