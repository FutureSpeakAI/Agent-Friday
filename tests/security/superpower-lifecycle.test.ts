import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('electron', () => ({ app: { getPath: () => '/tmp/test' } }));
vi.mock('fs/promises', () => ({ default: { readFile: vi.fn(), writeFile: vi.fn(), mkdir: vi.fn() } }));
vi.mock('crypto', () => ({ default: { randomUUID: () => 'aaaabbbb-cccc-dddd-eeee-ffffffffffff' } }));
vi.mock('../../src/main/adapter-engine', () => ({
  validateAdaptedConnector: vi.fn(() => ({ valid: true, errors: [] })),
}));

import { SuperpowerStore, type Superpower, type SuperpowerStatus } from '../../src/main/superpower-store';
import { validateAdaptedConnector } from '../../src/main/adapter-engine';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConnector(id = 'sp-test') {
  return {
    id,
    label: 'Test SP',
    description: 'A test superpower',
    category: 'devops' as const,
    tools: [{ name: 'test_tool', description: 'test', parameters: { type: 'object', properties: {} } }],
    sourceCode: 'export default {}',
    bridgeScript: undefined,
    sandbox: { network: false, filesystem: 'none' as const, maxMemoryMb: 128, timeoutMs: 5000 },
    dependencies: [],
    plan: { repoName: 'test-repo', strategy: { type: 'direct', reason: 'TypeScript direct' }, steps: [], tools: [] },
  };
}

function makeVerdict(approved = true) {
  return { approved, riskLevel: 'low' as const, findings: 0, scannedAt: Date.now() };
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('SuperpowerStore lifecycle', () => {
  let store: SuperpowerStore;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-15T12:00:00Z'));
    store = new SuperpowerStore();
    vi.mocked(validateAdaptedConnector).mockReturnValue({ valid: true, errors: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // =========================================================================
  // 1. Prepare Install
  // =========================================================================
  describe('Prepare Install', () => {
    it('creates superpower in pending-consent status', () => {
      const sp = store.prepareInstall(makeConnector(), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(sp.status).toBe('pending-consent');
    });

    it('sets enabled to false before consent', () => {
      const sp = store.prepareInstall(makeConnector(), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(sp.enabled).toBe(false);
    });

    it('assigns connector fields (name, description, tools, sourceCode)', () => {
      const connector = makeConnector();
      const sp = store.prepareInstall(connector, makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(sp.name).toBe(connector.label);
      expect(sp.description).toBe(connector.description);
      expect(sp.tools).toEqual(connector.tools);
      expect(sp.sourceCode).toBe(connector.sourceCode);
    });

    it('throws if connector validation fails', () => {
      vi.mocked(validateAdaptedConnector).mockReturnValue({ valid: false, errors: ['bad connector'] });
      expect(() => store.prepareInstall(makeConnector(), makeVerdict(), 'https://example.com/repo', 'abc123'))
        .toThrow();
    });

    it('throws if maxSuperpowers limit reached', () => {
      const limited = new SuperpowerStore({ maxSuperpowers: 1, healthThreshold: 10, allowUnsigned: false });
      limited.prepareInstall(makeConnector('sp-1'), makeVerdict(), 'https://example.com/repo', 'abc123');
      limited.confirmInstall('sp-1', 'consent-token-1');

      expect(() => limited.prepareInstall(makeConnector('sp-2'), makeVerdict(), 'https://example.com/repo', 'abc123'))
        .toThrow();
    });

    it('throws if security verdict rejected and allowUnsigned is false', () => {
      const strict = new SuperpowerStore({ maxSuperpowers: 100, healthThreshold: 10, allowUnsigned: false });
      expect(() => strict.prepareInstall(makeConnector(), makeVerdict(false), 'https://example.com/repo', 'abc123'))
        .toThrow();
    });

    it('throws if already installed with same ID', () => {
      store.prepareInstall(makeConnector('sp-dup'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-dup', 'consent-token');
      expect(() => store.prepareInstall(makeConnector('sp-dup'), makeVerdict(), 'https://example.com/repo', 'abc123'))
        .toThrow();
    });

    it('consentToken is empty string before consent', () => {
      const sp = store.prepareInstall(makeConnector(), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(sp.consentToken).toBe('');
    });
  });

  // =========================================================================
  // 2. Confirm Install -- cLaw consent gate
  // =========================================================================
  describe('Confirm Install -- cLaw consent gate', () => {
    it('sets status to installed and enabled to true', () => {
      store.prepareInstall(makeConnector('sp-a'), makeVerdict(), 'https://example.com/repo', 'abc123');
      const sp = store.confirmInstall('sp-a', 'valid-consent-token');
      expect(sp.status).toBe('installed');
      expect(sp.enabled).toBe(true);
    });

    it('records consentToken and consentedAt', () => {
      store.prepareInstall(makeConnector('sp-b'), makeVerdict(), 'https://example.com/repo', 'abc123');
      const sp = store.confirmInstall('sp-b', 'my-consent-token');
      expect(sp.consentToken).toBe('my-consent-token');
      expect(sp.consentedAt).toBeDefined();
      expect(typeof sp.consentedAt).toBe('number');
    });

    it('throws if consentToken is empty string', () => {
      store.prepareInstall(makeConnector('sp-c'), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(() => store.confirmInstall('sp-c', '')).toThrow();
    });

    it('throws if consentToken is whitespace only', () => {
      store.prepareInstall(makeConnector('sp-d'), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(() => store.confirmInstall('sp-d', '   ')).toThrow();
    });

    it('throws if superpower not found', () => {
      expect(() => store.confirmInstall('nonexistent-id', 'token')).toThrow();
    });

    it('throws if status is not pending-consent', () => {
      store.prepareInstall(makeConnector('sp-e'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-e', 'token-1');
      expect(() => store.confirmInstall('sp-e', 'token-2')).toThrow();
    });

    it('sets installedAt timestamp', () => {
      store.prepareInstall(makeConnector('sp-f'), makeVerdict(), 'https://example.com/repo', 'abc123');
      const now = Date.now();
      const sp = store.confirmInstall('sp-f', 'consent-token');
      expect(sp.installedAt).toBe(now);
    });
  });

  // =========================================================================
  // 3. Enable/Disable lifecycle
  // =========================================================================
  describe('Enable/Disable lifecycle', () => {
    beforeEach(() => {
      store.prepareInstall(makeConnector('sp-toggle'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-toggle', 'consent-token');
    });

    it('enableSuperpower sets enabled to true', () => {
      store.disableSuperpower('sp-toggle');
      const sp = store.enableSuperpower('sp-toggle');
      expect(sp.enabled).toBe(true);
    });

    it('disableSuperpower sets enabled to false', () => {
      const sp = store.disableSuperpower('sp-toggle');
      expect(sp.enabled).toBe(false);
    });

    it('enableSuperpower throws if status is not installed', () => {
      const store2 = new SuperpowerStore();
      store2.prepareInstall(makeConnector('sp-pending'), makeVerdict(), 'https://example.com/repo', 'abc123');
      expect(() => store2.enableSuperpower('sp-pending')).toThrow();
    });

    it('enableSuperpower throws for unknown id', () => {
      expect(() => store.enableSuperpower('no-such-id')).toThrow();
    });

    it('getEnabled returns only enabled superpowers', () => {
      store.prepareInstall(makeConnector('sp-disabled'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-disabled', 'consent');
      store.disableSuperpower('sp-disabled');

      const enabled = store.getEnabled();
      expect(enabled.length).toBe(1);
      expect(enabled[0].id).toBe('sp-toggle');
    });

    it('getEnabledTools returns tools from enabled superpowers', () => {
      const tools = store.getEnabledTools();
      expect(tools.length).toBe(1);
      expect(tools[0].name).toBe('test_tool');
    });
  });

  // =========================================================================
  // 4. Uninstall
  // =========================================================================
  describe('Uninstall', () => {
    beforeEach(() => {
      store.prepareInstall(makeConnector('sp-remove'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-remove', 'consent-token');
    });

    it('removes superpower from store', () => {
      store.uninstallSuperpower('sp-remove');
      const status = store.getStatus();
      expect(status.totalInstalled).toBe(0);
    });

    it('getEnabled no longer returns it', () => {
      store.uninstallSuperpower('sp-remove');
      const enabled = store.getEnabled();
      expect(enabled.find((s) => s.id === 'sp-remove')).toBeUndefined();
    });

    it('getEnabledTools no longer returns its tools', () => {
      store.uninstallSuperpower('sp-remove');
      const tools = store.getEnabledTools();
      expect(tools.find((t) => t.name === 'test_tool')).toBeUndefined();
    });

    it('uninstalling non-existent ID does not throw', () => {
      expect(() => store.uninstallSuperpower('ghost-id')).not.toThrow();
    });
  });

  // =========================================================================
  // 5. Usage & Health tracking
  // =========================================================================
  describe('Usage & Health tracking', () => {
    beforeEach(() => {
      store.prepareInstall(makeConnector('sp-usage'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-usage', 'consent-token');
    });

    it('recordUsage increments usageCount', () => {
      store.recordUsage('sp-usage');
      store.recordUsage('sp-usage');
      store.recordUsage('sp-usage');
      const enabled = store.getEnabled();
      const sp = enabled.find((s) => s.id === 'sp-usage')!;
      expect(sp.usageCount).toBe(3);
    });

    it('recordUsage updates lastUsedAt', () => {
      vi.setSystemTime(new Date('2026-06-01T00:00:00Z'));
      store.recordUsage('sp-usage');
      const enabled = store.getEnabled();
      const sp = enabled.find((s) => s.id === 'sp-usage')!;
      expect(sp.lastUsedAt).toBe(new Date('2026-06-01T00:00:00Z').getTime());
    });

    it('recordError increments errorCount', () => {
      store.recordError('sp-usage', 'Something went wrong');
      store.recordError('sp-usage', 'Another error');
      const enabled = store.getEnabled();
      const sp = enabled.find((s) => s.id === 'sp-usage')!;
      expect(sp.health.errorCount).toBe(2);
    });

    it('recordError stores lastError message', () => {
      store.recordError('sp-usage', 'Timeout exceeded');
      const all = store.getAll();
      const sp = all.find((s) => s.id === 'sp-usage')!;
      expect(sp.health.lastError).toBe('Timeout exceeded');
    });

    it('auto-disables and adds warning after autoDisableAfterErrors threshold', () => {
      const strict = new SuperpowerStore({ maxSuperpowers: 100, autoDisableAfterErrors: 3, allowUnsigned: false });
      strict.prepareInstall(makeConnector('sp-ad'), makeVerdict(), 'https://example.com/repo', 'abc123');
      strict.confirmInstall('sp-ad', 'consent-token');

      strict.recordError('sp-ad', 'err1');
      strict.recordError('sp-ad', 'err2');
      strict.recordError('sp-ad', 'err3'); // triggers auto-disable

      const sp = strict.getAll().find((s) => s.id === 'sp-ad')!;
      expect(sp.enabled).toBe(false);
      expect(sp.health.warnings.length).toBeGreaterThanOrEqual(1);
      expect(sp.health.warnings.some((w: string) => w.includes('Auto-disabled'))).toBe(true);
    });

    it('recordUsage for non-existent ID does not throw', () => {
      expect(() => store.recordUsage('no-such-sp')).not.toThrow();
    });
  });

  // =========================================================================
  // 6. Export/Import
  // =========================================================================
  describe('Export/Import', () => {
    it('exportAll returns valid JSON', () => {
      store.prepareInstall(makeConnector('sp-exp'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-exp', 'consent-token');

      const json = store.exportAll();
      expect(() => JSON.parse(json)).not.toThrow();
    });

    it('importAll restores superpowers', () => {
      store.prepareInstall(makeConnector('sp-imp'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-imp', 'consent-token');
      const json = store.exportAll();

      const newStore = new SuperpowerStore();
      const result = newStore.importAll(json);
      expect(result.imported).toBeGreaterThanOrEqual(1);

      const enabled = newStore.getEnabled();
      expect(enabled.find((s) => s.id === 'sp-imp')).toBeDefined();
    });

    it('importAll skips already-installed IDs', () => {
      store.prepareInstall(makeConnector('sp-dup-imp'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-dup-imp', 'consent-token');
      const json = store.exportAll();

      const result = store.importAll(json);
      expect(result.skipped).toBeGreaterThanOrEqual(1);
    });

    it('importAll returns count stats', () => {
      store.prepareInstall(makeConnector('sp-stats'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-stats', 'consent-token');
      const json = store.exportAll();

      const newStore = new SuperpowerStore();
      const result = newStore.importAll(json);
      expect(result).toHaveProperty('imported');
      expect(result).toHaveProperty('skipped');
      expect(result).toHaveProperty('errors');
    });

    it('importAll with invalid JSON returns errors', () => {
      const result = store.importAll('this is not json at all {{{');
      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.imported).toBe(0);
    });
  });

  // =========================================================================
  // 7. Status
  // =========================================================================
  describe('Status', () => {
    it('getStatus returns correct counts', () => {
      store.prepareInstall(makeConnector('sp-s1'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-s1', 'consent-token');

      store.prepareInstall(makeConnector('sp-s2'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-s2', 'consent-token');
      store.disableSuperpower('sp-s2');

      const status = store.getStatus();
      expect(status.totalInstalled).toBe(2);
      expect(status.totalEnabled).toBe(1);
      // disabled = totalInstalled - totalEnabled
      expect(status.totalInstalled - status.totalEnabled).toBe(1);
    });

    it('getStatus totalTools matches enabled tools', () => {
      store.prepareInstall(makeConnector('sp-t1'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-t1', 'consent-token');

      store.prepareInstall(makeConnector('sp-t2'), makeVerdict(), 'https://example.com/repo', 'abc123');
      store.confirmInstall('sp-t2', 'consent-token');

      const status = store.getStatus();
      const tools = store.getEnabledTools();
      expect(status.totalTools).toBe(tools.length);
    });

    it('getStatus with empty store', () => {
      const status = store.getStatus();
      expect(status.totalInstalled).toBe(0);
      expect(status.totalEnabled).toBe(0);
      expect(status.totalTools).toBe(0);
    });
  });
});
