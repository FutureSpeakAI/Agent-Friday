/**
 * capability-manifest.ts — Data model for superpower capability manifests.
 *
 * Track II, Phase 1: Intelligent Code Analysis.
 *
 * A CapabilityManifest is the structured understanding of what a codebase can do.
 * It bridges the gap between "a folder of source code" and "tools Agent Friday
 * can use." The manifest describes:
 *   - What the code does (capabilities / functions)
 *   - How to invoke it (input/output schemas)
 *   - How confident we are in the analysis
 *   - What language/ecosystem it targets
 *   - What dependencies it requires
 *
 * The manifest is the handoff artifact from Phase 1 (analysis) to Phase 2
 * (adaptation), which converts capabilities into working Agent Friday connectors.
 */

// ── Core Manifest ────────────────────────────────────────────────────

export interface CapabilityManifest {
  /** Unique manifest ID */
  id: string;
  /** Repo this manifest was generated from */
  repoId: string;
  /** Repo name */
  repoName: string;
  /** When the analysis was performed */
  analyzedAt: number;
  /** Analysis duration in ms */
  analysisDurationMs: number;

  /** High-level description of what this codebase does */
  summary: string;
  /** Primary programming language */
  primaryLanguage: string;
  /** All languages detected */
  languages: string[];
  /** Repository type classification */
  repoType: RepoType;
  /** Ecosystem (npm, pip, cargo, go-modules, etc.) */
  ecosystem: string | null;

  /** Extracted capabilities — the core value */
  capabilities: Capability[];
  /** Entry points identified in the codebase */
  entryPoints: EntryPoint[];
  /** External dependencies required */
  dependencies: Dependency[];
  /** Detected configuration schema (if any) */
  configSchema: ConfigField[] | null;

  /** Overall confidence in the analysis */
  confidence: AnalysisConfidence;
  /** Analysis metadata */
  metadata: ManifestMetadata;
}

// ── Repository Classification ────────────────────────────────────────

export type RepoType =
  | 'library'        // Importable code with exported API surface
  | 'cli-tool'       // Command-line application
  | 'api-server'     // HTTP/gRPC/WebSocket server
  | 'framework'      // Application framework (Express, Django, etc.)
  | 'plugin'         // Plugin/extension for another system
  | 'standalone'     // Self-contained application
  | 'monorepo'       // Multi-package repository
  | 'data-pipeline'  // ETL/data processing pipeline
  | 'unknown';       // Could not determine

// ── Capabilities ─────────────────────────────────────────────────────

export interface Capability {
  /** Unique capability ID within this manifest */
  id: string;
  /** Human-readable name (suitable for tool name conversion) */
  name: string;
  /** Clear description of what this capability does */
  description: string;
  /** The category of capability */
  category: CapabilityCategory;

  /** Input parameters as JSON Schema */
  inputSchema: JSONSchemaObject;
  /** Output description */
  outputSchema: OutputSchema;

  /** Source location in the codebase */
  source: SourceLocation;
  /** Language of the source code */
  language: string;

  /** How confident we are in this specific capability's analysis */
  confidence: number; // 0-1
  /** What signals contributed to confidence */
  confidenceSignals: ConfidenceSignal[];

  /** Estimated complexity of adapting this capability */
  adaptationComplexity: 'trivial' | 'simple' | 'moderate' | 'complex' | 'infeasible';
  /** Notes for the adaptation engine */
  adaptationNotes: string;
}

export type CapabilityCategory =
  | 'data-processing'   // Transform, parse, convert data
  | 'file-operations'   // Read, write, transform files
  | 'network'           // HTTP requests, API calls
  | 'computation'       // Mathematical, scientific computation
  | 'text-processing'   // NLP, parsing, formatting
  | 'image-processing'  // Image manipulation, conversion
  | 'audio-processing'  // Audio manipulation, transcription
  | 'video-processing'  // Video manipulation
  | 'code-generation'   // Generate or transform code
  | 'database'          // Database operations
  | 'authentication'    // Auth flows, token management
  | 'messaging'         // Email, chat, notifications
  | 'scheduling'        // Cron, timers, job scheduling
  | 'system'            // OS interaction, process management
  | 'utility'           // General-purpose utilities
  | 'other';

// ── Schema Types ─────────────────────────────────────────────────────

export interface JSONSchemaObject {
  type: 'object';
  properties: Record<string, JSONSchemaProperty>;
  required?: string[];
  description?: string;
}

export interface JSONSchemaProperty {
  type: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  items?: JSONSchemaProperty;
  properties?: Record<string, JSONSchemaProperty>;
  required?: string[];
}

export interface OutputSchema {
  type: string;  // 'string' | 'object' | 'array' | 'buffer' | 'void' | 'stream'
  description: string;
  /** For object outputs, optional JSON Schema */
  schema?: JSONSchemaObject;
}

// ── Source Location ──────────────────────────────────────────────────

export interface SourceLocation {
  /** File path relative to repo root */
  filePath: string;
  /** Start line (1-indexed) */
  startLine: number;
  /** End line (1-indexed) */
  endLine: number;
  /** Exported name (function/class/method name) */
  exportedName: string;
  /** Whether this is a default export */
  isDefaultExport: boolean;
}

// ── Entry Points ─────────────────────────────────────────────────────

export interface EntryPoint {
  /** File path relative to repo root */
  filePath: string;
  /** Why this was identified as an entry point */
  reason: EntryPointReason;
  /** Exports from this entry point */
  exports: ExportedSymbol[];
  /** Confidence that this is a real entry point */
  confidence: number; // 0-1
}

export type EntryPointReason =
  | 'package-main'        // package.json main/module/exports
  | 'package-bin'         // package.json bin field
  | 'package-exports'     // package.json exports map
  | 'python-init'         // __init__.py
  | 'python-main'         // __main__.py or if __name__ == "__main__"
  | 'cargo-lib'           // src/lib.rs
  | 'cargo-bin'           // src/main.rs or src/bin/*.rs
  | 'go-main'             // package main + func main()
  | 'index-file'          // index.ts/js/py at package root
  | 'readme-referenced'   // Mentioned in README usage examples
  | 'public-api-pattern'  // Naming convention (api.ts, public.ts, etc.)
  | 'cli-entrypoint'      // Shebang line or CLI framework setup
  | 'heuristic';          // Detected via heuristic analysis

export interface ExportedSymbol {
  name: string;
  kind: 'function' | 'class' | 'constant' | 'type' | 'variable' | 'namespace';
  /** Whether this is likely a "capability" vs. utility */
  isCapability: boolean;
  /** Raw signature (if extractable) */
  signature?: string;
}

// ── Dependencies ─────────────────────────────────────────────────────

export interface Dependency {
  name: string;
  version: string;
  /** Is this needed at runtime or just for development? */
  scope: 'runtime' | 'dev' | 'peer' | 'optional';
  /** Ecosystem (npm, pip, cargo, etc.) */
  ecosystem: string;
  /** Does Agent Friday already have this dependency? */
  alreadyPresent?: boolean;
}

// ── Configuration ────────────────────────────────────────────────────

export interface ConfigField {
  key: string;
  type: string;
  description: string;
  required: boolean;
  default?: unknown;
  /** Environment variable that supplies this config (if applicable) */
  envVar?: string;
}

// ── Confidence Model ─────────────────────────────────────────────────

export interface AnalysisConfidence {
  /** Overall confidence 0-1 */
  overall: number;
  /** Individual confidence signals */
  signals: ConfidenceSignal[];
  /** Human-readable explanation */
  explanation: string;
}

export interface ConfidenceSignal {
  name: string;
  score: number;   // 0-1
  weight: number;  // How much this signal matters
  reason: string;
}

// ── Metadata ─────────────────────────────────────────────────────────

export interface ManifestMetadata {
  /** Number of files analyzed */
  filesAnalyzed: number;
  /** Number of files skipped (binary, too large, etc.) */
  filesSkipped: number;
  /** Total lines of code analyzed */
  linesAnalyzed: number;
  /** Number of Claude API calls made during analysis */
  claudeCalls: number;
  /** Whether the repo has tests */
  hasTests: boolean;
  /** Whether the repo has documentation */
  hasDocumentation: boolean;
  /** Whether the repo has type annotations */
  hasTypes: boolean;
  /** License detected */
  license: string | null;
  /** README excerpt (first 500 chars) */
  readmeExcerpt: string | null;
}

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Compute composite confidence score from signals.
 */
export function computeConfidence(signals: ConfidenceSignal[]): number {
  if (signals.length === 0) return 0;

  let weightedSum = 0;
  let totalWeight = 0;

  for (const signal of signals) {
    weightedSum += signal.score * signal.weight;
    totalWeight += signal.weight;
  }

  return totalWeight > 0 ? Math.max(0, Math.min(1, weightedSum / totalWeight)) : 0;
}

/**
 * Generate a human-readable confidence explanation.
 */
export function explainConfidence(signals: ConfidenceSignal[]): string {
  if (signals.length === 0) return 'No confidence signals available.';

  const sorted = [...signals].sort((a, b) => (b.score * b.weight) - (a.score * a.weight));
  const parts: string[] = [];

  const strong = sorted.filter(s => s.score >= 0.7);
  const weak = sorted.filter(s => s.score < 0.4);

  if (strong.length > 0) {
    parts.push(`Strengths: ${strong.map(s => s.reason).join('; ')}`);
  }
  if (weak.length > 0) {
    parts.push(`Weaknesses: ${weak.map(s => s.reason).join('; ')}`);
  }

  return parts.join(' | ') || 'Analysis completed with mixed signals.';
}

/**
 * Convert a capability into an Agent Friday ToolDeclaration shape.
 * This is the bridge between analysis output and the connector system.
 */
export function capabilityToToolDeclaration(capability: Capability, prefix = ''): {
  name: string;
  description: string;
  parameters: JSONSchemaObject;
} {
  const toolName = prefix
    ? `${prefix}_${sanitizeToolName(capability.name)}`
    : sanitizeToolName(capability.name);

  return {
    name: toolName,
    description: capability.description.slice(0, 500),
    parameters: capability.inputSchema,
  };
}

/**
 * Sanitize a capability name into a valid tool name.
 * Tool names must be lowercase, alphanumeric + underscores, no spaces.
 */
export function sanitizeToolName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
    .slice(0, 64);
}

/**
 * Validate that a manifest has minimum required fields.
 */
export function validateManifest(manifest: CapabilityManifest): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  if (!manifest.id) errors.push('Missing manifest ID');
  if (!manifest.repoId) errors.push('Missing repo ID');
  if (!manifest.summary) errors.push('Missing summary');
  if (!manifest.primaryLanguage) errors.push('Missing primary language');
  if (!manifest.capabilities || manifest.capabilities.length === 0) {
    errors.push('No capabilities extracted');
  }

  for (const cap of manifest.capabilities || []) {
    if (!cap.name) errors.push(`Capability missing name`);
    if (!cap.description) errors.push(`Capability "${cap.name}" missing description`);
    if (!cap.inputSchema) errors.push(`Capability "${cap.name}" missing input schema`);
    if (!cap.outputSchema) errors.push(`Capability "${cap.name}" missing output schema`);
  }

  if (!manifest.confidence) {
    errors.push('Missing confidence assessment');
  } else if (manifest.confidence.overall < 0 || manifest.confidence.overall > 1) {
    errors.push('Confidence overall out of range [0, 1]');
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Generate a human-readable summary of a manifest for display.
 */
export function summarizeManifest(manifest: CapabilityManifest): string {
  const parts: string[] = [];

  parts.push(`**${manifest.repoName}** (${manifest.primaryLanguage})`);
  parts.push(manifest.summary);
  parts.push('');

  // Capabilities
  parts.push(`### Capabilities (${manifest.capabilities.length})`);
  for (const cap of manifest.capabilities.slice(0, 10)) {
    const conf = Math.round(cap.confidence * 100);
    parts.push(`- **${cap.name}** — ${cap.description.slice(0, 100)} (${conf}% confidence)`);
  }
  if (manifest.capabilities.length > 10) {
    parts.push(`  ... +${manifest.capabilities.length - 10} more`);
  }
  parts.push('');

  // Confidence
  const overallConf = Math.round(manifest.confidence.overall * 100);
  parts.push(`### Analysis Confidence: ${overallConf}%`);
  parts.push(manifest.confidence.explanation);
  parts.push('');

  // Dependencies
  const runtimeDeps = manifest.dependencies.filter(d => d.scope === 'runtime');
  if (runtimeDeps.length > 0) {
    parts.push(`### Dependencies (${runtimeDeps.length} runtime)`);
    for (const dep of runtimeDeps.slice(0, 10)) {
      parts.push(`- ${dep.name}@${dep.version}`);
    }
  }

  return parts.join('\n');
}
