# The salon against Replit Agent, Lovable, Bolt, v0, Cursor and Codespaces: a gap matrix

> **Status:** research; a competitor pass against
> [`../active/vibe-coding-salon.md`](../active/vibe-coding-salon.md) (the spec).
> Nothing here is built and nothing here changes the spec.
> **Checked:** 2026-09-30, against each product's official documentation.
> Every product cell carries a source key that resolves in §6. **UNVERIFIED**
> means no official page confirmed it today (absent, 404, or silent); nothing
> is written from memory of a product. Codespaces is compared only as a box.

`§n` cites the spec. `NS-x.y-z` ids come from `../north-star/GAP-MATRIX.md`;
north-star `§` sections from `../north-star/agent-friday-ideal-product-spec.md`.
**Verdicts:** ALREADY IN SPEC (covered, often better than any competitor);
PARTIAL (a piece exists, a competitor shows the rest); GAP (nothing, or one
line).

---

## 1. Feature matrix

| # | Feature | Replit Agent | Lovable | Bolt | v0 | Cursor | Codespaces (box only) | In the spec | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Live preview loop and refresh | Preview panel; the product loop is described as Agent building, checking and fixing [R1]. Refresh time not stated | Live preview updates while it builds, can be toggled to "most recent completed version"; Shift-refresh restarts the environment [L7] | Preview in the browser tab; edits in Code view are free [B10] | "Real-time preview" with progress indicators [V1] | In-editor browser pane, inline or separate window [C2] | Forwarded ports at `https://NAME-PORT.app.github.dev` [G2] | B0 frame renders with no process (§3.1, §4.3); "about a second" is asserted, Phase 1 measures it (§0) | ALREADY IN SPEC |
| 2 | Checkpoints and rollback, vocabulary | "checkpoints … roll back to any previous state" [R1]; one checkpoint per request, priced by effort [R2] | "version", "restore", "revert", "bookmark", "preview" an old state; reverting restores code, not database rows [L6] | "Backups", "Version history", "Restore this version"; restore does not change the database [B1] | Design-mode edits become "a reviewable chat version" with diff and revert [V4] | "Checkpoints" are local snapshots, "restore"; "separate from Git" [C3] | git only | "step" and "undo", git-backed, revert-of-revert avoided; every step a receipt (§3.1, §4.8) | ALREADY IN SPEC. Note: like Lovable and Bolt, undo does not restore backstage data; §4.8 is silent on it |
| 3 | Self-testing loop: headless browser, console and network capture, screenshots, fix-then-report | "Agent tests its own work on a regular basis" [R1]; mechanism pages not found today (UNVERIFIED detail) | Build mode observes build errors and test failures, inspects "console output and network requests when verification tools are used", runs browser testing [L3] | UNVERIFIED (no debugging page found) | Opens the apps it builds, uses them, "critique designs, debug complex flows, and fix things proactively"; sends screenshots; runs shell commands and unit tests in a sandbox; "Fix with v0" on deploy logs [V2] | Browser tool: navigate, click, type, screenshots, console logs, network requests, for testing and verification [C2] | none | Receipts carry tests run and a non-blank preview screenshot hash (§4.8, §11); Friday looks at screenshots only for the self-edit swap card (§9.3); "describe it" (§6.5). No loop that drives the user's app, captures its console, and fixes before reporting | GAP (evidence hooks exist, the loop does not) |
| 4 | Visual point-and-edit | Visual Editor: click an element, Text/Layout/Color/Border panels; simple edits are deterministic and free of credits, complex ones route to Agent [R4] | Preview toolbar: select one or many elements and "request a change in plain language"; inline text edit; draw annotations sent as an image [L1] | Select tool: toolbar for styles, or select and prompt; "visual edits in the preview is free" until saved [B3] | Design Mode: hover highlights, click selects, panel plus natural-language instructions with element screenshots [V4] | UNVERIFIED (browser tool clicks elements for the agent; no user-side selector documented) | none | By voice, Friday disambiguates "that button" by naming DOM candidates (§6.5). No pointer selection in the Preview tab (§5) | GAP |
| 5 | Voice input | Voice Mode transcribes into the chat box; nothing sent automatically; replies are not read aloud; 6-minute cap [R6] | Dictation button, Alt+V / Option+V [L14] | UNVERIFIED | UNVERIFIED | UNVERIFIED (only a Grok-bot voice page is indexed) | none | Full voice-first surface: verbs, cards read aloud and answered, focus, private-summary handoff (§6) | ALREADY IN SPEC, ahead of every competitor |
| 6 | Bring your own repo or folder; push and PR | Import from GitHub (public via URL rewrite, private via guided import), Bitbucket, zip, Bolt, Lovable, Figma; push-back not documented [R7] | Export only: "You can only export from Lovable to GitHub"; one branch synced at a time [L5] | Import an existing GitHub repo; auto-commit on every non-breaking change; merge on GitHub [B4] | Import from GitHub at a base branch; v0 works on a working branch and creates or reuses a PR on publish [V5] | Cloud agents clone from GitHub, GitLab, Azure DevOps, Bitbucket, work on a branch, push "merge-ready PRs" with screenshots, videos and logs [C4] | Native: the repo is the box; "Export changes to a branch" or fork [G5] | A codebase is a new repo "or an existing folder the user points at", which gets a salon branch (§4.8); `DevGit` already pushes and opens PRs (§1.1); push and merge need explicit approval (NS-21.15-1/2/3, §14.1). The push/PR card and its spoken form are not written down | PARTIAL |
| 7 | Plan-first mode for big asks | Plan Mode is read-only, produces task lists; "Build here" or "Build in background" [R3]; tasks have a written plan with "what 'done' looks like" [R8] | Plan mode inspects files and logs, writes a structured plan; edit, comment, version, approve; one credit per message [L8] | Plan button; does not edit code; "Implement this plan" switches [B2] | UNVERIFIED (agentic features page describes modes Ask/Auto/Full, not a plan mode) [V2] | Plan Mode asks clarifying questions, researches, writes a markdown plan you edit before build; Shift+Tab [C1] | none | Goals work in a codebase chat (§8). No plan-before-build turn, no typed blockers on a step | GAP |
| 8 | Phone preview: QR, tunnel | Mobile apps: device selector for simulator, or "scan the QR code with your phone" into Expo Go [R5]. Team setting can require private dev URLs, which does not cover Expo Go [R10] | Device toggle; "open a preview link on your phone"; share links need no account, expire by plan [L7] | Device Preview icon shows a QR for Expo Go [B9] | Preview deployments have their own URLs [V6] | UNVERIFIED | Port visibility private/org/public; private ports need the GitHub token [G2] | A width switcher (phone, tablet, desktop) in the Preview tab (§5). Nothing reaches a real phone | GAP |
| 9 | Publish to a web address; custom domains | Autoscale, Reserved VM, Static, Scheduled; `.replit.app`; "security checks" before promotion [R9]; custom domain via two DNS records, HTTPS automatic [R11] | `lovable.app` snapshot publish, HTTPS; visibility public/workspace/custom on Business+ [L9]; custom domains on paid plans, or buy through Lovable [L10] | Free `bolt.host`; private sites on paid plans; "Update" to redeploy [B5]; own domain or purchase; paid plans only [B6] | Publish to Vercel: `vercel.app`, connected custom domain, visibility per plan [V6]; domain DNS handled in Vercel [V7] | none (PRs, not hosting) | none (dev ports only) | Publishing is an outward, non-grantable card (§4.5); "go live" swaps backstage stand-ins for real services (§4.6); sharing means signed bundles and later federation (§4.10). No path from a codebase to a web address the user owns | GAP |
| 10 | Database viewer and editor | Neon Postgres with separate Development and Production databases, provisioned by Agent [R12]; a viewer/editor is not described on the pages read (UNVERIFIED) [R13] | Database view: browse tables, edit inline, filter, add/delete rows, CSV export, SQL editor with confirmation on destructive statements, RLS policies, daily backups [L11] | Tables: rows, add row, delete, filter, query, CSV/JSON export, "View policies" [B7] | One-click Upstash, Neon, Supabase, Blob; generates and executes SQL; no visual viewer described [V8] | none | none | Backstage tab lists stand-ins; Mailpit's UI is embedded in the panel (§4.6, §5). No table view of the app's SQLite or Postgres | GAP |
| 11 | Secrets: environment variables or proxy injection | Secrets become environment variables, AES-256 at rest; collaborators can see values, others can print them from the environment [R14] | Backend Secrets injected into Edge Functions, "never reach the browser", write-only after save; `VITE_` values rejected [L12] | Secrets exposed to server functions as environment variables [B8] | Environment variables on Vercel per environment; the preview sees Development values [V9] | Own API keys are sent to Cursor's backend on every request [C5] | Secrets exported as environment variables; withheld from forks without write access; 100 secrets, 48 KB [G3] [G4] | The salon proxy injects credentials per host; nothing in the box's environment, files or logs, including Claude's agent key (§3.1, §4.7) | ALREADY IN SPEC, ahead of every competitor |
| 12 | Package install controls; licence and security scanning | "security checks" before publish [R9]; nothing on install-time controls found | Quick scan on publish: RLS, npm vulnerabilities, MCP exposure; Deep scan; Wiz and Aikido optional [L2]; dependency audit with severities, JSON report, bulk fix [L13]. Licence checks not mentioned | none found | none found | Sandbox network "blocked by default", then opened by network mode and `sandbox.json`; package registries allowed by default [C7] | none in scope | Exact pins, 24-hour cooldown, deterministic scan, reviewer pass on flags, signed override (§4.5). No licence check, no pre-publish scan of the app itself | PARTIAL |
| 13 | Export as a plain project | Import page lists "previous Agent exports" as a source [R7]; an export page was not found (UNVERIFIED) | Git sync to GitHub, GitLab or Bitbucket; "host them wherever you choose"; Cloud backend moves only to Supabase [L15] | "Export your project as a zipped file" [B1] | UNVERIFIED (code viewer page 404 today) | Files are local; checkpoints are local [C3] | Export to a branch or fork; only the checked-out branch [G5] | The repo is plain git at `~/.friday/codebases/<id>/repo/` with `.friday/` metadata (§4.6, §4.8). No "export" verb, no zip, no "open the folder" | PARTIAL |
| 14 | Collaboration and sharing | Invite teammates, 5 seats Core, 15 Pro; each task in an isolated copy; shared Kanban [R15] | Roles Viewer/Editor/Admin/Owner; each collaborator in a "draft" merged on acceptance [L16] | Multiplayer when public; prompts queue; "tokens from the prompter's account" [B11] | Team chats on Plus and Business [V10] | Cloud agents from web, desktop, iOS, Slack, GitHub comments, Linear [C4] | Live Share, read-only guests [G6] | Signed bundle export and import first, federation later, market cards last (§4.10). No multi-user editing; deferred by NS-7.3-1 | PARTIAL, deliberately |
| 15 | Cost display and whose key pays | Checkpoint cost shown per request in the Agent tab; Usage Dashboard; budget alerts; Free/Power/Max modes [R2] | Credits: Plan 1 credit, Build 0.5–2; "Lovable pays for API access"; per-project and per-member usage [L17] | Tokens, subscription balance shown under My Subscription; 300K daily on Free [B10] | Credits per plan, per-million-token model prices [V10] | Own OpenAI, Anthropic, Google, Azure, Bedrock keys for chat models; ZDR does not apply then [C5]; usage dashboard, Auto mode Cost/Balance/Intelligence [C6] | Core-hours and GB-months; free quota; budgets [G7] | Header line names model, key profile and running dollars; guest key bound to one codebase, metered separately, removable in one click (§4.7) | ALREADY IN SPEC. Guest keys have no competitor equivalent |
| 16 | Sandbox and isolation model | Cloud project; background tasks in isolated project copies [R8] | Cloud; per-collaborator drafts [L16] | In the browser tab (StackBlitz WebContainers, "everything is contained in a browser tab") [W1] [W2]; the Bolt docs read today do not name it (UNVERIFIED link) | Isolated cloud sandbox for shell commands; Ask/Auto/Full [V2] | Local machine; sandboxed terminal on macOS (Seatbelt) and Linux (Landlock v3), not Windows; Run Modes are "best-effort guardrails rather than a hard security boundary" [C7] [C8] | One VM per codespace, never co-located; own virtual network; fresh VM on restart; tokens scoped to repo access [G4] | Tiers B0 frame, B1 host with disclosure, B2 VM, B2-OS OpenShell past a gate (§4.4); one proxy (§4.5) | ALREADY IN SPEC |
| 17 | Model choice, local or cloud | Free, Power, Max modes [R1]; no local | Lovable's models only [L17] | Standard or Max agent [B12]; no local | Mini, Pro, Max, Max Fast [V10]; no local | Many cloud providers, own keys; local models not mentioned [C6] | n/a | Local seat for small edits by default, heavy cloud seat by choice, `local_only` mode (§4.7, §6.4) | ALREADY IN SPEC |
| 18 | Privacy posture and telemetry | Team admins can require private dev URLs and private deployments, pick geographies [R10] | Free and Pro content used for model training by default, opt-out in settings; Business+ excluded [L18] | UNVERIFIED | Training opt-out by default on Business; "never used for training" on Enterprise [V10] | Privacy Mode: no training, ZDR with providers, except on own keys [C9] | GitHub-hosted; secrets withheld from untrusted forks [G4] | No telemetry, verified by packet capture; private data reaches a cloud model only as a local PII-free summary with a payload card (§6.4, §8, §9.4) | ALREADY IN SPEC, ahead of every competitor |
| 19 | Agent-made tools and integrations | Connectors to BigQuery, Slack, Notion [R1]; an Agent Skills page is indexed [R16] (content UNVERIFIED) | Managed connectors; pasted keys are recognised and turned into a connector [L12] | MCP servers, owner-configured only [B11] | Vercel Marketplace and MCP servers [V2] | MCP via `mcp.json`, marketplace, OAuth; approval before MCP tool use; agents do not create their own servers [C10] | none | Workspaces are made and improved in the salon (§4.9); Friday edits a copy of herself (§7). No path from a codebase to a registered tool Friday can call | GAP |
| 20 | Parallel background work | Tasks board Drafts/Active/Ready/Done; 1, 10 or 64 concurrent by plan; apply back after review [R8] | Drafts per collaborator [L16] | Prompts queue [B11] | none documented | Cloud agents, spend limit required [C4] | one codespace per branch | Long changes run in the background through `delegate_to_friday` (§6.2); a codebase chat is one task-ledger run (§4.8). One thread per codebase | PARTIAL (note only; not a candidate) |
| 21 | Local stand-ins for the cloud during development | none: real Neon dev database [R12] | none: real Cloud backend | none: real Bolt Database | none: real integrations | none | none | The backstage: shim, SQLite, Mailpit, moto, Azurite; "go live" is a card (§4.6) | ALREADY IN SPEC, a differentiator |
| 22 | Pre-publish scan of the app itself | "security checks" before promotion [R9] | Quick scan and Deep scan in the publish dialog [L2] | none found | none found | none | none | Install scan only (§4.5) | GAP (folds into candidate 6) |

---

## 2. Candidates checked against the spec

Each candidate: what the spec already has, the gap, the recommended
engineering option, and the north-star requirement it serves.

### 2.1 Self-testing loop for user apps, plus a harsh-critic gauntlet

- **Already there:** receipts carry tests run and a non-blank preview
  screenshot hash (§4.8, §11); Friday describes the preview by voice
  (§6.5); screenshots are looked at and described for the self-edit swap
  card (§9.3). **Gap:** nothing drives the user's app, captures its
  console and network, or fixes before reporting. Lovable, v0 and Cursor
  all do [L3] [V2] [C2].
- **Option:** a `salon_verify` pass after every step, run by the box
  tier: in B0 the frame's broker relays `console.error`, unhandled
  rejections and failed fetches (the broker already carries
  `postMessage`, §4.3); in B1/B2 a headless Chromium (Playwright, already
  used by the test suite) loads the preview, clicks the template's smoke
  path, and records console, network and a screenshot. Failures loop back
  into the same step, at most N tries, then the step reports
  `run_failed` with the evidence attached. The receipt gains
  `console_errors`, `network_failures` and `smoke_path`.
- **Gauntlet as a user feature:** a "be harsh" toggle on a codebase (or
  "Friday, be picky about this one") runs a second, read-only reviewer
  pass against a written bar (the goal text, or the template's
  acceptance list) and attaches its verdict to the receipt as evidence,
  never as a veto, mirroring §9.3. It reuses the reviewer seat of §4.5.
- **North star:** §4.6 verify, NS-4.6-1/2; §17.12, NS-17.12-1; §6.5.

### 2.2 Point-and-say visual editing (pointer plus voice)

- **Already there:** voice disambiguation by naming DOM candidates
  (§6.5). **Gap:** no pointer selection; all five agent products have it
  [R4] [L1] [B3] [V4].
- **Option:** a "select" mode in the Preview tab. The frame's broker
  returns a stable selector, the element's outer HTML (trimmed), its
  computed box and a cropped screenshot; the chat gets a pinned
  "selected: the blue Save button" chip; the next typed or spoken request
  carries it. Simple property edits (text, colour, spacing) are applied
  by a non-model patcher on the source when the template maps selector
  to file, as Replit and Bolt do free of credits [R4] [B3]; anything else
  goes to the seat as an ordinary step. In B2 the same broker runs in an
  injected script on the dev server's page.
- **North star:** §20.2 browser control lane, NS-20.2-1; §22.5 voice
  parity, NS-22.5-1.

### 2.3 Bring your own code, push or PR behind an approval card

- **Already there:** "an existing folder the user points at" gets a salon
  branch (§4.8); `DevGit` pushes and opens PRs (§1.1); push and merge
  approvals are named in §14.1. **Gap:** the card itself is unwritten:
  what it shows, its spoken form, and that "trust this codebase" never
  covers it. Cursor's cloud agents ship PRs with screenshots and logs
  [C4]; v0 reuses an existing PR [V5].
- **Option:** "+ Codebase → Open a folder or repo" (local path or clone
  URL). Push and PR are one outward card kind, `code_publish`, listing
  remote, branch, commit count and diff stats, with the last receipt's
  screenshot attached to the PR body. Non-grantable by default, like
  publishing (§4.5). Spoken form per §6.3.
- **North star:** §21.15, NS-21.15-1/2/3.

### 2.4 Plan-first mode wired to goals and typed blockers

- **Already there:** goals work in a codebase chat and the evaluator
  reads step receipts (§8). **Gap:** no plan turn before a large change;
  every competitor has one [R3] [L8] [B2] [C1].
- **Option:** Friday's router (which already sizes "small edit", §4.7)
  classifies a request as big; a big request first yields a `markdown`
  artifact in the panel (Phase 1 kind) holding milestones with "done
  looks like" lines, editable by hand as any artifact is (§4.2). "Build
  it" turns milestones into task-ledger tasks under a goal; each step
  closes with a typed blocker from §17.8 (`goal_not_met_yet`,
  `run_failed`, `needs_user_input`) so the goal evaluator, not the
  model, decides continuation. Spoken: "plan it first" and "build it".
- **North star:** §17.5 plans and milestones, NS-17.5-1; §17.8 typed
  blockers, NS-17.8-1/2; §17.11 task ledger.

### 2.5 Phone preview through the user's own tunnel, with a QR code

- **Already there:** a width switcher (§5). **Gap:** no real device.
  Replit and Bolt show a QR into Expo Go [R5] [B9]; Lovable hands out an
  expiring link [L7]; Codespaces gates ports behind the GitHub token
  [G2].
- **Option:** "Show it on my phone" serves the preview on the LAN from
  the salon proxy at a random path with a one-time token, shows a QR in
  the panel, and expires it after 30 minutes or on "stop". B0 apps are
  served as a static bundle; B2 apps proxy the dev server. Never
  Friday's own port (§4.5 floor). Off-LAN reach uses a tunnel the user
  already runs (Tailscale, ngrok, Cloudflare Tunnel, detected by binary
  or config, never installed); opening one is an outward card. Requests
  arriving through the tunnel are authenticated by the token, which is
  what NS-11.3-9 requires of proxied or tunnelled requests.
- **North star:** §11.3, NS-11.3-9; §11.5 mobile companion, NS-11.5-1/2.

### 2.6 Publish to a web address the user owns

- **Already there:** publishing is outward and non-grantable (§4.5);
  "go live" is the backstage swap (§4.6); Phase 8 publishes bundles, and
  federation publishes to other Fridays (§4.10). **Gap:** none of those
  puts a user's app on a URL. All four agent products do [R9] [L9] [B5]
  [V6], with custom domains [R11] [L10] [B6] [V7].
- **Option:** a `publish_target` per codebase in `.friday/backstage.json`
  beside the stand-ins: `static_folder` (a path the user syncs), `ssh`
  (rsync to the user's host), `git_push` (a branch a Pages-style host
  builds), and `command` (the user's own deploy script). Friday never
  hosts, never registers domains, and never picks a vendor; she prepares
  the build, shows §24.9's checklist on the card (destination, audience,
  link check, cost), runs the target, then verifies the URL is reachable
  and serves the built hash (§4.6 verify). A pre-publish scan of the app
  (row 22) runs first: dependency audit, secrets in the bundle, and open
  CORS or auth on the backstage-to-live swap.
- **North star:** §24.9 publishing; §4.6, NS-4.6-1; §18.4 grants
  (public publishing non-grantable).

### 2.7 Vibe-coding a new tool for Friday herself

- **Already there:** new workspaces (bundles) are made here (§4.9), with
  the manifest, scan, "installed means disabled" and rollback; Friday
  edits a copy of herself for native changes (§7). **Gap:** a *tool*,
  meaning something registered in the tool catalogue with a gate class,
  callable by the agent loop, by voice through `delegate_to_friday`, and
  by reflexes, has no path. No competitor has one either: Cursor's and
  Bolt's MCP servers are user-configured [C10] [B11]. This is where the
  salon can be first.
- **Option:** "+ Codebase → New tool" from a template that produces
  §23.4's manifest (name, schemas, `ring`, `action_class`,
  `network_domains`, `sensitive_arguments`), a fixture test, and a
  handler that runs *in the box*, never in Friday's process: the
  catalogue entry is a broker stub that forwards the call into the
  codebase's B0 frame or B2 container and returns the result, so §23.7's
  skill sandbox is the salon box. The gate class is declared by the
  manifest and checked, never lowered, by the §4.5 floor; a tool that
  declares `outward` gets the ordinary card each call, and no
  "trust this codebase" grant can cover it (§4.5). Installing it is the
  §23.8 flow, with the loud approval of §7.4 when the tool asks for
  vault, mail or credential access. Voice-callable comes free from the
  catalogue; reflex-callable requires the tool to be `internal` class or
  an owner rule naming it. Test: a tool that tries to widen its own
  class or read `~/.friday` is refused at install.
- **North star:** §23.4 tool registry, NS-23.4-1; §23.5 tool
  truthfulness, NS-23.5-1; §23.9 Friday-generated skills, NS-23.9-1/2;
  §23.8, NS-23.8-1.

### 2.8 A data viewer for the app's database

- **Already there:** the Backstage tab, and Mailpit's UI embedded (§4.6,
  §5). **Gap:** no table view. Lovable and Bolt have full ones [L11]
  [B7].
- **Option:** the `table` artifact kind (§4.2) is already sortable,
  editable and CSV-exportable. The Backstage tab lists the SQLite file
  (or Postgres in B2) and opens each table as a `table` artifact bound
  to a query; cell edits become an `UPDATE` through the shim with the
  same confirmation Lovable uses for destructive statements [L11]; a
  read-only SQL box under it. Rows from a file flagged private
  (§6.4) are shown but never sent to a cloud seat.
- **North star:** §24.5 spreadsheet integrity; §21.1 workspaces are
  views.

### 2.9 Licence checks on every installed package

- **Already there:** pins, cooldown, the deterministic scan and the
  reviewer pass (§4.5). **Gap:** no licence field. No competitor read
  today does this either; Lovable's audit is vulnerabilities only [L13].
- **Option:** the scan reads the registry's `license` field (npm, PyPI
  classifiers, crates.io) and the package's LICENSE file, normalises to
  SPDX, and writes it to the lockfile sidecar and the step receipt. The
  install announcement names the licence. Default posture: announce, and
  flag (not refuse) copyleft, "no licence" and non-commercial terms;
  refusal is an owner rule ("no GPL in Rent Tracker"). The user's own
  licence posture is the owner-intent item in §4.
- **North star:** §21.15 security scanning; §23.9 static and security
  checks, NS-23.9-1.

### 2.10 One-click export as a plain project

- **Already there:** the repo is plain git with a lockfile (§4.8, §4.5).
  **Gap:** no verb for it. Bolt zips [B1]; Lovable syncs to a remote and
  says "host them wherever you choose" [L15].
- **Option:** "Export" on the codebase menu and "Friday, export this":
  copies the working tree to a folder the user picks (or a zip), strips
  `.friday/` and the guest-key binding, writes a `README-friday.md` that
  names the stand-ins the app expects and how to run it with plain
  `npm`/`pip`, and confirms the tree hash. INTERNAL, no card, because it
  writes only where the user pointed.
- **North star:** §6.10 the owner can leave, NS-6.10-1/2.

---

## 3. Delegated decisions (engineering calls, recommended option)

1. **Verification is per step, in the box tier, evidence not veto:** broker
   capture in B0, headless Chromium in B1/B2; a failed smoke path reports
   `run_failed`; a critic's verdict is attached, never blocking (2.1).
2. **Pointer selection returns a selector plus a cropped screenshot;**
   simple property edits go through a non-model patcher (2.2).
3. **Push and PR are one card kind, `code_publish`, non-grantable by
   default;** the receipt's screenshot rides in the PR body (2.3).
4. **A plan is a `markdown` artifact** whose milestones become task-ledger
   tasks; every step closes with a §17.8 typed blocker (2.4).
5. **Phone preview is LAN-first behind a one-time 30-minute token; a tunnel
   is detected, never installed, and opening one is a card** (2.5).
6. **Friday prepares and verifies publishes; she never hosts or sells
   domains.** Targets: folder, ssh, git push, the user's own command (2.6).
7. **A Friday-made tool runs in the box behind a broker stub; its gate class
   is declared, checked and never lowered; no grant covers an outward
   self-made tool** (2.7).
8. **The data viewer is the `table` artifact bound to a query**, with
   confirmation on destructive SQL (2.8).
9. **Licence is announced on every install and flagged for copyleft,
   unlicensed and non-commercial terms; refusal is an owner rule** (2.9).
10. **Export strips `.friday/` and the key binding, writes a plain README,
    and is INTERNAL** (2.10).
11. **Phase placement:** 2.2, 2.4, 2.8, 2.10 in Phase 2; 2.1, 2.3 in Phase
    4; 2.5, 2.6, 2.9 in Phase 5; 2.7 after Phase 7 (loud approval, install
    path).
12. **Undo stays code-only**, as every competitor's does; the Changes tab
    says so on any step that ran a migration.

---

## 4. Product intent for the owner

Three items are intent, not engineering, because each decides what Friday
*is for* rather than how she does it. Everything else above has a
recommended default the owner can overrule later.

1. **Does Friday publish, or only prepare?** Option 2.6 has Friday run the
   user's own deploy path and verify the URL, but never host, never
   register a domain, never pick a vendor. Every competitor sells hosting
   and domains [L10] [B6] [R11]. Choosing not to is a stance on §7.2 ("not
   a compute-rental economy") and on where the user's app lives; only the
   owner can say whether a Friday-run tunnel or a Friday-recommended host
   is ever on the table.
2. **May a tool Friday wrote for herself act while the owner is away?**
   Option 2.7 lets a self-made `internal` tool be reflex-callable and
   forbids any grant on a self-made `outward` one. That is a line about
   Friday's authority over her own extensions (§6.1, §13.1), stricter
   than §23.9 requires; the owner may want it looser (owner rules may
   name an outward self-made tool) or stricter (no reflex may call a
   self-made tool at all).
3. **Licence posture: inform, or refuse?** Option 2.9 announces and flags
   but never refuses without an owner rule. Whether Friday should hold an
   opinion about what the user's apps may be built from (for example,
   refusing copyleft in a project the user means to sell) is content
   policy, which the owner directs, not a scan setting.

---

## 5. Size

Rough focused agent-days with review, per adopted gap, on top of the
spec's 55–62 (§10).

| Candidate | Days | Phase |
|---|---|---|
| 2.1 self-test loop plus critic toggle | 6–8 | 4 |
| 2.2 point-and-say | 3–4 | 2 |
| 2.3 BYO code, `code_publish` card | 2–3 | 4 |
| 2.4 plan-first with typed blockers | 3–4 | 2 |
| 2.5 phone preview, QR, tunnel detection | 2–3 | 5 |
| 2.6 publish to the user's address, pre-publish scan | 5–6 | 5 |
| 2.7 a tool for Friday | 6–8 | after 7 |
| 2.8 data viewer | 2–3 | 2 |
| 2.9 licence checks | 1–2 | 5 |
| 2.10 export | 1 | 2 |
| **Total** | **31–42** (about 7 agent-weeks) | |

The cheapest four (2.2, 2.4, 2.8, 2.10, about 9–12 days) all land in
Phase 2 and close four of the six GAP rows a journalist would notice first.

---

## 6. Sources

All read 2026-09-30. The 404 list at the end is why the UNVERIFIED cells
are unverified.

**Replit** [R], base `https://docs.replit.com`
- R1 `/replitai/agent` · R2 `/billing/ai-billing` · R3 `/learn/plan-vs-build-mode`
- R4 `/design/visual-editor` · R5 `/build/mobile-app` · R6 `/chat/voice-mode`
- R7 `/build/import-from-providers` · R8 `/core-concepts/agent/task-system`
- R9 `/build/publish-your-app` · R10 `/teams/privacy-and-deployment-settings`
- R11 `/build/add-custom-domain` · R12 `/build/add-database`
- R13 `/learn/projects-and-artifacts/storage-and-databases`
- R14 `/replit-workspace/workspace-features/secrets` · R15 `/build/invite-teammates`
- R16 `/core-concepts/agent` · index `/llms.txt`

**Lovable** [L], base `https://docs.lovable.dev`
- L1 `/features/visual-edit` · L2 `/features/security` · L3 `/features/modes`
- L5 `/integrations/git-integration` · L6 `/features/projects/history`
- L7 `/features/projects/preview` · L8 `/features/plan-mode` · L9 `/features/publish`
- L10 `/features/custom-domain` · L11 `/features/database` · L12 `/features/secrets`
- L13 `/features/security-view` · L14 `/features/projects/chat`
- L15 `/tips-tricks/deployment-hosting-ownership` · L16 `/features/collaboration`
- L17 `/introduction/credits-and-usage` · L18 `/features/business/data-opt-out`
- also read: `/features/cloud` · index `/llms.txt`

**Bolt** [B], base `https://support.bolt.new`
- B1 `/building/using-bolt/rollback-backup` · B2 `/best-practices/plan-mode`
- B3 `/building/visual-edits` · B4 `/integrations/git` · B5 `/cloud/hosting/publish`
- B6 `/cloud/domains` · B7 `/cloud/database/tables` · B8 `/cloud/database/secrets`
- B9 `/integrations/expo` · B10 `/account-and-subscription/tokens`
- B11 `/building/using-bolt/collaborate` · B12 `/building/using-bolt` · `/` · `/llms.txt`
- W1 https://webcontainers.io/guides/introduction · W2 https://webcontainers.io/

**v0** [V], base `https://v0.app`
- V1 `/docs/introduction` · V2 `/docs/agentic-features` · V4 `/docs/design-mode`
- V5 `/docs/github` · V6 `/docs/deployments` · V7 `/docs/custom-domains`
- V8 `/docs/databases` · V9 `/docs/external-apis` · V10 `/pricing`
- index `/docs/llms.txt` (redirected from https://vercel.com/docs/v0)

**Cursor** [C], base `https://cursor.com`
- C1 `/docs/agent/plan-mode` · C2 `/docs/agent/tools/browser` · C3 `/docs/agent/chat/checkpoints`
- C4 `/docs/cloud-agent` · C5 `/help/models-and-usage/api-keys` · C6 `/docs/models-and-pricing`
- C7 `/docs/agent/security/run-modes` · C8 `/docs/agent/security`
- C9 `/help/security-and-privacy/privacy` · C10 `/docs/context/mcp`
- also read: `/docs/agent/tools/terminal` · `/docs` · `/docs/llms.txt`

**GitHub Codespaces** [G], base `https://docs.github.com/en`
- G1 `/codespaces/about-codespaces/what-are-codespaces`
- G2 `/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace`
- G3 `/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces`
- G4 `/codespaces/reference/security-in-github-codespaces`
- G5 `/codespaces/troubleshooting/exporting-changes-to-a-branch`
- G6 `/codespaces/developing-in-a-codespace/working-collaboratively-in-a-codespace`
- G7 `/billing/managing-billing-for-your-products/managing-billing-for-github-codespaces/about-billing-for-github-codespaces`
- also read: `/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration/introduction-to-dev-containers`

**Returned 404 today:** Replit `/replitai/agent-testing`, `/replitai/element-selector`,
`/replitai/agent-checkpoints`, `/replitai/agent-integrations`, `/core-concepts/agent/testing`,
`/build/export-your-app`, `/replit-workspace/workspace-features/download-as-zip`;
Lovable `/features/history`; Bolt `/best-practices/debugging`, `/integrations/mcp`;
v0 `/docs/faq`, `/docs/code-viewer`; Cursor `/docs/agent/chat/voice`.
