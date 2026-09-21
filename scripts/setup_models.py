"""
setup_models.py
─────────────────────────────────────────────────────────────────
One-time model downloader for Kuro AI

Downloads / verifies:
  1. Sherpa-ONNX streaming Zipformer STT
     → voice/models/sherpa_onnx/sherpa-onnx-streaming-zipformer-en-2023-02-21/
  2. Kokoro-ONNX TTS
     → models/kokoro-v1.0.onnx
     → models/voices-v1.0.bin
  3. OmniParser V2 (weights, not server code — see README for server setup)
     → models/omniparser/

Env checks:
  • PICOVOICE_KEY — required for wake word
  • GROQ_API_KEY  — required for intent parsing

Usage:
    python setup_models.py
─────────────────────────────────────────────────────────────────
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}✅ {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠️  {msg}{RESET}")
def err(msg):  print(f"{RED}❌ {msg}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Sherpa-ONNX Streaming Zipformer
# ─────────────────────────────────────────────────────────────────────────────
def setup_sherpa():
    model_dir = ROOT / "voice" / "models" / "sherpa_onnx" / \
                "sherpa-onnx-streaming-zipformer-en-2023-02-21"

    if model_dir.exists() and any(model_dir.glob("*.onnx")):
        ok(f"Sherpa-ONNX model already present: {model_dir}")
        return

    print("📥 Downloading Sherpa-ONNX streaming Zipformer model...")
    model_dir.parent.mkdir(parents=True, exist_ok=True)

    import urllib.request
    import tarfile

    url = (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "asr-models/sherpa-onnx-streaming-zipformer-en-2023-02-21.tar.bz2"
    )
    tar_path = model_dir.parent / "sherpa_model.tar.bz2"

    urllib.request.urlretrieve(url, tar_path, reporthook=_progress)
    print()

    print("📦 Extracting Sherpa model...")
    with tarfile.open(tar_path, "r:bz2") as tar:
        tar.extractall(model_dir.parent)
    tar_path.unlink()

    ok("Sherpa-ONNX model ready")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Kokoro-ONNX TTS
# ─────────────────────────────────────────────────────────────────────────────
def setup_kokoro():
    models_dir   = ROOT / "models"
    model_file   = models_dir / "kokoro-v1.0.onnx"
    voices_file  = models_dir / "voices-v1.0.bin"

    models_dir.mkdir(exist_ok=True)

    files_to_download = []
    if not model_file.exists():
        files_to_download.append((
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
            "model-files-v1.0/kokoro-v1.0.onnx",
            model_file,
            "Kokoro ONNX model",
        ))
    if not voices_file.exists():
        files_to_download.append((
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
            "model-files-v1.0/voices-v1.0.bin",
            voices_file,
            "Kokoro voices",
        ))

    if not files_to_download:
        ok("Kokoro TTS models already present")
        return

    import urllib.request
    for url, dest, name in files_to_download:
        print(f"📥 Downloading {name}...")
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        print()

    ok("Kokoro TTS models ready")


# ─────────────────────────────────────────────────────────────────────────────
# 3. OmniParser V2 weights
# ─────────────────────────────────────────────────────────────────────────────
def setup_omniparser():
    omni_dir   = ROOT / "models" / "omniparser"
    weights_dir = omni_dir / "weights"

    if weights_dir.exists() and any(weights_dir.iterdir()):
        ok("OmniParser weights already present")
        return

    print("📥 Downloading OmniParser V2 weights (Hugging Face)...")

    try:
        # huggingface_hub is lighter than cloning the whole repo
        import huggingface_hub as hf
    except ImportError:
        warn("huggingface_hub not installed — skipping OmniParser weights download")
        warn("Install with: pip install huggingface_hub")
        warn("Then run: python -c \"import huggingface_hub as hf; hf.snapshot_download('microsoft/OmniParser-v2.0', local_dir='models/omniparser/weights')\"")
        return

    omni_dir.mkdir(parents=True, exist_ok=True)
    hf.snapshot_download(
        repo_id   = "microsoft/OmniParser-v2.0",
        local_dir = str(weights_dir),
    )
    ok("OmniParser V2 weights ready")
    warn("To start OmniParser server: see docs/GUIDE.md → OmniParser Setup section")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Environment key checks
# ─────────────────────────────────────────────────────────────────────────────
def check_env():
    print("\n🔑 Checking API keys...")

    # Load .env manually (dotenv may not be installed yet)
    env_file = ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip()

    def get_var(name):
        return os.getenv(name, "") or env_vars.get(name, "")

    picovoice = get_var("PICOVOICE_KEY")
    groq      = get_var("GROQ_API_KEY")
    spotify_u = get_var("SPOTIFY_USERNAME")

    if picovoice:
        ok(f"PICOVOICE_KEY present ({picovoice[:8]}...)")
    else:
        err("PICOVOICE_KEY missing — wake word will not work")
        warn("Get a free key at: https://console.picovoice.ai/")

    if groq:
        ok(f"GROQ_API_KEY present ({groq[:8]}...)")
    else:
        err("GROQ_API_KEY missing — intent parsing will fail")
        warn("Get a key at: https://console.groq.com/")

    if spotify_u:
        ok(f"SPOTIFY_USERNAME present ({spotify_u})")
    else:
        warn("SPOTIFY_USERNAME not set — Spotify agent will be disabled")
        warn("Add SPOTIFY_USERNAME and SPOTIFY_PASSWORD to .env")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _progress(count, block_size, total_size):
    if total_size <= 0:
        return
    pct  = min(100, count * block_size * 100 // total_size)
    done = pct // 2
    bar  = "#" * done + "-" * (50 - done)
    print(f"\r  [{bar}] {pct}%", end="", flush=True)


if __name__ == "__main__":
    print("=" * 60)
    print("  Kuro AI — Model Setup")
    print("=" * 60)

    check_env()
    print()

    try:
        setup_sherpa()
    except Exception as e:
        err(f"Sherpa setup failed: {e}")

    try:
        setup_kokoro()
    except Exception as e:
        err(f"Kokoro setup failed: {e}")

    try:
        setup_omniparser()
    except Exception as e:
        err(f"OmniParser setup failed: {e}")

    print("\n" + "=" * 60)
    print("  Setup complete. Run:  python main.py")
    print("=" * 60)
