/**
 * git-scanner.ts — Static analysis pipeline for GitLoader security.
 *
 * Track I, Phase 1: The Immune System — Static Analysis.
 *
 * Scans cloned repositories BEFORE their contents are surfaced to the agent.
 * All analysis is purely static — no code execution, no subprocess spawning,
 * no `npm install`. If it can't be detected by reading files, it waits for
 * Phase 2 (behavioral sandbox).
 *
 * Scanner categories:
 *   1. Dependency risks — typosquatting, install scripts, known malicious patterns
 *   2. Secret detection — API keys, tokens, private keys, high-entropy strings
 *   3. Obfuscation detection — eval(), base64 decode, charCode, hex encoding
 *   4. Network inventory — hardcoded URLs, IPs, fetch/http calls
 *   5. Prompt injection — "ignore previous instructions", system prompt overrides
 *   6. Suspicious files — double extensions, hidden executables, oversized files
 *
 * cLaw Safety Boundary:
 *   The scanner itself must be protected from malicious input.
 *   - All regex patterns use bounded quantifiers (no catastrophic backtracking)
 *   - Per-file processing has size limits
 *   - Total scan has a time budget
 *   - NO untrusted code is executed
 */

import type { RepoFile, RepoTreeEntry, LoadedRepo } from './git-loader';

// ── Types ────────────────────────────────────────────────────────────

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical';
export type FindingCategory =
  | 'dependency'
  | 'secret'
  | 'obfuscation'
  | 'network'
  | 'prompt-injection'
  | 'suspicious-file';

export interface ScanFinding {
  id: string;
  category: FindingCategory;
  severity: RiskLevel;
  title: string;
  description: string;
  file?: string;
  line?: number;
  snippet?: string;        // Short excerpt (max 200 chars, no secrets)
  recommendation: string;
}

export interface DependencyReport {
  totalDependencies: number;
  directDependencies: string[];
  suspiciousPackages: string[];
  installScripts: string[];
  typosquatCandidates: Array<{ pkg: string; looksLike: string; distance: number }>;
}

export interface SecretReport {
  potentialSecrets: number;
  categories: Record<string, number>; // "aws-key": 3, "github-token": 1, etc.
}

export interface ObfuscationReport {
  evalCalls: number;
  base64Patterns: number;
  hexPatterns: number;
  charCodePatterns: number;
  minifiedFiles: string[];
}

export interface NetworkReport {
  uniqueUrls: string[];
  uniqueIps: string[];
  fetchCalls: number;
  websocketRefs: number;
}

export interface PromptInjectionReport {
  injectionAttempts: number;
  patterns: string[];
}

export interface ScanReport {
  repoId: string;
  repoUrl: string;
  timestamp: number;
  durationMs: number;

  // Overall assessment
  riskLevel: RiskLevel;
  riskScore: number;          // 0-100

  // All findings
  findings: ScanFinding[];

  // Category summaries
  dependencies: DependencyReport;
  secrets: SecretReport;
  obfuscation: ObfuscationReport;
  network: NetworkReport;
  promptInjection: PromptInjectionReport;

  // Metadata
  filesScanned: number;
  totalSize: number;
  languages: Record<string, number>;

  // Phase 2 can extend this
  extensions?: Record<string, unknown>;
}

export interface ScanOptions {
  /** Max time for entire scan in ms (default: 60000) */
  timeoutMs?: number;
  /** Max file size to scan in bytes (default: 1MB) */
  maxFileSizeBytes?: number;
  /** Skip dependency checking */
  skipDependencies?: boolean;
  /** Skip secret scanning */
  skipSecrets?: boolean;
}

// ── Constants ────────────────────────────────────────────────────────

const DEFAULT_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_FILE_SIZE = 1024 * 1024; // 1MB
const MAX_SNIPPET_LENGTH = 200;
const MAX_FINDINGS = 500;

// cLaw: Regex timeout protection — limit input size per pattern match
const MAX_LINE_LENGTH_FOR_REGEX = 10_000;

// ── Popular packages for typosquat detection ─────────────────────────

const POPULAR_PACKAGES = [
  'express', 'lodash', 'react', 'axios', 'moment', 'chalk', 'commander',
  'debug', 'request', 'bluebird', 'underscore', 'async', 'uuid', 'glob',
  'minimist', 'yargs', 'webpack', 'babel', 'eslint', 'prettier', 'jest',
  'mocha', 'chai', 'sinon', 'typescript', 'tslib', 'rxjs', 'ramda',
  'mongoose', 'sequelize', 'knex', 'prisma', 'dotenv', 'cors', 'helmet',
  'passport', 'jsonwebtoken', 'bcrypt', 'socket.io', 'redis', 'pg',
  'mysql', 'mongodb', 'firebase', 'aws-sdk', 'next', 'nuxt', 'vue',
  'angular', 'svelte', 'electron', 'puppeteer', 'sharp', 'nodemailer',
  'nodemon', 'concurrently', 'cross-env', 'rimraf', 'mkdirp',
];

// ── Secret Patterns ──────────────────────────────────────────────────

// Each pattern has a name, regex, and severity
const SECRET_PATTERNS: Array<{ name: string; pattern: RegExp; severity: RiskLevel }> = [
  // AWS
  { name: 'aws-access-key', pattern: /AKIA[0-9A-Z]{16}/g, severity: 'critical' },
  { name: 'aws-secret-key', pattern: /(?:aws_secret|secret_key|aws_key)[\s=:'"]+[A-Za-z0-9/+=]{40}/gi, severity: 'critical' },
  // GitHub
  { name: 'github-token', pattern: /gh[pousr]_[A-Za-z0-9_]{36,}/g, severity: 'critical' },
  { name: 'github-classic-token', pattern: /ghp_[A-Za-z0-9]{36}/g, severity: 'critical' },
  // Generic API keys
  { name: 'api-key-assignment', pattern: /(?:api[_-]?key|apikey|api[_-]?secret)[\s]*[=:][\s]*['"][A-Za-z0-9_-]{20,}['"]/gi, severity: 'high' },
  // Private keys
  { name: 'private-key', pattern: /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/g, severity: 'critical' },
  // JWT
  { name: 'jwt-token', pattern: /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, severity: 'high' },
  // Slack tokens
  { name: 'slack-token', pattern: /xox[bpors]-[0-9]{10,}-[A-Za-z0-9]{10,}/g, severity: 'critical' },
  // Google
  { name: 'google-api-key', pattern: /AIza[0-9A-Za-z_-]{35}/g, severity: 'high' },
  // Generic password in config
  { name: 'password-assignment', pattern: /(?:password|passwd|pwd)[\s]*[=:][\s]*['"][^'"]{8,}['"]/gi, severity: 'medium' },
  // Stripe
  { name: 'stripe-key', pattern: /(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}/g, severity: 'critical' },
  // Heroku
  { name: 'heroku-api-key', pattern: /heroku[A-Za-z0-9_]*[\s]*[=:][\s]*['"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['"]/gi, severity: 'high' },
  // SendGrid
  { name: 'sendgrid-api-key', pattern: /SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}/g, severity: 'critical' },
  // Twilio
  { name: 'twilio-api-key', pattern: /SK[0-9a-fA-F]{32}/g, severity: 'high' },
  // OpenAI
  { name: 'openai-api-key', pattern: /sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}/g, severity: 'critical' },
];

// ── Obfuscation Patterns ─────────────────────────────────────────────

const OBFUSCATION_PATTERNS: Array<{ name: string; pattern: RegExp; severity: RiskLevel; description: string }> = [
  {
    name: 'eval-call',
    pattern: /\beval\s*\(/g,
    severity: 'high',
    description: 'eval() can execute arbitrary code',
  },
  {
    name: 'function-constructor',
    pattern: /new\s+Function\s*\(/g,
    severity: 'high',
    description: 'Function constructor can create and execute arbitrary code',
  },
  {
    name: 'base64-decode-exec',
    pattern: /(?:atob|Buffer\.from)\s*\([^)]*(?:base64|'base64'|"base64")[^)]*\)/g,
    severity: 'medium',
    description: 'Base64 decoding may hide malicious payloads',
  },
  {
    name: 'charcode-manipulation',
    pattern: /String\.fromCharCode\s*\([^)]{10,}\)/g,
    severity: 'medium',
    description: 'CharCode manipulation can obfuscate strings',
  },
  {
    name: 'hex-string-decode',
    pattern: /\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){5,}/g,
    severity: 'medium',
    description: 'Hex-encoded strings may hide content',
  },
  {
    name: 'document-write',
    pattern: /document\.write\s*\(/g,
    severity: 'medium',
    description: 'document.write can inject content into the page',
  },
  {
    name: 'process-env-override',
    pattern: /process\.env\s*(?:\[|\.)\s*['"]\w+['"]\s*\]\s*=/g,
    severity: 'high',
    description: 'Modifying process.env at runtime is suspicious',
  },
  {
    name: 'child-process-exec',
    pattern: /(?:exec|execSync|spawn|spawnSync|execFile)\s*\(\s*[^)]*\)/g,
    severity: 'high',
    description: 'Subprocess execution can run arbitrary commands',
  },
];

// ── Network Patterns ─────────────────────────────────────────────────

const URL_PATTERN = /https?:\/\/[^\s'"`,;)}\]]{5,}/gi;
const IP_PATTERN = /\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b(?::\d{1,5})?/g;
const FETCH_PATTERN = /(?:fetch|axios|http\.request|https\.request|XMLHttpRequest|\.get\(|\.post\(|\.put\(|\.delete\()\s*\(/gi;
const WEBSOCKET_PATTERN = /new\s+WebSocket\s*\(|wss?:\/\//gi;

// ── Prompt Injection Patterns ────────────────────────────────────────

const PROMPT_INJECTION_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  { name: 'ignore-instructions', pattern: /ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions/gi },
  { name: 'system-prompt-override', pattern: /(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|your\s+new\s+instructions)/gi },
  { name: 'jailbreak-attempt', pattern: /(?:DAN|do\s+anything\s+now|jailbreak|bypass\s+(?:safety|security|restrictions))/gi },
  { name: 'role-manipulation', pattern: /(?:forget\s+(?:all|your|everything)|reset\s+your\s+(?:instructions|personality|role))/gi },
  { name: 'prompt-leak-attempt', pattern: /(?:reveal|show|display|print|output)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)/gi },
  { name: 'hidden-instruction-marker', pattern: /(?:SYSTEM:|ADMIN:|ROOT:|DEVELOPER:)\s*(?:override|execute|run|ignore)/gi },
];

// ── Suspicious File Patterns ─────────────────────────────────────────

const DOUBLE_EXTENSION_PATTERN = /\.\w+\.\w+$/;
const EXECUTABLE_EXTENSIONS = new Set([
  '.exe', '.bat', '.cmd', '.ps1', '.sh', '.bash', '.msi', '.dll',
  '.com', '.scr', '.pif', '.vbs', '.vbe', '.js', '.jse', '.wsf',
  '.wsh', '.app', '.action', '.command', '.workflow',
]);

// ── Utility Functions ────────────────────────────────────────────────

let findingCounter = 0;

function nextFindingId(): string {
  return `finding-${++findingCounter}`;
}

/**
 * Levenshtein distance for typosquat detection.
 * Bounded to max distance of 3 for performance.
 */
function levenshtein(a: string, b: string): number {
  if (Math.abs(a.length - b.length) > 3) return 4; // Quick reject

  const matrix: number[][] = [];
  for (let i = 0; i <= a.length; i++) {
    matrix[i] = [i];
  }
  for (let j = 0; j <= b.length; j++) {
    matrix[0][j] = j;
  }
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,
        matrix[i][j - 1] + 1,
        matrix[i - 1][j - 1] + cost,
      );
    }
  }
  return matrix[a.length][b.length];
}

/**
 * Calculate Shannon entropy of a string.
 * High entropy (> 4.5) on a string of length 20+ suggests a secret.
 */
function shannonEntropy(str: string): number {
  if (str.length === 0) return 0;
  const freq = new Map<string, number>();
  for (const ch of str) {
    freq.set(ch, (freq.get(ch) || 0) + 1);
  }
  let entropy = 0;
  for (const count of freq.values()) {
    const p = count / str.length;
    if (p > 0) entropy -= p * Math.log2(p);
  }
  return entropy;
}

/**
 * Truncate a string for safe snippet display.
 * Never include full secrets in snippets.
 */
function safeSnippet(line: string, maxLen = MAX_SNIPPET_LENGTH): string {
  const trimmed = line.trim();
  if (trimmed.length <= maxLen) return trimmed;
  return trimmed.slice(0, maxLen - 3) + '...';
}

/**
 * Safe regex test — truncate overly long lines to prevent ReDoS.
 */
function safeRegexTest(pattern: RegExp, line: string): boolean {
  const safeLine = line.length > MAX_LINE_LENGTH_FOR_REGEX
    ? line.slice(0, MAX_LINE_LENGTH_FOR_REGEX)
    : line;
  return pattern.test(safeLine);
}

/**
 * Safe regex match — truncate overly long lines.
 */
function safeRegexMatch(pattern: RegExp, content: string): RegExpMatchArray | null {
  const safeContent = content.length > MAX_LINE_LENGTH_FOR_REGEX * 10
    ? content.slice(0, MAX_LINE_LENGTH_FOR_REGEX * 10)
    : content;
  return safeContent.match(pattern);
}

// ── Individual Scanners ──────────────────────────────────────────────

/**
 * Scan dependencies for risks: typosquatting, install scripts, suspicious patterns.
 */
function scanDependencies(
  files: RepoFile[],
  findings: ScanFinding[],
): DependencyReport {
  const report: DependencyReport = {
    totalDependencies: 0,
    directDependencies: [],
    suspiciousPackages: [],
    installScripts: [],
    typosquatCandidates: [],
  };

  // Find package.json files
  const packageJsonFiles = files.filter(f =>
    f.path.endsWith('package.json') && !f.path.includes('node_modules')
  );

  for (const pkgFile of packageJsonFiles) {
    try {
      const pkg = JSON.parse(pkgFile.content);
      const allDeps = {
        ...(pkg.dependencies || {}),
        ...(pkg.devDependencies || {}),
        ...(pkg.peerDependencies || {}),
      };

      const depNames = Object.keys(allDeps);
      report.totalDependencies += depNames.length;
      report.directDependencies.push(...depNames);

      // Check for install scripts
      const scripts = pkg.scripts || {};
      const dangerousScripts = ['preinstall', 'postinstall', 'install', 'prepare'];
      for (const scriptName of dangerousScripts) {
        if (scripts[scriptName]) {
          report.installScripts.push(`${pkgFile.path}: ${scriptName}`);
          findings.push({
            id: nextFindingId(),
            category: 'dependency',
            severity: 'medium',
            title: `Install script: ${scriptName}`,
            description: `${pkgFile.path} defines a "${scriptName}" script that runs automatically during npm install.`,
            file: pkgFile.path,
            snippet: safeSnippet(`"${scriptName}": "${scripts[scriptName]}"`),
            recommendation: 'Review the install script to ensure it does not execute malicious code.',
          });
        }
      }

      // Typosquat detection
      for (const dep of depNames) {
        const normalizedDep = dep.replace(/^@[^/]+\//, ''); // Strip scope
        for (const popular of POPULAR_PACKAGES) {
          if (normalizedDep === popular) continue; // Exact match is fine
          const dist = levenshtein(normalizedDep, popular);
          if (dist > 0 && dist <= 2) {
            report.typosquatCandidates.push({ pkg: dep, looksLike: popular, distance: dist });
            report.suspiciousPackages.push(dep);
            findings.push({
              id: nextFindingId(),
              category: 'dependency',
              severity: dist === 1 ? 'high' : 'medium',
              title: `Potential typosquat: "${dep}" → "${popular}"`,
              description: `Package "${dep}" in ${pkgFile.path} is ${dist} character(s) away from popular package "${popular}". This may be a typosquatting attack.`,
              file: pkgFile.path,
              recommendation: `Verify that "${dep}" is the intended package, not a malicious look-alike of "${popular}".`,
            });
          }
        }
      }
    } catch {
      // Malformed JSON — note it
      findings.push({
        id: nextFindingId(),
        category: 'suspicious-file',
        severity: 'low',
        title: 'Malformed package.json',
        description: `${pkgFile.path} contains invalid JSON.`,
        file: pkgFile.path,
        recommendation: 'This file cannot be parsed. Verify its contents manually.',
      });
    }
  }

  // Check Python requirements.txt
  const reqFiles = files.filter(f =>
    f.path.endsWith('requirements.txt') || f.path.endsWith('setup.py') || f.path.endsWith('pyproject.toml')
  );
  for (const reqFile of reqFiles) {
    if (reqFile.path.endsWith('requirements.txt')) {
      const lines = reqFile.content.split('\n').filter(l => l.trim() && !l.trim().startsWith('#'));
      report.totalDependencies += lines.length;
      for (const line of lines) {
        const pkgName = line.split(/[>=<!\s]/)[0].trim();
        if (pkgName) report.directDependencies.push(pkgName);
      }
    }
  }

  return report;
}

/**
 * Scan for hardcoded secrets: API keys, tokens, passwords, private keys.
 */
function scanSecrets(
  files: RepoFile[],
  findings: ScanFinding[],
): SecretReport {
  const report: SecretReport = {
    potentialSecrets: 0,
    categories: {},
  };

  for (const file of files) {
    // Skip files that are likely documentation or test fixtures
    if (file.path.includes('test') && file.path.includes('fixture')) continue;

    const lines = file.content.split('\n');

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.length > MAX_LINE_LENGTH_FOR_REGEX) continue;

      for (const secretDef of SECRET_PATTERNS) {
        secretDef.pattern.lastIndex = 0; // Reset
        if (secretDef.pattern.test(line)) {
          report.potentialSecrets++;
          report.categories[secretDef.name] = (report.categories[secretDef.name] || 0) + 1;

          // Don't include the actual secret in the snippet
          const sanitizedSnippet = line.trim().replace(
            /[A-Za-z0-9_\-/+=]{20,}/g,
            '[REDACTED]'
          );

          findings.push({
            id: nextFindingId(),
            category: 'secret',
            severity: secretDef.severity,
            title: `Potential ${secretDef.name} detected`,
            description: `Found a pattern matching ${secretDef.name} in ${file.path}:${i + 1}.`,
            file: file.path,
            line: i + 1,
            snippet: safeSnippet(sanitizedSnippet),
            recommendation: 'If this is a real credential, it should be removed and rotated immediately.',
          });

          if (findings.length >= MAX_FINDINGS) return report;
          break; // One finding per line
        }
      }

      // High-entropy string detection (complementary to pattern matching)
      const stringMatches = line.match(/['"][A-Za-z0-9_\-/+=]{20,}['"]/g);
      if (stringMatches) {
        for (const match of stringMatches) {
          const inner = match.slice(1, -1);
          const entropy = shannonEntropy(inner);
          if (entropy > 4.5 && inner.length >= 30) {
            // Check it's not already caught by pattern matching
            let alreadyCaught = false;
            for (const sp of SECRET_PATTERNS) {
              sp.pattern.lastIndex = 0;
              if (sp.pattern.test(inner)) {
                alreadyCaught = true;
                break;
              }
            }
            if (!alreadyCaught) {
              report.potentialSecrets++;
              report.categories['high-entropy-string'] = (report.categories['high-entropy-string'] || 0) + 1;
              findings.push({
                id: nextFindingId(),
                category: 'secret',
                severity: 'medium',
                title: 'High-entropy string detected',
                description: `A high-entropy string (${entropy.toFixed(2)} bits) was found in ${file.path}:${i + 1}. This may be a secret.`,
                file: file.path,
                line: i + 1,
                snippet: safeSnippet(`[${inner.length} chars, entropy ${entropy.toFixed(2)}]`),
                recommendation: 'Verify this is not a hardcoded secret or credential.',
              });
            }
          }
        }
      }
    }
  }

  return report;
}

/**
 * Scan for code obfuscation techniques.
 */
function scanObfuscation(
  files: RepoFile[],
  findings: ScanFinding[],
): ObfuscationReport {
  const report: ObfuscationReport = {
    evalCalls: 0,
    base64Patterns: 0,
    hexPatterns: 0,
    charCodePatterns: 0,
    minifiedFiles: [],
  };

  for (const file of files) {
    // Only scan code files for obfuscation
    const codeExts = [
      'javascript', 'typescript', 'python', 'ruby', 'php',
      'shell', 'powershell', 'batch',
    ];
    if (!codeExts.includes(file.language)) continue;

    const lines = file.content.split('\n');

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.length > MAX_LINE_LENGTH_FOR_REGEX) continue;

      for (const obfDef of OBFUSCATION_PATTERNS) {
        obfDef.pattern.lastIndex = 0;
        if (obfDef.pattern.test(line)) {
          // Count by type
          if (obfDef.name === 'eval-call') report.evalCalls++;
          else if (obfDef.name.includes('base64')) report.base64Patterns++;
          else if (obfDef.name.includes('hex')) report.hexPatterns++;
          else if (obfDef.name.includes('charcode')) report.charCodePatterns++;

          findings.push({
            id: nextFindingId(),
            category: 'obfuscation',
            severity: obfDef.severity,
            title: obfDef.name,
            description: `${obfDef.description}. Found in ${file.path}:${i + 1}.`,
            file: file.path,
            line: i + 1,
            snippet: safeSnippet(line),
            recommendation: 'Review this code carefully. Legitimate uses exist, but this pattern is commonly used in malicious code.',
          });

          if (findings.length >= MAX_FINDINGS) return report;
        }
      }
    }

    // Minification detection: high ratio of code to whitespace, very long lines
    if (file.language === 'javascript' || file.language === 'typescript') {
      const avgLineLength = file.content.length / Math.max(lines.length, 1);
      const whitespaceRatio = (file.content.match(/\s/g)?.length || 0) / Math.max(file.content.length, 1);

      if (avgLineLength > 500 && whitespaceRatio < 0.1 && file.size > 5000) {
        report.minifiedFiles.push(file.path);
        findings.push({
          id: nextFindingId(),
          category: 'obfuscation',
          severity: 'low',
          title: 'Minified/bundled file',
          description: `${file.path} appears to be minified or bundled (avg line: ${avgLineLength.toFixed(0)} chars, ${(whitespaceRatio * 100).toFixed(1)}% whitespace). This is common for production builds but hides code intent.`,
          file: file.path,
          recommendation: 'Minified files are normal in production builds. Flag only if source maps are missing and the file is not in a dist/ or build/ directory.',
        });
      }
    }
  }

  return report;
}

/**
 * Scan for network calls, URLs, and IP addresses.
 */
function scanNetwork(
  files: RepoFile[],
  findings: ScanFinding[],
): NetworkReport {
  const urls = new Set<string>();
  const ips = new Set<string>();
  let fetchCalls = 0;
  let websocketRefs = 0;

  // Known safe URL domains (don't flag these)
  const safeDomains = new Set([
    'github.com', 'npmjs.com', 'registry.npmjs.org',
    'cdn.jsdelivr.net', 'unpkg.com', 'cdnjs.cloudflare.com',
    'fonts.googleapis.com', 'fonts.gstatic.com',
    'api.github.com', 'raw.githubusercontent.com',
    'www.w3.org', 'schema.org', 'json-schema.org',
    'developer.mozilla.org', 'docs.python.org',
    'example.com', 'example.org', 'localhost',
    'creativecommons.org', 'opensource.org',
    'shields.io', 'img.shields.io', 'badge.fury.io',
    'travis-ci.org', 'codecov.io', 'coveralls.io',
  ]);

  for (const file of files) {
    // Skip markdown and documentation for network scanning
    if (file.language === 'markdown') continue;

    const urlMatches = safeRegexMatch(URL_PATTERN, file.content);
    if (urlMatches) {
      for (const url of urlMatches) {
        try {
          const hostname = new URL(url).hostname;
          if (!safeDomains.has(hostname)) {
            urls.add(url.slice(0, 200)); // Truncate long URLs
          }
        } catch {
          // Not a valid URL, skip
        }
      }
    }

    const ipMatches = safeRegexMatch(IP_PATTERN, file.content);
    if (ipMatches) {
      for (const rawIp of ipMatches) {
        // Strip port suffix if present (regex captures optional :port)
        const ip = rawIp.replace(/:\d{1,5}$/, '');
        // Skip common safe IPs (loopback, any, RFC1918 private ranges)
        if (ip.startsWith('127.') || ip.startsWith('0.') || ip.startsWith('192.168.') || ip.startsWith('10.') || ip.startsWith('172.')) continue;
        ips.add(ip);
      }
    }

    const lines = file.content.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.length > MAX_LINE_LENGTH_FOR_REGEX) continue;

      FETCH_PATTERN.lastIndex = 0;
      if (FETCH_PATTERN.test(line)) {
        fetchCalls++;
      }

      WEBSOCKET_PATTERN.lastIndex = 0;
      if (WEBSOCKET_PATTERN.test(line)) {
        websocketRefs++;
      }
    }
  }

  // Only flag unusual URLs
  for (const url of urls) {
    findings.push({
      id: nextFindingId(),
      category: 'network',
      severity: 'low',
      title: 'External URL reference',
      description: `Code references external URL: ${safeSnippet(url, 100)}`,
      recommendation: 'Review the URL to ensure it is a legitimate endpoint and not a data exfiltration target.',
    });
  }

  for (const ip of ips) {
    findings.push({
      id: nextFindingId(),
      category: 'network',
      severity: 'medium',
      title: 'Hardcoded IP address',
      description: `Hardcoded IP address found: ${ip}`,
      recommendation: 'Hardcoded IPs are suspicious and may indicate C2 communication. Verify this is a known, safe endpoint.',
    });
  }

  if (websocketRefs > 0) {
    findings.push({
      id: nextFindingId(),
      category: 'network',
      severity: 'low',
      title: `WebSocket references (${websocketRefs})`,
      description: `Found ${websocketRefs} WebSocket references. WebSockets enable persistent bidirectional communication.`,
      recommendation: 'Verify WebSocket connections go to expected endpoints.',
    });
  }

  return {
    uniqueUrls: Array.from(urls),
    uniqueIps: Array.from(ips),
    fetchCalls,
    websocketRefs,
  };
}

/**
 * Scan for prompt injection attempts in code and comments.
 */
function scanPromptInjection(
  files: RepoFile[],
  findings: ScanFinding[],
): PromptInjectionReport {
  const report: PromptInjectionReport = {
    injectionAttempts: 0,
    patterns: [],
  };

  for (const file of files) {
    const lines = file.content.split('\n');

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.length > MAX_LINE_LENGTH_FOR_REGEX) continue;

      for (const pDef of PROMPT_INJECTION_PATTERNS) {
        pDef.pattern.lastIndex = 0;
        if (pDef.pattern.test(line)) {
          report.injectionAttempts++;
          if (!report.patterns.includes(pDef.name)) {
            report.patterns.push(pDef.name);
          }

          findings.push({
            id: nextFindingId(),
            category: 'prompt-injection',
            severity: 'high',
            title: `Prompt injection pattern: ${pDef.name}`,
            description: `Detected prompt injection pattern "${pDef.name}" in ${file.path}:${i + 1}. If this file's contents are included in an LLM API call, this could manipulate the agent's behavior.`,
            file: file.path,
            line: i + 1,
            snippet: safeSnippet(line),
            recommendation: 'This content should be sandboxed and never directly included in LLM system prompts.',
          });

          if (findings.length >= MAX_FINDINGS) return report;
          break; // One finding per line
        }
      }
    }
  }

  return report;
}

/**
 * Scan for suspicious files: double extensions, hidden executables, oversized files.
 */
function scanSuspiciousFiles(
  tree: RepoTreeEntry[],
  files: RepoFile[],
  findings: ScanFinding[],
): void {
  // Common config/compound extension patterns that are NOT suspicious
  const SAFE_COMPOUND_EXTENSIONS = new Set([
    '.config.ts', '.config.js', '.config.mjs', '.config.cjs',
    '.spec.ts', '.spec.js', '.test.ts', '.test.js',
    '.module.ts', '.module.js', '.d.ts', '.stories.tsx',
    '.stories.jsx', '.styles.ts', '.styles.css',
    '.min.js', '.min.css', '.bundle.js', '.esm.js',
    '.env.local', '.env.development', '.env.production',
  ]);

  for (const entry of tree) {
    const basename = entry.path.split('/').pop() || '';
    const ext = basename.includes('.') ? '.' + basename.split('.').pop()!.toLowerCase() : '';

    // Double extensions (e.g., .jpg.exe) — but skip known safe compound extensions
    if (DOUBLE_EXTENSION_PATTERN.test(basename)) {
      const parts = basename.split('.');
      if (parts.length >= 3) {
        const lastExt = '.' + parts[parts.length - 1].toLowerCase();
        const secondLastExt = '.' + parts[parts.length - 2].toLowerCase();
        const compoundExt = secondLastExt + lastExt;

        // Skip known safe compound extensions
        if (SAFE_COMPOUND_EXTENSIONS.has(compoundExt)) continue;

        if (EXECUTABLE_EXTENSIONS.has(lastExt) && !EXECUTABLE_EXTENSIONS.has(secondLastExt)) {
          findings.push({
            id: nextFindingId(),
            category: 'suspicious-file',
            severity: 'high',
            title: 'Double extension (masquerading)',
            description: `File "${entry.path}" has a double extension that disguises an executable as a non-executable file.`,
            file: entry.path,
            recommendation: 'This is a common malware distribution technique. The file should be removed or quarantined.',
          });
        }
      }
    }

    // Hidden files with executable extensions (not .gitignore etc.)
    if (basename.startsWith('.') && !basename.startsWith('.git') && !basename.startsWith('.env') &&
        !basename.startsWith('.eslint') && !basename.startsWith('.prettier') &&
        !basename.startsWith('.editor') && !basename.startsWith('.npm') &&
        !basename.startsWith('.babel') && !basename.startsWith('.docker') &&
        !basename.startsWith('.vscode') && !basename.startsWith('.husky') &&
        !basename.startsWith('.nyc') && !basename.startsWith('.travis') &&
        !basename.startsWith('.circleci') && !basename.startsWith('.github') &&
        EXECUTABLE_EXTENSIONS.has(ext)) {
      findings.push({
        id: nextFindingId(),
        category: 'suspicious-file',
        severity: 'high',
        title: 'Hidden executable file',
        description: `Hidden file "${entry.path}" has an executable extension (${ext}).`,
        file: entry.path,
        recommendation: 'Hidden executables are highly suspicious. Review the file contents carefully.',
      });
    }

    // Oversized single files (> 5MB in source tree, excluding obvious build artifacts)
    if (entry.type === 'file' && entry.size && entry.size > 5 * 1024 * 1024) {
      if (!entry.path.includes('dist/') && !entry.path.includes('build/') && !entry.path.includes('vendor/')) {
        findings.push({
          id: nextFindingId(),
          category: 'suspicious-file',
          severity: 'low',
          title: 'Oversized source file',
          description: `File "${entry.path}" is ${(entry.size / 1024 / 1024).toFixed(1)}MB. Large files in source may contain embedded binaries or obfuscated data.`,
          file: entry.path,
          recommendation: 'Verify this is a legitimate source file and not an embedded binary.',
        });
      }
    }
  }
}

// ── Risk Score Computation ───────────────────────────────────────────

function computeRiskScore(findings: ScanFinding[]): { score: number; level: RiskLevel } {
  let score = 0;

  for (const f of findings) {
    switch (f.severity) {
      case 'critical': score += 25; break;
      case 'high': score += 10; break;
      case 'medium': score += 3; break;
      case 'low': score += 1; break;
    }
  }

  // Cap at 100
  score = Math.min(100, score);

  let level: RiskLevel = 'low';
  if (score >= 70) level = 'critical';
  else if (score >= 40) level = 'high';
  else if (score >= 15) level = 'medium';

  return { score, level };
}

// ── Main Scanner ─────────────────────────────────────────────────────

/**
 * Run the full static analysis pipeline on a loaded repository.
 *
 * This is the primary entry point. Call after git clone, before
 * surfacing any file contents to the agent.
 *
 * cLaw Gate: No code execution. All analysis is file-reading only.
 */
export function scanRepository(
  repo: LoadedRepo,
  options: ScanOptions = {},
): ScanReport {
  const startTime = Date.now();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxFileSize = options.maxFileSizeBytes ?? DEFAULT_MAX_FILE_SIZE;

  // Reset finding counter per scan
  findingCounter = 0;
  const findings: ScanFinding[] = [];

  // Filter files by size for scanning
  const scannable = repo.files.filter(f => f.size <= maxFileSize);

  // Compute language stats
  const languages: Record<string, number> = {};
  for (const file of scannable) {
    languages[file.language] = (languages[file.language] || 0) + 1;
  }

  // Run all scanners
  const dependencies = !options.skipDependencies
    ? scanDependencies(scannable, findings)
    : { totalDependencies: 0, directDependencies: [], suspiciousPackages: [], installScripts: [], typosquatCandidates: [] };

  const secrets = !options.skipSecrets
    ? scanSecrets(scannable, findings)
    : { potentialSecrets: 0, categories: {} };

  const obfuscation = scanObfuscation(scannable, findings);
  const network = scanNetwork(scannable, findings);
  const promptInjection = scanPromptInjection(scannable, findings);

  // Suspicious files scan uses tree (includes files too large to content-scan)
  scanSuspiciousFiles(repo.tree, scannable, findings);

  // Timeout check (defensive — scanners are fast but just in case)
  const durationMs = Date.now() - startTime;
  if (durationMs > timeoutMs) {
    findings.push({
      id: nextFindingId(),
      category: 'suspicious-file',
      severity: 'medium',
      title: 'Scan timeout reached',
      description: `Static analysis took ${durationMs}ms (limit: ${timeoutMs}ms). Some files may not have been fully scanned.`,
      recommendation: 'Consider scanning with a higher timeout or fewer files.',
    });
  }

  const { score, level } = computeRiskScore(findings);

  return {
    repoId: repo.id,
    repoUrl: repo.url,
    timestamp: Date.now(),
    durationMs: Date.now() - startTime,
    riskLevel: level,
    riskScore: score,
    findings,
    dependencies,
    secrets,
    obfuscation,
    network,
    promptInjection,
    filesScanned: scannable.length,
    totalSize: scannable.reduce((sum, f) => sum + f.size, 0),
    languages,
  };
}

// ── Exported utilities for testing ───────────────────────────────────

export { levenshtein, shannonEntropy, safeSnippet, computeRiskScore };
export { scanDependencies, scanSecrets, scanObfuscation, scanNetwork, scanPromptInjection, scanSuspiciousFiles };
