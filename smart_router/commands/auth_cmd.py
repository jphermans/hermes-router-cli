"""`hr auth` — show which providers have keys and how many."""
from __future__ import annotations

import argparse
import os
import sys

from ..providers import build_providers, load_config, list_keys_for


def add_subparser(sub):
    p = sub.add_parser("auth",
                       help="Show which providers have keys configured in the environment.")
    p.add_argument("--show", action="store_true",
                   help="Print the actual key values. Default only shows counts and last 4 chars.")
    p.add_argument("--env-file",
                   default=os.path.expanduser("~/.hermes/.env"),
                   help="Path to the env file to scan for keys (default: ~/.hermes/.env).")
    p.set_defaults(func=run)
    return p


def _parse_env(path: str) -> dict[str, str]:
    """Read a .env-style file into a dict. No shell-side-effect — pure parse."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def run(args: argparse.Namespace) -> int:
    cfg = load_config()
    providers = build_providers(cfg, only_with_keys=False)  # include those WITHOUT keys

    env_file_vars = _parse_env(args.env_file)
    env_file_keys = {k for k in env_file_vars if "API_KEY" in k or "TOKEN" in k}

    print(f"{'provider':<20} {'class':<6} {'env_key':<28} {'keys in env':<12} {'in .env file'}")
    print("-" * 95)
    for p in providers:
        n = len(p.keys)
        in_file = "yes" if p.env_key in env_file_keys or any(k in env_file_vars for k in p.keys or []) else "no"
        print(f"{p.name:<20} {p.cost_class:<6} {p.env_key:<28} {n:<12} {in_file}")
        if args.show and p.keys:
            for i, k in enumerate(p.keys):
                masked = f"****{k[-4:]}" if len(k) > 4 else "****"
                print(f"    └─ key {i+1}: {masked}")
    if not providers:
        print("(no providers configured — check config.yaml)", file=sys.stderr)
        return 1
    return 0
