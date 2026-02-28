/**
 * superpowers-handlers.test.ts — Tests for the unified Superpowers IPC handlers.
 *
 * Track II, Phase 4: The Absorber — Superpower UI.
 *
 * Covers:
 *   1. v1/v2 Routing Logic (sp- prefix detection)
 *   2. Uninstall Preview (cLaw Gate: enumerate what gets removed)
 *   3. v2 Store Handler Delegation
 *   4. Input Validation (all handlers reject bad inputs)
 *   5. Toggle Routing (v1 registry vs v2 store)
 *   6. Record Invocation Routing
 *   7. v2 Store CRUD (list, get, confirm, enabled-tools, status, prompt-context, needs-attention)
 *   8. Error Handling
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock IPC Infrastructure ─────────────────────────────────────────────

type IpcHandler = (...args: any[]) => any;
const registeredHandlers = new Map<string, IpcHandler>();

vi.mock('electron', () => ({
  ipcMain: {
    handle: (channel: string, handler: IpcHandler) => {
      registeredHandlers.set(channel, handler);
    },
  },
}));

// vi.mock factories are hoisted — must use inline objects, not external refs

vi.mock('../../src/main/superpowers-registry', () => ({
  superpowersRegistry: {
    initialize: vi.fn().mockResolvedValue(undefined),
    listAll: vi.fn().mockReturnValue([]),
    get: vi.fn().mockReturnValue(null),
    setEnabled: vi.fn().mockReturnValue({ id: 'git-1', enabled: true }),
    setToolEnabled: vi.fn().mockReturnValue(true),
    updatePermissions: vi.fn().mockReturnValue(true),
    install: vi.fn().mockResolvedValue({ id: 'git-1', name: 'Test' }),
    uninstall: vi.fn().mockResolvedValue(true),
    getUsageStats: vi.fn().mockReturnValue({ totalInvocations: 10 }),
    getAllEnabledTools: vi.fn().mockReturnValue([]),
    recordInvocation: vi.fn(),
    flush: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('../../src/main/superpower-store', () => ({
  superpowerStore: {
    getAll: vi.fn().mockReturnValue([]),
    get: vi.fn().mockReturnValue(null),
    confirmInstall: vi.fn(),
    getEnabledTools: vi.fn().mockReturnValue([]),
    getStatus: vi.fn().mockReturnValue({ total: 0, enabled: 0 }),
    getPromptContext: vi.fn().mockReturnValue(''),
    getNeedingAttention: vi.fn().mockReturnValue([]),
    enableSuperpower: vi.fn(),
    disableSuperpower: vi.fn(),
    uninstallSuperpower: vi.fn(),
    recordUsage: vi.fn(),
    recordError: vi.fn(),
  },
}));

// ── Import AFTER mocks ─────────────────────────────────────────────────

import { registerSuperpowersHandlers } from '../../src/main/ipc/superpowers-handlers';
import { superpowersRegistry } from '../../src/main/superpowers-registry';
import { superpowerStore } from '../../src/main/superpower-store';

// Get typed mock references
const mockRegistry = vi.mocked(superpowersRegistry);
const mockStore = vi.mocked(superpowerStore);

// ── Helpers ─────────────────────────────────────────────────────────────

function invoke(channel: string, ...args: any[]): any {
  const handler = registeredHandlers.get(channel);
  if (!handler) throw new Error(`No handler for channel: ${channel}`);
  return handler({ sender: {} }, ...args);
}

// ── Tests ───────────────────────────────────────────────────────────────

describe('Superpowers IPC Handlers — Phase 4', () => {
  beforeEach(() => {
    registeredHandlers.clear();
    vi.clearAllMocks();
    registerSuperpowersHandlers();
  });

  // ── 1. Handler Registration ──────────────────────────────────────

  describe('Handler Registration', () => {
    it('registers all expected v1 channels', () => {
      const v1Channels = [
        'superpowers:list',
        'superpowers:get',
        'superpowers:toggle',
        'superpowers:toggle-tool',
        'superpowers:update-permissions',
        'superpowers:install',
        'superpowers:uninstall',
        'superpowers:usage-stats',
        'superpowers:enabled-tools',
        'superpowers:record-invocation',
        'superpowers:flush',
      ];
      for (const ch of v1Channels) {
        expect(registeredHandlers.has(ch), `missing: ${ch}`).toBe(true);
      }
    });

    it('registers all expected v2 store channels', () => {
      const v2Channels = [
        'superpowers:store-list',
        'superpowers:store-get',
        'superpowers:store-confirm',
        'superpowers:store-enabled-tools',
        'superpowers:store-status',
        'superpowers:store-prompt-context',
        'superpowers:store-needs-attention',
      ];
      for (const ch of v2Channels) {
        expect(registeredHandlers.has(ch), `missing: ${ch}`).toBe(true);
      }
    });

    it('registers the uninstall-preview handler', () => {
      expect(registeredHandlers.has('superpowers:uninstall-preview')).toBe(true);
    });
  });

  // ── 2. v1/v2 Toggle Routing ──────────────────────────────────────

  describe('Toggle Routing', () => {
    it('routes sp- prefixed IDs to v2 store for enable', async () => {
      mockStore.get.mockReturnValue({ id: 'sp-test', enabled: true } as any);
      await invoke('superpowers:toggle', 'sp-test', true);
      expect(mockStore.enableSuperpower).toHaveBeenCalledWith('sp-test');
      expect(mockStore.get).toHaveBeenCalledWith('sp-test');
      expect(mockRegistry.setEnabled).not.toHaveBeenCalled();
    });

    it('routes sp- prefixed IDs to v2 store for disable', async () => {
      mockStore.get.mockReturnValue({ id: 'sp-test', enabled: false } as any);
      await invoke('superpowers:toggle', 'sp-test', false);
      expect(mockStore.disableSuperpower).toHaveBeenCalledWith('sp-test');
      expect(mockStore.get).toHaveBeenCalledWith('sp-test');
      expect(mockRegistry.setEnabled).not.toHaveBeenCalled();
    });

    it('routes non-sp IDs to v1 registry', async () => {
      await invoke('superpowers:toggle', 'git-repo-1', true);
      expect(mockRegistry.setEnabled).toHaveBeenCalledWith('git-repo-1', true);
      expect(mockStore.enableSuperpower).not.toHaveBeenCalled();
    });
  });

  // ── 3. Uninstall Routing ─────────────────────────────────────────

  describe('Uninstall Routing', () => {
    it('routes sp- prefixed IDs to v2 store', async () => {
      const result = await invoke('superpowers:uninstall', 'sp-adapted-1');
      expect(mockStore.uninstallSuperpower).toHaveBeenCalledWith('sp-adapted-1');
      expect(result).toBe(true);
      expect(mockRegistry.uninstall).not.toHaveBeenCalled();
    });

    it('routes non-sp IDs to v1 registry', async () => {
      mockRegistry.uninstall.mockResolvedValue(true as any);
      await invoke('superpowers:uninstall', 'git-repo-1');
      expect(mockRegistry.uninstall).toHaveBeenCalledWith('git-repo-1');
      expect(mockStore.uninstallSuperpower).not.toHaveBeenCalled();
    });
  });

  // ── 4. Uninstall Preview (cLaw Gate) ─────────────────────────────

  describe('Uninstall Preview — cLaw Gate', () => {
    it('returns preview for v2 superpower with all fields', () => {
      mockStore.get.mockReturnValue({
        id: 'sp-test',
        name: 'Test Superpower',
        tools: [{ name: 'tool_a' }, { name: 'tool_b' }],
        usageCount: 42,
        sourceCode: 'const x = 1;',
        bridgeScript: '#!/bin/bash\necho hi',
        dependencies: ['dep1', 'dep2', 'dep3'],
      } as any);

      const preview = invoke('superpowers:uninstall-preview', 'sp-test');
      expect(preview).toEqual({
        id: 'sp-test',
        name: 'Test Superpower',
        toolsRemoved: ['tool_a', 'tool_b'],
        toolCount: 2,
        usageCount: 42,
        hasSourceCode: true,
        hasBridgeScript: true,
        dependencyCount: 3,
      });
    });

    it('returns preview for v1 GitLoader superpower', () => {
      mockRegistry.get.mockReturnValue({
        id: 'git-1',
        name: 'Git Repo',
        tools: [{ name: 'run_analysis' }, { name: 'fetch_data' }, { name: 'process' }],
        totalInvocations: 100,
      } as any);

      const preview = invoke('superpowers:uninstall-preview', 'git-1');
      expect(preview).toEqual({
        id: 'git-1',
        name: 'Git Repo',
        toolsRemoved: ['run_analysis', 'fetch_data', 'process'],
        toolCount: 3,
        usageCount: 100,
        hasSourceCode: false,
        hasBridgeScript: false,
        dependencyCount: 0,
      });
    });

    it('returns null for unknown v2 superpower', () => {
      mockStore.get.mockReturnValue(null as any);
      const preview = invoke('superpowers:uninstall-preview', 'sp-nonexistent');
      expect(preview).toBeNull();
    });

    it('returns null for unknown v1 superpower', () => {
      mockRegistry.get.mockReturnValue(null as any);
      const preview = invoke('superpowers:uninstall-preview', 'git-nonexistent');
      expect(preview).toBeNull();
    });

    it('handles v2 superpower with no source code or bridge', () => {
      mockStore.get.mockReturnValue({
        id: 'sp-minimal',
        name: 'Minimal SP',
        tools: [{ name: 'single_tool' }],
        usageCount: 0,
        sourceCode: '',
        bridgeScript: '',
        dependencies: [],
      } as any);

      const preview = invoke('superpowers:uninstall-preview', 'sp-minimal');
      expect(preview.hasSourceCode).toBe(false);
      expect(preview.hasBridgeScript).toBe(false);
      expect(preview.dependencyCount).toBe(0);
      expect(preview.usageCount).toBe(0);
      expect(preview.toolCount).toBe(1);
    });
  });

  // ── 5. Record Invocation Routing ─────────────────────────────────

  describe('Record Invocation Routing', () => {
    it('routes sp- prefixed invocations to v2 store', () => {
      invoke('superpowers:record-invocation', 'sp-test', 'my_tool', 150, true);
      expect(mockStore.recordUsage).toHaveBeenCalledWith('sp-test');
      expect(mockRegistry.recordInvocation).not.toHaveBeenCalled();
    });

    it('records error in v2 store on failure', () => {
      invoke('superpowers:record-invocation', 'sp-test', 'my_tool', 200, false);
      expect(mockStore.recordUsage).toHaveBeenCalledWith('sp-test');
      expect(mockStore.recordError).toHaveBeenCalledWith('sp-test', 'Tool my_tool failed');
    });

    it('does NOT record error in v2 store on success', () => {
      invoke('superpowers:record-invocation', 'sp-test', 'my_tool', 100, true);
      expect(mockStore.recordError).not.toHaveBeenCalled();
    });

    it('routes non-sp invocations to v1 registry', () => {
      invoke('superpowers:record-invocation', 'git-1', 'analyze', 300, true);
      expect(mockRegistry.recordInvocation).toHaveBeenCalledWith('git-1', 'analyze', 300, true);
      expect(mockStore.recordUsage).not.toHaveBeenCalled();
    });

    it('ignores invocations with invalid IDs', () => {
      invoke('superpowers:record-invocation', '', 'tool', 100, true);
      expect(mockStore.recordUsage).not.toHaveBeenCalled();
      expect(mockRegistry.recordInvocation).not.toHaveBeenCalled();
    });

    it('ignores invocations with invalid tool names', () => {
      invoke('superpowers:record-invocation', 'sp-test', '', 100, true);
      expect(mockStore.recordUsage).not.toHaveBeenCalled();
      expect(mockRegistry.recordInvocation).not.toHaveBeenCalled();
    });
  });

  // ── 6. Input Validation ──────────────────────────────────────────

  describe('Input Validation', () => {
    it('superpowers:get rejects missing id', () => {
      expect(() => invoke('superpowers:get', '')).toThrow('requires a string id');
    });

    it('superpowers:get rejects non-string id', () => {
      expect(() => invoke('superpowers:get', 123)).toThrow('requires a string id');
    });

    it('superpowers:toggle rejects missing id', async () => {
      await expect(invoke('superpowers:toggle', '', true)).rejects.toThrow('requires a string id');
    });

    it('superpowers:toggle rejects non-boolean enabled', async () => {
      await expect(invoke('superpowers:toggle', 'id', 'yes')).rejects.toThrow('requires a boolean enabled');
    });

    it('superpowers:toggle-tool rejects missing superpowerId', () => {
      expect(() => invoke('superpowers:toggle-tool', '', 'tool', true)).toThrow('requires a string superpowerId');
    });

    it('superpowers:toggle-tool rejects missing toolName', () => {
      expect(() => invoke('superpowers:toggle-tool', 'sp-1', '', true)).toThrow('requires a string toolName');
    });

    it('superpowers:toggle-tool rejects non-boolean enabled', () => {
      expect(() => invoke('superpowers:toggle-tool', 'sp-1', 'tool', 'yes')).toThrow('requires a boolean enabled');
    });

    it('superpowers:update-permissions rejects missing id', () => {
      expect(() => invoke('superpowers:update-permissions', '', {})).toThrow('requires a string id');
    });

    it('superpowers:update-permissions rejects non-object perms', () => {
      expect(() => invoke('superpowers:update-permissions', 'id', 'not-obj')).toThrow('requires a permissions object');
    });

    it('superpowers:install rejects missing repoUrl', async () => {
      await expect(invoke('superpowers:install', '')).rejects.toThrow('requires a string repoUrl');
    });

    it('superpowers:uninstall rejects missing id', async () => {
      await expect(invoke('superpowers:uninstall', '')).rejects.toThrow('requires a string id');
    });

    it('superpowers:uninstall-preview rejects missing id', () => {
      expect(() => invoke('superpowers:uninstall-preview', '')).toThrow('requires a string id');
    });

    it('superpowers:usage-stats rejects missing id', () => {
      expect(() => invoke('superpowers:usage-stats', '')).toThrow('requires a string id');
    });

    it('superpowers:store-get rejects missing id', () => {
      expect(() => invoke('superpowers:store-get', '')).toThrow('requires a string id');
    });

    it('superpowers:store-confirm rejects missing id', () => {
      expect(() => invoke('superpowers:store-confirm', '', 'token')).toThrow('requires a string id');
    });

    it('superpowers:store-confirm rejects missing consent token', () => {
      expect(() => invoke('superpowers:store-confirm', 'sp-1', '')).toThrow('requires a non-empty consent token');
    });
  });

  // ── 7. v2 Store CRUD ─────────────────────────────────────────────

  describe('v2 Store CRUD', () => {
    it('store-list delegates to superpowerStore.getAll()', () => {
      const data = [{ id: 'sp-1' }, { id: 'sp-2' }];
      mockStore.getAll.mockReturnValue(data as any);
      expect(invoke('superpowers:store-list')).toEqual(data);
    });

    it('store-get delegates to superpowerStore.get()', () => {
      const sp = { id: 'sp-1', name: 'Test' };
      mockStore.get.mockReturnValue(sp as any);
      expect(invoke('superpowers:store-get', 'sp-1')).toEqual(sp);
    });

    it('store-confirm delegates to superpowerStore.confirmInstall()', () => {
      const confirmed = { id: 'sp-1', status: 'enabled' };
      mockStore.get.mockReturnValue(confirmed as any);
      const result = invoke('superpowers:store-confirm', 'sp-1', 'consent-abc');
      expect(mockStore.confirmInstall).toHaveBeenCalledWith('sp-1', 'consent-abc');
      expect(result).toEqual(confirmed);
    });

    it('store-enabled-tools delegates to superpowerStore.getEnabledTools()', () => {
      const tools = [{ name: 'tool_a' }, { name: 'tool_b' }];
      mockStore.getEnabledTools.mockReturnValue(tools as any);
      expect(invoke('superpowers:store-enabled-tools')).toEqual(tools);
    });

    it('store-status delegates to superpowerStore.getStatus()', () => {
      const status = { total: 5, enabled: 3 };
      mockStore.getStatus.mockReturnValue(status as any);
      expect(invoke('superpowers:store-status')).toEqual(status);
    });

    it('store-prompt-context delegates to superpowerStore.getPromptContext()', () => {
      mockStore.getPromptContext.mockReturnValue('## Superpowers\nActive tools...');
      expect(invoke('superpowers:store-prompt-context')).toBe('## Superpowers\nActive tools...');
    });

    it('store-needs-attention delegates to superpowerStore.getNeedingAttention()', () => {
      const needing = [{ id: 'sp-1', reason: 'errors' }];
      mockStore.getNeedingAttention.mockReturnValue(needing as any);
      expect(invoke('superpowers:store-needs-attention')).toEqual(needing);
    });
  });

  // ── 8. v1 Registry Delegation ────────────────────────────────────

  describe('v1 Registry Delegation', () => {
    it('list delegates to registry.listAll()', () => {
      const list = [{ id: 'git-1' }];
      mockRegistry.listAll.mockReturnValue(list as any);
      expect(invoke('superpowers:list')).toEqual(list);
    });

    it('get delegates to registry.get()', () => {
      const sp = { id: 'git-1', name: 'Repo' };
      mockRegistry.get.mockReturnValue(sp as any);
      expect(invoke('superpowers:get', 'git-1')).toEqual(sp);
    });

    it('toggle-tool delegates to registry.setToolEnabled()', () => {
      invoke('superpowers:toggle-tool', 'git-1', 'my_tool', false);
      expect(mockRegistry.setToolEnabled).toHaveBeenCalledWith('git-1', 'my_tool', false);
    });

    it('update-permissions delegates to registry.updatePermissions()', () => {
      const perms = { networkDomains: ['api.com'] };
      invoke('superpowers:update-permissions', 'git-1', perms);
      expect(mockRegistry.updatePermissions).toHaveBeenCalledWith('git-1', perms);
    });

    it('install delegates to registry.install()', async () => {
      await invoke('superpowers:install', 'https://github.com/user/repo');
      expect(mockRegistry.install).toHaveBeenCalledWith('https://github.com/user/repo');
    });

    it('install returns error object on failure', async () => {
      mockRegistry.install.mockRejectedValue(new Error('Clone failed'));
      const result = await invoke('superpowers:install', 'https://github.com/user/repo');
      expect(result).toEqual({ error: 'Clone failed' });
    });

    it('usage-stats delegates to registry.getUsageStats()', () => {
      invoke('superpowers:usage-stats', 'git-1');
      expect(mockRegistry.getUsageStats).toHaveBeenCalledWith('git-1');
    });

    it('enabled-tools delegates to registry.getAllEnabledTools()', () => {
      invoke('superpowers:enabled-tools');
      expect(mockRegistry.getAllEnabledTools).toHaveBeenCalled();
    });

    it('flush delegates to registry.flush()', async () => {
      await invoke('superpowers:flush');
      expect(mockRegistry.flush).toHaveBeenCalled();
    });
  });

  // ── 9. Error Handling ────────────────────────────────────────────

  describe('Error Handling', () => {
    it('install catches errors and returns error object', async () => {
      mockRegistry.install.mockRejectedValue(new Error('Network timeout'));
      const result = await invoke('superpowers:install', 'https://github.com/user/repo');
      expect(result).toEqual({ error: 'Network timeout' });
    });

    it('install handles non-Error throws', async () => {
      mockRegistry.install.mockRejectedValue('string error');
      const result = await invoke('superpowers:install', 'https://github.com/user/repo');
      expect(result).toEqual({ error: 'string error' });
    });

    it('install warns on non-standard repo URL but allows it', async () => {
      const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      await invoke('superpowers:install', 'https://my-git-server.com/repo');
      expect(consoleSpy).toHaveBeenCalledWith(
        expect.stringContaining('Non-standard repo URL'),
        expect.any(String),
      );
      consoleSpy.mockRestore();
    });
  });
});
