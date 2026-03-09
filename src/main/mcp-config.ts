/**
 * mcp-config.ts — MCP server configuration loader.
 *
 * Manages a JSON config file at {userData}/mcp-servers.json that defines
 * which MCP servers to connect to. Similar to Claude Desktop's config format.
 *
 * Users can add any MCP server (filesystem, GitHub, Postgres, etc.) by
 * editing the JSON file or through the settings UI.
 */

import { app } from 'electron';
import fs from 'fs/promises';
import path from 'path';

// ── Types ────────────────────────────────────────────────────────────

export interface MCPServerConfig {
  /** Unique identifier for this server (used as tool name prefix) */
  id: string;
  /** Human-readable label */
  label?: string;
  /** Command to spawn the server process */
  command: string;
  /** Arguments for the command */
  args: string[];
  /** Additional environment variables */
  env?: Record<string, string>;
  /** Whether this server is enabled */
  enabled: boolean;
}

export interface MCPConfigFile {
  version: string;
  servers: MCPServerConfig[];
}

// ── Default config ───────────────────────────────────────────────────

const DEFAULT_CONFIG: MCPConfigFile = {
  version: '1.0.0',
  servers: [
    {
      id: 'filesystem',
      label: 'Filesystem (MCP)',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', process.env.HOME || process.env.USERPROFILE || '.'],
      enabled: true,
    },
    {
      id: 'firecrawl',
      label: 'Firecrawl',
      command: 'npx',
      args: ['-y', 'firecrawl-mcp'],
      // getSanitizedEnv() strips all API keys from subprocess env for security.
      // We must explicitly pass FIRECRAWL_API_KEY through config.env.
      env: { FIRECRAWL_API_KEY: process.env.FIRECRAWL_API_KEY || '' },
      enabled: false, // Requires FIRECRAWL_API_KEY — enable via settings after adding key
    },
  ],
};

// ── Config loader ────────────────────────────────────────────────────

class MCPConfigManager {
  private configPath = '';
  private config: MCPConfigFile = { ...DEFAULT_CONFIG };
  private watchAbort: AbortController | null = null;

  async initialize(): Promise<MCPConfigFile> {
    this.configPath = path.join(app.getPath('userData'), 'mcp-servers.json');

    try {
      const data = await fs.readFile(this.configPath, 'utf-8');
      const parsed = JSON.parse(data) as MCPConfigFile;

      // Validate structure
      if (parsed.servers && Array.isArray(parsed.servers)) {
        this.config = parsed;
        console.log(`[MCPConfig] Loaded ${parsed.servers.length} server(s) from config`);
      } else {
        console.warn('[MCPConfig] Invalid config format — using defaults');
        await this.save();
      }
    } catch {
      // First run or corrupt file — write defaults
      console.log('[MCPConfig] No config found — creating default config');
      await this.save();
    }

    return this.config;
  }

  getConfig(): MCPConfigFile {
    return { ...this.config };
  }

  getEnabledServers(): MCPServerConfig[] {
    return this.config.servers.filter((s) => s.enabled);
  }

  async addServer(server: MCPServerConfig): Promise<void> {
    // Check for duplicate IDs
    const existing = this.config.servers.findIndex((s) => s.id === server.id);
    if (existing >= 0) {
      this.config.servers[existing] = server;
    } else {
      this.config.servers.push(server);
    }
    await this.save();
  }

  async removeServer(id: string): Promise<void> {
    this.config.servers = this.config.servers.filter((s) => s.id !== id);
    await this.save();
  }

  async setEnabled(id: string, enabled: boolean): Promise<void> {
    const server = this.config.servers.find((s) => s.id === id);
    if (server) {
      server.enabled = enabled;
      await this.save();
    }
  }

  /**
   * Watch config file for external changes (hot-reload).
   * Returns an abort controller to stop watching.
   */
  watch(onChange: (config: MCPConfigFile) => void): void {
    if (this.watchAbort) this.watchAbort.abort();
    this.watchAbort = new AbortController();

    const doWatch = async () => {
      try {
        const watcher = fs.watch(this.configPath, { signal: this.watchAbort!.signal });
        for await (const event of watcher) {
          if (event.eventType === 'change') {
            try {
              const data = await fs.readFile(this.configPath, 'utf-8');
              const parsed = JSON.parse(data) as MCPConfigFile;
              if (parsed.servers && Array.isArray(parsed.servers)) {
                this.config = parsed;
                console.log('[MCPConfig] Config file changed — reloaded');
                onChange(parsed);
              }
            } catch {
              // Ignore parse errors during write
            }
          }
        }
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          console.warn('[MCPConfig] File watch error:', err?.message);
        }
      }
    };

    doWatch();
  }

  stopWatching(): void {
    if (this.watchAbort) {
      this.watchAbort.abort();
      this.watchAbort = null;
    }
  }

  private async save(): Promise<void> {
    await fs.writeFile(this.configPath, JSON.stringify(this.config, null, 2), 'utf-8');
  }
}

export const mcpConfig = new MCPConfigManager();
