/**
 * git-monitor.ts — Post-ingestion monitoring for installed superpowers.
 *
 * Track I, Phase 4: The Immune System — Post-Ingestion Monitoring.
 *
 * Monitors installed repos after initial scanning:
 *   1. Behavioral fingerprinting — baseline what a repo does at install time
 *   2. Drift detection — compare current behavior against baseline
 *   3. Audit logging — tamper-proof record of all superpower activity
 *   4. Periodic re-scanning — detect dependency/CVE changes over time
 *
 * Pattern follows memory-watchdog.ts: establish baseline → compare → alert on drift.
 *
 * cLaw Safety Boundary:
 *   - Audit logs stored OUTSIDE superpower sandbox (tamper-proof)
 *   - Monitoring process cannot be disabled by superpowers
 *   - Logs are append-only (write + read, no delete or overwrite)
 *   - CPU overhead target: <1% during normal operation
 */

import type { BehavioralProfile, BehavioralSummary } from './git-sandbox';

// ── Types ────────────────────────────────────────────────────────────

export type AuditActionType =
  | 'fs-read'
  | 'fs-write'
  | 'fs-delete'
  | 'net-request'
  | 'net-connect'
  | 'process-spawn'
  | 'env-access'
  | 'module-load'
  | 'ipc-call'
  | 'error'
  | 'lifecycle';

export type DriftSeverity = 'none' | 'minor' | 'notable' | 'major' | 'critical';

export interface AuditEntry {
  id: string;
  repoId: string;
  timestamp: number;
  action: AuditActionType;
  detail: string;
  metadata?: Record<string, unknown>;
  /** Was this action blocked? */
  blocked: boolean;
}

export interface BehavioralFingerprint {
  /** Repo this fingerprint belongs to */
  repoId: string;
  /** When the fingerprint was created */
  createdAt: number;
  /** Last time the fingerprint was updated */
  updatedAt: number;

  /** Baseline behavioral summary from initial sandbox */
  baseline: BehavioralBaselineStats;
  /** Number of re-scans performed */
  rescanCount: number;
  /** Last re-scan timestamp */
  lastRescanAt: number | null;
}

export interface BehavioralBaselineStats {
  /** File paths read during baseline */
  filesAccessed: string[];
  /** File paths written during baseline */
  filesWritten: string[];
  /** Network targets seen during baseline */
  networkTargets: string[];
  /** HTTP URLs requested */
  httpRequests: string[];
  /** Processes spawned */
  processesSpawned: string[];
  /** Env vars accessed */
  envVarsAccessed: string[];
  /** External modules loaded */
  externalModules: string[];
  /** OS queries made */
  osQueries: number;
}

export interface DriftReport {
  repoId: string;
  timestamp: number;
  /** Overall drift severity */
  severity: DriftSeverity;
  /** Did drift exceed the auto-suspend threshold? */
  shouldSuspend: boolean;
  /** Individual drift observations */
  drifts: DriftObservation[];
  /** Human-readable summary */
  summary: string;
}

export interface DriftObservation {
  category: string;
  description: string;
  severity: DriftSeverity;
  /** What was in baseline */
  baseline: string[];
  /** What is new */
  current: string[];
  /** What appeared that wasn't in baseline */
  newItems: string[];
}

export interface MonitoringState {
  /** All installed repo fingerprints */
  fingerprints: Map<string, BehavioralFingerprint>;
  /** Audit log entries (in-memory buffer, flushed to disk periodically) */
  auditBuffer: AuditEntry[];
  /** Pending re-scan queue */
  rescanQueue: string[];
}

export interface MonitorConfig {
  /** Max audit entries to buffer before flush (default: 100) */
  auditBufferSize: number;
  /** Max audit entries per repo to keep on disk (default: 10000) */
  maxAuditEntriesPerRepo: number;
  /** Re-scan interval in days (default: 7) */
  rescanIntervalDays: number;
  /** Drift severity that triggers auto-suspension (default: 'critical') */
  autoSuspendThreshold: DriftSeverity;
  /** Max new network targets before flagging (default: 3) */
  networkDriftThreshold: number;
  /** Max new process spawns before flagging (default: 1) */
  processDriftThreshold: number;
}

// ── Constants ────────────────────────────────────────────────────────

export const DEFAULT_MONITOR_CONFIG: MonitorConfig = {
  auditBufferSize: 100,
  maxAuditEntriesPerRepo: 10_000,
  rescanIntervalDays: 7,
  autoSuspendThreshold: 'critical',
  networkDriftThreshold: 3,
  processDriftThreshold: 1,
};

const DRIFT_SEVERITY_ORDER: DriftSeverity[] = ['none', 'minor', 'notable', 'major', 'critical'];

// ── Behavioral Fingerprinting ────────────────────────────────────────

/**
 * Create a behavioral fingerprint from initial sandbox results.
 * This is the baseline against which future behavior is compared.
 */
export function createFingerprint(
  repoId: string,
  behavioralProfile: BehavioralProfile
): BehavioralFingerprint {
  return {
    repoId,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    baseline: {
      filesAccessed: [...behavioralProfile.summary.filesAccessed].slice(0, 500),
      filesWritten: [...behavioralProfile.summary.filesWritten].slice(0, 200),
      networkTargets: [...behavioralProfile.summary.networkTargets].slice(0, 100),
      httpRequests: [...behavioralProfile.summary.httpRequests].slice(0, 100),
      processesSpawned: [...behavioralProfile.summary.processesSpawned].slice(0, 50),
      envVarsAccessed: [...behavioralProfile.summary.envVarsAccessed].slice(0, 100),
      externalModules: [...behavioralProfile.summary.externalModules].slice(0, 200),
      osQueries: behavioralProfile.summary.osQueries,
    },
    rescanCount: 0,
    lastRescanAt: null,
  };
}

/**
 * Update an existing fingerprint after a re-scan.
 * Merges new observations into baseline (expanding what's "known").
 */
export function updateFingerprint(
  fingerprint: BehavioralFingerprint,
  newProfile: BehavioralProfile
): BehavioralFingerprint {
  const merged = { ...fingerprint };
  merged.updatedAt = Date.now();
  merged.rescanCount += 1;
  merged.lastRescanAt = Date.now();

  // Merge: union of baseline and new observations
  merged.baseline.filesAccessed = dedup([...merged.baseline.filesAccessed, ...newProfile.summary.filesAccessed]).slice(0, 500);
  merged.baseline.filesWritten = dedup([...merged.baseline.filesWritten, ...newProfile.summary.filesWritten]).slice(0, 200);
  merged.baseline.networkTargets = dedup([...merged.baseline.networkTargets, ...newProfile.summary.networkTargets]).slice(0, 100);
  merged.baseline.httpRequests = dedup([...merged.baseline.httpRequests, ...newProfile.summary.httpRequests]).slice(0, 100);
  merged.baseline.processesSpawned = dedup([...merged.baseline.processesSpawned, ...newProfile.summary.processesSpawned]).slice(0, 50);
  merged.baseline.envVarsAccessed = dedup([...merged.baseline.envVarsAccessed, ...newProfile.summary.envVarsAccessed]).slice(0, 100);
  merged.baseline.externalModules = dedup([...merged.baseline.externalModules, ...newProfile.summary.externalModules]).slice(0, 200);
  merged.baseline.osQueries = Math.max(merged.baseline.osQueries, newProfile.summary.osQueries);

  return merged;
}

// ── Drift Detection ──────────────────────────────────────────────────

/**
 * Compare current behavioral observations against the baseline fingerprint.
 * Detects new behaviors not seen during installation.
 */
export function detectDrift(
  fingerprint: BehavioralFingerprint,
  currentBehavior: BehavioralSummary,
  config: MonitorConfig = DEFAULT_MONITOR_CONFIG
): DriftReport {
  const drifts: DriftObservation[] = [];

  // Network drift — most security-sensitive
  const newNetTargets = currentBehavior.networkTargets.filter(
    t => !fingerprint.baseline.networkTargets.includes(t)
  );
  if (newNetTargets.length > 0) {
    drifts.push({
      category: 'network',
      description: `${newNetTargets.length} new network target(s) not seen during installation`,
      severity: newNetTargets.length >= config.networkDriftThreshold ? 'major' : 'notable',
      baseline: fingerprint.baseline.networkTargets,
      current: currentBehavior.networkTargets,
      newItems: newNetTargets,
    });
  }

  // HTTP request drift
  const newHttpReqs = currentBehavior.httpRequests.filter(
    r => !fingerprint.baseline.httpRequests.includes(r)
  );
  if (newHttpReqs.length > 0) {
    drifts.push({
      category: 'http',
      description: `${newHttpReqs.length} new HTTP endpoint(s) accessed`,
      severity: newHttpReqs.length >= config.networkDriftThreshold ? 'major' : 'notable',
      baseline: fingerprint.baseline.httpRequests,
      current: currentBehavior.httpRequests,
      newItems: newHttpReqs,
    });
  }

  // Process spawn drift — very concerning
  const newProcs = currentBehavior.processesSpawned.filter(
    p => !fingerprint.baseline.processesSpawned.includes(p)
  );
  if (newProcs.length > 0) {
    drifts.push({
      category: 'process',
      description: `${newProcs.length} new process(es) spawned that weren't seen during installation`,
      severity: newProcs.length >= config.processDriftThreshold ? 'critical' : 'major',
      baseline: fingerprint.baseline.processesSpawned,
      current: currentBehavior.processesSpawned,
      newItems: newProcs,
    });
  }

  // File write drift
  const newWrites = currentBehavior.filesWritten.filter(
    f => !fingerprint.baseline.filesWritten.includes(f)
  );
  if (newWrites.length > 0) {
    drifts.push({
      category: 'file-write',
      description: `${newWrites.length} new file path(s) being written to`,
      severity: newWrites.length > 5 ? 'notable' : 'minor',
      baseline: fingerprint.baseline.filesWritten,
      current: currentBehavior.filesWritten,
      newItems: newWrites,
    });
  }

  // Env var access drift
  const newEnvVars = currentBehavior.envVarsAccessed.filter(
    e => !fingerprint.baseline.envVarsAccessed.includes(e)
  );
  if (newEnvVars.length > 0) {
    const sensitiveEnvs = newEnvVars.filter(e =>
      /key|secret|token|password|credential|auth/i.test(e)
    );
    drifts.push({
      category: 'env-access',
      description: `${newEnvVars.length} new environment variable(s) accessed${sensitiveEnvs.length > 0 ? ` (${sensitiveEnvs.length} sensitive)` : ''}`,
      severity: sensitiveEnvs.length > 0 ? 'major' : 'minor',
      baseline: fingerprint.baseline.envVarsAccessed,
      current: currentBehavior.envVarsAccessed,
      newItems: newEnvVars,
    });
  }

  // External module drift
  const newModules = currentBehavior.externalModules.filter(
    m => !fingerprint.baseline.externalModules.includes(m)
  );
  if (newModules.length > 0) {
    drifts.push({
      category: 'module-load',
      description: `${newModules.length} new external module(s) loaded`,
      severity: newModules.length > 5 ? 'notable' : 'minor',
      baseline: fingerprint.baseline.externalModules,
      current: currentBehavior.externalModules,
      newItems: newModules,
    });
  }

  // Evasion drift — always critical
  if (currentBehavior.evasionAttempted) {
    drifts.push({
      category: 'evasion',
      description: 'Superpower is attempting sandbox evasion detection',
      severity: 'critical',
      baseline: [],
      current: ['evasion-detected'],
      newItems: ['evasion-detected'],
    });
  }

  // Compute overall severity (highest among all drifts)
  const overallSeverity = drifts.length === 0
    ? 'none' as DriftSeverity
    : drifts.reduce(
        (max, d) => compareSeverity(d.severity, max) > 0 ? d.severity : max,
        'none' as DriftSeverity
      );

  // Should suspend?
  const shouldSuspend = compareSeverity(overallSeverity, config.autoSuspendThreshold) >= 0;

  // Generate summary
  const summary = generateDriftSummary(drifts, overallSeverity, shouldSuspend);

  return {
    repoId: fingerprint.repoId,
    timestamp: Date.now(),
    severity: overallSeverity,
    shouldSuspend,
    drifts,
    summary,
  };
}

// ── Audit Logging ────────────────────────────────────────────────────

/**
 * Create a new audit entry. Returns the entry (caller persists it).
 * Entries are immutable once created — append-only pattern.
 */
export function createAuditEntry(
  repoId: string,
  action: AuditActionType,
  detail: string,
  blocked = false,
  metadata?: Record<string, unknown>
): AuditEntry {
  return {
    id: `audit-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    repoId,
    timestamp: Date.now(),
    action,
    detail: detail.slice(0, 500),  // Cap detail length
    metadata: metadata ? sanitizeMetadata(metadata) : undefined,
    blocked,
  };
}

/**
 * Query audit entries by time range and optional filters.
 */
export function queryAuditLog(
  entries: AuditEntry[],
  filters: {
    repoId?: string;
    startTime?: number;
    endTime?: number;
    action?: AuditActionType;
    blockedOnly?: boolean;
  }
): AuditEntry[] {
  let result = entries;

  if (filters.repoId) {
    result = result.filter(e => e.repoId === filters.repoId);
  }
  if (filters.startTime !== undefined) {
    result = result.filter(e => e.timestamp >= filters.startTime!);
  }
  if (filters.endTime !== undefined) {
    result = result.filter(e => e.timestamp <= filters.endTime!);
  }
  if (filters.action) {
    result = result.filter(e => e.action === filters.action);
  }
  if (filters.blockedOnly) {
    result = result.filter(e => e.blocked);
  }

  return result;
}

/**
 * Generate a human-readable audit summary for a time period.
 */
export function summarizeAuditLog(entries: AuditEntry[]): string {
  if (entries.length === 0) return 'No activity recorded in this period.';

  const actionCounts: Record<string, number> = {};
  let blockedCount = 0;

  for (const entry of entries) {
    actionCounts[entry.action] = (actionCounts[entry.action] || 0) + 1;
    if (entry.blocked) blockedCount++;
  }

  const parts: string[] = [];
  parts.push(`${entries.length} total actions recorded.`);

  const sorted = Object.entries(actionCounts).sort((a, b) => b[1] - a[1]);
  for (const [action, count] of sorted) {
    parts.push(`  ${action}: ${count}`);
  }

  if (blockedCount > 0) {
    parts.push(`${blockedCount} action(s) were blocked.`);
  }

  return parts.join('\n');
}

// ── Re-scan Scheduling ───────────────────────────────────────────────

/**
 * Check which repos need re-scanning based on their last scan time.
 */
export function getReposNeedingRescan(
  fingerprints: BehavioralFingerprint[],
  config: MonitorConfig = DEFAULT_MONITOR_CONFIG
): string[] {
  const now = Date.now();
  const intervalMs = config.rescanIntervalDays * 24 * 60 * 60 * 1000;

  return fingerprints
    .filter(fp => {
      const lastScan = fp.lastRescanAt || fp.createdAt;
      return (now - lastScan) >= intervalMs;
    })
    .map(fp => fp.repoId);
}

// ── Serialization ────────────────────────────────────────────────────

/**
 * Serialize a fingerprint for JSON storage.
 */
export function serializeFingerprint(fp: BehavioralFingerprint): string {
  return JSON.stringify(fp);
}

/**
 * Deserialize a fingerprint from JSON storage.
 * Returns null for invalid data.
 */
export function deserializeFingerprint(json: string): BehavioralFingerprint | null {
  try {
    const parsed = JSON.parse(json);
    if (!parsed || typeof parsed !== 'object') return null;
    if (!parsed.repoId || !parsed.baseline) return null;
    return parsed as BehavioralFingerprint;
  } catch {
    return null;
  }
}

/**
 * Serialize audit entries for JSON storage (JSONL format — one per line).
 */
export function serializeAuditEntries(entries: AuditEntry[]): string {
  return entries.map(e => JSON.stringify(e)).join('\n');
}

/**
 * Deserialize audit entries from JSONL storage.
 */
export function deserializeAuditEntries(jsonl: string): AuditEntry[] {
  if (!jsonl.trim()) return [];
  return jsonl
    .split('\n')
    .filter(line => line.trim())
    .map(line => {
      try {
        return JSON.parse(line) as AuditEntry;
      } catch {
        return null;
      }
    })
    .filter((e): e is AuditEntry => e !== null);
}

// ── Helpers ──────────────────────────────────────────────────────────

function dedup(arr: string[]): string[] {
  return [...new Set(arr)];
}

function compareSeverity(a: DriftSeverity, b: DriftSeverity): number {
  return DRIFT_SEVERITY_ORDER.indexOf(a) - DRIFT_SEVERITY_ORDER.indexOf(b);
}

export function driftSeverityToNumber(severity: DriftSeverity): number {
  return DRIFT_SEVERITY_ORDER.indexOf(severity);
}

function generateDriftSummary(
  drifts: DriftObservation[],
  overallSeverity: DriftSeverity,
  shouldSuspend: boolean
): string {
  if (drifts.length === 0) {
    return 'No behavioral drift detected. Superpower is operating within established baseline.';
  }

  const parts: string[] = [];

  if (shouldSuspend) {
    parts.push(`⚠️ CRITICAL: Behavioral drift detected — automatic suspension recommended.`);
  } else {
    parts.push(`Behavioral drift detected (${overallSeverity} severity).`);
  }

  for (const drift of drifts) {
    parts.push(`- [${drift.severity}] ${drift.description}`);
    if (drift.newItems.length <= 3) {
      parts.push(`  New: ${drift.newItems.join(', ')}`);
    } else {
      parts.push(`  New: ${drift.newItems.slice(0, 3).join(', ')} (+${drift.newItems.length - 3} more)`);
    }
  }

  return parts.join('\n');
}

/**
 * Sanitize metadata to prevent log injection or excessive size.
 */
function sanitizeMetadata(metadata: Record<string, unknown>): Record<string, unknown> {
  const sanitized: Record<string, unknown> = {};
  const keys = Object.keys(metadata).slice(0, 20); // Max 20 keys

  for (const key of keys) {
    const val = metadata[key];
    if (typeof val === 'string') {
      sanitized[key] = val.slice(0, 200); // Cap string values
    } else if (typeof val === 'number' || typeof val === 'boolean') {
      sanitized[key] = val;
    }
    // Drop complex types (objects, arrays, functions)
  }

  return sanitized;
}
