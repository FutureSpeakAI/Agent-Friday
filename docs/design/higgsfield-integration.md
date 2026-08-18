# Higgsfield — cloud motion for local stills, and a capability list that stops lying

**Date:** 2026-08-17
**Status:** design. **No implementation code exists for this document — it lands first, by
instruction.** It is written to be built by a fresh-context session with no priors: every
fact you need is in this file or at a cited file:line, and nothing is assumed remembered.
**Subject:** integrating [Higgsfield](https://platform.higgsfield.ai) — an asynchronous
cloud generation API (images, video; audio/3D per-account, see §2.7) — into Friday, a
local-first personal AI on a Windows desktop with one RTX 4070 (12 GB).
**Build on:** the current default working branch (at spec time: `app-level-test-suite`,
HEAD `05076cb`). The judgment-gate work from `deep-research-gate` is **already merged into
that HEAD** (`git log HEAD..deep-research-gate` is empty — verified). Do not branch off
`deep-research-gate` or `residency-policy`. Other sessions may be active in this repo:
check `git status` before you start, commit only your own files, and never commit secrets.

**Evidence registers:**
- **VERIFIED** — the cited line, file on disk, or vendor doc page was read during the
  audit runs for this document (2026-08-17). Vendor cites are to
  `docs.higgsfield.ai/docs/*` pages as read that day.
- **INFERRED** — a conclusion from verified facts, reasoning shown.
- **UNKNOWN** — not determined; the check that would settle it is named.

---

## 0. What this integration is for, in one paragraph

Friday generates images locally (Z-Image Turbo on the user's own GPU, under an exclusive
Arbiter lease) and generates video today only through Google Veo — which works (§3.1) but
blocks a web-server thread for up to ten minutes and is lost if the server restarts
mid-render. Music is a confirmed stub that writes a markdown file describing a track
(§3.2). Higgsfield adds a second cloud creative provider with a properly asynchronous
lifecycle (submit → poll → download), a file-upload path that makes **local-still →
cloud-motion** a first-class flow, and — because its jobs consume zero local VRAM — a real
backing for the "run it in the cloud now" option Friday already offers when heavy work is
proposed and the GPU is busy. The integration's two non-negotiables: **every output is
pulled to Stephen's disk before a job is called done** (Higgsfield deletes outputs after
as little as seven days), and **the capability list tells the truth the moment this is
wired** — which also means correcting one stale self-description and one dishonest tool
description that predate this work (§3.1, §3.2).

---

## 1. The repository you just landed in

Orientation for a stranger. Every item here was verified 2026-08-17 and each has burned a
session before.

- **Package root is `src/agent_friday/`**, not a top-level `services/`. All relative paths
  in this document are under it unless absolute.
- **Python:** `venv\Scripts\python.exe` — bare `python` on PATH resolves to an unrelated
  venv without Flask. Tests: `venv/Scripts/python.exe -m pytest` (~1,870 offline tests,
  ~45 s, no network, no keys). `FRIDAY_TESTING=1` must be set **before** `import server` —
  it makes import inert (no threads) and redirects the home dir to a temp sandbox. LLM
  entry points are autouse-stubbed; `google.genai.Client` is patched by a `mock_gemini`
  fixture for creative routes — your Higgsfield transport needs the equivalent (§7).
- **No Flask reloader** (`server.py:659`, `debug=False`): any Python change needs a full
  server restart. The server holds a single-instance lock on port 3000; quit via the tray
  icon.
- **Never edit `index.html` directly.** It is the *served* file, read from disk per
  request. UI source lives in `ui_parts/`; stage changes through
  `scripts/ui_stage.py`'s `stage()` context manager (atomic swap after syntax check).
- **Pre-commit secret scanner** (`.githooks/security_scan.py`, active in this clone):
  trips on generic `api_key/secret/token/password = value` patterns, email addresses, and
  `C:\Users\<name>\` paths. Escape hatch for false positives:
  `# pragma: allowlist secret` (73 existing uses). The repo is **public**: no real keys,
  no personal paths, no `*.bat` files in commits, ever.
- **Line endings:** `.gitattributes` is deliberately scoped, not repo-wide. Use
  `git diff -w` to see real changes; verify committed state with `git show HEAD:<path>`.
  Several files carry UTF-8 BOMs — do not "fix" encodings in passing.
- **House test convention** (from `docs/audits/decisions-2026-08.md`): tests use **real
  provider response bodies with a fake transport** — capture Higgsfield's actual JSON
  shapes into fixtures; never invent simplified ones.

### House rules the implementing session is held to

These are standing law in this repo, each earned by a real incident. §4.11 maps each to a
mechanism in this design; internalize them before writing code.

1. **Never claim an action not taken.** (A prior session reported a build that never
   launched; the rule since: never report work without the artifact or task id.)
2. **Never invent a technical constraint.** (Friday once fabricated a "hard-coded safety
   filter" to explain a refusal; the audit found no such filter. `services/creative_policy.py:1-33`
   exists because of it.)
3. **A capability the tools can't express is disclosed, not silently substituted.**
4. **Warn before going quiet, with a real estimate.** (`services/pause_forecast.py` is the
   existing mechanism; ≥3 s of expected silence warrants a warning.)
5. **Report conclusions into the conversation unprompted when done.**
   (`notifications_engine.push(proactive_chat=True, ...)` is the working seam.)
6. **A subsystem that runs and produces nothing is a failure even when it exits zero.**
   (The stubbed contact-research route that wrote "pending" bullets is the canonical
   violation.)

---

## 2. The Higgsfield API — verified against their docs

All facts below read from `docs.higgsfield.ai` on 2026-08-17. Index of every page:
`https://docs.higgsfield.ai/docs/llms.txt`. **VERIFIED** unless marked otherwise.

### 2.1 Authentication — two halves, not one token

```
Authorization: Key ${HF_API_KEY_ID}:${HF_API_KEY_SECRET}
Content-Type: application/json
```

The credential is an **ID + secret pair**, created at `https://cloud.higgsfield.ai`.
Stephen has said "I have an API key" — **he may hold only one half; Q1 asks.** Legacy
`hf-api-key` / `hf-secret` headers are accepted but deprecated. Vendor guidance: keep
credentials server-side, store encrypted, rotate immediately on exposure.

### 2.2 Request lifecycle

Submit is `POST https://platform.higgsfield.ai/<model-path>` with a JSON body. The
response returns **immediately**:

```json
{"status": "queued",
 "request_id": "d7e6c0f3-6699-4f6c-bb45-2ad7fd9158ff",
 "status_url": "https://platform.higgsfield.ai/requests/<id>/status",
 "cancel_url":  "https://platform.higgsfield.ai/requests/<id>/cancel"}
```

Vendor instruction, twice-stated: **use the returned URLs; never construct them**, and
**persist `request_id` the moment it arrives** (also needed for support tickets).

Statuses: `queued` → `in_progress` → terminal `completed | failed | nsfw | canceled`.
Cancellation (`POST cancel_url`) works **only while `queued`**. Completed responses carry
outputs by modality: `images: [{url}]`, `video: {url}` (possibly with `zip/mov/jsx/fbx/ply`
artifacts), `audio`/`audios` with `url` fields.

### 2.3 Models documented publicly

| Modality | Endpoint path | Params seen |
|---|---|---|
| Text→image | `higgsfield-ai/soul/standard` ("flagship") | `prompt`, `aspect_ratio` (`"16:9"` etc.), `resolution` (`"720p"`) |
| Image→video | `higgsfield-ai/dop/standard` | `image_url`, `prompt` |
| Image→video | `kling-video/v2.1/pro/image-to-video` | `image_url`, `prompt` |

The API reference states model request schemas are being redesigned and are *intentionally
not listed*; the full catalogue is **account-dependent**, visible in the Higgsfield Cloud
dashboard. Duration/motion/resolution parameters for video are **UNKNOWN** from public
docs — settled by reading the dashboard's model pages or the OpenAPI spec
(`docs.higgsfield.ai/docs/openapi.json`) with a live account.

### 2.4 File uploads (the pairing enabler)

`POST https://platform.higgsfield.ai/files/generate-upload-url` → returns
`{public_url, upload_url, upload_headers, content_type}`. Send the file bytes to
`upload_url` **with every header from `upload_headers`** (presigned; expires in one hour),
then reference `public_url` as `image_url` in a generation request. Accepted types:
`image/jpeg|jpg|png|webp|gif`, `audio/wav|x-wav`, `video/mp4`. Size limits **UNKNOWN**
(not documented). Retention of the uploaded file itself: **UNKNOWN** (only the upload
URL's one-hour expiry is documented) — treat uploads as leaving the machine permanently
(§4.5).

### 2.5 Errors, retries, and the missing idempotency key

Sync errors: `{"detail": "..."}`. Terminal failure:
`{"status": "failed", "request_id": ..., "error": "..."}`. Every response carries an
`X-Correlation-ID` header — **log it beside the request_id**; support asks for both.

| Code | Meaning | Retry? |
|---|---|---|
| 400 | invalid params, rejected input, **or concurrency cap reached** | after fixing, or after a job finishes |
| 401 | bad credentials | no |
| 403 | **insufficient credits** | after funding |
| 404 | request/model not found *for this account* | no |
| 422 | body validation failed | no |
| 423 | model temporarily blocked | later |
| 500 | server error | yes, backoff |
| 503 | model disabled/not ready | later |

**Submissions have no idempotency keys.** Vendor's own warning: never auto-repeat a
generation after an ambiguous timeout — you may pay twice. This is a rule in §4.10, not a
suggestion.

### 2.6 Rate limits, billing, retention

- **The limit is concurrency, not rate**: N requests queued-or-running at once (example
  error: *"Maximum number of concurrent requests (4) has been reached"* — HTTP 400, **no
  `Retry-After` header**). The N is per-account/subscription, shown in the dashboard.
  Per-model caps may also exist. **UNKNOWN for Stephen's account — Q7.**
- **Billing:** account credits; **only successful generations are charged**. `failed` and
  `nsfw` are not charged; a successfully canceled queued request is **refunded**. Credits
  **expire one year** after purchase. Cost varies by model and parameters; a
  per-model **estimate endpoint** exists
  (`https://platform.higgsfield.ai/estimate/<model-path>` → `{credits, usd}`).
  **No balance endpoint is documented** — spend visibility must be built from Friday's own
  ledger (§4.8).
- **Retention:** *"Generated output is accessible for at least seven days after creation
  and may be removed after that period. Download completed files to your own storage for
  long-term retention."* This sentence is why §4.3 exists.

### 2.7 The honesty note on "images, video, audio and 3D"

The public docs concretely document **text-to-image and image-to-video only**. The webhook
envelope reserves an `audio` slot and video artifacts may include `fbx`/`ply` (3D-ish),
but **no audio/music or 3D model endpoint appears anywhere in the public docs** — the
catalogue is account-gated. Consequence for this spec: the video path is designed and
buildable now; **whether the music lie gets *fixed* by Higgsfield or merely *disclosed*
depends on what Stephen's dashboard actually lists — Q2.** Do not wire a music tool to an
endpoint this document cannot name.

### 2.8 Polling and webhooks

**Polling** (vendor-recommended): start at 2 s, multiply by 1.5 per cycle, cap at 10 s,
add jitter; 30 s HTTP timeout per poll; set an application-level total timeout per model.
Retry polls on network failure/5xx; stop on 401/404.

**Webhooks:** per-request query parameter `hf_webhook=<url-encoded HTTPS endpoint>`.
Receiver must be publicly reachable, answer within 10 s; retries on 5xx for up to two
hours; duplicates possible. **No signature verification exists — no HMAC, no shared
secret.** Anyone who learns the URL can forge a completion event. §4.9 decides
accordingly.

---

## 3. What exists in Friday today — the verified ground

### 3.1 Video: **works, and Friday's self-description is stale the other way**

The commissioning brief said Veo "has never generated anything on this machine." The
audit found the opposite: **two real H.264 MP4s on disk**
(`~/Desktop/friday-creations/friday-video-20260713-*.mp4` and `-20260720-*.mp4`, ISO-BMFF
headers verified), with a metadata sidecar recording
`model: veo, api_model: veo-3.1-generate-preview, mode: image-to-video, seed_image: ...` —
the full image→video pipeline ran end-to-end on 2026-07-13 and 2026-07-20. Meanwhile
`services/self_account.py:111-122` still tells the model video is *"untested rather than
proven."* **The lie is the stale note, and this build corrects it** (§4.7): the
`self_account` probe should learn to consult the creations record instead of guessing from
key-presence.

What *is* wrong with the Veo path, and what Higgsfield's design must not copy
(**VERIFIED** `services/creative_engine.py:843-854`): the poll loop is a synchronous
`time.sleep(8)` loop **inside the Flask request thread**, up to 600 s, with nothing
persisted — a server restart mid-render orphans the operation unrecorded. Also: Veo and
music prompts are **not egress-gated** — only `generate_image` calls
`gate_text` (`creative_engine.py:659-660`); `generate_video` sends the prompt raw.

### 3.2 Music: a confirmed stub with a dishonest tool description

`services/music_engine.py:19-28` (module docstring): the installed `google-genai` 1.72.x
has **no batch `generate_music` surface** — only Lyria RealTime streaming. Demo mode
(`:398`) writes a *markdown file describing the track*. The availability probe, the
`/api/create/music/available` route, and the artifact itself all disclose this honestly —
but two surfaces still lie (**VERIFIED**):

- `services/agent.py:2723-2734` — the `generate_music` **tool description** says
  *"Generate REAL music … You CAN make music — do not say you can't."* Unconditionally
  registered.
- `services/capability_router.py:21` — `CAPABILITIES` **omits `creative_music`
  entirely**, so `GET /api/capabilities` has never reported music at all.

Both get fixed in §4.7 regardless of what Higgsfield's catalogue turns out to hold.

### 3.3 The local image path (the pairing partner)

`services/local_image.py` (**VERIFIED** throughout): the only local image model is
**Z-Image Turbo FP8** via ComfyUI on port 8188 — *there is no local SD 3.5*; the
`EmptySD3LatentImage` node is a latent-shape node the Z-Image graph reuses. Generation
takes an **exclusive Arbiter `image_job` lease** (one lease per batch, everything except
the e2b sidekick evicted), registers a progress orb *before* the lease (pid
`image-<8hex>`), streams ComfyUI websocket progress, and on success **copies outputs into
`CREATIONS_DIR`** and appends one JSONL line per file to `creations-manifest.jsonl`.
Cancel: `POST /api/processes/<pid>/cancel` (`routes/tasks.py:345`) — flag first, then
ComfyUI interrupt, then lease reclaim with an 8 s grace.

### 3.4 The storage contract every engine must honor

**VERIFIED**; match these exactly or the gallery/UI breaks:

- **Gallery files:** `CREATIONS_DIR` = `~/Desktop/friday-creations/`
  (`core/__init__.py:647`). Flat; real artifacts only.
- **Metadata sidecars:** `~/.friday/creations_meta/<filename>.json` — deliberately
  *outside* the gallery dir (`creative_engine.py:53-55`).
- **File record shape** (`creative_engine.py:390-397`):
  `{"filename", "kind", "url": "/api/creations/<fn>", "framed_url": "/creation/<fn>",
  "path": str(CREATIONS_DIR / fn)}`. Returning bare path strings once turned a successful
  108-second render into an HTTP 500 (`local_image.py:550-561`).
- **Routes return HTTP 200 with status-in-body** (`'ok'|'blocked'|'unavailable'|'error'`;
  `routes/creations.py:312-314`); clients branch on the body.
- **Provenance:** `services/provenance.py.write(...)` — C2PA-style manifest + hash-chained
  ledger, best-effort (never breaks a generation). Called per artifact.
- **Surfacing:** `services/creations.py:623 _notify_creation(filename, orb_pid)` — fades
  the orb, pushes a notification with Open actions. Note it does **not** set
  `proactive_chat`; only the daily-creation push does (`creations.py:569-585`). §4.10 uses
  `proactive_chat=True` for finished Higgsfield jobs, because minutes-long unattended work
  must report unprompted (house rule 5).

### 3.5 Async machinery: nothing durable exists — copy the right pattern

**VERIFIED** (§6 of the audit): four mechanisms exist, none fits as-is. The in-process
`TASKS` registry dies with the process. `services/work_queue.py` is **GPU-lease-shaped**
(classes map to Arbiter leases; a no-GPU class only drains `when_away` — wrong for
user-initiated generation; the `now_cloud` disposition exists **with no cloud runner
behind it**). The scheduler ticks every 60 s. The only two external-API pollers in the
tree (Veo, dead Lyria code) both block request threads.

The pattern worth copying is `services/analytics_collector.py`: **persist the job, let a
tick re-derive what is due, never hold a thread across the wait.** §4.1 adapts it with a
faster wake-up.

### 3.6 Egress, secrets, and the image-bytes caveat

- `egress_gate.gate_text(text, provider, field)` (**VERIFIED** `egress_gate.py:672`) is
  the single-string gate built precisely for non-Anthropic-shaped call sites.
  **Unknown provider names classify as cloud and are gated — fail-closed.** Passing
  `"higgsfield"` needs no registry work to be safe.
- **Image bytes cannot be text-classified.** The documented caveat lives at
  `routes/chat.py:191-195` and `services/qa_gates.py:209-210`. Any upload of image bytes
  to Higgsfield is egress the text gate cannot inspect. §4.5 handles this by provenance
  and consent, not by pretending a scanner exists.
- **Secrets:** environment variables bootstrapped from the (gitignored) `start.bat` by
  `_bootstrap_env_from_launch_scripts()`; encrypted per-provider key files under
  `~/.friday/providers/keys/` via `services/credential_store.py`. A provider descriptor
  declares its key source: `"auth": {"type": "env_var", "key": "..."}`
  (`provider_registry.py:172`). Higgsfield needs **two** variables —
  `HIGGSFIELD_API_KEY_ID` and `HIGGSFIELD_API_KEY_SECRET` — the first paired credential
  in the system; `credential_store` stores them as two entries or one `id:secret` string
  (implementer's choice; document it).
- **Provider descriptor:** drop a JSON in `~/.friday/providers/` (or add to
  `_DEFAULT_PROVIDERS`); the shape to copy is `local-comfyui`
  (`provider_registry.py:139-158`) — with no `classification: "local"` claim, so it
  classifies **cloud** everywhere that matters.

### 3.7 The seven-place capability registration checklist

**VERIFIED** — a capability registered in fewer than all seven places produces exactly the
Lyria inconsistency (`core/__init__.py:1575-1580` documents the silent-drop failure mode):

| # | Place | File:line |
|---|---|---|
| 1 | `capability_router.CAPABILITIES` tuple + labels | `services/capability_router.py:21-34` |
| 2 | `DEFAULT_SETTINGS["capability_routing"]` — **the load-bearing one**; a key missing here is silently dropped on every settings save | `core/__init__.py:1572-1595` |
| 3 | `_CAP_FLAT_MAP` legacy mirror | `core/__init__.py:1646-1653` |
| 4 | Provider descriptor (`capabilities`, `roles`, `models`, `model_meta`) | `services/provider_registry.py` |
| 5 | Model catalog → `/api/models` | `services/model_catalog.py` |
| 6 | `routes/intelligence.py:_ROLES` table | `routes/intelligence.py:53-87` |
| 7 | Tool schemas + handlers + risk tiers | `services/agent.py:2677-3025, 3341` |

Plus the honesty layer that makes the list *true* rather than merely present:
`services/self_account.py:59-158` — **probed, not declared** — injected into the system
prompt with the instruction *"Never offer a capability marked NO."*

---

## 4. The design

### 4.1 The engine: `services/higgsfield_engine.py` + a durable job store

One new module owning the full lifecycle. Its skeleton:

- **Job store:** `~/.friday/higgsfield_jobs.json` (atomic tmp+replace, the
  `work_queue.py:85-117` pattern). Every job is persisted **before** the submit HTTP call
  leaves (so an ambiguous timeout can never orphan an unbilled/billed mystery — §2.5's
  no-idempotency rule), and updated on every transition.
- **Poller:** one daemon thread owned by the engine, started lazily on the first active
  job, exiting when none remain. It polls each active job on the vendor curve (2 s × 1.5
  → cap 10 s, jitter), using the stored `status_url` verbatim. **It is not a request
  thread** — submit routes return immediately (§4.2). Restart recovery: an engine-init
  scan plus a 60 s scheduler-tick builtin (`register_builtin_task`) re-adopt any
  `queued/in_progress/downloading` jobs found in the store — a restart mid-render resumes
  polling instead of orphaning, which is precisely what the Veo path cannot do today
  (§3.1).
- **Concurrency guard:** a semaphore sized by `settings.higgsfield.max_concurrent`
  (default 4, per the vendor's example cap — Q7 confirms the account's real number).
  Submissions beyond it queue in the job store as `waiting` and submit as slots free. A
  400 concurrency error from the vendor also re-queues (with backoff) rather than failing
  the job — the cap is account-global and something else may hold slots.
- **HTTP:** `requests` with 30 s timeouts; retries per the §2.5 table; every response's
  `X-Correlation-ID` recorded on the job. **Never resubmit a generation after an
  ambiguous timeout** — mark the job `unknown`, poll `status_url` if a `request_id` was
  received, and otherwise surface the ambiguity honestly.

**Job record:**

```
HiggsfieldJob
  job_id            hf-<8hex>          # doubles as the orb pid (cancel route keys on prefix)
  kind              image | video | animate
  model_path        e.g. higgsfield-ai/dop/standard
  params            the submitted body (post-gate prompt, image public_url, ...)
  source_file       filename|null      # the local still an animate job uploaded
  request_id, status_url, cancel_url   # verbatim from the submit response
  correlation_ids   [str]
  status            waiting | submitted | queued | in_progress | downloading |
                    done | failed | nsfw | canceled | unknown
  est               {credits, usd}|null   # from the estimate endpoint, pre-submit
  submitted_at, finished_at
  outputs           [{url, content_type, filename|null, downloaded: bool}]
  download_attempts int
  error             str|null
```

### 4.2 Submit is instant; the orb carries the wait

Routes return in milliseconds with HTTP 200 status-in-body (`{"status": "queued",
"job_id": ...}` — §3.4 convention). The orb (registered before the submit, like
`local_image.py:420-431`) carries the lifecycle: `Queued at Higgsfield` →
`Generating… (cloud)` → `Downloading…` → done/failed. `pause_forecast` is not needed —
nothing local goes quiet; the machine stays fully responsive, which is the entire point of
a zero-VRAM job. The orb label always names the provider: **"Video: … (Higgsfield
cloud)"** — house rule: name the actual servant.

### 4.3 The pull-to-disk step is part of the job, not an afterthought

Retention is ≥7 days, then gone (§2.6). Therefore:

- `completed` from the vendor moves the job to **`downloading`, not `done`**. The poller
  downloads **every** output URL to `CREATIONS_DIR`, writes the metadata sidecar
  (`kind, prompt, model: <friendly>, api_model: <model_path>, provider: "higgsfield",
  request_id, mode, seed image if any`), appends the manifest line, writes provenance
  (`tool: "higgsfield_engine.generate_<kind>"`), and only then marks `done` and fires
  `_notify_creation` + the proactive chat push (§4.10).
- **Download failure is a first-class state**: retry on the poll cadence with backoff,
  `download_attempts` counted. After 5 failed attempts, push a **warning notification**
  carrying the raw output URL and the expiry date ("I generated this but cannot pull it
  to disk — link valid until ~<date>, error: <detail>") so Stephen can save it manually.
  The job stays `downloading` and keeps retrying daily until day 6, then makes a final
  attempt and, if still failing, marks `failed` with the full account. **At no point does
  Friday say "done" while the only copy lives on someone else's server** — losing work he
  believed saved is this integration's worst outcome, and the state machine makes it
  structurally impossible to claim.
- Downloads verify content-length/non-empty bytes before the sidecar is written; a
  zero-byte file is a failed download, not a creation (house rule 6).

### 4.4 The pairing: local still → cloud motion, as one flow

A first-class `animate` job — not two tools the user must plumb together:

1. **Still:** produced locally — either just-generated by `local_image.generate()`
   (image_job lease, §3.3) or picked from `CREATIONS_DIR` by filename.
2. **Consent gate (§4.5):** the upload decision, made and disclosed here.
3. **Upload:** `POST /files/generate-upload-url` → PUT bytes with `upload_headers`
   (1-hour presign) → `public_url`.
4. **Submit:** `image_url = public_url` + motion prompt to the configured image-to-video
   model (`higgsfield-ai/dop/standard` default; Q3).
5. Poll → pull → done, per §4.1–4.3. The sidecar records `mode: "image-to-video",
   seed_image: <local filename>` — the same shape the Veo sidecar already uses, so the
   gallery and pipeline treat both providers alike.

The result summary states the split explicitly: *"Still rendered locally on Z-Image;
motion by Higgsfield (cloud)."* Privacy and GPU cost stay local for the still; only the
finished frame leaves. This flow is also exposed to `creative_pipeline`'s
image→video stage as an alternative backend, since that pipeline already produced the two
working Veo videos with exactly this seed-image shape.

### 4.5 The privacy boundary

Everything sent to Higgsfield leaves the machine. Three payload classes, three rules:

- **Text (prompts, negative prompts, any string field):** every string passes
  `egress_gate.gate_text(text, "higgsfield", "<kind>.<field>")` at **one choke point** in
  the engine's submit function — not per call site. Unknown providers classify cloud, so
  this is fail-closed from day one (§3.6). A prompt the gate redacts or drops does not
  submit "partially": if gating changed the text, the job is refused with the gate's
  explanation and the local alternative offered. This also *repairs the standing
  inconsistency* it sits next to: video prompts are ungated today (§3.1) — the new engine
  must not inherit that, and a one-line fix adding `gate_text` to the Veo path is in
  scope for the same commit (Q6 of the audit trail; it is a two-line change and leaving
  it makes the new provider stricter than the old one for no reason).
- **Image bytes (the animate upload):** the text gate cannot read pixels — a documented
  caveat, not a solvable one here (§3.6). The rule is provenance-based:
  - A **Friday-generated** image (sidecar exists; its prompt already passed the cLaws +
    egress gates at render time) may upload, **with the upload disclosed in the proposal
    and the orb** ("this sends the image to Higgsfield").
  - A **user-supplied or unknown-provenance** image (no sidecar) requires **explicit
    per-job confirmation** naming the file and the destination. A photo of his family is
    exactly this case; Friday asks, every time. Q5 lets Stephen loosen or tighten this.
  - A file under any vault-sensitive directory (`_sensitive_vault_dirs()`:
    `~/.friday/{finance,health}`, `vault/{legal,finances,family}` — **VERIFIED**
    `services/agent.py:4052-4064`) is **refused for upload outright**; the answer is the
    local pipeline or nothing. `work_queue`'s existing rule already raises on
    `touches_vault + now_cloud` — the engine honors the same law at its own front door.
- **Which side a job ran is always visible:** the orb label, the result summary, and the
  sidecar all carry provider + model. No job silently changes sides: a local render never
  falls back to cloud (or vice versa) without a new decision surfaced to Stephen — the
  disclosed-substitution rule.

### 4.6 Where it sits among the Arbiter, the queue, and the heavy-work choice

**A Higgsfield job takes no lease, ever.** It consumes no VRAM, so it must never enter
`work_queue`'s `image` class or touch the Arbiter — it runs the moment it is submitted,
even while the GPU is mid-`image_job` or the display reserve is refusing local renders
(`R-DISPLAY-RESERVE`, `residency_arbiter.py:626-636`). That is its structural value: **it
is the creative path that exists when the card is busy.**

Concretely, in the heavy-work proposal (`services/workflow_plan.py`, dispositions
`when_away | now_local | now_cloud`): for image/video work, `now_cloud` today is an empty
promise — no cloud runner exists behind it (§3.5). This integration makes it real for
creative jobs: the proposal shows `now_cloud` with the **estimate endpoint's
credits + USD** (§4.8) beside `now_local`'s time estimate (from `pause_forecast`), and
"choose for me" can weigh a busy card against a credit cost with both numbers on the
table. The lifecycle machinery is deliberately **not** the lease-and-batch machinery:
submit-poll-cancel resembles a lease only superficially — there is no scarce local
resource to arbitrate and no cold-load to amortize, so batching would add latency for
nothing. It sits **beside** the Arbiter, in its own engine, and the two meet only in the
proposal UI where their costs are compared. (INFERRED, from the measured facts that
motivate each mechanism; the alternative — a `cloud` work-queue class with
`CLASS_LEASE=None` — is workable but buys only queue-UI reuse at the price of coupling a
no-resource path to a resource scheduler.)

### 4.7 The capability list stops lying — all of it

The registration work, using §3.7's checklist:

1. **Provider descriptor** `higgsfield`: `type: "higgsfield"` (new adapter string —
   deliberately *not* in `LOCAL_CAPABLE_ADAPTERS`, so the egress gate can never classify
   it local), `auth: {"type": "env_var", "key": "HIGGSFIELD_API_KEY_ID"}` (+ secret),
   `capabilities: ["image", "video"]`, `models` + `model_meta` for `soul/standard`,
   `dop/standard`, `kling-video/v2.1/pro` with friendly names and modalities. Audio/3D
   entries are added **only after Q2 confirms they exist on his account** — a capability
   this spec cannot name does not get declared.
2. **`capability_routing`**: `creative_video` may now point at
   `{provider: "higgsfield", model: "dop-standard"}` as an alternative to Veo; the
   friendly→endpoint map lives in the engine (the `_VIDEO_MODEL_MAP` pattern,
   `creative_engine.py:81-103`). `creative_image` keeps its local default — cloud image
   via Soul is offered, not imposed (Q3).
3. **`capability_router.CAPABILITIES`**: add the missing **`creative_music`** entry
   (§3.2's omission) — regardless of Higgsfield, so `/api/capabilities` finally reports
   music honestly (as unavailable, until something real backs it).
4. **The `generate_music` tool description** (`agent.py:2723-2734`) is rewritten to tell
   the truth: it describes what actually happens ("if no cloud music backend is
   available, this writes a demo preview describing the track — say so"), and the *"You
   CAN make music — do not say you can't"* sentence is deleted. If Q2 lands a real
   Higgsfield audio model, the tool gains a real backend and the description follows the
   probe.
5. **`self_account.capabilities()`**: gains a `higgsfield` probe (both env halves present
   + a cheap authenticated GET — the estimate endpoint works as a ping) **and the stale
   Veo note is corrected** (§3.1): the video entry should consult the creations
   manifest/sidecars for prior successful output instead of asserting "no video has been
   generated on this machine" when two verified MP4s exist. The honesty layer must not
   itself be the liar.
6. Tool surface: `generate_video` and the pipeline gain provider routing through
   `capability_routing`; one new tool `animate_image(filename|prompt, motion_prompt)` for
   §4.4; risk tier 2 (network), like its siblings (`agent.py:3341`).
7. `routes/intelligence.py` `_ROLES` and `/api/models` follow automatically from the
   descriptor (§3.7 #5–6) — verify, don't assume.

### 4.8 Cost, before it surprises him

- **Pre-submit estimate:** call the estimate endpoint for the chosen model; show
  `credits (~$usd)` in the proposal and on the orb. If the estimate call fails, say
  "cost unknown" — never guess a number (house rule 2).
- **Ledger:** every submit records to `services/cost_meter.py` (`provider: "higgsfield"`,
  `model: <path>`, `kind`, `duration_ms` of the full job, `cost_usd` from the estimate,
  flagged `estimated` in `kind` or metadata since no billed-amount endpoint exists).
  Terminal `failed`/`nsfw`/`canceled` jobs record **zero cost** — the vendor does not
  charge them (§2.6), and Friday's books should match the vendor's.
- **Budget guard:** `settings.higgsfield.daily_credit_cap` (default: Q4). The engine
  refuses submits past the cap with the arithmetic shown — the same explained-refusal
  shape the residency layer uses. A 403 (out of credits) is reported as exactly that,
  with the dashboard URL, never as a generic failure.
- **Spend surface:** the existing `/api/costs` routes already aggregate by provider;
  `higgsfield` appears there with zero new UI. A dashboard link belongs in the Studio
  panel for the authoritative balance, since no balance API exists.

### 4.9 Webhooks: no. Polling, with the reasoning on record

Stephen runs a Cloudflare tunnel, so a webhook is *feasible* — and still the wrong call
today:

- **The vendor signs nothing** (§2.8). A webhook endpoint through the tunnel is an
  unauthenticated inbound surface where anyone who learns the URL can inject
  `{"status": "completed", "payload": {...}}` with attacker-controlled output URLs —
  which §4.3 would then download to Stephen's disk. Mitigating that means treating every
  webhook as only a *hint* and re-verifying via authenticated `GET status_url` — at which
  point the webhook saves a few seconds of poll latency at the cost of a standing public
  endpoint, secret-URL management, dedup logic, and a new attack surface on the machine
  he lives on.
- Polling at the vendor's own recommended curve costs a handful of HTTPS GETs per job
  (a 3-minute video ≈ ~25 polls) against no documented poll rate limit.

**Decision: polling now. If webhooks are revisited** (traffic scale, or the vendor ships
signatures), the design is pre-committed here: webhook = wake-up signal only; the poller
confirms via `status_url`; payload URLs from the webhook body are never trusted; the
endpoint path carries a per-job random token and rejects unknown ids. Q6 records
Stephen's appetite.

### 4.10 Failure, honestly

| Vendor outcome | Friday's behavior |
|---|---|
| `failed` | Job `failed`, error string surfaced verbatim, "not charged" stated (it isn't, §2.6), local alternative offered where one exists |
| `nsfw` | Reported as **Higgsfield's content moderation**, in those words — *their* gate, not Friday's. Friday's own cLaws gate already ran pre-submit; if the vendor still refuses, Friday says who refused and why that is all she knows. She never invents a filter of her own to explain it (the `creative_policy.py:1-33` incident is the standing law here), and never silently reruns with an altered prompt |
| `canceled` | Refunded (§2.6); stated |
| 423/503 model blocked | Disclosed as the model being down *at the provider*; another model is offered, **never silently substituted** |
| Ambiguous submit timeout | Job `unknown`; no resubmit (§2.5 — no idempotency); status polled if a request_id exists; otherwise Stephen is told exactly what is and isn't known |
| Download failure | §4.3's escalation ladder — never silent, never "done" |
| Poller dead / jobs stuck | The liveness audit pattern applies: a job in `queued`/`in_progress` whose `updated_at` is older than 3× the poll cap is a **failure of the poller**, flagged by the scheduler tick — a subsystem that runs and produces nothing is a failure even when it exits zero |

**Cancel:** wired into the existing route (`routes/tasks.py:345`) keyed on the `hf-`
prefix: while `queued`, `POST cancel_url` (refund); once `in_progress`, cancellation is
impossible upstream (§2.2) — Friday says so and offers to discard-on-arrival instead
(download skipped, job marked `canceled_locally`, cost still incurred and stated).

**Completion reports unprompted:** terminal transitions push
`notifications_engine.push(proactive_chat=True, chat_message=...)` with the outcome, the
gallery link, and the cost — the seam the deep-research spec named P4, now built and
merged (**VERIFIED**: `deep-research-gate` is in HEAD). A generation Stephen forgot he
asked for announces itself; a failure announces itself equally.

### 4.11 House rules → mechanisms

| Rule | Mechanism in this design |
|---|---|
| Never claim an action not taken | `done` requires bytes on disk, verified non-empty (§4.3); result summaries name provider+model from the job record, not from intent |
| Never invent a constraint | `nsfw` attributed to the vendor verbatim (§4.10); "cost unknown" when the estimate fails (§4.8); UNKNOWN registers throughout §2 rather than guessed numbers |
| Disclose, never substitute | 423/503 and gate-refusals offer alternatives explicitly (§4.10, §4.5); local↔cloud never switches sides silently (§4.5) |
| Warn before silence, with an estimate | Nothing local goes silent (no lease); the orb carries queue/progress state and the pre-submit estimate carries expected cost and (where the dashboard documents it) duration |
| Report conclusions unprompted | `proactive_chat=True` on every terminal transition (§4.10) |
| Runs-and-produces-nothing = failure | The stuck-job detector (§4.10); zero-byte downloads are failures (§4.3); the probe battery in §7's tests asserts an end-to-end artifact, not an exit code |

---

## 5. Settings

```
settings.higgsfield = {
  "max_concurrent": 4,          # Q7
  "daily_credit_cap": null,     # Q4; null = no cap, refusal shows arithmetic when set
  "default_video_model": "dop-standard",   # Q3
  "upload_consent": "ask_unknown",         # ask_unknown | ask_always | trust_friday_generated  (Q5)
}
```

Registered in `DEFAULT_SETTINGS` (a key not in `DEFAULT_SETTINGS` is erased on every
save — `core/__init__.py:1327` precedent). Env: `HIGGSFIELD_API_KEY_ID`,
`HIGGSFIELD_API_KEY_SECRET` (both required; presence probed by `self_account`).

---

## 6. Build order

One commit each, offline-testable, in dependency order. Fixtures use **real Higgsfield
response bodies** captured from the live API once keys exist (or transcribed verbatim
from §2's doc quotes until then), behind a fake transport.

1. **Engine core + job store** — submit/poll/state machine against a fake transport;
   restart-recovery test (kill store mid-`in_progress`, re-init, assert re-adoption);
   ambiguous-timeout test (no resubmit, `unknown` state).
2. **Egress choke point** — every string field through `gate_text("higgsfield", ...)`;
   test with a planted TIER_2 span (refused with explanation). Same commit: the two-line
   `gate_text` addition to the Veo path (§4.5).
3. **Download-to-disk** — the `downloading` state, sidecar/manifest/provenance writes,
   the failure ladder with the day-6 final attempt; test: simulated 404 on the output URL
   escalates to the warning notification with the expiry date.
4. **Text-to-image + video routes and tools** — `soul/standard`, `dop/standard`; orb
   lifecycle; cancel by `hf-` prefix (queued → refunded; in_progress →
   discard-on-arrival); proactive completion push. Route test asserts HTTP 200
   status-in-body and the `_file_record` contract.
5. **The animate flow** — upload (presign → PUT → `public_url`), consent gate with all
   three §4.5 image classes tested (Friday-generated proceeds with disclosure; unknown
   asks; vault-path refused), sidecar `seed_image` continuity, pipeline stage hookup.
6. **Capability truth** — the seven-place registration; the `creative_music` addition;
   the `generate_music` description rewrite; the `self_account` Higgsfield probe **and
   the Veo-note correction with its manifest-consulting check**. Test: `/api/capabilities`
   reports music; the system prompt's capability text contains no claim the probes don't
   back.
7. **Cost plumbing** — estimate-before-submit, cost_meter records, the daily cap
   refusal with arithmetic, 403 messaging.
8. **Live verification** (needs keys; the `network` pytest marker exists for this):
   one real image, one real animate from a Z-Image still, one queued-cancel with refund
   confirmed in the dashboard, one forced download-retry. **The acceptance bar is
   artifacts on disk** — files in `CREATIONS_DIR` with sidecars, manifest lines,
   provenance, and a proactive chat message for each; an integration that exits zero
   without producing those is not done (house rule 6).

---

## 7. Open questions for Stephen

Each answerable in a sentence.

**Q1 — The key halves.** Higgsfield auth needs an API key **ID and a secret** — does what
you have include both halves (check cloud.higgsfield.ai → API keys), or does a secret
still need generating?

**Q2 — The account catalogue.** What does your dashboard actually list beyond image and
video — is there any audio/music or 3D model on your plan (this decides whether the music
stub gets a real backend or an honest description)?

**Q3 — Defaults.** Should image-to-video default to Higgsfield's own `dop/standard` or
the Kling model, and should cloud text-to-image (Soul) be offered in the picker at all,
given local Z-Image stays the image default?

**Q4 — The budget guard.** What daily credit cap should the engine refuse past — or no
cap, just the per-job estimate shown before submit?

**Q5 — Upload consent.** For animating images **Friday herself generated**, is
disclose-and-proceed enough, or do you want to be asked every time anything is uploaded
(unknown-provenance images and vault-adjacent files are ask-always and refused
respectively, regardless)?

**Q6 — Webhooks.** Given the vendor signs nothing (§4.9), are you content with polling
permanently, or should a signed-webhook revisit stay on the list for when they ship
verification?

**Q7 — Concurrency.** What does your dashboard show as the account's concurrent-request
limit, so the engine's default of 4 matches reality?

---

## 8. Sources

- Higgsfield docs, read 2026-08-17: index `docs.higgsfield.ai/docs/llms.txt`;
  `authentication.md`; `concepts/{requests,polling,file-uploads,errors,rate-limits,
  billing-and-retention}.md`; `how-to/webhooks.md`; `guides/{images,video}.md`;
  `api-reference/overview.md`; `quickstart.md`; `help/faq.md`. OpenAPI:
  `docs.higgsfield.ai/docs/openapi.json` (not fetched; named for the implementer as the
  place model schemas will land).
- Friday-side audit, 2026-08-17, file:line cites inline throughout §1/§3 — notably
  `services/{creative_engine,music_engine,local_image,creations,provenance,self_account,
  capability_router,egress_gate,work_queue,analytics_collector,credential_store,
  provider_registry,cost_meter}.py`, `routes/{creations,tasks,chat,intelligence}.py`,
  `core/__init__.py`, and the on-disk evidence in `~/Desktop/friday-creations/` and
  `~/.friday/creations_meta/`.
- Prior design docs this composes with: [`residency-policy.md`](residency-policy.md)
  (the Arbiter and leases §4.6 deliberately does not touch),
  [`symphony-of-intelligence.md`](symphony-of-intelligence.md) (the heavy-work proposal
  §4.6 completes), [`deep-research.md`](deep-research.md) (the judgment gate and the
  proactive-push seam, both now merged into HEAD).
