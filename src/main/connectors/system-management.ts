/**
 * system-management.ts — Windows system administration connector.
 *
 * Provides an AI agent with comprehensive Windows system management:
 * services, scheduled tasks, network configuration, firewall rules,
 * package managers (winget/choco/scoop), disk usage, performance
 * monitoring, and event log access.
 *
 * Safety:
 *  - All commands run via powershell.exe -NoProfile -Command "...".
 *  - Elevated-permission failures are reported gracefully with
 *    guidance rather than cryptic error messages.
 *  - Default 30-second timeout on every spawn; longer for installs.
 *  - Errors are returned as { error: "..." }, never thrown.
 *
 * Exports:
 *   TOOLS    — Array of tool declarations for the agent tool registry
 *   execute  — Async handler that dispatches tool calls by name
 *   detect   — Async check: always true on Windows
 */

import { execFile, execSync } from 'child_process';
import { promisify } from 'util';
import * as os from 'os';

const execFileAsync = promisify(execFile);

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

/** Maximum characters returned in any single tool result. */
const MAX_OUTPUT_CHARS = 12_000;

/** Default timeout for PowerShell commands (30 s). */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Extended timeout for package installs (120 s). */
const INSTALL_TIMEOUT_MS = 120_000;

/** Hint appended when a command fails with an access-denied pattern. */
const ELEVATION_HINT =
  'This operation may require elevated (Administrator) privileges. ' +
  'Try running NEXUS OS as Administrator, or use an elevated terminal.';

// ---------------------------------------------------------------------------
// PowerShell runner
// ---------------------------------------------------------------------------

/**
 * Execute a PowerShell command string via `powershell.exe`.
 * Returns stdout trimmed. Throws on non-zero exit or timeout.
 */
async function ps(
  command: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<string> {
  const { stdout, stderr } = await execFileAsync(
    'powershell.exe',
    ['-NoProfile', '-Command', command],
    {
      timeout: timeoutMs,
      maxBuffer: 10 * 1024 * 1024, // 10 MB
      windowsHide: true,
    }
  );
  // Some commands write non-fatal warnings to stderr; we only care if
  // stdout is empty AND stderr has content (handled by caller).
  return (stdout ?? '').trim();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Truncate long output and append a notice if clipped. */
function truncate(text: string, limit: number = MAX_OUTPUT_CHARS): string {
  if (text.length <= limit) return text;
  return (
    text.slice(0, limit) +
    `\n\n--- Output truncated (${text.length} chars total, showing first ${limit}) ---`
  );
}

/** Wrap a successful result with truncation. */
function ok(text: string): ToolResult {
  return { result: truncate(text.trim()) || '(no output)' };
}

/** Wrap an error result; detect elevation issues and append hint. */
function fail(msg: string): ToolResult {
  const lower = msg.toLowerCase();
  if (
    lower.includes('access is denied') ||
    lower.includes('access denied') ||
    lower.includes('requires elevation') ||
    lower.includes('run it with elevated privileges') ||
    lower.includes('not have permission') ||
    lower.includes('unauthorized')
  ) {
    return { error: `${msg}\n\n${ELEVATION_HINT}` };
  }
  return { error: msg };
}

/**
 * Run a PowerShell command and return a ToolResult.
 * Never throws — errors are always captured into the result.
 */
async function safeRun(command: string, timeoutMs?: number): Promise<ToolResult> {
  try {
    const output = await ps(command, timeoutMs);
    return ok(output);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return fail(msg);
  }
}

/**
 * Escape a value for safe interpolation inside a PowerShell single-quoted string.
 * The only character needing escaping is the single-quote itself (doubled).
 */
function psEsc(value: string): string {
  return value.replace(/'/g, "''");
}

// ---------------------------------------------------------------------------
// Tool Declarations
// ---------------------------------------------------------------------------

export const TOOLS: ToolDeclaration[] = [
  // ── Services ─────────────────────────────────────────────────────────────
  {
    name: 'sys_services_list',
    description:
      'List Windows services. Optionally filter by display name substring or status (Running, Stopped, etc.).',
    parameters: {
      type: 'object',
      properties: {
        filter: {
          type: 'string',
          description:
            'Optional substring to filter service display names (case-insensitive).',
        },
        status: {
          type: 'string',
          enum: ['Running', 'Stopped', 'Paused', 'StartPending', 'StopPending'],
          description: 'Optional: only show services with this status.',
        },
      },
      required: [],
    },
  },
  {
    name: 'sys_service_control',
    description:
      'Start, stop, or restart a Windows service by name. May require elevated privileges.',
    parameters: {
      type: 'object',
      properties: {
        service_name: {
          type: 'string',
          description: 'The service name (e.g. "wuauserv", "Spooler", "sshd").',
        },
        action: {
          type: 'string',
          enum: ['start', 'stop', 'restart'],
          description: 'Action to perform on the service.',
        },
      },
      required: ['service_name', 'action'],
    },
  },
  {
    name: 'sys_service_info',
    description:
      'Get detailed information about a specific Windows service including status, start type, dependencies, and executable path.',
    parameters: {
      type: 'object',
      properties: {
        service_name: {
          type: 'string',
          description: 'The service name to inspect.',
        },
      },
      required: ['service_name'],
    },
  },

  // ── Scheduled Tasks ──────────────────────────────────────────────────────
  {
    name: 'sys_scheduled_task_create',
    description:
      'Create a new Windows scheduled task. Specify the program/script to run, trigger time, and optional repetition interval.',
    parameters: {
      type: 'object',
      properties: {
        task_name: {
          type: 'string',
          description: 'Name for the scheduled task (e.g. "DailyBackup").',
        },
        program: {
          type: 'string',
          description: 'Full path to the program or script to execute.',
        },
        arguments: {
          type: 'string',
          description: 'Optional command-line arguments for the program.',
        },
        trigger_type: {
          type: 'string',
          enum: ['once', 'daily', 'weekly', 'logon', 'startup'],
          description: 'When the task should trigger.',
        },
        trigger_time: {
          type: 'string',
          description:
            'Start time in ISO 8601 or "HH:mm" format (e.g. "09:00", "2025-03-01T14:30:00"). Required for once/daily/weekly triggers.',
        },
        repeat_interval: {
          type: 'string',
          description:
            'Optional repetition interval as a TimeSpan string (e.g. "PT1H" for hourly, "PT30M" for 30 minutes).',
        },
        description: {
          type: 'string',
          description: 'Optional description for the task.',
        },
      },
      required: ['task_name', 'program', 'trigger_type'],
    },
  },
  {
    name: 'sys_scheduled_task_list',
    description:
      'List Windows scheduled tasks. Optionally filter by task path prefix (e.g. "\\\\MyTasks\\\\").',
    parameters: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description:
            'Task folder path to list (e.g. "\\\\" for root, "\\\\MyFolder\\\\"). Default: all tasks.',
        },
      },
      required: [],
    },
  },
  {
    name: 'sys_scheduled_task_delete',
    description:
      'Delete a Windows scheduled task by name. Requires confirmation — this is irreversible.',
    parameters: {
      type: 'object',
      properties: {
        task_name: {
          type: 'string',
          description: 'Full task name or path (e.g. "DailyBackup" or "\\\\MyFolder\\\\DailyBackup").',
        },
      },
      required: ['task_name'],
    },
  },
  {
    name: 'sys_scheduled_task_run',
    description:
      'Immediately run a Windows scheduled task (does not wait for its trigger).',
    parameters: {
      type: 'object',
      properties: {
        task_name: {
          type: 'string',
          description: 'Full task name or path to run.',
        },
      },
      required: ['task_name'],
    },
  },

  // ── Network ──────────────────────────────────────────────────────────────
  {
    name: 'sys_network_info',
    description:
      'Get network interface information including adapter names, IP addresses, MAC addresses, and link speed.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },
  {
    name: 'sys_network_connections',
    description:
      'List active network connections (similar to netstat). Optionally filter by state or process name.',
    parameters: {
      type: 'object',
      properties: {
        state: {
          type: 'string',
          enum: ['Established', 'Listen', 'TimeWait', 'CloseWait', 'SynSent'],
          description: 'Optional: filter connections by TCP state.',
        },
        process_name: {
          type: 'string',
          description: 'Optional: filter connections by owning process name.',
        },
      },
      required: [],
    },
  },

  // ── Firewall ─────────────────────────────────────────────────────────────
  {
    name: 'sys_firewall_rules',
    description:
      'List Windows Firewall rules. Optionally filter by display name substring, direction, or action.',
    parameters: {
      type: 'object',
      properties: {
        filter: {
          type: 'string',
          description: 'Optional substring to filter rule display names.',
        },
        direction: {
          type: 'string',
          enum: ['Inbound', 'Outbound'],
          description: 'Optional: only show rules for this direction.',
        },
        action: {
          type: 'string',
          enum: ['Allow', 'Block'],
          description: 'Optional: only show rules with this action.',
        },
        enabled: {
          type: 'boolean',
          description: 'Optional: filter by enabled state (true/false).',
        },
      },
      required: [],
    },
  },
  {
    name: 'sys_firewall_add_rule',
    description:
      'Add a new Windows Firewall rule. Requires elevated privileges.',
    parameters: {
      type: 'object',
      properties: {
        name: {
          type: 'string',
          description: 'Display name for the new firewall rule.',
        },
        direction: {
          type: 'string',
          enum: ['Inbound', 'Outbound'],
          description: 'Rule direction.',
        },
        action: {
          type: 'string',
          enum: ['Allow', 'Block'],
          description: 'Whether to allow or block matching traffic.',
        },
        protocol: {
          type: 'string',
          enum: ['TCP', 'UDP', 'ICMPv4', 'ICMPv6', 'Any'],
          description: 'Network protocol (default: TCP).',
        },
        local_port: {
          type: 'string',
          description:
            'Local port or range (e.g. "8080", "3000-3100", "Any"). Required for TCP/UDP.',
        },
        remote_address: {
          type: 'string',
          description:
            'Remote IP address or range (e.g. "192.168.1.0/24", "Any"). Default: Any.',
        },
        program: {
          type: 'string',
          description: 'Optional: full path to the program this rule applies to.',
        },
        description: {
          type: 'string',
          description: 'Optional description for the rule.',
        },
      },
      required: ['name', 'direction', 'action'],
    },
  },

  // ── Package Managers ─────────────────────────────────────────────────────
  {
    name: 'sys_package_install',
    description:
      'Install a package using winget, chocolatey (choco), or scoop. Defaults to winget if available.',
    parameters: {
      type: 'object',
      properties: {
        package_id: {
          type: 'string',
          description: 'Package identifier (e.g. "Microsoft.VisualStudioCode", "git", "nodejs").',
        },
        manager: {
          type: 'string',
          enum: ['winget', 'choco', 'scoop'],
          description: 'Package manager to use (default: winget).',
        },
        version: {
          type: 'string',
          description: 'Optional: specific version to install.',
        },
      },
      required: ['package_id'],
    },
  },
  {
    name: 'sys_package_search',
    description:
      'Search for packages using winget, chocolatey, or scoop.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Search query string.',
        },
        manager: {
          type: 'string',
          enum: ['winget', 'choco', 'scoop'],
          description: 'Package manager to search with (default: winget).',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'sys_package_list',
    description:
      'List installed packages from winget, chocolatey, or scoop.',
    parameters: {
      type: 'object',
      properties: {
        manager: {
          type: 'string',
          enum: ['winget', 'choco', 'scoop'],
          description: 'Package manager to query (default: winget).',
        },
      },
      required: [],
    },
  },

  // ── Disk & Performance ───────────────────────────────────────────────────
  {
    name: 'sys_disk_usage',
    description:
      'Get disk usage for all fixed drives (or a specific drive letter), including total size, used space, free space, and percentage used.',
    parameters: {
      type: 'object',
      properties: {
        drive: {
          type: 'string',
          description: 'Optional drive letter (e.g. "C", "D"). If omitted, shows all fixed drives.',
        },
      },
      required: [],
    },
  },
  {
    name: 'sys_performance_info',
    description:
      'Get current system performance statistics: CPU usage, memory utilization, top processes by CPU and memory, and disk I/O.',
    parameters: {
      type: 'object',
      properties: {},
      required: [],
    },
  },

  // ── Event Log ────────────────────────────────────────────────────────────
  {
    name: 'sys_event_log',
    description:
      'Read entries from a Windows event log. Returns the most recent entries with optional filtering by level.',
    parameters: {
      type: 'object',
      properties: {
        log_name: {
          type: 'string',
          description:
            'Event log name (e.g. "Application", "System", "Security"). Default: "System".',
        },
        count: {
          type: 'number',
          description: 'Number of recent entries to return (default: 20, max: 100).',
        },
        level: {
          type: 'string',
          enum: ['Error', 'Warning', 'Information', 'Critical'],
          description: 'Optional: filter entries by severity level.',
        },
      },
      required: [],
    },
  },
];

// ---------------------------------------------------------------------------
// Tool Implementations
// ---------------------------------------------------------------------------

// ── Services ─────────────────────────────────────────────────────────────

async function servicesList(args: Record<string, unknown>): Promise<ToolResult> {
  const filter = args.filter as string | undefined;
  const status = args.status as string | undefined;

  let script = 'Get-Service';

  const conditions: string[] = [];
  if (filter) {
    conditions.push(`$_.DisplayName -like '*${psEsc(filter)}*'`);
  }
  if (status) {
    conditions.push(`$_.Status -eq '${psEsc(status)}'`);
  }

  if (conditions.length > 0) {
    script += ` | Where-Object { ${conditions.join(' -and ')} }`;
  }

  script += " | Sort-Object DisplayName | Format-Table -AutoSize Name, DisplayName, Status, StartType | Out-String -Width 300";

  return safeRun(script);
}

async function serviceControl(args: Record<string, unknown>): Promise<ToolResult> {
  const name = args.service_name as string;
  const action = args.action as 'start' | 'stop' | 'restart';

  if (!name) return fail('service_name is required.');

  const safeName = psEsc(name);

  let script: string;
  switch (action) {
    case 'start':
      script = `Start-Service -Name '${safeName}' -ErrorAction Stop; Get-Service -Name '${safeName}' | Format-List Name, DisplayName, Status | Out-String -Width 300`;
      break;
    case 'stop':
      script = `Stop-Service -Name '${safeName}' -Force -ErrorAction Stop; Get-Service -Name '${safeName}' | Format-List Name, DisplayName, Status | Out-String -Width 300`;
      break;
    case 'restart':
      script = `Restart-Service -Name '${safeName}' -Force -ErrorAction Stop; Get-Service -Name '${safeName}' | Format-List Name, DisplayName, Status | Out-String -Width 300`;
      break;
    default:
      return fail(`Unknown action: ${action}. Must be start, stop, or restart.`);
  }

  return safeRun(script);
}

async function serviceInfo(args: Record<string, unknown>): Promise<ToolResult> {
  const name = args.service_name as string;
  if (!name) return fail('service_name is required.');

  const safeName = psEsc(name);

  const script = `
$svc = Get-Service -Name '${safeName}' -ErrorAction Stop
$wmi = Get-CimInstance Win32_Service -Filter "Name='${safeName}'" -ErrorAction SilentlyContinue

$deps = ($svc.DependentServices | ForEach-Object { $_.Name }) -join ', '
$reqs = ($svc.ServicesDependedOn | ForEach-Object { $_.Name }) -join ', '

[PSCustomObject]@{
  Name        = $svc.Name
  DisplayName = $svc.DisplayName
  Status      = $svc.Status
  StartType   = $svc.StartType
  CanStop     = $svc.CanStop
  CanPause    = $svc.CanPauseAndContinue
  PathName    = if ($wmi) { $wmi.PathName } else { 'N/A' }
  StartName   = if ($wmi) { $wmi.StartName } else { 'N/A' }
  Description = if ($wmi) { $wmi.Description } else { 'N/A' }
  PID         = if ($wmi) { $wmi.ProcessId } else { 'N/A' }
  DependentServices = if ($deps) { $deps } else { '(none)' }
  DependsOn         = if ($reqs) { $reqs } else { '(none)' }
} | Format-List | Out-String -Width 300
  `.trim();

  return safeRun(script);
}

// ── Scheduled Tasks ────────────────────────────────────────────────────

async function scheduledTaskCreate(args: Record<string, unknown>): Promise<ToolResult> {
  const taskName = args.task_name as string;
  const program = args.program as string;
  const taskArgs = args.arguments as string | undefined;
  const triggerType = args.trigger_type as string;
  const triggerTime = args.trigger_time as string | undefined;
  const repeatInterval = args.repeat_interval as string | undefined;
  const description = args.description as string | undefined;

  if (!taskName || !program || !triggerType) {
    return fail('task_name, program, and trigger_type are required.');
  }

  const lines: string[] = [];

  // Build action
  if (taskArgs) {
    lines.push(`$action = New-ScheduledTaskAction -Execute '${psEsc(program)}' -Argument '${psEsc(taskArgs)}'`);
  } else {
    lines.push(`$action = New-ScheduledTaskAction -Execute '${psEsc(program)}'`);
  }

  // Build trigger
  switch (triggerType) {
    case 'once':
      if (!triggerTime) return fail('trigger_time is required for "once" trigger.');
      lines.push(`$trigger = New-ScheduledTaskTrigger -Once -At '${psEsc(triggerTime)}'`);
      break;
    case 'daily':
      if (!triggerTime) return fail('trigger_time is required for "daily" trigger.');
      lines.push(`$trigger = New-ScheduledTaskTrigger -Daily -At '${psEsc(triggerTime)}'`);
      break;
    case 'weekly':
      if (!triggerTime) return fail('trigger_time is required for "weekly" trigger.');
      lines.push(`$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '${psEsc(triggerTime)}'`);
      break;
    case 'logon':
      lines.push('$trigger = New-ScheduledTaskTrigger -AtLogOn');
      break;
    case 'startup':
      lines.push('$trigger = New-ScheduledTaskTrigger -AtStartup');
      break;
    default:
      return fail(`Unknown trigger_type: ${triggerType}`);
  }

  // Optional repetition
  if (repeatInterval) {
    lines.push(`$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Hours 0 -Minutes 0)).Repetition`);
    lines.push(`$trigger.Repetition.Interval = '${psEsc(repeatInterval)}'`);
  }

  // Register task
  let registerCmd = `Register-ScheduledTask -TaskName '${psEsc(taskName)}' -Action $action -Trigger $trigger -Force`;
  if (description) {
    registerCmd += ` -Description '${psEsc(description)}'`;
  }
  lines.push(registerCmd);
  lines.push(`Write-Output "Scheduled task '${psEsc(taskName)}' created successfully."`);

  return safeRun(lines.join('\n'));
}

async function scheduledTaskList(args: Record<string, unknown>): Promise<ToolResult> {
  const taskPath = args.path as string | undefined;

  let script: string;
  if (taskPath) {
    script = `Get-ScheduledTask -TaskPath '${psEsc(taskPath)}' -ErrorAction SilentlyContinue`;
  } else {
    script = 'Get-ScheduledTask -ErrorAction SilentlyContinue';
  }

  script += " | Select-Object TaskName, TaskPath, State, @{N='NextRun';E={(Get-ScheduledTaskInfo -TaskName $_.TaskName -ErrorAction SilentlyContinue).NextRunTime}} | Format-Table -AutoSize | Out-String -Width 300";

  return safeRun(script);
}

async function scheduledTaskDelete(args: Record<string, unknown>): Promise<ToolResult> {
  const taskName = args.task_name as string;
  if (!taskName) return fail('task_name is required.');

  const script = `Unregister-ScheduledTask -TaskName '${psEsc(taskName)}' -Confirm:$false -ErrorAction Stop; Write-Output "Scheduled task '${psEsc(taskName)}' deleted."`;

  return safeRun(script);
}

async function scheduledTaskRun(args: Record<string, unknown>): Promise<ToolResult> {
  const taskName = args.task_name as string;
  if (!taskName) return fail('task_name is required.');

  const script = `Start-ScheduledTask -TaskName '${psEsc(taskName)}' -ErrorAction Stop; Write-Output "Scheduled task '${psEsc(taskName)}' started."`;

  return safeRun(script);
}

// ── Network ──────────────────────────────────────────────────────────────

async function networkInfo(): Promise<ToolResult> {
  const script = `
$adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' }
if (-not $adapters) {
  $adapters = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' }
}

$results = foreach ($adapter in $adapters) {
  $ipConfig = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue |
    Where-Object { $_.AddressFamily -eq 'IPv4' }
  $gateway = Get-NetRoute -InterfaceIndex $adapter.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty NextHop
  $dns = (Get-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ', '

  [PSCustomObject]@{
    Adapter    = $adapter.Name
    Status     = $adapter.Status
    LinkSpeed  = $adapter.LinkSpeed
    MacAddress = $adapter.MacAddress
    IPv4       = ($ipConfig.IPAddress -join ', ')
    Subnet     = ($ipConfig.PrefixLength | ForEach-Object { "/$_" }) -join ', '
    Gateway    = if ($gateway) { $gateway } else { 'N/A' }
    DNS        = if ($dns) { $dns } else { 'N/A' }
  }
}

$results | Format-List | Out-String -Width 300
  `.trim();

  return safeRun(script);
}

async function networkConnections(args: Record<string, unknown>): Promise<ToolResult> {
  const state = args.state as string | undefined;
  const processName = args.process_name as string | undefined;

  const lines: string[] = [
    '$connections = Get-NetTCPConnection -ErrorAction SilentlyContinue',
  ];

  const filters: string[] = [];
  if (state) {
    filters.push(`$_.State -eq '${psEsc(state)}'`);
  }
  if (processName) {
    lines.push(
      `$targetPids = (Get-Process -Name '${psEsc(processName)}' -ErrorAction SilentlyContinue).Id`
    );
    filters.push('$targetPids -contains $_.OwningProcess');
  }

  if (filters.length > 0) {
    lines.push(`$connections = $connections | Where-Object { ${filters.join(' -and ')} }`);
  }

  lines.push(`
$connections | Select-Object @{N='LocalAddress';E={$_.LocalAddress}},
  @{N='LocalPort';E={$_.LocalPort}},
  @{N='RemoteAddress';E={$_.RemoteAddress}},
  @{N='RemotePort';E={$_.RemotePort}},
  @{N='State';E={$_.State}},
  @{N='Process';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}},
  @{N='PID';E={$_.OwningProcess}} |
  Format-Table -AutoSize | Out-String -Width 300
  `.trim());

  return safeRun(lines.join('\n'));
}

// ── Firewall ─────────────────────────────────────────────────────────────

async function firewallRules(args: Record<string, unknown>): Promise<ToolResult> {
  const filter = args.filter as string | undefined;
  const direction = args.direction as string | undefined;
  const action = args.action as string | undefined;
  const enabled = args.enabled as boolean | undefined;

  let script = 'Get-NetFirewallRule -ErrorAction SilentlyContinue';

  const conditions: string[] = [];
  if (filter) {
    conditions.push(`$_.DisplayName -like '*${psEsc(filter)}*'`);
  }
  if (direction) {
    conditions.push(`$_.Direction -eq '${psEsc(direction)}'`);
  }
  if (action) {
    conditions.push(`$_.Action -eq '${psEsc(action)}'`);
  }
  if (enabled !== undefined) {
    conditions.push(`$_.Enabled -eq '${enabled ? 'True' : 'False'}'`);
  }

  if (conditions.length > 0) {
    script += ` | Where-Object { ${conditions.join(' -and ')} }`;
  }

  script += " | Select-Object -First 50 DisplayName, Direction, Action, Enabled, Profile | Format-Table -AutoSize | Out-String -Width 300";

  return safeRun(script);
}

async function firewallAddRule(args: Record<string, unknown>): Promise<ToolResult> {
  const name = args.name as string;
  const direction = args.direction as string;
  const action = args.action as string;
  const protocol = (args.protocol as string) || 'TCP';
  const localPort = args.local_port as string | undefined;
  const remoteAddress = args.remote_address as string | undefined;
  const program = args.program as string | undefined;
  const description = args.description as string | undefined;

  if (!name || !direction || !action) {
    return fail('name, direction, and action are required.');
  }

  const parts: string[] = [
    `New-NetFirewallRule -DisplayName '${psEsc(name)}'`,
    `-Direction ${direction}`,
    `-Action ${action}`,
    `-Protocol ${protocol}`,
  ];

  if (localPort && (protocol === 'TCP' || protocol === 'UDP')) {
    parts.push(`-LocalPort ${psEsc(localPort)}`);
  }
  if (remoteAddress) {
    parts.push(`-RemoteAddress '${psEsc(remoteAddress)}'`);
  }
  if (program) {
    parts.push(`-Program '${psEsc(program)}'`);
  }
  if (description) {
    parts.push(`-Description '${psEsc(description)}'`);
  }

  parts.push('-ErrorAction Stop');

  const script = parts.join(' ') + `; Write-Output "Firewall rule '${psEsc(name)}' created."`;

  return safeRun(script);
}

// ── Package Managers ─────────────────────────────────────────────────────

async function packageInstall(args: Record<string, unknown>): Promise<ToolResult> {
  const packageId = args.package_id as string;
  const manager = (args.manager as string) || 'winget';
  const version = args.version as string | undefined;

  if (!packageId) return fail('package_id is required.');

  let script: string;

  switch (manager) {
    case 'winget':
      script = `winget install --id '${psEsc(packageId)}' --accept-package-agreements --accept-source-agreements`;
      if (version) script += ` --version '${psEsc(version)}'`;
      break;
    case 'choco':
      script = `choco install ${psEsc(packageId)} -y --no-progress`;
      if (version) script += ` --version ${psEsc(version)}`;
      break;
    case 'scoop':
      script = `scoop install ${psEsc(packageId)}`;
      // scoop does not support arbitrary version installs inline
      break;
    default:
      return fail(`Unknown package manager: ${manager}. Must be winget, choco, or scoop.`);
  }

  return safeRun(script, INSTALL_TIMEOUT_MS);
}

async function packageSearch(args: Record<string, unknown>): Promise<ToolResult> {
  const query = args.query as string;
  const manager = (args.manager as string) || 'winget';

  if (!query) return fail('query is required.');

  let script: string;

  switch (manager) {
    case 'winget':
      script = `winget search '${psEsc(query)}' --accept-source-agreements`;
      break;
    case 'choco':
      script = `choco search ${psEsc(query)} --limit-output`;
      break;
    case 'scoop':
      script = `scoop search ${psEsc(query)}`;
      break;
    default:
      return fail(`Unknown package manager: ${manager}. Must be winget, choco, or scoop.`);
  }

  return safeRun(script);
}

async function packageList(args: Record<string, unknown>): Promise<ToolResult> {
  const manager = (args.manager as string) || 'winget';

  let script: string;

  switch (manager) {
    case 'winget':
      script = 'winget list --accept-source-agreements';
      break;
    case 'choco':
      script = 'choco list --local-only --limit-output';
      break;
    case 'scoop':
      script = 'scoop list';
      break;
    default:
      return fail(`Unknown package manager: ${manager}. Must be winget, choco, or scoop.`);
  }

  return safeRun(script);
}

// ── Disk & Performance ───────────────────────────────────────────────────

async function diskUsage(args: Record<string, unknown>): Promise<ToolResult> {
  const drive = args.drive as string | undefined;

  let filter = "DriveType=3";
  if (drive) {
    const letter = drive.replace(':', '').toUpperCase();
    filter += ` AND DeviceID='${letter}:'`;
  }

  const script = `
$disks = Get-CimInstance Win32_LogicalDisk -Filter "${filter}"
foreach ($d in $disks) {
  $totalGB  = [math]::Round($d.Size / 1GB, 2)
  $freeGB   = [math]::Round($d.FreeSpace / 1GB, 2)
  $usedGB   = [math]::Round(($d.Size - $d.FreeSpace) / 1GB, 2)
  $pctUsed  = if ($d.Size -gt 0) { [math]::Round(($d.Size - $d.FreeSpace) / $d.Size * 100, 1) } else { 0 }

  [PSCustomObject]@{
    Drive      = $d.DeviceID
    VolumeName = $d.VolumeName
    FileSystem = $d.FileSystem
    'Total(GB)' = $totalGB
    'Used(GB)'  = $usedGB
    'Free(GB)'  = $freeGB
    'Used%'     = "$pctUsed%"
  }
}
  `.trim() + ' | Format-Table -AutoSize | Out-String -Width 300';

  return safeRun(script);
}

async function performanceInfo(): Promise<ToolResult> {
  const script = `
$cpuLoad = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$totalMemGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$freeMemGB  = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$usedMemGB  = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1MB, 2)
$memPct     = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize * 100, 1)

$uptime = (Get-Date) - $os.LastBootUpTime

Write-Output "=== CPU ==="
Write-Output "  Load: $cpuLoad%"
Write-Output "  Processors: $($env:NUMBER_OF_PROCESSORS)"
Write-Output ""
Write-Output "=== Memory ==="
Write-Output "  Total: $totalMemGB GB"
Write-Output "  Used:  $usedMemGB GB ($memPct%)"
Write-Output "  Free:  $freeMemGB GB"
Write-Output ""
Write-Output "=== Uptime ==="
Write-Output "  $($uptime.Days)d $($uptime.Hours)h $($uptime.Minutes)m"
Write-Output ""
Write-Output "=== Top 10 Processes by CPU ==="
Get-Process | Sort-Object CPU -Descending |
  Select-Object -First 10 Name, Id,
    @{N='CPU(s)';E={[math]::Round($_.CPU, 1)}},
    @{N='Mem(MB)';E={[math]::Round($_.WorkingSet64 / 1MB, 1)}} |
  Format-Table -AutoSize | Out-String -Width 300

Write-Output "=== Top 10 Processes by Memory ==="
Get-Process | Sort-Object WorkingSet64 -Descending |
  Select-Object -First 10 Name, Id,
    @{N='Mem(MB)';E={[math]::Round($_.WorkingSet64 / 1MB, 1)}},
    @{N='CPU(s)';E={[math]::Round($_.CPU, 1)}} |
  Format-Table -AutoSize | Out-String -Width 300
  `.trim();

  return safeRun(script);
}

// ── Event Log ────────────────────────────────────────────────────────────

async function eventLog(args: Record<string, unknown>): Promise<ToolResult> {
  const logName = (args.log_name as string) || 'System';
  const count = Math.min(Math.max((args.count as number) || 20, 1), 100);
  const level = args.level as string | undefined;

  // Map friendly level names to numeric levels used by Get-WinEvent
  const levelMap: Record<string, number> = {
    critical:    1,
    error:       2,
    warning:     3,
    information: 4,
  };

  let filterHash = `@{LogName='${psEsc(logName)}'`;
  if (level) {
    const numericLevel = levelMap[level.toLowerCase()];
    if (numericLevel !== undefined) {
      filterHash += `; Level=${numericLevel}`;
    }
  }
  filterHash += '}';

  const script = `
Get-WinEvent -FilterHashtable ${filterHash} -MaxEvents ${count} -ErrorAction SilentlyContinue |
  Select-Object TimeCreated, LevelDisplayName, Id, ProviderName,
    @{N='Message';E={ ($_.Message -split '\\n')[0] }} |
  Format-Table -AutoSize -Wrap | Out-String -Width 300
  `.trim();

  return safeRun(script);
}

// ---------------------------------------------------------------------------
// Main Execute Dispatcher
// ---------------------------------------------------------------------------

/**
 * Execute a tool by name with the provided arguments.
 * Returns a structured result or error object. Never throws.
 */
export async function execute(
  toolName: string,
  args: Record<string, unknown>
): Promise<ToolResult> {
  try {
    switch (toolName) {
      // Services
      case 'sys_services_list':         return await servicesList(args);
      case 'sys_service_control':       return await serviceControl(args);
      case 'sys_service_info':          return await serviceInfo(args);

      // Scheduled Tasks
      case 'sys_scheduled_task_create': return await scheduledTaskCreate(args);
      case 'sys_scheduled_task_list':   return await scheduledTaskList(args);
      case 'sys_scheduled_task_delete': return await scheduledTaskDelete(args);
      case 'sys_scheduled_task_run':    return await scheduledTaskRun(args);

      // Network
      case 'sys_network_info':          return await networkInfo();
      case 'sys_network_connections':   return await networkConnections(args);

      // Firewall
      case 'sys_firewall_rules':        return await firewallRules(args);
      case 'sys_firewall_add_rule':     return await firewallAddRule(args);

      // Package Managers
      case 'sys_package_install':       return await packageInstall(args);
      case 'sys_package_search':        return await packageSearch(args);
      case 'sys_package_list':          return await packageList(args);

      // Disk & Performance
      case 'sys_disk_usage':            return await diskUsage(args);
      case 'sys_performance_info':      return await performanceInfo();

      // Event Log
      case 'sys_event_log':             return await eventLog(args);

      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  } catch (err: unknown) {
    // Absolute last-resort safety net — individual handlers already catch,
    // but this prevents unhandled rejections from bubbling up.
    const msg = err instanceof Error ? err.message : String(err);
    return { error: `Unexpected error in ${toolName}: ${msg}` };
  }
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

/**
 * Check whether this connector should be active.
 * Always returns true on Windows — all tools use built-in PowerShell
 * commands and system utilities that ship with the OS.
 */
export async function detect(): Promise<boolean> {
  return os.platform() === 'win32';
}
