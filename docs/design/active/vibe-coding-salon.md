# The salon: Chat and Code in one room, an artifact panel in every chat, and codebases that run in a box

> **Status:** accepted and in build. Its three original questions were settled on 2026-09-29 as delegated decisions, as recommended, under the owner's "build all pending specs" delegation. Three owner rulings since then are recorded in §12 (LocalStack is out and no component may phone home; Friday never serves tools or pages to the internet from the user's hardware; "This PC" is the default host for published static artifacts). The 2026-09-30 revision also folds in the spike results (S1, S2, S4), a competitor gap pass and the workspace-evolution re-sequencing (§10).
> **Last verified:** 2026-09-30 against main `02035ba6`
> **Implementation:** Phase 1, first increment, on branch `feat/salon-phase1` (not on main): `services/artifacts.py` (the store), `routes/artifacts.py`, the `artifact_put` tool in `services/agent.py`, the fenced-block absorb in `routes/chat.py`, `static/friday_artifacts.js` (the panel and the frame), `FridayChatShell` in `index.html` and `ui_parts/app.html`. Tests: `tests/unit/test_artifacts_store.py`, `test_artifact_tool_and_gate.py`, `test_artifact_panel_ui_files.py`, `tests/api/test_artifacts_routes.py`, `test_chat_absorbs_fenced_artifact.py`, `tests/ui/test_artifact_frame_isolation.py`. **Phase 1b (publish to web), first three increments, on the same branch:** `services/publish_web.py` (the bundle, the scan, the licence check, the one card, "This PC" writes), `services/published_server.py` (the separate static server), `services/publish_hosting.py` (the tunnel, the switch, the status, account connections), `routes/publish.py`, `static/friday_publish.js` (the card body and the Settings section), the `publish_artifact` tool; tests in `tests/unit/test_publish_web.py`, `test_published_server.py`, `test_publish_hosting.py`, `tests/api/test_publish_routes.py`. 1b-D added `services/publish_adapters.py` (Cloudflare Pages and GitHub Pages, tested against fakes; their first real run is the gated check) and `tests/exposure_guard.py` (no test process may start a tunnel binary or bind a public host). **Workspace-ecosystem Phase 1 (pulled forward, §4.9.1):** undo walks backwards, ids are refused not rewritten, the studio chat passes the blast-radius gate, every workspace route requires login and carries a CSP; its Phase 0 UI already existed. **Phase 2, first two increments:** `services/codebases.py` (repo per codebase, templates, steps as commits with receipts, undo that walks back, the one-document preview, the model's context), `routes/codebases.py`, the `codebase_edit` / `codebase_undo` / `codebase_read` tools, "+ Codebase" in the sidebar and the panel's Preview, Files and Changes in `static/friday_artifacts.js`. **Phase 2, §4.11 gaps:** plan-first (`services/plans.py`, the `plan_first` / `plan_approve` / `plan_milestone` tools, the plan strip with "Build this plan", approval opens a task-ledger run, milestones close with typed blockers), one-click export (`codebase_export`, `GET /api/codebases/<id>/export`, a plain zip with nothing of Friday's inside) and point-and-say (`set_pick` / `clear_pick` / `quick_style` in `services/codebases.py`, the `/pick`, `/pick/clear` and `/quick-style` routes, the picker `window.fridayPickerDoc` injected into the preview frame in Point mode, the pick strip's quick actions; the pick is told to the model next turn, a quick style is one CSS rule as a step by "you", never a model call). **Phase 2b ("Improve this workspace", §4.9.1 items 3 and 5):** `services/workspace_bundles.py` (bundle workspaces under the Friday home's `workspaces/<id>/`, every installed version kept, one `workspace_swap` card per codebase head after the manifest check, the CIEDE2000 brand check against the reserved status colours and a headless load, install only on approval, one-click rollback), `routes/workspace_bundles.py`, the `improve_workspace` and `workspace_swap` tools in chat and by voice through the governed path, a declared `boundary` for all nineteen registry workspaces, `static/friday_bundles.js` (the header button, the sandboxed bundle frame attached to the broker, the swap card, the versions with rollback), the served page carrying the installed bundles so the dock shows them under Mine; the codebase panel's Compare and Swap in. A native workspace is refused with the typed blocker `needs_phase_7`. Not yet built in Phase 2: the data viewer of §4.11 (waits on Phase 5), Friday-proposed evolution (§4.9.1 item 4, waits on owner rules Phase 1), the receipt classifier hand-off to goals-and-receipts (its Phase 0 is not on main; receipts are written in the §4.8 shape and the classifier plugs in later), the frame broker's read-only subset. Not yet built in 1b: the voice verb. Everything else in this document is not built. Builds on:
> - `index.html`: `ChatSurface`, `ChatSidebar`, `CodeWS` and its `CODE_TABS`
>   (`DevDiff`, `DevFiles`, `DevGit`, `DevVibe`), `FWin`, `useTabState`,
>   `useNavTarget`
> - `services/code_sandbox.py`, `services/code_engine.py`, `routes/code.py`
> - `governance/action_gate.py` (`authorize`, `create_grant`, the cLaws pin)
> - `services/approvals.py`, `services/approval_executor.py`
> - `services/egress_gate.py`, `services/sensitivity_classifier.py`,
>   `services/file_grants.py`
> - `services/local_context.py` and the voice tools in `services/voice_engine.py`
>   (`delegate_to_friday`, `ask_local_for_context`, `answer_share_request`,
>   `revise_share_request`, `voice_restrictions`)
> - `services/residency_arbiter.py`, `services/seat_binding.py`,
>   `services/seat_transparency.py`, `services/one_key.py`,
>   `services/credential_store.py`, `services/cost_meter.py`
> - `services/workspace_studio.py`, `services/boot_guard.py`
> - `services/task_ledger.py`, `services/compaction.py`
>
> **Converges with (does not duplicate):**
> - [`workspace-ecosystem.md`](workspace-ecosystem.md). The salon is its
>   "Forge" (Phase 3), and its bundle, broker and sandboxed iframe are used
>   here unchanged.
> - [`grow-button.md`](grow-button.md). Self-editing is its Lane B2, with its
>   untouchable set and its non-model applier.
> - [`friday-builds-agents.md`](friday-builds-agents.md). Rules FA1–FA13
>   apply to anything that runs in the box.
> - [`goals-and-delivery-receipts.md`](goals-and-delivery-receipts.md). Every
>   salon change ends in a delivery receipt.
> - [`owner-rules-and-anomaly-detection.md`](owner-rules-and-anomaly-detection.md).
>   Owner rules apply to the salon's outward actions.
> - [`avatar-visual-genome.md`](avatar-visual-genome.md) Appendix A. This is
>   the positron and negatron model the owner decided on.
> - [`laya-across-the-harness.md`](laya-across-the-harness.md)
> - The north star, `docs/design/north-star/`. §14 maps this spec onto it, and
>   amendment A8 records the two places it departs from it.
>
> **Consumes, from parallel work not yet on main at `780e31fa`:**
> - **The voice contract** and **the private-summary handoff.** They are being
>   published by the voice work. This spec uses them and defines neither
>   (§6).
> - **Workspace deep-links, maximized tabs and item actions.** These are being
>   built now. `navigate_to` is already on main.
>
> **Written:** 2026-09-29

The owner's idea (2026-09-25, verbatim):

> "our local vibe coding salon... a Replit clone that lives on my hardware
> drinking my tokens, or drinking someone else's if I see fit."

> "Perhaps that artifact window should also be accessible to models not
> working on code too."

> "Vibe coding new workspaces, or improvements to existing workspaces, should
> live here too."

The owner's added requirement (2026-09-29, verbatim):

> "I want all of this to be workable in voice-first mode too. No restrictions
> unless the user explicitly sets them. Voice mode should be able to fire
> workflows that involve the local model too, in the event that private info
> needs to be summarized without any PII."

And the question that prompted this spec (verbatim): *"Is the vibe coding
workspace that combines with the chat workspace going into the pending specs?
If so, we may be able to learn some things from these two links."* The links
were LocalStack and NVIDIA OpenShell. §2 answers what Friday takes from each.

Code citations are `path:line` on main `780e31fa`. Paths are relative to
`src/agent_friday/` unless they start with `index.html`, `ui_parts/`, `docs/`
or `tests/`. `index.html` line numbers drift, so the identifiers are
authoritative.

**Evidence registers**, as in `docs/design/ROADMAP.md`:

- **VERIFIED**: read in the tree, or on the cited page, during this pass.
- **INFERRED**: a conclusion from verified facts, with the reasoning shown.
- **UNKNOWN**: not settled. The check that would settle it is named, and most
  of these are spikes in §10.

---

## 0. Summary for the owner (one page)

**What changes for you.**

1. **Every chat gets a panel on the right.** When Friday makes something you
   would rather see than read, it opens beside the conversation. That can be
   a table, a chart, a draft, a small working app, or a diff. This applies in
   every chat and with every model, not only coding chats. You can edit the
   thing in the panel, ask for changes, go back to any earlier version, or
   save it.
2. **"+ Codebase" sits under "+ New chat" and "+ Project".** It opens a chat
   whose panel has three tabs:
   - **Preview:** the thing running.
   - **Files:** what it is made of.
   - **Changes:** what changed and when.

   Every change Friday makes is saved as a step you can undo. "Undo that"
   takes you back one step. New workspaces for Friday, and improvements to
   existing ones, are built here.
3. **The code runs in a box.** Your vault, your keys, your mail and Friday's
   own settings are not in the box, and nothing in the box can reach them.
   - **Inside the box there are no restrictions you didn't set.** Friday
     installs packages and fetches from the internet as the work needs. Each
     time, she says what she did, and it goes on the change's receipt.
   - **Friday asks first only where she already asks today:** anything that
     sends or changes something outside the box, like publishing, emailing or
     posting to an API, and anything that would show your private data to a
     cloud model.
   - **"Trust this codebase"** turns a question into a standing yes, scoped to
     that one codebase. You can take it back at any time.
4. **You choose who does the work, and you can always see it.** The panel's
   header names the model doing the work and whose key is paying.
   - Small edits run on your local model by default.
   - The heavy lifting goes to Opus or Sonnet when you allow it, including
     Claude's own coding agent running inside the box.
   - A codebase can use someone else's key. Its spending is metered
     separately, and Friday never uses it outside that codebase.
5. **It all works by voice.** You can say "make the header bigger", "undo
   that", "ship it to the preview", or "what changed?". Friday reads each
   question aloud in a sentence or two, and your "Friday, allow it" counts
   the same as a click. A few things are easier on a screen: judging how
   something looks, reading a long diff line by line, and typing a key.
   Friday tells you when that is the case, and she never refuses to try by
   voice.
6. **Private data stays private.** If a codebase touches anything private,
   such as your notes, a spreadsheet of sources or a contacts export, the
   cloud model never sees the raw data.
   - Your local model writes a summary of its shape instead, with no names or
     identifiers, plus realistic fake rows to build against.
   - The real data is used only when the app runs in the box on your machine.
7. **Friday can improve herself, on a copy.** Friday edits a copy of herself,
   which runs next to the real one so you can compare them.
   - The tests must pass, and you approve the swap. Undo is one click.
   - Changing her own safety checkpoint or her signed laws needs a separate,
     unmistakable approval, never a routine one.
   - This only works on a machine that has a git checkout, like yours.
8. **Later, workspaces become shareable.** A workspace built here can be
   exported as a signed file and, once federation is switched back on, shared
   with other Fridays. Its card shows proven value (positrons) next to
   measured cost (negatrons), with flags kept apart, as you decided on
   2026-09-22.

**What it costs.**

| | Local path | Cloud path (your choice, per codebase) |
|---|---|---|
| **Privacy** | Nothing leaves the computer. There is no telemetry, from Friday or from anything the salon runs. | The cloud model sees your code and your instructions. Private data reaches it only as the local model's summary, and the payload card shows that summary first. |
| **Money** | Nothing. | Your tokens or the named other party's, metered per codebase and shown in the header and in Costs. A typical small app runs **UNMEASURED** (§11). Phase 1 measures it. |
| **Speed** | Small edits take seconds. Big changes are slow, because your 12 GB card is mostly held by the brain. | Big changes are fast. The preview updates in about a second whichever path does the work. |
| **Effort** | About **12 agent-weeks** (about 60 agent-days) across eight phases and three short spikes (§10), each shippable alone. The artifact panel ships first, in about 1.5 weeks. | Same. |

**Delegated decisions, as recommended (2026-09-29).** All three were taken as
recommended under the owner's "build all pending specs" delegation, not picked
by the owner; the owner may overrule any of them (§12 has the reasoning):

1. **What the box does before you've said anything: "announce".** Installs
   and reading from the internet go ahead and are announced. Anything that
   sends or changes something outside the box asks first, as everywhere else
   in Friday. "Ask me first" stays available as an owner rule.
2. **Friday may edit her own safety checkpoint and signed laws in the
   salon**, only with the loud approval of §7.4, and only through the copy.
3. **Someone else's key may live in your Friday**, bound to one codebase,
   metered separately and removable in one click.

---

## 1. What exists today (grounded)

### 1.1 Chat and Code are two rooms today, and the doors are already there

- **Chat is not a workspace. It is one component used everywhere.**
  - `ChatSurface` (`index.html:7828`) renders both the docked chat and every
    chat window. Its "+ New Chat" is at `index.html:7951`.
  - The sidebar is `ChatSidebar` (`index.html:42763`). "+ New chat" is at
    `:42853` and "+ Project" at `:42863`. A new chat inside a project posts
    `{title, project}` (`:42784`), and projects live at `/api/projects`
    (`routes/conversations.py:22-26`). **VERIFIED.**
  - **So "+ Codebase" is a third button in that row.** It creates a
    conversation that carries a codebase id, the same way a project chat
    carries a project.
- **Code is a workspace.** `CodeWS` (`index.html:24802`) has six tabs in
  `CODE_TABS` (`:24772`): Repos, Vibe, Git, Files, Procs and Logs. **VERIFIED.**
  - There is no editor, no terminal and no preview.
  - `DevVibe` (`:23909`) sends natural language to `/api/code/plan`, and
    `/api/code/apply` writes whole files (`routes/code.py:516`, `:685`).
  - `DevFiles` is read-only (`/api/files/list|read`).
  - `DevGit` does diff, branch, commit, push and PR (`routes/code.py:286-419`).
  - `DevDiff` (`:23716`) already renders diffs.
  - **These components are the salon's Files and Changes tabs.** They move
    into the panel and are not rebuilt.
- **No chat has an artifact panel.** **VERIFIED:** there is no canvas, artifact
  or side-panel component in `ChatSurface`. The nearest precedents are:
  - NewsWS's 280 px side column, which holds a chat beside content;
  - Workspace Studio's per-workspace chat (`services/workspace_studio.py:1-22`);
  - HTML previews in iframes in Studio (`index.html:18739`, `:19012`,
    `:19411`), with `sandbox="allow-scripts …"` on the first two.
- **One existing iframe is unsafe.** NewsWS renders briefing HTML with
  `srcDoc` and **no `sandbox` attribute** (`index.html:26483`), so the
  briefing's scripts run in Friday's origin. **VERIFIED.** It is filed as a
  separate fix and is not part of this spec's phases. It shows the failure the
  artifact panel must be built to prevent (§4.3).

### 1.2 Running code today

- **`/api/vibe-code/launch` opens a real Claude Code console on the owner's
  repo with `--dangerously-skip-permissions`** (`services/code_engine.py:45-86`).
  It is called from Career, Futurespeak and Studio (`index.html:9674`,
  `:14755`, `:18869`). **VERIFIED.**
  - The environment is inherited, which contradicts FA2.
  - `routes/code.py` calls neither `action_gate` nor `authorize`.
  - That is defensible for the owner's own repos. It is not defensible as
    anything a shared codebase can reach (`workspace-ecosystem.md` Phase 3
    says the same).
- **The existing "ClaudeCodeAdapter" is neither Claude Code nor the Agent
  SDK.** It runs Friday's own loop and regex-scrapes file paths
  (`services/worker_adapters/claude_code_adapter.py:1-7`, `:25`). **VERIFIED.**
- **`services/code_sandbox.py` (789 lines) is the real sandbox, and it runs
  Python only.** **VERIFIED**, from its own docstring `:1-44`:
  - **Host backend:**
    - a Low-integrity token with privileges dropped;
    - a Job Object limited to **one process**, with memory, CPU and wall-clock
      caps;
    - an environment built from nothing;
    - a temporary working folder labelled Low;
    - output caps.
  - **Its gaps, in its own words:**
    - the network block is inside Python, "NOT an OS boundary";
    - a Low process "can still READ what the owner's account can read".
  - **So host runs are classified OUTWARD and wait for a yes**
    (`classify`, `:118`, wired into `governance/action_gate.py:431-433`).
  - **Windows Sandbox backend** (`build_wsb` `:635`): a disposable VM with
    networking off. Runs there are INTERNAL. The module never turns the
    Windows feature on.
- **The owner's machine** (checked read-only during this pass, 2026-09-29):
  - Windows Sandbox: **not installed** (`WindowsSandbox.exe` is absent).
  - WSL 2 with `Ubuntu-24.04` and `docker-desktop`: **installed, stopped.**
  - Docker 28.1.1: **installed.**
  - Node 24.13.1: **installed.**
  - Podman: **not installed.**

### 1.3 The gate, grants, keys and meters

- **One checkpoint.** `governance/action_gate.py` (809 lines) does four
  things (`:1-25`). **VERIFIED.**
  - It checks the cLaws HMAC pin.
  - It classifies each action as INTERNAL or OUTWARD.
  - It holds OUTWARD actions for a chat yes, a card or a scoped grant.
  - It writes signed receipts to `~/.friday/decision-bom.jsonl`, and it fails
    closed.
- **Scoped grants already exist.** `create_grant(tools, scope,
  expires_in_seconds, max_uses)` (`:524`), with `revoke_grant` and `_use_grant`
  (`:546-563`). They are keyed by tool and scope.
  - **"Trust this codebase" is a grant with `scope = codebase:<id>`.** No new
    ledger is needed.
- **Keys.**
  - `services/one_key.py` holds one Anthropic or OpenRouter key per install.
  - `services/credential_store.py` encrypts at rest.
  - **Nothing records whose key paid for a call.** `cost_calls`
    (`services/cost_meter.py:455-460`) has provider, model, tokens, cost,
    workspace, kind, schedule and run columns, and no key or payer column.
    **VERIFIED.**
- **Seats.**
  - `residency_arbiter.py` owns the GPU.
  - `seat_binding.py` makes the residency plan drive `capability_routing`.
  - `seat_transparency.py` already announces every seat change in the chat.
    That is the precedent for the salon header naming the model and the key.
    **VERIFIED.**

### 1.4 Self-editing guards today

- `/api/code/apply` runs `boot_guard.safe_mode`, then `check_self_edit` per
  file, then `check_scope`, and refuses the whole plan if any check fails
  (`routes/code.py:704-753`). **VERIFIED.**
- **Gap:** `BOOT_CRITICAL` (`services/boot_guard.py:56-62`) names only
  `server.py`, `core/__init__.py`, `agent.py`, `model_router.py` and
  `boot_guard.py`. So `governance/action_gate.py`,
  `governance/proof_of_integrity.py` and `services/egress_gate.py` **can be
  written by `/api/code/apply` today.** **VERIFIED.**
- **cLaws.**
  - The text is `CLAWS_TEXT` (`governance/proof_of_integrity.py:42`).
  - Its HMAC is pinned in `~/.friday/governance/claws.pin.json`
    (`action_gate.py:77-107`).
  - An edited text therefore *stops outward actions* rather than being
    refused at write time.
- **Not built.** No grow button and no worktree use exist anywhere in `src/`.
  **VERIFIED.**

### 1.5 Voice today

- **Voice can do anything chat can.** `delegate_to_friday` hands a request to
  the full agent in the background. Outward actions it takes "still raise
  approval cards, exactly as in chat" (`services/voice_engine.py:315-326`).
  **VERIFIED.**
- **The limits voice has are listed, not hidden.** `voice_restrictions()`
  (`voice_engine.py:753-797`) lists them for the Voice settings tab:
  - the user's own settings;
  - approval cards;
  - the never-send list;
  - a 20-second limit on direct tools;
  - room-mode approvals.
- **Private context already has a path to a cloud model**
  (`services/local_context.py:1-38`, commit `8c4227e0`):
  1. The local model answers from private data.
  2. People become relationship placeholders, and identifiers become
     numbered placeholders.
  3. The egress floor refuses never-send material outright.
  4. A payload card (`local_context_share`) shows the exact text and both
     model names.
  5. Spoken decisions count only when "the user's own latest spoken words say
     so". In "Several people" room mode they count only when the speaker
     names Friday.
- **There is no general "answer this approval card by voice" tool yet.** The
  only spoken decision tool is `answer_share_request`, for share cards.
  **VERIFIED:** no other card-decision tool is in `voice_engine.py`. §6.3
  depends on the voice contract to close this.

---

## 2. The two links: what they are, and what Friday takes from each

### 2.1 LocalStack: borrow the pattern, depend on nothing

**What it is** (all **VERIFIED**; sources in §15):

- **The original product.** LocalStack emulated AWS services (S3, SQS, SNS,
  DynamoDB, Lambda, Kinesis, API Gateway, CloudFormation and more) in one
  Docker container on edge port 4566. Cloud apps could be built and tested
  offline, with no account and no bill.
- **Archived.** The GitHub repository was **archived on 2026-03-23**. The
  README points to "a single, unified image".
- **Last open release.** The last Apache-2.0 release is **v4.14.0
  (2026-02-26)**.
- **Now needs an account.** Since 2026.3.0, `latest` is the unified image, and
  it requires an account and `LOCALSTACK_AUTH_TOKEN`. The temporary bypass
  flag stopped working on 2026-04-06.
- **Licence.** The README now lists Apache-2.0 **plus an EULA**.
- **Free plan.** The free "Hobby" plan is for non-commercial use only.

**What that means for Friday.**

- **Friday cannot bundle it or depend on it.** Friday is distributed to other
  people. The current image needs an account and a token, which is a phone
  home, and its licence terms change what users may do. The frozen v4.14.0
  image is legally usable but gets no security fixes, so it is not a
  foundation.
- **The lesson is worth more than the product: an app under construction
  should never need the real cloud.** LocalStack's real idea is that
  everything an app calls out to has a local stand-in at a known address, so
  the whole app runs and is tested on your machine. For a vibe-coded app that
  matters three times over:
  - **Privacy.** The app's test traffic never leaves the machine.
  - **Money.** No cloud bill while you iterate.
  - **Safety.** A bug in half-built code can't email real people or write to
    a real bucket.

**What Friday builds: the backstage (§4.6).** Each codebase gets local
stand-ins for storage, a database, a queue, email, auth and key-value storage.
They are Friday's own small shims where that is enough, and openly licensed
tools where it isn't:

- moto server (Apache-2.0, active) for AWS APIs;
- Mailpit (MIT) for email capture;
- Azurite (MIT) for Azure storage.

MinIO is out: its repository says it is no longer maintained, and it ships
source only. **LocalStack is out entirely**, including the earlier idea of
connecting a user's own install. The evidence (localstack.cloud/pricing,
checked 2026-09-29): the free Hobby plan requires a LocalStack account,
"fully offline / air-gapped image delivery" is only on the top tiers, and
"Telemetry Sharing" is *Enforced* on the free plan. The owner's ruling,
verbatim: *"I don't want any telemetry built into our system so that's out.
But let's emulate as much as we can and learn as much as we can and build
our own as much as we can."* The salon's general rule follows: **no
component that phones home, ever**, not as a default and not as an option.
What Friday takes from LocalStack is knowledge: spike S4 studied the archived
repository and its verdict is in §4.6.1 and
[`docs/design/research/2026-09-30-localstack-archived-repo-study.md`](../research/2026-09-30-localstack-archived-repo-study.md).

### 2.2 NVIDIA OpenShell: copy the policy model now, adopt the runtime only past a gate

**What it is** (all **VERIFIED** unless marked; sources in §15):

- **Maturity.** Apache-2.0 and active, about 10.6k stars. The latest stable
  release is **v0.1.2 (2026-09-28)**, with roughly weekly releases. Some APIs
  are marked experimental. The earlier "0.1.0 coming soon" description is out
  of date.
- **Architecture.**
  - A **gateway** control plane handles sandbox lifecycle, policy delivery
    and credentials, using per-sandbox JWTs.
  - A **supervisor** inside each sandbox mediates everything over an
    authenticated protocol.
- **Backends:** Docker, Podman, Kubernetes and a libkrun MicroVM.
- **Isolation.**
  - Landlock covers the filesystem.
  - seccomp user-notification stages network calls.
  - An outer network fence blocks everything except the path to the
    supervisor.
- **Policy is YAML with five sections:** `filesystem_policy`, `landlock`,
  `process`, `network_policies` and `network_middlewares`.
  - **Egress is denied by default.**
  - The filesystem and process sections are fixed at creation. The network
    sections reload live.
  - An endpoint rule names host, port, protocol and enforcement. It then
    either grants `access: read-only | full` or lists `rules` with method and
    path globs.
  - **Rules are bound to specific binaries**, for example "curl may GET
    api.github.com".
- **A policy prover** checks a proposed change with an SMT solver before
  applying it. The solver is **UNKNOWN**.
- **Credentials are injected at the proxy.** The docs say agents "never see
  real credentials", and credentials only go to their approved endpoints.
- **SDKs:** Python, TypeScript, Go and Rust.
- **Platforms.** Linux and Apple Silicon are supported. **Windows works only
  through WSL 2 plus Docker Desktop, marked experimental.**
- **Telemetry is on by default.** Anonymous operational metrics are sent, and
  the README lists what they exclude. Opting out takes
  `OPENSHELL_TELEMETRY_ENABLED=false` on the gateway. Where the data goes is
  **UNKNOWN**.

**How closely it maps onto Friday** (**INFERRED**, rule by rule):

| OpenShell | Friday today | In the salon |
|---|---|---|
| deny-by-default egress | OUTWARD actions wait (`action_gate`) | the box's network goes through one proxy. What it allows by default is decision 1 |
| network policy edited live | scoped grants (`create_grant`) | **an approval card is a policy edit**: "allow" writes a rule, "trust this codebase" writes a grant-scoped rule, revoke deletes it. No box restarts |
| method + path rules | the two questions: *does data leave?* and *does it change anything outside?* | GET, HEAD and OPTIONS answer "reads". POST, PUT, PATCH and DELETE answer "changes something outside". Friday's existing cards ask about the second (§4.5) |
| rules bound to binaries | none | rules bound to the **codebase** and to its **tool** (npm, pip, the app itself, the coding agent) |
| credentials injected at the proxy | the vault never enters a subprocess (FA2), and the broker pattern (`grow-button.md` §7.6) | **the model API key is injected at Friday's proxy.** The coding agent in the box never holds it (§4.7) |
| a policy prover before a change | none | a deterministic check that a new rule never widens past the cLaws floor (§4.5). A solver is not needed at this size |

**The verdict: both, in that order.**

1. **Copy the pattern now.** The salon's policy file (§4.5) uses OpenShell's
   vocabulary and shape: network endpoints, method and path rules, a subject,
   and live-reloadable network sections. A later OpenShell backend then reads
   it with a mechanical translation. Friday's own proxy enforces it on every
   box tier, including those OpenShell cannot run on.
2. **Offer OpenShell as a box backend only once it passes a gate** (spike S3,
   §10):
   - it runs on the owner's WSL 2 + Docker Desktop;
   - telemetry is off and is **proven off by a packet capture** during a full
     session, because "no telemetry, ever" is a rule and not a preference;
   - its Windows support is no longer marked experimental, or the spike shows
     it is stable here anyway;
   - its network policy can be driven from Friday's grant ledger through the
     SDK.

   Until then Friday's own WSL 2 container backend fills that slot. **It is
   never a default and never required**, because a new Windows user without
   WSL 2 must still get a working salon (§4.4).

   **S3 result (2026-09-30):** it runs here after two WSL-specific fixes
   (the supervisor callback and the certificate SAN); telemetry is off and
   proven off on the gateway side by capture, on the sandbox side by a
   connection log; Windows support is not stable out of the box, which is
   what the two fixes show; the policy is driven live through the CLI in
   98 ms, through the SDK unverified. B2-OS stays behind the gate with the
   recipe written down. A finding beyond OpenShell: Docker Desktop calls
   Docker's hosts and Bugsnag at every start with analytics off, so any B2
   backend that must not phone home uses Podman or plain containerd inside
   the distro, not Docker Desktop. Details in
   `docs/design/research/2026-09-30-s3-openshell-wsl-spike.md`. **Ruling from
   the spike (2026-09-30):** Docker Desktop's crash reporter cannot be
   switched off below the Business tier and contacted Bugsnag at start, so
   Docker Desktop is not a required dependency for ordinary users under A3;
   Friday's own B2 backend and B2-OS target Podman inside the WSL distro,
   proven by capture, with Docker Desktop accepted only when the user already
   has it.

---

## 3. STORM: questioning it from six perspectives

Each perspective asked its hardest questions of the proposed shape. The
answers changed the design, and §3.7 says how.

### 3.1 The Replit / Codespaces product lead

- *"What is the loop, in seconds?"* Replit's lesson is that the product *is*
  the preview refresh: type, see it change, keep going. A salon whose preview
  needs a container start on every change loses to a browser tab. **So the
  default tier must need no process at all.** It should be a sandboxed iframe
  that renders the app directly, as chat artifacts do elsewhere. Heavier
  tiers are for codebases that genuinely need a server.
- *"Checkpoints, not commits, in the user's vocabulary."* Replit's Agent
  checkpoints every step and offers rollback. Git is the right storage.
  "Commit" is the wrong word for a journalist, so the UI says **step** and
  **undo**, and the Changes tab shows plain-language summaries over the git
  log.
- *"Where do secrets go?"* Replit and Codespaces both have a Secrets pane
  whose values become environment variables in the container. The salon must
  **not** copy that: an environment variable is exactly what a coding agent,
  a postinstall script or a crash log leaks. Secrets stay at the proxy
  (§4.7).
- *"How is the environment declared?"* Codespaces uses `devcontainer.json`.
  The container tier honours an existing `devcontainer.json` rather than
  inventing a format, and the other tiers ignore it.

### 3.2 The sandbox security engineer

- *"Which of your boxes does Windows actually enforce?"* This is the
  question that shaped §4.4. Only the VM-backed boxes enforce both reads and
  network: Windows Sandbox, a WSL 2 container, or a WHP microVM. **The browser
  iframe enforces too**, because the browser is the boundary: an opaque origin
  plus CSP. The host Low-integrity box enforces *writes* but not reads, and
  not the network for anything other than Python. `code_sandbox.py` already
  says so.
- *"Your proxy is only a boundary if the box can't go around it."* True in a
  VM or container with a network fence. False in the host box, where Node
  ignores proxy environment variables whenever it likes. So the host box is
  never described as network-fenced, and the UI says so plainly.
- *"The preview is a web page on loopback, and Friday trusts loopback."*
  This is the sharpest point in the review.
  - Friday authenticates any request from 127.0.0.1
    (`core/__init__.py:579-680`), and a grep of `src/` finds **no Origin or
    Sec-Fetch-Site check** (**VERIFIED**).
  - A preview served from a dev server at `127.0.0.1:5173` is a different
    origin, but its requests come *from loopback*. Whether it can make a
    state-changing call to Friday depends on which routes accept a
    CORS-simple request. That is **UNKNOWN**, and a separate investigation is
    filed.
  - **Either way, the salon may not ship a server-backed preview until
    Friday refuses cross-site state-changing requests.** This is a
    prerequisite of Phase 4, and it protects every other tab the user has
    open too.
- *"What stops an install script?"* npm and pip run arbitrary code at
  install time. These controls apply in the tier the codebase runs in:
  - the deterministic scan plus the read-only reviewer (the queued
    SkillScan-style pattern);
  - the 24-hour cooldown from `workspace-ecosystem.md` §4.5;
  - exact pins (FA12).

  In the host box, install scripts are off (`--ignore-scripts`), because
  nothing else there would contain them.

### 3.3 The OpenShell maintainer

- *"Don't fork our policy language; target it."* Accepted. The salon's
  policy file uses OpenShell's section names for network rules and keeps
  Friday's additions (codebase subject, grant id, expiry) in a namespaced
  block that a translator drops.
- *"Credentials at the proxy is the feature. Don't regress it by passing the
  key in an env var 'just for the SDK'."* Accepted, and it applies to Claude's
  own coding agent too. The agent in the box points its API base URL at
  Friday's proxy. The proxy adds the key header on the way out, for that host
  only (§4.7).
- *"Windows through WSL is experimental for a reason."* Also accepted. The
  maintainer cannot promise Windows today, so Friday does not either.

### 3.4 The LocalStack user

- *"The reason I used LocalStack was that my tests never hit AWS, not
  emulation fidelity."* So the backstage aims for the handful of calls a
  vibe-coded app actually makes: put and get a file, a row, a queue message,
  an email and a login. It does not aim for API completeness. Fidelity past
  that is what moto server is for.
- *"The day LocalStack needed a token, my CI broke."* This is the argument
  for depending only on stand-ins Friday can run with no account, and for
  keeping a stand-in behind an address the app reads from configuration. The
  backend behind that address can then change without touching the app.
- *"Tell me when I'm about to hit the real thing."* "Go live", meaning
  swapping a stand-in address for a real endpoint, is an outward change. It
  gets a card that names each service being switched.

### 3.5 The non-engineer creator (a journalist)

- *"I don't know what a diff is, and I shouldn't have to."* The Changes tab
  leads with Friday's one-line summary of each step, then the before and
  after preview. The code diff is one click further in.
- *"I'll mostly be talking, not typing."* This is §6. It has to work without
  looking at the screen, and it has to say so when looking would help.
- *"My sources' names can never end up in someone else's model."* This is
  §6.4. Private data is summarized locally before any cloud model sees
  anything, and the payload card shows exactly what goes.
- *"What did this cost me?"* The header shows the running total for this
  codebase in dollars, and whose dollars.
- *"Can I just say 'build me a tracker for my FOIA requests'?"* Yes. That is
  "+ Codebase" with a template, and the first preview appears within one
  turn.

### 3.6 The federation-market reviewer

- *"What are you actually selling?"* Nothing. The owner decided on
  2026-09-22 that positrons are proven value, negatrons are measured adoption
  cost and flags are a separate signed channel. There is no price.
  (`avatar-visual-genome.md` Appendix A.)
- *"Who measures the negatrons?"* The installer, on the adopting machine,
  signed. For a workspace that means its bundle size, its brokered token use
  per week and its permissions, all measured through the broker's audit log.
  This settles `workspace-ecosystem.md` §4.6's "cost to run" in favour of the
  owner's decision: running cost is one of the measured adoption costs.
- *"Your federation handshake can't fail."* Correct.
  `_verify_peer_card` still falls through on a failed signature
  (`workspace-ecosystem.md` §3). Federation is also deferred. **Sharing ships
  as a signed file first (Phase 8), and federation sharing waits on that fix
  being a prerequisite.**
- *"Agents will flood the queue."* Every version gets a machine review. Human
  review is narrowed to what gets flagged. `authored_by_agent` is shown as a
  byline, not as a warning.

### 3.7 Synthesis: what the questioning changed

1. **The default box is the browser.** The panel renders in a sandboxed,
   opaque-origin iframe with a strict CSP (§4.3). That tier needs no process
   and no install, and the browser enforces it. Most of what the owner will
   build lives there: single-file apps, charts, tables, and workspace bundles
   (which `workspace-ecosystem.md` already defines as one HTML file).
2. **Server-backed codebases get a VM-backed box where one exists.** Where
   none exists they get the host box, and Friday says plainly what the host
   box cannot guarantee (§4.4).
3. **Policy is OpenShell-shaped from day one.** OpenShell itself arrives
   later, behind a gate.
4. **Keys live at the proxy, never in the box**, and that includes the key
   of Claude's own coding agent.
5. **The backstage replaces the cloud during development.** Going live is an
   outward card.
6. **Cross-site protection for Friday's own API is a prerequisite** for any
   server-backed preview.
7. **Voice is a peer surface, not an afterthought.** Private data crosses to
   a cloud model only as a local summary, through machinery that already
   exists (`local_context.py`) and the voice contract now being published.

---

## 4. Design

### 4.1 One idea, two depths

- **The artifact panel** (Phase 1) belongs to *every* chat. It holds things
  a model made that are better seen than read.
- **The codebase chat** (Phases 2–5) is a chat whose panel is bound to a
  codebase: a git repository plus a box. Its panel adds Preview, Files and
  Changes.

The codebase chat is the artifact panel with a repository behind it. Nothing
in Phase 1 is thrown away later.

### 4.2 The artifact panel

**What it shows.** An artifact has a `kind`:

| kind | Rendered by | Notes |
|---|---|---|
| `markdown` | `FridayDoc`, the existing renderer | drafts, notes and letters, editable in place |
| `table` | a sortable table from rows plus a schema | exported as CSV |
| `chart` | a chart from rows plus a spec | the chart library is chosen in Phase 1 from what `index.html` already loads |
| `html` | the sandboxed frame (§4.3) | small apps and mockups |
| `diff` | `DevDiff` | also used by the Changes tab |
| `image` / `svg` | `<img>`, with SVG sanitised or framed | |

**Every artifact carries the north star's artifact contract** (§24.1,
§30.14): id, principal scope, optional task and goal, version, sha256,
sensitivity, source refs, provenance manifest id, `qa_status` and
`created_at`. The panel's kinds extend §30.14's `kind` list: `markdown` maps
to `document`, `table` to `spreadsheet`, and `chart`, `html` and `diff` are
added. A saved artifact's delivery receipt is the step receipt of §4.8.

**How a model makes one.** It uses one tool:

```
artifact_put(conversation_id, artifact_id?, kind, title, content, meta)
```

It creates or updates the artifact, and every update is a new version. The
tool is `INTERNAL` for the gate, because it writes only to Friday's own
output store.

- **Local models without reliable tool calls** can emit a fenced
  ```` ```friday-artifact ```` block instead. The server parses it into the
  same call. This is the pattern `workspace_studio`'s ```` ```friday-customize ````
  block already uses.
- **The artifact tool is in the always-resident tool set only when the panel
  is enabled.** Its schema is small (about 150 tokens, **INFERRED** from the
  other tools of that shape). `tool_catalogue.ALWAYS_RESIDENT` is where it
  goes.

**Where it lives.**

- Artifacts are stored at
  `~/.friday/artifacts/<conversation_id>/<artifact_id>/v<N>.json`, with an
  `artifact.json` index beside the versions, written the way conversations
  are: plain JSON, tmp + fsync + replace. (An earlier draft said "encrypted
  at rest like conversations"; the conversation store is not encrypted at
  rest, and the artifact store matches it. Encryption at rest for both is a
  separate decision.)
- **As built (Phase 1):** the fenced fallback accepts a whole-block JSON form
  and a header-line form (`\`\`\`friday-artifact {"kind": "html", "title":
  "…"}` followed by raw content), because a local model writes HTML far more
  reliably than JSON-escaped HTML. The page holds **one** event stream for
  every chat surface: a browser allows about six connections per host and
  Friday's page already keeps several open, so one stream per surface queued
  ordinary requests behind them (measured at 15 s for one fetch on the
  desktop page before the change). Charts are drawn in SVG by the panel
  itself; `index.html` loads no chart library and React plus SVG is what it
  already has.
- **Off the record means nothing is written.** `off_record.py` is asked
  before every write, as every other store asks it. Artifacts in an
  off-the-record chat live in memory and are gone when it ends.
- Versions are kept, never overwritten. The panel's timeline scrubs through
  them, and "restore" makes a new version.

**Editing in the panel.**

- A `markdown` artifact is editable by hand.
- A `table` artifact's cells are editable.
- A hand edit is a new version authored by "you". Friday sees it on her next
  turn as a diff. That matters because the model must know the user changed
  something before it edits again.

**Where it appears.**

- On the right of `ChatSurface`, collapsible, remembering its width. In a
  narrow window it becomes a tab over the chat.
- It opens by itself on the first `artifact_put` of a turn. An artifact
  message in the transcript reopens it.
- It works in the docked chat and in chat windows, because both are
  `ChatSurface`.
- **Maximized tabs** (the work in progress) give the panel its own browser
  tab. **Item actions** add "Open in panel" to any item that can be an
  artifact, such as a note, a wiki page or a CSV in Files.

`index.html` and `ui_parts/app.html` both change, per `AGENTS.md`.

### 4.3 The frame: the browser as the first box

This is the one mechanism that makes an `html` artifact and a static codebase
preview safe. It is `workspace-ecosystem.md` §4.3, used unchanged.

```html
<iframe sandbox="allow-scripts" srcdoc="…"></iframe>
```

- **`allow-scripts` without `allow-same-origin` gives the frame an opaque
  origin.** The frame has no cookies, no storage shared with Friday, no reach
  into Friday's DOM, and no same-origin fetch.
- **A CSP inside `srcdoc`** sets:
  - `default-src 'none'`;
  - `script-src` and `style-src` to `'unsafe-inline'` plus the salon's
    package CDN host (below);
  - `connect-src` to nothing, or to the codebase's backstage relay (§4.6);
  - `img-src` to `data:` and `blob:`;
  - `form-action 'none'`.
- **Everything else crosses by `postMessage` to the broker**
  (`workspace-ecosystem.md` §4.4). There is one broker for workspace bundles
  and artifacts, with one audit log.
- **Packages for frame apps come from one pinned CDN host.** Friday rewrites
  imports to exact versions, which is FA12 in URL form. Examples are React,
  a chart library, or a small CSS framework.
  - The 24-hour cooldown applies to the version chosen.
  - The CDN host is the one network hole a frame has. It is named on the
    codebase's policy (§4.5) like any other endpoint.
- **Build tools run in the frame too.** esbuild-wasm (MIT) can bundle JSX in
  the browser, so a React app can be built with nothing installed on the
  host. **INFERRED** from its documented browser build. Spike S1 confirms it
  on the owner's machine.

The frame is the default box for every codebase whose template does not need
a server. Friday picks the tier from the template and says which one she
picked.

### 4.4 The box tiers

| Tier | What | Enforced by | Reads your files? | Network fenced? | Needs |
|---|---|---|---|---|---|
| **B0: frame** | the app runs in the sandboxed iframe | the browser | no | yes, by CSP | nothing |
| **B1: host** | build tools and dev servers as Low-integrity processes in a Job Object, with a scrubbed environment and a Low-labelled folder | Windows (writes only) | **yes** | **no** (proxy variables only) | nothing |
| **B2: VM** | a container in WSL 2 (Podman inside the distro first; Docker Desktop only if the user already has it, since it phones home with no switch below Business, S3), or Windows Sandbox, or a WHP microVM (microsandbox, Apache-2.0) | the hypervisor | no, only the codebase folder is mapped | yes, the only route out is Friday's proxy | one of: WSL 2, Windows Sandbox, WHP. **Friday never turns a Windows feature on** |
| **B2-OS: OpenShell** | OpenShell on WSL 2 + Docker, fed the salon policy | OpenShell plus the hypervisor | no | yes | past the §2.2 gate |

**How Friday picks a tier** (an engineering call):

1. **B0** when the template needs no server. This is the default for new
   codebases, artifacts and workspace bundles.
2. **B2** when the codebase needs processes and a VM backend is present. The
   first time, Friday says which one, and offers to switch.
3. **B1** when the codebase needs processes and no VM backend is present.
   - **Proceed, disclose, offer.** Friday runs it and says, once per
     codebase and in plain words: "This runs on your PC without a wall
     around your files or the network. Code here could read your documents.
     Installing WSL would give it a real wall. Want the steps?"
   - Package install scripts are off in B1 (`--ignore-scripts`, and
     `pip install --only-binary :all:` where that works).
   - B1 never receives the private data of §6.4. That data goes to B2 or
     stays out.
   - This is not a restriction on the user. B1 simply cannot make the
     promise, so it does not claim to.

**B1 is `code_sandbox.py` generalised, not rewritten.** Its Low-token and
Job Object code is reused. The Job Object's one-process limit becomes a
per-codebase process cap, because `npm run dev` starts Node, which starts
esbuild. The classification stays OUTWARD in the gate's terms. With a
"trust this codebase" grant, the grant is what makes it quiet.

**AppContainer for B1: spike S2's answer (2026-09-30, VERIFIED on the
owner's machine, no admin).** Node, npm and a dev server all run inside an
AppContainer created per user; `npm install` works with the `internetClient`
capability; and "does not read your files" is real: every read and write
under the profile fails with EPERM, the drive root is unreadable, and there
is no network at all, not even DNS, without a capability. **The blocker is
the one this paragraph feared: the panel cannot reach a server inside the
box.** A server binds loopback fine, but every host-side connection (raw
socket, `localhost`, `[::1]`, the LAN address, a real headless Chromium) is
silently dropped in every capability combination; the exemption is
admin-only. What does cross without admin: inherited handles, and named
pipes in both directions (a host pipe with an "ALL APPLICATION PACKAGES" or
package ACE and a Low mandatory label; or a container pipe under `LOCAL\`
reached by the host at `\\.\pipe\Sessions\<n>\AppContainerNamedObjects\<SID>\<name>`).
So **B1 becomes an AppContainer only with a bridge: a tiny host-side proxy
on `127.0.0.1:<port>` for the panel, relaying over a pipe to a relay inside
the container, which reaches the dev server on its own loopback.** Three
conditions attach: the environment block must contain `LOCALAPPDATA` or
process creation fails (error 203); Node's real-path walk dies on
`lstat('C:\\')` unless run with `--preserve-symlinks --preserve-symlinks-main`
or from a `subst` drive; and Node must be a user-owned copy, because the
Program Files install lacks the "ALL APPLICATION PACKAGES" ACE. Untested: a
real Vite or Next dev server's own file watching under these conditions.
With the bridge, B1 gains "no, doesn't read your files" and the Low token
becomes the fallback for the copy of Friday (§7), which needs the owner's
Python environment. The pipe bridge is Phase 4 work and is sized there.

**The copy of Friday (§7) always runs in B1 on a dev checkout.** It has to,
because it needs the owner's Python environment. It is contained by a fixture
home instead (§7.2).

### 4.5 Network, installs and the policy file

**One proxy.** Every box tier's outbound traffic goes through a Friday-run
HTTP(S) proxy, the **salon proxy**. The one exception is B1, where it is
advisory. The salon proxy is a small, separate module, not part of
`egress_gate.py`: the box's traffic is the app's own traffic, not Friday's
context. It is where:

- policy is enforced, per codebase, per tool, per host, method and path;
- credentials are injected (§4.7);
- every request is logged per codebase, to the same audit log the broker
  uses;
- **an approval card becomes a policy edit.** Allow, trust and revoke change
  the live policy with no box restart.

**The policy file.** It lives at `~/.friday/codebases/<id>/policy.yaml`, is
written by Friday, and is shown in the Files tab.

```yaml
version: 1
network_policies:            # OpenShell's section name and shape
  package_registry:
    endpoints:
      - host: registry.npmjs.org
        port: 443
        protocol: rest
        access: read-only    # GET/HEAD only
  github_read:
    endpoints:
      - host: api.github.com
        port: 443
        protocol: rest
        rules:
          - allow: { method: GET, path: "/repos/**" }
x-friday:                    # dropped by the OpenShell translator
  codebase: rent-tracker
  subjects: { package_registry: [npm], github_read: [app] }
  grants: { github_read: "grant_7f3a…" }   # action_gate grant id, expiry lives there
  default_posture: announce  # decision 1: announce | ask
```

**What happens with no rule** depends on decision 1. Under the decided
`announce` posture:

| Request | Answers which question | Default |
|---|---|---|
| GET, HEAD or OPTIONS to any host | "does data leave?" Only what is in the URL. The proxy refuses URLs over 2 KB and query strings holding anything the sensitivity classifier flags | **go ahead and announce** (a line in the chat, "fetched api.weather.gov", and a receipt entry) |
| package install from npm, PyPI or crates.io | a GET, plus code that will run | **go ahead after the scan and cooldown, and announce** |
| POST, PUT, PATCH or DELETE to a host other than the backstage | "does it change anything outside?" | **ask**, as every outward action in Friday already does. The card becomes a rule |
| anything to Friday's own port | none | **refused**, always. The box never talks to Friday except through the broker |
| anything carrying a value from the box's private data (§6.4) | "does private data leave?" | **refused unless the owner approves that exact payload.** This reuses the `local_context_share` card |

Under `ask`, everything without a rule asks. That is the OpenShell default.

**The floor no grant can widen.** A deterministic check runs before any rule
is written. It is the salon's small "prover".

- **Going live and publishing are non-grantable by default** (north star
  §18: email, purchases, public publishing and credential changes stay
  non-grantable). "Trust this codebase" covers the box's own API writes. It
  never covers "go live", publishing a bundle, or sending mail as the owner.
  The owner can widen that explicitly for one codebase; Friday never
  proposes it.

- No rule may allow Friday's port.
- No rule may allow a host on the never-send list.
- No rule may inject a credential into a request to a host other than the
  one it is bound to.
- No grant may cover the loud approvals of §7.4.

**Installs.** An install is announced like any other network event. Before
it happens, Friday runs:

1. the exact pin, since a lockfile is always written;
2. the 24-hour cooldown (`workspace-ecosystem.md` §4.5), which offers the
   previous qualifying version;
3. the deterministic scan (the queued SkillScan-style checks: install
   scripts, obfuscation, network calls at import, typosquat distance);
4. a read-only reviewer model pass, **only when the scan flags something**.

A scan failure is not a restriction. It is the harm-floor equivalent for
code, like the existing egress floor: the package is not installed, and the
chat says why and offers an alternative. The user can override a
scan-flagged install explicitly. That records a signed override and cannot be
done by a grant.

**Owner rules apply.** "Ask me before any install in Rent Tracker" is an
owner rule (`owner-rules-and-anomaly-detection.md`). It can only add caution,
which is how a user "explicitly sets" a restriction.

### 4.6 The backstage: local stand-ins for the cloud

Each codebase gets a `.friday/backstage.json` that maps service names to
local addresses. The app reads its service URLs from configuration. The
templates generate code that does, and Friday's instructions for the
codebase say so.

| Service | Stand-in | Licence | Windows without Docker |
|---|---|---|---|
| file storage (S3-shaped) | Friday's own small S3-subset shim (put, get, list, delete on a folder), or moto server when full S3 behaviour matters | own code / Apache-2.0 | yes / yes (pip) |
| database | SQLite. Postgres through PGlite in B0/B1 (licence **UNKNOWN**, checked in Phase 5) or a Postgres container in B2 | public domain / — | yes / B2 only |
| queue | a SQLite-backed queue in the shim | own code | yes |
| email | Mailpit. It captures every message, sends none, and shows them in a web UI the panel can embed | MIT | **UNKNOWN** (a Go binary; Windows builds not checked) |
| auth | a local login stub with fake users generated per codebase | own code | yes |
| key-value | the shim | own code | yes |
| AWS APIs beyond the shim | moto server | Apache-2.0 | yes |
| Azure storage | Azurite | MIT | yes (npm) |

**No component that phones home, ever.** Every backstage dependency (moto,
Mailpit, Azurite, and anything added later) is audited for telemetry,
analytics and update checks before it ships, and each is disabled or patched
out at build time, not by an opt-out flag. A stand-in Friday cannot silence
is not shipped. Phase 5 carries an **egress test**: the whole backstage runs
with outbound network blocked at the host, and the test fails if any
component tries to reach the internet (§9.1). The capture of §9.4 is the
second check, not the first.

#### 4.6.1 Friday's own local cloud emulator (the Phase 5 build)

The owner's direction is "emulate as much as we can, learn as much as we can,
build our own as much as we can." Spike S4 read LocalStack's archived
Apache-2.0 tree (`v4.14.0`, the last open release) to learn from it; the
study is in `docs/design/research/2026-09-30-localstack-archived-repo-study.md`.
Its verdicts, adopted here:

- **Licence.** The `v4.14.0` source is Apache-2.0 with a click-through EULA
  that yields to the licence on conflict. Code from that tag may be reused
  with attribution: keep the copyright lines, ship the full Apache-2.0 text,
  mark modified files. Nothing from the unified "LocalStack for AWS" image or
  anything imported as `localstack.pro.core` is ever copied. Friday sits on
  upstream moto, not the `moto-ext` fork.
- **Never imported, ever:** the analytics bus and everything under
  `localstack/utils/analytics/`, `aws/handlers/analytics.py`,
  `runtime/analytics.py`, every `services/*/analytics.py`, and the
  `/_localstack/info` machine-id exposure. Tracking there is on by default
  and reports to `analytics.localstack.cloud`. Friday has no opt-out flag
  because there is nothing to opt out of.
- **Patterns adopted:** one gateway port with a handler chain (request,
  response, exception, finalizer); service detection from the `Authorization`
  credential scope, then `X-Amz-Target`, then path and host; a typed request
  context; account and region derived from the fake access key and passed to
  moto; account-and-region-bundled stores for Friday's own shims; moto
  in-process behind a fall-through dispatcher (own handler first, moto next);
  one error contract with 501 for not-implemented; init stages
  (`boot.d/start.d/ready.d/shutdown.d`) with a status endpoint; a
  `/_backstage/` internal prefix on the same port; parity discipline (one
  compatibility marker per test, snapshot recordings with a last-validated
  date); a generated implementation-coverage table produced by firing every
  botocore operation at the gateway; and an `awslocal`-style thin client
  wrapper (`AWS_ENDPOINT_URL`, dummy keys, a default region).
- **Not adopted:** a session handshake with any remote before serving; a
  machine-id file; hard-coded vendor CORS origins; plugin entry points for a
  single-binary desktop app; a full typed AWS protocol parser unless moto's
  own protocol layer proves insufficient (UNKNOWN, checked first in Phase 5).

**What Phase 5 builds:** one gateway; moto underneath for S3, SQS, SNS,
DynamoDB, Secrets Manager and Lambda-style functions run as local
subprocesses where feasible; Mailpit for email; Azurite for Azure storage;
SQLite for databases; Friday's own shims for auth (a local OIDC stub),
key-value and queues; **a published coverage table** of which services and
API calls work; **parity tests**; **snapshots and restore** of the whole
backstage state per codebase; and **a Friday-branded status panel** in the
Backstage tab (what is running, on which address, its init status, the
coverage table, the last snapshot). Sized honestly in §10: 24–34 agent-days,
not the earlier 5.

**"Go live"** swaps one or more stand-in addresses for real endpoints and
real credentials. It is an outward card that names each service and each
credential. Going back to stand-ins is one click and needs no card.

### 4.7 Seats, keys and meters

**The codebase header** is one line, always visible in the panel. For
example:

> Rent Tracker · **Bonsai2 (this PC)** for small edits · **Opus 5.5** for
> big ones · **your Anthropic key** · this codebase: $1.84

It changes the instant any of those changes. That uses the
`seat_transparency.py` rule, "any change produces a visible line", applied
per codebase.

**Seats per codebase.** A codebase has a small routing record:

- `small_edit_seat`: default is the resident local brain.
- `heavy_seat`: default is none until the user picks one. It is offered on
  the first change the local seat struggles with.
- `engine`: `friday` (Friday's own agent loop) or `claude_agent` (Claude's
  agent tooling running *inside the box*).

A "small edit" is decided by Friday's router from the request and the diff
size. The route taken is shown on each step ("edited by Bonsai2").

**Whose key.** A codebase names a **key profile**:

- `mine`, which is `one_key` or the credential store's existing key; or
- a **guest key**: another party's key, stored in the credential store under
  `codebase:<id>`. It is usable only by that codebase's calls, and removable
  in one click. Removal deletes it and shows that it is deleted.

**The proxy injects the key.**

- The Claude agent in the box gets `ANTHROPIC_BASE_URL` pointed at the salon
  proxy and a dummy key. The proxy swaps the dummy for the real key header,
  only on requests to the provider's host.
- The key is never in the box's environment, files or logs. That is FA2
  made structural, not just promised.

**Metering.**

- `cost_calls` gains `key_profile` and `codebase` columns, added by
  migration as the cache columns were (`cost_meter.py:463-470`).
- Costs can then show "your key" and "Alex's key, Rent Tracker" separately.
- A guest key can carry the payer's own cap, which is a limit the user set.
  Friday adds none of her own.

**When no local model is resident** (a cloud-only laptop), the small-edit
seat is the heavy seat. The header says so. It does not pretend a local model
is working.

*As built (Phase 3, first increment):* the routing record lives on the
codebase (`seats.small_edit_seat`, default the resident brain;
`seats.heavy_seat`, none until chosen; `seats.engine`, `friday`) with a
`key_profile` of `mine` or a guest key's label. Small or big is a rule Friday
can explain: a message over 400 characters, the words that name a feature or
a rewrite, an approved plan with an open milestone, or a last step that
touched more than three files or 200 lines. The chat routes each turn by the
record, and when no local model is resident the heavy seat takes everything.
The header line is computed on the server from the record, the arbiter's
residency (no probes, cached 15 s) and the meter, with a spoken form; every
seat or key change is a system line in the chat and a `codebase_header` bus
event, and the panel re-reads the line after each step. `cost_calls` carries
`key_profile` and `codebase`; the chat's session context sets both, and
Costs splits by either. The agent loops set the current model before a tool
runs, so each step and receipt names the seat that made it. Tools
`codebase_seat`, `codebase_key` and `codebase_costs` work in chat and by
voice. Guest keys, the per-call key, the Costs view and the `claude_agent`
engine follow in the next increments.

### 4.8 Every change is a step

- A codebase is a git repository at `~/.friday/codebases/<id>/repo/`, or an
  existing folder the user points at. An existing folder gets a salon branch
  rather than commits on its current branch.
- **Every applied change is a commit**, with an author line naming the model
  and the key profile, and a one-line summary Friday writes for people.
- **Undo** is `git revert` of the last step, and it is itself a step. It
  walks backwards and does not oscillate. This is the defect
  `workspace-ecosystem.md` §3 found in `undo_last`, not repeated here.
- **Every step ends in a delivery receipt** (`goals-and-delivery-receipts.md`
  §5). The receipt holds:
  - files written, with hashes;
  - the commit sha;
  - tests run and their results;
  - the preview screenshot hash;
  - network events;
  - model, key and cost.

  Friday may not say "done" unless the receipt shows it.
- **Long sessions** rely on the context compression now on main
  (`services/compaction.py`). **Resuming after a crash** relies on the task
  ledger (`services/task_ledger.py`, `task_resume.py`). A codebase chat is a
  task-ledger run like any other.

### 4.9 Workspaces are made here

- **"+ Codebase → New workspace"** starts from the bundle template of
  `workspace-ecosystem.md` §4.2: `manifest.json`, one `index.html`, and an
  icon. It runs in B0.
- **Installing** puts the bundle through the Phase-2 host of that spec:
  installed means disabled, a broker controls access, and every call is
  audited.
- **"Improve this workspace"** is an item action on any workspace.
  - For a **bundle**, it opens the bundle's codebase.
  - For a **native** workspace (the React components in `index.html`), it
    opens a codebase on Friday's *own* source, which is §7. That is where
    native workspaces are improved, never in place.
- Today's `workspace_studio` restyling (CSS, accent, density, hidden, actions)
  keeps working. It is the light path, and the salon is the full one.
- **Seeded templates.** A "+ Codebase" template can be seeded from a plain
  description of an existing app's core features ("a Trello-like board for
  my projects"), with an import step for the old app's export file. This is
  the seam the onboarding spec's "Own your tools" uses: Friday notices,
  locally and with opt-in, which apps the user relies on and suggests open
  alternatives or "build your own in the salon." Nothing is built for it
  here beyond the seam; the Phase 8/9 sharing path stays so anything built
  can later go to the federation.

#### 4.9.1 Evolving workspaces: the order that makes it safe

The owner, verbatim: *"let's make sure the vibe evolution feature for all of
the different workspaces is functional as well. that should probably plug
into the vibe coding salon piece, right?"* It does. What exists today is the
narrow `/api/vibe-code` launcher and workspace versioning with no UI (§1.1,
§1.2). Workspace evolution becomes functional as early as it can safely
happen, in this order:

1. **The cross-site fix first.** Any script in Friday's origin has the whole
   API today (loopback trust, no CSP, §3.2). A vibe-coded workspace must
   never run with that power, so the fix is a prerequisite for every step
   below, and the program lead is prioritising it.
2. **Workspace-ecosystem Phase 0 and 1 pulled forward (W0/W1):** version
   history, a review-and-rollback UI for every workspace, and dock show and
   hide. These land right after salon Phase 1b.
3. **"Improve this workspace" right after salon Phase 2 (Phase 2b).** From
   any workspace, by button or by voice ("Friday, improve the News
   workspace"), a codebase chat opens on that workspace's own bundle with a
   live side-by-side preview in the sandboxed frame. Every change is a
   commit with tests and the self-testing loop of §4.11; then one approval
   to swap it in and one-click rollback. The brand check applies, and
   nothing may repaint the reserved status colours. This is
   workspace-scoped evolution, lighter than Phase 7's self-edit of Friday's
   core. Anything touching governance, the gate or the cLaws keeps the loud
   approval of §7.4. *As built (Phase 2b):* the button sits in every window's
   header and is a chat and voice tool; a bundle workspace's codebase chat
   opens seeded from the installed version, the panel compares the live and
   the improved page side by side, "Swap in" raises the one card after the
   manifest check, the brand check (a colour distance of at least 20 from the
   reserved status colours, not a string match) and a headless load, the
   card's approval installs a new version and rollback restores any earlier
   one in one click, all versions kept. A native workspace is refused with
   the typed blocker `needs_phase_7` and told, in plain words, that
   improving it means Friday's own source. Every one of the nineteen
   registry workspaces now declares its boundary as a set of components.
4. **Friday-proposed evolution.** Friday may notice friction in a workspace
   (repeated manual steps, ignored panels, things the owner asks for often)
   and *propose* an improvement as a diff with a preview and evidence, one
   at a time, through the weekly review, with no pestering. It never
   auto-applies: structural changes require owner approval (north star §6).
5. **Every workspace is evolvable.** All of the roughly seventeen workspaces
   get an explicit bundle boundary, taken from the UI session's workspace
   registry, so "improve this workspace" works everywhere and a native
   workspace's boundary is a declared set of components rather than a guess.

### 4.10 Sharing

The order is exactly that of `workspace-ecosystem.md` Phases 4–5, with the
owner's ratings model:

1. **Export and import a signed bundle as a file.** Installed means disabled,
   with a cooldown, and consent is asked again whenever capabilities widen.
2. **Federation sharing** comes once federation is switched back on and
   `_verify_peer_card` rejects on failure.
3. **Market cards** show positrons (retained installs and endorsements),
   negatrons (installer-measured and signed: bundle size, permissions, and
   brokered tokens per week) and flags (separate, signed, weighted). There is
   no blended score and no price.

The follow-up list in `avatar-visual-genome.md` Appendix A, which covers code
where ψ and η still mean the old things, applies unchanged. It is not
duplicated here.

#### 4.10.1 Publish to web (Phase 1b)

**Owner decision, 2026-09-29.** Any artifact kind (a page, chart, document,
client-side app, or a podcast episode page) can be published to a web
address, and **"This PC" is the default host**. The owner's correction that
shaped it, verbatim: *"I was more thinking like an artifact that Claude
serves to the public web, if the user so chooses... I don't want to launch
MCP servers off of my local hardware."*

- **The bundle.** The artifact is packed into a self-contained static bundle:
  it runs entirely in the visitor's browser, with no backend and no secrets;
  any data is baked in explicitly and shown in the card; it carries no
  analytics or tracking of any kind. An optional small "Made with Friday"
  mark follows the brand system.
- **The card.** Publishing is an outward action: **one** approval card
  showing the file list, a preview, the PII-scan result and the licence
  check, with a receipt. It is voice-first ("publish this"), read back under
  §6.3. Republish, versions and take-down are supported; republishing an
  unchanged bundle needs no new card, a changed one does.
- **Host adapters, pluggable, using the user's own accounts connected once:**
  1. **"This PC" (default).** A separate, minimal static file server
     process (Caddy `file_server`, already on the machine, or equivalent)
     that shares no process, port, cookies or origin with Friday's app
     server; its own `cloudflared` ingress hostname, distinct from Friday's,
     with **no proxy or reverse route from that hostname to Friday's app,
     API or websocket, ever**; it serves only a read-only `published/`
     directory holding bundles that went through the card, with no directory
     listing, no uploads, no server-side execution and no query-driven
     behaviour; strict headers (sandboxed pages, `nosniff`, a CSP that fits
     self-contained artifacts) and rate limiting at Cloudflare where
     available; an owner switch that takes all local hosting offline
     instantly; a status line saying whether pages are reachable right now
     (PC awake, tunnel up); no analytics and no access logs beyond what rate
     limiting needs, kept locally. The card warns about uptime (only while
     the PC is on) and bandwidth for large media such as podcast audio.
  2. **Cloudflare Pages** and **GitHub Pages** on the user's own account, for
     pages that must stay up around the clock.
  3. A slot for a **FutureSpeak-hosted** option, later.
  The card suggests a hosted adapter when a page is meant to be always-on or
  carries large media.
- **Adversarial tests the critic must run (§9.1):** every Friday API and UI
  path, the websocket, and every path-traversal attempt through the pages
  hostname must fail (404 or refused); nothing outside `published/` is ever
  readable.
- **Not in 1b:** apps that need a backend. They belong to the later "go
  live" (§4.6) and federation work. Friday never hosts anything dynamic from
  the user's hardware, never registers domains and never sells hosting.
- **As built (2026-09-30).** The static server is Friday's own stdlib-only
  process (`services/published_server.py`), not Caddy: the Caddy service on
  the owner's machine fronts `agent.friday` and is left alone, and a
  hand-written server is the one whose every refusal is a test. The tunnel is
  a cloudflared *quick tunnel* given exactly one URL, the static server's
  loopback port, so its `*.trycloudflare.com` hostname is distinct from
  anything Friday uses and has no route to the app. A setting
  (`publish_this_pc_tunnel`) keeps pages on loopback only. The chart page
  carries the panel's own renderer inlined (`static/friday_chart.js`), so a
  published chart draws itself in the visitor's browser with no library and
  no network. Nothing is spawned while a pytest test runs: the first version
  of the hosting manager did spawn a server and a tunnel from each test
  worker when a unit test approved a card, exposing temporary folders with
  no approval; those processes were stopped, the guard reads pytest's own
  marker, and a test pins it (§9.1).

#### 4.10.2 The tool manifest: a seam for the parked sharing sprint

Every tool made in the salon (especially a tool for Friday herself, §4.11)
carries a standard manifest from day one, so the parked "federation publish"
sprint has something to publish. Nothing is shared now; this is the hook.

- **Fields:** the MCP-compatible `name`, `description` and JSON `schema`; the
  gate risk class; the data scopes it touches, with the vault **never**
  included by default; the network egress it needs; who pays for model or
  API calls (the caller's key or an owner cap); whether it runs in the box;
  its receipt shape; and `share_tier`.
- **`share_tier`:** `none` (the default), `bundle` (others install and run it
  on their own Friday, through the signed-bundle path above), or `web` (a
  static publication through §4.10.1). **The earlier "trusted" and "public"
  hosting tiers are removed** by the owner's ruling: Friday never serves
  tools or pages to the internet from the user's hardware, and the salon's
  B1 and B2 boxes are not internet-facing isolation boundaries. Exposure
  defaults to off, per tool; there is no telemetry, and receipts are kept
  locally.

### 4.11 What the competitor pass added

`docs/design/research/2026-09-30-salon-competitor-gap-matrix.md` sets the
spec against Replit Agent, Lovable, Bolt, v0, Cursor and Codespaces. The salon
is already ahead on voice, proxy-injected secrets, privacy and telemetry,
guest keys and the local backstage. The real gaps, folded in here with the
recommended engineering option taken as a delegated decision (§12), are:

1. **A self-testing loop for the user's app (Phase 4).** Each step runs the
   preview in the box tier (broker capture in B0, headless Chromium in B1 and
   B2), captures console and network errors, looks at the screenshot, fixes,
   and reports done only with that evidence on the receipt. A failed smoke
   path reports `run_failed`. The harsh-critic gauntlet is offered as a user
   feature: the critic's verdict is attached to the step, never a veto.
2. **Point-and-say visual editing (Phase 2).** Select an element in the
   preview and say or type the change. Selection returns a selector plus a
   cropped screenshot; simple property edits go through a non-model patcher,
   the rest is a normal edit turn. *As built:* the picker runs inside the
   sandboxed preview frame as an inline script (the frame's CSP allows no
   network); one click posts one message to the panel, `{selector, tag, text,
   snippet, rect}`, and nothing else crosses; the panel accepts it only from
   its own frame's window. The selector is `#id` when unique, else a
   structural path (`main > section > p.lead`) that resolves to that element
   alone. The pick is stored on the codebase and told to the model in its
   context block ("the user POINTED AT ..."), so "make this bigger" has a
   referent; it stays until cleared or replaced. The quick actions (bigger,
   smaller, bolder, center, hide) append one rule to `styles.css` (or create
   and link `friday-pick.css`) as a step by "you", undoable like any other.
   The patcher accepts only a fixed set of plain properties and plain values:
   no `url(`, no `expression(`, no `;` or `}`. The cropped screenshot waits
   for the frame broker; the rect is already in the message.
3. **Bring your own code (Phase 4).** Open any existing repo or local folder
   (§4.8 already gives it a salon branch); push and open a PR through one
   card kind, `code_publish`, non-grantable by default, with the receipt's
   screenshot in the PR body.
4. **Plan-first mode for big asks (Phase 2).** A short plan as a `markdown`
   artifact, approval, then build; its milestones become task-ledger tasks
   and every step closes with a typed blocker, wired to goals. *As built:*
   `plan_first` makes the plan a `markdown` artifact with `meta.plan`
   (twelve milestones at most); the panel shows a plan strip whose "Build
   this plan" is the approval (`POST .../plan/approve`), which opens a
   task-ledger run and appends a system line to the chat. Until then the
   model's context block says AWAITING and "Do not build". `plan_milestone`
   moves one milestone through todo, doing, done or blocked with one of the
   goals spec's typed blockers, and the block names the next milestone.
5. **Phone preview (Phase 5).** LAN-first behind a one-time 30-minute token
   with a QR code; a tunnel the user already runs is detected, never
   installed, and opening one is a card.
6. **Publish to a web address the user owns.** Static artifacts: §4.10.1
   (Phase 1b). Apps with a backend: Friday runs the user's own deploy path
   (folder, ssh, git push, their command), verifies the URL, and never hosts.
   A pre-publish scan of the app itself joins the card.
7. **Vibe-code a tool for Friday herself (after Phase 7).** Built and tested
   in the salon, then registered in the tool catalogue with a declared gate
   class that is checked and never lowered; voice-callable per the voice
   contract; callable by the Laya and Needle reflexes only when its class is
   internal; through the owner-rules and loud-approval path; no grant ever
   covers an outward self-made tool. It carries the §4.10.2 manifest.
8. **A data viewer for the app's database (Phase 2):** the `table` artifact
   bound to a query, with confirmation on destructive SQL.
9. **Licence checks on every installed package (Phase 5):** announced on each
   install and flagged for copyleft, unlicensed and non-commercial terms;
   refusal is an owner rule, not a default (the breeze TTS lesson).
10. **One-click export as a plain project (Phase 2):** strips `.friday/` and
    the key binding, writes a plain README, and is INTERNAL; no lock-in, per
    the north star's portability rules.

Undo stays code-only, as every competitor's does; the Changes tab says so on
any step that ran a migration. Three items are product intent and are
flagged for the owner in §12.

---

## 5. UI, using existing elements first

| Surface | Change | Existing element |
|---|---|---|
| Chat sidebar | "+ Codebase" after "+ Project" | `ChatSidebar` `:42863` |
| Chat | the right-hand panel | `ChatSurface`, `FWin` |
| Panel tabs (codebase) | Preview · Files · Changes · Backstage | `DevFiles`, `DevDiff`, `DevGit`, moved in |
| Code workspace | Repos, Git and Procs stay. "Vibe" becomes "open in salon" | `CODE_TABS` |
| Header line | model · key · cost | the `seat_transparency` system line style |
| Cards | install, network and go-live cards, each with a spoken form (§6.3) | the approval card popup, drawer and System list |
| Settings → Salon | default posture, default seats, guest keys, box backend | the Settings `TABS` array **and** its matching branch (the documented two-place edit) |
| Costs | split by key profile and codebase | the Costs view |

**What the Preview tab adds:**

- a reload button;
- a width switcher (phone, tablet, desktop);
- an "open in its own tab" button (maximized tabs);
- a **"describe it"** button, which is the same action as the spoken "what
  does it look like?" (§6.5).

---

## 6. Voice-first

The owner's rule is **"no restrictions unless the user explicitly sets
them."** Everything in the salon works by voice. Where a screen genuinely
helps, Friday says so and still tries.

This section **uses** two pieces being published by the voice work and
designs neither:

- **The voice contract.** How a spoken request becomes an action, how
  progress and results are spoken, and how a spoken decision on a card is
  recognised.
- **The private-summary handoff.** How the local model summarizes private
  data without PII before a cloud model receives anything.

Where this section needs something from them, it names the need (§6.6). It
does not fill the gap itself.

### 6.1 Which codebase you mean

A spoken salon command applies to the **codebase in focus**:

1. the one named ("in Rent Tracker, …");
2. otherwise, the codebase chat that was last active, on screen or by voice;
3. otherwise, Friday asks "Which one: Rent Tracker or the FOIA log?" She
   never guesses between two.

Friday says the codebase name back at the start of her first reply in a
session ("In Rent Tracker: …"), so a wrong target is caught in one sentence.

### 6.2 Vibe coding by voice

The voice model does not edit code. It hands the request to the salon
through `delegate_to_friday`, with the codebase in scope, exactly as voice
hands any substantial request to Friday today. Short results are spoken back
under the voice contract.

| You say | What happens | Card? |
|---|---|---|
| "Make the header bigger." | an edit turn on the codebase in focus → a step → the preview reloads → "Done. The header's 40% bigger. Want it bolder too?" | no (inside the box) |
| "Undo that." / "Go back two." | revert the last step or two → "Back to before the header change." | no |
| "Ship it to the preview." / "Show me." | rebuild, reload, and open the panel on screen (`navigate_to`) | no |
| "What changed?" | the last step's one-line summary. "And before that?" walks back | no |
| "Read me the diff." | a spoken summary per file. "Line by line" reads it, with a note that the screen is easier (§6.5) | no |
| "What does it look like?" | a description of the preview (§6.5) | no, unless the describing model is cloud and the preview shows private data (§6.4) |
| "Add a login." / "Store this in a database." | an edit that uses the backstage (§4.6) | no |
| "Install a date picker." | the install flow, announced: "Installed react-day-picker 9.4.0; it's two months old and the scan was clean." | only under the `ask` posture |
| "Let it talk to the weather API." | a GET rule, announced | only under `ask` |
| "Post the results to my Slack." | a POST to an outside host | **yes** (§6.3) |
| "Go live." / "Publish it." | outward | **yes** |
| "Trust this codebase." | a scoped grant, read back first: "Rent Tracker can then post to any host without asking, until you say stop. Sure?" | it *is* the card |
| "Use Opus for this one." / "Use Alex's key." | the seat or key profile changes and the header line changes, spoken: "Switching to Opus 5.5 on Alex's key." | no. It is the user's choice, disclosed |
| "How much has this cost?" | the codebase total, split by key | no |

**Progress is spoken as short lines**, under the voice contract's pacing,
not as a stream of narration: "Editing two files." then "Tests pass."

A long change (more than about 20 seconds) runs in the background, as
`delegate_to_friday` already does. The outcome is handed back when it is
done, whether or not the conversation has moved on.

### 6.3 Cards read aloud, and answered by voice

**Every salon card has a spoken form.** It is at most two sentences: *what,
where, why*, and *how to answer*.

- **Install (only under `ask`, or when scan-flagged):** "Install
  left-pad-plus 1.0.2 into Rent Tracker? The scan flagged an install script
  that downloads a file. Say 'Friday, install it anyway' or 'no'."
- **Network write:** "Rent Tracker wants to POST to hooks.slack.com, path
  /services/… — the results table. Say 'Friday, allow it once', 'always for
  this codebase', or 'no'."
- **Go live:** "Switch Rent Tracker's email from the practice mailbox to
  your real SendGrid account? Real emails would go to real people. Say
  'Friday, go live' or 'not yet'."
- **Private-data share:** this is the existing `local_context_share` card,
  spoken as it already is.

**Answering.** The recognition rules are the ones that already govern share
cards (`local_context.py:33-38`), carried into the voice contract for every
card kind:

- **Only the owner's own latest transcribed words count.** The cloud model
  cannot approve its own request.
- **In "Several people" mode, the answer must name Friday** ("Friday, allow
  it"), because voices are not yet told apart.
- **"Once", "always for this codebase" and "no" map to** a one-use grant, a
  `codebase:<id>` grant, and a decline.
- **The decision is executed once, by the existing approval executor, and
  the card disappears from every tab.**

A card raised during a voice session also appears on screen, as every card
does. Answering in either place settles it.

### 6.4 Private data: summarized locally before any cloud model sees it

The owner's rule: *"Voice mode should be able to fire workflows that involve
the local model too, in the event that private info needs to be summarized
without any PII."* In the salon it applies to every surface, not only voice.

**What counts as private.** In a codebase, private means:

- anything imported from the vault, the wiki, mail, calendar or contacts;
- any file the sensitivity classifier (`services/sensitivity_classifier.py`)
  marks above the cloud tier;
- any file the user marks private in the Files tab.

These files are flagged in the codebase's `.friday/private.json`, and the
flag travels with the file through renames.

**What the cloud model gets instead:**

1. **The shape, from the local model**, through the private-summary handoff.
   That is the file's structure (columns and types, or headings), counts, and
   a description of what the data is *for*, with every person and identifier
   replaced by the placeholder rules in `local_context.py`.
2. **Synthetic rows**, generated locally to match the shape, containing no
   real values. The cloud model builds and tests the app against these.
3. **The payload card first**, unless a conversation-scoped grant covers it.
   It shows the exact summary and the synthetic sample that will be sent,
   and both model names (`local_context_share`, unchanged).

**The real data is used only where the app runs.** That means in a box on
this machine, in B0 or B2, never B1 (§4.4). It is also never sent anywhere
by the box without the exact-payload approval of §4.5. A cloud model that
asks "show me a real row" gets the card, never the row.

**By voice, this is a workflow Friday fires herself.** "Build me a tracker
from my sources spreadsheet" works like this:

1. The local model reads the spreadsheet.
2. It writes the summary and the synthetic rows.
3. Friday speaks the card: "I'll tell Opus the sheet has 212 rows with name,
   outlet, beat, last-contact date and a notes column. No names or notes go.
   Send that?"
4. On "Friday, send it", the cloud model builds against the synthetic rows.
5. The preview then loads the real sheet locally.

This path already exists for voice context (`ask_local_for_context`). The
salon adds a *file-shaped* request to it, and that is the private-summary
handoff's job.

**When no local model is available**, Friday says so and offers three
choices:

- build with the cloud model from a shape the user describes aloud;
- wait for the local seat;
- use a cloud model on the raw data, with the exact-payload card, which is
  the user's explicit call.

She does not quietly send raw data, and she does not refuse.

**Local-only mode** (`model_routing.mode == local_only`) means every salon
model call is local. Friday says what that costs in speed.

### 6.5 What a screen is better for, and what Friday does instead

| Task | Why a screen helps | By voice, Friday… |
|---|---|---|
| judging how the preview looks | taste is visual | describes it. With a local vision model resident, the description is local. With a cloud one, it is sent a screenshot only when the preview holds no private data, or with the payload card when it does. She offers "open it on screen" |
| comparing two versions | side by side is instant | describes the difference in one sentence, then offers "put both on screen" |
| reading a long diff line by line | eyes are faster than ears | summarizes per file, and reads line by line on request |
| entering a key or password | **keys must never be spoken**. Transcripts and the room would hear them | opens the key form on screen and says so. By voice, "use Alex's key" selects a key already stored |
| fixing a layout by pointing | "that button" is ambiguous | asks which one, naming candidates from the DOM: "the blue 'Save' at the bottom, or 'Save draft' at the top?" |

**Nothing is voice-disabled.** The key-entry row is the only one where Friday
will not do the thing by voice, and that is a secret-handling rule, not a
voice restriction. It already governs chat, where Friday never asks for a key
in the transcript.

These entries appear in `voice_restrictions()` so the Voice settings tab
lists them honestly: "Entering keys needs the screen, because a spoken key
would be in the transcript."

### 6.6 What this section needs from the voice contract and the handoff

Named so that the parallel work can confirm or refuse them. They are not
designed here.

1. **A spoken decision on any card kind,** with `answer_share_request`'s
   rules (own latest words; name Friday in room mode). Today only share
   cards can be answered by voice (§1.5).
2. **A file-shaped private-summary request.** The input is a file or table
   reference plus a purpose. The output is a shape summary plus synthetic
   rows. It uses the same placeholder rules and the same payload card.
3. **A "focus" slot** that the salon can set and the voice model can read,
   holding the codebase in focus (§6.1).
4. **Progress pacing** for long background work: how often, and how short.

If the voice contract lands without item 1, Phase 6 of this spec carries a
thin `answer_card` tool that uses `answer_share_request`'s rule verbatim. It
is retired when the contract supersedes it.

---

## 7. Friday edits a copy of herself

This is `grow-button.md` Lane B2, specified further for the salon. The key
sentence carries over: **"You cannot sandbox the artifact. You can only
sandbox the authoring loop and gate the join."**

### 7.1 Where it runs

- **Only on a machine with a git checkout of Friday.** A payload install
  refuses with a plain explanation, as `grow-button.md` §2.6 requires.
- **"Improve Friday" opens a codebase on a worktree** of the running
  checkout, on a branch named `salon/<slug>`. Every change is a step on that
  branch.

### 7.2 The copy runs beside the original

- The worktree starts **a second Friday server on another port.** It has
  two protections:
  - **`FRIDAY_HOME` points at a fixture home** made from the test fixtures,
    never the real `~/.friday`. That also stops the copy from running the
    wiki merge against the real home at import.
  - **An environment built from nothing** (FA2), with no provider keys.
- **The copy's model calls go through the salon proxy with the codebase's
  key profile**, like any codebase.
- **The copy's UI loads in the panel's Preview.** "Side by side" puts the
  running Friday and the copy next to each other in the panel, at the same
  width.
- **The copy has no GPU seat of its own.** Its local-model calls go to the
  running Friday's seat through the proxy's broker path. They are metered
  like any other call and never load a second model: a second copy of
  the brain on a 12 GB card would starve the first.

### 7.3 Tests, then the owner's yes, then the swap

1. **The copy must pass** the required checks in `AGENTS.md`, plus any test
   the change adds. The added test must be shown to fail on the original,
   which is the red-first gate of `grow-button.md` §6.2. Results go on the
   receipt.
2. **The owner approves the swap** on a card, in chat or by voice. The card
   shows:
   - the summary;
   - the diff stats;
   - the tests;
   - the before and after screenshots, which Friday has looked at (§9.3);
   - the rollback.
3. **The swap is done by `apply_growth`**, which is not model-authored
   (`grow-button.md` §7.5):
   - it merges the branch into the running checkout's branch;
   - it checks the hashes of every untouchable file before and after;
   - it restarts the server by the existing restart path.
4. **The swap is health-checked, and a failed check rolls back by itself**
   (north-star amendment A7). After the restart, the running Friday must
   pass the boot health check and a short smoke run: chat, one tool call,
   and the approval card path. If it doesn't, `apply_growth` reverts and
   restarts without being asked, then says what failed.
5. **Rollback is one click, or one sentence ("Friday, roll that back").**
   It runs `git revert` on the merge and restarts. Additive data leaves
   orphans, which are reported. Transformative data needs the declared-store
   snapshot of `grow-button.md` §8.2, and a change that declares one says so
   on the swap card.

### 7.4 The loud approval

**What it covers.** A change to any of:

- `governance/**`: the action gate, the cLaws text and pin, Proof of
  Integrity;
- `services/egress_gate.py`, `sensitivity_classifier.py`,
  `credential_store.py`, `vault_passphrase.py` and `privacy/**`;
- auth and session handling in `core/__init__.py`;
- the salon proxy, the policy floor (§4.5) and `apply_growth` itself;
- everything in `grow-button.md` §7.5's untouchable set.

**What makes it loud** (decision 2):

- **A separate card, never merged with the ordinary swap card**, listing
  each protected file and a plain-language line on what changes in it.
- **No grant, rule or "trust this codebase" can answer it.** The policy
  floor enforces that (§4.5).
- **A spoken or typed challenge.** Friday picks a fresh random word and
  says or shows it: "To change the checkpoint, say 'Friday, change the
  checkpoint, amber'." A replayed recording or a TV in the room cannot
  produce a word that did not exist a moment ago. It works by voice, so it
  is not a screen restriction.
- **A visible, audible notice after the swap**, and an entry in the signed
  receipts that says a protected file changed.
- **cLaws edits re-pin** through the existing `repin_claws` flow, after
  approval, never before.

**The `boot_guard` gap in §1.4 is closed as part of Phase 7.** `governance/`,
`egress_gate.py` and `proof_of_integrity.py` join the protected list, so that
`/api/code/apply` refuses them too.

The rejected alternative, kept for reference: refuse these paths in the
salon and hand the owner a patch file to merge by hand.

---

## 8. How it fits the existing defences

- **The egress gate** still seals every cloud model call
  (`seal_outbound`), including calls from the salon's engines. The salon
  proxy governs *the box's* traffic, and the egress gate governs *Friday's*.
  Neither replaces the other.
- **Taint.** Content fetched by the box is untrusted input. When it reaches
  a model's context through a tool result, the taint rules apply as they do
  to web pages.
- **Owner rules** only add caution. Anomaly detection sees salon outward
  actions in the same outward-action log. A burst of POSTs from one codebase
  is the "velocity" shape it already watches for.
- **Goals.** `/goal the tracker imports my CSV and shows a chart` works in a
  codebase chat. The evaluator reads the step receipts and the preview
  screenshot hash.
- **No built-in caps.** The salon adds none. It stops for loops, lack of
  progress, Stop, and limits the user sets, exactly like every other
  surface.
- **No telemetry, ever.** Nothing the salon runs may phone home: stand-ins,
  a box backend, the coding agent. Each one's opt-out is set and then
  **verified by capture** in its phase (§9). For Claude's coding agent, that
  means its non-essential traffic settings are off and the proxy refuses any
  host but the provider's API. **UNKNOWN** until the Phase 3 capture: which
  hosts it contacts beyond the API.

---

## 9. Verification plan

### 9.1 Fail-first tests (each must fail on `780e31fa`, or on the phase's base, before its change)

- **Frame isolation:**
  - an `html` artifact tries `fetch('/api/settings')`, `parent.document`,
    `document.cookie` and `localStorage`, and each must fail;
  - its CSP must block a `<script src>` from a non-allowed host.
- **Broker vocabulary:** asking for the vault, credentials, `run_command` or
  mail is refused as *unknown*, not as *denied*.
- **B2 fence:**
  - from inside the box, reading a path under `~/.friday` fails;
  - a request to a host with no rule under `ask` fails;
  - a request to Friday's port always fails;
  - `env` contains no provider key.
- **B1 honesty:** the first B1 run of a codebase produces the disclosure
  line, and the B1 classification is OUTWARD without a grant.
- **Key injection:** the box's request to the provider reaches the proxy
  with the dummy key and leaves with the real one. A request to any other
  host with the dummy key leaves with the dummy.
- **Policy floor:** writing a rule for Friday's port, a never-send host or a
  loud-approval path raises an error. A grant cannot answer a loud card.
- **Cooldown and pins:** an install of a version under 24 hours old gets the
  previous version, and the lockfile has exact versions.
- **Private data:**
  - with a private file in the codebase, the cloud engine's request (captured
    at `seal_outbound`) contains the summary and the synthetic rows, and none
    of the real values (asserted by string search for every real cell);
  - with the card declined, nothing is sent.
- **Voice:**
  - a cloud-model-generated "yes" does not approve a card;
  - in room mode, "allow it" without "Friday" does not approve;
  - "undo that" reverts exactly one step;
  - "use Alex's key" changes the header line and the next `cost_calls` row.
- **Metering:** a call under a guest key writes `key_profile` and
  `codebase`, and Costs splits the two.
- **Self-edit:**
  - the copy's `FRIDAY_HOME` is not the real home;
  - the copy cannot read a real-home marker file;
  - `apply_growth` refuses a diff touching an untouchable file without the
    loud approval;
  - rollback restores the pre-swap tree hash.
- **Cross-site prerequisite:** a POST from loopback carrying a foreign
  `Origin` or `Sec-Fetch-Site: cross-site` is refused. This comes from the
  separate fix, and Phase 4 is gated on it.
- **Backstage egress (Phase 5):** the whole backstage runs with outbound
  network blocked at the host; any component that tries to reach the
  internet fails the test.
- **Publish to web (Phase 1b, built):** through the pages hostname, every
  Friday API and UI path, the websocket and every path-traversal attempt
  returns 404 or is refused; nothing outside `published/` is readable; a
  bundle with a tracking script or a secret-shaped string is refused before
  the card; the owner's kill switch makes every published page unreachable
  within a second; a declined card publishes nothing; a forged card with no
  staged bundle publishes nothing; **under a test run the hosting manager
  spawns nothing** unless the test has stubbed every process and says so.
- **Workspace evolution (Phase 2b, built):** an improved bundle runs only in
  the frame; the swap needs one approval; rollback restores the previous
  bundle hash; a change touching a reserved status colour fails the brand
  check; a page that throws at load is a `run_failed` blocker and no card;
  a declined swap installs nothing; a native workspace is refused with
  `needs_phase_7`. Tests: `tests/unit/test_workspace_bundles.py`,
  `test_codebase_smoke.py`, `test_improve_workspace_tool.py`,
  `test_workspace_bundles_ui_files.py`, the boundary assertion in
  `test_workspace_registry_ui.py`, `tests/api/test_workspace_bundle_routes.py`,
  `test_index_carries_installed_bundles.py`.
- **The panel (Phase 1, built):** a second put is a new version and the
  first is kept; restore is a new version; a hand edit is authored by "you"
  and the next turn's prompt carries its diff once; off the record nothing is
  written and the artifact is gone when off-record ends; the shipped frame
  blocks every probe above.

### 9.2 Screenshots, actually looked at

Every UI phase ships screenshots, and **each one is opened and looked at by
whoever claims the phase done.** The PR description then carries one
sentence per screenshot saying what it shows. That sentence is the evidence
the image was read, not just produced. A screenshot nobody described does
not count.

- **Taken against a page served for the check, never by driving the live
  server's mutating routes:**
  - `127.0.0.1`, not `localhost`;
  - stubbed non-GET requests;
  - mocked WebSockets.
- **Widths:** 1440, 1024 and 390 px.
- **States:** docked chat, chat window, and maximized tab.
- **The set, per phase:**
  - **Phase 1:**
    - a table artifact;
    - a chart artifact;
    - a markdown draft mid-edit;
    - an `html` app;
    - the version timeline;
    - the panel collapsed;
    - the panel at 390 px as a tab over the chat.
  - **Phase 1b:**
    - the publish card with the file list, preview, PII scan and licence
      check;
    - the published page in a plain browser, with the "Made with Friday"
      mark;
    - the "This PC" status line, reachable and unreachable;
    - the kill switch.
  - **Phase 2:**
    - "+ Codebase" in the sidebar;
    - a new codebase's first preview;
    - Files;
    - Changes with one-line summaries;
    - after "undo".
  - **Phase 2b (taken and looked at, 2026-09-30):**
    - "Improve this workspace" from a workspace's menu, and a native
      workspace's plain refusal;
    - the workspace and its improved copy side by side;
    - the swap card;
    - the installed versions, and after rollback.
  - **Phase 3:**
    - the header line on the local seat;
    - the header line on the cloud seat;
    - the header line on a guest key;
    - Costs split by key.
  - **Phase 4:**
    - an install announcement line;
    - an `ask`-posture install card;
    - a network-write card;
    - the B1 disclosure line.
  - **Phase 5:**
    - the Backstage tab;
    - Mailpit's captured mail in the panel;
    - the go-live card.
  - **Phase 6:**
    - a voice-originated card on screen alongside its spoken form (the
      transcript line);
    - the private-data payload card showing the summary and the synthetic
      rows.
  - **Phase 7:**
    - Friday and the copy side by side;
    - the swap card;
    - the loud card with its challenge word;
    - the post-swap notice.
- **What the looker checks:**
  - nothing clipped (dropdowns and menus inside the panel stay visible);
  - the panel's scroll is independent of the chat's;
  - the header line is readable at 390 px;
  - cards name the model and the key;
  - no real private value is visible in any cloud-bound payload shown.

### 9.3 Friday looks too

For the self-edit swap card, the before and after screenshots are taken by
Friday. They are described by the resident local vision model when there is
one, or by a cloud one with a card. **A swap card with screenshots that were
not described is not raised.** This is `grow-button.md` §7's vision check,
used here as evidence and not as a veto.

### 9.4 Captures

**Phases 3, 4, 5 and the S3 spike** each run a full session with a packet
capture on the host. The pass condition: every outbound connection is to a
host named on the codebase's policy, or to the provider API through the
proxy. Any other host fails the phase. That is how "no telemetry" is
checked, not asserted.

### 9.5 Live check after merge

For each phase, one real session on the owner's machine, by screen and by
voice, with the receipts and costs read back. Voice is checked in "Several
people" mode too.

---

## 10. Phased build plan

Effort is in focused agent-days with review. Each phase is shippable alone.

| Phase | What | Effort | Depends on |
|---|---|---|---|
| **S1–S3** | Spikes, which report and build nothing. **S1 (done, PASS):** esbuild-wasm plus pinned packages built a React app inside the opaque-origin frame on the owner's machine; every isolation probe was blocked; esm.sh is the one package host (unpkg serves raw CommonJS and fails); cold start about 25 s, warm about 2 s; the CSP needs `'wasm-unsafe-eval'` and `worker-src blob:`. **S2 (done):** Node, npm and a dev server run in an AppContainer without admin and cannot read the profile, but host-to-container loopback is dropped in every capability combination; a pipe bridge is the viable shape (§4.4). **S3 (done, 2026-09-30; research doc `2026-09-30-s3-openshell-wsl-spike.md`):** OpenShell 0.1.2 runs on the owner's WSL 2 + Docker Desktop after two fixes that are the experimental part: the Docker driver's supervisor callback must be Docker Desktop's host-gateway (`grpc_endpoint = "https://192.168.65.254:17670"`, gateway bound on all addresses) and the server certificate needs that IP as a SAN. Sandbox Ready in 3 s warm; the workload runs network-none and unprivileged; deny-by-default holds at connect(); `policy update --add-endpoint` applies live in 98 ms with no restart. Telemetry: off via `OPENSHELL_TELEMETRY_ENABLED=false`, propagated to supervisors, zero packets to the NVIDIA endpoint in a 40-minute capture on the distro side; the sandbox side is covered by a per-process connection log only (a Windows capture needs admin). Docker Desktop itself calls `api.docker.com`, `hub.docker.com`, `desktop.docker.com` and `sessions.bugsnag.com` at every start with analytics off, which has no switch outside Docker Business: a finding for every Docker Desktop tier. Not completed: the HTTP-level allow/refuse/revoke with a curl image (cut short by an external `wsl --shutdown`, twice); policy through the Python SDK is UNVERIFIED. Docker Desktop's VM costs about 2.1 GB of host RAM at idle. Nothing needed admin or a reboot. Windows Sandbox is absent; the WHP library is present; microsandbox's Windows path needs the WHP feature enabled, which needs admin to check | 3 | nothing |
| **S4** | **Research (done):** LocalStack's archived Apache-2.0 tree studied for the gateway, the provider model over moto, persistence and init hooks, coverage tracking, parity testing, licence reuse and the telemetry modules never to import. Verdicts in §4.6.1 and the research doc | 1 | nothing |
| **1** | **The artifact panel in every chat** (first increment built, on `feat/salon-phase1`): `artifact_put`, the fenced-block fallback, the store with off-record honoured, versions, hand edits as versions, the frame (§4.3), the six kinds, both HTML files, the Settings toggle. Left: the broker's read-only subset (moves to Phase 2 with the bundle broker), the live check after merge (§9.5) | 7–8 | S1 (for `html` apps; the other kinds don't wait) |
| **1b** | **Publish to web** (§4.10.1): the static bundle packer, the PII scan and licence check, the one card with its spoken form, the "This PC" adapter (separate static server, own tunnel hostname, read-only `published/`, no route to Friday, strict headers, kill switch, reachability status, adversarial tests), the Cloudflare Pages and GitHub Pages adapters on the user's account, the FutureSpeak slot, republish, versions, take-down, the "Made with Friday" mark | 6–8 | 1 |
| **W0/W1** | **Workspace-ecosystem Phase 0 and 1, pulled forward** (§4.9.1): version history, a review-and-rollback UI for every workspace, dock show and hide. Sized in that spec; listed here because the salon's order depends on it | 3–4 (that spec's budget) | 1b; the cross-site fix |
| **2** | **Codebase chat, B0:** "+ Codebase", git-backed steps, Preview, Files and Changes (moving in `DevFiles` and `DevDiff`), undo, templates (static app, React-in-frame, workspace bundle, seeded from a description with import), one-line summaries, step receipts, the frame broker's read-only subset. **Plus the cheap gaps of §4.11:** point-and-say editing, plan-first mode with typed blockers, the data viewer, one-click export | 6–7, plus 9–12 | 1; goals-and-receipts Phase 0 for the receipt classifier |
| **2b** | **"Improve this workspace"** (§4.9.1): from any workspace by button or voice, a codebase chat on its bundle, side-by-side preview in the frame, commits with tests and the self-testing loop, one approval to swap, one-click rollback, the brand check, explicit bundle boundaries for every workspace from the registry. **Friday-proposed evolution** (diffs with evidence through the weekly review, never auto-applied) follows once owner rules Phase 1 is on main | 5–7, plus 3–4 | 2; W0/W1; **the cross-site fix on main** |
| **3** | **Seats, keys and meters:** the routing record, key profiles, guest keys in the credential store, proxy key injection for provider calls, `cost_calls` columns, the header line, the Costs split, Claude's agent as an engine (it runs in B2, or in B1 with the disclosure; the codebase itself can still preview in B0) | 5–6 | 2 |
| **4** | **The box and the proxy:** B1 as an AppContainer with the pipe bridge (§4.4), with the generalised Low-token `code_sandbox` as the fallback for the copy of Friday, B2 on WSL 2 containers, the salon proxy, `policy.yaml`, the floor, the posture setting, install scan, cooldown and pins, the reviewer pass on flags, cards as policy edits, the audit log shared with the broker. **Plus:** the self-testing loop with the critic toggle, and bring-your-own-code with the `code_publish` card (§4.11) | 10–12, plus 2–3 for the bridge, plus 8–11 | 3; **the cross-site fix on main**; owner rules Phase 1 (so rules can see salon actions) |
| **5** | **The backstage: Friday's own local cloud emulator** (§4.6.1): the gateway with its handler chain and service detection, moto in-process behind the fall-through dispatcher (S3, SQS, SNS, DynamoDB, Secrets Manager, Lambda-style functions where feasible), Mailpit, Azurite, SQLite, the OIDC stub, key-value and queue shims, init stages, the internal endpoints, snapshots and restore, the parity harness and the published coverage table, the Friday-branded status panel, the telemetry audit of every dependency, the egress test, the go-live card, captures. **Plus:** phone preview with QR, licence checks on installs, the pre-publish scan and the user's own deploy path for apps with a backend (§4.11) | 24–34, plus 8–11 | 4 |
| **6** | **Voice-first:** focus, the verb table of §6.2, spoken card forms, the private-data file flow through the handoff, `voice_restrictions` entries, the thin `answer_card` only if the contract lacks it | 5–6 | 2 (verbs), 4 (cards); the voice contract and handoff on main |
| **7** | **Self-edit on a copy:** worktree, the second server with a fixture home, side by side, the red-first gate, `apply_growth`, the loud approval, the `boot_guard` list fix, rollback | 8–10 | 4, 6; goals-and-receipts (receipts); owner rules |
| **8** | **Workspaces and sharing:** the bundle host (`workspace-ecosystem.md` Phase 2) as the salon's install target, signed bundle export and import (`share_tier: bundle`), cooldown, re-consent on widening. "Improve this workspace" itself moved to 2b | 5–6 | 2b, 4; `workspace-ecosystem.md` Phase 1 fixes |
| **T** | **A tool for Friday herself** (§4.11 item 7): build and test in the salon, register in the tool catalogue with a declared gate class, the §4.10.2 manifest, voice-callable, reflex-callable when internal, through owner rules and the loud approval | 6–8 | 7; owner rules |
| **9** | **Market cards:** only once federation is switched back on and `_verify_peer_card` is fixed. Ratings per Appendix A | its own spec | federation |
| **B2-OS** | The OpenShell backend: a translator from `policy.yaml`, lifecycle via the SDK, grants driving live network policy | 4–5 | S3 passing its gate; 4 |

**Totals (2026-09-30 re-estimate).** The original Phases 1–8 were 55–62
agent-days. The changes above add: Phase 1b 6–8; W0/W1 3–4 (from the
workspace-ecosystem budget); Phase 2b 5–7 plus 3–4 for proposed evolution;
the B1 pipe bridge 2–3; the competitor gaps 31–42 spread over Phases 2, 4, 5
and T; and Phase 5 grows from 5 to 24–34. **Phases 1–8 plus 1b, 2b and T now
come to about 125–160 agent-days, roughly 25–32 agent-weeks.** The artifact
panel (Phase 1) still stands alone at about 1.5 weeks and is useful on day
one to every chat and every model.

**Where it is cheapest to stop:**

- **After Phase 1b** (about 3 weeks in), the owner can make things in any
  chat, keep every version, and publish any static artifact to a web address
  from this PC or a Pages account. No codebase, no box.
- **After Phase 2b** (about 9–10 weeks in), the owner has a working salon for
  everything that runs in a browser, with point-and-say, plan-first, a data
  viewer and export, and every workspace can be improved in it and rolled
  back. Still no box to maintain.
- **After Phase 4**, it runs anything, with the self-testing loop.
- **After Phase 5**, apps run against Friday's own local cloud, with no
  account and no telemetry anywhere.

### 10.1 Where it slots into the roadmap

The queue, as `avatar-visual-genome.md` §11.1 records it:

1. CLM research
2. goals and receipts
3. FridayWeaver-2
4. the salon
5. owner rules

The recommended order:

1. **S1–S3, S4 and Phase 1 first.** S1, S2, S3 and S4 are done; Phase 1's
   first increment is on its branch. They touch only the
   chat UI and a new store, need no GPU, and block nothing.
2. **Phase 1b (publish to web) right after Phase 1 lands**, then **W0/W1**
   (workspace version history, review, rollback, dock show and hide).
3. **The cross-site fix** (filed separately, prioritised by the program lead)
   lands before Phase 2b and Phase 4. It is small, and it protects every tab
   today; without it no vibe-coded workspace may run.
4. **Goals-and-receipts Phase 0 and owner-rules Phase 1 land before salon
   Phases 2 and 4 respectively.** The salon's steps want receipts from the
   start, and its outward actions want rules. Neither spec is delayed by the
   salon.
5. **Phase 2 with its cheap gaps, then Phase 2b, "Improve this workspace".**
   Friday-proposed evolution follows once owner rules are on main, because
   proposals ride the weekly review.
6. **Phases 3, 4 and 5** in that order; Phase 5 is now the largest single
   phase and is the "build our own" emulator.
7. **Phase 6 waits on the voice contract and handoff reaching main.** (The
   contract is on main as of `02035ba6`; the handoff is not.)
8. **Phase 7 comes after receipts and owner rules**, and preferably after
   FridayWeaver-2 lands. **T (a tool for Friday) comes after 7.**
9. **Avatar A0 (one day) is unaffected.** Nothing here competes with it.

---

## 11. Costs and failure modes

**Money** is **UNMEASURED**. Phase 3 measures a reference session: build a
small tracker app, twenty edits, local for small edits and Opus 5.5 for
three big ones. The header reports the result. This spec does not guess a
dollar figure.

**VRAM.**

- **B0 costs no VRAM.** It runs in the browser, and the preview uses the GPU
  only as a web page does.
- **B2 on WSL 2 takes host RAM, not VRAM.** Docker Desktop's VM reserves
  memory: **measured in S3** at about 2.1 GB of host RAM for Docker
  Desktop's VM at idle (1.15 → 3.29 GB at start, settling at 2.0–2.4 GB),
  plus 24 MB for an OpenShell supervisor and 10 MB for an idle workload.
  Docker's engine is reachable 11 s after a warm start and 116 s after a
  cold WSL start.
- **The local seat is the existing brain.** The salon never loads a second
  model.

**Failure modes:**

| Failure | What happens |
|---|---|
| no local seat resident | the header says so. Small edits go to the heavy seat if one is set; otherwise Friday asks which to use |
| WSL 2 stopped | Friday starts it only when a B2 codebase opens, says so, and shows the time it took |
| a B2 backend missing | B1 with disclosure (§4.4) |
| the proxy down | the box has no network, *and says so in the chat*. Nothing fails open |
| a guest key rejected | the header turns red and says whose key failed. Nothing falls back to the owner's key without asking |
| an install flagged | not installed, with the reason and an alternative, and an explicit override on offer |
| preview blank | the step is not reported as done, because the receipt includes the preview screenshot hash of a non-blank frame |
| the copy fails its tests | no swap card. The failure is spoken or shown with the failing test's name |
| OpenShell changes its policy format | only the translator changes. `policy.yaml` is Friday's |

---

## 12. Decisions

**Engineering calls made in this spec** (not the owner's):

- the artifact panel first, in every chat;
- the browser frame is the default box;
- the tier order B0 → B2 → B1, with disclosure;
- AppContainer is spiked, not assumed;
- the policy file uses OpenShell's shape, and Friday's proxy enforces it;
- OpenShell becomes a backend only past the gate in §2.2;
- MinIO is out, and moto, Mailpit and Azurite are in, each audited for
  anything that phones home (§4.6);
- charts are drawn in SVG by the panel; no chart library is added;
- esm.sh is the one package host for frame apps (S1);
- the page holds one event stream for every chat surface (§4.2);
- B1 is an AppContainer with a pipe bridge, and the Low token is the
  fallback for the copy of Friday (S2, §4.4);
- the backstage is Friday's own emulator on the S4 patterns, and nothing
  from LocalStack's analytics is ever imported (§4.6.1);
- the competitor-pass options of §4.11, each taken as recommended:
  verification per step as evidence not veto; pointer selection returning a
  selector plus a crop with a non-model patcher for simple edits; push and
  PR as one non-grantable `code_publish` card; a plan as a `markdown`
  artifact whose milestones become tasks; LAN-first phone preview with a
  detected, never installed, tunnel; Friday prepares and verifies publishes
  and never hosts or sells domains; a self-made tool runs in the box behind
  a broker stub with a declared gate class that is never lowered; the data
  viewer is the `table` artifact bound to a query; licence is announced and
  flagged, refusal is an owner rule; export strips `.friday/` and is
  INTERNAL; undo stays code-only;
- keys are injected at the proxy, including for Claude's agent;
- key and codebase columns go in `cost_calls`;
- git steps with "step" and "undo" in the UI;
- receipts per step;
- a fixture home for the copy;
- the challenge word for loud approvals;
- the cross-site fix is a prerequisite;
- ratings per the owner's 2026-09-22 decision.

**Delegated decisions, as recommended (2026-09-29).** Each was taken as
recommended below under the owner's "build all pending specs" delegation,
not picked by the owner, and is recorded in
`docs/decisions/2026-09-29-program-delegated-decisions.md`. The reasoning is
kept so the owner can overrule any of them, or revisit them on evidence.

1. **The box's default posture before you've said anything.**
   *Decided: "announce".* Reads and installs go ahead and are announced;
   writes to outside hosts and private-data sends ask, as they already do
   everywhere in Friday.
   - This follows your "no restrictions unless the user explicitly sets
     them". The questions left are Friday's existing ones, not new salon
     restrictions.
   - The cost is that a bad package can fetch anything it wants while it
     builds. The scan, the cooldown and the box's wall are what stand in
     the way, and the wall is weaker in B1.
   - The alternative, "ask", is OpenShell's default. It is safer and noisier.
   - Either way, "ask me before X" is always available as an owner rule.
2. **Self-editing her checkpoint and laws in the salon.**
   *Decided: allowed, only with the loud approval of §7.4.* That means a
   separate card, no grant can answer it, a fresh challenge word, and a
   signed notice.
   - The alternative is "never in the salon, hand-merge only". It is simpler
     and stricter, and it means Friday can never fix her own gate even when
     you ask her to.
3. **Someone else's key in your Friday.**
   *Decided: yes, bound to one codebase.* It is metered separately,
   Friday never uses it elsewhere, and it is deleted in one click.
   - The cost: Friday holds a secret that isn't yours. It is encrypted like
     yours, never enters the box, and its owner can ask you to remove it.

**Owner rulings since the spec was accepted** (dated; each may be revisited
by the owner):

1. **2026-09-29, LocalStack and telemetry.** *"I don't want any telemetry
   built into our system so that's out. But let's emulate as much as we can
   and learn as much as we can and build our own as much as we can."*
   LocalStack is out entirely (§2.1); no salon component may phone home
   (§4.6); Phase 5 is Friday's own emulator (§4.6.1).
2. **2026-09-29, no local hosting of tools.** *"I was more thinking like an
   artifact that Claude serves to the public web, if the user so chooses...
   I don't want to launch MCP servers off of my local hardware."* Friday
   never serves tools or pages to the internet from the user's hardware; the
   manifest's hosting tiers are gone (§4.10.2); static artifacts publish
   through §4.10.1.
3. **2026-09-29, the default publish host is "This PC"**, with the isolated
   static server, its own tunnel hostname, the read-only `published/`
   folder, no route to Friday, the kill switch, reachability status and the
   adversarial tests; hosted adapters are for pages that must stay up around
   the clock (§4.10.1).

**Product intent flagged for the owner** (from the competitor pass; each is
a stance on what Friday is for, not an engineering choice; the recommended
default is taken until the owner says otherwise):

1. **Does Friday publish, or only prepare?** Recommended: she runs the
   user's own deploy path and verifies the URL, hosts static pages from this
   PC or the user's own Pages account, and never registers a domain, never
   picks a vendor and never sells hosting. Every competitor sells hosting.
2. **May a tool Friday wrote for herself act while the owner is away?**
   Recommended: an internal-class self-made tool may be reflex-callable; no
   grant ever covers an outward self-made one. The owner may want it looser
   or stricter.
3. **Licence posture: inform, or refuse?** Recommended: announce and flag,
   never refuse without an owner rule. Whether Friday holds an opinion about
   what the user's apps may be built from is content policy the owner
   directs.

**Still open, not blocking:**

- whether upstream moto's server dispatch handles single-port routing well
  enough on its own (checked first in Phase 5; it moves the estimate);
- PGlite's licence (Phase 5), and whether Mailpit ships a Windows build;
- which hosts Claude's agent contacts beyond the API (the Phase 3 capture);
- whether a real Vite or Next dev server tolerates the AppContainer beyond
  the real-path issue (Phase 4);
- S3's last check: the HTTP-level allow, refuse and revoke through curl in
  a sandbox, and a Windows-side packet capture (`pktmon`, admin) if the
  sandbox path is to be proven the way the gateway side was.

---

## 13. What would falsify this

- **B0 is too small.** If the owner's first five real codebases all need a
  server, the frame-first bet is wrong, and B2 becomes the default wherever
  it exists.
- **"Announce" is noise.** If the owner stops reading install lines within a
  week, announcements need batching per step, or they are theatre.
- **The local seat can't do small edits.** If Bonsai2's edit acceptance
  rate over the first fifty steps is under half, the default small-edit seat
  should be the heavy seat, with a line saying so.
- **Voice approvals get made by accident.** One accidental approval in
  room mode means room mode needs Household Identity before salon cards can
  be answered by voice there.
- **Nobody shares.** If Phase 8's file export sees no use, Phase 9 is
  building a market for nothing, as `workspace-ecosystem.md` §7 already
  warns.

---

## 14. North-star mapping

`docs/design/north-star/agent-friday-ideal-product-spec.md` is the target
the product is measured against. Its owner rulings are in `AMENDMENTS.md`,
and `GAP-MATRIX.md` holds the requirement ids used below. Where a ruling and
the spec differ, the ruling wins.

### 14.1 What the salon implements

| North star | Requirement | Where the salon meets it |
|---|---|---|
| §21.15, NS-21.15-1/2/3 | Code: worktrees and branches, scoped sandboxes, tests, security scanning, commit prep, explicit push and merge approvals, provenance to tasks and goals. Friday MUST NOT modify a repository outside a selected worktree or branch | Codebase chats commit only to their own repo or a salon branch (§4.8). Self-edit uses a worktree (§7.1). Every step has a receipt tied to its task and goal (§4.8, §8). Publishing is an outward card |
| §24.1, §30.14, NS-24.1-1/2 | Artifact contract: id, ancestry, provenance, fingerprint, sensitivity, verification, QA, receipt, versions | §4.2 adopts the §30.14 fields and extends `kind` |
| §26.11, NS-26.11-1 | Sandboxed execution: scoped directories, allowlists, limits, environment filtering, secret isolation, output caps, receipts | The box tiers (§4.4), the environment built from nothing (FA2), keys at the proxy (§4.7), per-step receipts. B1's weaker guarantees are disclosed, not hidden |
| §26.13, NS-26.13-1 | A destination policy per connector, provider, skill and renderer | `policy.yaml` per codebase (§4.5). The default for unknown hosts is amended by A8 (§14.2) |
| §11.3, NS-11.3-11; §26.14 | Browser origin alone must not establish owner authority; proxied loopback is never trusted automatically | Phase 4 is gated on the Origin / Sec-Fetch-Site check this row already picks (§3.2, §10) |
| §23.3 | Credentials never shown to models; stored encrypted; ownership metadata; revocable | Keys are injected at the proxy and never enter the box. Guest keys carry their owner and codebase, and one click removes them (§4.7) |
| §23.7–§23.9, NS-23.9-1/2 | Friday-generated skills: manifest, tests, static and security checks, fixture demo, owner-approved install, rollback. A skill cannot loosen cLaws or modify its own permissions | Workspace bundles made in the salon (§4.9) use the `workspace-ecosystem.md` manifest, the step tests, the install scan (§4.5), "installed means disabled", and git rollback. A bundle's grants are the owner's, never its own |
| §23.12, §7.3, NS-7.3-1, NS-23.12-1 | Marketplace and federation stay absent from the core UX until sandbox, signing, review, permission diff, incident response and revocation are validated | Phase 8 is signed-file export only. Phase 9 (market cards) waits on federation being switched back on *and* on §23.12's validation list |
| §7.2, NS-7.2-1/2 | Not a creator marketplace or a compute-rental economy | No prices. Positrons and negatrons are ratings, per the owner's 2026-09-22 decision. Nothing is bought or sold |
| §18, §18.8, §18.9 | Approvals, grants that cannot authorize constitutional violations, delivery receipts, receipt chain | Cards are policy edits (§4.5). The floor refuses grants for loud approvals, and go-live and publishing are non-grantable by default. Step receipts use `goals-and-delivery-receipts.md` |
| §13.2, §26.24 | A constitution change presents a readable diff and needs re-attestation before outward actions resume | The loud card lists each protected file with a plain-language line. cLaws edits re-pin only after approval (§7.4) |
| §12, A6, §6.4 | Both paths and the user picks; no silent substitution | Per-codebase seats and key profiles, and the header line (§4.7). A rejected guest key never falls back to the owner's (§11) |
| §12.9, §29.7 | Cost management and a cost ledger | `cost_calls` gains `key_profile` and `codebase` (§4.7) |
| A3 | No telemetry, full stop | Every stand-in, backend and engine has telemetry off, verified by packet capture (§8, §9.4). OpenShell ships with telemetry on, so it is a backend only past that gate (§2.2) |
| A4, §22.5, NS-22.5-1/2 | Voice parity: same cards, answerable by voice or on screen; cloud voice sees private data only as a PII-free local summary | §6 in full. The need for a general spoken card answer (§6.6 item 1) is exactly the gap NS-22.5-1 records |
| A7 | Continuous deploys only to the owner's Friday: guarded, health-checked, rolled back automatically on failure | The self-edit swap (§7.3 steps 3–4) |
| §6.5 | "Done" is a verified state | Friday may not say a step is done unless its receipt shows it, including the non-blank preview hash (§4.8, §11) |
| §6.6, §23.11 | Untrusted content cannot confer authority | Fetched content is tainted (§8). Voice approvals count only in the owner's own latest words (§6.3) |

### 14.2 Where the salon amends it

Two of the owner's 2026-09-29 decisions depart from the spec as written.
They are recorded as amendment **A8** in `AMENDMENTS.md`.

1. **§26.11 and §26.13 (unexpected domains are blocked or need approval).**
   In a codebase's box, reads (GET, HEAD, OPTIONS) and package installs to
   hosts without a rule go ahead and are announced, and they go on the
   step's receipt. This is decision 1, following the owner's "no
   restrictions unless the user explicitly sets them".
   - It applies only to the box's own traffic.
   - Friday's own egress, connectors and providers keep §26.13 unchanged.
   - Writes, and anything carrying private data, still ask.
   - "Ask" stays available per codebase or as an owner rule.
2. **§13.1 ("non-editable" constitution), in part.** The constitution stays
   non-editable *by Friday on her own authority*. The owner may change it
   through the salon (decision 2):
   - only on the copy;
   - only through the loud approval of §7.4;
   - with §13.2's readable diff and re-attestation;
   - never through a grant.

Decision 3 (guest keys) needs no amendment. It is §23.3's "connection
ownership metadata" applied per codebase.

## 15. Sources

Checked 2026-09-29.

**LocalStack**
- Pricing (checked 2026-09-29: Hobby plan needs an account; offline delivery
  only on top tiers; "Telemetry Sharing" enforced on the free plan):
  https://localstack.cloud/pricing
- The S4 study of the archived tree:
  `docs/design/research/2026-09-30-localstack-archived-repo-study.md`
- Repository (archived 2026-03-23; README on the unified image, Apache-2.0 +
  EULA, Hobby plan): https://github.com/localstack/localstack
- README: https://raw.githubusercontent.com/localstack/localstack/main/README.md
- Releases (v4.14.0, 2026-02-26):
  https://api.github.com/repos/localstack/localstack/releases
- Pricing (Hobby, non-commercial): https://localstack.cloud/pricing
- Account and auth token requirement for the single image:
  https://blog.localstack.cloud/localstack-single-image-next-steps/ ;
  https://blog.localstack.cloud/the-road-ahead-for-localstack/

**NVIDIA OpenShell**
- Repository (Apache-2.0, releases, telemetry opt-out in README):
  https://github.com/NVIDIA/OpenShell ;
  https://api.github.com/repos/NVIDIA/OpenShell/releases
- Architecture (gateway, supervisor, Landlock, seccomp, credential
  injection): https://docs.nvidia.com/openshell/latest/about/architecture
- Support matrix (backends; Windows through WSL 2 + Docker Desktop,
  experimental): https://docs.nvidia.com/openshell/latest/about/support-matrix
- Policy overview (five sections, default deny, live network reload):
  https://docs.nvidia.com/openshell/latest/how-it-works/policies/overview
- Policy schema and network rules (method and path, binaries):
  https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema ;
  https://docs.nvidia.com/openshell/dev/how-it-works/policies/network-rules
- Policy prover (SMT): https://docs.nvidia.com/openshell/how-it-works/policies/prover
- NemoClaw (reference stack on OpenShell):
  https://docs.nvidia.com/nemoclaw/latest/about/overview

**Stand-ins and boxes**
- moto (Apache-2.0): https://github.com/getmoto/moto
- Mailpit (MIT): https://github.com/axllent/mailpit
- Azurite (MIT): https://github.com/Azure/Azurite
- MinIO (archived, source only): https://github.com/minio/minio
- microsandbox (Apache-2.0, Windows via WHP):
  https://github.com/superradcompany/microsandbox
- E2B self-host (Linux with KVM only): https://github.com/e2b-dev/infra
- gVisor (Linux only): https://gvisor.dev/docs/
- Windows Sandbox: https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-faq
- Docker Desktop licence (free for personal use and small businesses):
  https://docs.docker.com/subscription/desktop-license/

**In this repository**
- `docs/design/research/2026-09-30-salon-competitor-gap-matrix.md`: the
  competitor pass behind §4.11, with every product claim cited.
- `docs/design/active/workspace-ecosystem.md` and
  `docs/design/research/2026-09-20-extension-ecosystems-survey.md`: the
  sandbox, broker, cooldown and ratings evidence (Figma, Obsidian, pnpm, the
  Felt et al. permission studies).
- `docs/design/active/grow-button.md` §6–§8: the red-first gate, the
  untouchable set, and rollback.
- `docs/design/active/friday-builds-agents.md` §7: FA1–FA13.
- `docs/design/active/avatar-visual-genome.md` Appendix A: the ratings
  decision.
