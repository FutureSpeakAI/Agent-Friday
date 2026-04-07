# Text Chat Flow

## Quick Reference

| Property | Value |
|----------|-------|
| **Status** | Active, primary text interaction path |
| **Type** | Request-response with streaming |
| **Complexity** | Medium (multi-provider routing, tool loops, memory extraction) |
| **Last Analysed** | 2026-04-07 |

## Overview

The text chat flow handles typed user messages from the renderer through to LLM processing and response rendering. Messages are entered via `TextInput`, routed through `App.tsx`'s `handleTextSend` to the unified tool loop, and responses stream back into the Zustand store for display by `ChatHistory`. Post-response hooks extract memories, create episodic records, and calibrate personality.

As of Sprint 8 (v3.14.0), the former three-branch implementation (local Ollama / Gemini Live / Claude Express) has been replaced by a unified tool loop that routes all providers through `llmClient.complete()`. The tool loop emits `ToolLoopEvents` for real-time UI streaming and tracks per-turn cost/usage via the cost tracker. Direct Anthropic SDK instantiation is no longer used in the chat path.

## Flow Boundaries

- **Start**: User presses Enter in `TextInput.tsx` (or clicks Send button)
- **End**: Assistant response rendered in `ChatHistory.tsx`, memories extracted asynchronously

## Component Quick Reference

| Component | File | Purpose |
|-----------|------|---------|
| TextInput | `src/renderer/components/TextInput.tsx` | Textarea with Enter-to-send, auto-resize, connection status dot |
| App (handleTextSend) | `src/renderer/App.tsx:1133` | Routes text to correct backend, manages optimistic UI updates |
| App (sendText) | `src/renderer/App.tsx:857` | Low-level dispatcher: local IPC vs Gemini WebSocket |
| Zustand Store | `src/renderer/store/app-store.ts` | `messages[]` state, `addMessage`, `appendToLastAssistant` actions |
| ChatHistory | `src/renderer/components/ChatHistory.tsx` | Renders messages with Markdown, code blocks, thinking dots |
| Express Server | `src/main/server.ts:209` | `POST /api/chat` endpoint for Claude text chat |
| handleClaude | `src/main/server.ts:816` | Gathers tools, builds system prompt, delegates to tool loop |
| runClaudeToolLoop | `src/main/server.ts:459` | Iterative tool-use loop (Anthropic, OpenRouter, or local) |
| LLMClient | `src/main/llm-client.ts` | Unified provider abstraction with fallback chain |
| Providers | `src/main/providers/index.ts` | Registers Anthropic, OpenRouter, HuggingFace, Ollama |
| AnthropicProvider | `src/main/providers/anthropic-provider.ts` | Direct Anthropic SDK wrapper with retry logic |
| ChatHistoryStore | `src/main/chat-history.ts` | Persists messages to `{userData}/memory/chat-history.json` |
| Chat History IPC | `src/main/ipc/chat-history-handlers.ts` | `chat-history:load`, `chat-history:save`, `chat-history:clear` |
| Personality | `src/main/personality.ts` | Builds system prompt with memory context, relationship data |
| Privacy Shield | `src/main/privacy-shield.ts` | Scrubs PII before cloud providers, rehydrates responses |

## Detailed Steps

### 1. User Types and Submits (Renderer)

1. User types in `TextInput.tsx` textarea (line 102-114).
2. On Enter (without Shift), `handleSubmit()` fires at line 49, calling `onSend(trimmed)`.
3. The `onSend` prop is bound to `handleTextSend` in `App.tsx:1417`.

### 2. Optimistic UI Update (Renderer)

4. `handleTextSend` (App.tsx:1133) immediately adds the user message to Zustand store via `setMessages()` at line 1136-1144. This gives instant feedback before any backend responds.

### 3. Backend Routing (Renderer)

5. The function checks which backend is active:
   - **PersonaPlex active** (line 1148): Returns a "voice-only" notice (PersonaPlex has no text input channel).
   - **Local conversation active** (line 1163): Inserts a pending assistant placeholder (thinking dots), then calls `sendText(text)` which routes to `window.eve.localConversation.sendText(text)` via IPC.
   - **Neither active** (line 1184): Auto-connects if needed (waits up to 30s for in-progress connections), then routes to either `sendText(text)` or `geminiLive.sendTextToGemini(text)`.

### 4a. Local Path (Main Process)

6. `window.eve.localConversation.sendText(text)` triggers the local conversation loop in `src/main/local-conversation.ts`.
7. Text is processed through Ollama (local LLM) with the personality system prompt.
8. Response chunks stream back via IPC events:
   - `localConversation.onTranscript` -- user text echo
   - `localConversation.onResponseChunk` -- streaming tokens (App.tsx:534-558)
   - `localConversation.onResponse` -- final complete text (App.tsx:561-594)
9. TTS synthesis runs in parallel if available (Kokoro/Piper).

### 4b. Gemini Path (Renderer WebSocket)

6. `geminiLive.sendTextToGemini(text)` sends text through the existing Gemini Live WebSocket.
7. Gemini processes with the system instruction and responds with audio + text.
8. Text responses arrive via `onTextResponse` callback (App.tsx:110-130), appending/creating assistant messages in the store.

### 4c. Claude/Express Path (Not directly from handleTextSend)

Note: The Express `POST /api/chat` endpoint exists in `server.ts:209` but is **not** called by `handleTextSend` directly. It serves as a general-purpose text chat API that can be used by external clients or gateway integrations. The flow:

6. Client sends `{ message, history }` with Bearer session token to `POST /api/chat`.
7. `handleClaude()` (server.ts:816) builds the system prompt via `buildSystemPrompt()` from `personality.ts`.
8. History is trimmed to ~90k tokens via `trimHistoryToFit()` (server.ts:91-125).
9. MCP tools, browser tools, and connector tools are gathered (server.ts:833-872).
10. `runClaudeToolLoop()` (server.ts:459) executes the request via the unified tool loop:
    - All providers route through `llmClient.complete()` — no direct Anthropic SDK instantiation.
    - Emits `ToolLoopEvents` (`tool:start`, `tool:result`, `tool:error`) for real-time UI streaming.
    - Tool-use loop iterates up to 25 times, executing tool calls via MCP/browser/connectors.
    - Tool results use the dual content/details pattern: truncated summary for LLM context, full output for UI display.
    - Per-turn token usage and estimated USD cost are tracked by `costTracker`.
    - cLaw safe mode strips side-effect tools when integrity is compromised.
11. Response is returned as `{ response, model, toolCalls, usage }`.

### 5. Response Rendering (Renderer)

12. `ChatHistory.tsx` receives the updated `messages[]` from Zustand.
13. Messages are rendered with `ReactMarkdown` + `remarkGfm` for Markdown (line 188-194).
14. Code blocks get syntax labels and copy buttons via `CodeBlock` component (line 17-41).
15. Pending messages (empty content + `pending: true`) show animated thinking dots (line 181-186).
16. Chat auto-scrolls to bottom on new messages (line 131-133).
17. Only the last 100 messages are rendered by default; a "Show earlier" button loads more (line 113-114, 150-157).

### 6. Persistence (Background)

18. Whenever `messages` changes, `App.tsx:932-940` saves to disk via `window.eve.chatHistory.save()`.
19. Pending placeholder messages are filtered out before saving (line 937).
20. `ChatHistoryStore` (chat-history.ts) debounces writes by 2 seconds to `{userData}/memory/chat-history.json`.
21. On next app launch, messages are restored via `window.eve.chatHistory.load()` at App.tsx:1029-1033.

### 7. Post-Response Hooks (Main Process, fire-and-forget)

22. After the Express `/api/chat` responds, three hooks run asynchronously (server.ts:233-271):
    - **Personality calibration**: `personalityCalibration.processUserMessage()` detects implicit communication signals.
    - **Memory extraction**: `memoryManager.extractMemories()` analyses the full conversation for new facts.
    - **Episode tracking**: Maintains an active text session; flushes to episodic memory after 5 minutes of silence.

## IPC Channels Used

| Channel | Direction | Purpose |
|---------|-----------|---------|
| `chat-history:load` | Renderer -> Main | Restore messages on startup |
| `chat-history:save` | Renderer -> Main | Persist messages to disk (debounced) |
| `chat-history:clear` | Renderer -> Main | Clear all saved messages |
| `local-conversation:send-text` | Renderer -> Main | Send typed text to local Ollama loop |
| `local-conversation:started` | Main -> Renderer | Local session is active |
| `local-conversation:transcript` | Main -> Renderer | User speech transcript (STT) |
| `local-conversation:response-chunk` | Main -> Renderer | Streaming LLM token |
| `local-conversation:response` | Main -> Renderer | Final complete response |
| `local-conversation:error` | Main -> Renderer | Error notification |
| `voice:play-chunk` | Main -> Renderer | TTS audio PCM for playback |

## State Changes

| State | Location | Trigger |
|-------|----------|---------|
| `messages[]` | Zustand `app-store.ts` | User send, assistant response chunks, final response |
| `status` | Zustand `app-store.ts` | Connection state changes |
| `activeActions[]` | Zustand `app-store.ts` | Tool start/end during local conversation |
| `localConversationActive` | Zustand + ref | Local conversation start/stop |
| `connectionError` | Zustand `app-store.ts` | Backend connection failures |
| `chat-history.json` | Disk | Debounced write on message changes |

## Error Scenarios

| Scenario | Handling | Location |
|----------|----------|----------|
| No backend available | Error message injected into chat | App.tsx:1207-1216 |
| Local conversation sendText fails | Error logged, no user-visible crash | App.tsx:867-869 |
| Express /api/chat 500 | Generic "Internal server error" returned (no PII leak) | server.ts:276 |
| All LLM providers fail | Error propagated from LLMClient with descriptive message | llm-client.ts:294 |
| Tool-use loop exceeds 25 iterations | Polite "hit my limit" message returned | server.ts:531-537 |
| Anthropic API key missing | "API key not configured" message returned | server.ts:497-501 |
| Connection timeout during handleTextSend | Waits up to 30s, then shows error in chat | App.tsx:1187-1195 |
| cLaw safe mode active | Side-effect tools stripped, read-only tools only | server.ts:36-39 |
| Privacy Shield enabled | PII scrubbed from cloud requests, rehydrated in responses | server.ts:508-516 |
