# Agent Friday — Content Pipeline Specification

**FutureSpeak.AI · Asimov's Mind**

| | |
|---|---|
| **Status** | SPEC — design document. Nothing below is implemented yet except where explicitly marked *(exists)*. |
| **Version** | 1.0 · 2026-07-04 |
| **Scope** | A full social-media management system built into the sovereign AI desktop OS: create → compose → schedule → publish → monitor → learn, across eleven platforms plus the Friday Federation. |
| **Bridges** | Studio (creative engines) → the outside world. Companion to the Creator Economy & Federation design (Layers 1–3, all built) and the model-agnostic provider architecture (provider registry + capability routing). |

---

## 0. Executive Summary

Friday can already **make** things: images and video (`services/creative_engine.py`), music (`services/music_engine.py`), timelines and platform-preset exports (`services/timeline_engine.py`), decks and websites (`services/showcase_engine.py`), text and code art. She can already **prove** she made them (`services/provenance.py`, `services/content_credentials.py`, `services/ownership.py`) and **sell** them to other agents (`services/marketplace.py`, `services/economy.py`, `services/federation.py`).

What she cannot do is **ship them to where human audiences actually live**. The current Content workspace is an ideation kanban (idea → drafting → review → scheduled → published) whose "published" stage is a label, not an event — nothing ever leaves the machine. The Content Pipeline closes that loop:

```
make it (Studio) → shape it per platform (Composer) → time it (Scheduler)
→ ship it (Publisher) → measure it (Analytics) → get better at it (Learning)
```

### Sovereignty invariants (non-negotiable)

These carry Friday's existing guarantees through the new surface. Every design decision below is checked against them.

1. **Everything outbound passes the egress gate.** A social platform is an egress, exactly like a cloud model or a chat channel (`services/channels/manager.py` set the precedent). Fail-closed: a post that cannot be safety-checked is **held**, never silently published.
2. **Platform credentials never touch disk in plaintext.** OAuth tokens ride the existing `services/credential_store.py` (vault AES-256-GCM → Windows DPAPI → loud plaintext fallback), same as Google account tokens and channel bot tokens.
3. **Every published piece carries provenance.** The Ed25519-signed ContentCredential created at generation time extends through publication: the post itself becomes a signed distribution event in the ownership registry, and credentials are embedded in asset metadata wherever the platform preserves it.
4. **The harm floor applies at the source.** `services/moderation.py` (H1–H4) scans every outbound post before any platform API is called. Harm is blocked; taste is not policed — NSFW-tagged work maps to each platform's sensitivity flag instead of being refused.
5. **No platform ever sees the vault.** Adapters receive a finished, gated `PreparedPost` — never conversation history, never wiki content, never memory. The analytics collector treats everything a platform returns as untrusted data, never as instructions.
6. **Local-first analytics.** Engagement data lands in a local SQLite store and feeds a local heuristic learning loop (`services/learning_loop.py` pattern). No third-party analytics service, no telemetry.
7. **Friday publishes; she does not manipulate.** No auto-follows, auto-likes, engagement pods, or reply automation. Out of scope permanently — both platform-ToS poison and contrary to cLaws.

---

## 1. Research — STORM Six-Perspective Synthesis

Per the STORM method, the design was interrogated from six expert perspectives before synthesis. Each subsection records the questions that perspective asked, what the research found, and the design consequences that survive into the spec.

### 1.1 The Content Creator

**Asked:** Where does my work actually come from? How many times do I have to describe the same piece? Will posts sound like me or like an AI? Do I keep ownership?

**Findings.** Creators abandon tools that make them re-enter work. Friday's creations already live in one gallery (`CREATIONS_DIR`, surfaced by Studio) with metadata sidecars and signed manifests — the pipeline must treat that gallery, plus news editorials, briefings, and wiki drafts, as the native inbox. Voice is the second abandonment driver: generic AI copy is instantly recognizable and quietly damages a personal brand. Friday already solves voice at the system level — `services/soul.py` (`SOUL.md` personality, folded into every system prompt), `services/user_model.py` (learned communication style), and a proven per-format voice pattern in `services/misc_engine.py` (`DRAFT_MODE_PROMPTS`: the LinkedIn ghostwriter and sub-280-char tweet prompts).

**Consequences:** Quick-Post entry points on every creation and story (§10.2); composition always runs through `_get_friday_system_prompt()` so SOUL.md and the user model shape every adaptation (§5.6); per-platform voice cards the user can edit (§5.6); license and provenance travel with the post (§7.7).

### 1.2 The Social Media Manager

**Asked:** Can I see my whole week at a glance? Can one piece become ten posts? What happens when a post fails at 3 a.m.? How do I know what time to post? Can I A/B a hook?

**Findings.** Professional tools (Buffer, Hootsuite, Later) converge on four primitives: a composer with per-network preview, a calendar with drag-to-reschedule, a queue with statuses, and unified analytics. Their most-used single feature is cross-posting with per-network tailoring — not naive duplication. Failure handling is where cheap tools die: a failed post must retry with backoff, then notify loudly with a one-tap reschedule. Optimal timing matters but generic "best time" tables are weak; learned per-account times beat them within weeks.

**Consequences:** the five-tab workspace (§10.1); the repurposing engine as a first-class subsystem, not a checkbox (§9); retry/backoff + high-priority notification with a Reschedule action, reusing the scheduler's retry envelope (§7.5); best-practice seed tables that yield to learned per-account times (§6.4); A/B variants with per-variant attribution (§3.1, Phase 7).

### 1.3 The Platform API Expert

**Asked:** What can actually be automated in 2026, at what cost, with what auth, and what breaks first?

**Findings.** The eleven targets split into four tiers:

- **Open protocols** (Bluesky/AT Protocol, Mastodon/ActivityPub): free, documented, generous limits, no app review. Mastodon even schedules natively server-side.
- **Gated-but-workable APIs** (LinkedIn, YouTube, Reddit, Instagram, TikTok): OAuth2 with app registration; each has one sharp constraint — YouTube's 1,600-unit upload cost against a 10,000/day default quota, Instagram's public-URL media containers and 100-posts/24h cap, TikTok's unaudited-app private-only publishing, LinkedIn's undocumented member throttles, Reddit's per-subreddit rulebooks.
- **Pay-to-play** (X/Twitter): the API works, but write access is metered by paid tier and pricing has changed repeatedly; the adapter must budget locally and degrade gracefully.
- **No API** (Substack, Medium-for-new-integrations): assisted handoff or opt-in headless browser only. Never pretend these are automated.

Every platform strips or mangles asset metadata differently; C2PA Content Credentials survive on LinkedIn and TikTok (both display them), partially on YouTube, and not at all on most others — so provenance must never *depend* on the platform (the local sidecar + ledger is the source of truth, per `services/provenance.py`).

**Consequences:** the adapter contract with explicit `capabilities()` and a degradation ladder (§4.1, §4.14); local rate-budget bookkeeping per adapter (§4.1); a staging mechanism for URL-pull platforms (§7.4); embedded-credential best-effort + authoritative local ledger (§7.7); "verify current pricing" flags on volatile tiers (§4.4).

### 1.4 The Privacy Advocate

**Asked:** What leaves the machine, exactly, and who approved it? Where do tokens live? What does a platform learn about the user beyond the post? Can the user leave?

**Findings.** Three real risks: (a) accidental PII in post bodies — a draft that quotes an email, a screenshot with an address; (b) token theft from disk; (c) the reverse channel — platform responses (comments, analytics payloads, error messages) flowing into LLM context as a prompt-injection vector. Friday already has the machinery for all three: the sensitivity classifier + egress gate with fail-closed semantics, the encrypted credential store with audit log, and a hard house rule that observed content is data, not instructions.

**Consequences:** every final post body is classified before send; anything non-PUBLIC → `HELD` for explicit human release, never auto-redacted into a mangled public post (§7.3); tokens in the credential store with `audit_event()` on connect/publish/disconnect (§12.2); scope transparency in the Accounts tab in plain language (§10.1); analytics payloads sanitized and schema-validated before storage, never fed raw into prompts (§8.6); one-tap disconnect = token revoke (where the platform supports it) + local purge; full local data export (§12.6).

### 1.5 The UX Designer

**Asked:** Where does this live in the OS? What is the two-click path from "made a thing" to "scheduled everywhere"? How does the user trust what will actually appear?

**Findings.** The dock already reserves `content` (📝) — the workspace exists as a stub with a kanban the user understands. The strongest existing interaction patterns to reuse: `StudioPromptBar` (type chips + one prompt + options drawer, posting straight to engines, never through the agent loop), the `SendTo` flow-menu (`POST /api/flow` with pluggable destinations), `ProvenanceBar` on creation detail, process orbs for anything long-running, `useNavTarget` deep links from notifications, and `FridaySays` context lines. Per-platform preview is the trust moment — the user must see the truncation, the thread split, and the cropped 4:5 image *before* it ships. Accessibility is a differentiator: alt-text nudges at compose time (LinkedIn, X, Bluesky, Mastodon all accept alt text via API).

**Consequences:** ContentWS v2 keeps the kanban as an **Ideas** tab and adds Compose / Calendar / Queue / Analytics / Accounts (§10.1); preview panes render platform-native mockups with hard-limit meters (§5.5); Quick-Post modal reachable from any workspace via `SendTo` + a Share button on creations (§10.2); alt-text fields with a nag state (§5.5); every publish surfaces an orb and a completion notification that deep-links to the Queue tab (§7.6).

### 1.6 The Monetization Strategist

**Asked:** How does publishing feed the creator economy instead of bypassing it? What does the federation get that LinkedIn doesn't?

**Findings.** The economy layer already defines the hooks: `PSI_CREATE_CONTENT` (10ψ per creation) and `PSI_LIKE` (1ψ per like/share received) in `services/economy.py`; the marketplace already lists assets with license + `price_mpsi`; federation transport already speaks `CONTENT_OFFER`. External platforms are *reach*; the federation is *revenue and rights*. The differentiated story: a post published everywhere simultaneously lists on the federation marketplace with real license terms and ψ pricing, and its provenance chain proves ownership no matter where a copy surfaces. Cross-platform engagement translating into ψ earnings makes the wallet reflect actual creative output.

**Consequences:** federation is a peer `PlatformTarget`, not an afterthought (§4.13, §13); publish events optionally auto-list on the marketplace with the piece's existing license (§13.2); analytics deltas mint ψ via the existing earn API, bounded and idempotent (§8.7); the provenance chain extends creation → post → listing → transfer (§13.4).

### 1.7 Synthesis — the ten load-bearing decisions

| # | Decision | Driven by |
|---|----------|-----------|
| D1 | Platform adapters mirror the `services/channels/` package pattern (base contract + manager/registry), but outbound | 1.3, codebase symmetry |
| D2 | The publisher is a **builtin scheduler task** (1-minute scan of the content DB), not N ad-hoc schedules | 1.2, §6.2 rationale |
| D3 | Egress gate result on a post body is **hold-for-review**, never silent redaction into public text | 1.4 |
| D4 | Harm-floor moderation scan precedes every publish; NSFW maps to platform sensitivity flags | 1.4, cLaws §25 philosophy |
| D5 | Composition is engine-direct (like `StudioPromptBar` → `/api/create/<type>`), through `_generate_text` + `_get_friday_system_prompt(workspace="content")` — never the autonomous agent loop | 1.5, Studio miswiring lesson |
| D6 | Previews are contract renderings of the exact `PlatformTarget` payload — what you see is byte-what-ships | 1.5 |
| D7 | Tier-4 platforms (Substack, Medium) are **assisted handoff by default**, headless automation strictly opt-in | 1.3, 1.4 |
| D8 | Analytics are pulled on a decaying poll schedule, normalized into one metrics shape, stored locally, and treated as untrusted input | 1.3, 1.4 |
| D9 | Optimal times: seeded from static best-practice tables, replaced by learned per-account, per-platform histograms | 1.2 |
| D10 | ψ accrual from engagement uses existing `economy.earn()` constants, idempotent per (post, metric, threshold) | 1.6 |

---

## 2. Architecture Overview

### 2.1 The pipeline

```
             CREATE                    COMPOSE                     SCHEDULE
  ┌─────────────────────────┐  ┌──────────────────────┐  ┌──────────────────────────┐
  │  Studio                 │  │  content_composer.py │  │  scheduler.py (exists)   │
  │   creative_engine (img/ │  │                      │  │   + content_publisher    │
  │   video) · music_engine │  │  raw piece + targets │  │     builtin task         │
  │   timeline_engine       │─▶│   → per-platform     │─▶│     (1-min DB scan)      │
  │   showcase_engine       │  │     native versions  │  │                          │
  │  News (editorials,      │  │  SOUL.md voice       │  │  ContentPost.schedule    │
  │   briefings) · Wiki     │  │  hashtags · previews │  │   publish_at (UTC) · tz  │
  │  Chat / Quick-Post      │  │  variants (A/B)      │  │   recurrence · optimal   │
  └─────────────────────────┘  └──────────────────────┘  └────────────┬─────────────┘
              ▲                                                       │ due
              │                                                       ▼
  ┌───────────┴─────────────┐  ┌──────────────────────┐  ┌──────────────────────────┐
  │          LEARN          │  │       MONITOR        │  │         PUBLISH          │
  │  learning_loop.observe  │  │ analytics_collector  │  │  publisher.py            │
  │   (content_publish)     │  │  .py                 │  │   1 moderation.scan H1-H4│
  │  best-time histograms   │◀─│  decaying polls per  │◀─│   2 egress gate (hold)   │
  │  trend insights →       │  │  platform → unified  │  │   3 adapter.prepare      │
  │   composer heuristics + │  │  EngagementMetrics   │  │   4 adapter.publish      │
  │   Analytics tab cards   │  │  ψ earn on deltas    │  │   5 provenance + log     │
  └─────────────────────────┘  └──────────────────────┘  └────────────┬─────────────┘
                                                                      │
                                        ┌─────────────────────────────┴──────────────┐
                                        │       services/platforms/* adapters        │
                                        │ linkedin │ x │ instagram │ youtube │ tiktok │
                                        │ bluesky │ mastodon │ reddit │ substack     │
                                        │ medium │ federation_pub (marketplace + ψ)  │
                                        └────────────────────────────────────────────┘
```

### 2.2 Integration with existing systems

| Existing system | Module *(exists)* | Role in the pipeline |
|---|---|---|
| Studio engines | `services/creative_engine.py`, `music_engine.py`, `timeline_engine.py`, `showcase_engine.py` | Source of assets. `timeline_engine.EXPORT_PROFILES` already ships `youtube-16x9`, `instagram-reel`, `tiktok-vertical`, `gif-preview`, `audio-mp3` — the repurposing engine calls `compose()` per required profile instead of re-implementing transcodes. |
| Internal scheduler | `services/scheduler.py` | Hosts the `content_publisher` builtin task (registered via `register_builtin_task`), the `content_analytics` poll task, and the weekly `content_insights` job. Run history, retries, orbs, and notification plumbing come free. |
| Channel bridges | `services/channels/` | Architectural template for `services/platforms/` (base-class contract, manager registry, token-via-credential-store, status envelopes). Discord/Telegram bridges double as bonus **announce** targets (§4.13). |
| Egress gate | `services/egress_gate.py` | `gate_text(body, provider=f"platform_{name}", field="post.body")` on every outbound text; classifier verdict drives the `HELD` state (§7.3). Composition prompts to cloud models are gated exactly as `creative_engine` gates image prompts today. |
| Sensitivity classifier | `services/sensitivity_classifier.py` | The single source of tier truth backing the gate; `Tier.PUBLIC` is the only tier that publishes without a human release. |
| Provenance / credentials | `services/provenance.py`, `content_credentials.py` | Assets are already signed at generation. The publisher adds a signed publication entry per platform and embeds credentials in outbound copies where formats allow (§7.7). |
| Ownership registry | `services/ownership.py` | Post → asset linkage; publication recorded as a distribution event on the asset's record; `check_license_compat()` guards republishing purchased/CC works. |
| Marketplace + economy | `services/marketplace.py`, `economy.py` | Federation publishing = `create_listing()`; engagement→ψ via `earn()` with the shipped constants (`PSI_CREATE_CONTENT`, `PSI_LIKE`). |
| Federation | `services/federation.py`, `federation_transport.py` | Peer discovery + encrypted `CONTENT_OFFER` messages for the federation adapter. |
| Moderation | `services/moderation.py`, `content_policies.py` | H1–H4 harm scan pre-publish; policy-pack tags (e.g. `nsfw`) map to platform flags. |
| Credential store | `services/credential_store.py` | Encrypted OAuth blobs (`write_secret`/`read_secret`), simple keys via `set_provider_key("platform_<name>", …)`, `audit_event()` trail. |
| Model routing | `services/model_router.py`, `capability_router.py`, `provider_registry.py` | All text adaptation via `_generate_text(...)` under the `reasoning` capability; provider-agnostic by construction; cost attributed via `cost_meter` with `workspace="content"`. |
| Personality & user model | `services/soul.py`, `user_model.py` | Voice injection through `_get_friday_system_prompt(workspace="content")` (§5.6). |
| Learning loop | `services/learning_loop.py` | `observe(task_type="content_publish", …)` per published post outcome; promoted heuristics feed back into composer prompts (§8.5). |
| Notifications & orbs | `services/notifications.py` (via `voice_engine._notif_engine`), `core.process_*` | Publish/failure/held notifications with deep-link actions; orbs for compose, publish, and collect runs. |
| Legacy content stub | `routes/workflows.py` (`/api/content/*`), `ContentWS` in `ui_parts/app.html` | Becomes the **Ideas** tab; its items graduate into `ContentPost` drafts (§3.4 migration). |

### 2.3 Data flow — raw asset to analytics

1. **Create** *(exists)*. `creative_engine.generate_image()` writes `friday-image-….png` into the creations folder, a metadata sidecar into `~/.friday/creations_meta/`, and a signed manifest into `~/.friday/provenance/<hash>.jsonld` + ledger. The piece carries a per-piece license chosen at creation.
2. **Draft.** The user (or a schedule, or Quick-Post) creates a `ContentPost` referencing the asset by filename/content-hash, picks target platforms, and writes or requests a base body. Post state: `DRAFT`.
3. **Compose.** `content_composer.adapt(post)` produces one `PlatformTarget` per platform: adapted body inside the char budget, platform hashtags, thread segmentation, asset transform plan (e.g. 4:5 crop for Instagram, 9:16 reel via `timeline_engine`), preview payloads. Variants multiply targets. All LLM calls go out gated.
4. **Schedule.** `ScheduleConfig` resolves `publish_at` (explicit datetime, or `optimal_time=True` → best-times engine). Conflict detection warns on same-platform posts within the configurable window. State: `SCHEDULED`.
5. **Publish.** The `content_publisher` scheduler tick claims due targets (mark-before-run, like `scheduler.dispatch`). Per target: harm scan → egress classification (hold if non-public) → `adapter.prepare()` (assets transformed + uploaded) → `adapter.publish()` → `post_url` + `platform_post_id` recorded; publication entry appended to `publish_log.jsonl` and signed into the asset's provenance; ψ earn fires once. Target states: `PENDING → SENT → CONFIRMED | FAILED`; post rolls up to `PUBLISHED | PARTIAL | FAILED | HELD`.
6. **Monitor.** `analytics_collector` schedules polls at +1 h, +6 h, +24 h, +3 d, +7 d, +30 d per confirmed target; each poll normalizes platform metrics into `PlatformEngagement`, stores a snapshot row, and updates the post's rolled-up `EngagementMetrics`. Engagement deltas mint ψ (bounded, idempotent).
7. **Learn.** The weekly `content_insights` job aggregates snapshots into best-time histograms and content-attribute trends ("video posts get 4.1× the engagement of text"), records `learning_loop` observations, and surfaces insight cards + notifications. `optimal_time` scheduling and composer heuristics consume the results.

### 2.4 New modules

```
src/agent_friday/services/
  content_pipeline.py          # data model, content_pipeline.db store, status machine
  content_composer.py          # §5 — adaptation engine
  publisher.py                 # §7 — publication engine (scheduler builtin + dispatch)
  analytics_collector.py       # §8 — engagement polling, normalization, insights
  platforms/
    __init__.py                # registry + lifecycle (mirrors channels/manager.py)
    base.py                    # PlatformAdapter contract (§4.1)
    linkedin.py  x_twitter.py  instagram.py  youtube.py  tiktok.py
    bluesky.py   mastodon.py   reddit.py     substack.py  medium.py
    federation_pub.py          # §4.13 / §13
src/agent_friday/routes/
  content_pipeline.py          # Blueprint: /api/content/* v2 routes (§11)
ui_parts/app.html              # ContentWS v2, QuickPost modal, SendTo destination (§10)
tests/unit/test_content_*.py   # §15
tests/api/test_content_routes.py
```

Design rules inherited from the tree: leaf-ish modules with lazy imports, import-safe under `FRIDAY_TESTING`, SQLite WAL + `threading.RLock`, public helpers return envelopes and never raise, no secrets in source, process orbs best-effort.

---

## 3. Content Data Model

Lives in `services/content_pipeline.py`. Follows the tree's storage conventions: SQLite at `~/.friday/content_pipeline.db` (WAL, no ORM), ISO-8601 UTC strings for instants plus the post's IANA timezone for display math, envelopes out of every public helper.

### 3.1 Entities

```
ContentPost:
  id: str                     # "post_" + uuid4 hex[:10]
  title: str                  # working title (internal; some platforms use it)
  body: str                   # canonical body, markdown; the composer's source
  assets: list[AssetRef]      # ordered; images/videos/audio from Studio
  platforms: list[PlatformTarget]
  schedule: ScheduleConfig
  status: DRAFT | SCHEDULED | PUBLISHING | PUBLISHED | PARTIAL | HELD |
          FAILED | CANCELLED
  variants: list[ContentVariant]      # A/B alternates (Phase 7 active use)
  source: dict                # where it came from: {kind: creation|news|wiki|
                              #   idea|chat|repurpose, ref: filename|id, ...}
  provenance: dict            # {content_hash, manifest_ok: bool} — link into
                              #   ~/.friday/provenance/; posts themselves get a
                              #   signed publication entry per target (§7.7)
  license: dict               # normalized per provenance.normalize_license()
  analytics: EngagementMetrics        # rolled up across targets (denormalized)
  tags: list[str]             # moderation/user tags (nsfw, politics, …)
  created_at, updated_at, published_at: str | None   # ISO-8601 UTC

AssetRef:
  filename: str               # gallery name (creations folder)
  content_hash: str           # "sha256:…" — joins ownership + provenance
  kind: image | video | audio | document
  alt_text: str               # composer nags until set for image targets
  duration_s: float | None    # av assets

PlatformTarget:
  id: str                     # "tgt_" + uuid4 hex[:10]
  platform: linkedin | twitter | instagram | youtube | bluesky | mastodon |
            reddit | substack | medium | tiktok | federation | discord | telegram
  format: post | thread | story | reel | short | article | video_upload |
          image_post | link_post | listing | announce
  variant_id: str | None      # which ContentVariant this target carries
  adapted_title: str          # platforms with a title field (YouTube, Reddit, …)
  adapted_body: str           # platform-native text; threads → segments[]
  segments: list[str]         # thread mode: ordered post bodies
  adapted_assets: list[dict]  # transform plan + results:
                              #   {source: AssetRef, profile: "instagram-reel",
                              #    out_path, platform_media_id, alt_text}
  char_limit: int             # resolved from adapter.capabilities() at compose
  hashtags: list[str]
  mentions: list[str]
  options: dict               # per-platform extras: subreddit, flair_id,
                              #   visibility, sensitive, spoiler_text,
                              #   privacy_status, category_id, price_mpsi, …
  publish_at_override: str | None     # stagger targets off the post time
  status: PENDING | PREPARING | SENT | CONFIRMED | HELD | FAILED | SKIPPED
  attempt: int                # retry bookkeeping
  not_before: float           # epoch; backoff gate (scheduler convention)
  post_url: str | None        # canonical URL after publication
  platform_post_id: str | None
  error: str | None
  engagement: PlatformEngagement

ScheduleConfig:
  publish_at: str | None      # ISO-8601 UTC; None + optimal_time → resolver picks
  timezone: str               # IANA, default settings.timezone or America/Chicago
  recurrence: none | daily | weekly | custom_cron   # cron: 5-field, minute floor
  recurrence_spec: dict       # {weekday, hour, minute} | {cron: "0 9 * * 2"}
  optimal_time: bool          # let Friday pick from best-times (§6.4)
  expires_at: str | None      # recurring posts stop after this instant

ContentVariant:
  id: str                     # "var_" + uuid4 hex[:6]
  label: str                  # "Hook A — question lead"
  body: str
  title: str | None
  weight: float               # target-assignment share, default 0.5

EngagementMetrics:             # unified shape — §8.2 defines per-platform mapping
  impressions, reach, likes, comments, shares, saves, clicks: int
  video_views: int
  watch_time_s: int
  follows_gained: int
  engagement_rate: float      # (likes+comments+shares+saves+clicks) / max(impressions, views)
  collected_at: str
  per_platform: dict[platform, PlatformEngagement]

PlatformEngagement:            # one target's normalized numbers + the raw payload
  target_id, platform: str
  metrics: dict               # unified keys above, missing = platform can't report
  raw: dict                   # sanitized platform payload (audit/debug)
  collected_at: str
```

### 3.2 Post status machine

```
                       ┌──────────── edit ────────────┐
                       ▼                              │
 DRAFT ──schedule──▶ SCHEDULED ──due──▶ PUBLISHING ──all targets CONFIRMED──▶ PUBLISHED
   │                   │                   │ │
   │                   └──cancel──▶ CANCELLED │──any HELD (gate) ────▶ HELD ──user release──▶ PUBLISHING
   └──publish now──────────────────▶          │──some ok, some dead ─▶ PARTIAL
                                              └──all dead ───────────▶ FAILED ──reschedule──▶ SCHEDULED
```

Terminal states: `PUBLISHED`, `PARTIAL`, `FAILED`, `CANCELLED` (a `PARTIAL`/`FAILED` post can be re-armed, which re-opens only non-`CONFIRMED` targets — never double-posts a confirmed one). `HELD` is *sticky-safe*: nothing publishes until a human releases or edits, and holds expire to `CANCELLED` after 7 days unattended.

### 3.3 SQLite schema (`~/.friday/content_pipeline.db`)

```sql
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    title         TEXT, body TEXT,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    schedule_json TEXT,            -- ScheduleConfig
    assets_json   TEXT,            -- list[AssetRef]
    variants_json TEXT,
    source_json   TEXT, license_json TEXT, tags_json TEXT,
    provenance_hash TEXT,          -- primary asset content_hash ("" for text-only)
    analytics_json  TEXT,          -- rolled-up EngagementMetrics (denormalized)
    created_at TEXT, updated_at TEXT, published_at TEXT
);
CREATE TABLE IF NOT EXISTS targets (
    id            TEXT PRIMARY KEY,
    post_id       TEXT NOT NULL REFERENCES posts(id),
    platform      TEXT NOT NULL, format TEXT, variant_id TEXT,
    payload_json  TEXT,            -- adapted_* + segments + options + hashtags
    status        TEXT NOT NULL DEFAULT 'PENDING',
    attempt       INTEGER DEFAULT 0, not_before REAL DEFAULT 0,
    publish_at    TEXT,            -- resolved instant for THIS target (post time
                                   -- + override); the publisher scans this column
    post_url TEXT, platform_post_id TEXT, error TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS engagement_snapshots (
    id         TEXT PRIMARY KEY,
    target_id  TEXT NOT NULL REFERENCES targets(id),
    post_id    TEXT NOT NULL, platform TEXT NOT NULL,
    metrics_json TEXT NOT NULL,    -- normalized
    raw_json     TEXT,             -- sanitized platform payload
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS best_times (          -- learned histograms (§6.4, §8.4)
    platform  TEXT NOT NULL, weekday INTEGER NOT NULL, hour INTEGER NOT NULL,
    score REAL DEFAULT 0, samples INTEGER DEFAULT 0, updated_at TEXT,
    PRIMARY KEY (platform, weekday, hour)
);
CREATE TABLE IF NOT EXISTS psi_awards (          -- idempotent engagement→ψ (§8.7)
    key TEXT PRIMARY KEY,          -- "{target_id}:{metric}:{threshold}"
    amount_mpsi INTEGER, awarded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_due    ON targets(status, publish_at);
CREATE INDEX IF NOT EXISTS idx_targets_post   ON targets(post_id);
CREATE INDEX IF NOT EXISTS idx_snaps_target   ON engagement_snapshots(target_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_posts_status   ON posts(status, updated_at);
```

Plus an append-only `~/.friday/content/publish_log.jsonl` (rotation like `schedule_runs.jsonl`): one line per publish attempt with target id, platform, outcome, duration, URL, and error — powering the Queue tab's history and the audit story.

### 3.4 Migration from the v1 Content stub

The existing kanban (`/api/content/pipeline` in `routes/workflows.py`, items with `stage`, `channel`, `draft`, `scheduled_for`) stays alive as the **Ideas** tab. One-way graduation: an "→ Compose" action on any item creates a `ContentPost` (`source: {kind:"idea", ref:item.id}`, body = item.draft, platform pre-selected from item.channel) and advances the item to its `published` stage with a link to the post. Nothing is deleted; no schema change to the legacy JSON store. `~/.friday/wiki/content/` saved drafts remain readable as compose sources.

---

## 4. Platform Adapters

### 4.1 The adapter contract (`services/platforms/base.py`)

Mirrors `services/channels/base.py` in spirit — adapters own transport and translation, never governance. Governance (moderation, egress, provenance) happens in `publisher.py` *before* an adapter sees the payload.

```python
class PlatformAdapter:
    name: str                  # "linkedin"
    label: str                 # "LinkedIn"
    auth_mode: str             # oauth2 | oauth2_pkce | app_password | token | manual

    # ── capability declaration (drives composer + UI, §5) ──────────────────
    def capabilities(self) -> dict:
        """{formats: [...], char_limit: int, title_limit: int|None,
            media: {images: {max, formats, max_bytes, aspect: (lo, hi)},
                    video: {max_s, formats, max_bytes}|None, alt_text: bool},
            thread: bool, native_schedule: bool, native_delete: bool,
            analytics: full|counts|none, hashtags_max: int|None,
            notes: [str]}   # human-readable constraints for the UI"""

    # ── auth lifecycle (tokens ONLY via credential_store, §12.2) ───────────
    def connect_url(self, state: str) -> str | None      # start OAuth (None = tokenless/manual)
    def handle_callback(self, params: dict) -> dict      # code→tokens; encrypt; return status()
    def refresh(self) -> bool                            # refresh if expiring; True = usable
    def revoke(self) -> bool                             # platform-side revoke where supported
    def status(self) -> dict                             # {connected, account, scopes: [...],
                                                         #  expires_at, tier, last_error}

    # ── publish path ────────────────────────────────────────────────────────
    def prepare(self, target, post) -> dict              # validate + upload media →
                                                         # {ok, prepared, warnings: [...]}
    def publish(self, prepared) -> dict                  # {ok, post_url, platform_post_id, raw}
    def delete(self, platform_post_id) -> dict           # takedown; {ok} (best-effort)

    # ── analytics path ──────────────────────────────────────────────────────
    def fetch_metrics(self, platform_post_id) -> dict | None   # → PlatformEngagement.metrics
    def fetch_account_metrics(self) -> dict | None             # follower counts etc.

    # ── local rate budgeting (never trust ourselves to remember) ───────────
    def rate_budget(self) -> dict                        # {window, used, limit, reset_at} —
                                                         # persisted in the DB; the publisher
                                                         # defers targets that would exceed it
```

Registry and lifecycle live in `services/platforms/__init__.py` exactly like `channels/manager.py`: lazy singleton adapters, double-checked locking, config (non-secret) in `~/.friday/platforms.json`, `status()` aggregation for the Accounts tab and `/api/connectors/health` (so the dock's Content icon shows live/error like every other workspace).

**Credential storage convention.** Structured OAuth state (access + refresh + expiry + scopes + account identity) is one encrypted blob per platform at `~/.friday/platforms/<name>.cred` via `credential_store.write_secret()` (the `google_accounts.py` pattern). Single-string secrets (Bluesky app password, Mastodon token) use `credential_store.set_provider_key("platform_<name>", …)` (the channel-token pattern). Every connect/refresh/revoke/publish emits `credential_store.audit_event("platform", …)`.

**OAuth loopback.** Friday's Flask server is the redirect target: `http://localhost:3000/api/content/platforms/<name>/callback`, PKCE where the platform supports it, `state` bound to the session. No external redirect service, ever.

### 4.2 Capability matrix (summary — details per platform below)

| Platform | Auth | Text limit | Images | Video | Threads | Native schedule | Analytics API | Automation tier |
|---|---|---|---|---|---|---|---|---|
| LinkedIn | OAuth2 | 3,000 | ✅ multi | ✅ ≤30 min | — | — | counts (member) / full (org) | API |
| X / Twitter | OAuth2 + PKCE | 280 (25k premium) | ✅ ≤4 | ✅ ≤140 s std | ✅ reply chains | — | full (paid tiers) | API ($) |
| Instagram | OAuth2 (Graph) | 2,200 caption | ✅ JPEG, carousel ≤10 | ✅ reels | — | — | full (insights) | API (constraints) |
| YouTube | OAuth2 (Google) | 5,000 desc | thumbnail | ✅ core | — | ✅ `publishAt` | full (Analytics API) | API (quota) |
| Bluesky | app password / OAuth | 300 | ✅ ≤4, ≤1 MB | ✅ ≤3 min | ✅ reply chains | — | counts | API (open) |
| Mastodon | OAuth2 / token | 500 (instance) | ✅ ≤4 | ✅ | ✅ reply chains | ✅ `scheduled_at` | counts | API (open) |
| Reddit | OAuth2 | title 300 / body 40k | ✅ | ✅ | — | — | counts | API (rules) |
| Substack | none | n/a | ✅ | ✅ | — | ✅ (in-editor) | none | handoff / headless (opt-in) |
| Medium | legacy token only | n/a | ✅ (in content) | embed | — | — | none | legacy API / handoff |
| TikTok | OAuth2 | 2,200 caption | ✅ photo mode | ✅ core | — | — | counts (Display API) | API (audit-gated) |
| Federation | Ed25519 identity *(exists)* | n/a | ✅ | ✅ | — | ✅ (ours) | full (ours) | native |
| Discord / Telegram | bot token *(exists)* | 2,000 / 4,096 | ✅ | ✅ | — | — | none | native bridges |

> **Volatility warning.** Platform pricing, quotas, and endpoints churn constantly (X above all). Every adapter hard-codes nothing it can discover, surfaces its live limits in `capabilities()`/`status()`, and the figures in §4.3–§4.13 are the design-time snapshot (mid-2026) — verify at implementation.

### 4.3 LinkedIn

- **API / auth:** LinkedIn Marketing / Community Management APIs. OAuth 2.0 three-legged; scopes `w_member_social` (post as the member), `openid profile` (identity); organization posting + statistics require Community Management scopes and page-admin rights. Versioned REST: `POST https://api.linkedin.com/rest/posts` with a `LinkedIn-Version: YYYYMM` header (successor to `ugcPosts`). Member tokens live ~60 days; programmatic refresh is partner-gated — the adapter tracks expiry and prompts re-auth through the Accounts tab before it lapses.
- **Formats:** text posts up to **3,000 chars**; multi-image; native video (initialize-upload → chunked PUT → attach URN; ≥3 s, ≤30 min); documents (PDF carousels); polls; link shares with preview. Native *articles* have no public write API → the `article` format for LinkedIn is an assisted handoff (§4.14) or a link-share to a Friday-hosted page.
- **Media flow:** `POST /rest/images?action=initializeUpload` → upload → reference URN in the post; same shape for videos and documents. Alt text supported on images.
- **Rate limits:** undocumented member-level throttles (community-observed ≈150 posts/day, far above sane use); app-level daily quotas per endpoint visible in the developer console. Adapter keeps a conservative local budget (default 25 posts/day) — configurable.
- **Automated vs manual:** posts/images/video/documents automated. Articles, editing a published post's media, and member post *impressions* are manual/unavailable.
- **Analytics:** member posts expose social counts (likes, comments — via `socialActions`/reactions endpoints). Impressions/reach exist **only for organization pages** (`organizationalEntityShareStatistics`, Community Management approval). Adapter reports `analytics: counts` for member accounts, `full` when an org page is connected.

### 4.4 X / Twitter

- **API / auth:** X API v2. OAuth 2.0 Authorization Code + PKCE; scopes `tweet.read tweet.write users.read offline.access` (refresh tokens). `POST /2/tweets`, `DELETE /2/tweets/:id`.
- **Formats:** 280 chars (accounts with premium entitlements can post up to 25k — adapter reads the account's actual limit and exposes it in `capabilities()`); up to 4 images (PNG/JPEG/WebP ≤5 MB), GIF ≤15 MB, video ≤512 MB / ≤140 s standard (longer with premium); polls; quote posts.
- **Threads:** first-class here — the composer's `thread` format maps to sequential `POST /2/tweets` with `reply.in_reply_to_tweet_id` chaining, one segment at a time with jitter, storing every segment's id; a mid-thread failure resumes from the last confirmed segment.
- **Media flow:** v2 media upload (chunked INIT/APPEND/FINALIZE, GA since 2025); `media.media_ids` on the tweet. Alt text supported.
- **Rate limits / cost:** **the paid-tier trap.** Free tier: writes only, on the order of ~500 posts/month app-wide and ~17/day per user. Basic (paid, ~$200/mo as of the 2025 repricing): ~3,000 user posts/month, ~100/day/user. Pro: five figures monthly. Numbers drift — the adapter treats its tier as configuration, tracks a hard local budget, and the Accounts tab shows *posts remaining this month* so the user is never surprised.
- **Automated vs manual:** posting, threads, media, deletes automated. Long-form articles and community posts: manual.
- **Analytics:** `GET /2/tweets?ids=…&tweet.fields=public_metrics` → impressions, likes, retweets, quotes, replies, bookmarks (bookmarks ≈ saves). `non_public_metrics` (URL clicks, profile clicks) require user-context + paid tier. Free tier reads are minimal — the collector degrades to counts-at-poll-budget.

### 4.5 Instagram

- **API / auth:** Instagram Platform (Graph). Two supported paths: **Instagram API with Instagram Login** (business/creator account, `graph.instagram.com`, no Facebook Page required — preferred) or the classic Facebook-Login variant (requires a linked FB Page). App review needed for content-publishing permissions. Professional (business/creator) account is mandatory — the Accounts tab must say so plainly.
- **Formats:** feed images (**JPEG only**, ≤8 MB, aspect 4:5 … 1.91:1 — the composer auto-crops via the asset transform plan and previews the crop); carousels ≤10 children; **Reels** (MP4/MOV, 9:16, cover frame, `share_to_feed`); **Stories** (business accounts). Caption ≤2,200 chars, ≤30 hashtags, ≤20 mentions.
- **Media flow — the sharp constraint:** container-based publishing (`POST /{ig-id}/media` → `POST /{ig-id}/media_publish`) requires media as **publicly reachable URLs** (Reels also accept resumable binary upload). Friday runs on localhost → the adapter needs a staging strategy (§7.4): (a) resumable upload where supported, (b) the user's configured public staging host/tunnel, or (c) hold with a clear "Instagram needs a public asset URL — configure staging or post manually" message. Never a silent failure.
- **Rate limits:** **100 API-published posts per rolling 24 h** (carousel = 1); `content_publishing_limit` endpoint lets the adapter check before it schedules.
- **Automated vs manual:** feed/carousel/reels/stories automated (with the staging caveat). DMs are out of scope (messaging APIs are separately gated). First-comment-with-hashtags automated via the comments edge.
- **Analytics:** rich insights per media (`/{ig-media-id}/insights`: views/reach/likes/comments/saves/shares/total interactions — note Meta's ongoing impressions→views metric migration) and per account (follower deltas, profile views). Adapter reports `analytics: full`.

### 4.6 YouTube

- **API / auth:** YouTube Data API v3 (OAuth2, Google Cloud project; scopes `youtube.upload`, `youtube`, plus `yt-analytics.readonly` for the Analytics API). Reuses Friday's existing Google OAuth plumbing (`services/google_accounts.py`) — same encrypted token store, additional scopes.
- **Formats:** `videos.insert` resumable upload; `snippet.title` ≤100 chars (no angle brackets), `description` ≤5,000 bytes, tags ≤500 chars total, `categoryId`, `madeForKids`, `privacyStatus`. **Shorts** are ordinary uploads (vertical, ≤3 min, `#shorts`) — the `short` format applies the `mp4-vertical-9x16` transform. Custom thumbnails via `thumbnails.set` (phone-verified account, ≤2 MB). Captions API available.
- **Native scheduling:** `status.publishAt` (ISO) with `privacyStatus: private` → YouTube publishes server-side at the instant. The adapter advertises `native_schedule: true`; the publisher uses it when the post is scheduled ≥10 min out (§6.5), so a mid-window laptop shutdown can't miss the slot.
- **Rate limits — the quota trap:** default project quota 10,000 units/day; one upload costs 1,600 → **6 uploads/day** out of the box (quota raise via audit form). The adapter budgets locally and surfaces remaining uploads.
- **The audit caveat:** unverified/unaudited API projects may have uploads locked private. Accounts tab must display the project's audit state and the consequence.
- **Analytics:** best in class. Data API `statistics` (views/likes/comments snapshot) + YouTube Analytics API (watch time, average view duration, subscribers gained, per-day series). Adapter reports `analytics: full`.

### 4.7 Bluesky

- **API / auth:** AT Protocol XRPC against the user's PDS (default `bsky.social`). Simplest path: **app password** → `com.atproto.server.createSession` (access/refresh JWTs); OAuth is available and preferred as it matures. No app review, no cost.
- **Formats:** posts ≤**300 graphemes** (grapheme-aware counting, not bytes — the composer's limit meter must match); up to 4 images (blob upload `com.atproto.repo.uploadBlob`, ≤~1 MB each — adapter recompresses larger), alt text + aspect-ratio hints; external-link card embeds; **video is supported now** (up to ~3 min via the video service, daily upload caps) — the design brief's "no video yet" is stale; the adapter feature-detects.
- **Richtext facets:** links, mentions, and hashtags are **byte-range facets** the client must compute — the adapter owns UTF-8 byte-offset facet generation (classic source of off-by-one bugs; unit-test with emoji-adjacent links).
- **Threads:** reply chains with root/parent refs — same segmented publish pattern as X.
- **Rate limits:** generous account-level budget (≈5,000 action points/hour, `CREATE`=3 → ~1,600 posts/hour; ~35,000/day). Effectively unconstrained for a human account.
- **Analytics:** no aggregate API; per-post like/repost/reply/quote counts via `app.bsky.feed.getPosts`. No impressions. `analytics: counts`.

### 4.8 Mastodon

- **API / auth:** plain REST per instance. Register the app (`POST /api/v1/apps`) then OAuth2 authorization-code, or paste a personal access token from instance preferences (scopes `write:statuses write:media read:statuses`). Adapter is **instance-aware**: base URL is configuration; limits are discovered from `GET /api/v2/instance` (`configuration.statuses.max_characters` etc.) instead of hard-coding 500.
- **Formats:** status ≤500 chars default (instance-configurable); ≤4 media (images ≤~16 MB, av ≤~99 MB, instance-dependent), alt text + focal point; content warnings (`spoiler_text`), `sensitive` flag (**this is where the `nsfw` tag maps**), visibility (`public | unlisted | private | direct`), `language`.
- **Native scheduling:** `scheduled_at` ≥5 min ahead → server-side scheduled status, listable/cancelable via `/api/v1/scheduled_statuses`. Adapter advertises `native_schedule: true`.
- **Media flow:** `POST /api/v2/media` is async — poll the media id until processed, then attach. Idempotency-Key header on status creation (the adapter sends one derived from the target id — free double-post insurance).
- **Rate limits:** default 300 requests/5 min per token; media-upload sub-limits vary by instance. Non-issue at human volumes.
- **Analytics:** `favourites_count`, `reblogs_count`, `replies_count` on the status entity. No impressions. `analytics: counts`.

### 4.9 Reddit

- **API / auth:** Reddit Data API, OAuth2. For a personal sovereign tool the **script-app** credential pair (code flow with refresh token also fine); mandatory descriptive `User-Agent`. Free for personal-scale use under the 2023 commercial terms.
- **Formats:** `POST /api/submit` — `kind=self` (markdown body ≤40k), `kind=link`, image/gallery/video via the media-asset lease flow (S3 upload → submit). Title ≤300 chars. Flair (`flair_id` via `/api/selectflair`), `nsfw`, `spoiler`, `sendreplies` all supported → mapped from target `options` and tags.
- **Subreddit targeting — the real complexity:** every subreddit is its own jurisdiction. The adapter fetches `GET /r/{sr}/about` + `/about/rules` at compose time and surfaces requirements (flair mandatory? self-promo ratio? min karma?) as composer warnings *before* scheduling, not as publish-time failures. One `PlatformTarget` per subreddit (cross-posting to N subs = N targets, staggered by default to respect self-promotion norms).
- **Rate limits:** ~100 queries/min per OAuth client, plus invisible per-account/subreddit posting velocity heuristics (spam filter). Local budget defaults conservative (≤10 posts/day).
- **Analytics:** `GET /api/info?id=t3_…` → `score`, `upvote_ratio`, `num_comments`. No impressions. `analytics: counts`.

### 4.10 Substack

- **API / auth:** **no official API.** Unofficial JSON endpoints exist but are undocumented, cookie-authenticated, and ToS-gray — not a foundation.
- **Adapter modes:**
  1. **Assisted handoff (default):** the composer produces a Substack-ready package — title, subtitle, full HTML/markdown body with inline images exported to a local folder — one click copies the body and opens the editor URL; the user pastes and hits publish (or uses Substack's own in-editor scheduling). Friday records the resulting URL when the user pastes it back (or finds it via the publication's RSS feed within the poll window — automatic URL confirmation).
  2. **Headless browser (strictly opt-in, Phase 6+):** Playwright (already a dev dependency in this repo) drives the editor with a locally stored session. Marked experimental; breaks when Substack ships a redesign; disabled by default; every run is visible (headed mode option) and logged.
- **Formats:** long-form posts/newsletters; images fine; the ~100 KB email-clip threshold is a composer warning for very long pieces.
- **Analytics:** none via API. Options: manual entry in the Analytics tab, or opt-in headless stats scrape. The unified dashboard renders Substack as "manual/partial" honestly rather than pretending.
- Target status for handoff mode: `SENT` (package ready + notification) → `CONFIRMED` when a URL is attached. This keeps the queue truthful.

### 4.11 Medium

- **API / auth:** the official REST API v1 (`api.medium.com`) is **deprecated — new integration tokens are no longer issued**. Users holding a legacy integration token can still publish (`POST /v1/users/{authorId}/posts`: title, `contentFormat` html|markdown, content, ≤5 tags, `canonicalUrl`, `publishStatus` draft|public|unlisted, license, notify flag).
- **Adapter modes:** feature-detect a stored legacy token → full automation; otherwise the same assisted-handoff package as Substack (Medium's editor accepts pasted markdown/HTML well), with opt-in headless as a last resort.
- **The `canonicalUrl` field matters:** when a piece was first published elsewhere (blog, Substack), the composer sets the canonical link automatically — SEO hygiene for repurposed content.
- **Analytics:** none via API (partner dashboard only) → manual/none, rendered honestly.

### 4.12 TikTok

- **API / auth:** TikTok Content Posting API (`open.tiktokapis.com`), OAuth2; scopes `video.publish`, `video.upload`, `user.info.basic`, `video.list` (analytics). Developer app registration required, and — the sharp constraint — **unaudited apps can only create SELF_ONLY (private/draft) posts**. Until the app passes TikTok's audit, the adapter is honest: posts land as drafts in the user's inbox for one-tap manual publishing in the app. Post-audit: true direct publish.
- **Formats:** video (MP4/WebM/MOV, H.264, 9:16 preferred — the `tiktok-vertical` export profile) via chunked `FILE_UPLOAD` (init → 5–64 MB chunks → status polling) or `PULL_FROM_URL` (requires domain verification — same staging story as Instagram); **photo-mode posts** (up to 35 images); caption ≤2,200 chars incl. hashtags; privacy level, duet/stitch/comment toggles, commercial-content flags all in `options`.
- **Rate limits:** small per-user pending-share caps (single digits per 24 h) plus app-level QPS. Local budget defaults to ≤5 posts/day.
- **Analytics:** Display API `video.list`/`video.query` → views, likes, comments, shares per video. `analytics: counts`.

### 4.13 Friday Federation (+ Discord/Telegram announce)

- **Federation** (`services/platforms/federation_pub.py`): no third party — this is Friday's own rails, all *(exists)* infrastructure:
  1. Ensure the asset is registered (`ownership.register()` — auto-builds the signed manifest if missing).
  2. `marketplace.create_listing(asset_id, price_mpsi=target.options.price_mpsi or 0, license_offered=post.license.terms, title, description=adapted_body, visibility)` — free-commons (CC0/CC-BY/CC-BY-SA) or priced in Positrons.
  3. Announce: encrypted `CONTENT_OFFER` via `federation_transport` to trusted peers (trust-graph filtered), and to any subscribed discovery directories.
  4. `post_url` = the local listing URL; `platform_post_id` = listing id.
  - **Engagement** = listing views (local counter), peer fetches, purchases (`complete_purchase` receipts), and ψ tips — flowing into the same unified metrics as external platforms (§8.2). Purchases already move real ψ through `economy.transfer()`.
  - Moderation note: federation publishing runs the same H1–H4 scan; policy-pack tags travel with the listing so peer nodes' filters work (`content_policies` packs).
- **Discord / Telegram announce:** the existing channel bridges (`services/channels/`) already speak outbound `send()` with egress-gated text. A thin `announce` format posts the adapted body + link to a configured channel/chat id — zero new transport, config in `~/.friday/platforms.json` pointing at the bridge. Useful for "new post is live" community pings.

### 4.14 The degradation ladder

Every adapter declares where it sits and degrades one rung at a time, loudly:

```
API (full auto) → API-constrained (private drafts / quota-deferred)
→ assisted handoff (package + open editor + confirm URL)
→ clipboard-only (copy adapted text; user does the rest)
```

A target that cannot publish at its declared rung **never** silently falls to a lower one — it surfaces the choice ("TikTok app not audited: save as private draft, or hold?") through a notification + Queue action. Honesty about automation limits is a feature; fake automation is how tools lose trust.

---

## 5. Content Composition Engine (`services/content_composer.py`)

One input, N platform-native outputs — the intelligence between "I made a thing" and "it ships correctly everywhere."

### 5.1 Public API

```python
def adapt(post: dict, platforms: list[str] | None = None, *,
          formats: dict | None = None, regenerate: bool = False) -> dict:
    """Produce/refresh PlatformTargets for a ContentPost.
    Returns {ok, targets: [...], warnings: [...]}. Never raises.
    Skips CONFIRMED targets; regenerate=True rebuilds PENDING/HELD ones."""

def preview(body: str, assets: list, platform: str, format: str) -> dict:
    """Stateless single-platform adaptation for the Compose tab's live preview.
    {ok, adapted_body, segments, hashtags, char_used, char_limit,
     asset_plan: [...], warnings: [...]}"""

def suggest_hashtags(body: str, platform: str, limit: int = 8) -> list[str]
def convert_format(post: dict, conversion: str) -> dict     # §5.4 / §9
```

### 5.2 Adaptation pipeline (per target)

```
canonical body (markdown)
  1 resolve capabilities   ← adapter.capabilities() (limits, media, thread?)
  2 voice + rewrite        ← _generate_text via model router (gated), §5.6 prompt
  3 structural transform   ← thread split / caption + link / title extraction
  4 hashtag pass           ← §5.3 (platform norms, not spray)
  5 asset transform plan   ← map assets → export profiles / crops / recompress
  6 hard validation        ← grapheme counts, aspect ratios, missing alt text
  7 target write           ← PlatformTarget payload + preview snapshot
```

Rules that make adaptation *native* rather than truncation:

- **LinkedIn** gets the hook-first professional register (the proven `DRAFT_MODE_PROMPTS['linkedin_post']` voice: 1–3 paragraphs, strong opening, 2–3 hashtags max, "conversational authority, not corporate fluff").
- **X** gets compression to one sharp idea; overflow becomes a numbered thread with a hook tweet, one idea per segment, and a closing link/CTA segment. Grapheme-aware counting; URLs count as 23.
- **Instagram** gets a caption that survives the above-the-fold cut (~125 chars), line-broken for air, hashtags in a trailing block (≤30, default 8–12).
- **Bluesky/Mastodon** get the X-style compression *without* hashtag spray (norms differ: 0–3 tags), Mastodon body may carry a CW when tags suggest it.
- **YouTube** gets a keyword-front-loaded title ≤100 chars and a structured description (summary → chapters if timeline markers exist → links).
- **Reddit** gets an honest, community-register title (no clickbait), body in markdown, and *zero* hashtags; subreddit rules from §4.9 appear as warnings.
- **TikTok** gets a caption ≤2,200 with 3–5 tags and a hook line, plus the vertical transform plan.
- **Federation** gets full fidelity — title, complete body, license, price — it is the only target that never loses information.

Model calls are engine-direct (`_generate_text`, temperature ~0.4, `orb_label="✍️ Composing — <platform>"`, `workspace="content"`) — never the autonomous agent loop (D5). Every prompt leaves through `egress_gate.gate_text(prompt, provider, "compose.prompt")` exactly as `creative_engine` gates image prompts. Cost lands in `cost_meter` under the content workspace.

### 5.3 Hashtag generation

Per platform, not global: candidate extraction from the body (entities, topics) merged with the user's pinned tag sets (per-platform, editable in Compose), ranked by platform norm fit, capped by `capabilities().hashtags_max` and the per-platform default counts above. Never invents trending tags it can't verify; never exceeds platform norms even when the cap allows. The user's brand tags (e.g. a product name) are sticky-first.

### 5.4 Format conversion matrix

`convert_format(post, conversion)` powers both Compose ("make this a thread") and the repurposing engine (§9):

| Conversion | Mechanics |
|---|---|
| blog/article → tweet thread | outline extraction → hook + 1-idea-per-segment + CTA close |
| article → LinkedIn post | thesis + 2 supporting points + question close |
| video → reel/short/tiktok | `timeline_engine.compose()` with `instagram-reel` / `mp4-vertical-9x16` / `tiktok-vertical` profiles; optional caption burn-in via drawtext |
| long video → clip | timeline slice (start/end) → vertical profile |
| image set → carousel | order + per-slide alt text + cover selection |
| post → newsletter section | expand register, add context links |
| anything → YouTube description | summary + chapters + links block |
| audio → audiogram clip | `timeline_engine` still+waveform composition (Phase 5 stretch) |

### 5.5 Preview rendering

The Compose tab renders each target as a **platform-styled mockup** (avatar, display name, the exact adapted text, media crop, thread cards for segments) driven entirely by the `PlatformTarget` payload — the preview *is* the payload (D6). Each pane shows a char meter (`char_used / char_limit`, grapheme-correct), the asset transform result (post-crop thumbnail), and inline warnings (missing alt text, subreddit flair required, staging needed). Nothing renders from a second code path, so preview drift is structurally impossible.

### 5.6 SOUL.md personality injection

Composition system prompt = `_get_friday_system_prompt(workspace="content")` — which already folds in `soul.render_personality()` (SOUL.md), the learned user model (`user_model.render_user_model_prompt()`), and promoted learning-loop heuristics — plus a **platform voice card**:

```
~/.friday/content/voice_cards/<platform>.md      (user-editable, seeded defaults)
```

Seeds derive from the shipped `DRAFT_MODE_PROMPTS` registers (LinkedIn ghostwriter, sub-280 tweet voice) and SOUL.md's own tone rules. The card is small (≤2 KB), versioned like SOUL.md history, and the Compose tab links straight to editing it. Result: posts sound like *the user on that platform* — the LinkedIn voice and the Bluesky voice are recognizably the same human at different registers, and never generic AI paste.

---

## 6. Scheduling System

### 6.1 What the scheduler already gives us *(exists)*

`services/scheduler.py`: a 60-second tick loop; `interval`/`daily`/`weekly` triggers; `schedules.json` registry; retries `{max, backoff_seconds}` with `not_before`; mark-before-run double-fire protection; run history JSONL; orbs + notifications; builtin-task registration.

### 6.2 The publisher tick (D2) — why not one schedule per post

Posts are **data**, not schedule records. Registering an ad-hoc schedule per post would bloat `schedules.json`, leak internals into the Settings UI, and fight the seed/reconcile logic. Instead, `publisher.start()` registers exactly **one** builtin:

```python
register_builtin_task("content_publisher", publisher.tick,
                      label="Content publisher", default_trigger="interval",
                      default_spec={"every_minutes": 1}, notify="silent")
```

`publisher.tick()` scans `targets WHERE status='PENDING' AND publish_at <= now AND not_before <= now` and dispatches (§7.1). One-minute granularity matches the platform reality (nobody needs second-precision posting), inherits the scheduler's crash-safety, and keeps the content DB the single source of truth. Two more builtins ride the same rail: `content_analytics` (interval, 30 min, `notify="silent"` — §8.1) and `content_insights` (weekly — §8.4).

**Scheduler extension (small, general):** add a `once` trigger (`spec: {at: <epoch>}`, auto-disables after firing) to `scheduler.py` for arbitrary one-shot user schedules. The publisher does not need it, but the Calendar UI's "remind me to go live" and future one-shot jobs do — it rounds out the trigger set with ~20 lines inside the existing `_is_due`/`_next_run_ts` switch.

### 6.3 Timezones

`publish_at` is stored UTC; `ScheduleConfig.timezone` (IANA) is authoritative for display, recurrence math, and optimal-time resolution ("9 a.m. Tuesday" means the *user's* Tuesday — the scheduler's own Central-time tick is irrelevant because the publisher compares UTC instants). Recurrence expansion happens in the post's timezone, then converts — daylight-saving correct by construction.

### 6.4 Optimal time suggestions

Two-layer resolver behind `optimal_time: true` and the Calendar's highlighted slots:

1. **Seed layer** (ships day one) — static best-practice table per platform, weekday/hour ranges (e.g. LinkedIn Tue–Thu 08–11 local; X weekdays 09–12; Instagram 11–13 & 19–21; YouTube Thu–Fri 14–16 + weekend mornings; TikTok 18–22; Reddit 07–10 US-morning of the target sub; Mastodon/Bluesky 18–22; newsletters Tue–Thu 09–10). Marked in the UI as *general guidance*.
2. **Learned layer** — the `best_times` histogram (platform × weekday × hour, engagement-rate score with sample counts, §8.4). Once a cell has ≥5 samples it outranks the seed. The resolver picks the highest-scoring free slot ≥15 min ahead that clears conflict detection, and the UI labels it "learned from your audience" vs "general best practice" — the difference is trust.

### 6.5 Native-schedule delegation

When an adapter advertises `native_schedule: true` (YouTube `publishAt`, Mastodon `scheduled_at`) and the post is ≥10 min out, the publisher pushes at *arm time* and lets the platform hold it — the machine can be asleep at the instant and the post still lands. The target carries `options.native_scheduled: true`; cancel/reschedule calls the platform's cancel API. All other platforms are published by Friday at the instant (the desktop must be running — a Queue banner says so for any slot >24 h out, suggesting native-capable platforms or the federation for guaranteed delivery).

### 6.6 Queue management

Backed by `GET /api/content/queue` (targets joined to posts, ordered by `publish_at`): reorder (drag = swap `publish_at`s or shift-all-after), cancel (post or single target), reschedule (picker + optimal suggestion), pause-all (global kill switch in Accounts → also honored by the tick), and per-target attempt/error history from `publish_log.jsonl`.

### 6.7 Recurrence

`recurrence: daily | weekly | custom_cron` (+ `recurrence_spec`) turns a post into a **template**: at publish time the pipeline clones the post (fresh ids, `source: {kind:"recurrence", ref: parent_id}`), optionally re-runs the composer with a freshness hint ("vary the phrasing; don't repeat yesterday's wording" — variation guard against platform duplicate-content filters), publishes the clone, and computes the parent's next instant. Cron is 5-field with minute-floor resolution, evaluated in the post's timezone. `expires_at` retires the template. Typical uses: daily creation showcase, weekly digest, monthly newsletter nudge.

### 6.8 Conflict detection

At schedule time (and re-checked at tick): warn if another target on the **same platform** lands within `content.conflict_window_hours` (default 2; per-platform override; Reddit defaults wider at 24 h per-subreddit). Warnings, not hard blocks — the user can always override; the resolver never *suggests* a conflicting slot. Cross-platform simultaneity is explicitly fine (that's the point of the tool).

---

## 7. Publication Engine (`services/publisher.py`)

### 7.1 Dispatch flow

`tick()` (from §6.2) claims due targets — `status='PENDING' → 'PREPARING'` write-wins under the store lock, mark-before-run — groups by post, and runs each target on a daemon thread (scheduler's concurrency pattern; per-platform serialization so one platform's slowness never blocks another):

```
per target:
 1 GATE — moderation.scan(adapted_body + asset paths)      # H1–H4; blocked → FAILED(harm), no retry
 2 GATE — sensitivity classify final body + title           # non-PUBLIC → HELD (§7.3), notify, stop
 3 STALENESS — post edited after compose? → recompose target (composer §5.1)
 4 PREPARE — adapter.prepare(target, post)
      · asset transforms (crop/transcode via PIL / timeline_engine profiles)
      · content-credential embed into the OUTBOUND COPY (§7.7)
      · media upload (chunked/resumable per platform), alt text attached
      · final hard validation against capabilities()
 5 BUDGET — adapter.rate_budget() would exceed? → defer (not_before = reset_at), no attempt burned
 6 PUBLISH — adapter.publish(prepared) → {post_url, platform_post_id}
 7 RECORD — target SENT→CONFIRMED; publish_log.jsonl line; provenance publication
      entry (§7.7); ownership distribution event; ψ earn (once per post, §8.7)
 8 SURFACE — orb complete; notification (§7.6); analytics polls scheduled (§8.1)
```

Post-level rollup after all targets settle: `PUBLISHED` (all confirmed) / `PARTIAL` / `FAILED` / `HELD` (any held). `published_at` = first confirmation.

### 7.2 Idempotency — the double-post guarantee

The cardinal sin of publishing tools is posting twice. Defenses, in order: mark-before-run claims; one publish attempt in flight per target (running-set, scheduler pattern); platform idempotency keys where supported (Mastodon `Idempotency-Key`, TikTok init transaction, resumable upload session ids); and after an *ambiguous* failure (timeout after the POST left), a **verify-before-retry** probe — the adapter searches its recent posts for the payload fingerprint before any retry is allowed. A confirmed target is immutable — re-arming a `PARTIAL` post can never touch it.

### 7.3 Egress gate semantics — hold, don't mangle (D3)

A social post is *intentionally public*, so the gate's job here is not tier-redaction (a post with `[VAULT-PROTECTED]` placeholders must never ship) — it is a **PII/vault-leak backstop**:

```python
gated = egress_gate.gate_text(body, provider=f"platform_{name}", field="post.body")
if gated != body:            # classifier saw TIER_2/3 → something private is in there
    target.status = HELD     # fail-closed: nothing ships
    notify(priority="high", "Post held — it looks like it contains private data",
           actions=[Review in Content → Queue])
```

The Held view shows the flagged spans (classifier evidence) so the user edits or explicitly releases ("publish anyway — this is intentional"). A gate *error* (not verdict) also holds — same fail-closed backstop the channels funnel uses, but with a human release instead of a withheld reply, because unlike a chat reply a scheduled post has no waiting counterparty. Every gate decision lands in the standard egress log (`~/.friday/vault/egress-log.jsonl`).

### 7.4 Asset staging for URL-pull platforms

Instagram containers and TikTok `PULL_FROM_URL` want public URLs. Resolution order: (1) binary/resumable upload wherever the platform allows it (Reels resumable, TikTok `FILE_UPLOAD` — preferred, no staging at all); (2) the user's configured staging host (`settings.content.staging_base_url` — e.g. their own site or tunnel) to which the publisher copies the transformed asset under an unguessable path and deletes after confirmation; (3) **hold** with a clear explanation. Friday never stands up an ad-hoc public tunnel on her own — exposure of a local port is a user decision, made once, in Settings.

### 7.5 Failure handling

Per-target retry envelope (scheduler's shape): `retry: {max: 3, backoff_seconds: 300}` with exponential multiplier ×4 (5 min → 20 min → 80 min), honoring platform `Retry-After` headers when present (429s). Error classes: *permanent* (400 validation, 403 policy, moderation block) → `FAILED` immediately, no retry; *auth* (401) → single `refresh()` attempt, then `FAILED` with a re-auth deep link into Accounts; *transient* (429/5xx/network) → backoff ladder. Exhausted retries → `FAILED` + high-priority notification with one-tap **Reschedule** (next optimal slot) and **Retry now** actions. A recurring template that fails still computes its next occurrence — one bad morning never kills the series.

### 7.6 Notifications & orbs

Via `voice_engine._notif_engine.push()` with `source="content"`, `kind="content_publish"`, dedupe keys per target+day: success (low priority, "✓ Published to LinkedIn — view post", actions: open URL / open Analytics), partial/failure (high priority, error summary + Reschedule/Retry), held (high priority, §7.3). Every long operation (compose batch, publish, video upload) surfaces a process orb (`core.process_register` → progress → complete → fade) exactly like generation does today.

### 7.7 Provenance through publication

The asset's ContentCredential *(exists)* was signed at generation. Publication extends the chain rather than re-signing the artifact:

1. **Embed in the outbound copy:** `content_credentials.embed_credential()` on the *transformed copy* the platform receives (PNG text chunk / ID3 / front-matter — already built; JPEG/MP4 best-effort). Platforms that strip metadata strip it — LinkedIn and TikTok currently display C2PA credentials, others don't; embedding is best-effort by design.
2. **Sign the publication event:** a `publication` entry appended to the asset's manifest history via a new `provenance.add_publication(content_hash, {platform, post_url, platform_post_id, target_id, published_at})` — built and signed with the same `IntegrityEngine` payload signing, stored in the sidecar + hash-chained ledger. **The local ledger, not the platform, is the source of truth** — even if every platform strips everything, the user can prove "I published this, there, then" cryptographically.
3. **Ownership distribution event:** the asset's `ownership.db` record gains a distribution row (platform, URL, date) so `verify()` and the provenance chain UI show where copies legitimately live.
4. **Optional verify link:** a per-post toggle appends a short provenance line ("Signed original: <link>") for audiences that care; off by default to keep posts clean.
5. **License guard:** publishing an asset the user does not own outright (purchased, CC-derived) runs `ownership.check_license_compat()` first — CC-BY attribution requirements inject an attribution line automatically; incompatible licenses block with an explanation.

---

## 8. Analytics Collector (`services/analytics_collector.py`)

### 8.1 Poll scheduling

The `content_analytics` builtin (interval, 30 min, silent) walks a per-target poll plan created at confirmation: **+1 h, +6 h, +24 h, +3 d, +7 d, +30 d** after publish (then stop; a manual "refresh" button exists per post). Decaying cadence matches engagement physics (most signal in the first day) and keeps API usage negligible against every platform's read quotas — the collector also respects each adapter's `rate_budget()` and simply slides a poll to the next tick when budget is tight. X on the free tier (reads nearly nil) degrades to manual refresh with an honest badge.

### 8.2 Unified metric normalization

One shape, per-platform mapping, missing ≠ zero (unreportable metrics are absent, and the UI renders "—" not "0"):

| Unified | LinkedIn (member) | X | Instagram | YouTube | Bluesky | Mastodon | Reddit | TikTok | Federation |
|---|---|---|---|---|---|---|---|---|---|
| impressions | — (org only) | impression_count | views | views† | — | — | — | view_count | listing_views |
| likes | reactions | like_count | likes | likes | like_count | favourites | score‡ | like_count | tips_count |
| comments | comments | reply_count | comments | comments | reply_count | replies | num_comments | comment_count | peer_messages |
| shares | reposts | retweet+quote | shares | shares | repost+quote | reblogs | crossposts | share_count | peer_relays |
| saves | — | bookmark_count | saved | — | — | bookmarks* | saves* | — | — |
| clicks | — | url clicks (paid) | — | — | — | — | — | — | fetches |
| video_views | video views | video views | plays | views | — | — | — | view_count | — |
| watch_time_s | — | — | — | estMinutesWatched×60 | — | — | — | — | — |
| follows_gained | — | — | follows | subscribersGained | — | — | — | — | new_peers |

† YouTube "impressions" exist in Analytics API as thumbnail impressions; `views` is the headline number. ‡ Reddit score = upvotes−downvotes (plus `upvote_ratio` kept in raw). * Where instance/API exposes it.

`engagement_rate = (likes+comments+shares+saves+clicks) / max(impressions or video_views or 1)` — computed uniformly so cross-platform comparison is at least honest about its denominator (the UI footnotes which denominator each platform used).

### 8.3 Snapshot storage & rollups

Every poll writes an `engagement_snapshots` row (normalized + sanitized raw) and refreshes the denormalized `posts.analytics_json` rollup. Time-series per post = the snapshot rows; nothing is overwritten, so the Analytics tab can draw growth curves without platform re-fetches.

### 8.4 Trend detection & best-time analysis

The weekly `content_insights` builtin computes, locally, over ≥14 days of snapshots:

- **Attribute lift:** engagement rate grouped by content attributes (kind: text/image/video/thread; body-length bucket; hashtag count; has-question-hook; weekday; hour). Reported with sample sizes and a Wilson-style lower bound (the `learning_loop` scoring approach) so "video posts get 4.1× more engagement than text (n=23)" is statistically honest, and small-n flukes stay quiet.
- **Best-time histograms:** engagement rate per (platform, weekday, hour) → `best_times` rows. This is the learned layer of §6.4: "Your LinkedIn audience is most active Tuesday 9–11 a.m." appears only when cells have ≥5 samples.
- **Variant verdicts (Phase 7):** paired A/B targets compared on engagement rate; a variant that wins ≥3 pairings gets a "winner" insight and its style notes feed the voice card suggestions.

Insights surface as Analytics-tab cards + a weekly `on_change` notification, and every published post also records a `learning_loop.observe(task_type="content_publish", approach=f"{platform}:{kind}:{hour_bucket}", success=rate>account_baseline)` so promoted heuristics ("threads out-perform single posts on X for you") flow into future composer prompts through the existing prompt-injection rail.

### 8.5 Feedback into composition & scheduling

Consumers of the learned data: the optimal-time resolver (§6.4), composer heuristics (via `learning_loop.render_heuristics_prompt`), hashtag ranking (tags that appear in high-lift posts rank up), and repurposing priorities (§9 orders conversions by learned per-platform lift for this account).

### 8.6 Untrusted-input discipline

Everything a platform returns — metric payloads, error bodies, comment text if ever fetched — is **data, never instructions**. Concretely: schema-validate and type-coerce numbers; strip/escape all strings before storage; `raw_json` is capped (8 KB) and never rendered as HTML; **no platform-returned text is ever concatenated into an LLM prompt** (insights are computed numerically; the composer sees aggregate numbers and locally-derived phrases only). This closes the prompt-injection reverse channel flagged in §1.4.

### 8.7 Engagement → Positrons

Idempotent, bounded, local: on crossing metric thresholds (first 10 likes, each subsequent 10, shares in 5s — thresholds in settings), the collector writes a `psi_awards` row keyed `{target_id}:{metric}:{threshold}` and calls `economy.earn(agent_id, PSI_LIKE, reason="content:engagement:<platform>")` *(constants exist: 10ψ per creation-publish, 1ψ per like/share)*. The award table guarantees a re-poll never double-mints; a daily cap (`content.psi_daily_cap`, default 200ψ) keeps a viral day from distorting the wallet; federation purchases keep moving real ψ through the marketplace rails untouched.

---

## 9. Content Repurposing Engine

One input → N platform-native outputs, built as a thin orchestration over the composer (§5.4 conversions) + timeline engine profiles — no new media code.

### 9.1 Entry points

- **Repurpose button** on any creation (Studio detail), news editorial, wiki draft, or published post ("this did well — spin it out").
- `POST /api/content/repurpose {source: {kind, ref}, spread: "default"|custom[]}`.
- Recurring: "every weekly editorial → the full spread" as a recurrence template.

### 9.2 The spread matrix

| Source | Default spread (each = one `PlatformTarget`, individually editable before scheduling) |
|---|---|
| Blog post / editorial *(md)* | LinkedIn post + X thread + Bluesky thread + Mastodon post + Instagram caption card (§9.3) + YouTube description (if companion video) + Substack section + Medium (canonical-linked) + federation listing |
| Video *(mp4)* | YouTube upload (16:9) + Short (9:16 slice) + Instagram reel + TikTok + X native video (≤140 s slice) + Bluesky video + Mastodon + federation |
| Image / image set | Instagram post/carousel + X media post + LinkedIn visual + Bluesky (≤4, recompressed ≤1 MB) + Mastodon + federation gallery listing |
| Music track *(mp3)* | Federation listing (priced/CC) + audiogram clip (Phase 5) → reel/TikTok/Short + announce posts with player link |
| Daily creation | Showcase post per platform + federation listing — schedulable as the standing "Friday made a thing today" slot |

Each adaptation is a real composition pass (voice, register, hashtags, format) — **never** the same caption pasted N times; §5.2's per-platform rules apply in full. The spread generator orders and pre-selects targets by learned lift (§8.5) — platforms where this content kind historically dies are pre-deselected with the reason shown.

### 9.3 Asset fan-out

Media transforms resolve through `timeline_engine.EXPORT_PROFILES` *(exists)* (`youtube-16x9`, `instagram-reel`, `tiktok-vertical`, `gif-preview`, `audio-mp3`) plus PIL crop/recompress for stills (4:5 Instagram crop, ≤1 MB Bluesky recompress, quote-card render of a text pull for Instagram from md sources). Every derived file gets its own metadata sidecar + ContentCredential with a `source_edge` back to the original — derivations stay inside the provenance DAG *(mechanism exists: `provenance.source_edge`)*.

### 9.4 Cost + safety notes

Repurposing runs ≤N composer calls (one per target) — orb-tracked, cost-metered, and every prompt egress-gated like any other composition. A full 9-target spread from a blog post is roughly ten short LLM calls; the Compose tab shows the estimated cost before the run (via the pricing/cost-meter data) so "repurpose everything" is an informed click.

---

## 10. UI Specification

Design language: existing components and classes only — `card`/`card-title`/`btn`/`btn-magenta`/`input`/`badge-cyan`, `FridaySays` context line, `ProvenanceBar`, process orbs, `useNavTarget` deep links, the `SendTo` menu, and the Studio prompt-bar pattern. No new visual system.

### 10.1 Content Workspace (`ContentWS` v2 — replaces the stub, keeps its soul)

Tab rail (Studio-style buttons): **✍️ Compose · 📅 Calendar · 📋 Queue · 📈 Analytics · 🔗 Accounts · 💡 Ideas**

`FridaySays`: "3 posts scheduled this week · last post: 2.4k impressions · LinkedIn token expires in 6 days."

**✍️ Compose** — the `StudioPromptBar` pattern aimed at posts:
- Prompt bar: describe the post or pick a source (asset picker over the creations gallery, news editorials, wiki drafts, Ideas items). "✨ Draft" → composer.
- **Platform chips** (multi-select, connection-aware — disconnected platforms render dimmed with "Connect" linking to Accounts; the format select per chip: post/thread/reel/…).
- **Preview rail**: one platform-styled mockup per selected chip (§5.5) with char meters, crop previews, warnings; editing the canonical body live-refreshes via `POST /api/content/preview`; per-target manual override unlocks direct editing of that platform's text ("detach from canonical").
- **Hashtag row** per platform: suggested chips (tap to toggle) + pinned brand tags.
- **Variant creator**: "＋ Variant" duplicates the body into labeled alternates (Hook A/B); a per-platform variant selector assigns them; Phase 7 adds auto-split scheduling.
- Alt-text fields per image with a nag badge until filled.
- Footer: license/provenance line (`ProvenanceBar` for the primary asset), moderation pre-check chip (green/tagged/blocked), **Schedule** (opens slot picker with optimal suggestions) / **Post now** / **Save draft**.

**📅 Calendar** — month + week views:
- Posts as pills, color-coded per platform (multi-platform posts show stacked dots); drag-to-reschedule (week view = 30-min rows; month = day moves preserving time); click = detail popover (preview, edit, cancel).
- **Suggested optimal slots** render as soft-glow empty pills ("LinkedIn · learned" / "· general") — clicking one starts a compose pre-filled with that slot (§6.4 labels).
- Conflict warnings inline (amber pill edge + tooltip). Recurring templates show as ghost pills with a ↻ badge.

**📋 Queue** — truth about what will and did happen:
- Upcoming list (target-level rows: platform icon, title, time-in-tz, status chip PENDING/PREPARING/HELD, variant tag), drag to reorder, row actions edit/reschedule/cancel/run-now.
- **Held** section pinned on top (amber): flagged spans shown, Release / Edit actions (§7.3).
- History (from `publish_log.jsonl`): sent/confirmed/failed with error detail, retry buttons, post URL links. Global **pause-all** toggle mirrored from Accounts.

**📈 Analytics** — local dashboard, no external service:
- Header stat cards: 7/30-day impressions, engagement rate, best post, ψ earned from content.
- Cross-platform time-series (per-platform lines, unified metrics, "—" honesty for platforms that can't report a metric §8.2).
- **Insight cards** from `content_insights` (§8.4): attribute lift, best times ("learned", with n=), variant verdicts. Each card has "act on this" where applicable (e.g. "schedule next video at Tue 9 a.m.").
- Best-performing content gallery (top posts by rate, click-through to post + platform URL).
- Per-post drilldown: snapshot curve, per-platform table, refresh button (budget-aware).

**🔗 Accounts** — the trust surface:
- One card per platform: connection state (the `status()` envelope — account name/handle, tier, expiry countdown), **scopes in plain language** ("Can: publish posts, read your post statistics. Cannot: read DMs, see your feed, act as you elsewhere."), live rate budget ("18/25 posts left today"), last error.
- Actions: Connect (OAuth loopback flow §4.1 / token paste for Bluesky-Mastodon / "manual mode" for Substack-Medium), Re-authenticate, **Disconnect** (platform revoke where supported + credential purge + optional local-analytics purge — §12.6), Test post (to self/private where the platform allows).
- Global controls: pause-all publishing, conflict window, ψ thresholds, staging host config (with a plain-English explanation of what it exposes).

**💡 Ideas** — the v1 kanban, verbatim *(exists)*, plus the "→ Compose" graduation action per item (§3.4). Templates and saved drafts continue to live here.

### 10.2 Quick-Post from any workspace

- **`SendTo` destination** *(pattern exists)*: add `{label: "📤 Share / Post…", dest: "content_pipeline"}` to the flow menu — every surface that already renders `SendTo` (contacts research, news stories, briefings, calendar items) gains Quick-Post for free. `/api/flow` handler creates a `DRAFT` post from the content + metadata and opens the modal.
- **Share button** on first-class surfaces: Studio creation detail (next to `ProvenanceBar`), news editorial/briefing headers, wiki draft footer, daily-creation notification actions.
- **QuickPost modal** (global, like the Workspace Studio chat): pre-loaded content + primary asset, platform chips, one compact preview (tap to expand per-platform), caption edit, hashtags, **Schedule** (default = next optimal slot, one tap) / **Post now** / **Open full composer** (deep-links `fridayNavigate({workspace:'content', tab:'compose', post: id})`).
- Voice path: "Friday, post this to LinkedIn and Bluesky tomorrow morning" → the chat tool layer creates the draft + schedule and answers with the Queue deep link (tool: `content_create_post`, Ring 2, same governance as other network tools).

### 10.3 Dock & ambient

The dock's existing 📝 Content icon gains the standard connector-health dot (via `/api/connectors/health`, §2.2) and a badge for HELD/FAILED counts. Publish completions ride the normal notification bell with deep links; no new ambient surfaces.

---

## 11. API Routes (`routes/content_pipeline.py`, Blueprint `content_pipeline_bp`)

Legacy `/api/content/{pipeline,idea,draft,templates,from-template,item,drafts}` in `routes/workflows.py` remain untouched (Ideas tab). New, all JSON envelopes `{ok|status, ...}`, errors in-body with HTTP 200 (creations-route convention):

| Route | Method | Purpose |
|---|---|---|
| `/api/content/posts` | GET | list (filter: status, platform, from/to, source) |
| `/api/content/posts` | POST | create draft (body, assets, platforms, schedule?, source?) |
| `/api/content/posts/<id>` | GET / PATCH / DELETE | read / edit / delete (delete = CANCELLED + optional platform takedowns) |
| `/api/content/posts/<id>/compose` | POST | run composer (`{platforms?, regenerate?}`) |
| `/api/content/posts/<id>/schedule` | POST | set/replace ScheduleConfig (resolves optimal_time; returns conflicts) |
| `/api/content/posts/<id>/publish-now` | POST | immediate dispatch (still fully gated) |
| `/api/content/posts/<id>/cancel` | POST | cancel post or `{target_id}` |
| `/api/content/posts/<id>/release` | POST | release a HELD target after review (`{target_id, ack: true}`) |
| `/api/content/preview` | POST | stateless per-platform preview (§5.1) |
| `/api/content/repurpose` | POST | source → spread of drafts (§9) |
| `/api/content/queue` | GET | upcoming + held + recent history |
| `/api/content/calendar` | GET | `?from&to` → pills + optimal-slot suggestions |
| `/api/content/best-times` | GET | seed + learned tables (`?platform=`) |
| `/api/content/analytics/summary` | GET | dashboard rollup (`?days=30`) |
| `/api/content/analytics/post/<id>` | GET | drilldown series |
| `/api/content/analytics/refresh/<target_id>` | POST | manual poll (budget-aware) |
| `/api/content/insights` | GET | current insight cards |
| `/api/content/platforms` | GET | adapter registry + `status()` per platform |
| `/api/content/platforms/<name>/connect` | POST | begin auth (returns `connect_url` or token-paste/manual descriptor) |
| `/api/content/platforms/<name>/callback` | GET | OAuth loopback redirect target |
| `/api/content/platforms/<name>` | DELETE | disconnect: revoke + purge (`?purge_analytics=1`) |
| `/api/content/platforms/<name>/test` | POST | connection test (private/self post where supported) |
| `/api/content/voice-cards/<platform>` | GET / POST | read/edit platform voice card (§5.6) |
| `/api/content/export` | GET | full local data export (§12.6) |

Chat/agent tools (Ring 2, governance-gated like all network tools): `content_create_post`, `content_schedule_post`, `content_post_status`, `content_repurpose` — thin wrappers over the same service calls so voice and chat can drive the pipeline without new privilege surface.

---

## 12. Privacy & Security

### 12.1 Egress discipline

Every byte that leaves for a platform is either (a) a post body/title that passed the classifier at PUBLIC (or was explicitly human-released from HELD, §7.3), (b) a media file the user attached, transformed deterministically, or (c) protocol overhead (auth, ids). Adapters receive a `PreparedPost` — a closed struct — and have **no access** to conversation history, memory, the wiki, or the vault. Composition prompts to cloud models ride the same `gate_text` path as image prompts today. All decisions land in the egress log; `startup_self_test()` coverage extends to the publisher path (a gate that cannot prove itself operational blocks publishing, not just chat).

### 12.2 Credential handling

- Tokens/blobs only via `credential_store` *(exists)*: vault AES-256-GCM (Argon2id-derived) when `FRIDAY_PASSWORD` is set, Windows DPAPI otherwise, loud plaintext fallback never silent. Files under `~/.friday/platforms/` are permission-hardened (`harden_permissions`).
- Nothing tokenish is ever logged, echoed to the UI, or included in `status()` beyond boolean/expiry facts. The pre-commit secret scanner conventions apply to the new modules.
- `audit_event("platform", event, platform=…)` on connect, refresh, revoke, publish, disconnect → reviewable trail in the security audit log.
- OAuth: PKCE where supported, `state` bound + single-use, loopback-only redirect URIs (`localhost:3000`), scopes requested = minimum for declared capabilities (no read scopes for platforms where Friday only writes, except where the analytics scope is the same grant).

### 12.3 Scope transparency

The Accounts tab translates every granted scope into plain-language capability sentences (§10.1) and shows the *negative space* ("Cannot read your DMs") — sourced from a per-adapter scope dictionary, not free text. A connection whose live scopes exceed the adapter's declared needs (platform migrations do this) surfaces a warning chip.

### 12.4 Content safety & platform integrity

- H1–H4 harm floor (`moderation.scan`) before every publish — block is a refusal with the reason, mirrors `check_content_safety` UX in Studio. Taste is not policed; `nsfw`/violence tags map to platform mechanisms (Mastodon `sensitive`+CW, Reddit `nsfw`, X sensitive-media flag) per §4. Minor mode: publishing requires the adult profile; family-mode Friday composes but queues for guardian release (same `HELD` rail).
- **No engagement automation** (§0.7): no auto-follow/like/comment/DM anywhere in the surface — keeps the user inside every platform's automation policies and Friday out of the manipulation business.
- Rate budgets enforced locally *below* documented ceilings; `Retry-After` honored; jittered thread posting — the adapter set is designed to be a well-behaved API citizen so accounts never get flagged.

### 12.5 The reverse channel

Platform responses are untrusted (§8.6): schema-validated, size-capped, never rendered as HTML, never fed into prompts. Adapter errors are logged locally in full but externalized as fixed content-free strings (the channels-funnel lesson: an exception can embed paths or PII).

### 12.6 User rights (GDPR-aligned by construction)

- **Revoke:** Disconnect = platform-side token revoke where the API exists (LinkedIn, X, Google, Reddit, Mastodon, TikTok) + local credential purge; the card confirms both outcomes separately (revoke can fail platform-side — shown, with a deep link to the platform's own app-permissions page).
- **Erase:** optional purge of that platform's snapshots/targets on disconnect; `DELETE` on a published post offers best-effort platform takedown (`adapter.delete`) where supported, and records the takedown in the publish log either way.
- **Export:** `/api/content/export` → one JSON bundle (posts, targets, snapshots, best-times, publish log) — the user's data walks out the door on request, like everything else in `~/.friday/`.
- **No third parties:** analytics never leave the machine; there is no Friday-side telemetry, aggregation service, or shared "benchmark" pool. Platform data requests (GDPR/CCPA against the platforms themselves) are the platforms' obligation — the Accounts card links each platform's privacy dashboard as a courtesy.

### 12.7 Threat notes (delta to THREAT_MODEL.md)

| Threat | Mitigation |
|---|---|
| Token theft from disk | credential store encryption + hardened perms + audit trail (§12.2) |
| PII in a public post | classifier + HELD gate, human release only (§7.3) |
| Prompt injection via platform payloads | untrusted-input discipline (§8.6, §12.5) |
| Double-posting / spam appearance | idempotency stack (§7.2), conflict detection, local budgets |
| Account lockout via automation flags | conservative budgets, no engagement automation, honest degradation tiers |
| Malicious "staging" misconfiguration | staging is explicit user config with an exposure explanation; unguessable paths; delete-after-confirm (§7.4) |
| Headless-mode credential exposure (Substack/Medium) | opt-in only, session stored encrypted, headed-mode visibility option, feature-flagged off by default (§4.10) |

---

## 13. Federation Integration

The federation is where the pipeline's sovereignty story completes: external platforms rent reach; the federation grants **rights, revenue, and proof**.

### 13.1 Publish → listing

Selecting the Federation chip adds a `listing` target (§4.13): asset registered in the ownership index, `marketplace.create_listing()` with the piece's per-piece license *(chosen at creation — no account-wide default, per the creator-economy decision)* and optional `price_mpsi`, encrypted `CONTENT_OFFER` announcements to trusted peers. Free-commons listings (CC0/CC-BY/CC-BY-SA) enter the browsable commons; priced listings enter commerce with the marketplace policy budgets *(exists: per-item max, daily budget, approval threshold)* governing the *buyer's* side on peer nodes.

### 13.2 Simultaneity

One compose, one schedule: the federation target publishes at the same resolved instant as the external targets (or its own override — e.g. list on the federation a day early for peers/subscribers, then go wide: a "federation-first" toggle in the schedule picker, the creator-economy §26 subscription vision's natural hook).

### 13.3 Unified engagement

Federation metrics (listing views, fetches, purchases, ψ tips, new peer subscriptions) normalize into the same `EngagementMetrics` (§8.2 column) and render in the same Analytics dashboard — "your federation audience out-monetizes your LinkedIn audience 40:1 on a fraction of the impressions" is exactly the insight the dashboard exists to surface. Purchases and tips also appear in the wallet's transaction history with the listing reason string (already how `economy.transfer` records).

### 13.4 Provenance chain, end to end

```
creation manifest (signed, Ed25519)                         (exists)
  └─ derivation edges (repurposed variants, §9.3)           (exists: source_edge)
      └─ publication entries (per platform, §7.7)            NEW
          └─ federation listing (signed listing record)      (exists)
              └─ transfer records (purchases)                (exists)
```

One `trace()` walk answers "where did this piece go, who bought it, and can I prove it's mine" — across external platforms and the federation alike. The Trust workspace's provenance viewer gains the publication/listing hops for free since they live in the same sidecar + ledger.

### 13.5 Later (explicitly out of v1 scope)

Peer re-publication offers (a trusted peer's Friday asks to repost with attribution + ψ split), subscription feeds as standing ψ grants, and federation-native comment threads — all belong to the Federation roadmap (v6), not this pipeline. The data model leaves room (target `options`, listing ids) and nothing here forecloses them.

---

## 14. Implementation Phases

Each phase ships behind `settings.content.enabled` until Phase 2 completes, is independently releasable, and ends green on the offline suite.

| Phase | Scope | Deliverables | Exit criteria |
|---|---|---|---|
| **1 — Spine** | Data model + composer + scheduling + Calendar/Compose/Queue UI | `content_pipeline.py` (store + status machine), `content_composer.py` (adapt/preview/hashtags, voice cards), publisher tick + HELD rail with a **mock adapter**, ContentWS v2 (Compose/Calendar/Queue/Ideas), Quick-Post modal + SendTo destination, `once` trigger in scheduler | Compose→schedule→mock-publish round-trips; previews byte-match payloads; gate holds PII drafts; legacy kanban intact; unit + API tests |
| **2 — First real reach** | LinkedIn + X adapters | OAuth loopback flows, publish (text/images/video; X threads), retries, Accounts tab, counts-level analytics, publish_log + provenance publication entries | Real posts on both platforms from Compose and Quick-Post; token expiry surfaces before failure; thread resume works; double-post defenses tested against fault injection |
| **3 — Meta & Google** | Instagram + YouTube | IG container flow + staging strategy + carousel/reels; YouTube resumable upload + `publishAt` native scheduling + quota budget; insights + Analytics API collectors | Reel and Short ship from one video via profiles; quota exhaustion defers gracefully; native-schedule survives a machine-off test |
| **4 — Open protocols** | Bluesky + Mastodon | App-password/OAuth sessions, facet generation (byte-offset tested), instance discovery, native `scheduled_at`, sensitive/CW mapping, video feature-detect | Threads with links/mentions render with correct facets; NSFW-tagged post carries CW on Mastodon; grapheme meter matches server counting |
| **5 — Intelligence** | Analytics dashboard + repurposing engine | `analytics_collector.py` full (poll plans, normalization, snapshots), Analytics tab, `content_insights` job (attribute lift, best-times learned layer), repurposing spreads + derived-asset provenance, ψ awards | Best-times flips from seed to learned at n≥5; insight cards render with honest n=; one blog post → 9-target spread each individually edited/scheduled |
| **6 — Long tail** | Reddit + Substack + Medium + federation publishing | Reddit submit + rules surfacing + per-sub targets; Substack/Medium handoff packages (+ opt-in headless, feature-flagged); `federation_pub.py` listing + CONTENT_OFFER + unified federation metrics; Discord/Telegram announce | Subreddit rule violations warn at compose, not fail at publish; handoff round-trips URL confirmation; a priced federation listing sells to a test peer and the ψ lands |
| **7 — Optimization** | A/B + learning + recommendations | Variant split scheduling + paired attribution + verdict insights; learning-loop observations promoted into composer heuristics; content recommendations ("your audience gap: video on Tuesdays"); voice-card suggestions from winners | A variant pair runs to a statistically-honest verdict; a promoted heuristic demonstrably alters a composition; recommendation cards act on real account data |

Dependency note: Phases 2–4 and 6 are adapter work parallelizable behind the Phase-1 contract; 5 needs ≥2 live adapters' data; 7 needs 5.

---

## 15. Testing Strategy

House rules apply: the full suite runs **offline** (`FRIDAY_TESTING=1` keeps daemons inert but stores + dispatch callable), no network in unit tests, LLM entry points autouse-stubbed in the API conftest.

- **Unit:** status-machine transitions (every edge in §3.2, including HELD release and PARTIAL re-arm); grapheme/byte counting (X, Bluesky facets — emoji-adjacent links); schedule math across DST boundaries and timezones; conflict detection; best-times resolver (seed vs learned crossover); ψ award idempotency (re-poll storms); normalization tables (fixture payloads per platform → unified shape).
- **Adapter contract suite:** one parametrized battery every adapter must pass against its **mock transport** — capabilities sanity, prepare validation, publish envelope, idempotent-retry behavior, budget bookkeeping, revoke semantics. Real HTTP lives behind `responses`-style fixtures recorded per platform; no live calls in CI.
- **Publisher fault injection:** kill mid-thread, 429 with Retry-After, 401 → refresh → success, ambiguous timeout → verify-before-retry probe finds/doesn't find the post, gate error → HELD.
- **API tests:** every §11 route (blueprint pattern from `tests/api/`); egress-hold path asserts nothing reached the adapter mock.
- **UI:** Playwright specs (existing harness) — compose→preview→schedule flow, drag-reschedule, held release, accounts connect state machine with a stubbed OAuth server.
- **Manual procedures:** per-platform first-connect + first-publish checklists appended to `tests/MANUAL_TEST_PROCEDURES.md` (real OAuth can't be CI'd).

---

## 16. Open Questions

1. **X tier economics.** Is Basic-tier cost acceptable for personal use, or does the X adapter ship "free-tier writes + manual-refresh analytics" as its blessed mode? (Adapter supports both; default undecided.)
2. **Instagram staging default.** Ship with staging unconfigured (hold + explain) — or bundle a guided setup for a user-owned host? Guided setup is better UX but touches infrastructure this spec deliberately keeps user-owned.
3. **Headless tier appetite.** Substack/Medium headless mode: keep permanently opt-in-experimental, or graduate if breakage stays rare? Revisit after Phase 6 telemetry (local only, of course).
4. **Recurrence variation.** How aggressive should the anti-duplicate variation pass be for recurring templates? Platforms differ in duplicate tolerance; needs per-platform tuning data.
5. **Engagement-ψ calibration.** Do the shipped constants (1ψ/like at 10-like thresholds, 200ψ/day cap) price external engagement sensibly against federation-native earnings? Economy-layer question; the award table makes retuning safe.
6. **Publication entries in C2PA terms.** Model `add_publication` as a C2PA *distribution* assertion for maximum interop, or keep the leaner native shape? Leaning native-with-C2PA-mapping, matching the existing manifest philosophy.
7. **Legacy kanban retirement.** After Ideas-tab graduation has been live for a release, fold the v1 store into `content_pipeline.db` or leave the JSON store forever? (Cheap either way; decide on real usage.)
8. **Federation-first windows.** Should "federation a day early, then wide" become a first-class scheduling pattern with subscriber notifications (creator-economy §26 hook), or stay a manual override until v6 federation lands?

---

*Spec ends. Companion reading: `docs/ARCHITECTURE.md` (system overview), `THREAT_MODEL.md` (baseline threat model), `services/channels/` (the adapter pattern this extends), and the Creator Economy & Federation design (Layers 1–3) for the marketplace, economy, and moderation foundations this pipeline stands on.*
