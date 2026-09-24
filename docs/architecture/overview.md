# Architecture overview

> Status: current for 5.14.0. Last verified against the code: 2026-09-24.
> The top-level [ARCHITECTURE.md](../../ARCHITECTURE.md) is the short
> description of processes, the request flow and the governance hook chain.
> This page adds diagrams. Paths are relative to `src/agent_friday/`.

---

## System overview

```mermaid
graph TB
    subgraph Browser["UI (browser, index.html)"]
        UI[Workspaces and 3D views]
        Chat[Chat panel, windows, tabs]
        Cards[Approval cards]
    end

    subgraph Tray["friday_tray.py"]
        PTT[Push-to-transcribe hook]
    end

    subgraph Server["Flask server (127.0.0.1:3000)"]
        Auth[Auth and locality rule]
        Routes[Blueprints in routes/]
        Loop[Agent loops]
        Exec["_execute_tool + hook chain"]
        Gate[Egress gate]
        Sched[Scheduler]
        Phone["Phone ingress (127.0.0.1:3011)"]
    end

    subgraph Local["Local runtimes"]
        Llama[llama-server seats]
        Ollama[Ollama]
        Voice[Voice workers]
        Comfy[ComfyUI]
        Office[officecli.exe]
    end

    subgraph Cloud["Cloud providers (optional)"]
        Anthropic
        Gemini
        OAI["OpenAI-compatible (OpenRouter and others)"]
    end

    Data[("~/.friday")]

    Tray --> Server
    Browser --> Auth --> Routes --> Loop
    Loop --> Exec
    Loop --> Local
    Loop --> Gate --> Cloud
    Sched --> Loop
    Phone --> Loop
    Exec --> Data
    Server --> Data
```

Every OpenAI-compatible endpoint, local or cloud, runs through the same agentic
tool loop (`services/agent._oai_agentic_loop`); Anthropic models run the Claude
tool loop. Both dispatch tools through `_execute_tool`.

---

## A chat turn

```mermaid
flowchart TD
    A[Message from the UI] --> B{Direct loopback<br/>and not proxied?}
    B -->|No| L[Login required]
    B -->|Yes| C[Assemble context<br/>system prompt with explicit vault control,<br/>memory, pruning and compression]
    C --> D{Vault request and<br/>vault_local_only?}
    D -->|Yes| E[Local seat, or refuse]
    D -->|No| F{Seat chosen for this<br/>conversation or globally?}
    F -->|Yes| G[Use the chosen seat]
    F -->|No| H[Routing mode and task class]
    H --> I{local_only and<br/>no local seat?}
    I -->|Yes| J[Refuse; offer to answer in the cloud]
    I -->|No| G
    E --> K{Cloud provider?}
    G --> K
    K -->|No| M[Call the local model]
    K -->|Yes| N["_seal_or_block:<br/>spending cap, size ceiling,<br/>egress gate seal_outbound"]
    N -->|Gate failed| O[Send blocked]
    N -->|Sealed| P[Call the cloud model]
    M --> Q[Agent loop: tool calls via _execute_tool]
    P --> Q
    Q --> R[Stream reply with the model that answered;<br/>record cost and reasoning trace]
```

---

## The governance checkpoint

```mermaid
flowchart TD
    T[Tool call from any surface] --> R1["Priority 1: governance_rings (critical)"]
    R1 --> G1{Ring and scope allowed?}
    G1 -->|No| DENY[Deny: the model is told it did not run]
    G1 -->|Yes| P1{"Sensitive argument<br/>came from read content?<br/>(services/taint.py)"}
    P1 -->|Refused pattern| DENY
    P1 --> A1["action_gate.authorize:<br/>cLaws pin check, classify"]
    A1 --> C1{Class}
    C1 -->|internal, untainted| ALLOW[Allow]
    C1 -->|forbidden| DENY
    C1 -->|outward, live chat, untainted| CONF[Chat yes/no for this exact action]
    C1 -->|outward, scheduled, grant matches| ALLOW
    C1 -->|otherwise| CARD[Approval card; one approval, one call]
    A1 -->|any failure| HOLD[Hold outward actions]
    ALLOW --> RCPT[Signed receipt in decision-bom.jsonl]
    CONF --> RCPT
    CARD --> RCPT
    RCPT --> NEXT["Priority 10-40: confirmation gate, vault_zt,<br/>sandbox policy, rate limiter"]
    NEXT --> H[Handler runs]
    H --> POST["Post-hooks: cost, provenance record,<br/>audit log, PII scrub, file-grant registration"]
```

The table of hooks and what each one does is in
[ARCHITECTURE.md](../../ARCHITECTURE.md#tool-dispatch-and-the-governance-hook-chain).
The privilege rings still exist inside `_governance_check`: ring 0 and 1 tools
always pass the ring check, ring 2 needs an authenticated or background
session, and ring 3 (mouse, keyboard, screen) needs computer control to be
enabled. The rings decide whether a tool may be considered at all; the action
gate decides whether it may act without you.

---

## Vault tiers

```mermaid
flowchart TD
    Content[Content bound for a model] --> Classify{privacy/vault_access<br/>classification}
    Classify --> T1["TIER 1: public"]
    Classify --> T2["TIER 2: private<br/>contacts, family, personal notes"]
    Classify --> T3["TIER 3: sensitive<br/>financial, medical, legal, identity"]
    T1 --> Any[Any provider: full content]
    T2 --> L2[Local: full content]
    T2 --> C2[Cloud: placeholder]
    T3 --> L3[Local: full content]
    T3 --> C3[Cloud: withheld]
```

Assembly-time gating happens while the prompt is built; the egress gate
enforces the same policy again on the finished payload. Egress decisions are
logged to `~/.friday/vault/egress-log.jsonl`. The classifier's layers, and what
each build actually runs, are in the [threat model](../security/threat-model.md).

---

## Voice

The microphone button asks `GET /api/voice/session-info` which WebSocket to
use for the configured engine:

- **`/ws/voice-local`** (the default): on-device speech recognition
  (faster-whisper) and speech (Piper, or Kokoro on a GPU), or the NVIDIA NeMo
  GPU tier. GPU engines run in leased worker processes
  (`services/voice_workers.py`) that the residency arbiter can evict to the
  CPU with a notice.
- **`/ws/live`**: Gemini Live, used only when the voice engine is `gemini`.

Tools called from voice go through `_execute_tool` like any other, and the
action permission policy ends both voice prompts.

Push-to-transcribe is separate from conversation: the tray's keyboard hook
(`services/push_to_talk.py`) records while the key is held and asks the
server's local speech recogniser for the text, then types it into the window
that had focus when the key went down.

---

## Components that exist but are not wired

- **Federation and Economy** Settings panels are hidden
  (`SETTINGS_SHOW_HELD_FEATURES = false`); their routes and data remain.

---

## Storage

Everything Friday keeps is under `~/.friday` (or `$FRIDAY_HOME`). The full
layout, with what is encrypted, is in the
[configuration reference](../user-guide/configuration.md#what-lives-in-friday).
