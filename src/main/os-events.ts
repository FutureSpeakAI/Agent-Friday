/**
 * os-events.ts — OS-Level Event Engine.
 * Combines four subsystems into one module:
 *  1. App Lifecycle — detects when processes start/stop (WMI event subscription)
 *  2. Power & Session — sleep/wake/lock/unlock/shutdown via Electron powerMonitor
 *  3. Display Awareness — multi-monitor layout, resolution, DPI, display changes
 *  4. File Associations — maps extensions to default programs
 *
 * All events flow through a unified EventBus that the renderer and AI can subscribe to.
 */

import { powerMonitor, screen, BrowserWindow, app } from 'electron';
import { execFile } from 'child_process';
import * as path from 'path';
import { getSanitizedEnv } from './settings';

// ── Types ───────────────────────────────────────────────────────────────────

export type OSEventType =
  | 'app:launched' | 'app:closed'
  | 'power:suspend' | 'power:resume' | 'power:shutdown'
  | 'session:lock' | 'session:unlock'
  | 'power:ac' | 'power:battery' | 'power:low-battery'
  | 'display:added' | 'display:removed' | 'display:metrics-changed';

export interface OSEvent {
  type: OSEventType;
  timestamp: number;
  data: Record<string, unknown>;
}

export interface ProcessInfo {
  pid: number;
  name: string;
  path?: string;
  timestamp: number;
}

export interface DisplayInfo {
  id: number;
  label: string;
  bounds: { x: number; y: number; width: number; height: number };
  workArea: { x: number; y: number; width: number; height: number };
  scaleFactor: number;
  rotation: number;
  internal: boolean;
  isPrimary: boolean;
}

export interface PowerState {
  onBattery: boolean;
  batteryPercent: number;
  /** Minutes of battery remaining (-1 if unknown or plugged in) */
  timeRemaining: number;
  /** 'ac' | 'battery' | 'unknown' */
  source: string;
  isLocked: boolean;
  isSuspended: boolean;
  lastSuspend: number;
  lastResume: number;
  lastLock: number;
  lastUnlock: number;
}

export interface FileAssociation {
  extension: string;
  /** Friendly program name */
  programName: string;
  /** Executable path */
  executablePath: string;
}

export interface StartupProgram {
  name: string;
  command: string;
  location: 'registry-user' | 'registry-machine' | 'startup-folder' | 'task-scheduler';
  enabled: boolean;
}

// ── Implementation ──────────────────────────────────────────────────────────

class OSEventEngine {
  private mainWindow: BrowserWindow | null = null;
  private recentEvents: OSEvent[] = [];
  private processCache = new Map<string, number>(); // name -> last seen timestamp
  private processPoller: ReturnType<typeof setInterval> | null = null;
  private powerState: PowerState = {
    onBattery: false,
    batteryPercent: 100,
    timeRemaining: -1,
    source: 'unknown',
    isLocked: false,
    isSuspended: false,
    lastSuspend: 0,
    lastResume: 0,
    lastLock: 0,
    lastUnlock: 0,
  };
  private knownProcesses = new Set<string>(); // Process names from last poll

  // ── Public API ──────────────────────────────────────────────────────

  initialize(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow;

    // 1. Power & Session events via Electron powerMonitor
    this.initPowerMonitor();

    // 2. Display events via Electron screen
    this.initDisplayMonitor();

    // 3. App lifecycle via periodic process polling
    this.initProcessMonitor();

    // 4. Initial power state check
    this.updatePowerState().catch(() => {});

    console.log('[OSEvents] Initialized — power, display, and process monitoring active');
  }

  stop(): void {
    if (this.processPoller) {
      clearInterval(this.processPoller);
      this.processPoller = null;
    }
    // Electron event listeners are cleaned up automatically on app quit
    console.log('[OSEvents] Stopped');
  }

  /** Get the current power state */
  getPowerState(): PowerState {
    return { ...this.powerState };
  }

  /** Get recent OS events */
  getRecentEvents(limit = 30): OSEvent[] {
    return this.recentEvents.slice(-limit);
  }

  /** Get info about all connected displays */
  getDisplays(): DisplayInfo[] {
    const displays = screen.getAllDisplays();
    const primary = screen.getPrimaryDisplay();

    return displays.map(d => ({
      id: d.id,
      label: d.label || `Display ${d.id}`,
      bounds: { ...d.bounds },
      workArea: { ...d.workArea },
      scaleFactor: d.scaleFactor,
      rotation: d.rotation,
      internal: d.internal ?? false,
      isPrimary: d.id === primary.id,
    }));
  }

  /** Look up which program opens a given file extension */
  async getFileAssociation(ext: string): Promise<FileAssociation | null> {
    const safeExt = ext.replace(/[^a-zA-Z0-9]/g, '');
    if (!safeExt) return null;

    const script = `
      try {
        $ext = ".${safeExt}"
        $assoc = (cmd /c "assoc $ext" 2>$null) -replace "^[^=]+=", ""
        if (-not $assoc) { Write-Output "null"; exit 0 }
        $ftype = (cmd /c "ftype $assoc" 2>$null) -replace "^[^=]+=", ""
        if (-not $ftype) { Write-Output "null"; exit 0 }
        $exePath = ($ftype -split '"')[1]
        if (-not $exePath) { $exePath = ($ftype -split ' ')[0] }
        [PSCustomObject]@{
          Extension = $ext
          ProgramName = $assoc
          ExecutablePath = $exePath
        } | ConvertTo-Json -Compress
      } catch {
        Write-Output "null"
      }
    `;

    try {
      const raw = await this.runPowerShell(script);
      if (!raw || raw.trim() === 'null') return null;
      const obj = JSON.parse(raw);
      return {
        extension: String(obj.Extension || ''),
        programName: String(obj.ProgramName || ''),
        executablePath: String(obj.ExecutablePath || ''),
      };
    } catch {
      return null;
    }
  }

  /** Get multiple file associations at once */
  async getFileAssociations(extensions: string[]): Promise<FileAssociation[]> {
    const results: FileAssociation[] = [];
    // Batch — one PowerShell invocation for all
    const safeExts = extensions.map(e => e.replace(/[^a-zA-Z0-9]/g, '')).filter(Boolean);
    if (safeExts.length === 0) return [];

    const script = `
      $results = @()
      $extensions = @(${safeExts.map(e => `".${e}"`).join(',')})
      foreach ($ext in $extensions) {
        try {
          $assoc = (cmd /c "assoc $ext" 2>$null) -replace "^[^=]+=", ""
          if ($assoc) {
            $ftype = (cmd /c "ftype $assoc" 2>$null) -replace "^[^=]+=", ""
            $exePath = ""
            if ($ftype) {
              $exePath = ($ftype -split '"')[1]
              if (-not $exePath) { $exePath = ($ftype -split ' ')[0] }
            }
            $results += [PSCustomObject]@{
              Extension = $ext
              ProgramName = $assoc
              ExecutablePath = $exePath
            }
          }
        } catch {}
      }
      $results | ConvertTo-Json -Compress
    `;

    try {
      const raw = await this.runPowerShell(script);
      if (!raw || raw.trim() === 'null' || raw.trim() === '') return [];
      const parsed = JSON.parse(raw);
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      return arr.filter(Boolean).map((obj: any) => ({
        extension: String(obj.Extension || ''),
        programName: String(obj.ProgramName || ''),
        executablePath: String(obj.ExecutablePath || ''),
      }));
    } catch {
      return [];
    }
  }

  /** Open a file with its default program */
  async openWithDefault(filePath: string): Promise<boolean> {
    return new Promise((resolve) => {
      execFile('cmd.exe', ['/c', 'start', '', filePath], {
        windowsHide: true,
        env: getSanitizedEnv(),
      }, (err) => {
        resolve(!err);
      });
    });
  }

  /** List startup programs */
  async getStartupPrograms(): Promise<StartupProgram[]> {
    const script = `
      $results = @()

      # Registry: Current User Run
      try {
        Get-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" -ErrorAction SilentlyContinue |
          Get-Member -MemberType NoteProperty |
          Where-Object { $_.Name -notmatch '^PS' } |
          ForEach-Object {
            $val = (Get-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run").$($_.Name)
            $results += [PSCustomObject]@{ Name = $_.Name; Command = $val; Location = "registry-user"; Enabled = $true }
          }
      } catch {}

      # Registry: Local Machine Run
      try {
        Get-ItemProperty -Path "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" -ErrorAction SilentlyContinue |
          Get-Member -MemberType NoteProperty |
          Where-Object { $_.Name -notmatch '^PS' } |
          ForEach-Object {
            $val = (Get-ItemProperty -Path "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run").$($_.Name)
            $results += [PSCustomObject]@{ Name = $_.Name; Command = $val; Location = "registry-machine"; Enabled = $true }
          }
      } catch {}

      # Startup Folder
      try {
        $startupPath = [Environment]::GetFolderPath("Startup")
        Get-ChildItem -Path $startupPath -ErrorAction SilentlyContinue | ForEach-Object {
          $results += [PSCustomObject]@{ Name = $_.BaseName; Command = $_.FullName; Location = "startup-folder"; Enabled = $true }
        }
      } catch {}

      # Task Scheduler (boot/logon triggers only)
      try {
        Get-ScheduledTask -ErrorAction SilentlyContinue |
          Where-Object { $_.State -ne 'Disabled' -and $_.Triggers } |
          Where-Object {
            $hasBoot = $_.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' -or $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' }
            $hasBoot
          } |
          Select-Object -First 30 |
          ForEach-Object {
            $results += [PSCustomObject]@{
              Name = $_.TaskName
              Command = ($_.Actions | Select-Object -First 1).Execute
              Location = "task-scheduler"
              Enabled = ($_.State -eq 'Ready')
            }
          }
      } catch {}

      $results | ConvertTo-Json -Compress
    `;

    try {
      const raw = await this.runPowerShell(script);
      if (!raw || raw.trim() === 'null' || raw.trim() === '') return [];
      const parsed = JSON.parse(raw);
      const arr = Array.isArray(parsed) ? parsed : [parsed];
      return arr.filter(Boolean).map((item: any) => ({
        name: String(item.Name || ''),
        command: String(item.Command || ''),
        location: String(item.Location || 'registry-user') as StartupProgram['location'],
        enabled: Boolean(item.Enabled),
      }));
    } catch {
      return [];
    }
  }

  /** Build context string for AI prompt injection */
  getContextString(): string {
    const lines: string[] = [];

    // Power state
    if (this.powerState.onBattery) {
      lines.push(`## Power: Battery ${this.powerState.batteryPercent}%${this.powerState.timeRemaining > 0 ? ` (~${this.powerState.timeRemaining}min remaining)` : ''}`);
    }

    if (this.powerState.isLocked) {
      lines.push('## Session: Locked');
    }

    // Recent notable events (last 5 minutes)
    const cutoff = Date.now() - 5 * 60_000;
    const recent = this.recentEvents.filter(e => e.timestamp > cutoff);
    const launches = recent.filter(e => e.type === 'app:launched');
    const closes = recent.filter(e => e.type === 'app:closed');

    if (launches.length > 0) {
      lines.push(`**Apps launched:** ${launches.map(e => e.data.name).join(', ')}`);
    }
    if (closes.length > 0) {
      lines.push(`**Apps closed:** ${closes.map(e => e.data.name).join(', ')}`);
    }

    // Display info if multi-monitor
    const displays = this.getDisplays();
    if (displays.length > 1) {
      lines.push(`**Displays:** ${displays.length} monitors (${displays.map(d => `${d.bounds.width}x${d.bounds.height}@${d.scaleFactor}x`).join(', ')})`);
    }

    return lines.length > 0 ? lines.join('\n') : '';
  }

  // ── Power Monitor ───────────────────────────────────────────────────

  private initPowerMonitor(): void {
    powerMonitor.on('suspend', () => {
      this.powerState.isSuspended = true;
      this.powerState.lastSuspend = Date.now();
      this.emit({ type: 'power:suspend', timestamp: Date.now(), data: {} });
    });

    powerMonitor.on('resume', () => {
      this.powerState.isSuspended = false;
      this.powerState.lastResume = Date.now();
      const suspendDuration = this.powerState.lastSuspend
        ? Math.round((Date.now() - this.powerState.lastSuspend) / 1000)
        : 0;
      this.emit({ type: 'power:resume', timestamp: Date.now(), data: { suspendDurationSec: suspendDuration } });
    });

    powerMonitor.on('lock-screen', () => {
      this.powerState.isLocked = true;
      this.powerState.lastLock = Date.now();
      this.emit({ type: 'session:lock', timestamp: Date.now(), data: {} });
    });

    powerMonitor.on('unlock-screen', () => {
      this.powerState.isLocked = false;
      this.powerState.lastUnlock = Date.now();
      const lockDuration = this.powerState.lastLock
        ? Math.round((Date.now() - this.powerState.lastLock) / 1000)
        : 0;
      this.emit({ type: 'session:unlock', timestamp: Date.now(), data: { lockDurationSec: lockDuration } });
    });

    powerMonitor.on('shutdown', () => {
      this.emit({ type: 'power:shutdown', timestamp: Date.now(), data: {} });
    });

    powerMonitor.on('on-ac', () => {
      this.powerState.onBattery = false;
      this.powerState.source = 'ac';
      this.emit({ type: 'power:ac', timestamp: Date.now(), data: {} });
    });

    powerMonitor.on('on-battery', () => {
      this.powerState.onBattery = true;
      this.powerState.source = 'battery';
      this.emit({ type: 'power:battery', timestamp: Date.now(), data: {} });
    });
  }

  private async updatePowerState(): Promise<void> {
    try {
      const state = powerMonitor.getSystemIdleState(300);
      this.powerState.isLocked = state === 'locked';
      // Check battery via PowerShell
      const script = `
        try {
          $b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
          if ($b) {
            [PSCustomObject]@{
              Percent = $b.EstimatedChargeRemaining
              TimeRemaining = $b.EstimatedRunTime
              OnBattery = ($b.BatteryStatus -eq 1)
            } | ConvertTo-Json -Compress
          } else {
            '{"Percent":100,"TimeRemaining":-1,"OnBattery":false}'
          }
        } catch { '{"Percent":100,"TimeRemaining":-1,"OnBattery":false}' }
      `;
      const raw = await this.runPowerShell(script);
      const data = JSON.parse(raw || '{}');
      this.powerState.batteryPercent = Number(data.Percent) || 100;
      this.powerState.timeRemaining = Number(data.TimeRemaining) || -1;
      this.powerState.onBattery = Boolean(data.OnBattery);
      this.powerState.source = this.powerState.onBattery ? 'battery' : 'ac';
    } catch { /* non-critical */ }
  }

  // ── Display Monitor ─────────────────────────────────────────────────

  private initDisplayMonitor(): void {
    screen.on('display-added', (_event, display) => {
      this.emit({
        type: 'display:added',
        timestamp: Date.now(),
        data: { id: display.id, bounds: display.bounds, scaleFactor: display.scaleFactor },
      });
    });

    screen.on('display-removed', (_event, display) => {
      this.emit({
        type: 'display:removed',
        timestamp: Date.now(),
        data: { id: display.id },
      });
    });

    screen.on('display-metrics-changed', (_event, display, changedMetrics) => {
      this.emit({
        type: 'display:metrics-changed',
        timestamp: Date.now(),
        data: {
          id: display.id,
          changed: changedMetrics,
          bounds: display.bounds,
          scaleFactor: display.scaleFactor,
        },
      });
    });
  }

  // ── Process Monitor (App Lifecycle) ─────────────────────────────────

  private initProcessMonitor(): void {
    // Poll every 5 seconds — lightweight, detects new/closed processes
    this.pollProcesses(); // initial snapshot (silent, no events)
    this.processPoller = setInterval(() => this.pollProcesses(), 5000);
  }

  private async pollProcesses(): Promise<void> {
    try {
      const script = `
        Get-Process | Where-Object { $_.MainWindowTitle -ne '' } |
          Select-Object -Property Name -Unique |
          ForEach-Object { $_.Name } | ConvertTo-Json -Compress
      `;

      const raw = await this.runPowerShell(script);
      if (!raw) return;

      const current = new Set<string>();
      try {
        const parsed = JSON.parse(raw);
        const names: string[] = Array.isArray(parsed) ? parsed : [parsed];
        for (const n of names) {
          if (typeof n === 'string') current.add(n);
        }
      } catch { return; }

      // First poll — just capture baseline, don't emit
      if (this.knownProcesses.size === 0) {
        this.knownProcesses = current;
        return;
      }

      // Detect launches (in current but not in known)
      for (const name of current) {
        if (!this.knownProcesses.has(name)) {
          this.emit({
            type: 'app:launched',
            timestamp: Date.now(),
            data: { name },
          });
        }
      }

      // Detect closes (in known but not in current)
      for (const name of this.knownProcesses) {
        if (!current.has(name)) {
          this.emit({
            type: 'app:closed',
            timestamp: Date.now(),
            data: { name },
          });
        }
      }

      this.knownProcesses = current;
    } catch { /* non-critical polling failure */ }
  }

  // ── Event Bus ───────────────────────────────────────────────────────

  private emit(event: OSEvent): void {
    this.recentEvents.push(event);
    if (this.recentEvents.length > 200) {
      this.recentEvents = this.recentEvents.slice(-200);
    }

    // Forward to renderer
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send('os:event', event);
    }
  }

  // ── Helpers ─────────────────────────────────────────────────────────

  private runPowerShell(script: string): Promise<string> {
    return new Promise((resolve, reject) => {
      execFile(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        {
          timeout: 10_000,
          maxBuffer: 2 * 1024 * 1024,
          env: getSanitizedEnv(),
          windowsHide: true,
        },
        (err, stdout, stderr) => {
          if (err) reject(new Error(stderr || err.message));
          else resolve(stdout.trim());
        },
      );
    });
  }
}

export const osEvents = new OSEventEngine();
