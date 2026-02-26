/**
 * openrouter.ts — OpenRouter API client for Agent Friday.
 *
 * Provides a unified gateway to 200+ AI models through a single API:
 * - OpenAI-compatible Chat Completions (streaming + non-streaming)
 * - Tool calling with parallel execution
 * - AbortController cancellation support
 * - Automatic SSE parsing with keepalive handling
 * - Model fallback chains
 *
 * API: https://openrouter.ai/api/v1/chat/completions
 */

import { settingsManager } from './settings';

/* ── Types ────────────────────────────────────────────────────────────── */

export interface OpenRouterMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | OpenRouterContentPart[] | null;
  tool_calls?: OpenRouterToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface OpenRouterContentPart {
  type: 'text' | 'image_url';
  text?: string;
  image_url?: { url: string; detail?: 'auto' | 'low' | 'high' };
}

export interface OpenRouterTool {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
}

export interface OpenRouterToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

export interface OpenRouterRequestOptions {
  model: string;
  messages: OpenRouterMessage[];
  tools?: OpenRouterTool[];
  tool_choice?: 'auto' | 'none' | { type: 'function'; function: { name: string } };
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stream?: boolean;
  signal?: AbortSignal;
  /** Provider-specific preferences */
  provider?: {
    order?: string[];
    allow_fallbacks?: boolean;
    require_parameters?: boolean;
  };
  /** Response format for structured output */
  response_format?: { type: 'json_object' | 'text' };
}

export interface OpenRouterResponse {
  id: string;
  model: string;
  choices: Array<{
    index: number;
    message: {
      role: 'assistant';
      content: string | null;
      tool_calls?: OpenRouterToolCall[];
    };
    finish_reason: 'stop' | 'tool_calls' | 'length' | 'content_filter' | null;
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export interface OpenRouterStreamChunk {
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

export interface StreamCallbacks {
  onToken?: (token: string) => void;
  onToolCall?: (toolCall: OpenRouterToolCall) => void;
  onDone?: (fullText: string, toolCalls: OpenRouterToolCall[]) => void;
  onError?: (error: Error) => void;
}

/* ── Constants ────────────────────────────────────────────────────────── */

const BASE_URL = 'https://openrouter.ai/api/v1/chat/completions';

/** Popular models accessible through OpenRouter */
export const OPENROUTER_MODELS = {
  // Anthropic
  'claude-opus-4': 'anthropic/claude-opus-4',
  'claude-sonnet-4': 'anthropic/claude-sonnet-4',
  'claude-haiku-3.5': 'anthropic/claude-3.5-haiku',
  // OpenAI
  'gpt-4o': 'openai/gpt-4o',
  'gpt-4o-mini': 'openai/gpt-4o-mini',
  'gpt-4.1': 'openai/gpt-4.1',
  'gpt-4.1-mini': 'openai/gpt-4.1-mini',
  'gpt-4.1-nano': 'openai/gpt-4.1-nano',
  'o4-mini': 'openai/o4-mini',
  // Google
  'gemini-2.5-pro': 'google/gemini-2.5-pro-preview',
  'gemini-2.5-flash': 'google/gemini-2.5-flash-preview',
  'gemini-2.0-flash': 'google/gemini-2.0-flash-001',
  // Meta
  'llama-4-maverick': 'meta-llama/llama-4-maverick',
  'llama-4-scout': 'meta-llama/llama-4-scout',
  'llama-3.3-70b': 'meta-llama/llama-3.3-70b-instruct',
  // DeepSeek
  'deepseek-r1': 'deepseek/deepseek-r1',
  'deepseek-chat-v3': 'deepseek/deepseek-chat-v3-0324',
  // Mistral
  'mistral-large': 'mistralai/mistral-large-2411',
  // Qwen
  'qwen-2.5-72b': 'qwen/qwen-2.5-72b-instruct',
  'qwen-qwq-32b': 'qwen/qwq-32b',
} as const;

/* ── Client ───────────────────────────────────────────────────────────── */

class OpenRouterClient {
  private getApiKey(): string {
    return settingsManager.get().openrouterApiKey || '';
  }

  isConfigured(): boolean {
    return !!this.getApiKey();
  }

  /**
   * Non-streaming chat completion.
   */
  async chat(options: OpenRouterRequestOptions): Promise<OpenRouterResponse> {
    const apiKey = this.getApiKey();
    if (!apiKey) throw new Error('OpenRouter API key not configured');

    const body: Record<string, unknown> = {
      model: options.model,
      messages: options.messages,
      stream: false,
    };

    if (options.tools && options.tools.length > 0) body.tools = options.tools;
    if (options.tool_choice) body.tool_choice = options.tool_choice;
    if (options.max_tokens) body.max_tokens = options.max_tokens;
    if (options.temperature !== undefined) body.temperature = options.temperature;
    if (options.top_p !== undefined) body.top_p = options.top_p;
    if (options.provider) body.provider = options.provider;
    if (options.response_format) body.response_format = options.response_format;

    const fetchOptions: RequestInit = {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://agentfriday.app',
        'X-Title': 'Agent Friday',
      },
      body: JSON.stringify(body),
    };

    if (options.signal) fetchOptions.signal = options.signal;

    const res = await fetch(BASE_URL, fetchOptions);

    if (!res.ok) {
      const errBody = await res.text();
      let errMsg: string;
      try {
        const parsed = JSON.parse(errBody);
        errMsg = parsed.error?.message || parsed.error || errBody;
      } catch {
        errMsg = errBody;
      }
      throw new Error(`OpenRouter API error (${res.status}): ${errMsg}`);
    }

    return res.json() as Promise<OpenRouterResponse>;
  }

  /**
   * Streaming chat completion with SSE parsing.
   * Returns the complete response once streaming finishes.
   */
  async chatStream(
    options: OpenRouterRequestOptions,
    callbacks: StreamCallbacks = {}
  ): Promise<{ text: string; toolCalls: OpenRouterToolCall[]; model: string }> {
    const apiKey = this.getApiKey();
    if (!apiKey) throw new Error('OpenRouter API key not configured');

    const body: Record<string, unknown> = {
      model: options.model,
      messages: options.messages,
      stream: true,
    };

    if (options.tools && options.tools.length > 0) body.tools = options.tools;
    if (options.tool_choice) body.tool_choice = options.tool_choice;
    if (options.max_tokens) body.max_tokens = options.max_tokens;
    if (options.temperature !== undefined) body.temperature = options.temperature;
    if (options.top_p !== undefined) body.top_p = options.top_p;
    if (options.provider) body.provider = options.provider;
    if (options.response_format) body.response_format = options.response_format;

    const fetchOptions: RequestInit = {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://agentfriday.app',
        'X-Title': 'Agent Friday',
      },
      body: JSON.stringify(body),
    };

    if (options.signal) fetchOptions.signal = options.signal;

    const res = await fetch(BASE_URL, fetchOptions);

    if (!res.ok) {
      const errBody = await res.text();
      let errMsg: string;
      try {
        const parsed = JSON.parse(errBody);
        errMsg = parsed.error?.message || parsed.error || errBody;
      } catch {
        errMsg = errBody;
      }
      throw new Error(`OpenRouter API error (${res.status}): ${errMsg}`);
    }

    if (!res.body) {
      throw new Error('OpenRouter: no response body for streaming request');
    }

    // Parse SSE stream
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    let modelUsed = options.model;
    const toolCallAccumulator: Map<number, { id: string; name: string; args: string }> = new Map();
    let buffer = '';

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

          // Skip empty lines and keepalive comments
          if (!trimmed || trimmed.startsWith(':')) continue;

          if (!trimmed.startsWith('data: ')) continue;

          const data = trimmed.slice(6);
          if (data === '[DONE]') continue;

          try {
            const chunk: OpenRouterStreamChunk = JSON.parse(data);

            if (chunk.model) modelUsed = chunk.model;

            for (const choice of chunk.choices) {
              // Text content
              if (choice.delta.content) {
                fullText += choice.delta.content;
                callbacks.onToken?.(choice.delta.content);
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
          } catch {
            // Skip malformed JSON chunks (can happen with SSE)
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // Stream was cancelled — return what we have
      } else {
        callbacks.onError?.(err as Error);
        throw err;
      }
    }

    // Convert accumulated tool calls to final format
    const toolCalls: OpenRouterToolCall[] = [];
    for (const [, acc] of toolCallAccumulator) {
      const tc: OpenRouterToolCall = {
        id: acc.id,
        type: 'function',
        function: { name: acc.name, arguments: acc.args },
      };
      toolCalls.push(tc);
      callbacks.onToolCall?.(tc);
    }

    callbacks.onDone?.(fullText, toolCalls);

    return { text: fullText, toolCalls, model: modelUsed };
  }

  /**
   * Simple text completion — convenience wrapper.
   * Returns just the text response.
   */
  async complete(
    model: string,
    prompt: string,
    options: {
      systemPrompt?: string;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    } = {}
  ): Promise<string> {
    const messages: OpenRouterMessage[] = [];

    if (options.systemPrompt) {
      messages.push({ role: 'system', content: options.systemPrompt });
    }
    messages.push({ role: 'user', content: prompt });

    const response = await this.chat({
      model,
      messages,
      max_tokens: options.maxTokens || 2048,
      temperature: options.temperature,
      signal: options.signal,
    });

    return response.choices[0]?.message?.content || '';
  }

  /**
   * Streaming text completion — convenience wrapper.
   * Calls onToken for each chunk, returns full text.
   */
  async completeStream(
    model: string,
    prompt: string,
    onToken: (token: string) => void,
    options: {
      systemPrompt?: string;
      maxTokens?: number;
      temperature?: number;
      signal?: AbortSignal;
    } = {}
  ): Promise<string> {
    const messages: OpenRouterMessage[] = [];

    if (options.systemPrompt) {
      messages.push({ role: 'system', content: options.systemPrompt });
    }
    messages.push({ role: 'user', content: prompt });

    const result = await this.chatStream(
      {
        model,
        messages,
        max_tokens: options.maxTokens || 2048,
        temperature: options.temperature,
        signal: options.signal,
      },
      { onToken }
    );

    return result.text;
  }

  /**
   * Chat completion with tool-use loop (similar to runClaudeToolLoop).
   * Automatically handles tool calls and re-sends results.
   */
  async chatWithTools(options: {
    model: string;
    systemPrompt: string;
    messages: OpenRouterMessage[];
    tools: OpenRouterTool[];
    executeTool: (name: string, args: Record<string, unknown>) => Promise<string>;
    maxIterations?: number;
    signal?: AbortSignal;
    onThought?: (text: string) => void;
  }): Promise<{ text: string; toolCalls: number; model: string }> {
    const {
      model,
      systemPrompt,
      messages,
      tools,
      executeTool,
      maxIterations = 20,
      signal,
      onThought,
    } = options;

    const conversationMessages: OpenRouterMessage[] = [
      { role: 'system', content: systemPrompt },
      ...messages,
    ];

    let iterations = 0;
    let finalText = '';
    let modelUsed = model;

    while (iterations < maxIterations) {
      const response = await this.chat({
        model,
        messages: conversationMessages,
        tools: tools.length > 0 ? tools : undefined,
        tool_choice: tools.length > 0 ? 'auto' : undefined,
        max_tokens: 4096,
        signal,
      });

      modelUsed = response.model || model;
      const choice = response.choices[0];
      if (!choice) break;

      const assistantMsg = choice.message;

      // If no tool calls, we're done
      if (!assistantMsg.tool_calls || assistantMsg.tool_calls.length === 0) {
        finalText = assistantMsg.content || '';
        break;
      }

      // Add assistant message with tool calls to conversation
      conversationMessages.push({
        role: 'assistant',
        content: assistantMsg.content,
        tool_calls: assistantMsg.tool_calls,
      });

      // Count this round as one iteration (consistent with Anthropic path which counts per-round, not per-tool)
      iterations++;

      // Execute each tool call
      for (const tc of assistantMsg.tool_calls) {
        onThought?.(`Calling tool: ${tc.function.name}`);

        let result: string;
        try {
          const args = JSON.parse(tc.function.arguments);
          result = await executeTool(tc.function.name, args);
        } catch (err) {
          result = `Error: ${err instanceof Error ? err.message : String(err)}`;
        }

        conversationMessages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: result,
        });
      }
    }

    if (iterations >= maxIterations) {
      finalText = 'Tool-use limit reached. Please try breaking your request into smaller steps.';
    }

    return { text: finalText, toolCalls: iterations, model: modelUsed };
  }

  /**
   * List available models (hits OpenRouter model endpoint).
   */
  async listModels(): Promise<Array<{ id: string; name: string; pricing: { prompt: string; completion: string } }>> {
    const res = await fetch('https://openrouter.ai/api/v1/models', {
      headers: {
        'Authorization': `Bearer ${this.getApiKey()}`,
      },
    });

    if (!res.ok) {
      throw new Error(`Failed to fetch models: ${res.status}`);
    }

    const data = await res.json();
    return (data.data || []).map((m: any) => ({
      id: m.id,
      name: m.name,
      pricing: {
        prompt: m.pricing?.prompt || '0',
        completion: m.pricing?.completion || '0',
      },
    }));
  }

  /**
   * Check remaining credits.
   */
  async getCredits(): Promise<{ remaining: number; limit: number; usage: number }> {
    const res = await fetch('https://openrouter.ai/api/v1/auth/key', {
      headers: {
        'Authorization': `Bearer ${this.getApiKey()}`,
      },
    });

    if (!res.ok) {
      throw new Error(`Failed to fetch credits: ${res.status}`);
    }

    const data = await res.json();
    return {
      remaining: data.data?.limit ? data.data.limit - (data.data.usage || 0) : Infinity,
      limit: data.data?.limit || Infinity,
      usage: data.data?.usage || 0,
    };
  }

  /**
   * Validate an API key by checking auth endpoint.
   */
  async validateKey(key?: string): Promise<boolean> {
    const apiKey = key || this.getApiKey();
    if (!apiKey) return false;

    try {
      const res = await fetch('https://openrouter.ai/api/v1/auth/key', {
        headers: { 'Authorization': `Bearer ${apiKey}` },
      });
      return res.ok;
    } catch {
      return false;
    }
  }
}

export const openRouter = new OpenRouterClient();
