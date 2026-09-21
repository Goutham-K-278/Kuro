"""
scripts/spotify_auth.py
───────────────────────────────────────────────────────
One-time Spotify OAuth browser login.

Run this ONCE after adding your Spotify credentials to .env.
A browser window opens → log in → paste the redirect URL → done.
After this, spotify_agent.py works silently forever (tokens auto-refresh).

Usage:
    python scripts/spotify_auth.py
───────────────────────────────────────────────────────
"""

import os
import sys

sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

client_id     = os.getenv("SPOTIFY_CLIENT_ID", "")
client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
redirect_uri  = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

if not client_id or not client_secret:
    print("❌  SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET not set in .env")
    print()
    print("To get credentials:")
    print("  1. Go to https://developer.spotify.com/dashboard")
    print("  2. Create an app (any name)")
    print("  3. Add redirect URI: http://localhost:8888/callback")
    print("  4. Copy Client ID and Client Secret into your .env:")
    print()
    print("     SPOTIFY_CLIENT_ID=your_client_id_here")
    print("     SPOTIFY_CLIENT_SECRET=your_client_secret_here")
    print("     SPOTIFY_REDIRECT_URI=http://localhost:8888/callback")
    sys.exit(1)

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    print("❌  spotipy not installed. Run: pip install spotipy")
    sys.exit(1)

os.makedirs("data", exist_ok=True)

print("Opening browser for Spotify login...")
print(f"Redirect URI: {redirect_uri}")
print()
print("If the browser doesn't open automatically, copy the URL shown in the terminal.")
print()

auth_manager = SpotifyOAuth(
    client_id     = client_id,
    client_secret = client_secret,
    redirect_uri  = redirect_uri,
    scope         = (
        "user-modify-playback-state "
        "user-read-playback-state "
        "user-read-currently-playing"
    ),
    cache_path    = "data/.spotify_token_cache",
    open_browser  = True,
)

sp = spotipy.Spotify(auth_manager=auth_manager)

try:
    me = sp.me()
    print(f"✅ Spotify auth complete!")
    print(f"   Logged in as: {me['display_name']} ({me['id']})")
    print()
    print("Token cached at: data/.spotify_token_cache")
    print("Kuro will now auto-refresh tokens — you never need to run this again.")
except Exception as e:
    print(f"❌  Auth failed: {e}")
    sys.exit(1)
