@echo off
REM ---------------------------------------------------------------------------
REM Start the tracker so other devices on THIS Wi-Fi can reach it (phone, tablet).
REM
REM run.bat  binds 127.0.0.1 - this PC only, nothing else can connect.
REM this one binds 0.0.0.0   - anything on your local network can connect.
REM
REM There is NO LOGIN. Anyone on the same Wi-Fi who opens the address below can
REM read your trade log and add or delete trades. On a home network that is
REM usually fine; on a cafe, hotel or office network it is not - use run.bat there.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No virtual environment found. Run setup.bat first.
  pause
  exit /b 1
)

echo.
echo   Open this on your phone (same Wi-Fi):
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo        http://%%b:8000
)
echo.
echo   Ctrl+C here stops it.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
