/**
 * World Monitor Connector — Real-time global intelligence dashboard.
 *
 * Provides access to World Monitor's 17 API service domains covering
 * geopolitics, conflict, economics, markets, military, cyber, climate,
 * and more. The agent can start the dashboard, query any of the 44 RPC
 * endpoints, and get real-time intelligence on global events.
 *
 * Architecture:
 *   - World Monitor runs as a local Vite dev server on port 3000
 *   - All endpoints are POST /api/{domain}/v1/{rpc}
 *   - The connector manages the server lifecycle and proxies queries
 *
 * Exports: TOOLS, execute, detect
 */

import { ToolDeclaration, ToolResult } from './registry';
import { settingsManager, getSanitizedEnv } from '../settings';
// Crypto Sprint 14: execFile (no shell) + shell.openExternal for URLs.
import { execFile, spawn, ChildProcess } from 'child_process';
import { shell } from 'electron';
import { promisify } from 'util';
import * as path from 'path';
import * as fs from 'fs';
import * as http from 'http';

const execFileAsync = promisify(execFile);

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_WORLD_MONITOR_DIR = path.join(
  'C:', 'Users', 'swebs', 'Downloads', 'worldmonitor-main', 'worldmonitor-main'
);

/** Get the configured World Monitor directory (from settings or default). */
function getWorldMonitorDir(): string {
  try {
    const configured = settingsManager.getWorldMonitorPath();
    return configured || DEFAULT_WORLD_MONITOR_DIR;
  } catch {
    return DEFAULT_WORLD_MONITOR_DIR;
  }
}
const DEV_SERVER_PORT = 3000;
const DEV_SERVER_BASE = `http://localhost:${DEV_SERVER_PORT}`;
const API_BASE = `${DEV_SERVER_BASE}/api`;
const STARTUP_TIMEOUT_MS = 30_000;
const REQUEST_TIMEOUT_MS = 30_000;

// ── Server process management ────────────────────────────────────────

let serverProcess: ChildProcess | null = null;
let serverRunning = false;

// ── Helpers ──────────────────────────────────────────────────────────

function truncate(text: string, maxLen = 12_000): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + `\n\n…[truncated — ${text.length} chars total]`;
}

function ok(text: string): ToolResult {
  return { result: truncate(text.trim()) || '(no output)' };
}

function fail(msg: string): ToolResult {
  return { error: msg };
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

function num(v: unknown, fallback = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Make an HTTP POST request to a World Monitor API endpoint.
 */
function apiPost(domain: string, rpc: string, body: Record<string, unknown> = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    // Convert camelCase RPC name to kebab-case path segment
    const kebab = rpc.replace(/([A-Z])/g, '-$1').toLowerCase().replace(/^-/, '');
    const urlPath = `/api/worldmonitor.${domain}.v1.${capitalizeServiceName(domain)}Service/${kebab.charAt(0).toUpperCase() + kebab.slice(1).replace(/-([a-z])/g, (_, c) => c.toUpperCase())}`;

    // Actually, World Monitor uses a simpler URL scheme based on the sebuf router.
    // Routes are: POST /api/worldmonitor.{domain}.v1.{Service}Service/{RpcName}
    // Let me match the exact route format from the vite.config.ts sebufApiPlugin.

    // The router in vite.config.ts creates routes from the generated server stubs.
    // Route format: POST /api/worldmonitor.{domain}.v1.{ServiceName}Service/{RpcName}
    // Where ServiceName is the capitalized domain and RpcName is the original method name.

    const serviceName = capitalizeServiceName(domain);
    const routePath = `/api/worldmonitor.${domain}.v1.${serviceName}Service/${rpc}`;

    const postData = JSON.stringify(body);

    const req = http.request(
      {
        hostname: 'localhost',
        port: DEV_SERVER_PORT,
        path: routePath,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData),
        },
        timeout: REQUEST_TIMEOUT_MS,
      },
      (res) => {
        let data = '';
        res.on('data', (chunk: Buffer) => { data += chunk.toString(); });
        res.on('end', () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            resolve(data);
          } else {
            reject(new Error(`API ${res.statusCode}: ${data.slice(0, 500)}`));
          }
        });
      }
    );

    req.on('error', (err) => reject(new Error(`Request failed: ${err.message}`)));
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    req.write(postData);
    req.end();
  });
}

function capitalizeServiceName(domain: string): string {
  // Convert domain name to PascalCase service name
  // e.g., 'intelligence' → 'Intelligence', 'cyber' → 'Cyber'
  return domain.charAt(0).toUpperCase() + domain.slice(1);
}

/**
 * Check if the dev server is already running on the expected port.
 */
async function isServerRunning(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.request(
      { hostname: 'localhost', port: DEV_SERVER_PORT, path: '/', method: 'GET', timeout: 3000 },
      (res) => { res.resume(); resolve(true); }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.end();
  });
}

/**
 * Wait for the dev server to become responsive.
 */
async function waitForServer(timeoutMs: number = STARTUP_TIMEOUT_MS): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await isServerRunning()) return true;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return false;
}

// ── Tool declarations ────────────────────────────────────────────────

export const TOOLS: ReadonlyArray<ToolDeclaration> = [
  // ─── Setup & Server Management ───
  {
    name: 'worldmonitor_setup',
    description:
      'Check World Monitor installation status and get setup instructions. Call this when the user wants to use World Monitor intelligence features but it may not be installed yet. Returns step-by-step guidance for whatever is missing.',
    parameters: {
      type: 'object',
      properties: {
        install_path: {
          type: 'string',
          description: 'Optional: custom installation path for World Monitor. If not provided, uses the configured default path.',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_start',
    description:
      'Start the World Monitor real-time intelligence dashboard. Launches the Vite dev server and opens the dashboard in the browser. Must be running before querying any intelligence endpoints.',
    parameters: {
      type: 'object',
      properties: {
        variant: {
          type: 'string',
          description:
            'Dashboard variant: "world" (geopolitics, default), "tech" (AI/startups/cyber), or "finance" (markets/commodities).',
        },
        openBrowser: {
          type: 'boolean',
          description: 'Whether to open the dashboard in the default browser (default: true).',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_stop',
    description: 'Stop the World Monitor dev server and free the port.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_status',
    description:
      'Check whether the World Monitor server is running and responsive. Returns server status and available API domains.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Intelligence & Risk ───
  {
    name: 'worldmonitor_get_risk_scores',
    description:
      'Get the Country Instability Index (CII) risk scores for monitored nations. Returns composite risk scores across political, economic, security, social, and environmental dimensions.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: {
          type: 'string',
          description: 'Optional ISO 3166-1 alpha-2 country code to filter (e.g., "UA", "CN", "IR"). Omit for all monitored countries.',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_get_intel_brief',
    description:
      'Get a comprehensive intelligence briefing for a specific country. Includes threat assessment, recent events, risk trajectory, and key developments.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: {
          type: 'string',
          description: 'ISO 3166-1 alpha-2 country code (e.g., "UA", "CN", "RU", "IR").',
        },
      },
      required: ['countryCode'],
    },
  },
  {
    name: 'worldmonitor_classify_event',
    description:
      'Classify a geopolitical event by threat level, domain, and affected regions using AI analysis.',
    parameters: {
      type: 'object',
      properties: {
        eventDescription: {
          type: 'string',
          description: 'Natural language description of the event to classify.',
        },
      },
      required: ['eventDescription'],
    },
  },
  {
    name: 'worldmonitor_search_gdelt',
    description:
      'Search the GDELT global event database for news and events matching a query. Returns articles, events, and tone analysis.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Search query (e.g., "Taiwan strait military", "Iran nuclear").',
        },
        maxResults: {
          type: 'number',
          description: 'Maximum number of results to return (default: 20).',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'worldmonitor_get_pizzint_status',
    description:
      'Get the PIZZINT (Pizza Intelligence) status — a humorous but real indicator that tracks unusual patterns in government-area food delivery orders as a proxy for crisis activity.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Conflict & Military ───
  {
    name: 'worldmonitor_list_conflicts',
    description:
      'List active armed conflict events worldwide from ACLED (Armed Conflict Location & Event Data). Includes battles, violence against civilians, protests, and strategic developments.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: {
          type: 'string',
          description: 'Optional ISO country code to filter events.',
        },
        limit: {
          type: 'number',
          description: 'Maximum events to return (default: 50).',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_ucdp_events',
    description:
      'List organized violence events from UCDP (Uppsala Conflict Data Program). Academic-grade conflict data with fatality estimates.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'Optional ISO country code filter.' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_get_humanitarian_summary',
    description:
      'Get a humanitarian crisis summary including displacement, casualties, aid access, and civilian impact for a conflict zone.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'ISO country code for the crisis area.' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_military_flights',
    description:
      'Track military and government aircraft flights globally. Includes real-time ADS-B data for reconnaissance, tankers, bombers, and other military assets.',
    parameters: {
      type: 'object',
      properties: {
        region: {
          type: 'string',
          description: 'Optional region filter (e.g., "europe", "pacific", "middle-east").',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_get_theater_posture',
    description:
      'Get the military theater posture assessment — force disposition, readiness levels, and activity patterns for a geographic theater.',
    parameters: {
      type: 'object',
      properties: {
        theater: {
          type: 'string',
          description: 'Theater of operations (e.g., "indo-pacific", "europe", "middle-east").',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_get_fleet_report',
    description:
      'Get the latest USNI Fleet and Force Tracker report — positions and status of major naval vessels worldwide.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Markets & Economics ───
  {
    name: 'worldmonitor_list_market_quotes',
    description:
      'Get real-time market quotes for major stock indices, currencies, and benchmark rates worldwide.',
    parameters: {
      type: 'object',
      properties: {
        symbols: {
          type: 'string',
          description: 'Optional comma-separated ticker symbols to filter.',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_crypto_quotes',
    description: 'Get real-time cryptocurrency prices and market data for major digital assets.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_list_commodity_quotes',
    description:
      'Get commodity prices — oil, gas, gold, silver, agricultural products, and energy benchmarks.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_get_sector_summary',
    description: 'Get a sector-by-sector market performance summary with gainers, losers, and trends.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_list_stablecoin_markets',
    description: 'Get stablecoin market data including USDT, USDC peg status, market caps, and flows.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_list_etf_flows',
    description: 'Get ETF fund flow data — inflows, outflows, and net movements for major ETFs.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_get_country_stock_index',
    description: 'Get stock market index data for a specific country.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'ISO country code (e.g., "US", "JP", "DE").' },
      },
      required: ['countryCode'],
    },
  },
  {
    name: 'worldmonitor_get_macro_signals',
    description:
      'Get macroeconomic signals — GDP, inflation, employment, trade balance, and leading indicators across major economies.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_get_fred_series',
    description:
      'Query specific FRED (Federal Reserve Economic Data) time series for US economic indicators.',
    parameters: {
      type: 'object',
      properties: {
        seriesId: {
          type: 'string',
          description: 'FRED series ID (e.g., "GDP", "UNRATE", "CPIAUCSL", "DFF", "T10Y2Y").',
        },
      },
      required: ['seriesId'],
    },
  },
  {
    name: 'worldmonitor_get_energy_prices',
    description: 'Get real-time energy prices from the EIA — crude oil, natural gas, electricity, and refined products.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Cyber & Infrastructure ───
  {
    name: 'worldmonitor_list_cyber_threats',
    description:
      'Get the latest cyber threat intelligence — active campaigns, APT groups, vulnerabilities, and ransomware incidents.',
    parameters: {
      type: 'object',
      properties: {
        severity: {
          type: 'string',
          description: 'Optional minimum severity filter: "low", "medium", "high", "critical".',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_internet_outages',
    description:
      'Get active internet and infrastructure outages globally — network disruptions, submarine cable issues, DNS anomalies.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_get_cable_health',
    description: 'Get submarine cable infrastructure health — status of undersea fiber-optic cables that carry 95% of global internet traffic.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_list_service_statuses',
    description: 'Get status of major internet services and cloud platforms (AWS, Azure, Google Cloud, Cloudflare, etc.).',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Natural Disasters & Climate ───
  {
    name: 'worldmonitor_list_earthquakes',
    description:
      'Get recent earthquake events worldwide from USGS. Includes magnitude, depth, location, and tsunami warnings.',
    parameters: {
      type: 'object',
      properties: {
        minMagnitude: {
          type: 'number',
          description: 'Minimum magnitude filter (e.g., 4.0 for significant quakes only).',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_wildfires',
    description:
      'Get active wildfire detections worldwide from NASA FIRMS satellite data. Includes fire radiative power and affected areas.',
    parameters: {
      type: 'object',
      properties: {
        region: {
          type: 'string',
          description: 'Optional region filter (e.g., "north-america", "australia", "mediterranean").',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_climate_anomalies',
    description:
      'Get climate anomaly data — temperature extremes, precipitation anomalies, sea surface temps, and climate pattern indices (ENSO, NAO, etc.).',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Maritime & Aviation ───
  {
    name: 'worldmonitor_get_vessel_snapshot',
    description:
      'Get a snapshot of maritime vessel traffic — tankers, cargo ships, military vessels, and their positions via AIS data.',
    parameters: {
      type: 'object',
      properties: {
        region: {
          type: 'string',
          description: 'Optional region (e.g., "south-china-sea", "strait-of-hormuz", "baltic").',
        },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_nav_warnings',
    description: 'Get active navigational warnings for maritime regions — hazards, military exercises, and restricted areas.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  {
    name: 'worldmonitor_list_airport_delays',
    description: 'Get current airport delay and disruption data worldwide.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Displacement & Unrest ───
  {
    name: 'worldmonitor_get_displacement_summary',
    description:
      'Get displacement and refugee crisis data — IDP numbers, refugee flows, and humanitarian corridor status.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'Optional ISO country code filter.' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_get_population_exposure',
    description: 'Get population exposure data — how many people are affected by ongoing crises, natural disasters, or conflicts.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'ISO country code.' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_unrest_events',
    description:
      'Get civil unrest events — protests, riots, strikes, and political demonstrations worldwide.',
    parameters: {
      type: 'object',
      properties: {
        countryCode: { type: 'string', description: 'Optional ISO country code filter.' },
      },
      required: [],
    },
  },

  // ─── News & Research ───
  {
    name: 'worldmonitor_summarize_article',
    description:
      'Summarize a news article using World Monitor\'s AI summarization chain (Ollama → Groq → OpenRouter → browser T5).',
    parameters: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'URL of the article to summarize.',
        },
      },
      required: ['url'],
    },
  },
  {
    name: 'worldmonitor_list_arxiv_papers',
    description: 'Get recent papers from arXiv — AI/ML, physics, math, CS, and other academic research.',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search query for papers (e.g., "large language models", "quantum computing").' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_trending_repos',
    description: 'Get trending GitHub repositories — hot open-source projects across all languages.',
    parameters: {
      type: 'object',
      properties: {
        language: { type: 'string', description: 'Optional language filter (e.g., "python", "rust", "typescript").' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_hackernews',
    description: 'Get top stories from Hacker News — tech news, startup discussions, and engineering insights.',
    parameters: {
      type: 'object',
      properties: {
        limit: { type: 'number', description: 'Number of stories to return (default: 30).' },
      },
      required: [],
    },
  },
  {
    name: 'worldmonitor_list_tech_events',
    description: 'Get upcoming tech events, conferences, and launches.',
    parameters: { type: 'object', properties: {}, required: [] },
  },

  // ─── Prediction Markets ───
  {
    name: 'worldmonitor_list_prediction_markets',
    description:
      'Get prediction market data — probabilities for geopolitical events, elections, policy decisions, and other futures from platforms like Polymarket and Metaculus.',
    parameters: {
      type: 'object',
      properties: {
        category: {
          type: 'string',
          description: 'Optional category filter (e.g., "geopolitics", "elections", "technology", "economics").',
        },
      },
      required: [],
    },
  },
];

// ── RPC routing table ────────────────────────────────────────────────

interface RpcRoute {
  domain: string;
  rpc: string;
  buildBody: (args: Record<string, unknown>) => Record<string, unknown>;
}

const TOOL_ROUTES: Record<string, RpcRoute> = {
  // Intelligence
  worldmonitor_get_risk_scores: {
    domain: 'intelligence',
    rpc: 'getRiskScores',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },
  worldmonitor_get_intel_brief: {
    domain: 'intelligence',
    rpc: 'getCountryIntelBrief',
    buildBody: (a) => ({ countryCode: str(a.countryCode) }),
  },
  worldmonitor_classify_event: {
    domain: 'intelligence',
    rpc: 'classifyEvent',
    buildBody: (a) => ({ eventDescription: str(a.eventDescription) }),
  },
  worldmonitor_search_gdelt: {
    domain: 'intelligence',
    rpc: 'searchGdeltDocuments',
    buildBody: (a) => ({ query: str(a.query), maxResults: num(a.maxResults, 20) }),
  },
  worldmonitor_get_pizzint_status: {
    domain: 'intelligence',
    rpc: 'getPizzintStatus',
    buildBody: () => ({}),
  },

  // Conflict
  worldmonitor_list_conflicts: {
    domain: 'conflict',
    rpc: 'listAcledEvents',
    buildBody: (a) => {
      const body: Record<string, unknown> = {};
      if (a.countryCode) body.countryCode = str(a.countryCode);
      if (a.limit) body.limit = num(a.limit, 50);
      return body;
    },
  },
  worldmonitor_list_ucdp_events: {
    domain: 'conflict',
    rpc: 'listUcdpEvents',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },
  worldmonitor_get_humanitarian_summary: {
    domain: 'conflict',
    rpc: 'getHumanitarianSummary',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },

  // Military
  worldmonitor_list_military_flights: {
    domain: 'military',
    rpc: 'listMilitaryFlights',
    buildBody: (a) => (a.region ? { region: str(a.region) } : {}),
  },
  worldmonitor_get_theater_posture: {
    domain: 'military',
    rpc: 'getTheaterPosture',
    buildBody: (a) => (a.theater ? { theater: str(a.theater) } : {}),
  },
  worldmonitor_get_fleet_report: {
    domain: 'military',
    rpc: 'getUSNIFleetReport',
    buildBody: () => ({}),
  },

  // Market
  worldmonitor_list_market_quotes: {
    domain: 'market',
    rpc: 'listMarketQuotes',
    buildBody: (a) => (a.symbols ? { symbols: str(a.symbols) } : {}),
  },
  worldmonitor_list_crypto_quotes: {
    domain: 'market',
    rpc: 'listCryptoQuotes',
    buildBody: () => ({}),
  },
  worldmonitor_list_commodity_quotes: {
    domain: 'market',
    rpc: 'listCommodityQuotes',
    buildBody: () => ({}),
  },
  worldmonitor_get_sector_summary: {
    domain: 'market',
    rpc: 'getSectorSummary',
    buildBody: () => ({}),
  },
  worldmonitor_list_stablecoin_markets: {
    domain: 'market',
    rpc: 'listStablecoinMarkets',
    buildBody: () => ({}),
  },
  worldmonitor_list_etf_flows: {
    domain: 'market',
    rpc: 'listEtfFlows',
    buildBody: () => ({}),
  },
  worldmonitor_get_country_stock_index: {
    domain: 'market',
    rpc: 'getCountryStockIndex',
    buildBody: (a) => ({ countryCode: str(a.countryCode) }),
  },

  // Economic
  worldmonitor_get_macro_signals: {
    domain: 'economic',
    rpc: 'getMacroSignals',
    buildBody: () => ({}),
  },
  worldmonitor_get_fred_series: {
    domain: 'economic',
    rpc: 'getFredSeries',
    buildBody: (a) => ({ seriesId: str(a.seriesId) }),
  },
  worldmonitor_get_energy_prices: {
    domain: 'economic',
    rpc: 'getEnergyPrices',
    buildBody: () => ({}),
  },

  // Cyber
  worldmonitor_list_cyber_threats: {
    domain: 'cyber',
    rpc: 'listCyberThreats',
    buildBody: (a) => (a.severity ? { severity: str(a.severity) } : {}),
  },

  // Infrastructure
  worldmonitor_list_internet_outages: {
    domain: 'infrastructure',
    rpc: 'listInternetOutages',
    buildBody: () => ({}),
  },
  worldmonitor_get_cable_health: {
    domain: 'infrastructure',
    rpc: 'getCableHealth',
    buildBody: () => ({}),
  },
  worldmonitor_list_service_statuses: {
    domain: 'infrastructure',
    rpc: 'listServiceStatuses',
    buildBody: () => ({}),
  },

  // Seismology
  worldmonitor_list_earthquakes: {
    domain: 'seismology',
    rpc: 'listEarthquakes',
    buildBody: (a) => (a.minMagnitude ? { minMagnitude: num(a.minMagnitude) } : {}),
  },

  // Wildfire
  worldmonitor_list_wildfires: {
    domain: 'wildfire',
    rpc: 'listFireDetections',
    buildBody: (a) => (a.region ? { region: str(a.region) } : {}),
  },

  // Climate
  worldmonitor_list_climate_anomalies: {
    domain: 'climate',
    rpc: 'listClimateAnomalies',
    buildBody: () => ({}),
  },

  // Maritime
  worldmonitor_get_vessel_snapshot: {
    domain: 'maritime',
    rpc: 'getVesselSnapshot',
    buildBody: (a) => (a.region ? { region: str(a.region) } : {}),
  },
  worldmonitor_list_nav_warnings: {
    domain: 'maritime',
    rpc: 'listNavigationalWarnings',
    buildBody: () => ({}),
  },

  // Aviation
  worldmonitor_list_airport_delays: {
    domain: 'aviation',
    rpc: 'listAirportDelays',
    buildBody: () => ({}),
  },

  // Displacement
  worldmonitor_get_displacement_summary: {
    domain: 'displacement',
    rpc: 'getDisplacementSummary',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },
  worldmonitor_get_population_exposure: {
    domain: 'displacement',
    rpc: 'getPopulationExposure',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },

  // Unrest
  worldmonitor_list_unrest_events: {
    domain: 'unrest',
    rpc: 'listUnrestEvents',
    buildBody: (a) => (a.countryCode ? { countryCode: str(a.countryCode) } : {}),
  },

  // News
  worldmonitor_summarize_article: {
    domain: 'news',
    rpc: 'summarizeArticle',
    buildBody: (a) => ({ url: str(a.url) }),
  },

  // Research
  worldmonitor_list_arxiv_papers: {
    domain: 'research',
    rpc: 'listArxivPapers',
    buildBody: (a) => (a.query ? { query: str(a.query) } : {}),
  },
  worldmonitor_list_trending_repos: {
    domain: 'research',
    rpc: 'listTrendingRepos',
    buildBody: (a) => (a.language ? { language: str(a.language) } : {}),
  },
  worldmonitor_list_hackernews: {
    domain: 'research',
    rpc: 'listHackernewsItems',
    buildBody: (a) => (a.limit ? { limit: num(a.limit, 30) } : {}),
  },
  worldmonitor_list_tech_events: {
    domain: 'research',
    rpc: 'listTechEvents',
    buildBody: () => ({}),
  },

  // Prediction
  worldmonitor_list_prediction_markets: {
    domain: 'prediction',
    rpc: 'listPredictionMarkets',
    buildBody: (a) => (a.category ? { category: str(a.category) } : {}),
  },
};

// ── Tool implementations ─────────────────────────────────────────────

async function setupCheck(args: Record<string, unknown>): Promise<string> {
  // If a custom path is provided, update settings
  if (typeof args.install_path === 'string' && args.install_path.trim()) {
    const customPath = args.install_path.trim();
    try {
      await settingsManager.setWorldMonitorPath(customPath);
    } catch {
      // Non-critical — just use the path for this check
    }
  }

  const status = isInstalled();
  const running = await isServerRunning();

  const lines: string[] = ['## World Monitor Setup Status\n'];

  if (running) {
    lines.push('✅ **Status: RUNNING** — World Monitor is installed and the server is active at http://localhost:3000');
    lines.push(`📁 Install path: ${status.dir}`);
    lines.push('\nAll 17 intelligence domains and 44 RPC endpoints are available. You can query any of them now.');
    return lines.join('\n');
  }

  if (status.installed && status.hasNodeModules) {
    lines.push('✅ **Installed** — World Monitor is installed and dependencies are ready.');
    lines.push(`📁 Install path: ${status.dir}`);
    lines.push('⚠️ **Server not running** — Use `worldmonitor_start` to launch the dashboard and enable all intelligence endpoints.');
    return lines.join('\n');
  }

  if (status.installed && !status.hasNodeModules) {
    lines.push('⚠️ **Partially installed** — World Monitor source code found but dependencies are not installed.');
    lines.push(`📁 Install path: ${status.dir}`);
    lines.push('\n### Next Step:');
    lines.push('Run `npm install` in the World Monitor directory to install dependencies:');
    lines.push(`\`\`\`\ncd "${status.dir}"\nnpm install\n\`\`\``);
    lines.push('\nAfter that, use `worldmonitor_start` to launch the dashboard.');
    return lines.join('\n');
  }

  // Not installed at all
  lines.push('❌ **Not installed** — World Monitor was not found on this system.');
  lines.push(`📁 Expected path: ${status.dir}`);
  lines.push('\n### Setup Instructions:\n');
  lines.push('World Monitor is a real-time global intelligence dashboard with 17 domains covering conflicts, markets, military, cyber, climate, and more.\n');
  lines.push('**Step 1 — Download:**');
  lines.push('Download or clone the World Monitor repository from its source and place it in a folder on your computer.\n');
  lines.push('**Step 2 — Set the path:**');
  lines.push('Tell me the full path where you placed World Monitor, and I\'ll update the configuration.\n');
  lines.push('**Step 3 — Install dependencies:**');
  lines.push('Open a terminal in the World Monitor directory and run: `npm install`\n');
  lines.push('**Step 4 — Launch:**');
  lines.push('Once installed, just ask me to start World Monitor and I\'ll handle the rest. You can say "start World Monitor" or I\'ll use `worldmonitor_start` automatically when you ask about global events.\n');
  lines.push('Would you like help with any of these steps?');

  return lines.join('\n');
}

async function startServer(args: Record<string, unknown>): Promise<string> {
  // Check if already running
  if (await isServerRunning()) {
    serverRunning = true;
    return 'World Monitor is already running at http://localhost:3000';
  }

  // Check that the directory exists
  const wmDir = getWorldMonitorDir();
  if (!fs.existsSync(path.join(wmDir, 'package.json'))) {
    return 'ERROR: World Monitor not found at expected path. Run worldmonitor_setup for installation instructions.';
  }

  const variant = str(args.variant, 'world');
  const openBrowser = args.openBrowser !== false;

  // Set environment variable for variant
  // Crypto Sprint 15 (HIGH): Use getSanitizedEnv() to prevent API key leakage to Vite server.
  const env = { ...getSanitizedEnv(), VITE_VARIANT: variant } as NodeJS.ProcessEnv;

  // Spawn the dev server
  // Crypto Sprint 3: shell only on Windows (npm.cmd needs cmd.exe).
  // Args are hardcoded so this is low-risk, but belt-and-suspenders.
  const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  serverProcess = spawn(npmBin, ['run', 'dev'], {
    cwd: wmDir,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: process.platform === 'win32',
    detached: false,
  });

  // Log output for debugging
  serverProcess.stdout?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.log(`[WorldMonitor] ${line}`);
  });
  serverProcess.stderr?.on('data', (data: Buffer) => {
    const line = data.toString().trim();
    if (line) console.warn(`[WorldMonitor] ${line}`);
  });

  serverProcess.on('exit', (code) => {
    console.log(`[WorldMonitor] Dev server exited with code ${code}`);
    serverProcess = null;
    serverRunning = false;
  });

  // Wait for server to become responsive
  const ready = await waitForServer();
  if (!ready) {
    // Kill if startup timed out
    serverProcess?.kill();
    serverProcess = null;
    return 'ERROR: World Monitor server failed to start within 30 seconds. Check the console for errors.';
  }

  serverRunning = true;

  // Open in browser if requested
  if (openBrowser) {
    try {
      // Crypto Sprint 14: shell.openExternal — safe URL opening, no shell interpolation.
      await shell.openExternal(DEV_SERVER_BASE);
    } catch {
      // Non-critical — dashboard is still accessible
    }
  }

  return `World Monitor started successfully!\n` +
    `Dashboard: ${DEV_SERVER_BASE}\n` +
    `Variant: ${variant}\n` +
    `API: ${API_BASE}/worldmonitor.{domain}.v1.{Service}Service/{rpc}\n` +
    `17 service domains with 44 RPC endpoints are now available.`;
}

async function stopServer(): Promise<string> {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
    serverRunning = false;
    return 'World Monitor server stopped.';
  }

  // Try to kill any existing process on port 3000
  try {
    // Crypto Sprint 14: execFile with array args — no shell interpolation.
    await execFileAsync('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-Command',
      'Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }',
    ]);
    serverRunning = false;
    return 'World Monitor server stopped (killed process on port 3000).';
  } catch {
    serverRunning = false;
    return 'World Monitor server was not running.';
  }
}

async function getStatus(): Promise<string> {
  const running = await isServerRunning();
  serverRunning = running;

  if (!running) {
    return 'World Monitor is NOT running. Use worldmonitor_start to launch the dashboard and enable all intelligence endpoints.';
  }

  const domains = [
    'aviation', 'climate', 'conflict', 'cyber', 'displacement', 'economic',
    'infrastructure', 'intelligence', 'maritime', 'market', 'military',
    'news', 'prediction', 'research', 'seismology', 'unrest', 'wildfire',
  ];

  return `World Monitor is RUNNING at ${DEV_SERVER_BASE}\n\n` +
    `Available service domains (${domains.length}):\n` +
    domains.map((d) => `  • ${d}`).join('\n') +
    `\n\n44 RPC endpoints ready for queries.`;
}

async function executeApiCall(toolName: string, args: Record<string, unknown>): Promise<string> {
  // Ensure server is running
  if (!serverRunning && !(await isServerRunning())) {
    return `ERROR: World Monitor is not running. Call worldmonitor_start first to launch the dashboard.`;
  }

  const route = TOOL_ROUTES[toolName];
  if (!route) {
    return `ERROR: Unknown tool route: ${toolName}`;
  }

  const body = route.buildBody(args);
  const rawResponse = await apiPost(route.domain, route.rpc, body);

  // Try to pretty-format JSON responses
  try {
    const parsed = JSON.parse(rawResponse);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return rawResponse;
  }
}

// ── Public exports ───────────────────────────────────────────────────

export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    let result: string;

    switch (toolName) {
      case 'worldmonitor_setup':
        result = await setupCheck(args);
        break;
      case 'worldmonitor_start':
        result = await startServer(args);
        break;
      case 'worldmonitor_stop':
        result = await stopServer();
        break;
      case 'worldmonitor_status':
        result = await getStatus();
        break;
      default: {
        // All other tools route through the API
        if (TOOL_ROUTES[toolName]) {
          result = await executeApiCall(toolName, args);
        } else {
          return { error: `Unknown world-monitor tool: ${toolName}` };
        }
      }
    }

    return ok(result);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return { error: `world-monitor "${toolName}" failed: ${message}` };
  }
}

export async function detect(): Promise<boolean> {
  // Always return true — the worldmonitor_setup tool should always be available
  // so the agent can guide users through installation even if World Monitor isn't installed yet.
  // The individual intelligence tools will check server status at call time.
  return true;
}

/**
 * Check if World Monitor is actually installed at the configured path.
 * Used by worldmonitor_setup and startServer to give accurate status.
 */
function isInstalled(): { installed: boolean; hasNodeModules: boolean; dir: string } {
  const dir = getWorldMonitorDir();
  try {
    const pkgPath = path.join(dir, 'package.json');
    if (!fs.existsSync(pkgPath)) return { installed: false, hasNodeModules: false, dir };

    const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
    if (pkg.name !== 'world-monitor') return { installed: false, hasNodeModules: false, dir };

    const hasNodeModules = fs.existsSync(path.join(dir, 'node_modules'));
    return { installed: true, hasNodeModules, dir };
  } catch {
    return { installed: false, hasNodeModules: false, dir };
  }
}
