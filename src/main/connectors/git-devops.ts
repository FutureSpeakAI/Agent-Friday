/**
 * git-devops.ts — Comprehensive Git, Docker, npm, and Cloud CLI connector.
 *
 * Provides an AI agent with full DevOps tooling capabilities. All commands
 * run as child processes with timeouts, output truncation, and safety guards
 * against destructive operations.
 *
 * Exports:
 *   TOOLS    — Array of tool declarations for the agent tool registry
 *   execute  — Async handler that dispatches tool calls by name
 *   detect   — Async check for baseline availability (git)
 */

import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: 'object';
    properties: Record<string, any>;
    required: string[];
  };
}

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum characters returned in any single tool result */
const MAX_OUTPUT_CHARS = 8000;

/** Default timeout for child process execution (30 seconds) */
const EXEC_TIMEOUT_MS = 30_000;

/** Git diff output cap (smaller to stay readable) */
const DIFF_CHAR_LIMIT = 5000;

/** Patterns that should never be executed through the cloud CLI */
const DANGEROUS_CLOUD_PATTERNS = [
  /\bdelete\b/i,
  /\bdestroy\b/i,
  /\bterminate\b/i,
  /\bremove\b/i,
  /\bpurge\b/i,
  /\bformat\b/i,
  /\bdrop\b/i,
];

/** Docker command patterns that are too destructive to run blindly */
const DANGEROUS_DOCKER_PATTERNS = [
  /docker\s+rm\s+-f\s+\$\(/i,
  /docker\s+rmi\s+-f\s+\$\(/i,
  /docker\s+system\s+prune\s+-a/i,
  /docker\s+volume\s+prune/i,
];

// ---------------------------------------------------------------------------
// Helper: run a shell command safely
// ---------------------------------------------------------------------------

/**
 * Execute a shell command and return its stdout.
 * Throws on non-zero exit or timeout.
 */
function run(
  cmd: string,
  opts: { cwd?: string; timeout?: number } = {}
): string {
  const { cwd, timeout = EXEC_TIMEOUT_MS } = opts;
  try {
    const output = execSync(cmd, {
      cwd,
      timeout,
      encoding: 'utf-8',
      maxBuffer: 10 * 1024 * 1024, // 10 MB
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    return output;
  } catch (err: any) {
    // execSync throws on non-zero exit; stderr is often the useful part
    const stderr = err.stderr?.toString?.() ?? '';
    const stdout = err.stdout?.toString?.() ?? '';
    throw new Error(stderr || stdout || err.message);
  }
}

/**
 * Truncate output to a character limit and append a notice if clipped.
 */
function truncate(text: string, limit: number = MAX_OUTPUT_CHARS): string {
  if (text.length <= limit) return text;
  return text.slice(0, limit) + `\n\n--- Output truncated (${text.length} chars total, showing first ${limit}) ---`;
}

/**
 * Detect which package manager a project uses by checking lock files.
 * Falls back to 'npm' if nothing is detected.
 */
function detectPackageManager(cwd: string): 'npm' | 'yarn' | 'pnpm' {
  if (fs.existsSync(path.join(cwd, 'pnpm-lock.yaml'))) return 'pnpm';
  if (fs.existsSync(path.join(cwd, 'yarn.lock'))) return 'yarn';
  // package-lock.json or default
  return 'npm';
}

/**
 * Wrap a successful result with truncation.
 */
function ok(text: string, limit?: number): ToolResult {
  return { result: truncate(text.trim(), limit) };
}

/**
 * Wrap an error result.
 */
function fail(msg: string): ToolResult {
  return { error: msg };
}

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ── Git ──────────────────────────────────────────────────────────────────
  {
    name: 'git_status',
    description:
      'Get the current Git repository status including branch name, staged files, changed files, and untracked files.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
      },
      required: ['repo_path'],
    },
  },
  {
    name: 'git_log',
    description:
      'View commit history. Returns the most recent commits with optional filtering by author or date.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        count: { type: 'number', description: 'Number of commits to show (default: 20)' },
        oneline: { type: 'boolean', description: 'Use compact one-line format (default: true)' },
        author: { type: 'string', description: 'Filter commits by author name or email' },
        since: { type: 'string', description: 'Show commits after this date (e.g. "2024-01-01", "2 weeks ago")' },
      },
      required: ['repo_path'],
    },
  },
  {
    name: 'git_diff',
    description:
      'Show file changes in the working tree or staging area. Output is capped at 5000 characters.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        staged: { type: 'boolean', description: 'Show staged (cached) changes instead of unstaged' },
        file: { type: 'string', description: 'Limit diff to a specific file path' },
      },
      required: ['repo_path'],
    },
  },
  {
    name: 'git_commit',
    description:
      'Stage files and create a commit. Provide specific files to stage, or set all=true to stage everything.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        message: { type: 'string', description: 'Commit message' },
        files: {
          type: 'array',
          items: { type: 'string' },
          description: 'Specific file paths to stage before committing',
        },
        all: { type: 'boolean', description: 'Stage all changes (git add -A) before committing' },
      },
      required: ['repo_path', 'message'],
    },
  },
  {
    name: 'git_branch',
    description:
      'List, create, switch to, or delete branches.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        action: {
          type: 'string',
          enum: ['list', 'create', 'switch', 'delete'],
          description: 'Branch operation to perform',
        },
        branch_name: { type: 'string', description: 'Branch name (required for create/switch/delete)' },
      },
      required: ['repo_path', 'action'],
    },
  },
  {
    name: 'git_stash',
    description:
      'Stash or restore uncommitted changes. Useful for quickly saving work-in-progress.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        action: {
          type: 'string',
          enum: ['push', 'pop', 'list', 'drop'],
          description: 'Stash operation to perform',
        },
        message: { type: 'string', description: 'Optional message for stash push' },
      },
      required: ['repo_path', 'action'],
    },
  },
  {
    name: 'git_pull',
    description:
      'Pull the latest changes from the remote tracking branch.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        rebase: { type: 'boolean', description: 'Use --rebase instead of merge' },
      },
      required: ['repo_path'],
    },
  },
  {
    name: 'git_push',
    description:
      'Push local commits to the remote. WARNING: force push (force=true) rewrites remote history and can cause data loss for collaborators. Never force-push to main/master without extreme caution.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        force: { type: 'boolean', description: 'Force push (DANGEROUS — rewrites remote history)' },
        set_upstream: { type: 'boolean', description: 'Set upstream tracking (-u flag)' },
        branch: { type: 'string', description: 'Branch to push (defaults to current branch)' },
      },
      required: ['repo_path'],
    },
  },
  {
    name: 'git_clone',
    description:
      'Clone a remote Git repository to a local directory. Supports shallow clones for faster downloads.',
    parameters: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Repository URL to clone (HTTPS or SSH)' },
        destination: { type: 'string', description: 'Local directory path for the clone' },
        depth: { type: 'number', description: 'Create a shallow clone with this many commits of history' },
      },
      required: ['url'],
    },
  },
  {
    name: 'git_blame',
    description:
      'Show line-by-line authorship information for a file, including who last modified each line and when.',
    parameters: {
      type: 'object',
      properties: {
        repo_path: { type: 'string', description: 'Absolute path to the Git repository root' },
        file: { type: 'string', description: 'File path relative to the repo root' },
        lines: { type: 'string', description: 'Line range to blame, format: "start,end" (e.g. "10,20")' },
      },
      required: ['repo_path', 'file'],
    },
  },

  // ── Docker ───────────────────────────────────────────────────────────────
  {
    name: 'docker_ps',
    description:
      'List Docker containers. By default shows only running containers; set all=true to include stopped ones.',
    parameters: {
      type: 'object',
      properties: {
        all: { type: 'boolean', description: 'Include stopped containers' },
      },
      required: [],
    },
  },
  {
    name: 'docker_images',
    description:
      'List all locally available Docker images with repository, tag, size, and creation date.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'docker_run',
    description:
      'Create and start a new Docker container from an image. Supports port mapping, volume mounts, environment variables, and custom commands.',
    parameters: {
      type: 'object',
      properties: {
        image: { type: 'string', description: 'Docker image name (e.g. "nginx:latest")' },
        name: { type: 'string', description: 'Container name' },
        ports: {
          type: 'array',
          items: { type: 'string' },
          description: 'Port mappings (e.g. ["8080:80", "3000:3000"])',
        },
        volumes: {
          type: 'array',
          items: { type: 'string' },
          description: 'Volume mounts (e.g. ["./data:/app/data"])',
        },
        env: {
          type: 'object',
          description: 'Environment variables as key-value pairs',
        },
        detach: { type: 'boolean', description: 'Run container in the background (default: true)' },
        command: { type: 'string', description: 'Override the default container command' },
      },
      required: ['image'],
    },
  },
  {
    name: 'docker_compose',
    description:
      'Run Docker Compose operations: up, down, logs, ps, or build. Requires a docker-compose.yml in the working directory or specified via compose_file.',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['up', 'down', 'logs', 'ps', 'build'],
          description: 'Compose action to perform',
        },
        compose_file: { type: 'string', description: 'Path to docker-compose.yml (if not in current directory)' },
        service: { type: 'string', description: 'Target a specific service' },
        detach: { type: 'boolean', description: 'Run in detached mode (for "up" action)' },
      },
      required: ['action'],
    },
  },
  {
    name: 'docker_exec',
    description:
      'Execute a command inside a running Docker container.',
    parameters: {
      type: 'object',
      properties: {
        container: { type: 'string', description: 'Container name or ID' },
        command: { type: 'string', description: 'Command to execute inside the container' },
        interactive: { type: 'boolean', description: 'Allocate a pseudo-TTY (-it flags)' },
      },
      required: ['container', 'command'],
    },
  },
  {
    name: 'docker_logs',
    description:
      'Retrieve logs from a Docker container. Returns the most recent lines by default.',
    parameters: {
      type: 'object',
      properties: {
        container: { type: 'string', description: 'Container name or ID' },
        tail: { type: 'number', description: 'Number of recent lines to show (default: 100)' },
        follow: { type: 'boolean', description: 'Stream logs in real time (not recommended for agent use)' },
      },
      required: ['container'],
    },
  },

  // ── Package Manager ──────────────────────────────────────────────────────
  {
    name: 'npm_run',
    description:
      'Run a package.json script using npm, yarn, or pnpm. Auto-detects the package manager from lock files if not specified.',
    parameters: {
      type: 'object',
      properties: {
        script: { type: 'string', description: 'Script name from package.json (e.g. "dev", "build", "test")' },
        cwd: { type: 'string', description: 'Project root directory containing package.json' },
        package_manager: {
          type: 'string',
          enum: ['npm', 'yarn', 'pnpm'],
          description: 'Package manager to use (auto-detected if omitted)',
        },
      },
      required: ['script', 'cwd'],
    },
  },
  {
    name: 'npm_install',
    description:
      'Install dependencies. If packages are specified, installs those; otherwise installs all deps from package.json. Auto-detects package manager from lock files.',
    parameters: {
      type: 'object',
      properties: {
        packages: {
          type: 'array',
          items: { type: 'string' },
          description: 'Specific packages to install (e.g. ["express", "lodash@4"])',
        },
        cwd: { type: 'string', description: 'Project root directory' },
        dev: { type: 'boolean', description: 'Install as devDependency' },
        package_manager: {
          type: 'string',
          enum: ['npm', 'yarn', 'pnpm'],
          description: 'Package manager to use (auto-detected if omitted)',
        },
      },
      required: ['cwd'],
    },
  },
  {
    name: 'npm_search',
    description:
      'Search the npm registry for packages matching a query. Returns top 10 results with name, description, and version.',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search terms (e.g. "express middleware cors")' },
      },
      required: ['query'],
    },
  },

  // ── Cloud CLI ────────────────────────────────────────────────────────────
  {
    name: 'cloud_cli',
    description:
      'Run a cloud provider CLI command (AWS, Azure, or GCloud). SAFETY: Destructive commands (delete, destroy, terminate, remove, purge) are blocked. The agent should always confirm with the user before running commands that modify infrastructure or incur costs.',
    parameters: {
      type: 'object',
      properties: {
        provider: {
          type: 'string',
          enum: ['aws', 'az', 'gcloud'],
          description: 'Cloud provider CLI to use',
        },
        command: {
          type: 'string',
          description: 'CLI command arguments (without the provider prefix, e.g. "s3 ls" for AWS)',
        },
      },
      required: ['provider', 'command'],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool Implementations
// ---------------------------------------------------------------------------

// ── Git Tools ──────────────────────────────────────────────────────────────

function gitStatus(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;

  try {
    const porcelain = run('git status --porcelain', { cwd: repoPath });
    const branch = run('git branch --show-current', { cwd: repoPath }).trim();

    // Parse porcelain output into categories
    const staged: string[] = [];
    const modified: string[] = [];
    const untracked: string[] = [];

    for (const line of porcelain.split('\n')) {
      if (!line.trim()) continue;
      const index = line[0];
      const worktree = line[1];
      const file = line.slice(3);

      // Index column: staged changes
      if (index && index !== ' ' && index !== '?') {
        staged.push(`${index} ${file}`);
      }
      // Worktree column: unstaged modifications
      if (worktree && worktree !== ' ' && worktree !== '?') {
        modified.push(file);
      }
      // Untracked files
      if (index === '?' && worktree === '?') {
        untracked.push(file);
      }
    }

    const sections: string[] = [`Branch: ${branch || '(detached HEAD)'}`];

    if (staged.length > 0) {
      sections.push(`\nStaged (${staged.length}):\n  ${staged.join('\n  ')}`);
    }
    if (modified.length > 0) {
      sections.push(`\nModified (${modified.length}):\n  ${modified.join('\n  ')}`);
    }
    if (untracked.length > 0) {
      sections.push(`\nUntracked (${untracked.length}):\n  ${untracked.join('\n  ')}`);
    }
    if (staged.length === 0 && modified.length === 0 && untracked.length === 0) {
      sections.push('\nWorking tree clean.');
    }

    return ok(sections.join('\n'));
  } catch (err: any) {
    return fail(`git status failed: ${err.message}`);
  }
}

function gitLog(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const count = (args.count as number) ?? 20;
  const oneline = (args.oneline as boolean) ?? true;
  const author = args.author as string | undefined;
  const since = args.since as string | undefined;

  try {
    const parts = ['git log', `--max-count=${count}`];

    if (oneline) {
      parts.push('--oneline', '--decorate');
    } else {
      parts.push('--format=medium');
    }
    if (author) {
      parts.push(`--author="${author}"`);
    }
    if (since) {
      parts.push(`--since="${since}"`);
    }

    const output = run(parts.join(' '), { cwd: repoPath });
    return ok(output || 'No commits found matching the criteria.');
  } catch (err: any) {
    return fail(`git log failed: ${err.message}`);
  }
}

function gitDiff(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const staged = args.staged as boolean | undefined;
  const file = args.file as string | undefined;

  try {
    const parts = ['git diff'];
    if (staged) parts.push('--cached');
    if (file) parts.push('--', `"${file}"`);

    const output = run(parts.join(' '), { cwd: repoPath });
    if (!output.trim()) {
      return ok(staged ? 'No staged changes.' : 'No unstaged changes.');
    }
    return ok(output, DIFF_CHAR_LIMIT);
  } catch (err: any) {
    return fail(`git diff failed: ${err.message}`);
  }
}

function gitCommit(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const message = args.message as string;
  const files = args.files as string[] | undefined;
  const all = args.all as boolean | undefined;

  try {
    // Stage files
    if (files && files.length > 0) {
      // Stage specific files one at a time to handle paths with spaces
      for (const f of files) {
        run(`git add "${f}"`, { cwd: repoPath });
      }
    } else if (all) {
      run('git add -A', { cwd: repoPath });
    }

    // Escape double quotes in the commit message
    const safeMessage = message.replace(/"/g, '\\"');
    const output = run(`git commit -m "${safeMessage}"`, { cwd: repoPath });
    return ok(output);
  } catch (err: any) {
    return fail(`git commit failed: ${err.message}`);
  }
}

function gitBranch(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const action = args.action as 'list' | 'create' | 'switch' | 'delete';
  const branchName = args.branch_name as string | undefined;

  try {
    switch (action) {
      case 'list': {
        const output = run('git branch -a --no-color', { cwd: repoPath });
        return ok(output || 'No branches found.');
      }
      case 'create': {
        if (!branchName) return fail('branch_name is required for create action');
        const output = run(`git branch "${branchName}"`, { cwd: repoPath });
        return ok(output || `Branch "${branchName}" created.`);
      }
      case 'switch': {
        if (!branchName) return fail('branch_name is required for switch action');
        const output = run(`git checkout "${branchName}"`, { cwd: repoPath });
        return ok(output || `Switched to branch "${branchName}".`);
      }
      case 'delete': {
        if (!branchName) return fail('branch_name is required for delete action');
        // Use -d (safe delete) — refuses to delete unmerged branches
        const output = run(`git branch -d "${branchName}"`, { cwd: repoPath });
        return ok(output || `Branch "${branchName}" deleted.`);
      }
      default:
        return fail(`Unknown branch action: ${action}`);
    }
  } catch (err: any) {
    return fail(`git branch ${action} failed: ${err.message}`);
  }
}

function gitStash(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const action = args.action as 'push' | 'pop' | 'list' | 'drop';
  const message = args.message as string | undefined;

  try {
    switch (action) {
      case 'push': {
        const parts = ['git stash push'];
        if (message) parts.push(`-m "${message.replace(/"/g, '\\"')}"`);
        const output = run(parts.join(' '), { cwd: repoPath });
        return ok(output || 'Changes stashed.');
      }
      case 'pop': {
        const output = run('git stash pop', { cwd: repoPath });
        return ok(output || 'Stash applied and dropped.');
      }
      case 'list': {
        const output = run('git stash list', { cwd: repoPath });
        return ok(output || 'No stashes found.');
      }
      case 'drop': {
        const output = run('git stash drop', { cwd: repoPath });
        return ok(output || 'Most recent stash dropped.');
      }
      default:
        return fail(`Unknown stash action: ${action}`);
    }
  } catch (err: any) {
    return fail(`git stash ${action} failed: ${err.message}`);
  }
}

function gitPull(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const rebase = args.rebase as boolean | undefined;

  try {
    const cmd = rebase ? 'git pull --rebase' : 'git pull';
    const output = run(cmd, { cwd: repoPath });
    return ok(output || 'Already up to date.');
  } catch (err: any) {
    return fail(`git pull failed: ${err.message}`);
  }
}

function gitPush(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const force = args.force as boolean | undefined;
  const setUpstream = args.set_upstream as boolean | undefined;
  const branch = args.branch as string | undefined;

  try {
    // Safety: refuse to force-push to main/master
    if (force) {
      const currentBranch = branch || run('git branch --show-current', { cwd: repoPath }).trim();
      if (['main', 'master'].includes(currentBranch)) {
        return fail(
          `SAFETY BLOCK: Refusing to force-push to "${currentBranch}". ` +
          'Force-pushing to the primary branch can destroy collaborator work. ' +
          'If this is truly intended, please push manually from the terminal.'
        );
      }
    }

    const parts = ['git push'];
    if (force) parts.push('--force');
    if (setUpstream) parts.push('-u origin');
    if (branch) parts.push(branch);

    const output = run(parts.join(' '), { cwd: repoPath });
    return ok(output || 'Push completed successfully.');
  } catch (err: any) {
    return fail(`git push failed: ${err.message}`);
  }
}

function gitClone(args: Record<string, unknown>): ToolResult {
  const url = args.url as string;
  const destination = args.destination as string | undefined;
  const depth = args.depth as number | undefined;

  try {
    const parts = ['git clone'];
    if (depth && depth > 0) parts.push(`--depth ${depth}`);
    parts.push(`"${url}"`);
    if (destination) parts.push(`"${destination}"`);

    const output = run(parts.join(' '), { timeout: 120_000 }); // allow 2 min for large repos
    return ok(output || `Repository cloned from ${url}`);
  } catch (err: any) {
    return fail(`git clone failed: ${err.message}`);
  }
}

function gitBlame(args: Record<string, unknown>): ToolResult {
  const repoPath = args.repo_path as string;
  const file = args.file as string;
  const lines = args.lines as string | undefined;

  try {
    const parts = ['git blame'];
    if (lines) {
      // Format: "start,end" => "-L start,end"
      const [start, end] = lines.split(',').map((s) => s.trim());
      if (start && end) {
        parts.push(`-L ${start},${end}`);
      }
    }
    parts.push(`-- "${file}"`);

    const output = run(parts.join(' '), { cwd: repoPath });
    return ok(output || 'No blame output.');
  } catch (err: any) {
    return fail(`git blame failed: ${err.message}`);
  }
}

// ── Docker Tools ───────────────────────────────────────────────────────────

function dockerPs(args: Record<string, unknown>): ToolResult {
  const all = args.all as boolean | undefined;

  try {
    const cmd = all
      ? 'docker ps -a --format "table {{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}"'
      : 'docker ps --format "table {{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}"';
    const output = run(cmd);
    return ok(output || 'No containers found.');
  } catch (err: any) {
    return fail(`docker ps failed: ${err.message}`);
  }
}

function dockerImages(): ToolResult {
  try {
    const output = run(
      'docker images --format "table {{.Repository}}\\t{{.Tag}}\\t{{.ID}}\\t{{.Size}}\\t{{.CreatedSince}}"'
    );
    return ok(output || 'No images found.');
  } catch (err: any) {
    return fail(`docker images failed: ${err.message}`);
  }
}

function dockerRun(args: Record<string, unknown>): ToolResult {
  const image = args.image as string;
  const name = args.name as string | undefined;
  const ports = args.ports as string[] | undefined;
  const volumes = args.volumes as string[] | undefined;
  const env = args.env as Record<string, string> | undefined;
  const detach = (args.detach as boolean) ?? true;
  const command = args.command as string | undefined;

  try {
    const parts = ['docker run'];

    if (detach) parts.push('-d');
    if (name) parts.push(`--name "${name}"`);

    if (ports) {
      for (const p of ports) parts.push(`-p ${p}`);
    }
    if (volumes) {
      for (const v of volumes) parts.push(`-v "${v}"`);
    }
    if (env) {
      for (const [k, v] of Object.entries(env)) {
        parts.push(`-e "${k}=${v}"`);
      }
    }

    parts.push(image);
    if (command) parts.push(command);

    const fullCmd = parts.join(' ');

    // Safety: block destructive patterns
    for (const pattern of DANGEROUS_DOCKER_PATTERNS) {
      if (pattern.test(fullCmd)) {
        return fail(`SAFETY BLOCK: This docker run command matches a dangerous pattern. Please review manually.`);
      }
    }

    const output = run(fullCmd);
    return ok(output || 'Container started.');
  } catch (err: any) {
    return fail(`docker run failed: ${err.message}`);
  }
}

function dockerCompose(args: Record<string, unknown>): ToolResult {
  const action = args.action as 'up' | 'down' | 'logs' | 'ps' | 'build';
  const composeFile = args.compose_file as string | undefined;
  const service = args.service as string | undefined;
  const detach = args.detach as boolean | undefined;

  try {
    const parts = ['docker compose'];
    if (composeFile) parts.push(`-f "${composeFile}"`);

    switch (action) {
      case 'up':
        parts.push('up');
        if (detach) parts.push('-d');
        if (service) parts.push(service);
        break;
      case 'down':
        parts.push('down');
        break;
      case 'logs':
        parts.push('logs');
        if (service) parts.push(service);
        parts.push('--tail=100');
        break;
      case 'ps':
        parts.push('ps');
        break;
      case 'build':
        parts.push('build');
        if (service) parts.push(service);
        break;
      default:
        return fail(`Unknown compose action: ${action}`);
    }

    // Compose up/build can take a while
    const timeout = ['up', 'build'].includes(action) ? 120_000 : EXEC_TIMEOUT_MS;
    const output = run(parts.join(' '), { timeout });
    return ok(output || `docker compose ${action} completed.`);
  } catch (err: any) {
    return fail(`docker compose ${action} failed: ${err.message}`);
  }
}

function dockerExec(args: Record<string, unknown>): ToolResult {
  const container = args.container as string;
  const command = args.command as string;
  const interactive = args.interactive as boolean | undefined;

  try {
    const flags = interactive ? '-it' : '-i';
    const output = run(`docker exec ${flags} ${container} ${command}`);
    return ok(output || 'Command executed (no output).');
  } catch (err: any) {
    return fail(`docker exec failed: ${err.message}`);
  }
}

function dockerLogs(args: Record<string, unknown>): ToolResult {
  const container = args.container as string;
  const tail = (args.tail as number) ?? 100;
  // Note: follow=true is not practical in synchronous execSync; we always snapshot
  const follow = args.follow as boolean | undefined;

  try {
    if (follow) {
      return fail('Streaming logs (follow=true) is not supported in this synchronous connector. Use tail instead.');
    }
    const output = run(`docker logs --tail ${tail} ${container}`);
    return ok(output || 'No logs available.');
  } catch (err: any) {
    return fail(`docker logs failed: ${err.message}`);
  }
}

// ── Package Manager Tools ──────────────────────────────────────────────────

function npmRun(args: Record<string, unknown>): ToolResult {
  const script = args.script as string;
  const cwd = args.cwd as string;
  const pm = (args.package_manager as 'npm' | 'yarn' | 'pnpm') ?? detectPackageManager(cwd);

  try {
    let cmd: string;
    switch (pm) {
      case 'yarn':
        cmd = `yarn ${script}`;
        break;
      case 'pnpm':
        cmd = `pnpm run ${script}`;
        break;
      case 'npm':
      default:
        cmd = `npm run ${script}`;
        break;
    }

    const output = run(cmd, { cwd, timeout: 60_000 });
    return ok(output || `Script "${script}" completed.`);
  } catch (err: any) {
    return fail(`${pm} run ${script} failed: ${err.message}`);
  }
}

function npmInstall(args: Record<string, unknown>): ToolResult {
  const packages = args.packages as string[] | undefined;
  const cwd = args.cwd as string;
  const dev = args.dev as boolean | undefined;
  const pm = (args.package_manager as 'npm' | 'yarn' | 'pnpm') ?? detectPackageManager(cwd);

  try {
    let cmd: string;

    if (packages && packages.length > 0) {
      // Install specific packages
      const pkgList = packages.join(' ');
      switch (pm) {
        case 'yarn':
          cmd = `yarn add ${dev ? '--dev ' : ''}${pkgList}`;
          break;
        case 'pnpm':
          cmd = `pnpm add ${dev ? '-D ' : ''}${pkgList}`;
          break;
        case 'npm':
        default:
          cmd = `npm install ${dev ? '--save-dev ' : ''}${pkgList}`;
          break;
      }
    } else {
      // Install all dependencies
      switch (pm) {
        case 'yarn':
          cmd = 'yarn install';
          break;
        case 'pnpm':
          cmd = 'pnpm install';
          break;
        case 'npm':
        default:
          cmd = 'npm install';
          break;
      }
    }

    const output = run(cmd, { cwd, timeout: 120_000 }); // allow 2 min for installs
    return ok(output || 'Install completed successfully.');
  } catch (err: any) {
    return fail(`${pm} install failed: ${err.message}`);
  }
}

function npmSearch(args: Record<string, unknown>): ToolResult {
  const query = args.query as string;

  try {
    const output = run(`npm search "${query}" --json`, { timeout: 15_000 });

    // Parse JSON results and format top 10
    let results: any[];
    try {
      results = JSON.parse(output);
    } catch {
      return ok(output); // if not valid JSON, return raw
    }

    const top10 = results.slice(0, 10);
    if (top10.length === 0) {
      return ok(`No packages found for "${query}".`);
    }

    const formatted = top10.map((pkg: any, i: number) => {
      return [
        `${i + 1}. ${pkg.name}@${pkg.version}`,
        `   ${pkg.description || '(no description)'}`,
        `   keywords: ${(pkg.keywords || []).join(', ') || 'none'}`,
        `   date: ${pkg.date || 'unknown'}`,
      ].join('\n');
    });

    return ok(`Search results for "${query}":\n\n${formatted.join('\n\n')}`);
  } catch (err: any) {
    return fail(`npm search failed: ${err.message}`);
  }
}

// ── Cloud CLI ──────────────────────────────────────────────────────────────

function cloudCli(args: Record<string, unknown>): ToolResult {
  const provider = args.provider as 'aws' | 'az' | 'gcloud';
  const command = args.command as string;

  // Safety: block destructive commands
  for (const pattern of DANGEROUS_CLOUD_PATTERNS) {
    if (pattern.test(command)) {
      return fail(
        `SAFETY BLOCK: The command contains a potentially destructive keyword ("${pattern.source}"). ` +
        'Destructive cloud operations (delete, destroy, terminate, remove, purge) are blocked. ' +
        'Please confirm this action with the user and run it manually in the terminal if truly intended.'
      );
    }
  }

  try {
    const fullCmd = `${provider} ${command}`;
    const output = run(fullCmd, { timeout: 60_000 });
    return ok(output || `Command completed (no output).`);
  } catch (err: any) {
    return fail(`${provider} CLI failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Main Execute Dispatcher
// ---------------------------------------------------------------------------

/**
 * Execute a tool by name with the provided arguments.
 * Returns a structured result or error object.
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  switch (toolName) {
    // Git
    case 'git_status':    return gitStatus(args);
    case 'git_log':       return gitLog(args);
    case 'git_diff':      return gitDiff(args);
    case 'git_commit':    return gitCommit(args);
    case 'git_branch':    return gitBranch(args);
    case 'git_stash':     return gitStash(args);
    case 'git_pull':      return gitPull(args);
    case 'git_push':      return gitPush(args);
    case 'git_clone':     return gitClone(args);
    case 'git_blame':     return gitBlame(args);

    // Docker
    case 'docker_ps':      return dockerPs(args);
    case 'docker_images':  return dockerImages();
    case 'docker_run':     return dockerRun(args);
    case 'docker_compose': return dockerCompose(args);
    case 'docker_exec':    return dockerExec(args);
    case 'docker_logs':    return dockerLogs(args);

    // Package Manager
    case 'npm_run':     return npmRun(args);
    case 'npm_install': return npmInstall(args);
    case 'npm_search':  return npmSearch(args);

    // Cloud CLI
    case 'cloud_cli': return cloudCli(args);

    default:
      return fail(`Unknown tool: ${toolName}`);
  }
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/**
 * Check whether the baseline requirement (git) is available on this system.
 * Docker and cloud CLIs are optional and do not gate the connector.
 */
export async function detect(): Promise<boolean> {
  try {
    execSync('git --version', { encoding: 'utf-8', timeout: 5000, stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}
