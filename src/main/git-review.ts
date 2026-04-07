/**
 * git-review.ts — Claude-powered code review gate for GitLoader security.
 *
 * Track I, Phase 3: The Immune System — Claude Review Gate.
 *
 * Sends repo core files to Claude for intent analysis, adversarial framing,
 * and documentation discrepancy checks. Routes through server.ts Claude client.
 *
 * Three-layer synthesis:
 *   Layer 1: Static scan (Phase 1 ScanReport)
 *   Layer 2: Behavioral profile (Phase 2 BehavioralProfile)
 *   Layer 3: Claude intent analysis (this phase)
 *
 * Output: SecurityVerdict — the final synthesized report for user consent.
 *
 * cLaw Safety Boundary:
 *   - No code execution — Claude analyzes text only
 *   - Token budget enforced (MAX_REVIEW_TOKENS)
 *   - File selection prioritizes risk-bearing files
 *   - Claude response is advisory — not a hard gate on its own
 *   - User always sees verdict before any adaptation begins
 */

import type { ScanReport, ScanFinding, RiskLevel } from './git-scanner';
import type { BehavioralProfile } from './git-sandbox';
import type { LoadedRepo, RepoFile } from './git-loader';
import { runClaudeToolLoop } from './server';
import type { ChatMessage } from './llm-client';

// ── Types ────────────────────────────────────────────────────────────

export type VerdictOutcome = 'approve' | 'review' | 'reject';

export type ReviewCategory =
  | 'intent-analysis'
  | 'adversarial-finding'
  | 'documentation-discrepancy'
  | 'capability-assessment';

export interface ReviewFinding {
  id: string;
  category: ReviewCategory;
  severity: RiskLevel;
  title: string;
  description: string;
  evidence: string;             // Code excerpt or reasoning (max 300 chars)
  confidence: number;           // 0-1: how confident Claude is in this finding
  file?: string;
}

export interface IntentAnalysis {
  /** One-line summary of what the code does */
  purposeSummary: string;
  /** Does the stated purpose match actual behavior? */
  purposeAligned: boolean;
  /** Key capabilities the code possesses */
  capabilities: string[];
  /** Things the code COULD do (adversarial framing) */
  adversarialCapabilities: string[];
  /** Documentation discrepancies found */
  discrepancies: string[];
}

export interface LayerAgreement {
  /** Do all three layers agree on risk level? */
  unanimous: boolean;
  /** Per-layer risk assessments */
  staticRisk: RiskLevel;
  behavioralRisk: RiskLevel;
  claudeRisk: RiskLevel;
  /** When layers disagree, the resolution reasoning */
  resolution?: string;
}

export interface SecurityVerdict {
  /** Unique verdict ID */
  id: string;
  /** Repository ID */
  repoId: string;
  /** Repository URL */
  repoUrl: string;
  /** When the verdict was issued */
  timestamp: number;
  /** Total pipeline duration */
  pipelineDurationMs: number;

  /** Final outcome: approve, review (needs human attention), or reject */
  outcome: VerdictOutcome;
  /** Human-readable summary (2-3 sentences, non-technical) */
  summary: string;
  /** Detailed technical explanation */
  technicalDetail: string;

  /** Combined risk score (0-100) */
  riskScore: number;
  /** Risk level derived from combined score */
  riskLevel: RiskLevel;

  /** Three-layer agreement analysis */
  layerAgreement: LayerAgreement;

  /** Claude's intent analysis */
  intentAnalysis: IntentAnalysis;

  /** All findings from all three layers */
  findings: Array<ScanFinding | ReviewFinding>;

  /** Per-layer summaries for drill-down */
  layers: {
    static: {
      riskScore: number;
      findingCount: number;
      topFindings: string[];
    };
    behavioral: {
      riskContribution: number;
      sandboxIntegrity: string;
      topObservations: string[];
    };
    claude: {
      findingCount: number;
      confidence: number;
      topConcerns: string[];
    };
  };

  /** Phase 4 can extend with monitoring data */
  extensions?: Record<string, unknown>;
}

export interface ReviewOptions {
  /** Max tokens for Claude review prompt (default: 50000) */
  maxTokenBudget?: number;
  /** Max files to send to Claude (default: 30) */
  maxFiles?: number;
  /** Skip Claude review (for testing or offline mode) */
  skipClaudeReview?: boolean;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_MAX_TOKEN_BUDGET = 50_000;
const DEFAULT_MAX_FILES = 30;
const MAX_FILE_CONTENT_CHARS = 8_000;   // Per-file content cap
const MAX_EVIDENCE_LENGTH = 300;
const REVIEW_TIMEOUT_MS = 120_000;       // 2 minutes max for Claude review

// Files prioritized for review (ordered by security relevance)
const PRIORITY_EXTENSIONS = [
  '.js', '.ts', '.mjs', '.cjs',          // Runtime code
  '.jsx', '.tsx',                          // React/UI
  '.sh', '.bash', '.cmd', '.bat', '.ps1', // Scripts
  '.py', '.rb',                            // Other runtimes
];

const PRIORITY_FILES = [
  'package.json', 'setup.js', 'install.js', 'postinstall.js', 'preinstall.js',
  'index.js', 'index.ts', 'main.js', 'app.js', 'server.js',
  '.env', '.env.example', 'Dockerfile', 'docker-compose.yml',
];

// ── File Selection ───────────────────────────────────────────────────

/**
 * Select the most security-relevant files for Claude review.
 *
 * Priority order:
 * 1. Files with findings from static/behavioral analysis
 * 2. Known high-risk file names (package.json, setup scripts)
 * 3. Entry point files
 * 4. Largest code files (often contain core logic)
 *
 * Respects token budget by truncating long files.
 */
export function selectFilesForReview(
  repo: LoadedRepo,
  scanReport: ScanReport,
  behavioralProfile: BehavioralProfile | null,
  options: ReviewOptions = {}
): RepoFile[] {
  const maxFiles = options.maxFiles ?? DEFAULT_MAX_FILES;
  const maxTokenBudget = options.maxTokenBudget ?? DEFAULT_MAX_TOKEN_BUDGET;

  // Collect files with findings (highest priority)
  const filesWithFindings = new Set<string>();
  for (const finding of scanReport.findings) {
    if (finding.file) filesWithFindings.add(finding.file);
  }
  if (behavioralProfile) {
    for (const finding of behavioralProfile.findings) {
      if (finding.file) filesWithFindings.add(finding.file);
    }
  }

  // Score each file
  const scored: Array<{ file: RepoFile; score: number }> = [];
  for (const file of repo.files) {
    let score = 0;

    // Files with findings get top priority
    if (filesWithFindings.has(file.path)) score += 100;

    // Priority file names
    const basename = file.path.split('/').pop() || '';
    if (PRIORITY_FILES.includes(basename)) score += 50;

    // Priority extensions
    const ext = '.' + basename.split('.').pop();
    if (PRIORITY_EXTENSIONS.includes(ext)) score += 20;

    // Entry points from behavioral analysis
    if (behavioralProfile?.entryPointsTested.includes(file.path)) score += 30;

    // README/docs (useful for discrepancy checking)
    if (basename.toLowerCase().startsWith('readme')) score += 40;

    // Larger files are more likely to contain core logic
    score += Math.min(10, Math.floor(file.size / 1000));

    scored.push({ file, score });
  }

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);

  // Select files within token budget
  const selected: RepoFile[] = [];
  let totalChars = 0;
  // Rough estimate: 1 token ≈ 4 chars
  const charBudget = maxTokenBudget * 4;

  for (const { file } of scored) {
    if (selected.length >= maxFiles) break;
    const contentLen = Math.min(file.content.length, MAX_FILE_CONTENT_CHARS);
    if (totalChars + contentLen > charBudget) {
      // If we haven't selected any files yet, take at least one truncated
      if (selected.length === 0) {
        selected.push(file);
        break;
      }
      continue;
    }
    totalChars += contentLen;
    selected.push(file);
  }

  return selected;
}

// ── Prompt Construction ──────────────────────────────────────────────

/**
 * Build the system prompt for Claude code review.
 */
export function buildReviewSystemPrompt(): string {
  return `You are a security analyst reviewing code from a repository that a user wants to install.
Your job is to analyze the code for malicious intent, hidden capabilities, and documentation discrepancies.

You will be given:
1. The repository's files (most security-relevant ones selected)
2. A static analysis summary (pattern-matching findings)
3. A behavioral analysis summary (what the code did when executed in a sandbox)

You must perform THREE analyses:

## Analysis 1: Intent Analysis
What does this code ACTUALLY do? Summarize its purpose in one line.
Does the code's actual behavior match its stated purpose (README, package.json description)?
What capabilities does this code possess? (file access, network, process spawning, etc.)

## Analysis 2: Adversarial Framing
Now assume the worst: if this code WERE malicious, what COULD it be doing?
Look for: data exfiltration, backdoors, supply chain attacks, credential theft, cryptomining, botnet enrollment.
What's the most dangerous interpretation of ambiguous code sections?

## Analysis 3: Documentation Discrepancy
Compare what the README/docs say vs what the code actually does.
Flag undocumented capabilities, especially: network access, file writes outside project dir, env var reads, process spawning.
Rate each discrepancy: BENIGN (normal dev oversight), SUSPICIOUS (worth noting), ALARMING (potential deception).

## Output Format
Respond with ONLY a JSON object (no markdown, no explanation):
{
  "purposeSummary": "one-line description",
  "purposeAligned": true/false,
  "capabilities": ["list", "of", "capabilities"],
  "adversarialCapabilities": ["what", "it", "COULD", "do"],
  "discrepancies": ["list of documentation gaps"],
  "findings": [
    {
      "category": "intent-analysis|adversarial-finding|documentation-discrepancy|capability-assessment",
      "severity": "low|medium|high|critical",
      "title": "short title",
      "description": "explanation",
      "evidence": "code excerpt or reasoning (max 300 chars)",
      "confidence": 0.0-1.0,
      "file": "optional/file/path"
    }
  ],
  "overallRisk": "low|medium|high|critical",
  "overallConfidence": 0.0-1.0,
  "humanSummary": "2-3 sentences a non-technical user can understand"
}`;
}

/**
 * Build the user message with repo files and prior analysis results.
 */
export function buildReviewUserMessage(
  files: RepoFile[],
  scanReport: ScanReport,
  behavioralProfile: BehavioralProfile | null
): string {
  const parts: string[] = [];

  // Static analysis summary
  parts.push('## Static Analysis Summary');
  parts.push(`Risk: ${scanReport.riskLevel} (score ${scanReport.riskScore}/100)`);
  parts.push(`Findings: ${scanReport.findings.length}`);
  if (scanReport.findings.length > 0) {
    parts.push('Top findings:');
    for (const f of scanReport.findings.slice(0, 10)) {
      parts.push(`  - [${f.severity}] ${f.title}: ${f.description}`);
    }
  }
  parts.push(`Dependencies: ${scanReport.dependencies.totalDependencies} (${scanReport.dependencies.suspiciousPackages.length} suspicious)`);
  parts.push(`Secrets found: ${scanReport.secrets.potentialSecrets}`);
  parts.push(`Network: ${scanReport.network.uniqueUrls.length} URLs, ${scanReport.network.uniqueIps.length} IPs`);
  parts.push(`Obfuscation: ${scanReport.obfuscation.evalCalls} eval calls, ${scanReport.obfuscation.base64Patterns} base64 patterns`);
  parts.push('');

  // Behavioral analysis summary
  if (behavioralProfile) {
    parts.push('## Behavioral Analysis Summary');
    parts.push(`Sandbox integrity: ${behavioralProfile.sandboxIntegrity}`);
    parts.push(`Risk contribution: ${behavioralProfile.riskContribution}/50`);
    parts.push(`Entry points tested: ${behavioralProfile.entryPointsTested.join(', ') || 'none'}`);
    parts.push(`Files accessed: ${behavioralProfile.summary.filesAccessed.length}`);
    parts.push(`Files written: ${behavioralProfile.summary.filesWritten.length}`);
    parts.push(`Network targets: ${behavioralProfile.summary.networkTargets.join(', ') || 'none'}`);
    parts.push(`HTTP requests: ${behavioralProfile.summary.httpRequests.join(', ') || 'none'}`);
    parts.push(`Processes spawned: ${behavioralProfile.summary.processesSpawned.join(', ') || 'none'}`);
    parts.push(`Env vars accessed: ${behavioralProfile.summary.envVarsAccessed.join(', ') || 'none'}`);
    parts.push(`Blocked operations: ${behavioralProfile.summary.blockedOperations}`);
    parts.push(`Evasion attempted: ${behavioralProfile.summary.evasionAttempted}`);
    parts.push('');
  }

  // Repository files
  parts.push('## Repository Files');
  parts.push(`(${files.length} most security-relevant files selected)`);
  parts.push('');

  for (const file of files) {
    const content = file.content.length > MAX_FILE_CONTENT_CHARS
      ? file.content.slice(0, MAX_FILE_CONTENT_CHARS) + '\n... [truncated]'
      : file.content;
    parts.push(`### ${file.path} (${file.language}, ${file.size} bytes)`);
    parts.push('```');
    parts.push(content);
    parts.push('```');
    parts.push('');
  }

  return parts.join('\n');
}

// ── Claude Review Execution ──────────────────────────────────────────

/**
 * Parse Claude's JSON response into structured data.
 * Defensive parsing — handles malformed responses gracefully.
 */
export function parseClaudeResponse(response: string): {
  intentAnalysis: IntentAnalysis;
  findings: ReviewFinding[];
  overallRisk: RiskLevel;
  overallConfidence: number;
  humanSummary: string;
} {
  // Extract JSON from response (Claude sometimes wraps in markdown)
  let jsonStr = response.trim();
  const jsonMatch = jsonStr.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) {
    jsonStr = jsonMatch[1].trim();
  }

  // Try to find JSON object boundaries if not clean
  if (!jsonStr.startsWith('{')) {
    const start = jsonStr.indexOf('{');
    const end = jsonStr.lastIndexOf('}');
    if (start !== -1 && end !== -1) {
      jsonStr = jsonStr.slice(start, end + 1);
    }
  }

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(jsonStr);
  } catch {
    // Fallback for completely unparseable responses
    return {
      intentAnalysis: {
        purposeSummary: 'Claude review response was not parseable',
        purposeAligned: false,
        capabilities: [],
        adversarialCapabilities: [],
        discrepancies: [],
      },
      findings: [{
        id: `rf-parse-error`,
        category: 'intent-analysis',
        severity: 'medium',
        title: 'Claude review response parsing failed',
        description: 'The AI code review produced an unparseable response. Manual review recommended.',
        evidence: response.slice(0, MAX_EVIDENCE_LENGTH),
        confidence: 0,
      }],
      overallRisk: 'medium',
      overallConfidence: 0,
      humanSummary: 'The AI code review could not be completed. Manual review is recommended.',
    };
  }

  // Extract intent analysis
  const intentAnalysis: IntentAnalysis = {
    purposeSummary: typeof parsed.purposeSummary === 'string' ? parsed.purposeSummary : 'Unknown',
    purposeAligned: typeof parsed.purposeAligned === 'boolean' ? parsed.purposeAligned : false,
    capabilities: Array.isArray(parsed.capabilities) ? parsed.capabilities.map(String).slice(0, 20) : [],
    adversarialCapabilities: Array.isArray(parsed.adversarialCapabilities) ? parsed.adversarialCapabilities.map(String).slice(0, 20) : [],
    discrepancies: Array.isArray(parsed.discrepancies) ? parsed.discrepancies.map(String).slice(0, 20) : [],
  };

  // Extract findings
  const rawFindings = Array.isArray(parsed.findings) ? parsed.findings : [];
  const findings: ReviewFinding[] = rawFindings.slice(0, 50).map((f: unknown, i: number) => {
    const finding = f as Record<string, unknown>;
    return {
      id: `rf-${i}`,
      category: validateReviewCategory(finding.category) || 'intent-analysis',
      severity: validateRiskLevel(finding.severity) || 'medium',
      title: typeof finding.title === 'string' ? finding.title.slice(0, 200) : 'Unnamed finding',
      description: typeof finding.description === 'string' ? finding.description.slice(0, 500) : '',
      evidence: typeof finding.evidence === 'string' ? finding.evidence.slice(0, MAX_EVIDENCE_LENGTH) : '',
      confidence: typeof finding.confidence === 'number' ? Math.max(0, Math.min(1, finding.confidence)) : 0.5,
      file: typeof finding.file === 'string' ? finding.file : undefined,
    };
  });

  const overallRisk = validateRiskLevel(parsed.overallRisk) || 'medium';
  const overallConfidence = typeof parsed.overallConfidence === 'number'
    ? Math.max(0, Math.min(1, parsed.overallConfidence)) : 0.5;
  const humanSummary = typeof parsed.humanSummary === 'string'
    ? parsed.humanSummary.slice(0, 500) : 'AI review completed. Please check findings.';

  return { intentAnalysis, findings, overallRisk, overallConfidence, humanSummary };
}

/**
 * Execute Claude code review on a repository.
 * Routes through server.ts runClaudeToolLoop (no tools needed, just a prompt).
 */
export async function executeClaudeReview(
  files: RepoFile[],
  scanReport: ScanReport,
  behavioralProfile: BehavioralProfile | null
): Promise<{
  intentAnalysis: IntentAnalysis;
  findings: ReviewFinding[];
  overallRisk: RiskLevel;
  overallConfidence: number;
  humanSummary: string;
  durationMs: number;
}> {
  const startTime = Date.now();

  const systemPrompt = buildReviewSystemPrompt();
  const userMessage = buildReviewUserMessage(files, scanReport, behavioralProfile);

  const messages: ChatMessage[] = [
    { role: 'user', content: userMessage }
  ];

  const result = await runClaudeToolLoop({
    systemPrompt,
    messages,
    tools: [],           // No tools — pure analysis
    maxIterations: 1,    // Single turn
  });

  const parsed = parseClaudeResponse(result.response);
  return {
    ...parsed,
    durationMs: Date.now() - startTime,
  };
}

// ── Three-Layer Synthesis ────────────────────────────────────────────

/**
 * Determine risk level from numeric score.
 */
export function riskLevelFromScore(score: number): RiskLevel {
  if (score >= 75) return 'critical';
  if (score >= 50) return 'high';
  if (score >= 25) return 'medium';
  return 'low';
}

/**
 * Convert a risk level to numeric value for comparison.
 */
export function riskLevelToNumber(level: RiskLevel): number {
  switch (level) {
    case 'critical': return 4;
    case 'high': return 3;
    case 'medium': return 2;
    case 'low': return 1;
  }
}

/**
 * Determine layer agreement and resolve conflicts.
 */
export function resolveLayerAgreement(
  staticRisk: RiskLevel,
  behavioralRisk: RiskLevel,
  claudeRisk: RiskLevel
): LayerAgreement {
  const unanimous = staticRisk === behavioralRisk && behavioralRisk === claudeRisk;

  if (unanimous) {
    return { unanimous: true, staticRisk, behavioralRisk, claudeRisk };
  }

  // Resolution strategy: highest risk wins, with reasoning
  const levels = [
    { layer: 'static', level: staticRisk, num: riskLevelToNumber(staticRisk) },
    { layer: 'behavioral', level: behavioralRisk, num: riskLevelToNumber(behavioralRisk) },
    { layer: 'claude', level: claudeRisk, num: riskLevelToNumber(claudeRisk) },
  ];
  levels.sort((a, b) => b.num - a.num);

  const highest = levels[0];
  const disagreeing = levels.filter(l => l.level !== highest.level);

  let resolution: string;
  if (highest.layer === 'claude' && riskLevelToNumber(highest.level) > Math.max(riskLevelToNumber(staticRisk), riskLevelToNumber(behavioralRisk))) {
    resolution = `Claude AI review flagged ${highest.level}-risk concerns not detected by automated analysis. ` +
      `This may indicate sophisticated or context-dependent risks. Manual review recommended.`;
  } else if (highest.layer === 'static' || highest.layer === 'behavioral') {
    resolution = `Automated analysis detected ${highest.level}-risk patterns. ` +
      `${disagreeing.map(d => `${d.layer} analysis rated ${d.level}`).join('; ')}. ` +
      `Using highest risk level for safety.`;
  } else {
    resolution = `Layers disagree: ${levels.map(l => `${l.layer}=${l.level}`).join(', ')}. ` +
      `Using highest risk level (${highest.level}) as precaution.`;
  }

  return { unanimous: false, staticRisk, behavioralRisk, claudeRisk, resolution };
}

/**
 * Determine the final verdict outcome based on risk and layer agreement.
 */
export function determineOutcome(riskScore: number, layerAgreement: LayerAgreement): VerdictOutcome {
  // Critical risk always rejects
  if (riskScore >= 75) return 'reject';

  // High risk with unanimous agreement → reject
  if (riskScore >= 50 && layerAgreement.unanimous) return 'reject';

  // High risk but layers disagree → needs review
  if (riskScore >= 50) return 'review';

  // Medium risk → needs review
  if (riskScore >= 25) return 'review';

  // Low risk but Claude flagged something higher → needs review
  if (riskLevelToNumber(layerAgreement.claudeRisk) >= 3) return 'review';

  // Low risk, layers agree → approve
  return 'approve';
}

/**
 * Generate human-readable summary for the verdict.
 */
export function generateVerdictSummary(
  outcome: VerdictOutcome,
  intentAnalysis: IntentAnalysis,
  riskScore: number,
  layerAgreement: LayerAgreement,
  claudeHumanSummary: string
): string {
  if (outcome === 'approve') {
    return `This repository appears safe to use. ${intentAnalysis.purposeSummary}. ` +
      `All three analysis layers (pattern matching, sandbox execution, and AI review) agree on low risk.`;
  }

  if (outcome === 'reject') {
    return `This repository has been flagged as high-risk and is NOT recommended for installation. ` +
      `${claudeHumanSummary} Risk score: ${riskScore}/100.`;
  }

  // Review
  return `This repository needs your attention before installation. ${claudeHumanSummary} ` +
    (layerAgreement.unanimous
      ? `All analysis layers agree on ${layerAgreement.staticRisk} risk.`
      : `Analysis layers disagree: ${layerAgreement.resolution || 'manual review recommended'}.`);
}

/**
 * Generate technical detail for security-savvy users.
 */
export function generateTechnicalDetail(
  scanReport: ScanReport,
  behavioralProfile: BehavioralProfile | null,
  intentAnalysis: IntentAnalysis,
  claudeFindings: ReviewFinding[]
): string {
  const parts: string[] = [];

  parts.push(`## Three-Layer Security Analysis`);
  parts.push('');

  // Static layer
  parts.push(`### Layer 1: Static Pattern Analysis`);
  parts.push(`- Risk score: ${scanReport.riskScore}/100`);
  parts.push(`- Findings: ${scanReport.findings.length}`);
  parts.push(`- Dependencies: ${scanReport.dependencies.totalDependencies} (${scanReport.dependencies.suspiciousPackages.length} suspicious)`);
  parts.push(`- Secrets detected: ${scanReport.secrets.potentialSecrets}`);
  parts.push(`- Obfuscation: ${scanReport.obfuscation.evalCalls} eval, ${scanReport.obfuscation.base64Patterns} base64`);
  parts.push('');

  // Behavioral layer
  if (behavioralProfile) {
    parts.push(`### Layer 2: Behavioral Sandbox`);
    parts.push(`- Risk contribution: ${behavioralProfile.riskContribution}/50`);
    parts.push(`- Sandbox integrity: ${behavioralProfile.sandboxIntegrity}`);
    parts.push(`- Network attempts: ${behavioralProfile.summary.networkTargets.length}`);
    parts.push(`- Process spawn attempts: ${behavioralProfile.summary.processesSpawned.length}`);
    parts.push(`- Blocked operations: ${behavioralProfile.summary.blockedOperations}`);
    parts.push(`- Evasion attempted: ${behavioralProfile.summary.evasionAttempted ? 'YES ⚠️' : 'No'}`);
    parts.push('');
  }

  // Claude layer
  parts.push(`### Layer 3: AI Intent Analysis`);
  parts.push(`- Purpose: ${intentAnalysis.purposeSummary}`);
  parts.push(`- Purpose aligned with docs: ${intentAnalysis.purposeAligned ? 'Yes' : 'No ⚠️'}`);
  parts.push(`- Capabilities: ${intentAnalysis.capabilities.join(', ') || 'none identified'}`);
  parts.push(`- Adversarial capabilities: ${intentAnalysis.adversarialCapabilities.join(', ') || 'none'}`);
  parts.push(`- Documentation discrepancies: ${intentAnalysis.discrepancies.length}`);
  parts.push(`- AI findings: ${claudeFindings.length}`);
  parts.push('');

  // Discrepancies detail
  if (intentAnalysis.discrepancies.length > 0) {
    parts.push(`### Documentation Discrepancies`);
    for (const d of intentAnalysis.discrepancies) {
      parts.push(`- ${d}`);
    }
    parts.push('');
  }

  return parts.join('\n');
}

/**
 * Run the complete three-layer security pipeline and produce a SecurityVerdict.
 *
 * This is the main entry point for Phase 3.
 *
 * @param repo The loaded repository
 * @param scanReport Phase 1 static analysis results
 * @param behavioralProfile Phase 2 behavioral sandbox results (can be null if sandbox failed)
 * @param options Review configuration
 */
export async function produceSecurityVerdict(
  repo: LoadedRepo,
  scanReport: ScanReport,
  behavioralProfile: BehavioralProfile | null,
  options: ReviewOptions = {}
): Promise<SecurityVerdict> {
  const pipelineStart = Date.now();
  const verdictId = `sv-${crypto.randomUUID().slice(0, 12)}`;

  // Step 1: Select files for Claude review
  const files = selectFilesForReview(repo, scanReport, behavioralProfile, options);

  // Step 2: Execute Claude review (or skip if offline/testing)
  let claudeResult: Awaited<ReturnType<typeof executeClaudeReview>>;
  if (options.skipClaudeReview) {
    claudeResult = {
      intentAnalysis: {
        purposeSummary: 'Claude review skipped',
        purposeAligned: true,
        capabilities: [],
        adversarialCapabilities: [],
        discrepancies: [],
      },
      findings: [],
      overallRisk: 'low' as RiskLevel,
      overallConfidence: 0,
      humanSummary: 'AI review was skipped. Verdict based on automated analysis only.',
      durationMs: 0,
    };
  } else {
    claudeResult = await executeClaudeReview(files, scanReport, behavioralProfile);
  }

  // Step 3: Compute behavioral risk level
  const behavioralRiskScore = behavioralProfile
    ? Math.min(100, (behavioralProfile.riskContribution / 50) * 100)
    : 0;
  const behavioralRisk = riskLevelFromScore(behavioralRiskScore);

  // Step 4: Resolve layer agreement
  const layerAgreement = resolveLayerAgreement(
    scanReport.riskLevel,
    behavioralRisk,
    claudeResult.overallRisk
  );

  // Step 5: Compute final risk score
  // Weighted combination: static 35%, behavioral 25%, Claude 40%
  const staticWeight = 0.35;
  const behavioralWeight = 0.25;
  const claudeWeight = 0.40;
  const claudeRiskScore = riskLevelToNumber(claudeResult.overallRisk) * 25; // Convert to 0-100
  const combinedScore = Math.min(100, Math.round(
    scanReport.riskScore * staticWeight +
    behavioralRiskScore * behavioralWeight +
    claudeRiskScore * claudeWeight
  ));
  const riskLevel = riskLevelFromScore(combinedScore);

  // Step 6: Determine outcome
  const outcome = determineOutcome(combinedScore, layerAgreement);

  // Step 7: Generate summaries
  const summary = generateVerdictSummary(
    outcome, claudeResult.intentAnalysis, combinedScore, layerAgreement, claudeResult.humanSummary
  );
  const technicalDetail = generateTechnicalDetail(
    scanReport, behavioralProfile, claudeResult.intentAnalysis, claudeResult.findings
  );

  // Step 8: Combine all findings
  const allFindings: Array<ScanFinding | ReviewFinding> = [
    ...scanReport.findings,
    ...(behavioralProfile?.findings || []),
    ...claudeResult.findings,
  ];

  // Step 9: Build layer summaries
  const staticTopFindings = scanReport.findings.slice(0, 5).map(f => `[${f.severity}] ${f.title}`);
  const behavioralTopObs = behavioralProfile
    ? [
        ...(behavioralProfile.summary.networkTargets.length > 0 ? [`Network: ${behavioralProfile.summary.networkTargets.slice(0, 3).join(', ')}`] : []),
        ...(behavioralProfile.summary.processesSpawned.length > 0 ? [`Processes: ${behavioralProfile.summary.processesSpawned.slice(0, 3).join(', ')}`] : []),
        ...(behavioralProfile.summary.evasionAttempted ? ['Sandbox evasion attempted'] : []),
      ]
    : [];
  const claudeTopConcerns = claudeResult.findings
    .filter(f => f.severity === 'critical' || f.severity === 'high')
    .slice(0, 5)
    .map(f => f.title);

  return {
    id: verdictId,
    repoId: scanReport.repoId,
    repoUrl: scanReport.repoUrl,
    timestamp: Date.now(),
    pipelineDurationMs: Date.now() - pipelineStart,

    outcome,
    summary,
    technicalDetail,

    riskScore: combinedScore,
    riskLevel,

    layerAgreement,
    intentAnalysis: claudeResult.intentAnalysis,

    findings: allFindings,

    layers: {
      static: {
        riskScore: scanReport.riskScore,
        findingCount: scanReport.findings.length,
        topFindings: staticTopFindings,
      },
      behavioral: {
        riskContribution: behavioralProfile?.riskContribution ?? 0,
        sandboxIntegrity: behavioralProfile?.sandboxIntegrity ?? 'skipped',
        topObservations: behavioralTopObs,
      },
      claude: {
        findingCount: claudeResult.findings.length,
        confidence: claudeResult.overallConfidence,
        topConcerns: claudeTopConcerns,
      },
    },
  };
}

// ── Helpers ──────────────────────────────────────────────────────────

function validateRiskLevel(value: unknown): RiskLevel | null {
  if (typeof value === 'string' && ['low', 'medium', 'high', 'critical'].includes(value)) {
    return value as RiskLevel;
  }
  return null;
}

function validateReviewCategory(value: unknown): ReviewCategory | null {
  if (typeof value === 'string' && [
    'intent-analysis', 'adversarial-finding', 'documentation-discrepancy', 'capability-assessment'
  ].includes(value)) {
    return value as ReviewCategory;
  }
  return null;
}
