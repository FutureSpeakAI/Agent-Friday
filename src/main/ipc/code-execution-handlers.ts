/**
 * Direct code execution handlers — fallback when container engine is unavailable.
 * Runs Python/Bash/Node via subprocess with timeout and output capture.
 *
 * This avoids requiring Docker: code is written to a temp file and executed
 * directly via the system's Python, Bash, or Node interpreter.
 */
import { ipcMain } from 'electron';
import { execFile } from 'child_process';
import { writeFile, unlink } from 'fs/promises';
import { join } from 'path';
import { tmpdir } from 'os';
import { randomUUID } from 'crypto';
import { assertString, assertObject } from './validate';

/** Maximum output size (stdout or stderr) in characters. */
const MAX_OUTPUT_SIZE = 100_000;

/** Execution timeout in milliseconds. */
const TIMEOUT_MS = 15_000;

/** Supported languages and their file extensions. */
const LANGUAGE_CONFIG: Record<string, { ext: string; commands: string[] }> = {
  python: { ext: '.py', commands: ['python3', 'python'] },
  bash:   { ext: '.sh', commands: ['bash'] },
  node:   { ext: '.js', commands: ['node'] },
};

/**
 * Try each command in order until one succeeds.
 * Returns { stdout, stderr, exitCode }.
 */
function executeWithFallback(
  commands: string[],
  args: string[],
  timeoutMs: number,
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return new Promise((resolve, reject) => {
    let cmdIndex = 0;

    function tryNext(): void {
      if (cmdIndex >= commands.length) {
        reject(new Error(
          `No interpreter found. Tried: ${commands.join(', ')}. ` +
          'Ensure the language runtime is installed and on PATH.',
        ));
        return;
      }

      const cmd = commands[cmdIndex];
      cmdIndex++;

      execFile(cmd, args, { timeout: timeoutMs, maxBuffer: MAX_OUTPUT_SIZE * 2 }, (error, stdout, stderr) => {
        // If the command was not found, try the next one
        if (error && 'code' in error && (error as NodeJS.ErrnoException).code === 'ENOENT') {
          tryNext();
          return;
        }

        const exitCode = error && 'code' in error && typeof (error as any).code === 'number'
          ? (error as any).code as number
          : error
            ? 1
            : 0;

        // Check for timeout (killed by Node)
        if (error && (error as any).killed) {
          resolve({
            stdout: truncate(stdout || ''),
            stderr: truncate((stderr || '') + `\n[Process killed: exceeded ${timeoutMs / 1000}s timeout]`),
            exitCode: 124, // conventional timeout exit code
          });
          return;
        }

        resolve({
          stdout: truncate(stdout || ''),
          stderr: truncate(stderr || ''),
          exitCode,
        });
      });
    }

    tryNext();
  });
}

/** Truncate a string to MAX_OUTPUT_SIZE with a truncation notice. */
function truncate(s: string): string {
  if (s.length <= MAX_OUTPUT_SIZE) return s;
  return s.slice(0, MAX_OUTPUT_SIZE) + '\n[output truncated]';
}

export function registerCodeExecutionHandlers(): void {
  ipcMain.handle(
    'code:execute-direct',
    async (_event, payload: unknown) => {
      assertObject(payload, 'code:execute-direct payload');
      const p = payload as Record<string, unknown>;

      assertString(p.code, 'code', 50_000);
      assertString(p.language, 'language', 20);

      const code = p.code as string;
      const language = p.language as string;

      const config = LANGUAGE_CONFIG[language];
      if (!config) {
        throw new Error(
          `Unsupported language "${language}". Supported: ${Object.keys(LANGUAGE_CONFIG).join(', ')}`,
        );
      }

      // Write code to a temp file
      const tempFile = join(tmpdir(), `nexus-exec-${randomUUID()}${config.ext}`);

      try {
        await writeFile(tempFile, code, 'utf-8');

        const result = await executeWithFallback(
          config.commands,
          [tempFile],
          TIMEOUT_MS,
        );

        return result;
      } finally {
        // Clean up temp file — best effort
        try {
          await unlink(tempFile);
        } catch {
          // Ignore cleanup errors
        }
      }
    },
  );
}
