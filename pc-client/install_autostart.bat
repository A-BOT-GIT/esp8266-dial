@echo off
REM ESP8266 Dial Listener - Register autostart (HKCU, no admin required)

setlocal
cd /d "%~dp0"

echo ============================================
echo ESP8266 Dial Listener - Install Autostart
echo ============================================
echo.

set "EXE_PATH=%cd%\dist\dial_listener.exe"

if not exist "%EXE_PATH%" (
    echo [ERROR] %EXE_PATH% not found.
    echo Run build_windows.bat first.
    pause
    exit /b 1
)

echo Will register autostart entry:
echo   %EXE_PATH%
echo.
set /p "CONFIRM=Proceed? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V DialListener /T REG_SZ /F /D "\"%EXE_PATH%\""

if errorlevel 1 (
    echo [ERROR] Registry write failed.
    pause
    exit /b 1
)

echo.
echo Autostart registered. Will run on next login.
echo.
echo To remove, run uninstall_autostart.bat
echo.
pause
endlocal
