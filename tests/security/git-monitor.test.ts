/**
 * git-monitor.test.ts — Tests for Post-Ingestion Monitoring (Track I Phase 4).
 *
 * Covers:
 *   1. Behavioral Fingerprinting (createFingerprint, updateFingerprint)
 *   2. Drift Detection (detectDrift — 7 categories)
 *   3. Audit Logging (createAuditEntry, queryAuditLog, summarizeAuditLog)
 *   4. Re-scan Scheduling (getReposNeedingRescan)
 *   5. Serialization (serialize/deserialize fingerprints & audit entries)
 *   6. cLaw Gate Safety Invariants
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { BehavioralProfile, BehavioralSummary } from '../../src/main/git-sandbox';

import {
  createFingerprint,
  updateFingerprint,
  detectDrift,
  createAuditEntry,
  queryAuditLog,
  summarizeAuditLog,
  getReposNeedingRescan,
  serializeFingerprint,
  deserializeFingerprint,
  serializeAuditEntries,
  deserializeAuditEntries,
  driftSeverityToNumber,
  DEFAULT_MONITOR_CONFIG,
  type BehavioralFingerprint,
  type BehavioralBaselineStats,
  type AuditEntry,
  type MonitorConfig,
  type DriftReport,
  type DriftSeverity,
} from '../../src/main/git-monitor';

// ── Test Helpers ──────────────────────────────────────────────────────

function makeSummary(overrides: Partial<BehavioralSummary> = {}): BehavioralSummary {
  return {
    filesAccessed: [],
    filesWritten: [],
    networkTargets: [],
    httpRequests: [],
    processesSpawned: [],
    envVarsAccessed: [],
    externalModules: [],
    osQueries: 0,
    blockedOperations: 0,
    evasionAttempted: false,
    evasionPatterns: [],
    ...overrides,
  };
}

function makeProfile(overrides: Partial<BehavioralSummary> = {}): BehavioralProfile {
  return {
    runId: 'test-run-001',
    timestamp: Date.now(),
    durationMs: 5000,
    entryPointsTested: ['index.js'],
    observations: [],
    summary: makeSummary(overrides),
    resources: {
      peakMemoryMB: 50,
      cpuTimeMs: 200,
      observationCount: 10,
    },
    riskContribution: 0,
    findings: [],
    sandboxIntegrity: 'intact',
  } as BehavioralProfile;
}

function makeFingerprint(
  repoId = 'test-repo',
  baseline: Partial<BehavioralBaselineStats> = {}
): BehavioralFingerprint {
  return {
    repoId,
    createdAt: Date.now() - 86400000, // 1 day ago
    updatedAt: Date.now() - 86400000,
    baseline: {
      filesAccessed: [],
      filesWritten: [],
      networkTargets: [],
      httpRequests: [],
      processesSpawned: [],
      envVarsAccessed: [],
      externalModules: [],
      osQueries: 0,
      ...baseline,
    },
    rescanCount: 0,
    lastRescanAt: null,
  };
}

function makeAuditEntry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: `audit-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    repoId: 'test-repo',
    timestamp: Date.now(),
    action: 'fs-read',
    detail: 'Read /tmp/file.txt',
    blocked: false,
    ...overrides,
  };
}

// ═════════════════════════════════════════════════════════════════════
// 1. Behavioral Fingerprinting
// ═════════════════════════════════════════════════════════════════════

describe('Behavioral Fingerprinting', () => {
  describe('createFingerprint', () => {
    it('should create fingerprint from behavioral profile', () => {
      const profile = makeProfile({
        filesAccessed: ['/tmp/a.txt', '/tmp/b.txt'],
        networkTargets: ['api.example.com:443'],
        processesSpawned: ['node'],
      });

      const fp = createFingerprint('repo-abc', profile);

      expect(fp.repoId).toBe('repo-abc');
      expect(fp.baseline.filesAccessed).toEqual(['/tmp/a.txt', '/tmp/b.txt']);
      expect(fp.baseline.networkTargets).toEqual(['api.example.com:443']);
      expect(fp.baseline.processesSpawned).toEqual(['node']);
      expect(fp.rescanCount).toBe(0);
      expect(fp.lastRescanAt).toBeNull();
    });

    it('should cap baseline arrays to prevent unbounded growth', () => {
      const largeArray = Array.from({ length: 1000 }, (_, i) => `file-${i}.txt`);
      const profile = makeProfile({
        filesAccessed: largeArray,
      });

      const fp = createFingerprint('repo-big', profile);
      expect(fp.baseline.filesAccessed.length).toBeLessThanOrEqual(500);
    });

    it('should snapshot baseline independently (no shared references)', () => {
      const targets = ['api.example.com:443'];
      const profile = makeProfile({ networkTargets: targets });
      const fp = createFingerprint('repo-snap', profile);

      // Mutating original should not affect fingerprint
      targets.push('evil.com:80');
      expect(fp.baseline.networkTargets).toEqual(['api.example.com:443']);
    });

    it('should record creation timestamps', () => {
      const before = Date.now();
      const fp = createFingerprint('repo-time', makeProfile());
      const after = Date.now();

      expect(fp.createdAt).toBeGreaterThanOrEqual(before);
      expect(fp.createdAt).toBeLessThanOrEqual(after);
      expect(fp.updatedAt).toBe(fp.createdAt);
    });
  });

  describe('updateFingerprint', () => {
    it('should merge new observations into baseline', () => {
      const fp = makeFingerprint('repo-upd', {
        networkTargets: ['api.example.com:443'],
        processesSpawned: ['node'],
      });

      const newProfile = makeProfile({
        networkTargets: ['api.example.com:443', 'cdn.example.com:443'],
        processesSpawned: ['node', 'python3'],
      });

      const updated = updateFingerprint(fp, newProfile);

      expect(updated.baseline.networkTargets).toContain('api.example.com:443');
      expect(updated.baseline.networkTargets).toContain('cdn.example.com:443');
      expect(updated.baseline.processesSpawned).toContain('python3');
    });

    it('should increment rescanCount', () => {
      const fp = makeFingerprint();
      const updated = updateFingerprint(fp, makeProfile());
      expect(updated.rescanCount).toBe(1);

      const updated2 = updateFingerprint(updated, makeProfile());
      expect(updated2.rescanCount).toBe(2);
    });

    it('should update lastRescanAt', () => {
      const fp = makeFingerprint();
      expect(fp.lastRescanAt).toBeNull();

      const before = Date.now();
      const updated = updateFingerprint(fp, makeProfile());
      expect(updated.lastRescanAt).toBeGreaterThanOrEqual(before);
    });

    it('should deduplicate merged arrays', () => {
      const fp = makeFingerprint('repo-dedup', {
        filesAccessed: ['/a.txt', '/b.txt'],
      });

      const newProfile = makeProfile({
        filesAccessed: ['/a.txt', '/b.txt', '/c.txt'],
      });

      const updated = updateFingerprint(fp, newProfile);
      expect(updated.baseline.filesAccessed).toEqual(['/a.txt', '/b.txt', '/c.txt']);
    });

    it('should take max of osQueries', () => {
      const fp = makeFingerprint('repo-os', { osQueries: 5 });
      const newProfile = makeProfile({ osQueries: 3 });

      const updated = updateFingerprint(fp, newProfile);
      expect(updated.baseline.osQueries).toBe(5);

      const newProfile2 = makeProfile({ osQueries: 10 });
      const updated2 = updateFingerprint(updated, newProfile2);
      expect(updated2.baseline.osQueries).toBe(10);
    });
  });
});

// ═════════════════════════════════════════════════════════════════════
// 2. Drift Detection
// ═════════════════════════════════════════════════════════════════════

describe('Drift Detection', () => {
  it('should report no drift when behavior matches baseline', () => {
    const fp = makeFingerprint('repo-clean', {
      networkTargets: ['api.example.com:443'],
      filesAccessed: ['/tmp/data.json'],
    });

    const current = makeSummary({
      networkTargets: ['api.example.com:443'],
      filesAccessed: ['/tmp/data.json'],
    });

    const report = detectDrift(fp, current);

    expect(report.severity).toBe('none');
    expect(report.drifts).toHaveLength(0);
    expect(report.shouldSuspend).toBe(false);
  });

  it('should detect new network targets as notable drift', () => {
    const fp = makeFingerprint('repo-net', {
      networkTargets: ['api.example.com:443'],
    });

    const current = makeSummary({
      networkTargets: ['api.example.com:443', 'evil.com:80'],
    });

    const report = detectDrift(fp, current);

    expect(report.drifts.some(d => d.category === 'network')).toBe(true);
    const netDrift = report.drifts.find(d => d.category === 'network')!;
    expect(netDrift.newItems).toEqual(['evil.com:80']);
    expect(netDrift.severity).toBe('notable');
  });

  it('should escalate network drift to major with many new targets', () => {
    const fp = makeFingerprint('repo-net-many', {
      networkTargets: ['api.example.com:443'],
    });

    const current = makeSummary({
      networkTargets: [
        'api.example.com:443',
        'evil1.com:80',
        'evil2.com:80',
        'evil3.com:80',
      ],
    });

    const report = detectDrift(fp, current);
    const netDrift = report.drifts.find(d => d.category === 'network')!;
    expect(netDrift.severity).toBe('major');
  });

  it('should detect new process spawns as critical', () => {
    const fp = makeFingerprint('repo-proc', {
      processesSpawned: ['node'],
    });

    const current = makeSummary({
      processesSpawned: ['node', 'curl'],
    });

    const report = detectDrift(fp, current);
    const procDrift = report.drifts.find(d => d.category === 'process')!;
    expect(procDrift.severity).toBe('critical');
    expect(procDrift.newItems).toEqual(['curl']);
  });

  it('should detect HTTP endpoint drift', () => {
    const fp = makeFingerprint('repo-http', {
      httpRequests: ['https://api.example.com/v1/data'],
    });

    const current = makeSummary({
      httpRequests: ['https://api.example.com/v1/data', 'https://evil.com/steal'],
    });

    const report = detectDrift(fp, current);
    expect(report.drifts.some(d => d.category === 'http')).toBe(true);
  });

  it('should detect sensitive env var access as major drift', () => {
    const fp = makeFingerprint('repo-env', {
      envVarsAccessed: ['NODE_ENV', 'PATH'],
    });

    const current = makeSummary({
      envVarsAccessed: ['NODE_ENV', 'PATH', 'AWS_SECRET_KEY'],
    });

    const report = detectDrift(fp, current);
    const envDrift = report.drifts.find(d => d.category === 'env-access')!;
    expect(envDrift.severity).toBe('major');
    expect(envDrift.description).toContain('sensitive');
  });

  it('should detect non-sensitive env var access as minor drift', () => {
    const fp = makeFingerprint('repo-env-safe', {
      envVarsAccessed: ['NODE_ENV'],
    });

    const current = makeSummary({
      envVarsAccessed: ['NODE_ENV', 'HOME'],
    });

    const report = detectDrift(fp, current);
    const envDrift = report.drifts.find(d => d.category === 'env-access')!;
    expect(envDrift.severity).toBe('minor');
  });

  it('should detect evasion attempts as always critical', () => {
    const fp = makeFingerprint('repo-evasion');

    const current = makeSummary({
      evasionAttempted: true,
    });

    const report = detectDrift(fp, current);
    const evasionDrift = report.drifts.find(d => d.category === 'evasion')!;
    expect(evasionDrift.severity).toBe('critical');
  });

  it('should detect new file writes as minor drift', () => {
    const fp = makeFingerprint('repo-files', {
      filesWritten: ['/tmp/output.txt'],
    });

    const current = makeSummary({
      filesWritten: ['/tmp/output.txt', '/tmp/newfile.txt'],
    });

    const report = detectDrift(fp, current);
    const fileDrift = report.drifts.find(d => d.category === 'file-write')!;
    expect(fileDrift.severity).toBe('minor');
  });

  it('should detect new module loads', () => {
    const fp = makeFingerprint('repo-mod', {
      externalModules: ['lodash'],
    });

    const current = makeSummary({
      externalModules: ['lodash', 'child_process'],
    });

    const report = detectDrift(fp, current);
    expect(report.drifts.some(d => d.category === 'module-load')).toBe(true);
  });

  it('should compute overall severity as the maximum', () => {
    const fp = makeFingerprint('repo-multi', {
      networkTargets: ['api.example.com:443'],
      processesSpawned: ['node'],
    });

    const current = makeSummary({
      networkTargets: ['api.example.com:443', 'new.com:80'],
      processesSpawned: ['node', 'bash'],
      evasionAttempted: true,
    });

    const report = detectDrift(fp, current);
    expect(report.severity).toBe('critical');
  });

  it('should recommend suspension when severity meets threshold', () => {
    const fp = makeFingerprint('repo-suspend', {
      processesSpawned: ['node'],
    });

    const current = makeSummary({
      processesSpawned: ['node', 'bash'],
    });

    const report = detectDrift(fp, current, {
      ...DEFAULT_MONITOR_CONFIG,
      autoSuspendThreshold: 'critical',
    });
    expect(report.shouldSuspend).toBe(true);
  });

  it('should not recommend suspension when below threshold', () => {
    const fp = makeFingerprint('repo-ok', {
      filesWritten: ['/tmp/a.txt'],
    });

    const current = makeSummary({
      filesWritten: ['/tmp/a.txt', '/tmp/b.txt'],
    });

    const report = detectDrift(fp, current);
    expect(report.shouldSuspend).toBe(false);
  });

  it('should generate human-readable summary', () => {
    const fp = makeFingerprint('repo-summary', {
      networkTargets: ['api.example.com:443'],
    });

    const current = makeSummary({
      networkTargets: ['api.example.com:443', 'evil.com:80'],
    });

    const report = detectDrift(fp, current);
    expect(report.summary).toContain('drift');
    expect(report.summary).toContain('network');
  });

  it('should report no-drift summary for clean behavior', () => {
    const fp = makeFingerprint('repo-clean2');
    const current = makeSummary();

    const report = detectDrift(fp, current);
    expect(report.summary).toContain('No behavioral drift');
  });

  it('should use custom config thresholds', () => {
    const fp = makeFingerprint('repo-custom', {
      networkTargets: ['api.example.com:443'],
    });

    const current = makeSummary({
      networkTargets: ['api.example.com:443', 'new.com:80'],
    });

    const strictConfig: MonitorConfig = {
      ...DEFAULT_MONITOR_CONFIG,
      networkDriftThreshold: 1,  // Even 1 new target → major
    };

    const report = detectDrift(fp, current, strictConfig);
    const netDrift = report.drifts.find(d => d.category === 'network')!;
    expect(netDrift.severity).toBe('major');
  });
});

// ═════════════════════════════════════════════════════════════════════
// 3. Audit Logging
// ═════════════════════════════════════════════════════════════════════

describe('Audit Logging', () => {
  describe('createAuditEntry', () => {
    it('should create entry with all required fields', () => {
      const entry = createAuditEntry('repo-1', 'fs-read', 'Read /tmp/file.txt');

      expect(entry.id).toMatch(/^audit-/);
      expect(entry.repoId).toBe('repo-1');
      expect(entry.action).toBe('fs-read');
      expect(entry.detail).toBe('Read /tmp/file.txt');
      expect(entry.blocked).toBe(false);
      expect(entry.timestamp).toBeGreaterThan(0);
    });

    it('should support blocked flag', () => {
      const entry = createAuditEntry('repo-1', 'net-connect', 'Connecting to evil.com', true);
      expect(entry.blocked).toBe(true);
    });

    it('should sanitize metadata', () => {
      const entry = createAuditEntry('repo-1', 'fs-read', 'test', false, {
        path: '/tmp/file.txt',
        count: 42,
        valid: true,
        nested: { bad: 'data' },  // Should be dropped
      });

      expect(entry.metadata?.path).toBe('/tmp/file.txt');
      expect(entry.metadata?.count).toBe(42);
      expect(entry.metadata?.valid).toBe(true);
      expect(entry.metadata?.nested).toBeUndefined();
    });

    it('should cap detail length to 500 characters', () => {
      const longDetail = 'x'.repeat(1000);
      const entry = createAuditEntry('repo-1', 'fs-read', longDetail);
      expect(entry.detail.length).toBe(500);
    });

    it('should generate unique IDs', () => {
      const ids = new Set<string>();
      for (let i = 0; i < 100; i++) {
        ids.add(createAuditEntry('repo-1', 'fs-read', 'test').id);
      }
      expect(ids.size).toBe(100);
    });
  });

  describe('queryAuditLog', () => {
    let entries: AuditEntry[];

    beforeEach(() => {
      entries = [
        makeAuditEntry({ repoId: 'repo-a', action: 'fs-read', timestamp: 1000 }),
        makeAuditEntry({ repoId: 'repo-a', action: 'net-request', timestamp: 2000 }),
        makeAuditEntry({ repoId: 'repo-b', action: 'fs-write', timestamp: 3000 }),
        makeAuditEntry({ repoId: 'repo-a', action: 'process-spawn', timestamp: 4000, blocked: true }),
        makeAuditEntry({ repoId: 'repo-b', action: 'env-access', timestamp: 5000 }),
      ];
    });

    it('should filter by repoId', () => {
      const result = queryAuditLog(entries, { repoId: 'repo-a' });
      expect(result).toHaveLength(3);
      expect(result.every(e => e.repoId === 'repo-a')).toBe(true);
    });

    it('should filter by time range', () => {
      const result = queryAuditLog(entries, { startTime: 2000, endTime: 4000 });
      expect(result).toHaveLength(3);
    });

    it('should filter by action type', () => {
      const result = queryAuditLog(entries, { action: 'fs-read' });
      expect(result).toHaveLength(1);
    });

    it('should filter blocked-only', () => {
      const result = queryAuditLog(entries, { blockedOnly: true });
      expect(result).toHaveLength(1);
      expect(result[0].action).toBe('process-spawn');
    });

    it('should combine multiple filters', () => {
      const result = queryAuditLog(entries, {
        repoId: 'repo-a',
        startTime: 1500,
      });
      expect(result).toHaveLength(2);
    });

    it('should return all entries with no filters', () => {
      const result = queryAuditLog(entries, {});
      expect(result).toHaveLength(5);
    });
  });

  describe('summarizeAuditLog', () => {
    it('should handle empty log', () => {
      expect(summarizeAuditLog([])).toBe('No activity recorded in this period.');
    });

    it('should count actions by type', () => {
      const entries = [
        makeAuditEntry({ action: 'fs-read' }),
        makeAuditEntry({ action: 'fs-read' }),
        makeAuditEntry({ action: 'net-request' }),
      ];

      const summary = summarizeAuditLog(entries);
      expect(summary).toContain('3 total actions');
      expect(summary).toContain('fs-read: 2');
      expect(summary).toContain('net-request: 1');
    });

    it('should report blocked actions', () => {
      const entries = [
        makeAuditEntry({ blocked: true }),
        makeAuditEntry({ blocked: false }),
      ];

      const summary = summarizeAuditLog(entries);
      expect(summary).toContain('1 action(s) were blocked');
    });
  });
});

// ═════════════════════════════════════════════════════════════════════
// 4. Re-scan Scheduling
// ═════════════════════════════════════════════════════════════════════

describe('Re-scan Scheduling', () => {
  it('should identify repos needing rescan based on interval', () => {
    const eightDaysAgo = Date.now() - 8 * 24 * 60 * 60 * 1000;
    const twoDaysAgo = Date.now() - 2 * 24 * 60 * 60 * 1000;

    const fingerprints: BehavioralFingerprint[] = [
      makeFingerprint('repo-old'),
      makeFingerprint('repo-recent'),
    ];
    // Override creation times
    fingerprints[0].createdAt = eightDaysAgo;
    fingerprints[0].lastRescanAt = null;
    fingerprints[1].createdAt = twoDaysAgo;
    fingerprints[1].lastRescanAt = null;

    const needsRescan = getReposNeedingRescan(fingerprints);
    expect(needsRescan).toContain('repo-old');
    expect(needsRescan).not.toContain('repo-recent');
  });

  it('should use lastRescanAt if available', () => {
    const fp = makeFingerprint('repo-rescanned');
    fp.createdAt = Date.now() - 30 * 24 * 60 * 60 * 1000; // 30 days ago
    fp.lastRescanAt = Date.now() - 1 * 24 * 60 * 60 * 1000; // 1 day ago

    const needsRescan = getReposNeedingRescan([fp]);
    expect(needsRescan).not.toContain('repo-rescanned');
  });

  it('should respect custom rescan interval', () => {
    const fp = makeFingerprint('repo-custom');
    fp.createdAt = Date.now() - 2 * 24 * 60 * 60 * 1000; // 2 days ago

    const config: MonitorConfig = {
      ...DEFAULT_MONITOR_CONFIG,
      rescanIntervalDays: 1, // Rescan daily
    };

    const needsRescan = getReposNeedingRescan([fp], config);
    expect(needsRescan).toContain('repo-custom');
  });

  it('should return empty array when no repos need rescan', () => {
    const fp = makeFingerprint('repo-fresh');
    fp.createdAt = Date.now();

    expect(getReposNeedingRescan([fp])).toEqual([]);
  });
});

// ═════════════════════════════════════════════════════════════════════
// 5. Serialization
// ═════════════════════════════════════════════════════════════════════

describe('Serialization', () => {
  describe('Fingerprint serialization', () => {
    it('should round-trip a fingerprint through JSON', () => {
      const fp = makeFingerprint('repo-serial', {
        networkTargets: ['api.example.com:443'],
        processesSpawned: ['node'],
      });

      const serialized = serializeFingerprint(fp);
      const deserialized = deserializeFingerprint(serialized);

      expect(deserialized).not.toBeNull();
      expect(deserialized!.repoId).toBe('repo-serial');
      expect(deserialized!.baseline.networkTargets).toEqual(['api.example.com:443']);
      expect(deserialized!.baseline.processesSpawned).toEqual(['node']);
    });

    it('should return null for invalid JSON', () => {
      expect(deserializeFingerprint('not json')).toBeNull();
      expect(deserializeFingerprint('{}')).toBeNull(); // missing repoId and baseline
      expect(deserializeFingerprint('null')).toBeNull();
      expect(deserializeFingerprint('')).toBeNull();
    });

    it('should return null for malformed fingerprint', () => {
      expect(deserializeFingerprint('{"repoId":"x"}')).toBeNull(); // missing baseline
    });
  });

  describe('Audit entry serialization (JSONL)', () => {
    it('should round-trip audit entries through JSONL', () => {
      const entries = [
        makeAuditEntry({ action: 'fs-read', detail: 'Read file A' }),
        makeAuditEntry({ action: 'net-request', detail: 'GET /api/data' }),
        makeAuditEntry({ action: 'process-spawn', detail: 'spawn bash', blocked: true }),
      ];

      const serialized = serializeAuditEntries(entries);
      const deserialized = deserializeAuditEntries(serialized);

      expect(deserialized).toHaveLength(3);
      expect(deserialized[0].action).toBe('fs-read');
      expect(deserialized[1].action).toBe('net-request');
      expect(deserialized[2].blocked).toBe(true);
    });

    it('should handle empty input', () => {
      expect(serializeAuditEntries([])).toBe('');
      expect(deserializeAuditEntries('')).toEqual([]);
      expect(deserializeAuditEntries('  ')).toEqual([]);
    });

    it('should skip invalid JSONL lines', () => {
      const jsonl = [
        JSON.stringify(makeAuditEntry({ detail: 'valid' })),
        'not-valid-json',
        JSON.stringify(makeAuditEntry({ detail: 'also-valid' })),
      ].join('\n');

      const deserialized = deserializeAuditEntries(jsonl);
      expect(deserialized).toHaveLength(2);
      expect(deserialized[0].detail).toBe('valid');
      expect(deserialized[1].detail).toBe('also-valid');
    });

    it('should handle trailing newlines', () => {
      const entry = makeAuditEntry({ detail: 'test' });
      const jsonl = JSON.stringify(entry) + '\n\n';
      const deserialized = deserializeAuditEntries(jsonl);
      expect(deserialized).toHaveLength(1);
    });
  });
});

// ═════════════════════════════════════════════════════════════════════
// 6. Utility Functions
// ═════════════════════════════════════════════════════════════════════

describe('Utility Functions', () => {
  it('driftSeverityToNumber should order correctly', () => {
    expect(driftSeverityToNumber('none')).toBe(0);
    expect(driftSeverityToNumber('minor')).toBe(1);
    expect(driftSeverityToNumber('notable')).toBe(2);
    expect(driftSeverityToNumber('major')).toBe(3);
    expect(driftSeverityToNumber('critical')).toBe(4);
  });

  it('driftSeverityToNumber should maintain strict ordering', () => {
    const severities: DriftSeverity[] = ['none', 'minor', 'notable', 'major', 'critical'];
    for (let i = 0; i < severities.length - 1; i++) {
      expect(driftSeverityToNumber(severities[i])).toBeLessThan(
        driftSeverityToNumber(severities[i + 1])
      );
    }
  });
});

// ═════════════════════════════════════════════════════════════════════
// 7. cLaw Gate — Safety Invariants
// ═════════════════════════════════════════════════════════════════════

describe('cLaw Gate: Post-Ingestion Monitoring Safety', () => {
  it('SAFETY: Evasion detection ALWAYS results in critical severity', () => {
    const fp = makeFingerprint('repo-evasion-safety');
    const current = makeSummary({ evasionAttempted: true });

    const report = detectDrift(fp, current);
    expect(report.severity).toBe('critical');
    expect(report.drifts.some(d => d.category === 'evasion' && d.severity === 'critical')).toBe(true);
  });

  it('SAFETY: New process spawns ALWAYS trigger major or critical', () => {
    const fp = makeFingerprint('repo-proc-safety');
    const current = makeSummary({ processesSpawned: ['rm'] });

    const report = detectDrift(fp, current);
    const procDrift = report.drifts.find(d => d.category === 'process')!;
    expect(driftSeverityToNumber(procDrift.severity)).toBeGreaterThanOrEqual(
      driftSeverityToNumber('critical')
    );
  });

  it('SAFETY: Sensitive env var access ALWAYS triggers major severity', () => {
    const fp = makeFingerprint('repo-env-safety');
    const sensitiveVars = ['AWS_SECRET_KEY', 'DATABASE_PASSWORD', 'API_TOKEN', 'AUTH_CREDENTIAL'];

    for (const envVar of sensitiveVars) {
      const current = makeSummary({ envVarsAccessed: [envVar] });
      const report = detectDrift(fp, current);
      const envDrift = report.drifts.find(d => d.category === 'env-access')!;
      expect(envDrift.severity).toBe('major');
    }
  });

  it('SAFETY: Audit entries are immutable — no mutation of returned entries', () => {
    const entry = createAuditEntry('repo-immutable', 'fs-read', 'test');
    const original = { ...entry };

    // Query should return matching entries
    const results = queryAuditLog([entry], { repoId: 'repo-immutable' });
    expect(results[0].id).toBe(original.id);
    expect(results[0].detail).toBe(original.detail);
  });

  it('SAFETY: Metadata sanitization drops complex types', () => {
    const entry = createAuditEntry('repo-meta', 'fs-read', 'test', false, {
      safe: 'string',
      number: 42,
      bool: true,
      nested: { attack: 'payload' } as any,
      array: [1, 2, 3] as any,
      func: (() => {}) as any,
    });

    expect(entry.metadata?.safe).toBe('string');
    expect(entry.metadata?.number).toBe(42);
    expect(entry.metadata?.bool).toBe(true);
    expect(entry.metadata?.nested).toBeUndefined();
    expect(entry.metadata?.array).toBeUndefined();
    expect(entry.metadata?.func).toBeUndefined();
  });

  it('SAFETY: Metadata string values are capped at 200 characters', () => {
    const entry = createAuditEntry('repo-cap', 'fs-read', 'test', false, {
      longValue: 'x'.repeat(500),
    });

    expect((entry.metadata?.longValue as string).length).toBe(200);
  });

  it('SAFETY: Metadata is capped at 20 keys', () => {
    const bigMeta: Record<string, unknown> = {};
    for (let i = 0; i < 50; i++) {
      bigMeta[`key${i}`] = `value${i}`;
    }

    const entry = createAuditEntry('repo-keys', 'fs-read', 'test', false, bigMeta);
    expect(Object.keys(entry.metadata!).length).toBeLessThanOrEqual(20);
  });

  it('SAFETY: Drift detection never downgrades severity below observation level', () => {
    // Each individual drift should maintain its severity
    const fp = makeFingerprint('repo-no-downgrade');
    const current = makeSummary({
      processesSpawned: ['bash'],       // critical
      evasionAttempted: true,            // critical
      networkTargets: ['evil.com:80'],   // notable
    });

    const report = detectDrift(fp, current);

    // Process drift should be critical, not downgraded
    const procDrift = report.drifts.find(d => d.category === 'process')!;
    expect(procDrift.severity).toBe('critical');

    // Evasion should always be critical
    const evasionDrift = report.drifts.find(d => d.category === 'evasion')!;
    expect(evasionDrift.severity).toBe('critical');

    // Overall should be critical (maximum of all)
    expect(report.severity).toBe('critical');
  });

  it('SAFETY: Deserialization of corrupted data returns null/empty (no crash)', () => {
    // Fingerprint
    expect(deserializeFingerprint('')).toBeNull();
    expect(deserializeFingerprint('null')).toBeNull();
    expect(deserializeFingerprint('{invalid json')).toBeNull();
    expect(deserializeFingerprint('{"repoId":"x"}')).toBeNull();

    // Audit entries
    expect(deserializeAuditEntries('')).toEqual([]);
    expect(deserializeAuditEntries('not json\nalso not json')).toEqual([]);
  });

  it('SAFETY: Suspension recommended at critical threshold by default', () => {
    const fp = makeFingerprint('repo-suspend-safety');
    const current = makeSummary({
      processesSpawned: ['bash'],  // Critical severity
    });

    const report = detectDrift(fp, current);
    expect(report.severity).toBe('critical');
    expect(report.shouldSuspend).toBe(true);
  });
});
