/**
 * VoiceProfileManager -- Unit tests for voice selection and personalization.
 *
 * Sprint 4 K.2: "The Timbre" -- VoiceProfileManager
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// -- Hoisted mocks (vi.hoisted pattern) --------------------------------------

const mocks = vi.hoisted(() => {
  const store = new Map<string, unknown>();
  return {
    store,
    settingsManager: {
      get: vi.fn(() => ({})),
      setSetting: vi.fn(),
    },
    ttsEngine: {
      synthesize: vi.fn(),
      isReady: vi.fn(() => true),
      getAvailableVoices: vi.fn(() => [
        { id: 'default', name: 'Default', language: 'en', backend: 'kokoro' as const, sampleRate: 24000 },
        { id: 'en-us-1', name: 'En Us 1', language: 'en', backend: 'kokoro' as const, sampleRate: 24000 },
      ]),
    },
  };
});

vi.mock('../../../src/main/settings', () => ({
  settingsManager: mocks.settingsManager,
}));

vi.mock('../../../src/main/voice/tts-engine', () => ({
  ttsEngine: mocks.ttsEngine,
  TTSEngine: { getInstance: () => mocks.ttsEngine },
}));

import {
  VoiceProfileManager,
  voiceProfileManager,
} from '../../../src/main/voice/voice-profile-manager';
import type { VoiceProfile } from '../../../src/main/voice/voice-profile-manager';
// -- Helpers ------------------------------------------------------------------

function createMockPCM(samples = 12000): Float32Array {
  const buf = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    buf[i] = Math.sin(2 * Math.PI * 440 * (i / 24000)) * 0.3;
  }
  return buf;
}

// -- Tests --------------------------------------------------------------------

describe('VoiceProfileManager', () => {
  beforeEach(() => {
    VoiceProfileManager.resetInstance();
    vi.clearAllMocks();
    mocks.store.clear();
  });

  // 1. getActiveProfile() returns default profile when none set
  it('getActiveProfile() returns default profile when none set', () => {
    const mgr = VoiceProfileManager.getInstance();
    const profile = mgr.getActiveProfile();

    expect(profile).toBeDefined();
    expect(profile.id).toBe('default');
    expect(profile.name).toBe('Default');
    expect(profile.voiceId).toBe('default');
    expect(profile.speed).toBe(1.0);
    expect(profile.pitch).toBe(0.0);
    expect(profile.volume).toBe(1.0);
    expect(profile.isDefault).toBe(true);
  });

  // 2. setActiveProfile(id) switches the active voice
  it('setActiveProfile(id) switches the active voice', () => {
    const mgr = VoiceProfileManager.getInstance();
    const created = mgr.createProfile({ name: 'Custom', voiceId: 'en-us-1' });
    mgr.setActiveProfile(created.id);

    const active = mgr.getActiveProfile();
    expect(active.id).toBe(created.id);
    expect(active.name).toBe('Custom');
    expect(active.voiceId).toBe('en-us-1');
  });

  // 3. listProfiles() includes default + any custom profiles
  it('listProfiles() includes default + any custom profiles', () => {
    const mgr = VoiceProfileManager.getInstance();
    mgr.createProfile({ name: 'Voice A', voiceId: 'en-us-1' });
    mgr.createProfile({ name: 'Voice B', voiceId: 'default' });

    const profiles = mgr.listProfiles();
    expect(profiles.length).toBe(3); // default + 2 custom
    expect(profiles[0].id).toBe('default');
    expect(profiles[0].isDefault).toBe(true);
    expect(profiles.some((p) => p.name === 'Voice A')).toBe(true);
    expect(profiles.some((p) => p.name === 'Voice B')).toBe(true);
  });
  // 4. createProfile({name, voiceId, speed, pitch}) persists a new profile
  it('createProfile() persists a new profile', () => {
    const mgr = VoiceProfileManager.getInstance();
    const profile = mgr.createProfile({
      name: 'My Voice',
      voiceId: 'en-us-1',
      speed: 1.5,
      pitch: 0.2,
    });

    expect(profile.id).toBeTruthy();
    expect(profile.id).not.toBe('default');
    expect(profile.name).toBe('My Voice');
    expect(profile.voiceId).toBe('en-us-1');
    expect(profile.speed).toBe(1.5);
    expect(profile.pitch).toBe(0.2);
    expect(profile.volume).toBe(1.0); // default volume
    expect(profile.isDefault).toBe(false);

    // Verify persistence was called
    expect(mocks.settingsManager.setSetting).toHaveBeenCalled();
  });

  // 5. deleteProfile(id) removes a custom profile
  it('deleteProfile(id) removes a custom profile', () => {
    const mgr = VoiceProfileManager.getInstance();
    const created = mgr.createProfile({ name: 'Temp', voiceId: 'default' });
    expect(mgr.listProfiles().length).toBe(2);

    const result = mgr.deleteProfile(created.id);
    expect(result).toBe(true);
    expect(mgr.listProfiles().length).toBe(1);
    expect(mgr.listProfiles()[0].id).toBe('default');
  });

  // 6. Default profile cannot be deleted (returns false)
  it('default profile cannot be deleted (returns false)', () => {
    const mgr = VoiceProfileManager.getInstance();
    const result = mgr.deleteProfile('default');
    expect(result).toBe(false);
    expect(mgr.listProfiles().length).toBe(1);
    expect(mgr.listProfiles()[0].id).toBe('default');
  });
  // 7. Speed adjustment (0.5x - 2.0x) is stored in profile
  it('speed adjustment (0.5x - 2.0x) is stored in profile', () => {
    const mgr = VoiceProfileManager.getInstance();

    const slow = mgr.createProfile({ name: 'Slow', voiceId: 'default', speed: 0.5 });
    expect(slow.speed).toBe(0.5);

    const fast = mgr.createProfile({ name: 'Fast', voiceId: 'default', speed: 2.0 });
    expect(fast.speed).toBe(2.0);

    // Verify clamping: values outside range are clamped
    const tooSlow = mgr.createProfile({ name: 'TooSlow', voiceId: 'default', speed: 0.1 });
    expect(tooSlow.speed).toBe(0.5);

    const tooFast = mgr.createProfile({ name: 'TooFast', voiceId: 'default', speed: 5.0 });
    expect(tooFast.speed).toBe(2.0);
  });

  // 8. Pitch adjustment (-0.5 to 0.5) is stored in profile
  it('pitch adjustment (-0.5 to 0.5) is stored in profile', () => {
    const mgr = VoiceProfileManager.getInstance();

    const low = mgr.createProfile({ name: 'Low', voiceId: 'default', pitch: -0.5 });
    expect(low.pitch).toBe(-0.5);

    const high = mgr.createProfile({ name: 'High', voiceId: 'default', pitch: 0.5 });
    expect(high.pitch).toBe(0.5);

    // Verify clamping
    const tooLow = mgr.createProfile({ name: 'TooLow', voiceId: 'default', pitch: -1.0 });
    expect(tooLow.pitch).toBe(-0.5);

    const tooHigh = mgr.createProfile({ name: 'TooHigh', voiceId: 'default', pitch: 1.0 });
    expect(tooHigh.pitch).toBe(0.5);
  });
  // 9. Profiles persist across restarts (via settings system mock)
  it('profiles persist across restarts via settings system', () => {
    const mgr = VoiceProfileManager.getInstance();
    const profile = mgr.createProfile({
      name: 'Persistent',
      voiceId: 'en-us-1',
      speed: 1.2,
      pitch: -0.1,
      volume: 0.8,
    });
    mgr.setActiveProfile(profile.id);

    // Verify that settings were persisted with the right keys
    const setSettingCalls = mocks.settingsManager.setSetting.mock.calls;
    const profilesCalls = setSettingCalls.filter(
      (call: unknown[]) => call[0] === 'voice.profiles',
    );
    const activeCalls = setSettingCalls.filter(
      (call: unknown[]) => call[0] === 'voice.activeProfileId',
    );

    expect(profilesCalls.length).toBeGreaterThan(0);
    expect(activeCalls.length).toBeGreaterThan(0);

    // The last profiles call should include our custom profile
    const lastProfiles = profilesCalls[profilesCalls.length - 1][1] as VoiceProfile[];
    expect(lastProfiles.some((p: VoiceProfile) => p.name === 'Persistent')).toBe(true);

    // The last active call should be our profile id
    const lastActiveId = activeCalls[activeCalls.length - 1][1];
    expect(lastActiveId).toBe(profile.id);
  });

  // 10. previewVoice() returns a short audio buffer via TTSEngine
  it('previewVoice() returns a short audio buffer via TTSEngine', async () => {
    const mockAudio = createMockPCM(12000);
    mocks.ttsEngine.synthesize.mockResolvedValue(mockAudio);

    const mgr = VoiceProfileManager.getInstance();
    const audio = await mgr.previewVoice('default');

    expect(audio).toBeInstanceOf(Float32Array);
    expect(audio.length).toBeGreaterThan(0);
    expect(mocks.ttsEngine.synthesize).toHaveBeenCalledOnce();

    // Verify the synthesize call received the preview text and profile options
    const [text, opts] = mocks.ttsEngine.synthesize.mock.calls[0];
    expect(text).toBe('Hello, this is a voice preview.');
    expect(opts).toBeDefined();
    expect(opts.voiceId).toBe('default');
    expect(opts.speed).toBe(1.0);
    expect(opts.pitch).toBe(0.0);
  });
});
