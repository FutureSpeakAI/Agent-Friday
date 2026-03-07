## Interface Contract: VoiceProfileManager
**Sprint:** 4, Phase K.2
**Source:** src/main/voice/voice-profile-manager.ts (to be created)

### Exports
- `voiceProfileManager` — singleton instance

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| getActiveProfile() | `(): VoiceProfile` | Current voice settings |
| setActiveProfile(id) | `(id: string): void` | Switch active voice |
| listProfiles() | `(): VoiceProfile[]` | All profiles |
| createProfile(opts) | `(opts: CreateProfileOpts): VoiceProfile` | New custom profile |
| deleteProfile(id) | `(id: string): boolean` | Remove (not default) |
| previewVoice(id) | `(id: string): Promise<Float32Array>` | Short sample |

### Types
```typescript
interface VoiceProfile {
  id: string;
  name: string;
  voiceId: string;       // References VoiceInfo.id from TTSEngine
  speed: number;         // 0.5 - 2.0
  pitch: number;         // -0.5 to 0.5
  isDefault: boolean;
}

interface CreateProfileOpts {
  name: string;
  voiceId: string;
  speed?: number;
  pitch?: number;
}
```

### Persistence
- Stored via settings system: `settings.get('voice.profiles')`
- Active profile ID: `settings.get('voice.activeProfileId')`

### Dependencies
- Requires: TTSEngine (K.1), Settings system
- Required by: SpeechSynthesis (K.3), VoiceCircle (L.1)
