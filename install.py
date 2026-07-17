#!/usr/bin/env python3
"""Colorful one-shot installer for hermes-router.

Sets up:
  - A .venv with PyYAML (idempotent)
  - A `hr` symlink in ~/.local/bin (optional)
  - A post-install health check via `hr doctor`

Output degrades gracefully to plain text when --no-color is passed or when
stdout is not a TTY. Has zero non-stdlib imports so it runs even before the
venv is created.

Usage:
    python3 install.py                  # full auto-install
    python3 install.py --no-symlink     # skip the ~/.local/bin link
    python3 install.py --no-color       # plain text output
    python3 install.py --help
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / "bin" / "python"
LOCAL_BIN = Path.home() / ".local" / "bin"
SYMLINK = LOCAL_BIN / "hr"
HERE = Path(__file__).resolve().parent


# ─── Output helpers (zero deps, ANSI-only) ─────────────────────────────────

USE_COLOR = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


# Auto-detect NO_COLOR (https://no-color.org/) — any value means "off".
# Apply BEFORE we instantiate any class with ANSI codes so colors never leak.
if os.environ.get("NO_COLOR") is not None:
    USE_COLOR = False


class Style:
    BOLD = "\033[1m" if USE_COLOR else ""
    DIM = "\033[2m" if USE_COLOR else ""
    RED = "\033[31m" if USE_COLOR else ""
    GREEN = "\033[32m" if USE_COLOR else ""
    YELLOW = "\033[33m" if USE_COLOR else ""
    BLUE = "\033[34m" if USE_COLOR else ""
    MAGENTA = "\033[35m" if USE_COLOR else ""
    CYAN = "\033[36m" if USE_COLOR else ""
    BGBLUE = "\033[44m" if USE_COLOR else ""
    RESET = "\033[0m" if USE_COLOR else ""


def _c(text: str, *styles: str) -> str:
    if not USE_COLOR or not styles:
        return text
    # Strip nested reset codes to avoid clobbering outer styles
    codes = "".join(s for s in styles if s)
    return f"{codes}{text}{Style.RESET}"


def banner(text: str) -> None:
    bar = "─" * max(60, len(text) + 4)
    print()
    print(_c(bar, Style.CYAN))
    print(_c(f"  {text}", Style.BOLD, Style.CYAN))
    print(_c(bar, Style.CYAN))
    print()


def info(msg: str) -> None:
    print(_c("  →  ", Style.DIM) + msg)


def step(n: int, total: int, msg: str) -> None:
    counter = _c(f"[{n}/{total}]", Style.DIM, Style.CYAN)
    print(f"  {counter} {msg}")


def ok(msg: str) -> None:
    print(_c("  ✓  ", Style.GREEN, Style.BOLD) + msg)


def warn(msg: str) -> None:
    print(_c("  !  ", Style.YELLOW, Style.BOLD) + msg)


def err(msg: str) -> None:
    print(_c("  ✗  ", Style.RED, Style.BOLD) + msg)


def highlight(msg: str) -> None:
    print(_c(msg, Style.BOLD, Style.MAGENTA))


def dim(msg: str) -> None:
    print(_c(f"  {msg}", Style.DIM))


def run_subprocess(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, **kw)


def humanize_path(p: Path) -> str:
    """Replace $HOME prefix with ~ for shorter display."""
    s = str(p)
    home = str(Path.home())
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


# ─── Steps ───────────────────────────────────────────────────────────────────

def step_create_venv(n: int = 1, total: int = 5) -> None:
    step(n, total, "Creating virtual environment")
    if VENV.exists():
        info(_c(f"Already exists at {humanize_path(VENV)} — reusing.", Style.DIM))
        ok("Virtual environment")
        return
    if sys.platform == "win32":
        venv_py = VENV / "Scripts" / "python.exe"
    else:
        venv_py = VENV / "bin" / "python"
    r = run_subprocess([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode != 0:
        err("Failed to create venv")
        print(r.stderr)
        sys.exit(1)
    ok(f"Created venv at {humanize_path(VENV)}")
    if not venv_py.exists():
        warn(f"venv created but python at {venv_py} not found?")


def step_install_deps(n: int = 2, total: int = 5) -> None:
    step(n, total, "Installing dependencies (PyYAML)")
    if not PYTHON.exists():
        # fallback to venv/Scripts/python.exe on Windows
        py = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    else:
        py = PYTHON
    if not py.exists():
        err(f"Cannot find venv python at {py}")
        sys.exit(1)
    r = run_subprocess([str(py), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    if r.returncode != 0:
        err("pip install failed")
        print(r.stderr)
        sys.exit(1)
    ok("Dependencies installed")


def step_chmod_hr(n: int = 3, total: int = 5) -> None:
    step(n, total, "Making hr launcher executable")
    hr = ROOT / "hr"
    hr.chmod(0o755)
    ok(f"{humanize_path(hr)} marked executable")


def step_symlink(n: int = 4, total: int = 5) -> None:
    step(n, total, "Creating ~/.local/bin/hr symlink")
    hr = ROOT / "hr"
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    if SYMLINK.is_symlink():
        existing_target = os.readlink(SYMLINK)
        if Path(existing_target).resolve() == hr.resolve():
            info(_c(f"Already symlinked → {humanize_path(hr)}", Style.DIM))
            ok("Symlink")
            return
        else:
            info(f"Existing symlink points elsewhere ({existing_target}); updating.")
    elif SYMLINK.exists():
        info(f"Existing file at {SYMLINK}; replacing with symlink.")
        SYMLINK.unlink()
    os.symlink(hr, SYMLINK)
    ok(f"Symlink created: {humanize_path(SYMLINK)} → {humanize_path(hr)}")
    # PATH hint
    if LOCAL_BIN.exists() and os.environ.get("PATH", "").find(str(LOCAL_BIN)) == -1:
        warn(f"{humanize_path(LOCAL_BIN)} is not on your PATH yet")
        dim(f"Add this to your ~/.bashrc / ~/.zshrc:")
        dim(f'    export PATH="{LOCAL_BIN}:$PATH"')
        dim(f"Then open a new terminal or run:  hash -r")


def step_health_check(n: int = 5, total: int = 5) -> None:
    step(n, total, "Health check: hr doctor")
    if not PYTHON.exists():
        return
    r = run_subprocess([str(PYTHON), "-m", "smart_router", "doctor"],
                       cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)})
    if r.returncode != 0 and r.returncode != 1:
        # doctor returns 1 if any check is just "ok=False" but it's still informative.
        if "Traceback" in r.stderr:
            err("doctor failed unexpectedly")
            print(r.stderr[-500:])
            return
    # Re-render with our own color so it stays consistent with installer output.
    if r.stdout.strip():
        # Trim leading whitespace and wrap with our palette
        rendered = r.stdout.rstrip()
        print()
        for line in rendered.splitlines():
            print(f"    {line}")
        print()


# ─── Post-install friendly summary ──────────────────────────────────────────

def print_summary() -> None:
    print()
    print(_c("━" * 72, Style.CYAN))
    print(_c("  All set.", Style.BOLD, Style.GREEN))
    print(_c("━" * 72, Style.CYAN))
    print()
    print(_c("Quick start:", Style.BOLD))
    print()
    print(f"    {_c('hr route', Style.BOLD, Style.CYAN)} --prompt \"Translate hello\" {Style.DIM}# cheapest capable model{Style.RESET}")
    print(f"    {_c('hr route', Style.BOLD, Style.CYAN)} --prompt \"...\" {Style.DIM}--class free   # subscription pool only{Style.RESET}")
    print(f"    {_c('hr route', Style.BOLD, Style.CYAN)} --prompt \"...\" {Style.DIM}--class paid   # billed pool only{Style.RESET}")
    print(f"    {_c('hr models', Style.BOLD, Style.CYAN)}                    {Style.DIM}# list every configured model{Style.RESET}")
    print(f"    {_c('hr auth', Style.BOLD, Style.CYAN)}                      {Style.DIM}# see which API keys are loaded{Style.RESET}")
    print(f"    {_c('hr chat', Style.BOLD, Style.CYAN)}                      {Style.DIM}# interactive REPL{Style.RESET}")
    print()
    print(_c("Need API keys?", Style.BOLD))
    print()
    print("    Copy .env.example to your own .env (or ~/.hermes/.env), then fill in")
    print("    one key per provider. Re-run this installer any time to check health.")
    print()
    print(_c("Docs:", Style.BOLD))
    print(f"    {humanize_path(ROOT / 'README.md')}")
    print()


# ─── Entry point ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install hermes-router into a local venv and link the `hr` CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run from the project root. Re-running is safe (idempotent).",
    )
    ap.add_argument("--no-symlink", action="store_true",
                    help="Skip creating ~/.local/bin/hr symlink")
    ap.add_argument("--no-doctor", action="store_true",
                    help="Skip the post-install doctor health check")
    ap.add_argument("--no-color", action="store_true",
                    help="Disable ANSI colors (plain text output)")
    args = ap.parse_args()

    if args.no_color:
        # Override USE_COLOR + null out every style code so nothing escapes.
        globals()["USE_COLOR"] = False
        for attr in ("BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE",
                     "MAGENTA", "CYAN", "BGBLUE", "RESET"):
            setattr(Style, attr, "")

    # Compute total so skipping steps renumbers [N/M] correctly.
    total_steps = 5 - (1 if args.no_symlink else 0) - (1 if args.no_doctor else 0)

    print()
    banner("hermes-router installer")
    dim(f"Source:   {humanize_path(ROOT)}")
    dim(f"Python:   {sys.executable} ({sys.version.split()[0]})")
    dim(f"Platform: {sys.platform}")
    if not USE_COLOR:
        dim("(output: plain text — pass --no-color to disable ANSI)")
    print()

    n = 0

    n += 1; step_create_venv(n, total_steps)
    n += 1; step_install_deps(n, total_steps)
    n += 1; step_chmod_hr(n, total_steps)
    if not args.no_symlink:
        n += 1; step_symlink(n, total_steps)
    else:
        info(_c("Skipped (--no-symlink). Use ./hr from the project root instead.", Style.DIM))
    if not args.no_doctor:
        n += 1; step_health_check(n, total_steps)

    print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
