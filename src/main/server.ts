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

  // Security headers
  app.use((_req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('X-XSS-Protection', '1; mode=block');
    res.setHeader('Referrer-Policy', 'no-referrer');
    next();
  });

  // Session token authentication middleware for API routes
  const authenticateToken: express.RequestHandler = (req, res, next) => {
    const authHeader = req.headers.authorization;
    const token = authHeader?.startsWith('Bearer ') ? authHeader.slice(7) : null;

    // Also accept token from query param (for SSE/streaming endpoints)
    const queryToken = req.query.token as string | undefined;

    if (token === sessionToken || queryToken === sessionToken) {
      return next();
    }

    // Allow unauthenticated access from same-origin (no origin = Electron renderer).
    // Security model: server binds to 127.0.0.1 only (never exposed to network).
    // Same-origin requests from the Electron renderer don't carry an Origin header.
    // Risk: other local processes can also call without Origin. Acceptable for a
    // desktop app — local processes are in the same trust boundary as the user.
    if (!req.headers.origin) {
      return next();
    }

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

    try {
      const result = await handleClaude(message, history);
      res.json(result);
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : 'Internal server error';
      console.error('[Server] Chat error:', err);
      res.status(500).json({ error: errMsg });
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
      const errMsg = err instanceof Error ? err.message : 'Transcription failed';
      console.error('[Server] Transcription error:', err);
      res.status(500).json({ error: errMsg });
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
        console.error('[Server] Gemini TTS API error:', data.error.message || JSON.stringify(data.error).slice(0, 300));
        res.status(500).json({ error: data.error.message || 'Gemini TTS error' });
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
      const errMsg = err instanceof Error ? err.message : 'TTS failed';
      console.error('[Server] TTS error:', err);
      res.status(500).json({ error: errMsg });
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
      console.error('[Server] Failed to bind:', err);
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
    tools,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
  } = options;

  // Clone messages to avoid mutating the caller's array
  const messages = [...options.messages];

  // Check if we should use OpenRouter
  const provider = settingsManager.getPreferredProvider();
  if (provider === 'openrouter' && openRouter.isConfigured()) {
    return runOpenRouterToolLoop({ ...options, messages });
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
