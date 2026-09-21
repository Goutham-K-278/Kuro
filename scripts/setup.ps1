#!/usr/bin/env pwsh
# ============================================================
# KURO Setup Script - One-time initialization
# Adds venv to PATH and configures environment
# Usage: .\setup.ps1
# ============================================================

#Requires -Version 5.0

Write-Host "`nKURO Project Setup" -ForegroundColor Cyan
Write-Host "=" * 60

# Get the script directory (project root)
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot ".venv\Scripts"

Write-Host "`nProject Root: $projectRoot" -ForegroundColor Gray
Write-Host "venv Path: $venvPath" -ForegroundColor Gray

# Step 1: Add venv to User PATH
Write-Host "`n[1/3] Adding venv to system PATH..." -ForegroundColor Yellow

$currentPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")

if ($currentPath -like "*$venvPath*") {
    Write-Host "[OK] venv already in PATH" -ForegroundColor Green
} else {
    $newPath = "$venvPath;$currentPath"
    [System.Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Host "[OK] Added venv to PATH" -ForegroundColor Green
    Write-Host "[WARN] Restart PowerShell/VS Code for changes to take effect" -ForegroundColor Yellow
}

# Step 2: Verify venv Python
Write-Host "`n[2/3] Verifying Python in venv..." -ForegroundColor Yellow

$pythonExe = Join-Path $venvPath "python.exe"
if (Test-Path $pythonExe) {
    $pythonVersion = & $pythonExe --version 2>&1
    Write-Host "[OK] Python found: $pythonVersion" -ForegroundColor Green
    
    # Check key packages
    $packages = @("ollama", "fastapi", "chromadb", "pyautogui", "pytesseract")
    Write-Host "`n   Checking packages..." -ForegroundColor Gray
    foreach ($pkg in $packages) {
        $check = & $pythonExe -c "import $pkg; print('OK')" 2>&1
        if ($check -eq "OK") {
            Write-Host "   [OK] $pkg" -ForegroundColor Green
        } else {
            Write-Host "   [WARN] $pkg missing" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[ERROR] Python not found at $pythonExe" -ForegroundColor Red
    exit 1
}

# Step 3: Verify npm scripts
Write-Host "`n[3/3] Verifying npm setup..." -ForegroundColor Yellow

$packageJson = Join-Path $projectRoot "kuro-ai\package.json"
if (Test-Path $packageJson) {
    Write-Host "[OK] kuro-ai/package.json found" -ForegroundColor Green
    Write-Host "`n   Available commands in kuro-ai:" -ForegroundColor Gray
    Write-Host "   npm run dev:desktop     (Start everything)" -ForegroundColor Cyan
    Write-Host "   npm run dev:brain       (Just Python brain)" -ForegroundColor Cyan
    Write-Host "   npm run dev:frontend    (Just React app)" -ForegroundColor Cyan
    Write-Host "   npm run dev:backend     (Just Node backend)" -ForegroundColor Cyan
} else {
    Write-Host "[WARN] kuro-ai/package.json not found" -ForegroundColor Yellow
}

# Summary
Write-Host "`n" + "=" * 60
Write-Host "[OK] Setup Complete!" -ForegroundColor Green

Write-Host "`nNext Steps:" -ForegroundColor Cyan
Write-Host "   1. Restart PowerShell or VS Code terminal" -ForegroundColor Gray
Write-Host "   2. Navigate to kuro-ai: cd kuro-ai" -ForegroundColor Gray
Write-Host "   3. Run: npm run dev:desktop" -ForegroundColor Gray

Write-Host "`nOr use the launcher scripts from project root:" -ForegroundColor Cyan
Write-Host "   .\dev.ps1       (PowerShell)" -ForegroundColor Gray
Write-Host "   .\dev.bat       (Command Prompt)" -ForegroundColor Gray
Write-Host "   ./dev.sh        (Git Bash/WSL)" -ForegroundColor Gray

Write-Host "`n" + "=" * 60 + "`n"
