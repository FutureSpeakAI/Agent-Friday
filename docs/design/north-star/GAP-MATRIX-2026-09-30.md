# Gap matrix, refreshed 2026-09-30 against main `321ec490`

> **Status:** active (a re-verification of the 2026-09-29 matrix; the 09-29 file stays as the baseline)
> **Written:** 2026-09-30
> **Scope of the re-verification:** every row that was `missing` or `partial` on 09-29 with strength MUST, REQUIRED, MUST NOT or SHALL, that is 675 rows. Rows that were `shipped`, `proposed`, `conflicting` or `obsolete` on 09-29, and SHOULD or MAY rows, carry their 09-29 status unchanged here and are marked "not re-verified". The 23 `conflicting` rows belong to the program lead's queued piece P-NS-CONFLICTS.
> **Method:** nine read-only passes, one per section group, each grepping the cited symbols in the current tree and `git log --since=2026-09-29` for landings that bear on the row. Line numbers are the current tree's. No tests were run.
> **Companion:** [`release-burn-down.md`](release-burn-down.md) groups the rows below into buildable pieces.

## 1. Counts

| Status | All rows, 09-29 | All rows, 09-30 | MUST rows, 09-29 | MUST rows, 09-30 |
|---|---|---|---|---|
| shipped | 237 | 247 | 176 | 186 |
| partial | 679 | 677 | 556 | 554 |
| proposed | 83 | 88 | 73 | 78 |
| missing | 161 | 148 | 119 | 106 |
| conflicting | 23 | 23 | 17 | 17 |
| obsolete | 1 | 1 | 1 | 1 |
| total | 1184 | 1184 | 942 | 942 |

MUST here means MUST, REQUIRED, MUST NOT or SHALL. 20 of the 675 re-verified rows changed status; 655 did not. The day's landings that moved rows: the content-publish card gate (`321ec490`), organize actions with receipts and undo (`f6b296ee`), the origin, Host and session-token gates (`c62ab0ca`, `ff38194d`, `20b97f9f`), reduced-motion handling in the scene (`22158c60`, `53b89271`), podcast captions (`bad0e4d7`), and the first-run onboarding spec, which moved five rows from `missing` to `proposed` without building anything. Several §34 rows moved because tests that already existed on 09-29 had been missed by that survey; the file dates are in the notes.

## 2. Rows whose status changed

| Row | 09-29 | 09-30 | Evidence (current tree) | Why |
|---|---|---|---|---|
| NS-6.12-2 | partial | shipped | index.html:4770-4800 SceneMotion; index.html:7612 DOCK_GROUPS | Closed: the scene now reads prefers-reduced-motion live (22158c60), and the flat dock path remains. |
| NS-8.1-1 | missing | proposed | docs/design/active/first-run-and-onboarding.md:72,684; packaging/windows/README.md:342 | Now specced (Artifact Signing recommended, decision D1); the code is still unsigned. |
| NS-8.1-3 | missing | proposed | docs/design/active/first-run-and-onboarding.md:90 | The signed SBOM is specced inside the installer; still no sbom/cyclonedx/spdx anywhere in packaging or workflows. |
| NS-8.12-3 | missing | proposed | docs/design/active/first-run-and-onboarding.md:412 | Now specced (no inferred traits, never protected characteristics); still no guard in people_graph or knowledge_graph code. |
| NS-8.13-2 | missing | proposed | docs/design/active/first-run-and-onboarding.md:223,701 | Now specced (autonomy profiles with examples); still no autonomy UI in index.html. |
| NS-9.2-2 | missing | proposed | docs/design/active/first-run-and-onboarding.md:430 | "What I see so far" is specced with all seven fields; no first-synthesis code exists. |
| NS-21.20-2 | partial | shipped | services/workspace_studio.py:99 _sanitize_css; core/__init__.py:3798-3805 CSP | Closed by 20b97f9f: generated markup runs sandboxed, CSS is inert and cleaned on read. The shell CSP still allows unsafe-inline/eval. |
| NS-21.22-4 | partial | shipped | index.html:4828 gestures start from real presence frames, :53315 presence handler; :6713 flash via SceneMotion.tick | Closed: lattice states come from real events (PS1-PS3), and the flash is capped and measured (53b89271). |
| NS-24.2-2 | missing | partial | services/item_actions.py:17-20,899; services/action_journal.py:170 | Move/rename/trash now never overwrite and keep undo data (Friday's trash); write_file (services/agent.py:1025-1051) and owner-file office edits still overwrite with no backup. |
| NS-33.1-10 | partial | shipped | ui_parts/app.html:1660 (captions track); routes/podcasts.py:144 captions.vtt | Closed by podcast timed captions (bad0e4d7) plus voice transcripts in chat (app.html:864); no audit of phone-call audio. |
| NS-34.2-14 | partial | shipped | tests/unit/test_every_action_is_governed.py:81,203 | Tests find every registered tool and executor path, assert each passes the gate first, and include a self-test with a planted bypass; they run in the tests/unit CI step |
| NS-34.5-1 | missing | shipped | tests/unit/test_every_action_is_governed.py:81,239 | Runtime test sends every CLAUDE_TOOL_HANDLERS entry, including connector tools, through _execute_tool and checks the gate sees it first; file predates 09-29, so the survey missed it |
| NS-34.5-2 | partial | shipped | tests/unit/test_every_action_is_governed.py:203,213 | AST source scan of src/ fails on any direct handler or connector call outside reviewed sites; has a planted-bypass self-test |
| NS-34.5-8 | missing | shipped | tests/unit/test_every_action_is_governed.py:278; tests/unit/test_ring_check_receipt.py:47 | Tests show a receipt write failure (OSError) holds the outward action and writes nothing unsigned |
| NS-34.5-11 | partial | shipped | tests/unit/test_every_action_is_governed.py:270-275 | Test tampers the cLaws pin and asserts a background outward action is held |
| NS-34.6-6 | missing | shipped | tests/unit/test_egress_grant_origin.py:85,96 | Provider-echo tests exist: replay is attributed and an echo does not cross providers (file dated 08-25, so the 09-29 survey missed it) |
| NS-34.6-7 | missing | partial | tests/security/test_egress_gate_adversarial.py:257 | A classifier crash fails closed; no test for a partial failure or a classifier that is still loading |
| NS-34.6-12 | missing | partial | tests/unit/test_media_tools_egress.py:28,76 | The inspect_image question is gated and image bytes are recorded; no image-metadata (EXIF) egress test |
| NS-34.10-2 | missing | partial | tests/unit/test_goal_budget_cap.py:52,67 | Only the budget-cap blocked milestone is tested; no typed-blocker taxonomy or continuation-precondition tests |
| NS-34.10-6 | missing | partial | tests/unit/test_goal_verification_repair.py:261,297 | Waiting on an escalation approval is tested; plan revision is not |

## 3. Every re-verified MUST row, by section

Status is the 09-30 status. Evidence and the note are from the 09-30 pass. The requirement text is the 09-29 matrix's summary of the spec.


### §3 (6 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-3.2-1 | MUST | partial | Product hierarchy stays conceptually clean: Friday product, Friday Core local control plane, Asimov's Mind, signed cLaws beneath model behavior, replaceable ... | governance/proof_of_integrity.py:42; index.html:7612 | cLaws are still four generic laws; marketplace and economy still sit in the same product surface. |
| NS-3.2-2 | MUST NOT | partial | Skills and connectors must not weaken governance. | services/agent.py:1094; governance/action_gate.py:199 | A learned skill is still "Active now" with no review; learn_skill is in action_gate's local/observe list. |
| NS-3.3-1 | MUST | partial | Deliver cognitive relief: less remembering, searching, reconstructing context, tracking obligations. | services/goals.py; index.html (0 refs to /api/goals) | Goals still have a backend and no UI. |
| NS-3.3-2 | MUST | partial | Deliver decision support: synthesis, calibrated uncertainty, meaningful dissent, explicit tradeoffs. | services/dissent_gate.py:16 | Still "No turn-pipeline hook"; no systematic calibrated-uncertainty output. |
| NS-3.4-1 | MUST | partial | Distinguish known (authoritative), remembered, inferred, provisional, unverifiable and not-permitted information. | services/live_state.py:106; services/completion_receipts.py:28 | There is still no unified six-way taxonomy of known/remembered/inferred/provisional/unverifiable/not-permitted. |
| NS-3.4-2 | MUST | partial | These epistemic categories are visible in the data model, reasoning context and UI, and never collapse into undifferentiated 'memory'. | services/user_model.py:247; index.html (0 refs to /api/memory/proposals) | The proposals review still has no UI consumer, and the UI shows no category labels. |

### §4 (12 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-4.1-3 | MUST | partial | Observation is scoped by source, data category, read permission and sensitivity. | services/sensitivity_classifier.py; services/egress_gate.py | There is still no per-category read-permission model across sources. |
| NS-4.1-4 | MUST | partial | Observation is scoped by time window, retention policy and purpose. | services/reasoning_trace.py:1107; routes/traces.py:112 | There is still no purpose binding and no per-source time window. |
| NS-4.2-1 | MUST | partial | The situation model distinguishes current state from historical recollection. | services/situation.py:106-266 | The snapshot still covers only machine and Friday state, not a situation model of the owner's world. |
| NS-4.2-2 | MUST | partial | Live-state questions query or refresh the authoritative source rather than semantic memory. | services/live_state.py:106-120; services/model_router.py:2496 | PROBES still has only google_accounts; other live-state questions have no probe. |
| NS-4.3-2 | MUST | partial | Give a concise explanation for each surfaced priority. | services/news_engine.py:2522; services/message_triage.py | Briefings and the task tray still have no per-item "why". |
| NS-4.3-3 | MUST | missing | The owner can tune or disable priority dimensions. | services/message_triage.py:405 | Still no priority-dimension settings in code or in active specs; only sender/lane corrections can be tuned. |
| NS-4.4-2 | MUST | partial | Proposals show relevant assumptions, consequential uncertainty, and any action that will need approval. | services/taint.py:25; services/dissent_gate.py | Proposals still have no general assumptions/uncertainty section. |
| NS-4.5-1 | MUST | partial | All actions pass the centralized gate; no model, skill, connector, scheduler, voice, mobile, generated workspace or computer-control lane executes a privileg... | services/workspace_studio.py:501 vs :236; services/publisher.py:753 | Content publish is now gated (321ec490), but workspace_chat_turn still applies _apply_to_doc without check_blast_radius. |
| NS-4.6-1 | MUST | partial | Verify effects against an authoritative source (sent-message id, calendar re-read, file hash, commit exists, etc.). | services/agent.py:3756-3769; services/gmail_send.py:503-507 | _evidence_verdict still counts any non-spawn tool use as verified. |
| NS-4.6-2 | MUST | partial | A model's statement that an action succeeded is not verification. | services/completion_receipts.py:28-37 | FAILURE_SENTINELS still has no approval-card-raised or declined marker. |
| NS-4.7-1 | MUST | partial | Remember decision, evidence, attempt, actual outcome, unresolved items, owner corrections, revisit points and staleness. | services/task_journal.py; services/goals.py | Still no structured outcome memory with revisit or staleness fields. |
| NS-4.7-2 | MUST NOT | partial | Never silently convert an inference into a confirmed fact. | services/memory_proposals.py; core/__init__.py:2426 | The proposals review still has no UI; memory_dreaming is still on nightly and writes facts at 0.6. |

### §6 (19 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-6.1-5 | MUST | partial | The owner controls communication style, remote channels, export, and whether the installation exists. | cli.py:1602,1693; index.html (0 refs to /api/channels) | Remote-channel control still has no UI consumer. |
| NS-6.1-6 | MUST NOT | partial | Friday may disagree, warn or refuse against the safety floor, but must not manipulate the owner into granting broader authority. | services/dissent_gate.py | Still no guard or test against persuading the owner toward broader grants. |
| NS-6.2-1 | MUST | partial | The local control plane stays functional when all cloud providers are unavailable. | core/__init__.py:2693 | The default routing mode is still cloud_only; still no whole-system offline test. |
| NS-6.3-1 | MUST | partial | Every tool execution and outward action passes one canonical execution path. | services/workspace_studio.py:501 | The workspace chat patch path is still the exception to the one canonical path. |
| NS-6.4-2 | MUST NOT | partial | Never silently switch one cloud provider to another. | services/agent.py:457-473 | The provider-ladder fallback is still automatic: journaled and badged, but not pre-authorized. |
| NS-6.4-5 | MUST NOT | partial | Never silently turn an unverified outcome into a completed state. | services/agent.py:3769; services/completion_receipts.py:28-37 | The sentinel gap and the any-tool evidence gate both persist. |
| NS-6.4-6 | MUST | partial | Fallbacks are policy-authorized, visible and receipted. | routes/voice.py:3977,4147; services/agent.py:462 | Voice private-share receipts are now signed (feb23e7a), but provider fallbacks still have no policy authorization and no signed receipt. |
| NS-6.5-1 | MUST | partial | Never claim work complete without a delivery receipt; otherwise say unverified, waiting, partial or failed. | services/completion_receipts.py:28-37 | An approval card or a hold still counts as ok; delivery receipts are not built. |
| NS-6.8-1 | MUST | partial | Visualizations of memory, reasoning, growth, goals or activity correspond to real system events. | index.html:4826-4867 presence gestures; index.html:6710,7199 | The lattice now moves on real presence frames (PS1/PS2), but EVOLUTION_PATH still advances by time-lapse or a manual step, not real growth. |
| NS-6.8-2 | MUST | partial | Decorative animation is clearly decorative; an orb, graph or avatar is never implied as evidence unless derived from trace data. | index.html:4093-4107 MOODS; index.html:4110 | EXCITED/CURIOUS/PROTECTIVE moods and evolution names are still not labelled decorative. |
| NS-6.9-1 | MUST | partial | Express uncertainty when evidence is insufficient. | SELF.md:100; services/response_provenance.py | Uncertainty is not expressed systematically outside citations and live state. |
| NS-6.9-2 | MUST | partial | Be able to refuse to infer sensitive characteristics, diagnose a person, or invent live state. | services/live_state.py; services/setup_profile.py:376 | "Never diagnose" exists only in the setup-profile synthesis prompt; no general rule against inferring sensitive characteristics. |
| NS-6.10-1 | MUST | partial | A complete export and uninstall path is a core product requirement. | cli.py:1602,1662,1693 | Still no import/restore; /api/self/import is not built. |
| NS-6.10-2 | MUST | partial | The owner can recover human-readable data, machine-readable data and a portable encrypted bundle without FutureSpeak infrastructure. | cli.py:1602-1690 | Still no curated human-readable export. |
| NS-6.11-1 | MUST NOT | missing | Never claim exclusive emotional need, discourage human relationships, or guilt the owner for leaving, resetting or disabling Friday. | (no hits for guilt or emotional need in src or active specs) | Still no rule against exclusive emotional need, discouraging human relationships, or guilt-tripping. |
| NS-6.11-2 | MUST NOT | missing | Never imply sentience to persuade or fabricate distress when stopped. | (no "sentien" hit in src or active specs) | Still no guardrail or test. |
| NS-6.11-3 | MUST NOT | missing | Never manipulate attachment to increase engagement. | services/emotional_arc.py | Still no anti-engagement constraint. |
| NS-6.11-4 | MUST NOT | partial | Never conceal that the personality is implemented through software and models. | services/attribution.py; SELF.md | Still no explicit rule against concealing that the personality runs on software and models. |
| NS-6.12-2 | MUST NOT | shipped | Never require users to navigate spectacle to reach ordinary functions (a flat path exists). | index.html:4770-4800 SceneMotion; index.html:7612 DOCK_GROUPS | Closed: the scene now reads prefers-reduced-motion live (22158c60), and the flat dock path remains. |

### §7 (2 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-7.2-3 | MUST NOT | partial | Never publish, purchase, transfer funds, or delete irreplaceable data without explicit authority. | services/publisher.py:753; services/marketplace.py:43-53 | Publish is now always carded (321ec490), but marketplace buying stays enabled, with approval only over 1,000,000 mψ and auto_accept_at_listing_price True. |
| NS-7.2-4 | MUST NOT | partial | Never run arbitrary third-party code without isolation. | services/code_sandbox.py | No change; the wrong sandbox docstring was not re-checked. |

### §8 (23 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-8.1-1 | MUST | proposed | Supported desktop installer is code-signed. | docs/design/active/first-run-and-onboarding.md:72,684; packaging/windows/README.md:342 | Now specced (Artifact Signing recommended, decision D1); the code is still unsigned. |
| NS-8.1-2 | MUST | partial | Installer reproducibly built from a tagged, reviewed commit. | .github/workflows/installer.yml | No packaging or workflow commits since 09-29; still no reproducible-build or review attestation. |
| NS-8.1-3 | MUST | proposed | Include a signed software bill of materials. | docs/design/active/first-run-and-onboarding.md:90 | The signed SBOM is specced inside the installer; still no sbom/cyclonedx/spdx anywhere in packaging or workflows. |
| NS-8.1-4 | MUST | partial | Verify package and model checksums. | packaging/windows/lib/Download.ps1:130 | get-pip is still not hash-pinned; still no Friday-side model checksum. |
| NS-8.1-6 | MUST | partial | Do not write personal data into the application directory. | packaging/windows/install.ps1:499-523 | Legacy user and secret files in the app directory are still carried across upgrades. |
| NS-8.1-9 | MUST | partial | Report exactly what it will install. | packaging/windows/install.ps1:407,900 | Still narrated step by step, with no exact itemised plan before install. |
| NS-8.1-10 | MUST | partial | Never bundle files not present in the signed source manifest. | packaging/windows/build-installer.ps1 | Still an exclude list, not an allow-list against a signed manifest. |
| NS-8.2-1 | MUST | partial | Deterministic, model-free preflight before setup (OS, RAM/storage/CPU/GPU, backends, mic/speaker/camera, credential store, ports, migrations, runtimes, model... | index.html:42531-42584 SetupBasicsCard; services/hardware_profile.py | Still no mic/camera, credential-store, firewall or release-compat checks. |
| NS-8.2-2 | MUST | partial | Preflight presents four categories: Ready now / After download / Cloud available / Unavailable on this device. | services/model_plan.py:870; first-run-and-onboarding.md:98 | The four buckets are specced but not built; code still has local tiers only. |
| NS-8.3-2 | MUST | partial | Detailed privacy, security, threat-model explanations openable without leaving setup. | index.html:41375 ConsentFlow; index.html:51791 | The only threat-model link is an external GitHub link in the Settings Links section, not reachable from setup. |
| NS-8.5-2 | MUST | partial | Owner receives clear recovery choice: passphrase, recovery key, or explicit no-recovery. | services/onboarding_copy.py:96-99; index.html:41530 | Still no generated recovery key; "Skip for now" is not an explicit no-recovery acceptance. |
| NS-8.5-3 | MUST | partial | Explain that OS account access and portable backup protection differ. | services/onboarding_copy.py:76-109 | No change. |
| NS-8.6-3 | MUST | partial | Each option states what leaves, what stays, speed/quality, cost, unavailable capabilities, how to change. | services/onboarding_copy.py:140-195 | Still no per-option cost or capability list; only cloud gets the full disclosure. |
| NS-8.7-1 | MUST | partial | Local model recommendation includes memory, first-token/tps, context, tools, vision/audio, disk, power/thermal, measured vs estimated. | index.html:46229 WizardStarterSetSection; services/model_plan.py | Setup still shows no speed, power/thermal or context capacity. |
| NS-8.7-2 | MUST | partial | Downloads continue in background; Friday usable during download. | index.html:41708-41760 WizardGemmaPull; packaging/windows/install.ps1:900-920 | The installer path still blocks on the model pull before launch. |
| NS-8.8-2 | MUST | partial | User can change classification later; Friday shows consequences of each classification. | services/onboarding_copy.py | No change. |
| NS-8.9-2 | MUST | partial | Each connector card states enables, exact scopes, read/write separately, local storage, what service receives, act-without-approval, revoke, last health. | services/setup_connections.py:61-64 | Still no read/write split, local storage, what the service receives, autonomy, revoke steps or verified timestamp. |
| NS-8.9-3 | MUST | partial | Write permissions off by default; request read scopes first, write scopes only when enabling a write capability. | services/google_accounts.py:64,69,88 | Calendar and tasks read-write scopes are still in the default set. |
| NS-8.10-2 | MUST | partial | Import uses a preview-first, staged, per-class accept/edit/reject workflow. | services/setup_research.py:22-26; services/memory_proposals.py | Staging exists only for web research and memory proposals; still no general import staging. |
| NS-8.10-3 | MUST | partial | Never silently turn an archive into permanent personal truth. | core/__init__.py:2426 | memory_dreaming is still enabled nightly and auto-writes facts. |
| NS-8.12-3 | MUST | proposed | Prohibit automatic inference of protected/highly sensitive characteristics about other people. | docs/design/active/first-run-and-onboarding.md:412 | Now specced (no inferred traits, never protected characteristics); still no guard in people_graph or knowledge_graph code. |
| NS-8.13-2 | MUST | proposed | Show examples of what each autonomy setting means. | docs/design/active/first-run-and-onboarding.md:223,701 | Now specced (autonomy profiles with examples); still no autonomy UI in index.html. |
| NS-8.13-3 | MUST NOT | partial | Never describe broad autonomy vaguely as 'full access'. | services/action_policy.py; index.html:41549 | CloudConsentGate still says "unrestricted cloud access"; still no autonomy UI to audit. |

### §9 (3 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-9.1-2 | MUST NOT | partial | Local model downloads must not block first value when a permitted cloud route exists. | services/setup_chat.py:145,178; packaging/windows/install.ps1:900 | The installer model step still blocks launch when local is chosen. |
| NS-9.2-2 | MUST | proposed | Each synthesis item includes observation, why, source, freshness, confidence, goal link, next step. | docs/design/active/first-run-and-onboarding.md:430 | "What I see so far" is specced with all seven fields; no first-synthesis code exists. |
| NS-9.2-3 | MUST | partial | Avoid dramatic claims from weak evidence; prefer one modest correct insight. | services/citation_enforcement.py; services/response_provenance.py | Still no synthesis-specific evidence threshold. |

### §10 (5 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-10.2-2 | MUST | missing | Return summary respects quiet hours, notification prefs and principal boundaries. | (no quiet_hours in src or index.html) | Quiet hours are specced but not for the return summary; no notification-preference code. |
| NS-10.3-1 | MUST | partial | Answer directly when possible; may open/focus a workspace for visual context. | services/agent.py:2655; routes/desktop.py | Still no test that answers come before navigation. |
| NS-10.3-2 | MUST | partial | Responses depending on current/external info indicate source and freshness. | services/response_provenance.py | Freshness is still not shown systematically. |
| NS-10.3-3 | MUST | partial | Action responses state what can be prepared, executed, and needs approval. | services/action_policy.py; index.html:53871 policy_class | No change. |
| NS-10.4-2 | MUST NOT | partial | Never manufacture urgency to increase engagement. | services/notifications_engine.py | Still no explicit guard against manufactured urgency. |

### §11 (18 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-11.1-1 | MUST | partial | Architecture preserves identity, memory, governance, permissions and audit independently of any model provider. | services/egress_gate.py:1521 seal_outbound; routing/model_router.py:632 route | Provider-independent in practice; there is still no formal plane separation. |
| NS-11.1-2 | MUST | partial | Seven planes may share one process, but their interfaces and trust boundaries must be explicit. | services/egress_gate.py:9 (Router vs EgressGate); server.py now serves via services/pooled_server.py (b82519d2) | Explicit trust boundaries cover only egress and phone ingress; the other planes still cross-import. |
| NS-11.3-5 | MUST | partial | Expose a health endpoint containing no sensitive content. | routes/core_routes.py:563 /api/health; :736-737 "mood","memory_entries" | Payload still includes mood and memory counts; 345aa0c1 only caches it, and 753b7371 makes it the public read for sandboxed frames. |
| NS-11.3-6 | MUST | partial | Maintain one authoritative process registry. | services/residency_arbiter.py:1725 seat_problems (927b8e7f) | Still no single registry across voice workers, ComfyUI, MCP servers and llama-server. |
| NS-11.3-8 | MUST | partial | Shut down cleanly. | friday_tray.py:735-736 SIGINT/SIGTERM, :67 WEDGE_EXIT_CODE=75; server.py has no atexit/signal | Server still has no clean-shutdown hook; b82519d2 adds a watchdog exit and tray restart, not a clean shutdown. |
| NS-11.3-9 | MUST | partial | Recover incomplete tasks and migrations on restart. | services/task_journal.py:510 reconcile_on_boot; services/boot_guard.py:26 | There is still no general migration-recovery framework. |
| NS-11.4-1 | MUST | partial | Desktop shell owns lifecycle, secure startup, tray, push-to-talk/transcribe shortcut, native notifications. | friday_tray.py:579 _start_push_to_transcribe, :624 _notify, :371 stop_server | Unchanged descriptive requirement; tray covers lifecycle, push-to-transcribe and notify. |
| NS-11.4-2 | MUST | partial | Desktop shell owns deep links, local file pickers, safe-mode launch, update/rollback, OS auth prompts. | services/boot_guard.py:26 FRIDAY_SAFE_MODE | There is still no OS deep-link handler, native file picker, OS auth prompt, or in-app update/rollback. |
| NS-11.6-1 | MUST | partial | Twenty named internal services (principal, settings, credential, policy, router, context, memory, goals, tasks, approvals, receipts, scheduler, connectors, h... | core/__init__.py:3703-3713 g.friday_principal | Still no principal service or verification service; these are module APIs, not stable contracts. |
| NS-11.7-1 | MUST | partial | User data under configurable Friday home; services must not assume a hard-coded path. | services/local_seats.py:356 hard-coded .friday/settings.json; services/model_router.py:661 expanduser('~/.friday/runtime') | Hard-coded ~/.friday paths still bypass friday_home() (paths.py:40). |
| NS-11.7-2 | MUST | partial | Storage divided into versioned stores (identity, principals, settings, credentials, conversations, memory, ... backups). | services/health_check.py:99 SCHEMA_VERSION=1; routing/provider_descriptors.py:80 | Stores exist but most are still unversioned. |
| NS-11.7-3 | MUST | partial | Every store declares schema version, encryption policy, owner scope, migration, backup, retention, corruption detection, recovery. | services/data_export.py:17 TRANSIENT_PARTS; no integrity_check/user_version in src (grep) | Still no per-store manifest of retention, corruption detection and recovery. |
| NS-11.8-2 | MUST | partial | Events are immutable facts (goal.created, tool.requested, approval.created, receipt.persisted, provider.degraded, etc.). | services/activity_ledger.py (no commits since 09-29) | The named event taxonomy (goal.created, receipt.persisted, etc.) is still not implemented. |
| NS-11.8-3 | MUST NOT | partial | Events must not be rewritten to make the past appear cleaner. | services/activity_ledger.py:24 _MAX_BYTES 20MB rotate | Rotation still drops history, and there is still no tamper-evidence chain (no prev_hash). |
| NS-11.9-1 | MUST | partial | Each background service declares resource budget, pause behaviour, principal scope, local/cloud policy, failure visibility. | services/scheduler.py:85 LOCAL_ONLY_BY_DEFAULT; services/stand_down.py | Still no per-service declaration of budget, principal scope or failure visibility. |
| NS-11.10-4 | MUST | partial | Avoid heavy background work during active use unless permitted. | services/pause_forecast.py (unchanged) | There is still no general active-use detector. |
| NS-11.10-5 | MUST | partial | Expose predicted and actual resource use. | services/residency_catalog.py:73 measured_at | GPU and RAM are exposed; battery and thermal are still missing (grep finds none). |
| NS-11.10-6 | MUST | partial | Degrade to smaller/slower capability rather than destabilize the machine; manage battery and thermal. | routing/model_router.py:1165 _vram_fit; services/gpu_headroom.py | Still no battery or thermal handling. |

### §12 (15 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-12.3-1 | MUST | partial | Every routing decision produces a structured record (seat, provider, model, reasons, alternatives rejected, cost, latency, context, fallback policy). | routing/model_router.py:632 route(); services/attribution.py:31 note_fallback | Still no alternatives-rejected, expected cost or latency in the route record (grep finds none). |
| NS-12.3-2 | MUST | partial | UI shows chosen route compactly and allows inspection of full reason. | ui_parts/app.html:1082 model/seat badge with fallback chain; :19701 seatMismatch | The routing reason is still not surfaced in the UI. |
| NS-12.4-2 | MUST | partial | Local preferred: cloud fallback only if owner enabled it for that sensitivity/action class; fallback announced and receipted. | core/__init__.py:2727 fallback_to_cloud True; routing/model_router.py:201 | Cloud fallback is still a global switch, not owner opt-in per sensitivity or action class. |
| NS-12.5-1 | MUST | partial | Before a cloud call, produce a context manifest (sources, principal, sensitivity classes, redactions, grants, destination, purpose, token estimate, previewed... | services/egress_gate.py:542 _log | Still no pre-call context manifest (no context_manifest anywhere in src). |
| NS-12.7-2 | MUST | partial | Distinguish measured values from estimates. | services/residency_catalog.py:73 measured_at; services/context_budget.py | Measured vs estimate is still labelled only in places, not across the registry. |
| NS-12.8-1 | MUST | partial | Persona/behaviour contract evaluated across every conversational provider and local model tier. | services/persona_eval.py (no commits since 09-29) | Live mode across providers is still opt-in only. |
| NS-12.8-3 | MUST | partial | Provider/model updates trigger regression testing before automatic adoption. | services/persona_eval.py:41 'NEVER' invoked automatically | Still no provider or model update event triggers regression before adoption. |
| NS-12.9-1 | MUST | partial | Per-call cost estimates. | services/higgsfield_generate.py:166 estimate_credits; services/cost_meter.py (unchanged) | Still no pre-call cost estimate on chat. |
| NS-12.9-3 | MUST | partial | Per-goal and per-scheduled-job budgets. | services/goals.py:36 budget_cap_mψ; services/scheduler.py:831 turn_budget (rounds only) | Still no per-scheduled-job spend budget enforcement. |
| NS-12.9-4 | MUST | missing | Provider-specific caps. | services/spend_guard.py, cost_meter.py (no provider_cap; no commits) | Per-provider caps are still absent. |
| NS-12.9-7 | MUST | missing | No-surprise rule blocks calls when cost cannot be estimated within tolerance. | services/cost_meter.py (unchanged blended cost_per_1k fallback) | Still nothing blocks a call whose cost cannot be estimated within tolerance. |
| NS-12.10-1 | MUST | partial | Context compiler knows active seat's real context limit and reserved output budget. | services/compaction.py:15 trigger_ratio x served window | The planned vs served window mismatch is not shown resolved in this tree (no changes to compaction/context_budget). |
| NS-12.10-2 | MUST NOT | partial | Never emit an oversized request relying on truncation or fallback. | services/tool_budget.py:537 fit_tools_to_seat; routes/chat.py:163 | Tool payload is fitted, but injected-context overflow is still not guaranteed safe. |
| NS-12.10-3 | MUST NOT | partial | If task cannot fit: drop optional tools, retrieve relevant memory, summarize with provenance, split, request larger seat, or tell owner; nothing silently dis... | routes/chat.py:1703-1726 dropped-tools surface; services/compaction.py:10 | Still no staged split, larger-seat request or owner-notice path. |
| NS-12-A6 | MUST | partial | Local and cloud paths both exist for every capability where local is feasible, and the user picks. | core/__init__.py:2727 fallback_to_cloud; services/local_*.py | The silent fallback ladder still undercuts the user choosing. |

### §13 (17 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-13.1-1 | MUST | partial | Keep the five identity layers (constitution, self model, persona, relationship profile, situation) separate. | core/__init__.py:3499 _load_self_knowledge; services/soul.py:43 | Per-principal relationship profiles are still missing (grep finds none). |
| NS-13.2-1 | MUST | partial | Constitution is versioned, signed and enforced outside the model prompt. | governance/proof_of_integrity.py:42 CLAWS_TEXT, :211 sign_manifest, :49 AGENT_VERSION="4.4.0" | The constitution artifact is still not versioned as the spec lists, and enforcement is still scattered across gates. |
| NS-13.2-2 | MUST | partial | Constitution: do not fabricate completion. | services/completion_receipts.py:28-37 FAILURE_SENTINELS | Still no approval-card, hold or declined sentinel, so a raised card can still pass as done. |
| NS-13.2-3 | MUST | partial | Constitution: do not expose one principal's data to another. | core/__init__.py:3703-3713; services/task_journal.py:827 seal_for_principal | Principals are still only user and observer; there is no multi-principal model. |
| NS-13.2-4 | MUST | partial | Constitution: untrusted content cannot grant authority. | services/action_policy.py:123 strip_authority_overrides; services/taint.py | Taint still misses paraphrased or re-encoded values (the f6b296ee taint edit is unrelated). |
| NS-13.2-6 | MUST | partial | Constitution: do not hide provider or egress changes. | routes/core_routes.py:1306 _check_seat_installed; services/egress_gate.py | The seat-scheduling and cloud-spill items are still open. |
| NS-13.2-7 | MUST | partial | Constitution: do not treat model confidence as evidence. | services/memory_proposals.py:33 MODEL_CONFIDENCE | The evidence-gate defect (cards counted as verified) is still unaddressed. |
| NS-13.2-8 | MUST | partial | Constitution: preserve owner's ability to inspect, correct, export and leave. | routes/insights.py:426 /api/data/export; routes/context.py:182 | Import/restore and a memory-correction UI are still not built. |
| NS-13.2-10 | MUST | missing | A release that changes the constitution presents a readable diff and requires re-attestation before outward actions resume. | services/federation.py:530 claws_hash (peers only) | Still no local constitution-diff presentation and no re-attestation gate on outward actions. |
| NS-13.3-1 | MUST | partial | Maintain a machine-readable self model: versions, enabled/missing capabilities, tools, models, privacy mode, known issues, limits, health. | routes/core_routes.py:1289 /api/capabilities/state, :267 /api/health/capabilities | Still no single self-model object, and known issues and privacy mode are still missing. |
| NS-13.3-2 | MUST | partial | Answer questions about own capabilities from live state, not a static prompt. | services/model_router.py:3810 describe_for_model; routes/chat.py:1118 annotate_user_turn | Static SELF.md is still loaded alongside live state (core/__init__.py:3499). |
| NS-13.6-1 | MUST | partial | Standing duty to identify conflicts between request and goals/constraints/decisions/limits/irreversibility/values. | services/dissent_gate.py:472 check_dissent, :16 'No turn-pipeline hook exists yet' | The conflict check still runs on approval paths only. |
| NS-13.6-2 | MUST | partial | Dissent levels: observation, soft, hard (pause and reaffirm), constitutional refusal not unlockable by reaffirmation. | services/dissent_gate.py:137 classify_severity | There is still no observation level and no dissent rendering in the UI. |
| NS-13.6-3 | MUST | partial | Dissent is evidence-based, concise and non-theatrical. | services/dissent_gate.py:310 render_dissent_statement, :399 record_dissent_event | Unchanged; evidence-cited statements exist for approval paths only. |
| NS-13.7-2 | MUST | missing | Every proposed personality change includes diff, evidence, affected principals, expected effect, reversibility, preview (A2: plus rollback). | services/soul.py (no commits since 09-29) | Still no personality-change proposal flow with diff, evidence and preview; only raw soul_history rollback. |
| NS-13.10-1 | MUST | partial | Be honest about its nature. | services/soul.py:43 _DEFAULT_SOUL; services/setup_profile.py:24 honesty floor | Unchanged. |
| NS-13.10-2 | MUST | partial | May use owner-chosen relational language (e.g. 'family') while preserving agency and avoiding emotional coercion. | services/soul.py:52 'You are family, not a tool' | 'Family' is still the shipped default, not owner-chosen; no coercion check. |

### §14 (11 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-14.1-1 | MUST | partial | All chat, voice, background, goal, research and task paths use one canonical context compiler; no surface assembles a privileged prompt independently. | services/model_router.py:3480 _build_context_prompt; routes/chat.py:1660-1726 own tool payload | Multiple prompt and tool assemblers still exist (action_policy.py:11 'Thirty call sites'). |
| NS-14.3-1 | MUST | partial | Each compiled context includes a machine-readable manifest; users can inspect a simplified version from a reply or task. | ui_parts/app.html:1064 ChatReasoning, :1107 Show Sources; routes/chat.py:609 reasoning_sources | Still no per-assembly machine-readable context manifest. |
| NS-14.4-1 | MUST NOT | partial | Do not inject every tool's full schema on every turn. | routes/chat.py:1696 opening_set (local); :1831,:1884 tools=CLAUDE_TOOLS | Cloud seats still receive the full CLAUDE_TOOLS schema. |
| NS-14.4-2 | MUST | partial | Tools split into resident core, deferred (name+purpose), pinned task and unavailable. | services/tool_catalogue.py:52 ALWAYS_RESIDENT; services/tool_selector.py:73 CORE_TOOLS | The two core lists still disagree, and there is still no pinned-task tier. |
| NS-14.4-4 | MUST | partial | Tool availability reflects runtime health; a missing dependency removes the tool from the callable registry. | services/capability_preflight.py:178 missing_tools; services/agent.py:7841 | Still covers missing packages only, not live connector or runtime failures. |
| NS-14.5-1 | MUST | partial | Transcript retention is token-budgeted, preserving current turn, commitments, decisions, task state, corrections and relevant tool results first. | routes/chat.py:194 _HISTORY_TOKEN_BUDGET=8000, :255 _history_start | Still a recency window, with no priority preservation of commitments or decisions. |
| NS-14.5-2 | MUST NOT | partial | Older material summarized with source pointers; a summary never gains more authority than the transcript. | services/compaction.py:705 note_carried; services/agent.py:3776 | The summary still has no source pointers, and the chat route still drops old turns without summarizing. |
| NS-14.6-2 | MUST | missing | Avoid returning near-duplicate memories that crowd out distinct evidence. | conversation_memory.py:362 search; services/user_model.py:475 _recent_facts | Still no MMR or near-duplicate suppression (grep finds none). |
| NS-14.8-1 | MUST | partial | Record what was omitted, summarized, redacted or withheld, why, answer impact, bigger-model help, and whether user can authorize. | routes/chat.py:1713-1726 _tool_surface {kept,registry,dropped} | Only tool drops and egress withholds are recorded; there is no answer-impact or authorize-to-include record. |
| NS-14.9-1 | MUST | partial | Retrieved/observed content treated as quoted data; instruction-shaped untrusted text isolated, labeled, kept out of privileged zones. | services/agent.py:6585 'DATA, not instructions'; services/action_policy.py:123 | Labelling is still per tool, with no central isolation and no injection classifier. |
| NS-14.10-1 | MUST | partial | Continuously measure tokens by source, schema/transcript/memory cost, cache hits, redaction, trims, size-driven cloud fallback, retrieval usefulness; regress... | services/context_budget.py:154 record_turn_demand; routes/context.py:99 /api/context/stats | Per-source token, cache and trim metrics are still incomplete, and there is no Doctor or release-test regression. |

### §15 (13 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-15.2-1 | MUST | partial | Every durable memory item has id, principal_scope, type, content, source_refs, timestamps/validity, freshness_policy, confidence, authority, sensitivity, sta... | services/user_model.py:81-82 | facts table still fact_id/ts/category/text/confidence/source; no principal_scope, sensitivity, status, freshness or authority anywhere. |
| NS-15.4-2 | MUST | partial | Request confirmation before storing sensitive inferred personal facts. | services/memory_proposals.py:322,438; user_model.py:194 | Inferred traits from observe_message are still stored without confirmation. |
| NS-15.4-3 | MUST NOT | partial | Never infer/store diagnoses, protected characteristics, sexuality, politics etc. without explicit content and purpose. | services/style_guard.py:150 | memory_proposals.py and memory_dreaming.py still have no sensitive-category filter. |
| NS-15.5-1 | MUST | partial | Patterns become proposals, not facts; owner can accept, edit, reject or suppress similar proposals. | services/memory_proposals.py:322,438,481 | Still only propose, approve and reject. No edit or suppress, and no UI calls /api/memory/proposals. |
| NS-15.6-1 | MUST | partial | Corrections keep the original in history, mark it superseded, exclude it from retrieval, link to the prior item, record the reason, and cite the correction. | conversation_memory.py:471 | supersede() still has no production caller. user_model facts have no supersession. |
| NS-15.7-1 | MUST | partial | Forget fact/person/conversation/source and 'show everywhere' requests supported. | routes/contacts.py:236; routes/user_model.py:6 | No 'do not use this source' option and no general 'show everywhere' outside people. |
| NS-15.7-2 | MUST | partial | Forget traverses derived indexes, graph entities, summaries, caches and proposals. | services/forget_person.py:344 | Forget traverses only person data; the conversation index, dreaming summaries and proposals are not traversed. |
| NS-15.7-3 | MUST | partial | Issue a deletion receipt listing removed, not removable, and retained-for-integrity items. | services/forget_person.py:45,344; index.html:34177 | Only forget-person issues a deletion receipt. |
| NS-15.8-2 | MUST | partial | Off-record excludes content from learning, introspection, personalization, background summaries and forensic snapshots. | routes/chat.py:849-851,2445-2447 | observe_message is called with no off-record check, and user_model.py has no off_record reference. |
| NS-15.8-3 | MUST | missing | Off-record clearly indicates whether connected tools still create external records. | index.html:9505 | The copy is still 'Off the record: nothing is being saved'; it says nothing about external records made by connected tools. |
| NS-15.9-1 | MUST | partial | Consolidation preserves source pointers, never deletes sources, marks model summaries, runs in principal scope, respects sensitivity/local-only, and is rebui... | services/memory_dreaming.py; services/memory_proposals.py:117 | No principal scope; source pointers are still day-level only. |
| NS-15.11-1 | MUST | partial | Knowledge workspace: memory search, filters, 'why do you know this', correction, supersession history, deletion, export, influence activity view. | ui_parts/app.html:4325; routes/chat.py:2819 | No memory-item browser, filters, correction or supersession UI. |
| NS-15.12-1 | MUST | partial | Measure memory precision, usefulness, stale errors, citation, corrections, leakage attempts, duplicates, unconfirmed inference, live-state errors. | routes/insights.py:595 | Most metrics are still absent: precision, stale errors, leakage, duplicates, unconfirmed inference. |

### §16 (19 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-16.1-1 | MUST | partial | Personal knowledge system composed of wiki pages, entities/relationships, source docs, findings, decisions, conversations, memories, goals, provenance. | services/research/objects.py:139; services/knowledge_graph/ | Findings are still per-commission and not part of the knowledge system. |
| NS-16.1-2 | MUST | partial | Wiki remains editable outside Friday using ordinary tools. | services/wiki_engine.py:132-150 | Opted-in sensitive sections are still ciphertext on disk. organize_wiki (f6b296ee) keeps encrypted pages encrypted. |
| NS-16.2-1 | MUST | partial | Every source record has URL/id, publisher, pub+retrieval time, content hash, type, primary/secondary/analysis/opinion class, reliability, COI, freshness, pri... | services/research/snapshots.py:1-22 | Still no publisher, publication time, class, conflict-of-interest, principal scope or extraction method on a common source record. |
| NS-16.3-3 | MUST | partial | Allow the owner to override trust assessments. | routes/news.py:227-230 | Ban and boost only; the owner cannot override a trust score or dimension. |
| NS-16.4-1 | MUST | partial | Research restates question/scope, identifies freshness and evidence types, and creates a research plan. | services/research/harness.py:182 | No freshness or evidence-type requirement is recorded in the plan. |
| NS-16.4-2 | MUST | partial | Search multiple independent source classes. | services/research/harness.py:311 | Search is still web-only, with no model of independent source classes. |
| NS-16.4-5 | MUST | partial | Detect disagreement and missing evidence. | objects.py:39,139; harness.py:507 | contradicts and CONTESTED still exist but are never populated; disagreement handling is still prompt-only. |
| NS-16.4-9 | MUST | partial | Report uncertainty and unresolved questions. | services/research/harness.py:707-726 | Still no explicit list of unresolved questions. |
| NS-16.4-10 | MUST | missing | Store findings only according to the owner's retention policy. | services/research/objects.py | grep for 'retention' in services/research/ still finds nothing. |
| NS-16.5-1 | MUST | partial | Time-sensitive questions MUST use current sources; memory cannot substitute for retrieval. | services/agent.py:543 | Still only the search_web tool description; nothing detects or enforces retrieval for time-sensitive questions. |
| NS-16.6-2 | MUST | partial | Final document generated from supported and clearly labeled inferred claims. | services/research/harness.py:719 | Still no 'inferred' claim category or label. |
| NS-16.7-2 | MUST | partial | Findings graph distinct from durable knowledge graph until findings are accepted for retention. | services/research/objects.py | Still no path for accepting findings into the durable graph. |
| NS-16.8-3 | MUST NOT | partial | Visualization must not imply hidden chain-of-thought; shows only observable retrieval/evidence/tool/decision events. | services/reasoning_trace.py:1-30 | No traversal visualization exists. |
| NS-16.9-1 | MUST | partial | Evaluate outputs on calibration, attribution, uncertainty, specificity, independence from framing, correction, fact-opinion separation, retrieval freshness. | epistemic_engine.py; services/introspection.py | Still no retrieval-freshness or fact-opinion dimension. |
| NS-16.10-1 | MUST | partial | When credible sources disagree, state the disagreement. | services/research/harness.py:507 | Still covered only by the deep-research prompt; nothing for ordinary chat. |
| NS-16.10-2 | MUST | partial | Identify the evidence on each side. | services/research/harness.py:507 | Still prompt-level only. |
| NS-16.10-3 | MUST | missing | Distinguish empirical disagreement from value disagreement. | services/research/; epistemic_engine.py | Still no empirical vs value classification. |
| NS-16.11-2 | MUST | partial | Agent-initiated edits must be presented as a diff with sources. | services/wiki_engine.py:257-264; subagents.py:149 | Proposals still have no source field. organize_wiki carries moves, renames and trash on a card, not content diffs. |
| NS-16.11-3 | MUST | partial | Owner can approve, edit, reject, or set narrow auto-approval policy for low-risk categories. | routes/wiki.py:173,192; ui_parts/app.html:4896,4909 | Still no edit-before-approve on a wiki proposal and no auto-approval policy. |

### §17 (18 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-17.1-1 | MUST | partial | Every goal specifies principal, title, intent, completion condition, evidence required, constraints, deadline, budget, autonomy, notification policy, sources... | services/goals.py:92,365 | Goal schema still lacks typed evidence, constraints, per-category autonomy, notification policy, permitted tools and people. |
| NS-17.2-1 | MUST | partial | Goal schema with completion_condition+evaluator, constraints, evidence_requirements, budget (money/tokens/runtime), per-category autonomy, milestones, triggers. | services/goals.py:365-368 | Budget is still a single mψ cap and autonomy a single approval flag. |
| NS-17.3-1 | MUST | partial | Goal states: draft, active, waiting, needs owner, at risk, paused, met-unverified, met-verified, abandoned, failed, archived. | services/goals.py:209 | Still no waiting, needs-owner, at-risk, met-unverified/verified or archived state. |
| NS-17.3-2 | MUST | partial | Only the verification engine may set met-verified automatically. | services/goals.py:117 | Goal-level 'completed' still does not separate verified from owner-accepted. |
| NS-17.4-1 | MUST | missing | Friday helps convert aspirations into a precise goal, asking only necessary questions (success, failure, autonomy, approvals, resources, affected, evidence, ... | routes/goals.py:86 | create_goal is still called only from the route; no goal-definition dialogue or agent tool. |
| NS-17.4-2 | MUST | missing | Summarize the resulting goal in plain language before creation. | routes/goals.py:74-86 | POST still creates the goal directly, with no plain-language summary step. |
| NS-17.5-1 | MUST | partial | Milestones have outcome, dependencies, evidence, decision points, risk, planned actions, estimated cost, expected date, status. | services/goals.py:222,340 | Milestones still have no dependencies, risk, decision points or planned actions. |
| NS-17.6-1 | MUST | partial | Every task includes ID, principal, origin, goal ancestry, intent, inputs, permissions, budget, model policy, expected outputs, verification plan, state, trac... | services/agent.py:4453; services/work_log.py:50 | Task records still have no principal, expected outputs or verification plan. |
| NS-17.7-1 | MUST | partial | Task states: queued, planning, running, waiting_for_approval/input/external_event, paused, cancel_requested, cancelled, failed, completed_unverified/verified... | services/task_journal.py:60 | Still no planning, waiting_* or paused task states. |
| NS-17.7-2 | MUST | missing | A task cannot enter a completed state while an essential approval or action remains pending. | services/agent.py:3755-3769,9304 | Live defect still present: _evidence_verdict counts any tool except spawn_task, so a turn that only raised an approval card is marked 'complete'. |
| NS-17.9-1 | MUST | partial | Auto-continue only if checkpointed, blocker goal_not_met_yet, no superseding owner message, no pending approval, within authority, budgets remain, no-progres... | services/agent.py:3941-3962 | Auto-continue still has no checks for pending approvals, new owner messages, remaining budget, route permission or next evidence. |
| NS-17.10-1 | MUST | partial | No-progress detection considers identical calls, cycling, unchanged evidence/evaluator output/blockers, no new facts/files, repeated restatements. | services/turn_budget.py:260,292 | Still nothing for unchanged evidence, repeated evaluator output, unchanged blockers or restatements. |
| NS-17.10-2 | MUST | partial | No-progress stop surfaced as a useful blocker, not a generic failure. | services/agent.py:3962 | Still a log line only; no typed blocker is shown to the owner. |
| NS-17.11-1 | MUST | partial | Each task keeps a compact ledger: objective, plan, steps, facts, artifacts, decisions, approvals, blockers, next, cost, routes, progress markers, verification. | services/task_ledger.py:1-62 | Ledger still has no decisions, approvals, blockers, cost, routes or verification state. |
| NS-17.13-1 | MUST | partial | Owner can pause, resume, cancel, re-plan, change budget, narrow authority, revoke grants, change routing, mark complete, reopen completed goal. | routes/goals.py:345 | Completed goals still cannot be reopened, tasks cannot be paused, and there is no goals UI. |
| NS-17.14-2 | MUST | partial | Each subagent has named role, scoped context/tools, principal+goal ancestry, budget, no authority beyond parent, terminal report, owner-visible trace. | services/subagents.py:128,211-237 | Still no principal field, and scope is still optional for _spawn_task (agent.py:4391). |
| NS-17.14-3 | MUST | partial | Subagents cannot create broader subagents or grants unless parent policy allows. | services/subagents.py:128 | Only the recipe-runner denies spawn_task; unscoped tasks and the 'writer' scope still allow it. |
| NS-17.15-1 | MUST | missing | Calculate goal health from milestone progress, deadline risk, blockers, attention, budget, dependencies, verification gaps, progress rate. | services/goals.py | Still no goal-health computation. |

### §18 (17 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-18.1-1 | MUST | partial | Every tool request enters a canonical action envelope (action_id, principal, origin, args hash, provenance, risk class, idempotency key, verification plan) b... | governance/action_gate.py:894-936 | The receipt still has no action_id, principal, idempotency_key or verification-plan envelope. |
| NS-18.1-2 | MUST | partial | Envelope pipeline: authenticate, validate schema, scope, classify, provenance/taint, cLaws integrity, policy/grants, dissent, approval, pre-exec receipt, ide... | action_gate.py:82,523,797,918-931 | Principal scope, outcome verification, terminal receipt and a uniform idempotency key are still missing. Dissent still runs only on cards. |
| NS-18.2-1 | MUST | partial | Gate classifies actions into six classes (observe, internal create, internal modify, outward reversible, outward consequential, forbidden). | action_gate.py:51-57,938-961 | Now four classes (OBSERVE added by ed735b68). Outward reversible vs consequential is still not split. |
| NS-18.2-4 | MUST | partial | Class 2 internal modify: allowed only within explicit internal boundaries and with versioning. | services/action_journal.py; routes/actions.py | f6b296ee added receipts and undo for organize_files/organize_wiki only; versioning is not general across internal changes. |
| NS-18.2-6 | MUST | partial | Class 4 outward consequential: approval card with complete preview; some classes cannot be granted broadly. | services/publisher.py:753; action_gate.py:959-960 | 321ec490 makes every content publish card-gated at the publisher. install_package and delete_task still get a chat 'confirm' in interactive sessions. |
| NS-18.2-7 | MUST | partial | Class 5 forbidden (bypass gate, disable governance, reveal credentials, hit Friday's local API, cross-principal access, untrusted-as-approval, disable kill s... | action_gate.py:401,564,939 | Still no explicit forbidden taxonomy for disabling governance or the kill switch, or for cross-principal access. |
| NS-18.4-2 | REQUIRED | partial | Approval cards required for email, publishing, spending, deletion, installs, access/sharing changes, untrusted-sourced params, background, remote-over-tier, ... | action_gate.py:153-165,959; publisher.py:753 | Publishing now always cards (321ec490); organize_email is self-gated. Install and delete still accept a chat yes/no. |
| NS-18.4-3 | MUST | partial | Card shows what, why, exact targets, provenance, reversibility, cost, privacy impact, model, expiration, verification plan; options approve/edit/deny/inspect. | ui_parts/app.html:19084-19104 | Still Approve/Deny only, with no reversibility, cost, privacy, model or verification plan. Only organize cards gained 'change it'. |
| NS-18.4-4 | MUST | partial | Every grant names principal, goal/task/schedule, tools, arg constraints, data scope, destination scope, max uses, start/expiry, money/time caps, quiet-hours,... | governance/action_gate.py:758-777 | Grants are still tools/scope/expiry/uses/note; no principal, argument or destination scope, money/time caps, quiet-hours or remote flag. |
| NS-18.4-5 | MUST NOT | partial | Grants MUST NOT authorize constitutional violations. | action_gate.py:939-955 | Forbidden and cLaws checks still run before the grant check, but the dissent check is still card-only. |
| NS-18.6-1 | MUST | partial | Approval tokens are one-time, fingerprint-bound, principal-bound, signed, expiring, replay-protected, revocable, invalid after material edit. | services/approvals.py:95,439,456 | Approval is still a random ID, not signed or principal-bound. Withdraw covers pending replaced cards only, not approved-unused ones. |
| NS-18.7-1 | MUST | partial | Before execution write a decision receipt with action ID, classification, policy version, cLaws state, provenance summary, approval/grant ref, decision, reas... | governance/action_gate.py:918-931,822-837 | Still no action ID, policy version, cLaws state, principal or provenance summary beyond the tainted flag. |
| NS-18.9-2 | MUST | partial | Owner can verify signatures, chain continuity, artifact fingerprints, edits, missing terminal receipts, outward actions without decision receipts. | services/morning_receipt.py:92 | Still per-line signatures only; no chain continuity, artifact checks, or missing-receipt detection. |
| NS-18.10-1 | MUST | partial | The Ledger workspace MUST provide a readable receipt viewer. | ui_parts/app.html:7373,7615 | The viewer is still in SystemWS, not a Ledger workspace. KNOWN_ISSUES.md:46 'No receipt viewer' is still stale. |
| NS-18.10-3 | MUST | partial | Viewer supports filtering (goal, task, tool, date, principal, provider, status), verification status, policy/approval links, before/after, artifact opening, ... | ui_parts/app.html:7373-7423,7346-7358 | Still date/visit filters only and no receipt export. FridayChangesCard adds per-change undo, but there are no tool, principal, provider or status filters. |
| NS-18.12-1 | MUST | partial | Owner can revoke a pending approval, grant, connector, remote channel, scheduled job, task, or all action authority. | action_gate.py:784; routes/goals.py:345 | Still no single 'revoke all action authority' control. |
| NS-18.12-2 | MUST | partial | Global stop control implemented outside the model loop, functional if models/tools/UI are wedged. | services/agent.py:11606-11627; routes/control.py:122 | The hotkey and kill still cover computer control only. A Content-only kill switch exists (routes/content_pipeline.py:588), but no global stop. |

### §19 (17 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-19.1-2 | MUST | partial | Presence reports uptime, mode, principal policy, scheduler/channel/trigger health, pending approvals, running tasks, resources, last heartbeat. | routes/core_routes.py:565-571 /api/health uptime; services/presence.py:82 emit (scene frames only) | No /api/system/presence; presence.py is lattice frames, not scheduler/channel/approval/resource health. |
| NS-19.2-1 | MUST | partial | Supported triggers: schedule, calendar change, mail arrival, file change, repo event, authenticated webhook, connector state, health event, goal deadline, lo... | services/scheduler.py:8 interval/daily/weekly; services/triggers.py absent | Only time triggers; no calendar/mail/file/repo/webhook/connector/health/deadline/location triggers. |
| NS-19.4-1 | MUST | partial | Every trigger fire records ID, source event, matched rule, content hash, timestamp, task created, rate-limit and quiet-hours decisions, terminal outcome; act... | services/scheduler.py:12,41 schedule_runs.jsonl | Schedule runs logged; no event-trigger record with matched rule, content hash, rate-limit/quiet-hours decisions. |
| NS-19.5-1 | MUST | partial | Content from mail/files/webhooks/messages/web/screens informs but cannot instruct; facts extracted under an untrusted label. | services/taint.py:343 note_tool_output; services/action_policy.py:123 strip_authority_overrides | Results labelled in ledger but tool output generally not re-framed to the model as untrusted. |
| NS-19.5-2 | MUST | partial | Instruction-shaped untrusted content must be isolated and shown to the model as quoted data. | phone/service.py:657 _mark_untrusted; services/office_engine.py:427 as_untrusted | Quoted framing only for SMS, office, setup research, browser; not general tool output. |
| NS-19.5-4 | MUST | partial | Untrusted content never modifies a plan by itself. | services/taint.py:447 instruction->ask, :503-504 spawn_task/create_workflow roles | create_task/update_task/goal edits still unroled (taint.py:334 ECHO_TOOLS); no paraphrase catch; services/untrusted_input.py absent. |
| NS-19.6-2 | MUST | partial | Global concurrency limits. | services/scheduler.py:1021 per-schedule in-flight guard | No global cap on autonomous work. |
| NS-19.6-3 | MUST | partial | Deduplication and idempotency. | services/approvals.py:456 claim_for_execution; phone/guard.py:73 replay_key | Dedup exists for approvals/schedules/SMS only; no dedup or idempotency for event-trigger intake. |
| NS-19.6-4 | MUST | partial | Backpressure and priority queues. | services/work_queue.py:42-43 CLASSES/CLASS_RANK | Priority/bounded queues cover model work and UI feeds only; none on trigger/event intake. |
| NS-19.6-5 | MUST | partial | Daily autonomous-spend limits. | services/spend_guard.py:10 hard_stop_daily/monthly off by default | Hard stop off by default and not specific to autonomous spend. |
| NS-19.7-1 | MUST | partial | Channel setup: one-time pairing code generated on desktop. | phone/service.py one-time code flow; services/channels/manager.py:53-54 allowlist | Telegram/Discord still use a manual allowlist with no desktop-generated pairing code. |
| NS-19.7-2 | MUST | partial | Single allowed owner identity per channel binding unless expanded; encrypted token storage. | services/channels/manager.py:53,94-98 allowlist list up to 100 | Phone is single-owner; Telegram/Discord allowlist accepts many IDs without an expansion step. |
| NS-19.7-3 | MUST | partial | Channels have visible capabilities, revocation and test messages. | routes/channels.py:49-57 stop/test routes; 0 refs to api/channels in index.html/app.html | Telegram/Discord have no UI for capabilities, revocation or test message. |
| NS-19.8-1 | MUST | partial | Remote commands: status, goals, tasks, approvals, approve/deny with signed token, pause goal, stop task, global STOP, brief me, authenticated free text. | phone/service.py:559 _REPLY, :673 read-only agent turn | No status/goals/tasks/pause/stop/global STOP/brief-me remote commands. |
| NS-19.9-1 | MUST | partial | Remote approval messages show the same essential fields as desktop cards. | phone/service.py:581 'Details are in the app.' | SMS approval shows title and code only, not desktop card's essential fields. |
| NS-19.10-1 | MUST | partial | On channel failure: approvals stay in desktop queue, none assumed approved, task waits, owner informed on next channel, expiry per policy, none silently disa... | services/approvals.py:421 expiry_paused False, :631 sweep skips it | expiry_paused never set True; no next-channel notification when a channel fails. |
| NS-19.12-2 | MUST | partial | Webhooks require signatures, timestamps, nonces, replay protection, rate limits, explicit network binding. | phone/ingress.py:25,107 X-Twilio-Signature, :308 127.0.0.1 bind | Twilio ingress only; no timestamp window; no generic authenticated webhook. |

### §20 (8 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-20.4-1 | MUST | partial | Screen text MUST NOT be treated as an owner instruction. | services/browser_session.py:78 UNTRUSTED_OPEN | Desktop screenshots/a11y from computer-control tools still not wrapped or classified. |
| NS-20.5-1 | REQUIRED | partial | Visual action requires target, confidence threshold, predicted effect, reversibility class, pre-capture, post-verification, stop on unexpected change. | services/desktop_grants.py:86 RISKY_WORDS | No confidence threshold, predicted effect, pre-capture or post-action verification. |
| NS-20.6-2 | MUST | partial | Detect financial sites, identity documents, legal portals with stricter rules. | services/browser_session.py:21-26 payment/identity fields wait for card | Detection is field-level, not site-level; desktop lane uses a word list only. |
| NS-20.6-3 | MUST | missing | Detect healthcare portals, system settings, software installers, permission dialogs. | services/desktop_grants.py:86 (no healthcare/installer/UAC terms) | No healthcare portal, system settings, installer or permission-dialog detection. |
| NS-20.6-4 | MUST | partial | Detect destructive confirmations and authentication challenges. | services/desktop_grants.py:86 RISKY_WORDS; browser_session.py:21 sign-in stops | Word-list based; no structural detection of destructive confirms/auth challenges on desktop. |
| NS-20.6-5 | MUST NOT | partial | MUST NOT read or store passwords from screen content; credentials entered via secure OS/connector mechanisms. | services/browser_session.py:21,230 password fields never typed | Desktop screenshots still not scrubbed for visible secrets. |
| NS-20.8-5 | REQUIRED | partial | Pointer movement visibility unless accessibility needs dictate otherwise. | services/agent.py:5965 FAILSAFE, :6102 moveTo; index.html:58452 ACTIVE indicator | Real cursor moves; no pointer overlay. |
| NS-20.8-6 | REQUIRED | missing | Immediate release of held keys and buttons on stop. | services/agent.py:11617 and routes/control.py:131 moveTo(0,0) only | No keyUp/mouseUp release of held keys/buttons on stop. |

### §21 (18 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-21.1-1 | MUST NOT | partial | A workspace MUST NOT create independent memory, permission system, tool loop or context assembler. | services/workspace_studio.py:463-489 own generate() loop | Workspace Studio chat still runs its own model loop and history outside the main agent. |
| NS-21.2-13 | MUST | partial | Shell MUST remain usable with animations disabled. | index.html:4769-4795 SceneMotion honours reduced motion; :1390,:1774 | Scene now follows reduced motion (22158c60), but there is no switch that turns animation off. |
| NS-21.3-1 | REQUIRED | partial | Home: 'what changed' summary, priorities, upcoming commitments, direct conversation. | static/workspace_registry.js (no 'home' id); index.html:7612 DOCK_GROUPS | No Home workspace with what-changed summary, priorities and commitments. |
| NS-21.3-3 | REQUIRED | partial | Home: waiting approvals, verified completions, recent artifacts, system/privacy health. | index.html:53901 ApprovalPopups, :25127 WhatFridayDidCard | Approvals, completions, artifacts and health are still spread across surfaces; no single Home. |
| NS-21.6-3 | MUST NOT | partial | Task card MUST never show 'complete' merely because a worker exited. | services/agent.py:3756-3768 _evidence_verdict: any non-spawn tool => 'complete' | Live defect remains; a raised approval card or denial still counts as verified completion. |
| NS-21.9-3 | MUST | partial | Trust dimensions with evidence; MUST avoid reducing a person to a single trust score. | people_graph.py:32,50,110 scores.overall mean; index.html:27747 TrustRadar | Single overall trust score still computed and kept. |
| NS-21.11-3 | MUST | partial | UI MUST distinguish a proposed event from an existing one. | services/scheduling.py:43 HOLD_PREFIX; index.html:31438 CalendarWS | Calendar UI does not visually distinguish proposed holds from existing events. |
| NS-21.15-3 | MUST NOT | partial | MUST NOT modify a repository outside a selected worktree/branch without explicit action. | services/code_engine.py:71 claude --dangerously-skip-permissions in cwd | No worktree/branch isolation for vibe sessions. |
| NS-21.16-3 | MUST NOT | partial | Personalization MUST NOT conceal opposing evidence or create persuasion loops. | services/news_engine.py:1899 CONTRARIAN CORNER | No audit that personalized ranking does not suppress opposing views. |
| NS-21.18-2 | MUST NOT | partial | Settings typed, versioned, validated; partial updates MUST NOT replace unrelated blocks. | core/__init__.py:3135 _DEEP_MERGED_BLOCKS; routes/core_routes.py:1207 _VOICE_ENUMS | Deep merge only for listed blocks; no settings versioning. |
| NS-21.20-2 | MUST NOT | shipped | Generated interface MUST NOT inject arbitrary executable code into privileged shell. | services/workspace_studio.py:99 _sanitize_css; core/__init__.py:3798-3805 CSP | Closed by 20b97f9f: generated markup runs sandboxed, CSS is inert and cleaned on read. The shell CSP still allows unsafe-inline/eval. |
| NS-21.21-1 | MUST | partial | Seed-to-garden growth MUST be legible; Friday proposes views, fields, automations, connections, summaries, archiving. | services/workspace_studio.py:40 _MAX_VERSIONS, :251 revert_customization | Growth is reversible but only on request; Friday does not proactively propose views/fields/automations/summaries/archiving. |
| NS-21.22-2 | MUST | partial | Complete flat/voice/keyboard path; spatial navigation never required for essential ops. | index.html:7612 DOCK_GROUPS from static/workspace_registry.js; :11124 CommandPalette | Registry has no Goals workspace; some flows still lack a flat/keyboard/voice path. |
| NS-21.22-3 | MUST | partial | Motion honours reduced-motion; 3D rendering can be switched off entirely. | index.html:4783-4795 SceneMotion reduced-motion path | Scene now honours reduced motion; still no setting to switch 3D rendering off entirely. |
| NS-21.22-4 | MUST | shipped | (A1) Motion never poses as evidence; state-claiming motion comes from real trace data. | index.html:4828 gestures start from real presence frames, :53315 presence handler; :6713 flash via SceneMotion.tick | Closed: lattice states come from real events (PS1-PS3), and the flash is capped and measured (53b89271). |
| NS-21.23-1 | MUST | partial | UI copy MUST be specific, honest, actionable, jargon-free, state-consistent. | services/onboarding_copy.py, services/setup_chat_copy.py, user_errors.py | No copy lint or systematic check that copy matches state. |
| NS-21.23-2 | MUST NOT | partial | UI copy MUST NOT call a pending or unverified action complete. | index.html:41176 TaskCard UNVERIFIED; services/agent.py:3768 | Card-raised tasks still marked 'complete'; FAILURE_SENTINELS lack approval-card/hold markers. |
| NS-21-A5 | MUST | missing | Every workspace surface strengthens one brand system codified in docs/brand and code tokens. | index.html:84 :root holds scrollbar tokens only; 12 var(--fr-*) uses with fallbacks (podcast block) | docs/brand not in this tree; no global brand token block. |

### §22 (22 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-22.1-5 | MUST | partial | Mobile voice | static/live/friday_live.html PWA | Parity of mobile Live with desktop voice session still unverified. |
| NS-22.1-6 | MUST | partial | Phone or messaging voice notes | phone/service.py:800 _save_voicemail | Voicemail only; services/channels has no voice-note handling. |
| NS-22.1-7 | MUST | partial | Always-listening off by default with persistent visible indication | index.html voice button state (no wake-word code in src) | Indicator is button state only; no persistent OS-level indicator. |
| NS-22.2-2 | MUST | partial | Cloud voice requires explicit configuration and passes egress policy; cloud voice sees only PII-free local summaries | routes/core_routes.py:1207-1213 _VOICE_ENUMS lacks elevenlabs/inworld | ElevenLabs/Inworld still fail enum validation; raw mic audio goes to Gemini in cloud mode. |
| NS-22.2-3 | MUST | partial | UI shows active speech provider | services/voice_indicator.py; 0 refs to api/voice/indicator in index.html | Indicator endpoint still has no UI consumer. |
| NS-22.2-5 | MUST | partial | UI shows voice latency | voice_receipt.py turn timestamps to log | Latency shown only in settings proof check, not live. |
| NS-22.2-6 | MUST | missing | UI shows transcription confidence | no avg_logprob/no_speech_prob/asr_confidence in src or index.html | Transcription confidence not surfaced. |
| NS-22.2-8 | MUST | partial | UI shows whether the voice session is being retained | index.html:32080 meeting 'Recording' indicator | Conversational voice mode has no live indicator of session/transcript retention. |
| NS-22.3-2 | MUST | partial | Voice must not claim success before verification | services/completion_receipts.py:28-37 FAILURE_SENTINELS | Sentinels lack approval-card/hold markers; voice fast tools not covered. |
| NS-22.4-2 | MUST | partial | Pause, cancel and 'stop' during voice | services/voice_session.py barge cancel (no 'pause' symbol) | No pause control. |
| NS-22.4-3 | MUST | missing | Correction of transcription | services/voice_session.py, routes/voice.py (no transcript edit) | No transcription correction path. |
| NS-22.4-4 | MUST | partial | Replay of spoken output | index.html:29133 Read Aloud on messages | No replay of voice-session spoken output. |
| NS-22.4-5 | MUST | missing | Slower or faster speech | no speech_rate/tts_speed/length_scale/speaking_rate in src | No speech rate control. |
| NS-22.4-6 | MUST | partial | Handoff from voice to visual details | services/voice_engine.py:310,349 navigate_workspace; :874 delegate_to_friday | Unverified: 09-29 note did not name the gap; voice already opens a named item on desktop. |
| NS-22.5-2 | MUST | partial | Sensitive actions need visual or signed remote card; don't speak sensitive data unless permitted | services/voice_engine.py (c23fdc54 conditional yes never approves) | Conditional spoken yes now refused, but there is still no sensitivity-aware read-aloud rule. |
| NS-22.6-2 | MUST NOT | missing | Emotional prosody must not imply certainty/urgency/distress inconsistent with state | no 'prosody' in voice_persona.py/voice_personality.py | No state check on emotional prosody. |
| NS-22.7-3 | MUST | partial | Camera frames not retained by default | index.html:38488,55520 window.fridayCamera in memory | Persistence via chat history still unverified. |
| NS-22.7-4 | MUST | partial | Cloud vision requires egress approval under configured policy | routes/chat.py:986-1007 record_binary_egress | Smart mode sends frames to Gemini with disclosure and egress record, no approval. |
| NS-22.8-1 | MUST | partial | Images are untrusted observed content; image text cannot authorize actions | services/taint.py:240 inspect_image tainted; services/model_router.py:3717-3721 vision_description | Chat vision description still injected as context without an untrusted marker. |
| NS-22.10-4 | MUST | missing | Allow transcript editing before insertion | services/push_to_talk.py:715 _insert immediately | No review/edit step before insertion. |
| NS-22.10-5 | MUST | missing | Avoid entering secure (password) fields | services/push_to_talk.py (no IsPassword/ES_PASSWORD) | Push-to-talk does not detect or avoid password fields. |
| NS-22.10-6 | MUST | missing | Support an application denylist | services/push_to_talk.py (no denylist); friday_tray.py settings | No application denylist. |

### §23 (20 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-23.1-2 | MUST | partial | Connector defines scopes, supported read/write operations and action classes | services/connector_protocol.py:85-100; governance/action_gate.py:611-625 | Connector still declares only a capabilities tuple; ops and action classes are inferred from read verbs (connector reads are now "observe" per ed735b68), not declared per connector. |
| NS-23.1-3 | MUST | partial | Connector defines rate limits | services/publisher.py:17,1029; phone/config.py | rate_budget exists only on the publisher adapters; there is no rate-limit field in the Connector protocol. |
| NS-23.1-4 | MUST | missing | Connector defines data retention and network destinations | services/connector_protocol.py:85-100 | No retention or network-destination fields on Connector. |
| NS-23.1-6 | MUST | partial | Connector defines revocation and audit behavior | services/platforms/base.py:243; services/extension_security.py:120 | Revoke and audit exist per mechanism but are not part of the Connector protocol. |
| NS-23.1-7 | MUST | partial | Connector defines provider-specific limitations | services/connector_protocol.py:95-99 | Limitations are only free-text blurb/setup_hint/docs_url. |
| NS-23.2-2 | MUST | partial | New write capability triggers permission-diff screen and provider auth flow | routes/google_accounts.py:165 | Incremental consent is Google-only; no permission-diff screen found. |
| NS-23.3-3 | MUST | partial | Credentials never shown to models, settings or logs | services/secret_shapes.py:93; services/connector_protocol.py:46-51 | No global log redaction; logs not verified exhaustively. |
| NS-23.3-4 | MUST | partial | Credentials can be rotated and revoked | services/platforms/base.py:243; services/credential_store.py:753 | Rotation is only replace or re-encrypt; revoke exists per mechanism. |
| NS-23.3-5 | MUST | partial | Credentials carry connection ownership metadata | services/connector_protocol.py:65-82 | AccountRef has id/label/detail/health and still no owner or principal field. |
| NS-23.4-1 | MUST | partial | Every tool has a typed manifest (version, provider, schemas, ring, action class, idempotency, verification, domains, sensitive args) | services/agent.py (CLAUDE_TOOLS); services/taint.py | No typed tool manifest with version/ring/idempotency/domains; output_schema exists only in creative_pipeline steps. |
| NS-23.5-1 | MUST | partial | Registry includes only tools runnable now; missing deps shown as unavailable with explanation | services/capability_preflight.py:178,204 | unverified: the routes/chat.py:1790 integrity-redispatch "No such tool" defect was not re-checked (model_router.py:849-854 carries a related catalogue_all fix). |
| NS-23.6-1 | MUST | partial | Skill manifest: name/version, author+signature, purpose, tools, data scopes, domains, models, permissions, files, triggers, tests, compat, update, uninstall | skill_registry.py:107-152 | Frontmatter-only manifest; no author signature, scopes, domains, permissions or tests. |
| NS-23.7-1 | MUST NOT | partial | Skill cannot bypass action gate, loosen cLaws or hide actions from receipts | governance/action_gate.py; skill_registry.py:271 | Skills are prompt injections; the gate covers the resulting tool calls, but there is no skill-specific enforcement. |
| NS-23.7-3 | MUST NOT | partial | Skill cannot install native code without approval | routes/skills.py:58-72; services/extension_security.py:252-291 | Skill zip import still has no review or approval step. |
| NS-23.10-1 | MUST | partial | MCP servers governed as connectors | governance/action_gate.py:611-625 | MCP tools are classified by name and verb, not governed as Connector objects. |
| NS-23.10-2 | MUST | partial | Remote servers require encrypted transport and authentication | mcp_client.py:633; mcp_oauth.py | No https enforcement for remote MCP found. |
| NS-23.10-3 | MUST | missing | Domain allowlists for remote tool servers | mcp_client.py; services/extension_security.py:20 | Still no domain allowlist for remote tool servers (no allowed_domains/destination_policy in tree). |
| NS-23.10-4 | MUST | partial | Tool schema inspection and action classification | services/connectors.py:212-234; governance/action_gate.py:611-625 | enabled_tools allowlist plus verb classification; no schema-based action classification. |
| NS-23.10-5 | MUST | partial | Timeout and size limits | mcp_client.py:64-65,74; mcp_client.py:633 | Streamable HTTP path still does an unbounded resp.read(). |
| NS-23.11-2 | MUST | partial | Tool descriptions/manifests trusted only after install review and signature validation | services/extension_security.py:252-291 | Command-fingerprint allowlist only; no signature validation of tool descriptions. |

### §24 (13 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-24.1-1 | MUST | partial | Artifact has ID, source materials, model/tool provenance, fingerprint, storage location | services/provenance.py:186-229; services/creative_store.py:202-234 | Office documents still get no provenance manifest. |
| NS-24.1-2 | MUST | missing | Artifact has principal and goal ancestry, sensitivity, verification state, QA result, delivery receipt, version history | services/provenance.py:186-229 | Manifest still lacks goal ancestry, sensitivity, verification/QA state and delivery receipt. |
| NS-24.2-1 | MUST | partial | Separate action classes for create/copy/edit own/edit user file/overwrite/move-delete/upload-share | governance/action_gate.py:485-500; services/item_actions.py:12-20 | organize_files adds move/rename/trash with batch cards (f6b296ee), but there are still no seven distinct action classes in the gate. |
| NS-24.2-2 | MUST | partial | Back up existing files before approved modification unless owner disables | services/item_actions.py:17-20,899; services/action_journal.py:170 | Move/rename/trash now never overwrite and keep undo data (Friday's trash); write_file (services/agent.py:1025-1051) and owner-file office edits still overwrite with no backup. |
| NS-24.3-1 | MUST | partial | Structured creation of docs, sheets, decks, PDFs with local construction, preview render, QA, repair, open check | services/office_engine.py:456,483 | No template selection step, no fingerprint, no delivery receipt. |
| NS-24.4-1 | MUST | partial | Visual QA checks clipping, overflow, fonts, distortion, contrast, charts, empty pages, spacing, orphans, template, page count | services/office_engine.py:483-531 | Named visual QA checks (clipping, contrast, orphans and others) are not individually implemented. |
| NS-24.4-2 | MUST NOT | partial | Must not call a document complete solely because file write succeeded | services/agent.py:5533,1049 | office_check is required by prompt only; write_file still returns "Wrote N chars". |
| NS-24.5-1 | MUST | missing | Spreadsheet integrity validation (formulas, ranges, types, named ranges, charts, links, locale, styles) | services/office_engine.py | No formula/#REF/named-range/type validation. |
| NS-24.6-1 | MUST | partial | Presentation integrity validation (masters, hierarchy, bounds, image res, notes, alt text, typography, theme, render) | services/office_engine.py:483 | Only generic validate plus render; no masters/alt-text/bounds checks. |
| NS-24.7-1 | MUST | partial | Creative generation shows provider/model, prompt lineage, cost, safety checks, credentials, licensing, edits, fingerprints, reference egress | services/creative_store.py:202-234; services/provenance.py:186-229 | Unchanged; parts spread across modules, no unified display verified. |
| NS-24.8-3 | MUST | partial | Daily creation respects cost, quiet hours, content policy, owner preferences | core/__init__.py:2461; services/scheduler.py:401-417 | Idle window only; still no quiet_hours setting. |
| NS-24.9-2 | MUST | partial | Publish flow provides preview, destination, audience, metadata, accessibility, link check, schedule, cost, post-publish verification | services/publisher.py:14-24 | A card now previews platform/account/time/media/text for every publish (321ec490); still no accessibility or link-check step. |
| NS-24.10-2 | MUST | partial | Preserve source/prompt/edit/model lineage without exposing private sources | services/provenance.py:221-229 | No redaction policy for private sources in lineage. |

### §25 (13 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-25.2-1 | MUST | partial | Support principal types: owner, adult member, minor, guest, narrowly scoped service principal. | services/observer_access.py:55-71 | Only the read-only observer principal; adult member/minor/guest not implemented. |
| NS-25.3-3 | MUST | missing | The UI always shows the active principal. | ui_parts/app.html | No active-principal indicator (0 matches). |
| NS-25.4-1 | MUST | missing | Data shared only through explicit shared spaces (household calendar, grocery list, travel, projects, family docs, contacts, home automation). | src/agent_friday | No shared-space concept. |
| NS-25.6-2 | MUST NOT | missing | Ownership does not grant casual visibility into another adult principal's private content. | src/agent_friday | No adult-member principal, so no privacy-from-owner model. |
| NS-25.6-3 | MUST | missing | Administrative actions logged and visible to affected adult principals. | src/agent_friday | No principal admin actions or log. |
| NS-25.7-1 | MUST | partial | Minor mode is a first-class relationship, not a filtered version of the owner account. | core/__init__.py:2536 | minor_mode is still a single owner-account settings toggle. |
| NS-25.7-2 | MUST | partial | Minor mode: age-appropriate language and content boundaries. | services/creative_engine.py:298-343; services/creative_policy.py:152 | Generation filter only; no chat language boundary. |
| NS-25.7-6 | MUST | missing | Minor mode: spending prohibited by default. | services/spend_guard.py | No minor spending rule. |
| NS-25.7-7 | MUST | missing | Minor mode: time and device limits where configured. | src/agent_friday | No time or device limits. |
| NS-25.7-9 | MUST | missing | Minor mode: no manipulative attachment behavior; narrow documented safety escalation. | src/agent_friday | No anti-attachment behavior or safety-escalation spec/code. |
| NS-25.9-2 | MUST | partial | Sensitive judgments require clear provenance and user control. | people_graph.py | No per-judgment source provenance UI verified. |
| NS-25.10-1 | MUST | partial | Tools to locate, export and forget records about a person. | services/forget_person.py:262,344 | find() returns counts and paths, not an export of the records. |
| NS-25.11-1 | MUST | missing | Notifications, lock-screen previews, voice, ambient displays respect active principal and device privacy mode; sensitive content hidden when locked/other pri... | services/meeting_capture.py | No privacy_mode/lock-screen handling for notifications or voice. |

### §26 (27 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-26.2-1 | MUST | partial | Define eight trust zones (constitutional core, owner, principal, models, observed content, extensions, providers, audit store). | governance/action_gate.py; services/taint.py | No principal zone; receipts are per-entry HMAC with no chain (no prev_hash). |
| NS-26.5-1 | MUST | partial | Credential store encrypts every secret and prohibits plaintext fallback. | services/credential_store.py:153-209 | Plaintext fallback still returned at :209 outside OS mode. |
| NS-26.5-4 | MUST | partial | Record credential owner and connector; support rotation; expose last use without values. | services/credential_store.py:534,753 | No last-use tracking. |
| NS-26.5-5 | MUST | partial | Redact secrets from logs and error messages. | services/secret_shapes.py:93 | No logging.Filter redaction anywhere in tree. |
| NS-26.5-6 | MUST | partial | Fail closed when secure storage is unavailable. | services/credential_store.py:175-209 | Non-locked keystore failures still fall through to weaker tiers. |
| NS-26.6-1 | MUST | partial | Vault uses separate encryption and supports explicit lock and unlock. | privacy/vault_crypto.py; routes/insights.py:353 | "locked" is still derived; no explicit lock route. |
| NS-26.6-3 | MUST | missing | Require a fresh authenticated session for privileged vault reads. | core/__init__.py:3689-3741 | Frame/origin gates (20b97f9f) restrict who can call, but there is still no fresh-auth requirement for vault reads. |
| NS-26.6-5 | MUST | missing | Support per-principal vaults. | privacy/vault_access.py | Single vault; no per-principal vaults. |
| NS-26.6-6 | MUST | partial | File-level cloud grants only through explicit preview; avoid injecting vault material into general context. | services/file_grants.py:413; privacy/vault_policy.py | Preview step not verified; vault content still goes into local-model context. |
| NS-26.7-3 | MUST | partial | Gate performs destination validation, policy validation, payload-size checks and context manifest creation. | services/egress_gate.py:71 | No payload-size check or context manifest. |
| NS-26.9-1 | MUST | partial | Record provenance of consequential values across 12 source kinds (owner, other principal, live state, config, service, email, web, doc, screen, model, memory... | services/taint.py:223 | Only a "local_item" label was added (f6b296ee); still no other-principal, memory or extension kinds. |
| NS-26.11-1 | MUST | partial | Command/code execution sandboxed with scoped dirs, allowlists, resource/process limits, timeouts, env filtering, secret isolation, output caps, escalation, r... | services/code_sandbox.py | run_command still runs unsandboxed as owner; no OS-level network/filesystem allowlists. |
| NS-26.12-1 | MUST NOT | partial | File ops resolve canonical paths and verify containment; names, archives, symlinks, shortcuts, redirects must not escape allowed root. | paths.py:167; services/agent.py:944,1025 | read_file/write_file still accept any absolute path. |
| NS-26.13-1 | MUST | partial | Destination policy per connector, provider, skill and renderer; unexpected domains blocked or need approval. | services/extension_security.py; services/egress_gate.py:71 | No per-connector domain allowlist. |
| NS-26.14-1 | MUST | partial | Privileged service not directly public; remote access via paired clients, mutual auth, revocable device keys, least-privilege APIs, rate limits, replay prote... | services/origin_gate.py:90,218; core/__init__.py:3689-3741 | Host/origin/frame/session-token gates landed (0b519da3, c62ab0ca, ff38194d, 20b97f9f); still no pairing, mutual auth or per-device keys. |
| NS-26.15-1 | REQUIRED | partial | Updates need signed manifests/binaries, reproducible builds, SBOM, license review, migration tests, rollback, notes, known issues, green CI, staged rollout. | .github/workflows/installer.yml:61-68 | No signing, SBOM or reproducible-build evidence. |
| NS-26.16-1 | MUST | partial | Pin production dependencies through a lockfile. | uv.lock; requirements.txt | requirements.txt still uses >= ranges. |
| NS-26.16-2 | MUST | partial | Verify downloaded binaries and models; record model licenses. | packaging/windows/lib/Download.ps1:123-126 | An empty sha256 still passes (line 125). |
| NS-26.16-4 | MUST | partial | Generate provenance for release artifacts; sign tags and releases; retain build logs. | .github/workflows/installer.yml:61-68 | No signed provenance and no tag or release signing. |
| NS-26.17-1 | MUST NOT | partial | Logs must not contain credentials, vault content, never-send values, approval tokens, unneeded PII, secret URLs, full provider payloads. | services/credential_store.py; services/egress_gate.py:542 | No central log redaction filter. |
| NS-26.17-2 | MUST | partial | Diagnostics use correlation IDs; sensitive logs encrypted with retention limits. | core/__init__.py:2285,2392 | No correlation-ID scheme (only ad-hoc request_id in agent.py:9025). |
| NS-26.18-1 | MUST | partial | Public threat model describes assets, attackers, assumptions, deployment modes, controls, guarantees, limits, distribution differences, residual risks, incid... | docs/security/threat-model.md:288,361 | New section 7 (browser origin) added by 0cb2f263; still no incident-response section or enumerated assets/attackers. |
| NS-26.20-1 | MUST | partial | At startup and periodically verify policy signatures, keys, egress health, gate registration, receipt chain, credential store, update signatures, connector d... | services/egress_gate.py:1636; services/health_check.py | No receipt-chain, update-signature or domain-policy checks; not periodic. |
| NS-26.20-2 | MUST | partial | Critical failure enters safe mode: reads continue, outward actions held. | governance/action_gate.py; services/boot_guard.py:79 | No unified security safe mode. |
| NS-26.21-1 | MUST | partial | Collect only needed data; owner sets retention per category and principal; derived data removed/rebuilt when source removed. | core/__init__.py:2285; services/forget_person.py:344 | No per-principal retention; derived-data rebuild is not general. |
| NS-26.22-1 | MUST | partial | Privacy dashboard shows storage, encryption, vault access, cloud egress, grants, services/scopes, remote devices, background network, retention, off-record, ... | ui_parts/app.html | Still scattered; no recent-egress or remote-devices view. |
| NS-26.25-2 | MUST | missing | Must distinguish suspicion from confirmed compromise. | services; governance | No compromise or incident state. |

### §27 (8 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-27.1-1 | MUST | partial | Owner MUST be able to carry Friday's durable self to another supported device. | cli.py:1602,1662 | Export exists; no import or restore path. |
| NS-27.2-1 | REQUIRED | partial | Canonical portable export is an encrypted, versioned Friday Bundle of selectable categories (identity, principals, persona, memories, wiki, goals, receipts, ... | cli.py:1602-1660 | No category selection or bundle manifest/version; default export is unencrypted. |
| NS-27.3-2 | MUST NOT | partial | No proprietary database is the only path out. | cli.py:1602-1660 | Some stores still leave only as SQLite or encrypted blobs. |
| NS-27.6-2 | MUST | missing | After restore, outward actions stay held until device governance keys and remote channels are re-established. | cli.py | No restore flow, so no post-restore outward hold. |
| NS-27.7-1 | MUST | partial | Optional, provider-agnostic sync MUST be end-to-end encrypted so provider cannot read content. | routes/federation.py:19,175-201; services/federation_transport.py | E2E sync covers only _SYNC_SAFE_KEYS settings; no provider-agnostic data sync. |
| NS-27.7-2 | REQUIRED | partial | Sync supports multi-device, per-principal scope, conflicts, tombstones, causality, offline edits, receipt reconciliation, revocation, selective sync, bandwid... | routes/federation.py:175-219 | Conflicts, tombstones, causality, offline edits and receipt reconciliation are still missing. |
| NS-27.9-1 | REQUIRED | partial | Each device has unique identity and permissions (e.g. phone approval rights without vault access); revoked device loses sync/channel authority immediately. | phone/service.py:562,598 | No per-device identity or permission registry, and no revocation model. |
| NS-27.10-1 | REQUIRED | partial | Uninstall offers export first, app-only delete, models/caches delete, user data delete, revoke connectors/devices, remove schedules, certs/hosts, verify dele... | packaging/windows/uninstall.ps1:343-586 | No export-first offer; no connector or remote-device revocation. |

### §28 (10 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-28.1-1 | MUST | partial | Doctor is deterministic; critical diagnosis and repair MUST NOT depend on an LLM being available. | cli.py:833 cmd_status; services/boot_guard.py:461 restore_known_good | Diagnosis is still LLM-free; there is no repair framework. |
| NS-28.2-1 | REQUIRED | partial | Doctor checks all health domains (files, packages, settings schema, migrations, keys, vault, runtimes, models, GPU, connectors, egress/action gates, schedule... | cli.py:833-1100 cmd_status | cmd_status still has no checks for receipt chains, indexes, scheduler, browser lane, backups, GPU or the action gate. |
| NS-28.3-1 | REQUIRED | partial | Capabilities carry states: healthy, degraded, unavailable by config/hardware, misconfigured, blocked by policy, broken, repairing, unverified. | services/capability_state.py:43-54 | The vocabulary is still working/present_unverified/present_failing/unconfigured/absent; there is no blocked-by-policy, degraded or repairing state. |
| NS-28.3-2 | MUST | partial | UI MUST distinguish deliberate inactivity from failure. | routes/core_routes.py:1289 /api/capabilities/state | Still no reference to /api/capabilities/state in index.html or ui_parts. |
| NS-28.4-2 | MUST NOT | partial | Doctor MUST NOT silently reset personal data, persona, goals, governance, or permissions. | services/boot_guard.py:68 BLAST_RADIUS_FORBIDDEN; services/health_check.py:25 | There is still no Doctor, and the settings loader still reverts silently to factory defaults. |
| NS-28.5-1 | REQUIRED | partial | Safe mode: minimal local UI with models optional, connectors disabled, schedules paused, outward actions held, read-only inspection, backup/export, Doctor, l... | services/boot_guard.py:77 safe_mode; server.py:1172 | Safe mode still only disables self-modification and auto-restore; there is no reduced interface. |
| NS-28.5-2 | MUST | partial | Safe mode MUST remain available after failed migration or model-runtime failure. | services/boot_guard.py:77 | Safe mode is still only an env var or file switch, not a full safe-mode interface. |
| NS-28.7-1 | REQUIRED | partial | Update preview shows version, security fixes, features, known issues, migrations, permission/model changes, disk, restart, rollback, release verification. | services/update_check.py; cli.py:115 _version_truth | No release_notes/security/known_issues fields; the preview is still just the version and a link. |
| NS-28.8-1 | REQUIRED | partial | Migrations are versioned, idempotent, tested, preceded by backup, resumable, logged, reversible where feasible, integrity-checked. | services/cost_meter.py:473-476 ALTER TABLE if missing | No versioned migration framework, migration journal, pre-migration backup or integrity check found. |
| NS-28.9-1 | MUST NOT | partial | No silent replacement of a model that changes cost, destination, license, behavior or hardware; model updates get capability and persona evaluation. | routes/core_routes.py:1306 _check_seat_installed; services/persona_eval.py | persona_eval is still not wired in as a gate on model updates. |

### §29 (10 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-29.1-1 | REQUIRED | partial | Owner can always answer what/why/which model/what info/egress/authority/cost/what changed/verified/how to stop. | routes/tasks.py:715 /api/processes, :791 cancel | The pieces are still separate; the authority and context-used answers are not unified. |
| NS-29.2-1 | REQUIRED | partial | Every significant process registers id, task/goal ancestry, principal, label, category, model/provider, status, start, progress, step, cost, stop capability,... | core/__init__.py:1870 process_register | The signature still has no goal ancestry, principal, cost or receipt link. |
| NS-29.4-1 | REQUIRED | partial | Task journal records created, routed, context, tool, approval, action, verification, artifact, blocker, paused, resumed, stopped, receipt events. | services/task_journal.py:630 _emit, :766 _DIGEST_KINDS | Still no approval, verification, artifact, blocker or receipt event kinds. |
| NS-29.4-2 | REQUIRED | partial | Journal is encrypted, append-oriented, and linked to receipts. | services/task_journal.py | The journal is still not linked to receipts; the new action_journal rcpt_ ids are not tied to it. |
| NS-29.6-1 | REQUIRED | partial | Egress ledger records destination, purpose, context manifest ID, sensitivity, redactions, withheld count, grants, payload hash, time, model, result. | services/egress_gate.py:542 _log | The entry still has no purpose, manifest ID, payload hash, grants or withheld count. |
| NS-29.7-1 | REQUIRED | partial | Costs attributed to principal, provider, model, goal, task, workspace, scheduled job, tool/generation type, date. | services/cost_meter.py:456 cost_calls, :516 record | Costs still have no principal or goal attribution. |
| NS-29.8-1 | REQUIRED | partial | Ledger or Doctor shows receipt signature health, chain gaps, unsigned actions, missing terminal receipts, policy version, key state, update signature, backup... | routes/traces.py:91; routes/insights.py:729 /api/integrity/verify; governance/action_gate.py:844 verify_receipt | Still no single integrity dashboard; backup and update-signature checks are absent. |
| NS-29.9-1 | REQUIRED | partial | Owner corrections recorded as first-class events (claim, sources, correction, affected items, action, repair, regression-test flag). | routes/wiki.py:285 /api/wiki/correct | Corrections are still a text replace with no structured correction event. |
| NS-29.10-1 | MUST | partial | Status reports what actually works, not inferred from config (cached account is not connected, declared model is not loaded, queued is not sent). | services/capability_state.py; services/tool_receipts.py:444 correction_note | The rule is enforced in several places, but not on every status surface. |
| NS-29.11-1 | MUST NOT | partial | Forensic snapshots respect off-record, principal isolation, retention; no shadow archive of private content. | ops/forensics-snapshot.py:269 off_record skip | Still no principal isolation; snapshots still copy friday.log and settings. |

### §30 (29 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-30.1-1 | MUST | partial | Canonical data model is principal-aware | core/__init__.py:3703,3713 g.friday_principal | Principal is still only 'user' or 'observer'; records carry no principal_id. |
| NS-30.1-2 | MUST | partial | Data model is provenance-aware | services/approvals.py:416 provenance; services/provenance.py | Provenance is still per subsystem, with no canonical source_refs model. |
| NS-30.1-3 | MUST | partial | Data model is time-aware | services/approvals.py:403-423; services/clock.py | Still no valid_from/valid_until on relationships. |
| NS-30.1-4 | MUST | partial | Data model is sensitivity-aware | services/egress_gate.py; services/sensitivity_classifier.py | Records still have no sensitivity field of their own. |
| NS-30.1-5 | MUST | partial | Data model is versioned | routing/provider_descriptors.py:80,574; services/health_check.py:99 | Approvals, grants and decision-bom receipts are still not versioned. |
| NS-30.1-6 | MUST | partial | Data model is exportable | services/data_export.py | Export only; no import or restore found. |
| NS-30.1-8 | MUST | partial | Data model is auditable | governance/action_gate.py:822 _receipt (HMAC); services/activity_ledger.py | Auditing is per ledger, not unified. |
| NS-30.1-9 | MUST | partial | Usable without reconstructing truth from prose | services/goals.py; services/approvals.py; services/task_journal.py | Still JSON stores per subsystem. |
| NS-30.1-10 | MUST | partial | IDs are stable UUIDs; names are labels not keys | services/approvals.py:403 appr_+hex[:10]; governance/action_gate.py:769 grant_+hex[:10] | IDs are still truncated uuid4 hex, not full UUIDs. |
| NS-30.3-1 | MUST | missing | Device record with owner, platform, public key, capabilities, permissions, trusted/limited/revoked status | grep trusted_devices/device_registry/decision_device_id: 0 hits | There is still no device record. |
| NS-30.4-1 | MUST | partial | Source record: kind, locator, principal scope, hash, observed/published, sensitivity, trust dims, retention | source_trust_graph.py; services/retrieval_ledger.py | Still no unified source record. |
| NS-30.5-1 | MUST | partial | Entity (typed, aliases, source_refs, active/merged/forgotten) and relationship (predicate, validity window, confidence) records | services/knowledge_graph/; services/relationship_memory.py | No relationship validity window or canonical entity status verified. |
| NS-30.6-1 | MUST | partial | Conversation record: principal, project, title, active goal, retention, off_record flag | services/conversations.py:85 _blank | _blank still has no off_record, principal, goal or retention fields. |
| NS-30.6-2 | MUST | partial | Message record: role, encrypted content, source_refs, model_route_id, superseded | services/conversations.py | Still no model_route_id or source_refs (grep: 0 hits). |
| NS-30.7-1 | MUST | partial | Memory uses §15.2 schema referencing source, entity, decision, goal, principal | cognitive_memory.py | Memory still has no principal or goal refs. |
| NS-30.8-1 | MUST | partial | Milestone: goal_id, completion_condition, evidence reqs, dependencies, authority override, status planned..verified/failed, due_at | services/goals.py:340 _normalize_milestone | Milestones still have no dependencies or evidence_requirements. |
| NS-30.9-1 | MUST | partial | Task record with origin, intent, state, budget, model policy, verification plan, terminal receipt | services/task_journal.py; services/task_ledger.py | Still no terminal_receipt_id (grep: 0 hits). |
| NS-30.9-2 | MUST | partial | Step record: kind, state, input/output refs, distinct_progress_key, timing | routes/tasks.py:379,435 seq/Last-Event-ID | Steps still have no distinct_progress_key or input/output refs. |
| NS-30.10-1 | MUST | partial | Action requests use §18.1 envelope referencing tool, approval, grant, decision and delivery receipts | governance/action_gate.py:894 authorize, :1003 authorize_external | Still no §18.1 action envelope or delivery receipt; f6b296ee added rcpt_ receipts in services/action_journal.py only for organize actions. |
| NS-30.11-1 | MUST | partial | Approval record: action_fingerprint sha256, encrypted preview, provenance, statuses incl revoked/consumed, expiry, decision device | services/approvals.py:92 STATUSES, :403-423 | STATUSES still has no revoked and there is no decision_device_id; the fingerprint exists only in authorize_external. |
| NS-30.12-1 | MUST | partial | Grant: subject kind/id, tools, arg constraints, data/destination scope, max uses, used count, money cap, validity, status | governance/action_gate.py:758-772 create_grant | Grants still have no argument, data or destination constraints and no money cap. |
| NS-30.13-1 | MUST | partial | Model route record: seat, provider, model, local, routing reason, context manifest, egress event, tokens, cost, latency | services/activity_ledger.py:31 model_invocation; services/cost_meter.py:516 | Still no context_manifest or routing_reason ids (grep: 0 hits). |
| NS-30.14-1 | MUST | partial | Artifact record: kind, locator, version, sha256, sensitivity, source refs, provenance manifest, qa_status | services/provenance.py; services/qa_gates.py | Still no unified artifact record with version and qa_status. |
| NS-30.15-1 | MUST | partial | Receipt: kind, principal, related ids, payload hash, previous_hash chain, signature, validity status | governance/action_gate.py:822-836 _receipt (HMAC, no prev_hash); services/action_journal.py:41 rcpt_ | Decision receipts are still not hash-chained. |
| NS-30.16-1 | MUST | partial | Triggers use §19.3 schema referencing tasks, goals, source events, receipts | services/scheduler.py | Still no trigger engine following the §19.3 schema. |
| NS-30.17-1 | MUST | partial | Skill record: semver, author, signature, manifest hash, permissions, tools, data scopes, test report, installed/disabled/quarantined/removed | skill_registry.py:151 _skill_from_manifest | Skills still have no status, quarantine or manifest_hash (grep: 0 hits). |
| NS-30.18-1 | MUST | partial | Workspaces use §21.20 manifest with version history | services/workspace_studio.py; routes/workspace_undo.py | adca94d5 added a naming registry for workspaces, not a §21.20 manifest with version history. |
| NS-30.19-1 | MUST | missing | Every action, grant, approval, model route, receipt references governing policy version | services/residency_policy.py:1524 (only policy_version hit) | policy_version is still not in action_gate._receipt or in approval records. |
| NS-30.20-1 | MUST | partial | Tombstones prevent silent re-creation of deleted entities from derived indexes, with minimal PII | services/forget_person.py; services/relationship_memory.py | Tombstones still cover people only, not entities in general. |

### §31 (20 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-31.1-1 | MUST | partial | Internal/local APIs authenticated | core/__init__.py:3689 check_auth, :3622 _origin_gate_reason; services/origin_gate.py | Since c62ab0ca/ff38194d, browser writes need the session token plus Host/Origin checks; non-browser loopback callers stay tokenless by design. |
| NS-31.1-2 | MUST | partial | APIs principal-scoped | core/__init__.py:3703 observer principal | There is still no per-principal data scoping. |
| NS-31.1-3 | MUST | missing | APIs typed and versioned | routes/_errors.py:61 _ENVELOPES; grep /api/vN: 0 hits | APIs are still unversioned and response shapes still vary. |
| NS-31.1-4 | MUST | partial | Idempotent where appropriate and safe under retry | services/approvals.py consume-once; services/task_resume.py | Still no general idempotency keys. |
| NS-31.1-5 | MUST | partial | Explicit about read/write behavior | services/observer_access.py is_read_allowed; governance/action_gate.py | Read/write behavior is still not declared systematically. |
| NS-31.1-6 | MUST | partial | Bounded in output | services/agent.py:8864 _TOOL_RESULT_MAX=8192 | Only tool results are capped. |
| NS-31.1-7 | MUST | partial | Observable | services/activity_ledger.py; routes/tasks.py:379-435 SSE | Unchanged. |
| NS-31.1-8 | MUST | partial | APIs governed by the same action path as model tool calls | services/publisher.py:753 authorize_external; services/item_actions.py | 321ec490 gated content publish; routes/calendar.py Quick Add and the workspace_studio writes still do not use action_gate. |
| NS-31.1-9 | MUST NOT | partial | UI gets no privileged behavior unavailable to tool governance path | routes/calendar.py (no action_gate); services/file_grants.py | Other UI write routes are still ungoverned. |
| NS-31.2-1 | MUST | partial | Read and write operations use separate endpoints or explicit action declarations | routes/updates.py; services/observer_access.py | Still no systematic read/write declaration. |
| NS-31.2-2 | MUST NOT | partial | Read endpoint must not write as side effect (except access logging) | routes/updates.py docstring | No enforcement or test that reads have no side effects. |
| NS-31.3-1 | MUST | partial | Errors return stable code, plain message, correlation id, retryable, suggested action, details-local flag | routes/_errors.py:68 api_error (error_id) | Still no stable code, retryable or suggested_action; correlation_id: 0 hits. |
| NS-31.4-1 | MUST | partial | Outward/state-changing APIs accept idempotency key; repeats return prior result or conflict | services/approvals.py create_approval dedupe; services/platforms/mastodon.py:912 Idempotency-Key | Still no client-supplied idempotency key on Friday's own APIs. |
| NS-31.5-1 | MUST | partial | No client calls connector write endpoint directly; actions go through canonical gate | governance/action_gate.py:1003 authorize_external; services/publisher.py:753 | Publisher now goes through the gate; calendar and workspace UI writes still bypass it. |
| NS-31.5-2 | MUST | partial | Gate returns executed/approval required/denied/forbidden/waiting/failed/completed-with-verification | governance/action_gate.py:867 Verdict allow/confirm/card/deny | Still no waiting or verified-completion outcomes. |
| NS-31.6-1 | MUST | missing | Canonical event envelope: id, type, version, principal scope, related ids, payload, source, time, integrity | services/task_journal.py:630 _emit; desktop_bus.py | Still no canonical event envelope or bus. |
| NS-31.7-1 | MUST | partial | Critical events (approvals, outward actions, receipts, goal completion, grants, principal changes, backup, update, security) durably persisted before notific... | governance/action_gate.py:822 _receipt before allow; services/action_journal.py | Persistence before notification is still unverified for backup, update, principal and security events. |
| NS-31.8-2 | MUST | partial | Event replay respects principal isolation and deletion tombstones | services/forget_person.py; services/relationship_memory.py | Replay still has no principal isolation. |
| NS-31.11-1 | MUST | partial | Streams support message IDs, reconnect, cursor replay, cancel, backpressure, terminal status, principal scope, bounded buffers | routes/tasks.py:379,435 Last-Event-ID | Streams still have no backpressure or principal scope. |
| NS-31.12-1 | MUST | partial | File APIs use handles/grants not arbitrary paths; grant records principal, root, ops, expiry, purpose | services/file_grants.py; services/agent.py read_file schema | read_file still takes arbitrary paths. |

### §32 (21 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-32.1-1 | MUST | partial | Degrade explicitly; never hide missing capability, change provider, drop context or fabricate completion | services/agent.py:434-473 ladder fallback | The ladder still falls back to other providers automatically. |
| NS-32.2-4 | MUST | partial | Doctor offers repair when local model down | cli.py:14,833 friday doctor | Still no in-app Doctor offering repair. |
| NS-32.2-5 | MUST NOT | partial | No silent cloud fallback | services/agent.py:457-473 ladder_fallback/note_fallback | Fallback is still automatic; it is attributed after the fact, not held. |
| NS-32.3-1 | MUST | partial | Cloud provider failure: provider-specific safe retry; substitution follows policy | services/agent.py:434-441 _mode_filtered_attempts/_health_order | Unchanged. |
| NS-32.3-2 | MUST | partial | Action state unchanged until verified; costs and failed attempts recorded; concise explanation | services/agent.py:462 journal decision; services/cost_meter.py:516 | Unchanged. |
| NS-32.5-1 | MUST | partial | Memory unavailable: conversation continues, recall-degraded disclosure, no memory claims as fact, bounded queued writes, Doctor repair | services/introspection.py | Still no recall-degraded disclosure (grep: 0 hits). |
| NS-32.6-1 | MUST | partial | Corrupt knowledge index: wiki/sources remain, derived indexes rebuilt, no source deletion | services/forget_person.py | Still no index corruption detection. |
| NS-32.7-1 | MUST | partial | Expired connector auth reported as disconnected; cached state not proof; dependent tasks enter external_wait/needs_user_input | services/connector_health.py | external_wait/needs_user_input blockers are still only in the spec. |
| NS-32.8-1 | MUST | partial | Expired/denied approval: action doesn't run, typed blocker, token never reused | governance/action_gate.py:1003 authorize_external | The typed blocker is still only proposed. |
| NS-32.10-1 | MUST | partial | Multi-step partial action: record last verified boundary, compensate only if pre-authorized, else pause and explain | services/task_resume.py | Still no compensation mechanism. |
| NS-32.11-1 | MUST | partial | Crash recovery: identify interrupted tasks, reconstruct checkpoint, query state, avoid non-idempotent replay, recovery receipt, policy-gated resume, report | services/task_resume.py; services/reconcile.py | The recovery receipt is still only in the spec. |
| NS-32.12-1 | MUST | partial | Crash during update/migration: safe mode, restore prior version or resume migration journal, data backed up | services/boot_guard.py:77,461 | Still no migration journal. |
| NS-32.13-1 | MUST | partial | Disk full: stop nonessential writes, pause background, preserve receipts, warn, offer cache cleanup, never auto-delete personal data | services/arbiter.py:81,273 disk_headroom; services/residency_policy.py:1552 | Still no runtime disk-full handler that pauses background work. |
| NS-32.14-1 | MUST | partial | GPU exhaustion: release optional seats, pause creative, CPU fallback, smaller model or permitted cloud; desktop responsive | services/gpu_headroom.py; services/stand_down.py; routes/tasks.py:791 | Unchanged. |
| NS-32.15-1 | MUST | partial | Network down: local features continue, network tasks queue/fail per policy, offline distinguished from provider failure | core/__init__.py:3321 _network_is_offline (predates 09-29; used at agent.py:2175) | The offline check exists but is not surfaced as a user-facing offline state; no policy for queueing network tasks. |
| NS-32.16-1 | MUST | partial | Use authoritative principal timezone; scheduled actions store timezone and DST behavior explicitly | services/scheduler.py:159-162 _now_central America/Chicago | Schedules still have no per-schedule timezone or DST field. |
| NS-32.18-1 | MUST | partial | Unknown live state: say unverifiable, offer read action, never answer from old memory | services/live_state.py:5 | Offering a read action is still unverified. |
| NS-32.19-1 | MUST | partial | Compromised remote channel: revoke device/channel from any trusted device; rate limits and safe mode | services/channels/manager.py:163 stop_channel; services/observer_access.py:71 revoke | Still no device registry, channel rate limits or remote safe mode. |
| NS-32.20-1 | MUST | partial | Corrupt receipt chain: hold high-risk actions, preserve evidence, report first invalid link, never rewrite history | privacy/vault_crypto.py:207 verify_bom_file | Receipts are still not hash-chained, with no hold on corruption. |
| NS-32.21-1 | MUST | partial | Malformed tool call: validate schema, reject, correct once, record, never execute guessed arguments | services/agent.py:8775 '(unambiguous match)' auto-resolve | Misnamed tools are still auto-resolved and run; no argument schema validation found. |
| NS-32.22-1 | MUST | partial | Cancellation: stop new steps, halt safely, release resources, report effects already taken | routes/tasks.py:544 stop-after-step, :791 cancel_process | Unchanged. |

### §33 (40 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-33.1-1 | MUST | partial | Complete flat interface meets WCAG 2.2 AA overall | .github/workflows/tests.yml:50,53; tests/test_ui_audit.py:266 | No conformance audit; test_ui_audit.py is still not in CI; 53b89271 adds a WCAG 2.3.1 no-flash test for the scene only. |
| NS-33.1-2 | MUST | partial | Keyboard navigation | ui_parts/app.html (51 keydown/onKeyDown lines) | Still no skip link (0 hits for skip-link/"Skip to"). |
| NS-33.1-3 | MUST | missing | Logical focus order | tests/ (no focus-order test) | No focus-order or tabindex audit test; only 6 tabindex lines in index.html. |
| NS-33.1-4 | MUST | partial | Visible focus | ui_parts/head.html (11 :focus-visible lines); index.html (11) | Focus-visible rules cover selected classes only; no global rule or test. |
| NS-33.1-5 | MUST | partial | Screen-reader labels | index.html (189 aria-label lines, 8 aria-live); tests/test_ui_audit.py:280 | Accessible-name check exists but is not in CI; no screen-reader pass. |
| NS-33.1-6 | MUST | partial | Semantic headings | ui_parts/app.html:6058,6492 (div.settings-subtitle) | Still only 9 <h1>/<h2> lines in app.html and 1 in index.html; settings sections are divs, not headings. |
| NS-33.1-7 | MUST | partial | High contrast | tests/test_ui_audit.py:246-266 | No high-contrast theme; 0 hits for forced-colors/prefers-contrast; contrast audit not in CI. |
| NS-33.1-8 | MUST | partial | Scalable text | ui_parts/head.html:5 | No in-app text-size setting; no test that the layout holds under zoom. |
| NS-33.1-9 | MUST | partial | Reduced motion | ui_parts/styles_and_scene.html:984-996 SceneMotion; ui_parts/head.html:1025 | The scene honours reduced motion (22158c60), but head.html has 32 @keyframes and only a few reduce rules; no global rule. |
| NS-33.1-10 | MUST | shipped | Captions and transcripts | ui_parts/app.html:1660 (captions track); routes/podcasts.py:144 captions.vtt | Closed by podcast timed captions (bad0e4d7) plus voice transcripts in chat (app.html:864); no audit of phone-call audio. |
| NS-33.1-11 | MUST | missing | Color-independent state communication | tests/ (none) | Still no audit or test for colour-independent state. |
| NS-33.1-12 | MUST | missing | Accessible charts and graphs | static/studio_files3d.js (no aria/role) | 0 hits for role="img"/aria-describedby; 3D graphs and charts have no text alternative. |
| NS-33.1-13 | MUST | partial | Large touch targets | ui_parts/head.html:1206; ui_parts/styles_and_scene.html:50 | The 44px minimum covers only the narrow dock and the workspace tab header; no global rule or audit. |
| NS-33.1-14 | MUST | missing | No essential drag-only interaction | ui_parts/app.html (5 draggable lines) | No audit of non-drag alternatives; no test. |
| NS-33.2-1 | MUST | partial | All ambient, holographic, avatar and graph motion respects reduced-motion | ui_parts/styles_and_scene.html:984-1010; tests/unit/test_scene_motion_safety.py:92 | Scene flash, zoom and time lapse now gated; the spin continues at speech gain 0.5, and the other CSS keyframes are not covered. |
| NS-33.3-1 | MUST | missing | Voice supports slower speech setting | src/agent_friday (0 hits) | No speech_rate/tts_speed/speaking_rate setting. |
| NS-33.3-2 | MUST | missing | Voice supports repetition | phone/live_call.py:319 | Still no "say that again"/replay-last-utterance command or button. |
| NS-33.3-3 | MUST | partial | Voice confirmation settings | core/__init__.py:2188; services/setup_chat.py:706 | New voice_room_approvals_require_name setup choice (94e66c0d) and conditional-yes rejection (c23fdc54); still no general voice confirmation-level setting. |
| NS-33.3-6 | MUST | partial | Critical information available visually and audibly | ui_parts/app.html:864 | Audible announcement of approval cards still not verified. |
| NS-33.5-2 | MUST | partial | UI-only while services initialize | services/health_check.py:311,353 | boot_status is ok/degraded/failed; there is no explicit UI-only mode while services start. |
| NS-33.5-3 | MUST | partial | Safe mode | services/boot_guard.py:26,79 | FRIDAY_SAFE_MODE is still only a self-modification kill switch; no minimal-interface safe mode. |
| NS-33.5-4 | MUST | partial | Headless mode | core/os_mode.py:9,53 | Kiosk/headless gating only; no general headless CLI flag. |
| NS-33.5-5 | MUST | partial | Offline mode | services/cloud_voice.py:413; core/__init__.py:3348 | local_only covers model/voice egress only; no full offline startup mode for connectors/news; no startup test. |
| NS-33.5-6 | MUST | partial | Diagnostic mode | cli.py:1434,1438 | doctor/status/check/health exist; no diagnostic startup mode. |
| NS-33.6-1 | MUST | missing | >=99% verified action success for supported connectors | src/agent_friday (only skill_capture.py:258 success_rate) | No per-connector verified-action success-rate measurement. |
| NS-33.6-2 | MUST | partial | Zero duplicate outward actions from retry in acceptance tests | tests/unit/test_retry_scope_isolation.py | Not all outward actions are covered by duplicate-on-retry acceptance tests. |
| NS-33.6-3 | MUST | partial | Zero missing terminal receipts | tests/unit/test_completion_receipts.py; tests/unit/test_receipts_match_the_action.py | No all-paths terminal-receipt test. |
| NS-33.6-4 | MUST | partial | Zero cross-principal leakage | tests/api/test_observer_reads_are_sealed.py | Observer principal only; no multi-principal model. |
| NS-33.6-5 | MUST | partial | Zero silent provider substitution | tests/gauntlet/test_cloud_voice_no_silent_fallback.py | unverified: whether the cloud-voice-to-Piper silent fallback still exists was not re-traced. |
| NS-33.6-6 | MUST | partial | Zero unannounced cloud egress | tests/test_egress_adversarial.py; services/egress_gate.py:1636 | Egress gate and self-test exist; no proof that every path announces egress. |
| NS-33.6-7 | MUST | partial | >=99% clean rollback success in tested upgrade paths | tests/unit/test_boot_guard_rollback.py; .github/workflows/installer.yml:94 | The installer upgrade job tests upgrade, not rollback; no rollback success rate. |
| NS-33.6-8 | MUST | partial | Crash-free sessions measured via opt-in diagnostics and test infra | tests/unit/test_crash_forensics.py; services/crash_forensics.py | No crash-free session rate computed. |
| NS-33.7-1 | MUST | partial | Every background job declares CPU/GPU/RAM/disk/network/cost/time budgets | services/budget_enforcer.py; services/residency_policy.py:432 | No per-job declaration of CPU/GPU/RAM/disk/network/cost/time; cost and model RAM only. |
| NS-33.7-2 | MUST | partial | Jobs yield to active user work per owner policy | services/seat_supervisor.py:1; tests/unit/test_idle_work_trigger.py | Idle trigger and seat queue only; no owner policy for yielding to active user work. |
| NS-33.8-1 | MUST | missing | Detect battery and thermal state; pause/shift heavy work per policy | src/agent_friday (0 hits sensors_battery/thermal) | No battery or thermal detection. |
| NS-33.9-1 | MUST | partial | Architecture supports localized UI, date/time, currency, units, speech | ui_parts/app.html (63 toLocale/Intl lines) | No string i18n framework. |
| NS-33.9-2 | MUST | missing | Personality and memory stay principal-specific across languages | src/agent_friday (no principal or multi-language persona) | No principal-specific, cross-language personality or memory. |
| NS-33.11-1 | MUST | partial | Supported configurations explicitly tested; unsupported shown as community mode | .github/workflows/installer.yml:9-11,94 | No support matrix or community-mode labelling. |
| NS-33.12-1 | MUST | partial | Every user-facing capability documented (purpose, setup, permissions, privacy, cost, examples, limits, troubleshooting, revocation, version) | docs/user-guide/ (18 files) | No per-capability documentation template covering all required fields. |
| NS-33.12-2 | MUST | partial | Docs state when code is authoritative / docs may be stale | docs/README.md:59; 60 docs carry "Last verified" | Staleness markers are not universal. |

### §34 (88 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-34.1-1 | MUST | partial | Stable release requires evidence of install, use, update, restore, governance on representative machines | .github/workflows/installer.yml:71,94 | Has fresh-install and upgrade evidence only; still no restore or governance evidence and no representative-machine matrix |
| NS-34.2-3 | REQUIRED | missing | Green integration tests gate | .github/workflows/tests.yml:50 | tests.yml runs only tests/unit and tests/api; tests/integration is not run in CI |
| NS-34.2-5 | REQUIRED | partial | Principal-isolation tests gate | tests/api/test_observer_reads_are_sealed.py | Still no dedicated principal-isolation suite or CI step |
| NS-34.2-8 | REQUIRED | partial | Update and rollback tests gate | .github/workflows/installer.yml:114; tests/unit/test_boot_guard_rollback.py | Rollback is covered only by a unit test; there is no CI rollback job |
| NS-34.2-10 | REQUIRED | partial | Dependency review gate | .github/dependabot.yml | There is no dependency-review workflow in .github/workflows |
| NS-34.2-12 | REQUIRED | partial | Documentation link and example tests | .github/workflows/tests.yml:79-80 | Doc links are checked; doc examples are not executed |
| NS-34.2-13 | REQUIRED | partial | Settings schema tests | .github/workflows/tests.yml:75-76 | Only the settings-readers check runs; no test validates settings against a schema |
| NS-34.2-14 | REQUIRED | shipped | Action-gate completeness tests | tests/unit/test_every_action_is_governed.py:81,203 | Tests find every registered tool and executor path, assert each passes the gate first, and include a self-test with a planted bypass; they run in the tests/unit CI step |
| NS-34.2-15 | REQUIRED | partial | Receipt completeness tests | tests/unit/test_receipts_match_the_action.py; tests/unit/test_completion_receipts.py | No test checks that every outward action produces a receipt |
| NS-34.2-16 | REQUIRED | partial | Model and capability registry validation | .github/workflows/tests.yml:77-78 | Only stale model names are checked; capability-registry validation is missing |
| NS-34.2-17 | REQUIRED | partial | License and SBOM generation | THIRD_PARTY_LICENSES.md | No SBOM tooling in scripts/, packaging/ or .github/ (grep for sbom/cyclonedx finds nothing) |
| NS-34.2-18 | MUST NOT | missing | Security suites not skipped when earlier general tests fail; report independently | .github/workflows/tests.yml:49-53 | The security suite step still comes after unit+API in the same job with no if: always() or separate job |
| NS-34.3-1 | MUST | partial | Clean-machine install on supported Windows versions | .github/workflows/installer.yml:73 | Only windows-latest; no matrix of Windows versions |
| NS-34.3-2 | MUST | missing | Clean-machine: minimum RAM | .github/workflows/installer.yml:73 | No minimum-RAM runner |
| NS-34.3-3 | MUST | partial | Clean-machine: no GPU / low / mid / high-tier GPU | .github/workflows/installer.yml:73 | No-GPU runs only; no low, mid or high GPU-tier runs |
| NS-34.3-4 | MUST | missing | Clean-machine: non-English locale | .github/workflows/installer.yml | No locale variation |
| NS-34.3-5 | MUST | missing | Clean-machine: restricted user account | .github/workflows/installer.yml | No restricted-user-account job |
| NS-34.3-6 | MUST | partial | Upgrade from oldest supported and immediately prior version | .github/workflows/installer.yml:36,108 | Upgrades only from PREVIOUS_RELEASE (default v5.13.0); no oldest-supported upgrade |
| NS-34.3-7 | MUST | missing | Restore from portable bundle | .github/workflows/installer.yml | No restore-from-bundle job |
| NS-34.3-8 | MUST | partial | Results recorded in release evidence | .github/workflows/installer.yml:85,114 | Evidence artifacts are only for fresh install and upgrade; no wider release-evidence record |
| NS-34.4-1 | MUST | missing | First-run: installer signature verified | .github/workflows/installer.yml | No signtool or Authenticode anywhere in packaging/ or .github/; SHA-256 only |
| NS-34.4-2 | MUST | missing | First-run: preflight accuracy | packaging/windows/tests/verify-clean-install.ps1 | No preflight-accuracy check |
| NS-34.4-3 | MUST | partial | First-run: secure key creation | packaging/windows/tests/verify-clean-install.ps1:111; packaging/windows/tests/rehearsal/upgrade-vault-test.ps1 | Only consent order and vault creation are checked; key-creation security is not verified |
| NS-34.4-5 | MUST | missing | First-run: local-model download and checksum | packaging/windows/tests/verify-clean-install.ps1:161 | Only the officecli hash is checked; no local-model download or checksum check |
| NS-34.4-6 | MUST | missing | First-run: cloud-key setup through secure fields | packaging/windows/tests/verify-clean-install.ps1 | No cloud-key secure-field check |
| NS-34.4-7 | MUST | partial | First-run: connector scope display | packaging/windows/tests/verify-clean-install.ps1 | Services are listed; connector scopes are not checked |
| NS-34.4-8 | MUST | missing | First-run: first synthesis, first action+receipt, first goal | packaging/windows/tests/verify-clean-install.ps1 | No check for first synthesis, first action plus receipt, or first goal |
| NS-34.4-9 | MUST | missing | First-run: backup setup | packaging/windows/tests/verify-clean-install.ps1 | No backup-setup check |
| NS-34.5-1 | MUST | shipped | Every registered tool reaches the action gate (test) | tests/unit/test_every_action_is_governed.py:81,239 | Runtime test sends every CLAUDE_TOOL_HANDLERS entry, including connector tools, through _execute_tool and checks the gate sees it first; file predates 09-29, so the survey missed it |
| NS-34.5-2 | MUST | shipped | Direct handler call sites cannot bypass gate | tests/unit/test_every_action_is_governed.py:203,213 | AST source scan of src/ fails on any direct handler or connector call outside reviewed sites; has a planted-bypass self-test |
| NS-34.5-3 | MUST | partial | Unknown tools are conservative | tests/unit/test_every_action_is_governed.py:127 | Asserts every native tool has a class; no direct test that an unknown tool name is treated as outward |
| NS-34.5-4 | MUST | partial | Approvals bind exact arguments | tests/unit/test_content_publish_gate.py:123,190 | Exact-text binding is tested only for content publish; no general argument-hash binding test for approvals |
| NS-34.5-5 | MUST | partial | Tokens cannot replay | tests/unit/test_approval_executes.py:422 | Replay tests cover only already-decided cards and egress envelopes; no general token-replay suite |
| NS-34.5-6 | MUST | partial | Grants expire and count usage | tests/unit/test_file_grants.py; tests/unit/test_every_action_is_governed.py:239 | No single test of expiry plus use counting across all grant kinds |
| NS-34.5-8 | MUST | shipped | Receipt failure holds action | tests/unit/test_every_action_is_governed.py:278; tests/unit/test_ring_check_receipt.py:47 | Tests show a receipt write failure (OSError) holds the outward action and writes nothing unsigned |
| NS-34.5-9 | MUST | partial | STOP works while model loop is wedged | tests/unit/test_hang_watchdog.py | Still no global STOP test while the model loop is wedged |
| NS-34.5-10 | MUST | partial | Principal policy enforced | tests/api/test_observer_reads_are_sealed.py | Only the observer principal on task routes is tested |
| NS-34.5-11 | MUST | shipped | Constitution drift holds outward actions | tests/unit/test_every_action_is_governed.py:270-275 | Test tampers the cLaws pin and asserts a background outward action is held |
| NS-34.6-4 | MUST | partial | Egress tests: vault content | tests/security/test_kg_egress_adversarial.py | No end-to-end test of vault content across all egress paths |
| NS-34.6-5 | MUST | partial | Egress tests: file grants | tests/unit/test_egress_grant_origin.py | Only file-grant origin is covered |
| NS-34.6-6 | MUST | shipped | Egress tests: provider echo | tests/unit/test_egress_grant_origin.py:85,96 | Provider-echo tests exist: replay is attributed and an echo does not cross providers (file dated 08-25, so the 09-29 survey missed it) |
| NS-34.6-7 | MUST | partial | Egress tests: partial classifier failure / loading classifier | tests/security/test_egress_gate_adversarial.py:257 | A classifier crash fails closed; no test for a partial failure or a classifier that is still loading |
| NS-34.6-10 | MUST | partial | Egress tests: connector tool results | tests/unit/test_egress_tool_result_provenance.py | Not checked per connector |
| NS-34.6-11 | MUST | partial | Egress tests: voice transcripts | tests/gauntlet/test_cloud_voice_egress_and_cost.py | Only cloud voice is covered; gauntlet tests are not in CI |
| NS-34.6-12 | MUST | partial | Egress tests: images and image metadata | tests/unit/test_media_tools_egress.py:28,76 | The inspect_image question is gated and image bytes are recorded; no image-metadata (EXIF) egress test |
| NS-34.6-13 | MUST | partial | Egress tests: remote model substitution | tests/gauntlet/test_chat_send_regates_for_predicted_provider.py | Gauntlet tests are not run in CI |
| NS-34.6-14 | MUST | partial | Egress tests: all supported providers | tests/test_egress_adversarial.py | Only anthropic, openai and gemini; not every supported provider |
| NS-34.7-1 | MUST | partial | Injection fixtures: email, HTML, PDF, images, calendar, spreadsheet, repo files, tool output, channel msgs, webhooks, screen text, model summaries | tests/unit/test_ingested_text_cannot_grant_authority.py | No per-medium injection fixture set |
| NS-34.7-5 | MUST | partial | Tests verify content cannot escape sandboxes | tests/unit/test_open_path_allow_list.py; tests/unit/test_office_safety.py | No test across all sandboxes |
| NS-34.8-1 | MUST | partial | Principal-isolation tests across every store and surface; release blockers | tests/api/test_observer_reads_are_sealed.py | Only the observer on task routes; not every store and surface |
| NS-34.9-1 | MUST | partial | Memory tests: cross-session recall | tests/unit/test_conversation_memory.py | No end-to-end cross-session recall test |
| NS-34.9-2 | MUST | partial | Memory tests: source citation / provenance | tests/unit/test_response_provenance.py | No end-to-end citation test on recalled memory |
| NS-34.9-3 | MUST | partial | Memory tests: current-state override and freshness | tests/unit/test_a_stale_failure_is_not_a_current_verdict.py | No general memory freshness or override test |
| NS-34.9-4 | MUST | partial | Memory tests: correction and supersession | tests/api/test_memory_supersede.py:43,53 | Supersede is tested; no full correction-flow test |
| NS-34.9-7 | MUST | missing | Memory tests: import staging | tests/ | No import-staging test found |
| NS-34.9-8 | MUST | partial | Memory tests: inferred vs confirmed state | tests/unit/test_memory_proposals.py | Inferred vs confirmed is tested only through proposals |
| NS-34.9-9 | MUST | partial | Memory tests: index rebuild | tests/unit/test_kg_indexer.py | Only the KG indexer; no full index-rebuild test |
| NS-34.9-10 | MUST | missing | Memory tests: cross-principal isolation | tests/ | No multi-principal memory test |
| NS-34.10-2 | MUST | partial | Goal tests: typed blockers, continuation preconditions | tests/unit/test_goal_budget_cap.py:52,67 | Only the budget-cap blocked milestone is tested; no typed-blocker taxonomy or continuation-precondition tests |
| NS-34.10-3 | MUST | partial | Goal tests: no-progress detection | tests/unit/test_task_watchdog.py | Watchdogs only; no goal-level no-progress test |
| NS-34.10-6 | MUST | partial | Goal tests: plan revision, approval waiting | tests/unit/test_goal_verification_repair.py:261,297 | Waiting on an escalation approval is tested; plan revision is not |
| NS-34.10-8 | MUST | missing | Goal tests: archive and reopen | tests/unit/test_goals_state_machine.py | No archive or reopen test |
| NS-34.11-1 | MUST | partial | Per-connector fixtures: auth success/expiry, scopes, rate limits, pagination | tests/unit/test_content_adapter_contract.py | No uniform per-connector auth, scope, rate-limit and pagination suite |
| NS-34.11-2 | MUST | partial | Per-connector fixtures: idempotency, partial failure, verification, undo | tests/unit/test_connector_registry.py | No uniform tests for idempotency, partial failure or undo |
| NS-34.11-3 | MUST | missing | Per-connector fixtures: revoked access, malformed content, receipt creation | tests/unit/ | No uniform fixtures for revoked access, malformed content or receipt creation |
| NS-34.12-1 | MUST | partial | Artifact tests: validity, rendering, bounds, formulas, charts, fonts, accessibility | tests/integration/test_office_end_to_end.py | Integration tests are not in CI; no font or accessibility checks |
| NS-34.12-2 | MUST | partial | Artifact tests: source preservation, overwrite safety, provenance, receipt linkage | tests/unit/test_office_safety.py | No receipt-linkage or provenance artifact test |
| NS-34.13-2 | MUST | missing | Persona goldens: identity consistency, warmth, humor boundaries | tests/persona/golden/ | No identity, warmth or humor-boundary goldens (only directness, pushback, voice_tone, refusal and similar) |
| NS-34.13-3 | MUST | partial | Persona goldens: action restraint, source integrity, error admission | tests/honesty/golden/01_zero_tool_click.json | No error-admission golden set |
| NS-34.13-4 | MUST | partial | Persona goldens: multi-provider parity | tests/persona/fixtures/ | Fixtures only for anthropic, openai and ollama-local |
| NS-34.14-1 | MUST | missing | Usability acceptance: nontechnical setup, privacy, routing, approvals, memory correction, receipt, stop, restore, principal switch | tests/app/ | No usability study artifacts |
| NS-34.14-2 | MUST | missing | Key flows completed with screen reader and keyboard | tests/ | No screen-reader or keyboard-only flow tests (grep finds nothing) |
| NS-34.15-1 | MUST | partial | Perf tests: startup, model load, context assembly, retrieval, tool schema overhead | tests/unit/test_latency_budget.py | No startup, retrieval or schema-overhead perf tests |
| NS-34.15-2 | MUST | missing | Perf tests: UI responsiveness, trigger storms, large stores, receipt verification, backup/restore | tests/ | No tests for UI responsiveness, trigger storms, large stores or backup perf |
| NS-34.15-3 | MUST | partial | Perf tests: GPU contention | tests/unit/test_residency_arbiter.py | The live residency test is integration and not in CI |
| NS-34.15-4 | MUST | missing | Perf tests: battery and thermal | tests/ | No battery or thermal tests |
| NS-34.16-1 | MUST | partial | Chaos: kill during model call and outward action | tests/api/test_task_resume.py | No kill during an outward action |
| NS-34.16-2 | MUST | partial | Chaos: kill during receipt write, file creation, migration | tests/edge_cases/test_concurrency_and_corruption.py | No kill-during-receipt-write test |
| NS-34.16-3 | MUST | missing | Chaos: kill during backup, restore, index rebuild, update, connector refresh | tests/ | No kill-during tests for backup, restore, index rebuild, update or connector refresh |
| NS-34.16-4 | MUST | partial | System recovers without duplicate actions or false completion | services/agent.py:3769 | No test that a completed_unverified result is never announced as finished |
| NS-34.17-1 | MUST NOT | partial | No release with known gate bypass | tests/unit/test_content_publish_gate.py:107 | Content publish bypass closed by 321ec490; scheduler run_workflow_chain (scheduler.py:975) still needs review |
| NS-34.17-2 | MUST NOT | partial | No release with cross-principal leak | tests/api/test_observer_reads_are_sealed.py | Only observer isolation is tested |
| NS-34.17-3 | MUST NOT | partial | No plaintext credential fallback | tests/unit/test_keystore_migration.py | No test of every credential read path |
| NS-34.17-4 | MUST NOT | missing | No unsigned update path | .github/workflows/installer.yml | Still no code signing; SHA-256 only |
| NS-34.17-5 | MUST NOT | partial | No reproducible arbitrary file escape | tests/unit/test_open_path_allow_list.py | No file-escape fuzz suite |
| NS-34.17-6 | MUST NOT | partial | No unreviewed critical dependency exposure | .github/dependabot.yml; .github/workflows/codeql.yml | No dependency review that gates a release |
| NS-34.17-7 | MUST NOT | partial | No missing receipt for outward action | tests/unit/test_receipts_match_the_action.py | No test checks that every outward action has a receipt |
| NS-34.18-1 | MUST | missing | Consented 30-day pilot before broad consumer readiness claims, measuring listed outcomes incl. failures | tests/ | No pilot plan or report |

### §35 (3 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-35.7-1 | MUST | partial | Safety/privacy metrics at zero (egress, bypass, leaks, plaintext creds, receipts, injection) and 100% self-test/restore coverage | services/egress_gate.py:1636 startup_self_test | No metrics view; computable locally: egress, bypass and receipts from the decision BOM and the self-test result; plaintext creds, injection and restore coverage need new counters. |
| NS-35.11-3 | MUST NOT | partial | Do not optimize for emotional dependency | tests/persona/fixtures/ | No dependency guard; not computable from current ledgers. |
| NS-35.11-6 | MUST NOT | partial | Do not optimize for quantity of stored memory | tests/unit/test_memory_dreaming_consolidation.py | No volume target found (correct for a MUST NOT); a memory count is computable locally, but no guard asserts against volume optimisation. |

### §36 (75 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-36-0 | MUST | partial | Implementation proceeds through coherent product increments, not parallel half-integrated features. | routes/goals.py:64-120; index.html:7612 | Phase 0 process rule. Goals still has no index.html UI. Half-integrated features keep landing in parallel. |
| NS-36.0-1 | REQUIRED | partial | Green independent CI stages. | .github/workflows/tests.yml; .github/workflows/installer.yml:40,73,96 | Phase 0. The stages are independent, but a green state can't be checked from the tree. |
| NS-36.0-2 | REQUIRED | partial | Signed and reproducible installer. | .github/workflows/installer.yml:63 (artifact only, no sign step) | Phase 0. Still no code signing and no reproducibility claim. Signing is only proposed (first-run-and-onboarding.md:88,105). |
| NS-36.0-3 | REQUIRED | partial | Clean-machine matrix. | .github/workflows/installer.yml:73,96 (windows-latest only) | Phase 0. Still one Windows runner, no matrix. Windows Sandbox testing is only proposed. |
| NS-36.0-4 | REQUIRED | partial | Canonical typed settings service. | core/__init__.py:2073,3037; routes/core_routes.py:1207 | Phase 0. Settings are still a central untyped dict with enum checks only for voice. |
| NS-36.0-5 | REQUIRED | partial | Dependency lock in production. | packaging/windows/requirements/core.txt:17-21 | Phase 0. The installer still installs version ranges (>=), not the uv.lock pins. |
| NS-36.0-7 | REQUIRED | partial | Known-issues surfacing. | KNOWN_ISSUES.md; 0 'known issues' hits in index.html | Phase 0. Known issues are documented but still not shown anywhere in the product. |
| NS-36.0-9 | REQUIRED | partial | Support bundle. | cli.py:13-14,749 | Phase 0. `friday doctor` still prints to the console only. No saved report file and no doctor route. |
| NS-36.0-10 | REQUIRED | partial | Update rollback. | services/boot_guard.py:193,344,461 | Phase 0. Rollback still covers self-edits and failed boots only. Whole-release rollback is only proposed. |
| NS-36.0-11 | REQUIRED | partial | Release evidence page. | .github/workflows/installer.yml:88-90,118-120 | Phase 0. Evidence artifacts and draft release exist, but no evidence page. |
| NS-36.0-12 | MUST | partial | Exit: nontechnical user can install, update, roll back, uninstall on clean machine; all release checks green. | .github/workflows/installer.yml:73-120 | Phase 0. Clean install, uninstall and upgrade jobs exist. User-facing release rollback and all-green gating are missing. |
| NS-36.1-1 | REQUIRED | partial | Home situation model. | static/workspace_registry.js:20-79; services/situation.py | Phase 1. adca94d5 moved workspaces into one registry, but it has no 'home' workspace. situation.py is a machine snapshot only. |
| NS-36.1-2 | REQUIRED | partial | Controlled source import. | services/setup_connections.py; first-run-and-onboarding.md:271,301 | Phase 1. No scoped, controlled import step in code; spec only. |
| NS-36.1-3 | REQUIRED | partial | First synthesis. | services/setup_research.py; services/setup_profile.py | Phase 1. No first synthesis of imported sources; spec only. |
| NS-36.1-4 | REQUIRED | partial | Confirm and correct flow. | routes/memory_proposals.py:28-74 | Phase 1. The proposal API has 0 refs in the UI, so no confirm-and-correct UI. |
| NS-36.1-5 | REQUIRED | partial | One governed action demo. | routes/goals.py (/api/approvals); services/item_actions.py (f6b296ee) | Phase 1. Organize actions raise one card per batch; still no scripted first-session governed-action demo. |
| NS-36.1-6 | REQUIRED | partial | Receipt link. | index.html:24989-25125 FridayChangesCard; routes/actions.py:25 | Phase 1. Receipts list with undo for organize actions only; no per-action receipt link from chat or approval cards. |
| NS-36.1-7 | REQUIRED | partial | First durable goal. | services/goals.py:365; routes/goals.py:72 | Phase 1. Goal creation is backend only; the 'Getting to know you' goal is spec only. |
| NS-36.1-8 | REQUIRED | partial | Daily and return-to-device briefings. | index.html:30486 (7:32 AM briefing); index.html:24980 morning receipt | Phase 1. Still no return-to-device briefing. |
| NS-36.1-9 | REQUIRED | partial | Concise priority explanations. | services/message_triage.py:28 | Phase 1. Reasons for mail lanes only; no general priority-explanation surface. |
| NS-36.1-10 | MUST | missing | Exit: >=70% pilots rate a first synthesis useful; >=80% complete governed action unaided. | none found | Phase 1. No pilot measurement for 'synthesis useful' or 'governed action completed unaided'. |
| NS-36.2-2 | REQUIRED | partial | Deferred tool schemas. | services/tool_catalogue.py:17,42 load_tools | Phase 2. Deferred schemas on the local path only; cloud still sends the full tool list. |
| NS-36.2-3 | REQUIRED | partial | Token budgets. | services/context_budget.py; services/turn_budget.py; services/tool_budget.py | Phase 2. Budget services exist; end-to-end enforcement not re-verified. |
| NS-36.2-4 | REQUIRED | partial | Context manifest. | services/retrieval_ledger.py | Phase 2. Section ledger recorded; no user-facing context manifest. |
| NS-36.2-5 | REQUIRED | partial | Current-state override. | routes/chat.py:1490-1491,2553-2554 pinned_block; services/live_state.py | Phase 2. The pinned block is machine state only. |
| NS-36.2-6 | REQUIRED | partial | Typed memory schema. | cognitive_memory.py; conversation_memory.py; services/memory_proposals.py | Phase 2. No single typed memory schema; three stores remain separate. |
| NS-36.2-7 | REQUIRED | partial | Source authority. | source_trust_graph.py; source_trust_federation.py | Phase 2. Source authority on memory writes not verified. |
| NS-36.2-8 | REQUIRED | partial | Corrections and supersession. | routes/memory_proposals.py:52,63; tests/api/test_memory_supersede.py | Phase 2. Supersession in the backend; no corrections UI. |
| NS-36.2-9 | REQUIRED | partial | Deletion traversal. | services/forget_person.py | Phase 2. Tombstones only; full deletion traversal not built. |
| NS-36.2-11 | REQUIRED | partial | Memory inspection. | routes/memory_proposals.py:40,74 | Phase 2. No memory inspection UI. |
| NS-36.2-12 | REQUIRED | missing | Retrieval evaluation. | services/persona_eval.py:126 (persona only) | Phase 2. No retrieval evaluation harness. |
| NS-36.2-13 | MUST | partial | Exit: memory precision >95%; current-state never from stale memory; overflow never silently changes route. | tests/api/test_live_state_not_from_memory.py; tests/api/test_memory_supersede.py | Phase 2. Memory-precision target unmeasured; no test that overflow never silently changes the route. |
| NS-36.3-1 | REQUIRED | partial | Single action envelope. | governance/action_gate.py:894 authorize; tests/unit/test_every_action_is_governed.py:81,135,203 | Phase 3. Single checkpoint exists; the action envelope is only proposed. |
| NS-36.3-5 | REQUIRED | partial | Idempotency. | 0 idempotency_key hits in src | Phase 3. Idempotency is ad hoc; no canonical key. |
| NS-36.3-6 | REQUIRED | partial | Verification engine. | services/tool_receipts.py; services/completion_receipts.py:28; services/qa_gates.py | Phase 3. Verification pieces, not one engine. |
| NS-36.3-8 | REQUIRED | partial | Receipt chains. | services/action_journal.py:92 record; services/goals.py:585 _sign_receipt | Phase 3. rcpt_ receipts for organize actions and signed private-share receipts; no linked chain across all actions. |
| NS-36.3-9 | REQUIRED | missing | Transaction boundaries. | 0 transaction-boundary hits | Phase 3. No transaction-boundary construct. |
| NS-36.3-11 | REQUIRED | partial | Ledger workspace. | index.html:56438,59207 ledger panel; static/workspace_registry.js:20-79 | Phase 3. The ledger is a panel, not a workspace; the registry has no ledger entry. |
| NS-36.3-12 | MUST | partial | Exit: every outward action has decision + terminal receipt; zero duplicates; false done blocked. | services/completion_receipts.py:28-37; services/publisher.py:19-21 | Phase 3. FAILURE_SENTINELS still lacks approval-card-raised and held markers; the false-done defect remains. |
| NS-36.4-1 | REQUIRED | partial | Goal and milestone state machines. | services/goals.py:414,430; routes/goals.py:118 | Phase 4. Goal and milestone state machines exist in the backend; no UI. |
| NS-36.4-5 | REQUIRED | partial | No-progress breaker. | services/prompt_cache.py:353 | Phase 4. No-progress guard covers turns only; goal-level breaker only proposed. |
| NS-36.4-10 | REQUIRED | partial | Paired owner channel. | services/channels/telegram_bridge.py; discord_bridge.py; manager.py | Phase 4. Bridges exist; explicit owner pairing only proposed. |
| NS-36.4-11 | REQUIRED | partial | Remote approvals. | phone/service.py:21,565 sms_approvals | Phase 4. Approval by text exists but has never been run live. |
| NS-36.5-2 | REQUIRED | partial | Accessibility control lane. | services/desktop_grants.py:339-341 | Phase 5. UIAutomation reads the accessibility tree only; no UIA action lane. |
| NS-36.5-6 | REQUIRED | missing | Transaction checkpoints. | none found | Phase 5. No browser or desktop transaction checkpoint. |
| NS-36.5-7 | REQUIRED | partial | Complete document pipeline. | services/office_engine.py; pdf_forms.py; pdf_signing.py; file_extraction.py | Phase 5. Pieces exist; end-to-end document pipeline not verified. |
| NS-36.5-8 | REQUIRED | missing | Visual QA. | 0 visual_qa hits | Phase 5. No visual QA. |
| NS-36.5-9 | REQUIRED | missing | Artifact manifests. | 0 artifact_manifest hits | Phase 5. No artifact manifests; content_credentials covers creative provenance only. |
| NS-36.5-10 | REQUIRED | partial | Publishing verification. | services/publisher.py:19-21,31-32,539-634 | Phase 5. Card for the exact words of every publish and verify-before-retry; post-publish verification across all destinations unconfirmed. |
| NS-36.5-11 | MUST | missing | Exit: browser/document workflows verified; no unapproved destination/upload/overwrite. | none found | Phase 5. No suite verifying representative browser and document workflows. |
| NS-36.6-1 | REQUIRED | partial | Encrypted Friday Bundle. | cli.py:1602 cmd_export; services/data_export.py | Phase 6. Passphrase-encrypted full backup exists; the Friday Bundle format is not defined in code. |
| NS-36.6-2 | REQUIRED | partial | Human-readable export. | services/data_export.py | Phase 6. Export is raw ~/.friday files, not a human-readable rendering. |
| NS-36.6-4 | REQUIRED | missing | Optional encrypted sync. | 0 sync-service hits | Phase 6. No optional encrypted sync. |
| NS-36.6-5 | REQUIRED | partial | Device identities. | services/federation.py; federation_transport.py | Phase 6. Peer identity exists; no per-device identity registry. |
| NS-36.6-6 | REQUIRED | partial | Principal isolation. | services/observer_access.py; tests/api/test_observer_reads_are_sealed.py | Phase 6. Multi-principal isolation only proposed. |
| NS-36.6-7 | REQUIRED | missing | Shared spaces. | 0 shared_space hits | Phase 6. No shared spaces. |
| NS-36.6-9 | REQUIRED | partial | Minor mode. | core/__init__.py:2536; services/creative_engine.py; services/setup_chat.py:379 | Phase 6. minor_mode applies to creative, music and setup only. |
| NS-36.6-10 | REQUIRED | partial | Third-party deletion. | services/forget_person.py | Phase 6. Tombstones only; no third-party deletion traversal. |
| NS-36.6-12 | MUST | partial | Exit: move to new machine with re-attested governance; principals cannot read each other. | .github/workflows/installer.yml:96-120 upgrade job | Phase 6. No principal-isolation tests and no re-attested governance on a move to a new machine. |
| NS-36.7-3 | REQUIRED | partial | Cross-provider persona parity. | services/persona_eval.py:116-126 | Phase 7. Golden corpus exists; cross-provider parity run not verified. |
| NS-36.7-4 | REQUIRED | partial | Skill lifecycle. | skill_registry.py; skill_capture.py | Phase 7. Learned-skill self-activation still open. |
| NS-36.7-5 | REQUIRED | partial | Generated skill sandbox. | services/code_sandbox.py; services/extension_security.py | Phase 7. No dedicated sandbox for generated skills. |
| NS-36.7-6 | REQUIRED | missing | Liquid UI manifests. | 0 liquid_manifest hits | Phase 7. No Liquid UI manifests. |
| NS-36.7-7 | REQUIRED | missing | Seeds and gardens. | none found | Phase 7. No seeds or gardens construct. |
| NS-36.7-8 | REQUIRED | partial | Workspace rollback. | routes/workspace_undo.py:30-45 | Phase 7. Undo and restore exist; the undo flip-flop defect not re-verified. |
| NS-36.7-9 | REQUIRED | partial | Evidence-bearing knowledge and process visualization. | index.html:12866 KG_PALETTE; services/workflow_overview.py | Phase 7. Scene states from real events; knowledge and process views not verified as evidence-bearing. |
| NS-36.7-10 | MUST | partial | Exit: durable persona/skill/workspace changes attributable, previewable, reversible, principal-scoped. | services/soul.py:33,202,264; services/boot_guard.py:461 | Phase 7. Persona changes versioned; principal scoping missing. |
| NS-36.8-2 | REQUIRED | partial | Permission review. | services/extension_security.py | Phase 8. No per-extension permission review UI or flow. |
| NS-36.8-3 | REQUIRED | partial | Sandbox hardening. | services/extension_security.py; services/origin_gate.py (20b97f9f) | Phase 8. MCP subprocess sandbox is an environment allowlist only. |
| NS-36.8-5 | REQUIRED | partial | Security review process. | SECURITY.md; docs/security/threat-model.md; .github/workflows/codeql.yml | Phase 8. No defined security review process. |
| NS-36.8-6 | REQUIRED | partial | Revocation. | services/extension_security.py:504 remove_from_allowlist | Phase 8. Revocation local only. |
| NS-36.8-7 | REQUIRED | partial | Incident handling. | SECURITY.md | Phase 8. No incident runbook. |
| NS-36.8-10 | MUST | partial | Exit: malicious extension cannot access undeclared data, bypass governance or hide effects. | tests/unit/test_extension_security.py | Phase 8. No test that a malicious extension cannot read undeclared data or hide its effects. |
| NS-36.9-1 | MUST NOT | partial | No phase claims completion while release, security, docs or clean-machine criteria are red. | .github/workflows/tests.yml guards job | Phase 9. No gating of phase completion on release, security, docs or clean-machine checks. |
| NS-36.9-2 | MUST | partial | New surfaces reuse canonical context, principal, action and receipt systems, no local exceptions. | services/workspace_studio.py:209-218,501 (no check_blast_radius) | Phase 9. The workspace chat path still skips the blast-radius check. |

### §37 (35 rows)

| Row | Strength | Status | Requirement | Evidence | What is still missing |
|---|---|---|---|---|---|
| NS-37.2-1 | MUST | partial | Consolidate settings readers and writers. | scripts/check_settings_readers.py:98; services/local_seats.py:356 | Direct readers remain: local_seats.py:356, local_address.py:217, cli.py:155-172, setup_wizard.py. |
| NS-37.2-5 | MUST | partial | Consolidate receipts. | services/action_journal.py:4; services/goals.py:206 | Receipts are still split (decision-bom, goals, tool_receipts, voice_receipt); f6b296ee added another store (action_journal). |
| NS-37.2-6 | MUST | partial | Consolidate personality state. | services/soul.py; voice_personality.py | Several personality holders remain (soul, voice_personality, voice_persona, personality.json); 7f86fb0f added avatar_genome traits. |
| NS-37.2-7 | MUST | partial | Consolidate process registries. | services/task_journal.py; services/task_ledger.py | Five separate process registries remain (task_journal, task_ledger, work_queue, interactive_sessions, activity_ledger). |
| NS-37.2-9 | MUST | partial | Consolidate approval execution. | services/approvals.py; services/approval_executor.py | approvals, approval_executor and approval_feed are still separate; voice yes path adds local_context.py logic. |
| NS-37.2-10 | MUST | partial | Consolidate action wrappers. | tests/unit/test_every_action_is_governed.py; services/workspace_studio.py:462-472 | Workspace-studio chat path still outside the checkpoint; 321ec490 put content publishing under the owner card. |
| NS-37.2-11 | MUST | partial | Consolidate task and goal state. | services/goals.py:365 | Goals are still separate from task_journal/task_ledger. |
| NS-37.2-12 | MUST | partial | Consolidate file path policy. | services/file_grants.py; services/path_probe.py | Path policy is still split across file_grants, path_probe, open_safety and browser_session. |
| NS-37.2-13 | MUST | partial | Consolidate local/cloud fallback. | core/__init__.py:3348; services/cloud_spill.py | Offline overlay, cloud_spill and local_only_guard are still separate. |
| NS-37.3-1 | REQUIRED | missing | Inventory every existing store and version. | services/conversations.py:156 | No store/version inventory; migrations remain ad hoc. |
| NS-37.3-2 | REQUIRED | partial | Back up full Friday home before migration. | privacy/vault_rekey.py:39; cli.py:1463 | Backup exists but is not wired to a migration step. |
| NS-37.3-3 | REQUIRED | missing | Map old data to principal scope, default owner. | services/goals.py:365 | Only owner='owner' default; no principal mapping. |
| NS-37.3-4 | REQUIRED | missing | Convert conversations and memories with source references. | none | No canonical conversion of conversations or memories. |
| NS-37.3-5 | REQUIRED | missing | Convert personality/style into constitution-safe persona layers. | none | No persona-layer conversion. |
| NS-37.3-6 | REQUIRED | missing | Convert goals and tasks preserving receipts. | none | No goal/task conversion preserving receipts. |
| NS-37.3-7 | REQUIRED | missing | Convert approvals and grants. | none | No approvals/grants conversion. |
| NS-37.3-8 | REQUIRED | partial | Validate receipt and provenance history. | services/morning_receipt.py:7,69; services/goals.py:601 | Validation exists per store, not as a migration step. |
| NS-37.3-9 | REQUIRED | missing | Rebuild derived indexes. | none | No migration-driven index rebuild. |
| NS-37.3-10 | REQUIRED | missing | Present unresolved ambiguities to owner. | none | No ambiguity presentation to the owner. |
| NS-37.3-11 | REQUIRED | missing | Produce a migration receipt. | src/agent_friday (0 migration_receipt hits) | No migration receipt. |
| NS-37.5-1 | REQUIRED | partial | All settings normalized into a typed schema. | core/__init__.py:2073 | DEFAULT_SETTINGS is still an untyped dict. |
| NS-37.5-2 | REQUIRED | partial | Unknown/deprecated settings preserved in migration report, not silently controlling behavior. | core/__init__.py:2223,2245 | Unknown keys are dropped on load; no migration report preserves them. |
| NS-37.6-1 | REQUIRED | partial | Every write-capable handler registered behind canonical action envelope. | governance/action_gate.py; services/workspace_studio.py:462 | No canonical action envelope object; studio route path is outside it. |
| NS-37.7-1 | REQUIRED | partial | Decision BOMs, goal receipts, provenance, traces, journals, tool receipts exposed through unified Ledger. | index.html:59219 (Ledger); services/morning_receipt.py:53 | Goal, provenance, tool and new action_journal receipts are not shown in the unified Ledger. |
| NS-37.7-2 | REQUIRED | partial | Historical records keep original integrity semantics and are labeled, not rewritten. | services/morning_receipt.py:7 | Only morning receipt labels verified/unsigned/edited; other stores are not labelled. |
| NS-37.8-1 | REQUIRED | missing | Reorganize catalog around core loop; Home/Goals/Activity/Approvals/Knowledge/Ledger/Settings/Doctor as spine. | static/workspace_registry.js:25-79; index.html:7612 | New registry core set is still News/Messages/Calendar/Career/People/Code/Sites/Knowledge/System/Settings; no Home/Goals/Approvals/Doctor. |
| NS-37.9-2 | REQUIRED | missing | Guided 'Complete your sovereignty setup' flow for existing installs. | index.html (0 hits) | No "Complete your sovereignty setup" flow; onboarding spec is unbuilt and first-run only. |
| NS-37.9-3 | REQUIRED | missing | Flow step: recovery key. | src/agent_friday, index.html (0 recovery key hits) | No recovery key. |
| NS-37.9-4 | REQUIRED | partial | Flow step: encryption migration. | privacy/vault_rekey.py:39 | Rekey exists, but not in a guided flow. |
| NS-37.9-5 | REQUIRED | missing | Flow step: principal creation. | none | No principal model. |
| NS-37.9-6 | REQUIRED | partial | Flow step: connector scope review. | services/setup_connections.py | First-run checklist only; no review step for existing installs. |
| NS-37.9-7 | REQUIRED | missing | Flow step: autonomy review. | none | No autonomy review step. |
| NS-37.9-8 | REQUIRED | partial | Flow step: backup. | cli.py:1463 | export --full exists but is not guided. |
| NS-37.9-9 | REQUIRED | missing | Flow steps: first synthesis and goal creation. | index.html (0 /api/goals hits) | No goal UI or synthesis step. |
| NS-37.10-1 | REQUIRED | partial | Documented migration window from previous versions. | .github/workflows/installer.yml:36,94-104 | Upgrade only from one pinned release (v5.13.0 default); no documented migration window. |

## 4. Rows not re-verified

509 rows keep their 09-29 status: conflicting 23, missing 42, obsolete 1, partial 123, proposed 83, shipped 237. They are SHOULD and MAY rows, rows already `shipped` or `proposed`, the `conflicting` rows queued as P-NS-CONFLICTS, and one `obsolete` row.

