# Agent Friday — Threat Model

This document describes what Agent Friday defends against, what it explicitly
does not defend against, and the guarantees provided by each security mechanism.

---

## First: which Agent Friday are you running?

**There is no single artifact, and the difference is a security difference, not
a packaging detail.** Every guarantee below is conditioned on this, so it is
stated before anything else rather than in a footnote.

| | **`AgentFriday.exe`** | **`AgentFriday-Setup-*.zip`** | **From source** |
|---|---|---|---|
| Built by | PyInstaller, one frozen file | Embedded CPython + source payload + wheelhouse (`packaging/windows/`) | Your own `pip install` |
| Egress classifier | **Layers 1a + 1b only** | 1a + 1b, plus Layer 3 if the memory tier installs | Depends on your extras |
| `sentence-transformers` (Layer 3) | **Excluded deliberately** — pulls torch, over 4 GB measured against a ~152 MB binary | Installed by the *memory* tier (~2.5 GB, announced, skippable) | `.[local]` or `.[all]` |
| `presidio-analyzer` (Layer 2) | Not bundled | Installed — but **observe-only**, see §1 | `.[pii]` or `.[all]` — still observe-only |

So the honest one-line summary: **the downloadable `.exe` runs two layers of
pattern matching.** That is a deliberate trade — not shipping a 4+ GB tensor
library inside a desktop download — and not a defect. What *would* be a defect
is letting you believe otherwise, so:

**Do not take this table's word for it.** Friday probes its own layers at every
boot and prints the result, and prints a boxed `SENSITIVITY CLASSIFIER IS
RUNNING DEGRADED` notice whenever anything declared is not running. That output
is generated from the live process, not from this document. If the two ever
disagree, the boot line is right and this file is stale — please open an issue.

---

## What We Defend Against

### 1. Cloud-side exposure of sensitive data

**Threat:** A cloud AI provider (Anthropic, OpenAI, OpenRouter) receives sensitive
personal data — financial records, medical information, legal documents, SSNs,
family details — as part of a prompt or conversation history.

**Defence:** The **Egress Gate** (`services/egress_gate.py`) runs immediately
before every outbound cloud HTTP call, after payload assembly. It classifies all
content using a locally-running classifier that *declares* four layers. **How
many are actually in force depends on how you installed Friday, and it is
usually two.** This section used to say "four-layer" flatly. That was wrong in
every environment that has ever existed, and for a product that sells data
sovereignty it was the worst possible thing to be wrong about. What follows is
what actually runs.

| Layer | What it does | Actually in force? |
|---|---|---|
| **1a — Regex** | Structured tokens: SSN, card numbers, API keys, routing numbers, phone/address/account-tail shapes | **Always.** No dependency. |
| **1b — Keyword** | Strong phrases plus context-gated keyword tiers | **Always.** No dependency. |
| **2 — Presidio NER** | Names, dates, medical/financial entities | **No, by default** — see below. |
| **3 — Embedding** | MiniLM semantic similarity to curated sensitive exemplars | **Only if `sentence-transformers` is present.** Installed by the Windows installer's *memory* tier; **excluded from the PyInstaller `.exe` on purpose.** |
| **4 — Local LLM** | Ollama adjudication of ambiguous spans | **No** — opt-in per call (`use_llm=True`), off by default. |

So the honest summary: **the frozen `.exe` runs Layers 1a+1b only** — four
regexes and two keyword lists. A full Windows installer run additionally gets
Layer 3.

**Presidio was evaluated and deliberately rejected.** It is not a missing
feature or an unfinished one. Measured on 2026-08-24:

- it returned **TIER_2 where the existing regex returns TIER_3** — weaker than
  what was already shipping, on real PII;
- it **escalated 6 of 12 entirely benign prompts**, including *"What is the
  weather going to be like tomorrow?"* and *"Remind me to buy milk on Friday"*,
  because its `DATE_TIME` and `LOCATION` recognisers fire on ordinary prose.

Enforcing that would withhold roughly half of normal conversation from the
cloud while making PII detection *worse*. The Windows installer still installs
`presidio-analyzer` so the evidence can keep accumulating, but it runs in
**shadow mode**: it logs what it *would* have flagged (entity type, offsets,
score, and a salted hash — never the matched text) and changes no decision.
Enforcement requires setting `FRIDAY_PRESIDIO_ENFORCE=1` explicitly, and we
do not recommend it. Because of this, `privacy_layers` reports Layer 2 as
**inactive even when it imports** — a layer that cannot change an outcome is
not a protection.

**Verify it yourself rather than trusting this table.** Friday probes its own
layers at every boot and prints the result; a shortfall prints a boxed
`SENSITIVITY CLASSIFIER IS RUNNING DEGRADED` notice. Or ask directly:

```
python -c "from agent_friday.services.privacy_layers import describe; print(describe())"
# Sensitivity classifier: 3/4 layers active (source checkout). DEGRADED - not running: presidio.
```

Default on uncertainty: **REDACT** (fail-closed). Anything the gate cannot
confidently classify as PUBLIC is withheld from cloud providers. The gate never
sends content to cloud to determine if it is sensitive — all classification runs
locally.

**Guarantee:** No content above TIER_1 (PUBLIC) leaves your device to cloud
providers via the normal call path — **except content you have explicitly
granted**, which is a deliberate exception added in 5.6.0 and described in
[docs/FILE_GRANTS.md](docs/FILE_GRANTS.md). The model router is an
optimization; the egress gate is the enforcement boundary and cannot be
bypassed without modifying `services/egress_gate.py`.

**Scope of that guarantee — read this if you are deciding whether to trust
Friday with a vault.** The gate is an enforcement boundary, not a proof. Three
honest limits:

1. **It is only as good as its classifier**, which on a frozen build is two
   layers of pattern matching. Novel PII shapes it has no rule for will pass.
   Between 2026-08-24 and 2026-08-25 the classifier had **no phone, address, or
   account-number regex at all**, and real contact details reached the cloud.
   That is fixed; it is also the kind of thing that can recur.
2. **A grant is a real hole, on purpose.** Granted file content is registered as
   sendable and crosses the wire. The design reasoning is in FILE_GRANTS.md.
3. **Only the user can open it.** No model on any surface can create a grant —
   there is no grant tool in `CLAUDE_TOOLS`, so a prompt-injected model cannot
   widen its own reach, and a spoken "yes" cannot create one either.

---

### 2. Unauthorized modification of behavioral constraints

**Threat:** An attacker modifies Friday's cLaws (ethical constraints), governance
ring definitions, or privilege rules to remove safety floors or escalate tool
access.

**Defence:** All behavioral constraints are HMAC-SHA256 signed with a governance
key stored in the OS credential store (Windows Credential Manager, macOS Keychain,
Linux Secret Service) via the `keyring` library, with a file fallback at
`~/.friday/vault/.governance-key`. The `IntegrityEngine` verifies HMAC and
Ed25519 signatures before every action. Drift is logged to
`~/.friday/vault/access-log.jsonl`.

**Keyring fallback:** On systems without a supported keyring backend (e.g. a
headless Linux server without Secret Service), `get_governance_key()` falls back
to the file at `~/.friday/vault/.governance-key` with 0o600 permissions. This
fallback is now **logged as a WARNING** so operators are aware. File-based storage
is weaker than OS keychain — the file is protected only by filesystem permissions.
Set up a keyring backend (`python-secretstorage` + D-Bus on Linux) to eliminate
this risk.

**Guarantee:** Constraint modifications are detectable (integrity drift) and logged.
The HMAC key lives in the OS keychain and is not stored in the repository.

---

### 3. PII leakage in transit

**Threat:** A message to a cloud provider contains phone numbers, email addresses,
physical addresses, or other PII that was assembled from memory, wiki, or context
injection.

**Defence:** Two complementary layers:

  - **Vault Access Control** (`vault_access.py`): tier-gates vault content during
    prompt assembly. Cloud providers receive only TIER_1 (PUBLIC) content in full;
    TIER_2 (PRIVATE) is replaced with a redaction placeholder; TIER_3 (SENSITIVE)
    is dropped entirely.
  - **Egress Gate** (`services/egress_gate.py`): last-line enforcement on the
    assembled payload. Catches content that slipped through assembly-time gating
    (e.g., PII injected via tool results or context files).

**Guarantee:** The egress gate is the final barrier. Even if vault access control
is bypassed (e.g., a bug in prompt assembly), the gate enforces the same policy
at the HTTP call boundary.

---

## What We Do NOT Defend Against

### 1. A compromised or hostile local machine owner

The local machine owner can:
- Re-sign governance constraints with a newly generated key (they control the keystore)
- Modify `services/egress_gate.py` or `vault_access.py` to bypass gating
- Read `~/.friday/vault/` directly (it's their filesystem)
- Intercept network traffic from the Friday process

**This is by design.** Agent Friday is a personal sovereign AI. The user is the
sovereign. We defend against *remote exposure* to third parties (cloud providers),
not against the local owner themselves. A hostile local owner is out of scope.

### 2. Physical access attacks

An attacker with physical access to the machine can read the credential store,
bypass disk encryption, and extract all keys. This is a hardware-level threat
that application software cannot mitigate.

### 3. Compromised cloud providers

If Anthropic, OpenAI, or another cloud provider is compromised, content that
was legitimately sent to them (TIER_1 PUBLIC content) may be exposed. The egress
gate minimizes what cloud providers receive, but cannot protect content that was
intentionally shared with them.

### 4. Zero-day exploits in dependencies

A supply-chain attack on Flask, Anthropic SDK, sentence-transformers, or another
dependency could bypass all application-level controls. We mitigate this with
pinned dependency versions and optional extras (presidio, keyring) rather than
mandatory ones.

---

## Egress Gate Guarantee

> **Nothing classified as PRIVATE or SENSITIVE leaves your device to cloud
> providers via the normal call path. The gate is the enforcement boundary,
> not the router. The default on uncertainty is REDACT.**

This guarantee holds as long as:
- `services/egress_gate.py` is not modified
- The shared fail-closed wrapper `_seal_or_block()` in `services/model_router.py`
  is present and called at every cloud provider call site — `_call_claude()` and
  `_call_openai`'s `_send()` — covering Anthropic and all OpenAI-compatible
  providers, including OpenRouter
- The sensitivity classifier (`services/sensitivity_classifier.py`) is not modified
  to return PUBLIC for content it should classify as PRIVATE/SENSITIVE

---

## Privacy Posture Summary

| Configuration | What leaves your device |
|--------------|------------------------|
| With Ollama (local routing) | Nothing — all processing on-device |
| Cloud-only, no Ollama | TIER_1 (PUBLIC) content only; sensitive data redacted by egress gate |
| Egress gate disabled (not recommended) | Everything in the assembled payload |

The privacy posture is visible in the setup wizard and in Settings → Privacy.

---

## Key Storage

| Key | Location | Purpose |
|-----|----------|---------|
| HMAC governance key | OS keychain (keyring) → `~/.friday/vault/.governance-key` (fallback) | Signs cLaws and behavioral constraints |
| Ed25519 attestation keypair | `~/.friday/vault/.attestation-key-ed25519` | Federation and peer attestation |
| Provider API keys (Anthropic / Gemini / OpenRouter / OpenAI-compatible) | `~/.friday/providers/keys/<provider>.key` (encrypted via credential_store: vault AES-256-GCM → Windows DPAPI → warned plaintext fallback) | Cloud model access |

API keys are encrypted at rest in per-provider files under
`~/.friday/providers/keys/` and decrypted into the process environment at
startup by `bootstrap_provider_env()`. The governance key and Ed25519
private key are stored in the OS credential store when available; both are
confined to `~/.friday/vault/` with 600 permissions as a fallback.

---

*Last updated: 2026-07-04. This document should be updated whenever the security
architecture changes. The egress gate guarantee is a functional invariant — any
PR that weakens it requires explicit security review.*
