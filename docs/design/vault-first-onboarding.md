# Vault-first onboarding — what Friday collects, and how she asks

**Status:** spec. No implementation. Open questions `Q-V1`–`Q-V9` await Stephen.
**Date:** 2026-08-29
**Branch at time of writing:** `fix/janet-backport-5.6.4`, HEAD `c60172a`
**Prompted by:** Stephen, 2026-08-29 — "setting up the vault should be prioritized during the installation process. Users need to be aware that Friday will collect a lot of info about them… The setup definitely needs to explain the whole point of our architecture is to allow this to occur but to protect it from exposure to the cloud."

**Method.** STORM: ground truth first, then a multi-perspective interrogation of
the design, then synthesis. Every factual claim below carries a `file:line`. Four
claims were checked by *running the code* rather than reading it; §11 says which
and shows the output. Where the code contradicted the brief, the code won, and
§9 says so plainly.

---

## 0. The short version

Three things are true, and the second and third are the reason this document is
long.

1. **The vault step is last, not first.** In the terminal wizard it is step 6 of
   10 (`setup_wizard.py:706`). In the app wizard it is an optional password box
   in the bottom third of the final screen, below the summary and above the
   Launch button (`index.html:30721`). Stephen's instinct is right and the fix is
   cheap.

2. **The onboarding copy that exists today is not true.** The terminal wizard
   tells every user, on the welcome screen and again on the provider screen, that
   "your private information never leaves your device"
   (`setup_wizard.py:459`) and that with Ollama installed "nothing leaves your
   machine" (`setup_wizard.py:452`). Neither statement survives contact with the
   router. The first describes a keyword filter with documented gaps; the second
   describes a routing mode the wizard does not set.

3. **"The full feature set is still available in cloud-only mode" is false.**
   Measured, not argued: in cloud-only mode with the factory settings, a question
   about a bank balance is withheld entirely and never answered, and a question
   containing a phone number is withheld entirely and never answered. Tier B of
   the knowledge graph produces nothing at all. §4 has the list and §11 has the
   transcript.

The spec in §7 is written so that a user who chooses cloud-only does so without
embarrassment and without a surprise later.

---

## 1. Ground truth — the wizard as it exists today

### 1.1 There are three onboarding surfaces, not one

| Surface | File | Reached when | Status |
|---|---|---|---|
| Terminal wizard | `src/agent_friday/setup_wizard.py` (1,197 lines) | Installer step 12 runs `python -m agent_friday.cli setup` in the foreground (`packaging/windows/install.ps1:795`) | **This is what a new installed user meets.** |
| App wizard (React) | `index.html:29834`–`~30800` | `/api/setup/status` returns `initialized: false` (`index.html:36003`, `routes/core_routes.py:576`) | Reached only if the terminal wizard was cancelled, failed, or ran `--unattended` |
| Voice-first state machine | `src/agent_friday/services/onboarding.py` (226 lines) | Never | **Dead.** `grep -n "api/onboarding" index.html` returns nothing. The blueprint is registered (`server.py:86`) and the endpoints answer, but no client calls them. |

The terminal wizard writes `~/.friday/.setup_complete` (`setup_wizard.py:974`).
The app wizard is gated on that marker's absence. So on a successful install the
app wizard **never renders** — which matters, because the app wizard is the only
one of the three with a hardware check and the only one that stores the vault
passphrase in the OS keychain rather than a batch file.

> **Q-V1.** Three surfaces, one of them dead, two of them disagreeing about where
> secrets go. Should the terminal wizard be reduced to "install, then open Friday"
> and the app wizard become the single onboarding? Everything in §7 is written to
> work either way, but building it twice is how the current disagreement happened.

### 1.2 The terminal wizard, step by step

`total_steps` is 10 in full mode and 6 in `--quick` (`setup_wizard.py:1063`).
Nine `_header()` calls exist and two of them are both numbered 7
(`setup_wizard.py:759` and `:784`), so the progress bar advertises a step that
does not exist and repeats one that does.

| # | Step | Default | Writes | Notes |
|---|---|---|---|---|
| — | `step_welcome` (`:391`) | — | nothing | Carries the two false privacy claims. See §3. |
| 1 | `step_name` (`:426`) | `AGENT FRIDAY` | `agent_name` | Upper-cased. |
| 2 | `step_provider` (`:475`) | `1` → `anthropic` | `provider` | Options 2 and 3 (OpenAI, Ollama) print "coming in v5" and silently return `anthropic` (`:497`). **A user cannot choose local here.** |
| 3 | `step_model` (`:501`) | first row | `orchestrator_model` | Lineup resolved live from the discovery cache. |
| 4 | `step_creative_engine` (`:526`) | first row | `creative_model` | |
| 5 | `step_brain` (`:549`) | `3` (both) if capable | keys via `credential_store`, and into `config` | The best screen in the file. Asks the hardware what it can do and says so. |
| 6 | `step_vault_password` (`:703`) | **generate one** | `vault_password` → `start.bat` | See §1.6. |
| 7 | `step_voice_engine` (`:752`) | `local` | `voice_engine` | |
| 7 | `step_voice` (`:782`) | `Aoede` | `tts_voice` | Duplicate step number. |
| 8 | `step_scene` (`:806`) | auto-rotate | `preferred_scene_index` | |
| 9 | `step_connectors` (`:855`) | — | `connectors` | Reports status honestly; asks nothing. Correctly rewritten after Janet's install. |
| — | `step_summary` (`:923`) | save | — | |

After the last step the wizard computes `model_routing` from the provider answer
(`_routing_block_for`, `:236`). Because step 2 cannot return `ollama`, the block
it writes is always `mode: "cloud_only"` (`:271`).

**So the terminal wizard cannot produce a local-first install.** A user who wants
one has to finish setup, open Friday, and change the mode in Settings.

### 1.3 The app wizard, step by step

`WIZARD_STEPS` (`index.html:29834`) is six entries: `welcome`, `profile`,
`providers`, `services`, `hardware`, `done`. Payload posted to
`/api/setup/complete` is `wizData` (`index.html:35987`): `agent_name`,
`communication_style`, `preferred_scene_index`, `distribution`, `providers`, and
— added later, and only bound on the final screen — `vault_passphrase`
(`index.html:30730`).

There is **no privacy step, no routing-mode step, and no statement of what
Friday will record.** The vault appears as a purple box captioned "🔐 ENCRYPT
YOUR VAULT (RECOMMENDED)" with the sub-line "Leave blank to set it later in
Settings → Privacy", positioned under the configuration summary on the *Ready to
Launch* screen, immediately above the button that dismisses the wizard forever.

The backend for it is already correct: `/api/setup/complete` puts the passphrase
in the OS keychain under `agent-friday / vault-passphrase` and sets
`FRIDAY_VAULT_PASSPHRASE` for the live process without a restart
(`routes/core_routes.py:631`). The plumbing is not the problem. The placement is.

### 1.4 The voice-first state machine

`services/onboarding.py:31` — `greet → name → voice_test → keys → identity →
soul → done`. It is the friendliest of the three and it is unreachable. Its
opening line is `"Hi, I'm Friday. I'm your personal AI, and I run right here on
your computer — no cloud required."` (`:34`) and its keys line is `"everything
works locally without them"` (`:50`). On a machine with no GPU both sentences are
false; see §2.4 for the hardware floor.

### 1.5 `_existing_user()`

Stephen asked which steps consult it. **None do.** It is defined at
`setup_wizard.py:368` and called exactly once, at `:1044`, as a whole-wizard
gate in `main()`: if it returns true and `--force` was not passed, the wizard
prints "Existing installation detected" and exits.

Its four signals are the marker file, an API key in `config.yaml`/`settings.json`,
`ANTHROPIC_API_KEY`/`GEMINI_API_KEY` in the environment, and the presence of
`start.bat` / `friday_startup.bat` / `friday_startup.vbs` beside the package
(`:376`). The last one looked like a false-positive risk on a fresh install —
the installer creates its launchers at step 10, before the wizard runs at step 12
— but it is not: `Install-LauncherScripts` writes `.cmd` files and explicitly
declines to reproduce the `start.bat` pattern (`packaging/windows/lib/Shortcuts.ps1:309-320`).
The check is safe as shipped. It would fire on a source checkout where the
developer has ever launched Friday, which is the intended behaviour.

### 1.6 Two defects found while reading

**The wizard writes secrets to a plaintext batch file that the installer went out
of its way not to write.** `_persist()` (`:966`) strips only `vault_password`
from what it writes to `settings.json` and `config.yaml`; the Anthropic and
Gemini keys go in as plaintext. It then calls `_write_start_bat()` (`:993`),
which writes `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` and `FRIDAY_PASSWORD` into
`<install>/start.bat` in the clear. The installer's own launcher code says of
exactly this: *"They contain no keys. Keys live in Friday's encrypted credential
store via the setup wizard. The legacy start.bat pattern … is deliberately not
reproduced here"* (`Shortcuts.ps1:316`). The installer then runs the wizard,
which reproduces it.

This is also the mechanism behind the 5.6.3 bug where a key replaced in Settings
did not survive a restart: `core` re-bootstraps its environment from the launch
scripts on every launch, so the wizard's original key outlived every
replacement.

**The vault screen recommends defeating its own threat model.** `step_vault_password`
(`:703`) tells the user the passphrase protects data "even if your disk is
accessed by another user or process", then offers, defaulted to yes, to
*"Generate a random passphrase for me? (saves it to start.bat)"* (`:720`),
prints the passphrase to the terminal, and writes it next to the API keys in the
same readable file. Anyone who can read `~/.friday/vault` can read `start.bat`.

Stephen's own machine has `friday_startup.bat` carrying `FRIDAY_PASSWORD=` in the
clear at line 12. The value is not reproduced here.

> **Q-V2.** Should `_write_start_bat` be deleted outright, given the installer
> ships `.cmd` launchers and `credential_store` already holds the keys? The only
> thing that would break is a source checkout launched by double-clicking
> `start.bat`.

---

## 2. Ground truth — what Friday actually collects

This is the inventory the onboarding has to be able to describe truthfully.

### 2.1 The stores

| What | Where | Created by | Automatic? |
|---|---|---|---|
| **Wiki** — Markdown pages about projects, people, work | `~/.friday/wiki` (`core/__init__.py:637`) | User and Friday's tools | Written by Friday during ordinary conversation |
| **Knowledge graph** — entities and relationships derived from the wiki, conversation turns, cognitive memory and `SOUL.md` | `~/.friday/knowledge-graph` (`knowledge_graph/__init__.py:23`) | `indexer.py` | **Yes.** `enabled: True`, `nightly_reindex: True`, all four sources on (`knowledge_graph/__init__.py:28-45`). Runs nightly at 03:30 (`services/notifications.py:555`). Entity types are `person,organization,project,tool,concept,event,place` (`indexer.py:45`) |
| **Conversation memory** — every user and assistant message, embedded and searchable months later | `~/.friday/memory/conversations/` (ChromaDB) (`conversation_memory.py:1-17`) | server, off the hot path | **Yes**, whenever `chromadb` is installed — which the installer does at step `deps.memory` |
| **Cognitive memory** — a hash-chained, append-only fact ledger | `~/.friday/memory/memory_ledger.jsonl` (`cognitive_memory.py:1-11`) | consolidation | Yes |
| **Memory dreaming** — nightly consolidation of the day's turns into durable facts | `~/.friday/dreams.db`, `~/.friday/dreams/<day>.md` (`services/memory_dreaming.py:1-16`) | scheduler, 03:00 | Yes |
| **User model** — formality, verbosity, per-domain expertise, active hours, top tools | `~/.friday/user_model.db` (`services/user_model.py:1-18`) | heuristics, local only, no LLM | Yes |
| **People graph** — trust scores for humans across reliability, emotional safety, alignment, competence | `~/.friday/people_graph.json`, mirrored to `trust_graph.json` (`people_graph.py:1-33`) | `/api/trust/add-person`, `/api/trust/edit` | **No — manual only.** See §9.1 |
| **Source trust graph** — six-dimension reputation scores for news domains | `~/.friday/source_trust.json` (`source_trust_graph.py:1-40`) | every news fetch | Yes |
| **Provenance ledger** — signed C2PA-aligned credentials for everything Friday creates | `~/.friday/provenance/` (`services/provenance.py:1-20`) | on creation | Yes |
| **Context log** — append-only event log | `~/.friday/` | server | Yes. `context_logging_enabled: True`, `context_retention_days: 0` — **0 means keep forever** (`core/__init__.py:1452`) |
| **Vault** — finance, health, legal, family | `~/.friday/vault/{legal,finances,family}`, `~/.friday/finance`, `~/.friday/health` (`services/agent.py:4966`) | workspaces and tools | Yes |

### 2.2 What is encrypted at rest, and what is not

Encryption is AES-256-GCM with an Argon2id-derived key
(`privacy/vault_crypto.py:1-24`). The key comes from the OS keychain first, then
`FRIDAY_VAULT_PASSPHRASE`, then legacy `FRIDAY_PASSWORD`
(`services/agent.py:4852`, `core/__init__.py:288`).

**Encrypted** — and only these: `~/.friday/finance`, `~/.friday/health`, and
`~/.friday/vault/{legal,finances,family}` (`services/agent.py:4966`), plus any
wiki sections the user has explicitly named in `wiki_encrypted_sections`.

**Not encrypted:** the wiki (`wiki_engine.py:44` — *"the wiki is the user's
hand-editable knowledge base, so encryption is OFF by default"*, and
`wiki_encrypted_sections` is not in `DEFAULT_SETTINGS` at all, so the set is
empty), conversation memory, the cognitive-memory ledger, `dreams.db`,
`user_model.db`, `people_graph.json`, `source_trust.json`, the context log, and
chat history. Knowledge-graph records are a partial case: only records whose
provenance marks them TIER_2/TIER_3 are split into an encrypted sibling file, and
when no key is available those records are **not persisted at all** rather than
written in the clear (`knowledge_graph/store.py:7-13`).

**Setting the passphrase later is not a lost cause.** `_migrate_vault_plaintext()`
(`services/agent.py:4985`) runs at every boot when a key is available
(`server.py:653`), proves a decrypt round-trip before replacing each file, and is
idempotent. So a user who skips the passphrase and sets it in Settings a week
later gets the vault directories encrypted in place on the next start. That is
worth saying in the copy, because it converts "you have ruined it" into "you have
delayed it" — which is both kinder and true.

What it does **not** retroactively cover is everything in the "not encrypted"
list above. The most sensitive real-world leak this project has had —
contact PII reaching Anthropic on 2026-08-25 — came out of two plaintext wiki
files.

> **Q-V3.** Should the onboarding offer `wiki_encrypted_sections: ["health",
> "legal", "family"]` as a default when a passphrase is set? The machinery exists
> and the startup migration already handles those directories
> (`wiki_engine.py:63`). The cost is that those wiki files stop being readable in
> an external Markdown editor, which is the reason encryption is off there.

### 2.3 What crosses the network, by mode

The enforcement boundary is `services/egress_gate.py`, not the router. Its own
docstring is unusually honest about this and should be read by whoever writes the
final copy (`egress_gate.py:1-50`). `seal_outbound()` runs immediately before
every cloud HTTP call. Local providers bypass it entirely because nothing leaves
the device.

The gate classifies text into three tiers and **withholds whole fields rather
than masking spans** — safe, but blunt, and the blunt part is what a user
experiences as a missing answer. Image and document blocks are not gated at all,
deliberately: a text classifier cannot judge them (`egress_gate.py:26`).

| Mode | What runs where | What the provider sees |
|---|---|---|
| `local_only` | everything on this machine | nothing |
| `local_preferred` | local first, cloud when it helps | sealed payloads on the cloud legs |
| `smart` | classified per turn | sealed payloads |
| `cloud_only` **(factory default, `core/__init__.py:1646`)** | everything at Anthropic — **except** vault-tier turns, which are force-routed local if a local model exists (`routing/model_router.py:505`) | sealed payloads |

The vault force-route has a fallback, and its default matters more than anything
else in this section:

```
"vault_local_only": True,
"vault_cloud_fallback": "redact",     # core/__init__.py:1683-1684
```

`redact` means: when a vault-touching question arrives and **no local model
exists**, the turn goes to Anthropic anyway, `refuse: false`, and the egress gate
is the only thing standing between the user's finances and the network
(`routing/model_router.py:487`).

### 2.4 The hardware floor, so the copy can be specific

`services/model_plan.py:140`, with footprints computed rather than typed:

| Model | Download | On the card | Tools | Card needed (incl. 2.5 GiB display reserve) |
|---|---|---|---|---|
| `qwen3:4b` | 2.50 GB | 4.03 GiB | yes | **6.53 GiB** |
| `qwen3:8b` | 5.23 GB | 6.57 GiB | yes | 9.07 GiB |
| `gemma4:12b` | 7.56 GB | 8.74 GiB | yes | 11.24 GiB |
| `qwen3:14b` | 9.28 GB | 10.34 GiB | yes | 12.84 GiB |
| `qwen3:32b` | 20.20 GB | 20.51 GiB | yes | 23.01 GiB |

The bar for "local brain" is tool calling, not conversation
(`model_plan.py:708-721`) — an assistant that talks and remembers but cannot read
a file or touch a calendar is a different product, not a slower one. `gemma3:4b`
is in the table only so the planner can recognise it and explain why it declined
to use it.

**The entry rung needs roughly a 6.5 GiB discrete GPU.** Most laptops sold do not
have one. This is the whole reason §7's copy must not shame the cloud choice.

---

## 3. The claims audit — sentences shown to users today that are not true

Nothing in §7 reuses any of these.

| Claim | Where | Verdict |
|---|---|---|
| "Without Ollama: An egress gate redacts sensitive data before sending to cloud providers. **Your private information never leaves your device**, but redacted conversations may lose context." | `setup_wizard.py:456-460` (welcome panel) | **False as stated.** The gate is a keyword-and-pattern classifier with three documented recall gaps (`docs/audits/privacy-classifier-known-gaps-2026-08-25.md`) and a measured failure demonstrated in §11. "Never" is not a claim a pattern matcher can support. |
| "Ollama detected — sensitive conversations stay entirely on your device. **Nothing leaves your machine.**" | `setup_wizard.py:449-453` (`_show_privacy_posture`) | **False.** Ollama being *installed* does not change routing. The wizard writes `mode: "cloud_only"` regardless (`:271`), because step 2 cannot return `ollama`. A user who installs Ollama on this advice gets a green panel and cloud routing. |
| "A passphrase encrypts this data … so it cannot be read even if your disk is accessed by another user or process." | `setup_wizard.py:709-713` | **True of the ciphertext, defeated by the next prompt**, which writes the passphrase to `start.bat` in the clear (`:720`, `:993`). |
| "A passphrase encrypts private data (finance, health, notes) at rest… Stored in your OS keychain — never on disk." | `index.html:30726` | **True on this path.** Also the only path that is. |
| "I run right here on your computer — no cloud required… everything works locally without them." | `services/onboarding.py:34`, `:50` | **False on a machine below the hardware floor.** Currently harmless because nothing calls it. |
| "Friday's full feature set is still available in cloud-only mode." | Stephen's brief, and not yet in any UI | **False.** §4. |

---

## 4. Question 2, answered — cloud-only is not feature-complete

Stephen asked me to check this before writing it. It does not hold. Here is the
list, and §11 has the transcript.

**1. Vault-backed questions are refused, not degraded.** With the factory
defaults on a machine with no local model, `"Remind me what my Chase account
balance was last month."` classifies TIER_3 and the entire message is replaced
before it reaches Anthropic with:

> `[EGRESS-GATE: message withheld — it stayed on this device. Tell the user their last message contained sensitive content that is only processed locally, and that they can switch to the local model to discuss it.]`

The user asked a question and got told to install something. That is not a
degraded feature; it is an absent one.

**2. Ordinary contact details are refused too.** `"Call Dave at 555-0142 about
the Hartley contract."` classifies TIER_2 and is withheld whole — the phone
number *and* the contract question. The gate masks nothing; it withholds fields
(`egress_gate.py:36-40`). So in cloud-only mode, "draft an email to Dave" fails
the moment Dave's number is in context.

**3. Tier B of the knowledge graph produces nothing.** `indexing_mode` defaults
to `"local_only"`, which pins every extraction call to
`settings.model_routing.local_model` (`knowledge_graph/indexer.py:222-241`). On a
machine with no local model each call raises, is caught, and the chunk is skipped
(`indexer.py:361`). Tier A — structural links between wiki pages, no LLM — still
works. The semantic layer, which is what makes the graph interesting, does not.
It fails quietly.

**4. Offline means dead, not degraded.** `offline_auto_local: True`
(`core/__init__.py:1448`) switches routing to `local_only` when the network drops.
With no local model that is a mode with nothing in it.

**5. Voice is unaffected**, and worth saying so, because it is the one place the
cloud-only user loses nothing: the local engine (faster-whisper + Piper) runs on
any CPU and is the wizard's default (`setup_wizard.py:759`).

**What is genuinely the same in both modes:** every store in §2.1 is a local
file either way. Cloud mode changes where the *thinking* happens, not where the
*memory* lives. That distinction is the single most useful sentence available to
this onboarding and §7 leads with it.

> **Q-V4.** Should `vault_cloud_fallback` default to `"warn"` instead of
> `"redact"` when the install has no local model? `warn` refuses and explains;
> `redact` sends to the cloud and relies on the gate. Today the factory default
> is the one that transmits. Changing it makes cloud-only *more* visibly limited
> and *less* quietly risky, which I think is the right trade — but it is a
> product decision, not a bug fix, and it is Stephen's.

---

## 5. Question 3 — the people who were never asked

Friday accumulates records about people who are not her user. This is the part of
the pitch most likely to matter to a thoughtful person and it deserves the real
answer, which has three parts.

**Who actually gets recorded, and by what.** Stephen's framing puts this on the
trust graph. The code puts it somewhere else.

- The **people graph** — the thing that scores humans on reliability, emotional
  safety, alignment and competence — is **populated only by explicit user
  action**. Its writers are `/api/trust/add-person` and `/api/trust/edit`
  (`routes/contacts.py:188`, `:164`). Nothing in the tree calls `add_person`
  automatically. Gmail and Calendar do not feed it.
- The **knowledge graph** is where third parties actually accumulate, without
  being asked for. Its entity types include `person` (`indexer.py:45`), its
  sources include the wiki and every conversation turn
  (`knowledge_graph/__init__.py:36-41`), and it re-runs nightly at 03:30
  (`notifications.py:555`). Over a year that is a linked picture of the people
  around the user, built from things the user said in passing.
- The **source trust graph** scores news domains, not individuals
  (`source_trust_graph.py:1-40`). Peer-agent scoring is described as "eventually".

**They are exposed by the same gap the user is.** The classifier does not know
that a word is a name. Measured (§11): `"Emma's school pickup is at 3:15 and her
teacher is Ms. Alvarez."` classifies **TIER_1** and is transmitted to Anthropic
verbatim — a child's name, her schedule, and her teacher's name. `"She started
sertraline 50mg last month."` also classifies **TIER_1** and is transmitted
verbatim. Both are known gaps, recorded and deliberately not fixed
(`docs/audits/privacy-classifier-known-gaps-2026-08-25.md`), for reasons that are
good ones: a `\d+\s?mg` regex fires on health journalism, and adding `dr\.` to the
TIER-3 vocabulary re-breaks "Dr. Seuss".

The consequence stands regardless of the reasons. **In cloud mode, a sentence
about someone else's medication is no better protected than a sentence about the
user's own.** Any onboarding that claims otherwise is claiming something the
maintainers have already written down as untrue.

**There is no way to remove a person.** `people_graph.py` exposes `add_person`
and `edit` and nothing else — no `delete`, no `forget`, no retention window, no
expiry (`people_graph.py:59-244`). `source_trust_graph.py` is the same. There is
no `/api/trust/delete-person` route (`routes/contacts.py`). Evidence entries
append forever. `context_retention_days: 0` means the context log is kept
forever by default (`core/__init__.py:1453`).

So if a colleague asks the user to delete what Friday holds about them, today's
honest answer is: hand-edit `~/.friday/people_graph.json`, remember its legacy
mirror `trust_graph.json`, then find and remove the derived nodes in
`~/.friday/knowledge-graph`, then rebuild the index. That is not a feature; it is
a workaround, and it should be named as missing rather than papered over.

**What the design should do about it.** Three proposals, in the order I would
build them.

1. **A delete path, before anything else.** `PeopleGraph.forget(person_key)` that
   removes the node from both files, plus a knowledge-graph purge keyed on the
   entity title, plus a `DELETE /api/trust/person/<key>` and a button in
   Contacts. Without this, nothing else in this section is honest.
2. **Ageing for third-party evidence.** The source trust graph already decays
   observation weight at `0.95 ** weeks_ago` (`source_trust_graph.py:36-40`).
   The people graph does not decay at all. A person the user has not mentioned in
   two years should fade and eventually drop, not persist at full fidelity.
3. **A third-party register.** One screen, reachable from Settings, listing every
   person Friday currently holds a record about, where each record came from, and
   a delete control per row. This is the thing a user can actually show a
   colleague who asks.

> **Q-V5.** Is (1) a precondition for shipping this onboarding? My position is
> yes: telling a user "you are responsible for people who did not consent" while
> giving them no mechanism to act on that responsibility is worse than not
> raising it. But it is build work this spec does not otherwise require.

> **Q-V6.** Should the knowledge-graph indexer be able to exclude named people —
> a `~/.friday/kg-exclude.txt` the indexer honours? The obvious case is a minor.
> This repository's history includes a scrub of exactly that kind of data
> (`SCRUB_REPORT.md`, 2026-06-21).

---

## 6. STORM — the perspectives round

Six readers were put to the design. Each asks the question they would actually
ask. Answers are grounded in §1–§5.

### The non-technical first user

> *"I don't know what a vault is. Why are you asking me for a password before I've
> even seen the thing?"*

Correct, and it is the strongest argument against a literal reading of Stephen's
"vault first." A passphrase is meaningless before the user knows what is being
protected. The fix is not to move the vault later; it is to put **one screen
before it** that says what Friday will write down. Then the passphrase is an
obvious answer to a question the user now has. §7.2 orders it that way.

> *"If I pick the cloud, are you going to make me feel bad about it?"*

No, and the copy is tested against this. The cloud screen opens with "This is a
normal choice and most people will make it" and never uses the word "sacrifice".
§9.4 says why that word has to go from the pitch too.

### The privacy engineer

> *"Your onboarding is about to say 'local models keep your data off the cloud.'
> Which component actually enforces that?"*

The egress gate, not the local model. The router is an optimisation that runs
first and can be wrong or bypassed; the gate runs immediately before every cloud
HTTP call and cannot be bypassed without editing that module
(`egress_gate.py:8-13`). Local models help only when routing selects them, and
the factory routing mode is `cloud_only`. Copy that teaches "local model = safe"
teaches the wrong mental model and produces the exact failure in §3, row 2:
someone installs Ollama, sees a green panel, and changes nothing.

> *"What is your worst case?"*

Cloud-only, no local model, `vault_cloud_fallback: "redact"`, a vault question
whose sensitive term is a bare drug name or an unfamiliar person's name. The
router sends it to Anthropic, the classifier scores it TIER_1, and it goes over
the wire intact. Every step behaved as designed.

### The data-protection lawyer

> *"Your user is the data controller for their colleagues' personal data and
> almost certainly does not know it. What have you told them?"*

Today: nothing. §7's fourth screen is the first place this product says it out
loud. The screen deliberately does not use the phrase "data controller" or cite
any regulation — the target reader is a journalist with a laptop, not a DPO — but
it states the substance: other people's records accumulate, they did not agree,
and there is currently no delete button.

> *"'There is currently no way to remove a person' is a sentence you are willing
> to put in an onboarding flow?"*

It is the sentence that makes the rest of the screen credible. A product that
admits a specific missing capability is believed about the capabilities it
claims. See Q-V5 — I would rather ship the delete path and change the sentence.

### The plain-language editor

> *"Read the vault screen aloud. Where does the reader's attention go?"*

To the passphrase field, which is right, and then to the recovery warning, which
is also right and is the sentence most likely to make someone use a password
manager. What was cut in drafting: "military-grade", "AES-256-GCM + Argon2id" on
the first screen, "sovereign", and every instance of a bulleted feature list
where a sentence would do. The cipher name stays exactly once, on the vault
screen, because a technical reader will look for it and its absence would read as
evasion.

> *"How long is the whole flow?"*

Five screens before first use, three of which are one short paragraph. Longer
than today's, shorter than any terms-of-service anyone has read.

### The adversary

> *"I have physical access to this laptop for ten minutes. What do I get?"*

Without a passphrase: everything. The wiki, every conversation ever had, the
dreams, the user model, the trust graph and the finance and health directories,
all as readable files under `~/.friday`. With a passphrase set at install: the
finance, health, legal, finances and family directories are ciphertext; the rest
of that list is still readable. With a passphrase generated by the terminal
wizard's default path: everything, because the passphrase is in `start.bat`
(§1.6).

That third case is why §7 removes the generate-and-save-to-file option entirely
rather than fixing its wording.

### The person who is in someone else's graph

> *"Nobody asked me. What can I ask your user to do?"*

Today, realistically: nothing you can verify. There is no register you can be
shown and no button they can press. That is the honest state and §5 proposes the
three things that would change it. This perspective is in the list because it is
the one with no seat in the product, and the design should be answerable to it
anyway.

---

## 7. The spec

### 7.1 Principles

1. **Say what is collected before asking for a key to protect it.** Comprehension
   precedes consent; a passphrase prompt with no context is a dark pattern in the
   direction of security theatre.
2. **Never state a guarantee the code cannot keep.** Every sentence in §7.3 was
   checked against behaviour. Where the behaviour is imperfect, the copy says the
   imperfection rather than rounding it up.
3. **Cloud-only is a first-class choice, presented without penalty.** It is the
   only real option for most hardware. The copy normalises it in its first
   sentence and then states the differences precisely.
4. **One claim per screen, and a concrete example instead of a category.** "She
   will write a page about the person you met" beats "personal data may be
   processed."
5. **The secrets go where the app wizard already puts them** — OS keychain and
   `credential_store` — and nowhere else.

### 7.2 The step order

Five screens before the agent name, provider and cosmetic choices that already
exist. Screens 1, 2 and 4 are new; screen 3 replaces `step_provider` and
`_show_privacy_posture`; screen 5 is new and runs after first launch.

| # | Screen | Purpose | Writes |
|---|---|---|---|
| 1 | **What Friday writes down** | The accumulation, stated as fact, with the "it all stays in one folder" reassurance | nothing |
| 2 | **A passphrase for the private part** | The vault, now an obvious answer to screen 1 | OS keychain (`agent-friday`/`vault-passphrase`) + live `FRIDAY_VAULT_PASSPHRASE` |
| 3 | **Where your words go** | Hardware assessment, then the three-way choice | `model_routing.mode`, keys via `credential_store` |
| 3b | **What cloud mode changes** | Shown only if 3 chose cloud. The informed-consent screen | nothing; records `onboarding.cloud_ack` locally |
| 4 | **The part about other people** | Third-party responsibility and the missing delete path | records `onboarding.third_party_ack` locally |
| — | *existing steps: name, model, creative engine, voice, scene, connectors* | unchanged | unchanged |
| 5 | **Friday's first page** (after launch, not in the wizard) | Show rather than tell. See §8.2 | a real wiki page, or nothing if declined |

Screen 3 must be able to return `local_only` / `local_preferred`, which today's
`step_provider` cannot (§1.2). That is the one behavioural change the spec
requires outside the new screens.

### 7.3 The copy

Verbatim. Square brackets are controls or substitutions, not placeholder prose.

---

#### Screen 1 — What Friday writes down

> **Before anything else**
>
> Friday is meant to be useful the way a person who knows you is useful. That
> only works if she remembers things, so she writes things down.
>
> Over the first few weeks she will build a wiki — plain text pages about your
> projects, your work and the people you deal with, which you can open and edit
> in any editor. Every night she reads those pages and links them together into a
> map of how the parts of your life connect. She keeps every conversation you
> have with her, and can find something you said months ago. She builds a picture
> of how you work: your hours, the tools you reach for, the subjects you know
> well.
>
> All of it is a folder on this computer called `.friday`. You can open it, read
> it, back it up, or delete the whole thing.
>
> The rest of this setup is about who else can read it.
>
> `[ Continue ]`

---

#### Screen 2 — A passphrase for the private part

> **A passphrase for the private part**
>
> Some of what Friday keeps is more sensitive than the rest. Finance, health,
> legal and family records live in a separate place she calls the vault.
>
> A passphrase encrypts those files on this disk, using AES-256-GCM. Without one
> they sit there as readable text, and anyone who gets to this computer can open
> them — another account on the same machine, a backup service, whoever fixes it
> when it breaks.
>
> `[ Passphrase          ]`
> `[ Confirm passphrase  ]`
>
> This covers finance, health, legal and family records. It does not cover your
> wiki, your conversation history or the map she builds from them. Those stay
> readable on this disk. You can add wiki sections to the encrypted set later, in
> Settings.
>
> If you would rather not decide now, you can set this in Settings whenever you
> like. Friday encrypts whatever already exists the next time she starts, so
> nothing is stranded — but everything written before then will have spent that
> time readable.
>
> **If you lose this passphrase the files cannot be recovered.** There is no
> reset. Put it in your password manager, or write it down somewhere you would
> keep a spare key.
>
> `[ Set it ]`   `[ Skip for now ]`

*Notes for the builder.* No generated-passphrase option. No writing to any file.
On `Set it`, `POST /api/setup/complete` with `vault_passphrase`, which already
does the right thing (`routes/core_routes.py:631`). `Skip for now` records
`onboarding.vault_skipped = true` locally so screen 5 and the Settings banner can
mention it once, without nagging.

---

#### Screen 3 — Where your words go

> **Where your words go**
>
> Friday needs a language model to think with, and there are two places it can
> run.
>
> On this computer, where nothing leaves. That needs a graphics card with about
> 6.5 GB free for the smallest model that can still use her tools, and more for a
> better one.
>
> Or in the cloud, at Anthropic or Google, where your messages are sent over an
> encrypted connection and answered on their servers.
>
> `[ <assess()['reason'] — the existing sentence from setup_brain.py:102> ]`
> `[ <assess()['brain_label'] if any> ]`
>
> `( ) Cloud`  — Friday thinks at Anthropic. Fastest to set up, and the sharpest
> answers.
> `( ) On this computer only` — nothing is sent anywhere, ever.
> `( ) Both` — this computer by default, the cloud when it would clearly help.
>
> `[ Continue ]`

*Notes for the builder.* The recommendation marker follows
`setup_brain.assess()['capable']` and nothing else. Do not present "on this
computer only" as recommended on hardware that cannot run it, and do not hide it
either — a user with an eGPU or a plan to buy a card should be able to pick it.
Writing `model_routing.mode` is the point of this screen; `_routing_block_for`
(`setup_wizard.py:236`) already knows how to write the block safely without
clobbering its siblings.

---

#### Screen 3b — What cloud mode changes

Shown only when screen 3 chose Cloud.

> **What cloud mode changes**
>
> You have chosen to have Friday think at Anthropic. This is a normal choice and
> most people will make it — it is the same arrangement you already have with any
> assistant you use in a browser. Here is what actually differs, so none of it is
> a surprise later.
>
> **What stays the same.** Everything Friday writes down still lives on this
> computer. The wiki, the conversations, the map, the vault — those are local
> files either way. Cloud mode changes where the thinking happens, not where the
> memory lives.
>
> **What she will refuse to do.** Friday will not send certain things to
> Anthropic. When she recognises money, health, legal or identity details in what
> you have written, she holds the whole message back and tells you she has. On a
> computer that cannot run a local model, that means those questions do not get
> answered at all. Ask her about a bank balance and she will decline rather than
> reply. That is the boundary working, not a fault, but it is a real limit and
> you should know about it before you meet it.
>
> **What we cannot promise.** The thing that recognises sensitive material is a
> pattern matcher. It knows the shape of an account number, a phone number and an
> address, and the vocabulary of finance, medicine and law. It does not know that
> a word is a name. If you write "she started sertraline last month", that
> sentence goes to Anthropic, because nothing in it looks like a medical record to
> a program matching words. Assume that anything you type in cloud mode may be
> read by the provider you chose.
>
> You can change this later in Settings, and changing it changes only where
> future thinking happens.
>
> `[ Continue with cloud ]`   `[ Show me the other options again ]`

---

#### Screen 4 — The part about other people

> **The part about other people**
>
> Friday will end up holding records about people who are not you. Tell her about
> a meeting and she may write a page about the person you met. Every night she
> reads her own notes and links names to projects and to each other. After a year
> that is a fairly detailed picture of your colleagues, your family, and anyone
> you deal with often.
>
> They did not agree to any of this. You are the only person here who was asked.
>
> Two things follow from that, and they are worth a minute.
>
> **In cloud mode, their details travel with yours.** The limits on the last
> screen apply to what you write about other people exactly as they apply to what
> you write about yourself — including the gap. A sentence about a friend's
> diagnosis is no better protected than one about your own.
>
> **Friday cannot yet forget a person.** She can learn about someone and change
> her mind about them. She has no way to remove them, and nothing ages out on its
> own. If someone asks you to delete what you hold about them, today that means
> editing a file by hand. We think that is the wrong answer and we are working on
> a better one.
>
> `[ I understand ]`

*Notes for the builder.* If Q-V5 resolves in favour of building the delete path
first, the last paragraph becomes: *"Friday can forget a person. Settings →
Contacts lists everyone she holds a record about, where each record came from,
and a button to remove it — from her notes and from the map she builds out of
them."* That is the version we should be shipping.

---

### 7.4 What each screen writes

| Screen | Destination | Key |
|---|---|---|
| 1 | — | — |
| 2 | OS keychain via `POST /api/setup/complete` (`core_routes.py:631`); `os.environ` for the live process | `agent-friday` / `vault-passphrase` |
| 3 | `settings.json` via `_routing_block_for` (deep-merged — `_save_settings` merges `model_routing` since 5.6.4) | `model_routing.mode` |
| 3 | `credential_store` (`setup_brain.store_key`) | provider keys |
| 3b | `~/.friday/onboarding.json` | `cloud_ack`, `cloud_ack_ts` |
| 4 | `~/.friday/onboarding.json` | `third_party_ack`, `third_party_ack_ts` |
| 5 | `~/.friday/wiki/…` | the page the user approved |

**Nothing is written to `start.bat` or any other file on disk in the clear.**
`_write_start_bat` (`setup_wizard.py:993`) is deleted, and `_persist` (`:966`)
filters `anthropic_api_key` and `gemini_api_key` out of `settings.json` and
`config.yaml` the way it already filters `vault_password`.

### 7.5 Re-entry

Every screen is reachable afterwards from **Settings → Privacy**, in the same
order and with the same copy. Screens 3b and 4 are re-shown, once, when the
routing mode changes from a local mode to `cloud_only` — because the tradeoff the
user accepted at install is not the one they are accepting now.

The acknowledgement keys in `onboarding.json` are what suppress re-showing. They
are local and are never transmitted.

---

## 8. Comprehension over disclosure

A wall of text everyone clicks past is disclosure without consent. Four
mechanisms, in the order they act.

### 8.1 Progressive disclosure with a real floor

Each screen is one claim and at most four short paragraphs. Nothing is behind a
"learn more" link that the flow depends on the user having opened. If a fact
matters enough that the design would be dishonest without it, it is in the body
text — which is why the "what we cannot promise" paragraph is not collapsible.

### 8.2 Show, don't tell — Friday's first page

The most persuasive screen is not in the wizard. After the first real
conversation, Friday writes her first wiki page and shows it before saving:

> **I wrote this down. Have a look.**
>
> `[ the actual rendered page, title and body ]`
>
> This is the kind of thing I keep. It lives at
> `~/.friday/wiki/<section>/<page>.md` and it is an ordinary text file.
>
> `[ Keep it ]`   `[ Change it ]`   `[ Don't keep it, and don't write pages like this ]`

The third button is not decorative. It writes a real preference. A user who has
seen one true example of what Friday records understands the arrangement better
than one who has read four paragraphs about it, and the example is *theirs*, not
a mock-up.

> **Q-V7.** Where does "don't write pages like this" persist to, and what is its
> scope — the section, the page shape, or wiki-writing altogether? There is no
> existing setting for it.

### 8.3 One question, asked in Friday's voice

At the end of the wizard, one question. Not a quiz, not a gate.

> **One thing before we start.** If you asked me right now what your bank balance
> was, what do you think would happen?
>
> `( )` You would answer, using what you know.
> `( )` You would look it up online.
> `( )` You would decline, because that is one of the things you do not send to
> the cloud.

The third is correct for a cloud-only install; the first is correct for a local
install; the question is generated from the mode the user just chose, so it is
answerable rather than a trick. A wrong answer does not block anything — it
returns the relevant screen, once, with "Worth a second look at this one" above
it, and then continues regardless.

### 8.4 How we tell whether it worked

Stephen's second-user testing is the evidence base and it has already earned its
keep. The install on Janet's laptop on 2026-08-26 surfaced defects the author's
machine could not: `cloud_only` discarding the chosen seat at dispatch, a
Local-Only switch that displayed the inverse of its own state, connector tokens
sitting in plaintext, and a Connect Services step that drew a green dot beside a
service that had never been connected. Every one of those was invisible on a
machine whose settings had been hand-edited into a working state — and §11 shows
the same pattern recurring during this audit, where a routing probe found a local
model on Stephen's machine and had to be re-run with the local seat forced empty
before the real default behaviour appeared.

So the measurement is not analytics. It is three things:

1. **The answer to §8.3, stored locally in `onboarding.json`, never transmitted.**
   Stephen can read it off a test machine. Across a handful of installs it is a
   real signal about whether screen 3b landed.
2. **A scripted second-user session**, run on hardware that is *not* the reference
   card and by someone who is not the author, with three questions asked
   afterwards and written down verbatim: what does Friday keep, where does it go,
   and what happens if you ask her about money. Anything the user cannot answer
   is a copy defect, not a user defect.
3. **The `vault_skipped` rate.** If most testers skip the passphrase, screen 2 has
   not made the case, and the fix is the copy, not a nag.

> **Q-V8.** Is one comprehension question the right number? Two would measure
> more and cost the goodwill of a flow that has just asked for four screens of
> attention.

---

## 9. Where the framing has a problem

Four places where the brief describes a product slightly ahead of the code, and
one where it concedes something it need not.

### 9.1 The trust graph does not yet track people automatically

The brief says Friday builds "a trust graph that tracks people and other AI
agents." The people graph is manual: `add_person` and `edit`, both reached only
from `/api/trust/*`, with nothing in the tree calling them on its own
(`people_graph.py:185`, `:215`; `routes/contacts.py:164`, `:188`). Agent scoring
is aspirational — `source_trust_graph.py:5` says "news domains, and eventually
peer agents."

What *does* accumulate people automatically is the knowledge graph, which is a
different system with a different storage location and different encryption
behaviour. The onboarding must describe the second, not the first, or it will be
describing a feature the user cannot find.

This cuts the useful way, incidentally: "she scores your friends on emotional
safety without being asked" is a much harder sentence to put in an onboarding
than "she writes down who you mentioned and links them together."

### 9.2 Local models are not what protects the vault

The brief says the architecture's point is "to protect it from exposure to the
cloud by using local AI models." The enforcing component is the egress gate,
which runs before every cloud call and cannot be bypassed
(`egress_gate.py:8-13`). The local model is a *routing preference* whose default
fallback, when no local model exists, is to send the turn to the cloud and rely
on the gate (`core/__init__.py:1684`, `routing/model_router.py:487`).

This matters for the copy in a specific way: a user taught "local model = safe"
will install Ollama and believe they are done. They will not be, because
`model_routing.mode` stays `cloud_only` until something changes it, and the
current wizard cannot change it. The terminal wizard already ships that exact
false reassurance as a green panel (`setup_wizard.py:449`).

### 9.3 "Vault first" is one screen too early

Taken literally it produces a passphrase prompt in front of a user who has not
yet been told what a vault is. The requirement behind it — that this comes before
the cosmetics, the model picker and the scene chooser, and is not an optional box
on the last screen — is right and §7 honours it. But screen 1 has to come first,
and screen 1 is the one that makes screen 2 make sense.

### 9.4 "Sacrificing" is the wrong word, and the wrong stance

The brief says the user "has to understand what they are sacrificing" before
turning cloud mode on. Two problems. It is the wrong emotional register for a
choice most users have no alternative to — the entry rung of local inference
needs roughly a 6.5 GiB discrete GPU (§2.4), which most laptops do not have — and
it frames a normal decision as a loss.

It is also, on the facts, an overstatement in one direction and an understatement
in another. Overstatement: everything Friday *records* is local in both modes,
so the user is not sacrificing sovereignty over their data, only over where the
inference runs. Understatement: what they actually give up is not vague privacy,
it is **specific answers** — the bank-balance question that gets declined, the
Dave-and-his-phone-number email that fails, the semantic layer of the knowledge
graph that silently produces nothing (§4). Naming those three is more useful and
less moralising than asking someone to feel a sacrifice.

### 9.5 The claim the brief could make and does not

"Cloud-only is the default and you can change it later" is currently true but
weak. The stronger, verifiable claim is: **changing the mode later changes only
where future thinking happens; it does not move anything Friday has already
written down, because none of it was ever anywhere else.** That is true in every
mode, it is the thing that distinguishes this product from a hosted assistant,
and it is not currently said anywhere in the onboarding.

---

## 10. Open questions

| ID | Question | Blocks |
|---|---|---|
| **Q-V1** | Collapse three onboarding surfaces into one? Which one? | §7 build shape, not its content |
| **Q-V2** | Delete `_write_start_bat` outright? | §7.4 |
| **Q-V3** | Default `wiki_encrypted_sections` to `["health","legal","family"]` when a passphrase is set? | Screen 2 copy |
| **Q-V4** | Change `vault_cloud_fallback` default from `redact` to `warn` on installs with no local model? | Screen 3b copy — it changes "she will decline" from *sometimes* to *always* |
| **Q-V5** | Is `PeopleGraph.forget()` + a knowledge-graph purge a precondition for shipping screen 4? | Screen 4's last paragraph |
| **Q-V6** | A `kg-exclude.txt` the indexer honours, for named people who should never be recorded? | Nothing in §7; a separate build |
| **Q-V7** | Where does "don't write pages like this" persist, and what is its scope? | §8.2 |
| **Q-V8** | One comprehension question or two? | §8.3 |
| **Q-V9** | Should the wizard offer to pull a local model on capable hardware during setup, given `models.install` already exists as an installer step (`install.ps1:662`) and the app wizard has `WizardGemmaPull` (`index.html:29863`) — which still names `gemma3:4b`, the one model in the ladder that cannot call tools? | Screen 3 |

---

## 11. What was verified, and how

Four claims were checked by running code rather than reading it. Probes were
written to the session scratchpad and are not part of the tree.

**1. The factory routing defaults.** Read from `DEFAULT_SETTINGS` in a live
interpreter, not from the source comment:

```
mode                  cloud_only
vault_local_only      True
vault_cloud_fallback  redact
local_model           qwen3:4b
fallback_to_cloud     True
```

**2. Vault routing with no local model.** `ModelRouter` at the factory defaults,
`_local_candidates()` forced empty to simulate a machine with no local seat:

```
vault_cloud_fallback=redact  →  provider=cloud  model=claude-sonnet-5  refuse=False
vault_cloud_fallback=deny    →  provider=cloud  model=claude-sonnet-5  refuse=True
vault_cloud_fallback=warn    →  provider=cloud  model=claude-sonnet-5  refuse=True
```

The shipped default transmits.

*The first run of this probe did not show that.* Pointed at a dead Ollama port it
still returned `provider=local, model=qwen3.5:9b`, because `_local_candidates()`
asks Friday's own seat store before the daemon (`routing/model_router.py:409`) and
Stephen's machine has seats loaded. The author's hardware hid the default
behaviour, exactly as it hid the four defects Janet's laptop found. Worth
recording as method: a privacy default cannot be verified on the machine that
would never hit it.

**3. The egress gate on realistic sentences.** `classify()` and `seal_outbound()`
against Anthropic, factory settings:

| Input | Tier | What Anthropic receives |
|---|---|---|
| `Remind me what my Chase account balance was last month.` | 3 | `[EGRESS-GATE: message withheld — it stayed on this device …]` |
| `Call Dave at 555-0142 about the Hartley contract.` | 2 | `[EGRESS-GATE: TIER_2 content withheld …]` |
| `Emma's school pickup is at 3:15 and her teacher is Ms. Alvarez.` | **1** | **the sentence, verbatim** |
| `She started sertraline 50mg last month.` | **1** | **the sentence, verbatim** |
| `What's the weather tomorrow?` | 1 | the sentence, verbatim |

Rows 1 and 2 are §4. Rows 3 and 4 are §5. Rows 3 and 4 are known and deliberate
(`docs/audits/privacy-classifier-known-gaps-2026-08-25.md`); this run confirms
they are still live at `c60172a`.

**4. The local-model ladder and the hardware floor.** Computed from
`model_plan.BRAIN_MODELS` with `RUNTIME_OVERHEAD_GIB = 1.7` and
`DISPLAY_RESERVE_GIB = 2.5`, reproduced as the table in §2.4. Entry rung
`qwen3:4b` needs a card of about 6.53 GiB.

Everything else in this document is a code reference with a line number. Nothing
was modified: no file in the tree was edited, no server was started or stopped,
and no settings were written. This document is the only artifact.
