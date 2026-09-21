/**
 * KURO - Preload Script
 * Safely exposes Electron IPC to the React frontend.
 * contextIsolation=true means we can't use require() in the renderer,
 * so we expose specific bridge functions here.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    setWindowSize: (size) => ipcRenderer.send('set-window-size', size),
    setInteractive: (val) => ipcRenderer.send('set-interactive', val),
});

contextBridge.exposeInMainWorld('kuro', {
    hide: () => ipcRenderer.send('hide-overlay'),
    show: () => ipcRenderer.send('show-overlay'),
    dock: () => ipcRenderer.send('dock-overlay'),
    undock: () => ipcRenderer.send('undock-overlay'),
    triggerListen: () => ipcRenderer.send('trigger-listen'),
    onOpenSettings: (callback) => ipcRenderer.on('open-settings', callback),
    onAutoListen: (callback) => {
        ipcRenderer.removeAllListeners('auto-listen');
        ipcRenderer.on('auto-listen', callback);
    },
    onCommandText: (callback) => {
        ipcRenderer.removeAllListeners('command-text');
        ipcRenderer.on('command-text', (_event, text) => callback(text));
    },
    onKuroEvent: (callback) => {
        ipcRenderer.removeAllListeners('kuro-event');
        ipcRenderer.on('kuro-event', (_event, data) => callback(data));
    },
    removeListener: (channel, callback) => ipcRenderer.removeListener(channel, callback),
});
