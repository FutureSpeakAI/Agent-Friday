/**
 * hf-provider.ts — HuggingFace / Local Inference backend for the LLM abstraction layer.
 *
 * Supports TWO modes through the same OpenAI-compatible chat completions API:
 *
 * 1. **HuggingFace Inference API (Cloud)**
 *    Endpoint: https://api-inference.huggingface.co/v1/chat/completions
 *    Requires an HF API token for authentication.
 *
 * 2. **Local TGI / vLLM / Ollama**
 *    Endpoint: http://localhost:8080/v1/chat/completions (or user-configured)
 *    No authentication required for local inference.
 *
 * Both share the same OpenAI-compatible wire format, so a single implementation
 * handles request formatting, SSE streaming, and response parsing for both modes.
 *
 * Uses native fetch() — no external HTTP dependencies.
 */

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
import { settingsManager } from '../settings';

// ── Constants ─────────────────────────────────────────────────────────────

/** Default model for HuggingFace cloud inference */
const DEFAULT_CLOUD_MODEL = 'meta-llama/Llama-3.3-70B-Instruct';

/** Default model for local inference (lets the server pick its loaded model) */
const DEFAULT_LOCAL_MODEL = 'default';

/** Default HuggingFace Inference API base URL */
const DEFAULT_HF_ENDPOINT = 'https://api-inference.huggingface.co/v1';

/** Default local inference server base URL (Ollama default port) */
const DEFAULT_LOCAL_ENDPOINT = 'http://localhost:11434/v1';

/** How long to cache health check results (ms) */
const HEALTH_CHECK_CACHE_MS = 60_000;

/** Maximum number of retries on 429 (rate limit) responses */
const MAX_RATE_LIMIT_RETRIES = 3;

/** Base delay for rate-limit exponential backoff (ms) */
const RATE_LIMIT_BASE_DELAY_MS = 1000;

// ── OpenAI-compatible wire types ──────────────────────────────────────────

/** Message format for the OpenAI-compatible chat completions API */
interface OAIMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | OAIContentPart[] | null;
  tool_calls?: OAIToolCall[];
  tool_call_id?: string;
  name?: string;
}

interface OAIContentPart {
  type: 'text' | 'image_url';
  text?: string;
  image_url?: { url: string; detail?: 'auto' | 'low' | 'high' };
}

interface OAIToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

interface OAITool {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
}

/** Non-streaming response from the chat completions API */
interface OAIChatResponse {
  id: string;
  model: string;
  choices: Array<{
    index: number;
    message: {
      role: 'assistant';
      content: string | null;
      tool_calls?: OAIToolCall[];
    };
    finish_reason: 'stop' | 'tool_calls' | 'length' | 'content_filter' | null;
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

/** Streaming chunk from SSE */
interface OAIStreamChunk {
  id: string;
  model?: string;
  choices: Array<{
    index: number;
    delta: {
      role?: 'assistant';
      content?: string | null;
      tool_calls?: Array<{
        index: number;
        id?: string;
        type?: 'function';
        function?: {
          name?: string;
          arguments?: string;
        };
      }>;
    };
    finish_reason: 'stop' | 'tool_calls' | 'length' | 'content_filter' | null;
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

// ── Programmatic config (for use before settings are wired up) ────────────

interface HFProviderConfig {
  /** HuggingFace API token (overrides settings / env) */
  apiKey?: string;
  /** HuggingFace Inference API base URL */
  huggingfaceEndpoint?: string;
  /** Local inference server base URL */
  localEndpoint?: string;
  /** Whether local model mode is enabled */
  localEnabled?: boolean;
  /** Default model ID override */
  defaultModel?: string;
}

// ── Provider ──────────────────────────────────────────────────────────────

/**
 * HuggingFace / Local Inference provider for the LLM abstraction layer.
 *
 * Handles both cloud (HF Inference API) and local (TGI, vLLM, Ollama) inference
 * through the shared OpenAI-compatible chat completions endpoint.
 *
 * Registered with ProviderName 'local' in the intelligence router.
 */
export class HuggingFaceProvider implements LLMProvider {
  readonly name: ProviderName = 'local';

  /** Programmatic config that overrides settings (useful before settings are wired) */
  private config: HFProviderConfig = {};

  /** Cached health check result: { healthy, timestamp } */
  private healthCache: { healthy: boolean; timestamp: number } | null = null;

  /**
   * Number of requests currently in-flight to the local endpoint.
   * Ollama typically processes one request at a time (sequential inference).
   * The router uses this to degrade speed scoring when the local server is busy,
   * preventing request pile-up that would cause latency spikes.
   */
  private inflightRequests = 0;

  /** Get current number of in-flight requests to the local endpoint */
  getInflightCount(): number {
    return this.inflightRequests;
  }

  // ── Configuration ─────────────────────────────────────────────────────

  /**
   * Get the current programmatic configuration.
   * Settings from settingsManager take precedence where available,
   * falling back to programmatic config, then env vars, then defaults.
   */
  getConfig(): HFProviderConfig {
    return { ...this.config };
  }

  /**
   * Set programmatic configuration that takes effect immediately.
   * Useful for testing or configuring before settingsManager is wired up.
   */
  setConfig(partial: Partial<HFProviderConfig>): void {
    this.config = { ...this.config, ...partial };
    // Invalidate health cache when config changes
    this.healthCache = null;
  }

  // ── Resolved Getters (merge settings → config → env → defaults) ───────

  /**
   * Resolve the HuggingFace API key from all sources.
   * Priority: programmatic config > settingsManager > HF_TOKEN env var
   */
  private getApiKey(): string {
    if (this.config.apiKey) return this.config.apiKey;
    try {
      const settings = settingsManager.get();
      if (settings.huggingfaceApiKey) return settings.huggingfaceApiKey;
    } catch {
      // settingsManager may not be initialized yet
    }
    return settingsManager.getHuggingfaceApiKey() || '';
  }

  /**
   * Resolve the HuggingFace cloud endpoint URL.
   * Priority: programmatic config > settingsManager > default
   */
  private getHFEndpoint(): string {
    if (this.config.huggingfaceEndpoint) return this.config.huggingfaceEndpoint;
    try {
      const settings = settingsManager.get();
      if (settings.huggingfaceEndpoint) return settings.huggingfaceEndpoint;
    } catch {
      // settingsManager may not be initialized yet
    }
    return DEFAULT_HF_ENDPOINT;
  }

  /**
   * Resolve the local inference endpoint URL.
   * Priority: programmatic config > settingsManager > default
   */
  private getLocalEndpoint(): string {
    if (this.config.localEndpoint) return this.config.localEndpoint;
    try {
      const settings = settingsManager.get();
      if (settings.localInferenceEndpoint) return settings.localInferenceEndpoint;
    } catch {
      // settingsManager may not be initialized yet
    }
    return DEFAULT_LOCAL_ENDPOINT;
  }

  /**
   * Check whether local model mode is enabled.
   * Priority: programmatic config > settingsManager > false
   */
  private isLocalEnabled(): boolean {
    if (this.config.localEnabled !== undefined) return this.config.localEnabled;
    try {
      const settings = settingsManager.get();
      return settings.localModelEnabled ?? false;
    } catch {
      return false;
    }
  }

  /**
   * Resolve the default model ID.
   * For local mode, uses the configured localModelId or 'default'.
   * For cloud mode, uses the configured default or the Llama 3.3 70B model.
   */
  private getDefaultModel(): string {
    if (this.config.defaultModel) return this.config.defaultModel;
    if (this.isLocalEnabled()) {
      try {
        const settings = settingsManager.get();
        if (settings.localModelId) return settings.localModelId;
      } catch {
        // settingsManager may not be initialized yet
      }
      return DEFAULT_LOCAL_MODEL;
    }
    return DEFAULT_CLOUD_MODEL;
  }

  /**
   * Determine the base URL and auth configuration for the current mode.
   * Returns the endpoint and optional bearer token.
   */
  private resolveEndpoint(): { baseUrl: string; authHeader: string | null } {
    if (this.isLocalEnabled()) {
      const baseUrl = this.getLocalEndpoint();
      // Local servers generally don't need auth, but pass it if available
      const apiKey = this.getApiKey();
      return {
        baseUrl,
        authHeader: apiKey ? `Bearer ${apiKey}` : null,
      };
    }
    // Cloud mode — always requires API key
    return {
      baseUrl: this.getHFEndpoint(),
      authHeader: `Bearer ${this.getApiKey()}`,
    };
  }

  // ── LLMProvider Interface ─────────────────────────────────────────────

  /**
   * Returns true if EITHER:
   * - HF API key is set (cloud mode available), OR
   * - Local model mode is enabled (health checked and cached for 60s)
   */
  isAvailable(): boolean {
    // Cloud mode: API key present
    if (this.getApiKey()) return true;

    // Local mode: must be enabled and recent health check must have passed
    if (this.isLocalEnabled()) {
      if (this.healthCache && (Date.now() - this.healthCache.timestamp) < HEALTH_CHECK_CACHE_MS) {
        return this.healthCache.healthy;
      }
      // No cached result — optimistically return true if local is enabled.
      // checkHealth() will be called separately for definitive status.
      return true;
    }

    return false;
  }

  /**
   * Send a non-streaming chat completion request.
   * Handles request formatting, retry on 429, and response parsing.
   */
  async complete(request: LLMRequest): Promise<LLMResponse> {
    const model = request.model || this.getDefaultModel();
    const startTime = Date.now();

    const { baseUrl, authHeader } = this.resolveEndpoint();
    const url = `${baseUrl}/chat/completions`;

    // Build OpenAI-compatible request body
    const body = this.buildRequestBody(request, model, false);

    // Track in-flight requests so the router can degrade scoring when busy
    this.inflightRequests++;
    try {
      // Execute with retry on rate-limit
      const responseData = await this.fetchWithRetry<OAIChatResponse>(
        url, body, authHeader, request.signal
      );

      const latencyMs = Date.now() - startTime;
      return this.parseCompletionResponse(responseData, model, latencyMs);
    } finally {
      this.inflightRequests--;
    }
  }

  /**
   * Stream a chat completion request via SSE.
   * Yields LLMStreamChunk for each text delta and tool call,
   * with the final chunk containing done: true and the full LLMResponse.
   */
  async *stream(request: LLMRequest): AsyncGenerator<LLMStreamChunk> {
    const model = request.model || this.getDefaultModel();
    const startTime = Date.now();

    const { baseUrl, authHeader } = this.resolveEndpoint();
    const url = `${baseUrl}/chat/completions`;

    // Build request body with streaming enabled
    const body = this.buildRequestBody(request, model, true);

    // Build headers
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    };
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    const fetchOptions: RequestInit = {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    };
    if (request.signal) {
      fetchOptions.signal = request.signal;
    }

    // Track in-flight requests for concurrency awareness
    this.inflightRequests++;

    const res = await fetch(url, fetchOptions);

    if (!res.ok) {
      this.inflightRequests--;
      const errText = await res.text();
      throw this.buildApiError(res.status, errText, url);
    }

    if (!res.body) {
      this.inflightRequests--;
      throw new Error('[HFProvider] No response body for streaming request');
    }

    // Parse SSE stream
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    let fullText = '';
    let resolvedModel = model;
    let finishReason: string | null = null;
    let usageData: { prompt_tokens: number; completion_tokens: number } | null = null;
    const toolCallAccumulator = new Map<number, { id: string; name: string; args: string }>();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        // Keep the last potentially incomplete line in the buffer
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();

          // Skip empty lines and SSE comments (keepalive)
          if (!trimmed || trimmed.startsWith(':')) continue;

          if (!trimmed.startsWith('data: ')) continue;

          const data = trimmed.slice(6);
          if (data === '[DONE]') continue;

          let chunk: OAIStreamChunk;
          try {
            chunk = JSON.parse(data);
          } catch {
            // Skip malformed JSON chunks
            continue;
          }

          if (chunk.model) resolvedModel = chunk.model;

          // Capture usage if reported in stream
          if (chunk.usage) {
            usageData = {
              prompt_tokens: chunk.usage.prompt_tokens,
              completion_tokens: chunk.usage.completion_tokens,
            };
          }

          for (const choice of chunk.choices) {
            // Capture finish reason
            if (choice.finish_reason) {
              finishReason = choice.finish_reason;
            }

            // Text content delta
            if (choice.delta.content) {
              fullText += choice.delta.content;
              yield { text: choice.delta.content, done: false };
            }

            // Tool calls (accumulated across chunks)
            if (choice.delta.tool_calls) {
              for (const tc of choice.delta.tool_calls) {
                if (!toolCallAccumulator.has(tc.index)) {
                  toolCallAccumulator.set(tc.index, {
                    id: tc.id || '',
                    name: tc.function?.name || '',
                    args: '',
                  });
                }
                const acc = toolCallAccumulator.get(tc.index)!;
                if (tc.id) acc.id = tc.id;
                if (tc.function?.name) acc.name = tc.function.name;
                if (tc.function?.arguments) acc.args += tc.function.arguments;
              }
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // Stream was cancelled — yield what we have so far as the final chunk
      } else {
        throw err;
      }
    }

    // Convert accumulated tool calls to unified format
    const toolCalls: ToolCall[] = [];
    for (const [, acc] of toolCallAccumulator) {
      let parsedArgs: unknown;
      try {
        parsedArgs = JSON.parse(acc.args);
      } catch {
        parsedArgs = acc.args;
      }
      const tc: ToolCall = {
        id: acc.id,
        type: 'tool_use',
        name: acc.name,
        input: parsedArgs,
      };
      toolCalls.push(tc);
      yield { toolCall: tc, done: false };
    }

    // Map finish reason to unified stopReason
    const stopReason = this.mapFinishReason(finishReason, toolCalls.length > 0);

    // Release in-flight slot before final yield
    this.inflightRequests--;

    // Final chunk with full assembled response
    const latencyMs = Date.now() - startTime;
    yield {
      done: true,
      fullResponse: {
        content: fullText,
        toolCalls,
        usage: {
          inputTokens: usageData?.prompt_tokens || 0,
          outputTokens: usageData?.completion_tokens || 0,
        },
        model: resolvedModel,
        provider: 'local',
        stopReason,
        latencyMs,
      },
    };
  }

  /**
   * List available models from the connected endpoint.
   * Calls GET {baseUrl}/models and parses the response.
   */
  async listModels(): Promise<Array<{ id: string; name: string }>> {
    const { baseUrl, authHeader } = this.resolveEndpoint();
    const url = `${baseUrl}/models`;

    const headers: Record<string, string> = {};
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    try {
      const res = await fetch(url, {
        method: 'GET',
        headers,
        signal: AbortSignal.timeout(10_000),
      });

      if (!res.ok) {
        console.warn(`[HFProvider] Failed to list models (${res.status})`);
        return [];
      }

      const data = await res.json() as any;

      // OpenAI-compatible format: { data: [{ id, ... }] }
      if (Array.isArray(data.data)) {
        return data.data.map((m: any) => ({
          id: m.id || m.model_id || '',
          name: m.id || m.model_id || 'Unknown',
        }));
      }

      // Some servers return a flat array
      if (Array.isArray(data)) {
        return data.map((m: any) => ({
          id: typeof m === 'string' ? m : (m.id || m.model_id || ''),
          name: typeof m === 'string' ? m : (m.id || m.model_id || 'Unknown'),
        }));
      }

      return [];
    } catch (err) {
      console.warn('[HFProvider] Error listing models:', (err as Error).message);
      return [];
    }
  }

  /**
   * Check the health of the inference endpoint.
   *
   * For local endpoints: tries GET {endpoint}/health, then falls back
   * to GET {endpoint}/models as a connectivity check.
   *
   * For cloud endpoints: tries GET {endpoint}/models with auth.
   *
   * Results are cached for 60 seconds to avoid excessive polling.
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

  // ── Private: Request Building ─────────────────────────────────────────

  /**
   * Build the OpenAI-compatible request body for chat completions.
   */
  private buildRequestBody(
    request: LLMRequest,
    model: string,
    stream: boolean
  ): Record<string, unknown> {
    const body: Record<string, unknown> = {
      model,
      messages: this.formatMessages(request),
      stream,
    };

    if (request.maxTokens) body.max_tokens = request.maxTokens;
    if (request.temperature !== undefined) body.temperature = request.temperature;
    if (request.responseFormat) body.response_format = request.responseFormat;

    // Tools
    if (request.tools && request.tools.length > 0) {
      body.tools = this.formatTools(request.tools);
    }

    // Tool choice
    if (request.toolChoice) {
      if (request.toolChoice === 'auto' || request.toolChoice === 'none') {
        body.tool_choice = request.toolChoice;
      } else if (typeof request.toolChoice === 'object' && 'name' in request.toolChoice) {
        body.tool_choice = {
          type: 'function',
          function: { name: request.toolChoice.name },
        };
      }
    }

    // Request usage reporting in streaming mode (some servers support this)
    if (stream) {
      body.stream_options = { include_usage: true };
    }

    return body;
  }

  // ── Private: Message Format Conversion ────────────────────────────────

  /**
   * Convert unified ChatMessage[] to OpenAI-compatible message format.
   *
   * - System prompts go as { role: 'system', content: '...' }
   * - Tool results use { role: 'tool', tool_call_id: '...', content: '...' }
   * - Assistant tool_calls use { role: 'assistant', tool_calls: [{ id, type: 'function', function: { name, arguments } }] }
   * - User/assistant messages pass through with content parts converted
   */
  private formatMessages(request: LLMRequest): OAIMessage[] {
    const messages: OAIMessage[] = [];

    // Add system prompt first if present
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
          tool_call_id: msg.tool_call_id,
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
        });
        continue;
      }

      if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        const oaiToolCalls: OAIToolCall[] = msg.tool_calls.map((tc) => ({
          id: tc.id,
          type: 'function' as const,
          function: {
            name: tc.name,
            arguments: typeof tc.input === 'string' ? tc.input : JSON.stringify(tc.input),
          },
        }));
        messages.push({
          role: 'assistant',
          content: typeof msg.content === 'string' ? msg.content : null,
          tool_calls: oaiToolCalls,
        });
        continue;
      }

      // Regular user/assistant message
      if (typeof msg.content === 'string') {
        messages.push({
          role: msg.role as 'user' | 'assistant',
          content: msg.content,
        });
      } else if (Array.isArray(msg.content)) {
        // Convert content parts to OpenAI-compatible format
        const parts: OAIContentPart[] = (msg.content as ContentPart[]).map((p) => {
          if (p.type === 'text') {
            return { type: 'text' as const, text: p.text };
          }
          if (p.type === 'image' && p.source) {
            // Convert Anthropic-style base64 image to OpenAI-style data URI
            return {
              type: 'image_url' as const,
              image_url: {
                url: `data:${p.source.media_type};base64,${p.source.data}`,
              },
            };
          }
          if (p.image_url) {
            return { type: 'image_url' as const, image_url: p.image_url };
          }
          // Fallback: serialize unknown parts as text
          return { type: 'text' as const, text: JSON.stringify(p) };
        });
        messages.push({
          role: msg.role as 'user' | 'assistant',
          content: parts,
        });
      } else {
        messages.push({
          role: msg.role as 'user' | 'assistant',
          content: msg.content as string | null,
        });
      }
    }

    return messages;
  }

  // ── Private: Tool Format Conversion ───────────────────────────────────

  /**
   * Convert unified ToolDefinition[] to OpenAI function-calling format.
   *
   * Input can be either:
   * - Anthropic format: { name, description, input_schema }
   * - OpenAI format: { function: { name, description, parameters } }
   *
   * Output is always: { type: 'function', function: { name, description, parameters } }
   */
  private formatTools(tools: ToolDefinition[]): OAITool[] {
    return tools.map((t) => {
      // Already in OpenAI nested format
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
      // Anthropic format or bare minimum — convert to OpenAI format
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
   * Parse a non-streaming chat completion response into unified LLMResponse.
   */
  private parseCompletionResponse(
    response: OAIChatResponse,
    requestedModel: string,
    latencyMs: number
  ): LLMResponse {
    const choice = response.choices?.[0];
    if (!choice) {
      return {
        content: '',
        toolCalls: [],
        usage: { inputTokens: 0, outputTokens: 0 },
        model: response.model || requestedModel,
        provider: 'local',
        stopReason: 'end_turn',
        latencyMs,
      };
    }

    const content = choice.message?.content || '';

    // Parse tool calls
    const toolCalls: ToolCall[] = (choice.message?.tool_calls || []).map((tc: OAIToolCall) => {
      let parsedArgs: unknown;
      try {
        parsedArgs = JSON.parse(tc.function.arguments);
      } catch {
        parsedArgs = tc.function.arguments;
      }
      return {
        id: tc.id,
        type: 'tool_use' as const,
        name: tc.function.name,
        input: parsedArgs,
      };
    });

    const stopReason = this.mapFinishReason(choice.finish_reason, toolCalls.length > 0);

    return {
      content,
      toolCalls,
      usage: {
        inputTokens: response.usage?.prompt_tokens || 0,
        outputTokens: response.usage?.completion_tokens || 0,
      },
      model: response.model || requestedModel,
      provider: 'local',
      stopReason,
      latencyMs,
    };
  }

  /**
   * Map OpenAI-compatible finish_reason to unified stop reason.
   */
  private mapFinishReason(finishReason: string | null, hasToolCalls: boolean): string {
    if (!finishReason) return hasToolCalls ? 'tool_use' : 'end_turn';
    switch (finishReason) {
      case 'stop': return 'end_turn';
      case 'tool_calls': return 'tool_use';
      case 'length': return 'max_tokens';
      case 'content_filter': return 'content_filter';
      default: return finishReason;
    }
  }

  // ── Private: HTTP ─────────────────────────────────────────────────────

  /**
   * Execute a POST request with automatic retry on 429 (rate limit).
   * Uses exponential backoff with jitter.
   */
  private async fetchWithRetry<T>(
    url: string,
    body: Record<string, unknown>,
    authHeader: string | null,
    signal?: AbortSignal
  ): Promise<T> {
    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= MAX_RATE_LIMIT_RETRIES; attempt++) {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (authHeader) {
        headers['Authorization'] = authHeader;
      }

      const fetchOptions: RequestInit = {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      };
      if (signal) {
        fetchOptions.signal = signal;
      }

      const res = await fetch(url, fetchOptions);

      if (res.ok) {
        return res.json() as Promise<T>;
      }

      const errText = await res.text();

      // Retry on rate limit (429)
      if (res.status === 429 && attempt < MAX_RATE_LIMIT_RETRIES) {
        const retryAfter = this.parseRetryAfter(res, attempt);
        console.warn(
          `[HFProvider] Rate limited (429), retrying in ${retryAfter}ms ` +
          `(attempt ${attempt + 1}/${MAX_RATE_LIMIT_RETRIES})`
        );
        await this.sleep(retryAfter);
        continue;
      }

      lastError = this.buildApiError(res.status, errText, url);
      break;
    }

    throw lastError || new Error('[HFProvider] Request failed after retries');
  }

  /**
   * Parse the Retry-After header or compute exponential backoff delay.
   */
  private parseRetryAfter(res: Response, attempt: number): number {
    const retryAfterHeader = res.headers.get('Retry-After');
    if (retryAfterHeader) {
      const seconds = parseInt(retryAfterHeader, 10);
      if (!isNaN(seconds)) return seconds * 1000;
    }
    // Exponential backoff with jitter
    const baseDelay = RATE_LIMIT_BASE_DELAY_MS * Math.pow(2, attempt);
    const jitter = Math.random() * baseDelay * 0.5;
    return baseDelay + jitter;
  }

  /**
   * Build a descriptive API error from a failed response.
   */
  private buildApiError(status: number, responseText: string, url: string): Error {
    let message: string;
    try {
      const parsed = JSON.parse(responseText);
      message = parsed.error?.message || parsed.error || parsed.message || responseText;
    } catch {
      message = responseText;
    }
    return new Error(`[HFProvider] API error (${status}) from ${url}: ${message}`);
  }

  /**
   * Sleep for a given number of milliseconds.
   */
  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ── Private: Health Check ─────────────────────────────────────────────

  /**
   * Perform a health check against the configured endpoint.
   * Tries the /health endpoint first (TGI), then /models as a fallback.
   */
  private async performHealthCheck(): Promise<boolean> {
    const { baseUrl, authHeader } = this.resolveEndpoint();
    const headers: Record<string, string> = {};
    if (authHeader) {
      headers['Authorization'] = authHeader;
    }

    // For local endpoints, try multiple health check strategies
    if (this.isLocalEnabled()) {
      const baseWithoutV1 = this.getLocalEndpoint().replace(/\/v1\/?$/, '');

      // Strategy 1: TGI /health endpoint
      try {
        const healthUrl = baseWithoutV1 + '/health';
        const res = await fetch(healthUrl, {
          method: 'GET',
          headers,
          signal: AbortSignal.timeout(5_000),
        });
        if (res.ok) {
          return true;
        }
      } catch {
        // /health not available, try Ollama root
      }

      // Strategy 2: Ollama root endpoint (returns "Ollama is running")
      try {
        const res = await fetch(baseWithoutV1, {
          method: 'GET',
          headers,
          signal: AbortSignal.timeout(5_000),
        });
        if (res.ok) {
          return true;
        }
      } catch {
        // Root not available, fall through to /models
      }
    }

    // Fallback: try GET /models as a generic connectivity test
    try {
      const modelsUrl = `${baseUrl}/models`;
      const res = await fetch(modelsUrl, {
        method: 'GET',
        headers,
        signal: AbortSignal.timeout(5_000),
      });
      return res.ok;
    } catch {
      return false;
    }
  }
}
