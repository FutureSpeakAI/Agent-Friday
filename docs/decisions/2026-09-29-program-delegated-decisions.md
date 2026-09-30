# Delegated decisions for the "build all pending specs" program

**Status:** in force; each entry is reversible by the owner in one line.
**Written:** 2026-09-29
**Authority:** the owner's commission to build every pending spec, which is a
go on everything pending. Where a spec leaves a product decision open, the
spec's own recommended option is taken unless the owner has already ruled. The
precedent is the 2026-09-04 gauntlet-audit delegation: each entry records the
reasoning as the program's own, never as the owner's words, so overturning one
means reading a decision rather than reconstructing a question.

**Standing rules from the owner that outrank any spec:**
1. Voice-first parity. Nothing is voice-only-limited unless the user sets the limit.
2. Local and cloud paths both exist, and the user picks.
3. No telemetry, ever.
4. Approval gates and cLaws are never weakened.
5. Avatar evolution is on by default, with a frontier author by default; any
   model may be chosen.
6. Branding stays intact and is strengthened across every surface.

**What escalates instead of being decided here:** anything irreversible,
anything only the owner can physically do, and anything whose purpose would
be a guess.

Every entry was checked against the code on main at 780e31fa.

---

## Product decisions

| # | Spec · question | Decision | Why |
|---|---|---|---|
| D1 | avatar-visual-genome §12 · on or off for new installs | **On.** Existing installs get the spec's one-time question. | Owner rule 5. |
| D2 | avatar-visual-genome §12 · default author | **A frontier (cloud) author by default.** The on-this-computer author is one click away, and any model may be chosen. The first cloud step still passes the existing cloud-consent screen and egress gate. | Owner rule 5 overrides the spec's local-author recommendation. Rule 4 keeps the consent screen and egress gate. |
| D3 | avatar-visual-genome §12 · lifetime hue drift | **±30° around the stored anchor.** Validators keep ΔE2000 ≥ 20 from every reserved status colour. | The spec's recommendation. |
| D4 | avatar-visual-genome §3 · anchor hue value | **The anchor is the shipped brand cyan `#00d4ff` (hue ≈ 190°), not 180°.** | The spec says "v1 cyan (hue ≈ 180°)", but v1 cyan in the code is `#00d4ff`. Code beats documentation, and moving the brand colour to match a doc would change the brand. |
| D5 | vault-cloud-fallback-decision · `redact` or `warn` | **`warn`** for new installs. An existing stored value is left alone, and the user can choose `redact` in Settings. | The spec recommends `warn`. It is the stricter choice, so it strengthens rule 4. Offering the choice satisfies rule 2. |
| D6 | local-voice-repair-and-native-audio · native audio-in (ROADMAP Q1) | **Parked.** `native_audio.py` and its test stay; no UI is added. | No audio-capable local model is resident: the resident seat is a 27B text model, and the module's markers only accept e2b/e4b/12b. A mode with nothing able to serve it would be a control that does nothing. Revisit when an audio-capable seat is resident. |
| D7 | deep-research · surface or delete (ROADMAP Q1) | **Surface.** Finish the remainder and archive the spec. | It has been reachable as a chat tool since 2026-09-24. |
| D8 | google-housekeeper Q-H4 / ROADMAP Q3 · standing Gmail send | **No standing send.** Sending stays behind one approval card per message, which is how it is built today. | Rule 4. The spec: "sends never" as a standing grant. |
| D9 | workspace-ecosystem §5 · marketplace purchases | **Purchases off by default until the transfer is real.** | The spec already decided this and it was never applied. `spend`/`earn` are called without `reason`, so a purchase reports success while nothing moves. |
| D10 | workspace-ecosystem §6.2 · network access for community workspaces | **None at first.** | The spec's recommendation. |
| D11 | workspace-ecosystem §6.4 · an agent publishing without a human sponsor | **No. A human sponsor is required.** | Rule 4. |
| D12 | autonomy A7 · learning loop default | **Default off for new installs until promotions emit signed receipts and notify-tier cards (crew D10). Then back on.** The owner's stored setting is not touched. | The spec says off. The code ships it on, without the receipts crew D10 requires. |
| D13 | autonomy Q10 · browser lane | **Ratify what shipped: a Playwright-owned browser with its own profile.** | Code beats documentation. It is also better isolated than a CDP bridge into the owner's Chrome. |
| D14 | laya-across-the-harness · judgment gate | **Stays off.** Turning it on waits for a test proving it can only add gates, never clear one. | Rule 4. It is an appeals court, and an appeal that can clear a gate weakens that gate. |
| D15 | goals-and-delivery-receipts · local-only with no evaluator seat | **"Unchecked"**, shown as such. | The spec's recommendation. It is honest about limits. |
| D16 | goals-and-delivery-receipts · workflow `done_when` | **Phase 4.** | The spec's recommendation. |
| D17 | goals-and-delivery-receipts · Ed25519 head signing | **Daily.** | The spec's proposal. |
| D18 | goals-and-delivery-receipts · keyed recipient hashing | **Yes, keyed (HMAC).** | The spec leaves it open. Keyed hashing prevents a leaked receipt from being matched to an address by brute force. It is reversible. |
| D19 | owner-rules · detectors on by default | **Yes.** | The spec's recommendation. The detectors only pause; they never act. |
| D20 | owner-rules · log retention | **90 days.** | The spec's proposal. |
| D21 | owner-rules · purchases | **Count Twilio spend as a purchase now.** A general purchase tool is not built. | The spec leaves it open. Twilio is the only real outward spend today. |
| D22 | owner-rules · where "ask twice" asks | **A second confirmation on a different surface when one is configured (the phone line). Otherwise, a second desktop card after a delay.** | The spec leaves it open. A different surface resists a single compromised session. |
| D23 | one-tool-registry Q1 · `run_command` on voice | **Available on voice, behind the same approval gate as text.** | Rule 1 overrides the spec's exclusion. Rule 4 is kept because the gate is the same one. |
| D24 | one-tool-registry Q2 · spoken confirmation for voice writes | **Voice writes raise the same approval card, and it can be answered by voice or on screen.** | Rules 1 and 4. |
| D25 | action-creation Q1 and Q8 · creative and Office tools on voice | **All of them on voice, the three creative tools first.** | Rule 1 overrides "chat-only first". |
| D26 | v6 Q6 · dissent strength | **Soft by default. Hard for outward, irreversible or high-cost actions.** | The spec's recommendation. |
| D27 | v6 Q5 · export identity | **Include it, re-encrypted under a transit passphrase.** | The spec's recommendation. |
| D28 | context-assembly Q2 · gate TODAY'S CONTEXT by relevance | **Yes.** | The spec leans yes. |
| D29 | context-assembly Q4 · verbatim-turn floor | **Keep the shipped 12-message floor.** | It is more generous than the spec's 4 turns and already live. |
| D30 | voice-system-clean-sheet Q2 · LuxTTS | **Parked.** | No recommendation in the spec. It is not needed for parity. |
| D31 | cloud voice · ElevenLabs/Inworld selection | **Fix both halves together: the server accepts the choice, and conversation actually speaks through the cloud voice.** Inworld stays unshipped until it is GA (spec §10.0). | Fixing only the first half would turn a visible error into a silent substitute voice. |

## Salon decisions (vibe-coding-salon.md §12, amendment A8)

These three were first recorded as "decided by the owner". They were not.
They were taken as the spec recommended, under the owner's delegation, and
are relabelled "delegated decision, as recommended" in the spec and in
AMENDMENTS.md A8.

| # | Question | Decision | Why |
|---|---|---|---|
| S1 | The box's default network posture | **"Announce".** Reads and installs go ahead and are announced. Writes to outside hosts and private-data sends ask, as they do everywhere else. "Ask" remains available as an owner rule. | The spec's recommendation. It follows the owner's "no restrictions unless the user sets them" and keeps every existing outward-action gate. |
| S2 | Friday editing her own safety checkpoint and signed laws in the salon | **Allowed only on a copy, and only through the loud approval of §7.4.** That means a separate card, a fresh challenge word, no grant, a signed notice afterwards, and re-attestation before outward actions resume. | The spec's recommendation. The alternative, hand-merge only, would mean Friday could never fix her own gate even when asked. **This touches cLaws, so it is flagged for the owner to confirm.** |
| S3 | Someone else's API key in your Friday | **Yes, bound to one codebase,** metered separately and removable in one click. | The spec's recommendation. |

## Brand decisions

| # | Question | Decision | Why |
|---|---|---|---|
| B1 | Brand primary | **`#00d4ff` brand cyan.** Its variants are folded into tokens (`#00ffff` stays only for the HUD and cursor). | 308 literal uses plus the scoped settings tokens. This codifies it; it does not invent. |
| B2 | FutureSpeak amber `#f59e0b` also means warn and approval | **Amber stays in the FutureSpeak wordmark and nowhere else as an accent. Everywhere else, amber means "needs you".** | It removes the collision without changing either the wordmark or the status language. |
| B3 | Status hues (4–8 values per role today) | **One token each: ok `#00ff80`, warn/approve-pending `#f59e0b`, deny `#ff0080`, error `#ef4444`.** Python dicts read the same values. | These are the reserved colours the avatar spec names, and the most common value for each role. |
| B4 | The mark | **The neon rocket (tray, favicon, exe) is the mark.** It is re-exported with a real alpha channel. The bolt stays only as the Sites workspace icon. | The rocket is what ships everywhere the product is identified. |
| B5 | News register | **An evidence-first anchor register, described by traits and never by named journalists.** | `voice_persona.VOICE_ANCHOR_RULES` already forbids imitating real broadcasters. Product copy and prompts keep that rule, so the "Jennings-meets-Maddow" note stays an internal description of traits. |
| B6 | Body font | **Inter**, replacing the Helvetica default. Orbitron is display-only, with a `sans-serif` fallback everywhere. JetBrains Mono is for data. | Inter is already the settings and UI face. The Helvetica default is an inherited leftover. |
| B7 | Status dots · `.status-dot` was defined twice (6px and 8px) | **One 8px dot, one set of hues.** ok `#00ff80` (was `#00ff66`), warn `#f59e0b` (was `#ffcc00`), error `#ef4444` (was `#ff0033`), neutral `#7a8699` (was `#666`, now unglowed). Connecting `#7b61ff`, needs-setup `#f59e0b` and blocked `#ff0080` get colours; they had none. | The two definitions disagreed on size and hue, and four states rendered as an invisible dot. The reserved hues are the shipped ones (B3). |
| B8 | Settings danger colour | **Error red `#ef4444`** replaces `#ff6b8a` for the danger button and failure text in Settings. | A failure is error red. The pink was a third red beside the two already shipped. |
| B9 | Connector states (Python) | **error `#ef4444` (was `#ff5470`), blocked by policy `#ff0080` (was `#ff8c42`), connecting `#7b61ff` (was `#f59e0b`), unknown `#7a8699` (was `#888888`).** | A failure is error red, a refusal is deny magenta, and amber only means "needs you"; a handshake in progress needs nothing from you, so it is violet. |
| B10 | Notification priority colours (Python) | **critical `#f59e0b` (was `#ff3366`), high `#00d4ff` (was `#ff8a00`), medium `#a78bfa` (was `#ffd23f`), low `#7a8699` (was `#00d4ff`).** | Decoration and priority do not borrow error red: a critical alert needs you, and nothing has failed. Amber is "needs you"; the rest are brand and category hues. |
| B11 | Push-to-talk indicator (Python) | **arming and idle `#7a8699`, recording `#00d4ff` (was `#ef4444`), thinking `#7b61ff` (was `#e0a030`), done `#00ff80` (was `#22c55e`), clipboard `#f59e0b` (was `#e0a030`).** | A live microphone is the brand, not a failure. Thinking is work in progress (violet). Landing on the clipboard is a hand-off to you (amber). |
| B12 | Connected-account palette (Python) | **cyan, purple, teal, pink, blue `#60a5fa`, sand `#d6c7a1`, soft violet `#a78bfa`.** New accounts only. | An account badge is decoration; it holds no ok, warn or error hue, so it never reads as connected, approved or failed. |
| B13 | Type · finishes B6 | **The `body` font is Inter, Orbitron falls back to `sans-serif` in every UI source, and the login page is Inter with an Orbitron heading.** | B6 said "everywhere"; the login page and many declarations still fell back to monospace or to nothing. |
| B14 | The mark · finishes B4 | **`assets/icons/futurespeak.png` and both `.ico` files carry a real alpha channel; `assets/friday.ico` is added.** | The installer and autostart shortcuts read `app\assets\friday.ico` and it did not exist. |
| B15 | Decoration never carries status | **Politics news `#60a5fa` and local news `#d6c7a1` (were amber and ok green); the finance lane `#d6c7a1` (was `#22c55e`); trending clusters `#a78bfa` (was `#ffae5b`); a connector that needs setup is amber `#f59e0b` (was cyan); the login page and the generated draft page read the brand tokens (`#7c3aed`, `#ff4466` and the violet login palette are gone); the remaining `#22c55e` done-greens become `#00ff80`.** | Amber only means "needs you", green only means done or connected, red only means failed. Decoration that borrowed those hues taught the reader the wrong meaning. `scripts/check_brand_tokens.py` now fails a category or lane property that points at a status hue. |

## Retirements proposed (with evidence; not silent)

These are proposals, reported to the owner with the evidence. Moving a doc to
`docs/design/historical/` is reversible, and nothing is deleted.

- **`findings-graph.md`**: never built, and the gauntlet's `findings.jsonl` pattern replaced it in practice.
- **`friday-crew-spec.md`** (desks, demonstrations, Hyper-V). Kept: skill draft/active status (X2) and receipted promotions (D10). There is one agent, one user and one 12 GB machine.
- **`agent-editor-and-coordination-spec.md` AE-2 to AE-7**: the spec's own kill criterion R3.
- **`self-patching-installer.md` §6–17**: its own §0.3 measured zero conflicts, and the only install is one git checkout. Kept: the Phase 0 manifest and a doctor scan.
- **`tool-index.md`**: the flat catalogue superseded grouping. Kept: the §7.0 disclosure rule.
- **`v6-wholeness-spec.md`** as an umbrella, with P3 (traversal lighting) and P9 (multi-user). Its live pieces continue as their own items.
- **`elevenlabs-voice.md` (C) and §4 floor control**: nothing was built toward it.
- **`voice-system-spec.md`**: superseded by the clean-sheet spec, which absorbs §13.
- **`model-soup.md`, `frontier-on-12gb-deepseek-derived.md`**: every figure is from the Gemma-era seat.
- **`grow-button.md`** (the button itself). Its verification discipline moves into the nightly loop.

## Corrections to the 2026-09-19 roadmap

- **`hostname-onboarding.md` is built for Windows, wired and tested (52 tests), and is not the Caddy design.** Archive it; do not delete it.
- **Calendar writes are account-explicit** (ea3055ec). Only the Quick Add route remains.
- **Deep research is reachable** as a chat tool (37a9ce85).
- **`approvals.py` has about 22 callers**, not only `goals.py`.
- **The resident seat is `bonsai2:27b`**, planned at 65,536 context and served at 49,152, not 32,768.
- **The ElevenLabs selection never saves.** The server rejects the value with a 400. The "hears Piper" outcome needs a second path around validation.

## Salon additions, 2026-09-30

Owner rulings (the owner's words are quoted in `docs/design/active/vibe-coding-salon.md` §12):

- **LocalStack is out entirely; no salon component may phone home.** Evidence: localstack.cloud/pricing, checked 2026-09-29 (account required on the free plan, offline delivery only on top tiers, telemetry sharing enforced on the free plan). Phase 5 becomes Friday's own local cloud emulator on the patterns spike S4 recorded.
- **Friday never serves tools or pages to the internet from the user's hardware.** The tool manifest keeps `share_tier` `none` and `bundle` and adds `web`; the "trusted" and "public" hosting tiers are removed.
- **"This PC" is the default host for published static artifacts**, with the isolated static server, its own tunnel hostname, the read-only `published/` folder, no route to Friday, the kill switch, reachability status and adversarial tests. Hosted adapters (the user's own Cloudflare Pages or GitHub Pages; FutureSpeak later) are for always-on pages.

Delegated decisions taken as recommended (engineering; the owner may overrule): the competitor-pass options in the spec's §4.11 and §12; B1 as an AppContainer with a pipe bridge (S2); esm.sh as the one frame package host (S1); one shared event stream per page; SVG charts drawn by the panel. Product intent flagged, not decided: whether Friday publishes or only prepares; whether a self-made tool may act while the owner is away; licence posture (inform or refuse).
