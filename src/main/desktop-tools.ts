/**
 * desktop-tools.ts — Native Windows desktop automation via PowerShell.
 * Replaces the broken MCP Desktop Commander with direct PowerShell-based tools.
 */

import { execFile } from 'child_process';
import { writeFileSync, unlinkSync } from 'fs';
import { readFile as fsReadFile, writeFile as fsWriteFile, readdir } from 'fs/promises';
import { tmpdir } from 'os';
import path from 'path';
import { clipboard, BrowserWindow } from 'electron';
import { getSanitizedEnv } from './settings';
import { assertSafePath } from './ipc/validate';

interface ToolResult {
  result?: string;
  error?: string;
}

/** Tools that require user confirmation before execution */
const DESTRUCTIVE_TOOLS = new Set([
  'run_command',
  'close_window',
  'launch_app',
  'write_clipboard',
  'set_volume',
  'send_keys',
  'write_file',
  // cLaw Security Fix (CRITICAL-003): Mouse tools can click destructive UI elements
  'mouse_click',
  'mouse_double_click',
  'mouse_right_click',
  'mouse_move',
  'mouse_scroll',
  'mouse_drag',
  // cLaw Security Fix (CRITICAL-004): Keyboard tools can type/execute arbitrary actions
  'type_text',
  'press_keys',
]);

const READ_ONLY_TOOLS = new Set([
  'list_windows',
  'get_active_window',
  'read_clipboard',
  'focus_window',
  'read_screen',
  'read_file',
  'list_directory',
]);

/** Pending confirmation requests, keyed by request ID */
const pendingConfirmations = new Map<string, {
  resolve: (approved: boolean) => void;
  timer: ReturnType<typeof setTimeout>;
}>();

let confirmationId = 0;

/**
 * Send a confirmation request to the renderer and wait for approval.
 * Times out after 30s (auto-deny).
 */
export function requestConfirmation(
  win: BrowserWindow | null,
  toolName: string,
  args: Record<string, unknown>
): Promise<boolean> {
  if (!win || win.isDestroyed()) return Promise.resolve(false);

  const id = String(++confirmationId);
  const description = formatToolDescription(toolName, args);

  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => {
      pendingConfirmations.delete(id);
      resolve(false); // auto-deny on timeout
    }, 30_000);

    pendingConfirmations.set(id, { resolve, timer });
    win.webContents.send('desktop:confirm-request', { id, toolName, description });
  });
}

/** Called from IPC when user responds to a confirmation */
export function handleConfirmationResponse(id: string, approved: boolean): void {
  const pending = pendingConfirmations.get(id);
  if (pending) {
    clearTimeout(pending.timer);
    pendingConfirmations.delete(id);
    pending.resolve(approved);
  }
}

function formatToolDescription(toolName: string, args: Record<string, unknown>): string {
  switch (toolName) {
    case 'run_command':
      return `Run command: ${String(args.command || '').slice(0, 200)}`;
    case 'close_window':
      return `Close window matching: "${args.target}"`;
    case 'launch_app':
      return `Launch application: ${args.app_name}`;
    case 'write_clipboard':
      return `Write to clipboard: "${String(args.text || '').slice(0, 100)}..."`;
    case 'set_volume':
      return `Set system volume to ${args.level}%`;
    case 'send_keys':
      return `Send keystrokes to "${args.target}": "${String(args.keys || '').slice(0, 80)}"`;
    case 'write_file':
      return `Write to file: ${args.file_path}`;
    case 'read_screen':
      return `Read screen content from window: "${args.target}"`;
    case 'read_file':
      return `Read file: ${args.file_path}`;
    case 'list_directory':
      return `List directory: ${args.dir_path}`;
    default:
      return `${toolName}: ${JSON.stringify(args).slice(0, 150)}`;
  }
}

/** Main window reference for confirmation dialogs */
let mainWindowRef: BrowserWindow | null = null;
export function setMainWindow(win: BrowserWindow): void {
  mainWindowRef = win;
}

// --- Security ---

/**
 * Sanitize a string for safe interpolation into PowerShell scripts.
 * Escapes special characters to prevent injection attacks.
 */
function sanitizePS(input: string): string {
  return input
    .replace(/`/g, '``')        // backtick escape
    .replace(/\$/g, '`$')       // dollar sign
    .replace(/'/g, "''")        // single quote (PowerShell escape)
    .replace(/"/g, '`"')        // double quote
    .replace(/[;|&]/g, '')      // remove command chaining operators
    .replace(/\r?\n/g, ' ');    // flatten newlines
}

// --- Win32 Mouse/Keyboard Interop ---

/** C# interop class for mouse control via user32.dll. Prepended to mouse tool PowerShell scripts. */
const MOUSE_INTEROP = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Mouse {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, int dwData, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT lpPoint);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }
    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP = 0x0040;
    public const uint MOUSEEVENTF_WHEEL = 0x0800;
    public const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
    public const uint MOUSEEVENTF_MOVE = 0x0001;
}
"@
`;

/**
 * Convert friendly key names (e.g. "ctrl+c", "alt+tab", "enter") to .NET SendKeys syntax.
 * See: https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.sendkeys
 */
function convertKeysToSendKeysSyntax(keys: string): string {
  // Map of friendly modifier names to SendKeys modifier chars
  const modifierMap: Record<string, string> = {
    ctrl: '^',
    control: '^',
    alt: '%',
    shift: '+',
    win: '^{ESC}', // approximate — no direct SendKeys for Win key
  };

  // Map of friendly key names to SendKeys special key syntax
  const keyMap: Record<string, string> = {
    enter: '{ENTER}',
    return: '{ENTER}',
    tab: '{TAB}',
    escape: '{ESC}',
    esc: '{ESC}',
    backspace: '{BS}',
    delete: '{DEL}',
    del: '{DEL}',
    insert: '{INS}',
    home: '{HOME}',
    end: '{END}',
    pageup: '{PGUP}',
    pagedown: '{PGDN}',
    up: '{UP}',
    down: '{DOWN}',
    left: '{LEFT}',
    right: '{RIGHT}',
    space: ' ',
    f1: '{F1}', f2: '{F2}', f3: '{F3}', f4: '{F4}',
    f5: '{F5}', f6: '{F6}', f7: '{F7}', f8: '{F8}',
    f9: '{F9}', f10: '{F10}', f11: '{F11}', f12: '{F12}',
    capslock: '{CAPSLOCK}',
    numlock: '{NUMLOCK}',
    scrolllock: '{SCROLLLOCK}',
    printscreen: '{PRTSC}',
    break: '{BREAK}',
  };

  const lower = keys.toLowerCase().trim();

  // If it already looks like SendKeys syntax, pass through
  if (/^[{^%+~]/.test(keys) || /\{[A-Z]+\}/.test(keys)) {
    return keys;
  }

  // Split on + to handle combos like "ctrl+shift+s"
  const parts = lower.split('+').map((p) => p.trim());

  if (parts.length === 1) {
    // Single key
    return keyMap[parts[0]] || parts[0];
  }

  // Multiple parts: modifiers + final key
  let prefix = '';
  const finalKey = parts[parts.length - 1];

  for (let i = 0; i < parts.length - 1; i++) {
    const mod = modifierMap[parts[i]];
    if (mod) {
      prefix += mod;
    }
  }

  const mappedKey = keyMap[finalKey] || finalKey;
  // If the mapped key is a special key in braces, wrap with parens for modifier grouping
  if (mappedKey.startsWith('{')) {
    return prefix + mappedKey;
  }
  // For single char keys, just append
  return prefix + mappedKey;
}

// --- PowerShell runner ---

/** Concurrency limiter for PowerShell processes to prevent resource exhaustion. */
const PS_MAX_CONCURRENT = 5;
let psActiveCount = 0;
const psQueue: Array<{ run: () => void }> = [];

function drainPsQueue(): void {
  while (psQueue.length > 0 && psActiveCount < PS_MAX_CONCURRENT) {
    const next = psQueue.shift()!;
    psActiveCount++;
    next.run();
  }
}

/**
 * Run a PowerShell script via a temporary .ps1 file to avoid quote-escaping issues.
 * Enforces a concurrency cap of PS_MAX_CONCURRENT simultaneous processes.
 */
function runPS(script: string, timeoutMs = 15000): Promise<string> {
  return new Promise((resolve, reject) => {
    const execute = () => {
      const tmpFile = path.join(tmpdir(), `eve-ps-${Date.now()}.ps1`);
      writeFileSync(tmpFile, script, 'utf-8');

      // Crypto Sprint 13: Use execFile to avoid shell interpolation of temp path.
      const child = execFile(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', tmpFile],
        { timeout: timeoutMs, maxBuffer: 1024 * 1024, env: getSanitizedEnv() as NodeJS.ProcessEnv },
        (err, stdout, stderr) => {
          try { unlinkSync(tmpFile); } catch { /* cleanup */ }
          psActiveCount--;
          drainPsQueue();
          if (err) {
            reject(new Error(stderr?.trim() || err.message));
          } else {
            resolve(stdout.trim());
          }
        }
      );
      child.stdin?.end();
    };

    if (psActiveCount < PS_MAX_CONCURRENT) {
      psActiveCount++;
      execute();
    } else {
      psQueue.push({ run: execute });
    }
  });
}

// --- Tool Definitions (Gemini function declarations) ---

export const DESKTOP_TOOL_DECLARATIONS = [
  {
    name: 'launch_app',
    description: 'Launch a Windows application by name. Examples: "Spotify", "Chrome", "Notepad", "File Explorer", "Calculator".',
    parameters: {
      type: 'object',
      properties: {
        app_name: {
          type: 'string',
          description: 'Name of the application to launch (e.g. "Spotify", "Chrome", "Terminal").',
        },
      },
      required: ['app_name'],
    },
  },
  {
    name: 'list_windows',
    description: 'List all visible application windows with their process names and window titles.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'focus_window',
    description: 'Bring a window to the foreground by matching its title (partial match, case-insensitive).',
    parameters: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          description: 'Partial window title to match (e.g. "Spotify", "Visual Studio").',
        },
      },
      required: ['target'],
    },
  },
  {
    name: 'close_window',
    description: 'Close an application window by matching its title (partial match, case-insensitive).',
    parameters: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          description: 'Partial window title to match.',
        },
      },
      required: ['target'],
    },
  },
  {
    name: 'set_volume',
    description: 'Set the system audio volume to a specific percentage (0-100).',
    parameters: {
      type: 'object',
      properties: {
        level: {
          type: 'number',
          description: 'Volume level as a percentage, 0 to 100.',
        },
      },
      required: ['level'],
    },
  },
  {
    name: 'get_active_window',
    description: 'Get the title of the currently active (foreground) window.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'run_command',
    description: 'Run a PowerShell command and return the output. Use for system queries, file operations, etc. Output is limited to 4000 characters.',
    parameters: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          description: 'The PowerShell command to execute.',
        },
      },
      required: ['command'],
    },
  },
  {
    name: 'read_clipboard',
    description: 'Read the current contents of the system clipboard.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'write_clipboard',
    description: 'Write text to the system clipboard.',
    parameters: {
      type: 'object',
      properties: {
        text: {
          type: 'string',
          description: 'Text to copy to the clipboard.',
        },
      },
      required: ['text'],
    },
  },
  {
    name: 'send_keys',
    description: 'Send keystrokes to a target window. Focuses the window first, then sends the specified key sequence using SendKeys syntax (e.g. "{ENTER}", "Hello World", "%{F4}" for Alt+F4).',
    parameters: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          description: 'Partial window title to match and focus before sending keys.',
        },
        keys: {
          type: 'string',
          description: 'Keystrokes to send in .NET SendKeys format (e.g. "{ENTER}", "Hello", "^a" for Ctrl+A).',
        },
      },
      required: ['target', 'keys'],
    },
  },
  {
    name: 'read_screen',
    description: 'Read text content from a window using the Windows UI Automation accessibility tree. Returns all visible text elements from the specified window.',
    parameters: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          description: 'Partial window title to match (e.g. "Notepad", "Chrome").',
        },
      },
      required: ['target'],
    },
  },
  {
    name: 'read_file',
    description: 'Read the contents of a file from the filesystem. Returns up to 50KB of text content.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute or relative path to the file to read.',
        },
      },
      required: ['file_path'],
    },
  },
  {
    name: 'write_file',
    description: 'Write content to a file on the filesystem. Creates the file if it does not exist, or overwrites it if it does. Requires user confirmation.',
    parameters: {
      type: 'object',
      properties: {
        file_path: {
          type: 'string',
          description: 'Absolute or relative path to the file to write.',
        },
        content: {
          type: 'string',
          description: 'The text content to write to the file.',
        },
      },
      required: ['file_path', 'content'],
    },
  },
  {
    name: 'list_directory',
    description: 'List files and subdirectories in a directory. Returns names with type indicators ([FILE] or [DIR]).',
    parameters: {
      type: 'object',
      properties: {
        dir_path: {
          type: 'string',
          description: 'Absolute or relative path to the directory to list.',
        },
      },
      required: ['dir_path'],
    },
  },

  // --- Computer Control: Mouse ---
  {
    name: 'mouse_click',
    description: 'Move the mouse cursor to (x, y) screen coordinates and perform a left click. Use with screen capture frames to identify where to click. Coordinates are absolute screen pixels.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'X coordinate (pixels from left edge of screen).' },
        y: { type: 'number', description: 'Y coordinate (pixels from top edge of screen).' },
      },
      required: ['x', 'y'],
    },
  },
  {
    name: 'mouse_double_click',
    description: 'Move the mouse cursor to (x, y) and double-click (left button). Useful for opening files, selecting words, etc.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'X coordinate (pixels from left edge).' },
        y: { type: 'number', description: 'Y coordinate (pixels from top edge).' },
      },
      required: ['x', 'y'],
    },
  },
  {
    name: 'mouse_right_click',
    description: 'Move the mouse cursor to (x, y) and right-click to open a context menu.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'X coordinate (pixels from left edge).' },
        y: { type: 'number', description: 'Y coordinate (pixels from top edge).' },
      },
      required: ['x', 'y'],
    },
  },
  {
    name: 'mouse_move',
    description: 'Move the mouse cursor to (x, y) without clicking. Useful for hovering over elements.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'X coordinate (pixels from left edge).' },
        y: { type: 'number', description: 'Y coordinate (pixels from top edge).' },
      },
      required: ['x', 'y'],
    },
  },
  {
    name: 'mouse_scroll',
    description: 'Scroll the mouse wheel at the current cursor position. Positive amount scrolls up, negative scrolls down.',
    parameters: {
      type: 'object',
      properties: {
        direction: { type: 'string', description: '"up" or "down".' },
        amount: { type: 'number', description: 'Number of scroll clicks (default 3). Each click is 120 units.' },
      },
      required: ['direction'],
    },
  },
  {
    name: 'mouse_drag',
    description: 'Click and drag from one screen position to another. Holds left mouse button, moves, then releases.',
    parameters: {
      type: 'object',
      properties: {
        from_x: { type: 'number', description: 'Starting X coordinate.' },
        from_y: { type: 'number', description: 'Starting Y coordinate.' },
        to_x: { type: 'number', description: 'Ending X coordinate.' },
        to_y: { type: 'number', description: 'Ending Y coordinate.' },
      },
      required: ['from_x', 'from_y', 'to_x', 'to_y'],
    },
  },

  // --- Computer Control: Keyboard ---
  {
    name: 'type_text',
    description: 'Type text at the current cursor/focus position. Does NOT require targeting a specific window — types wherever the cursor currently is. For special keys or shortcuts, use press_keys instead.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'The text to type.' },
      },
      required: ['text'],
    },
  },
  {
    name: 'press_keys',
    description: 'Press a key or key combination at the current focus. Accepts friendly names like "ctrl+c", "alt+tab", "enter", "ctrl+shift+s", "f5", "escape". Types at current focus — no window targeting needed.',
    parameters: {
      type: 'object',
      properties: {
        keys: {
          type: 'string',
          description: 'Key combination in friendly format (e.g. "ctrl+c", "alt+tab", "enter", "ctrl+shift+s", "f5", "backspace", "ctrl+a"). Modifiers: ctrl, alt, shift. Can be combined with +.',
        },
      },
      required: ['keys'],
    },
  },

  // --- Computer Control: Screen Info ---
  {
    name: 'get_screen_size',
    description: 'Get the primary screen resolution (width x height in pixels). Use this to understand the coordinate space for mouse tools.',
    parameters: { type: 'object', properties: {} },
  },
  {
    name: 'get_cursor_position',
    description: 'Get the current mouse cursor position as (x, y) screen coordinates.',
    parameters: { type: 'object', properties: {} },
  },
];

// --- Tool Implementations ---

async function launchApp(appName: string): Promise<ToolResult> {
  // Common app name -> executable mappings
  const aliases: Record<string, string> = {
    chrome: 'chrome',
    'google chrome': 'chrome',
    firefox: 'firefox',
    spotify: 'spotify',
    notepad: 'notepad',
    calculator: 'calc',
    'file explorer': 'explorer',
    explorer: 'explorer',
    terminal: 'wt',
    'windows terminal': 'wt',
    cmd: 'cmd',
    powershell: 'powershell',
    code: 'code',
    'visual studio code': 'code',
    'vs code': 'code',
    vscode: 'code',
    discord: 'discord',
    slack: 'slack',
    teams: 'teams',
    outlook: 'outlook',
    word: 'winword',
    excel: 'excel',
    powerpoint: 'powerpnt',
    paint: 'mspaint',
    snipping: 'snippingtool',
    'task manager': 'taskmgr',
    settings: 'ms-settings:',
  };

  const lower = appName.toLowerCase().trim();
  const exe = aliases[lower];

  try {
    if (exe) {
      // Special handling for UWP protocol launches (ms-settings:, spotify:, etc.)
      if (exe.includes(':')) {
        await runPS(`Start-Process '${sanitizePS(exe)}'`);
      } else {
        await runPS(`Start-Process '${sanitizePS(exe)}'`);
      }
    } else {
      // Search Start Menu for the app
      const safeName = sanitizePS(lower);
      const searchScript = `
$app = Get-StartApps | Where-Object { $_.Name -like '*${safeName}*' } | Select-Object -First 1
if ($app) { Start-Process "shell:AppsFolder\\$($app.AppID)"; Write-Output $app.Name }
else { Start-Process '${sanitizePS(appName)}' }
`;
      await runPS(searchScript);
    }
    return { result: `Launched ${appName}` };
  } catch (err: unknown) {
    return { error: `Failed to launch ${appName}: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function listWindows(): Promise<ToolResult> {
  try {
    const output = await runPS(
      `Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | Select-Object ProcessName, MainWindowTitle | Format-Table -AutoSize | Out-String -Width 200`
    );
    return { result: output || 'No visible windows found.' };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

async function focusWindow(target: string): Promise<ToolResult> {
  const safeTarget = sanitizePS(target);
  try {
    const script = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
$proc = Get-Process | Where-Object { $_.MainWindowTitle -like '*${safeTarget}*' } | Select-Object -First 1
if ($proc) {
    [Win32]::ShowWindow($proc.MainWindowHandle, 9)
    [Win32]::SetForegroundWindow($proc.MainWindowHandle)
    Write-Output "Focused: $($proc.MainWindowTitle)"
} else {
    Write-Output "No window found matching '${safeTarget}'"
}
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

async function closeWindow(target: string): Promise<ToolResult> {
  const safeTarget = sanitizePS(target);
  try {
    const script = `
$proc = Get-Process | Where-Object { $_.MainWindowTitle -like '*${safeTarget}*' } | Select-Object -First 1
if ($proc) {
    $proc.CloseMainWindow() | Out-Null
    Write-Output "Closed: $($proc.MainWindowTitle)"
} else {
    Write-Output "No window found matching '${safeTarget}'"
}
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

async function setVolume(level: number): Promise<ToolResult> {
  const clamped = Math.max(0, Math.min(100, Math.round(level)));
  try {
    // Use AudioDeviceCmdlets or nircmd if available, fallback to SendKeys approach
    const script = `
Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    int _0(); int _1(); int _2(); int _3(); int _4(); int _5(); int _6(); int _7(); int _8(); int _9(); int _10(); int _11();
    int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume ppInterface); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppDevice); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {}
public class Audio {
    public static void SetVolume(float level) {
        var enumerator = new MMDeviceEnumerator() as IMMDeviceEnumerator;
        IMMDevice device;
        enumerator.GetDefaultAudioEndpoint(0, 1, out device);
        var iid = typeof(IAudioEndpointVolume).GUID;
        IAudioEndpointVolume volume;
        device.Activate(ref iid, 1, 0, out volume);
        volume.SetMasterVolumeLevelScalar(level, System.Guid.Empty);
    }
}
"@
[Audio]::SetVolume(${clamped / 100})
`;
    await runPS(script);
    return { result: `Volume set to ${clamped}%` };
  } catch {
    // Fallback: use nircmd or simulated key presses
    try {
      await runPS(`(New-Object -ComObject WScript.Shell).SendKeys([char]173)`);
      return { result: `Volume adjustment attempted (${clamped}%)` };
    } catch (err: unknown) {
      return { error: `Failed to set volume: ${err instanceof Error ? err.message : String(err)}` };
    }
  }
}

async function getActiveWindow(): Promise<ToolResult> {
  try {
    const script = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class FG {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
}
"@
$h = [FG]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 256
[FG]::GetWindowText($h, $sb, 256) | Out-Null
Write-Output $sb.ToString()
`;
    const output = await runPS(script);
    return { result: output || 'Unknown window' };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

async function runCommand(command: string): Promise<ToolResult> {
  try {
    const output = await runPS(command, 30000);
    const truncated = output.length > 4000 ? output.slice(0, 4000) + '\n... (truncated)' : output;
    return { result: truncated || '(no output)' };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

function readClipboard(): ToolResult {
  try {
    const text = clipboard.readText();
    return { result: text || '(clipboard is empty)' };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

function writeClipboard(text: string): ToolResult {
  try {
    clipboard.writeText(text);
    return { result: 'Text copied to clipboard.' };
  } catch (err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) };
  }
}

// --- New Tool Implementations ---

async function sendKeys(target: string, keys: string): Promise<ToolResult> {
  const safeTarget = sanitizePS(target);
  // Keys are passed to SendKeys::SendWait — sanitize to prevent script breakout
  // but preserve SendKeys syntax like {ENTER}, ^a, %{F4}
  const safeKeys = keys
    .replace(/`/g, '``')
    .replace(/\$/g, '`$')
    .replace(/'/g, "''")
    .replace(/"/g, '`"')
    .replace(/[;|&]/g, '')
    .replace(/\r?\n/g, ' ');
  try {
    const script = `
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32SK {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
Add-Type -AssemblyName System.Windows.Forms
$proc = Get-Process | Where-Object { $_.MainWindowTitle -like '*${safeTarget}*' } | Select-Object -First 1
if ($proc) {
    [Win32SK]::ShowWindow($proc.MainWindowHandle, 9)
    [Win32SK]::SetForegroundWindow($proc.MainWindowHandle)
    Start-Sleep -Milliseconds 200
    [System.Windows.Forms.SendKeys]::SendWait('${safeKeys}')
    Write-Output "Sent keys to: $($proc.MainWindowTitle)"
} else {
    Write-Output "No window found matching '${safeTarget}'"
}
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Failed to send keys: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function readScreen(target: string): Promise<ToolResult> {
  const safeTarget = sanitizePS(target);
  try {
    const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$proc = Get-Process | Where-Object { $_.MainWindowTitle -like '*${safeTarget}*' } | Select-Object -First 1
if ($proc) {
    $auto = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
    $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::IsEnabledProperty, $true)
    $children = $auto.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
    $texts = @()
    foreach ($el in $children) {
        $name = $el.Current.Name
        if ($name) { $texts += $name }
    }
    $texts -join "\`n"
} else {
    Write-Output "No window found matching '${safeTarget}'"
}
`;
    const output = await runPS(script, 20000);
    const truncated = output.length > 8000 ? output.slice(0, 8000) + '\n... (truncated)' : output;
    return { result: truncated || '(no text content found)' };
  } catch (err: unknown) {
    return { error: `Failed to read screen: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function readFile(filePath: string): Promise<ToolResult> {
  try {
    // Crypto Sprint 11: Validate path against traversal, UNC, and shell metacharacters.
    assertSafePath(filePath, 'desktop-tools readFile path');
    const MAX_SIZE = 50 * 1024; // 50KB
    const buffer = await fsReadFile(filePath);
    if (buffer.length > MAX_SIZE) {
      const truncated = buffer.slice(0, MAX_SIZE).toString('utf-8');
      return { result: truncated + '\n... (truncated at 50KB)' };
    }
    return { result: buffer.toString('utf-8') || '(empty file)' };
  } catch (err: unknown) {
    return { error: `Failed to read file: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function writeFile(filePath: string, content: string): Promise<ToolResult> {
  try {
    // Crypto Sprint 11: Validate path against traversal, UNC, and shell metacharacters.
    assertSafePath(filePath, 'desktop-tools writeFile path');
    await fsWriteFile(filePath, content, 'utf-8');
    // Notify renderer about file modification
    if (mainWindowRef && !mainWindowRef.isDestroyed()) {
      mainWindowRef.webContents.send('file:modified', {
        path: filePath,
        action: 'write',
        size: content.length,
        timestamp: Date.now(),
      });
    }
    return { result: `File written: ${filePath} (${content.length} characters)` };
  } catch (err: unknown) {
    return { error: `Failed to write file: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function listDirectory(dirPath: string): Promise<ToolResult> {
  try {
    // Crypto Sprint 11: Validate path against traversal, UNC, and shell metacharacters.
    assertSafePath(dirPath, 'desktop-tools listDirectory path');
    const entries = await readdir(dirPath, { withFileTypes: true });
    if (entries.length === 0) {
      return { result: '(empty directory)' };
    }
    const lines = entries.map((entry) => {
      const prefix = entry.isDirectory() ? '[DIR]  ' : '[FILE] ';
      return prefix + entry.name;
    });
    return { result: lines.join('\n') };
  } catch (err: unknown) {
    return { error: `Failed to list directory: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// --- Computer Control: Mouse Tool Implementations ---

async function mouseClick(x: number, y: number): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
[Win32Mouse]::SetCursorPos(${Math.round(x)}, ${Math.round(y)})
Start-Sleep -Milliseconds 50
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 30
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
Write-Output "Clicked at (${Math.round(x)}, ${Math.round(y)})"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse click failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function mouseDoubleClick(x: number, y: number): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
[Win32Mouse]::SetCursorPos(${Math.round(x)}, ${Math.round(y)})
Start-Sleep -Milliseconds 50
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 20
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 80
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 20
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
Write-Output "Double-clicked at (${Math.round(x)}, ${Math.round(y)})"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse double-click failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function mouseRightClick(x: number, y: number): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
[Win32Mouse]::SetCursorPos(${Math.round(x)}, ${Math.round(y)})
Start-Sleep -Milliseconds 50
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 30
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_RIGHTUP, 0, 0, 0, [IntPtr]::Zero)
Write-Output "Right-clicked at (${Math.round(x)}, ${Math.round(y)})"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse right-click failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function mouseMove(x: number, y: number): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
[Win32Mouse]::SetCursorPos(${Math.round(x)}, ${Math.round(y)})
Write-Output "Cursor moved to (${Math.round(x)}, ${Math.round(y)})"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse move failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function mouseScroll(direction: string, amount: number): Promise<ToolResult> {
  const clicks = Math.max(1, Math.round(amount || 3));
  // Positive wheel delta = scroll up, negative = scroll down
  const delta = direction.toLowerCase() === 'up' ? 120 * clicks : -(120 * clicks);
  try {
    const script = `${MOUSE_INTEROP}
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_WHEEL, 0, 0, ${delta}, [IntPtr]::Zero)
Write-Output "Scrolled ${direction} by ${clicks} clicks"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse scroll failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function mouseDrag(fromX: number, fromY: number, toX: number, toY: number): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
# Move to start position
[Win32Mouse]::SetCursorPos(${Math.round(fromX)}, ${Math.round(fromY)})
Start-Sleep -Milliseconds 50
# Press left button
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
Start-Sleep -Milliseconds 100
# Move to end position (smooth drag with intermediate steps)
$steps = 10
$dx = (${Math.round(toX)} - ${Math.round(fromX)}) / $steps
$dy = (${Math.round(toY)} - ${Math.round(fromY)}) / $steps
for ($i = 1; $i -le $steps; $i++) {
    $cx = [int](${Math.round(fromX)} + $dx * $i)
    $cy = [int](${Math.round(fromY)} + $dy * $i)
    [Win32Mouse]::SetCursorPos($cx, $cy)
    Start-Sleep -Milliseconds 20
}
# Release left button
[Win32Mouse]::mouse_event([Win32Mouse]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [IntPtr]::Zero)
Write-Output "Dragged from (${Math.round(fromX)}, ${Math.round(fromY)}) to (${Math.round(toX)}, ${Math.round(toY)})"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Mouse drag failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// --- Computer Control: Keyboard Tool Implementations ---

async function typeText(text: string): Promise<ToolResult> {
  // Escape for SendKeys but preserve the actual text characters
  // SendKeys special chars that need escaping: +, ^, %, ~, (, ), {, }
  const escaped = text
    .replace(/([+^%~(){}])/g, '{$1}')  // Wrap special SendKeys chars in braces
    .replace(/'/g, "''");               // Escape single quotes for PowerShell
  try {
    const script = `
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
Write-Output "Typed ${text.length} characters"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Type text failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function pressKeys(keys: string): Promise<ToolResult> {
  const sendKeysStr = convertKeysToSendKeysSyntax(keys);
  // Escape single quotes for PowerShell string
  const escaped = sendKeysStr.replace(/'/g, "''");
  try {
    const script = `
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
Write-Output "Pressed: ${keys}"
`;
    const output = await runPS(script);
    return { result: output };
  } catch (err: unknown) {
    return { error: `Press keys failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// --- Computer Control: Screen Info Implementations ---

async function getScreenSize(): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
$w = [Win32Mouse]::GetSystemMetrics(0)
$h = [Win32Mouse]::GetSystemMetrics(1)
Write-Output "$w x $h"
`;
    const output = await runPS(script);
    return { result: `Screen size: ${output} pixels` };
  } catch (err: unknown) {
    return { error: `Get screen size failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

async function getCursorPosition(): Promise<ToolResult> {
  try {
    const script = `${MOUSE_INTEROP}
$point = New-Object Win32Mouse+POINT
[Win32Mouse]::GetCursorPos([ref]$point) | Out-Null
Write-Output "$($point.X), $($point.Y)"
`;
    const output = await runPS(script);
    return { result: `Cursor position: (${output})` };
  } catch (err: unknown) {
    return { error: `Get cursor position failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

// --- Router ---

export async function callDesktopTool(
  name: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  // Gate destructive tools behind user confirmation
  if (DESTRUCTIVE_TOOLS.has(name)) {
    const approved = await requestConfirmation(mainWindowRef, name, args);
    if (!approved) {
      return { result: `Action cancelled — user denied: ${name}` };
    }
  }

  switch (name) {
    case 'launch_app':
      return launchApp(String(args.app_name || ''));
    case 'list_windows':
      return listWindows();
    case 'focus_window':
      return focusWindow(String(args.target || ''));
    case 'close_window':
      return closeWindow(String(args.target || ''));
    case 'set_volume':
      return setVolume(Number(args.level ?? 50));
    case 'get_active_window':
      return getActiveWindow();
    case 'run_command':
      return runCommand(String(args.command || ''));
    case 'read_clipboard':
      return readClipboard();
    case 'write_clipboard':
      return writeClipboard(String(args.text || ''));
    case 'send_keys':
      return sendKeys(String(args.target || ''), String(args.keys || ''));
    case 'read_screen':
      return readScreen(String(args.target || ''));
    case 'read_file':
      return readFile(String(args.file_path || ''));
    case 'write_file':
      return writeFile(String(args.file_path || ''), String(args.content || ''));
    case 'list_directory':
      return listDirectory(String(args.dir_path || ''));

    // --- Computer Control: Mouse ---
    case 'mouse_click':
      return mouseClick(Number(args.x ?? 0), Number(args.y ?? 0));
    case 'mouse_double_click':
      return mouseDoubleClick(Number(args.x ?? 0), Number(args.y ?? 0));
    case 'mouse_right_click':
      return mouseRightClick(Number(args.x ?? 0), Number(args.y ?? 0));
    case 'mouse_move':
      return mouseMove(Number(args.x ?? 0), Number(args.y ?? 0));
    case 'mouse_scroll':
      return mouseScroll(String(args.direction || 'down'), Number(args.amount ?? 3));
    case 'mouse_drag':
      return mouseDrag(
        Number(args.from_x ?? 0), Number(args.from_y ?? 0),
        Number(args.to_x ?? 0), Number(args.to_y ?? 0)
      );

    // --- Computer Control: Keyboard ---
    case 'type_text':
      return typeText(String(args.text || ''));
    case 'press_keys':
      return pressKeys(String(args.keys || ''));

    // --- Computer Control: Screen Info ---
    case 'get_screen_size':
      return getScreenSize();
    case 'get_cursor_position':
      return getCursorPosition();

    default:
      return { error: `Unknown desktop tool: ${name}` };
  }
}
