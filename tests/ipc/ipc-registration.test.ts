/**
 * Track D, Phase 2: "The Switchboard" — IPC Handler Registration Tests
 *
 * Validates that the 4 missing IPC handler groups are properly
 * registered in the main process boot sequence.
 *
 * Tests verify handler modules directly since index.ts boot logic
 * can't be imported in isolation (same pattern as D.1).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  ipcMainHandle: vi.fn(),
  ipcMainRemoveHandler: vi.fn(),
  contextStreamOn: vi.fn(() => vi.fn()),
  contextGraphGetActiveStream: vi.fn(() => null),
  contextGraphGetRecentStreams: vi.fn(() => []),
  contextGraphGetTopEntities: vi.fn(() => []),
  briefingDeliveryGetRecentBriefings: vi.fn(() => []),
  briefingDeliveryDismissBriefing: vi.fn(),
  executionDelegateExecute: vi.fn(),
  executionDelegateExecuteAfterConfirmation: vi.fn(),
  toolRegistryListTools: vi.fn(() => []),
  liveContextBridgeGetContextForApp: vi.fn(() => ({
    activeStream: null,
    entities: [],
    briefingSummary: null,
  })),
  webContentsSend: vi.fn(),
  isDestroyed: vi.fn(() => false),
}));

vi.mock('electron', () => ({
  ipcMain: {
    handle: mocks.ipcMainHandle,
    removeHandler: mocks.ipcMainRemoveHandler,
    on: vi.fn(),
  },
  app: {
    getPath: () => '/tmp/test',
    on: vi.fn(),
    whenReady: () => Promise.resolve(),
  },
  BrowserWindow: vi.fn(),
  globalShortcut: { register: vi.fn(), unregisterAll: vi.fn() },
  shell: { openExternal: vi.fn() },
  dialog: { showOpenDialog: vi.fn() },
  nativeTheme: { shouldUseDarkColors: false },
  screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 1920, height: 1080 } }) },
}));

vi.mock('../../src/main/context-stream', () => ({
  contextStream: {
    on: mocks.contextStreamOn,
  },
}));

vi.mock('../../src/main/context-graph', () => ({
  contextGraph: {
    getActiveStream: mocks.contextGraphGetActiveStream,
    getRecentStreams: mocks.contextGraphGetRecentStreams,
    getTopEntities: mocks.contextGraphGetTopEntities,
  },
}));

vi.mock('../../src/main/briefing-delivery', () => ({
  briefingDelivery: {
    getRecentBriefings: mocks.briefingDeliveryGetRecentBriefings,
    dismissBriefing: mocks.briefingDeliveryDismissBriefing,
  },
}));

vi.mock('../../src/main/execution-delegate', () => ({
  executionDelegate: {
    execute: mocks.executionDelegateExecute,
    executeAfterConfirmation: mocks.executionDelegateExecuteAfterConfirmation,
  },
}));

vi.mock('../../src/main/tool-registry', () => ({
  toolRegistry: {
    listTools: mocks.toolRegistryListTools,
  },
}));

vi.mock('../../src/main/live-context-bridge', () => ({
  liveContextBridge: {
    getContextForApp: mocks.liveContextBridgeGetContextForApp,
  },
}));

describe('IPC Handler Registration — The Switchboard', () => {
  const mockMainWindow = {
    webContents: {
      send: mocks.webContentsSend,
      isDestroyed: mocks.isDestroyed,
    },
  } as any;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // D.2 Validation Criterion 7: All 4 handlers importable from barrel
  describe('barrel exports', () => {
    it('exports all 4 missing handler registration functions', { timeout: 30_000 }, async () => {
      const ipc = await import('../../src/main/ipc');
      expect(typeof ipc.registerExecutionDelegateHandlers).toBe('function');
      expect(typeof ipc.registerAppContextHandlers).toBe('function');
      expect(typeof ipc.registerContextPushHandlers).toBe('function');
      expect(typeof ipc.registerBriefingDeliveryHandlers).toBe('function');
    });

    it('exports ContextPushCleanup type (type-level)', async () => {
      // Type check: the barrel exports the type; this is a compile-time check
      const ipc = await import('../../src/main/ipc');
      const cleanup = ipc.registerContextPushHandlers(mockMainWindow);
      expect(typeof cleanup).toBe('function');
    });
  });

  // D.2 Validation Criteria 1-4: Handler registration calls
  describe('registerExecutionDelegateHandlers', () => {
    it('registers tool:execute, tool:confirm-response, tool:list-tools channels', async () => {
      const { registerExecutionDelegateHandlers } = await import(
        '../../src/main/ipc/execution-delegate-handlers'
      );
      registerExecutionDelegateHandlers();

      const channels = mocks.ipcMainHandle.mock.calls.map((c: any[]) => c[0]);
      expect(channels).toContain('tool:execute');
      expect(channels).toContain('tool:confirm-response');
      expect(channels).toContain('tool:list-tools');
    });
  });

  describe('registerAppContextHandlers', () => {
    it('registers app-context:get channel', async () => {
      const { registerAppContextHandlers } = await import(
        '../../src/main/ipc/app-context-handlers'
      );
      registerAppContextHandlers();

      const channels = mocks.ipcMainHandle.mock.calls.map((c: any[]) => c[0]);
      expect(channels).toContain('app-context:get');
    });
  });

  describe('registerBriefingDeliveryHandlers', () => {
    it('registers briefing:list and briefing:dismiss channels', async () => {
      const { registerBriefingDeliveryHandlers } = await import(
        '../../src/main/ipc/briefing-delivery-handlers'
      );
      registerBriefingDeliveryHandlers();

      const channels = mocks.ipcMainHandle.mock.calls.map((c: any[]) => c[0]);
      expect(channels).toContain('briefing:list');
      expect(channels).toContain('briefing:dismiss');
    });
  });

  describe('registerContextPushHandlers', () => {
    it('subscribes to context stream and registers subscribe/unsubscribe channels', async () => {
      const { registerContextPushHandlers } = await import(
        '../../src/main/ipc/context-push-handlers'
      );
      registerContextPushHandlers(mockMainWindow);

      expect(mocks.contextStreamOn).toHaveBeenCalledOnce();
      const channels = mocks.ipcMainHandle.mock.calls.map((c: any[]) => c[0]);
      expect(channels).toContain('context:subscribe');
      expect(channels).toContain('context:unsubscribe');
    });

    // D.2 Socratic: Constraint Discovery — cleanup function
    it('returns a cleanup function that unsubscribes', async () => {
      const unsubscribe = vi.fn();
      mocks.contextStreamOn.mockReturnValue(unsubscribe);

      const { registerContextPushHandlers } = await import(
        '../../src/main/ipc/context-push-handlers'
      );
      const cleanup = registerContextPushHandlers(mockMainWindow);

      expect(typeof cleanup).toBe('function');
      cleanup();
      expect(unsubscribe).toHaveBeenCalledOnce();
    });

    // D.2 Validation Criterion 8: Idempotent cleanup
    it('cleanup is idempotent — calling twice only unsubscribes once', async () => {
      const unsubscribe = vi.fn();
      mocks.contextStreamOn.mockReturnValue(unsubscribe);

      const { registerContextPushHandlers } = await import(
        '../../src/main/ipc/context-push-handlers'
      );
      const cleanup = registerContextPushHandlers(mockMainWindow);

      cleanup();
      cleanup();
      expect(unsubscribe).toHaveBeenCalledOnce();
    });
  });
});
