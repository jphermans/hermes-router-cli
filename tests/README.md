# tests/

## smoke.py — unit + CLI smoke tests

```bash
python -m tests.smoke
```

Tests:
* Heuristic classifier correctness (cheap/standard/pro, long-prompt bump)
* Budget save/load/cap
* Multi-key env collection + dedupe
* Cost-class filter
* Candidate ranking with $0 models
* Vision routing forces vision-flagged models only
* Curated fallback chain (simulated provider failure)
* Per-provider key rotation
* Every CLI sub-command (`hr route`, `hr models`, `hr verify`, `hr auth`,
  `hr doctor`, `hr budget`, `hr chat`) including `--pretty`, `--dry-run`,
  `--class free|paid|any`, and key-masking in `hr auth`.

No network is required.

## fake_server.py — a tiny OpenAI-compatible stub for manual testing

Not used by the test suite. Useful when you want to point a real `hr route` at
something local to inspect JSON round-trips end-to-end without spending money.

```bash
# Terminal 1
python -m tests.fake_server
# prints: DUMMY_SERVER_PORT=NNNNN

# Terminal 2 — temporarily edit config.yaml to point a provider at
# http://127.0.0.1:NNNNN, set any dummy env key, then:
hr route --prompt "Hello" --pretty
```

The server accepts any `POST /chat/completions` and always returns a valid 200
+ non-empty body. To test error handling, swap its base path to `/empty` (for
the empty-response gotcha) or `/fail/N` (for the first N requests returning
500 then succeeding — exercises the retry + fallback chain).
