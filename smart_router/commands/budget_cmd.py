"""`hr budget` — show the running spend tracker."""
from __future__ import annotations

import argparse
from datetime import datetime

from ..budget import spend_for_current_month


def add_subparser(sub):
    p = sub.add_parser("budget",
                       help="Show this month's spend per provider.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    spend = spend_for_current_month()
    month = datetime.now().strftime("%Y-%m")
    if args.json:
        import json
        print(json.dumps({"month": month, "spend_usd": spend}, indent=2))
        return 0
    if not spend:
        print(f"({month}: no recorded spend yet)")
        return 0
    total = 0.0
    for prov, cost in sorted(spend.items(), key=lambda x: -x[1]):
        total += cost
        print(f"  {prov:<20}  ${cost:>10.4f}")
    print(f"  {'TOTAL':<20}  ${total:>10.4f}")
    return 0
