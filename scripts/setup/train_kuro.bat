@echo off
title Kuro Wake-Word Training
color 0B

echo ========================================================
echo KURO AI - WAKE WORD TRAINING
echo ========================================================
echo.
echo We are about to record your voice saying "Kuro" so the AI
echo can recognize your specific voice signature!
echo.
echo WARNING: 
echo - For the FIRST 20 recordings, press ENTER and say "Kuro" clearly.
echo - For the LAST 20 recordings, press ENTER and STAY COMPLETELY SILENT 
echo   or just let the microphone hear your keyboard/breathing.
echo   Do NOT say "Kuro" during the negative samples!
echo.
pause

echo.
echo Clearing old broken recordings...
del /Q voice\training\positive\*.wav 2>nul
del /Q voice\training\negative\*.wav 2>nul

echo.
echo Starting Voice Recorder...
.venv\Scripts\python.exe scripts\record_kuro.py

echo.
echo ========================================================
echo TRAINING AI CLASSIFIER...
echo ========================================================
.venv\Scripts\python.exe scripts\train_kuro.py

echo.
echo ========================================================
echo ALL DONE! 
echo Your new kuro_verifier.joblib has been created!
echo You can now close this window and launch KURO.bat.
echo ========================================================
pause
