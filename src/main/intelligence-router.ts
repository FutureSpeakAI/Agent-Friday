/**
 * Track VII, Phase 1: Intelligence Routing Layer
 *
 * Automatically routes tasks to the optimal language model based on:
 *   - Task complexity & capability requirements
 *   - Latency constraints
 *   - Cost awareness & budget enforcement
 *   - Model-specific strengths (reasoning, code, vision, speed)
 *   - Fallback chains when primary model is unavailable
 *
 * All routing decisions are logged and queryable. The user can always
 * ask "which model did you use?" and override any choice.
 *
 * cLaw Gate: Automatic routing is a convenience, not a cage. The user
 * retains full control and can pin specific models at any time.
 */

import crypto from 'crypto';
import { app } from 'electron';
import path from 'path';
import fs from 'fs/promises';
import type { HuggingFaceProvider } from './providers/hf-provider';
import { settingsManager } from './settings';

// ── Types ─────────────────────────────────────────────────────────────

export type TaskCategory =
  | 'reasoning'      // Deep analysis, legal review, complex decisions
  | 'code'           // Code generation, review, refactoring
  | 'creative'       // Writing, brainstorming, ideation
  | 'conversation'   // Quick chat, Q&A, simple lookup
  | 'extraction'     // Data parsing, summarization, key point extraction
  | 'vision'         // Image analysis, screenshot interpretation
  | 'audio'          // Voice interaction, transcription
  | 'tool-use'       // Function calling, multi-step agent loops
  | 'embedding'      // Similarity search, semantic matching
  | 'unknown';

export type TaskComplexity = 'trivial' | 'simple' | 'moderate' | 'complex' | 'expert';

export type LatencyTier = 'realtime' | 'fast' | 'standard' | 'batch';

export type ProviderName = 'anthropic' | 'openrouter' | 'google' | 'local' | 'ollama';

export interface TaskProfile {
  /** Primary category */
  category: TaskCategory;
  /** Estimated complexity */
  complexity: TaskComplexity;
  /** Latency requirement */
  latency: LatencyTier;
  /** Approximate input tokens */
  estimatedInputTokens: number;
  /** Whether tool use is required */
  requiresToolUse: boolean;
  /** Whether vision capability is needed */
  requiresVision: boolean;
  /** Whether audio capability is needed */
  requiresAudio: boolean;
  /** Whether long context (>32k) is needed */
  requiresLongContext: boolean;
  /** Optional tags for finer matching */
  tags: string[];
}

export interface ModelCapability {
  /** Unique model identifier (e.g. 'anthropic/claude-opus-4') */
  modelId: string;
  /** Human-readable name */
  name: string;
  /** Provider this model is accessed through */
  provider: ProviderName;
  /** Which provider to route through (may differ for OpenRouter models) */
  routeVia: 'anthropic' | 'openrouter' | 'google' | 'local' | 'ollama';
  /** Maximum context window in tokens */
  contextWindow: number;
  /** Cost per million input tokens (USD) */
  inputCostPerMillion: number;
  /** Cost per million output tokens (USD) */
  outputCostPerMillion: number;
  /** Estimated tokens per second output */
  tokensPerSecond: number;
  /** Category-specific strength scores (0-1) */
  strengths: Partial<Record<TaskCategory, number>>;
  /** Supports function/tool calling */
  supportsToolUse: boolean;
  /** Supports vision (image inputs) */
  supportsVision: boolean;
  /** Supports audio input/output */
  supportsAudio: boolean;
  /** Whether this model is currently available */
  available: boolean;
  /** Last time we checked availability */
  lastChecked: number;
  /** Rate limit: max requests per minute (0 = unknown) */
  rateLimit: number;
  /** Current failures in a row (for circuit breaking) */
  consecutiveFailures: number;
}

export interface RoutingDecision {
  id: string;
  timestamp: number;
  /** The task that was analyzed */
  taskProfile: TaskProfile;
  /** Which model was selected */
  selectedModelId: string;
  /** Why this model was chosen */
  reason: string;
  /** Score breakdown */
  scores: ModelScore[];
  /** Whether budget constraint affected the choice */
  budgetConstrained: boolean;
  /** Whether this was a fallback selection */
  isFallback: boolean;
  /** User override (if any) */
  userOverride: string | null;
  /** How long the request took (filled after completion) */
  durationMs: number | null;
  /** Whether the request succeeded */
  success: boolean | null;
  /** Actual tokens used (filled after completion) */
  actualInputTokens: number | null;
  actualOutputTokens: number | null;
  /** Actual cost (filled after completion) */
  actualCost: number | null;
}

export interface ModelScore {
  modelId: string;
  totalScore: number;
  /** Breakdown of how the score was computed */
  breakdown: {
    capabilityScore: number;    // 0-1: how well it handles this task category
    costScore: number;          // 0-1: how budget-friendly (inverted cost)
    speedScore: number;         // 0-1: how fast relative to latency needs
    contextScore: number;       // 0-1: can it handle the input size
    reliabilityScore: number;   // 0-1: recent success rate
  };
}

export interface RoutingConfig {
  /** Whether automatic routing is enabled */
  enabled: boolean;
  /** Monthly budget in USD (0 = unlimited) */
  monthlyBudgetUsd: number;
  /** Spent so far this month */
  monthlySpentUsd: number;
  /** Budget month start (day of month) */
  budgetResetDay: number;
  /** Whether to prefer speed over quality for simple tasks */
  preferSpeed: boolean;
  /** Whether to prefer cost savings over quality */
  preferCost: boolean;
  /** Pinned model ID (overrides routing if set) */
  pinnedModelId: string | null;
  /** Maximum cost per single request in USD */
  maxRequestCostUsd: number;
  /** How many routing decisions to retain in history */
  maxDecisionHistory: number;
  /** Fallback model when primary selection fails */
  fallbackModelId: string;
  /**
   * Controls when local models are eligible for automatic routing.
   * - 'disabled':     Local models never chosen by the router (user can still force via settings)
   * - 'background':   Only non-interactive tasks (memory ops, extraction, summarization)
   * - 'conservative': Simple/moderate tasks only (conversation, extraction, basic creative)
   * - 'all':          Local models compete equally for all task types
   * - 'preferred':    Local models get scoring bonus, cloud is gated escape hatch
   * Default: 'preferred' — local-first with cloud as escape hatch
   */
  localModelPolicy: 'disabled' | 'background' | 'conservative' | 'all' | 'preferred';
  /**
   * Minimum capability score (0-1) a local model must have for the task category
   * to be considered. Prevents local models from winning on cost alone.
   * Default: 0.55 — only tasks where the local model is at least moderately capable
   */
  localMinCapability: number;
}

export interface RouterStats {
  totalDecisions: number;
  successfulRoutes: number;
  failedRoutes: number;
  fallbacksUsed: number;
  totalCostUsd: number;
  monthlySpentUsd: number;
  monthlyBudgetUsd: number;
  budgetUtilization: number;
  modelUsage: Array<{ modelId: string; count: number; totalCost: number }>;
  avgLatencyMs: number;
}

// ── Constants ─────────────────────────────────────────────────────────

const COMPLEXITY_WEIGHTS: Record<TaskComplexity, number> = {
  trivial: 0.1,
  simple: 0.3,
  moderate: 0.5,
  complex: 0.8,
  expert: 1.0,
};

const LATENCY_MAX_MS: Record<LatencyTier, number> = {
  realtime: 500,
  fast: 3000,
  standard: 15000,
  batch: 120000,
};

const CIRCUIT_BREAKER_THRESHOLD = 3;

/**
 * Get the number of in-flight requests to the local inference server.
 * Uses lazy require to avoid circular dependency with llm-client.
 * Returns 0 if the provider is not available or not registered.
 */
function getLocalInflightCount(): number {
  try {
    const { llmClient } = require('./llm-client');
    const provider = llmClient.getProvider?.('local') as HuggingFaceProvider | undefined;
    return provider?.getInflightCount?.() ?? 0;
  } catch {
    return 0;
  }
}

// ── Built-in Model Registry ───────────────────────────────────────────

const DEFAULT_MODELS: ModelCapability[] = [
  // ── Anthropic (Direct) ──
  {
    modelId: 'anthropic/claude-opus-4',
    name: 'Claude Opus 4',
    provider: 'anthropic',
    routeVia: 'anthropic',
    contextWindow: 200000,
    inputCostPerMillion: 15,
    outputCostPerMillion: 75,
    tokensPerSecond: 40,
    strengths: { reasoning: 0.98, code: 0.95, creative: 0.92, extraction: 0.95, 'tool-use': 0.95, conversation: 0.90 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 60,
    consecutiveFailures: 0,
  },
  {
    modelId: 'anthropic/claude-sonnet-4',
    name: 'Claude Sonnet 4',
    provider: 'anthropic',
    routeVia: 'anthropic',
    contextWindow: 200000,
    inputCostPerMillion: 3,
    outputCostPerMillion: 15,
    tokensPerSecond: 80,
    strengths: { reasoning: 0.88, code: 0.90, creative: 0.85, extraction: 0.90, 'tool-use': 0.90, conversation: 0.88 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 120,
    consecutiveFailures: 0,
  },
  {
    modelId: 'anthropic/claude-haiku-3.5',
    name: 'Claude Haiku 3.5',
    provider: 'anthropic',
    routeVia: 'anthropic',
    contextWindow: 200000,
    inputCostPerMillion: 0.8,
    outputCostPerMillion: 4,
    tokensPerSecond: 150,
    strengths: { reasoning: 0.70, code: 0.72, creative: 0.65, extraction: 0.78, 'tool-use': 0.75, conversation: 0.80 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 200,
    consecutiveFailures: 0,
  },

  // ── Google (Gemini) ──
  {
    modelId: 'google/gemini-2.5-pro',
    name: 'Gemini 2.5 Pro',
    provider: 'google',
    routeVia: 'google',
    contextWindow: 1000000,
    inputCostPerMillion: 1.25,
    outputCostPerMillion: 10,
    tokensPerSecond: 100,
    strengths: { reasoning: 0.90, code: 0.88, creative: 0.82, extraction: 0.88, 'tool-use': 0.85, audio: 0.95, vision: 0.92, conversation: 0.85 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: true,
    available: true,
    lastChecked: 0,
    rateLimit: 60,
    consecutiveFailures: 0,
  },
  {
    modelId: 'google/gemini-2.5-flash',
    name: 'Gemini 2.5 Flash',
    provider: 'google',
    routeVia: 'google',
    contextWindow: 1000000,
    inputCostPerMillion: 0.15,
    outputCostPerMillion: 0.6,
    tokensPerSecond: 200,
    strengths: { reasoning: 0.72, code: 0.75, creative: 0.68, extraction: 0.80, 'tool-use': 0.78, audio: 0.85, vision: 0.80, conversation: 0.82 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: true,
    available: true,
    lastChecked: 0,
    rateLimit: 200,
    consecutiveFailures: 0,
  },

  // ── OpenRouter models ──
  {
    modelId: 'openai/gpt-4o',
    name: 'GPT-4o',
    provider: 'openrouter',
    routeVia: 'openrouter',
    contextWindow: 128000,
    inputCostPerMillion: 2.5,
    outputCostPerMillion: 10,
    tokensPerSecond: 90,
    strengths: { reasoning: 0.88, code: 0.87, creative: 0.85, extraction: 0.87, 'tool-use': 0.90, vision: 0.90, conversation: 0.88 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 100,
    consecutiveFailures: 0,
  },
  {
    modelId: 'openai/gpt-4o-mini',
    name: 'GPT-4o Mini',
    provider: 'openrouter',
    routeVia: 'openrouter',
    contextWindow: 128000,
    inputCostPerMillion: 0.15,
    outputCostPerMillion: 0.6,
    tokensPerSecond: 150,
    strengths: { reasoning: 0.65, code: 0.68, creative: 0.62, extraction: 0.72, 'tool-use': 0.70, conversation: 0.75 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 200,
    consecutiveFailures: 0,
  },
  {
    modelId: 'meta-llama/llama-3.3-70b',
    name: 'Llama 3.3 70B',
    provider: 'openrouter',
    routeVia: 'openrouter',
    contextWindow: 131072,
    inputCostPerMillion: 0.4,
    outputCostPerMillion: 0.4,
    tokensPerSecond: 120,
    strengths: { reasoning: 0.72, code: 0.75, creative: 0.68, extraction: 0.75, 'tool-use': 0.60, conversation: 0.72 },
    supportsToolUse: true,
    supportsVision: false,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 200,
    consecutiveFailures: 0,
  },
  {
    modelId: 'deepseek/deepseek-r1',
    name: 'DeepSeek R1',
    provider: 'openrouter',
    routeVia: 'openrouter',
    contextWindow: 163840,
    inputCostPerMillion: 0.55,
    outputCostPerMillion: 2.19,
    tokensPerSecond: 60,
    strengths: { reasoning: 0.92, code: 0.90, creative: 0.65, extraction: 0.82, 'tool-use': 0.55, conversation: 0.60 },
    supportsToolUse: false,
    supportsVision: false,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 100,
    consecutiveFailures: 0,
  },
  {
    modelId: 'qwen/qwen-2.5-coder-32b',
    name: 'Qwen 2.5 Coder 32B',
    provider: 'openrouter',
    routeVia: 'openrouter',
    contextWindow: 32768,
    inputCostPerMillion: 0.2,
    outputCostPerMillion: 0.2,
    tokensPerSecond: 140,
    strengths: { reasoning: 0.60, code: 0.88, creative: 0.45, extraction: 0.65, 'tool-use': 0.55, conversation: 0.55 },
    supportsToolUse: false,
    supportsVision: false,
    supportsAudio: false,
    available: true,
    lastChecked: 0,
    rateLimit: 200,
    consecutiveFailures: 0,
  },

  // ── Local / HuggingFace models ──
  // These are registered as defaults but marked unavailable until
  // auto-discovery confirms a local endpoint is reachable.
  {
    modelId: 'local/llama-3.3-70b',
    name: 'Llama 3.3 70B (Local)',
    provider: 'local',
    routeVia: 'local',
    contextWindow: 131072,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 40,
    strengths: { reasoning: 0.72, code: 0.75, creative: 0.68, extraction: 0.75, 'tool-use': 0.60, conversation: 0.72 },
    supportsToolUse: true,
    supportsVision: false,
    supportsAudio: false,
    available: false,         // Enabled by auto-discovery
    lastChecked: 0,
    rateLimit: 0,             // No rate limit for local
    consecutiveFailures: 0,
  },
  {
    modelId: 'local/qwen-2.5-coder-32b',
    name: 'Qwen 2.5 Coder 32B (Local)',
    provider: 'local',
    routeVia: 'local',
    contextWindow: 32768,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 60,
    strengths: { reasoning: 0.60, code: 0.88, creative: 0.45, extraction: 0.65, 'tool-use': 0.55, conversation: 0.55 },
    supportsToolUse: false,
    supportsVision: false,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'local/deepseek-r1',
    name: 'DeepSeek R1 (Local)',
    provider: 'local',
    routeVia: 'local',
    contextWindow: 131072,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 30,
    strengths: { reasoning: 0.92, code: 0.90, creative: 0.65, extraction: 0.82, 'tool-use': 0.55, conversation: 0.60 },
    supportsToolUse: false,
    supportsVision: false,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'local/mistral-large',
    name: 'Mistral Large (Local)',
    provider: 'local',
    routeVia: 'local',
    contextWindow: 131072,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 45,
    strengths: { reasoning: 0.78, code: 0.76, creative: 0.72, extraction: 0.80, 'tool-use': 0.65, conversation: 0.75 },
    supportsToolUse: true,
    supportsVision: false,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'local/phi-4',
    name: 'Phi 4 (Local)',
    provider: 'local',
    routeVia: 'local',
    contextWindow: 16384,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 120,
    strengths: { reasoning: 0.55, code: 0.62, creative: 0.48, extraction: 0.60, 'tool-use': 0.40, conversation: 0.65 },
    supportsToolUse: false,
    supportsVision: false,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  // ── Ollama / Gemma 4 (Local, zero cost) ──
  // Google Gemma 4 family — Apache 2.0 licensed, native tool calling.
  // Released April 2026. Runs via Ollama provider.
  {
    modelId: 'ollama/gemma4-e2b',
    name: 'Gemma 4 E2B (2.3B active)',
    provider: 'ollama',
    routeVia: 'local',
    contextWindow: 128000,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 200,
    strengths: { reasoning: 0.42, code: 0.44, creative: 0.38, extraction: 0.50, 'tool-use': 0.25, conversation: 0.55 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: true,
    available: false,         // Enabled by Ollama auto-discovery
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'ollama/gemma4-e4b',
    name: 'Gemma 4 E4B (4.5B active)',
    provider: 'ollama',
    routeVia: 'local',
    contextWindow: 128000,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 150,
    strengths: { reasoning: 0.50, code: 0.52, creative: 0.45, extraction: 0.58, 'tool-use': 0.42, conversation: 0.60, vision: 0.55 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: true,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'ollama/gemma4-26b',
    name: 'Gemma 4 26B MoE (3.8B active)',
    provider: 'ollama',
    routeVia: 'local',
    contextWindow: 256000,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 80,
    strengths: { reasoning: 0.75, code: 0.77, creative: 0.68, extraction: 0.78, 'tool-use': 0.68, conversation: 0.72, vision: 0.70 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
  {
    modelId: 'ollama/gemma4-31b',
    name: 'Gemma 4 31B Dense',
    provider: 'ollama',
    routeVia: 'local',
    contextWindow: 256000,
    inputCostPerMillion: 0,
    outputCostPerMillion: 0,
    tokensPerSecond: 45,
    strengths: { reasoning: 0.82, code: 0.80, creative: 0.75, extraction: 0.83, 'tool-use': 0.77, conversation: 0.78, vision: 0.78 },
    supportsToolUse: true,
    supportsVision: true,
    supportsAudio: false,
    available: false,
    lastChecked: 0,
    rateLimit: 0,
    consecutiveFailures: 0,
  },
];

// ── Exported Pure Functions (for testability) ─────────────────────────

/**
 * Classify a task from message content, tool presence, and context.
 */
export function classifyTask(params: {
  messageContent: string;
  toolCount: number;
  hasImages: boolean;
  hasAudio: boolean;
  systemPromptLength: number;
  conversationLength: number;
}): TaskProfile {
  const { messageContent, toolCount, hasImages, hasAudio, systemPromptLength, conversationLength } = params;
  const lower = messageContent.toLowerCase();
  const wordCount = messageContent.split(/\s+/).length;

  // ── Category detection ──
  let category: TaskCategory = 'conversation';
  if (hasAudio) category = 'audio';
  else if (hasImages) category = 'vision';
  else if (toolCount > 0 && /\b(search|browse|execute|run|fetch|call|look up)\b/.test(lower)) category = 'tool-use';
  else if (/\b(code|function|class|refactor|debug|implement|typescript|python|javascript|bug|error|fix)\b/.test(lower)) category = 'code';
  else if (/\b(analy[zs]e|review|evaluate|compare|assess|reason|explain why|think through|legal|contract)\b/.test(lower)) category = 'reasoning';
  else if (/\b(write|draft|compose|creative|story|poem|blog|article|essay|brainstorm)\b/.test(lower)) category = 'creative';
  else if (/\b(extract|summar|parse|list|key points|highlights|tldr)\b/.test(lower)) category = 'extraction';
  else if (/\b(embed|similar|semantic|vector|match)\b/.test(lower)) category = 'embedding';

  // ── Complexity estimation ──
  let complexity: TaskComplexity = 'simple';
  if (wordCount > 500 || /\b(comprehensive|thorough|detailed|in-depth|exhaustive)\b/.test(lower)) complexity = 'expert';
  else if (wordCount > 200 || /\b(analy[zs]e|review|evaluate|compare|multi-step)\b/.test(lower)) complexity = 'complex';
  else if (wordCount > 50 || toolCount > 3) complexity = 'moderate';
  else if (wordCount < 15) complexity = 'trivial';

  // ── Latency tier ──
  let latency: LatencyTier = 'standard';
  if (hasAudio) latency = 'realtime';
  else if (wordCount < 30 && category === 'conversation') latency = 'fast';
  else if (/\b(batch|background|whenever|no rush)\b/.test(lower)) latency = 'batch';

  // ── Token estimation ──
  const estimatedInputTokens = Math.ceil(
    (messageContent.length / 4) + (systemPromptLength / 4) + (conversationLength / 4)
  );

  return {
    category,
    complexity,
    latency,
    estimatedInputTokens,
    requiresToolUse: toolCount > 0,
    requiresVision: hasImages,
    requiresAudio: hasAudio,
    requiresLongContext: estimatedInputTokens > 32000,
    tags: [],
  };
}

/**
 * Score a model against a task profile. Returns 0-1.
 */
export function scoreModel(
  model: ModelCapability,
  task: TaskProfile,
  config: RoutingConfig
): ModelScore {
  // ── Hard filters (disqualifiers) ──
  if (task.requiresVision && !model.supportsVision) return zeroScore(model.modelId);
  if (task.requiresAudio && !model.supportsAudio) return zeroScore(model.modelId);
  if (task.requiresToolUse && !model.supportsToolUse) return zeroScore(model.modelId);
  if (task.estimatedInputTokens > model.contextWindow * 0.9) return zeroScore(model.modelId);
  if (!model.available) return zeroScore(model.modelId);
  if (model.consecutiveFailures >= CIRCUIT_BREAKER_THRESHOLD) return zeroScore(model.modelId);

  // ── Local model policy enforcement ──
  // Prevents local models from competing in routing decisions where they shouldn't.
  // The user can still force local via settings.preferredProvider — this only
  // affects *automatic* routing.
  let localBonus = 0;
  const isLocal = model.routeVia === 'local' || model.provider === 'local' || model.provider === 'ollama';
  if (isLocal) {
    const policy = config.localModelPolicy;

    // 'disabled' — local models never win automatic routing
    if (policy === 'disabled') return zeroScore(model.modelId);

    // 'background' — only non-interactive background tasks
    const backgroundCategories: Set<TaskCategory> = new Set([
      'extraction', 'embedding',
    ]);
    if (policy === 'background' && !backgroundCategories.has(task.category)) {
      return zeroScore(model.modelId);
    }

    // 'conservative' — simple/moderate tasks in safe categories
    if (policy === 'conservative') {
      // Block expert/complex tasks from routing to local
      if (task.complexity === 'expert' || task.complexity === 'complex') {
        return zeroScore(model.modelId);
      }
      // Block categories where quality degradation is most noticeable
      const conservativeBlocked: Set<TaskCategory> = new Set([
        'reasoning', 'audio', 'vision',
      ]);
      if (conservativeBlocked.has(task.category)) {
        return zeroScore(model.modelId);
      }
    }

    // 'all' — no additional restrictions, local competes equally

    // 'preferred' — local models get a scoring bonus, competing strongly against cloud
    // Does NOT block any complexity level — lets local try everything
    if (policy === 'preferred') {
      const catStrength = model.strengths[task.category] ?? 0;
      if (catStrength >= 0.4) {
        localBonus = 0.3;
      }
    }

    // Minimum capability threshold — applies to ALL local policies except 'disabled'
    // Prevents local models from winning on cost alone when they're weak at the task
    const categoryStrength = model.strengths[task.category] ?? 0;
    if (categoryStrength < config.localMinCapability) {
      return zeroScore(model.modelId);
    }
  }

  // ── Capability score (0-1) ──
  const categoryStrength = model.strengths[task.category] ?? 0.5;
  const complexityNeeded = COMPLEXITY_WEIGHTS[task.complexity];
  // High complexity demands high capability; low complexity doesn't penalize strong models
  const capabilityScore = Math.min(1, categoryStrength / Math.max(complexityNeeded, 0.3));

  // ── Cost score (0-1, inverted: cheaper = higher score) ──
  const estimatedCost = estimateRequestCost(model, task.estimatedInputTokens, task.estimatedInputTokens * 0.5);
  const maxAcceptableCost = config.maxRequestCostUsd || 1.0;
  let costScore = 1 - Math.min(estimatedCost / maxAcceptableCost, 1);
  if (config.preferCost) costScore = Math.pow(costScore, 0.5); // Boost cost importance

  // Budget check
  if (config.monthlyBudgetUsd > 0) {
    const remaining = config.monthlyBudgetUsd - config.monthlySpentUsd;
    if (estimatedCost > remaining) costScore = 0;
  }

  // ── Speed score (0-1) ──
  const maxLatency = LATENCY_MAX_MS[task.latency];
  const estimatedOutputTokens = task.estimatedInputTokens * 0.5;
  const estimatedTimeMs = (estimatedOutputTokens / model.tokensPerSecond) * 1000;
  let speedScore = Math.min(1, maxLatency / Math.max(estimatedTimeMs, 100));
  if (config.preferSpeed) speedScore = Math.pow(speedScore, 0.5); // Boost speed importance

  // Penalize local models that are already serving requests (Ollama is typically sequential).
  // Each in-flight request adds estimated queuing delay, degrading the speed score.
  if (isLocal) {
    const inflightCount = getLocalInflightCount();
    if (inflightCount > 0) {
      // Each queued request roughly doubles the wait time
      const queuePenalty = Math.pow(0.5, inflightCount);
      speedScore *= queuePenalty;
    }
  }

  // ── Context score (0-1) ──
  const contextUtilization = task.estimatedInputTokens / model.contextWindow;
  const contextScore = contextUtilization < 0.5 ? 1.0 :
    contextUtilization < 0.8 ? 0.8 :
    contextUtilization < 0.95 ? 0.5 : 0.1;

  // ── Reliability score (0-1) ──
  const reliabilityScore = model.consecutiveFailures === 0 ? 1.0 :
    Math.max(0, 1 - (model.consecutiveFailures * 0.3));

  // ── Weighted composite ──
  const weights = {
    capability: 0.35,
    cost: 0.20,
    speed: 0.20,
    context: 0.10,
    reliability: 0.15,
  };

  const totalScore =
    capabilityScore * weights.capability +
    costScore * weights.cost +
    speedScore * weights.speed +
    contextScore * weights.context +
    reliabilityScore * weights.reliability +
    localBonus;

  return {
    modelId: model.modelId,
    totalScore: Math.max(0, Math.min(1, totalScore)),
    breakdown: {
      capabilityScore,
      costScore,
      speedScore,
      contextScore,
      reliabilityScore,
    },
  };
}

/**
 * Estimate request cost in USD.
 */
export function estimateRequestCost(
  model: ModelCapability,
  inputTokens: number,
  outputTokens: number
): number {
  return (inputTokens * model.inputCostPerMillion / 1_000_000) +
         (outputTokens * model.outputCostPerMillion / 1_000_000);
}

/**
 * Build a human-readable explanation for a routing decision.
 */
export function buildRoutingExplanation(
  selected: ModelScore,
  task: TaskProfile,
  budgetConstrained: boolean,
  isFallback: boolean
): string {
  const parts: string[] = [];
  const b = selected.breakdown;

  parts.push(`Task: ${task.category} (${task.complexity})`);
  parts.push(`Capability: ${(b.capabilityScore * 100).toFixed(0)}%`);
  parts.push(`Cost: ${(b.costScore * 100).toFixed(0)}%`);
  parts.push(`Speed: ${(b.speedScore * 100).toFixed(0)}%`);

  if (budgetConstrained) parts.push('BUDGET CONSTRAINED');
  if (isFallback) parts.push('FALLBACK');

  return parts.join(' | ');
}

// ── Helpers ──────────────────────────────────────────────────────────

function zeroScore(modelId: string): ModelScore {
  return {
    modelId,
    totalScore: 0,
    breakdown: {
      capabilityScore: 0,
      costScore: 0,
      speedScore: 0,
      contextScore: 0,
      reliabilityScore: 0,
    },
  };
}

// ── Core Engine ───────────────────────────────────────────────────────

class IntelligenceRouter {
  private models: ModelCapability[] = [];
  private decisions: RoutingDecision[] = [];
  private config: RoutingConfig = {
    enabled: true,
    monthlyBudgetUsd: 0,
    monthlySpentUsd: 0,
    budgetResetDay: 1,
    preferSpeed: false,
    preferCost: false,
    pinnedModelId: null,
    maxRequestCostUsd: 1.0,
    maxDecisionHistory: 500,
    fallbackModelId: 'anthropic/claude-sonnet-4',
    localModelPolicy: 'preferred',
    localMinCapability: 0.55,
  };
  private filePath = '';
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  // ── Lifecycle ─────────────────────────────────────────────────────

  async initialize(): Promise<void> {
    try {
      const userDataPath = app.getPath('userData');
      this.filePath = path.join(userDataPath, 'intelligence-router.json');
      await this.load();
    } catch {
      // Fresh start
    }

    // Merge defaults into model registry (preserve customized entries)
    for (const defaultModel of DEFAULT_MODELS) {
      if (!this.models.find((m) => m.modelId === defaultModel.modelId)) {
        this.models.push({ ...defaultModel });
      }
    }

    // Check if budget needs reset
    this.checkBudgetReset();

    // ── Auto-configure for local-only operation ──
    // When no cloud API keys are configured and preferredProvider is 'ollama',
    // switch the fallback model to a local one so routing doesn't dead-end
    // on an unreachable Anthropic endpoint.
    this.autoConfigureForLocalIfNeeded();

    console.log(
      `[Router] Initialized — ${this.models.length} models registered, ` +
      `${this.decisions.length} decisions in history, ` +
      `budget: $${this.config.monthlySpentUsd.toFixed(2)}/${this.config.monthlyBudgetUsd > 0 ? '$' + this.config.monthlyBudgetUsd.toFixed(2) : 'unlimited'}`
    );
  }

  stop(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
  }

  /**
   * When the user has no cloud API keys (Anthropic, Gemini, OpenRouter)
   * and their preferred provider is 'ollama', reconfigure routing defaults
   * to avoid dead-ending on unreachable cloud fallbacks.
   */
  private autoConfigureForLocalIfNeeded(): void {
    const hasAnthropicKey = !!settingsManager.getAnthropicApiKey();
    const hasGeminiKey = !!settingsManager.getGeminiApiKey();
    const preferred = settingsManager.getPreferredProvider();

    // Only auto-configure when explicitly set to local-first and no cloud keys
    if (preferred !== 'ollama' || hasAnthropicKey || hasGeminiKey) return;

    // Find the first available local model to use as fallback
    const localModel = this.models.find(
      (m) => (m.provider === 'local' || m.provider === 'ollama') && m.available
    );

    const currentFallback = this.config.fallbackModelId;
    const isCloudFallback = currentFallback.startsWith('anthropic/') ||
      currentFallback.startsWith('google/') ||
      currentFallback.startsWith('openrouter/');

    if (isCloudFallback) {
      const newFallback = localModel?.modelId || 'ollama/llama3';
      this.config.fallbackModelId = newFallback;
      this.config.localModelPolicy = 'preferred';
      console.log(
        `[Router] No cloud API keys detected — auto-configured for local-only operation ` +
        `(fallback: ${newFallback}, policy: preferred)`
      );
      this.scheduleSave();
    }
  }

  // ── Primary Routing API ───────────────────────────────────────────

  /**
   * Select the optimal model for a task. This is the main entry point.
   * Returns the model ID and a routing decision for transparency.
   */
  selectModel(task: TaskProfile): RoutingDecision {
    const decisionId = crypto.randomUUID().slice(0, 12);

    // If pinned, use that model directly
    if (this.config.pinnedModelId) {
      const pinned = this.models.find((m) => m.modelId === this.config.pinnedModelId);
      if (pinned && pinned.available) {
        const decision = this.createDecision(decisionId, task, pinned.modelId, 'User-pinned model', [], false, false);
        this.recordDecision(decision);
        return decision;
      }
    }

    // Score all available models
    const scores = this.models
      .map((m) => scoreModel(m, task, this.config))
      .filter((s) => s.totalScore > 0)
      .sort((a, b) => b.totalScore - a.totalScore);

    if (scores.length === 0) {
      // No model qualifies — use fallback
      const fallback = this.config.fallbackModelId;
      const decision = this.createDecision(
        decisionId, task, fallback,
        'No model met requirements — using fallback',
        [], false, true
      );
      this.recordDecision(decision);
      return decision;
    }

    const best = scores[0];
    const budgetConstrained = this.isBudgetConstrained(task, best.modelId);

    let selectedModelId = best.modelId;
    let reason = buildRoutingExplanation(best, task, budgetConstrained, false);

    // If budget-constrained, try cheaper alternatives
    if (budgetConstrained && scores.length > 1) {
      for (const alt of scores.slice(1)) {
        if (!this.isBudgetConstrained(task, alt.modelId)) {
          selectedModelId = alt.modelId;
          reason = buildRoutingExplanation(alt, task, true, false);
          break;
        }
      }
    }

    const decision = this.createDecision(
      decisionId, task, selectedModelId, reason, scores, budgetConstrained, false
    );
    this.recordDecision(decision);
    return decision;
  }

  /**
   * Record the outcome of a routing decision (called after request completes).
   */
  recordOutcome(
    decisionId: string,
    outcome: {
      success: boolean;
      durationMs: number;
      inputTokens?: number;
      outputTokens?: number;
    }
  ): void {
    const decision = this.decisions.find((d) => d.id === decisionId);
    if (!decision) return;

    decision.success = outcome.success;
    decision.durationMs = outcome.durationMs;
    decision.actualInputTokens = outcome.inputTokens ?? null;
    decision.actualOutputTokens = outcome.outputTokens ?? null;

    // Calculate actual cost
    const model = this.models.find((m) => m.modelId === decision.selectedModelId);
    if (model && outcome.inputTokens && outcome.outputTokens) {
      const cost = estimateRequestCost(model, outcome.inputTokens, outcome.outputTokens);
      decision.actualCost = cost;
      this.config.monthlySpentUsd += cost;
    }

    // Update model reliability
    if (model) {
      if (outcome.success) {
        model.consecutiveFailures = 0;
      } else {
        model.consecutiveFailures++;
      }
    }

    this.scheduleSave();
  }

  // ── Model Registry ───────────────────────────────────────────────

  getModel(modelId: string): ModelCapability | null {
    return this.models.find((m) => m.modelId === modelId) || null;
  }

  getAllModels(): ModelCapability[] {
    return [...this.models];
  }

  getAvailableModels(): ModelCapability[] {
    return this.models.filter(
      (m) => m.available && m.consecutiveFailures < CIRCUIT_BREAKER_THRESHOLD
    );
  }

  /**
   * Register or update a model in the registry.
   */
  registerModel(model: ModelCapability): void {
    const idx = this.models.findIndex((m) => m.modelId === model.modelId);
    if (idx >= 0) {
      this.models[idx] = model;
    } else {
      this.models.push(model);
    }
    this.scheduleSave();
  }

  /**
   * Mark a model as available/unavailable.
   */
  setModelAvailability(modelId: string, available: boolean): void {
    const model = this.models.find((m) => m.modelId === modelId);
    if (model) {
      model.available = available;
      model.lastChecked = Date.now();
      if (available) model.consecutiveFailures = 0;
      this.scheduleSave();
    }
  }

  /**
   * Reset consecutive failures for a model (e.g., after user fixes config).
   */
  resetModelFailures(modelId: string): void {
    const model = this.models.find((m) => m.modelId === modelId);
    if (model) {
      model.consecutiveFailures = 0;
      this.scheduleSave();
    }
  }

  // ── Local Model Discovery ─────────────────────────────────────────

  /**
   * Probe the local inference endpoint and discover available models.
   * Updates the model registry with actual availability.
   *
   * Called on startup and can be re-invoked from settings when
   * the user changes the local endpoint configuration.
   */
  async discoverLocalModels(): Promise<{ found: number; models: string[] }> {
    let hfProvider: HuggingFaceProvider;
    try {
      // Late-require to avoid circular dependency at module load time
      const { llmClient } = require('./llm-client');
      const provider = llmClient.getProvider?.('local');
      if (!provider) {
        return { found: 0, models: [] };
      }
      hfProvider = provider as HuggingFaceProvider;
    } catch {
      return { found: 0, models: [] };
    }

    // Check if the local endpoint is healthy
    const healthy = await hfProvider.checkHealth();
    if (!healthy) {
      // Mark all local models as unavailable
      for (const model of this.models) {
        if (model.provider === 'local') {
          model.available = false;
          model.lastChecked = Date.now();
        }
      }
      console.log('[Router] Local inference endpoint unreachable — local models disabled');
      return { found: 0, models: [] };
    }

    // Discover what models are served by the endpoint
    let remoteModelIds: string[] = [];
    try {
      const remoteModels = await hfProvider.listModels();
      remoteModelIds = remoteModels.map((m) => m.id);
    } catch {
      // Health passed but listing failed — assume a single model is served
      // (common with TGI which only serves one model at a time)
      remoteModelIds = [];
    }

    const discoveredNames: string[] = [];

    // Strategy 1: Match remote model IDs to our known local/ entries
    for (const model of this.models) {
      if (model.provider !== 'local') continue;

      // Extract the base name (e.g., 'llama-3.3-70b' from 'local/llama-3.3-70b')
      const baseName = model.modelId.replace('local/', '');

      // Check if any remote model ID contains our base name (fuzzy match)
      const matched = remoteModelIds.length === 0 || remoteModelIds.some((rid) => {
        const ridLower = rid.toLowerCase();
        return ridLower.includes(baseName) ||
          baseName.includes(ridLower.split('/').pop() || '');
      });

      if (matched || remoteModelIds.length === 0) {
        // If no specific model list (single-model TGI), enable the first
        // plausible model. If we have a list, only enable matches.
        if (remoteModelIds.length === 0 && discoveredNames.length > 0) {
          // Already enabled one generic model — skip rest
          continue;
        }
        model.available = true;
        model.lastChecked = Date.now();
        model.consecutiveFailures = 0;
        discoveredNames.push(model.name);
      } else {
        model.available = false;
        model.lastChecked = Date.now();
      }
    }

    // Strategy 2: Register any remote models that don't match known entries
    for (const rid of remoteModelIds) {
      const ridLower = rid.toLowerCase();
      const alreadyKnown = this.models.some((m) =>
        m.provider === 'local' && (
          ridLower.includes(m.modelId.replace('local/', '')) ||
          m.modelId.replace('local/', '').includes(ridLower.split('/').pop() || '')
        )
      );

      if (!alreadyKnown) {
        // Register as a new local model with moderate default capabilities
        const newModel: ModelCapability = {
          modelId: `local/${rid.replace(/\//g, '-').toLowerCase()}`,
          name: `${rid} (Local)`,
          provider: 'local',
          routeVia: 'local',
          contextWindow: 32768,
          inputCostPerMillion: 0,
          outputCostPerMillion: 0,
          tokensPerSecond: 40,
          strengths: {
            reasoning: 0.60,
            code: 0.60,
            creative: 0.55,
            extraction: 0.65,
            'tool-use': 0.50,
            conversation: 0.60,
          },
          supportsToolUse: false,
          supportsVision: false,
          supportsAudio: false,
          available: true,
          lastChecked: Date.now(),
          rateLimit: 0,
          consecutiveFailures: 0,
        };
        this.models.push(newModel);
        discoveredNames.push(newModel.name);
      }
    }

    if (discoveredNames.length > 0) {
      console.log(`[Router] Discovered ${discoveredNames.length} local model(s): ${discoveredNames.join(', ')}`);
      this.scheduleSave();
    } else {
      console.log('[Router] Local endpoint healthy but no matching models found');
    }

    return { found: discoveredNames.length, models: discoveredNames };
  }

  // ── Decision History ─────────────────────────────────────────────

  getDecision(id: string): RoutingDecision | null {
    return this.decisions.find((d) => d.id === id) || null;
  }

  getRecentDecisions(limit?: number): RoutingDecision[] {
    const sorted = [...this.decisions].sort((a, b) => b.timestamp - a.timestamp);
    return limit ? sorted.slice(0, limit) : sorted;
  }

  getDecisionsForModel(modelId: string, limit?: number): RoutingDecision[] {
    const filtered = this.decisions
      .filter((d) => d.selectedModelId === modelId)
      .sort((a, b) => b.timestamp - a.timestamp);
    return limit ? filtered.slice(0, limit) : filtered;
  }

  // ── Stats ──────────────────────────────────────────────────────

  getStats(): RouterStats {
    const successful = this.decisions.filter((d) => d.success === true);
    const failed = this.decisions.filter((d) => d.success === false);
    const fallbacks = this.decisions.filter((d) => d.isFallback);
    const totalCost = this.decisions.reduce((sum, d) => sum + (d.actualCost || 0), 0);

    // Model usage breakdown
    const usageMap = new Map<string, { count: number; totalCost: number }>();
    for (const d of this.decisions) {
      const entry = usageMap.get(d.selectedModelId) || { count: 0, totalCost: 0 };
      entry.count++;
      entry.totalCost += d.actualCost || 0;
      usageMap.set(d.selectedModelId, entry);
    }

    const completedDecisions = this.decisions.filter((d) => d.durationMs !== null);
    const avgLatency = completedDecisions.length > 0
      ? completedDecisions.reduce((sum, d) => sum + (d.durationMs || 0), 0) / completedDecisions.length
      : 0;

    return {
      totalDecisions: this.decisions.length,
      successfulRoutes: successful.length,
      failedRoutes: failed.length,
      fallbacksUsed: fallbacks.length,
      totalCostUsd: totalCost,
      monthlySpentUsd: this.config.monthlySpentUsd,
      monthlyBudgetUsd: this.config.monthlyBudgetUsd,
      budgetUtilization: this.config.monthlyBudgetUsd > 0
        ? this.config.monthlySpentUsd / this.config.monthlyBudgetUsd
        : 0,
      modelUsage: Array.from(usageMap.entries())
        .map(([modelId, { count, totalCost }]) => ({ modelId, count, totalCost }))
        .sort((a, b) => b.count - a.count),
      avgLatencyMs: avgLatency,
    };
  }

  // ── Config ────────────────────────────────────────────────────────

  getConfig(): RoutingConfig {
    return { ...this.config };
  }

  updateConfig(partial: Partial<RoutingConfig>): RoutingConfig {
    if (partial.enabled !== undefined) this.config.enabled = partial.enabled;
    if (partial.monthlyBudgetUsd !== undefined) this.config.monthlyBudgetUsd = partial.monthlyBudgetUsd;
    if (partial.budgetResetDay !== undefined) this.config.budgetResetDay = partial.budgetResetDay;
    if (partial.preferSpeed !== undefined) this.config.preferSpeed = partial.preferSpeed;
    if (partial.preferCost !== undefined) this.config.preferCost = partial.preferCost;
    if (partial.pinnedModelId !== undefined) this.config.pinnedModelId = partial.pinnedModelId;
    if (partial.maxRequestCostUsd !== undefined) this.config.maxRequestCostUsd = partial.maxRequestCostUsd;
    if (partial.maxDecisionHistory !== undefined) this.config.maxDecisionHistory = partial.maxDecisionHistory;
    if (partial.fallbackModelId !== undefined) this.config.fallbackModelId = partial.fallbackModelId;
    this.scheduleSave();
    return { ...this.config };
  }

  // ── Context Generation ──────────────────────────────────────────

  /**
   * Generate context string for system prompt injection.
   * Shows routing status and budget awareness.
   */
  getPromptContext(): string {
    if (!this.config.enabled) return '';

    const lines: string[] = [];

    // Budget status
    if (this.config.monthlyBudgetUsd > 0) {
      const remaining = this.config.monthlyBudgetUsd - this.config.monthlySpentUsd;
      const pct = ((this.config.monthlySpentUsd / this.config.monthlyBudgetUsd) * 100).toFixed(0);
      lines.push(
        `BUDGET: $${this.config.monthlySpentUsd.toFixed(2)} / $${this.config.monthlyBudgetUsd.toFixed(2)} ` +
        `(${pct}% used, $${remaining.toFixed(2)} remaining)`
      );
      if (remaining < this.config.monthlyBudgetUsd * 0.1) {
        lines.push('  ⚠ Budget nearly exhausted — prefer cheaper models');
      }
    }

    // Pinned model
    if (this.config.pinnedModelId) {
      const model = this.getModel(this.config.pinnedModelId);
      lines.push(`PINNED MODEL: ${model?.name || this.config.pinnedModelId}`);
    }

    // Recent routing summary (last 5)
    const recent = this.getRecentDecisions(5);
    if (recent.length > 0) {
      lines.push('RECENT ROUTES:');
      for (const d of recent) {
        const model = this.getModel(d.selectedModelId);
        const name = model?.name || d.selectedModelId;
        const cost = d.actualCost ? ` ($${d.actualCost.toFixed(4)})` : '';
        const status = d.success === true ? '✓' : d.success === false ? '✗' : '…';
        lines.push(`  ${status} ${name} — ${d.taskProfile.category}/${d.taskProfile.complexity}${cost}`);
      }
    }

    return lines.join('\n');
  }

  // ── Internal Helpers ──────────────────────────────────────────────

  private createDecision(
    id: string,
    task: TaskProfile,
    selectedModelId: string,
    reason: string,
    scores: ModelScore[],
    budgetConstrained: boolean,
    isFallback: boolean
  ): RoutingDecision {
    return {
      id,
      timestamp: Date.now(),
      taskProfile: task,
      selectedModelId,
      reason,
      scores,
      budgetConstrained,
      isFallback,
      userOverride: null,
      durationMs: null,
      success: null,
      actualInputTokens: null,
      actualOutputTokens: null,
      actualCost: null,
    };
  }

  private recordDecision(decision: RoutingDecision): void {
    this.decisions.push(decision);

    // Enforce max history
    if (this.decisions.length > this.config.maxDecisionHistory) {
      this.decisions = this.decisions.slice(-this.config.maxDecisionHistory);
    }

    this.scheduleSave();
  }

  private isBudgetConstrained(task: TaskProfile, modelId: string): boolean {
    if (this.config.monthlyBudgetUsd <= 0) return false;
    const model = this.models.find((m) => m.modelId === modelId);
    if (!model) return true;
    const estimatedCost = estimateRequestCost(model, task.estimatedInputTokens, task.estimatedInputTokens * 0.5);
    const remaining = this.config.monthlyBudgetUsd - this.config.monthlySpentUsd;
    return estimatedCost > remaining;
  }

  private checkBudgetReset(): void {
    const now = new Date();
    if (now.getDate() === this.config.budgetResetDay) {
      // Check if we already reset today (compare with last decision timestamp)
      const lastDecision = this.decisions[this.decisions.length - 1];
      if (lastDecision) {
        const lastDate = new Date(lastDecision.timestamp);
        if (lastDate.getMonth() !== now.getMonth() || lastDate.getFullYear() !== now.getFullYear()) {
          this.config.monthlySpentUsd = 0;
          console.log('[Router] Monthly budget reset');
        }
      }
    }
  }

  // ── Persistence ─────────────────────────────────────────────────

  private async load(): Promise<void> {
    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      this.models = data.models || [];
      this.decisions = data.decisions || [];
      if (data.config) {
        this.config = { ...this.config, ...data.config };
      }
    } catch {
      // Fresh start
    }
  }

  private async save(): Promise<void> {
    if (!this.filePath) return;
    try {
      const data = {
        models: this.models,
        decisions: this.decisions,
        config: this.config,
        savedAt: Date.now(),
      };
      await fs.writeFile(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
    } catch (err: any) {
      console.warn('[Router] Save failed:', err?.message);
    }
  }

  private scheduleSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.save();
      this.saveTimer = null;
    }, 2000);
  }
}

export const intelligenceRouter = new IntelligenceRouter();
