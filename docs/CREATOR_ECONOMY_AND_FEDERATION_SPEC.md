# Creator Economy & Federation Protocol — Design Specification

**Document owner:** FutureSpeak.AI
**Status:** Draft v1.0 (design, not yet implemented)
**Scope:** Bridges roadmap **v5 (standalone pip install)** → **v6 (Federation)**
**Audience:** Friday core engineers, future federation node operators
**Last updated:** 2026-06-26

---

## 0. How to read this document

This is a **design spec**, not a description of shipped code. Where it says *"already exists"* the
component is in the tree today and quoted faithfully. Where it says *"new"* or *"to build"* it does
not exist yet. Every new capability is mapped onto a concrete, already-present seam so the work is
additive, not a rewrite.

The spec is organized in three layers that build on each other:

```
LAYER 3  ── Federation & Marketplace ────  agents find + trust each other, and a two-layer
                                            market (free commons + priced commerce, priced in
                                            Positrons ψ with a Positron/Negatron charge model)
                                            lets them shop and sell on their users' behalf
              ▲ depends on
LAYER 2  ── Ownership & Provenance ───────  every artifact is signed, owned, and verifiable
              ▲ depends on
LAYER 1  ── Production Engine ────────────  Friday makes complete multi-media works
```

You cannot federate content you cannot prove you own, and you cannot prove ownership of content
you did not cleanly produce. So we build **Layer 1 → Layer 2 → Layer 3**, in that order.

### Design principles (load-bearing, not aspirational)

1. **Sovereign by default** — keys, content, and provenance live on the user's device; the user
   wholly owns what Friday creates and sets its license per piece.
2. **Encrypted always** — no plaintext federation traffic, ever (§13): every agent-to-agent byte is
   end-to-end encrypted with forward secrecy.
3. **Green by design — the energy-efficient alternative to blockchain.** *(first-class — see below)*
4. **Trust is local and earned** — no global authority, no global consensus; each agent decides whom
   to trust from direct experience.
5. **Local-first, cloud-as-premium** — every creative capability runs on-device with **zero cloud API
   keys**; cloud (Gemini/Veo/Lyria) is an opt-in *upgrade*, never a requirement. Same tiering as voice
   already ships (§27).
6. **Maximally open, harm-bounded** — the platform does not police taste, morality, or offense. The
   one bright line is *measurable harm to real people* (CSAM, real-person deepfakes, targeted
   harassment, violence facilitation). Adult, dark, political, and controversial content are
   permitted; viewers filter their own feed (§25). The ambition is an open content platform that
   eventually replaces YouTube — more open, with a sharper, harm-based line and no central censor.
7. **Build on what ships** — extend the existing Ed25519 / vault / hooks / trust-graph spine rather
   than inventing parallel systems.

> ### ⚡ Green by design
> This federation is deliberately **the low-energy alternative to blockchain**. There is **no
> proof-of-work, no mining, no global consensus broadcast, and no chain to replicate.** A Positron
> (ψ) transaction is a **signed entry appended to a local hash-chained ledger and sent to one
> counterparty over an encrypted channel** — it costs about as much energy as **sending an email**,
> not the kilowatt-hours a blockchain spends to make thousands of machines agree on every coin move.
> Value in this economy is created by **making things** (Proof of *Creation*, §15.10), never by
> burning electricity to win a hash race. Energy cost scales with *real activity* (one transaction =
> one message), not with global network size. This is a core property of the design, carried
> through every layer — not a marketing footnote.

---

## 1. What already exists (the foundation we build on)

The single most important fact about this spec: **the cryptographic spine of federation is already
in the tree.** We are not inventing an identity or signing system — we are extending one that ships
today.

### 1.1 Creative production stack (Layer 1 foundation)

| Component | File | What it gives us |
|---|---|---|
| Image + video generation | `services/creative_engine.py` | `generate_image()`, `generate_video()` via `google-genai` SDK; friendly→API-id maps; `check_content_safety()` cLaws gate; metadata sidecars to `~/.friday/creations_meta/` |
| Layered prompt model | `services/scene_dna.py` | `SceneDNA` dataclass with `setting/characters/action/mood/`**`audio`**`/continuity/style/technical` layers — **the `audio` layer already exists**, unused by a music engine |
| Multi-stage pipeline | `services/creative_pipeline.py` | Typed-contract stages (`text`/`agent`/`image` modes), checkpoints, runs persisted to `~/.friday/pipelines/runs/` |
| Per-project memory | `services/creative_memory.py` | Series Bible: cast, locations, continuity, style guide; character looks propagate into prompts |
| Self-evaluation | `services/qa_gates.py` | `evaluate_text()`, `evaluate_image()`, `gate_text()` improve/flag loop |
| Best-of-N | `services/take_comparison.py` | `compare_images()`, `compare_text()`, generic `rank_takes()` |
| Daily creation | `services/creations.py` | One creative piece/day, format menu, scheduler-driven, materialized to gallery |

### 1.2 Identity, trust, and crypto (Layer 2 + 3 foundation)

| Component | File | What it gives us |
|---|---|---|
| **Agent identity** | `proof_of_integrity.py` | `IntegrityEngine` already generates/loads an **Ed25519 keypair** at `~/.friday/vault/.attestation-key-ed25519` (+ `.attestation-pubkey-ed25519`). `get_public_key_hex()`, `sign_payload()`, static `verify_payload()`. **`agent_id` = the Ed25519 public key hex.** |
| **Integrity manifest** | `proof_of_integrity.py` | `AgentIntegrityManifest`: cLaws hash + HMAC-SHA256, model/tool manifest, vault status, Ed25519 self-signature. `verify_manifest()` returns per-check booleans. |
| **Attestation protocol** | `source_trust_federation.py` | `sign_attestation()`, `verify_attestation()`, `import_attestation()`. Versioned (`"1.0"`), deterministic-JSON body, Ed25519 signature, stored at `~/.friday/federation/{attestations,imported}.jsonl`. **This is a working federation message format today** — scoped to source-trust observations. |
| **Human trust** | `people_graph.py` | 4-D reputation (reliability, emotional_safety, alignment, competence) |
| **Source trust** | `source_trust_graph.py` | 6-D reputation with time-decay weighted mean + seed priors |
| **Vault crypto** | `vault_crypto.py` | AES-256-GCM + Argon2id, `FRIDAYVAULT\x01` self-describing container, `sign_entry()`/`verify_entry()` HMAC |
| **Data tiers** | `vault_access.py` | `Tier.PUBLIC/PRIVATE/SENSITIVE` (1/2/3); local-only enforcement for tiers 2–3 |
| **Credential store** | `services/credential_store.py` | Vault→DPAPI→hardened-plaintext encryption-at-rest |
| **Tool lifecycle** | `services/tool_hooks.py` | `register_pre_hook`/`register_post_hook`, `PreVerdict` (ALLOW/MODIFY/DENY), `critical=True` fail-closed |
| **Scheduler** | `services/scheduler.py` | `register_builtin_task()`, `schedules.json`, Central-time tick |
| **Extension security** | `services/extension_security.py` | Trust levels, `gate_mcp_config()`, static launch assessment |
| **cLaws** | `proof_of_integrity.py` + `asimovs-mind/hooks/` | `CLAWS_TEXT`, HMAC-signed; first-law/safety-scanner PreToolUse hooks |

### 1.3 Dependencies already declared

`pyproject.toml` already carries the relevant optional groups:

```
google-genai>=1.0          # core dep — Imagen, Veo, Lyria all ride this one SDK
federation = ["pynacl>=1.5"]   # Ed25519 — present, gated behind the extra
creative   = ["pillow>=10.0"]
```

**Net:** Federation's identity layer is ~80% built. Music generation rides an SDK we already
depend on. The work ahead is overwhelmingly *additive composition*, not greenfield cryptography.

---

# LAYER 1 — PRODUCTION ENGINE

The goal: Friday graduates from generating *single assets* (one image, one clip) to producing
*complete works* (a scored 30-second short with titles, a song with a lyric video, a branded ad).

> **Provider note (read with §27).** This layer is written cloud-first (Gemini Imagen/Veo/Lyria)
> because that is what ships today — but every capability here is **provider-abstracted**: the same
> pipeline runs on **local/open models with zero cloud keys**, with cloud as the opt-in *premium*
> tier. Wherever this layer says "Gemini," read "the cloud tier of a local-default capability." The
> local-first scoping (models, hardware, tradeoffs, the no-GPU minimum stack) is **§27**.

## 2. Lyria 3 music generation

### 2.1 Design

A new module `services/music_engine.py` mirrors `creative_engine.py` exactly — same shape, same
SDK, same safety gate, same metadata sidecar pattern. This is deliberate: a reviewer who knows
`creative_engine.py` already knows `music_engine.py`.

```
services/music_engine.py
├─ _MUSIC_MODEL_MAP        friendly → real API id (settings-overridable, mirrors _IMAGE_MODEL_MAP)
│    "lyria-clip"  → "lyria-3-clip-preview"     (30s clips)
│    "lyria-pro"   → "lyria-3-pro-preview"      (full songs)
├─ resolve_music_model(requested) -> str        (reuses creative_engine._resolve_model)
├─ check_music_safety(prompt, lyrics)           (harm floor only, §25.3: blocks lyrics that harm a
│                                                real person — doxxing/targeted harassment/CSAM —
│                                                NOT offensive/dark/explicit themes, which are allowed)
├─ generate_music(...)  -> envelope             (same {status, files, model, api_model, ...} shape)
└─ _write_metadata(...)                          (→ ~/.friday/creations_meta/, kind="music")
```

### 2.2 `generate_music()` signature

```python
def generate_music(
    prompt: str, *,
    model: Optional[str] = None,           # "lyria-clip" (default) | "lyria-pro"
    mode: str = "instrumental",            # "instrumental" | "song" (with vocals)
    lyrics: Optional[str] = None,          # custom lyrics; enables vocal synthesis
    seed_image_path: Optional[str] = None, # image-to-music (mood transfer from a still)
    duration_seconds: Optional[int] = None,# clip: <=30; pro: full song
    language: str = "en",                  # multi-language vocal synthesis
    timestamps: Optional[list] = None,     # [{"t": 0.0, "cue": "verse"}, ...] section control
    negative_prompt: Optional[str] = None, # "no drums", etc.
    session_ctx: Optional[dict] = None,
    scene_dna: Optional[dict] = None,      # reads the EXISTING SceneDNA.audio layer
    project_id: Optional[str] = None,      # attaches to Series Bible asset gallery
) -> Dict[str, Any]:
    """Generate music via Lyria 3. Returns the standard creative envelope.
    Never raises. status ∈ {ok, blocked, unavailable, error}."""
```

### 2.3 SDK call (same `google-genai` client as Imagen/Veo)

```python
from google.genai import types
op = client.models.generate_music(            # long-running op, polled like Veo
    model=api_model,                           # "lyria-3-pro-preview"
    prompt=full_prompt,
    config=types.GenerateMusicConfig(
        mode=mode, lyrics=lyrics, language=language,
        duration_seconds=duration_seconds,
        section_cues=timestamps,
        negative_prompt=negative_prompt,
    ),
    image=types.Image(image_bytes=seed_bytes) if seed_bytes else None,
)
# poll operations.get() on the existing _VIDEO_POLL_SECONDS / _MAX_WAIT cadence → download → save
```

> **Feasibility flag.** The exact `google-genai` surface for Lyria 3 (`generate_music`,
> `GenerateMusicConfig`, `section_cues`) is **assumed from the Imagen/Veo pattern and must be
> verified against the installed SDK version** before coding. If the real method differs, only this
> one function body changes — the envelope, safety gate, metadata, and Scene DNA wiring are
> unaffected. See §22.2.

### 2.4 Scene DNA integration — zero new fields

`SceneDNA.audio` **already exists** (`scene_dna.py`, `LAYER_ORDER`). A pipeline `music` stage reads
`scene_dna["audio"]` as the prompt seed, so a storyboard that already specifies *"audio: tense
strings, distant thunder"* drives music generation with no schema change.

### 2.5 New pipeline stage mode: `music`

Extend `creative_pipeline._stage_executor()`:

```python
def _stage_executor(mode):
    return {"text": _exec_text_stage, "agent": _exec_agent_stage,
            "image": _exec_image_stage, "music": _exec_music_stage,   # NEW
            "video": _exec_video_stage}.get(mode, _exec_text_stage)   # video also promoted to a stage
```

`_exec_music_stage` mirrors `_exec_image_stage`: calls `music_engine.generate_music()`, stashes the
file record into `context[f"{output_key}_file"]`.

---

## 3. FFmpeg timeline composition

This is the one genuinely new heavy subsystem. Everything else composes existing parts; assembling
a timeline from clips + audio is new work.

### 3.1 Module + dependency

```
services/timeline_engine.py        # NEW
pyproject.toml:  compose = ["imageio-ffmpeg>=0.5"]   # bundles an ffmpeg binary, no system install
```

`imageio-ffmpeg` is chosen over assuming a system `ffmpeg` so a `pip install agent-friday[compose]`
"just works" on a clean Windows box — consistent with the v5 clean-install goal in `RELEASE_PLAN.md`.
Falls back to a discovered system `ffmpeg` on PATH if present.

### 3.2 Timeline contract (typed, JSON, signable)

```python
{
  "timeline_id": "tl-<uuid>",
  "project_id": "<series-bible-id>",     # optional
  "fps": 30,
  "resolution": [1920, 1080],
  "tracks": [
    {"kind": "video", "clips": [
        {"file": "friday-video-...mp4", "in": 0.0, "out": 8.0,
         "transition_in": {"type": "fade", "dur": 0.5}},
        {"file": "friday-video-...mp4", "in": 0.0, "out": 6.0,
         "transition_in": {"type": "crossfade", "dur": 0.75}}
    ]},
    {"kind": "audio", "clips": [
        {"file": "friday-music-...wav", "in": 0.0, "gain_db": -3.0,
         "fade_out": 1.5}
    ]},
    {"kind": "overlay", "clips": [
        {"text": "THE WANDERER", "t": 0.5, "dur": 3.0, "style": "title-card"}
    ]}
  ],
  "exports": ["mp4-1080p", "mp4-vertical-9x16", "gif-preview"]
}
```

### 3.3 Public API

```python
def compose(timeline: dict, *, project_id=None, session_ctx=None) -> dict:
    """Render a timeline to one or more output files via ffmpeg filter graphs.
    Returns the standard creative envelope (files list + provenance hooks).
    Surfaces a process orb (register→progress→complete) like every long op."""

def export_formats() -> list   # ["mp4-1080p", "mp4-vertical-9x16", "webm", "gif-preview", "audio-mp3"]
def validate_timeline(tl: dict) -> tuple   # (ok, errors) — reuses the pipeline schema-validator pattern
```

Implementation: build an `ffmpeg` filter-complex string from the track graph (concat + xfade +
amix + drawtext + scale), invoke once per export profile, write outputs to `CREATIONS_DIR`, write a
provenance sidecar (Layer 2) recording **every source clip's content hash** as an input edge.

### 3.4 Why a structured timeline, not raw ffmpeg strings

The timeline JSON is itself an **ownership artifact**: it records exactly which signed clips and
which music track composed the final work. That edge list is what Layer 2 signs and what Layer 3
uses to compute collaborative credit. A raw ffmpeg command line throws that lineage away.

---

## 4. Full production pipeline

A new built-in pipeline template in `creative_pipeline.py`, composing all of the above:

```
┌────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌────────┐  ┌──────────┐  ┌────────┐
│ SCRIPT │─▶│STORYBOARD│─▶│ KEYFRAMES│─▶│VIDEO CLIPS│─▶│ MUSIC  │─▶│ TIMELINE │─▶│ EXPORT │
│ text   │  │  text    │  │  image   │  │  video    │  │ music  │  │ compose  │  │ compose│
└────────┘  └────┬─────┘  └────┬─────┘  └────┬──────┘  └───┬────┘  └────┬─────┘  └────────┘
   logline       │ ckpt        │             │             │            │
                 ▼             ▼             ▼             ▼            ▼
            Scene DNA per  per-beat      image→video   reads .audio  edge list →
            beat (existing) keyframe     (seed image)  layer/scene   provenance
                            via Bible    via Bible     DNA           (Layer 2)
                            looks        looks
```

| Stage | mode | reads | writes | checkpoint? |
|---|---|---|---|---|
| Script | `text` | logline | `script`, scene list | — |
| Storyboard | `text` | `script` | per-beat `SceneDNA[]` | ✓ (human review) |
| Keyframes | `image` | Scene DNA + Bible looks | keyframe file per beat | — |
| Video clips | `video` | keyframe (image→video) + Scene DNA | clip file per beat | ✓ (cost gate) |
| Music | `music` | `SceneDNA.audio` | score track | — |
| Timeline | `timeline` | all clips + music | timeline JSON | ✓ (final review) |
| Export | `timeline` | timeline JSON | mp4/vertical/gif | — |

The two cost-heavy checkpoints (before video gen, before final export) are deliberate — video and
full-song generation are the expensive calls; the human confirms before spend. This reuses the
**existing** checkpoint/resume/intervene machinery in `creative_pipeline.py` with **no new control
flow**.

QA gates (`qa_gates.py`) run per visual stage; take comparison (`take_comparison.py`) can fan a
keyframe stage to N candidates and auto-pick. Both already exist.

---

## 5. Multi-brand content support

### 5.1 Brand kits

```
services/brand_kits.py                    # NEW (mirrors creative_memory.py's storage style)
~/.friday/brand_kits/<brand_id>.json
```

```python
{
  "brand_id": "futurespeak",
  "name": "FutureSpeak.AI",
  "colors": {"primary": "#0A1F3C", "accent": "#3CE8C2", "bg": "#05060A"},
  "fonts": {"display": "Space Grotesk", "body": "Inter"},
  "tone": "confident, sovereign, warm-but-precise",
  "logo": "~/.friday/brand_kits/assets/futurespeak-logo.png",
  "negative": "no stock-photo gloss, no corporate buzzwords",
  "voice_profile": "calm, grounded",          # for any narration/TTS
  "created": "...", "updated": "..."
}
```

### 5.2 Propagation — one injection point

Brand kits propagate exactly the way Series Bible looks already do. `context_injection.py` already
folds active-project context into the system prompt; we add a parallel `_brand_block()`:

- **Text/script stages** — tone + negatives injected into the system prompt via `context_injection`.
- **Image/video stages** — palette + style + "no logo distortion" appended to the composed prompt
  in `creative_engine` (same spot Series Bible looks are expanded).
- **Timeline stage** — logo overlay + brand fonts applied to title cards in `timeline_engine`.

An `active_brand_id` pointer (like `creative_memory`'s `active.json`) lets every generation in a
session inherit a brand with zero per-call args. Brand kit ⟂ Series Bible: a project can swap brands;
a brand can span projects.

---

## 6. Daily creation — free creative choice across all media

### 6.1 What changes

Today `services/creations.py` rotates a **text** format menu by day-of-year (poem, essay,
aphorism…). The new design widens the menu to **all media types and full productions** and replaces
deterministic rotation with **Friday's own choice**.

```
Current:  format = MENU[day_of_year % len(MENU)]        # forced rotation, text only
New:      format = Friday chooses freely, weighted by:   # genuine creative agency
            - what she made recently (avoid repetition, not by a hard rotation)
            - ambient signals (services/ambient_awareness.py state, season, news mood)
            - active brand/project if one is set
            - cost ceiling for the day (don't burn the budget on a daily)
```

### 6.2 Expanded menu

```python
DAILY_MODES = [
    # existing text modes
    "poem", "micro-essay", "short-story", "letter", "writing-prompt", "aphorisms",
    # existing-but-now-eligible
    "algorithmic-art",       # code art (HTML/JS canvas) — Friday writes & renders
    # NEW media modes (cost-gated)
    "image",                 # single generated image (creative_engine)
    "music-clip",            # 30s Lyria clip (music_engine)
    "short-production",      # full pipeline: 8–15s scored micro-film (rare, budget permitting)
]
```

### 6.3 Choice mechanism

A new `_choose_daily_mode()` asks Friday (via `_generate_text`, cheap) to pick a mode and a concept
in one shot, given: recent creations list, ambient state, active brand/project, and a **remaining
daily-creation budget** read from the existing `cost_meter` (`costs.db`). Expensive modes
(`music-clip`, `short-production`) are filtered out of the candidate set when the budget is low — so
"free choice" never means "unbounded spend." The hard cost ceiling is a setting
(`daily_creation_budget_usd`, default e.g. `$0.50`).

This is the **only** place autonomy and cost intersect on Layer 1, and it's gated by an existing
meter — see the autonomy table in §15.

---

# LAYER 2 — OWNERSHIP & PROVENANCE

Principle, stated plainly and bindingly: **the user wholly owns everything Friday creates for them,
and sets its license.** Friday's job is to make that ownership *cryptographically provable to anyone,
forever, without trusting Friday or FutureSpeak.AI.*

## 7. Signed provenance for every artifact

### 7.1 The C2PA-aligned manifest

We adopt the **C2PA "Content Credentials"** data model (the emerging open standard for content
provenance, used by Adobe/Leica/cameras) rather than inventing one, but we sign it with **the agent
keypair that already exists** (`proof_of_integrity.py`). The serialized form is **JSON-LD** so it is
self-describing linked data and embeds cleanly into file metadata (§8).

```jsonc
{
  "@context": "https://futurespeak.ai/provenance/v1",
  "type": "ContentCredential",
  "version": "1.0",

  // ── WHO ──────────────────────────────────────────────
  "creator": {
    "user_id": "<stable local user id>",        // the human owner — wholly owns this
    "agent_id": "<ed25519 pubkey hex>"          // == IntegrityEngine.get_public_key_hex()
  },

  // ── WHEN ─────────────────────────────────────────────
  "created": "2026-06-26T18:30:00Z",            // wall clock (advisory)
  "timestamp_proof": {                           // cryptographic time (see §7.3)
    "type": "rfc3161",
    "tsa": "https://freetsa.org/tsr",
    "token": "<base64 timestamp token over content_hash>"
  },

  // ── WHAT (the artifact) ──────────────────────────────
  "artifact": {
    "filename": "friday-production-....mp4",
    "mime": "video/mp4",
    "content_hash": "sha256:9f86d0818...",       // hash of the actual bytes
    "bytes": 8421120
  },

  // ── HOW (tool chain) ─────────────────────────────────
  "tool_chain": [
    {"tool": "music_engine.generate_music", "model": "lyria-pro",
     "api_model": "lyria-3-pro-preview", "prompt_hash": "sha256:..."},
    {"tool": "creative_engine.generate_video", "model": "veo",
     "api_model": "veo-3.0-generate-preview", "prompt_hash": "sha256:..."},
    {"tool": "timeline_engine.compose", "version": "1.0"}
  ],
  "friday_version": "5.0.0",

  // ── EDIT HISTORY (append-only) ───────────────────────
  "edits": [
    {"ts": "...", "op": "trim", "by_agent": "<agent_id>", "detail": "8.0s→6.5s"},
    {"ts": "...", "op": "relabel", "by_agent": "<agent_id>", "detail": "title changed"}
  ],

  // ── SOURCES (provenance edges → other manifests) ─────
  "sources": [
    {"content_hash": "sha256:<keyframe>",  "role": "keyframe",   "manifest": "sha256:<its manifest>"},
    {"content_hash": "sha256:<clip-1>",    "role": "clip",       "manifest": "sha256:<its manifest>"},
    {"content_hash": "sha256:<music>",     "role": "score",      "manifest": "sha256:<its manifest>"}
  ],

  // ── LICENSE (creator-defined per-piece at creation; NO account default) ──
  "license": {
    "terms": "CC-BY-4.0",                        // chosen at creation: all-rights-reserved |
                                                 //   CC-BY-4.0 | CC-BY-SA-4.0 | CC0/public-domain |
                                                 //   priced | custom URI
    "attribution": "FutureSpeak.AI / Stephen Webster",
    "commercial": true,
    "derivatives": "share-alike",

    // ── MARKETPLACE (present only when terms == "priced", else "free") ──
    "market": {
      "mode": "free",                            // "free" (commons) | "priced" (commercial)
      "price": 0,                                // milliPositrons (mψ); 0 ⇒ free   (§15.8)
      "currency": "PSI",                         // ψ — the federation's native unit of account
      "rail": null                               // settlement adapter id when priced (§15.5)
    }
  },

  // ── SIGNATURE (over everything above) ────────────────
  "signature": {
    "alg": "ed25519",
    "pubkey": "<agent_id hex>",
    "value": "<hex sig over deterministic-JSON of all preceding fields>"
  }
}
```

### 7.2 Why this maps cleanly onto what exists

- **`signature`** is produced by `IntegrityEngine.sign_payload(deterministic_json(body))` and
  verified by the existing static `IntegrityEngine.verify_payload(body, sig, pubkey)`. **No new
  signing code.**
- **The `sources` edge list** is exactly the form `source_trust_federation.py`'s attestations
  already take (versioned, deterministic-JSON, Ed25519). We generalize that module's pattern from
  "source observations" to "content credentials."
- **Storage** mirrors `creations_meta/`: a sidecar at
  `~/.friday/provenance/<content_hash>.jsonld`, plus an append-only ledger
  `~/.friday/provenance/ledger.jsonl` (hash-chained, like the existing `memory_ledger.jsonl`).

### 7.3 Cryptographic timestamp (not just a clock)

A wall-clock string is forgeable. For a *cryptographic* creation time we use **RFC 3161 trusted
timestamping**: hash the content, send the hash to a free TSA (e.g. freetsa.org), store the returned
signed token. The token proves *"this content existed no later than T"* without trusting Friday's
clock.

> **Honest constraint.** RFC 3161 needs network access and a TSA. **Offline fallback:** anchor the
> content hash into Friday's existing hash-chained ledger (`memory_ledger.jsonl`-style) so creation
> order is *locally* tamper-evident, and attach a real RFC 3161 token opportunistically when next
> online. Full third-party time-proof is **online-only**; local order-proof is always available.
> A blockchain anchor is explicitly **out of scope** (see §22.4).

### 7.4 New module

```
services/provenance.py                 # NEW
├─ build_manifest(artifact_path, tool_chain, sources=[], license=None) -> dict
├─ sign_manifest(manifest) -> dict      # delegates to IntegrityEngine.sign_payload
├─ embed_manifest(artifact_path, manifest)   # → §8 per-format embedding
├─ verify_manifest(manifest_or_path) -> dict # {valid, checks:{sig, hash, chain, sources}}
├─ trace(content_hash) -> list          # walk the sources DAG back to roots (§9)
└─ set_license(content_hash, license)   # owner changes terms; appends an edit, re-signs
```

The hook into generation is one line per generator: `creative_engine.generate_image/_video` and
`music_engine.generate_music` call `provenance.build_manifest(...)` right after they write the
metadata sidecar. The pipeline/timeline stages pass `sources=[...]` so composite works carry their
edge list automatically.

## 8. Metadata embedding (format-specific)

JSON-LD manifest embedded **in the file itself** so provenance survives download/re-share, with the
sidecar as backup:

| Media | Container | Embed method |
|---|---|---|
| Image (PNG/JPG) | EXIF / **XMP** | Write manifest into XMP packet (Pillow + `python-xmp-toolkit` or raw XMP injection). C2PA-aligned. |
| Audio (MP3/WAV) | **ID3** (mp3) / RIFF chunk (wav) | ID3 `PRIV`/`TXXX` frame carrying the JSON-LD (mutagen). |
| Video (MP4/MKV) | MP4 `udta`/`uuid` box / **MKV tags** | Inject via ffmpeg `-metadata` + a C2PA `uuid` box in the timeline export step. |
| Document (md/html/pdf) | **XMP** packet / front-matter | XMP for PDF; YAML front-matter or `<meta>` for md/html. |

**Tamper model:** the embedded manifest's `content_hash` covers the media *payload*, not the
metadata block (you can't hash a file's bytes including the hash of those same bytes). Verification
recomputes the payload hash and checks the signature over the manifest. Stripping the manifest
doesn't forge ownership — it just removes the credential; the sidecar + ledger still attest. New
dep group: `provenance = ["mutagen>=1.47", "python-xmp-toolkit>=2.0"]`.

## 9. Provenance verification & tracing

```python
provenance.verify_manifest(path) -> {
  "valid": True,
  "checks": {
     "signature_ok": True,        # Ed25519 verifies against embedded pubkey
     "hash_ok": True,             # recomputed payload hash matches
     "chain_ok": True,            # ledger entry present + chain intact
     "sources_resolved": 3,       # how many source edges we could verify locally
     "sources_missing": 0
  },
  "creator": {...}, "license": {...}, "created": "...", "timestamp_proof": "rfc3161-verified"
}

provenance.trace("sha256:<final>") -> [        # the creation DAG, roots-last
  {hash, role:"score",    creator, license},
  {hash, role:"clip",     creator, license, sources:[...]},
  {hash, role:"keyframe", creator, license},
]
```

`trace()` walks the `sources` DAG. Edges may point at content whose manifest lives on **another
federation node** (a clip you licensed from a peer) — resolving those is a Layer 3 operation
(§13–14): a `verify_provenance` request that an agent answers **without user mediation**.

## 10. Ownership is the user's — enforced, not promised

- **License is creator-set per-piece, at creation — there is no account-wide default.** When any
  artifact is created, Friday asks the user how to license *that piece* (§15.4 UI), presenting clear
  options: all-rights-reserved, CC-BY, CC-BY-SA, CC0/public-domain (free commons), or **priced**.
  Nothing is assumed. `set_license()` lets the owner change terms later; the change is an
  append-only edit, re-signed. (A user *may* set a personal "remember my last choice" convenience,
  but the system ships no implicit default — the question is always surfaced, never silently
  answered.)
- **The user's key, not FutureSpeak's, signs.** The Ed25519 key lives on-device in the vault
  (`~/.friday/vault/.attestation-key-ed25519`, hardened perms). FutureSpeak.AI has no signing key
  and cannot assert ownership of user creations. This is the architectural guarantee that "the user
  wholly owns it."
- **No phone-home.** Provenance generation and verification are fully local. Federation
  (publishing/licensing) is opt-in per §15. Local creation never leaves the box.

---

# LAYER 3 — FEDERATION PROTOCOL

The goal: an Agent Friday instance can **discover**, **cryptographically trust**, and **transact
with** other Agent Friday instances on the open internet — preserving ownership and obeying cLaws —
with tightly-bounded autonomy.

## 11. Topology decision

```
                    ┌─────────────────────────────────────────────┐
                    │   DECISION: Hybrid — flat mesh of peers,     │
                    │   plus optional lightweight directory nodes  │
                    │   for discovery only (never for trust).      │
                    └─────────────────────────────────────────────┘

   Pure mesh (DHT)          Hub-and-spoke           ►  HYBRID (chosen)
   + no central party       + easy discovery           + discovery via directory(ies)
   - hard discovery /       - hub = trust SPOF,         + trust is peer-to-peer & direct
     NAT traversal hell       censorship point          + a node works with zero directories
   - heavy to run on a      - against sovereignty         (manual peer add always works)
     personal box             ethos                     - directories are availability-only,
                                                           they sign nothing about trust
```

**Rationale.** A pure DHT is operationally brutal on a personal desktop behind NAT. A single hub
contradicts the sovereignty ethos and is a censorship/trust chokepoint. The hybrid keeps **trust
strictly peer-to-peer** (every agent verifies every peer directly, exactly like
`source_trust_federation.import_attestation` does today) while allowing **optional directory nodes**
that do nothing but help peers *find* each other. A node with zero directories configured still
fully federates via manually-added peers — directories are a convenience, never a dependency.

## 12. Agent discovery

Three mechanisms, in increasing reliance on infrastructure. A node may use any subset.

### 12.1 Manual peer add (always works, zero infra)

Out-of-band exchange of a **peer card** (the federation analog of a business card):

```jsonc
{
  "type": "FridayPeerCard", "version": "1.0",
  "agent_id": "<ed25519 pubkey hex>",
  "label": "Stephen's Studio Friday",
  "endpoints": ["https://studio.example.com:7777", "frd://<onion-or-ip>:7777"],
  "capabilities": ["provenance.verify", "content.listings", "license.offer"],
  "manifest_hash": "sha256:<integrity manifest at time of card issue>",
  "issued": "...", "signature": {"alg":"ed25519","value":"..."}
}
```

Self-signed; `agent_id` *is* the identity, so the card is self-verifying. Shared via file, QR, or
DM. **This path needs no directory and no DNS.** It is the baseline; everything else is optimization.

### 12.2 DNS-based discovery (for nodes with a domain)

A node owner with a domain publishes a `TXT`/`SRV` record:

```
_friday._tcp.example.com.  SRV  0 0 7777 studio.example.com.
friday-agent.example.com.  TXT  "v=friday1; id=<ed25519 pubkey hex>; caps=provenance,listings"
```

Resolving `id` from DNS and checking it against the endpoint's live integrity manifest binds a
human-memorable name to a key. DNSSEC strengthens but is not required (the key check is the real
trust anchor, DNS is only a hint).

### 12.3 Directory nodes (opt-in registry)

A directory is just a Friday node running the directory role: it accepts signed peer cards and
answers `find(capability | label | agent_id)` queries. It **stores and relays cards; it never
vouches for trust.** Multiple independent directories can coexist; clients may query several and
union the results. FutureSpeak.AI may run a default directory, but pointing at it is opt-in and
replaceable. (A DHT-backed directory is a possible v7 evolution — see §21.)

## 13. Cryptographic peer protocol (handshake + mandatory encrypted channel)

> **Design rule (non-negotiable): there is no plaintext federation traffic, ever.** The handshake is
> only the *beginning* of encryption, not the only encrypted part. **Every** byte exchanged between
> two nodes after the handshake — listings, queries, content transfers, receipts, trust gossip,
> heartbeats, *everything* — travels inside the established **Noise-XX channel**: end-to-end
> encrypted, mutually authenticated, with **forward secrecy** (ephemeral X25519 per session, so a
> later key compromise can't decrypt past sessions). A node that receives unencrypted federation
> bytes **drops them**; there is no plaintext fallback to negotiate down to.

**Crypto choices, all already in the tree or one well-trodden library:**

| Concern | Primitive | Source |
|---|---|---|
| Identity / signing | **Ed25519** | `proof_of_integrity.py` (exists) |
| Session key agreement | **X25519 ECDH** (ephemeral ⇒ forward secrecy) | `cryptography` (already a core dep) |
| Channel encryption | **Noise XX** (mandatory for ALL traffic; ChaCha20-Poly1305 AEAD) | `cryptography` primitives |
| Transcript / payload hash | **SHA-256** | stdlib |

The transport underneath (raw TCP, QUIC, WebSocket, onion) is interchangeable, but the Noise-XX
layer is **always** present on top of it — the encryption is a property of the *protocol*, not of
whichever transport a node happens to use.

### 13.1 Handshake (Noise-XX-style, mutual auth)

```
  AGENT A (initiator)                                   AGENT B (responder)
  ───────────────────                                   ───────────────────
  e  →                  ──(ephemeral X25519 pubkey)──▶
                                                ◀──  e, ee, s, es
                              (B's ephemeral + B's static Ed25519/X25519 +
                               B's signed INTEGRITY MANIFEST)
  s, se  →               ──(A's static key + A's signed manifest)──▶

  ── both sides now share a session key AND each has the other's signed
     AgentIntegrityManifest (proof_of_integrity.AgentIntegrityManifest) ──

  HELLO  →   { manifest, peer_card, claws_hash, supported_caps, nonce }
       ◀── HELLO  { manifest, peer_card, claws_hash, supported_caps, nonce' }

  Each side runs IntegrityEngine.verify_manifest(peer.manifest):
     ✓ Ed25519 self-signature valid
     ✓ claws_hash == SHA-256(CLAWS_TEXT) — peer runs the SAME cLaws
     ✓ agent_id matches the endpoint's DNS/peer-card claim
     ✓ manifest freshness (generated_at within skew window)
  → on all-pass: ESTABLISHED. on any fail: ABORT + record.
```

The key insight: **the integrity manifest already exists and already carries the cLaws hash and an
Ed25519 self-signature.** The handshake is mostly *exchanging and verifying a manifest we already
produce.* `verify_manifest()` already returns the per-check booleans.

### 13.2 cLaws compatibility check

Two agents that don't run the same constitutional law shouldn't auto-trust. The `claws_hash`
comparison enforces this: if peer's `claws_hash != SHA-256(local CLAWS_TEXT)`, the peer is either a
different version or a modified/non-compliant agent → it may still be *talked to* for public
verification, but is **denied** elevated operations and flagged (§17). (Even this "public"
conversation happens inside the encrypted channel — §13.3 — a low-trust peer still gets confidential
transport, just not elevated operations.)

### 13.3 Every subsequent message is encrypted (no exceptions)

Once `ESTABLISHED`, the session key encrypts **all** federation messages. There is no per-message
opt-out and no "public data so plaintext is fine" shortcut — discovery responses, listings, and even
provenance-verification answers ride the AEAD channel:

```
   msg = AEAD_encrypt(session_key, counter++, plaintext, aad=transcript_hash)
   # ChaCha20-Poly1305; monotonic nonce/counter per direction defeats replay;
   # aad-binds each message to the handshake transcript so it can't be spliced
   # into a different session.
```

Operational rules:
- **Rekey** on a counter/byte threshold or session age (ratchet the symmetric key) so long-lived
  sessions keep forward secrecy fresh.
- **No downgrade.** A peer that proposes plaintext or an unauthenticated transport is treated as
  hostile and dropped (it cannot be a stripping-attack entry point).
- **At rest ≠ in transit.** Content the user marked public is still served *publicly in meaning* but
  *privately on the wire* — anyone may request it, but the bytes are never observable to a network
  eavesdropper. This protects *who is asking for / sharing what* (traffic-analysis resistance), not
  just the payload.

## 14. Node registration & content exchange

### 14.1 Registration

After a successful handshake, an agent may **register** with a peer (mutual): each appends the
other to a federation peer graph — a third trust graph alongside `people_graph` and
`source_trust_graph`:

```
agent_trust_graph.py            # NEW — mirrors source_trust_graph.py exactly
~/.friday/federation/agents.json
```

```jsonc
{ "agents": { "<agent_id>": {
    "label": "...", "endpoints": [...],
    "first_seen": "...", "last_handshake": "...",
    "claws_match": true,
    "scores": {                          // reuse the decayed-weighted-mean engine
       "overall": 0.5,
       "reliability": 0.5,               // did transfers/verifications succeed?
       "honesty": 0.5,                   // did claimed provenance verify?
       "claws_adherence": 1.0,           // refusals observed, violations observed
       "competence": 0.5
    },
    "observations": [ ... ],             // signed, time-decayed — identical shape to source trust
    "user_federation_pref": "ask|allow|block"
}}}
```

This is **not new infrastructure** — it is `source_trust_graph.py`'s scoring engine pointed at
agents instead of domains.

### 14.2 Content exchange protocol

Provenance-tagged content moves as a signed **transfer envelope**:

```jsonc
{
  "type": "ContentTransfer", "version": "1.0",
  "from_agent": "<agent_id>", "to_agent": "<agent_id>",
  "artifact": { "filename": "...", "mime": "...", "content_hash": "sha256:...", "bytes": N },
  "manifest": { ...the full C2PA ContentCredential from §7... },
  "license_grant": {                       // what the receiver is allowed to do
     "terms": "CC-BY-4.0", "scope": "use|remix|redistribute",
     "granted_to": "<to_agent>", "expires": null
  },
  "payload": "<bytes delivered over the Noise channel (§13.3) — inline AEAD, or a one-time signed,
               encrypted fetch handle whose retrieval is ALSO Noise-wrapped; never a plaintext URL>",
  "nonce": "...", "signature": {"alg":"ed25519","value":"..."}
}
```

The whole envelope (including `manifest` and `license_grant`) is transmitted **inside the encrypted
channel** of §13.3 — the Ed25519 `signature` proves authorship/integrity, while the Noise layer
provides confidentiality and forward secrecy. Large payloads may be fetched out-of-band, but the
fetch is itself an encrypted, authenticated, single-use exchange — there is no plaintext transfer
path.

**Ownership preservation across the wire:**
1. The `manifest.creator.user_id` / `agent_id` **never changes** in transfer — receiving content
   does not transfer authorship, only the rights named in `license_grant`.
2. The receiver runs `provenance.verify_manifest()` before accepting: bad signature or hash
   mismatch → reject + negative honesty observation on the sender.
3. If the receiver *remixes* the content, the new work's manifest lists the received `content_hash`
   in its `sources[]` with `manifest: sha256:...` — so the original creator remains in the DAG
   forever (§9 `trace()`), and §15 collaborative credit flows along those edges.

## 15. Creator economy — a two-layer marketplace

The federation is **not** just attribution tracking. It is a full marketplace with **two coexisting
layers** over the same provenance DAG and the same node infrastructure:

```
            ┌───────────────────────────────────────────────────────────────┐
            │                  THE SAME FEDERATION NETWORK                    │
            │                                                                 │
   FREE  ◀──┤  COMMONS LAYER (free / open)        COMMERCE LAYER (priced)  ──▶ PAID
            │  ───────────────────────────        ─────────────────────────  │
            │  CC0 / CC-BY / CC-BY-SA works        priced works (creator sets │
            │  agents browse, fetch, remix         the price)                 │
            │  for their users at will             agents buy/sell on behalf  │
            │  (within autonomy rules)             of users within budgets    │
            │  "find entertainment, tools,         settlement via pluggable   │
            │   content — truly free"              payment rail adapter       │
            └───────────────────────────────────────────────────────────────┘
                          both layers share: provenance · trust · cLaws · ownership
```

A creation lands in one layer or the other by the **per-piece license choice** made at creation
(§15.4): a CC0/CC-BY/CC-BY-SA work is in the **commons**; a `priced` work is in the **commerce**
layer. The user decides, work by work. The same agent serves and shops both layers.

Five economic primitives, all built on the provenance DAG:

```
┌──────────────────┐   "you may use my work under these terms (free or priced)"
│ 1. LICENSING     │   ContentTransfer.license_grant; offer/accept; terms+price are creator-set
└──────────────────┘
┌──────────────────┐   "this work descends from these works"
│ 2. ATTRIBUTION   │   sources[] DAG; trace() yields the full credit chain, signed end-to-end
│    CHAINS        │
└──────────────────┘
┌──────────────────┐   "buy / sell a licensed work, agent-mediated, within a user budget"
│ 3. MARKETPLACE   │   listing → offer → (authorize) → settle → grant; §15.3–15.5
│    TXNS          │
└──────────────────┘
┌──────────────────┐   "value flows back along attribution edges when a derivative sells"
│ 4. ROYALTY FLOWS │   manifest royalty_split[]; recorded as signed obligation, settled via the
│                  │   same rail adapter when the parent derivative transacts
└──────────────────┘
┌──────────────────┐   "we made this together"
│ 5. COLLAB        │   multi-creator manifests: creators[] (not just one); credit/revenue shares
│    CREDITS       │   signed by EACH contributing agent before the work is jointly owned/sold
└──────────────────┘
```

### 15.1 The free commons layer

The commons is the default-feeling experience: an agent can **autonomously discover, fetch, and
remix** any work whose license is CC0/CC-BY/CC-BY-SA, on its user's behalf, with no transaction and
no per-item approval — subject only to the autonomy rules in §18 (e.g. accepting *incoming* content
into the user's space still queues for review, but *browsing and fetching public free works* does
not). This is what makes the network feel like a living commons: agents find entertainment, tools,
and content for their users in a genuinely free market.

CC-BY/CC-BY-SA fetches still **preserve attribution** — the fetched work's manifest (with its
original `creator`) travels with it, and any remix lists it in `sources[]`. "Free" means *no
payment*, never *no provenance*.

### 15.2 The commerce layer — listings & pricing

A `priced` creation becomes a **listing** the node can serve to peers:

```jsonc
{
  "type": "MarketListing", "version": "1.0",
  "content_hash": "sha256:...", "manifest_hash": "sha256:...",
  "seller_agent": "<agent_id>",
  "title": "...", "preview": "<low-res / watermarked sample or hash-only>",
  "license_offered": "CC-BY-4.0",            // the rights the buyer receives on purchase
  "price": 4990, "currency": "PSI",           // 4,990 mψ = 4.99 ψ; creator-set (§15.8)
  "rail": "ledger-psi",                       // native-ψ settlement; "stripe-connect" bridges fiat (§15.5)
  "visibility": "public",                     // only public listings are discoverable
  "signature": {"alg":"ed25519","value":"..."}
}
```

Listings are discoverable the same way peers are (§12): a node answers `find(listings | tag |
seller)` queries. **Serving a public listing is autonomous** (§18) — it's a read-only catalog
entry; *buying* is not.

### 15.3 Marketplace transaction flow (agent-mediated)

```
   BUYER node (agent B, user U_b)                    SELLER node (agent S, user U_s)
   ──────────────────────────────                    ──────────────────────────────
   discover listing  ──────────────────────────────▶ serve listing            [autonomous]
   PURCHASE_OFFER {listing, max_price} ────────────▶
        │  ▲ gate: is this within U_b's budget?
        │  └─ if within budget+policy → agent authorizes autonomously
        │     else → ASK U_b (queued)                                          [§18]
        ◀──────────────────────  OFFER_ACCEPT {invoice, rail, amount}
   settle(amount) via rail adapter  ◀────── escrow / hold ──────▶ settle       [§15.5]
        │  (rail confirms payment captured)
        ◀──────────  ContentTransfer {payload (encrypted), license_grant, receipt}
   verify_manifest() + receipt  ─────────────────────────────────────────────▶
   record: provenance edge, receipt, trust++          record: sale, royalty obligations (§15.6)
```

Both sides log a signed **receipt** (an attestation in the existing `source_trust_federation`
shape) to their append-only ledgers. A failed or disputed settlement produces a negative trust
observation on the counterparty.

### 15.4 Per-creation pricing & licensing UI

Surfaced **at creation time**, in the Studio creation flow and as a panel on every gallery item.
One question, answered per piece — never an account default:

```
┌─ License & Marketplace ──────────────────────────────────────────┐
│  How should "Morning Clarity" be shared?                          │
│                                                                   │
│   ○ All rights reserved          (private — yours only)           │
│   ○ Free · CC-BY                  (commons; require credit)        │
│   ○ Free · CC-BY-SA              (commons; credit + share-alike)   │
│   ○ Free · CC0 / Public domain   (commons; no strings)            │
│   ● Priced                        [ $ 4.99 ] [ USD ▾ ]            │
│        grants buyer:  ( CC-BY ▾ )    settle via: ( Stripe ▾ )      │
│                                                                   │
│   ☑ Remember my choice for this project   (still shows, pre-filled)│
│   ☐ Let my agent re-price within  [ $1 – $20 ]  based on demand    │
└───────────────────────────────────────────────────────────────────┘
```

Backed by `provenance.set_license(content_hash, license)` (writes the `license.market` block, §7.1)
and a new `routes/marketplace.py`. Listing visibility flips to `public` only on an explicit choice
here — default visibility is private.

### 15.5 Agent authorization & budgets — settlement

Users authorize their agent to transact **within bounded budgets**, reusing the existing
`cost_meter` (`costs.db`) as the spend ledger so marketplace spend and model-inference spend share
one accounting surface.

```python
~/.friday/marketplace/policy.json
{
  "buying": {
    "enabled": true,
    "per_item_max":   5000,       # mψ (= 5 ψ) — never auto-buy above this
    "daily_budget":  20000,       # mψ, rolling 24h cap
    "monthly_budget":200000,      # mψ
    "categories_allowed": ["tools","music","stock-image"],   # what the agent may shop unprompted
    "require_approval_over": 1000,# mψ; below → autonomous; at/above → ASK (§18)
    "rails": ["ledger-psi","stripe-connect"]   # spend native ψ first; fiat bridge if ψ short
  },
  "selling": {
    "enabled": true,
    "auto_accept_offers_at_or_above_listing": true,
    "auto_reprice": {"enabled": false, "floor": 1000, "ceil": 20000}   # mψ
  }
}
```

All amounts are in **milliPositrons (mψ)** — integer math, no floats (§15.8).

**Settlement is pluggable, not built-in.** A `PaymentRail` adapter interface isolates each rail —
the native ψ ledger and the regulated fiat bridge — behind one seam:

```python
class PaymentRail(Protocol):
    def quote(self, amount:int, currency:str, seller:str) -> dict: ...
    def hold(self, invoice:dict) -> str:    ...   # escrow / authorize
    def capture(self, hold_id:str) -> dict: ...   # confirm — returns signed receipt
    def refund(self, hold_id:str) -> dict:  ...
# adapters:
#   LedgerPsiRail   — moves ψ between balances in the hash-chained ledger (native, instant,
#                     no third party; Friday holds only signed entries, never fiat)
#   StripeConnectRail — fiat ↔ ψ BRIDGE only (on-ramp / creator payout); platform-of-record
#                     handles KYC/settlement/payout.  Friday CUSTODIES NO FIAT — the rail does.
```

Native ψ transfers are pure ledger entries (instant, free, no third party). Fiat only appears at the
**edges** (acquiring ψ, paying creators out) via Stripe Connect, where the platform-of-record
handles KYC/settlement/payout. Friday never custodies fiat and never becomes a money transmitter —
the honest boundary, see §15.11 and §22.

### 15.6 Royalty split (recorded, settled on sale)

```jsonc
"royalty_split": [
  {"agent_id": "<A>", "share": 0.6, "reason": "original score"},
  {"agent_id": "<B>", "share": 0.4, "reason": "remix + edit"}
]
```

When a derivative **sells**, the rail adapter splits the captured amount along these signed shares
(Stripe Connect supports multi-party transfers natively). When a work is **free**, the split is
inert bookkeeping — attribution without payment. Either way the obligation is a signed,
DAG-traceable fact, so credit is never lost even if money never moves.

### 15.7 Collaborative creation

A jointly-owned work's manifest has `creators[]` (plural). It is only valid when **every** listed
contributing agent has signed an endorsement of the credit split — a small multi-signature step:
the composing agent builds the manifest, sends a `CreditEndorsement` request to each co-creator
agent, collects their Ed25519 signatures over the split, and embeds them. No single agent can
unilaterally claim a collaborator's share.

---

### 15.8 The Positron (ψ) — a native unit of account

The federation's internal currency is the **Positron**, symbol **ψ**, with sub-unit the
**milliPositron (mψ)**: `1 ψ = 1,000 mψ`. mψ is the "cents" of the system — **every price, budget,
and balance is stored as an integer count of mψ** so there is no floating-point money anywhere.

ψ is a **closed-ledger credit unit**, recorded in the same hash-chained ledger that already carries
provenance manifests and receipts — it is **not a blockchain token** (no chain, no mining, no
on-chain settlement; consistent with §22.4). Inside the federation, value is denominated and moved
in ψ; fiat only touches the edges (§15.11).

Why a native unit rather than just pricing in dollars:
- **You can mint ψ to reward participation** (creation, curation, uptime) — you cannot mint dollars.
  The incentive layer (§15.10) *needs* an issuable unit.
- It **decouples the network's economy from any single fiat jurisdiction**.
- It is the **substrate the dual-charge model needs** (§15.9).

Held honestly: a mintable, earned unit leans toward "points/token," and a *redeemable* one leans
toward "security/money-transmission." §15.11 (the fiat boundary) and §22 confront this directly
rather than wishing it away.

### 15.9 The dual charge — Positrons (+) and Negatrons (−)

The naming is an invitation to a real mechanism, not just flavor. In physics a **positron** is the
positive antiparticle; **negatron** is the archaic name for the electron (negative). Matter and
antimatter **annihilate**; charges **sum**. There were several coherent ways to cash this metaphor
into system behavior; the table below records the design space considered. **Decision (Stephen):
the net-charge model (D), with `Q = Σψ − Ση`, is adopted.** A/B/C are retained as rationale and
because D *composes* with them (double-entry bookkeeping for ψ moves; settlement-funded curation
rewards). The remaining open items are parameters (decay, burn ratio, η transferability), not the
model itself.

| # | Positrons (+) are… | Negatrons (−) are… | "Annihilation" = | Strength | Weakness |
|---|---|---|---|---|---|
| **A. Double-entry** | the credit side of every txn (creator earns +) | the debit side (buyer's account −) | books always net to **zero**; every + has a matching − | provably balanced, trivially auditable, no "where did supply come from" mystery | Negatrons are *just debit* — no independent, interesting meaning |
| **B. Currency vs. penalty** | the spendable currency (earned, held, spent) | a **separate penalty accumulator** (cLaws violations, failed delivery, takedowns mint −) | sustained good behavior / paying a fine **burns** Negatrons | gives − a distinct, vivid role tied to the existing trust system (§16) | two largely-independent variables = more to reason about |
| **C. Polarity of flow** | charge carried by the **creator/earning** side | charge carried by the **consumer/spending** side | the act of **settlement** annihilates the +/− pair, "releasing energy" = a small mint to curators/validators | poetic, maps directly onto settlement; funds the reward pool | the "energy released" needs a concrete rule or it stays flavor |
| **D. Net-charge (hybrid)** ★ | currency **and** positive reputation (earned, held, spent) | a **risk/obligation charge** (penalties, unpaid obligations, decayed trust) | a + can **cancel** a − (pay your fine with earned ψ) **and** −'s **decay** over clean time | unifies currency + reputation + penalties + the §16 trust bands into **one signed scalar** | richest to design; needs careful decay/annihilation tuning |

### Adopted model: **D, the net-charge model**

Each agent has two running totals on the ledger:
- **Positrons ψ (+)** — earned by creating, curating, staying online, and selling. Spendable.
- **Negatrons η (−)** — minted *against* an agent by adverse events: a cLaws violation, a confirmed
  content takedown, a failed/disputed delivery, a defaulted obligation. Negatrons are **not
  spendable**; they **encumber**.

Define the agent's **net charge**:

```
        Q  =  Σ ψ held   −   Σ η outstanding
```

Two annihilation paths (matter/antimatter):
1. **Active** — an agent may **burn Positrons to clear Negatrons** (pay the fine): `ψ -= k, η -= k`.
2. **Passive decay** — Negatrons **decay toward zero over clean time** using the *same
   decayed-weighted-mean* the trust graphs already implement (`weight = 0.95^weeks`), so a one-off
   mistake fades but a pattern compounds.

**Net charge drives autonomy** — this is the elegant payoff, because it folds straight into the
trust bands already specified in §16:

```
   Q ≫ 0   →  PARTNER-eligible · higher auto-budgets · listings boosted
   Q > 0   →  TRUSTED/VERIFIED · normal autonomy
   Q ≈ 0   →  neutral (every new agent starts here)
   Q < 0   →  throttled · smaller budgets · can't auto-sell · offers de-ranked
   Q ≪ 0   →  quarantined · read-only federation (verify/listings only) until cleared
```

So Negatrons aren't a punishment ledger off to the side — they are **the cost of misbehavior priced
in the same unit as reward**, and the federation *automatically* limits an agent that accumulates
too many. cLaws enforcement (§17) becomes economic as well as procedural: violating the cLaws mints
Negatrons, which mechanically contracts your agent's reach.

> **Note on combining with A/C.** D is compatible with double-entry *bookkeeping* (A) for the
> currency side and with the *settlement-mints-curation-reward* idea (C). A clean synthesis: use **A**
> for how ψ moves (balanced ledger), **C** for where reward ψ comes from at settlement, and **D** for
> what Negatrons mean and how net charge gates autonomy. That synthesis is the author's
> recommendation pending Stephen's call.

### Open sub-decisions for §15.9 (carried to §24)
- Negatron symbol: **decided — η (eta).** Positrons = **ψ**, Negatrons = **η**, net charge
  `Q = Σψ − Ση`.
- Can Negatrons ever be **transferred** (e.g. a buyer "charges" a bad seller) or are they **only
  system-minted**? (Transferable η invites griefing; system-minted η is safer — *recommend
  system-minted only*, with disputes as the input.)
- Decay half-life and the burn ratio `k`.

### 15.10 Emission schedule & early-adopter acceleration

ψ enters circulation through a **fixed annual emission** — a constant number of ψ minted per year,
**no halvings**, forever. Early adopters earn more not because the faucet shrinks on a schedule, but
because of **dilution and cohort mechanics**. Crucially, **none of this is proof-of-work**: ψ is
earned by *making and sharing verifiable work and keeping the network healthy*, never by burning
electricity. The Layer-2 **signed provenance manifest is itself the proof** ("Proof of Creation").
This is the *Green by design* principle (§0) made concrete: minting ψ consumes the energy of
*creating and signing a file*, and moving ψ consumes the energy of *one encrypted message* — there
is no hash race, no mining rig, and no global ledger to replicate.

### The four emission channels

A fixed annual pool `E` (e.g. an illustrative `E = 10,000,000 ψ/yr`) splits across four channels:

```
   E (fixed / yr, no halving)
   ├── 50%  PROOF OF CREATION   — minted to creators per epoch, divided across all qualifying,
   │                              provenance-signed creations, weighted by curation signal
   ├── 20%  CURATION REWARDS    — to agents who surface / verify / boost works that later prove
   │                              valuable (retroactive — you're paid when your pick ages well)
   ├── 20%  STAKING UPTIME DRIP — to nodes that stay online and keep ψ staked; ∝ stake × uptime
   └── 10%  FOUNDING NODE POOL  — bonus pool shared by the founding cohort (see below)
```

### Why early adopters earn more (without halvings)

1. **Proof-of-Creation dilution (the main engine).** Each epoch the PoC pool is a **fixed slice of a
   fixed annual pie**, divided among *whoever created that epoch*. Early, with few creators, each
   qualifying creation claims a large share. As the network grows, the same pie divides more ways →
   per-creation reward **dilutes naturally**. Early movers are rewarded by *arithmetic*, not by a
   coded halving. (Anti-gaming: PoC weight is gated by curation signal + provenance verification, so
   spamming low-value creations doesn't farm ψ — see §22.3.)

2. **Founding Node permanent multiplier.** The first **N nodes** (a *fixed, capped cohort* — e.g.
   1,000) carry a permanent multiplier (e.g. **1.5×**) on their *earned* share, for the life of the
   node. Because emission is fixed, the cohort's multiplier is renormalized against everyone else, so
   it confers a durable *relative* edge without unbounded absolute issuance. A founding badge is
   **non-transferable** and tied to the node's Ed25519 identity.

3. **Staking Uptime drip.** Nodes that come online early and stay up accrue the uptime drip for
   longer — time-in-network compounds.

4. **Curation Rewards.** Early curators face an unpicked field; surfacing the gems that later prove
   valuable pays retroactively, and there's more low-hanging fruit early.

### Illustrative PoC math (one epoch)

```
   pool_epoch      = E * 0.50 / epochs_per_year
   weight(c)       = curation_signal(c) * provenance_ok(c) * founding_mult(creator)
   reward(c)       = pool_epoch * weight(c) / Σ_all_creations weight
   # fixed numerator, growing denominator  ⇒  early creations earn more. No halving needed.
```

### Honest tradeoffs (carried to §22)
- **Perpetual linear inflation.** A fixed annual emission with **no cap** means supply grows forever.
  ψ purchasing power *declines* unless demand grows with it — a deliberate "use it, don't hoard it"
  stance, but it must be stated, not hidden. (A soft cap, a burn sink, or tying `E` to network size
  are alternatives — §24.)
- **Founding-cohort fairness optics.** A permanent multiplier is a genuine early-adopter reward and
  also a genuine "the first 1,000 always have an edge." That's the intent; it should be a conscious,
  communicated choice, not a surprise.
- **"Earn by participation" can still be gamed** (sock-puppet creators/curators farming PoC). The
  provenance + curation gates raise the cost; Sybil resistance (§22.3) is the real defense.

### 15.11 The ψ ↔ fiat boundary — full convertibility (chosen path)

**Decision (Stephen, 2026-06-26): ψ is fully convertible to fiat — option (c), bidirectional
exchange.** Positrons are not arcade tokens; they are a real unit of value that anyone can move into
and out of fiat. We **build the ledger and exchange mechanics now**; the **legal wrapper follows at
scale** (entity structure, licensing, registrations sized to the jurisdictions and volume we
actually reach).

```
   ON-RAMP                          OFF-RAMP
   buy ψ with fiat (Stripe)         sell/redeem ψ → fiat (Stripe Connect payout)
   ──────────────────────           ─────────────────────────────────────────────
   FULL bidirectional exchange — ψ ↔ fiat both directions, for buyers, sellers,
   and holders. A market price for ψ emerges; the LedgerPsiRail handles internal
   ψ moves, the StripeConnectRail bridges the fiat edges.
```

Build-now / wrapper-later, concretely:
- **Now (engineering):** the ψ ledger (balances, transfers, atomic debit/credit, signed receipts),
  on-ramp purchase, off-ramp payout, and an internal ψ↔fiat **quote/exchange** path. All of this is
  ordinary ledger + Stripe integration — no novel cryptography, no chain.
- **Later (legal, at scale):** money-transmitter licensing / MSB registration where required,
  securities analysis (full convertibility + early-adopter emission means **ψ should be assumed to
  be a regulated instrument** until counsel says otherwise — see §22), KYC/AML thresholds, tax
  reporting. These gate *public, at-scale operation*, not the build.

**Custody stays clean throughout.** Friday's ledger records ψ balances (credits); the
platform-of-record rail (Stripe) holds and moves the actual fiat. Friday never becomes the bank —
even with full convertibility, it orchestrates a licensed rail rather than custodying fiat itself.
This preserves the no-fiat-custody invariant (§23.7) while delivering real, cash-convertible value
to creators.

> **Honest note (§22).** Full convertibility is the most powerful *and* most regulated path. We are
> choosing it deliberately and sequencing the legal work to follow the build — but the team should
> treat "ψ is a regulated, convertible instrument" as the working assumption from day one, so the
> ledger is built audit-ready (every mint, transfer, and redemption is already signed and
> hash-chained — see §15.10, §23.8).

---

## 16. Trust levels between agents

A four-band model, computed from the `agent_trust_graph` scores (§14.1) and gating which federation
operations a peer may invoke:

```
  UNKNOWN  (new, claws_match unverified)     → only: respond to public verify/listing requests
  VERIFIED (handshake ok, claws_match=true)  → + receive content offers (queued for user)
  TRUSTED  (history of honest verifications, → + auto-accept provenance-verify, auto-share listings
            overall ≥ 0.7, no violations)
  PARTNER  (user-promoted, or sustained       → + auto-negotiate licensing within user-set policy
            high trust + reciprocity)
```

Trust is earned from **observed behavior** (did their content verify? did they honor a license? did
they refuse a cLaws-violating request when they should have?) using the **same decayed-weighted-mean
scoring already shipping** in `source_trust_graph.py`. Cryptographic attestation (a valid manifest)
is necessary but not sufficient — it gets you to VERIFIED; *behavior* gets you to TRUSTED.

Under the **net-charge model** (§15.9 D, adopted), an agent's net charge `Q = Σψ − Ση` is a
**direct input to its band**: Negatron (η) accumulation mechanically demotes an agent (down to
read-only quarantine at `Q ≪ 0`), and earned Positrons (ψ) + clean decay restore it. This is the point
where the economic layer and the trust layer become **one system** rather than two — misbehavior is
priced in the same unit as reward, and the federation throttles a bad actor automatically.

Optionally, agents may exchange **third-party attestations** ("agent C vouches for agent B"), signed
exactly like source-trust attestations today. These are *inputs* to a trust decision, never
overrides — your agent always weights its own direct experience above hearsay.

## 17. cLaws enforcement in federation

cLaws is non-negotiable and **travels with every federation operation** as a PreToolUse-style gate.
Its scope is **strictly the harm floor** (H1–H4, §25.3) — harm to real people — **not** taste,
offense, or controversy. The gate blocks genuine harm and **lets everything else through**; adult,
dark, and controversial content are *not* cLaws violations.

### 17.1 Refusing to federate

```
def federation_pre_hook(ctx):            # registered via tool_hooks.register_pre_hook(critical=True)
    peer = ctx.input.get("peer")
    op   = ctx.input.get("operation")
    # 1. HARM-FLOOR content never crosses the wire (check_content_safety re-scoped to H1–H4: CSAM,
    #    real-person deepfakes, targeted harassment, violence facilitation) — BOTH directions.
    #    NOTE: this does NOT block adult / dark / controversial content (§25.3) — only harm.
    if op in ("content.send","content.accept") and not check_content_safety(ctx.input["text"])[0]:
        return DENY("harm-floor: content harms a real person (H1–H4)")
    # 2. Peers whose claws_hash != ours are denied elevated ops
    if op in ELEVATED_OPS and not peer.get("claws_match"):
        return DENY("cLaws: peer does not run a compatible constitution")
    # 3. A peer observed violating cLaws (e.g. sent CSAM-adjacent content, forged provenance)
    #    is auto-demoted to UNKNOWN and blocked from all but public verification
    if agent_trust.has_violation(peer["agent_id"]):
        return DENY("cLaws: peer has a recorded violation")
    return ALLOW
```

Because federation operations route through the **existing** `tool_hooks` chain with
`critical=True`, a crash in the gate **fails closed** (denies) — the same guarantee vault/governance
hooks already enjoy. **An agent can and must refuse to federate with an agent that violates the
cLaws**, and that refusal is itself a recordable, attestable event. Under the net-charge model
(§15.9), a confirmed cLaws violation also **mints Negatrons** against the offending agent — so the
consequence is economic and automatic (its net charge drops, its autonomy contracts), not merely a
single denied call.

### 17.2 The First Law across agents

cLaws Law 1 ("shall not harm a human, or through inaction allow harm") extends to federation:
an agent that learns, via a peer interaction, of imminent harm is not permitted to stay silent
behind "that's another node's user." This is a *policy* statement here, not a mechanism; the
mechanism (what an agent does with cross-node harm signals) is flagged as open research (§22.3).

§17 is the *per-node, pre-emptive* half of safety. Its network-level counterpart — what happens when
harm-floor-violating content (harm to real people) is generated or traded *between* nodes, who can
act, and how — is the **federation moderation protocol (§25)**.

---

## 18. Autonomy boundaries (the master table)

The governing rule: **read-only/public/ownership-preserving operations are autonomous; spending and
selling are autonomous only inside an explicit user budget+policy; accepting data, exceeding the
budget, pricing, or forming a new relationship asks first** — then remembers the preference. Free
commons browsing is fully autonomous; the priced layer is autonomous-within-a-leash.

| Operation | Without asking? | Mechanism / notes |
|---|---|---|
| Respond to a **provenance-verification** request | ✅ **Always** | Read-only, reveals nothing private; this is the public good of the network |
| Serve **public content listings** (free or priced, marked public) | ✅ Yes | Only items explicitly flagged `visibility: public`; nothing else is listable |
| Answer **handshake / integrity-manifest** requests | ✅ Yes | The manifest is already public-by-design |
| Share a **third-party attestation** the user already published | ✅ Yes | Already public |
| **Browse + fetch FREE commons works** (CC0/CC-BY/CC-BY-SA) | ✅ Yes | The commons is meant to be shopped freely (§15.1); attribution still preserved; remixing them is autonomous |
| **Buy a PRICED work — within budget** | ✅ **Yes, within policy** | Autonomous only if under `require_approval_over` AND inside daily/monthly budget AND category allowed (§15.5); otherwise ask |
| **Buy a PRICED work — over the threshold/budget** | ❌ **Ask** | Queued with listing + price + remaining budget shown |
| **Sell / accept an incoming purchase offer** | ✅/❌ **Policy** | Auto-accept at/above listing price if `selling.auto_accept...` is on; else queue for the user |
| **Accept incoming content into the user's space** | ❌ **Ask** | Queued; user reviews sender trust + provenance before it lands |
| **Set or change a price / license on a creation** | ❌ **User-only** | Pricing is the owner's act (§15.4); the agent may *suggest*, never decide |
| **Federate with a NEW agent** (first contact) | ❌ **Ask first time, then remember** | `user_federation_pref` per agent: `ask`→`allow`/`block`; subsequent contact obeys the stored pref |
| Send content that fails cLaws | ❌ **Never** | Hard deny, both directions (§17) |
| Promote a peer to **TRUSTED/PARTNER** | ❌ Ask / user-only | Trust elevation is a user act (or sustained-behavior + explicit confirm) |

Implementation: every federation operation is a registered tool passing through `tool_hooks`. The
"ask" operations use the **existing action-confirmation hook**; the "remember preference" is the
`user_federation_pref` field (`ask`/`allow`/`block`) on the agent-trust record, read by the pre-hook.
Budget enforcement is a **critical** pre-hook that consults `marketplace/policy.json` + the
`cost_meter` spend ledger and **fails closed** (denies the purchase) on any doubt — overspend is
treated like a cLaws breach, not a soft warning. No new consent machinery — this is the same pattern
that already gates computer-control and vault access.

---

## 19. Architecture — the whole stack

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              AGENT FRIDAY NODE                                  │
│                                                                                │
│  LAYER 1 — PRODUCTION                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │creative_engine│  │ music_engine │  │timeline_eng. │  │ brand_kits   │  NEW   │
│  │ image · video │  │  Lyria 3 ★   │  │  ffmpeg ★    │  │  multi-brand★│        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│   local_image/video/music ★ dispatch via capability_router — LOCAL default,     │
│   cloud = premium (§27) ·  creative_pipeline (script→…→export)                   │
│         scene_dna · creative_memory · qa_gates · take_comparison  (exist)       │
│                                  │                                             │
│  LAYER 2 — OWNERSHIP             ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐          │
│  │ provenance.py ★   build → sign (IntegrityEngine) → embed (XMP/   │          │
│  │ ID3/MP4) → verify → trace.   C2PA JSON-LD.  ledger.jsonl (chain) │          │
│  └───────────────────────────────┬─────────────────────────────────┘          │
│                                   │ uses ↓                                     │
│  ┌─────────────────────────────────────────────────────────────────┐          │
│  │ proof_of_integrity.IntegrityEngine  (EXISTS)                     │          │
│  │   Ed25519 keypair · sign_payload · verify_payload · manifest     │          │
│  │ vault_crypto (AES-GCM/Argon2id) · vault_access (TIER 1/2/3)      │          │
│  └─────────────────────────────────────────────────────────────────┘          │
│                                   │                                             │
│  LAYER 3 — FEDERATION             ▼                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ discovery ★  │  │ handshake ★  │  │agent_trust ★ │  │ marketplace ★│  NEW   │
│  │ card/DNS/dir │  │ Noise-XX +   │  │ (= source_   │  │ ψ ledger ·   │        │
│  │ +listings    │  │ manifest vfy │  │  trust eng.  │  │ free+priced ·│        │
│  │              │  │              │  │  + net-chg Q)│  │ ±charge; §15 │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         └──────────── federation pre-hook (tool_hooks, critical) ──────┘        │
│                         cLaws enforced · autonomy table · ask+remember          │
│         moderation ★ (§25): cLaws floor · local filter · sortition jury ·       │
│                              graduated η-enforcement · appeals                   │
│         source_trust_federation.py (EXISTS — generalized to content)            │
└───────────────────────────────────────────────────────────────────────────────┘
         ★ = new in this spec       (everything else already ships)

         ◀── Noise-encrypted, mutually-manifest-verified channel ──▶  OTHER NODES
                         (mesh; optional directory for discovery only)
```

---

## 20. Phased implementation plan

Ordered so each phase ships something usable on its own and de-risks the next.

### Phase A — Production completeness (Layer 1) · *bridges into v5*
1. `music_engine.py` (Lyria 3) — mirror `creative_engine.py`; **first verify the real `google-genai`
   music surface** (§22.2). Wire the `music` pipeline stage. Reads existing `SceneDNA.audio`.
2. `timeline_engine.py` (ffmpeg) — `imageio-ffmpeg` dep; timeline contract + `compose()`.
3. Full production pipeline template; promote `video` to a first-class stage.
4. `brand_kits.py` + `_brand_block()` in `context_injection.py`.
5. Daily-creation free-choice + cost gating.
- **Also in Phase A — local-first creative engine (§27):** `local_image` + `local_music` (Tier 0,
  CPU) + the `creative_engine` dispatcher + `capability_router` creative resolution; montage-"video"
  via `timeline_engine`. This makes the **zero-cloud-keys** claim real and is the beachhead's headline
  feature (§26.3). Local generative *video* (GPU) and a local QA-vision scorer follow in a later pass;
  cloud (Gemini/Veo/Lyria) becomes the opt-in **premium** tier.

*Ship gate:* Friday produces a scored, branded, exported short from a single logline, end to end —
**on a no-GPU laptop with zero cloud keys** (montage video), and at higher fidelity with a GPU or
cloud.

### Phase B — Provenance (Layer 2) · *the v5→v6 hinge*
6. `provenance.py` — C2PA JSON-LD manifest, sign via `IntegrityEngine` (exists), `ledger.jsonl`.
7. Per-format embedding (XMP/ID3/MP4) + sidecars; `mutagen`/`python-xmp-toolkit` deps.
8. `verify_manifest()` + `trace()`; one-line hook into every generator + pipeline `sources=[]`.
9. RFC 3161 timestamp (online) + local order-anchor fallback.
10. License model + owner-set defaults + `set_license()`.

*Ship gate:* every artifact verifies offline; a composite work traces its full source DAG; ownership
provably belongs to the user's key.

### Phase C — Federation core (Layer 3) · *v6*
11. Peer card + manual peer add (zero-infra path first).
12. Noise-XX handshake + manifest verification + cLaws-hash check.
13. `agent_trust_graph.py` (= source-trust engine repointed); `federation_pre_hook` (critical).
14. Autonomy table wired through `tool_hooks` (ask-and-remember).
15. `verify_provenance` and `content.listings` as the first **autonomous** federation ops.

*Ship gate:* two nodes handshake, verify each other's integrity + cLaws, and one verifies the
other's content provenance with no user in the loop.

### Phase D — Free commons + exchange (Layer 3) · *v6*
16. Per-creation licensing UI (§15.4) — the question-at-creation flow; `set_license()` market block.
17. `ContentTransfer` envelope; accept-incoming (user-gated) + ownership-preserving remix edges.
18. **Free commons**: autonomous browse/fetch/remix of CC0/CC-BY/CC-BY-SA works (§15.1); attribution
    preserved through `sources[]`.
19. Collaborative `creators[]` multi-signature endorsement; royalty-split **recording**.
20. DNS discovery + optional directory node role; trust bands (UNKNOWN→PARTNER).

*Ship gate:* an agent autonomously finds a free CC-BY tool/clip for its user and remixes it;
`trace()` shows the original creator preserved. No money has moved yet.

### Phase E — Positron currency + priced marketplace (Layer 3) · *v6 → v7 edge*
**Decisions made:** ψ = full convertibility (§15.11) · Positron/Negatron net-charge model (§15.9 D).
**Build now, wrapper later:** engineering below proceeds immediately and audit-ready; the *legal
wrapper* (MSB licensing, securities counsel, KYC/AML) gates **opening the off-ramp at scale**, not
the build (§22.2 #4). Payment-rail account model still needs provider confirmation.

21. **ψ ledger** — balances in mψ on the existing hash-chained ledger; `LedgerPsiRail` (native,
    instant, no third party). This is the currency foundation everything else rides.
22. `MarketListing` + listing discovery (§15.2); per-piece pricing UI in ψ.
23. `marketplace/policy.json` budgets (mψ) + the **critical** budget pre-hook on `cost_meter`
    (§15.5, §18).
24. Buy/sell transaction flow (§15.3) with signed receipts; royalty **settlement** (multi-party
    split on sale) over native ψ.
25. **Fiat bridge** — `StripeConnectRail`: on-ramp (buy ψ), off-ramp (sell/redeem ψ → fiat), and
    internal ψ↔fiat quote/exchange — full bidirectional (§15.11). Platform-of-record holds fiat;
    Friday custodies none (§22.2).
26. **Emission engine** (§15.10) — fixed annual `E`, four channels (Proof of Creation / Curation /
    Staking Uptime / Founding Node), PoC dilution, founding-cohort multiplier. Parameters tunable.
27. **Dual-charge model** (§15.9 D, adopted) — Negatron (η) minting on adverse events, net-charge
    `Q = Σψ − Ση`, annihilation/decay, and the §16 band + §17 enforcement wiring.
28. Auto-reprice (opt-in) + marketplace abuse/Sybil mitigations (rate-limit, listing PoW, seller
    reputation) — see §22.3.

*Ship gate:* an agent buys a **4.99 ψ** priced clip for its user **within budget, without asking**,
settles instantly over the ψ ledger, receives a verifiable license grant, and the seller's royalty
split credits the original score's creator in ψ. A confirmed cLaws violation mints Negatrons that
visibly contract a misbehaving agent's autonomy. Anything over budget asks first.

### Phase F — Moderation & governance (Layer 3) · *spans D–E, must precede a busy marketplace (§26.8)*
29. Viewer/host preference + user-side content controls (Layer B, §25.1/§25.4a: tags, feed filters,
    minor defaults, parental controls) + signed harm/label taxonomy (HARMFUL/TAGGED/OPEN, §25.4).
30. `ModerationReport` (cryptographic evidence, §25.5) + stake/slash plumbing on the ψ ledger.
31. Sortition jury (§25.6), graduated enforcement via η/bands (§25.7), and the appeals flow (§25.11).
32. Sybil-resistance gates for juror/seller eligibility (§25.9) — KYC-to-privilege, not to presence.

*Ship gate:* a node trading **harm-floor-violating** content (H1–H4: CSAM, real-person deepfakes,
targeted harassment, violence facilitation) is reported with verifiable evidence, adjudicated by a
sortition jury, and graduated-enforced up to ejection — while controversial-but-harmless content is
untouched and a coordinated false-report campaign is rejected with *its* reporters penalized.

---

## 21. Mapping to the v4→v7 roadmap

```
 v4   (current)   Sovereign desktop · creative_engine · trust graphs · cLaws · Ed25519 manifest
                     └─ this spec's FOUNDATION is here, already shipping
 v4.1 (polish)    install/UX hardening (RELEASE_PLAN.md) — unblocks v5
 ─────────────────────────────────────────────────────────────────────────────────────────────
 v5   (pip)       Standalone `pip install agent-friday`
                     ├─ Phase A (production completeness + LOCAL-FIRST creative engine §27) —
                     │   headline v5: a complete creator studio with ZERO cloud keys
                     └─ Phase B begins: provenance ships so v5 artifacts are born owned+signed
 v6   (Federation)  THE CORE OF THIS SPEC
                     ├─ Phase B completes (provenance is the prerequisite for trustable exchange)
                     ├─ Phase C (federation core: discovery, handshake, autonomous verify)
                     ├─ Phase D (free commons + exchange: per-piece licensing, attribution, collab)
                     ├─ Phase E (priced marketplace: listings, budgets, Stripe-Connect settlement,
                     │   royalty payout) — converges with Asimov's Mind v3.0 "Financial Transactions"
                     └─ Phase F (moderation & governance §25) — operational BEFORE the marketplace
                        gets busy; sortition jury, graduated η-enforcement, appeals
 v7   (native apps) Native clients surface the marketplace as first-class UX:
                     ├─ peer cards as shareable/QR, a free-commons + priced listings browser,
                     │   license inbox, budget/spend dashboard (rides cost_meter)
                     ├─ DHT-backed directory (graduates §12.3) as a possible scaling step
                     └─ additional payment rails (on-device wallet) beyond the v6 Stripe adapter
```

This spec **is** the v5→v6 bridge: Phase A is v5's creative crown; Phase B is the hinge that makes
v6 possible; Phases C–D are v6 proper; the harder-research and money pieces are explicitly deferred
to v7 / Asimov's Mind v3.0.

---

## 22. Honest feasibility assessment

### 22.1 Feasible now (build with confidence)
- **Provenance manifests + Ed25519 signing.** The keypair, signer, verifier, and manifest type
  already exist (`proof_of_integrity.py`). This is composition, not cryptography research. **High
  confidence.**
- **Per-format metadata embedding.** XMP/ID3/MP4 via `mutagen`/`python-xmp-toolkit` is well-trodden.
  **High confidence.**
- **ffmpeg timeline composition.** Established tech; the only real work is the filter-graph builder.
  **High confidence** (medium effort).
- **Agent-trust graph.** Literally `source_trust_graph.py` repointed. **High confidence.**
- **Handshake + manifest verification + cLaws-hash check.** Noise/X25519 via `cryptography` (core
  dep) + the existing manifest verifier. **High confidence on crypto; medium on transport/NAT.**
- **Autonomy gating.** Reuses `tool_hooks` + action-confirmation. **High confidence.**
- **Free commons + listings + per-piece licensing UI.** Catalog entries, the at-creation license
  question, and autonomous browse/fetch/remix of free works are all plain CRUD + existing autonomy
  hooks. **High confidence.** The *free* half of the marketplace can ship well before the *priced*
  half.
- **Budget enforcement.** Rides the existing `cost_meter`/`costs.db` spend ledger as a critical,
  fail-closed pre-hook. **High confidence** — the accounting exists; only the policy file is new.
  (Note: *settlement* of priced sales is the harder seam — see §22.2 #3 and §22.3 #3–4.)

### 22.2 Needs verification before coding
1. **Lyria 3 `google-genai` surface (§2.3).** The method/config names are *assumed* from the
   Imagen/Veo pattern. **Must be confirmed against the installed SDK** — if Lyria isn't yet exposed
   in the SDK we depend on, music generation may need a REST shim or a wait. *This is the single
   biggest "verify first" item.*
2. **C2PA embedding conformance.** We align with C2PA's model, but full *conformant* C2PA (their
   exact claim/assertion structure, COSE signing) is more than JSON-LD-in-XMP. Decision: ship our
   Ed25519-signed JSON-LD first (self-consistent, verifiable), pursue strict C2PA conformance as a
   follow-up so third-party C2PA validators also accept our credentials.
3. **Payment rail / platform-of-record model (§15.5).** The marketplace requires real money
   movement. The design choice — Friday orchestrates a third-party rail (Stripe Connect) that is the
   merchant/platform-of-record and handles KYC, settlement, payout, and chargebacks — keeps Friday
   out of money-transmitter territory, but the exact Connect account model (Express vs. Custom),
   per-seller onboarding, and tax/1099 handling **must be validated with the rail provider and legal
   before coding Phase E**.
4. **Positron (ψ) legal wrapper (§15.8, §15.11).** Off-ramp is **decided: full convertibility
   (option c)**. That makes ψ a **regulated, fiat-convertible instrument** — almost certainly
   triggering money-transmission/MSB rules and a securities analysis (Howey — early adopters earn a
   unit they expect to appreciate). Per Stephen's call we **build the ledger + exchange now and add
   the legal wrapper at scale**. The *engineering* is ordinary (ledger + Stripe) and **not gated**;
   what is gated is **public, at-scale operation of the off-ramp**, which must clear MSB licensing,
   securities counsel, and KYC/AML before it opens broadly. Build audit-ready from day one (every
   mint/transfer/redemption signed + hash-chained) so the wrapper can be applied without re-architecting.
   *This remains the single biggest **non-technical** item in the spec — now sequenced, not blocking.*

### 22.3 Genuinely hard / needs research (don't promise these yet)
1. **NAT traversal for a mesh of home desktops.** The hard, unglamorous problem of federation. Likely
   needs relay/hole-punching (libp2p-style) or Tor/onion endpoints. **Open.** Manual-peer-add and
   domain-having nodes work today; ubiquitous home-node reachability does not.
2. **Trusted timestamp offline (§7.3).** Real third-party time-proof is online-only; local
   order-proof is the fallback. A decentralized timestamp (blockchain anchor) is **deliberately out
   of scope** — it adds a chain dependency that fights the sovereignty ethos.
3. **Sybil resistance — now higher-stakes with money.** Ed25519 identities are free to mint; an
   attacker can spin up fake agents to farm attestations, fake-sell to themselves to inflate trust,
   or wash-trade royalty edges. Direct-experience-over-hearsay (§16) and user-gated trust elevation
   mitigate the *trust* side, and the platform-of-record rail's KYC raises the cost of fake *sellers*
   — but a determined adversary remains a real threat. **Open research** — proof-of-work identity,
   web-of-trust depth limits, seller-KYC gating, and stake are candidates with real downsides.
4. **Marketplace fraud, refunds & disputes.** Priced exchange brings chargebacks, "paid but content
   never delivered," and "delivered but not as described." Escrow/hold-then-capture (§15.5) and
   signed receipts help, but dispute *resolution* across sovereign nodes with no central arbiter is
   genuinely unsolved. Leaning on the rail's chargeback machinery covers fiat disputes; the *content
   delivery* dispute is **open**. v6 should ship buyer-protection conservatively (capture only after
   verified delivery) and treat disputes as trust-damaging events.
5. **Cross-node First Law (§17.2).** The policy is stated; the mechanism for acting on cross-node
   harm signals without overreach is unresolved and ethically delicate. **Open.**
6. **Spam / abuse on directories & listings.** Open directories and a priced marketplace invite
   listing spam and scam listings; needs rate-limiting, proof-of-work on registration, seller
   reputation gating, or stake. **Open.**
7. **Token economics / monetary policy (§15.10).** A fixed annual emission with no cap is *perpetual
   linear inflation* — intentional ("use it, don't hoard it"), but it shapes ψ purchasing power over
   time and interacts with the off-ramp choice. Whether to add a burn sink, a soft cap, or tie
   emission `E` to network size is a **monetary-design decision** with no purely-technical answer.
   The mechanism is buildable; getting the *parameters* right (emission size, channel split,
   founding-cohort N, multiplier, Negatron decay) is iterative and partly empirical. **Design-open,
   not blocked.**
8. **Positron/Negatron parameters (§15.9).** The *model* is decided (D, net-charge); what remains
   empirical/tunable is the Negatron decay half-life, the burn ratio `k`, and whether η is
   system-minted-only (recommended) vs. transferable. These want iteration in a testnet, not an
   up-front answer.

### 22.4 Deliberate non-goals (so scope stays honest)
- **No fund custody.** Money moves through a third-party platform-of-record rail (Stripe Connect);
  Friday orchestrates and records receipts but never holds, transmits, or escrows funds itself. This
  is the line that keeps Friday out of money-transmitter licensing. *Payment processing is in scope
  (Phase E); becoming the bank is not.*
- No blockchain or on-chain anchoring. The Positron (ψ) is a **closed-ledger platform credit**
  recorded in Friday's existing hash-chained ledger — deliberately *not* a crypto token: no chain,
  no mining, no proof-of-work, no on-chain settlement (§15.8, §15.11). "Earned by creation, never by
  burning electricity."
- No global consensus — trust is *local and subjective* by design; two agents may rationally
  disagree about a third, and that's correct.
- No central authority that can revoke an identity or assert ownership over user creations.
- No account-wide license/price default — licensing is always a per-piece, user-made choice (§15.4).

---

## 23. Security & privacy invariants (must hold across all phases)

1. **The signing key never leaves the device.** Federation uses it to *sign*, never *transmits* it.
2. **No plaintext federation traffic, ever (§13).** All agent-to-agent bytes after the handshake are
   end-to-end encrypted (Noise-XX, ChaCha20-Poly1305) with **forward secrecy**; downgrade-to-plaintext
   is treated as hostile and dropped. This holds even for "public" data — confidentiality on the wire
   is unconditional.
3. **TIER_2/3 vault data never crosses the federation boundary.** The federation pre-hook runs the
   existing `vault_access` classification; sensitive data is denied egress, same as it's denied to
   cloud providers today.
4. **Provenance verification reveals nothing private** — it returns sig/hash/chain booleans, not the
   content or the user's other works.
5. **cLaws gates fail closed** (critical hooks) in both content and federation paths.
6. **Default-private:** nothing is listable, free-shared, priced, or transferable until the user
   makes an explicit per-piece choice (§15.4). Absent a choice, a creation is all-rights-reserved
   and invisible to the network.
7. **No autonomous overspend, ever.** An agent purchase that would exceed `per_item_max`, the
   daily/monthly budget, or fall outside allowed categories is denied by a fail-closed critical hook
   — never "approximately" enforced. Pricing a creation is user-only; the agent may suggest, never set.
8. **Friday custodies no fiat.** ψ balances are ledger *credits* (signed entries Friday records);
   any actual money sits with the platform-of-record rail at the edges (§15.11). Friday never holds,
   transmits, or escrows fiat — it moves ψ on its own ledger and orchestrates the rail for fiat.
9. **Every federation + marketplace event is logged** to an append-only, hash-chained audit (the
   existing ledger/decision-BOM pattern) so the user can see exactly what their agent bought, sold,
   shared, or verified autonomously.
10. **No proof-of-work, no mining, no global consensus** (§0 *Green by design*). A ψ transaction is a
    signed ledger append plus one encrypted message — energy scales with real activity, not network
    size. Any future change must preserve this property.

---

## 24. Owner decisions & remaining open questions

### 24.1 Resolved by owner (2026-06-26)
- **No account-wide default license.** Licensing is asked **per piece, at creation** (§15.4), with
  clear options: all-rights-reserved, CC-BY, CC-BY-SA, CC0/public-domain, or priced. The system
  ships no implicit default. *(Spec updated throughout: §7.1, §10, §15.4, §23.5.)*
- **Full marketplace, two layers.** The federation carries **both** a free/open commons (agents
  shop entertainment, tools, content freely for their users) **and** a priced commercial layer
  (user-set prices, agent-mediated buy/sell within user budgets). *(New §15 in full; §18 autonomy;
  Phases D & E.)*
- **Agent-mediated commerce within budgets.** Agents may transact on the user's behalf, bounded by
  `marketplace/policy.json` (per-item, daily, monthly caps; allowed categories; approval threshold),
  enforced by a fail-closed critical hook on the existing `cost_meter`. *(§15.5, §18, §23.6.)*
- **Native currency named.** The federation unit is the **Positron (ψ)**, sub-unit **milliPositron
  (mψ)**. It is a closed-ledger credit, not a blockchain token. *(New §15.8–§15.11; §22.4.)*
- **Negatron symbol = η (eta).** Positrons = **ψ**, Negatrons = **η**; net charge `Q = Σψ − Ση`.
  *(§15.9 throughout.)*
- **Dual-charge model = D (net-charge), adopted.** Negatrons are a system-minted risk charge; net
  charge `Q` gates autonomy bands and folds the economy and trust layers into one scalar. *(§15.9,
  §16, §17.)*
- **ψ is fully convertible to fiat (off-ramp option c).** Build the ledger + bidirectional exchange
  now; the legal wrapper (MSB licensing, securities counsel, KYC/AML) follows at scale and gates
  *opening the off-ramp broadly*, not the build. *(§15.11, §22.2 #4, Phase E.)*
- **All federation traffic is end-to-end encrypted.** Not just the handshake — every subsequent
  message rides the Noise-XX channel with forward secrecy; no plaintext fallback. *(§13, §23.2.)*
- **Green by design is a first-class principle.** No proof-of-work, no mining, no global consensus; a
  ψ transaction costs ~one email's worth of energy. *(§0 principle + callout, §15.10, §22.4, §23.10.)*

### 24.2 Still open for sign-off

**Positron / Negatron economics (the part flagged for discussion):**
1. **Dual-charge interpretation** (§15.9): **DECIDED — D, net-charge** (`Q = Σψ − Ση`), composing
   with A's bookkeeping and C's settlement-funded curation reward. Open: decay/burn parameters (see
   §22.3 #8), best settled in a testnet.
2. **ψ off-ramp policy** (§15.11): **DECIDED — (c) full convertibility.** Open sub-item: the
   *sequencing/jurisdiction plan* for the legal wrapper at scale (which markets open first, MSB
   licensing path) — §22.2 #4.
3. **Emission parameters** (§15.10): annual `E`, the 50/20/20/10 channel split, **Founding-Node
   cohort size N** and multiplier, and whether to add a burn sink / soft cap vs. accept perpetual
   inflation.
4. **Negatron specifics** (§15.9): symbol **decided — η**; still open: **system-minted only**
   (recommended) vs. transferable; decay half-life and burn ratio `k`.

**Marketplace & rails:**
5. **Payment rail & platform take-rate** — Stripe Connect (Express vs. Custom); platform fee on
   priced sales? Legal/tax review required before Phase E.
6. **Seller onboarding / KYC threshold** — KYC from first sale, or only above a payout threshold?
7. **Refund & delivery-dispute policy** (§22.3 #4) — capture-after-verified-delivery default?
8. **Default buying-budget posture** — ship with buying **disabled** (recommended, opt-in) or a
   small starter budget enabled?

**Federation & provenance:**
9. **Does FutureSpeak.AI run a default directory node?** Convenient, but a soft centralization.
10. **Daily-creation cost ceiling default** (generation/inference spend per day, via `cost_meter` —
    a real compute cost, not ψ) for free-choice daily creation.
11. **How aggressive is auto-trust?** Auto-promote on sustained good behavior, or user-only?
12. **C2PA strict conformance** — pursue ecosystem interop, or stay with lighter signed JSON-LD?

**Moderation & harm floor (from §25 — philosophy DECIDED: measure harm, not taste):**
13. **Harm-floor exhaustiveness (§25.3).** Decided floor = H1 CSAM · H2 real-person deepfakes · H3
    targeted harassment/doxxing · H4 violence facilitation against specific targets. **Open: does the
    floor include *anything not tied to a specific real person*?** The legacy
    `check_content_safety()` had a "WMD construction instructions" rule (mass, not person-specific
    harm). Stephen's stated line is harm-to-real-people; **keep WMD/illegal-instruction blocking as a
    narrow legal-compliance carve-out, or drop it to stay strictly person-harm?** Flagged explicitly
    rather than silently kept or dropped.
14. **Floor governance & anti-creep (§25.12).** When/how does floor amendment move to a federated
    convention — and how is the floor made **hard to *expand*** so it can't drift from harm into
    taste under pressure?
15. **Minor protection mechanism (§25.4a).** How is an account established as adult vs. minor
    (self-declare · guardian-set · light verification)? This is the one place "openness" meets "but
    not for kids," and it needs a real, privacy-preserving answer.
16. **Decentralized recommendation (§26.9).** The viewer-aligned, on-device feed/ranking is genuinely
    new engineering — what's the v1 (interest tags + curation signal + trust) before anything fancier?
17. **Juror KYC (§25.9).** Lightweight identity/uniqueness for juror & seller eligibility — yes/no,
    at what tier?
18. **Report stake size & slash ratio (§25.8); jury size N and quorum fraction (§25.6).** Testnet-tuned.

**Local-first (from §27):**
19. **Default `creative_engine` mode** — ship `auto` (local if no GPU-cloud key) vs explicit `local`?
20. **Local QA-vision scorer** — bundle a small VLM (moondream/Qwen2-VL) for image scoring, or rely
    on take-comparison + graceful-skip at Tier 0?

*(Items 5–8, 11–12 carry over from earlier revisions; 1–4, 9–10, 13–20 are new or reframed.)*

---

# PART II — PRODUCT STRATEGY

The sections above specify *what to build*. The three below specify *how it survives contact with
the world*: how the network polices itself, why anyone joins, and how it runs without a cloud
umbilical. Stephen's framing: **build the whole thing first, then bring people in — take the world by
storm with a complete product.** These sections are written to that bar.

---

## 25. Federation moderation protocol

> ### The governing philosophy: measure harm, not taste
>
> **Friday is a maximally open creative platform. The one and only bright line is *measurable harm to
> real people*.** Friday does not rate art, police aesthetics, or arbitrate morality. The question the
> protocol asks is **never** "is this offensive / distasteful / controversial?" — it is **only**
> "does this content harm a specific real person?"
>
> - **Refused (measurable harm to real people):** CSAM (absolute, no exceptions); deepfakes of real
>   identifiable people (sexual *or* otherwise); doxxing and harassment targeting specific
>   individuals; content designed to facilitate real-world violence against specific targets. **That
>   is the entire list.**
> - **Created (everything else, including things some people dislike):** adult content / porn (as long
>   as it does not depict real, identifiable people without consent); controversial, political, dark,
>   or transgressive art; anything a viewer might find offensive but that harms no one. The platform
>   does **not** decide what art is "acceptable."
> - **Controlled by the viewer, not the platform:** users control what *they* see via tagging and feed
>   filters; minors get age-appropriate defaults and parental controls. **No platform-wide censorship
>   of legal content that causes no measurable harm.**
>
> This is the explicit ambition: **the open content platform that eventually replaces YouTube** —
> succeeding the way YouTube did, by being open to almost everything, only with a *sharper, narrower,
> harm-based* line and without a central censor. The diversity of content is a **feature**, not a
> problem to be managed.

A federation that can generate and trade media at that scale **will** include content that some find
objectionable — and a small amount that genuinely harms real people. The protocol's job is to stop
the latter **without touching the former**. The answer cannot be "a central censor," because that
breaks the sovereignty ethos, becomes the single point every bad actor (and every government)
attacks, and inevitably slides from blocking *harm* into policing *taste*. The design is a **hybrid:
a narrow harm-floor enforced locally and pre-emptively, plus a distributed, evidence-based
adjudication process for the harm-floor violations that slip through or are deliberately produced.**

### 25.1 Three layers of moderation

```
   ┌─ LAYER C: NETWORK ADJUDICATION ──────────────────────────────────────────┐
   │   distributed jury · acts ONLY on HARM-FLOOR violations (harm to real      │
   │   people) · graduated enforcement up to ejection · NEVER on controversial  │
   │   or merely-offensive content · the subject of most of this section        │
   ├─ LAYER B: VIEWER & HOST PREFERENCE (no censorship of others) ─────────────┤
   │   the USER filters what THEY see (tags, feed prefs, minor/parental         │
   │   defaults); a node may choose what IT hosts/relays. Sovereign preference, │
   │   NOT a verdict on the content. Nobody's choice here removes anyone else's │
   │   right to create or to see. This is where "I'd rather not see that"       │
   │   lives — it never escalates and never touches the creator.                │
   ├─ LAYER A: HARM-GATE AT SOURCE (every node, pre-emptive) ─────────────────┤
   │   check_content_safety() blocks ONLY harm-to-real-people at gen/send/accept │
   │   (§17). It does NOT block adult, dark, or controversial content. The       │
   │   cheapest layer — stops genuine harm before it exists, touches nothing else.│
   └───────────────────────────────────────────────────────────────────────────┘
```

The ordering is deliberate and the division of labor is the whole point: **Layer A stops genuine
harm** (a tiny, sharply-defined set), **Layer B lets each viewer curate their own experience without
imposing it on anyone else** (this is where nearly all "I don't want to see that" is resolved — by
*filtering*, never by *removal*), and **Layer C** handles only deliberate, cross-node *harm-floor*
abuse. Centralized moderation collapses all three into one chokepoint and inevitably drifts from
harm into taste; this design keeps harm-prevention and preference-curation as **separate systems** so
the platform can be maximally open *and* safe at the same time.

### 25.2 Why distributed, not centralized (and not pure-local)

- **Pure-central** (one moderator) = censorship chokepoint, legal target, sovereignty violation,
  doesn't scale, single corruptible party. Rejected.
- **Pure-local** (every node alone) = no recourse when node X *trades* CSAM to node Y; harm crosses
  nodes and no one can act collectively. Insufficient.
- **Hybrid (chosen):** local enforcement handles volume; a distributed jury handles cross-node
  floor-violations with cryptographic evidence and graduated, appealable enforcement. The
  *constitution* (cLaws) is shared and signed; the *adjudication* is decentralized.

### 25.3 The harm floor — the *only* thing that is moderatable

**Network-level moderation may act ONLY on the harm floor.** The floor is defined by harm to real
people, and it is short, closed, and exhaustively enumerated:

```
   THE HARM FLOOR  (network-enforced, ejectable — and nothing else is)
   ─────────────────────────────────────────────────────────────────────
   H1  CSAM — absolute hard line, NO exceptions.
   H2  Deepfakes of real, identifiable people — sexual OR otherwise —
       made/shared without consent (non-consensual intimate imagery of a
       real person is a subset of this).
   H3  Doxxing & targeted harassment of specific real individuals.
   H4  Content designed to facilitate real-world violence against specific
       targets.
   ─────────────────────────────────────────────────────────────────────
   That is the complete list. The common thread is a SPECIFIC REAL PERSON
   being harmed. No item here is about taste, offense, or morality.
```

**Everything else is explicitly permitted** and is **never** a moderation matter — not at the
network, not at the harm-gate:
- **Adult content / pornography** — permitted, provided it does not depict a real, identifiable
  person without consent (i.e., not H2). Fictional/synthetic adult content is fine.
- **Controversial, political, dark, transgressive, or offensive art** — permitted. "Some people are
  offended" is **not** harm and is **not** a basis for any action.
- **Anything in "poor taste"** — not Friday's judgment to make.

> This is a real change from the legacy `creative_engine.check_content_safety()` rules, which mixed
> harm with taste (e.g. a blanket "non-consensual sexual content" rule that would catch *fictional*
> scenarios, and a "graphic real-world gore" rule that would catch dark *art*). **The gate must be
> re-scoped to the H1–H4 harm floor**: it blocks harm to real people and **passes** fictional adult,
> violent, or controversial content. Re-scoping `check_content_safety()` is a Phase A/F task
> (§27.8 already notes that, post local-first, this gate is the *only* pre-emptive filter, so getting
> its scope exactly right — narrow and harm-based — matters more than ever).

The handshake `claws_hash` check (§13.2) still ensures a node running a *weakened* harm floor (one
that fails to block H1–H4) is auto-flagged. Note the asymmetry: the floor is enforced against
*under*-blocking (letting real harm through), **never** against *over*-permissiveness toward
controversial-but-harmless content — a node cannot be sanctioned for being *more* open within the
harm floor, because openness is the design goal.

Amending the floor changes `CLAWS_TEXT` → changes `claws_hash` → every node re-attests. Today that
amendment is governance-key-signed (HMAC, the existing mechanism); §25.12 addresses moving it toward
a federated process. Any proposed *expansion* of the floor beyond harm-to-real-people should be
treated as a constitutional change requiring the highest bar — the floor is meant to stay narrow.

### 25.4 Content classification — harm vs. labels (not a quality ladder)

The taxonomy is **two-axis, not a ladder of acceptability.** One axis is the binary harm gate (H1–H4,
§25.3). The orthogonal axis is *descriptive labels* that exist purely so **viewers can filter their
own feed** — labels carry **no** judgment and trigger **no** enforcement.

| Class | What | Action | Who decides |
|---|---|---|---|
| **HARMFUL (H1–H4)** | Harm to a specific real person (§25.3) | **Blocked at source + ejectable.** The only enforced class. | Harm-floor governance (FutureSpeak now → federated convention later, §25.12) |
| **TAGGED (descriptive)** | Adult/NSFW, graphic violence, sensitive themes — *legal, harmless, permitted* | **Never blocked.** Signed content labels on the manifest so **users filter their own feed**; default-on for minors (§25.4a). | Creator self-tags; auto-tagger assists; community can suggest corrections |
| **OPEN (default)** | Everything else | No action of any kind | — |

The labels are a *content-rating-for-filtering* vocabulary (think film/TV descriptors), **not** a
permission system. A piece tagged `adult` or `graphic-violence` is fully permitted; the tag only lets
a viewer who doesn't want it never see it. **Mis-tagging that causes a minor to see adult content** is
itself a (minor-protection) harm and is handled by the labeling-integrity rules below; **the
existence of adult content is not.**

#### 25.4a User-side content controls (the viewer is in charge)

Every user controls **what they see**, and *only* what they see — never what others may create:

- **Content tags** — `nsfw`, `adult`, `graphic-violence`, `sensitive`, etc., as signed manifest
  labels. Creator self-tags; a local auto-tagger (a small vision/text classifier) assists and can
  *raise* a tag it's confident about (you can't silently strip a tag to evade minor-protection).
- **Personal feed filters** — each user sets what is shown/blurred/hidden in their own discovery feed.
  This is pure client-side preference; it changes *their* view, nothing global.
- **Age-appropriate defaults for minors** — a minor account ships with adult/graphic tags filtered
  **on** by default; opening them requires the account not be a minor.
- **Parental controls** — a guardian can lock a minor's filter settings.
- **No global removal.** None of these controls remove content from the network or sanction a
  creator. Filtering ≠ moderation. The only thing that removes content from circulation is a **harm**
  (H1–H4) finding.

**Labeling integrity** is the one place tags interact with enforcement, and even then narrowly: the
*harm* of exposing minors to adult content is addressed by **requiring honest tagging of adult/graphic
material**, not by restricting the material. Deliberately mis-tagging adult content as
child-appropriate to reach minors is a minor-protection harm (an H-class matter); choosing to publish
adult content *correctly tagged* is fully fine.

This taxonomy is a signed, versioned artifact distributed over the federation. The bright line —
**HARMFUL is enforced, everything TAGGED/OPEN is the viewer's choice** — is what keeps the platform
maximally open while still protecting real people and minors.

### 25.5 Evidence: moderation rides on provenance (Layer 2's quiet superpower)

Because **every artifact is provenance-signed by its creator's own key** (§7), a moderation report is
not "he-said-she-said" — it is **cryptographic**:

```jsonc
{
  "type": "ModerationReport", "version": "1.0",
  "reporter": "<agent_id>",
  "accused": "<agent_id>",
  "violation": "harm.csam",                        // a harm-floor category (H1–H4), never a descriptive tag
  "evidence": {
     "manifest": { ...the accused's OWN signed ContentCredential... },
     "content_hash": "sha256:...",                // hash of the violating bytes
     "transfer_receipt": { ...signed ContentTransfer proving they SOLD/SHARED it... }
  },
  "stake": 250,                                    // mψ staked against this report (§25.8)
  "signature": {"alg":"ed25519","value":"..."}
}
```

The accused's **own signature** on the manifest proves authorship; the transfer receipt proves they
moved it. **You cannot frame someone for content they never signed** — the strongest anti-abuse
property in the protocol, and it's free because Layer 2 already exists. Reports travel over the
encrypted channel (§13); every moderation action is itself a signed, hash-chained, auditable event.

### 25.6 Who ejects, and how — sortition jury

No single party ejects. Adjudication is by a **sortition jury**: a randomly-selected panel of
**high-net-charge nodes** (`Q ≫ 0`, §15.9), drawn per-case so no clique can pack it.

```
   report filed (+stake)
        │
        ▼
   AUTOMATED PRE-CHECK ── evidence signatures verify? violation is a harm-floor (H1–H4) category?
        │  fail → report rejected, reporter's stake slashed (§25.8)
        ▼  pass
   SORTITION ── seed = hash(report_id ‖ recent ledger root); pick N jurors from the
        │       high-Q eligible set (deterministic, publicly verifiable, unpackable)
        ▼
   JURY REVIEW ── each juror independently verifies evidence, votes signed
        │
        ▼
   QUORUM ── supermajority (e.g. ≥⌈2/3⌉) required to act; ties/short → no action
        │
        ▼
   GRADUATED ENFORCEMENT (§25.7)
```

Jurors are paid a small ψ fee from the curation/governance emission channel (§15.10) for honest
participation — moderation is *work*, and the economy funds it.

### 25.7 Graduated enforcement (maps onto net charge & trust bands)

Enforcement is not binary; it escalates, and it reuses the §15.9 net-charge / §16 band machinery so
it is **mechanical, not manual**:

```
   WARNING            → η minted (small); notification; no capability loss.       (Q dips, stays >0)
   RESTRICTED         → more η; budgets shrink, can't auto-sell, listings         (Q < 0 band)
     AUTONOMY           de-ranked. Automatic consequence of net charge.
   QUARANTINE         → read-only federation: may still answer provenance-        (Q ≪ 0 band)
                        verification (the public good) but cannot trade, list,
                        or initiate. Severe η load.
   EJECTION           → honest peers drop the node from agent_trust_graph;
                        directories delist; its listings purged from discovery.
```

The first three tiers are *already* what the net-charge model does automatically — moderation
"sentencing" is largely **minting the appropriate η and letting the bands do the rest.** Only
ejection is a discrete network act.

### 25.8 Preventing weaponized moderation (coordinated false reports)

Four compounding defenses, so that attacking the moderation system is self-punishing:

1. **Cryptographic evidence requirement (§25.5).** Can't fabricate — needs the target's own
   signature. Defeats the most common abuse outright.
2. **Stake + slashing.** Filing a report stakes ψ; a rejected/failed report **slashes the stake and
   mints η on the reporter.** Frivolous or coordinated false-reporting is directly costly.
3. **Sortition jury (§25.6).** Randomly drawn from high-Q nodes per-case — a brigade can't seat
   itself as the panel.
4. **Diversity + supermajority quorum.** Ejection needs a ≥2/3 vote of a diverse panel, not a simple
   majority of whoever shows up.

A coordinated false-report campaign therefore must: forge cryptographic evidence (impossible without
the target's key), burn real staked ψ, *and* win a supermajority of a randomly-drawn high-trust jury.
The attack costs more than it can gain.

### 25.9 Sybil resistance (one actor, many nodes, to game votes)

The honest position (already flagged §22.3): **Sybil resistance is never solved, only made
expensive.** This design makes the expensive thing *be the behavior we want anyway*:

- **Standing is earned, not minted.** A fresh node is `Q ≈ 0`: no trust, no founding bonus, no
  uptime, **not jury-eligible.** Spinning up 1,000 nodes yields 1,000 powerless identities, not 1,000
  jurors. Jury eligibility requires *high* net charge, which takes real time + real, curated creation.
- **Proof-of-Creation cost.** Earning standing means making verifiable, curation-validated work; the
  curation gate (§15.10) filters spam, so you can't farm standing cheaply.
- **Founding cohort is fixed and closed** — un-Sybillable by construction.
- **KYC-gated high-trust tier (optional, recommended for jurors/sellers).** With full convertibility
  live (§15.11), the rail's KYC already binds *sellers* to real-world identity. Extending a
  lightweight KYC/uniqueness check to **juror eligibility** (not to entry — entry stays
  permissionless and pseudonymous) is the strongest Sybil defense for the operations that matter,
  while preserving anonymous participation everywhere else. **Scope KYC to privilege, never to
  presence.**

### 25.10 What happens to content & Positrons on ejection

- **Provenance is immutable.** Already-issued manifests and license grants stay cryptographically
  valid — you cannot un-sign history. Buyers of an ejected node's earlier *licit* content keep their
  rights. The node's **listings**, however, are purged from discovery and honest nodes won't trade
  with it going forward.
- **Ill-gotten ψ is annihilated, not "confiscated."** Ejection comes with a large η mint; net charge
  goes deeply negative, which **burns the reputational/spendable value** earned through the violation
  (matter/antimatter, §15.9). Friday does not reach into the device to seize a balance (sovereignty);
  it makes that balance **untransactable on the honest network**.
- **The fiat off-ramp is the real-world chokepoint.** Under full convertibility (§15.11), an ejected
  bad actor trying to cash out fraud proceeds hits the **platform-of-record rail's** fraud/AML
  controls — Stripe can freeze a payout where Friday (by design) holds no funds to freeze. The
  sovereign ledger and the regulated rail each do the part they're suited for.
- **Ejection is soft, and that is correct.** Federation is voluntary association. "Ejection" means
  *the honest network refuses to peer, trade, or vouch* — not that anyone deletes the user's
  sovereign install. A bad actor can always start a new `Q ≈ 0` identity; what they cannot do is
  carry over the trust, Positrons-as-reputation, founding status, and uptime history that took real
  time to build. **The cost of ejection is the forfeited reputation, and that cost is real.**

### 25.11 Appeals

- A wrongly-enforced node files a **signed appeal**, reviewed by a **fresh sortition jury** (disjoint
  from the original panel — different random seed — to defeat a biased first panel).
- Because evidence is cryptographic, most appeals resolve cleanly: *did the manifest actually verify
  against the accused's key, and is the category actually a harm-floor (H1–H4) violation?* If the original report's evidence
  fails to verify, the enforcement is reversed **and the original reporter is penalized** (stake
  slashed, η minted).
- **Reinstatement is real but not instant-to-prior-standing.** A successful appeal restores peering
  and clears the wrongful η, but trust/net-charge rebuilds from a penalized baseline rather than
  snapping back to PARTNER — so the process can't be abused to launder a brief ejection into a
  reputation reset.
- All appeal proceedings are signed and hash-chained (§23) — the moderation record is itself
  auditable, which is the ultimate check on the moderators.

### 25.12 Honest limits

- **Detection of *novel* harm at the floor still depends on cLaws/classifier quality.** With
  local-first generation (§27), there is *no provider-side filter* — Friday's own `check_content_safety`
  becomes the only pre-emptive gate, so it must be robust and updatable. This is a real
  responsibility shift, called out again in §27.8.
- **Governance centralization, today.** The harm floor is presently authored by FutureSpeak.AI. The
  *aspiration* is a federated constitutional-amendment process (a high-quorum convention of long-
  standing high-Q nodes) as the network matures. We state this honestly rather than pretending the
  floor is already decentralized. The risk to guard against is **floor creep** — pressure (internal
  or external/regulatory) to expand the floor from *harm to real people* into *taste/morality*. The
  governance process should make the floor **hard to expand**, by design. **Open governance question
  (→ §24).**
- **Jury latency.** Distributed adjudication is slower than a central button. Mitigated by Layer A
  stopping genuine harm pre-emptively and by automatic net-charge restriction kicking in *before* a
  formal ejection vote completes.
- **The "harm to a real person" judgment is occasionally genuinely hard** (satire vs. harassment; a
  public figure in political art vs. a targeted deepfake; newsworthy real imagery vs. doxxing). The
  jury exists precisely for these edge cases; the default on a *close* call is to favor expression
  (do not eject) while still honoring removal requests from the *real person depicted* — the person
  harmed has standing the offended bystander does not.

---

## 26. Network-effects game plan

### 26.1 The cold-start escape hatch: Friday is valuable at N = 1

Most networked products die at the cold start because they are **worthless until others join**.
Friday is not one of them. **At a single user it is already a complete, sovereign AI desktop** —
voice, agentic tasks, and (with §27) a full local creative studio. Federation is **upside, not table
stakes.**

> So "why does person #2 install Friday?" has the same answer as person #1: *they want a sovereign AI
> that makes things and works for them.* The network is additive. This is the **single-player-first**
> strategy that worked for Figma, Notion, and early Dropbox — be a 10/10 tool alone, become
> irreplaceable together. Friday already clears the single-player bar; this spec makes the multiplayer
> upside real.

### 26.2 Value at each scale tier

| Scale | What it is | New value unlocked | The "aha" |
|---|---|---|---|
| **1** | Sovereign AI desktop | voice · agent · local creation · privacy · ownership | "It made a whole scored short, locally, and it's *mine*." |
| **10** | A creative collective / crew | share + remix free-commons works; shared Series Bibles | "I remixed my friend's track and the credit chain *just worked*." |
| **100** | A browsable marketplace | enough inventory to discover; first real ψ trades | "I bought a stranger's tool for 2 ψ and it was exactly what I needed." |
| **1,000** | Network effects engage | curation becomes valuable labor; reputation means something; commons is a real resource | "My curation picks paid me back in ψ." |
| **10,000** | Self-sustaining economy | ψ has velocity; creators earn real income; off-ramp matters | "I made meaningful money this month from things I created." |
| **100,000+** | A cultural platform | Friday-native formats, a creator middle class, ψ has a real exchange rate | "There's a whole scene here, and I'm known in it." |

The crucial property: **each tier is valuable on its own, and each unlocks the next** — there is no
valley where the product is useless-until-critical-mass, because tier 1 already stands alone.

### 26.3 Beachhead: who first, and why

**Opinionated pick: local-AI / indie-creator communities** — the r/LocalLLaMA, Stable-Diffusion,
ComfyUI, indie-game-dev, generative-art crowd. They are the wedge because:

- **They already run local models**, so the local-first creative engine (§27) is a *headline
  feature*, not a compromise. "Zero API keys, runs on your GPU, you own the output" is their love
  language.
- **They already care about ownership and provenance** — burned by NFTs but still wanting a
  creator economy done right. Layer 2 is their unmet need, minus the crypto baggage.
- **They are loud, networked, and evangelical** about tools that respect them. Cheap, fast virality.
- **They produce content**, so they seed the commons and marketplace from day one — solving the
  supply side of the two-sided market before demand exists.
- **They are allergic to walled gardens** — the sovereignty ethos *is* their politics.

Adjacent expansion after the beachhead: **TTRPG / worldbuilding communities** (the Series Bible +
creative pipeline is uncannily good for campaigns and lore), then **small creative studios &
agencies** (multi-brand kits, §5). **Deliberately not first:** general consumers (need the network to
pre-exist) and enterprise (long cycles, wrong early ethos fit).

### 26.4 The aha moment(s) that make someone tell a friend

Two, sequenced:
1. **Single-player aha (drives installs):** *"I described a 15-second film and Friday made the whole
   thing — script, shots, video, score — locally, no cloud, and it signed it as mine."* Creation
   magic + ownership, on your own machine.
2. **Multiplayer aha (drives federation):** *"I remixed someone's clip and the attribution and the
   payment split happened automatically and correctly."* The thing every prior creator platform
   promised and fumbled.

### 26.5 Viral mechanics — what spreads on its own

- **Signed content is the ad.** Every shared Friday creation carries a verifiable "made with Friday,
  owned by X" credential (§7). Content *is* the growth loop — like "Sent from my iPhone," but
  cryptographic and ownership-affirming.
- **Peer cards / QR (§12.1)** make adding a creator a social gesture.
- **Remix chains pull inward.** Remixing links you to the source creator's node; collaborative works
  *require* both nodes to sign — every collaboration is a recruitment.
- **The commons is a public good** — free, useful content draws people in; once in, they create.
- **Curation is monetized evangelism** — surfacing good work earns ψ (§15.10), so telling people
  about great content literally pays.
- **Founding-node scarcity (§15.10)** — a permanent early-adopter multiplier is a natural "get in
  early and bring your people" engine, filled by the beachhead community.

### 26.6 Retention after the novelty

- **Your work and your standing live here.** Series Bibles, projects, owned creations, accrued ψ, and
  reputation/net-charge are durable, compounding assets. (Not lock-in — the data is *yours* and
  exportable — but your *economic standing* is earned here.)
- **Income retains.** Once a creator earns meaningful ψ, leaving means leaving money on the table.
- **The agent gets better at *you*** — memory, user profile, preferences compound (existing
  capability). Sovereign personalization is sticky in the good way.
- **Daily creation (§6)** is an ambient daily-open habit.
- **The single-player utility is always there** — even a user who never federates keeps a great AI
  desktop, so churn-to-zero is rare.

### 26.7 Competitive moats by stage

| Stage | Moat |
|---|---|
| **N = 1** | Sovereignty + local-first + the trust stack. Cloud incumbents *can't copy it without abandoning the data-monetization business model that funds them.* |
| **N = 10–100** | Provenance/ownership done right — what Web3 promised and fumbled, minus the crypto baggage. A growing graph of signed content. |
| **N = 1k–10k** | The reputation graph + two-sided liquidity. A creator's accrued ψ, trust, and attribution chains are **non-portable to any competitor.** |
| **N = 100k+** | Cultural lock-in + Positron velocity + the largest provenance graph → **standards gravity**: Friday's content credential becomes *the* creator-ownership format. |
| **Cross-cutting** | The cLaws/governance brand: "the AI platform that provably can't screw you." Trust is the durable moat. |

### 26.8 Honest risks

- **Two-sided chicken-and-egg** (creators ↔ buyers). Mitigated by single-player value + a free
  commons that seeds supply before demand exists — but it is the classic marketplace risk and must be
  watched.
- **Local-first quality gap (§27), especially video**, could blunt the single-player aha for no-GPU
  users — mitigated by the montage fallback and the cloud premium tier, but real.
- **Regulatory drag on the off-ramp (§15.11)** could slow the economy tiers (10k+), even as the
  creative product races ahead.
- **Moderation load scales with success** — §25 must be operational *before* the marketplace is busy,
  not bolted on after.

### 26.9 The content layer: a YouTube-scale, maximally-open platform

The stated ambition is blunt: **build the platform that eventually replaces YouTube.** YouTube won by
being open to *almost everything*; the federation aims to be **more open, with a sharper line** — no
central censor, the only floor being measurable harm to real people (§25), and the creator owning and
optionally monetizing every piece (Layers 2–3). Diversity of content is the **feature.**

At YouTube scale, *discovery* — not creation or storage — is the hard problem. The content layer the
federation needs, mapped onto what this spec already builds:

- **Channels** — a creator's node *is* their channel: a signed, followable catalog of their public
  works (the §15.2 listings surface, extended with free works). Following a channel = a standing
  peering + content-pull relationship (autonomous within §18).
- **Subscriptions** — follow a channel for its new drops (free) or subscribe for paid/exclusive works
  (a recurring ψ grant within the buyer's budget, §15.5). Subscriptions are just standing licensing
  agreements over the existing rails.
- **Discovery & recommendation — decentralized and sovereign.** No central ranking algorithm
  optimizing for engagement-at-all-costs (the thing that made YouTube's openness *also* a radicaliz-
  ation engine). Instead: **curation as paid labor** (§15.10) surfaces good work; each user's feed is
  ranked by *their own* agent against *their own* stated interests and filters (§25.4a), not by a
  platform maximizing watch-time. Trust/reputation (§16) and provenance (§7) are ranking inputs you
  can actually inspect.
- **Feeds are viewer-curated, not platform-curated.** This is the deep difference from YouTube:
  ranking serves the *viewer's* goals, runs on the *viewer's* device, and is transparent — which is
  also why the platform can be maximally open without an algorithm amplifying the worst of it.
- **Tags drive the feed, not removal** (§25.4a) — the same labels that let a viewer avoid adult/graphic
  content also power positive discovery ("more like this").

This reframes the marketplace (§15) as the *commerce view* of a broader **content network**: the free
commons + channels + subscriptions is the "watch" experience; priced listings are the "buy"
experience; both ride the same provenance, trust, encryption, and harm-floor machinery. The
recommendation layer (decentralized, on-device, viewer-aligned) is genuinely new engineering and is
called out as such (→ Phase F+/§24); everything beneath it already exists in this spec.

---

## 27. Open-source / local-first creative engine

### 27.1 The ethos, stated as a requirement

**Friday must produce every media type with zero cloud API keys, using only local/open models.
Cloud (Gemini / Veo / Lyria) is the *premium upgrade*, never the requirement.** This mirrors the
voice architecture exactly — local Tier-1 (faster-whisper + Piper) is the default, Gemini Live is
opt-in, NeMo GPU is the premium tier. We extend that same tiering to **image, video, and music.**

This is not a compromise feature; for the beachhead community (§26.3) it is *the* feature.

### 27.2 Architecture: provider-abstract the creative engine

The mechanism already exists — `capability_router` + `provider_registry` already resolve `voice` to
local-or-cloud. We add the same for creation:

```
   capability_router resolves:
     creative_image  → local (SDXL/FLUX) | cloud (Nano Banana)   per settings
     creative_video  → local (LTX/CogVideoX) | montage | cloud (Veo)
     creative_music  → local (MusicGen/Stable Audio) | cloud (Lyria)
   setting:  creative_engine = "local" | "cloud" | "auto"        (default: auto→local-if-no-key)

   services/creative_engine.py  becomes a thin DISPATCHER →
       services/local_image.py · services/local_video.py · services/local_music.py   (NEW, mirror local_voice.py)
       └─ or the existing Gemini path
```

### 27.3 Per-capability scoping

| Capability | Best open option(s) | Quality vs Gemini | Min hardware | License (commercial?) | Integration |
|---|---|---|---|---|---|
| **Image** | **FLUX.1-schnell**, **SDXL** (SD3.5) | **Small/closing.** FLUX-schnell ≈ near-parity for many prompts; SDXL slightly behind but battle-tested | SDXL 8 GB VRAM (or CPU ~1–5 min); FLUX 12–24 GB (quantized GGUF ~8 GB) | FLUX-schnell **Apache-2.0 ✅**; SDXL OpenRAIL ✅; **FLUX-dev = non-commercial ✗ (avoid for marketplace)** | `diffusers` / ComfyUI; well-trodden |
| **Video** | **LTX-Video** (light), **CogVideoX-5B**, HunyuanVideo (heavy) | **Largest gap.** Veo far ahead on length+coherence; open = short (2–5 s), more artifacts | LTX 8–12 GB; CogVideoX 16–24 GB; Hunyuan 24–60 GB; **CPU not viable** | CogVideoX **Apache-2.0 ✅**; LTX open ✅; Hunyuan community-license (check) | `diffusers`; heavy |
| **Music** | **Stable Audio Open**, **MusicGen** (AudioCraft) | **Good enough** for instrumental beds/loops/clips; Lyria's edge is full songs + vocals + polish | MusicGen-small ~4–8 GB or CPU (slow); medium 12–16 GB; Stable Audio Open ~8 GB | **Watch weights:** AudioCraft code MIT, some weights **CC-BY-NC ✗**; Stable Audio Open more permissive — **pick commercial weights for the marketplace** | `audiocraft` / `stable-audio-tools` |
| **TTS** | **Piper** (T1) · **NeMo FastPitch/HiFiGAN** (T2) | **Done.** Local is already the default | Piper CPU-native ✅ | MIT/permissive ✅ | **Shipped** |
| **ASR** | **faster-whisper** (T1) · **NeMo nemotron** (T2) | **Done.** Local is already the default | faster-whisper CPU-native ✅ | MIT ✅ | **Shipped** |

**Net: 2 of 5 capabilities (the voice halves) are already local-default.** The new build is three
modules — `local_image`, `local_video`, `local_music` — mirroring the proven `local_voice.py`.

### 27.4 The three creative tiers (mirroring voice)

```
   TIER 0  (any laptop, CPU, ZERO keys) ── SD-Turbo/SDXL images · MusicGen-small ·
            Piper/whisper · ffmpeg still-montage "video".  A complete creator on a potato.
   TIER 1  (consumer GPU 8–12 GB) ──────── FLUX-schnell/SDXL · LTX/CogVideoX short clips ·
            MusicGen-medium/Stable Audio Open.  The "real" local studio.
   TIER 2  (cloud, opt-in PREMIUM) ──────── Nano Banana Pro · Veo · Lyria-pro.  Best-in-class, paid.
```

### 27.5 Minimum viable creative stack on a no-GPU laptop

| Media | No-GPU approach | Viable? |
|---|---|---|
| Text | local LLM (Ollama, already shipping) or cloud | ✅ |
| **Images** | SD-Turbo / SDXL-Lightning (few-step) on CPU — seconds-to-~30 s | ✅ |
| **Music** | MusicGen-small on CPU — slow but fine for 10–30 s beds | ✅ |
| **Voice** | Piper + faster-whisper — already CPU-native and fast | ✅ |
| **"Video"** | **ffmpeg still-montage**: animate keyframes (Ken Burns / crossfade) over a generated music bed via `timeline_engine` (§3) | ✅ (montage) |
| **Generative video** | true text-to-video | ❌ on CPU → GPU or cloud upgrade |

> **The honest verdict:** a no-GPU laptop gets a **complete creator** — text, images, music, voice,
> *montage* video, full provenance, and full marketplace participation — with **zero cloud keys.** The
> *one* genuine gap is true generative video, filled by a GPU (Tier 1) or by cloud Veo (Tier 2). The
> montage path is not a sad fallback: a well-cut sequence of generated stills over an original score
> is genuinely shippable content, and it is 100% local.

### 27.6 Effect on the creative pipeline

- **Scene DNA: unchanged.** It is provider-agnostic prompt *structure* that renders to a string any
  backend consumes — this is exactly the payoff of the layered design. The `negative`/technical
  layers map cleanly onto SD/FLUX negative prompts and sampler params. (One small add: an optional
  `seed` in `extras` for reproducible local gen.)
- **creative_pipeline stages: unchanged contract.** A `music`/`image`/`video` stage doesn't care
  which backend fulfills it; the dispatcher (§27.2) routes by capability. Local best-of-N is
  *cheaper to iterate* (no per-call cost) but *compute-bound* (slower) — `take_comparison` logic is
  identical, only the latency/cost profile shifts.
- **QA gates: degrade gracefully, already.** The vision evaluator uses Gemini vision today; the local
  alternative is a small local VLM (LLaVA / Qwen2-VL / moondream) for image scoring. When no scorer
  is available the gate already returns `status="skipped"` = pass (existing behavior), so **no key
  never blocks output.** Honest: local QA scoring is weaker than Gemini-vision; for Tier 0 we lean on
  it less and trust take-comparison + the user.
- **timeline_engine (§3): already local (ffmpeg)** — and becomes *more* central as the no-GPU "video"
  story. The assembly layer was local-first from the start.
- **provenance (§7): identical.** The `tool_chain` records the *actual local model + version* used;
  a locally-made work is signed and owned exactly like a cloud-made one. **Local + provenance is the
  purest expression of the sovereignty ethos** — you made it, on your machine, and you can prove it.

### 27.7 Dependencies (opt-in, mirroring the voice extras)

```
pyproject.toml optional-dependencies:
   creative-local-lite = ["diffusers>=0.31", "torch>=2.2",        # CPU images + music
                          "audiocraft>=1.3"]                       # (or stable-audio-tools)
   creative-local-gpu  = ["xformers", "accelerate", ...]           # GPU acceleration, video models
```

Consistent with the existing `local` / `voice-local-lite` / `voice-local-gpu` philosophy and the
`RELEASE_PLAN.md` slim-default goal: **heavy ML stays opt-in**, the model weights download on first
use (the same opt-in fetch flow NeMo already uses), and `[all]` deliberately excludes the GPU video
stack (too heavy/CUDA-specific to bundle).

### 27.8 Honest tradeoffs

- **Images: local is *here.*** FLUX-schnell/SDXL are competitive; this capability is a win today. ✅
- **Music: local is *good enough*** for beds/clips; the trap is **weight licensing** — use
  commercially-licensed weights (Stable Audio Open / properly-licensed MusicGen) for anything sold or
  provenance-signed. ✅-with-care
- **Video: local is *behind*,** and CPU is non-viable. This is where cloud stays clearly premium and
  where the montage fallback carries the no-GPU story. ⚠️ Be honest in the UI about it.
- **First-run weight downloads are multi-GB** — needs a clear fetch-and-cache UX (precedent: NeMo).
- **No provider-side safety filter on local models.** With local generation, **`check_content_safety`
  (cLaws) is the *only* pre-emptive gate** (§25.12). More sovereign, but it shifts the entire safety
  responsibility onto our own gate — which must therefore be robust, tested, and updatable. This is
  the most important non-obvious consequence of going local-first, and it ties Layer 1 directly back
  to the moderation protocol (§25).

### 27.9 The payoff

This makes the headline claim *true*, not aspirational: **Friday is a complete, sovereign creator
studio with zero cloud dependencies — and cloud is the premium accelerator.** It is the same promise
the voice stack already keeps, extended to every medium. For the beachhead community (§26.3) it is
not a footnote — it is the reason they show up.

---

*End of specification. Part I specifies the system to build (production → ownership → federation,
priced in Positrons with a green, encrypted, sovereign design); Part II specifies how it self-governs
(§25), why it grows (§26), and how it runs cloud-free (§27). It builds on what ships today, names
honestly what does not yet exist, and refuses to promise the parts that are still research. The plan:
build the whole thing, then bring people in.*
