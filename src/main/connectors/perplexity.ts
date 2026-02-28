/**
 * Perplexity Connector — AI-powered search, research, and reasoning via Perplexity API.
 *
 * Provides four tools across different intelligence tiers:
 *   - perplexity_search:        Fast web search with citations (Sonar)
 *   - perplexity_research:      Deep comprehensive research (Sonar Pro)
 *   - perplexity_deep_research: Async multi-step investigation (Sonar Deep Research)
 *   - perplexity_reason:        Search-augmented reasoning for complex analysis (Sonar Reasoning Pro)
 *
 * All calls go through the Perplexity API (OpenAI-compatible chat/completions endpoint).
 * Authentication via Bearer token from settings.
 *
 * Exports: TOOLS, execute, detect
 */

import { ToolDeclaration, ToolResult } from './registry';
import { settingsManager } from '../settings';
import * as https from 'https';

// ── Constants ────────────────────────────────────────────────────────

const API_HOST = 'api.perplexity.ai';
const REQUEST_TIMEOUT_MS = 90_000;
const DEEP_RESEARCH_TIMEOUT_MS = 300_000; // 5 min for deep research
const MAX_RESPONSE_CHARS = 20_000;

// ── Model tiers ──────────────────────────────────────────────────────

const MODELS = {
  search: 'sonar',                    // Fast search — quick lookups, current info
  research: 'sonar-pro',              // Comprehensive search — detailed research
  deepResearch: 'sonar-deep-research', // Multi-step async investigation
  reasoning: 'sonar-reasoning-pro',    // Search + chain-of-thought reasoning
} as const;

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
  return settingsManager.getPerplexityApiKey();
}

/**
 * Make an HTTPS request to the Perplexity API (OpenAI-compatible format).
 */
function apiRequest(
  body: Record<string, unknown>,
  timeoutMs: number = REQUEST_TIMEOUT_MS
): Promise<{ status: number; data: any }> {
  return new Promise((resolve, reject) => {
    const apiKey = getApiKey();
    if (!apiKey) {
      reject(new Error('Perplexity API key not configured. Set it in settings.'));
      return;
    }

    const postData = JSON.stringify(body);

    const options: https.RequestOptions = {
      hostname: API_HOST,
      port: 443,
      path: '/chat/completions',
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

    req.on('error', (err) => reject(new Error(`Perplexity request failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Perplexity request timed out')); });

    req.write(postData);
    req.end();
  });
}

/**
 * Format citations from Perplexity response into a readable block.
 */
function formatCitations(citations: string[] | undefined): string {
  if (!citations || citations.length === 0) return '';
  const formatted = citations.map((url, i) => `[${i + 1}] ${url}`).join('\n');
  return `\n\n---\n**Sources:**\n${formatted}`;
}

/**
 * Format related questions if returned.
 */
function formatRelated(questions: string[] | undefined): string {
  if (!questions || questions.length === 0) return '';
  const formatted = questions.map((q, i) => `${i + 1}. ${q}`).join('\n');
  return `\n\n**Related questions:**\n${formatted}`;
}

// ── Tool implementations ─────────────────────────────────────────────

async function perplexitySearch(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  if (!query) return 'ERROR: search query is required.';

  // Build request body
  const body: Record<string, unknown> = {
    model: MODELS.search,
    messages: [
      { role: 'system', content: 'Be precise and concise. Provide factual, well-sourced answers.' },
      { role: 'user', content: query },
    ],
    return_citations: true,
    return_related_questions: true,
  };

  // Optional filters
  if (args.domains && Array.isArray(args.domains)) {
    body.search_domain_filter = args.domains;
  }
  if (typeof args.recency === 'string' && ['month', 'week', 'day', 'hour'].includes(args.recency)) {
    body.search_recency_filter = args.recency;
  }
  if (typeof args.focus === 'string') {
    body.search_context_size = args.focus === 'high' ? 'high' : 'low';
  }

  const { status, data } = await apiRequest(body);

  if (status === 401) return 'ERROR: Perplexity API key is invalid. Check your settings.';
  if (status === 429) return 'ERROR: Perplexity rate limit exceeded. Try again in a moment.';
  if (status !== 200) {
    return `ERROR: Perplexity search failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const content = data.choices?.[0]?.message?.content || '(no content returned)';
  const citations = formatCitations(data.citations);
  const related = formatRelated(data.related_questions);

  return truncate(`## Search: "${query}"\n\n${content}${citations}${related}`, MAX_RESPONSE_CHARS);
}

async function perplexityResearch(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  if (!query) return 'ERROR: research query is required.';

  const body: Record<string, unknown> = {
    model: MODELS.research,
    messages: [
      {
        role: 'system',
        content: 'You are a thorough research assistant. Provide comprehensive, well-structured analysis with multiple perspectives. Cite all sources.',
      },
      { role: 'user', content: query },
    ],
    return_citations: true,
    return_related_questions: true,
    search_context_size: 'high',
  };

  if (args.domains && Array.isArray(args.domains)) {
    body.search_domain_filter = args.domains;
  }
  if (typeof args.recency === 'string' && ['month', 'week', 'day', 'hour'].includes(args.recency)) {
    body.search_recency_filter = args.recency;
  }

  const { status, data } = await apiRequest(body);

  if (status === 401) return 'ERROR: Perplexity API key is invalid.';
  if (status === 429) return 'ERROR: Perplexity rate limit exceeded.';
  if (status !== 200) {
    return `ERROR: Perplexity research failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const content = data.choices?.[0]?.message?.content || '(no content returned)';
  const citations = formatCitations(data.citations);
  const related = formatRelated(data.related_questions);

  return truncate(`## Research: "${query}"\n\n${content}${citations}${related}`, MAX_RESPONSE_CHARS);
}

async function perplexityDeepResearch(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  if (!query) return 'ERROR: research query is required.';

  const body: Record<string, unknown> = {
    model: MODELS.deepResearch,
    messages: [
      {
        role: 'system',
        content: 'Conduct a thorough, multi-step investigation. Cross-reference multiple sources, identify conflicts in information, and provide a synthesis with confidence levels for each claim.',
      },
      { role: 'user', content: query },
    ],
    return_citations: true,
  };

  const { status, data } = await apiRequest(body, DEEP_RESEARCH_TIMEOUT_MS);

  if (status === 401) return 'ERROR: Perplexity API key is invalid.';
  if (status === 429) return 'ERROR: Perplexity rate limit exceeded.';
  if (status !== 200) {
    return `ERROR: Perplexity deep research failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const content = data.choices?.[0]?.message?.content || '(no content returned)';
  const citations = formatCitations(data.citations);

  return truncate(`## Deep Research: "${query}"\n\n${content}${citations}`, MAX_RESPONSE_CHARS);
}

async function perplexityReason(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  if (!query) return 'ERROR: reasoning query is required.';

  const body: Record<string, unknown> = {
    model: MODELS.reasoning,
    messages: [
      {
        role: 'system',
        content: 'Think step by step. Search for relevant information, then reason through the problem methodically. Show your reasoning chain and cite sources for factual claims.',
      },
      { role: 'user', content: query },
    ],
    return_citations: true,
  };

  const { status, data } = await apiRequest(body, DEEP_RESEARCH_TIMEOUT_MS);

  if (status === 401) return 'ERROR: Perplexity API key is invalid.';
  if (status === 429) return 'ERROR: Perplexity rate limit exceeded.';
  if (status !== 200) {
    return `ERROR: Perplexity reasoning failed (${status}): ${data.error?.message || JSON.stringify(data).slice(0, 500)}`;
  }

  const content = data.choices?.[0]?.message?.content || '(no content returned)';
  const citations = formatCitations(data.citations);

  return truncate(`## Reasoning: "${query}"\n\n${content}${citations}`, MAX_RESPONSE_CHARS);
}

// ── Tool declarations ────────────────────────────────────────────────

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  {
    name: 'perplexity_search',
    description:
      'Fast AI-powered web search with citations. Returns concise, factual answers sourced from the internet. ' +
      'Best for: current events, quick facts, documentation lookups, "what is X" questions. ' +
      'Cheaper and faster than perplexity_research — use this as the default for web queries.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The search query — be specific for better results.',
        },
        domains: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional domain filter — restrict results to these domains (e.g. ["reddit.com", "stackoverflow.com"]).',
        },
        recency: {
          type: 'string',
          description: 'Optional time filter: "hour", "day", "week", or "month". Limits results to content published within this window.',
        },
        focus: {
          type: 'string',
          description: 'Search depth: "low" (faster, less context) or "high" (slower, more comprehensive context). Default: standard.',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'perplexity_research',
    description:
      'Comprehensive AI-powered research with deep source analysis. Returns thorough, multi-perspective analysis with full citations. ' +
      'Best for: detailed research questions, comparing options, understanding complex topics, technical deep-dives. ' +
      'More expensive than perplexity_search — use when depth matters more than speed.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The research question — more detail produces better results.',
        },
        domains: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional domain filter — restrict sources to these domains.',
        },
        recency: {
          type: 'string',
          description: 'Optional time filter: "hour", "day", "week", or "month".',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'perplexity_deep_research',
    description:
      'Multi-step deep investigation that cross-references many sources. Takes longer (up to 5 minutes) but produces ' +
      'the most thorough analysis possible. Best for: investigative research, competitive analysis, due diligence, ' +
      'complex technical questions requiring synthesis across many sources. Most expensive tier — use sparingly for high-value queries.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The investigation topic — be very specific about what you need to know and why.',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'perplexity_reason',
    description:
      'Search-augmented reasoning that combines web research with step-by-step logical analysis. ' +
      'Searches for relevant data, then reasons through the problem methodically with citations. ' +
      'Best for: complex analytical questions, comparing contradictory information, technical problem-solving ' +
      'that requires both facts and logic. Use when the answer requires thinking, not just finding.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The reasoning question — explain the problem and what analysis you need.',
        },
      },
      required: ['query'],
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
      case 'perplexity_search':
        return ok(await perplexitySearch(args));
      case 'perplexity_research':
        return ok(await perplexityResearch(args));
      case 'perplexity_deep_research':
        return ok(await perplexityDeepResearch(args));
      case 'perplexity_reason':
        return ok(await perplexityReason(args));
      default:
        return fail(`Unknown perplexity tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return fail(`perplexity "${toolName}" failed: ${message}`);
  }
}

export async function detect(): Promise<boolean> {
  const key = getApiKey();
  return !!key && key.length > 0;
}
