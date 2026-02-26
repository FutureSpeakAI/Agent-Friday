/**
 * powershell.ts — PowerShell/Windows automation connector.
 *
 * Provides an AI agent with deep Windows system control: arbitrary script
 * execution, COM automation, registry access, WMI queries, service management,
 * environment variables, clipboard, and rich system introspection.
 *
 * Safety:
 *  - All commands run with -NoProfile -NonInteractive -ExecutionPolicy Bypass.
 *  - Dangerous patterns (Format-Volume, recursive deletion of system dirs,
 *    shutdown/reboot, critical registry writes) are blocked before execution.
 *  - Default 30-second timeout on every spawn; callers may override per-tool.
 *  - Errors are returned as { error: "..." }, never thrown.
 */

import { execSync, spawn } from 'child_process';
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

/** Base arguments passed to every powershell.exe invocation. */
const PS_BASE_ARGS = [
  '-NoProfile',
  '-NonInteractive',
  '-ExecutionPolicy', 'Bypass',
  '-Command',
];

/** Default execution timeout in milliseconds (30 s). */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Maximum stdout/stderr length returned to the caller (64 KB). */
const MAX_OUTPUT_LENGTH = 64 * 1024;

// ---------------------------------------------------------------------------
// Safety — dangerous command / path blocklists
// ---------------------------------------------------------------------------

/**
 * Regex patterns that should NEVER appear in an arbitrary PowerShell command.
 * Matched case-insensitively against the full command string.
 */
const DANGEROUS_COMMAND_PATTERNS: RegExp[] = [
  // Disk destruction
  /\bFormat-Volume\b/i,
  /\bClear-Disk\b/i,
  /\bInitialize-Disk\b/i,
  /\bRemove-Partition\b/i,

  // Recursive deletion targeting system-critical directories
  /Remove-Item\s+.*(?:C:\\Windows|C:\\Program\s*Files|System32|SysWOW64).*-Recurse/i,
  /rm\s+-r.*(?:C:\\Windows|C:\\Program\s*Files|System32|SysWOW64)/i,
  /del\s+\/s.*(?:C:\\Windows|C:\\Program\s*Files|System32|SysWOW64)/i,

  // Machine shutdown / reboot (block unless the caller's own command text
  // explicitly contains these — the raw execute tool will still block them)
  /\bStop-Computer\b/i,
  /\bRestart-Computer\b/i,
  /\bshutdown\s+\//i,

  // Credential / token theft helpers
  /\bMimikatz\b/i,
  /\bInvoke-Mimikatz\b/i,

  // Disable security features
  /\bSet-MpPreference\b.*\bDisableRealtimeMonitoring\b/i,

  // Boot configuration destruction
  /\bbcdedit\b.*\/delete/i,
];

/**
 * Registry paths that are too dangerous to allow writes to.
 * Reads are generally safe; writes are blocked for these prefixes.
 */
const BLOCKED_REGISTRY_WRITE_PREFIXES: string[] = [
  'HKLM:\\SYSTEM',
  'HKLM:\\SECURITY',
  'HKLM:\\SAM',
  // Autostart abuse prevention
  'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run',
  'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce',
  'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run',
  'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce',
  // Boot configuration
  'HKLM:\\BCD00000000',
];

/**
 * Registry paths that are too dangerous to even read (security hives that
 * require SYSTEM-level access and have no legitimate agent use case).
 */
const BLOCKED_REGISTRY_READ_PREFIXES: string[] = [
  'HKLM:\\SAM',
  'HKLM:\\SECURITY',
];

// ---------------------------------------------------------------------------
// Safety helpers
// ---------------------------------------------------------------------------

/**
 * Returns a human-readable reason if `command` matches a dangerous pattern,
 * or `null` if the command is safe to run.
 */
function checkDangerousCommand(command: string): string | null {
  for (const pattern of DANGEROUS_COMMAND_PATTERNS) {
    if (pattern.test(command)) {
      return `Blocked: command matches dangerous pattern ${pattern.source}`;
    }
  }
  return null;
}

/**
 * Normalise a registry path to a canonical form for prefix comparison.
 * Accepts both "HKLM:\\" and "HKLM:" forms, and back/forward slashes.
 */
function normaliseRegistryPath(p: string): string {
  return p
    .replace(/\//g, '\\')          // forward -> back slash
    .replace(/:\\\\?/g, ':\\')      // ensure single trailing backslash after drive
    .replace(/\\+$/g, '');          // strip trailing slashes
}

function isRegistryWriteBlocked(regPath: string): boolean {
  const norm = normaliseRegistryPath(regPath).toUpperCase();
  return BLOCKED_REGISTRY_WRITE_PREFIXES.some((prefix) =>
    norm.startsWith(normaliseRegistryPath(prefix).toUpperCase())
  );
}

function isRegistryReadBlocked(regPath: string): boolean {
  const norm = normaliseRegistryPath(regPath).toUpperCase();
  return BLOCKED_REGISTRY_READ_PREFIXES.some((prefix) =>
    norm.startsWith(normaliseRegistryPath(prefix).toUpperCase())
  );
}

// ---------------------------------------------------------------------------
// PowerShell runner (spawn-based, with timeout)
// ---------------------------------------------------------------------------

/**
 * Execute a PowerShell command string via `powershell.exe` using `spawn`.
 * Returns combined stdout. Rejects on non-zero exit, timeout, or spawn error.
 */
function runPowerShell(command: string, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const child = spawn('powershell.exe', [...PS_BASE_ARGS, command], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stdout = '';
    let stderr = '';
    let killed = false;

    // Enforce timeout
    const timer = setTimeout(() => {
      killed = true;
      child.kill('SIGKILL');
      reject(new Error(`PowerShell command timed out after ${timeoutMs / 1000}s`));
    }, timeoutMs);

    child.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString();
      // Cap memory usage — stop accumulating if we exceed limit
      if (stdout.length > MAX_OUTPUT_LENGTH) {
        stdout = stdout.slice(0, MAX_OUTPUT_LENGTH);
      }
    });

    child.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString();
      if (stderr.length > MAX_OUTPUT_LENGTH) {
        stderr = stderr.slice(0, MAX_OUTPUT_LENGTH);
      }
    });

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      if (killed) return; // already rejected by timeout handler

      if (code !== 0 && stderr.trim()) {
        reject(new Error(stderr.trim()));
      } else {
        // Some PS commands write non-fatal warnings to stderr; still return stdout
        resolve(stdout.trim());
      }
    });
  });
}

/**
 * Convenience: wrap a call to `runPowerShell` and catch all errors into a
 * ToolResult so callers never need try/catch.
 */
async function safeRun(command: string, timeoutMs?: number): Promise<ToolResult> {
  try {
    const output = await runPowerShell(command, timeoutMs);
    return { result: output || '(no output)' };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { error: msg };
  }
}

// ---------------------------------------------------------------------------
// Sanitisation helpers
// ---------------------------------------------------------------------------

/**
 * Escape a value for safe interpolation inside a PowerShell single-quoted string.
 * In PS single-quoted strings, the only character that needs escaping is the
 * single-quote itself (doubled: '').
 */
function psSingleQuoteEscape(value: string): string {
  return value.replace(/'/g, "''");
}

// ---------------------------------------------------------------------------
// Tool declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // 1. Arbitrary execution
  {
    name: 'powershell_execute',
    description:
      'Execute an arbitrary PowerShell command or script. Returns stdout and stderr. ' +
      'Dangerous operations (Format-Volume, recursive deletion of system directories, ' +
      'shutdown, etc.) are blocked for safety.',
    parameters: {
      type: 'object',
      properties: {
        command: {
          type: 'string',
          description: 'The PowerShell command or script block to execute.',
        },
        timeout_seconds: {
          type: 'number',
          description: 'Maximum execution time in seconds (default 30).',
        },
      },
      required: ['command'],
    },
  },

  // 2. COM object invocation
  {
    name: 'powershell_com_invoke',
    description:
      'Create a COM object by ProgID and invoke a method on it. ' +
      'Example: ProgID "Excel.Application", method "Quit". ' +
      'Useful for automating Office apps, Shell.Application, WScript.Shell, etc.',
    parameters: {
      type: 'object',
      properties: {
        progId: {
          type: 'string',
          description: 'COM ProgID (e.g. "Excel.Application", "Shell.Application").',
        },
        method: {
          type: 'string',
          description: 'Method or property to invoke on the COM object.',
        },
        args: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional positional arguments to pass to the method.',
        },
      },
      required: ['progId', 'method'],
    },
  },

  // 3. Registry read
  {
    name: 'powershell_registry_read',
    description:
      'Read a Windows registry value or enumerate all values at a key. ' +
      'Path format: "HKLM:\\SOFTWARE\\Microsoft\\..." or "HKCU:\\...". ' +
      'If name is omitted, returns all values under the key.',
    parameters: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Full registry key path (e.g. "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion").',
        },
        name: {
          type: 'string',
          description: 'Optional value name. If omitted, all values at the key are returned.',
        },
      },
      required: ['path'],
    },
  },

  // 4. Registry write
  {
    name: 'powershell_registry_write',
    description:
      'Write a value to the Windows registry. Writes to dangerous system paths ' +
      '(HKLM\\SYSTEM, HKLM\\SECURITY, autostart Run keys) are blocked for safety. ' +
      'Default value type is REG_SZ (string).',
    parameters: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Full registry key path.',
        },
        name: {
          type: 'string',
          description: 'Value name to create or update.',
        },
        value: {
          type: 'string',
          description: 'The data to write.',
        },
        type: {
          type: 'string',
          description:
            'Registry value type: String, ExpandString, DWord, QWord, Binary, MultiString. Default: String.',
        },
      },
      required: ['path', 'name', 'value'],
    },
  },

  // 5. WMI / CIM query
  {
    name: 'powershell_wmi_query',
    description:
      'Run a WMI (CIM) query using Get-CimInstance. Returns formatted results. ' +
      'Example query: "SELECT * FROM Win32_Processor".',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'WQL query string (e.g. "SELECT * FROM Win32_OperatingSystem").',
        },
        namespace: {
          type: 'string',
          description: 'WMI namespace (default "root/cimv2").',
        },
      },
      required: ['query'],
    },
  },

  // 6. Service control
  {
    name: 'powershell_service_control',
    description:
      'Manage a Windows service: get status, start, stop, or restart it.',
    parameters: {
      type: 'object',
      properties: {
        name: {
          type: 'string',
          description: 'Service name (e.g. "wuauserv", "Spooler", "sshd").',
        },
        action: {
          type: 'string',
          enum: ['status', 'start', 'stop', 'restart'],
          description: 'Action to perform on the service.',
        },
      },
      required: ['name', 'action'],
    },
  },

  // 7. Installed applications
  {
    name: 'powershell_installed_apps',
    description:
      'List all installed applications on this machine. Returns name, version, ' +
      'publisher, and install date for each application.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },

  // 8. System information
  {
    name: 'powershell_system_info',
    description:
      'Retrieve comprehensive system information: OS version, CPU, RAM, disk space, ' +
      'uptime, and network adapters.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },

  // 9. Environment variables
  {
    name: 'powershell_env_variable',
    description:
      'Get or set an environment variable. If value is provided the variable is set; ' +
      'otherwise its current value is returned.',
    parameters: {
      type: 'object',
      properties: {
        name: {
          type: 'string',
          description: 'Environment variable name (e.g. "PATH", "JAVA_HOME").',
        },
        value: {
          type: 'string',
          description: 'New value to set. Omit to read the current value.',
        },
        scope: {
          type: 'string',
          enum: ['Process', 'User', 'Machine'],
          description: 'Variable scope (default "Process").',
        },
      },
      required: ['name'],
    },
  },

  // 10. Clipboard
  {
    name: 'powershell_clipboard',
    description:
      'Get or set the system clipboard text content.',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['get', 'set'],
          description: '"get" to read clipboard, "set" to write to it.',
        },
        text: {
          type: 'string',
          description: 'Text to place on the clipboard (required when action is "set").',
        },
      },
      required: ['action'],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

/** 1. powershell_execute */
async function executeCommand(args: Record<string, unknown>): Promise<ToolResult> {
  const command = String(args.command ?? '');
  if (!command.trim()) {
    return { error: 'No command provided.' };
  }

  const blocked = checkDangerousCommand(command);
  if (blocked) {
    return { error: blocked };
  }

  const timeoutSec = Number(args.timeout_seconds) || 30;
  const timeoutMs = Math.min(Math.max(timeoutSec, 1), 300) * 1000; // clamp 1-300s

  return safeRun(command, timeoutMs);
}

/** 2. powershell_com_invoke */
async function comInvoke(args: Record<string, unknown>): Promise<ToolResult> {
  const progId = String(args.progId ?? '');
  const method = String(args.method ?? '');
  if (!progId || !method) {
    return { error: 'Both progId and method are required.' };
  }

  const comArgs = Array.isArray(args.args) ? (args.args as string[]) : [];

  // Build the argument list for the PS method call
  const argList = comArgs
    .map((a) => `'${psSingleQuoteEscape(String(a))}'`)
    .join(', ');

  // Construct script: create COM object, invoke method, output result
  const script = [
    `$obj = New-Object -ComObject '${psSingleQuoteEscape(progId)}'`,
    `try {`,
    argList
      ? `  $result = $obj.${method}(${argList})`
      : `  $result = $obj.${method}()`,
    `  if ($null -ne $result) { $result | Out-String -Width 300 }`,
    `  else { Write-Output '(method returned null)' }`,
    `} finally {`,
    `  try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj) | Out-Null } catch {}`,
    `}`,
  ].join('\n');

  return safeRun(script);
}

/** 3. powershell_registry_read */
async function registryRead(args: Record<string, unknown>): Promise<ToolResult> {
  const regPath = String(args.path ?? '');
  if (!regPath) {
    return { error: 'Registry path is required.' };
  }

  if (isRegistryReadBlocked(regPath)) {
    return { error: `Blocked: reading from ${regPath} is not permitted for safety.` };
  }

  const valueName = args.name != null ? String(args.name) : null;

  let script: string;
  if (valueName) {
    script = `Get-ItemProperty -Path '${psSingleQuoteEscape(regPath)}' -Name '${psSingleQuoteEscape(valueName)}' | Select-Object -ExpandProperty '${psSingleQuoteEscape(valueName)}'`;
  } else {
    script = `Get-ItemProperty -Path '${psSingleQuoteEscape(regPath)}' | Format-List | Out-String -Width 300`;
  }

  return safeRun(script);
}

/** 4. powershell_registry_write */
async function registryWrite(args: Record<string, unknown>): Promise<ToolResult> {
  const regPath = String(args.path ?? '');
  const name = String(args.name ?? '');
  const value = String(args.value ?? '');

  if (!regPath || !name) {
    return { error: 'Registry path and name are required.' };
  }

  if (isRegistryWriteBlocked(regPath)) {
    return { error: `Blocked: writing to ${regPath} is not permitted for safety.` };
  }

  // Map user-friendly type names to PowerShell registry types
  const typeMap: Record<string, string> = {
    string:       'String',
    reg_sz:       'String',
    expandstring: 'ExpandString',
    reg_expand_sz: 'ExpandString',
    dword:        'DWord',
    reg_dword:    'DWord',
    qword:        'QWord',
    reg_qword:    'QWord',
    binary:       'Binary',
    reg_binary:   'Binary',
    multistring:  'MultiString',
    reg_multi_sz: 'MultiString',
  };

  const rawType = String(args.type ?? 'String').toLowerCase().replace(/[\s_-]/g, '');
  const psType = typeMap[rawType] || 'String';

  // Ensure the key exists before writing the value
  const script = [
    `if (-not (Test-Path '${psSingleQuoteEscape(regPath)}')) {`,
    `  New-Item -Path '${psSingleQuoteEscape(regPath)}' -Force | Out-Null`,
    `}`,
    `Set-ItemProperty -Path '${psSingleQuoteEscape(regPath)}' -Name '${psSingleQuoteEscape(name)}' -Value '${psSingleQuoteEscape(value)}' -Type ${psType}`,
    `Write-Output "Wrote ${psType} value ''${psSingleQuoteEscape(name)}'' to ${regPath}"`,
  ].join('\n');

  return safeRun(script);
}

/** 5. powershell_wmi_query */
async function wmiQuery(args: Record<string, unknown>): Promise<ToolResult> {
  const query = String(args.query ?? '');
  if (!query) {
    return { error: 'WMI query is required.' };
  }

  const namespace = String(args.namespace ?? 'root/cimv2');

  const script =
    `Get-CimInstance -Query '${psSingleQuoteEscape(query)}' -Namespace '${psSingleQuoteEscape(namespace)}' ` +
    `| Format-List | Out-String -Width 300`;

  return safeRun(script);
}

/** 6. powershell_service_control */
async function serviceControl(args: Record<string, unknown>): Promise<ToolResult> {
  const serviceName = String(args.name ?? '');
  const action = String(args.action ?? '').toLowerCase();

  if (!serviceName) {
    return { error: 'Service name is required.' };
  }

  const validActions = ['status', 'start', 'stop', 'restart'];
  if (!validActions.includes(action)) {
    return { error: `Invalid action "${action}". Must be one of: ${validActions.join(', ')}` };
  }

  const safeName = psSingleQuoteEscape(serviceName);

  let script: string;
  switch (action) {
    case 'status':
      script = `Get-Service -Name '${safeName}' | Format-List Name, DisplayName, Status, StartType | Out-String -Width 300`;
      break;
    case 'start':
      script = `Start-Service -Name '${safeName}'; Get-Service -Name '${safeName}' | Select-Object -ExpandProperty Status`;
      break;
    case 'stop':
      script = `Stop-Service -Name '${safeName}' -Force; Get-Service -Name '${safeName}' | Select-Object -ExpandProperty Status`;
      break;
    case 'restart':
      script = `Restart-Service -Name '${safeName}' -Force; Get-Service -Name '${safeName}' | Select-Object -ExpandProperty Status`;
      break;
    default:
      return { error: `Unknown action: ${action}` };
  }

  return safeRun(script);
}

/** 7. powershell_installed_apps */
async function installedApps(): Promise<ToolResult> {
  const script = `
$paths = @(
  'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
  'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
  'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
)
$apps = $paths | ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } |
  Where-Object { $_.DisplayName } |
  Select-Object DisplayName, DisplayVersion, Publisher, InstallDate |
  Sort-Object DisplayName -Unique
$apps | Format-Table -AutoSize | Out-String -Width 300
`.trim();

  return safeRun(script);
}

/** 8. powershell_system_info */
async function systemInfo(): Promise<ToolResult> {
  const script = `
$os   = Get-CimInstance Win32_OperatingSystem
$cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
$mem  = $os
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
$net  = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True"
$boot = $os.LastBootUpTime
$up   = (Get-Date) - $boot

$info = @"
=== Operating System ===
  Caption : $($os.Caption)
  Version : $($os.Version)
  Build   : $($os.BuildNumber)
  Arch    : $($os.OSArchitecture)

=== CPU ===
  Name    : $($cpu.Name)
  Cores   : $($cpu.NumberOfCores)
  Logical : $($cpu.NumberOfLogicalProcessors)
  Speed   : $($cpu.MaxClockSpeed) MHz

=== Memory ===
  Total   : $([math]::Round($mem.TotalVisibleMemorySize / 1MB, 2)) GB
  Free    : $([math]::Round($mem.FreePhysicalMemory / 1MB, 2)) GB

=== Disk ===
$($disk | ForEach-Object {
  "  $($_.DeviceID) $([math]::Round($_.Size / 1GB, 1)) GB total, $([math]::Round($_.FreeSpace / 1GB, 1)) GB free"
} | Out-String)

=== Uptime ===
  $($up.Days)d $($up.Hours)h $($up.Minutes)m (since $boot)

=== Network Adapters ===
$($net | ForEach-Object {
  "  $($_.Description): $($_.IPAddress -join ', ')"
} | Out-String)
"@

Write-Output $info
`.trim();

  return safeRun(script);
}

/** 9. powershell_env_variable */
async function envVariable(args: Record<string, unknown>): Promise<ToolResult> {
  const name = String(args.name ?? '');
  if (!name) {
    return { error: 'Variable name is required.' };
  }

  const value = args.value != null ? String(args.value) : null;
  const scope = String(args.scope ?? 'Process');

  const validScopes = ['Process', 'User', 'Machine'];
  if (!validScopes.includes(scope)) {
    return { error: `Invalid scope "${scope}". Must be one of: ${validScopes.join(', ')}` };
  }

  const safeName = psSingleQuoteEscape(name);

  if (value !== null) {
    // SET the variable
    const safeValue = psSingleQuoteEscape(value);
    const script = `[Environment]::SetEnvironmentVariable('${safeName}', '${safeValue}', '${scope}'); Write-Output "Set ${'${'}scope} variable ''${safeName}'' = ''${safeValue}''"`;
    return safeRun(script);
  } else {
    // GET the variable
    const script = `$v = [Environment]::GetEnvironmentVariable('${safeName}', '${scope}'); if ($null -ne $v) { Write-Output $v } else { Write-Output "(not set)" }`;
    return safeRun(script);
  }
}

/** 10. powershell_clipboard */
async function clipboardAction(args: Record<string, unknown>): Promise<ToolResult> {
  const action = String(args.action ?? '').toLowerCase();

  if (action === 'get') {
    return safeRun('Get-Clipboard -Raw');
  } else if (action === 'set') {
    const text = String(args.text ?? '');
    if (!text) {
      return { error: 'Text is required when action is "set".' };
    }
    const script = `Set-Clipboard -Value '${psSingleQuoteEscape(text)}'; Write-Output 'Clipboard updated.'`;
    return safeRun(script);
  } else {
    return { error: `Invalid action "${action}". Must be "get" or "set".` };
  }
}

// ---------------------------------------------------------------------------
// Execute router
// ---------------------------------------------------------------------------

/**
 * Route a tool call to the correct implementation.
 * Never throws — always returns a ToolResult.
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    switch (toolName) {
      case 'powershell_execute':
        return await executeCommand(args);
      case 'powershell_com_invoke':
        return await comInvoke(args);
      case 'powershell_registry_read':
        return await registryRead(args);
      case 'powershell_registry_write':
        return await registryWrite(args);
      case 'powershell_wmi_query':
        return await wmiQuery(args);
      case 'powershell_service_control':
        return await serviceControl(args);
      case 'powershell_installed_apps':
        return await installedApps();
      case 'powershell_system_info':
        return await systemInfo();
      case 'powershell_env_variable':
        return await envVariable(args);
      case 'powershell_clipboard':
        return await clipboardAction(args);
      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    // Absolute last-resort safety net — should never be reached because
    // individual handlers already catch, but just in case.
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Unexpected error in ${toolName}: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/**
 * Check whether PowerShell is available on this system.
 * Returns true if `powershell.exe` can be invoked successfully.
 */
export async function detect(): Promise<boolean> {
  try {
    execSync('powershell.exe -NoProfile -NonInteractive -Command "echo ok"', {
      timeout: 5000,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return true;
  } catch {
    return false;
  }
}
