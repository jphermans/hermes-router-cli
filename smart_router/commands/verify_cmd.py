"""`hr verify` — probe every configured model with a 1-token ping."""
from __future__ import annotations

import argparse
import sys

from ..providers import (
    COST_CLASS_FREE, COST_CLASS_PAID,
    build_providers, expand_candidates, load_config,
)
from ..route import probe


def add_subparser(sub):
    p = sub.add_parser("verify",
                       help="Probe every configured (provider,model) with a 1-token ping. "
                            "Use this to populate a clean baseline before running `hr route`.")
    p.add_argument("--class", dest="cost_class",
                   choices=["free", "paid", "any"], default="any")
    p.add_argument("--provider", help="Restrict verification to one provider.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    cfg = load_config()
    providers = build_providers(cfg)
    cands = expand_candidates(providers, cost_class=args.cost_class)
    if args.provider:
        cands = [c for c in cands if c.provider == args.provider]

    if not cands:
        print("no candidates to verify (check keys / cost_class filter)", file=sys.stderr)
        return 2

    rows = []
    for c in cands:
        ok = probe(c)
        rows.append({
            "provider": c.provider, "model": c.model,
            "cost_class": c.cost_class, "ok": ok,
        })
        marker = "OK " if ok else "FAIL"
        print(f"  [{marker}] {c.provider:<18} {c.model:<48} [{c.cost_class}]")

    failed = [r for r in rows if not r["ok"]]
    ok = [r for r in rows if r["ok"]]
    print()
    print(f"verified: {len(ok)}/{len(rows)} reachable")
    if failed:
        print(f"  {len(failed)} candidate(s) failed — they will be skipped on next route()", file=sys.stderr)
        return 1
    return 0
