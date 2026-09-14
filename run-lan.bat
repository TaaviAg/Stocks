@echo off
REM ---------------------------------------------------------------------------
REM Start the tracker so your phone can open it over your HOME Wi-Fi.
REM
REM run.bat      binds 127.0.0.1 - this PC only.
REM this one     binds 0.0.0.0   - devices on your network can connect.
REM
REM Other devices must log in with the password from set-password.bat. The
REM laptop itself never has to. Without a password this refuses to start.
REM
REM Use it on your own network only. On a cafe, hotel or office Wi-Fi, use
REM run.bat instead.
REM ---------------------------------------------------------------------------
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No virtual environment found. Run setup.bat first.
  pause
  exit /b 1
)
if not exist "data\auth.json" (
  echo.
  echo   No password set yet. Your phone would reach the tracker with no login.
  echo   Run set-password.bat first, then start this again.
  echo.
  pause
  exit /b 1
)

echo.
echo   Open this on your phone ^(same Wi-Fi^):
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo        http://%%b:8000
)
echo.
echo   Then use your browser's "Add to Home Screen" for an app icon.
echo   Ctrl+C here stops it.
echo.
REM --no-proxy-headers: a network client must not be able to claim it is the
REM laptop (127.0.0.1) through a forwarded header and skip the login.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
