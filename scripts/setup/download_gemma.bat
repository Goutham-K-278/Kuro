@echo off
title Downloading Kuro Lightweight AI
color 0B

echo ========================================================
echo KURO AI - LIGHTWEIGHT MODEL DOWNLOAD
echo ========================================================
echo.
echo The 9.6GB model is too heavy for this laptop.
echo We are now downloading the "gemma2:2b" model, which is 
echo an extremely fast and smart 2 Billion parameter model!
echo.
echo This model is only ~1.6 GB, so it will download very fast
echo and will never run out of memory on your system.
echo.

ollama pull gemma2:2b

echo.
echo ========================================================
echo ALL DONE! 
echo The lightweight AI model is installed!
echo You can now close this window and launch KURO.bat.
echo ========================================================
pause
