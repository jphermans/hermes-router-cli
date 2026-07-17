"""
Routing core.

Given a prompt and a cost_class filter ('free' | 'paid' | 'any'), this module:
  1. Runs the heuristic classifier to pick a tier (cheap|standard|pro).
  2. Builds candidates from configured providers (filtered by cost_class).
  3. Ranks them by estimated USD cost, ties broken by capability tier.
  4. Calls the cheapest candidate. On failure, rotates within the provider's
     key pool, then falls back to the next-cheapest candidate, and finally
     runs a broader (any-tier) sweep so we don't give up after one bad key.
  5. Returns a rich dict: response, selected provider/model, usage, cost,
     and a complete `fallbacks_tried` trace so the caller can see what
     actually happened.

Vision prompts route ONLY to vision-flagged models. Multimodal images are
sent as OpenAI-compatible content arrays (data URLs or http(s) URLs).

No external SDK: relies on urllib + the OpenAI-compatible /chat/completions
shape that all major providers expose.
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from .budget import check as budget_check, record as budget_record
from .classify import classify, estimate_tokens, explain as explain_classify, needs_vision
from .providers import (
    Provider,
    build_providers,
    expand_candidates,
    load_config,
)

TIER_ORDER = {"cheap": 0, "standard": 1, "pro": 2}


@dataclass
class Candidate:
    provider: str
    model: str
    tier: str
    input_price: float
    output_price: float
    context: int
    vision: bool
    cost_class: str            # 'free' | 'paid'
    base_url: str
    api_keys: list[str] = field(default_factory=list)
    est_cost: float = 0.0
    # Filled in lazily during a routing attempt so we can rotate keys mid-call.
    last_key_index: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Selection — build the ranked plan, without firing any API call yet.
# ─────────────────────────────────────────────────────────────────────────────

def select(
    prompt: str,
    cfg: Optional[dict] = None,
    force_tier: Optional[str] = None,
    max_out: int = 512,
    force_vision: Optional[bool] = None,
    cost_class: str = "any",
) -> tuple[list[Candidate], str, dict]:
    """Return (ranked_candidates, chosen_tier, classification_debug).

    The classifier is a heuristic (keyword + token-length) — no extra LLM
    cost. Vision prompts route ONLY to vision-capable models.
    """
    if cfg is None:
        cfg = load_config()
    debug = (
        explain_classify(prompt)
        if force_tier is None
        else {
            "tier": force_tier,
            "estimated_tokens": estimate_tokens(prompt),
            "pro_signals": [],
            "cheap_signals": [],
            "needs_vision": force_vision if force_vision is not None else needs_vision(prompt),
            "vision_signals": [],
        }
    )
    chosen_tier = force_tier or debug["tier"]
    est_in = debug["estimated_tokens"]
    want_vision = bool(force_vision) if force_vision is not None else bool(debug.get("needs_vision"))

    providers = build_providers(cfg)
    if not providers:
        # Empty pool — let the caller (route()) decide whether to raise or
        # fall back to the un-filtered listing for a "what-if" dry-run.
        return [], chosen_tier, debug

    cands = expand_candidates(providers, cost_class=cost_class)
    if not cands:
        raise RuntimeError(
            f"No models match cost_class={cost_class!r}. "
            f"Try `hr models --class {cost_class}` to see the pool."
        )

    # Compute estimated cost per candidate.
    for c in cands:
        c.est_cost = (est_in / 1_000_000) * c.input_price + (max_out / 1_000_000) * c.output_price

    def meets_tier(c: Candidate) -> bool:
        ct = TIER_ORDER.get(c.tier, 1)
        mt = TIER_ORDER.get(chosen_tier, 1)
        if cfg.get("policy", {}).get("allow_tier_upgrade", True):
            return ct >= mt
        return ct == mt

    # Vision takes priority: any vision model beats a non-vision one for image tasks.
    if want_vision:
        pool = [c for c in cands if c.vision]
        # No vision-flagged model in the requested pool? Try the broader pool before giving up.
        if not pool:
            pool = [c for c in expand_candidates(providers, cost_class="any") if c.vision]
    else:
        pool = [c for c in cands if meets_tier(c)]
        if not pool:
            # Loosen the tier rule if the strict filter wiped everything out.
            pool = cands

    # Stability: sort by (cost, capability asc, provider, model) so the plan
    # is deterministic across runs of the same prompt.
    pool.sort(key=lambda c: (c.est_cost, TIER_ORDER.get(c.tier, 1), c.provider, c.model))

    # Apply model filters: blocklist removes candidates, preferlist boosts them.
    filters = cfg.get("policy", {}).get("model_filters", {})
    blocked_names = {b.get("name") for b in (filters.get("block") or []) if b.get("name")}
    if blocked_names:
        removed = [f"{c.provider}/{c.model}" for c in pool if f"{c.provider}/{c.model}" in blocked_names or c.model in blocked_names]
        pool = [c for c in pool if f"{c.provider}/{c.model}" not in blocked_names and c.model not in blocked_names]

    preferred = [p.get("name") for p in (filters.get("prefer") or []) if p.get("name")]
    if preferred:
        # Boost preferred models: reduce their effective cost to 0 so they
        # sort above everything else (except $0 free models).
        for c in pool:
            key = f"{c.provider}/{c.model}"
            if key in preferred or c.model in preferred:
                if c.est_cost > 0:
                    c.est_cost = 0.000001  # just above $0
        pool.sort(key=lambda c: (c.est_cost, TIER_ORDER.get(c.tier, 1), c.provider, c.model))

    return pool, chosen_tier, debug


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — one POST path works for every OpenAI-compatible provider.
# ─────────────────────────────────────────────────────────────────────────────

class CallError(Exception):
    """Raised by _call() so route() can categorise failures.

    permanent=True means: do NOT retry on this key, do NOT rotate to other
    keys of the same provider, do NOT retry this candidate. Skip immediately
    to the next candidate in the chain. Use this for auth/permissions,
    model-not-found, and other 'same outcome every time' failures —
    retrying just wastes time.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        permanent: bool = False,
        http_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.permanent = permanent
        self.http_code = http_code


# HTTP codes where retrying the SAME request (other key, retry, etc.) is
# fundamentally pointless — the request itself is wrong. Move on fast.
PERMANENT_HTTP_CODES = {400, 401, 403, 404, 405, 406, 410, 415, 422, 451}


def _classify_http(code: int) -> tuple[bool, bool]:
    """Return (retryable, permanent) for an HTTP status code.

    permanent implies 'don't retry, don't rotate keys, don't try other
    variants of this request'. retryable implies 'safe to try again'.
    They are not mutually exclusive.
    """
    if code in PERMANENT_HTTP_CODES:
        return (False, True)
    if code in (408, 425, 429, 500, 502, 503, 504, 529):
        return (True, False)
    return (False, False)  # everything else: fail fast


def _http_post(url: str, headers: dict, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8") or "{}")


def _call(c: Candidate, prompt: str, max_out: int, system: str,
          images: Optional[list[str]] = None,
          timeout: int = 60) -> dict:
    """Single attempt on one (candidate, current_key). Raises CallError.

    Reasoning-model gotcha: GLM-5.x / MiniMax / deepseek-reasoner burn
    output tokens on internal ˆÕÈ reasoning blocks before the visible answer.
    Empty / whitespace content = all tokens consumed — treat as failure so
    the fallback chain activates and we don't return a blank string.
    """
    if not c.api_keys:
        raise CallError(f"no keys configured for {c.provider}", retryable=False)

    api_key = c.api_keys[c.last_key_index % len(c.api_keys)]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if images:
        content: list = [{"type": "text", "text": prompt}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        user_msg = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": prompt}
    messages = ([{"role": "system", "content": system}] if system else []) + [user_msg]
    payload = {"model": c.model, "messages": messages, "max_tokens": max_out}
    url = f"{c.base_url.rstrip('/')}/chat/completions"

    try:
        body = _http_post(url, headers, payload, timeout=timeout)
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        retryable, permanent = _classify_http(e.code)
        raise CallError(
            f"HTTP {e.code} from {c.provider}/{c.model}: {body_text}",
            retryable=retryable,
            permanent=permanent,
            http_code=e.code,
        ) from e
    except TimeoutError as e:
        raise CallError(f"timeout from {c.provider}/{c.model}", retryable=True) from e
    except urllib.error.URLError as e:
        raise CallError(f"connection error to {c.provider}: {e}", retryable=True) from e
    except Exception as e:  # JSON errors etc.
        raise CallError(f"{type(e).__name__}: {e}", retryable=True) from e

    try:
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    except Exception:
        text = ""
    if not text.strip():
        raise CallError(
            f"empty response from {c.provider}/{c.model} (model may have burned "
            f"output budget on reasoning; increase max_tokens)",
            retryable=True,
        )
    return {"text": text, "usage": body.get("usage", {}), "raw": body}


def _try_candidate(
    c: Candidate, prompt: str, max_out: int, system: str,
    images: Optional[list[str]] = None,
    key_retries: int = 2,
    key_backoff_base: float = 0.4,
) -> tuple[Optional[dict], list[dict]]:
    """Try one candidate with per-key rotation + small backoff between keys.

    Returns (result_dict, attempts_log).

    Fail-fast behavior:
      • If a 4xx-class error is PERMANENT (auth, model-not-found, etc.),
        stop immediately — don't retry, don't rotate keys, don't try
        other variants. The attempts_log records the one attempt; the
        caller moves on to the next candidate.
      • If the error is retryable (429/5xx/timeout), retry the SAME key
        with backoff up to key_retries times. Then move to the next key.
      • If budget cap is exceeded, skip this candidate entirely without
        recording it as a failure.
    """
    attempts: list[dict] = []
    if not c.api_keys:
        return None, [{"provider": c.provider, "model": c.model, "error": "no keys"}]

    starting_index = c.last_key_index
    n = len(c.api_keys)
    for offset in range(n):
        c.last_key_index = (starting_index + offset) % n
        for kr in range(1 + key_retries):
            try:
                result = _call(c, prompt, max_out, system, images=images)
                attempts.append({
                    "provider": c.provider, "model": c.model,
                    "key_index": c.last_key_index,
                    "ok": True,
                })
                c.last_key_index = (c.last_key_index + 1) % n
                return result, attempts
            except CallError as e:
                attempts.append({
                    "provider": c.provider, "model": c.model,
                    "key_index": c.last_key_index,
                    "error": str(e),
                    "retryable": e.retryable,
                    "permanent": e.permanent,
                    "http_code": e.http_code,
                })
                if e.permanent:
                    # Fail-fast: don't retry, don't rotate keys, don't try
                    # other variants of this same request. Step straight out
                    # to the next candidate in the route plan.
                    return None, attempts
                if not e.retryable or kr >= key_retries:
                    # Non-retryable / exhausted retries on this key.
                    break
                # Transient — back off briefly and retry the same key.
                time.sleep(key_backoff_base * (1.5 ** kr))
        # Advance to the next key for the next outer iteration.
    return None, attempts


# ─────────────────────────────────────────────────────────────────────────────
# Top-level route() — orchestrates selection + tried-candidates + broader sweep.
# ─────────────────────────────────────────────────────────────────────────────

def route(
    prompt: str,
    force_tier: Optional[str] = None,
    max_out: int = 512,
    dry_run: bool = False,
    system: str = "",
    cost_class: str = "any",
    images: Optional[list[str]] = None,
    force_vision: Optional[bool] = None,
    cfg: Optional[dict] = None,
    auto_fallback: bool = False,
    max_cost_usd: Optional[float] = None,
) -> dict:
    """Route a prompt through the configured providers.

    cost_class:  'free' (default subscription/prepaid pool), 'paid', or 'any'.
    images:      list of URLs or data URLs for vision prompts.
    auto_fallback: if True and cost_class='free' fails, retry with 'paid'.
    max_cost_usd: if set, skip candidates whose estimated cost exceeds this.
    """
    cfg = cfg if cfg is not None else load_config()
    max_out = min(max_out, cfg.get("policy", {}).get("max_output_tokens", 1024))
    cap = cfg.get("policy", {}).get("monthly_budget_per_provider", 0) or 0

    want_vision = bool(force_vision) or bool(images)
    plan, chosen_tier, debug = select(
        prompt, cfg=cfg, force_tier=force_tier, max_out=max_out,
        force_vision=want_vision, cost_class=cost_class,
    )
    if not plan:
        if dry_run:
            # Show the user what their config CAN route to — even with zero keys.
            providers_all = build_providers(cfg, only_with_keys=False)
            cands_all = expand_candidates(providers_all, cost_class=cost_class)
            for c in cands_all:
                c.est_cost = (debug["estimated_tokens"] / 1_000_000) * c.input_price + \
                             (max_out / 1_000_000) * c.output_price
            cands_all.sort(key=lambda c: (c.est_cost, TIER_ORDER.get(c.tier, 1), c.provider, c.model))
            plan = cands_all
        else:
            raise RuntimeError(
                f"No providers available (cost_class={cost_class!r}). "
                f"Run `hr doctor` to see what's missing."
            )

    # Filter by max_cost_usd if set
    skipped_for_cost = []
    if max_cost_usd is not None:
        before = len(plan)
        plan = [c for c in plan if c.est_cost <= max_cost_usd]
        skipped_for_cost = before - len(plan)

    out: dict = {
        "classified_tier": chosen_tier,
        "cost_class": cost_class,
        "debug": debug,
        "max_cost_usd": max_cost_usd,
        "skipped_for_cost": skipped_for_cost,
        "plan": [
            {
                "rank": i + 1, "provider": c.provider, "model": c.model,
                "tier": c.tier, "cost_class": c.cost_class,
                "est_cost_usd": round(c.est_cost, 6),
                "vision": c.vision,
            }
            for i, c in enumerate(plan)
        ],
    }
    if not plan and max_cost_usd is not None:
        out["ok"] = False
        out["error"] = (
            f"No candidates within max_cost=${max_cost_usd}. "
            f"{skipped_for_cost} candidate(s) were filtered out. "
            f"Try a higher --max-cost or use --class free."
        )
        return out
    if dry_run:
        out["dry_run"] = True
        return out

    tried: list[dict] = []

    # Circuit-breaker state for *this* route() invocation only.
    # A provider that just hit >= FAIL_SKIP_THRESHOLD retryable errors in
    # this run gets temporarily skipped for REMAINING candidates of the
    # same provider/model (not just this one). Permanent errors are
    # already fail-fast at the _try_candidate level.
    FAIL_SKIP_THRESHOLD = 3
    failed_counts: dict[tuple[str, str], int] = {}
    skipped_circuit: list[dict] = []

    def _circuit_skip(c: Candidate, reason_note: str) -> bool:
        """Check + increment retryable failure counter; return True if
        we should skip this candidate due to the in-process circuit breaker.
        """
        key = (c.provider, c.model)
        if failed_counts.get(key, 0) >= FAIL_SKIP_THRESHOLD:
            skipped_circuit.append({"provider": c.provider, "model": c.model,
                                    "reason": reason_note,
                                    "fails_so_far": failed_counts[key]})
            return True
        return False

    # First pass: try candidates with parallel fallback for speed.
    # The first PARALLEL_BATCH candidates are tried simultaneously with a
    # short timeout. The fastest successful response wins. If all fail,
    # continue sequentially with the remaining candidates.
    PARALLEL_BATCH = 3
    parallel_timeout = min(15, cfg.get("policy", {}).get("parallel_timeout", 15))
    sequential_candidates = list(plan)

    # Take the first N for parallel attempt
    parallel_cands = sequential_candidates[:PARALLEL_BATCH]
    sequential_candidates = sequential_candidates[PARALLEL_BATCH:]

    def _try_parallel(c: Candidate) -> tuple:
        """Wrapper for parallel execution."""
        if _circuit_skip(c, f"{FAIL_SKIP_THRESHOLD}+ retryable failures earlier in this run"):
            return ("circuit_skipped", c, None, [])
        if not budget_check(c.provider, c.est_cost, cap):
            return ("budget", c, None, [{"provider": c.provider, "model": c.model,
                                         "error": "monthly budget cap exceeded"}])
        try:
            result, attempts = _try_candidate(c, prompt, max_out, system, images=images,
                                              key_backoff_base=0.2)  # shorter backoff for parallel
            return ("ok", c, result, attempts)
        except Exception as e:
            return ("error", c, None, [{"provider": c.provider, "model": c.model,
                                        "error": f"{type(e).__name__}: {e}"}])

    with ThreadPoolExecutor(max_workers=PARALLEL_BATCH) as executor:
        futures = {executor.submit(_try_parallel, c): c for c in parallel_cands if not _circuit_skip(c, "circuit-skipped before parallel")}
        for future in as_completed(futures, timeout=parallel_timeout):
            status, c, result, attempts = future.result()
            # Count failures for circuit breaker
            for a in attempts:
                if not a.get("ok") and not a.get("permanent"):
                    failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
            tried.extend(attempts)
            if status == "ok" and result is not None:
                # Cancel remaining futures
                for f in futures:
                    f.cancel()
                budget_record(c.provider, c.est_cost)
                out.update({
                    "selected_provider": c.provider,
                    "selected_model": c.model,
                    "response": result["text"],
                    "usage": result["usage"],
                    "est_cost_usd": round(c.est_cost, 6),
                    "fallbacks_tried": tried,
                    "ok": True,
                })
                return out

    # Remaining candidates (sequential fallback)
    for c in sequential_candidates:
        if _circuit_skip(c, f"{FAIL_SKIP_THRESHOLD}+ retryable failures earlier in this run"):
            continue
        if not budget_check(c.provider, c.est_cost, cap):
            tried.append({
                "provider": c.provider, "model": c.model,
                "error": "monthly budget cap would be exceeded",
            })
            continue
        try:
            result, attempts = _try_candidate(c, prompt, max_out, system, images=images)
        except Exception as e:
            tried.append({
                "provider": c.provider, "model": c.model,
                "error": f"{type(e).__name__}: {e}",
            })
            failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
            continue
        # Count failed attempts (anything not ok=True) for the circuit breaker.
        for a in attempts:
            if not a.get("ok"):
                key = (c.provider, c.model)
                # permanent failures don't count toward circuit (they're fail-fast).
                if a.get("permanent"):
                    continue
                failed_counts[key] = failed_counts.get(key, 0) + 1
        tried.extend(attempts)
        if result is not None:
            budget_record(c.provider, c.est_cost)
            out.update({
                "selected_provider": c.provider,
                "selected_model": c.model,
                "response": result["text"],
                "usage": result["usage"],
                "est_cost_usd": round(c.est_cost, 6),
                "fallbacks_tried": tried,
                "ok": True,
            })
            return out

    # Second pass: per-provider curated fallback chains.
    # Look up the failing provider(s) in policy.fallback_chains and collect
    # candidates from those chains. Backward-compat: the old
    # policy.zai_fallback_chain is merged into fallback_chains under key 'zai'.
    tried_keys = {(t["provider"], t["model"]) for t in tried}
    fallback_chains = dict(cfg.get("policy", {}).get("fallback_chains", {}) or {})
    # Backward-compat: merge old zai_fallback_chain if still present
    old_chain = cfg.get("policy", {}).get("zai_fallback_chain", []) or []
    if old_chain and "zai" not in fallback_chains:
        fallback_chains["zai"] = old_chain

    # Collect providers that failed in the first pass
    failed_providers = set()
    for t in tried:
        if not t.get("ok"):
            failed_providers.add(t.get("provider"))
    # Also add any providers that were circuit-skipped
    for s in skipped_circuit:
        failed_providers.add(s.get("provider"))

    curated_cands: list[Candidate] = []
    seen_chain_pairs: set[tuple[str, str]] = set()

    def _add_chain_entries(for_provider: str) -> None:
        chain = fallback_chains.get(for_provider, [])
        if not chain:
            return
        providers_list = build_providers(cfg)
        for entry in chain:
            if (entry.get("provider"), entry.get("model")) in seen_chain_pairs:
                continue
            seen_chain_pairs.add((entry.get("provider"), entry.get("model")))
            for p in providers_list:
                if p.name != entry.get("provider"):
                    continue
                target_model = entry.get("model")
                m = next((mm for mm in p.models if mm.name == target_model), None)
                if not m:
                    continue
                if cost_class not in ("any", m.cost_class):
                    continue
                curated_cands.append(Candidate(
                    provider=p.name, model=m.name, tier=m.tier,
                    input_price=m.input_price, output_price=m.output_price,
                    context=m.context, vision=m.vision, cost_class=m.cost_class,
                    base_url=p.base_url, api_keys=list(p.keys),
                    est_cost=m.input_price / 1_000_000 * debug["estimated_tokens"] +
                             m.output_price / 1_000_000 * max_out,
                ))
                break

    # First, try chains of all failed providers (most relevant)
    for prov in failed_providers:
        _add_chain_entries(prov)
    # If no failed-provider chains matched, fall back to 'default' chain if any
    if not curated_cands:
        _add_chain_entries("default")

    for c in curated_cands:
        if (c.provider, c.model) in tried_keys:
            continue
        if _circuit_skip(c, f"{FAIL_SKIP_THRESHOLD}+ retryable failures earlier in this run"):
            continue
        if not budget_check(c.provider, c.est_cost, cap):
            continue
        try:
            result, attempts = _try_candidate(c, prompt, max_out, system, images=images)
        except Exception as e:
            tried.append({"provider": c.provider, "model": c.model, "error": str(e)})
            failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
            continue
        for a in attempts:
            if not a.get("ok") and not a.get("permanent"):
                failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
        tried.extend(attempts)
        if result is not None:
            budget_record(c.provider, c.est_cost)
            out.update({
                "selected_provider": c.provider,
                "selected_model": c.model,
                "response": result["text"],
                "usage": result["usage"],
                "est_cost_usd": round(c.est_cost, 6),
                "fallbacks_tried": tried,
                "ok": True,
                "via_curated_chain": True,
            })
            return out

    # Third pass: broader fallback — try every (provider,model) that wasn't in the plan
    # yet, regardless of tier (still respecting cost_class and vision flag).
    broader = expand_candidates(build_providers(cfg, only_with_keys=True), cost_class=cost_class)
    if want_vision:
        broader = [b for b in broader if b.vision]
    # Re-derive est_cost for broader candidates
    for b in broader:
        b.est_cost = (debug["estimated_tokens"] / 1_000_000) * b.input_price + \
                     (max_out / 1_000_000) * b.output_price
    seen = tried_keys | {(c.provider, c.model) for c in plan}
    broader = [b for b in broader if (b.provider, b.model) not in seen]
    broader.sort(key=lambda c: (c.est_cost, TIER_ORDER.get(c.tier, 1), c.provider, c.model))

    for c in broader:
        if _circuit_skip(c, f"{FAIL_SKIP_THRESHOLD}+ retryable failures earlier in this run"):
            continue
        if not budget_check(c.provider, c.est_cost, cap):
            continue
        try:
            result, attempts = _try_candidate(c, prompt, max_out, system, images=images)
        except Exception as e:
            tried.append({"provider": c.provider, "model": c.model, "error": str(e)})
            failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
            continue
        for a in attempts:
            if not a.get("ok") and not a.get("permanent"):
                failed_counts[(c.provider, c.model)] = failed_counts.get((c.provider, c.model), 0) + 1
        tried.extend(attempts)
        if result is not None:
            budget_record(c.provider, c.est_cost)
            out.update({
                "selected_provider": c.provider,
                "selected_model": c.model,
                "response": result["text"],
                "usage": result["usage"],
                "est_cost_usd": round(c.est_cost, 6),
                "fallbacks_tried": tried,
                "ok": True,
                "via_broader_fallback": True,
            })
            return out

    out.update({
        "ok": False,
        "error": "All candidates failed. See fallbacks_tried for details.",
        "fallbacks_tried": tried,
    })
    if skipped_circuit:
        out["circuit_skipped"] = skipped_circuit

    # Auto-fallback: if cost_class='free' failed and auto_fallback is on,
    # retry with the paid pool. This saves the user from manually retrying.
    if auto_fallback and cost_class == "free":
        out["auto_fallback_used"] = True
        retry = route(
            prompt, force_tier=force_tier, max_out=max_out,
            dry_run=dry_run, system=system, cost_class="paid",
            images=images, force_vision=force_vision, cfg=cfg,
            auto_fallback=False,  # don't recurse infinitely
        )
        if retry.get("ok"):
            retry["auto_fallback_used"] = True
            retry["auto_fallback_note"] = "free pool exhausted; fell back to paid"
            return retry
        # Paid also failed — merge fallbacks_tried for a complete picture.
        tried.extend(retry.get("fallbacks_tried", []))
        out["fallbacks_tried"] = tried
        out["error"] = "Free + paid pools both exhausted. See fallbacks_tried."

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Tooling used by the `verify` sub-command (lightweight liveness check).
# ─────────────────────────────────────────────────────────────────────────────

def probe(c: Candidate, timeout: int = 15) -> bool:
    """Cheap 1-token ping. Returns True only on 200 + a non-empty body."""
    if not c.api_keys:
        return False
    api_key = c.api_keys[0]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": c.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        body = _http_post(
            f"{c.base_url.rstrip('/')}/chat/completions",
            headers, payload, timeout=timeout,
        )
    except Exception:
        return False
    text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    # Either a real 200 with text, or a clean 200 with finish_reason=length
    # after 1 token is fine — both mean the model answered.
    return bool(body) and (text != "" or "choices" in body)


__all__ = ["Candidate", "CallError", "route", "select", "probe", "TIER_ORDER"]
