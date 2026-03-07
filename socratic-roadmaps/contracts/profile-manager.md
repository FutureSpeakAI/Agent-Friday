# Contract: ProfileManager

## Module
`src/main/setup/profile-manager.ts`

## Singleton
`ProfileManager.getInstance()`

## Types

```typescript
interface UserProfile {
  id: string;               // UUID
  name: string;
  createdAt: number;        // timestamp
  updatedAt: number;        // timestamp
  tierOverride: TierName | null; // null = use detected tier
  voiceProfileId: string | null; // references VoiceProfile
  preferences: UserPreferences;
  deleted: boolean;         // soft delete
}

interface UserPreferences {
  theme: 'system' | 'light' | 'dark';
  fontSize: 'small' | 'medium' | 'large';
  cloudConsent: boolean;    // allow gated cloud LLM
  [key: string]: unknown;   // extensible
}

interface CreateProfileOpts {
  name: string;
  preferences?: Partial<UserPreferences>;
}
```

## Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `createProfile` | `(opts: CreateProfileOpts) → UserProfile` | Create new profile |
| `getProfile` | `(id: string) → UserProfile \| null` | Get by ID |
| `getActiveProfile` | `() → UserProfile \| null` | Currently active |
| `setActiveProfile` | `(id: string) → void` | Switch active |
| `updateProfile` | `(id: string, data: Partial<UserProfile>) → UserProfile` | Merge update |
| `deleteProfile` | `(id: string) → void` | Soft delete |
| `exportProfile` | `(id: string) → string` | JSON string |
| `importProfile` | `(json: string) → UserProfile` | Create from JSON |
| `listProfiles` | `() → UserProfile[]` | All non-deleted profiles |

## Events
- `profile-changed` → `UserProfile` (active profile switched or updated)
- `profile-created` → `UserProfile`
- `profile-deleted` → `{ id: string }`

## Persistence
- `settings.get('profiles')` → `Record<string, UserProfile>`
- `settings.get('activeProfileId')` → `string | null`

## Defaults
```typescript
const DEFAULT_PREFERENCES: UserPreferences = {
  theme: 'system',
  fontSize: 'medium',
  cloudConsent: false,  // sovereign-first: no cloud by default
};
```

## Boundary
- Manages identity, not conversations or history
- Soft delete preserves ID for referential integrity
- First created profile auto-becomes active
- Unknown keys in preferences preserved on update (extensible)
- Export/import uses JSON — no binary formats
