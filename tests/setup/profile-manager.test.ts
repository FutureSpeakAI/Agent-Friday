/**
 * profile-manager.test.ts -- Tests for ProfileManager singleton.
 *
 * Sprint 6 P.2: "The Identity" -- ProfileManager
 * 10 tests covering: create, get, set active, auto-active, update,
 * delete, export, import, profile shape, and event emission.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// -- Mocks (vi.hoisted) ------------------------------------------------------

const mockFs = vi.hoisted(() => ({
  existsSync: vi.fn(() => false),
  readFileSync: vi.fn(() => '{}'),
  writeFileSync: vi.fn(),
  mkdirSync: vi.fn(),
}));

const mockApp = vi.hoisted(() => ({
  getPath: vi.fn(() => '/mock/userData'),
}));

vi.mock('electron', () => ({
  app: mockApp,
}));

vi.mock('node:fs', () => ({
  default: mockFs,
  ...mockFs,
}));

// Stable UUID mock for deterministic tests
let uuidCounter = 0;
vi.mock('node:crypto', () => ({
  randomUUID: () => `test-uuid-${++uuidCounter}`,
}));

// -- Import under test -------------------------------------------------------

import { ProfileManager } from '../../../src/main/setup/profile-manager';
import type { UserProfile } from '../../../src/main/setup/profile-manager';

// -- Test Suite ---------------------------------------------------------------

describe('ProfileManager', () => {
  let pm: ProfileManager;

  beforeEach(() => {
    vi.clearAllMocks();
    uuidCounter = 0;
    mockFs.existsSync.mockReturnValue(false);
    mockFs.readFileSync.mockReturnValue('{}');
    ProfileManager.resetInstance();
    pm = ProfileManager.getInstance();
  });

  // Test 1: createProfile returns profile with generated ID
  it('createProfile({ name: "User" }) returns profile with generated ID', () => {
    const profile = pm.createProfile({ name: 'User' });

    expect(profile).toBeDefined();
    expect(profile.id).toBe('test-uuid-1');
    expect(profile.name).toBe('User');
    expect(typeof profile.createdAt).toBe('number');
    expect(typeof profile.updatedAt).toBe('number');
  });

  // Test 2: getActiveProfile returns null when no profiles exist
  it('getActiveProfile() returns null when no profiles exist', () => {
    const active = pm.getActiveProfile();
    expect(active).toBeNull();
  });

  // Test 3: setActiveProfile sets the active profile
  it('setActiveProfile(id) sets the active profile', () => {
    const p1 = pm.createProfile({ name: 'Alice' });
    const p2 = pm.createProfile({ name: 'Bob' });

    pm.setActiveProfile(p2.id);
    const active = pm.getActiveProfile();

    expect(active).not.toBeNull();
    expect(active!.id).toBe(p2.id);
    expect(active!.name).toBe('Bob');
  });

  // Test 4: First createProfile automatically becomes active
  it('first createProfile() automatically becomes active', () => {
    const profile = pm.createProfile({ name: 'FirstUser' });

    const active = pm.getActiveProfile();
    expect(active).not.toBeNull();
    expect(active!.id).toBe(profile.id);
  });

  // Test 5: updateProfile merges new data into existing profile
  it('updateProfile() merges new data into existing profile', () => {
    const profile = pm.createProfile({ name: 'User' });

    const updated = pm.updateProfile(profile.id, {
      name: 'UpdatedUser',
      preferences: {
        theme: 'dark',
        fontSize: 'large',
        cloudConsent: true,
        customKey: 'customValue',
      },
    });

    expect(updated.name).toBe('UpdatedUser');
    expect(updated.preferences.theme).toBe('dark');
    expect(updated.preferences.fontSize).toBe('large');
    expect(updated.preferences.cloudConsent).toBe(true);
    // Unknown keys preserved (extensible)
    expect((updated.preferences as Record<string, unknown>).customKey).toBe('customValue');
  });

  // Test 6: deleteProfile removes profile; switches active if deleted was active
  it('deleteProfile() soft-deletes; switches active if deleted was active', () => {
    const p1 = pm.createProfile({ name: 'Alice' });
    const p2 = pm.createProfile({ name: 'Bob' });

    // p1 is active (first created)
    expect(pm.getActiveProfile()!.id).toBe(p1.id);

    pm.deleteProfile(p1.id);

    // Should not be retrievable
    expect(pm.getProfile(p1.id)).toBeNull();

    // Active should switch to p2
    const active = pm.getActiveProfile();
    expect(active).not.toBeNull();
    expect(active!.id).toBe(p2.id);

    // List should only contain p2
    const all = pm.listProfiles();
    expect(all).toHaveLength(1);
    expect(all[0].id).toBe(p2.id);
  });

  // Test 7: exportProfile returns complete profile JSON including preferences
  it('exportProfile() returns complete profile JSON including preferences', () => {
    const profile = pm.createProfile({
      name: 'Exported',
      preferences: { theme: 'dark', fontSize: 'small', cloudConsent: true },
    });

    const json = pm.exportProfile(profile.id);
    const parsed = JSON.parse(json);

    expect(parsed.name).toBe('Exported');
    expect(parsed.id).toBe(profile.id);
    expect(parsed.preferences.theme).toBe('dark');
    expect(parsed.preferences.fontSize).toBe('small');
    expect(parsed.preferences.cloudConsent).toBe(true);
    expect(parsed.createdAt).toBeDefined();
    expect(parsed.updatedAt).toBeDefined();
  });

  // Test 8: importProfile creates new profile from exported JSON
  it('importProfile() creates new profile from exported JSON', () => {
    const original = pm.createProfile({ name: 'Original' });
    const json = pm.exportProfile(original.id);

    const imported = pm.importProfile(json);

    // New ID, different from original
    expect(imported.id).not.toBe(original.id);
    // Name preserved
    expect(imported.name).toBe('Original');
    // New timestamps
    expect(imported.createdAt).toBeGreaterThanOrEqual(original.createdAt);
    // Should appear in list
    const all = pm.listProfiles();
    expect(all.some((p) => p.id === imported.id)).toBe(true);
  });

  // Test 9: Profile includes required shape fields
  it('profile includes: name, createdAt, tierOverride, voiceProfileId, theme', () => {
    const profile = pm.createProfile({ name: 'ShapeTest' });

    expect(profile).toMatchObject({
      name: 'ShapeTest',
      tierOverride: null,
      voiceProfileId: null,
      deleted: false,
      preferences: {
        theme: 'system',
        fontSize: 'medium',
        cloudConsent: false,
      },
    });
    expect(typeof profile.id).toBe('string');
    expect(typeof profile.createdAt).toBe('number');
    expect(typeof profile.updatedAt).toBe('number');
  });

  // Test 10: Profile changes emit profile-changed event
  it('profile changes emit profile-changed event for other modules to react', () => {
    const changedCb = vi.fn();
    pm.on('profile-changed', changedCb);

    const p1 = pm.createProfile({ name: 'Alice' });
    const p2 = pm.createProfile({ name: 'Bob' });

    // Switching active should emit
    pm.setActiveProfile(p2.id);
    expect(changedCb).toHaveBeenCalledWith(
      expect.objectContaining({ id: p2.id, name: 'Bob' }),
    );

    // Updating active should emit
    changedCb.mockClear();
    pm.updateProfile(p2.id, { name: 'Bobby' });
    expect(changedCb).toHaveBeenCalledWith(
      expect.objectContaining({ id: p2.id, name: 'Bobby' }),
    );
  });
});
