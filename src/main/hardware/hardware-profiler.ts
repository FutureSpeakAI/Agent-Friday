/**
 * hardware-profiler.ts -- Singleton that detects GPU, VRAM, RAM, CPU, and disk.
 *
 * Detects hardware once at startup, caches the result, and emits an event.
 * No recommendations are made here -- that is the responsibility of O.2.
 *
 * Sprint 6 O.1: "The Nerves" -- HardwareProfiler
 */

import { app } from 'electron';
import os from 'node:os';
import { statfs } from 'node:fs/promises';
import { execFile } from 'node:child_process';

// -- Contract Types ---------------------------------------------------------

export interface HardwareProfile {
  gpu: GPUInfo;
  vram: VRAMInfo;
  ram: RAMInfo;
  cpu: CPUInfo;
  disk: DiskInfo;
  detectedAt: number; // timestamp
}

export interface GPUInfo {
  name: string;       // e.g., "NVIDIA GeForce RTX 4070"
  vendor: string;     // "nvidia" | "amd" | "intel" | "apple" | "unknown"
  driver: string;     // driver version string
  available: boolean; // GPU detected and functional
}

export interface VRAMInfo {
  total: number;          // bytes
  available: number;      // bytes (total minus system reservation)
  systemReserved: number; // ~1.5GB for desktop compositor
}

export interface RAMInfo {
  total: number;     // bytes
  available: number; // bytes (free at detection time)
}

export interface CPUInfo {
  model: string;  // e.g., "AMD Ryzen 7 5800X"
  cores: number;  // physical cores
  threads: number; // logical threads
}

export interface DiskInfo {
  modelStoragePath: string; // where models are stored
  totalSpace: number;       // bytes
  freeSpace: number;        // bytes
}

// -- Constants --------------------------------------------------------------

/** Estimated VRAM reserved by desktop compositor (~1.5 GB). */
const SYSTEM_RESERVED_VRAM = 1.5 * 1024 * 1024 * 1024;

/** Well-known PCI vendor IDs. */
const VENDOR_NVIDIA = 0x10de;
const VENDOR_AMD = 0x1002;
const VENDOR_INTEL = 0x8086;
const VENDOR_APPLE = 0x106b;

// -- Event types ------------------------------------------------------------

type HardwareEvent = 'hardware-detected';
type HardwareCallback = (profile: HardwareProfile) => void;

// -- Helpers ----------------------------------------------------------------

/** Map a PCI vendor ID to a human-readable vendor string. */
function vendorFromId(vendorId: number): string {
  switch (vendorId) {
    case VENDOR_NVIDIA: return 'nvidia';
    case VENDOR_AMD:    return 'amd';
    case VENDOR_INTEL:  return 'intel';
    case VENDOR_APPLE:  return 'apple';
    default:            return 'unknown';
  }
}

/** Query nvidia-smi for GPU name, total VRAM (MB), and free VRAM (MB). */
function queryNvidiaSmi(): Promise<{ name: string; totalMB: number; freeMB: number } | null> {
  return new Promise((resolve) => {
    execFile(
      'nvidia-smi',
      ['--query-gpu=name,memory.total,memory.free', '--format=csv,noheader,nounits'],
      (err: Error | null, stdout: string) => {
        if (err || !stdout.trim()) {
          resolve(null);
          return;
        }
        const parts = stdout.trim().split(', ');
        if (parts.length < 3) {
          resolve(null);
          return;
        }
        resolve({
          name: parts[0],
          totalMB: parseInt(parts[1], 10) || 0,
          freeMB: parseInt(parts[2], 10) || 0,
        });
      },
    );
  });
}

/** Query Windows WMI for GPU name and total VRAM (fallback for non-NVIDIA GPUs). */
function queryWindowsVRAM(): Promise<{ name: string; totalBytes: number } | null> {
  if (process.platform !== 'win32') return Promise.resolve(null);
  return new Promise((resolve) => {
    execFile(
      'wmic',
      ['path', 'win32_VideoController', 'get', 'Name,AdapterRAM', '/format:list'],
      { timeout: 5000 },
      (err: Error | null, stdout: string) => {
        if (err || !stdout.trim()) {
          resolve(null);
          return;
        }
        const gpus: { name: string; totalBytes: number }[] = [];
        let name = '';
        let ram = 0;
        for (const line of stdout.split('\n')) {
          const trimmed = line.trim();
          if (trimmed.startsWith('AdapterRAM=')) {
            ram = parseInt(trimmed.slice(11), 10) || 0;
          } else if (trimmed.startsWith('Name=')) {
            name = trimmed.slice(5).trim();
          } else if (trimmed === '' && (name || ram)) {
            if (name && ram > 0) gpus.push({ name, totalBytes: ram });
            name = '';
            ram = 0;
          }
        }
        if (name && ram > 0) gpus.push({ name, totalBytes: ram });
        if (gpus.length > 0) {
          // Pick the GPU with the most VRAM (likely discrete)
          gpus.sort((a, b) => b.totalBytes - a.totalBytes);
          resolve(gpus[0]);
        } else {
          resolve(null);
        }
      },
    );
  });
}

// -- HardwareProfiler -------------------------------------------------------

export class HardwareProfiler {
  private static instance: HardwareProfiler | null = null;

  private cachedProfile: HardwareProfile | null = null;
  private listeners = new Map<HardwareEvent, HardwareCallback[]>();

  private constructor() {
    // Singleton -- use getInstance()
  }

  static getInstance(): HardwareProfiler {
    if (!HardwareProfiler.instance) {
      HardwareProfiler.instance = new HardwareProfiler();
    }
    return HardwareProfiler.instance;
  }

  static resetInstance(): void {
    HardwareProfiler.instance = null;
  }

  // -- Public API -----------------------------------------------------------

  /** Run full hardware detection, cache the result, and emit event. */
  async detect(): Promise<HardwareProfile> {
    if (this.cachedProfile) {
      return this.cachedProfile;
    }

    const [gpuResult, ramResult, cpuResult, diskResult] = await Promise.all([
      this.detectGPU(),
      this.detectRAM(),
      this.detectCPU(),
      this.detectDisk(),
    ]);

    const profile: HardwareProfile = {
      gpu: gpuResult.gpu,
      vram: gpuResult.vram,
      ram: ramResult,
      cpu: cpuResult,
      disk: diskResult,
      detectedAt: Date.now(),
    };

    this.cachedProfile = profile;
    this.emit('hardware-detected', profile);
    return profile;
  }

  /** Return the cached profile, or null if detect() has not been called. */
  getProfile(): HardwareProfile | null {
    return this.cachedProfile;
  }

  /** Force re-detection (clears cache first). */
  async refresh(): Promise<HardwareProfile> {
    this.cachedProfile = null;
    return this.detect();
  }

  /** Total VRAM minus system reserved (bytes). Returns 0 if no VRAM. */
  getEffectiveVRAM(): number {
    if (!this.cachedProfile) return 0;
    const { total } = this.cachedProfile.vram;
    if (total === 0) return 0;
    return Math.max(0, total - SYSTEM_RESERVED_VRAM);
  }

  /** Subscribe to events. Returns an unsubscribe function. */
  on(event: HardwareEvent, callback: HardwareCallback): () => void {
    const existing = this.listeners.get(event) ?? [];
    existing.push(callback);
    this.listeners.set(event, existing);

    return () => {
      const callbacks = this.listeners.get(event);
      if (callbacks) {
        this.listeners.set(
          event,
          callbacks.filter((cb) => cb !== callback),
        );
      }
    };
  }

  // -- Private detection methods --------------------------------------------

  private async detectGPU(): Promise<{ gpu: GPUInfo; vram: VRAMInfo }> {
    try {
      const info = await app.getGPUInfo('complete') as {
        gpuDevice?: Array<{ vendorId: number; deviceId: number; driverVersion?: string }>;
      };
      const devices = info?.gpuDevice ?? [];

      if (devices.length === 0) {
        return {
          gpu: { name: 'Unknown', vendor: 'unknown', driver: '', available: false },
          vram: { total: 0, available: 0, systemReserved: 0 },
        };
      }

      const primary = devices[0];
      const vendor = vendorFromId(primary.vendorId);
      const driver = primary.driverVersion ?? '';

      // For NVIDIA, try nvidia-smi for name and VRAM
      if (vendor === 'nvidia') {
        const smiResult = await queryNvidiaSmi();
        if (smiResult) {
          const totalBytes = smiResult.totalMB * 1024 * 1024;
          const availableBytes = smiResult.freeMB * 1024 * 1024;
          return {
            gpu: {
              name: smiResult.name,
              vendor,
              driver,
              available: true,
            },
            vram: {
              total: totalBytes,
              available: availableBytes,
              systemReserved: SYSTEM_RESERVED_VRAM,
            },
          };
        }
        // nvidia-smi failed — fall through to WMI fallback
      }

      // Fallback: Windows WMI (works for AMD, Intel, and NVIDIA without smi)
      const wmiResult = await queryWindowsVRAM().catch(() => null);
      if (wmiResult && wmiResult.totalBytes > 0) {
        const available = Math.max(0, wmiResult.totalBytes - SYSTEM_RESERVED_VRAM);
        return {
          gpu: { name: wmiResult.name, vendor, driver, available: true },
          vram: { total: wmiResult.totalBytes, available, systemReserved: SYSTEM_RESERVED_VRAM },
        };
      }

      // No VRAM detection method succeeded
      return {
        gpu: {
          name: vendor !== 'unknown' ? `${vendor.toUpperCase()} GPU` : 'Unknown GPU',
          vendor,
          driver,
          available: vendor !== 'unknown',
        },
        vram: { total: 0, available: 0, systemReserved: 0 },
      };
    } catch {
      return {
        gpu: { name: 'Unknown', vendor: 'unknown', driver: '', available: false },
        vram: { total: 0, available: 0, systemReserved: 0 },
      };
    }
  }

  private detectRAM(): RAMInfo {
    return {
      total: os.totalmem(),
      available: os.freemem(),
    };
  }

  private detectCPU(): CPUInfo {
    const cpuList = os.cpus();
    const model = cpuList[0]?.model ?? 'Unknown';
    const threads = cpuList.length;
    const cores = Math.max(1, Math.floor(threads / 2));
    return { model, cores, threads };
  }

  private async detectDisk(): Promise<DiskInfo> {
    const modelPath = app.getPath('userData');
    try {
      const stats = await statfs(modelPath);
      return {
        modelStoragePath: modelPath,
        totalSpace: stats.blocks * stats.bsize,
        freeSpace: stats.bavail * stats.bsize,
      };
    } catch {
      return {
        modelStoragePath: modelPath,
        totalSpace: 0,
        freeSpace: 0,
      };
    }
  }

  // -- Private event helpers ------------------------------------------------

  private emit(event: HardwareEvent, payload: HardwareProfile): void {
    const callbacks = this.listeners.get(event) ?? [];
    for (const cb of callbacks) {
      cb(payload);
    }
  }
}
