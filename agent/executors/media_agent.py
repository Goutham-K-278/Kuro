"""
MediaAgent — Control media playback globally via Windows media keys + Spotify API.

Works with any media player (Spotify, VLC, Windows Media, YouTube in browser, etc.)
Uses Windows virtual key codes via ctypes — no app-specific API needed.
Spotify Web API (optional): if credentials are set in .env, uses spotipy for
direct track search + playback (bypasses keyboard automation entirely).

Commands:
  play_pause()     → VK_MEDIA_PLAY_PAUSE
  next_track()     → VK_MEDIA_NEXT_TRACK
  previous_track() → VK_MEDIA_PREV_TRACK
  play_song(query) → Spotify Web API search + playback
"""

import ctypes
import time
import logging
import os

logger = logging.getLogger(__name__)

# Windows virtual key codes for media keys
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_VOLUME_MUTE      = 0xAD
VK_VOLUME_UP        = 0xAF
VK_VOLUME_DOWN      = 0xAE

# Windows keybd_event flags
KEYEVENTF_KEYUP       = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class MediaAgent:
    """
    Control media playback via system-level Windows media key events.
    Works globally with any media player — no app focus required.
    Optionally uses Spotify Web API for direct track playback.
    """

    def __init__(self):
        self._sp = None          # spotipy.Spotify instance (lazy init)
        self._sp_checked = False # whether we've tried to init spotify

    def _init_spotify(self):
        """Lazy-init Spotify Web API client from .env credentials."""
        if self._sp_checked:
            return self._sp
        self._sp_checked = True

        client_id     = os.environ.get("SPOTIFY_CLIENT_ID", "")
        client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        redirect_uri  = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

        if not client_id or not client_secret:
            logger.info("No Spotify API credentials in .env — using keyboard fallback.")
            return None

        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth
            self._sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="user-modify-playback-state user-read-playback-state",
                cache_path=os.path.join(os.path.expanduser("~"), ".kuro_spotify_cache"),
            ))
            logger.info("Spotify Web API initialized successfully.")
        except Exception as e:
            logger.warning(f"Spotify API init failed: {e}")
            self._sp = None

        return self._sp

    def play_song(self, query: str) -> str | None:
        """
        Search Spotify for a track and start playback via Web API.
        Returns a success string, or None if API is unavailable (caller should fallback).
        """
        sp = self._init_spotify()
        if not sp:
            return None  # signal caller to use keyboard fallback

        try:
            results = sp.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return None  # no results, fallback to keyboard

            track = items[0]
            track_name = track["name"]
            artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
            uri = track["uri"]

            sp.start_playback(uris=[uri])
            return f"Playing '{track_name}' by {artist} on Spotify 🎵"

        except Exception as e:
            logger.warning(f"Spotify API playback failed: {e}")
            return None  # fallback to keyboard

    def play_pause(self) -> str:
        """Toggle play/pause for any currently playing media."""
        self._press_media_key(VK_MEDIA_PLAY_PAUSE)
        return "Toggled play/pause"

    def next_track(self) -> str:
        """Skip to next track."""
        self._press_media_key(VK_MEDIA_NEXT_TRACK)
        return "Next track"

    def previous_track(self) -> str:
        """Go to previous track."""
        self._press_media_key(VK_MEDIA_PREV_TRACK)
        return "Previous track"

    def mute_media(self) -> str:
        """Toggle mute via media key."""
        self._press_media_key(VK_VOLUME_MUTE)
        return "Toggled mute"

    # ─── Windows Media Key Sender ─────────────────────────────────────────

    def _press_media_key(self, vk_code: int) -> None:
        """
        Send a media key press + release via Windows keybd_event API.
        More reliable than SendInput for extended media keys.
        """
        try:
            # Key down
            ctypes.windll.user32.keybd_event(
                vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0
            )
            time.sleep(0.05)
            # Key up
            ctypes.windll.user32.keybd_event(
                vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0
            )
        except Exception as e:
            logger.warning(f"Media key send failed: {e}")

