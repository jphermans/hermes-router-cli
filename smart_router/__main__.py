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
"""
from __future__ import annotations

import argparse
import importlib
import sys

from . import __version__


def _load_commands():
    """Lazy-import each sub-command module + register it."""
    from .commands import route_cmd, models_cmd, verify_cmd, auth_cmd, doctor_cmd, budget_cmd, chat_cmd
    return {
        "route": route_cmd,
        "models": models_cmd,
        "verify": verify_cmd,
        "auth": auth_cmd,
        "doctor": doctor_cmd,
        "budget": budget_cmd,
        "chat": chat_cmd,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hr",
        description="hermes-router — cheap-or-capable LLM routing from one CLI.",
        epilog="Use `hr <command> --help` for command-specific options.",
    )
    p.add_argument("--version", action="version",
                   version=f"hermes-router {__version__}")
    sub = p.add_subparsers(dest="command", required=False)

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
