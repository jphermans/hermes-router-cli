"""
Provider registry.

Reads config.yaml, builds typed Provider/Model objects, supports multi-key
rotation per provider. A provider with no key in the env is silently skipped
— the user can show "what would be available if my keys were set" via `hr doctor`.

Multi-key convention (simplified from Shaf2665/Hermes-router):
  Singular:  ZAI_API_KEY=k1
  Plural:    ZAI_API_KEYS=k1,k2,k3   (comma-separated)
  Numbered:  ZAI_API_KEY_2=k2, ZAI_API_KEY_3=k3, ...
All three forms merge automatically and de-duplicate.

cost_class groups providers as "free" (subscription / prepaid / free tier) or
"paid" (USD per 1M tokens). The CLI uses it to filter candidates.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

try:
    import yaml
except ImportError:
    import sys
    sys.exit("Missing dependency: pip install pyyaml")

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE.parent / "config.yaml"

COST_CLASS_FREE = "free"
COST_CLASS_PAID = "paid"


@dataclass
class ModelSpec:
    name: str
    tier: str = "standard"
    input_price: float = 0.0   # USD per 1M input tokens
    output_price: float = 0.0  # USD per 1M output tokens
    context: int = 8192
    vision: bool = False
    cost_class: str = COST_CLASS_FREE  # inherits from provider if unset in YAML


@dataclass
class Provider:
    name: str
    base_url: str
    env_key: str
    cost_class: str = COST_CLASS_FREE
    keys: list[str] = field(default_factory=list)
    models: list[ModelSpec] = field(default_factory=list)

    def visible_models(self, cost_class: Optional[str] = None) -> list[ModelSpec]:
        """Return models visible under the given cost_class filter."""
        if cost_class is None or cost_class == "any":
            return list(self.models)
        return [m for m in self.models if m.cost_class == cost_class]


def _collect_keys(env_var: str) -> list[str]:
    """Merge keys from singular / plural / numbered forms. Deduped, order preserved."""
    collected: list[str] = []
    singular = env_var[:-1] if env_var.endswith("S") else env_var

    if singular != env_var:
        v = os.environ.get(singular, "").strip()
        if v:
            collected.append(v)

    for piece in os.environ.get(env_var, "").split(","):
        piece = piece.strip()
        if piece:
            collected.append(piece)

    i = 2
    while True:
        nv = os.environ.get(f"{singular}_{i}", "").strip()
        if not nv:
            break
        collected.append(nv)
        i += 1

    seen, out = set(), []
    for k in collected:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def load_config(path: Optional[Path] = None) -> dict:
    p = path or CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"No config.yaml at {p}")
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _resolve_cost_class(model_raw: dict, provider_default: str) -> str:
    cc = model_raw.get("cost_class")
    if cc in (COST_CLASS_FREE, COST_CLASS_PAID):
        return cc
    return provider_default


def build_providers(cfg: Optional[dict] = None,
                    only_with_keys: bool = True) -> list[Provider]:
    """Instantiate all providers from config.

    When only_with_keys=True (default), providers with no keys in env are
    skipped — that's how the router auto-filters dead pools at startup.
    """
    if cfg is None:
        cfg = load_config()
    out: list[Provider] = []
    for name, pdata in (cfg.get("providers") or {}).items():
        env_key = (pdata.get("env_key") or "").strip()
        if not env_key:
            continue
        keys = _collect_keys(env_key)
        if only_with_keys and not keys:
            continue
        provider_cc = pdata.get("cost_class", COST_CLASS_FREE)
        if provider_cc not in (COST_CLASS_FREE, COST_CLASS_PAID):
            provider_cc = COST_CLASS_FREE
        models_raw = pdata.get("models") or []
        models = [
            ModelSpec(
                name=m["name"],
                tier=m.get("tier", "standard"),
                input_price=m.get("input_price", 0.0),
                output_price=m.get("output_price", 0.0),
                context=m.get("context", 8192),
                vision=m.get("vision", False),
                cost_class=_resolve_cost_class(m, provider_cc),
            )
            for m in models_raw
        ]
        out.append(Provider(
            name=name,
            base_url=(pdata.get("base_url") or "").rstrip("/"),
            env_key=env_key,
            cost_class=provider_cc,
            keys=keys,
            models=models,
        ))
    return out


def expand_candidates(providers: Iterable[Provider],
                      cost_class: str = "any") -> list["Candidate"]:
    """Cross product of (provider, model) -> list of Candidate.

    cost_class: 'free' | 'paid' | 'any'.
    """
    from .route import Candidate  # local import to avoid circular at module load
    out: list[Candidate] = []
    for prov in providers:
        models = prov.visible_models(cost_class if cost_class != "any" else None)
        for m in models:
            out.append(Candidate(
                provider=prov.name,
                model=m.name,
                tier=m.tier,
                input_price=m.input_price,
                output_price=m.output_price,
                context=m.context,
                vision=m.vision,
                cost_class=m.cost_class,
                base_url=prov.base_url,
                api_keys=list(prov.keys),
            ))
    return out


def list_keys_for(env_key: str) -> list[str]:
    return _collect_keys(env_key)


if __name__ == "__main__":
    cfg = load_config()
    for prov in build_providers(cfg):
        free = sum(1 for m in prov.models if m.cost_class == COST_CLASS_FREE)
        paid = sum(1 for m in prov.models if m.cost_class == COST_CLASS_PAID)
        print(f"{prov.name:18} class={prov.cost_class:4}  "
              f"keys={len(prov.keys):2}  models={len(prov.models):2}  "
              f"(free={free}, paid={paid})")
