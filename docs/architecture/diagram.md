# Architecture diagrams

> Last checked against the code on 2026-10-17 (origin/main). Paths are relative
> to `src/agent_friday/`. The prose that goes with these diagrams is in
> [ARCHITECTURE.md](../../ARCHITECTURE.md) and
> [overview.md](overview.md). When a diagram and the code disagree, the code
> is right.

## 1. Component layout

Processes, the main server-side subsystems and where state is kept.

```mermaid
flowchart TD
    Tray["friday_tray.py: tray and push-to-transcribe hook"]
    Browser["Browser: index.html and static/*.js"]

    subgraph Server["server.py: Flask app on 127.0.0.1, port 3000 by default"]
        Routes["routes/*: blueprints, auto-discovered"]
        Chat["routes/chat.py: chat(), chat_stream()"]
        Laya["Laya: reflex_chat, laya_router, chat_tool_routing"]
        Router["routing/model_router.py: picks the seat"]
        Exec["services/model_router.py: calls models, _seal_or_block"]
        Agent["services/agent.py: agent loops, _execute_tool, hooks"]
        Egress["services/egress_gate.py: seal_outbound"]
        Floor["services/judgment_gate.py: never-send floor, raw-record holds"]
        Vault["privacy/vault_access.py and vault_policy.py"]
        Gate["governance/action_gate.py: classify, decide, signed receipt"]
        Approvals["services/approvals.py: cards"]
        Sched["services/scheduler.py: 60-second tick"]
        Jobs["services/visible_jobs.py and local_scheduler.py: Local AI queue"]
        Setup["services/setup_chat.py, setup_steps.py, setup_record.py"]
    end

    subgraph Local["Local runtimes"]
        Arbiter["services/residency_arbiter.py"]
        Llama["llama-server seats, ports from 8090"]
        Ollama["Ollama, localhost:11434"]
        VoiceW["voice workers"]
        MCP["MCP servers: stdio or Streamable HTTP"]
    end

    Cloud["Cloud providers: Anthropic, OpenRouter, Gemini, other OpenAI-compatible"]
    Data[("Data folder: ~/.friday or FRIDAY_HOME")]

    Tray --> Server
    Browser --> Routes
    Routes --> Chat
    Chat --> Laya
    Chat --> Router
    Router --> Exec
    Exec --> Agent
    Exec --> Egress
    Egress --> Floor
    Egress --> Cloud
    Exec --> Arbiter
    Arbiter --> Llama
    Arbiter --> Ollama
    Arbiter --> VoiceW
    Agent --> Vault
    Agent --> Gate
    Gate --> Approvals
    Agent --> MCP
    Sched --> Jobs
    Jobs --> Exec
    Setup --> Data
    Approvals --> Data
    Gate --> Data
    Sched --> Data
```

## 2. A chat turn through the gates

One typed turn on `/api/chat` (or `/api/chat/stream`, which runs the same
`chat()` on a worker thread). Optional or trial-mode steps are marked.

```mermaid
flowchart TD
    A["Message posted to /api/chat"] --> B["Pasted keys withheld: pasted_secrets"]
    B --> C{"Reflex answer? reflex_chat"}
    C -->|"yes to a waiting card, on the owner's page"| C1["Card claimed by compare-and-set"]
    C -->|"no reflex"| D["Turn shape logged: reflex_turn, shadow only"]
    D --> E["routing/model_router.route: vault check, chosen seat, routing mode"]
    E --> G["System prompt: privacy summary, frozen memory head, vault-gated context"]
    G --> F{"chat_tool_routing set to laya? off unless chosen"}
    F -->|"tool decision"| T["_execute_tool"]
    F -->|"model"| H{"Seat is local?"}
    H -->|"yes"| L["Local model call"]
    H -->|"no"| S["_seal_or_block: spend cap, size ceiling"]
    S --> N["egress_gate.seal_outbound"]
    N --> NF{"Never-send floor hit?"}
    NF -->|"yes"| X["Whole call blocked"]
    NF -->|"no"| TP{"Unrestricted consent?"}
    TP -->|"yes"| CC["Cloud model call"]
    TP -->|"no"| TC["Scrub identifiers, withhold private and sensitive spans"]
    TC --> CC
    L --> LOOP["Agent loop asks for a tool"]
    CC --> LOOP
    LOOP --> T
    T --> R1["Hook 1 governance_rings: rings, taint, action_gate.authorize"]
    R1 --> V{"Verdict"}
    V -->|"deny"| DN["Model told it did not run"]
    V -->|"allow"| HK["Hooks 5 to 40: verifier, confirmation, vault_zt, sandbox, rate limit"]
    V -->|"confirm or card"| CARD["Approval card, from the first ask"]
    CARD --> DEC{"Owner decides: chat card, Activity, popup, System > Approvals"}
    DEC -->|"approve"| HK
    DEC -->|"decline"| DN
    HK --> RUN["Handler runs"]
    RUN --> POST["Post-hooks: cost, taint record, audit, PII scrub, file grants"]
    POST --> RC["Receipts: tool_receipts, decision-bom.jsonl"]
    RC --> OUT["Reply checked by completion_receipts, then returned"]
```
