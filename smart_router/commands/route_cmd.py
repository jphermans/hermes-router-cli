"""`hr route` — pick a model and (optionally) call it."""
from __future__ import annotations

import argparse
import json
import sys

from ..route import route


def add_subparser(sub):
    p = sub.add_parser("route", help="Route a prompt to the cheapest capable model.")
    p.add_argument("--prompt", "-p", required=True,
                   help="The prompt to send.")
    p.add_argument("--tier", choices=["cheap", "standard", "pro"], default=None,
                   help="Force a capability tier (overrides the heuristic classifier).")
    p.add_argument("--class", dest="cost_class",
                   choices=["free", "paid", "any"], default="any",
                   help="Pick a cost pool: free (subscription), paid (billed), any (default).")
    p.add_argument("--max-tokens", type=int, default=512,
                   help="Max output tokens (capped by policy.max_output_tokens).")
    p.add_argument("--system", default="", help="Optional system prompt.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the ranked plan without making any API call.")
    p.add_argument("--verify", action="store_true",
                   help="Probe providers with a 1-token ping first to skip dead keys/models.")
    p.add_argument("--image", action="append", default=[], dest="images",
                   help="Image URL or data URL (repeatable). Forces vision-capable models.")
    p.add_argument("--vision", action="store_true",
                   help="Force vision-capable model selection.")
    p.add_argument("--pretty", action="store_true",
                   help="Render a human-readable summary instead of raw JSON.")
    p.add_argument("--auto-fallback", action="store_true",
                   help="If --class free fails, automatically retry with --class paid.")
    p.add_argument("--max-cost", type=float, default=None, metavar="USD",
                   help="Skip candidates whose estimated cost exceeds this (USD).")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    # `--verify` is a soft "skip dead keys first" hint. Implemented by pre-probing
    # the candidate pool via build_providers + probe, then short-circuiting a
    # pointer (simpler: just rerun select() with verify=True).
    if args.verify:
        from ..route import select
        try:
            plan, tier, debug = select(
                args.prompt, force_tier=args.tier, max_out=args.max_tokens,
                force_vision=args.vision or bool(args.images),
                cost_class=args.cost_class,
            )
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        from ..route import probe
        kept = []
        skipped = []
        for c in plan:
            if probe(c):
                kept.append(c)
            else:
                skipped.append((c.provider, c.model))
        for prov, mdl in skipped:
            print(f"skipped (dead): {prov}/{mdl}", file=sys.stderr)
        # Replace plan with verified-only
        args._verified_plan = kept
    result = route(
        args.prompt,
        force_tier=args.tier,
        max_out=args.max_tokens,
        dry_run=args.dry_run,
        system=args.system,
        cost_class=args.cost_class,
        images=args.images or None,
        force_vision=args.vision,
        auto_fallback=args.auto_fallback,
        max_cost_usd=args.max_cost,
    )
    if args.pretty:
        print(_render_pretty(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") or result.get("dry_run") else 1


def _render_pretty(r: dict) -> str:
    if r.get("dry_run"):
        lines = [f"[dry-run] classified as {r['classified_tier']}  cost_class={r.get('cost_class','any')}"]
        for p in r["plan"]:
            q = "👁 " if p.get("vision") else "  "
            lines.append(f"  {p['rank']:>2}. {q}{p['provider']:<18} {p['model']:<48} "
                         f"[{p['tier']:<8}/{p['cost_class']:<4}]  ${p['est_cost_usd']:.6f}")
        return "\n".join(lines)
    if r.get("ok"):
        chain = ""
        if r.get("auto_fallback_used"):
            chain = " (via auto-fallback from free → paid)"
        elif r.get("via_curated_chain"):
            chain = " (via curated chain)"
        elif r.get("via_broader_fallback"):
            chain = " (via broader sweep)"
        return (
            f"✓ {r['selected_provider']}/{r['selected_model']}{chain}  "
            f"(~${r['est_cost_usd']:.6f}, {len(r.get('fallbacks_tried', []))} tries)\n\n"
            f"{r['response']}"
        )

    # Failure mode — show the chain, mark permanents visibly.
    tried = r.get("fallbacks_tried", [])
    summary_lines = []
    n_perm = sum(1 for t in tried if t.get("permanent"))
    summary_lines.append(f"✗ {r.get('error', 'unknown error')}")
    if n_perm:
        summary_lines.append(f"  ({n_perm} failure(s) were permanent — skipped all keys & retried the chain)")
    if r.get("circuit_skipped"):
        summary_lines.append(
            f"  (circuit-breaker skipped {len(r['circuit_skipped'])} candidate(s) that failed repeatedly)"
        )
    summary_lines.append("")
    summary_lines.append("Attempts in order:")
    for i, t in enumerate(tried, 1):
        perm = "  [permanent]" if t.get("permanent") else ""
        http = f"  HTTP {t.get('http_code')}" if t.get("http_code") else ""
        summary_lines.append(
            f"  {i:>2}. {t.get('provider','?'):<14} {t.get('model','?'):<46} "
            f"key={t.get('key_index', '-')}{http}{perm}"
        )
        err = (t.get("error") or "")
        if err:
            summary_lines.append(f"        → {err[:200]}")
    return "\n".join(summary_lines)
