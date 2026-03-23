/**
 * HardwareProfiler -- Unit tests for GPU, VRAM, RAM, CPU, and disk detection.
 *
 * Tests full detection, GPU info population, no-GPU graceful fallback,
 * RAM via os.totalmem/freemem, CPU via os.cpus(), disk space via statfs,
 * cached profile (no redundant detection), NVIDIA GPU with nvidia-smi VRAM,
 * AMD/Intel GPU with degraded VRAM, and event emission on detection.
 *
 * All Electron, OS, fs, and child_process APIs are mocked -- no real
 * hardware dependency in CI.
 *
 * Sprint 6 O.1: "The Nerves" -- HardwareProfiler
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// -- Hoisted mocks (required for vi.mock factories) -------------------------

const mocks = vi.hoisted(() => ({
  getGPUInfo: vi.fn(),
  getPath: vi.fn(() => '/tmp/test-models'),
  totalmem: vi.fn(() => 32 * 1024 * 1024 * 1024),       // 32 GB
  freemem: vi.fn(() => 16 * 1024 * 1024 * 1024),         // 16 GB
  cpus: vi.fn(() =>
    Array.from({ length: 16 }, () => ({
      model: 'AMD Ryzen 7 5800X 8-Core Processor',
      speed: 3800,
    })),
  ),
  statfs: vi.fn(async () => ({
    blocks: 500_000_000,
    bsize: 4096,
    bavail: 250_000_000,
  })),
  execFile: vi.fn(),
}));

// -- Module mocks -----------------------------------------------------------

vi.mock('electron', () => ({
  app: {
    getGPUInfo: mocks.getGPUInfo,
    getPath: mocks.getPath,
    isPackaged: false,
    getName: vi.fn(() => 'nexus-test'),
    on: vi.fn(),
    whenReady: vi.fn().mockResolvedValue(undefined),
  },
  ipcMain: { handle: vi.fn(), on: vi.fn(), once: vi.fn() },
}));

vi.mock('node:os', () => ({
  default: {
    totalmem: mocks.totalmem,
    freemem: mocks.freemem,
    cpus: mocks.cpus,
  },
}));

vi.mock('node:fs/promises', () => ({
  statfs: mocks.statfs,
}));

vi.mock('node:child_process', () => ({
  execFile: mocks.execFile,
}));

// -- Import after mocks -----------------------------------------------------

import { HardwareProfiler } from '../../src/main/hardware/hardware-profiler';

// -- Helpers ----------------------------------------------------------------

/** Configure mocks for an NVIDIA GPU system. */
function setupNvidiaMocks(): void {
  mocks.getGPUInfo.mockResolvedValue({
    gpuDevice: [
      {
        vendorId: 0x10de,
        deviceId: 0x2786,
        driverVersion: '551.61',
      },
    ],
  });
  mocks.execFile.mockImplementation(
    (_cmd: string, _args: string[], _opts: unknown, cb: (err: Error | null, stdout: string, stderr: string) => void) => {
      cb(null, 'NVIDIA GeForce RTX 4070, 12288, 10500\n', '');
    },
  );
}

/** Configure mocks for a system with no discrete GPU. */
function setupNoGPUMocks(): void {
  mocks.getGPUInfo.mockResolvedValue({ gpuDevice: [] });
  mocks.execFile.mockImplementation(
    (_cmd: string, _args: string[], _opts: unknown, cb: (err: Error | null, stdout: string, stderr: string) => void) => {
      cb(new Error('nvidia-smi not found'), '', '');
    },
  );
}

/** Configure mocks for an Intel integrated GPU. */
function setupIntelMocks(): void {
  mocks.getGPUInfo.mockResolvedValue({
    gpuDevice: [
      {
        vendorId: 0x8086,
        deviceId: 0x3e92,
        driverVersion: '31.0.101.2111',
      },
    ],
  });
  mocks.execFile.mockImplementation(
    (_cmd: string, _args: string[], _opts: unknown, cb: (err: Error | null, stdout: string, stderr: string) => void) => {
      cb(new Error('nvidia-smi not found'), '', '');
    },
  );
}

// -- Test Suite --------------------------------------------------------------

describe('HardwareProfiler', () => {
  const originalPlatform = process.platform;

  beforeEach(() => {
    HardwareProfiler.resetInstance();
    vi.clearAllMocks();
    // Mock platform to 'win32' so nvidia-smi and WMI code paths are exercised
    Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  });

  afterEach(() => {
    Object.defineProperty(process, 'platform', { value: originalPlatform, configurable: true });
  });

  // VC-1: detect() populates a complete HardwareProfile
  it('detect() populates a complete HardwareProfile', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile).toBeDefined();
    expect(profile.gpu).toBeDefined();
    expect(profile.vram).toBeDefined();
    expect(profile.ram).toBeDefined();
    expect(profile.cpu).toBeDefined();
    expect(profile.disk).toBeDefined();
    expect(profile.detectedAt).toBeGreaterThan(0);

    // Verify all sub-objects have their required fields
    expect(typeof profile.gpu.name).toBe('string');
    expect(typeof profile.gpu.vendor).toBe('string');
    expect(typeof profile.gpu.driver).toBe('string');
    expect(typeof profile.gpu.available).toBe('boolean');
    expect(typeof profile.vram.total).toBe('number');
    expect(typeof profile.vram.available).toBe('number');
    expect(typeof profile.vram.systemReserved).toBe('number');
    expect(typeof profile.ram.total).toBe('number');
    expect(typeof profile.ram.available).toBe('number');
    expect(typeof profile.cpu.model).toBe('string');
    expect(typeof profile.cpu.cores).toBe('number');
    expect(typeof profile.cpu.threads).toBe('number');
    expect(typeof profile.disk.modelStoragePath).toBe('string');
    expect(typeof profile.disk.totalSpace).toBe('number');
    expect(typeof profile.disk.freeSpace).toBe('number');
  });

  // VC-2: detect() populates GPU name, vendor, driver, available flag
  it('detect() populates GPU fields from Electron getGPUInfo', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.gpu.vendor).toBe('nvidia');
    expect(profile.gpu.driver).toBe('551.61');
    expect(profile.gpu.available).toBe(true);
    expect(profile.gpu.name).toContain('NVIDIA');
  });

  // VC-3: detect() handles no-GPU systems gracefully
  it('detect() handles no-GPU systems gracefully', async () => {
    setupNoGPUMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.gpu.available).toBe(false);
    expect(profile.gpu.vendor).toBe('unknown');
    expect(profile.gpu.name).toBe('Unknown');
    expect(profile.gpu.driver).toBe('');
    expect(profile.vram.total).toBe(0);
    expect(profile.vram.available).toBe(0);
  });

  // VC-4: getProfile() returns total and available system RAM via os
  it('getProfile() returns total and available RAM from os module', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    await profiler.detect();
    const profile = profiler.getProfile();

    expect(profile).not.toBeNull();
    expect(profile!.ram.total).toBe(32 * 1024 * 1024 * 1024);
    expect(profile!.ram.available).toBe(16 * 1024 * 1024 * 1024);
    expect(mocks.totalmem).toHaveBeenCalled();
    expect(mocks.freemem).toHaveBeenCalled();
  });

  // VC-5: CPU info returns model name, core count, thread count via os.cpus()
  it('CPU info returns model, cores, and threads via os.cpus()', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.cpu.model).toBe('AMD Ryzen 7 5800X 8-Core Processor');
    expect(profile.cpu.threads).toBe(16);
    expect(profile.cpu.cores).toBe(8); // 16 threads / 2 heuristic
    expect(mocks.cpus).toHaveBeenCalled();
  });

  // VC-6: Disk info returns available space at userData path
  it('disk info returns space at userData path via statfs', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.disk.modelStoragePath).toBe('/tmp/test-models');
    expect(profile.disk.totalSpace).toBe(500_000_000 * 4096);
    expect(profile.disk.freeSpace).toBe(250_000_000 * 4096);
    expect(mocks.statfs).toHaveBeenCalled();
  });

  // VC-7: getProfile() returns cached result -- detect() called only once
  it('getProfile() returns cached result without redundant detection', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();

    // Before detect: null
    expect(profiler.getProfile()).toBeNull();

    // First detect
    const first = await profiler.detect();
    expect(mocks.getGPUInfo).toHaveBeenCalledTimes(1);

    // Second detect returns same cached result
    const second = await profiler.detect();
    expect(mocks.getGPUInfo).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
    expect(second.detectedAt).toBe(first.detectedAt);
  });

  // VC-8: NVIDIA GPU detected via getGPUInfo with VRAM from nvidia-smi
  it('NVIDIA GPU detected with VRAM from nvidia-smi', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.gpu.vendor).toBe('nvidia');
    expect(profile.gpu.name).toBe('NVIDIA GeForce RTX 4070');
    // 12288 MB -> bytes
    expect(profile.vram.total).toBe(12288 * 1024 * 1024);
    // available = 10500 MB -> bytes
    const expectedAvailable = 10500 * 1024 * 1024;
    expect(profile.vram.available).toBe(expectedAvailable);
    // system reserved = total - available from nvidia-smi perspective,
    // but the contract says ~1.5GB for desktop compositor
    expect(profile.vram.systemReserved).toBe(1.5 * 1024 * 1024 * 1024);
    // getEffectiveVRAM = total - systemReserved
    expect(profiler.getEffectiveVRAM()).toBe(
      12288 * 1024 * 1024 - 1.5 * 1024 * 1024 * 1024,
    );
  });

  // VC-9: AMD/Intel GPU detected with degraded VRAM info (total=0)
  it('AMD/Intel GPU detected with degraded VRAM info', async () => {
    setupIntelMocks();
    const profiler = HardwareProfiler.getInstance();
    const profile = await profiler.detect();

    expect(profile.gpu.vendor).toBe('intel');
    expect(profile.gpu.driver).toBe('31.0.101.2111');
    expect(profile.gpu.available).toBe(true);
    // Non-NVIDIA: VRAM can't be determined reliably
    expect(profile.vram.total).toBe(0);
    expect(profile.vram.available).toBe(0);
    expect(profiler.getEffectiveVRAM()).toBe(0);
  });

  // VC-10: Event emission and mock isolation
  it('emits hardware-detected event with profile payload', async () => {
    setupNvidiaMocks();
    const profiler = HardwareProfiler.getInstance();

    const listener = vi.fn();
    const unsub = profiler.on('hardware-detected', listener);

    const profile = await profiler.detect();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledWith(profile);

    // Unsubscribe works
    unsub();
    // Force re-detection via refresh
    await profiler.refresh();
    // Listener should still only have been called once (the first detect)
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
