# Server Death Forensics - 2026-08-20 incident

**Investigator:** Claude (Cowork session)
**Evidence captured:** 2026-08-20 14:38 - 14:52 local
**Machine:** VADERSCASTLE
**Tree:** `C:\Users\<user>\Projects\friday-desktop` (~276 uncommitted files from other sessions; nothing committed, reverted, or cleaned during this investigation)

---

## Bottom line

**The server did not crash. It cannot start.**

The working-tree copy of `src/agent_friday/services/agent.py` has a use-before-definition
error that makes the module fail at *import* time. Every start attempt dies roughly two
seconds in, before the server can open a log file, bind port 3000, or acquire its
single-instance lock. Because the tray spawns the server with `stderr=subprocess.DEVNULL`,
the traceback is discarded by the OS. That is the whole mechanism of the "silent death."

The traceback was recovered by re-running the exact same command the tray runs, with stderr
redirected to a file instead of `DEVNULL`:

```
Traceback (most recent call last):
  File "C:\Users\<user>\Projects\friday-desktop\server.py", line 9, in <module>
    exec(compile(open(_target, encoding='utf-8-sig').read(), _target, 'exec'))
  File "C:\Users\<user>\Projects\friday-desktop\src\agent_friday\server.py", line 36, in <module>
    from agent_friday.services.agent import (
  File "C:\Users\<user>\Projects\friday-desktop\src\agent_friday\services\agent.py", line 2701, in <module>
    CLAUDE_TOOL_HANDLERS.update({
NameError: name 'CLAUDE_TOOL_HANDLERS' is not defined
```

---

## 1. What I established (direct evidence)

### 1.1 The import error

A registration block sits *above* the definitions it mutates:

| Symbol | Defined at | Mutated at | Result |
|---|---|---|---|
| `CLAUDE_TOOLS` | line 318 | line 2699 (`.extend`) | OK |
| `CLAUDE_TOOL_HANDLERS` | line **3260** | line **2701** (`.update`) | **NameError** |
| `TOOL_RINGS` | line **3576** | line **2707** (`.update`) | **NameError** (masked; would fire next) |

The offending block is lines 2701-2711 - the `create_workflow` / `run_workflow` /
`workflow_status` tool registration.

**The committed version is correct.** In `HEAD`, `CLAUDE_TOOL_HANDLERS = {` is at line 2981
and every `.update()` call follows it (3282, 3519, 3973, 4090). The ordering bug exists only
in the uncommitted working tree (`git diff --stat`: +693 / -108 on this file).

This class of corruption has happened before in this file. The most recent commit touching
it is:

> `e8c6140` 2026-08-20 00:33:56 - *"fix: rejoin the dict d207fec spliced apart in agent.py - branch compiles again"*

So a dict was spliced apart once, fixed, and has now been spliced apart again.

### 1.2 Why no traceback is ever captured

`src/agent_friday/friday_tray.py`, `start_server()`:

```python
self.server_proc = subprocess.Popen(
    [python_exe, str(SERVER_SCRIPT)],
    cwd=str(PROJECT_DIR),
    creationflags=CREATE_NO_WINDOW,
    stdout=subprocess.DEVNULL,   # <- discarded
    stderr=subprocess.DEVNULL,   # <- discarded
)
```

Both streams go to `DEVNULL`. This is not a logging gap that happens to lose tracebacks -
it is an unconditional discard of everything the child ever writes to its console. Any
failure occurring *before* the app's own file logging is initialised is therefore invisible
by construction. This is the single highest-value thing to change.

### 1.3 The tray never restarts a dead server

`_watchdog()` polls every 5s and, on a state change, only updates the menu label:

```python
alive = (proc is not None and proc.poll() is None) or _port_in_use(PORT)
if alive != self.running:
    self.running = alive
    self._refresh_menu()
```

It never calls `start_server()`. The tray icon and its tooltip ("Agent Friday ... Running on
port 3000") persist regardless of server state. This is why the tray "looks fine" while
nothing is serving - the icon is not a health indicator.

### 1.4 Process state at capture time

- Tray alive: PID 25616 -> 25636 (`pythonw.exe friday_tray.py`), started 14:31:37.
  These two PIDs are one logical process - the venv `pythonw.exe` stub re-execs the real
  Python 3.13 interpreter with identical argv. Confirmed via `nvidia-smi`, which reports the
  resolved path `...\Programs\Python\Python313\python.exe` for the same PIDs.
- **No server process of any kind.** No `python.exe server.py`.
- **No orphaned `llama-server` processes.** None at all - the recurring orphan problem was
  not a factor here.
- No ComfyUI process. No image-generation job in flight.

The "tray alive, server child dead" pattern is **confirmed**, with the refinement that the
child was never successfully born after the reboot.

### 1.5 The machine was rebooted, deliberately, by a human

- `LastBootUpTime` = 2026-08-20 **14:30:54**
- Event 1074 (User32), **14:30:16**: *"The process ...StartMenuExperienceHost.exe has
  initiated the restart of computer VADERSCASTLE on behalf of user VadersCastle\swebs"*

Clean, user-initiated restart via the Start menu. **No bugcheck, no `MEMORY.DMP`, no
minidump, no Kernel-Power 41.** The OS did not fall over.

### 1.6 No OS-level crash record for the server - ever

The Application log for the preceding 16 hours (events 1000 / 1001 / 1026 - AppCrash,
AppHang, .NET runtime) contains **zero** entries matching `python`, `pythonw`, `friday`,
`llama`, `comfy`, or `uvicorn`. A single unrelated `ollama` mention is the only hit.

This is a meaningful negative: there has been **no access violation and no hard crash** of
the server in that window. A process dying of an access violation or being OOM-killed leaves
a WER record. Nothing did. Consistent with a clean, early `sys.exit`-style death - which is
exactly what an import-time `NameError` produces.

### 1.7 Resource state - all clean, all exonerated

- **GPU:** RTX 4070, 12282 MiB total, **1209 MiB used, 10804 MiB free.** No `llama-server`
  processes, orphaned or otherwise. VRAM exhaustion is ruled out for this instance.
- **RAM:** 32 GB total, ~21 GB free. No memory pressure.
- **Port 3000:** nothing bound. Nothing else stole it. The only listener of interest is
  ollama on 11434.

### 1.8 The last healthy server instance

- `~/.friday/friday_server.pid` = PID **9596**, written **2026-08-19T22:15:35** - the last
  successful lock acquisition and startup.
- That instance ran ~16 hours and was still doing background work seconds before the
  reboot: `~/.friday` writes at 14:26:52 (scheduler runs, activity ledger, vault context
  log), 14:27:57 (news archive), 14:28:51 (source trust), 14:30:03-14:30:05 (content
  pipeline, credential audit, google accounts, ambient state), 14:30:09 (residency hardware
  profile) - seven seconds before the restart was issued at 14:30:16.

**That server was healthy when the machine went down.** It did not die; it was shut down.

- Since boot at 14:30:54, the only files written anywhere under `~/.friday` are
  `tunnel-log.txt` and `tunnel-url.txt` (cloudflared). The server has written nothing.

### 1.9 A note on `friday.log`

`~/.friday/friday.log` (3.4 MB) ends at `2026-08-20T13:37:15`, mid-request, with an ordinary
egress `ALLOW` line and no error. **This is not the time of death.** The log is
activity-driven and already contains routine hour-long gaps (11:37 -> 12:37 -> 13:17 ->
13:36). The 13:37 endpoint simply marks the last user activity; section 1.8 shows the
process alive for another 53 minutes. Reading that final line as a death timestamp would
have been the natural mistake here.

---

## 2. What I infer (consistent with evidence, not proven)

**The "six deaths overnight" were most likely six failed *start* attempts, not six crashes
of a running process.**

Supporting timeline:
- `agent.py` working-tree mtime: **2026-08-19 23:28:54**
- Last successful server start: **2026-08-19 22:15:35** - i.e. *73 minutes before* the
  breaking edit landed.

A process that has already imported its modules is immune to a later edit of those files.
So the instance started at 22:15 kept running happily all night on code held in memory,
while every *new* start attempt after 23:28 would have hit the `NameError` and died in
about two seconds, silently, logging nothing. Six such attempts would look exactly like
"the server died six times with no traceback."

**Confidence: moderate.** The mechanism is proven and the timeline fits precisely, but I
cannot directly confirm the count or timing of those six attempts - by design, they left no
trace anywhere. I did search `friday.log` and `friday.log.1` for startup banners and found
no successful start after 22:15:35; however, the server does not appear to emit a
distinctive startup banner, so absence there is weak evidence rather than proof.

**The image-generation / ComfyUI hypothesis is not supported for this instance.** No ComfyUI
process, no image job in flight, no GPU pressure, and the failure is a deterministic
import-time error with no dependency on runtime workload. I would not carry that hypothesis
forward on this evidence, though it cannot be retroactively excluded for the earlier
occurrences.

---

## 3. What remains unknown

1. **The actual count, timing, and trigger of the six overnight occurrences.** No process
   accounting, no stderr, no WER records. Genuinely unrecoverable from this instance.
2. **Whether all six shared this root cause.** The 23:28 edit cannot explain any death
   *before* 23:28. If deaths occurred earlier in the evening, they had a different cause
   that this investigation does not reach.
3. **Whether any earlier occurrence was a true mid-run crash** (as opposed to a failed
   start). The two failure modes are indistinguishable in the surviving evidence precisely
   because stderr is discarded in both.
4. **Which session made the breaking edit**, and whether the +693/-108 diff contains other
   latent breakage beyond the two names identified. I checked only as far as the first
   import error; there may be more behind it.

---

## 4. Instrumentation that would catch this next time

Ordered by value per unit of effort. All are proposals - **none have been implemented.**

### 4.1 Stop discarding the child's stderr (highest value, ~4 lines)

In `friday_tray.py::start_server()`, replace `DEVNULL` with an append-mode file handle:

```python
log_dir = Path.home() / ".friday"
log_dir.mkdir(parents=True, exist_ok=True)
self._child_err = open(log_dir / "server_stderr.log", "ab", buffering=0)
self.server_proc = subprocess.Popen(
    [python_exe, str(SERVER_SCRIPT)],
    cwd=str(PROJECT_DIR),
    creationflags=CREATE_NO_WINDOW,
    stdout=self._child_err,
    stderr=subprocess.STDOUT,
)
```

This alone would have turned six silent overnight deaths into six timestamped tracebacks.
Append mode preserves history across restarts. Keep the handle referenced for the process
lifetime, and close it in `stop_server()`.

### 4.2 Surface non-zero exit codes in the tray

`start_server()` currently calls `_wait_for_health()` and, on failure, leaves
`self.running = False` with no explanation. Capture `self.server_proc.poll()` after the
health wait fails and put the exit code plus the last stderr line in the tray menu label
(e.g. "Server Status: FAILED TO START (exit 1)"). Right now a failed start and a stopped
server are visually identical.

### 4.3 Enable `faulthandler` in the server

At the very top of `src/agent_friday/server.py`:

```python
import faulthandler, os
from pathlib import Path
_fh = open(Path.home() / ".friday" / "faulthandler.log", "a", buffering=1)
faulthandler.enable(file=_fh, all_threads=True)
```

This catches the failure classes that a Python-level `except` cannot: segfaults in native
extensions (torch, PIL, CUDA), stack overflows, and hard aborts. It would not have caught
today's `NameError`, but it is the correct net for the "access violation with no traceback"
scenario that was originally suspected - and it is the only way to distinguish the two.

### 4.4 An import smoke test in the pre-commit / pre-launch path

The failure is deterministic and would be caught by a sub-second check:

```
python -c "import sys; sys.path.insert(0,'src'); import agent_friday.services.agent"
```

Wiring this into `.githooks` (the repo already has a hooks directory) or into the front of
`friday_startup.vbs` would convert a silent no-serve into an immediate, legible failure.
Given that a dict in this specific file has now been spliced apart twice, a guard here is
well justified.

### 4.5 Make the tray icon reflect reality

The static tooltip "Agent Friday by FutureSpeak.AI - Running on port 3000" is asserted at
construction and never updated. It should track `self.running`. As it stands the tray
actively misleads: it looked "visibly running" throughout an outage in which no server
process existed.

### 4.6 Consider whether the watchdog should restart

`_watchdog()` observes death and does nothing about it. A bounded auto-restart (e.g. 3
attempts with backoff, then give up loudly) is worth discussing - **but not before 4.1 is in
place.** Auto-restarting into a broken import would have produced an invisible restart loop
and destroyed the evidence trail, which is arguably worse than staying down. Diagnose first.

---

## 5. Operational notes

- **Cloudflared is up and pointing at a dead origin.** `tunnel-log.txt` / `tunnel-url.txt`
  were being written at 14:37 while nothing listened on port 3000. Any external consumer of
  the tunnel has been getting connection failures for the entire outage.
- **`ANTHROPIC_MODEL` disagrees between launchers:** `friday_startup.vbs` sets
  `claude-sonnet-4-6`; `start.bat` sets `claude-sonnet-5`. Since the Startup-folder shortcut
  runs the VBS, boot-time launches get `4-6` and manual `start.bat` launches get `5`. Worth
  reconciling.
- **Secrets in launchers:** `start.bat` and `friday_startup.vbs` both contain live API keys
  and the account password in plaintext. Both are correctly `.gitignore`d and neither is
  git-tracked (verified read-only), so this is *not* a repository exposure. Still worth
  moving to an untracked `.env` and rotating on principle, since the keys have now been read
  into a session transcript.
- **`logs/` in the repo is empty.** All real logging goes to `~/.friday/`. Anyone looking in
  the repo for logs during an incident will wrongly conclude the app logs nothing.

---

## 6. Evidence preservation

No process was killed, no service restarted, and no file in the working tree was modified
before evidence collection completed. The one intrusive action was re-running `server.py`
as a detached child with stderr redirected to `%TEMP%\friday_server_stderr.txt` - this
exited on its own within ~2 seconds and never bound port 3000 or took the lock, so it
disturbed no state. Nothing was committed, reverted, stashed, or cleaned.

---

# ADDENDUM - Remediation and restoration (2026-08-20 15:20-15:45)

Authorised by Stephen after the Phase 1 report above. **Nothing was committed.**

## A. Exactly what I changed (for the session landing this tree)

Three files. Two are code; one is a new doc. My changes are described precisely so they can
be separated from the other session's uncommitted work.

### A.1 `src/agent_friday/services/agent.py` - PURE RELOCATION, net zero lines

This file already carried another session's uncommitted work (+693 / -108 vs HEAD). **I did
not touch any of that.** My entire change is a block move:

- **Removed** lines 2701-2712 (11 content lines + 1 trailing blank): the
  `CLAUDE_TOOL_HANDLERS.update({...})` and `TOOL_RINGS.update({...})` calls registering
  `create_workflow` / `run_workflow` / `workflow_status`.
- **Re-inserted** verbatim at lines 3798-3808, immediately after the sibling
  `TOOL_RINGS.update({...})` block for the creative tools (which now ends at 3796).

Byte-identical text, no logic change, no reformatting. File total unchanged at **6460
lines**. All other line numbers below 2701 are unaffected; content between 2713 and 3808
shifts up by exactly 12 lines.

The move restores the ordering `HEAD` already has. Post-move positions:

| Symbol | Defined | First mutated |
|---|---|---|
| `CLAUDE_TOOL_HANDLERS` | 3248 | 3549 |
| `TOOL_RINGS` | 3564 | 3792 |

Verified: no reference to either name appears above its definition (the only earlier hit is
a prose comment at line 317).

Backup of the pre-edit file: `%TEMP%\agent.py.before_reorder.bak`

### A.2 `src/agent_friday/friday_tray.py` - stderr capture (+20 lines)

This file was **clean in git** before I touched it, so the whole diff is mine and trivially
separable. Four edits:

1. New module constant beside `VOICE_LOG`:
   `SERVER_STDERR_LOG = Path.home() / ".friday" / "server_stderr.log"`
2. `__init__`: added `self._child_err = None`
3. `start_server()`: replaced `stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL` with an
   append-mode binary handle (`stdout=self._child_err, stderr=subprocess.STDOUT`), preceded
   by a timestamped `===== server start <ISO8601> =====` marker so restarts are separable.
4. `stop_server()`: closes the handle and resets it to `None`, so repeated restarts do not
   leak file descriptors.

Backup: `%TEMP%\friday_tray.py.before_stderr.bak`

### A.3 `friday_startup.vbs` - model string alignment (gitignored, not tracked)

`ANTHROPIC_MODEL` changed from `claude-sonnet-4-6` to `claude-sonnet-5`, matching
`start.bat`. This is the launcher that actually runs at boot, so this is the one that was
taking effect. No objection to the direction - `claude-sonnet-5` is the current model and
the two launchers silently disagreeing is worse than either value.

Backup: `%TEMP%\friday_startup.vbs.before_model.bak`

### A.4 `~/.friday/tunnel-url.txt` - corrected stale hostname (data file, not in repo)

See section C.2. Previous contents backed up to `%TEMP%\tunnel-url.txt.stale.bak`.

## B. Verification - she serves

Restarted via the real production path (`wscript friday_startup.vbs`) after killing the old
tray, so the new code went through the same route it will take at next boot.

- Import smoke test passes: `agent_friday.services.agent` imports clean, **67 handlers, 66
  tool rings** registered.
- Port 3000 bound at **15:28:20**, T+2m23s after launch (startup is genuinely slow - wiki
  merge, model discovery, embedding load, judgment probe battery).
- `GET /` -> **HTTP 200**, 1,508,014 bytes, `<title>FRIDAY Desktop - FutureSpeak.AI</title>`
- `GET /api/health` -> **HTTP 200 in 2.1s**, reporting `anthropic_key: true`,
  `gemini_key: true`, governance enabled (cLaws), 16 creations today.
- **Browser confirmed visually**, not just by port and process: Chrome on `localhost:3000`
  renders the live UI - AGENT FRIDAY header, GENESIS LATTICE, a green **LIVE** indicator, a
  ticking clock, and a populated Edition panel (Career Signals / Your People / Friday's
  Desk).
- The new `~/.friday/server_stderr.log` is working: it captured this startup, including
  Flask's access log. **The instrumentation is live and already earning its keep.**

Note on `/api/health`: a first probe during startup timed out at 15s, then returned 200 in
2.1s once initialisation finished. Given the tray's `_wait_for_health()` uses a 1.5s
per-attempt timeout inside a 30s budget, and full startup takes ~143s, **the tray will
essentially always conclude the server failed to start.** It recovers only because
`_watchdog()` later observes the port is bound. Worth widening that budget; it is currently
misreporting healthy startups as failures.

## C. Nothing was hiding behind the NameError - but three live issues surfaced

Direct answer: **no second import error.** The `NameError` was the only thing blocking
startup, and once fixed the server initialised fully and served. However:

### C.1 GPU headroom is nearly exhausted (flagged, not acted on)

After a healthy start: **11,395 MiB used, 618 MiB free** of 12,282 MiB.

Both `llama-server` processes are **legitimately parented, not orphans** - I verified
parentage before considering any cleanup, and killed nothing:

| PID | Parent | What |
|---|---|---|
| 19048 | 17604 (Friday's server) | `gemma4-12b.gguf`, port 8090, `-ngl 99`, ~10.2 GB |
| 28932 | 29908 (`ollama.exe`) | Ollama's own model server |

So the "eight orphans holding 4.1 GB" pattern did **not** recur here. But with 618 MiB free,
**any image-generation or ComfyUI job needing VRAM will fail or OOM.** This is worth
connecting to the audit's original image-generation hypothesis: not as the cause of *this*
incident (which was an import error), but as a plausible mechanism for a *genuine* crash in
some earlier occurrence. Two local model runtimes (llama.cpp + Ollama) both resident is the
underlying tension. **Hypothesis, not a finding.**

### C.2 The tunnel was fine; the URL file was lying

`cloudflared` (PID 13040, `tunnel --url http://localhost:3000`) is healthy and **now serving
the live server**: the public URL returns HTTP 200, 1,508,014 bytes, correct title, in 1.5s.

But `~/.friday/tunnel-url.txt` contained
`https://inbox-married-instruction-visitor.trycloudflare.com`, which **fails DNS** - it is a
hostname from an old run. The tunnel actually created at 19:37:34Z is
`https://experts-recreational-messenger-naturally.trycloudflare.com`.

Worse, the file's mtime was **15:28:17 today** - the VBS *rewrote* it during my launch and
still wrote the wrong value, meaning its log-scraping regex is matching a stale occurrence
in an 876 KB append-only log rather than the most recent one. I corrected the file contents
but **did not rewrite the VBS scraping logic** - that is a real code change and wants your
call. Until it is fixed, every launch will republish a dead URL.

Separately, cloudflared logs `Failed to refresh DNS local resolver: lookup
region1.v2.argotunnel.com: i/o timeout` every 5 minutes, and binds its ICMP proxy to the
**PIA OpenVPN adapter**. The tunnel works regardless, so this is noise rather than breakage -
but it is VPN interference and explains historical `api.trycloudflare.com: no such host`
failures in the log.

### C.3 A persistent model-routing misconfiguration

Throughout `friday.log`, for hours before and after the restart:

```
endpoints.json points gemma4:e2b at http://127.0.0.1:8091/v1, which is not serving it
local_call HTTP 404 from gemma4:e2b: {"error":"model 'gemma4:e2b' not found"}
```

The running model is `gemma4:12b` on port **8090**; `endpoints.json` points `gemma4:e2b` at
port **8091**, where nothing listens. Every local reasoning call 404s and falls back. Seat
binding reports `reasoning -> gemma4:12b`, so the fallback works, but the configured e2b path
is dead. Unrelated to the outage; worth fixing.

## D. Recommended next step

Item 4.4 above - the import smoke test - is now clearly the highest-value guard:

```
python -c "import sys; sys.path.insert(0,'src'); import agent_friday.services.agent"
```

It runs in about two seconds and would have caught this exact failure the moment the edit
landed, instead of after seven invisible failures and a reboot. A dict in this one file has
now been spliced apart twice. I have **not** wired this into `.githooks` or the VBS - say the
word.

---

# ADDENDUM 2 - Repo unblock: stale git locks cleared (2026-08-20 ~15:50)

A separate session stalled on a denied delete permission and left the repo unwritable. Five
artifacts cleared. **All five were genuinely stale - none had a live owner.**

## Staleness verification (done before any deletion)

1. **No git processes running.** A scan of `Win32_Process` for `git*` and for command lines
   matching `git (maintenance|gc|repack|commit|add|index)` returned nothing.
2. **Exclusive-open test** - the authoritative check on Windows. Each file was opened with
   `FileShare.None`; all four succeeded, which is only possible if no handle is held.
3. **Timing corroboration for `index.lock`:** timestamped **10:31:45**, which predates the
   **14:30:54 reboot**. Its creating process cannot possibly still exist - the machine has
   been power-cycled since. This independently confirms the exclusive-open result.

| Artifact | Size | Timestamp | Verdict |
|---|---|---|---|
| `.git\index.lock` | 0 B | 2026-08-20 10:31:45 | stale - owner died in this morning's restart, then reboot |
| `.git\objects\maintenance.lock` | 0 B | **2026-04-22** 17:48:07 | stale - four months old |
| `.probe3` | 0 B | 2026-08-20 14:46:11 | probe artifact |
| `.write-test-probe` | 0 B | 2026-08-20 14:45:53 | probe artifact |
| `.probedir\` | empty dir | 2026-08-20 14:46:11 | probe artifact |

The `maintenance.lock` dating to April is worth noting on its own: background `git
maintenance` has likely been silently failing in this repo for four months.

## Post-clear verification

- `git status --porcelain` -> **exit 0**, no lock error, 26 entries.
- `git add --dry-run -A` -> **exit 0**, 128 would-be-added paths.
- `git diff --cached --name-only` -> **0 staged files.** Nothing was staged for real.
- `.git\index.lock` not recreated.

**Nothing committed, nothing staged, nothing reverted.**

## Separability note for whoever lands this tree

`git status` now shows **14** modified tracked files. Thirteen are the other session's work,
untouched by me. The fourteenth is `src/agent_friday/friday_tray.py`, which was clean in git
before I edited it - so its entire diff is mine (section A.2). My only change inside the
other session's 13 files is the pure block relocation in `agent.py` (section A.1), which is
net zero lines and byte-identical text.

---

# ADDENDUM 3 - Guardrails installed (2026-08-20 ~17:00-18:10)

Authorised follow-up. **Nothing committed.**

## A. Import smoke test - `scripts/check_imports.py` (NEW file, untracked)

Runs in ~3.5s. Imports `agent_friday.services.agent` then `agent_friday.server`; on failure
it walks to the **deepest traceback frame inside the repo** (the outermost frame is always
the shim, never the bug) and reports symbol, file, line, and source text.

For a `NameError` it goes further: it scans the failing file for a module-level assignment
to that symbol and, if the definition is *below* the use, says so explicitly. Verified
against a synthetic reproduction of the exact 2026-08-19 bug:

```
[import-check] FAILED importing _import_check_selftest_tmp
[import-check] NameError: name 'PLACEHOLDER_HANDLERS' is not defined
[import-check]   at scripts/_import_check_selftest_tmp.py:2
[import-check]   2 | PLACEHOLDER_HANDLERS.update({
[import-check]
[import-check]   'PLACEHOLDER_HANDLERS' IS defined in this file, at line 6 -
[import-check]   which is AFTER line 2 that uses it.
[import-check]   This is a module-level use-before-definition: move the block
[import-check]   at line 2 to below line 6.
[import-check]
[import-check] server startup WILL fail with this error. Fix before launching.
```

Exit 1 on failure, 0 on success. The self-test fixture was deleted immediately after the
run (verified absent). `FRIDAY_IMPORT_CHECK_MODULES` overrides the module list for testing.

### Why three wiring points, not one

**The breaking edit was never committed.** It sat uncommitted in the working tree for ~16
hours. A pre-commit hook alone would not have caught it - which is why the launch path
matters most here.

| Path | File | Behaviour on failure |
|---|---|---|
| **Launch** (where this outage manifested) | `friday_startup.vbs` | Runs pre-flight; on failure shows a `MsgBox` with the full symbol/line report and **refuses to launch** rather than leaving a tray icon over a dead server |
| **Commit** | `.githooks/pre-commit` | Blocks the commit. Skipped when no repo venv exists, so a bare clone is not blocked by a missing-dependency false alarm |
| **CI** | `.github/workflows/tests.yml` | New "Import smoke test" step ahead of the pytest suites |

Note on CI: the existing `ruff check --select E9,F63,F7,F82` step does **not** catch this.
F821 flags names undefined *anywhere*; these names are defined, just too late. Only
executing the import proves module-level statement order.

## B. `_wait_for_health()` widened, and failure made distinguishable

`src/agent_friday/friday_tray.py`:

- New `SERVER_START_TIMEOUT_S = 300.0` (was a hardcoded 30.0). Measured cold start is ~143s;
  the old budget was **structurally guaranteed to expire before a healthy server finished
  booting**, so the tray reported failure on every successful start and only recovered when
  the watchdog later noticed the port.
- Signature is now `_wait_for_health(timeout, proc) -> tuple[bool, str]`. It polls
  `proc.poll()` each iteration, so **a dead child is reported immediately with its exit
  code** instead of burning the full 300s and looking identical to a slow start. Per-request
  timeout raised 1.5s -> 3.0s.
- `_status_label()` now distinguishes three states that were previously collapsed into two:
  `Running` / `FAILED TO START (exit N) - see server_stderr.log` / `Stopped`. A crashed
  server and a deliberately quit one no longer look the same.

Also raised `MAX_WAIT` in `friday_startup.vbs` from **60 -> 300**. Same defect, different
file: the VBS waits for health, then opens Chrome regardless. At 60s against a 72-143s
startup, **Chrome opened on a dead page on every single launch.**

## C. Tunnel URL scrape - fixed and verified live

Root cause, `friday_startup.vbs`: `re.Global = False` plus `matches(0).Value` took the
**first** hostname in an append-only 876 KB log - i.e. the oldest tunnel ever created, dead
for weeks - and republished it as the live URL on every launch.

Three-line fix:
1. Record `preLen = FSO.GetFile(tunnelLog).Size` *before* this run's cloudflared appends.
2. Scan only `Mid(content, preLen + 1)` - this run's output only.
3. `re.Global = True` and take `matches(matches.Count - 1)` - the newest, not the oldest.

Verified by full relaunch with the old tunnel killed:

| | value |
|---|---|
| Previous file contents | `experts-recreational-messenger-naturally...` (stale) |
| New tunnel created | `anniversary-points-survey-pre.trycloudflare.com` |
| `tunnel-url.txt` after relaunch | `anniversary-points-survey-pre...` - **matches newest in log** |
| That URL fetched | **HTTP 200**, 1,508,014 bytes, correct title |

Note: the VBS spawns a new `cloudflared` unconditionally on every launch without checking
for an existing one, so repeated launches accumulate tunnels. Out of scope here; flagged.

## D. `endpoints.json` - investigated, NOT changed, and here is why

The instruction was to fix the mapping. **There is no mapping to fix.** The file already
reads, correctly:

```json
{ "endpoints": { "gemma4:12b": "http://127.0.0.1:8090/v1" } }
```

No `gemma4:e2b` entry, and the file is **auto-generated** by `residency_arbiter` on every
start (rewritten at 15:26:34 during this session's restart), so hand-editing it would be
overwritten within minutes.

The real chain is different, and it is already handled in code:

- `gemma4-e2b.gguf` still exists on disk (6.8 GB), but `gemma4:e2b` was **deleted from the
  Ollama daemon on 2026-08-18** and is not resident.
- `judgment_gate.DEFAULT_JUDGE_MODEL` still names `gemma4:e2b`, **but it is no longer
  returned unconditionally** - `_judge_model()` resolves through
  `local_seats.resolve("judge", ...)`, a fix already made on 2026-08-18 precisely because
  "a module constant naming a model that has been uninstalled bit three modules the same
  day."

So the residual 404 noise is not a stale config value. It is that **e2b is not resident**,
and it cannot be while `gemma4:12b` alone holds 10.2 GB of a 12 GB card with 618 MiB free.
That is the GPU/seat-residency question - explicitly out of my scope for this pass. Making
the judgment layer point at a different model is a change to which model adjudicates egress
decisions, which is a privacy-boundary decision, not a config typo.

**Stopping here and reporting rather than pulling on it.**

## E. Incidental finding - a blueprint is silently not registering

Surfaced by the new import check on every run:

```
[FRIDAY] WARNING failed to import agent_friday.routes.jobs: No module named 'data'
[FRIDAY] WARNING Blueprint auto-discovery: 60 registered, 1 skipped
```

`routes/jobs.py` fails to import, so **the jobs routes are not served at all**. It degrades
to a warning rather than a failure, which is why it has gone unnoticed. Likely the same
`data/` import-path issue as the repo-root `data/` directory. Not touched.

## F. Complete file inventory for this pass

| File | Tracked? | Change |
|---|---|---|
| `scripts/check_imports.py` | new, untracked | the checker (5.5 KB) |
| `.githooks/pre-commit` | tracked | +23 lines, LF preserved |
| `.github/workflows/tests.yml` | tracked | +9 lines, CRLF preserved |
| `src/agent_friday/friday_tray.py` | tracked (already mine) | health wait, status label |
| `friday_startup.vbs` | **gitignored** | import gate, tunnel scrape, MAX_WAIT |
| `~/.friday/tunnel-url.txt` | data file | now written correctly by the fixed scrape |

Backups of every pre-edit file are in `%TEMP%` (`*.before_*.bak`).

`git status` remains: the other session's 13 files untouched; `friday_tray.py` entirely
mine; `agent.py` carries only the net-zero block relocation from Addendum 1.

---

# ADDENDUM 4 - The skipped blueprint: `routes/jobs.py` (2026-08-20 ~18:30-18:45)

## A. What it actually is - and what it is NOT

**It is not a background-job surface, and it is not the missing durable job store.**

`routes/jobs.py` is the **career pipeline**: scanning job listings, scoring and deduping
them, drafting cover letters, and tracking applications. "Jobs" as in employment.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/pipeline/jobs` | GET | list tracked listings (`min_score`, `limit`) |
| `/api/pipeline/scan` | POST | run the scanner; accepts pushed `raw_listings` |
| `/api/pipeline/jobs/<job_id>/apply` | POST | apply; `dry_run=True` by default |
| `/api/pipeline/applications/<application_id>/response` | POST | record an employer response |

It wires two bundled skills (`skills/job_scanner`, `skills/application_engine`) plus
`data/job_tracker_schema.JobTracker` into Flask, with an LLM cover-letter polish that falls
back to a template.

**So the Higgsfield hypothesis does not hold.** The 8 credits burned on a job that reported
success while producing nothing is a *different* gap. A durable store for background media
generation genuinely is missing, but this blueprint was never it and fixing this does not
touch it. That remains open and unbuilt.

## B. What was actually broken - and what was not

**The routes are live right now, and were live before I changed anything.** Verified against
the running server:

```
GET   /api/pipeline/jobs                      -> 200  {"count":0,"jobs":[]}
POST  /api/pipeline/scan                      -> 200  {"keyword_set":["Director of AI",...],...}
POST  /api/pipeline/jobs/<bad-id>/apply       -> 404  (jobs.py's own "job not found")
POST  /api/pipeline/applications/<bad>/response -> 400  "response_kind required"
GET   /api/pipeline/definitely-not-a-route    -> 404  (control)
```

The `400` is the decisive one: an **unregistered** route can only ever produce 404. A 400
proves Flask dispatched into `api_jobs_record_response`.

### The two warnings I reported earlier were produced by my own tool

`check_imports.py` runs as `python scripts/check_imports.py`, so Python puts **`scripts/`**
on `sys.path[0]`, not the repo root. It imports `agent_friday.server`, whose module-level
code runs blueprint discovery and logs into `friday.log`. The 18:03:41 and 18:07:28 warnings
are mine, not the server's. Corrected below. I reported those as evidence of a live defect;
that was wrong, and the timestamps (both immediately before a tray launch, interleaved with
a second `pyautogui loaded` four seconds later) are what disambiguates them.

**However, the underlying defect is real** - just older and conditional. ~70 genuine skips
appear in `friday.log`/`friday.log.1` between **2026-07-01 and 2026-08-19**, from real
server runs.

## C. Root cause: not renamed, moved, or deleted - a sys.path condition

Established from git, not inference:

- `data/job_tracker_schema.py` is **tracked** (`git ls-files data/` returns it), **not
  gitignored** (`git check-ignore` returns nothing), and present since the
  `69e9f27 Initial public release` commit.
- `skills/` is tracked - 9 files.
- Nothing was renamed or removed. `git log --follow` shows no rename.

`data` and `skills` are **top-level packages at the repo root**, outside `src/`. They resolve
only when the repo root is on `sys.path`:

| Launch path | repo root on sys.path? | Result |
|---|---|---|
| `python server.py` (tray, `start.bat`, VBS) | **yes** - Python adds the script's own dir | works |
| `friday` CLI entry point (`agent_friday.cli:main`) | **no** - `cli.py:310` inserts only `src` | **skipped** |
| any launch from a different cwd | no | **skipped** |
| `pip install` wheel | n/a - **files not shipped at all** | **impossible** |

Reproduced deterministically: importing `agent_friday.routes.jobs` from a non-repo cwd
raises `ModuleNotFoundError: No module named 'data'` at `jobs.py:22`.

### The packaging finding, which matters more for public release

`pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

**Only `src/` is packaged.** `data/` and `skills/` are not in the wheel. So a stranger who
runs `pip install agent-friday && friday` gets a Friday whose career pipeline **cannot work
under any circumstances** - the modules are not on their disk, and the `friday` entry point
would not find them even if they were. They would see no error. Just four endpoints that
404 and a feature that appears not to exist.

I have **not** fixed this. The right fix is to move `data/job_tracker_schema.py` and the two
skill packages under `src/agent_friday/`, but `skills/` is clearly a deliberate top-level
concept in this architecture (SKILL.md + config.yaml per skill, a `skills-lock.json`, a
runtime hot-reload watcher over `~/.friday/skills`, sibling `optional-skills/` and
`vibe-mode/` trees). Relocating it is a design decision about the skills system, not a
packaging typo, and it is yours to make.

## D. What I changed

### D.1 `src/agent_friday/routes/jobs.py` (tracked, was clean - whole diff is mine)

Added a guarded repo-root resolution above the three imports, so the dependency is explicit
rather than accidental:

```python
_REPO_ROOT = _Path(__file__).resolve().parents[3]
if (_REPO_ROOT / "data").is_dir() and str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
```

Guarded on `data/` existing, so it is a no-op in an installed wheel rather than a lie. This
is **not** a stub - the real modules load and the endpoints do real work. It fixes every
source-tree launch including the `friday` CLI. It does **not** fix the wheel case (C above),
by design.

Verified: import from a non-repo cwd now returns `<Blueprint 'jobs'>`; full restart shows
**zero skips** (no `Blueprint auto-discovery` warning at all for the 18:41:44 launch); all
four endpoints respond as coded.

### D.2 `scripts/check_imports.py` - my own bug

Now inserts **both** `SRC` and `ROOT`, mirroring what `python server.py` gets. Without this
the checker reported failures the real server never had, and wrote them into `friday.log`.

## E. A problem with the import checker I introduced, which you should decide on

Importing `agent_friday.server` executes module-level code that **starts the whole
application**: the internal scheduler, notification trigger loop, news archiver, network
monitor, connector health monitor, skill hot-reload watcher, MCP client connections, and a
global `Ctrl+Shift+Q` kill hotkey - in a process that then exits ~4s later. Visible in
`friday.log` at 18:07:28.

That runs on **every commit** and **every boot** now. It is idempotent in practice and I have
seen no damage, but it is heavier than a smoke test should be, and registering a global
hotkey from a throwaway process is the kind of thing that bites once and confusingly.

Three options, cheapest first - **not implemented, your call**:

1. **Drop `agent_friday.server` from the module list**, keeping only
   `agent_friday.services.agent` (the module that actually broke). Much lighter, but loses
   coverage of route-module import errors - exactly the class in this addendum.
2. **Guard the side effects** behind an env var (`FRIDAY_IMPORT_CHECK=1`) that the app
   respects by skipping background-thread startup. Correct, but touches app startup.
3. **Replace the import with a static AST check** for module-level use-before-definition. No
   side effects at all and near-instant, but it only catches that one bug class, not the
   general "does this import" question.

I lean 2, then 1. Option 3 alone would not have caught the `data` issue.

## F. Proposal: how blueprint registration failure should behave

**Not implemented - proposal only, as requested.**

### Current behaviour

`server.py:117-130` catches `Exception` per module, appends to `_failed`, logs
`_log.warning("failed to import %s: %s")`, continues, then logs a one-line summary warning.
Nothing else. Consequences:

- A whole API surface can vanish and the app reports itself healthy.
- `/api/health` returns 200 with a rich payload that says nothing about missing routes.
- It went unnoticed for **seven weeks and ~70 restarts**.
- On a stranger's machine it would be equally invisible - worse, because they have no
  baseline for what "should" be there.

This is the same disease as `stderr=DEVNULL`: **the system hides its own injuries.**

### Recommended: criticality tiers, not blanket fail-fast

Blanket "refuse to start on any blueprint failure" is the wrong answer for a public release -
one optional route module with a missing optional dependency would brick the entire app for
a stranger. But silence is worse than both. So:

**1. Mark criticality in the existing manifest.** `ROUTE_MODULES` already exists as an
explicit list for the frozen build. Extend it to distinguish `REQUIRED` from `OPTIONAL`.

**2. Required blueprint fails -> refuse to start.** Non-zero exit, full traceback to stderr
(now captured to `server_stderr.log`), and the tray shows `FAILED TO START` with the reason.
A server missing its core API is not a degraded server, it is a broken one.

**3. Optional blueprint fails -> start, but be loud in three places:**
   - **`/api/health`**: add `"blueprints": {"registered": N, "skipped": [{"module":..., "error":...}]}`
     and flip overall status to `"degraded"`. Machine-checkable, and it is the endpoint the
     tray and any monitoring already hit.
   - **UI**: push a high-priority notification through the notification engine the app
     already has (`notifications.push` - `jobs.py` itself uses it) naming the lost
     capability in user terms: "Career pipeline unavailable - routes failed to load."
   - **`~/.friday/startup-report.json`**: written every boot with the full registered/skipped
     breakdown. Same reasoning as `server_stderr.log` - put it where someone will actually
     look, next to the other diagnostics.

**4. Pin the expected count in CI.** A test asserting the full expected blueprint set
registers would have caught this on 2026-07-01 and every day since. This is the cheapest
durable guard of the four and I would do it first. It also catches the inverse - a blueprint
silently disappearing from the manifest.

**5. Consider `strict` mode for development.** `FRIDAY_STRICT_BLUEPRINTS=1` turning every
skip into a hard failure, on by default in CI and dev, off for end users. Developers should
never accumulate silent skips; end users should never lose the whole app to one.

The general principle worth stating: **degradation must cost something visible.** Every place
this codebase currently degrades silently - `DEVNULL`, the blueprint skip, the `judgment
unavailable` fallback, `local_call`'s 404-and-fall-through - is a place where the system
kept working well enough that nobody investigated, which is precisely how seven weeks pass.

## G. File inventory for this pass

| File | Tracked? | Change |
|---|---|---|
| `src/agent_friday/routes/jobs.py` | tracked, was clean | +16 lines, guarded repo-root sys.path resolution |
| `scripts/check_imports.py` | new, untracked (mine) | sys.path now mirrors production |

Backups: `%TEMP%\jobs.py.before_pathfix.bak`, `%TEMP%\check_imports.py.before_rootfix.bak`.
Nothing committed. The 13 files carrying other uncommitted work remain untouched.

---

# ADDENDUM 5 - Guardrails implemented (2026-08-20 ~19:30-20:05)

Standing principle adopted: **degradation must cost something visible.**

## A. Import-check side effects - fixed with the codebase's own switch

No new mechanism was needed. `server.py:219` already reads:

```python
# ── Background daemons (skipped under FRIDAY_TESTING=1) ───────────
if not _TESTING:
```

Every daemon start - kill hotkey, scheduler, notification loop, news archiver,
network monitor, MCP boot, connector monitor, residency - is already inside that block,
and ~30 modules across the codebase document being "import-safe under FRIDAY_TESTING".
`cli.py:1118` already does `os.environ.setdefault("FRIDAY_TESTING", "1")  # keep import
side effects inert` for precisely this reason.

So `scripts/check_imports.py` now does the same, via `setdefault` so an explicit outer
value still wins. **Zero application code changed.**

Verified: the checker previously appended daemon-start lines to `friday.log` on every run.
Now:

```
friday.log size before: 3453377
[import-check] OK - 2 module(s) imported in 3.7s
friday.log size after : 3453377   (delta 0 bytes)
background daemon lines written: NONE
```

Blueprint discovery still runs (it happens at line 133, before the `_TESTING` gate), so the
checker keeps full coverage of route-import errors while starting nothing.

## B. CI guard - the July-1 catcher

`tests/api/test_blueprint_discovery.py` already existed with two tests. **Neither would have
caught this regression**: `jobs` stayed in `ROUTE_MODULES` (so the manifest test passed) and
no asserted path was a pipeline route (so the endpoint test passed). Five tests added:

| Test | Guards |
|---|---|
| `test_no_blueprint_was_skipped` | **the July-1 catcher** - asserts `skipped == []` |
| `test_every_manifest_module_registered` | every `ROUTE_MODULES` entry actually registered |
| `test_required_modules_are_never_skipped` | REQUIRED tier never silently degrades |
| `test_career_pipeline_endpoints_registered` | all four `/api/pipeline/*` rules, pinned by name |
| `test_startup_report_endpoint_registered` | the integrity endpoint itself exists |

The career-pipeline test is pinned explicitly because those routes depend on `data` and
`skills` at the repo root - the module most likely to stop registering again - and because
this is a live job search, not a demo. All 7 tests pass.

## C. Tiered failure policy - implemented

### C.1 Criticality manifest (`server.py`)

```python
REQUIRED_ROUTE_MODULES = frozenset({'core_routes', 'chat'})
```

Deliberately short. A blanket "any failure is fatal" rule would let one optional module with
a missing optional dependency brick a stranger's entire install - worse than the silence it
replaces. **`jobs` is deliberately OPTIONAL** despite being actively used, because its
dependencies legitimately cannot exist in a pip install (section D). It must be loud, not
fatal.

`ROUTE_LABELS` maps modules to human names so a user is told "Career pipeline unavailable",
never "routes/jobs.py failed to import".

### C.2 Outcome recorded, not just logged

`_discover_and_register_blueprints` now populates `server.BLUEPRINT_REPORT`
(`registered`, `skipped[{module,error}]`, `blueprint_count`), making the result
enforceable, servable and testable.

### C.3 Enforcement

`_enforce_blueprint_policy()` is placed **after** `_fail_loud_and_exit` is defined - the
discovery call near the top of the module runs long before that name exists, so putting the
enforcement inside the discovery function would have reintroduced exactly the module-level
use-before-definition that caused this outage. That was a live trap and I walked into it
once while drafting.

- **REQUIRED fails** -> `_fail_loud_and_exit(...)`: friday.log ERROR, stderr (now captured),
  a Windows message box, exit(1). A server missing its core API is broken, not degraded.
- **OPTIONAL fails** -> starts, and announces in three places:
  1. `friday.log`: `CAPABILITY OFFLINE: <label> - routes/<mod>.py failed to import: <error>`
  2. High-priority notification, worded for a human, with `dedupe_key` so restarts don't spam
  3. `~/.friday/startup-report.json`, rewritten every boot

Everything is inert under `FRIDAY_TESTING`, so tests assert on `BLUEPRINT_REPORT` rather
than exiting the interpreter.

### C.4 New endpoint - `GET /api/startup-report`

New file `src/agent_friday/routes/startup_report.py` (added to `ROUTE_MODULES`). Returns
`status: ok|degraded`, `degraded_capabilities` (human labels), and the full skipped list.
HTTP stays 200 even when degraded - the tray treats non-2xx as "server is dead" and would
restart a server that is running fine but incomplete. Liveness and integrity are different
signals, the same distinction `/api/health` already draws for inference health.

It resolves `BLUEPRINT_REPORT` through `sys.modules` rather than importing `server`, because
`routes/` is imported *by* `server.py` during discovery - a top-level import would be
circular. Falls back to the on-disk report.

### C.5 Proved by fault injection, not just the happy path

Testing only the healthy path would repeat the exact mistake being designed against. A
deliberately unimportable `routes/zz_selftest_broken.py` was injected and the server
restarted. All four surfaces fired:

```
friday.log   CAPABILITY OFFLINE: zz_selftest_broken - routes/zz_selftest_broken.py
             failed to import: deliberate fault injection - blueprint policy self-test

json         "skipped": [{"module": "zz_selftest_broken", "error": "deliberate fault ..."}]

endpoint     {"status":"degraded","degraded_capabilities":["zz_selftest_broken"], ...}

notification title: "zz_selftest_broken unavailable"  priority: high
             kind: degradation  source: startup   (confirmed via /api/notifications)
```

Fixture deleted, server restarted clean, verified back to
`{"status":"ok","blueprint_count":62,"skipped":[]}` with the career pipeline serving 200 and
all 7 tests passing. Had this been `jobs`, the notification would have read **"Career
pipeline unavailable"**.

## D. RELEASE BLOCKER - packaging excludes the career pipeline's dependencies

**Status: BLOCKING for public release. Not fixed - this is a design decision, not a bug fix.**

### The defect

`pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

Only `src/` ships. `routes/jobs.py` (which *is* in the package) imports:

```python
from data.job_tracker_schema import JobTracker      # repo root - NOT shipped
from skills.application_engine import engine        # repo root - NOT shipped
from skills.job_scanner import scanner              # repo root - NOT shipped
```

A stranger who runs `pip install agent-friday && friday` gets a career pipeline that
**cannot work under any circumstances**. The files are not on their disk. With the policy
in section C they will now at least be *told* ("Career pipeline unavailable"), which is a
large improvement over silence - but being reliably informed that a shipped feature is
permanently broken is not the same as shipping a working feature.

Also affected: `skills/application_engine/engine.py:28` and `skills/job_scanner/scanner.py:31`
both import `data.job_tracker_schema`, so the whole subsystem moves or none of it does.

### Options, with trade-offs - Stephen's call

**Option 1 - Move `data/` and `skills/` under `src/agent_friday/`.**
- *For:* actually fixes it; one packaging model; no sys.path shims anywhere.
- *Against:* fights the architecture. `skills/` is plainly a deliberate top-level concept -
  `SKILL.md` + `config.yaml` per skill, a `skills-lock.json`, a runtime hot-reload watcher
  over `~/.friday/skills`, sibling `optional-skills/` and `vibe-mode/` trees. Moving the two
  bundled skills into the Python package makes them structurally different from every other
  skill, which may be exactly wrong for how the skills system is meant to grow.
- *Cost:* 10 tracked files moved, 5 import lines, plus whatever the skill loader assumes.

**Option 2 - Ship them as package data alongside the wheel.**
`[tool.setuptools.package-data]` + a loader that resolves bundled skills from the installed
location.
- *For:* keeps `skills/` conceptually top-level; ships what users need.
- *Against:* package-data is not importable as `skills.*` without a loader shim, so
  `jobs.py` and both skill modules still need their imports reworked. Half a refactor with
  most of the cost of Option 1.

**Option 3 - Make the career pipeline an explicitly optional extra.**
Declare it unavailable unless the source tree is present; document `pip install
agent-friday[career]` as source-only, or drop `routes/jobs.py` from the public build.
- *For:* smallest change; honest; the section C notification already communicates it.
- *Against:* ships a visibly missing feature. For a first public release, "this button does
  nothing for you" is a poor first impression.

**Option 4 - Restructure so bundled skills are first-class installed content.**
The real answer if the skills system is meant to be a public extension point: a defined
install location, a discovery path covering both bundled and user skills, and a documented
contract for third-party skills.
- *For:* solves this permanently and unblocks an ecosystem.
- *Against:* by far the largest, and a product decision about what Friday's skills system is.

**My read, offered not assumed:** Option 3 is the honest short-term unblock for a release
that is imminent, and Option 4 is where this wants to end up. Option 1 is tempting because
it is mechanical, but it quietly answers a product question ("are bundled skills Python
package internals?") inside a bug fix, which is the thing you told me not to do.

## E. Handoff - one line I could not write

`/api/health` does not carry the blueprint block, because `routes/core_routes.py` is one of
the 13 files carrying another session's uncommitted work. The addition is small - inside
`friday_health()`'s returned dict:

```python
"blueprints": {
    "registered": len(server.BLUEPRINT_REPORT.get("registered") or []),
    "skipped": server.BLUEPRINT_REPORT.get("skipped") or [],
},
```

...and flipping `status` to `"degraded"` when `skipped` is non-empty. Until then
`/api/startup-report` is authoritative. Worth doing: `/api/health` is what the tray and any
monitoring already poll.

## F. File inventory

| File | Tracked? | Change |
|---|---|---|
| `scripts/check_imports.py` | untracked (mine) | sets `FRIDAY_TESTING=1` before importing |
| `src/agent_friday/server.py` | tracked, was clean | criticality manifest, labels, `BLUEPRINT_REPORT`, `_enforce_blueprint_policy()`, +`startup_report` in manifest (693 -> 818 lines) |
| `src/agent_friday/routes/startup_report.py` | **new** | `GET /api/startup-report` |
| `tests/api/test_blueprint_discovery.py` | tracked, was clean | +5 tests |

Backups: `%TEMP%\server.py.before_bppolicy.bak`,
`%TEMP%\check_imports.py.before_testingguard.bak`. Nothing committed. The 13 files carrying
other uncommitted work remain untouched.
