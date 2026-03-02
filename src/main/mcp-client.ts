/**
 * mcp-client.ts — Multi-server MCP Manager.
 *
 * Manages connections to multiple MCP servers defined in mcp-servers.json.
 * Each server gets its own StdioClientTransport + Client instance.
 * Tools are namespaced with server ID prefixes to avoid collisions.
 *
 * Backward-compatible: the exported `mcpClient` object has the same
 * connect/disconnect/listTools/callTool interface as the original single-server client.
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { mcpConfig, MCPServerConfig } from './mcp-config';
import { getSanitizedEnv } from './settings';

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Crypto Sprint 14: Redact API keys from MCP tool arg/result log entries.
 * MCP servers often receive API keys as arguments; logging them is a leak vector.
 */
function redactKeys(str: string): string {
  return str
    .replace(/(?:AIza|sk-|sk_|ant-|fc-|pplx-|ya29\.|xox[bpsa]-|key[=:])[A-Za-z0-9_.-]{15,}/g, '[REDACTED]')
    .replace(/(?:Bearer\s+)[A-Za-z0-9_.-]{20,}/g, 'Bearer [REDACTED]')
    .replace(/bot[0-9]{8,}:[A-Za-z0-9_-]{30,}/gi, '[REDACTED-BOT-TOKEN]');
}

// ── Types ────────────────────────────────────────────────────────────

export interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: unknown;
  /** Which server this tool belongs to */
  serverId?: string;
}

interface ServerConnection {
  config: MCPServerConfig;
  client: Client;
  transport: StdioClientTransport;
  connected: boolean;
  tools: MCPTool[];
  error?: string;
}

export interface MCPServerStatus {
  id: string;
  label: string;
  connected: boolean;
  toolCount: number;
  error?: string;
}

// ── Multi-Server Manager ─────────────────────────────────────────────

class MCPManager {
  private servers: Map<string, ServerConnection> = new Map();
  private toolToServer: Map<string, string> = new Map(); // toolName → serverId
  private initialized = false;

  /**
   * Initialize: load config, connect to all enabled servers.
   * Replaces the old single-server connect() method.
   */
  async connect(): Promise<void> {
    if (this.initialized) return;

    const config = await mcpConfig.initialize();
    const enabledServers = config.servers.filter((s) => s.enabled);

    console.log(`[MCP] Connecting to ${enabledServers.length} server(s)...`);

    // Connect to all servers in parallel — failures are isolated
    const results = await Promise.allSettled(
      enabledServers.map((s) => this.connectServer(s))
    );

    // Log results
    for (let i = 0; i < results.length; i++) {
      const server = enabledServers[i];
      if (results[i].status === 'fulfilled') {
        const conn = this.servers.get(server.id);
        console.log(`[MCP] ✓ ${server.label || server.id} — ${conn?.tools.length || 0} tools`);
      } else {
        const err = (results[i] as PromiseRejectedResult).reason;
        console.warn(`[MCP] ✗ ${server.label || server.id} — ${err?.message || err}`);
      }
    }

    this.initialized = true;

    // Watch for config changes (hot-reload)
    mcpConfig.watch(async (newConfig) => {
      await this.handleConfigChange(newConfig.servers);
    });
  }

  /**
   * Connect a single MCP server.
   */
  private async connectServer(config: MCPServerConfig): Promise<void> {
    // Disconnect existing connection if any
    if (this.servers.has(config.id)) {
      await this.disconnectServer(config.id);
    }

    // Crypto Sprint 6 (CRITICAL): Use getSanitizedEnv() instead of process.env to prevent
    // leaking ALL API keys to third-party MCP server subprocesses. Each MCP server only
    // receives its own config.env overrides, not the full set of application secrets.
    const spawnEnv: Record<string, string> = getSanitizedEnv() as Record<string, string>;
    if (config.env) {
      for (const [key, value] of Object.entries(config.env)) {
        if (value) spawnEnv[key] = value; // Only override if config has a non-empty value
      }
    }

    const transport = new StdioClientTransport({
      command: config.command,
      args: config.args,
      env: spawnEnv,
    });

    const client = new Client(
      { name: 'agent-friday', version: '1.0.0' },
      { capabilities: {} }
    );

    try {
      await client.connect(transport);

      // Discover tools
      const result = await client.listTools();
      const tools: MCPTool[] = (result.tools || []).map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
        serverId: config.id,
      }));

      // Register connection
      this.servers.set(config.id, {
        config,
        client,
        transport,
        connected: true,
        tools,
      });

      // Build tool routing (no prefix for single server, prefix for multi)
      for (const tool of tools) {
        this.toolToServer.set(tool.name, config.id);
      }
    } catch (err: any) {
      // Store the failed connection so we can report status
      this.servers.set(config.id, {
        config,
        client,
        transport,
        connected: false,
        tools: [],
        error: err?.message || String(err),
      });
      throw err;
    }
  }

  /**
   * Disconnect a single server.
   */
  private async disconnectServer(id: string): Promise<void> {
    const conn = this.servers.get(id);
    if (!conn) return;

    // Remove tool routing entries
    for (const tool of conn.tools) {
      this.toolToServer.delete(tool.name);
    }

    // Close connection
    try {
      if (conn.connected) {
        await conn.client.close();
      }
    } catch {
      // Ignore close errors
    }

    this.servers.delete(id);
  }

  /**
   * Disconnect all servers.
   */
  async disconnect(): Promise<void> {
    mcpConfig.stopWatching();
    const ids = Array.from(this.servers.keys());
    await Promise.allSettled(ids.map((id) => this.disconnectServer(id)));
    this.initialized = false;
    console.log('[MCP] All servers disconnected');
  }

  /**
   * Handle config file changes — connect new servers, disconnect removed ones.
   */
  private async handleConfigChange(newServers: MCPServerConfig[]): Promise<void> {
    const currentIds = new Set(this.servers.keys());
    const newEnabled = newServers.filter((s) => s.enabled);
    const newIds = new Set(newEnabled.map((s) => s.id));

    // Disconnect removed servers
    for (const id of currentIds) {
      if (!newIds.has(id)) {
        console.log(`[MCP] Server ${id} removed from config — disconnecting`);
        await this.disconnectServer(id);
      }
    }

    // Connect new servers
    for (const server of newEnabled) {
      if (!currentIds.has(server.id)) {
        console.log(`[MCP] New server ${server.id} found in config — connecting`);
        try {
          await this.connectServer(server);
          const conn = this.servers.get(server.id);
          console.log(`[MCP] ✓ ${server.label || server.id} — ${conn?.tools.length || 0} tools`);
        } catch (err: any) {
          console.warn(`[MCP] ✗ ${server.label || server.id} — ${err?.message}`);
        }
      }
    }
  }

  // ── Public API (backward-compatible) ───────────────────────────────

  /**
   * List all tools from all connected servers.
   */
  async listTools(): Promise<MCPTool[]> {
    const allTools: MCPTool[] = [];
    for (const conn of this.servers.values()) {
      if (conn.connected) {
        allTools.push(...conn.tools);
      }
    }
    return allTools;
  }

  /**
   * Call a tool by name. Routes to the correct server.
   */
  async callTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    const serverId = this.toolToServer.get(name);
    if (!serverId) {
      throw new Error(`MCP tool not found: ${name}`);
    }

    const conn = this.servers.get(serverId);
    if (!conn || !conn.connected) {
      throw new Error(`MCP server ${serverId} not connected`);
    }

    // Crypto Sprint 14: Redact API keys before logging tool arguments and results.
    console.log(`[MCP] Calling ${serverId}::${name}`, redactKeys(JSON.stringify(args).slice(0, 200)));
    const result = await conn.client.callTool({ name, arguments: args });
    console.log(`[MCP] Result for ${name}:`, redactKeys(JSON.stringify(result).slice(0, 200)));
    return result.content;
  }

  /**
   * Check if any server is connected.
   */
  isConnected(): boolean {
    for (const conn of this.servers.values()) {
      if (conn.connected) return true;
    }
    return false;
  }

  /**
   * Get status of all servers.
   */
  getStatus(): MCPServerStatus[] {
    return Array.from(this.servers.values()).map((conn) => ({
      id: conn.config.id,
      label: conn.config.label || conn.config.id,
      connected: conn.connected,
      toolCount: conn.tools.length,
      error: conn.error,
    }));
  }

  /**
   * Add a new server dynamically and connect to it.
   */
  async addServer(config: MCPServerConfig): Promise<void> {
    await mcpConfig.addServer(config);
    if (config.enabled) {
      try {
        await this.connectServer(config);
        console.log(`[MCP] ✓ Added and connected: ${config.label || config.id}`);
      } catch (err: any) {
        console.warn(`[MCP] Added but failed to connect: ${config.label || config.id} — ${err?.message}`);
      }
    }
  }
}

// ── Singleton export (backward-compatible) ───────────────────────────

export const mcpClient = new MCPManager();
