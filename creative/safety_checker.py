"""
KURO - Script Safety Checker
Validates generated Blender scripts before execution.
Blocks dangerous patterns: os, subprocess, socket, network, file writes outside allowed dirs.
"""
import ast
import logging
import re

logger = logging.getLogger(__name__)

# Modules that are NEVER allowed in generated scripts
BLOCKED_IMPORTS = {
    "os", "subprocess", "socket", "shutil", "sys",
    "requests", "urllib", "httpx", "aiohttp",
    "pickle", "shelve", "ctypes", "cffi",
    "importlib", "builtins",
}

# Dangerous built-in function calls
BLOCKED_CALLS = {
    "exec", "eval", "compile", "__import__",
    "open",    # File I/O blocked (use bpy only)
    "input",   # stdin access blocked
}

# Dangerous code patterns (regex)
BLOCKED_PATTERNS = [
    r"os\.system\s*\(",
    r"os\.popen\s*\(",
    r"subprocess\.(run|Popen|call|check_output)",
    r"socket\.(socket|create_connection)",
    r"urllib\.request",
    r"requests\.(get|post|put|delete)",
    r"open\s*\(",
    r"__builtins__",
    r"globals\s*\(",
    r"locals\s*\(",
    r"__class__\.__bases__",
]


class SafetyChecker:
    """
    Two-layer safety check:
      Layer 1: Pattern matching (fast regex scan)
      Layer 2: AST analysis (structural validation)
    """

    def is_safe(self, script: str) -> tuple[bool, str]:
        """
        Check if a script is safe to execute.
        Returns: (is_safe: bool, reason: str)
        """
        # Layer 1: Regex pattern scan
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, script, re.IGNORECASE):
                return False, f"Blocked pattern detected: {pattern}"

        # Layer 2: AST analysis
        try:
            tree = ast.parse(script)
        except SyntaxError as e:
            return False, f"Syntax error in script: {e}"

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = (
                    node.names[0].name if isinstance(node, ast.Import)
                    else (node.module or "")
                )
                root_module = module.split(".")[0]
                if root_module in BLOCKED_IMPORTS:
                    return False, f"Blocked import: '{module}'"

            # Check function calls
            if isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in BLOCKED_CALLS:
                    return False, f"Blocked function call: '{func_name}()'"

        return True, "Script passed safety checks"

    def get_report(self, script: str) -> dict:
        """Return a detailed safety report (for debugging)."""
        issues = []

        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, script, re.IGNORECASE):
                issues.append(f"Pattern: {pattern}")

        try:
            tree = ast.parse(script)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = (
                        node.names[0].name if isinstance(node, ast.Import)
                        else (node.module or "")
                    )
                    if module.split(".")[0] in BLOCKED_IMPORTS:
                        issues.append(f"Import: {module}")
        except SyntaxError as e:
            issues.append(f"SyntaxError: {e}")

        return {
            "safe": len(issues) == 0,
            "issues": issues,
            "line_count": len(script.splitlines()),
        }
