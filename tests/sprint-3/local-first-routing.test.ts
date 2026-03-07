/**
 * local-first-routing.test.ts — Tests for Phase H.3: The Inversion.
 *
 * Sprint 3 H.3: Local-first routing — The Inversion
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// -- Hoisted mocks -----------------------------------------------------------

const mocks = vi.hoisted(() => ({
  settingsGet: vi.fn(() => ({})),
  setSetting: vi.fn(() => Promise.resolve()),
  ipcOnce: vi.fn(),
}));

vi.mock('electron', () => ({
  ipcMain: { once: mocks.ipcOnce },
  app: { getPath: vi.fn(() => '/tmp/test') },
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    get: mocks.settingsGet,
    setSetting: mocks.setSetting,
  },
}));

// -- Imports (after mocks) ---------------------------------------------------

import {
  scoreModel,
  type TaskProfile,
  type ModelCapability,
  type RoutingConfig,
} from '../../src/main/intelligence-router';
import { assessConfidence } from '../../src/main/confidence-assessor';
import { CloudGate } from '../../src/main/cloud-gate';
import {
  routeLocalFirst,
  type LLMRequest,
  type LLMResponse,
  type LLMProvider,
  type RoutingEvent,
  type LocalFirstOptions,
  llmClient,
} from '../../src/main/llm-client';
import type { ProviderName } from '../../src/main/intelligence-router';
// -- Factories ---------------------------------------------------------------

function makeModel(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    modelId: overrides.modelId ?? 'test/model-1',
    name: overrides.name ?? 'Test Model',
    provider: overrides.provider ?? 'anthropic',
    routeVia: overrides.routeVia ?? 'anthropic',
    contextWindow: overrides.contextWindow ?? 200_000,
    inputCostPerMillion: overrides.inputCostPerMillion ?? 3,
    outputCostPerMillion: overrides.outputCostPerMillion ?? 15,
    tokensPerSecond: overrides.tokensPerSecond ?? 80,
    strengths: overrides.strengths ?? { reasoning: 0.88, code: 0.90, conversation: 0.85 },
    supportsToolUse: overrides.supportsToolUse ?? true,
    supportsVision: overrides.supportsVision ?? true,
    supportsAudio: overrides.supportsAudio ?? false,
    available: overrides.available ?? true,
    lastChecked: overrides.lastChecked ?? 0,
    rateLimit: overrides.rateLimit ?? 120,
    consecutiveFailures: overrides.consecutiveFailures ?? 0,
  };
}

function makeLocalModel(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return makeModel({
    modelId: 'local/llama-3.1-8b',
    provider: 'local',
    routeVia: 'local',
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 40,
    strengths: { conversation: 0.65, extraction: 0.70, code: 0.55, creative: 0.50, reasoning: 0.45 },
    ...overrides,
  });
}

function makeTask(overrides: Partial<TaskProfile> = {}): TaskProfile {
  return {
    category: overrides.category ?? 'conversation',
    complexity: overrides.complexity ?? 'simple',
    latency: overrides.latency ?? 'standard',
    estimatedInputTokens: overrides.estimatedInputTokens ?? 500,
    requiresToolUse: overrides.requiresToolUse ?? false,
    requiresVision: overrides.requiresVision ?? false,
    requiresAudio: overrides.requiresAudio ?? false,
    requiresLongContext: overrides.requiresLongContext ?? false,
    tags: overrides.tags ?? [],
  };
}

function makeConfig(overrides: Partial<RoutingConfig> = {}): RoutingConfig {
  return {
    enabled: overrides.enabled ?? true,
    monthlyBudgetUsd: overrides.monthlyBudgetUsd ?? 0,
    monthlySpentUsd: overrides.monthlySpentUsd ?? 0,
    budgetResetDay: overrides.budgetResetDay ?? 1,
    preferSpeed: overrides.preferSpeed ?? false,
    preferCost: overrides.preferCost ?? false,
    pinnedModelId: overrides.pinnedModelId ?? null,
    maxRequestCostUsd: overrides.maxRequestCostUsd ?? 1.0,
    maxDecisionHistory: overrides.maxDecisionHistory ?? 100,
    fallbackModelId: overrides.fallbackModelId ?? 'anthropic/claude-sonnet-4',
    localModelPolicy: overrides.localModelPolicy ?? 'preferred',
    localMinCapability: overrides.localMinCapability ?? 0.55,
  };
}

function makeRequest(overrides: Partial<LLMRequest> = {}): LLMRequest {
  return {
    messages: [{ role: 'user', content: 'Hello, how are you?' }],
    ...overrides,
  };
}

function makeResponse(overrides: Partial<LLMResponse> = {}): LLMResponse {
  return {
    content: 'I am doing well, thank you for asking! How can I help you today?',
    toolCalls: [],
    usage: { inputTokens: 20, outputTokens: 40 },
    model: 'local/llama-3.1-8b',
    provider: 'local' as ProviderName,
    stopReason: 'end_turn',
    latencyMs: 150,
    ...overrides,
  };
}

function makeMockProvider(name: ProviderName, response: LLMResponse): LLMProvider {
  return {
    name,
    isAvailable: () => true,
    complete: vi.fn().mockResolvedValue(response),
    async *stream() {
      yield { text: response.content, done: true, fullResponse: response };
    },
  };
}
// -- Tests -------------------------------------------------------------------

describe('Phase H.3: Local-First Routing', () => {

  describe('default policy', () => {
    it('defaults localModelPolicy to preferred', () => {
      const config = makeConfig();
      expect(config.localModelPolicy).toBe('preferred');
    });
  });

  describe('scoreModel with preferred policy', () => {
    it('gives local models a +0.3 scoring bonus when category strength >= 0.4', () => {
      const localModel = makeLocalModel({ strengths: { conversation: 0.45 } });
      const task = makeTask({ category: 'conversation', complexity: 'expert' });
      const preferredConfig = makeConfig({ localModelPolicy: 'preferred', localMinCapability: 0.4 });
      const allConfig = makeConfig({ localModelPolicy: 'all', localMinCapability: 0.4 });
      const preferredScore = scoreModel(localModel, task, preferredConfig);
      const allScore = scoreModel(localModel, task, allConfig);
      expect(preferredScore.totalScore).toBeGreaterThan(allScore.totalScore);
      const diff = preferredScore.totalScore - allScore.totalScore;
      expect(diff).toBeGreaterThan(0.1);
    });

    it('does NOT apply bonus when category strength < 0.4', () => {
      const weakLocal = makeLocalModel({ strengths: { reasoning: 0.35, conversation: 0.65 } });
      const task = makeTask({ category: 'reasoning', complexity: 'simple' });
      const preferredConfig = makeConfig({ localModelPolicy: 'preferred', localMinCapability: 0.3 });
      const allConfig = makeConfig({ localModelPolicy: 'all', localMinCapability: 0.3 });
      const preferredScore = scoreModel(weakLocal, task, preferredConfig);
      const allScore = scoreModel(weakLocal, task, allConfig);
      expect(preferredScore.totalScore).toEqual(allScore.totalScore);
    });

    it('does not restrict any complexity level under preferred policy', () => {
      const localModel = makeLocalModel({ strengths: { code: 0.70 } });
      const complexTask = makeTask({ category: 'code', complexity: 'complex' });
      const expertTask = makeTask({ category: 'code', complexity: 'expert' });
      const config = makeConfig({ localModelPolicy: 'preferred' });
      expect(scoreModel(localModel, complexTask, config).totalScore).toBeGreaterThan(0);
      expect(scoreModel(localModel, expertTask, config).totalScore).toBeGreaterThan(0);
    });
  });

  describe('trivial/simple routing', () => {
    it('selects local model over cloud for trivial/simple tasks when preferred', () => {
      const localModel = makeLocalModel({ strengths: { conversation: 0.65 } });
      const cloudModel = makeModel({
        modelId: 'anthropic/claude-sonnet-4', provider: 'anthropic', routeVia: 'anthropic',
        strengths: { conversation: 0.85 }, inputCostPerMillion: 3, outputCostPerMillion: 15,
      });
      const task = makeTask({ category: 'conversation', complexity: 'trivial' });
      const config = makeConfig({ localModelPolicy: 'preferred' });
      const localScore = scoreModel(localModel, task, config);
      const cloudScore = scoreModel(cloudModel, task, config);
      expect(localScore.totalScore).toBeGreaterThan(cloudScore.totalScore);
    });
  });

  describe('confidence assessment by complexity', () => {
    it('uses threshold 0.5 for moderate complexity (passes when confidence >= 0.5)', () => {
      const request = makeRequest();
      const goodResponse = makeResponse({
        content: 'Here is a detailed and helpful response about the topic you asked about.',
      });
      const result = assessConfidence(request, goodResponse, undefined, { threshold: 0.5 });
      expect(result.score).toBeGreaterThanOrEqual(0.5);
      expect(result.escalate).toBe(false);
    });

    it('uses higher threshold (0.7) for complex/expert tasks, flags truncated responses', () => {
      const request = makeRequest();
      const truncatedResponse = makeResponse({
        content: 'The answer is partially...',
        stopReason: 'max_tokens',
      });
      const result = assessConfidence(request, truncatedResponse, undefined, { threshold: 0.7 });
      expect(result.score).toBe(0.7);
      expect(result.escalate).toBe(false);
    });
  });
  describe('CloudGate escalation', () => {
    let gate: CloudGate;
    beforeEach(() => {
      CloudGate.resetInstance();
      gate = CloudGate.getInstance();
      gate.start();
    });

    it('calls requestEscalation and denies when no renderer exists', async () => {
      const decision = await gate.requestEscalation({
        taskCategory: 'code',
        confidence: { score: 0.3, signals: [], escalate: true },
        promptPreview: 'Write a quicksort function',
        targetProvider: 'anthropic',
      });
      expect(decision.allowed).toBe(false);
      expect(decision.reason).toBe('no-renderer');
    });

    it('returns allowed: true when policy is set to allow', async () => {
      gate.setPolicy('code', 'allow', 'session');
      const decision = await gate.requestEscalation({
        taskCategory: 'code',
        confidence: { score: 0.3, signals: [], escalate: true },
        promptPreview: 'Write a quicksort function',
        targetProvider: 'anthropic',
      });
      expect(decision.allowed).toBe(true);
      expect(decision.reason).toBe('policy-allow');
    });

    it('returns allowed: false when policy is set to deny', async () => {
      gate.setPolicy('code', 'deny', 'session');
      const decision = await gate.requestEscalation({
        taskCategory: 'code',
        confidence: { score: 0.3, signals: [], escalate: true },
        promptPreview: 'Write a quicksort function',
        targetProvider: 'anthropic',
      });
      expect(decision.allowed).toBe(false);
      expect(decision.reason).toBe('policy-deny');
    });
  });

  describe('routeLocalFirst end-to-end', () => {
    let gate: CloudGate;
    beforeEach(() => {
      CloudGate.resetInstance();
      gate = CloudGate.getInstance();
      gate.start();
    });

    it('completes full flow: local -> assess -> gate allow -> cloud retry', async () => {
      const localResponse = makeResponse({
        content: '', provider: 'local' as ProviderName, stopReason: 'end_turn',
      });
      const cloudResponse = makeResponse({
        content: 'Here is a comprehensive and detailed answer to your question.',
        provider: 'anthropic' as ProviderName, model: 'claude-sonnet-4',
      });
      const localProvider = makeMockProvider('local', localResponse);
      const cloudProvider = makeMockProvider('anthropic', cloudResponse);
      llmClient.registerProvider(localProvider);
      llmClient.registerProvider(cloudProvider);
      gate.setPolicy('general', 'allow', 'session');
      const events: RoutingEvent[] = [];
      const result = await routeLocalFirst(makeRequest(), {
        complexity: 'moderate', cloudProvider: 'anthropic',
        onRoutingEvent: (e) => events.push(e),
      });
      expect(result.provider).toBe('anthropic');
      expect(result.content).toContain('comprehensive');
      const eventTypes = events.map((e) => e.type);
      expect(eventTypes).toContain('local-attempt');
      expect(eventTypes).toContain('confidence-assessed');
      expect(eventTypes).toContain('escalation-requested');
      expect(eventTypes).toContain('escalation-result');
      expect(eventTypes).toContain('final-response');
    });

    it('returns local response when confidence is sufficient (no escalation)', async () => {
      const localResponse = makeResponse({
        content: 'I am doing well, thank you for asking! How can I help you today?',
        provider: 'local' as ProviderName,
      });
      const localProvider = makeMockProvider('local', localResponse);
      llmClient.registerProvider(localProvider);
      const events: RoutingEvent[] = [];
      const result = await routeLocalFirst(makeRequest(), {
        complexity: 'simple', onRoutingEvent: (e) => events.push(e),
      });
      expect(result.provider).toBe('local');
      const eventTypes = events.map((e) => e.type);
      expect(eventTypes).toContain('local-attempt');
      expect(eventTypes).toContain('confidence-assessed');
      expect(eventTypes).not.toContain('escalation-requested');
      expect(eventTypes).toContain('final-response');
      const finalEvent = events.find((e) => e.type === 'final-response');
      expect(finalEvent?.provider).toBe('local');
    });

    it('returns local response when gate denies escalation', async () => {
      const localResponse = makeResponse({ content: '', provider: 'local' as ProviderName });
      const localProvider = makeMockProvider('local', localResponse);
      llmClient.registerProvider(localProvider);
      const events: RoutingEvent[] = [];
      const result = await routeLocalFirst(makeRequest(), {
        complexity: 'moderate', onRoutingEvent: (e) => events.push(e),
      });
      expect(result.provider).toBe('local');
      const escalationResult = events.find((e) => e.type === 'escalation-result');
      expect(escalationResult?.allowed).toBe(false);
    });
  });
  describe('routing event logging', () => {
    it('emits system events with routing decision metadata via onRoutingEvent', async () => {
      CloudGate.resetInstance();
      const gate = CloudGate.getInstance();
      gate.start();
      const localResponse = makeResponse({
        content: 'A helpful response from the local model with sufficient detail.',
        provider: 'local' as ProviderName,
      });
      const localProvider = makeMockProvider('local', localResponse);
      llmClient.registerProvider(localProvider);
      const events: RoutingEvent[] = [];
      await routeLocalFirst(makeRequest(), {
        complexity: 'simple', onRoutingEvent: (e) => events.push(e),
      });
      for (const event of events) {
        expect(event.timestamp).toBeGreaterThan(0);
        expect(typeof event.type).toBe('string');
      }
      const assessEvent = events.find((e) => e.type === 'confidence-assessed');
      expect(assessEvent).toBeDefined();
      expect(typeof assessEvent!.confidence).toBe('number');
      expect(typeof assessEvent!.escalate).toBe('boolean');
    });
  });

  describe('backward compatibility', () => {
    it('existing policies (disabled, background, conservative, all) still work', () => {
      const localModel = makeLocalModel({ strengths: { conversation: 0.65, extraction: 0.70 } });
      const conversationTask = makeTask({ category: 'conversation', complexity: 'simple' });

      const disabled = scoreModel(localModel, conversationTask, makeConfig({ localModelPolicy: 'disabled' }));
      expect(disabled.totalScore).toBe(0);

      const background = scoreModel(localModel, conversationTask, makeConfig({ localModelPolicy: 'background' }));
      expect(background.totalScore).toBe(0);

      const extractionTask = makeTask({ category: 'extraction' });
      const bgExtract = scoreModel(localModel, extractionTask, makeConfig({ localModelPolicy: 'background' }));
      expect(bgExtract.totalScore).toBeGreaterThan(0);

      const conservative = scoreModel(localModel, conversationTask, makeConfig({ localModelPolicy: 'conservative' }));
      expect(conservative.totalScore).toBeGreaterThan(0);

      const complexTask = makeTask({ category: 'conversation', complexity: 'complex' });
      const consComplex = scoreModel(localModel, complexTask, makeConfig({ localModelPolicy: 'conservative' }));
      expect(consComplex.totalScore).toBe(0);

      const allPolicy = scoreModel(localModel, conversationTask, makeConfig({ localModelPolicy: 'all' }));
      expect(allPolicy.totalScore).toBeGreaterThan(0);
    });
  });

  describe('cloud models unaffected', () => {
    it('cloud models do not receive the local bonus under preferred policy', () => {
      const cloudModel = makeModel({
        provider: 'anthropic', routeVia: 'anthropic', strengths: { conversation: 0.85 },
      });
      const task = makeTask({ category: 'conversation', complexity: 'simple' });
      const preferredConfig = makeConfig({ localModelPolicy: 'preferred' });
      const conservativeConfig = makeConfig({ localModelPolicy: 'conservative' });
      const preferredScore = scoreModel(cloudModel, task, preferredConfig);
      const conservativeScore = scoreModel(cloudModel, task, conservativeConfig);
      expect(preferredScore.totalScore).toEqual(conservativeScore.totalScore);
    });
  });
});