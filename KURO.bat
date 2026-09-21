@echo off
chcp 65001 >nul
:: ══════════════════════════════════════════════════════════════
::  KURO — One-Click Launcher
::  Double-click this file to start Kuro AI Assistant
:: ══════════════════════════════════════════════════════════════
title KURO — AI Assistant Launcher
color 0B

:: Move to the project folder (same location as this .bat file)
cd /d "%~dp0"

cls
echo.
echo   ██╗  ██╗██╗   ██╗██████╗  ██████╗
echo   ██║ ██╔╝██║   ██║██╔══██╗██╔═══██╗
echo   █████╔╝ ██║   ██║██████╔╝██║   ██║
echo   ██╔═██╗ ██║   ██║██╔══██╗██║   ██║
echo   ██║  ██╗╚██████╔╝██║  ██║╚██████╔╝
echo   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝
echo.
echo   Starting Kuro AI Assistant...
echo.

:: Launch the PowerShell launcher (visible window so you can see logs)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launchers\launch.ps1"

:: If something went wrong, pause so you can read the error
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo   [ERROR] Kuro exited with an error. See messages above.
    pause
)
