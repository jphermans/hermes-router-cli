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

# Hermes keeps its API keys in three places. The router honours all three
# in cascade order so users never have to re-import anything once Hermes
# has their keys:
#   1. process env (KEY, KEYS, KEY_2 …) — set by shell, CI, systemd, etc.
#   2. ~/.hermes/.env                  — written by `hermes auth add`
#   3. ~/.hermes/auth.json             — Hermes' structured credential pool
#
# Hermes' own .env is at ~/.hermes/.env, NOT the project's .env — so we look
# in the user-home Hermes directory by default, but the path can be overridden
# via HERMES_ENV_FILE for tests / container setups / multi-user hosts.
# NOTE: path resolution happens at call time, not import time, so tests can
# override HERMES_ENV_FILE / HERMES_AUTH_FILE at runtime via os.environ. The
# constants kept here are only for documentation / introspection.
HERMES_ENV_PATH_DEFAULT = str(Path.home() / ".hermes" / ".env")
HERMES_AUTH_PATH_DEFAULT = str(Path.home() / ".hermes" / "auth.json")


def _resolve_hermes_env_path() -> Path:
    p = os.environ.get("HERMES_ENV_FILE")
    return Path(p) if p else Path(HERMES_ENV_PATH_DEFAULT)


def _resolve_hermes_auth_path() -> Path:
    p = os.environ.get("HERMES_AUTH_FILE")
    return Path(p) if p else Path(HERMES_AUTH_PATH_DEFAULT)


# Backwards-compat: many callers may still reference these. They are now
# Path objects that RE-EVALUATE on each attribute access via a small trick —
# but for our purposes, a simple re-read on call is safer. Treat the below
# names as the *current* value at module load — if you need the live value
# inside runtime code, prefer calling _resolve_hermes_env_path() /
# _resolve_hermes_auth_path() instead.
HERMES_ENV_PATH = _resolve_hermes_env_path()
HERMES_AUTH_PATH = _resolve_hermes_auth_path()

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


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file. No shell side-effects, no variable expansion."""
    out: dict[str, str] = {}
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
    except Exception:
        pass  # a malformed .env must not break the router
    return out


def _read_auth_json_providers(path: Path) -> dict[str, list[str]]:
    """Extract provider->keys mapping from ~/.hermes/auth.json.

    Hermes stores keys in two places inside auth.json:
      * top-level "providers": {"zai": ["k1","k2"], ...}  (legacy / direct)
      * top-level "credential_pool": [name, name, ...]     (just names, NOT keys)

    Plus each named pool may have additional files. For hermes-router's
    purposes we accept whatever structure is present: any list-of-strings
    under a provider key, and any dict with a "key" / "keys" field at the
    top level mapped by lowercase normalised name.
    """
    out: dict[str, list[str]] = {}
    if not path.exists():
        return out
    try:
        import json
        doc = json.loads(path.read_text())
    except Exception:
        return out
    if not isinstance(doc, dict):
        return out

    # Form 1: {"providers": {"zai": ["k1","k2"], ...}}
    provs = doc.get("providers") or {}
    if isinstance(provs, dict):
        for name, keys in provs.items():
            if isinstance(keys, list):
                cleaned = [str(k).strip() for k in keys if str(k).strip()]
                if cleaned:
                    out[str(name)] = cleaned

    # Form 2: docs with a per-credential "provider" + "api_key" pair.
    # (Some Hermes formats keep both: pools of named credentials.)
    for key in ("credentials", "pool", "items"):
        items = doc.get(key)
        if isinstance(items, list):
            for entry in items:
                if not isinstance(entry, dict):
                    continue
                provider = entry.get("provider") or entry.get("name") or entry.get("id")
                api_key = entry.get("api_key") or entry.get("key") or entry.get("credential")
                if not provider or not api_key:
                    continue
                out.setdefault(str(provider), []).append(str(api_key).strip())
    return out


_AUTH_CACHE: dict[str, dict[str, list[str]]] = {}


def _auth_json_providers_cached(path: Path) -> dict[str, list[str]]:
    """Read auth.json once and cache, but read files fresh on every call
    if the file's mtime changed — so users adding a key via `hermes auth add`
    then re-running `hr route` get the new key without a process restart.
    """
    try:
        mtime = path.stat().st_mtime if path.exists() else 0
    except OSError:
        mtime = 0
    cached = _AUTH_CACHE.get(str(path))
    if cached and cached.get("__mtime__") == mtime:
        return cached["data"]
    data = _read_auth_json_providers(path)
    _AUTH_CACHE[str(path)] = {"__mtime__": mtime, "data": data}
    return data


_ENV_CACHE: dict[str, dict[str, str]] = {}


def _hermes_env_cached(path: Path) -> dict[str, str]:
    """Same mtime-aware caching for ~/.hermes/.env."""
    try:
        mtime = path.stat().st_mtime if path.exists() else 0
    except OSError:
        mtime = 0
    cached = _ENV_CACHE.get(str(path))
    if cached and cached.get("__mtime__") == mtime:
        return cached["data"]
    data = _read_env_file(path)
    _ENV_CACHE[str(path)] = {"__mtime__": mtime, "data": data}
    return data


def _add_to(collected: list[str], seen: set[str], value: str):
    """Helper: add a non-empty, not-already-seen value to a collector list."""
    value = str(value).strip() if value else ""
    if value and value not in seen:
        seen.add(value)
        collected.append(value)


def _add_multi_form(env_map: dict[str, str], var_name: str,
                    collected: list[str], seen: set[str]) -> None:
    """Apply the multi-key conventions (KEY, KEYS, KEY_2...) against a dict-like env.

    Always reads `var_name` literally (works for both KEY and KEYS forms),
    and — only when var_name ends in S — also reads the singular form.
    Always reads numbered suffixes (KEY_2, KEY_3, …) on the singular name.
    """
    # Read var_name itself, comma-split (handles KEYS=k1,k2).
    for piece in env_map.get(var_name, "").split(","):
        _add_to(collected, seen, piece)
    # When the caller passed the plural form (ends in 'S'), also try the singular.
    if var_name.endswith("S"):
        singular = var_name[:-1]
        _add_to(collected, seen, env_map.get(singular, ""))
    else:
        singular = var_name
    # Numbered suffixes on the singular name (_2, _3, …).
    j = 2
    while True:
        nv = env_map.get(f"{singular}_{j}", "")
        if not str(nv).strip():
            break
        _add_to(collected, seen, nv)
        j += 1


def _normalise_provider_name(env_var: str) -> str:
    """GLM_API_KEY -> glm; OPENROUTER_API_KEY -> openrouter; KILOCODE_API_KEY -> kilocode."""
    name = env_var.lower()
    for suffix in ("_api_key", "_apikey", "_api_keys", "_token", "_secret"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _collect_keys(env_var: str) -> list[str]:
    """Active implementation.

    Looks up keys in priority order:
      1. Process env (live shell), honouring KEY / KEYS / KEY_2 forms.
      2. ~/.hermes/.env   — what `hermes auth add` writes.
      3. ~/.hermes/auth.json — Hermes' structured provider→keys pool.

    Dedupes, preserves first-seen order. Multi-key conventions apply to
    sources (1) and (2); auth.json is matched against the literal env_var
    or its normalised form (GLM_API_KEY -> "glm").

    HERMES_ENV_FILE / HERMES_AUTH_FILE override paths for testing or
    container setups. Path resolution happens at call time so env-var
    overrides work without re-importing the module.
    """
    collected: list[str] = []
    seen: set[str] = set()

    # 1. Process env — magic that bridges KEY / KEYS / KEY_2 via the caller.
    _add_multi_form(dict(os.environ), env_var, collected, seen)

    # 2. Hermes' dotenv — same form conventions.
    env_path = _resolve_hermes_env_path()
    _add_multi_form(_hermes_env_cached(env_path), env_var, collected, seen)

    # 3. Hermes auth.json — match by literal env_var OR normalised name.
    auth_path = _resolve_hermes_auth_path()
    auth_pool = _auth_json_providers_cached(auth_path)
    for name in (env_var, _normalise_provider_name(env_var)):
        for k in auth_pool.get(name, []) or []:
            _add_to(collected, seen, k)

    return collected


# Public alias — point here if you want to bypass ~/.hermes in tests.
_collect_keys_strict = _collect_keys  # kept for downstream imports


def clear_caches() -> None:
    """Drop the in-process mtime caches. Tests use this to keep fixtures hermetic."""
    _AUTH_CACHE.clear()
    _ENV_CACHE.clear()


def _collect_keys_simple(env_var: str) -> list[str]:
    """Process env only — process-env cascade without Hermes' own .env/auth.json.

    Useful in tests and for CLI tools that want strict isolation. Not used by
    the router itself.
    """
    collected: list[str] = []
    seen: set[str] = set()
    _add_multi_form(dict(os.environ), env_var, collected, seen)
    return collected


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
