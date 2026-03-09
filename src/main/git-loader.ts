/**
 * GitLoader — On-the-fly GitHub repository loading + code intelligence.
 *
 * Allows Agent Friday to clone any public (or authenticated private) GitHub repo,
 * index its structure, search its contents, and read individual files.
 * Inspired by GitNexus: repos are loaded into memory for fast agent querying.
 */

// Crypto Sprint 10: Removed `exec` import — all callers use `execFile` (no shell).
import { execFile } from 'child_process';
import { getSanitizedEnv } from './settings';
import { promises as fs } from 'fs';
import * as path from 'path';
import { app } from 'electron';

/* ── Types ─────────────────────────────────────────────────────────── */

export interface LoadedRepo {
  id: string;
  name: string;
  owner: string;
  branch: string;
  description: string;
  url: string;
  localPath: string;
  files: RepoFile[];
  tree: RepoTreeEntry[];
  loadedAt: number;
  totalSize: number;
}

export interface RepoFile {
  path: string;
  content: string;
  language: string;
  size: number;
}

export interface RepoTreeEntry {
  path: string;
  type: 'file' | 'directory';
  size?: number;
  language?: string;
}

export interface LoadOptions {
  branch?: string;
  sparse?: string[];        // Only load these paths (sparse checkout)
  maxFileSize?: number;      // Skip files larger than this (bytes), default 512KB
  includePatterns?: string[]; // Glob patterns to include
  excludePatterns?: string[]; // Glob patterns to exclude
  excludeOverrides?: string[]; // Remove these entries from DEFAULT_EXCLUDE (e.g. 'packages' for monorepos)
}

export interface SearchResult {
  file: string;
  line: number;
  content: string;
  context: string[];
}

/* ── Language Detection ─────────────────────────────────────────────── */

const EXTENSION_MAP: Record<string, string> = {
  '.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript', '.jsx': 'javascript',
  '.py': 'python', '.rb': 'ruby', '.rs': 'rust', '.go': 'go', '.java': 'java',
  '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp', '.cs': 'csharp',
  '.swift': 'swift', '.kt': 'kotlin', '.scala': 'scala', '.php': 'php',
  '.html': 'html', '.css': 'css', '.scss': 'scss', '.less': 'less',
  '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
  '.xml': 'xml', '.md': 'markdown', '.mdx': 'markdown',
  '.sql': 'sql', '.sh': 'shell', '.bash': 'shell', '.zsh': 'shell',
  '.ps1': 'powershell', '.bat': 'batch', '.cmd': 'batch',
  '.dockerfile': 'dockerfile', '.r': 'r', '.lua': 'lua', '.dart': 'dart',
  '.vue': 'vue', '.svelte': 'svelte', '.astro': 'astro',
  '.graphql': 'graphql', '.gql': 'graphql', '.proto': 'protobuf',
  '.tf': 'terraform', '.hcl': 'hcl', '.zig': 'zig', '.nim': 'nim',
  '.ex': 'elixir', '.exs': 'elixir', '.erl': 'erlang', '.hs': 'haskell',
  '.clj': 'clojure', '.lisp': 'lisp', '.ml': 'ocaml',
};

const BINARY_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp',
  '.mp3', '.wav', '.ogg', '.mp4', '.avi', '.mov', '.webm',
  '.zip', '.tar', '.gz', '.bz2', '.rar', '.7z',
  '.exe', '.dll', '.so', '.dylib', '.bin', '.dat',
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
  '.woff', '.woff2', '.ttf', '.eot', '.otf',
  '.pyc', '.pyo', '.class', '.o', '.obj',
  '.lock', '.sqlite', '.db',
]);

const DEFAULT_EXCLUDE = [
  'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
  'dist', 'build', '.next', '.nuxt', '.output', 'target',
  'vendor', 'packages', '.cache', '.parcel-cache',
  'coverage', '.nyc_output', '.pytest_cache',
  '.DS_Store', 'Thumbs.db',
];

function detectLanguage(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (EXTENSION_MAP[ext]) return EXTENSION_MAP[ext];
  const basename = path.basename(filePath).toLowerCase();
  if (basename === 'dockerfile') return 'dockerfile';
  if (basename === 'makefile') return 'makefile';
  if (basename === 'cmakelists.txt') return 'cmake';
  if (basename.endsWith('.env')) return 'env';
  return 'text';
}

function isBinary(filePath: string): boolean {
  const ext = path.extname(filePath).toLowerCase();
  return BINARY_EXTENSIONS.has(ext);
}

/* ── GitLoader Class ─────────────────────────────────────────────── */

class GitLoader {
  private repos = new Map<string, LoadedRepo>();
  private basePath: string;

  constructor() {
    this.basePath = path.join(app.getPath('userData'), 'git-loader');
  }

  async initialize(): Promise<void> {
    await fs.mkdir(this.basePath, { recursive: true });
  }

  /** Parse a GitHub URL into owner/name/branch */
  private parseGitHubUrl(url: string): { owner: string; name: string; defaultBranch?: string } {
    // Support various formats:
    // https://github.com/owner/repo
    // https://github.com/owner/repo.git
    // https://github.com/owner/repo/tree/branch
    // git@github.com:owner/repo.git
    // owner/repo

    let owner = '';
    let name = '';
    let defaultBranch: string | undefined;

    if (url.includes('github.com')) {
      const match = url.match(/github\.com[:/]([^/]+)\/([^/.]+)/);
      if (match) {
        owner = match[1];
        name = match[2];
      }
      // Check for /tree/branch
      const branchMatch = url.match(/\/tree\/([^/]+)/);
      if (branchMatch) {
        defaultBranch = branchMatch[1];
      }
    } else if (url.match(/^[^/]+\/[^/]+$/)) {
      // owner/repo shorthand
      const parts = url.split('/');
      owner = parts[0];
      name = parts[1];
    }

    return { owner, name: name.replace(/\.git$/, ''), defaultBranch };
  }

  /** Load a GitHub repository */
  async load(repoUrl: string, options: LoadOptions = {}): Promise<LoadedRepo> {
    const { owner, name, defaultBranch } = this.parseGitHubUrl(repoUrl);
    if (!owner || !name) {
      throw new Error(`Invalid GitHub URL: ${repoUrl}`);
    }

    const branch = options.branch || defaultBranch || 'main';
    const repoId = `${owner}/${name}@${branch}`;

    // Check if already loaded
    if (this.repos.has(repoId)) {
      return this.repos.get(repoId)!;
    }

    const cloneUrl = `https://github.com/${owner}/${name}.git`;
    const localPath = path.join(this.basePath, owner, name);

    // Clean up any existing clone
    await fs.rm(localPath, { recursive: true, force: true });
    await fs.mkdir(path.dirname(localPath), { recursive: true });

    // Shallow clone with depth 1 for speed
    // Crypto Sprint 9: Use execFileAsync (no shell) to prevent injection via branch name.
    console.log(`[GitLoader] Cloning ${cloneUrl} (branch: ${branch})...`);
    await this.execFileAsync(
      'git',
      ['clone', '--depth', '1', '--branch', branch, '--single-branch', cloneUrl, localPath],
      60000,
    );

    // Get repo description from git
    let description = '';
    try {
      // Try to get description from GitHub API (lightweight)
      const response = await fetch(`https://api.github.com/repos/${owner}/${name}`);
      if (response.ok) {
        const data = await response.json();
        description = data.description || '';
      }
    } catch {
      // API call is optional
    }

    // Walk the repo and index files
    const maxFileSize = options.maxFileSize || 512 * 1024; // 512KB default
    const overrides = new Set(options.excludeOverrides || []);
    const excludePatterns = [
      ...DEFAULT_EXCLUDE.filter((p) => !overrides.has(p)),
      ...(options.excludePatterns || []),
    ];
    const includePatterns = options.includePatterns || [];

    const files: RepoFile[] = [];
    const tree: RepoTreeEntry[] = [];
    let totalSize = 0;

    await this.walkDirectory(
      localPath,
      localPath,
      files,
      tree,
      maxFileSize,
      excludePatterns,
      includePatterns,
    );

    for (const f of files) {
      totalSize += f.size;
    }

    const repo: LoadedRepo = {
      id: repoId,
      name,
      owner,
      branch,
      description,
      url: cloneUrl,
      localPath,
      files,
      tree,
      loadedAt: Date.now(),
      totalSize,
    };

    this.repos.set(repoId, repo);
    console.log(`[GitLoader] Loaded ${owner}/${name}: ${files.length} files, ${(totalSize / 1024).toFixed(0)}KB`);

    return repo;
  }

  /** Recursively walk directory and collect files */
  private async walkDirectory(
    rootPath: string,
    currentPath: string,
    files: RepoFile[],
    tree: RepoTreeEntry[],
    maxFileSize: number,
    excludePatterns: string[],
    includePatterns: string[],
  ): Promise<void> {
    const entries = await fs.readdir(currentPath, { withFileTypes: true });

    for (const entry of entries) {
      const fullPath = path.join(currentPath, entry.name);
      const relativePath = path.relative(rootPath, fullPath).replace(/\\/g, '/');

      // Skip excluded directories/files
      if (excludePatterns.some(pattern => {
        if (entry.name === pattern) return true;
        if (relativePath.includes(`/${pattern}/`) || relativePath.startsWith(`${pattern}/`)) return true;
        return false;
      })) {
        continue;
      }

      // Skip .git
      if (entry.name === '.git') continue;

      if (entry.isDirectory()) {
        tree.push({ path: relativePath, type: 'directory' });
        await this.walkDirectory(rootPath, fullPath, files, tree, maxFileSize, excludePatterns, includePatterns);
      } else if (entry.isFile()) {
        // Skip binary files
        if (isBinary(fullPath)) continue;

        // Check include patterns (if specified, only include matching files)
        if (includePatterns.length > 0) {
          const matchesInclude = includePatterns.some(pattern => {
            if (pattern.startsWith('*.')) {
              return fullPath.endsWith(pattern.slice(1));
            }
            return relativePath.includes(pattern);
          });
          if (!matchesInclude) continue;
        }

        try {
          const stat = await fs.stat(fullPath);
          if (stat.size > maxFileSize) {
            tree.push({
              path: relativePath,
              type: 'file',
              size: stat.size,
              language: detectLanguage(fullPath),
            });
            continue; // Skip content but include in tree
          }

          const content = await fs.readFile(fullPath, 'utf-8');
          const language = detectLanguage(fullPath);

          files.push({
            path: relativePath,
            content,
            language,
            size: stat.size,
          });

          tree.push({
            path: relativePath,
            type: 'file',
            size: stat.size,
            language,
          });
        } catch {
          // Skip unreadable files
        }
      }
    }
  }

  /** Get the file tree for a loaded repo */
  getTree(repoId: string): RepoTreeEntry[] {
    const repo = this.repos.get(repoId);
    if (!repo) throw new Error(`Repo not loaded: ${repoId}`);
    return repo.tree;
  }

  /** Get a single file's content */
  getFile(repoId: string, filePath: string): RepoFile | null {
    const repo = this.repos.get(repoId);
    if (!repo) throw new Error(`Repo not loaded: ${repoId}`);
    return repo.files.find(f => f.path === filePath) || null;
  }

  /** Search file contents with regex or literal string */
  search(
    repoId: string,
    query: string,
    options: { filePattern?: string; maxResults?: number; contextLines?: number } = {},
  ): SearchResult[] {
    const repo = this.repos.get(repoId);
    if (!repo) throw new Error(`Repo not loaded: ${repoId}`);

    const maxResults = options.maxResults || 50;
    const contextLines = options.contextLines || 2;
    const results: SearchResult[] = [];

    // Crypto Sprint 6 (CRITICAL — ReDoS): Reject regex patterns with nested quantifiers
    // that could cause catastrophic backtracking when tested against every line of every file.
    let regex: RegExp;
    try {
      // Detect potentially dangerous patterns: nested quantifiers, alternation in quantifiers
      if (/(\+|\*|\?|\{)\s*\)(\+|\*|\?|\{)/.test(query) || /(\(.*\|.*\))(\+|\*|\{)/.test(query)) {
        // Fall back to safe literal search
        regex = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
      } else {
        regex = new RegExp(query, 'gi');
      }
    } catch {
      // If not valid regex, escape it for literal search
      regex = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    }

    for (const file of repo.files) {
      // Filter by file pattern
      if (options.filePattern) {
        if (options.filePattern.startsWith('*.')) {
          if (!file.path.endsWith(options.filePattern.slice(1))) continue;
        } else if (!file.path.includes(options.filePattern)) {
          continue;
        }
      }

      const lines = file.content.split('\n');
      for (let i = 0; i < lines.length; i++) {
        if (regex.test(lines[i])) {
          regex.lastIndex = 0; // Reset regex state
          const contextStart = Math.max(0, i - contextLines);
          const contextEnd = Math.min(lines.length - 1, i + contextLines);
          const context = lines.slice(contextStart, contextEnd + 1);

          results.push({
            file: file.path,
            line: i + 1,
            content: lines[i],
            context,
          });

          if (results.length >= maxResults) return results;
        }
      }
    }

    return results;
  }

  /** Get README content */
  getReadme(repoId: string): string | null {
    const repo = this.repos.get(repoId);
    if (!repo) throw new Error(`Repo not loaded: ${repoId}`);

    const readmeFile = repo.files.find(f =>
      /^readme\.(md|txt|rst|markdown)$/i.test(path.basename(f.path))
    );
    return readmeFile?.content || null;
  }

  /** Generate a structured summary of the repo */
  getSummary(repoId: string): {
    name: string;
    description: string;
    language: string;
    topics: string[];
    structure: string;
    keyFiles: string[];
    dependencies: Record<string, string>;
  } {
    const repo = this.repos.get(repoId);
    if (!repo) throw new Error(`Repo not loaded: ${repoId}`);

    // Detect primary language
    const langCounts: Record<string, number> = {};
    for (const file of repo.files) {
      if (file.language !== 'text' && file.language !== 'json' && file.language !== 'markdown') {
        langCounts[file.language] = (langCounts[file.language] || 0) + file.size;
      }
    }
    const primaryLang = Object.entries(langCounts)
      .sort((a, b) => b[1] - a[1])[0]?.[0] || 'unknown';

    // Find key files
    const keyFileNames = [
      'package.json', 'Cargo.toml', 'pyproject.toml', 'setup.py', 'go.mod',
      'pom.xml', 'build.gradle', 'Gemfile', 'composer.json',
      'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
      '.env.example', 'tsconfig.json', 'webpack.config.js', 'vite.config.ts',
      'tailwind.config.js', 'tailwind.config.ts',
    ];
    const keyFiles = repo.files
      .filter(f => keyFileNames.includes(path.basename(f.path)))
      .map(f => f.path);

    // Extract dependencies
    const dependencies: Record<string, string> = {};
    const pkgJson = repo.files.find(f => f.path === 'package.json');
    if (pkgJson) {
      try {
        const pkg = JSON.parse(pkgJson.content);
        Object.assign(dependencies, pkg.dependencies || {});
      } catch { /* skip */ }
    }

    const cargoToml = repo.files.find(f => f.path === 'Cargo.toml');
    if (cargoToml) {
      const depSection = cargoToml.content.match(/\[dependencies\]([\s\S]*?)(?:\[|$)/);
      if (depSection) {
        const lines = depSection[1].split('\n');
        for (const line of lines) {
          const match = line.match(/^(\w[\w-]*)\s*=/);
          if (match) dependencies[match[1]] = 'rust';
        }
      }
    }

    // Build top-level structure summary
    const topLevel = repo.tree
      .filter(e => !e.path.includes('/'))
      .map(e => `${e.type === 'directory' ? '📁' : '📄'} ${e.path}`)
      .slice(0, 30)
      .join('\n');

    // Detect topics from structure
    const topics: string[] = [];
    if (repo.files.some(f => f.path.includes('test') || f.path.includes('spec'))) topics.push('tested');
    if (repo.files.some(f => f.path.includes('.github/workflows'))) topics.push('ci/cd');
    if (repo.files.some(f => f.path.includes('docker'))) topics.push('containerized');
    if (repo.files.some(f => /\.(tsx|jsx)$/.test(f.path))) topics.push('react');
    if (repo.files.some(f => f.path.includes('.vue'))) topics.push('vue');
    if (repo.files.some(f => f.path.includes('.svelte'))) topics.push('svelte');
    if (keyFiles.includes('tailwind.config.js') || keyFiles.includes('tailwind.config.ts')) topics.push('tailwind');

    return {
      name: `${repo.owner}/${repo.name}`,
      description: repo.description,
      language: primaryLang,
      topics,
      structure: topLevel,
      keyFiles,
      dependencies,
    };
  }

  /** List all loaded repos */
  listLoaded(): Array<{
    id: string;
    name: string;
    owner: string;
    branch: string;
    files: number;
    loadedAt: number;
  }> {
    return Array.from(this.repos.values()).map(r => ({
      id: r.id,
      name: r.name,
      owner: r.owner,
      branch: r.branch,
      files: r.files.length,
      loadedAt: r.loadedAt,
    }));
  }

  /** Unload a repo from memory and clean up disk */
  async unload(repoId: string): Promise<boolean> {
    const repo = this.repos.get(repoId);
    if (!repo) return false;

    this.repos.delete(repoId);
    try {
      await fs.rm(repo.localPath, { recursive: true, force: true });
    } catch {
      // Cleanup is best-effort
    }
    return true;
  }

  /**
   * Crypto Sprint 9: Shell-free command execution using execFile.
   * Arguments are passed as an array — no shell interpolation, no injection risk.
   * Use this instead of execAsync for any command with untrusted arguments.
   */
  private execFileAsync(cmd: string, args: string[], timeoutMs = 30000): Promise<string> {
    return new Promise((resolve, reject) => {
      execFile(cmd, args, { timeout: timeoutMs, maxBuffer: 10 * 1024 * 1024, env: getSanitizedEnv() as NodeJS.ProcessEnv }, (err, stdout, stderr) => {
        if (err) {
          reject(new Error(`Command failed: ${err.message}\n${stderr}`));
        } else {
          resolve(stdout);
        }
      });
    });
  }

  // Crypto Sprint 10: Removed dead `execAsync` shell method.
  // All callers migrated to `execFileAsync` (Sprint 9) — no remaining references.
}

/* ── Singleton ─────────────────────────────────────────────────────── */

export const gitLoader = new GitLoader();

/* ── Tool Declarations ─────────────────────────────────────────────── */

export function buildGitLoaderToolDeclarations(): Array<{
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}> {
  return [
    {
      name: 'git_load_repo',
      description: 'Load a GitHub repository for code analysis. Clones the repo, indexes all files, and makes them searchable. Use this when you need to understand, analyze, or reference code from any GitHub repository. Supports public repos and authenticated private repos.',
      parameters: {
        type: 'object',
        properties: {
          repo_url: {
            type: 'string',
            description: 'GitHub repository URL or shorthand (e.g., "https://github.com/owner/repo", "owner/repo")',
          },
          branch: {
            type: 'string',
            description: 'Branch to load (default: main)',
          },
          include_patterns: {
            type: 'array',
            items: { type: 'string' },
            description: 'Only include files matching these patterns (e.g., ["*.ts", "*.py"]). If empty, includes all non-binary files.',
          },
          exclude_patterns: {
            type: 'array',
            items: { type: 'string' },
            description: 'Exclude files/directories matching these patterns (node_modules, .git, etc. are excluded by default)',
          },
        },
        required: ['repo_url'],
      },
    },
    {
      name: 'git_get_tree',
      description: 'Get the file/directory tree of a loaded GitHub repository. Shows the complete structure with file sizes and detected languages.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID from git_load_repo (format: "owner/name@branch")',
          },
        },
        required: ['repo_id'],
      },
    },
    {
      name: 'git_get_file',
      description: 'Read the contents of a specific file from a loaded GitHub repository.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID from git_load_repo',
          },
          file_path: {
            type: 'string',
            description: 'Path to the file within the repository (e.g., "src/main.ts", "README.md")',
          },
        },
        required: ['repo_id', 'file_path'],
      },
    },
    {
      name: 'git_search',
      description: 'Search through all files in a loaded GitHub repository. Supports regex and literal string search with context lines.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID from git_load_repo',
          },
          query: {
            type: 'string',
            description: 'Search query (supports regex). E.g., "function.*auth", "TODO", "import.*react"',
          },
          file_pattern: {
            type: 'string',
            description: 'Filter to files matching this pattern (e.g., "*.ts", "src/")',
          },
          max_results: {
            type: 'number',
            description: 'Maximum results to return (default: 50)',
          },
        },
        required: ['repo_id', 'query'],
      },
    },
    {
      name: 'git_get_readme',
      description: 'Get the README file content from a loaded GitHub repository.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID from git_load_repo',
          },
        },
        required: ['repo_id'],
      },
    },
    {
      name: 'git_get_summary',
      description: 'Get a structured summary of a loaded GitHub repository: primary language, dependencies, key files, detected frameworks, and directory structure.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID from git_load_repo',
          },
        },
        required: ['repo_id'],
      },
    },
    {
      name: 'git_list_loaded',
      description: 'List all currently loaded GitHub repositories.',
      parameters: {
        type: 'object',
        properties: {},
        required: [],
      },
    },
    {
      name: 'git_unload_repo',
      description: 'Unload a GitHub repository from memory and clean up disk space.',
      parameters: {
        type: 'object',
        properties: {
          repo_id: {
            type: 'string',
            description: 'Repository ID to unload',
          },
        },
        required: ['repo_id'],
      },
    },
  ];
}
