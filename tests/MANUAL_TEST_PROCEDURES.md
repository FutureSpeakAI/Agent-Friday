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

## Regression checklist (recalled models)

Fable 5 and Mythos 5 were pulled. Verify they appear **nowhere**:

- [ ] Header model badge reads "Opus 4.8" (☁️) — never "Fable 5".
- [ ] Settings → AI Model → Orchestrator / Subagent / Creative dropdowns list
      only Opus 4.8, Sonnet 4.6, Haiku 4.5, Gemini, and local Ollama models.
- [ ] First-run setup wizard model step lists the same — no Fable/Mythos.
- [ ] `~/.friday/settings.json`: `orchestrator_model` and
      `model_routing.default_cloud_model` are `claude-opus-4-8`.
- [ ] `grep -ri "fable\|mythos" index.html ui_parts/ *.py routes/ services/`
      returns nothing (comments excepted only where explaining the removal).

This regression is automated in `friday_ui_full.spec.ts` →
"Model selector reflects available models only".
