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
    p.add_argument("--auto-fallback", action="store_true",
                   help="If --class free fails, automatically retry with --class paid.")
    p.add_argument("--max-cost", type=float, default=None, metavar="USD",
                   help="Skip candidates whose estimated cost exceeds this (USD).")
    p.add_argument("--show-cost", action="store_true",
                   help="Show per-call and session cost after each prompt.")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    fallback_note = ", auto-fallback=on" if args.auto_fallback else ""
    cost_note = ", show-cost=on" if args.show_cost else ""
    max_cost_note = f", max-cost=${args.max_cost}" if args.max_cost is not None else ""
    print(f"hermes-router chat  (cost_class={args.cost_class}, tier={args.tier or 'auto'}, "
          f"max_tokens={args.max_tokens}{fallback_note}{cost_note}{max_cost_note})")
    print("Type a prompt and press Enter. Empty line or Ctrl-D to exit.")
    if args.show_cost:
        print("After each prompt: provider, model, cost, session-total.")

    session_cost = 0.0
    call_count = 0

    while True:
        try:
            prompt = input("\nhr> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            if args.show_cost and call_count > 0:
                print(f"Session summary: {call_count} call(s), total ${session_cost:.6f}")
            return 0
        if not prompt:
            continue
        result = route(
            prompt,
            force_tier=args.tier,
            max_out=args.max_tokens,
            system=args.system,
            cost_class=args.cost_class,
            auto_fallback=args.auto_fallback,
            max_cost_usd=args.max_cost,
        )
        if result.get("ok"):
            note = ""
            if result.get("auto_fallback_used"):
                note = " [auto-fallback: free → paid]"
            cost = result.get("est_cost_usd", 0.0) or 0.0
            session_cost += cost
            call_count += 1
            if args.show_cost:
                print(f"[{result['selected_provider']}/{result['selected_model']}] "
                      f"~${cost:.6f} "
                      f"(session: {call_count} call(s), ${session_cost:.6f}){note}")
            else:
                print(f"[{result['selected_provider']}/{result['selected_model']}]{note}")
            print(result["response"])
        else:
            print(f"\n✗ {result.get('error')}\n", file=sys.stderr)
    return 0
