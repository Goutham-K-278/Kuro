"""
KURO - Blender Engine
Generates bpy Python scripts using CLOUD brain and executes them.

Two execution modes:
  1. Background render: blender --background --python script.py
  2. File watcher:      Write script to watch folder → Blender addon picks it up
"""
import logging
import subprocess
import tempfile
import time
from pathlib import Path

from core.config import BLENDER_PATH, WATCH_DIR, DATA_DIR, BLENDER_TIMEOUT

logger = logging.getLogger(__name__)

# Template that wraps generated code into a valid Blender scene-reset + run structure
BPY_TEMPLATE = '''import bpy

# Clear existing objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# ─── GENERATED CODE ──────────────────────────────────────────────────────────
{script}
# ─────────────────────────────────────────────────────────────────────────────
'''


class BlenderEngine:
    """Generate and execute Blender Python scripts via CLOUD brain."""

    def __init__(self, llm_engine):
        self.llm = llm_engine
        from creative.safety_checker import SafetyChecker
        self.checker = SafetyChecker()
        WATCH_DIR.mkdir(parents=True, exist_ok=True)

    def generate_script(self, description: str, context: str = "") -> str:
        """
        Generate a Blender bpy script from a description.
        Uses CLOUD model (Qwen3-Coder-480B) for script quality.
        """
        prompt = self._build_prompt(description, context)
        messages = [{"role": "user", "content": prompt}]

        logger.info(f"Generating Blender script for: {description[:80]}...")
        script = self.llm.chat(messages, use_cloud=True, mode="blender")

        # Extract code block if wrapped in ```python ... ```
        script = self._extract_code(script)
        return script

    def generate_and_run(
        self,
        description: str,
        mode: str = "watch",    # "watch" (live viewport) or "background" (render+save)
        output_path: str | None = None,
    ) -> dict:
        """
        Full pipeline: describe → generate → validate → execute

        mode='watch':       Writes to WATCH_DIR for live Blender viewport
        mode='background':  Renders a .png via blender --background
        """
        # Step 1: Generate
        script = self.generate_script(description)

        # Step 2: Safety check
        safe, reason = self.checker.is_safe(script)
        if not safe:
            logger.warning(f"Script blocked by safety checker: {reason}")
            return {
                "message": f"Script generation blocked for safety: {reason}",
                "script": script,
                "success": False,
            }

        # Step 3: Wrap in template
        full_script = BPY_TEMPLATE.format(script=script)

        # Step 4: Execute
        if mode == "watch":
            return self._execute_via_watcher(full_script, description)
        else:
            return self._execute_background(full_script, output_path)

    def _execute_via_watcher(self, script: str, description: str) -> dict:
        """
        Write script to watch folder.
        Blender's file watcher addon will auto-execute it in the live viewport.
        """
        script_name = f"kuro_{int(time.time())}.py"
        script_path = WATCH_DIR / script_name

        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script)

            logger.info(f"Script written to watch folder: {script_path}")
            return {
                "message": f"Script ready! Open Blender and run the Kuro Watcher addon.\nScript saved at: {script_path}",
                "script": script,
                "script_path": str(script_path),
                "success": True,
                "mode": "watch",
            }
        except Exception as e:
            return {"message": f"Failed to write script: {e}", "script": script, "success": False}

    def _execute_background(
        self, script: str, output_path: str | None = None
    ) -> dict:
        """
        Execute script via Blender's background mode and render output.
        Requires Blender to be installed and BLENDER_PATH to be configured.
        """
        output_path = output_path or str(DATA_DIR / "renders" / f"kuro_{int(time.time())}.png")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Append render command to script
        render_script = script + f"""
# Save render
bpy.context.scene.render.filepath = r"{output_path}"
bpy.ops.render.render(write_still=True)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(render_script)
            tmp_path = f.name

        try:
            cmd = [BLENDER_PATH, "--background", "--python", tmp_path]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=BLENDER_TIMEOUT,
            )

            if result.returncode == 0 and Path(output_path).exists():
                return {
                    "message": f"Render complete! Saved to: {output_path}",
                    "output_path": output_path,
                    "script": script,
                    "success": True,
                    "mode": "background",
                }
            else:
                return {
                    "message": f"Render failed.\nError: {result.stderr[-500:]}",
                    "script": script,
                    "success": False,
                }

        except subprocess.TimeoutExpired:
            return {"message": f"Render timed out after {BLENDER_TIMEOUT}s", "script": script, "success": False}
        except FileNotFoundError:
            return {"message": f"Blender not found at: {BLENDER_PATH}\nUpdate BLENDER_PATH in .env", "script": script, "success": False}
        except Exception as e:
            return {"message": f"Execution error: {e}", "script": script, "success": False}

    def _build_prompt(self, description: str, context: str = "") -> str:
        context_block = f"\n\nAdditional context:\n{context}" if context else ""
        return f"""Generate a complete Blender Python (bpy) script for:

"{description}"{context_block}

Requirements:
- Use bpy API only
- Script must be self-contained and runnable
- Include comments explaining each major step
- Use proper scene setup (clear existing objects first is handled externally)
- Output ONLY the Python code, no explanations outside the code

The script should create exactly what was described."""

    def _extract_code(self, response: str) -> str:
        """Extract Python code from markdown code block if present."""
        import re
        match = re.search(r"```(?:python)?\n([\s\S]*?)```", response)
        if match:
            return match.group(1).strip()
        return response.strip()
