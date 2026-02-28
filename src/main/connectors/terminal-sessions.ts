/**
 * terminal-sessions.ts — Persistent terminal session management for AI agents.
 *
 * Provides tools to create, interact with, and manage long-running shell sessions
 * (build watchers, REPLs, dev servers, etc.) from the Electron main process.
 *
 * Exports:
 *   TOOLS    - Array of tool declarations for the agent framework
 *   execute  - Async handler for tool invocations
 *   detect   - Async availability check (always true on Windows)
 */

import { spawn, ChildProcess } from 'child_process';
import { randomUUID } from 'crypto';
import path from 'path';
import os from 'os';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TerminalSession {
  id: string;
  name: string;
  shell: ShellType;
  process: ChildProcess;
  outputBuffer: string[];
  createdAt: string;
  lastActivity: string;
  cwd: string;
  cols: number;
  rows: number;
  running: boolean;
  /** Timestamp (epoch ms) when the process exited — used for auto-cleanup */
  exitedAt: number | null;
  /** Accumulated unread cursor — index into outputBuffer from which new data starts */
  readCursor: number;
}

type ShellType = 'powershell' | 'cmd' | 'bash' | 'wsl';

interface ToolResult {
  result?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_SESSIONS = 10;
const MAX_OUTPUT_LINES = 10_000;
const MAX_RESPONSE_CHARS = 5_000;
const DEAD_SESSION_TTL_MS = 5 * 60 * 1000; // 5 minutes
const CLEANUP_INTERVAL_MS = 60 * 1000;      // check every minute
const DEFAULT_WAIT_MS = 2_000;
const DEFAULT_READ_LINES = 50;
const DEFAULT_WAIT_FOR_TIMEOUT_MS = 30_000;
const WAIT_FOR_POLL_INTERVAL_MS = 200;

// ---------------------------------------------------------------------------
// Session store
// ---------------------------------------------------------------------------

const sessions: Map<string, TerminalSession> = new Map();

let cleanupTimer: ReturnType<typeof setInterval> | null = null;

function ensureCleanupTimer(): void {
  if (cleanupTimer) return;
  cleanupTimer = setInterval(() => {
    const now = Date.now();
    for (const [id, session] of sessions) {
      if (session.exitedAt && now - session.exitedAt > DEAD_SESSION_TTL_MS) {
        sessions.delete(id);
      }
    }
    // Stop timer when no sessions remain
    if (sessions.size === 0 && cleanupTimer) {
      clearInterval(cleanupTimer);
      cleanupTimer = null;
    }
  }, CLEANUP_INTERVAL_MS);
  // Allow the Node process to exit even if the timer is running
  if (cleanupTimer && typeof cleanupTimer === 'object' && 'unref' in cleanupTimer) {
    cleanupTimer.unref();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resolveShellCommand(shell: ShellType): { command: string; args: string[] } {
  switch (shell) {
    case 'powershell':
      return { command: 'powershell.exe', args: ['-NoLogo', '-NoProfile', '-Command', '-'] };
    case 'cmd':
      return { command: 'cmd.exe', args: ['/Q'] };
    case 'bash':
      return { command: 'bash', args: ['--norc'] };
    case 'wsl':
      return { command: 'wsl.exe', args: ['bash', '--norc'] };
    default:
      return { command: 'powershell.exe', args: ['-NoLogo', '-NoProfile', '-Command', '-'] };
  }
}

function isoNow(): string {
  return new Date().toISOString();
}

function truncate(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const half = Math.floor((maxChars - 30) / 2);
  return (
    text.slice(0, half) +
    `\n\n--- truncated (${text.length} chars total) ---\n\n` +
    text.slice(text.length - half)
  );
}

function sanitizeOutput(text: string): string {
  // Strip null bytes and other non-printable control chars except \n, \r, \t
  // eslint-disable-next-line no-control-regex
  return text.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');
}

function getSession(sessionId: string): TerminalSession {
  const session = sessions.get(sessionId);
  if (!session) {
    throw new Error(`No terminal session found with id: ${sessionId}`);
  }
  return session;
}

function appendOutput(session: TerminalSession, data: string): void {
  const lines = data.split('\n');
  session.outputBuffer.push(...lines);
  // Trim to rolling max
  if (session.outputBuffer.length > MAX_OUTPUT_LINES) {
    session.outputBuffer.splice(0, session.outputBuffer.length - MAX_OUTPUT_LINES);
    // Adjust read cursor if it pointed into trimmed area
    if (session.readCursor > session.outputBuffer.length) {
      session.readCursor = session.outputBuffer.length;
    }
  }
  session.lastActivity = isoNow();
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

async function terminalCreate(args: Record<string, unknown>): Promise<ToolResult> {
  // Enforce session limit
  const activeSessions = [...sessions.values()].filter((s) => s.running);
  if (activeSessions.length >= MAX_SESSIONS) {
    return { error: `Maximum concurrent sessions reached (${MAX_SESSIONS}). Kill an existing session first.` };
  }

  const shell: ShellType = (args.shell as ShellType) || 'powershell';
  const validShells: ShellType[] = ['powershell', 'cmd', 'bash', 'wsl'];
  if (!validShells.includes(shell)) {
    return { error: `Invalid shell type: ${shell}. Must be one of: ${validShells.join(', ')}` };
  }

  const name = typeof args.name === 'string' && args.name.trim()
    ? args.name.trim().slice(0, 64)
    : `${shell}-session`;

  const cwd = typeof args.cwd === 'string' && args.cwd.trim()
    ? path.resolve(args.cwd.trim())
    : os.homedir();

  const { command, args: shellArgs } = resolveShellCommand(shell);
  const id = randomUUID();

  let proc: ChildProcess;
  try {
    proc = spawn(command, shellArgs, {
      stdio: 'pipe',
      shell: false,
      cwd,
      env: {
        ...process.env,
        TERM: 'dumb',
        COLUMNS: '120',
        LINES: '30',
      },
      windowsHide: true,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Failed to spawn ${shell}: ${msg}` };
  }

  if (!proc.pid) {
    return { error: `Failed to start ${shell} process — no PID assigned.` };
  }

  const session: TerminalSession = {
    id,
    name,
    shell,
    process: proc,
    outputBuffer: [],
    createdAt: isoNow(),
    lastActivity: isoNow(),
    cwd,
    cols: 120,
    rows: 30,
    running: true,
    exitedAt: null,
    readCursor: 0,
  };

  // Wire up stdout/stderr -> output buffer
  proc.stdout?.on('data', (chunk: Buffer) => {
    appendOutput(session, sanitizeOutput(chunk.toString('utf-8')));
  });

  proc.stderr?.on('data', (chunk: Buffer) => {
    appendOutput(session, sanitizeOutput(chunk.toString('utf-8')));
  });

  // Handle process exit
  proc.on('exit', (code, signal) => {
    session.running = false;
    session.exitedAt = Date.now();
    appendOutput(session, `\n[Process exited with code ${code ?? 'null'}, signal ${signal ?? 'none'}]\n`);
  });

  proc.on('error', (err: Error) => {
    session.running = false;
    session.exitedAt = Date.now();
    appendOutput(session, `\n[Process error: ${err.message}]\n`);
  });

  sessions.set(id, session);
  ensureCleanupTimer();

  return {
    result: JSON.stringify({
      session_id: id,
      name,
      shell,
      pid: proc.pid,
      cwd,
    }),
  };
}

async function terminalSend(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  const input = args.input as string;
  if (typeof input !== 'string') return { error: 'Missing required argument: input' };

  const waitMs = typeof args.wait_ms === 'number' && args.wait_ms >= 0
    ? Math.min(args.wait_ms, 60_000)
    : DEFAULT_WAIT_MS;

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  if (!session.running) {
    return { error: 'Terminal session has exited. Read remaining output with terminal_read.' };
  }

  if (!session.process.stdin || session.process.stdin.destroyed) {
    return { error: 'Terminal stdin is not writable.' };
  }

  // Mark read cursor so we know where new output starts
  const cursorBefore = session.outputBuffer.length;

  try {
    session.process.stdin.write(input + '\n');
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Failed to write to terminal stdin: ${msg}` };
  }

  session.lastActivity = isoNow();

  // Wait for output to accumulate
  if (waitMs > 0) {
    await new Promise<void>((resolve) => setTimeout(resolve, waitMs));
  }

  // Return new output since we sent the command
  const newLines = session.outputBuffer.slice(cursorBefore);
  const output = truncate(newLines.join('\n'), MAX_RESPONSE_CHARS);

  // Advance read cursor
  session.readCursor = session.outputBuffer.length;

  return { result: output || '(no output)' };
}

async function terminalRead(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  const lineCount = typeof args.lines === 'number' && args.lines > 0
    ? Math.min(args.lines, MAX_OUTPUT_LINES)
    : DEFAULT_READ_LINES;

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  const buf = session.outputBuffer;
  const startIdx = Math.max(0, buf.length - lineCount);
  const lines = buf.slice(startIdx);
  const output = truncate(lines.join('\n'), MAX_RESPONSE_CHARS);

  return {
    result: output || '(no output)',
  };
}

async function terminalList(): Promise<ToolResult> {
  const list = [...sessions.values()].map((s) => ({
    id: s.id,
    name: s.name,
    shell: s.shell,
    pid: s.process.pid ?? null,
    cwd: s.cwd,
    running: s.running,
    created_at: s.createdAt,
    last_activity: s.lastActivity,
  }));

  return { result: JSON.stringify(list, null, 2) };
}

async function terminalKill(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  if (session.running) {
    try {
      // On Windows, tree-kill the process group
      if (process.platform === 'win32' && session.process.pid) {
        spawn('taskkill', ['/PID', String(session.process.pid), '/T', '/F'], {
          stdio: 'ignore',
          windowsHide: true,
        });
      } else {
        session.process.kill('SIGKILL');
      }
    } catch {
      // Process may have already exited — that is fine
    }
    session.running = false;
    session.exitedAt = Date.now();
  }

  // Remove from map immediately on explicit kill
  sessions.delete(sessionId);

  return { result: `Session ${sessionId} terminated and removed.` };
}

async function terminalResize(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  const cols = typeof args.cols === 'number' ? Math.max(20, Math.min(args.cols, 500)) : 120;
  const rows = typeof args.rows === 'number' ? Math.max(5, Math.min(args.rows, 200)) : 30;

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  session.cols = cols;
  session.rows = rows;

  // Send stty-style resize if the shell supports it, and set env for child processes
  if (session.running && session.process.stdin && !session.process.stdin.destroyed) {
    if (session.shell === 'bash' || session.shell === 'wsl') {
      try {
        session.process.stdin.write(`stty cols ${cols} rows ${rows} 2>/dev/null\n`);
      } catch {
        // Non-critical — ignore
      }
    } else if (session.shell === 'powershell') {
      try {
        session.process.stdin.write(
          `$Host.UI.RawUI.BufferSize = New-Object System.Management.Automation.Host.Size(${cols}, 9999); ` +
          `$Host.UI.RawUI.WindowSize = New-Object System.Management.Automation.Host.Size(${cols}, ${rows})\n`
        );
      } catch {
        // Non-critical — ignore
      }
    } else if (session.shell === 'cmd') {
      try {
        session.process.stdin.write(`mode con: cols=${cols} lines=${rows}\n`);
      } catch {
        // Non-critical — ignore
      }
    }
  }

  return {
    result: JSON.stringify({ session_id: sessionId, cols, rows }),
  };
}

async function terminalSendSignal(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  const signal = args.signal as string;
  const validSignals = ['SIGINT', 'SIGTERM', 'SIGKILL'] as const;
  type SignalType = typeof validSignals[number];

  if (!signal || !validSignals.includes(signal as SignalType)) {
    return { error: `Invalid signal: ${signal}. Must be one of: ${validSignals.join(', ')}` };
  }

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  if (!session.running) {
    return { error: 'Terminal session has already exited.' };
  }

  const pid = session.process.pid;
  if (!pid) {
    return { error: 'Process has no PID.' };
  }

  try {
    // On Windows, SIGINT doesn't propagate cleanly to child processes.
    // For SIGINT specifically, we write Ctrl+C to stdin as a workaround.
    if (process.platform === 'win32' && signal === 'SIGINT') {
      if (session.process.stdin && !session.process.stdin.destroyed) {
        session.process.stdin.write('\x03'); // ETX = Ctrl+C
      }
    } else if (process.platform === 'win32' && (signal === 'SIGTERM' || signal === 'SIGKILL')) {
      spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
    } else {
      process.kill(pid, signal);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Failed to send ${signal}: ${msg}` };
  }

  session.lastActivity = isoNow();

  return { result: `Signal ${signal} sent to session ${sessionId} (pid ${pid}).` };
}

async function terminalWaitFor(args: Record<string, unknown>): Promise<ToolResult> {
  const sessionId = args.session_id as string;
  if (!sessionId) return { error: 'Missing required argument: session_id' };

  const pattern = args.pattern as string;
  if (!pattern) return { error: 'Missing required argument: pattern' };

  const timeoutMs = typeof args.timeout_ms === 'number' && args.timeout_ms > 0
    ? Math.min(args.timeout_ms, 120_000)
    : DEFAULT_WAIT_FOR_TIMEOUT_MS;

  let session: TerminalSession;
  try {
    session = getSession(sessionId);
  } catch (err: unknown) {
    return { error: (err as Error).message };
  }

  let regex: RegExp;
  try {
    regex = new RegExp(pattern, 'm');
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Invalid regex pattern: ${msg}` };
  }

  const startTime = Date.now();
  const searchFrom = session.outputBuffer.length;

  while (Date.now() - startTime < timeoutMs) {
    // Search in output that arrived since we started waiting (and a small window before)
    const windowStart = Math.max(0, searchFrom - 10);
    const recentOutput = session.outputBuffer.slice(windowStart).join('\n');
    const match = regex.exec(recentOutput);

    if (match) {
      // Return context around the match — the matched line and a few surrounding lines
      const matchIdx = recentOutput.indexOf(match[0]);
      const contextStart = Math.max(0, matchIdx - 200);
      const contextEnd = Math.min(recentOutput.length, matchIdx + match[0].length + 200);
      const context = recentOutput.slice(contextStart, contextEnd);

      return {
        result: JSON.stringify({
          matched: true,
          match: match[0].slice(0, 500),
          context: truncate(context, MAX_RESPONSE_CHARS),
          elapsed_ms: Date.now() - startTime,
        }),
      };
    }

    // If the session has died, no point waiting further
    if (!session.running) {
      return {
        error: `Session exited before pattern was matched. Waited ${Date.now() - startTime}ms.`,
      };
    }

    await new Promise<void>((resolve) => setTimeout(resolve, WAIT_FOR_POLL_INTERVAL_MS));
  }

  return {
    error: `Timeout after ${timeoutMs}ms waiting for pattern: ${pattern}`,
  };
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS = [
  {
    name: 'terminal_create',
    description:
      'Create a new persistent terminal session (shell process). ' +
      'Returns a session ID for subsequent interaction. ' +
      'Supports powershell (default), cmd, bash, and wsl shells. ' +
      'Use this for long-running processes like dev servers, build watchers, REPLs, etc.',
    parameters: {
      type: 'object' as const,
      properties: {
        shell: {
          type: 'string',
          enum: ['powershell', 'cmd', 'bash', 'wsl'],
          description: 'Shell to use. Default: powershell.',
        },
        name: {
          type: 'string',
          description: 'Human-readable name for the session (e.g. "dev-server", "build-watcher").',
        },
        cwd: {
          type: 'string',
          description: 'Working directory for the shell. Default: user home directory.',
        },
      },
      required: [] as string[],
    },
  },
  {
    name: 'terminal_send',
    description:
      'Send a command or input to an existing terminal session and wait for output. ' +
      'A newline is appended automatically. Returns stdout/stderr produced after sending.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID.',
        },
        input: {
          type: 'string',
          description: 'The text to send to the terminal stdin.',
        },
        wait_ms: {
          type: 'number',
          description:
            'Milliseconds to wait for output after sending. Default: 2000. Max: 60000. Set to 0 for fire-and-forget.',
        },
      },
      required: ['session_id', 'input'],
    },
  },
  {
    name: 'terminal_read',
    description:
      'Read the latest output from a terminal session without sending any input. ' +
      'Returns the last N lines of the output buffer. Does not clear the buffer.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID.',
        },
        lines: {
          type: 'number',
          description: 'Number of recent lines to return. Default: 50.',
        },
      },
      required: ['session_id'],
    },
  },
  {
    name: 'terminal_list',
    description:
      'List all active and recently-exited terminal sessions with their status, PIDs, and metadata.',
    parameters: {
      type: 'object' as const,
      properties: {},
      required: [] as string[],
    },
  },
  {
    name: 'terminal_kill',
    description:
      'Kill a terminal session and its child processes. Removes the session from the active list.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID to kill.',
        },
      },
      required: ['session_id'],
    },
  },
  {
    name: 'terminal_resize',
    description:
      'Resize the terminal dimensions. Useful for tools that respect COLUMNS/LINES environment variables.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID.',
        },
        cols: {
          type: 'number',
          description: 'Number of columns. Default: 120.',
        },
        rows: {
          type: 'number',
          description: 'Number of rows. Default: 30.',
        },
      },
      required: ['session_id', 'cols', 'rows'],
    },
  },
  {
    name: 'terminal_send_signal',
    description:
      'Send a signal to the terminal process. SIGINT (Ctrl+C) is the most common — ' +
      'use it to interrupt a running command. SIGTERM and SIGKILL for forceful termination.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID.',
        },
        signal: {
          type: 'string',
          enum: ['SIGINT', 'SIGTERM', 'SIGKILL'],
          description: 'Signal to send. SIGINT = Ctrl+C, SIGTERM = graceful stop, SIGKILL = force kill.',
        },
      },
      required: ['session_id', 'signal'],
    },
  },
  {
    name: 'terminal_wait_for',
    description:
      'Wait for a specific output pattern (regex) to appear in the terminal output. ' +
      'Useful for waiting until a server starts, a build completes, or a prompt appears. ' +
      'Returns the matched text and surrounding context, or a timeout error.',
    parameters: {
      type: 'object' as const,
      properties: {
        session_id: {
          type: 'string',
          description: 'The terminal session ID.',
        },
        pattern: {
          type: 'string',
          description: 'Regular expression pattern to match against terminal output.',
        },
        timeout_ms: {
          type: 'number',
          description: 'Maximum milliseconds to wait. Default: 30000. Max: 120000.',
        },
      },
      required: ['session_id', 'pattern'],
    },
  },
] as const;

// ---------------------------------------------------------------------------
// Execute dispatcher
// ---------------------------------------------------------------------------

export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'terminal_create':
        return await terminalCreate(args);
      case 'terminal_send':
        return await terminalSend(args);
      case 'terminal_read':
        return await terminalRead(args);
      case 'terminal_list':
        return await terminalList();
      case 'terminal_kill':
        return await terminalKill(args);
      case 'terminal_resize':
        return await terminalResize(args);
      case 'terminal_send_signal':
        return await terminalSendSignal(args);
      case 'terminal_wait_for':
        return await terminalWaitFor(args);
      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Unexpected error executing ${toolName}: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detect — capability check
// ---------------------------------------------------------------------------

export async function detect(): Promise<boolean> {
  // Terminal sessions are always available on Windows (primary target).
  // On other platforms, check that at least one shell is accessible.
  if (process.platform === 'win32') return true;

  try {
    const { execSync } = await import('child_process');
    execSync('bash --version', { stdio: 'ignore', timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}
