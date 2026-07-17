"""`hr init` — interactive setup wizard for new users.

Walks through:
  1. Checking Python + PyYAML availability
  2. Choosing which providers to enable (free/paid)
  3. Pasting API keys (writes to ~/.hermes/.env if writable)
  4. Validating with `hr doctor`
  5. Showing a summary

This is a *helper*, not a replacement for `install.py` — it just
generates a config.yaml that lists your chosen providers, and offers
to write your keys into ~/.hermes/.env so hr can find them on first run.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Common providers — name → (env_key, base_url, cost_class, sample_models)
PROVIDER_CATALOG = {
    "zai": {
        "env_key": "GLM_API_KEY",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "cost_class": "free",
        "description": "z.ai GLM Coding Plan (free with prepaid subscription)",
    },
    "kilo": {
        "env_key": "KILOCODE_API_KEY",
        "base_url": "https://api.kilo.ai/api/gateway",
        "cost_class": "free",
        "description": "Kilo Code (prepaid monthly)",
    },
    "minimax": {
        "env_key": "MINIMAX_API_KEY",
        "base_url": "https://api.minimax.io/v1",
        "cost_class": "free",
        "description": "Minimax (prepaid)",
    },
    "github_models": {
        "env_key": "GITHUB_TOKEN",
        "base_url": "https://models.inference.ai.azure.com",
        "cost_class": "free",
        "description": "GitHub Models free tier",
    },
    "gemini": {
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "cost_class": "free",
        "description": "Google Gemini (free tier)",
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "cost_class": "paid",
        "description": "DeepSeek (paid)",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "cost_class": "paid",
        "description": "OpenRouter (paid)",
    },
    "xai": {
        "env_key": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "cost_class": "paid",
        "description": "xAI Grok (paid)",
    },
    "nvidia": {
        "env_key": "NVIDIA_API_KEY",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "cost_class": "paid",
        "description": "NVIDIA NIM (paid)",
    },
    "venice": {
        "env_key": "VENICE_API_KEY",
        "base_url": "https://api.venice.ai/api/v1",
        "cost_class": "paid",
        "description": "Venice AI (paid)",
    },
}

ENV_FILE = Path.home() / ".hermes" / ".env"


def _yes_no(prompt: str, default: bool = True) -> bool:
    """Interactive yes/no prompt."""
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt_with_default(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def _mask_key(key: str) -> str:
    """Mask all but the last 4 chars of an API key."""
    if len(key) <= 8:
        return "****"
    return "****" + key[-4:]


def _check_env_writable() -> bool:
    """Check whether ~/.hermes/.env is writable."""
    if not ENV_FILE.exists():
        try:
            ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            return False
    return os.access(ENV_FILE, os.W_OK)


def _append_env_line(line: str) -> bool:
    """Append a single line to ~/.hermes/.env (creates if missing)."""
    try:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ENV_FILE, "a") as f:
            f.write(line + "\n")
        return True
    except Exception as e:
        print(f"  ! Could not write to {ENV_FILE}: {e}", file=sys.stderr)
        return False


def add_subparser(sub):
    p = sub.add_parser("init",
                       help="Interactive setup: pick providers and save API keys.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Use sensible defaults without prompting.")
    p.add_argument("--providers", metavar="p1,p2,...",
                   help="Comma-separated list of providers to enable (default: interactive).")
    p.add_argument("--config", metavar="PATH",
                   help="Write config.yaml to this path (default: project config.yaml).")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    print("=" * 60)
    print("  hermes-router — interactive setup")
    print("=" * 60)
    print()
    print("This wizard helps you pick which providers to enable and")
    print("optionally saves your API keys to ~/.hermes/.env.")
    print()

    # 1. Choose providers
    if args.providers:
        chosen = [p.strip() for p in args.providers.split(",") if p.strip()]
        invalid = [p for p in chosen if p not in PROVIDER_CATALOG]
        if invalid:
            print(f"  ! Unknown provider(s): {', '.join(invalid)}", file=sys.stderr)
            print(f"    Valid options: {', '.join(PROVIDER_CATALOG.keys())}", file=sys.stderr)
            return 2
    elif args.yes:
        # Defaults: all free providers that don't need a key
        chosen = ["zai", "kilo", "minimax", "github_models", "gemini",
                  "deepseek", "openrouter", "xai", "nvidia", "venice"]
    else:
        print("Which providers do you have API keys for?")
        print("(comma-separated names, or 'none' to skip)")
        print()
        print("Available providers:")
        for name, info in PROVIDER_CATALOG.items():
            cls = "🟢" if info["cost_class"] == "free" else "🟡"
            print(f"  {cls} {name:<18} {info['description']}")
        print()
        try:
            answer = input("Providers (e.g. zai,github_models,openrouter): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            answer = ""
        if answer.lower() in ("none", ""):
            print("No providers selected. Run `hr init` again when you have keys.")
            return 0
        chosen = [p.strip() for p in answer.split(",") if p.strip()]
        invalid = [p for p in chosen if p not in PROVIDER_CATALOG]
        if invalid:
            print(f"  ! Unknown provider(s): {', '.join(invalid)}", file=sys.stderr)
            print(f"    Valid options: {', '.join(PROVIDER_CATALOG.keys())}", file=sys.stderr)
            return 2

    print()
    print(f"Selected: {', '.join(chosen)}")
    print()

    # 2. Optional: write keys to ~/.hermes/.env
    env_writable = _check_env_writable()
    keys_written = []
    if env_writable:
        if args.yes or _yes_no("Save API keys to ~/.hermes/.env?", default=True):
            for prov in chosen:
                info = PROVIDER_CATALOG[prov]
                env_key = info["env_key"]
                # Check if already set in env
                if os.environ.get(env_key):
                    print(f"  ✓ {prov}: {env_key} already set in environment")
                    continue
                # Check if already in ~/.hermes/.env
                already_in_env = False
                if ENV_FILE.exists():
                    already_in_env = any(
                        line.startswith(f"{env_key}=")
                        for line in ENV_FILE.read_text().splitlines()
                    )
                if already_in_env:
                    print(f"  ✓ {prov}: {env_key} already in ~/.hermes/.env")
                    continue
                # Prompt
                try:
                    import getpass
                    key = getpass.getpass(f"  {env_key} for {prov}: ")
                except (ImportError, EOFError, KeyboardInterrupt):
                    print()
                    continue
                if not key:
                    print(f"  - {prov}: skipped (no key entered)")
                    continue
                if _append_env_line(f"{env_key}={key}"):
                    keys_written.append(prov)
                    print(f"  ✓ {prov}: saved {env_key}={_mask_key(key)}")
        else:
            print("Skipped key saving. Set them manually:")
            for prov in chosen:
                info = PROVIDER_CATALOG[prov]
                print(f"  export {info['env_key']}=...")
    else:
        print(f"  ! ~/.hermes/.env is not writable — set env vars manually instead")
        for prov in chosen:
            info = PROVIDER_CATALOG[prov]
            print(f"  export {info['env_key']}=...")

    # 3. Optional: write a slim config.yaml
    print()
    config_path = Path(args.config) if args.config else Path("config.yaml")
    if args.yes or _yes_no(f"Write a starter config.yaml to {config_path}?", default=False):
        try:
            _write_starter_config(chosen, config_path)
            print(f"  ✓ Wrote {config_path} with your selected providers")
        except Exception as e:
            print(f"  ! Could not write {config_path}: {e}", file=sys.stderr)

    # 4. Summary
    print()
    print("=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print(f"  1. Run `hr doctor` to verify your setup")
    print(f"  2. Try `hr route --prompt \"Hello\" --class free --pretty`")
    print(f"  3. Or start a chat: `hr chat --class free --show-cost`")
    print()
    if keys_written:
        print(f"Keys saved to {ENV_FILE}: {', '.join(keys_written)}")
    return 0


def _write_starter_config(providers: list, path: Path) -> None:
    """Write a minimal config.yaml with the selected providers."""
    lines = ["providers:"]
    for prov in providers:
        info = PROVIDER_CATALOG[prov]
        lines.append(f"  {prov}:")
        lines.append(f"    env_key: {info['env_key']}")
        lines.append(f"    base_url: {info['base_url']}")
        lines.append(f"    cost_class: {info['cost_class']}")
        lines.append(f"    models: []  # TODO: list model names, prices, vision flag")
    lines.append("")
    lines.append("policy:")
    lines.append("  allow_tier_upgrade: true")
    lines.append("  max_output_tokens: 1024")
    lines.append("  monthly_budget_per_provider: 0")
    lines.append("  parallel_timeout: 15")
    path.write_text("\n".join(lines) + "\n")
