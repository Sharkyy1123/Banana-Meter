@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python from https://www.python.org/downloads/
  echo During installation, tick "Add Python to PATH", then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)

echo Installing required packages if needed...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check that you are connected to the internet.
  pause
  exit /b 1
)

echo.
echo Banana Sense is starting. Open http://127.0.0.1:5000 in your browser.
echo Keep this window open while using the website. Press Ctrl+C to stop it.
echo.
".venv\Scripts\python.exe" app.py
pause

