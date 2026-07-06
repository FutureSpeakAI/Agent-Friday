# Agent Friday — Manual Test Procedures

Surfaces that require real hardware, external OAuth, or human judgement and
therefore can't be covered by the automated Playwright/pytest suites. Run these
by hand before a release. Automated coverage lives in:

- `tests/friday_ui_full.spec.ts` — broad UI + connection sweep (Playwright)
- `tests/friday.spec.ts` — original API/UI smoke (Playwright)
- `tests/api/`, `tests/unit/` — ~1,870 offline backend tests (pytest)

Prereq for all UI procedures: server running on <http://localhost:3000> (via
`start.bat`, which loads the API keys). Confirm boot with
`curl localhost:3000/api/health`.

---

## 1. Voice mode (Gemini Live, WebSocket `/ws/live`)

Cannot be automated — needs a microphone, speakers, and a live Gemini key.

1. Click the 🎤 button in the hero input (or a workspace title-bar 🎤).
2. **Expect:** mic permission prompt → "● LIVE" indicator → Friday greets you.
3. Speak a sentence; confirm a transcript appears and Friday answers in audio.
4. **Speaker-echo regression:** with output on *speakers* (not headphones),
   confirm Friday does NOT cut herself off mid-sentence (NO_INTERRUPTION mode).
   Settings → Audio & Voice → Interruption Mode = "Speaker" should be default.
5. **Headphones/barge-in:** switch Interruption Mode to "Headphones", confirm you
   can interrupt her by speaking.
6. Check no progressive "raspiness" over a 60s+ reply (AudioWorklet ring buffer).
7. End the session; confirm the socket closes cleanly (no console errors).

Diagnostics if it fails:
- 1008 "Expected OAuth 2 access token" = invalid/stale key (often a User-scope
  env var shadowing start.bat's rotated `AQ.` key), NOT a model problem.
- Silent mic vs API failure: check RMS meter in Settings → Microphone → Test (5s).

## 2. Google OAuth — Gmail & Calendar

Cannot be automated — needs an interactive Google consent screen.

1. Settings → Connectors (or the Messages/Calendar workspace "Connect" button).
2. Click **Connect Google**. A browser tab opens Google's consent screen.
3. Approve; confirm redirect back and `~/.friday/google_token.json` is written.
4. Reopen **Messages** → real Gmail threads load (not the "Google not connected"
   sentinel). Reopen **Calendar** → real events load.
5. **Until connected**, both workspaces must degrade gracefully with the
   "built in, needs one-time OAuth" note — verify no crash/blank.

> Known state: a Desktop OAuth client JSON must be dropped in first; only a Web
> client exists today. See `scripts/friday_google_connect.py`.

## 3. Camera mode

1. Click 📷 ("Enable camera mode") in the header.
2. **Expect:** camera permission prompt → live preview tile.
3. Ask Friday "what do you see?" in voice/chat → confirm a vision response.
4. Disable; confirm the camera light turns off and the tile closes.

## 4. Computer control (pyautogui, cloud agent only)

Off + non-persistent by default (public-release hardening). Local models can't
drive it — cloud `_call_claude_agent` tool loop only.

1. Settings → enable computer control (grant the runtime permission prompt).
2. In chat ask Friday to "open Notepad" (or move the mouse to a corner).
3. **Expect:** a permission gate, then the action executes; screenshots are sent
   back as image blocks with scaled coordinates.
4. Confirm the setting does NOT persist across a server restart.

## 5. Process orbs (cursor gravity / clickability)

1. Trigger background work (send a chat that spawns a task, or run a daily
   creation: Studio → run).
2. **Expect:** floating process orbs appear; they drift toward the cursor
   (gravity) and are clickable.
3. Click an orb → the Task Result modal opens with status + activity log.
4. Confirm completed orbs clear and don't leak.

## 6. Notifications → deep-link navigation

1. Open 🔔. Confirm the unread count matches `/api/notifications`.
2. Click a notification carrying a `target` (e.g., a news or message item).
3. **Expect:** the correct workspace opens and scrolls to the referenced
   thread/event/article (the `friday-nav` deep-link bus).
4. Dismiss one; confirm the count decrements and it doesn't reappear.

## 7. Browser-tab opening + URL validation

1. Ask Friday to open a known-good URL (e.g., a news source link).
2. **Expect:** the URL is validated before opening; a real tab opens.
3. Try a malformed/suspicious URL → confirm it is rejected, not opened.

## 8. Offline-first resilience

1. With Friday running, disable networking (airplane mode / pull ethernet).
2. Within ~30s confirm: header shows an offline badge, the holo scene
   desaturates, and routing forces local-only (Ollama/gemma4).
3. Send a chat → confirm a local reply (or a clearly-queued action).
4. Restore networking → confirm the offline queue flushes and feeds refresh.

## 9. Voice everywhere (per-workspace)

1. In any workspace title bar, click 🎤 ("Start voice for <workspace>").
2. **Expect:** a voice session scoped to that workspace's context
   (`/api/voice-context/<ws>`).
3. "Start my day" button → a spoken briefing (`/api/voice/start-my-day`).

---

## Model defaults checklist (v5.1+)

Sonnet 5 is the default orchestrator. Fable 5 is the creative/narrative specialist. Verify:

- [ ] Top-bar model pill shows **just the model name** — "Sonnet 5" on a
      fresh install. No ☁️/🏠 emoji, no "+ Local" suffix.
- [ ] Clicking the pill opens the compact selector panel with, in order:
      **Quick Switch** (≤5 models, current one highlighted first; clicking
      another switches the orchestrator and closes the panel),
      **By Role** collapsible rows — Orchestrator, Subagent, Creative
      (split into Image model and Video model), Voice (engine selector;
      Gemini Live model sublist appears only when the gemini engine is
      active) — with only one row expanded at a time,
      **Routing Mode** (Cloud Only / Smart / Local Pref. / Local Only),
      **Local Models** (present **only** when Ollama is running),
      and a **Browse All Models** footer button.
- [ ] The panel never lists more than ~15 model entries total, and no
      grayed-out/unavailable models appear anywhere in it.
- [ ] "Browse All Models" opens Settings on the **Providers** tab.
- [ ] Settings → Providers **Model Browser** auto-populates on open (no
      empty state) and offers a search box, provider filter, capability
      filter (Tool calling / Vision / Image gen / Video gen / Free /
      Local), and price/context sorting.
- [ ] First-run setup wizard model step lists Sonnet 5 as the selected default.
- [ ] `~/.friday/settings.json`: `orchestrator_model` and
      `model_routing.default_cloud_model` are `claude-sonnet-5`.
- [ ] Mythos 5 does **not** appear anywhere (it was never shipped).
- [ ] `grep -ri "mythos" index.html ui_parts/ *.py routes/ services/`
      returns nothing.

This regression is automated in `friday_ui_full.spec.ts` →
"Model selector reflects available models only".

---

## 10. Content Pipeline — per-platform first connect & first publish

Real platform OAuth and live publishing can't be CI'd
(`docs/CONTENT_PIPELINE_SPEC.md` §15). Run the common checklist once per
platform, then that platform's specific steps. Prereqs: server on
localhost:3000, Content workspace enabled (`settings.content.enabled`).

### Common checklist (every platform)

First connect:

1. Content → **Accounts** → the platform card shows "Not connected" plus its
   auth mode (OAuth / token paste / manual).
2. Click **Connect**. OAuth platforms open the provider consent page; the
   redirect must return to
   `http://localhost:3000/api/content/platforms/<name>/callback` (loopback
   only — never an external redirect host).
3. **Expect:** the card flips to connected with account name/handle, a
   plain-language scope list ("Can: … Cannot: …"), an expiry countdown where
   applicable, and a live rate budget ("N posts left today").
4. Confirm no token material appears in the UI, server logs, or the
   `/api/content/platforms` payload; `~/.friday/platforms/<name>.cred`
   exists and is not plaintext (unless the loud plaintext-fallback warning
   fired).
5. The credential-store audit log records the connect event.

First publish:

1. Compose a short test post (Compose tab), select ONLY this platform, and
   check the preview mockup: char meter, adapted body, media transform.
2. **Post now.** **Expect:** a process orb, then a success notification with
   the live post URL; the Queue history row goes SENT → CONFIRMED;
   `~/.friday/content/publish_log.jsonl` gains a line.
3. Open the post URL — the published content must match the preview exactly
   (the preview is the payload).
4. Verify a signed publication entry landed in the asset's provenance
   ledger/sidecar (Trust → provenance viewer) and an ownership distribution
   event was recorded.
5. Analytics: after +1 h the first engagement snapshot appears (Analytics →
   post drilldown); unreportable metrics render "—", never "0".
6. **HELD drill** (once, on any platform): compose a post containing an
   obvious fake SSN → **Expect** HELD status, a high-priority notification,
   and the flagged spans in the Queue's Held section. **Release** publishes;
   editing the body clears the hold. Nothing must reach the platform while
   held.
7. Disconnect: platform-side token revoke (where supported) + local purge —
   the card must report both outcomes separately.

### LinkedIn

- OAuth scopes requested: `w_member_social`, `openid profile` — nothing more.
- **Expect:** ~60-day token expiry countdown on the card; a re-auth prompt
  surfaces *before* the token lapses.
- Publish a text post + one image with alt text; verify the alt text via
  LinkedIn's alt-text viewer.
- Analytics tier shows `counts` for a member account (impressions require a
  connected org page).

### X / Twitter

- OAuth2 + PKCE; scopes `tweet.read tweet.write users.read offline.access`.
- Card shows the configured API tier and **posts remaining this month**.
- Publish a 2–3 segment thread → **Expect** correctly chained replies.
  Fault drill: kill the network mid-thread, re-arm → resumes from the last
  confirmed segment, zero duplicate tweets.

### Instagram

- Professional (business/creator) account required — the card must say so
  plainly *before* connect.
- Feed image: JPEG conversion + 4:5 crop preview must match the published
  result.
- With no staging host configured → **Expect** a clear hold with "Instagram
  needs a public asset URL — configure staging or post manually". Never a
  silent failure. With `settings.content.staging_base_url` set, confirm the
  staged file is deleted after publish confirmation.
- Reels use resumable upload (no staging required).

### YouTube

- Reuses the existing Google OAuth plumbing with the `youtube.upload` scope
  added; same encrypted token store.
- Card shows remaining daily upload quota (1,600 units per upload; ~6/day on
  the default project quota) and the API project's audit state (unaudited
  projects may have uploads locked private — must be displayed).
- Schedule an upload ≥10 min out → **Expect** native `publishAt`
  scheduling: shut the machine down; the video still goes public on time.
- Shorts: vertical ≤3 min via the `mp4-vertical-9x16` export profile.

### TikTok

- Unaudited app: **Expect** the card to state SELF_ONLY honestly; a publish
  lands as a private draft in the TikTok inbox with a notification
  explaining one-tap manual publishing in the app. Status must NOT read
  "published". Post-audit apps: true direct publish.

### Bluesky

- Connect = app-password paste (no OAuth consent page).
- Publish a post with an emoji immediately before a link and a mention →
  open in the app; **Expect** link + mention facets intact (UTF-8
  byte-offset facets are the classic off-by-one source).
- The 300-grapheme meter must match the server's counting; a >1 MB image is
  recompressed automatically.

### Mastodon

- Connect = instance base URL + token paste (or OAuth); the card shows the
  instance and its *discovered* character limit (never a hard-coded 500).
- Publish with the `nsfw` tag → **Expect** the status carries
  `sensitive` + a content warning.
- Schedule ≥5 min out → **Expect** server-side `scheduled_at` (verify via
  the instance's scheduled-statuses list); machine-off test as with YouTube.

### Reddit

- Script-app credentials; descriptive `User-Agent`.
- Pick a subreddit with mandatory flair → **Expect** the requirement
  surfaced as a compose-time warning, not a publish-time failure.
- Publish a markdown self-post; confirm zero hashtags in the body.

### Substack (manual mode)

- No API: Connect shows "manual mode" — no credentials are collected.
- Publish → **Expect** a handoff package (title, subtitle, HTML/markdown
  body, exported images), the editor opened, and target status `SENT`.
- Paste the published URL back (or let RSS confirmation find it) → status
  `CONFIRMED`. The queue must never claim Substack was automated.

### Medium

- With a legacy integration token: full publish; verify `canonicalUrl` is
  set when the piece was first published elsewhere.
- Without a token: same assisted-handoff flow as Substack.

### Federation

- No external service. Publish with the Federation chip → **Expect** the
  asset registered in the ownership index, a signed marketplace listing at
  the local listing URL, and an encrypted `CONTENT_OFFER` delivered to a
  trusted test peer.
- Have the test peer purchase the listing → ψ lands in the wallet
  (transaction history shows the listing reason) and the transfer appears in
  the provenance chain (`trace()` walks creation → publication → listing →
  transfer).
