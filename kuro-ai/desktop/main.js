const { app, BrowserWindow, globalShortcut, Tray, Menu, screen, ipcMain, session } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const WebSocket = require('ws');

let overlayWindow;
let tray;
let backendProcess;
let wakeSSEAbort = null;  // AbortController for SSE connection
let pipelineWS   = null;  // WebSocket to Python /ws pipeline events
let pipelineRunning = false;
let activeHotkeyLabel = 'Alt+X';
let watchdogTimer = null;

const ISLAND_MAX_W = 320;
const ISLAND_MAX_H = 120;

// Prevent duplicate instances from competing for global shortcuts.
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
    app.quit();
}

// ===== Start the Node.js backend (skips if already running) =====
async function startBackend() {
    if (backendProcess && !backendProcess.killed) {
        return;
    }

    // Check if backend is already running (e.g. started by concurrently)
    try {
        const res = await fetch('http://localhost:3001/api/health');
        if (res.ok) {
            console.log('[Kuro] Backend already running on port 3001 — skipping auto-start.');
            return;
        }
    } catch { /* not running yet — start it */ }

    const backendPath = path.join(__dirname, '..', 'backend', 'server.js');
    backendProcess = spawn('node', [backendPath], {
        stdio: 'pipe',
        env: { ...process.env },
    });

    backendProcess.stdout.on('data', (data) => console.log(`[Backend] ${data}`));
    backendProcess.stderr.on('data', (data) => console.error(`[Backend Error] ${data}`));
    backendProcess.on('close', (code) => {
        console.log(`[Backend] Process exited: ${code}`);
        backendProcess = null;
        if (pipelineRunning) {
            setTimeout(() => {
                startBackend().catch((e) => console.log('[Kuro] Backend restart failed:', e.message));
            }, 1500);
        }
    });
}

function startWatchdog() {
    if (watchdogTimer) return;
    watchdogTimer = setInterval(async () => {
        if (!pipelineRunning) return;

        try {
            const res = await fetch('http://localhost:3001/api/health');
            if (!res.ok) throw new Error(`health ${res.status}`);
        } catch {
            startBackend().catch((e) => console.log('[Watchdog] Backend ensure failed:', e.message));
        }

        if (!pipelineWS || pipelineWS.readyState === WebSocket.CLOSED) {
            connectWebSocket();
        }

        if (!wakeSSEAbort) {
            connectWakeSSE();
        }
    }, 5000);
}

function stopWatchdog() {
    if (!watchdogTimer) return;
    clearInterval(watchdogTimer);
    watchdogTimer = null;
}

// ===== Position: top-center of primary display =====
function getOverlayPosition(width, height) {
    const { workArea } = screen.getPrimaryDisplay();
    const x = Math.round(workArea.x + ((workArea.width - width) / 2));
    const y = workArea.y;
    return { x, y };
}

function getDockedPosition(width, height) {
    return getOverlayPosition(width, height);
}

let isDocked = false;

// ===== Create the Siri-style overlay window =====
function createOverlay() {
    const { x, y } = getOverlayPosition(ISLAND_MAX_W, ISLAND_MAX_H);

    overlayWindow = new BrowserWindow({
        width: ISLAND_MAX_W,
        height: ISLAND_MAX_H,
        x,
        y,
        frame: false,
        transparent: true,
        backgroundColor: '#00000000',
        resizable: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        hasShadow: false,
        icon: path.join(__dirname, '..', 'frontend', 'public', 'Kuro-icon.png'),
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js'),
        },
        show: false,
    });

    overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    overlayWindow.setAlwaysOnTop(true, 'screen-saver');
    overlayWindow.setIgnoreMouseEvents(true, { forward: true });

    // Auto-detect frontend dev server port
    const isDev = process.env.NODE_ENV !== 'production';
    if (isDev) {
        loadOverlayWithPortDetection();
    } else {
        overlayWindow.loadFile(path.join(__dirname, '..', 'frontend', 'dist', 'index.html'));
    }

    overlayWindow.webContents.setBackgroundThrottling(false);

    // Keep the island visible even when focus changes.
    overlayWindow.on('blur', () => {
        if (!isDocked) overlayWindow.setIgnoreMouseEvents(true, { forward: true });
    });

    overlayWindow.on('close', (event) => {
        event.preventDefault();
        hideOverlay();
    });
}

// Try ports 5173-5179 to find the running Vite dev server
async function loadOverlayWithPortDetection() {
    for (let port = 5173; port <= 5179; port++) {
        try {
            const res = await fetch(`http://localhost:${port}/`);
            if (res.ok) {
                overlayWindow.loadURL(`http://localhost:${port}`);
                console.log(`[Kuro] Overlay loaded from http://localhost:${port}`);
                return;
            }
        } catch { /* port not available */ }
    }
    console.log('[Kuro] No dev server found, defaulting to http://localhost:5173');
    overlayWindow.loadURL('http://localhost:5173');
}

// Flag to prevent blur-hide during voice interaction
let preventBlurHide = false;
let commandTimeout = null;

// ===== Connect to new KuroPipeline WebSocket (Upgrade 1) =====
function connectWebSocket(retryDelay = 3000) {
    if (!pipelineRunning) return;
    const host = process.env.KURO_HOST || '127.0.0.1';
    const port = process.env.KURO_PORT || '8000';
    const url  = `ws://${host}:${port}/ws`;

    console.log(`[Kuro] Connecting to pipeline WebSocket: ${url}`);

    pipelineWS = new WebSocket(url);

    pipelineWS.on('open', () => {
        console.log('[Kuro] Pipeline WebSocket connected ✅');
        retryDelay = 3000;
    });

    pipelineWS.on('message', (raw) => {
        try {
            const data = JSON.parse(raw.toString());
            // Forward all pipeline events to the renderer process
            if (overlayWindow && !overlayWindow.isDestroyed()) {
                overlayWindow.webContents.send('kuro-event', data);
            }
            // Handle state transitions in main process
            if (data.type === 'status') {
                if (data.state === 'listening' || data.state === 'thinking' || data.state === 'acting') {
                    if (!overlayWindow?.isVisible()) showOverlay();
                }
            }
        } catch (e) {
            console.error('[Kuro WS] Parse error:', e.message);
        }
    });

    pipelineWS.on('error', (err) => {
        console.log('[Kuro WS] Error:', err.message || 'connection error');
    });

    pipelineWS.on('close', () => {
        if (!pipelineRunning) {
            console.log('[Kuro WS] Disconnected (Kuro inactive).');
            pipelineWS = null;
            return;
        }
        console.log(`[Kuro WS] Disconnected — reconnecting in ${retryDelay / 1000}s`);
        pipelineWS = null;
        setTimeout(() => connectWebSocket(Math.min(retryDelay * 1.5, 30000)), retryDelay);
    });
}

// ===== Connect to Python wake word SSE via Node backend =====
function connectWakeSSE(retryCount = 0) {
    if (!pipelineRunning) return;
    const MAX_RETRIES = 999;  // Basically always retry

    if (wakeSSEAbort) wakeSSEAbort.abort();
    wakeSSEAbort = new AbortController();

    console.log('[Kuro] Connecting to wake word SSE...');

    fetch('http://localhost:3001/wake/events', { signal: wakeSSEAbort.signal })
        .then(async (response) => {
            if (!response.ok) throw new Error(`SSE HTTP ${response.status}`);
            console.log('[Kuro] Wake word SSE connected!');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true });
                // Parse SSE lines — look for both wake events and JSON payloads
                const lines = chunk.split('\n');
                for (const line of lines) {
                    const trimmed = line.trim();
                    if (trimmed === 'data: wake') {
                        console.log('[Kuro] 🎤 Wake word detected by Python! Showing overlay...');
                        showOverlayAndListen();
                    } else if (trimmed.startsWith('data: {')) {
                        // JSON payload (alert_mode etc.) — handled by frontend EventSource
                        // Just log it here for debugging
                        try {
                            const payload = JSON.parse(trimmed.slice(6));
                            if (payload.type === 'alert_mode') {
                                console.log(`[Kuro] Alert mode: ${payload.active ? 'ON' : 'OFF'}`);
                            }
                        } catch { /* not valid JSON */ }
                    }
                }
            }
            // Stream ended — reconnect
            if (!pipelineRunning) return;
            console.log('[Kuro] SSE stream ended, reconnecting...');
            setTimeout(() => connectWakeSSE(0), 2000);
        })
        .catch((err) => {
            if (err.name === 'AbortError') return;
            if (!pipelineRunning) return;
            const delay = Math.min(5000, 1000 * (retryCount + 1));
            console.log(`[Kuro] SSE connection failed (retry in ${delay / 1000}s):`, err.message);
            setTimeout(() => connectWakeSSE(retryCount + 1), delay);
        });
}

function toggleKuro() {
    if (pipelineRunning) {
        shutdownKuro();
    } else {
        startupKuro();
    }
}

function startupKuro() {
    console.log('[Kuro] Starting up via hotkey...');
    pipelineRunning = true;
    startBackend().catch((e) => console.log('[Kuro] Backend start failed:', e.message));
    startWatchdog();

    if (tray) {
        tray.setToolTip(`Kuro — Active (${activeHotkeyLabel} to stop)`);
        tray.setContextMenu(buildTrayMenu());
    }

    if (!pipelineWS) {
        connectWebSocket();
    }
    if (!wakeSSEAbort) {
        connectWakeSSE();
    }

    // Show overlay + activate mic after a brief connection delay
    setTimeout(() => {
        showOverlayAndListen();
    }, 1500);

    console.log('[Kuro] Active — mic on, listening...');
}

function shutdownKuro() {
    console.log('[Kuro] Shutting down via hotkey...');
    pipelineRunning = false;
    stopWatchdog();

    if (tray) {
        tray.setToolTip(`Kuro — Inactive (${activeHotkeyLabel} to start)`);
        tray.setContextMenu(buildTrayMenu());
    }

    if (overlayWindow && !overlayWindow.isDestroyed()) {
        overlayWindow.webContents.send('kuro-event', { type: 'status', state: 'shutdown' });
    }

    hideOverlay();

    if (pipelineWS) {
        try {
            pipelineWS.removeAllListeners();
            pipelineWS.close();
        } catch {}
        pipelineWS = null;
    }

    if (wakeSSEAbort) {
        try { wakeSSEAbort.abort(); } catch {}
        wakeSSEAbort = null;
    }

    console.log(`[Kuro] Shut down cleanly. ${activeHotkeyLabel} to restart.`);
}

// ===== Show overlay WITHOUT listening (for shortcut/tray) =====
function showOverlay() {
    if (!overlayWindow) return;
    const { width, height } = overlayWindow.getBounds();
    const { x, y } = getOverlayPosition(width, height);
    overlayWindow.setPosition(x, y);
    overlayWindow.show();
    if (tray) tray.setContextMenu(buildTrayMenu());
}

// ===== Hide overlay =====
function hideOverlay() {
    if (!overlayWindow) return;
    preventBlurHide = false;
    if (commandTimeout) { clearTimeout(commandTimeout); commandTimeout = null; }
    overlayWindow.hide();
    isDocked = false;
    if (tray) tray.setContextMenu(buildTrayMenu());
}

// ===== Dock overlay to right side (kept for IPC compat, but now just hides) =====
function dockOverlay() {
    if (!overlayWindow) return;
    isDocked = false;
    hideOverlay();
    console.log('[Kuro] Response done — hiding overlay');
}

// ===== Undock overlay: bring back to center with full opacity =====
function undockOverlay() {
    if (!overlayWindow) return;
    isDocked = false;
    overlayWindow.setAlwaysOnTop(true, 'floating');
    overlayWindow.setIgnoreMouseEvents(false, { forward: true });
    const { width, height } = overlayWindow.getBounds();
    const { x, y } = getOverlayPosition(width, height);
    overlayWindow.setPosition(x, y);
    console.log('[Kuro] Overlay active (full opacity)');
}

// ===== Show overlay and trigger voice pipeline =====
let isCapturing = false;  // Prevent duplicate STT triggers

function showOverlayAndListen(viaManual = false) {
    if (!overlayWindow) return;
    if (!pipelineRunning) {
        console.log('[Kuro] Ignoring listen request while inactive.');
        return;
    }

    // Undock if currently docked
    if (isDocked) {
        undockOverlay();
    } else {
        const { width, height } = overlayWindow.getBounds();
        const { x, y } = getOverlayPosition(width, height);
        overlayWindow.setPosition(x, y);
        overlayWindow.setIgnoreMouseEvents(false, { forward: true });
    }

    preventBlurHide = true;
    setTimeout(() => { preventBlurHide = false; }, 120000); // 2 min safety

    overlayWindow.show();
    overlayWindow.focus();
    overlayWindow.setAlwaysOnTop(true, 'screen-saver');

    if (viaManual) {
        console.log(`[Kuro] Manual trigger... sending /wake/trigger to backend`);
        fetch('http://localhost:3001/wake/trigger', { method: 'POST' })
            .catch(err => console.log('[Kuro] Trigger failed:', err.message));
    }
}

// ESC key = dismiss
function registerEscKey() {
    overlayWindow?.webContents.on('before-input-event', (event, input) => {
        if (input.key === 'Escape') {
            hideOverlay();
        }
    });
}

// ===== System tray =====
function buildTrayMenu() {
    return Menu.buildFromTemplate([
        { label: '🌑 Kuro — The Shadow That Thinks', enabled: false },
        { type: 'separator' },
        { label: overlayWindow?.isVisible() ? `Hide (${activeHotkeyLabel})` : `Show (${activeHotkeyLabel})`, click: toggleKuro },
        { type: 'separator' },
        { label: '⏹ Stop Kuro (background off)', click: stopKuroBackground },
        { label: '▶ Start Kuro (background on)', click: startKuroBackground },
        { type: 'separator' },
        {
            label: '📋 View Logs',
            click: () => {
                const { shell } = require('electron');
                const logsPath = path.join(__dirname, '..', '..', 'data', 'logs');
                shell.openPath(logsPath);
            }
        },
        { type: 'separator' },
        {
            label: '✕ Quit Kuro completely',
            click: () => {
                stopKuroBackground();
                setTimeout(() => app.exit(0), 1000);
            }
        }
    ]);
}

// Stop background services (Python + Node) but keep Electron alive in tray
function stopKuroBackground() {
    console.log('[Kuro] Stopping background services...');

    // Disconnect WebSocket and SSE
    if (pipelineWS) { try { pipelineWS.removeAllListeners(); pipelineWS.close(); } catch {} pipelineWS = null; }
    if (wakeSSEAbort) { try { wakeSSEAbort.abort(); } catch {} wakeSSEAbort = null; }

    // Hide overlay if visible
    hideOverlay();

    // Kill Python and Node via PowerShell (hidden)
    const { spawn } = require('child_process');
    spawn('powershell.exe', [
        '-NoProfile', '-WindowStyle', 'Hidden', '-Command',
        `
        Get-Process python,python3 -ErrorAction SilentlyContinue | ForEach-Object {
            $cmd = (Get-WmiObject Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
            if ($cmd -like '*main.py*') { Stop-Process -Id $_.Id -Force }
        }
        foreach ($port in @(8000, 3001, 5173)) {
            netstat -ano 2>$null | Select-String ":$port " | ForEach-Object {
                $targetPid = ($_ -split '\\s+')[-1]
                if ($targetPid -match '^\\d+$' -and $targetPid -ne '0') { Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue }
            }
        }
        `
    ], { windowsHide: true });

    // Update tray tooltip
    if (tray) {
        tray.setToolTip('Kuro — Stopped (right-click to start)');
        tray.setContextMenu(buildTrayMenu());
    }

    console.log('[Kuro] Background services stopped.');
}

// Start background services again
function startKuroBackground() {
    console.log('[Kuro] Starting background services...');

    const scriptPath = path.join(__dirname, '..', '..', 'start.ps1');
    const { spawn } = require('child_process');

    spawn('powershell.exe', [
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-WindowStyle', 'Hidden',
        '-File', scriptPath,
        '-BackgroundOnly'
    ], { windowsHide: true, detached: true }).unref();

    // Reconnect after services boot (~8s)
    setTimeout(() => {
        if (!pipelineWS) connectWebSocket();
        if (!wakeSSEAbort) connectWakeSSE();
        if (tray) {
            tray.setToolTip(`Kuro — Active (${activeHotkeyLabel} to show)`);
            tray.setContextMenu(buildTrayMenu());
        }
    }, 8000);
}

function createTray() {
    tray = new Tray(path.join(__dirname, '..', 'frontend', 'public', 'Kuro-icon.png'));
    tray.setToolTip(`Kuro — Inactive (${activeHotkeyLabel} to start)`);
    tray.setContextMenu(buildTrayMenu());
    tray.on('click', toggleKuro);
}

function registerToggleHotkey() {
    const preferred = { accel: 'Alt+X', label: 'Alt+X' };
    const backup = { accel: 'CommandOrControl+Shift+Space', label: 'Ctrl+Shift+Space' };
    const optional = [
        { accel: 'AltRight', label: 'Right Alt' },
        { accel: 'F8', label: 'F8 (or Fn+F8)' },
    ];

    let hasAny = false;
    let labels = [];

    const tryRegister = (c) => {
        try {
            const ok = globalShortcut.register(c.accel, toggleKuro);
            if (ok) {
                hasAny = true;
                labels.push(c.label);
                console.log(`[Kuro] Hotkey registered: ${c.label} (${c.accel})`);
            }
            return ok;
        } catch (e) {
            console.log(`[Kuro] Hotkey '${c.accel}' not supported: ${e.message}`);
            return false;
        }
    };

    // Always try both primary and backup so users have a guaranteed fallback.
    const primaryOk = tryRegister(preferred);
    tryRegister(backup);
    for (const c of optional) tryRegister(c);

    if (primaryOk) {
        activeHotkeyLabel = preferred.label;
    } else if (labels.length > 0) {
        activeHotkeyLabel = labels[0];
    }

    if (!hasAny) {
        console.log('[Kuro] No global hotkey registered. Use tray menu toggle.');
    } else {
        console.log(`[Kuro] Active hotkeys: ${labels.join(', ')}`);
    }

    return hasAny;
}

// ===== IPC handlers =====
ipcMain.on('hide-overlay', () => hideOverlay());
ipcMain.on('show-overlay', () => showOverlayAndListen(true));
ipcMain.on('trigger-listen', () => showOverlayAndListen(true));
ipcMain.on('dock-overlay', () => dockOverlay());
ipcMain.on('undock-overlay', () => undockOverlay());
ipcMain.on('set-interactive', (_event, interactive) => {
    if (!overlayWindow) return;
    overlayWindow.setIgnoreMouseEvents(!interactive, { forward: true });
});
ipcMain.on('set-window-size', (_event, size = {}) => {
    if (!overlayWindow) return;
    const current = overlayWindow.getBounds();
    const width = Number.isFinite(size.width) ? Math.max(88, Math.min(size.width, ISLAND_MAX_W)) : current.width;
    const height = Number.isFinite(size.height) ? Math.max(30, Math.min(size.height, ISLAND_MAX_H)) : current.height;
    const { x, y } = getOverlayPosition(width, height);
    overlayWindow.setBounds({ x, y, width, height }, true);
});

// Auto-hide after Kuro finishes a response/action
ipcMain.on('response-done', () => {
    console.log('[Kuro] Response done — returning to idle island');
    if (!overlayWindow) return;
    const { x, y } = getOverlayPosition(ISLAND_MAX_W, ISLAND_MAX_H);
    overlayWindow.setBounds({ x, y, width: ISLAND_MAX_W, height: ISLAND_MAX_H }, true);
    overlayWindow.setIgnoreMouseEvents(true, { forward: true });
    overlayWindow.show();
});

// ===== App Lifecycle =====
const cachePath = path.join(__dirname, '..', 'frontend', 'electron-cache');
app.setPath('userData', cachePath);

app.whenReady().then(() => {
    startBackend();
    startWatchdog();

    try {
        app.setLoginItemSettings({ openAtLogin: true });
        console.log('[Kuro] Enabled auto-start at login.');
    } catch (e) {
        console.log('[Kuro] Could not enable auto-start at login:', e.message);
    }

    // Grant microphone permission automatically
    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
        callback(permission === 'media' || permission === 'microphone');
    });
    session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
        return permission === 'media' || permission === 'microphone';
    });
    console.log('[Kuro] Microphone permissions auto-granted.');

    setTimeout(() => {
        createOverlay();
        hideOverlay();
        registerToggleHotkey();
        createTray();

        // ── AUTO-START: Connect to Python brain immediately on startup ──
        // Don't wait for tray click. Python is already listening for wake words.
        // We need SSE + WS open NOW so the overlay fires the instant wake word fires.
        pipelineRunning = true;
        startBackend().catch((e) => console.log('[Kuro] Backend start failed:', e.message));
        connectWebSocket();
        connectWakeSSE();
        if (tray) {
            tray.setToolTip(`Kuro — Active (${activeHotkeyLabel} to show/hide)`);
            tray.setContextMenu(buildTrayMenu());
        }

        console.log('[Kuro] Wake word listener ACTIVE — overlay will appear automatically.');
        registerEscKey();

        // Show it on screen so the user knows it successfully booted!
        setTimeout(() => {
            showOverlayAndListen();
        }, 1500);

        console.log(`[Kuro] Hotkey: ${activeHotkeyLabel} to toggle Kuro on/off`);
    }, 1500);
});

app.on('window-all-closed', () => {
    // Keep alive in tray
});

app.on('before-quit', () => {
    stopWatchdog();
    if (wakeSSEAbort) wakeSSEAbort.abort();
    if (pipelineWS)   pipelineWS.close();
    if (backendProcess) backendProcess.kill();
});

app.on('will-quit', () => {
    globalShortcut.unregisterAll();
});
