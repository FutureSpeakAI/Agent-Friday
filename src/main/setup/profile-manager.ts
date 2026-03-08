/**
 * profile-manager.ts -- Singleton for user identity and preferences.
 *
 * Manages user profiles with soft delete, export/import (JSON), extensible
 * preferences, and file-based persistence to {userData}/profiles.json.
 *
 * Sprint 6 P.2: "The Identity" -- ProfileManager
 */

import { app } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import type { TierName } from '../hardware/tier-recommender';

// -- Contract Types -----------------------------------------------------------

export interface UserPreferences {
  theme: 'system' | 'light' | 'dark';
  fontSize: 'small' | 'medium' | 'large';
  cloudConsent: boolean;
  [key: string]: unknown;
}

export interface UserProfile {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  tierOverride: TierName | null;
  voiceProfileId: string | null;
  preferences: UserPreferences;
  deleted: boolean;
}

export interface CreateProfileOpts {
  name: string;
  preferences?: Partial<UserPreferences>;
}

// -- Defaults -----------------------------------------------------------------

const DEFAULT_PREFERENCES: UserPreferences = {
  theme: 'system',
  fontSize: 'medium',
  cloudConsent: false,
};

// -- Event Types --------------------------------------------------------------

export type ProfileEvent = 'profile-changed' | 'profile-created' | 'profile-deleted';

type ProfileChangedCallback = (profile: UserProfile) => void;
type ProfileCreatedCallback = (profile: UserProfile) => void;
type ProfileDeletedCallback = (data: { id: string }) => void;

type EventCallback = ProfileChangedCallback | ProfileCreatedCallback | ProfileDeletedCallback;

// -- Persistence Types --------------------------------------------------------

interface PersistedData {
  profiles: UserProfile[];
  activeProfileId: string | null;
}

// -- Persistence Helpers ------------------------------------------------------

function getProfilesFilePath(): string {
  return path.join(app.getPath('userData'), 'profiles.json');
}

function readPersistedData(): PersistedData {
  try {
    const filePath = getProfilesFilePath();
    if (!fs.existsSync(filePath)) return { profiles: [], activeProfileId: null };
    const raw = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(raw) as PersistedData;
  } catch {
    return { profiles: [], activeProfileId: null };
  }
}

function writePersistedData(data: PersistedData): void {
  const filePath = getProfilesFilePath();
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
}

// -- ProfileManager -----------------------------------------------------------

export class ProfileManager {
  private static instance: ProfileManager | null = null;

  private profiles: UserProfile[] = [];
  private activeProfileId: string | null = null;
  private listeners = new Map<ProfileEvent, Set<EventCallback>>();

  private constructor() {
    this.loadFromDisk();
  }

  static getInstance(): ProfileManager {
    if (!ProfileManager.instance) {
      ProfileManager.instance = new ProfileManager();
    }
    return ProfileManager.instance;
  }

  static resetInstance(): void {
    ProfileManager.instance = null;
  }

  // -- Public API -------------------------------------------------------------

  /** Create a new profile. First created profile auto-becomes active. */
  createProfile(opts: CreateProfileOpts): UserProfile {
    const now = Date.now();
    const profile: UserProfile = {
      id: randomUUID(),
      name: opts.name,
      createdAt: now,
      updatedAt: now,
      tierOverride: null,
      voiceProfileId: null,
      preferences: { ...DEFAULT_PREFERENCES, ...opts.preferences },
      deleted: false,
    };

    this.profiles.push(profile);

    // First non-deleted profile auto-becomes active
    const nonDeleted = this.profiles.filter((p) => !p.deleted);
    if (nonDeleted.length === 1) {
      this.activeProfileId = profile.id;
    }

    this.persist();
    this.emit('profile-created', { ...profile });

    return { ...profile };
  }

  /** Get a profile by ID (non-deleted only). */
  getProfile(id: string): UserProfile | null {
    const profile = this.profiles.find((p) => p.id === id && !p.deleted);
    return profile ? { ...profile, preferences: { ...profile.preferences } } : null;
  }

  /** Get the currently active profile, or null if none. */
  getActiveProfile(): UserProfile | null {
    if (!this.activeProfileId) return null;
    return this.getProfile(this.activeProfileId);
  }

  /** Switch the active profile. */
  setActiveProfile(id: string): void {
    const profile = this.profiles.find((p) => p.id === id && !p.deleted);
    if (!profile) {
      throw new Error('Profile not found: ' + id);
    }
    this.activeProfileId = id;
    this.persist();
    this.emit('profile-changed', { ...profile, preferences: { ...profile.preferences } });
  }

  /** Merge-update a profile, preserving unknown preference keys. */
  updateProfile(id: string, data: Partial<UserProfile>): UserProfile {
    const idx = this.profiles.findIndex((p) => p.id === id && !p.deleted);
    if (idx === -1) {
      throw new Error('Profile not found: ' + id);
    }

    const existing = this.profiles[idx];

    // Merge preferences (preserve unknown keys from both sides)
    const mergedPreferences = data.preferences
      ? { ...existing.preferences, ...data.preferences }
      : { ...existing.preferences };

    // Merge top-level fields (exclude id, createdAt, deleted from overwrite)
    const updated: UserProfile = {
      ...existing,
      ...data,
      id: existing.id,
      createdAt: existing.createdAt,
      deleted: existing.deleted,
      updatedAt: Date.now(),
      preferences: mergedPreferences,
    };

    this.profiles[idx] = updated;
    this.persist();

    // Emit if the updated profile is the active one
    if (this.activeProfileId === id) {
      this.emit('profile-changed', { ...updated, preferences: { ...updated.preferences } });
    }

    return { ...updated, preferences: { ...updated.preferences } };
  }

  /** Soft-delete a profile. If it was active, switch to another or null. */
  deleteProfile(id: string): void {
    const idx = this.profiles.findIndex((p) => p.id === id && !p.deleted);
    if (idx === -1) {
      throw new Error('Profile not found: ' + id);
    }

    this.profiles[idx].deleted = true;

    // If deleted profile was active, switch to another non-deleted profile or null
    if (this.activeProfileId === id) {
      const nextProfile = this.profiles.find((p) => !p.deleted);
      this.activeProfileId = nextProfile ? nextProfile.id : null;

      if (nextProfile) {
        this.emit('profile-changed', { ...nextProfile, preferences: { ...nextProfile.preferences } });
      }
    }

    this.persist();
    this.emit('profile-deleted', { id });
  }

  /** Export a profile as a JSON string. */
  exportProfile(id: string): string {
    const profile = this.profiles.find((p) => p.id === id && !p.deleted);
    if (!profile) {
      throw new Error('Profile not found: ' + id);
    }
    return JSON.stringify(profile, null, 2);
  }

  /** Import a profile from a JSON string. Creates a new profile with new ID and timestamps. */
  importProfile(json: string): UserProfile {
    const parsed = JSON.parse(json) as UserProfile;
    const now = Date.now();

    const profile: UserProfile = {
      ...parsed,
      id: randomUUID(),
      createdAt: now,
      updatedAt: now,
      deleted: false,
      preferences: { ...DEFAULT_PREFERENCES, ...parsed.preferences },
    };

    this.profiles.push(profile);

    // Auto-become active if first non-deleted profile
    const nonDeleted = this.profiles.filter((p) => !p.deleted);
    if (nonDeleted.length === 1) {
      this.activeProfileId = profile.id;
    }

    this.persist();
    this.emit('profile-created', { ...profile });

    return { ...profile };
  }

  /** List all non-deleted profiles. */
  listProfiles(): UserProfile[] {
    return this.profiles
      .filter((p) => !p.deleted)
      .map((p) => ({ ...p, preferences: { ...p.preferences } }));
  }

  /** Subscribe to profile events. Returns an unsubscribe function. */
  on(event: 'profile-changed', callback: ProfileChangedCallback): () => void;
  on(event: 'profile-created', callback: ProfileCreatedCallback): () => void;
  on(event: 'profile-deleted', callback: ProfileDeletedCallback): () => void;
  on(event: ProfileEvent, callback: EventCallback): () => void {
    const existing = this.listeners.get(event) ?? new Set<EventCallback>();
    existing.add(callback);
    this.listeners.set(event, existing);

    return () => {
      const callbacks = this.listeners.get(event);
      if (callbacks) {
        callbacks.delete(callback);
      }
    };
  }

  // -- Private Helpers --------------------------------------------------------

  /** Load profiles from disk. */
  private loadFromDisk(): void {
    const data = readPersistedData();
    this.profiles = data.profiles;
    this.activeProfileId = data.activeProfileId;
  }

  /** Persist profiles and active ID to disk. */
  private persist(): void {
    writePersistedData({
      profiles: this.profiles,
      activeProfileId: this.activeProfileId,
    });
  }

  /** Emit an event to all registered listeners. */
  private emit(event: ProfileEvent, data: unknown): void {
    const callbacks = this.listeners.get(event);
    if (!callbacks) return;
    for (const cb of callbacks) {
      try {
        (cb as (data: unknown) => void)(data);
      } catch {
        // Never let a listener crash the manager
      }
    }
  }
}
