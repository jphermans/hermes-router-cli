<div align="center">

# 🪶 hermes-router

> **A simple LLM router that picks the cheapest capable model — and lets you
> always choose between the free pool and the paid pool.**

[![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#license)
[![Providers](https://img.shields.io/badge/providers-11-2ea44f)](config.yaml)
[![Models](https://img.shields.io/badge/models-37-2ea44f)](config.yaml)
[![Sub-commands](https://img.shields.io/badge/subcommands-7-blue)](#-usage)
[![Tests](https://img.shields.io/badge/tests-28%20passing-brightgreen)](tests/)
[![Plugin](https://img.shields.io/badge/Hermes%20Plugin-1.0.0-8A2BE2)](#-hermes-agent-plugin)
[![Cost](https://img.shields.io/badge/$0%2Ftoken-free%20pool-2ea44f)](#-free-vs-paid--the-one-knob)

A Python CLI that **routes any prompt to the cheapest LLM that can answer it**,
across **11 OpenAI-compatible providers** you already have keys for.
Built on top of the keys Hermes stored for you — no re-exporting needed.

Comes in two flavours: a **standalone CLI** (`hr` — for your terminal) and a
**Hermes Agent plugin** (`hr_route`, `hr_models`, `hr_doctor` — tools Hermes
can call). Same engine, different surfaces.

</div>

---

## 🌊 Table of contents

1. [🌟 Why this exists](#-why-this-exists)
1. [⚡ Quick start: curl → install](#-quick-start-curl--install)
1. [🧩 Hermes Agent plugin](#-hermes-agent-plugin)
1. [📦 Install](#-install)
1. [🗑️ Uninstall](#-uninstall)
1. [🔑 Where keys come from](#-where-keys-come-from)
1. [🎯 Usage](#-usage)
1. [🧠 How it picks](#-how-it-picks)
1. [🐍 Programmatic use](#-programmatic-use)
1. [⚙️ Configuration](#-configuration)
1. [🧪 Testing](#-testing)
1. [⚠️ Known quirks](#-known-quirks)
1. [📜 Background](#-background)
1. [📄 License](#-license)

---

## 🌟 Why this exists

> _"A simple hermes-router that works flawlessly."_  — you, today

There are a lot of LLM providers now. Many of them are *free right now* if you
sit on the right subscription — **z.ai's Coding Plan**, **GitHub Models' free tier**,
**Gemini's flash quota**, **Kilo Code's monthly prepaid**, etc. But you have to
know each one's quirks: which models they expose, what the request format
looks like, whether the token actually costs money.

**`hermes-router` collects this so you don't have to.** Run `hr models` and you'll
see everything that's wired up at a glance. Run `hr route --class free` and you
get the cheapest free answer that still meets the capability bar. Run
`hr route --class paid` when you need a model the free tier doesn't expose.

The router **either succeeds** or **reports every model it tried and why each one
failed** — no silent "I gave up". Every response includes a `fallbacks_tried`
trace.

### ✨ Feature highlights

| Feature | Symbol | Meaning |
|---|---|---|
| **Free vs paid pool**              | 🟢🟡 | Always pick; never silent surprise bills |
| **Multi-key rotation**            | 🔑     | One provider, N keys, automatic failover |
| **Fail-fast on permanent errors** | ⚡     | 404 → 1 attempt, not 16. No retry storms. |
| **Circuit breaker**                | 🛡️     | 3+ retryable failures → skip for the rest of the call |
| **Hermes auth integration**       | 🪶     | Reads `~/.hermes/.env` + `~/.hermes/auth.json` automatically |
| **Vision routing**                 | 👁     | Auto-pick VLMs when images are attached |
| **Zero new dependencies**          | 🪶     | Just PyYAML. Nothing else. |

---

## ⚡ Quick start: curl → install

One-liner — no `git clone` needed. The bootstrap downloads the latest tarball
from GitHub, verifies it, and runs `install.py`. It installs into
**`~/.hermes/hermes-router/`** — right next to Hermes' own config:

```bash
python3 -c "$(curl -fsSL https://raw.githubusercontent.com/jphermans/hermes-router-cli/main/bootstrap-install.py)"
```

After it finishes, `hr` is available on your `PATH`. Try it:

```bash
hr route --prompt "Translate hello to Dutch" --pretty
hr models
hr auth
```

### Pinning to a specific commit

For scripts and CI, pass the exact commit SHA and tarball hash:

```bash
curl -fsSL https://raw.githubusercontent.com/jphermans/hermes-router-cli/main/bootstrap-install.py \
  |  python3 - --sha=<tarball-sha> --prefix=~/.hermes/hermes-router
```

### Forwarding install flags

Everything after `--` goes straight to `install.py`:

```bash
python3 -c "$(curl -fsSL ...)" -- --no-symlink --no-color
```

### How it works

1. ⬇️ Downloads the latest tarball from `codeload.github.com/<repo>/tar.gz/main`
1. 🔐 Verifies SHA-256 (when `--sha=` is provided; otherwise warns with the digest)
1. 📦 Extracts the tarball into a temp dir
1. 📂 Moves the extracted project to `--prefix` (default: `~/.hermes/hermes-router/`)
1. 🚀 Runs `install.py install` from that prefix, forwarding all extra args
1. 🧹 Cleans up the temp dir

The result is a fully installed hermes-router project directory with a
working `.venv/` inside it, ready to use.

> **Where does it install?** By default it installs into **`~/.hermes/hermes-router/`**
> — right alongside Hermes' own config, plugins, and skills. No need to cd
> anywhere, no clutter in your projects folder:
>
> ```bash
> python3 -c "$(curl -fsSL https://raw.githubusercontent.com/jphermans/hermes-router-cli/main/bootstrap-install.py)"
> ```
>
> > 💡 The one-liner fetches the latest `main` branch. A pinned commit SHA
> > is available by adding `--ref <sha>` to the bootstrap flags.
>
> ```
> ...
> 📁 Installing into /home/you/.hermes/hermes-router
> ...
> ```
>
> Use `--prefix` to install elsewhere: `--prefix ~/some/other/path`.

---

## 🧩 Hermes Agent plugin

hermes-router also ships as a **Hermes Agent plugin** — this gives Hermes
access to `hr_route`, `hr_models`, and `hr_doctor` as native Hermes tools.

### How it works

| Layer | What it does |
|---|---|
| **Hermes Agent** (`~/.hermes/config.yaml`) | routes *itself* through its own model picker (Minimax, DeepSeek, etc.) |
| **hermes-router plugin** (`~/.hermes/plugins/hermes-router/`) | adds `hr_route` / `hr_models` / `hr_doctor` tools that Hermes *may* call |
| **hermes-router CLI** (`hr`) | standalone terminal tool — you use it directly |

> The plugin does **not** replace Hermes' own model selection. Hermes still
> picks its own model via `config.yaml`. The plugin gives Hermes the *option*
> to route specific prompts through hermes-router's cost-aware pool.

### Install

The plugin lives in `~/.hermes/plugins/hermes-router/` and is automatically
created by `install.py` (included in the default install; skip with
`--no-plugin`). Enable it with:

```bash
hermes plugins enable hermes-router        # ✅ enable after install
hermes plugins list | grep hermes-router    # should show "enabled"
```

After enabling, start a **new Hermes session** (`/reset` in chat, or exit and
re-launch). The three tools — `hr_route`, `hr_models`, `hr_doctor` — will
appear in Hermes' tool list. The plugin is **auto-generated** with the
correct project path for your machine — no hardcoded paths.

### Available tools

| Tool | What it does | When to use |
|---|---|---|
| `hr_route(prompt, cost_class, tier, ...)` | Routes a prompt through the cheapest capable model | "Route this through the free pool" |
| `hr_models(cost_class, tier)` | Lists available models across all providers | "Which free models do I have?" |
| `hr_doctor()` | Health check — providers, keys, config | "Is everything working?" |

### Important: Tools ≠ Slash Commands

The plugin adds **tools** (`hr_route`, `hr_models`, `hr_doctor`) that the
**Hermes AI agent** can call when you ask it to. These are **not** slash
commands — you don't type `/hr doctor` in the chat. Instead, you just say
what you want in natural language:

| You say... | What happens |
|---|---|
| "Run hr_doctor" | Hermes calls `hr_doctor()` and shows the health report |
| "Route this through the free pool: vertaal hallo naar Frans" | Hermes calls `hr_route()` and returns the answer |
| "Which free models do I have?" | Hermes calls `hr_models()` and shows the list |
| `/hr doctor` | ❌ Unknown command — Hermes has no `/hr` slash command |

> **In short:** tell the agent what you want, don't type a slash command.
> The agent decides when to use the plugin tools.

### Using the tools in a Hermes session

Once the plugin is enabled and you've started a new session (`/reset`), just
tell Hermes in plain language:

> "Use the free pool to translate this: Hello → French"

Hermes will see `hr_route` is available and call it as needed. You can also
be explicit:

> "Run `hr_doctor` to check my providers"
> "Show me `hr_models` with only free providers"
> "Route this through the paid pool: Write a React component"

### Where things live

| What | Installed at | How to use it |
|---|---|---|
| **CLI** (`hr`) | `~/.local/bin/hr` (symlink) | Type `hr route`, `hr doctor`, `hr models` in your terminal |
| **Project** | `~/.hermes/hermes-router/` | Contains `.venv/`, `config.yaml`, source code |
| **Plugin** (Hermes Agent) | `~/.hermes/plugins/hermes-router/` | Installed by `install.py`, enable with `hermes plugins enable hermes-router` |
| **Plugin tools** | Loaded by Hermes at session start | Say "hr_doctor" to the agent — no `/hr` commands |

The CLI (`hr` in your terminal) and the plugin tools (`hr_route` etc. inside
Hermes) use the **same engine** — same config, same keys, same venv. The
difference is just how you reach it.

### What the plugin shares with Hermes

- **API keys** — same `~/.hermes/.env` (set once, works for both)
- **Config** — hermes-router reads `config.yaml` from its own project dir
- **venv** — the `.venv/` inside `~/.hermes/hermes-router/.venv/`

### Uninstall

```bash
hermes plugins disable hermes-router        # disable without removing
hermes plugins remove hermes-router         # delete the plugin entirely
```

---

## 📦 Install

```bash
git clone https://github.com/jphermans/hermes-router.git
cd hermes-router
python3 install.py                  # 🚀 colour output, full setup
```

That single command does everything:

1. 🐍 **Creates a `.venv`** (re-uses an existing one if present)
1. 📚 **Installs PyYAML** into it
1. 🔓 **Marks `hr` executable**
1. 🔗 **Symlinks `~/.local/bin/hr`** so `hr` works from anywhere on your PATH
1. 🧩 **Installs the Hermes plugin** (`~/.hermes/plugins/hermes-router/`) — skip with `--no-plugin`
1. 🩺 **Runs `hr doctor`** and prints a colourised health report

It's **idempotent** — running it again detects existing state and skips the work.

### 🛠️ Other install styles

```bash
python3 install.py --no-color      # 📄 plain text (or set NO_COLOR=1)
python3 install.py --no-symlink    # 🔓 skip ~/.local/bin
python3 install.py --no-plugin     # 🧩 skip Hermes plugin install
python3 install.py --no-doctor     # 🩺 skip the post-install health check
```

If you'd rather not use the installer:

```bash
cd hermes-router
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ln -s "$(pwd)/hr" ~/.local/bin/hr   # optional — for `hr` on PATH
```

---

## 🗑️ Uninstall

The same script uninstalls too. By design it's conservative — it won't delete
the project, your config, or your `.venv/` unless you ask for it.

```bash
python3 install.py uninstall --dry-run       # 👀 see what would be removed
python3 install.py uninstall                 # 🗑️  remove ~/.local/bin/hr (interactive confirm)
python3 install.py uninstall --yes --purge    # 🔥 also delete ./venv/ (no venv left behind)
```

What gets removed, in order:

| # | Target | When | Safety |
|---|---|---|---|
| 1️⃣ | `~/.local/bin/hr` | always — if it points at this project | Skips foreign symlinks; skips regular files; checks resolve target |
| 2️⃣ | `./venv/` | only with `--purge` | Venv is preserved by default (re-installing is the slow step) |
| 3️⃣ | project dir | only with `--purge-project`, never from inside cwd | "**This is the nuclear option.**" |

Re-running is safe: missing items are reported and skipped.

---

## 🔑 Where keys come from

The router reads API keys in **three places**, in priority order:

| # | Source | What lives there | Who writes it |
|---|---|---|---|
| 1️⃣ | **process env** (`GLM_API_KEY=...`) | whatever you `export` in your shell, CI, `systemd --setenv`, etc. | you, manually |
| 2️⃣ | **`~/.hermes/.env`** 🏠 | all Hermes' keys in dotenv form | `hermes auth add`, your hand |
| 3️⃣ | **`~/.hermes/auth.json`** 📋 | Hermes' structured credential pool | `hermes auth add`, Hermes itself |

If Hermes already has keys configured, `hr route` picks them up automatically —
**you don't need to re-export anything**. Just run `hr route --prompt "..."`
and it will discover whichever providers have keys present.

You can override the file paths for testing or container setups:

```bash
export HERMES_ENV_FILE=/etc/hermes/keys.env       # 🏠  default: ~/.hermes/.env
export HERMES_AUTH_FILE=/etc/hermes/auth.json     # 📋  default: ~/.hermes/auth.json
```

### 🔧 Configure your API keys manually

If you don't use `hermes auth add`, you can set them yourself. Pick whichever
form fits you — all three work and merge automatically:

```bash
export GLM_API_KEY=sk-...               # 🔑 single key — singular form
export OPENROUTER_API_KEYS=sk-1,sk-2,sk-3   # 🔑🔑 multiple keys — plural form
export OPENROUTER_API_KEY_2=sk-2         # 🔑 also numbered: KEY_2, KEY_3, ...
```

If you keep keys in `~/.hermes/.env`, `hr auth` will scan it for you:

```bash
hr auth                # 🔒 show which keys are present (values masked)
hr auth --show         # 🔓 show last-4 of each key
```

---

## 🎯 Usage

### 🔍 Dry-run — see the plan, spend nothing

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

The `class` column tells you which pool each candidate comes from.
`$0.000000` free models always rank above priced ones because cost dominates
the sort.

### 🤖 Real call

```bash
hr route --prompt "Translate 'hello' to French" --pretty
```

```
✓ zai/glm-4.5-air  (~$0.000000, 1 tries)

"Bonjour."
```

### 🎯 Choose the pool explicitly

```bash
hr route --prompt "Summarize this PDF" --class free --tier standard   # 🟢 free pool only
hr route --prompt "Reason step-by-step about this proof" \           # 🟡 paid pool only
            --class paid --tier pro
hr route --prompt "Quick translation" --class any --tier cheap       # 🌐 cheapest anywhere
```

`--tier` overrides the heuristic classifier (`cheap` / `standard` / `pro`).
`--class` selects the pool.

### 👁 Vision prompts

```bash
hr route --prompt "What's in this image?" --image https://.../photo.jpg --pretty     # 🌍 URL
hr route --prompt "OCR this" --vision --image data:image/png;base64,...               # 🧬 data URL
```

The router only sends vision prompts to `vision: true` models. The same
`--class free|paid|any` filter applies within the vision pool.

### 🧰 Other sub-commands

| Command | What it does |
|---|---|
| `hr models`                          | 📚 table of every configured model |
| `hr models --class free --tier pro`  | 🎯 filtered |
| `hr models --with-keys-only`         | 🔑 skip providers whose key isn't set |
| `hr verify`                          | 🩺 1-token ping every model (slow; sanity check) |
| `hr doctor`                          | 🩺 config + auth + coverage report |
| `hr budget`                          | 💰 this month's spend per provider |
| `hr chat`                            | 💬 interactive REPL — type prompts, get answers |
| `hr auth`                            | 🔐 which providers have keys |

`hr chat` opens a loop; type prompts, get answers. **Ctrl-D to exit.**

---

## 🧠 How it picks

1. 🎚️ **Classify** the prompt into a tier (`cheap` | `standard` | `pro`)
   using a pure-heuristic regex + token-length scan. No LLM call —
   no extra cost.

   * **Pro signals:** `reason`, `analyze`, `debug`, `multi-step`, `refactor`,
     `architect`, `optimize`, `investigate`, …
   * **Cheap signals:** `translate`, `summarize`, `rephrase`, `extract`, `list`,
     `title`, `convert`, `format`, `yes or no`, …
   * **Long prompts** (> ~4 kB) bump cheap → standard (larger context).

   Override with `--tier`.

1. 🧹 **Filter** candidates to the pool you asked for (`free` / `paid` /
   `any`), then to models that meet the classified tier (with optional
   tier-upgrade). Vision prompts: only vision-capable models survive.

1. 💱 **Rank** the survivors by `est_cost = in_tokens × input_price +
   out_tokens × output_price`. `$0` models always win. Ties broken by
   capability tier.

1. ⚡ **Call** the cheapest. On failure, **retry inside the same provider by
   rotating keys** (if the provider has multiple), with exponential backoff.

   **Fail-fast on permanent errors**: HTTP 400/401/403/404/422/… mean
   "this request itself is wrong" — don't retry, don't rotate keys, don't
   try other variants. Skip immediately to the next candidate. Cuts wasted
   time on auth/permissions/model-not-found from O(keys × retries) to **1
   attempt**.

1. 🔁 **Fall back** in this order:
   1. Next-cheapest candidate in the price-ranked plan.
   1. The configured **curated chain** (defaults to a small set of
      good-fast-cheap alternatives; configurable in `config.yaml` as
      `policy.zai_fallback_chain`).
   1. Any other model that wasn't in the plan, regardless of tier, still
      respecting `cost_class` and `vision`.

   **🛡️ Circuit breaker**: in any single `route()` invocation, a
   `(provider, model)` that accumulates **3+ retryable failures**
   (429/5xx/timeout) gets temporarily skipped for the rest of the run. The
   router doesn't waste budget hammering a known-broken provider while
   looking for a working one elsewhere.

1. 📋 **Return** either the answer, or a complete trace of every attempt +
   error. Each attempt entry records whether it was `permanent` (skipped
   all keys), `retryable` (transient, retried with backoff), or `ok`
   (succeeded).

The whole thing runs in one Python process. **No daemon, no DB, no proxy.**

---

## 🐍 Programmatic use

```python
from smart_router.route import route

result = route("Translate hello to French", cost_class="free")
print(result["selected_provider"], result["response"])
print("💰 cost:", result.get("est_cost_usd"))
print("🔁 tried:", result.get("fallbacks_tried"))
```

```python
# With images 👁
result = route("What's in this?", images=["data:image/png;base64,iVBOR..."])

# With a specific tier 🎯
result = route("Reason about X", force_tier="pro", cost_class="paid", max_out=1024)
```

All costs are **estimated**; real billed amounts may differ slightly because the
router estimates input tokens at ~4 chars/token.

---

## ⚙️ Configuration

Edit `config.yaml` to add/remove providers or change prices:

```yaml
providers:
  my_provider:
    env_key: MY_API_KEY          # 🔑 env var holding the key(s)
    base_url: https://api.my.com/v1
    cost_class: paid             # 🟡 or "free"
    models:
      - name: my-fast
        tier: cheap
        input_price: 0.10        # 💵 USD per 1M tokens
        output_price: 0.50
        context: 32768
        vision: false            # 👁 set true if model accepts images
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

### 🟢 Free vs paid — the one knob

`cost_class: free` and `cost_class: paid` are *user-facing concepts*. You can
always pick one with `--class`. The router never silently routes between them —
there's no `promote free to paid if free fails` rule.

| Pool | Examples |
|---|---|
| 🟢 **free** | Subscription plans (z.ai Coding Plan, Kilo Code, MiniMax prepaid, …), public free tiers (Gemini free, GitHub Models free, OpenRouter `:free` models), local inference (base_url + no API key needed) |
| 🟡 **paid** | Everything with a real USD bill |

If you want "**always free first, paid only when free failed**":

```bash
hr route --prompt "..." --class free   # 🟢 try this first
hr route --prompt "..." --class paid   # 🟡 retry with this if free failed
```

The router itself doesn't bridge the two because the use case is genuinely
different (zero-extra-cost vs billable-by-API).

---

## 🧪 Testing

A full test suite (no external dependencies beyond `pyyaml`):

```bash
source .venv/bin/activate
python -m tests.smoke     # 🧪 21 unit + CLI tests, no network required
```

This exercises:

- 🧠 Heuristic classifier (cheap / standard / pro / long-prompt bump)
- 💰 Budget save/load/record/cap
- 🔑 Multi-key env collection + dedupe
- 🟢🟡 Cost-class filter (free / paid)
- 👁 Vision routing forces vision-flagged models only
- 🔁 Curated fallback chain (simulated provider failure)
- 🔂 Per-key rotation
- ⚡ Permanent vs retryable HTTP errors (`_classify_http`)
- 🚫 Fail-fast: one attempt on a 404, not N keys
- 🛡️ Circuit breaker limiting retryable failures
- 🪶 All CLI sub-commands (`route`, `models`, `verify`, `auth`, `doctor`,
  `budget`, `chat`) including `--pretty`, `--dry-run`,
  `--class free|paid|any`, and key-masking in `hr auth`.

### 🧬 Live integration test against a fake server

```bash
# Terminal 1: a tiny test server that always returns 200
python -m tests.fake_server

# Terminal 2: drive it with a side-loaded config that points at the dummy URL,
# then run hr route — see `https://github.com/.../tests/fake_server.py`.
```

The server accepts any `POST /chat/completions` and always returns a valid 200
+ non-empty body. To test error handling, swap its base path to `/empty` (for
the empty-response gotcha) or `/fail/N` (for the first N requests returning
500 then succeeding — exercises the retry + fallback chain).

---

## ⚠️ Known quirks

- **Reasoning models** (z.ai GLM-5.x, MiniMax M-series, deepseek-reasoner)
  burn output tokens on internal `ˆÕÈ` blocks before the visible answer
  appears. Use `--max-tokens 300+` for cheap tasks and `≥ 800` for pro tasks
  so the model has budget left over for the actual response. `route()`
  already triggers fallback on an empty response.

- **`hr verify`** fires one HTTP request per model — useful for a one-shot
  health check, expensive at scale. Don't add it to cron without thinking.

- **The curated fallback chain** (`zai_fallback_chain`) is currently keyed
  to z.ai overloading — if you remove z.ai the chain still works but you
  might prefer to rename the field to `primary_fallback_chain` to reflect
  reality.

---

## 📜 Background

This project grew out of two earlier routers. One (`Shaf2665/Hermes-router`) is
a full **Flask proxy** with key rotation, dashboard, response caching,
SQLite, and a Codex OAuth importer — a different shape: a server. We borrowed:

- 🔑 the **multi-key env convention** (`KEY`, `KEYS`, `KEY_2`)
- ⚡ the **priority order** for handling provider overload
  (price rank → curated chain → broader sweep)

…and explicitly **didn't borrow** 🚫:

- 🐢 the server, the dashboard, the database, the SSE streaming
- 📚 the model-ratings dictionary (we keep tiers simpler)
- 🔐 the auth-key dashboard

The other parent was a personal `smart-llm-router` that picked cheapest-capable
from a flat pool of OpenAI-compatible providers. We extended that with:

- 🟢🟡 explicit `--class free|paid` (it had no notion of pool)
- 🔂 per-provider key rotation
- 🔁 multi-tier fallback trace (`fallbacks_tried`)
- 🧰 7 sub-commands instead of one giant flag set
- 👁 vision routing with multi-image payloads
- 💻 a real CLI with sub-commands and a REPL

---

## 📄 License

MIT — see the [LICENSE](LICENSE) file.

---

<div align="center">

<sub>Built with 🪶 by JP's Hermes Agent on a Raspberry Pi 5.
All routes tested, no daemon required. 🧪✓</sub>

</div>
