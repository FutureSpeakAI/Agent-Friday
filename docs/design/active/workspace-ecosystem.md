# The Workspace Ecosystem

**Status:** spec, unbuilt. Written 2026-09-20 against the tree at `e54d82d`.
**Asked for:** dock settings (show/hide per workspace), version review and rollback,
vibe-coding workspace enhancements with Friday, a dedicated UI-building workspace,
and community sharing paid in positrons and negatrons.
**Evidence appendix:** `docs/design/research/2026-09-20-extension-ecosystems-survey.md`

---

## THE ONE THING THAT DECIDES EVERYTHING ELSE

Friday's Flask server trusts loopback absolutely. `core/__init__.py:2843`:

```python
if _loopback_trusted():
    if not session.get("authenticated"):
        session['authenticated'] = True
```

That is the right call for a local-first app a person runs on their own machine — a
user should not meet a login screen on their own desktop. But it has a consequence
that governs this entire document: **any JavaScript running in Friday's origin has
the whole API.** The vault. The credential store. `/api/agent/chat`, which reaches
`run_command`, which is real PowerShell at full user privilege
(`agent.py:1746-1764`). There is no CSP anywhere in the tree — grepped for
`Content-Security-Policy`, `X-Frame-Options` and `after_request` across
`src/agent_friday/`: zero hits.

So there is exactly one question to answer before any of the rest of this is worth
designing: **does a workspace somebody else wrote run in Friday's origin?**

If yes, "install a community workspace" means "hand a stranger your machine," and
every subsequent control — review, signing, reputation, badges — is theatre over a
hole. If no, the whole thing is tractable.

The answer here is no, and §4 is how.

---

## 1. WHAT EXISTS TODAY

Establishing this first, because half of what the ask describes is already built and
the other half is blocked on something that isn't.

### The dock

`index.html:4167-4282`. One hardcoded array, `DOCK_GROUPS`, flattened into `WS`.
Entries are `{id, ico, label, core?}` — **no component field**. Three groups (Life,
Work, System), 22 workspaces.

Rendering is inline in `App()` at `index.html:46449-46553`, iterating the registry.
The only filter that exists is:

```js
g.items.filter(w => showAllWorkspaces || w.core)
```

One boolean, `show_all_workspaces`, all-or-nothing across 22 items. **No per-item
hide, no reorder, no pinning, nothing persisted per workspace.**

Workspace → component is a *second* hardcoded literal, `wsMap`
(`index.html:43852-43876`), holding pre-constructed elements. Two literals that must
be kept in sync by hand, plus the component itself, plus the `ui_parts/app.html`
mirror that `AGENTS.md:52-57` requires. A workspace costs four edits in two files.

Worth noting the existing graceful-degradation path: a `DOCK_GROUPS` entry with no
`wsMap` key renders the string **"Coming soon"** inside a working window
(`index.html:44719`). That is already the socket a bundle host plugs into.

### Versioning — mostly built, genuinely useful, no UI

`services/workspace_studio.py` already does most of what "review older versions and
load them" asks for:

- `~/.friday/workspace_studio/<ws>.json` per workspace, holding `chat`,
  `customization`, `versions`.
- 40 snapshots retained (`_MAX_VERSIONS`, `:40`), oldest truncated.
- Snapshots hold the **pre-change** state, and `history()` says so in prose:
  `"state BEFORE: %s"` (`:297`).
- `undo_last`, `revert_customization(version_id)`, `restore_as_of(timestamp)`,
  `reset_customization` — all implemented, all snapshotting before they act, so
  every undo is itself undoable.
- Endpoints exist: `GET /api/workspace/<id>/history`, `POST .../undo`,
  `POST .../restore-as-of`, `POST .../revert`, `POST .../reset`.

There is **no UI for any of it.** The only way to reach this today is to ask Friday,
through two agent tools. So this part of the ask is a front-end job on a back end
that is already there, which makes it Phase 0 work.

Four real defects in it, though (§3).

### Vibe-coding — built, and narrower than it sounds

`workspace_chat_turn` (`workspace_studio.py:399-463`) is a real loop: chat with
Friday about a workspace, Friday emits a fenced ` ```friday-customize ` block, the
patch is sanitised and applied live.

But the patch can only touch six keys (`:43`):

```python
_ALLOWED_KEYS = {"css", "note", "accent", "density", "hidden", "actions", "summary"}
```

CSS, a note, an accent colour, density, a list of hidden selectors, and up to eight
`{label, prompt}` buttons that seed a chat. **It cannot add behaviour, fetch data,
render a component, or create a workspace that did not already exist.**

This is the gap between what is built and what was asked for. Today "vibe-code a
workspace" means "restyle a workspace." The owner is describing "build me a panel that
does a thing, then sell it." Those are different products, and everything from §4
onward exists to close that distance.

### The marketplace — a catalogue with a broken till

`services/marketplace.py`, 532 lines, local SQLite at `~/.friday/marketplace.db`,
surfaced through `routes/federation.py:278-405`. Listings, purchases, a policy
table. Ed25519 signing on create.

It does not work, in four separate ways, and they compound (§3). The one that
matters architecturally: **nothing is transferred.** A purchase writes a ledger
entry, an ownership row and a receipt. No bytes move. There is no mechanism by which
a listing *could* deliver a workspace, because there is no artefact format to
deliver.

### Skills — the precedent to NOT follow

`skill_registry.py` already installs third-party content: `POST /api/skills/import`
takes a zip, extracts it, and `build_injection` (`:271-288`) splices the skill's
body straight into Friday's system prompt.

There is no signature, no hash, no publisher, no review. A skill is a
prompt-injection payload by construction. Grepped `skill_registry.py` for `verify`,
`signature`, `hash`, `trust`: nothing.

By contrast `services/extension_security.py` — the MCP path — has a real model:
command-fingerprint allowlisting where "a changed command is treated as NOT
allowlisted" (`:430`), an env **allowlist** rather than denylist, a static launch
scanner with allow/warn/block verdicts, and a per-call audit log.

A shareable workspace is structurally much closer to a skill than to an MCP server.
Left alone, it inherits the weaker model by default. That is the trap.

---

## 2. WHAT THE FIELD ALREADY LEARNED

Full citations in the research appendix. Six findings changed this design.

**Only Figma actually isolated the plugin.** VS Code, Obsidian and Raycast all
isolate the *UI surface* or nothing, and leave the logic tier with full ambient
authority. Figma paid a real tax — a second JS VM (QuickJS) compiled to WASM, no
native devtools, slower — to buy a boundary that does not depend on a reviewer
noticing. Their own sentence is the sharpest in the survey:

> "We do not rely on human reviews to audit newly-published plugins for security as
> audits can produce false negatives. We instead use a sandbox to enforce a security
> boundary."

And the shape is better than a hierarchy: two contexts that are capability-
*orthogonal*, not ranked. The main thread reaches the document but not the browser;
the iframe reaches the browser but not the document. **Neither half is worth
compromising alone.**

**Install-time permission manifests do not work, and this is measured.** Felt et al.
(SOUPS 2012): 8 of 302 people answered three permission-comprehension questions
correctly; the end-to-end figure is "8% of 25 participants paid attention to,
understood, and previously acted on permissions." 91.4% of the top 500 Chrome
extensions trigger at least one warning, so the warning carries no information.
Worse, the structural attack: declare broadly at install, ship the code in a later
update, and **no warning fires at all**. Contextual runtime prompts do measurably
better — a 16% denial rate with articulable reasons, versus 8% end-to-end efficacy
at install.

**Default-deny at the consent layer outperformed every review, badge and signature
regime in the survey.** The one ecosystem with no documented malicious-registry
incident is Obsidian, where plugins are off by default, enablement cannot be toggled
remotely, and the user must take an explicit action per vault. Attackers targeting
Obsidian abandoned the supply chain entirely and social-engineered individuals
instead (REF6598/PHANTOMPULSE, April 2026 — and note that campaign weaponised *two
entirely legitimate plugins* through their configuration).

**Cooldown has the best evidence-to-cost ratio of any control.** pnpm 11 ships
`minimumReleaseAge` on by default at 1440 minutes. The counterfactual is clean:
Nx Console was live 11 minutes, chalk/debug 2.5 hours, Shai-Hulud ~12 hours. A
24-hour hold blocks all of them. No allowlist to maintain, no behaviour change.
It works precisely *because* detection is fast — cooldown converts the ecosystem's
detection speed into your protection.

**Do not build a verified-publisher badge.** Google announced on 2026-08-20 that it
is sunsetting the "Featured" badge because it became "a less meaningful signal."
Microsoft's verified-publisher badge is forgeable. VS Code's Established Publisher
badge is held by ~75% of the store — a signal three-quarters of a population carries
is not a signal. And signing is forensics, not prevention: ChainDrop shipped with
**genuine SLSA provenance recorded in Rekor**, and the Nx Console payload carried
full Sigstore integration so that stolen OIDC tokens could produce validly signed
malicious publishes.

**The incentive shape sets the fraud rate more than the detection does.** LooksRare
paid rewards proportional to trading volume and got ~98% fake volume. Blur excluded
wash trades from eligibility and rewarded listing and bidding rather than throughput
— low single digits by trade count. Same asset class, same period, two orders of
magnitude apart, and the difference is what was rewarded.

One more that is directly load-bearing here. Obsidian's May 2026 post explaining why
they rebuilt their review pipeline:

> "As coding agents accelerate the creation of plugins, the review queue was only
> getting longer."

Friday instances authoring and publishing workspaces is not a hypothetical future
stress on this system. It is the design goal, and it is the thing that broke the one
comparable ecosystem, eight months ago.

---

## 3. DEFECTS FOUND WHILE SURVEYING

None of these were reported by anyone noticing a symptom. All were found by reading
the class at once. Listed because several of them are load-bearing for the plan, and
two are the kind that quietly produce a wrong outcome that looks like a right one.

**The blast-radius gate is not in the path.** `check_blast_radius` (`boot_guard.py:560`)
and the `safe_mode()` check are called only from `apply_customization`
(`workspace_studio.py:200-205`) — and grep finds **no caller of
`apply_customization` anywhere in `src/`**. The live route path is
`workspace_chat_turn`, which calls `_apply_to_doc` directly at `:440`, skipping
both. The gate exists, is well written, and never runs.

**Marketplace purchases complete the wrong purchase.** `invoice_id` is generated at
`marketplace.py:424`, handed to the caller, and never persisted anywhere.
`complete_purchase` ignores it and instead does:

```python
"SELECT * FROM purchases WHERE buyer_agent=? AND status='pending' "
"ORDER BY created_at DESC LIMIT 1"
```

Confirming invoice A completes whatever the buyer's most recent pending purchase
happens to be. The docstring calls `invoice_id` an idempotency key. It is not one.

**No positrons have ever moved through a purchase.** `marketplace.py:486-487` calls
`_economy.spend(buyer, amount)` and `_economy.earn(seller, amount)`, but both
functions require `reason` positionally (`economy.py:286`, `:317`). Every call
raises `TypeError`, caught by the bare `except` at `:488` which prints
`"[marketplace] economy transfer failed"` — and the purchase still returns
`ok: True`. A user can complete a purchase, be told it worked, and have nothing
happen.

**Every marketplace ownership transfer fails verification.** `marketplace.py` signs
`asset_id + buyer + purchase["created_at"]` as raw bytes;
`ownership.verify_transfer_sig` (`ownership.py:338-356`) expects
`sha256(asset_id + to_key + timestamp).digest()` where `timestamp` is generated
*inside* `ownership.transfer()` at `:297` — a different value. Wrong payload, wrong
hashing.

**Listing signatures are never verified.** `_sign_listing` (`:159-169`) signs every
listing. Grep finds no verification call anywhere. `content_credential_hash` — the
field that would bind a listing to a real artefact — is hardcoded to `""` at `:244`
and never populated.

**`_verify_peer_card` never rejects.** `federation.py:275-304` prints on a failed
Ed25519 verification and falls through; the caller stores the peer regardless. The
call site's own docstring at `:246` claims it "raises on hard failure." It does not.

**`list_workspace_history` hands the model broken JSON.** `agent.py:1189`:

```python
return json.dumps(ws.history(wsid), default=str)[:2400]
```

`history()` returns up to 40 entries plus the full current customization. Truncating
serialised JSON at a byte count produces unparseable text, routinely.

**`undo_last` oscillates instead of walking back.** Each undo pushes a
`"before revert"` snapshot, so the second consecutive `undo_last` reverts to the
snapshot the first one just created. Calling undo twice returns you to where you
started.

**The publish form and the publish endpoint disagree on field names.** The UI POSTs
`{license, public}` (`ui_parts/app.html:3348`); the route reads
`{license_offered, visibility}` (`federation.py:302-321`). Every publish silently
gets the defaults regardless of what the user chose. The browse card reads
`creator_agent_id` and `license`; the backend returns `creator_pubkey` and
`license_offered`, so every listing displays "by unknown."

**Workspace ids collide.** `_ws_path` (`:49-51`) strips disallowed characters rather
than rejecting: `re.sub(r"[^a-z0-9_-]", "", ...)`. No traversal, but `my.workspace`
and `myworkspace` resolve to one file.

**`routes/workspace_studio.py` has no `@login_required`** on any of its six routes,
where `routes/federation.py` decorates every one of its. The global `before_request`
covers it today, so this is defence-in-depth missing rather than a hole — but it is
the wrong side of the line for routes that will soon accept executable content.

---

## 4. THE DESIGN

### 4.1 Two tiers, named honestly

**Native workspaces** are the 22 that exist. They are React components inside
`index.html`, they run in Friday's origin, they have the whole API, and they are
written by whoever ships Friday. They are not migrating. Rewriting 22 working
surfaces to buy uniformity is a large risk for an aesthetic gain, and it would block
everything else for weeks.

**Bundle workspaces** are the new thing. They are directories with a manifest, they
run in a sandboxed iframe with no access to Friday's origin, and they reach the rest
of the app only through a broker. Anything shared, installed, agent-authored or
vibe-coded is a bundle.

Two classes of citizen, and the UI says which is which rather than pretending
otherwise. A native workspace carries no capability list because it has everything;
a bundle carries one because it doesn't. That asymmetry is the honest statement of
the trust model, and hiding it would be the lie.

### 4.2 The bundle

```
~/.friday/workspaces/<id>/
  manifest.json
  index.html          # the whole UI, one file, no build step (house convention)
  icon.svg
  README.md
  .signature          # detached Ed25519 over the canonical hash of the above
```

```jsonc
{
  "id": "rent-tracker",
  "name": "Rent Tracker",
  "version": "1.2.0",
  "author": { "name": "...", "pubkey": "<ed25519 hex>" },
  "authored_by_agent": true,           // declared, and shown; see §4.7
  "friday_api": 1,
  "capabilities": {
    "read":  ["calendar.events", "wiki.read"],
    "write": [],
    "network": ["none"],               // or explicit hosts; "*" demands `reasoning`
    "reasoning": "..."                 // REQUIRED when network is "*" or localhost
  },
  "integrity": { "sha256": "..." }
}
```

Three things are deliberate here.

**The manifest is an upper bound, not a grant.** It says the most this workspace
could ever ask for. What it actually holds is whatever the user has granted, which
starts at nothing. This is Chrome MV3's real contract and the piece most manifest
designs get backwards.

**`reasoning` is required exactly where the machine-checkable declaration goes
wide.** Stolen from Figma, which requires it only when `allowedDomains` contains
`"*"` or a localhost address, and then publishes it on the listing page. WASI has
enforcement with no human-legible intent; Figma has intent with partial enforcement.
Nobody has both. Requiring prose precisely at the point where the declaration stops
being informative is cheap and nobody has done it.

**One HTML file, no build step.** Matches how `index.html` already works and how
`docs/development/ui-build.md` says to check it. A bundle a person can read is a
bundle a person can review.

### 4.3 The sandbox

```html
<iframe sandbox="allow-scripts" srcdoc="..." csp="default-src 'none'; ..."></iframe>
```

**`allow-scripts` without `allow-same-origin`.** That combination gives the frame an
opaque origin: no `document.cookie`, no `localStorage`, no `fetch` to
`127.0.0.1:3000` that carries the session, no reach into Friday's DOM. It is the one
line in this document that does the actual work, and the reason §0 asked its
question first.

The cost is real and should be stated: no build step, no npm, no React inside the
frame unless the bundle inlines it, and debugging is worse. Figma paid a bigger
version of this bill and considered it worth paying. So do I.

Everything crosses by `postMessage`, envelope-wrapped Figma-style so a message
cannot be confused for a native one, and every payload is `structuredClone`-able
data — never a function, never a reference.

### 4.4 The broker

Host side. Holds all authority. One module, one place to audit.

```
frame                      broker                         Friday
─────                      ──────                         ──────
postMessage ──▶  is this capability declared?
                 has the user granted it?     ──▶ /api/calendar/events
                 does the grant still hold?
                 record it                     ◀── response
             ◀── structured data only
```

Four properties:

1. **A capability the manifest did not declare is refused before anything else
   runs.** Declaration is necessary, not sufficient.
2. **A capability the user has not granted is refused, and asks.** At the moment of
   use, naming the workspace and what it wants. This is the contextual prompt the
   2015-2017 field studies say recovers efficacy that install-time review never had.
3. **The broker returns data, never a handle.** A workspace that reads calendar
   events gets events. It never gets a fetch, a token, or a path.
4. **Every call is logged**, per workspace, and the log is readable in the UI.
   Felt et al.'s first-ranked mechanism is automatic grant *paired with auditing* so
   a user can trace an annoyance to its source. The audit is what makes the cheap
   grant safe.

`extension_security.py` already does per-call audit logging for MCP
(`audit_tool_call`, `:121-138`). The broker should use the same log, not a second
one.

**Capabilities Friday will not broker, at any grant level:** the vault, the
credential store, `run_command`, agent tool invocation, sending mail, the keystore,
and anything the `BLAST_RADIUS_FORBIDDEN` list already names
(`boot_guard.py:68-72`). Not "gated" — absent from the broker's vocabulary. A gate
is a thing that can be misconfigured open.

### 4.5 Installation

Borrowed wholesale from the one ecosystem with a clean record.

**Installed is not enabled.** A bundle arrives disabled, with zero capabilities
granted, and does not appear in the dock. Enabling is a per-workspace action that
shows the manifest, the `reasoning` prose, the capability list, and who signed it.

**Cooldown on install and on update.** A version published less than 24 hours ago is
not installed; Friday offers the previous qualifying version instead — pnpm's
`minimumReleaseAgeStrict: false` behaviour, which degrades rather than fails.
Configurable, default on. This is the single cheapest control available and it would
have caught every named incident in the survey.

**Updates re-consent when capabilities widen.** Not on every update — that is the
habituation failure. Only when the new manifest asks for something the old one did
not. This is aimed squarely at the documented structural attack: declare broadly,
ship the code later, no warning fires.

**Signatures are forensics.** `governance/proof_of_integrity.py` is real and already
does Ed25519 with an honest degradation path (`"ed25519_unavailable"` propagates as
a distinct value rather than silently passing). Sign bundles with it. Record who
signed what. **Do not treat a valid signature as a trust terminal** — two in-window
incidents shipped valid attestations, and one shipped a payload that generated more.

**No publisher badges.** See §2.

### 4.6 Positrons and negatrons

The evidence runs against a soft currency as a *price*, and Roblox ran the
experiment: in April 2024 they pulled Robux out of their own small-artefact
marketplace, explicitly because it produced a race to the bottom where "robust
plugins [were] really hard to justify making," and moved creators from roughly 25%
to ~90% share by switching to real dollars. Meanwhile the badge/points literature
has been revised down — Hoernle et al. find ~20% of users respond, and identify
"Phantom Steering," a statistical artifact that inflated the original 2013 estimates
— and **nobody has ever tested whether points are load-bearing at all.** There is no
control group anywhere in that literature.

So: **positrons are not a price.** Nothing costs positrons. A workspace is free to
install.

What positrons are is a record of contribution that is expensive to fake, and the
design rule comes from LooksRare versus Blur: reward the hard-to-fake signal, never
throughput. Concretely — earn on *sustained installed use by distinct machines over
time*, not on downloads, not on stars, not on listings published. A download is one
HTTP request. A machine that still has your workspace enabled ninety days later is
not.

**Negatrons are where the design gets interesting, and Friday already has them.**
`economy.py:7-9` defines negatrons as the cost-and-obligation side of the pair. That
is a gift, because EVE's actual lesson — nineteen years of published monthly
economic reports — is that a currency survives when its principal sink *is the core
loop*. Destroyed ships must be rebought.

Friday has a real sink sitting right there: a workspace that calls a cloud model on
every render costs the user money, and `cost_meter.py` already measures it in USD
per token. Meter a bundle's brokered consumption in negatrons. A workspace's listing
then shows what it *costs to run*, measured, not claimed — which is a number no
competing marketplace can show, and which is exactly the hard-to-fake signal the
positron side needs.

Net charge Q — `psi_balance - eta_balance`, already computed at `economy.py:154-159`
— becomes a real statement: contribution minus consumption.

**And none of this gets built until §5 Phase 4 has shipped and shown real traffic.**
The agentic-payments layer this would sit in is, on current public numbers, mostly
not there: x402 daily volume fell ~93% between January and September 2026 with
roughly half of what remains self-dealt; Stripe and OpenAI's Instant Checkout
shipped in September 2025 and was killed in March 2026; the best-documented
autonomous agent earning real money in a real marketplace (XBOW on HackerOne) loses
money once compute is counted. Build the permission and provenance primitives so an
agent-authored workspace is a first-class citizen — *that* part is real. Treat
agent-to-agent payment as a hook to leave room for, not a mechanism to build on.

### 4.7 Agent-authored workspaces

Obsidian's review queue broke in May 2026 because coding agents accelerated plugin
creation faster than humans could review. For Friday that is not a risk to mitigate,
it is the stated goal — the owner's framing is that "unique agents will come up with
innovations that they can share."

Three consequences, and they are the reason `authored_by_agent` is in the manifest:

**Declared, and shown.** Not as a warning. As a fact, the way a byline is a fact.
**Every version gets machine review** — the initial submission is not the
interesting one; Nx Console and `postmark-mcp` both passed clean reviews and went
bad on a later version. **Human review narrows to the flagged minority**, which is
what Obsidian moved to and what actually scales.

A human sponsor requirement for agent-published bundles is a live option and a
product call, not an engineering one. It is in §6.

---

## 5. PHASES

Ordered so that every phase ships something usable on its own, and so that nothing
is built on the broken parts.

### Phase 0 — Dock settings and the version timeline

Everything asked for in the first two sentences. Needs no new architecture and
creates no new security surface.

- A **Dock** tab in `SettingsWS` (`TABS` at `index.html:39279` *and* a matching
  branch at `:39411` — omitting one is the documented failure mode recorded in the
  comment at `:39292`). Per-workspace show/hide, drag reorder, live preview of the
  dock as you edit it. New settings keys need a `DEFAULT_SETTINGS` entry and a real
  reader or `scripts/check_settings_readers.py` fails the commit.
- **Version timeline UI** over the history API that already exists: a scrubbable
  strip in the workspace's own window title bar and in the Dock settings tab,
  showing each snapshot's label and timestamp with the existing prose
  ("state BEFORE: …"), a preview, and restore. Reachable from both places the owner
  named.
- Keep `show_all_workspaces` working as the coarse switch it is; per-item settings
  layer over it.

### Phase 1 — Fix the foundation

The §3 list. Specifically and in order: route `workspace_chat_turn` through the
blast-radius gate that already exists; make `list_workspace_history` return
structured, bounded data instead of truncated JSON; make `undo_last` walk backwards;
reject rather than strip in `_ws_path`; add a CSP and `@login_required` to the
workspace routes.

Separately, a call I am making rather than asking about: **the marketplace purchase
path gets disabled, not repaired.** Four independent defects that each silently
produce a success where nothing happened is not a thing to patch — and there is
nothing to sell yet, because there is no artefact format. It becomes a catalogue
until Phase 4 gives it something real to carry. The listing and signing code is
sound and stays.

### Phase 2 — The bundle host

The architecture. Manifest schema, loader, sandboxed iframe host, the broker, the
capability grant UI, the audit log. Ships with **one** bundle workspace written
in-house as the proof, and the honest measure of success is that the proof workspace
is genuinely useful while provably unable to reach the vault.

`FWin` (`index.html:4694-4967`) hosts it unchanged — it takes `{id, title, emoji,
children}` and knows nothing about workspaces beyond the id string. The `wsMap`
miss already renders "Coming soon"; that becomes "render the bundle host."

### Phase 3 — The Forge

The dedicated UI-building workspace, opening in its own tab. Live sandboxed preview
on the left, Friday on the right with the manifest and source in context, the
version timeline along the bottom. It writes bundle files, not CSS patches.

This is also where `code_engine._run_claude_terminal` needs a hard look before it is
wired to anything community-facing — it currently spawns Claude Code with
`--dangerously-skip-permissions` (`code_engine.py:44-86`), which is defensible for
the owner's own repos and not defensible as a path a shared workspace can influence.

### Phase 4 — Sharing

Export and import a signed bundle as a file first — the thing you send someone. No
registry, no server, no economy. Install flow, cooldown, consent, update path,
re-consent on capability widening. If a file you can email works, a registry is a
distribution detail. If it doesn't, a registry would not have saved it.

### Phase 5 — Registry, then economy

Only on evidence from Phase 4. The economy last, for the reasons in §4.6.

---

## 6. THE FOUR PRODUCT CALLS, AND WHAT I RECOMMEND

Sequencing and architecture above are mine and I own them. These four are product
intent. Recommendations follow, with the reasoning and the caveat on each, because
a recommendation without its own objection is a sales pitch.

### 6.1 How open, day one?

**Recommendation: neither a file nor a registry. Share it over the federation layer
that already exists and has never carried anything real.**

The obvious answer is "a `.friday-workspace` file you email, and a registry later,"
and that is what Phase 4 was drafted as. On a second pass it is the wrong shape for
this codebase specifically.

`services/federation_transport.py` is real crypto that works: Ed25519 seed converted
to X25519, ECDH, HKDF key derivation, `encrypt_message`/`decrypt_message`, rate
limiting, `send_to_peer` over the wire (`:120`, `:139`, `:232`, `:309`, `:424-443`).
`services/federation.py` does a genuine three-check handshake — cLaws hash, Ed25519
signature, and agent_id↔pubkey match (`:521-578`). All of it built, none of it
carrying a payload anyone cares about.

A workspace handed from one Friday instance to another over that channel is
strictly better than a registry for v1, on four counts. The trust context comes
free, because you already know who the peer is and the handshake already proved it.
There is no hosting, no moderation queue, no abuse desk, no takedown process — the
commitments that never end. It is the FutureSpeak thesis doing something instead of
being a slide: agentic federation, value moving because trust is provable, with an
artefact actually moving. And it degrades gracefully to a file, because a signed
bundle is a signed bundle whether it arrives over a socket or over email.

A registry then stops being a separate decision. It is the same bundle with a URL
and a search index, added when there is evidence anyone publishes.

**Caveat, and it is a hard blocker:** `_verify_peer_card` never rejects
(`federation.py:275-304` — it prints on a failed signature and falls through, while
its own call site's docstring at `:246` claims it raises). Sharing executable
content over a handshake that cannot fail is worse than sharing a file. That fix
moves from "Phase 1 defect list" to "prerequisite," and the honest cost of this
recommendation is that Phase 4 now depends on the federation path being trustworthy,
which today it is not.

### 6.2 Do community workspaces get network access?

**Recommendation: `"network": ["none"]` for v1. Brokered only. No exceptions, no
declared-host escape hatch, not yet.**

Egress control is the only layer in the entire survey that survives a valid
signature, and that is not theoretical — ChainDrop shipped with genuine SLSA
provenance recorded in Rekor, and the Nx Console payload carried full Sigstore
integration, and both of them still needed an outbound connection to be worth
anything. Meanwhile Figma, who did this properly, documents their own leak: network
limits "only apply directly to the website's domain. Network access limits do not
affect resources needed by that website." Even the sophisticated version has a hole
in it.

The Friday-specific argument is the stronger one, though. Most workspaces worth
having here do not want the internet. They want *your* data — calendar, wiki, mail,
the knowledge graph — arranged differently. That is precisely what the broker is
for. A weather panel is the real counterexample, and its correct answer is a brokered
`http.get` capability with a declared host list, which is a clean v2 feature with an
obvious shape.

And this is exactly why the boundary goes in the sandbox rather than in policy:
policy can be loosened later without re-architecting anything. Starting closed costs
one feature class and buys the ability to open deliberately. Starting open cannot be
reversed without breaking whatever shipped in the meantime.

**Caveat:** this will feel arbitrary to the first person whose workspace needs an
API. The mitigation is to ship `http.get`-with-allowlist as a named, dated Phase 5
item rather than as a vague "later," so the ceiling reads as sequencing instead of
refusal.

### 6.3 Positrons as reputation, or as a price?

**Recommendation: neither. Positrons price trust, not artefacts. You stake them to
vouch for a workspace, and you lose them when it goes bad.**

§4.6 argued reputation over price on Roblox's evidence, and that holds — in April
2024 Roblox pulled their own soft currency out of their own small-artefact
marketplace because it produced a race to the bottom where "robust plugins [were]
really hard to justify making." But "reputation" as usually built is a scoreboard,
and the badge literature has been revised down to roughly 20% of users responding,
with part of the original 2013 effect identified as a statistical artifact. A
scoreboard is weak.

Staking is better, and it is better *because it is the FutureSpeak claim taken
literally*. Proof of Integrity says value moves because trust is provable. It does
not say artefacts have prices. So: nothing costs positrons to install. What
positrons buy is the right to say "I vouch for this," backed by something you can
lose.

That single mechanism does four jobs at once:

- **It is the sink.** Every soft currency that died, died of having sources and no
  sinks. Vouching burns on a bad outcome. EVE's whole nineteen-year lesson is that
  the sink has to be the core loop, and here it is.
- **It is the anti-sybil layer.** A sybil has no positrons to stake, because
  positrons are earned on sustained installed use by distinct machines over time —
  the hard-to-fake signal, not throughput. This is Blur versus LooksRare: reward
  listing and bidding while excluding self-trades and you get single-digit fraud;
  reward volume and you get 98% fake.
- **It is the sponsorship mechanism** for §6.4, which is why that question turns out
  not to need its own machinery.
- **It gives the listing a number nobody else can show**, when paired with the
  negatron side: what a workspace costs to run, metered through the broker against
  `cost_meter.py`'s real per-token accounting, rather than claimed.

**Caveat, and it is the one that decides whether this works:** the meta-lesson from
every anti-sybil programme in the survey is that *the adjudication layer gets
attacked.* LayerZero's sybil-reporting phase drew 3,000 reports and 30,000 appeals
within hours and had to be paused, because people were reporting their accusers to
get reports withdrawn. So the staking resolution must never be a vote or a community
judgement. It resolves only on machine-checkable events: a version failing automated
review, a capability-widening update, a bundle pulled for cause. If it ever needs a
human to adjudicate who was right, it has become the attack surface.

**Second caveat, stated plainly: this is an economy with no users.** Design the
fields now — `authored_by_agent`, the audit log, the negatron meter — so the data
exists when there is traffic. Build the staking machinery last. Every developer-tool
marketplace in the survey (VS Code, Open VSX, Chrome Web Store, Obsidian, Raycast,
npm) has no payment rail, and several killed one deliberately.

### 6.4 Can an agent publish without a human sponsor?

**Recommendation: yes. Any agent can publish. Publishing requires a stake, and an
agent that has not earned one borrows it from its principal.**

This falls straight out of §6.3, which is the point. The wrong version of a
"human sponsor" requirement is a human reviewing the content — that does not scale,
it is the exact thing that broke Obsidian's queue in May 2026 ("as coding agents
accelerate the creation of plugins, the review queue was only getting longer"), and
it makes agents second-class in a product whose entire thesis is that they are not.

The right version is that somebody with something to lose is on the hook. A Friday
instance that has published good workspaces has its own positrons and stakes them
itself. A fresh agent has none, so its principal stakes for it. Same mechanism, no
special case, and the human involvement is proportional to how much the agent has
already proved.

`authored_by_agent` stays in the manifest and is displayed — as a byline, the way a
byline is a fact. Not a warning badge. Friday is an agent; a product that
warning-labels its own kind is arguing against itself.

**Caveat:** every version gets machine review, not just the first. Nx Console and
`postmark-mcp` both passed clean initial reviews and went bad on a later version, and
this is the failure mode that pre-publication review cannot see by construction.
Obsidian's fix — automated review of *every* version, with human review narrowed to
the flagged minority — is the one to copy, and it is a Phase 5 commitment that
should be written down now so it is not discovered later.

### 6.5 The through-line

All four answers are the same answer. **Put the boundary where it cannot be
misconfigured, and make the social layer cost something to abuse.** The sandbox is
the boundary; the stake is the cost. Everything else — review, signatures, badges,
scores — is evidence for humans, not enforcement, and the survey is unanimous that
treating evidence as enforcement is how these systems fail.

---

## 7. WHAT WOULD FALSIFY THIS

Stated up front so it can be checked rather than argued.

- **The sandbox costs too much.** If a genuinely useful workspace cannot be built
  inside `sandbox="allow-scripts"` with brokered data only, the two-tier model
  collapses into "native or nothing." The Phase 2 proof workspace is the test, and
  it should be built to fail this rather than to pass it.
- **The broker becomes a second API surface.** If the capability list grows to
  mirror `/api/*` one-for-one, the boundary is nominal. A capability count that
  climbs past ~20 in the first year is the signal.
- **Nobody publishes.** Every developer-tool marketplace in the survey — VS Code,
  Open VSX, Chrome Web Store, Obsidian, Raycast, npm — has **no payment rail**, and
  several killed one deliberately. If Phase 4 file-sharing sees no traffic, Phase 5
  is building a market for a thing nobody makes.
- **Cooldown is felt as breakage.** If 24 hours produces more complaints than it
  prevents incidents, it is wrong for a single-user tool and should be reconsidered
  — the evidence is drawn from ecosystems with millions of installs, and it may not
  transfer to one machine.
