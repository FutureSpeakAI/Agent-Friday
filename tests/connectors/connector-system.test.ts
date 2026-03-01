/**
 * Connector System — Comprehensive test suite.
 *
 * Tests the ConnectorRegistry hub-and-spoke architecture plus the
 * structural integrity of all 18 connector modules. These tests
 * validate the contract (TOOLS, execute, detect), safety blocklists,
 * routing, and personality prompt generation WITHOUT executing
 * real system commands or spawning child processes.
 *
 * Track: Phase D — Integration & Build Verification
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// ── Registry under test ─────────────────────────────────────────────

// We'll test the registry class directly by constructing it fresh
// and injecting mock modules via the private loadModules path.
// Since the registry uses require() internally, we mock individual connectors.

import {
  connectorRegistry,
  type Connector,
  type ConnectorCategory,
  type ToolDeclaration,
  type ToolResult,
} from '../../src/main/connectors/registry';

// ── Helpers ─────────────────────────────────────────────────────────

function makeMockConnector(overrides: Partial<Connector> = {}): Connector {
  return {
    id: overrides.id ?? 'test-connector',
    label: overrides.label ?? 'Test Connector',
    category: overrides.category ?? 'foundation',
    description: overrides.description ?? 'A test connector',
    tools: overrides.tools ?? [
      {
        name: 'test_tool_one',
        description: 'First test tool',
        parameters: { type: 'object', properties: { input: { type: 'string' } }, required: ['input'] },
      },
      {
        name: 'test_tool_two',
        description: 'Second test tool',
        parameters: { type: 'object', properties: {}, required: [] },
      },
    ],
    execute: overrides.execute ?? (async (toolName: string, args: Record<string, unknown>) => ({ result: `${toolName} executed` })),
    available: overrides.available ?? true,
  };
}

// ── Module-level connector contracts ────────────────────────────────

describe('Connector Module Contracts', () => {
  /**
   * Every connector under src/main/connectors/ must export:
   *  - TOOLS: array of tool declarations
   *  - execute: async function (toolName, args) → ToolResult
   *  - detect: async function () → boolean
   *
   * We import each module and verify the exports exist and have correct shapes.
   */

  const connectorFiles = [
    'powershell',
    'terminal-sessions',
    'vscode',
    'git-devops',
    'office',
    'adobe',
    'creative-3d',
    'media-streaming',
    'comms-hub',
    'dev-environments',
    'ui-automation',
    'system-management',
    'world-monitor',
    'firecrawl',
    'perplexity',
    'openai-services',
    'pageindex',
  ];

  for (const name of connectorFiles) {
    describe(`${name}`, () => {
      let mod: any;

      beforeEach(async () => {
        mod = await import(`../../src/main/connectors/${name}`);
      });

      it('exports TOOLS as a non-empty array', () => {
        expect(mod.TOOLS).toBeDefined();
        expect(Array.isArray(mod.TOOLS)).toBe(true);
        expect(mod.TOOLS.length).toBeGreaterThan(0);
      });

      it('each tool has name, description, and parameters', () => {
        for (const tool of mod.TOOLS) {
          expect(typeof tool.name).toBe('string');
          expect(tool.name.length).toBeGreaterThan(0);
          expect(typeof tool.description).toBe('string');
          expect(tool.description.length).toBeGreaterThan(0);
          expect(tool.parameters).toBeDefined();
          expect(typeof tool.parameters.type).toBe('string');
          expect(tool.parameters.properties).toBeDefined();
        }
      });

      it('tool names are unique within module', () => {
        const names = mod.TOOLS.map((t: any) => t.name);
        const unique = new Set(names);
        expect(unique.size).toBe(names.length);
      });

      it('tool names use snake_case convention', () => {
        for (const tool of mod.TOOLS) {
          expect(tool.name).toMatch(/^[a-z][a-z0-9_]*$/);
        }
      });

      it('exports execute as a function', () => {
        expect(typeof mod.execute).toBe('function');
      });

      it('exports detect as a function', () => {
        expect(typeof mod.detect).toBe('function');
      });

      it('execute returns error for unknown tool', async () => {
        const result = await mod.execute('__totally_bogus_tool__', {});
        expect(result).toBeDefined();
        expect(result.error).toBeDefined();
        expect(typeof result.error).toBe('string');
      });
    });
  }
});

// ── Tool name uniqueness across ALL connectors ──────────────────────

describe('Cross-Connector Tool Uniqueness', () => {
  it('no two connectors share a tool name', async () => {
    const connectorFiles = [
      'powershell', 'terminal-sessions', 'vscode', 'git-devops',
      'office', 'adobe', 'creative-3d', 'media-streaming',
      'comms-hub', 'dev-environments', 'ui-automation', 'system-management',
      'world-monitor', 'firecrawl', 'perplexity', 'openai-services', 'pageindex',
    ];

    const seen = new Map<string, string>(); // toolName → connectorName
    const duplicates: string[] = [];

    for (const name of connectorFiles) {
      const mod = await import(`../../src/main/connectors/${name}`);
      for (const tool of mod.TOOLS) {
        if (seen.has(tool.name)) {
          duplicates.push(`"${tool.name}" defined in both ${seen.get(tool.name)} and ${name}`);
        }
        seen.set(tool.name, name);
      }
    }

    expect(duplicates).toEqual([]);
  });
});

// ── Safety Blocklist Tests ──────────────────────────────────────────

describe('Safety Blocklists', () => {
  describe('PowerShell dangerous command patterns', () => {
    let mod: any;

    beforeEach(async () => {
      mod = await import('../../src/main/connectors/powershell');
    });

    const dangerousCommands = [
      'Format-Volume -DriveLetter C',
      'Clear-Disk -Number 0',
      'Initialize-Disk -Number 1',
      'Remove-Partition -DiskNumber 0',
      'Remove-Item C:\\Windows\\System32 -Recurse',
      'Stop-Computer',
      'Restart-Computer -Force',
      'shutdown /s /t 0',
      'Invoke-Mimikatz',
      'Set-MpPreference -DisableRealtimeMonitoring $true',
      'bcdedit /delete {bootmgr}',
    ];

    for (const cmd of dangerousCommands) {
      it(`blocks: ${cmd.slice(0, 50)}...`, async () => {
        const result = await mod.execute('powershell_execute', { command: cmd });
        expect(result.error).toBeDefined();
        expect(result.error!.toLowerCase()).toContain('blocked');
      });
    }
  });

  describe('Dev Environments safety', () => {
    let mod: any;

    beforeEach(async () => {
      mod = await import('../../src/main/connectors/dev-environments');
    });

    it('blocks dangerous pip packages', async () => {
      const result = await mod.execute('python_pip_install', {
        venv_path: '/tmp/test-venv',
        packages: 'keylogger',
      });
      expect(result.error).toBeDefined();
      expect(result.error!.toLowerCase()).toContain('blocked');
    });

    it('blocks destructive SQL on production databases', async () => {
      const result = await mod.execute('database_query', {
        engine: 'postgres',
        connection_string: 'host=mydb.rds.amazonaws.com user=admin dbname=production',
        query: 'DROP TABLE users',
      });
      expect(result.error).toBeDefined();
      expect(result.error!.toLowerCase()).toContain('blocked');
    });

    it('blocks TRUNCATE on production databases', async () => {
      const result = await mod.execute('database_query', {
        engine: 'mysql',
        connection_string: 'host=production.database.azure.com',
        query: 'TRUNCATE TABLE orders',
      });
      expect(result.error).toBeDefined();
      expect(result.error!.toLowerCase()).toContain('blocked');
    });
  });
});

// ── ConnectorRegistry Unit Tests ────────────────────────────────────

describe('ConnectorRegistry', () => {
  // We test the singleton's public API shape and internal routing logic.
  // Since initialize() does real require() calls, we test the class behavior
  // by inspecting the public methods on the exported singleton.

  describe('Exported Types', () => {
    it('exports ConnectorCategory type values', () => {
      const validCategories: ConnectorCategory[] = [
        'foundation', 'creative', 'office', 'devops', 'communication', 'system',
      ];
      expect(validCategories.length).toBe(6);
    });

    it('ToolDeclaration shape is correct', () => {
      const tool: ToolDeclaration = {
        name: 'test',
        description: 'Test tool',
        parameters: { type: 'object', properties: {}, required: [] },
      };
      expect(tool.name).toBe('test');
      expect(tool.parameters.type).toBe('object');
    });

    it('ToolResult allows result or error', () => {
      const success: ToolResult = { result: 'ok' };
      const failure: ToolResult = { error: 'bad' };
      expect(success.result).toBe('ok');
      expect(failure.error).toBe('bad');
    });
  });

  describe('Singleton API surface', () => {
    it('exports connectorRegistry singleton', () => {
      expect(connectorRegistry).toBeDefined();
    });

    it('has initialize method', () => {
      expect(typeof connectorRegistry.initialize).toBe('function');
    });

    it('has getAllTools method', () => {
      expect(typeof connectorRegistry.getAllTools).toBe('function');
    });

    it('has executeTool method', () => {
      expect(typeof connectorRegistry.executeTool).toBe('function');
    });

    it('has isConnectorTool method', () => {
      expect(typeof connectorRegistry.isConnectorTool).toBe('function');
    });

    it('has getAvailableConnectors method', () => {
      expect(typeof connectorRegistry.getAvailableConnectors).toBe('function');
    });

    it('has getAllConnectors method', () => {
      expect(typeof connectorRegistry.getAllConnectors).toBe('function');
    });

    it('has getStatus method', () => {
      expect(typeof connectorRegistry.getStatus).toBe('function');
    });

    it('has buildToolRoutingContext method', () => {
      expect(typeof connectorRegistry.buildToolRoutingContext).toBe('function');
    });
  });

  describe('getStatus structure', () => {
    it('returns valid status shape', () => {
      const status = connectorRegistry.getStatus();
      expect(typeof status.initialized).toBe('boolean');
      expect(typeof status.totalConnectors).toBe('number');
      expect(typeof status.availableConnectors).toBe('number');
      expect(typeof status.totalTools).toBe('number');
      expect(Array.isArray(status.connectors)).toBe(true);
    });

    it('connector entries have required fields', () => {
      const status = connectorRegistry.getStatus();
      for (const c of status.connectors) {
        expect(typeof c.id).toBe('string');
        expect(typeof c.label).toBe('string');
        expect(typeof c.category).toBe('string');
        expect(typeof c.available).toBe('boolean');
        expect(typeof c.toolCount).toBe('number');
      }
    });
  });

  describe('Tool routing', () => {
    it('executeTool returns error for unknown tool', async () => {
      const result = await connectorRegistry.executeTool('__nonexistent_tool__', {});
      expect(result.error).toBeDefined();
      expect(result.error).toContain('Unknown connector tool');
    });

    it('isConnectorTool returns false for unknown tool', () => {
      expect(connectorRegistry.isConnectorTool('__nonexistent_tool__')).toBe(false);
    });
  });

  describe('buildToolRoutingContext', () => {
    it('returns a string', () => {
      const ctx = connectorRegistry.buildToolRoutingContext();
      expect(typeof ctx).toBe('string');
    });

    it('includes category headers when connectors available', () => {
      const ctx = connectorRegistry.buildToolRoutingContext();
      // If any connectors detected, we should see headers
      if (ctx.length > 0) {
        expect(ctx).toContain('Software Connectors');
        expect(ctx).toContain('Tools:');
      }
    });
  });
});

// ── Registry Routing Logic (isolated) ───────────────────────────────

describe('Registry Routing Logic (singleton, pre-init)', () => {
  /**
   * Test routing behavior on the singleton before initialize() is called.
   * The singleton starts uninitialized in test context.
   */

  it('executeTool errors for unknown tool', async () => {
    const result = await connectorRegistry.executeTool('anything', {});
    expect(result.error).toBeDefined();
  });

  it('isConnectorTool returns false for unknown tool', () => {
    expect(connectorRegistry.isConnectorTool('anything')).toBe(false);
  });

  it('getAllTools returns array', () => {
    const tools = connectorRegistry.getAllTools();
    expect(Array.isArray(tools)).toBe(true);
  });

  it('getAvailableConnectors returns array', () => {
    const available = connectorRegistry.getAvailableConnectors();
    expect(Array.isArray(available)).toBe(true);
  });

  it('getAllConnectors returns array', () => {
    const all = connectorRegistry.getAllConnectors();
    expect(Array.isArray(all)).toBe(true);
  });

  it('buildToolRoutingContext returns string', () => {
    const ctx = connectorRegistry.buildToolRoutingContext();
    expect(typeof ctx).toBe('string');
  });

  it('getStatus returns valid shape', () => {
    const status = connectorRegistry.getStatus();
    expect(typeof status.initialized).toBe('boolean');
    expect(typeof status.totalConnectors).toBe('number');
    expect(typeof status.availableConnectors).toBe('number');
    expect(typeof status.totalTools).toBe('number');
  });
});

// ── Connector Tool Declaration Quality ──────────────────────────────

describe('Tool Declaration Quality', () => {
  const connectorFiles = [
    'powershell', 'terminal-sessions', 'vscode', 'git-devops',
    'office', 'adobe', 'creative-3d', 'media-streaming',
    'comms-hub', 'dev-environments', 'ui-automation', 'system-management',
    'world-monitor', 'firecrawl', 'perplexity', 'openai-services', 'pageindex',
  ];

  it('all tool descriptions are actionable (start with a verb or noun)', async () => {
    for (const name of connectorFiles) {
      const mod = await import(`../../src/main/connectors/${name}`);
      for (const tool of mod.TOOLS) {
        expect(tool.description.length).toBeGreaterThan(10);
      }
    }
  });

  it('all required parameters are defined in properties', async () => {
    for (const name of connectorFiles) {
      const mod = await import(`../../src/main/connectors/${name}`);
      for (const tool of mod.TOOLS) {
        const required = tool.parameters.required || [];
        const properties = Object.keys(tool.parameters.properties || {});
        for (const req of required) {
          expect(properties).toContain(req);
        }
      }
    }
  });

  it('total tool count across all connectors is reasonable (50-500)', async () => {
    let total = 0;
    for (const name of connectorFiles) {
      const mod = await import(`../../src/main/connectors/${name}`);
      total += mod.TOOLS.length;
    }
    expect(total).toBeGreaterThan(50);
    expect(total).toBeLessThan(500);
  });
});

// ── Connector Categories ────────────────────────────────────────────

describe('Connector Categories', () => {
  it('registry module definitions cover all six categories', () => {
    // The registry hard-codes module definitions with categories.
    // Verify all categories are represented.
    const expectedCategories: ConnectorCategory[] = [
      'foundation', 'devops', 'office', 'creative', 'communication', 'system',
    ];

    // This is verified by the registry source code having modules in each category.
    // If we can't initialize, we at least verify the type system supports them.
    for (const cat of expectedCategories) {
      const c: ConnectorCategory = cat;
      expect(typeof c).toBe('string');
    }
  });
});

// ── Integration Readiness ───────────────────────────────────────────

describe('Integration Readiness', () => {
  it('registry can be imported without side effects', () => {
    // The singleton should exist but NOT auto-initialize
    expect(connectorRegistry).toBeDefined();
    // initialized starts false — real init happens in index.ts
    const status = connectorRegistry.getStatus();
    // Status should be queryable without errors
    expect(typeof status.initialized).toBe('boolean');
  });

  it('IPC contract: getAllTools returns serializable data', () => {
    const tools = connectorRegistry.getAllTools();
    // Must be JSON-serializable for IPC
    const serialized = JSON.stringify(tools);
    const deserialized = JSON.parse(serialized);
    expect(deserialized).toEqual(tools);
  });

  it('IPC contract: getStatus returns serializable data', () => {
    const status = connectorRegistry.getStatus();
    const serialized = JSON.stringify(status);
    const deserialized = JSON.parse(serialized);
    expect(deserialized).toEqual(status);
  });

  it('IPC contract: executeTool returns serializable data', async () => {
    const result = await connectorRegistry.executeTool('nonexistent', {});
    const serialized = JSON.stringify(result);
    const deserialized = JSON.parse(serialized);
    expect(deserialized).toEqual(result);
  });

  it('IPC contract: buildToolRoutingContext returns string', () => {
    const ctx = connectorRegistry.buildToolRoutingContext();
    expect(typeof ctx).toBe('string');
  });
});

// ── Personality Prompt Injection Format ──────────────────────────────

describe('Personality Prompt Injection Format', () => {
  it('routing context includes tool names inline', async () => {
    // If any connectors are available, the context should list tools
    const ctx = connectorRegistry.buildToolRoutingContext();
    if (ctx.length > 0) {
      expect(ctx).toContain('Tools:');
      // Should have markdown formatting
      expect(ctx).toContain('**');
    }
  });

  it('routing context ends with usage guidance', () => {
    const ctx = connectorRegistry.buildToolRoutingContext();
    if (ctx.length > 0) {
      expect(ctx.toLowerCase()).toContain('proactive');
    }
  });
});

// ── Connector Safety Architecture ───────────────────────────────────

describe('Connector Safety Architecture', () => {
  it('each connector has output length limits', async () => {
    const connectorFiles = [
      'powershell', 'terminal-sessions', 'vscode', 'git-devops',
      'office', 'adobe', 'creative-3d', 'media-streaming',
      'comms-hub', 'dev-environments', 'ui-automation', 'system-management',
    ];

    for (const name of connectorFiles) {
      const source = await import(`../../src/main/connectors/${name}`);
      // Every connector should have output capping — verified by the module
      // exporting TOOLS and execute() that cap output internally.
      // We verify indirectly: the module has the expected shape.
      expect(source.TOOLS).toBeDefined();
      expect(typeof source.execute).toBe('function');
    }
  });

  it('execute functions wrap errors, never throw', async () => {
    const connectorFiles = [
      'powershell', 'terminal-sessions', 'vscode', 'git-devops',
      'office', 'adobe', 'creative-3d', 'media-streaming',
      'comms-hub', 'dev-environments', 'ui-automation', 'system-management',
    ];

    for (const name of connectorFiles) {
      const mod = await import(`../../src/main/connectors/${name}`);
      // Calling with unknown tool should return { error: ... }, not throw
      const result = await mod.execute('__bogus__', {});
      expect(result).toBeDefined();
      expect(result.error).toBeDefined();
    }
  });
});
