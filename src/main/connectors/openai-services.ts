/**
 * OpenAI Services Connector — Image generation, reasoning, transcription, and embeddings.
 *
 * Provides specialized AI capabilities that complement the core Gemini + Claude stack:
 *   - generate_image:     Create images via DALL-E 3
 *   - reason_through:     Complex multi-step reasoning via o3
 *   - transcribe_audio:   Speech-to-text via Whisper
 *   - generate_embedding: Semantic embeddings for memory search (internal use)
 *
 * All calls go through the OpenAI API (https://api.openai.com/v1).
 * Authentication via Bearer token from settings.
 *
 * Exports: TOOLS, execute, detect
 */

import { ToolDeclaration, ToolResult } from './registry';
import { settingsManager } from '../settings';
import * as https from 'https';
import * as fs from 'fs';
import * as path from 'path';
import { app } from 'electron';

// ── Constants ────────────────────────────────────────────────────────

const API_HOST = 'api.openai.com';
const REQUEST_TIMEOUT_MS = 120_000;
const REASONING_TIMEOUT_MS = 300_000; // 5 min for o3 reasoning
const MAX_RESPONSE_CHARS = 20_000;

// ── Helpers ──────────────────────────────────────────────────────────

function truncate(text: string, maxLen: number): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + `\n\n…[truncated — ${text.length} chars total]`;
}

function ok(text: string): ToolResult {
  return { result: text.trim() || '(no output)' };
}

function fail(msg: string): ToolResult {
  return { error: msg };
}

function getApiKey(): string {
  return settingsManager.getOpenaiApiKey();
}

/**
 * Make an HTTPS request to the OpenAI API.
 */
function apiRequest(
  apiPath: string,
  body: Record<string, unknown>,
  timeoutMs: number = REQUEST_TIMEOUT_MS
): Promise<{ status: number; data: any }> {
  return new Promise((resolve, reject) => {
    const apiKey = getApiKey();
    if (!apiKey) {
      reject(new Error('OpenAI API key not configured. Set it in settings.'));
      return;
    }

    const postData = JSON.stringify(body);

    const options: https.RequestOptions = {
      hostname: API_HOST,
      port: 443,
      path: `/v1${apiPath}`,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData),
      },
      timeout: timeoutMs,
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve({ status: res.statusCode || 0, data: parsed });
        } catch {
          resolve({ status: res.statusCode || 0, data: { raw: data } });
        }
      });
    });

    req.on('error', (err) => reject(new Error(`OpenAI request failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('OpenAI request timed out')); });

    req.write(postData);
    req.end();
  });
}

// ── Tool implementations ─────────────────────────────────────────────

async function generateImage(args: Record<string, unknown>): Promise<string> {
  const prompt = typeof args.prompt === 'string' ? args.prompt : '';
  if (!prompt) return 'ERROR: image prompt is required.';

  const size = typeof args.size === 'string' && ['1024x1024', '1792x1024', '1024x1792'].includes(args.size)
    ? args.size
    : '1024x1024';
  const quality = args.quality === 'hd' ? 'hd' : 'standard';
  const style = args.style === 'natural' ? 'natural' : 'vivid';

  const { status, data } = await apiRequest('/images/generations', {
    model: 'dall-e-3',
    prompt,
    n: 1,
    size,
    quality,
    style,
    response_format: 'url',
  });

  if (status === 401) return 'ERROR: OpenAI API key is invalid. Check your settings.';
  if (status === 429) return 'ERROR: OpenAI rate limit exceeded. Try again in a moment.';
  if (status === 400) {
    return `ERROR: Image generation rejected: ${data.error?.message || 'Content policy violation or invalid prompt'}`;
  }
  if (status !== 200) {
    return `ERROR: DALL-E 3 failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const imageUrl = data.data?.[0]?.url;
  const revisedPrompt = data.data?.[0]?.revised_prompt;

  if (!imageUrl) return 'ERROR: No image URL returned from DALL-E 3.';

  // Download the image to temp storage
  const tempDir = path.join(app.getPath('temp'), 'agent-friday-images');
  try {
    if (!fs.existsSync(tempDir)) {
      fs.mkdirSync(tempDir, { recursive: true });
    }
  } catch {
    // If we can't create temp dir, just return the URL
  }

  const filename = `dalle3-${Date.now()}.png`;
  const localPath = path.join(tempDir, filename);

  // Async download the image
  try {
    await downloadFile(imageUrl, localPath);
  } catch {
    // If download fails, still return the URL
    return `## Image Generated\n\n**Prompt:** ${prompt}\n${revisedPrompt ? `**DALL-E revised prompt:** ${revisedPrompt}\n` : ''}\n**URL:** ${imageUrl}\n\n(Image URL expires in ~1 hour. Download failed — use the URL directly.)`;
  }

  return `## Image Generated\n\n**Prompt:** ${prompt}\n${revisedPrompt ? `**DALL-E revised prompt:** ${revisedPrompt}\n` : ''}\n**Saved to:** ${localPath}\n**URL:** ${imageUrl}\n\n(URL expires in ~1 hour. Local copy saved.)`;
}

/**
 * Download a file from a URL to a local path.
 */
function downloadFile(url: string, dest: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const mod = urlObj.protocol === 'https:' ? https : require('http');

    const file = fs.createWriteStream(dest);
    mod.get(url, (response: any) => {
      if (response.statusCode === 301 || response.statusCode === 302) {
        // Follow redirect
        const redirectUrl = response.headers.location;
        if (redirectUrl) {
          downloadFile(redirectUrl, dest).then(resolve).catch(reject);
          return;
        }
      }
      response.pipe(file);
      file.on('finish', () => { file.close(); resolve(); });
    }).on('error', (err: Error) => {
      fs.unlink(dest, () => {}); // Clean up partial file
      reject(err);
    });
  });
}

async function reasonThrough(args: Record<string, unknown>): Promise<string> {
  const problem = typeof args.problem === 'string' ? args.problem : '';
  if (!problem) return 'ERROR: problem statement is required.';

  const effort = typeof args.effort === 'string' && ['low', 'medium', 'high'].includes(args.effort)
    ? args.effort
    : 'medium';

  const { status, data } = await apiRequest('/chat/completions', {
    model: 'o3',
    messages: [
      {
        role: 'user',
        content: problem,
      },
    ],
    reasoning_effort: effort,
  }, REASONING_TIMEOUT_MS);

  if (status === 401) return 'ERROR: OpenAI API key is invalid.';
  if (status === 429) return 'ERROR: OpenAI rate limit exceeded.';
  if (status !== 200) {
    return `ERROR: o3 reasoning failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const content = data.choices?.[0]?.message?.content || '(no content returned)';
  const usage = data.usage;

  let usageInfo = '';
  if (usage) {
    usageInfo = `\n\n---\n*Reasoning tokens: ${usage.completion_tokens_details?.reasoning_tokens || 'N/A'} | Total: ${usage.total_tokens || 'N/A'}*`;
  }

  return truncate(`## Reasoning Analysis\n\n${content}${usageInfo}`, MAX_RESPONSE_CHARS);
}

async function transcribeAudio(args: Record<string, unknown>): Promise<string> {
  const filePath = typeof args.file_path === 'string' ? args.file_path : '';
  if (!filePath) return 'ERROR: audio file_path is required.';

  if (!fs.existsSync(filePath)) {
    return `ERROR: Audio file not found: ${filePath}`;
  }

  const language = typeof args.language === 'string' ? args.language : undefined;

  // Whisper requires multipart/form-data — use a boundary-based approach
  const apiKey = getApiKey();
  if (!apiKey) return 'ERROR: OpenAI API key not configured.';

  const boundary = `----FormBoundary${Date.now()}`;
  const fileBuffer = fs.readFileSync(filePath);
  const fileName = path.basename(filePath);

  // Build multipart body
  const parts: Buffer[] = [];

  // File part
  parts.push(Buffer.from(
    `--${boundary}\r\n` +
    `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n` +
    `Content-Type: audio/mpeg\r\n\r\n`
  ));
  parts.push(fileBuffer);
  parts.push(Buffer.from('\r\n'));

  // Model part
  parts.push(Buffer.from(
    `--${boundary}\r\n` +
    `Content-Disposition: form-data; name="model"\r\n\r\n` +
    `whisper-1\r\n`
  ));

  // Language part (optional)
  if (language) {
    parts.push(Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="language"\r\n\r\n` +
      `${language}\r\n`
    ));
  }

  // Response format
  parts.push(Buffer.from(
    `--${boundary}\r\n` +
    `Content-Disposition: form-data; name="response_format"\r\n\r\n` +
    `verbose_json\r\n`
  ));

  parts.push(Buffer.from(`--${boundary}--\r\n`));

  const bodyBuffer = Buffer.concat(parts);

  return new Promise((resolve) => {
    const options: https.RequestOptions = {
      hostname: API_HOST,
      port: 443,
      path: '/v1/audio/transcriptions',
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': bodyBuffer.length,
      },
      timeout: REQUEST_TIMEOUT_MS,
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          if (res.statusCode === 401) {
            resolve('ERROR: OpenAI API key is invalid.');
            return;
          }
          if (res.statusCode !== 200) {
            resolve(`ERROR: Whisper transcription failed (${res.statusCode}): ${parsed.error?.message || data.slice(0, 500)}`);
            return;
          }

          const text = parsed.text || '(no transcription)';
          const duration = parsed.duration ? `${Math.round(parsed.duration)}s` : 'unknown';
          const lang = parsed.language || 'detected';

          resolve(`## Transcription\n\n**File:** ${fileName}\n**Duration:** ${duration}\n**Language:** ${lang}\n\n${truncate(text, MAX_RESPONSE_CHARS)}`);
        } catch {
          resolve(`ERROR: Failed to parse Whisper response: ${data.slice(0, 500)}`);
        }
      });
    });

    req.on('error', (err) => resolve(`ERROR: Whisper request failed: ${err.message}`));
    req.on('timeout', () => { req.destroy(); resolve('ERROR: Whisper request timed out'); });

    req.write(bodyBuffer);
    req.end();
  });
}

/**
 * Generate embeddings for semantic search / memory.
 * This is primarily for internal use by the memory system but exposed as a tool
 * for advanced users who want to compute similarity.
 */
async function generateEmbedding(args: Record<string, unknown>): Promise<string> {
  const text = typeof args.text === 'string' ? args.text : '';
  if (!text) return 'ERROR: text is required.';

  const { status, data } = await apiRequest('/embeddings', {
    model: 'text-embedding-3-small',
    input: text,
    dimensions: 512, // Compact but effective
  });

  if (status === 401) return 'ERROR: OpenAI API key is invalid.';
  if (status === 429) return 'ERROR: OpenAI rate limit exceeded.';
  if (status !== 200) {
    return `ERROR: Embedding failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const embedding = data.data?.[0]?.embedding;
  if (!embedding || !Array.isArray(embedding)) {
    return 'ERROR: No embedding vector returned.';
  }

  return `## Embedding Generated\n\n**Model:** text-embedding-3-small\n**Dimensions:** ${embedding.length}\n**Input length:** ${text.length} chars\n\nVector: [${embedding.slice(0, 5).map((n: number) => n.toFixed(6)).join(', ')}… (${embedding.length} total)]`;
}

// ── Tool declarations ────────────────────────────────────────────────

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  {
    name: 'generate_image',
    description:
      'Generate an image using DALL-E 3. Creates high-quality images from text descriptions. ' +
      'Best for: visual content creation, concept art, diagrams, illustrations, UI mockups, icons, ' +
      'or any time the user asks to create, draw, or visualize something. ' +
      'Images are saved locally and a temporary URL is provided.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Detailed description of the image to generate. Be specific about style, composition, colors, and content.',
        },
        size: {
          type: 'string',
          description: 'Image dimensions: "1024x1024" (square, default), "1792x1024" (landscape), "1024x1792" (portrait).',
        },
        quality: {
          type: 'string',
          description: '"standard" (default, faster) or "hd" (higher detail, slower, costs more).',
        },
        style: {
          type: 'string',
          description: '"vivid" (default, hyper-real and dramatic) or "natural" (more natural, less hyper-real).',
        },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'reason_through',
    description:
      'Deep multi-step reasoning via OpenAI o3. Excels at complex analytical problems that require ' +
      'extended chain-of-thought. Best for: mathematical proofs, code architecture decisions, scientific analysis, ' +
      'logic puzzles, complex debugging, strategic planning, and any problem that benefits from thinking step by step. ' +
      'Takes longer than standard completions — use when quality of reasoning matters most.',
    parameters: {
      type: 'object',
      properties: {
        problem: {
          type: 'string',
          description: 'The problem to reason through. Provide full context, constraints, and what kind of answer you need.',
        },
        effort: {
          type: 'string',
          description: 'Reasoning effort: "low" (quick, less thorough), "medium" (default, balanced), "high" (maximum depth, most expensive).',
        },
      },
      required: ['problem'],
    },
  },
  {
    name: 'transcribe_audio',
    description:
      'Transcribe audio files to text using OpenAI Whisper. Supports mp3, mp4, mpeg, mpga, m4a, wav, and webm. ' +
      'Best for: transcribing meetings, voice notes, podcasts, interviews, or any audio content. ' +
      'Maximum file size: 25MB.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute path to the audio file on disk.',
        },
        language: {
          type: 'string',
          description: 'Optional ISO-639-1 language code (e.g. "en", "es", "fr"). Improves accuracy if known.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'generate_embedding',
    description:
      'Generate a semantic embedding vector for text using OpenAI text-embedding-3-small. ' +
      'Useful for computing semantic similarity between texts, building search indices, or clustering content. ' +
      'Returns a 512-dimensional vector. Primarily used internally by the memory system.',
    parameters: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description: 'The text to generate an embedding for.',
        },
      },
      required: ['text'],
    },
  },
];

// ── Public exports ───────────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'generate_image':
        return ok(await generateImage(args));
      case 'reason_through':
        return ok(await reasonThrough(args));
      case 'transcribe_audio':
        return ok(await transcribeAudio(args));
      case 'generate_embedding':
        return ok(await generateEmbedding(args));
      default:
        return fail(`Unknown openai-services tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return fail(`openai-services "${toolName}" failed: ${message}`);
  }
}

export async function detect(): Promise<boolean> {
  const key = getApiKey();
  return !!key && key.length > 0;
}
