/**
 * comfyui.ts — ComfyUI local image generation connector for NEXUS OS.
 *
 * Provides AI agent tooling for local Stable Diffusion image generation
 * via ComfyUI's HTTP API. Constructs workflow JSON node graphs for txt2img
 * and img2img, polls for completion, and returns generated image paths.
 *
 * ComfyUI API surface:
 *   GET  /system_stats           — health check + system info
 *   POST /prompt                 — submit workflow graph
 *   GET  /history/{prompt_id}    — poll for completion + outputs
 *   GET  /queue                  — current queue status
 *   GET  /object_info/{node}     — list available models/nodes
 *
 * Three-tier routing (Track B3 will unify these):
 *   1. ComfyUI (local)           — this connector
 *   2. Nano Banana 2 (Gemini)    — existing openai-services
 *   3. DALL-E 3 (OpenAI)         — existing openai-services
 *
 * Sprint 6 Track B Phase 1: "The Canvas" — ComfyUI Discovery & Scaffold
 * Sprint 6 Track B Phase 2: Workflow Templates & Model Management
 *
 * Exports:
 *   TOOLS    — Array of tool declarations for the connector registry
 *   execute  — Async handler that dispatches tool calls by name
 *   detect   — Async check for whether ComfyUI is running
 */

import * as http from 'node:http';
import * as path from 'node:path';

// ---------------------------------------------------------------------------
// Types
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
// Configuration
// ---------------------------------------------------------------------------

const COMFYUI_HOST = process.env.COMFYUI_HOST || '127.0.0.1';
const COMFYUI_PORT = parseInt(process.env.COMFYUI_PORT || '8188', 10);

/** Poll interval when waiting for image generation to complete. */
const POLL_INTERVAL_MS = 1_500;

/** Maximum time to wait for a single generation (2 minutes). */
const GENERATION_TIMEOUT_MS = 120_000;

/** HTTP request timeout for quick API calls (10 s). */
const HTTP_TIMEOUT_MS = 10_000;

/** Max characters in tool result output. */
const MAX_OUTPUT_CHARS = 8_000;

// ---------------------------------------------------------------------------
// Model Registry — Track B Phase 2: Workflow Templates & Model Management
// ---------------------------------------------------------------------------

/** Known Stable Diffusion model architecture types. */
export type ModelArchitecture = 'sd15' | 'sdxl' | 'sd3' | 'turbo' | 'flux' | 'unknown';

/** Quality-speed tier based on model architecture. */
export type QualityTier = 'fast' | 'balanced' | 'quality';

/** Recommended generation settings for a model architecture. */
export interface ModelProfile {
  architecture: ModelArchitecture;
  defaultWidth: number;
  defaultHeight: number;
  defaultSteps: number;
  defaultCfg: number;
  defaultSampler: string;
  defaultScheduler: string;
  estimatedVramMB: number;
  qualityTier: QualityTier;
  description: string;
}

/**
 * Architecture profiles — recommended settings per model type.
 * These drive auto-resolution, step counts, and CFG when the user doesn't
 * specify explicit overrides.  SDXL models automatically get 1024×1024;
 * Turbo models get 4 steps with low CFG, etc.
 */
export const MODEL_PROFILES: Record<ModelArchitecture, ModelProfile> = {
  sd15: {
    architecture: 'sd15',
    defaultWidth: 512, defaultHeight: 512,
    defaultSteps: 20, defaultCfg: 7,
    defaultSampler: 'euler', defaultScheduler: 'normal',
    estimatedVramMB: 4096, qualityTier: 'balanced',
    description: 'Stable Diffusion 1.5 — fast, versatile, 4 GB VRAM',
  },
  sdxl: {
    architecture: 'sdxl',
    defaultWidth: 1024, defaultHeight: 1024,
    defaultSteps: 25, defaultCfg: 7,
    defaultSampler: 'dpmpp_2m', defaultScheduler: 'karras',
    estimatedVramMB: 8192, qualityTier: 'quality',
    description: 'Stable Diffusion XL — high quality, 8 GB VRAM',
  },
  sd3: {
    architecture: 'sd3',
    defaultWidth: 1024, defaultHeight: 1024,
    defaultSteps: 28, defaultCfg: 4.5,
    defaultSampler: 'dpmpp_2m', defaultScheduler: 'sgm_uniform',
    estimatedVramMB: 12288, qualityTier: 'quality',
    description: 'Stable Diffusion 3 — premium quality, 12 GB VRAM',
  },
  turbo: {
    architecture: 'turbo',
    defaultWidth: 512, defaultHeight: 512,
    defaultSteps: 4, defaultCfg: 1.5,
    defaultSampler: 'euler_ancestral', defaultScheduler: 'normal',
    estimatedVramMB: 4096, qualityTier: 'fast',
    description: 'SD Turbo / Lightning — 4 steps, near-instant, 4 GB VRAM',
  },
  flux: {
    architecture: 'flux',
    defaultWidth: 1024, defaultHeight: 1024,
    defaultSteps: 20, defaultCfg: 3.5,
    defaultSampler: 'euler', defaultScheduler: 'normal',
    estimatedVramMB: 12288, qualityTier: 'quality',
    description: 'Flux — strong prompt-following, 12 GB VRAM',
  },
  unknown: {
    architecture: 'unknown',
    defaultWidth: 512, defaultHeight: 512,
    defaultSteps: 20, defaultCfg: 7,
    defaultSampler: 'euler', defaultScheduler: 'normal',
    estimatedVramMB: 4096, qualityTier: 'balanced',
    description: 'Unknown architecture — using SD 1.5 defaults',
  },
};

/**
 * Classify a checkpoint model filename into its architecture type.
 * Uses filename pattern matching — covers common community naming conventions.
 */
export function classifyModel(filename: string): ModelArchitecture {
  const lower = filename.toLowerCase();
  // Turbo/Lightning variants — check first (may also contain 'xl' or 'sd')
  if (lower.includes('turbo') || lower.includes('lightning') || lower.includes('lcm')) return 'turbo';
  // Flux
  if (lower.includes('flux')) return 'flux';
  // SD3
  if (lower.includes('sd3') || lower.includes('sd_3') || lower.includes('stable-diffusion-3')) return 'sd3';
  // SDXL — includes community models ending in "XL" (juggernautXL, protovisionXL, etc.)
  if (
    lower.includes('sdxl') || lower.includes('sd_xl') ||
    lower.includes('xl_base') || lower.includes('xl-base') ||
    /xl[._\-v]|xl\.(?:safetensors|ckpt|pt|bin)$/.test(lower)
  ) return 'sdxl';
  // SD 1.5 — common community model names
  if (
    lower.includes('sd_1') || lower.includes('sd1') || lower.includes('v1-5') ||
    lower.includes('v1.5') || lower.includes('sd-v1') || lower.includes('dreamshaper') ||
    lower.includes('realistic') || lower.includes('deliberate') || lower.includes('revanimated')
  ) return 'sd15';

  return 'unknown';
}

/**
 * Resolve generation settings by merging user overrides with model-profile defaults.
 * User-provided values always win; the model profile fills in the gaps.
 */
export function resolveSettings(
  modelFilename: string,
  overrides: {
    width?: number;
    height?: number;
    steps?: number;
    cfg?: number;
    sampler?: string;
    scheduler?: string;
  },
): {
  width: number;
  height: number;
  steps: number;
  cfg: number;
  sampler: string;
  scheduler: string;
  profile: ModelProfile;
} {
  const profile = MODEL_PROFILES[classifyModel(modelFilename)];
  return {
    width: overrides.width ?? profile.defaultWidth,
    height: overrides.height ?? profile.defaultHeight,
    steps: overrides.steps ?? profile.defaultSteps,
    cfg: overrides.cfg ?? profile.defaultCfg,
    sampler: overrides.sampler ?? profile.defaultSampler,
    scheduler: overrides.scheduler ?? profile.defaultScheduler,
    profile,
  };
}

// ---------------------------------------------------------------------------
// HTTP Helpers
// ---------------------------------------------------------------------------

/**
 * Perform an HTTP GET request to ComfyUI.
 * Returns the response body as a string.
 */
function httpGet(urlPath: string, timeoutMs = HTTP_TIMEOUT_MS): Promise<string> {
  return new Promise((resolve, reject) => {
    const req = http.get(
      {
        hostname: COMFYUI_HOST,
        port: COMFYUI_PORT,
        path: urlPath,
        timeout: timeoutMs,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
        res.on('end', () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve(body);
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 500)}`));
          }
        });
      },
    );
    req.on('error', (err) => reject(err));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`HTTP GET ${urlPath} timed out after ${timeoutMs}ms`));
    });
  });
}

/**
 * Perform an HTTP POST request to ComfyUI with a JSON body.
 * Returns the response body as a string.
 */
function httpPostJson(urlPath: string, payload: unknown): Promise<string> {
  return new Promise((resolve, reject) => {
    const jsonBody = JSON.stringify(payload);
    const req = http.request(
      {
        hostname: COMFYUI_HOST,
        port: COMFYUI_PORT,
        path: urlPath,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(jsonBody),
        },
        timeout: HTTP_TIMEOUT_MS,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
        res.on('end', () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve(body);
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 500)}`));
          }
        });
      },
    );
    req.on('error', (err) => reject(err));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`HTTP POST ${urlPath} timed out`));
    });
    req.write(jsonBody);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Result Helpers
// ---------------------------------------------------------------------------

function truncate(text: string, limit = MAX_OUTPUT_CHARS): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit) + `\n--- truncated (${text.length} chars total) ---`;
}

function ok(text: string): ToolResult {
  return { result: truncate(text.trim()) };
}

function fail(msg: string): ToolResult {
  return { error: msg };
}

// ---------------------------------------------------------------------------
// Workflow Builders
// ---------------------------------------------------------------------------

/**
 * Build a ComfyUI workflow graph for text-to-image generation.
 * Returns a node graph compatible with POST /prompt.
 */
function buildTxt2ImgWorkflow(opts: {
  prompt: string;
  negative?: string;
  width?: number;
  height?: number;
  model?: string;
  steps?: number;
  cfg?: number;
  seed?: number;
  sampler?: string;
  scheduler?: string;
}): Record<string, unknown> {
  const {
    prompt: positive,
    negative = 'bad quality, blurry, worst quality, low quality',
    width = 512,
    height = 512,
    model = '',  // empty = ComfyUI will use its default/first model
    steps = 20,
    cfg = 7,
    seed = Math.floor(Math.random() * 2 ** 32),
    sampler = 'euler',
    scheduler = 'normal',
  } = opts;

  return {
    '1': {
      class_type: 'CheckpointLoaderSimple',
      inputs: { ckpt_name: model },
    },
    '2': {
      class_type: 'CLIPTextEncode',
      inputs: { text: positive, clip: ['1', 1] },
    },
    '3': {
      class_type: 'CLIPTextEncode',
      inputs: { text: negative, clip: ['1', 1] },
    },
    '4': {
      class_type: 'EmptyLatentImage',
      inputs: { width, height, batch_size: 1 },
    },
    '5': {
      class_type: 'KSampler',
      inputs: {
        seed,
        steps,
        cfg,
        sampler_name: sampler,
        scheduler,
        denoise: 1.0,
        model: ['1', 0],
        positive: ['2', 0],
        negative: ['3', 0],
        latent_image: ['4', 0],
      },
    },
    '6': {
      class_type: 'VAEDecode',
      inputs: { samples: ['5', 0], vae: ['1', 2] },
    },
    '7': {
      class_type: 'SaveImage',
      inputs: { filename_prefix: 'nexus', images: ['6', 0] },
    },
  };
}

/**
 * Build a ComfyUI workflow graph for image-to-image generation.
 * Takes an input image path, encodes it, and applies diffusion with denoise < 1.0.
 */
function buildImg2ImgWorkflow(opts: {
  prompt: string;
  negative?: string;
  imagePath: string;
  denoise?: number;
  model?: string;
  steps?: number;
  cfg?: number;
  seed?: number;
  sampler?: string;
  scheduler?: string;
}): Record<string, unknown> {
  const {
    prompt: positive,
    negative = 'bad quality, blurry, worst quality, low quality',
    imagePath,
    denoise = 0.65,
    model = '',
    steps = 20,
    cfg = 7,
    seed = Math.floor(Math.random() * 2 ** 32),
    sampler = 'euler',
    scheduler = 'normal',
  } = opts;

  // For img2img, we load the image and encode it through the VAE,
  // then use KSampler with denoise < 1.0 to preserve source structure
  const imageName = path.basename(imagePath);

  return {
    '1': {
      class_type: 'CheckpointLoaderSimple',
      inputs: { ckpt_name: model },
    },
    '2': {
      class_type: 'CLIPTextEncode',
      inputs: { text: positive, clip: ['1', 1] },
    },
    '3': {
      class_type: 'CLIPTextEncode',
      inputs: { text: negative, clip: ['1', 1] },
    },
    '4': {
      class_type: 'LoadImage',
      inputs: { image: imageName },
    },
    '4b': {
      class_type: 'VAEEncode',
      inputs: { pixels: ['4', 0], vae: ['1', 2] },
    },
    '5': {
      class_type: 'KSampler',
      inputs: {
        seed,
        steps,
        cfg,
        sampler_name: sampler,
        scheduler,
        denoise,
        model: ['1', 0],
        positive: ['2', 0],
        negative: ['3', 0],
        latent_image: ['4b', 0],
      },
    },
    '6': {
      class_type: 'VAEDecode',
      inputs: { samples: ['5', 0], vae: ['1', 2] },
    },
    '7': {
      class_type: 'SaveImage',
      inputs: { filename_prefix: 'nexus_i2i', images: ['6', 0] },
    },
  };
}

// ---------------------------------------------------------------------------
// Core API Functions
// ---------------------------------------------------------------------------

/**
 * Submit a workflow to ComfyUI and return the prompt_id for polling.
 */
async function submitWorkflow(
  workflow: Record<string, unknown>,
): Promise<string> {
  const payload = { prompt: workflow };
  const responseBody = await httpPostJson('/prompt', payload);
  const data = JSON.parse(responseBody);

  if (data.error) {
    throw new Error(`ComfyUI rejected workflow: ${JSON.stringify(data.error)}`);
  }
  if (!data.prompt_id) {
    throw new Error('ComfyUI response missing prompt_id');
  }
  return data.prompt_id as string;
}

/**
 * Poll /history/{prompt_id} until the prompt completes or times out.
 * Returns the history entry for the completed prompt.
 */
async function pollForCompletion(
  promptId: string,
): Promise<Record<string, unknown>> {
  const deadline = Date.now() + GENERATION_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));

    try {
      const body = await httpGet(`/history/${promptId}`);
      const history = JSON.parse(body);

      if (history[promptId]) {
        const entry = history[promptId] as Record<string, unknown>;
        // Check for execution errors
        const status = entry.status as Record<string, unknown> | undefined;
        if (status?.status_str === 'error') {
          const messages = (status.messages as string[][]) || [];
          const errMsg = messages
            .filter((m) => m[0] === 'execution_error')
            .map((m) => JSON.stringify(m[1]))
            .join('; ');
          throw new Error(`ComfyUI execution error: ${errMsg || 'unknown'}`);
        }
        return entry;
      }
    } catch (err: unknown) {
      // If it's our own thrown error, re-throw
      if (err instanceof Error && err.message.startsWith('ComfyUI execution error')) {
        throw err;
      }
      // Otherwise it might be a transient HTTP error — keep polling
    }
  }

  throw new Error(
    `Image generation timed out after ${GENERATION_TIMEOUT_MS / 1000}s. ` +
    `The job may still be running in ComfyUI (prompt_id: ${promptId}).`,
  );
}

/**
 * Extract output image filenames from a completed history entry.
 * Returns an array of objects with filename, subfolder, and type.
 */
function extractOutputImages(
  historyEntry: Record<string, unknown>,
): Array<{ filename: string; subfolder: string; type: string }> {
  const outputs = historyEntry.outputs as Record<string, unknown> | undefined;
  if (!outputs) return [];

  const images: Array<{ filename: string; subfolder: string; type: string }> = [];
  for (const nodeOutput of Object.values(outputs)) {
    const nodeData = nodeOutput as Record<string, unknown>;
    const nodeImages = nodeData.images as Array<{
      filename: string;
      subfolder?: string;
      type?: string;
    }> | undefined;
    if (nodeImages) {
      for (const img of nodeImages) {
        images.push({
          filename: img.filename,
          subfolder: img.subfolder || '',
          type: img.type || 'output',
        });
      }
    }
  }
  return images;
}

/**
 * Build a viewable URL for a ComfyUI output image.
 */
function imageViewUrl(img: { filename: string; subfolder: string; type: string }): string {
  const params = new URLSearchParams({
    filename: img.filename,
    subfolder: img.subfolder,
    type: img.type,
  });
  return `http://${COMFYUI_HOST}:${COMFYUI_PORT}/view?${params.toString()}`;
}

// ---------------------------------------------------------------------------
// Tool Handlers
// ---------------------------------------------------------------------------

async function handleTxt2Img(args: Record<string, unknown>): Promise<ToolResult> {
  const prompt = String(args.prompt || '');
  if (!prompt) return fail('Missing required parameter: prompt');

  // Phase 2: resolve model-aware defaults (SDXL → 1024², Turbo → 4 steps, etc.)
  const modelName = args.model ? String(args.model) : '';
  const settings = resolveSettings(modelName, {
    width: args.width ? Number(args.width) : undefined,
    height: args.height ? Number(args.height) : undefined,
    steps: args.steps ? Number(args.steps) : undefined,
    cfg: args.cfg ? Number(args.cfg) : undefined,
    sampler: args.sampler ? String(args.sampler) : undefined,
    scheduler: args.scheduler ? String(args.scheduler) : undefined,
  });

  const workflow = buildTxt2ImgWorkflow({
    prompt,
    negative: args.negative_prompt ? String(args.negative_prompt) : undefined,
    width: settings.width,
    height: settings.height,
    model: modelName || undefined,
    steps: settings.steps,
    cfg: settings.cfg,
    seed: args.seed != null ? Number(args.seed) : undefined,
    sampler: settings.sampler,
    scheduler: settings.scheduler,
  });

  const promptId = await submitWorkflow(workflow);
  console.log(`[ComfyUI] txt2img submitted (${settings.profile.architecture}) — prompt_id=${promptId}`);

  const historyEntry = await pollForCompletion(promptId);
  const images = extractOutputImages(historyEntry);

  if (images.length === 0) {
    return fail('Generation completed but no images were produced.');
  }

  const lines = [
    `Generated ${images.length} image(s) via ComfyUI:`,
    '',
    ...images.map((img, i) => [
      `  [${i + 1}] ${img.filename}`,
      `      URL: ${imageViewUrl(img)}`,
    ]).flat(),
    '',
    `prompt_id: ${promptId}`,
    `Model: ${modelName || '(default)'} [${settings.profile.architecture}]`,
    `Settings: ${settings.width}×${settings.height}, ${settings.steps} steps, CFG ${settings.cfg}`,
    `Seed: ${args.seed ?? 'random'}`,
  ];

  return ok(lines.join('\n'));
}

async function handleImg2Img(args: Record<string, unknown>): Promise<ToolResult> {
  const prompt = String(args.prompt || '');
  const imagePath = String(args.image_path || '');

  if (!prompt) return fail('Missing required parameter: prompt');
  if (!imagePath) return fail('Missing required parameter: image_path');

  // Phase 2: resolve model-aware defaults
  const modelName = args.model ? String(args.model) : '';
  const settings = resolveSettings(modelName, {
    steps: args.steps ? Number(args.steps) : undefined,
    cfg: args.cfg ? Number(args.cfg) : undefined,
    sampler: args.sampler ? String(args.sampler) : undefined,
    scheduler: args.scheduler ? String(args.scheduler) : undefined,
  });

  const workflow = buildImg2ImgWorkflow({
    prompt,
    imagePath,
    negative: args.negative_prompt ? String(args.negative_prompt) : undefined,
    denoise: args.denoise != null ? Number(args.denoise) : undefined,
    model: modelName || undefined,
    steps: settings.steps,
    cfg: settings.cfg,
    seed: args.seed != null ? Number(args.seed) : undefined,
    sampler: settings.sampler,
    scheduler: settings.scheduler,
  });

  const promptId = await submitWorkflow(workflow);
  console.log(`[ComfyUI] img2img submitted (${settings.profile.architecture}) — prompt_id=${promptId}`);

  const historyEntry = await pollForCompletion(promptId);
  const images = extractOutputImages(historyEntry);

  if (images.length === 0) {
    return fail('Generation completed but no images were produced.');
  }

  const lines = [
    `Generated ${images.length} image(s) via ComfyUI (img2img):`,
    '',
    ...images.map((img, i) => [
      `  [${i + 1}] ${img.filename}`,
      `      URL: ${imageViewUrl(img)}`,
    ]).flat(),
    '',
    `prompt_id: ${promptId}`,
    `Model: ${modelName || '(default)'} [${settings.profile.architecture}]`,
    `Source: ${path.basename(imagePath)}`,
    `Denoise: ${args.denoise ?? 0.65}`,
    `Settings: ${settings.steps} steps, CFG ${settings.cfg}`,
  ];

  return ok(lines.join('\n'));
}

async function handleListModels(_args: Record<string, unknown>): Promise<ToolResult> {
  // Query the CheckpointLoaderSimple node info to get available model names
  const body = await httpGet('/object_info/CheckpointLoaderSimple');
  const data = JSON.parse(body);

  const nodeInfo = data.CheckpointLoaderSimple;
  if (!nodeInfo?.input?.required?.ckpt_name) {
    return fail('Could not retrieve model list from ComfyUI.');
  }

  // ckpt_name is [[model1, model2, ...]] — nested array
  const modelList = nodeInfo.input.required.ckpt_name[0] as string[];

  if (modelList.length === 0) {
    return ok(
      'No checkpoint models found in ComfyUI.\n' +
      'Place .safetensors or .ckpt files in ComfyUI/models/checkpoints/',
    );
  }

  // Phase 2: classify each model and group by architecture
  const byArch = new Map<ModelArchitecture, string[]>();
  for (const name of modelList) {
    const arch = classifyModel(name);
    if (!byArch.has(arch)) byArch.set(arch, []);
    byArch.get(arch)!.push(name);
  }

  const lines = [
    `Found ${modelList.length} checkpoint model(s) in ComfyUI:`,
    '',
  ];

  for (const [arch, names] of byArch) {
    const profile = MODEL_PROFILES[arch];
    lines.push(`  [${arch.toUpperCase()}] ${profile.description}`);
    lines.push(`    Defaults: ${profile.defaultWidth}×${profile.defaultHeight}, ${profile.defaultSteps} steps, CFG ${profile.defaultCfg}`);
    for (const name of names) {
      lines.push(`    • ${name}`);
    }
    lines.push('');
  }

  lines.push('Use the "model" parameter to specify a checkpoint.');
  lines.push('Settings auto-adjust based on detected model architecture.');

  return ok(lines.join('\n'));
}

async function handleGetQueue(_args: Record<string, unknown>): Promise<ToolResult> {
  const body = await httpGet('/queue');
  const data = JSON.parse(body);

  const running = (data.queue_running as unknown[] | undefined) || [];
  const pending = (data.queue_pending as unknown[] | undefined) || [];

  if (running.length === 0 && pending.length === 0) {
    return ok('ComfyUI queue is empty — no jobs running or pending.');
  }

  const lines = [
    `ComfyUI Queue Status:`,
    `  Running: ${running.length} job(s)`,
    `  Pending: ${pending.length} job(s)`,
  ];

  // Add brief info about running jobs
  for (const [i, job] of running.entries()) {
    const jobArr = job as unknown[];
    const promptId = jobArr?.[1];
    lines.push(`  Running [${i + 1}]: prompt_id=${promptId ?? 'unknown'}`);
  }

  return ok(lines.join('\n'));
}

async function handleSystemInfo(_args: Record<string, unknown>): Promise<ToolResult> {
  const body = await httpGet('/system_stats');
  const data = JSON.parse(body);

  const system = data.system as Record<string, unknown> | undefined;
  if (!system) return fail('Could not retrieve system stats from ComfyUI.');

  const devices = data.devices as Array<Record<string, unknown>> | undefined;

  const info: Record<string, unknown> = {
    comfyui_version: system.comfyui_version,
    python_version: system.python_version,
    os: system.os,
  };

  if (devices && devices.length > 0) {
    info.gpu_devices = devices.map((d) => ({
      name: d.name,
      type: d.type,
      vram_total_mb: d.vram_total ? Math.round(Number(d.vram_total) / (1024 * 1024)) : null,
      vram_free_mb: d.vram_free ? Math.round(Number(d.vram_free) / (1024 * 1024)) : null,
      torch_vram_total_mb: d.torch_vram_total ? Math.round(Number(d.torch_vram_total) / (1024 * 1024)) : null,
      torch_vram_free_mb: d.torch_vram_free ? Math.round(Number(d.torch_vram_free) / (1024 * 1024)) : null,
    }));

    // VRAM budget recommendation — maps GPU capability to model architectures
    const primary = devices[0];
    const totalVram = Number(primary.vram_total || 0) / (1024 * 1024);
    if (totalVram >= 12_000) {
      info.vram_tier = 'premium';
      info.recommended_architectures = ['sd3', 'flux', 'sdxl', 'sd15', 'turbo'];
    } else if (totalVram >= 8_000) {
      info.vram_tier = 'standard';
      info.recommended_architectures = ['sdxl', 'sd15', 'turbo'];
    } else if (totalVram >= 4_000) {
      info.vram_tier = 'light';
      info.recommended_architectures = ['sd15', 'turbo'];
    } else {
      info.vram_tier = 'minimal';
      info.recommended_architectures = ['turbo'];
    }
  }

  return ok(JSON.stringify(info, null, 2));
}

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'comfyui_txt2img',
    description:
      'Generate an image from a text prompt using local Stable Diffusion via ComfyUI. ' +
      'Supports customizable resolution, sampling steps, CFG scale, and model selection. ' +
      'Returns the output image filename and a URL to view it.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Text prompt describing the desired image.',
        },
        negative_prompt: {
          type: 'string',
          description: 'Negative prompt — things to avoid (default: "bad quality, blurry, worst quality").',
        },
        width: {
          type: 'number',
          description: 'Image width in pixels (default: 512). Use multiples of 64.',
        },
        height: {
          type: 'number',
          description: 'Image height in pixels (default: 512). Use multiples of 64.',
        },
        model: {
          type: 'string',
          description: 'Checkpoint model filename (e.g. "sd_v1.5.safetensors"). Use comfyui_list_models to see available options.',
        },
        steps: {
          type: 'number',
          description: 'Number of sampling steps (default: 20). More steps = better quality but slower.',
        },
        cfg: {
          type: 'number',
          description: 'CFG (classifier-free guidance) scale (default: 7). Higher = more prompt adherence.',
        },
        seed: {
          type: 'number',
          description: 'Random seed for reproducibility. Omit for random.',
        },
        sampler: {
          type: 'string',
          description: 'Sampler name: euler, euler_ancestral, dpmpp_2m, dpmpp_sde, etc. (default: euler).',
        },
        scheduler: {
          type: 'string',
          description: 'Scheduler: normal, karras, exponential, sgm_uniform (default: normal).',
        },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'comfyui_img2img',
    description:
      'Transform an existing image using a text prompt via local Stable Diffusion (ComfyUI). ' +
      'Adjustable denoise strength controls how much the source image is preserved. ' +
      'The source image must be in ComfyUI\'s input directory.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'Text prompt describing the desired transformation.',
        },
        image_path: {
          type: 'string',
          description: 'Path to the source image (must be in ComfyUI\'s input directory).',
        },
        negative_prompt: {
          type: 'string',
          description: 'Negative prompt — things to avoid.',
        },
        denoise: {
          type: 'number',
          description: 'Denoise strength 0.0–1.0 (default: 0.65). Lower = preserve more of original image.',
        },
        model: {
          type: 'string',
          description: 'Checkpoint model filename. Use comfyui_list_models to see available options.',
        },
        steps: { type: 'number', description: 'Sampling steps (default: 20).' },
        cfg: { type: 'number', description: 'CFG scale (default: 7).' },
        seed: { type: 'number', description: 'Random seed for reproducibility.' },
        sampler: { type: 'string', description: 'Sampler name (default: euler).' },
        scheduler: { type: 'string', description: 'Scheduler (default: normal).' },
      },
      required: ['prompt', 'image_path'],
    },
  },
  {
    name: 'comfyui_list_models',
    description:
      'List all Stable Diffusion checkpoint models installed in ComfyUI. ' +
      'Returns model filenames that can be used with comfyui_txt2img and comfyui_img2img.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'comfyui_get_queue',
    description:
      'Check the current ComfyUI generation queue. Shows how many jobs are ' +
      'running and pending. Useful for monitoring generation progress.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'comfyui_system_info',
    description:
      'Get ComfyUI system information including GPU details, VRAM availability, ' +
      'and recommended model architectures for the available hardware. ' +
      'Useful for determining achievable quality level before generating images.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
];

// ---------------------------------------------------------------------------
// Execute — Main Dispatcher
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'comfyui_txt2img':     return await handleTxt2Img(args);
      case 'comfyui_img2img':     return await handleImg2Img(args);
      case 'comfyui_list_models': return await handleListModels(args);
      case 'comfyui_get_queue':   return await handleGetQueue(args);
      case 'comfyui_system_info': return await handleSystemInfo(args);
      default:
        return fail(`Unknown ComfyUI tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `ComfyUI tool "${toolName}" failed: ${message}` };
  }
}

// ---------------------------------------------------------------------------
// Detect — Check if ComfyUI is running
// ---------------------------------------------------------------------------

/**
 * Returns true if ComfyUI is reachable at the configured host:port.
 * Uses GET /system_stats as a lightweight health check.
 */
export async function detect(): Promise<boolean> {
  try {
    const body = await httpGet('/system_stats', 3_000); // 3s timeout for detection
    const data = JSON.parse(body);
    // system_stats returns { system: { ... } } when ComfyUI is running
    return data.system != null;
  } catch {
    return false;
  }
}
