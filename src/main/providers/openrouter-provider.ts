/**
 * openrouter-provider.ts — OpenRouter backend for the LLM abstraction layer.
 *
 * Wraps the existing OpenRouterClient (openrouter.ts) to implement LLMProvider.
 * OpenRouter provides access to 200+ models through a single OpenAI-compatible API.
 */

import type {
  LLMProvider,
  LLMRequest,
  LLMResponse,
  LLMStreamChunk,
  ChatMessage,
  ToolCall,
  ToolDefinition,
} from '../llm-client';
import type { ProviderName } from '../intelligence-router';
import {
  openRouter,
  type OpenRouterMessage,
  type OpenRouterTool,
  type OpenRouterToolCall,
} from '../openrouter';
import { settingsManager } from '../settings';

/** Default model for OpenRouter requests */
const DEFAULT_MODEL = 'anthropic/claude-sonnet-4';

export class OpenRouterProvider implements LLMProvider {
  readonly name: ProviderName = 'openrouter';

  isAvailable(): boolean {
    return openRouter.isConfigured();
  }

  async complete(request: LLMRequest): Promise<LLMResponse> {
    if (!this.isAvailable()) {
      throw new Error('OpenRouter API key not configured. Please set it in Settings.');
    }

    const model = request.model || settingsManager.getOpenrouterModel() || DEFAULT_MODEL;
    const startTime = Date.now();

    // Convert messages to OpenRouter format
    const messages = this.formatMessages(request);
    const tools = request.tools ? this.formatTools(request.tools) : undefined;

    const response = await openRouter.chat({
      model,
      messages,
      tools,
      max_tokens: request.maxTokens || 1024,
      temperature: request.temperature,
      signal: request.signal,
      response_format: request.responseFormat,
    });

    const latencyMs = Date.now() - startTime;
    return this.parseResponse(response, model, latencyMs);
  }

  async *stream(request: LLMRequest): AsyncGenerator<LLMStreamChunk> {
    if (!this.isAvailable()) {
      throw new Error('OpenRouter API key not configured. Please set it in Settings.');
    }

    const model = request.model || settingsManager.getOpenrouterModel() || DEFAULT_MODEL;
    const startTime = Date.now();

    const messages = this.formatMessages(request);
    const tools = request.tools ? this.formatTools(request.tools) : undefined;

    let fullText = '';
    const toolCalls: ToolCall[] = [];
    let resolvedModel = model;

    const result = await openRouter.chatStream(
      {
        model,
        messages,
        tools,
        max_tokens: request.maxTokens || 4096,
        temperature: request.temperature,
        signal: request.signal,
      },
      {
        onToken: (token: string) => {
          fullText += token;
        },
        onToolCall: (tc: OpenRouterToolCall) => {
          let parsedArgs: unknown;
          try {
            parsedArgs = JSON.parse(tc.function.arguments);
          } catch {
            parsedArgs = tc.function.arguments;
          }
          toolCalls.push({
            id: tc.id,
            type: 'tool_use',
            name: tc.function.name,
            input: parsedArgs,
          });
        },
      }
    );

    resolvedModel = result.model || model;
    fullText = result.text || fullText;

    // Yield a single chunk with the complete response
    // (OpenRouter's streaming callback approach doesn't map cleanly to yield-per-token)
    const latencyMs = Date.now() - startTime;
    yield {
      text: fullText,
      done: true,
      fullResponse: {
        content: fullText,
        toolCalls,
        usage: { inputTokens: 0, outputTokens: 0 }, // OpenRouter doesn't always report usage in stream
        model: resolvedModel,
        provider: 'openrouter',
        stopReason: toolCalls.length > 0 ? 'tool_use' : 'end_turn',
        latencyMs,
      },
    };
  }

  // ── Private: Format Conversion ──────────────────────────────────────

  private formatMessages(request: LLMRequest): OpenRouterMessage[] {
    const messages: OpenRouterMessage[] = [];

    // Add system prompt first if present
    if (request.systemPrompt) {
      messages.push({ role: 'system', content: request.systemPrompt });
    }

    for (const msg of request.messages) {
      if (msg.role === 'system') {
        messages.push({ role: 'system', content: typeof msg.content === 'string' ? msg.content : '' });
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
        const orToolCalls: OpenRouterToolCall[] = msg.tool_calls.map((tc) => ({
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
          tool_calls: orToolCalls,
        });
        continue;
      }

      // Regular message
      if (typeof msg.content === 'string') {
        messages.push({ role: msg.role as 'user' | 'assistant', content: msg.content });
      } else if (Array.isArray(msg.content)) {
        // Convert content parts to OpenRouter format
        const parts = msg.content.map((p: any) => {
          if (p.type === 'text') return { type: 'text' as const, text: p.text };
          if (p.type === 'image' && p.source) {
            return {
              type: 'image_url' as const,
              image_url: {
                url: `data:${p.source.media_type};base64,${p.source.data}`,
              },
            };
          }
          if (p.type === 'image_url' || p.image_url) return p;
          return { type: 'text' as const, text: JSON.stringify(p) };
        });
        messages.push({ role: msg.role as 'user' | 'assistant', content: parts });
      } else {
        messages.push({
          role: msg.role as 'user' | 'assistant',
          content: msg.content as string | null,
        });
      }
    }

    return messages;
  }

  private formatTools(tools: ToolDefinition[]): OpenRouterTool[] {
    return tools.map((t) => {
      // If already in OpenAI format
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
      // Convert from Anthropic format
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

  private parseResponse(response: any, model: string, latencyMs: number): LLMResponse {
    const choice = response.choices?.[0];
    if (!choice) {
      return {
        content: '',
        toolCalls: [],
        usage: { inputTokens: 0, outputTokens: 0 },
        model,
        provider: 'openrouter',
        stopReason: 'end_turn',
        latencyMs,
      };
    }

    const content = choice.message?.content || '';
    const toolCalls: ToolCall[] = (choice.message?.tool_calls || []).map((tc: OpenRouterToolCall) => {
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

    // Map finish reasons
    let stopReason = choice.finish_reason || 'end_turn';
    if (stopReason === 'stop') stopReason = 'end_turn';
    else if (stopReason === 'tool_calls') stopReason = 'tool_use';

    return {
      content,
      toolCalls,
      usage: {
        inputTokens: response.usage?.prompt_tokens || 0,
        outputTokens: response.usage?.completion_tokens || 0,
      },
      model: response.model || model,
      provider: 'openrouter',
      stopReason,
      latencyMs,
    };
  }
}
