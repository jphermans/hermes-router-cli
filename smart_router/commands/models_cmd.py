"""`hr models` — list every configured model, with filters."""
from __future__ import annotations

import argparse

from ..providers import (
    COST_CLASS_FREE, COST_CLASS_PAID, build_providers, load_config,
)


def add_subparser(sub):
    p = sub.add_parser("models",
                       help="List all configured providers and their models.")
    p.add_argument("--provider", help="Filter by provider name.")
    p.add_argument("--tier", choices=["cheap", "standard", "pro"],
                   help="Filter by capability tier.")
    p.add_argument("--class", dest="cost_class",
                   choices=["free", "paid", "any"], default="any",
                   help="Filter by cost_class.")
    p.add_argument("--with-keys-only", action="store_true",
                   help="Hide providers whose env_key is unset.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of the table.")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    cfg = load_config()
    providers = build_providers(cfg, only_with_keys=not args.with_keys_only)

    rows: list[dict] = []
    for prov in providers:
        for m in prov.models:
            row = {
                "provider": prov.name,
                "model": m.name,
                "tier": m.tier,
                "cost_class": m.cost_class,
                "vision": m.vision,
                "input_price": m.input_price,
                "output_price": m.output_price,
                "context": m.context,
                "keys_available": len(prov.keys),
            }
            if args.provider and prov.name != args.provider:
                continue
            if args.tier and m.tier != args.tier:
                continue
            if args.cost_class != "any" and m.cost_class != args.cost_class:
                continue
            rows.append(row)

    if args.json:
        import json
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'provider':<18} {'model':<46} {'tier':<8} {'class':<5} "
          f"{'vision':<6} {'in/M':<8} {'out/M':<8} {'ctx':<8} {'keys':<4}")
    print("-" * 120)
    for r in rows:
        print(f"{r['provider']:<18} {r['model']:<46} {r['tier']:<8} {r['cost_class']:<5} "
              f"{('YES' if r['vision'] else ''):<6} {r['input_price']:<8} {r['output_price']:<8} "
              f"{r['context']:<8} {r['keys_available']:<4}")
    print(f"\n{len(rows)} models · {len(providers)} providers")
    return 0
