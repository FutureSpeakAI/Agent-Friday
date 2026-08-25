# Higgsfield in the creative picker — why it is absent, what it actually has, and how to list it without inventing anything

**Status:** **BUILT** — see §9. Measured 2026-08-24 against
`@higgsfield/cli@1.1.23` and the Higgsfield MCP connector, account
`stephen@futurespeak.ai` (ultra plan).
**Supersedes parts of** [`higgsfield-integration.md`](higgsfield-integration.md)
§2.7, §4.1, §4.8 — see §6.

---

## 0. The one-paragraph answer

Higgsfield's models are missing from Intelligence Settings → Creative Model because
Higgsfield was never registered as a **creative provider**. It is wired into Friday as a
**remote MCP tool surface** (`~/.friday/mcp_servers.json` → `servers.higgsfield`,
`https://mcp.higgsfield.ai/mcp`), which the chat/agent loop can call, but the picker is
built from an entirely different structure — the declarative provider descriptors in
`services/provider_registry.py`. Higgsfield has no descriptor there, so it contributes
zero rows. This is not a filter, not a whitelist, and not a modality mismatch: the
provider simply does not exist in the list the picker reads from.

---

## 1. How the creative picker is actually built

The chain, verified end to end:

| # | Layer | File:line |
|---|-------|-----------|
| 1 | Declarative provider descriptors — the root source | `services/provider_registry.py:55` (`DEFAULT_PROVIDERS`) |
| 2 | Role constant the picker filters on | `services/provider_registry.py:46` (`ROLE_CREATIVE = "creative"`) |
| 3 | Per-model role/modality overrides | `services/provider_registry.py:153-270` (`model_meta`) |
| 4 | Catalog assembly — grouped by role | `services/model_catalog.py:580` (`build_catalog()`) |
| 5 | HTTP surface | `GET /api/models` → `catalog["roles"]["creative"]` |
| 6 | UI select | `ui_parts/app.html:7898` (`mkSel('creative','creative_model', …)`) |
| 7 | Modality split inside the UI | `ui_parts/app.html:2088-2089`, `9296-9305` |

**It is a static array, not a runtime query** — with one important exception. Step 1 is a
hardcoded Python list of descriptors. Every creative model Friday offers today is a
literal in that file: two local ComfyUI image models
(`provider_registry.py:150`, `z-image-turbo-fp8` / `sd3.5-medium-fp8`) and the Gemini
creative set (`provider_registry.py:249-262`, Nano Banana Pro / Nano Banana 2 / Veo 3 /
Gemini Omni Flash).

The exception is the precedent that matters: `services/hosted_catalog.py` already fetches
Anthropic's and OpenRouter's **live** `/v1/models` and lets that list **replace** the
shipped statics (`model_catalog.py:305-312`). That is the pattern this design extends.

The UI needs no changes. It renders whatever the backend sends, filtered by
`modalities` (`app.html:2088`) — a correctly-declared provider surfaces automatically.

---

## 2. Why Higgsfield is absent — cause identified, alternatives eliminated

Four candidate causes were tested. Only the first holds.

| Candidate | Verdict | Evidence |
|---|---|---|
| **Registered only as a tool/MCP surface, never as a creative provider** | **CONFIRMED** | `higgsfield` appears in `services/media_tools.py:266`, `agent.py:4978-5610`, `tool_budget.py:13`, `creative_store.py:99`, `tool_receipts.py:39` — all tool-loop plumbing. It appears **nowhere** in `provider_registry.py` or `routing/provider_descriptors.py`. |
| Settings page filters to a provider-ID whitelist | Ruled out | No whitelist exists. `build_catalog()` iterates `registry.get_enabled_providers()` (`model_catalog.py:625`) — anything enabled is included. |
| Provider exposes no enumerate/list capability | Ruled out | The CLI exposes a complete enumeration API. See §3. |
| Models present but filtered by modality/type mismatch | Ruled out | Nothing to filter — no Higgsfield entries reach the catalog at all. |

**Supporting detail:** Higgsfield is registered as an MCP server contributing ~86 tools
(`tool_budget.py:13` — "Higgsfield connector registered 86 more"). Those are *actions*
(`mcp_higgsfield_balance`, generation calls), not *catalog rows*. The two systems never
meet.

---

## 3. What Higgsfield actually offers — measured inventory

Enumerated 2026-08-24. The image/video/audio/text counts below come from `higgsfield
model list --<type> --json` (**67 job types + 19 workflows**); the 3D section and the
parameter shapes come from the MCP's `models_explore`, which carries strictly more —
see §3.5. IDs are `job_type` / `id` values, the string the API accepts.

### 3.1 Image — 31 job types

Generation: `flux_2`, `flux_kontext`, `gpt_image_2`, `grok_image`, `grok_image_2_0`,
`text2image_soul_v2` (Higgsfield Soul 2.0), `image_auto`, `kling_omni_image`,
`nano_banana`, `nano_banana_flash` (Nano Banana 2), `nano_banana_2_lite`,
`nano_banana_pro`, `openai_hazel`, `recraft_v4_1`, `seedream_v4_5`, `seedream_v5_lite`,
`seedream_v5_pro`, `soul_cast`, `soul_cinematic`, `soul_location`, `z_image`.

Edit/enhance (**not generation** — see §4.2): `bytedance_image_upscale`,
`flux_2_pro_outpaint`, `outpaint`, `image_background_remover`, `topaz_image`,
`topaz_image_generative`, `nano_banana_2_ai_stylist`, `nano_banana_2_relight`,
`nano_banana_2_skin_enhancer`, `nano_banana_2_shots`.

### 3.2 Video — 29 job types

Generation: `flux_3_video`, `gemini_omni`, `veo3`, `veo3_1`, `veo3_1_lite`, `grok_video`,
`grok_video_v15`, `happy_horse_video`, `kling2_6`, `kling3_0`, `kling3_0_turbo`,
`minimax_h3`, `minimax_hailuo`, `seedance1_5`, `seedance_2_0`, `seedance_2_0_mini`,
`seedance_2_5`, `wan2_6`, `wan2_7`, `wan3_0`, `wan3_0_prime`.

Edit/enhance: `bytedance_video_upscale`, `clipify`, `sam_3_video`,
`video_background_remover`, `video_deflicker`, `video_upscale`, `topaz_video`.
Oddity: `llm_text` ("LLM Generation") is typed `video` upstream.

### 3.3 Audio — 6 job types, and this is where the premise needs correcting

| job_type | Display | What it really is |
|---|---|---|
| `sonilo_music` | Sonilo Music | **Music generation.** Params: `prompt`, `duration` (both required). The only unambiguous music model. |
| `seed_audio` | Seed Audio 1.0 | **Speech/TTS.** Params include `voice_id`, `voice_type`, `speech_rate`, `pitch_rate`, `sample_rate`. |
| `inworld_text_to_speech` | Inworld TTS | Speech. |
| `qwen_audio_tts` | Qwen Audio 3.0 TTS Flash | Speech. |
| `text2speech_v2` | Text to Speech V2 | Speech. |
| `mirelo_text_to_audio` | Mirelo Text to Audio | Text-to-audio / sound effects. |

**Correction to the working assumption.** "Music, video and image aplenty" is right for
**image (31)** and **video (29)**, and **wrong for music**: audio is *six* models of which
**four are speech/TTS**, one is sound-effects, and exactly **one is music generation**
(`sonilo_music`). A picker that offers "Higgsfield music" as a category implies a depth
that is not there. Audio should be split into **Music** (1) and **Speech** (4) rather than
presented as one "audio" bucket — and Friday already has a separate voice/TTS surface, so
the speech models arguably belong to the *voice* role, not *creative*.

### 3.4 3D — 17 models. Present, and nearly missed

**This section originally said 3D did not exist. That was wrong, and the way it was wrong
is the whole argument of this document.**

The Higgsfield CLI has no `--3d` flag, returns no `type: "3d"` in any response, and lists
no 3D workflow. Enumerating carefully from that one surface yields a confident, documented,
**false** conclusion. The MCP connector's `models_explore(type:'3d')` returns **17 models**
with `has_more: false`:

Generation: `image_to_3d` / `meshy_image_to_3d`, `multi_image_to_3d` /
`meshy_multi_image_to_3d`, `meshy_v6_text_to_3d`, `meshy_v7_image_to_3d`, `sam_3_3d`,
`sam_3_3d_body`, `tripo_3d`, `tripo_h3_1_image_to_3d`, `tripo_h3_1_multiview_to_3d`,
`hunyuan3d_v3_image_to_3d`, `hunyuan3d_v3_1_text_to_3d` — from Meshy, Meta, Tripo and
Tencent.
Edit/rig: `3d_rigging` / `meshy_rigging`, `meshy_v5_remesh`, `meshy_v5_retexture`.

Constraints are rich and per-model: `target_polycount` 100–300,000, `topology`
`quad|triangle`, `symmetry_mode`, PBR toggles, and a 697-entry animation-clip library
addressed by `animation_action_id`.

So a static list built from the "obvious" enumeration source would have shipped Friday a
capability list missing an entire modality — the phantom-seat bug in its mirror image:
not claiming what cannot be delivered, but **denying what can**.

### 3.5 The two enumeration surfaces disagree — and which one wins

| | Higgsfield CLI `model list` | MCP `models_explore` |
|---|---|---|
| image / video / audio | yes | yes |
| **3D** | **absent entirely** | **17 models** |
| Per-model params | via a second `model get` call | inline on every item |
| Aspect ratios / durations | in `params` enums | inline `aspect_ratios` + `parameters` |
| Requires | the npm CLI installed | the connector Friday already has |

**The MCP is authoritative** and is what the implementation enumerates from:

1. It is the **superset** — it carries everything the CLI does plus 3D.
2. It is **already connected inside Friday** (`~/.friday/mcp_servers.json`), so it adds no
   dependency. The CLI is a separate npm install a user may not have.
3. It is **the same surface generation dispatches through**. One source for listing and
   for calling means the picker cannot drift from what is runnable: if enumeration works,
   dispatch works, and if the connector is down both fail together and say so.

### 3.6 Text — 1 job type

`brain_activity` ("Brain Activity"). Not a chat/orchestrator model; keep it out of
language-model role lists.

### 3.7 Workflows — 19, image and video only

Image: `cinematic_studio_2_5`, `cinematic_studio_image`, `cinematic_studio_soul_cast`,
`cinematic_studio_soul_location`, `image_decompose`, `ms_image`, `marketing_studio_image`,
`soul_cinema_studio`.
Video: `cinematic_studio_video_4_0`, `cinematic_studio_3_0`, `cinematic_studio_video`,
`cinematic_studio_video_3_5`, `cinematic_studio_video_v2`, `draw_to_video`,
`kling3_0_motion_control`, `marketing_studio_video`, `reframe`, `dubbing`, `voice_change`.

### 3.8 Parameter constraints — the picker needs these, and they are machine-readable

`higgsfield model get <job_type> --json` returns `params` (name, type, default, required,
`enum`) plus `rules` — **CEL expressions with human messages**. Measured examples:

- `nano_banana_pro` — `aspect_ratio` enum of 10 values (`1:1` … `21:9`), `resolution`
  `1k|2k|4k` (default `2k`); rule: *"at most 14 image references are allowed"*.
- `seedance_2_0` — `aspect_ratio` 7 values, `resolution` `480p|720p|1080p|4k`,
  `duration` int (default 5), `mode` `std|fast`, `genre` enum of 7, `generate_audio` bool;
  rule capping `image_references + start_image + end_image <= 9`.
- `sonilo_music` — `duration` **required**, `prompt` required. No aspect ratio.
- `seed_audio` — `format` `wav|mp3|pcm|ogg_opus`, `sample_rate` 6 values, plus four
  mutual-exclusion rules (e.g. *"image_references and audio_references are mutually
  exclusive"*).

Constraints vary per model and are published by the vendor. A hand-maintained table would
be wrong within a release. **Fetch them.**

### 3.9 Cost — a 150× spread, per generation

`higgsfield generate cost <job_type> --prompt … --json` returns `{"credits": N}`,
pre-submit. Measured:

| Model | Credits |
|---|---|
| `z_image` | 0.15 |
| `sonilo_music` (10 s) | 0.63 |
| `seedream_v5_pro` | 3 |
| `gpt_image_2` | 7 |
| `kling3_0` | 10 |
| `veo3_1` | 22 |
| `seedance_2_0` | 22.5 |

`higgsfield account status` returns the live balance (`3000.4 credits` at time of
measurement). **Both a pre-submit estimate and a balance endpoint exist** — so cost can be
shown truthfully at the point of choice, and §4.8 of the prior spec ("no balance API
exists") is out of date.

---

## 4. The design

### 4.1 Principle: enumerate, never enshrine

The failure mode to avoid is the phantom `gemma4:26b` seat — config asserting a capability
the system could not deliver. Two ways to reproduce it here, both rejected:

1. **Hardcode a Higgsfield model list.** The prior spec already fell into this: §4.1 names
   `soul/standard`, `dop/standard`, `kling-video/v2.1/pro`. **None of those job_types
   exist in the live catalog.** The list was stale before it was built.
2. **Offer models nothing can dispatch to.** `creative_engine.generate_image`
   (`creative_engine.py:594`) and `generate_video` (`:786`) route to Gemini and local
   ComfyUI. Neither has a Higgsfield branch. Listing 60 Higgsfield models today would make
   every one of them pickable and none of them callable — the exact bug, rebuilt.

So the rule is: **a model appears in the picker only if it was enumerated from the
provider now, and a dispatch path exists for its modality.**

### 4.2 `services/higgsfield_catalog.py` — modeled on `hosted_catalog.py`

Mirror the established pattern (`hosted_catalog.py:1-27`) exactly:

- **Single network seam** (`_explore()`), one monkeypatchable choke point, so tests never
  hit the wire.
- Enumerate via the MCP's `models_explore` per output type (§3.5 explains why this and not
  the CLI), following `next_page_token`. Items carry their own `parameters`,
  `aspect_ratios` and `medias`, so one call per modality yields both the ids and their
  constraints. Write into the **same on-disk discovery cache** that
  `model_discovery.cached_models()` and therefore the picker already read.
- **Stale-while-revalidate**: a failed or empty fetch never clobbers a working cache.
- Surface `catalog_stale` so the UI says *"showing cached list"* rather than presenting a
  stale list as live. **Degrade honestly** — an unreachable provider shows dimmed entries
  with a real reason, never a confident stale lineup.
- Refresh on `POST /api/models/refresh`, matching the existing manual path.

**Filter edit-only job types out of the generation picker.** `image_background_remover`,
`video_upscale`, `outpaint`, `topaz_*`, `clipify`, `video_deflicker`, `sam_3_video` are
post-processors. Offering "Image Background Remover" as your image *generation* model is
its own small lie. They belong on a future edit/enhance surface, tagged
`kind: "edit"`, not in the `creative` role list.

### 4.3 Descriptor: thin, and deliberately not a model list

Add a `higgsfield` provider descriptor carrying **auth, type, capabilities, and roles —
but an empty `models` list**, exactly as the hosted-native path does for Anthropic
(`model_catalog.py:305`): when the live catalog is present it *replaces* statics; when it
is absent the provider still renders, dimmed, with an honest hint. `type: "higgsfield"`
must stay out of `LOCAL_CAPABLE_ADAPTERS` so the egress gate can never classify it local.

Declare `capabilities: ["image", "video", "music", "speech"]` — **including `3d`** (§3.4).

### 4.4 Modality mapping

| Higgsfield type | Friday modality | Role | Surfaces in |
|---|---|---|---|
| `image` (generation only) | `image` | `creative` | Creative Model picker — automatic, `app.html:2088` |
| `video` (generation only) | `video` | `creative` | Creative Model picker — automatic, `app.html:2089` |
| `sonilo_music` | `audio` + `music` | *(none — `music_model`)* | Studio Music panel, matching Lyria's treatment (`provider_registry.py:337`) |
| TTS models | `audio` | `voice` | Voice surface, not the creative picker |
| `brain_activity` | `text` | *(none)* | Model Browser only |

Image and video need **no UI change at all**. Music and speech need their existing panels
pointed at the new entries.

### 4.5 Cost at the point of choice

Given §3.9's 150× spread, a picker that makes these one click away must show cost.

- Carry `credits` per model on the catalog entry, from `generate cost` (cached; it is a
  cheap call but not free). The existing entry shape already has `cost_per_1k` and a
  `note` field — extend rather than invent.
- Show the live balance from `account status` near the picker. Unlike the prior spec's
  assumption, this is available.
- If an estimate call fails, render **"cost unknown"** — never a guessed number.
- Record actual submits to `services/cost_meter.py` under `provider: "higgsfield"`.

### 4.6 Dispatch — the gating dependency

The picker must not outrun the engine. Either:

**(a)** add a Higgsfield branch to `creative_engine.generate_image` / `generate_video`
(a real engine, per `higgsfield-integration.md` §4.1's durable job store), **or**
**(b)** route picked Higgsfield models through the already-working MCP tool path.

**(b) is far cheaper and already proven in production** — the MCP connector has generated
real output on this machine. Recommend (b) for the first landing, with (a) as the
follow-on when a durable job store exists. Whichever is chosen, ship it *with* the picker
change, not after.

---

## 5. Cost of the fix

| Piece | Size | Risk |
|---|---|---|
| `services/higgsfield_catalog.py` (enumerate + cache + stale flags) | ~200 lines, patterned on `hosted_catalog.py` | Low — additive, new file |
| Provider descriptor (empty `models`, hosted-native path) | ~25 lines in `provider_registry.py` | Low |
| Hosted-native branch extended past `ptype == "anthropic"` (`model_catalog.py:305`) | ~5 lines | Low |
| Edit-vs-generation classification | ~20 lines | Low |
| Cost/balance surfacing | ~40 lines | Low |
| Dispatch, option (b) | ~60 lines | **Medium — the load-bearing piece** |
| UI | **zero** for image/video | — |
| Tests | catalog-shape + a `test_no_hardcoded_model_lists`-style guard | Low |

**No file overlaps the mail-triage/message-center work or the residency-planner/task-registry
work.** `ui_parts/app.html` is untouched by this design, which is the one file where a
collision with the in-flight Opus 5 UI run was plausible.

---

## 6. Corrections to `higgsfield-integration.md`

Three claims in the prior spec are now measurably wrong and should not be built from:

1. **§2.7** — *"no audio/music or 3D model endpoint appears anywhere in the public docs"*,
   with **Q2** asking what the account actually lists. **Q2 is now answered:** audio
   exists (6 models, §3.3), music exists (`sonilo_music`), **3D does not** (§3.4).
2. **§4.1** — the hardcoded `soul/standard` / `dop/standard` / `kling-video/v2.1/pro`
   descriptor. **Those job_types do not exist.** Replaced by §4.3's empty-list descriptor.
3. **§4.8** — *"no balance API exists"*. **It does**: `account status`, plus a pre-submit
   `generate cost` estimate (§3.9).

### 6.1 And a correction to this document

The first draft of §3.4 stated flatly that **3D was not present on the account**, citing
the CLI's absent `--3d` flag and its `image | video | audio | text` type list. That was
wrong: there are 17 3D models (§3.4). The claim was carefully sourced, internally
consistent, and false, because it generalised from one enumeration surface to "the
account". It is left in the record rather than quietly edited away, because it is the
cheapest available demonstration of why §4.1's rule is worth the code: **a list is a
snapshot of one vantage point at one moment, and the only defence is to ask the provider
at the time of asking.**

---

## 7. Decisions taken, and what is still open

Settled during the build (Stephen: *"I trust your judgement"*):

- **Q-C1 → option (b).** Dispatch routes through the MCP connector, not a second HTTP
  engine. One surface for listing and calling; they cannot drift apart.
- **Q-C2 → voice.** The TTS models carry `ROLE_VOICE` and appear on the voice surface, not
  in the creative picker. They are speech, and the creative picker is for image/video/3D.
- **Q-C3 → catalogued, never a generation pick.** Edit/enhance models keep `roles: []`, so
  they are visible in the Model Browser and excluded from every role picker. When an
  edit/enhance surface exists they are already tagged `kind: "edit"` and ready for it.
- **Q-C4 → 24 h**, matching `hosted_catalog.STALE_AFTER_S`. One convention, not two.

Still open, and genuinely his call:

- **Q-C5.** A per-generation credit cap before submit, given the 150× spread. Not built:
  a spend limit is a policy question about his money, not an implementation detail.
- **Q-C6.** Should `sonilo_music` become the Studio Music panel's default, or does Lyria
  keep it? It is catalogued with `modalities: ["audio","music"]` and ready either way.
  Note Higgsfield tags it *"Game pipeline only"*.
- **Q-C7.** 3D is now enumerated into the creative picker, but Friday has no 3D *viewer* —
  a picked 3D model saves a GLB to disk and nothing renders it. Worth a surface?

---

## 8. How every claim here was verified

`@higgsfield/cli@1.1.23`, authenticated, 2026-08-24:
`higgsfield model list --{image,video,audio,text} --json` (67 job types),
`higgsfield workflow list --json` (19), `higgsfield model get <job_type> --json`
(params/rules for `nano_banana_pro`, `seedance_2_0`, `sonilo_music`, `seed_audio`),
`higgsfield generate cost <job_type> --json` (7 models), `higgsfield account status`,
and the MCP connector's `models_explore` (`type:'3d'` — 17 items, `has_more:false`;
`type:'audio'` music search — 1 item). Repository claims are cited file:line inline and
were read on `higgsfield-integration`.

---

## 9. What was built

### 9.1 Files

| File | Change |
|---|---|
| `services/higgsfield_catalog.py` | **new.** Enumerates via MCP `models_explore` per output type, paginates, classifies, normalises into the shared discovery cache. Single seam `_explore()`. |
| `services/higgsfield_generate.py` | **new.** Dispatch: cost preflight → submit → poll to terminal → pull bytes to disk via `creative_store`. Single seam `_call()`. |
| `services/provider_registry.py` | `higgsfield` descriptor with an **empty `models` list**; availability probed against the live connector, not asserted from config. |
| `services/model_catalog.py` | `HOSTED_NATIVE_TYPES` replaces the `== "anthropic"` test; enumerated entries may now carry their own `roles`, `kind`, `note` and `constraints`; honest hint when the connector is down. |
| `services/creative_engine.py` | Higgsfield branches in `generate_image` / `generate_video`, before the Gemini availability check; new `_configured_video_model()`. |
| `services/hosted_catalog.py` | `catalog_meta()` now covers hosted-native providers, so a stale Higgsfield cache shows in the staleness banner. |
| `routes/core_routes.py` | `POST /api/models/refresh` accepts `higgsfield` and includes it in the refresh-all sweep. |
| `ui_parts/app.html` | **untouched.** Image and video surface automatically. |

### 9.2 Verified behaviour

Run against the real measured payloads with an isolated cache:

```
CREATIVE picker gets: nano_banana_pro, z_image, seedance_2_0, image_to_3d
VOICE picker gets:    qwen_audio_tts
music (Studio panel, roles=[]): sonilo_music
excluded from every picker (kind=edit): image_background_remover,
                                        video_upscale, 3d_rigging
```

Connector down with an empty cache: **0 models named**, provider row present and dimmed,
`is_stale() == True`. It declines to invent a lineup rather than showing a stale one.

### 9.3 Tests

`tests/unit/test_higgsfield_catalog.py` (23), `tests/unit/test_higgsfield_generate.py`
(17), and 3 added/updated in `tests/api/test_models_refresh_route.py` — 43 passing.
They pin, among others:

- the descriptor's `models` list is **empty** (AST-checked, so a convenience literal fails
  loudly — the same shape as `test_no_hardcoded_model_lists.py`);
- the phantom ids from the prior spec cannot return as literals;
- post-processors never enter a role list; music and speech never share a bucket;
- a failed **or empty** enumeration never clobbers the cache;
- a partial sweep reports as partial, not as a clean one;
- unknown cost is `None`, never a guessed number;
- a finished-but-unsaved generation reports its URL, credits and job id, because the
  vendor has already charged for it and the file expires in seven days;
- the free-trial allowance is never spent on the user's behalf (`use_unlim` pinned false).

### 9.4 Known gaps

- **Music and speech need their panels pointed at the new entries.** The catalogue carries
  them correctly; the Studio Music panel and the voice surface still read their old
  sources. Image, video and 3D need nothing.
- **No 3D viewer** (Q-C7): a picked 3D model writes a GLB to disk and nothing renders it.
- **Cost is carried, not yet drawn.** Entries carry `credits`; the picker does not display
  them yet — that is an `app.html` change, deliberately deferred while another session
  holds that file.
- The wider suite carries **pre-existing failures, none caused by this work** — confirmed
  by running it at clean HEAD in a detached worktree and diffing the failure sets:
  17 failures at baseline (random order), 11 here (fixed order), and the second set is a
  strict subset of the first.

  Most are **test-order pollution**, not product bugs: `test_local_image.py` (26/26),
  `test_egress_gate.py` (24/24) and `test_residency_arbiter.py` (30/30) each pass fully in
  isolation and fail only in combination.

  The exception is real and is filed as a task: **`test_vault_access.py` fails standalone**
  — `classify("emergency contact details")` returns Tier 1 instead of `Tier.PRIVATE`, and
  `gate_content("emergency contact: 555-1234", "anthropic")` returns the phone number
  verbatim. Same classifier that makes the egress "SSN" symptom appear under load.

### 9.5 One regression this work caused, and its fix

`test_models_refresh_route.py::test_refresh_route_all_providers` asserted
`set(results) == set(HOSTED_PROVIDERS)`. Adding Higgsfield to the refresh-all sweep put a
third key in that dict and broke the assertion. The behaviour is intended — refreshing
"all" should include Higgsfield — so the test was updated to the new contract rather than
the behaviour narrowed, and two cases were added alongside it: Higgsfield refreshing
alone, and an unreachable connector leaving a populated cache untouched.
