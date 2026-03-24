/**
 * anthropic-provider.ts — Anthropic SDK backend for the LLM abstraction layer.
 *
 * Wraps the @anthropic-ai/sdk to implement the LLMProvider interface.
 * Handles Anthropic-specific message formatting, tool calling format,
 * and streaming via SSE.
 *
 * This is the "reference" provider — the existing codebase's direct
 * Anthropic calls get refactored to pass through here.
 */

import type {
  LLMProvider,
  LLMRequest,
  LLMResponse,
  LLMStreamChunk,
  ChatMessage,
  ToolCall,
  ToolDefinition,
  ContentPart,
} from '../llm-client';
import type { ProviderName } from '../intelligence-router';
import { settingsManager } from '../settings';

// Late-bind Anthropic SDK to avoid import-time crashes when key isn't set
let _Anthropic: any = null;
async function getAnthropicSDK() {
  if (!_Anthropic) {
    _Anthropic = await import('@anthropic-ai/sdk');
  }
  return _Anthropic;
}

/** Default model if none specified */
const DEFAULT_MODEL = 'claude-sonnet-4-20250514';

export class AnthropicProvider implements LLMProvider {
  readonly name: ProviderName = 'anthropic';

  private getApiKey(): string | undefined {
    return settingsManager.getAnthropicApiKey() || undefined;
  }

  isAvailable(): boolean {
    return !!this.getApiKey();
  }

  async complete(request: LLMRequest): Promise<LLMResponse> {
    const apiKey = this.getApiKey();
    if (!apiKey) {
      throw new Error('ANTHROPIC_API_KEY not configured. Please set it in Settings.');
    }

    return this.withRetry(async () => {
      const Anthropic = await getAnthropicSDK();
      const anthropic = new Anthropic.default
        ? new Anthropic.default({ apiKey })
        : new Anthropic({ apiKey });

      const model = request.model || DEFAULT_MODEL;
      const startTime = Date.now();

      // Convert messages from unified format to Anthropic format
      const { messages, system } = this.formatMessages(request);

      // Convert tools to Anthropic format
      const tools = request.tools ? this.formatTools(request.tools) : undefined;

      // Build tool_choice
      let toolChoice: any = undefined;
      if (request.toolChoice === 'auto') {
        toolChoice = { type: 'auto' };
      } else if (request.toolChoice === 'none') {
        toolChoice = undefined; // Anthropic doesn't have explicit 'none'
      } else if (request.toolChoice && typeof request.toolChoice === 'object') {
        toolChoice = { type: 'tool', name: request.toolChoice.name };
      }

      const createParams: any = {
        model,
        max_tokens: request.maxTokens || 1024,
        messages,
        ...(system ? { system } : {}),
        ...(tools && tools.length > 0 ? { tools } : {}),
        ...(toolChoice ? { tool_choice: toolChoice } : {}),
        ...(request.temperature !== undefined ? { temperature: request.temperature } : {}),
      };

      const response = await anthropic.messages.create(
        createParams,
        request.signal ? { signal: request.signal } : undefined
      );

      const latencyMs = Date.now() - startTime;

      return this.parseResponse(response, model, latencyMs);
    }, 'complete');
  }

  async *stream(request: LLMRequest): AsyncGenerator<LLMStreamChunk> {
    const apiKey = this.getApiKey();
    if (!apiKey) {
      throw new Error('ANTHROPIC_API_KEY not configured. Please set it in Settings.');
    }

    const Anthropic = await getAnthropicSDK();
    const anthropic = new Anthropic.default
      ? new Anthropic.default({ apiKey })
      : new Anthropic({ apiKey });

    const model = request.model || DEFAULT_MODEL;
    const startTime = Date.now();
    const { messages, system } = this.formatMessages(request);
    const tools = request.tools ? this.formatTools(request.tools) : undefined;

    const createParams: any = {
      model,
      max_tokens: request.maxTokens || 4096,
      messages,
      stream: true,
      ...(system ? { system } : {}),
      ...(tools && tools.length > 0 ? { tools } : {}),
      ...(request.temperature !== undefined ? { temperature: request.temperature } : {}),
    };

    const stream = await anthropic.messages.create(
      createParams,
      request.signal ? { signal: request.signal } : undefined
    );

    let fullText = '';
    let inputTokens = 0;
    let outputTokens = 0;
    const toolCalls: ToolCall[] = [];
    let stopReason = 'end_turn';
    let currentToolUse: { id: string; name: string; inputJson: string } | null = null;

    for await (const event of stream) {
      if (event.type === 'message_start') {
        inputTokens = event.message?.usage?.input_tokens || 0;
      } else if (event.type === 'content_block_start') {
        if (event.content_block?.type === 'tool_use') {
          currentToolUse = {
            id: event.content_block.id,
            name: event.content_block.name,
            inputJson: '',
          };
        }
      } else if (event.type === 'content_block_delta') {
        if (event.delta?.type === 'text_delta' && event.delta.text) {
          fullText += event.delta.text;
          yield { text: event.delta.text, done: false };
        } else if (event.delta?.type === 'input_json_delta' && currentToolUse) {
          currentToolUse.inputJson += event.delta.partial_json || '';
        }
      } else if (event.type === 'content_block_stop') {
        if (currentToolUse) {
          let parsedInput: unknown;
          try {
            parsedInput = JSON.parse(currentToolUse.inputJson);
          } catch {
            parsedInput = currentToolUse.inputJson;
          }
          const tc: ToolCall = {
            id: currentToolUse.id,
            type: 'tool_use',
            name: currentToolUse.name,
            input: parsedInput,
          };
          toolCalls.push(tc);
          yield { toolCall: tc, done: false };
          currentToolUse = null;
        }
      } else if (event.type === 'message_delta') {
        stopReason = event.delta?.stop_reason || stopReason;
        outputTokens = event.usage?.output_tokens || outputTokens;
      }
    }

    // Final chunk with complete response
    const latencyMs = Date.now() - startTime;
    yield {
      done: true,
      fullResponse: {
        content: fullText,
        toolCalls,
        usage: { inputTokens, outputTokens },
        model,
        provider: 'anthropic',
        stopReason,
        latencyMs,
      },
    };
  }

  // ── Private: Retry Logic ───────────────────────────────────────────

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
          `[AnthropicProvider] ${label} attempt ${attempt + 1} failed, retrying in ${Math.round(delay)}ms...`
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
    // Check for Anthropic SDK error status codes
    if (err && typeof err === 'object' && 'status' in err) {
      const status = (err as any).status;
      return status === 429 || status === 500 || status === 502 || status === 503 || status === 529;
    }
    return false;
  }

  // ── Private: Format Conversion ──────────────────────────────────────

  /**
   * Convert unified ChatMessage[] to Anthropic's format.
   * Anthropic uses a separate 'system' field rather than a system message.
   */
  private formatMessages(request: LLMRequest): {
    messages: any[];
    system: string | undefined;
  } {
    let system = request.systemPrompt || undefined;
    const messages: any[] = [];

    for (const msg of request.messages) {
      if (msg.role === 'system') {
        // Anthropic doesn't use system role in messages array
        system = (system ? system + '\n\n' : '') + (typeof msg.content === 'string' ? msg.content : '');
        continue;
      }

      if (msg.role === 'tool') {
        // Convert tool results to Anthropic format
        messages.push({
          role: 'user',
          content: [{
            type: 'tool_result',
            tool_use_id: msg.tool_call_id,
            content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
            ...(msg.name === 'error' ? { is_error: true } : {}),
          }],
        });
        continue;
      }

      if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        // Assistant message with tool calls — Anthropic uses content blocks
        const contentBlocks: any[] = [];
        if (typeof msg.content === 'string' && msg.content) {
          contentBlocks.push({ type: 'text', text: msg.content });
        }
        for (const tc of msg.tool_calls) {
          contentBlocks.push({
            type: 'tool_use',
            id: tc.id,
            name: tc.name,
            input: tc.input,
          });
        }
        messages.push({ role: 'assistant', content: contentBlocks });
        continue;
      }

      // Regular user/assistant message
      if (typeof msg.content === 'string') {
        messages.push({ role: msg.role, content: msg.content });
      } else if (Array.isArray(msg.content)) {
        // Convert content parts
        const parts = (msg.content as ContentPart[]).map((p) => {
          if (p.type === 'text') {
            return { type: 'text', text: p.text };
          }
          if (p.type === 'image' && p.source) {
            return {
              type: 'image',
              source: {
                type: 'base64',
                media_type: p.source.media_type,
                data: p.source.data,
              },
            };
          }
          return p;
        });
        messages.push({ role: msg.role, content: parts });
      } else {
        messages.push({ role: msg.role, content: msg.content });
      }
    }

    return { messages, system };
  }

  /**
   * Convert unified ToolDefinition[] to Anthropic's tool format.
   */
  private formatTools(tools: ToolDefinition[]): any[] {
    return tools.map((t) => {
      // If it's already in Anthropic format (name + input_schema)
      if (t.input_schema) {
        return {
          name: t.name,
          description: t.description || '',
          input_schema: t.input_schema,
        };
      }
      // If it's in OpenAI format (function.name + function.parameters)
      if (t.function) {
        return {
          name: t.function.name,
          description: t.function.description || '',
          input_schema: t.function.parameters || { type: 'object', properties: {} },
        };
      }
      // Bare minimum
      return {
        name: t.name,
        description: t.description || '',
        input_schema: { type: 'object', properties: {} },
      };
    });
  }

  /**
   * Parse Anthropic's response into our unified format.
   */
  private parseResponse(response: any, model: string, latencyMs: number): LLMResponse {
    let content = '';
    const toolCalls: ToolCall[] = [];

    for (const block of response.content || []) {
      if (block.type === 'text') {
        content += block.text;
      } else if (block.type === 'tool_use') {
        toolCalls.push({
          id: block.id,
          type: 'tool_use',
          name: block.name,
          input: block.input,
        });
      }
    }

    // Map Anthropic stop reasons to our unified format
    let stopReason: string = response.stop_reason || 'end_turn';
    if (stopReason === 'end_turn') stopReason = 'end_turn';
    else if (stopReason === 'tool_use') stopReason = 'tool_use';
    else if (stopReason === 'max_tokens') stopReason = 'max_tokens';

    return {
      content,
      toolCalls,
      usage: {
        inputTokens: response.usage?.input_tokens || 0,
        outputTokens: response.usage?.output_tokens || 0,
      },
      model,
      provider: 'anthropic',
      stopReason,
      latencyMs,
    };
  }
}
