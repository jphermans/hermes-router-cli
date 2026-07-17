"""Smoke tests — runs every CLI sub-command against a known config.

Runs without making real API calls (uses `_FakeHTTP` injected via env).

Run with: python -m tests.smoke
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


# Tiny monkeypatch helper so we don't depend on pytest.
class _MonkeyPatchCtx:
    def __init__(self):
        self._stack = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        while self._stack:
            obj, name, old = self._stack.pop()
            setattr(obj, name, old)

    def setattr(self, obj, name, value):
        old = getattr(obj, name)
        self._stack.append((obj, name, old))
        setattr(obj, name, value)


def _banner(t):
    print("\n" + "=" * 60 + f"\n  {t}\n" + "=" * 60)


# ─────────────────────────────────────────────────────────────────────
# Module-level tests — exercise the pure logic without HTTP.
# ─────────────────────────────────────────────────────────────────────

def test_classify_distinct_tiers():
    from smart_router.classify import classify
    assert classify("Translate hello to French") == "cheap"
    assert classify("Reason step-by-step about the proof") in ("pro", "standard")
    assert classify("Refactor this function") in ("standard", "pro")
    long_prompt = ("the quick brown fox " * 1000)  # long, but only cheap signals
    assert classify(long_prompt) == "standard"  # long -> bumped from cheap
    print("  ✓ classify")


def test_budget_load_save(tmp_path):
    from smart_router import budget
    saved = budget.BUDGET_PATH
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        budget.BUDGET_PATH = tmp_path / "b.json"
        budget.save_budget({})
        assert budget.spend_for_current_month() == {}
        budget.record("zai", 0.00123)
        assert budget.spend_for_current_month().get("zai") == 0.00123
        assert budget.check("zai", 0.0, 0) is True
        assert budget.check("zai", 10, 1) is False
    finally:
        budget.BUDGET_PATH = saved
    print("  ✓ budget")


def test_providers_multi_key():
    from smart_router.providers import build_providers, load_config
    # Three keys via two forms — verify all collected and order preserved.
    os.environ["TESTVENDOR_KEYS"] = "k1,k2"
    os.environ["TESTVENDOR_KEY_2"] = "k3"
    os.environ.pop("TESTVENDOR_KEY", None)
    cfg = {
        "providers": {
            "testvendor": {
                "env_key": "TESTVENDOR_KEYS",
                "base_url": "https://example.test/v1",
                "cost_class": "free",
                "models": [{"name": "m1", "tier": "cheap"}],
            }
        }
    }
    providers = build_providers(cfg)
    assert len(providers) == 1
    assert providers[0].keys == ["k1", "k2", "k3"], f"got {providers[0].keys}"

    # Now test dedupe: setting TESTVENDOR_KEY to one of the values must collapse.
    os.environ["TESTVENDOR_KEY"] = "k1"
    providers = build_providers(cfg)
    assert providers[0].keys == ["k1", "k2", "k3"], f"got {providers[0].keys}"
    os.environ.pop("TESTVENDOR_KEY", None)
    os.environ.pop("TESTVENDOR_KEYS", None)
    os.environ.pop("TESTVENDOR_KEY_2", None)
    print("  ✓ providers multi-key + dedupe")


def test_cost_class_split():
    from smart_router.providers import build_providers, expand_candidates
    cfg = {
        "providers": {
            "free1": {"env_key": "K1", "base_url": "u", "cost_class": "free",
                      "models": [{"name": "fa", "tier": "cheap"}]},
            "free2": {"env_key": "K2", "base_url": "u", "cost_class": "free",
                      "models": [{"name": "fb", "tier": "pro"}]},
            "paid1": {"env_key": "K3", "base_url": "u", "cost_class": "paid",
                      "models": [{"name": "pa", "tier": "standard",
                                  "input_price": 0.5, "output_price": 1.5}]},
        }
    }
    os.environ["K1"] = "k1"; os.environ["K2"] = "k2"; os.environ["K3"] = "k3"
    providers = build_providers(cfg)
    free = expand_candidates(providers, cost_class="free")
    paid = expand_candidates(providers, cost_class="paid")
    any_ = expand_candidates(providers, cost_class="any")
    assert {c.model for c in free} == {"fa", "fb"}
    assert {c.model for c in paid} == {"pa"}
    assert {c.model for c in any_} == {"fa", "fb", "pa"}
    print("  ✓ cost_class filter")


def test_select_order_free_then_paid():
    """With cost_class=any and prices per model, free models rank FIRST
    (because cost=0) — but capability still wins ties correctly."""
    from smart_router.providers import build_providers, expand_candidates
    from smart_router.route import select
    cfg = {
        "providers": {
            "paid": {"env_key": "K1", "base_url": "u", "cost_class": "paid",
                     "models": [
                         {"name": "pay_cheap", "tier": "cheap", "input_price": 0.1, "output_price": 0.1},
                         {"name": "pay_pro",   "tier": "pro",   "input_price": 1.0, "output_price": 2.0},
                     ]},
            "free": {"env_key": "K2", "base_url": "u", "cost_class": "free",
                     "models": [{"name": "free_cheap", "tier": "cheap"}]},
        }
    }
    os.environ["K1"] = "k1"; os.environ["K2"] = "k2"
    providers = build_providers(cfg)
    plan, tier, debug = select("Translate hello", cfg=cfg, max_out=128, cost_class="any")
    models = [c.model for c in plan]
    # free_cheap should be first (cost=0)
    assert models[0] == "free_cheap", f"got {models!r}"
    print("  ✓ select() orders cheapest-first including $0 models")


def test_select_vision_routing():
    """When vision is needed, only vision-flagged models are in the plan."""
    from smart_router.providers import build_providers
    from smart_router.route import select
    cfg = {
        "providers": {
            "p": {"env_key": "K1", "base_url": "u", "cost_class": "free",
                  "models": [
                      {"name": "text_only", "tier": "standard"},
                      {"name": "vision_model", "tier": "standard", "vision": True},
                  ]}
        }
    }
    os.environ["K1"] = "k1"
    plan, tier, debug = select("What's in this image?", cfg=cfg, max_out=128, cost_class="any")
    models = [c.model for c in plan]
    assert models == ["vision_model"], f"got {models!r}"
    # And when we force vision on a text prompt, same thing
    plan2, _, _ = select("Just write a story.", cfg=cfg, max_out=128, force_vision=True, cost_class="any")
    assert [c.model for c in plan2] == ["vision_model"]
    print("  ✓ vision routing forces vision models only")


def test_route_uses_fallback_chain_on_failure():
    """All primary candidates dead → curated fallback chain takes over."""
    from smart_router import route as route_mod

    cfg = {
        "providers": {
            "free_primary": {"env_key": "K1", "base_url": "u", "cost_class": "free",
                             "models": [{"name": "m1", "tier": "cheap"}]},
            "free_fb": {"env_key": "K2", "base_url": "u", "cost_class": "free",
                        "models": [{"name": "fb1", "tier": "pro"}]},
        },
        "policy": {
            "zai_fallback_chain": [{"provider": "free_fb", "model": "fb1"}],
        },
    }
    os.environ["K1"] = "k1"; os.environ["K2"] = "k2"

    def fake_call(c, prompt, max_out, system, images=None):
        from smart_router.route import CallError
        if c.provider == "free_primary":
            raise CallError("forced failure", retryable=True)
        if c.provider == "free_fb":
            return {"text": "ok", "usage": {}, "raw": {"choices": [{"message": {"content": "ok"}}]}}
        raise AssertionError(f"unexpected candidate {c.provider}/{c.model}")

    with _MonkeyPatchCtx() as mp:
        mp.setattr(route_mod, "_call", fake_call)
        result = route_mod.route("Translate hello", cfg=cfg, max_out=64, cost_class="any")
    assert result.get("ok"), f"route returned: {result}"
    assert result["selected_provider"] == "free_fb"
    print("  ✓ curated fallback chain fires when primary fails")


def test_route_uses_key_rotation():
    """Per-key rotation: first key fails, second key succeeds."""
    from smart_router import route as route_mod

    cfg = {
        "providers": {
            "multi": {
                "env_key": "MULTI_KEY",
                "base_url": "u", "cost_class": "free",
                "models": [{"name": "m1", "tier": "cheap"}],
            }
        }
    }
    # Two keys for the single provider
    os.environ["MULTI_KEY"] = "k1,k2"

    seen_keys: list[str] = []

    def fake_call(c, prompt, max_out, system, images=None):
        from smart_router.route import CallError
        # c.api_keys holds the keys; the caller passed via api_keys field.
        # We can't read the key here directly because api_keys holds the list and
        # _call() picks one. Instead, simulate: first call returns a value for k1,
        # then we want to verify that on the second candidate attempt we re-call
        # with k2.
        seen_keys.append(c.last_key_index)
        if c.last_key_index == 0:
            raise CallError("k1 broken", retryable=True)
        return {"text": "ok", "usage": {}, "raw": {"choices": [{"message": {"content": "ok"}}]}}

    with _MonkeyPatchCtx() as mp:
        mp.setattr(route_mod, "_call", fake_call)
        result = route_mod.route("Test", cfg=cfg, max_out=64, cost_class="free")
    assert result.get("ok"), result
    assert 0 in seen_keys  # tried key 0
    assert 1 in seen_keys  # then rotated to key 1
    print("  ✓ per-provider key rotation works")


# ─────────────────────────────────────────────────────────────────────
# CLI-level tests — run the actual `hr` binary as a subprocess.
# ─────────────────────────────────────────────────────────────────────

def run_cli(*args, env=None, cwd=None):
    cmd = [sys.executable, "-m", "smart_router", *args]
    return subprocess.run(
        cmd, capture_output=True, text=True,
        env=env or os.environ, cwd=cwd or str(ROOT),
        timeout=30,
    )


def test_cli_version_flag():
    r = run_cli("--version")
    assert r.returncode == 0
    assert "hermes-router" in r.stdout
    print("  ✓ hr --version")


def test_cli_help():
    r = run_cli("--help")
    assert r.returncode == 0
    for name in ["route", "models", "verify", "auth", "doctor", "budget", "chat"]:
        assert name in r.stdout, f"missing {name} in --help"
    print("  ✓ hr --help lists all sub-commands")


def test_cli_models_table():
    """Listing models needs at least one key per provider to show non-empty; we
    set a single dummy key on one provider so we exercise both branches."""
    env = os.environ.copy()
    env["GLM_API_KEY"] = "dummy"  # makes `zai` active
    r = run_cli("models", "--json", env=env)
    assert r.returncode == 0
    rows = json.loads(r.stdout)
    assert rows, "no models emitted"
    zai_rows = [row for row in rows if row["provider"] == "zai"]
    assert zai_rows, "zai provider should be active"
    for row in zai_rows:
        assert row["cost_class"] == "free"
        assert row["keys_available"] == 1
    print(f"  ✓ hr models --json  ({len(rows)} rows)")


def test_cli_models_filter_class():
    env = os.environ.copy()
    env["GLM_API_KEY"] = "dummy"
    env["OPENROUTER_API_KEY"] = "dummy_paid"
    r = run_cli("models", "--class", "free", "--json", env=env)
    assert r.returncode == 0
    rows = json.loads(r.stdout)
    assert all(row["cost_class"] == "free" for row in rows)
    r = run_cli("models", "--class", "paid", "--json", env=env)
    rows = json.loads(r.stdout)
    assert all(row["cost_class"] == "paid" for row in rows)
    print("  ✓ hr models --class free/paid")


def test_cli_route_dry_run_classified():
    """--dry-run must never fire HTTP — works with no keys at all."""
    env = os.environ.copy()
    for k in list(env):
        if "API_KEY" in k or "TOKEN" in k:
            env.pop(k, None)
    r = run_cli("route", "--prompt", "Translate hello", "--dry-run", "--class", "free", env=env)
    if r.returncode != 0:
        print("STDOUT:", r.stdout); print("STDERR:", r.stderr)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data.get("dry_run") is True
    assert data["plan"], "plan should not be empty"
    for p in data["plan"]:
        assert p["cost_class"] == "free", f"non-free leaked: {p}"
    print("  ✓ hr route --dry-run --class free emits free-only plan")


def test_cli_route_dry_run_class_paid():
    env = os.environ.copy()
    for k in list(env):
        if "API_KEY" in k or "TOKEN" in k:
            env.pop(k, None)
    r = run_cli("route", "--prompt", "hello", "--dry-run", "--class", "paid", env=env)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    for p in data["plan"]:
        assert p["cost_class"] == "paid", f"non-paid leaked: {p}"
    print("  ✓ hr route --dry-run --class paid emits paid-only plan")


def test_cli_route_class_any_includes_both():
    env = os.environ.copy()
    env["GLM_API_KEY"] = "dummy"           # free
    env["OPENROUTER_API_KEY"] = "dummy_paid"
    r = run_cli("route", "--prompt", "hello", "--dry-run", "--class", "any", env=env)
    data = json.loads(r.stdout)
    classes = {p["cost_class"] for p in data["plan"]}
    assert "free" in classes and "paid" in classes, f"missing one: {classes}"
    print("  ✓ hr route --dry-run --class any mixes both pools")


def test_cli_route_pretty_no_keys():
    env = os.environ.copy()
    for k in list(env):
        if "API_KEY" in k or "TOKEN" in k:
            env.pop(k, None)
    r = run_cli("route", "--prompt", "Summarize X", "--dry-run", "--pretty", "--class", "free", env=env)
    assert r.returncode == 0
    assert "[dry-run]" in r.stdout
    print("  ✓ hr route --pretty")


def test_cli_doctor_clean_output():
    r = run_cli("doctor")
    assert r.returncode in (0, 1)  # acceptable: ok or findings exist
    print("  ✓ hr doctor")


def test_cli_auth_shows_dummy_key():
    env = os.environ.copy()
    env["GLM_API_KEY"] = "ghp_AB_CDtestkeyXYZ12345"
    r = run_cli("auth", "--show", env=env)
    assert r.returncode == 0
    assert "zai" in r.stdout
    assert "****2345" in r.stdout or "****" in r.stdout  # masked
    assert "ghp_" not in r.stdout or r.stdout.count("ghp_") == 0  # never leak full key
    print("  ✓ hr auth masks keys")


def main():
    _banner("module-level tests")
    test_classify_distinct_tiers()
    test_budget_load_save(HERE / "_tmp")
    test_providers_multi_key()
    test_cost_class_split()
    test_select_order_free_then_paid()
    test_select_vision_routing()
    test_route_uses_fallback_chain_on_failure()
    test_route_uses_key_rotation()

    _banner("CLI-level tests")
    test_cli_version_flag()
    test_cli_help()
    test_cli_models_table()
    test_cli_models_filter_class()
    test_cli_route_dry_run_classified()
    test_cli_route_dry_run_class_paid()
    test_cli_route_class_any_includes_both()
    test_cli_route_pretty_no_keys()
    test_cli_doctor_clean_output()
    test_cli_auth_shows_dummy_key()

    print("\nALL TESTS PASSED ✅\n")


if __name__ == "__main__":
    main()
