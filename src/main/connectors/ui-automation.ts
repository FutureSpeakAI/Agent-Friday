/**
 * ui-automation.ts — Windows UI Automation connector.
 *
 * Gives an AI agent full control over ANY Windows application via
 * the Windows UI Automation API (System.Windows.Automation) and
 * Win32 P/Invoke for mouse/keyboard input.
 *
 * Runs entirely in the Electron main process using PowerShell as the
 * interop bridge to .NET and Win32.
 */

import { execFileSync } from 'child_process';
import { writeFileSync, unlinkSync } from 'fs';
import { tmpdir } from 'os';
import path from 'path';

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

/** Default timeout for PowerShell operations (ms). */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Max characters returned from ui_inspect_window to prevent context overflow. */
const INSPECT_MAX_CHARS = 5000;

/** Polling interval for ui_wait_for_element (ms). */
const POLL_INTERVAL_MS = 500;

/** Default wait timeout for ui_wait_for_element (ms). */
const DEFAULT_WAIT_TIMEOUT_MS = 10_000;

/** Window titles that must never be automated (security dialogs, UAC, etc.). */
const BLOCKED_WINDOW_PATTERNS = [
  /user account control/i,
  /windows security/i,
  /credential/i,
  /smartscreen/i,
  /uac/i,
];

/** Control types that must never receive automated input. */
const BLOCKED_CONTROL_TYPES = [
  'ControlType.Edit', // only blocked when associated with password — checked dynamically
];

/** In-memory audit log. */
const auditLog: Array<{ ts: string; tool: string; args: Record<string, unknown>; ok: boolean; note?: string }> = [];

// ---------------------------------------------------------------------------
// PowerShell preambles (loaded once per script invocation)
// ---------------------------------------------------------------------------

/**
 * .NET assemblies required for UI Automation.
 * UIAutomationClient + UIAutomationTypes give access to AutomationElement,
 * TreeWalker, patterns, etc.  System.Windows.Forms gives SendKeys.
 */
const PS_PREAMBLE_UIA = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
`;

/**
 * Win32 P/Invoke for low-level mouse, keyboard, and window management.
 */
const PS_PREAMBLE_WIN32 = `
if (-not ([System.Management.Automation.PSTypeName]'Win32').Type) {
  Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, int dwData, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, IntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT lpPoint);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr hWnd, out int processId);
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    public const uint MOUSEEVENTF_MOVE       = 0x0001;
    public const uint MOUSEEVENTF_LEFTDOWN   = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP     = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN  = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP    = 0x0010;
    public const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
    public const uint MOUSEEVENTF_MIDDLEUP   = 0x0040;
    public const uint MOUSEEVENTF_WHEEL      = 0x0800;
    public const uint MOUSEEVENTF_ABSOLUTE   = 0x8000;
    public const int  SW_RESTORE             = 9;
    public const int  SW_SHOW                = 5;
    public const uint KEYEVENTF_KEYUP        = 0x0002;
}
"@
}
`;

/**
 * System.Drawing assembly for screenshots.
 */
const PS_PREAMBLE_DRAWING = `
Add-Type -AssemblyName System.Drawing
`;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Sanitize a string for safe interpolation into single-quoted PowerShell strings.
 * Escapes single quotes (the only character that breaks single-quoted PS strings).
 */
function sanitizePS(input: string): string {
  return input.replace(/'/g, "''");
}

/**
 * Execute a PowerShell script synchronously and return stdout as a string.
 * Throws on non-zero exit code or timeout.
 */
function runPS(script: string, timeout: number = DEFAULT_TIMEOUT_MS): string {
  // Write to a temp .ps1 file to avoid command-line length limits and encoding issues.
  const tmpFile = path.join(tmpdir(), `nexus-uia-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.ps1`);
  try {
    // BOM + UTF-8 so PowerShell reads Unicode correctly.
    writeFileSync(tmpFile, '\uFEFF' + script, 'utf-8');
    // Crypto Sprint 13: Use execFileSync to avoid shell interpolation of temp path.
    const result = execFileSync(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', tmpFile],
      {
        timeout,
        maxBuffer: 10 * 1024 * 1024, // 10 MB
        encoding: 'utf-8',
        windowsHide: true,
      },
    );
    return (result ?? '').trim();
  } finally {
    try { unlinkSync(tmpFile); } catch { /* ignore cleanup errors */ }
  }
}

/**
 * Build a PowerShell snippet that locates a top-level window handle by
 * title (partial, case-insensitive), process name, or PID.
 * Returns the variable `$hwnd` (IntPtr) and `$proc` (Process object).
 */
function psWindowLocator(args: { title?: string; process_name?: string; pid?: number }): string {
  const { title, process_name, pid } = args;

  if (pid) {
    return `
$proc = Get-Process -Id ${Number(pid)} -ErrorAction Stop
$hwnd = $proc.MainWindowHandle
if ($hwnd -eq [IntPtr]::Zero) { throw "Process ${Number(pid)} has no main window." }
`;
  }

  if (process_name) {
    const safe = sanitizePS(String(process_name));
    return `
$procs = Get-Process -Name '${safe}' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero }
if (-not $procs -or $procs.Count -eq 0) { throw "No window found for process '${safe}'." }
$proc = $procs | Select-Object -First 1
$hwnd = $proc.MainWindowHandle
`;
  }

  if (title) {
    const safe = sanitizePS(String(title));
    return `
$procs = Get-Process | Where-Object { $_.MainWindowTitle -like '*${safe}*' -and $_.MainWindowHandle -ne [IntPtr]::Zero }
if (-not $procs -or $procs.Count -eq 0) { throw "No window found matching title '${safe}'." }
$proc = $procs | Select-Object -First 1
$hwnd = $proc.MainWindowHandle
`;
  }

  return `
$hwnd = [Win32]::GetForegroundWindow()
$pid2 = 0
[void][Win32]::GetWindowThreadProcessId($hwnd, [ref]$pid2)
$proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
`;
}

/**
 * Check whether a window title matches any blocked security pattern.
 */
function isBlockedWindow(title?: string): boolean {
  if (!title) return false;
  return BLOCKED_WINDOW_PATTERNS.some((re) => re.test(title));
}

/**
 * Append to the in-memory audit log.
 */
function audit(tool: string, args: Record<string, unknown>, ok: boolean, note?: string): void {
  auditLog.push({ ts: new Date().toISOString(), tool, args, ok, note });
  // Keep log bounded — retain last 1000 entries.
  if (auditLog.length > 1000) auditLog.splice(0, auditLog.length - 1000);
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // 1 -----------------------------------------------------------------------
  {
    name: 'ui_list_windows',
    description:
      'List all visible top-level windows on the system. Returns process name, PID, and window title for every window that has a non-empty title.',
    parameters: { type: 'object', properties: {}, required: [] },
  },
  // 2 -----------------------------------------------------------------------
  {
    name: 'ui_focus_window',
    description:
      'Bring a window to the foreground. Identify the window by (partial) title, process name, or PID.',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Partial window title to match (case-insensitive).' },
        process_name: { type: 'string', description: 'Exact process name (e.g. "notepad").' },
        pid: { type: 'number', description: 'Process ID.' },
      },
      required: [],
    },
  },
  // 3 -----------------------------------------------------------------------
  {
    name: 'ui_inspect_window',
    description:
      'Retrieve the UI Automation element tree of a window. Shows Name, ControlType, AutomationId, and BoundingRectangle for each element. Use depth to control how deep to walk (default 3). Output capped at 5 000 chars.',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Partial window title.' },
        process_name: { type: 'string', description: 'Process name.' },
        pid: { type: 'number', description: 'Process ID.' },
        depth: { type: 'number', description: 'Max tree depth (default 3).' },
      },
      required: [],
    },
  },
  // 4 -----------------------------------------------------------------------
  {
    name: 'ui_click_element',
    description:
      'Click a UI element inside a window. Identify the element by name, automation ID, and/or control type. The element\'s center point is computed from its BoundingRectangle and a Win32 mouse click is simulated.',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Partial window title to find the parent window.' },
        element_name: { type: 'string', description: 'Name property of the target element.' },
        automation_id: { type: 'string', description: 'AutomationId of the target element.' },
        control_type: { type: 'string', description: 'ControlType filter (e.g. "Button", "MenuItem").' },
      },
      required: ['window_title'],
    },
  },
  // 5 -----------------------------------------------------------------------
  {
    name: 'ui_type_text',
    description:
      'Type text into the currently focused element. Optionally focuses a window first. Uses SendKeys for reliable text entry including special characters.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'The text to type.' },
        window_title: { type: 'string', description: 'Optional: focus this window before typing.' },
      },
      required: ['text'],
    },
  },
  // 6 -----------------------------------------------------------------------
  {
    name: 'ui_send_keys',
    description:
      'Send keyboard shortcuts using .NET SendKeys syntax. Examples: "{ENTER}" for Enter, "^c" for Ctrl+C, "%{F4}" for Alt+F4, "+{TAB}" for Shift+Tab. Optionally focuses a window first.',
    parameters: {
      type: 'object',
      properties: {
        keys: { type: 'string', description: 'Keys in SendKeys format.' },
        window_title: { type: 'string', description: 'Optional: focus this window first.' },
      },
      required: ['keys'],
    },
  },
  // 7 -----------------------------------------------------------------------
  {
    name: 'ui_get_element_value',
    description:
      'Read the text or value of a UI element. Uses ValuePattern or the element Name as fallback. Identify the element by name or automation ID inside the given window.',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Partial window title.' },
        element_name: { type: 'string', description: 'Name of the element.' },
        automation_id: { type: 'string', description: 'AutomationId of the element.' },
      },
      required: ['window_title'],
    },
  },
  // 8 -----------------------------------------------------------------------
  {
    name: 'ui_set_element_value',
    description:
      'Set the value of a UI element (text fields, combo boxes, etc.) using the ValuePattern. Will not set values on password fields for security.',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Partial window title.' },
        element_name: { type: 'string', description: 'Name of the element.' },
        automation_id: { type: 'string', description: 'AutomationId of the element.' },
        value: { type: 'string', description: 'The value to set.' },
      },
      required: ['window_title', 'value'],
    },
  },
  // 9 -----------------------------------------------------------------------
  {
    name: 'ui_screenshot_window',
    description:
      'Take a screenshot of a specific window (or the entire screen if no window specified). Returns the file path to the saved PNG image.',
    parameters: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Partial window title.' },
        process_name: { type: 'string', description: 'Process name.' },
      },
      required: [],
    },
  },
  // 10 ----------------------------------------------------------------------
  {
    name: 'ui_wait_for_element',
    description:
      'Wait for a UI element to appear in a window. Polls every 500 ms until the element is found or timeout is reached (default 10 s).',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Partial window title.' },
        element_name: { type: 'string', description: 'Name of the element to wait for.' },
        automation_id: { type: 'string', description: 'AutomationId of the element.' },
        timeout_ms: { type: 'number', description: 'Max wait in milliseconds (default 10000).' },
      },
      required: ['window_title'],
    },
  },
  // 11 ----------------------------------------------------------------------
  {
    name: 'ui_mouse_move_click',
    description:
      'Move the mouse to absolute screen coordinates and click. Supports left, right, middle buttons and double-click.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'X coordinate (pixels from left edge of screen).' },
        y: { type: 'number', description: 'Y coordinate (pixels from top edge of screen).' },
        button: { type: 'string', enum: ['left', 'right', 'middle'], description: 'Mouse button (default left).' },
        double_click: { type: 'boolean', description: 'If true, double-click instead of single.' },
      },
      required: ['x', 'y'],
    },
  },
  // 12 ----------------------------------------------------------------------
  {
    name: 'ui_scroll',
    description:
      'Scroll the mouse wheel in a window. Positive amount scrolls up, negative scrolls down. Each unit is roughly one "notch" (120 delta units).',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Optional: focus this window first.' },
        direction: { type: 'string', enum: ['up', 'down'], description: 'Scroll direction.' },
        amount: { type: 'number', description: 'Number of scroll notches (default 3).' },
      },
      required: ['direction'],
    },
  },
  // 13 ----------------------------------------------------------------------
  {
    name: 'ui_menu_click',
    description:
      'Navigate an application menu hierarchy by name. Provide an ordered array of menu item names, e.g. ["File", "Save As"]. Each item is found via UI Automation and invoked.',
    parameters: {
      type: 'object',
      properties: {
        window_title: { type: 'string', description: 'Partial window title.' },
        menu_path: {
          type: 'array',
          items: { type: 'string' },
          description: 'Ordered menu item names, e.g. ["File", "Save As"].',
        },
      },
      required: ['window_title', 'menu_path'],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

async function toolListWindows(): Promise<ToolResult> {
  const script = `
Get-Process | Where-Object { $_.MainWindowTitle -ne '' } |
  Sort-Object -Property MainWindowTitle |
  ForEach-Object {
    "$($_.ProcessName) | PID $($_.Id) | $($_.MainWindowTitle)"
  }
`;
  const out = runPS(script);
  return { result: out || '(no visible windows found)' };
}

// ---------------------------------------------------------------------------

async function toolFocusWindow(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.title as string | undefined;
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  const script = `
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title: title, process_name: args.process_name as string | undefined, pid: args.pid as number | undefined })}
[void][Win32]::ShowWindow($hwnd, [Win32]::SW_RESTORE)
[void][Win32]::SetForegroundWindow($hwnd)
Write-Output "Focused: $($proc.MainWindowTitle) (PID $($proc.Id))"
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolInspectWindow(args: Record<string, unknown>): Promise<ToolResult> {
  const depth = Math.min(Math.max(Number(args.depth) || 3, 1), 8);
  const maxChars = INSPECT_MAX_CHARS;

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title: args.title as string | undefined, process_name: args.process_name as string | undefined, pid: args.pid as number | undefined })}

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$sb = New-Object System.Text.StringBuilder

function Walk-Tree($el, $indent, $depthLeft) {
    if ($depthLeft -le 0) { return }
    if ($sb.Length -gt ${maxChars}) { return }
    $name = $el.Current.Name
    $ct   = $el.Current.ControlType.ProgrammaticName
    $aid  = $el.Current.AutomationId
    $rect = $el.Current.BoundingRectangle
    $rectStr = if ($rect -ne [System.Windows.Rect]::Empty) {
        "$([int]$rect.X),$([int]$rect.Y),$([int]$rect.Width)x$([int]$rect.Height)"
    } else { "" }

    [void]$sb.AppendLine("$indent[$ct] Name='$name' AutomationId='$aid' Rect=$rectStr")

    $child = $walker.GetFirstChild($el)
    while ($child -ne $null) {
        Walk-Tree $child "$indent  " ($depthLeft - 1)
        $child = $walker.GetNextSibling($child)
    }
}

Walk-Tree $root "" ${depth}

if ($sb.Length -gt ${maxChars}) {
    $sb.Length = ${maxChars}
    [void]$sb.AppendLine("... (truncated at ${maxChars} chars)")
}
Write-Output $sb.ToString()
`;
  const out = runPS(script, DEFAULT_TIMEOUT_MS);
  return { result: out || '(empty tree)' };
}

// ---------------------------------------------------------------------------

/**
 * Build a PowerShell snippet that finds a child automation element
 * by name, automation ID, and/or control type inside a window.
 * Result is stored in `$target`.
 */
function psFindElement(args: {
  element_name?: string;
  automation_id?: string;
  control_type?: string;
}): string {
  const conditions: string[] = [];

  if (args.automation_id) {
    const safe = sanitizePS(String(args.automation_id));
    conditions.push(
      `New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '${safe}')`,
    );
  }

  if (args.element_name) {
    const safe = sanitizePS(String(args.element_name));
    conditions.push(
      `New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, '${safe}')`,
    );
  }

  if (args.control_type) {
    const safe = sanitizePS(String(args.control_type));
    conditions.push(
      `New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::${safe})`,
    );
  }

  if (conditions.length === 0) {
    return `throw "You must specify at least element_name, automation_id, or control_type."`;
  }

  let conditionExpr: string;
  if (conditions.length === 1) {
    conditionExpr = conditions[0];
  } else {
    conditionExpr = `New-Object System.Windows.Automation.AndCondition(${conditions.join(', ')})`;
  }

  return `
$cond = ${conditionExpr}
$target = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if ($target -eq $null) { throw "Element not found." }
`;
}

/**
 * PowerShell snippet that checks if the found element is a password field
 * and aborts if so.
 */
const PS_CHECK_PASSWORD = `
$isPassword = $false
try {
    $isPassword = $target.Current.ControlType.Id -eq [System.Windows.Automation.ControlType]::Edit.Id -and $target.Current.IsPassword
} catch {}
if ($isPassword) { throw "SECURITY: Refusing to automate a password field." }
`;

async function toolClickElement(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string;
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 100

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
${psFindElement({
    element_name: args.element_name as string | undefined,
    automation_id: args.automation_id as string | undefined,
    control_type: args.control_type as string | undefined,
  })}

$rect = $target.Current.BoundingRectangle
if ($rect -eq [System.Windows.Rect]::Empty) { throw "Element has no bounding rectangle." }

$cx = [int]($rect.X + $rect.Width / 2)
$cy = [int]($rect.Y + $rect.Height / 2)

[Win32]::SetCursorPos($cx, $cy)
Start-Sleep -Milliseconds 50
[Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [IntPtr]::Zero)
[Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTUP,   0, 0, 0, [IntPtr]::Zero)

Write-Output "Clicked '$($target.Current.Name)' at ($cx, $cy)."
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolTypeText(args: Record<string, unknown>): Promise<ToolResult> {
  const text = String(args.text ?? '');
  const title = args.window_title as string | undefined;
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  // Escape characters that SendKeys treats as special: +, ^, %, ~, (, ), {, }
  // We wrap each special char in braces so it is sent literally.
  const escaped = text.replace(/([+^%~(){}[\]])/g, '{$1}');
  const safe = sanitizePS(escaped);

  const focusPart = title
    ? `
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 150
`
    : '';

  const script = `
${PS_PREAMBLE_UIA}
${focusPart}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('${safe}')
Write-Output "Typed ${text.length} characters."
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolSendKeys(args: Record<string, unknown>): Promise<ToolResult> {
  const keys = String(args.keys ?? '');
  const title = args.window_title as string | undefined;
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  const safe = sanitizePS(keys);

  const focusPart = title
    ? `
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 150
`
    : '';

  const script = `
${focusPart}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('${safe}')
Write-Output "Sent keys: ${safe}"
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolGetElementValue(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string;

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
${psFindElement({
    element_name: args.element_name as string | undefined,
    automation_id: args.automation_id as string | undefined,
  })}

# Try ValuePattern first, then TextPattern, then fall back to Name.
$value = $null
try {
    $vp = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $value = $vp.Current.Value
} catch {}

if ($value -eq $null) {
    try {
        $tp = $target.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
        $range = $tp.DocumentRange
        $value = $range.GetText(4096)
    } catch {}
}

if ($value -eq $null) {
    $value = $target.Current.Name
}

Write-Output $value
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolSetElementValue(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string;
  const value = String(args.value ?? '');
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  const safeValue = sanitizePS(value);

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
${psFindElement({
    element_name: args.element_name as string | undefined,
    automation_id: args.automation_id as string | undefined,
  })}

${PS_CHECK_PASSWORD}

try {
    $vp = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $vp.SetValue('${safeValue}')
    Write-Output "Value set to '${safeValue}' on '$($target.Current.Name)'."
} catch {
    throw "Element does not support ValuePattern: $_"
}
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolScreenshotWindow(args: Record<string, unknown>): Promise<ToolResult> {
  const outPath = path.join(tmpdir(), `nexus-screenshot-${Date.now()}.png`);
  const safeOutPath = outPath.replace(/\\/g, '\\\\');

  const hasTarget = args.title || args.process_name;

  const script = `
${PS_PREAMBLE_WIN32}
${PS_PREAMBLE_DRAWING}
${hasTarget ? psWindowLocator({ title: args.title as string | undefined, process_name: args.process_name as string | undefined }) : ''}

${hasTarget ? `
[void][Win32]::ShowWindow($hwnd, [Win32]::SW_RESTORE)
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 300

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinRect {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@

$r = New-Object WinRect+RECT
[void][WinRect]::GetWindowRect($hwnd, [ref]$r)
$x = $r.Left
$y = $r.Top
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
` : `
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$x = $screen.Bounds.X
$y = $screen.Bounds.Y
$w = $screen.Bounds.Width
$h = $screen.Bounds.Height
Add-Type -AssemblyName System.Windows.Forms
`}

if ($w -le 0 -or $h -le 0) { throw "Invalid window dimensions: $w x $h" }

$bmp = New-Object System.Drawing.Bitmap($w, $h)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $h)))
$gfx.Dispose()
$bmp.Save('${safeOutPath.replace(/'/g, "''")}', [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output '${safeOutPath.replace(/'/g, "''")}'
`;
  const out = runPS(script, DEFAULT_TIMEOUT_MS);
  return { result: `Screenshot saved: ${out || outPath}` };
}

// ---------------------------------------------------------------------------

async function toolWaitForElement(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string;
  const timeoutMs = Number(args.timeout_ms) || DEFAULT_WAIT_TIMEOUT_MS;
  const maxIterations = Math.ceil(timeoutMs / POLL_INTERVAL_MS);

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)

${(() => {
    // Build the condition once (reused in the loop).
    const conditions: string[] = [];
    if (args.automation_id) {
      const s = sanitizePS(String(args.automation_id));
      conditions.push(`New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, '${s}')`);
    }
    if (args.element_name) {
      const s = sanitizePS(String(args.element_name));
      conditions.push(`New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, '${s}')`);
    }
    if (conditions.length === 0) return `throw "Specify element_name or automation_id."`;
    if (conditions.length === 1) return `$cond = ${conditions[0]}`;
    return `$cond = New-Object System.Windows.Automation.AndCondition(${conditions.join(', ')})`;
  })()}

$found = $false
for ($i = 0; $i -lt ${maxIterations}; $i++) {
    $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
    if ($el -ne $null) {
        $found = $true
        Write-Output "Element found after $($i * ${POLL_INTERVAL_MS}) ms: '$($el.Current.Name)' ($($el.Current.ControlType.ProgrammaticName))"
        break
    }
    Start-Sleep -Milliseconds ${POLL_INTERVAL_MS}
}

if (-not $found) {
    throw "Timeout: element not found within ${timeoutMs} ms."
}
`;
  const out = runPS(script, timeoutMs + 5000); // extra buffer for PS startup
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolMouseMoveClick(args: Record<string, unknown>): Promise<ToolResult> {
  const x = Number(args.x);
  const y = Number(args.y);
  const button = String(args.button || 'left');
  const doubleClick = Boolean(args.double_click);

  let downFlag: string;
  let upFlag: string;
  switch (button) {
    case 'right':
      downFlag = '[Win32]::MOUSEEVENTF_RIGHTDOWN';
      upFlag = '[Win32]::MOUSEEVENTF_RIGHTUP';
      break;
    case 'middle':
      downFlag = '[Win32]::MOUSEEVENTF_MIDDLEDOWN';
      upFlag = '[Win32]::MOUSEEVENTF_MIDDLEUP';
      break;
    default:
      downFlag = '[Win32]::MOUSEEVENTF_LEFTDOWN';
      upFlag = '[Win32]::MOUSEEVENTF_LEFTUP';
  }

  const clickBlock = `
[Win32]::mouse_event(${downFlag}, 0, 0, 0, [IntPtr]::Zero)
[Win32]::mouse_event(${upFlag},   0, 0, 0, [IntPtr]::Zero)
`;

  const script = `
${PS_PREAMBLE_WIN32}
[Win32]::SetCursorPos(${x}, ${y})
Start-Sleep -Milliseconds 50
${clickBlock}
${doubleClick ? `Start-Sleep -Milliseconds 60\n${clickBlock}` : ''}
Write-Output "${doubleClick ? 'Double-clicked' : 'Clicked'} ${button} at (${x}, ${y})."
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolScroll(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string | undefined;
  const direction = String(args.direction || 'down');
  const amount = Number(args.amount) || 3;
  // Each wheel notch = 120 delta units.  Positive = up, negative = down.
  const delta = direction === 'up' ? 120 * amount : -120 * amount;

  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  const focusPart = title
    ? `
${psWindowLocator({ title })}
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 100
`
    : '';

  const script = `
${PS_PREAMBLE_WIN32}
${focusPart}
[Win32]::mouse_event([Win32]::MOUSEEVENTF_WHEEL, 0, 0, ${delta}, [IntPtr]::Zero)
Write-Output "Scrolled ${direction} by ${amount} notches."
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------

async function toolMenuClick(args: Record<string, unknown>): Promise<ToolResult> {
  const title = args.window_title as string;
  const menuPath = args.menu_path as string[];
  if (!Array.isArray(menuPath) || menuPath.length === 0) {
    return { error: 'menu_path must be a non-empty array of menu item names.' };
  }
  if (isBlockedWindow(title)) return { error: 'Refused: target window matches a blocked security dialog.' };

  // Build PowerShell that walks the menu hierarchy.
  const steps = menuPath.map((item, idx) => {
    const safe = sanitizePS(item);
    // The first item is searched from the window root; subsequent items from
    // the previous element.
    const parent = idx === 0 ? '$root' : '$el';
    return `
$cond${idx} = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, '${safe}')
$el = ${parent}.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond${idx})
if ($el -eq $null) { throw "Menu item '${safe}' not found at step ${idx + 1}." }
try {
    $inv = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $inv.Invoke()
} catch {
    # If InvokePattern is not available, try ExpandCollapse for submenus.
    try {
        $ec = $el.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
        $ec.Expand()
    } catch {
        throw "Cannot invoke or expand menu item '${safe}': $_"
    }
}
Start-Sleep -Milliseconds 200
`;
  }).join('\n');

  const script = `
${PS_PREAMBLE_UIA}
${PS_PREAMBLE_WIN32}
${psWindowLocator({ title })}
[void][Win32]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds 150

$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
${steps}
Write-Output "Menu path [${menuPath.join(' > ')}] invoked."
`;
  const out = runPS(script);
  return { result: out };
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

/**
 * Execute a tool by name.
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  let result: ToolResult;
  try {
    switch (toolName) {
      case 'ui_list_windows':
        result = await toolListWindows();
        break;
      case 'ui_focus_window':
        result = await toolFocusWindow(args);
        break;
      case 'ui_inspect_window':
        result = await toolInspectWindow(args);
        break;
      case 'ui_click_element':
        result = await toolClickElement(args);
        break;
      case 'ui_type_text':
        result = await toolTypeText(args);
        break;
      case 'ui_send_keys':
        result = await toolSendKeys(args);
        break;
      case 'ui_get_element_value':
        result = await toolGetElementValue(args);
        break;
      case 'ui_set_element_value':
        result = await toolSetElementValue(args);
        break;
      case 'ui_screenshot_window':
        result = await toolScreenshotWindow(args);
        break;
      case 'ui_wait_for_element':
        result = await toolWaitForElement(args);
        break;
      case 'ui_mouse_move_click':
        result = await toolMouseMoveClick(args);
        break;
      case 'ui_scroll':
        result = await toolScroll(args);
        break;
      case 'ui_menu_click':
        result = await toolMenuClick(args);
        break;
      default:
        result = { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: any) {
    const msg = err?.message ?? String(err);
    result = { error: msg };
  }

  // Audit every call.
  audit(toolName, args, !result.error, result.error);

  return result;
}

// ---------------------------------------------------------------------------
// Detect
// ---------------------------------------------------------------------------

/**
 * Returns true if this connector is available on the current platform.
 * UI Automation is a Windows-only API.
 */
export async function detect(): Promise<boolean> {
  return process.platform === 'win32';
}
