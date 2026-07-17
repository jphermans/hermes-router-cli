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
) -> dict:
    """Route a prompt through the configured providers.

    cost_class:  'free' (default subscription/prepaid pool), 'paid', or 'any'.
    images:      list of URLs or data URLs for vision prompts.
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

    out: dict = {
        "classified_tier": chosen_tier,
        "cost_class": cost_class,
        "debug": debug,
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

    # First pass: try every candidate in the price-ranked plan.
    for c in plan:
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

    # Second pass: a 'curated' fallback chain (zai-overload case lives here).
    tried_keys = {(t["provider"], t["model"]) for t in tried}
    curated = cfg.get("policy", {}).get("zai_fallback_chain", []) or []
    curated_cands: list[Candidate] = []
    for entry in curated:
        for p in build_providers(cfg):
            if p.name != entry.get("provider"):
                continue
            target_model = entry.get("model")
            m = next((mm for mm in p.models if mm.name == target_model), None)
            if not m:
                continue
            # If the user constrained cost_class, only follow a chain entry that fits.
            if cost_class not in ("any", m.cost_class):
                continue
            curated_cands.append(Candidate(
                provider=p.name, model=m.name, tier=m.tier,
                input_price=m.input_price, output_price=m.output_price,
                context=m.context, vision=m.vision, cost_class=m.cost_class,
                base_url=p.base_url, api_keys=list(p.keys),
                est_cost=c.est_cost,  # rough — we re-derive below
            ))
            break

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
