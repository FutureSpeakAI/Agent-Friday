/**
 * ollama-provider.ts — Native Ollama API backend for the LLM abstraction layer.
 *
 * Talks directly to Ollama's native API endpoints:
 *   - /api/chat   — Chat completions (with tool support)
 *   - /api/tags   — List available models
 *   - /api/embed  — Embeddings (future)
 *
 * Uses Ollama's native wire format (NOT the OpenAI-compat layer) to get
 * full access to Ollama-specific features like native tool calling.
 *
 * Ollama processes requests sequentially via its internal queue, so no
 * provider-side queuing is needed.
 *
 * Sprint 3 G.1: "The Native Tongue" — OllamaProvider
 */

import { randomUUID } from 'crypto';
import type {
  LLMProvider,
  LLMRequest,
  LLMResponse,
  LLMStreamChunk,
  ToolCall,
  ToolDefinition,
  ContentPart,
} from '../llm-client';
import type { ProviderName } from '../intelligence-router';

// ── Constants ─────────────────────────────────────────────────────────────

/** Default Ollama API base URL */
const DEFAULT_OLLAMA_ENDPOINT = 'http://localhost:11434';

/** Default model if none specified — Gemma 4 26B MoE is the sweet spot:
 *  25B total params, only 3.8B active per token, 256K context, native tool calling.
 *  Falls back gracefully if not pulled yet (Ollama returns 404 → retry with llama3.2). */
const DEFAULT_MODEL = 'gemma4:26b';

/** How long to cache health check results (ms) */
const HEALTH_CHECK_CACHE_MS = 60_000;

/** Timeout for health check requests (ms) */
const HEALTH_CHECK_TIMEOUT_MS = 5_000;

// ── Ollama native wire types ──────────────────────────────────────────────

/** Ollama message format */
interface OllamaMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls?: OllamaToolCall[];
}

/** Ollama tool call format */
interface OllamaToolCall {
  function: {
    name: string;
    arguments: Record<string, unknown>;
  };
}

/** Ollama tool definition format */
interface OllamaTool {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
}

/** Ollama /api/chat response (non-streaming) */
interface OllamaChatResponse {
  model: string;
  message: {
    role: 'assistant';
    content: string;
    tool_calls?: OllamaToolCall[];
  };
  done: boolean;
  total_duration?: number;
  prompt_eval_count?: number;
  eval_count?: number;
}

/** Ollama /api/chat streaming chunk */
interface OllamaStreamChunk {
  model: string;
  message: {
    role: 'assistant';
    content: string;
    tool_calls?: OllamaToolCall[];
  };
  done: boolean;
  total_duration?: number;
  prompt_eval_count?: number;
  eval_count?: number;
}

/** Ollama /api/tags response */
interface OllamaTagsResponse {
  models: Array<{
    name: string;
    model: string;
    size: number;
    details?: {
      parameter_size?: string;
      family?: string;
    };
  }>;
}

// ── Provider ──────────────────────────────────────────────────────────────

/**
 * Native Ollama provider for the LLM abstraction layer.
 *
 * Communicates with Ollama's native /api/* endpoints rather than the
 * OpenAI-compatible layer, enabling full access to Ollama-specific
 * features including native tool calling support.
 *
 * Registered with ProviderName 'ollama' in the intelligence router.
 */
export class OllamaProvider implements LLMProvider {
  readonly name: ProviderName = 'ollama';

  /** Cached health check result: { healthy, timestamp } */
  private healthCache: { healthy: boolean; timestamp: number } | null = null;

  // ── Configuration ─────────────────────────────────────────────────────

  /**
   * Resolve the Ollama API base URL.
   * Checks settings first, falls back to default.
   */
  private getEndpoint(): string {
    try {
      // Future: read from settingsManager if we add an ollamaEndpoint setting
      return DEFAULT_OLLAMA_ENDPOINT;
    } catch {
      return DEFAULT_OLLAMA_ENDPOINT;
    }
  }

  // ── LLMProvider Interface ─────────────────────────────────────────────

  /**
   * Returns true if Ollama is reachable (based on cached health check).
   * On first call before any health check, optimistically returns false
   * until checkHealth() has been called.
   */
  isAvailable(): boolean {
    if (this.healthCache && (Date.now() - this.healthCache.timestamp) < HEALTH_CHECK_CACHE_MS) {
      return this.healthCache.healthy;
    }
    // No cached result — conservatively return false.
    // checkHealth() should be called during initialization.
    return false;
  }

  /**
   * Send a non-streaming chat completion to /api/chat.
   * Normalizes the response to the unified LLMResponse format.
   */
  async complete(request: LLMRequest): Promise<LLMResponse> {
    return this.withRetry(async () => {
      const model = request.model || DEFAULT_MODEL;
      const startTime = Date.now();
      const endpoint = this.getEndpoint();
      const url = `${endpoint}/api/chat`;

      const body = this.buildRequestBody(request, model, false);

      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };

      const fetchOptions: RequestInit = {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      };
      // Use caller's signal if provided, otherwise apply a 60s timeout
      fetchOptions.signal = request.signal || AbortSignal.timeout(60_000);

      const res = await fetch(url, fetchOptions);

      if (!res.ok) {
        const errText = await res.text();
        const err = new Error(`[OllamaProvider] API error (${res.status}) from ${url}: ${errText}`);
        (err as any).status = res.status;
        throw err;
      }

      const response = await res.json() as OllamaChatResponse;
      const latencyMs = Date.now() - startTime;

      return this.parseResponse(response, model, latencyMs);
    }, 'complete');
  }

  /**
   * Stream a chat completion from /api/chat.
   * Ollama streams NDJSON (newline-delimited JSON), one object per line.
   * Yields LLMStreamChunk for each text delta, with the final chunk
   * containing done: true and the full assembled LLMResponse.
   */
  async *stream(request: LLMRequest): AsyncGenerator<LLMStreamChunk> {
    const model = request.model || DEFAULT_MODEL;
    const startTime = Date.now();
    const endpoint = this.getEndpoint();
    const url = `${endpoint}/api/chat`;

    const body = this.buildRequestBody(request, model, true);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    const fetchOptions: RequestInit = {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    };
    if (request.signal) {
      fetchOptions.signal = request.signal;
    }

    const res = await fetch(url, fetchOptions);

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`[OllamaProvider] API error (${res.status}) from ${url}: ${errText}`);
    }

    if (!res.body) {
      throw new Error('[OllamaProvider] No response body for streaming request');
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';
    let resolvedModel = model;
    let promptTokens = 0;
    let completionTokens = 0;
    const toolCalls: ToolCall[] = [];
    const yieldedToolCallIds = new Set<string>();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;

          let chunk: OllamaStreamChunk;
          try {
            chunk = JSON.parse(trimmed);
          } catch (parseErr) {
            console.warn(`[OllamaProvider] Malformed NDJSON line skipped: ${trimmed.slice(0, 200)}`, parseErr);
            continue;
          }

          if (chunk.model) resolvedModel = chunk.model;

          if (chunk.done) {
            // Final chunk with stats
            promptTokens = chunk.prompt_eval_count || 0;
            completionTokens = chunk.eval_count || 0;

            // Process any tool calls from the final chunk
            if (chunk.message?.tool_calls) {
              for (const tc of chunk.message.tool_calls) {
                const toolCall = this.convertToolCall(tc);
                if (!yieldedToolCallIds.has(toolCall.id)) {
                  yieldedToolCallIds.add(toolCall.id);
                  toolCalls.push(toolCall);
                  yield { toolCall, done: false };
                }
              }
            }
          } else if (chunk.message?.content) {
            fullText += chunk.message.content;
            yield { text: chunk.message.content, done: false };
          }

          // Non-final chunk with tool calls (some models send them incrementally)
          if (!chunk.done && chunk.message?.tool_calls) {
            for (const tc of chunk.message.tool_calls) {
              const toolCall = this.convertToolCall(tc);
              if (!yieldedToolCallIds.has(toolCall.id)) {
                yieldedToolCallIds.add(toolCall.id);
                toolCalls.push(toolCall);
                yield { toolCall, done: false };
              }
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // Audit Fix H3: Signal truncation — don't treat partial text as complete.
        // Yield what we have, but mark stopReason as 'interrupted' so callers
        // know the response was cut short.
        const latencyMs = Date.now() - startTime;
        yield {
          done: true,
          fullResponse: {
            content: fullText,
            toolCalls,
            usage: {
              inputTokens: promptTokens,
              outputTokens: completionTokens,
            },
            model: resolvedModel,
            provider: 'ollama',
            stopReason: 'interrupted',
            latencyMs,
          },
        };
        return;
      } else {
        throw err;
      }
    }

    // Final chunk with complete response
    const latencyMs = Date.now() - startTime;
    const stopReason = toolCalls.length > 0 ? 'tool_use' : 'end_turn';

    yield {
      done: true,
      fullResponse: {
        content: fullText,
        toolCalls,
        usage: {
          inputTokens: promptTokens,
          outputTokens: completionTokens,
        },
        model: resolvedModel,
        provider: 'ollama',
        stopReason,
        latencyMs,
      },
    };
  }

  /**
   * List available models by querying /api/tags.
   * Returns model IDs with parameter sizes from the details field.
   */
  async listModels(): Promise<Array<{ id: string; name: string }>> {
    const endpoint = this.getEndpoint();
    const url = `${endpoint}/api/tags`;

    try {
      const res = await fetch(url, {
        method: 'GET',
        signal: AbortSignal.timeout(HEALTH_CHECK_TIMEOUT_MS),
      });

      if (!res.ok) {
        console.warn(`[OllamaProvider] Failed to list models (${res.status})`);
        return [];
      }

      const data = await res.json() as OllamaTagsResponse;

      if (!Array.isArray(data.models)) {
        return [];
      }

      return data.models.map((m) => {
        const paramSize = m.details?.parameter_size ? ` (${m.details.parameter_size})` : '';
        return {
          id: m.name,
          name: `${m.name}${paramSize}`,
        };
      });
    } catch (err) {
      console.warn('[OllamaProvider] Error listing models:', (err as Error).message);
      return [];
    }
  }

  /**
   * Check Ollama health by hitting /api/tags.
   * Results are cached for 60 seconds.
   */
  async checkHealth(): Promise<boolean> {
    // Return cached result if still fresh
    if (this.healthCache && (Date.now() - this.healthCache.timestamp) < HEALTH_CHECK_CACHE_MS) {
      return this.healthCache.healthy;
    }

    const healthy = await this.performHealthCheck();
    this.healthCache = { healthy, timestamp: Date.now() };
    return healthy;
  }

  // ── Private: Retry Logic ──────────────────────────────────────────────

  private async withRetry<T>(fn: () => Promise<T>, label: string): Promise<T> {
    const MAX_RETRIES = 3;
    const BASE_DELAY_MS = 1000;

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        return await fn();
      } catch (err: unknown) {
        if (attempt === MAX_RETRIES || !this.isRetryable(err)) {
          throw err;
        }
        const delay = BASE_DELAY_MS * Math.pow(2, attempt) + Math.random() * 500;
        console.warn(
          `[OllamaProvider] ${label} attempt ${attempt + 1} failed, retrying in ${Math.round(delay)}ms...`
        );
        await new Promise((r) => setTimeout(r, delay));
      }
    }
    throw new Error('Unreachable');
  }

  private isRetryable(err: unknown): boolean {
    if (err instanceof Error) {
      if (
        err.message.includes('ECONNREFUSED') ||
        err.message.includes('ETIMEDOUT') ||
        err.message.includes('ENOTFOUND') ||
        err.message.includes('fetch failed')
      ) {
        return true;
      }
    }
    // Check for HTTP status codes attached to the error
    if (err && typeof err === 'object' && 'status' in err) {
      const status = (err as any).status;
      return status === 500 || status === 502 || status === 503;
    }
    return false;
  }

  // ── Private: Request Building ─────────────────────────────────────────

  /**
   * Build the Ollama /api/chat request body.
   */
  private buildRequestBody(
    request: LLMRequest,
    model: string,
    stream: boolean
  ): Record<string, unknown> {
    const messages = this.formatMessages(request);
    const body: Record<string, unknown> = {
      model,
      messages,
      stream,
    };

    // Options object for Ollama-specific parameters
    const options: Record<string, unknown> = {};
    if (request.temperature !== undefined) {
      options.temperature = request.temperature;
    }
    if (request.maxTokens) {
      options.num_predict = request.maxTokens;
    }
    if (Object.keys(options).length > 0) {
      body.options = options;
    }

    // Tools
    if (request.tools && request.tools.length > 0) {
      body.tools = this.formatTools(request.tools);
    }

    return body;
  }

  // ── Private: Message Format Conversion ────────────────────────────────

  /**
   * Convert unified ChatMessage[] to Ollama's message format.
   * Ollama uses the same role names (system, user, assistant, tool)
   * but content is always a string.
   */
  private formatMessages(request: LLMRequest): OllamaMessage[] {
    const messages: OllamaMessage[] = [];

    // Add system prompt if present
    if (request.systemPrompt) {
      messages.push({ role: 'system', content: request.systemPrompt });
    }

    for (const msg of request.messages) {
      if (msg.role === 'system') {
        messages.push({
          role: 'system',
          content: typeof msg.content === 'string' ? msg.content : '',
        });
        continue;
      }

      if (msg.role === 'tool') {
        messages.push({
          role: 'tool',
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
        });
        continue;
      }

      if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        const ollamaToolCalls: OllamaToolCall[] = msg.tool_calls.map((tc) => ({
          function: {
            name: tc.name,
            arguments: typeof tc.input === 'object' && tc.input !== null
              ? tc.input as Record<string, unknown>
              : {},
          },
        }));
        messages.push({
          role: 'assistant',
          content: typeof msg.content === 'string' ? msg.content : '',
          tool_calls: ollamaToolCalls,
        });
        continue;
      }

      // Regular user/assistant message
      let content: string;
      if (typeof msg.content === 'string') {
        content = msg.content;
      } else if (Array.isArray(msg.content)) {
        // Flatten content parts to text (Ollama doesn't support multipart content natively)
        content = (msg.content as ContentPart[])
          .filter((p) => p.type === 'text')
          .map((p) => p.text || '')
          .join('');
      } else {
        content = msg.content ? String(msg.content) : '';
      }

      messages.push({
        role: msg.role as 'user' | 'assistant',
        content,
      });
    }

    return messages;
  }

  // ── Private: Tool Format Conversion ───────────────────────────────────

  /**
   * Convert unified ToolDefinition[] to Ollama's native tool format.
   *
   * Ollama uses the same structure as OpenAI:
   *   { type: 'function', function: { name, description, parameters } }
   */
  private formatTools(tools: ToolDefinition[]): OllamaTool[] {
    return tools.map((t) => {
      if (t.function) {
        return {
          type: 'function' as const,
          function: {
            name: t.function.name,
            description: t.function.description || '',
            parameters: t.function.parameters || {},
          },
        };
      }
      return {
        type: 'function' as const,
        function: {
          name: t.name,
          description: t.description || '',
          parameters: t.input_schema || {},
        },
      };
    });
  }

  // ── Private: Response Parsing ─────────────────────────────────────────

  /**
   * Parse Ollama's /api/chat response into unified LLMResponse format.
   */
  private parseResponse(
    response: OllamaChatResponse,
    requestedModel: string,
    latencyMs: number
  ): LLMResponse {
    const content = response.message?.content || '';

    // Parse tool calls
    const toolCalls: ToolCall[] = (response.message?.tool_calls || []).map(
      (tc) => this.convertToolCall(tc)
    );

    const stopReason = toolCalls.length > 0 ? 'tool_use' : 'end_turn';

    return {
      content,
      toolCalls,
      usage: {
        inputTokens: response.prompt_eval_count || 0,
        outputTokens: response.eval_count || 0,
      },
      model: response.model || requestedModel,
      provider: 'ollama',
      stopReason,
      latencyMs,
    };
  }

  /**
   * Convert an Ollama tool call to the unified ToolCall format.
   */
  private convertToolCall(tc: OllamaToolCall): ToolCall {
    return {
      id: `call_${randomUUID()}`,
      type: 'tool_use',
      name: tc.function.name,
      input: tc.function.arguments,
    };
  }

  // ── Private: Health Check ─────────────────────────────────────────────

  /**
   * Perform a health check by hitting /api/tags.
   * If Ollama is running, this endpoint will respond with the list of models.
   */
  private async performHealthCheck(): Promise<boolean> {
    const endpoint = this.getEndpoint();
    const url = `${endpoint}/api/tags`;

    try {
      const res = await fetch(url, {
        method: 'GET',
        signal: AbortSignal.timeout(HEALTH_CHECK_TIMEOUT_MS),
      });
      return res.ok;
    } catch {
      return false;
    }
  }
}
