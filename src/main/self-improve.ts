/**
 * self-improve.ts — Friday's self-modification system.
 * Allows Friday to read and propose changes to her own source code.
 * ALL changes require explicit user approval before being written.
 */

import { BrowserWindow } from 'electron';
import fs from 'fs/promises';
import path from 'path';

// The project root — Friday's own codebase
export const PROJECT_ROOT = path.resolve(__dirname, '../../');

// Allowed file extensions for reading/writing
const ALLOWED_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.json', '.css', '.html', '.md',
]);

// Files/dirs that should never be modified
const PROTECTED_PATHS = new Set([
  'node_modules', '.git', 'dist', 'out', 'package-lock.json',
]);

/** Hot-reload handlers — keyed by relative file path pattern */
const hotReloadHandlers = new Map<string, () => Promise<void>>();

export function registerHotReload(pattern: string, handler: () => Promise<void>) {
  hotReloadHandlers.set(pattern, handler);
}

interface PendingChange {
  id: string;
  filePath: string;
  description: string;
  diff: string;
  newContent: string;
  originalContent: string;
  resolve: (approved: boolean) => void;
  timer: ReturnType<typeof setTimeout>;
}

const pendingChanges = new Map<string, PendingChange>();
let mainWindowRef: BrowserWindow | null = null;

export function setMainWindowForSelfImprove(win: BrowserWindow) {
  mainWindowRef = win;
}

/** Validate that a file path is within the project and is allowed */
function validatePath(filePath: string): { valid: boolean; resolved: string; error?: string } {
  const resolved = path.resolve(PROJECT_ROOT, filePath);

  // Must be within project root
  if (!resolved.startsWith(PROJECT_ROOT)) {
    return { valid: false, resolved, error: 'Path escapes project root' };
  }

  // Check protected paths
  const relative = path.relative(PROJECT_ROOT, resolved);
  const parts = relative.split(path.sep);
  for (const part of parts) {
    if (PROTECTED_PATHS.has(part)) {
      return { valid: false, resolved, error: `Protected path: ${part}` };
    }
  }

  // Check extension
  const ext = path.extname(resolved).toLowerCase();
  if (ext && !ALLOWED_EXTENSIONS.has(ext)) {
    return { valid: false, resolved, error: `Disallowed extension: ${ext}` };
  }

  return { valid: true, resolved };
}

/** Read a project file — safe, no approval needed */
export async function readProjectFile(filePath: string): Promise<string> {
  const { valid, resolved, error } = validatePath(filePath);
  if (!valid) throw new Error(error);

  const content = await fs.readFile(resolved, 'utf-8');
  return content;
}

/** List files in a project directory */
export async function listProjectFiles(dirPath: string): Promise<string[]> {
  const { valid, resolved, error } = validatePath(dirPath || '.');
  if (!valid) throw new Error(error);

  const entries = await fs.readdir(resolved, { withFileTypes: true });
  return entries.map((e) => {
    const prefix = e.isDirectory() ? '[DIR] ' : '[FILE] ';
    return prefix + e.name;
  });
}

/** Generate a simple diff between old and new content */
function generateDiff(original: string, modified: string, filePath: string): string {
  const oldLines = original.split('\n');
  const newLines = modified.split('\n');
  const diffLines: string[] = [`--- a/${filePath}`, `+++ b/${filePath}`];

  // Simple line-by-line diff (not a full Myers diff, but good enough for review)
  const maxLen = Math.max(oldLines.length, newLines.length);
  let inHunk = false;
  let hunkStart = -1;
  const hunkLines: string[] = [];

  for (let i = 0; i < maxLen; i++) {
    const oldLine = i < oldLines.length ? oldLines[i] : undefined;
    const newLine = i < newLines.length ? newLines[i] : undefined;

    if (oldLine !== newLine) {
      if (!inHunk) {
        inHunk = true;
        hunkStart = Math.max(0, i - 2);
        // Context lines before
        for (let j = hunkStart; j < i; j++) {
          if (j < oldLines.length) hunkLines.push(` ${oldLines[j]}`);
        }
      }
      if (oldLine !== undefined && (newLine === undefined || oldLine !== newLine)) {
        hunkLines.push(`-${oldLine}`);
      }
      if (newLine !== undefined && (oldLine === undefined || oldLine !== newLine)) {
        hunkLines.push(`+${newLine}`);
      }
    } else if (inHunk) {
      // Context after change
      hunkLines.push(` ${oldLine}`);
      if (hunkLines.filter((l) => l.startsWith('+') || l.startsWith('-')).length > 0) {
        // Check if we've had enough context after the change
        const afterContext = hunkLines.slice(-3).every((l) => l.startsWith(' '));
        if (afterContext) {
          diffLines.push(`@@ -${hunkStart + 1} @@`);
          diffLines.push(...hunkLines);
          hunkLines.length = 0;
          inHunk = false;
        }
      }
    }
  }

  // Flush remaining hunk
  if (hunkLines.length > 0) {
    diffLines.push(`@@ -${hunkStart + 1} @@`);
    diffLines.push(...hunkLines);
  }

  return diffLines.join('\n');
}

/** Propose a code change — sends to renderer for user approval */
export async function proposeCodeChange(
  filePath: string,
  newContent: string,
  description: string
): Promise<{ approved: boolean; message: string }> {
  const { valid, resolved, error } = validatePath(filePath);
  if (!valid) return { approved: false, message: `Invalid path: ${error}` };

  if (!mainWindowRef || mainWindowRef.isDestroyed()) {
    return { approved: false, message: 'No window available for approval' };
  }

  // Read original content
  let originalContent = '';
  try {
    originalContent = await fs.readFile(resolved, 'utf-8');
  } catch {
    // New file — that's fine
  }

  const diff = generateDiff(originalContent, newContent, filePath);
  const id = crypto.randomUUID();

  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      pendingChanges.delete(id);
      resolve({ approved: false, message: 'Change request timed out (60s)' });
    }, 60_000);

    pendingChanges.set(id, {
      id,
      filePath: resolved,
      description,
      diff,
      newContent,
      originalContent,
      resolve: (approved) => {
        clearTimeout(timer);
        pendingChanges.delete(id);
        resolve({
          approved,
          message: approved ? 'Change approved and applied' : 'Change denied by user',
        });
      },
      timer,
    });

    // Send to renderer for approval
    mainWindowRef!.webContents.send('self-improve:propose', {
      id,
      filePath,
      description,
      diff,
    });
  });
}

/** Handle user response to a proposed change */
export async function handleChangeResponse(
  id: string,
  approved: boolean
): Promise<void> {
  const pending = pendingChanges.get(id);
  if (!pending) return;

  if (approved) {
    // Write the change
    try {
      // Ensure directory exists
      await fs.mkdir(path.dirname(pending.filePath), { recursive: true });
      await fs.writeFile(pending.filePath, pending.newContent, 'utf-8');
      console.log(`[SelfImprove] Change applied: ${pending.filePath}`);

      // Hot-reload if a handler is registered for this file
      const relative = path.relative(PROJECT_ROOT, pending.filePath);
      for (const [pattern, handler] of hotReloadHandlers.entries()) {
        if (relative.includes(pattern) || relative.endsWith(pattern)) {
          try {
            console.log(`[SelfImprove] Hot-reloading: ${pattern}`);
            await handler();
          } catch (reloadErr) {
            console.warn(`[SelfImprove] Hot-reload failed for ${pattern}:`, reloadErr);
          }
          break;
        }
      }
    } catch (err) {
      // Crypto Sprint 17: Sanitize error output.
      console.error('[SelfImprove] Failed to write change:', err instanceof Error ? err.message : 'Unknown error');
      pending.resolve(false);
      return;
    }
  }

  pending.resolve(approved);
}

/** Force re-import of a module by clearing the require cache */
export function invalidateModuleCache(filePath: string): void {
  const resolved = path.resolve(PROJECT_ROOT, filePath);
  // Clear from Node's require cache
  delete require.cache[resolved];
  // Also try with common extensions
  for (const ext of ['.js', '.ts']) {
    delete require.cache[resolved + ext];
    delete require.cache[resolved.replace(/\.[jt]sx?$/, ext)];
  }
  console.log(`[SelfImprove] Module cache invalidated: ${resolved}`);
}

/** Tool declarations for Gemini */
export const SELF_IMPROVE_TOOLS = [
  {
    name: 'read_own_source',
    description:
      'Read one of your own source code files. Use this to understand your current implementation before proposing changes. Path is relative to the project root (e.g. "src/main/personality.ts").',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Relative path to the file within the Agent Friday project.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'list_own_files',
    description:
      'List files in a directory of your own source code. Path is relative to project root (e.g. "src/main" or "src/renderer/components").',
    parameters: {
      type: 'object',
      properties: {
        dir_path: {
          type: 'string',
          description: 'Relative directory path within the Agent Friday project.',
        },
      },
      required: ['dir_path'],
    },
  },
  {
    name: 'propose_code_change',
    description:
      'Propose a change to your own source code. The user will see a diff and must approve before the change is applied. Use this to fix bugs in yourself, add new capabilities, or improve your own code. IMPORTANT: Always read the file first with read_own_source, make targeted changes, and explain clearly what you are changing and why.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Relative path to the file to modify (e.g. "src/main/personality.ts").',
        },
        new_content: {
          type: 'string',
          description: 'The complete new content for the file.',
        },
        description: {
          type: 'string',
          description:
            'Clear description of what is being changed and why. This is shown to the user for approval.',
        },
      },
      required: ['file_path', 'new_content', 'description'],
    },
  },
];
