# Friday Test Suite

A comprehensive offline test suite for the Friday agent: **~1,870 tests** covering
the standalone Python modules (unit) and the Flask backend's 240 routes (API),
plus the pre-existing vault-crypto and Playwright UI tests.

The entire offline suite runs in **~45 seconds**, needs **no live server, no
network, and no API keys**, and **never makes a paid model call** — every LLM
entry point is hard-stubbed.

## Running

```bash
# Full offline suite (unit + API + vault) — the default
venv/Scripts/python.exe -m pytest

# Just the fast unit layer (no server import, ~0.1s collection)
venv/Scripts/python.exe -m pytest tests/unit

# Just the API/route layer
venv/Scripts/python.exe -m pytest tests/api

# A single module
venv/Scripts/python.exe -m pytest tests/unit/test_vault_access.py -v

# The Playwright UI tests — these need a REAL server on localhost:3000
#   (start it first: venv/Scripts/python.exe server.py)
venv/Scripts/python.exe -m pytest tests/test_friday_ui.py
```

## How isolation works

The suite is hermetic by construction (see `tests/conftest.py` and
`tests/api/conftest.py`):

| Concern | Mechanism |
|---|---|
| Background daemon loops (kill-hotkey, scheduler, news archiver, notif triggers) | `FRIDAY_TESTING=1` set before `import server` gates every module-level `Thread(...).start()`. Import becomes inert. |
| Touching the real user's data | The Windows home dir is redirected to a throwaway temp dir before import, so every `~/.friday`, creations dir, vault, and `settings.json` resolves under isolation. |
| Paid / network LLM calls | An **autouse** fixture stubs `_generate_text`, `_generate_agent`, `_call_claude`, `_call_ollama`, `_call_openai`, and returns a non-None sentinel from `get_anthropic_client`. Friday self-loads real keys from its launch scripts at import, so env scrubbing alone isn't enough — the call sites are patched. A route that reaches an *unmocked* model path fails loudly instead of calling out. |
| Gemini-direct routes (`create/*`, `analyze`, `voice/tts`) | The `mock_gemini` fixture patches `google.genai.Client`. |
| Routes that shell out (`git/*`, `computer/open`, `vibe-code/*`, `system`) | The devtools test file installs an autouse fixture that replaces `subprocess.run/Popen/check_output`, `os.startfile`, and helper launchers with recorders — **no real git push, app launch, or terminal spawn can occur.** |

## Layout

```
tests/
  conftest.py                 # root: hermetic env + light fixtures (no server import)
  test_smoke.py → api/        # import safety, route-map sanity, LLM kill-switch
  unit/                       # one file per module; imports only that module (fast)
    test_vault_access.py          # ← the unit exemplar
    test_model_router.py  test_ollama_manager.py
    test_epistemic_engine.py  test_behavioral_monitor.py
    test_source_trust_graph.py  test_source_trust_federation.py
    test_skillopt_engine.py  test_dynamic_rings.py
    test_notifications.py  test_notifications_engine.py  test_voice_personality.py
    test_liquid_ui.py  test_skill_registry.py  test_context_compressor.py
    test_people_graph.py  test_cognitive_memory.py  test_proof_of_integrity.py
    test_context_pruner.py  test_conversation_memory.py
  api/                        # Flask test_client; imports server once
    conftest.py                   # server import + LLM/Gemini stubs + client fixtures
    test_routes_core.py           # ← the API exemplar (PURE / FILE / LLM patterns)
    test_smoke.py
    test_security_memory_routes.py     # memory, governance, integrity, epistemic, security
    test_news_feed_routes.py           # news, sources, source-trust, federation, briefings
    test_people_workspace_routes.py    # trust, people, contacts, personality, finance, health…
    test_wiki_settings_routes.py       # wiki, settings, setup, skills, context-log, creations
    test_generation_routes.py          # create/*, analyze, voice/tts, chat/send, draft, outreach…
    test_devtools_system_routes.py     # git, repos, files, vibe-code, code, computer, flow, tasks…
  test_vault_crypto.py        # pre-existing: AES-256-GCM + Argon2id + HMAC
  test_vault_at_rest.py       # pre-existing: vault encryption at rest
  test_friday_ui.py           # pre-existing: Playwright UI (needs a live server)
```

### Writing more tests

Copy the nearest exemplar. Unit tests import their target module directly and
assert pure-logic invariants. API tests use the `client` fixture; the LLM is
already stubbed, so you assert request→response shape, status codes, file
round-trips through the isolated home, and that bad input yields a 4xx (never a
500). When a test surfaces a real product bug you can't fix in scope, mark it
`@pytest.mark.xfail(reason=...)` so it's documented without breaking the run.

## Bugs / weaknesses this suite surfaced

These were found while writing the tests. The first is **fixed**; the rest are
documented (and, where reproducible, guarded by a test).

1. **`POST /api/settings` 500 on a non-string `personality`** — *fixed.* The
   handler passed the raw value to `_save_agent_personality().strip()`, so a
   dict payload raised `AttributeError` → 500. Now returns a clean 400. Guarded
   by `test_settings_personality_non_string_is_400_not_500`.
2. **Duplicate `@app.route` registration** for the behavioral-security routes
   (`/api/security/behavioral-report` etc. registered ~twice, lines ~9145 and
   ~9424). Flask silently keeps the first; the later `@login_required` variant is
   dead. Latent maintenance hazard — worth de-duping.
3. **`notifications_engine` 1-second timestamp resolution** — two `push()` calls
   in the same second get identical `created_at`, making "newest-first" ordering
   within a priority tier non-deterministic. Documented by the one remaining
   strict `xfail` (`test_same_priority_newer_first`).
4. **`cognitive_memory.verify_chain` is linkage-first** — it walks `prev_hash`
   pointers; tamper-evidence is only as strong as the per-entry `entry_hash`
   recomputation. Worth a focused review given Friday's "sovereign / tamper-
   evident" positioning.
5. **`datetime.utcnow()` deprecation** across many modules (epistemic, cognitive,
   skillopt, liquid_ui, notifications_engine, …) — deprecated in 3.12+, removed
   in a future Python. A mechanical `datetime.now(datetime.UTC)` sweep.
6. **`liquid_ui` minor issues** — `re.split(..., 1)` passes `maxsplit`
   positionally (deprecated); `classify_complexity("")` scores `0.005` instead of
   `0.0` (harmless — still the `trivial` tier).
```
