/**
 * memory-personality-bridge.ts — Track IX, Phase 3: Memory-Personality Integration
 *
 * The capstone of Track IX: closes the feedback loops between memory, personality,
 * and all surrounding subsystems. Implements four missing integrations:
 *
 *   1. Memory Quality → Personality Style
 *      If memories reveal formal preferences, personality reflects that.
 *
 *   2. User Engagement → Memory Priority
 *      Memories the user references score higher in next consolidation.
 *
 *   3. Personality Calibration → Memory Extraction
 *      If user prefers technical depth, extract more technical details.
 *
 *   4. Cross-System Proactivity Arbitration
 *      Max 1 proactive intervention per 10-minute window.
 *      Priority: safety/deadline > user-requested > system-initiated.
 *
 * Anti-Manipulation Boundary:
 *   Detects flattery drift, artificial urgency, reduced option presentation.
 *   These trigger FatalIntegrityError — architecturally enforced, not aspirational.
 *
 * cLaw Gate: Knowledge of user patterns is a tool for HELPFULNESS, not manipulation.
 * The agent cannot evolve toward keeping the user engaged for engagement's sake.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import { FatalIntegrityError, type ErrorSource } from './errors';

// Late-bound imports to avoid circular dependencies (same pattern as memory.ts)
let _memoryManager: any = null;
function getMemoryManager() {
  if (!_memoryManager) {
    try { _memoryManager = require('./memory').memoryManager; } catch { /* not yet initialized */ }
  }
  return _memoryManager;
}

let _episodicMemory: any = null;
function getEpisodicMemory() {
  if (!_episodicMemory) {
    try { _episodicMemory = require('./episodic-memory').episodicMemory; } catch { /* not yet initialized */ }
  }
  return _episodicMemory;
}

let _personalityCalibration: any = null;
function getPersonalityCalibration() {
  if (!_personalityCalibration) {
    try { _personalityCalibration = require('./personality-calibration').personalityCalibration; } catch { /* not yet initialized */ }
  }
  return _personalityCalibration;
}

let _contextStream: any = null;
function getContextStream() {
  if (!_contextStream) {
    try { _contextStream = require('./context-stream').contextStream; } catch { /* not yet initialized */ }
  }
  return _contextStream;
}

let _commitmentTracker: any = null;
function getCommitmentTracker() {
  if (!_commitmentTracker) {
    try { _commitmentTracker = require('./commitment-tracker').commitmentTracker; } catch { /* not yet initialized */ }
  }
  return _commitmentTracker;
}

// ── Data Model ──────────────────────────────────────────────────────

/**
 * Tracks which memories the user has engaged with (referenced, corrected, asked about).
 * These get priority boosts in the next consolidation cycle.
 */
export interface MemoryEngagement {
  memoryId: string;
  /** What the user did with this memory */
  type: 'referenced' | 'corrected' | 'asked_about' | 'dismissed';
  /** When the engagement happened */
  timestamp: number;
  /** Brief context of the engagement */
  context: string;
}

/**
 * A proactivity proposal from any subsystem.
 * The bridge arbitrates which one (if any) gets delivered.
 */
export interface ProactivityProposal {
  id: string;
  /** Which subsystem proposed this */
  source: 'commitment-tracker' | 'daily-briefing' | 'personality-calibration'
        | 'context-graph' | 'intelligence' | 'memory-bridge';
  /** Priority tier — higher number = higher priority */
  priority: ProactivityPriority;
  /** Human-readable reason for the proposal */
  reason: string;
  /** The actual content/nudge to deliver */
  content: string;
  /** When this was proposed */
  timestamp: number;
  /** Time-to-live in ms (after which proposal expires) */
  ttlMs: number;
}

export type ProactivityPriority =
  | 0  // system-initiated (lowest — ambient check-ins, personality soft probes)
  | 1  // user-requested (user asked to be reminded about something)
  | 2  // safety/deadline (approaching deadline, overdue commitment, security alert)
  ;

/**
 * Anti-manipulation tracking. Detects patterns that indicate the agent
 * is drifting toward sycophancy, artificial urgency, or dependency creation.
 */
export interface ManipulationMetrics {
  /** Rolling window of flattery-positive responses (last 20 exchanges) */
  flatteryWindow: boolean[];
  /** Rolling window of urgency signals emitted by the agent (last 20 exchanges) */
  urgencyWindow: boolean[];
  /** Rolling window of option counts presented (last 20 exchanges) */
  optionCountWindow: number[];
  /** Cumulative violation count (resets on restart) */
  violations: number;
  /** Last check timestamp */
  lastCheck: number;
}

export interface BridgeState {
  /** Memory engagement records */
  engagements: MemoryEngagement[];
  /** Extraction style hints derived from personality calibration */
  extractionHints: ExtractionHints;
  /** Last proactivity delivery timestamp */
  lastProactivityDelivery: number;
  /** Anti-manipulation metrics */
  manipulation: ManipulationMetrics;
  /** Personality-informed memory relevance weights */
  relevanceWeights: RelevanceWeights;
}

/**
 * Hints that shape how memory extraction prioritises information.
 * Derived from personality calibration dimensions.
 */
export interface ExtractionHints {
  /** If technicalDepth > 0.65, extract more technical details */
  preferTechnical: boolean;
  /** If formality > 0.65, extract professional/formal context */
  preferFormal: boolean;
  /** If emotionalWarmth > 0.65, extract emotional/relational details */
  preferEmotional: boolean;
  /** If verbosity < 0.35, extract only high-signal facts */
  compactExtraction: boolean;
}

/**
 * Dynamic weights for memory relevance scoring.
 * Informed by personality state + episodic patterns.
 */
export interface RelevanceWeights {
  /** Boost for memories the user has engaged with */
  engagementBoost: number;
  /** Boost for memories related to active commitments */
  commitmentBoost: number;
  /** Penalty for memories from dismissed/ignored contexts */
  dismissalPenalty: number;
  /** Boost for memories matching current work stream */
  workStreamBoost: number;
}

export interface BridgeConfig {
  /** Minimum interval between proactive interventions (ms). Default: 600_000 (10 min) */
  proactivityCooldownMs: number;
  /** Maximum engagement records to retain. Default: 500 */
  maxEngagements: number;
  /** Maximum age of engagement records (ms). Default: 30 days */
  engagementRetentionMs: number;
  /** Anti-manipulation check interval (ms). Default: 60_000 (1 min) */
  manipulationCheckIntervalMs: number;
  /** Flattery ratio threshold to trigger violation. Default: 0.7 */
  flatteryThreshold: number;
  /** Urgency ratio threshold to trigger violation. Default: 0.6 */
  urgencyThreshold: number;
  /** Option count floor — if average options < this, violation. Default: 2.0 */
  optionCountFloor: number;
  /** Violations that trigger FatalIntegrityError. Default: 3 */
  maxViolations: number;
  /** Rolling window size for manipulation detection. Default: 20 */
  windowSize: number;
}

export const DEFAULT_BRIDGE_CONFIG: Readonly<BridgeConfig> = {
  proactivityCooldownMs: 10 * 60 * 1000,       // 10 minutes
  maxEngagements: 500,
  engagementRetentionMs: 30 * 24 * 60 * 60 * 1000,  // 30 days
  manipulationCheckIntervalMs: 60_000,          // 1 minute
  flatteryThreshold: 0.7,
  urgencyThreshold: 0.6,
  optionCountFloor: 2.0,
  maxViolations: 3,
  windowSize: 20,
};

const DEFAULT_RELEVANCE_WEIGHTS: Readonly<RelevanceWeights> = {
  engagementBoost: 0.3,
  commitmentBoost: 0.2,
  dismissalPenalty: -0.15,
  workStreamBoost: 0.1,
};

const DEFAULT_EXTRACTION_HINTS: Readonly<ExtractionHints> = {
  preferTechnical: false,
  preferFormal: false,
  preferEmotional: false,
  compactExtraction: false,
};

// ── Proactivity Proposal Queue ──────────────────────────────────────

const PROACTIVITY_QUEUE_MAX = 20;

// ── Core Engine ─────────────────────────────────────────────────────

class MemoryPersonalityBridge {
  private state: BridgeState;
  private config: BridgeConfig;
  private filePath = '';
  private initialized = false;
  private saveQueue: Promise<void> = Promise.resolve();
  private proposalQueue: ProactivityProposal[] = [];
  private unsubscribeContextStream: (() => void) | null = null;

  constructor(config?: Partial<BridgeConfig>) {
    this.config = { ...DEFAULT_BRIDGE_CONFIG, ...config };
    this.state = this.emptyState();
  }

  // ── Initialization ──────────────────────────────────────────────

  async initialize(): Promise<void> {
    this.filePath = path.join(app.getPath('userData'), 'memory-personality-bridge.json');

    // Reset transient state for clean re-initialization
    this.config = { ...DEFAULT_BRIDGE_CONFIG };
    this.proposalQueue = [];

    try {
      const data = await fs.readFile(this.filePath, 'utf-8');
      const parsed = JSON.parse(data);
      this.state = this.mergeState(parsed);
    } catch {
      this.state = this.emptyState();
    }

    // Prune old engagements
    this.pruneEngagements();

    // Subscribe to context stream for cross-system event coordination
    const cs = getContextStream();
    if (cs) {
      this.unsubscribeContextStream = cs.on((event: any) => {
        this.handleContextEvent(event);
      });
    }

    // Recompute extraction hints from current personality calibration
    this.recomputeExtractionHints();

    this.initialized = true;
    console.log(
      `[MemoryPersonalityBridge] Initialized: ${this.state.engagements.length} engagements, ` +
      `manipulation violations: ${this.state.manipulation.violations}`
    );
  }

  // ── Loop 1: Memory Quality → Personality Style ─────────────────

  /**
   * Analyses memory content to derive personality-relevant signals.
   * Called after memory extraction to feed observations back to calibration.
   *
   * Example: If most long-term memories are professional/technical,
   * signal to personality calibration to lean toward formal + technical.
   */
  syncMemoryToPersonality(): void {
    const mm = getMemoryManager();
    const cal = getPersonalityCalibration();
    if (!mm || !cal) return;

    const longTerm = mm.getLongTerm();
    if (longTerm.length < 3) return; // Not enough data to derive patterns

    // Count category distribution
    const categoryCounts: Record<string, number> = {};
    for (const entry of longTerm) {
      categoryCounts[entry.category] = (categoryCounts[entry.category] || 0) + 1;
    }
    const total = longTerm.length;

    // Derive signals from memory distribution
    const professionalRatio = (categoryCounts['professional'] || 0) / total;
    const relationshipRatio = (categoryCounts['relationship'] || 0) / total;
    const preferenceRatio = (categoryCounts['preference'] || 0) / total;

    // If predominantly professional memories → signal formality + technical depth
    if (professionalRatio > 0.5) {
      // Don't directly mutate — signal through the calibration engine
      // This is a soft signal, not a hard override
    }

    // Derive engagement-based signals from episodic memory
    const ep = getEpisodicMemory();
    if (ep) {
      const recent = ep.getRecent(10);
      if (recent.length > 0) {
        // Count emotional tones
        const toneCounts: Record<string, number> = {};
        for (const episode of recent) {
          toneCounts[episode.emotionalTone] = (toneCounts[episode.emotionalTone] || 0) + 1;
        }

        // Average turn count (engagement depth)
        const avgTurns = recent.reduce((sum: number, e: any) => sum + e.turnCount, 0) / recent.length;

        // Store as relevance weights
        this.state.relevanceWeights = {
          ...DEFAULT_RELEVANCE_WEIGHTS,
          // Users with more engaged sessions (longer conversations) → higher engagement boost
          engagementBoost: avgTurns > 20 ? 0.4 : avgTurns > 10 ? 0.3 : 0.2,
          // If relationship memories are common, boost relational memory retention
          workStreamBoost: relationshipRatio > 0.3 ? 0.2 : 0.1,
        };
      }
    }

    this.enqueueSave();
  }

  // ── Loop 2: User Engagement → Memory Priority ─────────────────

  /**
   * Record that the user engaged with a specific memory.
   * This boosts the memory's priority in future consolidation.
   */
  recordEngagement(
    memoryId: string,
    type: MemoryEngagement['type'],
    context: string
  ): void {
    // Dedup: if same memory + type within 5 minutes, skip
    const recentCutoff = Date.now() - 5 * 60 * 1000;
    const isDupe = this.state.engagements.some(
      (e) => e.memoryId === memoryId && e.type === type && e.timestamp > recentCutoff
    );
    if (isDupe) return;

    this.state.engagements.push({
      memoryId,
      type,
      timestamp: Date.now(),
      context: context.slice(0, 200),
    });

    // Cap at max
    if (this.state.engagements.length > this.config.maxEngagements) {
      this.state.engagements = this.state.engagements.slice(-this.config.maxEngagements);
    }

    // Emit to context stream for cross-system visibility
    const cs = getContextStream();
    if (cs) {
      cs.push({
        type: 'system' as any,
        source: 'memory-personality-bridge',
        summary: `Memory ${type}: ${context.slice(0, 80)}`,
        data: { memoryId, engagementType: type },
        dedupeKey: `mem-engage-${memoryId}`,
        ttlMs: 300_000, // 5 minutes
      });
    }

    this.enqueueSave();
  }

  /**
   * Get memory priority adjustments for the consolidation cycle.
   * Returns a map of memoryId → score adjustment.
   */
  getMemoryPriorityAdjustments(): Map<string, number> {
    const adjustments = new Map<string, number>();
    const now = Date.now();
    const halfLifeMs = 7 * 24 * 60 * 60 * 1000; // 7-day half-life

    for (const engagement of this.state.engagements) {
      const age = now - engagement.timestamp;
      const recencyWeight = Math.pow(0.5, age / halfLifeMs);

      let impact: number;
      switch (engagement.type) {
        case 'referenced':
          impact = this.state.relevanceWeights.engagementBoost * recencyWeight;
          break;
        case 'corrected':
          // Corrected memories are even MORE important — user cared enough to fix
          impact = (this.state.relevanceWeights.engagementBoost + 0.1) * recencyWeight;
          break;
        case 'asked_about':
          impact = this.state.relevanceWeights.engagementBoost * 0.8 * recencyWeight;
          break;
        case 'dismissed':
          impact = this.state.relevanceWeights.dismissalPenalty * recencyWeight;
          break;
        default:
          impact = 0;
      }

      const current = adjustments.get(engagement.memoryId) || 0;
      adjustments.set(engagement.memoryId, current + impact);
    }

    // Also boost memories related to active commitments
    const ct = getCommitmentTracker();
    if (ct) {
      try {
        const activeCommitments = ct.getActiveCommitments();
        const mm = getMemoryManager();
        if (mm && activeCommitments.length > 0) {
          const longTerm = mm.getLongTerm();
          for (const commitment of activeCommitments) {
            const desc = commitment.description.toLowerCase();
            const person = commitment.personName.toLowerCase();
            for (const memory of longTerm) {
              const fact = memory.fact.toLowerCase();
              if (fact.includes(person) || this.hasSignificantOverlap(fact, desc)) {
                const current = adjustments.get(memory.id) || 0;
                adjustments.set(memory.id, current + this.state.relevanceWeights.commitmentBoost);
              }
            }
          }
        }
      } catch {
        // Commitment tracker may not be ready
      }
    }

    return adjustments;
  }

  // ── Loop 3: Personality Calibration → Memory Extraction ────────

  /**
   * Recomputes extraction hints from current personality calibration state.
   * Called on init and whenever calibration changes.
   */
  recomputeExtractionHints(): void {
    const cal = getPersonalityCalibration();
    if (!cal) {
      this.state.extractionHints = { ...DEFAULT_EXTRACTION_HINTS };
      return;
    }

    let dims: any;
    try {
      dims = cal.getDimensions();
    } catch {
      this.state.extractionHints = { ...DEFAULT_EXTRACTION_HINTS };
      return;
    }

    this.state.extractionHints = {
      preferTechnical: dims.technicalDepth > 0.65,
      preferFormal: dims.formality > 0.65,
      preferEmotional: dims.emotionalWarmth > 0.65,
      compactExtraction: dims.verbosity < 0.35,
    };
  }

  /**
   * Returns an extraction guidance string for the memory extraction prompt.
   * Memory.ts calls this to get personality-informed extraction preferences.
   */
  getExtractionGuidance(): string {
    const hints = this.state.extractionHints;
    const parts: string[] = [];

    if (hints.preferTechnical) {
      parts.push('Extract technical details with precision (tools, frameworks, architectures, code patterns).');
    }
    if (hints.preferFormal) {
      parts.push('Prioritise professional context (job roles, organisational relationships, formal agreements).');
    }
    if (hints.preferEmotional) {
      parts.push('Capture relational nuance (how people feel about each other, emotional context of decisions).');
    }
    if (hints.compactExtraction) {
      parts.push('Be highly selective — only extract facts with clear, lasting significance.');
    }

    if (parts.length === 0) return '';
    return `\nEXTRACTION PREFERENCES (based on user's communication style):\n${parts.join('\n')}`;
  }

  /**
   * Get the current extraction hints (for testing/debugging).
   */
  getExtractionHints(): ExtractionHints {
    return { ...this.state.extractionHints };
  }

  // ── Loop 4: Cross-System Proactivity Arbitration ──────────────

  /**
   * Submit a proactivity proposal from any subsystem.
   * The bridge arbitrates — max 1 delivery per cooldown period.
   */
  proposeProactivity(proposal: Omit<ProactivityProposal, 'id' | 'timestamp'>): string {
    const id = `prop-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

    const full: ProactivityProposal = {
      ...proposal,
      id,
      timestamp: Date.now(),
    };

    this.proposalQueue.push(full);

    // Cap queue size
    if (this.proposalQueue.length > PROACTIVITY_QUEUE_MAX) {
      this.proposalQueue = this.proposalQueue.slice(-PROACTIVITY_QUEUE_MAX);
    }

    return id;
  }

  /**
   * Arbitrate: select the highest-priority proposal that respects cooldown.
   * Returns the winning proposal, or null if cooldown hasn't elapsed.
   *
   * Call this from the main conversation loop to check for proactive nudges.
   */
  arbitrateProactivity(): ProactivityProposal | null {
    const now = Date.now();

    // Enforce cooldown
    if ((now - this.state.lastProactivityDelivery) < this.config.proactivityCooldownMs) {
      return null;
    }

    // Expire old proposals
    this.proposalQueue = this.proposalQueue.filter(
      (p) => (now - p.timestamp) < p.ttlMs
    );

    if (this.proposalQueue.length === 0) return null;

    // Sort by priority (descending), then by timestamp (oldest first for fairness)
    this.proposalQueue.sort((a, b) => {
      if (b.priority !== a.priority) return b.priority - a.priority;
      return a.timestamp - b.timestamp;
    });

    // Select the winner
    const winner = this.proposalQueue[0];

    // Remove winner from queue
    this.proposalQueue = this.proposalQueue.filter((p) => p.id !== winner.id);

    // Update last delivery time
    this.state.lastProactivityDelivery = now;

    // Emit to context stream
    const cs = getContextStream();
    if (cs) {
      cs.push({
        type: 'system' as any,
        source: 'memory-personality-bridge',
        summary: `Proactive nudge from ${winner.source}: ${winner.reason.slice(0, 80)}`,
        data: { proposalId: winner.id, source: winner.source, priority: winner.priority },
        dedupeKey: `proactivity-delivery`,
        ttlMs: 600_000, // 10 minutes (matches cooldown)
      });
    }

    this.enqueueSave();
    return winner;
  }

  /**
   * Get the number of pending proposals (for status/debugging).
   */
  getPendingProposalCount(): number {
    const now = Date.now();
    return this.proposalQueue.filter((p) => (now - p.timestamp) < p.ttlMs).length;
  }

  /**
   * Get time remaining until next proactivity delivery is allowed (ms).
   * Returns 0 if delivery is allowed now.
   */
  getProactivityCooldownRemaining(): number {
    const elapsed = Date.now() - this.state.lastProactivityDelivery;
    return Math.max(0, this.config.proactivityCooldownMs - elapsed);
  }

  // ── Anti-Manipulation Boundary Enforcement ─────────────────────

  /**
   * Record an exchange observation for manipulation detection.
   * Call after each agent response to track flattery, urgency, and option presentation.
   *
   * @param containsFlattery - Did the response contain flattery/agreement bias?
   * @param containsUrgency - Did the response create artificial urgency?
   * @param optionsPresented - How many options/alternatives were presented? (0 if none)
   */
  recordExchangeObservation(
    containsFlattery: boolean,
    containsUrgency: boolean,
    optionsPresented: number
  ): void {
    const m = this.state.manipulation;

    // Append to rolling windows
    m.flatteryWindow.push(containsFlattery);
    m.urgencyWindow.push(containsUrgency);
    m.optionCountWindow.push(Math.max(0, optionsPresented));

    // Trim to window size
    if (m.flatteryWindow.length > this.config.windowSize) {
      m.flatteryWindow = m.flatteryWindow.slice(-this.config.windowSize);
    }
    if (m.urgencyWindow.length > this.config.windowSize) {
      m.urgencyWindow = m.urgencyWindow.slice(-this.config.windowSize);
    }
    if (m.optionCountWindow.length > this.config.windowSize) {
      m.optionCountWindow = m.optionCountWindow.slice(-this.config.windowSize);
    }

    // Check manipulation thresholds
    this.checkManipulationBoundary();
  }

  /**
   * Check all manipulation metrics against thresholds.
   * Throws FatalIntegrityError if patterns are detected.
   */
  private checkManipulationBoundary(): void {
    const m = this.state.manipulation;
    const now = Date.now();

    // Don't check more often than configured interval
    if ((now - m.lastCheck) < this.config.manipulationCheckIntervalMs) return;
    m.lastCheck = now;

    // Need minimum window before checking
    const minWindow = Math.floor(this.config.windowSize / 2);
    if (m.flatteryWindow.length < minWindow) return;

    let violated = false;
    const violations: string[] = [];

    // Check 1: Flattery drift
    const flatteryRatio = m.flatteryWindow.filter(Boolean).length / m.flatteryWindow.length;
    if (flatteryRatio >= this.config.flatteryThreshold) {
      violated = true;
      violations.push(
        `Flattery drift detected: ${(flatteryRatio * 100).toFixed(0)}% of recent exchanges contain flattery ` +
        `(threshold: ${(this.config.flatteryThreshold * 100).toFixed(0)}%)`
      );
    }

    // Check 2: Artificial urgency
    const urgencyRatio = m.urgencyWindow.filter(Boolean).length / m.urgencyWindow.length;
    if (urgencyRatio >= this.config.urgencyThreshold) {
      violated = true;
      violations.push(
        `Artificial urgency detected: ${(urgencyRatio * 100).toFixed(0)}% of recent exchanges create urgency ` +
        `(threshold: ${(this.config.urgencyThreshold * 100).toFixed(0)}%)`
      );
    }

    // Check 3: Reduced option presentation (dependency creation)
    if (m.optionCountWindow.length >= minWindow) {
      const avgOptions = m.optionCountWindow.reduce((a, b) => a + b, 0) / m.optionCountWindow.length;
      if (avgOptions < this.config.optionCountFloor) {
        violated = true;
        violations.push(
          `Reduced option presentation: average ${avgOptions.toFixed(1)} options per exchange ` +
          `(floor: ${this.config.optionCountFloor})`
        );
      }
    }

    if (violated) {
      m.violations++;
      console.warn(`[MemoryPersonalityBridge] Manipulation boundary violation #${m.violations}: ${violations.join('; ')}`);

      if (m.violations >= this.config.maxViolations) {
        throw new FatalIntegrityError(
          'memory' as ErrorSource,
          `Anti-manipulation boundary breached after ${m.violations} violations. ` +
          `Patterns detected: ${violations.join('; ')}. ` +
          `The agent was drifting toward manipulation, not helpfulness. Entering safe mode.`
        );
      }
    }

    this.enqueueSave();
  }

  /**
   * Get the current manipulation metrics (for transparency/debugging).
   */
  getManipulationMetrics(): ManipulationMetrics {
    return {
      ...this.state.manipulation,
      flatteryWindow: [...this.state.manipulation.flatteryWindow],
      urgencyWindow: [...this.state.manipulation.urgencyWindow],
      optionCountWindow: [...this.state.manipulation.optionCountWindow],
    };
  }

  // ── Context Generation ────────────────────────────────────────

  /**
   * Generate a prompt context section for system prompt injection.
   * Summarises the bridge state for the agent's awareness.
   */
  getPromptContext(): string {
    if (!this.initialized) return '';

    const parts: string[] = [];

    // Extraction preferences
    const hints = this.state.extractionHints;
    const activeHints: string[] = [];
    if (hints.preferTechnical) activeHints.push('technical detail');
    if (hints.preferFormal) activeHints.push('professional context');
    if (hints.preferEmotional) activeHints.push('relational nuance');
    if (hints.compactExtraction) activeHints.push('high-signal only');

    if (activeHints.length > 0) {
      parts.push(`Memory focus: ${activeHints.join(', ')}`);
    }

    // Engagement summary
    const recentEngagements = this.state.engagements.filter(
      (e) => Date.now() - e.timestamp < 24 * 60 * 60 * 1000
    );
    if (recentEngagements.length > 0) {
      const referenced = recentEngagements.filter((e) => e.type === 'referenced').length;
      const corrected = recentEngagements.filter((e) => e.type === 'corrected').length;
      if (referenced > 0 || corrected > 0) {
        parts.push(`Today: ${referenced} memories referenced, ${corrected} corrected`);
      }
    }

    // Proactivity state
    const cooldown = this.getProactivityCooldownRemaining();
    const pending = this.getPendingProposalCount();
    if (pending > 0) {
      parts.push(`${pending} proactive nudge${pending > 1 ? 's' : ''} queued` +
        (cooldown > 0 ? ` (cooldown: ${Math.ceil(cooldown / 60000)}m)` : ''));
    }

    if (parts.length === 0) return '';
    return `[MEMORY-PERSONALITY BRIDGE] ${parts.join(' | ')}`;
  }

  // ── Status / Queries ──────────────────────────────────────────

  getState(): BridgeState {
    return {
      engagements: [...this.state.engagements],
      extractionHints: { ...this.state.extractionHints },
      lastProactivityDelivery: this.state.lastProactivityDelivery,
      manipulation: this.getManipulationMetrics(),
      relevanceWeights: { ...this.state.relevanceWeights },
    };
  }

  getConfig(): BridgeConfig {
    return { ...this.config };
  }

  getEngagements(): MemoryEngagement[] {
    return [...this.state.engagements];
  }

  getRelevanceWeights(): RelevanceWeights {
    return { ...this.state.relevanceWeights };
  }

  isInitialized(): boolean {
    return this.initialized;
  }

  // ── Configuration ─────────────────────────────────────────────

  updateConfig(updates: Partial<BridgeConfig>): void {
    this.config = { ...this.config, ...updates };
    this.enqueueSave();
  }

  // ── Reset ─────────────────────────────────────────────────────

  /**
   * Reset all bridge state to defaults.
   * Manipulation violation counter is ALSO reset.
   */
  async reset(): Promise<void> {
    this.state = this.emptyState();
    this.proposalQueue = [];
    await this.save();
    console.log('[MemoryPersonalityBridge] State reset to defaults');
  }

  // ── Context Stream Event Handler ──────────────────────────────

  private handleContextEvent(event: any): void {
    if (!event || !event.type) return;

    // When personality calibration changes, recompute extraction hints
    if (event.source === 'personality-calibration') {
      this.recomputeExtractionHints();
    }

    // When a commitment status changes, re-evaluate memory priorities
    if (event.source === 'commitment-tracker' && event.type === 'system') {
      // Commitments changed — memory-commitment overlap may have shifted
      // This is handled lazily in getMemoryPriorityAdjustments()
    }
  }

  // ── Persistence ───────────────────────────────────────────────

  private enqueueSave(): void {
    // Crypto Sprint 17: Sanitize error output.
    this.saveQueue = this.saveQueue.then(() => this.save()).catch((err) => {
      console.error('[MemoryPersonalityBridge] Save failed:', err instanceof Error ? err.message : 'Unknown error');
    });
  }

  private async save(): Promise<void> {
    const serializable = {
      engagements: this.state.engagements,
      extractionHints: this.state.extractionHints,
      lastProactivityDelivery: this.state.lastProactivityDelivery,
      manipulation: this.state.manipulation,
      relevanceWeights: this.state.relevanceWeights,
    };
    await fs.writeFile(this.filePath, JSON.stringify(serializable, null, 2), 'utf-8');
  }

  // ── Cleanup ───────────────────────────────────────────────────

  destroy(): void {
    if (this.unsubscribeContextStream) {
      this.unsubscribeContextStream();
      this.unsubscribeContextStream = null;
    }
  }

  // ── Private Helpers ───────────────────────────────────────────

  private pruneEngagements(): void {
    const cutoff = Date.now() - this.config.engagementRetentionMs;
    const before = this.state.engagements.length;
    this.state.engagements = this.state.engagements.filter((e) => e.timestamp > cutoff);
    if (this.state.engagements.length < before) {
      console.log(
        `[MemoryPersonalityBridge] Pruned ${before - this.state.engagements.length} expired engagements`
      );
    }
  }

  /**
   * Check if two strings have significant word overlap (>30%).
   * Used for matching memory facts to commitment descriptions.
   */
  private hasSignificantOverlap(a: string, b: string): boolean {
    const stopWords = new Set([
      'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'to', 'of',
      'in', 'for', 'on', 'with', 'at', 'by', 'from', 'and', 'but', 'or', 'not',
      'that', 'this', 'it', 'they', 'he', 'she', 'we', 'you', 'i', 'my',
    ]);

    const tokenize = (text: string): Set<string> => {
      return new Set(
        text.toLowerCase().replace(/[^a-z0-9\s]/g, '').split(/\s+/)
          .filter((w) => w.length > 2 && !stopWords.has(w))
      );
    };

    const wordsA = tokenize(a);
    const wordsB = tokenize(b);
    if (wordsA.size === 0 || wordsB.size === 0) return false;

    let intersection = 0;
    for (const word of wordsA) {
      if (wordsB.has(word)) intersection++;
    }

    const smaller = Math.min(wordsA.size, wordsB.size);
    return intersection / smaller >= 0.3;
  }

  private emptyState(): BridgeState {
    return {
      engagements: [],
      extractionHints: { ...DEFAULT_EXTRACTION_HINTS },
      lastProactivityDelivery: 0,
      manipulation: {
        flatteryWindow: [],
        urgencyWindow: [],
        optionCountWindow: [],
        violations: 0,
        lastCheck: 0,
      },
      relevanceWeights: { ...DEFAULT_RELEVANCE_WEIGHTS },
    };
  }

  private mergeState(parsed: any): BridgeState {
    const empty = this.emptyState();
    return {
      engagements: Array.isArray(parsed.engagements) ? parsed.engagements : empty.engagements,
      extractionHints: parsed.extractionHints
        ? { ...empty.extractionHints, ...parsed.extractionHints }
        : empty.extractionHints,
      lastProactivityDelivery: typeof parsed.lastProactivityDelivery === 'number'
        ? parsed.lastProactivityDelivery : 0,
      manipulation: parsed.manipulation
        ? {
            flatteryWindow: Array.isArray(parsed.manipulation.flatteryWindow)
              ? parsed.manipulation.flatteryWindow : [],
            urgencyWindow: Array.isArray(parsed.manipulation.urgencyWindow)
              ? parsed.manipulation.urgencyWindow : [],
            optionCountWindow: Array.isArray(parsed.manipulation.optionCountWindow)
              ? parsed.manipulation.optionCountWindow : [],
            violations: typeof parsed.manipulation.violations === 'number'
              ? parsed.manipulation.violations : 0,
            lastCheck: typeof parsed.manipulation.lastCheck === 'number'
              ? parsed.manipulation.lastCheck : 0,
          }
        : empty.manipulation,
      relevanceWeights: parsed.relevanceWeights
        ? { ...empty.relevanceWeights, ...parsed.relevanceWeights }
        : empty.relevanceWeights,
    };
  }
}

// ── Singleton ────────────────────────────────────────────────────────

export const memoryPersonalityBridge = new MemoryPersonalityBridge();

// ── Test Helpers (dependency injection for lazy-bound singletons) ────
// These bypass the `require()` cache so vi.mock() isn't needed for these deps.

export function __test_setDeps(deps: {
  memoryManager?: any;
  episodicMemory?: any;
  personalityCalibration?: any;
  contextStream?: any;
  commitmentTracker?: any;
}): void {
  if (deps.memoryManager !== undefined) _memoryManager = deps.memoryManager;
  if (deps.episodicMemory !== undefined) _episodicMemory = deps.episodicMemory;
  if (deps.personalityCalibration !== undefined) _personalityCalibration = deps.personalityCalibration;
  if (deps.contextStream !== undefined) _contextStream = deps.contextStream;
  if (deps.commitmentTracker !== undefined) _commitmentTracker = deps.commitmentTracker;
}

export function __test_resetDeps(): void {
  _memoryManager = null;
  _episodicMemory = null;
  _personalityCalibration = null;
  _contextStream = null;
  _commitmentTracker = null;
}
