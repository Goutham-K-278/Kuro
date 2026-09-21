#!/usr/bin/env pwsh
# ============================================================
#  KURO -- Perfect One-Click Launcher  (v3.0)
#  Double-click KURO.bat  OR  run:  powershell -File launch.ps1
# ============================================================
#  Flags:
#    -NoOllama       Skip starting Ollama (already running)
#    -NoBrowser      Don't open browser after start
#    -StopOnly       Kill all Kuro processes and exit
#    -Status         Show what is running right now
#    -InstallStartup Add Kuro to Windows auto-start
# ============================================================

param(
    [switch]$NoOllama,
    [switch]$NoBrowser,
    [switch]$InstallStartup,
    [switch]$StopOnly,
    [switch]$Status
)

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force 2>$null
$OutputEncoding = [System.Text.Encoding]::UTF8

$root       = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$logsDir    = Join-Path $root "logs"
$dataLogs   = Join-Path $root "data\logs"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$dotenv     = Join-Path $root ".env"

if (Test-Path $dotenv) {
    Get-Content $dotenv | Where-Object { $_ -match '^([^#=]+)=(.*)$' } | ForEach-Object {
        [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
    }
}

# ── Pretty output helpers ────────────────────────────────────
function Log-Step  { param($m) Write-Host "  >> $m" -ForegroundColor Cyan    }
function Log-OK    { param($m) Write-Host "  [OK] $m" -ForegroundColor Green  }
function Log-Warn  { param($m) Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Log-Fail  { param($m) Write-Host "  [X]  $m" -ForegroundColor Red    }
function Log-Info  { param($m) Write-Host "       $m" -ForegroundColor DarkGray }

function Show-Banner {
    Write-Host ""
    Write-Host "  =====================================" -ForegroundColor Magenta
    Write-Host "       K U R O   A I   A S S I S T A N T" -ForegroundColor Cyan
    Write-Host "  =====================================" -ForegroundColor Magenta
    Write-Host ""
}

# ── GPU detection ────────────────────────────────────────────
function Detect-GPU {
    $r = @{ Available = $false; Name = "None"; VRAM = "" }
    try {
        # Use Kuro's built-in robust GPU detection rather than raw nvidia-smi (which may not be in PATH)
        $pyCode = "from core.gpu_detect import GPU_AVAILABLE, GPU_NAME, GPU_VRAM_TOTAL_MB; print(f'{GPU_AVAILABLE}|{GPU_NAME}|{GPU_VRAM_TOTAL_MB}')"
        $raw = & $venvPython -c $pyCode 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $parts = $raw.Trim() -split "\|"
            if ($parts[0] -eq "True") {
                $r.Available = $true
                $r.Name      = $parts[1]
                $r.VRAM      = "$($parts[2]) MB"
            }
        }
    } catch {}
    return $r
}


# ── Pre-flight checks ────────────────────────────────────────
function Assert-Prereqs {
    $ok = $true

    if (-not (Test-Path $venvPython)) {
        Log-Fail "Python venv missing: $venvPython"
        Log-Info "Fix: python -m venv .venv"
        Log-Info "     .venv\Scripts\pip install -r requirements.txt"
        $ok = $false
    } else {
        Log-OK "Python venv found"
    }

    if (-not (Test-Path $dotenv)) {
        Log-Fail ".env file missing"
        Log-Info "Fix: copy .env.example to .env and fill in your API keys"
        $ok = $false
    } else {
        Log-OK ".env found"
    }

    if (-not $NoOllama) {
        if (Get-Command ollama -ErrorAction SilentlyContinue) {
            Log-OK "ollama found"
        } else {
            Log-Warn "ollama not in PATH -- Kuro will use Groq cloud LLM"
        }
    }
    return $ok
}

# ── Kill all Kuro processes ───────────────────────────────────
function Stop-AllKuro {
    Log-Step "Stopping Kuro processes..."

    # Kill brain (main.py)
    Get-Process -Name python, python3 -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
            if ($cmdline -like "*main.py*") {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
                Log-Info "Stopped brain PID $($_.Id)"
            }
        } catch {}
    }

    # Free ports
    foreach ($port in @(8000, 3001, 5173)) {
        $portPids = netstat -ano 2>$null | Select-String ":$port " | ForEach-Object { ($_ -split '\s+')[-1] }
        foreach ($portPid in $portPids) {
            if ($portPid -match '^\d+$' -and $portPid -ne '0') {
                Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # Kill electron and node
    Get-Process -Name electron, node -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    Log-OK "Cleanup done"
}

# ── Ollama ────────────────────────────────────────────────────
function Start-Ollama {
    param([bool]$GpuAvailable)

    # Already running?
    try {
        Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        Log-OK "Ollama already running"
        return
    } catch {}

    Log-Step "Starting Ollama..."

    if ($GpuAvailable) {
        $env:OLLAMA_NUM_GPU        = "-1"   # all layers on GPU
        $env:OLLAMA_FLASH_ATTENTION = "1"
        Log-Info "Ollama: GPU mode"
    } else {
        $env:OLLAMA_NUM_GPU        = "0"    # CPU only
        $env:OLLAMA_FLASH_ATTENTION = "0"
        Log-Info "Ollama: CPU mode (no GPU detected)"
    }
    if (-not $env:OLLAMA_KV_CACHE_TYPE) { $env:OLLAMA_KV_CACHE_TYPE = "q8_0" }

    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    Start-Process -FilePath "ollama" `
        -ArgumentList "serve" `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "ollama.log") `
        -RedirectStandardError  (Join-Path $logsDir "ollama-err.log")

    # Poll for readiness (10 s max)
    for ($i = 1; $i -le 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 1 -ErrorAction Stop | Out-Null
            Log-OK "Ollama ready in ${i}s"
            return
        } catch {}
    }
    Log-Warn "Ollama not responding -- Kuro will use Groq cloud as fallback"
}

function Ensure-OllamaModel {
    param([string]$Model = "gemma4:e4b")
    try {
        $tags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop
        $names = @($tags.models | ForEach-Object { $_.name })
        if ($names -contains $Model) {
            Log-OK "Model ready: $Model"
            return
        }
        Log-Warn "Model '$Model' not cached -- pulling now (may take a few minutes)..."
        & ollama pull $Model
        if ($LASTEXITCODE -eq 0) {
            Log-OK "Model pulled: $Model"
        } else {
            Log-Warn "Model pull returned non-zero -- check ollama logs"
        }
    } catch {
        Log-Warn "Could not verify Ollama model -- continuing anyway"
    }
}

# ── Brain ─────────────────────────────────────────────────────
function Start-Brain {
    Log-Step "Starting Kuro brain (main.py)..."
    New-Item -ItemType Directory -Force -Path $logsDir  | Out-Null
    New-Item -ItemType Directory -Force -Path $dataLogs | Out-Null

    $proc = Start-Process -FilePath $venvPython `
        -ArgumentList "main.py" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "brain.log") `
        -RedirectStandardError  (Join-Path $logsDir "brain-err.log") `
        -PassThru

    Log-Info "Brain PID : $($proc.Id)"
    Log-Info "Brain log : $logsDir\brain.log"

    # Poll /health until ready or 90 s timeout
    Log-Step "Waiting for brain to be ready (up to 90 s)..."
    $deadline = (Get-Date).AddSeconds(90)
    $dots = 0
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $dots++
        try {
            $resp = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction Stop
            if ($resp.ready -eq $true) {
                $elapsed = [int](90 - ($deadline - (Get-Date)).TotalSeconds)
                Write-Host ""
                Log-OK "Brain ready in ${elapsed}s"
                return $proc
            }
        } catch {}
        Write-Host "." -NoNewline -ForegroundColor DarkGray
        if ($dots % 25 -eq 0) { Write-Host "" }
    }
    Write-Host ""
    Log-Warn "Brain health check timed out -- it may still be loading models"
    Log-Info "Check $logsDir\brain-err.log if something looks wrong"
    return $proc
}

# ── Start UI Components ──────────────────────────────────────
function Start-UI {
    Log-Step "Starting Node Backend..."
    $serverJs = Join-Path $root "kuro-ai\backend\server.js"
    Start-Process -FilePath "node" `
        -ArgumentList $serverJs `
        -WorkingDirectory (Join-Path $root "kuro-ai\backend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "backend.log") `
        -RedirectStandardError (Join-Path $logsDir "backend-err.log")

    Log-Step "Starting Vite Frontend..."
    $frontendDir = Join-Path $root "kuro-ai\frontend"
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npm run dev" `
        -WorkingDirectory $frontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "vite.log") `
        -RedirectStandardError (Join-Path $logsDir "vite-err.log")

    # Wait for Vite port 5173 to be open
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", 5173)
            $tcp.Close()
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    Log-Step "Starting Electron Desktop..."
    $electronDir = Join-Path $root "kuro-ai\desktop"
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npx electron ." `
        -WorkingDirectory $electronDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "electron.log") `
        -RedirectStandardError (Join-Path $logsDir "electron-err.log")
        
    Log-OK "UI started"
}

# ── Status display ────────────────────────────────────────────
function Show-Status {
    Write-Host ""
    Write-Host "  ======= KURO STATUS ========================" -ForegroundColor Cyan

    try {
        $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction Stop
        if ($h.ready) {
            Write-Host "  Brain  : [OK]  READY" -ForegroundColor Green
        } else {
            Write-Host "  Brain  : [!!] STARTING..." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Brain  : [X]  NOT RUNNING" -ForegroundColor Red
    }

    try {
        Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        Write-Host "  Ollama : [OK]  RUNNING" -ForegroundColor Green
    } catch {
        Write-Host "  Ollama : [X]  NOT RUNNING" -ForegroundColor Red
    }

    $g = Detect-GPU
    if ($g.Available) {
        Write-Host "  GPU    : [OK]  $($g.Name) ($($g.VRAM))" -ForegroundColor Green
    } else {
        Write-Host "  GPU    : [--] Not detected (CPU mode)" -ForegroundColor Yellow
    }

    Write-Host "  ============================================" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Install to Windows startup ────────────────────────────────
function Install-AutoStart {
    $link = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Kuro.lnk"
    $wsh  = New-Object -ComObject WScript.Shell
    $sc   = $wsh.CreateShortcut($link)
    $sc.TargetPath       = "powershell.exe"
    $sc.Arguments        = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\launch.ps1`" -NoBrowser"
    $sc.WorkingDirectory = $root
    $sc.Save()
    Log-OK "Auto-start shortcut created:"
    Log-Info $link
    Log-Info "Kuro will now launch silently on every Windows login."
}

# ======================================================
#  MAIN
# ======================================================

Show-Banner

if ($Status) {
    Show-Status
    exit 0
}

if ($StopOnly) {
    Stop-AllKuro
    exit 0
}

if ($InstallStartup) {
    Install-AutoStart
    exit 0
}

# Pre-flight
Write-Host "  -- PRE-FLIGHT CHECKS --" -ForegroundColor DarkCyan
if (-not (Assert-Prereqs)) {
    Write-Host ""
    Log-Fail "Pre-flight checks failed. Fix issues above and try again."
    Read-Host "  Press Enter to close"
    exit 1
}
Log-OK "Pre-flight passed"

# GPU
Write-Host ""
Write-Host "  -- GPU --" -ForegroundColor DarkCyan
$gpu = Detect-GPU
if ($gpu.Available) {
    Log-OK "GPU: $($gpu.Name)  VRAM: $($gpu.VRAM)"
} else {
    Log-Warn "No GPU found -- CPU-only mode (Whisper + Ollama on CPU)"
}

# Cleanup stale processes
Write-Host ""
Write-Host "  -- CLEANUP --" -ForegroundColor DarkCyan
Stop-AllKuro

# Ollama
if (-not $NoOllama) {
    Write-Host ""
    Write-Host "  -- OLLAMA --" -ForegroundColor DarkCyan
    if ($env:LLM_BACKEND -eq 'groq') {
        Log-OK "Skipping Ollama (LLM_BACKEND=groq)"
    } else {
        Start-Ollama -GpuAvailable $gpu.Available
        $localModel = if ($env:LOCAL_MODEL) { $env:LOCAL_MODEL } else { "gemma2:2b" }
        Ensure-OllamaModel -Model $localModel
    }
}

# Brain
Write-Host ""
Write-Host "  -- KURO BRAIN --" -ForegroundColor DarkCyan
$brainProc = Start-Brain

# Open UI
if (-not $NoBrowser) {
    Start-UI
}

# Summary
Show-Banner
Write-Host "  ============================================" -ForegroundColor DarkGray
Write-Host "  KURO IS RUNNING" -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor DarkGray
Show-Status

Write-Host "  UI   -> Electron App" -ForegroundColor DarkGray
Write-Host "  API  -> http://127.0.0.1:8000" -ForegroundColor DarkGray
Write-Host "  Logs -> $logsDir\" -ForegroundColor DarkGray
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# Stay alive / watch the brain
try {
    if ($brainProc -and -not $brainProc.HasExited) {
        Wait-Process -Id $brainProc.Id -ErrorAction SilentlyContinue
        Write-Host ""
        Log-Warn "Brain process exited unexpectedly."
        Log-Info "Check $logsDir\brain-err.log for the cause."
    } else {
        while ($true) { Start-Sleep -Seconds 10 }
    }
} catch {
    Write-Host ""
    Log-Step "Shutting down..."
    Stop-AllKuro
    Log-OK "Kuro stopped. Goodbye!"
}
