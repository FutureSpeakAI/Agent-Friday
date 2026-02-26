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

  // Serve the built renderer files over http://
  // (required for microphone access in Electron)
  const rendererPath = path.join(__dirname, '../renderer');
  app.use(express.static(rendererPath));

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok' });
  });

  app.post('/api/chat', async (req, res) => {
    const { message, history = [] } = req.body;

    try {
      const result = await handleClaude(message, history);
      res.json(result);
    } catch (err: any) {
      console.error('[EVE] Chat error:', err);
      res.status(500).json({ error: err.message || 'Internal server error' });
    }
  });

  // --- Audio transcription via Gemini ---
  app.post('/api/transcribe', async (req, res) => {
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
      console.log('[EVE] Transcribed:', transcript);
      res.json({ transcript });
    } catch (err: any) {
      console.error('[EVE] Transcription error:', err);
      res.status(500).json({ error: err.message || 'Transcription failed' });
    }
  });

  // --- Text-to-speech via Gemini (natural voice) ---
  app.post('/api/speak', async (req, res) => {
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

      const data = await apiRes.json();
      const parts = data.candidates?.[0]?.content?.parts || [];
      const audioPart = parts.find((p: any) => p.inlineData?.mimeType?.startsWith('audio/'));

      if (audioPart) {
        res.json({
          audio: audioPart.inlineData.data,
          mimeType: audioPart.inlineData.mimeType,
        });
      } else {
        console.warn('[EVE] No audio in Gemini TTS response:', JSON.stringify(data).slice(0, 500));
        res.status(500).json({ error: 'No audio generated' });
      }
    } catch (err: any) {
      console.error('[EVE] TTS error:', err);
      res.status(500).json({ error: err.message || 'TTS failed' });
    }
  });

  return new Promise((resolve) => {
    // Bind to 127.0.0.1 only — never expose to network
    const server = app.listen(0, '127.0.0.1', () => {
      const addr = server.address();
      const port = typeof addr === 'object' && addr ? addr.port : 3333;
      resolve(port);
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
    messages,
    tools,
    maxIterations = 25,
    browserToolNames = new Set<string>(),
    connectorToolNames = new Set<string>(),
  } = options;

  const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

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
          // Route to connector registry (PowerShell, VS Code, Git, Firecrawl, etc.)
          const connResult = await connectorRegistry.executeTool(
            toolUse.name,
            toolUse.input as Record<string, unknown>
          );
          result = connResult.result || connResult.error || '(no output)';
        } else {
          // Fall through to MCP (Desktop Commander, etc.)
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
      } catch (err: any) {
        toolResults.push({
          type: 'tool_result',
          tool_use_id: toolUse.id,
          content: `Error: ${err.message}`,
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
    response: textBlock ? (textBlock as any).text : 'No response generated.',
    model: 'claude-opus-4-6',
    toolCalls: toolIterations,
  };
}

// --- Claude Opus 4.6 handler (local /api/chat) ---
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
  } catch (err: any) {
    console.warn('[Server] Failed to load connector tools for Claude:', err?.message);
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
