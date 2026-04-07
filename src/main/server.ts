import express from 'express';
import cors from 'cors';
import crypto from 'crypto';
import path from 'path';
import dotenv from 'dotenv';
import { GoogleGenerativeAI } from '@google/generative-ai';
import { mcpClient } from './mcp-client';
import { buildSystemPrompt } from './personality';
import { browserToolDefs, executeBrowserTool } from './browser';
import { connectorRegistry } from './connectors/registry';
import { settingsManager } from './settings';
import { llmClient, type ChatMessage, type ToolDefinition, type LLMResponse } from './llm-client';
import { privacyShield } from './privacy-shield';
import { integrityManager } from './integrity';
import { assertMessageArray } from './ipc/validate';
import { memoryManager } from './memory';
import { episodicMemory } from './episodic-memory';
import { personalityCalibration } from './personality-calibration';
import { encode } from 'gpt-tokenizer';
import type { ProviderName } from './intelligence-router';

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

dotenv.config({ override: true });

// ── Experience Loop: Text chat session tracking for episodic memory ──────────
// A "session" is a burst of conversation. When the user goes quiet for
// SESSION_TIMEOUT_MS, we seal the session into an episode.
const SESSION_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes of silence = new session

interface TextChatSession {
  startTime: number;
  lastActivity: number;
  transcript: Array<{ role: string; text: string }>;
}

let activeTextSession: TextChatSession | null = null;

/** Flush the current text session into an episode (if it has substance). */
async function flushTextSession(): Promise<void> {
  if (!activeTextSession || activeTextSession.transcript.length < 2) {
    activeTextSession = null;
    return;
  }
  try {
    const episode = await episodicMemory.createFromSession(
      activeTextSession.transcript,
      activeTextSession.startTime,
      Date.now()
    );
    if (episode) {
      console.log(`[Server/Experience] Episode created: "${episode.summary.slice(0, 60)}" (${activeTextSession.transcript.length} turns)`);
    }
  } catch (err) {
    console.error('[Server/Experience] Episode creation failed:', err instanceof Error ? err.message : 'Unknown error');
  }
  activeTextSession = null;
}

// Export so index.ts can flush on app quit
export { flushTextSession };

/** Per-session token — only the Electron main process knows this. */
let sessionToken = '';
export function getSessionToken(): string { return sessionToken; }

// ── Context window management ────────────────────────────────────────────
// Trim conversation history to fit within model context limits.
// Keeps the most recent messages, dropping oldest first.
// Reserve budget for system prompt (~4k) + new message (~1k) + response (~4k).
const MAX_HISTORY_TOKENS = 90_000; // ~90k leaves room in a 128k context window

function trimHistoryToFit(
  history: Array<{ role: string; content: string }>
): Array<{ role: string; content: string }> {
  if (history.length === 0) return history;

  // Fast path: estimate with char count first (1 token ≈ 4 chars)
  const totalChars = history.reduce((sum, m) => sum + m.content.length, 0);
  if (totalChars / 4 < MAX_HISTORY_TOKENS) return history;

  // Slow path: actual tokenization, drop oldest messages until under budget
  let tokenCount = 0;
  const tokenCounts: number[] = history.map((m) => encode(m.content).length);
  const totalTokens = tokenCounts.reduce((a, b) => a + b, 0);

  if (totalTokens <= MAX_HISTORY_TOKENS) return history;

  // Walk backwards from most recent, accumulating until budget exceeded
  let keepFrom = history.length;
  tokenCount = 0;
  for (let i = history.length - 1; i >= 0; i--) {
    if (tokenCount + tokenCounts[i] > MAX_HISTORY_TOKENS) break;
    tokenCount += tokenCounts[i];
    keepFrom = i;
  }

  // Ensure we don't start mid-pair (assistant without preceding user)
  if (keepFrom < history.length && history[keepFrom].role === 'assistant') {
    keepFrom++;
  }
  if (keepFrom >= history.length) return [];

  const trimmed = history.slice(keepFrom);
  console.log(`[Server/Context] Trimmed history: ${history.length} → ${trimmed.length} messages (${totalTokens} → ${tokenCount} tokens)`);
  return trimmed;
}

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
      const requestStartTime = Date.now();
      const result = await handleClaude(message, validatedHistory);
      res.json(result);

      // ── Experience Loop: fire-and-forget post-response hooks ──────────
      // These run AFTER the response is sent, so they don't add latency.

      // 1.5: Personality calibration — detect implicit signals from user messages
      const responseTimeMs = Date.now() - requestStartTime;
      try {
        personalityCalibration.processUserMessage(message, responseTimeMs);
      } catch (calErr) {
        console.error('[Server/Experience] Personality calibration error:', calErr instanceof Error ? calErr.message : 'Unknown');
      }

      // 1.1: Memory extraction — build full conversation and extract memories
      const fullConversation = [
        ...validatedHistory,
        { role: 'user', content: message },
        { role: 'assistant', content: result.response },
      ];
      memoryManager.extractMemories(fullConversation).catch((memErr) => {
        console.error('[Server/Experience] Memory extraction error:', memErr instanceof Error ? memErr.message : 'Unknown');
      });

      // 1.2: Episode tracking — maintain session, flush on timeout gaps
      const now = Date.now();
      if (activeTextSession && (now - activeTextSession.lastActivity > SESSION_TIMEOUT_MS)) {
        // Gap detected — seal the old session, start fresh
        flushTextSession().catch(() => {}); // fire-and-forget
        activeTextSession = null;
      }

      if (!activeTextSession) {
        activeTextSession = { startTime: now, lastActivity: now, transcript: [] };
      }

      activeTextSession.lastActivity = now;
      activeTextSession.transcript.push(
        { role: 'user', text: message },
        { role: 'assistant', text: result.response }
      );

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
      const geminiKey = settingsManager.getGeminiApiKey();
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
      const apiKey = settingsManager.getGeminiApiKey();
      if (!apiKey) {
        res.status(500).json({ error: 'GEMINI_API_KEY not configured' });
        return;
      }

      // Privacy Shield: scrub user text before sending to Google cloud API.
      // TTS reads aloud what the user provides — may contain names, addresses, etc.
      const ttsText = privacyShield.isEnabled()
        ? privacyShield.scrub(text).text
        : text;

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
            contents: [{ parts: [{ text: ttsText }] }],
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

// ── Unified Tool Loop ─────────────────────────────────────────────────
// Single provider-agnostic tool loop that routes through llmClient.
// Replaces the former three-branch implementation (Anthropic direct,
// local LLMClient, OpenRouter) with a unified path.
//
// All callers pass unified ChatMessage[] and ToolDefinition[] types.
// Provider selection is handled by llmClient based on user preference.

export interface ToolLoopEvent {
  type: 'turn_start' | 'tool_start' | 'tool_end' | 'turn_end' | 'loop_end';
  /** Tool name (for tool_start/tool_end events) */
  tool?: string;
  /** Tool call ID */
  toolCallId?: string;
  /** Content sent to LLM context */
  content?: string;
  /** UI-only details (not sent to LLM — dual content/details pattern) */
  details?: unknown;
  /** Current iteration number */
  iteration?: number;
  /** Timestamp */
  timestamp: number;
}

export interface ClaudeToolLoopOptions {
  systemPrompt: string;
  messages: ChatMessage[];
  tools: ToolDefinition[];
  /** Max tool-use iterations before breaking. Defaults to 25 (local tier). */
  maxIterations?: number;
  /** Tool names routed to browser automation layer */
  browserToolNames?: Set<string>;
  /** Tool names routed to connector registry */
  connectorToolNames?: Set<string>;
  /** Explicit provider to use (omit for user preference) */
  provider?: ProviderName;
  /** Explicit model to use */
  model?: string;
  /** Event callback for UI streaming (dual content/details pattern) */
  onEvent?: (event: ToolLoopEvent) => void;
}

export interface ClaudeToolLoopResult {
  response: string;
  model: string;
  provider: ProviderName;
  toolCalls: number;
  /** Token usage for cost tracking */
  usage: { inputTokens: number; outputTokens: number };
  /** Total latency in milliseconds */
  latencyMs: number;
}

/**
 * Unified tool loop — provider-agnostic iterative tool execution.
 *
 * Routes through llmClient.complete() which handles provider selection,
 * fallback chains, and Privacy Shield (PII scrubbing for cloud providers).
 *
 * Tool routing: browserToolNames → executeBrowserTool,
 *               connectorToolNames → connectorRegistry,
 *               everything else → MCP client.
 *
 * Used by:
 * - handleClaude() for local /api/chat requests
 * - GatewayManager for multi-channel inbound messages
 * - git-review.ts, git-analyzer.ts for code analysis
 */
export async function runClaudeToolLoop(
  options: ClaudeToolLoopOptions
): Promise<ClaudeToolLoopResult> {
  const {
    systemPrompt,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
    onEvent,
  } = options;

  const startTime = Date.now();

  // cLaw Security Fix (CRITICAL-001): Architecturally strip side-effect tools in safe mode.
  const tools = filterToolsForSafeMode(options.tools);

  // Clone messages to avoid mutating the caller's array
  const messages: ChatMessage[] = [...options.messages];

  // Resolve provider: explicit option > user preference > default
  const resolvedProvider = options.provider ?? (settingsManager.getPreferredProvider() as ProviderName);

  // Emit helper
  const emit = (event: Omit<ToolLoopEvent, 'timestamp'>) =>
    onEvent?.({ ...event, timestamp: Date.now() });

  // Accumulate usage across iterations
  let totalInputTokens = 0;
  let totalOutputTokens = 0;

  // Tool executor — routes to browser, connector, or MCP based on tool name
  async function executeToolCall(
    name: string,
    args: Record<string, unknown>
  ): Promise<{ content: string; details?: unknown }> {
    let result: unknown;
    if (browserToolNames.has(name)) {
      result = await executeBrowserTool(name, args);
    } else if (connectorToolNames.has(name)) {
      const connResult = await connectorRegistry.executeTool(name, args);
      result = connResult.result || connResult.error || '(no output)';
    } else {
      result = await mcpClient.callTool(name, args);
    }

    const content = typeof result === 'string' ? result : JSON.stringify(result);

    // Dual content/details: truncate large tool results for LLM context,
    // but pass full result as details for UI rendering
    const MAX_TOOL_RESULT_TOKENS = 4000;
    const truncated = content.length > MAX_TOOL_RESULT_TOKENS * 4
      ? content.slice(0, MAX_TOOL_RESULT_TOKENS * 4) + '\n\n[... truncated for context window ...]'
      : content;

    return { content: truncated, details: content !== truncated ? { fullResult: content } : undefined };
  }

  // Initial LLM call
  emit({ type: 'turn_start', iteration: 0 });

  let response: LLMResponse = await llmClient.complete(
    {
      messages,
      systemPrompt,
      model: options.model,
      tools: tools.length > 0 ? tools : undefined,
      maxTokens: 4096,
    },
    resolvedProvider
  );

  totalInputTokens += response.usage.inputTokens;
  totalOutputTokens += response.usage.outputTokens;

  emit({ type: 'turn_end', iteration: 0 });

  // Tool-use loop (capped by maxIterations to prevent runaway)
  let toolIterations = 0;
  while (response.stopReason === 'tool_use' && response.toolCalls.length > 0) {
    toolIterations++;
    if (toolIterations > maxIterations) {
      console.warn(`[Server] Tool loop exceeded ${maxIterations} iterations — breaking`);
      emit({ type: 'loop_end', iteration: toolIterations });
      return {
        response: 'I hit my tool-use limit for this request. Please try breaking your request into smaller steps.',
        model: response.model,
        provider: response.provider,
        toolCalls: toolIterations,
        usage: { inputTokens: totalInputTokens, outputTokens: totalOutputTokens },
        latencyMs: Date.now() - startTime,
      };
    }

    emit({ type: 'turn_start', iteration: toolIterations });

    // Add assistant message with tool calls to conversation
    messages.push({
      role: 'assistant',
      content: response.content || null,
      tool_calls: response.toolCalls,
    });

    // Execute each tool call and add results
    for (const tc of response.toolCalls) {
      emit({ type: 'tool_start', tool: tc.name, toolCallId: tc.id, iteration: toolIterations });

      try {
        const { content, details } = await executeToolCall(
          tc.name,
          tc.input as Record<string, unknown>
        );
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content,
        });
        emit({ type: 'tool_end', tool: tc.name, toolCallId: tc.id, content, details, iteration: toolIterations });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: `Error: ${msg}`,
        });
        emit({ type: 'tool_end', tool: tc.name, toolCallId: tc.id, content: `Error: ${msg}`, iteration: toolIterations });
      }
    }

    response = await llmClient.complete(
      {
        messages,
        systemPrompt,
        model: options.model,
        tools: tools.length > 0 ? tools : undefined,
        maxTokens: 4096,
      },
      resolvedProvider
    );

    totalInputTokens += response.usage.inputTokens;
    totalOutputTokens += response.usage.outputTokens;

    emit({ type: 'turn_end', iteration: toolIterations });
  }

  emit({ type: 'loop_end', iteration: toolIterations });

  return {
    response: response.content || 'No response generated.',
    model: response.model,
    provider: response.provider,
    toolCalls: toolIterations,
    usage: { inputTokens: totalInputTokens, outputTokens: totalOutputTokens },
    latencyMs: Date.now() - startTime,
  };
}

// --- Claude handler (local /api/chat) ---
// Thin wrapper: gathers tools, builds system prompt, delegates to runClaudeToolLoop().
async function handleClaude(
  message: string,
  history: Array<{ role: string; content: string }>
) {
  const systemPrompt = await buildSystemPrompt();

  // Phase 1.4: Trim history to fit context window before sending to LLM
  const trimmedHistory = trimHistoryToFit(history);

  const messages: ChatMessage[] = [
    ...trimmedHistory.map((h) => ({
      role: h.role as 'user' | 'assistant',
      content: h.content,
    })),
    { role: 'user' as const, content: message },
  ];

  // Gather MCP tools (Desktop Commander etc.)
  let mcpTools: ToolDefinition[] = [];
  try {
    const raw = await mcpClient.listTools();
    mcpTools = raw.map((t) => ({
      name: t.name,
      description: t.description || '',
      input_schema: (t.inputSchema || { type: 'object', properties: {} }) as Record<string, unknown>,
    }));
  } catch {
    // MCP not connected
  }

  // Gather connector tools (PowerShell, VS Code, Git, Firecrawl, World Monitor, etc.)
  let connectorTools: ToolDefinition[] = [];
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
    }));
    if (connectorTools.length > 0) {
      console.log(`[Server] LLM has access to ${connectorTools.length} connector tools`);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn('[Server] Failed to load connector tools:', msg);
  }

  // Gather browser tool defs in unified format
  const unifiedBrowserTools: ToolDefinition[] = browserToolDefs.map((t) => ({
    name: t.name,
    description: t.description || '',
    input_schema: (t.input_schema || { type: 'object', properties: {} }) as Record<string, unknown>,
  }));

  // Combine MCP + browser + connector tools (local tier = ALL tools)
  const allTools: ToolDefinition[] = [
    ...mcpTools,
    ...unifiedBrowserTools,
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
