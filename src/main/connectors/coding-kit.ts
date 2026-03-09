/**
 * coding-kit.ts — Coding Kit connector for NEXUS OS.
 *
 * Sprint 6 Track E: "The Coder" — Full Coding Agent Intelligence
 *
 * Phase 1 (GitLoader Ingestion):
 *   1. Optimized GitLoader LoadOptions for the coding-kit monorepo
 *   2. First-party security exemption (auto-approved via trusted-repos)
 *   3. Coding-kit status queries and code search
 *
 * Phase 2-3 (Code Intelligence & Analysis):
 *   4. File tree navigation — browse repo structure by directory
 *   5. Project summary — language stats, deps, key files, structure
 *   6. Symbol finder — locate function/class/interface/type/enum definitions
 *   7. Dependency analysis — parse package.json across workspace packages
 *
 * The coding-kit repo: https://github.com/FutureSpeakAI/agent-fridays-coding-kit
 * It's a TypeScript npm workspace monorepo with 7 packages.
 *
 * Loaded packages (relevant to agent capabilities):
 *   - pi-ai:            Multi-provider LLM API (15+ providers)
 *   - pi-agent-core:    Agent runtime, transport, state management
 *   - pi-coding-agent:  CLI coding agent with bash/edit/read/write tools
 *
 * Excluded packages (not needed in Electron context):
 *   - pi-mom:     Slack bot (comms-hub connector handles messaging)
 *   - pi-pods:    GPU pod management (infrastructure, not agent logic)
 *   - pi-tui:     Terminal UI (we have our own Electron UI)
 *   - pi-web-ui:  Web components (deferred to Phase 3)
 *
 * Adaptation strategy: direct-import (TypeScript, npm workspace)
 *
 * Exports:
 *   TOOLS    — Tool declarations for the connector registry
 *   execute  — Async handler that dispatches tool calls
 *   detect   — Async check for whether the coding kit is available
 */

import { gitLoader, type LoadOptions, type LoadedRepo } from '../git-loader';

// ---------------------------------------------------------------------------
// Types (mirrored from registry.ts to avoid circular import)
// ---------------------------------------------------------------------------

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** The official coding-kit repo URL. */
export const CODING_KIT_REPO = 'https://github.com/FutureSpeakAI/agent-fridays-coding-kit';

/** Repo ID after loading (owner/name@branch). */
const CODING_KIT_REPO_ID = 'FutureSpeakAI/agent-fridays-coding-kit@main';

/**
 * Optimized LoadOptions for the coding-kit monorepo.
 *
 * Key decisions:
 *   - excludeOverrides: ['packages'] — DEFAULT_EXCLUDE includes 'packages'
 *     which would skip the entire monorepo. We override this.
 *   - excludePatterns: Skip the 4 irrelevant packages + build artifacts.
 *   - includePatterns: Focus on TypeScript source, configs, and docs.
 *   - maxFileSize: 256KB — coding-kit files are all reasonable size.
 */
export const CODING_KIT_LOAD_OPTIONS: LoadOptions = {
  branch: 'main',
  excludeOverrides: ['packages'],  // Don't exclude the packages/ directory
  excludePatterns: [
    // Skip irrelevant packages
    'packages/pi-mom',
    'packages/pi-pods',
    'packages/pi-tui',
    'packages/pi-web-ui',
    // Skip build artifacts within loaded packages
    'dist',
    'build',
    '.turbo',
    '.cache',
    'coverage',
    'node_modules',
  ],
  includePatterns: [
    '*.ts',
    '*.tsx',
    '*.json',
    '*.md',
    '*.yaml',
    '*.yml',
  ],
  maxFileSize: 256 * 1024,  // 256KB
};

// ---------------------------------------------------------------------------
// Internal State
// ---------------------------------------------------------------------------

let loadedRepo: LoadedRepo | null = null;
let loadError: string | null = null;

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'coding_kit_load',
    description:
      'Load the Agent Friday coding kit repository via GitLoader with optimized settings. ' +
      'Ingests the pi-ai, pi-agent-core, and pi-coding-agent packages for coding agent capabilities. ' +
      'First-party repo — auto-approved by security pipeline.',
    parameters: {
      type: 'object',
      properties: {
        force: {
          type: 'boolean',
          description: 'Force reload even if already loaded (default: false)',
        },
      },
    },
  },
  {
    name: 'coding_kit_status',
    description:
      'Get the current status of the coding kit — whether loaded, file count, ' +
      'indexed packages, total size, and any load errors.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'coding_kit_search',
    description:
      'Search the loaded coding kit codebase for code patterns, function definitions, ' +
      'tool declarations, or any text content. Requires the coding kit to be loaded first.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Search query — text pattern to find in the coding kit source code',
        },
        maxResults: {
          type: 'number',
          description: 'Maximum number of results to return (default: 20)',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'coding_kit_read_file',
    description:
      'Read a specific file from the loaded coding kit repository. ' +
      'Use coding_kit_search or coding_kit_status to discover file paths first.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Relative path within the repo (e.g. "packages/pi-coding-agent/src/index.ts")',
        },
      },
      required: ['file_path'],
    },
  },

  // ── Phase 2-3: Code Intelligence Tools ──────────────────────────────

  {
    name: 'coding_kit_get_tree',
    description:
      'Get the file/directory tree of the loaded coding kit repository. ' +
      'Optionally filter to a specific directory subtree. ' +
      'Shows file sizes and detected languages for each entry.',
    parameters: {
      type: 'object',
      properties: {
        directory: {
          type: 'string',
          description: 'Filter to entries under this directory (e.g. "packages/pi-coding-agent/src"). If omitted, returns full tree.',
        },
        files_only: {
          type: 'boolean',
          description: 'Only return files (exclude directories). Default: false.',
        },
      },
    },
  },
  {
    name: 'coding_kit_get_summary',
    description:
      'Get a structured intelligence summary of the coding kit repository: ' +
      'primary language, detected frameworks/topics, dependency list, key config files, ' +
      'and top-level directory structure. Requires the coding kit to be loaded first.',
    parameters: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'coding_kit_find_symbols',
    description:
      'Find symbol definitions (functions, classes, interfaces, types, enums, constants) ' +
      'in the loaded coding kit codebase. Returns the symbol name, kind, file path, line number, ' +
      'and surrounding context. Great for understanding APIs, finding implementations, and navigating code.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Symbol name or pattern to search for (e.g. "execute", "Tool", "Agent")',
        },
        kind: {
          type: 'string',
          description: 'Filter by symbol kind: "function", "class", "interface", "type", "enum", "const", or "all" (default: "all")',
        },
        package_name: {
          type: 'string',
          description: 'Filter to a specific package (e.g. "pi-coding-agent", "pi-ai"). If omitted, searches all packages.',
        },
        exported_only: {
          type: 'boolean',
          description: 'Only return exported symbols (default: false)',
        },
        maxResults: {
          type: 'number',
          description: 'Maximum number of results to return (default: 30)',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'coding_kit_analyze_deps',
    description:
      'Analyze dependencies across workspace packages in the coding kit. ' +
      'Shows production deps, dev deps, peer deps, and internal workspace cross-references ' +
      'for each loaded package. Optionally filter to a specific package.',
    parameters: {
      type: 'object',
      properties: {
        package_name: {
          type: 'string',
          description: 'Analyze a specific package (e.g. "pi-coding-agent"). If omitted, analyzes all packages.',
        },
      },
    },
  },
];

// ---------------------------------------------------------------------------
// Execute Dispatcher
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'coding_kit_load':
        return await handleLoad(args.force === true);
      case 'coding_kit_status':
        return handleStatus();
      case 'coding_kit_search':
        return await handleSearch(args);
      case 'coding_kit_read_file':
        return handleReadFile(args);
      // Phase 2-3: Code Intelligence
      case 'coding_kit_get_tree':
        return handleGetTree(args);
      case 'coding_kit_get_summary':
        return handleGetSummary();
      case 'coding_kit_find_symbols':
        return handleFindSymbols(args);
      case 'coding_kit_analyze_deps':
        return handleAnalyzeDeps(args);
      default:
        return { error: `Unknown coding-kit tool: ${toolName}` };
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Coding kit error: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detect — checks if the coding kit repo can be loaded (git available)
// ---------------------------------------------------------------------------

export async function detect(): Promise<boolean> {
  try {
    // Check if git is available (required for GitLoader)
    const { execFile } = await import('node:child_process');
    return new Promise((resolve) => {
      execFile('git', ['--version'], { windowsHide: true }, (err) => {
        resolve(!err);
      });
    });
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Tool Handlers
// ---------------------------------------------------------------------------

async function handleLoad(force: boolean): Promise<ToolResult> {
  // Check if already loaded
  if (loadedRepo && !force) {
    return {
      result: JSON.stringify({
        status: 'already_loaded',
        repoId: loadedRepo.id,
        files: loadedRepo.files.length,
        totalSizeKB: Math.round(loadedRepo.totalSize / 1024),
        message: 'Coding kit is already loaded. Use force=true to reload.',
      }),
    };
  }

  // If force reload, unload first
  if (loadedRepo && force) {
    try {
      await gitLoader.unload(loadedRepo.id);
    } catch {
      // May have already been unloaded
    }
    loadedRepo = null;
  }

  loadError = null;

  try {
    console.log('[CodingKit] Loading coding kit repository...');
    const repo = await gitLoader.load(CODING_KIT_REPO, CODING_KIT_LOAD_OPTIONS);
    loadedRepo = repo;

    // Categorize loaded files by package
    const packages: Record<string, number> = {};
    for (const file of repo.files) {
      const match = file.path.match(/^packages\/([^/]+)\//);
      if (match) {
        packages[match[1]] = (packages[match[1]] || 0) + 1;
      }
    }

    const result = {
      status: 'loaded',
      repoId: repo.id,
      files: repo.files.length,
      totalSizeKB: Math.round(repo.totalSize / 1024),
      packages,
      message: `Coding kit loaded: ${repo.files.length} files across ${Object.keys(packages).length} packages`,
    };

    console.log(`[CodingKit] ${result.message}`);
    return { result: JSON.stringify(result) };
  } catch (err) {
    loadError = err instanceof Error ? err.message : String(err);
    console.error(`[CodingKit] Load failed: ${loadError}`);
    return { error: `Failed to load coding kit: ${loadError}` };
  }
}

function handleStatus(): ToolResult {
  if (!loadedRepo) {
    return {
      result: JSON.stringify({
        loaded: false,
        error: loadError,
        repoUrl: CODING_KIT_REPO,
        message: loadError
          ? `Coding kit not loaded (error: ${loadError})`
          : 'Coding kit not loaded. Use coding_kit_load to ingest it.',
      }),
    };
  }

  // Categorize files
  const packages: Record<string, number> = {};
  const languages: Record<string, number> = {};
  for (const file of loadedRepo.files) {
    const match = file.path.match(/^packages\/([^/]+)\//);
    if (match) packages[match[1]] = (packages[match[1]] || 0) + 1;
    languages[file.language] = (languages[file.language] || 0) + 1;
  }

  return {
    result: JSON.stringify({
      loaded: true,
      repoId: loadedRepo.id,
      branch: loadedRepo.branch,
      files: loadedRepo.files.length,
      totalSizeKB: Math.round(loadedRepo.totalSize / 1024),
      packages,
      languages,
      loadedAt: new Date(loadedRepo.loadedAt).toISOString(),
    }),
  };
}

async function handleSearch(args: Record<string, unknown>): Promise<ToolResult> {
  if (!args.query || typeof args.query !== 'string') {
    return { error: 'Missing required parameter: query' };
  }

  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  const maxResults = typeof args.maxResults === 'number' ? args.maxResults : 20;

  try {
    const results = gitLoader.search(loadedRepo.id, args.query, { maxResults });
    return {
      result: JSON.stringify({
        query: args.query,
        totalResults: results.length,
        results: results.map((r) => ({
          file: r.file,
          line: r.line,
          content: r.content,
          context: r.context,
        })),
      }),
    };
  } catch (err) {
    return { error: `Search failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

function handleReadFile(args: Record<string, unknown>): ToolResult {
  if (!args.file_path || typeof args.file_path !== 'string') {
    return { error: 'Missing required parameter: file_path' };
  }

  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  try {
    const file = gitLoader.getFile(loadedRepo.id, args.file_path);
    if (!file) {
      return { error: `File not found: ${args.file_path}` };
    }

    return {
      result: JSON.stringify({
        path: file.path,
        language: file.language,
        size: file.size,
        content: file.content,
      }),
    };
  } catch (err) {
    return { error: `Read failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// ---------------------------------------------------------------------------
// Phase 2-3: Code Intelligence Handlers
// ---------------------------------------------------------------------------

/**
 * Get the file/directory tree, optionally filtered to a subtree.
 */
function handleGetTree(args: Record<string, unknown>): ToolResult {
  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  try {
    const tree = gitLoader.getTree(loadedRepo.id);
    const directory = typeof args.directory === 'string' ? args.directory : '';
    const filesOnly = args.files_only === true;

    let filtered = tree;

    // Filter to subtree if directory specified
    if (directory) {
      const prefix = directory.endsWith('/') ? directory : `${directory}/`;
      filtered = filtered.filter(
        (e) => e.path.startsWith(prefix) || e.path === directory,
      );
    }

    // Filter to files only
    if (filesOnly) {
      filtered = filtered.filter((e) => e.type === 'file');
    }

    return {
      result: JSON.stringify({
        directory: directory || '(root)',
        totalEntries: filtered.length,
        entries: filtered.map((e) => ({
          path: e.path,
          type: e.type,
          ...(e.size !== undefined && { sizeBytes: e.size }),
          ...(e.language && { language: e.language }),
        })),
      }),
    };
  } catch (err) {
    return { error: `Tree failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

/**
 * Get a structured project summary using GitLoader's getSummary().
 */
function handleGetSummary(): ToolResult {
  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  try {
    const summary = gitLoader.getSummary(loadedRepo.id);

    // Also extract workspace packages from loaded files
    const packages: Record<string, { files: number; sizeKB: number }> = {};
    for (const file of loadedRepo.files) {
      const match = file.path.match(/^packages\/([^/]+)\//);
      if (match) {
        const pkg = match[1];
        if (!packages[pkg]) packages[pkg] = { files: 0, sizeKB: 0 };
        packages[pkg].files += 1;
        packages[pkg].sizeKB += Math.round(file.size / 1024);
      }
    }

    return {
      result: JSON.stringify({
        name: summary.name,
        description: summary.description,
        primaryLanguage: summary.language,
        topics: summary.topics,
        keyFiles: summary.keyFiles,
        dependencyCount: Object.keys(summary.dependencies).length,
        dependencies: summary.dependencies,
        packages,
        structure: summary.structure,
        totalFiles: loadedRepo.files.length,
        totalSizeKB: Math.round(loadedRepo.totalSize / 1024),
      }),
    };
  } catch (err) {
    return { error: `Summary failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// ── Symbol Patterns ─────────────────────────────────────────────────

/** Symbol kinds we can detect in TypeScript/JavaScript source files. */
type SymbolKind = 'function' | 'class' | 'interface' | 'type' | 'enum' | 'const';

interface SymbolMatch {
  name: string;
  kind: SymbolKind;
  exported: boolean;
  file: string;
  line: number;
  context: string;
}

/**
 * Regex patterns for TypeScript/JavaScript symbol definitions.
 * Each pattern captures: (optional export) + keyword + name.
 */
const SYMBOL_PATTERNS: Array<{ kind: SymbolKind; regex: RegExp }> = [
  { kind: 'function',  regex: /^(export\s+)?(export\s+default\s+)?(async\s+)?function\s+(\w+)/  },
  { kind: 'class',     regex: /^(export\s+)?(export\s+default\s+)?class\s+(\w+)/                },
  { kind: 'interface', regex: /^(export\s+)?interface\s+(\w+)/                                   },
  { kind: 'type',      regex: /^(export\s+)?type\s+(\w+)\s*[=<]/                                },
  { kind: 'enum',      regex: /^(export\s+)?(const\s+)?enum\s+(\w+)/                            },
  { kind: 'const',     regex: /^(export\s+)?const\s+(\w+)\s*[=:]/                               },
];

/**
 * Extract the symbol name from a regex match, handling varying capture groups.
 */
function extractSymbolName(match: RegExpMatchArray, kind: SymbolKind): string {
  // Walk backwards through capture groups to find the last non-undefined word
  for (let i = match.length - 1; i >= 1; i--) {
    const group = match[i];
    if (group && /^\w+$/.test(group.trim())) {
      return group.trim();
    }
  }
  return match[0]; // fallback
}

/**
 * Check if a match represents an exported symbol.
 */
function isExported(line: string): boolean {
  return /^export\s/.test(line.trim());
}

/**
 * Find symbol definitions in the loaded codebase.
 */
function handleFindSymbols(args: Record<string, unknown>): ToolResult {
  if (!args.query || typeof args.query !== 'string') {
    return { error: 'Missing required parameter: query' };
  }

  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  const query = args.query.toLowerCase();
  const kindFilter = typeof args.kind === 'string' ? args.kind : 'all';
  const packageFilter = typeof args.package_name === 'string' ? args.package_name : '';
  const exportedOnly = args.exported_only === true;
  const maxResults = typeof args.maxResults === 'number' ? args.maxResults : 30;

  // Validate kind filter
  const validKinds = ['all', 'function', 'class', 'interface', 'type', 'enum', 'const'];
  if (!validKinds.includes(kindFilter)) {
    return { error: `Invalid kind filter: ${kindFilter}. Valid: ${validKinds.join(', ')}` };
  }

  const results: SymbolMatch[] = [];

  for (const file of loadedRepo.files) {
    // Only search TypeScript/JavaScript files for symbols
    if (!['typescript', 'javascript'].includes(file.language)) continue;

    // Filter by package
    if (packageFilter) {
      if (!file.path.startsWith(`packages/${packageFilter}/`)) continue;
    }

    const lines = file.content.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line) continue;

      for (const pattern of SYMBOL_PATTERNS) {
        // Filter by kind
        if (kindFilter !== 'all' && pattern.kind !== kindFilter) continue;

        const match = line.match(pattern.regex);
        if (!match) continue;

        const name = extractSymbolName(match, pattern.kind);
        const exported = isExported(line);

        // Filter by exported-only
        if (exportedOnly && !exported) continue;

        // Check if symbol name matches query (case-insensitive partial match)
        if (!name.toLowerCase().includes(query)) continue;

        results.push({
          name,
          kind: pattern.kind,
          exported,
          file: file.path,
          line: i + 1,
          context: line,
        });

        if (results.length >= maxResults) break;
      }

      if (results.length >= maxResults) break;
    }

    if (results.length >= maxResults) break;
  }

  return {
    result: JSON.stringify({
      query: args.query,
      kind: kindFilter,
      packageFilter: packageFilter || '(all)',
      exportedOnly,
      totalResults: results.length,
      symbols: results,
    }),
  };
}

/**
 * Analyze dependencies across workspace packages.
 */
function handleAnalyzeDeps(args: Record<string, unknown>): ToolResult {
  if (!loadedRepo) {
    return { error: 'Coding kit not loaded. Use coding_kit_load first.' };
  }

  const packageFilter = typeof args.package_name === 'string' ? args.package_name : '';

  try {
    // Find all package.json files
    const pkgJsonFiles = loadedRepo.files.filter((f) => {
      if (!f.path.endsWith('package.json')) return false;
      // If filtering by package, only include that package
      if (packageFilter) {
        return f.path === `packages/${packageFilter}/package.json`;
      }
      return true;
    });

    if (packageFilter && pkgJsonFiles.length === 0) {
      return { error: `Package not found: ${packageFilter}. Use coding_kit_status to see loaded packages.` };
    }

    const analysis: Array<{
      package: string;
      version: string;
      description: string;
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
      peerDependencies: Record<string, string>;
      internalDeps: string[];
      scripts: string[];
    }> = [];

    // Collect all workspace package names for cross-reference detection
    const workspacePackageNames = new Set<string>();
    for (const f of loadedRepo.files) {
      if (!f.path.endsWith('package.json')) continue;
      try {
        const pkg = JSON.parse(f.content);
        if (pkg.name) workspacePackageNames.add(pkg.name);
      } catch { /* skip malformed JSON */ }
    }

    for (const pkgFile of pkgJsonFiles) {
      try {
        const pkg = JSON.parse(pkgFile.content);
        const deps = pkg.dependencies || {};
        const devDeps = pkg.devDependencies || {};
        const peerDeps = pkg.peerDependencies || {};

        // Detect internal workspace cross-references
        const internalDeps: string[] = [];
        for (const depName of [...Object.keys(deps), ...Object.keys(devDeps), ...Object.keys(peerDeps)]) {
          if (workspacePackageNames.has(depName)) {
            internalDeps.push(depName);
          }
        }

        // Extract the package directory name from path
        const pathMatch = pkgFile.path.match(/^packages\/([^/]+)\/package\.json$/);
        const dirName = pathMatch ? pathMatch[1] : pkgFile.path;

        analysis.push({
          package: pkg.name || dirName,
          version: pkg.version || '0.0.0',
          description: pkg.description || '',
          dependencies: deps,
          devDependencies: devDeps,
          peerDependencies: peerDeps,
          internalDeps,
          scripts: Object.keys(pkg.scripts || {}),
        });
      } catch {
        // Skip malformed package.json
      }
    }

    // Also parse root package.json if present and no filter
    if (!packageFilter) {
      const rootPkg = loadedRepo.files.find((f) => f.path === 'package.json');
      if (rootPkg) {
        try {
          const pkg = JSON.parse(rootPkg.content);
          analysis.unshift({
            package: pkg.name || '(root)',
            version: pkg.version || '0.0.0',
            description: pkg.description || '',
            dependencies: pkg.dependencies || {},
            devDependencies: pkg.devDependencies || {},
            peerDependencies: pkg.peerDependencies || {},
            internalDeps: [],
            scripts: Object.keys(pkg.scripts || {}),
          });
        } catch { /* skip */ }
      }
    }

    // Summary statistics
    const allDeps = new Set<string>();
    for (const a of analysis) {
      for (const d of Object.keys(a.dependencies)) allDeps.add(d);
      for (const d of Object.keys(a.devDependencies)) allDeps.add(d);
    }

    return {
      result: JSON.stringify({
        packageFilter: packageFilter || '(all)',
        packagesAnalyzed: analysis.length,
        uniqueDependencies: allDeps.size,
        workspacePackages: Array.from(workspacePackageNames),
        packages: analysis,
      }),
    };
  } catch (err) {
    return { error: `Dependency analysis failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}
