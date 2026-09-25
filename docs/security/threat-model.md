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

**What "fail-closed" covers, precisely.** A failure of the gate itself (an
exception, a scrub that cannot run, a startup self-test that failed) blocks the
send. Content is different: the classifier's default for text that matches no
rule is PUBLIC (`sensitivity_classifier.classify(default=Tier.PUBLIC)`, called
that way by the gate). Only Layer 3, when it is installed, errs toward PRIVATE
on uncertain similarity. So content the rules do not recognise is sent. The gate
never sends content to the cloud to decide whether it is sensitive — all
classification runs locally.

**Guarantee:** No content the classifier places above TIER_1 (PUBLIC) leaves
your device to cloud providers via the normal call path — **except content you
have explicitly granted**, a deliberate exception described in
[File grants](../user-guide/file-grants.md). The model router is an
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
`~/.friday/vault/.governance-key`. The `IntegrityEngine` (`governance/
proof_of_integrity.py`) can verify those HMAC and Ed25519 signatures against a
signed manifest, but it is reachable only on demand — via `GET /api/integrity`,
`POST /api/integrity/verify`, and the federation/provenance modules — not from
any pre-action gate. The check that actually runs before every tool call is a
separate mechanism, `_governance_check()` (`services/agent.py`): a ring-based
allow/deny gate (Ring 0/1 always, Ring 2 requires auth, Ring 3 requires
computer-control confirmation) that HMAC-signs its own audit-log entry but does
not call `IntegrityEngine` or re-verify the signed constraint manifest. Drift in
the full manifest is detectable when the integrity-verification API is invoked.

A narrower check does run before every outward action: the per-action
checkpoint (`governance/action_gate.authorize`, see §4) computes the HMAC of the
cLaws text under the governance key and compares it with the pin in
`~/.friday/governance/claws.pin.json`. If they differ, outward actions are held
and reads continue. Its decisions, and the ring check's, are receipted in the
one signed file `~/.friday/decision-bom.jsonl`; if an entry cannot be signed
and written, a ring-2+ call is held rather than logged unsigned. Only the owner
re-pins, from Settings → Privacy & Approvals (`/api/governance/claws/repin`,
which requires a request from this machine carrying the page's token and an
explicit confirmation, and writes a signed receipt).

**Keyring fallback:** On systems without a supported keyring backend (e.g. a
headless Linux server without Secret Service), `get_governance_key()` falls back
to the file at `~/.friday/vault/.governance-key` with 0o600 permissions. This
fallback is now **logged as a WARNING** so operators are aware. File-based storage
is weaker than OS keychain — the file is protected only by filesystem permissions.
Set up a keyring backend (`python-secretstorage` + D-Bus on Linux) to eliminate
this risk.

**Guarantee:** A change to the cLaws text stops outward actions at the next
action. Drift in the rest of the signed manifest is detectable via the
on-demand attestation API, not automatically before every action. The HMAC key
lives in the OS keychain and is not stored in the repository, and it is never
replaced automatically: an unreadable key is an error, not a reason to mint a
new one. The ring-based `_governance_check()` gate runs before every tool call
and enforces allow/deny policy — it is not the same thing as manifest
verification.

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

### 4. Actions the owner did not approve

**Threat:** The agent sends, publishes, deletes, installs or changes something
because a model decided to, without the owner having agreed.

**Defence:** One per-action checkpoint, `governance/action_gate.authorize`,
called from the first and critical hook of `_execute_tool`
(`services/agent.py`), which is the only way a tool handler runs. Every call is
classified:

- **internal** — reading, drafting, and writing inside Friday's own output
  folders — runs;
- **outward** — anything that leaves the machine or changes something the owner
  owns, and any tool the classifier does not know — waits for a decision;
- **forbidden** — a shell command that names Friday's own local API — is
  refused.

An outward action is approved by a chat yes/no in a live conversation (bound to
that exact action and arguments), by an approval card otherwise, or, for a
scheduled job, by a grant the owner created that names the job, the tools, an
expiry and a use count (`~/.friday/governance/grants.json`, created only through
an owner session). Each decision is written as an HMAC-signed receipt to
`~/.friday/decision-bom.jsonl`.

Fail-closed: if classification, the cLaws integrity check, a configured
second-opinion model or the receipt write fails, outward actions are held and
reads continue.

**Guarantee:** No outward action runs without a recorded decision.
`tests/unit/test_every_action_is_governed.py` discovers every registered tool
(including connector tools) and every direct handler call site in `src/`, and
fails if any can run without passing the checkpoint first.

---

### 5. Prompt injection through content Friday reads

**Threat:** An email, web page, document or tool result contains instructions,
or supplies a recipient, link or command, that steers Friday into an action
the owner did not ask for.

**Defence:** Provenance tracking (`services/taint.py`; decision record
[2026-09-24-injection-provenance-gate](../decisions/2026-09-24-injection-provenance-gate.md)).
Every user message is recorded as trusted; every tool result is recorded as
read content with a label naming its source. Before a tool runs, each sensitive
argument (recipient, link, account number, file path, command, memory text) is
matched against what was read. A value that came only from read content sends
the action to an approval card that says where it came from, whatever its
class, and a chat "yes" does not satisfy it. A link found only in read content
that points at this machine is refused. A memory write asks when its text came
from read content, or when read content was taken in during the same
conversation in the previous 30 minutes. The action permission policy is the
last part of every system prompt, and override attempts in assembled context
are stripped before it.

**Limits, stated in the decision record:** a value that was paraphrased or
re-encoded is not tracked; a model's summary of read content is not tracked;
and the provenance ledger is held in memory, so it does not survive a restart.
The checkpoint in §4 still applies to every outward action in those cases.

---

### 6. Remote access through tunnels and proxies

**Threat:** Friday trusts requests from this PC as the owner. A tunnel (for
example `cloudflared`) or a reverse proxy makes remote requests arrive from
loopback, which would hand the owner's session to anyone who finds the URL.

**Defence:** `_is_local_request()` (`core/__init__.py`) requires the immediate
peer to be loopback **and** the request not to look proxied. Any `CF-*` header,
a forwarding hop with no address to check, a forwarded host that is not a local
alias, or any non-loopback address in `X-Forwarded-For`, `X-Real-IP`,
`True-Client-IP`, `X-Cluster-Client-IP` or `Forwarded` marks the request remote,
and a remote request must log in. Friday's own local-address proxy
(`https://agent.<name>`) binds to loopback and forwards a loopback chain, so it
remains local. The phone ingress is a separate listener on `127.0.0.1:3011`
with no Friday routes at all.

**Limit:** a tunnel that forwards no headers still looks like loopback. Do not
publish Friday's own port through a tunnel; if you must, set
`FRIDAY_TRUST_LOOPBACK=0` so every request needs a login.

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

The same holds for **a compromised Windows account or malware running with the
owner's privileges**: it can read `~/.friday`, including
`~/.friday/security/keystore.json`, which by default holds the credential root
key unwrapped behind an owner-only file ACL. Friday is not a sandbox against the
account it runs under.

### 2. Physical access attacks

An attacker with physical access to the machine can read the credential store,
bypass disk encryption, and extract all keys. This is a hardware-level threat
that application software cannot mitigate.

### 3. Compromised cloud providers

If Anthropic, OpenAI, or another cloud provider is compromised, content that
was legitimately sent to them (TIER_1 PUBLIC content) may be exposed. The egress
gate minimizes what cloud providers receive, but cannot protect content that was
intentionally shared with them.

### 4. Extraction of the shipped Google OAuth client

**Friday ships a Google OAuth client ID and secret in this public repository,
on purpose.** If you found it and are about to report it as a leaked
credential: thank you, and it is deliberate. Here is the reasoning, so you can
decide whether you disagree rather than having to guess what we intended.

`src/agent_friday/services/google_oauth_client.py` holds
`BUNDLED_CLIENT_ID` / `BUNDLED_CLIENT_SECRET`. They are also allowlisted **by
name, with this reasoning attached**, in `.githooks/security_scan.py`
(`DELIBERATE_PUBLIC_CREDENTIALS`) rather than silenced with a bare pragma.

**Why it is not a secret.** Friday is an installed desktop application.
[RFC 8252](https://datatracker.ietf.org/doc/html/rfc8252) classifies native
apps as *public clients* that cannot keep secrets, and
[Google's own native-app guidance](https://developers.google.com/identity/protocols/oauth2/native-app)
is written on that assumption. The value ships inside every copy of Friday and
can be read out of any installation. There is no configuration in which it
could be confidential.

**What it does not grant.** It identifies the *application*, not a user. It
reads no mailbox and unlocks no account. Every actual grant still requires that
person's interactive Google sign-in, on Google's own domain, and the resulting
refresh token is encrypted on their machine and never transits any server
the project operates.

**What someone can actually do with it.** Two things, both recoverable and
neither confidential:

1. **Impersonate Friday's consent screen.** Build an app that asks for Google
   permissions under the name "Agent Friday". This is a phishing/reputation
   risk, not a data-disclosure one — the victim still has to sign in and
   approve, and the tokens go to the attacker's app, not to ours.
2. **Burn the project's user cap.** Google limits an unverified project to 100
   new users over its lifetime, and that counter cannot be reset. Someone could
   exhaust it deliberately.

**Mitigation.** Both are fixed by rotating the client, which is a one-line
change and a release. And (2) is why *bring your own Google sign-in* is a
first-class path rather than an advanced option: if the shared client is
exhausted — through ordinary growth or through abuse — every user can still
connect with a client of their own, and Friday walks them through creating one.

**What would change this.** If Friday ever gains a server component that holds
tokens on users' behalf, the client stops being a public client and this entry
stops being true. Nothing here applies to a web deployment.

### 5. Zero-day exploits in dependencies

A supply-chain attack on Flask, Anthropic SDK, sentence-transformers, or another
dependency could bypass all application-level controls. We mitigate this with
enforced minimum dependency versions (`pyproject.toml` and every packaging/
requirements file use `>=` floors, not exact pins) and optional extras
(presidio, keyring) rather than mandatory ones.

---

## Egress Gate Guarantee

> **Nothing classified as PRIVATE or SENSITIVE leaves your device to cloud
> providers via the normal call path. The gate is the enforcement boundary,
> not the router. A failure of the gate blocks the send.**

This guarantee holds as long as:
- `services/egress_gate.py` is not modified
- The shared fail-closed wrapper `_seal_or_block()` in `services/model_router.py`
  is present and called at every cloud provider call site — `_call_claude()`,
  `_call_openai`'s `_send()`, and the two direct call sites in
  `services/agent.py` — covering Anthropic and all OpenAI-compatible providers,
  including OpenRouter. The same wrapper applies the hard spending cap and a
  payload size ceiling before sealing.
  `tests/unit/test_egress_paths_outside_the_router.py` covers the outbound paths
  that do not go through the router.
- The sensitivity classifier (`services/sensitivity_classifier.py`) is not modified
  to return PUBLIC for content it should classify as PRIVATE/SENSITIVE

---

## Privacy Posture Summary

| Configuration | What leaves your device |
|--------------|------------------------|
| A local model answers | Nothing — processing on-device |
| A cloud model answers | Content the classifier places in TIER_1 (PUBLIC); private content becomes a placeholder, sensitive content is withheld |
| Unrestricted cloud (an explicit, recorded consent) | Everything in the assembled payload |

In "On this computer only" (`local_only`) mode, a chat turn with no local seat
serving is refused (`routing/model_router._route_basic`) with an offer to answer
it in the cloud, which the owner must accept; voice has its own pipeline. The
first-run screen's text still describes a fallback to cloud and is out of date.
There is no switch that turns the egress gate off; unrestricted cloud is the
only bypass, and it requires the recorded consent in `privacy/cloud_consent.py`.
The privacy posture is visible in the setup wizard and in Settings → Privacy & Approvals.

---

## Key Storage

| Key | Location | Purpose |
|-----|----------|---------|
| HMAC governance key | OS keychain (keyring) → `~/.friday/vault/.governance-key` (fallback) | Signs cLaws and behavioral constraints |
| Ed25519 attestation keypair | `~/.friday/vault/.attestation-key-ed25519` | Federation and peer attestation |
| Credential root key | `~/.friday/security/keystore.json` (owner-only; unwrapped by default, optionally Argon2id-wrapped) | Encrypts every credential at rest |
| Provider API keys (Anthropic / Gemini / OpenRouter / OpenAI-compatible) | `~/.friday/providers/keys/<provider>.key` (AES-256-GCM under the keystore root key; blobs from older versions written with the vault key or Windows DPAPI remain readable and are migrated) | Cloud model access |

API keys are encrypted at rest in per-provider files under
`~/.friday/providers/keys/` and decrypted into the process environment at
startup by `bootstrap_provider_env()`. The governance key is stored in the OS
credential store when available, with `~/.friday/vault/.governance-key` (600
permissions) as the fallback. The Ed25519 private key is a file only,
`~/.friday/vault/.attestation-key-ed25519`, with 600 permissions.

---

*Last verified against the code: 2026-09-24. Update this document whenever the
security architecture changes. The egress gate guarantee is a functional
invariant; any change that weakens it requires explicit security review. How to
report a problem, and which versions receive fixes, is in the repository's
[SECURITY.md](../../SECURITY.md).*
