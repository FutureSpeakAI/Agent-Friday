/**
 * system-monitor.ts — Unit tests for system stats provider.
 *
 * Tests CPU measurement, memory calculation, disk usage parsing,
 * and process list parsing by mocking os module and child_process.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mocks ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  cpus: vi.fn(),
  totalmem: vi.fn(),
  freemem: vi.fn(),
  uptime: vi.fn(),
  platform: vi.fn(),
  exec: vi.fn(),
}));

vi.mock('os', () => ({
  default: {
    cpus: mocks.cpus,
    totalmem: mocks.totalmem,
    freemem: mocks.freemem,
    uptime: mocks.uptime,
    platform: mocks.platform,
  },
  cpus: mocks.cpus,
  totalmem: mocks.totalmem,
  freemem: mocks.freemem,
  uptime: mocks.uptime,
  platform: mocks.platform,
}));

vi.mock('child_process', () => ({
  exec: mocks.exec,
}));

vi.mock('util', () => ({
  promisify: (fn: any) => (...args: any[]) =>
    new Promise((resolve, reject) => {
      fn(...args, (err: any, stdout: string, stderr: string) => {
        if (err) reject(err);
        else resolve({ stdout, stderr });
      });
    }),
}));

vi.mock('fs/promises', () => ({
  default: { readFile: vi.fn(), writeFile: vi.fn() },
}));

// ── Import (after mocks) ──────────────────────────────────────────

let systemMonitor: typeof import('../../src/main/system-monitor').systemMonitor;

// ── Helpers ────────────────────────────────────────────────────────

function makeCpuInfo(user: number, nice: number, sys: number, idle: number, irq: number) {
  return {
    model: 'Mock CPU',
    speed: 3000,
    times: { user, nice, sys, idle, irq },
  };
}

const DISK_PS_OUTPUT = JSON.stringify({ Used: 107374182400, Free: 107374182400 }); // 100 GB each

const PROCESS_PS_OUTPUT = JSON.stringify([
  { Name: 'chrome', Id: 1234, CPU: 15.5, MemMB: 512.3 },
  { Name: 'node', Id: 5678, CPU: 8.2, MemMB: 256.1 },
  { Name: 'explorer', Id: 9012, CPU: 0.5, MemMB: 128.0 },
]);

// ── Tests ──────────────────────────────────────────────────────────

describe('systemMonitor.getStats', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    mocks.platform.mockReturnValue('win32');
    mocks.totalmem.mockReturnValue(16 * 1024 * 1024 * 1024); // 16 GB
    mocks.freemem.mockReturnValue(8 * 1024 * 1024 * 1024);   // 8 GB free
    mocks.uptime.mockReturnValue(86400); // 1 day
    mocks.cpus.mockReturnValue([
      makeCpuInfo(1000, 0, 500, 8500, 0), // 15% usage on this core
    ]);
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        callback(null, DISK_PS_OUTPUT, '');
      },
    );
    const mod = await import('../../src/main/system-monitor');
    systemMonitor = mod.systemMonitor;
  });

  it('returns memory stats in MB', async () => {
    const stats = await systemMonitor.getStats();
    expect(stats.memTotalMB).toBe(16384); // 16 GB in MB
    expect(stats.memUsedMB).toBe(8192);   // 8 GB used
  });

  it('returns disk usage in GB on Windows', async () => {
    const stats = await systemMonitor.getStats();
    expect(stats.diskUsedGB).toBe(100);
    expect(stats.diskTotalGB).toBe(200);
  });

  it('returns uptime in seconds', async () => {
    const stats = await systemMonitor.getStats();
    expect(stats.uptime).toBe(86400);
  });

  it('returns cpuPercent as a number', async () => {
    const stats = await systemMonitor.getStats();
    expect(typeof stats.cpuPercent).toBe('number');
    expect(stats.cpuPercent).toBeGreaterThanOrEqual(0);
    expect(stats.cpuPercent).toBeLessThanOrEqual(100);
  });

  it('gracefully handles disk query failure', async () => {
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        callback(new Error('PowerShell failed'), '', 'error');
      },
    );

    const stats = await systemMonitor.getStats();
    expect(stats.diskUsedGB).toBe(0);
    expect(stats.diskTotalGB).toBe(0);
    // Other stats should still be present
    expect(stats.memTotalMB).toBe(16384);
  });
});

describe('systemMonitor.getProcesses', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    mocks.platform.mockReturnValue('win32');
    mocks.totalmem.mockReturnValue(16 * 1024 * 1024 * 1024);
    mocks.freemem.mockReturnValue(8 * 1024 * 1024 * 1024);
    mocks.uptime.mockReturnValue(86400);
    mocks.cpus.mockReturnValue([makeCpuInfo(1000, 0, 500, 8500, 0)]);
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        callback(null, PROCESS_PS_OUTPUT, '');
      },
    );
    const mod = await import('../../src/main/system-monitor');
    systemMonitor = mod.systemMonitor;
  });

  it('parses process list from PowerShell output', async () => {
    const procs = await systemMonitor.getProcesses();
    expect(procs).toHaveLength(3);
    expect(procs[0].name).toBe('chrome');
    expect(procs[0].pid).toBe(1234);
    expect(procs[0].cpu).toBe(15.5);
    expect(procs[0].mem).toBe(512.3);
  });

  it('handles single process result (non-array JSON)', async () => {
    const singleProc = JSON.stringify({
      Name: 'solo', Id: 42, CPU: 1.0, MemMB: 64,
    });
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        callback(null, singleProc, '');
      },
    );

    const procs = await systemMonitor.getProcesses();
    expect(procs).toHaveLength(1);
    expect(procs[0].name).toBe('solo');
  });

  it('returns empty array on process query failure', async () => {
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        callback(new Error('Access denied'), '', 'error');
      },
    );

    const procs = await systemMonitor.getProcesses();
    expect(procs).toEqual([]);
  });

  it('respects the limit parameter', async () => {
    mocks.exec.mockImplementation(
      (cmd: string, opts: any, cb?: Function) => {
        const callback = cb || opts;
        // Verify limit is passed through in the command
        expect(cmd).toContain('10');
        callback(null, PROCESS_PS_OUTPUT, '');
      },
    );

    await systemMonitor.getProcesses(10);
    expect(mocks.exec).toHaveBeenCalled();
  });
});
