# RSI Next Targets — queued for the frontier spec stage (Fable 5.1 Max)

> Status: queue as of 2026-09-21; superseded in part by `phase-plan.md`.


> Queue file read by `rsi-spec-frontier` when selecting its next target.
> Priority order is authoritative: Target 1 before Target 2.
> Every spec written from this queue must obey the AGENTS.md rules
> (venv-only checks, pre-written failing tests as ground truth, no
> sensitive-subsystem edits without flagged review, work on a branch).

---

## Target 1 — Bonsai2 serving-config optimization (PRIORITY)

**Problem.** The local brain seat (bonsai2:27b) is served by llama.cpp at
:8090 with generous headroom left unused:

- Quantization is PTQ1_0 (~1.75 bpw ternary, whole model ≈ 5.9 GB). A
  Q4-class quant (~15 GB) of the same model should be measurably sharper
  on code tasks if VRAM allows.
- Context is capped at 32K of a 262K trained window. Raising `n_ctx`
  (64–128K, memory permitting) directly improves spec+file co-residency
  during implementation runs.
- Temperature and KV-cache quantization live in the llama.cpp launch
  flags (wherever Friday starts the server); implementation runs want
  low temperature (~0.2) and KV-cache quantization frees VRAM for
  context headroom.

**Spec requirements.**
1. Locate how the llama.cpp server is launched (startup script/flags).
2. Define a before/after benchmark: a small fixed set of code-editing
   tasks with objective pass/fail (existing unit tests are the model),
   plus tokens/sec and VRAM usage recorded for each config.
3. Evaluate, in order of expected value:
   a. quant swap PTQ1_0 → Q4-class (only if VRAM covers it; measure,
      don't assume),
   b. `n_ctx` raise,
   c. KV-cache quantization,
   d. per-role sampling temperature (implement runs at ~0.2).
4. Every change must be trivially reversible: keep the old launch
   config; rollback = restore it and restart the server.
5. No change ships without the benchmark showing neutral-or-better
   quality AND acceptable latency on this hardware.

**Constraints.**
- Do not delete the PTQ1_0 model file; both quants coexist on disk.
- Do not touch governance/privacy subsystems; this is serving config only.
- Verification commands run through `.venv\Scripts\python.exe`.

---

## Target 2 — Notification pipeline with reasoning transparency

**Problem.** Friday cannot push notifications between turns; task/workflow
completion is only discoverable by polling. Status labels have been
demonstrably unreliable (workflows reported "interrupted" while actively
writing files), so notifications must carry evidence, not labels.

**Spec requirements.**
1. A push channel from background tasks/workflows to the UI: completion,
   failure, and milestone events surface as toasts in the holographic UI
   (and optionally OS notifications).
2. Each notification carries its *why*: the triggering step, artifacts
   written (paths + mod times), and a short reasoning summary — full
   context and transparency into the reasoning thread, not just "done".
3. Deep-links: clicking a notification opens the process/reasoning log in
   a new holographic window, or in a Chrome tab (same pattern as the
   chat window spin-out).
4. Notification relevance is filterable — the user tunes which event
   classes ping him.
5. Ground truth: pre-written failing tests for the event pipeline, plus
   a manual UI acceptance checklist.

**Constraints.**
- UI changes edit both `index.html` and `ui_parts/app.html` (see
  docs/development/ui-build.md).
- No private data in notification payloads that would leave the machine;
  this is a local UI channel.

---

*Queued 2026-09-20 by Agent Friday at the owner's direction. Lesson baked
in from the 2026-09-19 run: spec quality is the ceiling on local-model
autonomy — write specs for Bonsai2 as executor, with exact files,
functions, and failing tests named.*
