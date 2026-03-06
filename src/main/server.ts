import express from 'express';
import cors from 'cors';
import crypto from 'crypto';
import path from 'path';
import dotenv from 'dotenv';
import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenerativeAI } from '@google/generative-ai';
import { mcpClient } from './mcp-client';
import { buildSystemPrompt } from './personality';
import { browserToolDefs, executeBrowserTool } from './browser';
import { connectorRegistry } from './connectors/registry';
import { openRouter, OpenRouterMessage, OpenRouterTool } from './openrouter';
import { settingsManager } from './settings';
import { llmClient, type ChatMessage, type ToolDefinition } from './llm-client';
import { integrityManager } from './integrity';
import { assertMessageArray } from './ipc/validate';

/**
 * cLaw Security Fix (CRITICAL-001): Safe mode tool filtering.
 * When integrity is compromised, strip ALL side-effect tools.
 * Only allow read-only information retrieval tools.
 */
const SAFE_MODE_ALLOWED_TOOLS = new Set([
  // Read-only MCP tools
  'list_windows', 'get_active_window', 'read_clipboard', 'focus_window',
  'read_screen', 'read_file', 'list_directory',
  // Read-only browser tools
  'browser_screenshot', 'browser_get_page_info',
]);

function filterToolsForSafeMode<T extends { name: string }>(tools: T[]): T[] {
  if (!integrityManager.isInSafeMode()) return tools;
  console.warn('[Server/cLaw] Safe mode active — stripping all side-effect tools');
  return tools.filter(t => SAFE_MODE_ALLOWED_TOOLS.has(t.name));
}

dotenv.config();

/** Per-session token — only the Electron main process knows this. */
let sessionToken = '';
export function getSessionToken(): string { return sessionToken; }

export async function startServer(): Promise<number> {
  const app = express();

  // Generate a random session token for API authentication
  sessionToken = crypto.randomBytes(32).toString('hex');

  // CORS: restrict to localhost origins only
  app.use(cors({
    origin: (origin, callback) => {
      // Allow requests with no origin (same-origin, curl, Electron)
      if (!origin) return callback(null, true);
      if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
        return callback(null, true);
      }
      callback(new Error('CORS: origin not allowed'));
    },
  }));

  app.use(express.json({ limit: '10mb' }));

  // Crypto Sprint 6 (MEDIUM — DNS Rebinding): Validate the Host header to ensure requests
  // are actually coming from localhost. In a DNS rebinding attack, a malicious website
  // resolves an attacker domain to 127.0.0.1 — the Host header will contain the attacker's
  // domain, not "localhost". Rejecting non-localhost Host headers blocks this attack.
  app.use((req, res, next) => {
    const host = req.headers.host || '';
    if (!/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host)) {
      res.status(403).json({ error: 'Forbidden: invalid Host header' });
      return;
    }
    next();
  });

  // Security headers
  app.use((_req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('X-XSS-Protection', '1; mode=block');
    res.setHeader('Referrer-Policy', 'no-referrer');
    // Crypto Sprint 5: Restrictive CSP for the API server — it should never serve
    // page-rendering resources. Any error pages or HTML-bearing responses get locked down.
    res.setHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'");
    next();
  });

  // Session token authentication middleware for API routes
  const authenticateToken: express.RequestHandler = (req, res, next) => {
    const authHeader = req.headers.authorization;
    const token = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : null;

    // Security hardening: session token ONLY via Authorization header.
    // Query parameter fallback removed — tokens in URLs leak via referrer headers,
    // server logs, browser history, and proxy logs.
    // Crypto Sprint 4 (HIGH-TIMING): Use constant-time comparison to prevent
    // timing side-channel attacks on the session token. Regular === leaks
    // information about how many leading characters match.
    if (token && token.length === sessionToken.length) {
      const tokenBuf = Buffer.from(token, 'utf-8');
      const expectedBuf = Buffer.from(sessionToken, 'utf-8');
      if (crypto.timingSafeEqual(tokenBuf, expectedBuf)) {
        return next();
      }
    }

    // cLaw Security Fix (HIGH-002): Removed no-Origin auth bypass.
    // Previously, requests without an Origin header bypassed authentication entirely,
    // allowing any local process to call authenticated endpoints.
    // Now all requests require the session token regardless of Origin.

    console.warn('[Server] Rejected unauthorized request from origin:', req.headers.origin);
    res.status(401).json({ error: 'Unauthorized' });
  };

  // Serve the built renderer files over http://
  // (required for microphone access in Electron)
  const rendererPath = path.join(__dirname, '../renderer');
  app.use(express.static(rendererPath));

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok' });
  });

  app.post('/api/chat', authenticateToken, async (req, res) => {
    const { message, history = [] } = req.body;

    if (!message || typeof message !== 'string') {
      res.status(400).json({ error: 'Message is required and must be a string' });
      return;
    }

    // Crypto Sprint 9: Validate history array structure and size.
    // Without this, an attacker could send a malformed history (huge array, non-object
    // entries, missing role/content) to crash downstream handlers or exhaust memory.
    let validatedHistory: Array<{ role: string; content: string }>;
    try {
      validatedHistory = assertMessageArray(history, '/api/chat history');
    } catch (validationErr) {
      res.status(400).json({ error: 'Invalid history format: each entry must have role and content strings (max 500 messages)' });
      return;
    }

    try {
      const result = await handleClaude(message, validatedHistory);
      res.json(result);
    } catch (err: unknown) {
      // Crypto Sprint 9: Never leak raw error messages to the client.
      // Crypto Sprint 10: Sanitize log output — full error objects may contain API keys.
      console.error('[Server] Chat error:', err instanceof Error ? err.message : 'Unknown error');
      res.status(500).json({ error: 'Internal server error' });
    }
  });

  // --- Audio transcription via Gemini ---
  app.post('/api/transcribe', authenticateToken, async (req, res) => {
    const { audio, mimeType } = req.body;

    if (!audio) {
      res.status(400).json({ error: 'No audio data provided' });
      return;
    }

    try {
      const geminiKey = process.env.GEMINI_API_KEY;
      if (!geminiKey) {
        res.status(500).json({ error: 'GEMINI_API_KEY not configured' });
        return;
      }

      const genAI = new GoogleGenerativeAI(geminiKey);
      const model = genAI.getGenerativeModel({ model: 'gemini-2.0-flash' });

      const result = await model.generateContent([
        {
          inlineData: {
            mimeType: mimeType || 'audio/webm',
            data: audio,
          },
        },
        { text: 'Transcribe this audio exactly as spoken. Return only the transcription text, nothing else. If the audio is empty or unintelligible, return an empty string.' },
      ]);

      const transcript = result.response.text().trim();
      console.log('[Server] Transcribed:', transcript.slice(0, 80));
      res.json({ transcript });
    } catch (err: unknown) {
      // Crypto Sprint 9: Never leak raw error messages to the client.
      // Crypto Sprint 10: Sanitize log output — full error objects may contain API keys.
      console.error('[Server] Transcription error:', err instanceof Error ? err.message : 'Unknown error');
      res.status(500).json({ error: 'Transcription failed' });
    }
  });

  // --- Text-to-speech via Gemini (natural voice) ---
  app.post('/api/speak', authenticateToken, async (req, res) => {
    const { text } = req.body;

    if (!text) {
      res.status(400).json({ error: 'No text provided' });
      return;
    }

    try {
      const apiKey = process.env.GEMINI_API_KEY;
      if (!apiKey) {
        res.status(500).json({ error: 'GEMINI_API_KEY not configured' });
        return;
      }

      // Use Gemini REST API directly for audio generation
      const apiRes = await fetch(
        'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-goog-api-key': apiKey,
          },
          body: JSON.stringify({
            system_instruction: {
              parts: [{ text: 'You are a voice assistant. Read the user\'s text aloud naturally and expressively. Do not add, remove, or change any words. Just speak exactly what is provided.' }],
            },
            contents: [{ parts: [{ text }] }],
            generation_config: {
              response_modalities: ['AUDIO'],
              speech_config: {
                voice_config: {
                  prebuilt_voice_config: {
                    voice_name: 'Kore',
                  },
                },
              },
            },
          }),
        }
      );

      if (!apiRes.ok) {
        const errorText = await apiRes.text();
        console.error('[Server] Gemini TTS HTTP error:', apiRes.status, errorText.slice(0, 300));
        res.status(500).json({ error: `Gemini TTS failed (HTTP ${apiRes.status})` });
        return;
      }

      const data = await apiRes.json();

      // Check for Gemini API-level errors
      if (data.error) {
        // Crypto Sprint 9: Log full error internally but return generic message to client.
        console.error('[Server] Gemini TTS API error:', data.error.message || JSON.stringify(data.error).slice(0, 300));
        res.status(500).json({ error: 'TTS generation failed' });
        return;
      }

      const parts = data.candidates?.[0]?.content?.parts || [];
      const audioPart = parts.find((p: Record<string, any>) => p.inlineData?.mimeType?.startsWith('audio/'));

      if (audioPart) {
        res.json({
          audio: audioPart.inlineData.data,
          mimeType: audioPart.inlineData.mimeType,
        });
      } else {
        console.warn('[Server] No audio in Gemini TTS response:', JSON.stringify(data).slice(0, 500));
        res.status(500).json({ error: 'No audio generated' });
      }
    } catch (err: unknown) {
      // Crypto Sprint 9: Never leak raw error messages to the client.
      // Crypto Sprint 10: Sanitize log output — full error objects may contain API keys.
      console.error('[Server] TTS error:', err instanceof Error ? err.message : 'Unknown error');
      res.status(500).json({ error: 'TTS failed' });
    }
  });

  return new Promise((resolve, reject) => {
    // Bind to 127.0.0.1 only — never expose to network
    const server = app.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const port = typeof addr === 'object' && addr ? addr.port : 3333;
      resolve(port);
    });

    // Handle bind errors (e.g., address in use)
    server.on('error', (err: Error) => {
      // Crypto Sprint 17: Sanitize error output.
      console.error('[Server] Failed to bind:', err instanceof Error ? err.message : 'Unknown error');
      reject(err);
    });
  });
}

// ── Reusable Claude Tool Loop ─────────────────────────────────────────
// Extracted so both /api/chat (local Electron UI) and the gateway manager
// can invoke Claude with tool-use iteration using their own filtered tool
// sets, system prompts, and iteration caps.

export interface ClaudeToolLoopOptions {
  systemPrompt: string;
  messages: Anthropic.MessageParam[];
  tools: Anthropic.Tool[];
  /** Max tool-use iterations before breaking. Defaults to 25 (local tier). */
  maxIterations?: number;
  /** Tool names routed to browser automation layer */
  browserToolNames?: Set<string>;
  /** Tool names routed to connector registry */
  connectorToolNames?: Set<string>;
}

export interface ClaudeToolLoopResult {
  response: string;
  model: string;
  toolCalls: number;
}

/**
 * Run Claude with iterative tool execution.
 *
 * Handles the Anthropic API call → tool-use loop → final text response.
 * Tool routing: browserToolNames → executeBrowserTool,
 *               connectorToolNames → connectorRegistry,
 *               everything else → MCP client.
 *
 * Used by:
 * - handleClaude() for local /api/chat requests
 * - GatewayManager for multi-channel inbound messages
 */
export async function runClaudeToolLoop(
  options: ClaudeToolLoopOptions
): Promise<ClaudeToolLoopResult> {
  const {
    systemPrompt,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
  } = options;

  // cLaw Security Fix (CRITICAL-001): Architecturally strip side-effect tools in safe mode.
  // This replaces the prompt-only safe mode restriction with a hard tool surface reduction.
  const tools = filterToolsForSafeMode(options.tools);

  // Clone messages to avoid mutating the caller's array
  const messages = [...options.messages];

  // TODO: Refactor the Anthropic branch below to use llmClient.complete() from llm-client.ts.
  // This is deferred because the tool loop uses Anthropic-specific types throughout
  // (Anthropic.ContentBlockParam, Anthropic.ToolResultBlockParam, etc.) that require
  // careful conversion to the unified ChatMessage/ToolCall types. The OpenRouter branch
  // should also be migrated to llmClient once the Anthropic branch is proven stable.
  // See: llm-client.ts for the unified LLMRequest/LLMResponse/ChatMessage types.

  // Check if we should use OpenRouter
  const provider = settingsManager.getPreferredProvider();
  if (provider === 'openrouter' && openRouter.isConfigured()) {
    return runOpenRouterToolLoop({ ...options, messages });
  }

  // Route to local LLM provider (Ollama, TGI, vLLM, HuggingFace cloud)
  if (provider === 'local') {
    return runLocalToolLoop({ ...options, tools, messages });
  }

  // Default: direct Anthropic SDK
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return {
      response: 'Anthropic API key not configured. Please set it in Settings.',
      model: 'claude-opus-4-6',
      toolCalls: 0,
    };
  }

  const anthropic = new Anthropic({ apiKey });

  let response = await anthropic.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 4096,
    system: systemPrompt,
    messages,
    tools: tools.length > 0 ? tools : undefined,
  });

  // Tool-use loop (capped by maxIterations to prevent runaway)
  let toolIterations = 0;
  while (response.stop_reason === 'tool_use') {
    toolIterations++;
    if (toolIterations > maxIterations) {
      console.warn(`[Server] Tool-use loop exceeded ${maxIterations} iterations — breaking`);
      return {
        response: 'I hit my tool-use limit for this request. Please try breaking your request into smaller steps.',
        model: 'claude-opus-4-6',
        toolCalls: toolIterations,
      };
    }

    const assistantContent = response.content;
    const toolUseBlocks = assistantContent.filter(
      (b): b is Anthropic.ContentBlockParam & { type: 'tool_use'; id: string; name: string; input: unknown } =>
        b.type === 'tool_use'
    );

    const toolResults: Anthropic.ToolResultBlockParam[] = [];
    for (const toolUse of toolUseBlocks) {
      try {
        let result: unknown;
        if (browserToolNames.has(toolUse.name)) {
          result = await executeBrowserTool(toolUse.name, toolUse.input as Record<string, unknown>);
        } else if (connectorToolNames.has(toolUse.name)) {
          const connResult = await connectorRegistry.executeTool(
            toolUse.name,
            toolUse.input as Record<string, unknown>
          );
          result = connResult.result || connResult.error || '(no output)';
        } else {
          result = await mcpClient.callTool(toolUse.name, toolUse.input as Record<string, unknown>);
        }

        // Handle screenshot — send as image block to Claude
        if (toolUse.name === 'browser_screenshot' && typeof result === 'string' && result.length > 500) {
          toolResults.push({
            type: 'tool_result',
            tool_use_id: toolUse.id,
            content: [
              {
                type: 'image',
                source: {
                  type: 'base64',
                  media_type: 'image/jpeg',
                  data: result as string,
                },
              },
            ] as any,
          });
        } else {
          toolResults.push({
            type: 'tool_result',
            tool_use_id: toolUse.id,
            content: typeof result === 'string' ? result : JSON.stringify(result),
          });
        }
      } catch (err: unknown) {
        const errMsg = err instanceof Error ? err.message : String(err);
        toolResults.push({
          type: 'tool_result',
          tool_use_id: toolUse.id,
          content: `Error: ${errMsg}`,
          is_error: true,
        });
      }
    }

    messages.push({ role: 'assistant', content: assistantContent as any });
    messages.push({ role: 'user', content: toolResults });

    response = await anthropic.messages.create({
      model: 'claude-opus-4-6',
      max_tokens: 4096,
      system: systemPrompt,
      messages,
      tools: tools.length > 0 ? tools : undefined,
    });
  }

  const textBlock = response.content.find((b) => b.type === 'text');
  return {
    response: textBlock && 'text' in textBlock ? textBlock.text : 'No response generated.',
    model: 'claude-opus-4-6',
    toolCalls: toolIterations,
  };
}

/**
 * Local LLM tool loop — routes through the unified LLMClient abstraction.
 *
 * Supports any OpenAI-compatible backend: Ollama, TGI, vLLM, HuggingFace Inference API.
 * Converts Anthropic-typed inputs → unified ChatMessage/ToolDefinition types,
 * then delegates to llmClient.complete() which handles provider selection and
 * automatic fallback if the local endpoint is unreachable.
 */
async function runLocalToolLoop(
  options: ClaudeToolLoopOptions
): Promise<ClaudeToolLoopResult> {
  const {
    systemPrompt,
    messages: anthropicMessages,
    tools: anthropicTools,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
  } = options;

  // Convert Anthropic tool format → unified ToolDefinition format
  const tools: ToolDefinition[] = anthropicTools.map((t) => ({
    name: t.name,
    description: t.description || '',
    input_schema: t.input_schema as Record<string, unknown>,
  }));

  // Convert Anthropic messages → unified ChatMessage format
  const messages: ChatMessage[] = anthropicMessages.map((m) => ({
    role: m.role as 'user' | 'assistant',
    content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
  }));

  // Tool executor — routes to browser, connector, or MCP based on tool name
  async function executeToolCall(
    name: string,
    args: Record<string, unknown>
  ): Promise<string> {
    let result: unknown;
    if (browserToolNames.has(name)) {
      result = await executeBrowserTool(name, args);
    } else if (connectorToolNames.has(name)) {
      const connResult = await connectorRegistry.executeTool(name, args);
      result = connResult.result || connResult.error || '(no output)';
    } else {
      result = await mcpClient.callTool(name, args);
    }
    return typeof result === 'string' ? result : JSON.stringify(result);
  }

  let response = await llmClient.complete(
    {
      messages,
      systemPrompt,
      tools: tools.length > 0 ? tools : undefined,
      maxTokens: 4096,
    },
    'local'
  );

  // Log which provider actually served (may fall back from local → anthropic)
  if (response.provider !== 'local') {
    console.warn(
      `[Server] Local provider unavailable — fell back to ${response.provider} (${response.model})`
    );
  }

  // Tool-use loop (same pattern as Anthropic/OpenRouter branches)
  let toolIterations = 0;
  while (response.stopReason === 'tool_use' && response.toolCalls.length > 0) {
    toolIterations++;
    if (toolIterations > maxIterations) {
      console.warn(`[Server] Local tool loop exceeded ${maxIterations} iterations — breaking`);
      return {
        response:
          'I hit my tool-use limit for this request. Please try breaking your request into smaller steps.',
        model: response.model,
        toolCalls: toolIterations,
      };
    }

    // Add assistant message with tool calls to conversation
    messages.push({
      role: 'assistant',
      content: response.content || null,
      tool_calls: response.toolCalls,
    });

    // Execute each tool call and add results
    for (const tc of response.toolCalls) {
      try {
        const resultContent = await executeToolCall(
          tc.name,
          tc.input as Record<string, unknown>
        );
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: resultContent,
        });
      } catch (err: unknown) {
        const errMsg = err instanceof Error ? err.message : String(err);
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: `Error: ${errMsg}`,
        });
      }
    }

    response = await llmClient.complete(
      {
        messages,
        systemPrompt,
        tools: tools.length > 0 ? tools : undefined,
        maxTokens: 4096,
      },
      'local'
    );
  }

  return {
    response: response.content || 'No response generated.',
    model: response.model,
    toolCalls: toolIterations,
  };
}

/**
 * OpenRouter-based tool loop — same behavior as Anthropic but via OpenRouter API.
 * Supports any model available through OpenRouter (200+ models).
 */
async function runOpenRouterToolLoop(
  options: ClaudeToolLoopOptions
): Promise<ClaudeToolLoopResult> {
  const {
    systemPrompt,
    messages: anthropicMessages,
    tools: anthropicTools,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
  } = options;

  const model = settingsManager.getOpenrouterModel();

  // Convert Anthropic tool format → OpenRouter tool format
  const orTools: OpenRouterTool[] = anthropicTools.map((t) => ({
    type: 'function' as const,
    function: {
      name: t.name,
      description: t.description || '',
      parameters: t.input_schema as Record<string, unknown>,
    },
  }));

  // Helper to execute a tool by name
  async function executeTool(name: string, args: Record<string, unknown>): Promise<string> {
    let result: unknown;
    if (browserToolNames.has(name)) {
      result = await executeBrowserTool(name, args);
    } else if (connectorToolNames.has(name)) {
      const connResult = await connectorRegistry.executeTool(name, args);
      result = connResult.result || connResult.error || '(no output)';
    } else {
      result = await mcpClient.callTool(name, args);
    }
    return typeof result === 'string' ? result : JSON.stringify(result);
  }

  // Convert Anthropic messages → OpenRouter messages
  const orMessages: OpenRouterMessage[] = anthropicMessages.map((m) => ({
    role: m.role as 'user' | 'assistant',
    content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
  }));

  const result = await openRouter.chatWithTools({
    model,
    systemPrompt,
    messages: orMessages,
    tools: orTools,
    executeTool,
    maxIterations,
  });

  return {
    response: result.text,
    model: result.model,
    toolCalls: result.toolCalls,
  };
}

// --- Claude handler (local /api/chat) ---
// Thin wrapper: gathers tools, builds system prompt, delegates to runClaudeToolLoop().
async function handleClaude(
  message: string,
  history: Array<{ role: string; content: string }>
) {
  const systemPrompt = await buildSystemPrompt();

  const messages: Anthropic.MessageParam[] = [
    ...history.map((h) => ({
      role: h.role as 'user' | 'assistant',
      content: h.content,
    })),
    { role: 'user', content: message },
  ];

  // Gather MCP tools (Desktop Commander etc.)
  let mcpTools: Anthropic.Tool[] = [];
  try {
    const raw = await mcpClient.listTools();
    mcpTools = raw.map((t) => ({
      name: t.name,
      description: t.description || '',
      input_schema: t.inputSchema || { type: 'object' as const, properties: {} },
    })) as Anthropic.Tool[];
  } catch {
    // MCP not connected
  }

  // Gather connector tools (PowerShell, VS Code, Git, Firecrawl, World Monitor, etc.)
  let connectorTools: Anthropic.Tool[] = [];
  try {
    const rawConnectorTools = connectorRegistry.getAllTools();
    connectorTools = rawConnectorTools.map((t) => ({
      name: t.name,
      description: t.description || '',
      input_schema: {
        type: 'object' as const,
        properties: t.parameters.properties || {},
        ...(t.parameters.required ? { required: t.parameters.required } : {}),
      },
    })) as Anthropic.Tool[];
    if (connectorTools.length > 0) {
      console.log(`[Server] Claude has access to ${connectorTools.length} connector tools`);
    }
  } catch (err: unknown) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.warn('[Server] Failed to load connector tools:', errMsg);
  }

  // Combine MCP + browser + connector tools (local tier = ALL tools)
  const allTools: Anthropic.Tool[] = [
    ...mcpTools,
    ...browserToolDefs as Anthropic.Tool[],
    ...connectorTools,
  ];

  return runClaudeToolLoop({
    systemPrompt,
    messages,
    tools: allTools,
    maxIterations: 25,
    browserToolNames: new Set(browserToolDefs.map((t) => t.name)),
    connectorToolNames: new Set(connectorTools.map((t) => t.name)),
  });
}
