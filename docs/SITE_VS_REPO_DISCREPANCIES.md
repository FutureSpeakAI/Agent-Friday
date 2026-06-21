# Website vs. Repo — Discrepancy Audit

> **Date:** 2026-06-06
> **Author:** generated audit (Claude) comparing the live **FutureSpeak.AI** website against the
> documentation and source in this repository.
> **Scope:** marketing/capability claims only — surfaced for reconciliation, not a security review.

---

## TL;DR

There are effectively **two different products sharing one brand**, and the public website describes the
*other* one:

| | **This repo (`friday-desktop`)** | **The website's product** |
|---|---|---|
| Artifact | A local **Flask web app** with a holographic Three.js desktop UI | The **`asimovs-mind` Claude Code plugin** (`mcp/friday-core`, a Node/MCP server) |
| Install | `git clone` + `python server.py` → `localhost:3000` | `claude plugin add https://github.com/FutureSpeakAI/asimovs-mind` |
| Interface | Browser UI, 18 workspaces, process orbs | Slash commands (`/friday unlock`, `/onboard`, `/unleash`, `/breed`…) |
| Vault encryption | **Plaintext at rest** (crypto module exists but is **not wired** — see below) | **Real AES-256-GCM at rest** (code-verified) |
| Version | docs say v4.4 | plugin is v2.3.0 |

The website is **accurate about its own product** (the plugin). The friction is that (1) this repo's docs
describe a *different* product than the site sells, and (2) **this repo's own docs over-claim vault
encryption that the Flask app does not actually perform.**

---

## Verified finding: vault encryption

The single most concrete claim was checked against source in both codebases.

### This repo (`friday-desktop`, the Flask app) — gap **FIXED** (was FALSE / aspirational)

- `vault_crypto.py` implements correct **AES-256-GCM + Argon2id (256 MiB / 4 passes) + HMAC-SHA256**, with tests.
- **Originally `server.py` never imported it** — vault data (`finance`, `health`, `vault/legal`…)
  sat in **plaintext at rest**, contradicting the "encrypted at rest" claims in `VOICE_DEMO.md` /
  `docs/ARCHITECTURE.md`.
- **Now wired (2026-06-06):** `server.py` derives a key from `FRIDAY_PASSWORD` via Argon2id at startup
  (`_get_vault_key`), transparently encrypts sensitive files on write and decrypts on read
  (`_vault_write_text` / `_vault_read_text`), and encrypts any existing plaintext in place on first boot
  (`_migrate_vault_plaintext`). Verified end-to-end: with a passphrase the on-disk file carries the
  `FRIDAYVAULT` AES-256-GCM header with no plaintext leak and still decodes through the API; tests in
  `tests/test_vault_at_rest.py` (5/5) and `tests/test_vault_crypto.py` (14/14) pass.
- **Caveat (now accurate):** encryption activates only when a non-empty **`FRIDAY_PASSWORD`** is set. With
  no passphrase the app falls back to plaintext (logged at startup) — so the claim is true *for a
  passphrase-protected install*, not unconditionally.

### The plugin (`asimovs-mind/mcp/friday-core`, the website's product) — claim is **TRUE**

- `core/crypto.js` implements AES-256-GCM, **Argon2id (opslimit=4, memlimit=256 MB)**, **BLAKE2b-KDF**
  sub-keys (`AF_VAULT` / `AF_HMAC_` / `AF_IDENT`), HMAC-SHA256, Ed25519, X25519 ECDH P2P channels — via
  libsodium. Passphrase policy ≥ 8 words.
- `core/vault.js` **actually wires it**: `deriveAllKeys()` → `#vaultKey`, with `encrypt()` on write and
  `decrypt()` on read of state files.
- The website's specific phrasing — *"Sovereign Vault v2: AES-256-GCM with passphrase-only root of trust…
  Argon2id (256MB memory-hard) and BLAKE2b KDF… no master key on disk"* — **matches this implementation.**

**Net:** the website's vault claim is real *for the plugin*. The encryption gap is in **this Flask repo**,
whose docs promise crypto the running server doesn't apply.

---

## Product framing & delivery — the biggest divergence

The repo's entire UI story — holographic Three.js desktop, 13 evolution scenes, process orbs, Liquid UI
workspaces, audio reactivity, 18 workspaces — **does not appear on the website.** The site sells a Claude
Code plugin / "DevOps hivemind" governed by the same cLaws/vault/trust vocabulary but with a completely
different delivery model and feature surface (slash commands, swarms, custom-model spawning).

---

## Numeric claims

| Claim | This repo | Website | Plugin source of truth (`asimovs-mind/CLAUDE.md`) |
|---|---|---|---|
| Subsystems | 11 (Python) | "17 subsystems" | **19** (v2.3.0; header says 19, index comment says 18) |
| Tools | 30 | "92 MCP tools" | **97** |
| API endpoints | 178 | — | — |
| UI workspaces | 18 | — | (plugin has no holographic UI) |
| Holographic scenes | 13 | — | — |
| Relationship profiles | unbounded | "Up to 200" | — |

The website's `17 / 92` appears to be a **slightly stale snapshot** of the plugin's now-`19 / 97`. None of
the repo's headline numbers (178 endpoints, 18 workspaces, 13 scenes, 30 tools) describe the website's product.

---

## Subsystems — overlap and gaps

- **In both:** Trust Graph, Cognitive Memory ("three-tier"), Integrity Engine, Personality Evolution,
  Privacy Shield, cLaws, Federation.
- **Website-only (plugin subsystems, not in this repo's docs):** Predictive Engine, Meeting Intelligence,
  Context Graph, Commitment Tracker, **Pageindex RAG** ("98.7% on FinanceBench"), Self-Improvement Kit.
- **This-repo-only (absent from website):** the holographic UI, Liquid UI app generation, `job_scanner`,
  `application_engine`, daily creation, vibe-code swarm, Seeds & Gardens.

---

## Other notable claims

| Topic | Finding |
|---|---|
| **Epistemic score** | Site calls it "Epistemic Independence Score (**EIS**)"; this repo calls it "Epistemic Score." The plugin has a real `core/eis.js`. Site adds hard stats — **"crash rate 56%→22%," "3× slower degradation," "98.7% FinanceBench"** — that appear in **neither** repo's source/docs and need a citation. |
| **cLaw spec** | Site publishes "**cLaw Specification v1.0.0, CC BY 4.0**" as an open standard; this repo doesn't mention a versioned spec. |
| **Models** | This repo: Claude (Opus 4.8 / Sonnet 4.6 / Haiku 4.5), Gemini, Ollama (Gemma, Qwen3), OpenAI/OpenRouter. Site: no model names; emphasizes "100% local via Ollama," "custom model spawning," `/breed`. |
| **Voice mode** | Heavily documented in this repo (Gemini Live, 6 moods, affective dialog, claimed Whisper local path). **Absent from the website.** |
| **Business positioning** | The site frames FutureSpeak.AI as an enterprise-focused offering, while this repo frames the product as **"sovereign AI for everyone… not just enterprises," MIT, free.** Opposite go-to-market. |
| **Founder bio** | The site and this repo present slightly different versions of the founder bio; the specifics are out of scope for this technical audit. |

---

## Internal inconsistencies *within this repo* (independent of the website)

These should be fixed regardless of the site comparison:

| Topic | Conflict |
|---|---|
| Server port | `INSTALL.md` says **5000**; everything else says **3000** |
| Voice model | `gemini-2.5-flash-preview-native-audio-dialog` (README) vs `gemini-3.1-flash-live-preview` (ARCHITECTURE/docs) |
| Orchestrator default | `claude-opus-4-8` (ARCHITECTURE) vs `claude-sonnet-4-6` (docs/CONFIGURATION) |
| Vault "encrypted at rest" | Claimed in `VOICE_DEMO.md` / `docs/ARCHITECTURE.md`; **not true** of the running Flask app (see verified finding) |
| Trust-graph dimensions | **4** (README/SELF) vs **6** (VOICE_DEMO) |
| Tool/endpoint count | "30 tools" vs "80+ routes" vs "178 endpoints" |
| Install repo name | `asimovs-mind` (INSTALL.md one-liners) vs `Agent-Friday` / `friday-desktop` (README) |
| Scene count | "6 structures" (v4.0) vs "13" (everywhere else) |

---

## Recommendations (priority order)

1. **Fix the encryption claim in this repo.** ✅ **Done (2026-06-06)** — `vault_crypto.py` is now wired into
   `server.py` (encrypt-on-write / decrypt-on-read, keyed by `FRIDAY_PASSWORD`, with in-place migration of
   existing plaintext). The "encrypted at rest" claim is now true for a passphrase-protected install. Docs
   that state it unconditionally should add the passphrase caveat.
2. **Disambiguate the two products publicly.** Decide whether "Agent Friday" = the Flask desktop app or the
   `asimovs-mind` plugin, and make the website + repo READMEs say the same thing. Right now they describe
   different artifacts under one name.
3. **Source or retract the site's performance stats** (56%→22% crash rate, 3× degradation, 98.7% FinanceBench).
   None are backed by code or docs in either repo as audited.
4. **Refresh the site's counts** to the plugin's current `19 subsystems / 97 tools` (from `17 / 92`).
5. **Reconcile go-to-market.** "Sovereign AI for everyone, MIT, free" (repo) vs. enterprise agency + paid
   certification (site) are opposite stories; pick the canonical one per surface.
6. **Sweep the intra-repo inconsistencies** above (port, model strings, dimension counts, repo names).

---

## Methodology & caveats

- Repo side: read every tracked `.md` in root + `docs/` + skill definitions, and verified the vault claim
  directly in `vault_crypto.py`, `vault_encrypt_migrate.py`, `server.py`, and the plugin's
  `asimovs-mind/mcp/friday-core/core/{crypto,vault}.js`.
- Website side: `WebFetch` over `futurespeak.ai` pages. WebFetch returns a small-model summary of each page,
  not raw HTML, and `agents.futurespeak.ai` refused the connection — so individual website numbers should be
  confirmed in a browser before being treated as authoritative.
- Plugin counts/version are quoted from `asimovs-mind/CLAUDE.md` (v2.3.0), which is itself slightly
  inconsistent (header "19 subsystems" vs architecture note "18 subsystems").

---

### Sources

- [futurespeak.ai](https://futurespeak.ai) · [/services](https://futurespeak.ai/services) · [/contact](https://futurespeak.ai/contact)
- This repository: `README.md`, `SELF.md`, `VOICE_DEMO.md`, `ARCHITECTURE.md`, `docs/*`, `vault_crypto.py`,
  `vault_encrypt_migrate.py`, `server.py`
- Plugin: `asimovs-mind/CLAUDE.md`, `asimovs-mind/mcp/friday-core/core/crypto.js`, `…/core/vault.js`
