/**
 * polymath-router.ts — Unified Creative Dispatch for NEXUS OS.
 *
 * Sprint 6 Track F: "The Polymath Router"
 *
 * A meta-connector that provides:
 *   1. Creative intent detection — classify what the user wants to make
 *   2. Unified dispatch — single entry point for all creative operations
 *   3. Pipeline orchestration — chain multi-step creative workflows
 *   4. Capability reporting — what creative tools are available
 *
 * Does NOT replace individual connectors — instead sits above them as an
 * intelligent routing layer. The Gemini model uses polymath_ tools when
 * the request spans multiple creative domains or needs orchestration.
 *
 * Supported creative backends:
 *   - ComfyUI  (images)      → comfyui_txt2img, comfyui_img2img
 *   - VEO 3    (video)       → video_generate, video_from_image, video_stitch
 *   - Composer (audio/music) → composer_generate_music, composer_generate_sfx,
 *                               composer_synthesize_speech, composer_create_podcast
 *   - Coding Kit (code)      → coding_kit_search, coding_kit_find_symbols
 *   - OpenAI   (DALL-E 3)    → openai_generate_image (fallback for images)
 *
 * Exports:
 *   TOOLS   — tool declarations array
 *   execute — async tool dispatcher
 *   detect  — returns true (always available as meta-router)
 */

// ── Types ────────────────────────────────────────────────────────────

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

/** Creative domain categories for intent classification */
export type CreativeDomain =
  | 'image'
  | 'video'
  | 'music'
  | 'sfx'
  | 'speech'
  | 'podcast'
  | 'code'
  | 'document';

/** A single step in a creative pipeline */
export interface PipelineStep {
  /** Which creative domain this step targets */
  domain: CreativeDomain;
  /** Natural language description of what this step produces */
  prompt: string;
  /** Domain-specific options passed to the underlying connector */
  options?: Record<string, unknown>;
  /** Whether to use the output of the previous step as input */
  chain_from_previous?: boolean;
}

/** Pipeline execution state */
export interface PipelineState {
  id: string;
  name: string;
  steps: PipelineStep[];
  current_step: number;
  status: 'pending' | 'running' | 'completed' | 'failed';
  outputs: Array<{ step: number; domain: CreativeDomain; result: string; file_path?: string }>;
  error?: string;
  started_at: number;
  completed_at?: number;
}

/** Creative backend availability */
interface BackendStatus {
  name: string;
  domain: CreativeDomain[];
  available: boolean;
  tools: string[];
}

// ── Constants ────────────────────────────────────────────────────────

/** Map of creative domains to their primary connector tools */
const DOMAIN_TOOL_MAP: Record<CreativeDomain, { connector: string; primary_tool: string; fallback_tool?: string }> = {
  image:    { connector: 'comfyui',      primary_tool: 'comfyui_txt2img',           fallback_tool: 'openai_generate_image' },
  video:    { connector: 'video-gen',    primary_tool: 'video_generate'             },
  music:    { connector: 'audio-gen',    primary_tool: 'composer_generate_music'    },
  sfx:      { connector: 'audio-gen',    primary_tool: 'composer_generate_sfx'      },
  speech:   { connector: 'audio-gen',    primary_tool: 'composer_synthesize_speech' },
  podcast:  { connector: 'audio-gen',    primary_tool: 'composer_create_podcast'    },
  code:     { connector: 'coding-kit',   primary_tool: 'coding_kit_search'          },
  document: { connector: 'office',       primary_tool: 'office_create_document'     },
};

/** Keywords that signal creative domains in user prompts */
const DOMAIN_KEYWORDS: Record<CreativeDomain, string[]> = {
  image:    ['image', 'picture', 'photo', 'illustration', 'drawing', 'render', 'portrait', 'landscape', 'artwork', 'thumbnail', 'icon', 'logo', 'banner', 'poster', 'wallpaper', 'meme', 'comic', 'concept art'],
  video:    ['video', 'clip', 'animation', 'animate', 'motion', 'film', 'cinematic', 'trailer', 'timelapse', 'footage', 'movie'],
  music:    ['music', 'song', 'melody', 'beat', 'track', 'composition', 'instrumental', 'background music', 'soundtrack', 'jingle', 'loop', 'ambient'],
  sfx:      ['sound effect', 'sfx', 'noise', 'whoosh', 'explosion', 'click', 'alert', 'notification sound', 'foley'],
  speech:   ['speech', 'narration', 'voiceover', 'voice over', 'read aloud', 'say', 'speak', 'tts', 'text to speech', 'announce'],
  podcast:  ['podcast', 'episode', 'dialogue', 'conversation', 'interview', 'radio', 'show'],
  code:     ['code', 'function', 'class', 'module', 'implementation', 'algorithm', 'api', 'endpoint', 'script', 'program'],
  document: ['document', 'report', 'spreadsheet', 'presentation', 'slides', 'letter', 'memo', 'template'],
};

/** Common multi-step workflow templates */
export const WORKFLOW_TEMPLATES: Record<string, { name: string; description: string; steps: PipelineStep[] }> = {
  music_video: {
    name: 'Music Video',
    description: 'Generate music, create visuals, combine into a music video',
    steps: [
      { domain: 'music',  prompt: 'Generate the music track', options: { duration_seconds: 15, type: 'soundtrack' } },
      { domain: 'image',  prompt: 'Create the visual concept as a still image' },
      { domain: 'video',  prompt: 'Animate the visual into a video clip', chain_from_previous: true },
      { domain: 'video',  prompt: 'Overlay the music onto the video', chain_from_previous: true },
    ],
  },
  narrated_explainer: {
    name: 'Narrated Explainer',
    description: 'Generate visuals, narration, and combine into an explainer video',
    steps: [
      { domain: 'speech', prompt: 'Create the narration' },
      { domain: 'image',  prompt: 'Create the visual illustration' },
      { domain: 'video',  prompt: 'Animate the illustration', chain_from_previous: true },
      { domain: 'video',  prompt: 'Add narration to the video', chain_from_previous: true },
    ],
  },
  podcast_with_intro: {
    name: 'Podcast with Intro',
    description: 'Generate an intro jingle, synthesize dialogue, and mix together',
    steps: [
      { domain: 'music',   prompt: 'Create a podcast intro jingle', options: { type: 'jingle', duration_seconds: 5 } },
      { domain: 'podcast', prompt: 'Generate the podcast dialogue' },
      { domain: 'music',   prompt: 'Create ambient background music', options: { type: 'ambient', duration_seconds: 30 } },
    ],
  },
  social_media_post: {
    name: 'Social Media Post',
    description: 'Generate an eye-catching image with a short video teaser',
    steps: [
      { domain: 'image', prompt: 'Create the social media image' },
      { domain: 'video', prompt: 'Create a short animated teaser from the image', chain_from_previous: true, options: { duration: 4 } },
    ],
  },
  game_assets: {
    name: 'Game Assets',
    description: 'Generate concept art, sound effects, and background music for a game',
    steps: [
      { domain: 'image', prompt: 'Create the game concept art' },
      { domain: 'sfx',   prompt: 'Generate game sound effects' },
      { domain: 'music', prompt: 'Create the game background music', options: { type: 'loop', mood: 'epic' } },
    ],
  },
};

// ── Pipeline State ───────────────────────────────────────────────────

/** In-memory pipeline state store. Keyed by pipeline ID. */
const activePipelines: Map<string, PipelineState> = new Map();
let pipelineCounter = 0;

function generatePipelineId(): string {
  return `pipeline_${++pipelineCounter}_${Date.now()}`;
}

// ── Intent Detection ─────────────────────────────────────────────────

/**
 * Classify a natural language prompt into one or more creative domains.
 * Returns domains sorted by confidence (keyword match count).
 */
export function classifyIntent(prompt: string): CreativeDomain[] {
  const lower = prompt.toLowerCase();
  const scores: Array<{ domain: CreativeDomain; score: number }> = [];

  for (const [domain, keywords] of Object.entries(DOMAIN_KEYWORDS) as Array<[CreativeDomain, string[]]>) {
    let score = 0;
    for (const kw of keywords) {
      if (lower.includes(kw)) score++;
    }
    if (score > 0) scores.push({ domain, score });
  }

  // Sort by score descending
  scores.sort((a, b) => b.score - a.score);
  return scores.map((s) => s.domain);
}

/**
 * Suggest a workflow template based on the detected domains.
 */
export function suggestWorkflow(domains: CreativeDomain[]): string | null {
  if (domains.length < 2) return null;

  const domainSet = new Set(domains);

  // Match templates based on domain overlap
  if (domainSet.has('music') && domainSet.has('video')) return 'music_video';
  if (domainSet.has('speech') && domainSet.has('video')) return 'narrated_explainer';
  if (domainSet.has('podcast') && domainSet.has('music')) return 'podcast_with_intro';
  if (domainSet.has('image') && domainSet.has('video') && domains.length === 2) return 'social_media_post';
  if (domainSet.has('image') && domainSet.has('sfx') && domainSet.has('music')) return 'game_assets';

  return null;
}

// ── Tool Declarations ────────────────────────────────────────────────

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'polymath_capabilities',
    description: 'List all available creative capabilities and their status. Returns which creative backends (image, video, audio, code) are available, what tools they provide, and what pre-built workflow templates exist. Use this to understand what creative operations the agent can perform.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'polymath_classify',
    description: 'Analyze a creative request and classify which domains it involves (image, video, music, sfx, speech, podcast, code, document). Returns the detected domains sorted by confidence, the primary tool that would handle each domain, and suggests a multi-step workflow template if the request spans multiple domains. Use this before routing to understand what the user needs.',
    parameters: {
      type: 'object',
      properties: {
        prompt: {
          type: 'string',
          description: 'The creative request to analyze (e.g. "make a music video about space exploration")',
        },
      },
      required: ['prompt'],
    },
  },
  {
    name: 'polymath_dispatch',
    description: 'Route a creative request to the correct backend connector. Accepts a domain (image, video, music, sfx, speech, podcast, code) and prompt, then returns the connector tool name and suggested arguments. Does NOT execute the tool — returns routing info so the agent can call the appropriate tool directly with full control over parameters.',
    parameters: {
      type: 'object',
      properties: {
        domain: {
          type: 'string',
          description: 'Creative domain: image, video, music, sfx, speech, podcast, code, or document',
        },
        prompt: {
          type: 'string',
          description: 'What to create — natural language description of the desired output',
        },
        options: {
          type: 'object',
          description: 'Domain-specific options passed to the underlying connector (e.g. aspect_ratio for video, mood for music)',
        },
      },
      required: ['domain', 'prompt'],
    },
  },
  {
    name: 'polymath_pipeline_create',
    description: 'Create a multi-step creative pipeline that chains outputs from one domain to the next. Pipelines execute sequentially, passing file outputs forward. Use a template name for pre-built workflows (music_video, narrated_explainer, podcast_with_intro, social_media_post, game_assets) or define custom steps. Returns a pipeline ID for tracking.',
    parameters: {
      type: 'object',
      properties: {
        template: {
          type: 'string',
          description: 'Name of a pre-built workflow template (music_video, narrated_explainer, podcast_with_intro, social_media_post, game_assets). Overrides custom steps if provided.',
        },
        steps: {
          type: 'array',
          description: 'Custom pipeline steps. Each step has: domain (creative domain), prompt (what to create), options (domain-specific), chain_from_previous (use prior output as input). Ignored if template is provided.',
        },
        name: {
          type: 'string',
          description: 'Human-readable name for this pipeline (auto-generated if omitted)',
        },
        creative_brief: {
          type: 'string',
          description: 'Overall creative direction — this prompt is prepended to each step for consistency',
        },
      },
    },
  },
  {
    name: 'polymath_pipeline_status',
    description: 'Check the status of a creative pipeline. Returns current step, progress percentage, all outputs so far, and any errors. Use to monitor long-running multi-step creative workflows.',
    parameters: {
      type: 'object',
      properties: {
        pipeline_id: {
          type: 'string',
          description: 'The pipeline ID returned by polymath_pipeline_create',
        },
      },
      required: ['pipeline_id'],
    },
  },
  {
    name: 'polymath_pipeline_list',
    description: 'List all active and completed creative pipelines with their status, step count, and progress. Useful for managing multiple ongoing creative projects.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
];

// ── Execute Dispatcher ───────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'polymath_capabilities':
        return handleCapabilities();
      case 'polymath_classify':
        return handleClassify(args);
      case 'polymath_dispatch':
        return handleDispatch(args);
      case 'polymath_pipeline_create':
        return handlePipelineCreate(args);
      case 'polymath_pipeline_status':
        return handlePipelineStatus(args);
      case 'polymath_pipeline_list':
        return handlePipelineList();
      default:
        return { error: `Unknown polymath tool: ${toolName}` };
    }
  } catch (err: any) {
    return { error: `Polymath Router error: ${err?.message || String(err)}` };
  }
}

// ── Detect ───────────────────────────────────────────────────────────

/**
 * The Polymath Router is always available — it's a meta-routing layer
 * that delegates to whichever creative backends are detected.
 */
export async function detect(): Promise<boolean> {
  return true;
}

// ── Handler Functions ────────────────────────────────────────────────

/**
 * List all creative backends and their availability.
 */
function handleCapabilities(): ToolResult {
  const backends: BackendStatus[] = [
    {
      name: 'ComfyUI (Local Stable Diffusion)',
      domain: ['image'],
      available: true, // detected at registry level
      tools: ['comfyui_txt2img', 'comfyui_img2img', 'comfyui_list_models', 'comfyui_get_queue', 'comfyui_system_info'],
    },
    {
      name: 'VEO 3 / FFmpeg (Video Generation)',
      domain: ['video'],
      available: true,
      tools: ['video_generate', 'video_from_image', 'video_status', 'video_wait', 'video_stitch', 'video_info', 'video_convert'],
    },
    {
      name: 'Gemini Composer / ElevenLabs (Audio)',
      domain: ['music', 'sfx', 'speech', 'podcast'],
      available: true,
      tools: ['composer_generate_music', 'composer_generate_sfx', 'composer_synthesize_speech', 'composer_create_podcast', 'composer_mix_tracks', 'composer_apply_effects', 'composer_list_voices', 'composer_analyze_audio'],
    },
    {
      name: 'Agent Friday Coding Kit',
      domain: ['code'],
      available: true,
      tools: ['coding_kit_load', 'coding_kit_status', 'coding_kit_search', 'coding_kit_read_file', 'coding_kit_get_tree', 'coding_kit_get_summary', 'coding_kit_find_symbols', 'coding_kit_analyze_deps'],
    },
    {
      name: 'OpenAI Services (DALL-E 3, Whisper)',
      domain: ['image'],
      available: true,
      tools: ['openai_generate_image', 'openai_transcribe_audio'],
    },
  ];

  const templates = Object.entries(WORKFLOW_TEMPLATES).map(([id, t]) => ({
    id,
    name: t.name,
    description: t.description,
    step_count: t.steps.length,
    domains: [...new Set(t.steps.map((s) => s.domain))],
  }));

  return {
    result: JSON.stringify({
      backends,
      workflow_templates: templates,
      total_creative_tools: backends.reduce((sum, b) => sum + b.tools.length, 0),
      supported_domains: Object.keys(DOMAIN_TOOL_MAP),
    }, null, 2),
  };
}

/**
 * Classify a creative prompt into domains and suggest a workflow.
 */
function handleClassify(args: Record<string, unknown>): ToolResult {
  const prompt = args.prompt;
  if (!prompt || typeof prompt !== 'string') {
    return { error: 'polymath_classify requires a "prompt" string parameter' };
  }

  const domains = classifyIntent(prompt);
  const suggestedWorkflow = suggestWorkflow(domains);

  const routing = domains.map((d) => ({
    domain: d,
    primary_tool: DOMAIN_TOOL_MAP[d]?.primary_tool ?? 'unknown',
    fallback_tool: DOMAIN_TOOL_MAP[d]?.fallback_tool ?? null,
    connector: DOMAIN_TOOL_MAP[d]?.connector ?? 'unknown',
  }));

  return {
    result: JSON.stringify({
      prompt,
      detected_domains: domains,
      routing,
      suggested_workflow: suggestedWorkflow,
      workflow_template: suggestedWorkflow ? WORKFLOW_TEMPLATES[suggestedWorkflow] : null,
      is_multi_domain: domains.length > 1,
    }, null, 2),
  };
}

/**
 * Route a creative request to the correct connector tool.
 * Returns routing info — does NOT execute the tool.
 */
function handleDispatch(args: Record<string, unknown>): ToolResult {
  const domain = args.domain as string;
  const prompt = args.prompt as string;
  const options = (args.options as Record<string, unknown>) ?? {};

  if (!domain || typeof domain !== 'string') {
    return { error: 'polymath_dispatch requires a "domain" string parameter' };
  }
  if (!prompt || typeof prompt !== 'string') {
    return { error: 'polymath_dispatch requires a "prompt" string parameter' };
  }

  const mapping = DOMAIN_TOOL_MAP[domain as CreativeDomain];
  if (!mapping) {
    return {
      error: `Unknown creative domain: "${domain}". Valid domains: ${Object.keys(DOMAIN_TOOL_MAP).join(', ')}`,
    };
  }

  // Build suggested arguments for the target tool
  const suggestedArgs = buildToolArgs(domain as CreativeDomain, prompt, options);

  return {
    result: JSON.stringify({
      domain,
      connector: mapping.connector,
      primary_tool: mapping.primary_tool,
      fallback_tool: mapping.fallback_tool ?? null,
      suggested_args: suggestedArgs,
      instruction: `Call "${mapping.primary_tool}" with the suggested_args to execute this creative operation. If it fails, try "${mapping.fallback_tool ?? 'N/A'}" as fallback.`,
    }, null, 2),
  };
}

/**
 * Build suggested arguments for a creative tool call.
 */
function buildToolArgs(
  domain: CreativeDomain,
  prompt: string,
  options: Record<string, unknown>,
): Record<string, unknown> {
  switch (domain) {
    case 'image':
      return {
        prompt,
        negative_prompt: options.negative_prompt ?? '',
        width: options.width ?? 1024,
        height: options.height ?? 1024,
        steps: options.steps ?? 25,
        cfg_scale: options.cfg_scale ?? 7.0,
        ...options,
      };
    case 'video':
      return {
        prompt,
        aspect_ratio: options.aspect_ratio ?? '16:9',
        duration: options.duration ?? 8,
        allow_people: options.allow_people ?? false,
        ...options,
      };
    case 'music':
      return {
        prompt,
        mood: options.mood ?? 'cinematic',
        type: options.type ?? 'soundtrack',
        duration_seconds: options.duration_seconds ?? 15,
        ...options,
      };
    case 'sfx':
      return {
        prompt,
        duration_seconds: options.duration_seconds ?? 3,
        ...options,
      };
    case 'speech':
      return {
        text: prompt,
        voice: options.voice ?? 'Kore',
        speed: options.speed ?? 1.0,
        ...options,
      };
    case 'podcast':
      return {
        topic: prompt,
        host_voice: options.host_voice ?? 'Kore',
        guest_voice: options.guest_voice ?? 'Puck',
        duration_minutes: options.duration_minutes ?? 3,
        ...options,
      };
    case 'code':
      return {
        query: prompt,
        maxResults: options.maxResults ?? 20,
        ...options,
      };
    case 'document':
      return {
        title: prompt,
        content: options.content ?? '',
        ...options,
      };
    default:
      return { prompt, ...options };
  }
}

/**
 * Create a multi-step creative pipeline.
 */
function handlePipelineCreate(args: Record<string, unknown>): ToolResult {
  const templateName = args.template as string | undefined;
  const customSteps = args.steps as PipelineStep[] | undefined;
  const name = (args.name as string) ?? `Pipeline ${pipelineCounter + 1}`;
  const creativeBrief = args.creative_brief as string | undefined;

  let steps: PipelineStep[];

  if (templateName) {
    const template = WORKFLOW_TEMPLATES[templateName];
    if (!template) {
      return {
        error: `Unknown workflow template: "${templateName}". Available: ${Object.keys(WORKFLOW_TEMPLATES).join(', ')}`,
      };
    }
    steps = template.steps.map((s) => ({ ...s }));
  } else if (customSteps && Array.isArray(customSteps) && customSteps.length > 0) {
    // Validate custom steps
    for (let i = 0; i < customSteps.length; i++) {
      const step = customSteps[i];
      if (!step.domain || !DOMAIN_TOOL_MAP[step.domain]) {
        return { error: `Step ${i + 1}: invalid domain "${step.domain}". Valid: ${Object.keys(DOMAIN_TOOL_MAP).join(', ')}` };
      }
      if (!step.prompt || typeof step.prompt !== 'string') {
        return { error: `Step ${i + 1}: missing or invalid "prompt"` };
      }
    }
    steps = customSteps;
  } else {
    return { error: 'polymath_pipeline_create requires either "template" or "steps" parameter' };
  }

  // Apply creative brief to each step if provided
  if (creativeBrief) {
    steps = steps.map((s) => ({
      ...s,
      prompt: `[Creative Brief: ${creativeBrief}] ${s.prompt}`,
    }));
  }

  const pipeline: PipelineState = {
    id: generatePipelineId(),
    name: templateName ? WORKFLOW_TEMPLATES[templateName]!.name : name,
    steps,
    current_step: 0,
    status: 'pending',
    outputs: [],
    started_at: Date.now(),
  };

  activePipelines.set(pipeline.id, pipeline);

  // Build execution plan (what tools will be called)
  const executionPlan = steps.map((step, i) => ({
    step: i + 1,
    domain: step.domain,
    tool: DOMAIN_TOOL_MAP[step.domain]?.primary_tool ?? 'unknown',
    prompt: step.prompt,
    chains_from_previous: step.chain_from_previous ?? false,
  }));

  return {
    result: JSON.stringify({
      pipeline_id: pipeline.id,
      name: pipeline.name,
      status: pipeline.status,
      total_steps: steps.length,
      execution_plan: executionPlan,
      instruction: `Pipeline created. Execute each step in order using the tools listed in the execution plan. After each step, update the pipeline using step outputs. Steps with chains_from_previous=true should receive the file_path from the previous step's output.`,
    }, null, 2),
  };
}

/**
 * Check pipeline status.
 */
function handlePipelineStatus(args: Record<string, unknown>): ToolResult {
  const pipelineId = args.pipeline_id as string;
  if (!pipelineId || typeof pipelineId !== 'string') {
    return { error: 'polymath_pipeline_status requires a "pipeline_id" string parameter' };
  }

  const pipeline = activePipelines.get(pipelineId);
  if (!pipeline) {
    return { error: `Pipeline not found: "${pipelineId}"` };
  }

  const progress = pipeline.steps.length > 0
    ? Math.round((pipeline.outputs.length / pipeline.steps.length) * 100)
    : 0;

  return {
    result: JSON.stringify({
      pipeline_id: pipeline.id,
      name: pipeline.name,
      status: pipeline.status,
      progress_percent: progress,
      current_step: pipeline.current_step,
      total_steps: pipeline.steps.length,
      outputs: pipeline.outputs,
      error: pipeline.error,
      elapsed_ms: Date.now() - pipeline.started_at,
    }, null, 2),
  };
}

/**
 * List all pipelines.
 */
function handlePipelineList(): ToolResult {
  const pipelines = Array.from(activePipelines.values()).map((p) => ({
    pipeline_id: p.id,
    name: p.name,
    status: p.status,
    steps: p.steps.length,
    completed_steps: p.outputs.length,
    progress_percent: p.steps.length > 0 ? Math.round((p.outputs.length / p.steps.length) * 100) : 0,
    elapsed_ms: Date.now() - p.started_at,
  }));

  return {
    result: JSON.stringify({
      total_pipelines: pipelines.length,
      active: pipelines.filter((p) => p.status === 'running').length,
      completed: pipelines.filter((p) => p.status === 'completed').length,
      pipelines,
    }, null, 2),
  };
}
