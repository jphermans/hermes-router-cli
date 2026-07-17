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
        findings.append({"check": "config.yaml", "ok": True,
                         "detail": f"loaded {len(cfg.get('providers', {}) or {}) if (cfg := load_config()) else 0} providers"})
    else:
        findings.append({"check": "config.yaml", "ok": False, "detail": f"missing at {cfg_path}"})

    try:
        cfg = load_config()
    except Exception as e:
        findings.append({"check": "config.yaml valid YAML", "ok": False, "detail": str(e)})
        return _emit(args, findings)

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

    return _emit(args, findings)


def _emit(args: argparse.Namespace, findings: list[dict]) -> int:
    if args.json:
        import json
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            mark = "✓" if f["ok"] else "✗"
            print(f"{mark} {f['check']:<25}  {f['detail']}")
    return 0 if all(f["ok"] for f in findings) else 1
