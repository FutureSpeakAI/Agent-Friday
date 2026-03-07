/**
 * Sprint 3 Integration: "The Living Mind" -- Local Intelligence Circle
 *
 * End-to-end integration test validating the full local intelligence pipeline:
 * OllamaProvider -> EmbeddingPipeline -> OllamaLifecycle
 *   -> ConfidenceAssessor -> CloudGate -> routeLocalFirst
 *
 * These tests exercise the REAL module logic with mocked HTTP (fetch)
 * and Electron boundaries (IPC, BrowserWindow, settings).
 *
 * 10 integration criteria prove the local-first intelligence pipeline
 * functions as a complete, sovereign system.
 *
 * Sprint 3 I.1: "The Living Mind" -- Local Intelligence Circle
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// -- Hoisted mocks (vi.mock is hoisted, so variables must be too) --

const mocks = vi.hoisted(() => ({
  ipcOnce: vi.fn(),
  ipcHandle: vi.fn(),
  ipcOn: vi.fn(),
  settingsGet: vi.fn(() => ({})),
  setSetting: vi.fn(() => Promise.resolve()),
  webContentsSend: vi.fn(),
}));

vi.mock('electron', () => ({
  app: {
    isPackaged: false,
    getPath: vi.fn(() => '/tmp/test'),
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: {
    handle: mocks.ipcHandle,
    on: mocks.ipcOn,
    once: mocks.ipcOnce,
  },
  BrowserWindow: vi.fn(),
  nativeTheme: { shouldUseDarkColors: true, on: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn(), showSaveDialog: vi.fn() },
  screen: {
    getPrimaryDisplay: vi.fn(() => ({
      workAreaSize: { width: 1920, height: 1080 },
    })),
  },
}));

vi.mock('../../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    setSetting: mocks.setSetting,
  },
}));

import { OllamaProvider } from '../../../src/main/providers/ollama-provider';
import { EmbeddingPipeline } from '../../../src/main/embedding-pipeline';
import { OllamaLifecycle } from '../../../src/main/ollama-lifecycle';
import { assessConfidence, type ConfidenceResult } from '../../../src/main/confidence-assessor';
import { CloudGate, type EscalationContext, type GateDecision } from '../../../src/main/cloud-gate';
import {
  routeLocalFirst,
  llmClient,
  type LLMRequest,
  type LLMResponse,
  type LLMProvider,
  type RoutingEvent,
} from '../../../src/main/llm-client';
import type { ProviderName } from '../../../src/main/intelligence-router';

let fetchMock: ReturnType<typeof vi.fn>;

function mockFetchResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    body: null,
    bodyUsed: false,
    clone: vi.fn(),
    arrayBuffer: vi.fn(),
    blob: vi.fn(),
    formData: vi.fn(),
    redirected: false,
    type: 'basic' as ResponseType,
    url: '',
  } as unknown as Response;
}

function setupFetchForOllama(): void {
  fetchMock = vi.fn((url: string | URL | Request, _init?: RequestInit) => {
    const urlStr = typeof url === 'string' ? url : url instanceof URL ? url.toString() : url.url;
    if (urlStr.includes('/api/chat')) {
      return Promise.resolve(mockFetchResponse({
        model: 'llama3.2', message: { role: 'assistant', content: 'This is a locally generated response from Ollama.' },
        done: true, prompt_eval_count: 42, eval_count: 18,
      }));
    }
    if (urlStr.includes('/api/embed')) {
      return Promise.resolve(mockFetchResponse({ embeddings: [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]] }));
    }
    if (urlStr.includes('/api/tags')) {
      return Promise.resolve(mockFetchResponse({
        models: [
          { name: 'llama3.2', model: 'llama3.2', size: 2_000_000_000, digest: 'abc123', modified_at: '2025-01-01T00:00:00Z', details: { parameter_size: '3B', family: 'llama' } },
          { name: 'nomic-embed-text', model: 'nomic-embed-text', size: 275_000_000, digest: 'def456', modified_at: '2025-01-01T00:00:00Z' },
        ],
      }));
    }
    if (urlStr.includes('/api/ps')) {
      return Promise.resolve(mockFetchResponse({
        models: [{ name: 'llama3.2', model: 'llama3.2', size: 2_000_000_000, size_vram: 1_800_000_000, expires_at: '2025-12-31T23:59:59Z' }],
      }));
    }
    return Promise.reject(new Error('Unmocked fetch URL: ' + urlStr));
  });
  vi.stubGlobal('fetch', fetchMock);
}

function makeRequest(overrides: Partial<LLMRequest> = {}): LLMRequest {
  return { messages: [{ role: 'user', content: 'Explain quicksort in TypeScript' }], maxTokens: 512, ...overrides };
}

function makeLocalResponse(overrides: Partial<LLMResponse> = {}): LLMResponse {
  return { content: 'This is a locally generated response from Ollama.', toolCalls: [], usage: { inputTokens: 42, outputTokens: 18 }, model: 'llama3.2', provider: 'local' as ProviderName, stopReason: 'end_turn', latencyMs: 150, ...overrides };
}

function makeCloudResponse(overrides: Partial<LLMResponse> = {}): LLMResponse {
  return { content: 'This is a cloud-generated response from Anthropic with greater detail.', toolCalls: [], usage: { inputTokens: 42, outputTokens: 64 }, model: 'claude-sonnet-4-20250514', provider: 'anthropic' as ProviderName, stopReason: 'end_turn', latencyMs: 800, ...overrides };
}

function makeFakeWindow() {
  return { webContents: { isDestroyed: vi.fn(() => false), send: mocks.webContentsSend } } as unknown;
}

function createMockProvider(name: ProviderName, response: LLMResponse): LLMProvider {
  return {
    name, isAvailable: () => true, complete: vi.fn().mockResolvedValue(response),
    async *stream() { yield { done: true, fullResponse: response }; },
  };
}

describe('Sprint 3 Integration: The Living Mind -- Local Intelligence Circle', () => {
  beforeEach(() => {
    setupFetchForOllama();
    CloudGate.resetInstance();
    OllamaLifecycle.resetInstance();
    mocks.ipcOnce.mockReset();
    mocks.settingsGet.mockReturnValue({});
    mocks.setSetting.mockResolvedValue(undefined);
    mocks.webContentsSend.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('1. OllamaProvider.complete() returns valid LLMResponse via mock Ollama HTTP', async () => {
    const provider = new OllamaProvider();
    const request = makeRequest();
    const response = await provider.complete(request);
    expect(response).toBeDefined();
    expect(response.content).toBe('This is a locally generated response from Ollama.');
    expect(response.provider).toBe('ollama');
    expect(response.model).toBe('llama3.2');
    expect(response.stopReason).toBe('end_turn');
    expect(response.usage.inputTokens).toBe(42);
    expect(response.usage.outputTokens).toBe(18);
    expect(response.latencyMs).toBeGreaterThanOrEqual(0);
    expect(response.toolCalls).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:11434/api/chat',
      expect.objectContaining({ method: 'POST', headers: { 'Content-Type': 'application/json' } }),
    );
  });

  it('2. EmbeddingPipeline.embed() returns vectors from Ollama mock HTTP', async () => {
    const pipeline = new EmbeddingPipeline();
    await pipeline.start();
    expect(pipeline.isReady()).toBe(true);
    const vector = await pipeline.embed('Agent Friday is sovereign-first AI.');
    expect(vector).not.toBeNull();
    expect(vector).toHaveLength(8);
    expect(vector).toEqual([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]);
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:11434/api/embed', expect.objectContaining({ method: 'POST' }));
    pipeline.stop();
    expect(pipeline.isReady()).toBe(false);
  });

  it('3. OllamaLifecycle reports correct running/model state via mock HTTP', async () => {
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();
    const health = lifecycle.getHealth();
    expect(health.running).toBe(true);
    expect(health.modelsLoaded).toBe(1);
    expect(health.vramUsed).toBeGreaterThan(0);
    const available = lifecycle.getAvailableModels();
    expect(available.length).toBe(2);
    expect(available.some((m) => m.name === 'llama3.2')).toBe(true);
    expect(available.some((m) => m.name === 'nomic-embed-text')).toBe(true);
    const loaded = lifecycle.getLoadedModels();
    expect(loaded.length).toBe(1);
    expect(loaded[0].name).toBe('llama3.2');
    expect(loaded[0].sizeVram).toBe(1_800_000_000);
    expect(lifecycle.isModelAvailable('llama3.2')).toBe(true);
    expect(lifecycle.isModelAvailable('nonexistent-model')).toBe(false);
    lifecycle.stop();
  });

  it('4. High-confidence local response delivered without cloud escalation', async () => {
    const localResponse = makeLocalResponse({ content: 'Here is a detailed explanation of quicksort with TypeScript implementation and complexity analysis.' });
    const localProvider = createMockProvider('local', localResponse);
    const cloudProvider = createMockProvider('anthropic', makeCloudResponse());
    llmClient.registerProvider(localProvider);
    llmClient.registerProvider(cloudProvider);
    const request = makeRequest();
    const events: RoutingEvent[] = [];
    const result = await routeLocalFirst(request, { complexity: 'simple', confidenceThreshold: 0.5, onRoutingEvent: (e) => events.push(e) });
    expect(result.provider).toBe('local');
    expect(result.content).toContain('quicksort');
    expect(localProvider.complete).toHaveBeenCalledTimes(1);
    expect(cloudProvider.complete).not.toHaveBeenCalled();
    expect(events.some((e) => e.type === 'local-attempt')).toBe(true);
    expect(events.some((e) => e.type === 'confidence-assessed')).toBe(true);
    expect(events.some((e) => e.type === 'final-response' && e.provider === 'local')).toBe(true);
    expect(events.some((e) => e.type === 'escalation-requested')).toBe(false);
  });

  it('5. Low-confidence response triggers CloudGate consent request', async () => {
    const localResponse = makeLocalResponse({ content: '', toolCalls: [] });
    const localProvider = createMockProvider('local', localResponse);
    const cloudProvider = createMockProvider('anthropic', makeCloudResponse());
    llmClient.registerProvider(localProvider);
    llmClient.registerProvider(cloudProvider);
    const gate = CloudGate.getInstance();
    gate.start();
    const request = makeRequest();
    const events: RoutingEvent[] = [];
    const result = await routeLocalFirst(request, { complexity: 'moderate', confidenceThreshold: 0.5, onRoutingEvent: (e) => events.push(e) });
    expect(result.provider).toBe('local');
    expect(events.some((e) => e.type === 'escalation-requested')).toBe(true);
    expect(events.some((e) => e.type === 'escalation-result' && e.allowed === false)).toBe(true);
    gate.stop();
  });

  it('6. User approval routes to cloud, cloud response delivered', async () => {
    const localResponse = makeLocalResponse({ content: '', toolCalls: [] });
    const cloudResponse = makeCloudResponse();
    const localProvider = createMockProvider('local', localResponse);
    const cloudProvider = createMockProvider('anthropic', cloudResponse);
    llmClient.registerProvider(localProvider);
    llmClient.registerProvider(cloudProvider);
    const gate = CloudGate.getInstance();
    gate.start(makeFakeWindow() as any);
    gate.setPolicy('general', 'allow', 'once');
    const request = makeRequest({ taskHint: undefined });
    const events: RoutingEvent[] = [];
    const result = await routeLocalFirst(request, { complexity: 'moderate', confidenceThreshold: 0.5, onRoutingEvent: (e) => events.push(e) });
    expect(result.provider).toBe('anthropic');
    expect(result.content).toContain('cloud-generated');
    expect(cloudProvider.complete).toHaveBeenCalledTimes(1);
    expect(events.some((e) => e.type === 'escalation-result' && e.allowed === true)).toBe(true);
    expect(events.some((e) => e.type === 'final-response' && e.provider === 'anthropic')).toBe(true);
    gate.stop();
  });

  it('7. User denial returns local result as-is', async () => {
    const localResponse = makeLocalResponse({ content: '', toolCalls: [] });
    const localProvider = createMockProvider('local', localResponse);
    const cloudProvider = createMockProvider('anthropic', makeCloudResponse());
    llmClient.registerProvider(localProvider);
    llmClient.registerProvider(cloudProvider);
    const gate = CloudGate.getInstance();
    gate.start(makeFakeWindow() as any);
    gate.setPolicy('general', 'deny', 'once');
    const request = makeRequest();
    const events: RoutingEvent[] = [];
    const result = await routeLocalFirst(request, { complexity: 'moderate', confidenceThreshold: 0.5, onRoutingEvent: (e) => events.push(e) });
    expect(result.provider).toBe('local');
    expect(result.content).toBe('');
    expect(cloudProvider.complete).not.toHaveBeenCalled();
    expect(events.some((e) => e.type === 'escalation-result' && e.allowed === false)).toBe(true);
    expect(events.some((e) => e.type === 'final-response' && e.provider === 'local' && e.reason?.includes('denied'))).toBe(true);
    gate.stop();
  });

  it('8. Always-allow-for-code policy skips future consent prompts', async () => {
    const gate = CloudGate.getInstance();
    gate.start(makeFakeWindow() as any);
    gate.setPolicy('code', 'allow', 'always');
    const stored = gate.getPolicy('code');
    expect(stored).not.toBeNull();
    expect(stored!.decision).toBe('allow');
    expect(stored!.scope).toBe('always');
    expect(mocks.setSetting).toHaveBeenCalledWith('cloudGatePolicies', expect.objectContaining({
      code: expect.objectContaining({ decision: 'allow', scope: 'always' }),
    }));
    const context1: EscalationContext = { taskCategory: 'code', confidence: { score: 0.3, signals: [], escalate: true }, promptPreview: 'Write a function', targetProvider: 'anthropic' };
    const decision1 = await gate.requestEscalation(context1);
    expect(decision1.allowed).toBe(true);
    expect(decision1.reason).toBe('policy-allow');
    const decision2 = await gate.requestEscalation(context1);
    expect(decision2.allowed).toBe(true);
    expect(decision2.reason).toBe('policy-allow');
    expect(mocks.webContentsSend).not.toHaveBeenCalled();
    gate.stop();
  });

  it('9. Routing decisions logged via callback/event', async () => {
    const localResponse = makeLocalResponse({ content: 'A thorough local response with sufficient detail for the query.' });
    const localProvider = createMockProvider('local', localResponse);
    llmClient.registerProvider(localProvider);
    const request = makeRequest();
    const events: RoutingEvent[] = [];
    await routeLocalFirst(request, { complexity: 'simple', onRoutingEvent: (e) => events.push(e) });
    expect(events.length).toBeGreaterThanOrEqual(3);
    const eventTypes = events.map((e) => e.type);
    expect(eventTypes).toContain('local-attempt');
    expect(eventTypes).toContain('confidence-assessed');
    expect(eventTypes).toContain('final-response');
    const confidenceEvent = events.find((e) => e.type === 'confidence-assessed');
    expect(confidenceEvent).toBeDefined();
    expect(typeof confidenceEvent!.confidence).toBe('number');
    expect(confidenceEvent!.confidence).toBeGreaterThanOrEqual(0);
    expect(confidenceEvent!.confidence).toBeLessThanOrEqual(1);
    expect(typeof confidenceEvent!.escalate).toBe('boolean');
    expect(confidenceEvent!.timestamp).toBeGreaterThan(0);
    const finalEvent = events.find((e) => e.type === 'final-response');
    expect(finalEvent).toBeDefined();
    expect(finalEvent!.provider).toBe('local');
  });

  it('10. Full circle: local inference -> confidence -> gate -> cloud -> complete pipeline', async () => {
    const localResponse = makeLocalResponse({ content: '', toolCalls: [] });
    const cloudResponse = makeCloudResponse();
    const localProvider = createMockProvider('local', localResponse);
    const cloudProvider = createMockProvider('anthropic', cloudResponse);
    llmClient.registerProvider(localProvider);
    llmClient.registerProvider(cloudProvider);
    const gate = CloudGate.getInstance();
    gate.start(makeFakeWindow() as any);
    gate.setPolicy('code', 'allow', 'session');
    const ollamaProvider = new OllamaProvider();
    const ollamaResponse = await ollamaProvider.complete(makeRequest());
    expect(ollamaResponse.provider).toBe('ollama');
    expect(ollamaResponse.content).toBeTruthy();
    const pipeline = new EmbeddingPipeline();
    await pipeline.start();
    const vector = await pipeline.embed('test embedding');
    expect(vector).not.toBeNull();
    expect(vector!.length).toBeGreaterThan(0);
    const lifecycle = OllamaLifecycle.getInstance();
    await lifecycle.start();
    expect(lifecycle.getHealth().running).toBe(true);
    const events: RoutingEvent[] = [];
    const request = makeRequest({ taskHint: 'code' });
    const result = await routeLocalFirst(request, { complexity: 'moderate', confidenceThreshold: 0.5, cloudProvider: 'anthropic', onRoutingEvent: (e) => events.push(e) });
    expect(localProvider.complete).toHaveBeenCalledTimes(1);
    const confEvent = events.find((e) => e.type === 'confidence-assessed');
    expect(confEvent).toBeDefined();
    expect(confEvent!.confidence).toBeLessThan(0.5);
    expect(confEvent!.escalate).toBe(true);
    expect(events.some((e) => e.type === 'escalation-requested')).toBe(true);
    const gateEvent = events.find((e) => e.type === 'escalation-result');
    expect(gateEvent).toBeDefined();
    expect(gateEvent!.allowed).toBe(true);
    expect(result.provider).toBe('anthropic');
    expect(result.content).toContain('cloud-generated');
    expect(cloudProvider.complete).toHaveBeenCalledTimes(1);
    const finalEvent = events.find((e) => e.type === 'final-response');
    expect(finalEvent).toBeDefined();
    expect(finalEvent!.provider).toBe('anthropic');
    const stats = gate.getStats();
    expect(stats.escalatedAllowed).toBeGreaterThanOrEqual(1);
    pipeline.stop();
    lifecycle.stop();
    gate.stop();
  });
});
