# agent/intent_parser.py
import warnings
warnings.warn(
    "agent/intent_parser.py is deprecated. Use fast-path regex in kuro/utils/llm.py instead. "
    "This module will be removed in Phase 6.",
    DeprecationWarning,
    stacklevel=2
)
import json, re
from typing import Optional, Dict, Any

try:
    import ollama as _ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    _ollama = None
    OLLAMA_AVAILABLE = False

# ── Bug 6 Fix — Verb Synonyms (normalize before regex matching) ──────────────

VERB_SYNONYMS: Dict[str, list] = {
    "open":  ["open", "launch", "start", "pull up", "bring up",
              "load", "boot", "run", "fire up", "start up",
              # View synonyms that mean "show/open the app"
              "show me", "show", "display", "view", "see",
              "look at", "check", "preview"],
    "find":  ["find", "search", "look for", "locate", "get",
              "navigate to", "go to"],
    "close": ["close", "quit", "exit", "kill", "shut", "stop"],
    "send":  ["send", "message", "text", "ping", "tell", "dm",
              "write to", "say to"],
}


def _normalize_verb(text: str) -> str:
    """Replace synonym verbs with canonical verb for regex matching."""
    text_lower = text.lower().strip()
    for canonical, synonyms in VERB_SYNONYMS.items():
        # Sort by length descending so 'pull up' matches before 'pull'
        for syn in sorted(synonyms, key=len, reverse=True):
            if text_lower.startswith(syn + " ") or text_lower == syn:
                return canonical + text_lower[len(syn):]
    return text_lower


# ── Bug 5 Fix — Platform Keywords (extract messaging platform from text) ───────

PLATFORM_KEYWORDS: Dict[str, list] = {
    "whatsapp": ["whatsapp", "wa", "wp"],
    "discord":  ["discord", "dc"],
    "telegram": ["telegram", "tg"],
    "teams":    ["teams", "microsoft teams"],
    "slack":    ["slack"],
}


def _extract_platform(text: str) -> str:
    """Return platform name from text. Defaults to whatsapp."""
    text_lower = text.lower()
    for platform, keywords in PLATFORM_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return platform
    return "whatsapp"   # safe default

# ── Navigation regex patterns (checked FIRST — most specific) ──────────────
# NAV 1: "go to Goutham's chat on WhatsApp"
_NAV_CHAT_ON_APP = re.compile(
    r"^(?:go\s+to|navigate\s+to|open)\s+"
    r"(.+?)(?:'s)?\s+(?:chat|conversation|dm|messages?)\s+"
    r"(?:on|in)\s+(\w+)\s*$",
    re.IGNORECASE,
)

# NAV 2: "go to Goutham's chat" — no app specified, auto-detect
_NAV_CHAT_DEFAULT = re.compile(
    r"^(?:go\s+to|navigate\s+to|open)\s+"
    r"(.+?)(?:'s)?\s+(?:chat|conversation|dm|messages?)\s*$",
    re.IGNORECASE,
)

# NAV 3: "go to Goutham on WhatsApp" — no "chat" keyword
_NAV_CONTACT_ON_APP = re.compile(
    r"^(?:go\s+to|navigate\s+to|switch\s+to|jump\s+to)\s+"
    r"(.+?)\s+(?:on|in)\s+(whatsapp|telegram|discord|slack|teams)\s*$",
    re.IGNORECASE,
)

# NAV 4: "switch to #general channel on Discord"
_NAV_CHANNEL = re.compile(
    r"^(?:go\s+to|navigate\s+to|switch\s+to|jump\s+to)\s+"
    r"#?(.+?)\s+(?:channel|server)\s*(?:(?:on|in)\s+(\w+))?\s*$",
    re.IGNORECASE,
)

# NAV 5: "go to youtube.com" / "navigate to https://..."
_NAV_URL = re.compile(
    r"^(?:go\s+to|navigate\s+to|visit|browse\s+to)\s+"
    r"((?:https?://|www\.)\S+)\s*$",
    re.IGNORECASE,
)

# NAV 6: "go to downloads" / "go to documents folder"
_NAV_FOLDER = re.compile(
    r"^(?:go\s+to|navigate\s+to|open)\s+"
    r"(downloads|documents|desktop|pictures|music|videos|appdata|temp)"
    r"(?:\s+folder)?\s*$",
    re.IGNORECASE,
)

# NAV 7: "switch to Chrome" / "switch to VSCode"
_NAV_SWITCH = re.compile(
    r"^switch\s+to\s+(.+?)(?:\s+(?:tab|window|app|channel))?\s*$",
    re.IGNORECASE,
)

# NAV 8: Generic "go to X" (catch-all — MUST be last navigation pattern)
_NAV_GENERIC = re.compile(
    r"^(?:go\s+to|navigate\s+to|jump\s+to)\s+(.+)\s*$",
    re.IGNORECASE,
)


# ── Navigation builder methods ─────────────────────────────────────────────

def _parse_nav_chat_on_app(m) -> Dict[str, Any]:
    """'go to Goutham's chat on WhatsApp'"""
    contact = m.group(1).strip()
    app = m.group(2).strip().lower()
    return {"action": "navigate", "app": app, "target": contact,
            "content": None, "params": {"type": "chat"}, "risk_hint": "safe",
            "description": f"Open {contact}'s chat on {app}", "steps": None}


def _parse_nav_chat_default(m) -> Dict[str, Any]:
    """'go to Goutham's chat' — no app specified"""
    contact = m.group(1).strip()
    return {"action": "navigate", "app": None, "target": contact,
            "content": None, "params": {"type": "chat"}, "risk_hint": "safe",
            "description": f"Open {contact}'s chat", "steps": None}


def _parse_nav_contact_on_app(m) -> Dict[str, Any]:
    """'go to Goutham on WhatsApp'"""
    contact = m.group(1).strip()
    app = m.group(2).strip().lower()
    return {"action": "navigate", "app": app, "target": contact,
            "content": None, "params": {"type": "contact"}, "risk_hint": "safe",
            "description": f"Navigate to {contact} on {app}", "steps": None}


def _parse_nav_channel(m) -> Dict[str, Any]:
    """'go to #general on Discord'"""
    channel = m.group(1).strip()
    app = (m.group(2) or "discord").strip().lower()
    return {"action": "navigate", "app": app, "target": channel,
            "content": None, "params": {"type": "channel"}, "risk_hint": "safe",
            "description": f"Go to #{channel} on {app}", "steps": None}


def _parse_nav_url(m) -> Dict[str, Any]:
    """'go to youtube.com'"""
    url = m.group(1).strip()
    return {"action": "navigate", "app": "chrome", "target": url,
            "content": None, "params": {"type": "url"}, "risk_hint": "safe",
            "description": f"Navigate to {url}", "steps": None}


def _parse_nav_folder(m) -> Dict[str, Any]:
    """'go to downloads folder'"""
    folder = m.group(1).strip()
    return {"action": "navigate", "app": "explorer", "target": folder,
            "content": None, "params": {"type": "file"}, "risk_hint": "safe",
            "description": f"Open {folder} folder", "steps": None}


def _parse_nav_switch(m) -> Dict[str, Any]:
    """'switch to Chrome' / 'switch to Discord'"""
    target = m.group(1).strip()
    return {"action": "navigate", "app": None, "target": target,
            "content": None, "params": {"type": "switch"}, "risk_hint": "safe",
            "description": f"Switch to {target}", "steps": None}


def _parse_nav_generic(m) -> Dict[str, Any]:
    """'go to X' — catch-all navigation"""
    target = m.group(1).strip()
    return {"action": "navigate", "app": None, "target": target,
            "content": None, "params": {"type": "generic"}, "risk_hint": "safe",
            "description": f"Navigate to {target}", "steps": None}


# ── Timer builder methods ─────────────────────────────────────────────────

def _parse_timer_set(m) -> Dict[str, Any]:
    """'set a timer for 5 minutes'"""
    duration = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else "5 minutes"
    return {"action": "timer_set", "app": None, "target": duration,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": f"Set timer for {duration}", "steps": None}


def _parse_timer_cancel(m) -> Dict[str, Any]:
    """'cancel timer'"""
    return {"action": "timer_cancel", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Cancel timer", "steps": None}


def _parse_timer_status(m) -> Dict[str, Any]:
    """'how much time is left'"""
    return {"action": "timer_status", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Check timer status", "steps": None}


# ── Media builder methods ─────────────────────────────────────────────────

def _parse_media_playpause(m) -> Dict[str, Any]:
    """'pause' / 'play' / 'resume'"""
    return {"action": "media_playpause", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Toggle play/pause", "steps": None}


def _parse_media_next(m) -> Dict[str, Any]:
    """'next track' / 'skip'"""
    return {"action": "media_next", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Next track", "steps": None}


def _parse_media_prev(m) -> Dict[str, Any]:
    """'previous track' / 'go back'"""
    return {"action": "media_prev", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Previous track", "steps": None}


# ── WiFi/Bluetooth builder methods ────────────────────────────────────────

def _parse_wifi_on(m) -> Dict[str, Any]:
    return {"action": "wifi_on", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Enable WiFi", "steps": None}


def _parse_wifi_off(m) -> Dict[str, Any]:
    return {"action": "wifi_off", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Disable WiFi", "steps": None}


def _parse_bt_on(m) -> Dict[str, Any]:
    return {"action": "bt_on", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Enable Bluetooth", "steps": None}


def _parse_bt_off(m) -> Dict[str, Any]:
    return {"action": "bt_off", "app": None, "target": None,
            "content": None, "params": {}, "risk_hint": "safe",
            "description": "Disable Bluetooth", "steps": None}


# ── Existing builder functions ─────────────────────────────────────────────
# ── Regex fast-path patterns (skip LLM for obvious commands) ──────────────
# Each tuple: (compiled regex, builder function that returns intent dict)
def _open_intent(m):
	app = m.group(1).strip().lower()
	return {"action":"open","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"open {app}","steps":None}

def _close_intent(m):
	app = m.group(1).strip().lower()
	return {"action":"close","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"close {app}","steps":None}

def _type_intent(m):
	text = m.group(1).strip()
	return {"action":"type","app":None,"target":None,"content":text,"params":{},"risk_hint":"safe","description":f"type {text[:40]}","steps":None}

def _generate_in_app_intent(m):
	"""Handles 'generate/write/create X in notepad/vscode' — open app + smart type."""
	content = m.group(1).strip()
	app = m.group(2).strip().lower()
	return {"action":"compound","description":f"open {app} and type {content[:40]}","steps":[
		{"action":"open","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"open {app}"},
		{"action":"type","app":None,"target":None,"content":content,"params":{},"risk_hint":"safe","description":f"type {content[:40]}"}
	]}

def _generate_intent(m):
	"""Handles 'generate/write/create X' without specifying an app — just smart type."""
	content = m.group(1).strip()
	return {"action":"type","app":None,"target":None,"content":content,"params":{},"risk_hint":"safe","description":f"type {content[:40]}","steps":None}

def _press_intent(m):
	key = m.group(1).strip().lower()
	return {"action":"press","app":None,"target":None,"content":key,"params":{},"risk_hint":"safe","description":f"press {key}","steps":None}

def _volume_intent(m):
	level = int(m.group(1))
	return {"action":"volume","app":None,"target":None,"content":None,"params":{"level":level},"risk_hint":"safe","description":f"set volume to {level}%","steps":None}

def _screenshot_intent(m):
	return {"action":"screenshot","app":None,"target":None,"content":None,"params":{},"risk_hint":"safe","description":"take a screenshot","steps":None}

def _minimize_all_intent(m):
	return {"action":"minimize","app":None,"target":"all","content":None,"params":{},"risk_hint":"safe","description":"minimize all windows","steps":None}

def _maximize_intent(m):
	app = m.group(1).strip().lower()
	return {"action":"maximize","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"maximize {app}","steps":None}

def _minimize_intent(m):
	app = m.group(1).strip().lower()
	return {"action":"minimize","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"minimize {app}","steps":None}

def _switch_intent(m):
	app = m.group(1).strip().lower()
	return {"action":"switch_window","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"switch to {app}","steps":None}

def _search_intent(m):
	query = m.group(1).strip()
	return {"action":"search","app":"google","target":None,"content":query,"params":{},"risk_hint":"safe","description":f"search for {query}","steps":None}

def _play_intent(m):
	query = m.group(1).strip()
	return {"action":"play","app":"youtube","target":None,"content":query,"params":{},"risk_hint":"safe","description":f"play {query} on YouTube","steps":None}

def _lock_intent(m):
	return {"action":"lock","app":None,"target":None,"content":None,"params":{},"risk_hint":"safe","description":"lock screen","steps":None}

def _battery_intent(m):
	return {"action":"battery","app":None,"target":None,"content":None,"params":{},"risk_hint":"safe","description":"check battery","steps":None}

def _find_intent(m):
	text = m.group(1).strip()
	return {"action":"find","app":None,"target":text,"content":None,"params":{},"risk_hint":"safe","description":f"find {text} on screen","steps":None}

def _click_intent(m):
	text = m.group(1).strip()
	return {"action":"click","app":None,"target":text,"content":None,"params":{},"risk_hint":"safe","description":f"click on {text}","steps":None}

def _scroll_intent(m):
	dir = m.group(1).strip().lower()
	times = int(m.group(2)) if m.group(2) else 3
	return {"action":"scroll","app":None,"target":dir,"content":None,"params":{"direction":dir,"times":times},"risk_hint":"safe","description":f"scroll {dir}","steps":None}

def _what_on_screen_intent(m):
	return {"action":"read_screen","app":None,"target":None,"content":None,"params":{},"risk_hint":"safe","description":"read screen contents","steps":None}

def _dictate_intent(m):
	return {"action":"dictate","app":None,"target":None,"content":None,"params":{},"risk_hint":"safe","description":"start dictation mode","steps":None}

def _open_and_type_intent(m):
	app = m.group(1).strip().lower()
	text = m.group(2).strip()
	return {"action":"compound","description":f"open {app} and type {text[:40]}","steps":[
		{"action":"open","app":app,"target":None,"content":None,"params":{},"risk_hint":"safe","description":f"open {app}"},
		{"action":"type","app":None,"target":None,"content":text,"params":{},"risk_hint":"safe","description":f"type {text[:40]}"}
	]}

def _send_whatsapp_intent(m):
	# "send hi to goutham on whatsapp" or "message goutham on whatsapp saying hi"
	content = m.group(1).strip()
	target = m.group(2).strip()
	platform = _extract_platform(m.string if hasattr(m, 'string') else "")
	return {"action":"send","app":"whatsapp","target":target,"content":content,
	        "platform":platform,"params":{},"risk_hint":"safe",
	        "description":f"send WhatsApp message to {target}","steps":None}

def _send_whatsapp_intent2(m):
	# "send goutham hi on whatsapp" or "tell goutham hi"
	target = m.group(1).strip()
	# Reject if target is a pronoun — "tell me a joke" is NOT a WhatsApp message
	if target.lower() in ("me", "us", "him", "her", "them", "about", "that", "this", "it"):
		return None
	content = m.group(2).strip()
	# Extract platform from full matched string for routing
	full_text = m.string if hasattr(m, 'string') else ""
	platform = _extract_platform(full_text)
	# Route to correct action based on platform
	app = platform if platform != "whatsapp" else "whatsapp"
	return {"action":"send","app":app,"target":target,"content":content,
	        "platform":platform,"params":{},"risk_hint":"safe",
	        "description":f"send {platform} message to {target}","steps":None}

# ── Spotify: "play Starboy by The Weeknd" / "play Starboy from Weeknd" ─────
# Must be defined BEFORE _PLAY_VIDEO_OF so "play X by Y" goes to Spotify, not YouTube.
_SPOTIFY_PLAY_BY = re.compile(
	r'^play\s+(.+?)\s+(?:by|from)\s+(.+?)(?:\s+(?:on|in|at)\s+spotify)?$',
	re.IGNORECASE
)

def _spotify_play_by_intent(m):
	song   = m.group(1).strip()
	artist = m.group(2).strip()
	query  = f"{song} {artist}"
	return {"action": "play_spotify", "app": "spotify", "target": query,
	        "content": query, "params": {}, "risk_hint": "safe",
	        "description": f"play '{song}' by {artist} on Spotify", "steps": None}

# Phase 3 — YouTube play-video pattern
# Matches: "open browser and play latest video of mr beast in youtube"
#          "play latest video of mr beast"  "play mr beast's latest video"
#          "play mr beast on youtube"  (catches variations _play_intent misses)
_PLAY_VIDEO_OF = re.compile(
	r'^(?:open\s+(?:browser\s+and\s+)?)?play\s+'
	r'(?:(?:the\s+)?(?:latest\s+)?video\s+(?:of|from|by|of\s+)\s*)?'
	r'(.+?)'
	r'(?:\s+(?:on|in|at)\s+(?:youtube|yt|youtube\.com))?$',
	re.IGNORECASE,
)

def _play_video_intent(m):
	query = m.group(1).strip()
	return {"action": "play_video", "app": "youtube", "target": None,
	        "content": query, "params": {}, "risk_hint": "safe",
	        "description": f"play YouTube video: {query}", "steps": None}


# ── Phase 3 Pattern Constants ──────────────────────────────────────────────────
# Fix 2: "find / open <contact> chat/dm/conversation" → open WhatsApp chat, NOT screen-find
_FIND_WHATSAPP_CHAT = re.compile(
	r'^(?:find|open)\s+(.+?)\s+(?:chat|conversation|dm)$', re.IGNORECASE
)
# Fix 3: "send <message> to <contact>" (contact is NOT an app name)
_SEND_MSG_TO = re.compile(
	r'^(?:send|message)\s+(.+?)\s+to\s+(?!(?:whatsapp|telegram|discord|slack|teams|email|gmail|outlook)\b)(.+)$',
	re.IGNORECASE,
)
# Fix 4: "open chrome/firefox/edge and go to <url>"
_OPEN_BROWSER_NAV = re.compile(
	r'^open\s+(chrome|firefox|edge|brave|browser)\s+(?:and\s+)?(?:go\s+to|navigate\s+to|visit)\s+(\S+)$',
	re.IGNORECASE,
)

def _open_whatsapp_chat_intent(m):
	contact = m.group(1).strip()
	return {"action": "open_whatsapp_chat", "app": "whatsapp", "target": contact,
	        "content": None, "params": {}, "risk_hint": "safe",
	        "description": f"open {contact}'s WhatsApp chat", "steps": None}

# ── Vision Action Patterns (moved below) ──────────────────────────────────

# ── Spotify Patterns ──────────────────────────────────────────────────────────
# Matches: "open spotify and play starboy" / "launch spotify and play weeknd"
_SPOTIFY_OPEN_PLAY = re.compile(
	r'^(?:open|launch|start)\s+spotify\s+(?:and\s+)?play\s+(.+)$',
	re.IGNORECASE
)
# Matches: "play starboy on spotify" / "play weeknd in spotify"
_SPOTIFY_PLAY_ON = re.compile(
	r'^play\s+(.+?)\s+(?:on|in|at)\s+spotify$',
	re.IGNORECASE
)

def _spotify_play_intent(m):
	song = m.group(1).strip()
	return {"action": "play_spotify", "app": "spotify", "target": song,
	        "content": song, "params": {}, "risk_hint": "safe",
	        "description": f"open Spotify and play '{song}'", "steps": None}

def _send_wa_desktop_intent(m):
	# Correct group order: group(1)=message, group(2)=contact
	message = m.group(1).strip()
	contact = m.group(2).strip()
	# Strip trailing platform indicator: "send hi to goutham in my whatsapp"
	# → contact = "goutham" not "goutham in my whatsapp"
	contact = re.sub(
		r'\s+(?:on|in|via|through|using|at)\s+(?:my\s+)?'
		r'(?:whatsapp|telegram|discord|slack|teams|email|gmail|outlook)\s*$',
		'', contact, flags=re.IGNORECASE
	).strip()
	# Bug 5 fix: extract platform from the full matched string
	full_text = m.string if hasattr(m, 'string') else ""
	platform = _extract_platform(full_text)
	# Use send_whatsapp_desktop for whatsapp, or the generic send for other platforms
	if platform == "whatsapp":
		return {"action": "send_whatsapp_desktop", "app": "whatsapp", "target": contact,
		        "content": message, "platform": platform, "params": {}, "risk_hint": "safe",
		        "description": f"send '{message}' to {contact} on WhatsApp Desktop", "steps": None}
	else:
		return {"action": "send", "app": platform, "target": contact,
		        "content": message, "platform": platform, "params": {}, "risk_hint": "safe",
		        "description": f"send '{message}' to {contact} on {platform}", "steps": None}

def _open_browser_navigate_intent(m):
	browser = m.group(1).strip().lower()
	url     = m.group(2).strip()
	return {"action": "open_browser_navigate", "app": browser, "target": url,
	        "content": url, "params": {}, "risk_hint": "safe",
	        "description": f"open {browser} and navigate to {url}", "steps": None}


# ── Vision / Screen-grounded intent patterns ──────────────────────────────────
# These power the VisionAgent for direct screen interaction.

# "send hi to Keerth on WhatsApp" / "send a message to Keerth saying hello"
_VISION_SEND_MSG = re.compile(
	r'^send\s+(?:a\s+)?(?:message\s+)?(.+?)\s+to\s+(.+?)'
	r'(?:\s+(?:on|in|via)\s+(?P<app>whatsapp|telegram|discord))?\s*$',
	re.IGNORECASE,
)

def _vision_send_msg_intent(m):
	message = m.group(1).strip()
	contact = m.group(2).strip()
	app = m.group('app') or 'whatsapp'
	# Strip trailing platform indicator from contact
	contact = re.sub(
		r'\s+(?:on|in|via|through)\s+(?:whatsapp|telegram|discord)\s*$',
		'', contact, flags=re.IGNORECASE
	).strip()
	return {"action": "send_message", "app": app.lower(), "target": contact,
	        "contact": contact, "message": message,
	        "content": message, "params": {}, "risk_hint": "safe",
	        "description": f"send '{message}' to {contact} on {app}", "steps": None}

# "play Starboy" / "play Starboy on Spotify" / "put on Starboy"
_VISION_PLAY_MEDIA = re.compile(
	r'^(?:play|put\s+on)\s+(.+?)\s+(?:on|in)\s+(?P<app>spotify|music)\s*$',
	re.IGNORECASE,
)

def _vision_play_media_intent(m):
	query = m.group(1).strip()
	app = m.group('app') or 'spotify'
	return {"action": "play_media", "app": app.lower(), "target": query,
	        "query": query, "content": query, "params": {}, "risk_hint": "safe",
	        "description": f"play '{query}' on {app}", "steps": None}

# "click the send button" / "click on search"
_VISION_CLICK = re.compile(
	r'^click\s+(?:on\s+)?(?:the\s+)?(.+)$',
	re.IGNORECASE,
)

def _vision_click_intent(m):
	target = m.group(1).strip()
	return {"action": "vision_click", "app": None, "target": target,
	        "content": None, "params": {}, "risk_hint": "safe",
	        "description": f"click on '{target}'", "steps": None}

# "what's on my screen" / "read the screen"
_VISION_READ_SCREEN = re.compile(
	r'^(?:what(?:\'s|\s+is)\s+on\s+(?:my\s+)?screen|read\s+(?:the\s+)?screen|'
	r'what\s+does\s+(?:the\s+)?screen\s+say|describe\s+screen)$',
	re.IGNORECASE,
)

def _vision_read_screen_intent(m):
	return {"action": "read_screen", "app": None, "target": None,
	        "content": None, "params": {}, "risk_hint": "safe",
	        "description": "read screen contents", "steps": None}

# "find Keerth in WhatsApp" / "find settings button"
_VISION_FIND_IN_APP = re.compile(
	r'^find\s+(.+?)\s+(?:in|on)\s+(\w+)$',
	re.IGNORECASE,
)

def _vision_find_in_app_intent(m):
	target = m.group(1).strip()
	app = m.group(2).strip()
	return {"action": "vision_instruction", "app": app, "target": target,
	        "instruction": f"find {target} in {app}",
	        "content": None, "params": {}, "risk_hint": "safe",
	        "description": f"find '{target}' in {app}", "steps": None}


FAST_PATTERNS = [
	# ── NAVIGATION (checked FIRST — most specific, must precede 'switch', 'open') ──
	(_NAV_CHAT_ON_APP,    _parse_nav_chat_on_app),
	(_NAV_CHAT_DEFAULT,   _parse_nav_chat_default),
	(_NAV_CONTACT_ON_APP, _parse_nav_contact_on_app),
	(_NAV_CHANNEL,        _parse_nav_channel),
	(_NAV_URL,            _parse_nav_url),
	(_NAV_FOLDER,         _parse_nav_folder),
	(_NAV_SWITCH,         _parse_nav_switch),
	(_NAV_GENERIC,        _parse_nav_generic),   # catch-all — must be last of nav group

	# ── TIMER COMMANDS ─────────────────────────────────────────────────────
	(re.compile(r'^(?:set\s+(?:a\s+)?(?:timer|alarm)\s+(?:for\s+)?|start\s+(?:a\s+)?timer\s+(?:for\s+)?)(.+)$', re.I), _parse_timer_set),
	(re.compile(r'^(?:cancel|stop|clear)\s+(?:the\s+)?timer$', re.I), _parse_timer_cancel),
	(re.compile(r'^(?:how\s+much\s+time|time\s+(?:left|remaining)|timer\s+status).*$', re.I), _parse_timer_status),

	# ── MEDIA CONTROLS ─────────────────────────────────────────────────────
	(re.compile(r'^(?:pause|resume|play\s+pause|toggle\s+play)$', re.I), _parse_media_playpause),
	(re.compile(r'^(?:next|skip|next\s+(?:track|song))$', re.I), _parse_media_next),
	(re.compile(r'^(?:previous|prev|go\s+back|previous\s+(?:track|song)|last\s+(?:track|song))$', re.I), _parse_media_prev),

	# ── WIFI / BLUETOOTH ────────────────────────────────────────────────────
	(re.compile(r'^(?:turn\s+on|enable|connect)\s+(?:the\s+)?wi\s*fi$', re.I), _parse_wifi_on),
	(re.compile(r'^(?:turn\s+off|disable|disconnect)\s+(?:the\s+)?wi\s*fi$', re.I), _parse_wifi_off),
	(re.compile(r'^(?:turn\s+on|enable)\s+(?:the\s+)?bluetooth$', re.I), _parse_bt_on),
	(re.compile(r'^(?:turn\s+off|disable)\s+(?:the\s+)?bluetooth$', re.I), _parse_bt_off),

	# ── EXISTING PATTERNS ───────────────────────────────────────────────────
	# Fix 4: "open chrome and go to youtube.com" — must precede generic open+type compound
	(_OPEN_BROWSER_NAV, _open_browser_navigate_intent),
	# Compound: "open X and type Y" / "open X and write Y"
	(re.compile(r'^open\s+(\w+)\s+(?:and|then)\s+(?:type|write)\s+(.+)$', re.I), _open_and_type_intent),
	# Messaging: "send X to Y on whatsapp" — explicit app
	(re.compile(r'^send\s+(.+?)\s+to\s+(\w+)\s+on\s+whatsapp$', re.I), _send_whatsapp_intent),
	# Fix 3: "send <msg> to <contact>" (no app suffix) — correct group order
	(_SEND_MSG_TO, _send_wa_desktop_intent),
	(re.compile(r'^(?:send|tell|message)\s+(\w+)\s+(.+?)(?:\s+on\s+whatsapp)?$', re.I), _send_whatsapp_intent2),
	# Single commands
	(re.compile(r'^open\s+(.+)$', re.I), _open_intent),
	(re.compile(r'^close\s+(.+)$', re.I), _close_intent),
	# Generate/write/create X in <app> — compound: open app + smart type
	(re.compile(r'^(?:generate|write|create|make)(?:\s+me)?(?:\s+a(?:n)?)?\s+(.+?)\s+(?:in|inside|into|on)\s+(notepad|vscode|code|editor|word|sublime|terminal|cmd|powershell|wordpad|excel|powerpoint)$', re.I), _generate_in_app_intent),
	# Generate/create X (no app specified) — smart type in current window
	(re.compile(r'^(?:generate|create|make)(?:\s+me)?(?:\s+a(?:n)?)?\s+(.+)$', re.I), _generate_intent),
	(re.compile(r'^(?:type|write)\s+(.+)$', re.I), _type_intent),
	(re.compile(r'^press\s+(.+)$', re.I), _press_intent),
	(re.compile(r'^(?:set\s+)?volume\s+(?:to\s+)?(\d+)(?:\s*%)?$', re.I), _volume_intent),
	(re.compile(r'^(?:take\s+(?:a\s+)?)?screenshot$', re.I), _screenshot_intent),
	(re.compile(r'^minimize\s+all(?:\s+windows)?$', re.I), _minimize_all_intent),
	(re.compile(r'^maximize\s+(.+)$', re.I), _maximize_intent),
	(re.compile(r'^minimize\s+(.+)$', re.I), _minimize_intent),
	(re.compile(r'^(?:search|google)\s+(?:(?:google|bing|youtube)\s+)?(?:for\s+)?(.+)$', re.I), _search_intent),
	# ── SPOTIFY (before _PLAY_VIDEO_OF to avoid YouTube capture) ──────────────
	(_SPOTIFY_OPEN_PLAY, _spotify_play_intent),   # "open spotify and play Starboy"
	(_SPOTIFY_PLAY_ON,   _spotify_play_intent),   # "play Starboy on spotify"
	(_SPOTIFY_PLAY_BY,   _spotify_play_by_intent), # "play Starboy by/from The Weeknd"
	# 'play latest video of mr beast in youtube' / 'open browser and play X'
	(_PLAY_VIDEO_OF,  _play_video_intent),
	(re.compile(r'^play\s+(.+?)(?:\s+on\s+youtube)?$', re.I), _play_intent),
	(re.compile(r'^lock(?:\s+(?:the\s+)?(?:screen|computer))?$', re.I), _lock_intent),
	(re.compile(r'^(?:check\s+)?battery(?:\s+(?:status|level))?$', re.I), _battery_intent),
	# Vision / screen interaction
	# Fix 2: "find Goutham Son chat" → WhatsApp chat opener (must precede generic find)
	(_FIND_WHATSAPP_CHAT, _open_whatsapp_chat_intent),
	# Vision: "send a message to X saying Y" → multi-step vision execution
	(_VISION_SEND_MSG, _vision_send_msg_intent),
	# Vision: "find X in/on Y" → vision-based element search in specific app
	(_VISION_FIND_IN_APP, _vision_find_in_app_intent),
	(re.compile(r'^find\s+(.+)$', re.I), _find_intent),
	(re.compile(r'^click(?:\s+on)?\s+(.+)$', re.I), _click_intent),
	(re.compile(r'^scroll\s+(up|down)(?:\s+(\d+))?$', re.I), _scroll_intent),
	(re.compile(r'^(?:what(?:s|\s+is)\s+on\s+(?:my\s+)?screen|read\s+screen|describe\s+screen)$', re.I), _what_on_screen_intent),
	# Dictation mode
	(re.compile(r'^(?:start\s+)?(?:dictate|dictation|start\s+typing|type\s+what\s+i\s+say|listen\s+and\s+type)$', re.I), _dictate_intent),
]

INTENT_PROMPT = """
You are a Windows PC control intent parser. Extract structured intent from voice commands.
Return ONLY valid JSON. No explanation, no markdown.

Schema for single actions:
{
  "action": string,        // open|close|send|type|press|search|play|pause|volume|brightness|
                           // screenshot|delete|move|copy|rename|create|shutdown|restart|
                           // lock|install|uninstall|browse|scroll|click|minimize|maximize|
                           // snap|resize|switch_window|run_command|empty_trash|battery|
                           // system_stats|clipboard|print|zip|unzip|task_kill
  "app": string|null,      // whatsapp|telegram|discord|chrome|firefox|edge|spotify|
                           // youtube|vscode|explorer|notepad|excel|word|powerpoint|
                           // task_manager|settings|cmd|powershell|paint|calculator|null
  "target": string|null,   // contact name, file path, window title, process name
  "content": string|null,  // message text, search query, text to type, file content
  "params": object,        // extra: {level, path, direction, times, monitor, key...}
  "risk_hint": string,     // "safe"|"moderate"|"destructive"|"critical"
  "description": string,   // human readable description
  "steps": null            // null for single actions
}

For COMPOUND commands (two or more sequential actions joined by "and" or "then"),
use the "steps" array instead. Return:
{
  "action": "compound",
  "description": string,
  "steps": [
    { "action":..., "app":..., "target":..., "content":..., "params":..., "risk_hint":..., "description":... },
    ...
  ]
}

Risk hints:
  safe        = reversible, no data loss, no external communication to unknowns
  moderate    = sends data externally, modifies files
  destructive = permanent deletion, system modification
  critical    = shutdown/restart, installs, registry, disabling security

Examples:

Single actions:

"open whatsapp"
→ {"action":"open","app":"whatsapp","target":null,"content":null,"params":{},"risk_hint":"safe","description":"open WhatsApp","steps":null}

"open notepad"
→ {"action":"open","app":"notepad","target":null,"content":null,"params":{},"risk_hint":"safe","description":"open Notepad","steps":null}

"type hello world"
→ {"action":"type","app":null,"target":null,"content":"hello world","params":{},"risk_hint":"safe","description":"type hello world","steps":null}

"press enter"
→ {"action":"press","app":null,"target":null,"content":"enter","params":{},"risk_hint":"safe","description":"press Enter key","steps":null}

"open whatsapp and send Rahul I'll be late"
→ {"action":"send","app":"whatsapp","target":"Rahul","content":"I'll be late","params":{},"risk_hint":"safe","description":"send WhatsApp message to Rahul saying I'll be late","steps":null}

"delete the file report.pdf from downloads"
→ {"action":"delete","app":"explorer","target":"~/Downloads/report.pdf","content":null,"params":{"permanent":false},"risk_hint":"moderate","description":"delete report.pdf from Downloads folder","steps":null}

"shutdown the computer in 2 minutes"
→ {"action":"shutdown","app":null,"target":null,"content":null,"params":{"delay":120},"risk_hint":"critical","description":"shut down the computer in 2 minutes","steps":null}

"set volume to 40 percent"
→ {"action":"volume","app":null,"target":null,"content":null,"params":{"level":40},"risk_hint":"safe","description":"set system volume to 40 percent","steps":null}

"snap chrome to the left side"
→ {"action":"snap","app":"chrome","target":"left","content":null,"params":{},"risk_hint":"safe","description":"snap Chrome window to left half of screen","steps":null}

"find all pdf files in documents folder"
→ {"action":"search","app":"explorer","target":"~/Documents","content":"*.pdf","params":{},"risk_hint":"safe","description":"search for PDF files in Documents","steps":null}

"kill the chrome process"
→ {"action":"task_kill","app":"chrome","target":null,"content":null,"params":{},"risk_hint":"moderate","description":"force kill Chrome process","steps":null}

Compound actions (use "steps"):

"open notepad and type hello"
→ {"action":"compound","description":"open Notepad and type hello","steps":[{"action":"open","app":"notepad","target":null,"content":null,"params":{},"risk_hint":"safe","description":"open Notepad"},{"action":"type","app":null,"target":null,"content":"hello","params":{},"risk_hint":"safe","description":"type hello"}]}

"open notepad and write my name is Kuro"
→ {"action":"compound","description":"open Notepad and type my name is Kuro","steps":[{"action":"open","app":"notepad","target":null,"content":null,"params":{},"risk_hint":"safe","description":"open Notepad"},{"action":"type","app":null,"target":null,"content":"my name is Kuro","params":{},"risk_hint":"safe","description":"type my name is Kuro"}]}

"open chrome then go to youtube"
→ {"action":"compound","description":"open Chrome and navigate to YouTube","steps":[{"action":"open","app":"chrome","target":null,"content":null,"params":{},"risk_hint":"safe","description":"open Chrome"},{"action":"browse","app":null,"target":"https://youtube.com","content":null,"params":{},"risk_hint":"safe","description":"navigate to YouTube"}]}

"minimize all windows and take a screenshot"
→ {"action":"compound","description":"minimize all windows then take screenshot","steps":[{"action":"minimize","app":null,"target":"all","content":null,"params":{},"risk_hint":"safe","description":"minimize all windows"},{"action":"screenshot","app":null,"target":null,"content":null,"params":{},"risk_hint":"safe","description":"take a screenshot"}]}

Command: "{command}"
"""

class IntentParser:
	def __init__(self, model="mistral"):
		self.model = model

	def parse(self, command: str) -> dict:
		# ── Fast-path: regex match for simple commands (~0ms) ──
		# Strip trailing punctuation that Whisper adds ("notepad." → "notepad")
		cleaned = command.strip().rstrip('.,!?;:')
		# Bug 6 fix: normalize verb synonyms BEFORE regex matching
		# so "launch Spotify" → "open Spotify", "dm Bob hey" → "send Bob hey"
		normalized = _normalize_verb(cleaned)
		for pattern, builder in FAST_PATTERNS:
			m = pattern.match(normalized)
			if m:
				intent = builder(m)
				if intent is None:
					continue  # builder rejected the match (e.g. pronoun guard)
				print(f"[IntentParser] Fast-path matched: {intent.get('action')} ({normalized!r})")
				return intent

		# ── Vision intent fast-path (send_message, play_media, etc.) ──
		try:
			from agent.intent_parser_vision_patch import parse_vision_intents
			vision_intent = parse_vision_intents(normalized)
			if vision_intent:
				print(f"[IntentParser] Vision-patch matched: {vision_intent.get('action')} ({normalized!r})")
				return vision_intent
		except ImportError:
			pass  # vision patch not installed — skip

		# ── Slow-path: LLM parse for complex/ambiguous commands ──
		if not OLLAMA_AVAILABLE:
			return {"action": "unknown", "error": "ollama unavailable"}
		prompt = INTENT_PROMPT.replace("{command}", command)
		response = _ollama.chat(
			model=self.model,
			messages=[{"role": "user", "content": prompt}],
			options={"temperature": 0, "num_predict": 200, "num_ctx": 1024}
		)
		raw = response['message']['content'].strip()
		# Strip markdown fences if present
		if "```" in raw:
			parts = raw.split("```")
			raw = parts[1] if len(parts) > 1 else parts[0]
			if raw.startswith("json"):
				raw = raw[4:]
		try:
			return json.loads(raw.strip())
		except json.JSONDecodeError:
			return {
				"action": "unknown",
				"app": None,
				"target": None,
				"content": command,
				"params": {},
				"risk_hint": "safe",
				"description": command,
				"steps": None,
			}
# Intent Parser implementation from system control guide