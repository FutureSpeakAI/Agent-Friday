# Contract: SetupWizard

## Module
`src/main/setup/setup-wizard.ts`

## Singleton
`SetupWizard.getInstance()`

## Types

```typescript
type SetupStep = 'idle' | 'detecting' | 'recommending' | 'confirming' | 'downloading' | 'loading' | 'complete';

interface SetupState {
  step: SetupStep;
  profile: HardwareProfile | null;
  recommendation: TierRecommendation | null;
  confirmedTier: TierName | null;
  downloads: DownloadProgress[];
  error: string | null;
}

interface DownloadProgress {
  modelName: string;
  status: 'pending' | 'downloading' | 'complete' | 'failed';
  bytesDownloaded: number;
  bytesTotal: number;
  percentComplete: number;
}
```

## Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `isFirstRun` | `() → boolean` | Check setup completion marker |
| `getSetupState` | `() → SetupState` | Current wizard state |
| `startSetup` | `() → Promise<void>` | Begin: detect → recommend |
| `skipSetup` | `() → void` | Default to Whisper tier, mark complete |
| `confirmTier` | `(tier: TierName) → void` | Accept tier selection |
| `startModelDownload` | `() → Promise<void>` | Download confirmed tier's models |
| `getDownloadProgress` | `() → DownloadProgress[]` | Per-model progress |
| `completeSetup` | `() → void` | Persist and mark done |
| `resetSetup` | `() → void` | Clear marker for re-run |

## Events
- `setup-state-changed` → `SetupState`
- `download-progress` → `DownloadProgress[]`
- `setup-complete` → `{ tier: TierName }`
- `setup-error` → `{ error: string, step: SetupStep }`

## IPC Channels
| Channel | Direction | Payload |
|---------|-----------|---------|
| `setup:state` | main → renderer | `SetupState` |
| `setup:progress` | main → renderer | `DownloadProgress[]` |
| `setup:start` | renderer → main | `void` |
| `setup:confirm-tier` | renderer → main | `TierName` |
| `setup:skip` | renderer → main | `void` |

## Persistence
- `settings.get('setup.completed')` → boolean
- `settings.get('setup.tier')` → TierName
- `settings.get('setup.downloads')` → partial progress for resume

## Boundary
- Main-process service only — no UI rendering
- Downloads are sequential per-model (simpler, more reliable)
- Wraps `ollama pull` for Ollama models, HTTP fetch for whisper models
- Resumable: persists state so interrupted downloads continue on next launch
