/**
 * Firecrawl Connector — Web search, scraping, and crawling via Firecrawl API.
 *
 * Provides three tools for web intelligence:
 *   - web_search:  Search the internet for information
 *   - web_scrape:  Extract content from a specific URL as markdown
 *   - web_crawl:   Crawl an entire website starting from a URL
 *
 * All calls go through the Firecrawl v2 REST API (https://api.firecrawl.dev/v2).
 * Authentication via Bearer token from settings.
 *
 * Exports: TOOLS, execute, detect
 */

import { ToolDeclaration, ToolResult } from './registry';
import { settingsManager } from '../settings';
import * as https from 'https';

// ── Constants ────────────────────────────────────────────────────────

const API_BASE = 'api.firecrawl.dev';
const API_VERSION = 'v2';
const REQUEST_TIMEOUT_MS = 60_000;
const MAX_CONTENT_CHARS = 15_000;
const MAX_SNIPPET_CHARS = 1_500;
const CRAWL_POLL_INTERVAL_MS = 3_000;
const CRAWL_MAX_WAIT_MS = 120_000;

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
  return settingsManager.getFirecrawlApiKey();
}

/**
 * Make an HTTPS request to the Firecrawl API.
 */
function apiRequest(
  method: 'GET' | 'POST',
  path: string,
  body?: Record<string, unknown>
): Promise<{ status: number; data: any }> {
  return new Promise((resolve, reject) => {
    const apiKey = getApiKey();
    if (!apiKey) {
      reject(new Error('Firecrawl API key not configured. Set it in settings.'));
      return;
    }

    const postData = body ? JSON.stringify(body) : undefined;

    const options: https.RequestOptions = {
      hostname: API_BASE,
      port: 443,
      path: `/${API_VERSION}${path}`,
      method,
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        ...(postData ? { 'Content-Length': Buffer.byteLength(postData) } : {}),
      },
      timeout: REQUEST_TIMEOUT_MS,
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

    req.on('error', (err) => reject(new Error(`Firecrawl request failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Firecrawl request timed out')); });

    if (postData) req.write(postData);
    req.end();
  });
}

// ── Tool implementations ─────────────────────────────────────────────

async function webSearch(args: Record<string, unknown>): Promise<string> {
  const query = typeof args.query === 'string' ? args.query : '';
  if (!query) return 'ERROR: search query is required.';

  const limit = Math.min(Math.max(Number(args.limit) || 5, 1), 20);

  const { status, data } = await apiRequest('POST', '/search', {
    query,
    limit,
    scrapeOptions: {
      formats: ['markdown'],
      onlyMainContent: true,
    },
  });

  if (status === 401) return 'ERROR: Firecrawl API key is invalid. Check your settings.';
  if (status === 429) return 'ERROR: Firecrawl rate limit exceeded. Try again in a moment.';
  if (status !== 200 || !data.success) {
    return `ERROR: Firecrawl search failed (${status}): ${data.error || JSON.stringify(data).slice(0, 500)}`;
  }

  // Format results
  const results: string[] = [];

  // Handle web results (primary)
  const webResults = data.data || [];
  if (Array.isArray(webResults) && webResults.length > 0) {
    for (let i = 0; i < webResults.length; i++) {
      const r = webResults[i];
      const title = r.title || r.metadata?.title || 'Untitled';
      const url = r.url || r.metadata?.sourceURL || '';
      const snippet = truncate(
        r.markdown || r.description || r.metadata?.description || '',
        MAX_SNIPPET_CHARS
      );
      results.push(`### ${i + 1}. ${title}\n**URL:** ${url}\n\n${snippet}`);
    }
  }

  if (results.length === 0) return `No results found for: "${query}"`;

  return `## Search Results for: "${query}"\n\n${results.join('\n\n---\n\n')}`;
}

async function webScrape(args: Record<string, unknown>): Promise<string> {
  const url = typeof args.url === 'string' ? args.url : '';
  if (!url) return 'ERROR: URL is required.';

  const onlyMainContent = args.onlyMainContent !== false; // default true

  const { status, data } = await apiRequest('POST', '/scrape', {
    url,
    formats: ['markdown'],
    onlyMainContent,
  });

  if (status === 401) return 'ERROR: Firecrawl API key is invalid. Check your settings.';
  if (status === 429) return 'ERROR: Firecrawl rate limit exceeded. Try again in a moment.';
  if (status !== 200 || !data.success) {
    return `ERROR: Firecrawl scrape failed (${status}): ${data.error || JSON.stringify(data).slice(0, 500)}`;
  }

  const pageData = data.data || {};
  const title = pageData.metadata?.title || 'Untitled';
  const sourceUrl = pageData.metadata?.sourceURL || url;
  const markdown = truncate(pageData.markdown || '(no content extracted)', MAX_CONTENT_CHARS);

  return `## ${title}\n**URL:** ${sourceUrl}\n\n${markdown}`;
}

async function webCrawl(args: Record<string, unknown>): Promise<string> {
  const url = typeof args.url === 'string' ? args.url : '';
  if (!url) return 'ERROR: URL is required.';

  const limit = Math.min(Math.max(Number(args.limit) || 5, 1), 20);

  // Start crawl job
  const startResult = await apiRequest('POST', '/crawl', {
    url,
    limit,
    scrapeOptions: {
      formats: ['markdown'],
      onlyMainContent: true,
    },
  });

  if (startResult.status === 401) return 'ERROR: Firecrawl API key is invalid.';
  if (startResult.status === 429) return 'ERROR: Firecrawl rate limit exceeded.';
  if (startResult.status !== 200 || !startResult.data.success) {
    return `ERROR: Firecrawl crawl failed (${startResult.status}): ${startResult.data.error || JSON.stringify(startResult.data).slice(0, 500)}`;
  }

  const jobId = startResult.data.id;
  if (!jobId) return 'ERROR: No crawl job ID returned.';

  // Poll for completion
  const startTime = Date.now();
  while (Date.now() - startTime < CRAWL_MAX_WAIT_MS) {
    await new Promise((r) => setTimeout(r, CRAWL_POLL_INTERVAL_MS));

    const pollResult = await apiRequest('GET', `/crawl/${jobId}`);

    if (pollResult.data.status === 'completed') {
      const pages = pollResult.data.data || [];
      if (!Array.isArray(pages) || pages.length === 0) {
        return `Crawl completed but no pages were extracted from: ${url}`;
      }

      const formatted = pages.map((page: any, i: number) => {
        const title = page.metadata?.title || `Page ${i + 1}`;
        const pageUrl = page.metadata?.sourceURL || '';
        const content = truncate(page.markdown || '', MAX_SNIPPET_CHARS * 2);
        return `### ${i + 1}. ${title}\n**URL:** ${pageUrl}\n\n${content}`;
      });

      return `## Crawl Results for: ${url}\n**Pages crawled:** ${pages.length}\n\n${formatted.join('\n\n---\n\n')}`;
    }

    if (pollResult.data.status === 'failed') {
      return `ERROR: Crawl job failed: ${pollResult.data.error || 'Unknown error'}`;
    }

    // Still running — continue polling
  }

  return `Crawl job ${jobId} is still running after ${CRAWL_MAX_WAIT_MS / 1000}s. The results may be available later.`;
}

// ── Tool declarations ────────────────────────────────────────────────

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  {
    name: 'web_search',
    description:
      'Search the internet for current information, news, documentation, or answers to questions. Returns a list of relevant results with titles, URLs, and content snippets.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'The search query — be specific for better results.',
        },
        limit: {
          type: 'number',
          description: 'Maximum number of results to return (1-20, default: 5).',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'web_scrape',
    description:
      'Extract the full content of a web page as clean markdown. Use this when you need to read an article, documentation page, or any URL in detail.',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'The URL of the page to scrape.',
        },
        onlyMainContent: {
          type: 'boolean',
          description: 'If true (default), strips headers, footers, navigation — returns only the main content.',
        },
      },
      required: ['url'],
    },
  },
  {
    name: 'web_crawl',
    description:
      'Crawl an entire website starting from a URL. Discovers and scrapes multiple pages. Use for comprehensive site coverage — documentation sites, wikis, etc. This is an async operation and may take up to 2 minutes.',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'The starting URL to crawl from.',
        },
        limit: {
          type: 'number',
          description: 'Maximum number of pages to crawl (1-20, default: 5).',
        },
      },
      required: ['url'],
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
      case 'web_search':
        return ok(await webSearch(args));
      case 'web_scrape':
        return ok(await webScrape(args));
      case 'web_crawl':
        return ok(await webCrawl(args));
      default:
        return fail(`Unknown firecrawl tool: ${toolName}`);
    }
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return fail(`firecrawl "${toolName}" failed: ${message}`);
  }
}

export async function detect(): Promise<boolean> {
  // Firecrawl is a cloud API — available if we have an API key
  const key = getApiKey();
  return !!key && key.length > 0;
}
