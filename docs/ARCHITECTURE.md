# Architecture

Agent Friday's architecture is organized around three pillars: **intelligence** (how Friday thinks), **data sovereignty** (how Friday protects), and **self-improvement** (how Friday evolves).

---

## System Overview

```mermaid
graph TB
    subgraph Frontend["Holographic UI (Browser)"]
        UI[Three.js WebGL Scene]
        Audio[Web Audio API]
        PWA[PWA / Service Worker]
        Chat[Chat Interface]
        Orbs[Process Orbs]
    end

    subgraph Server["Flask Backend (server.py · port 3000)"]
        Auth[Authentication]
        Routes[80+ API Routes]
        Pipeline[Chat Pipeline]
        WS["WebSocket /ws/live"]
    end

    subgraph Intelligence["Intelligence Layer"]
        Pruner["Context Pruner<br/>(sentence-transformers)"]
        Compressor["Context Compressor<br/>(Headroom)"]
        Router[Model Router]
        VaultGate[Vault Access Control]
        PII["Privacy Shield<br/>(PII Scrubber)"]
    end

    subgraph Providers["Model Providers"]
        Claude["Anthropic Claude<br/>(Cloud)"]
        Gemini["Google Gemini<br/>(Cloud + Voice)"]
        Ollama["Ollama<br/>(Local Models)"]
    end

    subgraph Sovereignty["Data Sovereignty"]
        Vault["Sovereign Vault<br/>(AES-256-GCM)"]
        TrustGraph["Trust Graph<br/>(6-dimension scoring)"]
        Memory["Cognitive Memory<br/>(3-tier)"]
        Wiki["Personal Wiki<br/>(~/.friday/wiki/)"]
        HMAC["HMAC Integrity<br/>(SHA-256 signed constraints)"]
    end

    subgraph Evolution["Self-Improvement"]
        SkillOpt["SkillOpt Engine<br/>(versioned skills)"]
        AutoRes["Auto-Research Loop<br/>(Karpathy-inspired)"]
        LiquidUI["Liquid UI<br/>(self-evolving interface)"]
        Personality[Personality Evolution]
        Epistemic[Epistemic Score]
    end

    Frontend --> Server
    Server --> Intelligence
    Intelligence --> Providers
    Intelligence --> Sovereignty
    Server --> Evolution
    Evolution --> Intelligence
```

---

## Chat Pipeline

Every user message flows through this pipeline before reaching a model:

```mermaid
flowchart TD
    A[User Message] --> B{Conversation<br/>exceeds max_turns?}

    B -->|Yes| C["Context Pruner<br/>Embed current prompt +<br/>archive turns with<br/>all-MiniLM-L6-v2.<br/>Keep system msgs sacred.<br/>Keep recent N turn pairs.<br/>Score archive by cosine<br/>similarity → top-k."]
    B -->|No| D[Skip pruning]

    C --> E{Estimated tokens<br/>≥ min_tokens_to_compress?}
    D --> E

    E -->|Yes| F["Context Compressor<br/>Headroom compresses<br/>tool outputs, JSON,<br/>code, and prose.<br/>60-95% token savings."]
    E -->|No| G[Skip compression]

    F --> H[Model Router]
    G --> H

    H --> I{Vault keywords<br/>detected in message?}
    I -->|Yes| J{Local model<br/>available via Ollama?}
    I -->|No| K["Route by mode:<br/>cloud_only / local_preferred / smart"]

    J -->|Yes| L["Force-route to<br/>Ollama local model<br/>(vault_allowed=true)"]
    J -->|No| M{vault_cloud_fallback<br/>setting?}

    M -->|redact| N["Route to cloud<br/>with gated content<br/>(vault content redacted)"]
    M -->|deny / warn| O[Refuse request]

    K --> P[Selected Provider + Model]
    L --> P
    N --> P

    P --> Q["Vault Gate<br/>Classify each content block<br/>TIER 1 / 2 / 3.<br/>Gate by provider."]

    Q --> R{Cloud provider?}
    R -->|Yes| S["PII Scrubber<br/>Privacy Shield strips<br/>remaining patterns:<br/>SSN, phone, email, etc."]
    R -->|No| T["Skip PII scrub<br/>(local model is trusted)"]

    S --> U[Dispatch to Model]
    T --> U

    U --> V["Response<br/>+ CostTracker records<br/>provider, model, tokens, cost"]

    style L fill:#2d5016,color:#fff
    style O fill:#5c1a1a,color:#fff
    style N fill:#5c4a00,color:#fff
```

---

## Model Routing Decision Tree

```mermaid
flowchart TD
    Start[Incoming Request] --> VaultCheck{Contains vault<br/>keywords?}

    VaultCheck -->|Yes| VaultRoute[Force Vault Route]
    VaultCheck -->|No| ModeCheck{Routing mode?}

    VaultRoute --> OllamaAvail{Ollama available<br/>with models?}
    OllamaAvail -->|Yes| LocalVault["Route to local model<br/>vault_allowed=true<br/>scrub_pii=false"]
    OllamaAvail -->|No| Fallback{vault_cloud_fallback?}
    Fallback -->|redact| CloudRedact["Cloud with redaction<br/>vault content gated downstream"]
    Fallback -->|deny| Refuse[Refuse request outright]
    Fallback -->|warn| RefuseWarn["Refuse + tell user<br/>to install Ollama"]

    ModeCheck -->|cloud_only| CloudOnly["Route to Claude<br/>(default_cloud_model)"]
    ModeCheck -->|local_preferred| LocalPref[Try Ollama first]
    ModeCheck -->|smart| SmartRoute[Classify task type]

    LocalPref --> OllamaCheck{Ollama available<br/>with models?}
    OllamaCheck -->|Yes| PickLocal["Pick best local model<br/>by task type + size"]
    OllamaCheck -->|No| FallbackCloud[Fallback to cloud]

    SmartRoute --> TaskType{Task type?}
    TaskType -->|simple| SmallLocal["Smallest local model<br/>(fast response)"]
    TaskType -->|code / research| LargeLocal["Largest local model<br/>(≥4GB preferred)"]
    TaskType -->|tool_use| CloudTools["Cloud model required<br/>(tool support)"]
    TaskType -->|voice| GeminiVoice[Gemini Live pipeline]
    TaskType -->|vault_access| VaultRoute

    style LocalVault fill:#2d5016,color:#fff
    style Refuse fill:#5c1a1a,color:#fff
    style RefuseWarn fill:#5c1a1a,color:#fff
```

### Task Classification

The router classifies the last user message by scanning for intent signals:

| Task Type | Detection | Preferred Route |
|-----------|-----------|-----------------|
| `simple` | Short message (<200 chars), no tools | Smallest local model |
| `code` | Keywords: write code, implement, refactor, debug, function, class, def, import, algorithm | Largest local model (≥4GB) |
| `research` | Keywords: research, analyze, compare, deep dive, explain in detail, comprehensive | Largest local model (≥4GB) |
| `tool_use` | Request includes tool definitions | Cloud (tool support required) |
| `voice` | Voice pipeline active | Gemini Live |
| `vault_access` | Vault keywords or vault tool definitions | Forced local |

---

## Vault Access Control Flow

```mermaid
flowchart TD
    Content[Content to gate] --> Classify{Classify sensitivity<br/>by keyword scan}

    Classify --> T1["TIER 1 — Public<br/>Wiki, news, general docs"]
    Classify --> T2["TIER 2 — Private<br/>Contacts, family, trust graph,<br/>personal notes"]
    Classify --> T3["TIER 3 — Sensitive<br/>Financial, medical, legal,<br/>custody, SSN, encrypted"]

    T1 --> ProvCheck1{Provider?}
    T2 --> ProvCheck2{Provider?}
    T3 --> ProvCheck3{Provider?}

    ProvCheck1 -->|Local| Allow1[ALLOW — full content]
    ProvCheck1 -->|Cloud| Allow2[ALLOW — full content]

    ProvCheck2 -->|Local| Allow3[ALLOW — full content]
    ProvCheck2 -->|Cloud| Redact["REDACT — placeholder:<br/>[VAULT-PROTECTED — private<br/>content withheld from cloud<br/>models. Switch to local<br/>routing to access it.]"]

    ProvCheck3 -->|Local| Allow4[ALLOW — full content]
    ProvCheck3 -->|Cloud| Drop["DROP — empty string<br/>Cloud gets nothing"]

    Allow1 --> Log["Log decision to<br/>~/.friday/vault/context-log/<br/>(append-only JSONL)"]
    Allow2 --> Log
    Allow3 --> Log
    Allow4 --> Log
    Redact --> Log
    Drop --> Log

    style Allow1 fill:#2d5016,color:#fff
    style Allow2 fill:#2d5016,color:#fff
    style Allow3 fill:#2d5016,color:#fff
    style Allow4 fill:#2d5016,color:#fff
    style Redact fill:#5c4a00,color:#fff
    style Drop fill:#5c1a1a,color:#fff
```

### TIER 3 Keywords (Sensitive)
Financial, bank account, routing number, investment, portfolio, tax return, salary, income, health record, medical, medication, prescription, diagnosis, insurance, legal, custody, court, OFW, Our Family Wizard, SSN, social security, passport, driver's license, encrypted, sovereign vault.

### TIER 2 Keywords (Private)
Contact, phone number, home address, family, daughter, partner, personal note, memory, trust graph, relationship, todo, co-parenting schedule.

---

## Voice Mode Pipeline

```mermaid
sequenceDiagram
    participant User
    participant Browser
    participant Flask as Flask Server
    participant Gemini as Gemini Live API<br/>(gemini-3.1-flash-live-preview)

    User->>Browser: Click microphone
    Browser->>Flask: WebSocket connect /ws/live
    Flask->>Gemini: Open Gemini Live session
    Flask->>Browser: Send greeting audio

    loop Conversation
        User->>Browser: Speak
        Browser->>Flask: Audio chunks (base64 PCM)

        Flask->>Flask: Check for vault keywords
        alt Vault content detected
            Flask->>Browser: Spoken refusal +<br/>suggest local model or typing
        else Normal query
            Flask->>Gemini: Forward audio
            Gemini->>Flask: Response audio chunks
            Flask->>Browser: Stream audio response
            Browser->>User: Play audio
        end

        alt User interrupts
            Browser->>Flask: Interruption signal
            Flask->>Gemini: Cancel current response
            Flask->>Browser: Acknowledge interruption
        end
    end

    User->>Browser: End session
    Browser->>Flask: WebSocket close
    Flask->>Gemini: Close session
```

---

## Skill Self-Improvement Loop

```mermaid
flowchart TD
    Exec[Skill Execution] --> Score["Composite Score<br/>accuracy (40%) · satisfaction (25%)<br/>latency (15%) · completeness (10%) · cost (10%)"]

    Score --> Log[Append to metrics.jsonl]
    Log --> Rolling[Calculate 10-execution<br/>rolling mean]

    Rolling --> Check{Rolling mean dropped<br/>>10% below all-time best?}

    Check -->|No| Wait[Continue monitoring]
    Check -->|Yes| Research[Auto-Research Loop fires]

    Research --> Hypotheses["Generate hypotheses<br/>Error patterns? Latency spikes?<br/>Quality drift? Prompt issues?"]

    Hypotheses --> Edits["Propose skill edits<br/>ops: replace / patch / append"]

    Edits --> NewVersion["Create candidate version<br/>(v001 → v002 → ...)"]

    NewVersion --> Epoch["Training Epoch<br/>Score candidate vs baseline<br/>over evaluation batch"]

    Epoch --> Gate{"Validation Gate<br/>1. Within 5% of all-time best?<br/>2. Beats baseline by ≥0.5%?"}

    Gate -->|Pass| Promote["Promote new version<br/>Update best_skill.md<br/>Demote previous champion"]
    Gate -->|Fail| Reject["Reject candidate<br/>Log reason<br/>AutoResearch continues"]

    Promote --> Wait
    Reject --> Wait
    Wait --> Exec

    style Promote fill:#2d5016,color:#fff
    style Reject fill:#5c1a1a,color:#fff
```

---

## Liquid UI Pipeline

```mermaid
flowchart LR
    Signal["Intent Signal<br/>(explicit wish or<br/>behavioral pattern)"] --> Generator["FeatureSpecGenerator<br/>Classify complexity"]

    Generator --> Tier{Complexity tier?}

    Tier -->|trivial < 1m| Auto["Auto-approve<br/>Hot reload"]
    Tier -->|simple 1-5m| Quick[Quick confirm modal]
    Tier -->|medium 5-30m| Review[Spec review + edits]
    Tier -->|complex 30-120m| Detailed["Detailed review<br/>May spawn task"]
    Tier -->|epic 2h+| Full["Full spec + roadmap<br/>Multi-step delivery"]

    Auto --> Build["LiquidUIBuilder<br/>Generate React + Flask<br/>artifacts"]
    Quick --> Build
    Review --> Build
    Detailed --> Build
    Full --> Build

    Build --> Snapshot[Create rollback snapshot]
    Snapshot --> Deploy["Hot reload to UI<br/>(~/.friday/liquid_ui/features/)"]
    Deploy --> SkillOpt["Register with SkillOpt<br/>Track usage as skill"]

    Deploy --> Suggest["SuggestEngine watches:<br/>workspace ping-pong<br/>repeated filters<br/>error loops<br/>dwell-time collapse"]
    Suggest -->|"≥4 occurrences"| Signal
```

---

## Governance: Privilege Rings

```mermaid
graph LR
    subgraph Ring0["Ring 0 — Read-only (Always)"]
        R0["read_file · read_wiki · search_wiki<br/>query_trust_graph · query_calendar<br/>get_career_pipeline · get_briefing"]
    end

    subgraph Ring1["Ring 1 — Local Write (Always)"]
        R1["write_file · write_clipboard<br/>propose_wiki_update · correct_wiki<br/>learn_skill"]
    end

    subgraph Ring2["Ring 2 — Network (Authenticated)"]
        R2["search_web · browse_web · search_email<br/>draft_email · open_url · spawn_task<br/>run_command · install_package"]
    end

    subgraph Ring3["Ring 3 — OS Control (User-enabled)"]
        R3["move_mouse · click · type_text<br/>press_key · screenshot · scroll"]
    end

    Ring0 --> Ring1 --> Ring2 --> Ring3

    style Ring0 fill:#1a3a1a,color:#fff
    style Ring1 fill:#2d4a1a,color:#fff
    style Ring2 fill:#4a3a00,color:#fff
    style Ring3 fill:#4a1a1a,color:#fff
```

Every tool call passes through the governance gate, which:
1. Checks the privilege ring
2. Verifies the HMAC-SHA256 signature on behavioral constraints
3. Applies rate limiting (max 20 OS actions/second for Ring 3)
4. Blocks destructive operations (`rm`, `del`, `format`, `shutdown`, `reg delete`)
5. Logs the decision to `~/.friday/vault/decision-bom.jsonl`

---

## Data Storage Layout

```
~/.friday/
├── settings.json              # All configuration (API keys, routing, etc.)
├── personality.json           # Personality evolution state
├── epistemic_scores.json      # Epistemic self-calibration
├── trust_graph.json           # Relationship trust scores
├── privacy_shield.json        # PII scrubber config + watchlist
├── voice_debug.log            # Voice mode diagnostics
├── memory/                    # Long-term memory entries
├── wiki/                      # Personal wiki (by domain)
│   ├── identity/
│   ├── family/
│   ├── professional/
│   ├── health/
│   ├── legal/
│   └── finance/
├── vault/
│   ├── .governance-key        # HMAC signing key (generated on first run)
│   ├── context-log/           # Access decision audit trail (JSONL)
│   └── decision-bom.jsonl     # Governance decision log
├── skillopt/                  # Skill optimization data
│   └── <skill_name>/
│       ├── versions/          # v001.md, v002.md, ...
│       ├── metrics.jsonl      # Execution log (append-only)
│       ├── best_skill.md      # Current champion artifact
│       ├── config.json        # Weights + thresholds
│       └── research_log.jsonl # Auto-research findings
├── liquid_ui/                 # Self-evolving UI state
│   ├── requests.jsonl         # Intent log
│   ├── features/              # Feature specs + build artifacts
│   ├── snapshots/             # Rollback snapshots (60-day retention)
│   ├── usage.jsonl            # Feature usage events
│   └── suggestions.jsonl      # Proactive suggestions
├── skills/                    # Lightweight YAML skill definitions
├── audio-cache/               # TTS audio cache
└── vibe-code-logs/            # Vibe code terminal logs
```
