/**
 * Tests for setup-handlers.ts — IPC layer for SetupWizard and ProfileManager.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

type IpcHandler = (...args: unknown[]) => unknown;
const handlers = new Map<string, IpcHandler>();
const mockSend = vi.fn();
vi.mock('electron', () => ({
  ipcMain: {
    handle: vi.fn((channel: string, handler: IpcHandler) => {
      handlers.set(channel, handler);
    }),
  },
}));

const mocks = vi.hoisted(() => ({
  isFirstRun: vi.fn().mockReturnValue(true),
  getSetupState: vi.fn().mockReturnValue({ step: 'welcome' }),
  startSetup: vi.fn().mockResolvedValue(undefined),
  skipSetup: vi.fn(),
  confirmTier: vi.fn(),
  startModelDownload: vi.fn().mockResolvedValue(undefined),
  getDownloadProgress: vi.fn().mockReturnValue({ percent: 0 }),
  completeSetup: vi.fn(),
  resetSetup: vi.fn(),
  wizardOn: vi.fn(),
  createProfile: vi.fn().mockReturnValue({ id: 'prof-1', name: 'Test' }),
  getProfile: vi.fn().mockReturnValue(null),
  getActiveProfile: vi.fn().mockReturnValue(null),
  setActiveProfile: vi.fn(),
  updateProfile: vi.fn().mockReturnValue(true),
  deleteProfile: vi.fn(),
  exportProfile: vi.fn().mockReturnValue('{}'),
  importProfile: vi.fn().mockReturnValue({ id: 'prof-2' }),
  listProfiles: vi.fn().mockReturnValue([]),
  profilesOn: vi.fn(),
}));

vi.mock('../../src/main/setup/setup-wizard', () => ({
  SetupWizard: {
    getInstance: () => ({
      isFirstRun: mocks.isFirstRun,
      getSetupState: mocks.getSetupState,
      startSetup: mocks.startSetup,
      skipSetup: mocks.skipSetup,
      confirmTier: mocks.confirmTier,
      startModelDownload: mocks.startModelDownload,
      getDownloadProgress: mocks.getDownloadProgress,
      completeSetup: mocks.completeSetup,
      resetSetup: mocks.resetSetup,
      on: mocks.wizardOn,
    }),
  },
}));

vi.mock('../../src/main/setup/profile-manager', () => ({
  ProfileManager: {
    getInstance: () => ({
      createProfile: mocks.createProfile,
      getProfile: mocks.getProfile,
      getActiveProfile: mocks.getActiveProfile,
      setActiveProfile: mocks.setActiveProfile,
      updateProfile: mocks.updateProfile,
      deleteProfile: mocks.deleteProfile,
      exportProfile: mocks.exportProfile,
      importProfile: mocks.importProfile,
      listProfiles: mocks.listProfiles,
      on: mocks.profilesOn,
    }),
  },
}));

vi.mock('../../src/main/ipc/validate', () => ({
  assertString: vi.fn((val: unknown, label: string) => {
    if (typeof val !== 'string' || val.length === 0) throw new Error(`${label} requires a string`);
  }),
  assertObject: vi.fn((val: unknown, label: string) => {
    if (!val || typeof val !== 'object' || Array.isArray(val)) throw new Error(`${label} requires an object`);
  }),
}));

import { registerSetupHandlers } from '../../src/main/ipc/setup-handlers';

function invoke(channel: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`No handler for ${channel}`);
  return handler({}, ...args);
}

describe('Setup Handlers — Sprint 7 IPC', () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
    registerSetupHandlers({
      getMainWindow: () => ({ webContents: { send: mockSend } } as any),
    });
  });

  describe('Handler Registration', () => {
    it('registers all expected channels', () => {
      const expected = [
        'setup:is-first-run', 'setup:get-state', 'setup:start', 'setup:skip',
        'setup:confirm-tier', 'setup:start-download', 'setup:get-download-progress',
        'setup:complete', 'setup:reset',
        'profile:create', 'profile:get', 'profile:get-active', 'profile:set-active',
        'profile:update', 'profile:delete', 'profile:export', 'profile:import', 'profile:list',
      ];
      for (const ch of expected) {
        expect(handlers.has(ch), `Missing handler: ${ch}`).toBe(true);
      }
    });

    it('registers exactly 18 handlers', () => {
      expect(handlers.size).toBe(18);
    });
  });

  describe('Setup Wizard', () => {
    it('isFirstRun delegates', () => {
      const result = invoke('setup:is-first-run');
      expect(mocks.isFirstRun).toHaveBeenCalled();
      expect(result).toBe(true);
    });

    it('confirmTier delegates with tier string', () => {
      invoke('setup:confirm-tier', 'full');
      expect(mocks.confirmTier).toHaveBeenCalledWith('full');
    });

    it('confirmTier rejects non-string', () => {
      expect(() => invoke('setup:confirm-tier', 42)).toThrow();
    });
  });

  describe('Profile Manager', () => {
    it('createProfile delegates with opts', () => {
      const opts = { name: 'Test', voiceId: 'v1' };
      invoke('profile:create', opts);
      expect(mocks.createProfile).toHaveBeenCalledWith(opts);
    });

    it('createProfile rejects missing name', () => {
      expect(() => invoke('profile:create', { voiceId: 'v1' })).toThrow();
    });

    it('setActive delegates with id', () => {
      invoke('profile:set-active', 'prof-1');
      expect(mocks.setActiveProfile).toHaveBeenCalledWith('prof-1');
    });

    it('update delegates with id + data', () => {
      invoke('profile:update', 'prof-1', { name: 'Updated' });
      expect(mocks.updateProfile).toHaveBeenCalledWith('prof-1', { name: 'Updated' });
    });

    it('import delegates with json string', () => {
      invoke('profile:import', '{"name":"Imported"}');
      expect(mocks.importProfile).toHaveBeenCalledWith('{"name":"Imported"}');
    });

    it('import rejects non-string', () => {
      expect(() => invoke('profile:import', 42)).toThrow();
    });
  });

  describe('Event Forwarding', () => {
    it('registers wizard events', () => {
      expect(mocks.wizardOn).toHaveBeenCalledWith('setup-state-changed', expect.any(Function));
      expect(mocks.wizardOn).toHaveBeenCalledWith('download-progress', expect.any(Function));
      expect(mocks.wizardOn).toHaveBeenCalledWith('setup-complete', expect.any(Function));
      expect(mocks.wizardOn).toHaveBeenCalledWith('setup-error', expect.any(Function));
    });

    it('registers profile events', () => {
      expect(mocks.profilesOn).toHaveBeenCalledWith('profile-changed', expect.any(Function));
      expect(mocks.profilesOn).toHaveBeenCalledWith('profile-created', expect.any(Function));
      expect(mocks.profilesOn).toHaveBeenCalledWith('profile-deleted', expect.any(Function));
    });
  });
});
