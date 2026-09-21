<div align="center">
  <img src="kuro-ai/frontend/public/kuro-icon.svg" width="120" height="120" alt="Kuro Icon" />
  <h1>Kuro-LLM</h1>
  <p><b>Ultra-Low Latency, Event-Driven Agentic Desktop Assistant</b></p>
  <p>
    <a href="#"><img src="https://img.shields.io/badge/Python-3.11+-blue.svg" alt="Python"></a>
    <a href="#"><img src="https://img.shields.io/badge/Electron-React-61dafb.svg" alt="Electron"></a>
    <a href="#"><img src="https://img.shields.io/badge/Latency-under_1.5s-brightgreen.svg" alt="Latency"></a>
    <a href="#"><img src="https://img.shields.io/badge/Status-Active-success.svg" alt="Status"></a>
  </p>
</div>

<br/>

Kuro is an advanced, unified Agentic Desktop Assistant designed for ultra-low latency voice interactions and dynamic system automation. By leveraging a high-performance Python backend and a sleek Electron frontend, Kuro combines the privacy and speed of local AI models with the extreme intelligence of cloud fallback capabilities.

---

## 🏗️ System Architecture

<div align="center">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 450" width="100%">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#8b949e" />
    </marker>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="5" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <g stroke="#8b949e" stroke-width="2" fill="none" marker-end="url(#arrow)">
    <!-- Main Pipeline Flow -->
    <path d="M 110 225 L 140 225" />
    <path d="M 290 225 L 330 225" />
    <path d="M 480 225 L 520 225" />
    <path d="M 600 110 L 600 180" />
    
    <!-- Parallel Outputs -->
    <path d="M 670 215 C 710 215, 710 145, 740 145" />
    <path d="M 670 235 C 710 235, 710 305, 740 305" />
    
    <!-- WebSocket Telemetry Flow -->
    <path d="M 410 250 L 410 320" />
    <path d="M 600 260 C 600 360, 510 360, 500 360" />
    <path d="M 810 330 C 810 420, 415 420, 415 400" />
  </g>

  <g font-family="Arial, sans-serif" font-size="14" font-weight="bold" text-anchor="middle">
    <!-- Audio In -->
    <rect x="20" y="200" width="90" height="50" rx="8" fill="#1f2428" stroke="#30363d" stroke-width="2" />
    <text x="65" y="230" fill="#c9d1d9">Audio In</text>

    <!-- Wake Word -->
    <rect x="150" y="200" width="140" height="50" rx="8" fill="#1f2428" stroke="#58a6ff" stroke-width="2" />
    <text x="220" y="225" fill="#58a6ff">Wake Word</text>
    <text x="220" y="240" font-size="11" font-weight="normal" fill="#8b949e">OpenWakeWord</text>

    <!-- STT -->
    <rect x="340" y="200" width="140" height="50" rx="8" fill="#1f2428" stroke="#3fb950" stroke-width="2" />
    <text x="410" y="225" fill="#3fb950">Active STT</text>
    <text x="410" y="240" font-size="11" font-weight="normal" fill="#8b949e">Sherpa-ONNX</text>

    <!-- LLM Engine -->
    <rect x="530" y="190" width="140" height="70" rx="8" fill="#1f2428" stroke="#d2a8ff" stroke-width="2" filter="url(#glow)" />
    <text x="600" y="225" fill="#d2a8ff">LLM Engine</text>
    <text x="600" y="242" font-size="11" font-weight="normal" fill="#8b949e">Groq / Local</text>

    <!-- Vision Context -->
    <rect x="530" y="60" width="140" height="50" rx="8" fill="#1f2428" stroke="#f0883e" stroke-width="2" />
    <text x="600" y="85" fill="#f0883e">Vision Context</text>
    <text x="600" y="100" font-size="11" font-weight="normal" fill="#8b949e">Florence-2</text>

    <!-- TTS -->
    <rect x="750" y="120" width="120" height="50" rx="8" fill="#1f2428" stroke="#ff7b72" stroke-width="2" />
    <text x="810" y="145" fill="#ff7b72">Parallel TTS</text>
    <text x="810" y="160" font-size="11" font-weight="normal" fill="#8b949e">Kokoro / Piper</text>

    <!-- Action Agent -->
    <rect x="750" y="280" width="120" height="50" rx="8" fill="#1f2428" stroke="#79c0ff" stroke-width="2" />
    <text x="810" y="305" fill="#79c0ff">Host Agent</text>
    <text x="810" y="320" font-size="11" font-weight="normal" fill="#8b949e">UI Automation</text>

    <!-- WebSocket UI -->
    <rect x="340" y="330" width="150" height="60" rx="8" fill="#1f2428" stroke="#a371f7" stroke-width="2" stroke-dasharray="4" />
    <text x="415" y="355" fill="#a371f7">Electron UI</text>
    <text x="415" y="372" font-size="11" font-weight="normal" fill="#8b949e">WebSocket Overlay</text>
  </g>
</svg>
</div>

---

## ⚡ 4-Stage Execution Pipeline

Kuro operates on a highly optimized, fully async event loop designed to reduce end-to-end response times below 1.5 seconds. 

1. **Passive Wake Word & Greeting**  
   The system passively listens using `OpenWakeWord`. Upon detecting "Hey Kuro", it immediately drains the audio buffer and synthesizes a randomized sub-50ms verbal acknowledgment (e.g., *"Yeah?"*).
2. **Active STT Listening**  
   The microphone instantly arms. `Sherpa-ONNX` (streaming Zipformer ASR) captures the user's command, emitting 50ms partial transcripts directly to the Electron frontend for live subtitle rendering.
3. **Intent Parsing & Vision Context**  
   The final transcript (and optional `Florence-2` visual screen context) is routed through the fast reasoning engine (`Groq LPU` or `Ollama local`). The engine isolates exact user intent and formulates a structured execution payload.
4. **Parallel Execution (TTS + Action)**  
   To entirely mask latency, two async tasks spin up simultaneously: 
   - **Voice Acknowledgment**: TTS synthesizes a brief confirmation phrase (e.g., *"On it..."*).
   - **Agent Execution**: The `HostAgent` or `ActionRouter` dynamically controls the desktop utilizing `uiautomation` and `pywinauto`.

## 🛠️ Technology Stack

- **Frontend Environment**: `Electron`, `React`, `Node.js`.
- **Backend API Core**: `Python 3`, `FastAPI`, `Uvicorn`, `WebSockets`.
- **Large Language Models**: `Groq API` (Llama-3), `Ollama` (Local processing).
- **Audio Processing**: `OpenWakeWord`, `Sherpa-ONNX`, `Kokoro-onnx`, `Piper-TTS`.
- **Desktop Automation**: `uiautomation`, `pywinauto`, `mss` (Vision screenshots).

## 🚀 Getting Started

Ensure you have Python 3.11+ and Node.js installed.

1. **Clone and Configure**
   ```bash
   git clone https://github.com/Goutham-K-278/Kuro.git
   cd Kuro
   cp .env.example .env
   # Add your GROQ_API_KEY and Spotify credentials to the .env file
   ```

2. **Start Backend (Python API)**
   ```powershell
   ./scripts/dev.ps1
   ```
   *(Starts FastAPI server and loads ONNX models into memory).*

3. **Start Frontend (Electron UI)**
   ```bash
   cd kuro-ai
   npm install
   cd desktop && npm start
   ```

**Enjoy lightning-fast agentic assistance directly from your desktop overlay!**
