"""`hr chat` — interactive REPL: each line is a prompt routed to the cheapest capable model."""
from __future__ import annotations

import argparse
import sys

from ..route import route


def add_subparser(sub):
    p = sub.add_parser("chat",
                       help="Interactive REPL: type a prompt, get an answer.")
    p.add_argument("--class", dest="cost_class",
                   choices=["free", "paid", "any"], default="any")
    p.add_argument("--tier", choices=["cheap", "standard", "pro"], default=None)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--system", default="")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    print(f"hermes-router chat  (cost_class={args.cost_class}, tier={args.tier or 'auto'}, "
          f"max_tokens={args.max_tokens})")
    print("Type a prompt and press Enter. Empty line or Ctrl-D to exit.\n")

    while True:
        try:
            prompt = input("hr> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        result = route(
            prompt,
            force_tier=args.tier,
            max_out=args.max_tokens,
            system=args.system,
            cost_class=args.cost_class,
        )
        if result.get("ok"):
            print(f"\n[{result['selected_provider']}/{result['selected_model']}]\n{result['response']}\n")
        else:
            print(f"\n✗ {result.get('error')}\n", file=sys.stderr)
    return 0
