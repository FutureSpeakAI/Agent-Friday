/**
 * stage-presenter.ts — The Stage: Unified Creative Output Presenter
 *
 * Track G of the Polymath Update (v3.0.0).
 * Manages the creative output feed — every image, video, audio clip,
 * or code artefact that a creative connector produces flows through
 * The Stage for indexing, preview, and playback.
 *
 * 7 tools:
 *   stage_push_output   — record a new creative output
 *   stage_list_outputs   — list recent outputs with optional domain filter
 *   stage_get_output     — get a single output by ID
 *   stage_clear_outputs  — clear output history (or by domain)
 *   stage_get_stats      — aggregate statistics across domains
 *   stage_pin_output     — pin/unpin an output to keep it visible
 *   stage_export_feed    — export the feed as structured JSON
 *
 * Pure TypeScript, zero external dependencies.
 * Follows the established connector pattern: TOOLS[], execute(), detect().
 */

// ── Types ────────────────────────────────────────────────────────────────────

/** Creative domains matching polymath-router */
export type StageDomain =
  | 'image'
  | 'video'
  | 'music'
  | 'sfx'
  | 'speech'
  | 'podcast'
  | 'code'
  | 'document';

/** Render hint telling the UI how to display this output */
export type OutputRenderer =
  | 'image-viewer'
  | 'video-player'
  | 'audio-player'
  | 'code-block'
  | 'document-frame'
  | 'raw-text';

/** A single creative output record */
export interface StageOutput {
  id: string;
  domain: StageDomain;
  renderer: OutputRenderer;
  title: string;
  prompt?: string;
  source_tool: string;
  file_path?: string;
  url?: string;
  thumbnail?: string;
  metadata: Record<string, unknown>;
  pinned: boolean;
  created_at: string;
}

/** Aggregate stats for a domain */
export interface DomainStats {
  domain: StageDomain;
  count: number;
  pinned: number;
  latest?: string;
}

// ── Renderer mapping ─────────────────────────────────────────────────────────

const DOMAIN_RENDERER: Record<StageDomain, OutputRenderer> = {
  image: 'image-viewer',
  video: 'video-player',
  music: 'audio-player',
  sfx: 'audio-player',
  speech: 'audio-player',
  podcast: 'audio-player',
  code: 'code-block',
  document: 'document-frame',
};

const ALL_DOMAINS: StageDomain[] = [
  'image', 'video', 'music', 'sfx', 'speech', 'podcast', 'code', 'document',
];

// ── In-memory output store ───────────────────────────────────────────────────

const outputs: StageOutput[] = [];
let nextId = 1;

function generateId(): string {
  return `stage_${Date.now()}_${nextId++}`;
}

// ── Tool definitions ─────────────────────────────────────────────────────────

export const TOOLS = [
  {
    name: 'stage_push_output',
    description:
      'Record a new creative output on The Stage. Called after a creative tool produces a result.',
    parameters: {
      type: 'object' as const,
      properties: {
        domain: {
          type: 'string',
          enum: ALL_DOMAINS,
          description: 'Creative domain of this output',
        },
        title: {
          type: 'string',
          description: 'Human-readable title for this output',
        },
        source_tool: {
          type: 'string',
          description: 'Name of the tool that produced this output',
        },
        prompt: {
          type: 'string',
          description: 'Original prompt or instruction',
        },
        file_path: {
          type: 'string',
          description: 'Local file path (if saved to disk)',
        },
        url: {
          type: 'string',
          description: 'URL for remote or streaming content',
        },
        thumbnail: {
          type: 'string',
          description: 'Thumbnail path or data URI for preview',
        },
        metadata: {
          type: 'object',
          description: 'Arbitrary metadata (model, duration, dimensions, etc.)',
        },
      },
      required: ['domain', 'title', 'source_tool'],
    },
  },
  {
    name: 'stage_list_outputs',
    description:
      'List recent creative outputs, optionally filtered by domain. Returns newest first.',
    parameters: {
      type: 'object' as const,
      properties: {
        domain: {
          type: 'string',
          enum: ALL_DOMAINS,
          description: 'Filter to a specific creative domain',
        },
        limit: {
          type: 'number',
          description: 'Max results to return (default 20)',
        },
        pinned_only: {
          type: 'boolean',
          description: 'Only return pinned outputs',
        },
      },
    },
  },
  {
    name: 'stage_get_output',
    description: 'Get a single creative output by its ID.',
    parameters: {
      type: 'object' as const,
      properties: {
        id: {
          type: 'string',
          description: 'The output ID',
        },
      },
      required: ['id'],
    },
  },
  {
    name: 'stage_clear_outputs',
    description:
      'Clear creative output history. Optionally limit to a specific domain.',
    parameters: {
      type: 'object' as const,
      properties: {
        domain: {
          type: 'string',
          enum: ALL_DOMAINS,
          description: 'Clear only outputs in this domain (omit to clear all)',
        },
        keep_pinned: {
          type: 'boolean',
          description: 'If true, pinned outputs are preserved (default true)',
        },
      },
    },
  },
  {
    name: 'stage_get_stats',
    description:
      'Get aggregate statistics across all creative domains — counts, pinned, latest timestamps.',
    parameters: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'stage_pin_output',
    description: 'Pin or unpin a creative output to keep it visible on The Stage.',
    parameters: {
      type: 'object' as const,
      properties: {
        id: {
          type: 'string',
          description: 'The output ID to pin/unpin',
        },
        pinned: {
          type: 'boolean',
          description: 'Set pin state (default true)',
        },
      },
      required: ['id'],
    },
  },
  {
    name: 'stage_export_feed',
    description:
      'Export the entire output feed as structured JSON for archival or sharing.',
    parameters: {
      type: 'object' as const,
      properties: {
        domain: {
          type: 'string',
          enum: ALL_DOMAINS,
          description: 'Export only this domain (omit for all)',
        },
        include_metadata: {
          type: 'boolean',
          description: 'Include full metadata objects (default true)',
        },
      },
    },
  },
];

// ── Handlers ─────────────────────────────────────────────────────────────────

function handlePush(args: Record<string, unknown>): { result?: string; error?: string } {
  const domain = args.domain as StageDomain | undefined;
  const title = args.title as string | undefined;
  const source_tool = args.source_tool as string | undefined;

  if (!domain || !ALL_DOMAINS.includes(domain)) {
    return { error: `Invalid or missing domain. Must be one of: ${ALL_DOMAINS.join(', ')}` };
  }
  if (!title || typeof title !== 'string') {
    return { error: 'title is required and must be a string' };
  }
  if (!source_tool || typeof source_tool !== 'string') {
    return { error: 'source_tool is required and must be a string' };
  }

  const output: StageOutput = {
    id: generateId(),
    domain,
    renderer: DOMAIN_RENDERER[domain],
    title,
    prompt: typeof args.prompt === 'string' ? args.prompt : undefined,
    source_tool,
    file_path: typeof args.file_path === 'string' ? args.file_path : undefined,
    url: typeof args.url === 'string' ? args.url : undefined,
    thumbnail: typeof args.thumbnail === 'string' ? args.thumbnail : undefined,
    metadata: (args.metadata && typeof args.metadata === 'object' && !Array.isArray(args.metadata))
      ? args.metadata as Record<string, unknown>
      : {},
    pinned: false,
    created_at: new Date().toISOString(),
  };

  outputs.unshift(output); // newest first

  // Cap at 500 outputs to prevent memory bloat
  if (outputs.length > 500) {
    // Remove oldest unpinned outputs
    const pinnedCount = outputs.filter((o: StageOutput) => o.pinned).length;
    while (outputs.length > 500 && outputs.length > pinnedCount) {
      let lastUnpinned = -1;
      for (let i = outputs.length - 1; i >= 0; i--) {
        if (!(outputs[i] as StageOutput).pinned) { lastUnpinned = i; break; }
      }
      if (lastUnpinned >= 0) outputs.splice(lastUnpinned, 1);
      else break;
    }
  }

  return {
    result: JSON.stringify({
      id: output.id,
      domain: output.domain,
      renderer: output.renderer,
      title: output.title,
      created_at: output.created_at,
    }),
  };
}

function handleList(args: Record<string, unknown>): { result?: string; error?: string } {
  const domain = args.domain as StageDomain | undefined;
  const limit = typeof args.limit === 'number' ? Math.max(1, Math.min(args.limit, 100)) : 20;
  const pinnedOnly = args.pinned_only === true;

  let filtered = outputs;

  if (domain) {
    if (!ALL_DOMAINS.includes(domain)) {
      return { error: `Invalid domain: ${domain}` };
    }
    filtered = filtered.filter(o => o.domain === domain);
  }

  if (pinnedOnly) {
    filtered = filtered.filter(o => o.pinned);
  }

  const page = filtered.slice(0, limit);

  return {
    result: JSON.stringify({
      total: filtered.length,
      returned: page.length,
      outputs: page.map(o => ({
        id: o.id,
        domain: o.domain,
        renderer: o.renderer,
        title: o.title,
        source_tool: o.source_tool,
        file_path: o.file_path,
        url: o.url,
        thumbnail: o.thumbnail,
        pinned: o.pinned,
        created_at: o.created_at,
      })),
    }),
  };
}

function handleGet(args: Record<string, unknown>): { result?: string; error?: string } {
  const id = args.id;
  if (!id || typeof id !== 'string') {
    return { error: 'id is required and must be a string' };
  }

  const output = outputs.find(o => o.id === id);
  if (!output) {
    return { error: `Output not found: ${id}` };
  }

  return { result: JSON.stringify(output) };
}

function handleClear(args: Record<string, unknown>): { result?: string; error?: string } {
  const domain = args.domain as StageDomain | undefined;
  const keepPinned = args.keep_pinned !== false; // default true

  if (domain && !ALL_DOMAINS.includes(domain)) {
    return { error: `Invalid domain: ${domain}` };
  }

  let removed = 0;

  if (domain) {
    const before = outputs.length;
    for (let i = outputs.length - 1; i >= 0; i--) {
      if (outputs[i].domain === domain && (!keepPinned || !outputs[i].pinned)) {
        outputs.splice(i, 1);
      }
    }
    removed = before - outputs.length;
  } else {
    if (keepPinned) {
      const before = outputs.length;
      for (let i = outputs.length - 1; i >= 0; i--) {
        if (!outputs[i].pinned) outputs.splice(i, 1);
      }
      removed = before - outputs.length;
    } else {
      removed = outputs.length;
      outputs.length = 0;
    }
  }

  return {
    result: JSON.stringify({
      removed,
      remaining: outputs.length,
    }),
  };
}

function handleStats(): { result?: string; error?: string } {
  const stats: DomainStats[] = ALL_DOMAINS.map(domain => {
    const domainOutputs = outputs.filter(o => o.domain === domain);
    return {
      domain,
      count: domainOutputs.length,
      pinned: domainOutputs.filter(o => o.pinned).length,
      latest: domainOutputs.length > 0 ? domainOutputs[0].created_at : undefined,
    };
  });

  const total = outputs.length;
  const totalPinned = outputs.filter(o => o.pinned).length;

  return {
    result: JSON.stringify({
      total,
      total_pinned: totalPinned,
      domains: stats.filter(s => s.count > 0),
      all_domains: stats,
    }),
  };
}

function handlePin(args: Record<string, unknown>): { result?: string; error?: string } {
  const id = args.id;
  if (!id || typeof id !== 'string') {
    return { error: 'id is required and must be a string' };
  }

  const output = outputs.find(o => o.id === id);
  if (!output) {
    return { error: `Output not found: ${id}` };
  }

  const pinned = args.pinned !== false; // default true
  output.pinned = pinned;

  return {
    result: JSON.stringify({
      id: output.id,
      pinned: output.pinned,
      title: output.title,
    }),
  };
}

function handleExport(args: Record<string, unknown>): { result?: string; error?: string } {
  const domain = args.domain as StageDomain | undefined;
  const includeMetadata = args.include_metadata !== false; // default true

  if (domain && !ALL_DOMAINS.includes(domain)) {
    return { error: `Invalid domain: ${domain}` };
  }

  let filtered = outputs;
  if (domain) {
    filtered = filtered.filter(o => o.domain === domain);
  }

  const exported = filtered.map(o => {
    const entry: Record<string, unknown> = {
      id: o.id,
      domain: o.domain,
      renderer: o.renderer,
      title: o.title,
      prompt: o.prompt,
      source_tool: o.source_tool,
      file_path: o.file_path,
      url: o.url,
      thumbnail: o.thumbnail,
      pinned: o.pinned,
      created_at: o.created_at,
    };
    if (includeMetadata) {
      entry.metadata = o.metadata;
    }
    return entry;
  });

  return {
    result: JSON.stringify({
      exported_at: new Date().toISOString(),
      count: exported.length,
      domain_filter: domain ?? 'all',
      outputs: exported,
    }),
  };
}

// ── execute / detect ─────────────────────────────────────────────────────────

export function execute(
  toolName: string,
  args: Record<string, unknown>,
): { result?: string; error?: string } {
  try {
    switch (toolName) {
      case 'stage_push_output':  return handlePush(args);
      case 'stage_list_outputs': return handleList(args);
      case 'stage_get_output':   return handleGet(args);
      case 'stage_clear_outputs': return handleClear(args);
      case 'stage_get_stats':    return handleStats();
      case 'stage_pin_output':   return handlePin(args);
      case 'stage_export_feed':  return handleExport(args);
      default:
        return { error: `Unknown stage tool: ${toolName}` };
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Stage presenter error: ${msg}` };
  }
}

export function detect(): boolean {
  return true; // always available — UI backbone
}

// ── Exported helpers for testing ─────────────────────────────────────────────

/** Reset the in-memory store (for tests) */
export function _resetStore(): void {
  outputs.length = 0;
  nextId = 1;
}

/** Get current output count (for tests) */
export function _getOutputCount(): number {
  return outputs.length;
}
