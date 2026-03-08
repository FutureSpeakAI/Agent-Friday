/**
 * voice-profile-manager.ts -- Voice selection and personalization manager.
 *
 * Manages voice profiles including selection, speed/pitch preferences,
 * and profile persistence via the settings system.
 *
 * Sprint 4 K.2: "The Timbre" -- VoiceProfileManager
 */

import { settingsManager } from '../settings';
import { ttsEngine } from './tts-engine';

// -- Types --------------------------------------------------------------------

export interface VoiceProfile {
  id: string;
  name: string;
  voiceId: string;       // References VoiceInfo.id from TTSEngine
  speed: number;         // 0.5 - 2.0, default 1.0
  pitch: number;         // -0.5 to 0.5, default 0.0
  volume: number;        // 0.0 - 1.0, default 1.0
  isDefault: boolean;
}

export interface CreateProfileOptions {
  name: string;
  voiceId: string;
  speed?: number;
  pitch?: number;
  volume?: number;
}

// -- Constants ----------------------------------------------------------------

const DEFAULT_PROFILE: VoiceProfile = {
  id: 'default',
  name: 'Default',
  voiceId: 'default',
  speed: 1.0,
  pitch: 0.0,
  volume: 1.0,
  isDefault: true,
};

const PREVIEW_TEXT = 'Hello, this is a voice preview.';

/** Speed range limits */
const MIN_SPEED = 0.5;
const MAX_SPEED = 2.0;

/** Pitch range limits */
const MIN_PITCH = -0.5;
const MAX_PITCH = 0.5;

/** Volume range limits */
const MIN_VOLUME = 0.0;
const MAX_VOLUME = 1.0;

// -- Helpers ------------------------------------------------------------------

/** Clamp a numeric value to a [min, max] range. */
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Generate a unique profile ID. */
let profileCounter = 0;
function generateProfileId(): string {
  profileCounter += 1;
  return 'profile-' + Date.now() + '-' + profileCounter;
}
// -- VoiceProfileManager ------------------------------------------------------

export class VoiceProfileManager {
  private static instance: VoiceProfileManager | null = null;

  private profiles: VoiceProfile[] = [{ ...DEFAULT_PROFILE }];
  private activeProfileId: string = 'default';

  private constructor() {
    // Load persisted profiles on construction
    this.loadFromSettings();
  }

  static getInstance(): VoiceProfileManager {
    if (!VoiceProfileManager.instance) {
      VoiceProfileManager.instance = new VoiceProfileManager();
    }
    return VoiceProfileManager.instance;
  }

  static resetInstance(): void {
    VoiceProfileManager.instance = null;
    profileCounter = 0;
  }

  // -- Public API -------------------------------------------------------------

  /** Get the currently active voice profile. */
  getActiveProfile(): VoiceProfile {
    const profile = this.profiles.find((p) => p.id === this.activeProfileId);
    return { ...(profile || DEFAULT_PROFILE) };
  }

  /** Switch the active voice profile by ID. */
  setActiveProfile(id: string): void {
    const profile = this.profiles.find((p) => p.id === id);
    if (!profile) {
      throw new Error('Profile not found: ' + id);
    }
    this.activeProfileId = id;
    this.persistActiveProfileId();
  }

  /** List all available voice profiles (default + custom). */
  listProfiles(): VoiceProfile[] {
    return this.profiles.map((p) => ({ ...p }));
  }

  /** Create a new custom voice profile. */
  createProfile(opts: CreateProfileOptions): VoiceProfile {
    const profile: VoiceProfile = {
      id: generateProfileId(),
      name: opts.name,
      voiceId: opts.voiceId,
      speed: clamp(opts.speed ?? 1.0, MIN_SPEED, MAX_SPEED),
      pitch: clamp(opts.pitch ?? 0.0, MIN_PITCH, MAX_PITCH),
      volume: clamp(opts.volume ?? 1.0, MIN_VOLUME, MAX_VOLUME),
      isDefault: false,
    };

    this.profiles.push(profile);
    this.persistProfiles();
    return { ...profile };
  }

  /**
   * Delete a voice profile by ID.
   * Returns false if trying to delete the default profile.
   */
  deleteProfile(id: string): boolean {
    // Cannot delete the default profile
    const profile = this.profiles.find((p) => p.id === id);
    if (!profile || profile.isDefault) {
      return false;
    }

    this.profiles = this.profiles.filter((p) => p.id !== id);

    // If the deleted profile was active, fall back to default
    if (this.activeProfileId === id) {
      this.activeProfileId = 'default';
      this.persistActiveProfileId();
    }

    this.persistProfiles();
    return true;
  }

  /**
   * Generate a short audio preview for a given profile.
   * Calls ttsEngine.synthesize() with the profile voice settings.
   */
  async previewVoice(profileId: string): Promise<Float32Array> {
    const profile = this.profiles.find((p) => p.id === profileId);
    if (!profile) {
      throw new Error('Profile not found: ' + profileId);
    }

    const audio = await ttsEngine.synthesize(PREVIEW_TEXT, {
      voiceId: profile.voiceId,
      speed: profile.speed,
      pitch: profile.pitch,
    });

    return audio;
  }
  // -- Persistence helpers ----------------------------------------------------

  /** Load profiles and active ID from the settings system. */
  private loadFromSettings(): void {
    try {
      const settings = settingsManager.get();
      // Voice profile settings are stored as extended properties
      // Cast through unknown to access dynamic keys
      const settingsAny = settings as unknown as Record<string, unknown>;
      const savedProfiles = settingsAny['voice.profiles'] as VoiceProfile[] | undefined;
      const savedActiveId = settingsAny['voice.activeProfileId'] as string | undefined;

      if (Array.isArray(savedProfiles) && savedProfiles.length > 0) {
        // Ensure default profile is always present
        const hasDefault = savedProfiles.some((p) => p.id === 'default');
        this.profiles = hasDefault
          ? savedProfiles
          : [{ ...DEFAULT_PROFILE }, ...savedProfiles];
      }

      if (savedActiveId && this.profiles.some((p) => p.id === savedActiveId)) {
        this.activeProfileId = savedActiveId;
      }
    } catch {
      // Settings not available yet -- use defaults
    }
  }

  /** Persist the profiles array to the settings system. */
  private persistProfiles(): void {
    void settingsManager.setSetting('voice.profiles', this.profiles as unknown);
  }

  /** Persist the active profile ID to the settings system. */
  private persistActiveProfileId(): void {
    void settingsManager.setSetting('voice.activeProfileId', this.activeProfileId as unknown);
  }
}

// -- Singleton export ---------------------------------------------------------

export const voiceProfileManager = VoiceProfileManager.getInstance();
