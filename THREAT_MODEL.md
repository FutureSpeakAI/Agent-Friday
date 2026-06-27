# Agent Friday — Threat Model

This document describes what Agent Friday defends against, what it explicitly
does not defend against, and the guarantees provided by each security mechanism.

---

## What We Defend Against

### 1. Cloud-side exposure of sensitive data

**Threat:** A cloud AI provider (Anthropic, OpenAI, OpenRouter) receives sensitive
personal data — financial records, medical information, legal documents, SSNs,
family details — as part of a prompt or conversation history.

**Defence:** The **Egress Gate** (`services/egress_gate.py`) runs immediately
before every outbound cloud HTTP call, after payload assembly. It classifies all
content using a four-layer locally-running classifier:

  1. Regex — structured PII tokens (SSN, CC numbers, API keys, routing numbers)
  2. Presidio NER — contextual entity detection (names, dates, medical terms)
  3. Embedding similarity — semantic proximity to curated sensitive exemplars
  4. Local LLM — optional Ollama pass for ambiguous spans

Default on uncertainty: **REDACT** (fail-closed). Anything the gate cannot
confidently classify as PUBLIC is withheld from cloud providers. The gate never
sends content to cloud to determine if it is sensitive — all classification runs
locally.

**Guarantee:** No content above TIER_1 (PUBLIC) leaves your device to cloud
providers via the normal call path. The model router is an optimization; the
egress gate is the enforcement boundary and cannot be bypassed without modifying
`services/egress_gate.py`.

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
- The `seal_outbound()` call is present in `_call_claude()` and `_call_openai._send()`
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
| Anthropic / Gemini API keys | `~/.friday/settings.json` (encrypted via credential_store) | Cloud model access |

API keys are encrypted at rest in settings.json. The governance key and Ed25519
private key are stored in the OS credential store when available; both are
confined to `~/.friday/vault/` with 600 permissions as a fallback.

---

*Last updated: 2026-06-27. This document should be updated whenever the security
architecture changes. The egress gate guarantee is a functional invariant — any
PR that weakens it requires explicit security review.*
