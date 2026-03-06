## Interface Contract: LLM Client
**Generated:** 2026-03-06
**Source:** src/main/llm-client.ts (327 lines)

### Exports
- `llmClient` — singleton instance of `LLMClient`
- `ChatMessage` — interface: { role: 'user'|'assistant'|'system'|'tool', content: string|ContentPart[], name?, tool_call_id? }
- `ToolDefinition` — interface: { type?, name, description?, input_schema?, function? }
- `ToolCall` — interface: { id, type: 'function'|'tool_use', name, input }
- `ToolResult` — interface: { tool_use_id, content: string|ContentPart[], is_error? }
- `LLMRequest` — interface: { messages, systemPrompt?, model?, maxTokens?, temperature?, tools?, toolChoice?, stream?, taskHint? }
- `LLMResponse` — interface: { content, toolCalls, usage: {inputTokens, outputTokens}, model, provider, stopReason, latencyMs }
- `LLMProvider` — interface: { name, chat(), text(), isAvailable(), getModels() }

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| registerProvider(provider) | `(provider: LLMProvider): void` | Add a provider to the routing table |
| chat(request) | `(request: LLMRequest): Promise<LLMResponse>` | Full chat with tool support |
| text(prompt, opts?) | `(prompt: string, opts?): Promise<string>` | Simple text completion |
| getAvailableProviders() | `(): ProviderName[]` | List registered providers |

### Tool Call Flow (relevant to Track B)
1. Send `LLMRequest` with `tools: ToolDefinition[]`
2. Receive `LLMResponse` with `toolCalls: ToolCall[]`
3. Execute tool calls, produce `ToolResult[]`
4. Send follow-up request with tool results as messages

### Dependencies
- Requires: provider implementations (anthropic, openrouter, huggingface)
- Required by: intelligence-engine, tool-registry (Track B), execution-delegate (Track B)
