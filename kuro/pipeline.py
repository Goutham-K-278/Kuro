"""
kuro/pipeline.py
─────────────────────────────────────────────────────────────────
Kuro's Event-Driven Parallel Pipeline

Flow per voice command:
  Wake Word → STT → Groq (intent) → TTS + Action (parallel)
                                         ↕
                              OmniParser verify (background)

Key design:
  • TTS "On it..." fires SIMULTANEOUSLY with action execution
  • Host/App Agents handle action → no blocking pyautogui loops
  • WebSocket callback pushed to Electron frontend for real-time UI
  • Fully async — no blocking IO on the event loop
─────────────────────────────────────────────────────────────────
"""
import gc
import asyncio
import os

ACTION_TIMEOUT = float(os.getenv("ACTION_TIMEOUT_S", "5.0"))
import difflib
import logging
from typing import Callable, Optional

import numpy as np

from .agents.host_agent import HostAgent
from .utils.audio_pipeline import KuroWakeWordListener
from .utils.llm import KuroReasoner
from .utils.stt import StreamingSTT
from .utils.tts import StreamingTTS

logger = logging.getLogger(__name__)


class KuroPipeline:
    """
    Top-level pipeline orchestrator.  One instance lives for the lifetime
    of the FastAPI server process.

    Usage (in main.py lifespan):
        pipeline = KuroPipeline(ws_callback=broadcast_to_clients)
        asyncio.create_task(pipeline.start())
    """

    def __init__(self, ws_callback: Optional[Callable] = None):
        self._ws_cb = ws_callback   # async (event: dict) -> None
        self._running = False
        self._ready = False
        self._pending_confirmation: Optional[dict] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise components and enter the main command loop."""
        logger.info("[Pipeline] Initialising components...")

        self._stt  = StreamingSTT()
        self._llm  = KuroReasoner()
        self._tts  = StreamingTTS()
        self._host = HostAgent()
        self._wake = KuroWakeWordListener()

        # Pre-render all greeting phrases into RAM so the first greeting plays
        # in <50ms instead of the 4-5s cold-synthesis time.
        await self._tts.prewarm()

        # Pre-load Whisper model so first voice command doesn't pay the cold-start cost
        logger.info("[Pipeline] Pre-loading Whisper STT model...")
        try:
            from core.model_manager import model_manager
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: model_manager.get_sync("whisper"))
            logger.info("[Pipeline] Whisper model ready ✅")
        except Exception as e:
            logger.warning(f"[Pipeline] Whisper prewarm failed (will load on first use): {e}")

        self._running    = True
        self._ready      = True    # signal that pipeline is ready to accept commands
        self._first_wake = True    # first wake plays full greeting; after that short ack
        logger.info("[Pipeline] All components ready - waiting for wake word")
        await self._push_event("status", {"state": "idle", "message": "Kuro is listening"})
        loop = asyncio.get_event_loop()
        self._wake.start(loop)

        # Give the capture thread ~1.2s to finish its built-in ambient calibration
        # (calibration now runs inside _capture_loop to avoid pvrecorder thread-safety issues)
        await asyncio.sleep(1.2)

        while self._running:
            await self._command_cycle()

    def stop(self) -> None:
        """Cleanly shutdown the pipeline."""
        self._running = False
        if hasattr(self, "_wake"):
            self._wake.stop()
        if hasattr(self, "_tts"):
            self._tts.stop()
        logger.info("[Pipeline] Stopped")

    def interrupt(self) -> None:
        """Interrupts ongoing actions (TTS, etc.) and resets state."""
        try:
            if hasattr(self, "_tts"):
                self._tts.interrupt()
            
            # Drain audio queue to stop any queued listening
            if hasattr(self, "_wake"):
                self._wake.drain_and_arm()
                self._wake._direct_armed = False
                
            # Reset UI state
            try:
                loop = asyncio.get_running_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._push_event("state_change", {"state": "idle", "text": "Interrupted."}),
                        loop
                    )
            except RuntimeError:
                pass
                
            logger.info("[Pipeline] Interrupted by user.")
        except Exception as e:
            logger.error(f"[Pipeline] Error during interrupt: {e}")

    async def _get_vision_context(self) -> str:
        """Capture and analyze current screen for LLM context injection."""
        # BUG #4 FIX: Default is now "false" — screen capture only runs if explicitly enabled.
        if os.getenv("VISION_ENABLED", "false").lower() != "true":
            return ""

        try:
            from kuro.vision.scene_analyzer import SceneAnalyzer

            if not hasattr(self, "_scene_analyzer"):
                self._scene_analyzer = SceneAnalyzer()

            loop = asyncio.get_event_loop()
            state = await loop.run_in_executor(None, self._scene_analyzer.analyze)
            return state.to_llm_context()
        except Exception as e:
            logger.debug(f"[Pipeline] Vision context unavailable: {e}")
            return ""

    async def _capture_pre_action_state(self):
        """Capture pre-action state for post-action verification."""
        if os.getenv("VISION_POST_VERIFY", "true").lower() != "true":
            return None

        try:
            from kuro.vision.action_verifier import ActionVerifier

            if not hasattr(self, "_vision_verifier"):
                self._vision_verifier = ActionVerifier()

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._vision_verifier.capture_pre_state)
        except Exception as e:
            logger.debug(f"[Pipeline] Pre-action capture skipped: {e}")
            return None

    async def _verify_action(self, pre_state, expected_text: str = "") -> dict:
        """Verify action result by comparing before/after screen state."""
        if pre_state is None:
            return {"success": True, "description": "verification skipped"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._vision_verifier.verify(
                    pre_state=pre_state,
                    expected_text=expected_text,
                    timeout=2.0,
                ),
            )
            return result.to_dict()
        except Exception as e:
            logger.debug(f"[Pipeline] Verification skipped: {e}")
            return {"success": True, "description": f"verification error: {e}"}

    # ── Command Cycle ─────────────────────────────────────────────────────────

    async def _command_cycle(self) -> None:
        """
        Two-step command cycle:
          1. Wait for "Hey Kuro" → drain wake audio → greet
          2. Wait for user command (no wake word needed) → STT → Groq → Action
        """
        # Disarm any leftover direct-arm state from the previous cycle.
        # Without this, a timed-out Stage 2 leaves _direct_armed=True, causing
        # ambient noise to immediately re-fire Stage 1 on the next iteration —
        # producing 4-5 rapid greetings in a row.
        self._wake._direct_armed = False

        # -- Stage 1: Wait for wake word ---------------------------------------
        logger.info("[Pipeline] Waiting for wake word...")
        await self._push_event("status", {"state": "idle"})

        audio_gen = await self._wake.wait_for_command()   # blocks until wake word fires
        # Immediately emit the wake event → SSE → Node backend → Electron overlay
        await self._push_event("wake", {"source": "wakeword"})
        await self._push_event("status", {"state": "listening"})

        partials: list[str] = []

        async def on_partial(text: str) -> None:
            partials.append(text)
            await self._push_event("stt_partial", {"text": text})

        async def ui_wrapped_audio(gen):
            async for chunk in gen:
                yield chunk
            # The millisecond the user stops speaking, switch UI to "thinking"
            # so they know Kuro heard them and is processing the audio on the cloud.
            await self._push_event("status", {"state": "thinking"})

        try:
            from core.config import STT_TRANSCRIBE_TIMEOUT_S
            transcript = await asyncio.wait_for(
                self._stt.transcribe(ui_wrapped_audio(audio_gen), on_partial=on_partial),
                timeout=float(STT_TRANSCRIBE_TIMEOUT_S),
            )
        except asyncio.TimeoutError:
            self._wake._direct_armed = False
            logger.info("[Pipeline] STT timed out")
            await self._push_event("status", {"state": "idle"})
            return

        if not transcript.strip():
            # User woke the assistant but didn't speak a command immediately.
            # Play greeting and give them a second chance to speak.
            if self._first_wake:
                self._first_wake = False
                await self._push_event("status", {"state": "listening"})
                first_greeting = "Hey, I'm online. What do you need?"
                audio = self._tts._greeting_cache.get(first_greeting)
                if audio is not None:
                    import threading as _threading
                    done = _threading.Event()
                    self._tts._play_queue.put((audio, done))
                    await asyncio.get_event_loop().run_in_executor(None, done.wait)
                else:
                    await self._tts.speak(first_greeting)
            else:
                await self._push_event("status", {"state": "listening"})
                await self._tts.speak_greeting()

            await asyncio.sleep(0.3)
            self._wake.drain_and_arm()

            try:
                from core.config import STT_IDLE_TIMEOUT_S
                audio_gen2 = await asyncio.wait_for(
                    self._wake.wait_for_command(),
                    timeout=STT_IDLE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                self._wake._direct_armed = False
                await self._push_event("status", {"state": "idle", "message": "No speech — returning to idle"})
                return

            try:
                from core.config import STT_TRANSCRIBE_TIMEOUT_S
                transcript = await asyncio.wait_for(
                    self._stt.transcribe(ui_wrapped_audio(audio_gen2), on_partial=on_partial),
                    timeout=float(STT_TRANSCRIBE_TIMEOUT_S),
                )
            except asyncio.TimeoutError:
                self._wake._direct_armed = False
                await self._push_event("status", {"state": "idle"})
                return

            if not transcript.strip():
                self._wake._direct_armed = False
                logger.info("[Pipeline] Second attempt also empty — returning to idle")
                await self._push_event("status", {"state": "idle"})
                return

        logger.info(f"[Pipeline] Transcript: '{transcript}'")
        await self._push_event("stt_final", {"text": transcript})

        # -- Speaker verification (only runs if SPEAKER_VERIFY=true in .env) ---
        try:
            from voice.speaker_verify import is_enabled, is_owner_voice
            if is_enabled():
                # Collect buffered audio from STT for verification
                # audio_chunks were collected during transcription into partials list
                raw_audio = getattr(self._stt, "_last_audio", None)
                if raw_audio is not None and len(raw_audio) > 0:
                    is_owner, score = await asyncio.get_event_loop().run_in_executor(
                        None, is_owner_voice, raw_audio
                    )
                    if not is_owner:
                        logger.warning(
                            f"[Pipeline] Speaker rejected — score={score:.3f}. Ignoring command."
                        )
                        await self._push_event("status", {"state": "idle"})
                        return
        except Exception as _sve:
            logger.debug(f"[Pipeline] Speaker verify skipped: {_sve}")

        # -- Stage 3: Confirmation turn or Groq intent parsing -----------------
        await self._push_event("status", {"state": "thinking"})
        intent = self._resolve_pending_confirmation(transcript)
        if intent is None:
            vision_context = await self._get_vision_context()
            try:
                intent = await asyncio.wait_for(
                    self._llm.parse_intent(transcript, vision_context=vision_context),
                    timeout=60.0,  # cap intent parsing — 60s allows for slow local CPU LLMs
                )
            except asyncio.TimeoutError:
                logger.warning("[Pipeline] LLM parse_intent timed out (>60.0s)")
                intent = {
                    "tool": "general_response",
                    "args": {"response": "Sorry, I'm thinking slowly right now. Could you repeat that?"},
                    "speak_immediately": "Sorry, I'm thinking slowly right now. Could you repeat that?",
                }

        speak_phrase = intent.get("speak_immediately", "On it...")
        tool_name = intent["tool"]
        logger.info(f"[Pipeline] Intent: {tool_name}  speak: '{speak_phrase[:60]}'")

        # -- Stage 4: Parallel TTS + Action ------------------------------------
        # For conversational replies (general_response), the speak_phrase IS the
        # full answer and can be 2-4 sentences. Show a short UI label only.
        is_conversational = (tool_name == "general_response")

        # UI label: short phrase for overlay (not full conversational text)
        ui_phrase = speak_phrase if not is_conversational else "Kuro is talking..."
        await self._push_event("status", {
            "state":  "acting",
            "tool":   tool_name,
            "phrase": ui_phrase,
        })

        if is_conversational:
            # Conversational: use speak_stream for sentence-by-sentence TTS
            # No parallel action needed (general_response has no system action)
            await self._tts.speak_stream(speak_phrase)
            result = {"success": True, "message": "Responded"}
        else:
            # Action: parallel TTS acknowledgment + action execution
            pre_state = await self._capture_pre_action_state()
            speak_task  = asyncio.create_task(self._tts.speak(speak_phrase))
            action_task = asyncio.create_task(self._host.execute(intent))

            result = None
            try:
                _tts_done, result = await asyncio.gather(speak_task, action_task)
            except Exception as e:
                logger.error(f"[Pipeline] Cycle error: {e}")
                await self._tts.speak("Sorry, something went wrong.")
                pre_state = None

        if result:
            if not is_conversational:
                verification = await self._verify_action(pre_state=pre_state)
                result["vision_verified"] = verification.get("success", True)
                result["vision_description"] = verification.get("description", "")

            await self._push_event("action_result", result)
            await self._emit_app_badges(result)

            self._capture_confirmation_context(intent, result)

            follow_up = result.get("response_text", "")
            if follow_up and follow_up != speak_phrase:
                await self._tts.speak_stream(follow_up)

        await self._push_event("status", {"state": "idle"})

    # ── WebSocket Events ──────────────────────────────────────────────────────

    async def _push_event(self, event_type: str, payload: dict) -> None:
        """Send an event to all connected Electron WebSocket clients."""
        if self._ws_cb is None:
            return
        try:
            await self._ws_cb({"type": event_type, **payload})
        except Exception as e:
            logger.debug(f"[Pipeline] WS push error: {e}")

    async def _emit_app_badges(self, result: dict) -> None:
        """Emit compact app badge events for UI feedback."""
        if not isinstance(result, dict):
            return

        # For chained actions, emit one badge per executed step.
        steps = result.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                await self._push_step_badge(step)
            return

        await self._push_step_badge(result)

    async def _push_step_badge(self, step: dict) -> None:
        app = str(step.get("app") or step.get("tool") or "system").lower()
        ok = bool(step.get("success"))
        text = step.get("message") or step.get("error") or ("Done" if ok else "Failed")
        await self._push_event(
            "app_badge",
            {
                "app": app,
                "success": ok,
                "text": text,
            },
        )

    def _capture_confirmation_context(self, intent: dict, result: dict) -> None:
        """Capture ambiguous-contact context so the next user reply can resolve it."""
        if not isinstance(result, dict):
            return

        if result.get("needs_confirmation") and result.get("candidates"):
            msg = ""
            if isinstance(intent, dict):
                msg = str(intent.get("args", {}).get("message", ""))
            self._pending_confirmation = {
                "tool": "send_whatsapp_message",
                "candidates": list(result.get("candidates", [])),
                "message": msg,
            }
            return

        steps = result.get("steps")
        if not isinstance(steps, list):
            return

        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("needs_confirmation") and step.get("candidates"):
                msg = str(step.get("args", {}).get("message", ""))
                self._pending_confirmation = {
                    "tool": "send_whatsapp_message",
                    "candidates": list(step.get("candidates", [])),
                    "message": msg,
                }
                return

    def _resolve_pending_confirmation(self, transcript: str) -> Optional[dict]:
        pending = self._pending_confirmation
        if not pending:
            return None

        t_raw = (transcript or "").strip()
        t = t_raw.lower()
        candidates = [str(c) for c in pending.get("candidates", []) if c]
        if not candidates:
            self._pending_confirmation = None
            return None

        if t in {"no", "nope", "cancel", "stop", "none", "not this"}:
            self._pending_confirmation = None
            return {
                "tool": "general_response",
                "args": {"response": "Okay, canceled that message."},
                "speak_immediately": "Okay, canceled that message.",
            }

        chosen = None
        if t in {"yes", "yeah", "yep", "ok", "okay"}:
            chosen = candidates[0]
        elif t.isdigit():
            idx = int(t) - 1
            if 0 <= idx < len(candidates):
                chosen = candidates[idx]
        else:
            for c in candidates:
                c_low = c.lower()
                if t == c_low or t in c_low or c_low in t:
                    chosen = c
                    break
            if not chosen:
                best = max(candidates, key=lambda c: difflib.SequenceMatcher(None, t, c.lower()).ratio())
                if difflib.SequenceMatcher(None, t, best.lower()).ratio() >= 0.56:
                    chosen = best

        if not chosen:
            options = ", ".join([f"{i+1}. {c}" for i, c in enumerate(candidates[:3])])
            return {
                "tool": "general_response",
                "args": {"response": f"Please say the name or number. Options are {options}."},
                "speak_immediately": f"Please say the name or number. Options are {options}.",
            }

        self._pending_confirmation = None
        return {
            "tool": "send_whatsapp_message",
            "args": {
                "contact": chosen,
                "message": str(pending.get("message", "")),
            },
            "speak_immediately": f"Sending it to {chosen}.",
        }
