# Integration Connectors Flow

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active |
| **Type** | Integration / Extensibility |
| **Complexity** | High (22 connector modules, 6 categories, hub-and-spoke architecture) |
| **Last Analyzed** | 2026-03-24 |

## Overview

Agent Friday uses a hub-and-spoke connector architecture where each external service (PowerShell, VS Code, Adobe, Slack, Telegram, ComfyUI, etc.) is encapsulated as a connector module with three exports: `detect()` for availability checking, `TOOLS` for tool declarations, and `execute()` for tool dispatch. The ConnectorRegistry singleton auto-discovers installed software at startup, builds a tool routing table, and injects available tools into Claude's function-calling surface. Connectors span 6 categories (foundation, creative, office, devops, communication, system) and are supplemented by the SuperpowerStore for user-installed third-party extensions.

## Flow Boundaries

| Boundary | Location |
|----------|----------|
| **Start** | `connectorRegistry.initialize()` called during boot (`index.ts:791`) |
| **End** | Tool result returned to Claude tool loop or error propagated |

## Component Reference

| Component | File | Purpose |
|-----------|------|---------|
| Connector Registry | `src/main/connectors/registry.ts` | Central hub: module loading, detection, tool routing, status |
| PowerShell Bridge | `src/main/connectors/powershell.ts` | COM automation, registry, WMI, services, Windows control |
| Terminal Sessions | `src/main/connectors/terminal-sessions.ts` | Persistent shells, build watchers, REPL, process management |
| VS Code Bridge | `src/main/connectors/vscode.ts` | File editing, terminal, extensions, debugging, workspace |
| Git & DevOps | `src/main/connectors/git-devops.ts` | Git workflows, Docker, npm/yarn/pnpm, cloud CLIs |
| Office Automation | `src/main/connectors/office.ts` | Word, Excel, PowerPoint via COM automation |
| Adobe Suite | `src/main/connectors/adobe.ts` | Photoshop, Illustrator, Premiere via ExtendScript/UXP |
| 3D & VFX | `src/main/connectors/creative-3d.ts` | Blender Python scripting, Unity/Unreal editor |
| Media & Streaming | `src/main/connectors/media-streaming.ts` | OBS WebSocket, audio routing, FFmpeg pipelines |
| ComfyUI | `src/main/connectors/comfyui.ts` | Local Stable Diffusion image generation |
| Coding Kit | `src/main/connectors/coding-kit.ts` | Code reading, editing, shell execution, multi-provider LLM |
| Video Gen | `src/main/connectors/video-gen.ts` | VEO 3 text-to-video, image-to-video, FFmpeg stitching |
| Audio Gen | `src/main/connectors/audio-gen.ts` | Gemini 2.0 Flash + ElevenLabs music/audio/voice synthesis |
| Polymath Router | `src/main/connectors/polymath-router.ts` | Unified creative dispatch and pipeline orchestration |
| Stage Presenter | `src/main/connectors/stage-presenter.ts` | Creative output feed: push, list, pin, export artefacts |
| Communication Hub | `src/main/connectors/comms-hub.ts` | Slack/Discord/Teams webhooks, SMTP email, HTTP requests, Windows toast |
| Dev Environments | `src/main/connectors/dev-environments.ts` | Jupyter, Python venvs, conda, Docker Compose, databases |
| UI Automation | `src/main/connectors/ui-automation.ts` | Windows UI Automation API via accessibility tree |
| System Management | `src/main/connectors/system-management.ts` | Windows services, scheduled tasks, network, firewall |
| World Monitor | `src/main/connectors/world-monitor.ts` | Real-time global intelligence (44 API endpoints) |
| Firecrawl | `src/main/connectors/firecrawl.ts` | Web search, page scraping, site crawling |
| Messaging Gateway | `src/main/gateway/gateway-connector.ts` | Proactive messaging via Telegram/Discord/Slack |
| Perplexity | `src/main/connectors/perplexity.ts` | AI-powered web search and deep research |
| OpenAI Services | `src/main/connectors/openai-services.ts` | DALL-E 3 images, o3 reasoning, Whisper, embeddings |
| PageIndex | `src/main/connectors/pageindex.ts` | Vectorless reasoning-based RAG for PDFs |
| Superpower Store | `src/main/superpower-store.ts` | User-installed third-party tool extensions |
| Adapter Engine | `src/main/adapter-engine.ts` | Validates and adapts third-party connectors |
| Integration Handlers | `src/main/ipc/integration-handlers.ts` | IPC handlers for connector registry and gateway |
| Consent Gate | `src/main/consent-gate.ts` | User consent for outbound communication tools |
| IntegrationsStep | `src/renderer/components/onboarding/IntegrationsStep.tsx` | Onboarding UI for Calendar, Obsidian, Gateway, system toggles |
| FridayCalendar | `src/renderer/components/apps/FridayCalendar.tsx` | Calendar app: auth, events, create/delete |
| FridayGateway | `src/renderer/components/apps/FridayGateway.tsx` | Gateway management: pairings, channels, contacts |

## Detailed Flow

### 1. Registry initialization (`connectors/registry.ts:64-111`)

During boot (`index.ts:791`), `connectorRegistry.initialize()`:

1. **Load modules** (`registry.ts:117-351`): Iterates a hardcoded list of 22 module definitions. Each is `require()`-loaded dynamically. If a module file is missing (Wave 2 modules not yet implemented), it is skipped gracefully. Each module must export `TOOLS`, `execute`, and `detect`.

2. **Parallel detection** (`registry.ts:75-104`): All loaded modules' `detect()` functions run via `Promise.allSettled()`. Detection checks whether the software is installed (e.g., PowerShell checks `powershell.exe`, VS Code checks `code` CLI, ComfyUI checks localhost:8188). Failures default to `available: false`.

3. **Build routing table** (`registry.ts:95-98`): For available connectors, each tool name is mapped to its connector ID in `toolToConnector`.

4. **Category assignment**: Connectors are grouped into 6 categories:
   - **foundation**: PowerShell, Terminal Sessions
   - **devops**: VS Code, Git & DevOps, Coding Kit, Dev Environments
   - **creative**: Adobe, 3D/VFX, Media/Streaming, ComfyUI, Video Gen, Audio Gen, Polymath Router, Stage Presenter, OpenAI Services
   - **office**: Office Automation
   - **communication**: Communication Hub, Messaging Gateway
   - **system**: UI Automation, System Management, World Monitor, Firecrawl, Perplexity, PageIndex

### 2. Tool declaration aggregation (`registry.ts:359-367`)

`getAllTools()` collects tool declarations from all available connectors. These are sent to Claude/Gemini as function declarations. Only tools for **detected** software appear, keeping the function-calling surface lean.

### 3. System prompt injection (`registry.ts:444-483`)

`buildToolRoutingContext()` generates a dynamic personality/prompt section grouped by category:
```
## Software Connectors -- Installed & Available
### Foundation (PowerShell, Terminals)
- **PowerShell Bridge**: COM automation, registry, WMI...
  Tools: run_powershell, ...
### Development & DevOps
...
```
This is injected into the system prompt so the agent knows what tools are available.

### 4. Tool execution routing (`registry.ts:372-390`)

When Claude calls a tool:
1. `executeTool(toolName, args)` looks up `toolToConnector` to find the connector.
2. If the connector is not found or not available, returns `{ error: ... }`.
3. The connector's `execute(toolName, args)` is called.
4. Errors are caught and wrapped in `{ error: ... }` ToolResult format.

### 5. Communication Hub consent flow (`comms-hub.ts:984-1004`)

Six of the seven Communication Hub tools require user consent before execution:
- `slack_send_webhook`
- `discord_send_webhook`
- `teams_send_webhook`
- `smtp_send_email`
- `http_request`
- `webhook_send`

Only `notification_toast` (local-only) is exempt. The consent gate:
1. Checks if integrity system is in safe mode (auto-deny).
2. Sends `desktop:confirm-request` to the renderer.
3. User has 30 seconds to approve/deny.

### 6. Webhook safety (`comms-hub.ts:256-304`)

All webhook tools validate URLs:
- Must use HTTPS protocol
- Must not target localhost, `127.0.0.1`, `::1`, `.local`
- Must not target RFC-1918 private IP ranges (10.x, 172.16-31.x, 192.168.x)
- Must not target link-local (169.254.x)

### 7. Onboarding integration (`IntegrationsStep.tsx:45-409`)

The onboarding wizard's IntegrationsStep configures:
- **Google Calendar**: OAuth flow via `window.eve.calendar.authenticate()`
- **Obsidian Vault**: Path saved via `window.eve.settings.setObsidianVaultPath()`
- **Messaging Gateway**: Toggle + Telegram bot token + owner chat ID
- **System toggles**: Auto-launch on Windows login, file watcher

All integrations are optional. Existing settings are loaded on mount.

### 8. SuperpowerStore for third-party extensions

The SuperpowerStore (`superpower-store.ts`) extends the connector model:
1. Third-party repos are analyzed by the Adapter Engine.
2. Security verdicts are reviewed (risk level: low/medium/high).
3. User confirms install with explicit consent.
4. Enabled superpowers contribute their tools to the tool surface.
5. Health tracking: error counts degrade health scores; auto-disable after 5 errors.
6. Tools are distinct across superpowers (no name collisions).

## IPC Channels Used

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `connectors:list-tools` | Renderer -> Main | Get all tool declarations from available connectors |
| `connectors:call-tool` | Renderer -> Main | Execute a connector tool by name |
| `connectors:is-connector-tool` | Renderer -> Main | Check if a tool name belongs to the connector system |
| `connectors:status` | Renderer -> Main | Get registry status (connector count, availability, tool count) |
| `connectors:get-tool-routing` | Renderer -> Main | Get the dynamic tool routing context for prompts |
| `gateway:set-enabled` | Renderer -> Main | Enable/disable messaging gateway |
| `gateway:get-status` | Renderer -> Main | Gateway status with channel list |
| `calendar:authenticate` | Renderer -> Main | Trigger Google Calendar OAuth flow |
| `calendar:is-authenticated` | Renderer -> Main | Check calendar auth status |
| `calendar:get-today` | Renderer -> Main | Fetch today's events |
| `settings:set` | Renderer -> Main | Save individual setting values |
| `settings:setObsidianVaultPath` | Renderer -> Main | Save Obsidian vault path |
| `settings:setTelegramConfig` | Renderer -> Main | Save Telegram bot token + owner ID |
| `settings:setAutoLaunch` | Renderer -> Main | Toggle auto-launch on Windows login |
| `desktop:confirm-request` | Main -> Renderer | Push consent confirmation to UI |

## State Changes

| State | Trigger | Effect |
|-------|---------|--------|
| Connector detected | `detect()` returns true during init | Tools added to routing table and Claude's function surface |
| Connector unavailable | `detect()` returns false or throws | Connector tracked but tools excluded from routing |
| Tool execution error | `execute()` throws | Error wrapped in ToolResult, connector remains available |
| Superpower installed | User confirms install | Tools added to enabled pool |
| Superpower disabled | Manual disable or 5+ errors | Tools removed from enabled pool |
| Superpower health degraded | `recordError()` called | Health score decreases; auto-disable at threshold |
| Gateway enabled | User toggles in UI | Gateway manager initializes, Telegram adapter starts |
| Calendar connected | OAuth flow completes | Events available via `calendar:get-today` |
| Consent denied/timeout | User does not approve within 30s | Tool action cancelled, "user denied" result returned |

## Error Scenarios

| Scenario | Behavior |
|----------|----------|
| Module file missing (`MODULE_NOT_FOUND`) | Skipped with log: "not yet implemented" |
| Module missing required exports | Warning logged, module skipped |
| Detection throws | `available: false`, connector still registered for status |
| Tool execution throws | Error caught, returned as `{ error: "connector label error: message" }` |
| Webhook URL is HTTP (not HTTPS) | Throws: "Webhook URL must use HTTPS" |
| Webhook URL targets private IP | Throws: "must not target internal/private IP ranges" |
| SMTP auth fails | Error propagated: "AUTH password failed: 535 ..." |
| Consent gate timeout (30s) | Auto-deny; tool returns "user denied" |
| Safe mode active | All consent-gated tools auto-denied |
| Superpower errors exceed threshold | Superpower auto-disabled; other superpowers unaffected |
| Unknown tool name | Returns `{ error: "Unknown connector tool: ..." }` |
