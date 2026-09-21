// Preload for the hidden wake-word detector window
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('wakeIPC', {
    wakeDetected: () => ipcRenderer.send('wake-word-detected'),
    commandCaptured: (text) => ipcRenderer.send('command-captured', text),
    // Listen for mode-switch commands from main process
    onSwitchToCommand: (callback) => ipcRenderer.on('switch-to-command', callback),
    onSwitchToWake: (callback) => ipcRenderer.on('switch-to-wake', callback),
});
