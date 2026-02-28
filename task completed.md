# KURO — Complete Project Documentation

> **"The Shadow That Thinks"** — A J.A.R.V.I.S.-inspired AI assistant with voice control, system automation, personality, and memory.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Three-Process System](#2-three-process-system)
3. [Complete Request Flow](#3-complete-request-flow)
4. [File-by-File Analysis](#4-file-by-file-analysis)
   - [Core Files](#41-core-files)
   - [Agent System](#42-agent-system)
   - [Voice Pipeline](#43-voice-pipeline)
   - [Personality System](#44-personality-system)
   - [Security & Memory (kuro/ module)](#45-security--memory-kuro-module)
   - [Frontend & Desktop](#46-frontend--desktop)
   - [RAG System](#47-rag-system)
   - [Other Modules](#48-other-modules)
   - [Data & Config Files](#49-data--config-files)
5. [Electron ↔ Node.js ↔ Python Connection Chain](#5-electron--nodejs--python-connection-chain)
6. [All API Endpoints](#6-all-api-endpoints)
7. [All Environment Variables](#7-all-environment-variables)
8. [Memory System Architecture](#8-memory-system-architecture)
9. [Security Model](#9-security-model)
10. [Tech Stack](#10-tech-stack)
11. [Design Patterns & Principles](#11-design-patterns--principles)
12. [Bugs Fixed (Audit Log)](#12-bugs-fixed-audit-log)

---

## 1. High-Level Architecture

Kuro is a **multi-layer, J.A.R.V.I.S.-inspired AI assistant** that runs as **three cooperating processes** on Windows. It can:

- **Listen** for a custom wake word ("Hey Kuro") and transcribe speech in real-time
- **Think** using local LLMs (Ollama) or cloud LLMs (OpenRouter) with RAG-augmented context
- **Act** on the operating system — open apps, type text, control volume, send messages, browse the web, manage files, take screenshots, and 40+ other actions
- **Speak** responses using neural text-to-speech (edge-tts)
- **Remember** conversations, user facts, and preferences across sessions via ChromaDB long-term memory
- **Adapt** personality based on time of day, user emotion, and manual mode switching

```
┌──────────────────────────────────────────────────────────────────────────┐
│   USER                                                                   │
│   "Hey Kuro, generate me a python code inside notepad"                   │
└──────────┬───────────────────────────────────────────────────────────────┘
           │ (audio)
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PORCUPINE WAKE WORD → FASTER-WHISPER STT → INTENT PARSER (regex/LLM)  │
│                                                                          │
│         "generate me a python code inside notepad"                       │
│         → {action:"compound", steps: [open notepad, type code]}         │
└──────────┬───────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  ACTION ROUTER → PERMISSION GATE → EXECUTORS                            │
│                                                                          │
│  Step 1: AppLauncher.open("notepad")  →  wait 2 seconds                │
│  Step 2: SystemAgent.smart_type("python code")                           │
│          → LLM generates code → streams via clipboard paste into Notepad│
└──────────┬───────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  EDGE-TTS → "Generated and typed 245 characters"                        │
│  → Auto-dock overlay → Resume Porcupine wake word listening             │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Three-Process System

Kuro runs as **three processes** that communicate via HTTP:

```
┌──────────────────────────────────────────────────────────────────────┐
│                           ELECTRON APP                              │
│  desktop/main.js  ←→  preload.js  ←→  frontend/src/App.jsx (React) │
│  - Always-on-top overlay (480×620, transparent, frameless)          │
│  - System tray icon, Ctrl+Shift+K hotkey                           │
│  - SSE listener for wake word events                                │
│  - IPC bridge: show/hide/dock/undock/triggerListen/commandText      │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTP (port 3001)
┌───────────────────────────▼──────────────────────────────────────────┐
│                        NODE.JS BACKEND                               │
│  backend/server.js  (Express, port 3001)                            │
│  - Proxy layer between frontend & Python brain                      │
│  - SSE proxy for wake word events                                   │
│  - Fallback: direct Ollama calls if Python brain is offline         │
│  - TTS audio streaming proxy                                        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTP (port 8000)
┌───────────────────────────▼──────────────────────────────────────────┐
│                       PYTHON BRAIN API                               │
│  main.py → FastAPI (uvicorn, port 8000)                             │
│  - Brain orchestrator (intent → route → personality → RAG → LLM)    │
│  - Action system (intent parser → permission gate → executors)      │
│  - Voice pipeline (Porcupine wake → STT → TTS)                     │
│  - Memory (short-term + episodic JSON + ChromaDB LTM)              │
│  - Ollama LOCAL + OpenRouter CLOUD dual-brain LLM                   │
└──────────────────────────────────────────────────────────────────────┘
```

| Process | Port | Technology | Role |
|---------|------|------------|------|
| **Python Brain** | 8000 | FastAPI + Uvicorn | Core intelligence: LLM, voice, actions, memory |
| **Node.js Backend** | 3001 | Express.js | Proxy + fallback between frontend and Python |
| **Electron Desktop** | — | Electron + React (Vite) | Overlay UI, system tray, hotkey, IPC |

---

## 3. Complete Request Flow

### Voice Flow (Primary)

```
1. Porcupine (voice/wake_word.py) detects "Hey Kuro" → triggers on_wake_detected()
2. SSE event sent → Node backend (/wake/events) → Electron main.js
3. Electron: showOverlayAndListen() → fetches POST /wake/listen (via Node → Python)
4. Python /wake/listen:
   a. Captures the currently active window title (for later refocusing)
   b. Stops Porcupine (releases microphone)
   c. TTS greets ("Yeah?" / "I'm listening" / "What is it?")
   d. STT records with Silero VAD (auto-cutoff on silence) + transcribes (Distil-Whisper)
   e. Restarts Porcupine
   f. Returns {text, status}
5. Electron sends command-text IPC to React frontend
6. Frontend: setKuroState('thinking') → POST /api/chat (via Node backend)
7. Node /api/chat:
   a. Tries Python brain (POST http://127.0.0.1:8000/chat)
   b. Python /chat handler:
      - Fast path: _is_action_command() → IntentParser.parse() → ActionRouter.execute()
      - Normal path: Brain.think() → classify → route (local/cloud) →
        memory → personality → RAG context → LLM → response →
        LTM save + background fact extraction
   c. Fallback: Direct Ollama if Python is offline
8. Frontend receives response → displays text → speaks via edge-TTS
9. After speech: auto-triggers next voice capture (continuous mode) or docks overlay
```

### Text Flow (Fallback)

```
1. User types in text input → sendMessage()
2. POST /api/chat → Same routing as voice step 7-8 above
```

### Action vs Chat Decision

When a message arrives at `/chat`, the system decides in this order:

```
1. _is_action_command(text)  — 15 regex patterns check if text starts with
   action keywords (open, close, type, search, volume, etc.)
2. If YES → IntentParser.parse(text):
     a. Fast-path: 25+ compiled regex patterns (~0ms)
     b. Slow-path: LLM-based parse (Mistral via Ollama)
   → ActionRouter.execute(intent) → executor result
3. If NO → Brain.think(text):
     a. classify(text) → Intent enum
     b. route(intent, text) → RoutingDecision (local/cloud, mode)
     c. Build messages with personality + history + RAG + LTM context
     d. LLMEngine.chat(messages, use_cloud, mode)
     e. Save to memory + extract facts in background
```

---

## 4. File-by-File Analysis

### 4.1 Core Files

#### `main.py` — Entry Point & FastAPI Server (764 lines)

**Purpose:** The single entry point for Kuro. Supports 4 modes:
- `server` (default) — FastAPI brain API on port 8000 with full voice pipeline
- `terminal` — Rich-based CLI chat loop for development/debugging
- `voice` — Standalone voice mode (wake → STT → think → TTS, no UI)
- `kuro` — Alternative runtime with encryption + episodic memory

**Key functions:**
- `run_server()` — Starts FastAPI with TTS, STT, Porcupine, and action system preloaded
- `_is_action_command(text)` — Regex-based fast detection of system control commands
- `_execute_action(user_text)` — Parses intent and routes to ActionRouter
- `_init_action_system()` — Initializes IntentParser + PermissionGate + ActionRouter

**Startup sequence:**
1. TTS engine (edge-tts)
2. STT engine (faster-whisper on CUDA)
3. Porcupine wake word detector (custom "Hey Kuro" .ppn)
4. Action system (intent parser + permission gate + action router)

**Shutdown cleanup:**
- Stops Porcupine
- Stops TTS
- Cleans up Playwright browser processes

**Window tracking:** Before every voice interaction, captures the active window title and stores it globally so typed text goes to the right application (not the Kuro overlay).

---

#### `core/brain.py` — Central Orchestrator (312 lines)

**Purpose:** The "brain" that coordinates everything: intent classification → routing → personality → RAG context → module dispatch → LLM response → memory.

**Class:** `Brain`

**Key methods:**
- `think(user_input)` — Full pipeline: classify → route → memory → dispatch → save → extract facts (background)
- `stream(user_input)` — Streaming version yielding tokens
- `_dispatch()` — Routes to handlers: `_handle_chat`, `_handle_system`, `_handle_system_info`, `_handle_dsa`, `_handle_blender`
- `_build_messages()` — Constructs messages with personality-aware system prompt + conversation history + RAG context + LTM context
- `set_emotion(emotion)` — Called from voice pipeline to update detected emotion

**Lazy-loaded modules:** RAG (RAGRetriever), system (Commander), DSA tutor (DSATutor), Blender (BlenderEngine)

**Memory integration per turn:**
1. Add user message to short-term memory
2. Get LLM response
3. Add response to short-term memory
4. Save to LTM (background thread)
5. Extract facts via LLM (daemon thread)

---

#### `core/config.py` — Configuration Hub

**Purpose:** Central config. All settings from `.env` file with safe defaults.

**Key settings:**
- LLM: `LOCAL_MODEL` (qwen2.5-coder:3b), `FALLBACK_MODEL` (phi3.5:3.8b), `CLOUD_MODEL` (qwen/qwen3-coder:free)
- STT: `WHISPER_MODEL` (base.en), `STT_DEVICE` (cuda), `STT_VAD_THRESHOLD` (0.45)
- TTS: `TTS_BACKEND` (edge)
- Wake word: `PICOVOICE_KEY`, `PORCUPINE_KEYWORD_PATH` (Hey-Kuro_en_windows_v4_0_0.ppn)
- System prompts: GENERAL, CODING, FAST, BLENDER, DSA

---

#### `core/llm_engine.py` — Dual-Brain LLM Engine

**Purpose:** Provides both LOCAL (Ollama) and CLOUD (OpenRouter) LLM access.

**Class:** `LLMEngine`

**Routing:**
- `chat_local()` — Ollama `/api/chat` (non-streaming)
- `chat_cloud()` — OpenRouter API (falls back to local Phi-3.5 if offline)
- `stream_local()` / `stream_cloud()` — Streaming variants
- `chat(messages, use_cloud, mode)` — Unified router

**Key config:** `keep_alive: -1` keeps model in VRAM permanently for instant responses (~200ms first token).

**Fallback chain:** Cloud → local fallback model (Phi-3.5) → error message.

---

#### `core/model_router.py` — Local vs Cloud Decision Engine

**Purpose:** Decides whether to use LOCAL or CLOUD LLM based on intent, keywords, and complexity.

**Enum — 14 intents:** GENERAL_CHAT, SYSTEM_COMMAND, FILE_OPERATION, APP_CONTROL, SYSTEM_INFO, MEMORY_RECALL, SETTINGS, TEACH_DSA, CODE_HELP, CODE_REVIEW, BLENDER_CREATE, BLENDER_MODIFY, WEB_SEARCH, UNKNOWN

**Routing rules (priority order):**
1. `FORCE_LOCAL` flag → always local
2. Cloud intents (TEACH_DSA, CODE_REVIEW, BLENDER_*) → cloud
3. Local intents (SYSTEM_*, FILE_*, APP_*, MEMORY_*, SETTINGS) → local "fast" mode
4. Cloud keyword detection → cloud
5. Local keyword detection → local
6. CODE_HELP complexity check: >200 chars or optimization keywords → cloud
7. Default → local

---

#### `core/intent_classifier.py` — Two-Stage Intent Classification

**Purpose:** Classifies user messages into one of 14 intent categories.

**Two-stage approach:**
1. **Stage 1 — Regex patterns** (fast, deterministic): ~60 compiled patterns across 14 categories
2. **Stage 2 — Keyword scoring** (ambiguous fallback): weighted keyword matching

---

#### `core/memory.py` — Three-Layer Memory

**Class:** `Memory`

**Layers:**
| Layer | Storage | Retention |
|-------|---------|-----------|
| Short-term | In-memory list | Last 16 messages (rolling) |
| Episodic | Daily JSON files (`data/conversation_history/YYYY-MM-DD.json`) | All days, 500 chars per turn |
| Profile | `data/user_profile.json` | Persistent (name, interaction count, DSA progress, preferences) |

---

#### `core/long_term_memory.py` — ChromaDB Long-Term Memory

**Class:** `KuroMemory`

**Collections:**
- `ltm_conversations` — Full user+assistant turns
- `ltm_user_facts` — Auto-extracted facts (e.g., "User's name is Goutham")

**Embedding:** `nomic-embed-text` via Ollama (768-dim, 5s fast-fail timeout)

**Key methods:**
- `save_conversation()` — Async save in background thread
- `save_fact(fact, category)` — Store extracted user facts
- `recall(query, n)` — Semantic search
- `get_context_for_prompt()` — Builds memory context block for system prompt injection

---

#### `core/memory_extractor.py` — Background Fact Extraction

**Purpose:** Runs in a daemon thread after each conversation turn. Sends the exchange to a local LLM with a structured prompt asking it to extract user facts (name, preferences, context). Saves each extracted fact to ChromaDB.

---

### 4.2 Agent System

The agent system is the **action execution pipeline** that translates voice commands into OS-level operations.

```
Voice Input
    │
    ▼
IntentParser.parse()        ← Regex fast-path (~0ms) or LLM slow-path
    │ returns {action, app, target, content, params, risk_hint, steps}
    ▼
ActionRouter.execute()      ← Routes to correct executor
    │
    ├── PermissionGate.check_and_execute()  ← Risk check (SILENT/ASK/BLOCKED)
    │       │
    │       ▼
    │   Executor method call
    │
    ▼
Result string → sent to frontend → spoken via TTS
```

#### `agent/intent_parser.py` — Voice Command Parser (280 lines)

**Class:** `IntentParser`

**Two-path parsing:**
- **Fast-path:** 25+ compiled regex patterns with builder functions (~0ms). No LLM needed.
- **Slow-path:** LLM-based (Mistral via Ollama) with detailed JSON schema prompt.

**Intent schema:**
```json
{
  "action": "open|close|type|search|play|send|compound|...",
  "app": "notepad|chrome|whatsapp|...|null",
  "target": "contact name|file path|window title|null",
  "content": "message text|search query|text to type|null",
  "params": {"level": 50, "direction": "left", ...},
  "risk_hint": "safe|moderate|destructive|critical",
  "description": "human readable description",
  "steps": null  // or array for compound commands
}
```

**Compound commands:** For multi-step actions like "generate me a python code inside notepad":
```json
{
  "action": "compound",
  "steps": [
    {"action": "open", "app": "notepad"},
    {"action": "type", "content": "python code"}
  ]
}
```

**Key regex patterns:**
| # | Pattern | Example |
|---|---------|---------|
| 1 | `^open\s+(\w+)\s+(?:and|then)\s+(?:type|write)\s+(.+)$` | "open notepad and type hello" |
| 2 | `^send\s+(.+?)\s+to\s+(\w+)\s+on\s+whatsapp$` | "send hi to goutham on whatsapp" |
| 3 | `^(?:send\|tell\|message)\s+(\w+)\s+(.+?)` | "tell goutham hi" (pronoun guard for "me/us/them") |
| 4 | `^open\s+(.+)$` | "open chrome" |
| 5 | `^close\s+(.+)$` | "close notepad" |
| 6 | `^(?:generate\|write\|create\|make)...\s+(?:in\|inside\|into\|on)\s+(app)$` | "generate python code inside notepad" |
| 7 | `^(?:generate\|create\|make)...\s+(.+)$` | "generate a fibonacci function" |
| 8 | `^(?:type\|write)\s+(.+)$` | "type hello world" |
| 9 | `^press\s+(.+)$` | "press enter" |
| 10 | `^(?:set\s+)?volume\s+(?:to\s+)?(\d+)` | "set volume to 50" |
| 11 | `^(?:search\|google)\s+(?:google\|bing\|youtube)?\s*(?:for\s+)?(.+)$` | "search google for python tutorials" |
| 12 | `^play\s+(.+?)(?:\s+on\s+youtube)?$` | "play despacito on youtube" |
| 13 | `^scroll\s+(up\|down)(?:\s+(\d+))?$` | "scroll down 5" |
| 14 | `^(?:start\s+)?(?:dictate\|dictation\|...)$` | "start dictation" |

---

#### `agent/action_router.py` — Action Routing & Execution (322 lines)

**Class:** `ActionRouter`

**Executors managed:**
- `AppLauncher` — Open/close Windows apps
- `MessagingAgent` — WhatsApp, Telegram, Discord, email
- `BrowserAgent` — Web browsing and search
- `FileAgent` — File system operations
- `SystemAgent` — Volume, brightness, power, typing, screenshots
- `WindowAgent` — Window snapping, minimize, maximize, switch
- `VisionAgent` — Screen OCR + click + vision LLM

**Compound step handling:**
```python
# For compound commands (open notepad, then type code):
for i, step in enumerate(steps):
    result = self._route(step_action, step_app, ...)
    if step_action == "open" and i < len(steps) - 1:
        time.sleep(2.0)  # Wait for app to load
        # Update target window to the newly opened app
        set_target_window(newly_opened_window.title)
```

**40+ actions routed:** send (WhatsApp/Telegram/Discord/email), open, close, type, dictate, press, snap, maximize, minimize, switch_window, search, browse, play, scroll, new_tab, delete, move, copy, rename, create, list, volume, brightness, screenshot, battery, stats, shutdown, restart, lock, sleep, hibernate, task_kill, clipboard, find, click, read_screen

**Fallback:** `vision.read_screen_and_act()` for unrecognized commands.

---

#### `agent/permission_gate.py` — Risk-Based Permission System (172 lines)

**Class:** `PermissionGate`

**Risk levels:**
| Level | Behavior |
|-------|----------|
| `SILENT` | Auto-allow, no confirmation needed |
| `ASK_ONCE` | Voice confirmation, remembered for session |
| `ALWAYS_ASK` | Voice confirmation every time |
| `BLOCKED` | Absolute block, never executed |

**Risk rule mapping:**
| Action | Context | Risk Level |
|--------|---------|------------|
| open, close, search, play, type, volume, brightness, screenshot, lock | any | SILENT |
| delete | recycle bin | ASK_ONCE |
| email | any | ASK_ONCE |
| delete | permanent | ALWAYS_ASK |
| shutdown, restart, hibernate | any | ALWAYS_ASK |
| install, uninstall | any | ALWAYS_ASK |
| send | unknown contact | ALWAYS_ASK |
| system32/defender/format C: | any | BLOCKED |

**Permission flow:**
1. Speak question: "Sir, I need your confirmation. I'm about to delete report.pdf. Should I proceed?"
2. Listen for yes/no (8s timeout via STT)
3. Parse response: "yes", "yeah", "go ahead", "proceed" → granted; "no", "cancel", "stop" → denied
4. Log decision to `permissions/permission_log.json` (last 500 entries)

---

#### `agent/executors/app_launcher.py` — Windows App Launcher (125 lines)

**Class:** `AppLauncher`

**App registry (30+ apps):**
| App | Path |
|-----|------|
| WhatsApp | `%LOCALAPPDATA%\WhatsApp\WhatsApp.exe` |
| Telegram | `%APPDATA%\Telegram Desktop\Telegram.exe` |
| Discord | `%LOCALAPPDATA%\Discord\Update.exe --processStart Discord.exe` |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Firefox | `C:\Program Files\Mozilla Firefox\firefox.exe` |
| Edge | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| Spotify | `%APPDATA%\Spotify\Spotify.exe` |
| VSCode | `%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe` |
| Notepad | `notepad.exe` |
| Calculator | `calc.exe` |
| Settings | `ms-settings:` |
| + 20 more | (Office, system tools, dev tools) |

**Smart focus:** Tries to bring existing window to front before launching new instance.
**Close:** Kills processes by name via `psutil`.

---

#### `agent/executors/browser_agent.py` — Web Browser Agent

**Class:** `BrowserAgent`

**Two modes:**
1. **Basic (webbrowser.open)** — Opens URLs in user's default browser (Chrome/Edge with all cookies and logins). Used for: `go_to()`, `search_google()`, `play_youtube()`, `whatsapp_web_send()`
2. **Advanced (Playwright, lazy-loaded)** — Headless Chromium for automation. Used for: `read_page()`, `click_element()`, `fill_input()`

**Key methods:**
- `search_google(query)` → Opens `google.com/search?q=...` in default browser
- `play_youtube(query)` → Opens `youtube.com/results?search_query=...` in default browser
- `whatsapp_web_send(phone, message)` → Opens `web.whatsapp.com/send?phone=...&text=...`, auto-presses Enter after 10s
- `scroll(direction, times)` → Uses `pyautogui.scroll()` (works in any window, not just browser)
- `cleanup()` → Closes Playwright browser to prevent leaked Chromium processes

---

#### `agent/executors/system_agent.py` — System Control Agent (364 lines)

**Class:** `SystemAgent`

**Smart Typing (most complex feature):**

The `smart_type(content)` method decides between literal typing and LLM-generated content:

```python
def smart_type(self, content):
    if self._is_generative(content):
        return self._stream_type_from_llm(content)  # Generate via LLM
    else:
        return self.type_text(content)  # Literal clipboard paste
```

**`_is_generative()` decision:**
- ≤3 words without generation keywords → **literal** ("type hello" → types "hello")
- Language hint present ("in python", "in c++") → **generate**
- Generation keyword present (program, code, function, algorithm, essay, email, etc.) → **generate**
- Longer text without keywords → **literal** (dictation mode)

**`_stream_type_from_llm()` flow:**
1. Detect programming language from text
2. Build focused prompt (code vs prose)
3. Refocus the target window (the app that was active before Kuro overlay)
4. Stream from Ollama token-by-token
5. Accumulate tokens in 12-char buffer
6. Paste each buffer via `pyperclip.copy()` + `Ctrl+V`
7. First token appears in ~0.5-1s
8. Save and restore user's clipboard afterward

**Other capabilities:**
| Category | Methods |
|----------|---------|
| Audio | `set_volume()` (pycaw), `mute()`, `volume_up/down()` (pyautogui) |
| Display | `set_brightness()` (screen_brightness_control, lazy-loaded) |
| Power | `shutdown()`, `restart()`, `hibernate()`, `sleep()`, `lock_screen()` |
| Screenshots | `screenshot()` → saves to `~/Desktop/kuro_shot_YYYYMMDD_HHMMSS.png` |
| System Info | `get_battery()`, `get_stats()`, `list_processes()`, `kill_process()` |
| Commands | `run_command()` — Execute any shell command (15s timeout, 500 char output cap) |

---

#### `agent/executors/messaging.py` — Messaging Agent

**Class:** `MessagingAgent`

**WhatsApp Desktop:** `Ctrl+F` → search contact → Enter → `Ctrl+Shift+Space` (focus message box) → type message → Enter
**WhatsApp Web:** Opens `web.whatsapp.com/send?phone=...&text=...` → waits 10s → auto-presses Enter
**Telegram:** `Ctrl+F` → search → Enter → type → Enter
**Discord:** `Ctrl+K` (quick switcher) → search → Enter → type → Enter
**Email:** Opens `mailto:` link (triggers Outlook/default client) → `Alt+S` to send

**Contact resolution:** Looks up names in `contacts/contacts.json` (exact + partial match) → returns phone number for WhatsApp Web URL

**Fast typing:** All messaging uses clipboard paste (`Ctrl+V`) instead of `pyautogui.typewrite()` — 10× faster and Unicode-safe.

---

#### `agent/executors/file_agent.py` — File System Agent

**Class:** `FileAgent`

**Operations:** open_file, open_folder, search_files, move_to_recycle (winshell), permanent_delete, copy_file, move_file, rename_file, create_folder, create_file, read_file, zip_files, get_folder_size, list_folder, empty_recycle_bin

**Path resolution shortcuts:** `~/Downloads`, `~/Documents`, `~/Desktop`, `~/Pictures`, etc.

---

#### `agent/executors/window_agent.py` — Window Management Agent (86 lines)

**Class:** `WindowAgent`

**Operations:**
- `snap_left/right(app)` → `Win+Left` / `Win+Right`
- `maximize/minimize(app)` → via pygetwindow
- `minimize_all()` → `Win+D`
- `switch_window(app)` → Find window by title keyword, activate
- `list_windows()` → Lists all visible windows
- `move_window(app, x, y)` / `resize_window(app, w, h)`
- `virtual_desktop_new()` / `virtual_desktop_next()` → `Win+Ctrl+D` / `Win+Ctrl+Right`

---

#### `agent/executors/vision_agent.py` — Screen-Aware Vision Agent (398 lines)

**Class:** `VisionAgent`

**OCR engines (priority):**
1. **Tesseract** (pytesseract) — auto-detects install path on Windows
2. **Windows native OCR** (PowerShell + Windows.Media.Ocr) — no extra install needed

**Vision LLM:** Auto-detects LLaVA / BakLLaVA / MiniCPM-V / Moondream in Ollama. Sends screenshots as base64.

**Key features:**
- `find_text_on_screen(text)` → OCR → returns `[{text, x, y, w, h, cx, cy}]`
- `find_and_click(text)` → OCR-based text finder + click at center coordinates
- `analyze_screen(prompt)` → Sends screenshot to vision LLM (or OCR dump fallback)
- `verify_typed_text(expected)` → OCR verification after typing (confirms text was typed correctly)
- `read_screen_and_act(command)` → Vision-guided fallback for unknown commands

---

### 4.3 Voice Pipeline

#### `voice/stt.py` — Speech-to-Text Engine (446 lines)

**Class:** `STT`

**Model:** `base.en` via faster-whisper (~400MB VRAM), CUDA with CPU fallback

**CUDA fix:** Auto-adds NVIDIA DLL directories for Windows (solves common faster-whisper CUDA crash)

**Key methods:**
| Method | Purpose |
|--------|---------|
| `transcribe_file(path)` | Transcribe audio file |
| `transcribe_bytes(audio_bytes)` | Transcribe raw PCM (with optional noise reduction via noisereduce) |
| `listen()` | VAD-based recording + auto-cutoff on silence + transcription |
| `listen_streaming(on_chunk, chunk_seconds, max_seconds)` | Streaming dictation: 3s chunks, types each as it arrives |
| `record_until_silence()` | Silero VAD real-time mic capture |

**VAD config:**
| Setting | Value |
|---------|-------|
| VAD threshold | 0.45 (lowered to 0.30 in `.env` override) |
| Silence cutoff | 600ms (700ms in override) |
| Pre-speech buffer | 250ms |
| Initial timeout | 5.0s (8.0s in override) |
| Max record time | 60s |
| Trigger frames | 2 |

---

#### `voice/tts.py` — Text-to-Speech Engine

**Class:** `TTS`

**Backend:** edge-tts (Microsoft Edge neural voices)
**Voice:** `en-US-GuyNeural` (male, natural-sounding)

**Greetings:** Random selection from:
- "Yeah?"
- "What is it?"
- "Hmm?"
- "I'm listening."
- "Yes?"
- "I'm here."

**Methods:**
- `greet()` — Play random greeting (async)
- `speak(text)` / `speak_sync(text)` — Speak text (async/blocking)
- `stop()` — Stop mid-speech
- `is_playing` — Property for polling

**Playback:** `sounddevice` + `soundfile`, with PowerShell `SoundPlayer` fallback

---

#### `voice/wake_word.py` — Wake Word Detector

**Class:** `WakeWordDetector`

**Engine:** Porcupine (Picovoice) — custom trained `.ppn` model
**Wake phrase:** "Hey Kuro"
**Model file:** `Hey-Kuro_en_windows_v4_0_0.ppn`
**Sensitivity:** 0.7
**CPU usage:** ~2% (silicon-optimized detection)
**Runs as:** Daemon thread with proper PvRecorder/Porcupine cleanup on stop

---

### 4.4 Personality System

#### `kuro/personality/prompt_builder.py` — System Prompt Builder

**Core identity (injected into every LLM call):**
> You are **Kuro** — a J.A.R.V.I.S.-class AI companion. Loyal, dry humor, occasionally calls user "sir", never robotic or sycophantic. Confident, protective of the user's time.

**Mode-aware prompts:**
| Mode | Style |
|------|-------|
| WORK | Sharp, efficient, no filler, solution-first |
| CHILL | Warm, casual, humor OK, longer replies |
| DEEP_FOCUS | Ultra-minimal, code-only, no chatter |
| ASSISTANT | Balanced, professional, clear |

**Emotion addons:** Modifies tone based on detected emotion:
- Stressed → soothing, shorter responses
- Angry → calm and measured
- Sad → warm and empathetic
- Tired → brief, supportive
- Excited → match energy
- Happy → positive

**Memory injection:** Appends relevant memory context to system prompt:
```
## Memory Context
- User's name is Goutham
- User is working on a Kuro AI assistant project
- User prefers Python
```

---

#### `kuro/personality/personality_modes.py` — Mode Management

**Auto-switching by time of day:**
| Time | Mode |
|------|------|
| 6-9am | ASSISTANT |
| 9am-12pm | WORK |
| 12-1pm | CHILL |
| 1-6pm | WORK |
| 6-10pm | CHILL |
| 10pm+ | DEEP_FOCUS |

**Emotion-based override:** stressed/angry/sad → CHILL, excited → WORK

**Voice commands:** "work mode", "chill mode", "deep focus", "auto mode" (reset to time-based)

---

#### `kuro/personality/emotion_detector.py` — Audio-Based Emotion Detection

**Class:** `EmotionDetector`

**Features extracted (via librosa):** pitch, energy/RMS, tempo, MFCCs, zero-crossing rate

**Emotions detected:** stressed, angry, excited, happy, sad, tired, neutral

**Classification:** Threshold-based scoring with combination boosts

**Method:** `detect_from_mic(duration_s=4.0)` → `EmotionResult(emotion, confidence, features)`

---

### 4.5 Security & Memory (kuro/ module)

#### `kuro/security/voice_auth.py` — Voice Authentication

**Class:** `VoiceAuthenticator`

**Engine:** resemblyzer (VoiceEncoder) — speaker embedding comparison via cosine similarity

**Threshold:** 0.75 (configurable)

**Graceful degradation:** If resemblyzer not installed or no reference embedding → returns `authorized=True`

---

#### `kuro/memory/episodic_memory.py` — Timeline-Aware Memory

**Class:** `EpisodicMemory`

**Metadata per episode:** timestamp, date, weekday, hour, type, importance, tags, week_number, month, year

**Time queries:** `get_today()`, `get_yesterday()`, `get_last_n_days()`, `get_by_weekday()`

**Natural language time parsing:** "today", "yesterday", "last week", weekday names

---

#### `kuro/memory/importance_scorer.py` — Memory Importance Scoring

**Class:** `ImportanceScorer`

**Scoring:** Sends memory text to LLM → expects JSON `{score: 1-10, reason: "..."}`

**Temporal decay:** Exponential decay (`base × e^(-0.05 × days)`) with floor of 0.1

---

#### `kuro/memory/encrypted_store.py` — Encrypted Memory Storage

**Class:** `EncryptedMemoryStore`

**Encryption:** Fernet (symmetric) via `cryptography` library

**Key management:**
- Auto-generates key at `data/.kuro_key`
- Or derives from passphrase via PBKDF2 (480,000 iterations, SHA256)

---

### 4.6 Frontend & Desktop

#### `kuro-ai/frontend/src/App.jsx` — React Frontend (670 lines)

**UI Components:**
- `KuroOrb` — Animated orb with rotating rings, pulse waves, and equalizer bars
- `StatusLine` — Current state display (idle/listening/thinking/speaking)
- `ResponseDisplay` — Markdown response renderer
- `HudLines` — Decorative HUD elements

**States:** `idle` → `listening` → `thinking` → `speaking` → `idle`

**Key features:**
- Text input + voice input dual-mode
- Model switching via voice ("switch to llama")
- Health checks every 15 seconds (Ollama + brain status)
- Edge-TTS audio playback with browser SpeechSynthesis fallback
- Wake chime: Web Audio API three-tone ascending (A5 → C#6 → E6)
- Interrupt detection: "stop", "cancel", "shut up" during thinking/speaking
- Auto-dock after action (moves to right edge at 10% opacity)
- Continuous listening mode (auto-triggers next voice capture after response)

---

#### `kuro-ai/backend/server.js` — Node.js Backend Proxy

**Purpose:** Middleware between React frontend and Python brain API

**Chat routing strategy:**
1. Try Python Brain API (`POST http://127.0.0.1:8000/chat`)
2. Fallback: Direct Ollama call (`POST http://localhost:11434/api/chat`)

**Persona prompts:** friendly, professional, tutor, concise (used in direct Ollama fallback)

**SSE proxy:** Keeps connection to Electron permanently open, retries Python internally. Electron never sees disconnects.

---

#### `kuro-ai/desktop/main.js` — Electron Main Process (397 lines)

**Window configuration:**
- 480×620, frameless, transparent, always-on-top, skip taskbar
- Bottom-center when active, right-edge when docked
- 10% opacity in docked mode, ignores mouse events

**Wake word flow:**
1. SSE connection to `/wake/events`
2. On `data: wake` → `showOverlayAndListen()`
3. Fetch `POST /wake/listen` → wait for transcription
4. Send `command-text` IPC to renderer with transcribed text

**Keyboard shortcut:** `Ctrl+Shift+K` toggle
**System tray:** Icon with Activate / Settings / Quit menu
**Auto-start backend:** Spawns `backend/server.js` if not running
**Microphone permissions:** Auto-granted in Electron session

---

#### `kuro-ai/desktop/preload.js` — Secure IPC Bridge

Exposes `window.kuro` API via `contextBridge` (contextIsolation=true):
- `hide()`, `show()`, `dock()`, `undock()`
- `triggerListen()`, `onAutoListen()`, `onCommandText()`
- `removeListener(channel, handler)`

---

### 4.7 RAG System

#### `rag/embedder.py` — Document Chunking & Indexing

**Class:** `Embedder`

**Collections:** `dsa`, `blender_docs`, `personal_notes` (mapped to `rag/documents/` subdirectories)

**Chunking strategies:**
| Format | Strategy |
|--------|----------|
| `.md` | Split on headers (`##`, `###`), preserves structure |
| `.py` | Split on function/class definitions |
| `.txt` | Overlapping character chunks (1000 chars, 150 overlap) |

**Embedding:** `nomic-embed-text` via Ollama (768-dimensional, 30s timeout)

---

#### `rag/retriever.py` — Semantic Search

**Class:** `RAGRetriever`

**Method:** `retrieve(query, collection_name, top_k)` → searches across all collections if none specified

**Returns:** `[{content, source, distance}]` sorted by relevance

---

### 4.8 Other Modules

#### `system/commander.py` — Legacy System Command Handler (207 lines)

**Class:** `Commander`

Used by `Brain._handle_system()` for: screenshots (PIL.ImageGrab), open app, clipboard read, type (delegates to SystemAgent.smart_type), press key.

System info: CPU, RAM, disk, GPU (nvidia-smi), battery via psutil.

---

#### `teaching/dsa_tutor.py` — DSA Teaching Module

**Class:** `DSATutor`

**Methods:** `teach(topic)` (cloud), `quick_recall(question)` (local), `generate_practice_problem()` (cloud), `review_solution()` (cloud), `explain_complexity()` (cloud)

**Lesson format:** Definition → Analogy → Walkthrough → Code → Complexity → Use cases → Mistakes → Practice

---

#### `creative/blender_engine.py` — Blender 3D Integration

**Class:** `BlenderEngine`

**Pipeline:** Describe → Generate bpy script (cloud LLM) → Safety check → Wrap in template → Execute

**Modes:** "watch" (writes to watch folder for live viewport) or "background" (`blender --background` render)

---

#### `creative/safety_checker.py` — Script Safety Validation (116 lines)

**Two-layer validation for Blender scripts:**
| Layer | Mechanism | Blocked |
|-------|-----------|---------|
| 1 | Regex patterns | `os.system`, `subprocess`, `socket`, `open()` |
| 2 | AST analysis | `exec`, `eval`, `compile`, `__import__`, blocked imports (os, subprocess, socket, shutil, sys, requests, pickle, ctypes) |

---

#### `kuro/core/kuro_main.py` — Alternative Full Runtime

**Class:** `KuroRuntime`

An alternative standalone mode (`--mode kuro`) with enhanced features:
- sentence-transformers (`all-MiniLM-L6-v2`) instead of nomic-embed-text
- openWakeWord ("hey_jarvis") instead of Porcupine
- EncryptedMemoryStore for memory-at-rest encryption
- EpisodicMemory with timeline/weekday awareness
- Emotion detection before each command
- Worker queue pattern for non-blocking wake handling

---

#### `kuro/core/response_cache.py` — Disk-Backed Cache

**Class:** `KuroCache`

**Smart TTL rules:**
| Query Type | TTL |
|------------|-----|
| Weather, news | 30 min |
| Time, date | 60 sec |
| How-to, explain | 7 days |
| Default | 1 hour |

**Key:** MD5 of `query::mode`

---

### 4.9 Data & Config Files

| File | Purpose |
|------|---------|
| `data/user_profile.json` | User preferences, interaction count (195+), DSA progress |
| `contacts/contacts.json` | Contact registry (name → phone/email) |
| `permissions/permission_config.json` | Static risk rules for permission gate |
| `permissions/permission_log.json` | Last 500 permission decisions with timestamps |
| `data/conversation_history/*.json` | Daily conversation logs |
| `data/kuro_memory/chroma.sqlite3` | ChromaDB LTM database |
| `data/chroma_db/chroma.sqlite3` | ChromaDB RAG database |
| `Hey-Kuro_en_windows_v4_0_0.ppn` | Custom Porcupine wake word model |
| `.env` | All environment variables |
| `requirements.txt` | 69 Python dependencies |

---

## 5. Electron ↔ Node.js ↔ Python Connection Chain

```
Electron (desktop/main.js)
  │
  ├──[IPC]── preload.js ── App.jsx (React renderer)
  │             │
  │             └──[HTTP GET/POST]── Node.js server.js (port 3001)
  │                                     │
  │                                     ├──[HTTP proxy]── Python FastAPI (port 8000)
  │                                     │                    ├── Brain (intent + routing + LLM)
  │                                     │                    ├── Voice (Porcupine + Whisper + edge-tts)
  │                                     │                    ├── Action system (7 executors)
  │                                     │                    ├── Memory (ChromaDB + JSON + profile)
  │                                     │                    └── RAG (ChromaDB)
  │                                     │
  │                                     └──[HTTP fallback]── Ollama (port 11434)
  │
  └──[SSE]── Node /wake/events ←── Python /wake/events (wake word stream)
```

**IPC channels between Electron main ↔ renderer:**
| Channel | Direction | Purpose |
|---------|-----------|---------|
| `hide-overlay` | Renderer → Main | Hide the overlay window |
| `show-overlay` | Renderer → Main | Show the overlay window |
| `dock-overlay` | Renderer → Main | Move to right edge, 10% opacity |
| `undock-overlay` | Renderer → Main | Restore to center position |
| `trigger-listen` | Renderer → Main | Start voice capture |
| `auto-listen` | Main → Renderer | Auto-trigger next listen cycle |
| `command-text` | Main → Renderer | Send transcribed text to frontend |
| `open-settings` | Main → Renderer | Open settings panel |

---

## 6. All API Endpoints

### Python Brain API (port 8000)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Brain + voice pipeline status |
| `POST` | `/chat` | Main chat (action fast-path + LLM) |
| `POST` | `/api/chat` | Alias for `/chat` |
| `GET` | `/api/health` | Alias for `/health` |
| `GET` | `/system` | System info (legacy) |
| `GET` | `/wake/events` | SSE wake word stream |
| `POST` | `/wake/listen` | Full voice capture pipeline |
| `POST` | `/wake/dictate` | Streaming dictation mode |
| `POST` | `/wake/stop` | Stop TTS mid-speech |
| `POST` | `/tts/speak` | Generate edge-tts MP3 audio |
| `GET` | `/system/info` | CPU/RAM/Disk/GPU stats |
| `POST` | `/system/exec` | Execute system command |
| `POST` | `/blender` | Generate + run Blender script |
| `POST` | `/teach/dsa` | DSA teaching lesson |
| `POST` | `/rag/query` | RAG semantic search |
| `POST` | `/rag/ingest` | Re-index documents |

### Node.js Backend (port 3001)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Health + Ollama status check |
| `GET` | `/api/models` | List available Ollama models |
| `POST` | `/api/chat` | Chat proxy (brain → Ollama fallback) |
| `GET` | `/api/brain-health` | Python brain connectivity |
| `GET` | `/wake/events` | SSE proxy for wake word |
| `POST` | `/wake/listen` | STT proxy |
| `POST` | `/wake/dictate` | Dictation proxy |
| `POST` | `/wake/stop` | Stop TTS proxy |
| `POST` | `/tts/speak` | TTS audio proxy |
| `POST` | `/api/system/read-file` | Read file (direct Node.js) |
| `POST` | `/api/system/list-dir` | List directory (direct Node.js) |
| `POST` | `/api/system/exec` | Execute command (direct Node.js) |
| `GET` | `/api/brain/system/info` | System info proxy |
| `POST` | `/api/brain/system/exec` | System exec proxy |
| `POST` | `/api/brain/blender` | Blender generation proxy |
| `POST` | `/api/brain/teach/dsa` | DSA teaching proxy |
| `POST` | `/api/brain/rag/query` | RAG query proxy |
| `POST` | `/api/brain/rag/ingest` | RAG ingest proxy |

---

## 7. All Environment Variables

| Variable | Default | Category |
|----------|---------|----------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | LLM |
| `LOCAL_MODEL` | `qwen2.5-coder:3b-instruct-q4_K_M` | LLM |
| `FALLBACK_MODEL` | `phi3.5:3.8b-mini-instruct-q4_K_M` | LLM |
| `EMBED_MODEL` | `nomic-embed-text` | Embeddings |
| `LOCAL_TEMPERATURE` | `0.4` | LLM |
| `LOCAL_NUM_PREDICT` | `256` | LLM |
| `LOCAL_NUM_CTX` | `2048` | LLM |
| `LOCAL_TOP_P` | `0.9` | LLM |
| `CLOUD_API_URL` | `https://openrouter.ai/api/v1/chat/completions` | Cloud LLM |
| `CLOUD_API_KEY` | `""` | Cloud LLM |
| `CLOUD_MODEL` | `qwen/qwen3-coder:free` | Cloud LLM |
| `FORCE_LOCAL` | `false` | Routing |
| `WHISPER_MODEL` | `base.en` | STT |
| `WHISPER_BEAM_SIZE` | `1` | STT |
| `STT_DEVICE` | `cuda` | STT |
| `STT_VAD_THRESHOLD` | `0.45` | STT/VAD |
| `STT_SILENCE_MS` | `600` | STT/VAD |
| `STT_PRE_SPEECH_MS` | `250` | STT/VAD |
| `STT_MAX_RECORD_SECONDS` | `60` | STT/VAD |
| `STT_INITIAL_TIMEOUT_S` | `5.0` | STT/VAD |
| `STT_USE_NOISE_REDUCE` | `true` | STT |
| `PICOVOICE_KEY` | `""` | Wake Word |
| `PORCUPINE_KEYWORD_PATH` | `Hey-Kuro_en_windows_v4_0_0.ppn` | Wake Word |
| `BLENDER_PATH` | `blender` | Blender |
| `BLENDER_WATCH_DIR` | `data/blender_watch` | Blender |
| `APP_PORT` | `7860` | Server |
| `APP_DEBUG` | `false` | Server |
| `LOG_LEVEL` | `INFO` | Logging |
| `USERNAME` | OS default | App Launcher |
| `APPDATA` | OS default | App Launcher |
| `LOCALAPPDATA` | OS default | App Launcher |

---

## 8. Memory System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           MEMORY LAYERS                                 │
├──────────────┬──────────────────────────────────────────────────────────┤
│  Short-Term  │  In-memory list, last 16 messages (rolling)             │
│  (Memory)    │  Deleted on restart                                      │
├──────────────┼──────────────────────────────────────────────────────────┤
│  Episodic    │  Daily JSON files: data/conversation_history/            │
│  (Memory)    │  Timestamped turns, truncated to 500 chars               │
│              │  Persistent across restarts                               │
├──────────────┼──────────────────────────────────────────────────────────┤
│  LTM Facts   │  ChromaDB collection: ltm_user_facts                     │
│  (KuroMemory)│  Auto-extracted by memory_extractor.py (background LLM)  │
│              │  Embedded via nomic-embed-text (768-dim)                  │
│              │  E.g., "User's name is Goutham", "User prefers Python"   │
├──────────────┼──────────────────────────────────────────────────────────┤
│  LTM Convos  │  ChromaDB collection: ltm_conversations                  │
│  (KuroMemory)│  Full user+assistant turns, saved async in background     │
│              │  Semantic search for relevant past conversations          │
├──────────────┼──────────────────────────────────────────────────────────┤
│  RAG Docs    │  ChromaDB collections: dsa, blender_docs, personal_notes │
│  (Embedder)  │  Ingested from rag/documents/ (markdown/python/txt)      │
├──────────────┼──────────────────────────────────────────────────────────┤
│  Profile     │  data/user_profile.json                                   │
│              │  Name, interaction count, DSA progress, preferences       │
├──────────────┼──────────────────────────────────────────────────────────┤
│  kuro/ alt   │  Enhanced memory (used in --mode kuro):                   │
│  module      │  - EpisodicMemory: timeline + weekday metadata            │
│              │  - ImportanceScorer: LLM-scored + exponential decay        │
│              │  - EncryptedMemoryStore: Fernet encryption at rest         │
│              │  - KuroCache: diskcache with smart TTL (30min–7 days)      │
└──────────────┴──────────────────────────────────────────────────────────┘
```

**Memory flow per conversation turn:**
```
1. memory.add("user", input)           → Short-term (in-memory)
2. _log_episode()                       → Episodic (daily JSON file)
3. ltm.save_conversation()             → ChromaDB (async background thread)
4. extract_and_save_facts()            → LLM extracts facts → ltm.save_fact() (daemon thread)
5. On NEXT query:
   ltm.get_context_for_prompt()        → Retrieves relevant facts + past convos
                                        → Injected into system prompt
```

---

## 9. Security Model

| Layer | Mechanism | Details |
|-------|-----------|---------|
| **Permission Gate** | Risk-based (4 levels) | SILENT / ASK_ONCE / ALWAYS_ASK / BLOCKED with voice confirmation |
| **Blocked Patterns** | Regex blacklist | system32, disable defender/firewall, format C:, rmdir /s /q C:\ |
| **Script Safety** | Two-layer validation | Regex + AST analysis blocks os/subprocess/socket/eval/exec in Blender scripts |
| **Voice Auth** | Speaker verification | resemblyzer cosine similarity ≥ 0.75 (optional) |
| **Memory Encryption** | Fernet + PBKDF2 | Encryption at rest with auto-generated key or passphrase-derived key |
| **Context Isolation** | Electron security | preload.js with contextBridge, no nodeIntegration |
| **API Scope** | CORS whitelist | FastAPI restricted to localhost:3001 and localhost:5173 only |
| **Permission Logging** | Audit trail | Last 500 decisions with timestamps in permission_log.json |

---

## 10. Tech Stack

### Core AI
| Component | Technology | Purpose |
|-----------|------------|---------|
| Local LLM | Ollama (Qwen2.5-Coder 3B Q4) | Primary inference (~200ms first token) |
| Fallback LLM | Ollama (Phi-3.5 3.8B) | When primary model fails |
| Cloud LLM | OpenRouter API (Qwen3-Coder free tier) | Complex tasks (DSA, code review, Blender) |
| Embeddings | nomic-embed-text (768-dim) via Ollama | ChromaDB vector embeddings |
| Alt Embeddings | sentence-transformers (all-MiniLM-L6-v2) | Used in `--mode kuro` |
| Vector DB | ChromaDB (persistent, local SQLite) | LTM + RAG storage |

### Voice
| Component | Technology | Purpose |
|-----------|------------|---------|
| Wake Word | Porcupine (custom .ppn model) | Always-on "Hey Kuro" detection (~2% CPU) |
| Alt Wake Word | openWakeWord (ONNX) | Free alternative (no API key) |
| STT | faster-whisper (Distil-Whisper base.en, CUDA) | Speech transcription |
| VAD | Silero VAD (via faster-whisper) | Voice Activity Detection |
| TTS | edge-tts (en-US-GuyNeural) | Neural text-to-speech |
| Audio I/O | sounddevice + soundfile | Recording and playback |
| Noise Reduction | noisereduce | Optional audio cleaning |

### OS Automation
| Component | Technology | Purpose |
|-----------|------------|---------|
| Window Management | pygetwindow | Find, focus, snap, resize windows |
| Input Simulation | pyautogui + pyperclip | Keyboard/mouse control, clipboard paste |
| Audio Control | pycaw | Windows audio endpoint volume |
| Display Control | screen_brightness_control | Brightness adjustment |
| System Info | psutil | CPU, RAM, disk, battery, processes |
| Process Management | psutil | Kill processes, list running apps |
| OCR | pytesseract / Windows native OCR | Screen text recognition |
| Vision | LLaVA/BakLLaVA via Ollama | Screenshot understanding |

### Frontend & Desktop
| Component | Technology | Purpose |
|-----------|------------|---------|
| Desktop App | Electron | Frameless overlay, system tray, hotkey |
| Frontend | React (Vite, JSX) | Animated orb UI |
| Backend Proxy | Express.js | HTTP middleware + fallback |
| Animation | CSS + Web Audio API | Orb rings, pulse waves, wake chime |

### Security & Storage
| Component | Technology | Purpose |
|-----------|------------|---------|
| Encryption | cryptography (Fernet, PBKDF2) | Memory encryption at rest |
| Caching | diskcache | Response cache with smart TTL |
| Voice Auth | resemblyzer | Speaker verification |
| Script Safety | AST module | Blender script validation |

### 3D Creative
| Component | Technology | Purpose |
|-----------|------------|---------|
| 3D Engine | Blender (bpy) | Script generation + execution |
| Script Gen | Cloud LLM | Natural language → bpy Python code |

---

## 11. Design Patterns & Principles

### 1. Dual-Module Architecture
The `core/` and `kuro/` packages duplicate some functionality (memory, LLM engine, wake word). `core/` is the active production path used by `main.py --mode server`. `kuro/` is an enhanced alternative path (`--mode kuro`) with encryption, episodic timeline memory, importance scoring, and response caching.

### 2. Lazy Loading Everywhere
Brain lazy-loads RAG, system, blender, DSA tutor. ChromaDB clients are lazy. Playwright only initializes on first advanced browser command. This prevents import crashes from breaking unrelated features.

### 3. Fast-Path / Slow-Path Pattern
Both `IntentClassifier` (regex → keyword scoring) and `IntentParser` (regex → LLM) use fast deterministic matching first, falling back to LLM only for ambiguous inputs. Voice commands are resolved in ~0ms when regex matches.

### 4. Background Threads for Non-Blocking Operations
LTM conversation saving, fact extraction, and streaming dictation all run in daemon threads. The user never waits for memory operations.

### 5. Graceful Degradation Chain
Every component has fallbacks:
- Cloud → Local fallback model → Error message
- CUDA → CPU
- Tesseract → Windows native OCR
- edge-TTS → Browser SpeechSynthesis
- Porcupine → openWakeWord
- resemblyzer → Permissive stub (always authorized)
- Playwright → webbrowser.open()

### 6. Smart Typing Intelligence
`SystemAgent._is_generative()` distinguishes "type hello" (literal paste) from "type binary search program in c++" (LLM-generated, streamed token-by-token via clipboard).

### 7. Window Focus Tracking
Before any voice interaction, the system captures the active window title. After processing, `_refocus_target_window()` restores focus so typed text goes to the correct application, not the Kuro overlay.

### 8. SSE Resilience
Node.js keeps the SSE connection to Electron permanently open, retrying the Python connection internally. Electron never sees disconnects.

### 9. Compound Command Orchestration
Multi-step voice commands ("open notepad and generate a python code") are parsed into a `steps` array, executed sequentially with 2-second delay between app-open and subsequent actions, with target window updated after each step.

---

## 12. Bugs Fixed (Audit Log)

### P0 — System-Breaking Fixes
| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `browser_agent.py` | Top-level `from playwright.sync_api import sync_playwright` crashes entire action system if Playwright not installed | Moved to lazy import inside `_ensure_page()` with try/except |
| 2 | `system_agent.py` | Top-level `import screen_brightness_control as sbc` — same cascade failure | Moved to lazy import inside `set_brightness()` and `get_brightness()` |
| 3 | `main.py` | `_execute_action()` decomposed compound steps, bypassing ActionRouter's 2s delay + window update | Now passes full compound intent to `ActionRouter.execute()` |
| 4 | `intent_parser.py` | `_generate_in_app_intent` regex only matched `\s+in\s+`, not "inside"/"into"/"on" | Changed to `\s+(?:in\|inside\|into\|on)\s+` |
| 5 | `intent_parser.py` | LLM fallback: `.format(command=command)` crashed on JSON braces in prompt template | Changed to `.replace("{command}", command)` |
| 6 | `browser_agent.py` | `_page` attribute name collision with `_page` method (prior session fix) | Renamed to `_current_page` / `_ensure_page()` |
| 7 | `App.jsx` | `playWakeChime` had syntax error (`ew` instead of `const ctx = new`) | Fixed syntax |
| 8 | `action_router.py` | Missing `_route()` method — routing logic was inline in `execute()` | Extracted into proper `_route()` method |
| 9 | `action_router.py` | Malformed `except` block (no body) | Fixed indentation + added `results.append()` body |

### P1 — Feature-Breaking Fixes
| # | File | Bug | Fix |
|---|------|-----|-----|
| 10 | `intent_parser.py` | "tell me a joke" matched as WhatsApp send to user "me" | Added pronoun guard (me/us/him/her/them/about) |
| 11 | `main.py` | `ACTION_PATTERNS` included "tell" — "tell me about..." triggered action pipeline | Split into separate pattern with pronoun negative lookahead |
| 12 | `intent_parser.py` | "search google for X" captured "google for X" as query | Added optional `(?:google\|bing\|youtube)\s+)?` stripping |
| 13 | `browser_agent.py` | Search/browse opened separate Chromium (no cookies/logins) via Playwright | Replaced with `webbrowser.open()` (user's default browser) |
| 14 | `messaging.py` | `send_whatsapp_by_phone()` Playwright CDP on localhost:9222 always failed | Replaced with `webbrowser.open()` + `pyautogui.press('enter')` |
| 15 | `intent_parser.py` | "write hello" matched `_generate_intent` before `_type_intent` | Removed "write" from generate pattern (only "generate"/"create"/"make") |
| 16 | `messaging.py` | `CONTACTS_FILE = "./contacts/contacts.json"` — relative path broke from different cwd | Changed to absolute path using `__file__` |
| 17 | `main.py` | False-positive action triggers: substring matching ("why is the door open?") | Replaced with 15 regex patterns requiring triggers at START of sentence |

### P2 — Quality Fixes
| # | File | Bug | Fix |
|---|------|-----|-----|
| 18 | `system_agent.py` | Clipboard destroyed by streaming LLM type | Save/restore clipboard around streaming |
| 19 | `system_agent.py` | `_is_generative` false positives: "search" and "hello world" as gen keywords | Removed "search" and "hello world" from `_GENERATE_KEYWORDS` |
| 20 | `main.py` | TTS temp MP3 files never cleaned up | Added `BackgroundTask(os.unlink, temp_path)` to FileResponse |
| 21 | `browser_agent.py` | Playwright browser processes leaked on shutdown | Added `cleanup()` method, called from server shutdown |
| 22 | `browser_agent.py` | `scroll` routed to Playwright (required Chromium) | Added `scroll()` method using `pyautogui.scroll()` |
| 23 | `voice/tts.py` | TTS race condition: `_playing` set after thread spawned | Fixed: `_playing = True` set before thread |
| 24 | `voice/stt.py` | VAD timeout before detecting speech (threshold too high) | Lowered threshold 0.40→0.30, trigger frames 3→2, initial timeout 6→8s |

---

> **Project Status:** Active development. Voice pipeline, action system, personality, memory, and all 7 executors are operational. 40+ system actions supported. Continuous improvement ongoing.
