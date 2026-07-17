# hermes-router

> **A simple LLM router that picks the cheapest capable model — and lets you
> always choose between the free pool and the paid pool.**

hermes-router is a lightweight Python CLI. Given a prompt, it picks a
provider/model that can answer it well enough **at the lowest cost**, calls it,
and only spends a real API token when you ask it to. If that model fails, it
falls back through a curated chain — *not* a random retry storm.

It has one knob you can always reach for:

```
--class free     # subscription / prepaid / free tier  ($0 per token)
--class paid     # billed per token                     (USD per 1M)
--class any      # pick the cheapest of both             (default)
```

That choice is the whole point: you always decide which world you're routing
in. Free isn't a fallback for paid — they're two separate pools you pick between.

```
   prompt ──► classifier ──► free or paid pool ──► ranked plan
                                                       │
              200 + text  ◄──── call + retry + fallback
                                                       │
                                          ┌────────────┴────────────┐
                                          ▼                         ▼
                                    succeeded?              try curated chain
                                          │                then broader sweep
                                          ▼                         │
                                       done  ◄──────────────────────┘
```

## Why this exists

There are a lot of LLM providers now. Many of them are *free right now* if you
sit on the right subscription — z.ai's Coding Plan, GitHub Models' free tier,
Gemini's flash quota, etc. But you have to know each one's quirks: which models
they expose, what the request format looks like, whether the token actually
costs money.

`hermes-router` collects this so you don't have to. Run `hr models` and you'll
see everything that's wired up at a glance. Run `hr route --class free` and you
get the cheapest free answer that still meets the capability bar. Run
`hr route --class paid` when you need a model the free tier doesn't expose.

Crucially: the route either succeeds **or** reports every model it tried and
why each one failed. There's no silent "I gave up" — the response includes a
`fallbacks_tried` trace.

## Install

```bash
git clone https://github.com/<you>/hermes-router.git
cd hermes-router
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Add it to your PATH (optional):

```bash
ln -s "$(pwd)/hr" ~/.local/bin/hr
```

### Configure your API keys

Tokens are read from environment variables, never stored in the repo. Pick the
form that fits you — all three work and merge automatically:

```bash
# single key — singular form
export GLM_API_KEY=sk-...

# multiple keys — plural form
export OPENROUTER_API_KEYS=sk-1,sk-2,sk-3

# also numbered: OPENROUTER_API_KEY_2, _3, ...
```

If you already keep these in `~/.hermes/.env`, `hr auth` will scan it for you:

```bash
hr auth                # show which keys are present (values masked)
hr auth --show         # show last-4 of each key
```

## Usage

### Dry-run — see the plan, spend nothing

```bash
hr route --prompt "Translate 'hello' to French" --dry-run --pretty
```

```
[dry-run] classified as cheap  cost_class=any
   1.     zai                glm-4.5-air                          [cheap   /free ]  $0.000000
   2.     kilo               tencent/hy3                          [pro     /free ]  $0.000000
   3.     openrouter         meta-llama/llama-3.1-8b-instruct     [cheap   /paid ]  $0.000013
   4.     venice             openai-gpt-oss-120b                  [cheap   /paid ]  $0.000023
   ...
```

The `class` column on the right tells you which pool each candidate comes from.
`$0.000000` free models always rank above priced ones because cost dominates
the sort.

### Real call

```bash
hr route --prompt "Translate 'hello' to French" --pretty
```

Returns the chosen provider/model, the answer, usage in tokens, and the chain
of attempts if there were any failures.

```
✓ zai/glm-4.5-air  (~$0.000000, 1 tries)

"Bonjour."
```

### Choose the pool explicitly

```bash
hr route --prompt "Summarize this PDF" --class free --tier standard
hr route --prompt "Reason step-by-step about this proof" --class paid --tier pro
```

`--tier` overrides the heuristic classifier (`cheap` / `standard` / `pro`).
`--class` selects the pool. Use both when the auto-pick isn't what you want.

### Force a specific model

If you just want to call one model directly without routing, `hr` isn't the
right tool — but `route()` will accept any candidate in the plan. Forcing a
specific provider is out of scope; this is a *router*.

### Vision prompts

```bash
hr route --prompt "What's in this image?" --image https://.../photo.jpg --pretty
hr route --prompt "OCR this" --vision --image data:image/png;base64,...
```

The router only sends vision prompts to `vision: true` models. The same
`--class free|paid|any` filter applies within the vision pool.

### Other sub-commands

```bash
hr models                          # table of every configured model
hr models --class free --tier pro  # filtered
hr models --with-keys-only         # skip providers whose key isn't set
hr verify                          # 1-token ping every model (slow; sanity check)
hr doctor                          # config + auth + coverage report
hr budget                          # this month's spend per provider
hr chat                            # REPL: prompt, get answer, repeat
hr auth                            # which providers have keys
```

`hr chat` opens a loop; type prompts, get answers. Ctrl-D to exit.

## How it picks

1. **Classify** the prompt into a tier (`cheap` | `standard` | `pro`) using a
   pure-heuristic regex + token-length scan. No LLM call — no extra cost.

   * Pro signals: `reason`, `analyze`, `debug`, `multi-step`, `refactor`,
     `architect`, `optimize`, `investigate`, ...
   * Cheap signals: `translate`, `summarize`, `rephrase`, `extract`, `list`,
     `title`, `convert`, `format`, `yes or no`, ...
   * Long prompts (> ~4 kB) are bumped from cheap → standard.

   Override with `--tier`.

2. **Filter** candidates to the pool you asked for (`free` or `paid` or `any`),
   then to models that meet the classified tier (with optional tier-upgrade).
   Vision prompts: only vision-capable models survive this step.

3. **Rank** the survivors by `est_cost = in_tokens × input_price + out_tokens ×
   output_price`. $0 models always win. Ties broken by capability tier.

4. **Call** the cheapest. On failure, **retry inside the same provider by
   rotating keys** (if the provider has multiple), with exponential backoff.

   **Fail-fast on permanent errors**: HTTP 400/401/403/404/422/etc. mean "this
   request itself is wrong" — don't retry, don't rotate keys, don't try
   other variants of this same request. Skip immediately to the next
   candidate. Cuts wasted time on auth/permissions/model-not-found from
   O(keys × retries) per failed provider to **1 attempt**.

5. **Fall back** in this order:
   1. Next-cheapest candidate in the price-ranked plan.
   2. The configured curated chain (defaults to a small set of good-fast-cheap
      alternatives; configurable in `config.yaml` as
      `policy.zai_fallback_chain`).
   3. Any other model that wasn't in the plan, regardless of tier, still
      respecting `cost_class` and `vision`.

   **Circuit breaker**: in any single route() invocation, a (provider, model)
   that accumulates 3+ retryable failures (429/5xx/timeout) gets temporarily
   skipped for the rest of the run. The router doesn't waste budget hammering
   a known-broken provider while looking for a working one elsewhere.

6. **Return** either the answer, or a complete trace of every attempt + error.
   Each attempt entry records whether it was `permanent` (skipped all keys),
   `retryable` (transient, retried with backoff), or `ok` (succeeded).

The whole thing runs in one Python process. No daemon, no DB, no proxy.

## Programmatic use

```python
from smart_router.route import route

result = route("Translate hello to French", cost_class="free")
print(result["selected_provider"], result["response"])
print("cost:", result.get("est_cost_usd"))
print("tried:", result.get("fallbacks_tried"))
```

```python
# With images
result = route("What's in this?", images=["data:image/png;base64,iVBOR..."])
# Or with a specific tier
result = route("Reason about X", force_tier="pro", cost_class="paid", max_out=1024)
```

All costs are estimated; real billed amounts may differ slightly because the
router estimates input tokens at ~4 chars/token.

## Configuration

Edit `config.yaml` to add/remove providers or change prices:

```yaml
providers:
  my_provider:
    env_key: MY_API_KEY          # env var holding the key(s)
    base_url: https://api.my.com/v1
    cost_class: paid             # or "free"
    models:
      - name: my-fast
        tier: cheap
        input_price: 0.10        # USD per 1M tokens
        output_price: 0.50
        context: 32768
        vision: false            # set true if model accepts images
      - name: my-pro
        tier: pro
        input_price: 1.00
        output_price: 3.00
        context: 200000
        vision: true
```

`cost_class` can be set on the provider (default for all models in it) or per
model (overrides). Two providers can share `env_key`; `hr auth` will see one
combined entry.

### Free vs paid — the one knob

`cost_class: free` and `cost_class: paid` are *user-facing concepts*. You can
always pick one with `--class`. The router never silently routes between them —
there's no `promote free to paid if free fails` rule.

The `free` pool is for things like:
* Subscription plans (z.ai Coding Plan, Kilo Code, MiniMax prepaid, ...)
* Public free tiers (Gemini free, GitHub Models free, OpenRouter `:free` models)
* Local inference (base_url + no API key needed)

The `paid` pool is for everything with a real USD bill.

If you want "always free first, paid only when free fails", use:
```bash
hr route --prompt "..." --class free   # try this first
hr route --prompt "..." --class paid   # retry with this if free failed
```
The router itself doesn't bridge the two because the use case is genuinely
different (zero-extra-cost vs billable-by-API).

## Testing

A full test suite (no external dependencies beyond `pyyaml`):

```bash
source .venv/bin/activate
python -m tests.smoke
```

This exercises:
* Heuristic classifier (cheap / standard / pro / long-prompt bump)
* Budget save/load/record/cap
* Multi-key provider env collection + dedupe
* Cost-class filter
* Vision routing
* Curated fallback chain
* Per-key rotation
* Every CLI sub-command (`route`, `models`, `verify`, `auth`, `doctor`,
  `budget`, `chat`) including `--pretty`, `--dry-run`, `--class free|paid|any`,
  and key-masking in `hr auth`.

Live integration test against a fake OpenAI-compatible server:

```bash
# Terminal 1: a tiny test server that always returns 200
python -m tests._dummy_server

# Terminal 2: drive it with a side-loaded config that points at the dummy URL,
# then run hr route — see `https://github.com/.../tests/_dummy_server.py`.
```

## Known quirks

* **Reasoning models** (z.ai GLM-5.x, MiniMax M-series, deepseek-reasoner) burn
  output tokens on internal `ˆÕÈ` blocks before the visible answer appears.
  Use `--max-tokens 300+` for cheap tasks and `≥ 800` for pro tasks so the
  model has budget left over for the actual response. `route()` already
  triggers fallback on an empty response.

* **`hr verify`** fires one HTTP request per model — useful for a one-shot
  health check, expensive at scale. Don't add it to cron without thinking.

* **The curated fallback chain** (`zai_fallback_chain`) is currently keyed to
  z.ai overloading — if you remove z.ai the chain still works but you might
  prefer to rename the field to `primary_fallback_chain` to reflect reality.

## Background

This project grew out of two earlier routers. One (Shaf2665/Hermes-router) is a
full Flask proxy with key rotation, dashboard, response caching, SQLite, and a
Codex OAuth importer — a different shape: a server. We borrowed:
* the multi-key env convention (`KEY`, `KEYS`, `KEY_2`)
* the priority order for handling provider overload (price rank → curated
  chain → broader sweep)

…and explicitly **didn't borrow**:
* the server, the dashboard, the database, the SSE streaming
* the model-ratings dictionary (we keep tiers simpler)
* the auth-key dashboard

The other parent was a personal smart-llm-router that picked cheapest-capable
from a flat pool of OpenAI-compatible providers. We extended that with:
* explicit `--class free|paid` (it had no notion of pool)
* per-provider key rotation
* multi-tier fallback trace (`fallbacks_tried`)
* 7 sub-commands instead of one giant flag set
* vision routing with multi-image payloads
* a real CLI with sub-commands and a REPL

## License

MIT — see the LICENSE file.
