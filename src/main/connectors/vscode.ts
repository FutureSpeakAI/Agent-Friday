/**
 * connectors/vscode.ts — Deep VS Code integration via the `code` CLI.
 *
 * Provides tool declarations and an execute function that give an AI agent
 * the ability to open files, diff, manage extensions, read settings,
 * inspect recent files, and more — all through VS Code's command-line interface.
 *
 * Exports:
 *   TOOLS    - Array of tool declarations for the AI function-calling schema.
 *   execute  - Async dispatcher that runs the requested tool.
 *   detect   - Async probe that returns true when the `code` CLI is reachable.
 */

import { execSync, spawn } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

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

const CLI_TIMEOUT = 15_000; // 15 seconds
const MAX_OUTPUT = 8_000;   // Truncate large CLI output for sanity

/** Common VS Code CLI binary locations on Windows. */
const WINDOWS_CODE_PATHS = [
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Microsoft VS Code', 'bin', 'code.cmd'),
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Microsoft VS Code Insiders', 'bin', 'code-insiders.cmd'),
  path.join(process.env['ProgramFiles'] || '', 'Microsoft VS Code', 'bin', 'code.cmd'),
  path.join(process.env['ProgramFiles(x86)'] || '', 'Microsoft VS Code', 'bin', 'code.cmd'),
];

/** Map of language identifiers to file extensions for temp-file creation. */
const LANG_EXTENSION_MAP: Record<string, string> = {
  javascript: '.js',
  typescript: '.ts',
  python: '.py',
  ruby: '.rb',
  go: '.go',
  rust: '.rs',
  java: '.java',
  c: '.c',
  cpp: '.cpp',
  csharp: '.cs',
  html: '.html',
  css: '.css',
  scss: '.scss',
  json: '.json',
  yaml: '.yaml',
  yml: '.yml',
  xml: '.xml',
  markdown: '.md',
  sql: '.sql',
  shell: '.sh',
  bash: '.sh',
  powershell: '.ps1',
  dockerfile: '.dockerfile',
  toml: '.toml',
  lua: '.lua',
  php: '.php',
  swift: '.swift',
  kotlin: '.kt',
  r: '.r',
  perl: '.pl',
  plaintext: '.txt',
};

// ---------------------------------------------------------------------------
// CLI resolution
// ---------------------------------------------------------------------------

/** Cached path to the resolved `code` binary so we only search once. */
let resolvedCodeBin: string | null = null;

/**
 * Resolve the path to the VS Code CLI binary.
 * Tries `code --version` first (works when it is already in PATH).
 * Falls back to well-known Windows install locations.
 */
function resolveCodeBin(): string {
  if (resolvedCodeBin) return resolvedCodeBin;

  // 1. Try `code` directly (it may be in PATH)
  for (const candidate of ['code', 'code-insiders']) {
    try {
      execSync(`${candidate} --version`, { timeout: 5_000, stdio: 'pipe' });
      resolvedCodeBin = candidate;
      return resolvedCodeBin;
    } catch {
      // not found — keep searching
    }
  }

  // 2. Windows fallback: probe known install locations
  if (process.platform === 'win32') {
    for (const p of WINDOWS_CODE_PATHS) {
      if (p && fs.existsSync(p)) {
        resolvedCodeBin = `"${p}"`;
        return resolvedCodeBin;
      }
    }
  }

  throw new Error(
    'VS Code CLI (`code`) not found. Ensure VS Code is installed and ' +
    'the `code` command is available in your PATH (Shell Command: Install \'code\' command in PATH).',
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Run a VS Code CLI command synchronously and return trimmed stdout.
 * Throws on non-zero exit or timeout.
 */
function runCode(args: string): string {
  const bin = resolveCodeBin();
  const output = execSync(`${bin} ${args}`, {
    timeout: CLI_TIMEOUT,
    stdio: ['pipe', 'pipe', 'pipe'],
    encoding: 'utf-8',
    windowsHide: true,
  });
  return (output ?? '').trim();
}

/**
 * Run a VS Code CLI command asynchronously (fire-and-forget).
 * Used for commands that launch a GUI and never exit on their own (e.g. opening a file).
 */
function runCodeDetached(args: string): void {
  const bin = resolveCodeBin();
  // On Windows the bin may be quoted; split so spawn works correctly.
  const isQuoted = bin.startsWith('"') && bin.endsWith('"');
  const command = isQuoted ? bin.slice(1, -1) : bin;

  const child = spawn(command, args.split(/\s+/), {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
    shell: true,
  });
  child.unref();
}

/** Truncate long strings and append an indicator. */
function truncate(str: string, max = MAX_OUTPUT): string {
  if (str.length <= max) return str;
  return str.slice(0, max) + `\n... (truncated, ${str.length} chars total)`;
}

/** Generate a unique temp file path with an optional extension. */
function tempFile(ext = '.txt'): string {
  const name = `vscode-connector-${Date.now()}-${Math.random().toString(36).slice(2, 8)}${ext}`;
  return path.join(os.tmpdir(), name);
}

/**
 * Resolve the VS Code user settings directory.
 * Returns the platform-appropriate path to settings.json.
 */
function settingsJsonPath(): string {
  switch (process.platform) {
    case 'win32':
      return path.join(process.env.APPDATA || '', 'Code', 'User', 'settings.json');
    case 'darwin':
      return path.join(os.homedir(), 'Library', 'Application Support', 'Code', 'User', 'settings.json');
    default:
      return path.join(os.homedir(), '.config', 'Code', 'User', 'settings.json');
  }
}

/**
 * Resolve the VS Code global storage directory (contains storage.json / state.vscdb).
 */
function globalStoragePath(): string {
  switch (process.platform) {
    case 'win32':
      return path.join(process.env.APPDATA || '', 'Code', 'User', 'globalStorage');
    case 'darwin':
      return path.join(os.homedir(), 'Library', 'Application Support', 'Code', 'User', 'globalStorage');
    default:
      return path.join(os.homedir(), '.config', 'Code', 'User', 'globalStorage');
  }
}

/**
 * Locate the VS Code storage.json (pre-1.65) or state.vscdb (1.65+).
 * Returns the full path to whichever exists, or null.
 */
function resolveStoragePath(): string | null {
  const base = process.platform === 'win32'
    ? path.join(process.env.APPDATA || '', 'Code')
    : process.platform === 'darwin'
      ? path.join(os.homedir(), 'Library', 'Application Support', 'Code')
      : path.join(os.homedir(), '.config', 'Code');

  // Newer VS Code uses a SQLite-backed state database
  const stateDb = path.join(base, 'User', 'globalStorage', 'state.vscdb');
  if (fs.existsSync(stateDb)) return stateDb;

  // Older VS Code uses a plain JSON file
  const storageJson = path.join(base, 'storage.json');
  if (fs.existsSync(storageJson)) return storageJson;

  return null;
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

async function vscodeOpen(args: Record<string, unknown>): Promise<ToolResult> {
  const filePath = String(args.path || '');
  if (!filePath) return { error: 'Missing required argument: path' };

  const reuseWindow = Boolean(args.reuse_window);
  const line = args.line != null ? Number(args.line) : undefined;
  const column = args.column != null ? Number(args.column) : undefined;

  try {
    const flags: string[] = [];
    if (reuseWindow) flags.push('--reuse-window');

    let target: string;
    if (line != null) {
      // --goto expects path:line:column
      const col = column ?? 1;
      target = `--goto "${filePath}:${line}:${col}"`;
    } else {
      target = `"${filePath}"`;
    }

    runCodeDetached(`${flags.join(' ')} ${target}`);
    const desc = line != null
      ? `${filePath}:${line}${column != null ? ':' + column : ''}`
      : filePath;
    return { result: `Opened ${desc} in VS Code.` };
  } catch (err: unknown) {
    return { error: `Failed to open file: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeDiff(args: Record<string, unknown>): Promise<ToolResult> {
  const file1 = String(args.file1 || '');
  const file2 = String(args.file2 || '');
  if (!file1 || !file2) return { error: 'Missing required arguments: file1 and file2' };

  try {
    const label = args.label ? ` --label "${String(args.label)}"` : '';
    runCodeDetached(`--diff "${file1}" "${file2}"${label}`);
    return { result: `Opened diff view: ${file1} <-> ${file2}` };
  } catch (err: unknown) {
    return { error: `Failed to open diff: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeExtensionsList(): Promise<ToolResult> {
  try {
    const raw = runCode('--list-extensions --show-versions');
    if (!raw) return { result: JSON.stringify([]) };

    const extensions = raw
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        const atIdx = line.lastIndexOf('@');
        if (atIdx > 0) {
          return { id: line.slice(0, atIdx), version: line.slice(atIdx + 1) };
        }
        return { id: line, version: 'unknown' };
      });

    return { result: JSON.stringify(extensions, null, 2) };
  } catch (err: unknown) {
    return { error: `Failed to list extensions: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeExtensionInstall(args: Record<string, unknown>): Promise<ToolResult> {
  const extensionId = String(args.extension_id || '');
  if (!extensionId) return { error: 'Missing required argument: extension_id' };

  try {
    const output = runCode(`--install-extension "${extensionId}" --force`);
    const success = output.toLowerCase().includes('successfully installed')
      || output.toLowerCase().includes('already installed')
      || !output.toLowerCase().includes('error');
    return {
      result: success
        ? `Extension ${extensionId} installed successfully.`
        : `Extension install output: ${truncate(output)}`,
    };
  } catch (err: unknown) {
    return { error: `Failed to install extension ${extensionId}: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeExtensionUninstall(args: Record<string, unknown>): Promise<ToolResult> {
  const extensionId = String(args.extension_id || '');
  if (!extensionId) return { error: 'Missing required argument: extension_id' };

  try {
    const output = runCode(`--uninstall-extension "${extensionId}"`);
    return { result: `Extension ${extensionId} uninstalled. ${truncate(output)}` };
  } catch (err: unknown) {
    return { error: `Failed to uninstall extension ${extensionId}: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeWorkspaceOpen(args: Record<string, unknown>): Promise<ToolResult> {
  const workspacePath = String(args.workspace_path || '');
  if (!workspacePath) return { error: 'Missing required argument: workspace_path' };

  try {
    if (!fs.existsSync(workspacePath)) {
      return { error: `Workspace file not found: ${workspacePath}` };
    }
    runCodeDetached(`"${workspacePath}"`);
    return { result: `Opened workspace: ${workspacePath}` };
  } catch (err: unknown) {
    return { error: `Failed to open workspace: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeNewFile(args: Record<string, unknown>): Promise<ToolResult> {
  const content = String(args.content ?? '');
  const language = args.language ? String(args.language).toLowerCase() : undefined;

  try {
    const ext = language ? (LANG_EXTENSION_MAP[language] || `.${language}`) : '.txt';
    const tmpPath = tempFile(ext);
    fs.writeFileSync(tmpPath, content, 'utf-8');
    runCodeDetached(`"${tmpPath}"`);
    return { result: `Created temp file and opened in VS Code: ${tmpPath}` };
  } catch (err: unknown) {
    return { error: `Failed to create new file: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeSettingsRead(): Promise<ToolResult> {
  try {
    const settingsPath = settingsJsonPath();
    if (!fs.existsSync(settingsPath)) {
      return { result: JSON.stringify({ _note: 'No user settings.json found', path: settingsPath }) };
    }

    const raw = fs.readFileSync(settingsPath, 'utf-8');

    // VS Code settings.json may contain comments (JSONC). Strip them before parsing.
    const stripped = raw
      .replace(/\/\/.*$/gm, '')          // single-line comments
      .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
      .replace(/,\s*([}\]])/g, '$1');    // trailing commas

    let parsed: unknown;
    try {
      parsed = JSON.parse(stripped);
    } catch {
      // If stripping wasn't enough, return the raw text
      return { result: truncate(raw) };
    }

    return { result: truncate(JSON.stringify(parsed, null, 2)) };
  } catch (err: unknown) {
    return { error: `Failed to read settings: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeRecentFiles(): Promise<ToolResult> {
  try {
    const storagePath = resolveStoragePath();
    if (!storagePath) {
      return { error: 'Could not locate VS Code storage file (storage.json or state.vscdb).' };
    }

    // If it is the older JSON format, read it directly
    if (storagePath.endsWith('.json')) {
      const raw = fs.readFileSync(storagePath, 'utf-8');
      const data = JSON.parse(raw);

      const recentEntries: string[] = [];

      // Extract recently opened paths from various schema versions
      const opened = data?.openedPathsList?.entries
        ?? data?.openedPathsList?.workspaces3
        ?? data?.openedPathsList?.workspaces2
        ?? data?.openedPathsList?.workspaces
        ?? [];

      for (const entry of opened) {
        if (typeof entry === 'string') {
          recentEntries.push(entry);
        } else if (entry?.folderUri) {
          recentEntries.push(entry.folderUri);
        } else if (entry?.fileUri) {
          recentEntries.push(entry.fileUri);
        } else if (entry?.workspace?.configPath) {
          recentEntries.push(entry.workspace.configPath);
        }
      }

      return { result: JSON.stringify(recentEntries.slice(0, 50), null, 2) };
    }

    // For state.vscdb (SQLite), we attempt to read via a PowerShell / sqlite3 approach.
    // This is a best-effort extraction since we cannot bundle a native SQLite driver.
    if (storagePath.endsWith('.vscdb')) {
      try {
        // Try using sqlite3 CLI if available
        const query = `SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList';`;
        const raw = execSync(
          `sqlite3 "${storagePath}" "${query}"`,
          { timeout: CLI_TIMEOUT, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe'] },
        ).trim();

        if (raw) {
          const data = JSON.parse(raw);
          const entries: string[] = [];
          for (const entry of (data?.entries ?? [])) {
            if (entry?.folderUri) entries.push(entry.folderUri);
            else if (entry?.fileUri) entries.push(entry.fileUri);
            else if (entry?.workspace?.configPath) entries.push(entry.workspace.configPath);
          }
          return { result: JSON.stringify(entries.slice(0, 50), null, 2) };
        }
      } catch {
        // sqlite3 CLI not available — fall through
      }

      // Fallback: read the raw file and try to extract JSON blobs via regex.
      // state.vscdb is a small SQLite file; the recent-paths JSON is stored as a text value.
      try {
        const rawBuf = fs.readFileSync(storagePath);
        const rawStr = rawBuf.toString('utf-8');
        const marker = 'history.recentlyOpenedPathsList';
        const markerIdx = rawStr.indexOf(marker);
        if (markerIdx !== -1) {
          // The JSON value follows somewhere after the key. Search for the first '{'.
          const jsonStart = rawStr.indexOf('{', markerIdx);
          if (jsonStart !== -1) {
            // Attempt to find the matching closing brace
            let depth = 0;
            let jsonEnd = jsonStart;
            for (let i = jsonStart; i < rawStr.length; i++) {
              if (rawStr[i] === '{') depth++;
              else if (rawStr[i] === '}') depth--;
              if (depth === 0) { jsonEnd = i + 1; break; }
            }
            const jsonStr = rawStr.slice(jsonStart, jsonEnd);
            const data = JSON.parse(jsonStr);
            const entries: string[] = [];
            for (const entry of (data?.entries ?? [])) {
              if (entry?.folderUri) entries.push(entry.folderUri);
              else if (entry?.fileUri) entries.push(entry.fileUri);
              else if (entry?.workspace?.configPath) entries.push(entry.workspace.configPath);
            }
            return { result: JSON.stringify(entries.slice(0, 50), null, 2) };
          }
        }
      } catch {
        // binary parse failed
      }

      return {
        error: 'VS Code uses a SQLite state database (state.vscdb) and sqlite3 CLI is not available. '
             + 'Install sqlite3 or check storage.json at: ' + storagePath,
      };
    }

    return { error: `Unrecognized storage format: ${storagePath}` };
  } catch (err: unknown) {
    return { error: `Failed to read recent files: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function vscodeTerminalRun(args: Record<string, unknown>): Promise<ToolResult> {
  const command = String(args.command || '');
  if (!command) return { error: 'Missing required argument: command' };

  const terminalName = args.name ? String(args.name) : undefined;

  try {
    // Strategy: Write the command to a temp script, then ask VS Code to open a
    // new integrated terminal and run it. The `--command` approach with
    // workbench.action.terminal.sendSequence is fragile and requires VS Code to
    // be already running with focus. Instead, we use a more reliable approach:
    // create a temp script and execute it via `code --new-window` with an
    // integrated terminal command.

    // The most reliable cross-platform approach is to write a wrapper script
    // and then use the `code` CLI to instruct VS Code to open a terminal.
    const isWindows = process.platform === 'win32';
    const ext = isWindows ? '.cmd' : '.sh';
    const scriptPath = tempFile(ext);

    const titleLine = terminalName ? `title ${terminalName}` : '';
    if (isWindows) {
      const script = [
        '@echo off',
        titleLine,
        `echo [VS Code Terminal] Running command...`,
        `echo.`,
        command,
        `echo.`,
        `echo [Done] Press any key to close...`,
        `pause >nul`,
      ].filter(Boolean).join('\r\n');
      fs.writeFileSync(scriptPath, script, 'utf-8');
    } else {
      const script = [
        '#!/bin/bash',
        terminalName ? `echo -ne "\\033]0;${terminalName}\\007"` : '',
        `echo "[VS Code Terminal] Running command..."`,
        `echo`,
        command,
        `echo`,
        `echo "[Done] Press Enter to close..."`,
        `read`,
      ].filter(Boolean).join('\n');
      fs.writeFileSync(scriptPath, script, { mode: 0o755, encoding: 'utf-8' });
    }

    // Open an integrated terminal in VS Code that runs our script.
    // The `-` argument tells code to read stdin, but more practically we use
    // the --goto approach to open the script, or fall back to a direct terminal.
    // Actually the most reliable approach: use `code --reuse-window` to ensure
    // VS Code is active, then execute via shell integration.
    const bin = resolveCodeBin();
    const child = spawn(
      isWindows ? 'cmd.exe' : '/bin/sh',
      isWindows
        ? ['/c', `${bin} --reuse-window && timeout /t 1 >nul && ${bin} --command workbench.action.terminal.newWithCwd && timeout /t 1 >nul && ${bin} --command workbench.action.terminal.sendSequence --args "{\\"text\\":\\"${command.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}\\\\n\\"}" 2>nul || start "" "${scriptPath}"`]
        : ['-c', `${bin} --reuse-window && sleep 0.5 && ${bin} --command workbench.action.terminal.sendSequence '{"text":"${command.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}\\n"}' 2>/dev/null || open "${scriptPath}"`],
      {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        shell: false,
      },
    );
    child.unref();

    return {
      result: `Sent command to VS Code integrated terminal: ${command.slice(0, 200)}${command.length > 200 ? '...' : ''}`
        + (terminalName ? ` (terminal: ${terminalName})` : '')
        + `\nFallback script saved at: ${scriptPath}`,
    };
  } catch (err: unknown) {
    return { error: `Failed to run terminal command: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  {
    name: 'vscode_open',
    description:
      'Open a file or folder in VS Code. Optionally jump to a specific line and column. ' +
      'Set reuse_window to true to open in the most recently active VS Code window.',
    parameters: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Absolute path to a file or folder to open in VS Code.',
        },
        line: {
          type: 'number',
          description: 'Line number to jump to (1-based). Requires a file path.',
        },
        column: {
          type: 'number',
          description: 'Column number to jump to (1-based). Only used when line is also specified.',
        },
        reuse_window: {
          type: 'boolean',
          description: 'If true, reuse the most recently active VS Code window instead of opening a new one.',
        },
      },
      required: ['path'],
    },
  },
  {
    name: 'vscode_diff',
    description:
      'Open a side-by-side diff view in VS Code comparing two files.',
    parameters: {
      type: 'object',
      properties: {
        file1: {
          type: 'string',
          description: 'Absolute path to the first (left) file.',
        },
        file2: {
          type: 'string',
          description: 'Absolute path to the second (right) file.',
        },
        label: {
          type: 'string',
          description: 'Optional label for the diff tab.',
        },
      },
      required: ['file1', 'file2'],
    },
  },
  {
    name: 'vscode_extensions_list',
    description:
      'List all installed VS Code extensions with their version numbers. ' +
      'Returns a JSON array of { id, version } objects.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'vscode_extension_install',
    description:
      'Install a VS Code extension by its marketplace identifier (e.g. "ms-python.python").',
    parameters: {
      type: 'object',
      properties: {
        extension_id: {
          type: 'string',
          description: 'The extension marketplace ID (publisher.extensionName).',
        },
      },
      required: ['extension_id'],
    },
  },
  {
    name: 'vscode_extension_uninstall',
    description:
      'Uninstall a VS Code extension by its marketplace identifier.',
    parameters: {
      type: 'object',
      properties: {
        extension_id: {
          type: 'string',
          description: 'The extension marketplace ID to remove.',
        },
      },
      required: ['extension_id'],
    },
  },
  {
    name: 'vscode_workspace_open',
    description:
      'Open a VS Code workspace file (.code-workspace) in VS Code.',
    parameters: {
      type: 'object',
      properties: {
        workspace_path: {
          type: 'string',
          description: 'Absolute path to a .code-workspace file.',
        },
      },
      required: ['workspace_path'],
    },
  },
  {
    name: 'vscode_new_file',
    description:
      'Create a temporary file with the given content and open it in VS Code. ' +
      'Optionally specify a language for syntax highlighting.',
    parameters: {
      type: 'object',
      properties: {
        content: {
          type: 'string',
          description: 'The text content to put in the new file.',
        },
        language: {
          type: 'string',
          description:
            'Language identifier for syntax highlighting (e.g. "typescript", "python", "json"). ' +
            'Determines the temp file extension.',
        },
      },
      required: ['content'],
    },
  },
  {
    name: 'vscode_settings_read',
    description:
      'Read the current VS Code user settings.json and return its contents as formatted JSON.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'vscode_recent_files',
    description:
      'Retrieve the list of recently opened files and folders from VS Code\'s storage. ' +
      'Returns a JSON array of URI strings (up to 50 entries).',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'vscode_terminal_run',
    description:
      'Run a command in VS Code\'s integrated terminal. Opens a new terminal in VS Code ' +
      'and executes the specified command. Optionally name the terminal session.',
    parameters: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          description: 'The shell command to execute in the integrated terminal.',
        },
        name: {
          type: 'string',
          description: 'Optional human-readable name for the terminal tab.',
        },
      },
      required: ['command'],
    },
  },
];

// ---------------------------------------------------------------------------
// Execute dispatcher
// ---------------------------------------------------------------------------

/**
 * Execute a VS Code tool by name.
 * Returns an object with either `result` (success) or `error` (failure).
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  switch (toolName) {
    case 'vscode_open':
      return vscodeOpen(args);
    case 'vscode_diff':
      return vscodeDiff(args);
    case 'vscode_extensions_list':
      return vscodeExtensionsList();
    case 'vscode_extension_install':
      return vscodeExtensionInstall(args);
    case 'vscode_extension_uninstall':
      return vscodeExtensionUninstall(args);
    case 'vscode_workspace_open':
      return vscodeWorkspaceOpen(args);
    case 'vscode_new_file':
      return vscodeNewFile(args);
    case 'vscode_settings_read':
      return vscodeSettingsRead();
    case 'vscode_recent_files':
      return vscodeRecentFiles();
    case 'vscode_terminal_run':
      return vscodeTerminalRun(args);
    default:
      return { error: `Unknown VS Code tool: ${toolName}` };
  }
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/**
 * Detect whether the VS Code CLI is available on this system.
 * Returns true when `code --version` (or a known install path) responds successfully.
 */
export async function detect(): Promise<boolean> {
  try {
    resolveCodeBin();
    return true;
  } catch {
    return false;
  }
}
