@echo off
REM ESP8266 Dial Listener - Remove autostart

setlocal
echo ============================================
echo ESP8266 Dial Listener - Uninstall Autostart
echo ============================================
echo.

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V DialListener /F 2>nul

if errorlevel 1 (
    echo No autostart entry found.
) else (
    echo Autostart removed.
)

echo.

tasklist | findstr /I dial_listener.exe >nul 2>&1
if not errorlevel 1 (
    set /p "KILL=dial_listener.exe is running. Kill it? (Y/N): "
    if /i "%KILL%"=="Y" (
        taskkill /IM dial_listener.exe /F >nul 2>&1
        echo Process killed.
    )
)

pause
endlocal
