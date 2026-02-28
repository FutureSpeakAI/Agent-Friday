/**
 * Connector Registry — Central hub for all software connectors.
 *
 * Architecture: Hub-and-spoke model where each connector module:
 *  1. detect()   — checks if the app/tool is installed
 *  2. TOOLS      — declares function tools for Gemini
 *  3. execute()  — routes tool calls to native APIs
 *
 * The registry auto-discovers installed apps on startup, collects tools
 * from available connectors, and routes tool calls at runtime.
 *
 * Only tools for INSTALLED software are included in Gemini sessions,
 * keeping the function-calling surface lean and relevant.
 */

// ── Connector interface ──────────────────────────────────────────────

export interface ToolDeclaration {
  name: string;
  description: string;
  parameters: {
    type: string;
    properties: Record<string, unknown>;
    required?: string[];
  };
}

export interface ToolResult {
  result?: string;
  error?: string;
}

/** Category for built-in connectors — grouping in UI and personality routing */
export type ConnectorCategory = 'foundation' | 'creative' | 'office' | 'devops' | 'communication' | 'system';

export interface Connector {
  /** Unique module ID */
  id: string;
  /** Human-readable name for personality prompt */
  label: string;
  /** Category for grouping in UI and personality routing */
  category: ConnectorCategory;
  /** Short description of what this connector enables */
  description: string;
  /** Tool declarations from the module */
  tools: ToolDeclaration[];
  /** Execute a tool call */
  execute: (toolName: string, args: Record<string, unknown>) => Promise<ToolResult>;
  /** Whether the connector's app was detected as available */
  available: boolean;
}

// ── Registry singleton ───────────────────────────────────────────────

class ConnectorRegistry {
  private connectors: Map<string, Connector> = new Map();
  private toolToConnector: Map<string, string> = new Map();  // toolName → connectorId
  private initialized = false;

  /**
   * Initialize the registry: import all connector modules, run detection,
   * and build the tool routing table.
   */
  async initialize(): Promise<void> {
    if (this.initialized) return;

    console.log('[ConnectorRegistry] Initializing — scanning for available software...');
    const startTime = Date.now();

    // Import all connector modules dynamically
    // Each module exports: TOOLS, execute, detect
    const modules = await this.loadModules();

    // Run detection in parallel for speed
    const detections = await Promise.allSettled(
      modules.map(async (mod) => {
        try {
          const available = await mod.detect();
          return { ...mod, available };
        } catch (err) {
          console.warn(`[ConnectorRegistry] Detection failed for ${mod.id}:`, err);
          return { ...mod, available: false };
        }
      })
    );

    // Register all connectors (both available and unavailable for status reporting)
    for (const result of detections) {
      if (result.status === 'fulfilled') {
        const conn = result.value;
        this.connectors.set(conn.id, conn);

        // Only build tool routing for available connectors
        if (conn.available) {
          for (const tool of conn.tools) {
            this.toolToConnector.set(tool.name, conn.id);
          }
          console.log(`[ConnectorRegistry] ✓ ${conn.label} — ${conn.tools.length} tools`);
        } else {
          console.log(`[ConnectorRegistry] ✗ ${conn.label} — not detected`);
        }
      }
    }

    this.initialized = true;
    const elapsed = Date.now() - startTime;
    const available = this.getAvailableConnectors();
    const totalTools = available.reduce((sum, c) => sum + c.tools.length, 0);
    console.log(`[ConnectorRegistry] Ready — ${available.length} connectors, ${totalTools} tools (${elapsed}ms)`);
  }

  /**
   * Load all connector modules.
   * Each module is loaded safely — if a module fails to import, it's skipped.
   */
  private async loadModules(): Promise<Array<{
    id: string;
    label: string;
    category: Connector['category'];
    description: string;
    tools: ToolDeclaration[];
    execute: Connector['execute'];
    detect: () => Promise<boolean>;
  }>> {
    const moduleDefinitions: Array<{
      id: string;
      label: string;
      category: Connector['category'];
      description: string;
      importPath: string;
    }> = [
      // Tier 1 — Foundation
      {
        id: 'powershell',
        label: 'PowerShell Bridge',
        category: 'foundation',
        description: 'COM automation, registry, WMI, services, arbitrary Windows control',
        importPath: './powershell',
      },
      {
        id: 'terminal-sessions',
        label: 'Terminal Sessions',
        category: 'foundation',
        description: 'Persistent shells, build watchers, REPL sessions, process management',
        importPath: './terminal-sessions',
      },
      {
        id: 'vscode',
        label: 'VS Code Bridge',
        category: 'devops',
        description: 'File editing, terminal, extensions, debugging, workspace management',
        importPath: './vscode',
      },
      {
        id: 'git-devops',
        label: 'Git & DevOps',
        category: 'devops',
        description: 'Git workflows, Docker, npm/yarn/pnpm, cloud CLIs (AWS/Azure/GCP)',
        importPath: './git-devops',
      },
      // Tier 2 — Creative & Office
      {
        id: 'office',
        label: 'Office Automation',
        category: 'office',
        description: 'Word, Excel, PowerPoint creation/editing via COM automation',
        importPath: './office',
      },
      {
        id: 'adobe',
        label: 'Adobe Creative Suite',
        category: 'creative',
        description: 'Photoshop, Illustrator, Premiere via ExtendScript/UXP',
        importPath: './adobe',
      },
      {
        id: 'creative-3d',
        label: '3D & VFX',
        category: 'creative',
        description: 'Blender Python scripting, Unity/Unreal editor commands',
        importPath: './creative-3d',
      },
      {
        id: 'media-streaming',
        label: 'Media & Streaming',
        category: 'creative',
        description: 'OBS WebSocket, audio device routing, FFmpeg pipelines',
        importPath: './media-streaming',
      },
      // Tier 3 — Communication & AI
      {
        id: 'comms-hub',
        label: 'Communication Hub',
        category: 'communication',
        description: 'Slack, Discord, Teams webhooks; SMTP email',
        importPath: './comms-hub',
      },
      {
        id: 'dev-environments',
        label: 'Dev Environments',
        category: 'devops',
        description: 'Jupyter, Python venvs, conda, Docker Compose, databases',
        importPath: './dev-environments',
      },
      // Tier 4 — Universal
      {
        id: 'ui-automation',
        label: 'UI Automation',
        category: 'system',
        description: 'Windows UI Automation API — control ANY app via accessibility tree',
        importPath: './ui-automation',
      },
      {
        id: 'system-management',
        label: 'System Management',
        category: 'system',
        description: 'Windows services, scheduled tasks, network, firewall, package managers',
        importPath: './system-management',
      },
      // Tier 5 — Intelligence & Monitoring
      {
        id: 'world-monitor',
        label: 'World Monitor Intelligence',
        category: 'system',
        description: 'Real-time global intelligence: conflicts, markets, military, cyber, climate, earthquakes, shipping, and 17 more domains with 44 API endpoints',
        importPath: './world-monitor',
      },
      // Tier 6 — Web Intelligence
      {
        id: 'firecrawl',
        label: 'Firecrawl Web Intelligence',
        category: 'system',
        description: 'Web search, page scraping, and site crawling via Firecrawl API',
        importPath: './firecrawl',
      },
      // Tier 7 — Messaging Gateway
      {
        id: 'messaging-gateway',
        label: 'Messaging Gateway',
        category: 'communication',
        description: 'Send and receive messages via Telegram, Discord, and other channels',
        importPath: '../gateway/gateway-connector',
      },
      // Tier 8 — AI-Powered Search & Research
      {
        id: 'perplexity',
        label: 'Perplexity AI Search',
        category: 'system',
        description: 'AI-powered web search, deep research, and search-augmented reasoning via Perplexity (Sonar, Sonar Pro, Deep Research, Reasoning Pro)',
        importPath: './perplexity',
      },
      // Tier 9 — OpenAI Specialist Services
      {
        id: 'openai-services',
        label: 'OpenAI Services',
        category: 'creative',
        description: 'Image generation (DALL-E 3), deep reasoning (o3), audio transcription (Whisper), and semantic embeddings',
        importPath: './openai-services',
      },
      // Tier 10 — Document Intelligence (PageIndex)
      {
        id: 'pageindex',
        label: 'PageIndex Document Intelligence',
        category: 'system',
        description: 'Vectorless reasoning-based RAG — index PDFs into hierarchical trees and answer questions with ~99% accuracy (PageIndex by Vectify AI)',
        importPath: './pageindex',
      },
    ];

    const loaded: Array<{
      id: string;
      label: string;
      category: Connector['category'];
      description: string;
      tools: ToolDeclaration[];
      execute: Connector['execute'];
      detect: () => Promise<boolean>;
    }> = [];

    for (const def of moduleDefinitions) {
      try {
        // Dynamic import — if module file doesn't exist, it throws and we skip
        const mod = require(def.importPath);
        if (mod.TOOLS && typeof mod.execute === 'function' && typeof mod.detect === 'function') {
          loaded.push({
            id: def.id,
            label: def.label,
            category: def.category,
            description: def.description,
            tools: mod.TOOLS,
            execute: mod.execute,
            detect: mod.detect,
          });
        } else {
          console.warn(`[ConnectorRegistry] Module ${def.id} missing required exports (TOOLS, execute, detect)`);
        }
      } catch (err: any) {
        // Module file doesn't exist yet — that's expected for Wave 2 modules
        if (err?.code === 'MODULE_NOT_FOUND') {
          console.log(`[ConnectorRegistry] Module ${def.id} not yet implemented — skipping`);
        } else {
          console.warn(`[ConnectorRegistry] Failed to load ${def.id}:`, err?.message || err);
        }
      }
    }

    return loaded;
  }

  // ── Public API ────────────────────────────────────────────────────

  /**
   * Get all tool declarations for available connectors.
   * These are sent to Gemini as function declarations.
   */
  getAllTools(): ToolDeclaration[] {
    const tools: ToolDeclaration[] = [];
    for (const conn of this.connectors.values()) {
      if (conn.available) {
        tools.push(...conn.tools);
      }
    }
    return tools;
  }

  /**
   * Execute a tool call by name. Routes to the correct connector.
   */
  async executeTool(toolName: string, args: Record<string, unknown>): Promise<ToolResult> {
    const connectorId = this.toolToConnector.get(toolName);
    if (!connectorId) {
      return { error: `Unknown connector tool: ${toolName}` };
    }
    const connector = this.connectors.get(connectorId);
    if (!connector) {
      return { error: `Connector ${connectorId} not found` };
    }
    if (!connector.available) {
      return { error: `${connector.label} is not available on this system` };
    }

    try {
      return await connector.execute(toolName, args);
    } catch (err: any) {
      return { error: `${connector.label} error: ${err?.message || String(err)}` };
    }
  }

  /**
   * Check if a tool name belongs to the connector system.
   */
  isConnectorTool(toolName: string): boolean {
    return this.toolToConnector.has(toolName);
  }

  /**
   * Get all available (detected) connectors.
   */
  getAvailableConnectors(): Connector[] {
    return Array.from(this.connectors.values()).filter((c) => c.available);
  }

  /**
   * Get all connectors (including unavailable).
   */
  getAllConnectors(): Connector[] {
    return Array.from(this.connectors.values());
  }

  /**
   * Get a status summary for display.
   */
  getStatus(): {
    initialized: boolean;
    totalConnectors: number;
    availableConnectors: number;
    totalTools: number;
    connectors: Array<{ id: string; label: string; category: string; available: boolean; toolCount: number }>;
  } {
    const all = this.getAllConnectors();
    const available = this.getAvailableConnectors();
    return {
      initialized: this.initialized,
      totalConnectors: all.length,
      availableConnectors: available.length,
      totalTools: available.reduce((sum, c) => sum + c.tools.length, 0),
      connectors: all.map((c) => ({
        id: c.id,
        label: c.label,
        category: c.category,
        available: c.available,
        toolCount: c.tools.length,
      })),
    };
  }

  /**
   * Build a dynamic personality/tool-routing section based on available connectors.
   * This is injected into the system prompt so the agent knows what it can do.
   */
  buildToolRoutingContext(): string {
    const available = this.getAvailableConnectors();
    if (available.length === 0) return '';

    const lines: string[] = [
      '## Software Connectors — Installed & Available',
      'You have deep, native-level control over the following software on this machine. Use the appropriate tools when the user asks to work with these applications.',
      '',
    ];

    // Group by category
    const categories: Record<string, Connector[]> = {};
    for (const conn of available) {
      if (!categories[conn.category]) categories[conn.category] = [];
      categories[conn.category].push(conn);
    }

    const categoryLabels: Record<string, string> = {
      foundation: '### Foundation (PowerShell, Terminals)',
      devops: '### Development & DevOps',
      office: '### Office & Productivity',
      creative: '### Creative & Media',
      communication: '### Communication',
      system: '### System & UI Automation',
    };

    for (const [cat, conns] of Object.entries(categories)) {
      lines.push(categoryLabels[cat] || `### ${cat}`);
      for (const conn of conns) {
        const toolNames = conn.tools.map((t) => t.name).join(', ');
        lines.push(`- **${conn.label}**: ${conn.description}`);
        lines.push(`  Tools: ${toolNames}`);
      }
      lines.push('');
    }

    lines.push('When working with any of these tools, be proactive — use the right tool for the job without being asked. If the user mentions a specific app (e.g. "open in VS Code", "commit my changes", "create a Word doc"), route to the matching connector tool.');

    return lines.join('\n');
  }
}

// ── Singleton export ─────────────────────────────────────────────────

export const connectorRegistry = new ConnectorRegistry();
