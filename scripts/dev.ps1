#!/usr/bin/env pwsh
# Kuro full-stack launcher — starts ALL 4 services with one command
#   1. Python Brain API      (port 8000)  — FastAPI + voice + LLM
#   2. Node.js Backend       (port 3001)  — Express proxy
#   3. Vite Frontend         (port 5173)  — React UI
#   4. Electron Overlay                   — desktop overlay
#
# Usage (from project root):
#   .\scripts\dev.ps1          (PowerShell)
#   npm start                  (if root package.json present)

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force 2>$null

# Root is the parent of this scripts/ folder
$root = (Resolve-Path "$PSScriptRoot\..").Path

Write-Host ""
Write-Host "  KURO — Starting all services..." -ForegroundColor Magenta
Write-Host "  Brain :8000  |  Backend :3001  |  Vite :5173  |  Electron" -ForegroundColor DarkGray
Write-Host ""

# ── 0. Kill any stale processes on ports 8000 and 3001 ───────────────────────
Write-Host "[Kuro] Cleaning up stale processes..." -ForegroundColor DarkGray
foreach ($portPattern in @(':8000 ', ':3001 ')) {
    $stalePids = netstat -ano 2>$null | Select-String $portPattern | ForEach-Object {
        ($_ -split '\s+')[-1]
    } | Sort-Object -Unique
    foreach ($procId in $stalePids) {
        if ($procId -match '^\d+$' -and $procId -ne '0') {
            try { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}
Get-Process -Name python, python3 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
Write-Host "[Kuro] All clear." -ForegroundColor DarkGray

# ── 1. Verify .venv exists ───────────────────────────────────────────────────
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] .venv not found at $root\.venv" -ForegroundColor Red
    Write-Host "        Run: python -m venv .venv  then  pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

# ── 2. Start Python Brain API in a dedicated window ─────────────────────────
Write-Host "[Kuro] Starting Python Brain (port 8000)..." -ForegroundColor Cyan
$pyCmd = "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force 2>`$null; " +
         "Set-Location '$root'; " +
         "`$host.UI.RawUI.WindowTitle = 'Kuro Brain'; " +
         "Write-Host '[Kuro Brain] Loading models...' -ForegroundColor Cyan; " +
         "& '$venvPython' main.py"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $pyCmd

# ── 3. Wait for Brain /health to return ready=true (max 90s) ────────────────
Write-Host "[Kuro] Waiting for Brain to be ready..." -ForegroundColor DarkGray
$maxWait = 90; $waited = 0; $ready = $false
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 2; $waited += 2
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction Stop
        if ($resp.ready -eq $true) {
            Write-Host "[Kuro] Brain READY! ($waited s)" -ForegroundColor Green
            $ready = $true; break
        }
        Write-Host "[Kuro]   loading... ($waited s)" -ForegroundColor DarkGray
    } catch {
        Write-Host "[Kuro]   not up yet ($waited s)" -ForegroundColor DarkGray
    }
}
if (-not $ready) {
    Write-Host "[WARN] Brain not ready after ${maxWait}s — starting frontend anyway." -ForegroundColor Yellow
}

# ── 4. Start Node backend + Vite frontend + Electron in this terminal ────────
Write-Host "[Kuro] Launching Node backend + Vite + Electron..." -ForegroundColor Cyan
Set-Location (Join-Path $root "kuro-ai")
npm run dev:all