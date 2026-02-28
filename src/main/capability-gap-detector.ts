/**
 * capability-gap-detector.ts — Self-Directed Capability Acquisition.
 *
 * Track II, Phase 5: The Absorber — Self-Directed Acquisition.
 *
 * Detects capability gaps when tool calls fail, classifies them into
 * capability categories, tracks gap frequency, and proposes superpower
 * acquisitions. The pipeline:
 *
 *   1. Gap Detection — A tool call fails or a task description maps to
 *      no available tool. Log it as a CapabilityGap.
 *
 *   2. Gap Classification — Map the failed request to a CapabilityCategory
 *      using keyword matching and pattern analysis. Distinguish between
 *      "missing tool" gaps (solvable) and "impossible request" gaps (not).
 *
 *   3. Gap Aggregation — Track gap frequency over time. A single failure
 *      is noise; repeated failures in the same category signal a real gap.
 *
 *   4. Proposal Generation — When a gap threshold is met, generate an
 *      AcquisitionProposal describing what capability is needed, why,
 *      and (optionally) candidate repos.
 *
 *   5. Proposal Lifecycle — Track user acceptance/rejection. Declined
 *      proposals enter a cooldown period (default 30 days). Accepted
 *      proposals hand off to the superpower install pipeline.
 *
 * cLaw Boundary: This module PROPOSES. It NEVER installs without explicit
 * user consent. The proposal pipeline cannot be manipulated by external
 * repo metadata — gap detection is purely internal, based on the agent's
 * own failed operations.
 */

import * as crypto from 'crypto';
import type { CapabilityCategory } from './capability-manifest';

// ── Capability Gap ──────────────────────────────────────────────────

export interface CapabilityGap {
  /** Unique gap ID */
  id: string;
  /** When this gap was first detected */
  firstSeen: number;
  /** When this gap was last triggered */
  lastSeen: number;
  /** Number of times this gap was triggered */
  hitCount: number;
  /** Classified capability category (or null if unclassifiable) */
  category: CapabilityCategory | null;
  /** The task descriptions that triggered this gap */
  taskDescriptions: string[];
  /** Whether this is a solvable gap or an impossible request */
  solvability: GapSolvability;
  /** Keywords extracted from the failed requests */
  keywords: string[];
  /** Whether a proposal has been generated for this gap */
  proposalGenerated: boolean;
}

export type GapSolvability =
  | 'solvable'       // Could be fixed by installing a tool/superpower
  | 'impossible'     // Fundamentally impossible (predict future, etc.)
  | 'uncertain';     // Not clear yet — need more signal

// ── Acquisition Proposal ────────────────────────────────────────────

export interface AcquisitionProposal {
  /** Unique proposal ID */
  id: string;
  /** The gap this proposal addresses */
  gapId: string;
  /** Human-readable title */
  title: string;
  /** Description of the capability needed */
  description: string;
  /** The category of capability needed */
  category: CapabilityCategory;
  /** How many times the gap was triggered */
  gapHitCount: number;
  /** When the proposal was created */
  createdAt: number;
  /** Proposal status */
  status: ProposalStatus;
  /** When the user responded (accepted/declined) */
  respondedAt: number | null;
  /** If declined, when can we re-propose? */
  cooldownUntil: number | null;
  /** How many times this proposal has been shown to the user */
  presentationCount: number;
  /** Suggested search terms for finding repos */
  suggestedSearchTerms: string[];
}

export type ProposalStatus =
  | 'pending'     // Created but not shown to user yet
  | 'presented'   // Shown to user, awaiting response
  | 'accepted'    // User wants this capability
  | 'declined'    // User said no (enters cooldown)
  | 'installed'   // Superpower was installed to fill this gap
  | 'expired';    // Gap stopped recurring, proposal became irrelevant

// ── Gap Detection Configuration ─────────────────────────────────────

export interface GapDetectorConfig {
  /** Number of gap hits before generating a proposal (default: 3) */
  proposalThreshold: number;
  /** Days to cooldown after a declined proposal (default: 30) */
  declineCooldownDays: number;
  /** Maximum number of gaps to track (default: 200) */
  maxGaps: number;
  /** Maximum task descriptions to store per gap (default: 10) */
  maxTaskDescriptionsPerGap: number;
  /** Days after which a gap with no new hits is pruned (default: 60) */
  gapExpiryDays: number;
  /** Whether self-directed acquisition is enabled (default: true) */
  enabled: boolean;
}

export const DEFAULT_GAP_DETECTOR_CONFIG: GapDetectorConfig = {
  proposalThreshold: 3,
  declineCooldownDays: 30,
  maxGaps: 200,
  maxTaskDescriptionsPerGap: 10,
  gapExpiryDays: 60,
  enabled: true,
};

// ── Category Classification ─────────────────────────────────────────

/**
 * Keyword-to-category mapping for gap classification.
 * Each entry maps patterns to a CapabilityCategory.
 */
const CATEGORY_KEYWORDS: Array<{
  category: CapabilityCategory;
  keywords: string[];
  weight: number;
}> = [
  {
    category: 'image-processing',
    keywords: [
      'image', 'photo', 'picture', 'png', 'jpg', 'jpeg', 'gif', 'heic',
      'webp', 'svg', 'resize', 'crop', 'thumbnail', 'convert image',
      'compress image', 'watermark', 'filter', 'brightness', 'contrast',
      'rotate image', 'flip image', 'ocr',
    ],
    weight: 1.0,
  },
  {
    category: 'audio-processing',
    keywords: [
      'audio', 'sound', 'music', 'mp3', 'wav', 'flac', 'ogg', 'aac',
      'transcribe', 'speech to text', 'text to speech', 'voice',
      'podcast', 'normalize audio', 'trim audio', 'merge audio',
    ],
    weight: 1.0,
  },
  {
    category: 'video-processing',
    keywords: [
      'video', 'mp4', 'avi', 'mov', 'mkv', 'clip', 'render',
      'encode', 'decode', 'stream', 'subtitle', 'frame', 'gif from video',
    ],
    weight: 1.0,
  },
  {
    category: 'data-processing',
    keywords: [
      'csv', 'excel', 'spreadsheet', 'parse', 'transform data',
      'etl', 'pipeline', 'json transform', 'xml', 'yaml', 'toml',
      'data cleaning', 'data validation', 'schema', 'normalize data',
    ],
    weight: 0.9,
  },
  {
    category: 'file-operations',
    keywords: [
      'pdf', 'docx', 'zip', 'unzip', 'compress', 'archive',
      'extract', 'file convert', 'rename files', 'batch files',
      'merge pdf', 'split pdf', 'epub', 'markdown to',
    ],
    weight: 0.9,
  },
  {
    category: 'text-processing',
    keywords: [
      'translate', 'summarize', 'grammar', 'spell check', 'regex',
      'template', 'format text', 'tokenize', 'sentiment',
      'extract entities', 'classify text', 'diff',
    ],
    weight: 0.8,
  },
  {
    category: 'network',
    keywords: [
      'api', 'http', 'fetch', 'download', 'upload', 'webhook',
      'scrape', 'crawl', 'rest', 'graphql', 'grpc', 'websocket',
    ],
    weight: 0.8,
  },
  {
    category: 'computation',
    keywords: [
      'calculate', 'math', 'statistics', 'chart', 'graph', 'plot',
      'regression', 'forecast', 'simulation', 'optimize', 'linear algebra',
    ],
    weight: 0.8,
  },
  {
    category: 'code-generation',
    keywords: [
      'generate code', 'scaffold', 'boilerplate', 'template code',
      'refactor', 'lint', 'format code', 'minify', 'transpile',
      'compile', 'bundle', 'ast',
    ],
    weight: 0.7,
  },
  {
    category: 'database',
    keywords: [
      'database', 'sql', 'query', 'sqlite', 'postgres', 'mysql',
      'mongo', 'redis', 'crud', 'migration', 'backup database',
    ],
    weight: 0.8,
  },
  {
    category: 'authentication',
    keywords: [
      'oauth', 'jwt', 'token', 'login', 'authenticate', 'password',
      'saml', 'sso', 'mfa', '2fa', 'session',
    ],
    weight: 0.7,
  },
  {
    category: 'messaging',
    keywords: [
      'email', 'smtp', 'notification', 'push', 'sms', 'telegram',
      'slack', 'discord', 'whatsapp', 'chat',
    ],
    weight: 0.7,
  },
  {
    category: 'scheduling',
    keywords: [
      'cron', 'schedule', 'timer', 'recurring', 'job queue',
      'background job', 'delayed', 'interval',
    ],
    weight: 0.7,
  },
  {
    category: 'system',
    keywords: [
      'process', 'spawn', 'exec', 'shell', 'command line', 'cli',
      'environment', 'os info', 'disk', 'memory usage', 'cpu',
    ],
    weight: 0.6,
  },
];

/**
 * Patterns that indicate an impossible/non-solvable request.
 * These are things no tool can do.
 */
const IMPOSSIBLE_PATTERNS: RegExp[] = [
  /predict\s+(?:the\s+)?(?:future|stock|market|lottery|winner)/i,
  /guarantee\s+(?:success|outcome|result)/i,
  /(?:hack|break\s*into|crack)\s+(?:[\w']+\s+)*?(?:password|account|system)/i,
  /read\s+(?:someone'?s?\s+)?(?:mind|thoughts)/i,
  /(?:time\s+travel|go\s+back\s+in\s+time)/i,
  /(?:create|generate)\s+(?:consciousness|sentience)/i,
  /(?:bypass|circumvent|disable)\s+(?:security|safety|law)/i,
  /100%\s+(?:accuracy|certainty|guarantee)/i,
  /(?:change|alter|modify)\s+(?:the\s+)?(?:past|history)/i,
];

// ── Gap Detector ────────────────────────────────────────────────────

export class CapabilityGapDetector {
  private gaps: CapabilityGap[] = [];
  private proposals: AcquisitionProposal[] = [];
  private config: GapDetectorConfig;

  constructor(config: Partial<GapDetectorConfig> = {}) {
    this.config = { ...DEFAULT_GAP_DETECTOR_CONFIG, ...config };
  }

  // ── Gap Recording ───────────────────────────────────────────────

  /**
   * Record a capability gap from a failed tool call or unresolvable task.
   *
   * This is the primary entry point — called whenever the agent tries
   * to do something and can't.
   */
  recordGap(taskDescription: string): CapabilityGap {
    if (!this.config.enabled) {
      // Return a stub gap when disabled
      return this.createStubGap(taskDescription);
    }

    const normalized = taskDescription.toLowerCase().trim();
    if (!normalized) {
      return this.createStubGap(taskDescription);
    }

    // Classify the task
    const category = classifyTask(normalized);
    const solvability = assessSolvability(normalized);
    const keywords = extractKeywords(normalized);

    // Try to find an existing gap with matching category + overlapping keywords
    const existing = this.findMatchingGap(category, keywords);
    if (existing) {
      existing.hitCount++;
      existing.lastSeen = Date.now();
      if (
        existing.taskDescriptions.length < this.config.maxTaskDescriptionsPerGap &&
        !existing.taskDescriptions.includes(taskDescription)
      ) {
        existing.taskDescriptions.push(taskDescription);
      }
      // Merge keywords
      for (const kw of keywords) {
        if (!existing.keywords.includes(kw)) {
          existing.keywords.push(kw);
        }
      }
      // Re-assess solvability with more data
      if (existing.solvability === 'uncertain' && solvability !== 'uncertain') {
        existing.solvability = solvability;
      }
      return existing;
    }

    // Create new gap
    const gap: CapabilityGap = {
      id: crypto.randomUUID().slice(0, 12),
      firstSeen: Date.now(),
      lastSeen: Date.now(),
      hitCount: 1,
      category,
      solvability,
      taskDescriptions: [taskDescription],
      keywords,
      proposalGenerated: false,
    };

    this.gaps.push(gap);
    this.enforceGapLimit();

    return gap;
  }

  /**
   * Find a gap that matches the same category and has overlapping keywords.
   */
  private findMatchingGap(
    category: CapabilityCategory | null,
    keywords: string[],
  ): CapabilityGap | null {
    if (!category) return null;

    for (const gap of this.gaps) {
      if (gap.category !== category) continue;
      // Check for keyword overlap (at least 1 match)
      const overlap = keywords.some(kw => gap.keywords.includes(kw));
      if (overlap) return gap;
    }
    return null;
  }

  // ── Proposal Generation ─────────────────────────────────────────

  /**
   * Check all gaps and generate proposals for any that have crossed
   * the threshold. Returns newly created proposals.
   */
  generateProposals(): AcquisitionProposal[] {
    if (!this.config.enabled) return [];

    const newProposals: AcquisitionProposal[] = [];

    for (const gap of this.gaps) {
      // Skip gaps that already have proposals
      if (gap.proposalGenerated) continue;
      // Skip unsolvable gaps
      if (gap.solvability === 'impossible') continue;
      // Skip unclassified gaps
      if (!gap.category) continue;
      // Check threshold
      if (gap.hitCount < this.config.proposalThreshold) continue;

      // Check if there's already a declined proposal in cooldown for this category
      if (this.isInCooldown(gap.category)) continue;

      // Generate proposal
      const proposal = this.createProposal(gap);
      this.proposals.push(proposal);
      gap.proposalGenerated = true;
      newProposals.push(proposal);
    }

    return newProposals;
  }

  /**
   * Check if a category has a recently declined proposal in cooldown.
   */
  private isInCooldown(category: CapabilityCategory): boolean {
    const now = Date.now();
    return this.proposals.some(p => {
      if (p.status !== 'declined') return false;
      if (p.category !== category) return false;
      return p.cooldownUntil !== null && now < p.cooldownUntil;
    });
  }

  /**
   * Create a proposal from a gap.
   */
  private createProposal(gap: CapabilityGap): AcquisitionProposal {
    const title = generateProposalTitle(gap);
    const description = generateProposalDescription(gap);
    const searchTerms = generateSearchTerms(gap);

    return {
      id: crypto.randomUUID().slice(0, 12),
      gapId: gap.id,
      title,
      description,
      category: gap.category!,
      gapHitCount: gap.hitCount,
      createdAt: Date.now(),
      status: 'pending',
      respondedAt: null,
      cooldownUntil: null,
      presentationCount: 0,
      suggestedSearchTerms: searchTerms,
    };
  }

  // ── Proposal Lifecycle ──────────────────────────────────────────

  /**
   * Mark a proposal as presented to the user.
   */
  presentProposal(proposalId: string): AcquisitionProposal | null {
    const proposal = this.proposals.find(p => p.id === proposalId);
    if (!proposal) return null;
    if (proposal.status === 'declined' || proposal.status === 'installed') return null;

    proposal.status = 'presented';
    proposal.presentationCount++;
    return proposal;
  }

  /**
   * User accepted the proposal — they want this capability.
   */
  acceptProposal(proposalId: string): AcquisitionProposal | null {
    const proposal = this.proposals.find(p => p.id === proposalId);
    if (!proposal) return null;

    proposal.status = 'accepted';
    proposal.respondedAt = Date.now();
    return proposal;
  }

  /**
   * User declined the proposal — enter cooldown.
   */
  declineProposal(proposalId: string): AcquisitionProposal | null {
    const proposal = this.proposals.find(p => p.id === proposalId);
    if (!proposal) return null;

    proposal.status = 'declined';
    proposal.respondedAt = Date.now();
    proposal.cooldownUntil = Date.now() + this.config.declineCooldownDays * 86_400_000;
    return proposal;
  }

  /**
   * Mark a proposal as installed (gap was filled).
   */
  markInstalled(proposalId: string): AcquisitionProposal | null {
    const proposal = this.proposals.find(p => p.id === proposalId);
    if (!proposal) return null;

    proposal.status = 'installed';
    proposal.respondedAt = Date.now();
    return proposal;
  }

  // ── Queries ─────────────────────────────────────────────────────

  /**
   * Get all pending proposals that should be shown to the user.
   */
  getPendingProposals(): AcquisitionProposal[] {
    return this.proposals.filter(
      p => p.status === 'pending' || p.status === 'presented',
    );
  }

  /**
   * Get all accepted proposals awaiting installation.
   */
  getAcceptedProposals(): AcquisitionProposal[] {
    return this.proposals.filter(p => p.status === 'accepted');
  }

  /**
   * Get the most frequent unsolved gaps.
   */
  getTopGaps(limit: number = 10): CapabilityGap[] {
    return [...this.gaps]
      .filter(g => g.solvability !== 'impossible')
      .sort((a, b) => b.hitCount - a.hitCount)
      .slice(0, limit);
  }

  /**
   * Get all gaps.
   */
  getAllGaps(): CapabilityGap[] {
    return [...this.gaps];
  }

  /**
   * Get all proposals.
   */
  getAllProposals(): AcquisitionProposal[] {
    return [...this.proposals];
  }

  /**
   * Get a specific gap by ID.
   */
  getGap(id: string): CapabilityGap | null {
    return this.gaps.find(g => g.id === id) || null;
  }

  /**
   * Get a specific proposal by ID.
   */
  getProposal(id: string): AcquisitionProposal | null {
    return this.proposals.find(p => p.id === id) || null;
  }

  // ── Maintenance ─────────────────────────────────────────────────

  /**
   * Prune expired gaps and proposals.
   */
  prune(): { gapsPruned: number; proposalsExpired: number } {
    const now = Date.now();
    const expiryMs = this.config.gapExpiryDays * 86_400_000;

    const beforeGaps = this.gaps.length;
    this.gaps = this.gaps.filter(g => {
      const age = now - g.lastSeen;
      return age < expiryMs;
    });
    const gapsPruned = beforeGaps - this.gaps.length;

    // Expire proposals whose gaps no longer exist
    let proposalsExpired = 0;
    for (const p of this.proposals) {
      if (p.status === 'pending' || p.status === 'presented') {
        const gapExists = this.gaps.some(g => g.id === p.gapId);
        if (!gapExists) {
          p.status = 'expired';
          proposalsExpired++;
        }
      }
    }

    return { gapsPruned, proposalsExpired };
  }

  /**
   * Enforce gap limit by removing oldest, lowest-hit gaps.
   */
  private enforceGapLimit(): void {
    if (this.gaps.length <= this.config.maxGaps) return;

    // Sort by hitCount ascending, then by lastSeen ascending (least important first)
    this.gaps.sort((a, b) => {
      if (a.hitCount !== b.hitCount) return a.hitCount - b.hitCount;
      return a.lastSeen - b.lastSeen;
    });

    // Remove excess from the front (least important)
    this.gaps = this.gaps.slice(this.gaps.length - this.config.maxGaps);
  }

  // ── Status ──────────────────────────────────────────────────────

  /**
   * Get aggregate status.
   */
  getStatus(): GapDetectorStatus {
    return {
      totalGaps: this.gaps.length,
      solvableGaps: this.gaps.filter(g => g.solvability === 'solvable').length,
      impossibleGaps: this.gaps.filter(g => g.solvability === 'impossible').length,
      uncertainGaps: this.gaps.filter(g => g.solvability === 'uncertain').length,
      totalProposals: this.proposals.length,
      pendingProposals: this.proposals.filter(p => p.status === 'pending' || p.status === 'presented').length,
      acceptedProposals: this.proposals.filter(p => p.status === 'accepted').length,
      declinedProposals: this.proposals.filter(p => p.status === 'declined').length,
      installedProposals: this.proposals.filter(p => p.status === 'installed').length,
      enabled: this.config.enabled,
    };
  }

  /**
   * Get a context string for system prompt injection.
   */
  getPromptContext(): string {
    if (!this.config.enabled) return '';

    const pending = this.getPendingProposals();
    const topGaps = this.getTopGaps(5);

    if (pending.length === 0 && topGaps.length === 0) return '';

    let context = '';

    if (pending.length > 0) {
      context += 'CAPABILITY PROPOSALS (awaiting user decision):\n';
      for (const p of pending) {
        context += `- ${p.title} (triggered ${p.gapHitCount}x)\n`;
      }
    }

    if (topGaps.length > 0) {
      const unproposed = topGaps.filter(g => !g.proposalGenerated);
      if (unproposed.length > 0) {
        if (context) context += '\n';
        context += 'DETECTED CAPABILITY GAPS:\n';
        for (const g of unproposed) {
          context += `- ${g.category || 'unknown'}: ${g.taskDescriptions[0]} (${g.hitCount}x)\n`;
        }
      }
    }

    return context;
  }

  // ── Serialization ───────────────────────────────────────────────

  /**
   * Export state for persistence.
   */
  export(): GapDetectorState {
    return {
      gaps: [...this.gaps],
      proposals: [...this.proposals],
      config: { ...this.config },
    };
  }

  /**
   * Import state from persistence.
   */
  import(state: GapDetectorState): void {
    this.gaps = [...state.gaps];
    this.proposals = [...state.proposals];
    if (state.config) {
      this.config = { ...this.config, ...state.config };
    }
  }

  // ── Private Helpers ─────────────────────────────────────────────

  private createStubGap(taskDescription: string): CapabilityGap {
    return {
      id: 'stub',
      firstSeen: Date.now(),
      lastSeen: Date.now(),
      hitCount: 0,
      category: null,
      solvability: 'uncertain',
      taskDescriptions: [taskDescription],
      keywords: [],
      proposalGenerated: false,
    };
  }
}

// ── Status Type ─────────────────────────────────────────────────────

export interface GapDetectorStatus {
  totalGaps: number;
  solvableGaps: number;
  impossibleGaps: number;
  uncertainGaps: number;
  totalProposals: number;
  pendingProposals: number;
  acceptedProposals: number;
  declinedProposals: number;
  installedProposals: number;
  enabled: boolean;
}

// ── Serialization Type ──────────────────────────────────────────────

export interface GapDetectorState {
  gaps: CapabilityGap[];
  proposals: AcquisitionProposal[];
  config?: Partial<GapDetectorConfig>;
}

// ── Classification Functions ────────────────────────────────────────

/**
 * Classify a task description into a CapabilityCategory.
 * Uses keyword matching with weighted scoring.
 */
export function classifyTask(description: string): CapabilityCategory | null {
  const lower = description.toLowerCase();
  let bestCategory: CapabilityCategory | null = null;
  let bestScore = 0;

  for (const entry of CATEGORY_KEYWORDS) {
    let score = 0;
    for (const kw of entry.keywords) {
      if (lower.includes(kw)) {
        score += entry.weight;
      }
    }
    if (score > bestScore) {
      bestScore = score;
      bestCategory = entry.category;
    }
  }

  // Require a minimum score to classify
  return bestScore >= 0.6 ? bestCategory : null;
}

/**
 * Assess whether a task is solvable by installing a tool.
 */
export function assessSolvability(description: string): GapSolvability {
  // Check impossible patterns
  for (const pattern of IMPOSSIBLE_PATTERNS) {
    if (pattern.test(description)) {
      return 'impossible';
    }
  }

  // If we can classify it, it's likely solvable
  const category = classifyTask(description);
  if (category) return 'solvable';

  return 'uncertain';
}

/**
 * Extract meaningful keywords from a task description.
 */
export function extractKeywords(description: string): string[] {
  const lower = description.toLowerCase();
  const words = lower
    .replace(/[^a-z0-9\s-]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 2);

  // Remove stop words
  const stopWords = new Set([
    'the', 'and', 'for', 'but', 'not', 'you', 'all', 'can', 'had', 'her',
    'was', 'one', 'our', 'out', 'are', 'has', 'his', 'how', 'its', 'may',
    'new', 'now', 'old', 'see', 'way', 'who', 'did', 'get', 'let', 'say',
    'she', 'too', 'use', 'this', 'that', 'with', 'have', 'from', 'they',
    'been', 'some', 'than', 'them', 'then', 'what', 'when', 'will', 'more',
    'want', 'need', 'please', 'could', 'would', 'should', 'into', 'just',
    'also', 'like', 'make', 'take', 'help', 'file', 'able',
  ]);

  return [...new Set(words.filter(w => !stopWords.has(w)))].slice(0, 15);
}

// ── Proposal Text Generation ────────────────────────────────────────

/**
 * Generate a human-readable proposal title from a gap.
 */
function generateProposalTitle(gap: CapabilityGap): string {
  const categoryNames: Record<string, string> = {
    'image-processing': 'Image Processing',
    'audio-processing': 'Audio Processing',
    'video-processing': 'Video Processing',
    'data-processing': 'Data Processing',
    'file-operations': 'File Operations',
    'text-processing': 'Text Processing',
    'network': 'Network & API',
    'computation': 'Computation & Math',
    'code-generation': 'Code Generation',
    'database': 'Database Operations',
    'authentication': 'Authentication',
    'messaging': 'Messaging',
    'scheduling': 'Scheduling',
    'system': 'System Tools',
    'utility': 'Utilities',
    'other': 'General',
  };

  const categoryName = categoryNames[gap.category || 'other'] || 'General';
  return `Add ${categoryName} Capability`;
}

/**
 * Generate a human-readable proposal description.
 */
function generateProposalDescription(gap: CapabilityGap): string {
  const examples = gap.taskDescriptions.slice(0, 3);
  const exampleText = examples.map(t => `"${t}"`).join(', ');

  return (
    `I noticed I've been unable to help with ${gap.category || 'certain'} tasks ` +
    `${gap.hitCount} times recently. For example: ${exampleText}. ` +
    `Installing a superpower could help me handle these requests.`
  );
}

/**
 * Generate search terms for finding repos.
 */
function generateSearchTerms(gap: CapabilityGap): string[] {
  const terms: string[] = [];

  if (gap.category) {
    terms.push(gap.category.replace(/-/g, ' '));
  }

  // Add top keywords (most unique to this gap)
  const topKeywords = gap.keywords.slice(0, 5);
  terms.push(...topKeywords);

  // Add language-specific terms
  terms.push('typescript', 'node');

  return [...new Set(terms)].slice(0, 8);
}

// ── Singleton Export ────────────────────────────────────────────────

export const capabilityGapDetector = new CapabilityGapDetector();
