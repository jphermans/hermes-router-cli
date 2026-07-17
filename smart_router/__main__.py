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
import sys
from io import StringIO

from . import __version__


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


# Rich description shown by `hr --help`. Includes per-command examples.
DESCRIPTION = """\
hermes-router — cheap-or-capable LLM routing from one CLI.

Routes any prompt to the cheapest model that can answer it, across 11+
OpenAI-compatible providers you already have keys for.

──────────────────────────────────────────────────────────────────────
📋 Quick reference (cheat sheet)
──────────────────────────────────────────────────────────────────────

  Setup (one time):
    hr doctor                                # check health
    hr init                                  # interactive setup wizard

  Daily use:
    hr route --prompt "..." --class free     # cheapest free model
    hr route --prompt "..." --class paid     # cheapest paid model
    hr chat --class free                     # interactive REPL
    hr models                                # see all available models
    hr auth                                  # see which keys are loaded

  Monitoring:
    hr doctor --verbose                      # per-provider details
    hr budget --last 3                       # spending by month
    hr verify                                # live probe of every model

──────────────────────────────────────────────────────────────────────
🔧 Sub-commands (run `hr <cmd> --help` for full options)
──────────────────────────────────────────────────────────────────────

  route     Send a prompt; cheapest capable model answers it
              $ hr route --prompt "Translate hi" --class free --pretty

  chat      Interactive REPL — type prompts, get answers
              $ hr chat --class free --show-cost
              $ hr chat --class paid --auto-fallback

  models    List all configured providers and their models
              $ hr models --class free
              $ hr models --class paid --tier pro

  verify    Probe every (provider, model) with a 1-token ping
              $ hr verify              # ~40 calls; takes a minute

  auth      Show which providers have keys configured
              $ hr auth                # masked values
              $ hr auth --show         # show last 4 chars

  doctor    Health check — config, keys, coverage (no API calls)
              $ hr doctor              # summary
              $ hr doctor --verbose    # per-provider details

  budget    Show your tracked monthly spend per provider
              $ hr budget              # current month
              $ hr budget --last 3     # multi-month view
              $ hr budget --json       # machine-readable

  init      Interactive setup wizard for new users
              $ hr init                # pick providers, paste keys
              $ hr init --yes          # non-interactive (defaults)

──────────────────────────────────────────────────────────────────────
💡 Common flags (work on most commands)
──────────────────────────────────────────────────────────────────────

  --class free|paid|any   Pick a cost pool (default: any)
  --tier cheap|standard|pro  Force capability tier (override classifier)
  --pretty                 Color + structured output for humans
  --json                   Machine-readable output
  --max-tokens N           Max output tokens (default: 512)
  --max-cost USD           Per-call cost cap (skip expensive candidates)

──────────────────────────────────────────────────────────────────────
🔀 Auto-fallback & cost control
──────────────────────────────────────────────────────────────────────

  --auto-fallback          If --class free fails, retry with --class paid
  --max-cost USD           Skip candidates whose estimated cost exceeds this

──────────────────────────────────────────────────────────────────────
📚 More info
──────────────────────────────────────────────────────────────────────

  Web:  https://github.com/jphermans/hermes-router-cli
  Help: hr <command> --help       # detailed options for any command
        hr --version               # show version
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hr",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"hermes-router {__version__}")
    sub = p.add_subparsers(dest="command", required=False,
                            metavar="")

    cmds = _load_commands()
    for name, mod in cmds.items():
        try:
            mod.add_subparser(sub)
        except Exception as e:
            print(f"failed to register {name}: {e}", file=sys.stderr)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
