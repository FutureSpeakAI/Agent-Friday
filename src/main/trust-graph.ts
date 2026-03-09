/**
 * trust-graph.ts — Trust Graph Engine for Agent Friday.
 *
 * Models every person in the user's world with multi-dimensional trust scoring,
 * evidence tracking, person resolution (fuzzy alias matching), and hermeneutic
 * re-evaluation — each new observation re-evaluates the whole picture.
 *
 * Orthogonal to gateway/trust-engine.ts (access control tiers).
 * This is CREDIBILITY SCORING — how much should the agent weight a person's
 * input across different domains.
 *
 * Flow: Conversations → memory extraction picks up person mentions →
 * Trust Graph resolves to PersonNode → evidence accumulated → trust recomputed →
 * context injected into system prompts, meeting prep, communication drafts.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';
import crypto from 'crypto';
import type { TrustGraphConfig } from './settings';

// Late-bound vault import — avoids circular deps and allows test mocking
interface VaultIO { vaultRead: (p: string) => Promise<string>; vaultWrite: (p: string, c: string) => Promise<void> }
let _vault: VaultIO | null = null;
function getVault(): VaultIO {
  if (!_vault) {
    try {
      _vault = require('./vault');
    } catch {
      // Vault not available — use raw fs fallback
      _vault = {
        vaultRead: async (p: string) => (await fs.readFile(p, 'utf-8')),
        vaultWrite: async (p: string, c: string) => { await fs.writeFile(p, c, 'utf-8'); },
      };
    }
  }
  return _vault!;
}

/* ═══════════════════════════════════════════════════════════════════════════
   DATA MODEL
   ═══════════════════════════════════════════════════════════════════════════ */

export interface PersonNode {
  id: string;
  primaryName: string;
  aliases: PersonAlias[];
  trust: TrustScores;
  evidence: TrustEvidence[];
  communicationLog: CommEvent[];
  sentiment: SentimentPoint[];
  domains: string[];
  relationships: { personId: string; label: string }[];
  notes: string;
  firstSeen: number;
  lastSeen: number;
  interactionCount: number;
}

export interface PersonAlias {
  value: string;
  type: 'name' | 'email' | 'handle' | 'phone' | 'nickname';
  confidence: number;
}

export interface TrustScores {
  overall: number;
  reliability: number;
  expertise: DomainScore[];
  emotionalTrust: number;
  timeliness: number;
  informationQuality: number;
  /**
   * Runtime-extensible trust dimensions (Track X foundation).
   * Track X will add dimensions like 'commercialReliability', 'escrowHonor', etc.
   * Stored as string→number map so new dimensions can be added without schema migration.
   */
  extended?: Record<string, number>;
}

export interface DomainScore {
  domain: string;
  score: number;
  basis: string;
}

export type EvidenceType =
  | 'promise_kept'
  | 'promise_broken'
  | 'accurate_info'
  | 'inaccurate_info'
  | 'helpful_action'
  | 'unhelpful_action'
  | 'emotional_support'
  | 'user_stated'
  | 'observed'
  | 'inferred';

export interface TrustEvidence {
  id: string;
  timestamp: number;
  type: EvidenceType;
  description: string;
  impact: number; // -1 to +1
  domain?: string;
}

export interface CommEvent {
  timestamp: number;
  channel: string;
  direction: 'inbound' | 'outbound' | 'bidirectional';
  summary: string;
  sentiment: number;
}

export interface SentimentPoint {
  timestamp: number;
  score: number;
  context: string;
}

export interface PersonMention {
  name: string;
  context: string;
  sentiment: number;
  domains?: string[];
  evidenceType?: EvidenceType;
}

export interface ResolutionResult {
  person: PersonNode | null;
  confidence: number;
  isNew: boolean;
}

/* ═══════════════════════════════════════════════════════════════════════════
   CONSTANTS
   ═══════════════════════════════════════════════════════════════════════════ */

const DEFAULT_CONFIG: TrustGraphConfig = {
  maxPersons: 200,
  evidenceRetention: 90,
  decayRate: 0.001,
  reEvalThreshold: 5,
};

const MAX_EVIDENCE_PER_PERSON = 50;
const MAX_COMM_LOG_PER_PERSON = 30;
const MAX_SENTIMENT_PER_PERSON = 20;
const TRUST_FLOOR = 0.3; // Don't punish absence too harshly
const HALF_LIFE_DAYS = 30;
const HALF_LIFE_MS = HALF_LIFE_DAYS * 24 * 60 * 60 * 1000;

/* ═══════════════════════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════════════════════ */

/** Normalize a name for fuzzy comparison: lowercase, trim, collapse whitespace */
function normalizeName(name: string): string {
  return name.toLowerCase().trim().replace(/\s+/g, ' ');
}

/** Levenshtein distance between two strings */
function levenshtein(a: string, b: string): number {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));

  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1]
        : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
    }
  }
  return dp[m][n];
}

/** Clamp a number between min and max */
function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/** Create a fresh PersonNode with sensible defaults */
function createPersonNode(primaryName: string, aliasType: PersonAlias['type'] = 'name'): PersonNode {
  return {
    id: crypto.randomUUID().slice(0, 12),
    primaryName,
    aliases: [{ value: primaryName, type: aliasType, confidence: 1.0 }],
    trust: {
      overall: 0.5,
      reliability: 0.5,
      expertise: [],
      emotionalTrust: 0.5,
      timeliness: 0.5,
      informationQuality: 0.5,
    },
    evidence: [],
    communicationLog: [],
    sentiment: [],
    domains: [],
    relationships: [],
    notes: '',
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    interactionCount: 0,
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
   TRUST GRAPH ENGINE
   ═══════════════════════════════════════════════════════════════════════════ */

class TrustGraph {
  private persons: PersonNode[] = [];
  private filePath: string = '';
  private config: TrustGraphConfig = { ...DEFAULT_CONFIG };
  private savePromise: Promise<void> = Promise.resolve();
  private evidenceCountSinceReEval: Map<string, number> = new Map();

  /* ── Initialization ── */

  async initialize(): Promise<void> {
    this.filePath = path.join(app.getPath('userData'), 'trust-graph.json');

    try {
      // Vault-aware read: decrypts if vault is unlocked, falls back to plaintext
      const { vaultRead } = getVault();
      const data = await vaultRead(this.filePath);
      const saved = JSON.parse(data);
      this.persons = saved.persons || [];
      if (saved.config) {
        this.config = { ...DEFAULT_CONFIG, ...saved.config };
      }
    } catch {
      this.persons = [];
    }

    // Apply time-based decay on load
    this.applyDecay();
    // Prune old evidence
    this.pruneEvidence();

    console.log(`[TrustGraph] Initialized with ${this.persons.length} persons`);
  }

  /**
   * Re-read trust graph from disk after the vault is unlocked.
   *
   * During two-phase boot, the trust graph is first loaded in Phase A
   * (vault locked), which means encrypted files fall back to empty data.
   * This method is called in Phase B to re-read the now-decryptable graph.
   */
  async reloadFromVault(): Promise<void> {
    if (!this.filePath) return; // Not initialized yet

    try {
      const { vaultRead } = getVault();
      const data = await vaultRead(this.filePath);
      const saved = JSON.parse(data);
      this.persons = saved.persons || [];
      if (saved.config) {
        this.config = { ...DEFAULT_CONFIG, ...saved.config };
      }
      this.applyDecay();
      this.pruneEvidence();
      console.log(`[TrustGraph] Reloaded from vault (${this.persons.length} persons)`);
    } catch {
      // File doesn't exist or is still plaintext defaults — keep current data
      console.log('[TrustGraph] No encrypted trust graph to reload (using current values)');
    }
  }

  /* ── Person Resolution (Hermeneutic) ── */

  /**
   * Given an identifier (name, email, handle, etc.), resolve to an existing
   * PersonNode or create a new one.
   *
   * Resolution priority:
   * 1. Exact alias match (case-insensitive)
   * 2. Normalized name match (trimmed, lowered, collapsed whitespace)
   * 3. Levenshtein distance ≤ 2 for names (fuzzy)
   * 4. Create new node
   */
  resolvePerson(identifier: string, type?: PersonAlias['type']): ResolutionResult {
    if (!identifier || !identifier.trim()) {
      return { person: null, confidence: 0, isNew: false };
    }

    const normalized = normalizeName(identifier);
    const aliasType = type || this.inferAliasType(identifier);

    // 1. Exact alias match
    for (const person of this.persons) {
      for (const alias of person.aliases) {
        if (normalizeName(alias.value) === normalized) {
          return { person, confidence: 1.0, isNew: false };
        }
      }
    }

    // 2. Normalized primary name match
    for (const person of this.persons) {
      if (normalizeName(person.primaryName) === normalized) {
        return { person, confidence: 0.95, isNew: false };
      }
    }

    // 3. Fuzzy name match (Levenshtein ≤ 2, only for 'name' type identifiers)
    if (aliasType === 'name' || aliasType === 'nickname') {
      let bestMatch: PersonNode | null = null;
      let bestDistance = Infinity;

      for (const person of this.persons) {
        // Check against primary name
        const dist = levenshtein(normalized, normalizeName(person.primaryName));
        if (dist <= 2 && dist < bestDistance) {
          bestMatch = person;
          bestDistance = dist;
        }
        // Check against name-type aliases
        for (const alias of person.aliases) {
          if (alias.type === 'name' || alias.type === 'nickname') {
            const aDist = levenshtein(normalized, normalizeName(alias.value));
            if (aDist <= 2 && aDist < bestDistance) {
              bestMatch = person;
              bestDistance = aDist;
            }
          }
        }
      }

      if (bestMatch && bestDistance <= 2) {
        // Add this spelling as a new alias if it doesn't exist
        const existingAlias = bestMatch.aliases.find(
          (a) => normalizeName(a.value) === normalized
        );
        if (!existingAlias) {
          bestMatch.aliases.push({
            value: identifier.trim(),
            type: aliasType,
            confidence: bestDistance === 0 ? 1.0 : bestDistance === 1 ? 0.85 : 0.7,
          });
          this.scheduleSave();
        }
        return {
          person: bestMatch,
          confidence: bestDistance === 0 ? 0.95 : bestDistance === 1 ? 0.8 : 0.65,
          isNew: false,
        };
      }
    }

    // 4. Check if we have partial first-name matches (e.g. "John" matching "John Smith")
    if (aliasType === 'name' && normalized.split(' ').length === 1) {
      const firstName = normalized;
      const matches = this.persons.filter((p) => {
        const pFirst = normalizeName(p.primaryName).split(' ')[0];
        return pFirst === firstName;
      });
      if (matches.length === 1) {
        // Unique first-name match
        return { person: matches[0], confidence: 0.75, isNew: false };
      }
      // If multiple matches by first name, don't guess — create new
    }

    // 5. Create new PersonNode
    if (this.persons.length >= this.config.maxPersons) {
      // Evict least-recently-seen person with fewest interactions
      this.persons.sort((a, b) => {
        const scoreDiff = a.interactionCount - b.interactionCount;
        if (scoreDiff !== 0) return scoreDiff;
        return a.lastSeen - b.lastSeen;
      });
      this.persons.shift();
    }

    const newPerson = createPersonNode(identifier.trim(), aliasType);
    this.persons.push(newPerson);
    this.scheduleSave();

    return { person: newPerson, confidence: 1.0, isNew: true };
  }

  /**
   * Link an alias to an existing person (e.g. linking email to name).
   */
  addAlias(personId: string, alias: string, type: PersonAlias['type'], confidence: number = 0.9): boolean {
    const person = this.getPersonById(personId);
    if (!person) return false;

    const normalized = normalizeName(alias);
    const existing = person.aliases.find((a) => normalizeName(a.value) === normalized);
    if (existing) {
      existing.confidence = Math.max(existing.confidence, confidence);
      return true;
    }

    person.aliases.push({ value: alias.trim(), type, confidence });
    this.scheduleSave();
    return true;
  }

  /* ── Trust Scoring ── */

  /**
   * Add a piece of trust evidence to a person.
   * Triggers hermeneutic re-evaluation if threshold is reached.
   */
  addEvidence(
    personId: string,
    evidence: Omit<TrustEvidence, 'id' | 'timestamp'>
  ): void {
    const person = this.getPersonById(personId);
    if (!person) return;

    const fullEvidence: TrustEvidence = {
      ...evidence,
      id: crypto.randomUUID().slice(0, 8),
      timestamp: Date.now(),
      impact: clamp(evidence.impact, -1, 1),
    };

    person.evidence.push(fullEvidence);
    person.lastSeen = Date.now();
    person.interactionCount++;

    // Add domain if not already tracked
    if (evidence.domain && !person.domains.includes(evidence.domain)) {
      person.domains.push(evidence.domain);
    }

    // Cap evidence
    if (person.evidence.length > MAX_EVIDENCE_PER_PERSON) {
      // Keep the most impactful and most recent
      person.evidence.sort((a, b) => {
        const ageDiff = b.timestamp - a.timestamp;
        const impactDiff = Math.abs(b.impact) - Math.abs(a.impact);
        return impactDiff * 0.3 + ageDiff * 0.7; // Favor recency
      });
      person.evidence = person.evidence.slice(0, MAX_EVIDENCE_PER_PERSON);
    }

    // Track evidence count for re-evaluation threshold
    const count = (this.evidenceCountSinceReEval.get(personId) || 0) + 1;
    this.evidenceCountSinceReEval.set(personId, count);

    if (count >= this.config.reEvalThreshold) {
      // Hermeneutic circle: full re-evaluation
      this.recomputeTrust(personId);
      this.evidenceCountSinceReEval.set(personId, 0);
    } else {
      // Quick incremental update
      this.quickUpdateTrust(person, fullEvidence);
    }

    this.scheduleSave();
  }

  /**
   * HERMENEUTIC CIRCLE: Full re-evaluation of ALL trust dimensions from ALL evidence.
   * Not incremental — each new observation re-evaluates the whole picture.
   * Weighted by recency (exponential decay) and impact magnitude.
   */
  recomputeTrust(personId: string): void {
    const person = this.getPersonById(personId);
    if (!person || person.evidence.length === 0) return;

    const now = Date.now();

    // Weight each piece of evidence by recency and magnitude
    const weighted = person.evidence.map((e) => ({
      ...e,
      weight: Math.pow(0.5, (now - e.timestamp) / HALF_LIFE_MS) * Math.max(0.1, Math.abs(e.impact)),
    }));

    // ── Reliability: ratio of kept vs broken promises ──
    const promises = weighted.filter(
      (e) => e.type === 'promise_kept' || e.type === 'promise_broken'
    );
    if (promises.length > 0) {
      const kept = promises
        .filter((e) => e.type === 'promise_kept')
        .reduce((sum, e) => sum + e.weight, 0);
      const total = promises.reduce((sum, e) => sum + e.weight, 0);
      person.trust.reliability = clamp(kept / total, 0, 1);
    }

    // ── Information Quality: ratio of accurate vs inaccurate ──
    const info = weighted.filter(
      (e) => e.type === 'accurate_info' || e.type === 'inaccurate_info'
    );
    if (info.length > 0) {
      const accurate = info
        .filter((e) => e.type === 'accurate_info')
        .reduce((sum, e) => sum + e.weight, 0);
      const total = info.reduce((sum, e) => sum + e.weight, 0);
      person.trust.informationQuality = clamp(accurate / total, 0, 1);
    }

    // ── Emotional Trust: weighted from emotional evidence ──
    const emotional = weighted.filter((e) => e.type === 'emotional_support');
    if (emotional.length > 0) {
      const raw =
        emotional.reduce((sum, e) => sum + (e.impact > 0 ? e.weight : -e.weight), 0) /
        emotional.reduce((sum, e) => sum + e.weight, 0);
      person.trust.emotionalTrust = clamp((raw + 1) / 2, 0, 1);
    }

    // ── Timeliness: from helpful/unhelpful actions ──
    const actions = weighted.filter(
      (e) => e.type === 'helpful_action' || e.type === 'unhelpful_action'
    );
    if (actions.length > 0) {
      const helpful = actions
        .filter((e) => e.type === 'helpful_action')
        .reduce((sum, e) => sum + e.weight, 0);
      const total = actions.reduce((sum, e) => sum + e.weight, 0);
      person.trust.timeliness = clamp(helpful / total, 0, 1);
    }

    // ── Domain Expertise: per-domain scoring from domain-tagged evidence ──
    const domainMap = new Map<string, { positive: number; total: number; count: number }>();
    for (const e of weighted) {
      if (e.domain) {
        const d = domainMap.get(e.domain) || { positive: 0, total: 0, count: 0 };
        d.total += e.weight;
        d.count++;
        if (e.impact > 0) d.positive += e.weight;
        domainMap.set(e.domain, d);
      }
    }
    person.trust.expertise = Array.from(domainMap.entries()).map(([domain, { positive, total, count }]) => ({
      domain,
      score: clamp(positive / total, 0, 1),
      basis: `${count} observations`,
    }));

    // ── Overall Composite ──
    const expertiseAvg =
      person.trust.expertise.length > 0
        ? person.trust.expertise.reduce((s, e) => s + e.score, 0) / person.trust.expertise.length
        : 0.5;

    person.trust.overall = clamp(
      person.trust.reliability * 0.3 +
        person.trust.emotionalTrust * 0.2 +
        person.trust.timeliness * 0.15 +
        person.trust.informationQuality * 0.25 +
        expertiseAvg * 0.1,
      0,
      1
    );

    console.log(
      `[TrustGraph] Recomputed trust for ${person.primaryName}: overall=${person.trust.overall.toFixed(2)}`
    );
  }

  /**
   * Quick incremental trust update from a single piece of evidence.
   * Used between full hermeneutic re-evaluations.
   */
  private quickUpdateTrust(person: PersonNode, evidence: TrustEvidence): void {
    const blendFactor = 0.15; // New evidence blends 15% into current scores

    switch (evidence.type) {
      case 'promise_kept':
        person.trust.reliability = clamp(
          person.trust.reliability + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'promise_broken':
        person.trust.reliability = clamp(
          person.trust.reliability + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'accurate_info':
        person.trust.informationQuality = clamp(
          person.trust.informationQuality + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'inaccurate_info':
        person.trust.informationQuality = clamp(
          person.trust.informationQuality + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'emotional_support':
        person.trust.emotionalTrust = clamp(
          person.trust.emotionalTrust + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'helpful_action':
        person.trust.timeliness = clamp(
          person.trust.timeliness + blendFactor * evidence.impact,
          0, 1
        );
        break;
      case 'unhelpful_action':
        person.trust.timeliness = clamp(
          person.trust.timeliness + blendFactor * evidence.impact,
          0, 1
        );
        break;
      default:
        // user_stated, observed, inferred — update overall gently
        break;
    }

    // Recompute overall
    const expertiseAvg =
      person.trust.expertise.length > 0
        ? person.trust.expertise.reduce((s, e) => s + e.score, 0) / person.trust.expertise.length
        : 0.5;

    person.trust.overall = clamp(
      person.trust.reliability * 0.3 +
        person.trust.emotionalTrust * 0.2 +
        person.trust.timeliness * 0.15 +
        person.trust.informationQuality * 0.25 +
        expertiseAvg * 0.1,
      0,
      1
    );
  }

  /* ── Communication Tracking ── */

  logCommunication(personId: string, event: Omit<CommEvent, 'timestamp'>): void {
    const person = this.getPersonById(personId);
    if (!person) return;

    person.communicationLog.push({
      ...event,
      timestamp: Date.now(),
    });

    // Also add a sentiment point
    person.sentiment.push({
      timestamp: Date.now(),
      score: clamp(event.sentiment, -1, 1),
      context: event.summary,
    });

    // Cap logs
    if (person.communicationLog.length > MAX_COMM_LOG_PER_PERSON) {
      person.communicationLog = person.communicationLog.slice(-MAX_COMM_LOG_PER_PERSON);
    }
    if (person.sentiment.length > MAX_SENTIMENT_PER_PERSON) {
      person.sentiment = person.sentiment.slice(-MAX_SENTIMENT_PER_PERSON);
    }

    person.lastSeen = Date.now();
    person.interactionCount++;
    this.scheduleSave();
  }

  /* ── Batch Person Mention Processing ── */

  /**
   * Process person mentions extracted from conversation by the memory pipeline.
   * Resolves each mention to a PersonNode, adds evidence, updates sentiment.
   */
  async processPersonMentions(mentions: PersonMention[]): Promise<void> {
    for (const mention of mentions) {
      if (!mention.name || !mention.name.trim()) continue;

      const { person } = this.resolvePerson(mention.name, 'name');
      if (!person) continue;

      // Add evidence if there's a clear evidence type
      if (mention.evidenceType && mention.context) {
        this.addEvidence(person.id, {
          type: mention.evidenceType,
          description: mention.context,
          impact: mention.sentiment || 0,
          domain: mention.domains?.[0],
        });
      } else if (mention.context) {
        // Generic observation
        this.addEvidence(person.id, {
          type: 'observed',
          description: mention.context,
          impact: mention.sentiment || 0,
          domain: mention.domains?.[0],
        });
      }

      // Track domains
      if (mention.domains) {
        for (const domain of mention.domains) {
          if (!person.domains.includes(domain)) {
            person.domains.push(domain);
          }
        }
      }

      // Add sentiment point
      if (mention.sentiment !== 0) {
        person.sentiment.push({
          timestamp: Date.now(),
          score: clamp(mention.sentiment, -1, 1),
          context: mention.context,
        });
        if (person.sentiment.length > MAX_SENTIMENT_PER_PERSON) {
          person.sentiment = person.sentiment.slice(-MAX_SENTIMENT_PER_PERSON);
        }
      }
    }

    this.scheduleSave();
  }

  /* ── Context Generation ── */

  /**
   * Get full context string for a specific person.
   * Used in meeting prep, communication drafts, etc.
   */
  getContextForPerson(personId: string): string {
    const person = this.getPersonById(personId);
    if (!person) return '';

    const lines: string[] = [];
    lines.push(`### ${person.primaryName}`);

    // Trust summary
    const trustLabel = this.trustLabel(person.trust.overall);
    lines.push(`Trust: ${trustLabel} (${(person.trust.overall * 100).toFixed(0)}%)`);

    // Dimension breakdown (only non-default values)
    const dims: string[] = [];
    if (person.trust.reliability !== 0.5) dims.push(`reliability: ${(person.trust.reliability * 100).toFixed(0)}%`);
    if (person.trust.informationQuality !== 0.5) dims.push(`info quality: ${(person.trust.informationQuality * 100).toFixed(0)}%`);
    if (person.trust.emotionalTrust !== 0.5) dims.push(`emotional: ${(person.trust.emotionalTrust * 100).toFixed(0)}%`);
    if (person.trust.timeliness !== 0.5) dims.push(`timeliness: ${(person.trust.timeliness * 100).toFixed(0)}%`);
    if (dims.length > 0) lines.push(`  [${dims.join(', ')}]`);

    // Expertise
    if (person.trust.expertise.length > 0) {
      const expertises = person.trust.expertise
        .sort((a, b) => b.score - a.score)
        .slice(0, 5)
        .map((e) => `${e.domain} (${(e.score * 100).toFixed(0)}%)`)
        .join(', ');
      lines.push(`Expertise: ${expertises}`);
    }

    // Domains
    if (person.domains.length > 0 && person.trust.expertise.length === 0) {
      lines.push(`Known domains: ${person.domains.slice(0, 8).join(', ')}`);
    }

    // Recent evidence (last 3 most impactful)
    const recentEvidence = [...person.evidence]
      .sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact))
      .slice(0, 3);
    if (recentEvidence.length > 0) {
      lines.push('Key observations:');
      for (const e of recentEvidence) {
        const sign = e.impact > 0 ? '+' : e.impact < 0 ? '-' : '~';
        lines.push(`  ${sign} ${e.description}`);
      }
    }

    // Sentiment trend
    if (person.sentiment.length >= 3) {
      const recent = person.sentiment.slice(-5);
      const avgSentiment = recent.reduce((s, p) => s + p.score, 0) / recent.length;
      const trend = avgSentiment > 0.2 ? 'positive' : avgSentiment < -0.2 ? 'negative' : 'neutral';
      lines.push(`Sentiment trend: ${trend}`);
    }

    // Notes
    if (person.notes) {
      lines.push(`Notes: ${person.notes}`);
    }

    // Interaction stats
    const daysSinceFirst = Math.floor((Date.now() - person.firstSeen) / (1000 * 60 * 60 * 24));
    const daysSinceLast = Math.floor((Date.now() - person.lastSeen) / (1000 * 60 * 60 * 24));
    lines.push(`(${person.interactionCount} interactions over ${daysSinceFirst}d, last seen ${daysSinceLast}d ago)`);

    return lines.join('\n');
  }

  /**
   * Get context for multiple persons (e.g. meeting attendees).
   */
  getContextForPersons(personIds: string[]): string {
    return personIds
      .map((id) => this.getContextForPerson(id))
      .filter(Boolean)
      .join('\n\n');
  }

  /**
   * Get top-level summary for system prompt injection.
   * Limited to ~15 most relevant people to respect prompt budget.
   */
  getPromptContext(): string {
    if (this.persons.length === 0) return '';

    // Score persons by relevance: recency + trust variance from 0.5 + interaction count
    const now = Date.now();
    const scored = this.persons.map((p) => {
      const daysSinceSeen = (now - p.lastSeen) / (1000 * 60 * 60 * 24);
      const recencyScore = Math.exp(-daysSinceSeen / 14); // 2-week half-life
      const trustVariance = Math.abs(p.trust.overall - 0.5); // Higher variance = more interesting
      const interactionScore = Math.min(p.interactionCount / 20, 1); // Cap at 20 interactions
      return {
        person: p,
        relevance: recencyScore * 0.5 + trustVariance * 0.3 + interactionScore * 0.2,
      };
    });

    scored.sort((a, b) => b.relevance - a.relevance);
    const top = scored.slice(0, 15);

    if (top.length === 0) return '';

    const lines: string[] = [];
    lines.push('KEY PEOPLE:');

    for (const { person } of top) {
      const trustLabel = this.trustLabel(person.trust.overall);
      const domains = person.domains.slice(0, 3).join(', ');
      const domainsStr = domains ? ` | expertise: ${domains}` : '';

      // Include notable notes or recent evidence
      let note = '';
      if (person.notes) {
        note = ` | note: ${person.notes.slice(0, 80)}`;
      } else if (person.evidence.length > 0) {
        const latest = person.evidence[person.evidence.length - 1];
        if (latest.description.length <= 60) {
          note = ` | latest: ${latest.description}`;
        }
      }

      lines.push(`- ${person.primaryName} (trust: ${trustLabel}${domainsStr}${note})`);
    }

    return lines.join('\n');
  }

  /* ── Queries ── */

  findByDomain(domain: string): PersonNode[] {
    const normalized = domain.toLowerCase();
    return this.persons.filter(
      (p) =>
        p.domains.some((d) => d.toLowerCase().includes(normalized)) ||
        p.trust.expertise.some((e) => e.domain.toLowerCase().includes(normalized))
    );
  }

  getMostTrusted(limit: number = 10): PersonNode[] {
    return [...this.persons]
      .sort((a, b) => b.trust.overall - a.trust.overall)
      .slice(0, limit);
  }

  getRecentInteractions(limit: number = 10): PersonNode[] {
    return [...this.persons]
      .sort((a, b) => b.lastSeen - a.lastSeen)
      .slice(0, limit);
  }

  getAllPersons(): PersonNode[] {
    return [...this.persons].sort((a, b) => b.lastSeen - a.lastSeen);
  }

  getPersonById(id: string): PersonNode | null {
    return this.persons.find((p) => p.id === id) || null;
  }

  getPersonCount(): number {
    return this.persons.length;
  }

  /* ── Person Management ── */

  updateNotes(personId: string, notes: string): void {
    const person = this.getPersonById(personId);
    if (!person) return;
    person.notes = notes;
    this.scheduleSave();
  }

  linkPersons(personIdA: string, personIdB: string, label: string): void {
    const a = this.getPersonById(personIdA);
    const b = this.getPersonById(personIdB);
    if (!a || !b) return;

    if (!a.relationships.find((r) => r.personId === personIdB)) {
      a.relationships.push({ personId: personIdB, label });
    }
    if (!b.relationships.find((r) => r.personId === personIdA)) {
      b.relationships.push({ personId: personIdA, label });
    }
    this.scheduleSave();
  }

  /* ── Maintenance ── */

  /**
   * Apply time-based decay to trust scores for persons not seen recently.
   * Called on initialization.
   */
  private applyDecay(): void {
    const now = Date.now();
    const msPerDay = 24 * 60 * 60 * 1000;

    for (const person of this.persons) {
      const daysSinceSeen = (now - person.lastSeen) / msPerDay;
      if (daysSinceSeen < 1) continue; // No decay within 24 hours

      const decayFactor = 1 - this.config.decayRate * daysSinceSeen;

      // Decay each dimension toward the floor, not toward zero
      const decayDimension = (current: number): number => {
        if (current <= TRUST_FLOOR) return current;
        return Math.max(TRUST_FLOOR, current * decayFactor);
      };

      person.trust.reliability = decayDimension(person.trust.reliability);
      person.trust.emotionalTrust = decayDimension(person.trust.emotionalTrust);
      person.trust.timeliness = decayDimension(person.trust.timeliness);
      person.trust.informationQuality = decayDimension(person.trust.informationQuality);

      for (const exp of person.trust.expertise) {
        exp.score = decayDimension(exp.score);
      }

      // Recompute overall
      const expertiseAvg =
        person.trust.expertise.length > 0
          ? person.trust.expertise.reduce((s, e) => s + e.score, 0) / person.trust.expertise.length
          : 0.5;

      person.trust.overall = clamp(
        person.trust.reliability * 0.3 +
          person.trust.emotionalTrust * 0.2 +
          person.trust.timeliness * 0.15 +
          person.trust.informationQuality * 0.25 +
          expertiseAvg * 0.1,
        0,
        1
      );
    }
  }

  /**
   * Remove evidence older than retention period.
   * Keep at least 5 most impactful pieces regardless of age.
   */
  private pruneEvidence(): void {
    const cutoffMs = this.config.evidenceRetention * 24 * 60 * 60 * 1000;
    const cutoff = Date.now() - cutoffMs;

    for (const person of this.persons) {
      if (person.evidence.length <= 5) continue;

      // Sort by impact (descending) to identify keepers
      const byImpact = [...person.evidence].sort(
        (a, b) => Math.abs(b.impact) - Math.abs(a.impact)
      );
      const keepers = new Set(byImpact.slice(0, 5).map((e) => e.id));

      person.evidence = person.evidence.filter(
        (e) => keepers.has(e.id) || e.timestamp > cutoff
      );
    }
  }

  /* ── Persistence ── */

  private scheduleSave(): void {
    this.savePromise = this.savePromise
      .then(async () => {
        // Vault-aware write: encrypts if vault is unlocked, falls back to plaintext
        const { vaultWrite } = getVault();
        await vaultWrite(
          this.filePath,
          JSON.stringify({ persons: this.persons, config: this.config }, null, 2),
        );
      })
      .catch((err) => {
        // Crypto Sprint 17: Sanitize error output.
        console.error('[TrustGraph] Save failed:', err instanceof Error ? err.message : 'Unknown error');
      });
  }

  private async save(): Promise<void> {
    this.scheduleSave();
    return this.savePromise;
  }

  /* ── Helpers ── */

  private trustLabel(score: number): string {
    if (score >= 0.85) return 'very high';
    if (score >= 0.7) return 'high';
    if (score >= 0.55) return 'moderate';
    if (score >= 0.4) return 'developing';
    if (score >= 0.25) return 'low';
    return 'very low';
  }

  private inferAliasType(identifier: string): PersonAlias['type'] {
    if (identifier.includes('@') && identifier.includes('.')) return 'email';
    if (identifier.startsWith('@')) return 'handle';
    if (/^\+?[\d\s()-]{7,}$/.test(identifier)) return 'phone';
    return 'name';
  }
}

export const trustGraph = new TrustGraph();
