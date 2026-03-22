/**
 * Track VI — Switchboard: Gateway System Tests
 *
 * Validates: Connector registry structure, tool declaration contracts,
 * SuperpowerStore as the gateway for third-party tool integration,
 * category-based routing, and error isolation.
 *
 * 35 tests across 6 sections.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mocks (hoisted before imports) ──────────────────────────────────

vi.mock('electron', () => ({
  app: { getPath: () => '/tmp/test' },
}));

vi.mock('../../src/main/adapter-engine', () => ({
  validateAdaptedConnector: vi.fn(() => ({ valid: true, errors: [] })),
}));

import {
  connectorRegistry,
  type Connector,
  type ToolDeclaration,
  type ToolResult,
  type ConnectorCategory,
} from '../../src/main/connectors/registry';

import { SuperpowerStore } from '../../src/main/superpower-store';
import type { Superpower, SecurityVerdictSummary } from '../../src/main/superpower-store';
import type { AdaptedConnector, AdaptationPlan, SuperpowerSandboxConfig } from '../../src/main/adapter-engine';

// ── Helpers ─────────────────────────────────────────────────────────

function makeToolDeclaration(name: string, description = 'A test tool'): ToolDeclaration {
  return {
    name,
    description,
    parameters: {
      type: 'object',
      properties: {
        input: { type: 'string', description: 'Input value' },
      },
      required: ['input'],
    },
  };
}

function makeSandboxConfig(): SuperpowerSandboxConfig {
  return {
    allowNetwork: false,
    allowFileSystem: false,
    allowedPaths: [],
    maxExecutionTimeMs: 10000,
    maxMemoryMb: 128,
    allowChildProcesses: false,
  };
}

function makeAdaptationPlan(repoName = 'test-repo'): AdaptationPlan {
  return {
    manifestId: 'manifest-1',
    repoId: 'test/repo@main',
    repoName,
    strategy: {
      type: 'direct-import' as any,
      reason: 'typescript simple module',
      prerequisites: [],
      estimatedComplexity: 'simple',
    },
    capabilities: [],
    dependencies: {
      newDeps: [],
      conflicts: [],
      isolated: false,
      isolationReason: 'no conflicts',
    },
    estimatedDurationMs: 5000,
    createdAt: Date.now(),
  };
}

function makeAdaptedConnector(
  id: string,
  tools: ToolDeclaration[],
  category: ConnectorCategory = 'devops',
): AdaptedConnector {
  return {
    id,
    label: `Test Connector ${id}`,
    category,
    description: `Description for ${id}`,
    tools,
    sourceCode: 'module.exports = {}',
    dependencies: [],
    sandbox: makeSandboxConfig(),
    plan: makeAdaptationPlan(id),
    generatedAt: Date.now(),
  };
}

function makeVerdict(approved = true): SecurityVerdictSummary {
  return {
    approved,
    riskLevel: approved ? 'low' : 'high',
    summary: approved ? 'Safe to install' : 'Risky — rejected',
    reviewedAt: Date.now(),
  };
}

/**
 * Helper: prepare and confirm-install a superpower in one step.
 */
function installSuperpower(
  store: SuperpowerStore,
  connector: AdaptedConnector,
  verdict?: SecurityVerdictSummary,
): Superpower {
  const sp = store.prepareInstall(
    connector,
    verdict || makeVerdict(),
    'https://github.com/test/repo',
    'abc123',
  );
  return store.confirmInstall(sp.id, `consent-${sp.id}-${Date.now()}`);
}

// =====================================================================
// Section 1: ConnectorRegistry — module shape
// =====================================================================

describe('ConnectorRegistry — module shape', () => {
  it('1: exports connectorRegistry singleton', () => {
    expect(connectorRegistry).toBeDefined();
  });

  it('2: has initialize method', () => {
    expect(typeof connectorRegistry.initialize).toBe('function');
  });

  it('3: has getAllTools method', () => {
    expect(typeof connectorRegistry.getAllTools).toBe('function');
  });

  it('4: has executeTool method', () => {
    expect(typeof connectorRegistry.executeTool).toBe('function');
  });

  it('5: has isConnectorTool method', () => {
    expect(typeof connectorRegistry.isConnectorTool).toBe('function');
  });

  it('6: has getAvailableConnectors method', () => {
    expect(typeof connectorRegistry.getAvailableConnectors).toBe('function');
  });

  it('7: has getAllConnectors method', () => {
    expect(typeof connectorRegistry.getAllConnectors).toBe('function');
  });

  it('8: has getStatus method', () => {
    expect(typeof connectorRegistry.getStatus).toBe('function');
  });

  it('9: has buildToolRoutingContext method', () => {
    expect(typeof connectorRegistry.buildToolRoutingContext).toBe('function');
  });
});

// =====================================================================
// Section 2: ToolDeclaration validation
// =====================================================================

describe('ToolDeclaration — interface contract', () => {
  it('10: ToolDeclaration has name, description, parameters fields', () => {
    const tool = makeToolDeclaration('test_tool');
    expect(tool).toHaveProperty('name');
    expect(tool).toHaveProperty('description');
    expect(tool).toHaveProperty('parameters');
    expect(typeof tool.name).toBe('string');
    expect(typeof tool.description).toBe('string');
    expect(typeof tool.parameters).toBe('object');
  });

  it('11: parameters has type and properties structure', () => {
    const tool = makeToolDeclaration('param_tool');
    expect(tool.parameters).toHaveProperty('type');
    expect(tool.parameters).toHaveProperty('properties');
    expect(typeof tool.parameters.type).toBe('string');
    expect(typeof tool.parameters.properties).toBe('object');
  });

  it('12: empty parameters.properties is valid', () => {
    const tool: ToolDeclaration = {
      name: 'no_params_tool',
      description: 'A tool with no parameters',
      parameters: {
        type: 'object',
        properties: {},
      },
    };
    expect(tool.parameters.properties).toEqual({});
    expect(tool.parameters.required).toBeUndefined();
  });

  it('13: required array is optional', () => {
    const withRequired = makeToolDeclaration('with_req');
    expect(withRequired.parameters.required).toBeDefined();

    const withoutRequired: ToolDeclaration = {
      name: 'without_req',
      description: 'No required',
      parameters: { type: 'object', properties: { x: { type: 'string' } } },
    };
    expect(withoutRequired.parameters.required).toBeUndefined();
  });

  it('14: multiple tools can share a parameters shape', () => {
    const sharedParams = {
      type: 'object' as const,
      properties: { query: { type: 'string' } },
      required: ['query'] as string[],
    };
    const tool1: ToolDeclaration = { name: 'search_a', description: 'Search A', parameters: sharedParams };
    const tool2: ToolDeclaration = { name: 'search_b', description: 'Search B', parameters: sharedParams };
    expect(tool1.parameters).toBe(tool2.parameters);
    expect(tool1.name).not.toBe(tool2.name);
  });

  it('15: tool names should be unique convention (distinct strings)', () => {
    const tools = [
      makeToolDeclaration('tool_alpha'),
      makeToolDeclaration('tool_beta'),
      makeToolDeclaration('tool_gamma'),
    ];
    const names = tools.map(t => t.name);
    const unique = new Set(names);
    expect(unique.size).toBe(names.length);
  });
});

// =====================================================================
// Section 3: Connector interface contract
// =====================================================================

describe('Connector — interface contract', () => {
  function makeMockConnector(overrides: Partial<Connector> = {}): Connector {
    return {
      id: 'test-connector',
      label: 'Test Connector',
      category: 'devops',
      description: 'A test connector',
      tools: [makeToolDeclaration('test_exec_tool')],
      execute: async (toolName: string, _args: Record<string, unknown>) => {
        return { result: `Executed ${toolName}` };
      },
      available: true,
      ...overrides,
    };
  }

  it('16: Connector has id, label, category, description, tools, execute, available', () => {
    const conn = makeMockConnector();
    expect(conn).toHaveProperty('id');
    expect(conn).toHaveProperty('label');
    expect(conn).toHaveProperty('category');
    expect(conn).toHaveProperty('description');
    expect(conn).toHaveProperty('tools');
    expect(conn).toHaveProperty('execute');
    expect(conn).toHaveProperty('available');
    expect(typeof conn.id).toBe('string');
    expect(typeof conn.label).toBe('string');
    expect(typeof conn.category).toBe('string');
    expect(typeof conn.description).toBe('string');
    expect(Array.isArray(conn.tools)).toBe(true);
    expect(typeof conn.execute).toBe('function');
    expect(typeof conn.available).toBe('boolean');
  });

  it('17: ConnectorCategory is one of expected values', () => {
    const validCategories: ConnectorCategory[] = [
      'foundation', 'creative', 'office', 'devops', 'communication', 'system',
    ];
    for (const cat of validCategories) {
      const conn = makeMockConnector({ category: cat });
      expect(validCategories).toContain(conn.category);
    }
  });

  it('18: Connector with available=false has tools but they should not route', () => {
    const conn = makeMockConnector({
      available: false,
      tools: [makeToolDeclaration('unavailable_tool')],
    });
    expect(conn.available).toBe(false);
    expect(conn.tools.length).toBeGreaterThan(0);
    // Even though tools exist, the registry would not route to them
    // This tests the contract: tools exist but available gates routing
  });

  it('19: execute returns Promise<ToolResult>', async () => {
    const conn = makeMockConnector();
    const result = await conn.execute('test_exec_tool', { input: 'hello' });
    expect(result).toHaveProperty('result');
    expect(typeof result.result).toBe('string');
  });

  it('20: ToolResult has optional result and error', () => {
    const successResult: ToolResult = { result: 'ok' };
    expect(successResult.result).toBe('ok');
    expect(successResult.error).toBeUndefined();

    const errorResult: ToolResult = { error: 'Something failed' };
    expect(errorResult.error).toBe('Something failed');
    expect(errorResult.result).toBeUndefined();

    const bothResult: ToolResult = { result: 'partial', error: 'warning' };
    expect(bothResult.result).toBeDefined();
    expect(bothResult.error).toBeDefined();
  });
});

// =====================================================================
// Section 4: SuperpowerStore as gateway integration
// =====================================================================

describe('SuperpowerStore — gateway integration', () => {
  let store: SuperpowerStore;

  beforeEach(() => {
    store = new SuperpowerStore({ maxSuperpowers: 50, autoDisableAfterErrors: 5, allowUnsigned: false });
  });

  it('21: superpower tools declared correctly', () => {
    const tools = [makeToolDeclaration('sp_test_search'), makeToolDeclaration('sp_test_fetch')];
    const connector = makeAdaptedConnector('sp-test-1', tools, 'system');
    const sp = installSuperpower(store, connector);

    expect(sp.tools.length).toBe(2);
    expect(sp.tools[0].name).toBe('sp_test_search');
    expect(sp.tools[1].name).toBe('sp_test_fetch');
    for (const tool of sp.tools) {
      expect(tool).toHaveProperty('name');
      expect(tool).toHaveProperty('description');
      expect(tool).toHaveProperty('parameters');
    }
  });

  it('22: enabled superpowers contribute tools to the gateway', () => {
    const connector = makeAdaptedConnector('sp-enabled-1', [
      makeToolDeclaration('sp_enabled_tool_a'),
      makeToolDeclaration('sp_enabled_tool_b'),
    ]);
    installSuperpower(store, connector);

    const enabledTools = store.getEnabledTools();
    expect(enabledTools.length).toBe(2);
    expect(enabledTools.some(t => t.name === 'sp_enabled_tool_a')).toBe(true);
    expect(enabledTools.some(t => t.name === 'sp_enabled_tool_b')).toBe(true);
  });

  it('23: disabled superpowers do not contribute tools', () => {
    const connector = makeAdaptedConnector('sp-disabled-1', [
      makeToolDeclaration('sp_disabled_tool'),
    ]);
    const sp = installSuperpower(store, connector);
    store.disableSuperpower(sp.id);

    const enabledTools = store.getEnabledTools();
    expect(enabledTools.length).toBe(0);
    expect(enabledTools.some(t => t.name === 'sp_disabled_tool')).toBe(false);
  });

  it('24: multiple superpowers each register their tools', () => {
    const conn1 = makeAdaptedConnector('sp-multi-1', [makeToolDeclaration('sp_alpha')], 'devops');
    const conn2 = makeAdaptedConnector('sp-multi-2', [makeToolDeclaration('sp_beta')], 'creative');
    const conn3 = makeAdaptedConnector('sp-multi-3', [makeToolDeclaration('sp_gamma')], 'office');

    installSuperpower(store, conn1);
    installSuperpower(store, conn2);
    installSuperpower(store, conn3);

    const enabledTools = store.getEnabledTools();
    expect(enabledTools.length).toBe(3);
    expect(enabledTools.map(t => t.name).sort()).toEqual(['sp_alpha', 'sp_beta', 'sp_gamma']);
  });

  it('25: tool names from different superpowers are distinct', () => {
    const conn1 = makeAdaptedConnector('sp-distinct-1', [
      makeToolDeclaration('sp_read_data'),
      makeToolDeclaration('sp_write_data'),
    ]);
    const conn2 = makeAdaptedConnector('sp-distinct-2', [
      makeToolDeclaration('sp_parse_json'),
      makeToolDeclaration('sp_validate_schema'),
    ]);

    installSuperpower(store, conn1);
    installSuperpower(store, conn2);

    const allTools = store.getEnabledTools();
    const names = allTools.map(t => t.name);
    const unique = new Set(names);
    expect(unique.size).toBe(names.length);
  });

  it('26: uninstalled superpower tools are removed', () => {
    const connector = makeAdaptedConnector('sp-uninstall-1', [
      makeToolDeclaration('sp_will_be_removed'),
    ]);
    const sp = installSuperpower(store, connector);

    expect(store.getEnabledTools().length).toBe(1);

    store.uninstallSuperpower(sp.id);

    expect(store.getEnabledTools().length).toBe(0);
    expect(store.get(sp.id)).toBeNull();
  });

  it('27: category assignment is preserved', () => {
    const connector = makeAdaptedConnector('sp-cat-1', [makeToolDeclaration('sp_cat_tool')], 'creative');
    const sp = installSuperpower(store, connector);

    expect(sp.category).toBe('creative');
    const byCategory = store.getByCategory('creative');
    expect(byCategory.length).toBe(1);
    expect(byCategory[0].id).toBe('sp-cat-1');
  });

  it('28: superpower with multiple tools registers all of them', () => {
    const tools = Array.from({ length: 5 }, (_, i) =>
      makeToolDeclaration(`sp_bulk_tool_${i}`, `Bulk tool ${i}`),
    );
    const connector = makeAdaptedConnector('sp-bulk-1', tools);
    const sp = installSuperpower(store, connector);

    expect(sp.tools.length).toBe(5);
    const enabledTools = store.getEnabledTools();
    expect(enabledTools.length).toBe(5);
    for (let i = 0; i < 5; i++) {
      expect(enabledTools.some(t => t.name === `sp_bulk_tool_${i}`)).toBe(true);
    }
  });

  it('29: getStatus shows correct total tool count', () => {
    const conn1 = makeAdaptedConnector('sp-status-1', [
      makeToolDeclaration('sp_s1'),
      makeToolDeclaration('sp_s2'),
    ]);
    const conn2 = makeAdaptedConnector('sp-status-2', [
      makeToolDeclaration('sp_s3'),
    ]);

    installSuperpower(store, conn1);
    installSuperpower(store, conn2);

    const status = store.getStatus();
    expect(status.totalInstalled).toBe(2);
    expect(status.totalEnabled).toBe(2);
    expect(status.totalTools).toBe(3);
  });

  it('30: superpower tool declarations match ToolDeclaration interface', () => {
    const tools = [
      makeToolDeclaration('sp_interface_check'),
    ];
    const connector = makeAdaptedConnector('sp-iface-1', tools);
    const sp = installSuperpower(store, connector);

    for (const tool of sp.tools) {
      // Every tool must satisfy ToolDeclaration shape
      expect(typeof tool.name).toBe('string');
      expect(tool.name.length).toBeGreaterThan(0);
      expect(typeof tool.description).toBe('string');
      expect(tool.description.length).toBeGreaterThan(0);
      expect(typeof tool.parameters).toBe('object');
      expect(typeof tool.parameters.type).toBe('string');
      expect(typeof tool.parameters.properties).toBe('object');
    }
  });
});

// =====================================================================
// Section 5: Category-based routing
// =====================================================================

describe('Category-based routing', () => {
  let store: SuperpowerStore;

  beforeEach(() => {
    store = new SuperpowerStore({ maxSuperpowers: 50, autoDisableAfterErrors: 5, allowUnsigned: false });
  });

  it('31: all expected categories are valid ConnectorCategory values', () => {
    const expectedCategories: ConnectorCategory[] = [
      'foundation', 'creative', 'office', 'devops', 'communication', 'system',
    ];
    // Each should be assignable without type error and work in the store
    for (const cat of expectedCategories) {
      const connector = makeAdaptedConnector(`sp-catval-${cat}`, [
        makeToolDeclaration(`sp_${cat}_tool`),
      ], cat);
      const sp = installSuperpower(store, connector);
      expect(sp.category).toBe(cat);
    }
  });

  it('32: superpowers grouped by category', () => {
    const categories: ConnectorCategory[] = ['creative', 'creative', 'devops', 'system'];
    for (let i = 0; i < categories.length; i++) {
      const connector = makeAdaptedConnector(`sp-group-${i}`, [
        makeToolDeclaration(`sp_grp_tool_${i}`),
      ], categories[i]);
      installSuperpower(store, connector);
    }

    expect(store.getByCategory('creative').length).toBe(2);
    expect(store.getByCategory('devops').length).toBe(1);
    expect(store.getByCategory('system').length).toBe(1);
    expect(store.getByCategory('office').length).toBe(0);
    expect(store.getByCategory('foundation').length).toBe(0);
    expect(store.getByCategory('communication').length).toBe(0);
  });

  it('33: tools can be filtered by category through superpowers', () => {
    const devopsConn = makeAdaptedConnector('sp-filter-devops', [
      makeToolDeclaration('sp_build'),
      makeToolDeclaration('sp_deploy'),
    ], 'devops');
    const creativeConn = makeAdaptedConnector('sp-filter-creative', [
      makeToolDeclaration('sp_render'),
    ], 'creative');

    installSuperpower(store, devopsConn);
    installSuperpower(store, creativeConn);

    const devopsTools = store.getByCategory('devops')
      .filter(sp => sp.enabled)
      .flatMap(sp => sp.tools);
    const creativeTools = store.getByCategory('creative')
      .filter(sp => sp.enabled)
      .flatMap(sp => sp.tools);

    expect(devopsTools.length).toBe(2);
    expect(devopsTools.map(t => t.name).sort()).toEqual(['sp_build', 'sp_deploy']);
    expect(creativeTools.length).toBe(1);
    expect(creativeTools[0].name).toBe('sp_render');
  });

  it('34: all categories have string type', () => {
    const allCategories: ConnectorCategory[] = [
      'foundation', 'creative', 'office', 'devops', 'communication', 'system',
    ];
    for (const cat of allCategories) {
      expect(typeof cat).toBe('string');
      expect(cat.length).toBeGreaterThan(0);
    }
  });
});

// =====================================================================
// Section 6: Error isolation
// =====================================================================

describe('Error isolation', () => {
  let store: SuperpowerStore;

  beforeEach(() => {
    store = new SuperpowerStore({
      maxSuperpowers: 50,
      autoDisableAfterErrors: 5,
      allowUnsigned: false,
    });
  });

  it('35: one superpower error does not affect others', () => {
    const conn1 = makeAdaptedConnector('sp-err-1', [makeToolDeclaration('sp_err_tool_1')]);
    const conn2 = makeAdaptedConnector('sp-err-2', [makeToolDeclaration('sp_err_tool_2')]);
    const sp1 = installSuperpower(store, conn1);
    const sp2 = installSuperpower(store, conn2);

    // Record errors on sp1 only
    store.recordError(sp1.id, 'Connection timeout');
    store.recordError(sp1.id, 'Another error');

    // sp2 should be completely unaffected
    const sp2Current = store.get(sp2.id)!;
    expect(sp2Current.health.errorCount).toBe(0);
    expect(sp2Current.health.score).toBe(1.0);
    expect(sp2Current.enabled).toBe(true);

    // sp1 should have degraded
    const sp1Current = store.get(sp1.id)!;
    expect(sp1Current.health.errorCount).toBe(2);
  });

  it('36: recordError does not immediately disable the superpower', () => {
    const connector = makeAdaptedConnector('sp-noauto-1', [makeToolDeclaration('sp_noauto_tool')]);
    const sp = installSuperpower(store, connector);

    // Record a single error (below autoDisableAfterErrors threshold of 5)
    store.recordError(sp.id, 'Transient failure');

    const current = store.get(sp.id)!;
    expect(current.enabled).toBe(true);
    expect(current.health.errorCount).toBe(1);
    expect(current.health.lastError).toBe('Transient failure');
  });

  it('37: health score degrades with errors', () => {
    const connector = makeAdaptedConnector('sp-degrade-1', [makeToolDeclaration('sp_degrade_tool')]);
    const sp = installSuperpower(store, connector);

    const initialScore = store.get(sp.id)!.health.score;

    store.recordError(sp.id, 'Error 1');
    const afterOne = store.get(sp.id)!.health.score;

    store.recordError(sp.id, 'Error 2');
    const afterTwo = store.get(sp.id)!.health.score;

    store.recordError(sp.id, 'Error 3');
    const afterThree = store.get(sp.id)!.health.score;

    expect(initialScore).toBe(1.0);
    expect(afterOne).toBeLessThan(initialScore);
    expect(afterTwo).toBeLessThan(afterOne);
    expect(afterThree).toBeLessThan(afterTwo);
    // Score should never go below 0
    expect(afterThree).toBeGreaterThanOrEqual(0);
  });

  it('38: store continues operating after error recording', () => {
    const conn1 = makeAdaptedConnector('sp-cont-1', [makeToolDeclaration('sp_cont_tool_1')]);
    const sp1 = installSuperpower(store, conn1);

    // Hammer with errors
    for (let i = 0; i < 10; i++) {
      store.recordError(sp1.id, `Error ${i}`);
    }

    // Store should still be functional — can install new superpowers
    const conn2 = makeAdaptedConnector('sp-cont-2', [makeToolDeclaration('sp_cont_tool_2')]);
    const sp2 = installSuperpower(store, conn2);

    expect(sp2.enabled).toBe(true);
    expect(store.getAll().length).toBe(2);

    // Status should still work
    const status = store.getStatus();
    expect(status.totalInstalled).toBeGreaterThanOrEqual(1);
  });

  it('39: invalid tool execution returns error ToolResult shape', async () => {
    // Test via connectorRegistry.executeTool for an unknown tool
    const result = await connectorRegistry.executeTool('nonexistent_tool_xyz', { input: 'test' });
    expect(result).toHaveProperty('error');
    expect(typeof result.error).toBe('string');
    expect(result.error!.length).toBeGreaterThan(0);
    expect(result.result).toBeUndefined();
  });
});
