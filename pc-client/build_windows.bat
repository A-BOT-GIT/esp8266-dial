@echo off
REM ESP8266 Dial Listener - Build Windows exe

setlocal
cd /d "%~dp0"

echo ============================================
echo ESP8266 Dial Listener - Build Windows exe
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Install Python 3.10+ with "Add to PATH".
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    pause
    exit /b 1
)
echo.

echo [2/4] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist dial_listener.spec del /q dial_listener.spec
echo.

echo [3/4] Building...
python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --name dial_listener ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    dial_listener.py

if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [4/4] Cleaning temp files...
if exist build rmdir /s /q build
if exist dial_listener.spec del /q dial_listener.spec

echo.
echo ============================================
echo Build complete.
echo Output: %cd%\dist\dial_listener.exe
echo Log:    %%LOCALAPPDATA%%\dial\dial.log
echo ============================================
echo.
pause
endlocal
