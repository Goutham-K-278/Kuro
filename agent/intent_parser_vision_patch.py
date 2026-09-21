# agent/intent_parser_vision_patch.py
"""
Vision-aware intent patterns for Kuro.
Handles: send_message, play_media, read_screen, vision_click.

Called from IntentParser.parse() BEFORE the LLM fallback so these
fast regex paths skip the ~2s Ollama round-trip.
"""
import re
from typing import Optional, Dict, Any


# ── send_message patterns ────────────────────────────────────────────────────
# "send hi to Keerth"  /  "send a message to Mom saying I'm home"
_SEND_TO = re.compile(
    r"^(?:send|text)\s+(?:a\s+message\s+to\s+)?(.+?)\s+(?:saying|that)\s+(.+)$",
    re.IGNORECASE,
)
# "send hi to Keerth"  (short form — message then contact)
_SEND_SHORT = re.compile(
    r"^(?:send|text)\s+(.+?)\s+to\s+(.+?)$",
    re.IGNORECASE,
)
# "tell Keerth I'm on my way"  /  "tell Mom hi"
_TELL = re.compile(
    r"^tell\s+(.+?)\s+(?:that\s+)?(.+)$",
    re.IGNORECASE,
)

# ── play_media patterns ─────────────────────────────────────────────────────
# "play Starboy on Spotify"
_PLAY_ON_SPOTIFY = re.compile(
    r"^play\s+(.+?)\s+(?:on|in)\s+spotify$",
    re.IGNORECASE,
)
# "play blinding lights by the weeknd"
_PLAY_BY = re.compile(
    r"^play\s+(.+?)\s+by\s+(.+)$",
    re.IGNORECASE,
)
# "put on Bohemian Rhapsody"  /  "play Starboy"
_PLAY_GENERIC = re.compile(
    r"^(?:play|put\s+on|queue)\s+(.+)$",
    re.IGNORECASE,
)

# ── read_screen patterns ────────────────────────────────────────────────────
_READ_SCREEN = re.compile(
    r"^(?:what(?:'s|\s+is)\s+on\s+(?:my\s+)?screen|"
    r"read\s+(?:my\s+)?screen|describe\s+(?:my\s+)?screen|"
    r"what\s+(?:do\s+you|can\s+you)\s+see)$",
    re.IGNORECASE,
)

# ── vision_click patterns ───────────────────────────────────────────────────
_VISION_CLICK = re.compile(
    r"^(?:click|tap|press|hit|find\s+.+?\s+and\s+click)\s+(?:the\s+|on\s+(?:the\s+)?)?(.+?)(?:\s+button)?$",
    re.IGNORECASE,
)


# ── Pronoun / noise guard ───────────────────────────────────────────────────
_PRONOUN_GUARD = {"me", "us", "him", "her", "them", "it", "this", "that", "about"}


def parse_vision_intents(text: str) -> Optional[Dict[str, Any]]:
    """
    Try to match the input text against vision-related intent patterns.
    Returns an intent dict on match, or None if nothing matches.
    """
    text = text.strip()
    if not text:
        return None

    # ── send_message ─────────────────────────────────────────────────────
    # "send a message to Mom saying I'm home"
    m = _SEND_TO.match(text)
    if m:
        contact = m.group(1).strip()
        message = m.group(2).strip()
        if contact.lower() not in _PRONOUN_GUARD:
            return {
                "action": "send_message",
                "contact": contact,
                "message": message,
                "description": f"send '{message}' to {contact}",
                "risk_hint": "safe",
            }

    # "send hi to Keerth"
    m = _SEND_SHORT.match(text)
    if m:
        message = m.group(1).strip()
        contact = m.group(2).strip()
        if contact.lower() not in _PRONOUN_GUARD:
            return {
                "action": "send_message",
                "contact": contact,
                "message": message,
                "description": f"send '{message}' to {contact}",
                "risk_hint": "safe",
            }

    # "tell Keerth I'm on my way"
    m = _TELL.match(text)
    if m:
        contact = m.group(1).strip()
        message = m.group(2).strip()
        if contact.lower() not in _PRONOUN_GUARD:
            return {
                "action": "send_message",
                "contact": contact,
                "message": message,
                "description": f"tell {contact}: {message}",
                "risk_hint": "safe",
            }

    # ── play_media ───────────────────────────────────────────────────────
    # "play Starboy on Spotify"
    m = _PLAY_ON_SPOTIFY.match(text)
    if m:
        query = m.group(1).strip()
        return {
            "action": "play_media",
            "query": query,
            "description": f"play '{query}' on Spotify",
            "risk_hint": "safe",
        }

    # "play blinding lights by the weeknd"
    m = _PLAY_BY.match(text)
    if m:
        song = m.group(1).strip()
        artist = m.group(2).strip()
        query = f"{song} by {artist}"
        return {
            "action": "play_media",
            "query": query,
            "description": f"play '{song}' by {artist}",
            "risk_hint": "safe",
        }

    # "put on Bohemian Rhapsody"  /  "play Starboy"
    m = _PLAY_GENERIC.match(text)
    if m:
        query = m.group(1).strip()
        # Don't capture "play/pause" or bare "play" as media search
        if query.lower() not in ("pause", ""):
            return {
                "action": "play_media",
                "query": query,
                "description": f"play '{query}'",
                "risk_hint": "safe",
            }

    # ── read_screen ──────────────────────────────────────────────────────
    m = _READ_SCREEN.match(text)
    if m:
        return {
            "action": "read_screen",
            "description": "read screen contents",
            "risk_hint": "safe",
        }

    # ── vision_click ─────────────────────────────────────────────────────
    m = _VISION_CLICK.match(text)
    if m:
        target = m.group(1).strip()
        return {
            "action": "vision_click",
            "target": target,
            "description": f"click '{target}'",
            "risk_hint": "safe",
        }

    return None
