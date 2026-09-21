#!/usr/bin/env pwsh

param(
    [switch]$BackgroundOnly,
    [switch]$InstallStartup,
    [switch]$NoLoop,
    [int]$RestartDelaySeconds = 2
)

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force 2>$null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$root = $PSScriptRoot
$logsDir = Join-Path $root "data\logs"
$dotenv     = Join-Path $root ".env"

if (Test-Path $dotenv) {
    Get-Content $dotenv | Where-Object { $_ -match '^([^#=]+)=(.*)$' } | ForEach-Object {
        [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
    }
}
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

function Write-Status {
    param([string]$Message, [string]$Color = "Cyan")
    Write-Host $Message -ForegroundColor $Color
}

function Install-StartupShortcut {
    $startupLink = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Kuro.lnk"
    $targetPath = "powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\start.ps1`""

    if (Test-Path $startupLink) {
        Remove-Item -Force $startupLink -ErrorAction SilentlyContinue
    }

    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($startupLink)
    $shortcut.TargetPath = $targetPath
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $root
    $shortcut.Save()

    Write-Status "Startup shortcut installed: $startupLink" "Green"
}

function Stop-KuroProcesses {
    param([switch]$IncludeElectron)

    Get-Process -Name python, python3 -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
            if ($cmdLine -like "*main.py*") {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }

    foreach ($port in @(8000, 3001, 5173)) {
        $pids = netstat -ano 2>$null | Select-String ":$port " | ForEach-Object { ($_ -split '\s+')[-1] }
        foreach ($processId in $pids) {
            if ($processId -match '^\d+$' -and $processId -ne '0') {
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }
        }
    }

    if ($IncludeElectron) {
        Get-Process -Name electron -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

function Initialize-Ollama {
    try {
        Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        return
    } catch {}

    $env:OLLAMA_FLASH_ATTENTION = "1"
    if (-not $env:OLLAMA_KV_CACHE_TYPE) { $env:OLLAMA_KV_CACHE_TYPE = "q8_0" }
    Start-Process -FilePath "ollama" `
        -ArgumentList "serve" `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "ollama.log") `
        -RedirectStandardError (Join-Path $logsDir "ollama-err.log")

    Start-Sleep -Seconds 3
}

function Initialize-OllamaModel {
    param(
        [string]$ModelName = "gemma4:e4b"
    )

    try {
        $tags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop
        $installed = @($tags.models | ForEach-Object { $_.name })
        if ($installed -contains $ModelName) {
            Write-Status "Ollama model ready: $ModelName" "Green"
            return
        }

        Write-Status "Ollama model missing: $ModelName. Pulling now..." "Yellow"
        & ollama pull $ModelName
        if ($LASTEXITCODE -ne 0) {
            throw "ollama pull exited with code $LASTEXITCODE"
        }

        $verify = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5 -ErrorAction Stop
        $verifyInstalled = @($verify.models | ForEach-Object { $_.name })
        if (-not ($verifyInstalled -contains $ModelName)) {
            throw "model still not present after pull"
        }

        Write-Status "Ollama model pulled successfully: $ModelName" "Green"
    } catch {
        throw "Failed to ensure local model '$ModelName'. $_"
    }
}

function Wait-ForUrl {
    param(
        [string]$Url,
        [int]$MaxWaitSeconds = 30
    )

    $elapsed = 0
    while ($elapsed -lt $MaxWaitSeconds) {
        try {
            Invoke-WebRequest -Uri $Url -TimeoutSec 1 -ErrorAction Stop | Out-Null
            return $true
        } catch {}

        Start-Sleep -Seconds 1
        $elapsed += 1
    }

    return $false
}

function Wait-ForBrain {
    param([int]$MaxWaitSeconds = 60)

    $elapsed = 0
    while ($elapsed -lt $MaxWaitSeconds) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction Stop
            if ($resp.ready -eq $true) {
                return $true
            }
        } catch {}

        Start-Sleep -Seconds 2
        $elapsed += 2
    }

    return $false
}

function Start-KuroServices {
    param([switch]$StartElectron)

    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

    # ── Dynamic GPU detection ─────────────────────────────────────────────
    # Check if NVIDIA GPU is available and CUDA responds.
    # If GPU is off (e.g., battery/power-saving mode), set Ollama to CPU.
    $gpuAvailable = $false
    try {
        $gpuInfo = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
        if ($LASTEXITCODE -eq 0 -and $gpuInfo) {
            $gpuAvailable = $true
            Write-Status "GPU detected: $($gpuInfo.Trim())" "Green"
        }
    } catch {}

    if ($gpuAvailable) {
        # GPU is on: let Ollama use GPU layers, enable Flash Attention
        $env:OLLAMA_NUM_GPU    = "-1"   # auto: use all available layers
        $env:OLLAMA_FLASH_ATTENTION = "1"
        Write-Status "GPU mode: Ollama will use GPU layers" "Green"
    } else {
        # GPU is off or not found: force CPU mode
        $env:OLLAMA_NUM_GPU    = "0"
        $env:OLLAMA_FLASH_ATTENTION = "0"
        Write-Status "CPU mode: GPU not detected, Ollama running on CPU" "Yellow"
    }
    # ─────────────────────────────────────────────────────────────────────

    if (-not $env:OLLAMA_KV_CACHE_TYPE) { $env:OLLAMA_KV_CACHE_TYPE = "q8_0" }
    $env:SECURITY_LEVEL = if ($env:SECURITY_LEVEL) { $env:SECURITY_LEVEL } else { "high" }
    $env:PRIVACY_GUARD_ENABLED = if ($env:PRIVACY_GUARD_ENABLED) { $env:PRIVACY_GUARD_ENABLED } else { "true" }

    Initialize-Ollama
    $localModel = if ($env:LOCAL_MODEL) { $env:LOCAL_MODEL } else { "gemma2:2b" }
    Initialize-OllamaModel -ModelName $localModel

    Start-Process -FilePath $venvPython `
        -ArgumentList "main.py" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "brain.log") `
        -RedirectStandardError (Join-Path $logsDir "brain-err.log") | Out-Null

    [void](Wait-ForBrain -MaxWaitSeconds 60)

    $serverJs = Join-Path $root "kuro-ai\backend\server.js"
    Start-Process -FilePath "node" `
        -ArgumentList $serverJs `
        -WorkingDirectory (Join-Path $root "kuro-ai\backend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "backend.log") `
        -RedirectStandardError (Join-Path $logsDir "backend-err.log") | Out-Null

    $frontendDir = Join-Path $root "kuro-ai\frontend"
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npm run dev" `
        -WorkingDirectory $frontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "vite.log") `
        -RedirectStandardError (Join-Path $logsDir "vite-err.log") | Out-Null

    [void](Wait-ForUrl -Url "http://localhost:5173/" -MaxWaitSeconds 20)

    if (-not $StartElectron) {
        return $null
    }

    $electronDir = Join-Path $root "kuro-ai\desktop"
    return Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npx electron ." `
        -WorkingDirectory $electronDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "electron.log") `
        -RedirectStandardError (Join-Path $logsDir "electron-err.log") `
        -PassThru
}

if ($InstallStartup) {
    Install-StartupShortcut
    exit 0
}

if ($BackgroundOnly) {
    Write-Status "Starting background services only..."
    Stop-KuroProcesses
    Start-KuroServices | Out-Null
    Write-Status "Background services started." "Green"
    exit 0
}

Write-Status "Starting Kuro with auto-restart..."

do {
    Stop-KuroProcesses -IncludeElectron
    $electronProcess = Start-KuroServices -StartElectron

    if (-not $electronProcess) {
        Write-Status "Electron failed to start. Exiting launcher." "Red"
        exit 1
    }

    Write-Status "Kuro is running. Waiting for Electron to close..." "Green"
    try { Wait-Process -Id $electronProcess.Id } catch {}

    if ($NoLoop) {
        break
    }

    Write-Status "Electron closed. Restarting in $RestartDelaySeconds second(s)..." "Yellow"
    Start-Sleep -Seconds $RestartDelaySeconds
} while ($true)
