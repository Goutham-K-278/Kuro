"""
KURO — FastAPI Brain API Server v2
===================================
Unified AI pipeline with:
  • Porcupine v4 wake word detection
  • Sherpa-ONNX streaming STT (INT8, 50ms partials)
  • Groq Llama-3.1-8b intent parsing with tool forcing
  • Kokoro-ONNX streaming TTS (100ms latency)
  • HostAgent + AppAgents (WhatsApp, Spotify) with UFO2 desktop automation
  • OmniParser V2 observation loop for action verification
  • Two-phase greeting system
  • Full pre-warming in lifespan

Runs on port 8000. Electron desktop connects via WebSocket /ws.

Start:  python main.py
        OR  .\\dev.ps1  (full-stack: Python + Electron)

Endpoints
─────────
GET  /health              — readiness probe (pipeline running)
GET  /ws                  — WebSocket: real-time pipeline events (TO ELECTRON)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Callable, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from kuro.pipeline import KuroPipeline

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
import sys as _sys
_log_handler = logging.StreamHandler(_sys.stdout)
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
if hasattr(_sys.stdout, 'reconfigure'):
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])
logger = logging.getLogger("kuro")

# ── Set HIGH process priority (Windows only) ─────────────────────────────────
def _set_high_priority():
    """
    Set HIGH process priority and pin to P-cores on i5-12500H.
    P-cores (0-7) handle Kuro's main pipeline for minimum latency.
    E-cores (8-15) are left free for background tasks (DeepFilterNet, etc.).
    """
    if sys.platform == "win32":
        try:
            import win32api
            import win32con
            import win32process

            handle = win32api.OpenProcess(
                win32con.PROCESS_ALL_ACCESS, True, os.getpid()
            )
            win32process.SetPriorityClass(handle, win32process.HIGH_PRIORITY_CLASS)
            logger.info("Process priority set to HIGH_PRIORITY_CLASS")
        except Exception as e:
            logger.warning(f"Could not set process priority: {e}")

        # Pin to P-cores (0-7 on i5-12500H) for minimum scheduling jitter
        try:
            import psutil
            p = psutil.Process(os.getpid())
            cpu_count = psutil.cpu_count(logical=True)
            # Pin to the first 8 logical CPUs (P-cores on 12th-gen Intel)
            p_cores = list(range(min(8, cpu_count)))
            p.cpu_affinity(p_cores)
            logger.info(f"Process pinned to P-cores: {p_cores}")
        except Exception as e:
            logger.warning(f"Could not set CPU affinity: {e}")

_set_high_priority()

# ── Global state ──────────────────────────────────────────────────────────────
pipeline: KuroPipeline | None = None
_ws_connections: set[WebSocket] = set()
_tts_lock: asyncio.Lock | None = None   # Single TTS lock — prevents double-voice

# SSE fan-out: each connected Electron client gets its own asyncio.Queue.
# When the wake word fires, we push 'data: wake\n\n' to every queue.
_sse_clients: set[asyncio.Queue] = set()


async def _broadcast(data: dict) -> None:
    """Send event to all connected WebSocket clients."""
    dead = set()
    for ws in _ws_connections:
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    _ws_connections.difference_update(dead)


async def _ws_callback(data: dict) -> None:
    """
    Async callback from KuroPipeline._push_event().
    Broadcasts events to all connected WebSocket clients AND the SSE wake stream.
    """
    if _ws_connections:
        try:
            await _broadcast(data)
        except Exception as e:
            logger.debug(f"WS broadcast error: {e}")

    # Forward wake events to all SSE clients (Electron overlay trigger)
    event_type = data.get("type", "")
    if event_type == "wake":
        logger.info("[SSE] Broadcasting 'data: wake' to %d client(s)", len(_sse_clients))
        dead = set()
        for q in _sse_clients:
            try:
                q.put_nowait("data: wake\n\n")
            except asyncio.QueueFull:
                dead.add(q)
        _sse_clients.difference_update(dead)


# ── Lifespan: pre-warm all models ─────────────────────────────────────────────

async def _ping_ollama() -> bool:
    import aiohttp
    url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags"
    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status == 200
    except Exception:
        return False

async def _ping_omniparser() -> bool:
    import aiohttp
    host = os.getenv("OMNIPARSER_HOST", "127.0.0.1")
    port = os.getenv("OMNIPARSER_PORT", "8001")
    url  = f"http://{host}:{port}/"
    try:
        timeout = aiohttp.ClientTimeout(total=2.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                return resp.status < 500
    except Exception:
        return False

def _check_cuda() -> bool:
    from core.gpu_detect import GPU_AVAILABLE
    return GPU_AVAILABLE

async def print_startup_status():
    from pathlib import Path
    from core.gpu_detect import GPU_AVAILABLE, status_line as _gpu_status
    checks = {
        "OpenWakeWord": True,  # Always available — no API key needed
        "Groq API key":        bool(os.getenv("GROQ_API_KEY")),
        "Ollama running":      await _ping_ollama(),
        "Kokoro TTS model":    Path("models/kokoro-v1.0.onnx").exists(),
        "Contacts file":       Path("contacts/contacts.json").exists(),
        "CUDA GPU":            GPU_AVAILABLE,
        "OmniParser":          await _ping_omniparser(),
    }

    logger.info("=" * 50)
    logger.info("  KURO STARTUP STATUS")
    logger.info("=" * 50)
    logger.info(f"  {_gpu_status()}")
    logger.info("-" * 50)
    for name, ok in checks.items():
        icon = "OK" if ok else "--"
        logger.info(f"  [{icon}]  {name}")
    logger.info("=" * 50)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Pre-warming strategy:
      • PowerModeWatcher polls power scheme every 60 s — config is always live
      • Load all ONNX models into VRAM/RAM during startup
      • Pipeline.start() runs async and waits in wake-word loop
      • KuroPipeline stays running throughout FastAPI lifecycle
    """
    global pipeline
    from core.power_mode import PowerModeWatcher, get_current_config
    from core.model_manager import model_manager

    global _tts_lock
    _tts_lock = asyncio.Lock()   # Created inside the event loop — safe for async use

    # ── Start dynamic power mode watcher (replaces one-shot PowerAwareConfig.get) ──
    _power_watcher = PowerModeWatcher(interval_s=60.0)
    asyncio.create_task(_power_watcher.run())

    config = get_current_config()
    logger.info(f"[Startup] Initial power config: {config}")

    await print_startup_status()

    logger.info("Pre-warming Kuro models...")

    # ── Register models with VRAM-gated manager ────────────────────────────────
    # Factories are lambdas — nothing loads yet, only on first get_sync() call.
    # Registering here means STT and TTS constructors will find "whisper"/"kokoro"
    # in the registry and go through the Semaphore gate automatically.
    from core.config import (
        WHISPER_MODEL,
        STT_DEVICE,
        KOKORO_VOICE,
        VISION_ENABLED,
        TURBOQUANT_ENABLED,
        TURBOQUANT_MODE,
    )

    if TURBOQUANT_ENABLED and TURBOQUANT_MODE != "off":
        try:
            import turboquant  # noqa: F401
            logger.info("[Startup] turboquant package detected (Python-side support available)")
        except Exception:
            logger.warning("[Startup] turboquant package not available")
        logger.info(
            "[Startup] Ollama KV cache tuning is controlled via OLLAMA_KV_CACHE_TYPE (current env may use q8_0)"
        )

    def _make_whisper():
        from faster_whisper import WhisperModel
        import numpy as np
        from core.gpu_detect import effective_device, compute_type_for_device
        # Auto-detect: if GPU is off/disabled, silently falls back to CPU
        device  = effective_device(STT_DEVICE)
        compute = compute_type_for_device(device)
        if device == "cuda":
            try:
                model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type=compute)
                # Validate cuBLAS works before returning
                list(model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1)[0])
                logger.info(f"[ModelManager] Whisper ready: {WHISPER_MODEL} on cuda ({compute})")
                return model
            except Exception as e:
                logger.warning(f"[ModelManager] Whisper CUDA failed ({e}) — falling back to CPU")
        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        logger.info(f"[ModelManager] Whisper ready: {WHISPER_MODEL} on cpu (int8)")
        return model

    def _make_kokoro():
        from kokoro_onnx import Kokoro
        from pathlib import Path
        
        _root = Path(__file__).parent
        _model_path  = str(_root / "models" / "kokoro-v1.0.onnx")
        _voices_path = str(_root / "models" / "voices-v1.0.bin")
        model = Kokoro(_model_path, _voices_path)
        logger.info("[ModelManager] Kokoro ready (kokoro-v1.0.onnx)")
        return model

    model_manager.register("whisper", _make_whisper)
    model_manager.register("kokoro",  _make_kokoro)

    def _make_florence():
        from kuro.vision.florence_engine import FlorenceEngine

        engine = FlorenceEngine.get_instance()
        engine.ensure_loaded()
        logger.info("[ModelManager] Florence-2 ready")
        return engine

    if VISION_ENABLED:
        model_manager.register("florence", _make_florence)
        logger.info("[Startup] Models registered: whisper, kokoro, florence")
    else:
        logger.info("[Startup] Models registered: whisper, kokoro")

    # Initialize pipeline (STT/TTS constructors will now use model_manager)
    
    REQUIRED_ENV = {
        "GROQ_API_KEY": "STT + LLM (required unless STT_BACKEND=local and LLM_BACKEND=local)",
    }
    
    def validate_env():
        missing = []
        for key, purpose in REQUIRED_ENV.items():
            if not os.getenv(key, "").strip():
                missing.append(f"  {key} — {purpose}")
        if missing:
            logger.warning("Missing optional env vars (some features may degrade):\n" + "\n".join(missing))
    
    validate_env()
    
    pipeline = KuroPipeline(ws_callback=_ws_callback)

    # Pre-warm Kokoro TTS so the first response isn't delayed by 6 seconds
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: model_manager.get_sync("kokoro"))
        logger.info("[Startup] Kokoro TTS pre-loaded into memory")
    except Exception as e:
        logger.warning(f"[Startup] Kokoro prewarm failed: {e}")

    # Pre-warm Ollama local model so intent parsing is instant
    if os.getenv("LLM_BACKEND", "local") != "groq":
        try:
            import httpx
            from core.config import LOCAL_MODEL
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "http://localhost:11434/api/chat",
                    json={"model": LOCAL_MODEL, "messages": [{"role": "user", "content": "wake up"}]}
                )
            logger.info(f"[Startup] Ollama model ({LOCAL_MODEL}) pre-loaded into memory")
        except Exception as e:
            logger.warning(f"[Startup] Ollama prewarm failed: {e}")

    # Optional vision prewarm. If this fails, pipeline continues in voice-only mode.
    if VISION_ENABLED:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: model_manager.get_sync("florence"))
            logger.info("[Startup] Florence-2 pre-loaded")
        except Exception as e:
            logger.warning(f"[Startup] Florence prewarm failed: {e}")

    # Launch pipeline in a background task (event loop stays available for endpoints)
    pipeline_task = asyncio.create_task(pipeline.start())

    # Background task: reclaim VRAM from idle models every 2 minutes
    async def gc_loop():
        while True:
            await asyncio.sleep(120)
            await model_manager.unload_idle()
    asyncio.create_task(gc_loop())

    logger.info("All models loaded. Kuro is ready.")
    yield

    # Shutdown
    logger.info("Shutting down Kuro...")
    _power_watcher.stop()
    pipeline.stop()
    try:
        if pipeline._host and hasattr(pipeline._host, '_omni'):
            await pipeline._host._omni.close()
    except Exception as e:
        logger.warning(f"Failed to close OmniParserClient: {e}")
    try:
        await asyncio.wait_for(pipeline_task, timeout=2.0)
    except asyncio.TimeoutError:
        logger.warning("Pipeline task did not stop cleanly within 2s")


# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Kuro Agentic Desktop Assistant",
    version="2.0.0",
    description="Unified AI pipeline with offline STT/TTS and desktop automation",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Node.js backend
        "http://localhost:8000",  # Direct access
        "http://127.0.0.1:8000",
        "app://.",                # Electron protocol
        "file://",                # file:// protocol
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Readiness probe — reports if pipeline is initialized."""
    return {
        "status": "running",
        "ready": pipeline is not None and getattr(pipeline, '_ready', False),
        "pipeline_active": pipeline is not None,
    }


@app.get("/wake/events")
async def wake_events():
    """
    Real SSE endpoint — streams 'data: wake' to Electron whenever the
    wake word is detected. One asyncio.Queue per connected client.
    """
    from fastapi.responses import StreamingResponse

    q: asyncio.Queue = asyncio.Queue(maxsize=16)
    _sse_clients.add(q)
    logger.info("[SSE] Client connected (%d total)", len(_sse_clients))

    async def generator():
        try:
            # Send an immediate heartbeat so the client knows the stream is live
            yield ": connected\n\n"
            while True:
                try:
                    # Wait for next event with a 20s heartbeat timeout
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield msg
                except asyncio.TimeoutError:
                    # Heartbeat keeps the connection alive through proxies
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_clients.discard(q)
            logger.info("[SSE] Client disconnected (%d remaining)", len(_sse_clients))

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/wake/stop")
async def wake_stop():
    """Stop any active listening session and interrupt TTS/pipeline."""
    if pipeline is not None:
        if hasattr(pipeline, "interrupt"):
            pipeline.interrupt()
        elif hasattr(pipeline, "_wake"):
            pipeline._wake.drain_and_arm()
            pipeline._wake._direct_armed = False
    return {"status": "ok"}


@app.post("/wake/trigger")
async def wake_trigger():
    """Manual trigger endpoint — wakes the pipeline as if the wake word was spoken."""
    if pipeline is None or not pipeline._running:
        return {"status": "pipeline_not_ready"}
    
    try:
        pipeline._wake._direct_armed = True
        pipeline._wake._trigger_wake()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Trigger error: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/tts/speak")
async def tts_speak(request: dict):
    """TTS endpoint — synthesize and play text. Protected by _tts_lock so ONLY one voice plays at a time."""
    if pipeline is None:
        return {"status": "pipeline_not_ready"}

    text = request.get("text", "").strip()
    if not text:
        return {"status": "empty_text"}

    try:
        # Acquire the global TTS lock — if already speaking, wait (don't overlap)
        async with _tts_lock:
            await pipeline._tts.speak(text)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/chat")
async def chat(request: dict):
    """
    Chat endpoint — accepts a message, runs Groq intent parsing,
    executes the action via HostAgent, and returns the result.
    Used by the Node.js backend proxy (/api/chat).
    """
    if pipeline is None or not getattr(pipeline, '_ready', False):
        return {"response": "Kuro is still starting up. Try again in a moment.", "model": "kuro-brain"}

    message = (request.get("message") or "").strip()
    if not message:
        return {"response": "No message provided.", "model": "kuro-brain"}

    try:
        intent = await pipeline._llm.parse_intent(message)
        tool = intent.get("tool", "general_response")

        if tool == "general_response":
            text = intent.get("args", {}).get("response", "")
            return {"response": text, "model": "kuro-brain", "is_action": False}

        already_spoken = False
        speak_text = intent.get("speak_immediately")
        if speak_text:
            # Dispatch TTS early so audio plays while we run the desktop automation
            asyncio.create_task(pipeline._tts.speak(speak_text))
            already_spoken = True

        result = await pipeline._host.execute(intent)
        response_text = (
            result.get("response_text")
            or speak_text or "Done."
        )
        return {
            "response": response_text,
            "model": "kuro-brain",
            "is_action": True,
            "tool": tool,
            "result": result,
            "already_spoken": already_spoken
        }
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return {"response": f"Sorry, something went wrong: {e}", "model": "kuro-brain"}


# ── LLM Backend Mode Toggle ──────────────────────────────────────────────────

@app.get("/api/llm-mode")
async def get_llm_mode():
    """Returns current LLM backend: mode (auto/groq/local), active backend, override flag."""
    from core.model_router import get_backend_status, get_llm_backend
    active = await get_llm_backend()
    status = get_backend_status()
    status["active"] = active.value
    return status


@app.post("/api/llm-mode")
async def set_llm_mode(request: dict):
    """Pin or release the LLM backend. Body: {\"mode\": \"auto\" | \"groq\" | \"local\"}"""
    from core.model_router import set_backend_override, LLMBackend, get_backend_status
    mode = (request.get("mode") or "").lower().strip()
    if mode == "auto":
        set_backend_override(None)
    elif mode == "groq":
        set_backend_override(LLMBackend.GROQ)
    elif mode == "local":
        set_backend_override(LLMBackend.LOCAL)
    else:
        return {"error": f"Unknown mode '{mode}'. Valid values: auto, groq, local"}
    return get_backend_status()


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time pipeline events to Electron frontend.
    
    Event types emitted:
      • status: {"state": "idle"|"listening"|"thinking"|"acting", ...}
      • stt_partial: {"text": "...partial transcript..."}
      • stt_final: {"text": "...final transcript..."}
      • action_result: {"tool": "...", "success": bool, ...}
    """
    await websocket.accept()
    _ws_connections.add(websocket)
    logger.info(f"[WS] Client connected — {len(_ws_connections)} active")

    try:
        while True:
            # Keep connection alive; receive heartbeats if any
            data = await websocket.receive_text()
            logger.debug(f"[WS] Received: {data}")
    except WebSocketDisconnect:
        _ws_connections.discard(websocket)
        logger.info(f"[WS] Client disconnected — {len(_ws_connections)} active")
    except Exception as e:
        logger.error(f"[WS] Error: {e}")
        _ws_connections.discard(websocket)


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
        workers=1,  # Single worker for task completion
    )

