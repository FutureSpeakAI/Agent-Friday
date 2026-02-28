/**
 * git-analyzer.ts — Intelligent Code Analysis Engine.
 *
 * Track II, Phase 1: The Absorber — Intelligent Code Analysis.
 *
 * Analyzes a loaded repository to produce a CapabilityManifest:
 *   1. Detect ecosystem and repo type (library, CLI, API, etc.)
 *   2. Identify entry points using language-specific heuristics
 *   3. Extract capabilities via Claude-powered code comprehension
 *   4. Score confidence based on types, tests, docs, and consistency
 *   5. Produce a structured manifest for the Adaptation Engine (Phase 2)
 *
 * cLaw Safety Boundary:
 *   - Code analysis is READ-ONLY. No code execution, no npm install, no eval.
 *   - Analysis sends file contents to Claude for comprehension — nothing else runs.
 *   - All file reading uses the already-loaded LoadedRepo (no filesystem access).
 */

import type { LoadedRepo, RepoFile } from './git-loader';
import { runClaudeToolLoop } from './server';
import {
  type CapabilityManifest,
  type Capability,
  type EntryPoint,
  type EntryPointReason,
  type ExportedSymbol,
  type Dependency,
  type ConfigField,
  type ConfidenceSignal,
  type AnalysisConfidence,
  type ManifestMetadata,
  type RepoType,
  type CapabilityCategory,
  type JSONSchemaObject,
  type OutputSchema,
  type SourceLocation,
  computeConfidence,
  explainConfidence,
} from './capability-manifest';

// ── Types ────────────────────────────────────────────────────────────

export interface AnalysisOptions {
  /** Max files to send to Claude for capability extraction (default: 25) */
  maxFilesForClaude: number;
  /** Max characters of file content per Claude call (default: 50000) */
  maxContentChars: number;
  /** Whether to analyze README for additional context (default: true) */
  analyzeReadme: boolean;
  /** Whether to extract dependencies (default: true) */
  extractDependencies: boolean;
}

const DEFAULT_OPTIONS: AnalysisOptions = {
  maxFilesForClaude: 25,
  maxContentChars: 50_000,
  analyzeReadme: true,
  extractDependencies: true,
};

interface ClaudeAnalysisResult {
  summary: string;
  repoType: RepoType;
  capabilities: ClaudeCapability[];
  configFields: ConfigField[];
}

interface ClaudeCapability {
  name: string;
  description: string;
  category: CapabilityCategory;
  inputSchema: JSONSchemaObject;
  outputSchema: OutputSchema;
  sourceFile: string;
  exportedName: string;
  startLine: number;
  endLine: number;
  isDefaultExport: boolean;
  adaptationComplexity: 'trivial' | 'simple' | 'moderate' | 'complex' | 'infeasible';
  adaptationNotes: string;
}

// ── Constants ────────────────────────────────────────────────────────

const ENTRY_POINT_PATTERNS: Record<string, { pattern: RegExp; reason: EntryPointReason }[]> = {
  typescript: [
    { pattern: /^index\.(ts|tsx)$/, reason: 'index-file' },
    { pattern: /^src\/index\.(ts|tsx)$/, reason: 'index-file' },
    { pattern: /^lib\/index\.(ts|tsx)$/, reason: 'index-file' },
    { pattern: /^(api|public|exports?)\.(ts|tsx)$/, reason: 'public-api-pattern' },
    { pattern: /^src\/(api|public|exports?)\.(ts|tsx)$/, reason: 'public-api-pattern' },
  ],
  javascript: [
    { pattern: /^index\.(js|mjs|cjs)$/, reason: 'index-file' },
    { pattern: /^src\/index\.(js|mjs|cjs)$/, reason: 'index-file' },
    { pattern: /^lib\/index\.(js|mjs|cjs)$/, reason: 'index-file' },
  ],
  python: [
    { pattern: /__init__\.py$/, reason: 'python-init' },
    { pattern: /__main__\.py$/, reason: 'python-main' },
    { pattern: /^(main|app|cli)\.py$/, reason: 'cli-entrypoint' },
    { pattern: /^src\/[^/]+\/__init__\.py$/, reason: 'python-init' },
  ],
  rust: [
    { pattern: /^src\/lib\.rs$/, reason: 'cargo-lib' },
    { pattern: /^src\/main\.rs$/, reason: 'cargo-bin' },
    { pattern: /^src\/bin\/[^/]+\.rs$/, reason: 'cargo-bin' },
  ],
  go: [
    { pattern: /^main\.go$/, reason: 'go-main' },
    { pattern: /^cmd\/[^/]+\/main\.go$/, reason: 'go-main' },
  ],
};

const CONFIG_FILE_PATTERNS = [
  'package.json', 'setup.py', 'setup.cfg', 'pyproject.toml',
  'Cargo.toml', 'go.mod', 'Gemfile', 'requirements.txt',
  'pom.xml', 'build.gradle', 'build.gradle.kts',
];

const TEST_PATTERNS = [
  /\btest[s]?\b/i, /\bspec[s]?\b/i, /\b__tests__\b/,
  /\.test\.[jt]sx?$/, /\.spec\.[jt]sx?$/, /test_.*\.py$/,
  /.*_test\.go$/, /.*_test\.rs$/,
];

const DOC_PATTERNS = [
  /^readme/i, /^docs?\//i, /^documentation\//i,
  /\.md$/i, /\.rst$/i, /^wiki\//i,
  /^changelog/i, /^contributing/i,
];

// ── Main Analysis Pipeline ───────────────────────────────────────────

/**
 * Analyze a loaded repository and produce a CapabilityManifest.
 * This is the main entry point for Phase 1.
 *
 * The pipeline:
 *   1. Detect ecosystem, language distribution, repo structure
 *   2. Identify entry points via heuristic + manifest analysis
 *   3. Select priority files for Claude analysis
 *   4. Send to Claude for capability extraction
 *   5. Compute confidence scores
 *   6. Assemble final manifest
 */
export async function analyzeRepository(
  repo: LoadedRepo,
  options: Partial<AnalysisOptions> = {}
): Promise<CapabilityManifest> {
  const opts = { ...DEFAULT_OPTIONS, ...options };
  const startTime = Date.now();

  // Step 1: Structural analysis (no Claude needed)
  const ecosystem = detectEcosystem(repo);
  const languageDistribution = computeLanguageDistribution(repo);
  const primaryLanguage = languageDistribution[0]?.[0] || 'unknown';
  const metadata = buildMetadata(repo);

  // Step 2: Identify entry points
  const entryPoints = identifyEntryPoints(repo, ecosystem);

  // Step 3: Extract dependencies
  const dependencies = opts.extractDependencies
    ? extractDependencies(repo, ecosystem)
    : [];

  // Step 4: Select files for Claude analysis
  const selectedFiles = selectFilesForAnalysis(repo, entryPoints, opts);

  // Step 5: Claude-powered capability extraction
  const readme = opts.analyzeReadme ? getReadmeContent(repo) : null;
  const claudeResult = await extractCapabilitiesWithClaude(
    selectedFiles, entryPoints, ecosystem, primaryLanguage, readme, opts
  );

  // Step 6: Build capabilities from Claude output
  const capabilities = buildCapabilities(claudeResult.capabilities, repo);

  // Step 7: Compute confidence
  const confidenceSignals = computeConfidenceSignals(
    repo, entryPoints, capabilities, { ...metadata, claudeCalls: 0 }
  );
  const confidence: AnalysisConfidence = {
    overall: computeConfidence(confidenceSignals),
    signals: confidenceSignals,
    explanation: explainConfidence(confidenceSignals),
  };

  // Step 8: Assemble manifest
  const manifest: CapabilityManifest = {
    id: `manifest-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    repoId: repo.id,
    repoName: repo.name,
    analyzedAt: Date.now(),
    analysisDurationMs: Date.now() - startTime,
    summary: claudeResult.summary || `${repo.name}: ${repo.description}`,
    primaryLanguage,
    languages: languageDistribution.map(([lang]) => lang),
    repoType: claudeResult.repoType || detectRepoType(repo, ecosystem),
    ecosystem,
    capabilities,
    entryPoints,
    dependencies,
    configSchema: claudeResult.configFields.length > 0 ? claudeResult.configFields : null,
    confidence,
    metadata: {
      ...metadata,
      claudeCalls: 1, // One main extraction call
    },
  };

  return manifest;
}

// ── Ecosystem Detection ──────────────────────────────────────────────

/**
 * Detect the package ecosystem from config files.
 */
export function detectEcosystem(repo: LoadedRepo): string | null {
  const filePaths = new Set(repo.files.map(f => f.path));

  if (filePaths.has('package.json')) return 'npm';
  if (filePaths.has('pyproject.toml') || filePaths.has('setup.py') || filePaths.has('setup.cfg')) return 'pip';
  if (filePaths.has('Cargo.toml')) return 'cargo';
  if (filePaths.has('go.mod')) return 'go-modules';
  if (filePaths.has('Gemfile')) return 'rubygems';
  if (filePaths.has('pom.xml') || filePaths.has('build.gradle') || filePaths.has('build.gradle.kts')) return 'maven';
  if (filePaths.has('composer.json')) return 'composer';
  if (filePaths.has('Package.swift')) return 'swift-pm';
  if (filePaths.has('mix.exs')) return 'hex';

  return null;
}

// ── Language Distribution ────────────────────────────────────────────

/**
 * Compute language distribution sorted by total size.
 */
export function computeLanguageDistribution(repo: LoadedRepo): [string, number][] {
  const langSize: Record<string, number> = {};

  for (const file of repo.files) {
    if (file.language && file.language !== 'text' && file.language !== 'unknown') {
      langSize[file.language] = (langSize[file.language] || 0) + file.size;
    }
  }

  return Object.entries(langSize).sort((a, b) => b[1] - a[1]);
}

// ── Entry Point Detection ────────────────────────────────────────────

/**
 * Identify entry points in the repository using language-specific heuristics.
 */
export function identifyEntryPoints(repo: LoadedRepo, ecosystem: string | null): EntryPoint[] {
  const entryPoints: EntryPoint[] = [];
  const seen = new Set<string>();

  // 1. Package manifest entry points (highest confidence)
  const manifestEntries = detectManifestEntryPoints(repo, ecosystem);
  for (const ep of manifestEntries) {
    if (!seen.has(ep.filePath)) {
      entryPoints.push(ep);
      seen.add(ep.filePath);
    }
  }

  // 2. Language-specific heuristic patterns
  for (const file of repo.files) {
    if (seen.has(file.path)) continue;

    for (const [_lang, patterns] of Object.entries(ENTRY_POINT_PATTERNS)) {
      for (const { pattern, reason } of patterns) {
        if (pattern.test(file.path)) {
          entryPoints.push({
            filePath: file.path,
            reason,
            exports: extractExports(file),
            confidence: 0.7,
          });
          seen.add(file.path);
          break;
        }
      }
      if (seen.has(file.path)) break;
    }
  }

  // 3. CLI entrypoint detection (shebang lines)
  for (const file of repo.files) {
    if (seen.has(file.path)) continue;
    if (file.content.startsWith('#!/')) {
      entryPoints.push({
        filePath: file.path,
        reason: 'cli-entrypoint',
        exports: extractExports(file),
        confidence: 0.6,
      });
      seen.add(file.path);
    }
  }

  return entryPoints;
}

/**
 * Detect entry points declared in package manifests.
 */
export function detectManifestEntryPoints(
  repo: LoadedRepo,
  ecosystem: string | null
): EntryPoint[] {
  const entries: EntryPoint[] = [];

  if (ecosystem === 'npm') {
    const pkgFile = repo.files.find(f => f.path === 'package.json');
    if (pkgFile) {
      try {
        const pkg = JSON.parse(pkgFile.content);

        // main field
        if (pkg.main) {
          const resolved = resolveFilePath(repo, pkg.main);
          if (resolved) {
            entries.push({
              filePath: resolved.path,
              reason: 'package-main',
              exports: extractExports(resolved),
              confidence: 0.95,
            });
          }
        }

        // module field (ESM entry)
        if (pkg.module) {
          const resolved = resolveFilePath(repo, pkg.module);
          if (resolved) {
            entries.push({
              filePath: resolved.path,
              reason: 'package-main',
              exports: extractExports(resolved),
              confidence: 0.95,
            });
          }
        }

        // bin field (CLI entry points)
        if (pkg.bin) {
          const bins = typeof pkg.bin === 'string' ? { [pkg.name]: pkg.bin } : pkg.bin;
          for (const binPath of Object.values(bins) as string[]) {
            const resolved = resolveFilePath(repo, binPath);
            if (resolved) {
              entries.push({
                filePath: resolved.path,
                reason: 'package-bin',
                exports: extractExports(resolved),
                confidence: 0.9,
              });
            }
          }
        }

        // exports field (modern node resolution)
        if (pkg.exports) {
          const exportPaths = flattenExports(pkg.exports);
          for (const ep of exportPaths) {
            const resolved = resolveFilePath(repo, ep);
            if (resolved) {
              entries.push({
                filePath: resolved.path,
                reason: 'package-exports',
                exports: extractExports(resolved),
                confidence: 0.95,
              });
            }
          }
        }
      } catch {
        // Invalid package.json — skip
      }
    }
  }

  if (ecosystem === 'pip') {
    // Look for setup.py entry_points or pyproject.toml [project.scripts]
    const setupPy = repo.files.find(f => f.path === 'setup.py');
    if (setupPy) {
      const mainMatch = setupPy.content.match(/entry_points\s*=\s*\{[^}]*console_scripts[^}]*['"]([^'"]+)\s*=\s*([^'"]+)['"]/);
      if (mainMatch) {
        const modulePath = mainMatch[2].split(':')[0].replace(/\./g, '/') + '.py';
        const resolved = resolveFilePath(repo, modulePath);
        if (resolved) {
          entries.push({
            filePath: resolved.path,
            reason: 'cli-entrypoint',
            exports: extractExports(resolved),
            confidence: 0.85,
          });
        }
      }
    }
  }

  return entries;
}

// ── File Selection ───────────────────────────────────────────────────

/**
 * Select the most informative files for Claude analysis.
 * Priority: entry points > config files > README > source by size.
 */
export function selectFilesForAnalysis(
  repo: LoadedRepo,
  entryPoints: EntryPoint[],
  options: AnalysisOptions
): RepoFile[] {
  const selected: RepoFile[] = [];
  const seen = new Set<string>();

  // Priority 1: Entry point files
  for (const ep of entryPoints) {
    const file = repo.files.find(f => f.path === ep.filePath);
    if (file && !seen.has(file.path)) {
      selected.push(file);
      seen.add(file.path);
    }
  }

  // Priority 2: Config/manifest files
  for (const pattern of CONFIG_FILE_PATTERNS) {
    const file = repo.files.find(f => f.path === pattern);
    if (file && !seen.has(file.path)) {
      selected.push(file);
      seen.add(file.path);
    }
  }

  // Priority 3: Source files sorted by size (larger files = more substance)
  const sourceFiles = repo.files
    .filter(f => !seen.has(f.path))
    .filter(f => !isTestFile(f.path))
    .filter(f => !isDocFile(f.path))
    .filter(f => f.language !== 'text' && f.language !== 'unknown')
    .sort((a, b) => b.size - a.size);

  for (const file of sourceFiles) {
    if (selected.length >= options.maxFilesForClaude) break;
    selected.push(file);
    seen.add(file.path);
  }

  return selected.slice(0, options.maxFilesForClaude);
}

// ── Claude Capability Extraction ─────────────────────────────────────

/**
 * Send selected files to Claude for intelligent capability extraction.
 * Returns structured analysis of capabilities, repo type, and config.
 */
export async function extractCapabilitiesWithClaude(
  files: RepoFile[],
  entryPoints: EntryPoint[],
  ecosystem: string | null,
  primaryLanguage: string,
  readme: string | null,
  options: AnalysisOptions
): Promise<ClaudeAnalysisResult> {
  const systemPrompt = buildAnalysisSystemPrompt();
  const userMessage = buildAnalysisUserMessage(
    files, entryPoints, ecosystem, primaryLanguage, readme, options
  );

  try {
    const result = await runClaudeToolLoop({
      systemPrompt,
      messages: [{ role: 'user' as const, content: userMessage }],
      tools: [], // No tools needed — pure analysis, no tool use
    });

    return parseClaudeAnalysisResponse(result.response);
  } catch (err) {
    // Fallback: return minimal analysis from heuristics alone
    return {
      summary: `Repository with ${files.length} files in ${primaryLanguage}`,
      repoType: detectRepoType({ files, tree: [] } as any, ecosystem),
      capabilities: [],
      configFields: [],
    };
  }
}

/**
 * Build the system prompt for Claude code analysis.
 */
export function buildAnalysisSystemPrompt(): string {
  return `You are a code analysis engine for Agent Friday, an AGI OS. Your job is to analyze source code and extract a structured capability manifest.

You will receive:
1. Source files from a repository (the most important ones selected by heuristic)
2. Identified entry points
3. Ecosystem information (npm, pip, cargo, etc.)
4. README content (if available)

Your task: Analyze the code and produce a JSON response with this exact structure:

{
  "summary": "1-2 sentence description of what this codebase does",
  "repoType": "library|cli-tool|api-server|framework|plugin|standalone|monorepo|data-pipeline|unknown",
  "capabilities": [
    {
      "name": "human_readable_capability_name",
      "description": "Clear description of what this capability does and when you'd use it",
      "category": "data-processing|file-operations|network|computation|text-processing|image-processing|audio-processing|video-processing|code-generation|database|authentication|messaging|scheduling|system|utility|other",
      "inputSchema": {
        "type": "object",
        "properties": {
          "param_name": {
            "type": "string|number|boolean|array|object",
            "description": "What this parameter does"
          }
        },
        "required": ["param_name"]
      },
      "outputSchema": {
        "type": "string|object|array|buffer|void|stream",
        "description": "What this capability returns"
      },
      "sourceFile": "relative/path/to/file.ts",
      "exportedName": "functionOrClassName",
      "startLine": 1,
      "endLine": 50,
      "isDefaultExport": false,
      "adaptationComplexity": "trivial|simple|moderate|complex|infeasible",
      "adaptationNotes": "Brief notes on how to adapt this into an Agent Friday connector"
    }
  ],
  "configFields": [
    {
      "key": "config_key",
      "type": "string|number|boolean",
      "description": "What this config field controls",
      "required": true,
      "default": "default_value",
      "envVar": "ENV_VAR_NAME"
    }
  ]
}

RULES:
- Only extract REAL capabilities — functions/methods a consumer would actually call
- Skip internal utilities, type definitions, constants, and implementation details
- A capability must be something Agent Friday could expose as a tool to the user
- Input schemas must be valid JSON Schema objects
- Be conservative: if you're unsure about a capability, include it with adaptationComplexity "complex"
- Do NOT include test utilities, development helpers, or build scripts as capabilities
- Respond with ONLY the JSON object, no markdown formatting`;
}

/**
 * Build the user message with file contents and context.
 */
export function buildAnalysisUserMessage(
  files: RepoFile[],
  entryPoints: EntryPoint[],
  ecosystem: string | null,
  primaryLanguage: string,
  readme: string | null,
  options: AnalysisOptions
): string {
  const parts: string[] = [];

  parts.push(`## Repository Analysis Request`);
  parts.push(`- Primary language: ${primaryLanguage}`);
  parts.push(`- Ecosystem: ${ecosystem || 'unknown'}`);
  parts.push(`- Total files: ${files.length}`);
  parts.push('');

  // Entry points
  if (entryPoints.length > 0) {
    parts.push(`## Identified Entry Points`);
    for (const ep of entryPoints) {
      parts.push(`- \`${ep.filePath}\` (${ep.reason}, confidence: ${ep.confidence})`);
      if (ep.exports.length > 0) {
        for (const exp of ep.exports.slice(0, 10)) {
          parts.push(`  - ${exp.kind}: ${exp.name}${exp.signature ? ` — ${exp.signature}` : ''}`);
        }
      }
    }
    parts.push('');
  }

  // README
  if (readme) {
    parts.push(`## README`);
    parts.push(readme.slice(0, 3000));
    parts.push('');
  }

  // File contents
  let totalChars = 0;
  parts.push(`## Source Files`);
  for (const file of files) {
    if (totalChars >= options.maxContentChars) {
      parts.push(`\n(Remaining files truncated to stay within analysis budget)`);
      break;
    }

    const remaining = options.maxContentChars - totalChars;
    const content = file.content.slice(0, Math.min(remaining, 8000));
    totalChars += content.length;

    parts.push(`\n### ${file.path} (${file.language}, ${file.size} bytes)`);
    parts.push('```' + file.language);
    parts.push(content);
    parts.push('```');
  }

  return parts.join('\n');
}

/**
 * Parse Claude's analysis response into structured data.
 */
export function parseClaudeAnalysisResponse(response: string): ClaudeAnalysisResult {
  const fallback: ClaudeAnalysisResult = {
    summary: '',
    repoType: 'unknown',
    capabilities: [],
    configFields: [],
  };

  if (!response || response.trim().length === 0) return fallback;

  try {
    // Strip markdown fences if present
    let cleaned = response.trim();
    cleaned = cleaned.replace(/^```(?:json)?\s*/m, '');
    cleaned = cleaned.replace(/\s*```\s*$/m, '');

    // Find JSON boundaries
    const start = cleaned.indexOf('{');
    const end = cleaned.lastIndexOf('}');
    if (start === -1 || end === -1 || end <= start) return fallback;

    const json = cleaned.slice(start, end + 1);
    const parsed = JSON.parse(json);

    // Validate and extract
    const result: ClaudeAnalysisResult = {
      summary: typeof parsed.summary === 'string' ? parsed.summary.slice(0, 1000) : '',
      repoType: validateRepoType(parsed.repoType) ? parsed.repoType : 'unknown',
      capabilities: [],
      configFields: [],
    };

    // Parse capabilities
    if (Array.isArray(parsed.capabilities)) {
      for (const cap of parsed.capabilities.slice(0, 50)) {
        if (!cap.name || !cap.description) continue;

        result.capabilities.push({
          name: String(cap.name).slice(0, 100),
          description: String(cap.description).slice(0, 500),
          category: validateCategory(cap.category) ? cap.category : 'utility',
          inputSchema: validateInputSchema(cap.inputSchema)
            ? cap.inputSchema
            : { type: 'object', properties: {}, required: [] },
          outputSchema: cap.outputSchema && typeof cap.outputSchema.type === 'string'
            ? { type: cap.outputSchema.type, description: String(cap.outputSchema.description || '').slice(0, 300) }
            : { type: 'string', description: 'Output' },
          sourceFile: String(cap.sourceFile || '').slice(0, 300),
          exportedName: String(cap.exportedName || '').slice(0, 100),
          startLine: typeof cap.startLine === 'number' ? Math.max(0, cap.startLine) : 0,
          endLine: typeof cap.endLine === 'number' ? Math.max(0, cap.endLine) : 0,
          isDefaultExport: Boolean(cap.isDefaultExport),
          adaptationComplexity: validateComplexity(cap.adaptationComplexity) ? cap.adaptationComplexity : 'moderate',
          adaptationNotes: String(cap.adaptationNotes || '').slice(0, 500),
        });
      }
    }

    // Parse config fields
    if (Array.isArray(parsed.configFields)) {
      for (const field of parsed.configFields.slice(0, 30)) {
        if (!field.key) continue;
        result.configFields.push({
          key: String(field.key).slice(0, 100),
          type: String(field.type || 'string').slice(0, 50),
          description: String(field.description || '').slice(0, 300),
          required: Boolean(field.required),
          default: field.default,
          envVar: field.envVar ? String(field.envVar).slice(0, 100) : undefined,
        });
      }
    }

    return result;
  } catch {
    return fallback;
  }
}

// ── Capability Building ──────────────────────────────────────────────

/**
 * Build final Capability objects from Claude's analysis.
 */
function buildCapabilities(
  claudeCaps: ClaudeCapability[],
  repo: LoadedRepo
): Capability[] {
  return claudeCaps.map((cc, i) => {
    const source: SourceLocation = {
      filePath: cc.sourceFile,
      startLine: cc.startLine,
      endLine: cc.endLine,
      exportedName: cc.exportedName,
      isDefaultExport: cc.isDefaultExport,
    };

    // Compute per-capability confidence
    const file = repo.files.find(f => f.path === cc.sourceFile);
    const signals: ConfidenceSignal[] = [];

    if (file) {
      signals.push({
        name: 'source-found',
        score: 1.0,
        weight: 0.3,
        reason: 'Source file exists in repo',
      });

      if (file.language === 'typescript') {
        signals.push({
          name: 'typed-language',
          score: 0.9,
          weight: 0.3,
          reason: 'TypeScript provides type information',
        });
      }
    } else {
      signals.push({
        name: 'source-missing',
        score: 0.2,
        weight: 0.5,
        reason: 'Referenced source file not found',
      });
    }

    if (cc.description.length > 20) {
      signals.push({
        name: 'description-quality',
        score: 0.8,
        weight: 0.2,
        reason: 'Detailed capability description',
      });
    }

    if (cc.inputSchema.properties && Object.keys(cc.inputSchema.properties).length > 0) {
      signals.push({
        name: 'schema-present',
        score: 0.8,
        weight: 0.2,
        reason: 'Input schema has defined parameters',
      });
    }

    return {
      id: `cap-${i}-${Math.random().toString(36).slice(2, 8)}`,
      name: cc.name,
      description: cc.description,
      category: cc.category,
      inputSchema: cc.inputSchema,
      outputSchema: cc.outputSchema,
      source,
      language: file?.language || 'unknown',
      confidence: computeConfidence(signals),
      confidenceSignals: signals,
      adaptationComplexity: cc.adaptationComplexity,
      adaptationNotes: cc.adaptationNotes,
    };
  });
}

// ── Confidence Scoring ───────────────────────────────────────────────

/**
 * Compute confidence signals for the overall analysis.
 */
export function computeConfidenceSignals(
  repo: LoadedRepo,
  entryPoints: EntryPoint[],
  capabilities: Capability[],
  metadata: ManifestMetadata
): ConfidenceSignal[] {
  const signals: ConfidenceSignal[] = [];

  // Type annotations
  const hasTypes = repo.files.some(f =>
    f.language === 'typescript' || f.path.endsWith('.pyi') || f.path.endsWith('.d.ts')
  );
  signals.push({
    name: 'type-annotations',
    score: hasTypes ? 0.9 : 0.3,
    weight: 0.25,
    reason: hasTypes ? 'Repository has type annotations' : 'No type annotations detected',
  });

  // Test coverage
  signals.push({
    name: 'test-coverage',
    score: metadata.hasTests ? 0.8 : 0.3,
    weight: 0.2,
    reason: metadata.hasTests ? 'Tests present in repository' : 'No tests detected',
  });

  // Documentation
  signals.push({
    name: 'documentation',
    score: metadata.hasDocumentation ? 0.8 : 0.4,
    weight: 0.15,
    reason: metadata.hasDocumentation ? 'Documentation present' : 'Limited documentation',
  });

  // Entry point clarity
  const highConfEPs = entryPoints.filter(ep => ep.confidence >= 0.8);
  signals.push({
    name: 'entry-point-clarity',
    score: highConfEPs.length > 0 ? 0.9 : entryPoints.length > 0 ? 0.6 : 0.2,
    weight: 0.2,
    reason: highConfEPs.length > 0
      ? `${highConfEPs.length} clear entry point(s) identified`
      : entryPoints.length > 0
        ? 'Entry points identified with moderate confidence'
        : 'No clear entry points found',
  });

  // Capability extraction success
  signals.push({
    name: 'capabilities-extracted',
    score: capabilities.length > 0 ? Math.min(1, capabilities.length / 5) : 0.1,
    weight: 0.2,
    reason: capabilities.length > 0
      ? `${capabilities.length} capability(s) extracted`
      : 'No capabilities extracted',
  });

  return signals;
}

// ── Dependency Extraction ────────────────────────────────────────────

/**
 * Extract dependencies from package manifests.
 */
export function extractDependencies(repo: LoadedRepo, ecosystem: string | null): Dependency[] {
  const deps: Dependency[] = [];

  if (ecosystem === 'npm') {
    const pkgFile = repo.files.find(f => f.path === 'package.json');
    if (pkgFile) {
      try {
        const pkg = JSON.parse(pkgFile.content);

        for (const [name, version] of Object.entries(pkg.dependencies || {})) {
          deps.push({ name, version: String(version), scope: 'runtime', ecosystem: 'npm' });
        }
        for (const [name, version] of Object.entries(pkg.devDependencies || {})) {
          deps.push({ name, version: String(version), scope: 'dev', ecosystem: 'npm' });
        }
        for (const [name, version] of Object.entries(pkg.peerDependencies || {})) {
          deps.push({ name, version: String(version), scope: 'peer', ecosystem: 'npm' });
        }
      } catch { /* invalid JSON */ }
    }
  }

  if (ecosystem === 'pip') {
    const reqFile = repo.files.find(f => f.path === 'requirements.txt');
    if (reqFile) {
      for (const line of reqFile.content.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#') || trimmed.startsWith('-')) continue;
        const match = trimmed.match(/^([a-zA-Z0-9_-]+)\s*([><=!~]+.+)?$/);
        if (match) {
          deps.push({
            name: match[1],
            version: match[2]?.trim() || '*',
            scope: 'runtime',
            ecosystem: 'pip',
          });
        }
      }
    }
  }

  return deps;
}

// ── Repo Type Detection ──────────────────────────────────────────────

/**
 * Heuristic repo type detection (fallback when Claude doesn't provide one).
 */
export function detectRepoType(repo: LoadedRepo, ecosystem: string | null): RepoType {
  const filePaths = new Set(repo.files.map(f => f.path));

  // CLI indicators
  const hasBin = repo.files.some(f => {
    if (f.path === 'package.json') {
      try { return !!JSON.parse(f.content).bin; } catch { return false; }
    }
    return false;
  });
  if (hasBin) return 'cli-tool';

  // Server indicators
  const serverPatterns = /\b(express|fastify|koa|hapi|flask|django|gin|actix|rocket|spring)\b/i;
  const hasServer = repo.files.some(f =>
    f.path === 'package.json' && serverPatterns.test(f.content)
  );
  if (hasServer) return 'api-server';

  // Framework indicators
  if (filePaths.has('src/index.ts') || filePaths.has('src/index.js')) {
    const hasExports = repo.files.some(f =>
      (f.path === 'src/index.ts' || f.path === 'src/index.js') &&
      f.content.includes('export')
    );
    if (hasExports) return 'library';
  }

  // Monorepo indicators
  if (filePaths.has('lerna.json') || filePaths.has('pnpm-workspace.yaml')) return 'monorepo';
  if (repo.files.some(f => f.path === 'package.json' && f.content.includes('"workspaces"'))) return 'monorepo';

  // Python CLI
  const hasPythonMain = repo.files.some(f =>
    f.path.endsWith('__main__.py') || (f.path.endsWith('.py') && f.content.includes('argparse'))
  );
  if (hasPythonMain) return 'cli-tool';

  return 'library'; // Default assumption
}

// ── Helpers ──────────────────────────────────────────────────────────

function extractExports(file: RepoFile): ExportedSymbol[] {
  const exports: ExportedSymbol[] = [];

  if (file.language === 'typescript' || file.language === 'javascript') {
    // Named exports
    const namedExportRegex = /export\s+(?:async\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)/g;
    let match;
    while ((match = namedExportRegex.exec(file.content)) !== null) {
      const name = match[1];
      const line = match[0];
      let kind: ExportedSymbol['kind'] = 'variable';
      if (line.includes('function')) kind = 'function';
      else if (line.includes('class')) kind = 'class';
      else if (line.includes('type') || line.includes('interface')) kind = 'type';
      else if (line.includes('const') || line.includes('let') || line.includes('var')) kind = 'constant';

      exports.push({
        name,
        kind,
        isCapability: kind === 'function' || kind === 'class',
      });
    }

    // Default export
    if (/export\s+default\s/.test(file.content)) {
      const defaultMatch = file.content.match(/export\s+default\s+(?:async\s+)?(?:function|class)\s+(\w+)/);
      exports.push({
        name: defaultMatch?.[1] || 'default',
        kind: 'function',
        isCapability: true,
      });
    }
  }

  if (file.language === 'python') {
    // Python: functions and classes at module level
    const pyExportRegex = /^(?:async\s+)?(?:def|class)\s+(\w+)/gm;
    let match;
    while ((match = pyExportRegex.exec(file.content)) !== null) {
      const name = match[1];
      if (name.startsWith('_')) continue; // Skip private
      exports.push({
        name,
        kind: match[0].includes('class') ? 'class' : 'function',
        isCapability: !name.startsWith('_'),
      });
    }
  }

  return exports.slice(0, 50); // Cap at 50 exports
}

function resolveFilePath(repo: LoadedRepo, relativePath: string): RepoFile | null {
  // Try exact match
  let file = repo.files.find(f => f.path === relativePath);
  if (file) return file;

  // Try without leading ./
  const cleaned = relativePath.replace(/^\.\//, '');
  file = repo.files.find(f => f.path === cleaned);
  if (file) return file;

  // Try adding common extensions
  for (const ext of ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']) {
    file = repo.files.find(f => f.path === cleaned + ext);
    if (file) return file;
  }

  // Try as directory with index file
  for (const idx of ['index.ts', 'index.js', 'index.mjs']) {
    file = repo.files.find(f => f.path === `${cleaned}/${idx}`);
    if (file) return file;
  }

  return null;
}

function flattenExports(exports: unknown, prefix = ''): string[] {
  const paths: string[] = [];

  if (typeof exports === 'string') {
    paths.push(exports);
  } else if (typeof exports === 'object' && exports !== null) {
    for (const [key, value] of Object.entries(exports)) {
      if (key === 'import' || key === 'require' || key === 'default' || key === 'types') {
        if (typeof value === 'string') paths.push(value);
      } else if (typeof value === 'object') {
        paths.push(...flattenExports(value, key));
      } else if (typeof value === 'string') {
        paths.push(value);
      }
    }
  }

  return paths;
}

function getReadmeContent(repo: LoadedRepo): string | null {
  const readme = repo.files.find(f => /^readme/i.test(f.path));
  return readme ? readme.content.slice(0, 5000) : null;
}

function isTestFile(path: string): boolean {
  return TEST_PATTERNS.some(p => p.test(path));
}

function isDocFile(path: string): boolean {
  return DOC_PATTERNS.some(p => p.test(path));
}

function buildMetadata(repo: LoadedRepo): Omit<ManifestMetadata, 'claudeCalls'> {
  const codeFiles = repo.files.filter(f => f.language !== 'text' && f.language !== 'unknown');

  return {
    filesAnalyzed: codeFiles.length,
    filesSkipped: repo.files.length - codeFiles.length,
    linesAnalyzed: codeFiles.reduce((sum, f) => sum + f.content.split('\n').length, 0),
    hasTests: repo.files.some(f => isTestFile(f.path)),
    hasDocumentation: repo.files.some(f => isDocFile(f.path)),
    hasTypes: repo.files.some(f =>
      f.language === 'typescript' || f.path.endsWith('.d.ts') || f.path.endsWith('.pyi')
    ),
    license: detectLicense(repo),
    readmeExcerpt: getReadmeContent(repo)?.slice(0, 500) || null,
  };
}

function detectLicense(repo: LoadedRepo): string | null {
  const licenseFile = repo.files.find(f => /^license/i.test(f.path));
  if (!licenseFile) return null;

  const content = licenseFile.content.slice(0, 200).toLowerCase();
  if (content.includes('mit')) return 'MIT';
  if (content.includes('apache')) return 'Apache-2.0';
  if (content.includes('bsd')) return 'BSD';
  if (content.includes('gpl')) return content.includes('lesser') ? 'LGPL' : 'GPL';
  if (content.includes('isc')) return 'ISC';
  if (content.includes('mozilla')) return 'MPL-2.0';

  return 'Unknown';
}

// ── Validators ───────────────────────────────────────────────────────

const VALID_REPO_TYPES: RepoType[] = [
  'library', 'cli-tool', 'api-server', 'framework', 'plugin',
  'standalone', 'monorepo', 'data-pipeline', 'unknown',
];

function validateRepoType(type: unknown): type is RepoType {
  return typeof type === 'string' && VALID_REPO_TYPES.includes(type as RepoType);
}

const VALID_CATEGORIES: CapabilityCategory[] = [
  'data-processing', 'file-operations', 'network', 'computation',
  'text-processing', 'image-processing', 'audio-processing', 'video-processing',
  'code-generation', 'database', 'authentication', 'messaging',
  'scheduling', 'system', 'utility', 'other',
];

function validateCategory(cat: unknown): cat is CapabilityCategory {
  return typeof cat === 'string' && VALID_CATEGORIES.includes(cat as CapabilityCategory);
}

function validateComplexity(c: unknown): c is Capability['adaptationComplexity'] {
  return typeof c === 'string' && ['trivial', 'simple', 'moderate', 'complex', 'infeasible'].includes(c);
}

function validateInputSchema(schema: unknown): schema is JSONSchemaObject {
  if (!schema || typeof schema !== 'object') return false;
  const s = schema as Record<string, unknown>;
  return s.type === 'object' && typeof s.properties === 'object';
}
