/**
 * llm-client.ts — Unified LLM abstraction layer for Agent Friday.
 *
 * Provides a single interface that ALL modules call instead of directly
 * instantiating Anthropic, OpenRouter, or Google SDKs.
 *
 * Architecture:
 *   LLMClient → selects provider → LLMProvider.complete() / .stream()
 *
 * Providers:
 *   - AnthropicProvider   (direct Anthropic SDK)
 *   - OpenRouterProvider  (wraps existing openrouter.ts)
 *   - HuggingFaceProvider (local TGI or HF Inference API — Phase 2)
 *
 * The Intelligence Router (intelligence-router.ts) can optionally select
 * the model/provider, or callers can specify directly.
 */

import { ProviderName, TaskCategory, type TaskComplexity } from './intelligence-router';
import { assessConfidence } from './confidence-assessor';
import { CloudGate } from './cloud-gate';

// ── Core Types ─────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | ContentPart[] | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ContentPart {
  type: 'text' | 'image';
  text?: string;
  /** For Anthropic-style base64 images */
  source?: {
    type: 'base64';
    media_type: string;
    data: string;
  };
  /** For OpenAI-compatible image URLs */
  image_url?: {
    url: string;
    detail?: 'auto' | 'low' | 'high';
  };
}

export interface ToolDefinition {
  type?: 'function';
  name: string;
  description?: string;
  input_schema?: Record<string, unknown>;
  /** OpenAI-compatible nested format */
  function?: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
}

export interface ToolCall {
  id: string;
  type: 'function' | 'tool_use';
  name: string;
  input: unknown;
}

export interface ToolResult {
  tool_use_id: string;
  content: string | ContentPart[];
  is_error?: boolean;
}

// ── Request / Response ─────────────────────────────────────────────────

export interface LLMRequest {
  /** Chat messages (system prompt can be first message or separate field) */
  messages: ChatMessage[];
  /** System prompt — kept separate for providers that handle it differently */
  systemPrompt?: string;
  /** Specific model to use (e.g. 'claude-sonnet-4-20250514'). If omitted, provider picks default */
  model?: string;
  /** Max output tokens */
  maxTokens?: number;
  /** Sampling temperature (0-2) */
  temperature?: number;
  /** Tool definitions for function calling */
  tools?: ToolDefinition[];
  /** Tool choice strategy */
  toolChoice?: 'auto' | 'none' | { name: string };
  /** AbortController signal for cancellation */
  signal?: AbortSignal;
  /** Whether to stream the response */
  stream?: boolean;
  /** Hint for the Intelligence Router to pick optimal model */
  taskHint?: TaskCategory;
  /** Response format */
  responseFormat?: { type: 'json_object' | 'text' };
}

export interface LLMResponse {
  /** Text content of the response */
  content: string;
  /** Tool calls requested by the model */
  toolCalls: ToolCall[];
  /** Token usage */
  usage: {
    inputTokens: number;
    outputTokens: number;
  };
  /** Which model actually served the request */
  model: string;
  /** Which provider served the request */
  provider: ProviderName;
  /** Stop reason */
  stopReason: 'end_turn' | 'tool_use' | 'max_tokens' | 'stop' | string;
  /** Request latency in milliseconds */
  latencyMs: number;
}

export interface LLMStreamChunk {
  /** Incremental text token */
  text?: string;
  /** Tool call delta */
  toolCall?: Partial<ToolCall>;
  /** Whether this is the final chunk */
  done: boolean;
  /** Full response (only on final chunk) */
  fullResponse?: LLMResponse;
}

// ── Provider Interface ─────────────────────────────────────────────────

/**
 * Every LLM backend implements this interface.
 * The LLMClient delegates to the appropriate provider.
 */
export interface LLMProvider {
  /** Provider identifier */
  readonly name: ProviderName;

  /** Whether this provider is currently configured and available */
  isAvailable(): boolean;

  /** Send a completion request and get a full response */
  complete(request: LLMRequest): Promise<LLMResponse>;

  /** Stream a completion request */
  stream(request: LLMRequest): AsyncGenerator<LLMStreamChunk>;

  /** List available models (for auto-discovery) */
  listModels?(): Promise<Array<{ id: string; name: string }>>;

  /** Health check */
  checkHealth?(): Promise<boolean>;
}

// ── LLM Client (Main Entry Point) ──────────────────────────────────────

class LLMClient {
  private providers = new Map<ProviderName, LLMProvider>();
  private defaultProvider: ProviderName = 'anthropic';

  /**
   * Register a provider backend.
   */
  registerProvider(provider: LLMProvider): void {
    this.providers.set(provider.name, provider);
    console.log(`[LLMClient] Registered provider: ${provider.name}`);
  }

  /**
   * Set the default provider for requests that don't specify one.
   */
  setDefaultProvider(name: ProviderName): void {
    this.defaultProvider = name;
  }

  /**
   * Get a specific provider by name.
   */
  getProvider(name: ProviderName): LLMProvider | undefined {
    return this.providers.get(name);
  }

  /**
   * Check if a provider is registered and available.
   */
  isProviderAvailable(name: ProviderName): boolean {
    const provider = this.providers.get(name);
    return !!provider && provider.isAvailable();
  }

  /**
   * Send a completion request.
   * Routes to the specified provider, or the default if none specified.
   * If the selected provider fails at request time, automatically retries
   * with fallback providers (e.g. local → anthropic → openrouter).
   */
  async complete(request: LLMRequest, providerName?: ProviderName): Promise<LLMResponse> {
    const provider = this.resolveProvider(providerName);
    try {
      return await provider.complete(request);
    } catch (err: unknown) {
      // If this was an explicit provider request and there are fallbacks, try them
      const errMsg = err instanceof Error ? err.message : String(err);
      console.warn(
        `[LLMClient] Provider '${provider.name}' failed: ${errMsg} — trying fallbacks`
      );

      for (const [, fallback] of this.providers) {
        if (fallback.name === provider.name) continue;
        if (!fallback.isAvailable()) continue;
        try {
          console.warn(`[LLMClient] Retrying with fallback provider '${fallback.name}'`);
          return await fallback.complete(request);
        } catch {
          // Fallback also failed, try next
          continue;
        }
      }

      // All providers failed — rethrow original error
      throw err;
    }
  }

  /**
   * Stream a completion request.
   * Falls back to other providers if the selected one fails on the first chunk.
   */
  async *stream(request: LLMRequest, providerName?: ProviderName): AsyncGenerator<LLMStreamChunk> {
    const provider = this.resolveProvider(providerName);
    try {
      yield* provider.stream(request);
      return; // Completed successfully
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.warn(
        `[LLMClient] Streaming from '${provider.name}' failed: ${errMsg} — trying fallbacks`
      );
    }

    // Try fallback providers
    for (const [, fallback] of this.providers) {
      if (fallback.name === provider.name) continue;
      if (!fallback.isAvailable()) continue;
      try {
        console.warn(`[LLMClient] Retrying stream with fallback '${fallback.name}'`);
        yield* fallback.stream(request);
        return;
      } catch {
        continue;
      }
    }

    throw new Error(`[LLMClient] All providers failed for streaming request.`);
  }

  /**
   * Simple text completion — convenience wrapper for the most common pattern.
   * Takes a user prompt and optional system prompt, returns just the text.
   */
  async text(
    prompt: string,
    options: {
      systemPrompt?: string;
      model?: string;
      maxTokens?: number;
      temperature?: number;
      provider?: ProviderName;
      taskHint?: TaskCategory;
      signal?: AbortSignal;
    } = {}
  ): Promise<string> {
    const response = await this.complete(
      {
        messages: [{ role: 'user', content: prompt }],
        systemPrompt: options.systemPrompt,
        model: options.model,
        maxTokens: options.maxTokens ?? 1024,
        temperature: options.temperature,
        taskHint: options.taskHint,
        signal: options.signal,
      },
      options.provider
    );
    return response.content;
  }

  /**
   * Resolve which provider to use for a request.
   */
  private resolveProvider(explicit?: ProviderName): LLMProvider {
    const name = explicit || this.defaultProvider;
    const provider = this.providers.get(name);

    if (!provider) {
      // Try fallback to any available provider
      for (const [, p] of this.providers) {
        if (p.isAvailable()) {
          console.warn(`[LLMClient] Provider '${name}' not found, falling back to '${p.name}'`);
          return p;
        }
      }
      throw new Error(
        `[LLMClient] No LLM provider available. Requested: '${name}'. ` +
        `Registered: [${[...this.providers.keys()].join(', ')}]`
      );
    }

    if (!provider.isAvailable()) {
      // Try fallback
      for (const [, p] of this.providers) {
        if (p.isAvailable() && p.name !== name) {
          console.warn(`[LLMClient] Provider '${name}' unavailable, falling back to '${p.name}'`);
          return p;
        }
      }
      throw new Error(`[LLMClient] Provider '${name}' is not available and no fallback found.`);
    }

    return provider;
  }
}

/** Singleton LLM client — import this everywhere */
export const llmClient = new LLMClient();

// ── Local-First Routing Wrapper ────────────────────────────────────────

/**
 * Confidence thresholds by task complexity.
 * Higher complexity demands higher confidence to avoid cloud escalation.
 */
const CONFIDENCE_THRESHOLDS: Record<string, number> = {
  trivial: 0.3,
  simple: 0.3,
  moderate: 0.5,
  complex: 0.7,
  expert: 0.7,
};

export interface LocalFirstOptions {
  /** Task complexity — determines confidence threshold for escalation */
  complexity?: TaskComplexity;
  /** Override the default confidence threshold */
  confidenceThreshold?: number;
  /** Cloud provider to escalate to when confidence is low */
  cloudProvider?: ProviderName;
  /** Callback for routing events (e.g., logging to context stream) */
  onRoutingEvent?: (event: RoutingEvent) => void;
}

export interface RoutingEvent {
  type: 'local-attempt' | 'confidence-assessed' | 'escalation-requested' | 'escalation-result' | 'final-response';
  provider?: ProviderName;
  confidence?: number;
  escalate?: boolean;
  allowed?: boolean;
  reason?: string;
  timestamp: number;
}

/**
 * Route a request local-first with confidence assessment and cloud gating.
 *
 * Flow:
 *   1. Execute request via local provider
 *   2. Run ConfidenceAssessor on the response
 *   3. If confidence is below threshold → request CloudGate escalation
 *   4. If gate allows → retry with cloud provider
 *   5. Return final response
 *
 * This function does NOT modify the existing chat()/complete() behavior.
 * It is an opt-in wrapper for the local-first routing pattern.
 */
export async function routeLocalFirst(
  request: LLMRequest,
  options: LocalFirstOptions = {},
): Promise<LLMResponse> {
  const complexity = options.complexity ?? 'moderate';
  const threshold = options.confidenceThreshold ?? CONFIDENCE_THRESHOLDS[complexity] ?? 0.5;
  const cloudProvider = options.cloudProvider ?? 'anthropic';
  const emit = options.onRoutingEvent ?? (() => {});

  // Step 1: Execute locally
  emit({
    type: 'local-attempt',
    provider: 'local',
    timestamp: Date.now(),
  });

  const localResponse = await llmClient.complete(request, 'local');

  // Step 2: Assess confidence
  const confidence = assessConfidence(request, localResponse, request.tools);

  emit({
    type: 'confidence-assessed',
    provider: 'local',
    confidence: confidence.score,
    escalate: confidence.score < threshold,
    timestamp: Date.now(),
  });

  // If confidence is sufficient, return the local response
  if (confidence.score >= threshold) {
    emit({
      type: 'final-response',
      provider: 'local',
      confidence: confidence.score,
      timestamp: Date.now(),
    });
    return localResponse;
  }

  // Step 3: Request escalation through CloudGate
  emit({
    type: 'escalation-requested',
    provider: cloudProvider,
    confidence: confidence.score,
    timestamp: Date.now(),
  });

  const gate = CloudGate.getInstance();
  const decision = await gate.requestEscalation({
    taskCategory: (request.taskHint ?? 'general') as import('./cloud-gate').TaskCategory,
    confidence,
    promptPreview: typeof request.messages[0]?.content === 'string'
      ? request.messages[0].content
      : 'Complex request',
    targetProvider: cloudProvider,
  });

  emit({
    type: 'escalation-result',
    allowed: decision.allowed,
    reason: decision.reason,
    timestamp: Date.now(),
  });

  // Step 4: If gate denies, return local response as-is
  if (!decision.allowed) {
    emit({
      type: 'final-response',
      provider: 'local',
      confidence: confidence.score,
      reason: `escalation-denied:${decision.reason}`,
      timestamp: Date.now(),
    });
    return localResponse;
  }

  // Step 5: Retry with cloud provider
  const cloudResponse = await llmClient.complete(request, cloudProvider);

  emit({
    type: 'final-response',
    provider: cloudProvider,
    timestamp: Date.now(),
  });

  return cloudResponse;
}
