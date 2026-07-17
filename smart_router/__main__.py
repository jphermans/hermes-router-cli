"""
hermes-router CLI.

Run as: python -m smart_router <sub-command> [args...]

Sub-commands:
  route      pick a model and (optionally) call it
  models     list all configured models
  verify     probe every configured (provider,model) with a 1-token ping
  auth       show which providers have keys configured
  doctor     quick configuration health check
  budget     show this month's spend per provider
  chat       interactive REPL — type prompts, get answers
  init       interactive setup wizard for new users
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from . import __version__


# ─────────────────────────────────────────────────────────────────────────────
# ANSI color helpers — only used when stdout is a TTY (so non-tty output
# stays script-friendly).
# ─────────────────────────────────────────────────────────────────────────────

def _supports_color() -> bool:
    """Return True if we should emit ANSI colors."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


_COLOR = _supports_color()


class C:
    """ANSI color codes. Empty strings when color is disabled."""
    BOLD = "\033[1m" if _COLOR else ""
    DIM = "\033[2m" if _COLOR else ""
    RED = "\033[31m" if _COLOR else ""
    GREEN = "\033[32m" if _COLOR else ""
    YELLOW = "\033[33m" if _COLOR else ""
    BLUE = "\033[34m" if _COLOR else ""
    MAGENTA = "\033[35m" if _COLOR else ""
    CYAN = "\033[36m" if _COLOR else ""
    WHITE = "\033[37m" if _COLOR else ""
    BGBLUE = "\033[44m" if _COLOR else ""
    RESET = "\033[0m" if _COLOR else ""


def _c(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles. No-op if color disabled."""
    if not _COLOR or not styles:
        return text
    codes = "".join(s for s in styles if s)
    return f"{codes}{text}{C.RESET}"


def _icon(symbol: str) -> str:
    """Icon prefix — wrapped in dim so it doesn't shout in monospace."""
    return _c(symbol, C.DIM)


# ─────────────────────────────────────────────────────────────────────────────
# Command metadata — icons + one-liners for each subcommand
# ─────────────────────────────────────────────────────────────────────────────

COMMAND_META = {
    "route":   ("🎯", "Send a prompt; cheapest capable model answers it",
                "hr route --prompt \"Translate hi\" --class free --pretty"),
    "models":  ("📚", "List all configured providers and their models",
                "hr models --class free"),
    "verify":  ("🔬", "Probe every (provider, model) with a 1-token ping",
                "hr verify    # ~40 calls; takes a minute"),
    "auth":    ("🔐", "Show which providers have keys configured",
                "hr auth      # masked | --show to see last 4 chars"),
    "doctor":  ("🩺", "Health check — config, keys, coverage (no API calls)",
                "hr doctor           # summary\n"
                "hr doctor --verbose # per-provider details"),
    "budget":  ("💰", "Show your tracked monthly spend per provider",
                "hr budget           # current month\n"
                "hr budget --last 3  # multi-month view\n"
                "hr budget --json    # machine-readable"),
    "chat":    ("💬", "Interactive REPL — type prompts, get answers",
                "hr chat --class free\n"
                "hr chat --class paid --auto-fallback\n"
                "hr chat --class free --show-cost   # track session spend"),
    "init":    ("🚀", "Interactive setup wizard for new users",
                "hr init              # pick providers, paste keys\n"
                "hr init --yes        # non-interactive (defaults)"),
}


def _load_commands():
    """Lazy-import each sub-command module + register it."""
    from .commands import route_cmd, models_cmd, verify_cmd, auth_cmd, doctor_cmd, budget_cmd, chat_cmd, init_cmd
    return {
        "route": route_cmd,
        "models": models_cmd,
        "verify": verify_cmd,
        "auth": auth_cmd,
        "doctor": doctor_cmd,
        "budget": budget_cmd,
        "chat": chat_cmd,
        "init": init_cmd,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rich help formatter
# ─────────────────────────────────────────────────────────────────────────────

def _print_subcommand_help(sub_action) -> None:
    """Pretty-print a single subcommand's arguments (extracted from its parser)."""
    # sub_action._subparsers_action contains the choices; we want the inner parser.
    # argparse doesn't expose the subparser cleanly, so we use format_help().
    subparser = sub_action
    # Reach into the subparser to get its formatter output
    try:
        # Newer Python: sub_action.choices[name] gives the subparser
        # We get it via format_help() redirected to a string
        from io import StringIO
        buf = StringIO()
        # Save and replace stdout temporarily
        old = sys.stdout
        try:
            sys.stdout = buf
            subparser.print_help()
        finally:
            sys.stdout = old
        text = buf.getvalue().rstrip()
    except Exception:
        text = ""

    # Parse the help text: skip the "usage:" line, take the rest
    lines = text.splitlines()
    args_lines = []
    in_args = False
    for line in lines:
        if line.startswith("options:") or line.startswith("positional arguments:"):
            in_args = True
            args_lines.append(line)
            continue
        if in_args:
            if line.strip() == "" and args_lines and args_lines[-1].strip() == "":
                continue
            args_lines.append(line)
    # Strip trailing empties
    while args_lines and not args_lines[-1].strip():
        args_lines.pop()

    # Color the "options:" header and arg names
    colored = []
    for line in args_lines:
        if line.startswith("options:"):
            colored.append(_c("  " + line, C.BOLD, C.CYAN))
        elif line.strip().startswith("--") or line.strip().startswith("-"):
            # Indented flag — color the flag name
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            parts = stripped.split(" ", 1)
            if parts and parts[0].startswith("-"):
                flag = _c(parts[0], C.GREEN, C.BOLD)
                rest = parts[1] if len(parts) > 1 else ""
                colored.append(f"{indent}{flag} {rest}")
            else:
                colored.append(line)
        else:
            colored.append(line)
    print("\n".join(colored))


def _print_rich_help(parser: argparse.ArgumentParser) -> None:
    """Custom help output with icons, colors, and full per-command argument lists."""
    # Header
    bar = _c("─" * 70, C.DIM)
    print(bar)
    print(_c("🪶 ", C.CYAN) + _c(f"hermes-router v{__version__}", C.BOLD, C.CYAN))
    print(_c("    cheap-or-capable LLM routing from one CLI", C.DIM))
    print(bar)
    print()

    # Usage line
    print(_c("USAGE", C.BOLD) + _c("  ", C.DIM) + _c("hr [-h] [--version] <command> [args...]", C.WHITE))
    print()

    # ── 📋 QUICK REFERENCE ───────────────────────────────────────────────
    print(_c("📋  QUICK REFERENCE", C.BOLD, C.CYAN))
    print(_c("    " + "─" * 60, C.DIM))
    print(_c("    Setup (one time):", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr doctor" + _c("          # check health", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr init" + _c("            # interactive setup wizard", C.DIM))
    print(_c("    Daily use:", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr route --prompt \"...\" --class free" + _c("    # cheapest free model", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr route --prompt \"...\" --class paid" + _c("    # cheapest paid model", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr chat --class free" + _c("             # interactive REPL", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr models" + _c("                   # see all available models", C.DIM))
    print(_c("    Monitoring:", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr doctor --verbose" + _c("        # per-provider details", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr budget --last 3" + _c("          # spending by month", C.DIM))
    print(_c("      $ ", C.GREEN) + "hr verify" + _c("                  # live probe of every model", C.DIM))
    print()

    # ── 🔧 SUB-COMMANDS ──────────────────────────────────────────────────
    print(_c("🔧  SUB-COMMANDS", C.BOLD, C.CYAN))
    print(_c("    " + "─" * 60, C.DIM))

    # Build a list of registered subcommands and their parsers
    sub_actions = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub_actions.append(action)
            break

    if sub_actions:
        sub_action = sub_actions[0]
        # Iterate in registered order, not sorted (so route, models, ..., init)
        for name in sub_action.choices:
            subparser = sub_action.choices[name]
            icon, descr, _example = COMMAND_META.get(name, ("•", "", ""))
            cmd_label = _c(name, C.BOLD, C.MAGENTA)
            icon_label = _c(icon, C.YELLOW)
            print(f"    {icon_label}  {cmd_label}" + _c(f"  {descr}", C.WHITE))
            # Extract and show arguments
            _print_subcommand_help(subparser)
            print()

    # ── 💡 COMMON FLAGS ──────────────────────────────────────────────────
    print(_c("💡  COMMON FLAGS (work on most commands)", C.BOLD, C.YELLOW))
    print(_c("    " + "─" * 60, C.DIM))
    print(_c("      --class free|paid|any        ", C.GREEN) + _c("Pick a cost pool (default: any)", C.WHITE))
    print(_c("      --tier cheap|standard|pro   ", C.GREEN) + _c("Force capability tier (override classifier)", C.WHITE))
    print(_c("      --pretty                    ", C.GREEN) + _c("Color + structured output for humans", C.WHITE))
    print(_c("      --json                      ", C.GREEN) + _c("Machine-readable output", C.WHITE))
    print(_c("      --max-tokens N              ", C.GREEN) + _c("Max output tokens (default: 512)", C.WHITE))
    print(_c("      --max-cost USD              ", C.GREEN) + _c("Per-call cost cap (skip expensive candidates)", C.WHITE))
    print()

    # ── 🔀 AUTO-FALLBACK & COST CONTROL ─────────────────────────────────
    print(_c("🔀  AUTO-FALLBACK & COST CONTROL", C.BOLD, C.YELLOW))
    print(_c("    " + "─" * 60, C.DIM))
    print(_c("      --auto-fallback             ", C.GREEN) + _c("If --class free fails, retry with --class paid", C.WHITE))
    print(_c("      --max-cost USD              ", C.GREEN) + _c("Skip candidates whose estimated cost exceeds this", C.WHITE))
    print()

    # ── 📚 MORE INFO ─────────────────────────────────────────────────────
    print(_c("📚  MORE INFO", C.BOLD, C.CYAN))
    print(_c("    " + "─" * 60, C.DIM))
    print(_c("      🌐  ", C.BLUE) + _c("Web:    ", C.WHITE) + "https://github.com/jphermans/hermes-router-cli")
    print(_c("      ❓  ", C.BLUE) + _c("Help:   ", C.WHITE) + "hr <command> --help" + _c("     # detailed options per command", C.DIM))
    print(_c("      📦  ", C.BLUE) + _c("Version:", C.WHITE) + f" hr --version")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Argparse plumbing
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hr",
        description="hermes-router — cheap-or-capable LLM routing from one CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,  # we add our own rich --help action below
    )
    p.add_argument("--version", action="version",
                   version=f"hermes-router {__version__}")

    # Override the default --help action to use our rich formatter.
    # We keep -h working (it goes through the same help action).
    class _RichHelpAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            _print_rich_help(parser)
            parser.exit()

    p.add_argument("-h", "--help", action=_RichHelpAction, nargs=0,
                   help="Show this help message with all sub-commands.")

    sub = p.add_subparsers(dest="command", required=False, metavar="")

    cmds = _load_commands()
    for name, mod in cmds.items():
        try:
            mod.add_subparser(sub)
        except Exception as e:
            print(f"failed to register {name}: {e}", file=sys.stderr)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # Optional shell completion via argcomplete (bash/zsh/fish).
    # The library reads argparse definitions automatically — no hand-maintained
    # completion strings. If the package isn't installed, skip silently.
    try:
        import argcomplete  # type: ignore
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Use our rich help instead of argparse's default
        _print_rich_help(parser)
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
