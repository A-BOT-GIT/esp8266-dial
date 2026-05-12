@echo off
REM ESP8266 Dial Listener - Debug run (foreground, shows logs)

setlocal
cd /d "%~dp0"

echo ============================================
echo ESP8266 Dial Listener - Debug Mode
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ with "Add to PATH".
    pause
    exit /b 1
)

REM Auto-install deps on first run
python -c "import pynput, serial" 2>nul
if errorlevel 1 (
    echo [INFO] First run, installing dependencies...
    python -m pip install -r requirements.txt
    echo.
)

echo Press Ctrl+C to exit.
echo.
python dial_listener.py

pause
endlocal
