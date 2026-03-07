# Phase K.2 — The Timbre
## VoiceProfileManager: Voice Selection and Personalization

### Hermeneutic Focus
*The mouth can speak, but it has only one voice. This phase adds personality — letting the user choose between voices, adjust speed and pitch, and persist preferences. The timbre is what makes Agent Friday's voice recognizably "theirs."*

### Current State (Post-K.1)
- TTSEngine synthesizes speech via Kokoro or Piper
- No voice selection exists — uses whatever default model loads
- No speed/pitch adjustment
- Settings system exists (from Sprint 1) for preference persistence

### Architecture Context
```
VoiceProfileManager (this phase)
├── getActiveProfile()       — Current voice settings
├── setActiveProfile(id)     — Switch voice profile
├── listProfiles()           — All available voice profiles
├── createProfile(opts)      — Create custom profile
├── deleteProfile(id)        — Remove custom profile
├── getAvailableVoices()     — Voices from installed TTS models
└── previewVoice(profileId)  — Short audio sample
```

### Validation Criteria (Test-First)
1. `getActiveProfile()` returns default profile when none set
2. `setActiveProfile(id)` switches the active voice
3. `listProfiles()` includes default + any custom profiles
4. `createProfile({name, voiceId, speed, pitch})` persists a new profile
5. `deleteProfile(id)` removes a custom profile
6. Default profile cannot be deleted
7. Speed adjustment (0.5x - 2.0x) modifies TTS output rate
8. Pitch adjustment (-50% to +50%) modifies voice pitch
9. Profiles persist across application restarts (via settings system)
10. `previewVoice()` returns a short audio buffer for the profile

### Socratic Inquiry

**Boundary:** *What belongs in a voice profile vs. TTS engine configuration?*
Profile: voice selection, speed, pitch — user-facing preferences. Engine config: model paths, backend selection, buffer sizes — system internals. The profile tells the engine HOW to speak, not how to RUN.

**Inversion:** *What if the user's chosen voice model gets deleted?*
Fall back to default voice. Profile stores voice ID, not file path. If the ID resolves to a missing model, log a warning and use the default. Never crash on a missing voice.

**Constraint Discovery:** *How many voices can Kokoro/Piper offer?*
Kokoro ships with a few built-in voices. Piper has 100+ community voices across languages. VoiceProfileManager should enumerate what's installed, not hardcode a list.

**Precedent:** *How do existing settings persist in the app?*
The settings system uses typed getters/setters backed by `electron-store`. Voice profiles should use the same mechanism: `settings.get('voice.profiles')`, `settings.get('voice.activeProfileId')`.

**Safety Gate:** *Can voice profiles leak sensitive data?*
Profile names are user-created strings. Sanitize them (max length, no special chars). Voice model paths should never be exposed to renderers — only profile IDs and names cross the IPC boundary.

### Boundary Constraints
- Creates `src/main/voice/voice-profile-manager.ts` (~100-130 lines)
- Creates `tests/sprint-4/voice/voice-profile-manager.test.ts`
- Does NOT modify TTSEngine internals
- Does NOT handle audio playback (that's K.3)
- Uses existing settings system for persistence

### Files to Read
1. `src/main/voice/tts-engine.ts` — Available voices API
2. `src/main/settings.ts` — Settings persistence pattern

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-k-phase-2.md` before closing.
