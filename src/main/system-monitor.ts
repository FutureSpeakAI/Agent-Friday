/**
 * system-monitor.ts — System stats provider for Agent Friday.
 *
 * Provides CPU usage, memory, disk, uptime, and top processes.
 * Uses os module + cross-platform process listing.
 * Windows: PowerShell for per-process CPU/memory.
 *
 * Contract consumed by FridayMonitor.tsx via eve.system namespace.
 */

import os from 'os';
import { exec } from 'child_process';
import { promisify } from 'util';
import fs from 'fs/promises';

const execAsync = promisify(exec);

/* ── Types ────────────────────────────────────────────────────────────── */

export interface SystemStats {
  cpuPercent: number;
  memUsedMB: number;
  memTotalMB: number;
  diskUsedGB: number;
  diskTotalGB: number;
  uptime: number;
}

export interface ProcessInfo {
  name: string;
  pid: number;
  cpu: number;
  mem: number; // in MB
}

/* ── CPU measurement ─────────────────────────────────────────────────── */

let prevCpuIdle = 0;
let prevCpuTotal = 0;

function measureCpuPercent(): number {
  const cpus = os.cpus();
  let idle = 0;
  let total = 0;

  for (const cpu of cpus) {
    idle += cpu.times.idle;
    total += cpu.times.user + cpu.times.nice + cpu.times.sys + cpu.times.idle + cpu.times.irq;
  }

  const diffIdle = idle - prevCpuIdle;
  const diffTotal = total - prevCpuTotal;

  prevCpuIdle = idle;
  prevCpuTotal = total;

  if (diffTotal === 0) return 0;
  return Math.round(((diffTotal - diffIdle) / diffTotal) * 1000) / 10;
}

/* ── Disk usage ──────────────────────────────────────────────────────── */

async function getDiskUsage(): Promise<{ usedGB: number; totalGB: number }> {
  const platform = os.platform();
  try {
    if (platform === 'win32') {
      const { stdout } = await execAsync(
        'powershell.exe -NoProfile -Command "Get-PSDrive C | Select-Object Used,Free | ConvertTo-Json"',
        { timeout: 5000 },
      );
      const data = JSON.parse(stdout.trim());
      const usedBytes = Number(data.Used) || 0;
      const freeBytes = Number(data.Free) || 0;
      const totalBytes = usedBytes + freeBytes;
      return {
        usedGB: Math.round((usedBytes / (1024 ** 3)) * 10) / 10,
        totalGB: Math.round((totalBytes / (1024 ** 3)) * 10) / 10,
      };
    } else {
      const { stdout } = await execAsync("df -k / | tail -1 | awk '{print $2, $3}'", {
        timeout: 5000,
      });
      const [totalK, usedK] = stdout.trim().split(/\s+/).map(Number);
      return {
        usedGB: Math.round((usedK / (1024 * 1024)) * 10) / 10,
        totalGB: Math.round((totalK / (1024 * 1024)) * 10) / 10,
      };
    }
  } catch {
    return { usedGB: 0, totalGB: 0 };
  }
}

/* ── Process list ────────────────────────────────────────────────────── */

async function getTopProcesses(limit = 30): Promise<ProcessInfo[]> {
  const platform = os.platform();
  try {
    if (platform === 'win32') {
      const { stdout } = await execAsync(
        `powershell.exe -NoProfile -Command "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First ${limit} Name,Id,CPU,@{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | ConvertTo-Json"`,
        { timeout: 8000 },
      );
      const parsed = JSON.parse(stdout.trim());
      const procs = Array.isArray(parsed) ? parsed : [parsed];
      return procs.map((p: any) => ({
        name: String(p.Name || ''),
        pid: Number(p.Id) || 0,
        cpu: Math.round((Number(p.CPU) || 0) * 10) / 10,
        mem: Number(p.MemMB) || 0,
      }));
    } else {
      const { stdout } = await execAsync(
        `ps aux --sort=-%mem | head -${limit + 1} | tail -${limit}`,
        { timeout: 5000 },
      );
      return stdout
        .trim()
        .split('\n')
        .map((line) => {
          const parts = line.trim().split(/\s+/);
          const memPercent = parseFloat(parts[3]) || 0;
          const totalMem = os.totalmem() / (1024 * 1024);
          return {
            name: parts[10] || parts[parts.length - 1] || '',
            pid: parseInt(parts[1]) || 0,
            cpu: parseFloat(parts[2]) || 0,
            mem: Math.round((memPercent / 100) * totalMem * 10) / 10,
          };
        });
    }
  } catch {
    return [];
  }
}

/* ── Public API ───────────────────────────────────────────────────────── */

export const systemMonitor = {
  /** Get system stats: CPU, memory, disk, uptime. */
  async getStats(): Promise<SystemStats> {
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    const disk = await getDiskUsage();

    return {
      cpuPercent: measureCpuPercent(),
      memUsedMB: Math.round((totalMem - freeMem) / (1024 * 1024)),
      memTotalMB: Math.round(totalMem / (1024 * 1024)),
      diskUsedGB: disk.usedGB,
      diskTotalGB: disk.totalGB,
      uptime: Math.round(os.uptime()),
    };
  },

  /** Get top processes by memory usage. */
  async getProcesses(limit = 30): Promise<ProcessInfo[]> {
    return getTopProcesses(limit);
  },
};
