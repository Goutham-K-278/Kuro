@echo off
title Installing Kuro GPU Drivers (CUDA)
color 0A

echo ========================================================
echo KURO AI - GPU DRIVER INSTALLATION
echo ========================================================
echo.
echo Your system currently has the "CPU-only" version of PyTorch.
echo We are now replacing it with the NVIDIA CUDA version so Kuro 
echo can run natively on your GPU!
echo.
echo This might take a few minutes (downloading ~2.5 GB).
echo Please do not close this window until it says "ALL DONE".
echo.

echo [1/2] Uninstalling CPU version...
.venv\Scripts\python.exe -m pip uninstall -y torch torchvision torchaudio

echo.
echo [2/2] Installing CUDA 12.1 version (this will take a while)...
.venv\Scripts\python.exe -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

echo.
echo ========================================================
echo ALL DONE! GPU DRIVERS INSTALLED!
echo ========================================================
echo You can now close this window and launch KURO.bat!
pause
