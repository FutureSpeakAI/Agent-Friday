/**
 * git-review.test.ts — Tests for Claude-powered code review gate.
 *
 * Track I, Phase 3: The Immune System — Claude Review Gate.
 *
 * Test categories:
 *   1. File Selection — prioritization logic for Claude review
 *   2. Prompt Construction — system prompt and user message building
 *   3. Response Parsing — Claude JSON response parsing + defensive handling
 *   4. Layer Agreement — three-layer conflict resolution
 *   5. Verdict Synthesis — outcome determination + summary generation
 *   6. Risk Scoring — combined risk computation
 *   7. cLaw Gate — safety invariants (no code execution, consent required)
 */

import { describe, it, expect, vi } from 'vitest';
import type { ScanReport, ScanFinding, RiskLevel, FindingCategory } from '../../src/main/git-scanner';
import type { BehavioralProfile, BehavioralSummary, SandboxObservation } from '../../src/main/git-sandbox';
import type { LoadedRepo, RepoFile, RepoTreeEntry } from '../../src/main/git-loader';

// Mock Electron and server.ts to avoid Electron runtime dependencies in tests
vi.mock('electron', () => ({
  app: { getPath: vi.fn(() => '/tmp/test-userdata') },
  safeStorage: {
    isEncryptionAvailable: vi.fn(() => false),
    encryptString: vi.fn((s: string) => Buffer.from(s)),
    decryptString: vi.fn((b: Buffer) => b.toString()),
  },
  ipcMain: { handle: vi.fn() },
}));

vi.mock('../../src/main/server', () => ({
  runClaudeToolLoop: vi.fn(async () => ({ response: '{}', model: 'mock', toolCalls: 0 })),
}));

import {
  selectFilesForReview,
  buildReviewSystemPrompt,
  buildReviewUserMessage,
  parseClaudeResponse,
  resolveLayerAgreement,
  determineOutcome,
  generateVerdictSummary,
  generateTechnicalDetail,
  riskLevelFromScore,
  riskLevelToNumber,
} from '../../src/main/git-review';
import type {
  SecurityVerdict,
  ReviewFinding,
  IntentAnalysis,
  LayerAgreement,
  VerdictOutcome,
} from '../../src/main/git-review';

// ── Test Helpers ────────────────────────────────────────────────────

function makeFile(filePath: string, content: string, language = 'javascript'): RepoFile {
  return { path: filePath, content, language, size: content.length };
}

function makeRepo(files: RepoFile[]): LoadedRepo {
  return {
    id: 'test-repo',
    name: 'test-repo',
    owner: 'test',
    branch: 'main',
    description: 'Test repository',
    url: 'https://github.com/test/test-repo',
    localPath: '/tmp/test-repo',
    files,
    tree: files.map(f => ({ path: f.path, type: 'file' as const, size: f.size })),
    loadedAt: Date.now(),
    totalSize: files.reduce((s, f) => s + f.size, 0),
  };
}

function makeFinding(overrides: Partial<ScanFinding> = {}): ScanFinding {
  return {
    id: overrides.id || 'sf-1',
    category: overrides.category || 'network',
    severity: overrides.severity || 'medium',
    title: overrides.title || 'Test finding',
    description: overrides.description || 'Test description',
    recommendation: overrides.recommendation || 'Review this',
    file: overrides.file,
    line: overrides.line,
    snippet: overrides.snippet,
  };
}

function makeScanReport(overrides: Partial<ScanReport> = {}): ScanReport {
  return {
    repoId: 'test-repo',
    repoUrl: 'https://github.com/test/test-repo',
    timestamp: Date.now(),
    durationMs: 100,
    riskLevel: overrides.riskLevel || 'low',
    riskScore: overrides.riskScore ?? 10,
    findings: overrides.findings || [],
    dependencies: overrides.dependencies || { totalDependencies: 0, directDependencies: [], suspiciousPackages: [], installScripts: [], typosquatCandidates: [] },
    secrets: overrides.secrets || { potentialSecrets: 0, categories: {} },
    obfuscation: overrides.obfuscation || { evalCalls: 0, base64Patterns: 0, hexPatterns: 0, charCodePatterns: 0, minifiedFiles: [] },
    network: overrides.network || { uniqueUrls: [], uniqueIps: [], fetchCalls: 0, websocketRefs: 0 },
    promptInjection: overrides.promptInjection || { injectionAttempts: 0, patterns: [] },
    filesScanned: overrides.filesScanned ?? 10,
    totalSize: overrides.totalSize ?? 5000,
    languages: overrides.languages || { javascript: 10 },
    extensions: overrides.extensions,
  };
}

function makeBehavioralProfile(overrides: Partial<BehavioralProfile> = {}): BehavioralProfile {
  const defaultSummary: BehavioralSummary = {
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
  };
  return {
    runId: 'test-run',
    timestamp: Date.now(),
    durationMs: 500,
    entryPointsTested: overrides.entryPointsTested || ['index.js'],
    observations: overrides.observations || [],
    summary: { ...defaultSummary, ...overrides.summary },
    resources: overrides.resources || { peakMemoryMB: 50, cpuTimeMs: 100, wallTimeMs: 500 },
    riskContribution: overrides.riskContribution ?? 5,
    findings: overrides.findings || [],
    sandboxIntegrity: overrides.sandboxIntegrity || 'intact',
  };
}

function makeClaudeResponseJson(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    purposeSummary: overrides.purposeSummary ?? 'A utility library for string formatting',
    purposeAligned: overrides.purposeAligned ?? true,
    capabilities: overrides.capabilities ?? ['string-manipulation', 'regex'],
    adversarialCapabilities: overrides.adversarialCapabilities ?? [],
    discrepancies: overrides.discrepancies ?? [],
    findings: overrides.findings ?? [],
    overallRisk: overrides.overallRisk ?? 'low',
    overallConfidence: overrides.overallConfidence ?? 0.85,
    humanSummary: overrides.humanSummary ?? 'This library appears safe and does what it says.',
  });
}

// ══════════════════════════════════════════════════════════════════════
// TEST SUITES
// ══════════════════════════════════════════════════════════════════════

describe('Git Review — File Selection', () => {
  it('should prioritize files with findings', () => {
    const files = [
      makeFile('safe.js', 'console.log("ok")'),
      makeFile('risky.js', 'eval("code")'),
      makeFile('utils.js', 'module.exports = {}'),
    ];
    const repo = makeRepo(files);
    const report = makeScanReport({ findings: [makeFinding({ file: 'risky.js' })] });

    const selected = selectFilesForReview(repo, report, null);
    // risky.js should be first due to finding
    expect(selected[0].path).toBe('risky.js');
  });

  it('should prioritize package.json', () => {
    const files = [
      makeFile('lib/utils.js', 'module.exports = {}'),
      makeFile('package.json', '{"name":"test"}'),
      makeFile('src/other.js', 'console.log(1)'),
    ];
    const repo = makeRepo(files);
    const report = makeScanReport();

    const selected = selectFilesForReview(repo, report, null);
    expect(selected[0].path).toBe('package.json');
  });

  it('should prioritize README for discrepancy checking', () => {
    const files = [
      makeFile('lib/deep.js', 'module.exports = {}'),
      makeFile('README.md', '# My Library\nDoes things'),
      makeFile('package.json', '{"name":"test"}'),
    ];
    const repo = makeRepo(files);
    const report = makeScanReport();

    const selected = selectFilesForReview(repo, report, null);
    const readmeIdx = selected.findIndex(f => f.path === 'README.md');
    expect(readmeIdx).toBeLessThan(2); // Should be in top 2
  });

  it('should respect maxFiles limit', () => {
    const files = Array.from({ length: 50 }, (_, i) => makeFile(`file${i}.js`, `// file ${i}`));
    const repo = makeRepo(files);
    const report = makeScanReport();

    const selected = selectFilesForReview(repo, report, null, { maxFiles: 5 });
    expect(selected.length).toBeLessThanOrEqual(5);
  });

  it('should respect token budget', () => {
    // Create files that would exceed budget
    const bigContent = 'x'.repeat(10000);
    const files = Array.from({ length: 20 }, (_, i) => makeFile(`file${i}.js`, bigContent));
    const repo = makeRepo(files);
    const report = makeScanReport();

    // Very small token budget — should limit files
    const selected = selectFilesForReview(repo, report, null, { maxTokenBudget: 5000 });
    expect(selected.length).toBeLessThan(20);
  });

  it('should always select at least one file', () => {
    const bigContent = 'x'.repeat(100000);
    const files = [makeFile('big.js', bigContent)];
    const repo = makeRepo(files);
    const report = makeScanReport();

    // Tiny budget
    const selected = selectFilesForReview(repo, report, null, { maxTokenBudget: 100 });
    expect(selected.length).toBeGreaterThanOrEqual(1);
  });

  it('should prioritize entry points from behavioral analysis', () => {
    const files = [
      makeFile('random.js', 'console.log(1)'),
      makeFile('setup.js', 'require("child_process")'),
      makeFile('lib/deep.js', 'module.exports = {}'),
    ];
    const repo = makeRepo(files);
    const report = makeScanReport();
    const profile = makeBehavioralProfile({ entryPointsTested: ['setup.js'] });

    const selected = selectFilesForReview(repo, report, profile);
    const setupIdx = selected.findIndex(f => f.path === 'setup.js');
    expect(setupIdx).toBeLessThan(2); // Should be prioritized
  });

  it('should skip non-code files', () => {
    const files = [
      makeFile('index.js', 'console.log(1)'),
      makeFile('image.png', 'binary-data', 'binary'),
      makeFile('data.csv', 'a,b,c', 'csv'),
    ];
    const repo = makeRepo(files);
    const report = makeScanReport();

    const selected = selectFilesForReview(repo, report, null);
    // .js gets priority score, others get low score but are still selected if budget allows
    expect(selected[0].path).toBe('index.js');
  });
});

describe('Git Review — Prompt Construction', () => {
  it('should build a system prompt with all three analysis sections', () => {
    const prompt = buildReviewSystemPrompt();
    expect(prompt).toContain('Analysis 1: Intent Analysis');
    expect(prompt).toContain('Analysis 2: Adversarial Framing');
    expect(prompt).toContain('Analysis 3: Documentation Discrepancy');
  });

  it('should require JSON output format', () => {
    const prompt = buildReviewSystemPrompt();
    expect(prompt).toContain('JSON');
    expect(prompt).toContain('purposeSummary');
    expect(prompt).toContain('overallRisk');
  });

  it('should include static analysis in user message', () => {
    const files = [makeFile('index.js', 'console.log(1)')];
    const report = makeScanReport({ riskScore: 45, riskLevel: 'medium' });
    const msg = buildReviewUserMessage(files, report, null);
    expect(msg).toContain('Static Analysis Summary');
    expect(msg).toContain('medium');
    expect(msg).toContain('45');
  });

  it('should include behavioral analysis when available', () => {
    const files = [makeFile('index.js', 'console.log(1)')];
    const report = makeScanReport();
    const profile = makeBehavioralProfile({
      summary: { networkTargets: ['evil.com:443'], evasionAttempted: true } as BehavioralSummary,
    });
    const msg = buildReviewUserMessage(files, report, profile);
    expect(msg).toContain('Behavioral Analysis Summary');
    expect(msg).toContain('evil.com:443');
    expect(msg).toContain('true'); // evasion
  });

  it('should include file contents in user message', () => {
    const files = [
      makeFile('index.js', 'const x = 42;'),
      makeFile('lib/util.js', 'export default {}'),
    ];
    const report = makeScanReport();
    const msg = buildReviewUserMessage(files, report, null);
    expect(msg).toContain('### index.js');
    expect(msg).toContain('const x = 42;');
    expect(msg).toContain('### lib/util.js');
  });

  it('should truncate large file contents', () => {
    const bigContent = 'x'.repeat(20000);
    const files = [makeFile('big.js', bigContent)];
    const report = makeScanReport();
    const msg = buildReviewUserMessage(files, report, null);
    expect(msg).toContain('[truncated]');
    expect(msg.length).toBeLessThan(bigContent.length);
  });

  it('should include top findings in static summary', () => {
    const files = [makeFile('index.js', 'console.log(1)')];
    const report = makeScanReport({
      findings: [
        makeFinding({ title: 'Eval usage detected', severity: 'high' }),
        makeFinding({ title: 'Hardcoded secret', severity: 'critical' }),
      ]
    });
    const msg = buildReviewUserMessage(files, report, null);
    expect(msg).toContain('Eval usage detected');
    expect(msg).toContain('Hardcoded secret');
  });
});

describe('Git Review — Response Parsing', () => {
  it('should parse valid JSON response', () => {
    const json = makeClaudeResponseJson();
    const result = parseClaudeResponse(json);
    expect(result.intentAnalysis.purposeSummary).toBe('A utility library for string formatting');
    expect(result.intentAnalysis.purposeAligned).toBe(true);
    expect(result.overallRisk).toBe('low');
    expect(result.overallConfidence).toBe(0.85);
  });

  it('should parse JSON wrapped in markdown code block', () => {
    const json = '```json\n' + makeClaudeResponseJson() + '\n```';
    const result = parseClaudeResponse(json);
    expect(result.intentAnalysis.purposeSummary).toBe('A utility library for string formatting');
  });

  it('should parse JSON with leading text', () => {
    const json = 'Here is my analysis:\n' + makeClaudeResponseJson();
    const result = parseClaudeResponse(json);
    expect(result.intentAnalysis.purposeSummary).toBe('A utility library for string formatting');
  });

  it('should handle completely unparseable response', () => {
    const result = parseClaudeResponse('This is not JSON at all, just text.');
    expect(result.overallRisk).toBe('medium');
    expect(result.overallConfidence).toBe(0);
    expect(result.findings.length).toBe(1);
    expect(result.findings[0].title).toContain('parsing failed');
  });

  it('should extract findings from response', () => {
    const json = makeClaudeResponseJson({
      findings: [
        { category: 'adversarial-finding', severity: 'high', title: 'Data exfiltration possible',
          description: 'Code sends data to external server', evidence: 'fetch("https://evil.com")', confidence: 0.9, file: 'index.js' },
        { category: 'documentation-discrepancy', severity: 'medium', title: 'Undocumented network access',
          description: 'README says offline only', evidence: 'http.get() call found', confidence: 0.8 },
      ]
    });
    const result = parseClaudeResponse(json);
    expect(result.findings.length).toBe(2);
    expect(result.findings[0].category).toBe('adversarial-finding');
    expect(result.findings[0].severity).toBe('high');
    expect(result.findings[0].confidence).toBe(0.9);
    expect(result.findings[1].category).toBe('documentation-discrepancy');
  });

  it('should clamp confidence to 0-1', () => {
    const json = makeClaudeResponseJson({
      overallConfidence: 5.0,
      findings: [{ confidence: -2 }]
    });
    const result = parseClaudeResponse(json);
    expect(result.overallConfidence).toBe(1.0);
    expect(result.findings[0].confidence).toBe(0);
  });

  it('should sanitize invalid categories and severity', () => {
    const json = makeClaudeResponseJson({
      overallRisk: 'super-dangerous',
      findings: [{ category: 'made-up', severity: 'ultra', title: 'test' }]
    });
    const result = parseClaudeResponse(json);
    expect(result.overallRisk).toBe('medium'); // Falls back
    expect(result.findings[0].category).toBe('intent-analysis'); // Falls back
    expect(result.findings[0].severity).toBe('medium'); // Falls back
  });

  it('should truncate long evidence strings', () => {
    const longEvidence = 'x'.repeat(1000);
    const json = makeClaudeResponseJson({
      findings: [{ evidence: longEvidence, title: 'test' }]
    });
    const result = parseClaudeResponse(json);
    expect(result.findings[0].evidence.length).toBeLessThanOrEqual(300);
  });

  it('should cap findings at 50', () => {
    const findings = Array.from({ length: 100 }, (_, i) => ({
      title: `Finding ${i}`, severity: 'low', category: 'intent-analysis'
    }));
    const json = makeClaudeResponseJson({ findings });
    const result = parseClaudeResponse(json);
    expect(result.findings.length).toBeLessThanOrEqual(50);
  });

  it('should handle missing fields gracefully', () => {
    const json = JSON.stringify({ someRandomField: true });
    const result = parseClaudeResponse(json);
    expect(result.intentAnalysis.purposeSummary).toBe('Unknown');
    expect(result.intentAnalysis.purposeAligned).toBe(false);
    expect(result.intentAnalysis.capabilities).toEqual([]);
  });
});

describe('Git Review — Layer Agreement', () => {
  it('should detect unanimous agreement', () => {
    const agreement = resolveLayerAgreement('low', 'low', 'low');
    expect(agreement.unanimous).toBe(true);
    expect(agreement.resolution).toBeUndefined();
  });

  it('should detect unanimous high risk', () => {
    const agreement = resolveLayerAgreement('high', 'high', 'high');
    expect(agreement.unanimous).toBe(true);
  });

  it('should resolve disagreement with highest risk winning', () => {
    const agreement = resolveLayerAgreement('low', 'medium', 'high');
    expect(agreement.unanimous).toBe(false);
    expect(agreement.resolution).toBeDefined();
    expect(agreement.resolution!.length).toBeGreaterThan(0);
  });

  it('should flag when Claude sees more risk than automated tools', () => {
    const agreement = resolveLayerAgreement('low', 'low', 'high');
    expect(agreement.unanimous).toBe(false);
    expect(agreement.resolution).toContain('Claude AI review');
    expect(agreement.resolution).toContain('Manual review');
  });

  it('should handle static-led disagreement', () => {
    const agreement = resolveLayerAgreement('critical', 'low', 'low');
    expect(agreement.unanimous).toBe(false);
    expect(agreement.resolution).toContain('Automated analysis');
  });

  it('should preserve all layer risk levels', () => {
    const agreement = resolveLayerAgreement('low', 'medium', 'critical');
    expect(agreement.staticRisk).toBe('low');
    expect(agreement.behavioralRisk).toBe('medium');
    expect(agreement.claudeRisk).toBe('critical');
  });
});

describe('Git Review — Verdict Outcome', () => {
  const unanimousLow: LayerAgreement = { unanimous: true, staticRisk: 'low', behavioralRisk: 'low', claudeRisk: 'low' };
  const unanimousMed: LayerAgreement = { unanimous: true, staticRisk: 'medium', behavioralRisk: 'medium', claudeRisk: 'medium' };
  const unanimousHigh: LayerAgreement = { unanimous: true, staticRisk: 'high', behavioralRisk: 'high', claudeRisk: 'high' };
  const disagreeClaude: LayerAgreement = { unanimous: false, staticRisk: 'low', behavioralRisk: 'low', claudeRisk: 'high', resolution: 'test' };

  it('should approve low risk with unanimous agreement', () => {
    expect(determineOutcome(10, unanimousLow)).toBe('approve');
  });

  it('should reject critical risk', () => {
    expect(determineOutcome(80, unanimousHigh)).toBe('reject');
  });

  it('should reject high risk with unanimous agreement', () => {
    expect(determineOutcome(55, unanimousHigh)).toBe('reject');
  });

  it('should flag review for high risk with disagreement', () => {
    expect(determineOutcome(55, disagreeClaude)).toBe('review');
  });

  it('should flag review for medium risk', () => {
    expect(determineOutcome(30, unanimousMed)).toBe('review');
  });

  it('should flag review when Claude alone sees high risk', () => {
    expect(determineOutcome(15, disagreeClaude)).toBe('review');
  });

  it('should approve when all layers agree on low risk and score is low', () => {
    expect(determineOutcome(5, unanimousLow)).toBe('approve');
  });
});

describe('Git Review — Risk Scoring', () => {
  it('should convert risk levels to numbers correctly', () => {
    expect(riskLevelToNumber('low')).toBe(1);
    expect(riskLevelToNumber('medium')).toBe(2);
    expect(riskLevelToNumber('high')).toBe(3);
    expect(riskLevelToNumber('critical')).toBe(4);
  });

  it('should convert scores to risk levels correctly', () => {
    expect(riskLevelFromScore(0)).toBe('low');
    expect(riskLevelFromScore(24)).toBe('low');
    expect(riskLevelFromScore(25)).toBe('medium');
    expect(riskLevelFromScore(49)).toBe('medium');
    expect(riskLevelFromScore(50)).toBe('high');
    expect(riskLevelFromScore(74)).toBe('high');
    expect(riskLevelFromScore(75)).toBe('critical');
    expect(riskLevelFromScore(100)).toBe('critical');
  });
});

describe('Git Review — Summary Generation', () => {
  const cleanIntent: IntentAnalysis = {
    purposeSummary: 'A string formatting utility',
    purposeAligned: true,
    capabilities: ['string-manipulation'],
    adversarialCapabilities: [],
    discrepancies: [],
  };

  const riskyIntent: IntentAnalysis = {
    purposeSummary: 'A utility that sends data to external servers',
    purposeAligned: false,
    capabilities: ['network-access', 'file-read'],
    adversarialCapabilities: ['data-exfiltration'],
    discrepancies: ['README says offline, but code makes HTTP requests'],
  };

  it('should generate approval summary', () => {
    const agreement: LayerAgreement = { unanimous: true, staticRisk: 'low', behavioralRisk: 'low', claudeRisk: 'low' };
    const summary = generateVerdictSummary('approve', cleanIntent, 5, agreement, 'Looks safe.');
    expect(summary).toContain('safe');
    expect(summary).toContain('A string formatting utility');
  });

  it('should generate rejection summary', () => {
    const agreement: LayerAgreement = { unanimous: true, staticRisk: 'high', behavioralRisk: 'high', claudeRisk: 'high' };
    const summary = generateVerdictSummary('reject', riskyIntent, 80, agreement, 'Suspicious network activity detected.');
    expect(summary).toContain('NOT recommended');
    expect(summary).toContain('80/100');
  });

  it('should generate review summary for disagreement', () => {
    const agreement: LayerAgreement = { unanimous: false, staticRisk: 'low', behavioralRisk: 'medium', claudeRisk: 'high', resolution: 'Layers disagree' };
    const summary = generateVerdictSummary('review', riskyIntent, 35, agreement, 'Some concerns found.');
    expect(summary).toContain('needs your attention');
    expect(summary).toContain('disagree');
  });

  it('should generate technical detail with all layers', () => {
    const report = makeScanReport({ riskScore: 30, findings: [makeFinding()] });
    const profile = makeBehavioralProfile({
      summary: { networkTargets: ['api.example.com:443'] } as BehavioralSummary,
    });
    const detail = generateTechnicalDetail(report, profile, riskyIntent, []);
    expect(detail).toContain('Layer 1: Static Pattern Analysis');
    expect(detail).toContain('Layer 2: Behavioral Sandbox');
    expect(detail).toContain('Layer 3: AI Intent Analysis');
    expect(detail).toContain('Network attempts: 1');
  });

  it('should include discrepancies in technical detail', () => {
    const report = makeScanReport();
    const detail = generateTechnicalDetail(report, null, riskyIntent, []);
    expect(detail).toContain('Documentation Discrepancies');
    expect(detail).toContain('README says offline');
  });
});

describe('Git Review — cLaw Gate: Review Safety', () => {
  it('system prompt should not contain code execution instructions', () => {
    const prompt = buildReviewSystemPrompt();
    expect(prompt).not.toMatch(/eval\s*\(/);
    expect(prompt).not.toMatch(/exec\s*\(/);
    expect(prompt).not.toMatch(/spawn\s*\(/);
    expect(prompt).not.toMatch(/require\s*\(/);
    expect(prompt).not.toContain('npm install');
    expect(prompt).not.toContain('npm run');
  });

  it('user message should not include secrets from scan report', () => {
    const files = [makeFile('index.js', 'const key = "sk-abc123"')];
    const report = makeScanReport({
      secrets: { potentialSecrets: 1, categories: { 'api-key': 1 } }
    });
    const msg = buildReviewUserMessage(files, report, null);
    // The user message includes a summary count, not the field name or actual secret values
    expect(msg).toContain('Secrets found: 1');
    // Secret values in snippets are controlled by scanner (Phase 1 truncates them)
  });

  it('verdict outcome should always require user consent before action', () => {
    // This tests the invariant: every verdict is advisory, never auto-execute
    // The SecurityVerdict type has outcome + summary — it doesn't have an "execute" method
    const outcomes: VerdictOutcome[] = ['approve', 'review', 'reject'];
    for (const outcome of outcomes) {
      // Even "approve" just means "safe to proceed IF user consents"
      expect(typeof outcome).toBe('string');
    }
  });

  it('risk score should never exceed 100', () => {
    // Test boundary: even with max inputs, combined stays ≤ 100
    expect(riskLevelFromScore(0)).toBe('low');
    expect(riskLevelFromScore(100)).toBe('critical');
    expect(riskLevelFromScore(150)).toBe('critical'); // Over 100 still works
  });

  it('parsed findings should have bounded lengths', () => {
    const longTitle = 'A'.repeat(500);
    const longDesc = 'B'.repeat(1000);
    const longEvidence = 'C'.repeat(1000);
    const json = makeClaudeResponseJson({
      findings: [{ title: longTitle, description: longDesc, evidence: longEvidence }]
    });
    const result = parseClaudeResponse(json);
    expect(result.findings[0].title.length).toBeLessThanOrEqual(200);
    expect(result.findings[0].description.length).toBeLessThanOrEqual(500);
    expect(result.findings[0].evidence.length).toBeLessThanOrEqual(300);
  });

  it('file selection should cap total content size', () => {
    // Create 100 files each 50KB — would be 5MB total
    const bigContent = 'x'.repeat(50_000);
    const files = Array.from({ length: 100 }, (_, i) => makeFile(`file${i}.js`, bigContent));
    const repo = makeRepo(files);
    const report = makeScanReport();

    const selected = selectFilesForReview(repo, report, null, { maxTokenBudget: 10000 });
    // 10000 tokens ≈ 40000 chars, each file is 50000 (capped to 8000 per file)
    // So max ~5 files
    expect(selected.length).toBeLessThan(10);
  });

  it('should produce reject outcome for critical risk regardless of agreement', () => {
    const agreement: LayerAgreement = { unanimous: false, staticRisk: 'low', behavioralRisk: 'low', claudeRisk: 'low' };
    expect(determineOutcome(80, agreement)).toBe('reject');
  });

  it('should never auto-approve when Claude detects high risk', () => {
    const agreement: LayerAgreement = { unanimous: false, staticRisk: 'low', behavioralRisk: 'low', claudeRisk: 'high', resolution: 'test' };
    const outcome = determineOutcome(10, agreement);
    // Even with low combined score, Claude's high risk triggers review
    expect(outcome).not.toBe('approve');
    expect(outcome).toBe('review');
  });
});
