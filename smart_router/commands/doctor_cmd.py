"""`hr doctor` — quick sanity checks: keys present, config valid, env file readable."""
from __future__ import annotations

import argparse
import os
import sys

from ..providers import (
    COST_CLASS_FREE, COST_CLASS_PAID,
    build_providers, expand_candidates, load_config,
)


def add_subparser(sub):
    p = sub.add_parser("doctor",
                       help="Diagnose configuration problems. Safe to run — makes no API calls.")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show per-provider details (keys, models, base_url).")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    cfg_path = None
    from pathlib import Path
    try:
        from ..providers import CONFIG_PATH
        cfg_path = CONFIG_PATH
    except Exception:
        pass

    findings: list[dict] = []

    # 1. Config file
    if cfg_path and cfg_path.exists():
        try:
            cfg = load_config()
            findings.append({
                "check": "config.yaml",
                "ok": True,
                "detail": f"loaded {len(cfg.get('providers', {}) or {})} providers",
            })
        except ImportError as e:
            # PyYAML-missing case. Surface a clear "run install.py" hint instead of
            # burying the answer in a generic check failure.
            findings.append({"check": "PyYAML installed", "ok": False,
                             "detail": str(e)})
            return _emit(args, findings)
        except Exception as e:
            findings.append({
                "check": "config.yaml valid YAML",
                "ok": False, "detail": str(e),
            })
            return _emit(args, findings)
    else:
        findings.append({"check": "config.yaml", "ok": False,
                         "detail": f"missing at {cfg_path}"})

    # If cfg wasn't loaded (file missing branch), set cfg={} so the rest
    # of the function can still run consistently.
    if 'cfg' not in dir() or 'cfg' not in locals():
        cfg = {}

    # 2. Providers with at least one key
    providers_all = build_providers(cfg, only_with_keys=False)
    providers_active = build_providers(cfg, only_with_keys=True)

    findings.append({
        "check": "providers with keys",
        "ok": bool(providers_active),
        "detail": f"{len(providers_active)}/{len(providers_all)} have at least one key configured",
    })

    # 3. Cost-class coverage
    free_providers = [p for p in providers_active if p.cost_class == COST_CLASS_FREE]
    paid_providers = [p for p in providers_active if p.cost_class == COST_CLASS_PAID]
    findings.append({
        "check": "cost-class coverage",
        "ok": True,
        "detail": f"free: {len(free_providers)} providers, paid: {len(paid_providers)} providers",
    })

    free_cands = expand_candidates(providers_active, cost_class=COST_CLASS_FREE)
    paid_cands = expand_candidates(providers_active, cost_class=COST_CLASS_PAID)
    if not free_cands:
        findings.append({
            "check": "free pool usable",
            "ok": False,
            "detail": "no free-tier candidates available — `hr route --class free` will fail",
        })
    else:
        findings.append({"check": "free pool usable", "ok": True,
                         "detail": f"{len(free_cands)} free model(s) available"})
    if not paid_cands:
        findings.append({
            "check": "paid pool usable",
            "ok": False,
            "detail": "no paid candidates available — `hr route --class paid` will fail",
        })
    else:
        findings.append({"check": "paid pool usable", "ok": True,
                         "detail": f"{len(paid_cands)} paid model(s) available"})

    # 4. OpenAI-compatible base_urls sanity
    bad_urls = [p.name for p in providers_active if not p.base_url.startswith("http")]
    findings.append({
        "check": "all providers have base_url",
        "ok": not bad_urls,
        "detail": "missing: " + ", ".join(bad_urls) if bad_urls else "all set",
    })

    # 5. Verbose: per-provider details
    if args.verbose:
        provider_details = []
        for p in providers_all:
            details = {
                "provider": p.name,
                "class": p.cost_class,
                "base_url": p.base_url,
                "has_keys": len(p.keys) > 0,
                "key_count": len(p.keys),
                "models": [m.name for m in p.models],
            }
            provider_details.append(details)
        findings.append({
            "check": "provider details (--verbose)",
            "ok": True,
            "detail": provider_details,
        })

    return _emit(args, findings)


def _emit(args: argparse.Namespace, findings: list[dict]) -> int:
    if args.json:
        import json
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            mark = "✓" if f["ok"] else "✗"
            if f["check"] == "provider details (--verbose)":
                # Verbose output — print per-provider table
                details = f["detail"]
                if not details:
                    print(f"{mark} provider details  (no providers configured)")
                    continue
                print(f"{mark} provider details:")
                for p in details:
                    key_status = "🔑" if p["has_keys"] else "❌"
                    models_short = ", ".join(p["models"][:4])
                    if len(p["models"]) > 4:
                        models_short += f" … ({len(p['models'])} total)"
                    print(f"     {p['provider']:<18} {p['class']:<5} {key_status} "
                          f"{p['key_count']} key(s)  {p['base_url']}")
                    print(f"     {'':<18} {'':<5}  models: {models_short}")
            else:
                print(f"{mark} {f['check']:<25}  {f['detail']}")
    return 0 if all(f["ok"] for f in findings) else 1
