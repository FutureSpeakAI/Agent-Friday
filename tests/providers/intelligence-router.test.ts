/**
 * Intelligence Router — Unit tests for scoring and local model policy.
 *
 * Tests the pure functions scoreModel() and estimateRequestCost()
 * as well as the local model policy enforcement logic. No mocking
 * needed — these are deterministic, side-effect-free functions.
 *
 * Phase A.2: "The Chooser" — Intelligence Router Tests
 */

import { describe, it, expect } from 'vitest';
import {
  scoreModel,
  estimateRequestCost,
  type TaskProfile,
  type ModelCapability,
  type RoutingConfig,
} from '../../src/main/intelligence-router';

// ── Factories ───────────────────────────────────────────────────────

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
    localModelPolicy: overrides.localModelPolicy ?? 'conservative',
    localMinCapability: overrides.localMinCapability ?? 0.55,
  };
}

// ── Tests ────────────────────────────────────────────────────────────

describe('estimateRequestCost', () => {
  it('calculates cost from input and output token counts', () => {
    const model = makeModel({ inputCostPerMillion: 3, outputCostPerMillion: 15 });
    const cost = estimateRequestCost(model, 1_000_000, 500_000);
    // 1M input * $3/M + 500K output * $15/M = $3 + $7.50 = $10.50
    expect(cost).toBeCloseTo(10.5);
  });

  it('returns 0 for zero tokens', () => {
    const model = makeModel();
    expect(estimateRequestCost(model, 0, 0)).toBe(0);
  });

  it('handles free models (cost = 0)', () => {
    const model = makeModel({ inputCostPerMillion: 0, outputCostPerMillion: 0 });
    expect(estimateRequestCost(model, 1000, 1000)).toBe(0);
  });
});

describe('scoreModel — hard filters', () => {
  const config = makeConfig();

  it('returns zero score when model requires vision but lacks it', () => {
    const model = makeModel({ supportsVision: false });
    const task = makeTask({ requiresVision: true });
    const score = scoreModel(model, task, config);
    expect(score.totalScore).toBe(0);
  });

  it('returns zero score when task requires audio but model lacks it', () => {
    const model = makeModel({ supportsAudio: false });
    const task = makeTask({ requiresAudio: true });
    const score = scoreModel(model, task, config);
    expect(score.totalScore).toBe(0);
  });

  it('returns zero score when task requires tool use but model lacks it', () => {
    const model = makeModel({ supportsToolUse: false });
    const task = makeTask({ requiresToolUse: true });
    const score = scoreModel(model, task, config);
    expect(score.totalScore).toBe(0);
  });

  it('returns zero score when input exceeds 90% of context window', () => {
    const model = makeModel({ contextWindow: 10000 });
    const task = makeTask({ estimatedInputTokens: 9500 }); // 95%
    const score = scoreModel(model, task, config);
    expect(score.totalScore).toBe(0);
  });

  it('returns zero score when model is unavailable', () => {
    const model = makeModel({ available: false });
    const score = scoreModel(model, makeTask(), config);
    expect(score.totalScore).toBe(0);
  });

  it('returns zero score when circuit breaker threshold is reached', () => {
    const model = makeModel({ consecutiveFailures: 3 });
    const score = scoreModel(model, makeTask(), config);
    expect(score.totalScore).toBe(0);
  });

  it('allows model with 2 consecutive failures (under threshold)', () => {
    const model = makeModel({ consecutiveFailures: 2 });
    const score = scoreModel(model, makeTask(), config);
    expect(score.totalScore).toBeGreaterThan(0);
  });
});

describe('scoreModel — scoring breakdown', () => {
  const config = makeConfig();

  it('returns a score between 0 and 1 for a valid model', () => {
    const score = scoreModel(makeModel(), makeTask(), config);
    expect(score.totalScore).toBeGreaterThan(0);
    expect(score.totalScore).toBeLessThanOrEqual(1);
  });

  it('includes all breakdown components', () => {
    const score = scoreModel(makeModel(), makeTask(), config);
    expect(score.breakdown).toHaveProperty('capabilityScore');
    expect(score.breakdown).toHaveProperty('costScore');
    expect(score.breakdown).toHaveProperty('speedScore');
    expect(score.breakdown).toHaveProperty('contextScore');
    expect(score.breakdown).toHaveProperty('reliabilityScore');
  });

  it('gives higher capability score to a model strong in the task category', () => {
    const strong = makeModel({ strengths: { code: 0.95 } });
    const weak = makeModel({ modelId: 'test/weak', strengths: { code: 0.3 } });
    // Use expert complexity so the denominator is high enough to differentiate
    const task = makeTask({ category: 'code', complexity: 'expert' });

    const strongScore = scoreModel(strong, task, config);
    const weakScore = scoreModel(weak, task, config);
    expect(strongScore.breakdown.capabilityScore).toBeGreaterThan(weakScore.breakdown.capabilityScore);
  });

  it('gives higher cost score to cheaper models', () => {
    const cheap = makeModel({ inputCostPerMillion: 0.8, outputCostPerMillion: 4 });
    const expensive = makeModel({ modelId: 'test/exp', inputCostPerMillion: 15, outputCostPerMillion: 75 });
    const task = makeTask();

    const cheapScore = scoreModel(cheap, task, config);
    const expScore = scoreModel(expensive, task, config);
    expect(cheapScore.breakdown.costScore).toBeGreaterThan(expScore.breakdown.costScore);
  });

  it('gives higher speed score to faster models', () => {
    const fast = makeModel({ tokensPerSecond: 150 });
    const slow = makeModel({ modelId: 'test/slow', tokensPerSecond: 20 });
    const task = makeTask({ latency: 'fast' });

    const fastScore = scoreModel(fast, task, config);
    const slowScore = scoreModel(slow, task, config);
    expect(fastScore.breakdown.speedScore).toBeGreaterThan(slowScore.breakdown.speedScore);
  });

  it('reduces reliability score with consecutive failures', () => {
    const healthy = makeModel({ consecutiveFailures: 0 });
    const degraded = makeModel({ modelId: 'test/deg', consecutiveFailures: 2 });
    const task = makeTask();

    const healthyScore = scoreModel(healthy, task, config);
    const degradedScore = scoreModel(degraded, task, config);
    expect(healthyScore.breakdown.reliabilityScore).toBeGreaterThan(degradedScore.breakdown.reliabilityScore);
  });

  it('zeros cost score when budget is exhausted', () => {
    const budgetConfig = makeConfig({ monthlyBudgetUsd: 10, monthlySpentUsd: 9.99 });
    const model = makeModel({ inputCostPerMillion: 15, outputCostPerMillion: 75 });
    const task = makeTask({ estimatedInputTokens: 10000 });

    const score = scoreModel(model, task, budgetConfig);
    expect(score.breakdown.costScore).toBe(0);
  });
});

describe('scoreModel — local model policy', () => {
  const localModel = makeModel({
    modelId: 'local/llama-3.1-8b',
    provider: 'local',
    routeVia: 'local',
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 40,
    strengths: { conversation: 0.65, extraction: 0.70, code: 0.55, creative: 0.50, reasoning: 0.40 },
  });

  it('blocks local models entirely when policy is disabled', () => {
    const config = makeConfig({ localModelPolicy: 'disabled' });
    const score = scoreModel(localModel, makeTask({ category: 'conversation' }), config);
    expect(score.totalScore).toBe(0);
  });

  it('allows local models for extraction when policy is background', () => {
    const config = makeConfig({ localModelPolicy: 'background' });
    const score = scoreModel(localModel, makeTask({ category: 'extraction' }), config);
    expect(score.totalScore).toBeGreaterThan(0);
  });

  it('blocks local models for conversation when policy is background', () => {
    const config = makeConfig({ localModelPolicy: 'background' });
    const score = scoreModel(localModel, makeTask({ category: 'conversation' }), config);
    expect(score.totalScore).toBe(0);
  });

  it('allows local models for simple conversation when policy is conservative', () => {
    const config = makeConfig({ localModelPolicy: 'conservative' });
    const score = scoreModel(localModel, makeTask({ category: 'conversation', complexity: 'simple' }), config);
    expect(score.totalScore).toBeGreaterThan(0);
  });

  it('blocks local models for complex tasks when policy is conservative', () => {
    const config = makeConfig({ localModelPolicy: 'conservative' });
    const score = scoreModel(localModel, makeTask({ category: 'code', complexity: 'complex' }), config);
    expect(score.totalScore).toBe(0);
  });

  it('blocks local models for expert tasks when policy is conservative', () => {
    const config = makeConfig({ localModelPolicy: 'conservative' });
    const score = scoreModel(localModel, makeTask({ category: 'conversation', complexity: 'expert' }), config);
    expect(score.totalScore).toBe(0);
  });

  it('blocks local models for reasoning when policy is conservative', () => {
    const config = makeConfig({ localModelPolicy: 'conservative' });
    const score = scoreModel(localModel, makeTask({ category: 'reasoning', complexity: 'simple' }), config);
    expect(score.totalScore).toBe(0);
  });

  it('allows local models for all task types when policy is all', () => {
    const strongLocal = makeModel({
      ...localModel,
      strengths: { reasoning: 0.60, code: 0.60, creative: 0.60, conversation: 0.65 },
    });
    const config = makeConfig({ localModelPolicy: 'all', localMinCapability: 0.55 });

    const reasoning = scoreModel(strongLocal, makeTask({ category: 'reasoning', complexity: 'complex' }), config);
    expect(reasoning.totalScore).toBeGreaterThan(0);
  });

  it('enforces minimum capability threshold for local models', () => {
    // Local model has conversation=0.65 but creative=0.50
    const config = makeConfig({ localModelPolicy: 'all', localMinCapability: 0.55 });

    const conversation = scoreModel(localModel, makeTask({ category: 'conversation' }), config);
    expect(conversation.totalScore).toBeGreaterThan(0); // 0.65 >= 0.55

    const creative = scoreModel(localModel, makeTask({ category: 'creative' }), config);
    expect(creative.totalScore).toBe(0); // 0.50 < 0.55
  });

  it('does not restrict non-local models regardless of policy', () => {
    const cloudModel = makeModel({ provider: 'anthropic', routeVia: 'anthropic' });
    const config = makeConfig({ localModelPolicy: 'disabled' });
    const score = scoreModel(cloudModel, makeTask(), config);
    expect(score.totalScore).toBeGreaterThan(0);
  });
});
