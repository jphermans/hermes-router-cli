"""`hr budget` — show the running spend tracker."""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from ..budget import spend_for_current_month, spend_for_months


def add_subparser(sub):
    p = sub.add_parser("budget",
                       help="Show this month's spend per provider.")
    p.add_argument("--json", action="store_true",
                   help="Output as JSON (machine-readable).")
    p.add_argument("--last", type=int, default=1, metavar="N",
                   help="Show last N months (default: 1).")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    if args.last > 1:
        data = spend_for_months(args.last)
    else:
        data = {datetime.now().strftime("%Y-%m"): spend_for_current_month()}

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    total_all = 0.0
    for month in sorted(data.keys(), reverse=True):
        spend = data[month]
        if not spend:
            print(f"({month}: no recorded spend yet)")
            continue
        print(f"── {month} ──")
        total = 0.0
        for prov, cost in sorted(spend.items(), key=lambda x: -x[1]):
            total += cost
            total_all += cost
            print(f"  {prov:<20}  ${cost:>10.4f}")
        print(f"  {'TOTAL':<20}  ${total:>10.4f}")
        print()

    if len(data) > 1:
        print(f"── ALL MONTHS ──")
        print(f"  {'TOTAL':<20}  ${total_all:>10.4f}")
    return 0
