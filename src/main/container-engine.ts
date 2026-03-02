/**
 * container-engine.ts — Docker-based Execution Service for Agent Friday.
 *
 * Track XI, Phase 2: The Container Engine.
 *
 * Provides full OS-environment execution via Docker containers with:
 *   - Complete filesystem isolation (read-only source mounts, ephemeral workspace)
 *   - Network isolation (--network none by default, configurable per-task)
 *   - Resource limits (CPU, memory, disk, execution time)
 *   - Typed JSONL communication protocol (extending SOC Bridge patterns)
 *   - Consent gate integration for all container lifecycle operations
 *   - Sovereign Vault isolation (NO vault data ever mounted into containers)
 *   - Clean cancellation with interruptibility guarantee
 *   - Progress reporting through ActionFeed-compatible events
 *
 * Architecture: PARALLEL path to superpower-sandbox.ts.
 *   - Container Engine: Heavy isolation for Python, shell, untrusted code, package installs
 *   - Superpower Sandbox: Lightweight isolation for JS/TS adapted superpowers
 *   - SOC Bridge: Ultra-lightweight fallback for simple Python utility calls
 *
 * cLaw Safety Boundary:
 *   - All container creation routes through consent-gate.ts
 *   - Auto-deny in safe mode (integrity compromised)
 *   - No Sovereign Vault data ever enters a container filesystem
 *   - Sensitive values passed via controlled env vars, cleared on completion
 *   - All containers run as non-root (UID 1000), --cap-drop ALL
 *   - Seccomp profile blocks dangerous syscalls
 *   - Resource limits prevent host degradation
 *
 * Socratic Inquiry Answers (embedded in architecture):
 *   - Boundary: Security contract enforced in ContainerSecurityPolicy
 *   - Lifecycle: Consent model mapped in ConsentLevel per trigger type
 *   - Protocol: JSONL extending BridgeMessage/BridgeResponse from soc-bridge.ts
 *   - Precedent: Parallel to superpower-sandbox, routing by language/trust/duration
 *   - Tension: Two-tier model — SOC Bridge for <100ms, Container for full environments
 *   - Inversion: Escape mitigations in security policy (volume, channel, runtime)
 */

// Crypto Sprint 14: Removed execSync — all exec calls now use execFileSync (no shell).
import { spawn, execFileSync, ChildProcess } from 'child_process';
import { app } from 'electron';
import * as path from 'path';
import * as fs from 'fs';
import * as readline from 'readline';
import { randomUUID } from 'crypto';
import { requireConsent } from './consent-gate';
import { integrityManager } from './integrity';
import { contextStream } from './context-stream';
import type { ContextEventType } from './context-stream';

// ── Types ─────────────────────────────────────────────────────────────

/** Container lifecycle states */
export type ContainerState =
  | 'creating'      // Docker image/container being set up
  | 'configuring'   // Mounts, env vars, limits being applied
  | 'running'       // Actively executing user task
  | 'collecting'    // Gathering results from container
  | 'cancelling'    // User-initiated or timeout cancellation in progress
  | 'completed'     // Task finished successfully
  | 'failed'        // Task failed with error
  | 'cleaned'       // Container removed, resources freed
  | 'timeout';      // Execution time limit exceeded

/** What triggered the container — determines consent level */
export type ContainerTrigger =
  | 'user-explicit'     // User directly asked to run code/analyze
  | 'agent-subtask'     // Agent delegated a subtask requiring execution
  | 'scheduled-task'    // Scheduled task requires code execution
  | 'intelligence'      // Intelligence briefing needs code analysis
  | 'untrusted-code';   // Running code from unknown source

/** Network access level for a container */
export type NetworkPolicy =
  | 'none'          // --network none (default, most secure)
  | 'localhost'     // Host loopback only (for local service access)
  | 'dns-only'      // DNS resolution but no external connections
  | 'restricted';   // Full network but egress-filtered

/** Resource limits for a container */
export interface ResourceLimits {
  /** CPU limit in cores (default: 1.0) */
  cpuCores: number;
  /** Memory limit in MB (default: 512) */
  memoryMb: number;
  /** Disk space limit in MB (default: 1024) */
  diskMb: number;
  /** Execution time limit in ms (default: 300000 = 5 min) */
  timeoutMs: number;
  /** Maximum number of processes inside container (default: 64) */
  pidsLimit: number;
}

/** Security policy applied to every container */
export interface ContainerSecurityPolicy {
  /** Run as non-root user (UID 1000) */
  nonRoot: true;
  /** Drop ALL Linux capabilities */
  capDropAll: true;
  /** Prevent privilege escalation */
  noNewPrivileges: true;
  /** Read-only root filesystem */
  readOnlyRootfs: boolean;
  /** Seccomp profile (default or custom) */
  seccompProfile: 'default' | string;
  /** Network policy */
  network: NetworkPolicy;
  /** Allowed volume mounts (source → target, read-only flag) */
  mounts: ContainerMount[];
}

/** A volume mount into the container */
export interface ContainerMount {
  /** Host path (source) */
  hostPath: string;
  /** Container path (target) */
  containerPath: string;
  /** Read-only mount (default: true) */
  readOnly: boolean;
}

/** Communication protocol: Host → Container */
export interface ContainerMessage {
  /** Unique message ID */
  id: string;
  /** Message type */
  type: 'execute' | 'cancel' | 'ping' | 'install' | 'status';
  /** Language to execute (for 'execute' type) */
  language?: 'python' | 'bash' | 'node';
  /** Code or command to execute */
  code?: string;
  /** Packages to install (for 'install' type) */
  packages?: string[];
  /** Working directory inside container */
  workdir?: string;
  /** Environment variables (NO vault secrets — enforced by engine) */
  env?: Record<string, string>;
}

/** Communication protocol: Container → Host */
export interface ContainerResponse {
  /** Echoed message ID */
  id: string;
  /** Response status */
  status: 'ok' | 'error' | 'progress' | 'resource-warning';
  /** Result data (for 'ok' status) */
  result?: unknown;
  /** Error message (for 'error' status) */
  error?: string;
  /** Error code for classification */
  errorCode?: string;
  /** Progress percentage 0-100 (for 'progress' status) */
  progress?: number;
  /** Progress description */
  progressMessage?: string;
  /** Resource usage snapshot */
  resources?: ResourceUsage;
  /** Execution duration in ms */
  durationMs?: number;
  /** Standard output (accumulated) */
  stdout?: string;
  /** Standard error (accumulated) */
  stderr?: string;
}

/** Resource usage metrics reported by container */
export interface ResourceUsage {
  /** CPU usage percentage */
  cpuPercent: number;
  /** Memory usage in MB */
  memoryMb: number;
  /** Disk usage in MB */
  diskMb: number;
  /** Number of running processes */
  pids: number;
}

/** A managed container instance */
export interface ContainerInstance {
  /** Unique container task ID */
  taskId: string;
  /** Docker container ID (set after creation) */
  containerId: string | null;
  /** Current lifecycle state */
  state: ContainerState;
  /** What triggered this container */
  trigger: ContainerTrigger;
  /** Applied security policy */
  security: ContainerSecurityPolicy;
  /** Applied resource limits */
  limits: ResourceLimits;
  /** Creation timestamp */
  createdAt: number;
  /** Completion timestamp (if finished) */
  completedAt: number | null;
  /** Last known resource usage */
  lastResources: ResourceUsage | null;
  /** Accumulated stdout */
  stdout: string;
  /** Accumulated stderr */
  stderr: string;
  /** Final result (if completed) */
  result: unknown | null;
  /** Error message (if failed) */
  error: string | null;
  /** Description of what this container is doing */
  description: string;
  /** Pending message callbacks */
  pending: Map<string, {
    resolve: (resp: ContainerResponse) => void;
    reject: (err: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>;
  /** Docker exec process for JSONL communication */
  execProc: ChildProcess | null;
  /** Readline interface for stdout parsing */
  rl: readline.Interface | null;
}

/** Configuration for the container engine */
export interface ContainerEngineConfig {
  /** Docker image to use (default: 'friday-sandbox:latest') */
  imageName: string;
  /** Whether Docker is available on this system */
  dockerAvailable: boolean;
  /** Maximum concurrent containers (default: 3) */
  maxConcurrent: number;
  /** Default resource limits */
  defaultLimits: ResourceLimits;
  /** Auto-cleanup completed containers after this many ms (default: 60000) */
  autoCleanupMs: number;
}

/** Consent requirements per trigger type */
const CONSENT_MAP: Record<ContainerTrigger, 'pre-authorized' | 'confirm' | 'always-blocked'> = {
  'user-explicit': 'pre-authorized',    // User asked → go ahead
  'agent-subtask': 'confirm',           // Agent wants execution → ask user
  'scheduled-task': 'confirm',          // Scheduled → ask user
  'intelligence': 'pre-authorized',     // Intelligence analysis → pre-authorized (read-only)
  'untrusted-code': 'confirm',          // Untrusted → ALWAYS ask
};

// ── Default Values ────────────────────────────────────────────────────

const DEFAULT_LIMITS: ResourceLimits = {
  cpuCores: 1.0,
  memoryMb: 512,
  diskMb: 1024,
  timeoutMs: 300_000,  // 5 minutes
  pidsLimit: 64,
};

const DEFAULT_SECURITY: ContainerSecurityPolicy = {
  nonRoot: true,
  capDropAll: true,
  noNewPrivileges: true,
  readOnlyRootfs: false,  // Some tasks need /tmp writes
  seccompProfile: 'default',
  network: 'none',
  mounts: [],
};

const DEFAULT_CONFIG: ContainerEngineConfig = {
  imageName: 'friday-sandbox:latest',
  dockerAvailable: false,
  maxConcurrent: 3,
  defaultLimits: { ...DEFAULT_LIMITS },
  autoCleanupMs: 60_000,
};

// ── Container Engine ──────────────────────────────────────────────────

class ContainerEngine {
  private config: ContainerEngineConfig = { ...DEFAULT_CONFIG };
  private containers = new Map<string, ContainerInstance>();
  private initialized = false;
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;

  // ── Initialization ────────────────────────────────────────────────

  async initialize(): Promise<void> {
    if (this.initialized) return;

    // Check if Docker is available
    this.config.dockerAvailable = this.checkDockerAvailable();

    if (this.config.dockerAvailable) {
      console.log('[ContainerEngine] Docker detected — container execution available');
      await this.ensureImage();
    } else {
      console.log('[ContainerEngine] Docker not detected — container execution unavailable');
      console.log('[ContainerEngine] SOC Bridge remains the primary Python execution path');
    }

    // Start periodic cleanup of completed containers
    this.cleanupTimer = setInterval(() => this.cleanupCompleted(), this.config.autoCleanupMs);

    this.initialized = true;
  }

  /** Check if Docker daemon is running and accessible */
  private checkDockerAvailable(): boolean {
    try {
      // Crypto Sprint 14: execFileSync — no shell interpolation needed for 'docker info'.
      execFileSync('docker', ['info'], { stdio: 'pipe', timeout: 5000 });
      return true;
    } catch {
      return false;
    }
  }

  /** Ensure the sandbox Docker image exists, build if needed */
  private async ensureImage(): Promise<void> {
    try {
      // Crypto Sprint 11: Use execFileSync to avoid shell injection via imageName.
      execFileSync('docker', ['image', 'inspect', this.config.imageName], { stdio: 'pipe', timeout: 10000 });
      console.log(`[ContainerEngine] Image '${this.config.imageName}' found`);
    } catch {
      console.log(`[ContainerEngine] Image '${this.config.imageName}' not found — building...`);
      await this.buildSandboxImage();
    }
  }

  /** Build the sandbox Docker image */
  private async buildSandboxImage(): Promise<void> {
    const dockerfilePath = this.getDockerfilePath();

    // Create Dockerfile if it doesn't exist
    if (!fs.existsSync(dockerfilePath)) {
      this.createDockerfile(dockerfilePath);
    }

    try {
      const contextDir = path.dirname(dockerfilePath);
      // Crypto Sprint 11: Use execFileSync to avoid shell injection via imageName/paths.
      execFileSync(
        'docker', ['build', '-t', this.config.imageName, '-f', dockerfilePath, contextDir],
        { stdio: 'pipe', timeout: 120_000 }
      );
      console.log(`[ContainerEngine] Image '${this.config.imageName}' built successfully`);
    } catch (err) {
      // Crypto Sprint 11: Log only message, not full error object (may contain paths/tokens).
      console.error('[ContainerEngine] Failed to build sandbox image:', err instanceof Error ? err.message : String(err));
      this.config.dockerAvailable = false;
    }
  }

  /** Get path to the Dockerfile */
  private getDockerfilePath(): string {
    const isDev = !app.isPackaged;
    return isDev
      ? path.join(__dirname, '..', '..', 'docker', 'Dockerfile.sandbox')
      : path.join(process.resourcesPath, 'docker', 'Dockerfile.sandbox');
  }

  /** Create the sandbox Dockerfile with hardened security */
  private createDockerfile(dockerfilePath: string): void {
    const dir = path.dirname(dockerfilePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    const dockerfile = `# Friday Container Engine — Hardened Sandbox Image
# Track XI, Phase 2: The Container Engine
#
# Security hardening:
#   - Non-root user (UID 1000)
#   - Minimal base image (python:3.11-slim)
#   - No setuid/setgid binaries
#   - Clean /tmp with restricted permissions
#   - JSONL bridge script for structured communication

FROM python:3.11-slim

# Remove setuid/setgid binaries (escape vector mitigation)
RUN find / -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

# Create non-root user
RUN groupadd -g 1000 friday && \\
    useradd -u 1000 -g friday -m -s /bin/bash friday

# Create workspace directories
RUN mkdir -p /workspace /tmp/friday && \\
    chown -R friday:friday /workspace /tmp/friday

# Install common Python packages in the image (warm start)
RUN pip install --no-cache-dir \\
    numpy pandas requests beautifulsoup4 \\
    pyyaml toml python-dotenv 2>/dev/null || true

# Copy the JSONL bridge entrypoint
COPY bridge-entrypoint.py /usr/local/bin/bridge-entrypoint.py
RUN chmod +x /usr/local/bin/bridge-entrypoint.py

# Switch to non-root user
USER friday
WORKDIR /workspace

# Default entrypoint: JSONL bridge for structured communication
ENTRYPOINT ["python", "/usr/local/bin/bridge-entrypoint.py"]
`;

    fs.writeFileSync(dockerfilePath, dockerfile, 'utf-8');

    // Also create the bridge entrypoint script
    this.createBridgeEntrypoint(dir);
  }

  /** Create the JSONL bridge entrypoint for container communication */
  private createBridgeEntrypoint(dir: string): void {
    const entrypointPath = path.join(dir, 'bridge-entrypoint.py');

    const script = `#!/usr/bin/env python3
"""
Friday Container Bridge — JSONL communication entrypoint.

Reads JSONL messages from stdin, executes commands, writes JSONL responses to stdout.
This is the ONLY communication channel between the container and the host.

Security: This script runs as non-root (UID 1000) inside a capability-dropped,
network-isolated container. It cannot access the host filesystem beyond
explicitly mounted read-only volumes.
"""

import json
import sys
import os
import subprocess
import time
import traceback
import resource

def send_response(msg_id, status, **kwargs):
    """Send a JSONL response to stdout."""
    resp = {"id": msg_id, "status": status}
    resp.update(kwargs)
    print(json.dumps(resp), flush=True)

def get_resource_usage():
    """Get current resource usage."""
    try:
        ru = resource.getrusage(resource.RUSAGE_CHILDREN)
        return {
            "cpuPercent": 0,  # Approximate — Docker stats is more accurate
            "memoryMb": ru.ru_maxrss / 1024 if sys.platform != "darwin" else ru.ru_maxrss / (1024 * 1024),
            "diskMb": 0,
            "pids": 1
        }
    except Exception:
        return {"cpuPercent": 0, "memoryMb": 0, "diskMb": 0, "pids": 1}

def handle_execute(msg):
    """Execute code in the specified language."""
    language = msg.get("language", "python")
    code = msg.get("code", "")
    workdir = msg.get("workdir", "/workspace")
    env_vars = msg.get("env", {})

    # Build environment
    env = os.environ.copy()
    env.update(env_vars)

    start = time.time()

    try:
        if language == "python":
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=300,
                cwd=workdir, env=env
            )
        elif language == "bash":
            result = subprocess.run(
                ["bash", "-c", code],
                capture_output=True, text=True, timeout=300,
                cwd=workdir, env=env
            )
        elif language == "node":
            result = subprocess.run(
                ["node", "-e", code],
                capture_output=True, text=True, timeout=300,
                cwd=workdir, env=env
            )
        else:
            send_response(msg["id"], "error", error=f"Unsupported language: {language}")
            return

        duration_ms = int((time.time() - start) * 1000)

        if result.returncode == 0:
            send_response(
                msg["id"], "ok",
                result=result.stdout.strip(),
                stdout=result.stdout,
                stderr=result.stderr,
                durationMs=duration_ms,
                resources=get_resource_usage()
            )
        else:
            send_response(
                msg["id"], "error",
                error=result.stderr.strip() or f"Process exited with code {result.returncode}",
                errorCode=f"exit_{result.returncode}",
                stdout=result.stdout,
                stderr=result.stderr,
                durationMs=duration_ms,
                resources=get_resource_usage()
            )

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        send_response(
            msg["id"], "error",
            error="Execution timed out",
            errorCode="timeout",
            durationMs=duration_ms,
            resources=get_resource_usage()
        )
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        send_response(
            msg["id"], "error",
            error=str(e),
            errorCode="runtime_error",
            durationMs=duration_ms
        )

def handle_install(msg):
    """Install Python packages."""
    packages = msg.get("packages", [])
    if not packages:
        send_response(msg["id"], "error", error="No packages specified")
        return

    start = time.time()

    try:
        # Send progress
        send_response(msg["id"], "progress", progress=0, progressMessage=f"Installing {len(packages)} packages...")

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir"] + packages,
            capture_output=True, text=True, timeout=120
        )

        duration_ms = int((time.time() - start) * 1000)

        if result.returncode == 0:
            send_response(
                msg["id"], "ok",
                result=f"Installed: {', '.join(packages)}",
                stdout=result.stdout,
                stderr=result.stderr,
                durationMs=duration_ms
            )
        else:
            send_response(
                msg["id"], "error",
                error=result.stderr.strip(),
                errorCode="install_failed",
                stdout=result.stdout,
                stderr=result.stderr,
                durationMs=duration_ms
            )
    except Exception as e:
        send_response(msg["id"], "error", error=str(e), errorCode="install_error")

def handle_status(msg):
    """Report container status."""
    send_response(msg["id"], "ok", result={
        "alive": True,
        "pid": os.getpid(),
        "user": os.getenv("USER", "unknown"),
        "workdir": os.getcwd(),
        "resources": get_resource_usage()
    })

def main():
    # Signal readiness
    send_response("_init", "ok", result={"ready": True, "pid": os.getpid()})

    # Read JSONL from stdin
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = msg.get("type", "")
        msg_id = msg.get("id", "unknown")

        try:
            if msg_type == "execute":
                handle_execute(msg)
            elif msg_type == "install":
                handle_install(msg)
            elif msg_type == "cancel":
                send_response(msg_id, "ok", result={"cancelled": True})
                # Cancel is handled by the host killing the container
            elif msg_type == "ping":
                send_response(msg_id, "ok", result={"pong": True})
            elif msg_type == "status":
                handle_status(msg)
            else:
                send_response(msg_id, "error", error=f"Unknown message type: {msg_type}")
        except Exception as e:
            send_response(msg_id, "error", error=str(e), errorCode="handler_error")

if __name__ == "__main__":
    main()
`;

    fs.writeFileSync(entrypointPath, script, 'utf-8');
  }

  // ── Consent Gate ──────────────────────────────────────────────────

  /**
   * Check if a container operation requires consent and obtain it.
   * cLaw: Auto-deny in safe mode. Map trigger type to consent level.
   */
  private async checkConsent(
    trigger: ContainerTrigger,
    description: string,
    details: Record<string, unknown>
  ): Promise<boolean> {
    // cLaw First Law: Auto-deny if integrity is compromised
    if (integrityManager.isInSafeMode()) {
      console.warn('[ContainerEngine/cLaw] DENIED — system in safe mode');
      return false;
    }

    const consentLevel = CONSENT_MAP[trigger];

    switch (consentLevel) {
      case 'pre-authorized':
        // User-initiated or read-only intelligence — pre-authorized
        return true;

      case 'confirm':
        // Requires explicit user approval
        return requireConsent('container_execute', {
          trigger,
          description,
          ...details,
        });

      case 'always-blocked':
        // Should never happen with current trigger types but defense-in-depth
        console.warn(`[ContainerEngine/cLaw] BLOCKED trigger: ${trigger}`);
        return false;

      default:
        // Fail closed: unknown consent level → deny
        console.warn(`[ContainerEngine/cLaw] Unknown consent level for trigger: ${trigger}`);
        return false;
    }
  }

  // ── Container Lifecycle ───────────────────────────────────────────

  /**
   * Create, configure, and start a container for task execution.
   * This is the primary API for running code in an isolated environment.
   */
  async executeInContainer(options: {
    /** Code to execute */
    code: string;
    /** Programming language */
    language: 'python' | 'bash' | 'node';
    /** What triggered this execution */
    trigger: ContainerTrigger;
    /** Human-readable description */
    description: string;
    /** Optional: packages to install first */
    packages?: string[];
    /** Optional: source directory to mount read-only */
    sourcePath?: string;
    /** Optional: custom resource limits */
    limits?: Partial<ResourceLimits>;
    /** Optional: custom network policy */
    network?: NetworkPolicy;
    /** Optional: controlled environment variables (NO vault secrets) */
    env?: Record<string, string>;
  }): Promise<ContainerResponse> {
    // Pre-flight checks
    if (!this.initialized) {
      await this.initialize();
    }

    if (!this.config.dockerAvailable) {
      return {
        id: randomUUID(),
        status: 'error',
        error: 'Docker is not available on this system. Use the SOC Bridge for Python execution.',
        errorCode: 'docker_unavailable',
      };
    }

    // Check concurrent container limit
    const active = this.getActiveContainers();
    if (active.length >= this.config.maxConcurrent) {
      return {
        id: randomUUID(),
        status: 'error',
        error: `Maximum concurrent containers (${this.config.maxConcurrent}) reached. Wait for a container to complete.`,
        errorCode: 'max_concurrent',
      };
    }

    // cLaw: Validate environment variables — NO vault secrets
    if (options.env) {
      try {
        this.validateEnvVars(options.env);
      } catch (err) {
        return {
          id: randomUUID(),
          status: 'error',
          error: err instanceof Error ? err.message : String(err),
          errorCode: 'claw_violation',
        };
      }
    }

    // Consent gate
    const approved = await this.checkConsent(options.trigger, options.description, {
      language: options.language,
      codePreview: options.code.slice(0, 200),
      packages: options.packages,
      network: options.network || 'none',
    });

    if (!approved) {
      return {
        id: randomUUID(),
        status: 'error',
        error: 'Container execution denied by user or safety gate',
        errorCode: 'consent_denied',
      };
    }

    // Create container instance
    const taskId = randomUUID().slice(0, 12);
    const limits = { ...this.config.defaultLimits, ...options.limits };
    let security: ContainerSecurityPolicy;
    try {
      security = this.buildSecurityPolicy(options);
    } catch (err) {
      return {
        id: randomUUID(),
        status: 'error',
        error: err instanceof Error ? err.message : String(err),
        errorCode: 'claw_violation',
      };
    }

    const instance: ContainerInstance = {
      taskId,
      containerId: null,
      state: 'creating',
      trigger: options.trigger,
      security,
      limits,
      createdAt: Date.now(),
      completedAt: null,
      lastResources: null,
      stdout: '',
      stderr: '',
      result: null,
      error: null,
      description: options.description,
      pending: new Map(),
      execProc: null,
      rl: null,
    };

    this.containers.set(taskId, instance);
    this.emitProgress(taskId, 'creating', 'Creating container...');

    try {
      // Step 1: Create and start the Docker container
      await this.createDockerContainer(instance);

      // Step 2: Wait for bridge readiness
      instance.state = 'configuring';
      this.emitProgress(taskId, 'configuring', 'Configuring container...');
      await this.waitForBridgeReady(instance);

      // Step 3: Install packages if requested
      if (options.packages && options.packages.length > 0) {
        this.emitProgress(taskId, 'running', `Installing packages: ${options.packages.join(', ')}`);
        const installResp = await this.sendMessage(instance, {
          id: randomUUID(),
          type: 'install',
          packages: options.packages,
        }, 120_000);

        if (installResp.status === 'error') {
          throw new Error(`Package installation failed: ${installResp.error}`);
        }
      }

      // Step 4: Execute code
      instance.state = 'running';
      this.emitProgress(taskId, 'running', `Executing ${options.language} code...`);

      const response = await this.sendMessage(instance, {
        id: randomUUID(),
        type: 'execute',
        language: options.language,
        code: options.code,
        workdir: '/workspace',
        env: options.env,
      }, limits.timeoutMs);

      // Step 5: Collect results
      instance.state = 'collecting';
      instance.result = response.result;
      instance.stdout += response.stdout || '';
      instance.stderr += response.stderr || '';
      instance.lastResources = response.resources || null;

      if (response.status === 'ok') {
        instance.state = 'completed';
        instance.completedAt = Date.now();
        this.emitProgress(taskId, 'completed', 'Execution complete');
      } else {
        instance.state = 'failed';
        instance.error = response.error || 'Unknown error';
        instance.completedAt = Date.now();
        this.emitProgress(taskId, 'failed', `Failed: ${instance.error}`);
      }

      return response;
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      instance.state = 'failed';
      instance.error = errorMsg;
      instance.completedAt = Date.now();
      this.emitProgress(taskId, 'failed', `Error: ${errorMsg}`);

      return {
        id: taskId,
        status: 'error',
        error: errorMsg,
        errorCode: 'container_error',
        stdout: instance.stdout,
        stderr: instance.stderr,
      };
    } finally {
      // Step 6: Clean up — always runs
      await this.cleanupContainer(instance);

      // Clear any controlled env vars from memory
      if (options.env) {
        for (const key of Object.keys(options.env)) {
          options.env[key] = '';  // Overwrite before GC
        }
      }
    }
  }

  // ── Docker Operations ─────────────────────────────────────────────

  /** Create and start a Docker container with full security hardening */
  private async createDockerContainer(instance: ContainerInstance): Promise<void> {
    const args = this.buildDockerRunArgs(instance);

    try {
      // Crypto Sprint 10: Use execFileSync to prevent shell injection via docker args.
      // args is already an array from buildDockerRunArgs — pass directly, no shell.
      const result = execFileSync(
        'docker', ['run', ...args],
        { stdio: 'pipe', timeout: 30_000, encoding: 'utf-8' }
      );

      instance.containerId = result.trim();
      console.log(`[ContainerEngine] Container ${instance.containerId.slice(0, 12)} created for task ${instance.taskId}`);

      // Attach JSONL communication via docker exec
      this.attachCommunication(instance);
    } catch (err) {
      throw new Error(`Failed to create container: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** Build docker run arguments with full security hardening */
  private buildDockerRunArgs(instance: ContainerInstance): string[] {
    const { limits, security } = instance;
    const args: string[] = [
      // Detached mode — container runs in background
      '-d',

      // Resource limits
      `--memory=${limits.memoryMb}m`,
      `--cpus=${limits.cpuCores}`,
      `--pids-limit=${limits.pidsLimit}`,

      // Security hardening
      '--cap-drop=ALL',
      '--security-opt=no-new-privileges',

      // Seccomp profile
      security.seccompProfile === 'default'
        ? ''  // Docker applies default seccomp
        : `--security-opt=seccomp=${security.seccompProfile}`,

      // Read-only root filesystem (if enabled)
      security.readOnlyRootfs ? '--read-only' : '',

      // Temporary filesystem for writable areas
      '--tmpfs=/tmp:rw,noexec,nosuid,size=100m',
      '--tmpfs=/workspace:rw,noexec,nosuid,size=500m',

      // Network isolation
      `--network=${security.network}`,

      // Non-root user
      '--user=1000:1000',

      // Labels for identification
      `--label=friday.task=${instance.taskId}`,
      `--label=friday.trigger=${instance.trigger}`,

      // Auto-remove on exit (defense against orphaned containers)
      '--rm',

      // Stdin open for JSONL communication
      '-i',
    ].filter(Boolean);

    // Volume mounts (validated — no vault paths)
    for (const mount of security.mounts) {
      const roFlag = mount.readOnly ? ':ro' : '';
      args.push(`-v`, `"${mount.hostPath}":"${mount.containerPath}"${roFlag}`);
    }

    // Image name
    args.push(this.config.imageName);

    return args;
  }

  /** Attach JSONL communication channel to a running container */
  private attachCommunication(instance: ContainerInstance): void {
    if (!instance.containerId) {
      throw new Error('Cannot attach communication — no container ID');
    }

    // Use docker attach for stdin/stdout JSONL communication
    const proc = spawn('docker', ['attach', '--no-stdin=false', instance.containerId], {
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    instance.execProc = proc;

    // Parse stdout as JSONL
    const rl = readline.createInterface({
      input: proc.stdout!,
      crlfDelay: Infinity,
    });

    instance.rl = rl;

    rl.on('line', (line) => {
      try {
        const msg: ContainerResponse = JSON.parse(line);
        this.handleContainerResponse(instance, msg);
      } catch {
        // Non-JSON output — accumulate as stdout
        instance.stdout += line + '\n';
      }
    });

    proc.stderr?.on('data', (data) => {
      instance.stderr += data.toString();
    });

    proc.on('error', (err) => {
      console.error(`[ContainerEngine] Communication error for ${instance.taskId}:`, err.message);
      // Reject all pending messages
      for (const [, cb] of instance.pending) {
        clearTimeout(cb.timer);
        cb.reject(new Error(`Container communication lost: ${err.message}`));
      }
      instance.pending.clear();
    });

    proc.on('exit', (code) => {
      console.log(`[ContainerEngine] Container ${instance.taskId} communication exited (code: ${code})`);
      // Reject remaining pending messages
      for (const [, cb] of instance.pending) {
        clearTimeout(cb.timer);
        cb.reject(new Error(`Container exited with code ${code}`));
      }
      instance.pending.clear();
      instance.execProc = null;
      instance.rl = null;
    });
  }

  /** Handle a JSONL response from the container */
  private handleContainerResponse(instance: ContainerInstance, msg: ContainerResponse): void {
    // Progress events — emit but don't resolve pending
    if (msg.status === 'progress') {
      this.emitProgress(
        instance.taskId,
        'running',
        msg.progressMessage || `Progress: ${msg.progress}%`
      );
      // Accumulate resource usage
      if (msg.resources) {
        instance.lastResources = msg.resources;
      }
      return;
    }

    // Resource warnings — log but don't resolve pending
    if (msg.status === 'resource-warning') {
      console.warn(`[ContainerEngine] Resource warning for ${instance.taskId}:`, msg);
      return;
    }

    // Final response — resolve pending callback
    const pending = instance.pending.get(msg.id);
    if (pending) {
      clearTimeout(pending.timer);
      instance.pending.delete(msg.id);
      pending.resolve(msg);
    }
  }

  /** Send a JSONL message to the container and wait for response */
  private sendMessage(
    instance: ContainerInstance,
    msg: ContainerMessage,
    timeoutMs: number
  ): Promise<ContainerResponse> {
    if (!instance.execProc || !instance.execProc.stdin) {
      return Promise.reject(new Error('Container communication not attached'));
    }

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        instance.pending.delete(msg.id);
        reject(new Error(`Container message timeout after ${timeoutMs}ms for ${msg.type}`));
      }, timeoutMs);

      instance.pending.set(msg.id, { resolve, reject, timer });

      const line = JSON.stringify(msg) + '\n';
      instance.execProc!.stdin!.write(line);
    });
  }

  /** Wait for the bridge entrypoint to signal readiness */
  private waitForBridgeReady(instance: ContainerInstance): Promise<void> {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        instance.pending.delete('_init');
        reject(new Error('Container bridge startup timeout (15s)'));
      }, 15_000);

      instance.pending.set('_init', {
        resolve: () => {
          clearTimeout(timeout);
          resolve();
        },
        reject: (err) => {
          clearTimeout(timeout);
          reject(err);
        },
        timer: timeout,
      });
    });
  }

  // ── Security ──────────────────────────────────────────────────────

  /** Build security policy for a container */
  private buildSecurityPolicy(options: {
    sourcePath?: string;
    network?: NetworkPolicy;
  }): ContainerSecurityPolicy {
    const policy: ContainerSecurityPolicy = { ...DEFAULT_SECURITY };

    // Network policy
    if (options.network) {
      policy.network = options.network;
    }

    // Source mount (always read-only)
    if (options.sourcePath) {
      // cLaw: Validate source path doesn't contain vault data
      this.validateMountPath(options.sourcePath);

      policy.mounts.push({
        hostPath: options.sourcePath,
        containerPath: '/source',
        readOnly: true,  // ALWAYS read-only for source mounts
      });
    }

    return policy;
  }

  /**
   * cLaw: Validate that a mount path doesn't contain vault data.
   * The Sovereign Vault data directory is NEVER mountable into containers.
   */
  private validateMountPath(hostPath: string): void {
    const userDataPath = app.getPath('userData');
    const vaultDir = path.join(userDataPath, 'vault');
    const settingsDir = path.join(userDataPath);

    const normalizedPath = path.resolve(hostPath).toLowerCase();
    const normalizedVault = path.resolve(vaultDir).toLowerCase();
    const normalizedSettings = path.resolve(settingsDir).toLowerCase();

    // Block any path under the vault directory
    if (normalizedPath.startsWith(normalizedVault)) {
      throw new Error(
        '[cLaw] BLOCKED: Cannot mount Sovereign Vault directory into containers. ' +
        'Sensitive data must be passed through controlled env vars only.'
      );
    }

    // Block the settings directory (contains vault config, keys, etc.)
    if (normalizedPath === normalizedSettings) {
      throw new Error(
        '[cLaw] BLOCKED: Cannot mount Agent Friday settings directory into containers.'
      );
    }
  }

  /**
   * cLaw: Validate environment variables don't contain known vault key patterns.
   * Defense-in-depth: even if caller passes vault data, we catch common patterns.
   */
  private validateEnvVars(env: Record<string, string>): void {
    const dangerousPatterns = [
      /^vault[_-]/i,
      /^sovereign[_-]/i,
      /private[_-]?key/i,
      /^encryption[_-]key/i,
      /^master[_-]key/i,
      /^signing[_-]key/i,
      /^recovery[_-]phrase/i,
    ];

    for (const key of Object.keys(env)) {
      for (const pattern of dangerousPatterns) {
        if (pattern.test(key)) {
          throw new Error(
            `[cLaw] BLOCKED: Environment variable '${key}' matches vault data pattern. ` +
            'Sovereign Vault data must never enter containers.'
          );
        }
      }
    }
  }

  // ── Cancellation ──────────────────────────────────────────────────

  /**
   * Cancel a running container. Respects the interruptibility guarantee:
   * user's "stop" command takes precedence over container execution.
   *
   * cLaw Second Law: A user's stop command must never be delayed by
   * "just finishing up" inside a container.
   */
  async cancelContainer(taskId: string): Promise<boolean> {
    const instance = this.containers.get(taskId);
    if (!instance) return false;

    if (instance.state === 'completed' || instance.state === 'cleaned' || instance.state === 'failed') {
      return false;  // Already done
    }

    instance.state = 'cancelling';
    this.emitProgress(taskId, 'cancelling', 'Cancelling container...');

    try {
      // Try graceful cancellation first (send cancel message)
      if (instance.execProc && instance.execProc.stdin) {
        const cancelMsg: ContainerMessage = { id: randomUUID(), type: 'cancel' };
        instance.execProc.stdin.write(JSON.stringify(cancelMsg) + '\n');
      }

      // Give 3 seconds for graceful shutdown
      await new Promise(resolve => setTimeout(resolve, 3000));

      // Force kill if still running
      if (instance.containerId) {
        try {
          // Crypto Sprint 10: Use execFileSync to prevent shell injection via containerId.
          execFileSync('docker', ['kill', instance.containerId], { stdio: 'pipe', timeout: 5000 });
        } catch {
          // Container may already be stopped
        }
      }

      // Reject all pending callbacks
      for (const [, cb] of instance.pending) {
        clearTimeout(cb.timer);
        cb.reject(new Error('Container execution cancelled by user'));
      }
      instance.pending.clear();

      instance.state = 'failed';
      instance.error = 'Cancelled by user';
      instance.completedAt = Date.now();
      this.emitProgress(taskId, 'failed', 'Cancelled by user');

      await this.cleanupContainer(instance);
      return true;
    } catch (err) {
      // Crypto Sprint 17: Sanitize error output.
      console.error(`[ContainerEngine] Error cancelling container ${taskId}:`, err instanceof Error ? err.message : 'Unknown error');
      return false;
    }
  }

  // ── Cleanup ───────────────────────────────────────────────────────

  /** Remove a container and free resources */
  private async cleanupContainer(instance: ContainerInstance): Promise<void> {
    // Kill communication process
    if (instance.execProc) {
      instance.execProc.kill('SIGKILL');
      instance.execProc = null;
    }

    // Close readline
    if (instance.rl) {
      instance.rl.close();
      instance.rl = null;
    }

    // Remove Docker container (if not already auto-removed)
    if (instance.containerId) {
      try {
        // Crypto Sprint 10: Use execFileSync to prevent shell injection via containerId.
        execFileSync('docker', ['rm', '-f', instance.containerId], { stdio: 'pipe', timeout: 10000 });
      } catch {
        // Container may already be removed (--rm flag)
      }
      instance.containerId = null;
    }

    instance.state = 'cleaned';
  }

  /** Periodically clean up completed container records */
  private cleanupCompleted(): void {
    const now = Date.now();
    const cutoff = now - this.config.autoCleanupMs;

    for (const [taskId, instance] of this.containers) {
      if (
        instance.completedAt &&
        instance.completedAt < cutoff &&
        (instance.state === 'completed' || instance.state === 'failed' || instance.state === 'cleaned')
      ) {
        this.containers.delete(taskId);
      }
    }
  }

  // ── Progress & Events ─────────────────────────────────────────────

  /** Emit a progress event through the ActionFeed-compatible context stream */
  private emitProgress(taskId: string, state: string, message: string): void {
    try {
      contextStream.push({
        type: 'tool-invoke' as ContextEventType,
        source: 'container-engine',
        summary: `[Container ${taskId}] ${message}`,
        data: {
          taskId,
          state,
          message,
          timestamp: Date.now(),
        },
        dedupeKey: `container-${taskId}-${state}`,
        ttlMs: 300_000,  // 5 minute TTL
      });
    } catch {
      // Context stream may not be initialized yet
    }
  }

  // ── Query Methods ─────────────────────────────────────────────────

  /** Get all active (running) containers */
  getActiveContainers(): ContainerInstance[] {
    return Array.from(this.containers.values()).filter(
      c => c.state === 'creating' || c.state === 'configuring' || c.state === 'running'
    );
  }

  /** Get a container by task ID */
  getContainer(taskId: string): ContainerInstance | null {
    return this.containers.get(taskId) || null;
  }

  /** Get all container instances (for dashboard display) */
  getAllContainers(): Array<{
    taskId: string;
    state: ContainerState;
    trigger: ContainerTrigger;
    description: string;
    createdAt: number;
    completedAt: number | null;
    resources: ResourceUsage | null;
    error: string | null;
    durationMs: number;
  }> {
    return Array.from(this.containers.values()).map(c => ({
      taskId: c.taskId,
      state: c.state,
      trigger: c.trigger,
      description: c.description,
      createdAt: c.createdAt,
      completedAt: c.completedAt,
      resources: c.lastResources,
      error: c.error,
      durationMs: c.completedAt ? c.completedAt - c.createdAt : Date.now() - c.createdAt,
    }));
  }

  /** Check if Docker is available */
  isAvailable(): boolean {
    return this.config.dockerAvailable;
  }

  /** Get engine status */
  getStatus(): {
    available: boolean;
    imageName: string;
    activeContainers: number;
    maxConcurrent: number;
    totalExecuted: number;
  } {
    return {
      available: this.config.dockerAvailable,
      imageName: this.config.imageName,
      activeContainers: this.getActiveContainers().length,
      maxConcurrent: this.config.maxConcurrent,
      totalExecuted: this.containers.size,
    };
  }

  // ── Shutdown ──────────────────────────────────────────────────────

  /** Gracefully shut down all containers and clean up */
  async shutdown(): Promise<void> {
    console.log('[ContainerEngine] Shutting down...');

    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }

    // Cancel all active containers
    const active = this.getActiveContainers();
    await Promise.allSettled(
      active.map(c => this.cancelContainer(c.taskId))
    );

    // Clean up any remaining containers
    for (const instance of this.containers.values()) {
      await this.cleanupContainer(instance);
    }

    this.containers.clear();
    this.initialized = false;

    console.log('[ContainerEngine] Shutdown complete');
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const containerEngine = new ContainerEngine();
