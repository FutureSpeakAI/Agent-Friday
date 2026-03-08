# Track K Phase 2: The Timbre -- VoiceProfileManager

**Date:** 2026-03-07
**Sprint:** 4
**Track:** K (Voice)
**Phase:** K.2

## What Was Built

VoiceProfileManager singleton that manages voice selection, speed/pitch
preferences, and profile persistence for Agent Friday.

### Files Created

- **Implementation:** src/main/voice/voice-profile-manager.ts (~130 lines)
- **Tests:** tests/sprint-4/voice/voice-profile-manager.test.ts (10 tests)

### API Surface

- getActiveProfile() - returns the currently active voice profile
- setActiveProfile(id) - switches the active voice profile
- listProfiles() - returns all profiles (default + custom)
- createProfile(opts) - creates a new custom voice profile with clamped params
- deleteProfile(id) - removes a custom profile (default cannot be deleted)
- previewVoice(profileId) - generates audio preview via TTSEngine

### VoiceProfile Interface



## Validation Criteria (10/10 passing)

1. getActiveProfile() returns default profile when none set
2. setActiveProfile(id) switches the active voice
3. listProfiles() includes default + any custom profiles
4. createProfile() persists a new profile
5. deleteProfile(id) removes a custom profile
6. Default profile cannot be deleted (returns false)
7. Speed adjustment (0.5x - 2.0x) is stored in profile with clamping
8. Pitch adjustment (-0.5 to 0.5) is stored in profile with clamping
9. Profiles persist across restarts via settings system
10. previewVoice() returns a short audio buffer via TTSEngine

## Architecture Decisions

- **Singleton pattern** consistent with TTSEngine and other managers
- **resetInstance()** for test isolation (same pattern as TTSEngine)
- **Clamping** for speed/pitch/volume to enforce valid ranges
- **Persistence** via settingsManager.setSetting() with voice.profiles and
  voice.activeProfileId keys
- **Default profile** always exists, cannot be deleted, used as fallback
- **Profile IDs** generated via counter + timestamp for uniqueness
- **previewVoice()** delegates to ttsEngine.synthesize() with profile options

## Safety Gate

- npx tsc --noEmit: 0 errors
- npx vitest run: 110 files, 4145 tests, all passing
