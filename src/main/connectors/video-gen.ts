/**
 * video-gen.ts — Video generation connector for NEXUS OS.
 *
 * Sprint 6 Track C: "The Director" — AI Video Generation & Local Processing
 *
 * Two-tier architecture:
 *   1. Cloud generation — VEO 3 via Gemini API (text-to-video, image-to-video)
 *   2. Local processing  — FFmpeg/FFprobe for stitching, conversion, metadata
 *
 * VEO 3 API pattern (async long-running operations):
 *   POST /v1beta/models/veo-3:generateVideos  → returns operation name
 *   GET  /v1beta/{operation_name}              → poll until done → video URI
 *
 * Fallback: VEO 2 if VEO 3 is unavailable on the user's Gemini plan.
 *
 * Local FFmpeg operations:
 *   - Video stitching (concatenate clips with transitions)
 *   - Audio overlay (add music/narration to video)
 *   - Format conversion (MP4, WebM, GIF)
 *   - Metadata extraction via FFprobe
 *
 * Detection: Gemini API key present (cloud) OR FFmpeg in PATH (local).
 *
 * Exports:
 *   TOOLS    — Array of tool declarations for the connector registry
 *   execute  — Async handler that dispatches tool calls by name
 *   detect   — Async check for whether video capabilities are available
 */

import * as https from 'node:https';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { app } from 'electron';
import { settingsManager } from '../settings';

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// Types (mirrored from registry.ts to avoid circular import)
// ---------------------------------------------------------------------------

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const GEMINI_API_HOST = 'generativelanguage.googleapis.com';
const VEO_MODEL = 'veo-3';
const VEO_FALLBACK_MODEL = 'veo-2';

/** Valid aspect ratios for VEO video generation. */
const VALID_ASPECT_RATIOS = ['16:9', '9:16', '1:1'] as const;
type AspectRatio = typeof VALID_ASPECT_RATIOS[number];

/** Valid duration ranges (seconds). */
const MIN_DURATION = 4;
const MAX_DURATION = 16;
const DEFAULT_DURATION = 8;

/** Timeouts. */
const SUBMIT_TIMEOUT_MS = 30_000;   // 30s for the initial POST
const POLL_TIMEOUT_MS   = 300_000;  // 5min total polling window
const POLL_INTERVAL_MS  = 5_000;    // 5s between polls
const FFMPEG_TIMEOUT_MS = 120_000;  // 2min for local FFmpeg ops
const MAX_OUTPUT_CHARS  = 64 * 1024;

/** Person generation safety setting. */
type PersonGeneration = 'dont_allow' | 'allow_adult';

// ---------------------------------------------------------------------------
// Internal State — tracks pending video operations
// ---------------------------------------------------------------------------

interface PendingVideo {
  operationName: string;
  model: string;
  prompt: string;
  submittedAt: number;
  aspectRatio: string;
  durationSeconds: number;
}

const pendingJobs: Map<string, PendingVideo> = new Map();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getGeminiKey(): string {
  return settingsManager.getGeminiApiKey();
}

function getVideoOutputDir(): string {
  const dir = path.join(app.getPath('temp'), 'agent-friday-videos');
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/** Short job ID from operation name for display. */
function shortJobId(opName: string): string {
  const parts = opName.split('/');
  const raw = parts[parts.length - 1] || opName;
  return raw.length > 12 ? raw.slice(0, 12) : raw;
}

// ---------------------------------------------------------------------------
// Gemini API Helpers
// ---------------------------------------------------------------------------

function geminiRequest(
  apiPath: string,
  method: 'GET' | 'POST',
  body: Record<string, unknown> | null,
  apiKey: string,
  timeoutMs: number = SUBMIT_TIMEOUT_MS,
): Promise<{ status: number; data: any }> {
  return new Promise((resolve, reject) => {
    const postData = body ? JSON.stringify(body) : '';

    const headers: Record<string, string | number> = {
      'x-goog-api-key': apiKey,
    };
    if (method === 'POST' && body) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(postData);
    }

    const options: https.RequestOptions = {
      hostname: GEMINI_API_HOST,
      port: 443,
      path: apiPath,
      method,
      headers,
      timeout: timeoutMs,
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode || 0, data: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode || 0, data: { raw: data.slice(0, 2000) } });
        }
      });
    });

    req.on('error', (err) => reject(new Error(`Gemini request failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Gemini request timed out')); });

    if (method === 'POST' && postData) req.write(postData);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// VEO Generation — Submit
// ---------------------------------------------------------------------------

async function submitVideoGeneration(
  prompt: string,
  options: {
    model?: string;
    aspectRatio?: AspectRatio;
    durationSeconds?: number;
    personGeneration?: PersonGeneration;
    imageUri?: string;           // For image-to-video
    imageMimeType?: string;
    imageBase64?: string;
  } = {},
): Promise<{ operationName: string; model: string }> {
  const apiKey = getGeminiKey();
  if (!apiKey) throw new Error('Gemini API key not configured. Set it in settings to use VEO video generation.');

  const model = options.model || VEO_MODEL;
  const aspectRatio = options.aspectRatio && VALID_ASPECT_RATIOS.includes(options.aspectRatio)
    ? options.aspectRatio : '16:9';
  const durationSeconds = Math.min(MAX_DURATION, Math.max(MIN_DURATION,
    typeof options.durationSeconds === 'number' ? options.durationSeconds : DEFAULT_DURATION));
  const personGeneration = options.personGeneration || 'dont_allow';

  // Build request body
  const instance: Record<string, unknown> = { prompt };

  // If image-to-video, attach the reference image
  if (options.imageBase64 && options.imageMimeType) {
    instance.image = {
      bytesBase64Encoded: options.imageBase64,
      mimeType: options.imageMimeType,
    };
  } else if (options.imageUri) {
    instance.image = { uri: options.imageUri };
  }

  const requestBody = {
    instances: [instance],
    generationConfig: {
      aspectRatio,
      numberOfVideos: 1,
      durationSeconds,
      personGeneration,
    },
  };

  const apiPath = `/v1beta/models/${model}:generateVideos`;
  const { status, data } = await geminiRequest(apiPath, 'POST', requestBody, apiKey);

  if (status === 401 || status === 403) {
    throw new Error('Gemini API key is invalid or lacks video generation access.');
  }
  if (status === 429) {
    throw new Error('Gemini rate limit exceeded. Try again in a moment.');
  }
  if (status === 404 && model === VEO_MODEL) {
    // VEO 3 not available — try VEO 2 fallback
    return submitVideoGeneration(prompt, { ...options, model: VEO_FALLBACK_MODEL });
  }
  if (status !== 200) {
    const errMsg = data?.error?.message || JSON.stringify(data).slice(0, 500);
    throw new Error(`VEO generation failed (${status}): ${errMsg}`);
  }

  const operationName = data?.name;
  if (!operationName) {
    throw new Error('VEO API did not return an operation name. Response: ' + JSON.stringify(data).slice(0, 500));
  }

  return { operationName, model };
}

// ---------------------------------------------------------------------------
// VEO Generation — Poll
// ---------------------------------------------------------------------------

async function pollVideoOperation(
  operationName: string,
  apiKey: string,
): Promise<{ done: boolean; videoUri?: string; error?: string }> {
  const apiPath = `/v1beta/${operationName}`;
  const { status, data } = await geminiRequest(apiPath, 'GET', null, apiKey, SUBMIT_TIMEOUT_MS);

  if (status !== 200) {
    return { done: false, error: `Poll failed (${status}): ${data?.error?.message || 'unknown'}` };
  }

  if (data.done === true) {
    // Extract video URI from response
    const videos = data.response?.generatedVideos || data.response?.videos || [];
    const firstVideo = videos[0];
    const videoUri = firstVideo?.video?.uri || firstVideo?.uri;

    if (data.error) {
      return { done: true, error: `Generation failed: ${data.error.message || JSON.stringify(data.error)}` };
    }

    return { done: true, videoUri };
  }

  return { done: false };
}

/**
 * Blocking poll with timeout — waits for VEO to finish.
 */
async function waitForVideo(
  operationName: string,
): Promise<{ videoUri: string }> {
  const apiKey = getGeminiKey();
  if (!apiKey) throw new Error('Gemini API key not configured.');

  const deadline = Date.now() + POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const result = await pollVideoOperation(operationName, apiKey);

    if (result.done) {
      if (result.error) throw new Error(result.error);
      if (!result.videoUri) throw new Error('Video generation completed but no video URI returned.');
      return { videoUri: result.videoUri };
    }

    // Wait before next poll
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  throw new Error(`Video generation timed out after ${POLL_TIMEOUT_MS / 1000}s. Use video_status to check later.`);
}

// ---------------------------------------------------------------------------
// VEO Generation — Download
// ---------------------------------------------------------------------------

async function downloadVideo(videoUri: string, filename: string): Promise<string> {
  const outputDir = getVideoOutputDir();
  const localPath = path.join(outputDir, filename);

  return new Promise((resolve, reject) => {
    const url = new URL(videoUri);
    const protocol = url.protocol === 'https:' ? https : require('node:http');

    const req = protocol.get(videoUri, { timeout: 60_000 }, (res: any) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        // Follow redirect
        downloadVideo(res.headers.location, filename).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`Download failed: HTTP ${res.statusCode}`));
        return;
      }

      const fileStream = fs.createWriteStream(localPath);
      res.pipe(fileStream);
      fileStream.on('finish', () => { fileStream.close(); resolve(localPath); });
      fileStream.on('error', (err: Error) => reject(err));
    });

    req.on('error', (err: Error) => reject(new Error(`Download failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Download timed out')); });
  });
}

// ---------------------------------------------------------------------------
// FFmpeg / FFprobe Local Helpers
// ---------------------------------------------------------------------------

async function findFFmpeg(): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(
      process.platform === 'win32' ? 'where' : 'which',
      ['ffmpeg'],
      { windowsHide: true, timeout: 5000 },
    );
    return stdout.trim().split('\n')[0].trim();
  } catch {
    // Check common Windows install paths
    const commonPaths = [
      'C:\\ffmpeg\\bin\\ffmpeg.exe',
      'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
      path.join(process.env.LOCALAPPDATA || '', 'ffmpeg', 'bin', 'ffmpeg.exe'),
    ];
    for (const p of commonPaths) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  }
}

async function findFFprobe(): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(
      process.platform === 'win32' ? 'where' : 'which',
      ['ffprobe'],
      { windowsHide: true, timeout: 5000 },
    );
    return stdout.trim().split('\n')[0].trim();
  } catch {
    const commonPaths = [
      'C:\\ffmpeg\\bin\\ffprobe.exe',
      'C:\\Program Files\\ffmpeg\\bin\\ffprobe.exe',
      path.join(process.env.LOCALAPPDATA || '', 'ffmpeg', 'bin', 'ffprobe.exe'),
    ];
    for (const p of commonPaths) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  }
}

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'video_generate',
    description:
      'Generate a video from a text prompt using VEO 3 (Google Gemini). ' +
      'Returns immediately with a job ID — use video_status to poll or video_wait to block until complete. ' +
      'Supports 16:9, 9:16, and 1:1 aspect ratios. Duration 4-16 seconds.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Detailed description of the video to generate. Be specific about motion, camera angles, lighting, and style.',
        },
        aspect_ratio: {
          type: 'string',
          description: 'Aspect ratio: "16:9" (landscape, default), "9:16" (portrait/vertical), "1:1" (square)',
        },
        duration: {
          type: 'number',
          description: 'Duration in seconds (4-16, default 8)',
        },
        allow_people: {
          type: 'boolean',
          description: 'Allow generation of adult human figures (default: false)',
        },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'video_from_image',
    description:
      'Animate a still image into a video using VEO 3 (image-to-video). ' +
      'Provide a local image path and a motion prompt describing how the scene should animate.',
    parameters: {
      type: 'object',
      properties: {
        image_path: {
          type: 'string',
          description: 'Absolute path to the source image (PNG/JPG/WebP)',
        },
        prompt: {
          type: 'string',
          description: 'Description of how the image should animate (camera motion, subject movement, effects)',
        },
        aspect_ratio: {
          type: 'string',
          description: 'Aspect ratio: "16:9" (default), "9:16", "1:1"',
        },
        duration: {
          type: 'number',
          description: 'Duration in seconds (4-16, default 8)',
        },
      },
      required: ['image_path', 'prompt'],
    },
  },
  {
    name: 'video_status',
    description:
      'Check the status of a pending VEO video generation job. ' +
      'Returns whether the job is still processing, completed (with download URL), or failed.',
    parameters: {
      type: 'object',
      properties: {
        job_id: {
          type: 'string',
          description: 'The job ID returned by video_generate or video_from_image',
        },
      },
      required: ['job_id'],
    },
  },
  {
    name: 'video_wait',
    description:
      'Wait for a VEO video generation job to complete, download the result, and return the local file path. ' +
      'Blocks for up to 5 minutes. Use for small batches when you need the result immediately.',
    parameters: {
      type: 'object',
      properties: {
        job_id: {
          type: 'string',
          description: 'The job ID returned by video_generate or video_from_image',
        },
      },
      required: ['job_id'],
    },
  },
  {
    name: 'video_stitch',
    description:
      'Concatenate multiple video clips into one using FFmpeg. ' +
      'Optionally add an audio track (music, narration) as overlay. Requires FFmpeg installed locally.',
    parameters: {
      type: 'object',
      properties: {
        clips: {
          type: 'array',
          description: 'Array of absolute file paths to video clips, in order',
        },
        audio_path: {
          type: 'string',
          description: 'Optional: path to audio file to overlay on the final video',
        },
        output_path: {
          type: 'string',
          description: 'Output file path (default: auto-generated in temp directory)',
        },
        format: {
          type: 'string',
          description: 'Output format: "mp4" (default), "webm", "gif"',
        },
      },
      required: ['clips'],
    },
  },
  {
    name: 'video_info',
    description:
      'Get detailed metadata about a video file using FFprobe — duration, resolution, ' +
      'codec, frame rate, bitrate, audio tracks. Requires FFprobe installed locally.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute path to the video file to analyze',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'video_convert',
    description:
      'Convert a video file to a different format or resize it using FFmpeg. ' +
      'Supports MP4, WebM, GIF, and resolution scaling.',
    parameters: {
      type: 'object',
      properties: {
        input_path: {
          type: 'string',
          description: 'Absolute path to the input video file',
        },
        output_path: {
          type: 'string',
          description: 'Output file path (inferred format from extension)',
        },
        width: {
          type: 'number',
          description: 'Target width in pixels (maintains aspect ratio if only width specified)',
        },
        height: {
          type: 'number',
          description: 'Target height in pixels',
        },
        fps: {
          type: 'number',
          description: 'Target frame rate (e.g. 24, 30, 60)',
        },
      },
      required: ['input_path', 'output_path'],
    },
  },
];

// ---------------------------------------------------------------------------
// Execute Dispatcher
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'video_generate':
        return await handleGenerate(args);
      case 'video_from_image':
        return await handleFromImage(args);
      case 'video_status':
        return await handleStatus(args);
      case 'video_wait':
        return await handleWait(args);
      case 'video_stitch':
        return await handleStitch(args);
      case 'video_info':
        return await handleInfo(args);
      case 'video_convert':
        return await handleConvert(args);
      default:
        return { error: `Unknown video-gen tool: ${toolName}` };
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Video generation error: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detect — checks if video capabilities are available
// ---------------------------------------------------------------------------

export async function detect(): Promise<boolean> {
  // Cloud: Gemini API key present → VEO available
  const geminiKey = getGeminiKey();
  if (geminiKey && geminiKey.length > 0) return true;

  // Local: FFmpeg installed → local processing available
  const ffmpeg = await findFFmpeg();
  return ffmpeg !== null;
}

// ---------------------------------------------------------------------------
// Tool Handlers — Cloud (VEO)
// ---------------------------------------------------------------------------

async function handleGenerate(args: Record<string, unknown>): Promise<ToolResult> {
  const prompt = typeof args.prompt === 'string' ? args.prompt.trim() : '';
  if (!prompt) return { error: 'Missing required parameter: prompt' };

  const aspectRatio = typeof args.aspect_ratio === 'string'
    && (VALID_ASPECT_RATIOS as readonly string[]).includes(args.aspect_ratio)
    ? args.aspect_ratio as AspectRatio : '16:9';
  const duration = typeof args.duration === 'number'
    ? Math.min(MAX_DURATION, Math.max(MIN_DURATION, args.duration)) : DEFAULT_DURATION;
  const personGeneration: PersonGeneration = args.allow_people === true ? 'allow_adult' : 'dont_allow';

  const { operationName, model } = await submitVideoGeneration(prompt, {
    aspectRatio,
    durationSeconds: duration,
    personGeneration,
  });

  const jobId = shortJobId(operationName);

  // Track the pending job
  pendingJobs.set(jobId, {
    operationName,
    model,
    prompt,
    submittedAt: Date.now(),
    aspectRatio,
    durationSeconds: duration,
  });

  return {
    result: JSON.stringify({
      status: 'submitted',
      job_id: jobId,
      model,
      prompt: prompt.slice(0, 100) + (prompt.length > 100 ? '...' : ''),
      aspectRatio,
      durationSeconds: duration,
      message: `Video generation submitted (${model}). Use video_status("${jobId}") to check progress or video_wait("${jobId}") to block until complete.`,
    }),
  };
}

async function handleFromImage(args: Record<string, unknown>): Promise<ToolResult> {
  const imagePath = typeof args.image_path === 'string' ? args.image_path.trim() : '';
  if (!imagePath) return { error: 'Missing required parameter: image_path' };
  const prompt = typeof args.prompt === 'string' ? args.prompt.trim() : '';
  if (!prompt) return { error: 'Missing required parameter: prompt' };

  // Read and encode the image
  if (!fs.existsSync(imagePath)) {
    return { error: `Image file not found: ${imagePath}` };
  }

  const ext = path.extname(imagePath).toLowerCase();
  const mimeMap: Record<string, string> = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
  };
  const mimeType = mimeMap[ext];
  if (!mimeType) {
    return { error: `Unsupported image format: ${ext}. Use PNG, JPG, or WebP.` };
  }

  const imageBuffer = fs.readFileSync(imagePath);
  const imageBase64 = imageBuffer.toString('base64');

  const aspectRatio = typeof args.aspect_ratio === 'string'
    && (VALID_ASPECT_RATIOS as readonly string[]).includes(args.aspect_ratio)
    ? args.aspect_ratio as AspectRatio : '16:9';
  const duration = typeof args.duration === 'number'
    ? Math.min(MAX_DURATION, Math.max(MIN_DURATION, args.duration)) : DEFAULT_DURATION;

  const { operationName, model } = await submitVideoGeneration(prompt, {
    aspectRatio,
    durationSeconds: duration,
    imageBase64,
    imageMimeType: mimeType,
  });

  const jobId = shortJobId(operationName);

  pendingJobs.set(jobId, {
    operationName,
    model,
    prompt: `[img2vid] ${prompt}`,
    submittedAt: Date.now(),
    aspectRatio,
    durationSeconds: duration,
  });

  return {
    result: JSON.stringify({
      status: 'submitted',
      job_id: jobId,
      model,
      sourceImage: path.basename(imagePath),
      prompt: prompt.slice(0, 100),
      aspectRatio,
      durationSeconds: duration,
      message: `Image-to-video submitted (${model}). Use video_wait("${jobId}") to block until complete.`,
    }),
  };
}

async function handleStatus(args: Record<string, unknown>): Promise<ToolResult> {
  const jobId = typeof args.job_id === 'string' ? args.job_id.trim() : '';
  if (!jobId) return { error: 'Missing required parameter: job_id' };

  const job = pendingJobs.get(jobId);
  if (!job) {
    // List all known jobs
    const known = Array.from(pendingJobs.keys());
    return {
      error: `Unknown job ID: ${jobId}. Active jobs: ${known.length > 0 ? known.join(', ') : 'none'}`,
    };
  }

  const apiKey = getGeminiKey();
  if (!apiKey) return { error: 'Gemini API key not configured.' };

  const result = await pollVideoOperation(job.operationName, apiKey);
  const elapsed = Math.round((Date.now() - job.submittedAt) / 1000);

  if (result.done) {
    if (result.error) {
      pendingJobs.delete(jobId);
      return {
        result: JSON.stringify({
          status: 'failed',
          job_id: jobId,
          error: result.error,
          elapsed_seconds: elapsed,
        }),
      };
    }

    return {
      result: JSON.stringify({
        status: 'completed',
        job_id: jobId,
        video_uri: result.videoUri,
        elapsed_seconds: elapsed,
        message: `Video ready! Use video_wait("${jobId}") to download, or access directly: ${result.videoUri}`,
      }),
    };
  }

  return {
    result: JSON.stringify({
      status: 'processing',
      job_id: jobId,
      model: job.model,
      elapsed_seconds: elapsed,
      prompt: job.prompt.slice(0, 60),
      message: `Still generating (${elapsed}s elapsed). VEO typically takes 1-3 minutes.`,
    }),
  };
}

async function handleWait(args: Record<string, unknown>): Promise<ToolResult> {
  const jobId = typeof args.job_id === 'string' ? args.job_id.trim() : '';
  if (!jobId) return { error: 'Missing required parameter: job_id' };

  const job = pendingJobs.get(jobId);
  if (!job) {
    return { error: `Unknown job ID: ${jobId}` };
  }

  const { videoUri } = await waitForVideo(job.operationName);

  // Download the video
  const filename = `veo-${jobId}-${Date.now()}.mp4`;
  const localPath = await downloadVideo(videoUri, filename);

  // Clean up tracking
  pendingJobs.delete(jobId);

  return {
    result: JSON.stringify({
      status: 'downloaded',
      job_id: jobId,
      model: job.model,
      local_path: localPath,
      prompt: job.prompt.slice(0, 100),
      aspectRatio: job.aspectRatio,
      durationSeconds: job.durationSeconds,
      message: `Video generated and saved to: ${localPath}`,
    }),
  };
}

// ---------------------------------------------------------------------------
// Tool Handlers — Local (FFmpeg/FFprobe)
// ---------------------------------------------------------------------------

async function handleStitch(args: Record<string, unknown>): Promise<ToolResult> {
  const clips = Array.isArray(args.clips) ? args.clips.filter((c): c is string => typeof c === 'string') : [];
  if (clips.length === 0) return { error: 'Missing required parameter: clips (array of video file paths)' };

  // Validate all clips exist
  for (const clip of clips) {
    if (!fs.existsSync(clip)) {
      return { error: `Video clip not found: ${clip}` };
    }
  }

  const ffmpegPath = await findFFmpeg();
  if (!ffmpegPath) return { error: 'FFmpeg not installed. Install FFmpeg to use local video processing.' };

  const format = typeof args.format === 'string' && ['mp4', 'webm', 'gif'].includes(args.format)
    ? args.format : 'mp4';
  const outputPath = typeof args.output_path === 'string'
    ? args.output_path
    : path.join(getVideoOutputDir(), `stitched-${Date.now()}.${format}`);

  // Build FFmpeg concat filter
  // Create a temporary concat list file
  const listPath = path.join(getVideoOutputDir(), `concat-${Date.now()}.txt`);
  const listContent = clips.map((c) => `file '${c.replace(/'/g, "'\\''")}'`).join('\n');
  fs.writeFileSync(listPath, listContent);

  try {
    const ffmpegArgs = [
      '-f', 'concat',
      '-safe', '0',
      '-i', listPath,
    ];

    // Add audio overlay if specified
    const audioPath = typeof args.audio_path === 'string' ? args.audio_path : '';
    if (audioPath && fs.existsSync(audioPath)) {
      ffmpegArgs.push('-i', audioPath);
      ffmpegArgs.push(
        '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first[aout]',
        '-map', '0:v', '-map', '[aout]',
      );
    } else {
      ffmpegArgs.push('-c', 'copy');
    }

    ffmpegArgs.push('-y', outputPath);

    await execFileAsync(ffmpegPath, ffmpegArgs, {
      windowsHide: true,
      timeout: FFMPEG_TIMEOUT_MS,
    });

    // Clean up temp list file
    try { fs.unlinkSync(listPath); } catch { /* ignore */ }

    return {
      result: JSON.stringify({
        status: 'stitched',
        output_path: outputPath,
        clips_count: clips.length,
        has_audio_overlay: !!audioPath,
        format,
        message: `${clips.length} clips stitched → ${outputPath}`,
      }),
    };
  } catch (err) {
    try { fs.unlinkSync(listPath); } catch { /* ignore */ }
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `FFmpeg stitch failed: ${msg}` };
  }
}

async function handleInfo(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = typeof args.file_path === 'string' ? args.file_path.trim() : '';
  if (!filePath) return { error: 'Missing required parameter: file_path' };
  if (!fs.existsSync(filePath)) return { error: `File not found: ${filePath}` };

  const ffprobePath = await findFFprobe();
  if (!ffprobePath) return { error: 'FFprobe not installed. Install FFmpeg (includes FFprobe) for video metadata.' };

  try {
    const { stdout } = await execFileAsync(ffprobePath, [
      '-v', 'quiet',
      '-print_format', 'json',
      '-show_format',
      '-show_streams',
      filePath,
    ], {
      windowsHide: true,
      timeout: 30_000,
      maxBuffer: MAX_OUTPUT_CHARS,
    });

    const probeData = JSON.parse(stdout);

    // Extract useful fields
    const videoStream = probeData.streams?.find((s: any) => s.codec_type === 'video');
    const audioStream = probeData.streams?.find((s: any) => s.codec_type === 'audio');
    const format = probeData.format || {};

    return {
      result: JSON.stringify({
        file: path.basename(filePath),
        duration_seconds: parseFloat(format.duration) || 0,
        size_bytes: parseInt(format.size) || 0,
        size_mb: Math.round((parseInt(format.size) || 0) / 1024 / 1024 * 100) / 100,
        format: format.format_name,
        video: videoStream ? {
          codec: videoStream.codec_name,
          width: videoStream.width,
          height: videoStream.height,
          fps: eval(videoStream.r_frame_rate) || videoStream.r_frame_rate,
          bitrate: parseInt(videoStream.bit_rate) || undefined,
        } : null,
        audio: audioStream ? {
          codec: audioStream.codec_name,
          channels: audioStream.channels,
          sample_rate: parseInt(audioStream.sample_rate),
          bitrate: parseInt(audioStream.bit_rate) || undefined,
        } : null,
        stream_count: probeData.streams?.length || 0,
      }),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `FFprobe failed: ${msg}` };
  }
}

async function handleConvert(args: Record<string, unknown>): Promise<ToolResult> {
  const inputPath = typeof args.input_path === 'string' ? args.input_path.trim() : '';
  if (!inputPath) return { error: 'Missing required parameter: input_path' };
  if (!fs.existsSync(inputPath)) return { error: `Input file not found: ${inputPath}` };

  const outputPath = typeof args.output_path === 'string' ? args.output_path.trim() : '';
  if (!outputPath) return { error: 'Missing required parameter: output_path' };

  const ffmpegPath = await findFFmpeg();
  if (!ffmpegPath) return { error: 'FFmpeg not installed. Install FFmpeg for video conversion.' };

  const ffmpegArgs = ['-i', inputPath];

  // Scale filter
  const width = typeof args.width === 'number' ? Math.round(args.width) : 0;
  const height = typeof args.height === 'number' ? Math.round(args.height) : 0;
  if (width > 0 || height > 0) {
    const w = width > 0 ? String(width) : '-2';  // -2 = maintain aspect, ensure divisible by 2
    const h = height > 0 ? String(height) : '-2';
    ffmpegArgs.push('-vf', `scale=${w}:${h}`);
  }

  // Frame rate
  const fps = typeof args.fps === 'number' ? args.fps : 0;
  if (fps > 0) {
    ffmpegArgs.push('-r', String(fps));
  }

  // GIF-specific settings
  if (outputPath.endsWith('.gif')) {
    ffmpegArgs.push('-loop', '0');
  }

  ffmpegArgs.push('-y', outputPath);

  try {
    await execFileAsync(ffmpegPath, ffmpegArgs, {
      windowsHide: true,
      timeout: FFMPEG_TIMEOUT_MS,
    });

    // Get output file size
    const stats = fs.statSync(outputPath);

    return {
      result: JSON.stringify({
        status: 'converted',
        input: path.basename(inputPath),
        output: outputPath,
        output_size_mb: Math.round(stats.size / 1024 / 1024 * 100) / 100,
        message: `Converted ${path.basename(inputPath)} → ${path.basename(outputPath)} (${Math.round(stats.size / 1024)}KB)`,
      }),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `FFmpeg conversion failed: ${msg}` };
  }
}
