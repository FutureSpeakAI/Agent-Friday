# Agent Friday v5.0 — "Super Agent"

*Release date: 2026-07-01 · FutureSpeak.AI · Asimov's Mind*

Agent Friday grows up. v5 is the transformation from a powerful developer tool
into a sovereign consumer product you can install in five minutes and talk to
with **no cloud API key at all**. Under the hood, Friday now learns from her own
work, consolidates memory overnight, models how *you* like to work, and reads
her personality from a file you can edit.

Everything new is **local-first** and passes through the same cLaws governance
rings and egress gate as the rest of Friday. We absorbed the best ideas from
Hermes and OpenClaw — and rejected their security postures.

---

## Highlights

### 🧠 A learning agent, not a static one
Friday now runs a **closed-loop learning engine**. She observes which approaches
succeed for which kinds of task, mines the winners into concise *heuristics*, and
promotes the best ones into her own system prompt. Scoring uses a Wilson lower
bound blended with your satisfaction, so a lucky one-off never gets promoted.
Skills are **advisory text, never executable code** — the loop changes what
Friday is *reminded* of, never what she's *able* to do.

### 💤 Memory that consolidates while you sleep
Every night at 03:00, **memory dreaming** reviews the day's conversations
locally, pulls out the durable facts ("I prefer dark mode", "we decided to ship
Friday"), files them into long-term memory and your user model, and tags the
noise. It writes a readable `~/.friday/dreams/<day>.md` you can browse. No part
of this touches the cloud.

### 👤 Friday learns *you*
The new **user model** quietly tracks your communication style, your domain
expertise, and your workflow, and folds a compact summary into every prompt — so
Friday skips the basics in areas you know cold and explains more where you're
new. It's behavioral preference text (TIER_1), never raw PII, and you can wipe it
any time.

### ✍️ SOUL.md — personality you can edit
Friday's personality is no longer hardcoded. It lives in **`~/.friday/SOUL.md`**,
a plain-markdown file you own. Edit it and Friday changes on the next turn.
Ships with the current persona as the default; every save is versioned.

### 🟢 Zero-friction, zero-key install
Friday now ships a **bundled local model** — `gemma3:4b`, Google's open Gemma 3
4B, which runs on ~8 GB of RAM. The installers auto-install Ollama and pull it,
so **chat works fully offline with no cloud key**. First run greets you *by
voice* and walks you through setup. Cloud keys become optional upgrades for
sharper reasoning, image/video, and richer voice.

### 💬 Friday, everywhere you already are
New **channel bridges** connect Friday to **Discord** and **Telegram**. Every
inbound message runs Friday's real agent loop; every reply passes the egress gate
before it leaves. Bots are disabled by default, allowlist-gated, and their tokens
live in the encrypted credential store.

---

## Under the hood

| Subsystem | Module | Storage | Scheduler |
|-----------|--------|---------|-----------|
| Learning loop | `services/learning_loop.py` | `learning.db` | weekly `learning_epoch` |
| Memory dreaming | `services/memory_dreaming.py` | `dreams.db`, `dreams/` | nightly `memory_dreaming` |
| User modeling | `services/user_model.py` | `user_model.db` | (per-turn) |
| SOUL.md | `services/soul.py` | `SOUL.md`, `soul_history/` | — |
| Onboarding | `services/onboarding.py` | `onboarding.json` | first-run |
| Channels | `services/channels/` | `channels.json` + cred store | on-demand |

New API surfaces (auto-discovered blueprints): `/api/soul*`, `/api/user-model*`,
`/api/learning/*`, `/api/memory/dream*`, `/api/channels/*`, `/api/onboarding/*`.

## Bug fix worth calling out

Launching via the repo-root `python server.py` previously registered **zero** API
blueprints (the shim's `exec()` made discovery look in the wrong directory), so
the entire API 404'd on that path. Fixed — discovery now anchors to the
`agent_friday` package.

## Compatibility

- Default local model changed `gemma4:latest` → **`gemma3:4b`**. If you have a
  different model installed, the local-model picker degrades gracefully to it.
- No default cloud dependency was introduced anywhere.
- 3162 tests pass (64 new).

## Upgrading

```bash
git pull
pip install -e .
friday doctor            # confirms Ollama + gemma3:4b + no-key-mode
ollama pull gemma3:4b    # if the installer didn't already
```

Your existing `agent-personality.txt` still works, but `SOUL.md` takes
precedence once created — copy your persona into it to take over.
