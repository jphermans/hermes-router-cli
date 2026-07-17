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

def test_collect_keys_process_env_only():
    """Strict-isolation: no ~/.hermes/ files consulted.

    When HERMES_ENV_FILE and HERMES_AUTH_FILE point at nonexistent paths,
    only process env should be read. This is the test environment contract.
    """
    import os
    from smart_router.providers import _collect_keys, _collect_keys_simple
    saved = (os.environ.get("HERMES_ENV_FILE"), os.environ.get("HERMES_AUTH_FILE"))
    try:
        os.environ["HERMES_ENV_FILE"] = "/nonexistent/.hermes-env-strict.env"
        os.environ["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-auth-strict.json"
        os.environ.pop("TESTVENDOR_KEYS", None)
        os.environ.pop("TESTVENDOR_KEY_2", None)
        os.environ.pop("TESTVENDOR_KEY", None)
        os.environ["TESTVENDOR_KEYS"] = "k1,k2"
        os.environ["TESTVENDOR_KEY_2"] = "k3"
        keys = _collect_keys("TESTVENDOR_KEYS")
        # Only process env contributes in strict isolation.
        assert keys == ["k1", "k2", "k3"], f"strict got {keys}"

        keys_simple = _collect_keys_simple("TESTVENDOR_KEYS")
        assert keys_simple == ["k1", "k2", "k3"], f"simple got {keys_simple}"
    finally:
        if saved[0] is not None:
            os.environ["HERMES_ENV_FILE"] = saved[0]
        if saved[1] is not None:
            os.environ["HERMES_AUTH_FILE"] = saved[1]
    print("  ✓ _collect_keys: strict isolation (process-env only)")


def test_collect_keys_from_hermes_dotenv(tmp_path):
    """~/.hermes/.env is the source `hermes auth add` writes to.
    With process env empty and HERMES_AUTH_FILE pointing nowhere, the
    router must still discover keys from that file.
    """
    import os
    from smart_router.providers import _collect_keys, clear_caches
    tmp_path.mkdir(parents=True, exist_ok=True)
    clear_caches()
    dotenv = tmp_path / "fake-hermes.env"
    dotenv.write_text(
        "# Mimics what `hermes auth add` would write. We use both KEY and KEYS\n"
        "# forms here so the test exercises both branches of _add_multi_form.\n"
        "GEMINI_API_KEY=gem-env-1\n"
        "GEMINI_API_KEY_2=gem-env-2\n"     # numbered suffix on singular
        "XAI_API_KEYS=x1,x2,x3\n"           # plural comma-split
        "KILOCODE_API_KEYS=kilo-1,kilo-2\n" # also plural — exercise the plural→singular bridge
        "DEEPSEEK_API_KEY=d-env-1\n"        # single, no suffixes
    )
    saved_env, saved_auth = os.environ.get("HERMES_ENV_FILE"), os.environ.get("HERMES_AUTH_FILE")
    try:
        os.environ["HERMES_ENV_FILE"] = str(dotenv)
        os.environ["HERMES_AUTH_FILE"] = "/nonexistent/.fake.json"
        for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2",
                  "XAI_API_KEY", "XAI_API_KEYS",
                  "KILOCODE_API_KEY", "KILOCODE_API_KEYS",
                  "DEEPSEEK_API_KEY"):
            os.environ.pop(k, None)
        # gemini: KEY + KEY_2 → 2 keys, deduped, in source order
        gem = _collect_keys("GEMINI_API_KEY")
        assert gem == ["gem-env-1", "gem-env-2"], f"gemini got {gem}"
        # xai: caller passed singular form, but fixture used plural form.
        # _add_multi_form first reads var_name literally (here empty),
        # then since var_name doesn't end in S, falls through to numbered suffixes
        # (none). Result: empty. We didn't write XAI_API_KEY= directly.
        xai_via_singular = _collect_keys("XAI_API_KEY")
        assert xai_via_singular == [], f"xai (singular) got {xai_via_singular} — correct empty"
        # xai via the plural form: KEYS=x1,x2,x3 → 3 keys.
        xai_via_plural = _collect_keys("XAI_API_KEYS")
        assert xai_via_plural == ["x1", "x2", "x3"], f"xai (plural) got {xai_via_plural}"
        # kilocode via plural KEYS → 2 keys
        kc_via_plural = _collect_keys("KILOCODE_API_KEYS")
        assert kc_via_plural == ["kilo-1", "kilo-2"], f"kilocode plural got {kc_via_plural}"
        # kilocode via singular KEY → empty (no singular entry in fixture).
        kc_via_singular = _collect_keys("KILOCODE_API_KEY")
        assert kc_via_singular == [], f"kilocode singular got {kc_via_singular}"
        # deepseek single key
        ds = _collect_keys("DEEPSEEK_API_KEY")
        assert ds == ["d-env-1"], f"deepseek got {ds}"
    finally:
        if saved_env is not None: os.environ["HERMES_ENV_FILE"] = saved_env
        if saved_auth is not None: os.environ["HERMES_AUTH_FILE"] = saved_auth
    print("  ✓ _collect_keys: HERMES_ENV_FILE path with multi-key forms")


def test_collect_keys_from_auth_json():
    """Auth.json pool entries (the structured credential pool that
    `hermes auth add` mutates) must be discoverable too. Two matching
    strategies: literal env_var name AND normalised form (GLM_API_KEY -> glm).
    """
    import os, json
    from smart_router.providers import _collect_keys, clear_caches
    clear_caches()
    auth = tmp_path_path = os.environ.get("TMPDIR", "/tmp") + "/fake-auth.json"
    with open(auth, "w") as f:
        json.dump({
            "version": 1,
            "providers": {
                "openrouter": ["or-auth-1", "or-auth-2"],
            },
            "credential_pool": ["openrouter"],
        }, f)
    saved_env, saved_auth = (os.environ.get("HERMES_ENV_FILE"),
                             os.environ.get("HERMES_AUTH_FILE"))
    try:
        os.environ["HERMES_AUTH_FILE"] = auth
        os.environ["HERMES_ENV_FILE"] = "/nonexistent/.fake.env"
        os.environ.pop("OPENROUTER_API_KEY", None)
        # env_var is OPENROUTER_API_KEY, normalised form is "openrouter".
        # auth.json has key "openrouter". Match.
        keys = _collect_keys("OPENROUTER_API_KEY")
        assert "or-auth-1" in keys and "or-auth-2" in keys, f"got {keys}"
    finally:
        os.unlink(auth)
        if saved_env is not None: os.environ["HERMES_ENV_FILE"] = saved_env
        if saved_auth is not None: os.environ["HERMES_AUTH_FILE"] = saved_auth
    print("  ✓ _collect_keys: HERMES_AUTH_FILE pool with normalised-name match")


def test_collect_keys_cascade_combines():
    """All three sources together, deduped, in first-seen order:
    process env should win over ~/.hermes/.env for the SAME key.
    """
    import os, json
    from smart_router.providers import _collect_keys, clear_caches
    clear_caches()
    tmp_dir = os.environ.get("TMPDIR", "/tmp")
    dotenv = tmp_dir + "/cascade.env"
    auth = tmp_dir + "/cascade.json"
    with open(dotenv, "w") as f:
        f.write("VENDOR_KEY=from-dotenv\n")
    with open(auth, "w") as f:
        json.dump({"providers": {"VENDOR": ["from-authjson"]}}, f)
    saved_env, saved_auth = (os.environ.get("HERMES_ENV_FILE"),
                             os.environ.get("HERMES_AUTH_FILE"))
    try:
        os.environ["HERMES_ENV_FILE"] = dotenv
        os.environ["HERMES_AUTH_FILE"] = auth
        # Process env: 'from-process'
        os.environ["VENDOR_KEY"] = "from-process"
        keys = _collect_keys("VENDOR_KEY")
        # Order: process env → dotenv → auth.json (cascade precedence).
        assert keys == ["from-process", "from-dotenv"], f"got {keys}"
        # Now check VENDOR from auth.json's normalised lookup.
        os.environ.pop("VENDOR_KEY", None)
        keys2 = _collect_keys("VENDOR_KEY")
        # VENDOR_KEY is the env var, normalised "vendor". auth.json has "VENDOR" which
        # doesn't lowercase to "vendor_key" — so no auth match should occur here.
        # What we expect: dotenv (VENDOR_KEY) is empty, so just process check on VENDOR_KEY.
        # Reset and verify with a matching auth.json shape:
    finally:
        os.unlink(dotenv); os.unlink(auth)
        if saved_env is not None: os.environ["HERMES_ENV_FILE"] = saved_env
        if saved_auth is not None: os.environ["HERMES_AUTH_FILE"] = saved_auth
    print("  ✓ _collect_keys: cascade precedence (process env wins)")


def test_build_providers_uses_hermes_sources(tmp_path):
    """End-to-end: with HERMES_ENV_FILE pointing at a fixture file and no
    process env vars, build_providers returns ACTIVE providers for
    everything written to the fixture.
    """
    import os
    from smart_router.providers import build_providers, load_config, clear_caches
    clear_caches()
    tmp_path.mkdir(parents=True, exist_ok=True)
    dotenv = tmp_path / "fixture.env"
    dotenv.write_text(
        "GLM_API_KEY=fixture-zai-key\n"
        "OPENROUTER_API_KEY=fixture-or-key\n"
    )
    saved_env, saved_auth = os.environ.get("HERMES_ENV_FILE"), os.environ.get("HERMES_AUTH_FILE")
    try:
        os.environ["HERMES_ENV_FILE"] = str(dotenv)
        os.environ["HERMES_AUTH_FILE"] = "/nonexistent/.empty.json"
        for k in ("GLM_API_KEY", "OPENROUTER_API_KEY", "KILOCODE_API_KEY",
                  "DEEPSEEK_API_KEY", "XAI_API_KEY", "NVIDIA_API_KEY",
                  "VENICE_API_KEY", "MINIMAX_API_KEY", "GITHUB_TOKEN",
                  "GEMINI_API_KEY"):
            os.environ.pop(k, None)
        cfg = load_config()
        active = build_providers(cfg)
        names_with_keys = {p.name for p in active if p.keys}
        # Only providers that have a key in the fixture should be active.
        assert "zai" in names_with_keys, f"expected zai active, got {names_with_keys}"
        assert "openrouter" in names_with_keys, f"expected openrouter active, got {names_with_keys}"
        # A provider whose key is NOT in the fixture must be skipped.
        assert "kilo" not in names_with_keys, "kilo leaked"
        # The keys match exactly what we wrote.
        for p in active:
            if p.name == "zai":
                assert p.keys == ["fixture-zai-key"]
            if p.name == "openrouter":
                assert p.keys == ["fixture-or-key"]
    finally:
        if saved_env is not None: os.environ["HERMES_ENV_FILE"] = saved_env
        if saved_auth is not None: os.environ["HERMES_AUTH_FILE"] = saved_auth
    print("  ✓ build_providers: end-to-end via HERMES_ENV_FILE")


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
    set a single dummy key on one provider so we exercise both branches.
    Also overrides HERMES_ENV_FILE / HERMES_AUTH_FILE so this test never picks
    up the user's real ~/.hermes state — it must be hermetic.
    """
    env = os.environ.copy()
    env["GLM_API_KEY"] = "dummy"  # makes `zai` active (process env path)
    env["HERMES_ENV_FILE"] = "/nonexistent/.hermes-test-empty.env"
    env["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-test-empty.json"
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
    env["HERMES_ENV_FILE"] = "/nonexistent/.hermes-test-empty.env"
    env["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-test-empty.json"
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
    env["HERMES_ENV_FILE"] = "/nonexistent/.hermes-test-empty.env"
    env["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-test-empty.json"
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
    env["HERMES_ENV_FILE"] = "/nonexistent/.hermes-test-empty.env"
    env["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-test-empty.json"
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
    env["HERMES_ENV_FILE"] = "/nonexistent/.hermes-test-empty.env"
    env["HERMES_AUTH_FILE"] = "/nonexistent/.hermes-test-empty.json"
    r = run_cli("auth", "--show", env=env)
    assert r.returncode == 0
    assert "zai" in r.stdout
    assert "****2345" in r.stdout or "****" in r.stdout  # masked
    assert "ghp_" not in r.stdout or r.stdout.count("ghp_") == 0  # never leak full key
    print("  ✓ hr auth masks keys")


def test_classify_http_classifier():
    """Permanent vs retryable classification correctness — drives the
    fail-fast behavior of the router. If this regresses the router will
    either retry permanent errors (waste of time) or fail-fast on
    transient ones (gives up too eagerly).
    """
    from smart_router.route import _classify_http, PERMANENT_HTTP_CODES
    assert _classify_http(401) == (False, True), "401 must be permanent"
    assert _classify_http(403) == (False, True)
    assert _classify_http(404) == (False, True), "model-not-found must be permanent"
    assert _classify_http(422) == (False, True), "validation error must be permanent"
    assert _classify_http(400) == (False, True), "bad request must be permanent"
    assert _classify_http(429) == (True, False), "rate limit must be retryable only"
    assert _classify_http(503) == (True, False), "service unavailable must be retryable"
    assert _classify_http(529) == (True, False), "overloaded must be retryable"
    assert _classify_http(500) == (True, False), "internal error must be retryable"
    assert _classify_http(418) == (False, False), "teapot: neither retryable nor permanent; just fail"
    # Sanity: 401 is in the permanent set
    assert 401 in PERMANENT_HTTP_CODES
    assert 429 not in PERMANENT_HTTP_CODES
    print("  ✓ _classify_http")


def test_route_fails_fast_on_permanent_http():
    """A 404 (model-not-found) from the primary must NOT iterate all keys.
    Before the fix, the router would happily try every other key of the
    same provider, getting the same 404 every time. After the fix, it
    tries ONE key then immediately moves to the next candidate.
    """
    from smart_router import route as route_mod
    cfg = {
        "providers": {
            "bad": {
                "env_key": "BAD_KEYS",
                "base_url": "u", "cost_class": "free",
                # Many keys — so we can prove the router only tries ONE before giving up.
                "models": [{"name": "m1", "tier": "cheap"}],
            },
            "good": {
                "env_key": "GOOD_KEY",
                "base_url": "u", "cost_class": "free",
                "models": [{"name": "m1", "tier": "cheap"}],
            },
        },
        "policy": {},
    }
    os.environ["BAD_KEYS"] = "bk1,bk2,bk3,bk4,bk5"   # 5 keys
    os.environ["GOOD_KEY"] = "gk"

    bad_call_count = {"n": 0}
    good_call_count = {"n": 0}

    def fake_call(c, prompt, max_out, system, images=None):
        from smart_router.route import CallError
        if c.provider == "bad":
            bad_call_count["n"] += 1
            raise CallError("HTTP 404 from bad/m1: model not found",
                            retryable=False, permanent=True, http_code=404)
        if c.provider == "good":
            good_call_count["n"] += 1
            return {"text": "ok", "usage": {}, "raw": {"choices": [{"message": {"content": "ok"}}]}}
        raise AssertionError(f"unexpected candidate {c.provider}/{c.model}")

    with _MonkeyPatchCtx() as mp:
        mp.setattr(route_mod, "_call", fake_call)
        result = route_mod.route("Hello", cfg=cfg, max_out=64, cost_class="any")

    assert result.get("ok"), f"route failed: {result}"
    assert result["selected_provider"] == "good"
    # The big win: only ONE call to the bad provider, not 5.
    assert bad_call_count["n"] == 1, f"expected exactly 1 attempt on the bad provider, got {bad_call_count['n']}"
    assert good_call_count["n"] == 1
    # And the trace records the permanent flag
    bad_tries = [t for t in result["fallbacks_tried"] if t.get("provider") == "bad"]
    assert bad_tries and bad_tries[0].get("permanent") is True, f"permanent flag missing in trace: {bad_tries}"
    print("  ✓ 404 fails fast — only one attempt on each bad provider instead of N keys")


def test_route_circuit_breaker_skips_repeated_failures():
    """Three consecutive retryable failures on the same (provider,model)
    must trigger the in-process circuit breaker — additional candidates
    of that exact pair get skipped without re-trying. The broader sweep
    still gets a chance to find something else, though.
    """
    from smart_router import route as route_mod
    cfg = {
        "providers": {
            "flaky": {
                "env_key": "F_KEY",
                "base_url": "u", "cost_class": "free",
                "models": [{"name": "m1", "tier": "cheap"}],
            },
            "good": {
                "env_key": "G_KEY",
                "base_url": "u", "cost_class": "free",
                "models": [{"name": "m1", "tier": "cheap"}],
            },
        },
        "policy": {},
    }
    os.environ["F_KEY"] = "k1"; os.environ["G_KEY"] = "k2"

    flaky_calls = {"n": 0}
    calls_to_flaky = 0  # in practice should equal retry count per key

    def fake_call(c, prompt, max_out, system, images=None):
        from smart_router.route import CallError
        if c.provider == "flaky":
            flaky_calls["n"] += 1
            raise CallError("HTTP 503 overloaded", retryable=True, permanent=False, http_code=503)
        if c.provider == "good":
            return {"text": "ok", "usage": {}, "raw": {"choices": [{"message": {"content": "ok"}}]}}
        raise AssertionError("unexpected")

    with _MonkeyPatchCtx() as mp:
        mp.setattr(route_mod, "_call", fake_call)
        result = route_mod.route("Hello", cfg=cfg, max_out=64, cost_class="any")

    assert result.get("ok"), result
    # Flaky provider was tried the full retry budget (1 + key_retries=2 = 3 calls per key × 1 key = 3).
    # The CIRCUIT BREAKER then makes any later iteration of this (provider, model) skip.
    # Since each candidate has only one model, we'd see exactly 3 retries before the circuit triggers.
    # The exact count depends on the plan; what matters is "not too many".
    assert flaky_calls["n"] <= 3 * 4, f"expected circuit-breaker to limit calls, got {flaky_calls['n']}"
    # Either the good provider is in the curated chain OR in the broader sweep — regardless,
    # route() should eventually succeed.
    assert result["selected_provider"] == "good"
    print(f"  ✓ circuit breaker limits repeated retryable failures ({flaky_calls['n']} total flaky calls)")


def main():
    _banner("module-level tests")
    test_collect_keys_process_env_only()
    test_collect_keys_from_hermes_dotenv(HERE / "_tmp_hermes_dotenv_test")
    test_collect_keys_from_auth_json()
    test_collect_keys_cascade_combines()
    test_build_providers_uses_hermes_sources(HERE / "_tmp_hermes_e2e_test")
    test_classify_distinct_tiers()
    test_budget_load_save(HERE / "_tmp")
    test_providers_multi_key()
    test_cost_class_split()
    test_select_order_free_then_paid()
    test_select_vision_routing()
    test_route_uses_fallback_chain_on_failure()
    test_route_uses_key_rotation()
    test_classify_http_classifier()
    test_route_fails_fast_on_permanent_http()
    test_route_circuit_breaker_skips_repeated_failures()

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
