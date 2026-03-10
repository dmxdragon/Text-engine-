"""
health_check.py
───────────────
Health check for both Nixon Bot and Nixon RPG Bot.

Usage:
    python health_check.py          → check both bots
    python health_check.py nixon    → check Nixon Bot only
    python health_check.py rpg      → check RPG Bot only
"""

import ast
import os
import sys
import importlib.util
from typing import Optional

# ── Terminal colors ───────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):     print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg):   print(f"  {RED}❌ {msg}{RESET}")
def warn(msg):   print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg):   print(f"  {CYAN}ℹ️  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{CYAN}{'─'*55}\n{msg}\n{'─'*55}{RESET}")


# ── Files per bot ────────────────────────────────────────────
NIXON_FILES = [
    "core/config.py",
    "core/storage.py",
    "modules/events.py",
    "modules/ai_tools.py",
    "modules/web3.py",
    "modules/moderation.py",
    "modules/general.py",
    "main_nixon.py",
]

RPG_FILES = [
    "rpg_engine_v5.py",
    "world_map.py",
    "main_rpg.py",
]

# ── Environment Variables ─────────────────────────────────────
NIXON_REQUIRED_ENV = [
    ("DISCORD_TOKEN",     "Nixon Bot token — required"),
    ("AIMLAPI_KEY",       "AI API key — required for all AI features"),
]
NIXON_OPTIONAL_ENV = [
    ("ETHERSCAN_API_KEY", "Required for !wallet, !gas, !tx"),
    ("OPENSEA_API_KEY",   "Required for !nft"),
    ("MORALIS_API_KEY",   "Required for !chains"),
    ("YOUTUBE_API_KEY",   "Required for !youtube"),
    ("NASA_API_KEY",      "Optional for !nasa"),
]

RPG_REQUIRED_ENV = [
    ("DISCORD_TOKEN_RPG", "RPG Bot token — must be different from Nixon Bot"),
    ("AIMLAPI_KEY",       "AI API key — required for RPG engine"),
]
RPG_OPTIONAL_ENV = [
    ("MORALIS_API_KEY",   "Required for !verify (NFT ownership)"),
    ("NFT_CONTRACT",      "Your NFT collection contract address"),
    ("NFT_CHAIN",         "Chain ID (default: 0x1 = Ethereum)"),
]

# ── Required packages ─────────────────────────────────────────
NIXON_PACKAGES = [
    ("discord", "discord.py"),
    ("aiohttp",  "aiohttp"),
]
RPG_PACKAGES = [
    ("discord",  "discord.py"),
    ("aiohttp",  "aiohttp"),
    ("PIL",      "Pillow"),
    ("numpy",    "numpy"),
]


# ── Helpers ───────────────────────────────────────────────────

def check_syntax(filepath: str) -> tuple[bool, Optional[str]]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=filepath)
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg} → {(e.text or '').strip()}"
    except Exception as e:
        return False, str(e)

def check_imports(filepath: str) -> list[str]:
    errors = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except Exception:
        return errors
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if not _module_exists(name):
                    errors.append(f"Line {node.lineno}: `import {alias.name}` — not found. pip install {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if not _module_exists(name):
                    errors.append(f"Line {node.lineno}: `from {node.module}` — not found. pip install {name}")
    return errors

def _module_exists(name: str) -> bool:
    if name in sys.stdlib_module_names: return True
    if name in ("core", "modules", "rpg_engine_v5", "world_map"): return True
    return importlib.util.find_spec(name) is not None


# ── Check runners ─────────────────────────────────────────────

def run_file_checks(files: list[str], label: str) -> bool:
    header(f"📁 {label} — File Check")
    all_ok = True
    for filepath in files:
        if not os.path.isfile(filepath):
            fail(f"{filepath}  →  File not found!")
            all_ok = False
            continue
        syntax_ok, syntax_err = check_syntax(filepath)
        if not syntax_ok:
            fail(f"{filepath}\n       {RED}{syntax_err}{RESET}")
            all_ok = False
            continue
        import_errors = check_imports(filepath)
        if import_errors:
            fail(f"{filepath}")
            for ie in import_errors:
                print(f"       {RED}• {ie}{RESET}")
            all_ok = False
        else:
            ok(filepath)
    return all_ok

def run_env_checks(required: list, optional: list, label: str) -> bool:
    header(f"🔑 {label} — Environment Variables")
    all_ok = True
    for var, note in required:
        if os.getenv(var):
            ok(f"{var}  ✓")
        else:
            fail(f"{var}  →  Not set! {note}")
            all_ok = False
    print()
    for var, note in optional:
        if os.getenv(var):
            ok(f"{var}  ✓  (optional)")
        else:
            warn(f"{var}  →  Not set — {note}")
    return all_ok

def run_package_checks(packages: list, label: str) -> bool:
    header(f"📦 {label} — Packages")
    all_ok = True
    for import_name, pkg_name in packages:
        if importlib.util.find_spec(import_name):
            ok(pkg_name)
        else:
            fail(f"{pkg_name}  →  Not installed! pip install {pkg_name}")
            all_ok = False
    return all_ok


# ── Main ──────────────────────────────────────────────────────

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"

    print(f"\n{BOLD}{'='*55}")
    print("  Nixon Bot Suite — Health Check")
    print(f"{'='*55}{RESET}")
    print(f"  Working directory: {os.getcwd()}")
    print(f"  Mode: {mode}\n")

    results = []

    if mode in ("both", "nixon"):
        print(f"\n{BOLD}{'='*55}")
        print("  🤖 NIXON BOT")
        print(f"{'='*55}{RESET}")
        r1 = run_file_checks(NIXON_FILES, "Nixon Bot")
        r2 = run_env_checks(NIXON_REQUIRED_ENV, NIXON_OPTIONAL_ENV, "Nixon Bot")
        r3 = run_package_checks(NIXON_PACKAGES, "Nixon Bot")
        results.append(("Nixon Bot", r1 and r2 and r3))

    if mode in ("both", "rpg"):
        print(f"\n{BOLD}{'='*55}")
        print("  ⚔️  NIXON RPG BOT")
        print(f"{'='*55}{RESET}")
        r1 = run_file_checks(RPG_FILES, "RPG Bot")
        r2 = run_env_checks(RPG_REQUIRED_ENV, RPG_OPTIONAL_ENV, "RPG Bot")
        r3 = run_package_checks(RPG_PACKAGES, "RPG Bot")
        results.append(("RPG Bot", r1 and r2 and r3))

    # Summary
    print(f"\n{BOLD}{'='*55}")
    print("  SUMMARY")
    print(f"{'='*55}{RESET}")
    all_passed = True
    for name, passed in results:
        if passed:
            ok(f"{name} — Ready to run!")
        else:
            fail(f"{name} — Issues found. Fix before running.")
            all_passed = False

    print(f"{BOLD}{'='*55}{RESET}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
