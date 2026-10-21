# Kuro AI Assistant 🌙

Your personal AI assistant that runs entirely on your local machine.

## Prerequisites

1. **Node.js** (v18 or higher) — [Download](https://nodejs.org/)
2. **Ollama** — [Download](https://ollama.com/download)

## Quick Start

### 1. Install Ollama & Pull a Model

```bash
# After installing Ollama, pull a model:
ollama pull llama3
```

### 2. Install Dependencies

```bash
npm run install:all
```

### 3. Run in Dev Mode (Browser)

```bash
npm run dev
```

This starts both the backend (port 3001) and frontend (port 5173).
Open [http://localhost:5173](http://localhost:5173) in your browser.

### 4. Run as Desktop App

```bash
npm run dev:desktop
```

**Global Shortcut:** Press `Ctrl+Shift+K` to toggle Kuro on/off!

## Project Structure

```
kuro-ai/
├── frontend/    # React + Vite UI
├── backend/     # Node.js + Express server (Ollama proxy)
├── desktop/     # Electron wrapper for native app
└── package.json # Root orchestration scripts
```

## Features

- 💬 Chat with AI locally (no API keys needed)
- 🎙️ Voice input via Web Speech API
- 🔊 Voice output (text-to-speech)
- 📁 System access (read files, run commands)
- 🎨 Beautiful dark theme UI
- ⌨️ Global hotkey (Ctrl+Shift+K)
- 🖥️ System tray integration
