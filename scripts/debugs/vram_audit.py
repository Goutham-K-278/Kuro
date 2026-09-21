"""
scripts/vram_audit.py
─────────────────────────────────────────────────────────────────
Kuro Phase 7 — Live VRAM Audit

Shows exactly what's eating VRAM right now, per component.
Run BEFORE starting Kuro, AFTER starting Kuro, and after each
component loads to see cumulative VRAM usage.

Usage:
    python scripts/vram_audit.py            # one-shot snapshot
    python scripts/vram_audit.py --watch    # refresh every 2s
─────────────────────────────────────────────────────────────────
"""
import subprocess, sys, time, os

PHASE_7_TARGETS = {
    "Idle (listening only)": 220,
    "Peak (voice+LLM+vision)": 2820,
}


def get_vram() -> dict:
    """Query nvidia-smi for VRAM stats."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=3,
        ).decode().strip()
        parts = [p.strip() for p in out.split(",")]
        return {
            "name":       parts[0],
            "used_mb":    int(parts[1]),
            "total_mb":   int(parts[2]),
            "free_mb":    int(parts[3]),
            "gpu_util":   int(parts[4]),
            "temp_c":     int(parts[5]),
        }
    except Exception as e:
        return {"error": str(e)}


def bar(used, total, width=30) -> str:
    filled = int(used / total * width)
    pct = used / total * 100
    color = "\033[92m" if pct < 50 else "\033[93m" if pct < 80 else "\033[91m"
    return f"{color}{'█' * filled}{'░' * (width - filled)}\033[0m {pct:.0f}%"


def check_phase7_targets(used_mb: int) -> None:
    print()
    print("  Phase 7 Targets:")
    for label, target in PHASE_7_TARGETS.items():
        ok = used_mb <= target
        status = "\033[92m✅\033[0m" if ok else "\033[91m❌\033[0m"
        print(f"    {status}  {label}: target ≤{target}MB  (current {used_mb}MB)")


def snapshot():
    v = get_vram()
    if "error" in v:
        print(f"[VRAM] nvidia-smi unavailable: {v['error']}")
        return

    print()
    print("  ┌─────────────────────────────────────────┐")
    print(f"  │  GPU: {v['name']:<35}│")
    print("  ├─────────────────────────────────────────┤")
    print(f"  │  Used  : {v['used_mb']:>5} MB                        │")
    print(f"  │  Free  : {v['free_mb']:>5} MB                        │")
    print(f"  │  Total : {v['total_mb']:>5} MB                        │")
    print(f"  │  GPU%  : {v['gpu_util']:>3}%                           │")
    print(f"  │  Temp  : {v['temp_c']:>3}°C                           │")
    print("  ├─────────────────────────────────────────┤")
    print(f"  │  {bar(v['used_mb'], v['total_mb'])}  │")
    print("  └─────────────────────────────────────────┘")

    check_phase7_targets(v["used_mb"])
    print()


def main():
    watch = "--watch" in sys.argv
    print("\033[1;35m  Kuro VRAM Audit\033[0m")
    if watch:
        print("  Watching... (Ctrl+C to stop)\n")
        try:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                print("\033[1;35m  Kuro VRAM Audit — Live\033[0m")
                snapshot()
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        snapshot()


if __name__ == "__main__":
    main()
