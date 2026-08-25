# Privacy-layer packaging audit — 2026-08-24

**Question asked:** the sensitivity classifier's docstring described a four-layer
defence. Does the shipped artifact actually run four layers?

**Answer:** no, and it never has. Two of the four layers have never executed in
any environment that has ever existed. The code is real and correct; the
packaging is what was missing. Every number below was measured on this machine,
not estimated.

---

## 1. What was verified (independently, before acting)

| Claim under test | Verdict | Evidence |
|---|---|---|
| Presidio integration exists in the gate | **True, with a correction** | It is in `services/sensitivity_classifier.py` (`_load_presidio`, `_presidio_tier`), not in `egress_gate.py`. The gate *consumes* the classifier. |
| `presidio-analyzer` missing from `requirements.txt` | **True** | Not present. Never was. |
| `presidio-analyzer` missing from the venv | **True** | `find_spec('presidio_analyzer')` → `None`. So **Layer 2 has never run anywhere** — not in dev, not in the .exe. |
| `sentence_transformers` in PyInstaller `excludes` | **True** | `AgentFriday.spec` line 67. It *is* in `requirements.txt`, so Layer 3 runs from source but not when frozen. |
| The shipped `.exe` runs only regex + keywords | **True** | `build/AgentFriday/PYZ-00.toc`: `sentence_transformers` 0 hits, `presidio` 0 hits, `spacy` 0 hits, `sensitivity_classifier` present. |

### A correction to the report that prompted this work

`egress_gate.py`'s own docstring was **already honest**. It says Presidio and
friends "are under separate evaluation; nothing here presumes that decision" and
describes itself as "keyword- and pattern-driven". The false four-layer claim was
in `sensitivity_classifier.py`. Worth stating plainly, because the fix belonged in
a different file than the report implied — and `egress_gate.py` was off-limits
this session anyway (another session is editing it for a news-gating fix).

## 2. The second bug — silent degradation

This is the more important one, as suspected.

Both optional layers loaded inside bare handlers:

```python
except Exception:
    _ANALYZER = None      # no log, no warning, no raise
```

`grep -n "log\|warn\|print\|logger"` over all 424 lines of the module returned
**nothing**. There was no channel by which the shortfall could ever have been
noticed. The docstring was the only description of the behaviour, and it was
wrong.

### A third bug, found while confirming the second

The failure path stored `None` — which is *also* the "not yet attempted" value.
So a missing dependency was re-imported on **every** `classify()` call, taking a
lock and re-walking `sys.path` each time, forever, for a layer that was never
going to load.

| | before | after |
|---|---|---|
| `_load_presidio()` steady-state | 0.8599 ms/call | 0.00007 ms/call |

Fixed with an `_UNTRIED` sentinel in both `_load_presidio` and `_load_embedder`.
The miss now costs once, and logs once, at WARNING.

## 3. Measured costs

### Layer 3 (embeddings) — the reason the exclusion stays

| item | measured |
|---|---|
| `sentence_transformers` itself | 5 MB |
| `torch`, which it requires | **4,374 MB** |
| current `AgentFriday.exe` | 152 MB |
| cold load (first use) | 22.41 s |
| steady-state `classify()` | 13–17 ms |

Bundling a 4.4 GB dependency into a 152 MB desktop binary is not a sane trade.
**The exclusion is correct and stays.** What was wrong was not saying so.

### Layer 2 (Presidio) — cheaper, but worse

Presidio needs spaCy, **not** torch, so it is a genuinely different proposition:

| item | measured |
|---|---|
| wheels (`presidio-analyzer` + spaCy + 16 deps) | 46.8 MB |
| installed on disk | 287 MB |
| + `en_core_web_sm` | 302 MB total |
| cold load, model already on disk | 19.12 s |
| cold load, first ever run | **69.48 s** |
| steady-state analysis | median 8.5 ms, max 34.2 ms |

**Undisclosed behaviour worth flagging:** constructing `AnalyzerEngine()`
**downloaded `en_core_web_lg` (~590 MB) from the network at runtime**, without
being asked. For a product whose selling point is that data stays on the device,
a privacy layer that reaches out to the internet the first time it initialises is
a design fact that needs a decision, not a default.

## 4. The finding that settles the enforcement question

Presidio was run against twelve entirely benign prompts containing no personal
information whatsoever. Six were escalated from TIER_1 to TIER_2 — i.e. would
have been **withheld from the cloud**:

```
FALSE-POSITIVE T2: Can you help me draft a note to the team about next week...
FALSE-POSITIVE T2: What is the weather going to be like tomorrow?
FALSE-POSITIVE T2: The CDC reported flu activity rose in Texas during January 2026.
FALSE-POSITIVE T2: Remind me to buy milk on Friday.
FALSE-POSITIVE T2: Write a haiku about autumn in Kyoto.
FALSE-POSITIVE T2: What did Shakespeare write in 1601?

false-positive rate: 6/12 = 50%
```

`DATE_TIME` scores 0.85 on the word "tomorrow" — above the 0.8 threshold the code
already uses. In a chat assistant, a large share of ordinary turns mention a time
or a place.

Two of these deserve emphasis:

- **"What is the weather going to be like tomorrow?"** — enforcing Presidio would
  route this to a local model. That is the fourth over-broad-classification scar,
  arriving exactly where the previous three did.
- **"The CDC reported flu activity rose in Texas during January 2026."** — this is
  the *same* news-gating failure another session is fixing right now. Presidio
  would make it worse, via `LOCATION` + `DATE_TIME`.

And on the one case with genuine PII, Presidio returned TIER_2 where the existing
regex layer already returned TIER_3 — **weaker than what is already shipping**.

That combination — no gain where it matters, 50% false positives where it does
not — is the answer.

## 4b. CORRECTION (2026-08-25): the two shipped layers were NOT adequate

An earlier draft of this document described Layers 1a+1b in the frozen build as a
"deliberate, now-documented position" without qualifying how big the resulting
hole was. That reading was too comfortable, and a concurrent session proved it
wrong by finding the hole in production.

**There has never been a phone, street-address, or account-number regex in
Friday.** Not in Layer 1a, not anywhere. Those shapes were nominally the
responsibility of Layer 2 (Presidio `PHONE_NUMBER` / `LOCATION`) and Layer 3
embeddings — that is, of the two layers this audit has just shown do not exist in
the shipped artifact. The vault path made it worse still: `vault_access.classify`
passes `use_embeddings=False`, so Layer 3 was off even where it *was* installed.

The consequence was live, not theoretical. Real wiki files of Stephen's reached
Anthropic **verbatim**: `vault_access.gate_content(raw, "anthropic")` returned the
string unchanged and logged `[VAULT] ALLOW provider=anthropic tier=TIER_1`. The
only thing that had ever stood between `emergency contact: 555-1234` and the cloud
was the literal English words "phone number" happening to appear nearby.

Closed in `66fb53e` and `16c9fb5` by putting those detectors at **Layer 1a**,
which is the correct home for them: Layer 1a is mode-independent, so one fix
covers the routing path and the egress path together, and it cannot regress the
three over-redaction incidents this codebase has already paid for — no phone or
house-number pattern can re-fire on "courtesy", on "Sovereign Vault", or on CDC
flu guidance.

**This strengthens the recommendation below rather than weakening it.** The gap
was real, and it sat exactly where Presidio was supposed to be — which is the
strongest argument anyone could make *for* adopting Presidio. It still should not
be adopted, because the same gap was closed by four deterministic patterns with no
false positives, no 287 MB dependency, no 19 s cold start, and no runtime model
download. The correct lesson is not "we needed NER". It is **"a capability
assigned to a layer that does not load is not assigned to anything"** — and the
fix is to put the detector in a layer that actually ships.

## 5. Recommendation

**Do not enforce Presidio.** Not now, and not at these thresholds. Adopting it
because it was already written would be the wrong reason, and the measurements do
not support it. Concretely:

1. **Keep the `sentence_transformers` exclusion.** 4.4 GB into a 152 MB binary is
   not a trade worth making. Layers 1a+1b in the frozen build is a deliberate,
   now-documented position.
2. **Leave Presidio uninstalled by default.** It is commented out in
   `requirements.txt` with exact enable instructions.
3. **If it is ever enabled, shadow mode is the only default.** Enforcement needs
   `FRIDAY_PRESIDIO_ENFORCE=1`, which nothing sets.
4. **Before any enforcement decision, drop `DATE_TIME` and `LOCATION`** from
   `private_types`, or raise their threshold well above 0.85. They are the entire
   false-positive population in this sample.
5. **The honest headline:** deterministic patterns at Layer 1a — *including the
   phone/address/account detectors that were missing entirely until 2026-08-25,
   see §4b* — plus the keyword layer are the right answer for a desktop app. Not
   because the shipped layers were already sufficient (they demonstrably were
   not), but because every gap found so far has been closable with a regex that
   costs microseconds and ships. The real win here is not a new layer — it is
   that the gate can no longer misreport which layers it runs.

## 6. What changed

| file | change | effect |
|---|---|---|
| `services/privacy_layers.py` | **new** — probes layers, `describe()`, `self_check()`, `report_at_startup()` | immediate |
| `services/presidio_shadow.py` | **new** — observation harness | immediate |
| `services/sensitivity_classifier.py` | docstring corrected; `_UNTRIED` sentinel; WARNING on load failure; shadow-by-default gating | immediate |
| `server.py` | boot-time layer report + degraded banner | immediate |
| `requirements.txt` | documents the exclusion's real cost; optional Presidio block | on reinstall |
| `AgentFriday.spec` | corrected misleading comment; pinned privacy modules to `hiddenimports` | **rebuild only** |
| `tests/unit/test_privacy_layers.py` | **new** — 20 tests | immediate |
| `tests/unit/test_sensitivity_classifier_layers.py` | enforcement tests now set the flag; added a guard that shadow mode changes nothing | immediate |

### Why the spec comment mattered

It read: excludes the ML stack, "the app degrades gracefully without them
(semantic context pruning + Headroom compression fall back to no-ops)". That list
was *incomplete in the way that caused the bug* — whoever wrote the exclusion
believed `sentence_transformers` only powered performance features. It also backs
a **privacy** control. The exclusion was a reasonable decision made against an
incomplete picture, which is why the corrected comment now names Layer 3
explicitly and tells the next person to re-run the self-check.

## 7. Shadow-mode design notes

Three properties, each deliberate:

1. **Changes nothing.** `observe()` returns `None` and has no return value, so no
   caller can start depending on Presidio's opinion by accident.
2. **Costs nothing in the hot path.** Analysis runs on a background daemon thread
   behind a bounded (256) queue. Each egress decision pays one `put_nowait`.
   Back-pressure drops samples and counts them; it never blocks a cloud call.
3. **Its own log is not a leak.** The obvious implementation writes
   `would have redacted: <the sensitive text>` and creates a plaintext file of
   exactly what the gate exists to protect. This one records entity type, score,
   offsets, and a per-install salted 12-char hash. Verified: searching the log for
   words from the analysed prompts returns nothing.

Read the observations with:

```
python -m agent_friday.services.presidio_shadow
```

## 8. Pre-existing failures, NOT from this work

`tests/test_egress_adversarial.py::test_tier2_keyword_batch` fails on two
phrases — `"family gathering this weekend"` and `"contact information on file"`
(expected TIER_2, got TIER_1). Confirmed pre-existing by running HEAD's copy of
the classifier in isolation. This is fallout from `b69acb2` (the TIER-2
strong/weak split) and belongs to whoever owns that change. Left alone
deliberately: `egress_gate.py` and the news-gating fix are another session's
surface this session.
