@echo off
:: Kuro — One-click launcher
:: Double-click this file from Explorer or run: start.bat

title Kuro Launcher
color 0D

echo.
echo  ██╗  ██╗██╗   ██╗██████╗  ██████╗ 
echo  ██║ ██╔╝██║   ██║██╔══██╗██╔═══██╗
echo  █████╔╝ ██║   ██║██████╔╝██║   ██║
echo  ██╔═██╗ ██║   ██║██╔══██╗██║   ██║
echo  ██║  ██╗╚██████╔╝██║  ██║╚██████╔╝
echo  ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ 
echo.
echo  Starting Kuro AI Assistant...
echo.

:: Change to the project root (same folder as this .bat)
cd /d "%~dp0"

:: Run unified launcher
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start.ps1"

:: If PowerShell exits with an error, pause so the user can read it
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [ERROR] Kuro failed to start. See messages above.
    pause
)
